"""Protocol message payloads carried inside InboxMessage.text as JSON.

cs50victor defines several of these in `claude_teams.models` — we reuse those
where the schema matches and define the remaining ones (idle_notification
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


class ShutdownRequestIn(_Base):
    """Inbound shutdown request.

    The wire format is ambiguous — SendMessage legacy docs reference
    `request_id` (snake), cs50victor emits `requestId` (camel). We accept
    either and echo back whichever key the request used.
    """

    type: Literal["shutdown_request"] = "shutdown_request"
    request_id: str | None = Field(default=None, alias="requestId")
    from_: str | None = Field(default=None, alias="from")
    reason: str | None = None
    timestamp: str | None = None

    def effective_request_id(self) -> str | None:
        # Pydantic stores under the python name regardless of alias used.
        return self.request_id


class ShutdownResponseOut(_Base):
    """Outbound shutdown_response.

    Emits both `request_id` (matching the SendMessage legacy docs) and
    camelCase is handled transparently by clients via populate_by_name.
    """

    type: Literal["shutdown_response"] = "shutdown_response"
    request_id: str
    approve: bool
    feedback: str | None = None
    timestamp: str = Field(default_factory=now_iso)


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


def parse_protocol_text(text: str) -> _Base | None:
    """Best-effort parse of an inbox message's `text` field as a protocol payload.

    Returns None if the text isn't JSON or doesn't carry a known `type`/`kind`.
    Never raises for malformed input — callers should treat None as "prose".
    """
    import json

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
    if t == "plan_approval_request":
        return _safe_load(PlanApprovalRequestIn, raw)
    if t == "plan_approval_response":
        return _safe_load(PlanApprovalResponseIn, raw)
    if t == "permission_response":
        return _safe_load(PermissionResponseIn, raw)
    if t == "steer":
        return _safe_load(SteerIn, raw)
    return None


def _safe_load(cls: type[_Base], raw: dict[str, Any]) -> _Base | None:
    try:
        return cls.model_validate(raw)
    except Exception:
        return None
