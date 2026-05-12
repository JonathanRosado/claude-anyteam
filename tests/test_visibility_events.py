from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from claude_teams import messaging as cs_messaging  # type: ignore[import-untyped]

from claude_anyteam import app_server as app_server_mod
from claude_anyteam import codex as codex_mod
from claude_anyteam import loop as loop_mod
from claude_anyteam import protocol_io as pio
from claude_anyteam.config import Settings
from claude_anyteam.messages import VisibilityEvent


@pytest.fixture
def events_root(tmp_path: Path, monkeypatch):
    base = tmp_path / "home" / ".claude" / "teams"
    monkeypatch.setattr(cs_messaging, "TEAMS_DIR", base)
    return base


def _event(kind: str, seq: int, **overrides) -> VisibilityEvent:
    data = {
        "kind": kind,
        "event_id": f"agent:turn:{seq:06d}",
        "team": "team-x",
        "agent": "codex-runtime",
        "backend": "codex_app_server",
        "task_id": "16",
        "turn_id": "turn-1",
        "seq": seq,
        "severity": "info",
        "summary": f"event {seq}",
        "payload": {},
    }
    data.update(overrides)
    return VisibilityEvent.model_validate(data)


def _settings() -> Settings:
    return Settings(
        team_name="team-x",
        agent_name="codex-runtime",
        cwd=Path("/tmp").resolve(),
        poll_interval_s=0.01,
        color="cyan",
        plan_mode_required=False,
        codex_binary="codex",
        app_server=True,
    )


class _FakeClient:
    notifications = None

    def __init__(self, *args, notifications: list[dict] | None = None, **kwargs):
        self.notifications = _NotificationQueue(notifications or [])
        self.steers: list[dict] = []
        self.interrupts: list[dict] = []

    def start(self):
        pass

    def initialize(self, **_kwargs):
        return {}

    def thread_start(self, **kwargs):
        return "thread-1"

    def turn_start(self, **kwargs):
        return "turn-1"

    def turn_steer(self, **kwargs):
        self.steers.append(kwargs)
        return kwargs["expected_turn_id"]

    def turn_interrupt(self, **kwargs):
        self.interrupts.append(kwargs)

    def close(self):
        pass


class _NotificationQueue:
    def __init__(self, items: list[dict]):
        self.items = list(items)

    def get(self, timeout=None):
        if self.items:
            item = self.items.pop(0)
            if item == {"__raise__": True}:
                raise RuntimeError("empty (test)")
            return item
        raise RuntimeError("empty (test)")


def test_append_visibility_events_reads_since_seq(events_root: Path):
    for seq in range(1, 101):
        pio.append_visibility_event("team-x", "codex-runtime", _event("turn_progress", seq))

    got = pio.read_visibility_events("team-x", "codex-runtime", since_seq=50)
    assert [e.seq for e in got] == list(range(51, 101))
    assert (events_root / "team-x" / "events" / ".lock").exists()


def test_append_visibility_event_validates_before_write(events_root: Path):
    with pytest.raises(Exception):
        pio.append_visibility_event(
            "team-x",
            "codex-runtime",
            {
                "kind": "not_a_kind",
                "event_id": "bad",
                "team": "team-x",
                "agent": "codex-runtime",
                "backend": "codex_app_server",
                "seq": 1,
                "severity": "info",
                "summary": "bad",
                "payload": {},
            },
        )
    assert not pio.visibility_event_path("team-x", "codex-runtime").exists()


def test_send_visibility_event_to_lead_sets_message_kind(events_root: Path):
    event = _event(
        "turn_progress",
        1,
        visibility={"mailbox": True, "task_state": True, "event_log": True, "stderr": True},
    )
    pio.send_visibility_event_to_lead("team-x", "codex-runtime", event)

    raw = json.loads((events_root / "team-x" / "inboxes" / "team-lead.json").read_text())
    assert raw[0]["messageKind"] == "turn_progress"
    body = json.loads(raw[0]["text"])
    assert body["kind"] == "turn_progress"
    assert body["payload"] == {}


