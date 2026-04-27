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
    from_: str = Field(alias="from")
    timestamp: str = Field(default_factory=now_iso)
    idle_reason: str = Field(default="available", alias="idleReason")


class TaskCompleteOut(_Base):
    """Structured message sent to the lead after a task completes.

    Schema matches §4.4 of the architecture doc. The `summary` field is
    populated from Codex output, not authored by the adapter.
    """

    kind: Literal["task_complete"] = "task_complete"
    task_id: str
    files_changed: list[str] = Field(default_factory=list)
    summary: str
    codex_exit_code: int


VisibilityEventKind = Literal[
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
    if t == "permission_response":
        return _safe_load(PermissionResponseIn, raw)
    if t == "capability_manifest_updated":
        return _safe_load(CapabilityManifestUpdatedIn, raw)
    if t == "steer":
        return _safe_load(SteerIn, raw)
    if t in {
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
    }:
        return _safe_load(VisibilityEvent, raw)
    return None


def _safe_load(cls: type[_Base], raw: dict[str, Any]) -> _Base | None:
    try:
        return cls.model_validate(raw)
    except Exception:
        return None
