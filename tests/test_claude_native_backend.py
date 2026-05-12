"""Integration-style coverage for the native Claude headless backend.

The claude_native backend was added to route stress teammates through the
native Claude Code CLI in detached/headless runs.  These tests avoid requiring a
real authenticated ``claude`` binary while locking the spawn/argv shape and the
MCP wrapper config wiring that the S6n stress fix depends on.
"""
from __future__ import annotations

import json
import signal
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from claude_anyteam.backends.claude_native import cli, config, invoke, loop
from claude_anyteam.backends.claude_native.config import ClaudeNativeSettings
from claude_anyteam.capabilities import CLAUDE_NATIVE_HEADLESS_CAPABILITIES
from claude_anyteam.codex import TASK_COMPLETE_SCHEMA, CodexResult
from claude_anyteam.messages import ShutdownRequestIn
from tools.stress import run_scenario


def _settings(tmp_path: Path, **overrides: Any) -> ClaudeNativeSettings:
    values: dict[str, Any] = {
        "team_name": "native-team",
        "agent_name": "claude-a",
        "cwd": tmp_path,
        "poll_interval_s": 0.01,
        "color": "cyan",
        "plan_mode_required": False,
        "claude_binary": "claude",
        "model": "sonnet",
        "effort": None,
        "turn_timeout_s": 1800.0,
    }
    values.update(overrides)
    return ClaudeNativeSettings(**values)


def _task_result(
    *,
    exit_code: int = 0,
    structured: dict[str, Any] | None = None,
    last_message: str = "",
    error: str | None = None,
    tool_call_events: int = 0,
    events: list[dict[str, Any]] | None = None,
    session_id: str | None = None,
) -> CodexResult:
    return CodexResult(
        exit_code=exit_code,
        structured=structured,
        last_message=last_message,
        events=[] if events is None else events,
        error=error,
        tool_call_events=tool_call_events,
        session_id=session_id,
    )


def _patch_claude_invoke_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    responses: list[dict[str, Any] | BaseException],
) -> list[tuple[list[str], dict[str, Any]]]:
    """Replace subprocess/config side effects while preserving invoke.run()."""
    calls: list[tuple[list[str], dict[str, Any]]] = []
    mcp_config = tmp_path / "anyteam-mcp.json"
    mcp_config.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(invoke, "write_mcp_config", lambda *_args, **_kwargs: mcp_config)

    def fake_subprocess_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((args, kwargs))
        response = responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return subprocess.CompletedProcess(
            args,
            response.get("returncode", 0),
            stdout=response.get("stdout", ""),
            stderr=response.get("stderr", ""),
        )

    monkeypatch.setattr(invoke.subprocess, "run", fake_subprocess_run)
    return calls


def test_config_from_env_reads_native_claude_knobs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_ANYTEAM_TEAM", "env-team")
    monkeypatch.setenv("CLAUDE_ANYTEAM_NAME", "env-claude")
    monkeypatch.setenv("CLAUDE_ANYTEAM_CWD", str(tmp_path))
    monkeypatch.setenv("CLAUDE_ANYTEAM_POLL_S", "2.25")
    monkeypatch.setenv("CLAUDE_ANYTEAM_COLOR", "magenta")
    monkeypatch.setenv("CLAUDE_ANYTEAM_PLAN_MODE", "yes")
    monkeypatch.setenv("CLAUDE_ANYTEAM_MODEL", "opus")
    monkeypatch.setenv("CLAUDE_ANYTEAM_CLAUDE_EFFORT", "xhigh")
    monkeypatch.setenv("CLAUDE_ANYTEAM_CLAUDE_TURN_TIMEOUT_S", "1234.5")
    monkeypatch.setenv("CLAUDE_ANYTEAM_NATIVE_CLAUDE", "/opt/bin/claude")

    settings = config.from_env()

    assert settings.team_name == "env-team"
    assert settings.agent_name == "env-claude"
    assert settings.cwd == tmp_path.resolve()
    assert settings.poll_interval_s == 2.25
    assert settings.color == "magenta"
    assert settings.plan_mode_required is True
    assert settings.model == "opus"
    assert settings.effort == "xhigh"
    assert settings.turn_timeout_s == 1234.5
    assert settings.claude_binary == "/opt/bin/claude"


