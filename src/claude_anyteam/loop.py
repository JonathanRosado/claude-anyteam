"""Adapter control loop (§4.2 of architecture-decision.md, Python form).

Responsibilities per turn:

1. Drain inbox. Dispatch protocol messages (shutdown_request,
   plan_approval_request). Enqueue task_assignment references for claim.
2. Claim the next unblocked, unowned task assigned to us (or any pending
   unblocked task if none are explicitly assigned — prefer assigned).
3. Run Codex on the claimed task, mark completed, send task_complete.
4. When idle with no claimable tasks, send an idle_notification and sleep.

Safety properties:

- Shutdown is deterministic: approve unless mid-task; reject with
  `in-flight task #N` feedback otherwise.
- Exit-exit cleanup: on SIGTERM/SIGINT, if a task is in-flight it stays
  `in_progress` (lead can reclaim via reset_owner_tasks) but the loop
  deregisters before exiting.
- Every team-protocol call is wrapped so a transient FS race doesn't kill
  the loop — it logs and continues to next poll.
"""

from __future__ import annotations

import json
import os
import signal
import time
from dataclasses import dataclass, field
from typing import Any

from . import codex as codex_mod
from . import logger, protocol_io as pio
from . import prompts as prompts_mod
from .capabilities import (
    CODEX_APP_SERVER_CAPABILITIES,
    CODEX_EXEC_CAPABILITIES,
    assert_known_capabilities,
    rich_capability_manifest,
)
from .capability_manifest import CapabilityManifestCache
from .config import Settings
from .messages import (
    CapabilityManifestUpdatedIn,
    PlanApprovalRequestIn,
    ShutdownRequestIn,
    SteerIn,
    parse_protocol_text,
)
from .registration import BackendMetadata, deregister, register


APP_SERVER_TASK_STATE_SAMPLE_EVERY = 5


def _mid_turn_prose_should_be_steer(
    *,
    sender: str | None,
    recipient_capabilities: list[str],
) -> bool:
    """Return whether an untyped prose inbox message should become steer.

    Lead prose remains an operational steer while a task is in flight. Peer
    prose is more ambiguous: it can be ordinary coordination/informational DM
    traffic, and treating all such prose as ``turn/steer`` creates noisy
    peer_steer_rejected events for recipients that explicitly do not accept
    peer steer. The recipient's own capability declaration governs that
    interpretation: only recipients advertising ``accepts_peer_steer`` opt in
    to converting peer prose into mid-turn steer fragments.
    """

    if sender == "team-lead":
        return True
    return bool(sender and "accepts_peer_steer" in recipient_capabilities)


@dataclass
class LoopState:
    settings: Settings
    shutdown_requested: bool = False
    approved_shutdown: bool = False
    in_flight_task: str | None = None
    seen_shutdown_request_ids: set[str] = field(default_factory=set)
    # v7.2: most recent Codex session (`thread_id`) captured from a prior
    # task's JSONL event stream. First task for this adapter identity:
    # None → fresh `codex exec` (schema-constrained). Subsequent tasks:
    # invoke `codex exec resume <session_id>` so Codex carries prior-task
    # context forward. In-memory only; v7.2 doesn't persist across
    # adapter restarts.
    codex_session_id: str | None = None
    # v7.3 App Server lineage: threadId of the most recent `thread/start`
    # or `thread/fork` result. Passed to the next `app_server_invoke` as
    # `resume_thread_id` so that call uses `thread/fork` to inherit prior
    # conversational context. Same in-memory, same-process-lifetime scope
    # as codex_session_id.
    app_server_last_thread_id: str | None = None
    peer_manifest_cache: CapabilityManifestCache | None = None


def _backend_metadata(settings: Settings) -> BackendMetadata:
    """Registration metadata for the Codex adapter.

    09 R11 stores 08 §6.3 Agent Card-derived cheap capability flags on
    the roster; the richer manifest is intentionally deferred to R12/R13 wrapper MCP.
    """
    capabilities = (
        CODEX_APP_SERVER_CAPABILITIES if settings.app_server else CODEX_EXEC_CAPABILITIES
    )
    capabilities = assert_known_capabilities(capabilities)
    if settings.app_server:
        return BackendMetadata(
            capabilities=capabilities,
            capability_manifest=rich_capability_manifest(
                capabilities,
                delivery_mode="live",
                expiry_semantics="live_only",
                steer_authorization="lead_only",
                host_tool_surface="codex-native",
            ),
            transport="codex-app-server",
            host_tool_surface="codex-native",
        )
    return BackendMetadata(
        capabilities=capabilities,
        capability_manifest=rich_capability_manifest(
            capabilities,
            host_tool_surface="codex-native",
        ),
        transport="codex-exec",
        host_tool_surface="codex-native",
    )


