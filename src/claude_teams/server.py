import json
import logging
import os
import uuid
from types import SimpleNamespace
from typing import Any, Literal

from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.lifespan import lifespan
from fastmcp.server.middleware import Middleware

from claude_teams import messaging, tasks, teams
from claude_teams.models import (
    COLOR_PALETTE,
    InboxMessage,
    SendMessageResult,
    ShutdownApproved,
    SpawnResult,
    TeammateMember,
)
from claude_teams.spawner import (
    discover_harness_binary,
    kill_tmux_pane,
    spawn_teammate,
    use_tmux_windows,
)
from claude_teams.tmux_introspection import peek_pane, resolve_pane_target

logger = logging.getLogger(__name__)

KNOWN_CLIENTS: dict[str, str] = {
    "claude-code": "claude",
    "claude": "claude",
}

# NOTE(victor): Mutated by both app_lifespan and HarnessDetectionMiddleware.
# Safe under stdio (single session). Racy under SSE/streamable HTTP.
#
# more context:
#   app_lifespan yields _lifespan_state
#     -> _lifespan_manager stores as self._lifespan_result (same ref)
#     -> _lifespan_proxy yields self._lifespan_result
#     -> ctx.lifespan_context in tool handlers returns it
#   All references point to the same dict. Middleware mutations propagate.
_lifespan_state: dict[str, Any] = {}
_spawn_tool: Any = None
_check_teammate_tool: Any = None
_read_inbox_tool: Any = None


_VALID_BACKENDS = frozenset(KNOWN_CLIENTS.values())


def _parse_backends_env(raw: str) -> list[str]:
    if not raw:
        return []
    return list(
        dict.fromkeys(
            b.strip()
            for b in raw.split(",")
            if b.strip() and b.strip() in _VALID_BACKENDS
        )
    )


_SPAWN_TOOL_BASE_DESCRIPTION = (
    "Spawn a new teammate in a tmux {target}. The teammate receives its initial "
    "prompt via inbox and begins working autonomously. Names must be unique "
    "within the team. cwd must be an absolute path to the teammate's working directory."
)


def _build_spawn_description(
    claude_binary: str | None,
    enabled_backends: list[str] | None = None,
) -> str:
    tmux_target = "window" if use_tmux_windows() else "pane"
    parts = [_SPAWN_TOOL_BASE_DESCRIPTION.format(target=tmux_target)]
    backends = []
    show_claude = claude_binary is not None
    if enabled_backends is not None:
        show_claude = show_claude and "claude" in enabled_backends
    if show_claude:
        backends.append("'claude' (default, models: sonnet, opus, haiku)")
    if backends:
        parts.append(f"Available backends: {'; '.join(backends)}.")
    return " ".join(parts)


_CHECK_TEAMMATE_BASE_DESCRIPTION = (
    "Check a single teammate's status: alive/dead, unread messages from them, "
    "their unread count, and optionally terminal output. Always non-blocking. "
    "Use parallel calls to check multiple teammates."
)


def _build_check_teammate_description(push_available: bool) -> str:
    if push_available:
        return (
            _CHECK_TEAMMATE_BASE_DESCRIPTION
            + " Push notifications are available in this session."
            " Use notify_after_minutes to schedule a deferred reminder."
        )
    return (
        _CHECK_TEAMMATE_BASE_DESCRIPTION
        + " Push notifications are NOT available in this session"
        " (not supported by the current harness)."
        " Do NOT pass notify_after_minutes."
    )


_READ_INBOX_BASE_DESCRIPTION = (
    "Read messages from an agent's inbox. Returns unread messages by default "
    "and marks them as read."
)


def _build_read_inbox_description(is_lead_session: bool) -> str:
    if is_lead_session:
        return (
            _READ_INBOX_BASE_DESCRIPTION
            + " NOTE: As team-lead, prefer check_teammate to read messages"
            " from a specific teammate. check_teammate filters by sender"
            " and provides richer status."
        )
    return _READ_INBOX_BASE_DESCRIPTION


