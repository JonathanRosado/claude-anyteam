"""Canonical capability declarations for routed teammate registration.

R11 in ``docs/internal/protocol-rev/09-implementation-roadmap.md`` adds the
flat ``members[].capabilities`` roster layer. 08 §6.3 defines the
Agent Card ``capabilities()`` hook this cheap list is derived from; R12/R13
add the rich manifest that the wrapper MCP returns from cache.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from claude_teams.coupling import declaration_for_regime

CAPABILITY_NAMES = frozenset(
    {
        "turn_steer",
        "thread_fork",
        "permission_bridge",
        "live_tool_events",
        "structured_output",
        "headless_invocation",
        "session_resume",
        "plan_mode",
        "trust_modes",
        "native_skills",
        "large_context",
        "accepts_peer_steer",
        "soft_non_progress_watchdog",
        "wrapper_tool_failure_discriminator",
    }
)


CODEX_APP_SERVER_CAPABILITIES = [
    "turn_steer",
    "thread_fork",
    "live_tool_events",
    "structured_output",
    "plan_mode",
    "soft_non_progress_watchdog",
    "wrapper_tool_failure_discriminator",
    # Q4 (per opus-arch-impl): Codex App Server is deliberately lead-only
    # for peer steer until the handler and runtime behavior are re-reviewed.
]

CODEX_EXEC_CAPABILITIES = [
    "headless_invocation",
    "session_resume",
    "structured_output",
    "plan_mode",
]

GEMINI_ACP_CAPABILITIES = [
    # ACP delivery is wired at the next turn boundary today, but R11 declares
    # this cheap flag ahead of the richer R12/R13 manifest's delivery-mode
    # detail so peers can discover that steer exists at all.
    "turn_steer",
    "permission_bridge",
    "live_tool_events",
    "structured_output",
    "session_resume",
    "plan_mode",
    "trust_modes",
    "accepts_peer_steer",
]

GEMINI_HEADLESS_CAPABILITIES: list[str] = [
    "headless_invocation",
    "session_resume",
    "structured_output",
    "plan_mode",
]

KIMI_HEADLESS_CAPABILITIES = [
    "headless_invocation",
    "session_resume",
    "structured_output",
    "plan_mode",
    "native_skills",
    "large_context",
]

CLAUDE_NATIVE_HEADLESS_CAPABILITIES = [
    "headless_invocation",
    "structured_output",
    "live_tool_events",
    "native_skills",
    "large_context",
]

CAPABILITY_MANIFEST_SCHEMA_VERSION = 1
CAPABILITY_MANIFEST_VERSION = "2"

_CAPABILITY_DISPLAY_NAMES = {
    "turn_steer": "turn/steer (`turn_steer`)",
    "thread_fork": "thread/fork (`thread_fork`)",
    "permission_bridge": "permission bridge (`permission_bridge`)",
    "live_tool_events": "live tool events (`live_tool_events`)",
    "structured_output": "structured output (`structured_output`)",
    "headless_invocation": "headless invocation (`headless_invocation`)",
    "session_resume": "session resume (`session_resume`)",
    "plan_mode": "plan mode (`plan_mode`)",
    "trust_modes": "trust modes (`trust_modes`)",
    "native_skills": "native skills (`native_skills`)",
    "large_context": "large context (`large_context`)",
    "accepts_peer_steer": "peer steer acceptance (`accepts_peer_steer`)",
    "soft_non_progress_watchdog": "soft non-progress watchdog (`soft_non_progress_watchdog`)",
    "wrapper_tool_failure_discriminator": "wrapper-tool failure discriminator (`wrapper_tool_failure_discriminator`)",
}


@dataclass(frozen=True)
class CapabilityRuntimeHook:
    """Registry entry proving a capability is not decorative.

    ``runtime_paths`` point at code that delivers or enforces the capability.
    ``test_paths`` point at focused regression tests for that runtime path.
    The paths are intentionally stringly-typed so tests can validate the
    registry without importing backend CLIs or spawning subprocesses.
    """

    runtime_paths: tuple[str, ...]
    test_paths: tuple[str, ...]
    note: str


CAPABILITY_HOOKS: dict[str, CapabilityRuntimeHook] = {
    "turn_steer": CapabilityRuntimeHook(
        runtime_paths=(
            "claude_anyteam.codex:_SteerQueue.push",
            "claude_anyteam.backends.gemini.loop:_handle_steer",
            "claude_anyteam.backends.kimi.loop:_handle_steer",
        ),
        test_paths=(
            "tests/test_peer_steer_authz.py::test_phase4_17_codex_mid_turn_peer_steer_kind_with_capability_still_queues",
            "tests/test_gemini_next_turn_steer.py::test_acp_message_kind_steer_plain_prose_queues_peer_steer",
            "tests/test_kimi_loop.py::test_kimi_message_kind_steer_plain_prose_queues_team_lead_steer",
        ),
        note="Codex live turn/steer plus Gemini/Kimi next-turn steer queues.",
    ),
    "thread_fork": CapabilityRuntimeHook(
        runtime_paths=(
            "claude_anyteam.codex:_start_or_fork_thread",
            "claude_anyteam.app_server:AppServerClient.thread_fork",
        ),
        test_paths=(
            "tests/test_fork_dispatch.py::test_second_task_uses_thread_fork",
            "tests/test_fork_dispatch.py::test_thread_fork_method_hits_app_server_with_correct_params",
        ),
        note="Codex App Server session lineage uses thread/fork on resumed tasks.",
    ),
    "permission_bridge": CapabilityRuntimeHook(
        runtime_paths=(
            "claude_anyteam.backends.gemini.acp_client:GeminiAcpClient.handle_server_request",
            "claude_anyteam.protocol_io:send_permission_request_to_lead",
            "claude_anyteam.protocol_io:wait_for_permission_response",
        ),
        test_paths=(
            "tests/test_gemini_acp_client.py::test_request_permission_default_bridges_allow_once",
            "tests/test_permission_bridge.py::test_wait_for_permission_response_marks_only_matching_lead_message",
        ),
        note="Gemini ACP permission requests bridge to the team-lead mailbox.",
    ),
    "live_tool_events": CapabilityRuntimeHook(
        runtime_paths=(
            "claude_anyteam.codex:_visibility_for_app_server_item",
            "claude_anyteam.headless_visibility:HeadlessTurnVisibility",
            "claude_anyteam.backends.claude_native.invoke:_parse_stdout",
            "claude_anyteam.wrapper_server:build_server",
        ),
        test_paths=(
            "tests/test_visibility_events.py::test_app_server_command_execution_writes_tool_event_to_event_log_and_active_form",
            "tests/test_claude_native_backend.py::test_parse_stdout_synthesizes_anyteam_mcp_tool_events",
            "tests/test_wrapper_contract.py::test_exposed_tool_handlers_are_instrumented",
        ),
        note="Host-tool activity is normalized into visibility envelopes.",
    ),
    "structured_output": CapabilityRuntimeHook(
        runtime_paths=(
            "claude_anyteam.schema_validation:parse_and_validate",
            "claude_anyteam.codex:TASK_COMPLETE_SCHEMA",
            "claude_anyteam.backends.gemini.invoke:run",
            "claude_anyteam.backends.gemini.acp:run",
            "claude_anyteam.backends.kimi.invoke:run",
            "claude_anyteam.backends.claude_native.invoke:run",
        ),
        test_paths=(
            "tests/test_schema_validation.py::test_valid_output_parses_and_returns_dict",
            "tests/test_packaged_schemas.py::test_wheel_install_ships_schemas_and_validates_task_complete",
            "tests/test_gemini_invoke.py::test_run_parses_stream_json_and_validates_schema",
            "tests/test_gemini_acp_prompt_flow.py::test_acp_run_structured_result_and_state",
            "tests/test_kimi_invoke.py::test_run_exit_zero_success_captures_session_id_and_state",
            "tests/test_claude_native_backend.py::test_invoke_run_accepts_native_preamble_before_schema_json",
        ),
        note="Task completion output is schema-constrained and Python-validated.",
    ),
    "headless_invocation": CapabilityRuntimeHook(
        runtime_paths=(
            "claude_anyteam.codex:run",
            "claude_anyteam.backends.gemini.invoke:run",
            "claude_anyteam.backends.kimi.invoke:run",
            "claude_anyteam.backends.claude_native.invoke:run",
        ),
        test_paths=(
            "tests/test_codex_invocation_shape.py::test_fresh_exec_still_includes_schema_and_cwd",
            "tests/test_gemini_invoke.py::test_run_parses_stream_json_and_validates_schema",
            "tests/test_kimi_invocation_shape.py::test_fresh_argv_uses_print_stream_json_model_and_prompt",
            "tests/test_claude_native_backend.py::test_invoke_run_builds_claude_print_stream_json_argv",
        ),
        note="Non-App-Server backends run noninteractive CLI turns with machine-readable output.",
    ),
    "session_resume": CapabilityRuntimeHook(
        runtime_paths=(
            "claude_anyteam.codex:run",
            "claude_anyteam.backends.gemini.acp:_ensure_session",
            "claude_anyteam.backends.gemini.invoke:run",
            "claude_anyteam.backends.kimi.invoke:_known_session",
            "claude_anyteam.backends.kimi.loop:_backend_run",
        ),
        test_paths=(
            "tests/test_resume_dispatch.py::test_resume_path_validates_and_returns_structured",
            "tests/test_gemini_acp_session_reload.py::test_acp_run_reloads_persisted_storage_session",
            "tests/test_gemini_invoke.py::test_run_persists_headless_session_id_to_adapter_state",
            "tests/test_kimi_invocation_shape.py::test_known_resume_session_adds_session_flag",
        ),
        note="Headless/ACP transports carry cross-task context through CLI or ACP session IDs.",
    ),
    "plan_mode": CapabilityRuntimeHook(
        runtime_paths=(
            "claude_anyteam.loop:_handle_plan_approval",
            "claude_anyteam.backends.gemini.loop:_handle_plan_approval",
            "claude_anyteam.backends.kimi.loop:_handle_plan_approval",
        ),
        test_paths=(
            "tests/test_plan_approval.py::test_plan_success_path_sends_plan",
            "tests/test_gemini_plan_approval.py::test_gemini_plan_send_crash_is_logged_not_raised",
            "tests/test_kimi_plan_approval.py::test_plan_prompt_does_not_invoke_kimi_plan_flag_anywhere",
        ),
        note="Backends declaring plan_mode can participate in opt-in structured plan approval.",
    ),
    "trust_modes": CapabilityRuntimeHook(
        runtime_paths=(
            "claude_anyteam.backends.gemini.config:GeminiSettings",
            "claude_anyteam.backends.gemini.acp:TRUST_TO_ACP_MODE",
            "claude_anyteam.backends.gemini.acp_client:GeminiAcpClient.set_session_mode",
        ),
        test_paths=(
            "tests/test_gemini_effort.py::test_gemini_trust_mode_from_env_and_overrides",
            "tests/test_gemini_acp_prompt_flow.py::test_acp_run_trust_modes_map_to_acp_session_modes",
        ),
        note="Gemini ACP exposes trusted/default/plan modes and maps them onto ACP session modes.",
    ),
    "native_skills": CapabilityRuntimeHook(
        runtime_paths=(
            "claude_anyteam.backends.kimi.invoke:run",
            "claude_anyteam.backends.kimi.config:KimiSettings",
            "claude_anyteam.backends.claude_native.invoke:run",
            "claude_anyteam.backends.claude_native.loop:_backend_metadata",
        ),
        test_paths=(
            "tests/test_kimi_invocation_shape.py::test_default_invocation_preserves_kimi_native_skill_discovery",
            "tests/test_capability_declarations.py::test_kimi_headless_backend_metadata_declares_native_skills",
            "tests/test_capability_declarations.py::test_claude_native_backend_metadata_declares_native_tool_surface",
        ),
        note=(
            "Backend-native skill/tool discovery stays inside the routed harness "
            "(Kimi skills or Claude Code Skill/Task tools), not anyteam."
        ),
    ),
    "large_context": CapabilityRuntimeHook(
        runtime_paths=(
            "claude_anyteam.backends.kimi.invoke:run",
            "claude_anyteam.backends.kimi.loop:_backend_metadata",
            "claude_anyteam.backends.claude_native.loop:_backend_metadata",
        ),
        test_paths=(
            "tests/test_kimi_invocation_shape.py::test_fresh_argv_uses_print_stream_json_model_and_prompt",
            "tests/test_capability_declarations.py::test_kimi_headless_backend_metadata_declares_large_context_and_native_skills",
            "tests/test_capability_declarations.py::test_claude_native_backend_metadata_declares_native_tool_surface",
        ),
        note="Kimi and native Claude expose large-context routed harnesses through their native CLIs.",
    ),
    "accepts_peer_steer": CapabilityRuntimeHook(
        runtime_paths=(
            "claude_anyteam.capabilities:manifest_accepts_peer_steer",
            "claude_anyteam.wrapper_server:build_server",
            "claude_anyteam.backends.gemini.loop:_handle_steer",
        ),
        test_paths=(
            "tests/test_peer_steer_authz.py::test_peer_steer_from_non_lead_succeeds_for_gemini_acp",
            "tests/test_wrapper_contract.py::test_wrapper_peer_steer_still_refused_after_query_when_manifest_denies",
        ),
        note="Peer steer is opt-in and enforced by sender and recipient gates.",
    ),
    "soft_non_progress_watchdog": CapabilityRuntimeHook(
        runtime_paths=(
            "claude_anyteam.codex:app_server_invoke",
            "claude_anyteam.config:Settings",
        ),
        test_paths=(
            "tests/test_visibility_events.py::test_app_server_no_checkpoint_after_300s_emits_turn_progress",
            "tests/test_app_server_default.py::test_non_progress_env_and_overrides_are_honored",
        ),
        note="Codex App Server turns emit non-progress warnings and optional interrupts.",
    ),
    "wrapper_tool_failure_discriminator": CapabilityRuntimeHook(
        runtime_paths=(
            "claude_anyteam.codex:app_server_invoke",
            "claude_anyteam.wrapper_tool_failure:is_wrapper_tool_recovery_event_kind",
            "claude_anyteam.config:Settings",
        ),
        test_paths=(
            "tests/test_visibility_events.py::test_wrapper_tool_failure_unrecovered_emits_after_quiet_window",
            "tests/test_visibility_events.py::test_wrapper_tool_failure_multi_failure_series_emits_one_terminal_envelope",
            "tests/test_visibility_events.py::test_wrapper_tool_failure_per_tool_debounce_independent",
            "tests/test_app_server_default.py::test_wrapper_tool_failure_window_env_and_overrides_are_honored",
        ),
        note=(
            "Codex App Server emits wrapper_tool_failure_unrecovered after the "
            "configured discriminator window, debounces same-tool retry loops, "
            "and keeps different wrapper tools independent."
        ),
    ),
}

# Backwards-readable alias for callers/tests that want the longer name.
CAPABILITY_RUNTIME_REGISTRY = CAPABILITY_HOOKS

_BASE_CAPABILITY_MANIFEST: dict[str, dict[str, Any]] = {
    "turn_steer": {
        "version": "1",
        "schema": {
            "type": "object",
            "required": ["text"],
            "properties": {
                "text": {"type": "string", "maxLength": 8192},
                "task_id": {"type": ["string", "null"]},
                "priority": {"type": "string", "enum": ["normal", "urgent"], "default": "normal"},
                "expires_after_turns": {"type": "integer", "minimum": 1, "default": 1},
            },
        },
        "description": "Inject text mid-turn or at a turn boundary to redirect a teammate's reasoning.",
        "when_to_use": (
            "Use when you see the teammate pursuing a stale path, when a peer discovers a "
            "constraint that must be applied before the task continues, or when context should "
            "arrive without restarting the task."
        ),
        "when_not_to": (
            "Do not steer with low-information nudges such as 'are you done?', and avoid "
            "steering while a structured tool call depends on stable inputs."
        ),
        "failure_modes": [
            "RACE_LOST_NO_TURN_IN_FLIGHT",
            "STEER_BUFFERED_NEXT_BOUNDARY",
            "STEER_AUTH_REJECTED",
            "STEER_PAYLOAD_OVERFLOW",
        ],
    },
    "thread_fork": {
        "version": "1",
        "schema": {
            "type": "object",
            "properties": {
                "parent_thread_id": {"type": "string"},
                "task_id": {"type": ["string", "null"]},
            },
        },
        "description": "Fork a persisted Codex App Server thread so future work inherits prior-task context.",
        "when_to_use": (
            "Ask a Codex App Server teammate to use thread continuity when a follow-up task "
            "depends on substantial context from its previous task."
        ),
        "when_not_to": (
            "Do not depend on thread_fork for stateless one-shot tasks, or after a failed turn "
            "whose context should not poison the next attempt."
        ),
        "failure_modes": ["PARENT_THREAD_NOT_MATERIALIZED", "FORK_UNSUPPORTED", "FORK_CONTEXT_STALE"],
        "callable_from_peers": True,
    },
    "permission_bridge": {
        "version": "1",
        "schema": {
            "type": "object",
            "required": ["request_id", "decision"],
            "properties": {
                "request_id": {"type": "string"},
                "decision": {"type": "string", "enum": ["allow_once", "allow_session", "deny"]},
                "reason": {"type": "string"},
            },
        },
        "description": "Surface sensitive host-tool use to team-lead for interactive approval before execution.",
        "when_to_use": (
            "Route approval-sensitive work here when tasks touch production paths, secrets, "
            "external networks, or other operations the lead wants explicitly gated."
        ),
        "when_not_to": (
            "Do not route routine read-only or simple test tasks here solely for the bridge; "
            "approval prompts add latency without value."
        ),
        "failure_modes": [
            "APPROVAL_TIMEOUT",
            "APPROVAL_BRIDGE_ERROR",
            "APPROVAL_CONTEXT_MISSING",
            "DENIED_BY_TEAM_LEAD",
        ],
        "callable_from_peers": False,
    },
    "live_tool_events": {
        "version": "1",
        "schema": {"type": "object", "additionalProperties": True},
        "description": "Emit or expose host-tool activity while a turn is running rather than only at task completion.",
        "when_to_use": "Prefer this teammate when peers or the lead need live operational visibility into long-running work.",
        "when_not_to": "Not directly callable; this is an observability signal for routing and expectations.",
        "failure_modes": ["TOOL_EVENT_STREAM_DEGRADED", "HOST_EVENT_SHAPE_CHANGED"],
        "callable_from_peers": False,
    },
    "structured_output": {
        "version": "1",
        "schema": {"type": "object", "required": ["files_changed", "summary"]},
        "description": "Return schema-validated task-complete JSON with files_changed and summary fields.",
        "when_to_use": "Use for coding tasks where the lead needs machine-readable completion metadata.",
        "when_not_to": "Not directly callable by peers; it describes final-output fidelity.",
        "failure_modes": ["SCHEMA_VALIDATION_FAILED", "OUTPUT_SCHEMA_UNSUPPORTED", "RETRY_EXHAUSTED"],
        "callable_from_peers": False,
    },
    "headless_invocation": {
        "version": "1",
        "schema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["codex_exec", "gemini_headless", "kimi_headless", "claude_native"],
                },
                "machine_output": {"type": "boolean", "const": True},
            },
        },
        "description": (
            "Run a noninteractive CLI turn whose stdout/sidecar output can be "
            "parsed by the adapter."
        ),
        "when_to_use": (
            "Prefer this teammate for simple one-shot or batchable work where "
            "mid-turn steering is not needed and process isolation is useful."
        ),
        "when_not_to": (
            "Do not choose headless invocation when live mid-turn steering or "
            "Codex App Server thread/fork is required."
        ),
        "failure_modes": [
            "CLI_BINARY_MISSING",
            "HEADLESS_FLAG_UNSUPPORTED",
            "MACHINE_OUTPUT_PARSE_FAILED",
            "TURN_TIMEOUT",
        ],
        "callable_from_peers": False,
    },
    "session_resume": {
        "version": "1",
        "schema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "transport": {
                    "type": "string",
                    "enum": ["codex_exec", "gemini_acp", "gemini_headless", "kimi_headless"],
                },
            },
        },
        "description": (
            "Carry context across turns or tasks by resuming a backend-native "
            "session identifier."
        ),
        "when_to_use": (
            "Ask for continuity when a follow-up depends on prior conversation "
            "or repository findings and the backend is not using Codex "
            "App Server thread/fork."
        ),
        "when_not_to": (
            "Do not rely on resume after a failed or intentionally isolated "
            "ephemeral prose/plan turn."
        ),
        "failure_modes": [
            "SESSION_ID_MISSING",
            "SESSION_NOT_FOUND",
            "RESUME_UNSUPPORTED_FLAG_COMBINATION",
            "RESUMED_OUTPUT_SCHEMA_UNAVAILABLE",
        ],
        "callable_from_peers": False,
    },
    "plan_mode": {
        "version": "1",
        "schema": {
            "type": "object",
            "required": ["request_id"],
            "properties": {
                "request_id": {"type": "string"},
                "task_id": {"type": ["string", "null"]},
            },
        },
        "description": "Draft a structured plan for lead approval without completing the task.",
        "when_to_use": (
            "Use when a teammate was launched with planModeRequired and the "
            "lead requests an explicit plan before execution."
        ),
        "when_not_to": (
            "Do not send plan requests to teammates that were not configured "
            "for plan approval; they will ignore unexpected requests."
        ),
        "failure_modes": [
            "PLAN_MODE_NOT_REQUIRED",
            "PLAN_TARGET_MISSING",
            "PLAN_SCHEMA_VALIDATION_FAILED",
            "PLAN_SEND_FAILED",
        ],
        "callable_from_peers": False,
    },
    "trust_modes": {
        "version": "1",
        "schema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["trusted", "default", "plan"],
                }
            },
        },
        "description": (
            "Gemini ACP can run in trusted, default, or plan trust modes and "
            "maps them to ACP session modes."
        ),
        "when_to_use": (
            "Choose default or plan when sensitive Gemini host-tool use should "
            "bridge to team-lead approval instead of auto-approving."
        ),
        "when_not_to": (
            "Do not expect this capability on Codex, Gemini headless, or Kimi; "
            "their approval/sandbox controls have different shapes."
        ),
        "failure_modes": [
            "INVALID_TRUST_MODE",
            "SET_SESSION_MODE_FAILED",
            "APPROVAL_TIMEOUT",
            "DENIED_BY_TEAM_LEAD",
        ],
        "callable_from_peers": False,
    },
    "native_skills": {
        "version": "1",
        "schema": {
            "type": "object",
            "properties": {
                "discovery": {
                    "type": "string",
                    "enum": ["backend_default", "kimi_default", "claude_code_default"],
                },
                "overrides_skills_dir": {"type": "boolean", "const": False},
            },
        },
        "description": (
            "Preserve backend-native skill/tool discovery inside the routed "
            "teammate instead of re-implementing it in anyteam."
        ),
        "when_to_use": (
            "Route workflow-rich tasks to this teammate when Kimi-native skills "
            "or Claude Code's Skill/Task tools may help and peers do not need to "
            "enumerate or invoke those skills through anyteam."
        ),
        "when_not_to": (
            "Do not assume peers can list or call backend-native skills directly "
            "through anyteam; the root backend session owns discovery and selection."
        ),
        "failure_modes": [
            "SKILL_DISCOVERY_CHANGED_UPSTREAM",
            "SKILLS_DIR_OVERRIDE_HIDES_DEFAULTS",
            "SKILL_NOT_AVAILABLE_TO_ROOT_AGENT",
        ],
        "callable_from_peers": False,
    },
    "large_context": {
        "version": "1",
        "schema": {"type": "object", "properties": {"context_tokens": {"type": "integer", "minimum": 100000}}},
        "description": "Handle very large prompts or repositories with a context window above 100k tokens.",
        "when_to_use": "Route broad audits, large-file synthesis, or multi-document reasoning to this teammate.",
        "when_not_to": "Do not use large_context as a substitute for precise task scoping when a smaller teammate is faster.",
        "failure_modes": ["CONTEXT_TOO_LARGE", "CONTEXT_TRUNCATED", "MODEL_CONTEXT_POLICY_CHANGED"],
        "callable_from_peers": False,
    },
    "accepts_peer_steer": {
        "version": "1",
        "schema": {"type": "boolean"},
        "description": "Declare that non-lead peers may send steer messages to this teammate.",
        "when_to_use": "Check before steering another peer directly; if absent, route steer requests through team-lead.",
        "when_not_to": "Not an invocation primitive by itself; it is the authorization signal for turn_steer.",
        "failure_modes": ["PEER_STEER_REJECTED", "STEER_AUTH_REJECTED"],
        "callable_from_peers": False,
    },
    "soft_non_progress_watchdog": {
        "version": "1",
        "schema": {
            "type": "object",
            "properties": {
                "non_progress_warn_s": {
                    "type": ["number", "null"],
                    "default": None,
                    "minimum": 60,
                    "maximum": 1800,
                },
                "non_progress_interrupt_s": {
                    "type": ["number", "null"],
                    "default": None,
                    "minimum": 60,
                    "maximum": 3600,
                },
            },
        },
        "description": (
            "Self-monitor Codex App Server turns and emit a turn_progress warning "
            "envelope when no visible checkpoint appears for the configured interval."
        ),
        "when_to_use": (
            "Prefer this teammate for long-running Codex App Server tasks where the "
            "lead needs a durable warning and checkpoint steer rather than waiting "
            "silently for the wall-clock timeout."
        ),
        "when_not_to": (
            "Not directly callable by peers, and not declared by Codex exec, "
            "Gemini, or Kimi; those backends lack the same App Server polling "
            "signal and should not pretend to support it."
        ),
        "failure_modes": [
            "WATCHDOG_WARNING_SENT",
            "WATCHDOG_STEER_FAILED",
            "WATCHDOG_INTERRUPT_SENT",
        ],
        "callable_from_peers": False,
    },
    "wrapper_tool_failure_discriminator": {
        "version": "1",
        "schema": {
            "type": "object",
            "properties": {
                "wrapper_tool_failure_window_s": {
                    "type": "number",
                    "default": 90,
                    "minimum": 60,
                    "maximum": 300,
                },
                "recovery_event_kinds": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": ["turn_progress", "tool_event", "artifact_event"],
                },
                "debounce_key": {
                    "type": "string",
                    "const": "tool_name",
                },
            },
        },
        "description": (
            "Discriminate Codex App Server wrapper-MCP tool failures that are "
            "not followed by recovery activity within the configured window, "
            "then emit wrapper_tool_failure_unrecovered as a lead-actionable "
            "visibility envelope."
        ),
        "when_to_use": (
            "Prefer this teammate for long-running Codex App Server work where "
            "wrapper MCP failures such as missing files or bad task ids should "
            "be visible before turn completion."
        ),
        "when_not_to": (
            "Not directly callable by peers. Do not declare it for Codex exec, "
            "Gemini, or Kimi until those backends expose the same live wrapper "
            "tool event stream and discriminator window."
        ),
        # Per the capability-manifest schema convention, ``failure_modes`` is
        # the closed list callers must handle. For monitoring capabilities that
        # includes non-terminal outcomes such as recovered/debounced signals.
        "failure_modes": [
            "WRAPPER_TOOL_FAILURE_UNRECOVERED",
            "WRAPPER_TOOL_FAILURE_RECOVERED_BY_PROGRESS",
            "WRAPPER_TOOL_FAILURE_DEBOUNCED_BY_TOOL_NAME",
        ],
        "callable_from_peers": False,
    },
}


def assert_known_capabilities(capabilities: list[str]) -> list[str]:
    """Return a copy after asserting adapter-declared flags use the taxonomy."""
    unknown = sorted(set(capabilities) - CAPABILITY_NAMES)
    if unknown:
        raise ValueError(f"unknown capability flag(s): {', '.join(unknown)}")
    missing_hooks = [
        name
        for name in sorted(set(capabilities))
        if name not in CAPABILITY_RUNTIME_REGISTRY
        or not CAPABILITY_RUNTIME_REGISTRY[name].runtime_paths
        or not CAPABILITY_RUNTIME_REGISTRY[name].test_paths
    ]
    if missing_hooks:
        raise ValueError(
            "capability flag(s) missing runtime hook/test registry: "
            + ", ".join(missing_hooks)
        )
    return list(capabilities)


_REQUIRED_CAPABILITY_ENTRY_FIELDS = frozenset(
    {
        "version",
        "schema",
        "description",
        "when_to_use",
        "when_not_to",
        "failure_modes",
        "callable_from_peers",
    }
)


def validate_capability_manifest_entries(entries: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Validate rich manifest entries before an Agent Card is written.

    This is deliberately stricter than the cache reader, which must tolerate
    old manifests. New declarations must be exact: every advertised flag is
    part of the known taxonomy, has a registered runtime hook and regression
    test, and exposes the fields peers need to use the capability safely.
    """

    if not isinstance(entries, dict):
        raise ValueError("capability manifest entries must be an object")
    unknown = sorted(set(entries) - CAPABILITY_NAMES)
    if unknown:
        raise ValueError(f"unknown capability manifest entry(s): {', '.join(unknown)}")
    assert_known_capabilities(list(entries))

    validated: dict[str, dict[str, Any]] = {}
    for name, raw_entry in entries.items():
        if not isinstance(raw_entry, dict):
            raise ValueError(f"capability {name!r} entry must be an object")
        missing = sorted(_REQUIRED_CAPABILITY_ENTRY_FIELDS - set(raw_entry))
        if missing:
            raise ValueError(
                f"capability {name!r} missing required field(s): "
                + ", ".join(missing)
            )
        for field in ("version", "description", "when_to_use", "when_not_to"):
            value = raw_entry.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"capability {name!r} field {field!r} must be a non-empty string")
        if not isinstance(raw_entry.get("schema"), dict):
            raise ValueError(f"capability {name!r} field 'schema' must be an object")
        failures = raw_entry.get("failure_modes")
        if (
            not isinstance(failures, list)
            or not failures
            or not all(isinstance(item, str) and item.strip() for item in failures)
        ):
            raise ValueError(
                f"capability {name!r} field 'failure_modes' must be a non-empty string list"
            )
        if not isinstance(raw_entry.get("callable_from_peers"), bool):
            raise ValueError(
                f"capability {name!r} field 'callable_from_peers' must be boolean"
            )
        if name == "turn_steer":
            authorization = raw_entry.get("authorization")
            if authorization not in {"lead_only", "any_peer"}:
                raise ValueError(
                    "capability 'turn_steer' requires authorization "
                    "'lead_only' or 'any_peer'"
                )
        if name == "live_tool_events":
            native_tools = raw_entry.get("native_host_tools")
            if native_tools is not None:
                if (
                    not isinstance(native_tools, list)
                    or not all(
                        isinstance(item, str) and item.strip() for item in native_tools
                    )
                ):
                    raise ValueError(
                        "capability 'live_tool_events' field 'native_host_tools' "
                        "must be a list of non-empty strings"
                    )
        validated[name] = raw_entry
    return validated


