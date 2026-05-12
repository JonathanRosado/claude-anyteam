"""Regression coverage for Codex sqlite WAL-bloat mitigation (#43)."""

from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

from claude_anyteam import app_server as app_server_mod
from claude_anyteam import codex as codex_mod
from claude_anyteam import diagnose_cli
from claude_anyteam.codex_log_bloat import (
    CODEX_WAL_CHECKPOINT_ENV,
    CODEX_WAL_WARN_THRESHOLD_BYTES_ENV,
    CodexWalFile,
    checkpoint_codex_wal,
    inspect_codex_log_bloat,
)


def _make_sparse_wal(root: Path, *, size_bytes: int, name: str = "logs_2") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    wal = root / f"{name}.sqlite-wal"
    with wal.open("wb") as fh:
        fh.truncate(size_bytes)
    return wal


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


def test_pre_spawn_warning_emits_visibility_degraded_before_initialize(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Proof-of-fix unit: app_server_invoke warns before initialize starts."""

    _make_sparse_wal(tmp_path, size_bytes=200 * 1024 * 1024)
    monkeypatch.setenv("CODEX_SQLITE_HOME", str(tmp_path))
    monkeypatch.setenv(CODEX_WAL_CHECKPOINT_ENV, "0")

    captured = []

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
        result = codex_mod.app_server_invoke(
            task_prompt="noop",
            cwd=tmp_path,
            schema=None,
            settings_team="team",
            settings_agent="codex-a",
            event_sink=captured.append,
        )

    assert result.exit_code == 0
    surfaces = [event.payload.get("surface") for event in captured]
    assert surfaces[0] == "codex_sqlite_wal_bloat"
    warning = captured[0]
    assert warning.kind == "visibility_degraded"
    assert warning.severity == "warn"
    assert warning.payload["max_wal_bytes"] == 200 * 1024 * 1024
    completed_index = next(
        i for i, event in enumerate(captured) if event.kind == "app_server_initialize_completed"
    )
    assert completed_index > 0