def _update_spawn_tool(tool, enabled: list[str], state: dict[str, Any]) -> None:
    tool.parameters["properties"]["backend_type"]["enum"] = list(enabled)
    if enabled:
        tool.parameters["properties"]["backend_type"]["default"] = enabled[0]
    tool.description = _build_spawn_description(
        state.get("claude_binary"),
        enabled_backends=enabled,
    )


@lifespan
async def app_lifespan(server):
    global _spawn_tool, _check_teammate_tool, _read_inbox_tool

    claude_binary = discover_harness_binary("claude")
    if not claude_binary:
        raise FileNotFoundError(
            "No coding agent binary found on PATH. "
            "Install Claude Code ('claude')."
        )

    enabled_backends = _parse_backends_env(os.environ.get("CLAUDE_TEAMS_BACKENDS", ""))

    tool = await mcp.get_tool("spawn_teammate")
    _spawn_tool = tool

    if enabled_backends:
        _update_spawn_tool(
            tool,
            enabled_backends,
            {
                "claude_binary": claude_binary,
            },
        )
    else:
        tool.description = _build_spawn_description(
            claude_binary,
        )

    check_tool = await mcp.get_tool("check_teammate")
    _check_teammate_tool = check_tool
    # Push is never available at lifespan time (lead session discovered in middleware)
    check_tool.description = _build_check_teammate_description(push_available=False)

    ri_tool = await mcp.get_tool("read_inbox")
    _read_inbox_tool = ri_tool
    ri_tool.description = _build_read_inbox_description(is_lead_session=False)

    session_id = str(uuid.uuid4())
    _lifespan_state.clear()
    _lifespan_state.update(
        {
            "claude_binary": claude_binary,
            "enabled_backends": enabled_backends,
            "session_id": session_id,
            "active_team": None,
            "client_name": "unknown",
            "client_version": "unknown",
        }
    )
    yield _lifespan_state


class HarnessDetectionMiddleware(Middleware):
    # NOTE(victor): ctx.lifespan_context returns {} during on_initialize because
    # RequestContext isn't established yet. Client info is accessible from tool
    # handlers via ctx.session.client_params.clientInfo (stored by the MCP SDK).

    async def on_initialize(self, context, call_next):
        _unknown = SimpleNamespace(name="unknown", version="unknown")
        client_info = context.message.params.clientInfo or _unknown
        client_name = client_info.name
        client_version = client_info.version

        result = await call_next(context)

        logger.info("MCP client connected: %s v%s", client_name, client_version)

        native_backend = KNOWN_CLIENTS.get(client_name)
        enabled = _lifespan_state.get("enabled_backends", [])

        if native_backend and native_backend not in enabled:
            enabled.append(native_backend)

        if not enabled:
            if _lifespan_state.get("claude_binary"):
                enabled.append("claude")

        _lifespan_state["enabled_backends"] = enabled
        _lifespan_state["client_name"] = client_name
        _lifespan_state["client_version"] = client_version

        push_available = False
        if _check_teammate_tool:
            _check_teammate_tool.description = _build_check_teammate_description(
                push_available
            )

        is_lead = push_available
        if _read_inbox_tool:
            _read_inbox_tool.description = _build_read_inbox_description(is_lead)

        if _spawn_tool:
            _update_spawn_tool(_spawn_tool, enabled, _lifespan_state)

        return result


mcp = FastMCP(
    name="claude-teams",
    instructions=(
        "MCP server for orchestrating Claude Code agent teams. "
        "Manages team creation, teammate spawning, messaging, and task tracking."
    ),
    lifespan=app_lifespan,
)
mcp.add_middleware(HarnessDetectionMiddleware())


def _get_lifespan(ctx: Context) -> dict[str, Any]:
    return ctx.lifespan_context


def _content_metadata(content: str, sender: str) -> str:
    """Append sender signature and reply reminder to outgoing message content."""
    return (
        f"{content}\n\n"
        f"<system_reminder>"
        f"This message was sent from {sender}. "
        f"Use your send_message tool to respond."
        f"</system_reminder>"
    )