def manifest_accepts_peer_steer(manifest: Any) -> bool:
    """Return whether a rich Agent Card authorizes non-lead peer steer.

    The protocol has carried this bit in a few compatible shapes while the
    R11/R12 split settled:

    - top-level ``accepts_peer_steer: true`` from prototype Agent Cards,
    - rich ``capabilities.accepts_peer_steer`` entries,
    - ``turn_steer.authorization == "any_peer"`` /
      ``turn_steer.callable_from_peers == true``.

    Explicit false wins over any looser shape.  A plain ``turn_steer`` entry
    by itself is not peer authorization; lead-only steer-capable backends also
    declare that primitive.
    """

    if not isinstance(manifest, dict):
        return False

    top_level = manifest.get("accepts_peer_steer")
    if isinstance(top_level, bool):
        return top_level

    capabilities = manifest.get("capabilities")
    if isinstance(capabilities, list):
        return "accepts_peer_steer" in capabilities
    if not isinstance(capabilities, dict):
        return False

    accepts = capabilities.get("accepts_peer_steer")
    if isinstance(accepts, bool):
        return accepts
    if isinstance(accepts, dict):
        if accepts.get("enabled") is False or accepts.get("value") is False:
            return False
        return True
    if accepts is not None:
        return True

    turn_steer = capabilities.get("turn_steer")
    if isinstance(turn_steer, dict):
        nested_accepts = turn_steer.get("accepts_peer_steer")
        if isinstance(nested_accepts, bool):
            return nested_accepts
        if turn_steer.get("authorization") == "any_peer":
            return True
        if turn_steer.get("callable_from_peers") is True:
            return True

    return False


