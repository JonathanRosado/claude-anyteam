from __future__ import annotations

import json

from claude_anyteam.backends.gemini import acp, invoke
from claude_anyteam.backends.gemini.acp_client import GeminiAcpError


class RecoveringClient:
    loads = []
    news = 0
    def __init__(self, **kwargs): pass
    def start(self): pass
    def close(self): pass
    def initialize(self): return {}
    def session_load(self, **kwargs):
        self.loads.append(kwargs["session_id"])
        raise GeminiAcpError("Invalid session identifier")
    def session_new(self, **kwargs):
        self.news += 1
        return {"sessionId": "live-new"}
    def set_session_mode(self, **kwargs): return {}
    def unstable_set_session_model(self, **kwargs): return {}
    def session_prompt(self, **kwargs): return {"stopReason": "end_turn"}
    def drain_notifications(self): return []


def test_load_failure_creates_and_persists_new_session(tmp_path, monkeypatch):
    home = tmp_path / "home"
    invoke.write_adapter_state(home, backend="acp", acp_session_id="bad-live", acp_storage_session_id="bad-store")
    RecoveringClient.loads = []
    monkeypatch.setattr(acp, "GeminiAcpClient", RecoveringClient)
    monkeypatch.setattr(acp.invoke.shutil, "which", lambda name: "/bin/" + name)
    result = acp.run("prompt", cwd=tmp_path, gemini_home=home)
    assert RecoveringClient.loads == ["bad-store", "bad-live"]
    assert result.session_id == "live-new"
    state = json.loads((home / ".claude-anyteam" / "state.json").read_text())
    assert state["acp_session_id"] == "live-new"
