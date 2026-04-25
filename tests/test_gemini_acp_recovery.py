from __future__ import annotations

import json
import os

from claude_anyteam.backends.gemini import acp, invoke
from claude_anyteam.backends.gemini.acp_client import GeminiAcpError, GeminiAcpTimeoutError


class RecoveringClient:
    loads = []
    news = 0
    def __init__(self, **kwargs): pass
    def start(self): pass
    def close(self): pass
    def initialize(self): return {"protocolVersion": 1}
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
    invoke.write_adapter_state(home, backend="acp", acp_session_id="bad-live")
    RecoveringClient.loads = []
    monkeypatch.setattr(acp, "GeminiAcpClient", RecoveringClient)
    monkeypatch.setattr(acp.invoke.shutil, "which", lambda name: "/bin/" + name)
    result = acp.run("prompt", cwd=tmp_path, gemini_home=home)
    assert RecoveringClient.loads == ["bad-live"]
    assert result.session_id == "live-new"
    state = json.loads((home / ".claude-anyteam" / "state.json").read_text())
    assert state["acp_session_id"] == "live-new"


class PromptTimeoutClient(RecoveringClient):
    def session_load(self, **kwargs):
        return {"sessionId": kwargs["session_id"]}

    def session_prompt(self, **kwargs):
        raise GeminiAcpTimeoutError("JSON-RPC stdio process did not respond to session/prompt within 0.01s")


def test_jsonrpc_timeout_drops_persisted_acp_session(tmp_path, monkeypatch):
    home = tmp_path / "home"
    invoke.write_adapter_state(
        home,
        backend="acp",
        acp_session_id="zombie-live",
        acp_storage_session_id="zombie-store",
    )
    monkeypatch.setattr(acp, "GeminiAcpClient", PromptTimeoutClient)

    result = acp.run("prompt", cwd=tmp_path, gemini_home=home, timeout_s=0.01)

    assert result.exit_code == 124
    assert result.session_id == "zombie-live"
    assert "timed out" in (result.error or "")
    state = json.loads((home / ".claude-anyteam" / "state.json").read_text())
    assert state["acp_session_id"] is None
    assert state["acp_storage_session_id"] is None


class RuntimeStateClient(RecoveringClient):
    pid = 43210
    pgid = 43210

    def session_load(self, **kwargs):
        raise GeminiAcpError("no stored session")


def test_successful_acp_session_persists_adapter_and_gemini_pids(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(acp, "GeminiAcpClient", RuntimeStateClient)
    if hasattr(acp, "crash_hygiene"):
        monkeypatch.setattr(acp.crash_hygiene, "clear_acp_child", lambda home: None)
    result = acp.run("prompt", cwd=tmp_path, gemini_home=home, wrapper_identity=("team", "agent"))

    assert result.exit_code == 0
    state = invoke.read_adapter_state(home)
    assert state["adapter_pid"] == os.getpid()
    assert isinstance(state["adapter_start_time"], str)
    assert state["adapter_start_time"].endswith("Z")
    assert state["gemini_pid"] == RuntimeStateClient.pid
    assert state["team"] == "team"
    assert state["agent"] == "agent"
