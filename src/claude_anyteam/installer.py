"""Persistent Claude Code settings installer for claude-anyteam.

This writes the leader-side environment variables that Claude Code reads at
startup so users do not need to hand-edit ~/.claude/settings.json, and it
also manages the ``teammateMode`` key in ~/.claude.json so Claude Code
routes teammate spawns through the tmux/psmux pane backend (required for
out-of-process teammates to appear in the TUI presence line).
"""

from __future__ import annotations

import contextlib
import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

TEAMMATE_COMMAND_KEY = "CLAUDE_CODE_TEAMMATE_COMMAND"
TEAMMATE_BINARY_KEY = "CLAUDE_ANYTEAM_BINARY"
GEMINI_TEAMMATE_BINARY_KEY = "CLAUDE_ANYTEAM_GEMINI_BINARY"
LEGACY_TEAMMATE_BINARY_KEY = "CODEX_TEAMMATE_BINARY"

SHIM_BASENAME = "claude-anyteam-spawn-shim"
LEGACY_SHIM_BASENAME = "codex-teammate-spawn-shim"
BINARY_BASENAME = "claude-anyteam"
LEGACY_BINARY_BASENAME = "codex-teammate"

RECOMMENDED_ALLOWLIST_ENTRIES = (
    "Write(~/.claude/teams/**/config.json)",
    "Write(~/.claude/teams/**/agents/**.json)",
    "Write(~/.claude/tasks/**)",
    "Edit(~/.claude/teams/**/config.json)",
    "Bash(setsid nohup uv run gemini-anyteam *)",
    "Bash(setsid nohup uv run claude-anyteam *)",
    "Bash(pkill -f gemini-anyteam *)",
    "Bash(pkill -f claude-anyteam *)",
    "Bash(mkdir -p ~/.claude/teams/**)",
)

MANAGED_BINARY_KEYS = (TEAMMATE_BINARY_KEY, GEMINI_TEAMMATE_BINARY_KEY, LEGACY_TEAMMATE_BINARY_KEY)
MANAGED_SHIM_BASENAMES = {SHIM_BASENAME, LEGACY_SHIM_BASENAME}
MANAGED_BINARY_BASENAMES = {BINARY_BASENAME, LEGACY_BINARY_BASENAME, "gemini-anyteam", "claude-anyteam-gemini"}

TEAMMATE_MODE_KEY = "teammateMode"
TEAMMATE_MODE_TARGET_VALUE = "tmux"
STATE_SCHEMA_VERSION = 3  # v3 adds managed permissions.allow allowlist state

PLUGIN_DATA_DIR_NAME = "claude-anyteam-claude-anyteam"
STATE_FILE_NAME = "install-state.json"

# CLI exit codes carried on InstallError via the cli_exit_code attribute:
#   2 = generic install failure (default, when cli_exit_code is unset)
#   3 = install aborted by user (teammateMode overwrite prompt declined)
#   4 = uninstall refuses to mutate files due to corrupted/malformed state
#   5 = install refused because no provider CLI is installed and signed in
INSTALL_ERROR_EXIT_GENERIC = 2
INSTALL_ERROR_EXIT_PROMPT_DECLINED = 3
INSTALL_ERROR_EXIT_CORRUPTED_STATE = 4
INSTALL_ERROR_EXIT_NO_PROVIDER = 5

ProviderState = Literal["READY", "NEEDS_SIGNIN", "NEEDS_UPGRADE", "MISSING"]


@dataclass(frozen=True)
class ManagedPaths:
    settings_path: Path
    shim_path: Path
    binary_path: Path


@dataclass(frozen=True)
class PrereqCheck:
    """Result of checking for a terminal multiplexer on PATH."""

    found: bool
    binary: str | None
    path: Path | None
    platform: str  # "linux" | "darwin" | "windows" | <sys.platform fallback>


@dataclass(frozen=True)
class GeminiCliCheck:
    """Result of probing for the Gemini CLI on PATH.

    Gemini CLI is required at runtime for gemini-* teammates but is not a hard
    install prereq. Capability flags are more important than semver because
    Gemini CLI is moving quickly.
    """

    found: bool
    path: Path | None
    version: str | None
    raw_output: str | None
    capabilities: dict[str, bool] = field(default_factory=dict)
    missing_capabilities: tuple[str, ...] = ()
    signed_in: bool = False
    signed_in_detail: str | None = None


@dataclass(frozen=True)
class CodexCliCheck:
    """Result of probing for the OpenAI Codex CLI on PATH.

    codex-cli is required at runtime for codex-* teammates but is NOT a hard
    install prereq — users may install claude-anyteam first and add codex later.
    """

    found: bool
    path: Path | None
    version: str | None  # parsed version token (e.g. "0.124.0"); None if unparseable
    raw_output: str | None  # raw `codex --version` stdout, retained for debugging
    signed_in: bool = False
    signed_in_detail: str | None = None


@dataclass(frozen=True)
class AuthCheck:
    """Non-secret result of probing a provider's local auth state."""

    signed_in: bool
    signed_in_detail: str | None = None


@dataclass(frozen=True)
class ProviderStatus:
    """Display-ready aggregate of a provider's install + sign-in state."""

    provider_key: Literal["codex", "gemini"]
    display_name: str
    summary_name: str
    state: ProviderState
    version: str | None = None
    upgrade_summary: str | None = None
    upgrade_hint: str | None = None

    @property
    def ready(self) -> bool:
        return self.state == "READY"

    @property
    def signed_in(self) -> bool:
        return self.state == "READY"

    def installed_cell(self) -> str:
        if self.state in ("READY", "NEEDS_SIGNIN"):
            return f"✅ {self.version}" if self.version else "✅"
        if self.state == "NEEDS_UPGRADE":
            return f"⚠️  {self.version}" if self.version else "⚠️"
        return "❌"

    def signin_cell(self) -> str:
        if self.state == "READY":
            return "✅"
        if self.state == "NEEDS_SIGNIN":
            return "❌"
        return "—"

    def summary_entry(self) -> str:
        if self.state == "READY":
            return f"{self.summary_name} {self.version}" if self.version else self.summary_name
        if self.state == "NEEDS_SIGNIN":
            return f"{self.summary_name} (sign in to finish)"
        if self.state == "NEEDS_UPGRADE":
            detail = self.upgrade_summary or "upgrade required"
            return f"{self.summary_name} ({detail})"
        return f"{self.summary_name} (not installed)"


@dataclass(frozen=True)
class TeammateModeResult:
    """Outcome of install_teammate_mode()."""

    claude_json_path: Path
    state_path: Path
    previous_value: str | None  # what the key held before we touched it (None if absent)
    new_value: str  # what the key holds now ("tmux" in every success branch)
    wrote_value: bool  # True if we mutated claude.json; False on the already-"tmux" no-op
    state_written: bool  # True if a state file was created/overwritten
    claude_json_created_by_anyteam: bool = False  # v2: True if install() created the file from scratch


@dataclass(frozen=True)
class TeammateModeRevertResult:
    """Outcome of uninstall_teammate_mode()."""

    claude_json_path: Path
    state_path: Path
    state_was_present: bool
    managed_by_us: bool  # state.teammateMode_set_by_anyteam at read time
    restored_value: str | None  # value put back (None = key removed)
    claude_json_touched: bool  # True if we mutated claude.json
    state_file_removed: bool
    claude_json_removed: bool = False  # v2: True if the now-empty file was unlinked
    plugin_data_dir_removed: bool = False  # v2: True if our plugin-data dir was rmdir'd


@dataclass(frozen=True)
class InstallResult:
    paths: ManagedPaths
    created_file: bool
    changed: dict[str, str]
    removed_legacy_keys: tuple[str, ...] = ()
    prereq: PrereqCheck | None = None
    teammate_mode: TeammateModeResult | None = None
    codex_cli: CodexCliCheck | None = None
    gemini_cli: GeminiCliCheck | None = None
    codex_auth: AuthCheck | None = None
    gemini_auth: AuthCheck | None = None
    codex_status: ProviderStatus | None = None
    gemini_status: ProviderStatus | None = None
    force_empty_used: bool = False
    permissions_allow_added: tuple[str, ...] = ()
    permissions_allow_managed: tuple[str, ...] = ()
    permissions_allowlist_skipped: bool = False

    @property
    def changed_anything(self) -> bool:
        return (
            self.created_file
            or bool(self.changed)
            or bool(self.removed_legacy_keys)
            or bool(self.permissions_allow_added)
            or (self.teammate_mode is not None and self.teammate_mode.wrote_value)
        )


@dataclass(frozen=True)
class UninstallResult:
    settings_path: Path
    removed: dict[str, str]
    skipped: dict[str, str]
    file_present: bool
    teammate_mode: TeammateModeRevertResult | None = None
    settings_file_removed: bool = False  # v2: True if the now-empty file was unlinked
    permissions_allow_removed: tuple[str, ...] = ()

    @property
    def changed_anything(self) -> bool:
        return (
            bool(self.removed)
            or bool(self.permissions_allow_removed)
            or self.settings_file_removed
            or (self.teammate_mode is not None and self.teammate_mode.claude_json_touched)
            or (self.teammate_mode is not None and self.teammate_mode.claude_json_removed)
        )


