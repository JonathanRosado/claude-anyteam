#!/usr/bin/env python3
"""Score Phase-3 collaboration metrics from visibility event logs.

The scorer is intentionally read-only: it consumes the R16/R17 visibility
substrate and emits post-run JSON scorecards for §3 peer efficiency metrics.
"""

from __future__ import annotations

import argparse
import ast
import json
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from claude_anyteam import protocol_io as pio
from claude_anyteam.messages import VisibilityEvent
from claude_teams import messaging as cs_messaging  # type: ignore[import-untyped]

SCHEMA_VERSION = 1
TEAM_LEAD = "team-lead"
RTT_CAP_SECONDS = 600.0
DELIVERED_STEER_VALUES = {"delivered_mid_turn", "delivered_next_turn"}
KNOWN_STEER_VALUES = {
    "delivered_mid_turn",
    "delivered_next_turn",
    "queued",
    "expired",
    "dropped",
}
SEMANTIC_LABELS = ("ask", "answer", "handoff", "fyi", "other")
CLASSIFIER_METHOD = "prefix_v1"
SEMANTIC_PREFIXES = {
    "ask:": "ask",
    "answer:": "answer",
    "handoff:": "handoff",
    "fyi:": "fyi",
}
BLOCKING_CANDIDATE_SEMANTICS = {"ask", "handoff"}
NONBLOCKING_SEMANTICS = {"answer", "fyi"}
COUPLING_INTENT_ALIASES = {
    "tight": "tight_peer_loop",
    "tight_peer_loop": "tight_peer_loop",
    "loose": "loose_parallel",
    "loose_parallel": "loose_parallel",
    "batched_async": "batched_async",
}


@dataclass(frozen=True)
class SendMessageEvent:
    event: VisibilityEvent
    sender: str
    recipient: str | None
    timestamp: datetime | None
    semantic: str

    @property
    def is_peer(self) -> bool:
        return bool(self.recipient) and self.recipient not in {TEAM_LEAD, self.sender, "*"}


@dataclass(frozen=True)
class TerminalEvent:
    event: VisibilityEvent
    timestamp: datetime | None
    collision: bool


@dataclass(frozen=True)
class M13Attribution:
    terminal: TerminalEvent
    sender: str
    sender_backend: str
    recipient: str
    recipient_backend: str
    structured_reply_ts: datetime | None
    prose_fallback_ts: datetime | None
    terminal_event_kind: str

    @property
    def backend_key(self) -> str:
        return backend_pair_key(self.sender_backend, self.recipient_backend)

    def as_dict(self) -> dict[str, Any]:
        inter_event_ms: int | None = None
        if self.structured_reply_ts is not None and self.prose_fallback_ts is not None:
            inter_event_ms = int(
                round(
                    (self.prose_fallback_ts - self.structured_reply_ts).total_seconds()
                    * 1000
                )
            )
        turn_id = self.terminal.event.turn_id or "N/A"
        structured_reply_ts = (
            self.structured_reply_ts.isoformat().replace("+00:00", "Z")
            if self.structured_reply_ts is not None
            else "N/A"
        )
        prose_fallback_ts = (
            self.prose_fallback_ts.isoformat().replace("+00:00", "Z")
            if self.prose_fallback_ts is not None
            else "N/A"
        )
        return {
            "turn_id": turn_id,
            "terminal_event_id": self.terminal.event.event_id,
            # Kept as a sender-backend alias for the original M13 attribution
            # sketch; the explicit sender/recipient backend fields below are
            # the authoritative disambiguators for mixed-backend runs.
            "backend": self.sender_backend,
            "sender": self.sender,
            "sender_backend": self.sender_backend,
            "recipient": self.recipient,
            "recipient_backend": self.recipient_backend,
            "structured_reply_seen": self.structured_reply_ts is not None,
            "prose_fallback_seen": self.terminal.collision,
            "structured_reply_ts": structured_reply_ts,
            "prose_fallback_ts": prose_fallback_ts,
            "inter_event_ms": inter_event_ms,
            "terminal_event_kind": self.terminal_event_kind,
        }


@dataclass
class RttResult:
    by_pair: dict[tuple[str, str], list[float]]
    unmatched_by_pair: Counter[tuple[str, str]]
    by_sender_semantic: dict[tuple[str, str], list[float]]
    unmatched_by_sender_semantic: Counter[tuple[str, str]]
    self_dm_warnings: int = 0


@dataclass
class CollabDataset:
    events_by_agent: dict[str, list[VisibilityEvent]]
    events_dir: Path


class ScoreInputError(RuntimeError):
    pass


class ScoreOutputError(RuntimeError):
    pass


def warn(message: str) -> None:
    print(f"score_collab: warning: {message}", file=sys.stderr)


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        warn(f"invalid timestamp {value!r}; event skipped for time-based metrics")
        return None


def stats(samples: Iterable[float], *, include_unmatched: int | None = None) -> dict[str, Any]:
    values = sorted(float(v) for v in samples)
    p95 = None
    if len(values) >= 5:
        # Historical scorer contract uses stdlib's exclusive percentile.
        # Clamp to the observed maximum so undersampled pair-level buckets
        # cannot report an impossible p95 above max.
        p95 = min(statistics.quantiles(values, n=20)[18], max(values))
    out: dict[str, Any] = {
        "mean": round(statistics.mean(values), 3) if values else None,
        "median": round(statistics.median(values), 3) if values else None,
        "p95": round(p95, 3) if p95 is not None else None,
        "samples": len(values),
    }
    if include_unmatched is not None:
        out["unmatched_send_count"] = int(include_unmatched)
    return out


