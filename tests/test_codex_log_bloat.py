"""Regression coverage for Codex sqlite WAL-bloat mitigation (#43)."""

from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from claude_anyteam import app_server as app_server_mod
from claude_anyteam import codex as codex_mod
from claude_anyteam import codex_log_bloat as bloat_mod
from claude_anyteam import diagnose_cli
from claude_anyteam.codex_log_bloat import (
    CODEX_WAL_CHECKPOINT_ENV,
    CODEX_WAL_CHECKPOINT_TIMEOUT_ENV,
    CODEX_WAL_WARN_THRESHOLD_BYTES_ENV,
    MAX_CODEX_WAL_CHECKPOINT_TIMEOUT_S,
    MAX_CODEX_WAL_WARN_THRESHOLD_BYTES,
    CodexWalFile,
    CodexLogBloatReport,
    CodexWalCheckpointResult,
    checkpoint_bloated_codex_wals,
    checkpoint_codex_wal,
    inspect_codex_log_bloat,
)


def _make_sparse_wal(root: Path, *, size_bytes: int, name: str = "logs_2") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    wal = root / f"{name}.sqlite-wal"
    with wal.open("wb") as fh:
        fh.truncate(size_bytes)
    return wal


def _invoke_app_server_with_bloat(tmp_path: Path, captured: list) -> codex_mod.CodexResult:
    class _Queue:
        def __init__(self) -> None:
            self._items = [
                {"method": "turn/completed", "params": {"turn": {"status": "ok"}}}
            ]

        def get(self, timeout=None):
            if self._items:
                return self._items.pop(0)
            raise RuntimeError("empty (test)")

    class _FakeClient:
        notifications = _Queue()

        def __init__(self, *args, pre_start_hook=None, **kwargs) -> None:
            self._pre_start_hook = pre_start_hook
            self.notifications = _Queue()

        def start(self) -> None:
            if self._pre_start_hook is not None:
                self._pre_start_hook()

        def initialize(self, **_kwargs):
            return {}

        def thread_start(self, **kwargs):
            return "thread-id"

        def turn_start(self, **kwargs):
            return "turn-id"

        def drain_notifications(self):
            return []

        def turn_interrupt(self, **kwargs):
            pass

        def close(self, **kwargs):
            pass

    with patch.object(app_server_mod, "AppServerClient", _FakeClient):
        return codex_mod.app_server_invoke(
            task_prompt="noop",
            cwd=tmp_path,
            schema=None,
            settings_team="team",
            settings_agent="codex-a",
            event_sink=captured.append,
        )


def _bloated_report(tmp_path: Path, *, count: int = 1) -> CodexLogBloatReport:
    rows: list[CodexWalFile] = []
    for index in range(count):
        wal_path = _make_sparse_wal(
            tmp_path,
            size_bytes=2 * 1024 * 1024,
            name=f"logs_{index}",
        )
        database_path = Path(str(wal_path)[:-4])
        database_path.write_bytes(b"sqlite placeholder")
        rows.append(
            CodexWalFile(
                path=wal_path,
                size_bytes=wal_path.stat().st_size,
                threshold_bytes=1,
                database_path=database_path,
            )
        )
    return CodexLogBloatReport(
        sqlite_home=tmp_path,
        sqlite_home_source="explicit",
        threshold_bytes=1,
        wal_files=tuple(rows),
    )


def test_wal_size_check_flags_sparse_200mb_repro(tmp_path: Path) -> None:
    """Proof-of-repro unit: a fake 200 MiB Codex WAL is detected as bloated."""

    wal = _make_sparse_wal(tmp_path, size_bytes=200 * 1024 * 1024)
    (tmp_path / "logs_2.sqlite").write_bytes(b"not a real sqlite db for stat-only repro")

    report = inspect_codex_log_bloat(sqlite_home=tmp_path)

    assert report.is_bloated is True
    assert report.max_wal_bytes == 200 * 1024 * 1024
    assert report.bloated_wal_files[0].path == wal
    assert report.bloated_wal_files[0].database_size_bytes == len(
        b"not a real sqlite db for stat-only repro"
    )


