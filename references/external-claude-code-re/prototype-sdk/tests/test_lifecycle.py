from __future__ import annotations

import asyncio
import json

from claude_teams.models import InboxMessage

from agent_teams_kit import FilesystemStorage, TaskResult, Teammate
from agent_teams_kit.messages import TaskAssignment


class LifeMate(Teammate):
    def agent_card(self):
        return {
            "schema_version": 1,
            "harness": "life",
            "harness_version": "1",
            "transport": "test",
            "capabilities": {},
        }

    async def execute_task(self, task):
        self.emit_tool_event("host_tool", "fake_tool", "completed", status="success")
        return TaskResult(summary="life done")

    async def reply_to_prose(self, peer, body):
        return None


def test_idle_and_shutdown_lifecycle(tmp_path):
    storage = FilesystemStorage(tmp_path)
    storage.create_team("t")
    mate = LifeMate(team="t", name="life-1", storage=storage)
    mate.register()

    assert mate.maybe_send_idle(force=True) is True
    storage.append_message(
        "t",
        "life-1",
        InboxMessage(
            from_="team-lead",
            text=json.dumps({"kind": "shutdown_request", "requestId": "s1", "from": "team-lead"}),
            timestamp="now",
        ),
    )
    asyncio.run(mate.poll_once())

    lead_msgs = storage.read_own_inbox("t", "team-lead", unread_only=False)
    summaries = [m.summary for m in lead_msgs]
    assert "idle" in summaries
    assert "shutdown_approved" in summaries


def test_task_assignment_claims_completes_and_logs_events(tmp_path):
    storage = FilesystemStorage(tmp_path)
    storage.create_team("t")
    task = storage.create_task("t", "do", "do life")
    mate = LifeMate(team="t", name="life-1", storage=storage)
    mate.register()
    payload = TaskAssignment(taskId=task.id, subject=task.subject, description=task.description)
    storage.append_message("t", "life-1", InboxMessage(from_="team-lead", text=payload.model_dump_json(by_alias=True), timestamp="now"))

    asyncio.run(mate.poll_once())

    completed = storage.get_task("t", task.id)
    assert completed.status == "completed"
    lead_msgs = storage.read_own_inbox("t", "team-lead", unread_only=False)
    assert any(m.summary == f"task_complete:{task.id}" for m in lead_msgs)
    events = storage.read_events("t", "life-1")
    assert [e.kind for e in events] == ["turn_started", "tool_event", "turn_completed"]
