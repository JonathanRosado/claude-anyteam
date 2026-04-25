from __future__ import annotations

import json

from claude_anyteam.backends.gemini import acp


class FakeClient:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.notifications = []
        FakeClient.instances.append(self)

    def start(self): pass
    def close(self): pass
    def initialize(self): return {"protocolVersion": 1}
    def session_new(self, **kwargs): return {"sessionId": "live-1"}
    def set_session_mode(self, **kwargs): return {}
    def unstable_set_session_model(self, **kwargs): return {}
    def session_prompt(self, **kwargs):
        self.notifications.extend([
            {"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": kwargs["session_id"], "update": {"sessionUpdate": "tool_call", "status": "in_progress"}}},
            {"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": kwargs["session_id"], "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": '{"files_changed":[],'}}}},
            {"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": kwargs["session_id"], "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": '"summary":"done"}'}}}},
        ])
        return {"stopReason": "end_turn"}
    def drain_notifications(self): return self.notifications


def test_acp_run_structured_result_and_state(tmp_path, monkeypatch):
    FakeClient.instances = []
    monkeypatch.setattr(acp, "GeminiAcpClient", FakeClient)
    monkeypatch.setattr(acp.invoke.shutil, "which", lambda name: "/bin/" + name)
    home = tmp_path / "home"
    result = acp.run("prompt", cwd=tmp_path, schema=acp.TASK_COMPLETE_SCHEMA, gemini_home=home, wrapper_identity=("t", "a"), model="m")
    assert result.exit_code == 0
    assert result.structured == {"files_changed": [], "summary": "done"}
    assert result.session_id == "live-1"
    assert result.tool_call_events == 1
    state = json.loads((home / ".claude-anyteam" / "state.json").read_text())
    assert state["backend"] == "acp"
    assert state["acp_session_id"] == "live-1"
