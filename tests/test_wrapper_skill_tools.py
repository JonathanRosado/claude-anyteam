"""Wrapper MCP skill-as-tool PoC tests."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from claude_anyteam import protocol_io
from claude_anyteam.wrapper_server import build_server


@pytest.fixture
def isolated_wrapper(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_ANYTEAM_TEAM", "claude-anyteam")
    monkeypatch.setenv("CLAUDE_ANYTEAM_NAME", "skill-tool-test")
    team_root = tmp_path / "teams"
    monkeypatch.setattr("claude_anyteam.wrapper_server._cs_teams.TEAMS_DIR", team_root)
    monkeypatch.setattr("claude_anyteam.wrapper_server._cs_messaging.TEAMS_DIR", team_root)
    monkeypatch.setattr("claude_anyteam.protocol_io._m.TEAMS_DIR", team_root)
    return build_server()


@pytest.fixture
def captured_events(monkeypatch):
    """Capture every wrapper visibility event the server appends.

    Returns a list that the wrapper populates via the patched
    ``protocol_io.append_event``. Each entry is the original ``VisibilityEvent``
    so tests can assert on ``payload`` shape directly.
    """

    events: list = []

    def _capture(team, agent, event):
        events.append(event)

    monkeypatch.setattr(protocol_io, "append_event", _capture)
    return events


def _call_tool(mcp, name: str, arguments: dict):
    content = asyncio.run(mcp.call_tool(name, arguments)).structured_content
    if isinstance(content, dict) and set(content) == {"result"}:
        return content["result"]
    return content


def _skill_events(events, *, phase: str | None = None):
    matched = []
    for event in events:
        payload = event.payload or {}
        if payload.get("tool_name") != "mcp_anyteam_invoke_skill":
            continue
        if phase is not None and payload.get("phase") != phase:
            continue
        matched.append(event)
    return matched


def test_list_skills_returns_in_repo_help_diagnose_status(isolated_wrapper):
    skills = _call_tool(isolated_wrapper, "mcp_anyteam_list_skills", {})
    by_name = {entry["name"]: entry for entry in skills}

    assert {"help", "diagnose", "status"} <= set(by_name)
    for skill_name in ("help", "diagnose", "status"):
        assert by_name[skill_name]["source_path"].endswith(f"skills/{skill_name}/SKILL.md")
        assert by_name[skill_name]["description"]


def test_invoke_skill_returns_diagnose_body_and_source_path(isolated_wrapper):
    repo_root = Path(__file__).resolve().parents[1]
    expected_path = (repo_root / "skills" / "diagnose" / "SKILL.md").resolve()
    expected_body = expected_path.read_text(encoding="utf-8")

    result = _call_tool(
        isolated_wrapper,
        "mcp_anyteam_invoke_skill",
        {"skill_name": "diagnose"},
    )

    assert result == {
        "skill_name": "diagnose",
        "body": expected_body,
        "source_path": str(expected_path),
    }


def test_invoke_missing_skill_returns_typed_error(isolated_wrapper):
    result = _call_tool(
        isolated_wrapper,
        "mcp_anyteam_invoke_skill",
        {"skill_name": "nonexistent"},
    )

    assert result == {"error": "skill_not_found", "skill_name": "nonexistent"}


def test_invoke_skill_event_payload_carries_typed_skill_name(
    isolated_wrapper, captured_events
):
    """Both start and complete events must carry ``payload.skill_name`` as a typed field.

    Regression: PR #47 review v1 surfaced that events only encoded the skill
    in a formatted ``target`` string, forcing peers to parse prose. §2
    visibility parity requires the typed field for audit-loggable peers.
    """

    _call_tool(
        isolated_wrapper,
        "mcp_anyteam_invoke_skill",
        {"skill_name": "diagnose"},
    )

    started = _skill_events(captured_events, phase="started")
    completed = _skill_events(captured_events, phase="completed")

    assert started, "expected a `started` tool_event for the skill invoke"
    assert completed, "expected a `completed` tool_event for the skill invoke"

    for event in started + completed:
        payload = event.payload or {}
        assert payload.get("skill_name") == "diagnose", (
            f"event {event.event_id} missing typed payload.skill_name; "
            f"got payload={payload!r}"
        )


def test_invoke_missing_skill_emits_failed_event_with_error(
    isolated_wrapper, captured_events
):
    """``skill_not_found`` must surface as a typed failed event, not silent success.

    Regression: PR #47 review v1 noted that ``skill_not_found`` returned to
    the caller correctly but the wrapper-boundary event reported
    ``status=success`` with no error detail. §2 visibility parity needs
    failures to be visible at the event boundary so peers/lead see the miss.
    """

    _call_tool(
        isolated_wrapper,
        "mcp_anyteam_invoke_skill",
        {"skill_name": "definitely-not-a-skill"},
    )

    failed = _skill_events(captured_events, phase="failed")
    completed = _skill_events(captured_events, phase="completed")

    assert failed, (
        "expected a `failed` tool_event for skill_not_found; events: "
        + json.dumps([e.payload for e in captured_events], default=str)
    )
    assert not completed, "skill_not_found must NOT be tagged as completed/success"

    payload = failed[0].payload or {}
    assert payload.get("status") == "error"
    assert payload.get("skill_name") == "definitely-not-a-skill"
    assert payload.get("error") == "skill_not_found"
