"""High-level Gemini ACP invocation for claude-anyteam."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from claude_anyteam import logger
from claude_anyteam.codex import CodexResult, PLAN_SCHEMA, TASK_COMPLETE_SCHEMA
from claude_anyteam.env import identity_env
from claude_anyteam.schema_validation import load_schema, parse_and_validate

from . import invoke
from .acp_client import (
    GeminiAcpAuthenticationError,
    GeminiAcpClient,
    GeminiAcpError,
    GeminiAcpTimeoutError,
    detect_acp_flag,
)


def feature_test(gemini_binary: str = "gemini") -> None:
    resolved = shutil.which(gemini_binary)
    if not resolved:
        raise RuntimeError(f"gemini binary not found on PATH (expected {gemini_binary!r}). Install and authenticate Gemini CLI.")
    try:
        version = subprocess.run([gemini_binary, "--version"], capture_output=True, text=True, timeout=10, check=True)
        help_out = subprocess.run([gemini_binary, "--help"], capture_output=True, text=True, timeout=10, check=True)
    except (subprocess.SubprocessError, OSError) as e:
        raise RuntimeError(f"could not probe Gemini CLI {gemini_binary!r}: {e}") from e
    help_text = (help_out.stdout or "") + (help_out.stderr or "")
    if "--acp" not in help_text and "--experimental-acp" not in help_text:
        raise RuntimeError(f"Gemini CLI is missing required ACP flag --acp; found version {(version.stdout or version.stderr).strip()}")
    logger.info(
        "gemini_acp.version",
        binary=resolved,
        version=(version.stdout or version.stderr).strip(),
        acp_flag=detect_acp_flag(gemini_binary),
    )


def _extract_json_candidate(text: str) -> str:
    return invoke._extract_json_candidate(text)  # reuse the headless tolerant extractor


def _mcp_servers(team: str, agent: str, real_home: str | None) -> list[dict[str, Any]]:
    env = identity_env(os.environ, team=team, name=agent)
    if real_home:
        env["HOME"] = real_home
    keep = (
        "HOME",
        "CLAUDE_ANYTEAM_TEAM",
        "CLAUDE_ANYTEAM_NAME",
        "CODEX_TEAMMATE_TEAM",
        "CODEX_TEAMMATE_NAME",
    )
    return [
        {
            "name": invoke.WRAPPER_SERVER_ALIAS,
            "command": invoke._wrapper_binary(),
            "args": ["--team", team, "--name", agent],
            "env": [{"name": k, "value": env[k]} for k in keep if k in env],
        }
    ]


def _latest_storage_session_id(gemini_home: Path) -> str | None:
    chats_root = gemini_home / ".gemini" / "tmp"
    if not chats_root.exists():
        return None
    candidates = sorted(
        chats_root.glob("**/chats/session-*.jsonl"),
        key=lambda p: p.stat().st_mtime if p.exists() else 0,
        reverse=True,
    )
    for path in candidates:
        try:
            first = path.read_text(encoding="utf-8", errors="ignore").splitlines()[0]
            data = json.loads(first)
        except (IndexError, OSError, json.JSONDecodeError):
            continue
        sid = data.get("sessionId")
        if isinstance(sid, str) and sid:
            return sid
    return None


def _auth_method_id(method: Any) -> str | None:
    if isinstance(method, str) and method:
        return method
    if isinstance(method, dict):
        for key in ("id", "methodId", "method_id", "name"):
            value = method.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _authenticate_if_required(client: GeminiAcpClient, initialize_result: dict[str, Any]) -> bool:
    auth_methods = initialize_result.get("authMethods") if isinstance(initialize_result, dict) else None
    if not auth_methods:
        return False
    if not isinstance(auth_methods, list):
        raise GeminiAcpAuthenticationError(f"Gemini ACP initialize returned invalid authMethods: {auth_methods!r}")
    method_id = next((_auth_method_id(method) for method in auth_methods if _auth_method_id(method)), None)
    if not method_id:
        raise GeminiAcpAuthenticationError(f"Gemini ACP authentication required but no usable auth method was advertised: {auth_methods!r}")
    try:
        client.authenticate(method_id)
    except GeminiAcpError as e:
        raise GeminiAcpAuthenticationError(f"Gemini ACP authentication failed using method {method_id!r}: {e}") from e
    return True


def _tool_update_text(content: Any) -> str | None:
    if isinstance(content, dict):
        if content.get("type") == "text" and isinstance(content.get("text"), str):
            return content["text"]
        nested = content.get("content")
        if nested is not None:
            return _tool_update_text(nested)
    if isinstance(content, list):
        parts = [_tool_update_text(item) for item in content]
        text = "".join(part for part in parts if part)
        return text or None
    return None


def _normalised_tool_event(update: dict[str, Any], session_id: str) -> dict[str, Any] | None:
    kind = update.get("sessionUpdate")
    if kind == "tool_call":
        return {
            "type": "tool_use",
            "source": "gemini_acp",
            "session_id": session_id,
            "tool_call_id": update.get("toolCallId"),
            "tool_name": update.get("title"),
            "status": update.get("status"),
            "kind": update.get("kind"),
            "acp_update": update,
        }
    if kind == "tool_call_update":
        text = _tool_update_text(update.get("content"))
        if text is None:
            return None
        return {
            "type": "tool_result",
            "source": "gemini_acp",
            "session_id": session_id,
            "tool_call_id": update.get("toolCallId"),
            "tool_name": update.get("title"),
            "status": update.get("status"),
            "content": text,
            "acp_update": update,
        }
    return None


def _normalize_tool_events(events: list[dict[str, Any]], session_id: str | None) -> list[dict[str, Any]]:
    if not session_id:
        return events
    normalised: list[dict[str, Any]] = []
    for ev in events:
        normalised.append(ev)
        if ev.get("method") != "session/update":
            continue
        params = ev.get("params") if isinstance(ev.get("params"), dict) else {}
        if params.get("sessionId") not in (None, session_id):
            continue
        update = params.get("update") if isinstance(params.get("update"), dict) else {}
        tool_ev = _normalised_tool_event(update, session_id)
        if tool_ev is not None:
            normalised.append(tool_ev)
    return normalised


def _session_id_from_result(result: dict[str, Any], fallback: str | None = None) -> str | None:
    sid = result.get("sessionId") if isinstance(result, dict) else None
    return sid if isinstance(sid, str) and sid else fallback


def _ensure_session(
    client: GeminiAcpClient,
    *,
    cwd: Path,
    mcp_servers: list[dict[str, Any]],
    resume_session_id: str | None,
    stored_session_id: str | None,
    stored_storage_session_id: str | None,
) -> tuple[str, bool]:
    for candidate in (resume_session_id, stored_session_id, stored_storage_session_id):
        if not candidate:
            continue
        try:
            result = client.session_load(session_id=candidate, cwd=cwd, mcp_servers=mcp_servers)
            sid = _session_id_from_result(result, candidate)
            if sid:
                return sid, True
        except GeminiAcpError as e:
            logger.warn("gemini_acp.session_load_failed", session_id=candidate, error=str(e))
    result = client.session_new(cwd=cwd, mcp_servers=mcp_servers)
    sid = _session_id_from_result(result)
    if not sid:
        raise GeminiAcpError(f"session/new response missing sessionId: {result}")
    return sid, False


def _assistant_text_and_tools(events: list[dict[str, Any]], session_id: str) -> tuple[str, int]:
    parts: list[str] = []
    tool_calls = 0
    for ev in events:
        if ev.get("method") != "session/update":
            continue
        params = ev.get("params") if isinstance(ev.get("params"), dict) else {}
        if params.get("sessionId") not in (None, session_id):
            continue
        update = params.get("update") if isinstance(params.get("update"), dict) else {}
        kind = update.get("sessionUpdate")
        if kind == "agent_message_chunk":
            content = update.get("content") if isinstance(update.get("content"), dict) else {}
            if content.get("type") == "text" and isinstance(content.get("text"), str):
                parts.append(content["text"])
        if kind in {"tool_call", "tool_call_update"}:
            tool_calls += 1
    return "".join(parts).strip(), tool_calls


def _cancel_session_quietly(client: GeminiAcpClient, session_id: str | None) -> None:
    if not session_id:
        return
    try:
        client.session_cancel(session_id=session_id)
    except Exception as e:
        logger.warn("gemini_acp.cancel_failed", session_id=session_id, error=str(e))


def run(
    prompt: str,
    *,
    cwd: Path,
    schema: Path | None = None,
    gemini_binary: str = "gemini",
    timeout_s: float = 900.0,
    wrapper_identity: tuple[str, str] | None = None,
    resume_session_id: str | None = None,
    model: str | None = None,
    effort: str | None = None,
    gemini_home: Path | None = None,
    ephemeral: bool = False,
) -> CodexResult:
    team, agent = wrapper_identity or ("default", "gemini")
    real_home = os.environ.get("HOME")
    home = gemini_home or invoke._default_gemini_home(team, agent)
    settings_path = invoke.write_mcp_settings(
        home,
        team=team,
        agent_name=agent,
        real_home=real_home,
        cwd=cwd,
    )
    effective_model = model
    if model and effort:
        effective_model = invoke.inject_effort_alias(settings_path, model=model, effort=effort) or model
    adapter_state = invoke.read_adapter_state(home)
    mcp_servers = _mcp_servers(team, agent, real_home)

    sub_env = dict(os.environ)
    sub_env["HOME"] = str(home)
    sub_env.setdefault("GEMINI_CLI_NO_RELAUNCH", "true")
    if real_home:
        sub_env["CLAUDE_ANYTEAM_REAL_HOME"] = real_home
    if wrapper_identity:
        sub_env = identity_env(sub_env, team=team, name=agent)

    events: list[dict[str, Any]] = []
    error: str | None = None
    session_id: str | None = None
    loaded = False
    logger.info("gemini_acp.invoke", cwd=str(cwd), gemini_home=str(home), schema=str(schema) if schema else None, resumed=bool(resume_session_id), model=model, effort=effort, effective_model=effective_model)

    client = GeminiAcpClient(gemini_binary=gemini_binary, env=sub_env)
    try:
        client.start()
        initialize_result = client.initialize()
        _authenticate_if_required(client, initialize_result)
        stored = None if ephemeral else adapter_state.get("acp_session_id")
        stored_storage = None if ephemeral else adapter_state.get("acp_storage_session_id")
        session_id, loaded = _ensure_session(
            client,
            cwd=cwd,
            mcp_servers=mcp_servers,
            resume_session_id=resume_session_id,
            stored_session_id=stored if isinstance(stored, str) else None,
            stored_storage_session_id=stored_storage if isinstance(stored_storage, str) else None,
        )
        try:
            client.set_session_mode(session_id=session_id, mode_id="yolo")
        except GeminiAcpError as e:
            logger.warn("gemini_acp.set_mode_failed", error=str(e))
        if effective_model:
            try:
                client.unstable_set_session_model(session_id=session_id, model_id=effective_model)
            except GeminiAcpError as e:
                logger.warn("gemini_acp.set_model_failed", model=effective_model, raw_model=model, effort=effort, error=str(e))
        response = client.session_prompt(session_id=session_id, prompt=prompt, timeout=timeout_s)
        events = _normalize_tool_events(client.drain_notifications(), session_id)
    except (subprocess.TimeoutExpired, GeminiAcpTimeoutError):
        _cancel_session_quietly(client, session_id)
        if not ephemeral:
            invoke.reset_acp_adapter_state(home)
        return CodexResult(exit_code=124, structured=None, last_message="", events=events, error=f"gemini ACP timed out after {timeout_s}s; ACP session was dropped for the next task", session_id=session_id)
    except Exception as e:
        return CodexResult(exit_code=1, structured=None, last_message="", events=events, error=str(e), session_id=session_id)
    finally:
        client.close()

    last_message, tool_call_events = _assistant_text_and_tools(events, session_id)
    structured: dict[str, Any] | None = None
    if schema is not None:
        parsed, err = parse_and_validate(_extract_json_candidate(last_message), load_schema(schema))
        structured = parsed
        if err:
            error = f"gemini ACP final message failed schema validation: {err}"

    stop_reason = response.get("stopReason") if isinstance(response, dict) else None
    exit_code = 0
    if stop_reason not in (None, "end_turn") and not error:
        exit_code = 1
        error = f"gemini ACP stopReason {stop_reason!r}"
    if error:
        exit_code = 1

    if session_id and not ephemeral:
        if stop_reason == "cancelled":
            invoke.reset_acp_adapter_state(home)
        elif error is None:
            invoke.write_adapter_state(
                home,
                backend="acp",
                acp_session_id=session_id,
                acp_storage_session_id=_latest_storage_session_id(home),
            )

    logger.info("gemini_acp.result", session_id=session_id, loaded=loaded, stop_reason=stop_reason, tool_calls=tool_call_events)
    return CodexResult(
        exit_code=exit_code,
        structured=structured,
        last_message=last_message,
        events=events,
        error=error,
        tool_call_events=tool_call_events,
        session_id=session_id,
    )
