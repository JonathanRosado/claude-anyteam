from __future__ import annotations

import json

import pytest

from claude_teams.models import (
    COLOR_PALETTE,
    IdleNotification,
    InboxAttachment,
    InboxMessage,
    LeadMember,
    NextTaskWakeup,
    PermissionRequest,
    PlanApprovalResponse,
    PlanBlocked,
    SendMessageResult,
    ShutdownApproved,
    ShutdownRequest,
    SpawnResult,
    TaskAssignment,
    TaskBlocked,
    TaskCompleted,
    TaskFile,
    TeamConfig,
    TeamCreateResult,
    TeamDeleteResult,
    TeammateMember,
)


class TestColorPalette:
    def test_has_8_colors(self):
        assert len(COLOR_PALETTE) == 8

    def test_blue_first(self):
        assert COLOR_PALETTE[0] == "blue"

    def test_all_expected_colors_present(self):
        expected = {
            "blue",
            "green",
            "yellow",
            "purple",
            "orange",
            "pink",
            "cyan",
            "red",
        }
        assert set(COLOR_PALETTE) == expected


class TestLeadMember:
    def test_serializes_with_camel_case_aliases(self):
        lead = LeadMember(
            agent_id="team-lead@my-team",
            name="team-lead",
            agent_type="team-lead",
            model="claude-opus-4-6",
            joined_at=1770398183858,
            tmux_pane_id="",
            cwd="/tmp/work",
        )
        data = lead.model_dump(by_alias=True)
        assert data["agentId"] == "team-lead@my-team"
        assert data["agentType"] == "team-lead"
        assert data["joinedAt"] == 1770398183858
        assert data["tmuxPaneId"] == ""
        assert data["subscriptions"] == []

    def test_deserializes_from_camel_case_json(self):
        raw = {
            "agentId": "team-lead@my-team",
            "name": "team-lead",
            "agentType": "team-lead",
            "model": "claude-opus-4-6",
            "joinedAt": 1770398183858,
            "tmuxPaneId": "",
            "cwd": "/tmp/work",
            "subscriptions": [],
        }
        lead = LeadMember.model_validate(raw)
        assert lead.agent_id == "team-lead@my-team"
        assert lead.joined_at == 1770398183858

    def test_default_tmux_pane_id_is_empty(self):
        lead = LeadMember(
            agent_id="team-lead@t",
            name="team-lead",
            model="sonnet",
            joined_at=0,
            cwd="/tmp",
        )
        assert lead.tmux_pane_id == ""
        assert lead.agent_type == "team-lead"


class TestTeammateMember:
    def test_serializes_with_all_fields(self):
        mate = TeammateMember(
            agent_id="worker@my-team",
            name="worker",
            agent_type="general-purpose",
            model="sonnet",
            prompt="Do the work",
            color="blue",
            plan_mode_required=False,
            joined_at=1770398210601,
            tmux_pane_id="%34",
            cwd="/tmp/work",
            backend_type="claude",
            is_active=False,
            capabilities=["structured_output"],
        )
        data = mate.model_dump(by_alias=True)
        assert data["agentId"] == "worker@my-team"
        assert data["planModeRequired"] is False
        assert data["tmuxPaneId"] == "%34"
        assert data["backendType"] == "claude"
        assert data["isActive"] is False
        assert data["capabilities"] == ["structured_output"]

    def test_defaults(self):
        mate = TeammateMember(
            agent_id="w@t",
            name="w",
            agent_type="general-purpose",
            model="sonnet",
            prompt="p",
            color="blue",
            joined_at=0,
            tmux_pane_id="%1",
            cwd="/tmp",
        )
        assert mate.plan_mode_required is False
        assert mate.backend_type == "claude"
        assert mate.is_active is False
        assert mate.subscriptions == []
        assert mate.capabilities == []

    def test_capabilities_default_missing_config_row(self):
        raw = {
            "agentId": "w@t",
            "name": "w",
            "agentType": "claude-anyteam",
            "model": "codex-cli",
            "prompt": "p",
            "color": "blue",
            "joinedAt": 0,
            "tmuxPaneId": "in-process",
            "cwd": "/tmp",
        }
        mate = TeammateMember.model_validate(raw)
        assert mate.capabilities == []

    def test_capabilities_loads_from_config_row(self):
        raw = {
            "agentId": "w@t",
            "name": "w",
            "agentType": "claude-anyteam",
            "model": "codex-cli",
            "prompt": "p",
            "color": "blue",
            "joinedAt": 0,
            "tmuxPaneId": "in-process",
            "cwd": "/tmp",
            "capabilities": ["turn_steer", "thread_fork"],
        }
        mate = TeammateMember.model_validate(raw)
        assert mate.capabilities == ["turn_steer", "thread_fork"]

    def test_backend_type_defaults_to_claude(self):
        mate = TeammateMember(
            agent_id="w@t",
            name="w",
            agent_type="gp",
            model="sonnet",
            prompt="p",
            color="blue",
            joined_at=0,
            tmux_pane_id="",
            cwd="/tmp",
        )
        assert mate.backend_type == "claude"
        data = mate.model_dump(by_alias=True)
        assert data["backendType"] == "claude"

    def test_agent_type_defaults_to_claude_anyteam_for_legacy_rows(self):
        mate = TeammateMember(
            agent_id="w@t",
            name="w",
            model="sonnet",
            prompt="p",
            color="blue",
            joined_at=0,
            tmux_pane_id="",
            cwd="/tmp",
        )
        assert mate.agent_type == "claude-anyteam"
        assert mate.model_dump(by_alias=True)["agentType"] == "claude-anyteam"