def test_config_rejects_unknown_effort(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Claude effort must be one of"):
        config.from_env(
            {
                "team_name": "t",
                "agent_name": "a",
                "cwd": str(tmp_path),
                "effort": "turbo",
            }
        )


def test_feature_test_raises_when_claude_help_is_missing_required_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(
            args,
            0,
            stdout="--print --verbose --output-format --mcp-config",
            stderr="",
        )

    monkeypatch.setattr(invoke.shutil, "which", lambda binary: f"/usr/bin/{binary}")
    monkeypatch.setattr(invoke.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError) as excinfo:
        invoke.feature_test("claude")

    message = str(excinfo.value)
    assert "Claude CLI is missing required flags" in message
    assert "--strict-mcp-config" in message
    assert calls == [["claude", "--help"]]


def test_cli_main_parses_args_and_calls_loop_with_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, ClaudeNativeSettings] = {}

    def fake_run(settings: ClaudeNativeSettings) -> int:
        captured["settings"] = settings
        return 17

    monkeypatch.setattr(cli, "run", fake_run)

    rc = cli.main(
        [
            "--team",
            "cli-team",
            "--name",
            "claude-cli",
            "--cwd",
            str(tmp_path),
            "--poll-s",
            "0.5",
            "--color",
            "blue",
            "--plan-mode",
            "--claude-binary",
            "/usr/local/bin/claude",
            "--model",
            "sonnet-4.5",
            "--effort",
            "high",
            "--turn-timeout-s",
            "321",
        ]
    )

    assert rc == 17
    settings = captured["settings"]
    assert settings.team_name == "cli-team"
    assert settings.agent_name == "claude-cli"
    assert settings.cwd == tmp_path.resolve()
    assert settings.poll_interval_s == 0.5
    assert settings.color == "blue"
    assert settings.plan_mode_required is True
    assert settings.claude_binary == "/usr/local/bin/claude"
    assert settings.model == "sonnet-4.5"
    assert settings.effort == "high"
    assert settings.turn_timeout_s == 321.0


def test_wrapper_command_args_prefers_absolute_binary_on_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wrapper = tmp_path / "bin" / "claude-anyteam-wrapper"
    wrapper.parent.mkdir()
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setattr(invoke.shutil, "which", lambda name: str(wrapper) if name == "claude-anyteam-wrapper" else None)

    command, prefix_args = invoke._wrapper_command_args()

    assert command == str(wrapper.resolve())
    assert Path(command).is_absolute()
    assert prefix_args == []


def test_write_mcp_config_falls_back_to_module_wrapper_and_identity_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(invoke.shutil, "which", lambda _name: None)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PYTHONPATH", "/repo/src")

    config_path = invoke.write_mcp_config(
        tmp_path / "state-root",
        team="fallback-team",
        agent_name="claude-fallback",
    )

    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert set(data) == {"mcpServers"}
    assert set(data["mcpServers"]) == {"anyteam"}
    server = data["mcpServers"]["anyteam"]
    assert server["command"] == sys.executable
    assert server["args"] == [
        "-m",
        "claude_anyteam.wrapper_server",
        "--team",
        "fallback-team",
        "--name",
        "claude-fallback",
    ]
    assert server["env"]["HOME"] == str(tmp_path / "home")
    assert server["env"]["PYTHONPATH"] == "/repo/src"
    assert server["env"]["CLAUDE_ANYTEAM_TEAM"] == "fallback-team"
    assert server["env"]["CLAUDE_ANYTEAM_NAME"] == "claude-fallback"
    assert server["env"]["CODEX_TEAMMATE_TEAM"] == "fallback-team"
    assert server["env"]["CODEX_TEAMMATE_NAME"] == "claude-fallback"


