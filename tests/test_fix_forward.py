"""Regression tests for the two fix-forward bugs reviewer surfaced from
their task #5 plan-mode probe:

1. `codex.py::run` now passes `stdin=subprocess.DEVNULL`. Without it,
   plan-mode retries were stalling on "Reading additional input from
   stdin..." on codex-cli 0.120.0.
2. `loop.py::_mark_blocked` now checks current task status before
   mutating. On task #13 the plan-mode retry succeeded *after* the
   initial failure was scheduled but before it had written, producing
   `status: completed` AND `metadata.blocked_reason: "plan generation
   failed twice"`. The race-check skips the mutation when the task has
   already transitioned to `completed`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from claude_anyteam import codex as codex_mod
from claude_anyteam import loop as loop_mod
from claude_anyteam.config import Settings


# ---- fix-forward #1: stdin DEVNULL ----------------------------------------


class _FakeCompletedProcess:
    def __init__(self) -> None:
        self.stdout = ""
        self.stderr = ""
        self.returncode = 0


def test_run_passes_stdin_devnull():
    captured: dict = {}

    def fake_run(args, **kwargs):
        captured.update(kwargs)
        return _FakeCompletedProcess()

    with patch.object(codex_mod.subprocess, "run", side_effect=fake_run):
        codex_mod.run(prompt="noop", cwd=Path("/tmp"), schema=None)

    assert captured.get("stdin") == subprocess.DEVNULL, (
        "codex.run must pass stdin=subprocess.DEVNULL. Without it, plan-mode "
        "retries stall on 'Reading additional input from stdin...' per the "
        "task #5 probe finding."
    )


# ---- fix-forward #2: _mark_blocked race-check ------------------------------


def _settings() -> Settings:
    return Settings(
        team_name="t",
        agent_name="a",
        cwd=Path("/tmp").resolve(),
        poll_interval_s=0.01,
        color="cyan",
        plan_mode_required=False,
        codex_binary="codex",
    )


def test_mark_blocked_skips_when_task_already_completed():
    """If another codepath raced to `completed` first, don't trample it."""
    state = loop_mod.LoopState(settings=_settings())
    task = SimpleNamespace(id="42", status="in_progress")
    completed_on_disk = SimpleNamespace(id="42", status="completed")

    update_calls: list = []
    send_calls: list = []

    with (
        patch.object(loop_mod.pio, "get_task", return_value=completed_on_disk),
        patch.object(loop_mod.pio, "update_task", side_effect=lambda *a, **k: update_calls.append((a, k))),
        patch.object(loop_mod.pio, "send_task_blocked", side_effect=lambda *a, **k: send_calls.append((a, k))),
    ):
        loop_mod._mark_blocked(state, task, reason="test reason")

    assert update_calls == [], "must not update a completed task"
    assert send_calls == [], "must not emit a blocked message for a completed task"


def test_mark_blocked_proceeds_when_task_still_in_progress():
    """Normal path: task is still in_progress on re-read, so the block
    annotation + message both fire."""
    state = loop_mod.LoopState(settings=_settings())
    task = SimpleNamespace(id="42", status="in_progress")
    still_in_progress = SimpleNamespace(id="42", status="in_progress")

    update_calls: list = []
    send_calls: list = []

    with (
        patch.object(loop_mod.pio, "get_task", return_value=still_in_progress),
        patch.object(loop_mod.pio, "update_task", side_effect=lambda *a, **k: update_calls.append((a, k))),
        patch.object(loop_mod.pio, "send_task_blocked", side_effect=lambda *a, **k: send_calls.append((a, k))),
    ):
        loop_mod._mark_blocked(state, task, reason="test reason")

    assert len(update_calls) == 1
    assert len(send_calls) == 1


def test_mark_blocked_proceeds_when_precheck_raises():
    """Precheck is defensive, not load-bearing. If `get_task` itself
    raises (FS race, missing file, etc.) we should still try to write
    the block annotation — silent abandonment would hide real failures."""
    state = loop_mod.LoopState(settings=_settings())
    task = SimpleNamespace(id="42", status="in_progress")

    update_calls: list = []
    send_calls: list = []

    with (
        patch.object(loop_mod.pio, "get_task", side_effect=FileNotFoundError("missing")),
        patch.object(loop_mod.pio, "update_task", side_effect=lambda *a, **k: update_calls.append((a, k))),
        patch.object(loop_mod.pio, "send_task_blocked", side_effect=lambda *a, **k: send_calls.append((a, k))),
    ):
        loop_mod._mark_blocked(state, task, reason="test reason")

    assert len(update_calls) == 1
    assert len(send_calls) == 1