def peer_steer_authorized(
    capabilities: list[str] | tuple[str, ...],
    manifest: Any | None = None,
) -> bool:
    """Return whether the recipient currently authorizes peer steer.

    When a rich manifest is available it is authoritative.  The cheap
    ``accepts_peer_steer`` flag remains a fallback for focused unit tests and
    older adapters that have not loaded an Agent Card yet.
    """

    if isinstance(manifest, dict):
        return manifest_accepts_peer_steer(manifest)
    return "accepts_peer_steer" in capabilities


def effective_peer_steer_capabilities(
    capabilities: list[str] | tuple[str, ...],
    manifest: Any | None = None,
) -> list[str]:
    """Return cheap capability flags with manifest peer-steer policy applied."""

    result = [cap for cap in capabilities if cap != "accepts_peer_steer"]
    if peer_steer_authorized(capabilities, manifest):
        result.append("accepts_peer_steer")
    return result


def rich_capability_manifest(
    capabilities: list[str],
    *,
    delivery_mode: str | None = None,
    expiry_semantics: str | None = None,
    steer_authorization: str | None = None,
    host_tool_surface: str | None = None,
    native_host_tools: list[str] | tuple[str, ...] | None = None,
) -> dict[str, dict[str, Any]]:
    """Return R12 rich entries for the supplied R11 cheap capability list.

    The keys intentionally align one-for-one with ``CAPABILITY_NAMES`` and the
    per-backend ``*_CAPABILITIES`` constants so 09 R12 never invents a second
    naming layer on top of codex-impl-cap's R11 surface.

    ``native_host_tools`` enumerates the routed harness's native tool names
    that live OUTSIDE the wrapper-MCP surface (e.g. Codex App Server's
    ``imagegeneration`` / ``imageview`` / ``websearch`` / ``filechange``).
    Surfacing them on the ``live_tool_events`` entry is the §1-correct shape:
    each backend declares its own native inventory natively; peers and leads
    discover the inventory by querying the manifest, not by reading hardcoded
    lists in skill text.
    """
    assert_known_capabilities(capabilities)
    result: dict[str, dict[str, Any]] = {}
    for name in capabilities:
        entry = deepcopy(_BASE_CAPABILITY_MANIFEST[name])
        if name == "turn_steer":
            steer_authorization = steer_authorization or "lead_only"
            if delivery_mode:
                entry["delivery_mode"] = delivery_mode
            if expiry_semantics:
                entry["expiry_semantics"] = expiry_semantics
            entry["authorization"] = steer_authorization
            entry["callable_from_peers"] = steer_authorization == "any_peer"
        if name == "live_tool_events":
            if host_tool_surface:
                entry["host_tool_surface"] = host_tool_surface
            if native_host_tools:
                # Preserve per-backend declaration order; peers and leads read
                # this list as the canonical inventory of harness-native tools
                # (i.e. tools NOT exposed via the wrapper MCP).
                entry["native_host_tools"] = [str(t) for t in native_host_tools]
        result[name] = entry
    return validate_capability_manifest_entries(result)


