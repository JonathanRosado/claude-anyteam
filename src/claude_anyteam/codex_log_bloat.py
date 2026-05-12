"""Detection and bounded mitigation for Codex CLI sqlite WAL bloat.

Codex CLI stores its trace/state sqlite databases under the Codex sqlite home
(defaulting to ``~/.codex``). Issue #43 tracks an upstream failure mode where
``logs_*.sqlite-wal`` can grow large enough that ``codex app-server`` spends
its JSON-RPC initialize budget inside sqlite WAL recovery/checkpoint work.

This module keeps the claude-anyteam side small and harness-preserving:
we inspect Codex-owned files, emit typed visibility when they look dangerous,
and optionally ask sqlite to checkpoint through the public database API. We do
not delete, rotate, or rewrite Codex logs directly.
"""

from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .env import (
    CODEX_WAL_CHECKPOINT_ENV,
    CODEX_WAL_CHECKPOINT_TIMEOUT_ENV,
    CODEX_WAL_WARN_THRESHOLD_BYTES_ENV,
)

CODEX_HOME_ENV = "CODEX_HOME"
CODEX_SQLITE_HOME_ENV = "CODEX_SQLITE_HOME"

DEFAULT_CODEX_WAL_WARN_THRESHOLD_BYTES = 100 * 1024 * 1024
MIN_CODEX_WAL_WARN_THRESHOLD_BYTES = 1 * 1024 * 1024
MAX_CODEX_WAL_WARN_THRESHOLD_BYTES = 10 * 1024 * 1024 * 1024
DEFAULT_CODEX_WAL_CHECKPOINT_TIMEOUT_S = 10.0
MIN_CODEX_WAL_CHECKPOINT_TIMEOUT_S = 0.001
MAX_CODEX_WAL_CHECKPOINT_TIMEOUT_S = 60.0
LOGS_WAL_GLOB = "logs_*.sqlite-wal"

_FALSEY = {"0", "false", "no", "off", "disabled"}


@dataclass(frozen=True)
class CodexWalFile:
    """One Codex ``logs_*.sqlite-wal`` file and its adjacent sqlite files."""

    path: Path
    size_bytes: int
    threshold_bytes: int
    database_path: Path
    database_size_bytes: int | None = None
    shm_path: Path | None = None
    shm_size_bytes: int | None = None

    @property
    def exceeds_threshold(self) -> bool:
        return self.size_bytes > self.threshold_bytes

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "size_bytes": self.size_bytes,
            "size_mib": round(self.size_bytes / (1024 * 1024), 2),
            "threshold_bytes": self.threshold_bytes,
            "threshold_mib": round(self.threshold_bytes / (1024 * 1024), 2),
            "exceeds_threshold": self.exceeds_threshold,
            "database_path": str(self.database_path),
            "database_size_bytes": self.database_size_bytes,
            "database_size_mib": (
                round(self.database_size_bytes / (1024 * 1024), 2)
                if self.database_size_bytes is not None
                else None
            ),
            "shm_path": str(self.shm_path) if self.shm_path is not None else None,
            "shm_size_bytes": self.shm_size_bytes,
        }


