"""Terminal visibility digests for headless subprocess backends.

The visibility envelope normalizes routing fields only.  Backend payloads keep
their own raw event lists and count semantics so Gemini/Kimi/Codex can be
replayed without flattening their native streams.
"""

from __future__ import annotations

import time
import uuid
import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
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


def _json_preview(value: Any, *, limit: int = 1000) -> str:
    try:
        text = json.dumps(value, sort_keys=True, default=str)
    except Exception:
        text = repr(value)
    return text[:limit] + ("…" if len(text) > limit else "")


def _tool_call_name(call: Any) -> str | None:
    if not isinstance(call, dict):
        return None
    function = call.get("function")
    if isinstance(function, dict) and isinstance(function.get("name"), str):
        return function["name"]
    if isinstance(call.get("name"), str):
        return call["name"]
    if isinstance(call.get("tool_name"), str):
        return call["tool_name"]
    return None


def _tool_call_target(call: dict[str, Any]) -> Any:
    function = call.get("function")
    if isinstance(function, dict):
        return function.get("arguments")
    for key in ("arguments", "input", "command", "query", "path"):
        if key in call:
            return call.get(key)
    return None


def _category_for_tool(*, raw_type: str, tool_name: str | None, source: str | None) -> str:
    joined = " ".join(part.lower() for part in (raw_type, tool_name or "", source or ""))
    if "mcp" in joined or (tool_name or "").startswith("mcp_"):
        return "mcp_tool"
    if source in {"gemini_acp", "gemini_headless"}:
        return "mcp_tool"
    return "host_tool"


def _headless_tool_event_payloads(
    events: list[dict[str, Any]],
    *,
    source: str | None,
) -> list[dict[str, Any]]:
    """Extract best-effort per-tool envelopes from backend-native streams.

    The terminal digest still carries the full raw event list. These payloads
    add the stable ``tool_event`` envelope required by 07 §7 without flattening
    away the backend-native shape: raw type/name/id/details stay in payload.
    """

    payloads: list[dict[str, Any]] = []
    for idx, ev in enumerate(events):
        if not isinstance(ev, dict):
            continue
        ev_type = str(ev.get("type") or "")
        # Gemini headless and ACP-normalized streams expose tool invocations as
        # type=tool_use. Codex exec JSONL has used mcp_tool_call/tool_call-like
        # names across releases. Tool *results* are terminal data, not a new
        # invocation, so do not count them here.
        if ev_type and any(
            frag in ev_type.lower()
            for frag in ("tool_use", "mcp_tool_call", "tool_call", "function_call")
        ) and "result" not in ev_type.lower():
            tool_name = (
                ev.get("tool_name")
                or ev.get("name")
                or ev.get("title")
                or ev.get("function_name")
            )
            tool_name = str(tool_name) if tool_name else ev_type
            raw_source = str(ev.get("source") or source or "")
            target = (
                ev.get("target")
                or ev.get("command")
                or ev.get("query")
                or ev.get("arguments")
                or ev.get("input")
            )
            payloads.append(
                {
                    "category": _category_for_tool(
                        raw_type=ev_type,
                        tool_name=tool_name,
                        source=raw_source,
                    ),
                    "tool_name": tool_name,
                    "phase": ev.get("phase") or ev.get("status") or "started",
                    "target": target,
                    "status": ev.get("status"),
                    "raw_backend_type": ev_type,
                    "raw_event_index": idx,
                    "raw_event_preview": _json_preview(ev),
                    "tool_call_id": ev.get("tool_call_id") or ev.get("id"),
                    "tool_event_source": source,
                }
            )

        tool_calls = ev.get("tool_calls")
        if isinstance(tool_calls, list):
            for call_idx, call in enumerate(tool_calls):
                if not isinstance(call, dict):
                    continue
                tool_name = _tool_call_name(call) or "tool_call"
                raw_type = str(call.get("type") or "assistant.tool_calls[]")
                payloads.append(
                    {
                        "category": _category_for_tool(
                            raw_type=raw_type,
                            tool_name=tool_name,
                            source=source,
                        ),
                        "tool_name": tool_name,
                        "phase": "started",
                        "target": _tool_call_target(call),
                        "raw_backend_type": "assistant.tool_calls[]",
                        "raw_event_index": idx,
                        "raw_tool_call_index": call_idx,
                        "raw_event_preview": _json_preview(call),
                        "tool_call_id": call.get("id"),
                        "tool_event_source": source,
                    }
                )
    return [
        {key: value for key, value in payload.items() if value is not None}
        for payload in payloads
    ]


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
    mode: str = "task"
    prompt_kind: str = "task_complete"

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
        prompt_kind = prompt_kind_for_schema(schema)
        mode = "prose" if schema is None else "task"
        turn = cls(
            team=team,
            agent=agent,
            backend=backend,
            enabled=enabled,
            task_id=task_id,
            turn_id=f"{backend}-{uuid.uuid4().hex[:12]}",
            started_at=time.monotonic(),
            mode=mode,
            prompt_kind=prompt_kind,
        )
        payload: dict[str, Any] = {
            "mode": mode,
            "prompt_kind": prompt_kind,
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
        last_message_preview = last_message[:500]
        suppress_preview = (
            self.mode == "prose"
            and pio.should_skip_prose_fallback(
                SimpleNamespace(
                    exit_code=exit_code,
                    events=events,
                    tool_call_events=tool_call_events,
                )
            )
        )
        if suppress_preview:
            last_message_preview = ""
        payload: dict[str, Any] = {
            "exit_code": exit_code,
            "elapsed_s": round(time.monotonic() - self.started_at, 3),
            "structured": structured,
            "events": events,
            "event_count": len(events),
            "tool_call_events": tool_call_events,
            "last_message_preview": last_message_preview,
            "partial_events_available": bool(events)
            if partial_events_available is None
            else partial_events_available,
        }
        if suppress_preview:
            payload["last_message_suppressed_reason"] = "delivered_via_send_message_tool"
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
        source = payload.get("tool_call_event_source")
        source = source if isinstance(source, str) else None
        for tool_payload in _headless_tool_event_payloads(events, source=source):
            name = str(tool_payload.get("tool_name") or "tool")
            target = tool_payload.get("target")
            summary = f"{name}: {target}" if target else name
            self.emit(
                kind="tool_event",
                severity="info",
                summary=summary[:200],
                payload=tool_payload,
            )
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