def test_emit_coupling_conflict_writes_visibility_degraded_payload(events_root: Path):
    task = SimpleNamespace(id="7", coupling="tight")
    manifest = {
        "agent_name": "kimi-runtime",
        "capability_version": "1",
        "transport": "kimi-headless",
        "coupling_regime": "loose",
        "coupling": {"intent": "loose_parallel"},
    }

    event = pio.emit_coupling_conflict_if_needed(
        team="team-x",
        agent="kimi-runtime",
        backend="kimi_headless",
        task=task,
        manifest=manifest,
    )

    assert event is not None
    assert event.kind == "visibility_degraded"
    assert event.task_id == "7"
    assert event.payload["surface"] == "coupling_intent_conflict"
    assert event.payload["task_id"] == "7"
    assert event.payload["requested_coupling"] == "tight"
    assert event.payload["backend_coupling_regime"] == "loose"
    assert event.payload["backend_coupling_intent"] == "loose_parallel"
    assert "task.coupling" in event.payload["suggested_fix"]
    [logged] = pio.read_events("team-x", "kimi-runtime")
    assert logged.event_id == event.event_id

    raw = json.loads((events_root / "team-x" / "inboxes" / "team-lead.json").read_text())
    assert raw[0]["messageKind"] == "visibility_degraded"
    body = json.loads(raw[0]["text"])
    assert body["payload"] == event.payload


def test_emit_coupling_conflict_if_needed_skips_matching_manifest(events_root: Path):
    task = SimpleNamespace(id="8", coupling={"intent": "loose_parallel"})
    manifest = {"coupling_regime": "loose", "coupling": {"intent": "loose_parallel"}}

    event = pio.emit_coupling_conflict_if_needed(
        team="team-x",
        agent="kimi-runtime",
        backend="kimi_headless",
        task=task,
        manifest=manifest,
    )

    assert event is None
    assert pio.read_events("team-x", "kimi-runtime") == []


def test_app_server_command_execution_writes_tool_event_to_event_log_and_active_form(events_root: Path):
    notifications = [
        {
            "method": "item/completed",
            "params": {
                "item": {
                    "type": "commandExecution",
                    "command": "uv run pytest tests/test_visibility_events.py",
                    "exitCode": 0,
                    "durationMs": 1234,
                    "stdoutPreview": "1 passed",
                }
            },
        },
        {"method": "turn/completed", "params": {"turn": {"status": "ok"}}},
    ]

    def make_client(*args, **kwargs):
        return _FakeClient(*args, notifications=notifications, **kwargs)

    update_calls: list[tuple[tuple, dict]] = []
    with patch.object(app_server_mod, "AppServerClient", make_client):
        with (
            patch.object(loop_mod.pio, "read_own_inbox", return_value=[]),
            patch.object(
                loop_mod.pio,
                "update_task",
                side_effect=lambda *a, **k: update_calls.append((a, k)),
            ),
        ):
            result = loop_mod._execute_task_app_server(
                loop_mod.LoopState(settings=_settings()),
                SimpleNamespace(id="16"),
                "run tests",
            )

    assert result.exit_code == 0
    events = pio.read_events("team-x", "codex-runtime")
    tool_events = [e for e in events if e.kind == "tool_event"]
    assert len(tool_events) == 1
    payload = tool_events[0].payload
    assert payload["category"] == "host_tool"
    assert payload["tool_name"] == "commandExecution"
    assert payload["raw_backend_type"] == "commandExecution"
    assert payload["target"] == "uv run pytest tests/test_visibility_events.py"
    assert payload["exit_code"] == 0
    assert all(e.visibility.event_log for e in events)
    assert not any(e.visibility.stderr and not e.visibility.event_log for e in events)
    assert any(
        kwargs.get("active_form")
        == "commandExecution: uv run pytest tests/test_visibility_events.py"[:120]
        for _, kwargs in update_calls
    )


