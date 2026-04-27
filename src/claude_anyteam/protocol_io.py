"""Helpers around the team-protocol I/O surface.

Inbox reads, task list reads, and atomic message sends are centralised here
so the rest of the adapter doesn't reach into `claude_teams` directly. The
module also provides helpers for adapter-owned message types
(`idle_notification`, `task_complete`, `plan_approval_request` outbound).
"""

from __future__ import annotations

import json
import inspect
import time
from typing import Any

from claude_teams._filelock import file_lock as _file_lock  # type: ignore[import-untyped]
from claude_teams import messaging as _m  # type: ignore[import-untyped]
from claude_teams import tasks as _t  # type: ignore[import-untyped]
from claude_teams import teams as _teams  # type: ignore[import-untyped]
from claude_teams.models import InboxMessage as _InboxMessage  # type: ignore[import-untyped]
from claude_teams.models import TaskFile as _TaskFile  # type: ignore[import-untyped]

from . import logger
from .messages import (
    IdleNotificationOut,
    PermissionRequestOut,
    PermissionResponseIn,
    PlanApprovalRequestOut,
    ShutdownApprovedOut,
    ShutdownRejectedOut,
    ShutdownResponseOut,
    TaskCompleteOut,
    VisibilityEvent,
    now_iso,
)


def read_config(team: str):
    return _teams.read_config(team)


def should_skip_prose_fallback(result: Any) -> bool:
    """Return True when a prose reply was already delivered by a tool call.

    09 R23 extracts the PR-#11/#12 delivered-via-tool guard from the Codex,
    Gemini, and Kimi loops into one helper. Some backends leave
    ``last_message`` empty after the model calls the wrapper ``send_message``
    tool; in that case the adapter must not add a canned fallback on top of
    the real peer reply.
    """
    return (
        result is not None
        and getattr(result, "exit_code", None) == 0
        and getattr(result, "tool_call_events", 0) > 0
    )


def read_inbox(team: str, name: str, *, mark_as_read: bool = True) -> list[_InboxMessage]:
    """Read unread messages from an inbox.

    Returns an empty list on read failures (harness mid-write JSON races);
    caller should retry on the next poll.

    NOTE: For the control loop, prefer `read_own_inbox(team, self_name)` —
    the team-protocol `read_inbox(..., mark_as_read=True)` path rewrites the
    entire inbox file using its own serializer, which would clobber another
    teammate's inbox if called by mistake. `read_own_inbox` asserts the
    invariant that we only mutate our own file.
    """
    try:
        return _m.read_inbox(team, name, unread_only=True, mark_as_read=mark_as_read)
    except json.JSONDecodeError as e:
        logger.warn("inbox.parse_race", error=str(e))
        return []
    except FileNotFoundError:
        return []


def read_own_inbox(team: str, self_name: str, agent_name: str) -> list[_InboxMessage]:
    """Read and mark-as-read the adapter's own inbox.

    Raises AssertionError if `agent_name` is not our own name. This guards
    against catastrophic data loss: the mark-as-read path rewrites the target
    inbox file with the protocol Pydantic serializer, which would strip
    harness-written fields from another teammate's file.

    Use this from the control loop; use `read_inbox(..., mark_as_read=False)`
    for read-only probes of anyone's inbox.
    """
    assert agent_name == self_name, (
        f"refusing to mark_as_read on another teammate's inbox: "
        f"self_name={self_name!r}, agent_name={agent_name!r}. "
        f"Use read_inbox(..., mark_as_read=False) for read-only access."
    )
    return read_inbox(team, agent_name, mark_as_read=True)


def list_tasks(team: str) -> list[_TaskFile]:
    return _t.list_tasks(team)


def get_task(team: str, task_id: str) -> _TaskFile:
    return _t.get_task(team, task_id)


