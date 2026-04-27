from __future__ import annotations

import json
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Callable, Iterator, Protocol

# The vendored substrate imports the third-party `filelock` package. This
# prototype's dependency budget is stdlib+pydantic+pytest, so provide a tiny
# POSIX-compatible fallback before importing claude_teams. Production should
# keep the substrate's real dependency.
try:  # pragma: no cover - exercised only when filelock is installed
    import filelock as _filelock_probe  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover - behavior covered via substrate use
    import fcntl
    import types

    class _StdlibFileLock:
        def __init__(self, path: str):
            self.path = path
            self._fh = None

        def __enter__(self):
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            self._fh = open(self.path, "a+")
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
            return self

        def __exit__(self, exc_type, exc, tb):
            assert self._fh is not None
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            self._fh.close()

    module = types.ModuleType("filelock")
    module.FileLock = _StdlibFileLock
    sys.modules["filelock"] = module

from claude_teams import messaging as ct_messaging  # type: ignore[import-untyped]
from claude_teams import tasks as ct_tasks  # type: ignore[import-untyped]
from claude_teams import teams as ct_teams  # type: ignore[import-untyped]
from claude_teams._filelock import file_lock  # type: ignore[import-untyped]
from claude_teams.models import InboxMessage, TaskFile  # type: ignore[import-untyped]

from .events import VisibilityEvent

TeamConfig = dict[str, Any]
TaskUpdate = dict[str, Any]


class ConfigVersionConflict(RuntimeError):
    pass


class ClaimConflict(RuntimeError):
    pass


class TeamStorage(Protocol):
    """08 §6.2 substrate abstraction: config/inbox/task/event-log state."""

    def read_config(self, team: str) -> TeamConfig: ...
    def update_config(self, team: str, update: Callable[[TeamConfig], TeamConfig], *, expected_version: int | None = None) -> TeamConfig: ...
    def append_message(self, team: str, agent: str, message: InboxMessage) -> str: ...
    def read_own_inbox(self, team: str, agent: str, *, unread_only: bool = True) -> list[InboxMessage]: ...
    def ack_messages(self, team: str, agent: str, message_ids: list[str]) -> None: ...
    def list_tasks(self, team: str) -> list[TaskFile]: ...
    def get_task(self, team: str, task_id: str) -> TaskFile: ...
    def claim_task(self, team: str, task_id: str, new_owner: str, active_form: str, *, expected_status: str = "pending", expected_owner: str | None = None) -> TaskFile: ...
    def update_task(self, team: str, task_id: str, update: TaskUpdate, *, expected_version: int | None = None) -> TaskFile: ...
    def append_event(self, team: str, agent: str, envelope: VisibilityEvent) -> str: ...
    def read_events(self, team: str, agent: str, *, since_seq: int | None = None, limit: int = 100) -> list[VisibilityEvent]: ...
    @contextmanager
    def lock(self, path: str, *, timeout_s: float = 30.0) -> Iterator[None]: ...


