"""Tests for the async JSON-RPC client used by v7.1.

Uses a fake subprocess (stdin/stdout as `io.StringIO`-style streams plus
a backing thread to script responses) rather than a real `codex app-server`
process. Lets us assert on the wire shape of requests and on the client's
notification dispatch without burning Codex tokens.
"""

from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from unittest.mock import patch

import pytest

from claude_anyteam.app_server import AppServerClient, AppServerError


class _FakeProcess:
    """Minimal stand-in for subprocess.Popen.

    - `stdin`: accepts writes, parses them as JSON, and calls a scripted
      responder function for each request.
    - `stdout`: delivers pre-queued or scripted JSON-RPC messages line-by-line.
    - `stderr`: empty, read returns immediately.
    """

    def __init__(self, responder):
        self._out_queue: queue.Queue[str] = queue.Queue()
        self._responder = responder
        self.returncode: int | None = None
        self._stdout_reader = _QueueReader(self._out_queue)
        self._stderr_reader = _QueueReader(queue.Queue())
        self.stdin = _StdinWriter(self._feed)
        self.stdout = self._stdout_reader
        self.stderr = self._stderr_reader

    def _feed(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return
        for response_line in self._responder(msg):
            self._out_queue.put(response_line + "\n")

    def inject_notification(self, notif: dict) -> None:
        self._out_queue.put(json.dumps(notif) + "\n")

    def terminate(self) -> None:
        self.returncode = 0
        self._out_queue.put("")

    def kill(self) -> None:
        self.terminate()

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode or 0


class _StdinWriter:
    def __init__(self, feed_fn):
        self._feed = feed_fn
        self._closed = False

    def write(self, data: str) -> int:
        if self._closed:
            raise BrokenPipeError("closed")
        self._feed(data)
        return len(data)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self._closed = True


class _QueueReader:
    def __init__(self, q):
        self._q = q

    def __iter__(self):
        while True:
            line = self._q.get()
            if not line:
                return
            yield line

    def readline(self) -> str:
        line = self._q.get()
        return line

    def close(self) -> None:
        pass


def _launch_client_with(responder):
    fake = _FakeProcess(responder)
    with patch.object(subprocess, "Popen", return_value=fake):
        client = AppServerClient()
        client.start()
    return client, fake


# ---- initialize + request/response -------------------------------------------


def test_initialize_roundtrip():
    def respond(msg):
        assert msg["method"] == "initialize"
        assert msg["params"]["clientInfo"]["name"] == "claude-anyteam-adapter"
        yield json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {"server": "ok"}})

    client, _ = _launch_client_with(respond)
    try:
        result = client.initialize()
        assert result == {"server": "ok"}
    finally:
        client.close()


def test_request_with_params_and_result():
    def respond(msg):
        yield json.dumps(
            {"jsonrpc": "2.0", "id": msg["id"], "result": {"echo": msg["params"]}}
        )

    client, _ = _launch_client_with(respond)
    try:
        got = client.request("test/echo", {"hello": "world"})
        assert got == {"echo": {"hello": "world"}}
    finally:
        client.close()


def test_request_jsonrpc_error_raises():
    def respond(msg):
        yield json.dumps(
            {
                "jsonrpc": "2.0",
                "id": msg["id"],
                "error": {"code": -32602, "message": "invalid params"},
            }
        )

    client, _ = _launch_client_with(respond)
    try:
        with pytest.raises(AppServerError, match="-32602"):
            client.request("broken", {})
    finally:
        client.close()


def test_request_timeout_raises():
    def respond(msg):
        return iter([])

    client, _ = _launch_client_with(respond)
    try:
        with pytest.raises(AppServerError, match="did not respond"):
            client.request("slow", {}, timeout=0.2)
    finally:
        client.close()


# ---- notifications -----------------------------------------------------------


def test_notifications_delivered_to_queue():
    def respond(msg):
        yield json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {}})

    client, fake = _launch_client_with(respond)
    try:
        client.initialize()
        fake.inject_notification(
            {"jsonrpc": "2.0", "method": "TurnStartedNotification", "params": {"turnId": "t1"}}
        )
        fake.inject_notification(
            {"jsonrpc": "2.0", "method": "TurnCompletedNotification", "params": {"turnId": "t1"}}
        )
        time.sleep(0.2)
        drained = client.drain_notifications()
        methods = [n.get("method") for n in drained]
        assert "TurnStartedNotification" in methods
        assert "TurnCompletedNotification" in methods
    finally:
        client.close()