@dataclass(frozen=True)
class CodexLogBloatReport:
    sqlite_home: Path
    sqlite_home_source: str
    threshold_bytes: int
    wal_files: tuple[CodexWalFile, ...]

    @property
    def bloated_wal_files(self) -> tuple[CodexWalFile, ...]:
        return tuple(row for row in self.wal_files if row.exceeds_threshold)

    @property
    def is_bloated(self) -> bool:
        return bool(self.bloated_wal_files)

    @property
    def max_wal_bytes(self) -> int:
        return max((row.size_bytes for row in self.wal_files), default=0)

    @property
    def total_wal_bytes(self) -> int:
        return sum(row.size_bytes for row in self.wal_files)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sqlite_home": str(self.sqlite_home),
            "sqlite_home_source": self.sqlite_home_source,
            "threshold_bytes": self.threshold_bytes,
            "threshold_mib": round(self.threshold_bytes / (1024 * 1024), 2),
            "status": "bloated" if self.is_bloated else "ok",
            "wal_file_count": len(self.wal_files),
            "bloated_wal_file_count": len(self.bloated_wal_files),
            "total_wal_bytes": self.total_wal_bytes,
            "total_wal_mib": round(self.total_wal_bytes / (1024 * 1024), 2),
            "max_wal_bytes": self.max_wal_bytes,
            "max_wal_mib": round(self.max_wal_bytes / (1024 * 1024), 2),
            "wal_files": [row.to_dict() for row in self.wal_files],
            "bloated_wal_files": [row.to_dict() for row in self.bloated_wal_files],
        }


@dataclass(frozen=True)
class CodexWalCheckpointResult:
    wal_path: Path
    database_path: Path
    attempted: bool
    status: str
    duration_ms: int = 0
    busy: int | None = None
    log_frames: int | None = None
    checkpointed_frames: int | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status in {"checkpointed", "no_wal"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "wal_path": str(self.wal_path),
            "database_path": str(self.database_path),
            "attempted": self.attempted,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "busy": self.busy,
            "log_frames": self.log_frames,
            "checkpointed_frames": self.checkpointed_frames,
            "error": self.error,
        }


def _truthy_env_enabled(env: Mapping[str, str], name: str, *, default: bool) -> bool:
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() not in _FALSEY


def _bounded_int_env(
    env: Mapping[str, str],
    name: str,
    default: int,
    *,
    min_value: int,
    max_value: int,
) -> int:
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
    if not (min_value <= value <= max_value):
        raise ValueError(
            f"{name} must be in [{min_value}, {max_value}], got {value}"
        )
    return value


def _bounded_float_env(
    env: Mapping[str, str],
    name: str,
    default: float,
    *,
    min_value: float,
    max_value: float,
) -> float:
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be numeric, got {raw!r}") from exc
    if not (min_value <= value <= max_value):
        raise ValueError(
            f"{name} must be in [{min_value:g}, {max_value:g}] seconds, got {value}"
        )
    return value


def codex_sqlite_home(
    env: Mapping[str, str] | None = None,
    *,
    home: Path | None = None,
) -> tuple[Path, str]:
    """Return the Codex sqlite directory and the source used to derive it.

    Empirical Codex CLI 0.128.0 behavior: ``CODEX_SQLITE_HOME`` redirects
    sqlite files; otherwise ``CODEX_HOME`` redirects the whole Codex home;
    otherwise sqlite files live in ``~/.codex``. ``CODEX_SQLITE_HOME`` is not
    a log-only redirect (it also moves state sqlite DBs), so claude-anyteam
    treats it as an operator setting to observe rather than forcing it for
    teammates.
    """

    env_map = env if env is not None else os.environ
    raw = env_map.get(CODEX_SQLITE_HOME_ENV)
    if raw:
        return Path(raw).expanduser(), CODEX_SQLITE_HOME_ENV
    raw = env_map.get(CODEX_HOME_ENV)
    if raw:
        return Path(raw).expanduser(), CODEX_HOME_ENV
    root = home if home is not None else Path.home()
    return root / ".codex", "default"


def codex_wal_warn_threshold_bytes(env: Mapping[str, str] | None = None) -> int:
    env_map = env if env is not None else os.environ
    return _bounded_int_env(
        env_map,
        CODEX_WAL_WARN_THRESHOLD_BYTES_ENV,
        DEFAULT_CODEX_WAL_WARN_THRESHOLD_BYTES,
        min_value=MIN_CODEX_WAL_WARN_THRESHOLD_BYTES,
        max_value=MAX_CODEX_WAL_WARN_THRESHOLD_BYTES,
    )


