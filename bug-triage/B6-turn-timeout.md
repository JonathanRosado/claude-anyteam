# B6 turn timeout triage

## Review

### MEMORY.md checkpoint

- I looked for `MEMORY.md` before re-reading the code, per team-lead's instruction.
- No `MEMORY.md` was present under `/home/rosado/Projects` as of this turn (`find .. -name MEMORY.md -print` from repo root returned no matches).
- Proceeding with fresh checkpoints in this file after each small/critical file read.

### `src/claude_anyteam/app_server.py`

- `AppServerClient` is a thin JSON-RPC wrapper around `codex app-server`; it starts the subprocess with `log_prefix="app_server"` and `stderr_log_prefix="app_server.stderr"`.
- Turn entrypoints here are protocol helpers only:
  - `turn_start(...)` sends `turn/start` with `threadId` and text input, returning `turn["id"]` or top-level `turnId`.
  - `turn_steer(...)` sends `turn/steer` with `expectedTurnId`.
  - `turn_interrupt(...)` sends `turn/interrupt`.
- I did not find the 900s watchdog constant in `app_server.py`; the timeout enforcement appears to live in the higher-level Codex adapter (`codex.py`) rather than this client wrapper.
- Implication for B6: the app-server client can start/steer/interrupt turns, but progress/timeout policy must be implemented by its caller unless we extend the wrapper interface.

### `src/claude_anyteam/codex.py`

- The 900s B6 watchdog is in `app_server_invoke(..., overall_timeout_s: float = 900.0, ...)`.
- App Server turn lifecycle in this adapter:
  - Start JSON-RPC process with `AppServerClient(...)`.
  - `client.start()` and `client.initialize()`.
  - `_start_or_fork_thread(...)` starts a fresh non-ephemeral thread, or forks from `resume_thread_id` if materialized.
  - `client.turn_start(...)` starts the task turn and logs `app_server.turn_started`.
  - A polling loop runs until `done` or `time.monotonic() >= deadline`, where `deadline = time.monotonic() + overall_timeout_s`.
  - Each loop iteration drains pending steers, invokes `mid_turn_hook()`, then waits up to 0.5s for an app-server notification.
  - `agentMessage` notifications update `last_message`; `turn/completed` or `TurnCompletedNotification` marks `done`.
- Timeout behavior:
  - If the turn is not done by the deadline, the adapter sets `error = f"app_server turn did not complete within {overall_timeout_s}s"` and `exit_code = 124`.
  - It then attempts `client.turn_interrupt(thread_id=thread_id, turn_id=current_turn_id)` to stop the runaway turn.
  - The function still returns a normal `CodexResult(exit_code=124, structured=None, ..., error=...)`; it does not raise.
- The app-server path logs only terminal bookkeeping at `app_server.done` with `exit_code`, `events`, `structured`, and `tool_call_events`.
- I did not find `prose.codex_fail` or `task_blocked` emission in `codex.py`; this file produces the `CodexResult` that `loop.py` consumes. The user-visible/reporting failure path is therefore in the loop layer.
- B6 gap from this file: there is no mid-turn progress event/log carrying elapsed time, deadline, read/write/tool counts, or "likely to time out" forecast before the terminal 124 result.

### `src/claude_anyteam/loop.py`

- Main task lifecycle:
  - `_main_loop(...)` drains inbox, handles messages, claims a task via `_find_and_claim(...)`, then calls `_execute_task(...)`.
  - `_find_and_claim(...)` sets `active_form=f"Running codex on task #{t.id}"`, records `state.in_flight_task`, and logs `task.claimed`.
  - `_execute_task(...)` calls `_invoke_codex_for_task(...)`; if the returned `CodexResult` has `structured is None` or nonzero `exit_code`, it logs `task.codex_fail`, calls `_mark_blocked(...)`, clears `state.in_flight_task`, and returns.
  - `_mark_blocked(...)` updates the task with `active_form="blocked: ..."` and metadata `blocked_reason` / `blocked_by`, then emits `pio.send_task_blocked(...)`.
