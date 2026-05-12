"""``claude-anyteam diagnose`` — read-only substrate inspector.

The default mode inspects a live team's on-disk substrate: roster, rich
capability-manifest cache, visibility-degraded events, #51 SendMessage flap
repair evidence, wrapper MCP tool-discovery diagnostics, and a compact health
checklist. It is read-only unless ``--instrument-spawn`` is passed.

The legacy incident-artifact lookup remains available via ``--incident``.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version as _pkg_version
from pathlib import Path
from typing import Any, TextIO

from . import team_cli
from .capabilities import CAPABILITY_HOOKS, CAPABILITY_MANIFEST_VERSION
from .codex_log_bloat import format_bytes, inspect_codex_log_bloat
from .env import LEGACY_TEAM_ENV, TEAM_ENV

INSTRUMENT_ENV_KEY = "CLAUDE_ANYTEAM_WRAPPER_MCP_DIAGNOSTICS"
INSTRUMENT_ENV_VALUE = "1"

FLAP_REPAIR_NEEDLES = (
    "send_message_repair",
    "send_message_unavailable",
    "repaired_via_send_message_tool",
    "suppressed_send_message_claim",
    "mcp_send_message_unavailable",
    "send_message not available hallucination",
)

SNAPSHOT_EVENTS = {
    "server_registered_snapshot",
    "list_tools",
    "list_tools_failed",
    "codex_exec_session_start",
    "codex_app_server_mcp_config_prepared",
    "codex_app_server_session_start",
}

ROUTED_PREFIXES = ("codex-", "gemini-", "kimi-")
ROUTED_AGENT_TYPES = {"claude-anyteam", "codex", "gemini", "kimi"}
ROUTED_BACKEND_HINTS = (
    "codex",
    "gemini",
    "kimi",
    "in-process",
    "headless",
    "acp",
)


@dataclass(frozen=True)
class _ManifestRecord:
    agent: str
    capability_version: str | None
    mtime: str | None
    path: str | None
    status: str
    stale_reason: str | None
    capabilities: list[str]


# --------------------------------------------------------------------------- #
# Legacy incident artifact mode
# --------------------------------------------------------------------------- #


def _diagnostics_root() -> Path:
    return Path.home() / ".claude" / "teams"


def _find_incident(incident_id: str) -> Path | None:
    root = _diagnostics_root()
    if not root.exists():
        return None
    # Recursive glob is bounded by team-count × agent-count on the host.
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


def _incident_command(args: argparse.Namespace, *, stdout: TextIO, stderr: TextIO) -> int:
    if args.incident:
        path = _find_incident(args.incident)
        if path is None:
            stderr.write(f"error: no incident artifact found for {args.incident!r}\n")
            return 1
        record = _load(path)
        if args.json:
            stdout.write(json.dumps(record, indent=2, sort_keys=True) + "\n")
        else:
            stdout.write(f"incident artifact at {path}\n")
            stdout.write(_format_incident(record) + "\n")
        return 0

    paths = _list_incidents(team=args.team, agent=args.agent, limit=args.limit)
    if not paths:
        scope = f"team {args.team!r}" if args.team else "any team"
        if args.agent:
            scope += f" / agent {args.agent!r}"
        stdout.write(f"no incidents found ({scope})\n")
        return 0

    if args.json:
        stdout.write(json.dumps([_load(p) for p in paths], indent=2, sort_keys=True) + "\n")
        return 0

    for path in paths:
        record = _load(path)
        stdout.write(
            f"{record.get('incident_id', '?'):<14}  "
            f"team={record.get('team', '?')!s:<24}  "
            f"agent={record.get('agent', '?')!s:<24}  "
            f"backend={record.get('backend', '?'):<8}  "
            f"error={record.get('error_class', '?')}\n"
        )
    return 0


# --------------------------------------------------------------------------- #
# Substrate report helpers
# --------------------------------------------------------------------------- #


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="claude-anyteam diagnose",
        description=(
            "Inspect claude-anyteam substrate state: roster, manifest cache, "
            "visibility_degraded events, SendMessage flap-repair evidence, "
            "wrapper MCP diagnostics, and health checklist. Read-only unless "
            "--instrument-spawn is passed."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--incident",
        help=(
            "Legacy incident mode: print a specific adapter incident id "
            "(e.g. inc-4f782549) instead of the substrate report."
        ),
    )
    p.add_argument(
        "--incidents",
        action="store_true",
        help="Legacy incident mode: list recent incident artifacts instead of substrate state.",
    )
    p.add_argument("--team", type=team_cli._validate_team_name, help="Team name; defaults to CLAUDE_ANYTEAM_TEAM/CODEX_TEAMMATE_TEAM.")
    p.add_argument("--agent", type=team_cli._validate_agent_name, help="Scope the report to one teammate.")
    p.add_argument("--since", help="ISO timestamp lower bound for events/diagnostic logs (e.g. 2026-04-28T15:00:00Z).")
    p.add_argument("--limit", type=int, default=10, help="Recent rows per section (default: 10).")
    p.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable report.")
    p.add_argument(
        "--codex-log-bloat",
        action="store_true",
        help=(
            "Check Codex CLI logs_*.sqlite-wal size and print the #43 "
            "operator diagnostic. Does not require --team."
        ),
    )
    p.add_argument(
        "--codex-sqlite-home",
        help=argparse.SUPPRESS,
    )
    p.add_argument(
        "--instrument-spawn",
        action="store_true",
        help=(
            "MUTATING: write env.CLAUDE_ANYTEAM_WRAPPER_MCP_DIAGNOSTICS=1 "
            "to ~/.claude/settings.json so the next teammate spawn captures "
            "wrapper MCP tool-discovery diagnostics."
        ),
    )
    p.add_argument(
        "--bundle",
        action="store_true",
        help=(
            "Wrap the report in a markdown-formatted bundle suitable for "
            "GitHub issue submission. Includes versions (claude-anyteam, "
            "codex/gemini/kimi CLIs, Python, OS) and sanitizes the user's "
            "home directory in paths to '~'. Review and scrub team/agent "
            "names manually if sensitive before posting."
        ),
    )
    p.add_argument("--settings-path", help=argparse.SUPPRESS)
    return p


def _current_team() -> str | None:
    return os.environ.get(TEAM_ENV) or os.environ.get(LEGACY_TEAM_ENV) or None


def _team_dir(team: str) -> Path:
    return Path.home() / ".claude" / "teams" / team


def _settings_path(arg_value: str | None) -> Path:
    return Path(arg_value).expanduser() if arg_value else Path.home() / ".claude" / "settings.json"


def _read_json_file(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("JSON root is not an object")
    return raw


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    except OSError:
        return []
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _parse_iso(value: str | None) -> datetime | None:
    if value in (None, ""):
        return None
    raw = str(value).strip()
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid --since timestamp {value!r}: {exc}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _dt_for_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _after_since(row: dict[str, Any], since: datetime | None) -> bool:
    if since is None:
        return True
    dt = _dt_for_ts(row.get("timestamp"))
    return dt is not None and dt >= since


def _sort_key(row: dict[str, Any]) -> tuple[str, str]:
    return (str(row.get("timestamp") or ""), str(row.get("event_id") or row.get("event") or ""))


def _mtime_iso(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _manifest_agent(path: Path, manifest: dict[str, Any] | None) -> str:
    if isinstance(manifest, dict):
        for key in ("agent_name", "agentName", "name"):
            value = manifest.get(key)
            if isinstance(value, str) and value:
                return value
    return path.stem


def _manifest_version(manifest: dict[str, Any] | None) -> str | None:
    if not isinstance(manifest, dict):
        return None
    value = manifest.get("capability_version", manifest.get("capabilityVersion"))
    return str(value) if value is not None else None


def _manifest_capabilities(manifest: dict[str, Any] | None) -> list[str]:
    if not isinstance(manifest, dict):
        return []
    caps = manifest.get("capabilities")
    if isinstance(caps, dict):
        return sorted(str(name) for name in caps)
    if isinstance(caps, list):
        return sorted(str(name) for name in caps)
    return []


def _read_manifest_records(team_root: Path, *, scoped_agents: set[str], agent: str | None) -> tuple[list[_ManifestRecord], dict[str, dict[str, Any]]]:
    records: list[_ManifestRecord] = []
    manifests: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    manifest_dir = team_root / "manifests"
    if manifest_dir.exists():
        for path in sorted(manifest_dir.glob("*.json")):
            manifest: dict[str, Any] | None = None
            status = "current"
            stale_reason: str | None = None
            try:
                manifest = _read_json_file(path)
                agent_name = _manifest_agent(path, manifest)
                version = _manifest_version(manifest)
                capabilities = _manifest_capabilities(manifest)
                if agent and agent_name != agent:
                    continue
                if scoped_agents and agent_name not in scoped_agents:
                    status = "orphaned"
                    stale_reason = "not in scoped roster"
                elif version != CAPABILITY_MANIFEST_VERSION:
                    status = "stale_version"
                    stale_reason = f"expected {CAPABILITY_MANIFEST_VERSION}"
                manifests[agent_name] = manifest
                seen.add(agent_name)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                agent_name = path.stem
                if agent and agent_name != agent:
                    continue
                version = None
                capabilities = []
                status = "invalid"
                stale_reason = f"{type(exc).__name__}: {exc}"
            records.append(
                _ManifestRecord(
                    agent=agent_name,
                    capability_version=version,
                    mtime=_mtime_iso(path),
                    path=str(path),
                    status=status,
                    stale_reason=stale_reason,
                    capabilities=capabilities,
                )
            )

    for missing in sorted(scoped_agents - seen):
        if agent and missing != agent:
            continue
        records.append(
            _ManifestRecord(
                agent=missing,
                capability_version=None,
                mtime=None,
                path=None,
                status="missing",
                stale_reason="no manifest on disk",
                capabilities=[],
            )
        )
    records.sort(key=lambda r: r.agent)
    return records, manifests


def _latest_wrapper_pid_by_agent(team_root: Path) -> dict[str, int]:
    pids: dict[str, int] = {}
    for row in _load_jsonl(team_root / "diagnostics" / "wrapper-mcp-tools.jsonl"):
        agent = row.get("agent")
        pid = row.get("pid")
        if isinstance(agent, str) and isinstance(pid, int):
            pids[agent] = pid
    return pids


def _roster(team: str, *, agent: str | None, manifest_records: list[_ManifestRecord], team_root: Path) -> tuple[list[dict[str, Any]], str | None]:
    cfg_path = team_cli.team_config_path(team)
    if not cfg_path.exists():
        return [], f"no team config at {cfg_path}"
    cfg = team_cli._existing_dict(cfg_path)
    members = cfg.get("members")
    if not isinstance(members, list):
        return [], f"{cfg_path} has no 'members' list"

    version_by_agent = {r.agent: r.capability_version for r in manifest_records if r.status != "missing"}
    pids = _latest_wrapper_pid_by_agent(team_root)
    live_panes = team_cli._live_tmux_pane_ids()
    rows: list[dict[str, Any]] = []
    for member in members:
        if not isinstance(member, dict):
            continue
        name = str(member.get("name", "?"))
        if agent and name != agent:
            continue
        tmux_pane_id = str(member.get("tmuxPaneId", ""))
        row = {
            "name": name,
            "agent_type": str(member.get("agentType", "?")),
            "model": str(member.get("model", "?")),
            "backend_type": str(member.get("backendType", "?")),
            "color": str(member.get("color", "?")),
            "is_active": (bool(member["isActive"]) if "isActive" in member else None),
            "tmux_pane_id": tmux_pane_id,
            "is_dead_pane": tmux_pane_id not in ("", "in-process", "pending") and live_panes is not None and tmux_pane_id not in live_panes,
            "cwd": member.get("cwd"),
            "capabilities": [str(c) for c in member.get("capabilities", [])] if isinstance(member.get("capabilities"), list) else [],
            "capability_version": version_by_agent.get(name),
            "adapter_pid": pids.get(name),
            "adapter_pid_source": "wrapper-mcp-tools.jsonl" if name in pids else None,
        }
        rows.append(row)
    return rows, None


def _is_routed_member(row: dict[str, Any]) -> bool:
    name = str(row.get("name") or "")
    agent_type = str(row.get("agent_type") or "")
    backend = str(row.get("backend_type") or "")
    if name.startswith(ROUTED_PREFIXES):
        return True
    if agent_type in ROUTED_AGENT_TYPES:
        return True
    return any(hint in backend for hint in ROUTED_BACKEND_HINTS)


def _event_files(team_root: Path, *, agent: str | None) -> list[Path]:
    events_dir = team_root / "events"
    if not events_dir.exists():
        return []
    paths = [p for p in events_dir.glob("*.jsonl") if p.is_file()]
    if agent:
        paths = [p for p in paths if p.stem == agent]
    return sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True)


def _recent_events(team_root: Path, *, agent: str | None, since: datetime | None) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for path in _event_files(team_root, agent=agent):
        events.extend(row for row in _load_jsonl(path) if _after_since(row, since))
    events.sort(key=_sort_key)
    return events


def _category_for_visibility(row: dict[str, Any]) -> str:
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    for key in ("surface", "category", "reason", "phase"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return "uncategorized"


def _visibility_degraded_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    recent = [e for e in events if e.get("kind") == "visibility_degraded"][-50:]
    by_category: dict[str, dict[str, Any]] = {}
    for row in recent:
        cat = _category_for_visibility(row)
        current = by_category.setdefault(cat, {"count": 0, "latest": None, "agents": []})
        current["count"] += 1
        current["latest"] = row.get("timestamp")
        agent = row.get("agent")
        if isinstance(agent, str) and agent not in current["agents"]:
            current["agents"].append(agent)
    return {
        "total": len(recent),
        "categories": dict(sorted(by_category.items(), key=lambda item: item[0])),
        "recent": recent,
    }


def _row_mentions_flap_repair(row: dict[str, Any]) -> bool:
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    # Deliberately do not scan raw host-tool previews/targets: a developer
    # reading or editing this source file would otherwise look like #51 repair
    # activity. Repair-path emissions should identify themselves in the event
    # summary/id or in compact diagnostic payload fields.
    fragments = [
        row.get("summary"),
        row.get("event_id"),
        payload.get("surface"),
        payload.get("reason"),
        payload.get("error_class"),
        payload.get("event"),
        payload.get("logger"),
        payload.get("diagnostic"),
    ]
    text = " ".join(str(fragment) for fragment in fragments if fragment).lower()
    return any(needle in text for needle in FLAP_REPAIR_NEEDLES)


def _flap_repair_events(events: list[dict[str, Any]], *, limit: int) -> dict[str, Any]:
    matches = [e for e in events if _row_mentions_flap_repair(e)]
    return {"total": len(matches), "recent": matches[-limit:]}


def _wrapper_diagnostics(team_root: Path, *, agent: str | None, since: datetime | None, limit: int) -> dict[str, Any]:
    path = team_root / "diagnostics" / "wrapper-mcp-tools.jsonl"
    rows = []
    for row in _load_jsonl(path):
        if agent and row.get("agent") != agent:
            continue
        if not _after_since(row, since):
            continue
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        event = row.get("event")
        if event in SNAPSHOT_EVENTS or any(
            key in payload for key in ("send_message_registered", "missing_expected_tools", "tool_count", "registered_snapshot")
        ):
            rows.append(row)
    rows.sort(key=_sort_key)
    return {
        "path": str(path),
        "exists": path.exists(),
        "total": len(rows),
        "recent": rows[-limit:],
    }


def _pid_alive(pid: Any) -> bool | None:
    if not isinstance(pid, int) or pid <= 0:
        return None
    if os.name != "posix":
        return None
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None


def _sandbox_marker_candidates(cwd: Any) -> list[Path]:
    if not isinstance(cwd, str) or not cwd:
        return []
    p = Path(cwd)
    return [p / ".stress_sandbox_marker", p.parent / ".stress_sandbox_marker"]


def _sandbox_markers(roster: list[dict[str, Any]]) -> list[dict[str, Any]]:
    markers: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for row in roster:
        for marker in _sandbox_marker_candidates(row.get("cwd")):
            if marker in seen:
                continue
            seen.add(marker)
            if not marker.exists():
                continue
            record: dict[str, Any] = {"path": str(marker), "agents": [row.get("name")]}
            try:
                payload = _read_json_file(marker)
                record["payload"] = payload
                record["state"] = payload.get("state")
                record["owner_pid"] = payload.get("owner_pid")
                record["owner_pid_alive"] = _pid_alive(payload.get("owner_pid"))
                if payload.get("kind") != "claude-anyteam-stress-sandbox":
                    record["status"] = "invalid"
                    record["reason"] = "unexpected marker kind"
                elif payload.get("state") == "active" and record["owner_pid_alive"] is False:
                    record["status"] = "stale_active"
                    record["reason"] = "active marker owner pid is not alive"
                elif payload.get("state") in {"active", "completed", "aborted"}:
                    record["status"] = "expected"
                    record["reason"] = None
                else:
                    record["status"] = "invalid"
                    record["reason"] = "unexpected marker state"
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                record["status"] = "invalid"
                record["reason"] = f"{type(exc).__name__}: {exc}"
            markers.append(record)
    return markers


def _health(
    *,
    roster: list[dict[str, Any]],
    manifest_records: list[_ManifestRecord],
    wrapper_diagnostics: dict[str, Any],
    sandbox_markers: list[dict[str, Any]],
) -> dict[str, dict[str, str]]:
    health: dict[str, dict[str, str]] = {}
    routed = [row for row in roster if _is_routed_member(row)]

    latest_snapshot: dict[str, dict[str, Any]] = {}
    for row in wrapper_diagnostics.get("recent", []):
        event = row.get("event")
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        if event not in {"server_registered_snapshot", "list_tools"}:
            continue
        agent = row.get("agent")
        if isinstance(agent, str):
            latest_snapshot[agent] = row

    bad_snapshots: list[str] = []
    good_snapshots: list[str] = []
    for row in routed:
        name = str(row.get("name"))
        snap = latest_snapshot.get(name)
        if snap is None:
            continue
        payload = snap.get("payload") if isinstance(snap.get("payload"), dict) else {}
        missing = payload.get("missing_expected_tools")
        send_ok = payload.get("send_message_registered")
        if missing == [] and send_ok is True:
            good_snapshots.append(name)
        else:
            bad_snapshots.append(name)
    if not routed:
        health["adapter_mcp_responsive"] = {"status": "green", "summary": "no routed adapter members in scope"}
    elif bad_snapshots:
        health["adapter_mcp_responsive"] = {"status": "red", "summary": f"wrapper snapshots missing expected tools for {', '.join(bad_snapshots)}"}
    elif len(good_snapshots) == len(routed):
        health["adapter_mcp_responsive"] = {"status": "green", "summary": "latest wrapper snapshots registered all expected tools"}
    elif good_snapshots:
        missing = sorted(str(row.get("name")) for row in routed if str(row.get("name")) not in good_snapshots)
        health["adapter_mcp_responsive"] = {"status": "yellow", "summary": f"no recent wrapper snapshot for {', '.join(missing)}"}
    else:
        health["adapter_mcp_responsive"] = {"status": "yellow", "summary": "no wrapper MCP diagnostic snapshots found"}

    scoped_names = {str(row.get("name")) for row in routed}
    present_current = {r.agent for r in manifest_records if r.agent in scoped_names and r.status == "current"}
    missing_or_invalid = [r for r in manifest_records if r.agent in scoped_names and r.status in {"missing", "invalid"}]
    stale = [r for r in manifest_records if r.agent in scoped_names and r.status not in {"current", "missing", "invalid"}]
    if not routed:
        health["manifest_cache_populated"] = {"status": "green", "summary": "no routed adapter members in scope"}
    elif missing_or_invalid:
        names = ", ".join(f"{r.agent}:{r.status}" for r in missing_or_invalid)
        health["manifest_cache_populated"] = {"status": "red", "summary": f"manifest cache missing/invalid for {names}"}
    elif len(present_current) == len(scoped_names):
        health["manifest_cache_populated"] = {"status": "green", "summary": "manifests present for all scoped routed members"}
    elif stale:
        names = ", ".join(f"{r.agent}:{r.status}" for r in stale)
        health["manifest_cache_populated"] = {"status": "yellow", "summary": f"manifest cache has stale entries for {names}"}
    else:
        health["manifest_cache_populated"] = {"status": "yellow", "summary": "manifest cache state inconclusive"}

    advertised: set[str] = set()
    for row in roster:
        advertised.update(str(c) for c in row.get("capabilities", []) if c)
    for record in manifest_records:
        advertised.update(record.capabilities)
    missing_hooks = sorted(cap for cap in advertised if cap not in CAPABILITY_HOOKS)
    if missing_hooks:
        health["capability_hooks_registered"] = {"status": "red", "summary": f"missing runtime hook registry entries: {', '.join(missing_hooks)}"}
    elif advertised:
        health["capability_hooks_registered"] = {"status": "green", "summary": "advertised capabilities have runtime hooks"}
    else:
        health["capability_hooks_registered"] = {"status": "yellow", "summary": "no advertised capabilities found in scoped roster/manifests"}

    invalid_markers = [m for m in sandbox_markers if m.get("status") in {"invalid", "stale_active"}]
    if invalid_markers:
        health["sandbox_markers_expected"] = {"status": "red", "summary": "; ".join(f"{m.get('path')}: {m.get('reason')}" for m in invalid_markers)}
    elif sandbox_markers:
        health["sandbox_markers_expected"] = {"status": "green", "summary": "stress sandbox markers are active-with-live-owner or terminal"}
    else:
        health["sandbox_markers_expected"] = {"status": "yellow", "summary": "no stress sandbox marker found for scoped cwd"}

    return health


def _write_instrument_spawn(settings_path: Path) -> dict[str, Any]:
    try:
        settings = _read_json_file(settings_path)
        existed = True
    except FileNotFoundError:
        settings = {}
        existed = False
    env = settings.get("env")
    if env is None:
        env = {}
        settings["env"] = env
    if not isinstance(env, dict):
        raise ValueError(f"{settings_path} has non-object 'env'")
    previous = env.get(INSTRUMENT_ENV_KEY)
    env[INSTRUMENT_ENV_KEY] = INSTRUMENT_ENV_VALUE
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = settings_path.with_suffix(settings_path.suffix + ".tmp")
    tmp.write_text(json.dumps(settings, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, settings_path)
    return {
        "settings_path": str(settings_path),
        "env_key": INSTRUMENT_ENV_KEY,
        "previous_value": previous,
        "new_value": INSTRUMENT_ENV_VALUE,
        "changed": previous != INSTRUMENT_ENV_VALUE,
        "created_settings_file": not existed,
    }


def _build_report(args: argparse.Namespace, *, since: datetime | None, stderr: TextIO) -> tuple[dict[str, Any], int]:
    team = args.team or _current_team()
    if not team:
        stderr.write(f"error: --team is required when {TEAM_ENV}/{LEGACY_TEAM_ENV} are unset\n")
        return {}, 2

    team_root = _team_dir(team)
    cfg_path = team_cli.team_config_path(team)
    cfg = team_cli._existing_dict(cfg_path) if cfg_path.exists() else {}
    members = cfg.get("members") if isinstance(cfg, dict) else None
    scoped_agents = set()
    if isinstance(members, list):
        for member in members:
            if isinstance(member, dict) and isinstance(member.get("name"), str):
                name = member["name"]
                if args.agent is None or name == args.agent:
                    scoped_agents.add(name)
    elif args.agent:
        scoped_agents.add(args.agent)

    manifest_records, _manifest_map = _read_manifest_records(team_root, scoped_agents=scoped_agents, agent=args.agent)
    roster, roster_error = _roster(team, agent=args.agent, manifest_records=manifest_records, team_root=team_root)
    # If the roster was unreadable but a single agent was requested, keep the
    # missing synthetic manifest row tied to that agent visible.
    if roster_error and args.agent and not scoped_agents:
        manifest_records, _manifest_map = _read_manifest_records(team_root, scoped_agents={args.agent}, agent=args.agent)

    events = _recent_events(team_root, agent=args.agent, since=since)
    visibility = _visibility_degraded_summary(events)
    flap = _flap_repair_events(events, limit=args.limit)
    wrapper = _wrapper_diagnostics(team_root, agent=args.agent, since=since, limit=args.limit)
    markers = _sandbox_markers(roster)
    health = _health(
        roster=roster,
        manifest_records=manifest_records,
        wrapper_diagnostics=wrapper,
        sandbox_markers=markers,
    )

    instrumentation: dict[str, Any] | None = None
    if args.instrument_spawn:
        instrumentation = _write_instrument_spawn(_settings_path(args.settings_path))

    report = {
        "schema_version": 1,
        "mode": "mutating" if args.instrument_spawn else "read-only",
        "team": team,
        "agent": args.agent,
        "since": args.since,
        "team_dir": str(team_root),
        "roster_command": f"claude-anyteam team-roster --team {team} --json",
        "roster_error": roster_error,
        "roster": roster,
        "manifest_cache": [r.__dict__ for r in manifest_records],
        "visibility_degraded_last_50": visibility,
        "flap_repair_51": flap,
        "wrapper_mcp_diagnostics": wrapper,
        "sandbox_markers": markers,
        "health": health,
        "instrumentation": instrumentation,
    }
    return report, 0 if roster_error is None else 1


def _format_missing(value: Any) -> str:
    return "-" if value in (None, "", []) else str(value)


def _render_report(report: dict[str, Any]) -> str:
    lines: list[str] = []
    scope = report.get("agent") or "all"
    lines.append(f"claude-anyteam diagnose team={report.get('team')} scope={scope} mode={report.get('mode')}")
    lines.append(f"team_dir={report.get('team_dir')}")
    if report.get("since"):
        lines.append(f"since={report.get('since')}")
    if report.get("instrumentation"):
        inst = report["instrumentation"]
        lines.append(
            f"instrumentation: wrote env.{inst['env_key']}={inst['new_value']} to {inst['settings_path']} "
            f"changed={inst['changed']}"
        )
    if report.get("roster_error"):
        lines.append(f"roster_error={report.get('roster_error')}")
    lines.append("")

    lines.append("[roster]")
    roster = report.get("roster") or []
    if not roster:
        lines.append("(none)")
    for row in roster:
        caps = ",".join(row.get("capabilities") or []) or "-"
        lines.append(
            f"- {row.get('name')} type={row.get('agent_type')} backend={row.get('backend_type')} "
            f"model={row.get('model')} pid={_format_missing(row.get('adapter_pid'))} "
            f"capability_version={_format_missing(row.get('capability_version'))} capabilities={caps}"
        )
    lines.append("")

    lines.append("[manifest-cache]")
    manifests = report.get("manifest_cache") or []
    if not manifests:
        lines.append("(none)")
    for record in manifests:
        reason = f" reason={record.get('stale_reason')}" if record.get("stale_reason") else ""
        lines.append(
            f"- {record.get('agent')} capability_version={_format_missing(record.get('capability_version'))} "
            f"mtime={_format_missing(record.get('mtime'))} status={record.get('status')}{reason}"
        )
    lines.append("")

    visibility = report.get("visibility_degraded_last_50") or {}
    lines.append("[visibility-degraded:last-50]")
    lines.append(f"total={visibility.get('total', 0)}")
    categories = visibility.get("categories") or {}
    if not categories:
        lines.append("(none)")
    for category, item in categories.items():
        agents = ",".join(item.get("agents") or []) or "-"
        lines.append(f"- {category}: count={item.get('count')} latest={_format_missing(item.get('latest'))} agents={agents}")
    lines.append("")

    flap = report.get("flap_repair_51") or {}
    lines.append("[flap-repair:#51]")
    lines.append(f"total={flap.get('total', 0)}")
    recent_flap = flap.get("recent") or []
    if not recent_flap:
        lines.append("(none)")
    for row in recent_flap:
        lines.append(f"- {_format_missing(row.get('timestamp'))} {row.get('agent')} {row.get('summary') or row.get('event_id')}")
    lines.append("")

    wrapper = report.get("wrapper_mcp_diagnostics") or {}
    lines.append("[wrapper-mcp-diagnostics]")
    lines.append(f"path={wrapper.get('path')}")
    if not wrapper.get("exists"):
        lines.append("(none; #44 instrumentation log not found)")
    else:
        recent = wrapper.get("recent") or []
        if not recent:
            lines.append("(no matching snapshots)")
        for row in recent:
            payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
            missing = payload.get("missing_expected_tools")
            lines.append(
                f"- {_format_missing(row.get('timestamp'))} agent={row.get('agent')} event={row.get('event')} "
                f"pid={_format_missing(row.get('pid'))} "
                f"send_message_registered={_format_missing(payload.get('send_message_registered'))} "
                f"missing={missing if missing is not None else '-'}"
            )
    lines.append("")

    markers = report.get("sandbox_markers") or []
    lines.append("[sandbox-markers]")
    if not markers:
        lines.append("(none)")
    for marker in markers:
        lines.append(
            f"- {marker.get('path')} state={_format_missing(marker.get('state'))} "
            f"owner_pid={_format_missing(marker.get('owner_pid'))} status={marker.get('status')}"
        )
    lines.append("")

    glyph = {"green": "🟢", "yellow": "🟡", "red": "🔴"}
    lines.append("[health]")
    for key in ("adapter_mcp_responsive", "manifest_cache_populated", "capability_hooks_registered", "sandbox_markers_expected"):
        item = (report.get("health") or {}).get(key) or {"status": "yellow", "summary": "not checked"}
        status = item.get("status", "yellow")
        lines.append(f"{glyph.get(status, '🟡')} {key}: {item.get('summary')}")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# Bundle mode (--bundle): wrap the human-readable report in a markdown
# envelope suitable for GitHub issue submission. Adds versions + environment
# sections and sanitizes the user's home directory in paths.
# --------------------------------------------------------------------------- #


def _probe_cli_version(binary: str, *, args: tuple[str, ...] = ("--version",)) -> str | None:
    """Return the first version-shaped token from ``<binary> --version``, or None.

    Resolution mirrors the installer's `_parse_cli_version` heuristic but stays
    self-contained: missing binary → None, non-zero exit → None,
    no version-shaped token in output → None. Bounded subprocess timeout so a
    hung CLI cannot stall ``diagnose --bundle``.
    """
    if not shutil.which(binary):
        return None
    try:
        proc = subprocess.run(
            [binary, *args],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 and not proc.stdout:
        return None
    blob = (proc.stdout or "") + (proc.stderr or "")
    match = re.search(r"\b\d+\.\d+(?:\.\d+)?(?:[A-Za-z0-9.+-]*)?\b", blob)
    return match.group(0) if match else None


def _own_package_version() -> str:
    """Resolve ``claude-anyteam``'s installed version, falling back to 'unknown'.

    Defensive against editable-install or zip-import oddities where
    ``importlib.metadata.version`` raises.
    """
    try:
        return _pkg_version("claude-anyteam")
    except PackageNotFoundError:
        return "unknown"


def _detect_wsl() -> bool:
    """True iff /proc/version names microsoft (WSL/WSL2 marker)."""
    try:
        with open("/proc/version", encoding="utf-8") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


def _collect_versions_and_env() -> dict[str, Any]:
    return {
        "claude_anyteam": _own_package_version(),
        "codex": _probe_cli_version("codex"),
        "gemini": _probe_cli_version("gemini"),
        "kimi": _probe_cli_version("kimi"),
        "python": platform.python_version(),
        "os_system": platform.system(),
        "os_release": platform.release(),
        "wsl": _detect_wsl(),
    }


def _sanitize_home(text: str) -> str:
    """Replace the user's home directory in paths with '~' for issue safety.

    Operates on the rendered text once, after the report is fully composed.
    Idempotent if no occurrences are present. Does NOT touch team or agent
    names — the bundle docstring tells the user to review those manually.
    """
    home = os.path.expanduser("~")
    if not home or home == "/":
        return text
    return text.replace(home, "~")


def _render_bundle(report: dict[str, Any], inner: str, args: argparse.Namespace) -> str:
    """Wrap the human-readable report in a markdown envelope for GitHub issues."""
    versions = _collect_versions_and_env()

    def _line(label: str, value: Any) -> str:
        if value is None:
            return f"- {label}: (not installed)"
        return f"- {label}: {value}"

    parts: list[str] = []
    parts.append("# claude-anyteam diagnose bundle")
    parts.append("")
    parts.append(
        "> Generated for issue submission. The user's home directory has been "
        "replaced with `~/` in paths. Review and scrub team/agent names "
        "manually if they encode sensitive identifiers before posting publicly."
    )
    parts.append("")

    parts.append("## Versions")
    parts.append("")
    parts.append(_line("claude-anyteam", versions["claude_anyteam"]))
    parts.append(_line("codex CLI", versions["codex"]))
    parts.append(_line("gemini CLI", versions["gemini"]))
    parts.append(_line("kimi CLI", versions["kimi"]))
    parts.append(_line("Python", versions["python"]))
    os_label = f"{versions['os_system']} {versions['os_release']}"
    if versions["wsl"]:
        os_label += " (WSL detected)"
    parts.append(_line("OS", os_label))
    parts.append("")

    scope_bits = []
    if report.get("team"):
        scope_bits.append(f"team={report['team']}")
    if report.get("agent"):
        scope_bits.append(f"agent={report['agent']}")
    if report.get("since"):
        scope_bits.append(f"since={report['since']}")
    if args.limit != 10:
        scope_bits.append(f"limit={args.limit}")
    parts.append("## Scope")
    parts.append("")
    parts.append("- " + (", ".join(scope_bits) if scope_bits else "(default scope: all members, no since filter, default limit)"))
    parts.append("")

    parts.append("## Report")
    parts.append("")
    # Use FOUR-backtick fence so any inner triple-fence content (e.g.
    # GitHub-rendered code from the human report) survives nested rendering.
    parts.append("````")
    parts.append(_sanitize_home(inner).rstrip("\n"))
    parts.append("````")
    parts.append("")

    parts.append("## Suggested next steps")
    parts.append("")
    parts.append(
        "- Attach the relevant `~/.claude/teams/<team>/events/<agent>.jsonl` "
        "lines from around the failure window for the maintainer to inspect "
        "raw typed-event payloads."
    )
    parts.append(
        "- If the failure is reproducible, re-run with `--since <iso-time>` "
        "to scope this report to the exact failure window."
    )
    parts.append(
        "- Re-run with `--instrument-spawn` BEFORE the next reproduction if "
        "the bug touches MCP tool discovery or wrapper-side registration."
    )
    parts.append("")
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# Codex sqlite WAL bloat diagnostic (#43)
# --------------------------------------------------------------------------- #


def _build_codex_log_bloat_report(args: argparse.Namespace) -> dict[str, Any]:
    sqlite_home = (
        Path(args.codex_sqlite_home).expanduser()
        if getattr(args, "codex_sqlite_home", None)
        else None
    )
    report = inspect_codex_log_bloat(sqlite_home=sqlite_home)
    status = "degraded" if report.is_bloated else "ok"
    return {
        "schema_version": 1,
        "mode": "read-only",
        "status": status,
        "codex_log_bloat": report.to_dict(),
        "impact": (
            "Bloated Codex logs_*.sqlite-wal files can stall `codex app-server` "
            "before JSON-RPC initialize is serviced."
        ),
        "operator_actions": [
            "Run `claude-anyteam diagnose --codex-log-bloat` to confirm size before a repro.",
            (
                "If no Codex process is running, run a sqlite checkpoint or move/remove "
                "the matching logs_*.sqlite* files after backing them up."
            ),
            (
                "claude-anyteam automatically emits a typed visibility_degraded warning "
                "and attempts a bounded sqlite checkpoint before Codex App Server spawn."
            ),
        ],
    }


def _render_codex_log_bloat_report(report: dict[str, Any]) -> str:
    data = report.get("codex_log_bloat") or {}
    lines: list[str] = []
    lines.append("claude-anyteam diagnose --codex-log-bloat mode=read-only")
    lines.append(
        f"sqlite_home={data.get('sqlite_home')} source={data.get('sqlite_home_source')}"
    )
    threshold = data.get("threshold_bytes")
    lines.append(f"threshold={format_bytes(threshold)} ({threshold} bytes)")
    lines.append("")
    lines.append("[codex-log-bloat]")
    lines.append(
        "status={status} wal_files={files} bloated={bloated} total_wal={total} max_wal={max_wal}".format(
            status=report.get("status"),
            files=data.get("wal_file_count", 0),
            bloated=data.get("bloated_wal_file_count", 0),
            total=format_bytes(data.get("total_wal_bytes")),
            max_wal=format_bytes(data.get("max_wal_bytes")),
        )
    )
    wal_files = data.get("wal_files") or []
    if not wal_files:
        lines.append("(no logs_*.sqlite-wal files found)")
    for row in wal_files:
        state = "BLOATED" if row.get("exceeds_threshold") else "ok"
        lines.append(
            f"- {row.get('path')} status={state} wal={format_bytes(row.get('size_bytes'))} "
            f"db={format_bytes(row.get('database_size_bytes'))}"
        )
    lines.append("")
    if report.get("status") == "degraded":
        lines.append("impact: " + str(report.get("impact")))
        lines.append("operator_actions:")
        for action in report.get("operator_actions") or []:
            lines.append(f"- {action}")
    else:
        lines.append("impact: no threshold-exceeding Codex sqlite WAL files detected")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def main(argv: list[str], *, stdout: TextIO | None = None, stderr: TextIO | None = None) -> int:
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.limit < 0:
        err.write("error: --limit must be >= 0\n")
        return 2

    if args.codex_log_bloat:
        if args.incident or args.incidents:
            err.write("error: --codex-log-bloat cannot be combined with incident mode\n")
            return 2
        if args.instrument_spawn:
            err.write("error: --codex-log-bloat cannot be combined with --instrument-spawn\n")
            return 2
        if args.bundle:
            err.write("error: --codex-log-bloat cannot be combined with --bundle\n")
            return 2
        try:
            report = _build_codex_log_bloat_report(args)
        except (OSError, ValueError) as exc:
            err.write(f"error: {exc}\n")
            return 1
        if args.json:
            out.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
        else:
            out.write(_render_codex_log_bloat_report(report))
        return 1 if report.get("status") == "degraded" else 0

    if args.incident or args.incidents:
        if args.instrument_spawn:
            err.write("error: --instrument-spawn cannot be combined with incident mode\n")
            return 2
        if args.bundle:
            err.write("error: --bundle cannot be combined with incident mode\n")
            return 2
        return _incident_command(args, stdout=out, stderr=err)

    if args.bundle and args.json:
        err.write("error: --bundle cannot be combined with --json (bundle is markdown-shaped for issue submission)\n")
        return 2

    try:
        since = _parse_iso(args.since)
        report, code = _build_report(args, since=since, stderr=err)
    except argparse.ArgumentTypeError as exc:
        err.write(f"error: {exc}\n")
        return 2
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        err.write(f"error: {exc}\n")
        return 1

    if not report:
        return code
    if args.json:
        out.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    elif args.bundle:
        out.write(_render_bundle(report, _render_report(report), args))
    else:
        out.write(_render_report(report))
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
