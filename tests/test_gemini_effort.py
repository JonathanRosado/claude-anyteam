from __future__ import annotations

import json
import subprocess

import pytest

from claude_anyteam.backends.gemini import invoke
from claude_anyteam.backends.gemini.config import GEMINI_EFFORT_ENV, from_env


def _thinking_config(entry: dict) -> dict:
    return entry["modelConfig"]["generateContentConfig"]["thinkingConfig"]


def test_alias_generation_uses_tier_name() -> None:
    assert invoke.gemini_effort_alias_name("medium") == "claude-anyteam-effort-medium"


@pytest.mark.parametrize(
    ("effort", "budget"),
    [
        ("minimal", 0),
        ("low", 512),
        ("medium", 2048),
        ("high", 4096),
        ("xhigh", 8192),
    ],
)
def test_gemini_25_effort_mapping_uses_thinking_budget(effort: str, budget: int) -> None:
    entry = invoke._effort_alias_entry("gemini-2.5-pro", effort)
    assert entry is not None
    assert entry["extends"] == "gemini-2.5-pro"
    assert _thinking_config(entry) == {"thinkingBudget": budget, "includeThoughts": False}


@pytest.mark.parametrize(
    ("effort", "level"),
    [
        ("minimal", "LOW"),
        ("low", "LOW"),
        ("medium", "MEDIUM"),
        ("high", "HIGH"),
        ("xhigh", "HIGH"),
    ],
)
def test_gemini_3_effort_mapping_uses_thinking_level(effort: str, level: str) -> None:
    entry = invoke._effort_alias_entry("gemini-3-flash-preview", effort)
    assert entry is not None
    assert entry["extends"] == "gemini-3-flash-preview"
    assert _thinking_config(entry) == {"thinkingLevel": level, "includeThoughts": False}


def test_unknown_model_family_does_not_synthesize_alias_and_warns(tmp_path, monkeypatch) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({"mcpServers": {"anyteam": {}}}), encoding="utf-8")
    warnings = []
    monkeypatch.setattr(invoke.logger, "warn", lambda msg, **fields: warnings.append((msg, fields)))

    alias = invoke.inject_effort_alias(settings_path, model="gemini-1.5-pro", effort="high")

    assert alias is None
    assert json.loads(settings_path.read_text(encoding="utf-8")) == {"mcpServers": {"anyteam": {}}}
    assert warnings == [("gemini.effort.unknown_model_family", {"model": "gemini-1.5-pro", "effort": "high"})]


def test_settings_injection_preserves_existing_config_and_adds_custom_alias(tmp_path) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps({"mcpServers": {"anyteam": {"command": "wrapper"}}, "security": {"auth": {"selectedType": "oauth-personal"}}}),
        encoding="utf-8",
    )

    alias = invoke.inject_effort_alias(settings_path, model="gemini-2.5-flash", effort="medium")

    assert alias == "claude-anyteam-effort-medium"
    data = json.loads(settings_path.read_text(encoding="utf-8"))
    assert data["mcpServers"]["anyteam"]["command"] == "wrapper"
    assert data["security"]["auth"]["selectedType"] == "oauth-personal"
    entry = data["modelConfigs"]["customAliases"]["claude-anyteam-effort-medium"]
    assert entry["extends"] == "gemini-2.5-flash"
    assert _thinking_config(entry) == {"thinkingBudget": 2048, "includeThoughts": False}


def test_run_uses_effort_alias_model_and_writes_isolated_settings(tmp_path, monkeypatch) -> None:
    stdout = "\n".join([
        json.dumps({"type": "init", "session_id": "s1"}),
        json.dumps({"type": "message", "role": "assistant", "content": "ok"}),
        json.dumps({"type": "result", "status": "success"}),
    ])
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(invoke.subprocess, "run", fake_run)
    monkeypatch.setattr(invoke.shutil, "which", lambda name: "/bin/" + name)
    home = tmp_path / "home"

    result = invoke.run(
        "prompt",
        cwd=tmp_path,
        gemini_home=home,
        wrapper_identity=("t", "gemini-a"),
        model="gemini-3-flash-preview",
        effort="xhigh",
    )

    assert result.exit_code == 0
    argv = calls[0][0]
    assert argv[argv.index("--model") + 1] == "claude-anyteam-effort-xhigh"
    settings = json.loads((home / ".gemini" / "settings.json").read_text(encoding="utf-8"))
    entry = settings["modelConfigs"]["customAliases"]["claude-anyteam-effort-xhigh"]
    assert entry["extends"] == "gemini-3-flash-preview"
    assert _thinking_config(entry) == {"thinkingLevel": "HIGH", "includeThoughts": False}


def test_run_with_unknown_effort_model_passes_raw_model(tmp_path, monkeypatch) -> None:
    stdout = json.dumps({"type": "result", "status": "success"})
    calls = []
    monkeypatch.setattr(invoke.subprocess, "run", lambda args, **kwargs: calls.append(args) or subprocess.CompletedProcess(args, 0, stdout=stdout, stderr=""))
    monkeypatch.setattr(invoke.shutil, "which", lambda name: "/bin/" + name)
    monkeypatch.setattr(invoke.logger, "warn", lambda *args, **kwargs: None)

    result = invoke.run("prompt", cwd=tmp_path, gemini_home=tmp_path / "home", model="gemini-1.5-pro", effort="high")

    assert result.exit_code == 0
    assert calls[0][calls[0].index("--model") + 1] == "gemini-1.5-pro"


def test_config_reads_gemini_effort_env_and_validates(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CLAUDE_ANYTEAM_TEAM", "t")
    monkeypatch.setenv("CLAUDE_ANYTEAM_NAME", "gemini-a")
    monkeypatch.setenv("CLAUDE_ANYTEAM_CWD", str(tmp_path))
    monkeypatch.setenv(GEMINI_EFFORT_ENV, "low")

    assert from_env().effort == "low"

    monkeypatch.setenv(GEMINI_EFFORT_ENV, "ultra")
    with pytest.raises(ValueError, match="minimal\\|low\\|medium\\|high\\|xhigh"):
        from_env()
