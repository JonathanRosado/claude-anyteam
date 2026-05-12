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


def _clear_route_env(monkeypatch) -> None:
    monkeypatch.delenv("CLAUDE_ANYTEAM_SHIM_MATCH", raising=False)
    monkeypatch.delenv("CODEX_TEAMMATE_SHIM_MATCH", raising=False)
    monkeypatch.delenv("CLAUDE_ANYTEAM_CLAUDE_SHIM_MATCH", raising=False)
    monkeypatch.delenv("CLAUDE_ANYTEAM_GEMINI_SHIM_MATCH", raising=False)
    monkeypatch.delenv("CLAUDE_ANYTEAM_KIMI_SHIM_MATCH", raising=False)


def test_codex_dispatch(monkeypatch, tmp_path, capsys):
    calls = _record_execv(monkeypatch)
    _clear_binary_env(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path))
    _write_agent_config(tmp_path, "shim-build", "codex-alice", {})
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


def test_claude_prefix_passthrough_preserves_argv(monkeypatch, capsys):
    calls = _record_execv(monkeypatch)
    monkeypatch.delenv("CLAUDE_ANYTEAM_BINARY", raising=False)
    _clear_route_env(monkeypatch)
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
    assert json.loads(stderr)["route"] == "claude"


def test_non_matching_agent_native_passthrough_preserves_argv(monkeypatch, capsys):
    calls = _record_execv(monkeypatch)
    _clear_route_env(monkeypatch)
    argv = [
        "/usr/local/bin/claude-anyteam-spawn-shim",
        "--agent-name",
        "research-worker",
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


def test_env_overrides_pattern_and_codex_binary(monkeypatch, tmp_path):
    calls = _record_execv(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path))
    _write_agent_config(tmp_path, "shim-build", "helper-bob", {})
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


def test_native_claude_override_cannot_resolve_to_current_shim(monkeypatch):
    monkeypatch.setenv("CLAUDE_ANYTEAM_NATIVE_CLAUDE", "claude")
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
    monkeypatch.setattr(spawn_shim.os, "get_exec_path", lambda: ["/shim/bin"])
    monkeypatch.setattr(
        spawn_shim.os.path,
        "isfile",
        lambda path: path == "/shim/bin/claude",
    )
    monkeypatch.setattr(spawn_shim.os, "access", lambda path, mode: True)

    assert spawn_shim._resolve_native_claude("/shim/bin/claude") is None


def test_plan_mode_flag_is_forwarded(monkeypatch, tmp_path):
    calls = _record_execv(monkeypatch)
    _clear_binary_env(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path))
    _write_agent_config(tmp_path, "shim-build", "codex-planner", {})
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


def test_unknown_flags_are_stripped_on_codex_route(monkeypatch, tmp_path):
    calls = _record_execv(monkeypatch)
    _clear_binary_env(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path))
    _write_agent_config(tmp_path, "shim-build", "codex-alice", {})
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


# ---- Per-teammate agent config --------------------------------------------


def _write_agent_config(tmp_path, team: str, name: str, body: object) -> None:
    import os as _os

    agents_dir = tmp_path / ".claude" / "teams" / team / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    path = agents_dir / f"{name}.json"
    if isinstance(body, str):
        path.write_text(body)
    else:
        path.write_text(json.dumps(body))
    _os.chmod(path, 0o644)


def _codex_argv_for(monkeypatch, tmp_path, team: str, name: str, capsys):
    calls = _record_execv(monkeypatch)
    _clear_binary_env(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "/usr/local/bin/claude-anyteam-spawn-shim",
            "--agent-name",
            name,
            "--team-name",
            team,
        ],
    )
    monkeypatch.setattr(
        spawn_shim.shutil,
        "which",
        lambda n: {
            "claude-anyteam": "/usr/local/bin/claude-anyteam",
            "claude": "/usr/local/bin/claude",
        }.get(n),
    )

    assert spawn_shim.main() == 0
    return calls, capsys.readouterr().err


