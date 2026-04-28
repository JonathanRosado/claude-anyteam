from __future__ import annotations

import time
import uuid
from typing import Any, Literal

from typing import Annotated, Union

from pydantic import BaseModel, Discriminator, Field, Tag, field_validator

from .coupling import coupling_contract

COLOR_PALETTE: list[str] = [
    "blue", "green", "yellow", "purple",
    "orange", "pink", "cyan", "red",
]


class LeadMember(BaseModel):
    model_config = {"populate_by_name": True}

    agent_id: str = Field(alias="agentId")
    name: str
    # R2 (09 §3.1): tolerate legacy config rows
    # that predate agentType so one bad sibling cannot break read_config().
    agent_type: str = Field(alias="agentType", default="team-lead")
    model: str
    joined_at: int = Field(alias="joinedAt")
    tmux_pane_id: str = Field(alias="tmuxPaneId", default="")
    cwd: str
    subscriptions: list = Field(default_factory=list)


class TeammateMember(BaseModel):
    model_config = {"populate_by_name": True}

    agent_id: str = Field(alias="agentId")
    name: str
    # R2 (09 §3.1): additive default for legacy
    # rows missing agentType; existing explicit values still round-trip.
    agent_type: str = Field(alias="agentType", default="claude-anyteam")
    model: str
    prompt: str
    color: str
    plan_mode_required: bool = Field(alias="planModeRequired", default=False)
    joined_at: int = Field(alias="joinedAt")
    tmux_pane_id: str = Field(alias="tmuxPaneId")
    cwd: str
    subscriptions: list = Field(default_factory=list)
    # Protocol-rev 09 R11 and 08 §6.3 Agent Card/capabilities():
    # adapters declare a flat list of cheap capability flags at registration
    # for roster discovery; rich per-capability manifests are exposed later
    # via wrapper MCP in R12/R13.
    capabilities: list[str] = Field(
        default_factory=list,
        description=(
            "Adapter-declared flat capability flags for cheap roster "
            "discovery; rich per-capability manifests are exposed via the "
            "wrapper MCP."
        ),
    )
    backend_type: str = Field(alias="backendType", default="claude")
    is_active: bool = Field(alias="isActive", default=False)


def _discriminate_member(v: Any) -> str:
    if isinstance(v, dict):
        return "teammate" if "prompt" in v else "lead"
    if isinstance(v, TeammateMember):
        return "teammate"
    return "lead"


MemberUnion = Annotated[
    Union[
        Annotated[LeadMember, Tag("lead")],
        Annotated[TeammateMember, Tag("teammate")],
    ],
    Discriminator(_discriminate_member),
]


class TeamConfig(BaseModel):
    model_config = {"populate_by_name": True}

    name: str
    description: str = ""
    created_at: int = Field(alias="createdAt")
    lead_agent_id: str = Field(alias="leadAgentId")
    lead_session_id: str = Field(alias="leadSessionId")
    members: list[MemberUnion]


class TaskFile(BaseModel):
    model_config = {"populate_by_name": True}

    id: str
    subject: str
    description: str
    active_form: str = Field(alias="activeForm", default="")
    status: Literal["pending", "in_progress", "completed", "deleted"] = "pending"
    blocks: list[str] = Field(default_factory=list)
    blocked_by: list[str] = Field(alias="blockedBy", default_factory=list)
    owner: str | None = Field(default=None)
    coupling: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Optional per-task coupling override. Canonical shape is "
            "{intent: tight_peer_loop|loose_parallel|batched_async}; legacy "
            "'tight'/'loose' aliases are accepted and canonicalized for old "
            "task files."
        ),
    )
    metadata: dict[str, Any] | None = Field(default=None)

    @field_validator("coupling", mode="before")
    @classmethod
    def _canonicalize_coupling(cls, value: Any) -> dict[str, Any] | None:
        return coupling_contract(value)


class InboxAttachment(BaseModel):
    """Reference to a full message body stored outside the inbox row.

    Inbox rows keep a bounded preview in ``text`` so mailbox scans remain
    cheap.  When a body is too large, the complete text is written as an
    artifact and referenced here.
    """

    model_config = {"populate_by_name": True}

    kind: Literal["artifact"] = "artifact"
    path: str
    relative_path: str | None = Field(alias="relativePath", default=None)
    mime_type: str = Field(alias="mimeType", default="text/plain")
    char_count: int = Field(alias="charCount")
    preview_char_count: int = Field(alias="previewCharCount")
    sha256: str | None = None


