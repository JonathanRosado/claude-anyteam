from __future__ import annotations

from types import SimpleNamespace

import pytest

from claude_anyteam.codex import CodexResult
from claude_anyteam.protocol_io import should_skip_prose_fallback


def _result(**overrides):
    data = {
        "exit_code": 0,
        "structured": None,
        "last_message": "",
        "events": [],
        "error": None,
        "tool_call_events": 1,
    }
    data.update(overrides)
    return CodexResult(**data)


@pytest.mark.parametrize(
    "result",
    [
        _result(),
        _result(session_id="gemini-session-1", tool_call_events=2),
        SimpleNamespace(
            exit_code=0,
            structured=None,
            last_message="",
            events=[],
            error=None,
            tool_call_events=1,
            session_id=None,
        ),
    ],
    ids=["codex-result", "gemini-result", "kimi-duck-type-result"],
)
def test_should_skip_prose_fallback_for_tool_delivered_backend_results(result):
    assert should_skip_prose_fallback(result) is True


@pytest.mark.parametrize(
    "events",
    [
        [{"type": "mcp_tool_call", "name": "send_message"}],
        [{"params": {"item": {"type": "mcp_tool_call", "name": "send_message"}}}],
        [{"role": "assistant", "tool_calls": [{"function": {"name": "send_message"}}]}],
        [{"type": "tool_use", "tool_name": "mcp_anyteam_send_message"}],
    ],
    ids=["codex-exec", "codex-app-server", "kimi", "gemini"],
)
def test_should_skip_prose_fallback_detects_send_message_event_shapes(events):
    assert should_skip_prose_fallback(_result(events=events)) is True


def test_should_not_skip_prose_fallback_for_non_send_message_tools_when_events_available():
    result = _result(
        events=[
            {"role": "assistant", "tool_calls": [{"function": {"name": "ReadFile"}}]},
            {"type": "message", "role": "assistant", "content": "Here is the answer."},
        ],
        tool_call_events=1,
        last_message="Here is the answer.",
    )

    assert should_skip_prose_fallback(result) is False


@pytest.mark.parametrize(
    "result",
    [
        None,
        _result(exit_code=1, error="failed"),
        _result(tool_call_events=0),
        SimpleNamespace(exit_code=0),
    ],
    ids=["no-result", "nonzero-exit", "no-tool-calls", "missing-tool-count"],
)
def test_should_not_skip_prose_fallback_without_successful_tool_delivery(result):
    assert should_skip_prose_fallback(result) is False
