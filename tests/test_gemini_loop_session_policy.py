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
