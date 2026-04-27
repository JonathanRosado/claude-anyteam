from __future__ import annotations

import pytest
from claude_teams.models import InboxMessage

from agent_teams_kit import FilesystemStorage
from agent_teams_kit.storage import ClaimConflict, ConfigVersionConflict


def test_inbox_append_read_ack_preserves_message_ids(tmp_path):
    storage = FilesystemStorage(tmp_path)
    storage.create_team("t")
    msg_id = storage.append_message("t", "agent", InboxMessage(from_="peer", text="hi", timestamp="now"))

    raw_before = storage.inbox_path("t", "agent").read_text()
    assert msg_id in raw_before
    messages = storage.read_own_inbox("t", "agent", unread_only=True)
    assert messages[0].text == "hi"
    assert msg_id in storage.inbox_path("t", "agent").read_text()

    storage.ack_messages("t", "agent", [msg_id])
    assert storage.read_own_inbox("t", "agent", unread_only=True) == []


def test_atomic_task_claim_conflict(tmp_path):
    storage = FilesystemStorage(tmp_path)
    storage.create_team("t")
    task = storage.create_task("t", "subject", "description")

    claimed = storage.claim_task("t", task.id, "agent-1", "working")

    assert claimed.owner == "agent-1"
    assert claimed.status == "in_progress"
    with pytest.raises(ClaimConflict):
        storage.claim_task("t", task.id, "agent-2", "stealing")


def test_config_cas_version_conflict(tmp_path):
    storage = FilesystemStorage(tmp_path)
    storage.create_team("t")
    cfg = storage.read_config("t")
    version = cfg.get("version", 0)
    storage.update_config("t", lambda c: c, expected_version=version)

    with pytest.raises(ConfigVersionConflict):
        storage.update_config("t", lambda c: c, expected_version=version)