def test_invoke_run_builds_claude_print_stream_json_argv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    work = tmp_path / "work"
    work.mkdir()
    mcp_config = tmp_path / "anyteam-mcp.json"
    mcp_config.write_text("{}", encoding="utf-8")
    captured: dict[str, Any] = {}

    def fake_write_mcp_config(root: Path, **kwargs: Any) -> Path:
        captured["mcp_root"] = root
        captured["mcp_kwargs"] = kwargs
        return mcp_config

    def fake_subprocess_run(args: list[str], **run_kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        captured["run_kwargs"] = run_kwargs
        stdout = json.dumps(
            {
                "type": "assistant",
                "session_id": "claude-session-1",
                "message": {"content": [{"type": "text", "text": "CLAUDE_OK"}]},
            }
        )
        return subprocess.CompletedProcess(args, 0, stdout=stdout + "\n", stderr="")

    monkeypatch.setattr(invoke, "write_mcp_config", fake_write_mcp_config)
    monkeypatch.setattr(invoke.subprocess, "run", fake_subprocess_run)
    emitted = []

    result = invoke.run(
        "hello from tests",
        cwd=work,
        claude_binary="/bin/claude",
        timeout_s=42.0,
        wrapper_identity=("team-x", "claude-x"),
        resume_session_id="resume-123",
        model="sonnet-4.5",
        effort="xhigh",
        task_id="9",
        event_sink=emitted.append,
    )

    assert result.exit_code == 0
    assert result.last_message == "CLAUDE_OK"
    assert result.session_id == "claude-session-1"
    argv = captured["args"]
    assert argv[:2] == ["/bin/claude", "--print"]
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert argv[argv.index("--mcp-config") + 1] == str(mcp_config)
    assert "--verbose" in argv
    assert "--strict-mcp-config" in argv
    assert "--dangerously-skip-permissions" in argv
    assert argv[argv.index("--add-dir") + 1] == str(work)
    assert argv[argv.index("--model") + 1] == "sonnet-4.5"
    assert argv[argv.index("--effort") + 1] == "xhigh"
    assert argv[argv.index("--resume") + 1] == "resume-123"
    assert argv[-2:] == ["-p", "hello from tests"]
    assert argv.index("--strict-mcp-config") < argv.index("-p")
    assert argv.index("--model") < argv.index("-p")

    run_kwargs = captured["run_kwargs"]
    assert run_kwargs["cwd"] == str(work)
    assert run_kwargs["timeout"] == 42.0
    assert run_kwargs["stdin"] is subprocess.DEVNULL
    assert run_kwargs["env"]["CLAUDE_ANYTEAM_TEAM"] == "team-x"
    assert run_kwargs["env"]["CLAUDE_ANYTEAM_NAME"] == "claude-x"
    assert run_kwargs["env"]["CODEX_TEAMMATE_TEAM"] == "team-x"
    assert run_kwargs["env"]["CODEX_TEAMMATE_NAME"] == "claude-x"
    assert run_kwargs["env"]["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
    assert captured["mcp_kwargs"] == {"team": "team-x", "agent_name": "claude-x"}
    assert [event.kind for event in emitted] == ["turn_started", "turn_completed"]
    assert emitted[0].backend == "claude_native"
    assert emitted[0].payload["timeout_s"] == 42.0
    assert emitted[-1].payload["session_id"] == "claude-session-1"


def test_invoke_run_accepts_native_preamble_before_schema_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    mcp_config = tmp_path / "anyteam-mcp.json"
    mcp_config.write_text("{}", encoding="utf-8")
    final_text = (
        "Task #20 is complete. The implementation and tests were already present.\n\n"
        '{"files_changed":["src/mcp_anyteam_grep.py"],"summary":"Verified all tests pass."}'
    )

    monkeypatch.setattr(invoke, "write_mcp_config", lambda *_args, **_kwargs: mcp_config)

    def fake_subprocess_run(args: list[str], **_run_kwargs: Any) -> subprocess.CompletedProcess[str]:
        stdout = "\n".join(
            [
                json.dumps(
                    {
                        "type": "assistant",
                        "session_id": "claude-session-embedded-json",
                        "message": {"content": [{"type": "text", "text": final_text}]},
                    }
                ),
                json.dumps(
                    {
                        "type": "result",
                        "session_id": "claude-session-embedded-json",
                        "result": final_text,
                    }
                ),
            ]
        )
        return subprocess.CompletedProcess(args, 0, stdout=stdout + "\n", stderr="")

    monkeypatch.setattr(invoke.subprocess, "run", fake_subprocess_run)
    emitted = []

    result = invoke.run(
        "finish task",
        cwd=work,
        schema=TASK_COMPLETE_SCHEMA,
        claude_binary="/bin/claude",
        wrapper_identity=("team-x", "claude-x"),
        event_sink=emitted.append,
    )

    assert result.exit_code == 0
    assert result.error is None
    assert result.structured == {
        "files_changed": ["src/mcp_anyteam_grep.py"],
        "summary": "Verified all tests pass.",
    }
    assert emitted[-1].kind == "turn_completed"
    assert emitted[-1].payload["structured"] is True
    assert "error" not in emitted[-1].payload


def test_invoke_timeout_emits_terminal_turn_failed_visibility(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timeout_stdout = json.dumps(
        {
            "type": "assistant",
            "session_id": "claude-timeout-session",
            "message": {"content": [{"type": "text", "text": "partial work"}]},
        }
    )
    _patch_claude_invoke_run(
        monkeypatch,
        tmp_path,
        [
            subprocess.TimeoutExpired(
                cmd=["claude"],
                timeout=0.25,
                output=timeout_stdout + "\n",
                stderr="still running",
            )
        ],
    )
    emitted = []

    result = invoke.run(
        "slow prompt",
        cwd=tmp_path,
        claude_binary="/bin/claude",
        timeout_s=0.25,
        wrapper_identity=("team-x", "claude-timeout"),
        task_id="44",
        event_sink=emitted.append,
    )

    assert result.exit_code == 124
    assert result.last_message == "partial work"
    assert result.session_id == "claude-timeout-session"
    assert result.error and "claude timed out after 0.25s" in result.error
    assert [event.kind for event in emitted] == ["turn_started", "turn_failed"]
    assert emitted[-1].task_id == "44"
    payload = emitted[-1].payload
    assert payload["exit_code"] == 124
    assert payload["error_class"] == "turn_timeout"
    assert payload["last_message_preview"] == "partial work"
    assert payload["partial_events_available"] is True
    assert payload["session_id"] == "claude-timeout-session"


def test_invoke_nonzero_exit_emits_terminal_turn_failed_visibility(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stdout = json.dumps(
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "failed final text"}]},
        }
    )
    _patch_claude_invoke_run(
        monkeypatch,
        tmp_path,
        [{"stdout": stdout + "\n", "stderr": "boom from cli", "returncode": 7}],
    )
    emitted = []

    result = invoke.run(
        "failing prompt",
        cwd=tmp_path,
        claude_binary="/bin/claude",
        wrapper_identity=("team-x", "claude-nonzero"),
        event_sink=emitted.append,
    )

    assert result.exit_code == 7
    assert result.error == "claude exited 7; output: boom from cli"
    assert [event.kind for event in emitted] == ["turn_started", "turn_failed"]
    payload = emitted[-1].payload
    assert payload["exit_code"] == 7
    assert payload["error_class"] == "process_exit"
    assert payload["last_message_preview"] == "failed final text"
    assert payload["partial_events_available"] is True
    assert payload["tool_call_events"] == 0