def build_agent_card(
    *,
    team_name: str,
    agent_name: str,
    agent_id: str,
    agent_type: str,
    model: str,
    backend_type: str,
    capabilities: list[str],
    capability_manifest: dict[str, dict[str, Any]] | None = None,
    capability_version: str = CAPABILITY_MANIFEST_VERSION,
    transport: str | None = None,
    host_tool_surface: str | None = None,
    coupling_regime: str | None = None,
) -> dict[str, Any]:
    """Build the rich R12 Agent Card persisted under ``manifests/<agent>.json``."""
    entries = (
        validate_capability_manifest_entries(capability_manifest)
        if capability_manifest is not None
        else rich_capability_manifest(capabilities)
    )
    card = {
        "schema_version": CAPABILITY_MANIFEST_SCHEMA_VERSION,
        "capability_version": str(capability_version),
        "team_name": team_name,
        "agent_name": agent_name,
        "agent_id": agent_id,
        "agent_type": agent_type,
        "model": model,
        "backend_type": backend_type,
        "transport": transport or backend_type,
        "host_tool_surface": host_tool_surface,
        "capabilities": entries,
    }
    if coupling_regime is not None:
        regime, coupling = declaration_for_regime(coupling_regime)
        # Root-level field is the compact protocol declaration used by the
        # dispatcher conflict check. The canonical object preserves the
        # longer intent/sub-field shape used by scorers and workload manifests.
        card["coupling_regime"] = regime
        card["coupling"] = coupling
    return card


