from __future__ import annotations

import json
import logging
from collections import deque
from pathlib import Path
from typing import Any

from claude_teams import messaging, teams as team_store
from claude_teams._filelock import file_lock
from claude_teams.models import TaskFile

TASKS_DIR = Path.home() / ".claude" / "tasks"
TEAMS_DIR = Path.home() / ".claude" / "teams"

logger = logging.getLogger(__name__)


def _tasks_dir(base_dir: Path | None = None) -> Path:
    return (base_dir / "tasks") if base_dir else TASKS_DIR


def _teams_dir(base_dir: Path | None = None) -> Path:
    return (base_dir / "teams") if base_dir else TEAMS_DIR


_STATUS_ORDER = {"pending": 0, "in_progress": 1, "completed": 2}


def _flush_pending_writes(pending_writes: dict[Path, TaskFile]) -> None:
    for path, task_obj in pending_writes.items():
        path.write_text(json.dumps(task_obj.model_dump(by_alias=True, exclude_none=True)))


def _would_create_cycle(
    team_dir: Path, from_id: str, to_id: str, pending_edges: dict[str, set[str]]
) -> bool:
    """True if making from_id blocked_by to_id creates a cycle.

    BFS from to_id through blocked_by chains (on-disk + pending);
    cycle if it reaches from_id.
    """
    visited: set[str] = set()
    queue = deque([to_id])
    while queue:
        current = queue.popleft()
        if current == from_id:
            return True
        if current in visited:
            continue
        visited.add(current)
        fpath = team_dir / f"{current}.json"
        if fpath.exists():
            task = TaskFile(**json.loads(fpath.read_text()))
            queue.extend(d for d in task.blocked_by if d not in visited)
        queue.extend(d for d in pending_edges.get(current, set()) if d not in visited)
    return False


def _validate_parent_task_id(
    team_dir: Path,
    task_id: str,
    parent_task_id: str | None,
) -> None:
    """Validate a child→parent link before persisting it.

    Parent links are independent from dependency edges: dependencies express
    scheduling constraints, while ``parentTaskId`` groups delegated sub-tasks
    for later batch summaries.  Still, the parent must exist and the parent
    chain must remain acyclic so a batch summary always has one clear root.
    """

    if parent_task_id is None:
        return
    if not parent_task_id.strip():
        raise ValueError("Parent task ID must not be empty")
    if parent_task_id == task_id:
        raise ValueError(f"Task {task_id} cannot be its own parent")

    visited = {task_id}
    current: str | None = parent_task_id
    while current is not None:
        if current in visited:
            raise ValueError(
                f"Setting parent task {parent_task_id!r} would create a circular parent task chain"
            )
        visited.add(current)
        parent_path = team_dir / f"{current}.json"
        if not parent_path.exists():
            raise ValueError(f"Referenced parent task {current!r} does not exist")
        parent = TaskFile(**json.loads(parent_path.read_text()))
        current = parent.parent_task_id


def _agent_config_path(
    team_name: str,
    agent_name: str,
    base_dir: Path | None = None,
) -> Path:
    return _teams_dir(base_dir) / team_name / "agents" / f"{agent_name}.json"


def _agent_auto_pickup_enabled(
    team_name: str,
    agent_name: str,
    base_dir: Path | None = None,
) -> bool:
    path = _agent_config_path(team_name, agent_name, base_dir=base_dir)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "task.auto_pickup.agent_config_unreadable",
            extra={
                "team": team_name,
                "agent": agent_name,
                "path": str(path),
                "error": str(exc),
            },
        )
        return False
    return isinstance(raw, dict) and raw.get("auto_pickup_next_task") is True


def _team_auto_pickup_enabled(
    team_name: str,
    base_dir: Path | None = None,
) -> bool:
    try:
        return bool(
            team_store.read_config(team_name, base_dir=base_dir).auto_pickup_next_task
        )
    except Exception as exc:
        logger.warning(
            "task.auto_pickup.team_config_unreadable",
            extra={"team": team_name, "error": str(exc)},
        )
        return False


def _auto_pickup_enabled(
    team_name: str,
    agent_name: str,
    base_dir: Path | None = None,
) -> bool:
    return _team_auto_pickup_enabled(
        team_name, base_dir=base_dir
    ) or _agent_auto_pickup_enabled(team_name, agent_name, base_dir=base_dir)