def test_threshold_env_controls_bloat_boundary(tmp_path: Path, monkeypatch) -> None:
    _make_sparse_wal(tmp_path, size_bytes=2 * 1024 * 1024)
    monkeypatch.setenv(CODEX_WAL_WARN_THRESHOLD_BYTES_ENV, str(3 * 1024 * 1024))

    below = inspect_codex_log_bloat(sqlite_home=tmp_path)
    assert below.is_bloated is False

    monkeypatch.setenv(CODEX_WAL_WARN_THRESHOLD_BYTES_ENV, str(1024 * 1024))
    above = inspect_codex_log_bloat(sqlite_home=tmp_path)
    assert above.is_bloated is True


def test_threshold_env_rejects_out_of_range_values(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(
        CODEX_WAL_WARN_THRESHOLD_BYTES_ENV,
        str(MAX_CODEX_WAL_WARN_THRESHOLD_BYTES + 1),
    )

    with pytest.raises(ValueError, match=CODEX_WAL_WARN_THRESHOLD_BYTES_ENV):
        inspect_codex_log_bloat(sqlite_home=tmp_path)


def test_checkpoint_timeout_env_rejects_out_of_range_values(
    tmp_path: Path,
    monkeypatch,
) -> None:
    report = _bloated_report(tmp_path)
    monkeypatch.setenv(
        CODEX_WAL_CHECKPOINT_TIMEOUT_ENV,
        str(MAX_CODEX_WAL_CHECKPOINT_TIMEOUT_S + 1),
    )

    with pytest.raises(ValueError, match=CODEX_WAL_CHECKPOINT_TIMEOUT_ENV):
        checkpoint_bloated_codex_wals(report)


def test_diagnose_codex_log_bloat_subcommand_flags_large_wal(tmp_path: Path) -> None:
    _make_sparse_wal(tmp_path, size_bytes=200 * 1024 * 1024)
    out = io.StringIO()
    err = io.StringIO()

    rc = diagnose_cli.main(
        ["--codex-log-bloat", "--codex-sqlite-home", str(tmp_path), "--json"],
        stdout=out,
        stderr=err,
    )

    assert rc == 1
    assert err.getvalue() == ""
    payload = json.loads(out.getvalue())
    assert payload["status"] == "degraded"
    details = payload["codex_log_bloat"]
    assert details["bloated_wal_file_count"] == 1
    assert details["max_wal_bytes"] == 200 * 1024 * 1024
    assert "codex app-server" in payload["impact"]


def test_diagnose_codex_log_bloat_subcommand_reports_ok_without_wal(tmp_path: Path) -> None:
    out = io.StringIO()

    rc = diagnose_cli.main(
        ["--codex-log-bloat", "--codex-sqlite-home", str(tmp_path)],
        stdout=out,
        stderr=io.StringIO(),
    )

    assert rc == 0
    body = out.getvalue()
    assert "status=ok" in body
    assert "no logs_*.sqlite-wal files found" in body


def test_checkpoint_path_truncates_real_sqlite_wal(tmp_path: Path) -> None:
    """Option B proof: use sqlite's API to drain a real WAL safely."""

    db = tmp_path / "logs_2.sqlite"
    conn = sqlite3.connect(db)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.execute("CREATE TABLE events(id INTEGER PRIMARY KEY, body TEXT)")
        conn.executemany(
            "INSERT INTO events(body) VALUES (?)",
            [("x" * 1024,) for _ in range(256)],
        )
        conn.commit()
        wal_path = Path(str(db) + "-wal")
        assert wal_path.exists(), "test setup failed to create a WAL file"
        assert wal_path.stat().st_size > 0

        result = checkpoint_codex_wal(
            CodexWalFile(
                path=wal_path,
                size_bytes=wal_path.stat().st_size,
                threshold_bytes=1,
                database_path=db,
            ),
            timeout_s=5,
        )
    finally:
        conn.close()

    assert result.attempted is True
    assert result.status == "checkpointed"
    assert result.error is None
    assert (not wal_path.exists()) or wal_path.stat().st_size == 0


def test_checkpoint_path_reports_missing_db(tmp_path: Path) -> None:
    wal_path = _make_sparse_wal(tmp_path, size_bytes=1024)

    result = checkpoint_codex_wal(
        CodexWalFile(
            path=wal_path,
            size_bytes=wal_path.stat().st_size,
            threshold_bytes=1,
            database_path=tmp_path / "logs_2.sqlite",
        )
    )

    assert result.attempted is False
    assert result.status == "missing_db"
    assert "database file not found" in (result.error or "")


def test_checkpoint_path_reports_busy_from_sqlite_result(
    tmp_path: Path,
    monkeypatch,
) -> None:
    report = _bloated_report(tmp_path)

    class _Cursor:
        def fetchone(self):
            return (1, 12, 4)

    class _Connection:
        def execute(self, sql):
            if sql == "PRAGMA wal_checkpoint(TRUNCATE)":
                return _Cursor()
            return _Cursor()

        def set_progress_handler(self, callback, n):
            pass

        def close(self):
            pass

    monkeypatch.setattr(bloat_mod.sqlite3, "connect", lambda *a, **kw: _Connection())

    result = checkpoint_codex_wal(report.bloated_wal_files[0], timeout_s=1)

    assert result.attempted is True
    assert result.status == "busy"
    assert result.busy == 1
    assert result.log_frames == 12
    assert result.checkpointed_frames == 4


def test_checkpoint_path_reports_timeout_from_progress_interrupt(
    tmp_path: Path,
    monkeypatch,
) -> None:
    report = _bloated_report(tmp_path)

    class _Connection:
        def execute(self, sql):
            if sql == "PRAGMA wal_checkpoint(TRUNCATE)":
                raise sqlite3.OperationalError("interrupted")
            return self

        def set_progress_handler(self, callback, n):
            pass

        def close(self):
            pass

    monkeypatch.setattr(bloat_mod.sqlite3, "connect", lambda *a, **kw: _Connection())

    result = checkpoint_codex_wal(report.bloated_wal_files[0], timeout_s=1)

    assert result.attempted is True
    assert result.status == "timeout"
    assert "interrupted" in (result.error or "")


def test_checkpoint_path_reports_error_from_sqlite_operational_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    report = _bloated_report(tmp_path)

    class _Connection:
        def execute(self, sql):
            if sql == "PRAGMA wal_checkpoint(TRUNCATE)":
                raise sqlite3.OperationalError("disk I/O error")
            return self

        def set_progress_handler(self, callback, n):
            pass

        def close(self):
            pass

    monkeypatch.setattr(bloat_mod.sqlite3, "connect", lambda *a, **kw: _Connection())

    result = checkpoint_codex_wal(report.bloated_wal_files[0], timeout_s=1)

    assert result.attempted is True
    assert result.status == "error"
    assert "disk I/O error" in (result.error or "")


def test_checkpoint_bloated_codex_wals_caps_aggregate_timeout(
    tmp_path: Path,
    monkeypatch,
) -> None:
    report = _bloated_report(tmp_path, count=3)
    now = 100.0
    calls: list[float] = []

    def fake_monotonic() -> float:
        return now

    def fake_checkpoint(wal: CodexWalFile, *, timeout_s: float):
        nonlocal now
        calls.append(timeout_s)
        now += timeout_s + 0.001
        return CodexWalCheckpointResult(
            wal_path=wal.path,
            database_path=wal.database_path,
            attempted=True,
            status="checkpointed",
        )

    monkeypatch.setattr(bloat_mod.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(bloat_mod, "checkpoint_codex_wal", fake_checkpoint)

    results = checkpoint_bloated_codex_wals(
        report,
        enabled=True,
        timeout_s=0.01,
    )

    assert calls == [pytest.approx(0.01)]
    assert [row.status for row in results] == ["checkpointed", "timeout", "timeout"]
    assert [row.attempted for row in results] == [True, False, False]


def test_pre_spawn_warning_emits_visibility_degraded_before_initialize(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Proof-of-fix unit: app_server_invoke warns before initialize starts."""

    _make_sparse_wal(tmp_path, size_bytes=200 * 1024 * 1024)
    monkeypatch.setenv("CODEX_SQLITE_HOME", str(tmp_path))
    monkeypatch.setenv(CODEX_WAL_CHECKPOINT_ENV, "0")

    captured = []

    result = _invoke_app_server_with_bloat(tmp_path, captured)

    assert result.exit_code == 0
    surfaces = [event.payload.get("surface") for event in captured]
    assert surfaces[0] == "codex_sqlite_wal_bloat"
    warning = captured[0]
    assert warning.kind == "visibility_degraded"
    assert warning.severity == "warn"
    assert warning.payload["max_wal_bytes"] == 200 * 1024 * 1024
    assert warning.payload["pressure_source"] == "sqlite_wal_bloat"
    assert warning.payload["remediation"] == "bounded_checkpoint_before_spawn"
    assert "diagnose --codex-log-bloat" in warning.payload["hint"]
    completed_index = next(
        i for i, event in enumerate(captured) if event.kind == "app_server_initialize_completed"
    )
    assert completed_index > 0


def test_pre_spawn_checkpoint_success_uses_visibility_degraded_info(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _make_sparse_wal(tmp_path, size_bytes=200 * 1024 * 1024)
    monkeypatch.setenv("CODEX_SQLITE_HOME", str(tmp_path))

    def fake_checkpoint(report, *, env):
        wal = report.bloated_wal_files[0]
        return (
            CodexWalCheckpointResult(
                wal_path=wal.path,
                database_path=wal.database_path,
                attempted=True,
                status="checkpointed",
            ),
        )

    monkeypatch.setattr(codex_mod, "checkpoint_bloated_codex_wals", fake_checkpoint)
    captured = []

    result = _invoke_app_server_with_bloat(tmp_path, captured)

    assert result.exit_code == 0
    checkpoint = next(
        event
        for event in captured
        if event.payload.get("surface") == "codex_sqlite_wal_checkpoint"
    )
    assert checkpoint.kind == "visibility_degraded"
    assert checkpoint.severity == "info"
    assert checkpoint.visibility.mailbox is False
    assert checkpoint.payload["pressure_source"] == "sqlite_wal_bloat"
    assert checkpoint.payload["results"][0]["status"] == "checkpointed"


@pytest.mark.parametrize(
    ("status", "attempted"),
    [
        ("busy", True),
        ("timeout", True),
        ("error", True),
        ("missing_db", False),
    ],
)
def test_pre_spawn_checkpoint_failures_are_mailbox_actionable(
    tmp_path: Path,
    monkeypatch,
    status: str,
    attempted: bool,
) -> None:
    _make_sparse_wal(tmp_path, size_bytes=200 * 1024 * 1024)
    monkeypatch.setenv("CODEX_SQLITE_HOME", str(tmp_path))

    def fake_checkpoint(report, *, env):
        wal = report.bloated_wal_files[0]
        return (
            CodexWalCheckpointResult(
                wal_path=wal.path,
                database_path=wal.database_path,
                attempted=attempted,
                status=status,
                error=f"simulated {status}",
            ),
        )

    monkeypatch.setattr(codex_mod, "checkpoint_bloated_codex_wals", fake_checkpoint)
    captured = []

    result = _invoke_app_server_with_bloat(tmp_path, captured)

    assert result.exit_code == 0
    checkpoint = next(
        event
        for event in captured
        if event.payload.get("surface") == "codex_sqlite_wal_checkpoint"
    )
    assert checkpoint.kind == "visibility_degraded"
    assert checkpoint.severity == "warn"
    assert checkpoint.visibility.mailbox is True
    assert checkpoint.payload["pressure_source"] == "sqlite_wal_bloat"
    assert checkpoint.payload["results"][0]["status"] == status