def test_agent_config_forwards_model_and_effort(monkeypatch, tmp_path, capsys):
    _write_agent_config(tmp_path, "gemini-build", "codex-alice", {"model": "gpt-5.5", "effort": "xhigh"})
    calls, stderr = _codex_argv_for(monkeypatch, tmp_path, "gemini-build", "codex-alice", capsys)

    _, argv = calls[0]
    assert argv == [
        "/usr/local/bin/claude-anyteam",
        "--name",
        "codex-alice",
        "--team",
        "gemini-build",
        "--model",
        "gpt-5.5",
        "--effort",
        "xhigh",
    ]
    log = json.loads(stderr.strip())
    assert log["agent_config"] == {"model": "gpt-5.5", "effort": "xhigh"}


def test_agent_config_forwards_non_progress_watchdog_flags(monkeypatch, tmp_path, capsys):
    _write_agent_config(
        tmp_path,
        "build",
        "codex-alice",
        {
            "non_progress_warn_s": 180,
            "non_progress_interrupt_s": 420,
            "wrapper_tool_failure_window_s": 120,
        },
    )
    calls, stderr = _codex_argv_for(monkeypatch, tmp_path, "build", "codex-alice", capsys)

    _, argv = calls[0]
    assert argv == [
        "/usr/local/bin/claude-anyteam",
        "--name",
        "codex-alice",
        "--team",
        "build",
        "--non-progress-warn-s",
        "180",
        "--non-progress-interrupt-s",
        "420",
        "--wrapper-tool-failure-window-s",
        "120",
    ]
    log = json.loads(stderr.strip())
    assert log["agent_config"] == {
        "non_progress_warn_s": "180",
        "non_progress_interrupt_s": "420",
        "wrapper_tool_failure_window_s": "120",
    }


def test_agent_config_forwards_model_only(monkeypatch, tmp_path, capsys):
    _write_agent_config(tmp_path, "t", "codex-bob", {"model": "gpt-5.4-mini"})
    calls, _ = _codex_argv_for(monkeypatch, tmp_path, "t", "codex-bob", capsys)
    _, argv = calls[0]
    assert "--model" in argv and "gpt-5.4-mini" in argv
    assert "--effort" not in argv


def test_routed_prefix_without_agent_config_soft_refuses(monkeypatch, tmp_path, capsys):
    calls = _record_execv(monkeypatch)
    _clear_binary_env(monkeypatch)
    monkeypatch.delenv("CLAUDE_ANYTEAM_ALLOW_BARE_PREFIX", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "/usr/local/bin/claude-anyteam-spawn-shim",
            "--agent-name",
            "codex-ghost",
            "--team-name",
            "t",
        ],
    )
    monkeypatch.setattr(
        spawn_shim.shutil,
        "which",
        lambda n: {
            "claude-anyteam": "/usr/local/bin/claude-anyteam",
            "claude": "/usr/local/bin/claude",
        }.get(n),
    )

    assert spawn_shim.main() == 2

    assert calls == []
    log = json.loads(capsys.readouterr().err)
    assert log["event"] == "spawn_shim.bare_prefix_refused"
    assert log["route"] == "codex"
    assert log["agent_name"] == "codex-ghost"
    assert log["team_name"] == "t"
    assert log["error_class"] == "missing_agent_config"
    assert log["config_path"].endswith("/.claude/teams/t/agents/codex-ghost.json")
    assert (
        log["suggested_command"]
        == "claude-anyteam team-agent codex-ghost --team t --model <model> --effort <effort>"
    )
    assert log["override_env"] == "CLAUDE_ANYTEAM_ALLOW_BARE_PREFIX"


