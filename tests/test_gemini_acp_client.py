from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from claude_anyteam.backends.gemini import acp_client
from claude_anyteam.backends.gemini.acp_client import GeminiAcpClient


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
