"""Client for Codex App Server (experimental JSON-RPC 2.0 interface).

Used by v7.1 to replace the one-shot `codex exec` invocation with a
long-lived App Server session that the adapter can inject turns into
mid-task.

Transport: stdio. Codex CLI 0.120.0 provides `codex app-server` (default
`--listen stdio://`). Protocol verified against `codex app-server
generate-json-schema --out <dir>`; 60 methods, notifications for turn
lifecycle, `turn/steer` as the mid-turn injection primitive.

Design notes:

- One `AppServerClient` per Codex subprocess. Owns the subprocess and a
  reader thread that splits stdout into JSON messages and dispatches
  them to pending requests (by id) or to a notification queue.
- Requests are synchronous from the caller's perspective: you call
  `client.request(method, params)` and get the response back, with an
  optional timeout. The reader thread handles the async dispatch.
- Notifications arrive on `client.notifications` (a `queue.Queue`);
  callers drain it at their leisure. `wait_for_notification(predicate)`
  blocks until a matching notification arrives.
- Shutdown: `client.close()` terminates the subprocess and joins the
  reader thread. Idempotent.
"""

from __future__ import annotations

import io
import json
import queue
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from . import logger


class AppServerError(RuntimeError):
    """Raised on protocol-level errors (JSON-RPC error responses, IO errors,
    timeouts). The adapter's control loop treats these as blocking failures
    on the current task — the caller decides whether to retry."""


@dataclass
class _Pending:
    event: threading.Event
    response: dict | None = None
    error: dict | None = None


