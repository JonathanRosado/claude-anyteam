from __future__ import annotations

import json
from pathlib import Path

from claude_teams import messaging as cs_messaging
from claude_anyteam import protocol_io as pio
from claude_anyteam.messages import parse_protocol_text, PermissionResponseIn


def test_permission_response_parse_accepts_camel_request_id():
    parsed = parse_protocol_text('{"type":"permission_response","requestId":"p1","decision":"allow_once"}')
    assert isinstance(parsed, PermissionResponseIn)
    assert parsed.request_id == "p1"
    assert parsed.decision == "allow_once"


def test_wait_for_permission_response_marks_only_matching_lead_message(tmp_path: Path, monkeypatch):
    base = tmp_path / "teams"
    monkeypatch.setattr(cs_messaging, "TEAMS_DIR", base)
    cs_messaging.send_plain_message("t", "peer", "gemini-a", "hello", summary="peer")
    cs_messaging.send_plain_message("t", "team-lead", "gemini-a", json.dumps({"type": "permission_response", "request_id": "other", "decision": "allow_once"}), summary="other")
    cs_messaging.send_plain_message("t", "team-lead", "gemini-a", json.dumps({"type": "permission_response", "request_id": "perm-1", "decision": "deny", "reason": "no"}), summary="match")

    got = pio.wait_for_permission_response(team="t", teammate_name="gemini-a", request_id="perm-1", timeout_s=0.01, poll_interval_s=0.01)

    assert got is not None
    assert got.decision == "deny"
    raw = json.loads((base / "t" / "inboxes" / "gemini-a.json").read_text())
    assert [m["read"] for m in raw] == [False, False, True]
