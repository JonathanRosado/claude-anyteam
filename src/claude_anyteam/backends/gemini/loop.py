"""Control loop for Gemini-backed teammates.

This intentionally mirrors the Codex control loop at the protocol boundary but
uses one-shot Gemini CLI headless invocations. There is no Codex app-server /
turn-steer equivalent in this Plan A loop.
"""
from __future__ import annotations

import json
import os
import signal
import time
from dataclasses import dataclass, field
from typing import Any

from claude_anyteam import logger, protocol_io as pio
from claude_anyteam.capability_manifest import CapabilityManifestCache
from claude_anyteam.capabilities import (
    GEMINI_ACP_CAPABILITIES,
    GEMINI_HEADLESS_CAPABILITIES,
    assert_known_capabilities,
    rich_capability_manifest,
)
from claude_anyteam.messages import CapabilityManifestUpdatedIn, PlanApprovalRequestIn, ShutdownRequestIn, SteerIn, parse_protocol_text
from claude_anyteam.registration import BackendMetadata, deregister, register
from claude_anyteam.schema_validation import inline_schema_prompt_fragment, load_schema

from . import acp as acp_invoke, crash_hygiene, invoke as headless_invoke, prompts
from .config import GeminiSettings

# Backwards-compatible alias for tests/extensions that monkeypatch loop.invoke.
invoke = headless_invoke


@dataclass
class QueuedSteer:
    steer_id: str
    message: str
    task_id: str | None = None
    priority: str = "normal"
    expires_after_turns: int = 1


@dataclass
class GeminiLoopState:
    settings: GeminiSettings
    shutdown_requested: bool = False
    approved_shutdown: bool = False
    in_flight_task: str | None = None
    seen_shutdown_request_ids: set[str] = field(default_factory=set)
    gemini_session_id: str | None = None
    queued_steers: list[QueuedSteer] = field(default_factory=list)
    peer_manifest_cache: CapabilityManifestCache | None = None
    self_capability_manifest: dict[str, Any] | None = None


def _backend_metadata(settings: GeminiSettings) -> BackendMetadata:
    """Registration metadata for Gemini ACP/headless variants.

    Per 09 R11 and 08 §6.3, this is the cheap roster declaration only;
    rich invocation semantics live in the later Agent Card manifest layer.
    """
    capabilities = (
        GEMINI_ACP_CAPABILITIES
        if settings.backend == "acp"
        else GEMINI_HEADLESS_CAPABILITIES
    )
    capabilities = assert_known_capabilities(capabilities)
    manifest_kwargs = (
        {
            "delivery_mode": "live",
            "expiry_semantics": "session_managed",
            "steer_authorization": "any_peer",
            "host_tool_surface": "mcp_anyteam",
        }
        if settings.backend == "acp"
        else {"host_tool_surface": "mcp_anyteam"}
    )
    return BackendMetadata(
        model="gemini-cli",
        prompt=(
            "Gemini teammate adapter. Protocol I/O is handled by the adapter; "
            "coding work is delegated to Gemini CLI headless mode. No Claude LLM is involved."
        ),
        capabilities=capabilities,
        capability_manifest=rich_capability_manifest(capabilities, **manifest_kwargs),
        transport=f"gemini-{settings.backend}",
        host_tool_surface="mcp_anyteam",
        coupling_regime="tight" if settings.backend == "acp" else "loose",
    )