@mcp.tool
def team_create(
    team_name: str,
    ctx: Context,
    description: str = "",
) -> dict:
    """Create a new agent team. Sets up team config and task directories under ~/.claude/.
    One team per server session. Team names must be filesystem-safe
    (letters, numbers, hyphens, underscores)."""
    ls = _get_lifespan(ctx)
    if ls.get("active_team"):
        raise ToolError(
            f"Session already has active team: {ls['active_team']}. One team per session."
        )
    result = teams.create_team(
        name=team_name, session_id=ls["session_id"], description=description
    )
    ls["active_team"] = team_name

    return result.model_dump()


@mcp.tool
def team_delete(team_name: str, ctx: Context) -> dict:
    """Delete a team and all its data. Fails if any teammates are still active.
    Removes both team config and task directories."""
    try:
        result = teams.delete_team(team_name)
    except (RuntimeError, FileNotFoundError) as e:
        raise ToolError(str(e))
    _get_lifespan(ctx)["active_team"] = None
    return result.model_dump()


@mcp.tool(name="spawn_teammate")
def spawn_teammate_tool(
    team_name: str,
    name: str,
    prompt: str,
    cwd: str,
    ctx: Context,
    model: str = "sonnet",
    subagent_type: str = "general-purpose",
    plan_mode_required: bool = False,
    backend_type: Literal["claude"] = "claude",
) -> dict:
    """Spawn a new teammate in tmux. Description is dynamically updated
    at startup with available backends and models."""
    import os.path

    if not cwd or not os.path.isabs(cwd):
        raise ToolError("cwd is required and must be an absolute path.")
    ls = _get_lifespan(ctx)
    enabled = ls.get("enabled_backends", [])
    if enabled and backend_type not in enabled:
        raise ToolError(f"Backend {backend_type!r} is not enabled. Enabled: {enabled}")
    try:
        member = spawn_teammate(
            team_name=team_name,
            name=name,
            prompt=prompt,
            claude_binary=ls["claude_binary"],
            lead_session_id=ls["session_id"],
            model=model,
            subagent_type=subagent_type,
            plan_mode_required=plan_mode_required,
            backend_type=backend_type,
            cwd=cwd,
        )
    except ValueError as e:
        raise ToolError(str(e))
    return SpawnResult(
        agent_id=member.agent_id,
        name=member.name,
        team_name=team_name,
    ).model_dump()


def _find_teammate(team_name: str, name: str) -> TeammateMember | None:
    config = teams.read_config(team_name)
    for m in config.members:
        if isinstance(m, TeammateMember) and m.name == name:
            return m
    return None


