"""v7 narrowed MCP server exposing a safe tool subset to the Codex subprocess.

**Why a narrowed MCP surface.** The full team-control surface includes
destructive lifecycle operations (`team_delete`, `force_kill_teammate`,
`force_kill_team`, `spawn_teammate`, `team_create`, `process_shutdown_approved`,
`check_teammate`) that have no business being accessible from a running
teammate's context. A hallucinated tool call to any of them would have
outsized consequences.

Rather than rely on prompt discipline, this wrapper exposes **only the
small tool set a Codex teammate actually needs mid-task**, with descriptions
tuned for the team-protocol context and team/agent identity pre-filled
from startup env so Codex can't accidentally send as the wrong teammate.

The wrapper delegates internally to the `claude_teams` team-protocol
implementation for file I/O, locking, and schema handling. This keeps
the v6 invariants intact while narrowing the surface Codex sees.

Launched as a stdio subprocess by Codex via `-c mcp_servers.*.command=...`
overrides on `codex exec`. Lifetime matches the Codex invocation.

Environment:
- `CLAUDE_ANYTEAM_TEAM` — our team name (required).
- `CLAUDE_ANYTEAM_NAME` — our teammate name within the team (required).
- `CLAUDE_ANYTEAM_TASK_ID` — optional task-turn scope for manifest freshness.

Legacy `CODEX_TEAMMATE_*` identity vars are still honored as fallbacks during the rebrand.
"""

from __future__ import annotations

import fnmatch
import functools
import inspect
import json
import logging
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Literal, TypeVar, cast

from .capabilities import manifest_accepts_peer_steer
from .capability_manifest import CapabilityManifestCache
from .env import (
    CWD_ENV,
    LEGACY_CWD_ENV,
    LEGACY_NAME_ENV,
    LEGACY_TEAM_ENV,
    NAME_ENV,
    TEAM_ENV,
    env_first,
)
from .messages import (
    BatchSummaryChild,
    BatchSummaryPayload,
    KNOWN_TASK_BLOCKED_REASONS,
    VisibilityEvent,
    is_token_shaped_reason,
    parse_protocol_text,
)
from .skill_discovery import decode_bytes as _decode_bytes
from .skill_discovery import discover_skills as _discover_skills
from . import protocol_io
from .wrapper_mcp_diagnostics import append_wrapper_mcp_diagnostic
from claude_teams import messaging as _cs_messaging  # type: ignore[import-untyped]
from claude_teams import tasks as _cs_tasks  # type: ignore[import-untyped]
from claude_teams import teams as _cs_teams  # type: ignore[import-untyped]
from claude_teams.models import InboxMessage as _InboxMessage  # type: ignore[import-untyped]
from claude_teams.models import TeammateMember as _TeammateMember  # type: ignore[import-untyped]
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

logger = logging.getLogger("claude_anyteam.wrapper")

TASK_ID_ENV = "CLAUDE_ANYTEAM_TASK_ID"
LEGACY_TASK_ID_ENV = "CODEX_TEAMMATE_TASK_ID"
SEND_MESSAGE_KINDS = (
    "informational",
    "steer",
    "handoff",
    "idle_notification",
    "task_complete",
    "task_blocked",
    "plan_blocked",
    "plan_approval_request",
    "plan_approval_response",
    "permission_request",
    "shutdown_approved",
    "shutdown_rejected",
)
LIFECYCLE_SEND_MESSAGE_KINDS = frozenset(SEND_MESSAGE_KINDS) - {
    "informational",
    "steer",
    "handoff",
}


class PeerSteerManifestCheckError(ToolError):
    """Peer-steer was refused by the sender-side manifest precondition."""


def _flag_disabled(value: str | None) -> bool:
    return value is not None and value.strip().lower() in {"1", "true", "yes", "on"}


