"""Gemini CLI invocation for claude-anyteam."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
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


def _link_auth_cache(settings_dir: Path, real_home: str | None) -> None:
    """Expose real Gemini auth cache inside isolated HOME without copying settings.

    Gemini reads ~/.gemini for OAuth/account files. The adapter isolates HOME so
    its MCP settings do not mutate the user's real ~/.gemini/settings.json, but
    symlinks credential/account files into that isolated home when present so
    existing sign-in still works. Wrapper MCP subprocesses receive real HOME in
    their env separately so team protocol tools see the user's normal config.
    """
    if not real_home:
        return
    source_dir = Path(real_home) / ".gemini"
    if not source_dir.exists():
        return
    for name in ("oauth_creds.json", "google_accounts.json", "projects.json", "trustedFolders.json", "installation_id"):
        src = source_dir / name
        dst = settings_dir / name
        if not src.exists() or dst.exists():
            continue
        try:
            dst.symlink_to(src)
        except OSError:
            if src.is_file():
                dst.write_bytes(src.read_bytes())


def write_mcp_settings(gemini_home: Path, *, team: str, agent_name: str, real_home: str | None = None) -> Path:
    """Write adapter-owned Gemini MCP config without mutating ~/.gemini."""
    settings_dir = gemini_home / ".gemini"
    settings_dir.mkdir(parents=True, exist_ok=True)
    _link_auth_cache(settings_dir, real_home)
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
    missing = [flag for flag in ("--prompt", "--output-format", "--resume") if flag not in help_text]
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
    write_mcp_settings(home, team=team, agent_name=agent, real_home=real_home)

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
    error: str | None = None

    logger.info("gemini.invoke", cwd=str(cwd), schema=str(schema) if schema else None, resumed=bool(resume_session_id))
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
            captured_session_id = ev["session_id"]
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
    if proc.returncode != 0 and not error:
        error = f"gemini exited {proc.returncode}; stderr: {proc.stderr[:500]}"
    elif isinstance(terminal, dict) and terminal.get("status") not in (None, "success") and not error:
        error = f"gemini result status {terminal.get('status')!r}"

    return CodexResult(
        exit_code=proc.returncode,
        structured=structured,
        last_message=last_message,
        events=events,
        error=error,
        tool_call_events=tool_call_events,
        session_id=captured_session_id,
    )
