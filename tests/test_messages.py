"""Unit tests for protocol message parsing.

Focuses on robustness under partial/malformed input and on the two wire-format
variants (request_id / requestId) that the adapter has to accept on inbound.
"""

from __future__ import annotations

import json

from claude_anyteam.messages import (
    BatchSummaryChild,
    BatchSummaryPayload,
    IdleNotificationOut,
    NextTaskIn,
    PermissionRequestOut,
    PlanBlockedOut,
    PlanApprovalRequestIn,
    PlanApprovalResponseIn,
    ShutdownApprovedOut,
    ShutdownRejectedOut,
    ShutdownRequestIn,
    ShutdownResponseOut,
    TaskBlockedOut,
    TaskAssignmentIn,
    TaskCompleteOut,
    VisibilityEvent,
    KNOWN_TASK_BLOCKED_REASONS,
    parse_protocol_text,
)


def test_parse_task_assignment_snake_and_camel():
    body = {
        "type": "task_assignment",
        "taskId": "42",
        "subject": "Do the thing",
        "description": "And then the other thing",
        "assignedBy": "team-lead",
        "timestamp": "2026-04-21T22:00:00.000Z",
    }
    p = parse_protocol_text(json.dumps(body))
    assert isinstance(p, TaskAssignmentIn)
    assert p.task_id == "42"
    assert p.assigned_by == "team-lead"


def test_parse_next_task_wakeup():
    body = {
        "type": "next_task",
        "task_id": "43",
        "summary": "Auto-pickup task #43: Follow-up",
        "subject": "Follow-up",
        "completed_task_id": "42",
        "timestamp": "2026-05-12T12:00:00.000Z",
    }

    p = parse_protocol_text(json.dumps(body))

    assert isinstance(p, NextTaskIn)
    assert p.task_id == "43"
    assert p.completed_task_id == "42"


def test_parse_shutdown_request_snake_case():
    body = {"type": "shutdown_request", "request_id": "abc", "reason": "cleanup"}
    p = parse_protocol_text(json.dumps(body))
    assert isinstance(p, ShutdownRequestIn)
    assert p.effective_request_id() == "abc"
    assert p.reason == "cleanup"


def test_parse_shutdown_request_camel_case():
    # cs50victor's wire format.
    body = {"type": "shutdown_request", "requestId": "xyz", "from": "team-lead"}
    p = parse_protocol_text(json.dumps(body))
    assert isinstance(p, ShutdownRequestIn)
    assert p.effective_request_id() == "xyz"
    assert p.from_ == "team-lead"


def test_parse_plan_approval_request():
    body = {
        "type": "plan_approval_request",
        "requestId": "p1",
        "plan": {"steps": [{"summary": "Step one"}]},
    }
    p = parse_protocol_text(json.dumps(body))
    assert isinstance(p, PlanApprovalRequestIn)
    assert p.request_id == "p1"


def test_parse_non_json_returns_none():
    assert parse_protocol_text("hello world") is None


def test_parse_unknown_type_returns_none():
    body = {"type": "unknown", "x": 1}
    assert parse_protocol_text(json.dumps(body)) is None


def test_parse_malformed_but_typed_returns_none():
    # Type field present but required fields missing — model_validate will fail.
    body = {"type": "task_assignment"}  # missing everything
    assert parse_protocol_text(json.dumps(body)) is None


def test_parse_extra_fields_tolerated():
    body = {
        "type": "shutdown_request",
        "request_id": "abc",
        "reason": "cleanup",
        "extra_future_field": "ignored",
    }
    p = parse_protocol_text(json.dumps(body))
    assert isinstance(p, ShutdownRequestIn)


