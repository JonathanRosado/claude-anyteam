from __future__ import annotations

from claude_anyteam.backends.gemini.acp_client import GeminiAcpClient


def test_cancel_is_notification_and_reprompt_uses_prompt_method(monkeypatch):
    notes = []
    calls = []
    monkeypatch.setattr(GeminiAcpClient, "notify", lambda self, method, params=None: notes.append((method, params)))
    monkeypatch.setattr(GeminiAcpClient, "request", lambda self, method, params=None, timeout=600.0: calls.append((method, params)) or {"stopReason": "end_turn"})
    client = GeminiAcpClient()
    client.session_cancel(session_id="s1")
    client.session_prompt(session_id="s1", prompt="Do not complete the cancelled request; answer RECOVERED", message_id="m2")
    assert notes == [("session/cancel", {"sessionId": "s1"})]
    assert calls[0][0] == "session/prompt"
    assert calls[0][1]["sessionId"] == "s1"