def percentile_triplet(samples: Iterable[float], *, include_unmatched: int | None = None) -> dict[str, Any]:
    """p50/p95/max shape for M11a RTT distributions.

    Percentiles are computed across *matched RTT samples only*. Unmatched
    sends are exposed separately as ``unmatched_send_count`` so p95 cannot be
    inflated or deflated by synthetic cap values.
    """

    values = sorted(float(v) for v in samples)
    enough = len(values) >= 5
    quantiles = statistics.quantiles(values, n=20) if enough else []
    p95 = min(quantiles[18], max(values)) if enough else None
    out: dict[str, Any] = {
        "p50": round(quantiles[9], 3) if enough else None,
        "p95": round(p95, 3) if p95 is not None else None,
        "max": round(max(values), 3) if values else None,
        "samples": len(values),
    }
    if include_unmatched is not None:
        out["unmatched_send_count"] = int(include_unmatched)
    return out


def ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 3)


def normalize_coupling_intent(raw: Any) -> str | None:
    """Normalize scorer-only legacy aliases for coupling intent.

    New workload manifests should use the canonical nested form
    ``{"coupling": {"intent": "tight_peer_loop" | "loose_parallel" |
    "batched_async"}}``. This scorer accepts legacy string aliases
    (``"tight"``/``"loose"``) only while interpreting old run fixtures; the
    workload loader should not grow parallel schema spellings.
    """

    if raw in (None, ""):
        return None
    if isinstance(raw, dict):
        raw = raw.get("intent")
    value = str(raw)
    normalized = COUPLING_INTENT_ALIASES.get(value)
    if normalized is None:
        warn(f"unknown coupling intent {value!r}; compliance checks disabled")
    return normalized


def classification_coverage(counter: Counter[str], total_peer_sends: int) -> float | None:
    classified = sum(counter.get(label, 0) for label in SEMANTIC_LABELS if label != "other")
    return ratio(classified, total_peer_sends)


def classify_semantic(summary: str | None, payload: dict[str, Any]) -> str:
    """Coarse semantic label for peer-DM summaries.

    ``prefix_v1`` is intentionally strict: only explicit ``ask:``,
    ``answer:``, ``handoff:``, and ``fyi:`` prefixes classify. Older W7 runs
    without prefix discipline should remain ``other`` rather than getting
    silently reinterpreted by heuristic content matching.
    """

    candidates = (
        payload.get("message_summary"),
        payload.get("summary"),
        payload.get("tool_summary"),
        summary,
    )
    for candidate in candidates:
        candidate_text = str(candidate or "").strip().lower()
        for prefix, label in SEMANTIC_PREFIXES.items():
            if candidate_text.startswith(prefix):
                return label
    return "other"


def _tool_name(event: VisibilityEvent) -> str | None:
    payload = event.payload or {}
    value = payload.get("tool_name") or payload.get("name") or payload.get("raw_backend_type")
    return str(value) if value is not None else None


def is_send_message_event(event: VisibilityEvent) -> bool:
    if event.kind != "tool_event" or _tool_name(event) != "send_message":
        return False
    # R18 wrapper instrumentation emits started + terminal events. Count calls,
    # not lifecycle edges, by ignoring the non-terminal started record.
    return event.payload.get("phase") != "started"


def is_tool_call_event(event: VisibilityEvent, tool_name: str) -> bool:
    return (
        event.kind == "tool_event"
        and _tool_name(event) == tool_name
        and event.payload.get("phase") != "started"
    )


def recipient_from_payload(payload: dict[str, Any]) -> str | None:
    value = payload.get("recipient")
    if value not in (None, ""):
        return str(value)

    target = payload.get("target")
    if target not in (None, ""):
        text = str(target).strip()
        if text.startswith("to="):
            raw = text[3:].strip()
            try:
                parsed = ast.literal_eval(raw)
                if parsed not in (None, ""):
                    return str(parsed)
            except (ValueError, SyntaxError):
                return raw.strip("'\"") or None
        return text or None

    for key in ("to", "delivered_to"):
        value = payload.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def steer_delivery(payload: dict[str, Any]) -> str:
    raw = payload.get("delivery") or payload.get("status") or payload.get("state")
    if raw in (None, ""):
        return "_unknown:missing"
    value = str(raw)
    if value not in KNOWN_STEER_VALUES:
        return f"_unknown:{value}"
    return value


def is_collision(event: VisibilityEvent) -> bool:
    if event.kind not in {"turn_completed", "turn_failed"}:
        return False
    payload = event.payload or {}
    if payload.get("structured") is True:
        return False
    try:
        tool_calls = int(payload.get("tool_call_events") or 0)
    except (TypeError, ValueError):
        tool_calls = 0
    preview = str(payload.get("last_message_preview") or "").strip()
    return tool_calls > 0 and len(preview) > 32


def list_agent_names(events_dir: Path) -> list[str]:
    return sorted(path.stem for path in events_dir.glob("*.jsonl") if path.is_file())