def run(settings: Settings) -> int:
    """Run the adapter's main loop. Returns a process exit code."""
    codex_mod.feature_test(
        settings.codex_binary,
        mcp_probe=True,
        team=settings.team_name,
        agent_name=settings.agent_name,
    )
    register(settings, _backend_metadata(settings))

    state = LoopState(settings=settings)
    state.peer_manifest_cache = CapabilityManifestCache(
        settings.team_name,
        self_name=settings.agent_name,
    )
    state.peer_manifest_cache.load_startup()

    def _sig_handler(signum: int, _frame: Any) -> None:
        logger.warn("signal.received", signum=signum)
        state.shutdown_requested = True

    signal.signal(signal.SIGINT, _sig_handler)
    signal.signal(signal.SIGTERM, _sig_handler)

    exit_code = 0
    try:
        _main_loop(state)
    except Exception as e:
        logger.error("loop.crash", error=str(e))
        # Persist a structured incident so the lead can find this via
        # `claude-anyteam diagnose`. Without this the only crash signal
        # is stderr in a tmux pane the lead may not be reading.
        try:
            from . import diagnostics as _diag
            _diag.record_incident(
                team=settings.team_name,
                agent=settings.agent_name,
                backend="codex",
                error_class="adapter_crash",
                summary=str(e),
            )
        except Exception:
            # Diagnostics is best-effort; never let it mask the original error.
            pass
        exit_code = 1
    finally:
        # Deregister on a clean approved shutdown. SIGTERM-mid-task is NOT
        # a zombie case: the signal handler sets shutdown_requested=True and
        # the loop drains the current task turn before setting approved_shutdown
        # and returning — so SIGTERM exits cleanly. Only uncaught exceptions
        # (loop.crash) skip deregistration and leave a zombie entry for the
        # lead to inspect.
        if state.approved_shutdown:
            deregister(settings)
            logger.info("loop.deregistered", name=settings.agent_name)
        else:
            logger.warn(
                "loop.exit_without_deregister",
                in_flight_task=state.in_flight_task,
            )
    return exit_code


def _main_loop(state: LoopState) -> None:
    s = state.settings
    logger.info(
        "loop.start",
        team=s.team_name,
        name=s.agent_name,
        poll_s=s.poll_interval_s,
    )

    idle_last_sent_at: float | None = None

    while not state.approved_shutdown:
        # 1. Drain inbox. Use read_own_inbox so the "self-only" invariant is
        # asserted at call time — the protocol mark-as-read path rewrites the
        # file, and touching another teammate's inbox would corrupt its schema.
        messages = pio.read_own_inbox(s.team_name, s.agent_name, s.agent_name)
        for m in messages:
            _handle_message(state, m)
            if state.approved_shutdown:
                return

        # 2. Claim-and-execute.
        if not state.shutdown_requested:
            claimed = _find_and_claim(state)
            if claimed is not None:
                _execute_task(state, claimed)
                idle_last_sent_at = None
                continue  # loop again to drain inbox before idling

        # 3. Idle notification (rate-limited to once per 60s while idle).
        if not _has_claimable(state):
            now = time.monotonic()
            if idle_last_sent_at is None or (now - idle_last_sent_at) > 60.0:
                try:
                    pio.send_idle_notification(s.team_name, s.agent_name)
                    idle_last_sent_at = now
                    logger.info("idle.sent")
                except Exception as e:
                    logger.warn("idle.send_fail", error=str(e))

        # 4. Sleep.
        time.sleep(s.poll_interval_s)

        # 5. Honour SIGINT/SIGTERM if we haven't already agreed to shut down.
        if state.shutdown_requested and state.in_flight_task is None:
            logger.info("loop.signal_exit")
            state.codex_session_id = None
            state.app_server_last_thread_id = None
            state.approved_shutdown = True
            return


def _handle_message(state: LoopState, msg: Any) -> None:
    payload = parse_protocol_text(msg.text)
    if payload is None:
        _handle_prose(state, msg)
        return

    if isinstance(payload, ShutdownRequestIn):
        _handle_shutdown(state, payload)
        return
    if isinstance(payload, PlanApprovalRequestIn):
        _handle_plan_approval(state, payload)
        return
    if isinstance(payload, CapabilityManifestUpdatedIn):
        if state.peer_manifest_cache is not None:
            state.peer_manifest_cache.apply_update(payload)
        logger.info(
            "capability_manifest.update_seen",
            agent=payload.agent_name,
            capability_version=payload.capability_version,
            removed=payload.removed,
        )
        return
    # task_assignment, plan_approval_response — noted but not acted on here.
    # task_assignment messages are informational; the shared task list is the
    # source of truth. plan_approval_response is only meaningful if we sent a
    # request, handled in the opt-in branch.
    logger.debug("inbox.protocol_noop", type=payload.__class__.__name__)