class InboxMessage(BaseModel):
    model_config = {"populate_by_name": True}

    from_: str = Field(alias="from")
    text: str
    timestamp: str
    read: bool = False
    summary: str | None = Field(default=None)
    color: str | None = Field(default=None)
    # 09 R3 (Q1 option b): typed messageKind discriminator declared as an
    # explicit field with default="peer_dm". Survives substrate
    # `read_inbox(mark_as_read=True)` round-trip — model_dump preserves
    # the field by construction, no extra="allow" needed. Per CLAUDE.md
    # §3 anti-pattern A11 (no parse-prose-to-route): consumers filter by
    # this kind, never by parsing JSON inside `text`.
    message_kind: str = Field(default="peer_dm", alias="messageKind")
    attachment: InboxAttachment | None = Field(default=None)


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


class IdleNotification(BaseModel):
    model_config = {"populate_by_name": True}

    type: Literal["idle_notification"] = "idle_notification"
    schema_version: Literal[1] = 1
    from_: str = Field(alias="from")
    timestamp: str
    idle_reason: str = Field(alias="idleReason", default="available")


class TaskAssignment(BaseModel):
    model_config = {"populate_by_name": True}

    type: Literal["task_assignment"] = "task_assignment"
    task_id: str = Field(alias="taskId")
    subject: str
    description: str
    assigned_by: str = Field(alias="assignedBy")
    timestamp: str
    coupling: dict[str, Any] | None = None


class ShutdownRequest(BaseModel):
    model_config = {"populate_by_name": True}

    type: Literal["shutdown_request"] = "shutdown_request"
    request_id: str = Field(alias="requestId")
    from_: str = Field(alias="from")
    reason: str
    timestamp: str


class ShutdownApproved(BaseModel):
    model_config = {"populate_by_name": True}

    type: Literal["shutdown_approved"] = "shutdown_approved"
    request_id: str = Field(alias="requestId")
    from_: str = Field(alias="from")
    timestamp: str
    pane_id: str = Field(alias="paneId")
    backend_type: str = Field(alias="backendType")
    session_id: str | None = Field(alias="sessionId", default=None)


class ShutdownRejected(BaseModel):
    model_config = {"populate_by_name": True}

    type: Literal["shutdown_rejected"] = "shutdown_rejected"
    schema_version: Literal[1] = 1
    request_id: str = Field(alias="requestId")
    from_: str = Field(alias="from")
    reason: str
    timestamp: str


class PlanApprovalRequest(BaseModel):
    model_config = {"populate_by_name": True}

    type: Literal["plan_approval_request"] = "plan_approval_request"
    schema_version: Literal[1] = 1
    request_id: str = Field(alias="requestId")
    plan: dict[str, Any]
    timestamp: str


class PlanApprovalResponse(BaseModel):
    model_config = {"populate_by_name": True}

    type: Literal["plan_approval_response"] = "plan_approval_response"
    schema_version: Literal[1] = 1
    request_id: str = Field(alias="requestId")
    approve: bool
    feedback: str | None = None
    timestamp: str


class PermissionRequest(BaseModel):
    model_config = {"populate_by_name": True}

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
    timestamp: str


class PermissionResponse(BaseModel):
    model_config = {"populate_by_name": True}

    type: Literal["permission_response"] = "permission_response"
    request_id: str = Field(alias="requestId")
    decision: Literal["allow_once", "allow_session", "deny"]
    reason: str | None = None
    timestamp: str | None = None


class TaskCompleted(BaseModel):
    model_config = {"populate_by_name": True}

    kind: Literal["task_complete"] = "task_complete"
    schema_version: Literal[1] = 1
    task_id: str
    files_changed: list[str] = Field(default_factory=list)
    summary: str
    codex_exit_code: int


class TaskBlocked(BaseModel):
    model_config = {"populate_by_name": True}

    kind: Literal["task_blocked"] = "task_blocked"
    schema_version: Literal[1] = 1
    task_id: str
    reason: str
    timestamp: str


class PlanBlocked(BaseModel):
    model_config = {"populate_by_name": True}

    kind: Literal["plan_blocked"] = "plan_blocked"
    schema_version: Literal[1] = 1
    request_id: str
    reason: str
    task_id: str | None = None
    timestamp: str


class TeamCreateResult(BaseModel):
    team_name: str
    team_file_path: str
    lead_agent_id: str


class TeamDeleteResult(BaseModel):
    success: bool
    message: str
    team_name: str


class SpawnResult(BaseModel):
    agent_id: str
    name: str
    team_name: str
    message: str = "The agent is now running and will receive instructions via mailbox."


class SendMessageResult(BaseModel):
    success: bool
    message: str
    routing: dict | None = None
    request_id: str | None = None
    target: str | None = None
