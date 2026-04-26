"""``claude-anyteam status`` — one-screen view of a team's live operational state.

Combines what a lead actually wants to know in a daily-ops moment:

* Who's on the team (roster, with resolved adapter overrides)
* Are any of them ghosts (`-2`/`-3` re-spawn artifacts)
* Has anyone hit a recent incident (count + most-recent error_class)
* Most recent inbox activity per teammate (proxy for "alive")

This is a read-only snapshot — no side effects, safe to run during a
live session.

Designed broadly: the surface is the same regardless of whether the
team is doing coding, research, or any other work. Every field has a
matching diagnostic command (`claude-anyteam diagnose`,
`claude-anyteam team-roster`, `claude-anyteam team-config`) for drill-down.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

from . import team_cli


def _diagnostics_dir(team: str, agent: str) -> Path:
    return (
        Path(os.path.expanduser("~"))
        / ".claude"
        / "teams"
        / team
        / "diagnostics"
        / agent
    )


def _inbox_path(team: str, agent: str) -> Path:
    return Path(os.path.expanduser("~")) / ".claude" / "teams" / team / "inboxes" / f"{agent}.json"


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="claude-anyteam status",
        description=(
            "One-screen snapshot of a team's operational state. Combines "
            "roster, resolved adapter overrides, recent incident counts, "
            "and most-recent inbox activity per teammate."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--team", required=True, help="Team name")
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of the human-readable table",
    )
    return p


def _count_recent_incidents(team: str, agent: str, *, limit: int = 5) -> dict[str, Any]:
    diag = _diagnostics_dir(team, agent)
    if not diag.exists():
        return {"count": 0, "latest_class": None, "latest_id": None}
    paths = sorted(diag.glob("inc-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    count = len(paths)
    latest_class: str | None = None
    latest_id: str | None = None
    if paths:
        try:
            record = json.loads(paths[0].read_text(encoding="utf-8"))
            latest_class = record.get("error_class")
            latest_id = record.get("incident_id")
        except (OSError, json.JSONDecodeError):
            pass
    return {
        "count": count,
        "latest_class": latest_class,
        "latest_id": latest_id,
        "limit_window": limit,
    }


def _last_inbox_message_at(team: str, agent: str) -> str | None:
    """ISO timestamp of the most recent message *received* in this teammate's
    inbox.

    Caveat: this is an inbound-side signal — it tells you when someone last
    *wrote to* this teammate, not when this teammate last *did* anything.
    A live teammate that hasn't been messaged in a while will look "stale"
    by this signal even though they're healthy. Treat it as a freshness
    proxy, not a liveness oracle. (A true liveness signal would need
    outbound markers — e.g. mtime of the agent's last protocol_io.send_*.)

    Returns None if no inbox file exists or is empty/unparseable.
    """
    path = _inbox_path(team, agent)
    if not path.exists():
        return None
    try:
        records = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(records, list) or not records:
        return None
    latest_ts: str | None = None
    for r in records:
        if isinstance(r, dict):
            ts = r.get("timestamp")
            if isinstance(ts, str) and (latest_ts is None or ts > latest_ts):
                latest_ts = ts
    return latest_ts


def _humanize_age(iso_ts: str | None) -> str:
    if iso_ts is None:
        return "—"
    try:
        # Tolerate both Z-suffix and +00:00 forms.
        s = iso_ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
    except ValueError:
        return iso_ts
    delta = datetime.now(timezone.utc) - dt
    seconds = int(delta.total_seconds())
    if seconds < 0:
        return iso_ts
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def main(argv: list[str], *, stdout: TextIO | None = None, stderr: TextIO | None = None) -> int:
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    args = _build_parser().parse_args(argv)

    cfg_path = team_cli.team_config_path(args.team)
    if not cfg_path.exists():
        err.write(f"error: no team config at {cfg_path}\n")
        return 1
    cfg = team_cli._existing_dict(cfg_path)
    rows = team_cli._roster_rows(cfg, team=args.team, resolve=True)

    families: dict[str, int] = {}
    for r in rows:
        base, n = team_cli._split_respawn_suffix(r.name)
        families[base] = max(families.get(base, n), n)

    enriched: list[dict[str, Any]] = []
    for r in rows:
        base, n = team_cli._split_respawn_suffix(r.name)
        ghost = families.get(base, n) > n
        incidents = _count_recent_incidents(args.team, r.name)
        last_inbox_msg = _last_inbox_message_at(args.team, r.name)
        enriched.append(
            {
                "name": r.name,
                "agent_type": r.agent_type,
                "host_model": r.model,
                "backend_type": r.backend_type,
                "adapter_model": r.adapter_model,
                "adapter_effort": r.adapter_effort,
                "adapter_turn_timeout_s": r.adapter_turn_timeout_s,
                "is_ghost": ghost,
                "incidents_total": incidents["count"],
                "latest_incident_class": incidents["latest_class"],
                "latest_incident_id": incidents["latest_id"],
                "last_inbox_message_at": last_inbox_msg,
            }
        )

    if args.json:
        out.write(json.dumps({"team": args.team, "members": enriched}, indent=2, sort_keys=True) + "\n")
        return 0

    if not enriched:
        out.write(f"team {args.team!r} has no members\n")
        return 0

    name_w = max(len(e["name"]) for e in enriched)
    out.write(f"team {args.team}\n")
    for e in enriched:
        marker = "⚠ " if e["is_ghost"] else "  "
        adapter = ""
        if e["adapter_model"] or e["adapter_effort"]:
            parts = []
            if e["adapter_model"]:
                parts.append(f"model={e['adapter_model']}")
            if e["adapter_effort"]:
                parts.append(f"effort={e['adapter_effort']}")
            adapter = f"  adapter[{' '.join(parts)}]"
        incidents = ""
        if e["incidents_total"] > 0:
            incidents = f"  incidents={e['incidents_total']} latest={e['latest_incident_class']}/{e['latest_incident_id']}"
        last_msg = _humanize_age(e["last_inbox_message_at"])
        out.write(
            f"{marker}{e['name']:<{name_w}}  type={e['agent_type']:<16}  "
            f"host={e['host_model']:<24}  last_inbox_msg={last_msg}"
            f"{adapter}{incidents}\n"
        )
    return 0
