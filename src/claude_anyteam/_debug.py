"""Debug logger for the installer.

Activated by setting CLAUDE_ANYTEAM_DEBUG=1 (or passing --debug to the CLI).
Prints every interesting checkpoint to stderr with a `[debug] ` prefix so the
user can grep / paste it back when diagnosing Windows-specific issues that
the maintainer can't reproduce on their own machine.

What gets logged when active:
- npm wrapper version vs Python-tool version (resolved separately)
- Every subprocess call: argv, exit code, first 800 chars of stdout/stderr
- PATH state at install start + after every refresh
- PATHEXT (Windows) and locale env vars
- shutil.which() result for every probe
- uv tool dir / uv tool dir --bin
- Each provider check's resolved binary path (or "not found")
- Selected env vars: CLAUDE_ANYTEAM_*, FORCE_COLOR, NO_COLOR
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any, Iterable

_PREFIX = "\x1b[35m[debug]\x1b[39m " if sys.stderr.isatty() else "[debug] "
_ENABLED: bool | None = None


def debug_enabled() -> bool:
    global _ENABLED
    if _ENABLED is None:
        _ENABLED = os.environ.get("CLAUDE_ANYTEAM_DEBUG", "").lower() in ("1", "true", "yes", "on")
    return _ENABLED


def force_enable() -> None:
    """Used by --debug CLI flag — sets env var so child processes inherit."""
    global _ENABLED
    _ENABLED = True
    os.environ["CLAUDE_ANYTEAM_DEBUG"] = "1"


def log(*parts: Any) -> None:
    if not debug_enabled():
        return
    ts = time.strftime("%H:%M:%S")
    msg = " ".join(str(p) for p in parts)
    print(f"{_PREFIX}{ts} {msg}", file=sys.stderr)


def log_subprocess(argv: list[str], result: Any, *, label: str = "exec") -> None:
    if not debug_enabled():
        return
    code = getattr(result, "returncode", "?")
    out = getattr(result, "stdout", "") or ""
    err = getattr(result, "stderr", "") or ""
    log(f"{label}: argv={argv!r} exit={code}")
    if out:
        log(f"  stdout[:800]={out[:800]!r}")
    if err:
        log(f"  stderr[:800]={err[:800]!r}")


def log_env_snapshot(*, label: str = "env") -> None:
    if not debug_enabled():
        return
    keys = [
        "CLAUDE_ANYTEAM_DEBUG",
        "CLAUDE_ANYTEAM_NPM_PARENT",
        "CLAUDE_ANYTEAM_NPM_VERSION",
        "CLAUDE_ANYTEAM_FORCE_COLOR",
        "CLAUDE_ANYTEAM_ASCII",
        "FORCE_COLOR",
        "NO_COLOR",
        "PATHEXT",
        "PYTHONUTF8",
        "PYTHONIOENCODING",
        "WT_SESSION",
        "TERM_PROGRAM",
        "ConEmuANSI",
        "LC_ALL",
        "LANG",
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
    ]
    log(f"--- {label} (selected env) ---")
    for k in keys:
        v = os.environ.get(k)
        if v is not None:
            log(f"  {k}={v}")
    path = os.environ.get("PATH", "")
    parts = path.split(os.pathsep) if path else []
    log(f"  PATH ({len(parts)} entries; first 5 + last 3):")
    for p in parts[:5]:
        log(f"    {p}")
    if len(parts) > 8:
        log("    ...")
        for p in parts[-3:]:
            log(f"    {p}")
    log(f"  sys.platform={sys.platform!r}, sys.version={sys.version.split()[0]}")
    log(f"  sys.stdin.isatty()={sys.stdin.isatty()}, sys.stdout.isatty()={sys.stdout.isatty()}, sys.stderr.isatty()={sys.stderr.isatty()}")


def log_which(name: str, result: str | None) -> None:
    if not debug_enabled():
        return
    log(f"shutil.which({name!r}) -> {result!r}")