def run(settings: GeminiSettings) -> int:
    state: GeminiLoopState | None = None
    gemini_home = settings.gemini_home or headless_invoke._default_gemini_home(settings.team_name, settings.agent_name)
    exit_code = 0
    startup_phase = "feature_test"
    startup_complete = False
    try:
        _backend_feature_test(settings)
        if settings.backend == "acp":
            startup_phase = "startup_recovery"
            headless_invoke.ensure_adapter_state(gemini_home)
            previous_state = headless_invoke.read_adapter_state(gemini_home)
            crash_hygiene.run_startup_recovery(
                gemini_home=gemini_home,
                team=settings.team_name,
                agent=settings.agent_name,
                cwd=settings.cwd,
                gemini_binary=settings.gemini_binary,
                state=previous_state,
            )
            crash_hygiene.mark_adapter_start(
                gemini_home,
                team=settings.team_name,
                agent=settings.agent_name,
                cwd=settings.cwd,
            )
        startup_phase = "registration"
        register(settings, _backend_metadata(settings))
        startup_phase = "capability_manifest_load"
        state = GeminiLoopState(settings=settings)
        state.self_capability_manifest = pio.read_agent_manifest(
            settings.team_name,
            settings.agent_name,
        )
        state.peer_manifest_cache = CapabilityManifestCache(
            settings.team_name,
            self_name=settings.agent_name,
        )
        state.peer_manifest_cache.load_startup()

        def _sig_handler(signum: int, _frame: Any) -> None:
            logger.warn("gemini.signal.received", signum=signum)
            assert state is not None
            state.shutdown_requested = True
            if settings.backend == "acp":
                acp_invoke.terminate_active_acp_children(signum=signum, reason="adapter_signal")

        startup_phase = "signal_setup"
        signal.signal(signal.SIGINT, _sig_handler)
        signal.signal(signal.SIGTERM, _sig_handler)

        startup_complete = True
        _main_loop(state)
    except Exception as e:
        if startup_complete:
            logger.error("gemini.loop.crash", error=str(e))
            error_class = "adapter_crash"
        else:
            logger.error(
                "gemini.startup.crash",
                phase=startup_phase,
                backend=settings.backend,
                error=str(e),
            )
            error_class = "adapter_startup_crash"
            # Startup failures happen before the first inbox poll/turn, so
            # fan out an envelope directly instead of relying on task/prose
            # fallbacks that never run.
            try:
                pio.emit_adapter_startup_crash(
                    team=settings.team_name,
                    agent=settings.agent_name,
                    backend="gemini",
                    phase=startup_phase,
                    error=e,
                    payload={
                        "transport": settings.backend,
                        "gemini_binary": settings.gemini_binary,
                        "gemini_home": str(gemini_home),
                        "cwd": str(settings.cwd),
                        "model": settings.model,
                        "effort": settings.effort,
                    },
                )
            except Exception as emit_exc:
                logger.debug("gemini.startup.visibility_emit_failed", error=str(emit_exc))
        # Persist a structured incident so the lead can find this via
        # `claude-anyteam diagnose`. Particularly important for Gemini
        # cold-start failures where the adapter exits before its first
        # inbox poll — without this, the lead has no signal beyond a
        # tmux-pane stderr they may not be reading. (See bug-triage
        # report: B8 Gemini cold-start failure.)
        try:
            from claude_anyteam import diagnostics as _diag
            _diag.record_incident(
                team=settings.team_name,
                agent=settings.agent_name,
                backend="gemini",
                error_class=error_class,
                summary=str(e),
            )
        except Exception:
            pass
        exit_code = 1
    finally:
        if state is not None and state.approved_shutdown:
            deregister(settings)
            logger.info("gemini.loop.deregistered", name=settings.agent_name)
        else:
            logger.warn(
                "gemini.loop.exit_without_deregister",
                in_flight_task=(state.in_flight_task if state is not None else None),
            )
        if settings.backend == "acp" and exit_code == 0:
            crash_hygiene.mark_clean_shutdown(gemini_home)
    return exit_code


def _backend_feature_test(settings: GeminiSettings) -> None:
    if settings.backend == "acp":
        acp_invoke.feature_test(settings.gemini_binary)
    else:
        headless_invoke.feature_test(settings.gemini_binary)


def _backend_run(
    state: GeminiLoopState,
    prompt: str,
    *,
    schema=None,
    resume_session_id: str | None = None,
    ephemeral: bool = False,
    task_id: str | None = None,
):
    s = state.settings
    runner = acp_invoke if s.backend == "acp" else headless_invoke
    kwargs = {
        "cwd": s.cwd,
        "schema": schema,
        "gemini_binary": s.gemini_binary,
        "wrapper_identity": (s.team_name, s.agent_name),
        "model": s.model,
        "effort": s.effort,
        "gemini_home": s.gemini_home,
    }
    if s.backend == "acp":
        # ACP subprocesses are created and cleaned up per invocation in acp.run();
        # the loop only tracks durable Gemini session ids across tasks.
        kwargs["resume_session_id"] = None if ephemeral else resume_session_id
        kwargs["ephemeral"] = ephemeral
        kwargs["trust_mode"] = s.trust_mode
        kwargs["task_id"] = task_id
    else:
        kwargs["resume_session_id"] = resume_session_id
        kwargs["task_id"] = task_id
    return runner.run(prompt, **kwargs)


