"""Console entry point for the adapter.

Installed as `claude-anyteam` via pyproject.toml [project.scripts]. Supports
running the adapter plus one-time install/uninstall helpers for Claude Code's
persistent settings file.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TextIO

from . import logger
from .config import from_env
from .installer import (
    InstallError,
    format_uninstall_message,
    install as install_settings,
    uninstall as uninstall_settings,
)
from .loop import run


def _build_run_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="claude-anyteam",
        description="Route Codex-powered teammates into Claude Code with the claude-anyteam adapter.",
        epilog=(
            "Management commands:\n"
            "  claude-anyteam install    Persist the claude-anyteam shim in ~/.claude/settings.json\n"
            "  claude-anyteam uninstall  Remove the installed Claude teammate shim settings"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--team", help="Team name (overrides CLAUDE_ANYTEAM_TEAM)")
    p.add_argument("--name", help="Teammate name within the team (overrides CLAUDE_ANYTEAM_NAME)")
    p.add_argument("--cwd", help="Working directory for Codex invocations")
    p.add_argument("--poll-s", type=float, help="Inbox poll interval in seconds")
    p.add_argument("--color", help="Display color (default: cyan)")
    p.add_argument(
        "--plan-mode",
        action="store_true",
        help="Register with planModeRequired=true (opt-in path).",
    )
    p.add_argument("--codex-binary", help="Codex CLI binary name (default: codex)")
    # v7.1 default (task #21): App Server mode is ON unless explicitly
    # disabled via --no-app-server. Rationale: mid-task reactivity is a
    # v7.1 signature capability and users shouldn't need to opt into it;
    # the opt-out exists for v7.2-style session-memory scenarios, since
    # cross-task memory via `codex exec resume` is a documented non-goal
    # under App Server (see docs/v7.2-notes.md §6 and §9).
    #
    # argparse.BooleanOptionalAction auto-generates `--app-server` and
    # `--no-app-server` from a single declaration.
    p.add_argument(
        "--app-server",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Invoke Codex via `codex app-server` (default: on; "
            "pass --no-app-server to use the legacy `codex exec` path "
            "with v7.2 session-memory support)."
        ),
    )
    p.add_argument(
        "--model",
        help=(
            "Codex model slug (e.g. gpt-5.4, gpt-5.3-codex). "
            "Overrides CLAUDE_ANYTEAM_MODEL; when unset, Codex's "
            "~/.codex/config.toml default applies."
        ),
    )
    p.add_argument(
        "--effort",
        choices=["low", "medium", "high", "xhigh"],
        help=(
            "Reasoning effort for Codex. Overrides CLAUDE_ANYTEAM_EFFORT; "
            "when unset, Codex's per-model default applies."
        ),
    )
    return p


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return _build_run_parser().parse_args(argv)


def _build_install_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="claude-anyteam install",
        description=(
            "Persist the claude-anyteam spawn shim in ~/.claude/settings.json so "
            "Claude Code can launch it in future sessions."
        ),
    )
    p.add_argument(
        "--settings-path",
        default=None,
        help=argparse.SUPPRESS,
    )
    return p


def _parse_install_args(argv: list[str] | None = None) -> argparse.Namespace:
    return _build_install_parser().parse_args(argv)


def _build_uninstall_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="claude-anyteam uninstall",
        description="Remove claude-anyteam-managed entries from ~/.claude/settings.json.",
    )
    p.add_argument(
        "--settings-path",
        default=None,
        help=argparse.SUPPRESS,
    )
    return p


def _parse_uninstall_args(argv: list[str] | None = None) -> argparse.Namespace:
    return _build_uninstall_parser().parse_args(argv)


def _install_command(
    *,
    settings_path: Path | str | None = None,
    current_executable: str | None = None,
    out: TextIO | None = None,
) -> int:
    stream = out or sys.stdout
    try:
        result = install_settings(
            settings_path=settings_path,
            argv0=current_executable or sys.argv[0],
        )
    except InstallError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    stream.write(f"Updated {result.paths.settings_path}\n")
    stream.write(
        f"Set env.CLAUDE_CODE_TEAMMATE_COMMAND={result.paths.shim_path}\n"
    )
    stream.write(
        f"Set env.CLAUDE_ANYTEAM_BINARY={result.paths.binary_path}\n"
    )
    stream.write("Restart Claude Code for the changes to take effect.\n")
    return 0


def _uninstall_command(
    *,
    settings_path: Path | str | None = None,
    out: TextIO | None = None,
) -> int:
    stream = out or sys.stdout
    try:
        result = uninstall_settings(settings_path=settings_path)
    except InstallError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if result.removed:
        print(format_uninstall_message(result), file=stream)
        return 0

    print(format_uninstall_message(result), file=stream)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(argv) if argv is not None else sys.argv[1:]

    if argv:
        command = argv[0]
        if command == "install":
            args = _parse_install_args(argv[1:])
            kwargs: dict[str, object] = {}
            if args.settings_path is not None:
                kwargs["settings_path"] = args.settings_path
            return _install_command(**kwargs)
        if command == "uninstall":
            args = _parse_uninstall_args(argv[1:])
            kwargs: dict[str, object] = {}
            if args.settings_path is not None:
                kwargs["settings_path"] = args.settings_path
            return _uninstall_command(**kwargs)

    args = _parse_args(argv)

    overrides: dict[str, object] = {}
    if args.team:
        overrides["team_name"] = args.team
    if args.name:
        overrides["agent_name"] = args.name
    if args.cwd:
        overrides["cwd"] = args.cwd
    if args.poll_s is not None:
        overrides["poll_interval_s"] = args.poll_s
    if args.color:
        overrides["color"] = args.color
    if args.plan_mode:
        overrides["plan_mode_required"] = "true"
    if args.codex_binary:
        overrides["codex_binary"] = args.codex_binary
    # args.app_server is None when the flag isn't passed → fall through to
    # env/default. Only override when the user explicitly said --app-server
    # or --no-app-server on the command line.
    if args.app_server is not None:
        overrides["app_server"] = "true" if args.app_server else "false"
    if args.model:
        overrides["model"] = args.model
    if args.effort:
        overrides["effort"] = args.effort

    try:
        settings = from_env(overrides=overrides)
    except ValueError as e:
        logger.error("startup.config_error", error=str(e))
        return 2

    logger.info(
        "startup",
        team=settings.team_name,
        name=settings.agent_name,
        cwd=str(settings.cwd),
        plan_mode=settings.plan_mode_required,
        app_server=settings.app_server,
        model=settings.model,
        effort=settings.effort,
    )
    return run(settings)


if __name__ == "__main__":
    sys.exit(main())