def _entry_text(entry: dict[str, Any], key: str) -> str:
    value = entry.get(key)
    return str(value).strip() if value is not None else ""


def _failure_modes_text(entry: dict[str, Any]) -> str:
    value = entry.get("failure_modes")
    if isinstance(value, list):
        modes = [str(v).strip() for v in value if str(v).strip()]
        return ", ".join(modes[:6])
    if value is not None:
        return str(value).strip()
    return ""


def _peer_capability_block(peer: str, capability: str, entry: dict[str, Any]) -> str:
    lines = [f"## {peer}: {capability}"]
    description = _entry_text(entry, "description")
    when_to_use = _entry_text(entry, "when_to_use")
    when_not_to = _entry_text(entry, "when_not_to")
    failure_modes = _failure_modes_text(entry)
    if description:
        lines.append(f"- What: {description}")
    if when_to_use:
        lines.append(f"- When to use: {when_to_use}")
    if when_not_to:
        lines.append(f"- When not to use: {when_not_to}")
    if failure_modes:
        lines.append(f"- Failure modes: {failure_modes}")
    for key, label in (
        ("delivery_mode", "Delivery mode"),
        ("expiry_semantics", "Expiry semantics"),
        ("authorization", "Authorization"),
    ):
        value = _entry_text(entry, key)
        if value:
            lines.append(f"- {label}: {value}")
    return "\n".join(lines)


