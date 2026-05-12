"""Regression tests for the wrapper-MCP ``task_blocked.reason`` drift
warn (#40 Phase 1, graduated rung 1+2).

Per product-steward's requirement: stable token registry + wrapper-side
warn for token-shaped reasons that aren't registered, but never block
delivery. The §2 invariant: a typo in a stable token surfaces as a
``visibility_degraded`` warn so the lead sees it; free-form prose
reasons remain legal.

These tests pin:

1. Registered reason → no warn, payload delivered.
2. Token-shaped unregistered reason → warn emitted, payload still delivered.
3. Free-form prose reason → no warn (preserves backward compat for
   existing reasons like "codex invocation crashed: ...").
4. Both new typed tokens (``app_server_initialize_timeout`` and
   ``app_server_shutdown_timeout``) are in the registry.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from claude_teams.models import LeadMember, TeammateMember

from claude_anyteam import protocol_io as pio
from claude_anyteam.messages import (
    KNOWN_TASK_BLOCKED_REASONS,
    is_token_shaped_reason,
)
from claude_anyteam.wrapper_server import build_server


def _member(name: str, color: str) -> TeammateMember:
    return TeammateMember(
        agentId=f"{name}@claude-anyteam",
        name=name,
        agentType="claude-anyteam",
        model="codex-cli",
        prompt="test",
        color=color,
        joinedAt=0,
        tmuxPaneId="pane",
        cwd="/tmp",
        backendType="in-process",
    )


def _lead() -> LeadMember:
    return LeadMember(
        agentId="team-lead@claude-anyteam",
        name="team-lead",
        agentType="team-lead",
        model="claude-opus",
        joinedAt=0,
        tmuxPaneId="",
        cwd="/tmp",
    )


@pytest.fixture
def identity(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Wrapper identity + team config matching the
    ``test_wrapper_contract.py`` fixture pattern.
    """
    monkeypatch.setenv("CLAUDE_ANYTEAM_TEAM", "claude-anyteam")
    monkeypatch.setenv("CLAUDE_ANYTEAM_NAME", "contract-test")
    team_root = tmp_path / "teams"
    monkeypatch.setattr(
        "claude_anyteam.wrapper_server._cs_messaging.TEAMS_DIR", team_root
    )
    monkeypatch.setattr("claude_anyteam.protocol_io._m.TEAMS_DIR", team_root)

    team_dir = team_root / "claude-anyteam"
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "inboxes").mkdir(exist_ok=True)
    config = {
        "name": "claude-anyteam",
        "createdAt": 0,
        "leadAgentId": "team-lead@claude-anyteam",
        "leadSessionId": "lead-session",
        "members": [
            _lead().model_dump(by_alias=True, exclude_none=True),
            _member("contract-test", "magenta").model_dump(
                by_alias=True, exclude_none=True
            ),
            _member("peer-a", "blue").model_dump(
                by_alias=True, exclude_none=True
            ),
        ],
    }
    (team_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
    (team_dir / "inboxes" / "contract-test.json").write_text(
        "[]", encoding="utf-8"
    )
    monkeypatch.setattr(
        "claude_anyteam.wrapper_server._cs_teams.TEAMS_DIR", team_root
    )
    return team_root


def _call(mcp, name: str, arguments: dict) -> dict:
    return asyncio.run(mcp.call_tool(name, arguments)).structured_content


def _drift_warns() -> list:
    """Read warn events emitted by the wrapper for our identity."""
    events = pio.read_visibility_events("claude-anyteam", "contract-test")
    return [
        e
        for e in events
        if e.kind == "visibility_degraded"
        and e.payload.get("surface") == "task_blocked_unknown_reason"
    ]


def _peer_a_inbox(team_root: Path) -> list[dict]:
    path = team_root / "claude-anyteam" / "inboxes" / "peer-a.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def test_token_shape_recognises_snake_case() -> None:
    """Pin: ``is_token_shaped_reason`` matches snake_case identifiers,
    not prose. The wrapper-MCP validator gate uses this regex to decide
    whether a value is a "token attempt" worth policing.
    """
    assert is_token_shaped_reason("app_server_initialize_timeout") is True
    assert is_token_shaped_reason("plan_blocked_after_retry") is True

    # Free-form prose / mixed case / spaces / colons all fail the gate.
    assert is_token_shaped_reason("codex invocation crashed: foo") is False
    assert is_token_shaped_reason("Codex Crashed") is False
    assert is_token_shaped_reason("plan generation failed twice") is False
    assert is_token_shaped_reason("app_server_initialize_timeout: 90s") is False


def test_registered_reason_is_delivered_without_warn(identity: Path) -> None:
    """Pin: the new ``app_server_initialize_timeout`` token passes
    through cleanly — no drift warn fired, payload reaches peer's inbox.
    """
    mcp = build_server()
    body = json.dumps(
        {
            "kind": "task_blocked",
            "task_id": "42",
            "reason": "app_server_initialize_timeout",
        }
    )

    _call(
        mcp,
        "send_message",
        {
            "to": "peer-a",
            "body": body,
            "summary": "blocked",
            "kind": "task_blocked",
        },
    )

    inbox = _peer_a_inbox(identity)
    assert len(inbox) == 1
    payload = json.loads(inbox[0]["text"])
    assert payload["reason"] == "app_server_initialize_timeout"
    assert payload["task_id"] == "42"

    assert _drift_warns() == [], (
        "Registered token must NOT trigger a drift warn — only "
        "unregistered token-shaped values should."
    )


def test_token_shaped_unregistered_reason_emits_warn_but_delivers(
    identity: Path,
) -> None:
    """Pin: the §2 invariant — drift surfaces, doesn't break delivery.

    A typo or unregistered new token is exactly what the wrapper-MCP
    drift warn is designed to catch. The payload still reaches the
    recipient (don't break the team), but a ``visibility_degraded``
    event lands in the lead's event log so the gap is observable.
    """
    mcp = build_server()
    body = json.dumps(
        {
            "kind": "task_blocked",
            "task_id": "42",
            "reason": "app_server_inititialize_timeout",  # typo!
        }
    )

    _call(
        mcp,
        "send_message",
        {
            "to": "peer-a",
            "body": body,
            "summary": "blocked",
            "kind": "task_blocked",
        },
    )

    # Delivery preserved.
    inbox = _peer_a_inbox(identity)
    assert len(inbox) == 1
    delivered_payload = json.loads(inbox[0]["text"])
    assert delivered_payload["reason"] == "app_server_inititialize_timeout"

    # Drift surfaced.
    warns = _drift_warns()
    assert len(warns) == 1, f"Expected 1 drift warn, got {len(warns)}"
    warn = warns[0]
    assert warn.severity == "warn"
    assert warn.payload["reason"] == "app_server_inititialize_timeout"
    assert (
        "app_server_initialize_timeout"
        in warn.payload["registered_reasons"]
    )
    assert warn.visibility.mailbox is True


def test_free_form_prose_reason_is_silent(identity: Path) -> None:
    """Pin: free-form prose reasons (existing legacy behavior) are NOT
    policed. ``"codex invocation crashed: AbcdError(...)"`` etc. pass
    through silently — the gate only fires on token-shaped values.
    """
    mcp = build_server()
    body = json.dumps(
        {
            "kind": "task_blocked",
            "task_id": "42",
            "reason": "codex invocation crashed: BrokenPipeError(EOF)",
        }
    )

    _call(
        mcp,
        "send_message",
        {
            "to": "peer-a",
            "body": body,
            "summary": "blocked",
            "kind": "task_blocked",
        },
    )

    inbox = _peer_a_inbox(identity)
    assert len(inbox) == 1
    assert "BrokenPipeError" in json.loads(inbox[0]["text"])["reason"]

    assert _drift_warns() == [], (
        "Free-form prose reasons must NOT trigger drift warns; the "
        "registry only polices snake_case token-shaped values."
    )


def test_registry_includes_visibility_action_tokens() -> None:
    """Pin: both #40 Phase 1 tokens are in the registry. If they were
    missing, every legitimate task_blocked emission from the codex loop
    would trigger a drift warn — defeating the §2 invariant.
    """
    assert "app_server_initialize_timeout" in KNOWN_TASK_BLOCKED_REASONS
    assert "app_server_shutdown_timeout" in KNOWN_TASK_BLOCKED_REASONS
    assert "wrapper_tool_failure_unrecovered" in KNOWN_TASK_BLOCKED_REASONS


def test_wrapper_tool_failure_signal_is_not_terminal_error_class() -> None:
    from claude_anyteam.diagnostics import ERROR_CLASSES

    assert "wrapper_tool_failure_unrecovered" not in ERROR_CLASSES


def test_recipient_parses_typed_task_blocked_via_discriminator(
    identity: Path,
) -> None:
    """Pin: lead-side / peer-side recipients filter on
    ``task_blocked.reason``, NOT on prose. Per
    ``feedback_recipient_defined_concepts``: the wrapper-layer drift
    warn only matters if recipients actually read the typed reason
    field. This test asserts the recipient-side parse path returns a
    structured ``TaskBlockedOut`` whose ``reason`` matches the
    registered token — protecting against a future "simplification"
    that collapses the handler back to prose-parsing.
    """
    from claude_anyteam.messages import TaskBlockedOut, parse_protocol_text

    mcp = build_server()
    body = json.dumps(
        {
            "kind": "task_blocked",
            "task_id": "42",
            "reason": "app_server_initialize_timeout",
        }
    )
    _call(
        mcp,
        "send_message",
        {
            "to": "peer-a",
            "body": body,
            "summary": "blocked",
            "kind": "task_blocked",
        },
    )

    # Recipient picks up the message and parses it via the same path
    # the lead's loop uses.
    inbox = _peer_a_inbox(identity)
    assert len(inbox) == 1
    received = inbox[0]
    assert received["messageKind"] == "task_blocked"

    parsed = parse_protocol_text(received["text"])
    assert isinstance(parsed, TaskBlockedOut), (
        "Recipient must parse the typed payload via the structured "
        f"protocol path, got {type(parsed).__name__}"
    )
    assert parsed.reason == "app_server_initialize_timeout", (
        "Recipient reads the typed `reason` discriminator field, not "
        "prose substring matching."
    )
    assert parsed.task_id == "42"
    assert parsed.kind == "task_blocked"
