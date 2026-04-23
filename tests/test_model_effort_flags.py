"""Task #1: --model / --effort plumbing (adapter → Codex).

Covers three surfaces:

1. Fresh-exec (`codex.run`): `-c model="…"` and `-c model_reasoning_effort="…"`
   appear in argv when Settings-level values are set, and are **absent** when
   they're None (preserving pre-v7.3 behavior and letting Codex fall back to
   `~/.codex/config.toml`).

2. App Server thread/turn JSON-RPC params: `AppServerClient.thread_start`,
   `turn_start`, and `thread_fork` include `model` / `effort` when provided
   and omit them otherwise (ThreadStartParams + TurnStartParams both accept
   these as first-class fields on the 0.122.0 schemas).

3. Config parsing: `from_env` honors CLI overrides, env vars, and rejects
   invalid effort values with a clear ValueError.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from claude_anyteam import codex as codex_mod
from claude_anyteam.app_server import AppServerClient
from claude_anyteam.config import from_env


# ---- fresh-exec path --------------------------------------------------------


class _FakeCompletedProcess:
    stdout = ""
    stderr = ""
    returncode = 0


def _exec_argv(**kwargs) -> list[str]:
    captured: dict = {}

    def fake_run(args, **_):
        captured["args"] = args
        return _FakeCompletedProcess()

    call_kwargs: dict = {
        "prompt": "noop",
        "cwd": Path("/tmp"),
        "schema": None,
        "codex_binary": "codex",
    }
    call_kwargs.update(kwargs)
    with patch.object(codex_mod.subprocess, "run", side_effect=fake_run):
        codex_mod.run(**call_kwargs)
    return captured["args"]


def test_exec_injects_model_override_when_set():
    argv = _exec_argv(model="gpt-5.4")
    assert "-c" in argv
    assert 'model="gpt-5.4"' in argv


def test_exec_injects_effort_override_when_set():
    argv = _exec_argv(effort="xhigh")
    assert 'model_reasoning_effort="xhigh"' in argv


def test_exec_injects_both_when_both_set():
    argv = _exec_argv(model="gpt-5.3-codex", effort="high")
    assert 'model="gpt-5.3-codex"' in argv
    assert 'model_reasoning_effort="high"' in argv


def test_exec_omits_overrides_when_both_unset():
    """Pre-v7.3 baseline: no -c model / no -c model_reasoning_effort — Codex
    inherits defaults from ~/.codex/config.toml."""
    argv = _exec_argv()
    assert 'model=' not in " ".join(argv)
    assert "model_reasoning_effort" not in " ".join(argv)


def test_exec_overrides_precede_prompt():
    argv = _exec_argv(model="gpt-5.4", effort="high")
    prompt_idx = argv.index("noop")
    model_idx = argv.index('model="gpt-5.4"')
    effort_idx = argv.index('model_reasoning_effort="high"')
    assert model_idx < prompt_idx
    assert effort_idx < prompt_idx


def test_exec_overrides_present_on_resume_path_too():
    argv = _exec_argv(resume_session_id="sid-1", model="gpt-5.4", effort="medium")
    assert 'model="gpt-5.4"' in argv
    assert 'model_reasoning_effort="medium"' in argv


# ---- App Server JSON-RPC params --------------------------------------------


def _mk_client_with_mocked_request() -> tuple[AppServerClient, MagicMock]:
    client = AppServerClient(codex_binary="codex")
    mock_request = MagicMock(
        return_value={"thread": {"id": "t-new"}, "threadId": "t-new"}
    )
    client.request = mock_request  # type: ignore[method-assign]
    return client, mock_request


def test_thread_start_passes_model_in_params():
    client, mock_request = _mk_client_with_mocked_request()
    client.thread_start(cwd="/tmp", model="gpt-5.4")
    _, params = mock_request.call_args[0]
    assert params.get("model") == "gpt-5.4"


def test_thread_start_omits_model_when_none():
    client, mock_request = _mk_client_with_mocked_request()
    client.thread_start(cwd="/tmp")
    _, params = mock_request.call_args[0]
    assert "model" not in params


def test_turn_start_passes_model_and_effort_in_params():
    client = AppServerClient(codex_binary="codex")
    mock_request = MagicMock(return_value={"turn": {"id": "turn-1"}})
    client.request = mock_request  # type: ignore[method-assign]

    client.turn_start(
        thread_id="t-1", text="hello", model="gpt-5.3-codex", effort="high"
    )
    _, params = mock_request.call_args[0]
    assert params.get("model") == "gpt-5.3-codex"
    assert params.get("effort") == "high"


def test_turn_start_omits_model_and_effort_when_none():
    client = AppServerClient(codex_binary="codex")
    mock_request = MagicMock(return_value={"turn": {"id": "turn-1"}})
    client.request = mock_request  # type: ignore[method-assign]

    client.turn_start(thread_id="t-1", text="hello")
    _, params = mock_request.call_args[0]
    assert "model" not in params
    assert "effort" not in params


def test_thread_fork_passes_model_in_params():
    client, mock_request = _mk_client_with_mocked_request()
    client.thread_fork(thread_id="parent-1", model="gpt-5.4")
    _, params = mock_request.call_args[0]
    assert params.get("model") == "gpt-5.4"


# ---- from_env plumbing -----------------------------------------------------


def test_from_env_reads_model_and_effort_from_overrides(tmp_path):
    settings = from_env(
        overrides={
            "team_name": "t",
            "agent_name": "a",
            "cwd": str(tmp_path),
            "model": "gpt-5.4",
            "effort": "xhigh",
        }
    )
    assert settings.model == "gpt-5.4"
    assert settings.effort == "xhigh"


def test_from_env_reads_model_and_effort_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_ANYTEAM_TEAM", "t")
    monkeypatch.setenv("CLAUDE_ANYTEAM_NAME", "a")
    monkeypatch.setenv("CLAUDE_ANYTEAM_CWD", str(tmp_path))
    monkeypatch.setenv("CLAUDE_ANYTEAM_MODEL", "gpt-5.3-codex")
    monkeypatch.setenv("CLAUDE_ANYTEAM_EFFORT", "high")
    settings = from_env()
    assert settings.model == "gpt-5.3-codex"
    assert settings.effort == "high"


def test_from_env_defaults_model_and_effort_to_none(tmp_path, monkeypatch):
    for var in ("CLAUDE_ANYTEAM_MODEL", "CLAUDE_ANYTEAM_EFFORT"):
        monkeypatch.delenv(var, raising=False)
    settings = from_env(
        overrides={"team_name": "t", "agent_name": "a", "cwd": str(tmp_path)}
    )
    assert settings.model is None
    assert settings.effort is None


def test_from_env_rejects_invalid_effort(tmp_path):
    with pytest.raises(ValueError, match="effort must be one of"):
        from_env(
            overrides={
                "team_name": "t",
                "agent_name": "a",
                "cwd": str(tmp_path),
                "effort": "bogus",
            }
        )
