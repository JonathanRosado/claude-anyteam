"""Control loop for Kimi-backed teammates.

This intentionally mirrors the Codex control loop at the protocol boundary but
uses one-shot Kimi CLI headless invocations. There is no Codex app-server /
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
from claude_anyteam.capabilities import KIMI_HEADLESS_CAPABILITIES, assert_known_capabilities, rich_capability_manifest
from claude_anyteam.messages import CapabilityManifestUpdatedIn, PlanApprovalRequestIn, ShutdownRequestIn, SteerIn, parse_protocol_text
from claude_anyteam.registration import BackendMetadata, deregister, register
from claude_anyteam.schema_validation import inline_schema_prompt_fragment, load_schema

from . import invoke as headless_invoke, prompts
from .config import KimiSettings

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
class KimiLoopState:
    settings: KimiSettings
    shutdown_requested: bool = False
    approved_shutdown: bool = False
    in_flight_task: str | None = None
    seen_shutdown_request_ids: set[str] = field(default_factory=set)
    kimi_session_id: str | None = None
    queued_steers: list[QueuedSteer] = field(default_factory=list)
    peer_manifest_cache: CapabilityManifestCache | None = None
    self_capability_manifest: dict[str, Any] | None = None


def _backend_metadata(settings: KimiSettings) -> BackendMetadata:
    """Registration metadata for Kimi headless v1.

    09 R11 adds the 08 §6.3 Agent Card-derived cheap roster flags; rich
    manifest details remain deferred to the wrapper-MCP capability layer.
    """
    capabilities = assert_known_capabilities(KIMI_HEADLESS_CAPABILITIES)
    return BackendMetadata(
        model="kimi-cli",
        prompt=(
            "Kimi teammate adapter. Protocol I/O is handled by the adapter; "
            "coding work is delegated to Kimi CLI headless mode. No Claude LLM is involved."
        ),
        capabilities=capabilities,
        capability_manifest=rich_capability_manifest(
            capabilities,
            host_tool_surface="kimi-native",
        ),
        transport="kimi-headless",
        host_tool_surface="kimi-native",
        coupling_regime="loose",
    )


def run(settings: KimiSettings) -> int:
    state: KimiLoopState | None = None
    exit_code = 0
    startup_phase = "feature_test"
    startup_complete = False
    try:
        _backend_feature_test(settings)
        startup_phase = "registration"
        register(settings, _backend_metadata(settings))
        startup_phase = "capability_manifest_load"
        state = KimiLoopState(settings=settings)
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
            logger.warn("kimi.signal.received", signum=signum)
            assert state is not None
            state.shutdown_requested = True

        startup_phase = "signal_setup"
        signal.signal(signal.SIGINT, _sig_handler)
        signal.signal(signal.SIGTERM, _sig_handler)

        startup_complete = True
        _main_loop(state)
    except Exception as e:
        if startup_complete:
            logger.error("kimi.loop.crash", error=str(e))
            error_class = "adapter_crash"
        else:
            logger.error("kimi.startup.crash", phase=startup_phase, error=str(e))
            error_class = "adapter_startup_crash"
            # Startup failures happen before the first inbox poll/turn, so
            # fan out an envelope directly instead of relying on task/prose
            # fallbacks that never run.
            try:
                pio.emit_adapter_startup_crash(
                    team=settings.team_name,
                    agent=settings.agent_name,
                    backend="kimi",
                    phase=startup_phase,
                    error=e,
                    payload={
                        "transport": settings.backend,
                        "kimi_binary": settings.kimi_binary,
                        "kimi_home": str(settings.kimi_home) if settings.kimi_home else None,
                        "cwd": str(settings.cwd),
                        "model": settings.model,
                        "effort": settings.effort,
                        "thinking": settings.thinking,
                    },
                )
            except Exception as emit_exc:
                logger.debug("kimi.startup.visibility_emit_failed", error=str(emit_exc))
        # Persist a structured incident so the lead can find this via
        # `claude-anyteam diagnose`. Same crash-hygiene pattern as
        # Codex/Gemini — adapter crashes that would otherwise only
        # appear in tmux-pane stderr now leave a durable artifact.
        try:
            from claude_anyteam import diagnostics as _diag
            _diag.record_incident(
                team=settings.team_name,
                agent=settings.agent_name,
                backend="kimi",
                error_class=error_class,
                summary=str(e),
            )
        except Exception:
            pass
        exit_code = 1
    finally:
        if state is not None and state.approved_shutdown:
            deregister(settings)
            logger.info("kimi.loop.deregistered", name=settings.agent_name)
        else:
            logger.warn(
                "kimi.loop.exit_without_deregister",
                in_flight_task=(state.in_flight_task if state is not None else None),
            )
    return exit_code


def _backend_feature_test(settings: KimiSettings) -> None:
    if settings.backend == "acp":
        raise NotImplementedError("Plan B deferred to follow-up PR")
    headless_invoke.feature_test(settings.kimi_binary)


def _backend_run(
    state: KimiLoopState,
    prompt: str,
    *,
    schema=None,
    resume_session_id: str | None = None,
    ephemeral: bool = False,
    task_id: str | None = None,
):
    s = state.settings
    if s.backend == "acp":
        raise NotImplementedError("Plan B deferred to follow-up PR")
    if ephemeral:
        resume_session_id = None
    kwargs = {
        "cwd": s.cwd,
        "schema": schema,
        "kimi_binary": s.kimi_binary,
        "wrapper_identity": (s.team_name, s.agent_name),
        "model": s.model,
        "effort": s.effort,
        "kimi_home": s.kimi_home,
        "thinking": s.thinking,
        "resume_session_id": resume_session_id,
        "task_id": task_id,
    }
    return headless_invoke.run(prompt, **kwargs)


def _main_loop(state: KimiLoopState) -> None:
    s = state.settings
    logger.info("kimi.loop.start", team=s.team_name, name=s.agent_name, poll_s=s.poll_interval_s)
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
                    logger.info("kimi.idle.sent")
                except Exception as e:
                    logger.warn("kimi.idle.send_fail", error=str(e))
        time.sleep(s.poll_interval_s)
        if state.shutdown_requested and state.in_flight_task is None:
            state.kimi_session_id = None
            state.approved_shutdown = True
            return


def _handle_message(state: KimiLoopState, msg: Any) -> None:
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
            "kimi.capability_manifest.update_seen",
            agent=payload.agent_name,
            capability_version=payload.capability_version,
            removed=payload.removed,
        )
    elif isinstance(payload, SteerIn):
        _handle_steer(state, payload, msg)
    else:
        logger.debug("kimi.inbox.protocol_noop", type=payload.__class__.__name__)


def _peer_prompt_fragments(state: KimiLoopState) -> str:
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
        logger.warn("kimi.capability_manifest.peer_prompt_fragments_fail", error=str(e))
        return ""


MAX_STEER_PREFIX_CHARS = 8192


def _handle_steer(state: KimiLoopState, payload: SteerIn, msg: Any) -> None:
    sender = getattr(msg, "from_", None) or payload.from_
    capabilities = _backend_metadata(state.settings).capabilities
    if sender != "team-lead" and "accepts_peer_steer" not in capabilities:
        logger.warn(
            "kimi.steer.rejected",
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
                backend="kimi",
                sender=sender,
            )
        except Exception as e:
            logger.debug("kimi.steer.rejection_event_emit_failed", error=str(e))
        return
    message = payload.message.strip() if isinstance(payload.message, str) else ""
    if not message:
        logger.warn("kimi.steer.rejected", sender=sender, reason="empty_message")
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
    logger.info("kimi.steer.queued", steer_id=steer_id, task_id=payload.task_id, priority=payload.priority)


def _steer_prefix_for_task(state: KimiLoopState, task: Any) -> str:
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
    logger.info("kimi.steer.injected", task_id=task_id, count=len(applicable), truncated=truncated)
    return "\n".join(lines)


def _handle_prose(state: KimiLoopState, msg: Any) -> None:
    s = state.settings
    sender = getattr(msg, "from_", "unknown")
    logger.info("kimi.inbox.prose", sender=sender, summary=getattr(msg, "summary", None))
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
        logger.warn("kimi.prose.crash", sender=sender, error=str(e))
    if reply is None and pio.should_skip_prose_fallback(result):
        logger.info("kimi.prose.delivered_via_tool", sender=sender, tool_calls=getattr(result, "tool_call_events", 0))
        return
    if reply is None:
        # See diagnostics module + Codex/Gemini loops — same pattern across
        # all three backends so the lead can grep / diagnose without
        # per-backend knowledge.
        from claude_anyteam import diagnostics
        error_class = diagnostics.classify_failure(result)
        incident_id = diagnostics.record_incident(
            team=s.team_name,
            agent=s.agent_name,
            backend="kimi",
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
            backend="kimi",
            incident_id=incident_id,
            error_class=error_class,
        )
    try:
        pio.send_prose(s.team_name, s.agent_name, sender, reply, summary="prose_reply")
    except Exception as e:
        logger.warn("kimi.prose.reply_send_fail", sender=sender, error=str(e))




def _handle_shutdown(state: KimiLoopState, payload: ShutdownRequestIn) -> None:
    s = state.settings
    req_id = payload.effective_request_id() or "shutdown-unknown"
    if req_id in state.seen_shutdown_request_ids:
        return
    state.seen_shutdown_request_ids.add(req_id)
    if state.in_flight_task is not None:
        reason = f"in-flight task #{state.in_flight_task}"
        logger.info("kimi.shutdown.reject", request_id=req_id, in_flight=state.in_flight_task)
        try:
            pio.send_shutdown_rejected(s.team_name, s.agent_name, req_id, reason=reason)
        except Exception as e:
            logger.warn("kimi.shutdown.response_fail", error=str(e))
        state.shutdown_requested = True
        return
    logger.info("kimi.shutdown.approve", request_id=req_id)
    try:
        pio.send_shutdown_approved(s.team_name, s.agent_name, req_id)
    except Exception as e:
        logger.warn("kimi.shutdown.response_fail", error=str(e))
    state.kimi_session_id = None
    state.queued_steers.clear()
    state.approved_shutdown = True


def _handle_plan_approval(state: KimiLoopState, payload: PlanApprovalRequestIn) -> None:
    s = state.settings
    if not s.plan_mode_required:
        logger.warn("kimi.plan.unexpected_request", request_id=payload.request_id)
        return
    req_id = payload.request_id
    if req_id is None:
        logger.warn("kimi.plan.missing_request_id")
        return

    target = _target_task_for_plan(state, payload)
    if target is None:
        logger.warn("kimi.plan.no_target_task", request_id=req_id)
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
            logger.warn("kimi.plan.block_msg_fail", error=str(e))
        return

    logger.info("kimi.plan.request_received", request_id=req_id, task_id=target.id)

    for attempt in (1, 2):
        schema = load_schema(headless_invoke.PLAN_SCHEMA)
        prompt = prompts.plan_prompt(target, tighten=attempt == 2, agent_name=s.agent_name, team_name=s.team_name)
        prompt += "\n\n# Output contract\n" + inline_schema_prompt_fragment(schema)
        try:
            result = _backend_run(state, prompt, schema=headless_invoke.PLAN_SCHEMA, ephemeral=True)
        except Exception as e:
            logger.error("kimi.plan.crash", task_id=target.id, error=str(e))
            result = None

        if result is not None and result.exit_code == 0 and result.structured is not None:
            try:
                pio.send_plan_approval_request(s.team_name, s.agent_name, request_id=req_id, plan=result.structured)
                logger.info(
                    "kimi.plan.sent",
                    request_id=req_id,
                    task_id=target.id,
                    steps=len(result.structured.get("steps", [])),
                )
            except Exception as e:
                logger.warn("kimi.plan.send_fail", error=str(e))
            return

        if result is None:
            logger.warn("kimi.plan.attempt_failed", attempt=attempt, task_id=target.id)
        else:
            logger.warn(
                "kimi.plan.kimi_fail",
                task_id=target.id,
                exit_code=result.exit_code,
                error=result.error,
            )
            logger.warn("kimi.plan.attempt_failed", attempt=attempt, task_id=target.id)

    _mark_blocked(
        state,
        target,
        "Kimi plan generation failed schema validation twice",
    )


def _target_task_for_plan(state: KimiLoopState, payload: PlanApprovalRequestIn):
    s = state.settings
    try:
        all_tasks = pio.list_tasks(s.team_name)
    except Exception as e:
        logger.warn("kimi.plan.task_list_fail", error=str(e))
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


def _find_and_claim(state: KimiLoopState):
    s = state.settings
    try:
        all_tasks = pio.list_tasks(s.team_name)
    except Exception as e:
        logger.warn("kimi.tasks.list_fail", error=str(e))
        return None
    candidates = [t for t in all_tasks if t.status == "pending" and not _blocked(all_tasks, t) and t.owner == s.agent_name]
    candidates += [t for t in all_tasks if t.status == "pending" and not _blocked(all_tasks, t) and t.owner in (None, "")]
    for t in sorted(candidates, key=lambda x: int(x.id)):
        try:
            claimed = pio.claim_task(s.team_name, t.id, s.agent_name, active_form=f"Running kimi on task #{t.id}")
            state.in_flight_task = claimed.id
            try:
                pio.emit_coupling_conflict_if_needed(
                    team=s.team_name,
                    agent=s.agent_name,
                    backend="kimi_headless",
                    task=claimed,
                    manifest=state.self_capability_manifest,
                )
            except Exception as e:
                logger.warn(
                    "kimi.task.coupling_conflict_visibility_failed",
                    task_id=claimed.id,
                    error=str(e),
                )
            logger.info("kimi.task.claimed", task_id=claimed.id, subject=claimed.subject)
            return claimed
        except ValueError:
            continue
    return None


def _has_claimable(state: KimiLoopState) -> bool:
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


def _execute_task(state: KimiLoopState, task) -> None:
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
        try:
            result = _backend_run(state, prompt, schema=headless_invoke.TASK_COMPLETE_SCHEMA, resume_session_id=state.kimi_session_id, ephemeral=False, task_id=str(task.id))
        except Exception as exc:
            # Mirrors codex/loop.py:672-680 — a subprocess crash inside the
            # adapter (OSError, FileNotFoundError, anything from invoke) must
            # not propagate to the main loop and kill the adapter process.
            logger.warn("kimi.task.crash", task_id=task.id, attempt=attempt, error=str(exc))
            _mark_blocked(state, task, reason=f"Kimi invocation crashed: {exc}")
            state.in_flight_task = None
            return
        if result.exit_code == 0 and result.structured is not None:
            if result.session_id and state.kimi_session_id is None:
                logger.info("kimi.task.session_captured", session_id=result.session_id)
            if result.session_id:
                state.kimi_session_id = result.session_id
            break
        if result.session_id:
            state.kimi_session_id = result.session_id
    if result is None or result.exit_code != 0 or result.structured is None:
        _mark_blocked(state, task, result.error if result else "Kimi invocation did not run")
        state.in_flight_task = None
        return
    files_changed = result.structured.get("files_changed") or []
    summary_text = result.structured.get("summary") or "(no summary)"
    try:
        pio.update_task(s.team_name, task.id, status="completed")
    except Exception as e:
        logger.warn("kimi.task.complete_fail", task_id=task.id, error=str(e))
        state.in_flight_task = None
        return
    try:
        # Backwards-compatible protocol field name; see limitations doc.
        pio.send_task_complete(s.team_name, s.agent_name, task_id=task.id, files_changed=files_changed, summary_text=summary_text, codex_exit_code=result.exit_code)
    except Exception as e:
        logger.warn("kimi.task.complete_msg_fail", task_id=task.id, error=str(e))
    logger.info("kimi.task.completed", task_id=task.id, files=len(files_changed))
    state.in_flight_task = None


def _mark_blocked(state: KimiLoopState, task, reason: str) -> None:
    s = state.settings
    try:
        current = pio.get_task(s.team_name, task.id)
        if getattr(current, "status", None) == "completed":
            logger.info(
                "kimi.task.block_skip_already_completed",
                task_id=task.id,
                reason_would_have_been=reason[:120],
            )
            return
    except Exception as e:
        logger.warn("kimi.task.block_precheck_fail", task_id=task.id, error=str(e))
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
        logger.warn("kimi.task.block_update_fail", task_id=task.id, error=str(e))

    try:
        pio.send_task_blocked(s.team_name, s.agent_name, task_id=task.id, reason=reason)
    except Exception as e:
        logger.warn("kimi.task.block_msg_fail", task_id=task.id, error=str(e))
