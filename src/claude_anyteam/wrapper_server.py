"""v7 narrowed MCP server exposing a safe tool subset to the Codex subprocess.

**Why a narrowed MCP surface.** The full team-control surface includes
destructive lifecycle operations (`team_delete`, `force_kill_teammate`,
`spawn_teammate`, `team_create`, `process_shutdown_approved`,
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

from .capability_manifest import CapabilityManifestCache
from .env import LEGACY_NAME_ENV, LEGACY_TEAM_ENV, NAME_ENV, TEAM_ENV, env_first
from .messages import VisibilityEvent
from . import protocol_io
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
SEND_MESSAGE_KINDS = ("informational", "steer", "handoff")


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
    "task_create",
    "read_inbox",
    "task_list",
    "read_config",
    "mcp_anyteam_capability_manifest",
    "mcp_anyteam_shell",
    "mcp_anyteam_read_file",
    "mcp_anyteam_write_file",
    "mcp_anyteam_list_directory",
    "mcp_anyteam_edit_file",
    "mcp_anyteam_search",
    "mcp_anyteam_web_fetch",
)

# Full team-control tools that we deliberately do NOT surface. Checked by a
# test so removals are deliberate. If the protocol gains a new tool, the test
# fails and forces a decision about whether it belongs in EXPOSED_TOOLS or
# BLOCKED_TOOLS.
BLOCKED_TOOLS: tuple[str, ...] = (
    "team_create",
    "team_delete",
    "spawn_teammate",
    "force_kill_teammate",
    "process_shutdown_approved",
    "check_teammate",
)

ToolCategory = Literal["team_tool", "shadow_tool"]

TEAM_TOOLS: frozenset[str] = frozenset(
    {
        "send_message",
        "task_update",
        "task_create",
        "read_inbox",
        "task_list",
        "read_config",
        "mcp_anyteam_capability_manifest",
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



def _decode_bytes(data: bytes) -> tuple[str, str]:
    """Decode arbitrary file/HTTP bytes without raising on bad text."""
    for encoding in ("utf-8", "utf-16"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace"), "utf-8-replacement"


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


def _tool_target(tool_name: str, bound_args: dict[str, Any]) -> str | None:
    """Return a concise, non-secret-ish target label for visibility events."""

    if tool_name == "send_message":
        return f"to={bound_args.get('to')!r}"
    if tool_name == "task_update":
        return f"task_id={bound_args.get('task_id')!r}"
    if tool_name == "task_create":
        return _preview(bound_args.get("subject"), limit=160)
    if tool_name == "mcp_anyteam_capability_manifest":
        target = f"agent_name={bound_args.get('agent_name')!r}"
        if bound_args.get("capability") is not None:
            target += f" capability={bound_args.get('capability')!r}"
        return target
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
    if tool_name == "mcp_anyteam_web_fetch":
        return str(bound_args.get("url"))
    return None


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
    return tool_name == "mcp_anyteam_shell" and exit_code not in (None, 0)


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

    mcp = FastMCP(
        name="claude-anyteam-wrapper",
        instructions=(
            "Narrowed MCP surface for a Codex teammate. Team: "
            f"{team!r}; identity: {self_name!r}. Call these tools when it "
            "would be useful to your teammates — peer or lead updates via "
            "send_message, activeForm/owner/metadata changes via task_update, "
            "subtask creation via task_create, inspection via read_inbox / "
            "task_list / read_config. Destructive lifecycle operations "
            "(shutdown, spawn, kill) are not available here by design; the "
            "Python adapter owns those."
        ),
    )

    manifest_cache = CapabilityManifestCache(
        team,
        self_name=self_name,
        root=_cs_teams.TEAMS_DIR,
    )
    manifest_cache.load_startup()

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

    def _manifest_accepts_peer_steer(manifest: Any) -> bool:
        if not isinstance(manifest, dict):
            return False
        accepts = manifest.get("accepts_peer_steer")
        if isinstance(accepts, bool):
            return accepts
        capabilities = manifest.get("capabilities")
        if isinstance(capabilities, dict):
            value = capabilities.get("accepts_peer_steer")
            if isinstance(value, bool):
                return value
            return value is not None
        if isinstance(capabilities, list):
            return "accepts_peer_steer" in capabilities
        return False

    def _steer_recipients_requiring_manifest_query(
        to: str,
        cfg: Any,
    ) -> list[str]:
        """Return recipients needing a fresh manifest query before steer send.

        §3 L3 v2 gates on the recipient's interpretation criteria, not the
        sender's body shape. Post-57, however, this precondition applies only
        when the sender explicitly declares ``kind="steer"``; ordinary
        informational/handoff peer-DMs flow through without relocation gating.
        """
        if self_name == "team-lead" or to == "team-lead":
            return []
        if to == "*":
            recipients: list[str] = []
            for member in getattr(cfg, "members", []):
                target = getattr(member, "name", None)
                if target and target not in {self_name, "team-lead"}:
                    recipients.append(target)
        else:
            recipients = [to]

        rejecting: list[str] = []
        for recipient in recipients:
            manifest = manifest_cache.get(recipient)
            if manifest is None:
                # No declaration means we conservatively assume the recipient
                # rejects peer steer and require an explicit manifest lookup.
                rejecting.append(recipient)
                continue
            if not _manifest_accepts_peer_steer(manifest):
                rejecting.append(recipient)
        return rejecting

    def _emit_peer_steer_refused_at_wrapper(
        *,
        recipient: str,
        recipients: list[str],
    ) -> VisibilityEvent:
        guidance = (
            f"Call mcp_anyteam_capability_manifest({recipient!r}, "
            "'turn_steer') before attempting peer-steer."
        )
        event = _make_event(
            kind="visibility_degraded",
            severity="warn",
            summary=f"peer steer to {recipient} refused at wrapper; manifest not queried",
            mailbox=True,
            payload={
                "surface": "peer_steer_refused_at_wrapper",
                "reason": "manifest_not_queried",
                "sender": self_name,
                "recipient": recipient,
                "recipients": recipients,
                "primitive": "turn_steer",
                "max_age_turns": _peer_steer_manifest_max_age_turns(),
                "impact": (
                    "The peer steer was not delivered; the recipient backend "
                    "did not spend a turn rejecting it."
                ),
                "suggested_fix": (
                    guidance
                    + " Then retry only if the manifest permits peer steering."
                ),
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
    ) -> None:
        _cs_messaging.append_message(
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
                _emit_tool_event(
                    tool_name=tool_name,
                    category=category,
                    phase="started",
                    target=target,
                )
                try:
                    result = func(*args, **kwargs)
                except Exception as exc:
                    failed = _emit_tool_event(
                        tool_name=tool_name,
                        category=category,
                        phase="failed",
                        target=target,
                        started_at=started_at,
                        exc=exc,
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
                        started_at=started_at,
                        result=result,
                    )
                    exit_code = _result_exit_code(result)
                    reason = (
                        f"{tool_name} exited with code {exit_code}"
                        if exit_code is not None
                        else f"{tool_name} returned an error result"
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
                    started_at=started_at,
                    result=result,
                )
                return result

            setattr(wrapper, "__anyteam_instrumented_category__", category)
            return cast(_F, wrapper)

        return decorate

    @mcp.tool
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

    @mcp.tool
    @instrumented_tool(category="team_tool")
    def send_message(
        to: str,
        body: str,
        summary: str = "status update",
        kind: Literal["informational", "steer", "handoff"] = "informational",
    ) -> dict:
        """Send a message to another teammate (team-lead or any peer). Use
        for progress updates, clarifying questions, or handoffs. The sender
        is always you; do not try to impersonate another teammate. Set
        kind='steer' only when intentionally sending a mid-turn steer attempt;
        informational and handoff messages are ordinary peer-DMs.

        Args:
            to: recipient teammate name (e.g., 'team-lead' or a peer). Must
                be a member of this team; use '*' to broadcast to all others.
            body: message content. Plain prose or JSON-serialized protocol
                payload both work.
            summary: optional short label shown in notifications (5-10 words).
            kind: informational (default), steer, or handoff. Wrapper-side
                manifest gating applies only to explicit steer attempts.
        """
        if not to:
            raise ToolError("`to` must not be empty")
        if not body:
            raise ToolError("`body` must not be empty")
        if kind not in SEND_MESSAGE_KINDS:
            raise ToolError(f"`kind` must be one of {SEND_MESSAGE_KINDS}; got {kind!r}")
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
        steer_recipients = (
            _steer_recipients_requiring_manifest_query(to, cfg)
            if kind == "steer"
            else []
        )
        if _enforce_peer_steer_manifest_check() and steer_recipients:
            missing = [
                recipient
                for recipient in steer_recipients
                if not _recent_manifest_query(recipient)
            ]
            if missing:
                recipient = missing[0]
                _emit_peer_steer_refused_at_wrapper(
                    recipient=recipient,
                    recipients=missing,
                )
                raise PeerSteerManifestCheckError(
                    "peer steer refused at wrapper: manifest_not_queried; "
                    f"call mcp_anyteam_capability_manifest({recipient!r}, "
                    "'turn_steer') before attempting peer-steer"
                )
        if to == "*":
            delivered = 0
            for m in cfg.members:
                target = getattr(m, "name", None)
                if not target or target == self_name:
                    continue
                _send_peer_message(
                    to=target,
                    body=body,
                    summary=summary,
                    color=None,
                    message_kind=kind,
                )
                delivered += 1
            return {"delivered_to": "*", "sender": self_name, "count": delivered}
        # Stamp the sender's colour onto the wire payload. `send_plain_message`
        # stores this value directly on the inbox message, so using the
        # recipient's colour (the old behavior) misattributes who spoke.
        sender_color = None
        for m in cfg.members:
            if m.name == self_name and isinstance(m, _TeammateMember):
                sender_color = m.color
                break
        _send_peer_message(
            to=to,
            body=body,
            summary=summary,
            color=sender_color,
            message_kind=kind,
        )
        return {"delivered_to": to, "sender": self_name}

    @mcp.tool
    @instrumented_tool(category="team_tool")
    def task_update(
        task_id: str,
        active_form: str | None = None,
        status: Literal["pending", "in_progress", "completed"] | None = None,
        owner: str | None = None,
        metadata: dict[str, Any] | None = None,
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
            )
        except ValueError as e:
            raise ToolError(str(e))
        return {
            "id": result.id,
            "status": result.status,
            "active_form": result.active_form,
            "owner": result.owner,
            "metadata": result.metadata,
        }

    @mcp.tool
    @instrumented_tool(category="team_tool")
    def task_create(subject: str, description: str, coupling: dict | str | None = None) -> dict:
        """Create a new task in your team. Use when work you discovered
        during a task should be split off rather than bundled into the
        current one. The new task starts unowned and pending; the lead
        will assign it.

        Args:
            subject: one-line task title (imperative form).
            description: full task context and scope.
            coupling: optional per-task coordination override in canonical
                shape {intent: tight_peer_loop|loose_parallel|batched_async}.
        """
        if not subject.strip():
            raise ToolError("subject must not be empty")
        if not description.strip():
            raise ToolError("description must not be empty")
        try:
            t = _cs_tasks.create_task(team, subject, description, coupling=coupling)
        except ValueError as e:
            raise ToolError(str(e))
        return {"id": t.id, "status": t.status, "subject": t.subject, "coupling": t.coupling}

    @mcp.tool
    @instrumented_tool(category="team_tool")
    def read_inbox(unread_only: bool = True) -> list[dict]:
        """Read your own inbox. Useful if you want to see whether a
        teammate replied to a clarifying question you sent.

        By default returns unread only and marks them read on the way
        out. Pass `unread_only=False` to see everything in chronological
        order (does not re-mark anything).

        Other teammates' inboxes are not accessible from this tool.
        """
        msgs = _cs_messaging.read_inbox(
            team,
            self_name,
            unread_only=unread_only,
            mark_as_read=unread_only,
        )
        return [m.model_dump(by_alias=True, exclude_none=True) for m in msgs]

    @mcp.tool
    @instrumented_tool(category="team_tool")
    def task_list() -> list[dict]:
        """List all tasks in your team with current status and owners."""
        try:
            result = _cs_tasks.list_tasks(team)
        except ValueError as e:
            raise ToolError(str(e))
        return [t.model_dump(by_alias=True, exclude_none=True) for t in result]

    @mcp.tool
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


    @mcp.tool
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

    @mcp.tool
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

    @mcp.tool
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

    @mcp.tool
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

    @mcp.tool
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

    @mcp.tool
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

    @mcp.tool
    @instrumented_tool(category="team_tool")
    def read_config() -> dict:
        """Read the team config — useful to discover teammate names and
        roles before sending messages. Member `prompt` fields are
        omitted since they're irrelevant to a peer."""
        try:
            cfg = _cs_teams.read_config(team)
        except FileNotFoundError:
            raise ToolError(f"team {team!r} not found")
        data = cfg.model_dump(by_alias=True)
        for m in data.get("members", []):
            m.pop("prompt", None)
        return data

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
