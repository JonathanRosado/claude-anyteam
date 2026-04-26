"""Team-management CLI helpers.

Three subcommands of ``claude-anyteam``:

  * ``team-agent``  — write per-teammate ``model``/``effort`` overrides at
                      ``~/.claude/teams/<team>/agents/<agent>.json``. The spawn
                      shim reads this file and forwards the values to the
                      routed adapter (Codex, Gemini, Kimi).

  * ``team-patch``  — apply post-spawn fixups to a teammate row in
                      ``~/.claude/teams/<team>/config.json``. Today this
                      means setting ``agentType`` to ``claude-anyteam`` so
                      the wrapper MCP validation passes; the Agent tool
                      omits this field when spawning external-LLM teammates.

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
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

# Effort whitelist mirrors the per-backend allowlists (Codex/Gemini/Kimi all
# accept the same five-tier scale). If a future backend diverges, this
# becomes the union and the per-backend adapter remains the source of truth.
EFFORT_CHOICES = ("minimal", "low", "medium", "high", "xhigh")

# Whitelisted keys mirror spawn_shim._AGENT_CONFIG_KEYS; expanding this set
# requires extending the shim too.
AGENT_CONFIG_KEYS = ("model", "effort")

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
            "this file and forwards --model/--effort to the routed adapter."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  claude-anyteam team-agent codex-alice --team build --model gpt-5.5 --effort xhigh\n"
            "  claude-anyteam team-agent gemini-bob  --team build --model gemini-3.1-pro-preview --effort high\n"
            "  claude-anyteam team-agent kimi-cara   --team build --model kimi-for-coding\n"
            "  claude-anyteam team-agent codex-alice --team build --remove\n"
            "\nThe agent name's prefix (codex-/gemini-/kimi-) determines which adapter receives the\n"
            "config; this command does not validate the model slug against any backend catalog."
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

    if args.model is None and args.effort is None:
        err.write(
            "error: at least one of --model/--effort must be provided (or use --remove to delete)\n"
        )
        return 2

    config = _existing_dict(path)
    config = {k: v for k, v in config.items() if k in AGENT_CONFIG_KEYS}

    if args.model is not None:
        config["model"] = args.model
    if args.effort is not None:
        config["effort"] = args.effort

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
            "patches that field idempotently."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  claude-anyteam team-patch codex-alice --team build\n"
            "  claude-anyteam team-patch --team build --all-external\n"
            "\nWith --all-external the command patches every member whose name starts with\n"
            "codex-, gemini-, or kimi- (the routed-adapter prefixes)."
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


def _build_team_roster_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="claude-anyteam team-roster",
        description=(
            "Print a one-line-per-member summary of the team's config.json. "
            "Use this instead of reading and parsing the file by hand."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--team", type=_validate_team_name, required=True, help="Team name")
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit a JSON array instead of the human-readable table",
    )
    return p


def _roster_rows(cfg: dict[str, object]) -> list[_RosterRow]:
    members = cfg.get("members")
    if not isinstance(members, list):
        return []
    rows: list[_RosterRow] = []
    for m in members:
        if not isinstance(m, dict):
            continue
        rows.append(
            _RosterRow(
                name=str(m.get("name", "?")),
                agent_type=str(m.get("agentType", "?")),
                model=str(m.get("model", "?")),
                backend_type=str(m.get("backendType", "?")),
                color=str(m.get("color", "?")),
            )
        )
    return rows


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
    rows = _roster_rows(cfg)

    if args.json:
        out.write(json.dumps([row.__dict__ for row in rows], indent=2, sort_keys=True) + "\n")
        return 0

    if not rows:
        out.write(f"team {args.team!r} has no members\n")
        return 0

    name_w = max(len(r.name) for r in rows)
    type_w = max(len(r.agent_type) for r in rows)
    model_w = max(len(r.model) for r in rows)
    for r in rows:
        out.write(
            f"  {r.name:<{name_w}}  type={r.agent_type:<{type_w}}  model={r.model:<{model_w}}  "
            f"backend={r.backend_type}  color={r.color}\n"
        )
    return 0


# --------------------------------------------------------------------------- #
# Dispatch
# --------------------------------------------------------------------------- #


_SUBCOMMANDS = {
    "team-agent": _team_agent_command,
    "team-patch": _team_patch_command,
    "team-roster": _team_roster_command,
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