@mcp.tool
def send_message(
    team_name: str,
    type: Literal[
        "message",
        "broadcast",
        "shutdown_request",
        "shutdown_response",
        "plan_approval_response",
    ],
    ctx: Context,
    recipient: str = "",
    content: str = "",
    summary: str = "",
    request_id: str = "",
    approve: bool | None = None,
    sender: str = "team-lead",
) -> dict:
    """Send a message to a teammate or respond to a protocol request.
    Type 'message' sends a direct message (requires recipient, summary).
    Type 'broadcast' sends to all teammates (requires summary).
    Type 'shutdown_request' asks a teammate to shut down (requires recipient; content used as reason).
    Type 'shutdown_response' responds to a shutdown request (requires sender, request_id, approve).
    Type 'plan_approval_response' responds to a plan approval request (requires recipient, request_id, approve)."""
    try:
        teams.read_config(team_name)
    except FileNotFoundError:
        raise ToolError(f"Team {team_name!r} not found")

    if type == "message":
        if not content:
            raise ToolError("Message content must not be empty")
        if not summary:
            raise ToolError("Message summary must not be empty")
        if not recipient:
            raise ToolError("Message recipient must not be empty")
        config = teams.read_config(team_name)
        member_names = {m.name for m in config.members}
        if sender not in member_names:
            raise ToolError(f"Sender {sender!r} is not a member of team {team_name!r}")
        if recipient not in member_names:
            raise ToolError(
                f"Recipient {recipient!r} is not a member of team {team_name!r}"
            )
        if sender == recipient:
            raise ToolError("Cannot send a message to yourself")
        # 09 R21 / W3 peer-DM consistency: the protocol substrate should
        # enforce membership and self-send guards, not a lead-only topology.
        # Routed wrappers already allowed peer→peer; keeping the full server
        # lead-only made native/full-MCP and routed-wrapper peers diverge.
        target_color = None
        sender_color = None
        for m in config.members:
            if isinstance(m, TeammateMember):
                if m.name == recipient:
                    target_color = m.color
                if m.name == sender:
                    sender_color = m.color
        content = _content_metadata(content, sender)
        messaging.send_plain_message(
            team_name,
            sender,
            recipient,
            content,
            summary=summary,
            color=sender_color,
        )

        return SendMessageResult(
            success=True,
            message=f"Message sent to {recipient}",
            routing={
                "sender": sender,
                "target": recipient,
                "targetColor": target_color,
                "senderColor": sender_color,
            },
        ).model_dump(exclude_none=True)

    elif type == "broadcast":
        if sender != "team-lead":
            raise ToolError("Only team-lead can send broadcasts")
        if not summary:
            raise ToolError("Broadcast summary must not be empty")
        config = teams.read_config(team_name)
        content = _content_metadata(content, sender)
        count = 0
        for m in config.members:
            if isinstance(m, TeammateMember):
                messaging.send_plain_message(
                    team_name,
                    "team-lead",
                    m.name,
                    content,
                    summary=summary,
                    color=None,
                )
                count += 1
        return SendMessageResult(
            success=True,
            message=f"Broadcast sent to {count} teammate(s)",
        ).model_dump(exclude_none=True)

    elif type == "shutdown_request":
        if not recipient:
            raise ToolError("Shutdown request recipient must not be empty")
        if recipient == "team-lead":
            raise ToolError("Cannot send shutdown request to team-lead")
        config = teams.read_config(team_name)
        member_names = {m.name for m in config.members}
        if recipient not in member_names:
            raise ToolError(
                f"Recipient {recipient!r} is not a member of team {team_name!r}"
            )
        req_id = messaging.send_shutdown_request(team_name, recipient, reason=content)
        return SendMessageResult(
            success=True,
            message=f"Shutdown request sent to {recipient}",
            request_id=req_id,
            target=recipient,
        ).model_dump(exclude_none=True)

    elif type == "shutdown_response":
        config = teams.read_config(team_name)
        member = None
        for m in config.members:
            if isinstance(m, TeammateMember) and m.name == sender:
                member = m
                break
        if member is None:
            raise ToolError(
                f"Sender {sender!r} is not a teammate in team {team_name!r}"
            )

        if approve:
            pane_id = member.tmux_pane_id
            backend = member.backend_type
            payload = ShutdownApproved(
                request_id=request_id,
                from_=sender,
                timestamp=messaging.now_iso(),
                pane_id=pane_id,
                backend_type=backend,
            )
            messaging.send_structured_message(team_name, sender, "team-lead", payload)
            return SendMessageResult(
                success=True,
                message=f"Shutdown approved for request {request_id}",
            ).model_dump(exclude_none=True)
        else:
            messaging.send_plain_message(
                team_name,
                sender,
                "team-lead",
                content or "Shutdown rejected",
                summary="shutdown_rejected",
            )
            return SendMessageResult(
                success=True,
                message=f"Shutdown rejected for request {request_id}",
            ).model_dump(exclude_none=True)

    elif type == "plan_approval_response":
        if not recipient:
            raise ToolError("Plan approval recipient must not be empty")
        config = teams.read_config(team_name)
        member_names = {m.name for m in config.members}
        if recipient not in member_names:
            raise ToolError(
                f"Recipient {recipient!r} is not a member of team {team_name!r}"
            )
        if approve:
            messaging.send_plain_message(
                team_name,
                sender,
                recipient,
                '{"type":"plan_approval","approved":true}',
                summary="plan_approved",
            )
        else:
            messaging.send_plain_message(
                team_name,
                sender,
                recipient,
                content or "Plan rejected",
                summary="plan_rejected",
            )
        return SendMessageResult(
            success=True,
            message=f"Plan {'approved' if approve else 'rejected'} for {recipient}",
        ).model_dump(exclude_none=True)

    raise ToolError(f"Unknown message type: {type}")


