"""M3 / §9 #7 plan-mode probe.

Sends a `plan_approval_request` to a running Codex adapter (which must be
launched with `--plan-mode` so `planModeRequired=True` is in effect). The
adapter should:

1. Parse the request via `PlanApprovalRequestIn`.
2. Resolve the target task (from `taskId` if provided; else its assigned
   or unassigned claimable pending task).
3. Run `codex exec --json --output-schema plan.schema.json` once.
4. Send a `plan_approval_request` reply to the lead carrying the
   structured plan Codex produced.

Run via:
  uv run python -m claude_anyteam.plan_probe <target_name> [task_id]

Example:
  uv run python -m claude_anyteam.plan_probe codex-alice 8
"""

from __future__ import annotations

import json
import sys
import time
import uuid

from claude_teams import messaging as cs_messaging  # type: ignore[import-untyped]

from . import logger


def main() -> int:
    target = sys.argv[1] if len(sys.argv) > 1 else "codex-alice"
    task_id = sys.argv[2] if len(sys.argv) > 2 else None
    team = "claude-anyteam"

    request_id = f"plan-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
    payload: dict[str, object] = {
        "type": "plan_approval_request",
        "requestId": request_id,
        "from": "team-lead",
        "timestamp": cs_messaging.now_iso(),
    }
    if task_id:
        payload["taskId"] = task_id

    cs_messaging.send_plain_message(
        team,
        from_name="team-lead",
        to_name=target,
        text=json.dumps(payload),
        summary=f"plan_approval_request:{request_id}",
    )
    logger.info(
        "plan_probe.sent",
        team=team,
        target=target,
        request_id=request_id,
        task_id=task_id,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