- App Server task path:
  - `_invoke_codex_for_task(...)` dispatches to `_execute_task_app_server(...)` when `settings.app_server` is true.
  - `_execute_task_app_server(...)` loads `TASK_COMPLETE_SCHEMA`, creates a `SteerQueue`, defines `_mid_turn_hook()` to drain prose/shutdown inbox messages, then calls `codex_mod.app_server_invoke(...)`.
  - That hook provides steerability but not observability: it can forward inbox messages into the turn, yet it does not publish periodic progress outward.
- Prose/DM failure path:
  - `_handle_prose(...)` uses `app_server_invoke(..., schema=None)` in app-server mode and does not chain the result into task lineage.
  - If Codex returns no usable reply, it logs `prose.codex_fail` with `sender`, `exit_code`, and `error`, then falls back to a minimal canned `prose_reply`.
  - This matches the live B3/B6 incident: the adapter had enough structured failure data in the warning log, but the user-facing message was only the silent/minimal fallback.
- B6 gap from this file: the task path marks blocked only after the Codex invocation returns. During the 900s app-server wait there is no `task_update`, `send_message`, `task_progress`, or similar event that would let the lead intervene before the cap fires.

### Review synthesis

- The 900s timeout is real and adapter-owned: `codex.py` enforces it in `app_server_invoke` with `overall_timeout_s=900.0`.
- The timeout returns a structured `CodexResult(exit_code=124, error="app_server turn did not complete within 900.0s")`; it is not an uncaught crash.
- `loop.py` has two terminal consumers of that result:
  - prose DMs: warn-log `prose.codex_fail` and send a minimal fallback;
  - claimed tasks: warn-log `task.codex_fail`, update the task as blocked, and emit `task_blocked`.
- The missing piece for B6 is not failure detection after 900s; it is mid-turn diagnostic/progress surfacing before 900s.

## Critique

- **Raising the 900s cap is not the fix.** The new live data changes the
  diagnosis: my timed-out prose turn ran **14m 36s**, emitted **383 App Server
  events**, had **`tool_call_events: 0`**, and produced **0 durable
  artifacts**. The artifact count, not `tool_call_events` by itself, is the
  proof of non-progress: a later successful turn wrote ~100 lines while the
  wrapper still logged `tool_call_events: 0` because host-tool calls are not
  exposed as App Server MCP tool events. Raising the cap would make the
  zero-artifact failure quieter and longer. Keep a wall-clock cap for absolute
  containment, but add a separate non-progress watchdog based on observable
  output/artifact progress rather than elapsed time alone.

- **`--turn-timeout-s` should be backend-owned with overrides, not only a
  `team-agent` semantic.** The timeout is enforced inside the Codex App Server
  invocation (`codex.py:607`, `737-809`), so the default belongs in the Codex
  adapter `Settings` / CLI / env path. `team-agent` can be the convenient
  writer for a per-teammate override in
  `~/.claude/teams/<team>/agents/<name>.json`, the same way it writes
  model/effort today, but it should not be the only owner of the policy. A
  true team-level default may be useful later, but this repo currently has a
  typed per-teammate shim config and no typed team-default config; introducing
  a new team config just for this bug would be larger migration surface than
  necessary.

- **"Auto-flush every N tool calls" is the wrong framing at the wrapper layer.**
  The App Server loop already has the control primitive: it polls notifications,
  calls `mid_turn_hook()`, and can send `turn/steer` while the turn is in flight
  (`codex.py:734-780`; task inbox steers are queued in `loop.py:774-804`).
  That means the adapter can interject a checkpoint prompt mid-turn, but the
  wrapper MCP server cannot force Codex to reveal hidden reasoning or "flush" a
  partial answer. Completed tool calls are already durable at their side-effect
  boundary; invisible model thought is not. The right layer is therefore the
  App Server invocation loop: observe App Server-visible progress signals
  (`agentMessage` deltas, code blocks/final prose, optional artifact or byte
  deltas), emit external `turn_progress`, and if needed steer the model with
  "summarize and end now" or interrupt after a short grace period.