def claim_task(team: str, task_id: str, owner: str, active_form: str) -> _TaskFile:
    """Atomically claim a pending task for `owner` and mark it in_progress.

    Enforces compare-and-set semantics: raises ValueError if the task is not
    in `pending` status or already has an owner other than `owner` at the
    moment of the claim.

    We inline the read-check-write under a single `file_lock` acquisition.
    We deliberately do NOT call `_t.update_task` from inside the lock —
    `filelock.FileLock` instances pointing at the same path are NOT mutually
    reentrant across separate instances (they each open their own fd and
    `fcntl.flock`), so re-entering via `update_task` deadlocks within the
    same process.
    """
    import json as _json
    from claude_teams import tasks as _cs_tasks
    from claude_teams._filelock import file_lock as _file_lock

    # Resolve via `claude_teams.tasks.TASKS_DIR` so tests that monkeypatch it
    # to a tmp_path resolve to the same location our inline write targets.
    tasks_dir = _cs_tasks.TASKS_DIR / team
    lock_path = tasks_dir / ".lock"
    task_path = tasks_dir / f"{task_id}.json"

    with _file_lock(lock_path):
        current = _t.get_task(team, task_id)
        if current.status != "pending":
            raise ValueError(
                f"task {task_id} is not pending (status={current.status!r}); "
                "cannot claim"
            )
        if current.owner not in (None, "", owner):
            raise ValueError(
                f"task {task_id} already owned by {current.owner!r}; "
                "cannot claim"
            )
        # Inline write: mutate the TaskFile and serialize back to disk with
        # the same `model_dump` shape the protocol task writer uses.
        current.status = "in_progress"
        current.owner = owner
        current.active_form = active_form
        task_path.write_text(
            _json.dumps(current.model_dump(by_alias=True, exclude_none=True))
        )
        return current


