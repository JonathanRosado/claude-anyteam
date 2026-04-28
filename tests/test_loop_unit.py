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
from claude_anyteam.loop import (
    LoopState,
    _find_and_claim,
    _handle_message,
    _handle_prose_batch,
    _mid_turn_prose_should_be_steer,
    _partition_inbox,
)


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
    coupling: dict | str | None = None


# ---- _handle_message / shutdown ----------------------------------------------


def test_shutdown_while_idle_approves_and_exits():
    state = LoopState(settings=_settings())
    msg = FakeInboxMessage(
        text=json.dumps({"type": "shutdown_request", "request_id": "r1"}),
    )
    sent: list[tuple] = []
    with patch.object(
        loop_mod.pio,
        "send_shutdown_approved",
        side_effect=lambda *a, **k: sent.append((a, k)),
    ):
        _handle_message(state, msg)
    assert state.approved_shutdown is True
    assert state.shutdown_requested is False
    assert len(sent) == 1
    # (team, agent, req_id)
    args, kwargs = sent[0]
    assert args[2] == "r1"
    assert kwargs == {}


def test_shutdown_while_idle_clears_lineage_state():
    state = LoopState(
        settings=_settings(),
        codex_session_id="sid-1",
        app_server_last_thread_id="thread-1",
    )
    msg = FakeInboxMessage(
        text=json.dumps({"type": "shutdown_request", "request_id": "r1b"}),
    )

    with patch.object(loop_mod.pio, "send_shutdown_approved"):
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
        "send_shutdown_rejected",
        side_effect=lambda *a, **k: sent.append((a, k)),
    ):
        _handle_message(state, msg)
    assert state.approved_shutdown is False
    assert state.shutdown_requested is True  # queued for later
    args, kwargs = sent[0]
    assert args[2] == "r2"
    assert "in-flight task #4" in kwargs["reason"]


def test_shutdown_duplicate_request_id_ignored():
    state = LoopState(settings=_settings())
    state.seen_shutdown_request_ids.add("r3")
    msg = FakeInboxMessage(
        text=json.dumps({"type": "shutdown_request", "request_id": "r3"}),
    )
    with (
        patch.object(loop_mod.pio, "send_shutdown_approved") as approved,
        patch.object(loop_mod.pio, "send_shutdown_rejected") as rejected,
    ):
        _handle_message(state, msg)
    assert state.approved_shutdown is False
    assert approved.call_count == 0
    assert rejected.call_count == 0