def test_bare_prefix_override_allows_dispatch_without_config(monkeypatch, tmp_path, capsys):
    calls = _record_execv(monkeypatch)
    _clear_binary_env(monkeypatch)
    monkeypatch.setenv("CLAUDE_ANYTEAM_ALLOW_BARE_PREFIX", "1")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "/usr/local/bin/claude-anyteam-spawn-shim",
            "--agent-name",
            "codex-ghost",
            "--team-name",
            "t",
        ],
    )
    monkeypatch.setattr(
        spawn_shim.shutil,
        "which",
        lambda n: {
            "claude-anyteam": "/usr/local/bin/claude-anyteam",
            "claude": "/usr/local/bin/claude",
        }.get(n),
    )

    assert spawn_shim.main() == 0

    assert calls == [
        (
            "/usr/local/bin/claude-anyteam",
            ["/usr/local/bin/claude-anyteam", "--name", "codex-ghost", "--team", "t"],
        )
    ]
    assert json.loads(capsys.readouterr().err)["route"] == "codex"


def test_agent_config_malformed_json_is_tolerated(monkeypatch, tmp_path, capsys):
    _write_agent_config(tmp_path, "t", "codex-bad", "{not: valid json")
    calls, stderr = _codex_argv_for(monkeypatch, tmp_path, "t", "codex-bad", capsys)
    _, argv = calls[0]
    assert "--model" not in argv
    assert "--effort" not in argv
    # Error event is logged but spawn still proceeds.
    assert "spawn_shim.agent_config_error" in stderr


def test_agent_config_ignores_unknown_keys(monkeypatch, tmp_path, capsys):
    _write_agent_config(tmp_path, "t", "codex-x", {"model": "gpt-5.5", "poll_s": 2.0, "color": "magenta"})
    calls, _ = _codex_argv_for(monkeypatch, tmp_path, "t", "codex-x", capsys)
    _, argv = calls[0]
    assert "--model" in argv and "gpt-5.5" in argv
    assert "--color" not in argv
    assert "--poll-s" not in argv


def test_agent_config_not_loaded_for_native_route(monkeypatch, tmp_path, capsys):
    # A claude-* name should route native and never look up the agents file —
    # even if one were to exist, it must not influence the argv.
    _write_agent_config(tmp_path, "t", "claude-worker", {"model": "gpt-5.5"})
    calls = _record_execv(monkeypatch)
    _clear_binary_env(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path))
    argv = [
        "/usr/local/bin/claude-anyteam-spawn-shim",
        "--agent-name",
        "claude-worker",
        "--team-name",
        "t",
    ]
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(
        spawn_shim.shutil,
        "which",
        lambda n: {"claude": "/usr/local/bin/claude", "claude-anyteam": "/usr/local/bin/claude-anyteam"}.get(n),
    )

    assert spawn_shim.main() == 0
    # Native pass-through preserves the original argv verbatim.
    _, forwarded = calls[0]
    assert forwarded == argv
    assert "--model" not in forwarded


def test_gemini_dispatch_for_gemini_prefix(monkeypatch, tmp_path, capsys):
    calls = _record_execv(monkeypatch)
    monkeypatch.delenv("CLAUDE_ANYTEAM_GEMINI_BINARY", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    _write_agent_config(tmp_path, "shim-build", "gemini-rhea", {})
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "/usr/local/bin/claude-anyteam-spawn-shim",
            "--agent-name",
            "gemini-rhea",
            "--team-name",
            "shim-build",
        ],
    )
    monkeypatch.setattr(
        spawn_shim.shutil,
        "which",
        lambda name: {
            "gemini-anyteam": "/usr/local/bin/gemini-anyteam",
            "claude": "/usr/local/bin/claude",
        }.get(name),
    )

    assert spawn_shim.main() == 0

    assert calls == [
        (
            "/usr/local/bin/gemini-anyteam",
            [
                "/usr/local/bin/gemini-anyteam",
                "--name",
                "gemini-rhea",
                "--team",
                "shim-build",
            ],
        )
    ]
    assert json.loads(capsys.readouterr().err)["route"] == "gemini"


