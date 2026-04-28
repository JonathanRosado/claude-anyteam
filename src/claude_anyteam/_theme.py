"""ANSI theme + box renderer for the Python installer's user-facing output.

Mirrors npm/lib/art.js so visual style is consistent across the npm wrapper
and the Python child it spawns. Pure stdlib — no new dependencies. Honors
LC_ALL=C / cmd-codepage with an ASCII glyph + border fallback.
"""

from __future__ import annotations

import os
import re
import sys
from typing import Iterable

_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _supports_unicode(env: dict | None = None, platform: str | None = None) -> bool:
    env = env if env is not None else os.environ
    if env.get("CLAUDE_ANYTEAM_ASCII") == "1" or env.get("CLAUDE_ANYTEAM_FORCE_ASCII") == "1":
        return False
    if env.get("CLAUDE_ANYTEAM_UNICODE") == "1" or env.get("CLAUDE_ANYTEAM_FORCE_UNICODE") == "1":
        return True
    locale = env.get("LC_ALL") or env.get("LC_CTYPE") or env.get("LANG") or ""
    if re.fullmatch(r"(?i)C|POSIX", locale):
        return False
    if re.search(r"(?i)UTF-?8", locale):
        return True
    plat = platform if platform is not None else sys.platform
    if plat == "win32":
        return bool(env.get("WT_SESSION") or env.get("TERM_PROGRAM") or env.get("ConEmuANSI") == "ON" or env.get("ANSICON"))
    return bool(locale)


def _supports_color(stream=None) -> bool:
    """Belt-and-suspenders color detection. Multiple positive signals.

    The Python installer is often spawned through `npx → uv tool run` on
    Windows, which means `sys.stdout.isatty()` returns False even when the
    user's PowerShell window is a real interactive terminal. Without a
    parent-injected signal, the user gets plain white text. So:

    - Any of CLAUDE_ANYTEAM_FORCE_COLOR, FORCE_COLOR, CLAUDE_ANYTEAM_NPM_PARENT
      forces color ON.
    - NO_COLOR / CLAUDE_ANYTEAM_NO_COLOR force OFF.
    - Modern Windows Terminal / VS Code / iTerm / etc. detected via env vars.
    - Otherwise fall back to isatty().
    """
    env = os.environ
    if env.get("NO_COLOR") or env.get("CLAUDE_ANYTEAM_NO_COLOR"):
        return False
    # Strongest positive signals — any one wins.
    if env.get("CLAUDE_ANYTEAM_FORCE_COLOR") or env.get("FORCE_COLOR"):
        _enable_windows_vt()
        return True
    if env.get("CLAUDE_ANYTEAM_NPM_PARENT") == "1":
        # Spawned by the npm wrapper, which already verified the user has a
        # real terminal. Trust that and color regardless of isatty().
        _enable_windows_vt()
        return True
    # Per-terminal env signals (modern Windows + popular emulators).
    if env.get("WT_SESSION") or env.get("TERM_PROGRAM") or env.get("ConEmuANSI") == "ON" or env.get("ANSICON"):
        _enable_windows_vt()
        return True
    target = stream if stream is not None else sys.stdout
    isatty = getattr(target, "isatty", lambda: False)()
    if isatty:
        _enable_windows_vt()
        return True
    return False


