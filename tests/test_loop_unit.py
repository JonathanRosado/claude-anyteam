"""Unit tests for the control loop dispatch.

These bypass cs50victor by mocking `protocol_io` and `codex` modules so the
loop's decision logic is exercised in isolation. Registration, Codex
invocation, and real FS I/O are covered by their own tests.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from claude_anyteam import loop as loop_mod
from claude_anyteam.config import Settings
from claude_anyteam.loop import LoopState, _find_and_claim, _handle_message


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


@dataclass
class FakeInboxMessage:
    text: str
    from_: str = "team-lead"
    summary: str | None = None
    timestamp: str = "2026-04-21T00:00:00.000Z"


@dataclass
class FakeTask:
    id: str
    subject: str = "do thing"
    description: str = "the thing"
    status: str = "pending"
    owner: str | None = None
    blocked_by: list[str] = field(default_factory=list)


# ---- _handle_message / shutdown ----------------------------------------------


def test_shutdown_while_idle_approves_and_exits():
    state = LoopState(settings=_settings())
    msg = FakeInboxMessage(
        text=json.dumps({"type": "shutdown_request", "request_id": "r1"}),
    )
    sent: list[tuple] = []
    with patch.object(
        loop_mod.pio,
        "send_shutdown_response",
        side_effect=lambda *a, **k: sent.append((a, k)),
    ):
        _handle_message(state, msg)
    assert state.approved_shutdown is True
    assert state.shutdown_requested is False
    assert len(sent) == 1
    # (team, agent, req_id, approve=True)
    args, kwargs = sent[0]
    assert args[2] == "r1"
    assert kwargs["approve"] is True


def test_shutdown_while_idle_clears_lineage_state():
    state = LoopState(
        settings=_settings(),
        codex_session_id="sid-1",
        app_server_last_thread_id="thread-1",
    )
    msg = FakeInboxMessage(
        text=json.dumps({"type": "shutdown_request", "request_id": "r1b"}),
    )

    with patch.object(loop_mod.pio, "send_shutdown_response"):
        _handle_message(state, msg)

    assert state.approved_shutdown is True
    assert state.codex_session_id is None
    assert state.app_server_last_thread_id is None


def test_shutdown_while_mid_task_rejects_with_feedback():
    state = LoopState(settings=_settings(), in_flight_task="4")
    msg = FakeInboxMessage(
        text=json.dumps({"type": "shutdown_request", "requestId": "r2"}),
    )
    sent: list[tuple] = []
    with patch.object(
        loop_mod.pio,
        "send_shutdown_response",
        side_effect=lambda *a, **k: sent.append((a, k)),
    ):
        _handle_message(state, msg)
    assert state.approved_shutdown is False
    assert state.shutdown_requested is True  # queued for later
    args, kwargs = sent[0]
    assert args[2] == "r2"
    assert kwargs["approve"] is False
    assert "in-flight task #4" in kwargs["feedback"]


def test_shutdown_duplicate_request_id_ignored():
    state = LoopState(settings=_settings())
    state.seen_shutdown_request_ids.add("r3")
    msg = FakeInboxMessage(
        text=json.dumps({"type": "shutdown_request", "request_id": "r3"}),
    )
    with patch.object(loop_mod.pio, "send_shutdown_response") as m:
        _handle_message(state, msg)
    assert state.approved_shutdown is False
    assert m.call_count == 0


def _fake_codex_result(reply: str = "Four.", exit_code: int = 0):
    from claude_anyteam import codex as codex_mod
    return codex_mod.CodexResult(
        exit_code=exit_code,
        structured=None,
        last_message=reply,
        events=[],
        error=None if exit_code == 0 else "oops",
    )


def _settings_no_app_server() -> Settings:
    """Settings with app_server=False (fresh-exec path)."""
    s = _settings()
    d = {f: getattr(s, f) for f in s.__dataclass_fields__}
    d["app_server"] = False
    return s.__class__(**d)


def test_prose_message_app_server_invokes_codex_and_replies_to_sender():
    """App Server mode (default): prose invocation uses app_server_invoke, reply goes to sender."""
    state = LoopState(settings=_settings())  # app_server=True by default
    msg = FakeInboxMessage(text="hey what's 2+2?", from_="peer-bob")

    sent: list[tuple] = []

    with (
        patch.object(
            loop_mod.codex_mod, "app_server_invoke",
            return_value=_fake_codex_result("Four."),
        ),
        patch.object(
            loop_mod.pio, "send_prose",
            side_effect=lambda team, sender, to, text, summary: sent.append((to, text)),
        ),
    ):
        _handle_message(state, msg)

    # Loop state must not be dirtied.
    assert state.approved_shutdown is False
    assert state.shutdown_requested is False
    assert state.codex_session_id is None
    assert state.app_server_last_thread_id is None

    # Reply must be routed to the sender, not team-lead.
    assert len(sent) == 1
    to, text = sent[0]
    assert to == "peer-bob"
    assert "Four." in text


def test_prose_message_fresh_exec_path_replies_to_sender():
    """Fresh-exec mode (--no-app-server): prose invocation uses run(), reply goes to sender."""
    state = LoopState(settings=_settings_no_app_server())
    msg = FakeInboxMessage(text="ping", from_="claude-peer")

    sent: list[tuple] = []

    with (
        patch.object(
            loop_mod.codex_mod, "run",
            return_value=_fake_codex_result("pong"),
        ),
        patch.object(
            loop_mod.pio, "send_prose",
            side_effect=lambda team, sender, to, text, summary: sent.append((to, text)),
        ),
    ):
        _handle_message(state, msg)

    assert len(sent) == 1
    to, text = sent[0]
    assert to == "claude-peer"
    assert "pong" in text

    # Lineage slots must be clean after a prose invocation.
    assert state.app_server_last_thread_id is None
    assert state.codex_session_id is None


def test_prose_message_codex_fail_sends_fallback_ack():
    """If Codex fails (nonzero exit), the adapter still replies (no silence)."""
    state = LoopState(settings=_settings())
    msg = FakeInboxMessage(text="hello", from_="peer-bob")

    sent: list[tuple] = []

    with (
        patch.object(
            loop_mod.codex_mod, "app_server_invoke",
            return_value=_fake_codex_result("", exit_code=1),
        ),
        patch.object(
            loop_mod.pio, "send_prose",
            side_effect=lambda team, sender, to, text, summary: sent.append((to, text)),
        ),
    ):
        _handle_message(state, msg)

    assert len(sent) == 1
    to, _ = sent[0]
    assert to == "peer-bob"


def test_prose_message_codex_crash_sends_fallback_ack():
    """If Codex invocation raises, the adapter still replies (no silence)."""
    state = LoopState(settings=_settings())
    msg = FakeInboxMessage(text="hello", from_="peer-bob")

    sent: list[tuple] = []

    with (
        patch.object(loop_mod.codex_mod, "app_server_invoke", side_effect=RuntimeError("codex missing")),
        patch.object(
            loop_mod.pio, "send_prose",
            side_effect=lambda team, sender, to, text, summary: sent.append((to, text)),
        ),
    ):
        _handle_message(state, msg)

    assert len(sent) == 1
    to, _ = sent[0]
    assert to == "peer-bob"


def test_prose_message_reply_send_fail_does_not_raise():
    """A transport error on the reply send must not crash the loop."""
    state = LoopState(settings=_settings())
    msg = FakeInboxMessage(text="hello", from_="peer-bob")

    with (
        patch.object(loop_mod.codex_mod, "app_server_invoke", return_value=_fake_codex_result("hi")),
        patch.object(loop_mod.pio, "send_prose", side_effect=OSError("disk full")),
    ):
        _handle_message(state, msg)  # must not raise


def test_mid_turn_shutdown_sends_reject_response_immediately(tmp_path: Path):
    state = LoopState(settings=_settings(), in_flight_task="42")
    task = SimpleNamespace(id="42")
    shutdown_msg = FakeInboxMessage(
        text=json.dumps({"type": "shutdown_request", "requestId": "mid-task-1"}),
    )
    schema_path = tmp_path / "task-complete-schema.json"
    schema_path.write_text("{}", encoding="utf-8")
    sent: list[tuple] = []

    def fake_invoke(**kwargs):
        kwargs["mid_turn_hook"]()
        return _fake_codex_result("done")

    with (
        patch.object(loop_mod.codex_mod, "TASK_COMPLETE_SCHEMA", schema_path),
        patch.object(loop_mod.pio, "read_own_inbox", return_value=[shutdown_msg]),
        patch.object(
            loop_mod.pio,
            "send_shutdown_response",
            side_effect=lambda *a, **k: sent.append((a, k)),
        ),
        patch.object(loop_mod.codex_mod, "app_server_invoke", side_effect=fake_invoke),
    ):
        loop_mod._execute_task_app_server(state, task, prompt="do work")

    assert state.shutdown_requested is True
    assert "mid-task-1" in state.seen_shutdown_request_ids
    assert len(sent) == 1
    args, kwargs = sent[0]
    assert args[2] == "mid-task-1"
    assert kwargs["approve"] is False
    assert kwargs["feedback"] == "in-flight task #42"


def test_mid_turn_shutdown_duplicate_request_id_is_ignored(tmp_path: Path):
    state = LoopState(settings=_settings(), in_flight_task="42")
    task = SimpleNamespace(id="42")
    shutdown_msg = FakeInboxMessage(
        text=json.dumps({"type": "shutdown_request", "requestId": "mid-task-dup"}),
    )
    schema_path = tmp_path / "task-complete-schema-dup.json"
    schema_path.write_text("{}", encoding="utf-8")
    sent: list[tuple] = []

    def fake_invoke(**kwargs):
        kwargs["mid_turn_hook"]()
        kwargs["mid_turn_hook"]()
        return _fake_codex_result("done")

    with (
        patch.object(loop_mod.codex_mod, "TASK_COMPLETE_SCHEMA", schema_path),
        patch.object(loop_mod.pio, "read_own_inbox", return_value=[shutdown_msg]),
        patch.object(
            loop_mod.pio,
            "send_shutdown_response",
            side_effect=lambda *a, **k: sent.append((a, k)),
        ),
        patch.object(loop_mod.codex_mod, "app_server_invoke", side_effect=fake_invoke),
    ):
        loop_mod._execute_task_app_server(state, task, prompt="do work")

    assert state.shutdown_requested is True
    assert "mid-task-dup" in state.seen_shutdown_request_ids
    assert len(sent) == 1


def test_plan_request_ignored_under_default_policy():
    state = LoopState(settings=_settings())  # plan_mode_required=False
    msg = FakeInboxMessage(
        text=json.dumps({"type": "plan_approval_request", "requestId": "p1"}),
    )
    _handle_message(state, msg)
    assert state.approved_shutdown is False


# ---- _find_and_claim ---------------------------------------------------------


def test_claim_prefers_assigned_then_unassigned():
    state = LoopState(settings=_settings())
    tasks = [
        FakeTask(id="1", status="completed"),
        FakeTask(id="2", status="pending", owner=None),
        FakeTask(id="3", status="pending", owner="a"),  # assigned to us
    ]
    claimed_ids: list[str] = []

    def fake_claim(team, tid, owner, active_form):
        claimed_ids.append(tid)
        # Simulate the returned task having the new status.
        t = next(t for t in tasks if t.id == tid)
        return SimpleNamespace(id=t.id, subject=t.subject)

    with (
        patch.object(loop_mod.pio, "list_tasks", return_value=tasks),
        patch.object(loop_mod.pio, "claim_task", side_effect=fake_claim),
    ):
        result = _find_and_claim(state)
    assert result is not None
    assert state.in_flight_task == "3"
    assert claimed_ids == ["3"]  # assigned preferred over unassigned


def test_claim_falls_through_race_to_next_candidate():
    state = LoopState(settings=_settings())
    tasks = [
        FakeTask(id="1", status="pending", owner=None),
        FakeTask(id="2", status="pending", owner=None),
    ]
    attempts: list[str] = []

    def racy_claim(team, tid, owner, active_form):
        attempts.append(tid)
        if tid == "1":
            raise ValueError("someone else got it first")
        return SimpleNamespace(id=tid, subject="x")

    with (
        patch.object(loop_mod.pio, "list_tasks", return_value=tasks),
        patch.object(loop_mod.pio, "claim_task", side_effect=racy_claim),
    ):
        result = _find_and_claim(state)
    assert attempts == ["1", "2"]
    assert result is not None
    assert state.in_flight_task == "2"


def test_claim_treats_empty_string_owner_as_unassigned():
    """Regression test for the M2 stall.

    When a task_update tool serializes "no owner" as the empty string rather
    than null, the claim filter must still recognize the task as claimable.
    Observed live on task #7 after a TaskUpdate(owner="") reset — the adapter
    idled instead of claiming because the old filter only matched `is None`.
    """
    state = LoopState(settings=_settings())
    tasks = [
        FakeTask(id="9", status="pending", owner=""),  # empty string = unowned
    ]
    attempts: list[str] = []

    def fake_claim(team, tid, owner, active_form):
        attempts.append(tid)
        return SimpleNamespace(id=tid, subject="x")

    with (
        patch.object(loop_mod.pio, "list_tasks", return_value=tasks),
        patch.object(loop_mod.pio, "claim_task", side_effect=fake_claim),
    ):
        result = _find_and_claim(state)
    assert attempts == ["9"]
    assert result is not None
    assert state.in_flight_task == "9"


def test_claim_skips_blocked_tasks():
    state = LoopState(settings=_settings())
    tasks = [
        FakeTask(id="5", status="pending", blocked_by=["4"], owner=None),
        FakeTask(id="4", status="pending", blocked_by=[], owner=None),
    ]
    attempts: list[str] = []

    def fake_claim(team, tid, owner, active_form):
        attempts.append(tid)
        return SimpleNamespace(id=tid, subject="x")

    with (
        patch.object(loop_mod.pio, "list_tasks", return_value=tasks),
        patch.object(loop_mod.pio, "claim_task", side_effect=fake_claim),
    ):
        result = _find_and_claim(state)
    # Only #4 is unblocked; #5 is blocked by #4 which is still pending.
    assert attempts == ["4"]
    assert result is not None
    assert state.in_flight_task == "4"


def test_claim_returns_none_when_all_claimed_or_blocked():
    state = LoopState(settings=_settings())
    tasks = [
        FakeTask(id="1", status="in_progress", owner="someone"),
        FakeTask(id="2", status="pending", owner="someone"),
        FakeTask(id="3", status="pending", blocked_by=["2"], owner=None),
    ]
    with (
        patch.object(loop_mod.pio, "list_tasks", return_value=tasks),
        patch.object(loop_mod.pio, "claim_task") as m,
    ):
        result = _find_and_claim(state)
    assert result is None
    assert state.in_flight_task is None
    assert m.call_count == 0
