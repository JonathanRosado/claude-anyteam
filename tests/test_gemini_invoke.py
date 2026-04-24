from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from claude_anyteam.backends.gemini import invoke


def test_write_mcp_settings_uses_isolated_home_and_anyteam_alias(tmp_path, monkeypatch):
    wrapper = tmp_path / "bin" / "claude-anyteam-wrapper"
    wrapper.parent.mkdir()
    wrapper.write_text("#!/bin/sh\n")
    monkeypatch.setattr(invoke.shutil, "which", lambda name: str(wrapper) if name == "claude-anyteam-wrapper" else None)
    real_home = tmp_path / "real-home"
    real_gemini = real_home / ".gemini"
    real_gemini.mkdir(parents=True)
    (real_gemini / "oauth_creds.json").write_text("{}")

    settings_path = invoke.write_mcp_settings(tmp_path / "isolated", team="t", agent_name="gemini-a", real_home=str(real_home))

    data = json.loads(settings_path.read_text())
    server = data["mcpServers"]["anyteam"]
    assert server["command"] == str(wrapper)
    assert server["args"] == ["--team", "t", "--name", "gemini-a"]
    assert server["trust"] is True
    assert server["env"]["HOME"] == str(real_home)
    assert server["env"]["CLAUDE_ANYTEAM_TEAM"] == "t"
    assert (settings_path.parent / "oauth_creds.json").exists()


def test_run_parses_stream_json_and_validates_schema(tmp_path, monkeypatch):
    stdout = "\n".join([
        json.dumps({"type": "init", "session_id": "s1"}),
        "startup banner that should be ignored",
        json.dumps({"type": "tool_use", "tool_name": "mcp_anyteam_read_config"}),
        json.dumps({"type": "tool_result", "status": "success"}),
        json.dumps({"type": "message", "role": "assistant", "content": '{"files_changed":', "delta": True}),
        json.dumps({"type": "message", "role": "assistant", "content": '[],"summary":"done"}', "delta": True}),
        json.dumps({"type": "result", "status": "success", "stats": {"tool_calls": 1}}),
    ])
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(invoke.subprocess, "run", fake_run)
    monkeypatch.setattr(invoke.shutil, "which", lambda name: "/bin/" + name)

    result = invoke.run(
        "prompt",
        cwd=tmp_path,
        schema=invoke.TASK_COMPLETE_SCHEMA,
        gemini_binary="gemini",
        wrapper_identity=("team", "gemini-a"),
        resume_session_id="old",
        model="gemini-2.5-pro",
        gemini_home=tmp_path / "home",
    )

    assert result.exit_code == 0
    assert result.structured == {"files_changed": [], "summary": "done"}
    assert result.session_id == "s1"
    assert result.tool_call_events == 1
    argv = calls[0][0]
    assert argv[:5] == ["gemini", "--prompt", "prompt", "--output-format", "stream-json"]
    assert "--resume" in argv and "old" in argv
    assert "--model" in argv and "gemini-2.5-pro" in argv
    assert calls[0][1]["stdin"] is subprocess.DEVNULL


def test_feature_test_requires_headless_flags(monkeypatch):
    monkeypatch.setattr(invoke.shutil, "which", lambda name: "/bin/gemini")

    def fake_run(args, **kwargs):
        if args[1] == "--version":
            return subprocess.CompletedProcess(args, 0, stdout="0.39.0", stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="--prompt --output-format", stderr="")

    monkeypatch.setattr(invoke.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="--resume"):
        invoke.feature_test("gemini")