def read_events_via_protocol(team: str, agent: str) -> list[VisibilityEvent]:
    try:
        return pio.read_events(team, agent)
    except Exception as exc:
        raise ScoreInputError(f"failed to read events for {team}/{agent}: {exc}") from exc


def load_dataset(*, team: str | None, events_dir: Path | None) -> CollabDataset:
    if (team is None) == (events_dir is None):
        raise ScoreInputError("provide exactly one of --team or --events-dir")

    if team is not None:
        resolved_events_dir = cs_messaging.TEAMS_DIR / team / "events"
        if not resolved_events_dir.exists():
            raise ScoreInputError(f"events dir not found: {resolved_events_dir}")
        agents = list_agent_names(resolved_events_dir)
        if not agents:
            raise ScoreInputError(f"no event logs found in {resolved_events_dir}")
        return CollabDataset(
            events_by_agent={agent: read_events_via_protocol(team, agent) for agent in agents},
            events_dir=resolved_events_dir,
        )

    assert events_dir is not None
    resolved_events_dir = events_dir.expanduser().resolve()
    if not resolved_events_dir.exists() or not resolved_events_dir.is_dir():
        raise ScoreInputError(f"events dir not found: {resolved_events_dir}")
    agents = list_agent_names(resolved_events_dir)
    if not agents:
        raise ScoreInputError(f"no event logs found in {resolved_events_dir}")

    if resolved_events_dir.name == "events" and resolved_events_dir.parent.name:
        inferred_team = resolved_events_dir.parent.name
        inferred_teams_dir = resolved_events_dir.parent.parent
        old_teams_dir = cs_messaging.TEAMS_DIR
        try:
            cs_messaging.TEAMS_DIR = inferred_teams_dir
            events_by_agent = {
                agent: read_events_via_protocol(inferred_team, agent)
                for agent in agents
            }
        finally:
            cs_messaging.TEAMS_DIR = old_teams_dir
        return CollabDataset(events_by_agent=events_by_agent, events_dir=resolved_events_dir)

    raise ScoreInputError(
        "--events-dir must point at a '<team>/events' directory so "
        "protocol_io.read_events(team, agent) remains the sole JSONL reader"
    )


def extract_send_messages(events_by_agent: dict[str, list[VisibilityEvent]]) -> list[SendMessageEvent]:
    sends: list[SendMessageEvent] = []
    for agent, events in events_by_agent.items():
        for event in events:
            if not is_send_message_event(event):
                continue
            payload = event.payload or {}
            recipient = recipient_from_payload(payload)
            if recipient is None:
                warn(f"send_message missing recipient excluded from M3 numerator: {event.event_id}")
            sender = event.agent or agent
            if recipient == sender:
                warn(f"self-DM excluded from peer metrics: {event.event_id} {sender}->{recipient}")
            sends.append(
                SendMessageEvent(
                    event=event,
                    sender=sender,
                    recipient=recipient,
                    timestamp=parse_timestamp(event.timestamp),
                    semantic=classify_semantic(event.summary, payload),
                )
            )
    return sends


def extract_terminal_events(events_by_agent: dict[str, list[VisibilityEvent]]) -> list[TerminalEvent]:
    terminals: list[TerminalEvent] = []
    for events in events_by_agent.values():
        for event in events:
            if event.kind in {"turn_completed", "turn_failed"}:
                terminals.append(
                    TerminalEvent(
                        event=event,
                        timestamp=parse_timestamp(event.timestamp),
                        collision=is_collision(event),
                    )
                )
    return terminals


def compute_rtt(send_messages: list[SendMessageEvent]) -> RttResult:
    peer_sends = [s for s in send_messages if s.is_peer]
    sends_by_pair: dict[tuple[str, str], list[SendMessageEvent]] = defaultdict(list)
    replies_by_pair: dict[tuple[str, str], list[SendMessageEvent]] = defaultdict(list)
    self_dm_warnings = sum(1 for s in send_messages if s.recipient == s.sender and s.recipient is not None)

    for send in peer_sends:
        assert send.recipient is not None
        sends_by_pair[(send.sender, send.recipient)].append(send)
        replies_by_pair[(send.recipient, send.sender)].append(send)

    by_pair: dict[tuple[str, str], list[float]] = defaultdict(list)
    unmatched_by_pair: Counter[tuple[str, str]] = Counter()
    by_sender_semantic: dict[tuple[str, str], list[float]] = defaultdict(list)
    unmatched_by_sender_semantic: Counter[tuple[str, str]] = Counter()

    for pair, sends in sends_by_pair.items():
        replies = replies_by_pair.get(pair, [])
        replies = sorted(
            [r for r in replies if r.timestamp is not None],
            key=lambda r: r.timestamp,  # type: ignore[arg-type,return-value]
        )
        consumed: set[int] = set()
        for send in sorted(
            sends,
            key=lambda s: s.timestamp or datetime.max.replace(tzinfo=timezone.utc),
        ):
            semantic_key = (send.sender, send.semantic)
            if send.timestamp is None:
                unmatched_by_pair[pair] += 1
                unmatched_by_sender_semantic[semantic_key] += 1
                continue
            matched_delta: float | None = None
            matched_index: int | None = None
            for idx, reply in enumerate(replies):
                if idx in consumed:
                    continue
                if reply.timestamp is None:
                    continue
                if reply.timestamp <= send.timestamp:
                    continue
                delta = (reply.timestamp - send.timestamp).total_seconds()
                matched_delta = delta
                matched_index = idx
                break
            if matched_delta is not None and matched_delta <= RTT_CAP_SECONDS:
                by_pair[pair].append(float(matched_delta))
                by_sender_semantic[semantic_key].append(float(matched_delta))
                assert matched_index is not None
                consumed.add(matched_index)
            else:
                unmatched_by_pair[pair] += 1
                unmatched_by_sender_semantic[semantic_key] += 1

    return RttResult(
        by_pair=dict(by_pair),
        unmatched_by_pair=unmatched_by_pair,
        by_sender_semantic=dict(by_sender_semantic),
        unmatched_by_sender_semantic=unmatched_by_sender_semantic,
        self_dm_warnings=self_dm_warnings,
    )