class InstallError(ValueError):
    """Raised when install/uninstall cannot safely update Claude settings.

    Some install failures warrant a distinct CLI exit code (e.g. user declining
    the teammateMode overwrite prompt). Callers may attach a ``cli_exit_code``
    attribute on the exception instance to steer the CLI; default is 2.
    """



# ---------------------------------------------------------------------------
# File paths
# ---------------------------------------------------------------------------

def default_settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def default_claude_json_path() -> Path:
    return Path.home() / ".claude.json"


def default_state_path() -> Path:
    return Path.home() / ".claude" / "plugins" / "data" / PLUGIN_DATA_DIR_NAME / STATE_FILE_NAME



# ---------------------------------------------------------------------------
# Path discovery (existing install/uninstall helpers, unchanged)
# ---------------------------------------------------------------------------

def _resolve_executable(name_or_path: str | None) -> Path | None:
    if not name_or_path:
        return None

    candidate = Path(name_or_path)
    raw = str(candidate)
    has_sep = os.sep in raw or (os.altsep is not None and os.altsep in raw)
    if candidate.parent != Path(".") or has_sep:
        if candidate.exists():
            return candidate.resolve()
        return None

    found = shutil.which(name_or_path)
    if not found:
        return None
    return Path(found).resolve()



def _first_resolved(*candidates: str | None) -> Path | None:
    for candidate in candidates:
        resolved = _resolve_executable(candidate)
        if resolved is not None:
            return resolved
    return None



def discover_managed_paths(
    *,
    settings_path: Path | str | None = None,
    argv0: str | None = None,
    shim_path: str | None = None,
    binary_path: str | None = None,
) -> ManagedPaths:
    raw_settings = Path(settings_path) if settings_path is not None else default_settings_path()
    settings = raw_settings.expanduser().resolve()
    current = _resolve_executable(argv0)

    resolved_binary = _resolve_executable(binary_path)
    if resolved_binary is None and current is not None and current.name in MANAGED_BINARY_BASENAMES:
        resolved_binary = current
    if resolved_binary is None:
        resolved_binary = _first_resolved(BINARY_BASENAME, LEGACY_BINARY_BASENAME)

    resolved_shim = _resolve_executable(shim_path)
    if resolved_shim is None and current is not None:
        if current.name in MANAGED_BINARY_BASENAMES:
            for sibling_name in (SHIM_BASENAME, LEGACY_SHIM_BASENAME):
                sibling = current.with_name(sibling_name)
                if sibling.exists():
                    resolved_shim = sibling.resolve()
                    break
        elif current.name in MANAGED_SHIM_BASENAMES:
            resolved_shim = current
    if resolved_shim is None:
        resolved_shim = _first_resolved(SHIM_BASENAME, LEGACY_SHIM_BASENAME)

    if resolved_binary is None:
        raise InstallError(
            "Unable to resolve the claude-anyteam binary. Ensure the package is "
            "installed and the console script is on PATH."
        )
    if resolved_shim is None:
        raise InstallError(
            "Unable to resolve the claude-anyteam-spawn-shim binary. Ensure the "
            "package is installed and the console script is on PATH."
        )

    return ManagedPaths(
        settings_path=settings,
        shim_path=resolved_shim,
        binary_path=resolved_binary,
    )



# ---------------------------------------------------------------------------
# JSON I/O (shared by settings.json, claude.json, state file)
# ---------------------------------------------------------------------------

def _load_settings(path: Path) -> tuple[dict[str, Any], bool]:
    if not path.exists():
        return {}, False

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InstallError(f"{path} is not valid JSON: {exc}") from exc

    if not isinstance(raw, dict):
        raise InstallError(f"{path} must contain a JSON object at the top level.")

    return raw, True



def _env_block(settings: dict[str, Any], *, path: Path, create: bool) -> dict[str, str]:
    env = settings.get("env")
    if env is None:
        if not create:
            return {}
        env = {}
        settings["env"] = env

    if not isinstance(env, dict):
        raise InstallError(f"{path} has an 'env' entry, but it is not a JSON object.")

    for key, value in env.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise InstallError(
                f"{path} has a non-string entry under 'env'; refusing to overwrite it."
            )

    return env


def _permissions_block(
    settings: dict[str, Any],
    *,
    path: Path,
    create: bool,
) -> dict[str, Any]:
    permissions = settings.get("permissions")
    if permissions is None:
        if not create:
            return {}
        permissions = {}
        settings["permissions"] = permissions

    if not isinstance(permissions, dict):
        raise InstallError(
            f"{path} has a 'permissions' entry, but it is not a JSON object."
        )

    return permissions


def _permissions_allow_list(
    settings: dict[str, Any],
    *,
    path: Path,
    create: bool,
) -> list[str]:
    permissions = _permissions_block(settings, path=path, create=create)
    if not permissions and not create:
        return []

    allow = permissions.get("allow")
    if allow is None:
        if not create:
            return []
        allow = []
        permissions["allow"] = allow

    if not isinstance(allow, list):
        raise InstallError(
            f"{path} has a 'permissions.allow' entry, but it is not a JSON array."
        )

    if not all(isinstance(entry, str) for entry in allow):
        raise InstallError(
            f"{path} has a non-string entry under 'permissions.allow'; refusing to overwrite it."
        )

    return allow


def _merge_unique_preserving_order(*entry_groups: tuple[str, ...]) -> tuple[str, ...]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in entry_groups:
        for entry in group:
            if entry not in seen:
                seen.add(entry)
                merged.append(entry)
    return tuple(merged)


def _state_permissions_allow_added(state: dict[str, Any] | None) -> tuple[str, ...]:
    if state is None:
        return ()
    raw = state.get("permissions_allow_added_by_anyteam", ())
    if not isinstance(raw, list) or not all(isinstance(entry, str) for entry in raw):
        return ()
    recommended = set(RECOMMENDED_ALLOWLIST_ENTRIES)
    return tuple(entry for entry in raw if entry in recommended)


def _state_permissions_allow_added_strict(
    state: dict[str, Any],
    *,
    state_path: Path,
) -> tuple[str, ...]:
    if "permissions_allow_added_by_anyteam" not in state:
        return ()
    raw = state.get("permissions_allow_added_by_anyteam")
    if not isinstance(raw, list) or not all(isinstance(entry, str) for entry in raw):
        err = InstallError(
            f"{state_path} has a malformed 'permissions_allow_added_by_anyteam' value; "
            "refusing to touch permissions.allow.\n"
            f"Inspect or delete the state file manually, then re-run uninstall."
        )
        err.cli_exit_code = INSTALL_ERROR_EXIT_CORRUPTED_STATE  # type: ignore[attr-defined]
        raise err
    recommended = set(RECOMMENDED_ALLOWLIST_ENTRIES)
    return tuple(entry for entry in raw if entry in recommended)


def _state_permissions_bool(state: dict[str, Any] | None, key: str) -> bool:
    if state is None:
        return False
    return bool(state.get(key, False))


def _load_existing_state_for_install(path: Path) -> dict[str, Any] | None:
    try:
        return _load_state(path)
    except InstallError:
        # Existing install() did not read state before overwriting it. Preserve
        # that self-healing behavior if a previous receipt is unreadable.
        return None


def _install_permission_allowlist(
    settings: dict[str, Any],
    *,
    path: Path,
    no_allowlist: bool,
) -> tuple[tuple[str, ...], bool, bool]:
    """Append recommended permissions.allow entries, returning what we created.

    Returns (entries_added, permissions_object_created, allow_list_created).
    The entries are appended idempotently, so re-running install never creates
    duplicate permission patterns.
    """
    if no_allowlist:
        return (), False, False

    permissions_existed = "permissions" in settings
    permissions = settings.get("permissions")
    allow_existed = isinstance(permissions, dict) and "allow" in permissions

    allow = _permissions_allow_list(settings, path=path, create=True)
    added: list[str] = []
    for entry in RECOMMENDED_ALLOWLIST_ENTRIES:
        if entry not in allow:
            allow.append(entry)
            added.append(entry)

    return tuple(added), not permissions_existed, not allow_existed


def _remove_permission_allowlist_entries(
    settings: dict[str, Any],
    *,
    path: Path,
    entries: tuple[str, ...],
    permissions_created_by_anyteam: bool,
    allow_created_by_anyteam: bool,
) -> tuple[str, ...]:
    if not entries:
        return ()

    permissions = _permissions_block(settings, path=path, create=False)
    if not permissions:
        return ()

    allow = _permissions_allow_list(settings, path=path, create=False)
    if not allow:
        return ()

    removed: list[str] = []
    for entry in entries:
        with contextlib.suppress(ValueError):
            allow.remove(entry)
            removed.append(entry)

    if removed and not allow and allow_created_by_anyteam:
        permissions.pop("allow", None)
    if removed and not permissions and permissions_created_by_anyteam:
        settings.pop("permissions", None)

    return tuple(removed)



