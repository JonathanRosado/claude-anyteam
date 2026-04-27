"""Protocol-level coupling-intent helpers.

The coupling declaration is descriptive protocol metadata.  It lets routed
backends declare whether they natively tolerate tight peer loops or prefer
loose parallel work; it does not impose a shared runtime cadence.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal

CouplingRegime = Literal["tight", "loose"]
CouplingIntent = Literal["tight_peer_loop", "loose_parallel", "batched_async"]

TIGHT_INTENT: CouplingIntent = "tight_peer_loop"
LOOSE_INTENT: CouplingIntent = "loose_parallel"
BATCHED_ASYNC_INTENT: CouplingIntent = "batched_async"

REGIME_TO_INTENT: dict[str, CouplingIntent] = {
    "tight": TIGHT_INTENT,
    "loose": LOOSE_INTENT,
}

INTENT_TO_REGIME: dict[str, CouplingRegime] = {
    TIGHT_INTENT: "tight",
    LOOSE_INTENT: "loose",
    # Future middle mode: non-blocking under today's tight/loose comparison,
    # while retaining its own canonical value for scorers and future routers.
    BATCHED_ASYNC_INTENT: "loose",
}

DEFAULT_COUPLING_CONTRACTS: dict[CouplingIntent, dict[str, Any]] = {
    TIGHT_INTENT: {
        "intent": TIGHT_INTENT,
        "blocking_peer_reply": "allowed",
        "peer_dm_policy": "required_for_coordination",
        "sync_points": [
            "before_implementation",
            "handoff",
            "review_before_completion",
        ],
        "completion_boundary": "shared_artifact_complete_after_peer_review",
        "expected_metric_shape": {
            "M4_team_cross_peer_ratio": ">=0.8",
            "M11a_blocking_candidate_samples": ">0",
            "M11b_fast_backend_turn_inflation": "expected",
        },
    },
    LOOSE_INTENT: {
        "intent": LOOSE_INTENT,
        "blocking_peer_reply": "forbidden_before_local_completion",
        "peer_dm_policy": "fyi_only_during_local_work",
        "sync_points": [
            "optional_start_digest",
            "final_reconcile_after_local_completion",
        ],
        "completion_boundary": "per_assignee_shard_complete_independently",
        "expected_metric_shape": {
            "M4_team_cross_peer_ratio": "low_or_fyi_only",
            "M11a_blocking_candidate_samples": 0,
            "M1_hetero_throughput_per_min": "1.5-2.0 target band",
            "kimi_per_agent_rate": "near solo until shard queue drains",
        },
    },
    BATCHED_ASYNC_INTENT: {
        "intent": BATCHED_ASYNC_INTENT,
        "blocking_peer_reply": "allowed_at_batch_boundaries",
        "peer_dm_policy": "batched_coordination",
        "sync_points": [
            "batch_boundary",
            "final_reconcile_after_local_completion",
        ],
        "completion_boundary": "batch_complete_after_scheduled_reconcile",
        "expected_metric_shape": {
            "M11a_blocking_candidate_samples": "bounded_to_batch_boundaries",
            "M11b_fast_backend_turn_inflation": "bounded",
        },
    },
}


class CouplingDeclarationError(ValueError):
    """Raised when a coupling declaration is absent or malformed."""


def canonical_intent(value: Any) -> CouplingIntent | None:
    """Return the canonical long-form intent for a task/workload value.

    ``None`` means "no task override." Legacy string aliases are accepted here
    for old task fixtures and direct task-create calls; workload validation may
    reject those aliases before new manifests are admitted.
    """

    if value is None:
        return None
    raw: Any = value
    if isinstance(value, dict):
        raw = value.get("intent")
    if isinstance(raw, str):
        raw = raw.strip()
        raw = REGIME_TO_INTENT.get(raw, raw)
    if raw in DEFAULT_COUPLING_CONTRACTS:
        return raw  # type: ignore[return-value]
    raise CouplingDeclarationError(
        "coupling intent must be one of "
        f"{sorted(DEFAULT_COUPLING_CONTRACTS)} or legacy aliases "
        f"{sorted(REGIME_TO_INTENT)}"
    )


def canonical_regime(value: Any) -> CouplingRegime | None:
    intent = canonical_intent(value)
    if intent is None:
        return None
    return INTENT_TO_REGIME[intent]


def coupling_contract(value: Any) -> dict[str, Any] | None:
    """Return a full canonical coupling object, preserving supplied metadata."""

    intent = canonical_intent(value)
    if intent is None:
        return None
    contract = deepcopy(DEFAULT_COUPLING_CONTRACTS[intent])
    if isinstance(value, dict):
        for key, item in value.items():
            if item is not None:
                contract[key] = deepcopy(item)
        contract["intent"] = intent
    return contract


def declaration_for_regime(regime: str) -> tuple[CouplingRegime, dict[str, Any]]:
    if regime not in REGIME_TO_INTENT:
        raise CouplingDeclarationError(
            f"coupling_regime must be one of {sorted(REGIME_TO_INTENT)}"
        )
    declared_regime: CouplingRegime = regime  # type: ignore[assignment]
    contract = coupling_contract(REGIME_TO_INTENT[declared_regime])
    assert contract is not None
    return declared_regime, contract


def declared_regime_from_manifest(manifest: dict[str, Any]) -> CouplingRegime:
    """Read the backend declaration from an Agent Card manifest.

    This deliberately reads the declaration carried by the protocol manifest,
    not any inferred ``backend_type`` mapping.  If both legacy
    ``coupling_regime`` and canonical ``coupling.intent`` fields are present,
    they must agree.
    """

    if not isinstance(manifest, dict):
        raise CouplingDeclarationError("capability manifest must be an object")

    root_regime = manifest.get("coupling_regime")
    coupling = manifest.get("coupling")
    root_norm: CouplingRegime | None = None
    coupling_norm: CouplingRegime | None = None

    if root_regime is not None:
        if root_regime not in REGIME_TO_INTENT:
            raise CouplingDeclarationError(
                f"manifest coupling_regime must be one of {sorted(REGIME_TO_INTENT)}"
            )
        root_norm = root_regime  # type: ignore[assignment]

    if coupling is not None:
        coupling_norm = canonical_regime(coupling)

    if root_norm is None and coupling_norm is None:
        raise CouplingDeclarationError(
            "capability manifest must declare coupling_regime or coupling.intent"
        )
    if root_norm is not None and coupling_norm is not None and root_norm != coupling_norm:
        raise CouplingDeclarationError(
            "manifest coupling_regime and coupling.intent disagree: "
            f"{root_norm!r} vs {coupling_norm!r}"
        )
    return root_norm or coupling_norm  # type: ignore[return-value]


def declared_intent_from_manifest(manifest: dict[str, Any]) -> CouplingIntent:
    coupling = manifest.get("coupling") if isinstance(manifest, dict) else None
    if coupling is not None:
        intent = canonical_intent(coupling)
        assert intent is not None
        return intent
    return REGIME_TO_INTENT[declared_regime_from_manifest(manifest)]


def coupling_prompt_prefix(coupling: Any) -> str:
    """Human-facing task prefix for generated stress workload tasks."""

    intent = canonical_intent(coupling)
    if intent is None:
        return ""
    if intent == TIGHT_INTENT:
        return (
            "Coupling intent: TIGHT_PEER_LOOP. Coordinate directly with your "
            "peer before implementation and before completion. Blocking peer "
            "replies are part of this workload. Use `ask:` / `answer:` / "
            "`handoff:` peer-DM language. Do not route through team-lead."
        )
    if intent == LOOSE_INTENT:
        return (
            "Coupling intent: LOOSE_PARALLEL. Complete only the shard addressed "
            "to you. Do not wait for peer replies and do not require peer "
            "approval before marking your local shard complete. Peer-DM during "
            "local work is FYI-only; prefix any such message with `fyi:` and "
            "continue immediately. Reconcile only after local completion."
        )
    return (
        "Coupling intent: BATCHED_ASYNC. Continue local work between scheduled "
        "coordination points. Use `ask:` / `answer:` / `handoff:` only at batch "
        "boundaries; otherwise use `fyi:` and continue."
    )
