"""Canonical capability declarations for routed teammate registration.

R11 in ``docs/internal/protocol-rev/09-implementation-roadmap.md`` adds the
flat ``members[].capabilities`` roster layer. 08 §6.3 defines the
Agent Card ``capabilities()`` hook this cheap list is derived from; R12/R13
add the rich manifest that the wrapper MCP returns from cache.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from claude_teams.coupling import declaration_for_regime

CAPABILITY_NAMES = frozenset(
    {
        "turn_steer",
        "thread_fork",
        "permission_bridge",
        "live_tool_events",
        "structured_output",
        "large_context",
        "accepts_peer_steer",
        "native_swarm",
        "soft_non_progress_watchdog",
    }
)


CODEX_APP_SERVER_CAPABILITIES = [
    "turn_steer",
    "thread_fork",
    "live_tool_events",
    "structured_output",
    "soft_non_progress_watchdog",
    # Q4 (per opus-arch-impl): Codex App Server is deliberately lead-only
    # for peer steer until the handler and runtime behavior are re-reviewed.
]

CODEX_EXEC_CAPABILITIES = ["structured_output"]

GEMINI_ACP_CAPABILITIES = [
    # ACP delivery is wired at the next turn boundary today, but R11 declares
    # this cheap flag ahead of the richer R12/R13 manifest's delivery-mode
    # detail so peers can discover that steer exists at all.
    "turn_steer",
    "permission_bridge",
    "live_tool_events",
    "accepts_peer_steer",
]

GEMINI_HEADLESS_CAPABILITIES: list[str] = []

KIMI_HEADLESS_CAPABILITIES = ["large_context", "native_swarm"]

CAPABILITY_MANIFEST_SCHEMA_VERSION = 1
CAPABILITY_MANIFEST_VERSION = "2"

_CAPABILITY_DISPLAY_NAMES = {
    "turn_steer": "turn/steer (`turn_steer`)",
    "thread_fork": "thread/fork (`thread_fork`)",
    "permission_bridge": "permission bridge (`permission_bridge`)",
    "live_tool_events": "live tool events (`live_tool_events`)",
    "structured_output": "structured output (`structured_output`)",
    "large_context": "large context (`large_context`)",
    "accepts_peer_steer": "peer steer acceptance (`accepts_peer_steer`)",
    "native_swarm": "native swarm (`native_swarm`)",
    "soft_non_progress_watchdog": "soft non-progress watchdog (`soft_non_progress_watchdog`)",
}

_BASE_CAPABILITY_MANIFEST: dict[str, dict[str, Any]] = {
    "turn_steer": {
        "version": "1",
        "schema": {
            "type": "object",
            "required": ["text"],
            "properties": {
                "text": {"type": "string", "maxLength": 8192},
                "task_id": {"type": ["string", "null"]},
                "priority": {"type": "string", "enum": ["normal", "urgent"], "default": "normal"},
                "expires_after_turns": {"type": "integer", "minimum": 1, "default": 1},
            },
        },
        "description": "Inject text mid-turn or at a turn boundary to redirect a teammate's reasoning.",
        "when_to_use": (
            "Use when you see the teammate pursuing a stale path, when a peer discovers a "
            "constraint that must be applied before the task continues, or when context should "
            "arrive without restarting the task."
        ),
        "when_not_to": (
            "Do not steer with low-information nudges such as 'are you done?', and avoid "
            "steering while a structured tool call depends on stable inputs."
        ),
        "failure_modes": [
            "RACE_LOST_NO_TURN_IN_FLIGHT",
            "STEER_BUFFERED_NEXT_BOUNDARY",
            "STEER_AUTH_REJECTED",
            "STEER_PAYLOAD_OVERFLOW",
        ],
    },
    "thread_fork": {
        "version": "1",
        "schema": {
            "type": "object",
            "properties": {
                "parent_thread_id": {"type": "string"},
                "task_id": {"type": ["string", "null"]},
            },
        },
        "description": "Fork a persisted Codex App Server thread so future work inherits prior-task context.",
        "when_to_use": (
            "Ask a Codex App Server teammate to use thread continuity when a follow-up task "
            "depends on substantial context from its previous task."
        ),
        "when_not_to": (
            "Do not depend on thread_fork for stateless one-shot tasks, or after a failed turn "
            "whose context should not poison the next attempt."
        ),
        "failure_modes": ["PARENT_THREAD_NOT_MATERIALIZED", "FORK_UNSUPPORTED", "FORK_CONTEXT_STALE"],
        "callable_from_peers": True,
    },
    "permission_bridge": {
        "version": "1",
        "schema": {
            "type": "object",
            "required": ["request_id", "decision"],
            "properties": {
                "request_id": {"type": "string"},
                "decision": {"type": "string", "enum": ["allow_once", "allow_session", "deny"]},
                "reason": {"type": "string"},
            },
        },
        "description": "Surface sensitive host-tool use to team-lead for interactive approval before execution.",
        "when_to_use": (
            "Route approval-sensitive work here when tasks touch production paths, secrets, "
            "external networks, or other operations the lead wants explicitly gated."
        ),
        "when_not_to": (
            "Do not route routine read-only or simple test tasks here solely for the bridge; "
            "approval prompts add latency without value."
        ),
        "failure_modes": [
            "APPROVAL_TIMEOUT",
            "APPROVAL_BRIDGE_ERROR",
            "APPROVAL_CONTEXT_MISSING",
            "DENIED_BY_TEAM_LEAD",
        ],
        "callable_from_peers": False,
    },
    "live_tool_events": {
        "version": "1",
        "schema": {"type": "object", "additionalProperties": True},
        "description": "Emit or expose host-tool activity while a turn is running rather than only at task completion.",
        "when_to_use": "Prefer this teammate when peers or the lead need live operational visibility into long-running work.",
        "when_not_to": "Not directly callable; this is an observability signal for routing and expectations.",
        "failure_modes": ["TOOL_EVENT_STREAM_DEGRADED", "HOST_EVENT_SHAPE_CHANGED"],
        "callable_from_peers": False,
    },
    "structured_output": {
        "version": "1",
        "schema": {"type": "object", "required": ["files_changed", "summary"]},
        "description": "Return schema-validated task-complete JSON with files_changed and summary fields.",
        "when_to_use": "Use for coding tasks where the lead needs machine-readable completion metadata.",
        "when_not_to": "Not directly callable by peers; it describes final-output fidelity.",
        "failure_modes": ["SCHEMA_VALIDATION_FAILED", "OUTPUT_SCHEMA_UNSUPPORTED", "RETRY_EXHAUSTED"],
        "callable_from_peers": False,
    },
    "large_context": {
        "version": "1",
        "schema": {"type": "object", "properties": {"context_tokens": {"type": "integer", "minimum": 100000}}},
        "description": "Handle very large prompts or repositories with a context window above 100k tokens.",
        "when_to_use": "Route broad audits, large-file synthesis, or multi-document reasoning to this teammate.",
        "when_not_to": "Do not use large_context as a substitute for precise task scoping when a smaller teammate is faster.",
        "failure_modes": ["CONTEXT_TOO_LARGE", "CONTEXT_TRUNCATED", "MODEL_CONTEXT_POLICY_CHANGED"],
        "callable_from_peers": False,
    },
    "accepts_peer_steer": {
        "version": "1",
        "schema": {"type": "boolean"},
        "description": "Declare that non-lead peers may send steer messages to this teammate.",
        "when_to_use": "Check before steering another peer directly; if absent, route steer requests through team-lead.",
        "when_not_to": "Not an invocation primitive by itself; it is the authorization signal for turn_steer.",
        "failure_modes": ["PEER_STEER_REJECTED", "STEER_AUTH_REJECTED"],
        "callable_from_peers": False,
    },
    "native_swarm": {
        "version": "1",
        "schema": {"type": "object", "additionalProperties": True},
        "description": "Use the harness's native internal multi-agent or swarm features inside one teammate process.",
        "when_to_use": "Route broad exploration or parallelizable research to a teammate that can fan out internally.",
        "when_not_to": "Do not assume internal swarm activity is visible as separate team members or mailbox participants.",
        "failure_modes": ["SWARM_UNAVAILABLE", "SUBAGENT_LIMIT_REACHED", "SWARM_OUTPUT_COLLAPSED"],
        "callable_from_peers": False,
    },
    "soft_non_progress_watchdog": {
        "version": "1",
        "schema": {
            "type": "object",
            "properties": {
                "non_progress_warn_s": {
                    "type": "number",
                    "default": 300,
                    "minimum": 60,
                    "maximum": 900,
                },
                "non_progress_interrupt_s": {
                    "type": ["number", "null"],
                    "default": None,
                    "minimum": 60,
                    "maximum": 3600,
                },
            },
        },
        "description": (
            "Self-monitor Codex App Server turns and emit a turn_progress warning "
            "envelope when no visible checkpoint appears for the configured interval."
        ),
        "when_to_use": (
            "Prefer this teammate for long-running Codex App Server tasks where the "
            "lead needs a durable warning and checkpoint steer rather than waiting "
            "silently for the wall-clock timeout."
        ),
        "when_not_to": (
            "Not directly callable by peers, and not declared by Codex exec, "
            "Gemini, or Kimi; those backends lack the same App Server polling "
            "signal and should not pretend to support it."
        ),
        "failure_modes": [
            "WATCHDOG_WARNING_SENT",
            "WATCHDOG_STEER_FAILED",
            "WATCHDOG_INTERRUPT_SENT",
        ],
        "callable_from_peers": False,
    },
}


def assert_known_capabilities(capabilities: list[str]) -> list[str]:
    """Return a copy after asserting adapter-declared flags use the taxonomy."""
    unknown = sorted(set(capabilities) - CAPABILITY_NAMES)
    if unknown:
        raise ValueError(f"unknown capability flag(s): {', '.join(unknown)}")
    return list(capabilities)


def manifest_accepts_peer_steer(manifest: Any) -> bool:
    """Return whether a rich Agent Card authorizes non-lead peer steer.

    The protocol has carried this bit in a few compatible shapes while the
    R11/R12 split settled:

    - top-level ``accepts_peer_steer: true`` from prototype Agent Cards,
    - rich ``capabilities.accepts_peer_steer`` entries,
    - ``turn_steer.authorization == "any_peer"`` /
      ``turn_steer.callable_from_peers == true``.

    Explicit false wins over any looser shape.  A plain ``turn_steer`` entry
    by itself is not peer authorization; lead-only steer-capable backends also
    declare that primitive.
    """

    if not isinstance(manifest, dict):
        return False

    top_level = manifest.get("accepts_peer_steer")
    if isinstance(top_level, bool):
        return top_level

    capabilities = manifest.get("capabilities")
    if isinstance(capabilities, list):
        return "accepts_peer_steer" in capabilities
    if not isinstance(capabilities, dict):
        return False

    accepts = capabilities.get("accepts_peer_steer")
    if isinstance(accepts, bool):
        return accepts
    if isinstance(accepts, dict):
        if accepts.get("enabled") is False or accepts.get("value") is False:
            return False
        return True
    if accepts is not None:
        return True

    turn_steer = capabilities.get("turn_steer")
    if isinstance(turn_steer, dict):
        nested_accepts = turn_steer.get("accepts_peer_steer")
        if isinstance(nested_accepts, bool):
            return nested_accepts
        if turn_steer.get("authorization") == "any_peer":
            return True
        if turn_steer.get("callable_from_peers") is True:
            return True

    return False


def peer_steer_authorized(
    capabilities: list[str] | tuple[str, ...],
    manifest: Any | None = None,
) -> bool:
    """Return whether the recipient currently authorizes peer steer.

    When a rich manifest is available it is authoritative.  The cheap
    ``accepts_peer_steer`` flag remains a fallback for focused unit tests and
    older adapters that have not loaded an Agent Card yet.
    """

    if isinstance(manifest, dict):
        return manifest_accepts_peer_steer(manifest)
    return "accepts_peer_steer" in capabilities


def effective_peer_steer_capabilities(
    capabilities: list[str] | tuple[str, ...],
    manifest: Any | None = None,
) -> list[str]:
    """Return cheap capability flags with manifest peer-steer policy applied."""

    result = [cap for cap in capabilities if cap != "accepts_peer_steer"]
    if peer_steer_authorized(capabilities, manifest):
        result.append("accepts_peer_steer")
    return result


def rich_capability_manifest(
    capabilities: list[str],
    *,
    delivery_mode: str | None = None,
    expiry_semantics: str | None = None,
    steer_authorization: str | None = None,
    host_tool_surface: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Return R12 rich entries for the supplied R11 cheap capability list.

    The keys intentionally align one-for-one with ``CAPABILITY_NAMES`` and the
    per-backend ``*_CAPABILITIES`` constants so 09 R12 never invents a second
    naming layer on top of codex-impl-cap's R11 surface.
    """
    assert_known_capabilities(capabilities)
    result: dict[str, dict[str, Any]] = {}
    for name in capabilities:
        entry = deepcopy(_BASE_CAPABILITY_MANIFEST[name])
        if name == "turn_steer":
            if delivery_mode:
                entry["delivery_mode"] = delivery_mode
            if expiry_semantics:
                entry["expiry_semantics"] = expiry_semantics
            if steer_authorization:
                entry["authorization"] = steer_authorization
            entry["callable_from_peers"] = steer_authorization == "any_peer"
        if name == "live_tool_events" and host_tool_surface:
            entry["host_tool_surface"] = host_tool_surface
        result[name] = entry
    return result