def _task_unblocked(task: TaskFile, by_id: dict[str, TaskFile]) -> bool:
    for blocker_id in task.blocked_by:
        blocker = by_id.get(blocker_id)
        if blocker is not None and blocker.status not in ("completed", "deleted"):
            return False
    return True


def _sorted_task_files(team_dir: Path) -> list[tuple[Path, TaskFile]]:
    entries: list[tuple[int, Path, TaskFile]] = []
    for fpath in team_dir.glob("*.json"):
        try:
            numeric_id = int(fpath.stem)
        except ValueError:
            continue
        entries.append((numeric_id, fpath, TaskFile(**json.loads(fpath.read_text()))))
    entries.sort(key=lambda item: item[0])
    return [(fpath, task) for _, fpath, task in entries]


def _assign_next_unblocked_unclaimed_task(
    team_name: str,
    owner: str,
    *,
    completed_task_id: str,
    base_dir: Path | None = None,
) -> TaskFile | None:
    """Atomically assign the lowest-ID pending/unblocked/unclaimed task.

    This deliberately performs the read-check-write under the task lock rather
    than calling ``update_task``: the post-completion hook has already
    committed the completed task, and the next-task assignment must preserve
    compare-and-set semantics so any concurrent explicit owner assignment that
    reaches the lock first wins.
    """

    team_dir = _tasks_dir(base_dir) / team_name
    lock_path = team_dir / ".lock"

    with file_lock(lock_path):
        entries = _sorted_task_files(team_dir)
        by_id = {task.id: task for _, task in entries}
        for fpath, task in entries:
            if task.id == completed_task_id:
                continue
            if task.status != "pending":
                continue
            if task.owner not in (None, ""):
                continue
            if not _task_unblocked(task, by_id):
                continue
            task.owner = owner
            fpath.write_text(
                json.dumps(task.model_dump(by_alias=True, exclude_none=True))
            )
            return task
    return None


def _auto_pickup_next_task_after_completion(
    team_name: str,
    completed_task: TaskFile,
    *,
    was_completed: bool,
    base_dir: Path | None = None,
) -> None:
    if was_completed or completed_task.status != "completed":
        return
    owner = completed_task.owner
    if not owner:
        return
    if not _auto_pickup_enabled(team_name, owner, base_dir=base_dir):
        return

    try:
        next_task = _assign_next_unblocked_unclaimed_task(
            team_name,
            owner,
            completed_task_id=completed_task.id,
            base_dir=base_dir,
        )
    except Exception as exc:
        logger.warning(
            "task.auto_pickup.assign_failed",
            extra={
                "team": team_name,
                "agent": owner,
                "task_id": completed_task.id,
                "error": str(exc),
            },
        )
        return

    if next_task is None:
        return

    try:
        messaging.send_next_task_wakeup(
            team_name,
            owner,
            next_task,
            completed_task_id=completed_task.id,
            base_dir=base_dir,
        )
    except Exception as exc:
        logger.warning(
            "task.auto_pickup.wakeup_failed",
            extra={
                "team": team_name,
                "agent": owner,
                "completed_task_id": completed_task.id,
                "next_task_id": next_task.id,
                "error": str(exc),
            },
        )


def next_task_id(team_name: str, base_dir: Path | None = None) -> str:
    team_dir = _tasks_dir(base_dir) / team_name
    ids: list[int] = []
    for f in team_dir.glob("*.json"):
        try:
            ids.append(int(f.stem))
        except ValueError:
            continue
    return str(max(ids) + 1) if ids else "1"


def create_task(
    team_name: str,
    subject: str,
    description: str,
    active_form: str = "",
    metadata: dict | None = None,
    coupling: dict | str | None = None,
    base_dir: Path | None = None,
    parent_task_id: str | None = None,
) -> TaskFile:
    if not subject or not subject.strip():
        raise ValueError("Task subject must not be empty")
    if not team_store.team_exists(team_name, base_dir):
        raise ValueError(f"Team {team_name!r} does not exist")
    team_dir = _tasks_dir(base_dir) / team_name
    team_dir.mkdir(parents=True, exist_ok=True)
    lock_path = team_dir / ".lock"

    with file_lock(lock_path):
        task_id = next_task_id(team_name, base_dir)
        _validate_parent_task_id(team_dir, task_id, parent_task_id)
        task = TaskFile(
            id=task_id,
            subject=subject,
            description=description,
            active_form=active_form,
            status="pending",
            parent_task_id=parent_task_id,
            metadata=metadata,
            coupling=coupling,
        )
        fpath = team_dir / f"{task_id}.json"
        fpath.write_text(json.dumps(task.model_dump(by_alias=True, exclude_none=True)))

    return task


