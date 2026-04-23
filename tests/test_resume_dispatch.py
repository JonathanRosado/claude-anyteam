"""Tests for v7.2's dispatch logic in `_invoke_codex_for_task`.

- First task for a new identity: fresh `codex exec` with `--output-schema`.
- Subsequent tasks: `codex exec resume <session_id>` + Python validation.
- Resume with invalid output: one retry with firmer prompt; second
  failure → `_mark_blocked`.

Mocks `codex_mod.run` so we can assert what argv/params the loop chose
without spawning a real Codex.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from claude_anyteam import codex as codex_mod
from claude_anyteam import loop as loop_mod
from claude_anyteam.config import Settings
from claude_anyteam.loop import LoopState, _invoke_codex_for_task


def _settings(app_server: bool = False) -> Settings:
    return Settings(
        team_name="t",
        agent_name="a",
        cwd=Path("/tmp").resolve(),
        poll_interval_s=0.01,
        color="cyan",
        plan_mode_required=False,
        codex_binary="codex",
        app_server=app_server,
    )


def _task() -> SimpleNamespace:
    return SimpleNamespace(id="42", subject="do X", description="Description of X")


def _valid_response() -> str:
    return '{"files_changed": [], "summary": "done"}'


# ---- first-task: fresh exec --------------------------------------------------


def test_first_task_takes_fresh_exec_branch():
    state = LoopState(settings=_settings(), codex_session_id=None)
    captured_kwargs: dict = {}

    def fake_run(**kwargs):
        captured_kwargs.update(kwargs)
        return codex_mod.CodexResult(
            exit_code=0,
            structured={"files_changed": [], "summary": "done"},
            last_message=_valid_response(),
            events=[],
            session_id="sid-fresh",
        )

    with patch.object(codex_mod, "run", side_effect=fake_run):
        result = _invoke_codex_for_task(state, _task())

    assert result is not None
    assert result.structured == {"files_changed": [], "summary": "done"}
    # Must have passed --output-schema (the SCHEMA constant, not None).
    assert "schema" in captured_kwargs
    assert captured_kwargs["schema"] == codex_mod.TASK_COMPLETE_SCHEMA
    # Must NOT have passed resume_session_id.
    assert captured_kwargs.get("resume_session_id") is None


# ---- resume path: happy --------------------------------------------------


def test_resume_path_validates_and_returns_structured():
    state = LoopState(
        settings=_settings(),
        codex_session_id="sid-carry-forward",
    )

    def fake_run(**kwargs):
        # resume path must pass resume_session_id and NOT pass a schema file.
        assert kwargs.get("resume_session_id") == "sid-carry-forward"
        assert kwargs.get("schema") is None
        return codex_mod.CodexResult(
            exit_code=0,
            structured=None,  # resume path doesn't populate this; Python does
            last_message=_valid_response(),
            events=[],
            session_id=None,
        )

    with patch.object(codex_mod, "run", side_effect=fake_run):
        result = _invoke_codex_for_task(state, _task())

    assert result is not None
    assert result.structured == {"files_changed": [], "summary": "done"}
    # Session id should be preserved through the validation wrapper.
    assert result.session_id == "sid-carry-forward"


# ---- resume path: retry-once-then-block ---------------------------------


def test_resume_retries_on_invalid_json_then_succeeds():
    state = LoopState(settings=_settings(), codex_session_id="sid")
    call_count = {"n": 0}

    def fake_run(**kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # First attempt: bad JSON.
            return codex_mod.CodexResult(
                exit_code=0,
                structured=None,
                last_message="not json at all",
                events=[],
            )
        # Second attempt (firmer prompt): valid.
        prompt = kwargs["prompt"]
        assert "PRIOR ATTEMPT FAILED" in prompt
        return codex_mod.CodexResult(
            exit_code=0,
            structured=None,
            last_message=_valid_response(),
            events=[],
        )

    with patch.object(codex_mod, "run", side_effect=fake_run):
        result = _invoke_codex_for_task(state, _task())

    assert call_count["n"] == 2
    assert result is not None
    assert result.structured == {"files_changed": [], "summary": "done"}


def test_resume_two_failures_marks_blocked_and_returns_none():
    state = LoopState(settings=_settings(), codex_session_id="sid")
    mark_block_calls: list = []

    def fake_run(**kwargs):
        return codex_mod.CodexResult(
            exit_code=0,
            structured=None,
            last_message="still not json",
            events=[],
        )

    with (
        patch.object(codex_mod, "run", side_effect=fake_run),
        patch.object(
            loop_mod,
            "_mark_blocked",
            side_effect=lambda state, task, reason: mark_block_calls.append(reason),
        ),
    ):
        result = _invoke_codex_for_task(state, _task())

    assert result is None
    assert len(mark_block_calls) == 1
    assert "schema after retry" in mark_block_calls[0]


def test_resume_nonzero_exit_on_second_attempt_returns_result_for_caller():
    """If Codex exits nonzero on the retry, don't call _mark_blocked inside
    the dispatcher — return the failing result so the caller's normal
    `result.exit_code != 0` branch fires, keeping failure paths unified."""
    state = LoopState(settings=_settings(), codex_session_id="sid")
    call_count = {"n": 0}

    def fake_run(**kwargs):
        call_count["n"] += 1
        return codex_mod.CodexResult(
            exit_code=0 if call_count["n"] == 1 else 7,
            structured=None,
            last_message="not json",
            events=[],
        )

    mark_block_calls: list = []

    with (
        patch.object(codex_mod, "run", side_effect=fake_run),
        patch.object(
            loop_mod,
            "_mark_blocked",
            side_effect=lambda state, task, reason: mark_block_calls.append(reason),
        ),
    ):
        result = _invoke_codex_for_task(state, _task())

    # Nonzero-exit-on-retry path returns the result to the caller (who
    # will mark blocked via the usual `exit_code != 0` branch in
    # _execute_task).
    assert result is not None
    assert result.exit_code == 7
    assert mark_block_calls == []


# ---- session-id capture forwarding --------------------------------------


def test_fresh_exec_session_id_capture_is_passed_through():
    state = LoopState(settings=_settings(), codex_session_id=None)

    def fake_run(**kwargs):
        return codex_mod.CodexResult(
            exit_code=0,
            structured={"files_changed": [], "summary": "ok"},
            last_message="",
            events=[],
            session_id="019db-first-task",
        )

    with patch.object(codex_mod, "run", side_effect=fake_run):
        result = _invoke_codex_for_task(state, _task())

    assert result.session_id == "019db-first-task"