def peer_prompt_fragment(agent_name: str, card: dict[str, Any]) -> str:
    """Return the R14 prompt fragment for one peer's rich Agent Card.

    The fragment teaches peer agents both what capabilities exist and when
    invoking/routing through that peer is useful.  It intentionally includes
    informational and lead-gated capabilities as well as directly callable
    ones: even non-callable features shape routing decisions and peer
    expectations (§3 peer efficiency).
    """
    caps = card.get("capabilities", {}) if isinstance(card, dict) else {}
    if not isinstance(caps, dict):
        return ""
    blocks: list[str] = []
    coupling = card.get("coupling")
    coupling_regime = card.get("coupling_regime")
    if isinstance(coupling, dict) or coupling_regime:
        intent = coupling.get("intent") if isinstance(coupling, dict) else None
        parts = []
        if coupling_regime:
            parts.append(f"regime={coupling_regime}")
        if intent:
            parts.append(f"intent={intent}")
        blocks.append(
            f"## {agent_name}: coupling intent declaration\n"
            f"- Declaration: {', '.join(parts)}\n"
            f"- Use this as routing guidance only; each backend interprets "
            f"coupling natively."
        )
    for capability, entry in sorted(caps.items()):
        if not isinstance(entry, dict):
            continue
        block = _peer_capability_block(agent_name, str(capability), entry)
        if block:
            blocks.append(block)
    manifest_lookup = (
        f"## {agent_name}: REQUIRED capability lookup before peer steering\n"
        f"- ACTION REQUIRED: MUST query mcp_anyteam_capability_manifest "
        f"before any peer-steer attempt to {agent_name}. Call it as "
        f"`mcp_anyteam_capability_manifest('{agent_name}', '<primitive>')` "
        f"for primitives such as `turn_steer`; do this even when the "
        f"capability summary below appears to mention the primitive.\n"
        f"- Use the manifest response to verify acceptance "
        f"(`callable_from_peers`/authorization) and review "
        f"delivery_mode/expiry_semantics before sending the steer.\n"
        f"- Consequence: if you skip the manifest query, peer steers will be "
        f"rejected, you waste a turn, the peer pays about 5s of rejection "
        f"cost, and the run emits visibility_degraded noise. If the manifest "
        f"does not explicitly allow peer steering, route the request through "
        f"team-lead instead."
    )
    return "\n\n".join([manifest_lookup, *blocks])


