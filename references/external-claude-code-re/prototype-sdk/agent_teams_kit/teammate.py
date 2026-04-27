from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any, Literal

from claude_teams.models import InboxMessage, TaskFile  # type: ignore[import-untyped]

from .capabilities import flat_capabilities, peer_prompt_fragment, validate_manifest
from .events import Severity, VisibilityEvent, VisibilityFlags
from .messages import (
    IdleNotification,
    ShutdownRequest,
    ShutdownResponse,
    Steer,
    TaskAssignment,
    TaskBlocked,
    TaskComplete,
    TaskResult,
    dumps_payload,
    now_iso,
    parse_protocol_text,
)
from .storage import ClaimConflict, FilesystemStorage, TeamStorage
from .team import Team


class PermissionBridgeUnsupported(RuntimeError):
    pass


class Teammate:
    """08 §6.3 base: transport-layer defaults, capability layer declared by subclass."""

    team: str
    name: str
    cwd: Path
    color: str = "cyan"
    plan_mode_required: bool = False
    storage: TeamStorage

    poll_interval_s = 1.5
    idle_interval_s = 60.0

    def __init__(
        self,
        *,
        team: str,
        name: str,
        cwd: Path | str | None = None,
        color: str = "cyan",
        plan_mode_required: bool = False,
        storage: TeamStorage | None = None,
        model: str = "unknown",
        effort: str = "medium",
    ):
        self.team = team
        self.name = name
        self.cwd = Path(cwd or Path.cwd())
        self.color = color
        self.plan_mode_required = plan_mode_required
        self.storage = storage or FilesystemStorage()
        self.model = model
        self.effort = effort
        self.backend = self.agent_card().get("transport", "prototype")
        self._seq = 0
        self._shutdown_seen: set[str] = set()
        self._in_flight_task: str | None = None
        self._last_idle = 0.0
        self._last_progress_mailbox = 0.0
        self.team_view = Team(team, self.storage)
        self.peer_manifests: dict[str, dict[str, Any]] = {}

    @classmethod
    def parse_argv(cls, argv: list[str] | None = None) -> argparse.Namespace:
        """08 §6.5 argv parse/env wiring lives in the kit, not adapters."""
        p = argparse.ArgumentParser(prog=cls.__name__)
        p.add_argument("--team", required=True)
        p.add_argument("--name", required=True)
        p.add_argument("--cwd", default=str(Path.cwd()))
        p.add_argument("--color", default="cyan")
        p.add_argument("--base-dir", default=None)
        p.add_argument("--model", default="unknown")
        p.add_argument("--effort", default="medium")
        p.add_argument("--plan-mode-required", action="store_true")
        return p.parse_args(argv)

    @classmethod
    def from_argv(cls, argv: list[str] | None = None) -> "Teammate":
        args = cls.parse_argv(argv)
        return cls(
            team=args.team,
            name=args.name,
            cwd=args.cwd,
            color=args.color,
            plan_mode_required=args.plan_mode_required,
            storage=FilesystemStorage(args.base_dir) if args.base_dir else None,
            model=args.model,
            effort=args.effort,
        )

    # ---- Capability layer: subclasses override these, transport remains inherited. ----
    def agent_card(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "harness": "unknown",
            "harness_version": "unknown",
            "transport": "prototype",
            "capabilities": {},
        }

    def capabilities(self) -> list[str]:
        return flat_capabilities(self.agent_card())

    def capability_manifest(self, capability: str | None = None) -> dict[str, Any]:
        card = validate_manifest(self.agent_card())
        return card.get("capabilities", {}).get(capability, {}) if capability else card

    def peer_prompt_fragment(self) -> str:
        return peer_prompt_fragment(self.name, self.agent_card())

    def accepts_peer_steer(self) -> bool:
        card = self.agent_card()
        return card.get("accepts_peer_steer") is True or "accepts_peer_steer" in self.capabilities()

    async def execute_task(self, task: TaskFile) -> TaskResult:
        raise NotImplementedError

    async def reply_to_prose(self, peer: str, body: str) -> str | None:
        raise NotImplementedError

    async def request_permission(self, tool_name: str, tool_args: dict, task_id: str) -> Any:
        raise PermissionBridgeUnsupported(self.name)

    def steer_received(self, steer: Steer) -> Literal["live", "next_turn"]:
        self.emit_turn_progress(0, f"steer queued from {steer.from_ or 'peer'}", steer=steer.model_dump(by_alias=True))
        return "next_turn"

    # ---- Registration / lifecycle ----
    def register(self) -> dict[str, Any]:
        card = validate_manifest(self.agent_card())
        caps = flat_capabilities(card)

        def _upsert(cfg: dict[str, Any]) -> dict[str, Any]:
            members = cfg.setdefault("members", [])
            for member in members:
                if isinstance(member, dict) and member.get("name") == self.name:
                    row = member
                    break
            else:
                row = {"agentId": f"{self.name}@{self.team}", "name": self.name, "subscriptions": []}
                members.append(row)
            row.update(
                {
                    "agentType": "agent-teams-kit-prototype",
                    "backendType": card.get("harness", "prototype"),
                    "model": self.model,
                    "prompt": "Research prototype teammate; transport handled by agent_teams_kit.",
                    "color": self.color,
                    "planModeRequired": self.plan_mode_required,
                    "joinedAt": row.get("joinedAt") or int(time.time() * 1000),
                    "tmuxPaneId": row.get("tmuxPaneId") or "in-process",
                    "cwd": str(self.cwd),
                    "capabilities": caps,
                    "agentCard": card,
                    "peerPromptFragment": self.peer_prompt_fragment(),
                }
            )
            return cfg

        row: dict[str, Any] = {}
        cfg = self.storage.update_config(self.team, _upsert)
        for member in cfg.get("members", []):
            if isinstance(member, dict) and member.get("name") == self.name:
                row = member
        # Ensure our inbox exists without leaving registration noise.
        inbox_path = getattr(self.storage, "inbox_path", lambda *_: None)(self.team, self.name)
        if inbox_path is not None:
            inbox_path.parent.mkdir(parents=True, exist_ok=True)
            if not inbox_path.exists():
                inbox_path.write_text("[]\n")
        self.peer_manifests = self.team_view.broadcast_capability_manifest().copy()
        return row

    def deregister(self) -> bool:
        removed = False

        def _remove(cfg: dict[str, Any]) -> dict[str, Any]:
            nonlocal removed
            before = len(cfg.get("members", []))
            cfg["members"] = [m for m in cfg.get("members", []) if not (isinstance(m, dict) and m.get("name") == self.name)]
            removed = len(cfg["members"]) != before
            return cfg

        self.storage.update_config(self.team, _remove)
        return removed

    def on_shutdown_request(self, request_id: str) -> bool:
        if request_id in self._shutdown_seen:
            return True
        self._shutdown_seen.add(request_id)
        return self._in_flight_task is None

    def on_idle(self) -> str:
        return "available"

    def maybe_send_idle(self, *, force: bool = False) -> bool:
        now = time.monotonic()
        if not force and now - self._last_idle < self.idle_interval_s:
            return False
        self._last_idle = now
        payload = IdleNotification(from_=self.name, idle_reason=self.on_idle())
        self._send_payload("team-lead", payload, "idle")
        return True

    async def poll_once(self) -> int:
        handled = 0
        for msg in self.storage.read_own_inbox(self.team, self.name, unread_only=True):
            if msg.from_ == self.name and msg.summary == "self_register":
                continue
            await self._handle_message(msg)
            handled += 1
        return handled

    async def main_loop(self) -> None:
        self.register()
        while True:
            await self.poll_once()
            self.maybe_send_idle()
            await asyncio.sleep(self.poll_interval_s)

    async def _handle_message(self, msg: InboxMessage) -> None:
        payload = parse_protocol_text(msg.text)
        if isinstance(payload, TaskAssignment):
            await self._run_claimed_task(payload.task_id)
        elif isinstance(payload, ShutdownRequest):
            self._handle_shutdown(payload)
        elif isinstance(payload, Steer):
            if msg.from_ != "team-lead" and not self.accepts_peer_steer():
                self.emit_visibility_degraded("peer_steer", f"peer steer from {msg.from_} rejected", severity="warn")
            else:
                self.steer_received(payload)
        elif payload is None:
            reply = await self.reply_to_prose(msg.from_, msg.text)
            if reply:
                self._send_prose(msg.from_, reply, summary="prose_reply")

    async def _run_claimed_task(self, task_id: str) -> None:
        try:
            task = self.storage.claim_task(self.team, task_id, self.name, active_form="starting")
        except ClaimConflict as e:
            self.emit_visibility_degraded("task_claim", str(e), severity="warn", task_id=task_id)
            return
        self._in_flight_task = task_id
        self.emit_turn_started(task_id, turn_id=f"{self.name}-{task_id}", mode="task", cwd=str(self.cwd), model=self.model, effort=self.effort)
        try:
            result = await self.execute_task(task)
            if result.blocked:
                reason = result.reason or result.summary
                self.storage.update_task(self.team, task_id, {"active_form": f"blocked: {reason}"})
                self._send_payload("team-lead", TaskBlocked(task_id=task_id, reason=reason), f"task_blocked:{task_id}")
                self.emit_turn_failed(reason, "TaskBlocked", task_id=task_id)
            else:
                self.storage.update_task(self.team, task_id, {"status": "completed", "active_form": result.summary[:120]})
                self._send_payload(
                    "team-lead",
                    TaskComplete(task_id=task_id, files_changed=result.files_changed, summary=result.summary, backend_exit_code=result.exit_code),
                    f"task_complete:{task_id}",
                )
                self.emit_turn_completed(result.exit_code, task_id=task_id, summary=result.summary)
        except Exception as e:
            self.storage.update_task(self.team, task_id, {"active_form": f"failed: {e}"})
            self._send_payload("team-lead", TaskBlocked(task_id=task_id, reason=str(e)), f"task_failed:{task_id}")
            self.emit_turn_failed(str(e), e.__class__.__name__, task_id=task_id)
        finally:
            self._in_flight_task = None

    def _handle_shutdown(self, request: ShutdownRequest) -> None:
        approve = self.on_shutdown_request(request.request_id)
        response = ShutdownResponse(request_id=request.request_id, approve=approve, feedback=None if approve else "task in flight")
        self._send_payload("team-lead", response, "shutdown_approved" if approve else "shutdown_rejected")

    # ---- Visibility primitives; _emit handles B9 §6 fan-out. ----
    def emit_turn_started(self, task_id: str, turn_id: str, **payload: Any) -> None:
        self._emit("turn_started", f"started task {task_id}", task_id=task_id, turn_id=turn_id, payload=payload, task_state=True)

    def emit_tool_event(self, category: str, tool_name: str, phase: str, **payload: Any) -> None:
        severity: Severity = "error" if phase == "failed" else "info"
        self._emit("tool_event", f"{tool_name} {phase}", severity=severity, payload={"category": category, "tool_name": tool_name, "phase": phase, **payload})

    def emit_artifact_event(self, path: str, action: str, **payload: Any) -> None:
        self._emit("artifact_event", f"artifact {action}: {path}", payload={"path": path, "action": action, **payload}, task_state=True)

    def emit_turn_progress(self, elapsed_s: float, summary: str, **payload: Any) -> None:
        mailbox = time.monotonic() - self._last_progress_mailbox >= self.idle_interval_s
        if mailbox:
            self._last_progress_mailbox = time.monotonic()
        self._emit("turn_progress", summary, payload={"elapsed_s": elapsed_s, **payload}, mailbox=mailbox, task_state=True)

    def emit_turn_completed(self, exit_code: int, **payload: Any) -> None:
        self._emit("turn_completed", payload.pop("summary", f"turn completed ({exit_code})"), payload={"exit_code": exit_code, **payload}, mailbox=True, task_state=True)

    def emit_turn_failed(self, error: str, error_class: str, **payload: Any) -> None:
        self._emit("turn_failed", f"{error_class}: {error}", severity="error", payload={"error": error, "error_class": error_class, **payload}, mailbox=True, task_state=True)

    def emit_visibility_degraded(self, surface: str, reason: str, *, severity: Severity = "warn", **payload: Any) -> None:
        self._emit("visibility_degraded", f"visibility degraded: {surface}", severity=severity, payload={"surface": surface, "reason": reason, **payload}, mailbox=True)

    def _emit(
        self,
        kind: Any,
        summary: str,
        *,
        severity: Severity = "info",
        task_id: str | None = None,
        turn_id: str | None = None,
        payload: dict[str, Any] | None = None,
        mailbox: bool = False,
        task_state: bool = False,
    ) -> VisibilityEvent:
        self._seq += 1
        task_id = task_id or self._in_flight_task
        mailbox = mailbox or severity in {"warn", "error"}
        event = VisibilityEvent(
            kind=kind,
            event_id=f"{self.name}:{task_id or 'no-task'}:{self._seq}",
            team=self.team,
            agent=self.name,
            backend=self.backend,
            task_id=task_id,
            turn_id=turn_id,
            seq=self._seq,
            severity=severity,
            visibility=VisibilityFlags(mailbox=mailbox, task_state=task_state),
            summary=summary,
            payload=payload or {},
        )
        print(event.json_line(), file=sys.stderr)
        self.storage.append_event(self.team, self.name, event)
        if mailbox:
            self._send_prose("team-lead", event.json_line(), summary=f"event:{kind}")
        if task_state and task_id:
            self.storage.update_task(self.team, task_id, {"active_form": summary[:120], "metadata": {"visibility": {"last_event": kind, "seq": self._seq}}})
        return event

    def _send_payload(self, to: str, payload: Any, summary: str) -> None:
        self._send_prose(to, dumps_payload(payload), summary=summary)

    def _send_prose(self, to: str, text: str, summary: str) -> None:
        self.storage.append_message(self.team, to, InboxMessage(from_=self.name, text=text, timestamp=now_iso(), read=False, summary=summary, color=self.color))
