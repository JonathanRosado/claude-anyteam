"""Terminal visibility digests for headless subprocess backends.

The visibility envelope normalizes routing fields only.  Backend payloads keep
their own raw event lists and count semantics so Gemini/Kimi/Codex can be
replayed without flattening their native streams.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import logger
from .messages import VisibilityEvent
from . import protocol_io as pio


def coerce_stream_text(value: Any) -> str:
    """Return subprocess timeout stdout/stderr as text."""

    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def prompt_kind_for_schema(schema: Any) -> str:
    """Best-effort prompt kind for the turn_started payload."""

    if schema is None:
        return "prose_reply"
    name = ""
    try:
        name = Path(str(schema)).name
    except TypeError:
        name = str(schema)
    if "plan" in name:
        return "plan"
    return "task_complete"


def error_class_for_terminal(*, exit_code: int, error: str | None, default: str | None = None) -> str | None:
    if exit_code == 124:
        return "turn_timeout"
    if default:
        return default
    if exit_code != 0:
        return "process_exit"
    if error:
        lowered = error.lower()
        if "schema" in lowered or "valid json" in lowered:
            return "schema_validation_failed"
        return "turn_error"
    return None


@dataclass
class HeadlessTurnVisibility:
    team: str
    agent: str
    backend: str
    enabled: bool
    task_id: str | None = None
    turn_id: str | None = None
    started_at: float = 0.0
    seq: int = 0

    @classmethod
    def start(
        cls,
        *,
        team: str,
        agent: str,
        backend: str,
        enabled: bool,
        cwd: Path,
        schema: Any,
        timeout_s: float,
        model: str | None,
        effort: str | None,
        resume_session_id: str | None,
        task_id: str | None = None,
        extra_payload: dict[str, Any] | None = None,
    ) -> "HeadlessTurnVisibility":
        turn = cls(
            team=team,
            agent=agent,
            backend=backend,
            enabled=enabled,
            task_id=task_id,
            turn_id=f"{backend}-{uuid.uuid4().hex[:12]}",
            started_at=time.monotonic(),
        )
        payload: dict[str, Any] = {
            "mode": "prose" if schema is None else "task",
            "prompt_kind": prompt_kind_for_schema(schema),
            "timeout_s": timeout_s,
            "cwd": str(cwd),
            "model": model,
            "effort": effort,
        }
        if resume_session_id:
            payload["resume_session_id"] = resume_session_id
        if extra_payload:
            payload.update(extra_payload)
        turn.emit(
            kind="turn_started",
            severity="info",
            summary=f"{backend} turn started",
            payload=payload,
        )
        return turn

    def emit(
        self,
        *,
        kind: str,
        severity: str,
        summary: str,
        payload: dict[str, Any],
    ) -> VisibilityEvent | None:
        if not self.enabled:
            return None
        self.seq += 1
        turn_ref = self.turn_id or "headless-turn"
        event = VisibilityEvent.model_validate(
            {
                "kind": kind,
                "event_id": f"{self.agent}:{turn_ref}:{self.seq:06d}",
                "team": self.team,
                "agent": self.agent,
                "backend": self.backend,
                "task_id": self.task_id,
                "turn_id": self.turn_id,
                "seq": self.seq,
                "severity": severity,
                "summary": summary,
                "visibility": {
                    "mailbox": False,
                    "task_state": False,
                    "event_log": True,
                    "stderr": True,
                },
                "payload": payload,
            }
        )
        try:
            pio.append_event(self.team, self.agent, event)
        except Exception as exc:
            logger.warn(
                "headless_visibility.append_fail",
                backend=self.backend,
                kind=kind,
                agent=self.agent,
                error=str(exc),
            )
        return event

    def terminal(
        self,
        *,
        success: bool,
        exit_code: int,
        error: str | None,
        events: list[dict[str, Any]],
        tool_call_events: int,
        last_message: str,
        structured: bool,
        partial_events_available: bool | None = None,
        session_id: str | None = None,
        error_class: str | None = None,
        extra_payload: dict[str, Any] | None = None,
    ) -> VisibilityEvent | None:
        payload: dict[str, Any] = {
            "exit_code": exit_code,
            "elapsed_s": round(time.monotonic() - self.started_at, 3),
            "structured": structured,
            "events": events,
            "event_count": len(events),
            "tool_call_events": tool_call_events,
            "last_message_preview": last_message[:500],
            "partial_events_available": bool(events)
            if partial_events_available is None
            else partial_events_available,
        }
        if error:
            payload["error"] = error
        if session_id:
            payload["session_id"] = session_id
        if not success:
            payload["error_class"] = error_class_for_terminal(
                exit_code=exit_code,
                error=error,
                default=error_class,
            )
        if extra_payload:
            payload.update(extra_payload)
        return self.emit(
            kind="turn_completed" if success else "turn_failed",
            severity="info" if success else "error",
            summary=(
                f"{self.backend} turn completed"
                if success
                else f"{self.backend} turn failed"
            ),
            payload=payload,
        )