def test_shutdown_approved_serializes_host_catalog_shape():
    r = ShutdownApprovedOut(
        request_id="abc",
        from_="worker",
        pane_id="in-process",
        backend_type="codex",
    )
    as_dict = json.loads(r.model_dump_json(by_alias=True, exclude_none=True))
    assert as_dict["type"] == "shutdown_approved"
    assert as_dict["schema_version"] == 1
    assert as_dict["requestId"] == "abc"
    assert as_dict["from"] == "worker"
    assert as_dict["paneId"] == "in-process"
    assert as_dict["backendType"] == "codex"


def test_shutdown_rejected_serializes_host_catalog_shape():
    r = ShutdownRejectedOut(request_id="abc", from_="worker", reason="busy")
    as_dict = json.loads(r.model_dump_json(by_alias=True, exclude_none=True))
    assert as_dict["type"] == "shutdown_rejected"
    assert as_dict["schema_version"] == 1
    assert as_dict["requestId"] == "abc"
    assert as_dict["from"] == "worker"
    assert as_dict["reason"] == "busy"


def test_parse_shutdown_approved_and_rejected_host_catalog_shapes():
    approved = parse_protocol_text(
        json.dumps({"type": "shutdown_approved", "requestId": "a1", "from": "worker", "timestamp": "ts"})
    )
    rejected = parse_protocol_text(
        json.dumps({"type": "shutdown_rejected", "requestId": "r1", "from": "worker", "reason": "busy", "timestamp": "ts"})
    )
    assert isinstance(approved, ShutdownApprovedOut)
    assert approved.request_id == "a1"
    assert isinstance(rejected, ShutdownRejectedOut)
    assert rejected.reason == "busy"


def test_legacy_shutdown_response_alias_still_parses_for_one_release():
    r = ShutdownResponseOut(request_id="abc", approve=True)
    as_dict = json.loads(r.model_dump_json(by_alias=True, exclude_none=True))
    assert as_dict["type"] == "shutdown_response"
    assert as_dict["request_id"] == "abc"
    assert as_dict["approve"] is True
    assert "feedback" not in as_dict  # omitted when None
    parsed = parse_protocol_text(json.dumps(as_dict))
    assert isinstance(parsed, ShutdownResponseOut)


def test_legacy_shutdown_response_alias_maps_to_host_catalog_shape():
    r = ShutdownResponseOut(request_id="abc", approve=False, feedback="busy")
    host = r.to_host_catalog("worker")
    assert isinstance(host, ShutdownRejectedOut)
    as_dict = json.loads(host.model_dump_json(by_alias=True, exclude_none=True))
    assert as_dict["type"] == "shutdown_rejected"
    assert as_dict["schema_version"] == 1
    assert as_dict["requestId"] == "abc"
    assert as_dict["from"] == "worker"
    assert as_dict["reason"] == "busy"


def test_task_complete_serializes_with_kind():
    t = TaskCompleteOut(
        task_id="7",
        files_changed=["src/foo.py"],
        summary="Did the thing",
        codex_exit_code=0,
    )
    as_dict = json.loads(t.model_dump_json(by_alias=True, exclude_none=True))
    assert as_dict["kind"] == "task_complete"
    assert as_dict["task_id"] == "7"
    assert as_dict["files_changed"] == ["src/foo.py"]
    assert as_dict["codex_exit_code"] == 0


def test_batch_summary_payload_serializes_delegated_child_links():
    payload = BatchSummaryPayload(
        parent_task_id="40",
        child_task_ids=["41", "42"],
        child_tasks=[
            BatchSummaryChild(
                task_id="41",
                status="completed",
                session_id="session-41",
                stop_reason="task_complete",
                summary="child one done",
            ),
            BatchSummaryChild(
                task_id="42",
                status="blocked",
                stop_reason="needs review",
            ),
        ],
        summary="batch finished",
    )

    as_dict = payload.model_dump(by_alias=True, exclude_none=True)
    assert as_dict == {
        "parentTaskId": "40",
        "childTaskIds": ["41", "42"],
        "childTasks": [
            {
                "taskId": "41",
                "status": "completed",
                "sessionId": "session-41",
                "stopReason": "task_complete",
                "summary": "child one done",
            },
            {
                "taskId": "42",
                "status": "blocked",
                "stopReason": "needs review",
            },
        ],
        "summary": "batch finished",
    }


