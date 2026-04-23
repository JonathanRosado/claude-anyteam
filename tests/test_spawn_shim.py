from __future__ import annotations

import json
import sys

import pytest

from claude_anyteam import spawn_shim


def _record_execv(monkeypatch):
    calls: list[tuple[str, list[str]]] = []

    def fake_execv(path: str, argv: list[str]) -> None:
        calls.append((path, argv))

    monkeypatch.setattr(spawn_shim.os, "execv", fake_execv)
    return calls


def _clear_binary_env(monkeypatch) -> None:
    monkeypatch.delenv("CLAUDE_ANYTEAM_BINARY", raising=False)
    monkeypatch.delenv("CODEX_TEAMMATE_BINARY", raising=False)


def test_codex_dispatch(monkeypatch, capsys):
    calls = _record_execv(monkeypatch)
    _clear_binary_env(monkeypatch)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "/usr/local/bin/claude-anyteam-spawn-shim",
            "--agent-name",
            "codex-alice",
            "--team-name",
            "shim-build",
        ],
    )
    monkeypatch.setattr(
        spawn_shim.shutil,
        "which",
        lambda name: {
            "claude-anyteam": "/usr/local/bin/claude-anyteam",
            "claude": "/usr/local/bin/claude",
        }.get(name),
    )

    assert spawn_shim.main() == 0

    assert calls == [
        (
            "/usr/local/bin/claude-anyteam",
            [
                "/usr/local/bin/claude-anyteam",
                "--name",
                "codex-alice",
                "--team",
                "shim-build",
            ],
        )
    ]
    stderr = capsys.readouterr().err.strip()
    assert json.loads(stderr) == {
        "agent_name": "codex-alice",
        "binary": "/usr/local/bin/claude-anyteam",
        "event": "spawn_shim.dispatch",
        "route": "codex",
    }


def test_native_passthrough_for_non_codex_agent(monkeypatch, capsys):
    calls = _record_execv(monkeypatch)
    monkeypatch.delenv("CLAUDE_ANYTEAM_BINARY", raising=False)
    argv = [
        "/usr/local/bin/claude-anyteam-spawn-shim",
        "--agent-name",
        "claude-worker",
        "--team-name",
        "shim-build",
        "--agent-id",
        "agent-123",
    ]
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(
        spawn_shim.shutil,
        "which",
        lambda name: {
            "claude": "/usr/local/bin/claude",
            "claude-anyteam": "/usr/local/bin/claude-anyteam",
        }.get(name),
    )

    assert spawn_shim.main() == 0

    assert calls == [("/usr/local/bin/claude", argv)]
    stderr = capsys.readouterr().err.strip()
    assert json.loads(stderr)["route"] == "native"


def test_no_identity_flags_falls_back_to_native(monkeypatch):
    calls = _record_execv(monkeypatch)
    monkeypatch.delenv("CLAUDE_ANYTEAM_BINARY", raising=False)
    argv = [
        "/usr/local/bin/claude-anyteam-spawn-shim",
        "--plan-mode-required",
        "--agent-id",
        "agent-123",
    ]
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(spawn_shim.shutil, "which", lambda name: f"/opt/bin/{name}")

    assert spawn_shim.main() == 0

    assert calls == [("/opt/bin/claude", argv)]


def test_env_overrides_pattern_and_codex_binary(monkeypatch):
    calls = _record_execv(monkeypatch)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "/usr/local/bin/claude-anyteam-spawn-shim",
            "--agent-name=helper-bob",
            "--team-name=shim-build",
        ],
    )
    monkeypatch.setenv("CLAUDE_ANYTEAM_SHIM_MATCH", r"^helper-")
    monkeypatch.setenv("CLAUDE_ANYTEAM_BINARY", "/custom/bin/codex-launcher")
    monkeypatch.setattr(
        spawn_shim.shutil,
        "which",
        lambda name: {
            "/custom/bin/codex-launcher": "/custom/bin/codex-launcher",
            "claude": "/usr/local/bin/claude",
        }.get(name),
    )

    assert spawn_shim.main() == 0

    assert calls == [
        (
            "/custom/bin/codex-launcher",
            [
                "/custom/bin/codex-launcher",
                "--name",
                "helper-bob",
                "--team",
                "shim-build",
            ],
        )
    ]


