"""Persistent Claude Code settings installer for claude-anyteam.

This writes the leader-side environment variables that Claude Code reads at
startup so users do not need to hand-edit ~/.claude/settings.json.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TEAMMATE_COMMAND_KEY = "CLAUDE_CODE_TEAMMATE_COMMAND"
TEAMMATE_BINARY_KEY = "CLAUDE_ANYTEAM_BINARY"
LEGACY_TEAMMATE_BINARY_KEY = "CODEX_TEAMMATE_BINARY"

SHIM_BASENAME = "claude-anyteam-spawn-shim"
LEGACY_SHIM_BASENAME = "codex-teammate-spawn-shim"
BINARY_BASENAME = "claude-anyteam"
LEGACY_BINARY_BASENAME = "codex-teammate"

MANAGED_BINARY_KEYS = (TEAMMATE_BINARY_KEY, LEGACY_TEAMMATE_BINARY_KEY)
MANAGED_SHIM_BASENAMES = {SHIM_BASENAME, LEGACY_SHIM_BASENAME}
MANAGED_BINARY_BASENAMES = {BINARY_BASENAME, LEGACY_BINARY_BASENAME}


@dataclass(frozen=True)
class ManagedPaths:
    settings_path: Path
    shim_path: Path
    binary_path: Path


@dataclass(frozen=True)
class InstallResult:
    paths: ManagedPaths
    created_file: bool
    changed: dict[str, str]
    removed_legacy_keys: tuple[str, ...] = ()

    @property
    def changed_anything(self) -> bool:
        return self.created_file or bool(self.changed) or bool(self.removed_legacy_keys)


@dataclass(frozen=True)
class UninstallResult:
    settings_path: Path
    removed: dict[str, str]
    skipped: dict[str, str]
    file_present: bool

    @property
    def changed_anything(self) -> bool:
        return bool(self.removed)


class InstallError(ValueError):
    """Raised when install/uninstall cannot safely update Claude settings."""



def default_settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"



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



def _write_settings(path: Path, settings: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(settings, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)
        raise



def install(
    *,
    settings_path: Path | str | None = None,
    argv0: str | None = None,
    shim_path: str | None = None,
    binary_path: str | None = None,
) -> InstallResult:
    paths = discover_managed_paths(
        settings_path=settings_path,
        argv0=argv0,
        shim_path=shim_path,
        binary_path=binary_path,
    )
    settings, existed = _load_settings(paths.settings_path)
    env = _env_block(settings, path=paths.settings_path, create=True)

    desired = {
        TEAMMATE_COMMAND_KEY: str(paths.shim_path),
        TEAMMATE_BINARY_KEY: str(paths.binary_path),
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

    if changed or removed_legacy or not existed:
        _write_settings(paths.settings_path, settings)

    return InstallResult(
        paths=paths,
        created_file=not existed,
        changed=changed,
        removed_legacy_keys=tuple(removed_legacy),
    )



def _looks_managed(key: str, value: str) -> bool:
    basename = Path(value).name
    if key == TEAMMATE_COMMAND_KEY:
        return basename in MANAGED_SHIM_BASENAMES
    if key in MANAGED_BINARY_KEYS:
        return basename in MANAGED_BINARY_BASENAMES
    return False



def uninstall(*, settings_path: Path | str | None = None) -> UninstallResult:
    raw_path = Path(settings_path) if settings_path is not None else default_settings_path()
    path = raw_path.expanduser().resolve()
    settings, existed = _load_settings(path)
    if not existed:
        return UninstallResult(
            settings_path=path,
            removed={},
            skipped={},
            file_present=False,
        )

    env = _env_block(settings, path=path, create=False)
    removed: dict[str, str] = {}
    skipped: dict[str, str] = {}

    for key in (TEAMMATE_COMMAND_KEY, TEAMMATE_BINARY_KEY, LEGACY_TEAMMATE_BINARY_KEY):
        value = env.get(key)
        if value is None:
            continue
        if _looks_managed(key, value):
            removed[key] = value
            env.pop(key, None)
        else:
            skipped[key] = value

    if removed:
        if not env:
            settings.pop("env", None)
        _write_settings(path, settings)

    return UninstallResult(
        settings_path=path,
        removed=removed,
        skipped=skipped,
        file_present=True,
    )



def format_install_message(result: InstallResult) -> str:
    lines = [
        f"Updated {result.paths.settings_path}",
        f"Set env.{TEAMMATE_COMMAND_KEY}={result.paths.shim_path}",
        f"Set env.{TEAMMATE_BINARY_KEY}={result.paths.binary_path}",
        "Restart Claude Code for the changes to take effect.",
    ]
    if result.removed_legacy_keys:
        lines.insert(3, f"Removed legacy env.{LEGACY_TEAMMATE_BINARY_KEY} entry.")
    if not result.changed_anything:
        lines.insert(1, "The existing settings already matched this install.")
    return "\n".join(lines)



def format_uninstall_message(result: UninstallResult) -> str:
    if not result.file_present:
        return "\n".join(
            [
                f"No settings file found at {result.settings_path}; nothing to remove.",
                "Restart Claude Code for the changes to take effect.",
            ]
        )

    if result.removed:
        removed_keys = ", ".join(f"env.{key}" for key in result.removed)
        return "\n".join(
            [
                f"Updated {result.settings_path}",
                f"Removed {removed_keys}",
                "Restart Claude Code for the changes to take effect.",
            ]
        )

    if result.skipped:
        return "\n".join(
            [
                f"Updated {result.settings_path}",
                "No claude-anyteam-managed env keys were removed; existing values were left intact.",
                "Restart Claude Code for the changes to take effect.",
            ]
        )

    return "\n".join(
        [
            f"Updated {result.settings_path}",
            "No claude-anyteam env keys were present; existing settings were left intact.",
            "Restart Claude Code for the changes to take effect.",
        ]
    )
