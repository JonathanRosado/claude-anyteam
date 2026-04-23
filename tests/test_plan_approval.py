"""Unit tests for the opt-in plan-approval handler.

Mocks `protocol_io` and `codex_mod.run` so we exercise the decision logic
without invoking real Codex. Live end-to-end is the responsibility of
reviewer's §9 #7 probe (plan_probe.py).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from claude_anyteam import codex as codex_mod
from claude_anyteam import loop as loop_mod
from claude_anyteam.config import Settings
from claude_anyteam.loop import LoopState, _handle_plan_approval
from claude_anyteam.messages import PlanApprovalRequestIn


def _settings(plan_mode: bool) -> Settings:
    return Settings(
        team_name="t",
        agent_name="a",
        cwd=Path("/tmp").resolve(),
        poll_interval_s=0.01,
        color="cyan",
        plan_mode_required=plan_mode,
        codex_binary="codex",
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


# ---- default policy: plan-mode off ------------------------------------------


def test_plan_default_policy_drops_and_logs():
    state = LoopState(settings=_settings(plan_mode=False))
    with (
        patch.object(loop_mod.pio, "list_tasks") as lt,
        patch.object(loop_mod.pio, "send_plan_approval_request") as spr,
        patch.object(loop_mod.codex_mod, "run") as cr,
    ):
        _handle_plan_approval(state, _inbound("p1"))
    assert lt.call_count == 0
    assert spr.call_count == 0
    assert cr.call_count == 0


# ---- happy path: plan generated and sent ------------------------------------


def test_plan_success_path_sends_plan():
    state = LoopState(settings=_settings(plan_mode=True))
    tasks = [_fake_task(id="8", owner="a")]
    plan_result = {
        "steps": [{"summary": "write primes.py", "files_touched": ["primes.py"]}],
        "risks": [],
        "estimated_time": "5 minutes",
    }

    sent: list[tuple] = []

    def fake_send(team, sender, *, request_id, plan):
        sent.append((team, sender, request_id, plan))

    with (
        patch.object(loop_mod.pio, "list_tasks", return_value=tasks),
        patch.object(loop_mod.codex_mod, "run", return_value=_result(0, plan_result)) as cr,
        patch.object(loop_mod.pio, "send_plan_approval_request", side_effect=fake_send),
    ):
        _handle_plan_approval(state, _inbound("p1", task_id="8"))

    assert cr.call_count == 1
    assert len(sent) == 1
    team, sender, req_id, plan = sent[0]
    assert team == "t"
    assert sender == "a"
    assert req_id == "p1"
    assert plan == plan_result


# ---- retry-once-then-block ---------------------------------------------------


def test_plan_failure_retries_then_blocks():
    state = LoopState(settings=_settings(plan_mode=True))
    tasks = [_fake_task(id="8", owner="a")]
    attempts: list[bool] = []  # record `tighten` per call

    def flaky_run(prompt, *, cwd, schema, codex_binary, **_):
        attempts.append("PRIOR ATTEMPT FAILED" in prompt)
        return _result(exit_code=1, structured=None)

    send_plan: list[tuple] = []
    block_calls: list[tuple] = []

    with (
        patch.object(loop_mod.pio, "list_tasks", return_value=tasks),
        patch.object(loop_mod.codex_mod, "run", side_effect=flaky_run),
        patch.object(
            loop_mod.pio, "send_plan_approval_request",
            side_effect=lambda *a, **k: send_plan.append((a, k)),
        ),
        patch.object(loop_mod, "_mark_blocked", side_effect=lambda s, t, reason: block_calls.append((t.id, reason))),
    ):
        _handle_plan_approval(state, _inbound("p1", task_id="8"))

    assert attempts == [False, True], "first attempt normal, retry with tighten=True"
    assert send_plan == [], "no plan sent on double failure"
    assert len(block_calls) == 1
    assert block_calls[0][0] == "8"


# ---- no target task ---------------------------------------------------------


def test_plan_no_target_task_sends_plan_blocked():
    state = LoopState(settings=_settings(plan_mode=True))
    tasks: list = []  # nothing to plan against
    prose_sent: list[tuple] = []

    with (
        patch.object(loop_mod.pio, "list_tasks", return_value=tasks),
        patch.object(
            loop_mod.pio, "send_prose_to_lead",
            side_effect=lambda *a, **k: prose_sent.append((a, k)),
        ),
        patch.object(loop_mod.codex_mod, "run") as cr,
        patch.object(loop_mod.pio, "send_plan_approval_request") as spr,
    ):
        _handle_plan_approval(state, _inbound("p1"))

    assert cr.call_count == 0, "Codex must not be invoked with no target task"
    assert spr.call_count == 0
    assert len(prose_sent) == 1
    args, kwargs = prose_sent[0]
    assert kwargs["summary"] == "plan_blocked:p1"
    body = json.loads(args[2])
    assert body["kind"] == "plan_blocked"
    assert body["request_id"] == "p1"


# ---- missing request_id -----------------------------------------------------


def test_plan_missing_request_id_drops():
    state = LoopState(settings=_settings(plan_mode=True))
    # request_id is required on PlanApprovalRequestIn (optional None), so
    # construct one with request_id explicitly None.
    bad = PlanApprovalRequestIn.model_validate({"type": "plan_approval_request"})
    assert bad.request_id is None

    with (
        patch.object(loop_mod.pio, "list_tasks") as lt,
        patch.object(loop_mod.codex_mod, "run") as cr,
        patch.object(loop_mod.pio, "send_plan_approval_request") as spr,
    ):
        _handle_plan_approval(state, bad)

    assert lt.call_count == 0
    assert cr.call_count == 0
    assert spr.call_count == 0


# ---- target resolution -------------------------------------------------------


def test_plan_prefers_explicit_task_id_over_claimable():
    state = LoopState(settings=_settings(plan_mode=True))
    tasks = [
        _fake_task(id="5", owner="a", status="pending"),  # would be fallback
        _fake_task(id="8", owner=None, status="pending"),  # explicit target
    ]
    captured: list = []

    def capture_run(prompt, **_):
        captured.append(prompt)
        return _result(0, {"steps": [{"summary": "s"}], "risks": []})

    with (
        patch.object(loop_mod.pio, "list_tasks", return_value=tasks),
        patch.object(loop_mod.codex_mod, "run", side_effect=capture_run),
        patch.object(loop_mod.pio, "send_plan_approval_request"),
    ):
        _handle_plan_approval(state, _inbound("p1", task_id="8"))

    assert len(captured) == 1
    assert "task #8" in captured[0], "must plan against the explicit task_id, not the fallback"


def test_plan_fallback_uses_in_flight_task():
    state = LoopState(settings=_settings(plan_mode=True), in_flight_task="3")
    tasks = [
        _fake_task(id="3", owner="a", status="in_progress"),
        _fake_task(id="5", owner=None, status="pending"),
    ]
    captured: list = []

    def capture_run(prompt, **_):
        captured.append(prompt)
        return _result(0, {"steps": [{"summary": "s"}], "risks": []})

    with (
        patch.object(loop_mod.pio, "list_tasks", return_value=tasks),
        patch.object(loop_mod.codex_mod, "run", side_effect=capture_run),
        patch.object(loop_mod.pio, "send_plan_approval_request"),
    ):
        _handle_plan_approval(state, _inbound("p1"))  # no taskId in payload

    assert len(captured) == 1
    assert "task #3" in captured[0], "in-flight task should be preferred when no explicit task_id"