def _peer_prompt_fragments(state: LoopState) -> str:
    """Return cached R14 peer capability prompt fragments for this turn.

    Honors the S10a ablation knob ``CLAUDE_ANYTEAM_DISABLE_PEER_PROMPT_FRAGMENTS=1``
    per references/external-claude-code-re/proto-rev-execution-log/specs/
    S10-ablation-implementation-spec.md §2 — when set, returns empty string
    so peer-capability fragments are absent from the system prompt without
    touching the cache or substrate.
    """
    if os.environ.get("CLAUDE_ANYTEAM_DISABLE_PEER_PROMPT_FRAGMENTS") == "1":
        return ""
    if state.peer_manifest_cache is None:
        return ""
    try:
        return state.peer_manifest_cache.peer_prompt_fragments_for(
            state.settings.agent_name
        )
    except Exception as e:
        logger.warn("capability_manifest.peer_prompt_fragments_fail", error=str(e))
        return ""


def _handle_prose(state: LoopState, msg: Any) -> None:
    """Handle an inbound prose (non-protocol) message while idle.

    Invokes Codex once (schema-free) with the peer's message as the prompt so
    Codex can compose a natural reply. Codex is instructed to use the
    `send_message` MCP wrapper tool to deliver the reply directly to the
    sender, which gives the closest parity to how native Claude agents handle
    peer prose.

    On any Codex failure, falls back to a minimal acknowledgement sent directly
    via `pio.send_prose` so the sender is never left with total silence.

    The prose invocation is intentionally ephemeral — it does not update
    `state.codex_session_id` or `state.app_server_last_thread_id`, keeping
    the task-lineage slots clean for the next real task.
    """
    s = state.settings
    sender = getattr(msg, "from_", "unknown")
    logger.info("inbox.prose", sender=sender, summary=getattr(msg, "summary", None))

    prompt = prompts_mod.v7_prose_reply_prompt(
        sender=sender,
        body=msg.text,
        agent_name=s.agent_name,
        team_name=s.team_name,
        peer_prompt_fragments=_peer_prompt_fragments(state),
    )

    reply: str | None = None
    result = None
    try:
        if s.app_server:
            result = codex_mod.app_server_invoke(
                task_prompt=prompt,
                cwd=s.cwd,
                schema=None,
                settings_team=s.team_name,
                settings_agent=s.agent_name,
                codex_binary=s.codex_binary,
                model=s.model,
                effort=s.effort,
                overall_timeout_s=s.turn_timeout_s,
                non_progress_warn_s=s.non_progress_warn_s,
                non_progress_interrupt_s=s.non_progress_interrupt_s,
                # No resume_thread_id — ephemeral, not chained to task lineage.
            )
        else:
            result = codex_mod.run(
                prompt=prompt,
                cwd=s.cwd,
                schema=None,
                codex_binary=s.codex_binary,
                extra_args=codex_mod.wrapper_mcp_config_args(s.team_name, s.agent_name),
                wrapper_identity=(s.team_name, s.agent_name),
                model=s.model,
                effort=s.effort,
            )
        if result.exit_code == 0 and result.last_message:
            reply = result.last_message
        else:
            logger.warn(
                "prose.codex_fail",
                sender=sender,
                exit_code=result.exit_code,
                error=result.error,
            )
    except Exception as e:
        logger.warn("prose.codex_crash", sender=sender, error=str(e))

    if reply is None and pio.should_skip_prose_fallback(result):
        logger.info("prose.delivered_via_tool", sender=sender, tool_calls=getattr(result, "tool_call_events", 0))
        return

    if reply is None:
        # Codex couldn't produce a reply — record full error context to a
        # per-incident diagnostic file and embed the incident_id in the
        # user-facing reply so the lead can run
        # `claude-anyteam diagnose --incident <id>` to recover details
        # without us leaking raw error strings into chat.
        from . import diagnostics  # local import keeps loop.py import graph thin
        error_class = diagnostics.classify_failure(result)
        incident_id = diagnostics.record_incident(
            team=s.team_name,
            agent=s.agent_name,
            backend="codex",
            error_class=error_class,
            summary=(result.error if result is not None and result.error else "no reply produced"),
            sender=sender,
            payload={
                "exit_code": (result.exit_code if result is not None else None),
                "tool_call_events": (getattr(result, "tool_call_events", 0) if result is not None else 0),
                "error": (result.error if result is not None else None),
            },
        )
        reply = diagnostics.fallback_message(
            backend="codex",
            incident_id=incident_id,
            error_class=error_class,
        )

    try:
        pio.send_prose(s.team_name, s.agent_name, sender, reply, summary="prose_reply")
        logger.info("prose.reply_sent", sender=sender)
    except Exception as e:
        logger.warn("prose.reply_send_fail", sender=sender, error=str(e))


