from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from claude_teams import messaging as cs_messaging  # type: ignore[import-untyped]

from claude_anyteam.backends.kimi import invoke


def _capture_argv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> list[str]:
    """Run Kimi adapter with side effects stubbed and return the argv."""
    work = tmp_path / "work"
    home = tmp_path / "home"
    work.mkdir()
    captured: dict[str, Any] = {}
    monkeypatch.setattr(cs_messaging, "TEAMS_DIR", tmp_path / "teams")

    def fake_write_mcp_config(kimi_home: Path, **_kwargs: Any) -> Path:
        assert kimi_home == home
        path = tmp_path / "anyteam-mcp.json"
        path.write_text("{}", encoding="utf-8")
        return path

    def fake_subprocess_run(args: list[str], **_run_kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        return subprocess.CompletedProcess(
            args,
            0,
            stdout='{"role":"assistant","content":"KIMI_OK"}\n',
            stderr="",
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
    return captured["args"]


def _assert_no_explicit_thinking_flag(argv: list[str]) -> None:
    assert "--no-thinking" not in argv
    assert "--thinking" not in argv
    assert not any(token.startswith("--thinking=") for token in argv)


@pytest.mark.parametrize("effort", ["minimal", "low"])
def test_effort_minimal_and_low_disable_thinking(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, effort: str) -> None:
    argv = _capture_argv(tmp_path, monkeypatch, effort=effort)

    assert "--no-thinking" in argv
    assert argv.index("--no-thinking") < argv.index("-p")


@pytest.mark.parametrize("effort", ["medium", "high", "xhigh"])
def test_effort_medium_and_above_use_default_thinking(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, effort: str) -> None:
    argv = _capture_argv(tmp_path, monkeypatch, effort=effort)

    _assert_no_explicit_thinking_flag(argv)


@pytest.mark.parametrize(
    ("thinking", "effort", "expected_no_thinking"),
    [
        ("on", "low", False),
        ("off", "xhigh", True),
    ],
)
def test_explicit_thinking_overrides_effort(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    thinking: str,
    effort: str,
    expected_no_thinking: bool,
) -> None:
    argv = _capture_argv(tmp_path, monkeypatch, thinking=thinking, effort=effort)

    if expected_no_thinking:
        assert "--no-thinking" in argv
    else:
        _assert_no_explicit_thinking_flag(argv)


@pytest.mark.parametrize(
    ("effort", "expected_no_thinking"),
    [
        ("minimal", True),
        ("low", True),
        ("medium", False),
        ("high", False),
        ("xhigh", False),
    ],
)
def test_thinking_auto_resolves_from_effort(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    effort: str,
    expected_no_thinking: bool,
) -> None:
    argv = _capture_argv(tmp_path, monkeypatch, thinking="auto", effort=effort)

    if expected_no_thinking:
        assert "--no-thinking" in argv
    else:
        _assert_no_explicit_thinking_flag(argv)
