"""Shared helpers for #49 wrapper-tool failure recovery semantics."""

from __future__ import annotations

from typing import Any

WRAPPER_TOOL_RECOVERY_EVENT_KINDS = frozenset(
    {"turn_progress", "tool_event", "artifact_event"}
)


def is_wrapper_tool_recovery_event_kind(kind: str | None) -> bool:
    """Return True when a visibility kind counts as Mode-A recovery activity."""

    return kind in WRAPPER_TOOL_RECOVERY_EVENT_KINDS


def visibility_event_counts_as_wrapper_tool_recovery(event: Any | None) -> bool:
    """Return True when an emitted visibility event closes pending failures.

    Kept intentionally duck-typed to avoid importing the Pydantic event model
    on hot paths; callers only need the stable ``.kind`` attribute.
    """

    return is_wrapper_tool_recovery_event_kind(getattr(event, "kind", None))