def test_gemini_dispatch_forwards_model_and_effort(monkeypatch, tmp_path, capsys):
    _write_agent_config(tmp_path, "t", "gemini-pro", {"model": "gemini-2.5-pro", "effort": "xhigh"})
    calls = _record_execv(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_ANYTEAM_GEMINI_BINARY", "/custom/gemini-anyteam")
    monkeypatch.setattr(
        sys,
        "argv",
        ["/shim", "--agent-name", "gemini-pro", "--team-name", "t"],
    )
    monkeypatch.setattr(
        spawn_shim.shutil,
        "which",
        lambda name: {"/custom/gemini-anyteam": "/custom/gemini-anyteam"}.get(name),
    )

    assert spawn_shim.main() == 0
    _, argv = calls[0]
    assert argv == [
        "/custom/gemini-anyteam",
        "--name",
        "gemini-pro",
        "--team",
        "t",
        "--model",
        "gemini-2.5-pro",
        "--effort",
        "xhigh",
    ]


def test_gemini_dispatch_does_not_forward_codex_watchdog_flags(monkeypatch, tmp_path, capsys):
    _write_agent_config(
        tmp_path,
        "t",
        "gemini-pro",
        {
            "non_progress_warn_s": 180,
            "non_progress_interrupt_s": 420,
            "wrapper_tool_failure_window_s": 120,
        },
    )
    calls = _record_execv(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_ANYTEAM_GEMINI_BINARY", "/custom/gemini-anyteam")
    monkeypatch.setattr(
        sys,
        "argv",
        ["/shim", "--agent-name", "gemini-pro", "--team-name", "t"],
    )
    monkeypatch.setattr(
        spawn_shim.shutil,
        "which",
        lambda name: {"/custom/gemini-anyteam": "/custom/gemini-anyteam"}.get(name),
    )

    assert spawn_shim.main() == 0
    _, argv = calls[0]
    assert "--non-progress-warn-s" not in argv
    assert "--non-progress-interrupt-s" not in argv
    assert "--wrapper-tool-failure-window-s" not in argv
    assert json.loads(capsys.readouterr().err)["agent_config"] == {
        "non_progress_warn_s": "180",
        "non_progress_interrupt_s": "420",
        "wrapper_tool_failure_window_s": "120",
    }


def test_kimi_dispatch_for_kimi_prefix(monkeypatch, tmp_path, capsys):
    calls = _record_execv(monkeypatch)
    _clear_route_env(monkeypatch)
    monkeypatch.delenv("CLAUDE_ANYTEAM_KIMI_BINARY", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    _write_agent_config(tmp_path, "shim-build", "kimi-rhea", {})
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "/usr/local/bin/claude-anyteam-spawn-shim",
            "--agent-name",
            "kimi-rhea",
            "--team-name",
            "shim-build",
        ],
    )
    monkeypatch.setattr(
        spawn_shim.shutil,
        "which",
        lambda name: {
            "kimi-anyteam": "/usr/local/bin/kimi-anyteam",
            "claude": "/usr/local/bin/claude",
        }.get(name),
    )

    assert spawn_shim.main() == 0

    assert calls == [
        (
            "/usr/local/bin/kimi-anyteam",
            [
                "/usr/local/bin/kimi-anyteam",
                "--name",
                "kimi-rhea",
                "--team",
                "shim-build",
            ],
        )
    ]
    assert json.loads(capsys.readouterr().err)["route"] == "kimi"


def test_kimi_dispatch_respects_match_env_override(monkeypatch, tmp_path):
    calls = _record_execv(monkeypatch)
    _clear_route_env(monkeypatch)
    monkeypatch.delenv("CLAUDE_ANYTEAM_KIMI_BINARY", raising=False)
    monkeypatch.setenv("CLAUDE_ANYTEAM_KIMI_SHIM_MATCH", r"^moon-")
    monkeypatch.setenv("HOME", str(tmp_path))
    _write_agent_config(tmp_path, "shim-build", "moon-scout", {})
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "/usr/local/bin/claude-anyteam-spawn-shim",
            "--agent-name",
            "moon-scout",
            "--team-name",
            "shim-build",
        ],
    )
    monkeypatch.setattr(
        spawn_shim.shutil,
        "which",
        lambda name: {
            "kimi-anyteam": "/usr/local/bin/kimi-anyteam",
            "claude": "/usr/local/bin/claude",
        }.get(name),
    )

    assert spawn_shim.main() == 0

    assert calls == [
        (
            "/usr/local/bin/kimi-anyteam",
            [
                "/usr/local/bin/kimi-anyteam",
                "--name",
                "moon-scout",
                "--team",
                "shim-build",
            ],
        )
    ]


