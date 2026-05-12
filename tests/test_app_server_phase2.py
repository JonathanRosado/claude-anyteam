"""Regression tests for #40 Phase 2 Codex App Server cold-start mitigation."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import patch

from claude_anyteam import app_server as app_server_mod
from claude_anyteam import codex as codex_mod
from claude_anyteam.app_server import (
    APP_SERVER_START_GATE_COOLDOWN_ENV,
    APP_SERVER_START_GATE_JITTER_ENV,
    APP_SERVER_START_GATE_LOCK_PATH_ENV,
    app_server_start_gate,
)


def test_app_server_start_gate_serializes_threads(tmp_path: Path) -> None:
    lock_path = tmp_path / "codex-app-server-start.lock"
    env = {
        APP_SERVER_START_GATE_LOCK_PATH_ENV: str(lock_path),
        APP_SERVER_START_GATE_JITTER_ENV: "0",
        APP_SERVER_START_GATE_COOLDOWN_ENV: "0",
    }
    active = 0
    max_active = 0
    guard = threading.Lock()
    barrier = threading.Barrier(3)

    def worker() -> None:
        nonlocal active, max_active
        barrier.wait()
        with app_server_start_gate(env=env):
            with guard:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.05)
            with guard:
                active -= 1

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=5)

    assert max_active == 1


def test_app_server_initialize_timeout_retries_once(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(codex_mod.APP_SERVER_INITIALIZE_RETRY_BACKOFF_ENV, "0")
    monkeypatch.setenv(codex_mod.APP_SERVER_INITIALIZE_RETRIES_ENV, "1")

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

    class _RetryClient:
        starts = 0
        closes = 0
        initializes = 0

        def __init__(self, *args, **kwargs) -> None:
            self.notifications = _Queue()

        def start(self) -> None:
            type(self).starts += 1

        def initialize(self, **_kwargs):
            type(self).initializes += 1
            if type(self).initializes == 1:
                raise app_server_mod.AppServerError(
                    "JSON-RPC stdio process did not respond to initialize within 0.01s"
                )
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
            type(self).closes += 1

    with patch.object(app_server_mod, "AppServerClient", _RetryClient):
        result = codex_mod.app_server_invoke(
            task_prompt="noop",
            cwd=tmp_path,
            schema=None,
            settings_team="team",
            settings_agent="codex-a",
            event_sink=captured.append,
        )

    assert result.exit_code == 0
    assert _RetryClient.starts == 2
    assert _RetryClient.initializes == 2
    assert _RetryClient.closes >= 2  # retry cleanup + final close
    retry_events = [
        event
        for event in captured
        if event.payload.get("surface") == "app_server_initialize_retry"
    ]
    assert len(retry_events) == 1
    assert retry_events[0].kind == "turn_warning"
    assert retry_events[0].payload["attempt"] == 1
