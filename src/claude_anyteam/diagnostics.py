"""Per-incident diagnostic capture for adapter prose-reply failures.

When a backend's prose handler can't produce a reply (subprocess timeout,
schema-validation failure, MCP tool unavailable, crash, etc.), the
user-facing fallback message has to stay terse — it goes to the lead's
chat surface and must not leak stack traces, PII from the inbound prompt,
or stderr noise.

This module bridges the gap between "captured but discarded" (today's
behavior — the wrapper logs full error context to stderr and throws it
away in the chat reply) and "leaked unsafely" (dumping raw error strings
into the user-visible reply).

Pattern:
- The handler calls `record_incident()` with the full structured context.
- A short stable `incident_id` (`inc-<8 hex chars>`) is generated and
  the diagnostic file is written to
  `~/.claude/teams/<team>/diagnostics/<agent>/<incident_id>.json`.
- The handler embeds the `incident_id` in the prose fallback so the lead
  can find the file.

Broadly applicable: every backend (Codex / Gemini / Kimi) and every
failure shape (timeout, crash, schema-fail, MCP-missing) flows through
the same surface. Adding a new backend is a one-call integration.
"""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any

from . import logger


def _diagnostics_dir(team: str, agent: str) -> Path:
    return (
        Path(os.path.expanduser("~"))
        / ".claude"
        / "teams"
        / team
        / "diagnostics"
        / agent
    )


def record_incident(
    *,
    team: str,
    agent: str,
    backend: str,
    error_class: str,
    summary: str,
    sender: str | None = None,
    payload: dict[str, Any] | None = None,
) -> str:
    """Write a diagnostic JSON artifact and return its `incident_id`.

    Best-effort: if the directory cannot be created or the write fails,
    log a warning and return the (still useful) `incident_id` anyway —
    the caller can include it in chat even when the artifact is missing,
    and the warn-log retains the context for forensics.

    `error_class` should be a short, stable identifier the lead can grep
    for (e.g. `turn_timeout`, `mcp_send_message_unavailable`,
    `schema_validation_failed`, `subprocess_crash`). Keep the taxonomy
    closed and broadly applicable across backends; do not invent
    backend-specific classes when a generic one fits.
    """
    incident_id = f"inc-{secrets.token_hex(4)}"
    record: dict[str, Any] = {
        "incident_id": incident_id,
        "team": team,
        "agent": agent,
        "backend": backend,
        "error_class": error_class,
        "summary": summary,
    }
    if sender is not None:
        record["sender"] = sender
    if payload:
        record["payload"] = payload

    try:
        diag_dir = _diagnostics_dir(team, agent)
        diag_dir.mkdir(parents=True, exist_ok=True)
        path = diag_dir / f"{incident_id}.json"
        path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError as exc:
        logger.warn(
            "diagnostics.write_failed",
            incident_id=incident_id,
            team=team,
            agent=agent,
            error=str(exc),
        )

    logger.info(
        "diagnostics.incident_recorded",
        incident_id=incident_id,
        team=team,
        agent=agent,
        backend=backend,
        error_class=error_class,
    )
    return incident_id


# Closed taxonomy of error classes the prose-fallback path can raise. Kept
# as a tuple of strings rather than an Enum so it can cross module
# boundaries without forcing every caller to import the enum class.
# Adding a new class? Add it here AND extend `classify_failure` so the
# classifier actually returns it; otherwise it'll never appear in
# diagnostic artifacts.
ERROR_CLASSES: tuple[str, ...] = (
    "subprocess_crash",         # the backend process didn't return a result at all
    "turn_timeout",             # backend reported its own timeout (e.g. App Server 900s)
    "schema_validation_failed", # final response didn't match the configured schema
    "mcp_send_message_unavailable",  # wrapper MCP allowlist gap (B2 lifecycle bug)
    "subprocess_nonzero_exit",  # backend exited non-zero with no clearer signal
    "no_reply_produced",        # backend exited 0 but produced no usable reply
    "adapter_crash",             # uncaught exception in the adapter's main loop
)


def classify_failure(result: Any) -> str:
    """Map a backend-result object's error context to one of `ERROR_CLASSES`.

    Backend-agnostic: relies on `result.error` (a string) and `result.exit_code`
    (an int), both of which Codex/Gemini/Kimi result types carry. `result is
    None` (no result object captured) is treated as `subprocess_crash`.

    Centralized here so the three backends don't drift on the taxonomy.
    Extending the taxonomy is one edit, not three.
    """
    if result is None:
        return "subprocess_crash"
    err = (getattr(result, "error", "") or "").lower()
    if "timeout" in err or "did not complete within" in err:
        return "turn_timeout"
    if "schema" in err and "valid" in err:
        return "schema_validation_failed"
    if "send_message" in err and ("not available" in err or "missing" in err):
        return "mcp_send_message_unavailable"
    if getattr(result, "exit_code", 0) != 0:
        return "subprocess_nonzero_exit"
    return "no_reply_produced"


def fallback_message(*, backend: str, incident_id: str, error_class: str) -> str:
    """Render the user-facing prose fallback with the incident_id embedded.

    The shape is the same across backends so the lead can recognize it at
    a glance regardless of which teammate emitted it. Keep it short — this
    text lands in chat, not a log viewer.
    """
    return (
        f"I received your message but couldn't generate a reply "
        f"(adapter={backend}, error={error_class}, incident={incident_id}). "
        f"Run `claude-anyteam diagnose --incident {incident_id}` for details."
    )
