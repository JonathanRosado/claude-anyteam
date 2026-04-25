from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from claude_anyteam.backends.gemini import acp_client
from claude_anyteam.backends.gemini.acp_client import GeminiAcpClient
from claude_anyteam.messages import PermissionResponseIn


def test_method_wrappers_send_empirical_method_names(monkeypatch):
    calls = []

    def fake_request(self, method, params=None, *, timeout=600.0):
        calls.append((method, params, timeout))
        if method in {"session/new", "session/load"}:
            return {"sessionId": "s1"}
        return {"protocolVersion": 1} if method == "initialize" else {"ok": True}

    notes = []
    monkeypatch.setattr(GeminiAcpClient, "request", fake_request)
    monkeypatch.setattr(GeminiAcpClient, "notify", lambda self, method, params=None: notes.append((method, params)))
    c = GeminiAcpClient(gemini_binary="gemini")

    c.initialize()
    c.authenticate("gemini-api-key")
    c.session_new(cwd=Path("/work"), mcp_servers=[{"name": "m"}])
    c.session_load(session_id="store", cwd="/work")
    c.session_prompt(session_id="s1", prompt="hello", message_id="m1")
    c.session_cancel(session_id="s1")
    c.set_session_mode(session_id="s1", mode_id="yolo")
    c.unstable_set_session_model(session_id="s1", model_id="gemini-2.5-pro")

    assert [x[0] for x in calls] == [
        "initialize",
        "authenticate",
        "session/new",
        "session/load",
        "session/prompt",
        "session/set_mode",
        "session/set_model",
    ]
    assert calls[0][1]["protocolVersion"] == 1
    assert calls[2][1] == {"cwd": "/work", "mcpServers": [{"name": "m"}]}
    assert calls[4][1]["prompt"] == [{"type": "text", "text": "hello"}]
    assert notes == [("session/cancel", {"sessionId": "s1"})]


def test_request_permission_auto_approves():
    c = GeminiAcpClient()
    assert c.handle_server_request({"method": "session/request_permission", "params": {}}) == {"outcome": {"outcome": "selected", "optionId": "allow_once"}}
    assert c.handle_server_request({"method": "other", "params": {}}) is None


def test_initialize_rejects_protocol_version_mismatch(monkeypatch):
    monkeypatch.setattr(GeminiAcpClient, "request", lambda self, method, params=None, *, timeout=60.0: {"protocolVersion": 2})
    c = GeminiAcpClient(gemini_binary="gemini")
    try:
        c.initialize()
    except Exception as e:
        assert "protocolVersion 2" in str(e)
        assert "expected 1" in str(e)
    else:
        raise AssertionError("initialize should reject unsupported protocolVersion")


def test_client_uses_stable_acp_flag_when_advertised(monkeypatch):
    monkeypatch.setattr(acp_client.shutil, "which", lambda _name: "/usr/bin/gemini")
    monkeypatch.setattr(
        acp_client.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout="--acp --experimental-acp", stderr=""),
    )

    c = GeminiAcpClient(gemini_binary="gemini")

    assert c._argv[:2] == ["gemini", "--acp"]


def test_client_uses_experimental_acp_flag_when_only_deprecated_flag_is_advertised(monkeypatch):
    monkeypatch.setattr(acp_client.shutil, "which", lambda _name: "/usr/bin/gemini")
    monkeypatch.setattr(
        acp_client.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout="--experimental-acp", stderr=""),
    )

    c = GeminiAcpClient(gemini_binary="gemini")

    assert c._argv[:2] == ["gemini", "--experimental-acp"]


def test_request_permission_default_cancels_and_records_details():
    c = GeminiAcpClient(trust_mode="default")
    response = c.handle_server_request({"method": "session/request_permission", "params": {"toolCall": {"title": "Write file"}}})
    assert response == {"outcome": {"outcome": "selected", "optionId": "cancel"}}
    assert c.permission_blocked is not None
    assert c.permission_blocked["trust_mode"] == "default"
    assert c.permission_blocked["label"] == "Write file"