def test_app_server_file_changes_write_artifact_events(events_root: Path):
    notifications = [
        {
            "method": "item/completed",
            "params": {
                "item": {
                    "type": "fileChange",
                    "path": path,
                    "action": action,
                    "bytesDelta": bytes_delta,
                }
            },
        }
        for path, action, bytes_delta in (
            ("src/a.py", "created", 11),
            ("src/b.py", "modified", -3),
            ("tests/test_c.py", "deleted", -42),
        )
    ] + [{"method": "turn/completed", "params": {"turn": {"status": "ok"}}}]

    def make_client(*args, **kwargs):
        return _FakeClient(*args, notifications=notifications, **kwargs)

    with patch.object(app_server_mod, "AppServerClient", make_client):
        result = codex_mod.app_server_invoke(
            task_prompt="edit files",
            cwd=Path("/tmp"),
            schema=None,
            settings_team="team-x",
            settings_agent="codex-runtime",
            task_id="16",
            event_sink=lambda event: pio.append_event(
                "team-x", "codex-runtime", event
            ),
        )

    assert result.exit_code == 0
    artifact_events = [
        e for e in pio.read_events("team-x", "codex-runtime")
        if e.kind == "artifact_event"
    ]
    assert len(artifact_events) == 3
    assert [
        (
            event.payload["path"],
            event.payload["action"],
            event.payload["bytes_delta"],
            event.payload["raw_backend_type"],
        )
        for event in artifact_events
    ] == [
        ("src/a.py", "created", 11, "fileChange"),
        ("src/b.py", "modified", -3, "fileChange"),
        ("tests/test_c.py", "deleted", -42, "fileChange"),
    ]


def test_app_server_web_search_writes_host_tool_event(events_root: Path):
    notifications = [
        {
            "method": "item/completed",
            "params": {
                "item": {
                    "type": "webSearch",
                    "query": "R17 visibility parity",
                    "status": "completed",
                }
            },
        },
        {"method": "turn/completed", "params": {"turn": {"status": "ok"}}},
    ]

    def make_client(*args, **kwargs):
        return _FakeClient(*args, notifications=notifications, **kwargs)

    with patch.object(app_server_mod, "AppServerClient", make_client):
        result = codex_mod.app_server_invoke(
            task_prompt="search web",
            cwd=Path("/tmp"),
            schema=None,
            settings_team="team-x",
            settings_agent="codex-runtime",
            task_id="16",
            event_sink=lambda event: pio.append_event(
                "team-x", "codex-runtime", event
            ),
        )

    assert result.exit_code == 0
    tool_events = [
        e for e in pio.read_events("team-x", "codex-runtime")
        if e.kind == "tool_event"
    ]
    assert len(tool_events) == 1
    payload = tool_events[0].payload
    assert payload["category"] == "host_tool"
    assert payload["tool_name"] == "webSearch"
    assert payload["raw_backend_type"] == "webSearch"
    assert payload["target"] == "R17 visibility parity"
    assert payload["status"] == "success"


@pytest.mark.parametrize(
    ("item", "expected_kind"),
    [
        ({"type": "agentMessage", "text": "checkpoint ready"}, "turn_progress"),
        ({"type": "imageGeneration", "prompt": "draw a frog"}, "tool_event"),
        ({"type": "plan", "text": "1. inspect\n2. patch"}, "turn_progress"),
        ({"type": "error", "message": "backend failed"}, "turn_warning"),
    ],
)
def test_app_server_recognized_items_emit_normalized_envelopes(
    events_root: Path,
    item: dict,
    expected_kind: str,
):
    notifications = [
        {"method": "item/completed", "params": {"item": item}},
        {"method": "turn/completed", "params": {"turn": {"status": "ok"}}},
    ]

    def make_client(*args, **kwargs):
        return _FakeClient(*args, notifications=notifications, **kwargs)

    with patch.object(app_server_mod, "AppServerClient", make_client):
        result = codex_mod.app_server_invoke(
            task_prompt="exercise event",
            cwd=Path("/tmp"),
            schema=None,
            settings_team="team-x",
            settings_agent="codex-runtime",
            task_id="16",
            event_sink=lambda event: pio.append_event(
                "team-x", "codex-runtime", event
            ),
        )

    assert result.exit_code == 0
    matched = [
        e
        for e in pio.read_events("team-x", "codex-runtime")
        if e.kind == expected_kind
        and e.payload.get("raw_backend_type") == item["type"]
    ]
    assert matched, f"{item['type']} should emit a {expected_kind}"