class TestTeamConfig:
    def test_round_trip_with_lead_only(self):
        lead = LeadMember(
            agent_id="team-lead@test",
            name="team-lead",
            agent_type="team-lead",
            model="claude-opus-4-6",
            joined_at=1770398183858,
            cwd="/tmp",
        )
        config = TeamConfig(
            name="test",
            description="A test team",
            created_at=1770398183858,
            lead_agent_id="team-lead@test",
            lead_session_id="abc-123",
            members=[lead],
        )
        raw = json.loads(config.model_dump_json(by_alias=True))
        assert raw["createdAt"] == 1770398183858
        assert raw["leadAgentId"] == "team-lead@test"
        assert raw["leadSessionId"] == "abc-123"
        assert raw["auto_pickup_next_task"] is False
        assert config.auto_pickup_next_task is False
        assert len(raw["members"]) == 1

    def test_auto_pickup_next_task_defaults_false_for_legacy_config(self):
        raw = {
            "name": "test",
            "description": "",
            "createdAt": 100,
            "leadAgentId": "team-lead@test",
            "leadSessionId": "sid",
            "members": [
                {
                    "agentId": "team-lead@test",
                    "name": "team-lead",
                    "agentType": "team-lead",
                    "model": "opus",
                    "joinedAt": 100,
                    "tmuxPaneId": "",
                    "cwd": "/tmp",
                    "subscriptions": [],
                },
            ],
        }

        config = TeamConfig.model_validate(raw)

        assert config.auto_pickup_next_task is False

    def test_auto_pickup_next_task_can_be_enabled(self):
        raw = {
            "name": "test",
            "description": "",
            "createdAt": 100,
            "leadAgentId": "team-lead@test",
            "leadSessionId": "sid",
            "auto_pickup_next_task": True,
            "members": [
                {
                    "agentId": "team-lead@test",
                    "name": "team-lead",
                    "agentType": "team-lead",
                    "model": "opus",
                    "joinedAt": 100,
                    "tmuxPaneId": "",
                    "cwd": "/tmp",
                    "subscriptions": [],
                },
            ],
        }

        config = TeamConfig.model_validate(raw)

        assert config.auto_pickup_next_task is True

    def test_deserializes_mixed_members(self):
        raw = {
            "name": "test",
            "description": "",
            "createdAt": 100,
            "leadAgentId": "team-lead@test",
            "leadSessionId": "sid",
            "members": [
                {
                    "agentId": "team-lead@test",
                    "name": "team-lead",
                    "agentType": "team-lead",
                    "model": "opus",
                    "joinedAt": 100,
                    "tmuxPaneId": "",
                    "cwd": "/tmp",
                    "subscriptions": [],
                },
                {
                    "agentId": "worker@test",
                    "name": "worker",
                    "agentType": "general-purpose",
                    "model": "sonnet",
                    "prompt": "do stuff",
                    "color": "blue",
                    "planModeRequired": False,
                    "joinedAt": 200,
                    "tmuxPaneId": "%5",
                    "cwd": "/tmp",
                    "subscriptions": [],
                    "backendType": "claude",
                    "isActive": False,
                },
            ],
        }
        config = TeamConfig.model_validate(raw)
        assert len(config.members) == 2
        assert isinstance(config.members[0], LeadMember)
        assert isinstance(config.members[1], TeammateMember)
        assert config.members[1].capabilities == []

    def test_deserializes_legacy_members_missing_agent_type(self):
        raw = {
            "name": "test",
            "description": "",
            "createdAt": 100,
            "leadAgentId": "team-lead@test",
            "leadSessionId": "sid",
            "members": [
                {
                    "agentId": "team-lead@test",
                    "name": "team-lead",
                    "model": "opus",
                    "joinedAt": 100,
                    "tmuxPaneId": "",
                    "cwd": "/tmp",
                    "subscriptions": [],
                },
                {
                    "agentId": "worker@test",
                    "name": "worker",
                    "model": "sonnet",
                    "prompt": "do stuff",
                    "color": "blue",
                    "planModeRequired": False,
                    "joinedAt": 200,
                    "tmuxPaneId": "%5",
                    "cwd": "/tmp",
                    "subscriptions": [],
                    "backendType": "claude",
                    "isActive": False,
                },
            ],
        }
        config = TeamConfig.model_validate(raw)
        assert isinstance(config.members[0], LeadMember)
        assert config.members[0].agent_type == "team-lead"
        assert isinstance(config.members[1], TeammateMember)
        assert config.members[1].agent_type == "claude-anyteam"



