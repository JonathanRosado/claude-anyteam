from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from unittest.mock import patch

import pytest

from claude_anyteam.jsonrpc_stdio import JsonRpcStdioClient, JsonRpcStdioError


class _FakeProcess:
    def __init__(self, responder):
        self._out_queue: queue.Queue[str] = queue.Queue()
        self._responder = responder
        self.returncode: int | None = None
        self.stdin = _StdinWriter(self._feed)
        self.stdout = _QueueReader(self._out_queue)
        self.stderr = _QueueReader(queue.Queue())
        self.writes: list[dict] = []

    def _feed(self, raw: str) -> None:
        msg = json.loads(raw)
        self.writes.append(msg)
        for line in self._responder(msg):
            self._out_queue.put(line + "\n")

    def inject(self, msg: dict | str) -> None:
        self._out_queue.put((msg if isinstance(msg, str) else json.dumps(msg)) + "\n")

    def terminate(self) -> None:
        self.returncode = 0
        self._out_queue.put("")

    def kill(self) -> None:
        self.terminate()

    def wait(self, timeout=None):
        return self.returncode or 0


class _StdinWriter:
    def __init__(self, feed):
        self._feed = feed
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


def _client(responder):
    fake = _FakeProcess(responder)
    with patch.object(subprocess, "Popen", return_value=fake):
        c = JsonRpcStdioClient(argv=["fake"], log_prefix="test")
        c.start()
    return c, fake


def test_request_response_and_nonjson_tolerance():
    def respond(msg):
        yield "startup banner"
        yield json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {"ok": msg["params"]}})

    client, _ = _client(respond)
    try:
        assert client.request("echo", {"x": 1}) == {"ok": {"x": 1}}
    finally:
        client.close()


def test_notifications_timeout_and_dispatcher_server_request():
    class ApprovingClient(JsonRpcStdioClient):
        def handle_server_request(self, msg):
            return {"approved": msg["params"]["thing"]}

    fake = _FakeProcess(lambda msg: iter([]))
    with patch.object(subprocess, "Popen", return_value=fake):
        client = ApprovingClient(argv=["fake"], log_prefix="test")
        client.start()
    try:
        fake.inject({"jsonrpc": "2.0", "method": "note", "params": {"a": 1}})
        assert client.wait_for_notification(lambda n: n.get("method") == "note", timeout=1)["params"] == {"a": 1}
        with pytest.raises(JsonRpcStdioError, match="no matching notification"):
            client.wait_for_notification(lambda n: False, timeout=0.1)
        fake.inject({"jsonrpc": "2.0", "id": "srv1", "method": "ask", "params": {"thing": "yes"}})
        time.sleep(0.1)
        assert {"jsonrpc": "2.0", "id": "srv1", "result": {"approved": "yes"}} in fake.writes
    finally:
        client.close()


def test_request_timeout_unblocks_on_close():
    client, _ = _client(lambda msg: iter([]))
    try:
        with pytest.raises(JsonRpcStdioError, match="did not respond"):
            client.request("slow", {}, timeout=0.1)
        got: dict[str, str] = {}

        def call():
            try:
                client.request("blocked", {}, timeout=5)
            except JsonRpcStdioError as e:
                got["error"] = str(e)

        t = threading.Thread(target=call)
        t.start()
        time.sleep(0.1)
        client.close()
        t.join(2)
        assert "closed" in got["error"].lower()
    finally:
        client.close()


def test_process_group_close_sends_signal_to_group(monkeypatch):
    fake = _FakeProcess(lambda msg: iter([]))
    fake.pid = 4242
    calls = []

    def fake_popen(*args, **kwargs):
        assert kwargs["start_new_session"] is True
        return fake

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr("os.getpgid", lambda pid: 9001)
    monkeypatch.setattr("os.killpg", lambda pgid, sig: calls.append((pgid, sig)))

    client = JsonRpcStdioClient(
        argv=["fake"],
        log_prefix="test",
        start_new_session=True,
        terminate_process_group=True,
    )
    client.start()
    assert client.pid == 4242
    assert client.pgid == 9001
    client.close()

    assert calls
    assert calls[0][0] == 9001
