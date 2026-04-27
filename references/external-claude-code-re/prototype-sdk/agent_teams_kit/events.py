from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .messages import now_iso


EventKind = Literal[
    "turn_started",
    "turn_progress",
    "tool_event",
    "artifact_event",
    "turn_completed",
    "turn_failed",
    "visibility_degraded",
]
Severity = Literal["debug", "info", "warn", "error"]


class VisibilityFlags(BaseModel):
    mailbox: bool = False
    task_state: bool = False
    event_log: bool = True
    stderr: bool = True


# B9 §6 / 07 §7.2 visibility envelope: typed routing shell, rich payload preserved.
class VisibilityEvent(BaseModel):
    model_config = ConfigDict(extra="allow")

    kind: EventKind
    schema_version: int = 1
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = Field(default_factory=now_iso)
    team: str
    agent: str
    backend: str = "prototype"
    task_id: str | None = None
    turn_id: str | None = None
    seq: int = 0
    severity: Severity = "info"
    visibility: VisibilityFlags = Field(default_factory=VisibilityFlags)
    summary: str
    payload: dict[str, Any] = Field(default_factory=dict)

    def json_line(self) -> str:
        return self.model_dump_json(exclude_none=True)
