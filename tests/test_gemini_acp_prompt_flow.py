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
    chat_dir = home / ".gemini" / "tmp" / "x" / "chats"
    chat_dir.mkdir(parents=True)
    (chat_dir / "session-store-1.jsonl").write_text('{"sessionId":"store-1"}\n')
    result = acp.run("prompt", cwd=tmp_path, schema=acp.TASK_COMPLETE_SCHEMA, gemini_home=home, wrapper_identity=("t", "a"), model="m")
    assert result.exit_code == 0
    assert result.structured == {"files_changed": [], "summary": "done"}
    assert result.session_id == "live-1"
    assert result.tool_call_events == 1
    state = json.loads((home / ".claude-anyteam" / "state.json").read_text())
    assert state["backend"] == "acp"
    assert state["acp_session_id"] == "live-1"
    assert state["acp_storage_session_id"] == "store-1"


class AuthClient(FakeClient):
    authenticated = []
    def initialize(self): return {"protocolVersion": 1, "authMethods": [{"id": "api-key"}]}
    def authenticate(self, method_id, **kwargs):
        self.authenticated.append(method_id)
        return {}


def test_acp_run_authenticates_when_methods_advertised(tmp_path, monkeypatch):
    AuthClient.instances = []
    AuthClient.authenticated = []
    monkeypatch.setattr(acp, "GeminiAcpClient", AuthClient)
    home = tmp_path / "home"
    result = acp.run("prompt", cwd=tmp_path, gemini_home=home)
    assert result.exit_code == 0
    assert AuthClient.authenticated == ["api-key"]


class ToolResultClient(FakeClient):
    def session_prompt(self, **kwargs):
        self.notifications.extend([
            {"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": kwargs["session_id"], "update": {"sessionUpdate": "tool_call", "toolCallId": "c1", "title": "shout", "status": "in_progress"}}},
            {"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": kwargs["session_id"], "update": {"sessionUpdate": "tool_call_update", "toolCallId": "c1", "title": "shout", "status": "completed", "content": [{"type": "content", "content": {"type": "text", "text": "OK"}}]}}},
            {"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": kwargs["session_id"], "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "done"}}}},
        ])
        return {"stopReason": "end_turn"}


def test_acp_normalizes_tool_events(tmp_path, monkeypatch):
    monkeypatch.setattr(acp, "GeminiAcpClient", ToolResultClient)
    result = acp.run("prompt", cwd=tmp_path, gemini_home=tmp_path / "home")
    assert result.exit_code == 0
    assert any(ev.get("type") == "tool_use" and ev.get("tool_call_id") == "c1" for ev in result.events)
    assert any(ev.get("type") == "tool_result" and ev.get("content") == "OK" for ev in result.events)


class StopReasonClient(FakeClient):
    def session_prompt(self, **kwargs): return {"stopReason": "max_turns"}


def test_acp_non_end_turn_stop_reason_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(acp, "GeminiAcpClient", StopReasonClient)
    result = acp.run("prompt", cwd=tmp_path, gemini_home=tmp_path / "home")
    assert result.exit_code == 1
    assert result.error == "gemini ACP stopReason 'max_turns'"


class CancelledClient(FakeClient):
    def session_load(self, **kwargs): return {"sessionId": kwargs["session_id"]}
    def session_prompt(self, **kwargs): return {"stopReason": "cancelled"}


def test_acp_cancelled_stop_reason_clears_persisted_sessions(tmp_path, monkeypatch):
    home = tmp_path / "home"
    acp.invoke.write_adapter_state(home, backend="acp", acp_session_id="old", acp_storage_session_id="old-store")
    monkeypatch.setattr(acp, "GeminiAcpClient", CancelledClient)
    result = acp.run("prompt", cwd=tmp_path, gemini_home=home)
    assert result.exit_code == 1
    state = json.loads((home / ".claude-anyteam" / "state.json").read_text())
    assert state["acp_session_id"] is None
    assert state["acp_storage_session_id"] is None