def delivery_breakdown(events: Iterable[VisibilityEvent]) -> Counter[str]:
    counter: Counter[str] = Counter()
    for event in events:
        if event.kind == "steer_ack":
            counter[steer_delivery(event.payload or {})] += 1
    return counter


def delivery_rate_parts(counter: Counter[str]) -> tuple[int, int, int, float | None]:
    observed = sum(counter.get(value, 0) for value in DELIVERED_STEER_VALUES)
    inflight = counter.get("queued", 0)
    denominator = sum(counter.values()) - inflight
    return observed, denominator, inflight, ratio(observed, denominator)


def delivery_breakdown_dict(counter: Counter[str]) -> dict[str, int]:
    out = {value: counter.get(value, 0) for value in sorted(KNOWN_STEER_VALUES)}
    for key in sorted(k for k in counter if k not in KNOWN_STEER_VALUES):
        out[key] = counter[key]
    return out


def count_peer_steer_rejected(events: Iterable[VisibilityEvent]) -> int:
    return sum(
        1
        for event in events
        if event.kind == "visibility_degraded"
        and (event.payload or {}).get("surface") == "peer_steer_rejected"
    )


def backend_for(events: list[VisibilityEvent]) -> str | None:
    for event in events:
        if event.backend and event.backend != "wrapper_mcp":
            return event.backend
    for event in events:
        if event.backend:
            return event.backend
    return None


def recipient_backend_buckets(
    *,
    sender: str,
    rtt: RttResult,
    backend_by_agent: dict[str, str | None],
) -> tuple[dict[str, list[float]], Counter[str]]:
    """Return M11a RTT samples/unmatched counts partitioned by recipient backend."""

    buckets: dict[str, list[float]] = defaultdict(list)
    unmatched: Counter[str] = Counter()
    pairs = {pair for pair in rtt.by_pair if pair[0] == sender}
    pairs.update(pair for pair in rtt.unmatched_by_pair if pair[0] == sender)
    for _from_agent, recipient in pairs:
        backend = backend_by_agent.get(recipient) or "_unknown_backend"
        pair = (sender, recipient)
        buckets[backend].extend(rtt.by_pair.get(pair, []))
        unmatched[backend] += rtt.unmatched_by_pair.get(pair, 0)
    return dict(buckets), unmatched


def semantic_rtt_buckets(
    rtt: RttResult,
    *,
    sender: str | None = None,
) -> tuple[dict[str, list[float]], Counter[str]]:
    """Return M11a RTT samples/unmatched counts partitioned by semantic label."""

    buckets: dict[str, list[float]] = {label: [] for label in SEMANTIC_LABELS}
    unmatched: Counter[str] = Counter({label: 0 for label in SEMANTIC_LABELS})
    for (sample_sender, semantic), samples in rtt.by_sender_semantic.items():
        if sender is not None and sample_sender != sender:
            continue
        label = semantic if semantic in SEMANTIC_LABELS else "other"
        buckets[label].extend(samples)
    for (sample_sender, semantic), count in rtt.unmatched_by_sender_semantic.items():
        if sender is not None and sample_sender != sender:
            continue
        label = semantic if semantic in SEMANTIC_LABELS else "other"
        unmatched[label] += count
    return buckets, unmatched


def semantic_rtt_doc(
    rtt: RttResult,
    *,
    sender: str | None = None,
) -> dict[str, Any]:
    buckets, unmatched = semantic_rtt_buckets(rtt, sender=sender)
    out: dict[str, Any] = {"classifier_method": CLASSIFIER_METHOD}
    for label in SEMANTIC_LABELS:
        out[label] = percentile_triplet(
            buckets.get(label, []),
            include_unmatched=unmatched.get(label, 0),
        )
    return out


def coupling_compliance_doc(
    *,
    coupling_intent: str | None,
    sends: Iterable[SendMessageEvent],
) -> dict[str, Any]:
    peer_sends = [send for send in sends if send.is_peer]
    violations: list[dict[str, Any]] = []
    if coupling_intent == "loose_parallel":
        for send in peer_sends:
            if send.semantic not in BLOCKING_CANDIDATE_SEMANTICS:
                continue
            violations.append(
                {
                    "turn_id": send.event.turn_id,
                    "sender": send.sender,
                    "recipient": send.recipient,
                    "semantic": send.semantic,
                    "reason": "blocking_candidate_under_loose_parallel",
                    "event_id": send.event.event_id,
                }
            )
    return {
        "declared_intent": coupling_intent,
        "blocking_candidate_peer_dms": sum(
            1 for send in peer_sends if send.semantic in BLOCKING_CANDIDATE_SEMANTICS
        ),
        "nonblocking_peer_dms": sum(
            1 for send in peer_sends if send.semantic in NONBLOCKING_SEMANTICS
        ),
        "violations": violations,
    }


