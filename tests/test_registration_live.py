"""Live-filesystem integration tests for registration.

These target the real `~/.claude/teams/claude-anyteam/` location when it
exists, but bootstrap a minimal fixture team when it does not so the suite
remains self-contained. Each test adds a uniquely-named fixture, asserts
state, then removes it. Existing team members (including `test-peer`) are not
touched.

Run via:  uv run pytest -v tests/test_registration_live.py
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from claude_anyteam.config import Settings
from claude_anyteam.registration import (
    config_path,
    deregister,
    inbox_path,
    register,
)

TEAM = "claude-anyteam"


def _seed_team_config() -> dict:
    return {
        "name": TEAM,
        "leadAgentId": f"lead@{TEAM}",
        "leadSessionId": "fixture-lead-session",
        "createdAt": 0,
        "members": [
            {
                "agentId": f"test-peer@{TEAM}",
                "name": "test-peer",
                "color": "magenta",
                "joinedAt": 0,
                "tmuxPaneId": "in-process",
                "subscriptions": [],
                "agentType": "claude-teammate",
                "model": "claude-code",
                "prompt": "Fixture peer for registration integration tests.",
                "planModeRequired": False,
                "cwd": str(Path.cwd().resolve()),
                "backendType": "in-process",
            }
        ],
    }


def _fixture_name() -> str:
    return f"m1-test-{os.getpid()}"


def _settings(name: str) -> Settings:
    return Settings(
        team_name=TEAM,
        agent_name=name,
        cwd=Path.cwd().resolve(),
        poll_interval_s=1.5,
        color="cyan",
        plan_mode_required=False,
        codex_binary="codex",
    )


def _member_names(cfg_path: Path) -> list[str]:
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    return [m.get("name") for m in cfg["members"] if isinstance(m, dict)]


@pytest.fixture(scope="module", autouse=True)
def ensure_fixture_team():
    cfg_path = config_path(TEAM)
    team_root = cfg_path.parent
    inbox_root = team_root / "inboxes"
    created_config = False
    created_team_root = False

    if not cfg_path.exists():
        created_config = True
        created_team_root = not team_root.exists()
        inbox_root.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(json.dumps(_seed_team_config(), indent=2) + "\n", encoding="utf-8")

    yield

    if created_team_root:
        shutil.rmtree(team_root, ignore_errors=True)
        return

    if created_config and cfg_path.exists():
        cfg_path.unlink()

    lock_path = inbox_root / ".lock"
    if created_config and lock_path.exists():
        lock_path.unlink()

    if created_config and inbox_root.exists() and not any(inbox_root.iterdir()):
        inbox_root.rmdir()


@pytest.fixture
def fixture_name():
    name = _fixture_name()
    settings = _settings(name)
    yield name
    # Teardown: remove the member if still present.
    deregister(settings)
    # Teardown inbox file too, to avoid accumulating fixtures.
    ibx = inbox_path(TEAM, name)
    if ibx.exists():
        ibx.unlink()


def test_register_adds_entry(fixture_name: str):
    cfg_path = config_path(TEAM)
    assert cfg_path.exists(), "fixture setup should ensure a team config exists"

    before_names = _member_names(cfg_path)
    assert fixture_name not in before_names

    entry = register(_settings(fixture_name))
    assert entry["name"] == fixture_name
    assert entry["agentId"] == f"{fixture_name}@{TEAM}"
    assert entry["backendType"] == "in-process"
    assert entry["tmuxPaneId"] == "in-process"
    assert entry["planModeRequired"] is False

    after_names = _member_names(cfg_path)
    # Other members preserved.
    assert set(before_names).issubset(set(after_names))
    # New entry present.
    assert fixture_name in after_names

    # Inbox file is an empty JSON array.
    ibx = inbox_path(TEAM, fixture_name)
    assert ibx.exists()
    assert json.loads(ibx.read_text(encoding="utf-8")) == []


def test_register_is_idempotent(fixture_name: str):
    register(_settings(fixture_name))
    cfg_path = config_path(TEAM)
    after_first = _member_names(cfg_path)
    count_first = after_first.count(fixture_name)
    assert count_first == 1

    # Second call must not duplicate, must not raise.
    register(_settings(fixture_name))
    after_second = _member_names(cfg_path)
    assert after_second.count(fixture_name) == 1


def test_deregister_removes_entry(fixture_name: str):
    register(_settings(fixture_name))
    cfg_path = config_path(TEAM)
    assert fixture_name in _member_names(cfg_path)

    removed = deregister(_settings(fixture_name))
    assert removed is True
    assert fixture_name not in _member_names(cfg_path)

    # Second deregister is a no-op.
    removed_again = deregister(_settings(fixture_name))
    assert removed_again is False


def test_deregister_also_removes_inbox_file(fixture_name: str):
    """Symmetry test: register creates the inbox; deregister deletes it.

    Observed as a hygiene gap in the first live M3 shutdown run — the
    adapter left `inboxes/codex-alice.json` on disk after deregister. This
    asserts the inbox file is cleaned up so future task #5 runs don't
    accumulate stale fixtures.
    """
    register(_settings(fixture_name))
    ibx = inbox_path(TEAM, fixture_name)
    assert ibx.exists()

    deregister(_settings(fixture_name))
    assert not ibx.exists(), "deregister should delete the inbox file"


def test_register_preserves_other_members(fixture_name: str):
    """Guard against regressions that would drop existing entries (e.g., test-peer)
    when we rewrite the config.
    """
    cfg_path = config_path(TEAM)
    before = json.loads(cfg_path.read_text(encoding="utf-8"))
    before_names = [m["name"] for m in before["members"] if isinstance(m, dict)]

    register(_settings(fixture_name))
    after = json.loads(cfg_path.read_text(encoding="utf-8"))
    after_names = [m["name"] for m in after["members"] if isinstance(m, dict)]

    for name in before_names:
        assert name in after_names, f"pre-existing member {name!r} was dropped"

    # Non-member fields preserved too.
    for key in ("name", "leadAgentId", "leadSessionId", "createdAt"):
        assert before[key] == after[key], f"top-level field {key!r} was mutated"
