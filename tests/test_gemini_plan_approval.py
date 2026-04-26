"""Unit tests for the Gemini opt-in plan-approval handler.

Mocks `protocol_io` and Gemini `invoke.run` so the tests exercise adapter
control-flow without invoking Gemini CLI.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from claude_anyteam import codex as codex_mod
from claude_anyteam.backends.gemini import loop as loop_mod
from claude_anyteam.backends.gemini.config import GeminiSettings
from claude_anyteam.backends.gemini.loop import GeminiLoopState, _handle_plan_approval
from claude_anyteam.messages import PlanApprovalRequestIn


def _settings(plan_mode: bool) -> GeminiSettings:
    return GeminiSettings(
        team_name="t",
        agent_name="a",
        cwd=Path("/tmp").resolve(),
        poll_interval_s=0.01,
        color="cyan",
        plan_mode_required=plan_mode,
        gemini_binary="gemini",
        # These unit tests patch `loop_mod.invoke.run` (the headless path)
        # to mock Gemini behavior, so pin backend=headless explicitly.
        # The repo-wide default flipped to "acp" in v0.6.0; without this
        # pin, the tests would route through the ACP path and the
        # invoke-mocks would never be called.
        backend="headless",
    )


def _fake_task(**overrides):
    base = {
        "id": "8",
        "subject": "do plan thing",
        "description": "some description",
        "owner": None,
        "status": "pending",
        "blocked_by": [],
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _inbound(request_id: str = "p1", task_id: str | None = None) -> PlanApprovalRequestIn:
    body: dict[str, object] = {"type": "plan_approval_request", "requestId": request_id}
    if task_id:
        body["taskId"] = task_id
    return PlanApprovalRequestIn.model_validate(body)


def _result(exit_code=0, structured=None):
    return codex_mod.CodexResult(
        exit_code=exit_code,
        structured=structured,
        last_message=json.dumps(structured) if structured else "",
        events=[],
        error=None if exit_code == 0 else f"exit {exit_code}",
    )


# ---- missing request_id -----------------------------------------------------


def test_gemini_plan_missing_request_id_drops():
    state = GeminiLoopState(settings=_settings(plan_mode=True))
    bad = PlanApprovalRequestIn.model_validate({"type": "plan_approval_request"})
    assert bad.request_id is None

    with (
        patch.object(loop_mod.pio, "list_tasks") as lt,
        patch.object(loop_mod.invoke, "run") as gr,
        patch.object(loop_mod.pio, "send_plan_approval_request") as spr,
    ):
        _handle_plan_approval(state, bad)

    assert lt.call_count == 0
    assert gr.call_count == 0
    assert spr.call_count == 0


# ---- no target task ---------------------------------------------------------


def test_gemini_plan_no_target_task_sends_plan_blocked():
    state = GeminiLoopState(settings=_settings(plan_mode=True))
    prose_sent: list[tuple] = []

    with (
        patch.object(loop_mod.pio, "list_tasks", return_value=[]),
        patch.object(
            loop_mod.pio,
            "send_prose_to_lead",
            side_effect=lambda *a, **k: prose_sent.append((a, k)),
        ),
        patch.object(loop_mod.invoke, "run") as gr,
        patch.object(loop_mod.pio, "send_plan_approval_request") as spr,
    ):
        _handle_plan_approval(state, _inbound("p1"))

    assert gr.call_count == 0, "Gemini must not be invoked with no target task"
    assert spr.call_count == 0
    assert len(prose_sent) == 1
    args, kwargs = prose_sent[0]
    assert kwargs["summary"] == "plan_blocked:p1"
    body = json.loads(args[2])
    assert body["kind"] == "plan_blocked"
    assert body["request_id"] == "p1"


# ---- invoke.run raises ------------------------------------------------------


def test_gemini_plan_invoke_crash_retries_then_blocks():
    state = GeminiLoopState(settings=_settings(plan_mode=True))
    tasks = [_fake_task(id="8", owner="a")]
    attempts: list[bool] = []
    block_calls: list[tuple] = []

    def crashing_run(prompt, **_):
        attempts.append("PRIOR ATTEMPT FAILED" in prompt)
        raise RuntimeError("boom")

    with (
        patch.object(loop_mod.pio, "list_tasks", return_value=tasks),
        patch.object(loop_mod.invoke, "run", side_effect=crashing_run),
        patch.object(loop_mod.pio, "send_plan_approval_request") as spr,
        patch.object(loop_mod, "_mark_blocked", side_effect=lambda s, t, reason: block_calls.append((t.id, reason))),
    ):
        _handle_plan_approval(state, _inbound("p1", task_id="8"))

    assert attempts == [False, True], "first attempt normal, retry with tighten=True"
    assert spr.call_count == 0
    assert len(block_calls) == 1
    assert block_calls[0][0] == "8"


# ---- send_plan_approval_request raises --------------------------------------


def test_gemini_plan_send_crash_is_logged_not_raised():
    state = GeminiLoopState(settings=_settings(plan_mode=True))
    tasks = [_fake_task(id="8", owner="a")]
    plan_result = {
        "steps": [{"summary": "write primes.py", "files_touched": ["primes.py"]}],
        "risks": [],
        "estimated_time": "5 minutes",
    }
    block_calls: list[tuple] = []

    with (
        patch.object(loop_mod.pio, "list_tasks", return_value=tasks),
        patch.object(loop_mod.invoke, "run", return_value=_result(0, plan_result)) as gr,
        patch.object(loop_mod.pio, "send_plan_approval_request", side_effect=RuntimeError("send boom")) as spr,
        patch.object(loop_mod, "_mark_blocked", side_effect=lambda s, t, reason: block_calls.append((t.id, reason))),
    ):
        _handle_plan_approval(state, _inbound("p1", task_id="8"))

    assert gr.call_count == 1
    assert spr.call_count == 1
    assert block_calls == []


# ---- retry exhaustion -------------------------------------------------------


def test_gemini_plan_failure_retries_then_blocks():
    state = GeminiLoopState(settings=_settings(plan_mode=True))
    tasks = [_fake_task(id="8", owner="a")]
    attempts: list[bool] = []
    send_plan: list[tuple] = []
    block_calls: list[tuple] = []

    def flaky_run(prompt, **_):
        attempts.append("PRIOR ATTEMPT FAILED" in prompt)
        return _result(exit_code=1, structured=None)

    with (
        patch.object(loop_mod.pio, "list_tasks", return_value=tasks),
        patch.object(loop_mod.invoke, "run", side_effect=flaky_run),
        patch.object(
            loop_mod.pio,
            "send_plan_approval_request",
            side_effect=lambda *a, **k: send_plan.append((a, k)),
        ),
        patch.object(loop_mod, "_mark_blocked", side_effect=lambda s, t, reason: block_calls.append((t.id, reason))),
    ):
        _handle_plan_approval(state, _inbound("p1", task_id="8"))

    assert attempts == [False, True], "first attempt normal, retry with tighten=True"
    assert send_plan == [], "no plan sent on double failure"
    assert len(block_calls) == 1
    assert block_calls[0][0] == "8"
