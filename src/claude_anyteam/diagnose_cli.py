"""``claude-anyteam diagnose`` — read adapter incident artifacts.

When a routed teammate's prose handler fails, ``diagnostics.record_incident``
writes a JSON file to ``~/.claude/teams/<team>/diagnostics/<agent>/inc-<id>.json``
and embeds the ``incident_id`` in the user-facing fallback message. This
CLI is the read side of that contract: the lead pastes the incident id
(or asks the user to) and gets the full structured context that was kept
out of chat for safety.

Two modes:

* ``claude-anyteam diagnose --incident inc-XXXXXXXX`` — find a specific
  artifact across all teams/agents, print it.
* ``claude-anyteam diagnose --team <T> [--agent <A>] [--limit N]`` — list
  recent incidents in a team (optionally filtered by agent).

Designed as a read-only inspection tool: zero side effects, safe to run
during a live session.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, TextIO


def _diagnostics_root() -> Path:
    return Path(os.path.expanduser("~")) / ".claude" / "teams"


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="claude-anyteam diagnose",
        description=(
            "Inspect adapter incident artifacts. Reads from "
            "~/.claude/teams/<team>/diagnostics/<agent>/inc-*.json — written "
            "by routed-teammate prose handlers when they can't produce a reply."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--incident",
        help=(
            "Print a single incident by id (e.g. inc-4f782549). Searches all "
            "teams and agents. Use this when a teammate's fallback reply "
            "names an incident you want details on."
        ),
    )
    p.add_argument(
        "--team",
        help="List incidents in this team only (recent first).",
    )
    p.add_argument(
        "--agent",
        help="When --team is set, list incidents for this teammate only.",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Max number of incidents to list (default: 10).",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of human-readable summary.",
    )
    return p


def _find_incident(incident_id: str) -> Path | None:
    root = _diagnostics_root()
    if not root.exists():
        return None
    # Recursive glob is bounded by the team-count × agent-count of the host —
    # incident ids are unique enough that we don't need a manifest.
    for path in root.glob(f"*/diagnostics/*/{incident_id}.json"):
        return path
    return None


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _list_incidents(*, team: str | None, agent: str | None, limit: int) -> list[Path]:
    root = _diagnostics_root()
    if team:
        diag = root / team / "diagnostics"
        if agent:
            diag = diag / agent
        if not diag.exists():
            return []
        paths = sorted(diag.rglob("inc-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    else:
        if not root.exists():
            return []
        paths = sorted(root.glob("*/diagnostics/*/inc-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return paths[:limit]


def _format_incident(record: dict[str, Any]) -> str:
    parts = [
        f"  incident_id : {record.get('incident_id', '?')}",
        f"  team        : {record.get('team', '?')}",
        f"  agent       : {record.get('agent', '?')}",
        f"  backend     : {record.get('backend', '?')}",
        f"  error_class : {record.get('error_class', '?')}",
        f"  summary     : {record.get('summary', '?')}",
    ]
    if "sender" in record:
        parts.append(f"  sender      : {record['sender']}")
    payload = record.get("payload")
    if isinstance(payload, dict):
        parts.append("  payload     :")
        for k, v in payload.items():
            parts.append(f"    {k}: {v}")
    return "\n".join(parts)


def main(argv: list[str], *, stdout: TextIO | None = None, stderr: TextIO | None = None) -> int:
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    args = _build_parser().parse_args(argv)

    if args.incident:
        path = _find_incident(args.incident)
        if path is None:
            err.write(f"error: no incident artifact found for {args.incident!r}\n")
            return 1
        record = _load(path)
        if args.json:
            out.write(json.dumps(record, indent=2, sort_keys=True) + "\n")
        else:
            out.write(f"incident artifact at {path}\n")
            out.write(_format_incident(record) + "\n")
        return 0

    if not args.team and not args.agent:
        # Default: list recent incidents across all teams.
        pass

    paths = _list_incidents(team=args.team, agent=args.agent, limit=args.limit)
    if not paths:
        scope = f"team {args.team!r}" if args.team else "any team"
        if args.agent:
            scope += f" / agent {args.agent!r}"
        out.write(f"no incidents found ({scope})\n")
        return 0

    if args.json:
        records = [_load(p) for p in paths]
        out.write(json.dumps(records, indent=2, sort_keys=True) + "\n")
        return 0

    for path in paths:
        record = _load(path)
        out.write(
            f"{record.get('incident_id', '?'):<14}  "
            f"team={record.get('team', '?')!s:<24}  "
            f"agent={record.get('agent', '?')!s:<24}  "
            f"backend={record.get('backend', '?'):<8}  "
            f"error={record.get('error_class', '?')}\n"
        )
    return 0
