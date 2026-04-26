"""M3 shutdown probe.

Sends a well-formed `shutdown_request` to a running codex-alice adapter using
the team protocol's `send_shutdown_request` helper. This mirrors what
team-lead's MCP server would do in production.

Run via:
  uv run python -m claude_anyteam.shutdown_probe <target_name>
"""

from __future__ import annotations

import sys

from claude_teams import messaging  # type: ignore[import-untyped]

from . import logger


def main() -> int:
    target = sys.argv[1] if len(sys.argv) > 1 else "codex-alice"
    team = "claude-anyteam"
    req_id = messaging.send_shutdown_request(team, target, reason="M3 shutdown probe")
    logger.info("shutdown_probe.sent", team=team, target=target, request_id=req_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
