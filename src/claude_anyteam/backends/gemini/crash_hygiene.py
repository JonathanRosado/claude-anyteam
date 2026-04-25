"""Crash recovery helpers for Gemini ACP child processes."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from claude_anyteam import logger

from . import invoke


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    ppid: int | None
    pgid: int | None
    cmdline: list[str]
    environ: dict[str, str] | None


@dataclass
class ReaperSummary:
    orphan_processes_killed: int = 0
    orphan_pids: list[int] = field(default_factory=list)
    sessions_quarantined: int = 0
    quarantine_paths: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "orphan_processes_killed": self.orphan_processes_killed,
            "orphan_pids": self.orphan_pids,
            "sessions_quarantined": self.sessions_quarantined,
            "quarantine_paths": self.quarantine_paths,
        }


_ACTIVE_CLIENTS: set[Any] = set()
_ACTIVE_LOCK = threading.Lock()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def adapter_runtime_fields(*, team: str, agent: str, cwd: Path, generation: str | None = None) -> dict[str, Any]:
    return {
        "adapter_pid": os.getpid(),
        "adapter_start_time": utc_now(),
        "adapter_start_monotonic_ns": time.monotonic_ns(),
        "adapter_generation": generation or str(uuid.uuid4()),
        "adapter_exited_at": None,
        "team": team,
        "agent": agent,
        "cwd": str(cwd),
        "last_clean_shutdown_at": None,
    }


def pid_alive(pid: int | None) -> bool | None:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return None
    except OSError:
        return False


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def previous_adapter_died(state: dict[str, Any], *, current_pid: int | None = None) -> bool:
    pid = state.get("adapter_pid")
    if not isinstance(pid, int) or pid <= 0:
        return False
    if pid == (current_pid or os.getpid()):
        return False
    start = _parse_time(state.get("adapter_start_time"))
    clean = _parse_time(state.get("last_clean_shutdown_at"))
    if start is not None and clean is not None and clean >= start:
        return False
    alive = pid_alive(pid)
    if alive is None:
        logger.warn("gemini_acp.reaper.adapter_liveness_unknown", pid=pid)
        return False
    return not alive


def mark_adapter_start(gemini_home: Path, *, team: str, agent: str, cwd: Path, generation: str | None = None) -> str:
    gen = generation or str(uuid.uuid4())
    invoke.merge_adapter_state(gemini_home, **adapter_runtime_fields(team=team, agent=agent, cwd=cwd, generation=gen))
    return gen


def mark_clean_shutdown(gemini_home: Path) -> None:
    invoke.merge_adapter_state(
        gemini_home,
        last_clean_shutdown_at=utc_now(),
        adapter_exited_at=utc_now(),
        gemini_pid=None,
        gemini_pgid=None,
        gemini_started_at=None,
    )


def record_acp_child(gemini_home: Path, *, pid: int | None, pgid: int | None) -> None:
    invoke.merge_adapter_state(
        gemini_home,
        gemini_pid=pid,
        gemini_pgid=pgid,
        gemini_started_at=utc_now() if pid else None,
    )


def clear_acp_child(gemini_home: Path) -> None:
    invoke.merge_adapter_state(gemini_home, gemini_pid=None, gemini_pgid=None, gemini_started_at=None)


def register_active_client(client: Any) -> None:
    with _ACTIVE_LOCK:
        _ACTIVE_CLIENTS.add(client)


def unregister_active_client(client: Any) -> None:
    with _ACTIVE_LOCK:
        _ACTIVE_CLIENTS.discard(client)


def terminate_active_acp_children(*, reason: str = "shutdown", timeout: float = 3.0) -> None:
    with _ACTIVE_LOCK:
        clients = list(_ACTIVE_CLIENTS)
    for client in clients:
        try:
            logger.warn("gemini_acp.active_terminate", reason=reason, pid=getattr(client, "pid", None), pgid=getattr(client, "pgid", None))
            client.close(timeout=timeout)
        except Exception as e:
            logger.warn("gemini_acp.active_terminate_failed", reason=reason, error=str(e))


def _is_acp_gemini_process(info: ProcessInfo, gemini_binary: str) -> bool:
    if not info.cmdline:
        return False
    base = Path(gemini_binary).name
    exe_base = Path(info.cmdline[0]).name
    return exe_base == base and any(arg in {"--acp", "--experimental-acp"} for arg in info.cmdline[1:])


def _read_proc_process(pid: int) -> ProcessInfo | None:
    root = Path("/proc") / str(pid)
    try:
        raw_cmd = (root / "cmdline").read_bytes()
        cmdline = [p.decode(errors="ignore") for p in raw_cmd.split(b"\0") if p]
    except OSError:
        return None
    environ: dict[str, str] | None = None
    try:
        raw_env = (root / "environ").read_bytes()
        environ = {}
        for part in raw_env.split(b"\0"):
            if b"=" in part:
                key, value = part.split(b"=", 1)
                environ[key.decode(errors="ignore")] = value.decode(errors="ignore")
    except OSError:
        environ = None
    ppid = None
    try:
        fields = (root / "stat").read_text(encoding="utf-8", errors="ignore").split()
        if len(fields) > 3:
            ppid = int(fields[3])
    except (OSError, ValueError):
        pass
    pgid = None
    try:
        pgid = os.getpgid(pid)
    except OSError:
        pass
    return ProcessInfo(pid=pid, ppid=ppid, pgid=pgid, cmdline=cmdline, environ=environ)


def iter_processes() -> list[ProcessInfo]:
    if os.name != "posix" or not Path("/proc").exists():
        return []
    out: list[ProcessInfo] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        info = _read_proc_process(int(entry.name))
        if info is not None:
            out.append(info)
    return out


def _matches_adapter_home(info: ProcessInfo, *, gemini_home: Path, team: str, agent: str) -> bool:
    env = info.environ
    if env is None:
        return False
    try:
        if Path(env.get("HOME", "")).resolve() == gemini_home.resolve():
            return True
    except OSError:
        pass
    return env.get("CLAUDE_ANYTEAM_TEAM") == team and env.get("CLAUDE_ANYTEAM_NAME") == agent


def _terminate_pid_or_group(pid: int, pgid: int | None, *, timeout: float = 5.0) -> bool:
    target_group = os.name == "posix" and isinstance(pgid, int) and pgid > 0
    try:
        if target_group:
            os.killpg(pgid, signal.SIGTERM)  # type: ignore[arg-type]
        else:
            os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return False
    except OSError as e:
        logger.warn("gemini_acp.reaper.sigterm_failed", pid=pid, pgid=pgid, error=str(e))
        return False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pid_alive(pid) is False:
            return True
        time.sleep(0.05)
    try:
        if target_group:
            os.killpg(pgid, signal.SIGKILL)  # type: ignore[arg-type]
        else:
            os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    except OSError as e:
        logger.warn("gemini_acp.reaper.sigkill_failed", pid=pid, pgid=pgid, error=str(e))
        return False
    return True


def reap_orphan_acp_processes(
    *,
    gemini_home: Path,
    team: str,
    agent: str,
    gemini_binary: str = "gemini",
    recorded_pid: int | None = None,
    recorded_pgid: int | None = None,
    processes: list[ProcessInfo] | None = None,
) -> list[int]:
    killed: list[int] = []
    candidates = processes if processes is not None else iter_processes()
    by_pid = {p.pid: p for p in candidates}
    for info in candidates:
        if not _is_acp_gemini_process(info, gemini_binary):
            continue
        if info.ppid == os.getpid():
            continue
        if _matches_adapter_home(info, gemini_home=gemini_home, team=team, agent=agent):
            if _terminate_pid_or_group(info.pid, info.pgid):
                killed.append(info.pid)
    if recorded_pid and recorded_pid not in killed and recorded_pid not in by_pid and pid_alive(recorded_pid):
        # /proc env scanning may be unavailable. Fall back only to the exact pid/pgid we recorded.
        if _terminate_pid_or_group(recorded_pid, recorded_pgid):
            killed.append(recorded_pid)
    elif recorded_pid and recorded_pid in by_pid and recorded_pid not in killed:
        info = by_pid[recorded_pid]
        if _is_acp_gemini_process(info, gemini_binary) and _terminate_pid_or_group(info.pid, recorded_pgid or info.pgid):
            killed.append(info.pid)
    return killed


def quarantine_stale_acp_sessions(
    gemini_home: Path,
    *,
    previous_start_time: str | None,
    recovery_start_time: str | None = None,
    generation: str | None = None,
) -> list[Path]:
    chats_root = gemini_home / ".gemini" / "tmp"
    if not chats_root.exists():
        return []
    start_dt = _parse_time(previous_start_time) or datetime.fromtimestamp(0, timezone.utc)
    end_dt = (_parse_time(recovery_start_time) or datetime.now(timezone.utc)) + timedelta(seconds=1)
    graveyard = gemini_home / ".claude-anyteam" / "graveyard" / "acp-sessions" / (generation or str(int(time.time())))
    moved: list[Path] = []
    for path in list(chats_root.glob("**/chats/session-*.jsonl")):
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        except OSError:
            continue
        if mtime < start_dt or mtime > end_dt:
            continue
        rel = path.relative_to(gemini_home / ".gemini")
        dst = graveyard / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.replace(dst)
            moved.append(dst)
        except OSError as e:
            logger.warn("gemini_acp.reaper.quarantine_failed", path=str(path), error=str(e))
    return moved


def run_startup_recovery(
    *,
    gemini_home: Path,
    team: str,
    agent: str,
    cwd: Path,
    gemini_binary: str = "gemini",
    state: dict[str, Any] | None = None,
) -> ReaperSummary:
    state = state or invoke.read_adapter_state(gemini_home)
    recovery_start = utc_now()
    summary = ReaperSummary()
    if not previous_adapter_died(state):
        return summary
    state_team = state.get("team")
    state_agent = state.get("agent")
    if (state_team not in (None, team)) or (state_agent not in (None, agent)):
        logger.warn("gemini_acp.reaper.state_identity_mismatch", state_team=state_team, state_agent=state_agent, team=team, agent=agent)
        return summary
    recorded_pid = state.get("gemini_pid") if isinstance(state.get("gemini_pid"), int) else None
    recorded_pgid = state.get("gemini_pgid") if isinstance(state.get("gemini_pgid"), int) else None
    killed = reap_orphan_acp_processes(
        gemini_home=gemini_home,
        team=team,
        agent=agent,
        gemini_binary=gemini_binary,
        recorded_pid=recorded_pid,
        recorded_pgid=recorded_pgid,
    )
    moved = quarantine_stale_acp_sessions(
        gemini_home,
        previous_start_time=state.get("adapter_start_time"),
        recovery_start_time=recovery_start,
        generation=state.get("adapter_generation") if isinstance(state.get("adapter_generation"), str) else None,
    )
    summary.orphan_pids = killed
    summary.orphan_processes_killed = len(killed)
    summary.quarantine_paths = [str(p) for p in moved]
    summary.sessions_quarantined = len(moved)
    invoke.merge_adapter_state(
        gemini_home,
        acp_session_id=None,
        acp_storage_session_id=None,
        gemini_pid=None,
        gemini_pgid=None,
        gemini_started_at=None,
        last_reaper_run_at=recovery_start,
        last_reaper_summary=summary.to_dict(),
        team=team,
        agent=agent,
        cwd=str(cwd),
    )
    logger.warn("gemini_acp.reaper.completed", **summary.to_dict())
    return summary
