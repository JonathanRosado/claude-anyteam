"""Tests for v7.3's App Server fork dispatch and thread lineage."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from claude_anyteam import app_server as app_server_mod
from claude_anyteam import codex as codex_mod
from claude_anyteam import loop as loop_mod
from claude_anyteam.app_server import AppServerClient, AppServerError
from claude_anyteam.config import Settings
from claude_anyteam.loop import LoopState


def _settings(*, model: str | None = None, effort: str | None = None) -> Settings:
    return Settings(
        team_name="t",
        agent_name="a",
        cwd=Path("/tmp").resolve(),
        poll_interval_s=0.01,
        color="cyan",
        plan_mode_required=False,
        codex_binary="codex",
        app_server=True,
        model=model,
        effort=effort,
    )


def _task(task_id: str) -> SimpleNamespace:
    return SimpleNamespace(id=task_id, subject=f"do {task_id}", description="desc")


def _ok_result(thread_id: str) -> codex_mod.CodexResult:
    return codex_mod.CodexResult(
        exit_code=0,
        structured={"files_changed": [], "summary": "done"},
        last_message='{"files_changed": [], "summary": "done"}',
        events=[],
        session_id=thread_id,
    )


class _DoneQueue:
    def __init__(self):
        self._items = [{"method": "turn/completed", "params": {"turn": {"status": "ok"}}}]

    def get(self, timeout=None):
        if self._items:
            return self._items.pop(0)
        raise RuntimeError("empty (test)")


class _FakeClient:
    created: list["_FakeClient"] = []
    next_thread_num = 1

    def __init__(self, *args, **kwargs):
        self.notifications = _DoneQueue()
        self.started_threads: list[dict] = []
        self.forked_threads: list[tuple[str, dict]] = []
        self.turns: list[dict] = []
        type(self).created.append(self)

    def start(self):
        pass

    def initialize(self):
        return {}

    def thread_start(self, **kwargs):
        self.started_threads.append(kwargs)
        thread_id = f"thread-{type(self).next_thread_num}"
        type(self).next_thread_num += 1
        return thread_id

    def is_thread_materialized(self, thread_id: str) -> bool:
        return True

    def thread_fork(self, *, thread_id: str, **kwargs):
        self.forked_threads.append((thread_id, kwargs))
        new_thread_id = f"thread-{type(self).next_thread_num}"
        type(self).next_thread_num += 1
        return new_thread_id

    def turn_start(self, **kwargs):
        self.turns.append(kwargs)
        return "turn-1"

    def turn_interrupt(self, **kwargs):
        pass

    def close(self, **kwargs):
        pass


def test_unmaterialized_parent_falls_back_to_thread_start_and_logs():
    client = MagicMock()
    client.is_thread_materialized.return_value = False
    client.thread_start.return_value = "thread-fresh"

    with patch.object(codex_mod.logger, "warn") as warn_log:
        got = codex_mod._start_or_fork_thread(
            client,
            resume_thread_id="thread-parent",
            thread_kwargs={"cwd": "/tmp", "ephemeral": False},
        )

    assert got == "thread-fresh"
    client.is_thread_materialized.assert_called_once_with("thread-parent")
    client.thread_start.assert_called_once_with(cwd="/tmp", ephemeral=False)
    client.thread_fork.assert_not_called()
    warn_log.assert_called_once_with(
        "app_server.fork_fallback_unmaterialized",
        resume_thread_id="thread-parent",
    )


def test_materialization_probe_error_is_not_silenced():
    client = MagicMock()
    client.is_thread_materialized.side_effect = AppServerError("transport timeout")

    with pytest.raises(AppServerError, match="transport timeout"):
        codex_mod._start_or_fork_thread(
            client,
            resume_thread_id="thread-parent",
            thread_kwargs={"cwd": "/tmp", "ephemeral": False},
        )

    client.thread_start.assert_not_called()
    client.thread_fork.assert_not_called()


def test_thread_not_loaded_parent_falls_back_to_thread_start_and_logs():
    client = AppServerClient(codex_binary="codex")
    client.request = MagicMock(  # type: ignore[method-assign]
        side_effect=AppServerError("thread not loaded: thread-parent")
    )
    client.thread_start = MagicMock(return_value="thread-fresh")  # type: ignore[method-assign]
    client.thread_fork = MagicMock()  # type: ignore[method-assign]

    with patch.object(codex_mod.logger, "warn") as warn_log:
        got = codex_mod._start_or_fork_thread(
            client,
            resume_thread_id="thread-parent",
            thread_kwargs={"cwd": "/tmp", "ephemeral": False},
        )

    assert got == "thread-fresh"
    client.thread_start.assert_called_once_with(cwd="/tmp", ephemeral=False)
    client.thread_fork.assert_not_called()
    warn_log.assert_called_once_with(
        "app_server.fork_fallback_unmaterialized",
        resume_thread_id="thread-parent",
    )


def test_first_task_uses_thread_start():
    client = MagicMock()
    client.thread_start.return_value = "thread-1"

    got = codex_mod._start_or_fork_thread(
        client,
        resume_thread_id=None,
        thread_kwargs={"cwd": "/tmp", "ephemeral": False},
    )

    assert got == "thread-1"
    client.thread_start.assert_called_once_with(cwd="/tmp", ephemeral=False)
    client.thread_fork.assert_not_called()


def test_second_task_uses_thread_fork():
    _FakeClient.created = []
    _FakeClient.next_thread_num = 1

    with patch.object(app_server_mod, "AppServerClient", _FakeClient):
        first = codex_mod.app_server_invoke(
            task_prompt="noop",
            cwd=Path("/tmp"),
            schema=None,
            settings_team="t",
            settings_agent="a",
        )
        second = codex_mod.app_server_invoke(
            task_prompt="noop",
            cwd=Path("/tmp"),
            schema=None,
            settings_team="t",
            settings_agent="a",
            resume_thread_id=first.session_id,
        )

    assert first.session_id == "thread-1"
    assert second.session_id == "thread-2"
    assert _FakeClient.created[0].started_threads != []
    assert _FakeClient.created[0].forked_threads == []
    assert _FakeClient.created[1].started_threads == []
    assert _FakeClient.created[1].forked_threads[0][0] == "thread-1"


def test_failed_task_does_not_update_app_server_last_thread_id():
    state = LoopState(settings=_settings(), app_server_last_thread_id="thread-old")
    failed = codex_mod.CodexResult(
        exit_code=1,
        structured=None,
        last_message="",
        events=[],
        error="boom",
        session_id="thread-bad",
    )

    with (
        patch.object(loop_mod, "_invoke_codex_for_task", return_value=failed),
        patch.object(loop_mod, "_mark_blocked") as mark_blocked,
    ):
        loop_mod._execute_task(state, _task("1"))

    assert state.app_server_last_thread_id == "thread-old"
    mark_blocked.assert_called_once()


def test_fork_carries_session_id_forward():
    state = LoopState(settings=_settings())
    invoke_calls: list[dict] = []

    def fake_invoke(**kwargs):
        invoke_calls.append(kwargs)
        return _ok_result(f"thread-{len(invoke_calls)}")

    with (
        patch.object(codex_mod, "app_server_invoke", side_effect=fake_invoke),
        patch.object(loop_mod.pio, "update_task"),
        patch.object(loop_mod.pio, "send_task_complete"),
    ):
        loop_mod._execute_task(state, _task("1"))
        loop_mod._execute_task(state, _task("2"))

    assert state.app_server_last_thread_id == "thread-2"
    assert invoke_calls[0]["resume_thread_id"] is None
    assert invoke_calls[1]["resume_thread_id"] == "thread-1"


def test_fork_when_ephemeral_raises_clear_error():
    client = AppServerClient(codex_binary="codex")
    client.request = MagicMock(
        side_effect=[
            {"thread": {"id": "thread-ephemeral"}},
            AppServerError("no rollout found for thread id thread-ephemeral"),
        ]
    )

    thread_id = client.thread_start(cwd="/tmp", ephemeral=True)
    with pytest.raises(AppServerError, match="ephemeral=True|thread/start|materialized"):
        client.thread_fork(thread_id=thread_id)


def test_thread_fork_method_hits_app_server_with_correct_params():
    client = AppServerClient(codex_binary="codex")
    mock_request = MagicMock(return_value={"thread": {"id": "thread-2"}})
    client.request = mock_request  # type: ignore[method-assign]

    thread_id = client.thread_fork(
        thread_id="thread-1",
        cwd="/tmp/work",
        base_instructions="base",
        developer_instructions="dev",
        model="gpt-5.4",
    )

    assert thread_id == "thread-2"
    method, params = mock_request.call_args[0]
    assert method == "thread/fork"
    assert params == {
        "threadId": "thread-1",
        "cwd": "/tmp/work",
        "baseInstructions": "base",
        "developerInstructions": "dev",
        "sandbox": "danger-full-access",
        "approvalPolicy": "never",
        "ephemeral": False,
        "model": "gpt-5.4",
    }


def test_model_effort_carry_through_fork_path():
    _FakeClient.created = []
    _FakeClient.next_thread_num = 1

    with patch.object(app_server_mod, "AppServerClient", _FakeClient):
        result = codex_mod.app_server_invoke(
            task_prompt="noop",
            cwd=Path("/tmp"),
            schema=None,
            settings_team="t",
            settings_agent="a",
            resume_thread_id="thread-parent",
            model="gpt-5.4",
            effort="high",
        )

    assert result.session_id == "thread-1"
    client = _FakeClient.created[0]
    parent_thread_id, fork_kwargs = client.forked_threads[0]
    assert parent_thread_id == "thread-parent"
    assert fork_kwargs["base_instructions"] == (
        "You are a, a Codex teammate on the t team. Execute the task below."
    )
    assert fork_kwargs["developer_instructions"] == "noop"
    assert fork_kwargs["sandbox"] == "danger-full-access"
    assert fork_kwargs["approval_policy"] == "never"
    assert fork_kwargs["ephemeral"] is False
    assert "config" in fork_kwargs
    assert fork_kwargs["model"] == "gpt-5.4"
    assert client.turns == [
        {
            "thread_id": "thread-1",
            "text": "noop",
            "output_schema": None,
            "model": "gpt-5.4",
            "effort": "high",
        }
    ]