def sort_events(events: Iterable[VisibilityEvent]) -> list[VisibilityEvent]:
    def key(event: VisibilityEvent) -> tuple[datetime, int, str]:
        ts = parse_timestamp(event.timestamp)
        if ts is None:
            ts = datetime.max.replace(tzinfo=timezone.utc)
        return ts, event.seq, event.event_id

    return sorted(events, key=key)


def agent_backend(agent: str, backend_by_agent: dict[str, str | None]) -> str:
    return backend_by_agent.get(agent) or "N/A"


def backend_pair_key(sender_backend: str, recipient_backend: str) -> str:
    return f"{sender_backend}->{recipient_backend}"


def terminal_event_kind(
    structured_reply_ts: datetime | None,
    prose_fallback_ts: datetime | None,
) -> str:
    if structured_reply_ts is None or prose_fallback_ts is None:
        return "unknown"
    if structured_reply_ts == prose_fallback_ts:
        return "concurrent"
    if structured_reply_ts < prose_fallback_ts:
        return "structured_first_then_prose"
    return "prose_first_then_structured"


def m13_send_counts_by_backend(
    send_messages: list[SendMessageEvent],
    backend_by_agent: dict[str, str | None],
) -> Counter[str]:
    counts: Counter[str] = Counter()
    for send in send_messages:
        if not send.is_peer or send.recipient is None:
            continue
        sender_backend = agent_backend(send.sender, backend_by_agent)
        recipient_backend = agent_backend(send.recipient, backend_by_agent)
        counts[backend_pair_key(sender_backend, recipient_backend)] += 1
    return counts


def _earliest_send_by_recipient(sends: Iterable[SendMessageEvent]) -> dict[str, SendMessageEvent]:
    earliest: dict[str, SendMessageEvent] = {}
    for send in sends:
        recipient = send.recipient or "N/A"
        current = earliest.get(recipient)
        if current is None:
            earliest[recipient] = send
            continue
        if send.timestamp is None:
            continue
        if current.timestamp is None or send.timestamp < current.timestamp:
            earliest[recipient] = send
    return earliest


def m13_attribution_records(
    send_messages: list[SendMessageEvent],
    terminals: list[TerminalEvent],
    backend_by_agent: dict[str, str | None],
) -> list[M13Attribution]:
    """Attribute M13 prose-fallback collisions to sender/recipient backend pairs.

    send_message events are emitted by the wrapper MCP stream, so their
    ``turn_id`` is not the model turn's ``turn_id``.  The stable association
    available in archived runs is same-agent temporal containment: a peer
    send belongs to the terminal model turn whose terminal event is the first
    same-agent terminal after the send.  That preserves recipient attribution
    in both homogeneous and mixed-backend runs while keeping the legacy M13
    terminal-count numerator unchanged.
    """

    sends_by_agent: dict[str, list[SendMessageEvent]] = defaultdict(list)
    terminals_by_agent: dict[str, list[TerminalEvent]] = defaultdict(list)
    for send in send_messages:
        if send.is_peer:
            sends_by_agent[send.sender].append(send)
    for terminal in terminals:
        terminals_by_agent[terminal.event.agent].append(terminal)

    records: list[M13Attribution] = []
    for sender, agent_terminals in terminals_by_agent.items():
        agent_sends = sorted(
            sends_by_agent.get(sender, []),
            key=lambda send: send.timestamp or datetime.max.replace(tzinfo=timezone.utc),
        )
        previous_terminal_ts: datetime | None = None
        for terminal in sorted(
            agent_terminals,
            key=lambda item: item.timestamp or datetime.max.replace(tzinfo=timezone.utc),
        ):
            terminal_ts = terminal.timestamp
            window_sends: list[SendMessageEvent] = []
            for send in agent_sends:
                if send.timestamp is None:
                    continue
                after_previous = previous_terminal_ts is None or send.timestamp > previous_terminal_ts
                before_terminal = terminal_ts is None or send.timestamp <= terminal_ts
                if after_previous and before_terminal:
                    window_sends.append(send)
            if terminal.collision:
                sender_backend = agent_backend(sender, backend_by_agent)
                sends_by_recipient = _earliest_send_by_recipient(window_sends)
                if not sends_by_recipient:
                    records.append(
                        M13Attribution(
                            terminal=terminal,
                            sender=sender,
                            sender_backend=sender_backend,
                            recipient="N/A",
                            recipient_backend="N/A",
                            structured_reply_ts=None,
                            prose_fallback_ts=terminal_ts,
                            terminal_event_kind="unknown",
                        )
                    )
                else:
                    for recipient, send in sorted(sends_by_recipient.items()):
                        recipient_backend = (
                            agent_backend(recipient, backend_by_agent)
                            if recipient != "N/A"
                            else "N/A"
                        )
                        records.append(
                            M13Attribution(
                                terminal=terminal,
                                sender=sender,
                                sender_backend=sender_backend,
                                recipient=recipient,
                                recipient_backend=recipient_backend,
                                structured_reply_ts=send.timestamp,
                                prose_fallback_ts=terminal_ts,
                                terminal_event_kind=terminal_event_kind(send.timestamp, terminal_ts),
                            )
                        )
            if terminal_ts is not None:
                previous_terminal_ts = terminal_ts
    return sorted(
        records,
        key=lambda record: (
            record.prose_fallback_ts or datetime.max.replace(tzinfo=timezone.utc),
            record.sender,
            record.recipient,
            record.terminal.event.event_id,
        ),
    )