def build_agent_card(
    *,
    team_name: str,
    agent_name: str,
    agent_id: str,
    agent_type: str,
    model: str,
    backend_type: str,
    capabilities: list[str],
    capability_manifest: dict[str, dict[str, Any]] | None = None,
    capability_version: str = CAPABILITY_MANIFEST_VERSION,
    transport: str | None = None,
    host_tool_surface: str | None = None,
    coupling_regime: str | None = None,
) -> dict[str, Any]:
    """Build the rich R12 Agent Card persisted under ``manifests/<agent>.json``."""
    entries = capability_manifest if capability_manifest is not None else rich_capability_manifest(capabilities)
    card = {
        "schema_version": CAPABILITY_MANIFEST_SCHEMA_VERSION,
        "capability_version": str(capability_version),
        "team_name": team_name,
        "agent_name": agent_name,
        "agent_id": agent_id,
        "agent_type": agent_type,
        "model": model,
        "backend_type": backend_type,
        "transport": transport or backend_type,
        "host_tool_surface": host_tool_surface,
        "capabilities": entries,
    }
    if coupling_regime is not None:
        regime, coupling = declaration_for_regime(coupling_regime)
        # Root-level field is the compact protocol declaration used by the
        # dispatcher conflict check. The canonical object preserves the
        # longer intent/sub-field shape used by scorers and workload manifests.
        card["coupling_regime"] = regime
        card["coupling"] = coupling
    return card


