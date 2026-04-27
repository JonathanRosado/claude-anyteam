from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def now_iso() -> str:
    dt = datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


class WirePayload(BaseModel):
    """Typed payloads ride inside InboxMessage.text; `kind` avoids prose parsing."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")
    kind: str
    schema_version: int = 1


class TaskAssignment(WirePayload):
    kind: Literal["task_assignment"] = "task_assignment"
    task_id: str = Field(alias="taskId")
    subject: str = ""
    description: str = ""
    assigned_by: str = Field(default="team-lead", alias="assignedBy")
    timestamp: str = Field(default_factory=now_iso)


class ShutdownRequest(WirePayload):
    kind: Literal["shutdown_request"] = "shutdown_request"
    request_id: str = Field(alias="requestId")
    from_: str = Field(default="team-lead", alias="from")
    reason: str = ""
    timestamp: str = Field(default_factory=now_iso)


class ShutdownResponse(WirePayload):
    kind: Literal["shutdown_response"] = "shutdown_response"
    request_id: str
    approve: bool
    feedback: str | None = None
    timestamp: str = Field(default_factory=now_iso)


class IdleNotification(WirePayload):
    kind: Literal["idle_notification"] = "idle_notification"
    from_: str = Field(alias="from")
    timestamp: str = Field(default_factory=now_iso)
    idle_reason: str = Field(default="available", alias="idleReason")


class TaskComplete(WirePayload):
    kind: Literal["task_complete"] = "task_complete"
    task_id: str
    files_changed: list[str] = Field(default_factory=list)
    summary: str
    backend_exit_code: int = 0


class TaskBlocked(WirePayload):
    kind: Literal["task_blocked"] = "task_blocked"
    task_id: str
    reason: str


class PlanApprovalRequest(WirePayload):
    kind: Literal["plan_approval_request"] = "plan_approval_request"
    request_id: str
    task_id: str | None = None
    plan: dict[str, Any]


class Steer(WirePayload):
    kind: Literal["steer"] = "steer"
    message: str
    task_id: str | None = Field(default=None, alias="taskId")
    priority: Literal["normal", "urgent"] = "normal"
    from_: str | None = Field(default=None, alias="from")


class TaskResult(BaseModel):
    """Harness-returned result; kit turns it into task/mailbox/event state."""

    summary: str
    files_changed: list[str] = Field(default_factory=list)
    exit_code: int = 0
    blocked: bool = False
    reason: str | None = None


_KIND_TO_MODEL: dict[str, type[WirePayload]] = {
    "task_assignment": TaskAssignment,
    "shutdown_request": ShutdownRequest,
    "shutdown_response": ShutdownResponse,
    "idle_notification": IdleNotification,
    "task_complete": TaskComplete,
    "task_blocked": TaskBlocked,
    "plan_approval_request": PlanApprovalRequest,
    "steer": Steer,
}


def parse_protocol_text(text: str) -> WirePayload | None:
    if text[:6].lower() == "steer:":
        return Steer(message=text[6:].strip())
    try:
        raw = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    kind = raw.get("kind") or raw.get("type")  # accept v1 `type`, canonicalize to kind here.
    model = _KIND_TO_MODEL.get(kind)
    if model is None:
        return None
    raw.setdefault("kind", kind)
    try:
        return model.model_validate(raw)
    except Exception:
        return None


def dumps_payload(payload: WirePayload | dict[str, Any]) -> str:
    if isinstance(payload, WirePayload):
        return payload.model_dump_json(by_alias=True, exclude_none=True)
    payload.setdefault("schema_version", 1)
    return json.dumps(payload)