def _main_loop(state: GeminiLoopState) -> None:
    s = state.settings
    logger.info("gemini.loop.start", team=s.team_name, name=s.agent_name, poll_s=s.poll_interval_s)
    idle_last_sent_at: float | None = None
    while not state.approved_shutdown:
        messages = pio.read_own_inbox(s.team_name, s.agent_name, s.agent_name)
        for m in messages:
            _handle_message(state, m)
            if state.approved_shutdown:
                return

        if not state.shutdown_requested:
            claimed = _find_and_claim(state)
            if claimed is not None:
                _execute_task(state, claimed)
                idle_last_sent_at = None
                continue

        if not _has_claimable(state):
            now = time.monotonic()
            if idle_last_sent_at is None or (now - idle_last_sent_at) > 60:
                try:
                    pio.send_idle_notification(s.team_name, s.agent_name)
                    idle_last_sent_at = now
                except Exception as e:
                    logger.warn("gemini.idle.send_fail", error=str(e))
        time.sleep(s.poll_interval_s)
        if state.shutdown_requested and state.in_flight_task is None:
            state.gemini_session_id = None
            state.approved_shutdown = True
            return


def _handle_message(state: GeminiLoopState, msg: Any) -> None:
    payload = parse_protocol_text(msg.text)
    if payload is None:
        _handle_prose(state, msg)
    elif isinstance(payload, ShutdownRequestIn):
        _handle_shutdown(state, payload)
    elif isinstance(payload, PlanApprovalRequestIn):
        _handle_plan_approval(state, payload)
    elif isinstance(payload, CapabilityManifestUpdatedIn):
        if state.peer_manifest_cache is not None:
            state.peer_manifest_cache.apply_update(payload)
        logger.info(
            "gemini.capability_manifest.update_seen",
            agent=payload.agent_name,
            capability_version=payload.capability_version,
            removed=payload.removed,
        )
    elif isinstance(payload, SteerIn):
        _handle_steer(state, payload, msg)
    else:
        logger.debug("gemini.inbox.protocol_noop", type=payload.__class__.__name__)


