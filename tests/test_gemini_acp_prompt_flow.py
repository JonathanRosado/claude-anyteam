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

class CloseTrackingClient(FakeClient):
    close_count = 0
    def close(self):
        type(self).close_count += 1


def test_acp_run_closes_client_on_success(tmp_path, monkeypatch):
    CloseTrackingClient.instances = []
    CloseTrackingClient.close_count = 0
    monkeypatch.setattr(acp, "GeminiAcpClient", CloseTrackingClient)
    result = acp.run("prompt", cwd=tmp_path, gemini_home=tmp_path / "home")
    assert result.exit_code == 0
    assert CloseTrackingClient.close_count == 1


class TimeoutAfterSessionClient(CloseTrackingClient):
    cancels = []
    def session_prompt(self, **kwargs):
        raise acp.GeminiAcpTimeoutError("slow")
    def session_cancel(self, **kwargs):
        type(self).cancels.append(kwargs["session_id"])


def test_acp_run_cancels_then_closes_client_on_timeout(tmp_path, monkeypatch):
    TimeoutAfterSessionClient.instances = []
    TimeoutAfterSessionClient.close_count = 0
    TimeoutAfterSessionClient.cancels = []
    monkeypatch.setattr(acp, "GeminiAcpClient", TimeoutAfterSessionClient)
    result = acp.run("prompt", cwd=tmp_path, gemini_home=tmp_path / "home", timeout_s=1)
    assert result.exit_code == 124
    assert TimeoutAfterSessionClient.cancels == ["live-1"]
    assert TimeoutAfterSessionClient.close_count == 1


class JsonRpcErrorClient(CloseTrackingClient):
    def session_prompt(self, **kwargs):
        raise acp.GeminiAcpError("JSON-RPC error -32603: boom")


def test_acp_run_closes_client_on_jsonrpc_error(tmp_path, monkeypatch):
    JsonRpcErrorClient.instances = []
    JsonRpcErrorClient.close_count = 0
    monkeypatch.setattr(acp, "GeminiAcpClient", JsonRpcErrorClient)
    result = acp.run("prompt", cwd=tmp_path, gemini_home=tmp_path / "home")
    assert result.exit_code == 1
    assert "JSON-RPC error" in (result.error or "")
    assert JsonRpcErrorClient.close_count == 1


class InvalidSchemaClient(CloseTrackingClient):
    def session_prompt(self, **kwargs):
        self.notifications.append({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": kwargs["session_id"], "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": '{"files_changed": []}'}}}})
        return {"stopReason": "end_turn"}


def test_acp_run_closes_client_on_schema_validation_failure(tmp_path, monkeypatch):
    InvalidSchemaClient.instances = []
    InvalidSchemaClient.close_count = 0
    monkeypatch.setattr(acp, "GeminiAcpClient", InvalidSchemaClient)
    result = acp.run("prompt", cwd=tmp_path, schema=acp.TASK_COMPLETE_SCHEMA, gemini_home=tmp_path / "home")
    assert result.exit_code == 1
    assert "schema validation" in (result.error or "")
    assert InvalidSchemaClient.close_count == 1

class ModeRecordingClient(FakeClient):
    modes = []
    init_kwargs = []
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        type(self).init_kwargs.append(kwargs)
    def set_session_mode(self, **kwargs):
        type(self).modes.append(kwargs)
        return {}


def test_acp_run_trust_modes_map_to_acp_session_modes(tmp_path, monkeypatch):
    monkeypatch.setattr(acp, "GeminiAcpClient", ModeRecordingClient)
    for trust_mode, acp_mode in [("trusted", "yolo"), ("default", "default"), ("plan", "plan")]:
        ModeRecordingClient.instances = []
        ModeRecordingClient.modes = []
        ModeRecordingClient.init_kwargs = []
        result = acp.run("prompt", cwd=tmp_path, gemini_home=tmp_path / f"home-{trust_mode}", trust_mode=trust_mode)
        assert result.exit_code == 0
        assert ModeRecordingClient.init_kwargs[0]["trust_mode"] == trust_mode
        assert ModeRecordingClient.modes == [{"session_id": "live-1", "mode_id": acp_mode}]


class PermissionBlockedClient(FakeClient):
    def set_session_mode(self, **kwargs): return {}
    def session_prompt(self, **kwargs):
        self.permission_blocked = {"trust_mode": "default", "label": "Write file", "params": {"toolCall": {"title": "Write file"}}}
        raise acp.GeminiAcpError("permission denied")


def test_acp_run_returns_permission_blocked_result(tmp_path, monkeypatch):
    monkeypatch.setattr(acp, "GeminiAcpClient", PermissionBlockedClient)
    result = acp.run("prompt", cwd=tmp_path, gemini_home=tmp_path / "home", trust_mode="default")
    assert result.exit_code == 1
    assert result.error == "Gemini requested permission for Write file; trust_mode=default; rerun with CLAUDE_ANYTEAM_GEMINI_TRUST=trusted to allow."
    assert any(ev.get("type") == "permission_blocked" for ev in result.events)