def test_invoke_schema_failure_emits_terminal_turn_failed_visibility(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stdout = json.dumps(
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "not json"}]},
        }
    )
    _patch_claude_invoke_run(
        monkeypatch,
        tmp_path,
        [{"stdout": stdout + "\n", "stderr": "", "returncode": 0}],
    )
    emitted = []

    result = invoke.run(
        "schema prompt",
        cwd=tmp_path,
        schema=TASK_COMPLETE_SCHEMA,
        claude_binary="/bin/claude",
        wrapper_identity=("team-x", "claude-schema"),
        event_sink=emitted.append,
    )

    assert result.exit_code == 0
    assert result.structured is None
    assert result.error and "claude final message failed schema validation" in result.error
    assert [event.kind for event in emitted] == ["turn_started", "turn_failed"]
    payload = emitted[-1].payload
    assert payload["exit_code"] == 0
    assert payload["structured"] is False
    assert payload["error_class"] == "schema_validation_failed"
    assert "schema validation" in payload["error"]
    assert payload["last_message_preview"] == "not json"


def test_parse_stdout_synthesizes_anyteam_mcp_tool_events() -> None:
    stdout = json.dumps(
        {
            "type": "assistant",
            "session_id": "session-with-tool",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "mcp__anyteam__send_message",
                        "input": {"to": "team-lead", "kind": "informational", "body": "done"},
                    },
                    {"type": "text", "text": "Delivered via MCP."},
                ]
            },
        }
    )

    events, last_message, tool_calls, session_id = invoke._parse_stdout(stdout)

    assert last_message == "Delivered via MCP."
    assert tool_calls == 1
    assert session_id == "session-with-tool"
    synthetic = next(event for event in events if event.get("source") == "claude_native_mcp")
    assert synthetic["type"] == "tool_use"
    assert synthetic["name"] == "send_message"
    assert synthetic["raw_tool_name"] == "mcp__anyteam__send_message"
    assert synthetic["recipient"] == "team-lead"
    assert synthetic["target"] == "to='team-lead'"
    assert synthetic["kind"] == "informational"
    assert synthetic["input"]["body"] == "done"