def collision_pairs(attributions: list[M13Attribution]) -> Counter[tuple[str, str]]:
    out: Counter[tuple[str, str]] = Counter()
    for attribution in attributions:
        if attribution.recipient == "N/A":
            continue
        out[(attribution.sender, attribution.recipient)] += 1
    return out


def m13_collisions_by_backend(
    *,
    attributions: Iterable[M13Attribution],
    send_counts: Counter[str],
) -> dict[str, dict[str, Any]]:
    collision_counts = Counter(record.backend_key for record in attributions)
    out: dict[str, dict[str, Any]] = {}
    for key in sorted(set(send_counts) | set(collision_counts)):
        if "->" in key:
            sender_backend, recipient_backend = key.split("->", 1)
        else:
            sender_backend, recipient_backend = key, "N/A"
        collisions = collision_counts.get(key, 0)
        sends = send_counts.get(key, 0)
        out[key] = {
            "sender_backend": sender_backend,
            "recipient_backend": recipient_backend,
            "collisions": collisions,
            "send_message_count": sends,
            "collision_rate": ratio(collisions, sends),
        }
    return out


def turn_window_key(event: VisibilityEvent) -> str:
    return event.turn_id or f"_event:{event.event_id}"


def inbox_polled_without_peer_send(
    events: Iterable[VisibilityEvent],
    sends: Iterable[SendMessageEvent],
) -> int:
    read_inbox_by_turn: Counter[str] = Counter()
    peer_send_by_turn: Counter[str] = Counter()
    for event in events:
        if is_tool_call_event(event, "read_inbox"):
            read_inbox_by_turn[turn_window_key(event)] += 1
    for send in sends:
        if send.is_peer:
            peer_send_by_turn[turn_window_key(send.event)] += 1
    return sum(
        max(read_count - peer_send_by_turn.get(turn_id, 0), 0)
        for turn_id, read_count in read_inbox_by_turn.items()
    )


