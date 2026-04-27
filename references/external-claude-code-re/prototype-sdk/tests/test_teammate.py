from __future__ import annotations

import asyncio

from claude_teams.models import InboxMessage

from agent_teams_kit import FilesystemStorage, TaskResult, Teammate


class DemoMate(Teammate):
    def agent_card(self):
        return {
            "schema_version": 1,
            "harness": "demo",
            "harness_version": "1",
            "transport": "test",
            "capabilities": {
                "demo_cap": {
                    "version": "1",
                    "schema": {"type": "object"},
                    "description": "demo capability",
                    "when_to_use": "tests",
                    "when_not_to": "production",
                    "failure_modes": ["DEMO_FAIL"],
                }
            },
        }

    async def execute_task(self, task):
        return TaskResult(summary=f"done {task.id}", files_changed=["x.txt"])

    async def reply_to_prose(self, peer, body):
        return f"reply to {peer}: {body}"


def make_storage(tmp_path):
    storage = FilesystemStorage(tmp_path)
    storage.create_team("t")
    return storage


def test_register_deregister_round_trip(tmp_path):
    storage = make_storage(tmp_path)
    mate = DemoMate(team="t", name="demo-1", storage=storage)

    row1 = mate.register()
    row2 = mate.register()
    cfg = storage.read_config("t")

    assert row1["name"] == "demo-1"
    assert row2["capabilities"] == ["demo_cap"]
    assert [m["name"] for m in cfg["members"]].count("demo-1") == 1
    assert cfg["members"][1]["agentCard"]["capabilities"]["demo_cap"]["when_to_use"] == "tests"
    assert mate.deregister() is True
    assert "demo-1" not in [m["name"] for m in storage.read_config("t")["members"]]


def test_prose_reply_uses_typed_summary(tmp_path):
    storage = make_storage(tmp_path)
    mate = DemoMate(team="t", name="demo-1", storage=storage)
    mate.register()
    storage.append_message("t", "demo-1", InboxMessage(from_="peer", text="hello", timestamp="now"))

    asyncio.run(mate.poll_once())

    lead_or_peer = storage.read_own_inbox("t", "peer", unread_only=False)
    assert lead_or_peer[0].summary == "prose_reply"
    assert "reply to peer" in lead_or_peer[0].text
