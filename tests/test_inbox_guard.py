"""Guard against the mark_as_read data-loss landmine.

cs50victor's `read_inbox(..., mark_as_read=True)` rewrites the entire inbox
file using its own Pydantic serializer. If the adapter ever calls that path
on another teammate's inbox (typo in agent name, copy-paste bug, etc.), any
harness-written fields not modeled by cs50victor would be stripped. This
could destroy real messages the lead hasn't read yet.

`protocol_io.read_own_inbox(team, self_name, agent_name)` asserts
`self_name == agent_name`. This test proves the guard fires.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from claude_teams import messaging as cs_messaging  # type: ignore[import-untyped]

from claude_anyteam import protocol_io


@pytest.fixture
def team_env(tmp_path: Path, monkeypatch):
    base = tmp_path / "home" / ".claude" / "teams"
    monkeypatch.setattr(cs_messaging, "TEAMS_DIR", base)
    yield base


def test_read_own_inbox_allows_self(team_env: Path):
    """Happy path: when self_name == agent_name, the call proceeds and
    returns messages normally."""
    cs_messaging.send_plain_message(
        "team-a",
        from_name="team-lead",
        to_name="codex-me",
        text="hi",
        summary="s",
    )
    msgs = protocol_io.read_own_inbox("team-a", self_name="codex-me", agent_name="codex-me")
    assert len(msgs) == 1
    assert msgs[0].text == "hi"


def test_read_own_inbox_rejects_other_inbox(team_env: Path):
    """Guard path: passing someone else's name must raise."""
    with pytest.raises(AssertionError, match="refusing to mark_as_read"):
        protocol_io.read_own_inbox(
            "team-a", self_name="codex-me", agent_name="team-lead"
        )


def test_read_own_inbox_guard_prevents_harness_field_loss(team_env: Path):
    """End-to-end proof of the landmine: if we *had* no guard, calling
    cs50victor's read_inbox with mark_as_read=True on a foreign inbox
    containing harness-only fields would strip them. This test shows the
    damage is real (the raw call loses fields) and that our guard would
    prevent it."""
    # Seed an inbox with a message that includes a hypothetical future
    # harness field cs50victor doesn't know about.
    inbox = team_env / "team-a" / "inboxes" / "victim.json"
    inbox.parent.mkdir(parents=True, exist_ok=True)
    inbox.write_text(
        json.dumps(
            [
                {
                    "from": "team-lead",
                    "text": "hello",
                    "timestamp": "2026-04-21T00:00:00.000Z",
                    "read": False,
                    "summary": "s",
                    "future_harness_field": "critical-metadata-we-must-not-lose",
                }
            ]
        )
    )

    # Demonstrate that the unguarded cs50victor call is destructive.
    cs_messaging.read_inbox("team-a", "victim", unread_only=False, mark_as_read=True)
    after = json.loads(inbox.read_text())
    assert after[0]["read"] is True
    # The critical fact: cs50victor's Pydantic roundtrip dropped the unknown field.
    assert "future_harness_field" not in after[0], (
        "cs50victor preserves unknown fields now; the landmine may be mitigated "
        "upstream and this guard could be relaxed — re-evaluate."
    )

    # Re-seed the inbox for the guard check.
    inbox.write_text(
        json.dumps(
            [
                {
                    "from": "team-lead",
                    "text": "hello",
                    "timestamp": "2026-04-21T00:00:00.000Z",
                    "read": False,
                    "summary": "s",
                    "future_harness_field": "critical-metadata-we-must-not-lose",
                }
            ]
        )
    )

    # The guard must refuse to touch this inbox if it's not ours.
    with pytest.raises(AssertionError):
        protocol_io.read_own_inbox("team-a", self_name="codex-me", agent_name="victim")

    # And crucially, the inbox file must be unchanged after the refusal.
    after_guard = json.loads(inbox.read_text())
    assert after_guard[0]["future_harness_field"] == "critical-metadata-we-must-not-lose"
    assert after_guard[0]["read"] is False
