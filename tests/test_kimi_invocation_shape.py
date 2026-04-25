"""Assertions on the Kimi headless argv produced by ``kimi.invoke.run``.

These tests are intentionally narrow: they do not parse Kimi transcripts or
exercise MCP config details.  They only guard the deterministic one-shot CLI
shape so future adapter changes do not accidentally make live Kimi calls in
unit tests or move required flags into the prompt text.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from claude_anyteam.backends.kimi import invoke


def _capture_run(tmp_path: Path, monkeypatch, **kwargs: Any) -> tuple[list[str], dict[str, Any]]:
    """Run the adapter with subprocess/config side effects replaced."""
    work = tmp_path / "work"
    home = tmp_path / "home"
    work.mkdir()
    captured: dict[str, Any] = {}

    def fake_write_mcp_config(kimi_home: Path, **_kwargs: Any) -> Path:
        assert kimi_home == home
        path = tmp_path / "anyteam-mcp.json"
        path.write_text("{}", encoding="utf-8")
        return path

    def fake_subprocess_run(args: list[str], **run_kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        captured["kwargs"] = run_kwargs
        return subprocess.CompletedProcess(
            args,
            0,
            stdout='{"role":"assistant","content":"KIMI_OK"}\n',
            stderr="To resume this session: kimi -r fixture-session\n",
        )

    monkeypatch.setattr(invoke, "write_mcp_config", fake_write_mcp_config)
    monkeypatch.setattr(invoke.subprocess, "run", fake_subprocess_run)

    result = invoke.run(
        "hello from tests",
        cwd=work,
        kimi_binary="kimi",
        kimi_home=home,
        wrapper_identity=("team", "kimi-a"),
        **kwargs,
    )

    assert result.exit_code == 0
    assert result.last_message == "KIMI_OK"
    assert result.session_id == "fixture-session"
    return captured["args"], captured["kwargs"]


def _stream_json_flag_value(argv: list[str]) -> str | None:
    if "--output-format" in argv:
        idx = argv.index("--output-format")
        return argv[idx + 1]
    prefix = "--output-format="
    for token in argv:
        if token.startswith(prefix):
            return token[len(prefix) :]
    return None


def _cwd_arg_value(argv: list[str]) -> str | None:
    for flag in ("--work-dir", "-C"):
        if flag in argv:
            return argv[argv.index(flag) + 1]
    return None


def test_fresh_argv_uses_print_stream_json_model_and_prompt(tmp_path, monkeypatch):
    argv, run_kwargs = _capture_run(tmp_path, monkeypatch, model="kimi-k2", thinking="on")

    assert argv[0] == "kimi"
    assert "--print" in argv
    assert _stream_json_flag_value(argv) == "stream-json"
    assert argv[argv.index("--model") + 1] == "kimi-k2"
    assert argv[-2:] == ["-p", "hello from tests"]
    assert argv.index("--model") < argv.index("-p")
    assert run_kwargs["stdin"] is subprocess.DEVNULL


def test_cwd_is_passed_as_work_dir_or_c_flag(tmp_path, monkeypatch):
    argv, run_kwargs = _capture_run(tmp_path, monkeypatch)

    work = tmp_path / "work"
    assert _cwd_arg_value(argv) == str(work)
    assert run_kwargs["cwd"] == str(work)


def test_forced_no_thinking_flag_is_before_prompt(tmp_path, monkeypatch):
    argv, _ = _capture_run(tmp_path, monkeypatch, thinking="off")

    assert "--no-thinking" in argv
    assert argv.index("--no-thinking") < argv.index("-p")


def test_thinking_on_does_not_disable_thinking(tmp_path, monkeypatch):
    argv, _ = _capture_run(tmp_path, monkeypatch, thinking="on")

    assert "--no-thinking" not in argv


def test_prompt_is_positional_tail_so_flags_do_not_leak_into_prompt(tmp_path, monkeypatch):
    argv, _ = _capture_run(tmp_path, monkeypatch, model="kimi-k2", thinking="off")

    prompt_idx = argv.index("-p")
    assert prompt_idx == len(argv) - 2
    assert argv[prompt_idx + 1] == "hello from tests"
    assert all(token not in argv[prompt_idx + 1] for token in ("--print", "--model", "--no-thinking"))