def update_task(
    team: str,
    task_id: str,
    *,
    status: str | None = None,
    active_form: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> _TaskFile:
    return _t.update_task(
        team,
        task_id,
        status=status,
        active_form=active_form,
        metadata=metadata,
    )


def send_prose(team: str, sender: str, to: str, text: str, summary: str) -> None:
    """Send a plain-text message to an arbitrary teammate."""
    _m.send_plain_message(team, sender, to, text, summary=summary)


def send_prose_to_lead(team: str, sender: str, text: str, summary: str) -> None:
    """Send a plain-text message to the lead with a summary header.

    Prose here should still be structured JSON templates per §4.4 — this
    wrapper exists so the transport function signature is explicit.
    """
    send_prose(team, sender, "team-lead", text, summary=summary)


def _send_plain_message_compat(
    team: str,
    sender: str,
    to: str,
    body: str,
    *,
    summary: str,
    message_kind: str | None = None,
) -> None:
    """Call the substrate send helper with an optional R3 message_kind.

    R16 can stub messageKind now without taking ownership of foundation's R3
    substrate migration: if the installed `claude_teams.messaging` already
    accepts `message_kind`, pass the kind through; otherwise write the raw
    inbox entry under the normal inbox lock with `messageKind` preserved.
    """

    kwargs: dict[str, Any] = {"summary": summary}
    if message_kind is not None:
        try:
            params = inspect.signature(_m.send_plain_message).parameters
        except (TypeError, ValueError):
            params = {}
        accepts_var_kw = any(
            p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()
        )
        if "message_kind" in params or accepts_var_kw:
            kwargs["message_kind"] = message_kind
            _m.send_plain_message(team, sender, to, body, **kwargs)
            return
        path = _m.ensure_inbox(team, to)
        with _file_lock(path.parent / ".lock"):
            raw_list = json.loads(path.read_text())
            raw_list.append(
                {
                    "from": sender,
                    "text": body,
                    "timestamp": now_iso(),
                    "read": False,
                    "summary": summary,
                    "messageKind": message_kind,
                }
            )
            path.write_text(json.dumps(raw_list))
        return
    _m.send_plain_message(team, sender, to, body, **kwargs)


def send_json_to_lead(
    team: str,
    sender: str,
    payload: dict[str, Any] | Any,
    summary: str,
    message_kind: str | None = None,
) -> None:
    """Send a JSON payload as the message body. `payload` may be a dict or
    a Pydantic model (must have `.model_dump_json`).
    """
    if hasattr(payload, "model_dump_json"):
        body = payload.model_dump_json(by_alias=True, exclude_none=True)
    else:
        body = json.dumps(payload)
    _send_plain_message_compat(
        team,
        sender,
        "team-lead",
        body,
        summary=summary,
        message_kind=message_kind,
    )


def _visibility_events_dir(team: str):
    """Return the R16/B9 §6.4 event-log directory for a team.

    The append-only visibility stream lives next to `inboxes/` but uses its
    own `events/.lock` so high-volume backend activity never contends with
    human mailbox traffic. Resolve through `claude_teams.messaging.TEAMS_DIR`
    so tests and alternate storage roots follow the same monkeypatch surface
    as inbox helpers.
    """

    return _m.TEAMS_DIR / team / "events"


def visibility_event_path(team: str, agent: str):
    return _visibility_events_dir(team) / f"{agent}.jsonl"


def append_visibility_event(
    team: str,
    agent: str,
    event: VisibilityEvent | dict[str, Any],
) -> VisibilityEvent:
    """Append one validated B9 §6 visibility envelope.

    This is the canonical full-fidelity channel introduced by 09 R16 / 08
    PE-2+L17: `~/.claude/teams/<team>/events/<agent>.jsonl`, guarded by the
    directory-local `events/.lock`. Fan-out to mailbox/task-state/stderr is
    policy on top of this envelope, not a backend-name flattener; emitters
    must preserve native event names in `payload.raw_backend_type` per
    07 §7.3.
    """

    envelope = (
        event if isinstance(event, VisibilityEvent)
        else VisibilityEvent.model_validate(event)
    )
    events_dir = _visibility_events_dir(team)
    events_dir.mkdir(parents=True, exist_ok=True)
    (events_dir / ".lock").touch(exist_ok=True)
    line = envelope.model_dump_json(by_alias=True, exclude_none=True)
    with _file_lock(events_dir / ".lock"):
        with (events_dir / f"{agent}.jsonl").open("a", encoding="utf-8") as f:
            f.write(line)
            f.write("\n")
    # Some filelock implementations may unlink an empty lockfile on release;
    # keep the protocol-visible events/.lock sentinel present like inboxes/.lock.
    (events_dir / ".lock").touch(exist_ok=True)
    return envelope


def append_event(
    team: str,
    agent: str,
    event: VisibilityEvent | dict[str, Any],
) -> VisibilityEvent:
    """Compatibility alias for the R16/R17/R18/R19 roadmap name.

    The implementation originally landed as ``append_visibility_event``; the
    protocol-rev docs and later work items (R17 codex sink, R18 wrapper
    instrumentation, R19 headless digests) refer to the same helper as
    ``append_event``. Keep both spellings wired to one append-only substrate.
    """

    return append_visibility_event(team, agent, event)


def read_visibility_events(
    team: str,
    agent: str,
    *,
    since_seq: int | None = None,
    limit: int | None = None,
) -> list[VisibilityEvent]:
    """Read event-log entries for diagnostics/tests.

    `since_seq` is exclusive (`seq > since_seq`), matching the R16
    offset-style acceptance without mutating the append-only file.
    """

    path = visibility_event_path(team, agent)
    if not path.exists():
        return []
    out: list[VisibilityEvent] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = VisibilityEvent.model_validate(json.loads(line))
        if since_seq is not None and event.seq <= since_seq:
            continue
        out.append(event)
    if limit is not None and limit >= 0:
        out = out[-limit:]
    return out


def append_event(
    team: str,
    agent: str,
    event: VisibilityEvent | dict[str, Any],
) -> VisibilityEvent:
    """Compatibility alias for the R16/R17 roadmap helper name.

    The implementation landed as ``append_visibility_event``; 09 R17 and 07
    §7 call the primitive ``append_event``. Keep both spellings so backend
    fan-out code can use the protocol name without breaking existing tests
    and callers from the R16 catch-up.
    """

    return append_visibility_event(team, agent, event)


def read_events(
    team: str,
    agent: str,
    *,
    since_seq: int | None = None,
    limit: int | None = None,
) -> list[VisibilityEvent]:
    """Compatibility alias for ``read_visibility_events``."""

    return read_visibility_events(
        team,
        agent,
        since_seq=since_seq,
        limit=limit,
    )


def send_visibility_event_to_lead(
    team: str,
    sender: str,
    event: VisibilityEvent | dict[str, Any],
    *,
    summary: str | None = None,
) -> None:
    """Mailbox fan-out for low-frequency visibility events.

    R3's `InboxMessage.message_kind` discriminator is present as an explicit
    field in this branch; set it to the envelope kind (`turn_progress`,
    `tool_event`, ...), while leaving the envelope body itself unchanged.
    """

    envelope = (
        event if isinstance(event, VisibilityEvent)
        else VisibilityEvent.model_validate(event)
    )
    send_json_to_lead(
        team,
        sender,
        envelope,
        summary=summary or f"visibility:{envelope.kind}",
        message_kind=envelope.kind,
    )


def send_idle_notification(team: str, sender: str, reason: str = "available") -> None:
    payload = IdleNotificationOut(from_=sender, idle_reason=reason)  # type: ignore[call-arg]
    send_json_to_lead(team, sender, payload, summary="idle")


def _shutdown_member_metadata(
    team: str,
    sender: str,
) -> tuple[str | None, str | None]:
    """Best-effort metadata for host-catalog shutdown_approved.

    The 03 §"Lifecycle" host-binary lifecycle/mailbox extract lists paneId/backendType as
    optional; include them when the shim registry has them, but never let a
    config read race block the shutdown acknowledgement.
    """

    try:
        cfg = _teams.read_config(team)
    except Exception as e:
        logger.debug("shutdown.metadata_unavailable", error=str(e))
        return None, None
    for member in getattr(cfg, "members", []):
        if getattr(member, "name", None) == sender:
            return (
                getattr(member, "tmux_pane_id", None),
                getattr(member, "backend_type", None),
            )
    return None, None


def send_shutdown_approved(
    team: str,
    sender: str,
    request_id: str,
    *,
    pane_id: str | None = None,
    backend_type: str | None = None,
) -> None:
    if pane_id is None or backend_type is None:
        found_pane_id, found_backend_type = _shutdown_member_metadata(team, sender)
        pane_id = pane_id if pane_id is not None else found_pane_id
        backend_type = backend_type if backend_type is not None else found_backend_type
    payload = ShutdownApprovedOut(
        request_id=request_id,
        from_=sender,
        pane_id=pane_id,
        backend_type=backend_type,
    )
    send_json_to_lead(
        team,
        sender,
        payload,
        summary="shutdown_approved",
        message_kind="shutdown_approved",
    )


def send_shutdown_rejected(
    team: str,
    sender: str,
    request_id: str,
    reason: str,
) -> None:
    payload = ShutdownRejectedOut(
        request_id=request_id,
        from_=sender,
        reason=reason or "Shutdown rejected",
    )
    send_json_to_lead(
        team,
        sender,
        payload,
        summary="shutdown_rejected",
        message_kind="shutdown_rejected",
    )


def send_shutdown_response(
    team: str,
    sender: str,
    request_id: str,
    approve: bool,
    feedback: str | None = None,
) -> None:
    """Deprecated R6 alias for one release.

    New code should call send_shutdown_approved/send_shutdown_rejected so the
    wire type matches 09 R6, 07 §5.1, and the 03 §"Lifecycle" host catalog.
    """

    logger.warn(
        "shutdown_response.deprecated_alias",
        replacement="shutdown_approved" if approve else "shutdown_rejected",
        request_id=request_id,
    )
    payload = ShutdownResponseOut(
        request_id=request_id,
        approve=approve,
        feedback=feedback,
    )
    host_payload = payload.to_host_catalog(sender)
    if isinstance(host_payload, ShutdownApprovedOut):
        pane_id, backend_type = _shutdown_member_metadata(team, sender)
        host_payload.pane_id = pane_id
        host_payload.backend_type = backend_type
        send_json_to_lead(
            team,
            sender,
            host_payload,
            summary="shutdown_approved",
            message_kind="shutdown_approved",
        )
    else:
        send_json_to_lead(
            team,
            sender,
            host_payload,
            summary="shutdown_rejected",
            message_kind="shutdown_rejected",
        )


def send_task_blocked(
    team: str,
    sender: str,
    task_id: str,
    reason: str,
) -> None:
    """Structured sibling of send_task_complete. Lets the lead see why a
    task the adapter claimed cannot proceed, without relying on prose
    parsing.
    """
    payload = {
        "kind": "task_blocked",
        "task_id": task_id,
        "reason": reason,
    }
    send_json_to_lead(team, sender, payload, summary=f"task_blocked:{task_id}")


def send_task_complete(
    team: str,
    sender: str,
    task_id: str,
    files_changed: list[str],
    summary_text: str,
    codex_exit_code: int,
) -> None:
    payload = TaskCompleteOut(
        task_id=task_id,
        files_changed=files_changed,
        summary=summary_text,
        codex_exit_code=codex_exit_code,
    )
    send_json_to_lead(team, sender, payload, summary=f"task_complete:{task_id}")


def send_plan_approval_request(
    team: str,
    sender: str,
    request_id: str,
    plan: dict[str, Any],
) -> None:
    payload = PlanApprovalRequestOut(request_id=request_id, plan=plan)
    send_json_to_lead(team, sender, payload, summary=f"plan_approval:{request_id}")


def send_permission_request_to_lead(
    team: str,
    sender: str,
    *,
    request_id: str,
    tool_name: str,
    tool_args: Any,
    task_id: str,
    trust_mode: str,
    label: str | None = None,
    session_id: str | None = None,
) -> None:
    payload = PermissionRequestOut(
        request_id=request_id,
        tool_name=tool_name,
        tool_args=tool_args,
        task_id=task_id,
        teammate_name=sender,
        trust_mode=trust_mode,  # type: ignore[arg-type]
        label=label,
        session_id=session_id,
    )
    send_json_to_lead(team, sender, payload, summary=f"permission_request:{request_id}")


def _read_matching_permission_response_locked(
    team: str,
    teammate_name: str,
    request_id: str,
) -> PermissionResponseIn | None:
    path = _m.inbox_path(team, teammate_name)
    if not path.exists():
        return None
    lock_path = path.parent / ".lock"
    with _file_lock(lock_path):
        raw_list = json.loads(path.read_text())
        if not isinstance(raw_list, list):
            return None
        matched: PermissionResponseIn | None = None
        matched_index: int | None = None
        for idx, entry in enumerate(raw_list):
            try:
                msg = _InboxMessage.model_validate(entry)
            except Exception:
                continue
            if msg.read or msg.from_ != "team-lead":
                continue
            try:
                raw = json.loads(msg.text)
            except (TypeError, ValueError):
                continue
            if not isinstance(raw, dict) or raw.get("type") != "permission_response":
                continue
            try:
                parsed = PermissionResponseIn.model_validate(raw)
            except Exception:
                logger.warn("permission_response.malformed", request_id=request_id)
                continue
            if parsed.request_id != request_id or parsed.decision is None:
                continue
            matched = parsed
            matched_index = idx
            break
        if matched is None or matched_index is None:
            return None
        raw_list[matched_index]["read"] = True
        path.write_text(json.dumps(raw_list))
        return matched


def wait_for_permission_response(
    *,
    team: str,
    teammate_name: str,
    request_id: str,
    timeout_s: float,
    poll_interval_s: float = 1.0,
) -> PermissionResponseIn | None:
    deadline = time.monotonic() + max(0.0, timeout_s)
    while True:
        try:
            response = _read_matching_permission_response_locked(team, teammate_name, request_id)
        except (json.JSONDecodeError, OSError) as e:
            logger.warn("permission_response.read_race", request_id=request_id, error=str(e))
            response = None
        if response is not None:
            return response
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        time.sleep(min(max(0.01, poll_interval_s), remaining))