def test_kimi_dispatch_respects_binary_env_override(monkeypatch, tmp_path):
    calls = _record_execv(monkeypatch)
    _clear_route_env(monkeypatch)
    monkeypatch.setenv("CLAUDE_ANYTEAM_KIMI_BINARY", "/custom/bin/kimi-launcher")
    monkeypatch.setenv("HOME", str(tmp_path))
    _write_agent_config(tmp_path, "shim-build", "kimi-builder", {})
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "/usr/local/bin/claude-anyteam-spawn-shim",
            "--agent-name",
            "kimi-builder",
            "--team-name",
            "shim-build",
        ],
    )
    monkeypatch.setattr(
        spawn_shim.shutil,
        "which",
        lambda name: {
            "/custom/bin/kimi-launcher": "/custom/bin/kimi-launcher",
            "claude": "/usr/local/bin/claude",
        }.get(name),
    )

    assert spawn_shim.main() == 0

    assert calls == [
        (
            "/custom/bin/kimi-launcher",
            [
                "/custom/bin/kimi-launcher",
                "--name",
                "kimi-builder",
                "--team",
                "shim-build",
            ],
        )
    ]


def test_kimi_match_does_not_preempt_codex_or_gemini_routes(monkeypatch, tmp_path):
    calls = _record_execv(monkeypatch)
    _clear_route_env(monkeypatch)
    _clear_binary_env(monkeypatch)
    monkeypatch.delenv("CLAUDE_ANYTEAM_GEMINI_BINARY", raising=False)
    monkeypatch.delenv("CLAUDE_ANYTEAM_KIMI_BINARY", raising=False)
    monkeypatch.setenv("CLAUDE_ANYTEAM_KIMI_SHIM_MATCH", r"^(codex|gemini)-")
    monkeypatch.setenv("HOME", str(tmp_path))
    _write_agent_config(tmp_path, "shim-build", "codex-overlap", {})
    _write_agent_config(tmp_path, "shim-build", "gemini-overlap", {})
    monkeypatch.setattr(
        spawn_shim.shutil,
        "which",
        lambda name: {
            "claude-anyteam": "/usr/local/bin/claude-anyteam",
            "gemini-anyteam": "/usr/local/bin/gemini-anyteam",
            "kimi-anyteam": "/usr/local/bin/kimi-anyteam",
            "claude": "/usr/local/bin/claude",
        }.get(name),
    )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "/usr/local/bin/claude-anyteam-spawn-shim",
            "--agent-name",
            "codex-overlap",
            "--team-name",
            "shim-build",
        ],
    )
    assert spawn_shim.main() == 0

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "/usr/local/bin/claude-anyteam-spawn-shim",
            "--agent-name",
            "gemini-overlap",
            "--team-name",
            "shim-build",
        ],
    )
    assert spawn_shim.main() == 0

    assert calls == [
        (
            "/usr/local/bin/claude-anyteam",
            [
                "/usr/local/bin/claude-anyteam",
                "--name",
                "codex-overlap",
                "--team",
                "shim-build",
            ],
        ),
        (
            "/usr/local/bin/gemini-anyteam",
            [
                "/usr/local/bin/gemini-anyteam",
                "--name",
                "gemini-overlap",
                "--team",
                "shim-build",
            ],
        ),
    ]