def inspect_codex_log_bloat(
    *,
    sqlite_home: Path | None = None,
    env: Mapping[str, str] | None = None,
    threshold_bytes: int | None = None,
) -> CodexLogBloatReport:
    """Stat Codex ``logs_*.sqlite-wal`` files without mutating them."""

    env_map = env if env is not None else os.environ
    if sqlite_home is None:
        sqlite_home, source = codex_sqlite_home(env_map)
    else:
        source = "explicit"
    if threshold_bytes is None:
        threshold = codex_wal_warn_threshold_bytes(env_map)
    else:
        threshold = int(threshold_bytes)
        if not (
            MIN_CODEX_WAL_WARN_THRESHOLD_BYTES
            <= threshold
            <= MAX_CODEX_WAL_WARN_THRESHOLD_BYTES
        ):
            raise ValueError(
                "threshold_bytes must be in "
                f"[{MIN_CODEX_WAL_WARN_THRESHOLD_BYTES}, "
                f"{MAX_CODEX_WAL_WARN_THRESHOLD_BYTES}], got {threshold}"
            )
    rows: list[CodexWalFile] = []
    try:
        paths = sorted(sqlite_home.glob(LOGS_WAL_GLOB))
    except OSError:
        paths = []
    for wal_path in paths:
        try:
            size = wal_path.stat().st_size
        except OSError:
            continue
        # Strip only the trailing WAL marker: ``Path.with_suffix("")`` would
        # turn ``logs_2.sqlite-wal`` into ``logs_2`` because pathlib treats
        # ``.sqlite-wal`` as one suffix.
        database_path = Path(str(wal_path)[:-4])
        shm_path = database_path.with_name(database_path.name + "-shm")
        try:
            db_size = database_path.stat().st_size
        except OSError:
            db_size = None
        try:
            shm_size = shm_path.stat().st_size
        except OSError:
            shm_size = None
            shm_path_for_row: Path | None = None
        else:
            shm_path_for_row = shm_path
        rows.append(
            CodexWalFile(
                path=wal_path,
                size_bytes=size,
                threshold_bytes=threshold,
                database_path=database_path,
                database_size_bytes=db_size,
                shm_path=shm_path_for_row,
                shm_size_bytes=shm_size,
            )
        )
    rows.sort(key=lambda row: row.size_bytes, reverse=True)
    return CodexLogBloatReport(
        sqlite_home=sqlite_home,
        sqlite_home_source=source,
        threshold_bytes=threshold,
        wal_files=tuple(rows),
    )


def checkpoint_codex_wal(
    wal: CodexWalFile,
    *,
    timeout_s: float = DEFAULT_CODEX_WAL_CHECKPOINT_TIMEOUT_S,
) -> CodexWalCheckpointResult:
    """Run ``PRAGMA wal_checkpoint(TRUNCATE)`` for one Codex log DB.

    Safety stance for Option B (#43): use sqlite's own locking/checkpoint API
    with a short busy timeout and a progress deadline. If another Codex process
    is writing, sqlite reports ``busy``/``locked`` and we continue with a typed
    degraded signal instead of deleting files or waiting unboundedly.
    """

    started = time.monotonic()
    db_path = wal.database_path
    if not db_path.exists():
        return CodexWalCheckpointResult(
            wal_path=wal.path,
            database_path=db_path,
            attempted=False,
            status="missing_db",
            error="database file not found beside WAL",
        )

    deadline = started + max(0.001, timeout_s)
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(
            f"file:{db_path}?mode=rw",
            uri=True,
            timeout=max(0.001, timeout_s),
        )
        conn.execute(f"PRAGMA busy_timeout={int(max(0.001, timeout_s) * 1000)}")

        def _progress() -> int:
            return 1 if time.monotonic() >= deadline else 0

        conn.set_progress_handler(_progress, 1000)
        row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        duration_ms = int((time.monotonic() - started) * 1000)
        busy: int | None = None
        log_frames: int | None = None
        checkpointed_frames: int | None = None
        if row is not None and len(row) >= 3:
            busy, log_frames, checkpointed_frames = (int(row[0]), int(row[1]), int(row[2]))
        status = "busy" if busy and busy > 0 else "checkpointed"
        return CodexWalCheckpointResult(
            wal_path=wal.path,
            database_path=db_path,
            attempted=True,
            status=status,
            duration_ms=duration_ms,
            busy=busy,
            log_frames=log_frames,
            checkpointed_frames=checkpointed_frames,
        )
    except sqlite3.OperationalError as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        message = str(exc)
        if "interrupted" in message.lower():
            status = "timeout"
        elif "locked" in message.lower() or "busy" in message.lower():
            status = "busy"
        else:
            status = "error"
        return CodexWalCheckpointResult(
            wal_path=wal.path,
            database_path=db_path,
            attempted=True,
            status=status,
            duration_ms=duration_ms,
            error=f"sqlite3.OperationalError: {message}",
        )
    except sqlite3.Error as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        return CodexWalCheckpointResult(
            wal_path=wal.path,
            database_path=db_path,
            attempted=True,
            status="error",
            duration_ms=duration_ms,
            error=f"{type(exc).__name__}: {exc}",
        )
    finally:
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass


