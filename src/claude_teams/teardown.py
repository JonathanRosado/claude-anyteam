"""Fast team teardown helpers.

The normal lifecycle path asks each teammate to shut down, waits for a
shutdown_approved message, then removes one member at a time.  That is the
right default for graceful operation, but it is painful when the operator is
trying to tear down a whole team before creating the next one — especially if
one routed teammate is wedged mid-wrapper-tool failure.

This module implements the explicit destructive path used by the
``claude-anyteam team-kill`` CLI and the lead-only ``force_kill_team`` MCP tool:
request graceful shutdown in parallel, wait a small bounded budget, then kill
any remaining tmux targets / known wrapper subprocesses and remove those
members from config.
"""

from __future__ import annotations

import json
import os
import signal
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from claude_teams import messaging, tasks, teams
from claude_teams.models import TeammateMember
from claude_teams.spawner import kill_tmux_pane

DEFAULT_GRACEFUL_TIMEOUT_S = 5.0
WRAPPER_PID_TERM_TIMEOUT_S = 0.5


def _teams_root(base_dir: Path | None = None) -> Path:
    return (base_dir / "teams") if base_dir else teams.TEAMS_DIR


def _tasks_root(base_dir: Path | None = None) -> Path:
    return (base_dir / "tasks") if base_dir else teams.TASKS_DIR


def _team_root(team_name: str, base_dir: Path | None = None) -> Path:
    return _teams_root(base_dir) / team_name


def _teammate_members(team_name: str, base_dir: Path | None = None) -> list[TeammateMember]:
    config = teams.read_config(team_name, base_dir=base_dir)
    return [m for m in config.members if isinstance(m, TeammateMember)]


def _teammate_names(team_name: str, base_dir: Path | None = None) -> set[str]:
    return {m.name for m in _teammate_members(team_name, base_dir=base_dir)}


def _latest_wrapper_diag_pid(
    team_name: str,
    agent_name: str,
    *,
    base_dir: Path | None = None,
) -> int | None:
    path = _team_root(team_name, base_dir) / "diagnostics" / "wrapper-mcp-tools.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, OSError):
        return None

    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict) or row.get("agent") != agent_name:
            continue
        pid = row.get("pid")
        if isinstance(pid, int) and pid > 0:
            return pid
    return None


def _pid_alive(pid: int) -> bool | None:
    if not isinstance(pid, int) or pid <= 0 or os.name != "posix":
        return None
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None


def _decode_proc_bytes(raw: bytes) -> list[str]:
    return [part.decode("utf-8", "surrogateescape") for part in raw.split(b"\0") if part]


def _arg_value(args: list[str], flag: str) -> str | None:
    prefix = flag + "="
    for idx, arg in enumerate(args):
        if arg == flag and idx + 1 < len(args):
            return args[idx + 1]
        if arg.startswith(prefix):
            return arg[len(prefix):]
    return None


def _pid_matches_identity(pid: int, *, team_name: str, agent_name: str) -> bool:
    """Best-effort guard against killing a reused unrelated PID.

    Wrapper MCP subprocesses get the team/name either in environment
    (Gemini/native Claude) or CLI args (Codex App Server).  On Linux we can
    validate either source through /proc before sending a signal.  If /proc is
    unavailable or does not identify this exact team+agent, we skip the PID kill
    rather than risk killing an unrelated process whose PID was reused.
    """
    if os.name != "posix" or pid <= 0 or pid == os.getpid():
        return False

    proc_root = Path("/proc") / str(pid)
    if not proc_root.exists():
        return False

    try:
        env_parts = _decode_proc_bytes((proc_root / "environ").read_bytes())
        env: dict[str, str] = {}
        for part in env_parts:
            key, sep, value = part.partition("=")
            if sep:
                env[key] = value
        if env.get("CLAUDE_ANYTEAM_TEAM") == team_name and env.get("CLAUDE_ANYTEAM_NAME") == agent_name:
            return True
        if env.get("CODEX_TEAMMATE_TEAM") == team_name and env.get("CODEX_TEAMMATE_NAME") == agent_name:
            return True
    except OSError:
        pass

    try:
        args = _decode_proc_bytes((proc_root / "cmdline").read_bytes())
    except OSError:
        args = []
    if not args:
        return False

    team_arg = _arg_value(args, "--team")
    name_arg = _arg_value(args, "--name")
    if team_arg == team_name and name_arg == agent_name:
        command_blob = " ".join(args)
        return (
            "claude-anyteam-wrapper" in command_blob
            or "codex-teammate-wrapper" in command_blob
            or "claude_anyteam.wrapper_server" in command_blob
        )
    return False


