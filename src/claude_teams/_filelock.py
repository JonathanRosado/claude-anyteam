from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import threading
from typing import NamedTuple

from filelock import FileLock, Timeout

DEFAULT_FILE_LOCK_TIMEOUT_S = 30.0
FILE_LOCK_TIMEOUT_ENV = "CLAUDE_ANYTEAM_FILELOCK_TIMEOUT_S"
LEGACY_FILE_LOCK_TIMEOUT_ENV = "CLAUDE_TEAMS_FILELOCK_TIMEOUT_S"

class _LockState(NamedTuple):
    thread_lock: threading.RLock
    file_lock: FileLock


_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, _LockState] = {}


def _default_timeout_s() -> float:
    raw = os.environ.get(FILE_LOCK_TIMEOUT_ENV) or os.environ.get(LEGACY_FILE_LOCK_TIMEOUT_ENV)
    if raw is None or raw == "":
        return DEFAULT_FILE_LOCK_TIMEOUT_S
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_FILE_LOCK_TIMEOUT_S
    return max(0.0, value)


def _lock_state_for(lock_path: Path, timeout: float) -> _LockState:
    key = str(lock_path)
    with _LOCKS_GUARD:
        state = _LOCKS.get(key)
        if state is None:
            state = _LockState(
                thread_lock=threading.RLock(),
                file_lock=FileLock(str(lock_path), timeout=timeout),
            )
            _LOCKS[key] = state
        return state


@contextmanager
def file_lock(lock_path: Path, timeout: float | None = None):
    """Acquire a filesystem lock with a bounded wait.

    R4: default to 30s (overridable with
    `CLAUDE_ANYTEAM_FILELOCK_TIMEOUT_S`) so a crashed teammate or stale lock
    holder cannot deadlock later adapter startups forever. Callers that need a
    shorter probe can pass `timeout=` directly.
    """

    effective_timeout = _default_timeout_s() if timeout is None else timeout
    # `filelock` uses process-scoped OS locks on Unix, so another thread in
    # this interpreter can otherwise enter the same critical section. Keep a
    # per-path in-process mutex in front of the cross-process lock so stress
    # tests and thread-backed adapters get the same serialization guarantee.
    #
    # The FileLock object is cached with the thread lock: FileLock's recursion
    # counter is instance-local, so constructing a fresh FileLock for an inner
    # write_config()/write_manifest() call would self-timeout while the outer
    # config_lock is already held by the same thread.
    state = _lock_state_for(lock_path, effective_timeout)
    thread_lock = state.thread_lock
    acquired = thread_lock.acquire() if effective_timeout < 0 else thread_lock.acquire(timeout=effective_timeout)
    if not acquired:
        raise Timeout(str(lock_path))
    try:
        # The filelock object must be shared per path so nested lock users in
        # the same thread are truly re-entrant, but the caller's timeout should
        # still be honored for each outer acquisition. Update it only after the
        # per-path thread lock is held so concurrent callers cannot race each
        # other's timeout setting.
        state.file_lock.timeout = effective_timeout
        with state.file_lock:
            yield
    finally:
        thread_lock.release()


def config_lock_path(team_dir: Path) -> Path:
    """Return the shared mutex path for a team's config-adjacent writes."""

    return team_dir / "config.lock"


@contextmanager
def config_lock(team_dir: Path, timeout: float | None = None):
    """Acquire the shared team config lock.

    R5: every team membership/config read-modify-write path, plus the
    capability-manifest writers that announce config-adjacent state to peers,
    coordinates through ``~/.claude/teams/<team>/config.lock``.
    """

    lock_path = config_lock_path(team_dir)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.touch(exist_ok=True)
    try:
        with file_lock(lock_path, timeout=timeout):
            yield
    finally:
        lock_path.touch(exist_ok=True)
