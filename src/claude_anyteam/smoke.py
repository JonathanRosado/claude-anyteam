"""M1 smoke test.

Proves cs50victor's protocol I/O library works in-process, by reading the
live `claude-anyteam` team config + task list and printing them.

Run via:  uv run python -m claude_anyteam.smoke
"""

from __future__ import annotations

import os

from .env import LEGACY_TEAM_ENV, TEAM_ENV, env_first
import sys

from claude_teams import messaging, tasks, teams  # type: ignore[import-untyped]

from . import logger


def main() -> int:
    team = env_first(os.environ, TEAM_ENV, LEGACY_TEAM_ENV, default="claude-anyteam") or "claude-anyteam"
    try:
        cfg = teams.read_config(team)
    except Exception as e:
        logger.error("smoke.fail", stage="read_config", error=str(e))
        return 1

    members = [m.name for m in cfg.members]
    logger.info("config.read", team=cfg.name, members=members)

    try:
        all_tasks = tasks.list_tasks(team)
    except Exception as e:
        logger.error("smoke.fail", stage="list_tasks", error=str(e))
        return 1

    logger.info(
        "tasks.listed",
        count=len(all_tasks),
        ids=[f"{t.id}:{t.status}" for t in all_tasks],
    )

    # Read the team-lead inbox without marking anything read. This is a
    # non-mutating probe to confirm we can parse the on-disk format.
    try:
        lead_inbox = messaging.read_inbox(team, "team-lead", unread_only=False, mark_as_read=False)
    except Exception as e:
        logger.error("smoke.fail", stage="read_inbox", error=str(e))
        return 1

    logger.info(
        "inbox.read",
        agent="team-lead",
        total=len(lead_inbox),
        unread=sum(1 for m in lead_inbox if not m.read),
    )

    logger.info("smoke.pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