def test_loop_run_initializes_registration_manifest_cache_and_deregisters_on_approved_shutdown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path, model="opus", effort="high", claude_binary="/bin/claude")
    calls: dict[str, Any] = {"signals": []}

    class FakeCapabilityManifestCache:
        def __init__(self, team_name: str, *, self_name: str) -> None:
            calls["cache_init"] = (team_name, self_name)
            self.loaded = False

        def load_startup(self) -> None:
            calls["cache_loaded"] = True
            self.loaded = True

    def fake_register(reg_settings: ClaudeNativeSettings, metadata: Any) -> None:
        calls["register_settings"] = reg_settings
        calls["metadata"] = metadata

    def fake_signal(signum: signal.Signals, handler: Any) -> None:
        calls["signals"].append((signum, handler))

    def fake_main_loop(state: loop.ClaudeNativeLoopState) -> None:
        calls["main_state"] = state
        assert state.self_capability_manifest == {"agent_name": "claude-a"}
        assert isinstance(state.peer_manifest_cache, FakeCapabilityManifestCache)
        state.approved_shutdown = True

    monkeypatch.setattr(loop.invoke, "feature_test", lambda binary: calls.setdefault("feature_test", binary))
    monkeypatch.setattr(loop, "register", fake_register)
    monkeypatch.setattr(loop.pio, "read_agent_manifest", lambda team, name: {"agent_name": name})
    monkeypatch.setattr(loop, "CapabilityManifestCache", FakeCapabilityManifestCache)
    monkeypatch.setattr(loop.signal, "signal", fake_signal)
    monkeypatch.setattr(loop, "_main_loop", fake_main_loop)
    monkeypatch.setattr(loop, "deregister", lambda reg_settings: calls.setdefault("deregister", reg_settings))

    rc = loop.run(settings)

    assert rc == 0
    assert calls["feature_test"] == "/bin/claude"
    assert calls["register_settings"] is settings
    metadata = calls["metadata"]
    assert metadata.agent_type == "claude"
    assert metadata.model == "opus"
    assert metadata.backend_type == "claude_native"
    assert metadata.transport == "claude-native-headless"
    assert (
        metadata.host_tool_surface
        == "claude-code-native(Task,Skill,WebFetch,Read,Edit,Write,Bash)+mcp_anyteam"
    )
    assert metadata.coupling_regime == "loose"
    assert metadata.capabilities == CLAUDE_NATIVE_HEADLESS_CAPABILITIES
    assert "headless_invocation" in metadata.capabilities
    assert "structured_output" in metadata.capabilities
    assert "live_tool_events" in metadata.capabilities
    assert "native_skills" in metadata.capabilities
    assert metadata.capability_manifest is not None
    assert "plan_mode" not in metadata.capabilities
    assert "session_resume" not in metadata.capabilities
    assert (
        "Claude Code's Skill/Task tools"
        in metadata.capability_manifest["native_skills"]["when_to_use"]
    )
    assert (
        metadata.capability_manifest["live_tool_events"]["host_tool_surface"]
        == metadata.host_tool_surface
    )
    assert calls["cache_init"] == ("native-team", "claude-a")
    assert calls["cache_loaded"] is True
    assert [item[0] for item in calls["signals"]] == [signal.SIGINT, signal.SIGTERM]
    assert calls["main_state"].approved_shutdown is True
    assert calls["deregister"] is settings


