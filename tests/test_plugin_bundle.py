from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_MANIFEST = REPO_ROOT / ".claude-plugin" / "plugin.json"
MARKETPLACE_MANIFEST = REPO_ROOT / ".claude-plugin" / "marketplace.json"
HOOK_SCRIPT = REPO_ROOT / "hooks" / "session-start.sh"
WRAPPER_SCRIPT = REPO_ROOT / "bin" / "claude-anyteam"
HELP_SKILL = REPO_ROOT / "skills" / "help" / "SKILL.md"
STATUS_SKILL = REPO_ROOT / "skills" / "status" / "SKILL.md"
ORIENTATION_MESSAGE = (
    "claude-anyteam is installed; Agent Teams teammates named codex-* route to Codex "
    "and gemini-* route to Gemini CLI. Docs: https://github.com/JonathanRosado/claude-anyteam"
)


def _make_executable(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


def test_plugin_manifests_exist_and_are_well_formed() -> None:
    plugin = json.loads(PLUGIN_MANIFEST.read_text(encoding="utf-8"))
    marketplace = json.loads(MARKETPLACE_MANIFEST.read_text(encoding="utf-8"))

    assert plugin["name"] == "claude-anyteam"
    assert plugin["version"]
    assert plugin["homepage"] == "https://github.com/JonathanRosado/claude-anyteam"
    assert plugin["repository"] == "https://github.com/JonathanRosado/claude-anyteam"
    assert "hooks" not in plugin
    assert marketplace["name"] == "claude-anyteam"
    assert marketplace["plugins"][0]["name"] == "claude-anyteam"
    assert marketplace["plugins"][0]["source"] == "./"


def test_help_skill_exists_and_teaches_claude_about_cli_teammates() -> None:
    content = HELP_SKILL.read_text(encoding="utf-8")

    assert "name: help" in content
    # Core intent of the skill (unchanged across rewrites): teach Claude Code
    # to name CLI teammates with `codex-` / `gemini-` prefixes and call the Agent Teams
    # tools directly instead of explaining the mechanism.
    assert "codex-" in content
    assert "gemini-" in content
    assert "^codex-" in content
    assert "^gemini-" in content
    assert "TeamCreate" in content
    assert "Agent(" in content
    assert "when_to_use:" in content
    # Must not mark this skill as user-invokable only; it's proactive.
    assert "disable-model-invocation: true" not in content


def test_status_skill_has_required_name_frontmatter() -> None:
    content = STATUS_SKILL.read_text(encoding="utf-8")

    assert "name: status" in content
    assert "disable-model-invocation: true" in content


def test_wrapper_delegates_to_real_console_script(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    called = tmp_path / "called.txt"
    _make_executable(
        fake_bin / "claude-anyteam",
        f"#!/bin/sh\nprintf '%s\\n' \"$*\" > {called!s}\n",
    )

    env = os.environ.copy()
    env["PATH"] = f"{REPO_ROOT / 'bin'}:{fake_bin}:{env['PATH']}"

    completed = subprocess.run(
        [str(WRAPPER_SCRIPT), "install", "--settings-path", "/tmp/test-settings.json"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert completed.returncode == 0
    assert called.read_text(encoding="utf-8").strip() == "install --settings-path /tmp/test-settings.json"


def test_wrapper_prints_clear_error_when_package_is_missing(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["PATH"] = f"{REPO_ROOT / 'bin'}:{tmp_path}:/usr/bin:/bin"

    completed = subprocess.run(
        [str(WRAPPER_SCRIPT), "install"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert completed.returncode == 127
    assert "uv add claude-anyteam" in completed.stderr


def test_session_start_hook_skips_install_when_command_is_already_configured(tmp_path: Path) -> None:
    home = tmp_path / "home"
    settings_path = home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    configured_bin = tmp_path / "configured-bin"
    shim = _make_executable(configured_bin / "claude-anyteam-spawn-shim", "#!/bin/sh\nexit 0\n")
    binary = _make_executable(configured_bin / "claude-anyteam", "#!/bin/sh\nexit 0\n")
    settings_path.write_text(
        json.dumps(
            {
                "env": {
                    "CLAUDE_CODE_TEAMMATE_COMMAND": str(shim),
                    "CLAUDE_ANYTEAM_BINARY": str(binary),
                }
            }
        ),
        encoding="utf-8",
    )

    fake_bin = tmp_path / "fake-bin"
    marker = tmp_path / "install-called.txt"
    _make_executable(
        fake_bin / "claude-anyteam",
        f"#!/bin/sh\necho called > {marker!s}\n",
    )

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["CLAUDE_PLUGIN_ROOT"] = str(REPO_ROOT)
    env["PATH"] = f"{fake_bin}:{env['PATH']}"

    completed = subprocess.run(
        [str(HOOK_SCRIPT)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert completed.returncode == 0
    assert not marker.exists()
    assert completed.stdout.strip() == ORIENTATION_MESSAGE


def test_session_start_hook_repairs_missing_binary_entry(tmp_path: Path) -> None:
    home = tmp_path / "home"
    settings_path = home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    configured_bin = tmp_path / "configured-bin"
    shim = _make_executable(configured_bin / "claude-anyteam-spawn-shim", "#!/bin/sh\nexit 0\n")
    settings_path.write_text(
        json.dumps(
            {
                "env": {
                    "CLAUDE_CODE_TEAMMATE_COMMAND": str(shim),
                }
            }
        ),
        encoding="utf-8",
    )

    fake_bin = tmp_path / "fake-bin"
    marker = tmp_path / "install-called.txt"
    _make_executable(
        fake_bin / "claude-anyteam",
        f"#!/bin/sh\nprintf '%s\\n' \"$*\" > {marker!s}\n",
    )

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["CLAUDE_PLUGIN_ROOT"] = str(REPO_ROOT)
    env["PATH"] = f"{fake_bin}:{env['PATH']}"

    completed = subprocess.run(
        [str(HOOK_SCRIPT)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert completed.returncode == 0
    assert marker.read_text(encoding="utf-8").strip() == "install"
    assert completed.stdout.strip() == ORIENTATION_MESSAGE


def test_session_start_hook_repairs_missing_executable_paths(tmp_path: Path) -> None:
    home = tmp_path / "home"
    settings_path = home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(
            {
                "env": {
                    "CLAUDE_CODE_TEAMMATE_COMMAND": "/missing/claude-anyteam-spawn-shim",
                    "CLAUDE_ANYTEAM_BINARY": "/missing/claude-anyteam",
                }
            }
        ),
        encoding="utf-8",
    )

    fake_bin = tmp_path / "fake-bin"
    marker = tmp_path / "install-called.txt"
    _make_executable(
        fake_bin / "claude-anyteam",
        f"#!/bin/sh\nprintf '%s\\n' \"$*\" > {marker!s}\n",
    )

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["CLAUDE_PLUGIN_ROOT"] = str(REPO_ROOT)
    env["PATH"] = f"{fake_bin}:{env['PATH']}"

    completed = subprocess.run(
        [str(HOOK_SCRIPT)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert completed.returncode == 0
    assert marker.read_text(encoding="utf-8").strip() == "install"


def test_session_start_hook_runs_install_once_when_missing(tmp_path: Path) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "fake-bin"
    marker = tmp_path / "install-called.txt"
    _make_executable(
        fake_bin / "claude-anyteam",
        f"#!/bin/sh\nprintf '%s\\n' \"$*\" > {marker!s}\n",
    )

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["CLAUDE_PLUGIN_ROOT"] = str(REPO_ROOT)
    env["PATH"] = f"{fake_bin}:{env['PATH']}"

    completed = subprocess.run(
        [str(HOOK_SCRIPT)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert completed.returncode == 0
    assert marker.read_text(encoding="utf-8").strip() == "install"
    assert completed.stdout.strip() == ORIENTATION_MESSAGE


def test_session_start_hook_uses_grep_fallback_when_python3_is_missing(tmp_path: Path) -> None:
    home = tmp_path / "home"
    settings_path = home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(
            {
                "env": {
                    "CLAUDE_CODE_TEAMMATE_COMMAND": "/already/configured/claude-anyteam-spawn-shim"
                }
            }
        ),
        encoding="utf-8",
    )

    fake_bin = tmp_path / "fake-bin"
    marker = tmp_path / "install-called.txt"
    _make_executable(
        fake_bin / "claude-anyteam",
        f"#!/bin/sh\necho called > {marker!s}\n",
    )
    grep_path = shutil.which("grep")
    assert grep_path is not None
    (fake_bin / "grep").symlink_to(grep_path)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["CLAUDE_PLUGIN_ROOT"] = str(REPO_ROOT)
    env["PATH"] = str(fake_bin)

    completed = subprocess.run(
        [str(HOOK_SCRIPT)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert completed.returncode == 0
    assert not marker.exists()


def test_session_start_hook_ignores_missing_console_script(tmp_path: Path) -> None:
    home = tmp_path / "home"
    plugin_root = tmp_path / "plugin-root"
    _make_executable(
        plugin_root / "bin" / "claude-anyteam",
        "#!/bin/sh\nexit 127\n",
    )

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["CLAUDE_PLUGIN_ROOT"] = str(plugin_root)

    completed = subprocess.run(
        [str(HOOK_SCRIPT)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert completed.returncode == 0


def test_session_start_hook_propagates_real_install_failures(tmp_path: Path) -> None:
    home = tmp_path / "home"
    plugin_root = tmp_path / "plugin-root"
    _make_executable(
        plugin_root / "bin" / "claude-anyteam",
        "#!/bin/sh\nexit 2\n",
    )

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["CLAUDE_PLUGIN_ROOT"] = str(plugin_root)

    completed = subprocess.run(
        [str(HOOK_SCRIPT)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert completed.returncode == 2
