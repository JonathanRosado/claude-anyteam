"""Kimi CLI invocation for claude-anyteam.

Kimi's ``--output-format stream-json`` is per-message NDJSON, not the
Gemini init/result stream.  Session ids are emitted on stderr as a resume
hint.  This module keeps those Kimi-specific details behind the same
``CodexResult`` shape used by the existing Codex/Gemini loops.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Callable
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
from claude_anyteam.schema_validation import inline_schema_prompt_fragment, load_schema, parse_and_validate

WRAPPER_SERVER_ALIAS = "anyteam"
SESSION_HINT_RE = re.compile(r"To resume this session: kimi -r (\S+)")
KIMI_CREDENTIALS_REL = Path(".kimi") / "credentials" / "kimi-code.json"
KIMI_CREDENTIAL_LOCK_REL = Path(".kimi") / "credentials" / "kimi-code.lock"
KIMI_CONFIG_REL = Path(".kimi") / "config.toml"


def _safe_component(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in value)


def default_kimi_home(team: str, agent_name: str) -> Path:
    """Return an adapter-owned HOME root for one Kimi teammate."""
    return Path.home() / ".cache" / "claude-anyteam" / "kimi" / _safe_component(team) / _safe_component(agent_name)


# Backwards-compatible private spelling for callers that mirror Gemini tests.
_default_kimi_home = default_kimi_home


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


def _copy_if_present(src: Path, dst: Path, *, overwrite: bool = False) -> None:
    if not src.exists() or not src.is_file():
        return
    if dst.exists() and not overwrite:
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def prepare_isolated_kimi_home(kimi_home: Path, *, real_home: str | None = None) -> Path:
    """Prepare adapter-owned Kimi HOME and copy mutable auth state.

    Kimi stores OAuth credentials in ``~/.kimi/credentials/kimi-code.json``.
    Copy the credential and peer lock into the isolated HOME instead of
    symlinking so token-refresh writes do not race between concurrent
    teammates.  ``device_id`` is deliberately not copied; Kimi can create one
    for the isolated home.
    """
    kimi_dir = kimi_home / ".kimi"
    kimi_dir.mkdir(parents=True, exist_ok=True)
    if real_home:
        source_home = Path(real_home)
        _copy_if_present(source_home / KIMI_CREDENTIALS_REL, kimi_home / KIMI_CREDENTIALS_REL, overwrite=True)
        _copy_if_present(source_home / KIMI_CREDENTIAL_LOCK_REL, kimi_home / KIMI_CREDENTIAL_LOCK_REL, overwrite=True)
        _copy_if_present(source_home / KIMI_CONFIG_REL, kimi_home / KIMI_CONFIG_REL)
    ensure_adapter_state(kimi_home)
    return kimi_dir


def _adapter_state_path(kimi_home: Path) -> Path:
    return kimi_home / ".claude-anyteam" / "state.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _default_adapter_state() -> dict[str, Any]:
    return {
        "headless_session_id": None,
        "acp_session_id": None,
        "backend": "headless",
        "updated_at": None,
        "adapter_pid": None,
        "team": None,
        "agent": None,
        "cwd": None,
    }


def ensure_adapter_state(kimi_home: Path) -> Path:
    path = _adapter_state_path(kimi_home)
    if not path.exists():
        write_adapter_state(kimi_home, backend="headless")
    return path


def read_adapter_state(kimi_home: Path) -> dict[str, Any]:
    defaults = _default_adapter_state()
    path = _adapter_state_path(kimi_home)
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


def merge_adapter_state(kimi_home: Path, **updates: Any) -> Path:
    data = read_adapter_state(kimi_home)
    data.update(updates)
    data["updated_at"] = _utc_now()
    path = _adapter_state_path(kimi_home)
    _write_atomic_json(path, data)
    return path


def write_adapter_state(
    kimi_home: Path,
    *,
    backend: str,
    headless_session_id: str | None = None,
    acp_session_id: str | None = None,
) -> Path:
    previous = read_adapter_state(kimi_home)
    return merge_adapter_state(
        kimi_home,
        headless_session_id=headless_session_id if headless_session_id is not None else previous.get("headless_session_id"),
        acp_session_id=acp_session_id if acp_session_id is not None else previous.get("acp_session_id"),
        backend=backend,
    )


def _wrapper_command_args(wrapper_binary: str = "claude-anyteam-wrapper") -> tuple[str, list[str]]:
    resolved = shutil.which(wrapper_binary)
    if resolved:
        return str(Path(resolved).resolve()), []
    return sys.executable, ["-m", "claude_anyteam.wrapper_server"]


def write_mcp_config(
    kimi_home: Path,
    *,
    team: str,
    agent_name: str,
    real_home: str | None = None,
    wrapper_binary: str = "claude-anyteam-wrapper",
) -> Path:
    """Write adapter-owned Kimi MCP config without mutating ``~/.kimi``."""
    prepare_isolated_kimi_home(kimi_home, real_home=real_home)
    env = identity_env(os.environ, team=team, name=agent_name)
    if real_home:
        env["HOME"] = real_home
    command, prefix_args = _wrapper_command_args(wrapper_binary)
    data = {
        "mcpServers": {
            WRAPPER_SERVER_ALIAS: {
                "command": command,
                "args": [*prefix_args, "--team", team, "--name", agent_name],
                "env": {k: env[k] for k in (
                    "HOME",
                    "CLAUDE_ANYTEAM_TEAM",
                    "CLAUDE_ANYTEAM_NAME",
                    "CODEX_TEAMMATE_TEAM",
                    "CODEX_TEAMMATE_NAME",
                ) if k in env},
            }
        }
    }
    path = kimi_home / ".kimi" / "anyteam-mcp.json"
    _write_atomic_json(path, data)
    return path


# Alias matching Gemini's naming pattern for tests/extensions.
write_mcp_settings = write_mcp_config


def _check_kimi_signin(home: Path | None = None) -> tuple[bool, str | None]:
    base = home or Path.home()
    path = base / KIMI_CREDENTIALS_REL
    try:
        if not path.exists():
            return False, f"Kimi credentials not found: {path}"
        if not path.is_file() or path.stat().st_size <= 0:
            return False, f"Kimi credentials file empty: {path}"
        return True, None
    except OSError as exc:
        return False, f"Kimi credentials check failed: {exc}"


def feature_test(kimi_binary: str = "kimi") -> None:
    resolved = shutil.which(kimi_binary)
    if not resolved:
        raise RuntimeError(f"kimi binary not found on PATH (expected {kimi_binary!r}). Install and authenticate Kimi CLI.")
    resolved_wrapper = shutil.which("claude-anyteam-wrapper")
    if not resolved_wrapper:
        raise RuntimeError(
            "claude-anyteam-wrapper not on PATH. Ensure the adapter is installed "
            "in this environment (e.g. `uv sync` or `pip install -e .`)."
        )
    try:
        info = subprocess.run([kimi_binary, "info"], capture_output=True, text=True, timeout=10, check=True)
        # Kimi's typer/rich help truncates long flag names with "…" at narrow
        # terminal widths (e.g. `--mcp-config-fi…`), which breaks the
        # substring probe below. Force a wide column count so every flag
        # name renders intact regardless of the parent terminal size.
        help_env = dict(os.environ)
        help_env["COLUMNS"] = "2000"
        help_out = subprocess.run(
            [kimi_binary, "--help"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
            env=help_env,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        raise RuntimeError(f"could not probe Kimi CLI {kimi_binary!r}: {exc}") from exc
    help_text = (help_out.stdout or "") + (help_out.stderr or "")
    missing = [flag for flag in ("--print", "--output-format", "--mcp-config-file", "--no-thinking") if flag not in help_text]
    if missing:
        raise RuntimeError(f"Kimi CLI is missing required flags {missing}; info output {(info.stdout or info.stderr).strip()}")
    signed_in, detail = _check_kimi_signin(Path.home())
    if not signed_in:
        logger.warn("kimi.signin.missing", detail=detail)
    logger.info(
        "kimi.version",
        binary=str(Path(resolved).resolve()),
        wrapper_binary=str(Path(resolved_wrapper).resolve()),
        info=(info.stdout or info.stderr).strip(),
    )


def credential_preflight(
    *,
    kimi_binary: str = "kimi",
    cwd: Path,
    team: str,
    agent_name: str,
    model: str | None = None,
    effort: str | None = None,
    kimi_home: Path | None = None,
    thinking: str = "auto",
    timeout_s: float = 45.0,
) -> None:
    """Run a cheap Kimi API probe in the adapter's isolated HOME.

    Kimi's local ``info``/``--help`` checks do not validate MOONSHOT_API_KEY or
    OAuth token freshness.  This tiny ``--print`` call fails spawn before
    registration when the remote API returns 401/quota errors.
    """

    real_home = os.environ.get("HOME")
    home = kimi_home or default_kimi_home(team, agent_name)
    mcp_config = write_mcp_config(home, team=team, agent_name=agent_name, real_home=real_home)
    args = [
        kimi_binary,
        "--print",
        "--output-format=stream-json",
        "--work-dir",
        str(cwd),
        "--mcp-config-file",
        str(mcp_config),
        *(_thinking_args(thinking=thinking, effort=effort)),
    ]
    if model:
        args.extend(["--model", model])
    args.extend(["-p", "ping"])

    sub_env = dict(os.environ)
    sub_env["HOME"] = str(home)
    sub_env.setdefault("KIMI_CLI_NO_AUTO_UPDATE", "1")
    if real_home:
        sub_env["CLAUDE_ANYTEAM_REAL_HOME"] = real_home
    sub_env = identity_env(sub_env, team=team, name=agent_name)

    # Kimi headless emits "Unknown error: [Errno 2] No such file or directory"
    # for cwd if the work-dir doesn't exist yet. Stress harnesses create the
    # sandbox lazily, so ensure the dir exists before invoking kimi.
    try:
        cwd.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    logger.info(
        "kimi.auth_preflight.start",
        cwd=str(cwd),
        kimi_home=str(home),
        model=model,
        effort=effort,
        thinking=thinking,
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
        raise RuntimeError(f"Kimi auth preflight timed out after {timeout_s}s\n{detail}") from exc
    except (subprocess.SubprocessError, OSError) as exc:
        raise RuntimeError(f"could not run Kimi auth preflight {kimi_binary!r}: {exc}") from exc

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
                backend="kimi",
                error_class=error_class,
                error_message=diagnostic,
                reset_after_seconds=reset_after,
                cmd=args,
                returncode=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
            )
        raise RuntimeError(f"Kimi auth preflight exited {proc.returncode}\n{diagnostic}")
    logger.info("kimi.auth_preflight.ok", model=model, thinking=thinking)


def _loads_json_line(line: str) -> dict[str, Any] | None:
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _extract_session_id(stderr: str) -> str | None:
    match = SESSION_HINT_RE.search(stderr or "")
    return match.group(1) if match else None


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text" and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts)
    return ""


def _tool_call_name(call: Any) -> str | None:
    if not isinstance(call, dict):
        return None
    function = call.get("function")
    if isinstance(function, dict) and isinstance(function.get("name"), str):
        return function["name"]
    if isinstance(call.get("name"), str):
        return call["name"]
    return None


def _validate_tool_call_arguments(call: dict[str, Any]) -> bool:
    function = call.get("function")
    if not isinstance(function, dict):
        return True
    arguments = function.get("arguments")
    if arguments in (None, ""):
        return True
    if isinstance(arguments, dict):
        return True
    if not isinstance(arguments, str):
        return True
    try:
        json.loads(arguments)
        return True
    except json.JSONDecodeError as exc:
        logger.warn(
            "kimi.tool_call_arguments_invalid",
            tool=function.get("name"),
            error=str(exc),
            arguments_preview=arguments[:120],
        )
        return False


def _parse_stdout(stdout: str) -> tuple[list[dict[str, Any]], str, int]:
    events: list[dict[str, Any]] = []
    last_message = ""
    tool_call_events = 0
    for line in stdout.splitlines():
        if not line.strip():
            continue
        ev = _loads_json_line(line)
        if ev is None:
            logger.debug("kimi.nonjson_stdout", line=line[:200])
            events.append({"type": "non_json_stdout", "line": line})
            continue
        events.append(ev)
        if ev.get("role") == "assistant":
            tool_calls = ev.get("tool_calls")
            if isinstance(tool_calls, list) and tool_calls:
                for call in tool_calls:
                    if isinstance(call, dict):
                        name = _tool_call_name(call)
                        _validate_tool_call_arguments(call)
                        tool_call_events += 1
                        logger.info("kimi.tool_call", tool=name)
            text = _content_text(ev.get("content"))
            if text:
                last_message = text
    return events, last_message.strip(), tool_call_events


def _has_json_events(events: list[dict[str, Any]]) -> bool:
    return any(ev.get("type") != "non_json_stdout" for ev in events)


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


def _session_hash(cwd: Path) -> str:
    return hashlib.md5(str(cwd.resolve()).encode("utf-8")).hexdigest()


def _session_dir(kimi_home: Path, cwd: Path, session_id: str) -> Path:
    return kimi_home / ".kimi" / "sessions" / _session_hash(cwd) / session_id


def _known_session(kimi_home: Path, cwd: Path, session_id: str | None) -> bool:
    if not session_id:
        return False
    return _session_dir(kimi_home, cwd, session_id).is_dir()


def _thinking_args(*, thinking: str = "auto", effort: str | None = None) -> list[str]:
    if thinking == "off":
        return ["--no-thinking"]
    if thinking == "on":
        # Kimi defaults to thinking per config/model; keep argv stable.
        return []
    if effort in {"minimal", "low"}:
        return ["--no-thinking"]
    return []


def _prompt_with_schema(prompt: str, schema_obj: dict[str, Any] | None, *, retry_error: str | None = None) -> str:
    out = prompt
    if schema_obj is not None and "Your final response MUST be a single JSON object matching this schema:" not in out:
        out += "\n\n# Output contract\n" + inline_schema_prompt_fragment(schema_obj)
    if retry_error:
        out += "\n\nPRIOR ATTEMPT FAILED schema validation: " + retry_error + "\nReturn ONLY the JSON object matching the schema."
    return out


def _run_once(
    prompt: str,
    *,
    cwd: Path,
    schema_obj: dict[str, Any] | None,
    schema_path: Path | None,
    kimi_binary: str,
    timeout_s: float,
    wrapper_identity: tuple[str, str] | None,
    resume_session_id: str | None,
    model: str | None,
    effort: str | None,
    kimi_home: Path | None,
    thinking: str,
    task_id: str | None,
    event_sink: Callable[[VisibilityEvent], None] | None = None,
    retry_error: str | None = None,
) -> CodexResult:
    team, agent = wrapper_identity or ("default", "kimi")
    real_home = os.environ.get("HOME")
    home = kimi_home or default_kimi_home(team, agent)
    mcp_config = write_mcp_config(home, team=team, agent_name=agent, real_home=real_home)

    launch_prompt = _prompt_with_schema(prompt, schema_obj, retry_error=retry_error)
    args = [
        kimi_binary,
        "--print",
        "--output-format=stream-json",
        "--work-dir",
        str(cwd),
        "--mcp-config-file",
        str(mcp_config),
        *(_thinking_args(thinking=thinking, effort=effort)),
    ]
    if model:
        args.extend(["--model", model])
    if resume_session_id:
        if _known_session(home, cwd, resume_session_id):
            args.extend(["--session", resume_session_id])
        else:
            logger.warn("kimi.resume_session_missing", session_id=resume_session_id, session_dir=str(_session_dir(home, cwd, resume_session_id)))
    args.extend(["-p", launch_prompt])

    sub_env = dict(os.environ)
    sub_env["HOME"] = str(home)
    sub_env.setdefault("KIMI_CLI_NO_AUTO_UPDATE", "1")
    if real_home:
        sub_env["CLAUDE_ANYTEAM_REAL_HOME"] = real_home
    if wrapper_identity:
        sub_env = identity_env(sub_env, team=team, name=agent)

    logger.info(
        "kimi.invoke",
        cwd=str(cwd),
        kimi_home=str(home),
        schema=str(schema_path) if schema_path else None,
        resumed=bool(resume_session_id and "--session" in args),
        model=model,
        effort=effort,
        thinking=thinking,
    )
    visibility = HeadlessTurnVisibility.start(
        team=team,
        agent=agent,
        backend="kimi_headless",
        enabled=wrapper_identity is not None,
        cwd=cwd,
        schema=schema_path,
        timeout_s=timeout_s,
        model=model,
        effort=effort,
        resume_session_id=resume_session_id,
        task_id=task_id,
        extra_payload={"thinking": thinking},
        event_sink=event_sink,
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
        timeout_stdout = coerce_stream_text(getattr(exc, "stdout", None) or getattr(exc, "output", None))
        timeout_stderr = coerce_stream_text(getattr(exc, "stderr", None))
        events, last_message, tool_call_events = _parse_stdout(timeout_stdout)
        captured_session_id = _extract_session_id(timeout_stderr)
        error = f"kimi timed out after {timeout_s}s"
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
            partial_events_available=_has_json_events(events),
            session_id=captured_session_id,
            error_class="turn_timeout",
            extra_payload={"tool_call_event_source": "kimi assistant.tool_calls[]"},
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

    events, last_message, tool_call_events = _parse_stdout(proc.stdout)
    captured_session_id = _extract_session_id(proc.stderr)
    structured: dict[str, Any] | None = None
    error: str | None = None
    if schema_obj is not None:
        parsed, err = parse_and_validate(_extract_json_candidate(last_message), schema_obj)
        structured = parsed
        if err:
            error = f"kimi final message failed schema validation: {err}"

    if proc.returncode != 0 and not error:
        # Limit failures often put the diagnostic on stdout, not stderr.
        diagnostic = (proc.stderr or "").strip() or (proc.stdout or "").strip()
        error = f"kimi exited {proc.returncode}; output: {diagnostic[:500]}"

    if captured_session_id:
        write_adapter_state(home, backend="headless", headless_session_id=captured_session_id)

    success = proc.returncode == 0 and error is None
    visibility.terminal(
        success=success,
        exit_code=proc.returncode,
        error=error,
        events=events,
        tool_call_events=tool_call_events,
        last_message=last_message,
        structured=structured is not None,
        partial_events_available=_has_json_events(events),
        session_id=captured_session_id,
        extra_payload={"tool_call_event_source": "kimi assistant.tool_calls[]"},
    )

    return CodexResult(
        exit_code=proc.returncode,
        structured=structured,
        last_message=last_message,
        events=events,
        error=error,
        tool_call_events=tool_call_events,
        session_id=captured_session_id,
    )


def run(
    prompt: str,
    *,
    cwd: Path,
    schema: Path | None = None,
    kimi_binary: str = "kimi",
    timeout_s: float = 1800.0,
    wrapper_identity: tuple[str, str] | None = None,
    resume_session_id: str | None = None,
    model: str | None = None,
    effort: str | None = None,
    kimi_home: Path | None = None,
    thinking: str = "auto",
    task_id: str | None = None,
    event_sink: Callable[[VisibilityEvent], None] | None = None,
) -> CodexResult:
    """Single Kimi invocation.

    Schema-validation retries are owned by the loop layer (mirroring Codex's
    separation of concerns at codex.py:run() — never retries; loop owns
    retry policy). Earlier versions of this function ran a second
    invocation on schema failure; combined with the loop's own attempt
    pair, that produced up to 4 Kimi CLI runs per task. The loop now
    issues attempt 2 itself with a tightened prompt.
    """
    schema_obj = load_schema(schema) if schema is not None else None
    return _run_once(
        prompt,
        cwd=cwd,
        schema_obj=schema_obj,
        schema_path=schema,
        kimi_binary=kimi_binary,
        timeout_s=timeout_s,
        wrapper_identity=wrapper_identity,
        resume_session_id=resume_session_id,
        model=model,
        effort=effort,
        kimi_home=kimi_home,
        thinking=thinking,
        task_id=task_id,
        event_sink=event_sink,
    )
