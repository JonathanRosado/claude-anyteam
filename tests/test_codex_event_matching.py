"""Tests for the JSONL event classifier used to detect MCP tool calls.

M5's acceptance depends on the adapter log showing at least one mid-task
tool call by Codex. `codex.py::_is_tool_call_event` recognises a broad
set of event-type shapes so we don't miss a legitimate tool call
because Codex's version emits an unexpected type name.
"""

from __future__ import annotations

from claude_anyteam.codex import _is_tool_call_event, _tool_name_from_event


def test_mcp_tool_call_matches():
    assert _is_tool_call_event("mcp_tool_call", {})
    assert _is_tool_call_event("MCP_TOOL_CALL", {})


def test_mcp_tool_call_pascal_case_matches():
    """Codex 0.120.0 emits PascalCase event types like `McpToolCall` and
    `McpToolCallProgress`. Classifier must match these too — observed
    live (in the binary's string table) during M5 debugging."""
    assert _is_tool_call_event("McpToolCall", {})
    assert _is_tool_call_event("McpToolCallProgress", {})


def test_function_call_matches():
    assert _is_tool_call_event("function_call", {})


def test_tool_use_matches():
    assert _is_tool_call_event("tool.use", {})


def test_item_prefix_with_tool_call_inner_type():
    ev = {"type": "item.started", "item": {"type": "mcp_tool_call", "name": "send_message"}}
    assert _is_tool_call_event("item.started", ev)


def test_plain_item_without_tool_inner_does_not_match():
    ev = {"type": "item.message", "item": {"type": "message", "role": "assistant"}}
    assert not _is_tool_call_event("item.message", ev)


def test_unrelated_event_does_not_match():
    assert not _is_tool_call_event("thread.started", {})
    assert not _is_tool_call_event("turn.completed", {})
    assert not _is_tool_call_event("error", {})


def test_tool_name_extraction_top_level():
    ev = {"type": "mcp_tool_call", "name": "send_message"}
    assert _tool_name_from_event(ev) == "send_message"


def test_tool_name_extraction_inner_item():
    ev = {
        "type": "item.completed",
        "item": {"type": "mcp_tool_call", "name": "task_update"},
    }
    assert _tool_name_from_event(ev) == "task_update"


def test_tool_name_extraction_app_server_tool_field():
    """App Server mcpToolCall items use `tool`, not `name`."""
    ev = {
        "type": "item/completed",
        "item": {
            "type": "mcpToolCall",
            "server": "claude_anyteam_wrapper",
            "tool": "send_message",
        },
    }
    assert _tool_name_from_event(ev) == "send_message"
    assert _is_tool_call_event("item/completed", ev)


def test_tool_name_returns_none_when_missing():
    ev = {"type": "mcp_tool_call"}
    assert _tool_name_from_event(ev) is None


# ---- backstop: event mentions a wrapper tool by name ------------------------
# M5 surfaced a real gap — Codex emitted events whose type names didn't match
# any of our substrings, but whose payloads referenced wrapper tools by name.
# The classifier now has a backstop matcher keyed off `_WRAPPER_TOOL_NAMES`.


def test_backstop_matches_event_with_wrapper_tool_name_top_level():
    """Even if the event type is bland, a `name` field referencing a wrapper
    tool counts as a tool call."""
    ev = {"type": "SomeEnvelope", "name": "task_update"}
    assert _is_tool_call_event("SomeEnvelope", ev)


def test_backstop_matches_event_with_wrapper_tool_name_nested():
    ev = {
        "type": "ItemSomething",
        "item": {"type": "message", "name": "send_message"},
    }
    assert _is_tool_call_event("ItemSomething", ev)


def test_backstop_matches_event_with_wrapper_tool_field_nested():
    ev = {
        "type": "ItemSomething",
        "item": {"type": "mcpToolCall", "tool": "send_message"},
    }
    assert _is_tool_call_event("ItemSomething", ev)


def test_backstop_ignores_non_wrapper_tool_names():
    """A tool name not in our wrapper surface must not register as a
    tool-call event — otherwise random events with `name` fields produce
    false positives."""
    ev = {"type": "SomeEnvelope", "name": "bash"}
    assert not _is_tool_call_event("SomeEnvelope", ev)
    ev_nested = {"type": "ItemSomething", "item": {"name": "shell_command"}}
    assert not _is_tool_call_event("ItemSomething", ev_nested)


def test_backstop_recognises_all_wrapper_tools():
    from claude_anyteam.codex import _WRAPPER_TOOL_NAMES
    for tool in _WRAPPER_TOOL_NAMES:
        assert _is_tool_call_event("whatever", {"type": "whatever", "name": tool})
