from __future__ import annotations

import json
from pathlib import Path

import pytest

from claude_teams import messaging, tasks, teams
from claude_teams.tasks import create_task, get_task, update_task


TEAM = "auto-pickup"
AGENT_A = "agent-a"
AGENT_B = "agent-b"


@pytest.fixture
def auto_team(tmp_claude_dir: Path) -> str:
    teams.create_team(TEAM, "sess-auto", base_dir=tmp_claude_dir)
    return TEAM


def _enable_team_auto_pickup(team_name: str, base_dir: Path) -> None:
    config = teams.read_config(team_name, base_dir=base_dir)
    config.auto_pickup_next_task = True
    teams.write_config(team_name, config, base_dir=base_dir)


def _enable_agent_auto_pickup(team_name: str, agent_name: str, base_dir: Path) -> None:
    path = base_dir / "teams" / team_name / "agents" / f"{agent_name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"auto_pickup_next_task": True}))


def _create_three_tasks(team_name: str, base_dir: Path):
    return [
        create_task(team_name, f"Task {i}", f"do task {i}", base_dir=base_dir)
        for i in range(1, 4)
    ]


def _complete_first_as_agent_a(team_name: str, first_task_id: str, base_dir: Path) -> None:
    update_task(
        team_name,
        first_task_id,
        owner=AGENT_A,
        status="in_progress",
        base_dir=base_dir,
    )
    update_task(team_name, first_task_id, status="completed", base_dir=base_dir)


def _next_task_payload(team_name: str, base_dir: Path) -> dict:
    messages = messaging.read_inbox(
        team_name,
        AGENT_A,
        unread_only=False,
        mark_as_read=False,
        base_dir=base_dir,
    )
    assert len(messages) == 1
    msg = messages[0]
    assert msg.from_ == "team-protocol"
    assert msg.message_kind == "next_task"
    assert msg.summary is not None and msg.summary.startswith("next_task:")
    return json.loads(msg.text)


def test_auto_pickup_assigns_lowest_pending_unblocked_task_and_wakes_teammate(
    tmp_claude_dir: Path,
    auto_team: str,
) -> None:
    _enable_team_auto_pickup(auto_team, tmp_claude_dir)
    t1, t2, t3 = _create_three_tasks(auto_team, tmp_claude_dir)

    _complete_first_as_agent_a(auto_team, t1.id, tmp_claude_dir)

    next_task = get_task(auto_team, t2.id, base_dir=tmp_claude_dir)
    assert next_task.owner == AGENT_A
    assert next_task.status == "pending"
    assert get_task(auto_team, t3.id, base_dir=tmp_claude_dir).owner is None

    payload = _next_task_payload(auto_team, tmp_claude_dir)
    assert payload["type"] == "next_task"
    assert payload["task_id"] == t2.id
    assert payload["completed_task_id"] == t1.id
    assert payload["subject"] == t2.subject
    assert "Auto-pickup task #2" in payload["summary"]


def test_auto_pickup_flag_off_leaves_teammate_idle(
    tmp_claude_dir: Path,
    auto_team: str,
) -> None:
    t1, t2, _t3 = _create_three_tasks(auto_team, tmp_claude_dir)

    _complete_first_as_agent_a(auto_team, t1.id, tmp_claude_dir)

    assert get_task(auto_team, t2.id, base_dir=tmp_claude_dir).owner is None
    assert (
        messaging.read_inbox(auto_team, AGENT_A, mark_as_read=False, base_dir=tmp_claude_dir)
        == []
    )


def test_auto_pickup_can_be_enabled_per_teammate(
    tmp_claude_dir: Path,
    auto_team: str,
) -> None:
    _enable_agent_auto_pickup(auto_team, AGENT_A, tmp_claude_dir)
    t1, t2, _t3 = _create_three_tasks(auto_team, tmp_claude_dir)

    _complete_first_as_agent_a(auto_team, t1.id, tmp_claude_dir)

    assert get_task(auto_team, t2.id, base_dir=tmp_claude_dir).owner == AGENT_A
    assert _next_task_payload(auto_team, tmp_claude_dir)["task_id"] == t2.id


def test_auto_pickup_respects_concurrent_lead_reassignment(
    tmp_claude_dir: Path,
    auto_team: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_team_auto_pickup(auto_team, tmp_claude_dir)
    t1, t2, t3 = _create_three_tasks(auto_team, tmp_claude_dir)
    original_assign = tasks._assign_next_unblocked_unclaimed_task
    injected_race = False

    def lead_wins_before_auto_assign(team_name: str, owner: str, **kwargs):
        nonlocal injected_race
        if not injected_race:
            injected_race = True
            update_task(team_name, t2.id, owner=AGENT_B, base_dir=kwargs["base_dir"])
        return original_assign(team_name, owner, **kwargs)

    monkeypatch.setattr(
        tasks,
        "_assign_next_unblocked_unclaimed_task",
        lead_wins_before_auto_assign,
    )

    _complete_first_as_agent_a(auto_team, t1.id, tmp_claude_dir)

    assert get_task(auto_team, t2.id, base_dir=tmp_claude_dir).owner == AGENT_B
    assert get_task(auto_team, t3.id, base_dir=tmp_claude_dir).owner == AGENT_A
    assert _next_task_payload(auto_team, tmp_claude_dir)["task_id"] == t3.id


def test_auto_pickup_skips_task_already_owned_by_another_teammate(
    tmp_claude_dir: Path,
    auto_team: str,
) -> None:
    _enable_team_auto_pickup(auto_team, tmp_claude_dir)
    t1, t2, t3 = _create_three_tasks(auto_team, tmp_claude_dir)
    update_task(auto_team, t2.id, owner=AGENT_B, base_dir=tmp_claude_dir)

    _complete_first_as_agent_a(auto_team, t1.id, tmp_claude_dir)

    assert get_task(auto_team, t2.id, base_dir=tmp_claude_dir).owner == AGENT_B
    assert get_task(auto_team, t3.id, base_dir=tmp_claude_dir).owner == AGENT_A
    assert _next_task_payload(auto_team, tmp_claude_dir)["task_id"] == t3.id
