"""Protocol message payloads carried inside InboxMessage.text as JSON.

`claude_teams.models` defines several of these payloads; we reuse those where
the schema matches and define the remaining ones (idle_notification
*outbound*, plan_approval_request, plan_approval_response, task_complete)
here.

Outbound messages are built and serialized by the adapter; inbound messages
are parsed defensively since the harness and third-party teammates may not
conform exactly.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def now_iso() -> str:
    dt = datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


class _Base(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")


class TaskAssignmentIn(_Base):
    type: Literal["task_assignment"] = "task_assignment"
    task_id: str = Field(alias="taskId")
    subject: str
    description: str
    assigned_by: str = Field(alias="assignedBy")
    timestamp: str
    coupling: dict[str, Any] | None = None


class ShutdownRequestIn(_Base):
    """Inbound shutdown request.

    The wire format is ambiguous — SendMessage legacy docs reference
    `request_id` (snake), while the team protocol emits `requestId` (camel).
    We accept either and echo back whichever key the request used.
    """

    type: Literal["shutdown_request"] = "shutdown_request"
    request_id: str | None = Field(default=None, alias="requestId")
    from_: str | None = Field(default=None, alias="from")
    reason: str | None = None
    timestamp: str | None = None

    def effective_request_id(self) -> str | None:
        # Pydantic stores under the python name regardless of alias used.
        return self.request_id


class ShutdownApprovedOut(_Base):
    """Outbound host-catalog shutdown approval.

    R6 aligns the adapter with the v2.1.119 host catalog: 09 R6 and
    07 §5.1 split the legacy `shutdown_response` into distinct
    `shutdown_approved` / `shutdown_rejected` payloads, matching the
    03 §"Lifecycle" host-binary lifecycle/mailbox extract.
    """

    type: Literal["shutdown_approved"] = "shutdown_approved"
    schema_version: Literal[1] = 1
    request_id: str = Field(alias="requestId")
    from_: str = Field(alias="from")
    timestamp: str = Field(default_factory=now_iso)
    pane_id: str | None = Field(default=None, alias="paneId")
    backend_type: str | None = Field(default=None, alias="backendType")


class ShutdownRejectedOut(_Base):
    """Outbound host-catalog shutdown rejection."""

    type: Literal["shutdown_rejected"] = "shutdown_rejected"
    schema_version: Literal[1] = 1
    request_id: str = Field(alias="requestId")
    from_: str = Field(alias="from")
    reason: str
    timestamp: str = Field(default_factory=now_iso)


class ShutdownResponseOut(_Base):
    """Deprecated legacy shutdown_response alias.

    Kept for one release so callers/receivers can still accept the old
    `{type:"shutdown_response", request_id, approve, feedback?}` wire
    shape. New outbound code should use `ShutdownApprovedOut` or
    `ShutdownRejectedOut`; `to_host_catalog()` maps the legacy alias to the
    host-canonical shape.
    """

    type: Literal["shutdown_response"] = "shutdown_response"
    request_id: str
    approve: bool
    feedback: str | None = None
    timestamp: str = Field(default_factory=now_iso)

    def to_host_catalog(self, sender: str) -> ShutdownApprovedOut | ShutdownRejectedOut:
        if self.approve:
            return ShutdownApprovedOut(
                request_id=self.request_id,
                from_=sender,
                timestamp=self.timestamp,
            )
        return ShutdownRejectedOut(
            request_id=self.request_id,
            from_=sender,
            reason=self.feedback or "Shutdown rejected",
            timestamp=self.timestamp,
        )


class PlanApprovalRequestIn(_Base):
    type: Literal["plan_approval_request"] = "plan_approval_request"
    request_id: str | None = Field(default=None, alias="requestId")
    # Optional: the task this plan-approval flow is about. If absent, the
    # adapter falls back to its current assigned/claimable task. Accepting
    # both snake and camel keeps us tolerant to whichever the lead sends.
    task_id: str | None = Field(default=None, alias="taskId")
    plan: Any | None = None
    from_: str | None = Field(default=None, alias="from")
    timestamp: str | None = None


class PlanApprovalRequestOut(_Base):
    """Outbound structured plan sent by an opt-in (planModeRequired=True)
    Codex teammate. `plan` is produced by `codex exec --output-schema`."""

    type: Literal["plan_approval_request"] = "plan_approval_request"
    request_id: str
    plan: dict[str, Any]
    timestamp: str = Field(default_factory=now_iso)


class PlanApprovalResponseIn(_Base):
    type: Literal["plan_approval_response"] = "plan_approval_response"
    request_id: str | None = Field(default=None, alias="requestId")
    approve: bool | None = None
    feedback: str | None = None


class PlanApprovalResponseOut(_Base):
    """Outbound typed plan-approval decision.

    The lead/full-MCP surface historically sent plan decisions as either prose
    summaries (``plan_approved`` / ``plan_rejected``) or a legacy
    ``{"type":"plan_approval"}`` body. The adapter-side parser accepts the
    canonical lifecycle variant so receivers do not need to infer decisions
    from summary text.
    """

    type: Literal["plan_approval_response"] = "plan_approval_response"
    schema_version: Literal[1] = 1
    request_id: str = Field(alias="requestId")
    approve: bool
    feedback: str | None = None
    timestamp: str = Field(default_factory=now_iso)


class CapabilityManifestUpdatedIn(_Base):
    """Inbound R12 manifest-cache invalidation event.

    09 R12 uses this typed envelope (also stamped on InboxMessage.messageKind
    by the sender) to tell peers to reload or prune one cached Agent Card.
    """

    type: Literal["capability_manifest_updated"] = "capability_manifest_updated"
    agent_name: str = Field(alias="agentName")
    capability_version: str | None = Field(default=None, alias="capabilityVersion")
    manifest_path: str | None = Field(default=None, alias="manifestPath")
    removed: bool = False
    timestamp: str | None = None


class CapabilityManifestUpdatedOut(_Base):
    type: Literal["capability_manifest_updated"] = "capability_manifest_updated"
    agent_name: str = Field(alias="agentName")
    capability_version: str = Field(alias="capabilityVersion")
    manifest_path: str | None = Field(default=None, alias="manifestPath")
    removed: bool = False
    timestamp: str = Field(default_factory=now_iso)


class PermissionRequestOut(_Base):
    type: Literal["permission_request"] = "permission_request"
    schema_version: Literal[1] = 1
    request_id: str
    tool_name: str
    tool_args: Any
    task_id: str
    teammate_name: str
    trust_mode: Literal["default", "plan"]
    label: str | None = None
    session_id: str | None = None
    timestamp: str = Field(default_factory=now_iso)


class PermissionResponseIn(_Base):
    type: Literal["permission_response"] = "permission_response"
    request_id: str | None = Field(default=None, alias="requestId")
    decision: Literal["allow_once", "allow_session", "deny"] | None = None
    reason: str | None = None
    timestamp: str | None = None




class SteerIn(_Base):
    type: Literal["steer"] = "steer"
    message: str
    task_id: str | None = Field(default=None, alias="taskId")
    priority: Literal["normal", "urgent"] = "normal"
    expires_after_turns: int = Field(default=1, alias="expiresAfterTurns")
    from_: str | None = Field(default=None, alias="from")
    timestamp: str | None = None


class IdleNotificationOut(_Base):
    type: Literal["idle_notification"] = "idle_notification"
    schema_version: Literal[1] = 1
    from_: str = Field(alias="from")
    timestamp: str = Field(default_factory=now_iso)
    idle_reason: str = Field(default="available", alias="idleReason")


class TaskCompleteOut(_Base):
    """Structured message sent to the lead after a task completes.

    Schema matches §4.4 of the architecture doc. The `summary` field is
    populated from Codex output, not authored by the adapter.
    """

    kind: Literal["task_complete"] = "task_complete"
    schema_version: Literal[1] = 1
    task_id: str
    files_changed: list[str] = Field(default_factory=list)
    summary: str
    codex_exit_code: int


class TaskBlockedOut(_Base):
    """Typed lifecycle payload for a claimed task that could not proceed."""

    kind: Literal["task_blocked"] = "task_blocked"
    schema_version: Literal[1] = 1
    task_id: str
    reason: str
    timestamp: str = Field(default_factory=now_iso)


# #40 Phase 1 — discriminated-reason registry (graduated enforcement).
#
# ``task_blocked.reason`` is an open ``str`` field for backward compat
# with existing free-form values like ``"codex invocation crashed: ..."``
# or ``"plan generation failed twice ..."``. New machine-readable tokens
# get registered here so peers can query/filter by stable discriminator
# instead of grepping prose substrates. The wrapper-MCP ``send_message``
# tool emits a ``visibility_degraded`` warn for token-shaped reasons that
# are NOT in this registry — surfaces drift without blocking delivery.
#
# Adding a new typed token? Add it here AND ensure the emit-site is
# tested. Free-form prose reasons (with whitespace / mixed case) bypass
# the validator entirely — they are still legal but not recommended.
#
# Per ``feedback_graduated_enforcement_ladder``: rung 1 (declare) +
# rung 2 (suggest via warn). Future PR can promote to rung 3 (reject)
# once we've verified all production emit-sites use registered tokens.
KNOWN_TASK_BLOCKED_REASONS: frozenset[str] = frozenset(
    {
        # #40 Phase 1: JSON-RPC ``initialize`` budget exceeded during a
        # turn invocation. Stable token replaces the prior raw error
        # string ``"app_server error: did not respond to initialize ..."``.
        "app_server_initialize_timeout",
        # #40 Phase 1: ``initialize`` budget exceeded specifically while
        # the adapter was honoring an in-flight ``shutdown_request``. The
        # discriminator distinction lets the lead surface a no-op
        # shutdown burning the budget vs a work-turn timeout uniformly
        # in their dashboard, but with separate filters.
        "app_server_shutdown_timeout",
        # #49 / RFC §5.1: a wrapper MCP tool failed and no recovery
        # activity appeared within the configured discriminator window.
        # This is a lead-action token, not a diagnostics.ERROR_CLASSES entry:
        # the envelope itself is an in-flight signal rather than a terminal
        # turn failure.
        "wrapper_tool_failure_unrecovered",
    }
)


# Token shape for the registry's gate: snake_case, lowercase ASCII +
# digits + underscores, starting with a letter. Matches what we expect
# for stable machine-readable reason values; everything else (prose with
# spaces, mixed case, JSON, traceback dumps) bypasses the gate.
_TOKEN_SHAPED_REASON_RE = re.compile(r"^[a-z][a-z0-9_]+$")


def is_token_shaped_reason(reason: str) -> bool:
    """Return True if ``reason`` looks like a stable machine-readable token.

    The wrapper-MCP validator only checks the registry for token-shaped
    values; free-form prose reasons (which have whitespace or punctuation
    or mixed case) pass through silently. This is the §1-respecting
    pragmatic shape: structural tokens are policed, free-form is left
    alone for backward compat.
    """
    return bool(_TOKEN_SHAPED_REASON_RE.match(reason))


class PlanBlockedOut(_Base):
    """Typed lifecycle payload for an unfulfillable plan-approval request."""

    kind: Literal["plan_blocked"] = "plan_blocked"
    schema_version: Literal[1] = 1
    request_id: str
    reason: str
    task_id: str | None = None
    timestamp: str = Field(default_factory=now_iso)


LIFECYCLE_MESSAGE_KINDS = frozenset(
    {
        "idle_notification",
        "task_assignment",
        "task_complete",
        "task_blocked",
        "plan_blocked",
        "plan_approval_request",
        "plan_approval_response",
        "permission_request",
        "permission_response",
        "shutdown_request",
        "shutdown_approved",
        "shutdown_rejected",
        "shutdown_response",
        "capability_manifest_updated",
    }
)


class BatchSummaryChild(_Base):
    """One delegated child task result included in a batch summary event."""

    task_id: str = Field(alias="taskId")
    status: str
    session_id: str | None = Field(default=None, alias="sessionId")
    stop_reason: str | None = Field(default=None, alias="stopReason")
    summary: str | None = None


class BatchSummaryPayload(_Base):
    """Payload for the ``batch_summary`` visibility event.

    The task model remains per-task, so this payload is the structured
    visibility link that ties multiple delegated child task IDs to one parent
    task and preserves per-child session status/stop-reason details.
    """

    parent_task_id: str = Field(alias="parentTaskId")
    child_task_ids: list[str] = Field(alias="childTaskIds")
    child_tasks: list[BatchSummaryChild] = Field(alias="childTasks")
    summary: str


VisibilityEventKind = Literal[
    "agent_registered",
    "turn_started",
    "turn_progress",
    "tool_event",
    "artifact_event",
    "turn_warning",
    "turn_completed",
    "turn_failed",
    "visibility_degraded",
    "steer_ack",
    "capability_changed",
    "capability_manifest_updated",
    "batch_summary",
    # #40 Phase 1: success-path instrumentation for the Codex App Server
    # JSON-RPC ``initialize`` handshake. Carries ``elapsed_ms`` and
    # ``prompt_byte_size`` so we can revisit the default initialize
    # timeout (90s — see ``APP_SERVER_INITIALIZE_TIMEOUT_ENV``) with real
    # success-path data instead of the single 17s anecdote we have today.
    # Closes the meta-observability gap noted in the #40 thread:
    # "10-minute silence is indistinguishable from 'agent is genuinely
    # thinking hard' until the failure event lands."
    "app_server_initialize_completed",
    # #40 Phase 1: periodic progress envelope emitted while we are still
    # waiting on the ``initialize`` reply. Cadence is
    # ``APP_SERVER_INITIALIZE_PROGRESS_INTERVAL_ENV`` (default 30s). Lets
    # the lead observe a slow cold start without staring at a 90s
    # silence; carries ``attempt``, ``elapsed_s``, ``last_observed_pid``,
    # and (Linux only, best-effort) ``last_observed_cpu_pct``.
    "app_server_initialize_progress",
    # #49 / RFC §5.1: wrapper MCP tool failure followed by no observable
    # turn_progress/tool_event/artifact_event/agentMessage_delta within W.
    # This is a top-level signal, not ``visibility_degraded`` and not a
    # diagnostics.ERROR_CLASSES terminal failure.
    "wrapper_tool_failure_unrecovered",
]

VisibilitySeverity = Literal["debug", "info", "warn", "error"]


class VisibilityChannels(_Base):
    """B9 §6.2/§6.4 fan-out flags for the v0.7.1 event log.

    The envelope is intentionally a routing/visibility wrapper, not a
    backend-tool flattener: backend-native names stay in payload fields such
    as ``raw_backend_type`` per 07 §7.3. See 09 R16 and 08 PE-2/L17 for why
    the append-only event log exists alongside mailbox/task-state/stderr.
    """

    mailbox: bool = False
    task_state: bool = False
    event_log: bool = True
    stderr: bool = True


class VisibilityEvent(_Base):
    """Versioned B9 §6 visibility envelope.

    This is the common substrate for Codex/Gemini/Kimi visibility without
    erasing harness-specific event names (07 §7.3, 09 R16, 08 PE-2/L17).
    ``payload`` is intentionally permissive by kind so new backend fields can
    be preserved without a schema migration; the outer envelope remains
    stable for filtering, fan-out, and de-duplication.
    """

    kind: VisibilityEventKind
    schema_version: Literal[1] = 1
    event_id: str
    timestamp: str = Field(default_factory=now_iso)
    team: str
    agent: str
    backend: str
    task_id: str | None = None
    turn_id: str | None = None
    seq: int = Field(ge=0)
    severity: VisibilitySeverity = "info"
    summary: str
    visibility: VisibilityChannels = Field(default_factory=VisibilityChannels)
    payload: dict[str, Any] = Field(default_factory=dict)


def parse_protocol_text(text: str) -> _Base | None:
    """Best-effort parse of an inbox message's `text` field as a protocol payload.

    Returns None if the text isn't JSON or doesn't carry a known `type`/`kind`.
    Never raises for malformed input — callers should treat None as "prose".
    """
    import json

    if isinstance(text, str) and text[:6].lower() == "steer:":
        return SteerIn(message=text[6:].strip())

    try:
        raw = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(raw, dict):
        return None
    t = raw.get("type") or raw.get("kind")
    if t == "task_assignment":
        return _safe_load(TaskAssignmentIn, raw)
    if t == "shutdown_request":
        return _safe_load(ShutdownRequestIn, raw)
    if t == "shutdown_approved":
        return _safe_load(ShutdownApprovedOut, raw)
    if t == "shutdown_rejected":
        return _safe_load(ShutdownRejectedOut, raw)
    if t == "shutdown_response":
        return _safe_load(ShutdownResponseOut, raw)
    if t == "plan_approval_request":
        return _safe_load(PlanApprovalRequestIn, raw)
    if t == "plan_approval_response":
        return _safe_load(PlanApprovalResponseIn, raw)
    if t == "plan_blocked":
        return _safe_load(PlanBlockedOut, raw)
    if t == "permission_request":
        return _safe_load(PermissionRequestOut, raw)
    if t == "permission_response":
        return _safe_load(PermissionResponseIn, raw)
    if t == "capability_manifest_updated":
        return _safe_load(CapabilityManifestUpdatedIn, raw)
    if t == "steer":
        return _safe_load(SteerIn, raw)
    if t == "idle_notification":
        return _safe_load(IdleNotificationOut, raw)
    if t == "task_complete":
        return _safe_load(TaskCompleteOut, raw)
    if t == "task_blocked":
        return _safe_load(TaskBlockedOut, raw)
    if t in {
        "agent_registered",
        "turn_started",
        "turn_progress",
        "tool_event",
        "artifact_event",
        "turn_warning",
        "turn_completed",
        "turn_failed",
        "visibility_degraded",
        "steer_ack",
        "capability_changed",
        "capability_manifest_updated",
        "batch_summary",
        "app_server_initialize_completed",
        "app_server_initialize_progress",
        "wrapper_tool_failure_unrecovered",
    }:
        return _safe_load(VisibilityEvent, raw)
    return None


def _safe_load(cls: type[_Base], raw: dict[str, Any]) -> _Base | None:
    try:
        return cls.model_validate(raw)
    except Exception:
        return None
