from __future__ import annotations

from claude_anyteam.backends.gemini import acp, invoke


class LoadingClient:
    loaded = []
    def __init__(self, **kwargs): pass
    def start(self): pass
    def close(self): pass
    def initialize(self): return {}
    def session_load(self, **kwargs):
        self.loaded.append(kwargs["session_id"])
        return {}
    def session_new(self, **kwargs): raise AssertionError("should load")
    def set_session_mode(self, **kwargs): return {}
    def unstable_set_session_model(self, **kwargs): return {}
    def session_prompt(self, **kwargs): return {"stopReason": "end_turn"}
    def drain_notifications(self):
        return [{"method": "session/update", "params": {"sessionId": "store-1", "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "ok"}}}}]


def test_acp_run_reloads_persisted_storage_session(tmp_path, monkeypatch):
    home = tmp_path / "home"
    invoke.write_adapter_state(home, backend="acp", acp_session_id="live-old", acp_storage_session_id="store-1")
    LoadingClient.loaded = []
    monkeypatch.setattr(acp, "GeminiAcpClient", LoadingClient)
    monkeypatch.setattr(acp.invoke.shutil, "which", lambda name: "/bin/" + name)
    result = acp.run("prompt", cwd=tmp_path, gemini_home=home)
    assert result.exit_code == 0
    assert LoadingClient.loaded == ["store-1"]
    assert result.session_id == "store-1"