- **`turn_interrupt` stops the runaway; it does not by itself preserve a useful
  deliverable.** Current code interrupts only after the polling loop exits on
  timeout (`codex.py:802-809`), then returns a
  `CodexResult(exit_code=124, structured=None, ...)`. Any filesystem writes or
  wrapper-tool side effects that completed before the cap should already be
  durable outside the turn, and notifications drained before the deadline stay
  in `CodexResult.events`. But the adapter does not drain after interrupt, does
  not reconstruct partial tool results into a final summary, and does not
  treat completed tool calls as a resumable checkpoint. In this timed-out turn
  there were no durable artifacts and no wrapper-visible MCP tool calls, so
  the "lost work" was not completed output; it was uncommitted reasoning that
  never crossed an externally observable checkpoint or final-message boundary.

## Architect

- **Config knob.**
  - Add `Settings.turn_timeout_s` with default **900.0s** to preserve current
    behavior, sourced from `--turn-timeout-s` /
    `CLAUDE_ANYTEAM_TURN_TIMEOUT_S` (legacy fallback
    `CODEX_TEAMMATE_TURN_TIMEOUT_S`).
  - Validate range **60-3600s**. Values below 60s create false positives on
    ordinary startup/tool latency; values above 3600s turn the cap back into a
    runaway-hiding mechanism. Operators who truly need longer can still split
    the task or raise the range in a follow-up with evidence.
  - Add `Settings.non_progress_timeout_s` with default **300.0s** and range
    **60-900s**; `0` may disable it for emergency debugging, but the shipped
    default should be on.
  - Effective ownership: backend default in adapter settings; per-teammate
    override via the existing shim config if desired:
    `~/.claude/teams/<team>/agents/<name>.json`.

- **Non-progress watchdog, distinct from wall-clock cap.**
  - Track `turn_started_at`, `last_meaningful_progress_at`, App Server event
    count, `agentMessage` count/byte deltas, code-block/final-message deltas,
    optional cheap artifact snapshots, and `mcp_tool_call_events` inside
    `app_server_invoke`.
  - "Meaningful progress" for v1 should be externally observable output or
    artifact movement: a new/changed `agentMessage`, a final-message/code-block
    byte delta, or a host-visible artifact/checkpoint delta. Plain App Server
    churn does not reset the watchdog; the live incident had 383 such events
    and still made no durable progress.
  - Do **not** use `tool_call_events == 0` from the App Server stream as the
    watchdog's primary signal. Fresh field evidence shows that those events are
    MCP-only: a successful Codex turn can use host Read/Edit/Write internally,
    write files, and still report `tool_call_events: 0`.
  - Heuristic: if no observable message/artifact progress appears by
    `non_progress_timeout_s`, emit `app_server.non_progress` and a
    `turn_progress` payload, then send one `turn/steer` checkpoint prompt:
    "You have produced no observable checkpoint for N seconds. Summarize any
    useful findings in the requested final format and end the turn now; do not
    continue hidden reasoning."
  - Grace: if there is still no observable progress **60s** after the
    checkpoint steer, interrupt early with `exit_code=124` and error
    `"app_server turn made no observable progress within <threshold>s"`. This
    catches the 14m36s/zero-artifact runaway around the 6-minute mark while
    leaving the 900s wall-clock cap as the absolute containment backstop.
  - Future extension: after the first durable checkpoint, switch the heuristic
    to "no new observable progress for N minutes" with a higher threshold, so
    it can catch post-tool runaway without killing legitimate long-running
    shell commands.