def test_app_server_without_event_sink_keeps_stderr_visibility(capsys):
    notifications = [
        {
            "method": "item/completed",
            "params": {
                "item": {
                    "type": "commandExecution",
                    "command": "uv run pytest",
                    "exitCode": 0,
                }
            },
        },
        {"method": "turn/completed", "params": {"turn": {"status": "ok"}}},
    ]

    def make_client(*args, **kwargs):
        return _FakeClient(*args, notifications=notifications, **kwargs)

    with patch.object(app_server_mod, "AppServerClient", make_client):
        result = codex_mod.app_server_invoke(
            task_prompt="run tests",
            cwd=Path("/tmp"),
            schema=None,
            settings_team="team-x",
            settings_agent="codex-runtime",
            task_id="16",
        )

    assert result.exit_code == 0
    stderr_records = [
        json.loads(line)
        for line in capsys.readouterr().err.splitlines()
        if line.strip()
    ]
    visibility_records = [
        record
        for record in stderr_records
        if record.get("msg") == "visibility.event"
    ]
    assert any(
        record["visibility_event"]["kind"] == "tool_event"
        and record["visibility_event"]["payload"]["raw_backend_type"]
        == "commandExecution"
        for record in visibility_records
    )


def test_app_server_no_checkpoint_after_300s_emits_turn_progress(monkeypatch):
    notifications = [
        {"__raise__": True},
        {"method": "turn/completed", "params": {"turn": {"status": "ok"}}},
    ]
    created: list[_FakeClient] = []

    def make_client(*args, **kwargs):
        client = _FakeClient(*args, notifications=notifications, **kwargs)
        created.append(client)
        return client

    ticks = iter([0, 0, 0, 0, 0, 301, 301, 301, 301, 302, 303])

    def fake_monotonic():
        try:
            return next(ticks)
        except StopIteration:
            return 303

    emitted: list[VisibilityEvent] = []
    with (
        patch.object(app_server_mod, "AppServerClient", make_client),
        monkeypatch.context() as m,
    ):
        m.setattr(codex_mod.time, "monotonic", fake_monotonic)
        result = codex_mod.app_server_invoke(
            task_prompt="long task",
            cwd=Path("/tmp"),
            schema=None,
            settings_team="team-x",
            settings_agent="codex-runtime",
            task_id="16",
            overall_timeout_s=900,
            non_progress_warn_s=300,
            event_sink=emitted.append,
        )

    assert result.exit_code == 0
    progress = [e for e in emitted if e.kind == "turn_progress" and e.severity == "warn"]
    assert len(progress) == 1
    assert progress[0].payload["elapsed_s"] == 301
    assert progress[0].payload["action_taken"] == "turn_steer_sent"
    assert progress[0].visibility.mailbox is True
    assert progress[0].visibility.task_state is True
    assert created[0].steers, "watchdog should send checkpoint turn/steer"
    assert created[0].interrupts == []