def _fake_codex_result(reply: str = "Four.", exit_code: int = 0, tool_call_events: int = 0):
    from claude_anyteam import codex as codex_mod
    return codex_mod.CodexResult(
        exit_code=exit_code,
        structured=None,
        last_message=reply,
        events=[],
        error=None if exit_code == 0 else "oops",
        tool_call_events=tool_call_events,
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


def test_prose_message_skips_fallback_when_codex_used_send_message_tool():
    """Regression for cross-backend prose-double-send bug exposed by Kimi.

    When Codex follows the prose-prompt instruction to deliver the reply via
    the `send_message` MCP tool, last_message is empty by design. The adapter
    must NOT then send a canned fallback on top of the real reply.
    Detected via tool_call_events > 0. Same fix shape as the Kimi adapter
    PR #11 (commit e5a3fdb).
    """
    state = LoopState(settings=_settings())
    msg = FakeInboxMessage(text="hello", from_="peer-bob")

    sent: list[tuple] = []

    with (
        patch.object(
            loop_mod.codex_mod, "app_server_invoke",
            return_value=_fake_codex_result("", exit_code=0, tool_call_events=1),
        ),
        patch.object(
            loop_mod.pio, "send_prose",
            side_effect=lambda team, sender, to, text, summary: sent.append((to, text)),
        ),
    ):
        _handle_message(state, msg)

    # No fallback sent — model delivered via tool already.
    assert sent == []


def test_prose_message_skips_final_text_when_codex_used_send_message_tool():
    """Forward-compat: Codex also treats post-tool prose as non-delivery text."""
    state = LoopState(settings=_settings())
    msg = FakeInboxMessage(text="hello", from_="peer-bob")
    result = loop_mod.codex_mod.CodexResult(
        exit_code=0,
        structured=None,
        last_message="Already replied via send_message.",
        events=[{"type": "mcp_tool_call", "name": "send_message"}],
        error=None,
        tool_call_events=1,
    )

    sent: list[tuple] = []

    with (
        patch.object(loop_mod.codex_mod, "app_server_invoke", return_value=result),
        patch.object(
            loop_mod.pio,
            "send_prose",
            side_effect=lambda team, sender, to, text, summary: sent.append((to, text)),
        ),
    ):
        _handle_message(state, msg)

    assert sent == []


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
            "send_shutdown_rejected",
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
    assert kwargs["reason"] == "in-flight task #42"


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
            "send_shutdown_rejected",
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


def test_claim_emits_coupling_conflict_without_blocking_dispatch():
    state = LoopState(
        settings=_settings_no_app_server(),  # codex_exec manifest declares loose
        self_capability_manifest={
            "agent_name": "a",
            "coupling_regime": "loose",
            "coupling": {"intent": "loose_parallel"},
        },
    )
    tasks = [
        FakeTask(id="7", status="pending", owner="a", coupling="tight"),
    ]
    emitted: list[dict] = []

    def fake_claim(team, tid, owner, active_form):
        return tasks[0]

    def fake_emit(**kwargs):
        emitted.append(kwargs)
        return None

    with (
        patch.object(loop_mod.pio, "list_tasks", return_value=tasks),
        patch.object(loop_mod.pio, "claim_task", side_effect=fake_claim),
        patch.object(loop_mod.pio, "emit_coupling_conflict_if_needed", side_effect=fake_emit),
    ):
        result = _find_and_claim(state)

    assert result is tasks[0]
    assert state.in_flight_task == "7"
    assert emitted == [
        {
            "team": "t",
            "agent": "a",
            "backend": "codex_exec",
            "task": tasks[0],
            "manifest": state.self_capability_manifest,
        }
    ]


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


# ---- _mid_turn_prose_should_be_steer (phase4 #17) ---------------------------
#
# Phase4 #17 lands the L4 half of the R3/#59 sender-side messageKind
# discriminator. These tests pin the per-kind decision matrix so future
# refactors cannot silently undo the throughput-regression fix observed in
# stress run S6+W7-post59 (n_completed=3/15, M11a p95 RTT=237s) where
# informational peer-DMs jammed recipient turn budgets via queued steers.


def test_mid_turn_prose_should_be_steer_lead_prose_always_steer_default_kind():
    # Lead prose without an explicit kind defaults to steer for operational
    # parity with native Claude leads.
    assert _mid_turn_prose_should_be_steer(
        sender="team-lead",
        recipient_capabilities=[],
        message_kind=None,
    ) is True


def test_mid_turn_prose_should_be_steer_lead_informational_still_steer():
    # Lead authority overrides messageKind: an explicit "informational" lead
    # prose still becomes a steer, because §3 declares lead-as-orchestrator
    # in all backends regardless of how the helper labelled the wire.
    assert _mid_turn_prose_should_be_steer(
        sender="team-lead",
        recipient_capabilities=["accepts_peer_steer"],
        message_kind="informational",
    ) is True


def test_mid_turn_prose_should_be_steer_peer_informational_never_steer_even_when_recipient_accepts():
    # Core regression — without this branch, a peer's kind="informational"
    # coordination DM would queue as steer when recipient declares
    # accepts_peer_steer, which is exactly the post-#59 throughput collapse.
    assert _mid_turn_prose_should_be_steer(
        sender="codex-r1",
        recipient_capabilities=["accepts_peer_steer"],
        message_kind="informational",
    ) is False


def test_mid_turn_prose_should_be_steer_peer_steer_kind_with_capability_is_steer():
    # When sender explicitly tagged kind="steer" and recipient declares
    # accepts_peer_steer, the prose should become a steer fragment.
    assert _mid_turn_prose_should_be_steer(
        sender="codex-r1",
        recipient_capabilities=["accepts_peer_steer"],
        message_kind="steer",
    ) is True


def test_mid_turn_prose_should_be_steer_peer_handoff_with_capability_defers():
    # Matrix lift #5: peer messages are informational by default; handoff is
    # ordinary coordination, not a mid-turn interrupt.
    assert _mid_turn_prose_should_be_steer(
        sender="codex-r1",
        recipient_capabilities=["accepts_peer_steer"],
        message_kind="handoff",
    ) is False


def test_mid_turn_prose_should_be_steer_peer_unknown_kind_with_capability_defers():
    # Unknown kinds must not accidentally acquire interrupt semantics.
    assert _mid_turn_prose_should_be_steer(
        sender="codex-r1",
        recipient_capabilities=["accepts_peer_steer"],
        message_kind="future_kind",
    ) is False


def test_mid_turn_prose_should_be_steer_peer_default_kind_with_capability_defers():
    # Missing/legacy peer kind is now informational by default; only explicit
    # kind="steer" may interrupt.
    assert _mid_turn_prose_should_be_steer(
        sender="codex-r1",
        recipient_capabilities=["accepts_peer_steer"],
        message_kind=None,
    ) is False


def test_mid_turn_prose_should_be_steer_peer_default_kind_without_capability_defers():
    # Pre-R3 wire row from peer; recipient does NOT declare
    # accepts_peer_steer → defer to post-turn handler (post-#56 contract).
    assert _mid_turn_prose_should_be_steer(
        sender="codex-r1",
        recipient_capabilities=[],
        message_kind=None,
    ) is False


def test_mid_turn_prose_should_be_steer_peer_informational_without_capability_defers():
    # Belt-and-suspenders: even when capability is absent, kind="informational"
    # never becomes steer. Both branches agree on defer.
    assert _mid_turn_prose_should_be_steer(
        sender="codex-r1",
        recipient_capabilities=[],
        message_kind="informational",
    ) is False


def test_mid_turn_prose_should_be_steer_peer_steer_kind_without_capability_defers():
    # kind="steer" alone does NOT override the recipient's lack of
    # accepts_peer_steer. The capability declaration governs.
    assert _mid_turn_prose_should_be_steer(
        sender="codex-r1",
        recipient_capabilities=[],
        message_kind="steer",
    ) is False


def test_mid_turn_prose_should_be_steer_no_sender_defers():
    # Defensive: sender=None must not crash and must not become a steer.
    assert _mid_turn_prose_should_be_steer(
        sender=None,
        recipient_capabilities=["accepts_peer_steer"],
        message_kind=None,
    ) is False
    assert _mid_turn_prose_should_be_steer(
        sender=None,
        recipient_capabilities=["accepts_peer_steer"],
        message_kind="steer",
    ) is False


# ---- _handle_prose_batch (#18 prose-handler cascade fix) -------------------
#
# Phase4 #18 collapses consecutive prose messages in one inbox drain into a
# single Codex invocation. Pre-#18, N peer DMs produced N separate
# `app_server_invoke` calls; each pays full thread/start + system-prompt cost.
# These tests pin the empirical invariant so future refactors can't silently
# regress the throughput fix. Per `feedback_tests_lock_empirical_invariants.md`,
# tests are what prevent the regression — without them the fix would not stick.


def test_prose_batch_5_messages_one_invocation():
    """5 consecutive peer prose messages -> 1 app_server_invoke call.

    Pre-fix: this asserts call_count==1 after dispatching 5 prose messages.
    With the pre-#18 single-message handler the count would be 5, so this is
    the load-bearing regression assertion for the cascade collapse.
    """
    state = LoopState(settings=_settings())
    msgs = [
        FakeInboxMessage(text=f"hello {i}", from_=f"peer-{i}") for i in range(5)
    ]
    invocations: list[dict] = []

    def fake_invoke(**kwargs):
        invocations.append(kwargs)
        # Simulate Codex delivering replies via the send_message MCP tool —
        # the success path with no fallback noise.
        return _fake_codex_result("", exit_code=0, tool_call_events=5)

    sent: list[tuple] = []
    with (
        patch.object(loop_mod.codex_mod, "app_server_invoke", side_effect=fake_invoke),
        patch.object(
            loop_mod.pio, "send_prose",
            side_effect=lambda team, sender, to, text, summary: sent.append((to, text)),
        ),
    ):
        _handle_prose_batch(state, msgs)

    assert len(invocations) == 1, (
        f"prose batch must collapse to 1 invocation; got {len(invocations)}"
    )
    prompt = invocations[0]["task_prompt"]
    assert "# Team messaging" in prompt
    assert "Plain prose output is NOT visible to teammates" in prompt
    assert "try SendMessage (capitalized)" in prompt
    # delivered_via_tool path: no fan-out send_prose calls (Codex addressed
    # each sender via the MCP tool itself).
    assert sent == []


def test_prose_batch_preserves_per_sender_fallback():
    """Codex crash mid-batch -> diagnostic fallback ack to EACH sender.

    Preserves the "no silence" invariant. Collapsing N senders into a single
    fallback would leave N-1 senders waiting for a reply that never arrives.
    """
    state = LoopState(settings=_settings())
    msgs = [
        FakeInboxMessage(text="hi", from_="peer-a"),
        FakeInboxMessage(text="yo", from_="peer-b"),
        FakeInboxMessage(text="?", from_="peer-c"),
    ]
    sent: list[tuple] = []
    with (
        patch.object(
            loop_mod.codex_mod, "app_server_invoke",
            side_effect=RuntimeError("codex segfault"),
        ),
        patch.object(
            loop_mod.pio, "send_prose",
            side_effect=lambda team, sender, to, text, summary: sent.append((to, text)),
        ),
    ):
        _handle_prose_batch(state, msgs)

    # One fallback per original sender — order-independent, no duplicates.
    sent_to = sorted(to for to, _ in sent)
    assert sent_to == ["peer-a", "peer-b", "peer-c"]
    # Each fallback should be the diagnostic-shaped message with an
    # incident id embedded — verify the shape, not the exact incident id.
    for _, text in sent:
        assert "incident=" in text
        assert "claude-anyteam diagnose" in text


def test_prose_batch_does_not_batch_protocol_messages():
    """_partition_inbox keeps protocol messages individual; prose runs collapse.

    Mixing 2 prose + 1 shutdown_request in one drain produces:
    - 1 prose batch (2 messages) -> 1 app_server_invoke
    - 1 protocol group (1 shutdown_request) -> handled by _handle_shutdown
    """
    msgs = [
        FakeInboxMessage(text="hello", from_="peer-a"),
        FakeInboxMessage(text="hi", from_="peer-b"),
        FakeInboxMessage(
            text=json.dumps({"type": "shutdown_request", "request_id": "r-batch-1"}),
        ),
    ]

    groups = _partition_inbox(msgs)
    assert len(groups) == 2
    assert groups[0][0] == "prose"
    assert len(groups[0][1]) == 2
    assert groups[1][0] == "protocol"
    assert len(groups[1][1]) == 1


def test_prose_single_message_still_works():
    """1 prose message -> 1 invocation, identical to pre-#18 single path."""
    state = LoopState(settings=_settings())
    msg = FakeInboxMessage(text="just one", from_="peer-solo")
    invocations: list[dict] = []

    def fake_invoke(**kwargs):
        invocations.append(kwargs)
        return _fake_codex_result("Solo reply.", exit_code=0)

    sent: list[tuple] = []
    with (
        patch.object(loop_mod.codex_mod, "app_server_invoke", side_effect=fake_invoke),
        patch.object(
            loop_mod.pio, "send_prose",
            side_effect=lambda team, sender, to, text, summary: sent.append((to, text)),
        ),
    ):
        _handle_prose_batch(state, [msg])

    assert len(invocations) == 1
    # Single-message path uses send_prose for the reply (last_message text,
    # no tool calls), so the sender receives the codex reply directly.
    assert len(sent) == 1
    to, text = sent[0]
    assert to == "peer-solo"
    assert "Solo reply." in text


def test_prose_invocation_passes_event_sink():
    """Both the single and batch invocations pass event_sink (§2 fix).

    Pre-#18, `app_server_invoke` was called WITHOUT event_sink for prose
    turns. Tool calls during prose (Read / Edit / Bash) vanished into the
    wrapper instead of surfacing to the lead. This pins the §2 fix so a
    future refactor can't silently drop the kwarg.
    """
    state = LoopState(settings=_settings())
    invocations: list[dict] = []

    def fake_invoke(**kwargs):
        invocations.append(kwargs)
        return _fake_codex_result("hi back")

    # Single-message path
    msg_solo = FakeInboxMessage(text="ping", from_="peer-x")
    with (
        patch.object(loop_mod.codex_mod, "app_server_invoke", side_effect=fake_invoke),
        patch.object(loop_mod.pio, "send_prose"),
    ):
        _handle_prose_batch(state, [msg_solo])

    # Batch path
    msgs_many = [
        FakeInboxMessage(text="a", from_="peer-y"),
        FakeInboxMessage(text="b", from_="peer-z"),
    ]
    with (
        patch.object(loop_mod.codex_mod, "app_server_invoke", side_effect=fake_invoke),
        patch.object(loop_mod.pio, "send_prose"),
    ):
        _handle_prose_batch(state, msgs_many)

    assert len(invocations) == 2
    for inv in invocations:
        assert "event_sink" in inv, "event_sink kwarg must be present"
        assert inv["event_sink"] is not None, (
            "prose invocations must pass a non-None event_sink so prose-time "
            "tool calls surface to the lead (§2 visibility parity)"
        )
        # Sanity: it must be callable so app_server_invoke can fire it.
        assert callable(inv["event_sink"])