def _kill_validated_wrapper_pid(
    pid: int | None,
    *,
    team_name: str,
    agent_name: str,
    timeout_s: float = WRAPPER_PID_TERM_TIMEOUT_S,
) -> tuple[bool, list[str]]:
    """Terminate a wrapper PID only when it validates as this team/agent."""
    if pid is None:
        return False, []
    errors: list[str] = []
    if pid == os.getpid():
        return False, [f"refusing to signal current process pid={pid}"]
    alive = _pid_alive(pid)
    if alive is False:
        return False, []
    if not _pid_matches_identity(pid, team_name=team_name, agent_name=agent_name):
        return False, [f"skipped wrapper pid {pid}: identity did not match {team_name}/{agent_name}"]
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return False, []
    except OSError as exc:
        return False, [f"SIGTERM wrapper pid {pid} failed: {exc}"]

    deadline = time.monotonic() + max(0.0, timeout_s)
    while time.monotonic() < deadline:
        if _pid_alive(pid) is False:
            return True, []
        time.sleep(0.05)

    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return True, []
    except OSError as exc:
        errors.append(f"SIGKILL wrapper pid {pid} failed: {exc}")
    return True, errors


def _cleanup_member_files(team_name: str, agent_name: str, *, base_dir: Path | None = None) -> list[str]:
    removed: list[str] = []
    for path in (
        _team_root(team_name, base_dir) / "inboxes" / f"{agent_name}.json",
        _team_root(team_name, base_dir) / "manifests" / f"{agent_name}.json",
    ):
        try:
            path.unlink()
            removed.append(str(path))
        except FileNotFoundError:
            continue
        except OSError:
            continue
    return removed


def _send_shutdown_requests_parallel(
    team_name: str,
    members: list[TeammateMember],
    *,
    reason: str,
    base_dir: Path | None = None,
) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {
        m.name: {"name": m.name, "shutdown_request_id": None, "shutdown_error": None}
        for m in members
    }
    if not members:
        return results

    max_workers = min(32, max(1, len(members)))
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="team-kill-shutdown") as executor:
        future_to_name = {
            executor.submit(
                messaging.send_shutdown_request,
                team_name,
                member.name,
                reason,
                base_dir,
            ): member.name
            for member in members
        }
        for future in as_completed(future_to_name):
            name = future_to_name[future]
            try:
                results[name]["shutdown_request_id"] = future.result()
            except Exception as exc:  # best-effort teardown should continue
                results[name]["shutdown_error"] = str(exc)
    return results


def _wait_for_graceful_exits(
    team_name: str,
    initial_names: set[str],
    *,
    timeout_s: float,
    base_dir: Path | None = None,
) -> set[str]:
    """Return names that disappeared from config before timeout."""
    deadline = time.monotonic() + max(0.0, timeout_s)
    remaining = set(initial_names)
    while remaining and time.monotonic() < deadline:
        try:
            remaining = _teammate_names(team_name, base_dir=base_dir) & initial_names
        except FileNotFoundError:
            return set(initial_names)
        if not remaining:
            break
        time.sleep(0.1)
    try:
        remaining = _teammate_names(team_name, base_dir=base_dir) & initial_names
    except FileNotFoundError:
        remaining = set()
    return initial_names - remaining