def test_parse_batch_summary_visibility_event():
    event = VisibilityEvent(
        kind="batch_summary",
        event_id="worker:batch-summary:40:abc123",
        team="team-x",
        agent="worker",
        backend="claude_teams_server",
        task_id="40",
        seq=0,
        summary="batch finished",
        payload={
            "parentTaskId": "40",
            "childTaskIds": ["41"],
            "childTasks": [{"taskId": "41", "status": "completed"}],
            "summary": "batch finished",
        },
    )

    parsed = parse_protocol_text(event.model_dump_json(by_alias=True, exclude_none=True))

    assert isinstance(parsed, VisibilityEvent)
    assert parsed.kind == "batch_summary"
    assert parsed.payload["parentTaskId"] == "40"


def test_parse_wrapper_tool_failure_unrecovered_visibility_event():
    event = VisibilityEvent(
        kind="wrapper_tool_failure_unrecovered",
        event_id="worker:turn-1:000003",
        team="team-x",
        agent="worker",
        backend="codex_app_server",
        task_id="49",
        turn_id="turn-1",
        seq=3,
        severity="warn",
        summary="wrapper tool failed without recovery",
        payload={
            "tool_name": "mcp_anyteam_read_file",
            "error_class": "enoent",
            "silence_window_ms": 90000,
            "recovery_hint_dispatched": False,
        },
    )

    parsed = parse_protocol_text(event.model_dump_json(by_alias=True, exclude_none=True))

    assert isinstance(parsed, VisibilityEvent)
    assert parsed.kind == "wrapper_tool_failure_unrecovered"
    assert "wrapper_tool_failure_unrecovered" in KNOWN_TASK_BLOCKED_REASONS


def test_parse_team_kill_completed_visibility_event():
    event = VisibilityEvent(
        kind="team_kill_completed",
        event_id="team-lead:team-kill:abc123",
        team="team-x",
        agent="team-lead",
        backend="claude_teams.teardown",
        seq=0,
        severity="info",
        summary="team killed",
        payload={
            "surface": "team_kill_completed",
            "graceful": ["codex-a"],
            "forced": ["gemini-b"],
            "elapsed_s": 1.2,
        },
    )

    parsed = parse_protocol_text(event.model_dump_json(by_alias=True, exclude_none=True))

    assert isinstance(parsed, VisibilityEvent)
    assert parsed.kind == "team_kill_completed"
    assert parsed.payload["surface"] == "team_kill_completed"


def test_parse_typed_lifecycle_payload_variants():
    examples = [
        (
            {"type": "idle_notification", "from": "worker", "idleReason": "available"},
            IdleNotificationOut,
        ),
        (
            {
                "kind": "task_complete",
                "task_id": "7",
                "files_changed": ["src/foo.py"],
                "summary": "done",
                "codex_exit_code": 0,
            },
            TaskCompleteOut,
        ),
        (
            {"kind": "task_blocked", "task_id": "7", "reason": "missing approval"},
            TaskBlockedOut,
        ),
        (
            {
                "kind": "plan_blocked",
                "request_id": "p1",
                "reason": "no claimable task",
            },
            PlanBlockedOut,
        ),
        (
            {
                "type": "plan_approval_response",
                "requestId": "p1",
                "approve": False,
                "feedback": "revise",
            },
            PlanApprovalResponseIn,
        ),
        (
            {
                "type": "permission_request",
                "request_id": "perm-1",
                "tool_name": "Bash",
                "tool_args": {"cmd": "pytest"},
                "task_id": "7",
                "teammate_name": "worker",
                "trust_mode": "default",
            },
            PermissionRequestOut,
        ),
    ]

    for body, cls in examples:
        parsed = parse_protocol_text(json.dumps(body))
        assert isinstance(parsed, cls)
