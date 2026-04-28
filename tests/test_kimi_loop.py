from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from claude_anyteam.backends.kimi import loop
from claude_anyteam.backends.kimi.config import KimiSettings
from claude_anyteam.codex import CodexResult


def _settings(tmp_path: Path) -> KimiSettings:
    return KimiSettings(
        team_name="t",
        agent_name="a",
        cwd=tmp_path,
        poll_interval_s=0.01,
        color="cyan",
        plan_mode_required=False,
        kimi_binary="kimi",
    )


def _prose_result(
    text: str = "",
    *,
    exit_code: int = 0,
    tool_call_events: int = 0,
) -> CodexResult:
    return CodexResult(
        exit_code=exit_code,
        structured=None,
        last_message=text,
        events=[],
        tool_call_events=tool_call_events,
        error=None if exit_code == 0 else "failed",
    )


def test_backend_run_drops_resume_session_for_ephemeral_invocations(tmp_path: Path, monkeypatch):
    state = loop.KimiLoopState(settings=_settings(tmp_path))
    calls: list[dict] = []

    def fake_run(_prompt: str, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(exit_code=0, structured=None, last_message="", events=[])

    monkeypatch.setattr(loop.headless_invoke, "run", fake_run)

    loop._backend_run(state, "prose", resume_session_id="durable-session", ephemeral=True)
    loop._backend_run(state, "task", resume_session_id="durable-session", ephemeral=False)

    assert calls[0]["resume_session_id"] is None
    assert calls[1]["resume_session_id"] == "durable-session"


def test_main_loop_batches_five_idle_peer_dms_into_one_kimi_invocation(tmp_path: Path, monkeypatch):
    """5 peer prose DMs in one idle inbox drain -> one Kimi invocation."""
    state = loop.KimiLoopState(settings=_settings(tmp_path))
    messages = [
        SimpleNamespace(from_=f"peer-{idx}", text=f"hello {idx}", summary="dm")
        for idx in range(5)
    ]
    invocations: list[dict] = []

    def fake_backend_run(_state, prompt: str, **kwargs):
        invocations.append({"prompt": prompt, **kwargs})
        _state.approved_shutdown = True
        return _prose_result(tool_call_events=5)

    monkeypatch.setattr(loop.pio, "read_own_inbox", lambda *a: messages)
    monkeypatch.setattr(loop, "_backend_run", fake_backend_run)
    monkeypatch.setattr(loop.pio, "send_prose", lambda *a, **k: None)

    loop._main_loop(state)

    assert len(invocations) == 1
    prompt = invocations[0]["prompt"]
    assert prompt.count("[from peer-") == 5
    assert invocations[0]["ephemeral"] is True


def test_handle_prose_batch_preserves_per_sender_fallback_on_crash(tmp_path: Path, monkeypatch):
    state = loop.KimiLoopState(settings=_settings(tmp_path))
    messages = [
        SimpleNamespace(from_="peer-a", text="hi", summary="dm"),
        SimpleNamespace(from_="peer-b", text="yo", summary="dm"),
        SimpleNamespace(from_="peer-c", text="?", summary="dm"),
    ]
    sent: list[tuple[str, str]] = []

    def crashing_backend_run(*_args, **_kwargs):
        raise RuntimeError("kimi crashed")

    monkeypatch.setattr(loop, "_backend_run", crashing_backend_run)
    monkeypatch.setattr(
        loop.pio,
        "send_prose",
        lambda team, sender, to, text, summary: sent.append((to, text)),
    )

    loop._handle_prose_batch(state, messages)

    assert sorted(to for to, _ in sent) == ["peer-a", "peer-b", "peer-c"]
    for _, text in sent:
        assert "incident=" in text
        assert "adapter=kimi" in text


def test_handle_prose_batch_single_message_uses_fast_path(tmp_path: Path, monkeypatch):
    state = loop.KimiLoopState(settings=_settings(tmp_path))
    msg = SimpleNamespace(from_="peer-solo", text="just one", summary="dm")
    calls: list[object] = []

    monkeypatch.setattr(loop, "_handle_prose", lambda _state, _msg: calls.append(_msg))

    loop._handle_prose_batch(state, [msg])

    assert calls == [msg]


def test_kimi_peer_dm_message_kind_plain_prose_stays_prose(tmp_path: Path, monkeypatch):
    """Phase4 #19: peer-DM discriminators must not become Kimi steers."""

    state = loop.KimiLoopState(settings=_settings(tmp_path))
    msg = SimpleNamespace(
        from_="codex-implementer",
        text="FYI: I found the relevant Kimi loop path.",
        message_kind="peer-DM",
        summary="coordination",
    )
    handled_as_prose: list[object] = []
    monkeypatch.setattr(
        loop,
        "_handle_prose",
        lambda _state, prose_msg: handled_as_prose.append(prose_msg),
    )

    assert loop._partition_inbox([msg]) == [("prose", [msg])]

    loop._handle_message(state, msg)

    assert state.queued_steers == []
    assert handled_as_prose == [msg]


def test_kimi_message_kind_steer_plain_prose_queues_team_lead_steer(tmp_path: Path):
    """Phase4 #19: explicit sender-side steer intent uses Kimi's steer queue."""

    state = loop.KimiLoopState(settings=_settings(tmp_path))
    msg = SimpleNamespace(
        from_="team-lead",
        text="Use the messageKind discriminator before prose batching.",
        messageKind="steer",
        summary="intentional steer",
    )

    assert loop._partition_inbox([msg]) == [("protocol", [msg])]

    loop._handle_message(state, msg)

    assert len(state.queued_steers) == 1
    assert state.queued_steers[0].message == msg.text


def test_kimi_unknown_or_absent_message_kind_falls_back_to_existing_heuristic(
    tmp_path: Path, monkeypatch
):
    """Unknown/missing discriminators preserve the old parse_protocol_text path."""

    state = loop.KimiLoopState(settings=_settings(tmp_path))
    unknown_plain = SimpleNamespace(
        from_="codex-implementer",
        text="Plain coordination with a future message kind.",
        message_kind="future_kind",
        summary="future",
    )
    unknown_marker = SimpleNamespace(
        from_="team-lead",
        text="STEER: keep the test narrow.",
        message_kind="future_kind",
        summary="future steer marker",
    )
    absent_marker = SimpleNamespace(
        from_="team-lead",
        text="STEER: preserve legacy marker behavior.",
        summary="legacy steer marker",
    )
    handled_as_prose: list[object] = []
    monkeypatch.setattr(
        loop,
        "_handle_prose",
        lambda _state, prose_msg: handled_as_prose.append(prose_msg),
    )

    loop._handle_message(state, unknown_plain)
    loop._handle_message(state, unknown_marker)
    loop._handle_message(state, absent_marker)

    assert handled_as_prose == [unknown_plain]
    assert [s.message for s in state.queued_steers] == [
        "keep the test narrow.",
        "preserve legacy marker behavior.",
    ]


def test_kimi_prose_paths_pass_visibility_event_sink(tmp_path: Path, monkeypatch):
    state = loop.KimiLoopState(settings=_settings(tmp_path))
    invocations: list[dict] = []

    def fake_backend_run(_state, _prompt: str, **kwargs):
        invocations.append(kwargs)
        return _prose_result("reply")

    monkeypatch.setattr(loop, "_backend_run", fake_backend_run)
    monkeypatch.setattr(loop.pio, "send_prose", lambda *a, **k: None)

    loop._handle_prose_batch(
        state,
        [SimpleNamespace(from_="peer-one", text="one", summary="dm")],
    )
    loop._handle_prose_batch(
        state,
        [
            SimpleNamespace(from_="peer-two", text="two", summary="dm"),
            SimpleNamespace(from_="peer-three", text="three", summary="dm"),
        ],
    )

    assert len(invocations) == 2
    assert all(call["ephemeral"] is True for call in invocations)
    assert all(call["event_sink"] is not None for call in invocations)
    assert all(callable(call["event_sink"]) for call in invocations)


def test_kimi_mark_blocked_skips_when_task_already_completed(tmp_path: Path):
    state = loop.KimiLoopState(settings=_settings(tmp_path))
    task = SimpleNamespace(id="42", status="in_progress")
    completed_on_disk = SimpleNamespace(id="42", status="completed")

    update_calls: list = []
    send_calls: list = []

    with (
        patch.object(loop.pio, "get_task", return_value=completed_on_disk),
        patch.object(loop.pio, "update_task", side_effect=lambda *a, **k: update_calls.append((a, k))),
        patch.object(loop.pio, "send_task_blocked", side_effect=lambda *a, **k: send_calls.append((a, k))),
    ):
        loop._mark_blocked(state, task, reason="test reason")

    assert update_calls == []
    assert send_calls == []


def test_kimi_tight_task_on_loose_backend_emits_conflict_and_claims(tmp_path: Path):
    state = loop.KimiLoopState(
        settings=_settings(tmp_path),
        self_capability_manifest={
            "agent_name": "a",
            "coupling_regime": "loose",
            "coupling": {"intent": "loose_parallel"},
        },
    )
    task = SimpleNamespace(
        id="7",
        subject="tight coordination",
        description="needs mid-turn coordination",
        status="pending",
        owner="a",
        blocked_by=[],
        coupling="tight",
    )
    emitted: list[dict] = []

    with (
        patch.object(loop.pio, "list_tasks", return_value=[task]),
        patch.object(loop.pio, "claim_task", return_value=task),
        patch.object(
            loop.pio,
            "emit_coupling_conflict_if_needed",
            side_effect=lambda **kwargs: emitted.append(kwargs),
        ),
    ):
        claimed = loop._find_and_claim(state)

    assert claimed is task
    assert state.in_flight_task == "7"
    assert emitted[0]["backend"] == "kimi_headless"
    assert emitted[0]["manifest"] == state.self_capability_manifest
    assert emitted[0]["task"] is task


def test_execute_task_survives_backend_run_exception(tmp_path: Path, monkeypatch):
    """Regression for parity finding #6 — a subprocess crash inside
    _backend_run (OSError, FileNotFoundError, etc.) must NOT propagate to
    the main loop and kill the adapter. Codex (loop.py:672-680) handles
    this; Kimi must too.
    """
    state = loop.KimiLoopState(settings=_settings(tmp_path))
    task = SimpleNamespace(id="99", subject="x", description="x", status="in_progress")
    state.in_flight_task = "99"

    def crashing_backend_run(*_args, **_kwargs):
        raise OSError("simulated subprocess crash")

    mark_blocked_calls: list[tuple] = []

    monkeypatch.setattr(loop, "_backend_run", crashing_backend_run)
    monkeypatch.setattr(loop, "_mark_blocked", lambda state, task, reason: mark_blocked_calls.append((task.id, reason)))

    # Must not raise — exception is caught, _mark_blocked is called, return.
    loop._execute_task(state, task)

    assert state.in_flight_task is None
    assert mark_blocked_calls and mark_blocked_calls[0][0] == "99"
    assert "Kimi invocation crashed" in mark_blocked_calls[0][1]
    assert "simulated subprocess crash" in mark_blocked_calls[0][1]


def test_handle_prose_skips_fallback_when_model_used_send_message_tool(tmp_path: Path, monkeypatch):
    """Regression for parity finding #8 — when the model delivers its reply
    via the send_message MCP tool, last_message is empty by design. The
    adapter must NOT then send a canned 'could not generate' fallback on
    top of the real reply. Detect via tool_call_events > 0.
    """
    state = loop.KimiLoopState(settings=_settings(tmp_path))
    msg = SimpleNamespace(from_="codex-bob", text="ack", summary="prose")

    fake_result = SimpleNamespace(
        exit_code=0,
        last_message="",
        structured=None,
        events=[],
        tool_call_events=1,
        session_id=None,
        error=None,
    )
    monkeypatch.setattr(loop, "_backend_run", lambda *a, **k: fake_result)

    send_prose_calls: list = []
    monkeypatch.setattr(loop.pio, "send_prose", lambda *a, **k: send_prose_calls.append((a, k)))

    loop._handle_prose(state, msg)

    # No fallback sent — model delivered via tool already.
    assert send_prose_calls == []


def test_handle_prose_skips_prose_text_after_send_message_tool(tmp_path: Path, monkeypatch):
    """M13 regression: Kimi may emit both send_message and final prose text.

    The final assistant prose is informational after the wrapper tool has
    already delivered the peer reply; the adapter must not send it as a second
    prose-mode fallback.
    """
    state = loop.KimiLoopState(settings=_settings(tmp_path))
    msg = SimpleNamespace(from_="codex-bob", text="ack", summary="prose")

    fake_result = SimpleNamespace(
        exit_code=0,
        last_message="Already sent the answer with send_message; no fallback needed.",
        structured=None,
        events=[
            {
                "role": "assistant",
                "tool_calls": [{"function": {"name": "send_message", "arguments": "{}"}}],
            }
        ],
        tool_call_events=1,
        session_id=None,
        error=None,
    )
    monkeypatch.setattr(loop, "_backend_run", lambda *a, **k: fake_result)

    send_prose_calls: list = []
    monkeypatch.setattr(loop.pio, "send_prose", lambda *a, **k: send_prose_calls.append((a, k)))

    loop._handle_prose(state, msg)

    assert send_prose_calls == []


def test_handle_prose_falls_back_when_no_tool_and_no_text(tmp_path: Path, monkeypatch):
    """Negative case for #8 — when neither last_message nor tool calls,
    the canned fallback IS still sent so the sender gets *something*.
    """
    state = loop.KimiLoopState(settings=_settings(tmp_path))
    msg = SimpleNamespace(from_="codex-bob", text="ack", summary="prose")

    fake_result = SimpleNamespace(
        exit_code=0,
        last_message="",
        structured=None,
        events=[],
        tool_call_events=0,
        session_id=None,
        error=None,
    )
    monkeypatch.setattr(loop, "_backend_run", lambda *a, **k: fake_result)

    send_prose_calls: list = []
    monkeypatch.setattr(loop.pio, "send_prose", lambda *a, **k: send_prose_calls.append((a, k)))

    loop._handle_prose(state, msg)

    # Fallback sent because model didn't deliver via tool either. Reply
    # carries the incident_id and backend identity per diagnostics.fallback_message.
    assert len(send_prose_calls) == 1
    sent_args, _ = send_prose_calls[0]
    assert "couldn't generate a reply" in sent_args[3]
    assert "incident=inc-" in sent_args[3]
    assert "adapter=kimi" in sent_args[3]
