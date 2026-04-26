"""M1 live round-trip probe.

Registers a throwaway teammate `m1-roundtrip`, sends a probe message to the
lead, polls its own inbox for a reply, and deregisters. Intended to be run
interactively with team-lead's help: you run this, it pauses for the lead
to send a reply, you hit enter, it verifies.

Not wired into the CLI — this is a one-shot M1 completion probe.

Run via:  uv run python -m claude_anyteam.roundtrip_m1
"""

from __future__ import annotations

import os

from .env import LEGACY_NAME_ENV, LEGACY_PROBE_TAG_ENV, LEGACY_TEAM_ENV, NAME_ENV, PROBE_TAG_ENV, TEAM_ENV, env_first
import sys
import time
from pathlib import Path

from . import logger, protocol_io
from .config import Settings
from .messages import now_iso
from .registration import deregister, inbox_path, register

TEAM = env_first(os.environ, TEAM_ENV, LEGACY_TEAM_ENV, default="claude-anyteam") or "claude-anyteam"
NAME = env_first(os.environ, NAME_ENV, LEGACY_NAME_ENV, default="m1-roundtrip") or "m1-roundtrip"
PROBE_TAG = env_first(os.environ, PROBE_TAG_ENV, LEGACY_PROBE_TAG_ENV, default=f"m1-probe-{int(time.time())}") or f"m1-probe-{int(time.time())}"


def _settings() -> Settings:
    return Settings(
        team_name=TEAM,
        agent_name=NAME,
        cwd=Path.cwd().resolve(),
        poll_interval_s=1.0,
        color="cyan",
        plan_mode_required=False,
        codex_binary="codex",
    )


def main() -> int:
    settings = _settings()

    logger.info("m1.roundtrip.start", team=TEAM, name=NAME, tag=PROBE_TAG)

    register(settings)

    # 1. Read the team config through the protocol implementation in-process —
    #    confirms the adapter sees its own entry reflected.
    cfg = protocol_io.read_config(TEAM)
    own = next((m for m in cfg.members if m.name == NAME), None)
    if own is None:
        logger.error("m1.roundtrip.self_missing")
        return 1
    logger.info("m1.roundtrip.self_visible", agent_id=own.agent_id)

    # 2. Send a probe message to team-lead via the protocol send helper.
    protocol_io.send_prose_to_lead(
        TEAM,
        NAME,
        text=(
            f"[{PROBE_TAG}] claude-anyteam M1 round-trip probe. "
            f"If you see this, reply to `{NAME}` with the tag in the body and I'll verify."
        ),
        summary=f"m1-probe:{PROBE_TAG}",
    )
    logger.info("m1.roundtrip.probe_sent", tag=PROBE_TAG)

    # 3. Poll our own inbox for the echo. Wait up to 120s.
    deadline = time.monotonic() + 120.0
    echoed = False
    while time.monotonic() < deadline:
        msgs = protocol_io.read_inbox(TEAM, NAME, mark_as_read=True)
        for m in msgs:
            if PROBE_TAG in m.text:
                logger.info(
                    "m1.roundtrip.echo_received",
                    from_=m.from_,
                    timestamp=m.timestamp,
                )
                echoed = True
                break
            else:
                logger.debug("m1.roundtrip.other_inbound", from_=m.from_, text_head=m.text[:80])
        if echoed:
            break
        time.sleep(1.0)

    if not echoed:
        logger.warn("m1.roundtrip.echo_timeout", tag=PROBE_TAG)
        # Keep fixture for follow-up inspection.
        return 2

    # 4. Send a clean goodbye and deregister.
    protocol_io.send_prose_to_lead(
        TEAM,
        NAME,
        text=f"[{PROBE_TAG}] round-trip confirmed; adapter is deregistering.",
        summary=f"m1-probe-done:{PROBE_TAG}",
    )
    deregister(settings)
    ibx = inbox_path(TEAM, NAME)
    if ibx.exists():
        ibx.unlink()

    logger.info("m1.roundtrip.done", tag=PROBE_TAG)
    return 0


if __name__ == "__main__":
    sys.exit(main())
