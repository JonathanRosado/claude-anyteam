from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from claude_anyteam.backends.gemini import loop
from claude_anyteam.backends.gemini.config import GeminiSettings
from claude_anyteam.backends.gemini.loop import GeminiLoopState, QueuedSteer
from claude_anyteam.codex import CodexResult


def _settings(*, backend: str = "acp") -> GeminiSettings:
    return GeminiSettings(
        team_name="t",
        agent_name="a",
        cwd=Path("/tmp").resolve(),
        poll_interval_s=0.01,
        color="cyan",
        plan_mode_required=False,
        backend=backend,
    )


def _task(task_id: str = "7"):
    return SimpleNamespace(id=task_id, subject="Do work", description="Original task body", owner="a", status="pending", blocked_by=[])


def _success() -> CodexResult:
    return CodexResult(
        exit_code=0,
        structured={"files_changed": [], "summary": "done"},
        last_message='{"files_changed":[],"summary":"done"}',
        events=[],
        error=None,
        session_id="s1",
    )


def test_team_lead_steer_without_task_id_injects_next_task_once():
    state = GeminiLoopState(settings=_settings())
    msg = SimpleNamespace(
        from_="team-lead",
        text=json.dumps({"type": "steer", "message": "Skip benchmarks entirely."}),
    )
    loop._handle_message(state, msg)
    assert len(state.queued_steers) == 1

    prompts: list[str] = []
    with (
        patch.object(loop, "_backend_run", side_effect=lambda _state, prompt, **_kw: prompts.append(prompt) or _success()),
        patch.object(loop.pio, "update_task"),
        patch.object(loop.pio, "send_task_complete"),
    ):
        loop._execute_task(state, _task("7"))

    assert len(prompts) == 1
    assert prompts[0].startswith("# Team-lead next-turn steer")
    assert "Skip benchmarks entirely." in prompts[0]
    assert "# Original task prompt" in prompts[0]
    assert "# Subject\nDo work" in prompts[0]
    assert state.queued_steers == []

    prompts.clear()
    with (
        patch.object(loop, "_backend_run", side_effect=lambda _state, prompt, **_kw: prompts.append(prompt) or _success()),
        patch.object(loop.pio, "update_task"),
        patch.object(loop.pio, "send_task_complete"),
    ):
        loop._execute_task(state, _task("8"))
    assert not prompts[0].startswith("# Team-lead next-turn steer")



def test_team_lead_plain_text_steer_marker_injects_next_task_once():
    state = GeminiLoopState(settings=_settings())
    msg = SimpleNamespace(
        from_="team-lead",
        text="STEER: do X",
    )
    loop._handle_message(state, msg)
    assert len(state.queued_steers) == 1

    prompts: list[str] = []
    with (
        patch.object(loop, "_backend_run", side_effect=lambda _state, prompt, **_kw: prompts.append(prompt) or _success()),
        patch.object(loop.pio, "update_task"),
        patch.object(loop.pio, "send_task_complete"),
    ):
        loop._execute_task(state, _task("7"))

    assert len(prompts) == 1
    assert prompts[0].startswith("# Team-lead next-turn steer")
    assert "do X" in prompts[0]
    assert "# Original task prompt" in prompts[0]
    assert state.queued_steers == []


def test_acp_peer_dm_message_kind_plain_prose_stays_prose(monkeypatch):
    """Phase4 #14: peer-DM discriminators must not become ACP steers."""

    state = GeminiLoopState(settings=_settings(backend="acp"))
    msg = SimpleNamespace(
        from_="codex-implementer",
        text="FYI: I found the relevant Gemini loop path.",
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


def test_acp_message_kind_steer_plain_prose_queues_peer_steer():
    """Phase4 #14: explicit sender-side steer intent uses the ACP steer queue."""

    state = GeminiLoopState(settings=_settings(backend="acp"))
    msg = SimpleNamespace(
        from_="codex-implementer",
        text="Use the messageKind discriminator before prose batching.",
        message_kind="steer",
        summary="intentional steer",
    )

    assert loop._partition_inbox([msg]) == [("protocol", [msg])]

    loop._handle_message(state, msg)

    assert len(state.queued_steers) == 1
    assert state.queued_steers[0].message == msg.text


def test_acp_unknown_or_absent_message_kind_falls_back_to_existing_heuristic(monkeypatch):
    """Unknown/missing discriminators preserve the old parse_protocol_text path."""

    state = GeminiLoopState(settings=_settings(backend="acp"))
    unknown_plain = SimpleNamespace(
        from_="codex-implementer",
        text="Plain coordination with a future message kind.",
        message_kind="future_kind",
        summary="future",
    )
    unknown_marker = SimpleNamespace(
        from_="codex-implementer",
        text="STEER: keep the test narrow.",
        message_kind="future_kind",
        summary="future steer marker",
    )
    absent_marker = SimpleNamespace(
        from_="codex-implementer",
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


def test_matching_task_id_steer_injects_and_nonmatching_is_retained():
    state = GeminiLoopState(
        settings=_settings(),
        queued_steers=[
            QueuedSteer(steer_id="s-match", message="Only update docs.", task_id="42"),
            QueuedSteer(steer_id="s-later", message="Later task steer.", task_id="99"),
        ],
    )

    prefix = loop._steer_prefix_for_task(state, _task("42"))

    assert "Only update docs." in prefix
    assert "Later task steer." not in prefix
    assert [s.steer_id for s in state.queued_steers] == ["s-later"]


def test_non_lead_steer_without_declared_capability_is_ignored(monkeypatch):
    state = GeminiLoopState(settings=_settings(backend="headless"))
    warns: list[tuple[str, dict]] = []
    monkeypatch.setattr(loop.logger, "warn", lambda msg, **fields: warns.append((msg, fields)))
    msg = SimpleNamespace(
        from_="codex-implementer",
        text=json.dumps({"type": "steer", "message": "Malicious steer."}),
    )

    loop._handle_message(state, msg)

    assert state.queued_steers == []
    assert warns == [
        (
            "gemini.steer.rejected",
            {
                "sender": "codex-implementer",
                "reason": "not_team_lead_and_capability_not_declared",
            },
        )
    ]