def _peer_prompt_fragments(state: GeminiLoopState) -> str:
    """Return cached R14 peer capability prompt fragments for this turn.

    Honors the S10a ablation knob ``CLAUDE_ANYTEAM_DISABLE_PEER_PROMPT_FRAGMENTS=1``
    per S10-ablation-implementation-spec.md §2.
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
        logger.warn("gemini.capability_manifest.peer_prompt_fragments_fail", error=str(e))
        return ""


MAX_STEER_PREFIX_CHARS = 8192


def _handle_steer(state: GeminiLoopState, payload: SteerIn, msg: Any) -> None:
    sender = getattr(msg, "from_", None) or payload.from_
    capabilities = _backend_metadata(state.settings).capabilities
    if sender != "team-lead" and "accepts_peer_steer" not in capabilities:
        logger.warn(
            "gemini.steer.rejected",
            sender=sender,
            reason="not_team_lead_and_capability_not_declared",
        )
        # 09 R15-vis-followup (08 CD-6 / 07 §6.5): emit visibility_degraded
        # to lead's mailbox + event log so the rejection is observable
        # without stderr scrape (§2 anti-pattern closure).
        try:
            pio.emit_peer_steer_rejection(
                team=state.settings.team_name,
                agent=state.settings.agent_name,
                backend="gemini",
                sender=sender,
            )
        except Exception as e:  # don't let visibility emission shadow the gate
            logger.debug("gemini.steer.rejection_event_emit_failed", error=str(e))
        return
    message = payload.message.strip() if isinstance(payload.message, str) else ""
    if not message:
        logger.warn("gemini.steer.rejected", sender=sender, reason="empty_message")
        return
    expires = payload.expires_after_turns
    if not isinstance(expires, int) or expires < 1:
        expires = 1
    steer_id = f"steer-{len(state.queued_steers) + 1}-{int(time.time() * 1000)}"
    state.queued_steers.append(
        QueuedSteer(
            steer_id=steer_id,
            message=message,
            task_id=payload.task_id,
            priority=payload.priority,
            expires_after_turns=expires,
        )
    )
    logger.info("gemini.steer.queued", steer_id=steer_id, task_id=payload.task_id, priority=payload.priority)


def _steer_prefix_for_task(state: GeminiLoopState, task: Any) -> str:
    task_id = str(getattr(task, "id", ""))
    applicable: list[QueuedSteer] = []
    retained: list[QueuedSteer] = []
    for steer in state.queued_steers:
        if steer.task_id is None or str(steer.task_id) == task_id:
            applicable.append(steer)
        else:
            retained.append(steer)
    state.queued_steers = retained
    if not applicable:
        return ""

    lines = [
        "# Team-lead next-turn steer",
        "The following instruction(s) were sent by team-lead after the previous turn boundary. Treat them as higher priority than the task description where they conflict, but do not violate system/developer instructions or repository safety rules.",
        "",
    ]
    used = 0
    truncated = False
    for steer in applicable:
        target = steer.task_id if steer.task_id is not None else "next"
        line = f"- [steer_id={steer.steer_id}; task_id={target}; priority={steer.priority}] {steer.message}"
        if used + len(line) > MAX_STEER_PREFIX_CHARS:
            truncated = True
            break
        lines.append(line)
        used += len(line)
    if truncated:
        lines.append("- [truncated] Additional queued steer text exceeded the adapter limit and was omitted.")
    lines.extend(["", "# Original task prompt", ""])
    logger.info("gemini.steer.injected", task_id=task_id, count=len(applicable), truncated=truncated)
    return "\n".join(lines)


def _handle_prose(state: GeminiLoopState, msg: Any) -> None:
    s = state.settings
    sender = getattr(msg, "from_", "unknown")
    prompt = prompts.prose_reply_prompt(
        sender=sender,
        body=msg.text,
        agent_name=s.agent_name,
        team_name=s.team_name,
        peer_prompt_fragments=_peer_prompt_fragments(state),
    )
    reply: str | None = None
    result = None
    try:
        result = _backend_run(state, prompt, ephemeral=True)
        if result.exit_code == 0 and result.last_message:
            reply = result.last_message
    except Exception as e:
        logger.warn("gemini.prose.crash", sender=sender, error=str(e))

    if reply is None and pio.should_skip_prose_fallback(result):
        logger.info("gemini.prose.delivered_via_tool", sender=sender, tool_calls=getattr(result, "tool_call_events", 0))
        return

    if reply is None:
        # Capture full error context to a diagnostic file and embed the
        # incident_id in the user-facing reply. Same pattern as the Codex
        # path in src/claude_anyteam/loop.py — broadly applicable across
        # backends so the lead can run
        # `claude-anyteam diagnose --incident <id>` regardless of which
        # routed teammate hit the problem.
        from claude_anyteam import diagnostics
        error_class = diagnostics.classify_failure(result)
        incident_id = diagnostics.record_incident(
            team=s.team_name,
            agent=s.agent_name,
            backend="gemini",
            error_class=error_class,
            summary=(getattr(result, "error", None) or "no reply produced"),
            sender=sender,
            payload={
                "exit_code": (result.exit_code if result is not None else None),
                "tool_call_events": (getattr(result, "tool_call_events", 0) if result is not None else 0),
                "error": (getattr(result, "error", None) if result is not None else None),
            },
        )
        reply = diagnostics.fallback_message(
            backend="gemini",
            incident_id=incident_id,
            error_class=error_class,
        )
    try:
        pio.send_prose(s.team_name, s.agent_name, sender, reply, summary="prose_reply")
    except Exception as e:
        logger.warn("gemini.prose.reply_send_fail", sender=sender, error=str(e))


def _handle_shutdown(state: GeminiLoopState, payload: ShutdownRequestIn) -> None:
    s = state.settings
    req_id = payload.effective_request_id() or "shutdown-unknown"
    if req_id in state.seen_shutdown_request_ids:
        return
    state.seen_shutdown_request_ids.add(req_id)
    if state.in_flight_task is not None:
        reason = f"in-flight task #{state.in_flight_task}"
        try:
            pio.send_shutdown_rejected(s.team_name, s.agent_name, req_id, reason=reason)
        except Exception as e:
            logger.warn("gemini.shutdown.response_fail", error=str(e))
        state.shutdown_requested = True
        return
    try:
        pio.send_shutdown_approved(s.team_name, s.agent_name, req_id)
    except Exception as e:
        logger.warn("gemini.shutdown.response_fail", error=str(e))
    state.gemini_session_id = None
    state.queued_steers.clear()
    state.approved_shutdown = True


def _handle_plan_approval(state: GeminiLoopState, payload: PlanApprovalRequestIn) -> None:
    s = state.settings
    if not s.plan_mode_required:
        logger.warn("gemini.plan.unexpected_request", request_id=payload.request_id)
        return
    req_id = payload.request_id
    if req_id is None:
        logger.warn("gemini.plan.missing_request_id")
        return

    target = _target_task_for_plan(state, payload)
    if target is None:
        logger.warn("gemini.plan.no_target_task", request_id=req_id)
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
            logger.warn("gemini.plan.block_msg_fail", error=str(e))
        return

    logger.info("gemini.plan.request_received", request_id=req_id, task_id=target.id)

    for attempt in (1, 2):
        schema = load_schema(headless_invoke.PLAN_SCHEMA)
        prompt = prompts.plan_prompt(target, tighten=attempt == 2, agent_name=s.agent_name, team_name=s.team_name)
        prompt += "\n\n# Output contract\n" + inline_schema_prompt_fragment(schema)
        try:
            result = _backend_run(state, prompt, schema=headless_invoke.PLAN_SCHEMA, ephemeral=True)
        except Exception as e:
            logger.error("gemini.plan.crash", task_id=target.id, error=str(e))
            result = None

        if result is not None and result.exit_code == 0 and result.structured is not None:
            try:
                pio.send_plan_approval_request(s.team_name, s.agent_name, request_id=req_id, plan=result.structured)
                logger.info(
                    "gemini.plan.sent",
                    request_id=req_id,
                    task_id=target.id,
                    steps=len(result.structured.get("steps", [])),
                )
            except Exception as e:
                logger.warn("gemini.plan.send_fail", error=str(e))
            return

        if result is None:
            logger.warn("gemini.plan.attempt_failed", attempt=attempt, task_id=target.id)
        else:
            logger.warn(
                "gemini.plan.gemini_fail",
                task_id=target.id,
                exit_code=result.exit_code,
                error=result.error,
            )
            logger.warn("gemini.plan.attempt_failed", attempt=attempt, task_id=target.id)

    _mark_blocked(
        state,
        target,
        "Gemini plan generation failed schema validation twice",
    )


def _target_task_for_plan(state: GeminiLoopState, payload: PlanApprovalRequestIn):
    s = state.settings
    try:
        all_tasks = pio.list_tasks(s.team_name)
    except Exception as e:
        logger.warn("gemini.plan.task_list_fail", error=str(e))
        return None
    by_id = {t.id: t for t in all_tasks}
    if payload.task_id and payload.task_id in by_id:
        return by_id[payload.task_id]

    if state.in_flight_task and state.in_flight_task in by_id:
        return by_id[state.in_flight_task]

    for t in sorted((t for t in all_tasks if t.owner == s.agent_name and t.status == "pending" and not _blocked(all_tasks, t)), key=lambda x: int(x.id)):
        return t

    for t in sorted((t for t in all_tasks if t.owner in (None, "") and t.status == "pending" and not _blocked(all_tasks, t)), key=lambda x: int(x.id)):
        return t
    return None


def _find_and_claim(state: GeminiLoopState):
    s = state.settings
    try:
        all_tasks = pio.list_tasks(s.team_name)
    except Exception as e:
        logger.warn("gemini.tasks.list_fail", error=str(e))
        return None
    candidates = [t for t in all_tasks if t.status == "pending" and not _blocked(all_tasks, t) and t.owner == s.agent_name]
    candidates += [t for t in all_tasks if t.status == "pending" and not _blocked(all_tasks, t) and t.owner in (None, "")]
    for t in sorted(candidates, key=lambda x: int(x.id)):
        try:
            claimed = pio.claim_task(s.team_name, t.id, s.agent_name, active_form=f"Running gemini on task #{t.id}")
            state.in_flight_task = claimed.id
            try:
                pio.emit_coupling_conflict_if_needed(
                    team=s.team_name,
                    agent=s.agent_name,
                    backend="gemini_acp" if s.backend == "acp" else "gemini_headless",
                    task=claimed,
                    manifest=state.self_capability_manifest,
                )
            except Exception as e:
                logger.warn(
                    "gemini.task.coupling_conflict_visibility_failed",
                    task_id=claimed.id,
                    error=str(e),
                )
            return claimed
        except ValueError:
            continue
    return None


def _has_claimable(state: GeminiLoopState) -> bool:
    try:
        all_tasks = pio.list_tasks(state.settings.team_name)
    except Exception:
        return False
    return any(
        t.status == "pending"
        and not _blocked(all_tasks, t)
        and t.owner in (None, "", state.settings.agent_name)
        for t in all_tasks
    )


def _blocked(all_tasks: list, t) -> bool:
    if not getattr(t, "blocked_by", None):
        return False
    by_id = {x.id: x for x in all_tasks}
    return any((by_id.get(bid) is not None and by_id[bid].status not in ("completed", "deleted")) for bid in t.blocked_by)


def _is_permission_blocked(result: Any) -> bool:
    return any(
        isinstance(ev, dict) and ev.get("type") == "permission_blocked"
        for ev in getattr(result, "events", []) or []
    )


def _should_drop_session_after_failure(result: Any) -> bool:
    if getattr(result, "exit_code", None) == 124:
        return True
    error = getattr(result, "error", None)
    return isinstance(error, str) and "stopReason 'cancelled'" in error


def _execute_task(state: GeminiLoopState, task) -> None:
    s = state.settings
    schema = load_schema(headless_invoke.TASK_COMPLETE_SCHEMA)
    result = None
    for attempt in (1, 2):
        prompt = prompts.task_prompt(
            task,
            agent_name=s.agent_name,
            team_name=s.team_name,
            peer_prompt_fragments=_peer_prompt_fragments(state),
        )
        steer_prefix = _steer_prefix_for_task(state, task) if attempt == 1 else ""
        if steer_prefix:
            prompt = steer_prefix + prompt
        prompt += "\n\n# Output contract\n" + inline_schema_prompt_fragment(schema)
        if attempt == 2:
            prompt += "\n\nPRIOR ATTEMPT FAILED: return ONLY the JSON object matching the schema."
        result = _backend_run(state, prompt, schema=headless_invoke.TASK_COMPLETE_SCHEMA, resume_session_id=state.gemini_session_id, ephemeral=False, task_id=str(task.id))
        if result.exit_code == 0 and result.structured is not None:
            if result.session_id:
                state.gemini_session_id = result.session_id
            break
        if _is_permission_blocked(result):
            logger.warn(
                "gemini.task.permission_blocked",
                task_id=task.id,
                session_id=state.gemini_session_id or result.session_id,
                error=result.error,
            )
            state.gemini_session_id = None
            break
        if _should_drop_session_after_failure(result):
            logger.warn(
                "gemini.task.session_dropped_after_cancel",
                task_id=task.id,
                session_id=state.gemini_session_id or result.session_id,
                exit_code=result.exit_code,
                error=result.error,
            )
            state.gemini_session_id = None
            break
        if result.session_id:
            state.gemini_session_id = result.session_id
    if result is None or result.exit_code != 0 or result.structured is None:
        _mark_blocked(state, task, result.error if result else "Gemini invocation did not run")
        state.in_flight_task = None
        return
    files_changed = result.structured.get("files_changed") or []
    summary_text = result.structured.get("summary") or "(no summary)"
    try:
        pio.update_task(s.team_name, task.id, status="completed")
        # Backwards-compatible protocol field name; see limitations doc.
        pio.send_task_complete(s.team_name, s.agent_name, task_id=task.id, files_changed=files_changed, summary_text=summary_text, codex_exit_code=result.exit_code)
    except Exception as e:
        logger.warn("gemini.task.complete_fail", task_id=task.id, error=str(e))
    state.in_flight_task = None


def _mark_blocked(state: GeminiLoopState, task, reason: str) -> None:
    s = state.settings
    try:
        pio.update_task(s.team_name, task.id, active_form=f"blocked: {reason[:80]}", metadata={"blocked_reason": reason, "blocked_by": s.agent_name})
        pio.send_task_blocked(s.team_name, s.agent_name, task_id=task.id, reason=reason)
    except Exception as e:
        logger.warn("gemini.task.block_fail", task_id=task.id, error=str(e))
