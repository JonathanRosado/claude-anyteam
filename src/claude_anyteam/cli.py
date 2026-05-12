"""Console entry point for the adapter.

Installed as `claude-anyteam` via pyproject.toml [project.scripts]. Supports
running the adapter plus one-time install/uninstall helpers for Claude Code's
persistent settings file.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import os
import re
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TextIO
from urllib.parse import urlencode

from . import logger
from ._debug import (
    debug_enabled,
    force_enable as _debug_force_enable,
    log as _dlog,
    log_env_snapshot as _dlog_env,
    log_subprocess as _dlog_sub,
    log_which as _dlog_which,
)
from ._theme import get_theme, render_box
from .config import from_env
from claude_anyteam import installer as installer_mod
from claude_anyteam.installer import (
    INSTALL_ERROR_EXIT_NO_PROVIDER,
    InstallError,
    format_install_message,
    format_uninstall_message,
    install as install_settings,
    uninstall as uninstall_settings,
)
from .loop import run

ISSUE_NEW_URL = "https://github.com/JonathanRosado/claude-anyteam/issues/new"


def _bold(text: str) -> str:
    return f"\033[1m{text}\033[0m"


def _strip_ansi(text: str) -> str:
    # Tiny local stripper: enough for our own bold escape.
    return text.replace("\033[1m", "").replace("\033[0m", "")


def _render_box(title: str, lines: list[str]) -> str:
    rows = [title, *lines]
    width = max((len(_strip_ansi(row)) for row in rows), default=0)

    def fill(row: str) -> str:
        return row + " " * (width - len(_strip_ansi(row)))

    return "\n".join(
        [
            f"╭{'─' * (width + 2)}╮",
            f"│ {fill(rows[0])} │",
            f"├{'─' * (width + 2)}┤",
            *(f"│ {fill(row)} │" for row in rows[1:]),
            f"╰{'─' * (width + 2)}╯",
        ]
    )


def _python_tool_version() -> str:
    """The actually-installed Python package version (NOT the npm wrapper's
    claim). Used for version-mismatch diagnostics."""
    try:
        return importlib.metadata.version("claude-anyteam")
    except importlib.metadata.PackageNotFoundError:
        pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
        try:
            for line in pyproject.read_text(encoding="utf-8").splitlines():
                if line.startswith("version = "):
                    return line.split("=", 1)[1].strip().strip('"')
        except OSError:
            pass
    return "unknown"


def _installer_version() -> str:
    env_version = os.environ.get("CLAUDE_ANYTEAM_NPM_VERSION")
    if env_version:
        return env_version
    return _python_tool_version()


def _version_banner() -> str:
    return f"claude-anyteam installer v{_installer_version()}"


def _detect_version_mismatch() -> str | None:
    """If the npm wrapper's reported version differs from the installed Python
    tool's version, return a warning string. Caller prints it loudly so the
    user knows the wrapper bug-fixes won't actually run.
    """
    npm_claim = os.environ.get("CLAUDE_ANYTEAM_NPM_VERSION")
    if not npm_claim:
        return None
    actual = _python_tool_version()
    if actual == "unknown" or actual == npm_claim:
        return None
    return (
        f"npm wrapper is v{npm_claim} but the installed Python tool is v{actual}. "
        f"Wrapper-version bug fixes won't run until the Python tool refreshes. "
        f"Try: uv tool install --force --prerelease=allow claude-anyteam=={npm_claim}"
    )


def _command_version(command: str) -> str:
    path = shutil.which(command)
    if not path:
        return "not found"
    try:
        completed = subprocess.run(
            [path, "--version"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"found at {path}, but version check failed: {exc}"
    return (completed.stdout or completed.stderr or "").strip() or f"found at {path}"


def _issue_url(*, title: str, raw_error: str) -> str:
    return installer_mod.build_issue_url(title=title, raw_error=raw_error)


def _format_install_error(exc: InstallError) -> str:
    lines: list[str] = [_bold(exc.title)]
    lines.extend(exc.explanation.splitlines() or [""])
    raw_error = exc.as_plain_text(include_details=True, include_report=False)
    lines.extend(
        installer_mod.format_installer_diagnostic(
            summary=exc.explanation,
            raw_error=raw_error,
            next_steps_per_os=exc.action,
            title=exc.title,
            include_what_happened=False,
        )
    )
    lines.append(f"Severity: {exc.severity}")
    if exc.details:
        lines.append("Raw details (for debugging):")
        lines.extend(f"  {line}" for line in exc.details.splitlines())
    return _render_box("INSTALL FAILED", lines)


def _build_run_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="claude-anyteam",
        description="Route Codex- and Gemini-backed teammates into Claude Code with the claude-anyteam adapter.",
        epilog=(
            "Management commands:\n"
            "  claude-anyteam install      Persist the claude-anyteam shim in ~/.claude/settings.json\n"
            "  claude-anyteam uninstall    Remove the installed Codex/Gemini teammate shim settings\n"
            "  claude-anyteam team-agent   Write per-teammate model/effort/watchdog/turn-timeout to ~/.claude/teams/<team>/agents/<agent>.json\n"
            "  claude-anyteam team-patch   Patch agentType post-spawn so wrapper MCP validation passes\n"
            "  claude-anyteam team-roster  Print one-line-per-member team summary (flags ghosts and dead-pane members)\n"
            "  claude-anyteam team-config  Print resolved spawn-time config for a teammate (host model + adapter overrides)\n"
            "  claude-anyteam team-prune-dead  Remove members whose backing tmux pane is gone (use --yes to apply)\n"
            "  claude-anyteam diagnose     Inspect substrate state: roster, manifests, visibility, wrapper MCP diagnostics\n"
            "  claude-anyteam status       One-screen team snapshot — roster, adapter overrides, incidents, last activity\n"
            "  claude-anyteam visibility-tail  Follow the live VisibilityEvent JSONL stream"
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
            "Codex model slug (e.g. gpt-5.5, gpt-5.4, gpt-5.3-codex). "
            "Overrides CLAUDE_ANYTEAM_MODEL; when unset, Codex's "
            "~/.codex/config.toml default applies. "
            "See docs/configuration.md for the current model catalog."
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
    p.add_argument(
        "--turn-timeout-s",
        type=float,
        help=(
            "Wall-clock cap (seconds) on a single Codex App Server turn. "
            "Range [60, 3600], default 1800 (bumped from 900 in task #5). "
            "Overrides CLAUDE_ANYTEAM_TURN_TIMEOUT_S. Raise this for "
            "teammates that run long pytest/build invocations; tighten "
            "for short-loop executor roles. Currently affects Codex App "
            "Server only — Gemini and Kimi have their own subprocess-"
            "level timeouts."
        ),
    )
    p.add_argument(
        "--non-progress-warn-s",
        type=float,
        help=(
            "Codex App Server-only soft non-progress watchdog threshold in "
            "seconds. Range [60, 1800], default unset (opt-in as of task #5; "
            "see docs/design/timers-vs-visibility.md). Overrides "
            "CLAUDE_ANYTEAM_NON_PROGRESS_WARN_S. When set and tripped, the "
            "adapter emits a turn_progress warning envelope and sends one "
            "checkpoint turn/steer; it does not interrupt the turn."
        ),
    )
    p.add_argument(
        "--non-progress-interrupt-s",
        type=float,
        help=(
            "Codex App Server-only opt-in hard non-progress interrupt "
            "threshold in seconds. Default unset/disabled; overrides "
            "CLAUDE_ANYTEAM_NON_PROGRESS_INTERRUPT_S. Only considered after "
            "the soft watchdog has fired and no later visible checkpoint was "
            "observed."
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
            "Claude Code can launch it in future sessions, and set "
            "teammateMode=\"tmux\" in ~/.claude.json so teammates route through "
            "the pane backend."
        ),
    )
    p.add_argument(
        "--assume-yes",
        "-y",
        action="store_true",
        help="auto-accept prompts (needed for scripted installs)",
    )
    p.add_argument(
        "--force-empty",
        action="store_true",
        help="install without any provider ready (CI / set up later)",
    )
    p.add_argument(
        "--no-input",
        action="store_true",
        help="fail instead of prompting; use in CI",
    )
    p.add_argument(
        "--no-allowlist",
        action="store_true",
        help="skip writing recommended permission allowlist entries",
    )
    p.add_argument(
        "--debug",
        action="store_true",
        help="verbose logging to stderr (also: env CLAUDE_ANYTEAM_DEBUG=1)",
    )
    p.add_argument(
        "--self-heal",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    p.add_argument(
        "--settings-path",
        default=None,
        help=argparse.SUPPRESS,
    )
    p.add_argument(
        "--claude-json-path",
        default=None,
        help=argparse.SUPPRESS,
    )
    p.add_argument(
        "--state-path",
        default=None,
        help=argparse.SUPPRESS,
    )
    return p


def _parse_install_args(argv: list[str] | None = None) -> argparse.Namespace:
    return _build_install_parser().parse_args(argv)


def _build_uninstall_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="claude-anyteam uninstall",
        description="Remove claude-anyteam-managed entries from ~/.claude/settings.json and revert teammateMode.",
    )
    p.add_argument(
        "--settings-path",
        default=None,
        help=argparse.SUPPRESS,
    )
    p.add_argument(
        "--claude-json-path",
        default=None,
        help=argparse.SUPPRESS,
    )
    p.add_argument(
        "--state-path",
        default=None,
        help=argparse.SUPPRESS,
    )
    return p


def _parse_uninstall_args(argv: list[str] | None = None) -> argparse.Namespace:
    return _build_uninstall_parser().parse_args(argv)


def _interactive_prompt(current_value: str) -> bool:
    """Asks the user whether to overwrite an existing teammateMode value.

    Returns False on decline, anything-not-explicitly-yes, or if stdin is not
    a TTY (scripted install without --assume-yes: fail loudly rather than hang).
    """
    if not sys.stdin.isatty():
        return False
    try:
        answer = input(
            f"claude-anyteam wants to set teammateMode=\"tmux\" in ~/.claude.json. "
            f"Current value: {current_value!r}. Overwrite? [y/N] "
        )
    except EOFError:
        return False
    return answer.strip().lower() in {"y", "yes"}


def _yes_default_prompt(question: str) -> bool | None:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return None
    theme = get_theme()
    prompt = f"{theme.symbols['info']} {theme.heading(question)} {theme.muted('[Y/n]')} "
    try:
        answer = input(prompt)
    except EOFError:
        return None
    return answer.strip().lower() not in {"n", "no"}


_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _strip_ansi(value: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", value or "")


def _no_color_subprocess_env() -> dict:
    """Force uv (and any other ANSI-aware subprocess) to NOT emit color codes.

    We set FORCE_COLOR=1 in the npm wrapper for the Python theme — but that
    same env var also makes uv color its `tool dir` output, which corrupts
    our parsing. This helper builds a child env that explicitly disables
    color, used for subprocess calls whose stdout we parse.
    """
    env = dict(os.environ)
    env.pop("FORCE_COLOR", None)
    env.pop("CLAUDE_ANYTEAM_FORCE_COLOR", None)
    env["NO_COLOR"] = "1"
    return env


def _uv_tool_bin_dirs() -> list[str]:
    """Return ALL uv tool bin candidates so post-install checks can find a
    freshly-installed binary even when the calling shell's PATH is stale.

    Belt-and-suspenders: query both `uv tool dir --bin` (current uv API) and
    walk `uv tool dir`/<tool>/Scripts (where the venv-local binary lives on
    Windows, since uv only writes the shim to the bin dir lazily). Strips
    ANSI escapes from uv stdout because uv colors its output when FORCE_COLOR
    is set in the env (which the npm wrapper does for our own theming).
    """
    uv = shutil.which("uv")
    _dlog_which("uv", uv)
    if not uv:
        return []
    no_color_env = _no_color_subprocess_env()
    candidates: list[str] = []
    try:
        completed = subprocess.run(
            [uv, "tool", "dir", "--bin"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env=no_color_env,
        )
        _dlog_sub([uv, "tool", "dir", "--bin"], completed, label="uv-tool-dir-bin")
        if completed.returncode == 0 and completed.stdout:
            for line in completed.stdout.splitlines():
                line = _strip_ansi(line).strip()
                if line and os.path.isdir(line):
                    candidates.append(line)
                elif line:
                    _dlog(f"  uv-tool-dir-bin line {line!r} is not a directory")
    except (OSError, subprocess.SubprocessError) as exc:
        _dlog(f"uv tool dir --bin failed: {type(exc).__name__}: {exc}")
    try:
        completed = subprocess.run(
            [uv, "tool", "dir"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env=no_color_env,
        )
        _dlog_sub([uv, "tool", "dir"], completed, label="uv-tool-dir")
        if completed.returncode == 0 and completed.stdout:
            for line in completed.stdout.splitlines():
                root = _strip_ansi(line).strip()
                if not root or not os.path.isdir(root):
                    continue
                # Scan tools/<name>/Scripts (Windows) and tools/<name>/bin (POSIX).
                try:
                    for entry in os.listdir(root):
                        for sub in ("Scripts", "bin"):
                            candidate = os.path.join(root, entry, sub)
                            if os.path.isdir(candidate):
                                candidates.append(candidate)
                except OSError as exc:
                    _dlog(f"listdir({root!r}) failed: {exc}")
                    continue
    except (OSError, subprocess.SubprocessError) as exc:
        _dlog(f"uv tool dir failed: {type(exc).__name__}: {exc}")
    # De-duplicate while preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            out.append(c)
    _dlog(f"_uv_tool_bin_dirs() -> {out}")
    return out


def _refresh_path_for_uv_tools() -> None:
    """After `uv tool install`, the new binary lives in `uv tool dir --bin`
    AND inside the per-tool Scripts/bin directory. On Windows the bin-dir
    shim is sometimes written with a small delay; the per-tool path is the
    most reliable. Prepend ALL discovered uv tool dirs to os.environ['PATH']
    so subsequent shutil.which() probes succeed in the same Python process.
    """
    candidates = _uv_tool_bin_dirs()
    if not candidates:
        return
    current = os.environ.get("PATH", "")
    parts = current.split(os.pathsep) if current else []
    new_parts = [c for c in candidates if c not in parts]
    if new_parts:
        # Bug-fix from codex review: previous form `os.pathsep + current if not current ...`
        # silently dropped new_parts when current was empty. Use a single join path.
        os.environ["PATH"] = os.pathsep.join([*new_parts, *parts])


def _windows_executable_extensions() -> tuple[str, ...]:
    """Honor PATHEXT (with .EXE fallback) when probing files on Windows."""
    pathext = os.environ.get("PATHEXT", "")
    exts = [ext for ext in pathext.split(os.pathsep) if ext.strip()]
    if not exts:
        exts = [".COM", ".EXE", ".BAT", ".CMD"]
    # Lowercase + ensure .exe/.cmd/.bat are present even if PATHEXT is bizarre.
    seen: list[str] = []
    for ext in [*[e.lower() for e in exts], ".exe", ".cmd", ".bat"]:
        if ext not in seen:
            seen.append(ext)
    return tuple(seen)


def _resolve_binary(
    names: tuple[str, ...] | str,
    *,
    uv_tool_name: str | None = None,
    update_path: bool = True,
) -> str | None:
    _dlog(f"_resolve_binary names={names!r} uv_tool_name={uv_tool_name!r}")
    """Single shared resolver used by BOTH post-install success checks AND
    provider-status checks so they cannot disagree about whether a binary
    exists.

    Tries each alias in order:
      1. shutil.which(alias) — exact PATH lookup
      2. <uv tool dir --bin>/<alias>{.exe,.cmd,.bat,...}
      3. <uv tool dir>/<uv_tool_name>/Scripts|bin/<alias>{ext}
      4. Walk every (uv tool dir)/<entry>/Scripts|bin to catch installs that
         landed in a tool dir that doesn't match `uv_tool_name`.

    On success, prepends the containing directory to os.environ["PATH"] so
    later shutil.which() calls in the same process succeed. Returns the
    absolute path to the found binary, or None.
    """
    if isinstance(names, str):
        names = (names,)
    extensions = _windows_executable_extensions() if sys.platform == "win32" else ("",)

    def _record_path(parent: str) -> None:
        if not update_path:
            return
        current = os.environ.get("PATH", "")
        parts = current.split(os.pathsep) if current else []
        if parent not in parts:
            os.environ["PATH"] = os.pathsep.join([parent, *parts])

    for alias in names:
        hit = shutil.which(alias)
        _dlog_which(alias, hit)
        if hit:
            _record_path(os.path.dirname(hit))
            return hit

    bin_dirs: list[str] = list(_uv_tool_bin_dirs())
    # Promote uv_tool_name-specific dirs to the front for a faster first hit.
    if uv_tool_name:
        uv = shutil.which("uv")
        if uv:
            try:
                completed = subprocess.run(
                    [uv, "tool", "dir"],
                    capture_output=True, text=True, timeout=10, check=False,
                )
                if completed.returncode == 0 and completed.stdout:
                    for root in (line.strip() for line in completed.stdout.splitlines()):
                        if not root or not os.path.isdir(root):
                            continue
                        for sub in ("Scripts", "bin"):
                            cand = os.path.join(root, uv_tool_name, sub)
                            if os.path.isdir(cand) and cand not in bin_dirs:
                                bin_dirs.insert(0, cand)
            except (OSError, subprocess.SubprocessError):
                pass

    _dlog(f"  scanning {len(bin_dirs)} bin dirs with extensions={extensions!r}")
    for bin_dir in bin_dirs:
        for alias in names:
            for ext in extensions:
                cand = os.path.join(bin_dir, f"{alias}{ext}")
                exists = os.path.isfile(cand)
                _dlog(f"    probe: {cand} -> isfile={exists}")
                if exists:
                    _record_path(bin_dir)
                    return cand
    _dlog(f"_resolve_binary({names!r}) -> None (exhausted all probes)")
    return None


def _wait_for_binary(
    names: tuple[str, ...] | str,
    *,
    uv_tool_name: str | None = None,
    attempts: int = 6,
    delay_s: float = 0.25,
) -> str | None:
    """Retry-loop wrapper around _resolve_binary. Used after `uv tool install`
    because Windows file-system + uv shim writes can lag a moment.
    """
    import time
    for _ in range(attempts):
        hit = _resolve_binary(names, uv_tool_name=uv_tool_name, update_path=True)
        if hit:
            return hit
        time.sleep(delay_s)
        _refresh_path_for_uv_tools()
    # Last attempt with the freshly-refreshed PATH.
    return _resolve_binary(names, uv_tool_name=uv_tool_name, update_path=True)


_UV_PATH_HINT_RE = re.compile(r"`([A-Za-z]:\\[^`]+|/[^`]+)`\s+is not on your PATH", re.IGNORECASE)


def _extract_path_hints_from_uv_output(output: str) -> list[str]:
    """uv prints `<path> is not on your PATH` after `uv tool install`. Parse
    that out so we can prepend the dir to os.environ['PATH'] even if our
    `uv tool dir --bin` query fails or returns something else.
    """
    hits: list[str] = []
    for match in _UV_PATH_HINT_RE.finditer(output or ""):
        path = match.group(1).strip()
        if path and os.path.isdir(path) and path not in hits:
            hits.append(path)
    return hits


def _run_install_command(args: list[str], *, timeout_s: int = 300) -> tuple[bool, str]:
    _dlog(f"install command starting: {args!r}")
    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        _dlog_sub(args, completed, label="install-command")
    except (OSError, subprocess.SubprocessError) as exc:
        _dlog(f"install command exception: {type(exc).__name__}: {exc}")
        return False, f"{type(exc).__name__}: {exc}"
    raw_stdout = _strip_ansi(completed.stdout or "").strip()
    raw_stderr = _strip_ansi(completed.stderr or "").strip()
    output = "\n".join(part for part in (raw_stdout, raw_stderr) if part)
    ok = completed.returncode == 0
    if ok and len(args) >= 3 and "tool" in args and "install" in args:
        # uv tool install — refresh PATH so subsequent shutil.which() finds the
        # freshly-installed binary in the same Python process.
        _refresh_path_for_uv_tools()
        # Also parse uv's "is not on your PATH" warning and add that dir too.
        # When uv installs to a bin dir not on PATH, it tells the user — we
        # can use the same hint to fix our own process's PATH.
        for hint in _extract_path_hints_from_uv_output(raw_stdout + "\n" + raw_stderr):
            current = os.environ.get("PATH", "")
            parts = current.split(os.pathsep) if current else []
            if hint not in parts:
                _dlog(f"prepending uv-hint dir to PATH: {hint}")
                os.environ["PATH"] = hint + os.pathsep + current
    return ok, output or f"exit code {completed.returncode}"


def _provider_install_command(provider_key: str) -> tuple[str, list[str]] | None:
    if provider_key == "codex":
        npm = shutil.which("npm")
        if npm:
            return installer_mod.CODEX_CLI_INSTALL_COMMAND, [npm, "install", "-g", "@openai/codex"]
        return None
    if provider_key == "gemini":
        npm = shutil.which("npm")
        if npm:
            return installer_mod.GEMINI_CLI_INSTALL_COMMAND, [npm, "install", "-g", "@google/gemini-cli"]
        return None
    if provider_key == "kimi":
        uv = shutil.which("uv")
        if uv:
            return installer_mod.KIMI_CLI_INSTALL_COMMAND, [uv, "tool", "install", "--python", "3.13", "kimi-cli"]
        if sys.platform != "win32" and shutil.which("sh") and shutil.which("curl"):
            return installer_mod.KIMI_CLI_CURL_INSTALL_COMMAND, ["sh", "-lc", installer_mod.KIMI_CLI_CURL_INSTALL_COMMAND]
    return None


def _manual_provider_steps(provider_key: str) -> list[str]:
    if provider_key == "codex":
        return [
            f"Install Node.js/npm if needed: https://nodejs.org/",
            f"Install Codex: {installer_mod.CODEX_CLI_INSTALL_COMMAND}",
            f"Sign in: {installer_mod.CODEX_CLI_BINARY} login",
            f"Guide: {installer_mod.CODEX_CLI_DOCS_URL}",
        ]
    if provider_key == "gemini":
        return [
            f"Install Node.js/npm if needed: https://nodejs.org/",
            f"Install Gemini: {installer_mod.GEMINI_CLI_INSTALL_COMMAND}",
            f"Sign in: {installer_mod.GEMINI_CLI_BINARY} login (or set GEMINI_API_KEY/Vertex)",
            f"Guide: {installer_mod.GEMINI_CLI_DOCS_URL}",
        ]
    return [
        f"Install Kimi: {installer_mod.KIMI_CLI_CURL_INSTALL_COMMAND}",
        f"Or with uv: {installer_mod.KIMI_CLI_INSTALL_COMMAND}",
        f"Sign in: {installer_mod.KIMI_CLI_BINARY} login",
        f"Guide: {installer_mod.KIMI_CLI_DOCS_URL}",
    ]


def _multiplexer_binary_for_platform() -> str:
    """Linux/macOS use tmux; Windows uses psmux (winget/scoop/choco)."""
    return "psmux" if sys.platform == "win32" else "tmux"


def _multiplexer_install_command() -> tuple[str, list[str]] | None:
    _dlog(f"_multiplexer_install_command for sys.platform={sys.platform!r}")
    if sys.platform == "win32":
        # Windows multiplexer story: psmux from winget (preferred), scoop, or choco.
        # winget search by name (not --id) — the exact ID changes between
        # publishers and `winget install psmux` matches the canonical Microsoft
        # docs. Falls back to scoop / choco / manual instructions.
        for pm, args in (
            ("winget", ["winget", "install", "psmux", "--accept-package-agreements", "--accept-source-agreements", "--silent"]),
            ("scoop", ["scoop", "install", "psmux"]),
            ("choco", ["choco", "install", "psmux", "-y"]),
        ):
            pm_path = shutil.which(pm)
            _dlog_which(pm, pm_path)
            if pm_path:
                return " ".join(args), args
        return None
    if sys.platform == "darwin":
        brew = shutil.which("brew")
        _dlog_which("brew", brew)
        if brew:
            return "brew install tmux", [brew, "install", "tmux"]
        return None
    if sys.platform.startswith("linux"):
        for pm, install_args in (
            ("apt-get", ["sudo", "apt-get", "install", "-y", "tmux"]),
            ("dnf", ["sudo", "dnf", "install", "-y", "tmux"]),
            ("pacman", ["sudo", "pacman", "-S", "--noconfirm", "tmux"]),
            ("apk", ["sudo", "apk", "add", "tmux"]),
        ):
            pm_path = shutil.which(pm)
            _dlog_which(pm, pm_path)
            if pm_path:
                return " ".join(install_args), install_args
    return None


def _offer_multiplexer_install(*, no_input: bool, stream: TextIO) -> None:
    """Prompt the user to install the platform's terminal multiplexer when
    missing. tmux on Linux/macOS, psmux on Windows.

    Runs before install_settings()'s prereq check so a successful auto-install
    means the subsequent check finds the multiplexer on PATH and the install
    proceeds. Failure or decline falls through to the existing InstallError
    (Linux/macOS) or manual instructions (Windows).
    """
    multiplexer = _multiplexer_binary_for_platform()
    _dlog(f"_offer_multiplexer_install: platform={sys.platform!r} multiplexer={multiplexer!r}")
    if sys.platform == "win32":
        # Windows-specific: probe for psmux directly. The Python installer's
        # _check_terminal_multiplexer returns found=True on Windows (single-
        # terminal mode is the documented fallback), but psmux unlocks proper
        # pane-based teammate visibility, so OFFER it.
        existing = shutil.which("psmux") or shutil.which("psmux.exe")
        _dlog_which("psmux", existing)
        if existing:
            _dlog(f"psmux already installed at {existing}")
            return
    else:
        check = installer_mod._check_terminal_multiplexer()
        _dlog(f"_check_terminal_multiplexer() -> found={check.found} binary={check.binary} platform={check.platform}")
        if check.found:
            return
    if no_input or not (sys.stdin.isatty() and sys.stdout.isatty()):
        _dlog(f"skipping multiplexer prompt: no_input={no_input} stdin.isatty={sys.stdin.isatty()} stdout.isatty={sys.stdout.isatty()}")
        # Stay silent here; the InstallError later will print full instructions
        # on POSIX. Windows continues silently — single-terminal mode works.
        return
    if sys.platform == "win32":
        question = (
            "We can install psmux (a terminal multiplexer that lets teammates appear in their own panes). "
            "Without it, claude-anyteam still works but uses single-terminal mode. "
            "Want me to try installing psmux for you?"
        )
    else:
        question = (
            "We need tmux (a terminal multiplexer that lets teammates appear in their own panes). "
            "Want me to try installing it for you?"
        )
    answer = _yes_default_prompt(question)
    theme = get_theme()
    multiplexer = _multiplexer_binary_for_platform()
    if answer is None or not answer:
        if answer is False:
            print(f"{theme.symbols['info']} {theme.muted(f'Okay — skipping {multiplexer} auto-install.')}", file=stream)
        return
    command = _multiplexer_install_command()
    if command is None:
        if sys.platform == "win32":
            msg = "No package manager found (winget / scoop / choco) to install psmux automatically."
        else:
            msg = "No package manager found (brew / apt-get / dnf / pacman / apk) to install tmux automatically."
        print(f"{theme.symbols['warn']} {theme.warn(msg)}", file=stream)
        return
    display, argv = command
    print(f"{theme.symbols['arrow']} {theme.heading('Running:')} {theme.accent(display)}", file=stream)
    if argv and argv[0] == "sudo":
        print(f"{theme.symbols['info']} {theme.muted('You may be prompted for your sudo password.')}", file=stream)
    ok, output = _run_install_command(argv)
    if ok:
        print(f"{theme.symbols['success']} {theme.success(f'Installed {multiplexer}.')} {theme.muted('Continuing with claude-anyteam setup.')}", file=stream)
        return
    body = [
        f"{theme.symbols['error']} {theme.heading(f'I tried to install {multiplexer}, but it failed.')}",
        theme.muted("Raw installer output:"),
        *(f"  {theme.muted(line)}" for line in (output.splitlines() or ["No output captured."])),
        "",
        theme.muted("Falling through to manual instructions below."),
    ]
    print(render_box(theme.danger(f"{multiplexer} auto-install failed"), body, "red", theme=theme), file=stream)


def _offer_provider_dependency_installs(*, no_input: bool, stream: TextIO) -> None:
    checks = [
        ("codex", "Codex CLI", installer_mod._check_codex_cli()),
        ("gemini", "Gemini CLI", installer_mod._check_gemini_cli()),
        ("kimi", "Kimi CLI", installer_mod._check_kimi_cli()),
    ]
    missing = [(key, label) for key, label, check in checks if not check.found]
    if not missing:
        return

    theme = get_theme()
    if no_input or not (sys.stdin.isatty() and sys.stdout.isatty()):
        print(
            f"{theme.symbols['info']} {theme.muted('Skipping interactive dependency install (this terminal cannot answer questions); manual steps follow below.')}",
            file=stream,
        )
        return

    for key, label in missing:
        answer = _yes_default_prompt(
            f"We need {label} for {key}-* teammates. Want me to try installing it for you?"
        )
        if answer is None:
            print(f"{theme.symbols['info']} {theme.muted(f'Skipping {label} auto-install; manual steps will be shown below.')}", file=stream)
            continue
        if not answer:
            print(f"{theme.symbols['info']} {theme.muted(f'Okay — skipping {label}. Manual steps will be shown below.')}", file=stream)
            continue
        command = _provider_install_command(key)
        if command is None:
            print(f"{theme.symbols['warn']} {theme.warn(f'No installer tool found for {label}.')} {theme.muted('Try this next:')}", file=stream)
            for idx, step in enumerate(_manual_provider_steps(key), start=1):
                print(f"  {theme.muted(f'{idx}.')} {step}", file=stream)
            print(theme.muted("Still stuck? Report it:"), file=stream)
            print(
                f"  {_issue_url(title=f'{label} installer tool missing', raw_error=f'No installer command found for {label}')}",
                file=stream,
            )
            continue
        display, argv = command
        print(f"{theme.symbols['arrow']} {theme.heading('Running:')} {theme.accent(display)}", file=stream)
        ok, output = _run_install_command(argv)
        # Some packages register multiple console_scripts (e.g. kimi-cli ships
        # both `kimi` and `kimi-cli`). Try every alias so a stale PATH lookup
        # for one name doesn't make us think the install failed.
        alias_map: dict[str, tuple[tuple[str, ...], str | None]] = {
            "codex": ((installer_mod.CODEX_CLI_BINARY,), None),
            "gemini": ((installer_mod.GEMINI_CLI_BINARY,), None),
            "kimi": ((installer_mod.KIMI_CLI_BINARY, "kimi-cli"), "kimi-cli"),
        }
        aliases, uv_tool_name = alias_map.get(key, ((key,), None))
        binary_name = aliases[0] if aliases else key
        binary_found_path = (
            _wait_for_binary(aliases, uv_tool_name=uv_tool_name) if (ok and aliases) else None
        )
        if ok and binary_found_path:
            print(
                f"{theme.symbols['success']} {theme.success(f'Installed {label}.')} {theme.muted(f'Found at {binary_found_path}.')}",
                file=stream,
            )
            print(
                f"  {theme.muted('You may still need to sign in before teammates can use it.')}",
                file=stream,
            )
        elif ok and binary_name and not binary_found_path:
            # Install reported success but the binary still isn't on PATH after
            # multiple retries + direct uv tool dir probes. Be honest about the
            # ambiguity instead of celebrating a false positive.
            warn_body = [
                f"{theme.symbols['warn']} {theme.heading(f'{label} installer reported success, but I cannot find the {binary_name} binary on PATH.')}",
                theme.muted("This often means uv installed the tool but its shim landed in a directory that's not on PATH for the calling shell."),
                "",
                theme.muted("Try this next:"),
                f"  {theme.muted('1.')} Open a new terminal so PATH refreshes.",
                f"  {theme.muted('2.')} Run {theme.accent(binary_name + ' --version')} to verify the install.",
                f"  {theme.muted('3.')} Re-run {theme.accent('npx --yes claude-anyteam')} from the new terminal.",
                "",
                theme.muted("Raw installer output:"),
                *(f"  {theme.muted(line)}" for line in (output.splitlines() or ["No output captured."])),
            ]
            print(render_box(theme.warn(f"{label} install ambiguous"), warn_body, "yellow", theme=theme), file=stream)
        elif ok:
            print(
                f"{theme.symbols['success']} {theme.success(f'Installed {label}.')} {theme.muted('You may still need to sign in before teammates can use it.')}",
                file=stream,
            )
        else:
            issue_url = _issue_url(title=f"{label} auto-install failed", raw_error=output)
            body = [
                f"{theme.symbols['error']} {theme.heading(f'I tried to install {label}, but it failed.')}",
                theme.muted("Raw installer output:"),
                *(f"  {theme.muted(line)}" for line in (output.splitlines() or ["No output captured."])),
                "",
                theme.muted("Try this next:"),
                *(f"  {theme.muted(f'{idx}.')} {step}" for idx, step in enumerate(_manual_provider_steps(key), start=1)),
            ]
            print(render_box(theme.danger(f"{label} auto-install failed"), body, "red", theme=theme), file=stream)
            # URL outside the box so terminals render it as one clickable line
            # instead of word-wrapping the prefilled body into ~17 lines.
            print(
                f"{theme.symbols['info']} {theme.muted('Open in browser to report:')}\n  {theme.accent(issue_url)}",
                file=stream,
            )


def _install_command(
    *,
    settings_path: Path | str | None = None,
    claude_json_path: Path | str | None = None,
    state_path: Path | str | None = None,
    assume_yes: bool = False,
    force_empty: bool = False,
    no_input: bool = False,
    self_heal: bool = False,
    no_allowlist: bool = False,
    current_executable: str | None = None,
    out: TextIO | None = None,
) -> int:
    stream = out or sys.stdout
    if os.environ.get("CLAUDE_ANYTEAM_NPM_PARENT") != "1":
        print(_version_banner(), file=stream)
    if debug_enabled():
        _dlog(f"--- claude-anyteam install start ---")
        _dlog(f"  npm-wrapper version (CLAUDE_ANYTEAM_NPM_VERSION): {os.environ.get('CLAUDE_ANYTEAM_NPM_VERSION', '<unset>')!r}")
        _dlog(f"  python-tool version (importlib.metadata): {_python_tool_version()!r}")
        _dlog(f"  argv: {sys.argv!r}")
        _dlog_env(label="install-start")
    # Loud diagnostic when wrapper claims a newer version than the Python
    # tool actually installed — this used to silently mask v0.7.5 bug-fixes
    # because PyPI's index lagged npm publish and uv resolved an older wheel.
    mismatch = _detect_version_mismatch()
    if mismatch:
        from ._theme import get_theme as _get_theme
        _t = _get_theme()
        print(
            f"{_t.symbols['warn']} {_t.warn('Version mismatch:')} {_t.muted(mismatch)}",
            file=sys.stderr,
        )
    if assume_yes:
        prompt_fn = lambda _current: True
    elif no_input or self_heal:
        # --no-input is a prompt guard for CI. The current provider-ready /
        # refuse-to-install path is non-interactive, but future prompts should
        # route through this branch and fail fast instead of blocking on stdin.
        prompt_fn = lambda _current: False
    else:
        prompt_fn = _interactive_prompt

    # --assume-yes is the npm wrapper's "approve settings.json modifications"
    # signal, NOT a "skip every chance to ask the user about installing a
    # missing dep" signal. TTY detection inside the helpers gates the prompts.
    _offer_multiplexer_install(no_input=no_input or self_heal, stream=stream)
    _offer_provider_dependency_installs(
        no_input=no_input or self_heal,
        stream=stream,
    )

    provider_status_rendered = False

    def _print_provider_status(block: str) -> None:
        nonlocal provider_status_rendered
        print(block, file=stream)
        print("", file=stream)
        provider_status_rendered = True

    try:
        result = install_settings(
            settings_path=settings_path,
            claude_json_path=claude_json_path,
            state_path=state_path,
            argv0=current_executable or sys.argv[0],
            prompt_fn=prompt_fn,
            provider_status_callback=_print_provider_status,
            force_empty=force_empty or self_heal,
            no_allowlist=no_allowlist,
            # Skip plugin registration when invoked from the npm wrapper —
            # setup.js handles `claude plugin install` itself afterward and
            # has better error UX. Letting the Python child also try produces
            # a redundant warning box for the same failure.
            register_plugin=os.environ.get("CLAUDE_ANYTEAM_NPM_PARENT") != "1",
        )
    except InstallError as exc:
        exit_code = getattr(exc, "cli_exit_code", 2)
        print(
            _format_install_error(exc),
            file=stream if exit_code == INSTALL_ERROR_EXIT_NO_PROVIDER else sys.stderr,
        )
        return exit_code

    print(
        format_install_message(result, include_provider_status=not provider_status_rendered),
        file=stream,
    )
    return 0


def _uninstall_command(
    *,
    settings_path: Path | str | None = None,
    claude_json_path: Path | str | None = None,
    state_path: Path | str | None = None,
    out: TextIO | None = None,
) -> int:
    stream = out or sys.stdout
    try:
        result = uninstall_settings(
            settings_path=settings_path,
            claude_json_path=claude_json_path,
            state_path=state_path,
        )
    except InstallError as exc:
        print(_format_install_error(exc), file=sys.stderr)
        return getattr(exc, "cli_exit_code", 2)

    print(format_uninstall_message(result), file=stream)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(argv) if argv is not None else sys.argv[1:]

    if argv:
        command = argv[0]
        if command == "install":
            args = _parse_install_args(argv[1:])
            if args.debug:
                _debug_force_enable()
                _dlog("--debug flag passed; CLAUDE_ANYTEAM_DEBUG=1 set in env")
            kwargs: dict[str, object] = {
                "assume_yes": args.assume_yes,
                "force_empty": args.force_empty,
                "no_input": args.no_input,
                "self_heal": args.self_heal,
                "no_allowlist": args.no_allowlist,
            }
            if args.settings_path is not None:
                kwargs["settings_path"] = args.settings_path
            if args.claude_json_path is not None:
                kwargs["claude_json_path"] = args.claude_json_path
            if args.state_path is not None:
                kwargs["state_path"] = args.state_path
            return _install_command(**kwargs)
        if command == "uninstall":
            args = _parse_uninstall_args(argv[1:])
            kwargs: dict[str, object] = {}
            if args.settings_path is not None:
                kwargs["settings_path"] = args.settings_path
            if args.claude_json_path is not None:
                kwargs["claude_json_path"] = args.claude_json_path
            if args.state_path is not None:
                kwargs["state_path"] = args.state_path
            return _uninstall_command(**kwargs)
        if command in ("team-agent", "team-patch", "team-roster", "team-config", "team-prune-dead"):
            from . import team_cli
            return team_cli.main_dispatch(command, argv[1:])
        if command == "diagnose":
            from . import diagnose_cli
            return diagnose_cli.main(argv[1:])
        if command == "status":
            from . import status_cli
            return status_cli.main(argv[1:])
        if command == "visibility-tail":
            from . import visibility_tail
            return visibility_tail.main(argv[1:])

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
    if args.turn_timeout_s is not None:
        overrides["turn_timeout_s"] = args.turn_timeout_s
    if args.non_progress_warn_s is not None:
        overrides["non_progress_warn_s"] = args.non_progress_warn_s
    if args.non_progress_interrupt_s is not None:
        overrides["non_progress_interrupt_s"] = args.non_progress_interrupt_s

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
        turn_timeout_s=settings.turn_timeout_s,
        non_progress_warn_s=settings.non_progress_warn_s,
        non_progress_interrupt_s=settings.non_progress_interrupt_s,
    )
    return run(settings)


if __name__ == "__main__":
    sys.exit(main())