def _handle_shutdown(state: LoopState, payload: ShutdownRequestIn) -> None:
    s = state.settings
    req_id = payload.effective_request_id() or "shutdown-unknown"

    # Idempotent: never respond twice to the same request_id.
    if req_id in state.seen_shutdown_request_ids:
        logger.debug("shutdown.duplicate_ignored", request_id=req_id)
        return
    state.seen_shutdown_request_ids.add(req_id)

    if state.in_flight_task is not None:
        feedback = f"in-flight task #{state.in_flight_task}"
        logger.info("shutdown.reject", request_id=req_id, in_flight=state.in_flight_task)
        try:
            pio.send_shutdown_rejected(s.team_name, s.agent_name, req_id, reason=feedback)
        except Exception as e:
            logger.warn("shutdown.response_fail", error=str(e))
        state.shutdown_requested = True  # honour after current task finishes
        return

    logger.info("shutdown.approve", request_id=req_id)
    try:
        pio.send_shutdown_approved(s.team_name, s.agent_name, req_id)
    except Exception as e:
        logger.warn("shutdown.response_fail", error=str(e))
    state.codex_session_id = None
    state.app_server_last_thread_id = None
    state.approved_shutdown = True


def _handle_plan_approval(state: LoopState, payload: PlanApprovalRequestIn) -> None:
    """Opt-in plan-mode path (§4.5). Only active when planModeRequired=True.

    Invokes Codex once with `--output-schema plan.schema.json` to produce a
    structured plan for the task referenced by the request (or the
    adapter's current assigned/claimable task if no task_id was provided).
    On a schema-conformant success, sends the plan to the lead via
    `plan_approval_request`. On failure, retries once with a tightened
    prompt (per §5 failure-mode row); if the retry also fails, marks the
    task blocked. Never sends a canned stub.

    Default policy (planModeRequired=False) drops the message with a
    warning — we are not in the business of answering plan requests we
    didn't opt into.
    """
    s = state.settings
    if not s.plan_mode_required:
        logger.warn("plan.unexpected_request", request_id=payload.request_id)
        return

    req_id = payload.request_id
    if req_id is None:
        logger.warn("plan.missing_request_id")
        return

    target_task = _target_task_for_plan(state, payload)
    if target_task is None:
        logger.warn("plan.no_target_task", request_id=req_id)
        # No task to plan against — tell the lead explicitly rather than
        # silently drop. Use a plain-text status message (kind: plan_blocked)
        # so the lead can see why no plan was produced.
        try:
            pio.send_prose_to_lead(
                s.team_name,
                s.agent_name,
                json.dumps({
                    "kind": "plan_blocked",
                    "request_id": req_id,
                    "reason": "no task_id in plan_approval_request and no claimable task in flight",
                }),
                summary=f"plan_blocked:{req_id}",
            )
        except Exception as e:
            logger.warn("plan.block_msg_fail", error=str(e))
        return

    logger.info(
        "plan.request_received",
        request_id=req_id,
        task_id=target_task.id,
    )

    for attempt in (1, 2):
        plan = _generate_plan(state, target_task, tighten=(attempt == 2))
        if plan is not None:
            try:
                pio.send_plan_approval_request(
                    s.team_name,
                    s.agent_name,
                    request_id=req_id,
                    plan=plan,
                )
                logger.info(
                    "plan.sent",
                    request_id=req_id,
                    task_id=target_task.id,
                    steps=len(plan.get("steps", [])),
                )
            except Exception as e:
                logger.warn("plan.send_fail", error=str(e))
            return
        logger.warn("plan.attempt_failed", attempt=attempt, task_id=target_task.id)

    # Both attempts failed — block the task and tell the lead. No stub.
    _mark_blocked(
        state,
        target_task,
        reason="plan generation failed twice (codex --output-schema produced no schema-conformant result)",
    )


def _target_task_for_plan(state: LoopState, payload: PlanApprovalRequestIn):
    """Resolve which task a plan_approval_request refers to.

    Prefers an explicit payload.task_id. Falls back to the adapter's
    currently-owned in-progress task, then to its highest-priority pending
    assigned task, then to the first unblocked pending unassigned task.
    Returns None if nothing claimable exists.
    """
    s = state.settings
    try:
        all_tasks = pio.list_tasks(s.team_name)
    except Exception as e:
        logger.warn("plan.task_list_fail", error=str(e))
        return None
    by_id = {t.id: t for t in all_tasks}

    if payload.task_id and payload.task_id in by_id:
        return by_id[payload.task_id]

    # In-flight first.
    if state.in_flight_task and state.in_flight_task in by_id:
        return by_id[state.in_flight_task]

    # Assigned-to-us pending.
    for t in sorted(
        (t for t in all_tasks
         if t.owner == s.agent_name
         and t.status == "pending"
         and not _blocked(all_tasks, t)),
        key=lambda x: int(x.id),
    ):
        return t

    # Unassigned pending (empty-string tolerant).
    for t in sorted(
        (t for t in all_tasks
         if (t.owner is None or t.owner == "")
         and t.status == "pending"
         and not _blocked(all_tasks, t)),
        key=lambda x: int(x.id),
    ):
        return t

    return None