def test_app_server_watchdog_fans_out_to_event_log_mailbox_and_active_form(
    events_root: Path,
    monkeypatch,
):
    notifications = [
        {"__raise__": True},
        {"method": "turn/completed", "params": {"turn": {"status": "ok"}}},
    ]
    created: list[_FakeClient] = []

    def make_client(*args, **kwargs):
        client = _FakeClient(*args, notifications=notifications, **kwargs)
        created.append(client)
        return client

    ticks = iter([0, 0, 0, 0, 0, 0, 300, 301])

    def fake_monotonic():
        try:
            return next(ticks)
        except StopIteration:
            return 301

    update_calls: list[tuple[tuple, dict]] = []
    # Task #5 / RFC #50 Phase B: ``non_progress_warn_s`` default flipped
    # to None (opt-in). The watchdog test must pass explicit opt-in values
    # via dataclasses.replace so it still exercises the firing path. Also
    # pins ``turn_timeout_s`` to the prior 900 so the payload assertion
    # below stays stable across default changes.
    import dataclasses as _dataclasses

    watchdog_settings = _dataclasses.replace(
        _settings(), turn_timeout_s=900.0, non_progress_warn_s=300.0
    )
    with (
        patch.object(app_server_mod, "AppServerClient", make_client),
        patch.object(loop_mod.pio, "read_own_inbox", return_value=[]),
        patch.object(
            loop_mod.pio,
            "update_task",
            side_effect=lambda *a, **k: update_calls.append((a, k)),
        ),
        monkeypatch.context() as m,
    ):
        m.setattr(codex_mod.time, "monotonic", fake_monotonic)
        result = loop_mod._execute_task_app_server(
            loop_mod.LoopState(settings=watchdog_settings),
            SimpleNamespace(id="16"),
            "long task",
        )

    assert result.exit_code == 0
    events = [
        e for e in pio.read_events("team-x", "codex-runtime")
        if e.kind == "turn_progress" and e.severity == "warn"
    ]
    assert len(events) == 1
    assert events[0].summary == "no visible checkpoint for 300s; checkpoint steer sent"
    assert events[0].payload == {
        "elapsed_s": 300,
        "timeout_s": 900.0,
        "risk": "timeout_possible",
        "action_taken": "turn_steer_sent",
    }
    assert created[0].steers, "soft watchdog should checkpoint steer"
    assert created[0].interrupts == []
    assert any(
        kwargs.get("active_form")
        == "running codex: no visible checkpoint for 300s; checkpoint steer sent"
        for _, kwargs in update_calls
    )
    lead_inbox = json.loads(
        (events_root / "team-x" / "inboxes" / "team-lead.json").read_text()
    )
    warnings = [
        message
        for message in lead_inbox
        if message.get("messageKind") == "turn_progress"
        and json.loads(message["text"])["event_id"] == events[0].event_id
    ]
    assert len(warnings) == 1


def test_app_server_watchdog_does_not_interrupt_without_opt_in(monkeypatch):
    notifications = [
        {"__raise__": True},
        {"__raise__": True},
        {"method": "turn/completed", "params": {"turn": {"status": "ok"}}},
    ]
    created: list[_FakeClient] = []

    def make_client(*args, **kwargs):
        client = _FakeClient(*args, notifications=notifications, **kwargs)
        created.append(client)
        return client

    ticks = iter([0, 0, 0, 0, 0, 0, 300, 350, 420, 421])

    def fake_monotonic():
        try:
            return next(ticks)
        except StopIteration:
            return 421

    with (
        patch.object(app_server_mod, "AppServerClient", make_client),
        monkeypatch.context() as m,
    ):
        m.setattr(codex_mod.time, "monotonic", fake_monotonic)
        result = codex_mod.app_server_invoke(
            task_prompt="long task",
            cwd=Path("/tmp"),
            schema=None,
            settings_team="team-x",
            settings_agent="codex-runtime",
            task_id="16",
            overall_timeout_s=900,
            non_progress_warn_s=300,
        )

    assert result.exit_code == 0
    assert created[0].steers, "soft watchdog should still steer"
    assert created[0].interrupts == []


def test_app_server_watchdog_interrupts_only_when_opted_in(monkeypatch):
    notifications = [
        {"__raise__": True},
        {"__raise__": True},
        {"method": "turn/completed", "params": {"turn": {"status": "ok"}}},
    ]
    created: list[_FakeClient] = []

    def make_client(*args, **kwargs):
        client = _FakeClient(*args, notifications=notifications, **kwargs)
        created.append(client)
        return client

    ticks = iter([0, 0, 0, 0, 0, 0, 300, 350, 420])

    def fake_monotonic():
        try:
            return next(ticks)
        except StopIteration:
            return 420

    with (
        patch.object(app_server_mod, "AppServerClient", make_client),
        monkeypatch.context() as m,
    ):
        m.setattr(codex_mod.time, "monotonic", fake_monotonic)
        result = codex_mod.app_server_invoke(
            task_prompt="long task",
            cwd=Path("/tmp"),
            schema=None,
            settings_team="team-x",
            settings_agent="codex-runtime",
            task_id="16",
            overall_timeout_s=900,
            non_progress_warn_s=300,
            non_progress_interrupt_s=420,
        )

    assert result.exit_code == 124
    assert "interrupted" in (result.error or "")
    assert created[0].steers, "soft watchdog fires before hard interrupt"
    assert created[0].interrupts == [
        {"thread_id": "thread-1", "turn_id": "turn-1"}
    ]
