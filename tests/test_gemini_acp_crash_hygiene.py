from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from claude_anyteam.backends.gemini import crash_hygiene, invoke
from claude_anyteam.backends.gemini.crash_hygiene import ProcessInfo


def test_adapter_state_defaults_and_merge_preserves_unknown_keys(tmp_path):
    home = tmp_path / "home"
    state_path = home / ".claude-anyteam" / "state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({"backend": "acp", "acp_session_id": "s1", "custom": 3}), encoding="utf-8")

    state = invoke.read_adapter_state(home)

    assert state["acp_session_id"] == "s1"
    assert state["gemini_pid"] is None
    assert state["adapter_generation"] is None
    invoke.merge_adapter_state(home, gemini_pid=123)
    merged = json.loads(state_path.read_text(encoding="utf-8"))
    assert merged["custom"] == 3
    assert merged["acp_session_id"] == "s1"
    assert merged["gemini_pid"] == 123


def test_previous_adapter_died_only_for_dead_unclean_pid(monkeypatch):
    monkeypatch.setattr(crash_hygiene, "pid_alive", lambda pid: False)
    assert crash_hygiene.previous_adapter_died({"adapter_pid": os.getpid()}) is False
    assert crash_hygiene.previous_adapter_died({"adapter_pid": 9999, "adapter_start_time": "2026-01-01T00:00:00Z"}) is True
    assert crash_hygiene.previous_adapter_died({
        "adapter_pid": 9999,
        "adapter_start_time": "2026-01-01T00:00:00Z",
        "last_clean_shutdown_at": "2026-01-01T00:00:01Z",
    }) is False
    monkeypatch.setattr(crash_hygiene, "pid_alive", lambda pid: True)
    assert crash_hygiene.previous_adapter_died({"adapter_pid": 9999, "adapter_start_time": "2026-01-01T00:00:00Z"}) is False


def test_quarantine_stale_acp_sessions_preserves_relative_path(tmp_path):
    home = tmp_path / "home"
    stale = home / ".gemini" / "tmp" / "proj" / "chats" / "session-a.jsonl"
    stale.parent.mkdir(parents=True)
    stale.write_text('{"sessionId":"a"}\n', encoding="utf-8")
    old = home / ".gemini" / "tmp" / "proj" / "chats" / "session-old.jsonl"
    old.write_text('{"sessionId":"old"}\n', encoding="utf-8")
    now = time.time()
    os.utime(stale, (now, now))
    old_ts = datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp()
    os.utime(old, (old_ts, old_ts))

    moved = crash_hygiene.quarantine_stale_acp_sessions(
        home,
        previous_start_time="2026-01-01T00:00:00Z",
        recovery_start_time="2099-01-01T00:00:00Z",
        generation="gen1",
    )

    assert len(moved) == 1
    assert moved[0] == home / ".claude-anyteam" / "graveyard" / "acp-sessions" / "gen1" / "tmp" / "proj" / "chats" / "session-a.jsonl"
    assert moved[0].exists()
    assert not stale.exists()
    assert old.exists()


def test_reap_orphan_acp_processes_filters_by_isolated_home(monkeypatch, tmp_path):
    home = tmp_path / "home"
    other = tmp_path / "other"
    killed = []
    monkeypatch.setattr(crash_hygiene, "_terminate_pid_or_group", lambda pid, pgid, timeout=5.0: killed.append((pid, pgid)) or True)
    procs = [
        ProcessInfo(101, 1, 101, ["/usr/bin/gemini", "--acp"], {"HOME": str(home)}),
        ProcessInfo(102, 1, 102, ["/usr/bin/gemini", "--acp"], {"HOME": str(other)}),
        ProcessInfo(103, 1, 103, ["/usr/bin/gemini", "--prompt", "x"], {"HOME": str(home)}),
        ProcessInfo(104, os.getpid(), 104, ["/usr/bin/gemini", "--acp"], {"HOME": str(home)}),
    ]

    result = crash_hygiene.reap_orphan_acp_processes(
        gemini_home=home,
        team="t",
        agent="a",
        gemini_binary="gemini",
        processes=procs,
    )

    assert result == [101]
    assert killed == [(101, 101)]


def test_startup_recovery_resets_sessions_and_records_summary(monkeypatch, tmp_path):
    home = tmp_path / "home"
    now = time.time()
    crashed_start = datetime.fromtimestamp(now - 10, timezone.utc).isoformat().replace("+00:00", "Z")
    invoke.merge_adapter_state(
        home,
        backend="acp",
        adapter_pid=99999,
        adapter_start_time=crashed_start,
        adapter_generation="gen",
        acp_session_id="live",
        acp_storage_session_id="store",
        gemini_pid=101,
        gemini_pgid=101,
    )
    session = home / ".gemini" / "tmp" / "proj" / "chats" / "session-a.jsonl"
    session.parent.mkdir(parents=True)
    session.write_text("{}\n", encoding="utf-8")
    os.utime(session, (now, now))
    pre_crash_session = home / ".gemini" / "tmp" / "proj" / "chats" / "session-before-crash.jsonl"
    pre_crash_session.write_text("{}\n", encoding="utf-8")
    before_crash = now - 60
    os.utime(pre_crash_session, (before_crash, before_crash))
    monkeypatch.setattr(crash_hygiene, "pid_alive", lambda pid: False)
    monkeypatch.setattr(crash_hygiene, "reap_orphan_acp_processes", lambda **kwargs: [101])

    summary = crash_hygiene.run_startup_recovery(gemini_home=home, team="t", agent="a", cwd=tmp_path)

    assert summary.orphan_pids == [101]
    state = invoke.read_adapter_state(home)
    assert state["acp_session_id"] is None
    assert state["acp_storage_session_id"] is None
    assert state["gemini_pid"] is None
    assert state["last_reaper_summary"]["orphan_processes_killed"] == 1
    assert state["last_reaper_summary"]["sessions_quarantined"] == 1
    assert not session.exists()
    assert pre_crash_session.exists()