def _entry_text(entry: dict[str, Any], key: str) -> str:
    value = entry.get(key)
    return str(value).strip() if value is not None else ""


def _failure_modes_text(entry: dict[str, Any]) -> str:
    value = entry.get("failure_modes")
    if isinstance(value, list):
        modes = [str(v).strip() for v in value if str(v).strip()]
        return ", ".join(modes[:6])
    if value is not None:
        return str(value).strip()
    return ""


def _peer_capability_block(peer: str, capability: str, entry: dict[str, Any]) -> str:
    lines = [f"## {peer}: {capability}"]
    description = _entry_text(entry, "description")
    when_to_use = _entry_text(entry, "when_to_use")
    when_not_to = _entry_text(entry, "when_not_to")
    failure_modes = _failure_modes_text(entry)
    if description:
        lines.append(f"- What: {description}")
    if when_to_use:
        lines.append(f"- When to use: {when_to_use}")
    if when_not_to:
        lines.append(f"- When not to use: {when_not_to}")
    if failure_modes:
        lines.append(f"- Failure modes: {failure_modes}")
    for key, label in (
        ("delivery_mode", "Delivery mode"),
        ("expiry_semantics", "Expiry semantics"),
        ("authorization", "Authorization"),
    ):
        value = _entry_text(entry, key)
        if value:
            lines.append(f"- {label}: {value}")
    return "\n".join(lines)


