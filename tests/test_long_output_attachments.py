from __future__ import annotations

import json
from pathlib import Path

from claude_anyteam import protocol_io as pio
from claude_teams import messaging
from claude_teams.models import TeammateMember


def _member(name: str, color: str) -> TeammateMember:
    return TeammateMember(
        agentId=f"{name}@claude-anyteam",
        name=name,
        agentType="claude-anyteam",
        model="test-model",
        prompt="irrelevant",
        color=color,
        joinedAt=0,
        tmuxPaneId="pane",
        cwd="/tmp",
    )


def _call_tool(name: str, arguments: dict) -> dict:
    import asyncio
    from claude_anyteam.wrapper_server import build_server

    mcp = build_server()
    result = asyncio.run(mcp.call_tool(name, arguments))
    return result.structured_content


def test_protocol_io_materializes_attached_control_messages(tmp_path, monkeypatch):
    teams_root = tmp_path / "teams"
    monkeypatch.setattr(messaging, "TEAMS_DIR", teams_root)
    monkeypatch.setattr(pio._m, "TEAMS_DIR", teams_root)
    monkeypatch.setenv("CLAUDE_ANYTEAM_INBOX_SPILL_CHARS", "48")
    full = json.dumps(
        {
            "type": "shutdown_request",
            "requestId": "shutdown-long@worker",
            "from": "team-lead",
            "reason": "x" * 160,
            "timestamp": "2026-04-28T00:00:00.000Z",
        }
    )

    messaging.send_plain_message(
        "team",
        "team-lead",
        "worker",
        full,
        summary="shutdown",
        message_kind="shutdown_request",
    )

    raw = json.loads((teams_root / "team" / "inboxes" / "worker.json").read_text())
    assert raw[0]["text"] != full
    assert raw[0]["attachment"]["charCount"] == len(full)

    msg = pio.read_inbox("team", "worker", mark_as_read=False)[0]
    assert msg.text == full
    assert msg.attachment is not None


def test_protocol_io_keeps_plain_peer_dm_as_preview(tmp_path, monkeypatch):
    teams_root = tmp_path / "teams"
    monkeypatch.setattr(messaging, "TEAMS_DIR", teams_root)
    monkeypatch.setattr(pio._m, "TEAMS_DIR", teams_root)
    monkeypatch.setenv("CLAUDE_ANYTEAM_INBOX_SPILL_CHARS", "24")
    full = "peer-body-" * 20

    messaging.send_plain_message(
        "team",
        "peer-a",
        "worker",
        full,
        summary="long peer dm",
        message_kind="informational",
    )

    msg = pio.read_inbox("team", "worker", mark_as_read=False)[0]
    assert msg.text != full
    assert msg.text.startswith(full[:24])
    assert msg.attachment is not None
    assert pio.resolve_attachment_text("team", msg) == full


def test_wrapper_send_message_auto_spills_body(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_ANYTEAM_TEAM", "claude-anyteam")
    monkeypatch.setenv("CLAUDE_ANYTEAM_NAME", "contract-test")
    monkeypatch.setenv("CLAUDE_ANYTEAM_DISABLE_PEER_STEER_MANIFEST_CHECK", "1")
    monkeypatch.setenv("CLAUDE_ANYTEAM_INBOX_SPILL_CHARS", "32")
    team_root = tmp_path / "teams"
    monkeypatch.setattr("claude_anyteam.wrapper_server._cs_messaging.TEAMS_DIR", team_root)
    config = type(
        "Config",
        (),
        {
            "members": [
                _member("contract-test", "magenta"),
                _member("peer-a", "blue"),
            ]
        },
    )()
    full = "wrapper-body-" * 12

    monkeypatch.setattr(
        "claude_anyteam.wrapper_server._cs_teams.read_config",
        lambda _team: config,
    )

    result = _call_tool(
        "send_message",
        {"to": "peer-a", "body": full, "summary": "long dm"},
    )

    assert result["delivered_to"] == "peer-a"
    assert result["sender"] == "contract-test"
    assert result["attachment"]["charCount"] == len(full)

    inbox = team_root / "claude-anyteam" / "inboxes" / "peer-a.json"
    row = json.loads(inbox.read_text(encoding="utf-8"))[0]
    assert row["text"].startswith(full[:32])
    assert full[32:] not in row["text"]
    assert row["attachment"]["path"] == result["attachment"]["path"]
    assert Path(row["attachment"]["path"]).read_text(encoding="utf-8") == full
