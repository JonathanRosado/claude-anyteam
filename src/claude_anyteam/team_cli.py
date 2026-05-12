"""Team-management CLI helpers.

Team-management subcommands of ``claude-anyteam``:

  * ``team-agent``  — write per-teammate ``model``/``effort``/watchdog overrides at
                      ``~/.claude/teams/<team>/agents/<agent>.json``. The spawn
                      shim reads this file and forwards the values to the
                      routed adapter (Codex, Gemini, Kimi). Native Claude
                      teammates keep their host-supplied model/agent type and
                      do not consume these adapter overrides.

  * ``team-patch``  — apply post-spawn fixups to a teammate row in
                      ``~/.claude/teams/<team>/config.json``. Today this
                      means setting ``agentType`` to ``claude-anyteam`` for
                      routed external-LLM teammates so wrapper MCP validation
                      passes. Native ``claude-*`` teammates are intentionally
                      left as host-managed Claude rows.

  * ``team-roster`` — print a one-line-per-member roster summary so a
                      coordinating LLM can introspect the team without
                      reading and parsing config.json by hand.

These commands exist so a coordinating LLM has a typed, allowlistable
contract for team management and does not have to know magic file paths,
JSON shapes, or which fields need which post-spawn fixups. Future
sessions: prefer ``claude-anyteam team-*`` over ad-hoc Write/Edit/Bash.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from claude_teams._filelock import config_lock

# Effort whitelist mirrors the per-backend allowlists (Codex/Gemini/Kimi all
# accept the same five-tier scale). If a future backend diverges, this
# becomes the union and the per-backend adapter remains the source of truth.
EFFORT_CHOICES = ("minimal", "low", "medium", "high", "xhigh")

# Whitelisted keys mirror spawn_shim._AGENT_CONFIG_KEYS; expanding this set
# requires extending the shim too.
AGENT_CONFIG_KEYS = (
    "model",
    "effort",
    "turn_timeout_s",
    "non_progress_warn_s",
    "non_progress_interrupt_s",
    "wrapper_tool_failure_window_s",
)

# Default post-spawn agentType for codex-/gemini-/kimi- teammates. The Agent
# tool spawns them with ``agentType="general-purpose"``, which fails wrapper
# MCP validation; ``claude-anyteam`` is the canonical value.
DEFAULT_PATCHED_AGENT_TYPE = "claude-anyteam"


def _teams_root() -> Path:
    return Path.home() / ".claude" / "teams"


def agent_config_path(team: str, agent: str) -> Path:
    return _teams_root() / team / "agents" / f"{agent}.json"


def team_config_path(team: str) -> Path:
    return _teams_root() / team / "config.json"


def _validate_team_name(value: str) -> str:
    if not value or any(ch in value for ch in ("/", "\\", "\x00")):
        raise argparse.ArgumentTypeError(
            f"invalid team name {value!r}: must be non-empty and contain no path separators"
        )
    if value in (".", ".."):
        raise argparse.ArgumentTypeError(f"invalid team name {value!r}")
    return value


def _validate_agent_name(value: str) -> str:
    if not value or any(ch in value for ch in ("/", "\\", "\x00")):
        raise argparse.ArgumentTypeError(
            f"invalid agent name {value!r}: must be non-empty and contain no path separators"
        )
    if value in (".", ".."):
        raise argparse.ArgumentTypeError(f"invalid agent name {value!r}")
    return value


def _write_atomic_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _existing_dict(path: Path) -> dict[str, object]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


# --------------------------------------------------------------------------- #
# team-agent
# --------------------------------------------------------------------------- #


def _build_team_agent_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="claude-anyteam team-agent",
        description=(
            "Write or update a per-teammate config file at "
            "~/.claude/teams/<team>/agents/<agent>.json. The spawn shim reads "
            "this file and forwards model/effort/watchdog overrides to the "
            "routed adapter. Native Claude teammates keep host-supplied "
            "spawn settings instead."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  claude-anyteam team-agent codex-alice --team build --model gpt-5.5 --effort xhigh\n"
            "  claude-anyteam team-agent codex-alice --team build --non-progress-warn-s 300 \\\n"
            "    --non-progress-interrupt-s 420\n"
            "  claude-anyteam team-agent gemini-bob  --team build --model gemini-3.1-pro-preview --effort high\n"
            "  claude-anyteam team-agent kimi-cara   --team build --model kimi-for-coding\n"
            "  claude-anyteam team-agent codex-alice --team build --remove\n"
            "\nThe agent name's prefix (codex-/gemini-/kimi-) determines which adapter receives the\n"
            "config; claude-* names are native Claude and do not consume this file. This command\n"
            "does not validate the model slug against any backend catalog."
        ),
    )
    p.add_argument("agent", type=_validate_agent_name, help="Agent name (e.g. codex-alice, gemini-bob, kimi-cara)")
    p.add_argument("--team", type=_validate_team_name, required=True, help="Team name (the parent directory under ~/.claude/teams/)")
    p.add_argument("--model", help="Model slug to forward as --model to the adapter")
    p.add_argument(
        "--effort",
        choices=EFFORT_CHOICES,
        help="Reasoning/thinking effort to forward as --effort to the adapter",
    )
    p.add_argument(
        "--turn-timeout-s",
        type=float,
        help=(
            "Wall-clock cap (seconds) on a single Codex App Server turn. "
            "Range [60, 3600], default 1800 (bumped from 900 in task #5). "
            "Forwarded to the adapter as --turn-timeout-s. Useful for "
            "teammates that run long pytest or build invocations; tighten "
            "for short-loop executor roles. Codex App Server-only today."
        ),
    )
    p.add_argument(
        "--non-progress-warn-s",
        type=float,
        help=(
            "Codex App Server soft non-progress watchdog threshold in seconds. "
            "Range [60, 1800], default unset (opt-in as of task #5; see "
            "docs/design/timers-vs-visibility.md). Forwarded to the adapter "
            "as --non-progress-warn-s."
        ),
    )
    p.add_argument(
        "--non-progress-interrupt-s",
        type=float,
        help=(
            "Codex App Server opt-in hard non-progress interrupt threshold in "
            "seconds. Range [60, 3600] when set; omitted by default."
        ),
    )
    p.add_argument(
        "--wrapper-tool-failure-window-s",
        type=float,
        help=(
            "Codex App Server wrapper-tool recovery discriminator window in "
            "seconds. Range [60, 300], default 90. Forwarded to the adapter "
            "as --wrapper-tool-failure-window-s."
        ),
    )
    p.add_argument(
        "--remove",
        action="store_true",
        help="Delete the per-teammate config file instead of writing one",
    )
    p.add_argument(
        "--print-path",
        action="store_true",
        help="After writing, print the absolute config path on stdout (default: print a one-line summary)",
    )
    return p


def _team_agent_command(argv: list[str], *, stdout: TextIO | None = None, stderr: TextIO | None = None) -> int:
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    parser = _build_team_agent_parser()
    args = parser.parse_args(argv)

    path = agent_config_path(args.team, args.agent)

    if args.remove:
        try:
            path.unlink()
            out.write(f"removed {path}\n")
            return 0
        except FileNotFoundError:
            out.write(f"no config to remove at {path}\n")
            return 0
        except OSError as exc:
            err.write(f"failed to remove {path}: {exc}\n")
            return 1

    if (
        args.model is None
        and args.effort is None
        and args.turn_timeout_s is None
        and args.non_progress_warn_s is None
        and args.non_progress_interrupt_s is None
        and args.wrapper_tool_failure_window_s is None
    ):
        err.write(
            "error: at least one of --model/--effort/--turn-timeout-s/"
            "--non-progress-warn-s/--non-progress-interrupt-s/"
            "--wrapper-tool-failure-window-s must be provided (or use --remove "
            "to delete)\n"
        )
        return 2

    if args.turn_timeout_s is not None and not (60.0 <= args.turn_timeout_s <= 3600.0):
        err.write(
            f"error: --turn-timeout-s must be in [60, 3600] seconds, got {args.turn_timeout_s}\n"
        )
        return 2
    if args.non_progress_warn_s is not None and not (
        60.0 <= args.non_progress_warn_s <= 1800.0
    ):
        err.write(
            "error: --non-progress-warn-s must be in [60, 1800] seconds, "
            f"got {args.non_progress_warn_s}\n"
        )
        return 2
    if args.non_progress_interrupt_s is not None and not (
        60.0 <= args.non_progress_interrupt_s <= 3600.0
    ):
        err.write(
            "error: --non-progress-interrupt-s must be in [60, 3600] seconds, "
            f"got {args.non_progress_interrupt_s}\n"
        )
        return 2
    if args.wrapper_tool_failure_window_s is not None and not (
        60.0 <= args.wrapper_tool_failure_window_s <= 300.0
    ):
        err.write(
            "error: --wrapper-tool-failure-window-s must be in [60, 300] "
            f"seconds, got {args.wrapper_tool_failure_window_s}\n"
        )
        return 2

    config = _existing_dict(path)
    config = {k: v for k, v in config.items() if k in AGENT_CONFIG_KEYS}

    if args.model is not None:
        config["model"] = args.model
    if args.effort is not None:
        config["effort"] = args.effort
    if args.turn_timeout_s is not None:
        config["turn_timeout_s"] = args.turn_timeout_s
    if args.non_progress_warn_s is not None:
        config["non_progress_warn_s"] = args.non_progress_warn_s
    if args.non_progress_interrupt_s is not None:
        config["non_progress_interrupt_s"] = args.non_progress_interrupt_s
    if args.wrapper_tool_failure_window_s is not None:
        config["wrapper_tool_failure_window_s"] = (
            args.wrapper_tool_failure_window_s
        )

    _write_atomic_json(path, config)

    if args.print_path:
        out.write(f"{path}\n")
    else:
        keys = ", ".join(f"{k}={v}" for k, v in sorted(config.items()))
        out.write(f"wrote {path} ({keys})\n")
    return 0


# --------------------------------------------------------------------------- #
# team-patch
# --------------------------------------------------------------------------- #


def _build_team_patch_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="claude-anyteam team-patch",
        description=(
            "Apply post-spawn fixups to a teammate row in "
            "~/.claude/teams/<team>/config.json. The Agent tool spawns "
            "external-LLM teammates with agentType='general-purpose'; the "
            "wrapper MCP requires agentType='claude-anyteam'. This command "
            "patches that field idempotently for routed adapters only."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  claude-anyteam team-patch codex-alice --team build\n"
            "  claude-anyteam team-patch --team build --all-external\n"
            "\nWith --all-external the command patches every member whose name starts with\n"
            "codex-, gemini-, or kimi- (the routed-adapter prefixes). Native\n"
            "claude-* members are host-managed and are not patched."
        ),
    )
    p.add_argument(
        "agent",
        nargs="?",
        type=_validate_agent_name,
        help="Agent name to patch (omit when using --all-external)",
    )
    p.add_argument("--team", type=_validate_team_name, required=True, help="Team name")
    p.add_argument(
        "--agent-type",
        default=DEFAULT_PATCHED_AGENT_TYPE,
        help=f"agentType value to write (default: {DEFAULT_PATCHED_AGENT_TYPE})",
    )
    p.add_argument(
        "--all-external",
        action="store_true",
        help="Patch every member with a routed-adapter prefix (codex-/gemini-/kimi-)",
    )
    return p


# Native Claude is the host teammate harness, not a routed adapter. Keep
# claude-* out of this set so those rows preserve agentType="claude" (or any
# host-supplied value) and host-supplied capabilities.
_ROUTED_PREFIXES = ("codex-", "gemini-", "kimi-")


def _team_patch_command(argv: list[str], *, stdout: TextIO | None = None, stderr: TextIO | None = None) -> int:
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    parser = _build_team_patch_parser()
    args = parser.parse_args(argv)

    if args.all_external == bool(args.agent):
        err.write("error: provide exactly one of <agent> or --all-external\n")
        return 2

    path = team_config_path(args.team)
    if not path.exists():
        err.write(f"error: no team config at {path}\n")
        return 1

    with config_lock(path.parent):
        cfg = _existing_dict(path)
        members = cfg.get("members")
        if not isinstance(members, list):
            err.write(f"error: {path} has no 'members' list\n")
            return 1

        targets: list[str]
        if args.all_external:
            targets = [
                m["name"]
                for m in members
                if isinstance(m, dict)
                and isinstance(m.get("name"), str)
                and m["name"].startswith(_ROUTED_PREFIXES)
            ]
            if not targets:
                out.write(f"no routed-adapter members in {path}\n")
                return 0
        else:
            targets = [args.agent]

        patched: list[str] = []
        missing: list[str] = []
        for name in targets:
            member = next(
                (m for m in members if isinstance(m, dict) and m.get("name") == name),
                None,
            )
            if member is None:
                missing.append(name)
                continue
            if member.get("agentType") == args.agent_type:
                continue
            member["agentType"] = args.agent_type
            patched.append(name)

        if patched:
            _write_atomic_json(path, cfg)

    if missing:
        err.write(f"warning: members not found: {', '.join(missing)}\n")
    if patched:
        out.write(f"patched agentType={args.agent_type} on {len(patched)} member(s): {', '.join(patched)}\n")
    elif not missing:
        out.write("no changes needed\n")
    return 1 if missing and not patched else 0


# --------------------------------------------------------------------------- #
# team-roster
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _RosterRow:
    name: str
    agent_type: str
    model: str
    backend_type: str
    color: str
    # `is_active` is None when the field is absent in config.json (routed
    # teammates today; the host doesn't write it for them). Distinguishing
    # absent from explicit-false matters for the roster's stale marker —
    # we only flag rows that the host has actively reported as inactive,
    # not rows where we simply have no signal.
    is_active: bool | None
    # Effective per-teammate spawn-time overrides resolved from
    # ~/.claude/teams/<team>/agents/<name>.json. None when no config file
    # exists for this teammate (i.e. defaults apply). Present in JSON
    # output and the human roster's --effective view so the lead can
    # confirm a `team-agent` write actually took effect.
    adapter_model: str | None = None
    adapter_effort: str | None = None
    adapter_turn_timeout_s: float | None = None
    adapter_non_progress_warn_s: float | None = None
    adapter_non_progress_interrupt_s: float | None = None
    adapter_wrapper_tool_failure_window_s: float | None = None
    config_source: str | None = None
    # 09 R11 / 08 §6.3 cheap capability flags; rich Agent Card
    # manifests are intentionally not expanded in the roster table.
    capabilities: list[str] = field(default_factory=list)
    # tmux pane id from config.json. Used by the dead-pane check to flag
    # members whose backing tmux pane is gone (host crash, tmux kill-server,
    # process panic). Empty for non-tmux backends like the team-lead row.
    tmux_pane_id: str = ""
    # Read-only diagnostic enrichments. `capability_version` comes from the
    # R12 rich manifest cache; `adapter_pid` is the newest PID observed in the
    # wrapper MCP tool-discovery diagnostics (#44). Both are hints rather than
    # hard liveness assertions, so absence is represented as None.
    capability_version: str | None = None
    adapter_pid: int | None = None
    adapter_pid_source: str | None = None


def _build_team_roster_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="claude-anyteam team-roster",
        description=(
            "Print a one-line-per-member summary of the team's config.json. "
            "Use this instead of reading and parsing the file by hand. "
            "By default the resolved spawn-time adapter config is shown "
            "alongside the host model — use --no-resolve for legacy output."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--team", type=_validate_team_name, required=True, help="Team name")
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit a JSON array instead of the human-readable table",
    )
    p.add_argument(
        "--no-resolve",
        action="store_true",
        help=(
            "Skip resolving per-teammate adapter overrides "
            "(~/.claude/teams/<team>/agents/<name>.json). Faster + matches "
            "pre-v0.6.0 output for scripted callers."
        ),
    )
    return p


def _roster_rows(cfg: dict[str, object], *, team: str | None = None, resolve: bool = True) -> list[_RosterRow]:
    members = cfg.get("members")
    if not isinstance(members, list):
        return []
    rows: list[_RosterRow] = []
    for m in members:
        if not isinstance(m, dict):
            continue
        name = str(m.get("name", "?"))
        adapter_model: str | None = None
        adapter_effort: str | None = None
        adapter_turn_timeout_s: float | None = None
        adapter_non_progress_warn_s: float | None = None
        adapter_non_progress_interrupt_s: float | None = None
        adapter_wrapper_tool_failure_window_s: float | None = None
        config_source: str | None = None
        if resolve and team is not None:
            agent_cfg, source = _read_agent_config(team, name)
            adapter_model = agent_cfg.get("model")
            adapter_effort = agent_cfg.get("effort")
            tt = agent_cfg.get("turn_timeout_s")
            if tt is not None:
                try:
                    adapter_turn_timeout_s = float(tt)
                except (TypeError, ValueError):
                    adapter_turn_timeout_s = None
            npw = agent_cfg.get("non_progress_warn_s")
            if npw is not None:
                try:
                    adapter_non_progress_warn_s = float(npw)
                except (TypeError, ValueError):
                    adapter_non_progress_warn_s = None
            npi = agent_cfg.get("non_progress_interrupt_s")
            if npi is not None:
                try:
                    adapter_non_progress_interrupt_s = float(npi)
                except (TypeError, ValueError):
                    adapter_non_progress_interrupt_s = None
            wtf = agent_cfg.get("wrapper_tool_failure_window_s")
            if wtf is not None:
                try:
                    adapter_wrapper_tool_failure_window_s = float(wtf)
                except (TypeError, ValueError):
                    adapter_wrapper_tool_failure_window_s = None
            config_source = source
        capability_version = _manifest_version_for_agent(team, name) if resolve and team is not None else None
        adapter_pid = _latest_wrapper_diag_pid(team, name) if resolve and team is not None else None
        raw_capabilities = m.get("capabilities", [])
        capabilities = (
            [str(capability) for capability in raw_capabilities]
            if isinstance(raw_capabilities, list)
            else []
        )
        rows.append(
            _RosterRow(
                name=name,
                agent_type=str(m.get("agentType", "?")),
                model=str(m.get("model", "?")),
                backend_type=str(m.get("backendType", "?")),
                color=str(m.get("color", "?")),
                is_active=(bool(m["isActive"]) if "isActive" in m else None),
                adapter_model=adapter_model,
                adapter_effort=adapter_effort,
                adapter_turn_timeout_s=adapter_turn_timeout_s,
                adapter_non_progress_warn_s=adapter_non_progress_warn_s,
                adapter_non_progress_interrupt_s=adapter_non_progress_interrupt_s,
                adapter_wrapper_tool_failure_window_s=(
                    adapter_wrapper_tool_failure_window_s
                ),
                config_source=config_source,
                capabilities=capabilities,
                tmux_pane_id=str(m.get("tmuxPaneId", "")),
                capability_version=capability_version,
                adapter_pid=adapter_pid,
                adapter_pid_source=(
                    "wrapper-mcp-tools.jsonl" if adapter_pid is not None else None
                ),
            )
        )
    return rows


def _is_dead_tmux_pane(row: _RosterRow, live_pane_ids: frozenset[str] | None) -> bool:
    """True when the row's tmux pane is verifiably gone.

    Returns False when:
    - The row isn't tmux-backed (no tmuxPaneId, or it's the
      `in-process` sentinel routed teammates use).
    - tmux liveness is unknown (`live_pane_ids is None`) — we don't
      claim panes are dead when we can't actually check.

    Returns True only when tmux IS running, but the row's pane id
    isn't in its live-pane set. That's the unambiguous "dead" case.
    """
    if live_pane_ids is None:
        return False
    pane = row.tmux_pane_id
    if not pane or pane == "in-process":
        return False
    return pane not in live_pane_ids


def _read_agent_config(team: str, agent: str) -> tuple[dict[str, Any], str | None]:
    """Read the per-teammate adapter override file. Returns (config, source).

    `source` is the absolute path string when the file exists, or None when
    no per-teammate config was found (defaults apply). The return shape
    matches what spawn_shim._load_agent_config consumes — keep the two
    in sync if you extend the schema.
    """
    path = agent_config_path(team, agent)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, None
    except (OSError, json.JSONDecodeError):
        return {}, None
    if not isinstance(raw, dict):
        return {}, str(path)
    return raw, str(path)


def _manifest_version_for_agent(team: str, agent: str) -> str | None:
    path = _teams_root() / team / "manifests" / f"{agent}.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    value = raw.get("capability_version", raw.get("capabilityVersion"))
    return str(value) if value is not None else None


def _latest_wrapper_diag_pid(team: str, agent: str) -> int | None:
    path = _teams_root() / team / "diagnostics" / "wrapper-mcp-tools.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, OSError):
        return None
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict) or row.get("agent") != agent:
            continue
        pid = row.get("pid")
        if isinstance(pid, int):
            return pid
    return None


def _format_capabilities(capabilities: list[str]) -> str:
    return ",".join(capabilities) if capabilities else "-"


def _team_roster_command(argv: list[str], *, stdout: TextIO | None = None, stderr: TextIO | None = None) -> int:
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    parser = _build_team_roster_parser()
    args = parser.parse_args(argv)

    path = team_config_path(args.team)
    if not path.exists():
        err.write(f"error: no team config at {path}\n")
        return 1

    cfg = _existing_dict(path)
    rows = _roster_rows(cfg, team=args.team, resolve=not args.no_resolve)

    live_pane_ids = _live_tmux_pane_ids()

    if args.json:
        records = []
        for row in rows:
            r = dict(row.__dict__)
            r["is_dead_pane"] = _is_dead_tmux_pane(row, live_pane_ids)
            records.append(r)
        out.write(json.dumps(records, indent=2, sort_keys=True) + "\n")
        return 0

    if not rows:
        out.write(f"team {args.team!r} has no members\n")
        return 0

    name_w = max(len(r.name) for r in rows)
    type_w = max(len(r.agent_type) for r in rows)
    model_w = max(len(r.model) for r in rows)
    # B5 ghost detection: when re-spawning by the same name, the host appends
    # `-2`/`-3` to disambiguate, leaving the prior entry in the roster. Flag
    # a row as a likely ghost when there's another row with a higher numeric
    # suffix in the same name family (e.g. `codex-research` is a ghost when
    # `codex-research-2` also exists). isActive is unreliable across
    # backends — config.json is set at spawn time and not updated on the
    # fly — so we use the structural duplicate-name signal instead.
    families: dict[str, int] = {}
    for r in rows:
        base, n = _split_respawn_suffix(r.name)
        families[base] = max(families.get(base, n), n)

    for r in rows:
        base, n = _split_respawn_suffix(r.name)
        is_ghost = families.get(base, n) > n
        is_dead_pane = _is_dead_tmux_pane(r, live_pane_ids)
        # Both markers compose: a row can be a re-spawn ghost AND have a
        # dead pane. Show whichever is more actionable: dead-pane is
        # stronger evidence (verifiable now), ghost is heuristic.
        marker = "⚠ " if (is_ghost or is_dead_pane) else "  "
        statuses = []
        if is_dead_pane:
            statuses.append("dead-pane")
        if is_ghost:
            statuses.append("ghost")
        suffix = ("  status=" + ",".join(statuses)) if statuses else ""
        # Append the resolved adapter overrides when present, so the lead
        # can confirm a team-agent write actually took effect. Silent when
        # no per-teammate config exists — defaults apply, no noise.
        adapter_suffix = ""
        if (
            r.adapter_model
            or r.adapter_effort
            or r.adapter_turn_timeout_s is not None
            or r.adapter_non_progress_warn_s is not None
            or r.adapter_non_progress_interrupt_s is not None
            or r.adapter_wrapper_tool_failure_window_s is not None
        ):
            parts = []
            if r.adapter_model:
                parts.append(f"adapter_model={r.adapter_model}")
            if r.adapter_effort:
                parts.append(f"adapter_effort={r.adapter_effort}")
            if r.adapter_turn_timeout_s is not None:
                parts.append(f"adapter_turn_timeout_s={r.adapter_turn_timeout_s}")
            if r.adapter_non_progress_warn_s is not None:
                parts.append(
                    f"adapter_non_progress_warn_s={r.adapter_non_progress_warn_s}"
                )
            if r.adapter_non_progress_interrupt_s is not None:
                parts.append(
                    "adapter_non_progress_interrupt_s="
                    f"{r.adapter_non_progress_interrupt_s}"
                )
            if r.adapter_wrapper_tool_failure_window_s is not None:
                parts.append(
                    "adapter_wrapper_tool_failure_window_s="
                    f"{r.adapter_wrapper_tool_failure_window_s}"
                )
            adapter_suffix = "  " + " ".join(parts)
        capabilities_suffix = f"  capabilities={_format_capabilities(r.capabilities)}"
        diagnostic_suffix = ""
        if r.capability_version is not None or r.adapter_pid is not None:
            parts = []
            if r.capability_version is not None:
                parts.append(f"capability_version={r.capability_version}")
            if r.adapter_pid is not None:
                parts.append(f"adapter_pid={r.adapter_pid}")
            diagnostic_suffix = "  " + " ".join(parts)
        line = (
            f"{marker}{r.name:<{name_w}}  type={r.agent_type:<{type_w}}  model={r.model:<{model_w}}  "
            f"backend={r.backend_type}  color={r.color}{suffix}{adapter_suffix}{capabilities_suffix}{diagnostic_suffix}\n"
        )
        out.write(line)
    return 0


def _live_tmux_pane_ids() -> frozenset[str] | None:
    """Return the set of currently live tmux pane ids (e.g. {"%0", "%1", …}),
    or `None` if tmux is unavailable / no server is running.

    Distinguishing "no tmux" from "empty tmux" matters: if tmux isn't
    running at all (`error connecting to /tmp/tmux-1000/default`), we
    can't make a liveness assertion and shouldn't falsely flag every
    teammate as dead. The caller should treat `None` as "unknown".

    Best-effort: any non-zero exit, missing binary, or parse failure
    returns `None`.
    """
    if not shutil.which("tmux"):
        return None
    try:
        proc = subprocess.run(
            ["tmux", "list-panes", "-a", "-F", "#{pane_id}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if proc.returncode != 0:
        return None
    ids = {line.strip() for line in proc.stdout.splitlines() if line.strip()}
    return frozenset(ids)


def _split_respawn_suffix(name: str) -> tuple[str, int]:
    """Split a re-spawned-team name into (family_base, generation_index).

    `codex-research` → (`codex-research`, 1)
    `codex-research-2` → (`codex-research`, 2)
    `codex-research-12` → (`codex-research`, 12)

    Used for B5 ghost detection in `team-roster`.
    """
    parts = name.rsplit("-", 1)
    if len(parts) == 2 and parts[1].isdigit() and parts[0]:
        return parts[0], int(parts[1])
    return name, 1


# --------------------------------------------------------------------------- #
# Dispatch
# --------------------------------------------------------------------------- #


def _build_team_config_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="claude-anyteam team-config",
        description=(
            "Print the resolved spawn-time config for one teammate: host model "
            "from config.json plus per-teammate adapter overrides from "
            "agents/<name>.json. Use this when investigating why a "
            "teammate is running at the wrong model/effort/timeout — it "
            "shows exactly what the spawn shim will pass to the routed CLI."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("agent", type=_validate_agent_name, help="Agent name")
    p.add_argument("--team", type=_validate_team_name, required=True, help="Team name")
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of human-readable summary.",
    )
    return p


def _team_config_command(argv: list[str], *, stdout: TextIO | None = None, stderr: TextIO | None = None) -> int:
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    args = _build_team_config_parser().parse_args(argv)

    cfg_path = team_config_path(args.team)
    if not cfg_path.exists():
        err.write(f"error: no team config at {cfg_path}\n")
        return 1
    cfg = _existing_dict(cfg_path)

    members = cfg.get("members") if isinstance(cfg, dict) else None
    member: dict[str, Any] | None = None
    if isinstance(members, list):
        for m in members:
            if isinstance(m, dict) and m.get("name") == args.agent:
                member = m
                break
    if member is None:
        err.write(f"error: agent {args.agent!r} not found in team {args.team!r}\n")
        return 1

    agent_cfg, source = _read_agent_config(args.team, args.agent)
    resolved = {
        "team": args.team,
        "agent": args.agent,
        "host_agent_type": member.get("agentType"),
        "host_model": member.get("model"),
        "host_backend_type": member.get("backendType"),
        "adapter_model": agent_cfg.get("model"),
        "adapter_effort": agent_cfg.get("effort"),
        "adapter_turn_timeout_s": agent_cfg.get("turn_timeout_s"),
        "adapter_non_progress_warn_s": agent_cfg.get("non_progress_warn_s"),
        "adapter_non_progress_interrupt_s": agent_cfg.get("non_progress_interrupt_s"),
        "adapter_wrapper_tool_failure_window_s": agent_cfg.get(
            "wrapper_tool_failure_window_s"
        ),
        "config_source": source,
    }

    if args.json:
        out.write(json.dumps(resolved, indent=2, sort_keys=True) + "\n")
        return 0

    out.write(f"team   : {resolved['team']}\n")
    out.write(f"agent  : {resolved['agent']}\n")
    out.write(f"host   : agent_type={resolved['host_agent_type']!r} model={resolved['host_model']!r} backend_type={resolved['host_backend_type']!r}\n")
    if source is None:
        out.write("adapter: <no per-teammate config; defaults apply>\n")
    else:
        out.write(
            "adapter: "
            f"model={resolved['adapter_model']!r} "
            f"effort={resolved['adapter_effort']!r} "
            f"turn_timeout_s={resolved['adapter_turn_timeout_s']!r} "
            f"non_progress_warn_s={resolved['adapter_non_progress_warn_s']!r} "
            "non_progress_interrupt_s="
            f"{resolved['adapter_non_progress_interrupt_s']!r} "
            "wrapper_tool_failure_window_s="
            f"{resolved['adapter_wrapper_tool_failure_window_s']!r}\n"
        )
        out.write(f"source : {source}\n")
    return 0


def _build_team_prune_dead_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="claude-anyteam team-prune-dead",
        description=(
            "Remove team members whose backing tmux pane is gone (host crash, "
            "tmux kill-server, process panic). Operates only when tmux is "
            "running — refuses to prune blindly when liveness can't be "
            "checked. The lead is responsible for confirming with --yes; "
            "without it, the command prints what would be pruned and exits."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--team", type=_validate_team_name, required=True, help="Team name")
    p.add_argument(
        "--yes",
        action="store_true",
        help="Actually rewrite config.json (omit for dry-run preview).",
    )
    return p


def _team_prune_dead_command(argv: list[str], *, stdout: TextIO | None = None, stderr: TextIO | None = None) -> int:
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    args = _build_team_prune_dead_parser().parse_args(argv)

    cfg_path = team_config_path(args.team)
    if not cfg_path.exists():
        err.write(f"error: no team config at {cfg_path}\n")
        return 1
    live_pane_ids = _live_tmux_pane_ids()
    if live_pane_ids is None:
        err.write(
            "error: cannot reach tmux to verify pane liveness; refusing to "
            "prune. (Is tmux installed and running? If you intentionally "
            "killed the server and want to prune all tmux-backed members, "
            "edit ~/.claude/teams/<team>/config.json by hand.)\n"
        )
        return 1

    with config_lock(cfg_path.parent):
        cfg = _existing_dict(cfg_path)
        members = cfg.get("members") if isinstance(cfg, dict) else None
        if not isinstance(members, list):
            err.write(f"error: team config has no `members` array\n")
            return 1

        kept: list[Any] = []
        pruned: list[str] = []
        for m in members:
            if not isinstance(m, dict):
                kept.append(m)
                continue
            pane = str(m.get("tmuxPaneId", ""))
            if pane and pane != "in-process" and pane not in live_pane_ids:
                pruned.append(str(m.get("name", "?")))
                continue
            kept.append(m)

        if not pruned:
            out.write(f"no dead members in team {args.team!r}\n")
            return 0

        if not args.yes:
            out.write(f"would prune {len(pruned)} dead member(s) from team {args.team!r}:\n")
            for name in pruned:
                out.write(f"  - {name}\n")
            out.write("re-run with --yes to apply.\n")
            return 0

        cfg["members"] = kept
        _write_atomic_json(cfg_path, cfg)
    out.write(f"pruned {len(pruned)} dead member(s) from team {args.team!r}: {', '.join(pruned)}\n")
    return 0


_SUBCOMMANDS = {
    "team-agent": _team_agent_command,
    "team-patch": _team_patch_command,
    "team-roster": _team_roster_command,
    "team-config": _team_config_command,
    "team-prune-dead": _team_prune_dead_command,
}


def main_dispatch(subcommand: str, argv: list[str]) -> int:
    """Dispatch a ``team-*`` subcommand by name.

    Called from ``claude_anyteam.cli.main`` when the first positional arg
    matches one of the team-* subcommands.
    """
    handler = _SUBCOMMANDS.get(subcommand)
    if handler is None:
        sys.stderr.write(f"error: unknown subcommand {subcommand!r}\n")
        return 2
    return handler(argv)


def main(argv: list[str] | None = None) -> int:
    """Standalone entry point used by ``team-agent``-only invocations."""
    return _team_agent_command(list(argv) if argv is not None else sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
