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
    "claude-anyteam is installed; Agent Teams teammates named codex-* route to Codex, "
    "gemini-* to Gemini, kimi-* to Kimi. Use `claude-anyteam team-agent|team-patch|team-roster` "
    "for team config (preferred over hand-edits). Docs: https://github.com/JonathanRosado/claude-anyteam"
)
DRIFT_WARNING = "claude-anyteam: settings drifted — run `claude-anyteam install` to repair"


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
    assert "Kimi" in plugin["description"]
    assert "kimi-*" in marketplace["plugins"][0]["description"]
    assert "kimi" in plugin["keywords"]
    assert "kimi" in marketplace["plugins"][0]["tags"]


def test_help_skill_exists_and_teaches_claude_about_cli_teammates() -> None:
    content = HELP_SKILL.read_text(encoding="utf-8")

    assert "name: help" in content
    # Core intent of the skill (unchanged across rewrites): teach Claude Code
    # to name CLI teammates with `codex-` / `gemini-` / `kimi-` prefixes and call the Agent Teams
    # tools directly instead of explaining the mechanism.
    assert "codex-" in content
    assert "gemini-" in content
    assert "kimi-" in content
    assert "^codex-" in content
    assert "^gemini-" in content
    assert "^kimi-" in content
    assert "kimi-code/kimi-for-coding" in content
    assert "TeamCreate" in content
    assert "Agent(" in content
    assert "when_to_use:" in content
    # Must not mark this skill as user-invokable only; it's proactive.
    assert "disable-model-invocation: true" not in content


def test_help_skill_teaches_native_host_tools_via_manifest_lookup() -> None:
    """Issue #33 regression guard.

    The codex-app-server-native tools (`imagegeneration`, `imageview`,
    `websearch`, `filechange`) live outside the wrapper-MCP surface and the
    event classifier already knows about them, but leads have no way to
    discover them unless the help skill teaches the lookup pattern. Per
    north-star §1 (`feedback_capability_decl_vs_flatten`) the canonical
    enumeration lives in each teammate's capability manifest under
    `live_tool_events.native_host_tools`, NOT in skill text — the skill
    must teach the lookup, not flatten the inventory across backends.
    """
    content = HELP_SKILL.read_text(encoding="utf-8")
    # The skill must teach manifest lookup, not enumerate.
    assert "mcp_anyteam_capability_manifest" in content, (
        "help skill must teach the manifest-lookup pattern for native host tools"
    )
    assert "native_host_tools" in content, (
        "help skill must reference the canonical native_host_tools manifest field"
    )
    assert "live_tool_events" in content, (
        "help skill must name the live_tool_events capability that carries the inventory"
    )
    # Codex App Server's native tools are still the worked example so leads
    # have at least one concrete tool name to recognize in telemetry.
    for tool in ("imagegeneration", "websearch"):
        assert tool in content, f"help skill must mention codex-native tool `{tool}` as worked example"
    # The section must explicitly state these are NOT exposed by the wrapper MCP
    # so leads don't conflate them with mcp_anyteam_* tools.
    assert "wrapper-MCP" in content or "wrapper MCP" in content


def test_help_skill_disambiguates_against_codex_jr() -> None:
    """Issue #35 regression guard.

    When both claude-anyteam and codex-jr are installed, the lead's natural
    landing place is whichever surface is more discoverable. The help skill
    is the canonical disambiguation surface — it must explain that
    `codex-jr:codex-rescue` is a one-shot subagent, not a teammate, and
    that team-shaped intent should route through claude-anyteam.
    """
    content = HELP_SKILL.read_text(encoding="utf-8")
    assert "codex-jr" in content, "help skill must mention codex-jr for disambiguation"
    assert "codex-rescue" in content, "help skill must name codex-jr:codex-rescue explicitly"
    assert "one-shot" in content, "help skill must explain codex-jr is one-shot, not teammate"


def test_help_skill_promotes_runtime_observability_surface() -> None:
    """Issue #38 regression guard.

    Leads should not have to discover observability the hard way — i.e.
    after a failure forces them to grep for it. The help skill must promote
    `claude-anyteam status`, `claude-anyteam diagnose`, and
    `claude-anyteam visibility-tail` so a lead asking "is the team healthy?"
    finds a protocol-supported answer instead of dropping to file-system probes.
    """
    content = HELP_SKILL.read_text(encoding="utf-8")
    for cmd in (
        "claude-anyteam status",
        "claude-anyteam diagnose",
        "claude-anyteam visibility-tail",
    ):
        assert cmd in content, f"help skill must promote `{cmd}` for run-time observability"
    # The when_to_use must trigger on observation-shaped phrasing, not only
    # on spawn-shaped intent (#38 root cause: observation intent didn't
    # auto-load the help skill).
    assert "is the team healthy" in content
    # The #36 follow-up folds in here: a one-glance "is my team healthy?"
    # checklist must point leads at the canonical registration invariants
    # (registration_status, agent_type=claude-anyteam, capability_version="2",
    # backend_type=in-process) and at the diagnose surface for the deep dive.
    assert "registration_status" in content
    assert 'capability_version' in content
    assert 'agent_type' in content


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


def _write_hook_settings(home: Path, env_block: dict[str, str]) -> Path:
    settings_path = home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps({"env": env_block}), encoding="utf-8")
    return settings_path


