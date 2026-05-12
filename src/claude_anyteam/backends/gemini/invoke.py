"""Gemini CLI invocation for claude-anyteam."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from claude_anyteam import logger
from claude_anyteam.auth_preflight import (
    AuthPreflightFailure,
    classify_auth_error,
    subprocess_diagnostic,
)
from claude_anyteam.codex import CodexResult, PLAN_SCHEMA, TASK_COMPLETE_SCHEMA
from claude_anyteam.env import identity_env
from claude_anyteam.headless_visibility import HeadlessTurnVisibility, coerce_stream_text
from claude_anyteam.messages import VisibilityEvent
from claude_anyteam.schema_validation import load_schema, parse_and_validate

WRAPPER_SERVER_ALIAS = "anyteam"
WRAPPER_TOOL_PREFIX = f"mcp_{WRAPPER_SERVER_ALIAS}_"

GEMINI_EFFORT_ALIAS_PREFIX = "claude-anyteam-effort"
GEMINI_25_THINKING_BUDGETS = {
    "minimal": 0,
    "low": 512,
    "medium": 2048,
    "high": 4096,
    "xhigh": 8192,
}
GEMINI_3_THINKING_LEVELS = {
    "minimal": "LOW",
    "low": "LOW",
    "medium": "MEDIUM",
    "high": "HIGH",
    "xhigh": "HIGH",
}


def gemini_effort_alias_name(effort: str) -> str:
    return f"{GEMINI_EFFORT_ALIAS_PREFIX}-{effort}"


def _effort_alias_entry(model: str, effort: str) -> dict[str, Any] | None:
    if effort not in GEMINI_25_THINKING_BUDGETS:
        raise ValueError(f"effort must be one of minimal|low|medium|high|xhigh, got {effort!r}")
    thinking_config: dict[str, Any]
    if model.startswith("gemini-2.5"):
        thinking_config = {
            "thinkingBudget": GEMINI_25_THINKING_BUDGETS[effort],
            "includeThoughts": False,
        }
    elif model.startswith("gemini-3"):
        thinking_config = {
            "thinkingLevel": GEMINI_3_THINKING_LEVELS[effort],
            "includeThoughts": False,
        }
    else:
        return None
    return {
        "extends": model,
        "modelConfig": {
            "generateContentConfig": {
                "thinkingConfig": thinking_config,
            }
        },
    }


def inject_effort_alias(settings_path: Path, *, model: str, effort: str) -> str | None:
    """Inject a Gemini customAlias for an effort tier into isolated settings.

    Returns the alias name to pass via ``--model``, or ``None`` when the model
    family is unknown and the caller should pass through the raw model.
    """
    entry = _effort_alias_entry(model, effort)
    if entry is None:
        logger.warn("gemini.effort.unknown_model_family", model=model, effort=effort)
        return None
    alias = gemini_effort_alias_name(effort)
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    model_configs = data.setdefault("modelConfigs", {})
    if not isinstance(model_configs, dict):
        model_configs = {}
        data["modelConfigs"] = model_configs
    aliases = model_configs.setdefault("customAliases", {})
    if not isinstance(aliases, dict):
        aliases = {}
        model_configs["customAliases"] = aliases
    aliases[alias] = entry
    _write_atomic_json(settings_path, data)
    return alias


def _default_gemini_home(team: str, agent_name: str) -> Path:
    safe_team = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in team)
    safe_agent = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in agent_name)
    return Path.home() / ".cache" / "claude-anyteam" / "gemini" / safe_team / safe_agent


def _wrapper_binary(wrapper_binary: str = "claude-anyteam-wrapper") -> str:
    return shutil.which(wrapper_binary) or wrapper_binary


_AUTH_CACHE_FILES = (
    "oauth_creds.json",
    "google_accounts.json",
    "projects.json",
    "state.json",
)


def _copy_if_absent(src: Path, dst: Path) -> None:
    if dst.exists():
        return
    if not src.exists() or not src.is_file():
        return
    shutil.copy2(src, dst)


def _write_atomic_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _write_scoped_trusted_folders(settings_dir: Path, *, cwd: Path | None, include_dirs: list[Path] | None) -> None:
    if cwd is None and not include_dirs:
        return
    trusted: dict[str, str] = {}
    if cwd is not None:
        trusted[str(cwd.resolve())] = "TRUST_FOLDER"
    for directory in include_dirs or []:
        trusted[str(directory.resolve())] = "TRUST_FOLDER"
    _write_atomic_json(settings_dir / "trustedFolders.json", trusted)


def prepare_isolated_gemini_home(
    gemini_home: Path,
    *,
    real_home: str | None,
    cwd: Path | None = None,
    include_dirs: list[Path] | None = None,
) -> Path:
    """Prepare adapter-owned Gemini HOME without sharing mutable user state.

    The adapter isolates Gemini's HOME so it can inject exactly one MCP server
    and keep each teammate's transcript/session files separate. Auth/account
    files are copied on first use (not symlinked) to avoid token-refresh races
    and account/trust bleed between concurrent Gemini teammates. User tmp/ and
    history/ are never copied.
    """
    settings_dir = gemini_home / ".gemini"
    settings_dir.mkdir(parents=True, exist_ok=True)
    source_dir = Path(real_home) / ".gemini" if real_home else None
    if source_dir is not None and source_dir.exists():
        for name in _AUTH_CACHE_FILES:
            _copy_if_absent(source_dir / name, settings_dir / name)
        installation_dst = settings_dir / "installation_id"
        if not installation_dst.exists():
            installation_src = source_dir / "installation_id"
            if installation_src.exists() and installation_src.is_file():
                shutil.copy2(installation_src, installation_dst)
            else:
                installation_dst.write_text(str(uuid.uuid4()) + "\n", encoding="utf-8")
    else:
        installation_dst = settings_dir / "installation_id"
        if not installation_dst.exists():
            installation_dst.write_text(str(uuid.uuid4()) + "\n", encoding="utf-8")
    _write_scoped_trusted_folders(settings_dir, cwd=cwd, include_dirs=include_dirs)
    ensure_adapter_state(gemini_home)
    return settings_dir


def _link_auth_cache(settings_dir: Path, real_home: str | None) -> None:
    """Backward-compatible wrapper for older tests/imports.

    Deprecated: use prepare_isolated_gemini_home(). Despite the legacy name,
    this now copies mutable auth/account files instead of symlinking them.
    """
    prepare_isolated_gemini_home(settings_dir.parent, real_home=real_home)


def _adapter_state_path(gemini_home: Path) -> Path:
    return gemini_home / ".claude-anyteam" / "state.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _default_adapter_state() -> dict[str, Any]:
    return {
        "headless_session_id": None,
        "acp_session_id": None,
        "acp_storage_session_id": None,
        "backend": "headless",
        "updated_at": None,
        "adapter_pid": None,
        "adapter_start_time": None,
        "adapter_start_monotonic_ns": None,
        "adapter_generation": None,
        "adapter_exited_at": None,
        "team": None,
        "agent": None,
        "cwd": None,
        "gemini_pid": None,
        "gemini_pgid": None,
        "gemini_started_at": None,
        "last_clean_shutdown_at": None,
        "last_reaper_run_at": None,
        "last_reaper_summary": None,
    }


def merge_adapter_state(gemini_home: Path, **updates: Any) -> Path:
    data = read_adapter_state(gemini_home)
    data.update(updates)
    data["updated_at"] = _utc_now()
    path = _adapter_state_path(gemini_home)
    _write_atomic_json(path, data)
    return path


def ensure_adapter_state(gemini_home: Path) -> Path:
    path = _adapter_state_path(gemini_home)
    if not path.exists():
        write_adapter_state(gemini_home, backend="headless")
    return path


def read_adapter_state(gemini_home: Path) -> dict[str, Any]:
    defaults = _default_adapter_state()
    path = _adapter_state_path(gemini_home)
    if not path.exists():
        return dict(defaults)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(defaults)
    if not isinstance(data, dict):
        return dict(defaults)
    merged = dict(defaults)
    merged.update(data)
    return merged


def write_adapter_state(
    gemini_home: Path,
    *,
    backend: str,
    headless_session_id: str | None = None,
    acp_session_id: str | None = None,
    acp_storage_session_id: str | None = None,
) -> Path:
    previous = read_adapter_state(gemini_home)
    return merge_adapter_state(
        gemini_home,
        headless_session_id=headless_session_id if headless_session_id is not None else previous.get("headless_session_id"),
        acp_session_id=acp_session_id if acp_session_id is not None else previous.get("acp_session_id"),
        acp_storage_session_id=acp_storage_session_id if acp_storage_session_id is not None else previous.get("acp_storage_session_id"),
        backend=backend,
    )


def reset_acp_adapter_state(gemini_home: Path, *, backend: str = "acp") -> Path:
    previous = read_adapter_state(gemini_home)
    return merge_adapter_state(
        gemini_home,
        headless_session_id=previous.get("headless_session_id"),
        acp_session_id=None,
        acp_storage_session_id=None,
        backend=backend,
    )


def _real_auth_settings(real_home: str | None) -> dict[str, Any]:
    """Return only the Gemini auth settings that must follow isolated HOME."""
    if not real_home:
        return {}
    path = Path(real_home) / ".gemini" / "settings.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    security = data.get("security")
    if not isinstance(security, dict):
        return {}
    auth = security.get("auth")
    if not isinstance(auth, dict):
        return {}
    return {"security": {"auth": auth}}


def write_mcp_settings(
    gemini_home: Path,
    *,
    team: str,
    agent_name: str,
    real_home: str | None = None,
    cwd: Path | None = None,
    include_dirs: list[Path] | None = None,
) -> Path:
    """Write adapter-owned Gemini MCP config without mutating ~/.gemini."""
    settings_dir = prepare_isolated_gemini_home(
        gemini_home, real_home=real_home, cwd=cwd, include_dirs=include_dirs
    )
    env = identity_env(os.environ, team=team, name=agent_name)
    if real_home:
        env["HOME"] = real_home
    data = {
        "tools": {"core": []},
        "mcpServers": {
            WRAPPER_SERVER_ALIAS: {
                "command": _wrapper_binary(),
                "args": ["--team", team, "--name", agent_name],
                "env": {k: env[k] for k in ("HOME", "CLAUDE_ANYTEAM_TEAM", "CLAUDE_ANYTEAM_NAME", "CODEX_TEAMMATE_TEAM", "CODEX_TEAMMATE_NAME") if k in env},
                "trust": True,
                "timeout": 30000,
            }
        }
    }
    data.update(_real_auth_settings(real_home))
    path = settings_dir / "settings.json"
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


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
    missing = [flag for flag in ("--prompt", "--output-format", "--resume", "--approval-mode") if flag not in help_text]
    if missing:
        raise RuntimeError(f"Gemini CLI is missing required flags {missing}; found version {(version.stdout or version.stderr).strip()}")
    logger.info("gemini.version", binary=resolved, version=(version.stdout or version.stderr).strip())


def credential_preflight(
    *,
    gemini_binary: str = "gemini",
    cwd: Path,
    team: str,
    agent_name: str,
    model: str | None = None,
    effort: str | None = None,
    gemini_home: Path | None = None,
    timeout_s: float = 45.0,
) -> None:
    """Run a cheap Gemini API probe in the same HOME used by the adapter.

    ``feature_test`` intentionally only validates the local CLI surface.  This
    probe validates remote auth/quota before the teammate registers and enters
    the poll loop, preventing long silent stress-run stalls when the Gemini API
    rejects the configured account/model.
    """

    real_home = os.environ.get("HOME")
    home = gemini_home or _default_gemini_home(team, agent_name)
    settings_path = write_mcp_settings(
        home,
        team=team,
        agent_name=agent_name,
        real_home=real_home,
        cwd=cwd,
    )
    launch_model = model
    if model and effort:
        launch_model = inject_effort_alias(settings_path, model=model, effort=effort) or model

    args = [
        gemini_binary,
        "--prompt",
        "ping",
        "--output-format",
        "stream-json",
        "--approval-mode",
        "yolo",
    ]
    if launch_model:
        args.extend(["--model", launch_model])

    sub_env = dict(os.environ)
    sub_env["HOME"] = str(home)
    sub_env.setdefault("GEMINI_CLI_NO_RELAUNCH", "true")
    if real_home:
        sub_env["CLAUDE_ANYTEAM_REAL_HOME"] = real_home
    sub_env = identity_env(sub_env, team=team, name=agent_name)

    logger.info(
        "gemini.auth_preflight.start",
        cwd=str(cwd),
        gemini_home=str(home),
        model=model,
        effort=effort,
        effective_model=launch_model,
    )
    try:
        proc = subprocess.run(
            args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
            env=sub_env,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        detail = subprocess_diagnostic(
            args,
            returncode=None,
            stdout=coerce_stream_text(getattr(exc, "stdout", None) or getattr(exc, "output", None)),
            stderr=coerce_stream_text(getattr(exc, "stderr", None)),
        )
        raise RuntimeError(f"Gemini auth preflight timed out after {timeout_s}s\n{detail}") from exc
    except (subprocess.SubprocessError, OSError) as exc:
        raise RuntimeError(f"could not run Gemini auth preflight {gemini_binary!r}: {exc}") from exc

    diagnostic = subprocess_diagnostic(
        args,
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )
    if proc.returncode != 0:
        classified = classify_auth_error(diagnostic)
        if classified is not None:
            error_class, reset_after = classified
            raise AuthPreflightFailure(
                backend="gemini",
                error_class=error_class,
                error_message=diagnostic,
                reset_after_seconds=reset_after,
                cmd=args,
                returncode=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
            )
        raise RuntimeError(f"Gemini auth preflight exited {proc.returncode}\n{diagnostic}")
    logger.info("gemini.auth_preflight.ok", model=model, effective_model=launch_model)


def _extract_json_candidate(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    return stripped


def _parse_stream_json(stdout: str) -> tuple[list[dict[str, Any]], str, int, str | None]:
    events: list[dict[str, Any]] = []
    last_message_parts: list[str] = []
    tool_call_events = 0
    captured_session_id: str | None = None
    seen_non_init_event = False
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            logger.debug("gemini.nonjson_line", line=line[:200])
            continue
        events.append(ev)
        ev_type = str(ev.get("type", ""))
        if ev_type == "init" and isinstance(ev.get("session_id"), str):
            if seen_non_init_event:
                logger.warn("gemini.late_init", session_id=ev["session_id"], captured_session_id=captured_session_id)
            elif captured_session_id is None:
                captured_session_id = ev["session_id"]
            elif ev["session_id"] != captured_session_id:
                logger.warn("gemini.duplicate_init", session_id=ev["session_id"], captured_session_id=captured_session_id)
        else:
            seen_non_init_event = True
        if ev_type == "message" and ev.get("role") == "assistant" and isinstance(ev.get("content"), str):
            last_message_parts.append(ev["content"])
        if ev_type == "tool_use":
            tool_call_events += 1
            logger.info("gemini.tool_call", tool=ev.get("tool_name"), event=ev)
    return events, "".join(last_message_parts).strip(), tool_call_events, captured_session_id


def run(
    prompt: str,
    *,
    cwd: Path,
    schema: Path | None = None,
    gemini_binary: str = "gemini",
    timeout_s: float = 1800.0,
    wrapper_identity: tuple[str, str] | None = None,
    resume_session_id: str | None = None,
    model: str | None = None,
    effort: str | None = None,
    gemini_home: Path | None = None,
    task_id: str | None = None,
    event_sink: Callable[[VisibilityEvent], None] | None = None,
) -> CodexResult:
    team, agent = wrapper_identity or ("default", "gemini")
    real_home = os.environ.get("HOME")
    home = gemini_home or _default_gemini_home(team, agent)
    settings_path = write_mcp_settings(
        home,
        team=team,
        agent_name=agent,
        real_home=real_home,
        cwd=cwd,
    )

    launch_model = model
    if model and effort:
        launch_model = inject_effort_alias(settings_path, model=model, effort=effort) or model

    args = [gemini_binary, "--prompt", prompt, "--output-format", "stream-json", "--approval-mode", "yolo"]
    if launch_model:
        args.extend(["--model", launch_model])
    if resume_session_id:
        args.extend(["--resume", resume_session_id])

    sub_env = dict(os.environ)
    sub_env["HOME"] = str(home)
    if real_home:
        sub_env["CLAUDE_ANYTEAM_REAL_HOME"] = real_home
    if wrapper_identity:
        sub_env = identity_env(sub_env, team=team, name=agent)

    error: str | None = None

    logger.info(
        "gemini.invoke",
        cwd=str(cwd),
        gemini_home=str(home),
        schema=str(schema) if schema else None,
        resumed=bool(resume_session_id),
        model=model,
        effort=effort,
        effective_model=launch_model,
    )
    visibility = HeadlessTurnVisibility.start(
        team=team,
        agent=agent,
        backend="gemini_headless",
        enabled=wrapper_identity is not None,
        cwd=cwd,
        schema=schema,
        timeout_s=timeout_s,
        model=model,
        effort=effort,
        resume_session_id=resume_session_id,
        task_id=task_id,
        extra_payload={"effective_model": launch_model},
        event_sink=event_sink,
    )
    try:
        proc = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, timeout=timeout_s, check=False, env=sub_env, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as exc:
        timeout_stdout = coerce_stream_text(getattr(exc, "stdout", None) or getattr(exc, "output", None))
        events, last_message, tool_call_events, captured_session_id = _parse_stream_json(timeout_stdout)
        error = f"gemini timed out after {timeout_s}s"
        if captured_session_id:
            write_adapter_state(home, backend="headless", headless_session_id=captured_session_id)
        visibility.terminal(
            success=False,
            exit_code=124,
            error=error,
            events=events,
            tool_call_events=tool_call_events,
            last_message=last_message,
            structured=False,
            partial_events_available=bool(events),
            session_id=captured_session_id,
            error_class="turn_timeout",
            extra_payload={"tool_call_event_source": "gemini stream-json type=tool_use"},
        )
        return CodexResult(
            exit_code=124,
            structured=None,
            last_message=last_message,
            events=events,
            error=error,
            tool_call_events=tool_call_events,
            session_id=captured_session_id,
        )

    events, last_message, tool_call_events, captured_session_id = _parse_stream_json(proc.stdout)
    structured: dict[str, Any] | None = None
    if schema is not None:
        parsed, err = parse_and_validate(_extract_json_candidate(last_message), load_schema(schema))
        structured = parsed
        if err:
            error = f"gemini final message failed schema validation: {err}"
    terminal = next((ev for ev in reversed(events) if ev.get("type") == "result"), None)
    exit_code = proc.returncode
    error_class: str | None = None
    if proc.returncode != 0 and not error:
        error = f"gemini exited {proc.returncode}; stderr: {proc.stderr[:500]}"
    elif terminal is None:
        if not error:
            error = "gemini stream ended without result event"
        if exit_code == 0:
            exit_code = 1
        error_class = "missing_terminal_result"
    elif terminal.get("status") not in (None, "success") and not error:
        error = f"gemini result status {terminal.get('status')!r}"
        error_class = "result_status"

    if captured_session_id:
        write_adapter_state(home, backend="headless", headless_session_id=captured_session_id)

    success = exit_code == 0 and error is None
    visibility.terminal(
        success=success,
        exit_code=exit_code,
        error=error,
        events=events,
        tool_call_events=tool_call_events,
        last_message=last_message,
        structured=structured is not None,
        partial_events_available=bool(events),
        session_id=captured_session_id,
        error_class=error_class,
        extra_payload={
            "tool_call_event_source": "gemini stream-json type=tool_use",
            "terminal_result_event": terminal,
        },
    )

    return CodexResult(
        exit_code=exit_code,
        structured=structured,
        last_message=last_message,
        events=events,
        error=error,
        tool_call_events=tool_call_events,
        session_id=captured_session_id,
    )
