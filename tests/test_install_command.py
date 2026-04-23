from __future__ import annotations

import json
from pathlib import Path

from claude_anyteam import cli as cli_mod
from claude_anyteam import installer as installer_mod


def _make_executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_install_creates_settings_and_sets_required_env_keys(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    settings_path = tmp_path / "home" / ".claude" / "settings.json"
    bin_dir = tmp_path / "venv" / "bin"
    codex_binary = _make_executable(bin_dir / "claude-anyteam")
    shim_binary = _make_executable(bin_dir / "claude-anyteam-spawn-shim")

    monkeypatch.setattr(installer_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(cli_mod.sys, "argv", [str(codex_binary)])

    assert cli_mod.main(["install", "--settings-path", str(settings_path)]) == 0

    payload = json.loads(settings_path.read_text(encoding="utf-8"))
    assert payload == {
        "env": {
            installer_mod.TEAMMATE_COMMAND_KEY: str(shim_binary.resolve()),
            installer_mod.TEAMMATE_BINARY_KEY: str(codex_binary.resolve()),
        }
    }

    stdout = capsys.readouterr().out
    assert f"Updated {settings_path.resolve()}" in stdout
    assert f"Set env.{installer_mod.TEAMMATE_COMMAND_KEY}={shim_binary.resolve()}" in stdout
    assert f"Set env.{installer_mod.TEAMMATE_BINARY_KEY}={codex_binary.resolve()}" in stdout
    assert "Restart Claude Code for the changes to take effect." in stdout


def test_install_preserves_other_settings_and_env_entries(tmp_path: Path, monkeypatch):
    settings_path = tmp_path / "home" / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(
            {
                "theme": "dark",
                "env": {
                    "KEEP_ME": "yes",
                    installer_mod.TEAMMATE_COMMAND_KEY: "/old/shim",
                },
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        installer_mod.shutil,
        "which",
        lambda name: {
            installer_mod.SHIM_BASENAME: "/opt/tools/claude-anyteam-spawn-shim",
            installer_mod.BINARY_BASENAME: "/opt/tools/claude-anyteam",
        }.get(name),
    )

    result = installer_mod.install(settings_path=settings_path)

    payload = json.loads(settings_path.read_text(encoding="utf-8"))
    assert payload["theme"] == "dark"
    assert payload["env"] == {
        "KEEP_ME": "yes",
        installer_mod.TEAMMATE_COMMAND_KEY: "/opt/tools/claude-anyteam-spawn-shim",
        installer_mod.TEAMMATE_BINARY_KEY: "/opt/tools/claude-anyteam",
    }
    assert result.paths.settings_path == settings_path.resolve()


def test_uninstall_removes_only_target_env_keys(
    tmp_path: Path,
    capsys,
):
    settings_path = tmp_path / "home" / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(
            {
                "theme": "dark",
                "env": {
                    "KEEP_ME": "yes",
                    installer_mod.TEAMMATE_COMMAND_KEY: "/opt/tools/claude-anyteam-spawn-shim",
                    installer_mod.TEAMMATE_BINARY_KEY: "/opt/tools/claude-anyteam",
                },
            }
        ),
        encoding="utf-8",
    )

    assert cli_mod.main(["uninstall", "--settings-path", str(settings_path)]) == 0

    payload = json.loads(settings_path.read_text(encoding="utf-8"))
    assert payload == {
        "theme": "dark",
        "env": {
            "KEEP_ME": "yes",
        },
    }

    stdout = capsys.readouterr().out
    assert f"Updated {settings_path.resolve()}" in stdout
    assert "Removed env.CLAUDE_CODE_TEAMMATE_COMMAND, env.CLAUDE_ANYTEAM_BINARY" in stdout
    assert "Restart Claude Code for the changes to take effect." in stdout


def test_main_install_uses_subcommand_path(tmp_path: Path, monkeypatch):
    settings_path = tmp_path / "home" / ".claude" / "settings.json"
    bin_dir = tmp_path / "venv" / "bin"
    codex_binary = _make_executable(bin_dir / "claude-anyteam")
    _make_executable(bin_dir / "claude-anyteam-spawn-shim")

    monkeypatch.setattr(installer_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(cli_mod.sys, "argv", [str(codex_binary)])

    assert cli_mod.main(["install", "--settings-path", str(settings_path)]) == 0
    assert settings_path.exists()
