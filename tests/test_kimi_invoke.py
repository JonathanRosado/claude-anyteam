"""Coverage for the Kimi stream-json parser and session-id capture.

Asserts that ``claude_anyteam.backends.kimi.invoke`` correctly processes the
empirically captured stream-json fixtures under ``tests/fixtures/kimi/`` and
surfaces the same ``CodexResult`` shape the Kimi loop consumes.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from claude_teams import messaging as cs_messaging  # type: ignore[import-untyped]

from claude_anyteam import protocol_io as pio
from claude_anyteam.codex import TASK_COMPLETE_SCHEMA
from claude_anyteam.backends.kimi import invoke

FIXTURES = Path(__file__).parent / "fixtures" / "kimi"
PROBES = FIXTURES / "_research_probes"


@pytest.fixture
def events_root(tmp_path: Path, monkeypatch):
    base = tmp_path / "home" / ".claude" / "teams"
    monkeypatch.setattr(cs_messaging, "TEAMS_DIR", base)
    return base


def _read(name: str, root: Path = FIXTURES) -> str:
    return (root / name).read_text(encoding="utf-8")


def _patch_kimi_run(
    monkeypatch,
    tmp_path: Path,
    responses: list[dict[str, Any]],
) -> list[tuple[list[str], dict[str, Any]]]:
    """Replace subprocess/config side effects while preserving invoke.run."""
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def fake_write_mcp_config(kimi_home: Path, **_kwargs: Any) -> Path:
        path = tmp_path / "anyteam-mcp.json"
        path.write_text("{}", encoding="utf-8")
        return path

    def fake_subprocess_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((args, kwargs))
        response = responses.pop(0)
        return subprocess.CompletedProcess(
            args,
            response.get("returncode", 0),
            stdout=response.get("stdout", ""),
            stderr=response.get("stderr", ""),
        )

    monkeypatch.setattr(invoke, "write_mcp_config", fake_write_mcp_config)
    monkeypatch.setattr(invoke.subprocess, "run", fake_subprocess_run)
    return calls


def test_feature_test_raises_when_wrapper_binary_missing(monkeypatch):
    calls: list[list[str]] = []

    def fake_which(binary: str) -> str | None:
        if binary == "kimi":
            return "/usr/bin/kimi"
        if binary == "claude-anyteam-wrapper":
            return None
        raise AssertionError(f"unexpected binary probe: {binary}")

    def fake_run(args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(invoke.shutil, "which", fake_which)
    monkeypatch.setattr(invoke.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="claude-anyteam-wrapper not on PATH"):
        invoke.feature_test("kimi")

    assert calls == []


def test_feature_test_does_not_invoke_kimi_print_at_startup(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args == ["kimi", "info"]:
            return subprocess.CompletedProcess(args, 0, stdout="kimi-cli version: 1.39.0\n", stderr="")
        if args == ["kimi", "--help"]:
            assert kwargs.get("env", {}).get("COLUMNS") == "2000"
            return subprocess.CompletedProcess(
                args,
                0,
                stdout="--print --output-format --mcp-config-file --no-thinking",
                stderr="",
            )
        raise AssertionError(f"unexpected subprocess.run call: {args}")

    monkeypatch.setattr(invoke.subprocess, "run", fake_run)
    monkeypatch.setattr(invoke.shutil, "which", lambda b: f"/usr/bin/{b}")

    invoke.feature_test("kimi")

    assert calls == [["kimi", "info"], ["kimi", "--help"]]
    assert not any("--print" in args for args in calls)


def test_feature_test_forces_wide_columns_so_help_flags_render_intact(monkeypatch, tmp_path):
    """Regression: kimi --help truncates `--mcp-config-file` to `--mcp-config-fi…`
    at narrow terminal widths, breaking the substring probe. feature_test must
    pass COLUMNS=2000 (or similar wide value) when invoking --help so every
    flag name renders intact regardless of the parent terminal size.
    """
    seen_envs: list[dict[str, str] | None] = []

    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        # Track --help invocations to inspect the env passed in
        if args[-1] == "--help":
            seen_envs.append(kwargs.get("env"))
            return subprocess.CompletedProcess(
                args,
                0,
                stdout="--print --output-format --mcp-config-file --no-thinking",
                stderr="",
            )
        if args[-1] == "info":
            return subprocess.CompletedProcess(
                args, 0, stdout="kimi-cli version: 1.39.0\n", stderr=""
            )
        raise AssertionError(f"unexpected subprocess.run call: {args}")

    monkeypatch.setattr(invoke.subprocess, "run", fake_run)
    monkeypatch.setattr(invoke.shutil, "which", lambda b: f"/usr/bin/{b}")

    invoke.feature_test("kimi")

    assert seen_envs, "--help was not invoked"
    help_env = seen_envs[0]
    assert help_env is not None, "feature_test must pass an explicit env to --help"
    assert help_env.get("COLUMNS") == "2000", (
        "feature_test must set COLUMNS=2000 so kimi typer/rich help does not "
        "truncate flag names like --mcp-config-file → --mcp-config-fi…"
    )


def test_simple_assistant_text_returns_final_message():
    events, last, tool_calls = invoke._parse_stdout(_read("simple_assistant_text.jsonl"))
    assert last == "KIMI_SIMPLE_OK"
    assert tool_calls == 0
    assert all(ev.get("type") != "non_json_stdout" for ev in events)


def test_tool_call_lifecycle_counts_tools_and_returns_final_text():
    events, last, tool_calls = invoke._parse_stdout(_read("tool_call_lifecycle.jsonl"))
    assert tool_calls == 1
    assert last == "TOOL_SENTINEL=KIMI_SENTINEL_VALUE"

    assistant_event = next(ev for ev in events if ev.get("role") == "assistant" and ev.get("tool_calls"))
    assert invoke._tool_call_name(assistant_event["tool_calls"][0]) == "ReadFile"
    assert any(
        ev.get("role") == "tool" and "KIMI_SENTINEL_VALUE" in invoke._content_text(ev.get("content"))
        for ev in events
    )


def test_mcp_wrapper_tool_call_counts_one_wrapper_call_and_final_text():
    events, last, tool_calls = invoke._parse_stdout(_read("mcp_wrapper_tool_call.jsonl"))
    assert tool_calls == 1
    assert last == "MCP_READ_CONFIG_OK"

    assistant_event = next(ev for ev in events if ev.get("role") == "assistant" and ev.get("tool_calls"))
    assert invoke._tool_call_name(assistant_event["tool_calls"][0]) == "read_config"
    assert any(ev.get("role") == "tool" and "kimi-fixtures" in invoke._content_text(ev.get("content")) for ev in events)


def test_session_id_extracted_from_stderr_resume_hint():
    stderr = "\nTo resume this session: kimi -r 3dc086db-76bf-4188-9b53-8892dacbc654\n"
    assert invoke._extract_session_id(stderr) == "3dc086db-76bf-4188-9b53-8892dacbc654"


def test_session_id_absent_when_stderr_lacks_hint():
    assert invoke._extract_session_id("") is None
    assert invoke._extract_session_id("FastMCP AuthlibDeprecationWarning ...") is None


def test_non_json_stdout_lines_are_recorded_not_fatal():
    stdout = '{"role":"assistant","content":"OK"}\nMax number of steps reached: 1\n'
    events, last, tool_calls = invoke._parse_stdout(stdout)
    assert last == "OK"
    assert any(ev.get("type") == "non_json_stdout" for ev in events)


def test_invalid_tool_call_arguments_are_counted_and_warned(monkeypatch):
    warnings: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(invoke.logger, "warn", lambda msg, **fields: warnings.append((msg, fields)))
    stdout = (
        '{"role":"assistant","tool_calls":[{"type":"function",'
        '"id":"t1","function":{"name":"Shell","arguments":"{\\"command"}}]}\n'
        '{"role":"assistant","content":"ABORTED"}\n'
    )
    events, last, tool_calls = invoke._parse_stdout(stdout)
    # The malformed arguments are not fatal and do not erase the fact that the
    # assistant emitted a tool-call event.
    assert tool_calls == 1
    assert last == "ABORTED"
    assert events[0]["tool_calls"][0]["function"]["name"] == "Shell"
    assert warnings and warnings[0][0] == "kimi.tool_call_arguments_invalid"


def test_max_steps_overflow_fixture_tolerates_terminator_and_bad_arguments(monkeypatch):
    """The probed max_steps_overflow fixture stacks two parser landmines:
    a non-JSON terminator line and a tool-call with truncated JSON arguments.
    """
    warnings: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(invoke.logger, "warn", lambda msg, **fields: warnings.append((msg, fields)))

    events, _last, tool_calls = invoke._parse_stdout(_read("max_steps_overflow.jsonl", PROBES))
    assert any(
        ev.get("type") == "non_json_stdout" and ev.get("line") == "Max number of steps reached: 1"
        for ev in events
    )
    assert tool_calls == 2
    assert any(msg == "kimi.tool_call_arguments_invalid" and fields["tool"] == "Shell" for msg, fields in warnings)


def test_content_text_handles_string_form():
    assert invoke._content_text("OK") == "OK"


def test_content_text_handles_list_with_text_and_thought_parts():
    content = [
        {"type": "think", "think": "internal"},
        {"type": "text", "text": "hello "},
        {"type": "text", "text": "world"},
    ]
    assert invoke._content_text(content) == "hello world"


def test_parse_stdout_extracts_final_assistant_text_from_content_array():
    stdout = "\n".join([
        '{"role":"assistant","content":[{"type":"think","think":"hidden"},{"type":"text","text":"hello "},{"type":"text","text":"world"}]}',
        '{"role":"assistant","content":[{"type":"text","text":"final from array"}]}',
    ])
    _events, last, tool_calls = invoke._parse_stdout(stdout)
    assert last == "final from array"
    assert tool_calls == 0


def test_tool_call_name_reads_function_name_or_top_level():
    assert invoke._tool_call_name({"function": {"name": "Shell"}}) == "Shell"
    assert invoke._tool_call_name({"name": "ReadFile"}) == "ReadFile"
    assert invoke._tool_call_name({"unrelated": True}) is None


def test_session_hint_regex_matches_stderr_suffix_form():
    # Verbatim stderr shape captured in kimi-runtime.md §Real stdout sample
    stderr = "\nTo resume this session: kimi -r 9aaa1bbb-2222-3333-4444-555566667777"
    assert invoke._extract_session_id(stderr) == "9aaa1bbb-2222-3333-4444-555566667777"


def test_run_exit_zero_success_captures_session_id_and_state(tmp_path, monkeypatch):
    calls = _patch_kimi_run(
        monkeypatch,
        tmp_path,
        [{
            "stdout": _read("simple_assistant_text.jsonl"),
            "stderr": "To resume this session: kimi -r 3dc086db-76bf-4188-9b53-8892dacbc654\n",
            "returncode": 0,
        }],
    )
    home = tmp_path / "home"

    result = invoke.run("prompt", cwd=tmp_path, kimi_home=home)

    assert result.exit_code == 0
    assert result.error is None
    assert result.last_message == "KIMI_SIMPLE_OK"
    assert result.session_id == "3dc086db-76bf-4188-9b53-8892dacbc654"
    assert result.tool_call_events == 0
    assert calls[0][1]["stdin"] is subprocess.DEVNULL
    assert '"headless_session_id": "3dc086db-76bf-4188-9b53-8892dacbc654"' in (
        home / ".claude-anyteam" / "state.json"
    ).read_text(encoding="utf-8")


def test_run_exit_one_failure_keeps_parsed_events_and_error(tmp_path, monkeypatch):
    _patch_kimi_run(
        monkeypatch,
        tmp_path,
        [{
            "stdout": _read("max_steps_overflow.jsonl", PROBES),
            "stderr": "",
            "returncode": 1,
        }],
    )

    result = invoke.run("prompt", cwd=tmp_path, kimi_home=tmp_path / "home")

    assert result.exit_code == 1
    assert result.structured is None
    assert result.error is not None
    assert result.error.startswith("kimi exited 1; output:")
    assert result.tool_call_events == 2
    assert any(ev.get("type") == "non_json_stdout" for ev in result.events)


def test_headless_completed_payload_preserves_kimi_tool_calls(events_root, tmp_path, monkeypatch):
    tool_calls = [
        {"type": "function", "id": f"tool_{idx}", "function": {"name": "Shell", "arguments": "{}"}}
        for idx in range(5)
    ]
    stdout = "\n".join([
        json.dumps({"role": "assistant", "content": [], "tool_calls": tool_calls}),
        json.dumps({"role": "assistant", "content": "KIMI_DONE"}),
    ])
    _patch_kimi_run(
        monkeypatch,
        tmp_path,
        [{"stdout": stdout, "stderr": "", "returncode": 0}],
    )

    result = invoke.run(
        "prompt",
        cwd=tmp_path,
        kimi_home=tmp_path / "home",
        wrapper_identity=("team-x", "kimi-a"),
    )

    assert result.exit_code == 0
    assert result.tool_call_events == 5
    events = pio.read_visibility_events("team-x", "kimi-a")
    assert [event.kind for event in events] == ["turn_started", "turn_completed"]
    payload = events[1].payload
    assert payload["tool_call_events"] == 5
    assert payload["tool_call_event_source"] == "kimi assistant.tool_calls[]"
    assert payload["events"][0]["tool_calls"] == tool_calls


def test_headless_prose_send_message_suppresses_terminal_preview(events_root, tmp_path, monkeypatch):
    stdout = "\n".join([
        json.dumps({
            "role": "assistant",
            "content": [],
            "tool_calls": [
                {
                    "type": "function",
                    "id": "tool_send",
                    "function": {"name": "send_message", "arguments": "{}"},
                }
            ],
        }),
        json.dumps({
            "role": "assistant",
            "content": "This final prose would be an M13 collision if the adapter re-sent it.",
        }),
    ])
    _patch_kimi_run(
        monkeypatch,
        tmp_path,
        [{"stdout": stdout, "stderr": "", "returncode": 0}],
    )

    result = invoke.run(
        "prompt",
        cwd=tmp_path,
        kimi_home=tmp_path / "home",
        wrapper_identity=("team-x", "kimi-a"),
    )

    assert result.exit_code == 0
    assert result.last_message.startswith("This final prose")
    events = pio.read_visibility_events("team-x", "kimi-a")
    payload = events[1].payload
    assert payload["tool_call_events"] == 1
    assert payload["last_message_preview"] == ""
    assert payload["last_message_suppressed_reason"] == "delivered_via_send_message_tool"


def test_schema_validation_failure_returns_one_invocation_for_loop_to_retry(tmp_path, monkeypatch):
    """invoke.run() must NOT retry internally on schema failure.

    The loop layer owns retry policy (mirroring Codex's separation of
    concerns). A previous version retried inside invoke.run(), which combined
    with the loop's own attempt pair produced up to 4 Kimi invocations per
    task. After the single-source-retry fix, invoke.run() should return the
    schema-invalid result after exactly one invocation and let the loop's
    second attempt drive the retry with a tightened prompt.
    """
    calls = _patch_kimi_run(
        monkeypatch,
        tmp_path,
        [
            {
                "stdout": _read("schema_invalid_final.jsonl"),
                "stderr": "To resume this session: kimi -r retry-session\n",
                "returncode": 0,
            },
        ],
    )

    result = invoke.run("do work", cwd=tmp_path, schema=TASK_COMPLETE_SCHEMA, kimi_home=tmp_path / "home")

    # Exactly one CLI invocation — no internal retry.
    assert len(calls) == 1
    # exit_code stays 0 (Kimi succeeded), but structured is None and error is
    # set so the loop knows to drive the retry.
    assert result.exit_code == 0
    assert result.structured is None
    assert result.error and "not valid JSON" in result.error
    # session_id captured so the loop's second attempt can pass it through.
    assert result.session_id == "retry-session"
