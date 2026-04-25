from __future__ import annotations

from claude_anyteam.backends.gemini import loop
from claude_anyteam.codex import CodexResult


def test_hard_cancel_failures_drop_session_policy():
    timeout = CodexResult(exit_code=124, structured=None, last_message="", events=[], error="timed out")
    cancelled = CodexResult(exit_code=1, structured=None, last_message="", events=[], error="gemini ACP stopReason 'cancelled'")
    other = CodexResult(exit_code=1, structured=None, last_message="", events=[], error="schema failed")
    assert loop._should_drop_session_after_failure(timeout)
    assert loop._should_drop_session_after_failure(cancelled)
    assert not loop._should_drop_session_after_failure(other)

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from claude_anyteam.backends.gemini.config import GeminiSettings
from claude_anyteam.backends.gemini.loop import GeminiLoopState


def test_loop_surfaces_permission_block_with_details():
    settings = GeminiSettings(
        team_name="t",
        agent_name="a",
        cwd=Path("/tmp").resolve(),
        poll_interval_s=0.01,
        color="cyan",
        plan_mode_required=False,
        backend="acp",
        trust_mode="default",
    )
    state = GeminiLoopState(settings=settings, gemini_session_id="old")
    task = SimpleNamespace(id="7", subject="s", description="d", owner="a", status="pending", blocked_by=[])
    blocked = []
    result = CodexResult(
        exit_code=1,
        structured=None,
        last_message="",
        events=[{"type": "permission_blocked", "label": "Write file", "trust_mode": "default"}],
        error="Gemini requested permission for Write file; trust_mode=default; rerun with CLAUDE_ANYTEAM_GEMINI_TRUST=trusted to allow.",
        session_id="new",
    )
    with (
        patch.object(loop, "_backend_run", return_value=result) as run_mock,
        patch.object(loop, "_mark_blocked", side_effect=lambda _s, t, reason: blocked.append((t.id, reason))),
    ):
        loop._execute_task(state, task)
    assert run_mock.call_count == 1
    assert blocked == [("7", result.error)]
    assert state.gemini_session_id is None


def test_backend_run_passes_task_id_to_acp(monkeypatch):
    settings = GeminiSettings(
        team_name="t",
        agent_name="a",
        cwd=Path("/tmp").resolve(),
        poll_interval_s=0.01,
        color="cyan",
        plan_mode_required=False,
        backend="acp",
        trust_mode="default",
    )
    state = GeminiLoopState(settings=settings)
    calls = []
    monkeypatch.setattr(loop.acp_invoke, "run", lambda prompt, **kwargs: calls.append((prompt, kwargs)) or CodexResult(exit_code=0, structured=None, last_message="", events=[]))

    loop._backend_run(state, "prompt", task_id="7")

    assert calls[0][1]["task_id"] == "7"
    assert calls[0][1]["trust_mode"] == "default"