def _generate_plan(state: LoopState, task, *, tighten: bool) -> dict[str, Any] | None:
    """Run `codex exec --output-schema plan.schema.json` once and return the
    structured plan, or None on any failure.

    `tighten=True` appends a stricter instruction on the retry (per §5).
    """
    s = state.settings
    prompt = prompts_mod.v7_plan_prompt(
        task,
        tighten=tighten,
        agent_name=s.agent_name,
        team_name=s.team_name,
    )
    try:
        result = codex_mod.run(
            prompt=prompt,
            cwd=s.cwd,
            schema=codex_mod.PLAN_SCHEMA,
            codex_binary=s.codex_binary,
            extra_args=codex_mod.wrapper_mcp_config_args(s.team_name, s.agent_name),
            wrapper_identity=(s.team_name, s.agent_name),
            model=s.model,
            effort=s.effort,
            task_id=task.id,
        )
    except Exception as e:
        logger.error("plan.codex_crash", task_id=task.id, error=str(e))
        return None

    if result.exit_code != 0 or result.structured is None:
        logger.warn(
            "plan.codex_fail",
            task_id=task.id,
            exit_code=result.exit_code,
            error=result.error,
        )
        return None

    return result.structured


def _find_and_claim(state: LoopState):
    """Return a claimed task, or None if no claimable task is available."""
    s = state.settings
    try:
        all_tasks = pio.list_tasks(s.team_name)
    except Exception as e:
        logger.warn("tasks.list_fail", error=str(e))
        return None

    # Prefer tasks already assigned to us (`owner == agent_name`) in pending
    # state — this handles the case where the lead pre-assigned via
    # task_update owner=<us>. Fall back to unowned pending tasks. Some
    # task-list tools serialize "no owner" as an empty string instead of
    # null, so treat "" as unowned.
    assigned_pending = [
        t for t in all_tasks
        if t.owner == s.agent_name and t.status == "pending" and not _blocked(all_tasks, t)
    ]
    unassigned_pending = [
        t for t in all_tasks
        if (t.owner is None or t.owner == "") and t.status == "pending" and not _blocked(all_tasks, t)
    ]

    # Sort by numeric id ascending to honour the "lowest id first" convention.
    assigned_pending.sort(key=lambda t: int(t.id))
    unassigned_pending.sort(key=lambda t: int(t.id))

    candidates = assigned_pending + unassigned_pending
    for t in candidates:
        try:
            claimed = pio.claim_task(
                s.team_name,
                t.id,
                s.agent_name,
                active_form=f"Running codex on task #{t.id}",
            )
            state.in_flight_task = claimed.id
            logger.info("task.claimed", task_id=claimed.id, subject=claimed.subject)
            return claimed
        except ValueError as e:
            # e.g., someone else claimed in the race window; try the next.
            logger.debug("task.claim_race", task_id=t.id, error=str(e))
            continue
    return None


def _has_claimable(state: LoopState) -> bool:
    s = state.settings
    try:
        all_tasks = pio.list_tasks(s.team_name)
    except Exception:
        return False
    for t in all_tasks:
        if t.status != "pending":
            continue
        if _blocked(all_tasks, t):
            continue
        if t.owner is None or t.owner == s.agent_name:
            return True
    return False


def _blocked(all_tasks: list, t) -> bool:
    if not getattr(t, "blocked_by", None):
        return False
    by_id = {x.id: x for x in all_tasks}
    for bid in t.blocked_by:
        blocker = by_id.get(bid)
        if blocker is None:
            continue
        if blocker.status not in ("completed", "deleted"):
            return True
    return False


def _execute_task(state: LoopState, task) -> None:
    """Run Codex on a claimed task and update state accordingly.

    v7.2: when a prior task has captured a Codex session id for this
    agent identity, subsequent tasks invoke `codex exec resume` so
    Codex carries prior-task context forward. Because `resume` does
    not accept `--output-schema` on codex-cli 0.122.0, the resume path
    validates the final output in Python via `schema_validation`.
    Retry-once-with-firmer-prompt is the failure path.
    """
    s = state.settings
    result = _invoke_codex_for_task(state, task)
    if result is None:
        # _invoke_codex_for_task has already recorded the failure and
        # called `_mark_blocked`. Exit early.
        state.in_flight_task = None
        return

    # Capture session id for subsequent tasks. In CLI resume mode this is
    # only populated on the fresh-exec branch; in App Server mode the
    # current thread id is returned on every successful invocation.
    if result.session_id and state.codex_session_id is None:
        state.codex_session_id = result.session_id
        logger.info("task.session_captured", session_id=result.session_id)

    if result.structured is None or result.exit_code != 0:
        logger.warn(
            "task.codex_fail",
            task_id=task.id,
            exit_code=result.exit_code,
            error=result.error,
        )
        _mark_blocked(
            state,
            task,
            reason=(result.error or f"codex exited {result.exit_code} with no structured result"),
        )
        state.in_flight_task = None
        return

    # v7.3 App Server lineage: capture the thread id returned by
    # app_server_invoke (either the fresh thread/start or the forked
    # thread/fork) so the NEXT task can fork from it. Only overwrite on
    # success — failed turns must not poison the next fork parent.
    if s.app_server and result.session_id:
        state.app_server_last_thread_id = result.session_id
        logger.info(
            "task.app_server_thread_captured",
            thread_id=result.session_id,
        )

    files_changed = result.structured.get("files_changed") or []
    summary_text = result.structured.get("summary") or "(no summary)"

    try:
        pio.update_task(s.team_name, task.id, status="completed")
    except Exception as e:
        logger.error("task.complete_fail", task_id=task.id, error=str(e))
        state.in_flight_task = None
        return

    try:
        pio.send_task_complete(
            s.team_name,
            s.agent_name,
            task_id=task.id,
            files_changed=files_changed,
            summary_text=summary_text,
            codex_exit_code=result.exit_code,
        )
    except Exception as e:
        logger.warn("task.complete_msg_fail", task_id=task.id, error=str(e))

    logger.info("task.completed", task_id=task.id, files=len(files_changed))
    state.in_flight_task = None