class TestTaskFile:
    def test_initial_task_excludes_none_fields(self):
        task = TaskFile(id="1", subject="Do thing", description="Details")
        data = task.model_dump(by_alias=True, exclude_none=True)
        assert "owner" not in data
        assert "metadata" not in data
        assert "parentTaskId" not in data
        assert data["id"] == "1"
        assert data["status"] == "pending"
        assert data["blockedBy"] == []

    def test_task_with_owner_includes_it(self):
        task = TaskFile(id="2", subject="s", description="d", owner="worker")
        data = task.model_dump(by_alias=True, exclude_none=True)
        assert data["owner"] == "worker"

    def test_task_with_parent_id_uses_camel_alias(self):
        task = TaskFile(
            id="2",
            subject="child",
            description="delegated",
            parent_task_id="1",
        )
        data = task.model_dump(by_alias=True, exclude_none=True)
        assert data["parentTaskId"] == "1"
        round_trip = TaskFile.model_validate(data)
        assert round_trip.parent_task_id == "1"

    def test_id_is_string(self):
        task = TaskFile(id="1", subject="s", description="d")
        assert isinstance(task.id, str)


class TestInboxMessage:
    def test_serializes_with_from_alias(self):
        msg = InboxMessage(
            from_="team-lead",
            text="hello",
            timestamp="2026-02-06T17:18:04.701Z",
            read=False,
            summary="greeting",
        )
        data = msg.model_dump(by_alias=True, exclude_none=True)
        assert data["from"] == "team-lead"
        assert "color" not in data
        assert data["summary"] == "greeting"

    def test_optional_fields_excluded_when_none(self):
        msg = InboxMessage(
            from_="w",
            text="t",
            timestamp="ts",
        )
        data = msg.model_dump(by_alias=True, exclude_none=True)
        assert "summary" not in data
        assert "color" not in data

    def test_with_color(self):
        msg = InboxMessage(
            from_="worker",
            text="done",
            timestamp="ts",
            color="blue",
            summary="status",
        )
        data = msg.model_dump(by_alias=True, exclude_none=True)
        assert data["color"] == "blue"

    def test_with_attachment_metadata(self):
        msg = InboxMessage(
            from_="worker",
            text="preview",
            timestamp="ts",
            attachment=InboxAttachment(
                path="/tmp/full.txt",
                relativePath="artifacts/inbox/full.txt",
                charCount=5000,
                previewCharCount=4096,
                sha256="abc123",
            ),
        )
        data = msg.model_dump(by_alias=True, exclude_none=True)
        assert data["attachment"] == {
            "kind": "artifact",
            "path": "/tmp/full.txt",
            "relativePath": "artifacts/inbox/full.txt",
            "mimeType": "text/plain",
            "charCount": 5000,
            "previewCharCount": 4096,
            "sha256": "abc123",
        }


