"""Live pretty-printer for the VisibilityEvent JSONL stream.

``claude-anyteam visibility-tail`` is intentionally a small filesystem-tail
projector over the existing visibility substrate.  It does not replace tmux
panes or introduce a socket server; it gives leads one optional structured
feed that can be attached mid-run.
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, TextIO

from pydantic import ValidationError

from .messages import VisibilityEvent
from . import protocol_io as pio


_ANSI_RESET = "\033[0m"
_SEVERITY_COLORS: dict[str, str] = {
    "debug": "\033[2m",
    "info": "\033[36m",
    "warn": "\033[33m",
    "error": "\033[31m",
}
_RELATIVE_SINCE_UNITS: dict[str, float] = {
    "s": 1,
    "sec": 1,
    "secs": 1,
    "second": 1,
    "seconds": 1,
    "m": 60,
    "min": 60,
    "mins": 60,
    "minute": 60,
    "minutes": 60,
    "h": 60 * 60,
    "hr": 60 * 60,
    "hrs": 60 * 60,
    "hour": 60 * 60,
    "hours": 60 * 60,
    "d": 24 * 60 * 60,
    "day": 24 * 60 * 60,
    "days": 24 * 60 * 60,
}
_CARD_INLINE_VALUE_LIMIT = 220
_CARD_MULTILINE_VALUE_LIMIT = 120
_CARD_MULTILINE_MAX_CHARS = 2000
_CARD_MULTILINE_MAX_LINES = 20
_CARD_MULTILINE_WRAP_WIDTH = 100


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="claude-anyteam visibility-tail",
        description=(
            "Follow the per-team VisibilityEvent JSONL stream and print a "
            "structured live feed. By default the command attaches at EOF, "
            "so old rows are not replayed unless --from-start is passed."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--team", required=True, type=_validate_name, help="Team name")
    p.add_argument(
        "--agent",
        type=_validate_name,
        help="Optional teammate name filter (defaults to all agents)",
    )
    p.add_argument(
        "--from-start",
        action="store_true",
        help="Replay existing rows before following (default: attach at current EOF)",
    )
    p.add_argument(
        "--no-follow",
        dest="follow",
        action="store_false",
        default=True,
        help="Print currently available rows and exit",
    )
    p.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI color even when stdout is a terminal",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit matching VisibilityEvent envelopes as JSON Lines",
    )
    p.add_argument(
        "--filter-kind",
        action="append",
        default=[],
        metavar="KIND[,KIND...]",
        help="Only print events whose kind matches one of the provided values",
    )
    p.add_argument(
        "--since",
        type=_parse_since_arg,
        metavar="WHEN",
        help=(
            "Only print events at or after WHEN. Accepts ISO-8601 timestamps "
            "(for example 2026-04-27T15:30:00Z) or relative durations such "
            "as 10m, 2h, or 1d."
        ),
    )
    p.add_argument(
        "--max-events",
        type=int,
        default=None,
        help="Stop after printing N matching events (useful for tests/scripts)",
    )
    p.add_argument(
        "--poll-s",
        type=float,
        default=0.2,
        help=argparse.SUPPRESS,
    )
    return p


def _validate_name(value: str) -> str:
    if not value or any(ch in value for ch in ("/", "\\", "\x00")):
        raise argparse.ArgumentTypeError(
            f"invalid name {value!r}: must be non-empty and contain no path separators"
        )
    if value in {".", ".."}:
        raise argparse.ArgumentTypeError(f"invalid name {value!r}")
    return value


def _parse_timestamp(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_since_arg(value: str) -> datetime:
    parsed = _parse_timestamp(value)
    if parsed is not None:
        return parsed

    text = value.strip().lower()
    number = ""
    unit = ""
    for ch in text:
        if ch.isdigit() or ch == ".":
            if unit:
                raise argparse.ArgumentTypeError(
                    f"invalid --since value {value!r}: expected ISO timestamp or duration"
                )
            number += ch
        elif ch.isalpha():
            unit += ch
        elif ch.isspace():
            continue
        else:
            raise argparse.ArgumentTypeError(
                f"invalid --since value {value!r}: expected ISO timestamp or duration"
            )
    if not number or not unit or unit not in _RELATIVE_SINCE_UNITS:
        raise argparse.ArgumentTypeError(
            f"invalid --since value {value!r}: expected ISO timestamp or duration"
        )
    try:
        amount = float(number)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid --since duration {value!r}"
        ) from exc
    if amount < 0:
        raise argparse.ArgumentTypeError(f"invalid --since duration {value!r}")
    return datetime.now(timezone.utc) - timedelta(
        seconds=amount * _RELATIVE_SINCE_UNITS[unit]
    )


def _split_filter_kinds(values: Iterable[str]) -> set[str] | None:
    kinds = {
        item.strip()
        for value in values
        for item in value.split(",")
        if item.strip()
    }
    return kinds or None


def _truncate(text: str, *, limit: int = _CARD_INLINE_VALUE_LIMIT) -> str:
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


def _value_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if value is None:
        return "null"
    if isinstance(value, str):
        if value == "":
            return '""'
        if any(ch.isspace() for ch in value) or any(ch in value for ch in "[]{}=::"):
            return _truncate(json.dumps(value, ensure_ascii=False))
        return _truncate(value)
    try:
        return _truncate(
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
    except (TypeError, ValueError):
        return _truncate(repr(value))


def _kv_pairs(pairs: Iterable[tuple[str, Any]]) -> str:
    rendered = [
        f"{key}={_value_text(value)}"
        for key, value in pairs
        if value is not None
    ]
    return " ".join(rendered) if rendered else "-"


def _full_value_text(value: Any) -> str:
    if isinstance(value, str):
        return value if value else '""'
    try:
        return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    except (TypeError, ValueError):
        return repr(value)


def _needs_multiline_value(value: Any) -> bool:
    if value is None:
        return False
    text = _full_value_text(value)
    return "\n" in text or len(text) > _CARD_MULTILINE_VALUE_LIMIT


def _wrap_preserving_indent(line: str) -> list[str]:
    if len(line) <= _CARD_MULTILINE_WRAP_WIDTH:
        return [line]
    indent = line[: len(line) - len(line.lstrip())]
    wrapped = textwrap.wrap(
        line,
        width=_CARD_MULTILINE_WRAP_WIDTH,
        subsequent_indent=indent,
        break_long_words=True,
        break_on_hyphens=False,
        replace_whitespace=False,
        drop_whitespace=False,
    )
    return wrapped or [line]


def _multiline_value_lines(value: Any) -> list[str]:
    text = _full_value_text(value)
    truncated = False
    if len(text) > _CARD_MULTILINE_MAX_CHARS:
        text = text[:_CARD_MULTILINE_MAX_CHARS].rstrip()
        truncated = True

    raw_lines = text.splitlines() or [""]
    lines: list[str] = []
    for line in raw_lines:
        lines.extend(_wrap_preserving_indent(line))

    if len(lines) > _CARD_MULTILINE_MAX_LINES:
        remaining = len(lines) - _CARD_MULTILINE_MAX_LINES
        lines = lines[:_CARD_MULTILINE_MAX_LINES]
        lines.append(f"… ({remaining} lines more)")
    if truncated:
        lines.append("… (truncated)")
    return lines


def _card(label: str, pairs: Iterable[tuple[str, Any]]) -> str:
    fields = [(key, value) for key, value in pairs if value is not None]
    if not fields:
        return f"[{label}] -"
    if not any(_needs_multiline_value(value) for _, value in fields):
        return f"[{label}] {_kv_pairs(fields)}"

    lines = [f"[{label}]"]
    for key, value in fields:
        if _needs_multiline_value(value):
            lines.append(f"  {key}:")
            lines.extend(f"    {line}" for line in _multiline_value_lines(value))
        else:
            lines.append(f"  {key}: {_value_text(value)}")
    return "\n".join(lines)


def _first_present(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = payload.get(key)
        if value is not None:
            return value
    return None


def _args_card(event: VisibilityEvent) -> str:
    payload = event.payload or {}
    if event.kind == "tool_event":
        args = _first_present(
            payload,
            "tool_args",
            "args",
            "arguments",
            "input",
            "target",
            "command",
            "query",
            "path",
        )
        fields = (
            ("tool", payload.get("tool_name") or payload.get("raw_backend_type")),
            ("category", payload.get("category")),
            ("phase", payload.get("phase")),
            ("args", args),
        )
    elif event.kind == "turn_started":
        fields = (
            ("mode", payload.get("mode")),
            ("prompt", payload.get("prompt_kind")),
            ("cwd", payload.get("cwd")),
            ("timeout_s", payload.get("timeout_s")),
            ("model", payload.get("model") or payload.get("effective_model")),
            ("effort", payload.get("effort")),
        )
    elif event.kind == "artifact_event":
        fields = (
            ("path", payload.get("path")),
            ("action", payload.get("action")),
            ("source", payload.get("source")),
        )
    elif event.kind in {"visibility_degraded", "turn_warning"}:
        fields = (
            ("surface", payload.get("surface")),
            ("phase", payload.get("phase")),
            ("task_id", payload.get("task_id") or event.task_id),
        )
    else:
        fields = (
            ("task_id", event.task_id),
            ("turn_id", event.turn_id),
            ("surface", payload.get("surface")),
            ("phase", payload.get("phase")),
        )
    return _card("ARGS", fields)


def _result_card(event: VisibilityEvent) -> str:
    payload = event.payload or {}
    if event.kind == "tool_event":
        fields = (
            ("status", payload.get("status")),
            ("exit_code", payload.get("exit_code")),
            ("duration_ms", payload.get("duration_ms")),
            ("stdout", payload.get("stdout_preview")),
        )
        if not any(value is not None for _, value in fields):
            fields = (("phase", payload.get("phase") or "observed"),)
        return _card("RESULT", fields)
    if event.kind in {"turn_completed", "turn_failed"}:
        fields = (
            ("exit_code", payload.get("exit_code")),
            ("elapsed_s", payload.get("elapsed_s")),
            ("structured", payload.get("structured")),
            ("events", payload.get("event_count")),
            ("tool_calls", payload.get("tool_call_events")),
        )
        return _card("RESULT", fields)
    if event.kind == "turn_started":
        return "[RESULT] started"
    if event.kind == "artifact_event":
        return _card("RESULT", (("action", payload.get("action")),))
    if event.kind == "agent_registered":
        return "[RESULT] registered"
    return _card("RESULT", (("severity", event.severity),))


def _error_card(event: VisibilityEvent) -> str | None:
    payload = event.payload or {}
    error = _first_present(
        payload,
        "error",
        "error_message",
        "message",
        "stderr_preview",
    )
    error_class = _first_present(payload, "error_class", "error_type", "class")
    failed_status = payload.get("status") in {"error", "failed", "failure"}
    if error is None and event.severity == "error":
        error = event.summary
    if error is None and error_class is None and not failed_status:
        return None
    fields = (
        ("class", error_class),
        ("status", payload.get("status") if failed_status else None),
        ("error", error),
    )
    return _card("ERROR", fields)


def format_event(event: VisibilityEvent, *, color: bool = True) -> str:
    """Render one VisibilityEvent as a tri-card row or expanded card block."""

    kind = event.kind
    severity = event.severity.upper()
    if color:
        severity_color = _SEVERITY_COLORS.get(event.severity, "")
        if severity_color:
            severity = f"{severity_color}{severity}{_ANSI_RESET}"
            kind = f"{severity_color}{event.kind}{_ANSI_RESET}"
    prefix = (
        f"{event.timestamp} seq={event.seq} {event.agent} "
        f"{event.backend} {severity} {kind}"
    )
    cards = [_args_card(event), _result_card(event)]
    error = _error_card(event)
    if error:
        cards.append(error)
    if not any("\n" in card for card in cards):
        return " ".join([prefix, *cards, f":: {event.summary}"])

    lines = [f"{prefix} :: {event.summary}"]
    for card in cards:
        lines.extend(f"  {line}" for line in card.splitlines())
    return "\n".join(lines)


@dataclass
class _TailState:
    path: Path
    offset: int = 0
    buffer: str = ""

    @classmethod
    def create(cls, path: Path, *, from_start: bool, initial: bool) -> "_TailState":
        offset = 0
        if initial and not from_start:
            try:
                offset = path.stat().st_size
            except FileNotFoundError:
                offset = 0
        return cls(path=path, offset=offset)

    def read_lines(self) -> list[str]:
        try:
            size = self.path.stat().st_size
        except FileNotFoundError:
            return []
        if size < self.offset:
            # Log rotation/truncation: start over rather than seeking past EOF.
            self.offset = 0
            self.buffer = ""
        try:
            with self.path.open("r", encoding="utf-8", errors="replace") as f:
                f.seek(self.offset)
                chunk = f.read()
                self.offset = f.tell()
        except FileNotFoundError:
            return []
        if not chunk:
            return []
        data = self.buffer + chunk
        pieces = data.splitlines(keepends=True)
        complete: list[str] = []
        self.buffer = ""
        for piece in pieces:
            if piece.endswith(("\n", "\r")):
                complete.append(piece.rstrip("\r\n"))
            else:
                self.buffer = piece
        return complete


def _events_dir_for_team(team: str) -> Path:
    return pio.team_visibility_event_path(team).parent / "events"


def _discover_paths(team: str, agent: str | None) -> list[Path]:
    paths = [pio.team_visibility_event_path(team)]
    if agent:
        paths.append(pio.visibility_event_path(team, agent))
        return list(dict.fromkeys(paths))

    events_dir = _events_dir_for_team(team)
    try:
        agent_paths = sorted(
            path
            for path in events_dir.glob("*.jsonl")
            if path.is_file() and not path.name.startswith(".")
        )
    except OSError:
        agent_paths = []
    paths.extend(agent_paths)
    return list(dict.fromkeys(paths))


def tail_events(
    *,
    team: str,
    agent: str | None = None,
    filter_kinds: set[str] | None = None,
    since: datetime | None = None,
    from_start: bool = False,
    follow: bool = True,
    poll_s: float = 0.2,
    max_events: int | None = None,
    stderr: TextIO | None = None,
) -> Iterable[VisibilityEvent]:
    """Yield VisibilityEvents from the live aggregate stream plus fallback logs.

    The aggregate ``visibility.jsonl`` is the intended attach point.  Per-agent
    ``events/<agent>.jsonl`` files are also watched as a backwards-compatible
    fallback and to discover streams created before this CLI existed.  Events
    are de-duplicated by ``event_id`` when both surfaces contain the same row.
    """

    if max_events is not None and max_events <= 0:
        return

    err = stderr if stderr is not None else sys.stderr
    states: dict[Path, _TailState] = {}
    seen: set[str] = set()
    emitted = 0
    initial_discovery = True

    while True:
        for path in _discover_paths(team, agent):
            if path not in states:
                states[path] = _TailState.create(
                    path,
                    from_start=from_start,
                    initial=initial_discovery,
                )
        initial_discovery = False

        for path in sorted(states):
            for line in states[path].read_lines():
                if not line.strip():
                    continue
                try:
                    raw = json.loads(line)
                    event = VisibilityEvent.model_validate(raw)
                except (json.JSONDecodeError, ValidationError, TypeError) as exc:
                    err.write(f"warning: skipped malformed visibility row in {path}: {exc}\n")
                    err.flush()
                    continue
                if agent and event.agent != agent:
                    continue
                if filter_kinds is not None and event.kind not in filter_kinds:
                    continue
                if since is not None:
                    event_time = _parse_timestamp(event.timestamp)
                    if event_time is None or event_time < since:
                        continue
                if event.event_id in seen:
                    continue
                seen.add(event.event_id)
                yield event
                emitted += 1
                if max_events is not None and emitted >= max_events:
                    return

        if not follow:
            return
        time.sleep(max(0.01, poll_s))


def main(
    argv: list[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    args = _build_parser().parse_args(argv)
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    filter_kinds = _split_filter_kinds(args.filter_kind)
    color = (
        not args.json
        and not args.no_color
        and hasattr(out, "isatty")
        and bool(out.isatty())
    )
    try:
        for event in tail_events(
            team=args.team,
            agent=args.agent,
            filter_kinds=filter_kinds,
            since=args.since,
            from_start=args.from_start,
            follow=args.follow,
            poll_s=args.poll_s,
            max_events=args.max_events,
            stderr=err,
        ):
            if args.json:
                out.write(event.model_dump_json(by_alias=True, exclude_none=True) + "\n")
            else:
                out.write(format_event(event, color=color) + "\n")
            out.flush()
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