def _invoke_codex_for_task(state: LoopState, task):
    """Dispatch to the right Codex invocation shape for this task.

    Branch order (first match wins):
    1. `settings.app_server` → v7.1 App Server path (handles streaming
       + turn/steer via `_execute_task_app_server`).
    2. `state.codex_session_id is not None` → v7.2 resume path (no
       `--output-schema`, Python-side validation, retry once on
       schema failure).
    3. Otherwise → v7 fresh-exec path (schema-constrained at CLI layer).

    Returns `None` if the invocation crashed so fundamentally that
    `_mark_blocked` was called here; otherwise returns a `CodexResult`
    (possibly with `.structured is None` for schema failures that the
    caller will handle).
    """
    import json as _json
    from . import schema_validation as _sv

    s = state.settings

    if s.app_server:
        prompt = prompts_mod.v7_task_prompt(
            task,
            agent_name=s.agent_name,
            team_name=s.team_name,
            peer_prompt_fragments=_peer_prompt_fragments(state),
        )
        try:
            return _execute_task_app_server(state, task, prompt)
        except Exception as e:
            logger.error("task.codex_crash", task_id=task.id, error=str(e))
            _mark_blocked(state, task, reason=f"codex invocation crashed: {e}")
            return None

    # v7.2: resume path if we have a session to carry forward.
    if state.codex_session_id is not None:
        schema = _sv.load_schema(codex_mod.TASK_COMPLETE_SCHEMA)
        inline = _sv.inline_schema_prompt_fragment(schema)
        for attempt in (1, 2):
            prompt = prompts_mod.v7_task_prompt(
                task,
                agent_name=s.agent_name,
                team_name=s.team_name,
                peer_prompt_fragments=_peer_prompt_fragments(state),
            ) + "\n\n# Output contract (v7.2 resume)\n" + inline
            if attempt == 2:
                prompt += (
                    "\n\nPRIOR ATTEMPT FAILED: your previous response did "
                    "not match the schema above. Return ONLY the JSON object "
                    "with exactly the required fields. No markdown fences. "
                    "No prose before or after. No extra fields."
                )
            try:
                result = codex_mod.run(
                    prompt=prompt,
                    cwd=s.cwd,
                    schema=None,  # ignored on resume path; explicit for clarity
                    codex_binary=s.codex_binary,
                    extra_args=codex_mod.wrapper_mcp_config_args(
                        s.team_name, s.agent_name
                    ),
                    wrapper_identity=(s.team_name, s.agent_name),
                    resume_session_id=state.codex_session_id,
                    model=s.model,
                    effort=s.effort,
                    task_id=task.id,
                )
            except Exception as e:
                logger.error(
                    "task.codex_crash",
                    task_id=task.id,
                    error=str(e),
                    attempt=attempt,
                )
                _mark_blocked(state, task, reason=f"codex invocation crashed: {e}")
                return None

            if result.exit_code != 0:
                logger.warn(
                    "task.resume_nonzero",
                    task_id=task.id,
                    exit_code=result.exit_code,
                    attempt=attempt,
                )
                if attempt == 2:
                    return result  # let the caller mark it blocked
                continue

            parsed, err = _sv.parse_and_validate(result.last_message, schema)
            if err is None and parsed is not None:
                # Return a new CodexResult with `structured` filled in so
                # the caller's happy path works identically to the fresh-exec
                # schema-constrained path. Session id is preserved.
                return codex_mod.CodexResult(
                    exit_code=result.exit_code,
                    structured=parsed,
                    last_message=result.last_message,
                    events=result.events,
                    error=None,
                    tool_call_events=result.tool_call_events,
                    session_id=result.session_id or state.codex_session_id,
                )

            logger.warn(
                "task.resume_schema_fail",
                task_id=task.id,
                attempt=attempt,
                reason=err,
            )
            if attempt == 2:
                # Two failures: mark the task blocked with the reason.
                _mark_blocked(
                    state,
                    task,
                    reason=f"codex resume output failed schema after retry: {err}",
                )
                return None

    # v7 fresh-exec path (also: first task of an adapter's lifetime,
    # before we've captured a session id).
    prompt = prompts_mod.v7_task_prompt(
        task,
        agent_name=s.agent_name,
        team_name=s.team_name,
        peer_prompt_fragments=_peer_prompt_fragments(state),
    )
    try:
        return codex_mod.run(
            prompt=prompt,
            cwd=s.cwd,
            schema=codex_mod.TASK_COMPLETE_SCHEMA,
            codex_binary=s.codex_binary,
            extra_args=codex_mod.wrapper_mcp_config_args(s.team_name, s.agent_name),
            wrapper_identity=(s.team_name, s.agent_name),
            model=s.model,
            effort=s.effort,
            task_id=task.id,
        )
    except Exception as e:
        logger.error("task.codex_crash", task_id=task.id, error=str(e))
        _mark_blocked(state, task, reason=f"codex invocation crashed: {e}")
        return None