class TestStructuredMessages:
    def test_idle_notification(self):
        n = IdleNotification(
            from_="worker",
            timestamp="2026-02-06T17:18:04.701Z",
        )
        data = json.loads(n.model_dump_json(by_alias=True))
        assert data["type"] == "idle_notification"
        assert data["from"] == "worker"
        assert data["idleReason"] == "available"

    def test_task_assignment(self):
        a = TaskAssignment(
            task_id="1",
            subject="Do thing",
            description="Details",
            assigned_by="team-lead",
            timestamp="2026-02-06T17:18:04.701Z",
        )
        data = json.loads(a.model_dump_json(by_alias=True))
        assert data["type"] == "task_assignment"
        assert data["taskId"] == "1"
        assert data["assignedBy"] == "team-lead"

    def test_next_task_wakeup(self):
        n = NextTaskWakeup(
            task_id="2",
            summary="Auto-pickup task #2: Follow-up",
            subject="Follow-up",
            completed_task_id="1",
            timestamp="2026-05-12T12:00:00.000Z",
        )
        data = json.loads(n.model_dump_json(by_alias=True))
        assert data["type"] == "next_task"
        assert data["task_id"] == "2"
        assert data["completed_task_id"] == "1"
        assert data["summary"].startswith("Auto-pickup task #2")

    def test_shutdown_request(self):
        r = ShutdownRequest(
            request_id="shutdown-1770398300000@worker",
            from_="team-lead",
            reason="Done",
            timestamp="ts",
        )
        data = json.loads(r.model_dump_json(by_alias=True))
        assert data["type"] == "shutdown_request"
        assert data["requestId"] == "shutdown-1770398300000@worker"
        assert data["from"] == "team-lead"

    def test_shutdown_approved(self):
        a = ShutdownApproved(
            request_id="shutdown-123@worker",
            from_="worker",
            timestamp="ts",
            pane_id="%34",
            backend_type="claude",
        )
        data = json.loads(a.model_dump_json(by_alias=True))
        assert data["type"] == "shutdown_approved"
        assert data["paneId"] == "%34"
        assert data["backendType"] == "claude"

    def test_shutdown_approved_session_id_defaults_to_none(self):
        a = ShutdownApproved(
            request_id="r",
            from_="w",
            timestamp="ts",
            pane_id="%1",
            backend_type="claude",
        )
        assert a.session_id is None

    def test_shutdown_approved_session_id_excluded_when_none(self):
        a = ShutdownApproved(
            request_id="r",
            from_="w",
            timestamp="ts",
            pane_id="%1",
            backend_type="claude",
        )
        data = a.model_dump(by_alias=True, exclude_none=True)
        assert "sessionId" not in data


class TestToolReturnModels:
    def test_team_create_result(self):
        r = TeamCreateResult(
            team_name="t",
            team_file_path="/p",
            lead_agent_id="team-lead@t",
        )
        assert r.team_name == "t"

    def test_team_delete_result(self):
        r = TeamDeleteResult(
            success=True,
            message='Cleaned up directories and worktrees for team "t"',
            team_name="t",
        )
        assert r.success is True

    def test_spawn_result(self):
        r = SpawnResult(agent_id="w@t", name="w", team_name="t")
        assert (
            r.message
            == "The agent is now running and will receive instructions via mailbox."
        )

    def test_send_message_result(self):
        r = SendMessageResult(success=True, message="sent")
        data = r.model_dump(exclude_none=True)
        assert "routing" not in data
        assert "request_id" not in data