def _enable_windows_vt() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        for handle_id in (-11, -12):  # STDOUT, STDERR
            handle = kernel32.GetStdHandle(handle_id)
            mode = ctypes.c_ulong()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                kernel32.SetConsoleMode(handle, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:  # pragma: no cover - best-effort enable
        pass


def strip_ansi(value: str) -> str:
    return _ANSI_RE.sub("", str(value or ""))


_FG = {
    "black": 30, "red": 31, "green": 32, "yellow": 33,
    "blue": 34, "magenta": 35, "cyan": 36, "white": 37,
    "gray": 90, "bright_red": 91, "bright_green": 92, "bright_yellow": 93,
    "bright_blue": 94, "bright_magenta": 95, "bright_cyan": 96, "bright_white": 97,
}


class Theme:
    def __init__(self, color: bool, unicode: bool) -> None:
        self.color = color
        self.unicode = unicode
        self._symbols_unicode = {"success": "✔", "info": "●", "warn": "▲", "error": "✖", "bullet": "•", "arrow": "→"}
        self._symbols_ascii = {"success": "[OK]", "info": "[i]", "warn": "[!]", "error": "[X]", "bullet": "*", "arrow": "->"}

    def _wrap(self, value: str, code: int, *, bold: bool = False) -> str:
        if not self.color:
            return value
        prefix = "\x1b[1m" if bold else ""
        suffix = "\x1b[22m" if bold else ""
        return f"{prefix}\x1b[{code}m{value}\x1b[39m{suffix}"

    def fg(self, value: str, color: str, *, bold: bool = False) -> str:
        return self._wrap(value, _FG.get(color, 39), bold=bold)

    def heading(self, value: str) -> str:
        return self.fg(value, "white", bold=True)

    def accent(self, value: str) -> str:
        return self.fg(value, "cyan", bold=True)

    def success(self, value: str) -> str:
        return self.fg(value, "green", bold=True)

    def warn(self, value: str) -> str:
        return self.fg(value, "yellow", bold=True)

    def danger(self, value: str) -> str:
        return self.fg(value, "red", bold=True)

    def muted(self, value: str) -> str:
        return self.fg(value, "gray")

    @property
    def symbols(self) -> dict[str, str]:
        base = self._symbols_unicode if self.unicode else self._symbols_ascii
        if not self.color:
            return base
        return {
            "success": self.fg(base["success"], "green"),
            "info": self.fg(base["info"], "cyan"),
            "warn": self.fg(base["warn"], "yellow"),
            "error": self.fg(base["error"], "red"),
            "bullet": self.fg(base["bullet"], "gray"),
            "arrow": self.fg(base["arrow"], "cyan"),
        }


def get_theme(stream=None) -> Theme:
    return Theme(color=_supports_color(stream), unicode=_supports_unicode())


_BORDER_PALETTE = {"cyan": 36, "green": 32, "red": 31, "yellow": 33, "magenta": 35, "blue": 34, "gray": 90}
_BOX_MAX_WIDTH = 96
_BOX_MIN_WIDTH = 40


def _terminal_columns() -> int:
    cols_env = os.environ.get("COLUMNS")
    if cols_env and cols_env.isdigit():
        return int(cols_env)
    try:
        return os.get_terminal_size().columns
    except OSError:
        return 80


def _wrap_visible(row: str, width: int) -> list[str]:
    """Word-wrap a single row to at most width visible columns, preserving ANSI."""
    if len(strip_ansi(row)) <= width:
        return [row]
    tokens = re.split(r"(\s+)", row)
    lines: list[str] = []
    current = ""
    current_visible = 0

    def _flush() -> None:
        nonlocal current, current_visible
        if current:
            lines.append(current)
        current = ""
        current_visible = 0

    for tok in tokens:
        tok_visible = len(strip_ansi(tok))
        if tok_visible == 0:
            continue
        if tok_visible > width:
            _flush()
            remaining = tok
            while len(strip_ansi(remaining)) > width:
                cut, visible = 0, 0
                for ch in remaining:
                    if visible >= width:
                        break
                    cut += len(ch)
                    visible += 1
                lines.append(remaining[:cut])
                remaining = remaining[cut:]
            if strip_ansi(remaining):
                current = remaining
                current_visible = len(strip_ansi(remaining))
            continue
        if current_visible + tok_visible > width:
            _flush()
            if tok.isspace():
                continue
        current += tok
        current_visible += tok_visible
    _flush()
    return lines or [row]


def render_box(title: str, lines: Iterable[str], color: str = "cyan", *, theme: Theme | None = None) -> str:
    theme = theme or get_theme()
    code = _BORDER_PALETTE.get(color, 36)
    paint = (lambda v: f"\x1b[{code}m{v}\x1b[39m") if theme.color else (lambda v: v)
    if theme.unicode:
        b = {"tl": "╭", "tr": "╮", "ml": "├", "mr": "┤", "bl": "╰", "br": "╯", "h": "─", "v": "│"}
    else:
        b = {"tl": "+", "tr": "+", "ml": "+", "mr": "+", "bl": "+", "br": "+", "h": "-", "v": "|"}

    cols = _terminal_columns()
    budget = max(_BOX_MIN_WIDTH, min(_BOX_MAX_WIDTH, cols - 4))
    body_rows = [r for line in lines for r in str(line).split("\n") for r in _wrap_visible(r, budget)]
    title_rows = _wrap_visible(str(title), budget)
    rows = [*title_rows, *body_rows]
    width = max((len(strip_ansi(r)) for r in rows), default=0)

    def _fill(row: str) -> str:
        return f"{row}{' ' * (width - len(strip_ansi(row)))}"

    return "\n".join(
        [
            paint(f"{b['tl']}{b['h'] * (width + 2)}{b['tr']}"),
            *(f"{paint(b['v'])} {_fill(r)} {paint(b['v'])}" for r in title_rows),
            paint(f"{b['ml']}{b['h'] * (width + 2)}{b['mr']}"),
            *(f"{paint(b['v'])} {_fill(r)} {paint(b['v'])}" for r in body_rows),
            paint(f"{b['bl']}{b['h'] * (width + 2)}{b['br']}"),
        ]
    )


def section_header(theme: Theme, title: str, detail: str | None = None) -> str:
    head = f"{theme.symbols['info']} {theme.accent(title)}"
    if detail:
        head = f"{head} {theme.muted(detail)}"
    return head
