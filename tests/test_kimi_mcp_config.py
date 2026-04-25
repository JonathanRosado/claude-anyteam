"""Tests for the Kimi MCP config file written for the anyteam wrapper."""

from __future__ import annotations

import json
from pathlib import Path

from claude_anyteam.backends.kimi import invoke


def test_write_mcp_config_uses_anyteam_alias_absolute_wrapper_and_args(tmp_path, monkeypatch):
    wrapper = tmp_path / "bin" / "claude-anyteam-wrapper"
    wrapper.parent.mkdir()
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(
        invoke.shutil,
        "which",
        lambda name: str(wrapper) if name == "claude-anyteam-wrapper" else None,
    )

    config_path = invoke.write_mcp_config(
        tmp_path / "isolated-home",
        team="kimi-team",
        agent_name="kimi-agent",
    )

    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert set(data) == {"mcpServers"}
    assert set(data["mcpServers"]) == {"anyteam"}
    server = data["mcpServers"]["anyteam"]
    assert Path(server["command"]).is_absolute()
    assert server["command"] == str(wrapper.resolve())
    assert server["args"] == ["--team", "kimi-team", "--name", "kimi-agent"]


def test_write_mcp_config_restores_real_home_when_kimi_home_is_isolated(tmp_path, monkeypatch):
    isolated_home = tmp_path / "isolated-home"
    real_home = tmp_path / "real-home"
    env_home = tmp_path / "env-home"
    monkeypatch.setenv("HOME", str(env_home))
    monkeypatch.setattr(invoke.shutil, "which", lambda _name: "/usr/local/bin/claude-anyteam-wrapper")

    config_path = invoke.write_mcp_config(
        isolated_home,
        team="team-with-isolated-home",
        agent_name="kimi-agent",
        real_home=str(real_home),
    )

    server = json.loads(config_path.read_text(encoding="utf-8"))["mcpServers"]["anyteam"]
    assert server["env"]["HOME"] == str(real_home)
    assert server["env"]["HOME"] != str(isolated_home)
    assert server["env"]["HOME"] != str(env_home)
    assert server["env"]["CLAUDE_ANYTEAM_TEAM"] == "team-with-isolated-home"
    assert server["env"]["CLAUDE_ANYTEAM_NAME"] == "kimi-agent"
