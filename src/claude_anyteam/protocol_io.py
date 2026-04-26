"""Helpers around the team-protocol I/O surface.

Inbox reads, task list reads, and atomic message sends are centralised here
so the rest of the adapter doesn't reach into `claude_teams` directly. The
module also provides helpers for adapter-owned message types
(`idle_notification`, `task_complete`, `plan_approval_request` outbound).
"""

from __future__ import annotations

import json
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
    ShutdownResponseOut,
    TaskCompleteOut,
    now_iso,
)


def read_config(team: str):
    return _teams.read_config(team)


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


def send_json_to_lead(
    team: str,
    sender: str,
    payload: dict[str, Any] | Any,
    summary: str,
) -> None:
    """Send a JSON payload as the message body. `payload` may be a dict or
    a Pydantic model (must have `.model_dump_json`).
    """
    if hasattr(payload, "model_dump_json"):
        body = payload.model_dump_json(by_alias=True, exclude_none=True)
    else:
        body = json.dumps(payload)
    _m.send_plain_message(team, sender, "team-lead", body, summary=summary)


def send_idle_notification(team: str, sender: str, reason: str = "available") -> None:
    payload = IdleNotificationOut(from_=sender, idle_reason=reason)  # type: ignore[call-arg]
    send_json_to_lead(team, sender, payload, summary="idle")


def send_shutdown_response(
    team: str,
    sender: str,
    request_id: str,
    approve: bool,
    feedback: str | None = None,
) -> None:
    payload = ShutdownResponseOut(
        request_id=request_id,
        approve=approve,
        feedback=feedback,
    )
    summary = "shutdown_approved" if approve else "shutdown_rejected"
    send_json_to_lead(team, sender, payload, summary=summary)


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