class FilesystemStorage:
    """Filesystem default mirroring ~/.claude/{teams,tasks}/<team>/.

    Delegates to vendored `claude_teams` for task create/list/get/update and
    inbox shape where possible. Departures are deliberate research-prototype
    v2 needs: config writes preserve extra capability fields, inbox messages
    get stable messageId fields, and claim_task inlines a CAS under the task
    lock (the current substrate lacks that single primitive).
    """

    def __init__(self, base_dir: Path | str | None = None):
        self.base_dir = Path(base_dir) if base_dir is not None else Path.home() / ".claude"

    def teams_dir(self) -> Path:
        return self.base_dir / "teams"

    def tasks_dir(self) -> Path:
        return self.base_dir / "tasks"

    def create_team(self, team: str, *, session_id: str = "prototype-session") -> None:
        ct_teams.create_team(team, session_id=session_id, base_dir=self.base_dir)

    def config_path(self, team: str) -> Path:
        return self.teams_dir() / team / "config.json"

    def inbox_path(self, team: str, agent: str) -> Path:
        return self.teams_dir() / team / "inboxes" / f"{agent}.json"

    def read_config(self, team: str) -> TeamConfig:
        # Probe with substrate parser for compatibility, then return raw to keep v2 extras.
        ct_teams.read_config(team, base_dir=self.base_dir)
        return json.loads(self.config_path(team).read_text())

    def update_config(self, team: str, update: Callable[[TeamConfig], TeamConfig], *, expected_version: int | None = None) -> TeamConfig:
        path = self.config_path(team)
        (path.parent / "inboxes").mkdir(parents=True, exist_ok=True)
        with file_lock(path.parent / "inboxes" / ".lock"):
            cfg = json.loads(path.read_text())
            version = int(cfg.get("version", 0))
            if expected_version is not None and version != expected_version:
                raise ConfigVersionConflict(f"expected version {expected_version}, found {version}")
            new_cfg = update(cfg) or cfg
            new_cfg["version"] = version + 1
            self._atomic_write_json(path, new_cfg)
            return new_cfg

    def append_message(self, team: str, agent: str, message: InboxMessage) -> str:
        path = ct_messaging.ensure_inbox(team, agent, base_dir=self.base_dir)
        msg_id = f"msg-{int(time.time() * 1000)}-{os.getpid()}-{len(path.read_text())}"
        with file_lock(path.parent / ".lock"):
            raw = json.loads(path.read_text())
            entry = message.model_dump(by_alias=True, exclude_none=True)
            entry.setdefault("messageId", msg_id)
            raw.append(entry)
            self._atomic_write_json(path, raw)
        return msg_id

    def read_own_inbox(self, team: str, agent: str, *, unread_only: bool = True) -> list[InboxMessage]:
        # Inline instead of ct_messaging.read_inbox(mark_as_read=True): the
        # substrate serializer would drop unknown v2 fields like messageId.
        path = self.inbox_path(team, agent)
        if not path.exists():
            return []
        try:
            with file_lock(path.parent / ".lock"):
                raw = json.loads(path.read_text())
                selected = [e for e in raw if not (unread_only and e.get("read"))]
                for entry in selected:
                    entry["read"] = True
                self._atomic_write_json(path, raw)
            return [InboxMessage.model_validate(e) for e in selected]
        except (json.JSONDecodeError, FileNotFoundError):
            return []

    def ack_messages(self, team: str, agent: str, message_ids: list[str]) -> None:
        ids = set(message_ids)
        if not ids:
            return
        path = self.inbox_path(team, agent)
        with file_lock(path.parent / ".lock"):
            raw = json.loads(path.read_text()) if path.exists() else []
            for entry in raw:
                if entry.get("messageId") in ids:
                    entry["read"] = True
            self._atomic_write_json(path, raw)

    def list_tasks(self, team: str) -> list[TaskFile]:
        return ct_tasks.list_tasks(team, base_dir=self.base_dir)

    def get_task(self, team: str, task_id: str) -> TaskFile:
        return ct_tasks.get_task(team, task_id, base_dir=self.base_dir)

    def create_task(self, team: str, subject: str, description: str) -> TaskFile:
        return ct_tasks.create_task(team, subject=subject, description=description, base_dir=self.base_dir)

    def claim_task(self, team: str, task_id: str, new_owner: str, active_form: str, *, expected_status: str = "pending", expected_owner: str | None = None) -> TaskFile:
        team_dir = self.tasks_dir() / team
        path = team_dir / f"{task_id}.json"
        with file_lock(team_dir / ".lock"):
            task = TaskFile.model_validate(json.loads(path.read_text()))
            if task.status != expected_status:
                raise ClaimConflict(f"task {task_id} status {task.status!r} != {expected_status!r}")
            if expected_owner is not None and task.owner != expected_owner:
                raise ClaimConflict(f"task {task_id} owner {task.owner!r} != {expected_owner!r}")
            if expected_owner is None and task.owner not in (None, "", new_owner):
                raise ClaimConflict(f"task {task_id} already owned by {task.owner!r}")
            task.status = "in_progress"
            task.owner = new_owner
            task.active_form = active_form
            self._atomic_write_json(path, task.model_dump(by_alias=True, exclude_none=True))
            return task

    def update_task(self, team: str, task_id: str, update: TaskUpdate, *, expected_version: int | None = None) -> TaskFile:
        return ct_tasks.update_task(team, task_id, base_dir=self.base_dir, **update)

    def append_event(self, team: str, agent: str, envelope: VisibilityEvent) -> str:
        events_dir = self.teams_dir() / team / "events"
        events_dir.mkdir(parents=True, exist_ok=True)
        path = events_dir / f"{agent}.jsonl"
        with file_lock(events_dir / ".lock"):
            with path.open("a", encoding="utf-8") as f:
                f.write(envelope.json_line() + "\n")
        return envelope.event_id

    def read_events(self, team: str, agent: str, *, since_seq: int | None = None, limit: int = 100) -> list[VisibilityEvent]:
        path = self.teams_dir() / team / "events" / f"{agent}.jsonl"
        if not path.exists():
            return []
        events = [VisibilityEvent.model_validate_json(line) for line in path.read_text().splitlines() if line.strip()]
        if since_seq is not None:
            events = [e for e in events if e.seq > since_seq]
        return events[-limit:]

    @contextmanager
    def lock(self, path: str, *, timeout_s: float = 30.0) -> Iterator[None]:
        # Research prototype: defer staleness detection to claude_teams._filelock.
        with file_lock(Path(path)):
            yield

    def _atomic_write_json(self, path: Path, value: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False)
        try:
            json.dump(value, tmp, indent=2)
            tmp.write("\n")
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp.close()
            os.replace(tmp.name, path)
        finally:
            if not tmp.closed:
                tmp.close()
            try:
                os.unlink(tmp.name)
            except FileNotFoundError:
                pass
