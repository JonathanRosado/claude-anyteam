"""Inbox write-contention tests.

M0 observation #4: the Claude Code harness appends to the inbox JSON array
and may set `read: false` concurrently with our adapter's poll+mark-as-read.
We need to tolerate partial reads, malformed intermediate states, and not
lose messages when a race occurs.

These tests use cs50victor's `messaging` module against a tempdir, spawning
concurrent appenders (simulating the harness) and readers (simulating the
adapter). We assert:

1. No message is permanently lost during concurrent operation.
2. JSON parse errors are tolerated (our protocol_io wrapper returns []
   rather than crashing).
3. The `.lock` file is created under inboxes/ by cs50victor on first
   write — documenting the behavior explicitly.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
from claude_teams import messaging as cs_messaging  # type: ignore[import-untyped]
from claude_teams.models import InboxMessage  # type: ignore[import-untyped]

from claude_anyteam import protocol_io


@pytest.fixture
def team_env(tmp_path: Path, monkeypatch):
    """Point TEAMS_DIR at a fresh tmpdir for the duration of the test.

    This keeps contention probes off the live `~/.claude/teams/` directory.
    """
    base = tmp_path / "home" / ".claude" / "teams"
    monkeypatch.setattr(cs_messaging, "TEAMS_DIR", base)
    yield base


def test_inbox_lock_file_is_created_on_first_append(team_env: Path):
    cs_messaging.send_plain_message(
        "team-x", from_name="team-lead", to_name="codex", text="hi", summary="s1"
    )
    lock = team_env / "team-x" / "inboxes" / ".lock"
    # cs50victor creates this as `_filelock.file_lock(path)` — it should exist
    # after any write.
    assert lock.exists(), "cs50victor should create inboxes/.lock on first write"


def test_concurrent_appends_preserve_all_messages(team_env: Path):
    """100 appenders racing against the same inbox file — no message is lost."""
    num_threads = 5
    per_thread = 20

    def appender(tid: int):
        for i in range(per_thread):
            cs_messaging.send_plain_message(
                "team-y",
                from_name=f"sender-{tid}",
                to_name="codex",
                text=f"msg-{tid}-{i}",
                summary="s",
            )

    threads = [threading.Thread(target=appender, args=(n,)) for n in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    raw = (team_env / "team-y" / "inboxes" / "codex.json").read_text()
    parsed = json.loads(raw)
    assert len(parsed) == num_threads * per_thread

    # Every expected message should be present exactly once.
    expected = {f"msg-{tid}-{i}" for tid in range(num_threads) for i in range(per_thread)}
    actual = {m["text"] for m in parsed}
    assert actual == expected


def test_concurrent_append_and_read_never_loses_unread(team_env: Path):
    """Reader (mark_as_read=True) races against writer. Unread messages
    emitted during the race must all be observed at least once by the reader
    across polls."""
    seen: list[str] = []
    seen_lock = threading.Lock()
    stop = threading.Event()

    def reader():
        while not stop.is_set():
            msgs = protocol_io.read_inbox("team-z", "codex", mark_as_read=True)
            if msgs:
                with seen_lock:
                    seen.extend(m.text for m in msgs)
            time.sleep(0.001)

    def writer():
        for i in range(50):
            cs_messaging.send_plain_message(
                "team-z",
                from_name="team-lead",
                to_name="codex",
                text=f"msg-{i}",
                summary="s",
            )
            time.sleep(0.002)

    # Ensure the inbox file exists before the reader runs.
    cs_messaging.ensure_inbox("team-z", "codex")

    r = threading.Thread(target=reader)
    w = threading.Thread(target=writer)
    r.start()
    w.start()
    w.join()
    # Give reader time to drain final batch before stopping.
    time.sleep(0.1)
    stop.set()
    r.join(timeout=2.0)

    # Final drain — anything still unread.
    final = protocol_io.read_inbox("team-z", "codex", mark_as_read=True)
    with seen_lock:
        for m in final:
            seen.append(m.text)

    expected = {f"msg-{i}" for i in range(50)}
    assert set(seen) >= expected, f"missing messages: {expected - set(seen)}"


def test_read_inbox_returns_empty_on_malformed_file(team_env: Path, tmp_path: Path):
    """If the harness leaves the inbox in a mid-write invalid-JSON state, our
    protocol_io wrapper returns [] instead of crashing."""
    ibx = team_env / "team-w" / "inboxes" / "codex.json"
    ibx.parent.mkdir(parents=True, exist_ok=True)
    ibx.write_text("[{partial")  # clearly malformed

    # cs50victor's raw read would raise; our wrapper should not.
    result = protocol_io.read_inbox("team-w", "codex", mark_as_read=True)
    assert result == []
