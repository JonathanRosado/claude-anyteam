"""Tolerant newline-delimited JSON-RPC 2.0 over stdio transport."""

from __future__ import annotations

import io
import json
import os
import queue
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from . import logger


class JsonRpcStdioError(RuntimeError):
    """Raised on JSON-RPC stdio transport or protocol errors."""


class JsonRpcStdioTimeoutError(JsonRpcStdioError):
    """Raised when a JSON-RPC stdio request times out waiting for a response."""


@dataclass
class _Pending:
    event: threading.Event
    response: dict | None = None
    error: dict | None = None


class JsonRpcStdioClient:
    def __init__(
        self,
        *,
        argv: list[str],
        env: dict[str, str] | None = None,
        log_prefix: str = "jsonrpc_stdio",
        stderr_log_prefix: str | None = None,
        start_new_session: bool = False,
        terminate_process_group: bool = False,
    ) -> None:
        self._argv = list(argv)
        self._env = env
        self._log_prefix = log_prefix
        self._stderr_log_prefix = stderr_log_prefix or f"{log_prefix}.stderr"
        self._start_new_session = start_new_session
        self._terminate_process_group = terminate_process_group
        self._error_cls: type[JsonRpcStdioError] = JsonRpcStdioError
        self._timeout_error_cls: type[JsonRpcStdioError] | None = None
        self._proc: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._stderr_reader: threading.Thread | None = None
        self._stopping = threading.Event()
        self._pending: dict[str, _Pending] = {}
        self._pending_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self.notifications: "queue.Queue[dict]" = queue.Queue()

    # ---- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._proc is not None:
            raise self._error(f"{self.__class__.__name__} already started")
        logger.info(f"{self._log_prefix}.start", args=self._argv)
        self._stopping.clear()
        self._proc = subprocess.Popen(
            self._argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=self._env,
            start_new_session=self._start_new_session,
        )
        self._reader = threading.Thread(
            target=self._read_loop, name=f"{self._log_prefix}-reader", daemon=True
        )
        self._reader.start()
        self._stderr_reader = threading.Thread(
            target=self._drain_stderr, name=f"{self._log_prefix}-stderr", daemon=True
        )
        self._stderr_reader.start()

    def close(self, *, timeout: float = 5.0) -> None:
        if self._proc is None:
            return
        self._stopping.set()
        proc = self._proc
        try:
            if proc.stdin is not None:
                try:
                    proc.stdin.close()
                except (BrokenPipeError, OSError):
                    pass
            self._terminate_process(proc, timeout=timeout)
        finally:
            self._proc = None
        with self._pending_lock:
            for pending in self._pending.values():
                pending.error = {
                    "code": -32000,
                    "message": f"{self.__class__.__name__} closed before response arrived",
                }
                pending.event.set()
            self._pending.clear()
        logger.info(f"{self._log_prefix}.closed")


    @property
    def pid(self) -> int | None:
        proc = self._proc
        pid = getattr(proc, "pid", None) if proc is not None else None
        return pid if isinstance(pid, int) else None

    @property
    def pgid(self) -> int | None:
        pid = self.pid
        if pid is None or os.name != "posix":
            return None
        try:
            return os.getpgid(pid)
        except OSError:
            return None

    @property
    def argv(self) -> list[str]:
        return list(self._argv)

    def terminate_process_group(self, *, sig: int = signal.SIGTERM, timeout: float = 5.0) -> None:
        proc = self._proc
        if proc is None:
            return
        if os.name != "posix":
            self._terminate_process(proc, timeout=timeout)
            return
        self._signal_process_group_posix(proc, sig=sig)
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._signal_process_group_posix(proc, sig=signal.SIGKILL)
            proc.wait(timeout=timeout)

    def _signal_process_group_posix(self, proc: subprocess.Popen, *, sig: int) -> None:
        pid = getattr(proc, "pid", None)
        if not isinstance(pid, int):
            return
        try:
            pgid = os.getpgid(pid)
        except OSError:
            return
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return
        except OSError as e:
            logger.warn(f"{self._log_prefix}.killpg_failed", pgid=pgid, signum=sig, error=str(e))

    def _terminate_process(self, proc: subprocess.Popen, *, timeout: float) -> None:
        if self._terminate_process_group and os.name == "posix":
            self._signal_process_group_posix(proc, sig=signal.SIGTERM)
            try:
                proc.wait(timeout=timeout)
                return
            except subprocess.TimeoutExpired:
                self._signal_process_group_posix(proc, sig=signal.SIGKILL)
                proc.wait(timeout=timeout)
                return
        proc.terminate()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=timeout)

    def __enter__(self) -> "JsonRpcStdioClient":
        self.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    # ---- request/response --------------------------------------------------

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 600.0,
    ) -> Any:
        if self._proc is None or self._proc.stdin is None:
            raise self._error(f"{self.__class__.__name__} not started")
        req_id = str(uuid.uuid4())
        pending = _Pending(event=threading.Event())
        with self._pending_lock:
            self._pending[req_id] = pending
        msg = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}
        logger.debug(f"{self._log_prefix}.send", method=method, id=req_id)
        try:
            self._write_message(msg)
        except (BrokenPipeError, OSError) as e:
            with self._pending_lock:
                self._pending.pop(req_id, None)
            raise self._error(f"writing request to JSON-RPC stdio process failed: {e}") from e

        if not pending.event.wait(timeout=timeout):
            with self._pending_lock:
                self._pending.pop(req_id, None)
            raise self._timeout_error(
                f"JSON-RPC stdio process did not respond to {method} within {timeout}s"
            )

        if pending.error is not None:
            code = pending.error.get("code")
            message = pending.error.get("message", "unknown")
            data = pending.error.get("data")
            detail = f"JSON-RPC error {code}: {message}"
            if data is not None:
                detail += f"; data={data!r}"
            raise self._error(detail)
        return pending.response

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise self._error(f"{self.__class__.__name__} not started")
        msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        try:
            self._write_message(msg)
        except (BrokenPipeError, OSError) as e:
            raise self._error(f"writing notification to JSON-RPC stdio process failed: {e}") from e

    # ---- notifications -----------------------------------------------------

    def drain_notifications(self) -> list[dict]:
        out: list[dict] = []
        while True:
            try:
                out.append(self.notifications.get_nowait())
            except queue.Empty:
                break
        return out

    def wait_for_notification(
        self,
        predicate: Callable[[dict], bool],
        *,
        timeout: float = 600.0,
    ) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise self._error(f"no matching notification within {timeout}s")
            try:
                ev = self.notifications.get(timeout=min(remaining, 1.0))
            except queue.Empty:
                if self._stopping.is_set():
                    raise self._error(f"{self.__class__.__name__} closed while waiting")
                continue
            if predicate(ev):
                return ev
            self.notifications.put(ev)
            time.sleep(0.01)

    # ---- overridable hooks -------------------------------------------------

    def handle_server_request(self, msg: dict[str, Any]) -> Any:
        """Return a JSON-RPC result for server-originated requests, or None.

        Subclasses may override. Returning None leaves the request unanswered,
        preserving the historical Codex app-server behavior.
        """
        return None

    # ---- reader thread -----------------------------------------------------

    def _read_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        stdout: io.TextIOBase = self._proc.stdout  # type: ignore[assignment]
        try:
            for raw in stdout:
                if self._stopping.is_set():
                    break
                line = raw.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    logger.debug(f"{self._log_prefix}.nonjson", line=line[:200])
                    continue
                if isinstance(msg, dict):
                    self._dispatch(msg)
                else:
                    logger.debug(f"{self._log_prefix}.nonobject", line=line[:200])
        except (ValueError, OSError) as e:
            logger.debug(f"{self._log_prefix}.reader_exit", error=str(e))
        finally:
            logger.debug(f"{self._log_prefix}.reader_done")

    def _drain_stderr(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        try:
            for raw in self._proc.stderr:
                line = raw.rstrip()
                if line:
                    logger.debug(self._stderr_log_prefix, line=line[:500])
        except (ValueError, OSError):
            pass

    def _dispatch(self, msg: dict) -> None:
        req_id = msg.get("id")
        if "method" in msg and req_id is None:
            self.notifications.put(msg)
            return
        if "method" in msg and req_id is not None:
            result = self.handle_server_request(msg)
            if result is None:
                logger.warn(f"{self._log_prefix}.unhandled_server_request", method=msg.get("method"), id=req_id)
                return
            try:
                self._write_message({"jsonrpc": "2.0", "id": req_id, "result": result})
            except (BrokenPipeError, OSError) as e:
                logger.warn(f"{self._log_prefix}.server_request_reply_failed", method=msg.get("method"), id=req_id, error=str(e))
            return
        if req_id is None:
            logger.warn(f"{self._log_prefix}.malformed_message", msg_head=str(msg)[:200])
            return
        with self._pending_lock:
            pending = self._pending.pop(str(req_id), None)
        if pending is None:
            logger.debug(f"{self._log_prefix}.orphan_response", id=req_id)
            return
        if "error" in msg:
            pending.error = msg["error"]
        else:
            pending.response = msg.get("result")
        pending.event.set()

    def _write_message(self, msg: dict[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise BrokenPipeError("process stdin is closed")
        serialized = json.dumps(msg) + "\n"
        with self._write_lock:
            self._proc.stdin.write(serialized)
            self._proc.stdin.flush()

    def _error(self, message: str) -> JsonRpcStdioError:
        return self._error_cls(message)

    def _timeout_error(self, message: str) -> JsonRpcStdioError:
        return (self._timeout_error_cls or self._error_cls)(message)