class AppServerClient:
    def __init__(
        self,
        *,
        codex_binary: str = "codex",
        extra_args: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self._codex_binary = codex_binary
        self._extra_args = list(extra_args or [])
        self._env = env
        self._proc: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._stderr_reader: threading.Thread | None = None
        self._stopping = threading.Event()
        self._pending: dict[str, _Pending] = {}
        self._pending_lock = threading.Lock()
        self.notifications: "queue.Queue[dict]" = queue.Queue()

    # ---- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._proc is not None:
            raise AppServerError("AppServerClient already started")
        args = [self._codex_binary, "app-server"] + self._extra_args
        logger.info("app_server.start", args=args)
        self._proc = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,  # line-buffered; App Server emits one JSON per line
            env=self._env,
        )
        self._reader = threading.Thread(
            target=self._read_loop, name="app-server-reader", daemon=True
        )
        self._reader.start()
        self._stderr_reader = threading.Thread(
            target=self._drain_stderr, name="app-server-stderr", daemon=True
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
            proc.terminate()
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=timeout)
        finally:
            self._proc = None
        # Unblock any callers waiting on a response.
        with self._pending_lock:
            for pending in self._pending.values():
                pending.error = {
                    "code": -32000,
                    "message": "AppServerClient closed before response arrived",
                }
                pending.event.set()
            self._pending.clear()
        logger.info("app_server.closed")

    def __enter__(self) -> "AppServerClient":
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
        """Send a JSON-RPC request and block for the response.

        Raises AppServerError on timeout, transport failure, or JSON-RPC
        error. Returns the `result` field on success (commonly a dict).
        """
        if self._proc is None or self._proc.stdin is None:
            raise AppServerError("AppServerClient not started")
        req_id = str(uuid.uuid4())
        pending = _Pending(event=threading.Event())
        with self._pending_lock:
            self._pending[req_id] = pending
        msg = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params or {},
        }
        serialized = json.dumps(msg) + "\n"
        logger.debug("app_server.send", method=method, id=req_id)
        try:
            self._proc.stdin.write(serialized)
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            with self._pending_lock:
                self._pending.pop(req_id, None)
            raise AppServerError(f"writing request to App Server failed: {e}") from e

        if not pending.event.wait(timeout=timeout):
            with self._pending_lock:
                self._pending.pop(req_id, None)
            raise AppServerError(
                f"App Server did not respond to {method} within {timeout}s"
            )

        if pending.error is not None:
            code = pending.error.get("code")
            message = pending.error.get("message", "unknown")
            raise AppServerError(f"JSON-RPC error {code}: {message}")
        return pending.response

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Fire-and-forget client notification. No response expected."""
        if self._proc is None or self._proc.stdin is None:
            raise AppServerError("AppServerClient not started")
        msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        serialized = json.dumps(msg) + "\n"
        try:
            self._proc.stdin.write(serialized)
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise AppServerError(f"writing notification to App Server failed: {e}") from e

    # ---- notifications -----------------------------------------------------

    def drain_notifications(self) -> list[dict]:
        """Return all currently queued notifications (non-blocking)."""
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
        """Block until a notification matching `predicate` arrives. Raises
        AppServerError on timeout or if the client is closed while waiting."""
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AppServerError(
                    f"no matching notification within {timeout}s"
                )
            try:
                ev = self.notifications.get(timeout=min(remaining, 1.0))
            except queue.Empty:
                if self._stopping.is_set():
                    raise AppServerError("AppServerClient closed while waiting")
                continue
            if predicate(ev):
                return ev
            # Non-matching notifications are requeued at the back so other
            # consumers can see them. Simpler than branching predicates.
            self.notifications.put(ev)
            # Small sleep to avoid a tight busy-loop when the only queued
            # item is one we keep bouncing.
            time.sleep(0.01)

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
                    logger.debug("app_server.nonjson", line=line[:200])
                    continue
                self._dispatch(msg)
        except (ValueError, OSError) as e:
            # Closed pipe / process went away. Caller will see it via their
            # next request() or notification wait.
            logger.debug("app_server.reader_exit", error=str(e))
        finally:
            logger.debug("app_server.reader_done")

    def _drain_stderr(self) -> None:
        """Forward Codex stderr lines to our logger so they aren't lost."""
        assert self._proc is not None and self._proc.stderr is not None
        try:
            for raw in self._proc.stderr:
                line = raw.rstrip()
                if line:
                    logger.debug("app_server.stderr", line=line[:500])
        except (ValueError, OSError):
            pass

    def _dispatch(self, msg: dict) -> None:
        req_id = msg.get("id")
        # JSON-RPC: responses have id+result or id+error; notifications have
        # method but no id; requests from the server have id+method+params.
        if "method" in msg and req_id is None:
            self.notifications.put(msg)
            return
        if "method" in msg and req_id is not None:
            # Server-originated request — e.g. approval prompts. We don't
            # handle these in v7.1; stub with a default reply so Codex doesn't
            # deadlock waiting. Covered in step #4 of the impl plan (approval
            # policy is already "never" at thread-start time, so this should
            # rarely fire).
            logger.warn(
                "app_server.unhandled_server_request",
                method=msg.get("method"),
                id=req_id,
            )
            return
        if req_id is None:
            logger.warn("app_server.malformed_message", msg_head=str(msg)[:200])
            return
        with self._pending_lock:
            pending = self._pending.pop(str(req_id), None)
        if pending is None:
            logger.debug("app_server.orphan_response", id=req_id)
            return
        if "error" in msg:
            pending.error = msg["error"]
        else:
            pending.response = msg.get("result")
        pending.event.set()

    # ---- helpers for well-known methods -----------------------------------

    def initialize(self, client_info: dict[str, Any] | None = None) -> Any:
        params = {
            "clientInfo": client_info
            or {"name": "claude-anyteam-adapter", "version": "0.1.0"},
        }
        return self.request("initialize", params)

    def thread_start(
        self,
        *,
        cwd: str,
        base_instructions: str | None = None,
        developer_instructions: str | None = None,
        sandbox: str = "danger-full-access",
        approval_policy: str = "never",
        ephemeral: bool = False,
        config: dict[str, Any] | None = None,
        model: str | None = None,
    ) -> str:
        """Start a thread and return its `threadId`.

        Defaults match the v7 sandbox-bypass stance: no approvals, full
        filesystem access within the operator's trust envelope. See
        README "Codex sandbox" for the rationale.
        """
        params: dict[str, Any] = {
            "cwd": cwd,
            "sandbox": sandbox,
            "approvalPolicy": approval_policy,
            "ephemeral": ephemeral,
        }
        if base_instructions is not None:
            params["baseInstructions"] = base_instructions
        if developer_instructions is not None:
            params["developerInstructions"] = developer_instructions
        if config is not None:
            params["config"] = config
        if model is not None:
            params["model"] = model
        result = self.request("thread/start", params)
        return result["thread"]["id"] if isinstance(result.get("thread"), dict) else result["threadId"]

    def turn_start(
        self,
        *,
        thread_id: str,
        text: str,
        output_schema: dict[str, Any] | None = None,
        model: str | None = None,
        effort: str | None = None,
    ) -> str:
        """Start a turn with a single text input. Returns the `turnId`."""
        params: dict[str, Any] = {
            "threadId": thread_id,
            "input": [{"type": "text", "text": text}],
        }
        if output_schema is not None:
            params["outputSchema"] = output_schema
        if model is not None:
            params["model"] = model
        if effort is not None:
            params["effort"] = effort
        result = self.request("turn/start", params)
        turn = result.get("turn")
        if isinstance(turn, dict) and "id" in turn:
            return turn["id"]
        # Some versions may flatten to `turnId` at the top; tolerate.
        if "turnId" in result:
            return result["turnId"]
        raise AppServerError(f"turn/start response missing turn id: {result}")

    def turn_steer(self, *, thread_id: str, expected_turn_id: str, text: str) -> str:
        """Inject additional input into an in-flight turn. Returns the
        resulting `turnId` (may equal `expected_turn_id` or be a fresh id
        depending on how Codex handles the steer).
        """
        result = self.request(
            "turn/steer",
            {
                "threadId": thread_id,
                "expectedTurnId": expected_turn_id,
                "input": [{"type": "text", "text": text}],
            },
        )
        return result["turnId"]

    def turn_interrupt(self, *, thread_id: str, turn_id: str) -> None:
        self.request(
            "turn/interrupt",
            {"threadId": thread_id, "turnId": turn_id},
        )

    # ---- v7.3: thread continuation via fork -------------------------------

    def thread_fork(
        self,
        *,
        thread_id: str,
        cwd: str | None = None,
        base_instructions: str | None = None,
        developer_instructions: str | None = None,
        sandbox: str = "danger-full-access",
        approval_policy: str = "never",
        ephemeral: bool = False,
        config: dict[str, Any] | None = None,
        model: str | None = None,
    ) -> str:
        """Fork an existing thread into a new one that inherits its
        conversational history. Returns the NEW thread id.

        The parent thread must be materialized — see
        `is_thread_materialized` or the `"no rollout found for thread id"`
        error signal from Codex.

        `ephemeral` defaults to False so the forked thread is itself
        fork-able on a subsequent task (v7.3 lineage).
        """
        params: dict[str, Any] = {
            "threadId": thread_id,
            "sandbox": sandbox,
            "approvalPolicy": approval_policy,
            "ephemeral": ephemeral,
        }
        if cwd is not None:
            params["cwd"] = cwd
        if base_instructions is not None:
            params["baseInstructions"] = base_instructions
        if developer_instructions is not None:
            params["developerInstructions"] = developer_instructions
        if config is not None:
            params["config"] = config
        if model is not None:
            params["model"] = model
        try:
            result = self.request("thread/fork", params)
        except AppServerError as e:
            msg = str(e).lower()
            if "no rollout found" in msg or "not materialized" in msg:
                raise AppServerError(
                    "cannot fork from thread "
                    f"{thread_id!r}: parent thread is not materialized "
                    "(likely it was started with ephemeral=True). "
                    "Start the parent with ephemeral=False or fall back to "
                    "a fresh thread/start."
                ) from e
            raise
        thread = result.get("thread") if isinstance(result, dict) else None
        if isinstance(thread, dict) and "id" in thread:
            return thread["id"]
        if isinstance(result, dict) and "threadId" in result:
            return result["threadId"]
        raise AppServerError(f"thread/fork response missing thread id: {result}")

    def thread_read(
        self,
        *,
        thread_id: str,
        include_turns: bool = False,
    ) -> dict[str, Any]:
        """Read a thread's stored state. `include_turns=True` is the
        canonical materialization check — on an unmaterialized thread
        Codex responds with the error 'thread ... is not materialized yet',
        which `request()` surfaces as an `AppServerError`.

        Callers that want a bool should use `is_thread_materialized`
        instead; this method returns the full thread dict on success.
        """
        return self.request(
            "thread/read",
            {"threadId": thread_id, "includeTurns": include_turns},
        )

    def is_thread_materialized(self, thread_id: str) -> bool:
        """True if the thread is materialized (has a rollout file on disk
        Codex can load). False if App Server reports
        'thread ... is not materialized yet' — meaning `thread/fork`
        and `thread/resume` would fail with 'no rollout found'. On a
        fresh client/process, Codex may instead report `thread not
        loaded` for the same pre-materialization state; treat that as
        the same soft-false outcome so callers can fall back cleanly.

        Catches the common error signal via `thread/read(includeTurns=True)`
        and returns False; any other error propagates.

        This is the canonical signal per the v7.3 implementation plan.
        Upstream context: openai/codex#16872.
        """
        try:
            self.thread_read(thread_id=thread_id, include_turns=True)
            return True
        except AppServerError as e:
            msg = str(e).lower()
            if (
                "not materialized" in msg
                or "no rollout" in msg
                or "thread not loaded" in msg
            ):
                return False
            # Something else went wrong — e.g. thread doesn't exist at all,
            # transport failure. Let the caller see it.
            raise