def checkpoint_bloated_codex_wals(
    report: CodexLogBloatReport,
    *,
    env: Mapping[str, str] | None = None,
    enabled: bool | None = None,
    timeout_s: float | None = None,
) -> tuple[CodexWalCheckpointResult, ...]:
    """Checkpoint every threshold-exceeding WAL in ``report`` when enabled."""

    env_map = env if env is not None else os.environ
    should_run = (
        _truthy_env_enabled(env_map, CODEX_WAL_CHECKPOINT_ENV, default=True)
        if enabled is None
        else enabled
    )
    if not should_run:
        return tuple(
            CodexWalCheckpointResult(
                wal_path=wal.path,
                database_path=wal.database_path,
                attempted=False,
                status="disabled",
            )
            for wal in report.bloated_wal_files
        )
    budget = (
        _bounded_float_env(
            env_map,
            CODEX_WAL_CHECKPOINT_TIMEOUT_ENV,
            DEFAULT_CODEX_WAL_CHECKPOINT_TIMEOUT_S,
            min_value=MIN_CODEX_WAL_CHECKPOINT_TIMEOUT_S,
            max_value=MAX_CODEX_WAL_CHECKPOINT_TIMEOUT_S,
        )
        if timeout_s is None
        else float(timeout_s)
    )
    if not (
        MIN_CODEX_WAL_CHECKPOINT_TIMEOUT_S
        <= budget
        <= MAX_CODEX_WAL_CHECKPOINT_TIMEOUT_S
    ):
        raise ValueError(
            "timeout_s must be in "
            f"[{MIN_CODEX_WAL_CHECKPOINT_TIMEOUT_S:g}, "
            f"{MAX_CODEX_WAL_CHECKPOINT_TIMEOUT_S:g}] seconds, got {budget}"
        )

    deadline = time.monotonic() + budget
    results: list[CodexWalCheckpointResult] = []
    for wal in report.bloated_wal_files:
        remaining_s = deadline - time.monotonic()
        if remaining_s <= 0:
            results.append(
                CodexWalCheckpointResult(
                    wal_path=wal.path,
                    database_path=wal.database_path,
                    attempted=False,
                    status="timeout",
                    error=(
                        "aggregate Codex WAL checkpoint timeout exhausted "
                        f"after {budget:g}s"
                    ),
                )
            )
            continue
        results.append(checkpoint_codex_wal(wal, timeout_s=min(budget, remaining_s)))
    return tuple(results)


def format_bytes(num_bytes: int | None) -> str:
    if num_bytes is None:
        return "-"
    value = float(num_bytes)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(value) < 1024.0 or unit == "TiB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{num_bytes} B"