def peer_prompt_fragment(agent_name: str, card: dict[str, Any]) -> str:
    """Return the R14 prompt fragment for one peer's rich Agent Card.

    The fragment teaches peer agents both what capabilities exist and when
    invoking/routing through that peer is useful.  It intentionally includes
    informational and lead-gated capabilities as well as directly callable
    ones: even non-callable features shape routing decisions and peer
    expectations (§3 peer efficiency).
    """
    caps = card.get("capabilities", {}) if isinstance(card, dict) else {}
    if not isinstance(caps, dict):
        return ""
    blocks: list[str] = []
    coupling = card.get("coupling")
    coupling_regime = card.get("coupling_regime")
    if isinstance(coupling, dict) or coupling_regime:
        intent = coupling.get("intent") if isinstance(coupling, dict) else None
        parts = []
        if coupling_regime:
            parts.append(f"regime={coupling_regime}")
        if intent:
            parts.append(f"intent={intent}")
        blocks.append(
            f"## {agent_name}: coupling intent declaration\n"
            f"- Declaration: {', '.join(parts)}\n"
            f"- Use this as routing guidance only; each backend interprets "
            f"coupling natively."
        )
    for capability, entry in sorted(caps.items()):
        if not isinstance(entry, dict):
            continue
        block = _peer_capability_block(agent_name, str(capability), entry)
        if block:
            blocks.append(block)
    manifest_lookup = (
        f"## {agent_name}: REQUIRED capability lookup before peer steering\n"
        f"- ACTION REQUIRED: MUST query mcp_anyteam_capability_manifest "
        f"before any peer-steer attempt to {agent_name}. Call it as "
        f"`mcp_anyteam_capability_manifest('{agent_name}', '<primitive>')` "
        f"for primitives such as `turn_steer`; do this even when the "
        f"capability summary below appears to mention the primitive.\n"
        f"- Use the manifest response to verify acceptance "
        f"(`callable_from_peers`/authorization) and review "
        f"delivery_mode/expiry_semantics before sending the steer.\n"
        f"- Consequence: if you skip the manifest query, peer steers will be "
        f"rejected, you waste a turn, the peer pays about 5s of rejection "
        f"cost, and the run emits visibility_degraded noise. If the manifest "
        f"does not explicitly allow peer steering, route the request through "
        f"team-lead instead."
    )
    return "\n\n".join([manifest_lookup, *blocks])


