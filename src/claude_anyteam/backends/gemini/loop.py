"""Control loop for Gemini-backed teammates.

This intentionally mirrors the Codex control loop at the protocol boundary but
uses one-shot Gemini CLI headless invocations. There is no Codex app-server /
turn-steer equivalent in this Plan A loop.
"""
from __future__ import annotations

import json
import signal
import time
from dataclasses import dataclass, field
from typing import Any

from claude_anyteam import logger, protocol_io as pio
from claude_anyteam.messages import PlanApprovalRequestIn, ShutdownRequestIn, parse_protocol_text
from claude_anyteam.registration import BackendMetadata, deregister, register
from claude_anyteam.schema_validation import inline_schema_prompt_fragment, load_schema

from . import invoke, prompts
from .config import GeminiSettings


@dataclass
class GeminiLoopState:
    settings: GeminiSettings
    shutdown_requested: bool = False
    approved_shutdown: bool = False
    in_flight_task: str | None = None
    seen_shutdown_request_ids: set[str] = field(default_factory=set)
    gemini_session_id: str | None = None


def run(settings: GeminiSettings) -> int:
    invoke.feature_test(settings.gemini_binary)
    register(
        settings,
        BackendMetadata(
            model="gemini-cli",
            prompt=(
                "Gemini teammate adapter. Protocol I/O is handled by the adapter; "
                "coding work is delegated to Gemini CLI headless mode. No Claude LLM is involved."
            ),
        ),
    )
    state = GeminiLoopState(settings=settings)

    def _sig_handler(signum: int, _frame: Any) -> None:
        logger.warn("gemini.signal.received", signum=signum)
        state.shutdown_requested = True

    signal.signal(signal.SIGINT, _sig_handler)
    signal.signal(signal.SIGTERM, _sig_handler)

    exit_code = 0
    try:
        _main_loop(state)
    except Exception as e:
        logger.error("gemini.loop.crash", error=str(e))
        exit_code = 1
    finally:
        if state.approved_shutdown:
            deregister(settings)
            logger.info("gemini.loop.deregistered", name=settings.agent_name)
        else:
            logger.warn("gemini.loop.exit_without_deregister", in_flight_task=state.in_flight_task)
    return exit_code


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
    else:
        logger.debug("gemini.inbox.protocol_noop", type=payload.__class__.__name__)


def _handle_prose(state: GeminiLoopState, msg: Any) -> None:
    s = state.settings
    sender = getattr(msg, "from_", "unknown")
    prompt = prompts.prose_reply_prompt(sender=sender, body=msg.text, agent_name=s.agent_name, team_name=s.team_name)
    reply: str | None = None
    try:
        result = invoke.run(prompt, cwd=s.cwd, gemini_binary=s.gemini_binary, wrapper_identity=(s.team_name, s.agent_name), model=s.model, gemini_home=s.gemini_home)
        if result.exit_code == 0 and result.last_message:
            reply = result.last_message
    except Exception as e:
        logger.warn("gemini.prose.crash", sender=sender, error=str(e))
    if reply is None:
        reply = "I received your message, but the Gemini adapter could not generate a reply."
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
        try:
            pio.send_shutdown_response(s.team_name, s.agent_name, req_id, approve=False, feedback=f"in-flight task #{state.in_flight_task}")
        except Exception as e:
            logger.warn("gemini.shutdown.response_fail", error=str(e))
        state.shutdown_requested = True
        return
    try:
        pio.send_shutdown_response(s.team_name, s.agent_name, req_id, approve=True)
    except Exception as e:
        logger.warn("gemini.shutdown.response_fail", error=str(e))
    state.gemini_session_id = None
    state.approved_shutdown = True


def _handle_plan_approval(state: GeminiLoopState, payload: PlanApprovalRequestIn) -> None:
    s = state.settings
    if not s.plan_mode_required:
        logger.warn("gemini.plan.unexpected_request", request_id=payload.request_id)
        return
    req_id = payload.request_id
    target = _target_task_for_plan(state, payload)
    if not req_id or target is None:
        return
    for attempt in (1, 2):
        schema = load_schema(invoke.PLAN_SCHEMA)
        prompt = prompts.plan_prompt(target, tighten=attempt == 2, agent_name=s.agent_name, team_name=s.team_name)
        prompt += "\n\n# Output contract\n" + inline_schema_prompt_fragment(schema)
        result = invoke.run(prompt, cwd=s.cwd, schema=invoke.PLAN_SCHEMA, gemini_binary=s.gemini_binary, wrapper_identity=(s.team_name, s.agent_name), model=s.model, gemini_home=s.gemini_home)
        if result.exit_code == 0 and result.structured is not None:
            pio.send_plan_approval_request(s.team_name, s.agent_name, request_id=req_id, plan=result.structured)
            return
    _mark_blocked(state, target, "Gemini plan generation failed schema validation twice")


def _target_task_for_plan(state: GeminiLoopState, payload: PlanApprovalRequestIn):
    s = state.settings
    try:
        all_tasks = pio.list_tasks(s.team_name)
    except Exception:
        return None
    by_id = {t.id: t for t in all_tasks}
    if payload.task_id and payload.task_id in by_id:
        return by_id[payload.task_id]
    for t in sorted((t for t in all_tasks if (t.owner in (None, "", s.agent_name)) and t.status == "pending" and not _blocked(all_tasks, t)), key=lambda x: int(x.id)):
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


def _execute_task(state: GeminiLoopState, task) -> None:
    s = state.settings
    schema = load_schema(invoke.TASK_COMPLETE_SCHEMA)
    result = None
    for attempt in (1, 2):
        prompt = prompts.task_prompt(task, agent_name=s.agent_name, team_name=s.team_name)
        prompt += "\n\n# Output contract\n" + inline_schema_prompt_fragment(schema)
        if attempt == 2:
            prompt += "\n\nPRIOR ATTEMPT FAILED: return ONLY the JSON object matching the schema."
        result = invoke.run(
            prompt,
            cwd=s.cwd,
            schema=invoke.TASK_COMPLETE_SCHEMA,
            gemini_binary=s.gemini_binary,
            wrapper_identity=(s.team_name, s.agent_name),
            resume_session_id=state.gemini_session_id,
            model=s.model,
            gemini_home=s.gemini_home,
        )
        if result.session_id:
            state.gemini_session_id = result.session_id
        if result.exit_code == 0 and result.structured is not None:
            break
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