def test_env_override_native_claude_binary(monkeypatch):
    calls = _record_execv(monkeypatch)
    monkeypatch.delenv("CLAUDE_ANYTEAM_BINARY", raising=False)
    argv = [
        "/usr/local/bin/claude-anyteam-spawn-shim",
        "--agent-name",
        "alice",
        "--team-name",
        "shim-build",
    ]
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setenv("CLAUDE_ANYTEAM_NATIVE_CLAUDE", "/custom/bin/claude-real")
    monkeypatch.setattr(
        spawn_shim.shutil,
        "which",
        lambda name: {
            "/custom/bin/claude-real": "/custom/bin/claude-real",
            "claude-anyteam": "/usr/local/bin/claude-anyteam",
        }.get(name),
    )

    assert spawn_shim.main() == 0

    assert calls == [("/custom/bin/claude-real", argv)]


def test_plan_mode_flag_is_forwarded(monkeypatch):
    calls = _record_execv(monkeypatch)
    _clear_binary_env(monkeypatch)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "/usr/local/bin/claude-anyteam-spawn-shim",
            "--agent-name",
            "codex-planner",
            "--team-name",
            "shim-build",
            "--plan-mode-required",
        ],
    )
    monkeypatch.setattr(
        spawn_shim.shutil,
        "which",
        lambda name: f"/usr/local/bin/{name}",
    )

    assert spawn_shim.main() == 0

    assert calls == [
        (
            "/usr/local/bin/claude-anyteam",
            [
                "/usr/local/bin/claude-anyteam",
                "--name",
                "codex-planner",
                "--team",
                "shim-build",
                "--plan-mode",
            ],
        )
    ]


def test_unknown_flags_are_stripped_on_codex_route(monkeypatch):
    calls = _record_execv(monkeypatch)
    _clear_binary_env(monkeypatch)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "/usr/local/bin/claude-anyteam-spawn-shim",
            "--agent-name",
            "codex-alice",
            "--team-name",
            "shim-build",
            "--agent-id",
            "agent-123",
            "--parent-session-id",
            "session-456",
            "--teammate-mode",
            "foreground",
            "--unknown-flag",
            "value",
        ],
    )
    monkeypatch.setattr(
        spawn_shim.shutil,
        "which",
        lambda name: f"/usr/local/bin/{name}",
    )

    assert spawn_shim.main() == 0

    assert calls == [
        (
            "/usr/local/bin/claude-anyteam",
            [
                "/usr/local/bin/claude-anyteam",
                "--name",
                "codex-alice",
                "--team",
                "shim-build",
            ],
        )
    ]


def test_invalid_match_regex_raises_system_exit(monkeypatch):
    parsed = spawn_shim.ParsedArgs(agent_name="codex-alice")
    monkeypatch.setenv("CLAUDE_ANYTEAM_SHIM_MATCH", "(")

    with pytest.raises(SystemExit, match="Invalid CLAUDE_ANYTEAM_SHIM_MATCH regex"):
        spawn_shim._codex_route(parsed)


def test_native_resolution_skips_current_shim(monkeypatch):
    monkeypatch.setattr(
        spawn_shim,
        "_resolve_current_invocation",
        lambda argv0: "/shim/bin/claude",
    )
    monkeypatch.setattr(
        spawn_shim.shutil,
        "which",
        lambda name: "/shim/bin/claude" if name == "claude" else None,
    )
    monkeypatch.setattr(spawn_shim.os, "get_exec_path", lambda: ["/shim/bin", "/usr/bin"])
    monkeypatch.setattr(
        spawn_shim.os.path,
        "isfile",
        lambda path: path in {"/shim/bin/claude", "/usr/bin/claude"},
    )
    monkeypatch.setattr(spawn_shim.os, "access", lambda path, mode: True)

    assert spawn_shim._resolve_native_claude("/shim/bin/claude") == "/usr/bin/claude"
