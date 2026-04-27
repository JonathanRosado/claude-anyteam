from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from claude_anyteam.backends.gemini import loop
from claude_anyteam.backends.gemini.config import GeminiSettings
from claude_anyteam.backends.gemini.loop import GeminiLoopState
from claude_anyteam.codex import CodexResult


def test_hard_cancel_failures_drop_session_policy():
    timeout = CodexResult(exit_code=124, structured=None, last_message="", events=[], error="timed out")
    cancelled = CodexResult(exit_code=1, structured=None, last_message="", events=[], error="gemini ACP stopReason 'cancelled'")
    other = CodexResult(exit_code=1, structured=None, last_message="", events=[], error="schema failed")
    assert loop._should_drop_session_after_failure(timeout)
    assert loop._should_drop_session_after_failure(cancelled)
    assert not loop._should_drop_session_after_failure(other)


def _settings(tmp_path: Path, backend: str) -> GeminiSettings:
    return GeminiSettings(
        team_name="t",
        agent_name="gemini-peer",
        cwd=tmp_path,
        poll_interval_s=0.01,
        color="cyan",
        plan_mode_required=False,
        gemini_home=tmp_path / f"gemini-{backend}",
        backend=backend,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize("backend", ["acp", "headless"])
def test_handle_prose_skips_fallback_when_model_used_send_message_tool(tmp_path: Path, monkeypatch, backend: str):
    """09 R22 / W7: Gemini matches Codex PR #12 and Kimi PR #11.

    When Gemini delivers a prose reply via the wrapper send_message tool, the
    CLI may leave last_message empty. The loop must not add a second canned
    fallback reply on top of the tool-delivered peer DM.
    """

    state = loop.GeminiLoopState(settings=_settings(tmp_path, backend))
    msg = SimpleNamespace(from_="codex-peer", text="ack", summary="prose")
    fake_result = CodexResult(
        exit_code=0,
        structured=None,
        last_message="",
        events=[],
        tool_call_events=1,
    )
    monkeypatch.setattr(loop, "_backend_run", lambda *a, **k: fake_result)

    send_prose_calls: list = []
    monkeypatch.setattr(loop.pio, "send_prose", lambda *a, **k: send_prose_calls.append((a, k)))

    loop._handle_prose(state, msg)

    assert send_prose_calls == []


@pytest.mark.parametrize("backend", ["acp", "headless"])
def test_handle_prose_skips_prose_text_after_send_message_tool(tmp_path: Path, monkeypatch, backend: str):
    """Gemini parity for the M13 guard ordering: tool delivery wins over text."""

    state = loop.GeminiLoopState(settings=_settings(tmp_path, backend))
    msg = SimpleNamespace(from_="codex-peer", text="ack", summary="prose")
    fake_result = CodexResult(
        exit_code=0,
        structured=None,
        last_message="Already sent the answer with mcp_anyteam_send_message.",
        events=[{"type": "tool_use", "tool_name": "mcp_anyteam_send_message"}],
        tool_call_events=1,
    )
    monkeypatch.setattr(loop, "_backend_run", lambda *a, **k: fake_result)

    send_prose_calls: list = []
    monkeypatch.setattr(loop.pio, "send_prose", lambda *a, **k: send_prose_calls.append((a, k)))

    loop._handle_prose(state, msg)

    assert send_prose_calls == []


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