def test_main_loop_polls_inbox_then_executes_claimed_task(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = loop.ClaudeNativeLoopState(settings=_settings(tmp_path))
    claimed_task = SimpleNamespace(id="11", subject="claimed task")
    calls: dict[str, Any] = {"inbox": 0, "closed": False, "executed": []}

    class FakeWatchInbox:
        @classmethod
        def for_team(cls, team_name: str, agent_name: str, fallback_timeout_s: float) -> "FakeWatchInbox":
            calls["watch_args"] = (team_name, agent_name, fallback_timeout_s)
            return cls()

        def wait_for_change(self, _timeout_s: float) -> None:
            raise AssertionError("claimed work should execute immediately without idle waiting")

        def close(self) -> None:
            calls["closed"] = True

    def fake_execute_task(exec_state: loop.ClaudeNativeLoopState, task: Any) -> None:
        calls["executed"].append((exec_state, task))
        exec_state.approved_shutdown = True

    monkeypatch.setattr(loop, "WatchInbox", FakeWatchInbox)
    monkeypatch.setattr(loop.pio, "read_own_inbox", lambda *args: calls.__setitem__("inbox", calls["inbox"] + 1) or [])
    monkeypatch.setattr(loop, "_find_and_claim", lambda claim_state: claimed_task)
    monkeypatch.setattr(loop, "_execute_task", fake_execute_task)

    loop._main_loop(state)

    assert calls["watch_args"] == ("native-team", "claude-a", 0.01)
    assert calls["inbox"] == 1
    assert calls["executed"] == [(state, claimed_task)]
    assert calls["closed"] is True


def test_execute_task_success_completes_task_and_sends_lifecycle_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = loop.ClaudeNativeLoopState(settings=_settings(tmp_path, effort="high"))
    task = SimpleNamespace(id="21", subject="ship it", description="finish the work")
    state.in_flight_task = task.id
    invocations: list[dict[str, Any]] = []
    task_updates: list[tuple[str, str, dict[str, Any]]] = []
    completions: list[dict[str, Any]] = []

    def fake_run(prompt: str, **kwargs: Any) -> CodexResult:
        invocations.append({"prompt": prompt, **kwargs})
        return _task_result(
            structured={"files_changed": ["tests/test_claude_native_backend.py"], "summary": "Added coverage."},
            last_message='{"files_changed":["tests/test_claude_native_backend.py"],"summary":"Added coverage."}',
            session_id="native-session-success",
        )

    monkeypatch.setattr(loop.invoke, "run", fake_run)
    monkeypatch.setattr(
        loop.pio,
        "update_task",
        lambda team, task_id, **kwargs: task_updates.append((team, task_id, kwargs)),
    )
    monkeypatch.setattr(
        loop.pio,
        "send_task_complete",
        lambda team, sender, **kwargs: completions.append({"team": team, "sender": sender, **kwargs}),
    )

    loop._execute_task(state, task)

    assert state.in_flight_task is None
    assert state.claude_session_id == "native-session-success"
    assert len(invocations) == 1
    call = invocations[0]
    assert "ship it" in call["prompt"]
    assert call["schema"] == TASK_COMPLETE_SCHEMA
    assert call["resume_session_id"] is None
    assert call["wrapper_identity"] == ("native-team", "claude-a")
    assert call["model"] == "sonnet"
    assert call["effort"] == "high"
    assert call["task_id"] == "21"
    assert task_updates == [("native-team", "21", {"status": "completed"})]
    assert completions == [
        {
            "team": "native-team",
            "sender": "claude-a",
            "task_id": "21",
            "files_changed": ["tests/test_claude_native_backend.py"],
            "summary_text": "Added coverage.",
            "codex_exit_code": 0,
        }
    ]


def test_execute_task_blocks_after_schema_validation_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = loop.ClaudeNativeLoopState(settings=_settings(tmp_path))
    task = SimpleNamespace(id="22", subject="bad json", description="return invalid output")
    state.in_flight_task = task.id
    invocations: list[str] = []
    task_updates: list[tuple[str, str, dict[str, Any]]] = []
    blocked_messages: list[tuple[str, str, str, str]] = []
    validation_error = "claude final message failed schema validation: output was not valid JSON"

    def fake_run(prompt: str, **_kwargs: Any) -> CodexResult:
        invocations.append(prompt)
        return _task_result(
            exit_code=0,
            structured=None,
            last_message="not json",
            error=validation_error,
        )

    monkeypatch.setattr(loop.invoke, "run", fake_run)
    monkeypatch.setattr(loop.pio, "get_task", lambda _team, _task_id: SimpleNamespace(status="in_progress"))
    monkeypatch.setattr(
        loop.pio,
        "update_task",
        lambda team, task_id, **kwargs: task_updates.append((team, task_id, kwargs)),
    )
    monkeypatch.setattr(
        loop.pio,
        "send_task_blocked",
        lambda team, sender, task_id, reason: blocked_messages.append((team, sender, task_id, reason)),
    )
    monkeypatch.setattr(
        loop.pio,
        "send_task_complete",
        lambda *_args, **_kwargs: pytest.fail("schema-invalid task must not complete"),
    )

    loop._execute_task(state, task)

    assert state.in_flight_task is None
    assert len(invocations) == 2
    assert "PRIOR ATTEMPT FAILED" not in invocations[0]
    assert "PRIOR ATTEMPT FAILED" in invocations[1]
    assert len(task_updates) == 1
    team, task_id, update_kwargs = task_updates[0]
    assert (team, task_id) == ("native-team", "22")
    assert update_kwargs["active_form"].startswith("blocked: claude final message failed schema validation")
    assert update_kwargs["metadata"] == {
        "blocked_reason": validation_error,
        "blocked_by": "claude-a",
    }
    assert blocked_messages == [("native-team", "claude-a", "22", validation_error)]


def test_execute_task_retries_transient_nonzero_then_completes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = loop.ClaudeNativeLoopState(settings=_settings(tmp_path))
    task = SimpleNamespace(id="23", subject="retry task", description="first attempt fails transiently")
    state.in_flight_task = task.id
    responses = [
        _task_result(exit_code=1, structured=None, last_message="", error="temporary Claude CLI failure"),
        _task_result(
            structured={"files_changed": [], "summary": "Recovered on retry."},
            last_message='{"files_changed":[],"summary":"Recovered on retry."}',
            session_id="native-session-retry",
        ),
    ]
    prompts_seen: list[str] = []
    task_updates: list[dict[str, Any]] = []
    completions: list[dict[str, Any]] = []

    def fake_run(prompt: str, **_kwargs: Any) -> CodexResult:
        prompts_seen.append(prompt)
        return responses.pop(0)

    monkeypatch.setattr(loop.invoke, "run", fake_run)
    monkeypatch.setattr(loop, "_mark_blocked", lambda *_args, **_kwargs: pytest.fail("retry success must not block"))
    monkeypatch.setattr(loop.pio, "update_task", lambda _team, _task_id, **kwargs: task_updates.append(kwargs))
    monkeypatch.setattr(
        loop.pio,
        "send_task_complete",
        lambda team, sender, **kwargs: completions.append({"team": team, "sender": sender, **kwargs}),
    )

    loop._execute_task(state, task)

    assert state.in_flight_task is None
    assert state.claude_session_id == "native-session-retry"
    assert len(prompts_seen) == 2
    assert "PRIOR ATTEMPT FAILED" in prompts_seen[1]
    assert task_updates == [{"status": "completed"}]
    assert completions[0]["task_id"] == "23"
    assert completions[0]["summary_text"] == "Recovered on retry."


def test_handle_shutdown_approves_clean_shutdown_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = loop.ClaudeNativeLoopState(settings=_settings(tmp_path), claude_session_id="session-to-drop")
    approvals: list[tuple[str, str, str]] = []

    monkeypatch.setattr(
        loop.pio,
        "send_shutdown_approved",
        lambda team, sender, request_id: approvals.append((team, sender, request_id)),
    )
    monkeypatch.setattr(
        loop.pio,
        "send_shutdown_rejected",
        lambda *_args, **_kwargs: pytest.fail("clean shutdown must not be rejected"),
    )

    payload = ShutdownRequestIn(request_id="req-clean")
    loop._handle_shutdown(state, payload)
    loop._handle_shutdown(state, payload)

    assert approvals == [("native-team", "claude-a", "req-clean")]
    assert state.approved_shutdown is True
    assert state.shutdown_requested is False
    assert state.claude_session_id is None


def test_handle_shutdown_rejects_when_task_in_flight_and_defers_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = loop.ClaudeNativeLoopState(settings=_settings(tmp_path), in_flight_task="55")
    rejections: list[tuple[str, str, str, str]] = []

    monkeypatch.setattr(
        loop.pio,
        "send_shutdown_approved",
        lambda *_args, **_kwargs: pytest.fail("in-flight shutdown must not be approved"),
    )
    monkeypatch.setattr(
        loop.pio,
        "send_shutdown_rejected",
        lambda team, sender, request_id, reason: rejections.append((team, sender, request_id, reason)),
    )

    payload = ShutdownRequestIn(request_id="req-busy")
    loop._handle_shutdown(state, payload)
    loop._handle_shutdown(state, payload)

    assert rejections == [("native-team", "claude-a", "req-busy", "in-flight task #55")]
    assert state.approved_shutdown is False
    assert state.shutdown_requested is True
    assert state.in_flight_task == "55"


def test_handle_prose_skips_fallback_when_send_message_tool_delivered_reply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = loop.ClaudeNativeLoopState(settings=_settings(tmp_path))
    msg = SimpleNamespace(from_="codex-peer", text="please ack", summary="peer dm")
    invocations: list[dict[str, Any]] = []

    def fake_run(prompt: str, **kwargs: Any) -> CodexResult:
        invocations.append({"prompt": prompt, **kwargs})
        return _task_result(
            last_message="Already sent via tool.",
            tool_call_events=1,
            events=[{"type": "tool_use", "name": "mcp__anyteam__send_message"}],
        )

    monkeypatch.setattr(loop.invoke, "run", fake_run)
    send_prose_calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(loop.pio, "send_prose", lambda *args, **_kwargs: send_prose_calls.append(args))

    loop._handle_prose(state, msg)

    assert send_prose_calls == []
    assert len(invocations) == 1
    assert invocations[0]["schema"] is None
    assert invocations[0]["wrapper_identity"] == ("native-team", "claude-a")
    assert callable(invocations[0]["event_sink"])


def test_handle_prose_sends_fallback_when_reply_was_plain_prose_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = loop.ClaudeNativeLoopState(settings=_settings(tmp_path))
    msg = SimpleNamespace(from_="codex-peer", text="plain reply is fine", summary="peer dm")

    monkeypatch.setattr(
        loop.invoke,
        "run",
        lambda *_args, **_kwargs: _task_result(last_message="Plain prose reply.", events=[], tool_call_events=0),
    )
    send_prose_calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(loop.pio, "send_prose", lambda *args, **_kwargs: send_prose_calls.append(args))

    loop._handle_prose(state, msg)

    assert send_prose_calls == [("native-team", "claude-a", "codex-peer", "Plain prose reply.")]


def test_stress_spawn_command_routes_claude_members_to_native_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(run_scenario, "_resolve_backend_binary", lambda name: f"/usr/bin/{name}")

    cmd = run_scenario._command_for_member(
        {
            "name": "claude-pair-a",
            "agent_type": "claude",
            "model": "sonnet",
            "effort": "xhigh",
            "turn_timeout_s": 777,
        },
        "stress-S6n-test",
        tmp_path,
    )

    assert cmd is not None
    assert cmd[:3] == [sys.executable, "-m", "claude_anyteam.backends.claude_native.cli"]
    assert cmd[cmd.index("--team") + 1] == "stress-S6n-test"
    assert cmd[cmd.index("--name") + 1] == "claude-pair-a"
    assert cmd[cmd.index("--cwd") + 1] == str(tmp_path / "repo")
    assert cmd[cmd.index("--claude-binary") + 1] == "/usr/bin/claude"
    assert cmd[cmd.index("--model") + 1] == "sonnet"
    assert cmd[cmd.index("--effort") + 1] == "xhigh"
    assert cmd[cmd.index("--turn-timeout-s") + 1] == "777"
    assert "--agent-id" not in cmd
    assert "--agent-name" not in cmd
    assert "--team-name" not in cmd
    assert "--print" not in cmd