def test_session_start_hook_prints_orientation_when_settings_are_complete(tmp_path: Path) -> None:
    home = tmp_path / "home"
    command = _make_executable(tmp_path / "configured" / "claude-anyteam-spawn-shim", "#!/bin/sh\n")
    binary = _make_executable(tmp_path / "configured" / "claude-anyteam", "#!/bin/sh\n")
    gemini_binary = _make_executable(tmp_path / "configured" / "gemini-anyteam", "#!/bin/sh\n")
    kimi_binary = _make_executable(tmp_path / "configured" / "kimi-anyteam", "#!/bin/sh\n")
    _write_hook_settings(
        home,
        {
            "CLAUDE_CODE_TEAMMATE_COMMAND": str(command),
            "CLAUDE_ANYTEAM_BINARY": str(binary),
            "CLAUDE_ANYTEAM_GEMINI_BINARY": str(gemini_binary),
            "CLAUDE_ANYTEAM_KIMI_BINARY": str(kimi_binary),
        },
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


def test_session_start_hook_warns_when_env_var_is_missing(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _write_hook_settings(
        home,
        {
            "CLAUDE_CODE_TEAMMATE_COMMAND": "/configured/claude-anyteam-spawn-shim",
            "CLAUDE_ANYTEAM_BINARY": "/configured/claude-anyteam",
        },
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
    assert not marker.exists()
    assert completed.stdout.strip() == DRIFT_WARNING


def test_session_start_hook_warns_when_configured_paths_are_stale(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _write_hook_settings(
        home,
        {
            "CLAUDE_CODE_TEAMMATE_COMMAND": str(tmp_path / "missing" / "claude-anyteam-spawn-shim"),
            "CLAUDE_ANYTEAM_BINARY": str(tmp_path / "missing" / "claude-anyteam"),
            "CLAUDE_ANYTEAM_GEMINI_BINARY": str(tmp_path / "missing" / "gemini-anyteam"),
            "CLAUDE_ANYTEAM_KIMI_BINARY": str(tmp_path / "missing" / "kimi-anyteam"),
        },
    )

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["CLAUDE_PLUGIN_ROOT"] = str(REPO_ROOT)

    completed = subprocess.run(
        [str(HOOK_SCRIPT)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert completed.returncode == 0
    assert completed.stdout.strip() == DRIFT_WARNING


def test_session_start_hook_warns_when_configured_paths_are_not_executable(tmp_path: Path) -> None:
    home = tmp_path / "home"
    command = tmp_path / "configured" / "claude-anyteam-spawn-shim"
    binary = tmp_path / "configured" / "claude-anyteam"
    gemini_binary = tmp_path / "configured" / "gemini-anyteam"
    kimi_binary = tmp_path / "configured" / "kimi-anyteam"
    for path in (command, binary, gemini_binary, kimi_binary):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\n", encoding="utf-8")
        path.chmod(0o644)
    _write_hook_settings(
        home,
        {
            "CLAUDE_CODE_TEAMMATE_COMMAND": str(command),
            "CLAUDE_ANYTEAM_BINARY": str(binary),
            "CLAUDE_ANYTEAM_GEMINI_BINARY": str(gemini_binary),
            "CLAUDE_ANYTEAM_KIMI_BINARY": str(kimi_binary),
        },
    )

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["CLAUDE_PLUGIN_ROOT"] = str(REPO_ROOT)

    completed = subprocess.run(
        [str(HOOK_SCRIPT)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert completed.returncode == 0
    assert completed.stdout.strip() == DRIFT_WARNING


def test_session_start_hook_warns_when_settings_are_missing(tmp_path: Path) -> None:
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
    assert not marker.exists()
    assert completed.stdout.strip() == DRIFT_WARNING


def test_session_start_hook_warns_when_settings_are_malformed(tmp_path: Path) -> None:
    home = tmp_path / "home"
    settings_path = home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text("{not json", encoding="utf-8")

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["CLAUDE_PLUGIN_ROOT"] = str(REPO_ROOT)

    completed = subprocess.run(
        [str(HOOK_SCRIPT)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert completed.returncode == 0
    assert completed.stdout.strip() == DRIFT_WARNING


def test_session_start_hook_uses_grep_fallback_when_python3_is_missing(tmp_path: Path) -> None:
    home = tmp_path / "home"
    command = _make_executable(tmp_path / "configured" / "claude-anyteam-spawn-shim", "#!/bin/sh\n")
    binary = _make_executable(tmp_path / "configured" / "claude-anyteam", "#!/bin/sh\n")
    gemini_binary = _make_executable(tmp_path / "configured" / "gemini-anyteam", "#!/bin/sh\n")
    kimi_binary = _make_executable(tmp_path / "configured" / "kimi-anyteam", "#!/bin/sh\n")
    _write_hook_settings(
        home,
        {
            "CLAUDE_CODE_TEAMMATE_COMMAND": str(command),
            "CLAUDE_ANYTEAM_BINARY": str(binary),
            "CLAUDE_ANYTEAM_GEMINI_BINARY": str(gemini_binary),
            "CLAUDE_ANYTEAM_KIMI_BINARY": str(kimi_binary),
        },
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
    assert completed.stdout.strip() == ORIENTATION_MESSAGE
