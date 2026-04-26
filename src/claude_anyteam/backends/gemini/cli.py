"""Console entry point for Gemini-backed teammates."""
from __future__ import annotations

import argparse
import sys

from .config import GEMINI_EFFORTS, GEMINI_TRUST_MODES, from_env
from .loop import run


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gemini-anyteam", description="Route gemini-* teammates through Gemini CLI.")
    p.add_argument("--team", help="Team name (overrides CLAUDE_ANYTEAM_TEAM)")
    p.add_argument("--name", help="Teammate name within the team (overrides CLAUDE_ANYTEAM_NAME)")
    p.add_argument("--cwd", help="Working directory for Gemini invocations")
    p.add_argument("--poll-s", type=float, help="Inbox poll interval in seconds")
    p.add_argument("--color", help="Display color (default: cyan)")
    p.add_argument("--plan-mode", action="store_true", help="Register with planModeRequired=true")
    p.add_argument("--gemini-binary", help="Gemini CLI binary name (default: gemini)")
    p.add_argument("--model", help="Gemini model slug passed as --model. Overrides CLAUDE_ANYTEAM_MODEL.")
    p.add_argument("--effort", choices=sorted(GEMINI_EFFORTS), help="Gemini thinking effort tier. Overrides CLAUDE_ANYTEAM_GEMINI_EFFORT.")
    p.add_argument("--gemini-home", help="Adapter-owned HOME root for Gemini config/session state")
    p.add_argument(
        "--backend",
        choices=("headless", "acp"),
        help=(
            "Gemini backend transport (default: acp). ACP enables mid-turn "
            "steering and persistent sessions. Pass `--backend headless` if "
            "your Gemini CLI is too old to support `--acp`/`--experimental-acp`."
        ),
    )
    p.add_argument("--trust", choices=sorted(GEMINI_TRUST_MODES), help="ACP trust policy for permission requests (default: trusted)")
    return p


def main(argv: list[str] | None = None) -> int:
    ns = _build_parser().parse_args(argv)
    settings = from_env({
        "team_name": ns.team,
        "agent_name": ns.name,
        "cwd": ns.cwd,
        "poll_interval_s": ns.poll_s,
        "color": ns.color,
        "plan_mode_required": True if ns.plan_mode else None,
        "gemini_binary": ns.gemini_binary,
        "model": ns.model,
        "effort": ns.effort,
        "gemini_home": ns.gemini_home,
        "backend": ns.backend,
        "trust_mode": ns.trust,
    })
    return run(settings)


if __name__ == "__main__":
    raise SystemExit(main())