class TestInboxMessageKindRoundTrip:
    """09 R3 Q1 option (b): message_kind survives the substrate's
    read_inbox(mark_as_read=True) rewrite path. Regression test for the
    Pydantic-strip hazard flagged by opus-arch-impl post-546b72c."""

    def test_message_kind_default_peer_dm(self):
        m = InboxMessage(
            **{"from": "alice", "text": "hi", "timestamp": "2026-04-27T00:00:00Z"}
        )
        assert m.message_kind == "peer_dm"

    def test_message_kind_explicit_camel_case_alias_round_trip(self):
        # write a raw inbox entry with messageKind (camelCase)
        raw_in = {
            "from": "alice",
            "text": "watching tool call",
            "timestamp": "2026-04-27T00:00:00Z",
            "messageKind": "turn_progress",
        }
        m = InboxMessage.model_validate(raw_in)
        assert m.message_kind == "turn_progress"
        # round-trip through model_dump(by_alias=True, exclude_none=True) — the
        # exact serialization the substrate uses in read_inbox(mark_as_read=True)
        # at claude_teams/messaging.py:53-68.
        raw_out = m.model_dump(by_alias=True, exclude_none=True)
        assert raw_out["messageKind"] == "turn_progress"
        assert "message_kind" not in raw_out  # alias-only on the wire

    def test_legacy_inbox_row_without_message_kind_loads_with_default(self):
        # CRITICAL §3 invariant: legacy v0.6.x rows without messageKind must
        # load cleanly with default="peer_dm". Otherwise R2's pydantic-default
        # protection regression returns.
        legacy = {"from": "lead", "text": "go", "timestamp": "2026-04-27T00:00:00Z"}
        m = InboxMessage.model_validate(legacy)
        assert m.message_kind == "peer_dm"
        # And on rewrite, the default appears on disk so future readers see it
        # rather than another silent strip.
        raw_out = m.model_dump(by_alias=True, exclude_none=True)
        assert raw_out["messageKind"] == "peer_dm"

    def test_substrate_mark_as_read_preserves_message_kind(self, tmp_path, monkeypatch):
        """End-to-end: write inbox file with messageKind, call substrate's
        read_inbox(mark_as_read=True), assert raw file still has messageKind
        on the now-marked-read entry. Catches the post-546b72c strip
        regression where InboxMessage didn't declare the field."""
        from claude_teams import messaging

        team = "kind-roundtrip"
        agent = "alice"
        monkeypatch.setattr(messaging, "TEAMS_DIR", tmp_path / "teams")
        inbox_file = messaging.ensure_inbox(team, agent)
        # Two entries: one turn-progress event, one peer DM. Both must survive
        # the actual substrate mark-as-read rewrite path.
        entries = [
            {
                "from": "lead",
                "text": '{"kind":"turn_progress","payload":{}}',
                "timestamp": "2026-04-27T00:00:00Z",
                "read": False,
                "messageKind": "turn_progress",
            },
            {
                "from": "alice-2",
                "text": "hello",
                "timestamp": "2026-04-27T00:00:01Z",
                "read": False,
                "messageKind": "peer_dm",
            },
        ]
        inbox_file.write_text(json.dumps(entries), encoding="utf-8")

        returned = messaging.read_inbox(team, agent, unread_only=True, mark_as_read=True)

        assert [m.message_kind for m in returned] == ["turn_progress", "peer_dm"]
        rewritten = json.loads(inbox_file.read_text(encoding="utf-8"))
        assert [r["messageKind"] for r in rewritten] == ["turn_progress", "peer_dm"]
        assert all(r["read"] is True for r in rewritten)


class TestLifecyclePayloadModels:
    def test_task_completed_and_blocked_models_round_trip(self):
        completed = TaskCompleted(
            task_id="7",
            files_changed=["src/foo.py"],
            summary="done",
            codex_exit_code=0,
        )
        blocked = TaskBlocked(task_id="8", reason="needs approval", timestamp="ts")

        assert completed.model_dump(by_alias=True)["kind"] == "task_complete"
        assert completed.model_dump(by_alias=True)["schema_version"] == 1
        assert blocked.model_dump(by_alias=True)["kind"] == "task_blocked"
        assert blocked.model_dump(by_alias=True)["schema_version"] == 1

    def test_plan_and_permission_models_round_trip(self):
        plan_blocked = PlanBlocked(
            request_id="p1",
            reason="no claimable task",
            timestamp="ts",
        )
        plan_response = PlanApprovalResponse(
            requestId="p2",
            approve=False,
            feedback="revise",
            timestamp="ts",
        )
        permission = PermissionRequest(
            request_id="perm-1",
            tool_name="Bash",
            tool_args={"cmd": "pytest"},
            task_id="7",
            teammate_name="worker",
            trust_mode="default",
            timestamp="ts",
        )

        assert plan_blocked.model_dump(by_alias=True)["kind"] == "plan_blocked"
        assert plan_response.model_dump(by_alias=True)["type"] == "plan_approval_response"
        assert plan_response.model_dump(by_alias=True)["requestId"] == "p2"
        assert permission.model_dump(by_alias=True)["type"] == "permission_request"