def test_request_permission_plan_cancels_and_does_not_approve_writes():
    c = GeminiAcpClient(trust_mode="plan")
    response = c.handle_server_request({"method": "session/request_permission", "params": {"toolName": "edit"}})
    assert response == {"outcome": {"outcome": "selected", "optionId": "cancel"}}
    assert c.permission_blocked is not None
    assert c.permission_blocked["trust_mode"] == "plan"


def test_request_permission_default_bridges_allow_once(monkeypatch):
    sent = []
    monkeypatch.setattr(
        acp_client.pio,
        "send_permission_request_to_lead",
        lambda *args, **kwargs: sent.append((args, kwargs)),
    )
    monkeypatch.setattr(
        acp_client.pio,
        "wait_for_permission_response",
        lambda **kwargs: PermissionResponseIn(request_id=kwargs["request_id"], decision="allow_once"),
    )
    c = GeminiAcpClient(trust_mode="default", team_name="t", agent_name="gemini-a", task_id="7", approval_timeout_s=1)

    response = c.handle_server_request({"method": "session/request_permission", "params": {"toolCall": {"title": "Write file"}, "apiToken": "secret"}})

    assert response == {"outcome": {"outcome": "selected", "optionId": "allow_once"}}
    assert c.permission_blocked is None
    assert sent[0][0][:2] == ("t", "gemini-a")
    assert sent[0][1]["task_id"] == "7"
    assert sent[0][1]["tool_args"]["apiToken"] == "<<redacted>>"


def test_request_permission_default_bridges_deny(monkeypatch):
    monkeypatch.setattr(acp_client.pio, "send_permission_request_to_lead", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        acp_client.pio,
        "wait_for_permission_response",
        lambda **kwargs: PermissionResponseIn(request_id=kwargs["request_id"], decision="deny", reason="too risky"),
    )
    c = GeminiAcpClient(trust_mode="default", team_name="t", agent_name="gemini-a", task_id="7", approval_timeout_s=1)

    response = c.handle_server_request({"method": "session/request_permission", "params": {"toolName": "edit"}})

    assert response == {"outcome": {"outcome": "selected", "optionId": "cancel"}}
    assert c.permission_blocked is not None
    assert c.permission_blocked["reason"] == "too risky"


def test_request_permission_default_bridges_timeout(monkeypatch):
    monkeypatch.setattr(acp_client.pio, "send_permission_request_to_lead", lambda *args, **kwargs: None)
    monkeypatch.setattr(acp_client.pio, "wait_for_permission_response", lambda **kwargs: None)
    c = GeminiAcpClient(trust_mode="default", team_name="t", agent_name="gemini-a", task_id="7", approval_timeout_s=1)

    response = c.handle_server_request({"method": "session/request_permission", "params": {"toolName": "edit"}})

    assert response == {"outcome": {"outcome": "selected", "optionId": "cancel"}}
    assert c.permission_blocked is not None
    assert c.permission_blocked["reason"] == "approval_timeout"


def test_request_permission_allow_session_uses_advertised_always_option(monkeypatch):
    monkeypatch.setattr(acp_client.pio, "send_permission_request_to_lead", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        acp_client.pio,
        "wait_for_permission_response",
        lambda **kwargs: PermissionResponseIn(request_id=kwargs["request_id"], decision="allow_session"),
    )
    c = GeminiAcpClient(trust_mode="default", team_name="t", agent_name="gemini-a", task_id="7", approval_timeout_s=1)

    response = c.handle_server_request({"method": "session/request_permission", "params": {"options": [{"optionId": "allow_always"}]}})

    assert response == {"outcome": {"outcome": "selected", "optionId": "allow_always"}}


def test_gemini_acp_client_uses_process_group_flags(monkeypatch):
    monkeypatch.setattr(acp_client, "detect_acp_flag", lambda _binary: "--acp")
    monkeypatch.setattr(acp_client.os, "name", "posix")

    c = GeminiAcpClient(gemini_binary="gemini")

    assert c._start_new_session is True
    assert c._terminate_process_group is True