def _execute_task_app_server(state: LoopState, task, prompt: str):
    """v7.1 path: run the task via `codex app-server`, with a mid-turn hook
    that drains our own inbox and forwards prose messages to Codex via
    `turn/steer`.

    Returns a `codex.CodexResult` in the same shape as the v7 exec path
    so the surrounding control flow in `_execute_task` is uniform.
    """
    from . import schema_validation as _sv

    s = state.settings

    # Load the task-complete schema as a JSON dict (App Server wants it
    # inline in `turn/start` params, not as a file path).
    schema = _sv.load_schema(codex_mod.TASK_COMPLETE_SCHEMA)

    recipient_capabilities = _backend_metadata(s).capabilities
    steer_queue = codex_mod.SteerQueue(
        capabilities=recipient_capabilities,
        # 09 R15-vis-followup: pass team + agent so SteerQueue.push can emit
        # visibility_degraded(surface=peer_steer_rejected) per 08 CD-6 / 07 §6.5.
        team=s.team_name,
        agent=s.agent_name,
    )
    sampled_task_events = 0
    deferred_prose_messages: list[Any] = []

    def _mid_turn_hook() -> None:
        # Drain own inbox. Lead prose and peer prose explicitly accepted by
        # this recipient become steer fragments; other peer prose is deferred
        # to the normal conversational handler after the task turn completes.
        # Shutdown requests are snapshotted for the outer loop to handle after
        # the turn completes. Ignore everything else.
        try:
            messages = pio.read_own_inbox(s.team_name, s.agent_name, s.agent_name)
        except Exception:
            return
        for m in messages:
            payload = __import__(
                "claude_anyteam.messages", fromlist=["parse_protocol_text"]
            ).parse_protocol_text(m.text)
            if payload is None:
                sender = getattr(m, "from_", None)
                if not _mid_turn_prose_should_be_steer(
                    sender=sender,
                    recipient_capabilities=recipient_capabilities,
                ):
                    deferred_prose_messages.append(m)
                    logger.info(
                        "task.mid_turn_prose_deferred",
                        from_=sender,
                        text_head=m.text[:120],
                    )
                    continue
                accepted = steer_queue.push(
                    f"mid-task message from {sender}: {m.text}",
                    sender=sender,
                )
                if accepted:
                    logger.info(
                        "task.steer_queued",
                        from_=sender,
                        text_head=m.text[:120],
                    )
            elif isinstance(payload, SteerIn):
                sender = getattr(m, "from_", None) or payload.from_
                message = (
                    payload.message.strip()
                    if isinstance(payload.message, str)
                    else ""
                )
                if not message:
                    logger.warn(
                        "app_server.steer.rejected",
                        sender=sender,
                        reason="empty_message",
                    )
                    continue
                accepted = steer_queue.push(message, sender=sender)
                if accepted:
                    logger.info(
                        "task.steer_queued",
                        from_=sender,
                        text_head=message[:120],
                    )
            elif isinstance(payload, ShutdownRequestIn):
                # Reuse the normal shutdown handler so mid-task requests get
                # the same immediate reject+feedback response, idempotency,
                # and post-task honor semantics as the idle path.
                _handle_shutdown(state, payload)
                logger.info(
                    "task.steer_saw_shutdown", request_id=payload.effective_request_id()
                )

    def _visibility_event_sink(event) -> None:
        """R16 fan-out for Codex App Server events.

        The event log is the canonical substrate. Mailbox/task-state updates
        are low-volume projections driven by the envelope's `visibility`
        flags (B9 §6.2-§6.4); backend-native names remain in payload fields.
        """

        nonlocal sampled_task_events
        visibility = getattr(event, "visibility", None)
        task_state_requested = getattr(visibility, "task_state", False)
        payload = getattr(event, "payload", {}) or {}
        if (
            not task_state_requested
            and getattr(event, "kind", None) == "tool_event"
            and payload.get("category") == "host_tool"
            and payload.get("phase") == "completed"
            and payload.get("status", "success") == "success"
        ):
            sampled_task_events += 1
            task_state_requested = (
                sampled_task_events == 1
                or sampled_task_events % APP_SERVER_TASK_STATE_SAMPLE_EVERY == 0
            )

        if task_state_requested and not getattr(visibility, "task_state", False):
            event = event.model_copy(
                update={
                    "visibility": event.visibility.model_copy(
                        update={"task_state": True}
                    )
                }
            )
            visibility = getattr(event, "visibility", None)

        try:
            pio.append_event(s.team_name, s.agent_name, event)
        except Exception as e:
            logger.warn(
                "visibility.append_fail",
                task_id=getattr(task, "id", None),
                kind=getattr(event, "kind", None),
                error=str(e),
            )
        if task_state_requested:
            try:
                active_form = event.summary[:120]
                if (
                    getattr(event, "kind", None) == "turn_progress"
                    and payload.get("risk") == "timeout_possible"
                    and payload.get("action_taken") == "turn_steer_sent"
                ):
                    active_form = f"running codex: {event.summary}"[:120]
                pio.update_task(
                    s.team_name,
                    task.id,
                    active_form=active_form,
                    metadata={
                        "visibility": {
                            "last_event_id": event.event_id,
                            "last_kind": event.kind,
                            "last_summary": event.summary,
                            "last_payload": event.payload,
                        }
                    },
                )
            except Exception as e:
                logger.warn(
                    "visibility.task_state_fail",
                    task_id=getattr(task, "id", None),
                    kind=getattr(event, "kind", None),
                    error=str(e),
                )
        if getattr(visibility, "mailbox", False):
            try:
                pio.send_visibility_event_to_lead(
                    s.team_name,
                    s.agent_name,
                    event,
                    summary=event.summary[:120],
                )
            except Exception as e:
                logger.warn(
                    "visibility.mailbox_fail",
                    task_id=getattr(task, "id", None),
                    kind=getattr(event, "kind", None),
                    error=str(e),
                )

    result = codex_mod.app_server_invoke(
        task_prompt=prompt,
        cwd=s.cwd,
        schema=schema,
        settings_team=s.team_name,
        settings_agent=s.agent_name,
        codex_binary=s.codex_binary,
        steer_queue=steer_queue,
        mid_turn_hook=_mid_turn_hook,
        model=s.model,
        effort=s.effort,
        overall_timeout_s=s.turn_timeout_s,
        non_progress_warn_s=s.non_progress_warn_s,
        non_progress_interrupt_s=s.non_progress_interrupt_s,
        resume_thread_id=state.app_server_last_thread_id,
        task_id=str(task.id),
        event_sink=_visibility_event_sink,
    )
    for msg in deferred_prose_messages:
        try:
            _handle_prose(state, msg)
        except Exception as e:
            logger.warn(
                "task.mid_turn_prose_deferred_fail",
                from_=getattr(msg, "from_", None),
                error=str(e),
            )
    return result