@mcp.tool
def task_create(
    team_name: str,
    subject: str,
    description: str,
    active_form: str = "",
    metadata: dict | None = None,
    coupling: dict | str | None = None,
) -> dict:
    """Create a new task for the team. Tasks are auto-assigned incrementing IDs.
    Optional metadata dict is stored alongside the task. Optional coupling is
    the per-task coordination override."""
    try:
        task = tasks.create_task(
            team_name,
            subject,
            description,
            active_form,
            metadata,
            coupling,
        )
    except ValueError as e:
        raise ToolError(str(e))
    return {"id": task.id, "status": task.status, "coupling": task.coupling}


@mcp.tool
def task_update(
    team_name: str,
    task_id: str,
    status: Literal["pending", "in_progress", "completed", "deleted"] | None = None,
    owner: str | None = None,
    subject: str | None = None,
    description: str | None = None,
    active_form: str | None = None,
    add_blocks: list[str] | None = None,
    add_blocked_by: list[str] | None = None,
    metadata: dict | None = None,
) -> dict:
    """Update a task's fields. Setting owner auto-notifies the assignee via
    inbox. Setting status to 'deleted' removes the task file from disk.
    Metadata keys are merged into existing metadata (set a key to null to delete it)."""
    if owner is not None:
        try:
            config = teams.read_config(team_name)
        except FileNotFoundError:
            raise ToolError(f"Team {team_name!r} not found")
        member_names = {m.name for m in config.members}
        if owner not in member_names:
            raise ToolError(f"Owner {owner!r} is not a member of team {team_name!r}")
    try:
        task = tasks.update_task(
            team_name,
            task_id,
            status=status,
            owner=owner,
            subject=subject,
            description=description,
            active_form=active_form,
            add_blocks=add_blocks,
            add_blocked_by=add_blocked_by,
            metadata=metadata,
        )
    except FileNotFoundError:
        raise ToolError(f"Task {task_id!r} not found in team {team_name!r}")
    except ValueError as e:
        raise ToolError(str(e))
    if owner is not None and task.owner is not None and task.status != "deleted":
        messaging.send_task_assignment(team_name, task, assigned_by="team-lead")
    return {"id": task.id, "status": task.status}


@mcp.tool
def task_list(team_name: str) -> list[dict]:
    """List all tasks for a team with their current status and assignments."""
    try:
        result = tasks.list_tasks(team_name)
    except ValueError as e:
        raise ToolError(str(e))
    return [t.model_dump(by_alias=True, exclude_none=True) for t in result]


@mcp.tool
def task_get(team_name: str, task_id: str) -> dict:
    """Get full details of a specific task by ID."""
    try:
        task = tasks.get_task(team_name, task_id)
    except FileNotFoundError:
        raise ToolError(f"Task {task_id!r} not found in team {team_name!r}")
    return task.model_dump(by_alias=True, exclude_none=True)


@mcp.tool
def read_inbox(
    team_name: str,
    agent_name: str,
    unread_only: bool = True,
    mark_as_read: bool = True,
) -> list[dict]:
    """Read inbox messages. Description is dynamically updated at startup."""
    try:
        config = teams.read_config(team_name)
    except FileNotFoundError:
        raise ToolError(f"Team {team_name!r} not found")
    member_names = {m.name for m in config.members}
    if agent_name not in member_names:
        raise ToolError(f"Agent {agent_name!r} is not a member of team {team_name!r}")
    msgs = messaging.read_inbox(
        team_name, agent_name, unread_only=unread_only, mark_as_read=mark_as_read
    )
    return [m.model_dump(by_alias=True, exclude_none=True) for m in msgs]


@mcp.tool
def read_config(team_name: str) -> dict:
    """Read the current team configuration including all members."""
    try:
        config = teams.read_config(team_name)
    except FileNotFoundError:
        raise ToolError(f"Team {team_name!r} not found")
    data = config.model_dump(by_alias=True)
    for m in data.get("members", []):
        m.pop("prompt", None)
    return data