- **Concrete file/line edits.**
  - `src/claude_anyteam/env.py:30-38`: add
    `TURN_TIMEOUT_ENV`, `LEGACY_TURN_TIMEOUT_ENV`,
    `NON_PROGRESS_TIMEOUT_ENV`, and `LEGACY_NON_PROGRESS_TIMEOUT_ENV`.
  - `src/claude_anyteam/config.py:37-66` and `87-137`: add the two settings,
    parse/validate floats from overrides/env/defaults, and include them in the
    returned `Settings`.
  - `src/claude_anyteam/cli.py:68-129` and `357-380`: add
    `--turn-timeout-s` and `--non-progress-timeout-s`, then forward explicit
    CLI values into `from_env(overrides=...)`. Include the effective values in
    the startup log at `cli.py:388-397`.
  - `src/claude_anyteam/spawn_shim.py:104`, `118-157`, and `249-260`: extend
    the per-agent whitelist to `turn_timeout_s` and
    `non_progress_timeout_s`, accept numeric/string JSON values, and forward
    them as `--turn-timeout-s` / `--non-progress-timeout-s` during routed
    adapter dispatch.
  - `src/claude_anyteam/team_cli.py:41-43`, `124-131`, and `165-179`: extend
    `team-agent` so it can write/update/remove the new per-teammate timeout
    keys without stripping them. Validate with the same ranges as `config.py`.
  - `src/claude_anyteam/codex.py:599-843`: add
    `overall_timeout_s` call-site wiring from settings plus
    `non_progress_timeout_s`, `non_progress_grace_s`, and an optional
    `progress_hook`. In the polling loop, maintain progress timestamps,
    emit/log the non-progress checkpoint once, send `turn_steer`, and interrupt
    after grace if still stalled.
  - `src/claude_anyteam/loop.py:179-220` and `755-816`: pass the configured
    timeouts into prose/task App Server invocations. For task invocations,
    define `progress_hook` near `_mid_turn_hook()` that rate-limits a
    task-visible update: `active_form="running codex: <elapsed>/<timeout>s,
    messages=<n>, artifact_delta=<bytes>"` plus a JSON prose message to
    team-lead for the non-progress checkpoint.
  - Tests to add/update:
    `tests/test_model_effort_flags.py` for settings/CLI/env precedence,
    `tests/test_team_cli.py` and `tests/test_spawn_shim.py` for per-agent
    timeout forwarding, and a new `tests/test_app_server_watchdog.py` (or
    extend `tests/test_app_server_mcp_config.py`) with fake notifications/time
    to assert checkpoint steer and early interrupt on no observable progress.

- **Migration story for v0.5.0 installs.**
  - No on-disk migration is required. Existing per-agent JSON files with only
    `model` / `effort` remain valid; missing timeout keys fall through to the
    new defaults.
  - Existing v0.5.0 adapter processes will not gain the watchdog until they
    are upgraded and respawned; the shim reads per-agent config at spawn time,
    and the App Server loop is in-process Python code.
  - New releases should keep legacy `CODEX_TEAMMATE_*` env fallbacks for the
    timeout variables, matching the existing rebrand compatibility pattern.
  - Document the knobs in `docs/configuration.md:65-127` and the
    `team-agent` row at `docs/configuration.md:141-149`; note that old
    `team-agent` binaries strip unknown keys, so users should upgrade before
    editing timeout settings through the CLI.

## Diagnostic surface

- Live incident from this session, captured from tmux stderr:

```json
{"ts":"2026-04-26T17:38:08.195Z","level":"warn","msg":"prose.codex_fail","sender":"team-lead","exit_code":124,"error":"app_server turn did not complete within 900.0s"}
```

- Ground truth: the adapter already captures `sender`, `exit_code`, and
  `error` and warn-logs them. B6 should add pre-timeout progress;
  B3/reporting-kit should turn this captured failure into useful user-facing
  status instead of a generic silent fallback.

- Fresh wrapper visibility evidence: the App Server stream's
  `codex.tool_call` / `tool_call_events` counter is **not** a reliable
  proxy for Codex host-tool activity. My later successful turn wrote ~100
  lines into this deliverable and still logged `tool_call_events: 0` with
  `events: 288` from the wrapper's perspective. The counter fires for MCP
  tools such as `list_mcp_resources` / `list_mcp_resource_templates`; it does
  not include host tools the Codex CLI runs internally, such as file reads and
  edits. Therefore the diagnostic gap is:
  - what we want to know: "is the teammate reading/editing/writing, or only
    thinking?";
  - what the App Server protocol exposes today: stream events, agent messages,
    MCP tool events, completion/error, and not the internal host-tool calls.