def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)
        raise


def _write_settings(path: Path, settings: dict[str, Any]) -> None:
    _atomic_write_json(path, settings)


def _load_claude_json(path: Path) -> tuple[dict[str, Any], bool]:
    # Same shape/contract as _load_settings but applied to ~/.claude.json.
    if not path.exists():
        return {}, False

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InstallError(f"{path} is not valid JSON: {exc}") from exc

    if not isinstance(raw, dict):
        raise InstallError(f"{path} must contain a JSON object at the top level.")

    return raw, True


def _write_claude_json(path: Path, payload: dict[str, Any]) -> None:
    _atomic_write_json(path, payload)



# ---------------------------------------------------------------------------
# State file (install-state.json) management
# ---------------------------------------------------------------------------

def _load_state(path: Path) -> dict[str, Any] | None:
    """Returns the parsed state dict, or None if absent.

    Does NOT raise on missing-file — that's the expected case for fresh installs
    and for backward-compat with installs that predate this feature.
    """
    if not path.exists():
        return None

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InstallError(f"{path} is not valid JSON: {exc}") from exc

    if not isinstance(raw, dict):
        raise InstallError(f"{path} must contain a JSON object at the top level.")

    return raw


def _write_state(path: Path, state: dict[str, Any]) -> None:
    _atomic_write_json(path, state)


def _delete_state(path: Path) -> bool:
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False



# ---------------------------------------------------------------------------
# Terminal multiplexer prereq check
# ---------------------------------------------------------------------------

def _platform_name() -> str:
    raw = sys.platform
    if raw.startswith("linux"):
        return "linux"
    if raw == "darwin":
        return "darwin"
    if raw in ("win32", "cygwin"):
        return "windows"
    return raw


def _check_terminal_multiplexer() -> PrereqCheck:
    """Checks PATH for tmux (Linux/mac) or psmux/tmux (Windows).

    Windows prefers ``psmux`` (handles Claude Code's POSIX-shaped teammate-spawn
    command via a PowerShell translator), but accepts a plain ``tmux`` binary if
    the user installed one via Cygwin / MSYS2 / WSL-interop.

    Linux and macOS require ``tmux`` on PATH — there is no reason to look for
    psmux there.
    """
    platform = _platform_name()

    if platform == "windows":
        candidates = ("psmux", "tmux")
    else:
        candidates = ("tmux",)

    for name in candidates:
        found_path = shutil.which(name)
        if found_path:
            return PrereqCheck(
                found=True,
                binary=name,
                path=Path(found_path).resolve(),
                platform=platform,
            )

    return PrereqCheck(found=False, binary=None, path=None, platform=platform)


def _install_instructions(platform: str) -> str:
    if platform == "linux":
        return (
            "  Debian/Ubuntu: sudo apt install tmux\n"
            "  Fedora/RHEL:   sudo dnf install tmux\n"
            "  Arch:          sudo pacman -S tmux"
        )
    if platform == "darwin":
        return "  macOS (Homebrew): brew install tmux"
    if platform == "windows":
        return (
            "  Recommended: winget install psmux\n"
            "  Also supported: choco install psmux / scoop install psmux"
        )
    return "  Install tmux via your platform's package manager."



# ---------------------------------------------------------------------------
# Codex CLI prereq check (informational — non-blocking)
# ---------------------------------------------------------------------------

CODEX_CLI_BINARY = "codex"
CODEX_CLI_INSTALL_COMMAND = "npm install -g @openai/codex"
CODEX_CLI_DOCS_URL = "https://github.com/openai/codex#getting-started"
CODEX_CLI_MIN_VERSION: tuple[int, int, int] = (0, 120, 0)
CODEX_CLI_VERSION_TIMEOUT_S = 5
GEMINI_CLI_BINARY = "gemini"
GEMINI_CLI_INSTALL_COMMAND = "npm install -g @google/gemini-cli"
GEMINI_CLI_DOCS_URL = "https://github.com/google-gemini/gemini-cli"
GEMINI_CLI_VERSION_TIMEOUT_S = 5
CODEX_AUTH_PATH = Path(".codex") / "auth.json"
GEMINI_OAUTH_CREDS_PATH = Path(".gemini") / "oauth_creds.json"
GEMINI_GOOGLE_ACCOUNTS_PATH = Path(".gemini") / "google_accounts.json"
GEMINI_API_KEY_ENV = "GEMINI_API_KEY"
GEMINI_VERTEX_ENV_KEYS = ("GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_GENAI_USE_VERTEXAI")

# Accepts semver-ish tokens like "0.124.0", "0.124", or "0.124.0-rc1". Rejects
# garbage ("12abc"), leading-v prefixes ("v0.124.0"), and trailing junk.
_CLI_VERSION_TOKEN_RE = re.compile(
    r"^(?P<major>\d+)\.(?P<minor>\d+)(?:\.(?P<patch>\d+))?(?:[-+][A-Za-z0-9.\-]+)?$"
)


def _parse_cli_version(raw: str) -> str | None:
    for token in raw.split():
        if _CLI_VERSION_TOKEN_RE.match(token):
            return token
    return None


def _parse_version_tuple(version: str | None) -> tuple[int, int, int] | None:
    if version is None:
        return None
    match = _CLI_VERSION_TOKEN_RE.match(version)
    if match is None:
        return None
    major = int(match.group("major"))
    minor = int(match.group("minor"))
    patch = int(match.group("patch") or 0)
    return (major, minor, patch)


def _min_version_str() -> str:
    return ".".join(str(part) for part in CODEX_CLI_MIN_VERSION)


def _codex_meets_minimum(check: CodexCliCheck) -> bool | None:
    """True if detected version ≥ floor, False if below, None if unknown/unparseable."""
    if not check.found:
        return None
    parsed = _parse_version_tuple(check.version)
    if parsed is None:
        return None
    return parsed >= CODEX_CLI_MIN_VERSION


