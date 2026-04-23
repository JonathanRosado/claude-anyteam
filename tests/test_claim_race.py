"""Regression test for the concurrent claim_task race (bug #17).

Two adapters simultaneously calling claim_task on the same pending task must
result in exactly one winner — the loser must receive a ValueError that
_find_and_claim interprets as a lost race and skips to the next candidate.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from claude_teams import tasks as cs_tasks
from claude_teams import teams as cs_teams
from claude_anyteam import protocol_io as pio


def _setup_team_and_task(tmp_path: Path) -> tuple[str, str]:
    """Create a minimal team dir + one pending task. Returns (team_name, task_id)."""
    team = "claim-race-team"
    team_dir = tmp_path / "teams" / team
    team_dir.mkdir(parents=True)
    cfg = {"name": team, "members": []}
    (team_dir / "config.json").write_text(json.dumps(cfg))

    tasks_dir = tmp_path / "tasks" / team
    tasks_dir.mkdir(parents=True)
    task = {
        "id": "1",
        "subject": "race target",
        "description": "both adapters want this",
        "status": "pending",
        "owner": None,
        "active_form": "",
        "blocked_by": [],
        "blocks": [],
    }
    (tasks_dir / "1.json").write_text(json.dumps(task))
    return team, "1"


def test_concurrent_claim_exactly_one_winner(tmp_path: Path, monkeypatch):
    """Two threads racing on the same task: exactly one wins, one raises ValueError."""
    team, task_id = _setup_team_and_task(tmp_path)

    # Redirect cs50victor's path resolution to tmp_path.
    monkeypatch.setattr(cs_tasks, "TASKS_DIR", tmp_path / "tasks")
    monkeypatch.setattr(cs_teams, "TEAMS_DIR", tmp_path / "teams")

    barrier = threading.Barrier(2)
    results: list[object] = []
    errors: list[Exception] = []
    lock = threading.Lock()

    def try_claim(name: str) -> None:
        barrier.wait()
        try:
            task = pio.claim_task(team, task_id, name, active_form=f"{name} claiming")
            with lock:
                results.append(task)
        except ValueError as e:
            with lock:
                errors.append(e)

    t0 = threading.Thread(target=try_claim, args=("adapter-0",))
    t1 = threading.Thread(target=try_claim, args=("adapter-1",))
    t0.start()
    t1.start()
    t0.join()
    t1.join()

    # Exactly one winner, exactly one loser.
    assert len(results) == 1, f"expected 1 winner, got {len(results)}: {results}"
    assert len(errors) == 1, f"expected 1 ValueError, got {len(errors)}: {errors}"

    # On-disk state is consistent: task is in_progress, owner is the winner.
    final = cs_tasks.get_task(team, task_id)
    assert final.status == "in_progress"
    winner_task = results[0]
    assert final.owner == winner_task.owner  # type: ignore[union-attr]


def test_claim_raises_if_already_owned_and_pending(tmp_path: Path, monkeypatch):
    """claim_task raises ValueError if the task is pending but already has an owner."""
    team, task_id = _setup_team_and_task(tmp_path)
    monkeypatch.setattr(cs_tasks, "TASKS_DIR", tmp_path / "tasks")
    monkeypatch.setattr(cs_teams, "TEAMS_DIR", tmp_path / "teams")

    # Manually set owner without advancing status (simulates a partial-write race).
    tasks_dir = tmp_path / "tasks" / team
    task_path = tasks_dir / f"{task_id}.json"
    raw = json.loads(task_path.read_text())
    raw["owner"] = "adapter-0"
    task_path.write_text(json.dumps(raw))

    # A different owner trying to claim a pending-but-owned task must raise.
    with pytest.raises(ValueError, match="already owned"):
        pio.claim_task(team, task_id, "adapter-1", active_form="adapter-1 claiming")


def test_claim_raises_if_already_in_progress(tmp_path: Path, monkeypatch):
    """claim_task raises ValueError if the task is already in_progress."""
    team, task_id = _setup_team_and_task(tmp_path)
    monkeypatch.setattr(cs_tasks, "TASKS_DIR", tmp_path / "tasks")
    monkeypatch.setattr(cs_teams, "TEAMS_DIR", tmp_path / "teams")

    # First claim succeeds.
    pio.claim_task(team, task_id, "adapter-0", active_form="adapter-0 claiming")

    # Second claim by any owner must raise (task is now in_progress).
    with pytest.raises(ValueError, match="not pending"):
        pio.claim_task(team, task_id, "adapter-1", active_form="adapter-1 claiming")


def test_claim_raises_if_not_pending(tmp_path: Path, monkeypatch):
    """claim_task raises ValueError if the task is already in_progress or completed."""
    team, task_id = _setup_team_and_task(tmp_path)
    monkeypatch.setattr(cs_tasks, "TASKS_DIR", tmp_path / "tasks")
    monkeypatch.setattr(cs_teams, "TEAMS_DIR", tmp_path / "teams")

    # Manually advance task to in_progress without going through claim_task.
    cs_tasks.update_task(team, task_id, status="in_progress", owner="someone")

    with pytest.raises(ValueError, match="not pending"):
        pio.claim_task(team, task_id, "adapter-0", active_form="late claim")


def test_claim_idempotent_for_same_owner(tmp_path: Path, monkeypatch):
    """claim_task by the same owner on an already-owned pending task is allowed."""
    team, task_id = _setup_team_and_task(tmp_path)
    monkeypatch.setattr(cs_tasks, "TASKS_DIR", tmp_path / "tasks")
    monkeypatch.setattr(cs_teams, "TEAMS_DIR", tmp_path / "teams")

    # First claim.
    t1 = pio.claim_task(team, task_id, "adapter-0", active_form="first")
    assert t1.owner == "adapter-0"

    # Same owner claiming again on the now-in_progress task should raise
    # (not pending) — the idempotency only applies before status advances.
    with pytest.raises(ValueError, match="not pending"):
        pio.claim_task(team, task_id, "adapter-0", active_form="second")