def test_wait_for_notification_predicate_matches():
    def respond(msg):
        yield json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {}})

    client, fake = _launch_client_with(respond)
    try:
        client.initialize()
        fake.inject_notification(
            {"jsonrpc": "2.0", "method": "AgentMessageDeltaNotification", "params": {"text": "hi"}}
        )
        fake.inject_notification(
            {"jsonrpc": "2.0", "method": "TurnCompletedNotification", "params": {"turnId": "t42"}}
        )
        notif = client.wait_for_notification(
            lambda n: n.get("method") == "TurnCompletedNotification",
            timeout=2.0,
        )
        assert notif["params"]["turnId"] == "t42"
    finally:
        client.close()


def test_wait_for_notification_times_out():
    def respond(msg):
        yield json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {}})

    client, _ = _launch_client_with(respond)
    try:
        client.initialize()
        with pytest.raises(AppServerError, match="no matching notification"):
            client.wait_for_notification(lambda n: False, timeout=0.2)
    finally:
        client.close()


# ---- close semantics ---------------------------------------------------------


def test_close_unblocks_pending_requests():
    def respond(msg):
        return iter([])

    client, _ = _launch_client_with(respond)
    try:
        result_holder = {}

        def caller():
            try:
                client.request("will-be-cancelled", {}, timeout=5.0)
            except AppServerError as e:
                result_holder["err"] = str(e)

        t = threading.Thread(target=caller)
        t.start()
        time.sleep(0.2)
        client.close()
        t.join(timeout=2.0)
        assert not t.is_alive()
        assert "closed" in result_holder.get("err", "").lower()
    finally:
        client.close()


def test_close_is_idempotent():
    def respond(msg):
        yield json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {}})

    client, _ = _launch_client_with(respond)
    client.close()
    client.close()


# ---- helper methods ----------------------------------------------------------


def test_thread_start_sends_correct_params():
    captured: dict = {}

    def respond(msg):
        if msg.get("method") == "thread/start":
            captured["params"] = msg["params"]
            yield json.dumps(
                {"jsonrpc": "2.0", "id": msg["id"], "result": {"thread": {"id": "tid-1"}}}
            )
        else:
            yield json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {}})

    client, _ = _launch_client_with(respond)
    try:
        tid = client.thread_start(
            cwd="/work",
            base_instructions="be a teammate",
            developer_instructions="use the tools",
        )
        assert tid == "tid-1"
        params = captured["params"]
        assert params["cwd"] == "/work"
        assert params["sandbox"] == "danger-full-access"
        assert params["approvalPolicy"] == "never"
        assert params["ephemeral"] is False
        assert params["baseInstructions"] == "be a teammate"
        assert params["developerInstructions"] == "use the tools"
    finally:
        client.close()


def test_turn_start_with_output_schema():
    captured: dict = {}

    def respond(msg):
        if msg.get("method") == "turn/start":
            captured["params"] = msg["params"]
            yield json.dumps(
                {"jsonrpc": "2.0", "id": msg["id"], "result": {"turn": {"id": "turn-9"}}}
            )
        else:
            yield json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {}})

    client, _ = _launch_client_with(respond)
    try:
        schema = {"type": "object", "properties": {"x": {"type": "integer"}}}
        tid = client.turn_start(
            thread_id="tid-1", text="do the thing", output_schema=schema
        )
        assert tid == "turn-9"
        params = captured["params"]
        assert params["threadId"] == "tid-1"
        assert params["input"] == [{"type": "text", "text": "do the thing"}]
        assert params["outputSchema"] == schema
    finally:
        client.close()


def test_turn_steer_sends_expected_turn_id():
    captured: dict = {}

    def respond(msg):
        if msg.get("method") == "turn/steer":
            captured["params"] = msg["params"]
            yield json.dumps(
                {"jsonrpc": "2.0", "id": msg["id"], "result": {"turnId": "turn-9b"}}
            )
        else:
            yield json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {}})

    client, _ = _launch_client_with(respond)
    try:
        got = client.turn_steer(
            thread_id="tid-1", expected_turn_id="turn-9", text="change of plan"
        )
        assert got == "turn-9b"
        params = captured["params"]
        assert params["expectedTurnId"] == "turn-9"
        assert params["input"] == [{"type": "text", "text": "change of plan"}]
    finally:
        client.close()