def peer_prompt_fragments_for(requester: str, cache: Any) -> str:
    """Aggregate peer-capability prompt fragments for the requesting agent.

    Skips the requester's own card and capabilities already present on the
    requester, then concatenates one fragment per peer using the cache's known
    rich Agent Cards.
    """
    cards = getattr(cache, "cards", None)
    if cards is None:
        cards = getattr(cache, "manifests", {})
    if not isinstance(cards, dict):
        return ""

    requester_card = cards.get(requester)
    requester_caps: set[str] = set()
    if isinstance(requester_card, dict):
        caps = requester_card.get("capabilities", {})
        if isinstance(caps, dict):
            requester_caps = set(caps)

    parts: list[str] = []
    for peer_name in sorted(cards):
        if peer_name == requester:
            continue
        card = cards[peer_name]
        if not isinstance(card, dict):
            continue
        caps = card.get("capabilities", {})
        if not isinstance(caps, dict):
            continue
        peer_unique = {
            capability: entry
            for capability, entry in caps.items()
            if capability not in requester_caps
        }
        peer_card = dict(card)
        peer_card["capabilities"] = peer_unique
        fragment = peer_prompt_fragment(peer_name, peer_card)
        if fragment:
            parts.append(fragment)
    if not parts:
        return ""
    return "# Capabilities of your peers\n\n" + "\n\n".join(parts)