def force_kill_team(
    team_name: str,
    *,
    force: bool,
    purge: bool = False,
    graceful_timeout_s: float = DEFAULT_GRACEFUL_TIMEOUT_S,
    reason: str = "fast team teardown requested",
    base_dir: Path | None = None,
) -> dict[str, Any]:
    """Forcefully tear down all non-lead teammates in a team.

    Args:
        team_name: Team to tear down.
        force: Must be true.  The explicit flag keeps accidental CLI calls from
            becoming destructive; the MCP tool passes true by construction.
        purge: When true, delete the team config dir and tasks dir after all
            teammates are removed.
        graceful_timeout_s: Seconds to wait for adapters to deregister after
            parallel shutdown_request fan-out before force-killing leftovers.
        reason: shutdown_request reason string.
        base_dir: Test hook matching the rest of ``claude_teams``.
    """
    if not force:
        raise ValueError("team-kill is destructive; re-run with --force to stop teammates")
    if graceful_timeout_s < 0:
        raise ValueError("graceful_timeout_s must be >= 0")

    started_at = time.monotonic()
    members = _teammate_members(team_name, base_dir=base_dir)
    initial_by_name = {m.name: m for m in members}
    results = _send_shutdown_requests_parallel(
        team_name,
        members,
        reason=reason,
        base_dir=base_dir,
    )

    graceful = _wait_for_graceful_exits(
        team_name,
        set(initial_by_name),
        timeout_s=graceful_timeout_s,
        base_dir=base_dir,
    )
    for name in graceful:
        results.setdefault(name, {"name": name})["graceful"] = True
        results[name]["forced"] = False

    try:
        remaining_members = {
            m.name: m
            for m in _teammate_members(team_name, base_dir=base_dir)
            if m.name in initial_by_name
        }
    except FileNotFoundError:
        remaining_members = {}

    forced_names: list[str] = []
    for name, member in remaining_members.items():
        row = results.setdefault(name, {"name": name})
        row["graceful"] = False
        row["forced"] = True
        row["tmux_pane_id"] = member.tmux_pane_id
        row["tmux_killed"] = False
        row["wrapper_pid"] = None
        row["wrapper_pid_killed"] = False
        row["removed"] = False
        row["tasks_reset"] = False
        row.setdefault("errors", [])

        pane_id = member.tmux_pane_id
        if pane_id and pane_id != "in-process":
            try:
                kill_tmux_pane(pane_id)
                row["tmux_killed"] = True
            except Exception as exc:
                row["errors"].append(f"kill tmux target {pane_id!r} failed: {exc}")

        wrapper_pid = _latest_wrapper_diag_pid(team_name, name, base_dir=base_dir)
        row["wrapper_pid"] = wrapper_pid
        killed_pid, pid_errors = _kill_validated_wrapper_pid(
            wrapper_pid,
            team_name=team_name,
            agent_name=name,
        )
        row["wrapper_pid_killed"] = killed_pid
        row["errors"].extend(pid_errors)

        try:
            teams.remove_member(team_name, name, base_dir=base_dir)
            row["removed"] = True
        except Exception as exc:
            row["errors"].append(f"remove member failed: {exc}")

        try:
            tasks.reset_owner_tasks(team_name, name, base_dir=base_dir)
            row["tasks_reset"] = True
        except Exception as exc:
            row["errors"].append(f"reset owned tasks failed: {exc}")

        row["cleaned_files"] = _cleanup_member_files(team_name, name, base_dir=base_dir)
        forced_names.append(name)

    purged = False
    purge_error: str | None = None
    if purge:
        try:
            teams.delete_team(team_name, base_dir=base_dir)
            purged = True
        except Exception as exc:
            purge_error = str(exc)

    elapsed_s = time.monotonic() - started_at
    message = (
        f"team {team_name!r}: shutdown_request sent to {len(members)} teammate(s); "
        f"{len(graceful)} exited gracefully; {len(forced_names)} force-killed"
    )
    if purged:
        message += "; purged team state"
    elif purge_error:
        message += f"; purge failed: {purge_error}"

    return {
        "success": purge_error is None,
        "team_name": team_name,
        "requested": len(members),
        "graceful_timeout_s": graceful_timeout_s,
        "elapsed_s": elapsed_s,
        "graceful": sorted(graceful),
        "forced": sorted(forced_names),
        "purge": purge,
        "purged": purged,
        "purge_error": purge_error,
        "members": [results[name] for name in sorted(results)],
        "message": message,
        "team_dir": str(_team_root(team_name, base_dir)),
        "tasks_dir": str(_tasks_root(base_dir) / team_name),
    }