def _mark_blocked(state: LoopState, task, reason: str) -> None:
    """On Codex failure: set activeForm to indicate blocking, annotate metadata,
    and notify the lead. Task stays `in_progress` so the lead can see it needs
    attention without reclaim logic.

    If another codepath has already marked the task `completed` (e.g., a
    plan-mode retry succeeded after this failure was scheduled but before
    it executed — the race reviewer observed on task #13), skip the
    mutation and the blocked-message so we don't overwrite legitimate
    completion state. Defensive re-read to catch the race as late as possible.
    """
    s = state.settings
    try:
        current = pio.get_task(s.team_name, task.id)
        if getattr(current, "status", None) == "completed":
            logger.info(
                "task.block_skip_already_completed",
                task_id=task.id,
                reason_would_have_been=reason[:120],
            )
            return
    except Exception as e:
        logger.warn("task.block_precheck_fail", task_id=task.id, error=str(e))
        # Fall through to the mutation; precheck failing is not reason enough
        # to silently abandon a real failure report.

    try:
        pio.update_task(
            s.team_name,
            task.id,
            active_form=f"blocked: {reason[:80]}",
            metadata={"blocked_reason": reason, "blocked_by": s.agent_name},
        )
    except Exception as e:
        logger.warn("task.block_update_fail", task_id=task.id, error=str(e))

    try:
        pio.send_task_blocked(
            s.team_name,
            s.agent_name,
            task_id=task.id,
            reason=reason,
        )
    except Exception as e:
        logger.warn("task.block_msg_fail", task_id=task.id, error=str(e))
