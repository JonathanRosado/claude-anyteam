"""Regression coverage for send_message repair-path visibility telemetry."""

from __future__ import annotations

from typing import Any

from claude_anyteam.codex import _visibility_for_app_server_item
from claude_anyteam.messages import VisibilityEvent


def _make_event(
    *,
    kind: str,
    severity: str,
    summary: str,
    payload: dict[str, Any],
    visibility: dict[str, bool] | None = None,
) -> VisibilityEvent:
    return VisibilityEvent.model_validate(
        {
            "kind": kind,
            "event_id": "codex-pair:repair-turn:000001",
            "team": "stress-fixture",
            "agent": "codex-pair",
            "backend": "codex_app_server",
            "task_id": None,
            "turn_id": "repair-turn",
            "seq": 1,
            "severity": severity,
            "summary": summary,
            "visibility": visibility
            or {
                "mailbox": False,
                "task_state": False,
                "event_log": True,
                "stderr": True,
            },
            "payload": payload,
        }
    )


def test_app_server_send_message_event_stamps_recipient_aliases_from_arguments():
    """#51 repair retries emit App Server mcpToolCall items.

    The stress scorer consumes normalized visibility payload fields; it must
    not have to scrape ``raw_event_preview`` for the ``arguments.to`` value.
    """

    event = _visibility_for_app_server_item(
        method="item/completed",
        item={
            "type": "mcpToolCall",
            "server": "claude_anyteam_wrapper",
            "tool": "send_message",
            "status": "completed",
            "durationMs": 6,
            "arguments": {
                "to": "kimi-pair",
                "body": "Answer: repaired delivery.",
                "summary": "Answer: repaired delivery",
            },
            "result": {
                "structuredContent": {
                    "delivered_to": "kimi-pair",
                    "sender": "codex-pair",
                }
            },
        },
        make_event=_make_event,
    )

    assert event is not None
    assert event.kind == "tool_event"
    assert event.payload["tool_name"] == "send_message"
    assert event.payload["phase"] == "completed"
    assert event.payload["recipient"] == "kimi-pair"
    assert event.payload["to"] == "kimi-pair"
    assert event.payload["target"] == "to='kimi-pair'"


def test_app_server_send_message_event_falls_back_to_structured_result_recipient():
    event = _visibility_for_app_server_item(
        method="item/completed",
        item={
            "type": "mcpToolCall",
            "server": "claude_anyteam_wrapper",
            "tool": "send_message",
            "status": "completed",
            "result": {
                "structuredContent": {
                    "delivered_to": "codex-pair",
                    "sender": "kimi-pair",
                }
            },
        },
        make_event=_make_event,
    )

    assert event is not None
    assert event.payload["recipient"] == "codex-pair"
    assert event.payload["to"] == "codex-pair"
