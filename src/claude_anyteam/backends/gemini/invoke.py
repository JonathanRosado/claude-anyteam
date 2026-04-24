"""Gemini CLI invocation for claude-anyteam."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from claude_anyteam import logger
from claude_anyteam.codex import CodexResult, PLAN_SCHEMA, TASK_COMPLETE_SCHEMA
from claude_anyteam.env import identity_env
from claude_anyteam.schema_validation import load_schema, parse_and_validate

WRAPPER_SERVER_ALIAS = "anyteam"
WRAPPER_TOOL_PREFIX = f"mcp_{WRAPPER_SERVER_ALIAS}_"


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


def ensure_adapter_state(gemini_home: Path) -> Path:
    path = _adapter_state_path(gemini_home)
    if not path.exists():
        write_adapter_state(gemini_home, backend="headless")
    return path


def read_adapter_state(gemini_home: Path) -> dict[str, Any]:
    path = _adapter_state_path(gemini_home)
    if not path.exists():
        return {
            "headless_session_id": None,
            "acp_session_id": None,
            "backend": "headless",
            "updated_at": None,
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "headless_session_id": None,
            "acp_session_id": None,
            "backend": "headless",
            "updated_at": None,
        }
    if not isinstance(data, dict):
        return {
            "headless_session_id": None,
            "acp_session_id": None,
            "backend": "headless",
            "updated_at": None,
        }
    data.setdefault("headless_session_id", None)
    data.setdefault("acp_session_id", None)
    data.setdefault("backend", "headless")
    data.setdefault("updated_at", None)
    return data


def write_adapter_state(
    gemini_home: Path,
    *,
    backend: str,
    headless_session_id: str | None = None,
    acp_session_id: str | None = None,
) -> Path:
    previous = read_adapter_state(gemini_home) if _adapter_state_path(gemini_home).exists() else {}
    data = {
        "headless_session_id": headless_session_id if headless_session_id is not None else previous.get("headless_session_id"),
        "acp_session_id": acp_session_id if acp_session_id is not None else previous.get("acp_session_id"),
        "backend": backend,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }
    path = _adapter_state_path(gemini_home)
    _write_atomic_json(path, data)
    return path


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


def run(
    prompt: str,
    *,
    cwd: Path,
    schema: Path | None = None,
    gemini_binary: str = "gemini",
    timeout_s: float = 600.0,
    wrapper_identity: tuple[str, str] | None = None,
    resume_session_id: str | None = None,
    model: str | None = None,
    gemini_home: Path | None = None,
) -> CodexResult:
    team, agent = wrapper_identity or ("default", "gemini")
    real_home = os.environ.get("HOME")
    home = gemini_home or _default_gemini_home(team, agent)
    write_mcp_settings(home, team=team, agent_name=agent, real_home=real_home, cwd=cwd)

    args = [gemini_binary, "--prompt", prompt, "--output-format", "stream-json", "--approval-mode", "yolo"]
    if model:
        args.extend(["--model", model])
    if resume_session_id:
        args.extend(["--resume", resume_session_id])

    sub_env = dict(os.environ)
    sub_env["HOME"] = str(home)
    if real_home:
        sub_env["CLAUDE_ANYTEAM_REAL_HOME"] = real_home
    if wrapper_identity:
        sub_env = identity_env(sub_env, team=team, name=agent)

    events: list[dict[str, Any]] = []
    last_message_parts: list[str] = []
    tool_call_events = 0
    captured_session_id: str | None = None
    seen_non_init_event = False
    error: str | None = None

    logger.info("gemini.invoke", cwd=str(cwd), gemini_home=str(home), schema=str(schema) if schema else None, resumed=bool(resume_session_id))
    try:
        proc = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, timeout=timeout_s, check=False, env=sub_env, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return CodexResult(exit_code=124, structured=None, last_message="", events=[], error=f"gemini timed out after {timeout_s}s")

    for line in proc.stdout.splitlines():
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

    last_message = "".join(last_message_parts).strip()
    structured: dict[str, Any] | None = None
    if schema is not None:
        parsed, err = parse_and_validate(_extract_json_candidate(last_message), load_schema(schema))
        structured = parsed
        if err:
            error = f"gemini final message failed schema validation: {err}"
    terminal = next((ev for ev in reversed(events) if ev.get("type") == "result"), None)
    exit_code = proc.returncode
    if proc.returncode != 0 and not error:
        error = f"gemini exited {proc.returncode}; stderr: {proc.stderr[:500]}"
    elif terminal is None:
        if not error:
            error = "gemini stream ended without result event"
        if exit_code == 0:
            exit_code = 1
    elif terminal.get("status") not in (None, "success") and not error:
        error = f"gemini result status {terminal.get('status')!r}"

    if captured_session_id:
        write_adapter_state(home, backend="headless", headless_session_id=captured_session_id)

    return CodexResult(
        exit_code=exit_code,
        structured=structured,
        last_message=last_message,
        events=events,
        error=error,
        tool_call_events=tool_call_events,
        session_id=captured_session_id,
    )