def peer_prompt_fragments_for(requester: str, cache: Any) -> str:
    """Aggregate peer-capability prompt fragments for the requesting agent.

    Skips the requester's own card and capabilities already present on the
    requester, then concatenates one fragment per peer using the cache's known
    rich Agent Cards.
    """
    cards = getattr(cache, "cards", None)
    if cards is None:
        cards = getattr(cache, "manifests", {})
    if not isinstance(cards, dict):
        return ""

    requester_card = cards.get(requester)
    requester_caps: set[str] = set()
    if isinstance(requester_card, dict):
        caps = requester_card.get("capabilities", {})
        if isinstance(caps, dict):
            requester_caps = set(caps)

    parts: list[str] = []
    for peer_name in sorted(cards):
        if peer_name == requester:
            continue
        card = cards[peer_name]
        if not isinstance(card, dict):
            continue
        caps = card.get("capabilities", {})
        if not isinstance(caps, dict):
            continue
        peer_unique = {
            capability: entry
            for capability, entry in caps.items()
            if capability not in requester_caps
        }
        peer_card = dict(card)
        peer_card["capabilities"] = peer_unique
        fragment = peer_prompt_fragment(peer_name, peer_card)
        if fragment:
            parts.append(fragment)
    if not parts:
        return ""
    return "# Capabilities of your peers\n\n" + "\n\n".join(parts)
