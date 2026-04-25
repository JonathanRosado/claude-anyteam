"""Gemini CLI Agent Client Protocol (ACP) JSON-RPC client."""

from __future__ import annotations

import os
import uuid
import shutil
import subprocess
from pathlib import Path
from typing import Any

from claude_anyteam.jsonrpc_stdio import JsonRpcStdioClient, JsonRpcStdioError


class GeminiAcpError(JsonRpcStdioError):
    """Raised on Gemini ACP protocol/transport errors."""


class GeminiAcpTimeoutError(GeminiAcpError):
    """Raised when Gemini ACP does not answer a JSON-RPC request in time."""


class GeminiAcpAuthenticationError(GeminiAcpError):
    """Raised when Gemini ACP authentication fails."""


TRUST_MODES = {"trusted", "default", "plan"}


def permission_request_label(params: Any) -> str:
    """Return a compact human-readable label for ACP permission details."""
    if isinstance(params, dict):
        for key in ("title", "tool", "toolName", "tool_name", "command", "description"):
            value = params.get(key)
            if isinstance(value, str) and value:
                return value
        tool_call = params.get("toolCall") or params.get("tool_call")
        if isinstance(tool_call, dict):
            for key in ("title", "name", "toolName", "tool_name", "command"):
                value = tool_call.get(key)
                if isinstance(value, str) and value:
                    return value
    return "a tool/action"


def detect_acp_flag(gemini_binary: str = "gemini") -> str:
    """Return the supported Gemini CLI ACP flag, preferring the stable spelling.

    Older Gemini CLI builds advertised only ``--experimental-acp``.  If probing
    fails, keep the historical default so callers surface the underlying launch
    failure rather than failing during construction.
    """

    resolved = shutil.which(gemini_binary) or gemini_binary
    try:
        help_out = subprocess.run(
            [resolved, "--help"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "--acp"
    help_text = (help_out.stdout or "") + (help_out.stderr or "")
    if "--acp" in help_text:
        return "--acp"
    if "--experimental-acp" in help_text:
        return "--experimental-acp"
    return "--acp"


class GeminiAcpClient(JsonRpcStdioClient):
    def __init__(
        self,
        *,
        gemini_binary: str = "gemini",
        env: dict[str, str] | None = None,
        debug: bool = False,
        extra_args: list[str] | None = None,
        acp_flag: str | None = None,
        trust_mode: str = "trusted",
    ) -> None:
        if trust_mode not in TRUST_MODES:
            raise ValueError(f"Gemini ACP trust mode must be trusted, default, or plan, got {trust_mode!r}")
        argv = [gemini_binary, acp_flag or detect_acp_flag(gemini_binary)]
        if debug:
            argv.append("--debug")
        argv.extend(extra_args or [])
        super().__init__(
            argv=argv,
            env=env,
            log_prefix="gemini_acp",
            stderr_log_prefix="gemini_acp.stderr",
            start_new_session=(os.name == "posix"),
            terminate_process_group=(os.name == "posix"),
        )
        self._error_cls = GeminiAcpError
        self._timeout_error_cls = GeminiAcpTimeoutError
        self.trust_mode = trust_mode
        self.permission_blocked: dict[str, Any] | None = None

    def initialize(
        self,
        *,
        client_info: dict[str, Any] | None = None,
        client_capabilities: dict[str, Any] | None = None,
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        params = {
            "protocolVersion": 1,
            "clientInfo": client_info
            or {
                "name": "claude-anyteam-gemini-adapter",
                "title": "Claude AnyTeam Gemini Adapter",
                "version": "0.1.0",
            },
            "clientCapabilities": client_capabilities
            or {
                "fs": {"readTextFile": False, "writeTextFile": False},
                "terminal": False,
                "auth": {"terminal": False},
            },
        }
        result = self.request("initialize", params, timeout=timeout)
        protocol_version = result.get("protocolVersion") if isinstance(result, dict) else None
        if protocol_version != 1:
            raise GeminiAcpError(
                f"Gemini ACP initialize returned unsupported protocolVersion {protocol_version!r}; expected 1"
            )
        return result

    def authenticate(self, method_id: str, *, timeout: float = 300.0) -> dict[str, Any]:
        return self.request("authenticate", {"methodId": method_id}, timeout=timeout)

    def session_new(
        self,
        *,
        cwd: str | Path,
        mcp_servers: list[dict[str, Any]] | None = None,
        timeout: float = 120.0,
    ) -> dict[str, Any]:
        return self.request(
            "session/new",
            {"cwd": str(cwd), "mcpServers": mcp_servers or []},
            timeout=timeout,
        )

    def session_load(
        self,
        *,
        session_id: str,
        cwd: str | Path,
        mcp_servers: list[dict[str, Any]] | None = None,
        timeout: float = 120.0,
    ) -> dict[str, Any]:
        return self.request(
            "session/load",
            {"sessionId": session_id, "cwd": str(cwd), "mcpServers": mcp_servers or []},
            timeout=timeout,
        )

    def session_prompt(
        self,
        *,
        session_id: str,
        prompt: str | list[dict[str, Any]],
        message_id: str | None = None,
        timeout: float = 900.0,
    ) -> dict[str, Any]:
        prompt_blocks = prompt if isinstance(prompt, list) else [{"type": "text", "text": prompt}]
        return self.request(
            "session/prompt",
            {
                "sessionId": session_id,
                "prompt": prompt_blocks,
                "messageId": message_id or f"anyteam-{uuid.uuid4()}",
            },
            timeout=timeout,
        )

    def session_cancel(self, *, session_id: str) -> None:
        # Empirically Gemini 0.39.0 implements session/cancel as a notification.
        self.notify("session/cancel", {"sessionId": session_id})

    def set_session_mode(
        self,
        *,
        session_id: str,
        mode_id: str,
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        return self.request(
            "session/set_mode",
            {"sessionId": session_id, "modeId": mode_id},
            timeout=timeout,
        )

    def unstable_set_session_model(
        self,
        *,
        session_id: str,
        model_id: str,
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        return self.request(
            "session/set_model",
            {"sessionId": session_id, "modelId": model_id},
            timeout=timeout,
        )

    def handle_server_request(self, msg: dict[str, Any]) -> Any:
        if msg.get("method") == "session/request_permission":
            if self.trust_mode == "trusted":
                return {"outcome": {"outcome": "selected", "optionId": "allow_once"}}
            params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
            label = permission_request_label(params)
            self.permission_blocked = {
                "trust_mode": self.trust_mode,
                "label": label,
                "params": params,
            }
            # Wake the in-flight session/prompt request so acp.run can mark the
            # task blocked immediately instead of waiting for Gemini to settle.
            with self._pending_lock:
                for pending in self._pending.values():
                    pending.error = {
                        "code": -32001,
                        "message": f"Gemini ACP permission denied for {label}; trust_mode={self.trust_mode}",
                        "data": self.permission_blocked,
                    }
                    pending.event.set()
            return {"outcome": {"outcome": "selected", "optionId": "cancel"}}
        return None
