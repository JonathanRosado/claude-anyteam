from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from claude_anyteam.backends.gemini import loop
from claude_anyteam.backends.gemini.config import GeminiSettings
from claude_anyteam.backends.gemini.loop import GeminiLoopState, QueuedSteer
from claude_anyteam.codex import CodexResult


def _settings() -> GeminiSettings:
    return GeminiSettings(
        team_name="t",
        agent_name="a",
        cwd=Path("/tmp").resolve(),
        poll_interval_s=0.01,
        color="cyan",
        plan_mode_required=False,
        backend="acp",
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


def test_non_lead_steer_is_ignored():
    state = GeminiLoopState(settings=_settings())
    msg = SimpleNamespace(
        from_="codex-implementer",
        text=json.dumps({"type": "steer", "message": "Malicious steer."}),
    )

    loop._handle_message(state, msg)

    assert state.queued_steers == []