def build_scorecards(
    dataset: CollabDataset,
    *,
    scenario: str,
    run_id: str,
    coupling_intent: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, dict[str, Any]]]:
    coupling_intent = normalize_coupling_intent(coupling_intent)
    merged_events_by_agent: dict[str, list[VisibilityEvent]] = defaultdict(list)
    for file_agent, events in dataset.events_by_agent.items():
        if not events:
            merged_events_by_agent.setdefault(file_agent, [])
            continue
        for event in events:
            merged_events_by_agent[event.agent or file_agent].append(event)
    events_by_agent = {
        agent: sort_events(events) for agent, events in merged_events_by_agent.items()
    }
    backend_by_agent = {agent: backend_for(events) for agent, events in events_by_agent.items()}
    agents = sorted(events_by_agent)
    send_messages = extract_send_messages(events_by_agent)
    terminals = extract_terminal_events(events_by_agent)
    rtt = compute_rtt(send_messages)
    m13_attributions = m13_attribution_records(send_messages, terminals, backend_by_agent)
    pair_collisions = collision_pairs(m13_attributions)
    m13_send_counts = m13_send_counts_by_backend(send_messages, backend_by_agent)

    sends_by_sender: dict[str, list[SendMessageEvent]] = defaultdict(list)
    received_by_recipient: Counter[str] = Counter()
    semantic_by_sender: dict[str, Counter[str]] = defaultdict(Counter)
    semantic_by_pair: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for send in send_messages:
        sends_by_sender[send.sender].append(send)
        if send.is_peer and send.recipient is not None:
            received_by_recipient[send.recipient] += 1
            semantic_by_sender[send.sender][send.semantic] += 1
            semantic_by_pair[(send.sender, send.recipient)][send.semantic] += 1

    pairs: list[dict[str, Any]] = []
    pair_keys = sorted({(s.sender, s.recipient) for s in send_messages if s.is_peer and s.recipient is not None})
    for sender, recipient in pair_keys:
        assert recipient is not None
        pair = (sender, recipient)
        messages_sent = sum(
            1 for s in send_messages if s.sender == sender and s.recipient == recipient and s.is_peer
        )
        pairs.append(
            {
                "from": sender,
                "to": recipient,
                "messages_sent": messages_sent,
                "rtt_seconds": stats(
                    rtt.by_pair.get(pair, []),
                    include_unmatched=rtt.unmatched_by_pair.get(pair, 0),
                ),
                "prose_fallback_collisions": pair_collisions.get(pair, 0),
                "semantic_counts": {label: semantic_by_pair[pair].get(label, 0) for label in SEMANTIC_LABELS},
            }
        )

    per_agent: dict[str, dict[str, Any]] = {}
    all_rtt_samples: list[float] = []
    total_peer_sends = 0
    total_send_calls = 0
    total_to_lead = 0
    total_steer_counter: Counter[str] = Counter()
    total_collisions = 0
    total_terminals = 0
    total_peer_steer_rejected = 0
    total_semantic: Counter[str] = Counter()

    for agent in agents:
        events = events_by_agent[agent]
        sends = sends_by_sender.get(agent, [])
        send_total = len(sends)
        to_lead = sum(1 for s in sends if s.recipient == TEAM_LEAD)
        peer_sent = sum(1 for s in sends if s.is_peer)
        missing_recipient = sum(1 for s in sends if s.recipient is None)
        steer_counter = delivery_breakdown(events)
        steer_observed, steer_total, steer_inflight, steer_rate = delivery_rate_parts(steer_counter)
        agent_terminals = [t for t in terminals if t.event.agent == agent]
        collisions = sum(1 for t in agent_terminals if t.collision)
        peer_rejected = count_peer_steer_rejected(events)
        manifest_consulted = sum(
            1 for event in events if is_tool_call_event(event, "mcp_anyteam_capability_manifest")
        )
        m4_attribution = {
            "manifest_consulted_count": manifest_consulted,
            "inbox_polled_without_peer_send": inbox_polled_without_peer_send(events, sends),
            "peer_steer_rejection_observed": peer_rejected,
        }
        agent_pairs = [pair for pair in rtt.by_pair if pair[0] == agent]
        agent_samples: list[float] = []
        agent_unmatched = 0
        for pair in sorted(set(agent_pairs) | {p for p in rtt.unmatched_by_pair if p[0] == agent}):
            agent_samples.extend(rtt.by_pair.get(pair, []))
            agent_unmatched += rtt.unmatched_by_pair.get(pair, 0)
        all_rtt_samples.extend(agent_samples)
        backend_samples, backend_unmatched = recipient_backend_buckets(
            sender=agent,
            rtt=rtt,
            backend_by_agent=backend_by_agent,
        )
        m11a_by_backend = {
            backend: percentile_triplet(
                samples,
                include_unmatched=backend_unmatched.get(backend, 0),
            )
            for backend, samples in sorted(backend_samples.items())
        }
        agent_m13_attributions = [
            attribution for attribution in m13_attributions if attribution.sender == agent
        ]
        agent_m13_send_counts = m13_send_counts_by_backend(
            [send for send in send_messages if send.sender == agent],
            backend_by_agent,
        )
        m11a_peer = percentile_triplet(agent_samples, include_unmatched=agent_unmatched)
        m11a_by_semantic = semantic_rtt_doc(rtt, sender=agent)
        agent_classification_coverage = classification_coverage(semantic_by_sender[agent], peer_sent)
        agent_compliance = coupling_compliance_doc(
            coupling_intent=coupling_intent,
            sends=sends,
        )

        notes: list[str] = []
        for backend, samples in sorted(backend_samples.items()):
            if len(samples) < 5:
                notes.append(f"m11a_undersampled:{backend}")
        unclassified = semantic_by_sender[agent].get("other", 0)
        if unclassified:
            notes.append(f"m11a_unclassified_semantic:{unclassified}")
        if send_total == 0:
            notes.append("no_send_message_calls")
        if missing_recipient:
            notes.append(f"send_message_missing_recipient:{missing_recipient}")
        if peer_sent > 0 and len(agent_samples) < 5:
            notes.append("p95_undersampled")
        if steer_total == 0 and steer_inflight == 0:
            notes.append("no_steer_ack")
        if steer_inflight:
            notes.append(f"steer_ack_inflight:{steer_inflight}")
        if peer_rejected:
            notes.append(f"peer_steer_rejected:{peer_rejected}")

        per_agent[agent] = {
            "schema_version": SCHEMA_VERSION,
            "agent": agent,
            "backend": backend_for(events),
            "scenario": scenario,
            "run_id": run_id,
            "metrics": {
                "M3_peer_dm_sent": peer_sent,
                "M3_peer_dm_received": received_by_recipient.get(agent, 0),
                "M3_peer_dm_semantic_breakdown": {
                    label: semantic_by_sender[agent].get(label, 0) for label in SEMANTIC_LABELS
                },
                "M4_cross_peer_ratio": ratio(peer_sent, send_total),
                "M4_total_send_message_calls": send_total,
                "M4_to_lead_count": to_lead,
                "M4_attribution": m4_attribution,
                "M4_semantic_breakdown": {
                    label: semantic_by_sender[agent].get(label, 0) for label in SEMANTIC_LABELS
                },
                "M9_steer_ack_rate": steer_rate,
                "M9_steer_ack_observed": steer_observed,
                "M9_steer_ack_total": steer_total,
                "M9_inflight_count": steer_inflight,
                "M9_delivery_breakdown": delivery_breakdown_dict(steer_counter),
                "M11a_peer_dm_rtt_seconds": m11a_peer,
                "M11a_peer_dm_rtt_seconds_by_recipient_backend": m11a_by_backend,
                "M11a_peer_dm_rtt_seconds_by_semantic": m11a_by_semantic,
                "M11a_classification_coverage": agent_classification_coverage,
                "M11a_coupling_compliance": agent_compliance,
                "samples_used_for_M11a": m11a_peer["samples"],
                "M11a_p50": m11a_peer["p50"],
                "M11a_max": m11a_peer["max"],
                "M13_prose_fallback_collisions": collisions,
                "M13_total_send_message_replies": len(agent_terminals),
                "M13_prose_fallback_collision_rate": ratio(collisions, len(agent_terminals)),
                "M13_collisions_by_backend": m13_collisions_by_backend(
                    attributions=agent_m13_attributions,
                    send_counts=agent_m13_send_counts,
                ),
                "M13_per_collision_attribution": [
                    attribution.as_dict() for attribution in agent_m13_attributions
                ],
            },
            "notes": notes,
        }

        total_peer_sends += peer_sent
        total_send_calls += send_total
        total_to_lead += to_lead
        total_steer_counter.update(steer_counter)
        total_collisions += collisions
        total_terminals += len(agent_terminals)
        total_peer_steer_rejected += peer_rejected
        total_semantic.update(semantic_by_sender[agent])

    team_steer_observed, team_steer_total, team_steer_inflight, team_steer_rate = delivery_rate_parts(total_steer_counter)
    team_m11a = percentile_triplet(
        all_rtt_samples,
        include_unmatched=sum(rtt.unmatched_by_pair.values()),
    )
    team_classification_coverage = classification_coverage(total_semantic, total_peer_sends)
    team_compliance = coupling_compliance_doc(
        coupling_intent=coupling_intent,
        sends=[send for sends in sends_by_sender.values() for send in sends],
    )
    scenario_doc = {
        "schema_version": SCHEMA_VERSION,
        "scenario": scenario,
        "run_id": run_id,
        "agents": agents,
        "aggregate": {
            "M3_total_peer_dms": total_peer_sends,
            "M3_peer_dm_semantic_breakdown": {
                label: total_semantic.get(label, 0) for label in SEMANTIC_LABELS
            },
            "M4_semantic_breakdown": {
                label: total_semantic.get(label, 0) for label in SEMANTIC_LABELS
            },
            "M4_team_cross_peer_ratio": ratio(total_peer_sends, total_send_calls),
            "M4_total_send_message_calls": total_send_calls,
            "M4_to_lead_count": total_to_lead,
            "M9_team_steer_ack_rate": team_steer_rate,
            "M9_team_steer_ack_observed": team_steer_observed,
            "M9_team_steer_ack_total": team_steer_total,
            "M9_team_inflight_count": team_steer_inflight,
            "M9_delivery_breakdown": delivery_breakdown_dict(total_steer_counter),
            "M11a_team_p95_rtt_seconds": team_m11a["p95"],
            "M11a_team_rtt_seconds": team_m11a,
            "M11a_peer_dm_rtt_seconds_by_semantic": semantic_rtt_doc(rtt),
            "M11a_classification_coverage": team_classification_coverage,
            "M11a_coupling_compliance": team_compliance,
            "samples_used_for_M11a": team_m11a["samples"],
            "M11a_p50": team_m11a["p50"],
            "M11a_max": team_m11a["max"],
            "M13_total_collisions": total_collisions,
            "M13_total_send_message_replies": total_terminals,
            "M13_prose_fallback_collision_rate": ratio(total_collisions, total_terminals),
            "M13_collisions_by_backend": m13_collisions_by_backend(
                attributions=m13_attributions,
                send_counts=m13_send_counts,
            ),
            "M13_per_collision_attribution": [
                attribution.as_dict() for attribution in m13_attributions
            ],
            "peer_steer_rejected_count": total_peer_steer_rejected,
        },
        "per_agent_files": [f"agents/{agent}.json" for agent in agents],
        "pair_file": "pairs.json",
    }
    pairs_doc = {
        "schema_version": SCHEMA_VERSION,
        "scenario": scenario,
        "run_id": run_id,
        "pairs": pairs,
    }
    return scenario_doc, pairs_doc, per_agent


