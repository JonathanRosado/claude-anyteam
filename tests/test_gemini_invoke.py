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
    (real_gemini / "settings.json").write_text(json.dumps({
        "security": {"auth": {"selectedType": "oauth-personal"}},
        "otherTopLevel": {"must": "not copy"},
    }))

    settings_path = invoke.write_mcp_settings(tmp_path / "isolated", team="t", agent_name="gemini-a", real_home=str(real_home))

    data = json.loads(settings_path.read_text())
    assert data["security"]["auth"] == {"selectedType": "oauth-personal"}
    assert "otherTopLevel" not in data
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
        effort="xhigh",
        gemini_home=tmp_path / "home",
    )

    assert result.exit_code == 0
    assert result.structured == {"files_changed": [], "summary": "done"}
    assert result.session_id == "s1"
    assert result.tool_call_events == 1
    argv = calls[0][0]
    assert argv[:5] == ["gemini", "--prompt", "prompt", "--output-format", "stream-json"]
    assert "--resume" in argv and "old" in argv
    assert "--model" in argv and "claude-anyteam-effort-xhigh" in argv
    data = json.loads((tmp_path / "home" / ".gemini" / "settings.json").read_text())
    alias_config = data["modelConfigs"]["customAliases"]["claude-anyteam-effort-xhigh"]
    assert alias_config["extends"] == "gemini-2.5-pro"
    assert alias_config["modelConfig"]["generateContentConfig"]["thinkingConfig"]["thinkingBudget"] == 8192
    assert calls[0][1]["stdin"] is subprocess.DEVNULL


def test_run_fails_when_stream_ends_without_result(tmp_path, monkeypatch):
    stdout = "\n".join([
        json.dumps({"type": "init", "session_id": "s1"}),
        json.dumps({"type": "message", "role": "assistant", "content": '{"files_changed":[],"summary":"done"}'}),
    ])

    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(invoke.subprocess, "run", fake_run)
    monkeypatch.setattr(invoke.shutil, "which", lambda name: "/bin/" + name)

    result = invoke.run(
        "prompt",
        cwd=tmp_path,
        schema=invoke.TASK_COMPLETE_SCHEMA,
        gemini_binary="gemini",
        gemini_home=tmp_path / "home",
    )

    assert result.exit_code != 0
    assert result.structured == {"files_changed": [], "summary": "done"}
    assert result.error == "gemini stream ended without result event"


def test_run_discards_late_init_and_logs_warning(tmp_path, monkeypatch):
    stdout = "\n".join([
        json.dumps({"type": "init", "session_id": "s1"}),
        json.dumps({"type": "message", "role": "assistant", "content": '{"files_changed":[],"summary":"done"}'}),
        json.dumps({"type": "init", "session_id": "s2"}),
        json.dumps({"type": "result", "status": "success"}),
    ])
    warnings = []

    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    def fake_warn(msg, **fields):
        warnings.append((msg, fields))

    monkeypatch.setattr(invoke.subprocess, "run", fake_run)
    monkeypatch.setattr(invoke.shutil, "which", lambda name: "/bin/" + name)
    monkeypatch.setattr(invoke.logger, "warn", fake_warn)

    result = invoke.run(
        "prompt",
        cwd=tmp_path,
        schema=invoke.TASK_COMPLETE_SCHEMA,
        gemini_binary="gemini",
        gemini_home=tmp_path / "home",
    )

    assert result.exit_code == 0
    assert result.error is None
    assert result.session_id == "s1"
    assert warnings == [("gemini.late_init", {"session_id": "s2", "captured_session_id": "s1"})]


def test_feature_test_requires_headless_flags(monkeypatch):
    monkeypatch.setattr(invoke.shutil, "which", lambda name: "/bin/gemini")

    def fake_run(args, **kwargs):
        if args[1] == "--version":
            return subprocess.CompletedProcess(args, 0, stdout="0.39.0", stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="--prompt --output-format --resume", stderr="")

    monkeypatch.setattr(invoke.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="--approval-mode"):
        invoke.feature_test("gemini")


def test_prepare_isolated_gemini_home_copies_mutable_auth_files_and_scopes_trust(tmp_path):
    real_home = tmp_path / "real"
    real_gemini = real_home / ".gemini"
    real_gemini.mkdir(parents=True)
    for name in ("oauth_creds.json", "google_accounts.json", "projects.json", "state.json", "installation_id"):
        (real_gemini / name).write_text(f"{name}-real", encoding="utf-8")
    (real_gemini / "trustedFolders.json").write_text(json.dumps({"/secret": "TRUST_FOLDER"}), encoding="utf-8")
    (real_gemini / "tmp").mkdir()
    (real_gemini / "history").mkdir()
    cwd = tmp_path / "workspace"
    include_dir = tmp_path / "include"
    cwd.mkdir()
    include_dir.mkdir()

    home_a = tmp_path / "agent-a"
    home_b = tmp_path / "agent-b"
    invoke.prepare_isolated_gemini_home(home_a, real_home=str(real_home), cwd=cwd, include_dirs=[include_dir])
    invoke.prepare_isolated_gemini_home(home_b, real_home=str(real_home), cwd=cwd, include_dirs=[include_dir])

    for home in (home_a, home_b):
        gemini_dir = home / ".gemini"
        for name in ("oauth_creds.json", "google_accounts.json", "projects.json", "state.json", "installation_id"):
            copied = gemini_dir / name
            assert copied.exists()
            assert not copied.is_symlink()
        assert not (gemini_dir / "tmp").exists()
        assert not (gemini_dir / "history").exists()
        trusted = json.loads((gemini_dir / "trustedFolders.json").read_text(encoding="utf-8"))
        assert trusted == {str(cwd.resolve()): "TRUST_FOLDER", str(include_dir.resolve()): "TRUST_FOLDER"}
        assert (home / ".claude-anyteam" / "state.json").exists()

    assert (home_a / ".gemini" / "oauth_creds.json").resolve() != (home_b / ".gemini" / "oauth_creds.json").resolve()


def test_prepare_isolated_gemini_home_generates_installation_id_when_absent(tmp_path):
    real_home = tmp_path / "real"
    (real_home / ".gemini").mkdir(parents=True)
    isolated = tmp_path / "isolated"

    invoke.prepare_isolated_gemini_home(isolated, real_home=str(real_home))

    installation_id = (isolated / ".gemini" / "installation_id").read_text(encoding="utf-8").strip()
    assert installation_id
    assert not (isolated / ".gemini" / "installation_id").is_symlink()


def test_run_persists_headless_session_id_to_adapter_state(tmp_path, monkeypatch):
    stdout = "\n".join([
        json.dumps({"type": "init", "session_id": "s-persist"}),
        json.dumps({"type": "message", "role": "assistant", "content": "ok"}),
        json.dumps({"type": "result", "status": "success"}),
    ])

    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(invoke.subprocess, "run", fake_run)
    monkeypatch.setattr(invoke.shutil, "which", lambda name: "/bin/" + name)
    home = tmp_path / "home"

    result = invoke.run("prompt", cwd=tmp_path, gemini_home=home)

    assert result.session_id == "s-persist"
    state = json.loads((home / ".claude-anyteam" / "state.json").read_text(encoding="utf-8"))
    assert state["headless_session_id"] == "s-persist"
    assert state["acp_session_id"] is None
    assert state["backend"] == "headless"
    assert state["updated_at"]