def _flag_enabled(value: str | None, *, default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _enforce_peer_steer_manifest_check() -> bool:
    """S10c ablation knob for wrapper-side peer-steer enforcement."""

    if _flag_disabled(os.environ.get("CLAUDE_ANYTEAM_DISABLE_PEER_STEER_MANIFEST_CHECK")):
        return False
    return _flag_enabled(
        os.environ.get("CLAUDE_ANYTEAM_ENFORCE_PEER_STEER_MANIFEST_CHECK"),
        default=True,
    )


def _peer_steer_manifest_max_age_turns() -> int:
    raw = os.environ.get("CLAUDE_ANYTEAM_PEER_STEER_MANIFEST_MAX_AGE_TURNS")
    if raw in (None, ""):
        return 1
    try:
        return max(0, int(raw))
    except ValueError:
        logger.warning(
            "invalid CLAUDE_ANYTEAM_PEER_STEER_MANIFEST_MAX_AGE_TURNS=%r; using 1",
            raw,
        )
        return 1

# Tool set we deliberately expose to Codex. Checked by a test so additions
# require intent. Order here matches the help-text ordering Codex will see.
EXPOSED_TOOLS: tuple[str, ...] = (
    "send_message",
    "task_update",
    "checkpoint_commit",
    "task_create",
    "task_batch_summary",
    "read_inbox",
    "task_list",
    "read_config",
    "mcp_anyteam_capability_manifest",
    "mcp_anyteam_list_skills",
    "mcp_anyteam_invoke_skill",
    "mcp_anyteam_shell",
    "mcp_anyteam_read_file",
    "mcp_anyteam_write_file",
    "mcp_anyteam_list_directory",
    "mcp_anyteam_edit_file",
    "mcp_anyteam_search",
    "mcp_anyteam_grep",
    "mcp_anyteam_web_fetch",
)

GEMINI_WRAPPER_TOOL_PREFIX = "mcp_anyteam_"


def _gemini_visible_tool_name(tool_name: str) -> str:
    """Return the Gemini-visible name for one wrapper MCP tool.

    Gemini exposes bare wrapper tools through the configured MCP-server alias
    (``anyteam``), e.g. ``send_message`` becomes
    ``mcp_anyteam_send_message``. Tools that already carry our
    ``mcp_anyteam_*`` prefix are intentionally stable: prompts and prior tests
    refer to ``mcp_anyteam_shell`` rather than a doubled
    ``mcp_anyteam_mcp_anyteam_shell`` name.
    """

    if tool_name.startswith(GEMINI_WRAPPER_TOOL_PREFIX):
        return tool_name
    return f"{GEMINI_WRAPPER_TOOL_PREFIX}{tool_name}"


def _member_field(member: Any, field: str) -> Any:
    if isinstance(member, dict):
        return member.get(field)
    return getattr(member, field, None)


def _detect_caller_backend(
    *,
    self_name: str,
    self_member: Any | None,
    self_manifest: dict[str, Any] | None,
) -> str:
    """Infer the calling routed backend for protocol-tool naming.

    The wrapper process is shared by Codex, Gemini, and Kimi. It receives only
    team/name at startup, so discovery output derives the caller's surface from
    the self Agent Card first (``host_tool_surface`` / ``transport``), then the
    roster row (``model`` / ``backendType``), then the conventional teammate
    name prefix.
    """

    manifest_values: list[Any] = []
    if isinstance(self_manifest, dict):
        manifest_values.extend(
            [
                self_manifest.get("host_tool_surface"),
                self_manifest.get("transport"),
                self_manifest.get("backend_type"),
                self_manifest.get("backendType"),
                self_manifest.get("model"),
                self_manifest.get("agent_name"),
                self_manifest.get("agentName"),
            ]
        )
    if self_member is not None:
        manifest_values.extend(
            [
                _member_field(self_member, "model"),
                _member_field(self_member, "backend_type"),
                _member_field(self_member, "backendType"),
                _member_field(self_member, "agent_type"),
                _member_field(self_member, "agentType"),
                _member_field(self_member, "name"),
            ]
        )
    manifest_values.append(self_name)

    haystack = " ".join(str(value).lower() for value in manifest_values if value is not None)
    if "mcp_anyteam" in haystack or "gemini" in haystack:
        return "gemini"
    if "kimi" in haystack:
        return "kimi"
    if "codex" in haystack:
        return "codex"
    return "unknown"


def _visible_tool_name(tool_name: str, *, caller_backend: str) -> str:
    if caller_backend == "gemini":
        return _gemini_visible_tool_name(tool_name)
    return tool_name


def _protocol_tools_section(
    *,
    self_name: str,
    self_member: Any | None,
    self_manifest: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return self-healing exact wrapper tool names for this caller."""

    backend = _detect_caller_backend(
        self_name=self_name,
        self_member=self_member,
        self_manifest=self_manifest,
    )
    names = [_visible_tool_name(tool, caller_backend=backend) for tool in EXPOSED_TOOLS]

    def mapped_subset(source: frozenset[str]) -> list[str]:
        return [
            _visible_tool_name(tool, caller_backend=backend)
            for tool in EXPOSED_TOOLS
            if tool in source
        ]

    return {
        "backend": backend,
        "naming": "mcp_anyteam_prefixed" if backend == "gemini" else "raw",
        "source": "claude_anyteam.wrapper_server.EXPOSED_TOOLS",
        "tools": names,
        "team_tools": mapped_subset(TEAM_TOOLS),
        "shadow_tools": mapped_subset(SHADOW_TOOLS),
        "send_message": _visible_tool_name("send_message", caller_backend=backend),
        "read_config": _visible_tool_name("read_config", caller_backend=backend),
        "capability_manifest": _visible_tool_name(
            "mcp_anyteam_capability_manifest",
            caller_backend=backend,
        ),
        "guidance": (
            "If you are unsure whether a tool is available, use the exact name "
            "listed here; do not assume unavailability from prose."
        ),
    }

# Full team-control tools that we deliberately do NOT surface. Checked by a
# test so removals are deliberate. If the protocol gains a new tool, the test
# fails and forces a decision about whether it belongs in EXPOSED_TOOLS or
# BLOCKED_TOOLS.
BLOCKED_TOOLS: tuple[str, ...] = (
    "team_create",
    "team_delete",
    "spawn_teammate",
    "force_kill_teammate",
    "force_kill_team",
    "process_shutdown_approved",
    "check_teammate",
)

ToolCategory = Literal["team_tool", "shadow_tool"]

TEAM_TOOLS: frozenset[str] = frozenset(
    {
        "send_message",
        "task_update",
        "checkpoint_commit",
        "task_create",
        "task_batch_summary",
        "read_inbox",
        "task_list",
        "read_config",
        "mcp_anyteam_capability_manifest",
        "mcp_anyteam_list_skills",
        "mcp_anyteam_invoke_skill",
    }
)

SHADOW_TOOLS: frozenset[str] = frozenset(
    {
        "mcp_anyteam_shell",
        "mcp_anyteam_read_file",
        "mcp_anyteam_write_file",
        "mcp_anyteam_edit_file",
        "mcp_anyteam_list_directory",
        "mcp_anyteam_search",
        "mcp_anyteam_grep",
        "mcp_anyteam_web_fetch",
    }
)

TOOL_CATEGORIES: dict[str, ToolCategory] = {
    **{name: "team_tool" for name in TEAM_TOOLS},
    **{name: "shadow_tool" for name in SHADOW_TOOLS},
}

_F = TypeVar("_F", bound=Callable[..., Any])


def _identity(argv: list[str] | None = None) -> tuple[str, str]:
    """Resolve (team, name) for this wrapper process.

    Precedence: CLI flags (`--team`, `--name`) > env vars
    (`CLAUDE_ANYTEAM_TEAM`, `CLAUDE_ANYTEAM_NAME`). Raises RuntimeError
    if neither provides both values.

    CLI args exist because when App Server spawns the wrapper as its
    own MCP subprocess, it does NOT forward our adapter's env into the
    wrapper's env (observed live during task #22 sanity probes — the
    wrapper handshake failed with "connection closed: initialize
    response" because `_identity()` raised). CLI args route around the
    env-forwarding question entirely.
    """
    team: str | None = None
    name: str | None = None

    # Parse only --team/--name without failing on argv we don't recognise,
    # since FastMCP may pass its own stdio-runtime args through sys.argv
    # at some future point. Conservative: accept only the two flags we own.
    args = list(argv) if argv is not None else sys.argv[1:]
    i = 0
    while i < len(args):
        tok = args[i]
        if tok == "--team" and i + 1 < len(args):
            team = args[i + 1]
            i += 2
        elif tok.startswith("--team="):
            team = tok.split("=", 1)[1]
            i += 1
        elif tok == "--name" and i + 1 < len(args):
            name = args[i + 1]
            i += 2
        elif tok.startswith("--name="):
            name = tok.split("=", 1)[1]
            i += 1
        else:
            i += 1

    team = team or env_first(os.environ, TEAM_ENV, LEGACY_TEAM_ENV)
    name = name or env_first(os.environ, NAME_ENV, LEGACY_NAME_ENV)
    if not team or not name:
        raise RuntimeError(
            "claude_anyteam wrapper: team and name are required. "
            "Pass --team/--name as CLI args or set "
            f"{TEAM_ENV}/{NAME_ENV} env vars."
        )
    return team, name


def _task_id_scope(argv: list[str] | None = None) -> str | None:
    """Resolve the optional task id used to scope manifest-query freshness."""

    task_id: str | None = None
    args = list(argv) if argv is not None else sys.argv[1:]
    i = 0
    while i < len(args):
        tok = args[i]
        if tok == "--task-id" and i + 1 < len(args):
            task_id = args[i + 1]
            i += 2
        elif tok.startswith("--task-id="):
            task_id = tok.split("=", 1)[1]
            i += 1
        else:
            i += 1

    return task_id or env_first(os.environ, TASK_ID_ENV, LEGACY_TASK_ID_ENV)


def _cwd_scope(argv: list[str] | None = None) -> Path:
    """Resolve the teammate working directory for filesystem/git tools.

    App Server does not forward the adapter env to MCP subprocesses, so the
    adapter passes ``--cwd`` alongside ``--team``/``--name``. Exec-mode callers
    also set env for backward compatibility. If neither is present, fall back
    to the wrapper process cwd so local tests and manual runs remain usable.
    """

    cwd: str | None = None
    args = list(argv) if argv is not None else sys.argv[1:]
    i = 0
    while i < len(args):
        tok = args[i]
        if tok == "--cwd" and i + 1 < len(args):
            cwd = args[i + 1]
            i += 2
        elif tok.startswith("--cwd="):
            cwd = tok.split("=", 1)[1]
            i += 1
        else:
            i += 1

    raw = cwd or env_first(os.environ, CWD_ENV, LEGACY_CWD_ENV, default=os.getcwd())
    return Path(str(raw)).expanduser().resolve()


def _registered_tool_names(mcp: FastMCP) -> list[str]:
    """Best-effort snapshot of FastMCP's local registered tool names."""

    provider = getattr(mcp, "_local_provider", None)
    components = getattr(provider, "_components", {})
    names: list[str] = []
    if isinstance(components, dict):
        for key, component in components.items():
            name = getattr(component, "name", None)
            if isinstance(name, str) and name:
                names.append(name)
                continue
            if isinstance(key, str) and key.startswith("tool:"):
                names.append(key.removeprefix("tool:").split("@", 1)[0])
    return sorted(set(names))


def _tool_snapshot_payload(tools: list[str] | tuple[str, ...]) -> dict[str, Any]:
    observed = sorted(set(tools))
    expected = list(EXPOSED_TOOLS)
    return {
        "tools": observed,
        "tool_count": len(observed),
        "expected_tools": expected,
        "expected_tool_count": len(expected),
        "missing_expected_tools": [tool for tool in expected if tool not in observed],
        "unexpected_tools": [tool for tool in observed if tool not in expected],
        "send_message_registered": "send_message" in observed,
        "task_update_registered": "task_update" in observed,
        "read_config_registered": "read_config" in observed,
    }

def _entry_for(path: Path, *, base: Path | None = None) -> dict[str, Any]:
    try:
        stat = path.stat()
    except OSError as e:
        return {"path": str(path if base is None else path.relative_to(base)), "error": str(e)}
    kind = "directory" if path.is_dir() else "file" if path.is_file() else "other"
    return {
        "path": str(path if base is None else path.relative_to(base)),
        "name": path.name,
        "type": kind,
        "size": stat.st_size,
        "mtime": stat.st_mtime,
    }


def _preview(value: Any, *, limit: int = 600) -> str | None:
    if value is None:
        return None
    text = str(value)
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _normalise_lifecycle_send_body(kind: str, body: str) -> str:
    """Validate and canonicalize a typed lifecycle body for send_message.

    Ordinary peer DMs deliberately remain plain text. Lifecycle kinds are the
    opposite: callers must provide a JSON object whose ``kind``/``type`` matches
    the ``messageKind`` they are asking the wrapper to stamp, so downstream
    readers can route by the explicit discriminator rather than summary/prose.
    """

    try:
        raw = json.loads(body)
    except (TypeError, ValueError) as exc:
        raise ToolError(
            f"kind={kind!r} requires a JSON protocol payload body"
        ) from exc
    if not isinstance(raw, dict):
        raise ToolError(f"kind={kind!r} requires a JSON object payload")
    actual = raw.get("kind") or raw.get("type")
    if actual != kind:
        raise ToolError(
            f"kind={kind!r} must match payload kind/type; got {actual!r}"
        )
    payload = parse_protocol_text(body)
    if payload is None:
        raise ToolError(f"body is not a valid {kind!r} protocol payload")
    return payload.model_dump_json(by_alias=True, exclude_none=True)


def _task_blocked_reason_drift(body: str) -> str | None:
    """Return the offending reason if a typed ``task_blocked`` body uses a
    token-shaped reason that is not in ``KNOWN_TASK_BLOCKED_REASONS``.

    #40 Phase 1, graduated rung 1+2 (declare + suggest): the
    ``task_blocked.reason`` field is an open ``str`` for backward compat,
    but new machine-readable tokens (e.g. ``app_server_initialize_timeout``)
    are registered. Token-shaped reasons that are NOT in the registry
    likely indicate either a typo or a missing registration step. We
    surface those via a ``visibility_degraded`` warn at the wrapper layer
    so the lead can see drift, without blocking delivery (free-form prose
    reasons remain legal — only ``snake_case`` token-shaped strings are
    policed).
    """

    try:
        raw = json.loads(body)
    except (TypeError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    reason = raw.get("reason")
    if not isinstance(reason, str):
        return None
    if reason in KNOWN_TASK_BLOCKED_REASONS:
        return None
    if not is_token_shaped_reason(reason):
        return None
    return reason


def _run_git(
    cwd: Path,
    args: list[str],
    *,
    timeout: float = 30.0,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _git_failure(command: str, result: subprocess.CompletedProcess[str]) -> ToolError:
    detail = (result.stderr or result.stdout or "").strip()
    if detail:
        return ToolError(
            f"{command} failed with exit code {result.returncode}: {detail}"
        )
    return ToolError(f"{command} failed with exit code {result.returncode}")


def _unmerged_paths(ls_files_u: str) -> list[str]:
    paths: set[str] = set()
    for line in ls_files_u.splitlines():
        if "\t" not in line:
            continue
        _, path = line.split("\t", 1)
        if path:
            paths.add(path)
    return sorted(paths)


def _tool_target(tool_name: str, bound_args: dict[str, Any]) -> str | None:
    """Return a concise, non-secret-ish target label for visibility events."""

    if tool_name == "send_message":
        return f"to={bound_args.get('to')!r}"
    if tool_name == "task_update":
        return f"task_id={bound_args.get('task_id')!r}"
    if tool_name == "checkpoint_commit":
        return _preview(bound_args.get("message"), limit=160)
    if tool_name == "task_create":
        return _preview(bound_args.get("subject"), limit=160)
    if tool_name == "mcp_anyteam_capability_manifest":
        target = f"agent_name={bound_args.get('agent_name')!r}"
        if bound_args.get("capability") is not None:
            target += f" capability={bound_args.get('capability')!r}"
        return target
    if tool_name == "mcp_anyteam_invoke_skill":
        return f"skill_name={bound_args.get('skill_name')!r}"
    if tool_name == "mcp_anyteam_shell":
        return _preview(bound_args.get("command"), limit=240)
    if tool_name in {
        "mcp_anyteam_read_file",
        "mcp_anyteam_write_file",
        "mcp_anyteam_edit_file",
        "mcp_anyteam_list_directory",
    }:
        return str(bound_args.get("path"))
    if tool_name == "mcp_anyteam_search":
        return (
            f"pattern={_preview(bound_args.get('pattern'), limit=120)!r} "
            f"path={bound_args.get('path', '.')!r}"
        )
    if tool_name == "mcp_anyteam_grep":
        return (
            f"regex={_preview(bound_args.get('regex'), limit=120)!r} "
            f"directory={bound_args.get('directory')!r}"
        )
    if tool_name == "mcp_anyteam_web_fetch":
        return str(bound_args.get("url"))
    return None


def _send_message_recipient_from_args(bound_args: dict[str, Any] | None) -> str | None:
    if not bound_args:
        return None
    value = bound_args.get("to")
    if value in (None, ""):
        return None
    return str(value)


def _bind_tool_arguments(
    signature: inspect.Signature,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    try:
        bound = signature.bind_partial(*args, **kwargs)
        bound.apply_defaults()
        return dict(bound.arguments)
    except TypeError:
        # Instrumentation is observational; never fail a tool because we
        # couldn't build the human-readable target string.
        return dict(kwargs)


def _result_exit_code(result: Any) -> int | None:
    if isinstance(result, dict):
        exit_code = result.get("exit_code")
        if isinstance(exit_code, int):
            return exit_code
    return None


def _result_indicates_failure(tool_name: str, result: Any) -> bool:
    # The shell wrapper intentionally returns stdout/stderr/exit_code for the
    # backend to inspect instead of raising on non-zero exit. R18 still treats
    # that as a failed wrapper-tool invocation for lead visibility.
    exit_code = _result_exit_code(result)
    if tool_name == "mcp_anyteam_shell" and exit_code not in (None, 0):
        return True
    # mcp_anyteam_invoke_skill returns ``{"error": "skill_not_found", ...}`` on
    # miss instead of raising — surface that as a typed event-level failure so
    # peer/lead visibility reflects it (§2 visibility parity).
    if tool_name == "mcp_anyteam_invoke_skill" and isinstance(result, dict):
        err = result.get("error")
        if isinstance(err, str) and err:
            return True
    return False


def _tool_result_payload(result: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    exit_code = _result_exit_code(result)
    if exit_code is not None:
        payload["exit_code"] = exit_code
    if isinstance(result, dict):
        for source_key, payload_key in (
            ("stdout", "stdout_preview"),
            ("stderr", "stderr_preview"),
            ("content", "content_preview"),
            ("bytes", "bytes_read"),
            ("bytes_written", "bytes_written"),
            ("chars_written", "chars_written"),
            ("replacements", "replacements"),
            ("status", "http_status"),
            ("error", "error"),
        ):
            value = result.get(source_key)
            if value not in (None, ""):
                payload[payload_key] = (
                    _preview(value) if payload_key.endswith("_preview") else value
                )
    return payload


def _exception_payload(exc: BaseException) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "error_class": exc.__class__.__name__,
        "error": _preview(str(exc), limit=600) or exc.__class__.__name__,
    }
    if isinstance(exc, subprocess.TimeoutExpired):
        payload["timeout_s"] = exc.timeout
        payload["target"] = _preview(exc.cmd, limit=240)
        if exc.stdout not in (None, b"", ""):
            payload["stdout_preview"] = _preview(exc.stdout)
        if exc.stderr not in (None, b"", ""):
            payload["stderr_preview"] = _preview(exc.stderr)
    return payload


def build_server(argv: list[str] | None = None) -> FastMCP:
    """Construct the FastMCP app with the narrowed tools."""
    team, self_name = _identity(argv)
    wrapper_task_id = _task_id_scope(argv)
    wrapper_cwd = _cwd_scope(argv)

    mcp = FastMCP(
        name="claude-anyteam-wrapper",
        instructions=(
            "Narrowed MCP surface for a Codex teammate. Team: "
            f"{team!r}; identity: {self_name!r}. Call these tools when it "
            "would be useful to your teammates — peer or lead updates via "
            "send_message, activeForm/owner/metadata changes via task_update, "
            "durable git checkpoints via checkpoint_commit, subtask creation "
            "via task_create, delegated batch visibility via task_batch_summary, "
            "inspection via read_inbox / task_list / read_config. Destructive "
            "lifecycle operations "
            "(shutdown, spawn, kill) are not available here by design; the "
            "Python adapter owns those."
        ),
    )

    diagnostics_root = _cs_teams.TEAMS_DIR

    def _log_mcp_diag(event: str, payload: dict[str, Any] | None = None) -> None:
        append_wrapper_mcp_diagnostic(
            team=team,
            agent=self_name,
            event=event,
            payload=payload,
            root=diagnostics_root,
        )

    _log_mcp_diag(
        "server_build_start",
        {
            "argv": list(argv) if argv is not None else sys.argv[1:],
            "task_id": wrapper_task_id,
            "expected_tools": list(EXPOSED_TOOLS),
        },
    )

    original_list_tools = mcp.list_tools

    async def _diagnostic_list_tools(*, run_middleware: bool = True):
        started_at = time.monotonic()
        try:
            tools = await original_list_tools(run_middleware=run_middleware)
        except Exception as exc:
            if run_middleware:
                _log_mcp_diag(
                    "list_tools_failed",
                    {
                        "duration_ms": max(0, int((time.monotonic() - started_at) * 1000)),
                        "error_class": exc.__class__.__name__,
                        "error": _preview(str(exc), limit=600),
                        "registered_snapshot": _tool_snapshot_payload(_registered_tool_names(mcp)),
                    },
                )
            raise
        if run_middleware:
            names = sorted(
                name
                for name in (getattr(tool, "name", None) for tool in tools)
                if isinstance(name, str) and name
            )
            payload = _tool_snapshot_payload(names)
            payload["duration_ms"] = max(0, int((time.monotonic() - started_at) * 1000))
            payload["registered_snapshot"] = _tool_snapshot_payload(_registered_tool_names(mcp))
            _log_mcp_diag("list_tools", payload)
        return tools

    mcp.list_tools = _diagnostic_list_tools  # type: ignore[method-assign]

    def register_mcp_tool(func: _F) -> Any:
        tool = mcp.tool(func)
        name = getattr(tool, "name", None) or getattr(func, "__name__", "<unknown>")
        payload = _tool_snapshot_payload(_registered_tool_names(mcp))
        payload["registered_tool"] = name
        _log_mcp_diag("register_tool", payload)
        return tool

    manifest_cache = CapabilityManifestCache(
        team,
        self_name=self_name,
        root=_cs_teams.TEAMS_DIR,
    )
    manifest_cache.load_startup()
    skill_cache = _discover_skills()
    _log_mcp_diag(
        "skills_discovered",
        {
            "skill_count": len(skill_cache),
            "skills": sorted(skill_cache),
        },
    )

    visibility_seq = 0
    wrapper_turn_id = f"wrapper-{os.getpid()}"
    manifest_query_task_by_recipient: dict[str, str] = {}

    def _fanout_visibility_event(
        event: VisibilityEvent,
        *,
        mailbox: bool = False,
    ) -> None:
        """Emit one visibility envelope to stderr/event-log and maybe mailbox.

        The tool body is the user's requested action; visibility fan-out should
        not make an otherwise-successful wrapper tool fail. Validation errors
        happen before this helper constructs ``event``; storage/send failures
        are logged to stderr and swallowed.
        """

        line = event.model_dump_json(by_alias=True, exclude_none=True)
        print(line, file=sys.stderr, flush=True)
        try:
            protocol_io.append_event(team, self_name, event)
        except Exception as e:  # pragma: no cover - defensive logging path
            logger.warning("wrapper visibility event append failed: %s", e)
        if mailbox:
            try:
                protocol_io.send_visibility_event_to_lead(
                    team,
                    self_name,
                    event,
                    summary=event.summary[:120],
                )
            except Exception as e:  # pragma: no cover - defensive logging path
                logger.warning("wrapper visibility mailbox fan-out failed: %s", e)

    def _make_event(
        *,
        kind: str,
        severity: str,
        summary: str,
        payload: dict[str, Any],
        task_id: str | None = None,
        mailbox: bool = False,
    ) -> VisibilityEvent:
        nonlocal visibility_seq
        visibility_seq += 1
        return VisibilityEvent.model_validate(
            {
                "kind": kind,
                "event_id": f"{self_name}:{wrapper_turn_id}:{visibility_seq:06d}",
                "team": team,
                "agent": self_name,
                "backend": "wrapper_mcp",
                "task_id": task_id,
                "turn_id": wrapper_turn_id,
                "seq": visibility_seq,
                "severity": severity,
                "summary": summary[:200],
                "visibility": {
                    "mailbox": mailbox,
                    "task_state": False,
                    "event_log": True,
                    "stderr": True,
                },
                "payload": payload,
            }
        )

    def _emit_tool_event(
        *,
        tool_name: str,
        category: ToolCategory,
        phase: Literal["started", "completed", "failed"],
        target: str | None,
        tool_args: dict[str, Any] | None = None,
        started_at: float | None = None,
        result: Any = None,
        exc: BaseException | None = None,
    ) -> VisibilityEvent:
        payload: dict[str, Any] = {
            "category": category,
            "tool_name": tool_name,
            "phase": phase,
            "raw_backend_type": tool_name,
        }
        if target:
            payload["target"] = target
        if tool_name == "send_message":
            recipient = _send_message_recipient_from_args(tool_args)
            if recipient:
                payload["recipient"] = recipient
                payload["to"] = recipient
                payload["target"] = f"to={recipient!r}"
        if tool_name == "mcp_anyteam_invoke_skill":
            # Typed event field so peer/lead audit can pivot on skill_name
            # without parsing the formatted ``target`` string (§2).
            skill_name_arg = (tool_args or {}).get("skill_name")
            if skill_name_arg not in (None, ""):
                payload["skill_name"] = str(skill_name_arg)
        if started_at is not None:
            payload["duration_ms"] = max(0, int((time.monotonic() - started_at) * 1000))
        if phase == "started":
            summary = f"{tool_name} started"
            severity = "info"
        elif phase == "completed":
            payload["status"] = "success"
            payload.update(_tool_result_payload(result))
            summary = f"{tool_name} completed"
            severity = "info"
        else:
            payload["status"] = "error"
            if exc is not None:
                payload.update(_exception_payload(exc))
            else:
                payload.update(_tool_result_payload(result))
            summary = f"{tool_name} failed"
            severity = "error"
        event = _make_event(
            kind="tool_event",
            severity=severity,
            summary=summary,
            payload=payload,
        )
        _fanout_visibility_event(event)
        return event

    def _emit_visibility_degraded(
        *,
        tool_name: str,
        category: ToolCategory,
        reason: str,
        failed_event_id: str,
    ) -> VisibilityEvent:
        event = _make_event(
            kind="visibility_degraded",
            severity="warn",
            summary=f"wrapper tool failed: {tool_name}",
            mailbox=True,
            payload={
                "surface": "wrapper_tool",
                "tool_name": tool_name,
                "category": category,
                "reason": reason,
                "impact": "The lead can audit the failed wrapper tool call in the event log.",
                "suggested_fix": (
                    "Inspect the failed tool_event payload and retry or steer "
                    "the teammate if needed."
                ),
                "failed_event_id": failed_event_id,
            },
        )
        _fanout_visibility_event(event, mailbox=True)
        return event

    def _record_manifest_query(agent_name: str) -> None:
        manifest_query_task_by_recipient[agent_name] = wrapper_task_id or wrapper_turn_id

    def _recent_manifest_query(agent_name: str) -> bool:
        last_task_id = manifest_query_task_by_recipient.get(agent_name)
        if last_task_id is None:
            return False
        return last_task_id == (wrapper_task_id or wrapper_turn_id)

    def _steer_recipient_refusal_reasons(
        to: str,
        cfg: Any,
    ) -> dict[str, str]:
        """Return peer recipients that cannot receive an explicit steer.

        §3 L3 v2 gates on the recipient's interpretation criteria, not the
        sender's body shape. Post-57, however, this precondition applies only
        when the sender explicitly declares ``kind="steer"``; ordinary
        informational/handoff peer-DMs flow through without relocation gating.

        Matrix lift #5 tightens the precondition from "manifest queried" to
        "manifest-authorized": a recent query may teach the sender that steer
        is *not* permitted, but it must not turn a lead-only recipient into an
        interrupt target.
        """
        if self_name == "team-lead" or to == "team-lead":
            return {}
        if to == "*":
            recipients: list[str] = []
            for member in getattr(cfg, "members", []):
                target = getattr(member, "name", None)
                if target and target not in {self_name, "team-lead"}:
                    recipients.append(target)
        else:
            recipients = [to]

        refusing: dict[str, str] = {}
        for recipient in recipients:
            manifest = manifest_cache.get(recipient)
            if manifest is None:
                # No declaration means we conservatively assume the recipient
                # rejects peer steer and require an explicit manifest lookup.
                refusing[recipient] = "manifest_not_queried"
                continue
            if not manifest_accepts_peer_steer(manifest):
                refusing[recipient] = "manifest_denies_peer_steer"
        return refusing

    def _emit_peer_steer_refused_at_wrapper(
        *,
        recipient: str,
        recipients: list[str],
        reason: str,
    ) -> VisibilityEvent:
        if reason == "manifest_not_queried":
            summary = f"peer steer to {recipient} refused at wrapper; manifest not queried"
            impact = (
                "The peer steer was not delivered; the wrapper has no cached "
                "recipient authorization to avoid spending a backend turn on a reject."
            )
            suggested_fix_suffix = " Then retry only if the manifest permits peer steering."
        else:
            summary = f"peer steer to {recipient} refused at wrapper; manifest denies peer steer"
            impact = (
                "The peer steer was not delivered because the recipient's "
                "manifest does not authorize non-lead steer."
            )
            suggested_fix_suffix = (
                " Send an informational message instead, or ask team-lead to steer."
            )
        guidance = (
            f"Call mcp_anyteam_capability_manifest({recipient!r}, "
            "'turn_steer') before attempting peer-steer."
        )
        event = _make_event(
            kind="visibility_degraded",
            severity="warn",
            summary=summary,
            mailbox=True,
            payload={
                "surface": "peer_steer_refused_at_wrapper",
                "reason": reason,
                "sender": self_name,
                "recipient": recipient,
                "recipients": recipients,
                "primitive": "turn_steer",
                "max_age_turns": _peer_steer_manifest_max_age_turns(),
                "impact": impact,
                "suggested_fix": guidance + suggested_fix_suffix,
                "guidance": guidance,
            },
        )
        _fanout_visibility_event(event, mailbox=True)
        return event

    def _send_peer_message(
        *,
        to: str,
        body: str,
        summary: str,
        color: str | None,
        message_kind: str,
    ) -> _InboxMessage:
        return _cs_messaging.append_message(
            team,
            to,
            _InboxMessage(
                from_=self_name,
                text=body,
                timestamp=_cs_messaging.now_iso(),
                read=False,
                summary=summary,
                color=color,
                message_kind=message_kind,
            ),
        )

    def instrumented_tool(category: ToolCategory) -> Callable[[_F], _F]:
        def decorate(func: _F) -> _F:
            tool_name = func.__name__
            signature = inspect.signature(func)

            @functools.wraps(func)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                bound_args = _bind_tool_arguments(signature, args, kwargs)
                target = _tool_target(tool_name, bound_args)
                started_at = time.monotonic()
                _log_mcp_diag(
                    "call_tool_started",
                    {
                        "tool_name": tool_name,
                        "category": category,
                        "target": target,
                    },
                )
                _emit_tool_event(
                    tool_name=tool_name,
                    category=category,
                    phase="started",
                    target=target,
                    tool_args=bound_args,
                )
                try:
                    result = func(*args, **kwargs)
                except Exception as exc:
                    failed = _emit_tool_event(
                        tool_name=tool_name,
                        category=category,
                        phase="failed",
                        target=target,
                        tool_args=bound_args,
                        started_at=started_at,
                        exc=exc,
                    )
                    _log_mcp_diag(
                        "call_tool_failed",
                        {
                            "tool_name": tool_name,
                            "category": category,
                            "target": target,
                            "duration_ms": max(0, int((time.monotonic() - started_at) * 1000)),
                            "error_class": exc.__class__.__name__,
                            "error": _preview(str(exc), limit=600),
                        },
                    )
                    if not isinstance(exc, PeerSteerManifestCheckError):
                        _emit_visibility_degraded(
                            tool_name=tool_name,
                            category=category,
                            reason=_preview(str(exc), limit=600) or exc.__class__.__name__,
                            failed_event_id=failed.event_id,
                        )
                    raise

                if _result_indicates_failure(tool_name, result):
                    failed = _emit_tool_event(
                        tool_name=tool_name,
                        category=category,
                        phase="failed",
                        target=target,
                        tool_args=bound_args,
                        started_at=started_at,
                        result=result,
                    )
                    exit_code = _result_exit_code(result)
                    reason = (
                        f"{tool_name} exited with code {exit_code}"
                        if exit_code is not None
                        else f"{tool_name} returned an error result"
                    )
                    _log_mcp_diag(
                        "call_tool_failed_result",
                        {
                            "tool_name": tool_name,
                            "category": category,
                            "target": target,
                            "duration_ms": max(0, int((time.monotonic() - started_at) * 1000)),
                            "reason": reason,
                            "exit_code": exit_code,
                        },
                    )
                    _emit_visibility_degraded(
                        tool_name=tool_name,
                        category=category,
                        reason=reason,
                        failed_event_id=failed.event_id,
                    )
                    return result

                _emit_tool_event(
                    tool_name=tool_name,
                    category=category,
                    phase="completed",
                    target=target,
                    tool_args=bound_args,
                    started_at=started_at,
                    result=result,
                )
                _log_mcp_diag(
                    "call_tool_completed",
                    {
                        "tool_name": tool_name,
                        "category": category,
                        "target": target,
                        "duration_ms": max(0, int((time.monotonic() - started_at) * 1000)),
                        "result": _tool_result_payload(result),
                    },
                )
                return result

            setattr(wrapper, "__anyteam_instrumented_category__", category)
            return cast(_F, wrapper)

        return decorate

    @register_mcp_tool
    @instrumented_tool(category="team_tool")
    def mcp_anyteam_capability_manifest(
        agent_name: str,
        capability: str | None = None,
    ) -> dict:
        """Return a teammate's rich R12 Agent Card manifest from the local cache.

        Use `read_config()` for cheap roster discovery via members[].capabilities;
        use this R13 tool when you need the schema, description, when_to_use,
        when_not_to, and failure_modes before invoking a peer capability.

        Args:
            agent_name: target teammate name from this team's roster.
            capability: optional capability name. When omitted, returns the
                whole cached Agent Card. When set, returns just that rich
                per-capability entry.
        """
        if not agent_name:
            raise ToolError("agent_name must not be empty; use read_config() to discover teammate names")
        # S10b ablation per S10-ablation-implementation-spec.md §1: when the
        # manifest cache is disabled, the wrapper MCP returns an empty Agent
        # Card so peers see no capability data at all even if their wrapper
        # bypasses the cache layer directly.
        if os.environ.get("CLAUDE_ANYTEAM_DISABLE_MANIFEST_CACHE") == "1":
            _record_manifest_query(agent_name)
            return {}
        try:
            cfg = _cs_teams.read_config(team)
        except FileNotFoundError:
            raise ToolError(f"team {team!r} not found")
        member_names = {m.name for m in cfg.members}
        if agent_name not in member_names:
            raise ToolError(
                f"agent_name {agent_name!r} is not a member of team {team!r}; "
                "call read_config() to discover the roster"
            )

        # Long-lived wrapper processes refresh their in-memory cache from the
        # R12 inbox event stream just before serving the lookup. This remains a
        # cache hit for peer invocation: no per-call manifest file read unless
        # a capability_version bump event told us to reload this entry.
        manifest_cache.refresh_from_inbox()
        manifest = manifest_cache.get(agent_name)
        if manifest is None:
            raise ToolError(
                f"capability manifest for {agent_name!r} is not in the local cache; "
                "use read_config() to verify the roster and wait one inbox poll cycle "
                "for capability_manifest_updated broadcast refresh"
            )

        capabilities = manifest.get("capabilities")
        if capability is None:
            _record_manifest_query(agent_name)
            return manifest
        if not isinstance(capabilities, dict) or capability not in capabilities:
            available = sorted(capabilities) if isinstance(capabilities, dict) else []
            raise ToolError(
                f"capability {capability!r} is not cached for {agent_name!r}; "
                f"available capabilities: {available}"
            )
        entry = capabilities[capability]
        if not isinstance(entry, dict):
            raise ToolError(
                f"cached capability {capability!r} for {agent_name!r} is malformed"
            )
        _record_manifest_query(agent_name)
        return entry

    @register_mcp_tool
    @instrumented_tool(category="team_tool")
    def mcp_anyteam_list_skills() -> list[dict[str, Any]]:
        """List every Claude Code skill installed on this host that this teammate can follow.

        **Call this when**: the user asks for a deliverable that domain expertise
        could help with (marketing copy, SEO, cold email, design review, code
        review, observability diagnosis, etc.) AND you don't already see a
        ``## Available Claude Code skills`` section in your context describing a
        specific match. The host keeps a curated set of skills (often dozens) —
        marketing, growth, SEO, copywriting, sales enablement, product research,
        etc. — that encode opinionated playbooks. Listing them is cheap; the
        result is a small array of metadata, not skill bodies.

        Each entry is ``{name, description, when_to_use, source_path}``. To
        actually follow a skill, call ``mcp_anyteam_invoke_skill('<name>')``
        with the name from this list to fetch its full markdown body, then
        follow the instructions in the returned text. Skill bodies are NOT
        included here to keep the discovery call cheap.
        """

        return [
            {
                "name": record["name"],
                "description": record.get("description"),
                "when_to_use": record.get("when_to_use"),
                "source_path": record["source_path"],
            }
            for record in sorted(
                skill_cache.values(),
                key=lambda item: str(item.get("name", "")),
            )
        ]

    @register_mcp_tool
    @instrumented_tool(category="team_tool")
    def mcp_anyteam_invoke_skill(
        skill_name: str,
        request_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Fetch one Claude Code skill's full markdown body for you to follow.

        **Call this when**: ``mcp_anyteam_list_skills`` (or a ``## Available
        Claude Code skills`` fragment in your prompt) named a skill that fits
        the user's task. The skill body contains opinionated, vetted
        instructions written by humans — read it carefully and follow it,
        treating the prose as your operating instructions for this task.

        Returns ``{skill_name, body, source_path}`` on success, or
        ``{error: "skill_not_found", skill_name}`` if the name does not match a
        discovered skill. The wrapper returns the markdown verbatim and does
        not translate, flatten, or rewrite the body — your model interprets it
        natively. ``request_body`` is accepted for future templating
        experiments and is currently inert.
        """

        _ = request_body
        requested = skill_name.strip()
        record = skill_cache.get(requested)
        if record is None:
            return {
                "error": "skill_not_found",
                "skill_name": requested,
            }
        return {
            "skill_name": record["name"],
            "body": record["body"],
            "source_path": record["source_path"],
        }

    @register_mcp_tool
    @instrumented_tool(category="team_tool")
    def send_message(
        to: str,
        body: str,
        summary: str = "status update",
        kind: Literal[
            "informational",
            "steer",
            "handoff",
            "idle_notification",
            "task_complete",
            "task_blocked",
            "plan_blocked",
            "plan_approval_request",
            "plan_approval_response",
            "permission_request",
            "shutdown_approved",
            "shutdown_rejected",
        ] = "informational",
    ) -> dict:
        """Your plain text output is NOT visible to other agents — to communicate, you MUST call this tool. Refer to teammates by name, never UUID.

        progress updates, clarifying questions, handoffs, or typed lifecycle
        payloads. The sender is always you; do not try to impersonate another
        teammate. Set kind='steer' only when intentionally sending a mid-turn
        steer attempt; informational and handoff messages are ordinary peer-DMs,
        even if their body contains JSON or text that resembles a steer.
        Lifecycle kinds require body to be a JSON protocol payload whose
        kind/type matches the selected messageKind. Oversized bodies are
        stored as inbox artifacts automatically; the recipient sees a bounded
        preview plus an attachment reference.

        Args:
            to: recipient teammate name (e.g., 'team-lead' or a peer). Must
                be a member of this team; use '*' to broadcast to all others.
            body: message content. Plain prose or JSON-serialized protocol
                payload both work.
            summary: optional short label shown in notifications (5-10 words).
            kind: informational (default), steer, handoff, or a typed
                lifecycle kind. Wrapper-side manifest authorization applies
                only to explicit steer attempts.
        """
        if not to:
            raise ToolError("`to` must not be empty")
        if not body:
            raise ToolError("`body` must not be empty")
        if kind not in SEND_MESSAGE_KINDS:
            raise ToolError(f"`kind` must be one of {SEND_MESSAGE_KINDS}; got {kind!r}")
        if kind in LIFECYCLE_SEND_MESSAGE_KINDS:
            body = _normalise_lifecycle_send_body(kind, body)
            if kind == "task_blocked":
                drifted = _task_blocked_reason_drift(body)
                if drifted is not None:
                    # #40 Phase 1: surface drift in token-shaped
                    # task_blocked reasons without blocking delivery.
                    # The §2 contract is that the lead can see the
                    # mismatch — if a typo or missed registry update is
                    # leaking into typed lifecycle, the warn event is
                    # how they catch it.
                    try:
                        warn_event = _make_event(
                            kind="visibility_degraded",
                            severity="warn",
                            summary=(
                                f"task_blocked emitted with unregistered "
                                f"reason token: {drifted!r}"
                            ),
                            mailbox=True,
                            payload={
                                "surface": "task_blocked_unknown_reason",
                                "reason": drifted,
                                "registered_reasons": sorted(
                                    KNOWN_TASK_BLOCKED_REASONS
                                ),
                                "impact": (
                                    "Recipients filtering on task_blocked.reason "
                                    "may not match this value. Either add the "
                                    "token to KNOWN_TASK_BLOCKED_REASONS or "
                                    "use a free-form prose reason instead."
                                ),
                            },
                        )
                        _fanout_visibility_event(warn_event)
                    except Exception as e:
                        logger.warning(
                            "task_blocked_drift_emit_failed: %s", e
                        )
        if to == self_name:
            raise ToolError(f"refusing to send message to self ({self_name!r})")
        try:
            cfg = _cs_teams.read_config(team)
        except FileNotFoundError:
            raise ToolError(f"team {team!r} not found on disk")
        member_names = {m.name for m in cfg.members}
        if to != "*" and to not in member_names:
            raise ToolError(
                f"recipient {to!r} is not a member of team {team!r}; "
                f"members: {sorted(member_names)}"
            )
        steer_refusals = (
            _steer_recipient_refusal_reasons(to, cfg)
            if kind == "steer"
            else {}
        )
        if _enforce_peer_steer_manifest_check() and steer_refusals:
            recipient, reason = next(iter(steer_refusals.items()))
            recipients = [
                candidate
                for candidate, candidate_reason in steer_refusals.items()
                if candidate_reason == reason
            ]
            _emit_peer_steer_refused_at_wrapper(
                recipient=recipient,
                recipients=recipients,
                reason=reason,
            )
            if reason == "manifest_not_queried":
                raise PeerSteerManifestCheckError(
                    "peer steer refused at wrapper: manifest_not_queried; "
                    f"call mcp_anyteam_capability_manifest({recipient!r}, "
                    "'turn_steer') before attempting peer-steer"
                )
            raise PeerSteerManifestCheckError(
                "peer steer refused at wrapper: manifest_denies_peer_steer; "
                f"{recipient!r} does not authorize non-lead steer; send an "
                "informational message or route the steer through team-lead"
            )
        if to == "*":
            delivered = 0
            attachments: dict[str, dict[str, Any]] = {}
            for m in cfg.members:
                target = getattr(m, "name", None)
                if not target or target == self_name:
                    continue
                stored = _send_peer_message(
                    to=target,
                    body=body,
                    summary=summary,
                    color=None,
                    message_kind=kind,
                )
                if stored.attachment is not None:
                    attachments[target] = stored.attachment.model_dump(
                        by_alias=True,
                        exclude_none=True,
                    )
                delivered += 1
            result: dict[str, Any] = {
                "delivered_to": "*",
                "sender": self_name,
                "count": delivered,
            }
            if attachments:
                result["attachments"] = attachments
            return result
        # Stamp the sender's colour onto the wire payload. `send_plain_message`
        # stores this value directly on the inbox message, so using the
        # recipient's colour (the old behavior) misattributes who spoke.
        sender_color = None
        for m in cfg.members:
            if m.name == self_name and isinstance(m, _TeammateMember):
                sender_color = m.color
                break
        stored = _send_peer_message(
            to=to,
            body=body,
            summary=summary,
            color=sender_color,
            message_kind=kind,
        )
        result = {"delivered_to": to, "sender": self_name}
        if stored.attachment is not None:
            result["attachment"] = stored.attachment.model_dump(
                by_alias=True,
                exclude_none=True,
            )
        return result

    @register_mcp_tool
    @instrumented_tool(category="team_tool")
    def task_update(
        task_id: str,
        active_form: str | None = None,
        status: Literal["pending", "in_progress", "completed"] | None = None,
        owner: str | None = None,
        metadata: dict[str, Any] | None = None,
        parent_task_id: str | None = None,
    ) -> dict:
        """Update your own in-flight task. Use `active_form` to tell
        teammates what you're currently doing ('writing tests',
        'refactoring helper', etc.). Use `status` only to advance
        toward completion — do not set `deleted` here. `owner` and
        `metadata` are forwarded to the underlying TaskUpdate call for
        parity with native Claude agents.

        Args:
            task_id: id of the task to update. You must own it.
            active_form: short present-continuous description of current work.
            status: one of 'pending', 'in_progress', 'completed'.
            owner: optional owner override to forward to TaskUpdate.
            metadata: optional metadata patch to forward to TaskUpdate.
            parent_task_id: optional delegated parent task link.
        """
        if status is not None and status not in ("pending", "in_progress", "completed"):
            raise ToolError(f"invalid status {status!r}")
        try:
            existing = _cs_tasks.get_task(team, task_id)
        except FileNotFoundError:
            raise ToolError(f"task {task_id!r} not found in team {team!r}")
        if existing.owner not in (self_name, None, ""):
            raise ToolError(
                f"refusing to update task {task_id!r}: owned by {existing.owner!r}, not {self_name!r}"
            )
        try:
            result = _cs_tasks.update_task(
                team,
                task_id,
                status=status,
                owner=owner,
                active_form=active_form,
                metadata=metadata,
                parent_task_id=parent_task_id,
            )
        except ValueError as e:
            raise ToolError(str(e))
        response = {
            "id": result.id,
            "status": result.status,
            "active_form": result.active_form,
            "owner": result.owner,
            "metadata": result.metadata,
        }
        parent_task_id = getattr(result, "parent_task_id", None)
        if parent_task_id is not None:
            response["parentTaskId"] = parent_task_id
        return response

    @register_mcp_tool
    @instrumented_tool(category="team_tool")
    def checkpoint_commit(message: str) -> dict:
        """Commit incremental work in this teammate's git repository.

        Use this liberally during multi-file work, especially after each
        meaningful file edit, so progress survives an App Server turn timeout
        or process reap. The wrapper runs ``git add -A`` and ``git commit -m``
        inside the teammate's configured ``--cwd`` and returns the resulting
        commit SHA. If there are no changes, unresolved merge conflicts, or
        git refuses the commit, the tool fails without claiming success.

        Args:
            message: non-empty git commit message for the checkpoint.
        """

        commit_message = message.strip()
        if not commit_message:
            raise ToolError("message must not be empty")
        if not wrapper_cwd.exists():
            raise ToolError(f"configured cwd does not exist: {wrapper_cwd}")
        if not wrapper_cwd.is_dir():
            raise ToolError(f"configured cwd is not a directory: {wrapper_cwd}")

        top = _run_git(wrapper_cwd, ["rev-parse", "--show-toplevel"])
        if top.returncode != 0:
            raise _git_failure("git rev-parse --show-toplevel", top)
        repo_root = top.stdout.strip() or str(wrapper_cwd)

        unmerged = _run_git(wrapper_cwd, ["ls-files", "-u"])
        if unmerged.returncode != 0:
            raise _git_failure("git ls-files -u", unmerged)
        conflicted = _unmerged_paths(unmerged.stdout)
        if conflicted:
            preview = ", ".join(conflicted[:10])
            suffix = (
                "" if len(conflicted) <= 10 else f", +{len(conflicted) - 10} more"
            )
            raise ToolError(
                "refusing checkpoint_commit with unresolved merge conflicts: "
                f"{preview}{suffix}"
            )

        add = _run_git(wrapper_cwd, ["add", "-A"])
        if add.returncode != 0:
            raise _git_failure("git add -A", add)

        diff = _run_git(wrapper_cwd, ["diff", "--cached", "--quiet"])
        if diff.returncode == 0:
            raise ToolError("no changes to commit")
        if diff.returncode != 1:
            raise _git_failure("git diff --cached --quiet", diff)

        commit = _run_git(wrapper_cwd, ["commit", "-m", commit_message], timeout=60.0)
        if commit.returncode != 0:
            raise _git_failure("git commit", commit)

        rev = _run_git(wrapper_cwd, ["rev-parse", "HEAD"])
        if rev.returncode != 0:
            raise _git_failure("git rev-parse HEAD", rev)
        sha = rev.stdout.strip()
        return {
            "sha": sha,
            "commit": sha,
            "message": commit_message,
            "repo": repo_root,
            "cwd": str(wrapper_cwd),
        }

    @mcp.tool
    @instrumented_tool(category="team_tool")
    def task_create(
        subject: str,
        description: str,
        coupling: dict | str | None = None,
        parent_task_id: str | None = None,
    ) -> dict:
        """Create a new task in your team. Use when work you discovered
        during a task should be split off rather than bundled into the
        current one. The new task starts unowned and pending; the lead
        will assign it.

        Args:
            subject: one-line task title (imperative form).
            description: full task context and scope.
            coupling: optional per-task coordination override in canonical
                shape {intent: tight_peer_loop|loose_parallel|batched_async}.
            parent_task_id: optional delegated parent task link.
        """
        if not subject.strip():
            raise ToolError("subject must not be empty")
        if not description.strip():
            raise ToolError("description must not be empty")
        try:
            t = _cs_tasks.create_task(
                team,
                subject,
                description,
                coupling=coupling,
                parent_task_id=parent_task_id,
            )
        except ValueError as e:
            raise ToolError(str(e))
        response = {
            "id": t.id,
            "status": t.status,
            "subject": t.subject,
            "coupling": t.coupling,
        }
        if t.parent_task_id is not None:
            response["parentTaskId"] = t.parent_task_id
        return response

    def _batch_child_value(
        child: dict[str, Any],
        snake_key: str,
        camel_key: str,
    ) -> Any:
        return child[snake_key] if snake_key in child else child.get(camel_key)

    def _normalise_batch_summary_child(
        parent_task_id: str,
        child: dict[str, Any],
    ) -> BatchSummaryChild:
        if not isinstance(child, dict):
            raise ToolError("Each child task entry must be an object")

        raw_task_id = _batch_child_value(child, "task_id", "taskId")
        task_id = str(raw_task_id).strip() if raw_task_id is not None else ""
        if not task_id:
            raise ToolError("Each child task entry must include task_id")
        if task_id == parent_task_id:
            raise ToolError("Parent task cannot be included as a child task")

        try:
            task = _cs_tasks.get_task(team, task_id)
        except FileNotFoundError:
            raise ToolError(f"Child task {task_id!r} not found in team {team!r}")

        if task.parent_task_id is None:
            try:
                task = _cs_tasks.update_task(
                    team,
                    task_id,
                    parent_task_id=parent_task_id,
                )
            except ValueError as e:
                raise ToolError(str(e))
        elif task.parent_task_id != parent_task_id:
            raise ToolError(
                f"Child task {task_id!r} is already linked to parent task "
                f"{task.parent_task_id!r}"
            )

        raw_status = child.get("status") or task.status
        status = str(raw_status).strip()
        if not status:
            raise ToolError(f"Child task {task_id!r} must include a non-empty status")

        payload: dict[str, Any] = {"taskId": task_id, "status": status}
        for snake_key, camel_key in (
            ("session_id", "sessionId"),
            ("stop_reason", "stopReason"),
            ("summary", "summary"),
        ):
            value = _batch_child_value(child, snake_key, camel_key)
            if value is not None:
                payload[camel_key] = str(value)
        return BatchSummaryChild.model_validate(payload)

    @register_mcp_tool
    @instrumented_tool(category="team_tool")
    def task_batch_summary(
        parent_task_id: str,
        child_tasks: list[dict[str, Any]],
        summary: str,
    ) -> dict:
        """Emit a structured batch summary for delegated sub-tasks.

        Use this after completing or collecting multiple delegated child
        tasks. Each child entry must include task_id/taskId and may include
        status, session_id/sessionId, stop_reason/stopReason, and summary.
        Missing child parentTaskId links are wired to parent_task_id before
        the batch_summary visibility event is sent to the lead.
        """
        parent_task_id = parent_task_id.strip()
        if not parent_task_id:
            raise ToolError("parent_task_id must not be empty")
        summary_text = summary.strip()
        if not summary_text:
            raise ToolError("summary must not be empty")
        if not child_tasks:
            raise ToolError("child_tasks must not be empty")
        try:
            _cs_tasks.get_task(team, parent_task_id)
        except FileNotFoundError:
            raise ToolError(f"Parent task {parent_task_id!r} not found in team {team!r}")

        children: list[BatchSummaryChild] = []
        seen_child_ids: set[str] = set()
        for child in child_tasks:
            child_payload = _normalise_batch_summary_child(parent_task_id, child)
            if child_payload.task_id in seen_child_ids:
                raise ToolError(f"Duplicate child task {child_payload.task_id!r}")
            seen_child_ids.add(child_payload.task_id)
            children.append(child_payload)

        payload = BatchSummaryPayload(
            parent_task_id=parent_task_id,
            child_task_ids=[child.task_id for child in children],
            child_tasks=children,
            summary=summary_text,
        )
        event = _make_event(
            kind="batch_summary",
            severity="info",
            summary=summary_text,
            task_id=parent_task_id,
            mailbox=True,
            payload=payload.model_dump(by_alias=True, exclude_none=True),
        )
        _fanout_visibility_event(event, mailbox=True)
        return event.model_dump(by_alias=True, exclude_none=True)

    @register_mcp_tool
    @instrumented_tool(category="team_tool")
    def read_inbox(unread_only: bool = True) -> list[dict]:
        """Read your own inbox. Useful if you want to see whether a
        teammate replied to a clarifying question you sent.

        By default returns unread only and marks them read on the way
        out. Pass `unread_only=False` to see everything in chronological
        order (does not re-mark anything).

        Other teammates' inboxes are not accessible from this tool.
        Oversized messages include an `attachment` artifact reference; use
        mcp_anyteam_read_file on attachment.path only when the preview is
        insufficient.
        """
        msgs = _cs_messaging.read_inbox(
            team,
            self_name,
            unread_only=unread_only,
            mark_as_read=unread_only,
        )
        return [m.model_dump(by_alias=True, exclude_none=True) for m in msgs]

    @register_mcp_tool
    @instrumented_tool(category="team_tool")
    def task_list() -> list[dict]:
        """List all tasks in your team with current status and owners."""
        try:
            result = _cs_tasks.list_tasks(team)
        except ValueError as e:
            raise ToolError(str(e))
        return [t.model_dump(by_alias=True, exclude_none=True) for t in result]

    @register_mcp_tool
    @instrumented_tool(category="shadow_tool")
    def mcp_anyteam_shell(
        command: str,
        cwd: str | None = None,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
    ) -> dict:
        """Run a shell command for the teammate with unrestricted filesystem
        and network access.

        Args:
            command: shell command to execute.
            cwd: optional working directory for the command.
            timeout: optional timeout in seconds.
            env: optional environment variables to add/override.
        """
        completed = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            timeout=timeout,
            env={**os.environ, **env} if env is not None else None,
            capture_output=True,
            text=True,
        )
        return {
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "exit_code": completed.returncode,
        }


    @register_mcp_tool
    @instrumented_tool(category="shadow_tool")
    def mcp_anyteam_read_file(path: str, offset: int = 0, limit: int | None = None) -> dict:
        """Read a local file as text with safe decoding fallback.

        Args:
            path: filesystem path to read. No workspace restriction is applied.
            offset: zero-based line offset to start reading from.
            limit: optional maximum number of lines to return.
        """
        if offset < 0:
            raise ToolError("offset must be >= 0")
        if limit is not None and limit < 0:
            raise ToolError("limit must be >= 0")
        file_path = Path(path)
        try:
            raw = file_path.read_bytes()
        except OSError as e:
            raise ToolError(str(e))
        text, encoding = _decode_bytes(raw)
        lines = text.splitlines(keepends=True)
        selected = lines[offset : None if limit is None else offset + limit]
        return {
            "path": str(file_path),
            "content": "".join(selected),
            "encoding": encoding,
            "bytes": len(raw),
            "line_count": len(lines),
            "offset": offset,
            "limit": limit,
            "truncated": limit is not None and offset + limit < len(lines),
        }

    @register_mcp_tool
    @instrumented_tool(category="shadow_tool")
    def mcp_anyteam_write_file(
        path: str,
        content: str,
        mode: Literal["overwrite", "append"] = "overwrite",
    ) -> dict:
        """Write text to a local file with no filesystem sandbox.

        Args:
            path: filesystem path to write.
            content: text content to write.
            mode: overwrite the file or append to it.
        """
        if mode not in ("overwrite", "append"):
            raise ToolError("mode must be 'overwrite' or 'append'")
        file_path = Path(path)
        existed = file_path.exists()
        try:
            if mode == "append":
                with file_path.open("a", encoding="utf-8") as f:
                    written = f.write(content)
            else:
                with file_path.open("w", encoding="utf-8") as f:
                    written = f.write(content)
        except OSError as e:
            raise ToolError(str(e))
        return {
            "path": str(file_path),
            "mode": mode,
            "existed": existed,
            "chars_written": written,
            "bytes_written": len(content.encode("utf-8")),
        }

    @register_mcp_tool
    @instrumented_tool(category="shadow_tool")
    def mcp_anyteam_list_directory(path: str, recursive: bool = False, glob: str | None = None) -> dict:
        """List directory entries with optional recursion and glob filtering.

        Args:
            path: directory path to list.
            recursive: when true, walk the whole subtree.
            glob: optional glob pattern matched against relative paths and names.
        """
        root = Path(path)
        if not root.exists():
            raise ToolError(f"path does not exist: {path}")
        if not root.is_dir():
            raise ToolError(f"path is not a directory: {path}")
        try:
            candidates = root.rglob("*") if recursive else root.iterdir()
            entries = []
            for child in candidates:
                rel = str(child.relative_to(root))
                if glob and not (fnmatch.fnmatch(rel, glob) or fnmatch.fnmatch(child.name, glob)):
                    continue
                entries.append(_entry_for(child, base=root))
        except OSError as e:
            raise ToolError(str(e))
        entries.sort(key=lambda item: item.get("path", ""))
        return {"path": str(root), "recursive": recursive, "glob": glob, "entries": entries}

    @register_mcp_tool
    @instrumented_tool(category="shadow_tool")
    def mcp_anyteam_edit_file(path: str, old: str, new: str, replace_all: bool = False) -> dict:
        """Replace an exact string in a text file and return the replacement count.

        Args:
            path: filesystem path to edit.
            old: exact text to replace.
            new: replacement text.
            replace_all: replace every occurrence instead of requiring exactly one.
        """
        if old == "":
            raise ToolError("old must not be empty")
        file_path = Path(path)
        try:
            raw = file_path.read_bytes()
            text, encoding = _decode_bytes(raw)
        except OSError as e:
            raise ToolError(str(e))
        count = text.count(old)
        if not replace_all and count != 1:
            raise ToolError(f"expected exactly one occurrence of old text, found {count}")
        updated = text.replace(old, new) if replace_all else text.replace(old, new, 1)
        try:
            file_path.write_text(updated, encoding="utf-8")
        except OSError as e:
            raise ToolError(str(e))
        return {"path": str(file_path), "replacements": count if replace_all else 1, "encoding_read": encoding}

    @register_mcp_tool
    @instrumented_tool(category="shadow_tool")
    def mcp_anyteam_search(
        pattern: str,
        path: str = ".",
        regex: bool = False,
        glob: str | None = None,
    ) -> dict:
        """Search files under a path for text or regex matches.

        Args:
            pattern: literal text or regex to search for.
            path: file or directory path to search.
            regex: interpret pattern as a regular expression when true.
            glob: optional file glob matched against relative paths and names.
        """
        root = Path(path)
        if not root.exists():
            raise ToolError(f"path does not exist: {path}")
        try:
            rx = re.compile(pattern) if regex else None
        except re.error as e:
            raise ToolError(f"invalid regex: {e}")
        files = [root] if root.is_file() else [p for p in root.rglob("*") if p.is_file()]
        matches: list[dict[str, Any]] = []
        for file_path in files:
            rel = str(file_path.relative_to(root)) if root.is_dir() else file_path.name
            if glob and not (fnmatch.fnmatch(rel, glob) or fnmatch.fnmatch(file_path.name, glob)):
                continue
            try:
                text, encoding = _decode_bytes(file_path.read_bytes())
            except OSError:
                continue
            for line_no, line in enumerate(text.splitlines(), start=1):
                if (rx.search(line) if rx is not None else pattern in line):
                    matches.append({
                        "path": str(file_path),
                        "line": line_no,
                        "text": line,
                        "encoding": encoding,
                    })
        return {"pattern": pattern, "path": str(root), "regex": regex, "glob": glob, "matches": matches}

    @register_mcp_tool
    @instrumented_tool(category="shadow_tool")
    def mcp_anyteam_grep(regex: str, directory: str) -> dict:
        """Recursively grep a directory with a regular expression.

        Args:
            regex: Python regular expression to match against each text line.
            directory: directory path to search recursively.
        """
        root = Path(directory)
        if not root.exists():
            raise ToolError(f"directory does not exist: {directory}")
        if not root.is_dir():
            raise ToolError(f"path is not a directory: {directory}")
        try:
            rx = re.compile(regex)
        except re.error as e:
            raise ToolError(f"invalid regex: {e}")

        matches: list[dict[str, Any]] = []
        try:
            files = sorted((p for p in root.rglob("*") if p.is_file()), key=lambda p: str(p))
        except OSError as e:
            raise ToolError(str(e))
        for file_path in files:
            try:
                text, encoding = _decode_bytes(file_path.read_bytes())
            except OSError:
                continue
            for line_no, line in enumerate(text.splitlines(), start=1):
                if rx.search(line):
                    matches.append(
                        {
                            "path": str(file_path),
                            "line": line_no,
                            "text": line,
                            "encoding": encoding,
                        }
                    )
        return {"regex": regex, "directory": str(root), "matches": matches}

    @register_mcp_tool
    @instrumented_tool(category="shadow_tool")
    def mcp_anyteam_web_fetch(
        url: str,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        body: str | None = None,
    ) -> dict:
        """Fetch a URL with unrestricted network access and return response data.

        Args:
            url: http(s) URL to fetch. No allowlist is applied.
            method: HTTP method to use.
            headers: optional request headers.
            body: optional request body text encoded as UTF-8.
        """
        data = body.encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read()
                text, encoding = _decode_bytes(raw)
                return {
                    "url": response.geturl(),
                    "status": response.status,
                    "headers": dict(response.headers.items()),
                    "body": text,
                    "encoding": encoding,
                    "bytes": len(raw),
                }
        except urllib.error.HTTPError as e:
            raw = e.read()
            text, encoding = _decode_bytes(raw)
            return {
                "url": url,
                "status": e.code,
                "headers": dict(e.headers.items()) if e.headers else {},
                "body": text,
                "encoding": encoding,
                "bytes": len(raw),
            }
        except (urllib.error.URLError, OSError) as e:
            raise ToolError(str(e))

    @register_mcp_tool
    @instrumented_tool(category="team_tool")
    def read_config() -> dict:
        """Read the team config — useful to discover teammate names and
        roles before sending messages. Member `prompt` fields are
        omitted since they're irrelevant to a peer.

        The returned top-level ``protocol_tools`` section lists the exact
        wrapper MCP tool names visible to this caller (including backend
        prefixing), so a model can recover from tool-discovery uncertainty
        without guessing.
        """
        try:
            cfg = _cs_teams.read_config(team)
        except FileNotFoundError:
            raise ToolError(f"team {team!r} not found")
        data = cfg.model_dump(by_alias=True)
        for m in data.get("members", []):
            m.pop("prompt", None)
        self_member = next(
            (member for member in cfg.members if getattr(member, "name", None) == self_name),
            None,
        )
        data["protocol_tools"] = _protocol_tools_section(
            self_name=self_name,
            self_member=self_member,
            self_manifest=manifest_cache.get(self_name),
        )
        return data

    _log_mcp_diag("server_registered_snapshot", _tool_snapshot_payload(_registered_tool_names(mcp)))

    return mcp


def main() -> None:
    """Entry point for `claude-anyteam-wrapper` stdio MCP server."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    mcp = build_server()
    mcp.run()


if __name__ == "__main__":
    main()
