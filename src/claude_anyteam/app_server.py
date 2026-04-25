"""Client for Codex App Server (experimental JSON-RPC 2.0 interface)."""

from __future__ import annotations

from typing import Any

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
    ) -> None:
        self._codex_binary = codex_binary
        self._extra_args = list(extra_args or [])
        super().__init__(
            argv=[codex_binary, "app-server", *self._extra_args],
            env=env,
            log_prefix="app_server",
            stderr_log_prefix="app_server.stderr",
        )
        self._error_cls = AppServerError

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