@mcp.tool
def force_kill_teammate(team_name: str, agent_name: str, ctx: Context) -> dict:
    """Forcibly kill a teammate's tmux target. Use when graceful shutdown via
    send_message(type='shutdown_request') is not possible or not responding.
    Kills the tmux pane/window, removes member from config, and resets their tasks."""
    config = teams.read_config(team_name)
    member = None
    for m in config.members:
        if isinstance(m, TeammateMember) and m.name == agent_name:
            member = m
            break
    if member is None:
        raise ToolError(f"Teammate {agent_name!r} not found in team {team_name!r}")
    if member.tmux_pane_id:
        kill_tmux_pane(member.tmux_pane_id)
    teams.remove_member(team_name, agent_name)
    tasks.reset_owner_tasks(team_name, agent_name)
    return {"success": True, "message": f"{agent_name} has been stopped."}


@mcp.tool
def process_shutdown_approved(team_name: str, agent_name: str, ctx: Context) -> dict:
    """Process a teammate's shutdown by removing them from config and resetting
    their tasks. Call this after confirming shutdown_approved in the lead inbox."""
    if agent_name == "team-lead":
        raise ToolError("Cannot process shutdown for team-lead")
    member = _find_teammate(team_name, agent_name)
    if member is None:
        raise ToolError(f"Teammate {agent_name!r} not found in team {team_name!r}")
    if member.tmux_pane_id:
        kill_tmux_pane(member.tmux_pane_id)
    teams.remove_member(team_name, agent_name)
    tasks.reset_owner_tasks(team_name, agent_name)
    return {"success": True, "message": f"{agent_name} removed from team."}


@mcp.tool
async def check_teammate(
    team_name: str,
    agent_name: str,
    ctx: Context,
    include_output: bool = False,
    output_lines: int = 20,
    include_messages: bool = True,
    max_messages: int = 5,
    notify_after_minutes: int | None = None,
) -> dict:
    """Check a single teammate's status. Description is dynamically updated
    at startup with push notification availability."""
    output_lines = max(1, min(output_lines, 120))
    max_messages = max(1, min(max_messages, 20))
    if notify_after_minutes is not None and notify_after_minutes < 1:
        raise ToolError("notify_after_minutes must be >= 1")

    try:
        config = teams.read_config(team_name)
    except FileNotFoundError:
        raise ToolError(f"Team {team_name!r} not found")

    member = None
    for m in config.members:
        if isinstance(m, TeammateMember) and m.name == agent_name:
            member = m
            break
    if member is None:
        raise ToolError(f"Teammate {agent_name!r} not found in team {team_name!r}")

    # 1. Read lead's inbox for unread messages FROM this teammate
    pending_from: list[dict] = []
    if include_messages:
        msgs = messaging.read_inbox_filtered(
            team_name=team_name,
            agent_name="team-lead",
            sender_filter=agent_name,
            unread_only=True,
            mark_as_read=True,
            limit=max_messages,
        )
        pending_from = [m.model_dump(by_alias=True, exclude_none=True) for m in msgs]

    # 2. Check teammate's unread count (messages they haven't read)
    try:
        their_unread = messaging.read_inbox(
            team_name, agent_name, unread_only=True, mark_as_read=False
        )
        their_unread_count = len(their_unread)
    except (FileNotFoundError, json.JSONDecodeError):
        their_unread_count = 0

    # 3. tmux status
    alive = False
    error = None
    output = ""
    if not member.tmux_pane_id:
        error = "no tmux target recorded"
    else:
        pane_id, resolve_error = resolve_pane_target(member.tmux_pane_id)
        if pane_id is None:
            error = resolve_error
        else:
            pane = peek_pane(pane_id, output_lines if include_output else 1)
            alive = pane["alive"]
            error = pane["error"]
            if include_output:
                output = pane["output"]

    # 4. Optional deferred notification
    push_available = False
    notification_scheduled = False

    if notify_after_minutes is not None:
        raise ToolError("notify_after_minutes is not supported by the current harness.")

    result: dict = {
        "name": agent_name,
        "alive": alive,
        "pending_from": pending_from,
        "their_unread_count": their_unread_count,
        "error": error,
        "notification_scheduled": notification_scheduled,
        "push_available": push_available,
    }
    if include_output:
        result["output"] = output
    return result


def main():
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    mcp.run()


if __name__ == "__main__":
    main()
