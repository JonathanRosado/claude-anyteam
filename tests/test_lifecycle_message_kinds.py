from __future__ import annotations

import json
from pathlib import Path

from claude_anyteam import protocol_io as pio


def _lead_rows(root: Path) -> list[dict]:
    inbox = root / "teams" / "t" / "inboxes" / "team-lead.json"
    return json.loads(inbox.read_text(encoding="utf-8"))


def test_protocol_io_stamps_lifecycle_message_kinds(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(pio._m, "TEAMS_DIR", tmp_path / "teams")

    pio.send_idle_notification("t", "worker")
    pio.send_task_complete(
        "t",
        "worker",
        task_id="7",
        files_changed=["src/foo.py"],
        summary_text="done",
        codex_exit_code=0,
    )
    pio.send_task_blocked("t", "worker", task_id="8", reason="needs approval")
    pio.send_plan_blocked(
        "t",
        "worker",
        request_id="p1",
        reason="no claimable task",
    )
    pio.send_plan_approval_request(
        "t",
        "worker",
        request_id="p2",
        plan={"steps": [{"summary": "inspect"}]},
    )
    pio.send_permission_request_to_lead(
        "t",
        "worker",
        request_id="perm-1",
        tool_name="Bash",
        tool_args={"cmd": "pytest"},
        task_id="7",
        trust_mode="default",
    )

    rows = _lead_rows(tmp_path)
    assert [row["messageKind"] for row in rows] == [
        "idle_notification",
        "task_complete",
        "task_blocked",
        "plan_blocked",
        "plan_approval_request",
        "permission_request",
    ]
    assert [json.loads(row["text"]).get("kind") or json.loads(row["text"]).get("type") for row in rows] == [
        "idle_notification",
        "task_complete",
        "task_blocked",
        "plan_blocked",
        "plan_approval_request",
        "permission_request",
    ]


def test_protocol_io_derives_message_kind_for_legacy_json_helper(
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.setattr(pio._m, "TEAMS_DIR", tmp_path / "teams")

    pio.send_prose_to_lead(
        "t",
        "worker",
        json.dumps(
            {
                "kind": "plan_blocked",
                "request_id": "p1",
                "reason": "no claimable task",
            }
        ),
        summary="plan_blocked:p1",
    )

    [row] = _lead_rows(tmp_path)
    assert row["messageKind"] == "plan_blocked"
    assert json.loads(row["text"])["kind"] == "plan_blocked"
