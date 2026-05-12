"""Client for Codex App Server (experimental JSON-RPC 2.0 interface)."""

from __future__ import annotations

from typing import Any, Callable

from . import logger
from .jsonrpc_stdio import JsonRpcStdioClient, JsonRpcStdioError


class AppServerError(JsonRpcStdioError):
    """Raised on Codex App Server protocol/transport errors."""


class AppServerClient(JsonRpcStdioClient):
    def __init__(
        self,
        *,
        codex_binary: str = "codex",
        extra_args: list[str] | None = None,
        env: dict[str, str] | None = None,
        pre_start_hook: Callable[[], None] | None = None,
    ) -> None:
        self._codex_binary = codex_binary
        self._extra_args = list(extra_args or [])
        self._pre_start_hook = pre_start_hook
        super().__init__(
            argv=[codex_binary, "app-server", *self._extra_args],
            env=env,
            log_prefix="app_server",
            stderr_log_prefix="app_server.stderr",
            # Put the Codex app-server (and any helper subprocesses it forks
            # for auth refresh / model I/O / network handshake) in its own
            # POSIX session so that ``close()`` can SIGTERM the entire process
            # group, not just the leader. Without this, helper children
            # outlive the wrapper's ``client.close()`` and the next per-turn
            # ``AppServerClient`` cold start can collide with their open fds /
            # sockets / cache locks — the long-lived-wrapper
            # "second-or-later cold-start hangs at initialize for 600s"
            # symptom in #40. Mirrors the convention the gemini ACP transport
            # already uses (``backends/gemini/acp.py``:
            # ``client.terminate_process_group(...)``).
            start_new_session=True,
            terminate_process_group=True,
        )
        self._error_cls = AppServerError
        self.last_thread_result: dict[str, Any] | None = None

    def start(self) -> None:
        """Start the child process after any wrapper-side preflight hook."""

        if self._pre_start_hook is not None:
            self._pre_start_hook()
        super().start()

    # ---- transport health / restart ---------------------------------------

    def is_transport_alive(self) -> bool:
        """Return whether the current App Server transport still looks usable.

        The App Server is hosted as a child process behind JSON-RPC stdio.
        During a long turn the process can disappear without any more
        notifications being queued.  Polling this cheap predicate lets the
        caller distinguish "no event yet" from "transport is gone" and decide
        whether to reconnect instead of waiting for the wall-clock timeout.
        """

        proc = self._proc
        if proc is None:
            return False
        if proc.poll() is not None:
            return False
        reader = self._reader
        if reader is not None and not reader.is_alive() and not self._stopping.is_set():
            return False
        return True

    def transport_status(self) -> dict[str, Any]:
        """Best-effort diagnostic details for visibility/log payloads."""

        proc = self._proc
        return {
            "pid": getattr(proc, "pid", None) if proc is not None else None,
            "returncode": proc.poll() if proc is not None else None,
            "reader_alive": bool(self._reader and self._reader.is_alive()),
            "stderr_reader_alive": bool(
                self._stderr_reader and self._stderr_reader.is_alive()
            ),
        }

    def restart(
        self,
        *,
        initialize: bool = True,
        client_info: dict[str, Any] | None = None,
        close_timeout: float = 5.0,
    ) -> Any:
        """Restart the child App Server process and optionally initialize it.

        This is intentionally a local transport restart, not a thread-level
        semantic operation.  Callers that need conversational continuity should
        follow it with `thread_resume()` (or use `reconnect_and_resume()`).
        """

        old_status = self.transport_status()
        logger.warn("app_server.restart_start", old_status=old_status)
        self.close(timeout=close_timeout)
        self.start()
        result = self.initialize(client_info=client_info) if initialize else None
        logger.info("app_server.restart_complete", status=self.transport_status())
        return result

    def reconnect_and_resume(
        self,
        *,
        thread_id: str,
        cwd: str | None = None,
        base_instructions: str | None = None,
        developer_instructions: str | None = None,
        sandbox: str = "danger-full-access",
        approval_policy: str = "never",
        config: dict[str, Any] | None = None,
        model: str | None = None,
        client_info: dict[str, Any] | None = None,
        close_timeout: float = 5.0,
    ) -> dict[str, Any]:
        """Restart the App Server transport, then load an existing thread.

        Returns the raw `thread/resume` response so higher layers can inspect
        populated turns and decide whether the previous turn already completed
        or whether they should start a recovery continuation turn.
        """

        logger.warn(
            "app_server.reconnect_resume_start",
            thread_id=thread_id,
            status=self.transport_status(),
        )
        self.restart(
            initialize=True,
            client_info=client_info,
            close_timeout=close_timeout,
        )
        result = self.thread_resume(
            thread_id=thread_id,
            cwd=cwd,
            base_instructions=base_instructions,
            developer_instructions=developer_instructions,
            sandbox=sandbox,
            approval_policy=approval_policy,
            config=config,
            model=model,
        )
        resumed_thread_id = _thread_id_from_result(result) or thread_id
        logger.info(
            "app_server.reconnect_resume_complete",
            thread_id=thread_id,
            resumed_thread_id=resumed_thread_id,
        )
        return result

    # ---- helpers for well-known methods -----------------------------------

    def initialize(
        self,
        client_info: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> Any:
        """Send the JSON-RPC ``initialize`` handshake.

        ``timeout`` overrides ``request()``'s 600s default; the Codex App
        Server initialize handshake should complete in seconds, not
        minutes — the only successful empirical sample we have is ~17s on
        a parking-ack prompt (issue #40 thread). 600s as the silent
        default made a hung handshake indistinguishable from "agent is
        thinking hard." Callers should pass an explicit budget (the
        adapter loop reads ``CLAUDE_ANYTEAM_APP_SERVER_INITIALIZE_TIMEOUT_S``,
        default 90s).
        """
        params = {
            "clientInfo": client_info
            or {"name": "claude-anyteam-adapter", "version": "0.1.0"},
        }
        if timeout is None:
            return self.request("initialize", params)
        return self.request("initialize", params, timeout=timeout)

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
        self.last_thread_result = result if isinstance(result, dict) else None
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
            self.last_thread_result = result if isinstance(result, dict) else None
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

    def thread_resume(
        self,
        *,
        thread_id: str,
        cwd: str | None = None,
        base_instructions: str | None = None,
        developer_instructions: str | None = None,
        sandbox: str = "danger-full-access",
        approval_policy: str = "never",
        config: dict[str, Any] | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        """Load a persisted thread into this App Server process.

        Codex returns populated `thread.turns` on `thread/resume`, which the
        adapter uses during transport crash recovery to recover a final
        agentMessage if the turn completed while the transport was down.
        """

        params: dict[str, Any] = {
            "threadId": thread_id,
            "sandbox": sandbox,
            "approvalPolicy": approval_policy,
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
        result = self.request("thread/resume", params)
        self.last_thread_result = result if isinstance(result, dict) else None
        if not isinstance(result, dict):
            raise AppServerError(f"thread/resume response was not an object: {result}")
        if _thread_id_from_result(result) is None:
            raise AppServerError(f"thread/resume response missing thread id: {result}")
        return result

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


def _thread_id_from_result(result: dict[str, Any]) -> str | None:
    thread = result.get("thread") if isinstance(result, dict) else None
    if isinstance(thread, dict):
        tid = thread.get("id")
        if isinstance(tid, str) and tid:
            return tid
    tid = result.get("threadId") if isinstance(result, dict) else None
    if isinstance(tid, str) and tid:
        return tid
    return None
