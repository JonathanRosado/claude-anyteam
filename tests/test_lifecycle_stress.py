"""Lifecycle edge-case regression test for the team protocol implementation."""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_teams import teams


SESSION_ID = "stress-test-session-001"


def _base(tmp_path: Path) -> Path:
    (tmp_path / "teams").mkdir()
    (tmp_path / "tasks").mkdir()
    return tmp_path


def test_team_lifecycle_edge_cases(tmp_path: Path) -> None:
    base_dir = _base(tmp_path)

    for bad_name in ["", "test!@#$%^&*()", "my team name", "test-unicode-🚀", "test.dotted.name"]:
        with pytest.raises(ValueError):
            teams.create_team(name=bad_name, session_id=SESSION_ID, base_dir=base_dir)

    with pytest.raises(ValueError, match="too long"):
        teams.create_team(name="a" * 500, session_id=SESSION_ID, base_dir=base_dir)

    created = teams.create_team(
        name="stress-test-lifecycle-1",
        session_id=SESSION_ID,
        base_dir=base_dir,
    )
    assert created.team_name == "stress-test-lifecycle-1"

    duplicate = teams.create_team(
        name="stress-test-lifecycle-1",
        session_id=SESSION_ID,
        base_dir=base_dir,
    )
    assert duplicate.team_name == "stress-test-lifecycle-1"

    with pytest.raises(FileNotFoundError):
        teams.read_config(name="nonexistent-team-xyz", base_dir=base_dir)

    cfg = teams.read_config(name="stress-test-lifecycle-1", base_dir=base_dir)
    assert cfg.name == "stress-test-lifecycle-1"

    with pytest.raises(FileNotFoundError):
        teams.delete_team(name="nonexistent-team-xyz", base_dir=base_dir)

    deleted = teams.delete_team(name="stress-test-lifecycle-1", base_dir=base_dir)
    assert deleted.success is True

    with pytest.raises(FileNotFoundError):
        teams.delete_team(name="stress-test-lifecycle-1", base_dir=base_dir)

    leading = teams.create_team(
        name="-leading-hyphen",
        session_id=SESSION_ID,
        base_dir=base_dir,
    )
    assert leading.team_name == "-leading-hyphen"

    numeric = teams.create_team(name="12345", session_id=SESSION_ID, base_dir=base_dir)
    assert numeric.team_name == "12345"
