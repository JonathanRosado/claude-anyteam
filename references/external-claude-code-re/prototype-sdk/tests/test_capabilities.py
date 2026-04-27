from __future__ import annotations

from agent_teams_kit.capabilities import CapabilityManifest, flat_capabilities, peer_prompt_fragment, validate_manifest
from agent_teams_kit.events import VisibilityEvent


CARD = {
    "schema_version": 1,
    "harness": "codex-cli",
    "harness_version": "mock",
    "transport": "app-server",
    "capabilities": {
        "turn_steer": {
            "version": "1",
            "schema": {"type": "object"},
            "description": "inject steering",
            "when_to_use": "when work drifts",
            "when_not_to": "when no turn is active",
            "failure_modes": ["RACE_LOST_NO_TURN_IN_FLIGHT"],
            "accepts_peer_steer": True,
        }
    },
}


def test_capability_manifest_validates_and_flattens():
    manifest = CapabilityManifest.model_validate(CARD)
    assert manifest.flat_capabilities() == ["turn_steer"]
    assert flat_capabilities(CARD) == ["turn_steer"]
    normalized = validate_manifest(CARD)
    assert normalized["capabilities"]["turn_steer"]["schema"]["type"] == "object"


def test_peer_prompt_fragment_contains_semantic_guidance():
    fragment = peer_prompt_fragment("codex-1", CARD)
    assert "When to use: when work drifts" in fragment
    assert "When not to: when no turn is active" in fragment
    assert "RACE_LOST_NO_TURN_IN_FLIGHT" in fragment


def test_event_envelope_serialization_round_trip():
    event = VisibilityEvent(
        kind="tool_event",
        team="t",
        agent="a",
        summary="Bash completed",
        payload={"tool_name": "Bash", "phase": "completed"},
    )
    restored = VisibilityEvent.model_validate_json(event.json_line())
    assert restored.schema_version == 1
    assert restored.kind == "tool_event"
    assert restored.payload["tool_name"] == "Bash"