def write_outputs(out_dir: Path, scenario_doc: dict[str, Any], pairs_doc: dict[str, Any], per_agent: dict[str, dict[str, Any]]) -> None:
    try:
        agents_dir = out_dir / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        for agent, doc in per_agent.items():
            (agents_dir / f"{agent}.json").write_text(
                json.dumps(doc, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        (out_dir / "pairs.json").write_text(
            json.dumps(pairs_doc, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (out_dir / "scenario.json").write_text(
            json.dumps(scenario_doc, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise ScoreOutputError(str(exc)) from exc


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--team", help="team name under ~/.claude/teams")
    source.add_argument("--events-dir", type=Path, help="explicit events directory for archived runs")
    parser.add_argument("--scenario", required=True, help="scenario id, e.g. S5")
    parser.add_argument("--run-id", required=True, help="run id/timestamp")
    parser.add_argument("--out", type=Path, required=True, help="output directory")
    parser.add_argument(
        "--coupling-intent",
        help=(
            "Optional scorer-only coupling intent for M11a compliance. "
            "Canonical values: tight_peer_loop, loose_parallel, batched_async. "
            "Legacy aliases tight/loose are normalized here only."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    try:
        dataset = load_dataset(team=args.team, events_dir=args.events_dir)
        scenario_doc, pairs_doc, per_agent = build_scorecards(
            dataset,
            scenario=args.scenario,
            run_id=args.run_id,
            coupling_intent=args.coupling_intent,
        )
        write_outputs(args.out, scenario_doc, pairs_doc, per_agent)
    except ScoreInputError as exc:
        print(f"score_collab: {exc}", file=sys.stderr)
        return 1
    except ScoreOutputError as exc:
        print(f"score_collab: failed to write outputs: {exc}", file=sys.stderr)
        return 2
    print(
        "score_collab: wrote "
        f"{len(per_agent)} agent profiles, {len(pairs_doc['pairs'])} pairs to {args.out}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