- Consequence for B6: a non-progress watchdog **cannot** key on
  `tool_call_events == 0`. It should key on observable progress that the
  wrapper can actually see: `agentMessage` frequency, `agentMessage`
  byte-count deltas, code-block/checkpoint messages, final-message deltas, and
  optional host-side artifact deltas such as file/byte/git-status changes.
  `tool_call_events` is still useful as an MCP-specific diagnostic, but should
  be labeled that way to avoid false "no work happened" conclusions.

- Fresh B2 confirmation: in this live session, the MCP probe returned
  `{"resources":[]}` for `list_mcp_resources`, matching the architect's
  prediction that the wrapper's `send_message` MCP was empty for
  `codex-runtime` because the team patch ran after spawn and pydantic
  strictness on a sibling row broke the whole config read. That is why prose
  replies had to fall through `protocol_io.send_prose` instead of
  `mcp_anyteam_send_message`.

- The same diagnostic stream should surface that handshake condition up front:
  e.g. `mcp_peer_dm_unavailable` / "send_message MCP empty — no peer DM
  available". That would have warned the lead about both classes of problem:
  (1) turn non-progress before the 900s cap, and (2) degraded peer-DM transport
  before prose replies silently used the fallback path.

## Productivity lens

- Hard data from this incident:
  - Turn 1 burned **14m36s** and produced **0 durable artifacts** because B6
    timeout behavior, B3 fallback reporting, and B2 send-message unavailability
    stacked together.
  - Turn 2 used an explicit checkpointing pattern and produced ~**100 lines**
    in about **3.5 minutes**.
  - Turn 3 continued the pattern and produced ~**140 more lines** in about
    **4 minutes**.
  - The practical throughput changed from **0 lines / 14m36s** to roughly
    **28-35 lines/minute** once the work was split into visible checkpoints.
    Compared with an uncheckpointed long-form turn that can spend a whole cap
    on hidden reasoning, the checkpoint pattern more than doubled effective
    throughput and, more importantly, made partial work durable.

- What Claude in-process does **not** pay for:
  - no Codex App Server **900s cap** on a single hidden turn;
  - no MCP allowlist race where an executor starts before a newly patched peer
    tool exists;
  - no pydantic schema/strictness fragility that can break a whole sibling
    config read and remove `send_message`;
  - no host-tool-vs-MCP visibility split where the wrapper cannot tell whether
    file tools are being used.

- Cumulative effect: even when Codex is nominally as capable as Claude at the
  underlying reasoning/file-editing task, this adapter overhead plausibly makes
  Codex feel **~10-30% less productive** in team workflows. The loss is not
  model intelligence; it is coordination tax: missing early diagnostics, hidden
  progress, degraded DMs, and recovery turns after a cap.

- Recommendation: yes, `claude-anyteam` should ship a **long-form mode**
  preamble for executor roles. It should bake in the checkpoint pattern so
  users do not have to discover it manually:
  - write or edit a small durable section early;
  - emit a short checkpoint summary after each section;
  - prefer multiple bounded edits over one huge hidden reasoning turn;
  - if approaching the non-progress threshold, summarize current state and end
    rather than continue silently.

- A useful `turn_progress` event shape would have made the failure forecast
  visible before cutoff:

```json
{
  "kind": "turn_progress",
  "task_id": "B6",
  "agent": "codex-runtime",
  "phase": "review",
  "elapsed_s": 720,
  "timeout_s": 900,
  "agent_messages": 0,
  "agent_message_bytes": 0,
  "mcp_tool_calls": 0,
  "artifact_delta_bytes": 0,
  "last_checkpoint": null,
  "risk": "timeout_likely",
  "suggested_intervention": "ask agent to checkpoint deliverable and end turn"
}
```