def get_task(
    team_name: str, task_id: str, base_dir: Path | None = None
) -> TaskFile:
    team_dir = _tasks_dir(base_dir) / team_name
    fpath = team_dir / f"{task_id}.json"
    raw = json.loads(fpath.read_text())
    return TaskFile(**raw)


def update_task(
    team_name: str,
    task_id: str,
    *,
    status: str | None = None,
    owner: str | None = None,
    subject: str | None = None,
    description: str | None = None,
    active_form: str | None = None,
    add_blocks: list[str] | None = None,
    add_blocked_by: list[str] | None = None,
    metadata: dict | None = None,
    coupling: dict | str | None = None,
    parent_task_id: str | None = None,
    base_dir: Path | None = None,
) -> TaskFile:
    team_dir = _tasks_dir(base_dir) / team_name
    lock_path = team_dir / ".lock"
    fpath = team_dir / f"{task_id}.json"

    with file_lock(lock_path):
        # --- Phase 1: Read ---
        task = TaskFile(**json.loads(fpath.read_text()))
        was_completed = task.status == "completed"

        # --- Phase 2: Validate (no disk writes) ---
        pending_edges: dict[str, set[str]] = {}

        if add_blocks:
            for b in add_blocks:
                if b == task_id:
                    raise ValueError(f"Task {task_id} cannot block itself")
                if not (team_dir / f"{b}.json").exists():
                    raise ValueError(f"Referenced task {b!r} does not exist")
            for b in add_blocks:
                pending_edges.setdefault(b, set()).add(task_id)

        if add_blocked_by:
            for b in add_blocked_by:
                if b == task_id:
                    raise ValueError(f"Task {task_id} cannot be blocked by itself")
                if not (team_dir / f"{b}.json").exists():
                    raise ValueError(f"Referenced task {b!r} does not exist")
            for b in add_blocked_by:
                pending_edges.setdefault(task_id, set()).add(b)

        if add_blocks:
            for b in add_blocks:
                if _would_create_cycle(team_dir, b, task_id, pending_edges):
                    raise ValueError(
                        f"Adding block {task_id} -> {b} would create a circular dependency"
                    )

        if add_blocked_by:
            for b in add_blocked_by:
                if _would_create_cycle(team_dir, task_id, b, pending_edges):
                    raise ValueError(
                        f"Adding dependency {task_id} blocked_by {b} would create a circular dependency"
                    )

        if parent_task_id is not None:
            _validate_parent_task_id(team_dir, task_id, parent_task_id)

        if status is not None and status != "deleted":
            cur_order = _STATUS_ORDER[task.status]
            new_order = _STATUS_ORDER.get(status)
            if new_order is None:
                raise ValueError(f"Invalid status: {status!r}")
            if new_order < cur_order:
                raise ValueError(
                    f"Cannot transition from {task.status!r} to {status!r}"
                )
            effective_blocked_by = set(task.blocked_by)
            if add_blocked_by:
                effective_blocked_by.update(add_blocked_by)
            if status in ("in_progress", "completed") and effective_blocked_by:
                for blocker_id in effective_blocked_by:
                    blocker_path = team_dir / f"{blocker_id}.json"
                    if blocker_path.exists():
                        blocker = TaskFile(**json.loads(blocker_path.read_text()))
                        if blocker.status != "completed":
                            raise ValueError(
                                f"Cannot set status to {status!r}: "
                                f"blocked by task {blocker_id} (status: {blocker.status!r})"
                            )

        # --- Phase 3: Mutate (in-memory only) ---
        pending_writes: dict[Path, TaskFile] = {}

        if subject is not None:
            task.subject = subject
        if description is not None:
            task.description = description
        if active_form is not None:
            task.active_form = active_form
        if owner is not None:
            task.owner = owner
        if coupling is not None:
            task.coupling = TaskFile(
                id=task.id,
                subject=task.subject,
                description=task.description,
                coupling=coupling,
            ).coupling
        if parent_task_id is not None:
            task.parent_task_id = parent_task_id

        if add_blocks:
            existing = set(task.blocks)
            for b in add_blocks:
                if b not in existing:
                    task.blocks.append(b)
                    existing.add(b)
                b_path = team_dir / f"{b}.json"
                if b_path in pending_writes:
                    other = pending_writes[b_path]
                else:
                    other = TaskFile(**json.loads(b_path.read_text()))
                if task_id not in other.blocked_by:
                    other.blocked_by.append(task_id)
                pending_writes[b_path] = other

        if add_blocked_by:
            existing = set(task.blocked_by)
            for b in add_blocked_by:
                if b not in existing:
                    task.blocked_by.append(b)
                    existing.add(b)
                b_path = team_dir / f"{b}.json"
                if b_path in pending_writes:
                    other = pending_writes[b_path]
                else:
                    other = TaskFile(**json.loads(b_path.read_text()))
                if task_id not in other.blocks:
                    other.blocks.append(task_id)
                pending_writes[b_path] = other

        if metadata is not None:
            current = task.metadata or {}
            for k, v in metadata.items():
                if v is None:
                    current.pop(k, None)
                else:
                    current[k] = v
            task.metadata = current if current else None

        if status is not None and status != "deleted":
            task.status = status
            if status == "completed":
                for f in team_dir.glob("*.json"):
                    try:
                        int(f.stem)
                    except ValueError:
                        continue
                    if f.stem == task_id:
                        continue
                    if f in pending_writes:
                        other = pending_writes[f]
                    else:
                        other = TaskFile(**json.loads(f.read_text()))
                    if task_id in other.blocked_by:
                        other.blocked_by.remove(task_id)
                        pending_writes[f] = other

        if status == "deleted":
            task.status = "deleted"
            for f in team_dir.glob("*.json"):
                try:
                    int(f.stem)
                except ValueError:
                    continue
                if f.stem == task_id:
                    continue
                if f in pending_writes:
                    other = pending_writes[f]
                else:
                    other = TaskFile(**json.loads(f.read_text()))
                changed = False
                if task_id in other.blocked_by:
                    other.blocked_by.remove(task_id)
                    changed = True
                if task_id in other.blocks:
                    other.blocks.remove(task_id)
                    changed = True
                if other.parent_task_id == task_id:
                    other.parent_task_id = None
                    changed = True
                if changed:
                    pending_writes[f] = other

        # --- Phase 4: Write ---
        if status == "deleted":
            _flush_pending_writes(pending_writes)
            fpath.unlink()
        else:
            fpath.write_text(
                json.dumps(task.model_dump(by_alias=True, exclude_none=True))
            )
            _flush_pending_writes(pending_writes)

    _auto_pickup_next_task_after_completion(
        team_name,
        task,
        was_completed=was_completed,
        base_dir=base_dir,
    )
    return task


def list_tasks(
    team_name: str, base_dir: Path | None = None
) -> list[TaskFile]:
    if not team_store.team_exists(team_name, base_dir):
        raise ValueError(f"Team {team_name!r} does not exist")
    team_dir = _tasks_dir(base_dir) / team_name
    tasks: list[TaskFile] = []
    for f in team_dir.glob("*.json"):
        try:
            int(f.stem)
        except ValueError:
            continue
        tasks.append(TaskFile(**json.loads(f.read_text())))
    tasks.sort(key=lambda t: int(t.id))
    return tasks


def reset_owner_tasks(
    team_name: str, agent_name: str, base_dir: Path | None = None
) -> None:
    team_dir = _tasks_dir(base_dir) / team_name
    lock_path = team_dir / ".lock"

    with file_lock(lock_path):
        for f in team_dir.glob("*.json"):
            try:
                int(f.stem)
            except ValueError:
                continue
            task = TaskFile(**json.loads(f.read_text()))
            if task.owner == agent_name:
                if task.status != "completed":
                    task.status = "pending"
                task.owner = None
                f.write_text(
                    json.dumps(task.model_dump(by_alias=True, exclude_none=True))
                )
