"""Codex CLI invocation.

Wraps `codex exec` with `--json` (JSONL progress events) and optionally
`--output-schema` (schema-constrained final response). One entrypoint:

- `run(prompt, cwd, schema=...)` — runs a single `codex exec` subprocess
  with `--full-auto --json [--output-schema <schema>]`. Returns a
  `CodexResult` with exit code, the parsed structured response (when a
  schema was passed), the raw last message, and the JSONL event stream.

The control loop uses this for two purposes:
- Task completion (`schema=TASK_COMPLETE_SCHEMA`) — get Codex to do the
  work and produce a `{files_changed, summary}` response.
- Plan generation (`schema=PLAN_SCHEMA`) — opt-in plan-mode path; Codex
  drafts a structured plan for lead approval without executing work.

The adapter never parses free-form prose from Codex. If the `--output-schema`
pathway fails to produce conforming output, `run()` surfaces it via
`CodexResult.structured is None` / `CodexResult.error is not None` and the
control loop decides what to do (retry, mark blocked, etc.).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any, Callable

from . import logger
from .env import (
    APP_SERVER_INITIALIZE_PROGRESS_INTERVAL_ENV,
    APP_SERVER_INITIALIZE_TIMEOUT_ENV,
    CWD_ENV,
    DUMP_EVENTS_ENV,
    LEGACY_CWD_ENV,
    LEGACY_DUMP_EVENTS_ENV,
    TEAM_ENV,
    LEGACY_TEAM_ENV,
    NAME_ENV,
    LEGACY_NAME_ENV,
    identity_env,
    env_first,
)
from .codex_log_bloat import (
    checkpoint_bloated_codex_wals,
    inspect_codex_log_bloat,
)
from .headless_visibility import HeadlessTurnVisibility, coerce_stream_text
from .messages import VisibilityEvent
from .prompts import TEAM_MESSAGING_BLOCK
from .wrapper_tool_failure import (
    WRAPPER_TOOL_RECOVERY_EVENT_KINDS,
    is_wrapper_tool_recovery_event_kind,
    visibility_event_counts_as_wrapper_tool_recovery,
)
from .wrapper_mcp_diagnostics import append_wrapper_mcp_diagnostic

# R1 (09 §3.1): schema files are versioned wire
# assets, so resolve them as package resources instead of walking relative to
# this source file. This keeps fresh wheel installs from depending on a
# repository checkout layout.
SCHEMAS_DIR = resources.files("claude_anyteam.schemas")
TASK_COMPLETE_SCHEMA = SCHEMAS_DIR / "task-complete.schema.json"
PLAN_SCHEMA = SCHEMAS_DIR / "plan.schema.json"
SchemaResource = Path | Traversable


@dataclass(frozen=True)
class CodexResult:
    exit_code: int
    structured: dict[str, Any] | None
    last_message: str
    events: list[dict[str, Any]]
    error: str | None = None
    tool_call_events: int = 0  # count of MCP/tool-call events seen in the JSONL stream
    # v7.2+/v7.3: thread/session id captured from `thread.started` (or
    # equivalent) events in the JSONL stream. Used as the
    # `resume_session_id` for CLI resume mode and as the
    # `resume_thread_id` for App Server lineage on subsequent tasks.
    # None if the event was absent.
    session_id: str | None = None


# Substrings we match in the `type` field of `codex exec --json` events to
# identify MCP/tool invocations. Codex emits both snake_case (`mcp_tool_call`)
# and PascalCase (`McpToolCall`, `McpToolCallProgress`) shapes depending on
# version and transport; we normalise by stripping underscores+dots and
# lower-casing, so both forms hit the same substring.
#
# Host-tool coverage: Codex App Server also emits item types for host-side
# work — `commandExecution` (shell), `fileChange` (file writes), `webSearch`
# — which are NOT MCP tool calls but ARE meaningful work. Counting them
# closes the visibility gap where a turn writes files / runs shell yet
# `tool_call_events: 0` (B6/B9 finding; see
# `bug-triage/B9-visibility-parity-investigation.md` §1).
_TOOL_CALL_TYPE_SUBSTRINGS = (
    "mcptoolcall",         # matches `mcp_tool_call` *and* `McpToolCall*`
    "toolcall",            # generic tool-call events
    "tooluse",             # anthropic-style `tool.use`
    "functioncall",        # openai-style `function_call`
    "commandexecution",    # Codex App Server host shell
    "filechange",          # Codex App Server host file writes
    "websearch",           # Codex App Server web search
    "imagegeneration",     # Codex App Server image generation
    "imagegen",            # observed/possible abbreviated image tool spelling
    "imageview",           # Codex App Server image view
)

# Keep this diagnostic snapshot in sync with wrapper_server.EXPOSED_TOOLS.
# Duplicated here intentionally so codex.py can log session-start state
# without importing FastMCP/wrapper_server on the hot path.
_WRAPPER_EXPECTED_TOOL_NAMES = (
    "send_message",
    "task_update",
    "checkpoint_commit",
    "task_create",
    "task_batch_summary",
    "read_inbox",
    "task_list",
    "read_config",
    "mcp_anyteam_capability_manifest",
    "mcp_anyteam_list_skills",
    "mcp_anyteam_invoke_skill",
    "mcp_anyteam_shell",
    "mcp_anyteam_read_file",
    "mcp_anyteam_write_file",
    "mcp_anyteam_list_directory",
    "mcp_anyteam_edit_file",
    "mcp_anyteam_search",
    "mcp_anyteam_grep",
    "mcp_anyteam_web_fetch",
)

# Tool names our wrapper MCP server advertises. If any event payload
# references one of these by name, it's a tool call — regardless of how
# Codex spells the event type. Keep this as the set form of
# _WRAPPER_EXPECTED_TOOL_NAMES so #49 shadow-tool failures (for example
# mcp_anyteam_read_file ENOENT) participate in the same recovery logic as
# core team tools.
_WRAPPER_TOOL_NAMES = frozenset(_WRAPPER_EXPECTED_TOOL_NAMES)

@dataclass
class _PendingWrapperToolFailure:
    failed_event_id: str
    failed_event_seq: int
    failed_at_monotonic: float
    tool_name: str
    error_class: str
    error_detail: str | None
    turn_id: str | None
    recovered_at_monotonic: float | None = None
    recovery_event_id: str | None = None
    emitted: bool = False



def _bounded_preview(value: Any, *, limit: int = 1000) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= limit else value[: limit - 1] + "…"
    if isinstance(value, (list, tuple)):
        return [_bounded_preview(item, limit=limit) for item in list(value)[:30]]
    if isinstance(value, dict):
        return {
            str(k): _bounded_preview(v, limit=limit)
            for k, v in list(value.items())[:50]
        }
    return _bounded_preview(repr(value), limit=limit)


def _wrapper_tool_error_detail(payload: dict[str, Any]) -> str | None:
    """Best-effort bounded detail string for a failed wrapper-tool event."""

    for key in (
        "error",
        "error_detail",
        "stderr_preview",
        "stdout_preview",
        "raw_event_preview",
    ):
        value = payload.get(key)
        if value not in (None, ""):
            return str(_bounded_preview(value, limit=600))
    return None


def _wrapper_tool_error_class(payload: dict[str, Any]) -> str:
    """Return a stable-ish mid-turn wrapper-tool error discriminator.

    This deliberately does not feed diagnostics.ERROR_CLASSES: #49's
    unrecovered envelope is an in-flight visibility signal, not a terminal
    turn failure. The class here is forensic payload only.
    """

    detail = " ".join(
        str(value)
        for value in (
            payload.get("status"),
            payload.get("error"),
            payload.get("stderr_preview"),
            payload.get("raw_event_preview"),
        )
        if value not in (None, "")
    ).lower()
    if any(fragment in detail for fragment in ("errno 2", "enoent", "no such file")):
        return "enoent"
    if any(fragment in detail for fragment in ("validation", "schema")):
        return "schema_validation"
    if "timeout" in detail or "timed out" in detail:
        return "timeout"
    if "not found" in detail:
        return "not_found"
    status = payload.get("status")
    if isinstance(status, str) and status:
        return status
    return "wrapper_tool_error"


def _is_failed_wrapper_tool_event(event: VisibilityEvent | None) -> bool:
    if event is None or event.kind != "tool_event" or event.severity != "error":
        return False
    tool_name = event.payload.get("tool_name")
    return isinstance(tool_name, str) and tool_name in _WRAPPER_TOOL_NAMES


def _toolish_fields(value: Any, *, depth: int = 0, path: str = "") -> dict[str, Any]:
    """Extract bounded tool/MCP-looking fields from App Server responses."""

    if depth > 5:
        return {}
    found: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            key_l = str(key).lower()
            if any(fragment in key_l for fragment in ("tool", "mcp", "server")):
                found[child_path] = _bounded_preview(child)
            found.update(_toolish_fields(child, depth=depth + 1, path=child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value[:20]):
            found.update(_toolish_fields(child, depth=depth + 1, path=f"{path}[{index}]"))
    return found


def _log_codex_tool_snapshot(
    *,
    team: str | None,
    agent: str | None,
    event: str,
    payload: dict[str, Any],
) -> None:
    if not team or not agent:
        return
    append_wrapper_mcp_diagnostic(
        team=team,
        agent=agent,
        event=event,
        payload={
            "expected_wrapper_tools": list(_WRAPPER_EXPECTED_TOOL_NAMES),
            **payload,
        },
    )


def _normalise(s: str) -> str:
    return s.replace("_", "").replace(".", "").replace("-", "").lower()


def _event_mentions_wrapper_tool(ev: dict[str, Any]) -> bool:
    """True if the event carries (top-level or nested) a wrapper tool name."""
    for key in ("name", "tool", "tool_name", "function_name"):
        val = ev.get(key)
        if isinstance(val, str) and val in _WRAPPER_TOOL_NAMES:
            return True
    item = ev.get("item")
    if isinstance(item, dict):
        for key in ("name", "tool", "tool_name", "function_name"):
            val = item.get(key)
            if isinstance(val, str) and val in _WRAPPER_TOOL_NAMES:
                return True
    return False


def _is_tool_call_event(ev_type: str, ev: dict[str, Any]) -> bool:
    n = _normalise(ev_type)
    for frag in _TOOL_CALL_TYPE_SUBSTRINGS:
        if frag in n:
            return True
    # Envelope `item.*` / `Item*` events whose nested item has a tool-call shape.
    if n.startswith("item") and isinstance(ev.get("item"), dict):
        inner = _normalise(str(ev["item"].get("type", "")))
        if any(frag in inner for frag in _TOOL_CALL_TYPE_SUBSTRINGS):
            return True
    # Backstop: any event that mentions one of our wrapper tools by name.
    if _event_mentions_wrapper_tool(ev):
        return True
    return False


def _tool_name_from_event(ev: dict[str, Any]) -> str | None:
    """Best-effort tool-name extraction from a Codex JSONL tool-call event.

    Returns None if the event doesn't carry a recognizable tool name; the
    raw event is still logged by the caller so forensics aren't lost.
    """
    for key in ("name", "tool", "tool_name", "function_name"):
        val = ev.get(key)
        if isinstance(val, str) and val:
            return val
    item = ev.get("item")
    if isinstance(item, dict):
        for key in ("name", "tool", "tool_name", "function_name"):
            val = item.get(key)
            if isinstance(val, str) and val:
                return val
    return None


def _parse_exec_stdout(
    stdout: str,
    *,
    dump_events: bool = False,
) -> tuple[list[dict[str, Any]], int, str | None]:
    """Parse Codex exec JSONL without flattening the raw event stream."""

    events: list[dict[str, Any]] = []
    tool_call_events = 0
    captured_session_id: str | None = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            logger.debug("codex.nonjson_line", line=line[:200])
            continue
        events.append(ev)
        ev_type = str(ev.get("type", ""))
        if dump_events:
            logger.debug("codex.event", event_type=ev_type, event=ev)
        if ev_type == "thread.started" and captured_session_id is None:
            tid = ev.get("thread_id")
            if isinstance(tid, str) and tid:
                captured_session_id = tid
                logger.info("codex.session_captured", session_id=tid)
        if _is_tool_call_event(ev_type, ev):
            tool_call_events += 1
            logger.info(
                "codex.tool_call",
                event_type=ev_type,
                tool=_tool_name_from_event(ev),
                event=ev,
            )
    return events, tool_call_events, captured_session_id


def _last_message_is_task_complete_json(last_message: str) -> bool:
    """Best-effort terminal-digest classification for resume-path output."""

    try:
        payload = json.loads(last_message)
    except json.JSONDecodeError:
        return False
    return (
        isinstance(payload, dict)
        and "files_changed" in payload
        and "summary" in payload
    )


def wrapper_mcp_config_args(
    team: str,
    agent_name: str,
    *,
    server_name: str = "claude_anyteam_wrapper",
    wrapper_binary: str = "claude-anyteam-wrapper",
    cwd: str | Path | None = None,
) -> list[str]:
    """Build the `-c mcp_servers.<name>.*` overrides that point Codex at our
    narrowed wrapper MCP server for a single `codex exec` invocation.

    We use inline `-c` overrides rather than `codex mcp add` (which would
    mutate `~/.codex/config.toml`) so the MCP wiring is ephemeral and
    adapter-scoped — matching the v6 invariant that user config is never
    touched. See architecture doc §4.

    `CLAUDE_ANYTEAM_TEAM` and `CLAUDE_ANYTEAM_NAME` are passed via env
    (handled in `run()`), so the wrapper picks up identity at subprocess
    spawn without us leaking it into `~/.codex/config.toml`.

    We resolve `wrapper_binary` to its absolute path via `shutil.which`
    when we can. This defends against Codex's subprocess environment not
    inheriting our `.venv/bin` on PATH — a brittle assumption that bit
    us during earlier integration work. If `shutil.which` returns None
    we fall through to the bare name and let Codex's spawn fail loudly.

    No sandbox carve-out is emitted here: the adapter runs Codex with
    `--dangerously-bypass-approvals-and-sandbox` (see `run()` for the
    rationale), which supersedes any `sandbox_workspace_write.writable_roots`
    config. Keeping this helper narrowly scoped to the MCP wiring makes
    its single responsibility obvious.
    """
    resolved = shutil.which(wrapper_binary) or wrapper_binary
    prefix = f"mcp_servers.{server_name}"
    wrapper_args: list[str] = []
    if cwd is not None:
        wrapper_args += ["--cwd", str(cwd)]
    return [
        "-c", f'{prefix}.command="{resolved}"',
        "-c", f"{prefix}.args={json.dumps(wrapper_args)}",
    ]


def feature_test(
    codex_binary: str = "codex",
    *,
    mcp_probe: bool = False,
    team: str | None = None,
    agent_name: str | None = None,
) -> None:
    """Fail fast at adapter startup if the Codex binary is missing or too old.

    When ``mcp_probe=True``, also verify that Codex can successfully spawn
    the narrowed wrapper MCP server and list its tools. Requires `team` and
    `agent_name` so the wrapper has identity; pass the adapter's Settings
    values.

    Raises RuntimeError with a user-facing message on any problem.
    """
    resolved = shutil.which(codex_binary)
    if not resolved:
        raise RuntimeError(
            f"codex binary not found on PATH (expected {codex_binary!r}). "
            "Install OpenAI Codex CLI and retry."
        )
    try:
        out = subprocess.run(
            [codex_binary, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (subprocess.SubprocessError, OSError) as e:
        raise RuntimeError(f"could not run {codex_binary} --version: {e}") from e

    version = (out.stdout or out.stderr).strip()
    logger.info("codex.version", binary=resolved, version=version)

    # Probe the flags we depend on exist in this version.
    help_out = subprocess.run(
        [codex_binary, "exec", "--help"],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    required_flags = ["--json", "--output-schema", "--output-last-message"]
    missing = [f for f in required_flags if f not in help_out.stdout]
    if missing:
        raise RuntimeError(
            f"codex exec is missing required flags: {missing}. "
            f"Upgrade Codex CLI (found version: {version})."
        )

    if mcp_probe:
        if not team or not agent_name:
            raise RuntimeError("mcp_probe=True requires team and agent_name")
        _probe_wrapper_mcp(codex_binary, team=team, agent_name=agent_name)


def _probe_wrapper_mcp(codex_binary: str, *, team: str, agent_name: str) -> None:
    """Verify Codex can spawn the wrapper and list its tools.

    Strategy: run a trivial `codex exec` with our MCP config overrides
    and a prompt that asks Codex to call one of the safe tools
    (`read_config`) and report the team name. If Codex fails to spawn
    the wrapper or can't reach the tool, the subprocess exits nonzero
    or produces no schema-conformant response, and we fail closed.

    Not implemented as a real codex-exec round-trip here — that burns
    API tokens on every adapter startup. Instead we (a) confirm the
    wrapper binary itself is installed and starts, and (b) confirm the
    `codex mcp` config surface accepts our override shape without
    complaint. The first real `codex exec` invocation in M5 will prove
    end-to-end tool visibility.
    """
    resolved_wrapper = shutil.which("claude-anyteam-wrapper")
    if not resolved_wrapper:
        raise RuntimeError(
            "claude-anyteam-wrapper not on PATH. Ensure the adapter is installed "
            "in this environment (e.g. `uv sync` or `pip install -e .`)."
        )
    # Confirm the wrapper can be imported with the given identity — if the
    # identity env is wrong, fail here rather than inside Codex's subprocess
    # where the error would be buried.
    probe_env = {
        **os.environ,
        **identity_env(os.environ, team=team, name=agent_name),
        "PYTHONUNBUFFERED": "1",
    }
    try:
        r = subprocess.run(
            [
                "python",
                "-c",
                "from claude_anyteam.wrapper_server import build_server; "
                "build_server(); print('OK')",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            env=probe_env,
            check=False,
        )
    except (subprocess.SubprocessError, OSError) as e:
        raise RuntimeError(f"wrapper import probe failed to execute: {e}") from e
    if r.returncode != 0 or "OK" not in r.stdout:
        raise RuntimeError(
            f"wrapper build_server() probe failed: rc={r.returncode} "
            f"stderr={r.stderr[:300]!r}"
        )
    logger.info(
        "codex.mcp_probe_ok",
        wrapper_binary=resolved_wrapper,
        team=team,
        agent_name=agent_name,
    )


def run(
    prompt: str,
    *,
    cwd: Path,
    schema: SchemaResource | None = None,
    codex_binary: str = "codex",
    timeout_s: float = 600.0,
    extra_args: list[str] | None = None,
    wrapper_identity: tuple[str, str] | None = None,
    resume_session_id: str | None = None,
    model: str | None = None,
    effort: str | None = None,
    task_id: str | None = None,
) -> CodexResult:
    """Run `codex exec` with structured JSON output.

    Returns a CodexResult. Never raises on nonzero exit — the control loop
    inspects exit_code and decides on retry/block.

    When `wrapper_identity=(team_name, agent_name)` is supplied, the
    subprocess is launched with `CLAUDE_ANYTEAM_TEAM` and
    `CLAUDE_ANYTEAM_NAME` env vars set to that identity. This is how the
    wrapper MCP server (launched as a Codex subprocess of a subprocess
    via `-c mcp_servers.claude_anyteam_wrapper.command=...`) picks up
    which team/agent it's scoping to. Pair with `wrapper_mcp_config_args()`
    passed in `extra_args`.

    **v7.2 (cross-task context via session resume):** when
    `resume_session_id` is provided, the subprocess invokes
    `codex exec resume <session_id>` instead of fresh `codex exec`. The
    session id comes from a prior run's `thread.started` event (captured
    into `CodexResult.session_id`). Caveats of the resume path on
    codex-cli 0.122.0 (documented live in `docs/v7.2-notes.md`):

    - `--output-schema` is NOT accepted by `resume`. Callers that need
      schema-constrained output must validate the `--output-last-message`
      file contents in Python (see `validate_output_or_retry` in
      `claude_anyteam.schema_validation` for the standard pathway).
    - `-C/--cd` is NOT accepted by `resume`. The task's working directory
      is inherited from the adapter's own cwd. Callers that need a
      different cwd must communicate that via the prompt itself.
    - `schema` is silently ignored when `resume_session_id` is set.
    """
    last_msg_file = tempfile.NamedTemporaryFile(
        mode="w", delete=False, prefix="codex-last-", suffix=".txt"
    )
    last_msg_file.close()
    last_msg_path = Path(last_msg_file.name)

    # `--dangerously-bypass-approvals-and-sandbox` disables Codex's sandbox
    # entirely and skips all approval prompts. We use this deliberately:
    #
    # 1. The adapter is operator-run and Codex operates in the user's own
    #    trust envelope — the user already runs Codex autonomously in their
    #    own dev loop. The sandbox was adding friction without adding
    #    security in this deployment.
    # 2. With sandbox on (even `workspace-write`), the wrapper MCP server —
    #    which Codex spawns as a subprocess and which inherits Codex's
    #    sandbox — silently fails to write to `~/.claude/tasks/` and
    #    `~/.claude/teams/*/inboxes/`. Path 1 (a `writable_roots` carve-out)
    #    worked but the user prefers disabling the sandbox outright.
    # 3. Codex's own help text describes this flag as "intended solely for
    #    running in environments that are externally sandboxed." An
    #    operator-run Codex teammate is effectively that: externally
    #    sandboxed by the operator's choice of what to run it against.
    model_effort_overrides: list[str] = []
    if model is not None:
        model_effort_overrides += ["-c", f'model="{model}"']
    if effort is not None:
        model_effort_overrides += ["-c", f'model_reasoning_effort="{effort}"']

    if resume_session_id is not None:
        # v7.2 resume path: fewer flags, no schema, no cwd override.
        args = [
            codex_binary,
            "exec",
            "resume",
            resume_session_id,
            "--json",
            "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox",
            "--output-last-message", str(last_msg_path),
        ]
        args += model_effort_overrides
        if extra_args:
            args.extend(extra_args)
        args.append(prompt)
    else:
        args = [
            codex_binary,
            "exec",
            "--json",
            "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox",
        ]
        args += ["--output-last-message", str(last_msg_path)]
        if schema is not None:
            args += ["--output-schema", str(schema)]
        args += ["-C", str(cwd)]
        args += model_effort_overrides
        if extra_args:
            args.extend(extra_args)
        args.append(prompt)

    visibility_team, visibility_agent = wrapper_identity or ("default", "codex")
    sub_env = None
    if wrapper_identity is not None:
        team_name, agent_name = wrapper_identity
        sub_env = identity_env(os.environ, team=team_name, name=agent_name)
        sub_env[CWD_ENV] = str(cwd)
        sub_env[LEGACY_CWD_ENV] = str(cwd)
        if task_id is not None:
            sub_env["CLAUDE_ANYTEAM_TASK_ID"] = str(task_id)

    logger.info(
        "codex.invoke",
        cwd=str(cwd),
        schema=str(schema) if schema else None,
        mcp_wrapper=bool(wrapper_identity),
    )
    _log_codex_tool_snapshot(
        team=visibility_team,
        agent=visibility_agent,
        event="codex_exec_session_start",
        payload={
            "cwd": str(cwd),
            "schema": str(schema) if schema else None,
            "task_id": task_id,
            "resume_session_id": resume_session_id,
            "model": model,
            "effort": effort,
            "extra_args": list(extra_args or []),
            "mcp_wrapper_configured": bool(wrapper_identity),
        },
    )

    events: list[dict[str, Any]] = []
    structured: dict[str, Any] | None = None
    error: str | None = None
    # `CLAUDE_ANYTEAM_DUMP_EVENTS=1` logs every JSONL event at debug level.
    # Useful while tuning the tool-call classifier against a new Codex
    # release whose event-type names we haven't seen before.
    dump_events = env_first(os.environ, DUMP_EVENTS_ENV, LEGACY_DUMP_EVENTS_ENV) == "1"
    visibility = HeadlessTurnVisibility.start(
        team=visibility_team,
        agent=visibility_agent,
        backend="codex_exec",
        enabled=wrapper_identity is not None,
        cwd=cwd,
        schema=schema,
        timeout_s=timeout_s,
        model=model,
        effort=effort,
        resume_session_id=resume_session_id,
        task_id=task_id,
    )

    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
            env=sub_env,
            # Reviewer surfaced this during task #5 plan-mode probe: without an
            # explicit stdin, Codex exec retries stall on "Reading additional
            # input from stdin..." when the plan-generation codepath trips
            # whatever heuristic looks for one. Wiring to DEVNULL is cheap and
            # correct for every invocation we make — prompts are on argv, not
            # stdin.
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as e:
        error = f"codex exec timed out after {timeout_s}s"
        logger.error("codex.timeout", timeout_s=timeout_s)
        events, tool_call_events, captured_session_id = _parse_exec_stdout(
            coerce_stream_text(getattr(e, "stdout", None) or getattr(e, "output", None)),
            dump_events=dump_events,
        )
        last_message = ""
        if last_msg_path.exists():
            try:
                last_message = last_msg_path.read_text(encoding="utf-8").strip()
            finally:
                last_msg_path.unlink(missing_ok=True)
        else:
            last_msg_path.unlink(missing_ok=True)
        visibility.terminal(
            success=False,
            exit_code=124,
            error=error,
            events=events,
            tool_call_events=tool_call_events,
            last_message=last_message,
            structured=_last_message_is_task_complete_json(last_message),
            partial_events_available=bool(events),
            session_id=captured_session_id,
            error_class="turn_timeout",
            extra_payload={"tool_call_event_source": "codex exec JSONL classifier"},
        )
        return CodexResult(
            exit_code=124,
            structured=None,
            last_message=last_message,
            events=events,
            error=error,
            tool_call_events=tool_call_events,
            session_id=captured_session_id,
        )

    # Parse JSONL events; the last object that contains our schema fields is
    # the structured response (Codex emits it as an item.message with the
    # schema-constrained JSON).
    #
    # Also surface any event that looks like an MCP/tool call as a structured
    # log line. This is M5's acceptance evidence: "adapter log shows at least
    # one mid-task tool call by Codex." Exact event type names differ by Codex
    # version (`item.tool_call_begin`, `mcp_tool_call`, `function_call`,
    # `tool.use`, etc.), so we match a broad set and include the full event
    # in the log for forensics.
    # v7.2: capture `thread_id` from the `thread.started` event so callers
    # can pass it as `resume_session_id` on subsequent tasks for the same
    # teammate identity. Observed shape on codex-cli 0.122.0:
    #   {"type":"thread.started","thread_id":"<uuid>"}
    events, tool_call_events, captured_session_id = _parse_exec_stdout(
        proc.stdout,
        dump_events=dump_events,
    )

    last_message = ""
    if last_msg_path.exists():
        try:
            last_message = last_msg_path.read_text(encoding="utf-8").strip()
        finally:
            last_msg_path.unlink(missing_ok=True)

    if schema is not None and last_message:
        try:
            structured = json.loads(last_message)
        except json.JSONDecodeError:
            error = "codex last-message was not valid JSON despite --output-schema"
            logger.warn("codex.schema_parse_fail", last_message_head=last_message[:200])

    if proc.returncode != 0 and not error:
        error = f"codex exec exited {proc.returncode}; stderr: {proc.stderr[:500]}"

    terminal_structured = structured is not None or _last_message_is_task_complete_json(
        last_message
    )

    logger.info(
        "codex.done",
        exit_code=proc.returncode,
        events=len(events),
        structured=terminal_structured,
        tool_call_events=tool_call_events,
        session_id=captured_session_id,
        resumed=resume_session_id is not None,
    )

    success = proc.returncode == 0 and error is None
    visibility.terminal(
        success=success,
        exit_code=proc.returncode,
        error=error,
        events=events,
        tool_call_events=tool_call_events,
        last_message=last_message,
        structured=terminal_structured,
        partial_events_available=bool(events),
        session_id=captured_session_id,
        extra_payload={"tool_call_event_source": "codex exec JSONL classifier"},
    )

    return CodexResult(
        exit_code=proc.returncode,
        structured=structured,
        last_message=last_message,
        events=events,
        error=error,
        tool_call_events=tool_call_events,
        session_id=captured_session_id,
    )


# ---- v7.1: App Server path --------------------------------------------------
#
# Uses `codex app-server` (experimental JSON-RPC 2.0) instead of one-shot
# `codex exec`. The key capability this buys us is `turn/steer`: the adapter
# can inject additional input into an in-flight turn when a mid-task inbox
# message arrives. See `docs/v7-architecture.md` §4 (Option Y) and the
# v7.1 task spec.


_DEFAULT_INITIALIZE_TIMEOUT_S = 90.0
_DEFAULT_INITIALIZE_PROGRESS_INTERVAL_S = 30.0
_DEFAULT_INITIALIZE_RETRIES = 1
_DEFAULT_INITIALIZE_RETRY_BACKOFF_S = 2.0
APP_SERVER_INITIALIZE_RETRIES_ENV = "CLAUDE_ANYTEAM_APP_SERVER_INITIALIZE_RETRIES"
APP_SERVER_INITIALIZE_RETRY_BACKOFF_ENV = (
    "CLAUDE_ANYTEAM_APP_SERVER_INITIALIZE_RETRY_BACKOFF_S"
)


def _read_float_env(name: str, default: float, *, allow_zero: bool = False) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    if allow_zero:
        return max(0.0, value)
    return max(0.001, value)


def _read_int_env(name: str, default: int, *, min_value: int = 0, max_value: int = 5) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return min(max_value, max(min_value, value))


def _is_initialize_timeout_error(exc: BaseException) -> bool:
    return "did not respond to initialize" in str(exc).lower()


def _read_proc_cpu_pct(pid: int) -> float | None:
    """Best-effort CPU% sample from ``/proc/<pid>/stat`` (Linux only).

    Returns the cumulative ``utime + stime`` ratio over the process's wall
    time since fork — coarser than ``ps``'s short-window sample but
    requires no extra threads or sampling intervals. Used in
    ``app_server_initialize_progress`` payloads so the lead can see "this
    second-spawn is wedged at 100% CPU" without running ``top``. Silent
    on non-Linux (returns None) — the lead's UI just omits the field.
    """
    try:
        with open(f"/proc/{pid}/stat", "r", encoding="utf-8") as fh:
            stat = fh.read()
    except (FileNotFoundError, PermissionError, OSError):
        return None
    # Field order: pid (comm) state ppid pgrp session tty_nr tpgid flags
    # minflt cminflt majflt cmajflt utime stime cutime cstime priority
    # nice num_threads ... starttime ...
    # `comm` may contain spaces inside parentheses, so split on the
    # closing paren rather than whitespace.
    try:
        end_comm = stat.rindex(") ")
        rest = stat[end_comm + 2 :].split()
        utime = int(rest[11])  # 14 - 3
        stime = int(rest[12])  # 15 - 3
        starttime = int(rest[19])  # 22 - 3
    except (ValueError, IndexError):
        return None
    try:
        with open("/proc/uptime", "r", encoding="utf-8") as fh:
            uptime = float(fh.read().split()[0])
    except (FileNotFoundError, OSError, ValueError, IndexError):
        return None
    try:
        clock_ticks = float(os.sysconf("SC_CLK_TCK"))
    except (AttributeError, OSError, ValueError):
        clock_ticks = 100.0
    elapsed_s = uptime - (starttime / clock_ticks)
    if elapsed_s <= 0:
        return None
    cpu_s = (utime + stime) / clock_ticks
    return min(100.0 * 8, (cpu_s / elapsed_s) * 100.0)


def _initialize_with_progress_events(
    client: "Any",
    *,
    emit_visibility: Callable[..., VisibilityEvent],
    prompt_byte_size: int,
) -> Any:
    """Run JSON-RPC ``initialize`` against the App Server with timing,
    progress events, and a bounded budget.

    #40 Phase 1 deliverables, all bundled here so the App Server entry
    path has one structured handshake surface:

    * ``timeout`` defaults to 90s (override via
      ``CLAUDE_ANYTEAM_APP_SERVER_INITIALIZE_TIMEOUT_S``). The previous
      600s default was the only signal a hung initialize was hung at all
      — see #40 issue thread, "10-minute silence is indistinguishable
      from 'agent is genuinely thinking hard.'"
    * On success, emits ``app_server_initialize_completed`` with
      ``elapsed_ms`` and ``prompt_byte_size``. This is the success-path
      instrumentation the steward asked for so we can revisit the 90s
      default with real data instead of the single 17s anecdote.
    * While waiting, emits ``app_server_initialize_progress`` events at
      ``CLAUDE_ANYTEAM_APP_SERVER_INITIALIZE_PROGRESS_INTERVAL_S`` (default
      30s; set 0 to disable). Each carries ``attempt``, ``elapsed_s``,
      ``last_observed_pid``, and (Linux only) ``last_observed_cpu_pct``.

    On timeout: re-raises the underlying ``AppServerError`` so the
    surrounding ``try`` block in ``app_server_invoke`` records the
    failure via the existing `turn_failed` path. The error message is
    preserved so loop-side handlers that key on
    ``"did not respond to initialize"`` continue to work.

    ``prompt_byte_size`` source choice: callers pass
    ``len(task_prompt.encode('utf-8'))`` where ``task_prompt`` is the
    ``developer_instructions`` block we'll send to Codex on
    ``thread/start``. This is the user-supplied content that contributes
    to the prompt-side preprocessing the App Server may do before
    replying to ``initialize``. We deliberately do NOT include the
    base instructions / system prompt boilerplate in the byte-size: that
    payload is constant across turns, and the variable that
    distinguishes a fast first-cold-start from a slow one in the issue
    thread is the user's task brief size. See product-steward's #40
    Phase 1 brief, point 4.
    """
    import threading

    timeout_s = _read_float_env(
        APP_SERVER_INITIALIZE_TIMEOUT_ENV,
        _DEFAULT_INITIALIZE_TIMEOUT_S,
    )
    progress_interval_s = _read_float_env(
        APP_SERVER_INITIALIZE_PROGRESS_INTERVAL_ENV,
        _DEFAULT_INITIALIZE_PROGRESS_INTERVAL_S,
        allow_zero=True,
    )

    started_at = time.monotonic()
    result_holder: dict[str, Any] = {}
    error_holder: dict[str, BaseException] = {}

    def _worker() -> None:
        try:
            result_holder["result"] = client.initialize(timeout=timeout_s)
        except BaseException as exc:  # noqa: BLE001 — re-raised on main
            error_holder["error"] = exc

    worker = threading.Thread(
        target=_worker,
        name="app_server-initialize",
        daemon=True,
    )
    worker.start()

    attempt = 0
    while True:
        wait_s = (
            min(progress_interval_s, max(0.05, timeout_s - (time.monotonic() - started_at)))
            if progress_interval_s > 0
            else max(0.05, timeout_s - (time.monotonic() - started_at))
        )
        worker.join(timeout=wait_s)
        if not worker.is_alive():
            break
        elapsed_s = time.monotonic() - started_at
        if elapsed_s >= timeout_s:
            # The underlying request() timeout will fire on its own; let
            # the worker observe it and raise. We just stop emitting
            # progress and fall through to the join below.
            break
        if progress_interval_s <= 0:
            continue
        attempt += 1
        pid = client.pid
        cpu_pct = _read_proc_cpu_pct(pid) if isinstance(pid, int) else None
        progress_payload: dict[str, Any] = {
            "attempt": attempt,
            "elapsed_s": round(elapsed_s, 2),
            "timeout_s": timeout_s,
            "progress_interval_s": progress_interval_s,
            "prompt_byte_size": prompt_byte_size,
            "last_observed_pid": pid,
        }
        if cpu_pct is not None:
            progress_payload["last_observed_cpu_pct"] = round(cpu_pct, 1)
        try:
            emit_visibility(
                kind="app_server_initialize_progress",
                severity="warn",
                summary=(
                    f"Codex App Server initialize still pending after "
                    f"{int(elapsed_s)}s (attempt {attempt})"
                ),
                visibility={
                    "mailbox": False,
                    "task_state": False,
                    "event_log": True,
                    "stderr": True,
                },
                payload=progress_payload,
            )
        except Exception as e:
            logger.debug(
                "app_server.initialize_progress_emit_failed",
                error=str(e),
            )

    worker.join()
    elapsed_ms = int((time.monotonic() - started_at) * 1000)

    if "error" in error_holder:
        raise error_holder["error"]

    try:
        emit_visibility(
            kind="app_server_initialize_completed",
            severity="info",
            summary=f"Codex App Server initialize completed in {elapsed_ms}ms",
            visibility={
                "mailbox": False,
                "task_state": False,
                "event_log": True,
                "stderr": True,
            },
            payload={
                "elapsed_ms": elapsed_ms,
                "prompt_byte_size": prompt_byte_size,
                "timeout_s": timeout_s,
            },
        )
    except Exception as e:
        logger.debug(
            "app_server.initialize_completed_emit_failed",
            error=str(e),
        )

    return result_holder.get("result")


def _start_or_fork_thread(
    client: "Any",
    *,
    resume_thread_id: str | None,
    thread_kwargs: dict[str, Any],
) -> str:
    """Dispatch between `thread/start` (no lineage) and `thread/fork` (inherits
    a parent thread's conversational history).

    - `resume_thread_id is None` → fresh `thread/start`.
    - `resume_thread_id` set AND the parent is materialized on disk →
      `thread/fork(parent_id)` so the new thread inherits the parent's
      history. Returns the NEW thread id (not the parent's).
    - `resume_thread_id` set BUT the parent isn't materialized → fall back
      to `thread/start` and log `app_server.fork_fallback_unmaterialized`
      so the caller knows lineage was dropped (see openai/codex#16872 — a
      thread is unmaterialized until its first rollout file is written).

    `thread_kwargs` is the shared-shape dict used by both paths: it includes
    cwd, base/developer instructions, sandbox, approval_policy, ephemeral,
    config, and (optionally) model. `thread/fork` always drops `cwd`; the
    forked child inherits it from the parent thread.
    """
    if resume_thread_id is None:
        return client.thread_start(**thread_kwargs)

    materialized = client.is_thread_materialized(resume_thread_id)

    if not materialized:
        logger.warn(
            "app_server.fork_fallback_unmaterialized",
            resume_thread_id=resume_thread_id,
        )
        return client.thread_start(**thread_kwargs)

    # thread/fork does not require `cwd` (parent thread carries it).
    fork_kwargs = {k: v for k, v in thread_kwargs.items() if k != "cwd"}
    new_thread_id = client.thread_fork(
        thread_id=resume_thread_id, **fork_kwargs
    )
    logger.info(
        "app_server.forked",
        parent_thread_id=resume_thread_id,
        new_thread_id=new_thread_id,
    )
    return new_thread_id


def _json_preview(value: Any, *, limit: int = 1000) -> str:
    """Bounded JSON preview for B9 §6 payload forensic fields."""

    try:
        text = json.dumps(value, default=str, sort_keys=True)
    except Exception:
        text = repr(value)
    if len(text) > limit:
        return text[:limit] + "…"
    return text


def _first_present(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def _item_target(item: dict[str, Any]) -> str | None:
    value = _first_present(
        item,
        "target",
        "command",
        "cmd",
        "query",
        "url",
        "path",
        "file",
        "name",
        "text",
    )
    if isinstance(value, list):
        return " ".join(str(v) for v in value)
    if isinstance(value, dict):
        return _json_preview(value, limit=300)
    if value is None:
        return None
    return str(value)


def _jsonish_dict(value: Any) -> dict[str, Any] | None:
    """Return a dict from native or JSON-encoded tool-call fields."""

    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return None
        if isinstance(parsed, dict):
            return parsed
    return None


def _app_server_tool_arguments(item: dict[str, Any]) -> dict[str, Any]:
    """Best-effort argument map for Codex App Server tool-call items.

    App Server mcpToolCall records have used a native ``arguments`` object in
    recent runs, but older/backend-adapter shapes may surface JSON-encoded
    arguments under ``args``/``input`` or under nested call/function objects.
    Keep the extraction observational: malformed fields simply mean "unknown"
    instead of causing visibility emission to fail.
    """

    for key in ("arguments", "args", "input", "parameters", "tool_input"):
        parsed = _jsonish_dict(item.get(key))
        if parsed is not None:
            return parsed

    for container_key in ("call", "function", "tool_call", "toolCall"):
        container = _jsonish_dict(item.get(container_key))
        if container is None:
            continue
        for key in ("arguments", "args", "input", "parameters", "tool_input"):
            parsed = _jsonish_dict(container.get(key))
            if parsed is not None:
                return parsed
    return {}


def _app_server_structured_result(item: dict[str, Any]) -> dict[str, Any]:
    """Best-effort structured result payload for App Server tool-call items."""

    result = _jsonish_dict(item.get("result"))
    if result is None:
        return {}
    for key in ("structuredContent", "structured_content"):
        structured = _jsonish_dict(result.get(key))
        if structured is not None:
            return structured
    return result


def _send_message_recipient_from_app_server_item(item: dict[str, Any]) -> str | None:
    """Extract the recipient from an App Server send_message tool-call item."""

    for source in (_app_server_tool_arguments(item), _app_server_structured_result(item)):
        for key in ("recipient", "to", "delivered_to"):
            value = source.get(key)
            if value not in (None, ""):
                return str(value)
    return None


def _item_exit_code(item: dict[str, Any]) -> int | None:
    value = _first_present(item, "exit_code", "exitCode", "returncode", "returnCode")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _item_status(item: dict[str, Any], *, exit_code: int | None) -> str | None:
    value = _first_present(item, "status", "outcome", "state")
    if isinstance(value, str) and value:
        normalized = value.strip().lower()
        if normalized in {"success", "succeeded", "ok", "complete", "completed"}:
            return "success"
        if normalized in {"error", "failed", "failure"}:
            return "error"
        return normalized
    if exit_code is not None:
        return "success" if exit_code == 0 else "error"
    return None


def _phase_from_method(method: str, item: dict[str, Any]) -> str:
    explicit = _first_present(item, "phase")
    if isinstance(explicit, str) and explicit in {"started", "completed", "failed"}:
        return explicit
    method_n = _normalise(method)
    status = str(_first_present(item, "status", "outcome", "state") or "").lower()
    if "fail" in method_n or status in {"failed", "error"}:
        return "failed"
    if "start" in method_n or status in {"running", "started"}:
        return "started"
    return "completed"


def _artifact_action(item: dict[str, Any]) -> str:
    value = str(
        _first_present(item, "action", "operation", "status", "changeType") or "modified"
    ).lower()
    if value in {"create", "created", "add", "added", "new"}:
        return "created"
    if value in {"delete", "deleted", "remove", "removed"}:
        return "deleted"
    return "modified"


def _visibility_for_app_server_item(
    *,
    method: str,
    item: dict[str, Any],
    make_event: Callable[..., VisibilityEvent],
) -> VisibilityEvent | None:
    """Normalize Codex App Server native items into B9 §6 envelopes.

    This is intentionally *not* a flattener (07 §7.3): the backend-native
    item type is preserved verbatim as `payload.raw_backend_type` while the
    outer envelope gives the lead/event-log a stable routing shape.
    """

    raw_type = str(item.get("type", ""))
    item_n = _normalise(raw_type)
    raw_preview = _json_preview(item)

    if "agentmessage" in item_n:
        text = str(_first_present(item, "text", "message", "content") or "")
        preview = text[:500] if text else None
        summary = (
            f"{raw_type}: {preview[:120]}"
            if preview
            else f"{raw_type}: message update"
        )
        payload = {
            "raw_backend_type": raw_type,
            "raw_event_preview": raw_preview,
            "agent_message_bytes": len(text),
            "message_preview": preview,
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        return make_event(
            kind="turn_progress",
            severity="info",
            summary=summary[:200],
            payload=payload,
        )

    if "filechange" in item_n:
        path = _first_present(item, "path", "file", "filePath", "filename") or ""
        action = _artifact_action(item)
        payload: dict[str, Any] = {
            "source": f"codex_app_server.{raw_type}",
            "path": str(path),
            "action": action,
            "raw_backend_type": raw_type,
            "raw_event_preview": raw_preview,
        }
        for out_key, *in_keys in (
            ("bytes_delta", "bytes_delta", "bytesDelta"),
            ("line_delta", "line_delta", "lineDelta"),
            ("lines_added", "lines_added", "linesAdded"),
            ("lines_removed", "lines_removed", "linesRemoved"),
        ):
            value = _first_present(item, *in_keys)
            if value is not None:
                payload[out_key] = value
        summary = f"{raw_type}: {action} {path}".strip()
        return make_event(
            kind="artifact_event",
            severity="info",
            summary=summary,
            visibility={
                "mailbox": False,
                "task_state": True,
                "event_log": True,
                "stderr": True,
            },
            payload=payload,
        )

    if "plan" in item_n:
        return make_event(
            kind="turn_progress",
            severity="info",
            summary=f"{raw_type}: plan updated",
            visibility={
                "mailbox": False,
                "task_state": True,
                "event_log": True,
                "stderr": True,
            },
            payload={
                "raw_backend_type": raw_type,
                "raw_event_preview": raw_preview,
                "plan": _first_present(item, "plan", "text", "content", "message"),
            },
        )

    if "error" in item_n:
        summary = str(
            _first_present(item, "message", "error", "summary", "text")
            or f"{raw_type}: backend error"
        )
        return make_event(
            kind="turn_warning",
            severity="error",
            summary=summary[:200],
            visibility={
                "mailbox": True,
                "task_state": True,
                "event_log": True,
                "stderr": True,
            },
            payload={
                "raw_backend_type": raw_type,
                "raw_event_preview": raw_preview,
                "error": _first_present(item, "error", "message", "text"),
            },
        )

    tool_like = any(
        frag in item_n
        for frag in (
            "commandexecution",
            "websearch",
            "mcptoolcall",
            "toolcall",
            "tooluse",
            "imagegeneration",
            "imagegen",
            "imageview",
        )
    )
    if not tool_like:
        return None

    fake_ev = {
        "type": raw_type,
        "item": item,
        **{k: v for k, v in item.items() if k != "type"},
    }
    exit_code = _item_exit_code(item)
    status = _item_status(item, exit_code=exit_code)
    phase = _phase_from_method(method, item)
    tool_name = raw_type
    if "mcp" in item_n or "toolcall" in item_n or "tooluse" in item_n:
        tool_name = _tool_name_from_event(fake_ev) or raw_type
    payload = {
        "category": "mcp_tool" if "mcp" in item_n else "host_tool",
        "tool_name": tool_name,
        "phase": phase,
        "target": _item_target(item),
        "status": status,
        "exit_code": exit_code,
        "duration_ms": _first_present(item, "duration_ms", "durationMs", "elapsedMs"),
        "stdout_preview": _first_present(item, "stdout_preview", "stdoutPreview"),
        "stderr_preview": _first_present(item, "stderr_preview", "stderrPreview"),
        "raw_backend_type": raw_type,
        "raw_event_preview": raw_preview,
    }
    if tool_name == "send_message":
        recipient = _send_message_recipient_from_app_server_item(item)
        if recipient:
            payload["recipient"] = recipient
            payload["to"] = recipient
            payload["target"] = f"to={recipient!r}"
    payload = {k: v for k, v in payload.items() if v is not None}
    target = payload.get("target")
    summary = f"{raw_type}: {target}" if target else raw_type
    failed = phase == "failed" or status == "error"
    return make_event(
        kind="tool_event",
        severity="error" if failed else "info",
        summary=summary[:200],
        visibility={
            "mailbox": failed,
            "task_state": failed,
            "event_log": True,
            "stderr": True,
        },
        payload=payload,
    )


def _app_server_transport_alive(client: Any) -> bool:
    checker = getattr(client, "is_transport_alive", None)
    if callable(checker):
        try:
            return bool(checker())
        except Exception as e:
            logger.debug("app_server.transport_health_check_failed", error=str(e))
            return True
    # Test doubles / older clients that do not expose a health hook are
    # presumed healthy so the existing empty-queue polling behavior is
    # preserved.
    return True


def _app_server_transport_status(client: Any) -> dict[str, Any]:
    status = getattr(client, "transport_status", None)
    if callable(status):
        try:
            value = status()
            if isinstance(value, dict):
                return value
        except Exception as e:
            logger.debug("app_server.transport_status_failed", error=str(e))
    return {}


def _thread_id_from_app_server_payload(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    thread = payload.get("thread")
    if isinstance(thread, dict):
        tid = thread.get("id")
        if isinstance(tid, str) and tid:
            return tid
    tid = payload.get("threadId")
    if isinstance(tid, str) and tid:
        return tid
    return None


def _thread_turns_from_app_server_payload(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    thread = payload.get("thread")
    turns = thread.get("turns") if isinstance(thread, dict) else payload.get("turns")
    if not isinstance(turns, list):
        return []
    return [turn for turn in turns if isinstance(turn, dict)]


def _turn_error_summary(turn: dict[str, Any]) -> str | None:
    err = turn.get("error")
    if isinstance(err, dict):
        message = _first_present(err, "message", "error", "summary")
        if isinstance(message, str) and message:
            return message
        return _json_preview(err, limit=500)
    if isinstance(err, str) and err:
        return err
    return None


def _latest_agent_message(turn: dict[str, Any]) -> str | None:
    items = turn.get("items")
    if not isinstance(items, list):
        return None
    for item in reversed(items):
        if not isinstance(item, dict):
            continue
        if item.get("type") != "agentMessage":
            continue
        text = item.get("text")
        if isinstance(text, str) and text:
            return text
    return None


def _resume_turn_snapshot(
    payload: Any,
    *,
    preferred_turn_id: str | None,
) -> tuple[str | None, str | None, str | None, str | None]:
    """Return (turn_id, status, last_agent_message, error_summary).

    `thread/resume` responses populate `thread.turns[*].items`; during
    transport recovery this lets us salvage a final agentMessage if the turn
    completed while our stdio/WebSocket pipe was down.
    """

    turns = _thread_turns_from_app_server_payload(payload)
    if not turns:
        return None, None, None, None
    selected: dict[str, Any] | None = None
    if preferred_turn_id:
        for turn in turns:
            if turn.get("id") == preferred_turn_id:
                selected = turn
                break
    if selected is None:
        selected = turns[-1]
    turn_id = selected.get("id")
    status = selected.get("status")
    return (
        turn_id if isinstance(turn_id, str) else None,
        status if isinstance(status, str) else None,
        _latest_agent_message(selected),
        _turn_error_summary(selected),
    )


def _app_server_recovery_prompt(task_prompt: str, *, schema: dict[str, Any] | None) -> str:
    contract = (
        "Finish by returning exactly the requested JSON object for the output "
        "schema. No markdown fences and no prose outside the JSON."
        if schema is not None
        else "Finish by replying to the user normally."
    )
    return (
        "Transport recovery: the adapter reconnected to Codex App Server after "
        "the previous transport died mid-turn. Continue the same request from "
        "the current repository and conversation state. Preserve any durable "
        "work that is already done, avoid repeating expensive work unless you "
        "need to verify it, and complete the original request.\n\n"
        f"{contract}\n\n"
        "# Original request\n"
        f"{task_prompt}"
    )


def app_server_invoke(
    *,
    task_prompt: str,
    cwd: Path,
    schema: dict[str, Any] | None,
    settings_team: str,
    settings_agent: str,
    codex_binary: str = "codex",
    overall_timeout_s: float = 1800.0,
    non_progress_warn_s: float | None = None,
    non_progress_interrupt_s: float | None = None,
    wrapper_tool_failure_window_s: float = 90.0,
    steer_queue: "_SteerQueue | None" = None,
    mid_turn_hook: "Any | None" = None,
    resume_thread_id: str | None = None,
    model: str | None = None,
    effort: str | None = None,
    task_id: str | None = None,
    event_sink: Callable[[VisibilityEvent], None] | None = None,
) -> CodexResult:
    """Run a task against Codex App Server. Returns a CodexResult in the
    same shape as `run()` so the control loop can use a single code path
    for post-processing.

    If `steer_queue` is provided, the caller can push new text fragments
    into it from another thread to be delivered to Codex mid-turn via
    `turn/steer`. See `SteerQueue` for the contract.

    Thread lifecycle:

    - **First task for an adapter identity** (or `resume_thread_id=None`):
      fresh `thread/start`. `ephemeral=False` so the thread is persisted
      to disk and fork-able on subsequent tasks.
    - **Subsequent tasks** (`resume_thread_id` set): check whether the
      parent thread is materialized, then `thread/fork` to get a new
      thread that inherits the parent's conversational context. If the
      parent isn't materialized, fall back to fresh `thread/start` and
      log `app_server.fork_fallback_unmaterialized`. Upstream caveat:
      openai/codex#16872 — a thread is unmaterialized until its first
      rollout file exists on disk.

    The returned `CodexResult.session_id` carries the current (new)
    thread id, which the caller passes as `resume_thread_id` on the
    next invocation to continue the session lineage.
    """
    from .app_server import AppServerClient, AppServerError

    schema_json = json.dumps(schema) if schema else None
    client = AppServerClient(
        codex_binary=codex_binary,
        env=identity_env(os.environ, team=settings_team, name=settings_agent),
        pre_start_hook=lambda: _codex_log_bloat_pre_spawn(),
    )

    events: list[dict[str, Any]] = []
    tool_call_events = 0
    structured: dict[str, Any] | None = None
    last_message = ""
    error: str | None = None
    exit_code = 0
    thread_id: str | None = None
    current_turn_id: str | None = None
    turn_started_at: float | None = None
    visibility_seq = 0
    pending_wrapper_tool_failures: list[_PendingWrapperToolFailure] = []
    emitted_unrecovered_wrapper_tools: set[str] = set()

    def _emit_visibility_event(
        *,
        kind: str,
        severity: str,
        summary: str,
        payload: dict[str, Any],
        visibility: dict[str, bool] | None = None,
    ) -> VisibilityEvent:
        nonlocal visibility_seq
        visibility_seq += 1
        turn_ref = current_turn_id or thread_id or "unknown-turn"
        event = VisibilityEvent.model_validate(
            {
                "kind": kind,
                "event_id": f"{settings_agent}:{turn_ref}:{visibility_seq:06d}",
                "team": settings_team,
                "agent": settings_agent,
                "backend": "codex_app_server",
                "task_id": task_id,
                "turn_id": current_turn_id,
                "seq": visibility_seq,
                "severity": severity,
                "summary": summary,
                "visibility": visibility
                or {
                    "mailbox": False,
                    "task_state": False,
                    "event_log": True,
                    "stderr": True,
                },
                "payload": payload,
            }
        )
        if visibility_event_counts_as_wrapper_tool_recovery(event):
            recovered_at = time.monotonic()
            for pending in pending_wrapper_tool_failures:
                if (
                    pending.recovered_at_monotonic is None
                    and event.seq > pending.failed_event_seq
                ):
                    pending.recovered_at_monotonic = recovered_at
                    pending.recovery_event_id = event.event_id
        if event.visibility.stderr:
            log_payload = {
                "kind": event.kind,
                "event_id": event.event_id,
                "seq": event.seq,
                "severity": event.severity,
                "summary": event.summary,
                "visibility_event": event.model_dump(
                    by_alias=True, exclude_none=True
                ),
            }
            if event.severity == "error":
                logger.error("visibility.event", **log_payload)
            elif event.severity == "warn":
                logger.warn("visibility.event", **log_payload)
            elif event.severity == "debug":
                logger.debug("visibility.event", **log_payload)
            else:
                logger.info("visibility.event", **log_payload)
        if event_sink is not None:
            try:
                event_sink(event)
            except Exception as e:
                logger.warn(
                    "visibility.event_sink_failed",
                    kind=event.kind,
                    event_id=event.event_id,
                    error=str(e),
                )
        return event

    def _codex_log_bloat_pre_spawn() -> None:
        """Emit #43 pre-spawn visibility and run a bounded WAL checkpoint."""

        try:
            report = inspect_codex_log_bloat(env=os.environ)
        except Exception as e:
            logger.debug("codex.sqlite_wal_bloat.inspect_failed", error=str(e))
            return
        if not report.is_bloated:
            return

        report_payload = report.to_dict()
        _emit_visibility_event(
            kind="visibility_degraded",
            severity="warn",
            summary=(
                "Codex sqlite WAL bloat detected before App Server spawn; "
                "initialize may be delayed"
            ),
            visibility={
                "mailbox": False,
                "task_state": False,
                "event_log": True,
                "stderr": True,
            },
            payload={
                "surface": "codex_sqlite_wal_bloat",
                "action": "bounded_checkpoint_before_spawn",
                "diagnose_command": "claude-anyteam diagnose --codex-log-bloat",
                **report_payload,
            },
        )

        try:
            checkpoint_results = checkpoint_bloated_codex_wals(report, env=os.environ)
        except Exception as e:
            logger.debug("codex.sqlite_wal_bloat.checkpoint_failed", error=str(e))
            return
        if not checkpoint_results:
            return
        checkpoint_payload = {
            "surface": "codex_sqlite_wal_checkpoint",
            "results": [row.to_dict() for row in checkpoint_results],
        }
        all_ok = all(row.ok for row in checkpoint_results if row.attempted)
        any_attempted = any(row.attempted for row in checkpoint_results)
        if not any_attempted:
            return
        if all_ok:
            _emit_visibility_event(
                kind="turn_progress",
                severity="info",
                summary="Codex sqlite WAL checkpoint completed before spawn",
                visibility={
                    "mailbox": False,
                    "task_state": False,
                    "event_log": True,
                    "stderr": True,
                },
                payload=checkpoint_payload,
            )
        else:
            _emit_visibility_event(
                kind="visibility_degraded",
                severity="warn",
                summary=(
                    "Codex sqlite WAL checkpoint did not complete before "
                    "App Server spawn"
                ),
                visibility={
                    "mailbox": False,
                    "task_state": False,
                    "event_log": True,
                    "stderr": True,
                },
                payload=checkpoint_payload,
            )

    def _record(ev: dict[str, Any]) -> VisibilityEvent | None:
        nonlocal tool_call_events
        events.append(ev)
        method = str(ev.get("method", ""))
        params = ev.get("params", {}) if isinstance(ev.get("params"), dict) else {}
        item = params.get("item") if isinstance(params, dict) else None
        # App Server notification shape: `method` is `<namespace>/<event>`,
        # the item-type lives in `params.item.type`. Reuse the same
        # broad match we have for exec-path events.
        if isinstance(item, dict):
            item_type = str(item.get("type", ""))
            fake_ev = {"type": item_type, "item": item, **{k: v for k, v in item.items() if k != "type"}}
            if _is_tool_call_event(item_type, fake_ev):
                tool_call_events += 1
                logger.info(
                    "codex.tool_call",
                    event_type=f"{method} / {item_type}",
                    tool=_tool_name_from_event(fake_ev),
                    event={"item": item},
                )
            visibility_event = _visibility_for_app_server_item(
                method=method,
                item=item,
                make_event=_emit_visibility_event,
            )
            if _is_failed_wrapper_tool_event(visibility_event):
                if visibility_event is None:
                    return None
                pending_wrapper_tool_failures.append(
                    _PendingWrapperToolFailure(
                        failed_event_id=visibility_event.event_id,
                        failed_event_seq=visibility_event.seq,
                        failed_at_monotonic=time.monotonic(),
                        tool_name=str(visibility_event.payload["tool_name"]),
                        error_class=_wrapper_tool_error_class(
                            visibility_event.payload
                        ),
                        error_detail=_wrapper_tool_error_detail(
                            visibility_event.payload
                        ),
                        turn_id=visibility_event.turn_id,
                    )
                )
            return visibility_event
        return None

    # MCP config for the wrapper — same shape as the exec path, injected via
    # Codex's config override mechanism on thread start. App Server accepts
    # `config.mcp_servers.<name>.command/args`.
    wrapper_binary = shutil.which("claude-anyteam-wrapper") or "claude-anyteam-wrapper"
    # Identity goes in args, not env: App Server does NOT forward our adapter's
    # env into the wrapper subprocess (verified live 2026-04-22 — the wrapper
    # raised RuntimeError on identity lookup and the MCP handshake failed with
    # "connection closed: initialize response"). CLI args sidestep the
    # env-forwarding question entirely; the wrapper's `_identity()` resolves
    # CLI flags first, then env as fallback for backward compat.
    wrapper_args = [
        "--team",
        settings_team,
        "--name",
        settings_agent,
        "--cwd",
        str(cwd),
    ]
    if task_id is not None:
        wrapper_args += ["--task-id", str(task_id)]

    mcp_config = {
        "mcp_servers": {
            "claude_anyteam_wrapper": {
                "command": wrapper_binary,
                "args": wrapper_args,
            }
        }
    }
    _log_codex_tool_snapshot(
        team=settings_team,
        agent=settings_agent,
        event="codex_app_server_mcp_config_prepared",
        payload={
            "task_id": task_id,
            "wrapper_binary": wrapper_binary,
            "wrapper_args": wrapper_args,
            "mcp_config": mcp_config,
        },
    )

    # Common thread-start kwargs shared between fresh-start and fork paths.
    thread_kwargs: dict[str, Any] = {
        "cwd": str(cwd),
        "base_instructions": (
            f"You are {settings_agent}, a Codex teammate on the "
            f"{settings_team} team. Execute the task below.\n\n"
            f"{TEAM_MESSAGING_BLOCK}"
        ),
        "developer_instructions": task_prompt,
        "sandbox": "danger-full-access",
        "approval_policy": "never",
        # Non-ephemeral: the thread must be persisted to disk so a
        # *subsequent* invocation can fork from it. v7.3 trades a small
        # filesystem-footprint cost (threads in `~/.codex/sessions/`)
        # for cross-task session memory under App Server mode.
        "ephemeral": False,
        "config": mcp_config,
    }
    if model is not None:
        thread_kwargs["model"] = model

    def _start_and_initialize_with_retry() -> None:
        max_retries = _read_int_env(
            APP_SERVER_INITIALIZE_RETRIES_ENV,
            _DEFAULT_INITIALIZE_RETRIES,
            min_value=0,
            max_value=3,
        )
        backoff_s = _read_float_env(
            APP_SERVER_INITIALIZE_RETRY_BACKOFF_ENV,
            _DEFAULT_INITIALIZE_RETRY_BACKOFF_S,
            allow_zero=True,
        )
        attempt = 0
        while True:
            try:
                client.start()
                _initialize_with_progress_events(
                    client,
                    emit_visibility=_emit_visibility_event,
                    prompt_byte_size=len(task_prompt.encode("utf-8")),
                )
                return
            except AppServerError as exc:
                if not _is_initialize_timeout_error(exc) or attempt >= max_retries:
                    raise
                attempt += 1
                _emit_visibility_event(
                    kind="turn_warning",
                    severity="warn",
                    summary=(
                        "Codex App Server initialize timed out; "
                        f"retrying cold start ({attempt}/{max_retries})"
                    ),
                    visibility={
                        "mailbox": False,
                        "task_state": True,
                        "event_log": True,
                        "stderr": True,
                    },
                    payload={
                        "surface": "app_server_initialize_retry",
                        "attempt": attempt,
                        "max_retries": max_retries,
                        "backoff_s": backoff_s,
                        "raw_backend_error": str(exc)[:600],
                    },
                )
                try:
                    client.close()
                except Exception as close_exc:
                    logger.debug(
                        "app_server.initialize_retry_close_failed",
                        error=str(close_exc),
                    )
                if backoff_s > 0:
                    time.sleep(backoff_s)

    try:
        _start_and_initialize_with_retry()

        thread_id = _start_or_fork_thread(
            client,
            resume_thread_id=resume_thread_id,
            thread_kwargs=thread_kwargs,
        )
        thread_snapshot = getattr(client, "last_thread_result", None)
        _log_codex_tool_snapshot(
            team=settings_team,
            agent=settings_agent,
            event="codex_app_server_session_start",
            payload={
                "thread_id": thread_id,
                "resume_thread_id": resume_thread_id,
                "task_id": task_id,
                "model": model,
                "effort": effort,
                "mcp_config": mcp_config,
                "thread_response_toolish_fields": _toolish_fields(thread_snapshot),
            },
        )

        current_turn_id = client.turn_start(
            thread_id=thread_id,
            text=task_prompt,
            output_schema=schema,
            model=model,
            effort=effort,
        )
        logger.info("app_server.turn_started", turn_id=current_turn_id)
        _emit_visibility_event(
            kind="turn_started",
            severity="info",
            summary="Codex App Server turn started",
            visibility={
                "mailbox": False,
                "task_state": True,
                "event_log": True,
                "stderr": True,
            },
            payload={
                "mode": "task" if task_id is not None else "prose",
                "prompt_kind": "task_complete" if schema is not None else "prose_reply",
                "timeout_s": overall_timeout_s,
                "non_progress_soft_s": non_progress_warn_s,
                "non_progress_interrupt_s": non_progress_interrupt_s,
                "wrapper_tool_failure_window_s": wrapper_tool_failure_window_s,
                "cwd": str(cwd),
                "model": model,
                "effort": effort,
            },
        )

        # Event loop: drain notifications, watch for turn/completed, deliver
        # queued steers. Polling with a short timeout lets us interleave
        # steer injection with notification consumption.
        deadline = time.monotonic() + overall_timeout_s
        # Soft non-progress watchdog (v0.6.0/R20): if we see no observable
        # output for `non_progress_warn_s`, log a warning + send a single
        # `turn/steer` checkpoint prompt to the model. By default we do NOT
        # kill the turn — the wall-clock cap (`overall_timeout_s`) remains
        # the only interrupt. R20 adds an opt-in hard early interrupt via
        # `non_progress_interrupt_s`; it only fires after the soft watchdog
        # has fired and no later checkpoint appears. Only warn once per turn
        # so we don't spam the lead or the model. "Observable output" = an
        # agentMessage delta or a tool_call_event (post-v0.5.1 substring fix
        # this includes host commandExecution / fileChange events from Codex
        # App Server). See bug-triage/B9-visibility-parity-investigation.md §5.
        turn_started_at = time.monotonic()
        last_progress_at = turn_started_at
        last_tool_count = 0
        last_message_len = 0
        non_progress_warned = False
        non_progress_warned_at: float | None = None
        non_progress_interrupted = False
        transport_reconnect_attempts = 0
        done = False

        def _emit_unrecovered_wrapper_tool_failures(now: float) -> None:
            """Emit #49's Mode-B signal for wrapper-tool failures.

            Mode A recovery is marked in `_emit_visibility_event` when any
            later turn_progress/tool_event/artifact_event arrives (including
            agentMessage deltas, which normalize to turn_progress). This
            checker only fires after W when no such recovery event appeared.
            It is signal-only: no steer, interrupt, or kill.
            """

            for pending in pending_wrapper_tool_failures:
                if pending.tool_name in emitted_unrecovered_wrapper_tools:
                    pending.emitted = True
                    continue
                later_same_tool = [
                    candidate
                    for candidate in pending_wrapper_tool_failures
                    if (
                        candidate.tool_name == pending.tool_name
                        and candidate.failed_event_seq > pending.failed_event_seq
                        and candidate.recovered_at_monotonic is None
                        and not candidate.emitted
                    )
                ]
                if (
                    pending.emitted
                    or pending.recovered_at_monotonic is not None
                    or later_same_tool
                    or (now - pending.failed_at_monotonic)
                    < wrapper_tool_failure_window_s
                ):
                    continue
                suppressed_same_tool = [
                    candidate
                    for candidate in pending_wrapper_tool_failures
                    if (
                        candidate.tool_name == pending.tool_name
                        and candidate.failed_event_seq < pending.failed_event_seq
                        and candidate.recovered_at_monotonic is None
                        and not candidate.emitted
                    )
                ]
                for candidate in suppressed_same_tool:
                    candidate.emitted = True
                pending.emitted = True
                emitted_unrecovered_wrapper_tools.add(pending.tool_name)
                silence_window_ms = int(wrapper_tool_failure_window_s * 1000)
                payload: dict[str, Any] = {
                    "tool_name": pending.tool_name,
                    "error_class": pending.error_class,
                    "error_detail": pending.error_detail,
                    "turn_id": pending.turn_id,
                    "failed_event_id": pending.failed_event_id,
                    "failed_event_seq": pending.failed_event_seq,
                    "last_progress_elapsed_s": int(
                        last_progress_at - turn_started_at
                    ),
                    "silence_window_ms": silence_window_ms,
                    "recovery_hint_dispatched": False,
                    "recovery_event_kinds": sorted(
                        WRAPPER_TOOL_RECOVERY_EVENT_KINDS
                    ),
                    "debounced_by": "tool_name",
                    "suppressed_same_tool_failures": len(suppressed_same_tool),
                }
                payload = {k: v for k, v in payload.items() if v is not None}
                _emit_visibility_event(
                    kind="wrapper_tool_failure_unrecovered",
                    severity="warn",
                    summary=(
                        f"wrapper tool {pending.tool_name} failed; no recovery "
                        f"activity in {int(wrapper_tool_failure_window_s)}s"
                    )[:200],
                    visibility={
                        "mailbox": True,
                        "task_state": True,
                        "event_log": True,
                        "stderr": True,
                    },
                    payload=payload,
                )

        def _resume_kwargs() -> dict[str, Any]:
            """Map thread/start kwargs to the subset accepted by thread/resume."""

            return {
                "cwd": thread_kwargs.get("cwd"),
                "base_instructions": thread_kwargs.get("base_instructions"),
                "developer_instructions": thread_kwargs.get("developer_instructions"),
                "sandbox": thread_kwargs.get("sandbox", "danger-full-access"),
                "approval_policy": thread_kwargs.get("approval_policy", "never"),
                "config": thread_kwargs.get("config"),
                "model": thread_kwargs.get("model"),
            }

        def _mark_transport_recovery_failed(
            *,
            summary: str,
            payload: dict[str, Any],
        ) -> None:
            nonlocal done, error, exit_code
            error = summary
            exit_code = 1
            logger.error(
                "app_server.transport_recovery_failed",
                turn_id=current_turn_id,
                thread_id=thread_id,
                error=summary,
                payload=payload,
            )
            _emit_visibility_event(
                kind="visibility_degraded",
                severity="error",
                summary=summary[:200],
                visibility={
                    "mailbox": True,
                    "task_state": True,
                    "event_log": True,
                    "stderr": True,
                },
                payload={
                    "surface": "codex_app_server_transport",
                    "reason": "reconnect_failed",
                    **payload,
                },
            )
            done = True

        def _attempt_transport_recovery(reason: str) -> None:
            nonlocal transport_reconnect_attempts, thread_id, current_turn_id
            nonlocal last_message, last_progress_at, last_tool_count, last_message_len
            nonlocal non_progress_warned, non_progress_warned_at, done

            if done:
                return
            if thread_id is None or current_turn_id is None:
                _mark_transport_recovery_failed(
                    summary=(
                        "Codex App Server transport died before a resumable "
                        "thread/turn id was available"
                    ),
                    payload={
                        "action": "abort",
                        "reason_detail": reason,
                        "thread_id": thread_id,
                        "turn_id": current_turn_id,
                        "transport_status": _app_server_transport_status(client),
                    },
                )
                return
            if transport_reconnect_attempts >= 1:
                _mark_transport_recovery_failed(
                    summary=(
                        "Codex App Server transport died again after one "
                        "reconnect attempt"
                    ),
                    payload={
                        "action": "abort",
                        "reason_detail": reason,
                        "thread_id": thread_id,
                        "turn_id": current_turn_id,
                        "attempts": transport_reconnect_attempts,
                        "transport_status": _app_server_transport_status(client),
                    },
                )
                return

            transport_reconnect_attempts += 1
            previous_turn_id = current_turn_id
            status_before = _app_server_transport_status(client)
            logger.warn(
                "app_server.transport_lost",
                thread_id=thread_id,
                turn_id=previous_turn_id,
                reason=reason,
                attempt=transport_reconnect_attempts,
                transport_status=status_before,
            )
            _emit_visibility_event(
                kind="turn_warning",
                severity="warn",
                summary="Codex App Server transport disconnected; attempting reconnect/resume",
                visibility={
                    "mailbox": True,
                    "task_state": True,
                    "event_log": True,
                    "stderr": True,
                },
                payload={
                    "surface": "codex_app_server_transport",
                    "reason": reason,
                    "action": "reconnect_and_resume",
                    "attempt": transport_reconnect_attempts,
                    "thread_id": thread_id,
                    "turn_id": previous_turn_id,
                    "transport_status": status_before,
                },
            )

            try:
                reconnect_and_resume = getattr(client, "reconnect_and_resume", None)
                if callable(reconnect_and_resume):
                    resume_payload = reconnect_and_resume(
                        thread_id=thread_id,
                        **_resume_kwargs(),
                    )
                else:
                    client.close()
                    client.start()
                    client.initialize()
                    resume_payload = client.thread_resume(
                        thread_id=thread_id,
                        **_resume_kwargs(),
                    )
            except AppServerError as e:
                _mark_transport_recovery_failed(
                    summary=f"Codex App Server reconnect/resume failed: {e}",
                    payload={
                        "action": "abort",
                        "reason_detail": reason,
                        "thread_id": thread_id,
                        "turn_id": previous_turn_id,
                        "attempt": transport_reconnect_attempts,
                        "transport_status": status_before,
                    },
                )
                return

            resumed_thread_id = _thread_id_from_app_server_payload(resume_payload)
            if resumed_thread_id:
                thread_id = resumed_thread_id

            (
                resumed_turn_id,
                resumed_status,
                resumed_last_message,
                resumed_error,
            ) = _resume_turn_snapshot(
                resume_payload,
                preferred_turn_id=previous_turn_id,
            )
            if resumed_last_message:
                last_message = resumed_last_message

            if resumed_status in {"completed", "interrupted"} and resumed_last_message:
                logger.info(
                    "app_server.transport_recovered_terminal_turn",
                    thread_id=thread_id,
                    turn_id=resumed_turn_id or previous_turn_id,
                    status=resumed_status,
                )
                _emit_visibility_event(
                    kind="turn_progress",
                    severity="info",
                    summary=(
                        "Codex App Server reconnected; recovered completed "
                        "turn output from resumed thread"
                    ),
                    visibility={
                        "mailbox": False,
                        "task_state": True,
                        "event_log": True,
                        "stderr": True,
                    },
                    payload={
                        "surface": "codex_app_server_transport",
                        "action": "resume_recovered_terminal_turn",
                        "thread_id": thread_id,
                        "turn_id": resumed_turn_id or previous_turn_id,
                        "resumed_status": resumed_status,
                    },
                )
                done = True
                return

            try:
                current_turn_id = client.turn_start(
                    thread_id=thread_id,
                    text=_app_server_recovery_prompt(task_prompt, schema=schema),
                    output_schema=schema,
                    model=model,
                    effort=effort,
                )
            except AppServerError as e:
                _mark_transport_recovery_failed(
                    summary=f"Codex App Server recovery turn/start failed: {e}",
                    payload={
                        "action": "abort",
                        "reason_detail": reason,
                        "thread_id": thread_id,
                        "previous_turn_id": previous_turn_id,
                        "resumed_turn_id": resumed_turn_id,
                        "resumed_status": resumed_status,
                        "resumed_error": resumed_error,
                        "attempt": transport_reconnect_attempts,
                    },
                )
                return

            now = time.monotonic()
            last_progress_at = now
            last_tool_count = tool_call_events
            last_message_len = len(last_message)
            non_progress_warned = False
            non_progress_warned_at = None
            logger.info(
                "app_server.transport_recovered",
                thread_id=thread_id,
                previous_turn_id=previous_turn_id,
                recovery_turn_id=current_turn_id,
                resumed_status=resumed_status,
            )
            _emit_visibility_event(
                kind="turn_progress",
                severity="info",
                summary="Codex App Server reconnected; continuation turn started",
                visibility={
                    "mailbox": False,
                    "task_state": True,
                    "event_log": True,
                    "stderr": True,
                },
                payload={
                    "surface": "codex_app_server_transport",
                    "action": "recovery_turn_started",
                    "thread_id": thread_id,
                    "previous_turn_id": previous_turn_id,
                    "turn_id": current_turn_id,
                    "resumed_turn_id": resumed_turn_id,
                    "resumed_status": resumed_status,
                    "resumed_error": resumed_error,
                    "attempt": transport_reconnect_attempts,
                },
            )

        while not done and time.monotonic() < deadline:
            # 1. Deliver any pending steers. Non-blocking pop.
            if steer_queue is not None:
                while True:
                    steer_text = steer_queue.pop_nowait()
                    if steer_text is None:
                        break
                    try:
                        client.turn_steer(
                            thread_id=thread_id,
                            expected_turn_id=current_turn_id,
                            text=steer_text,
                        )
                        logger.info(
                            "app_server.steer_sent",
                            turn_id=current_turn_id,
                            text_head=steer_text[:120],
                        )
                    except AppServerError as e:
                        # Common case: the turn already ended before we could
                        # steer. Drop silently; a follow-up could re-queue as
                        # a fresh turn but that's out of v7.1 scope.
                        logger.warn(
                            "app_server.steer_failed",
                            turn_id=current_turn_id,
                            error=str(e),
                        )

            # 2. Invoke the mid-turn hook. Caller can use this to drain the
            # adapter's own inbox and push steer fragments. Must be cheap and
            # non-blocking — it runs inside the notification poll loop.
            if mid_turn_hook is not None:
                try:
                    mid_turn_hook()
                except Exception as e:
                    logger.warn("app_server.mid_turn_hook_error", error=str(e))

            # 3. Drain notifications.
            try:
                notif = client.notifications.get(timeout=0.5)
            except Exception:
                if not _app_server_transport_alive(client):
                    _attempt_transport_recovery("notification_transport_closed")
                    continue
                # No notification this iteration — check the soft watchdog
                # and (separately) the opt-in hard interrupt.
                #
                # Task #5 / RFC #50 Phase B: ``non_progress_warn_s`` is now
                # opt-in (default None). When None the soft watchdog is
                # skipped entirely; lead reads typed visibility events
                # instead.
                #
                # PR #52 reviewer block: prior to this fix, the interrupt
                # block required ``non_progress_warned=True``, which is
                # only ever set inside the warn block — so when a user set
                # ``non_progress_warn_s=None`` + an explicit
                # ``non_progress_interrupt_s`` for overnight kills, the
                # interrupt was silently dead and the teammate ran to the
                # ``turn_timeout_s`` cap. The two paths are now decoupled:
                # when warn is None, the interrupt fires when
                # ``(now - last_progress_at) >= non_progress_interrupt_s``
                # — i.e. "no observable progress for interrupt_s seconds,"
                # which is the §7.1 overnight-kill semantic the user wants.
                # When warn IS set, the original "warn-then-no-recovery"
                # gate is preserved so users who set both keep the prior
                # ordering semantics.
                now = time.monotonic()
                warn_active = non_progress_warn_s is not None
                _emit_unrecovered_wrapper_tool_failures(now)
                if (
                    warn_active
                    and not non_progress_warned
                    and (now - last_progress_at) >= non_progress_warn_s
                ):
                    elapsed_s = now - turn_started_at
                    non_progress_s = now - last_progress_at
                    warn_threshold_s = int(non_progress_warn_s)
                    logger.warn(
                        "app_server.non_progress",
                        turn_id=current_turn_id,
                        elapsed_s=int(elapsed_s),
                        non_progress_s=int(non_progress_s),
                        tool_call_events=tool_call_events,
                        last_message_len=len(last_message),
                    )
                    _emit_visibility_event(
                        kind="turn_progress",
                        severity="warn",
                        summary=(
                            f"no visible checkpoint for {warn_threshold_s}s; "
                            "checkpoint steer sent"
                        ),
                        visibility={
                            "mailbox": True,
                            "task_state": True,
                            "event_log": True,
                            "stderr": True,
                        },
                        payload={
                            "elapsed_s": int(elapsed_s),
                            "timeout_s": overall_timeout_s,
                            "risk": "timeout_possible",
                            "action_taken": "turn_steer_sent",
                        },
                    )
                    try:
                        client.turn_steer(
                            thread_id=thread_id,
                            expected_turn_id=current_turn_id,
                            text=(
                                "You have produced no externally visible "
                                f"checkpoint for {warn_threshold_s} seconds. "
                                "If you have useful findings or partial work, "
                                "summarize them now and either write a small "
                                "durable artifact or finish in the requested "
                                "output format. Do not continue hidden "
                                "reasoning without a visible checkpoint."
                            ),
                        )
                    except AppServerError as e:
                        logger.warn(
                            "app_server.non_progress_steer_failed",
                            turn_id=current_turn_id,
                            error=str(e),
                        )
                    non_progress_warned = True
                    non_progress_warned_at = now
                elif (
                    non_progress_interrupt_s is not None
                    and not non_progress_interrupted
                ):
                    # Decoupled interrupt: two cases.
                    #
                    # (a) warn is active — preserve original semantics: the
                    #     interrupt only fires AFTER the soft watchdog has
                    #     warned and no later checkpoint was observed, and
                    #     the total turn elapsed exceeds interrupt_s.
                    # (b) warn is None — fire when no observable progress
                    #     for at least interrupt_s seconds. This is the
                    #     §7.1 overnight-kill semantic when warn=None.
                    should_interrupt = False
                    if warn_active:
                        if (
                            non_progress_warned
                            and non_progress_warned_at is not None
                            and last_progress_at <= non_progress_warned_at
                            and (now - turn_started_at) >= non_progress_interrupt_s
                        ):
                            should_interrupt = True
                    else:
                        if (now - last_progress_at) >= non_progress_interrupt_s:
                            should_interrupt = True

                    if should_interrupt:
                        elapsed_s = now - turn_started_at
                        non_progress_interrupted = True
                        logger.warn(
                            "app_server.non_progress_interrupt",
                            turn_id=current_turn_id,
                            elapsed_s=int(elapsed_s),
                            non_progress_interrupt_s=non_progress_interrupt_s,
                            warn_active=warn_active,
                        )
                        try:
                            client.turn_interrupt(
                                thread_id=thread_id,
                                turn_id=current_turn_id,
                            )
                        except AppServerError as e:
                            logger.warn(
                                "app_server.non_progress_interrupt_failed",
                                turn_id=current_turn_id,
                                error=str(e),
                            )
                        else:
                            error = (
                                "app_server turn interrupted after "
                                f"{int(elapsed_s)}s with no visible checkpoint"
                            )
                            exit_code = 124
                            done = True
                            break
                continue
            visibility_event = _record(notif)
            method = str(notif.get("method", ""))
            params = notif.get("params", {}) if isinstance(notif.get("params"), dict) else {}
            item = params.get("item") if isinstance(params, dict) else None

            # Capture agentMessage as the final text payload. The last one
            # during a turn is the schema-constrained response.
            if isinstance(item, dict) and item.get("type") == "agentMessage":
                text = str(item.get("text", ""))
                if text:
                    last_message = text

            # Watchdog progress signal: any tool_call_event delta or any
            # agentMessage byte-length delta counts as observable progress.
            # Wrapper-tool failure recovery uses the same visibility-kind
            # helper so future recovery kinds cannot drift from this
            # predicate.
            tool_call_delta = tool_call_events > last_tool_count
            agent_message_delta = len(last_message) > last_message_len
            visibility_kind_delta = is_wrapper_tool_recovery_event_kind(
                visibility_event.kind if visibility_event is not None else None
            )
            if tool_call_delta or agent_message_delta or visibility_kind_delta:
                last_progress_at = time.monotonic()
                last_tool_count = tool_call_events
                last_message_len = len(last_message)

            if method == "turn/completed" or method == "TurnCompletedNotification":
                # Mirror exec-path bookkeeping.
                turn = params.get("turn") if isinstance(params, dict) else None
                if isinstance(turn, dict) and turn.get("status") == "failed":
                    error = str(turn.get("error") or "turn failed")
                    exit_code = 1
                done = True
                break

        if not done:
            error = f"app_server turn did not complete within {overall_timeout_s}s"
            exit_code = 124
            # Try to interrupt so we don't leave Codex spinning.
            try:
                client.turn_interrupt(thread_id=thread_id, turn_id=current_turn_id)
            except AppServerError:
                pass

        # Parse schema-constrained response.
        if schema is not None and last_message:
            try:
                structured = json.loads(last_message)
            except json.JSONDecodeError:
                error = "app_server last agentMessage was not valid JSON despite outputSchema"
                logger.warn(
                    "app_server.schema_parse_fail",
                    last_message_head=last_message[:200],
                )
    except AppServerError as e:
        error = f"app_server error: {e}"
        exit_code = 1
    finally:
        client.close()

    elapsed_s = (
        time.monotonic() - turn_started_at
        if turn_started_at is not None
        else None
    )
    last_message_preview = last_message[:500]
    delivered_via_send_message_tool = False
    if schema is None and task_id is None:
        from types import SimpleNamespace
        from . import protocol_io as pio

        delivered_via_send_message_tool = pio.should_skip_prose_fallback(
            SimpleNamespace(
                exit_code=exit_code,
                events=events,
                tool_call_events=tool_call_events,
            )
        )
        if delivered_via_send_message_tool:
            last_message_preview = ""

    terminal_payload = {
        "exit_code": exit_code,
        "error": error,
        "elapsed_s": int(elapsed_s) if elapsed_s is not None else None,
        "structured": bool(structured),
        "events": len(events),
        "tool_call_events": tool_call_events,
        "last_message_preview": last_message_preview,
    }
    if last_message and not last_message_preview:
        terminal_payload["last_message_suppressed_reason"] = "delivered_via_send_message_tool"
    if delivered_via_send_message_tool:
        terminal_payload["delivered_via_send_message_tool"] = True
    terminal_payload = {k: v for k, v in terminal_payload.items() if v is not None}
    if error or exit_code != 0:
        _emit_visibility_event(
            kind="turn_failed",
            severity="error",
            summary=(error or f"Codex App Server exited {exit_code}")[:200],
            visibility={
                "mailbox": True,
                "task_state": True,
                "event_log": True,
                "stderr": True,
            },
            payload=terminal_payload,
        )
    else:
        _emit_visibility_event(
            kind="turn_completed",
            severity="info",
            summary="Codex App Server turn completed",
            payload=terminal_payload,
        )

    logger.info(
        "app_server.done",
        exit_code=exit_code,
        events=len(events),
        structured=bool(structured),
        tool_call_events=tool_call_events,
    )

    return CodexResult(
        exit_code=exit_code,
        structured=structured,
        last_message=last_message,
        events=events,
        error=error,
        tool_call_events=tool_call_events,
        session_id=thread_id,
    )


class _SteerQueue:
    """Thread-safe one-way channel for mid-turn steer messages.

    The control loop's inbox poller (running in the main thread) calls
    `push(text)`; the `app_server_invoke` event loop (running in a worker
    thread) calls `pop_nowait()` and submits the text via `turn/steer`.

    Deliberately minimal — no backpressure, no ordering guarantees beyond
    FIFO. Messages dropped on the floor if the turn ends before they're
    delivered (same failure mode as a race-lost SendMessage).
    """

    def __init__(
        self,
        *,
        capabilities: list[str] | None = None,
        team: str | None = None,
        agent: str | None = None,
    ) -> None:
        import queue as _queue
        self.capabilities = list(capabilities or [])
        # 09 R15-vis-followup: stash team + agent for visibility_degraded
        # envelope on rejection. Optional + None-default preserves existing
        # callers (tests construct SteerQueue() without context).
        self._team = team
        self._agent = agent
        self._q: _queue.Queue[str] = _queue.Queue()

    def push(self, text: str, *, sender: str | None = "team-lead") -> bool:
        if sender != "team-lead" and "accepts_peer_steer" not in self.capabilities:
            logger.warn(
                "app_server.steer.rejected",
                sender=sender,
                reason="not_team_lead_and_capability_not_declared",
            )
            # 09 R15-vis-followup (08 CD-6 / 07 §6.5): emit visibility_degraded
            # to lead's mailbox + event log when team+agent context is set.
            # Non-fatal: visibility emission failures shouldn't shadow the gate.
            if self._team and self._agent and sender:
                try:
                    from . import protocol_io as pio
                    pio.emit_peer_steer_rejection(
                        team=self._team,
                        agent=self._agent,
                        backend="codex",
                        sender=sender,
                    )
                except Exception as e:
                    logger.debug(
                        "app_server.steer.rejection_event_emit_failed",
                        error=str(e),
                    )
            return False
        self._q.put(text)
        return True

    def pop_nowait(self) -> str | None:
        import queue as _queue
        try:
            return self._q.get_nowait()
        except _queue.Empty:
            return None


# Public alias; tests and the loop use this name.
SteerQueue = _SteerQueue