def _check_codex_cli() -> CodexCliCheck:
    found_path = shutil.which(CODEX_CLI_BINARY)
    if not found_path:
        return CodexCliCheck(
            found=False,
            path=None,
            version=None,
            raw_output=None,
            signed_in=False,
            signed_in_detail="Codex CLI not found on PATH",
        )

    resolved = Path(found_path).resolve()
    raw: str | None = None
    version: str | None = None
    try:
        completed = subprocess.run(
            [str(resolved), "--version"],
            capture_output=True,
            text=True,
            timeout=CODEX_CLI_VERSION_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        completed = None

    if completed is not None and completed.returncode == 0:
        raw = (completed.stdout or "").strip() or None
        if raw:
            version = _parse_cli_version(raw)

    try:
        signed_in, signed_in_detail = _check_codex_signin(resolved)
    except Exception as exc:
        signed_in, signed_in_detail = False, f"Codex sign-in check failed: {exc}"

    return CodexCliCheck(
        found=True,
        path=resolved,
        version=version,
        raw_output=raw,
        signed_in=signed_in,
        signed_in_detail=signed_in_detail,
    )


def _non_empty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _read_auth_json_object(path: Path, *, label: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, f"{label} file missing: {path}"
    except OSError as exc:
        return None, f"{label} file unreadable: {path}: {exc}"

    if not text.strip():
        return None, f"{label} file empty: {path}"

    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f"{label} file malformed: {path}: {exc.msg}"

    if not isinstance(raw, dict):
        return None, f"{label} file malformed: {path}: expected a JSON object"
    return raw, None


def _load_json_object_or_none(path: Path) -> dict[str, Any] | None:
    raw, _detail = _read_auth_json_object(path, label="auth")
    return raw


def _parse_expiry_timestamp(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        timestamp = float(value)
    elif isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            timestamp = float(stripped)
        except ValueError:
            try:
                parsed = datetime.fromisoformat(stripped.replace("Z", "+00:00"))
            except ValueError:
                return None
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.timestamp()
    else:
        return None

    # Gemini stores expiry_date as milliseconds since epoch; tolerate seconds too.
    if timestamp > 10_000_000_000:
        timestamp /= 1000
    return timestamp


def _expiry_detail(
    payload: dict[str, Any],
    *,
    path: Path,
    label: str,
    keys: tuple[str, ...] = ("expiry_date", "expires_at", "expiresAt", "expires"),
) -> str | None:
    now = datetime.now(timezone.utc).timestamp()
    for key in keys:
        if key not in payload:
            continue
        value = payload.get(key)
        if value in (None, ""):
            continue
        timestamp = _parse_expiry_timestamp(value)
        if timestamp is None:
            return f"{label} file malformed: {path}: invalid {key}"
        if timestamp <= now:
            return f"{label} credentials expired: {path}"
    return None


def _contains_non_empty_string(value: object) -> bool:
    if _non_empty_string(value):
        return True
    if isinstance(value, dict):
        return any(_contains_non_empty_string(child) for child in value.values())
    if isinstance(value, list):
        return any(_contains_non_empty_string(child) for child in value)
    return False


def _check_codex_signin(
    found_path: Path | str | None,
    auth_path: Path | str | None = None,
) -> tuple[bool, str | None]:
    """Detect Codex local auth state without exposing token values."""
    del found_path  # The auth file location is currently independent of the binary path.
    try:
        path = Path(auth_path).expanduser() if auth_path is not None else Path.home() / CODEX_AUTH_PATH
        raw, detail = _read_auth_json_object(path, label="Codex auth")
        if raw is None:
            return False, detail

        tokens = raw.get("tokens")
        if tokens is not None and not isinstance(tokens, dict):
            return False, f"Codex auth file malformed: {path}: tokens must be a JSON object"

        expiry = _expiry_detail(raw, path=path, label="Codex auth")
        if expiry is None and isinstance(tokens, dict):
            expiry = _expiry_detail(tokens, path=path, label="Codex auth")
        if expiry is not None:
            return False, expiry

        token_signed_in = isinstance(tokens, dict) and any(
            _non_empty_string(tokens.get(key))
            for key in ("access_token", "id_token", "refresh_token")
        )
        if _non_empty_string(raw.get("OPENAI_API_KEY")) or token_signed_in:
            return True, None
        return False, f"Codex auth file empty or missing credentials: {path}"
    except Exception as exc:
        return False, f"Codex sign-in check failed: {exc}"


def _check_gemini_signin(
    found_path: Path | str | None,
    oauth_creds_path: Path | str | None = None,
    google_accounts_path: Path | str | None = None,
    environ: dict[str, str] | None = None,
) -> tuple[bool, str | None]:
    """Detect Gemini local auth state without exposing token values."""
    del found_path  # The auth file locations are currently independent of the binary path.
    try:
        env = os.environ if environ is None else environ
        if _non_empty_string(env.get(GEMINI_API_KEY_ENV)):
            return True, None
        if any(_non_empty_string(env.get(key)) for key in GEMINI_VERTEX_ENV_KEYS):
            return True, None

        oauth_path = (
            Path(oauth_creds_path).expanduser()
            if oauth_creds_path is not None
            else Path.home() / GEMINI_OAUTH_CREDS_PATH
        )
        accounts_path = (
            Path(google_accounts_path).expanduser()
            if google_accounts_path is not None
            else Path.home() / GEMINI_GOOGLE_ACCOUNTS_PATH
        )

        oauth_raw, oauth_detail = _read_auth_json_object(oauth_path, label="Gemini OAuth credentials")
        if oauth_raw is not None:
            expiry = _expiry_detail(oauth_raw, path=oauth_path, label="Gemini OAuth credentials")
            if expiry is not None:
                return False, expiry
            if any(
                _non_empty_string(oauth_raw.get(key))
                for key in ("access_token", "id_token", "refresh_token")
            ):
                return True, None
            return False, f"Gemini OAuth credentials file empty or missing credentials: {oauth_path}"

        accounts_raw, accounts_detail = _read_auth_json_object(accounts_path, label="Gemini account")
        if accounts_raw is not None:
            active = accounts_raw.get("active")
            if _contains_non_empty_string(active):
                return True, None
            return False, f"Gemini account file empty or missing active account: {accounts_path}"

        if oauth_detail and accounts_detail:
            return False, f"{oauth_detail}; {accounts_detail}"
        return False, oauth_detail or accounts_detail
    except Exception as exc:
        return False, f"Gemini sign-in check failed: {exc}"


def _check_codex_auth(auth_path: Path | str | None = None) -> AuthCheck:
    """Detect whether Codex has usable local auth without exposing secrets."""
    signed_in, detail = _check_codex_signin(CODEX_CLI_BINARY, auth_path=auth_path)
    return AuthCheck(signed_in=signed_in, signed_in_detail=detail)


def _codex_cli_warning(check: CodexCliCheck) -> str | None:
    """Warning text for the codex-cli check, or None when no warning applies.

    Two warning branches — missing and below-floor — each include the install
    command, docs link, and the `codex` sign-in hint so users aren't blindsided
    by an auth prompt the first time a codex-* teammate launches.

    Returns None for both the "present and at-or-above floor" and "present but
    version unparseable" cases. Parse-fail falls back to a presence-only line
    (emitted by the caller); we don't synthesize a warning when we can't
    actually confirm the floor was violated.
    """
    min_version = _min_version_str()
    signin_hint = f"  After installing, run `{CODEX_CLI_BINARY}` once to sign in."
    docs_line = f"  Setup guide: {CODEX_CLI_DOCS_URL}"

    if not check.found:
        return (
            f"Warning: the OpenAI Codex CLI (`{CODEX_CLI_BINARY}`) was not found on PATH.\n"
            f"  claude-anyteam is installed, but codex-* teammates will fail to launch\n"
            f"  until Codex is installed. Add it with:\n"
            f"    {CODEX_CLI_INSTALL_COMMAND}\n"
            f"{signin_hint}\n"
            f"{docs_line}"
        )

    meets = _codex_meets_minimum(check)
    if meets is False:
        return (
            f"Warning: detected Codex CLI {check.version} at {check.path}, but\n"
            f"  claude-anyteam requires {min_version} or newer. Upgrade with:\n"
            f"    {CODEX_CLI_INSTALL_COMMAND}\n"
            f"{signin_hint}\n"
            f"{docs_line}"
        )

    # Found but version unparseable → fall back to presence-only acknowledgment;
    # don't block on a parse miss, don't fabricate a scary warning when we can't
    # confirm the floor either way.
    return None



_GEMINI_REQUIRED_CAPABILITIES = (
    "--prompt",
    "--output-format stream-json",
    "--resume",
    "--approval-mode yolo",
)


def _gemini_acp_flag_from_help(help_text: str) -> str | None:
    if "--acp" in help_text:
        return "--acp"
    if "--experimental-acp" in help_text:
        return "--experimental-acp"
    return None


def _gemini_capabilities_from_help(help_text: str) -> dict[str, bool]:
    return {
        "--prompt": "--prompt" in help_text,
        "--output-format stream-json": "--output-format" in help_text and "stream-json" in help_text,
        "--resume": "--resume" in help_text,
        "--approval-mode yolo": "--approval-mode" in help_text and "yolo" in help_text,
        "--acp": _gemini_acp_flag_from_help(help_text) is not None,
    }


def _check_gemini_cli() -> GeminiCliCheck:
    found_path = shutil.which(GEMINI_CLI_BINARY)
    if not found_path:
        return GeminiCliCheck(
            found=False,
            path=None,
            version=None,
            raw_output=None,
            signed_in=False,
            signed_in_detail="Gemini CLI not found on PATH",
        )
    resolved = Path(found_path).resolve()
    raw = None
    version = None
    try:
        completed = subprocess.run(
            [str(resolved), "--version"],
            capture_output=True,
            text=True,
            timeout=GEMINI_CLI_VERSION_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        completed = None
    if completed is not None and completed.returncode == 0:
        raw = ((completed.stdout or "") or (completed.stderr or "")).strip() or None
        version = _parse_cli_version(raw or "")

    help_text = ""
    try:
        help_completed = subprocess.run(
            [str(resolved), "--help"],
            capture_output=True,
            text=True,
            timeout=GEMINI_CLI_VERSION_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        help_completed = None
    if help_completed is not None and help_completed.returncode == 0:
        help_text = (help_completed.stdout or "") + (help_completed.stderr or "")
    capabilities = _gemini_capabilities_from_help(help_text)
    missing = tuple(name for name in _GEMINI_REQUIRED_CAPABILITIES if not capabilities.get(name, False))
    try:
        signed_in, signed_in_detail = _check_gemini_signin(resolved)
    except Exception as exc:
        signed_in, signed_in_detail = False, f"Gemini sign-in check failed: {exc}"
    return GeminiCliCheck(
        found=True,
        path=resolved,
        version=version,
        raw_output=raw,
        capabilities=capabilities,
        missing_capabilities=missing,
        signed_in=signed_in,
        signed_in_detail=signed_in_detail,
    )


def _check_gemini_auth(
    oauth_creds_path: Path | str | None = None,
    google_accounts_path: Path | str | None = None,
    environ: dict[str, str] | None = None,
) -> AuthCheck:
    """Detect whether Gemini CLI has usable OAuth/API-key/Vertex auth."""
    signed_in, detail = _check_gemini_signin(
        GEMINI_CLI_BINARY,
        oauth_creds_path=oauth_creds_path,
        google_accounts_path=google_accounts_path,
        environ=environ,
    )
    return AuthCheck(signed_in=signed_in, signed_in_detail=detail)


def _gemini_cli_warning(check: GeminiCliCheck) -> str | None:
    if not check.found:
        return (
            f"Warning: the Gemini CLI (`{GEMINI_CLI_BINARY}`) was not found on PATH.\n"
            f"  claude-anyteam is installed, but gemini-* teammates will fail to launch\n"
            f"  until Gemini CLI is installed and authenticated. Add it with:\n"
            f"    {GEMINI_CLI_INSTALL_COMMAND}\n"
            f"  After installing, run `{GEMINI_CLI_BINARY}` once to sign in, or configure GEMINI_API_KEY/Vertex auth.\n"
            f"  Setup guide: {GEMINI_CLI_DOCS_URL}"
        )
    if check.missing_capabilities:
        missing_lines = "\n".join(
            f"  Gemini CLI is missing required flag {capability}; gemini-* teammates may not work."
            for capability in check.missing_capabilities
        )
        return (
            f"Warning: detected Gemini CLI at {check.path}, but it is missing required headless capabilities.\n"
            f"{missing_lines}\n"
            f"  gemini-* teammates require `{GEMINI_CLI_BINARY} --help` to advertise these flags/choices. Upgrade Gemini CLI with:\n"
            f"    {GEMINI_CLI_INSTALL_COMMAND}\n"
            f"  Setup guide: {GEMINI_CLI_DOCS_URL}"
        )
    return None


def _codex_provider_status(cli: CodexCliCheck, auth: AuthCheck) -> ProviderStatus:
    if not cli.found:
        state: ProviderState = "MISSING"
        upgrade_summary = None
        upgrade_hint = None
    elif _codex_meets_minimum(cli) is False:
        state = "NEEDS_UPGRADE"
        detected = cli.version or "unknown"
        floor = _min_version_str()
        upgrade_summary = f"upgrade — {detected} < {floor} floor"
        upgrade_hint = f"detected {detected}, need ≥ {floor}"
    elif auth.signed_in:
        state = "READY"
        upgrade_summary = None
        upgrade_hint = None
    else:
        state = "NEEDS_SIGNIN"
        upgrade_summary = None
        upgrade_hint = None
    return ProviderStatus(
        provider_key="codex",
        display_name="Codex CLI",
        summary_name="Codex",
        state=state,
        version=cli.version,
        upgrade_summary=upgrade_summary,
        upgrade_hint=upgrade_hint,
    )


def _gemini_provider_status(cli: GeminiCliCheck, auth: AuthCheck) -> ProviderStatus:
    if not cli.found:
        state: ProviderState = "MISSING"
        upgrade_summary = None
        upgrade_hint = None
    elif auth.signed_in:
        state = "READY"
        upgrade_summary = None
        upgrade_hint = None
    else:
        state = "NEEDS_SIGNIN"
        upgrade_summary = None
        upgrade_hint = None
    return ProviderStatus(
        provider_key="gemini",
        display_name="Gemini CLI",
        summary_name="Gemini",
        state=state,
        version=cli.version,
        upgrade_summary=upgrade_summary,
        upgrade_hint=upgrade_hint,
    )


def _any_provider_ready(*statuses: ProviderStatus) -> bool:
    return any(status.ready for status in statuses)


# ---------------------------------------------------------------------------
# teammateMode install/uninstall
# ---------------------------------------------------------------------------

def install_teammate_mode(
    *,
    claude_json_path: Path,
    state_path: Path,
    prompt_fn: Callable[[str], bool],
    settings_file_created_by_anyteam: bool = False,
    codex_cli: CodexCliCheck | None = None,
    gemini_cli: GeminiCliCheck | None = None,
    codex_auth: AuthCheck | None = None,
    gemini_auth: AuthCheck | None = None,
    force_empty_used: bool = False,
    permissions_allow_added_by_anyteam: tuple[str, ...] = (),
    permissions_allowlist_skipped: bool = False,
    permissions_created_by_anyteam: bool = False,
    permissions_allow_created_by_anyteam: bool = False,
) -> TeammateModeResult:
    """Ensures ~/.claude.json has teammateMode='tmux', recording what we did in state.

    Branches per the approved install spec:
      * key absent → write 'tmux'; state records original=None, set_by=True.
      * key == 'tmux' → no-op on claude.json; state records original='tmux', set_by=False.
      * key in {'auto', 'in-process', other} → call prompt_fn(current_value).
          - True  → overwrite to 'tmux'; state records original=current, set_by=True.
          - False → raise InstallError with cli_exit_code=3 (no state, no mutation).

    ``settings_file_created_by_anyteam`` is plumbed through from install() so the
    state-file record is a complete receipt (both v2 created-flags in one place).
    """
    claude_json, existed = _load_claude_json(claude_json_path)
    current = claude_json.get(TEAMMATE_MODE_KEY)
    claude_json_created_by_anyteam = not existed

    if current is not None and not isinstance(current, str):
        raise InstallError(
            f"{claude_json_path} has a non-string {TEAMMATE_MODE_KEY!r} value; refusing to touch it."
        )

    def _build_state(original: str | None, set_by: bool) -> dict[str, Any]:
        state: dict[str, Any] = {
            "schema_version": STATE_SCHEMA_VERSION,
            "teammateMode_original": original,
            "teammateMode_set_by_anyteam": set_by,
            # v2 created-flags: mark every file we brought into existence so uninstall
            # can remove exactly those if and only if they end up empty later.
            "settings_file_created_by_anyteam": bool(settings_file_created_by_anyteam),
            "claude_json_created_by_anyteam": bool(claude_json_created_by_anyteam),
            "force_empty_used": bool(force_empty_used),
            "permissions_allow_added_by_anyteam": list(permissions_allow_added_by_anyteam),
            "permissions_allowlist_skipped": bool(permissions_allowlist_skipped),
            "permissions_created_by_anyteam": bool(permissions_created_by_anyteam),
            "permissions_allow_created_by_anyteam": bool(permissions_allow_created_by_anyteam),
        }
        if codex_cli is not None:
            state["codex_cli_found"] = bool(codex_cli.found)
            state["codex_cli_version"] = codex_cli.version
        if codex_auth is not None:
            state["codex_signed_in"] = bool(codex_auth.signed_in)
        if gemini_cli is not None:
            state["gemini_cli_found"] = bool(gemini_cli.found)
            state["gemini_cli_version"] = gemini_cli.version
            state["gemini_cli_capabilities"] = dict(gemini_cli.capabilities)
        if gemini_auth is not None:
            state["gemini_signed_in"] = bool(gemini_auth.signed_in)
        return state

    # Case 1: absent.
    if current is None:
        claude_json[TEAMMATE_MODE_KEY] = TEAMMATE_MODE_TARGET_VALUE
        _write_claude_json(claude_json_path, claude_json)
        _write_state(state_path, _build_state(None, True))
        return TeammateModeResult(
            claude_json_path=claude_json_path,
            state_path=state_path,
            previous_value=None,
            new_value=TEAMMATE_MODE_TARGET_VALUE,
            wrote_value=True,
            state_written=True,
            claude_json_created_by_anyteam=claude_json_created_by_anyteam,
        )

    # Case 2: already tmux.
    if current == TEAMMATE_MODE_TARGET_VALUE:
        _write_state(state_path, _build_state(TEAMMATE_MODE_TARGET_VALUE, False))
        return TeammateModeResult(
            claude_json_path=claude_json_path,
            state_path=state_path,
            previous_value=TEAMMATE_MODE_TARGET_VALUE,
            new_value=TEAMMATE_MODE_TARGET_VALUE,
            wrote_value=False,
            state_written=True,
            claude_json_created_by_anyteam=claude_json_created_by_anyteam,
        )

    # Case 3: something else. Prompt before overwriting.
    if not prompt_fn(current):
        err = InstallError(
            f"Install aborted: existing {TEAMMATE_MODE_KEY}={current!r} in {claude_json_path}\n"
            "  claude-anyteam needs teammateMode=\"tmux\" to route teammates through the pane backend.\n"
            "  Re-run with --assume-yes to accept, or manually set teammateMode=\"tmux\" in ~/.claude.json."
        )
        err.cli_exit_code = INSTALL_ERROR_EXIT_PROMPT_DECLINED  # type: ignore[attr-defined]
        raise err

    claude_json[TEAMMATE_MODE_KEY] = TEAMMATE_MODE_TARGET_VALUE
    _write_claude_json(claude_json_path, claude_json)
    _write_state(state_path, _build_state(current, True))
    return TeammateModeResult(
        claude_json_path=claude_json_path,
        state_path=state_path,
        previous_value=current,
        new_value=TEAMMATE_MODE_TARGET_VALUE,
        wrote_value=True,
        state_written=True,
        claude_json_created_by_anyteam=claude_json_created_by_anyteam,
    )


def uninstall_teammate_mode(
    *,
    claude_json_path: Path,
    state_path: Path,
) -> TeammateModeRevertResult:
    """Reverses whatever install_teammate_mode did, using the state file as ground truth.

    No state file (fresh install pre-feature, or user hand-deleted) → no-op.
    State exists and parseable:
      * set_by_anyteam=False → no-op on claude.json; state file is cleared.
      * set_by_anyteam=True  → restore teammateMode_original (None = remove key).
    State exists but malformed → raise InstallError(cli_exit_code=4); state file
      stays on disk so the user can inspect it. We never silently delete state
      we can't understand because it may encode data the user wants to recover.

    After a successful revert of a file we created (claude_json_created_by_anyteam)
    that now has no other keys, unlink it entirely. Same for the plugin-data dir
    containing the state file — rmdir it only if empty, never recursively.
    """
    state = _load_state(state_path)
    if state is None:
        return TeammateModeRevertResult(
            claude_json_path=claude_json_path,
            state_path=state_path,
            state_was_present=False,
            managed_by_us=False,
            restored_value=None,
            claude_json_touched=False,
            state_file_removed=False,
        )

    managed = bool(state.get("teammateMode_set_by_anyteam"))
    original = state.get("teammateMode_original")
    # v2 created-flag. Missing on v1 state files → default False. Safety bias:
    # never delete a file we are not CERTAIN we created. The v1→v2 migration
    # direction is one-way; a user who ran install on the v1 installer and
    # uninstalls on the v2 installer simply keeps the file we can't prove we
    # brought into existence.
    claude_json_was_ours = bool(state.get("claude_json_created_by_anyteam", False))

    if original is not None and not isinstance(original, str):
        # Corrupted state: refuse to touch anything, keep state file on disk for
        # the user to inspect. Bail with exit code 4 so scripts can differentiate.
        err = InstallError(
            f"{state_path} has a malformed 'teammateMode_original' value "
            f"({type(original).__name__}: {original!r}); refusing to touch config.\n"
            f"Inspect or delete the state file manually, then re-run uninstall."
        )
        err.cli_exit_code = INSTALL_ERROR_EXIT_CORRUPTED_STATE  # type: ignore[attr-defined]
        raise err

    if not managed:
        # We didn't own the value — leave claude.json alone, delete state.
        state_removed = _delete_state(state_path)
        return TeammateModeRevertResult(
            claude_json_path=claude_json_path,
            state_path=state_path,
            state_was_present=True,
            managed_by_us=False,
            restored_value=None,
            claude_json_touched=False,
            state_file_removed=state_removed,
        )

    claude_json, existed = _load_claude_json(claude_json_path)
    touched = False
    if original is None:
        if TEAMMATE_MODE_KEY in claude_json:
            claude_json.pop(TEAMMATE_MODE_KEY, None)
            touched = True
    else:
        if claude_json.get(TEAMMATE_MODE_KEY) != original:
            claude_json[TEAMMATE_MODE_KEY] = original
            touched = True

    claude_json_removed = False
    if touched:
        # "Leave no trace": if WE created the file AND it would now have no other
        # keys, delete the file rather than write `{}`. Only applies when existed
        # is True (we're mutating a file that's on disk right now) — but for the
        # `original is None, key was in claude_json` branch, existed must be True
        # since we loaded the key from it.
        if claude_json_was_ours and not claude_json:
            try:
                claude_json_path.unlink()
                claude_json_removed = True
            except FileNotFoundError:
                # Race with an external delete; treat as already-gone.
                claude_json_removed = True
            # touched remains True — the file WAS modified (by deletion).
        else:
            _write_claude_json(claude_json_path, claude_json)

    state_removed = _delete_state(state_path)

    # Remove our plugin-data dir if now empty. rmdir (not rmtree) so Python
    # refuses to recurse into a non-empty dir — safety by default. OSError
    # covers ENOTEMPTY (user placed their own files in our dir) and ENOENT
    # (dir never existed); both are treated as non-blocking best-effort.
    plugin_data_dir_removed = False
    plugin_data_dir = state_path.parent
    try:
        plugin_data_dir.rmdir()
        plugin_data_dir_removed = True
    except OSError:
        pass

    return TeammateModeRevertResult(
        claude_json_path=claude_json_path,
        state_path=state_path,
        state_was_present=True,
        managed_by_us=True,
        restored_value=original,
        claude_json_touched=touched,
        state_file_removed=state_removed,
        claude_json_removed=claude_json_removed,
        plugin_data_dir_removed=plugin_data_dir_removed,
    )



# ---------------------------------------------------------------------------
# Top-level install / uninstall
# ---------------------------------------------------------------------------


def install(
    *,
    settings_path: Path | str | None = None,
    argv0: str | None = None,
    shim_path: str | None = None,
    binary_path: str | None = None,
    claude_json_path: Path | str | None = None,
    state_path: Path | str | None = None,
    prompt_fn: Callable[[str], bool] | None = None,
    prereq_check_fn: Callable[[], PrereqCheck] | None = None,
    codex_cli_check_fn: Callable[[], CodexCliCheck] | None = None,
    gemini_cli_check_fn: Callable[[], GeminiCliCheck] | None = None,
    codex_auth_check_fn: Callable[[], AuthCheck] | None = None,
    gemini_auth_check_fn: Callable[[], AuthCheck] | None = None,
    force_empty: bool = False,
    no_allowlist: bool = False,
) -> InstallResult:
    """Full install: prereq check → env block write → teammateMode update.

    Install is all-or-nothing. If teammateMode can't be set (user declines the
    prompt), the env block written in this call is rolled back so the user
    is left in their original state.

    prereq_check_fn defaults to the real PATH probe. Tests inject a stub to
    exercise the found-or-missing branches without touching shutil.which.
    prompt_fn defaults to an auto-decline (False) so a scripted install that
    does not pass --assume-yes will fail loudly rather than hang; real TTY
    prompting is handled in cli.py.
    """
    # Collect BOTH prereqs before deciding to halt, so a user missing tmux AND
    # codex-cli sees a single combined report instead of fixing tmux first then
    # re-running only to hit a second surprise warning.
    checker = prereq_check_fn if prereq_check_fn is not None else _check_terminal_multiplexer
    codex_checker = codex_cli_check_fn if codex_cli_check_fn is not None else _check_codex_cli
    gemini_checker = gemini_cli_check_fn if gemini_cli_check_fn is not None else _check_gemini_cli
    prereq = checker()
    codex_cli = codex_checker()
    gemini_cli = gemini_checker()
    if not codex_cli.found:
        codex_auth = AuthCheck(signed_in=False, signed_in_detail=codex_cli.signed_in_detail)
    elif codex_auth_check_fn is not None:
        codex_auth = codex_auth_check_fn()
    elif codex_cli.signed_in or codex_cli.signed_in_detail is not None:
        codex_auth = AuthCheck(
            signed_in=codex_cli.signed_in,
            signed_in_detail=codex_cli.signed_in_detail,
        )
    else:
        codex_auth = _check_codex_auth()

    if not gemini_cli.found:
        gemini_auth = AuthCheck(signed_in=False, signed_in_detail=gemini_cli.signed_in_detail)
    elif gemini_auth_check_fn is not None:
        gemini_auth = gemini_auth_check_fn()
    elif gemini_cli.signed_in or gemini_cli.signed_in_detail is not None:
        gemini_auth = AuthCheck(
            signed_in=gemini_cli.signed_in,
            signed_in_detail=gemini_cli.signed_in_detail,
        )
    else:
        gemini_auth = _check_gemini_auth()
    codex_status = _codex_provider_status(codex_cli, codex_auth)
    gemini_status = _gemini_provider_status(gemini_cli, gemini_auth)

    if not prereq.found:
        message = (
            "claude-anyteam requires a terminal multiplexer on PATH; none was found.\n"
            "Install one of:\n"
            f"{_install_instructions(prereq.platform)}\n"
            "After installing, re-run `claude-anyteam install`."
        )
        codex_warning = _codex_cli_warning(codex_cli)
        if codex_warning is not None:
            message = f"{message}\n\nAdditionally:\n{codex_warning}"
        gemini_warning = _gemini_cli_warning(gemini_cli)
        if gemini_warning is not None:
            message = f"{message}\n\nAdditionally:\n{gemini_warning}"
        raise InstallError(message)

    if not _any_provider_ready(codex_status, gemini_status) and not force_empty:
        err = InstallError(_format_no_provider_ready_message(codex_status, gemini_status))
        err.cli_exit_code = INSTALL_ERROR_EXIT_NO_PROVIDER  # type: ignore[attr-defined]
        raise err

    paths = discover_managed_paths(
        settings_path=settings_path,
        argv0=argv0,
        shim_path=shim_path,
        binary_path=binary_path,
    )
    resolved_claude_json = (
        Path(claude_json_path).expanduser().resolve()
        if claude_json_path is not None
        else default_claude_json_path()
    )
    resolved_state_path = (
        Path(state_path).expanduser().resolve()
        if state_path is not None
        else default_state_path()
    )
    previous_state = _load_existing_state_for_install(resolved_state_path)

    settings, existed = _load_settings(paths.settings_path)
    pre_settings_snapshot = copy.deepcopy(settings)
    env = _env_block(settings, path=paths.settings_path, create=True)

    desired = {
        TEAMMATE_COMMAND_KEY: str(paths.shim_path),
        TEAMMATE_BINARY_KEY: str(paths.binary_path),
        GEMINI_TEAMMATE_BINARY_KEY: str(paths.binary_path.with_name("gemini-anyteam")),
    }
    changed: dict[str, str] = {}
    for key, value in desired.items():
        if env.get(key) != value:
            env[key] = value
            changed[key] = value

    removed_legacy: list[str] = []
    legacy_value = env.get(LEGACY_TEAMMATE_BINARY_KEY)
    if legacy_value is not None and _looks_managed(LEGACY_TEAMMATE_BINARY_KEY, legacy_value):
        env.pop(LEGACY_TEAMMATE_BINARY_KEY, None)
        removed_legacy.append(LEGACY_TEAMMATE_BINARY_KEY)

    (
        permissions_allow_added,
        permissions_created_now,
        permissions_allow_created_now,
    ) = _install_permission_allowlist(
        settings,
        path=paths.settings_path,
        no_allowlist=no_allowlist,
    )
    permissions_allow_managed = _merge_unique_preserving_order(
        _state_permissions_allow_added(previous_state),
        permissions_allow_added,
    )
    permissions_created_by_anyteam = _state_permissions_bool(
        previous_state,
        "permissions_created_by_anyteam",
    ) or permissions_created_now
    permissions_allow_created_by_anyteam = _state_permissions_bool(
        previous_state,
        "permissions_allow_created_by_anyteam",
    ) or permissions_allow_created_now

    settings_mutation = (
        bool(changed)
        or bool(removed_legacy)
        or bool(permissions_allow_added)
        or not existed
    )
    if settings_mutation:
        _write_settings(paths.settings_path, settings)
    effective_prompt = prompt_fn if prompt_fn is not None else (lambda _current: False)

    try:
        mode_result = install_teammate_mode(
            claude_json_path=resolved_claude_json,
            state_path=resolved_state_path,
            prompt_fn=effective_prompt,
            settings_file_created_by_anyteam=not existed,
            codex_cli=codex_cli,
            gemini_cli=gemini_cli,
            codex_auth=codex_auth,
            gemini_auth=gemini_auth,
            force_empty_used=force_empty,
            permissions_allow_added_by_anyteam=permissions_allow_managed,
            permissions_allowlist_skipped=no_allowlist,
            permissions_created_by_anyteam=permissions_created_by_anyteam,
            permissions_allow_created_by_anyteam=permissions_allow_created_by_anyteam,
        )
    except InstallError:
        _rollback_settings_file(
            path=paths.settings_path,
            pre_settings_snapshot=pre_settings_snapshot,
            pre_existed=existed,
        )
        raise

    return InstallResult(
        paths=paths,
        created_file=not existed,
        changed=changed,
        removed_legacy_keys=tuple(removed_legacy),
        prereq=prereq,
        teammate_mode=mode_result,
        codex_cli=codex_cli,
        gemini_cli=gemini_cli,
        codex_auth=codex_auth,
        gemini_auth=gemini_auth,
        codex_status=codex_status,
        gemini_status=gemini_status,
        force_empty_used=force_empty,
        permissions_allow_added=permissions_allow_added,
        permissions_allow_managed=permissions_allow_managed,
        permissions_allowlist_skipped=no_allowlist,
    )


def _rollback_settings_file(
    *,
    path: Path,
    pre_settings_snapshot: dict[str, Any],
    pre_existed: bool,
) -> None:
    """Best-effort rollback of settings.json mutations performed earlier in install().

    Called only on post-env-write failure (currently: teammateMode prompt declined).
    If the settings file did not exist before install(), we remove it entirely.
    Otherwise we restore the captured settings snapshot.
    """
    if not pre_existed:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return

    _write_settings(path, pre_settings_snapshot)



def _looks_managed(key: str, value: str) -> bool:
    basename = Path(value).name
    if key == TEAMMATE_COMMAND_KEY:
        return basename in MANAGED_SHIM_BASENAMES
    if key in MANAGED_BINARY_KEYS:
        return basename in MANAGED_BINARY_BASENAMES
    return False



def uninstall(
    *,
    settings_path: Path | str | None = None,
    claude_json_path: Path | str | None = None,
    state_path: Path | str | None = None,
) -> UninstallResult:
    """Reverse install(), leaving no trace of the three files install() touches.

    Specifically:
      1. Strip our keys from ~/.claude/settings.json's env block (leaves
         non-managed env keys and any other top-level keys alone).
      2. Revert ~/.claude.json's teammateMode to whatever the state file says
         was there before install, or remove the key if we added it from scratch.
      3. Delete the install-state file and rmdir the plugin-data dir if empty.
      4. "Leave no trace" escalation: if step 1 or 2 leaves a file that WE
         created (per state-file flags) with no remaining keys, unlink it.

    We read state BEFORE the settings.json unwind so we can apply the
    settings_file_created_by_anyteam flag after stripping our env keys.
    """
    raw_path = Path(settings_path) if settings_path is not None else default_settings_path()
    path = raw_path.expanduser().resolve()
    settings, existed = _load_settings(path)

    resolved_claude_json = (
        Path(claude_json_path).expanduser().resolve()
        if claude_json_path is not None
        else default_claude_json_path()
    )
    resolved_state_path = (
        Path(state_path).expanduser().resolve()
        if state_path is not None
        else default_state_path()
    )

    # Peek at the state file BEFORE uninstall_teammate_mode deletes it, so we
    # can apply the settings_file_created_by_anyteam flag to the settings.json
    # cleanup branch below. This is a read-only peek; the mode revert below
    # handles the authoritative delete.
    settings_file_was_ours = False
    permissions_allow_to_remove: tuple[str, ...] = ()
    permissions_created_by_anyteam = False
    permissions_allow_created_by_anyteam = False
    try:
        peeked_state = _load_state(resolved_state_path)
    except InstallError:
        # Malformed state — let uninstall_teammate_mode surface the error below.
        peeked_state = None
    if peeked_state is not None:
        settings_file_was_ours = bool(peeked_state.get("settings_file_created_by_anyteam", False))
        permissions_allow_to_remove = _state_permissions_allow_added_strict(
            peeked_state,
            state_path=resolved_state_path,
        )
        permissions_created_by_anyteam = bool(
            peeked_state.get("permissions_created_by_anyteam", False)
        )
        permissions_allow_created_by_anyteam = bool(
            peeked_state.get("permissions_allow_created_by_anyteam", False)
        )

    # teammateMode revert is independent of the env-block unwind and should
    # proceed whether or not settings.json exists. May raise InstallError
    # (cli_exit_code=4) on corrupted state; caller surfaces the message.
    mode_result = uninstall_teammate_mode(
        claude_json_path=resolved_claude_json,
        state_path=resolved_state_path,
    )

    if not existed:
        return UninstallResult(
            settings_path=path,
            removed={},
            skipped={},
            file_present=False,
            teammate_mode=mode_result,
            settings_file_removed=False,
            permissions_allow_removed=(),
        )

    env = _env_block(settings, path=path, create=False)
    removed: dict[str, str] = {}
    skipped: dict[str, str] = {}

    for key in (TEAMMATE_COMMAND_KEY, TEAMMATE_BINARY_KEY, GEMINI_TEAMMATE_BINARY_KEY, LEGACY_TEAMMATE_BINARY_KEY):
        value = env.get(key)
        if value is None:
            continue
        if _looks_managed(key, value):
            removed[key] = value
            env.pop(key, None)
        else:
            skipped[key] = value

    permissions_allow_removed = _remove_permission_allowlist_entries(
        settings,
        path=path,
        entries=permissions_allow_to_remove,
        permissions_created_by_anyteam=permissions_created_by_anyteam,
        allow_created_by_anyteam=permissions_allow_created_by_anyteam,
    )

    settings_file_removed = False
    settings_mutated = bool(removed) or bool(permissions_allow_removed)
    if settings_mutated:
        if not env:
            settings.pop("env", None)

        # "Leave no trace": if WE created settings.json AND it now has no
        # remaining keys, unlink it rather than writing `{}`. Safer than
        # comparing byte-length — we only care about logical emptiness.
        if settings_file_was_ours and not settings:
            try:
                path.unlink()
                settings_file_removed = True
            except FileNotFoundError:
                settings_file_removed = True
        else:
            _write_settings(path, settings)

    return UninstallResult(
        settings_path=path,
        removed=removed,
        skipped=skipped,
        file_present=True,
        teammate_mode=mode_result,
        settings_file_removed=settings_file_removed,
        permissions_allow_removed=permissions_allow_removed,
    )



# ---------------------------------------------------------------------------
# User-facing summary formatting
# ---------------------------------------------------------------------------

PROVIDER_STATUS_RULE = "─" * 45


def _provider_row(status: ProviderStatus) -> str:
    return f"{status.display_name:<14}{status.installed_cell():<18}{status.signin_cell()}"


def _provider_summary_entry(status: ProviderStatus) -> str:
    return status.summary_entry()


def _aggregate_summary_line(codex: ProviderStatus, gemini: ProviderStatus) -> str:
    if _any_provider_ready(codex, gemini):
        lead = "Ready"
    elif codex.state == "NEEDS_SIGNIN" or gemini.state == "NEEDS_SIGNIN":
        lead = "Almost ready"
    else:
        lead = "Not ready"
    return f"{lead}: {_provider_summary_entry(codex)} · {_provider_summary_entry(gemini)}."


def _format_provider_status_table(codex: ProviderStatus, gemini: ProviderStatus) -> str:
    return "\n".join(
        [
            "Provider status",
            PROVIDER_STATUS_RULE,
            f"{'':<14}{'Installed?':<18}{'Signed in?'}",
            _provider_row(codex),
            _provider_row(gemini),
            PROVIDER_STATUS_RULE,
            _aggregate_summary_line(codex, gemini),
        ]
    )


def _format_no_provider_explainer() -> str:
    return "\n".join(
        [
            "claude-anyteam routes some Claude Code teammates to external AI CLIs (Codex, Gemini).",
            "You need at least one signed-in CLI for it to do anything useful.",
            "Pick whichever you have access to.",
        ]
    )


def _format_codex_walkthrough(status: ProviderStatus) -> str:
    if status.state == "READY":
        return ""

    lines = ["Codex CLI:"]
    step = 1
    if status.state == "MISSING":
        lines.append(f"  {step}. Install:  {CODEX_CLI_INSTALL_COMMAND}")
        step += 1
    elif status.state == "NEEDS_UPGRADE":
        suffix = f" ({status.upgrade_hint})" if status.upgrade_hint else ""
        lines.append(
            f"  {step}. Upgrade:  {CODEX_CLI_INSTALL_COMMAND}{suffix}"
        )
        step += 1

    if status.state in ("MISSING", "NEEDS_SIGNIN"):
        lines.append(f"  {step}. Sign in:  {CODEX_CLI_BINARY}     (opens an OAuth flow on first run)")
    lines.append(f"  Docs: {CODEX_CLI_DOCS_URL}")
    return "\n".join(lines)


def _format_gemini_walkthrough(status: ProviderStatus) -> str:
    if status.state == "READY":
        return ""

    lines = ["Gemini CLI:"]
    step = 1
    if status.state == "MISSING":
        lines.append(f"  {step}. Install:  {GEMINI_CLI_INSTALL_COMMAND}")
        step += 1
    elif status.state == "NEEDS_UPGRADE":
        suffix = f" ({status.upgrade_hint})" if status.upgrade_hint else ""
        lines.append(f"  {step}. Upgrade:  {GEMINI_CLI_INSTALL_COMMAND}{suffix}")
        step += 1

    if status.state in ("MISSING", "NEEDS_SIGNIN"):
        lines.append(
            f"  {step}. Sign in:  {GEMINI_CLI_BINARY}    "
            "(or set GEMINI_API_KEY, or configure Vertex)"
        )
    lines.append(f"  Docs: {GEMINI_CLI_DOCS_URL}")
    return "\n".join(lines)


def _format_provider_walkthroughs(codex: ProviderStatus, gemini: ProviderStatus) -> str:
    blocks = [
        block
        for block in (_format_codex_walkthrough(codex), _format_gemini_walkthrough(gemini))
        if block
    ]
    return "\n\n".join(blocks)


def _format_no_provider_ready_message(codex: ProviderStatus, gemini: ProviderStatus) -> str:
    blocks = [
        _format_provider_status_table(codex, gemini),
        _format_no_provider_explainer(),
        _format_provider_walkthroughs(codex, gemini),
        (
            "Refusing to install — no provider is ready.\n"
            "  claude-anyteam needs at least one signed-in CLI (Codex or Gemini) to do anything\n"
            "  useful. Follow the steps above, then re-run `claude-anyteam install`.\n\n"
            "  Setting up later? Pass --force-empty to install with no provider ready:\n"
            "    claude-anyteam install --force-empty"
        ),
    ]
    return "\n\n".join(block for block in blocks if block)


def format_install_message(result: InstallResult) -> str:
    lines: list[str] = []

    codex_status = result.codex_status or _codex_provider_status(
        result.codex_cli or CodexCliCheck(found=False, path=None, version=None, raw_output=None),
        result.codex_auth or AuthCheck(signed_in=False),
    )
    gemini_status = result.gemini_status or _gemini_provider_status(
        result.gemini_cli or GeminiCliCheck(found=False, path=None, version=None, raw_output=None),
        result.gemini_auth or AuthCheck(signed_in=False),
    )

    if codex_status is not None and gemini_status is not None:
        lines.append(_format_provider_status_table(codex_status, gemini_status))
        if result.force_empty_used and not _any_provider_ready(codex_status, gemini_status):
            lines.append(_format_no_provider_explainer())
        walkthrough = _format_provider_walkthroughs(codex_status, gemini_status)
        if walkthrough:
            lines.append(walkthrough)
        if result.force_empty_used and not _any_provider_ready(codex_status, gemini_status):
            lines.append(
                "Proceeding with --force-empty: claude-anyteam is installed but inert until a CLI is ready."
            )

    receipt_lines = [
        f"Updated {result.paths.settings_path}",
        f"Set env.{TEAMMATE_COMMAND_KEY}={result.paths.shim_path}",
        f"Set env.{TEAMMATE_BINARY_KEY}={result.paths.binary_path}",
        f"Set env.{GEMINI_TEAMMATE_BINARY_KEY}={result.paths.binary_path.with_name('gemini-anyteam')}",
    ]
    if result.removed_legacy_keys:
        receipt_lines.append(f"Removed legacy env.{LEGACY_TEAMMATE_BINARY_KEY} entry.")

    mode = result.teammate_mode
    if mode is not None:
        if mode.wrote_value:
            if mode.previous_value is None:
                receipt_lines.append(f"Set {TEAMMATE_MODE_KEY}=\"tmux\" in {mode.claude_json_path}")
            else:
                receipt_lines.append(
                    f"Set {TEAMMATE_MODE_KEY}=\"tmux\" in {mode.claude_json_path} "
                    f"(was {mode.previous_value!r})"
                )
        else:
            receipt_lines.append(f"{TEAMMATE_MODE_KEY} already \"tmux\" in {mode.claude_json_path}; no change")

    if result.permissions_allowlist_skipped:
        receipt_lines.append("Permission allowlist skipped (--no-allowlist).")
    else:
        receipt_lines.append("Permission allowlist written so spawning teams won't prompt.")

    receipt_lines.append("Restart Claude Code for the changes to take effect. Use codex-* or gemini-* teammate names to route to the matching backend.")
    if not result.changed_anything:
        receipt_lines.insert(1, "The existing settings already matched this install.")
    lines.append("\n".join(receipt_lines))
    return "\n\n".join(lines)



def format_uninstall_message(result: UninstallResult) -> str:
    lines: list[str] = []

    removed_items = [f"env.{key}" for key in result.removed]
    if result.permissions_allow_removed:
        removed_items.append("permissions.allow entries")
    removed_summary = ", ".join(removed_items)

    if not result.file_present:
        lines.append(f"No settings file found at {result.settings_path}; nothing to remove.")
    elif result.settings_file_removed:
        lines.append(f"Removed {removed_summary}")
        lines.append(f"Deleted {result.settings_path} (empty after removal)")
    elif result.removed or result.permissions_allow_removed:
        lines.append(f"Updated {result.settings_path}")
        lines.append(f"Removed {removed_summary}")
    elif result.skipped:
        lines.append(f"Updated {result.settings_path}")
        lines.append("No claude-anyteam-managed env keys were removed; existing values were left intact.")
    else:
        lines.append(f"Updated {result.settings_path}")
        lines.append("No claude-anyteam env keys were present; existing settings were left intact.")

    mode = result.teammate_mode
    if mode is not None and mode.state_was_present:
        if mode.managed_by_us and mode.claude_json_removed:
            lines.append(f"Deleted {mode.claude_json_path} (empty after removal)")
        elif mode.managed_by_us and mode.claude_json_touched:
            if mode.restored_value is None:
                lines.append(f"Removed {TEAMMATE_MODE_KEY} from {mode.claude_json_path}")
            else:
                lines.append(
                    f"Restored {TEAMMATE_MODE_KEY}={mode.restored_value!r} in {mode.claude_json_path}"
                )
        elif not mode.managed_by_us:
            lines.append(f"{TEAMMATE_MODE_KEY} was not managed by claude-anyteam; left as-is")

    lines.append("Restart Claude Code for the changes to take effect.")
    return "\n".join(lines)
