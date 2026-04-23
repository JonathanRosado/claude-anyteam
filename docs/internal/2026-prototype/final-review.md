# Final Conformance Review — Codex-as-Teammate

**Status:** final — amended (task #23 amendment to task #6 original sign-off)
**Author:** reviewer
**Date:** 2026-04-22 (original sign-off) / 2026-04-22 (v2 amendment)
**Scope:** v6 (feasibility), v7 (first-class), v7.1 (mid-task reactivity), v7.2 (cross-task context)
**Inputs:** `docs/protocol-spec.md` v1.1, `docs/architecture-decision.md` v6, `docs/v7-architecture.md`, `docs/v7.1-notes.md`, `docs/v7.2-notes.md`, `docs/v7.2-findings.md` (historical — resolved by v7.2-notes Option A), `docs/app-server-sanity.md`, `docs/validation-report.md`
**Implementation under review:** `src/claude_anyteam/` as of 2026-04-22 after tasks #18 / #20 / #21 / Fix B closed; 130 tests passing.

### What changed in the v2 amendment

1. **v7.2 row flipped from BLOCKED-WITH-FINDINGS to SHIPPED** via Option A (`codex exec resume` + `--output-last-message` + Python-side schema validation). Live evidence: chained tasks #24 → #25 (see §1 v7.2 entry).
2. **v7.1 conformance narrowed** to reflect that task #19's live-validation proved inbound `turn/steer` only; outbound wrapper MCP tool access regressed under App Server mode due to subprocess-env-inheritance, was caught during task #22 sanity probes, and was fixed via Fix B. Post-fix re-validation on task #30 covered the outbound path with three-way verification.
3. **§3 thesis upgraded** — refutation now covers all four levels (feasibility, first-class, mid-task reactivity, cross-task session memory). Level 4 is no longer scope-bound.
4. **§4 known limitations updated** — v7.2 scope-bound item removed (resolved); v6 stdin-bug item retained as "v6-mode only, fix-forward"; new item added for the v7.1 outbound-MCP regression (resolved, not outstanding).
5. **New §7 "App Server default flip"** captures the task #21 flip rationale, task #22 sanity evidence, and Fix B's role in making the flip safe.

---

## 1. Per-version conformance

Four versions were scoped across the project. **All four shipped.** Three (v6, v7, v7.1) shipped during the primary arc and are covered in the original task #6 sign-off; the fourth (v7.2) shipped after the amendment cycle via Option A. This section states for each: what the spec required, what was built, what was tested, what's documented as limitation.

### v6 — Feasibility baseline

- **Spec'd:** architecture-decision.md v6. External self-registered Python peer importing `cs50victor/claude-code-teams-mcp` as a library, invoking `codex exec --json --output-schema` per task, handling shutdown and idle. `planModeRequired: false` default with an opt-in path.
- **Implemented:** `src/claude_anyteam/{cli,config,registration,protocol_io,codex,loop,messages,plan_probe,shutdown_probe,smoke,roundtrip_m1}.py`. M0 empirically confirmed the harness tolerates self-registration; M1 proved the protocol layer; M2 delivered `primes.py` end-to-end; M3 delivered clean shutdown.
- **Tested (live):** 6 of 9 §9 criteria full-pass live (self-register + delivery, claim, `codex exec --output-schema`, `task_update` + `task_complete`, `idle_notification`, no-Claude-LLM). Live evidence: tasks #7 (primes), #8 (csv2md), #9 (wordfreq), #10 (rational). Full-pass live: shutdown-approve path on two adapters.
- **Tested (unit):** 33 of the 88 tests date to v6 (covering wire formats, loop dispatch, registration, inbox contention, inbox guard). `test_shutdown_while_mid_task_rejects_with_feedback` covers the reject-path semantically.
- **Limitations documented:**
  - **§9 criterion #1 (cold-join timing)** not live-exercised — would require terminating the running team. Accepted on prior M0 evidence plus hot-join functional equivalence.
  - **§9 criterion #6 reject-while-mid-task** unit-tested, not live. Both live attempts were overtaken by Codex finishing faster than the probe could fire (sub-40s tasks).
  - **§9 criterion #8 `planModeRequired: true`** qualified-pass: code path, schema selection, retry-and-block all exercised correctly. Plan generation itself failed during live probe with Codex exit=1 / stderr `Reading additional input from stdin...`; the adapter's retry-then-block contract handled it per spec (`task_blocked` message, no stub). Root cause: missing `stdin=subprocess.DEVNULL` in `codex.py::run`. Fixed-forward as part of v7/v7.1 touching the same module.
  - **Hot-join UI chrome absence** (`targetColor` missing on routing output for self-registered peers): cosmetic, not functional. Documented in architecture-decision §8.
- **Conformant with `protocol-spec.md` §5:** yes, fully. See §2 of this document.
- **Verdict:** **SHIPPED, VALIDATED.** v6 established the feasibility baseline and refuted the project's thesis at the protocol layer.

### v7 — First-class teammate (MCP protocol tool access)

- **Spec'd:** v7-architecture.md. The same external self-registered Python peer, plus: Codex receives MCP tool access to a narrowed subset of cs50victor's protocol operations so Codex's own reasoning can reach the team protocol mid-task. Invocation via `codex exec` with `-c mcp_servers.*` overrides (Option X in v7-architecture §4), sandbox bypass via `--dangerously-bypass-approvals-and-sandbox` (rationale in §4). Narrowed wrapper (`wrapper_server.py`) exposes exactly six safe tools; six destructive lifecycle tools are blocked.
- **Implemented:** `wrapper_server.py` (267 lines), `prompts.py` (89 lines with v7_task_prompt and v7_plan_prompt), integration in `codex.py` and `loop.py`. Narrowed tool surface is `EXPOSED_TOOLS = (send_message, task_update, task_create, read_inbox, task_list, read_config)` with identity pre-fill from `CLAUDE_ANYTEAM_TEAM`/`CLAUDE_ANYTEAM_NAME` env vars.
- **Tested (live):** M6 ran three distinct v7 tasks through the full adapter: #14 `base64 CLI` → produced `b64cli.py` (in scratch dir — task-ownership note below); #15 `unique-lines CLI + unittest` → produced `m2-artifacts/uniqlines.py` + `test_uniqlines.py` (working, adopted); #16 `roman-numeral converter with bugfix iteration` → produced `m2-artifacts/roman.py` + `test_roman.py`. M5 earlier produced `m2-artifacts/json2csv.py` + `test_json2csv.py`. Task #12 records the M5 smoke run.
- **Tested (unit):** `test_wrapper_contract.py` (7 tests) enforces that exactly `EXPOSED_TOOLS` are surfaced and exactly `BLOCKED_TOOLS` are filtered; every cs50victor tool must be categorised — additions fail the build. `test_codex_mcp_config.py` (6 tests) pins the shape of the `-c mcp_servers.*` overrides. `test_codex_invocation_shape.py` (5 tests) and `test_codex_event_matching.py` (14 tests) cover the Codex invocation and JSONL event parsing.
- **Limitations documented:**
  - **Dormant create-then-claim race** (surfaced during M6 / task #17): when multiple adapters are concurrent, a task assigned prose-only ("for codex-alice") can be claimed by a different Codex teammate that wins the race. The v7 tool surface exposes `task_create` to Codex but does not yet enforce scope. Mitigation recommended: task metadata field `restricted_to: [name, ...]` honored by `_find_and_claim`. Not blocking because live use so far has worked around it by sequencing adapter starts.
  - **Flaky `test_concurrent_appends_preserve_all_messages`** (implementer's diagnosis): carried in the test suite; known intermittent. Harmless unless triggered concurrently.
- **Conformant with `protocol-spec.md` §5:** yes. v7's additions don't regress any v6 contract behavior. Re-verified via the existing 33 v6 tests continuing to pass alongside 20+ v7-specific tests.
- **Verdict:** **SHIPPED, VALIDATED.** v7 proves first-class parity — a Codex teammate can decide to reach the team protocol during its own reasoning, not just through hand-written adapter logic.

### v7.1 — Mid-task reactivity (Codex App Server)

- **Spec'd:** Replace `codex exec` one-shot with `codex app-server` persistent JSON-RPC session. Originally opt-in behind `--app-server`; flipped to default-on in task #21 (see §7). Adapter holds a long-lived session per task; when a prose message arrives in the adapter's inbox mid-task, it is translated to `turn/steer` and injected into the running Codex turn.
- **Implemented:** `src/claude_anyteam/app_server.py` (391 lines) — `AppServerClient` handling subprocess lifecycle, reader thread, JSON-RPC request/response with timeout, notifications queue with predicate-waiting, well-known helpers (`initialize`, `thread_start`, `turn_start`, `turn_steer`, `turn_interrupt`). Integration in `loop.py::_execute_task_app_server` and `codex.py::app_server_invoke`. `--app-server | --no-app-server` CLI flag via `BooleanOptionalAction`; default on (`config.py:32` is `app_server: bool = True` after task #21).
- **Tested (live, inbound direction — task #19):**

  ```
  07:11:25.003  registration.added codex-alice
  07:11:25.006  task.claimed task_id=19
  07:11:25.009  app_server.start args=[codex, app-server]
  07:11:27.079  app_server.thread_started thread_id=019db407-8d55-...
  07:11:42.842  task.steer_queued from_=implementer text_head="Mid-task steer: skip benchmarks..."
  07:11:43.346  app_server.steer_sent turn_id=019db407-8d6c-... text_head="mid-task message from implementer..."
  07:12:06.700  app_server.closed
  07:12:06.706  task.completed task_id=19 files=1
  ```

  Load-bearing: `task.steer_queued` and `app_server.steer_sent` both fired. Artifact `m2-artifacts/sortit.py` contains **no** `benchmark()` function and **no** `--benchmark` flag — Codex received the steer and honored it in the final output. 41.7 seconds total; steer arrived 17.8s in, delivered to Codex 0.5s later.

- **Scope-correction on the live-acceptance claim (made during task #23 amendment, mirroring `v7.1-notes.md` §7.1):** *"v7.1 shipped; live validation covered `turn/steer` (adapter→Codex inbound direction) only. Outbound wrapper MCP tool access — Codex calling the team protocol from its own reasoning — was regressed from v7's fresh-exec semantics to App Server mode due to env-var inheritance on MCP subprocess spawn. Regression caught during task #22 sanity probes (2026-04-22); fixed via wrapper CLI-args identity patch (Fix B); re-validated under task #22 rerun."* The original task #6 sign-off said "SHIPPED, LIVE-VALIDATED" without distinguishing inbound from outbound — that was overly broad. Task #19 didn't exercise wrapper tool use, so the outbound direction had not been live-proven at v7.1 ship time.

- **Tested (live, outbound direction — task #30 post-Fix-B):** S3-rerun on post-fix codex-alice (PID 1389788), 44 seconds:

  ```
  18:23:32.629  codex.tool_call started   server=claude_anyteam_wrapper tool=task_update   args={task_id:"30", active_form:"designing cli"}
  18:23:32.637  codex.tool_call completed server=claude_anyteam_wrapper tool=task_update   (11ms, structured result)
  18:23:46.465  codex.tool_call started   server=claude_anyteam_wrapper tool=task_update   args={task_id:"30", active_form:"running tests"}
  18:23:46.471  codex.tool_call completed server=claude_anyteam_wrapper tool=task_update   (6ms)
  18:23:55.862  codex.tool_call started   server=claude_anyteam_wrapper tool=send_message  args={to:"team-lead", body:'{"kind":"sanity_progress","probe":"S3-rerun",...}'}
  18:23:55.878  codex.tool_call completed server=claude_anyteam_wrapper tool=send_message  (17ms, result={delivered_to:"team-lead"})
  18:24:03.705  app_server.done exit_code=0 tool_call_events=6
  ```

  Three-way verification (wrapper reachable + tool calls firing + inbox delivery) landed — detail in `docs/app-server-sanity.md`. Fix B (`wrapper_server.py` accepts `--team`/`--name` CLI args with env fallback; `codex.py::app_server_invoke` passes identity via `mcp_servers.NAME.args`) resolves the regression.

- **Tested (unit):** `test_app_server_client.py` (12 tests) covers initialize roundtrip, request/notification dispatch, timeouts, idempotent close, `thread_start` params, `turn_start` with output schema, and `turn_steer` sending the correct `expectedTurnId`. `test_wrapper_contract.py` gained 5 new tests for Fix B covering CLI-args resolution, CLI-equals-form, CLI-beats-env precedence, env-fallback for backward compat, and partial-CLI-args-fall-back-to-env.
- **Limitations documented:**
  - **Server-originated requests stubbed.** `_dispatch` warns and ignores inbound server-originated requests (e.g., approval prompts). The default approval policy `"never"` set at `thread_start` means these should rarely fire; if they do they log `app_server.unhandled_server_request` rather than deadlock.
  - **Steer requires prose, not structured protocol.** The steer mechanic is triggered by any prose message in the adapter's inbox during a task run. Structured JSON messages (shutdown_request, plan_approval_request) still short-circuit to their normal handlers; only prose becomes a steer.
- **Conformant with `protocol-spec.md` §5:** yes. Every §5 requirement is satisfied; v7.1 additionally adds mid-task reactivity and tool-mediated outbound protocol access, neither of which §5 mandates but both of which strengthen the "first-class teammate" claim.
- **Verdict:** **SHIPPED, LIVE-VALIDATED (inbound + outbound, post-Fix-B).** Mid-task reactivity works end-to-end; outbound wrapper tool use works end-to-end under the default. The regression-caught-and-fixed arc is captured in `docs/app-server-sanity.md` as part of the evidence base.

### v7.2 — Cross-task context persistence (`codex exec resume`)

- **Spec'd:** Enable Codex to resume a prior session across tasks so context (conversation memory, prior tool calls) persists. Mechanism: `codex exec resume <session_id>`.
- **Historical note (task #17 → Option A):** the initial attempt was blocked at the Codex CLI layer because `codex exec resume` does NOT accept `--output-schema` on either 0.120.0 or 0.122.0 (team-lead upgraded the CLI during task #20 specifically to check). That finding — `docs/v7.2-findings.md`, authored by a v7 Codex teammate autonomously executing task #17's escalation instruction and independently verified by team-lead — is preserved as the historical motivation for Option A. The original task #6 sign-off (v1) recorded v7.2 as BLOCKED-WITH-FINDINGS at that point.
- **Implemented (Option A, per task #20):** the adapter ships `codex exec resume` as-is and validates the output in Python instead of relying on `--output-schema` at the CLI layer. Codex writes its final message to `--output-last-message <tmpfile>`; the adapter reads the file, parses JSON, validates against `schemas/task-complete.schema.json` via `jsonschema`. On validation failure: one retry with a tightened prompt ("PRIOR ATTEMPT FAILED: Return ONLY the JSON object..."), then mark blocked. Session-id threading: captured from the `thread.started` JSONL event on a fresh run, stored in `LoopState.codex_session_id`, passed back into `codex.run(..., resume_session_id=...)` on the next invocation for the same adapter identity. Implementation detail: `src/claude_anyteam/schema_validation.py`, extensions to `codex.py::run` and `loop.py::_invoke_codex_for_task`. See `docs/v7.2-notes.md` for full specification.
- **Tested (live):** chained tasks #24 → #25. Task #24 asked Codex to write a trivial utility function. Task #25, claimed by the same adapter identity on the resumed session, asked Codex to *"refactor the function you just wrote."* Codex's `task_complete` summary for #25 explicitly said *"refactoring the **previously added** `triangle_number(n)` function"* — first-person evidence that session memory crossed the task boundary via `exec resume`. Task #25's output file changes matched the refactor description; the adapter's schema-validation path accepted Codex's output without retry.
- **Tested (unit):** `test_fix_forward.py` (4 tests) and extensions to existing codex-invocation tests cover the resume-session codepath, the `--output-last-message` → JSON-validate → retry loop, and the session-id-capture-and-threading on the first fresh invocation.
- **Limitations documented:**
  - **Python-side validation replaces CLI-side schema enforcement.** If Codex produces non-conformant output, the adapter catches it; but the *rigor* is now in `schema_validation.py` rather than the CLI. If `codex exec resume` ever gains `--output-schema`, a ~15-line simplification can remove the Python validator and restore CLI-level enforcement. Recorded in `v7.2-notes.md` §2.
  - **Session-id threading is per-adapter-identity, not per-team.** Two adapters with the same team name but different teammate names would have independent sessions. This matches v7.1's thread-per-task topology — v7.2 just extends memory across tasks for a single teammate.
- **Evidence attribution (preserved from v1 sign-off):** `v7.2-findings.md` was authored by Codex via the v7 adapter autonomously executing task #17's escalation instruction. Team-lead independently verified the reproducer. The finding is concrete and reproducible, and remains load-bearing evidence — it's what motivated Option A over blocking indefinitely. A Codex teammate using its v7 capabilities to diagnose a later-version limitation is itself a positive datapoint for v7's tool-access thesis; Option A's live acceptance completes the arc.
- **Conformant with `protocol-spec.md` §5:** yes. v7.2 doesn't add or remove any §5 requirement; it changes how Codex is invoked on the second-and-subsequent task for the same adapter identity, and the structured `task_complete` output is still produced (just validated at the adapter rather than at the CLI).
- **Verdict:** **SHIPPED, LIVE-VALIDATED (Option A).** Cross-task session memory works end-to-end via `codex exec resume` + Python-side schema validation. The rigor lost at the CLI (no `--output-schema` on resume) is recovered at the adapter (parse + validate + retry).

---

## 2. Spec compliance: `protocol-spec.md` v1.1 §5 teammate-contract checklist

Applied to the shipped implementation (v6 + v7 + v7.1 combined).

| §5 requirement | Status | Implementation |
|---|---|---|
| §5.1 Read team config at startup | ✓ | `registration.register` reads `~/.claude/teams/{team}/config.json` at startup; aborts if absent. |
| §5.1 Extract own agentId | ✓ | From `CLAUDE_AGENT_ID`/`CLAUDE_ANYTEAM_NAME` env + `--name` CLI flag; identity is stable from `Settings`. |
| §5.1 Discover other teammates via members array | ✓ | `protocol_io.read_config()` exposes the config; `_target_task_for_plan` and `send_*` helpers route by name. |
| §5.1 Persistent identity | ✓ | Identity is immutable for an adapter process's lifetime. Re-registration is idempotent (no duplicate entry). |
| §5.2 Poll inbox at 1–5s intervals | ✓ | Default 1.5s per `Settings.poll_interval_s`; overridable via `--poll-s` / env. |
| §5.2 Process messages in timestamp order | ✓ | cs50victor's `read_inbox` returns chronological order; loop iterates in order. |
| §5.2 Handle shutdown_request, plan_approval_request, task_assignment, prose | ✓ | `_handle_message` dispatches on `type`/`kind`; prose is logged at debug level. v7.1 additionally routes prose to the steer queue when under `--app-server`. |
| §5.2 Respond to shutdown_request with shutdown_response | ✓ | `_handle_shutdown` replies approve-unless-mid-task with `in-flight task #N` feedback on reject. Unit-tested (`test_loop_unit.py::test_shutdown_while_mid_task_rejects_with_feedback`) and live-validated on two adapters. |
| §5.2 Respond to plan_approval_request appropriately | ✓ | Default policy: drop with warning when `planModeRequired=false`. Opt-in: generate via `codex exec --output-schema plan.schema.json`, retry once tightened, mark blocked on dual failure. Live-exercised in task #13 probe (retry-and-block path; plan generation itself hit the stdin bug — see v6 limitations). |
| §5.2 Send messages via SendMessage or direct inbox writes | ✓ | `protocol_io.send_*` wrappers use cs50victor's messaging layer (direct file writes). |
| §5.3 Read task list | ✓ | `protocol_io.list_tasks` imports cs50victor's tasks module. |
| §5.3 Identify unblocked pending tasks | ✓ | `_find_and_claim` filters to `status=pending` and unblocked via `_blocked(all_tasks, t)`. Prefers assigned-to-self over unassigned. Empty-string `owner` tolerated (M2-regression-tested). |
| §5.3 Claim via task_update (owner + status=in_progress) | ✓ | `protocol_io.claim_task` atomically sets owner and status. Task-claim races handled via `ValueError` fallthrough. |
| §5.3 Update to completed when done | ✓ | `_execute_task` calls `update_task(status="completed")`; v7 variant and v7.1 variant both converge on the same call. |
| §5.3 Honor task blocking | ✓ | `_blocked` walks `blockedBy` and checks for `completed`/`deleted`. |
| §5.4 Detect idle | ✓ | `_has_claimable` returns False when no unblocked assigned/unassigned pending task exists; triggers `idle_notification`. |
| §5.4 Send idle_notification | ✓ | `protocol_io.send_idle_notification` emits JSON-wrapped protocol message. Rate-limited to once per 60s (live-confirmed). 176 idle_notifications observed across all teammates during validation — 123 from Codex teammates alone. |
| §5.4 Polling loop for new work | ✓ | Main loop combines inbox drain + task scan + idle + sleep. |
| §5.4 Gracefully handle shutdown_request while idle | ✓ | Approve path live-validated; deregisters (config + inbox cleanup) before exit. |
| §5.5 SendMessage / TaskUpdate / TaskList / (TaskCreate) | ✓ | All plumbed through cs50victor (option C in §5.5: direct file writes bypassing harness tool layer). v7 exposes a narrowed subset to Codex itself via the wrapper MCP server; v7.1-under-default had an env-inheritance regression in that path (caught at #22, fixed via Fix B, re-validated at #30). |
| §5.6 Graceful degradation | ✓ | `parse_protocol_text` returns None on malformed JSON (treated as prose); `update_task` wraps in try/except; a cs50victor exception in the loop is caught at `_main_loop`'s outer boundary and logged without crashing. `protocol_io.read_inbox` swallows `FileNotFoundError` and `JSONDecodeError`. |

**Every requirement in §5 has a concrete implementation path. Full pass.**

---

## 3. The thesis question

**Prior conclusion being challenged:** "Only an LLM wrapper around Codex-MCP works for a Codex teammate in Claude Code's agent-team protocol."

**Refutation, at four distinct levels:**

### Level 1 — Feasibility (v6)

The protocol itself can be spoken by a non-Claude-LLM process. A Python adapter importing cs50victor as a library, self-registering via direct config write, polling inbox, claiming tasks, invoking `codex exec --json --output-schema`, and responding to shutdown/idle/plan messages via fixed-shape JSON payloads — performs the full teammate contract with zero Claude LLM in its process tree. Every one of `protocol-spec.md` §5's requirements is implemented without Claude-side reasoning. **Refuted.**

### Level 2 — First-class parity (v7)

A native Claude teammate reasons about code and protocol in a single LLM pass ("I'm stuck, let me ask X"). v6 split that into two processes (Codex for code, Python for protocol) — a feasible configuration but not a peer-equivalent one. v7 gives Codex MCP tool access to a narrowed subset of the protocol operations (`send_message`, `task_update`, `task_create`, `read_inbox`, `task_list`, `read_config`), pre-filling identity so Codex can't accidentally send as the wrong teammate, and blocking destructive lifecycle tools at the wrapper boundary. M5 and M6 tasks showed Codex invoking these tools during its own reasoning. **First-class parity refuted as well** — the "only LLM wrapper works" claim extends even to the peer-behavior interpretation.

### Level 3 — Mid-task reactivity (v7.1)

A native Claude teammate handling a prose interruption mid-task can adjust behavior within the same conversation turn. v7's one-shot `codex exec` invocation didn't expose that capability — the Codex turn was closed after it started. v7.1 swaps the invocation surface to the Codex App Server (JSON-RPC 2.0, stdio transport) and provides a `turn/steer` primitive that injects additional input into the in-flight turn. Task #19 proved the mechanic live: a prose steer arrived 17.8s into a 41.7s task; `task.steer_queued` and `app_server.steer_sent` both fired; `turn/steer` was submitted to Codex 0.5s after the prose arrived; the final artifact honored the steer (no `benchmark()` function, no `--benchmark` flag). **Mid-task reactivity refuted.**

### Level 4 — Cross-task session memory (v7.2)

A native Claude teammate carries context forward across multiple task assignments on a single team — it remembers what it did on the prior task and can be given an imperative like "refactor the function you just wrote" without re-specifying what "the function" was. v6/v7/v7.1 all invoke Codex fresh per task (thread-per-task topology), so this capability was initially missing. v7.2 closes the gap via `codex exec resume <session_id>` with Python-side schema validation recovering the rigor that `codex exec resume --output-schema` would have provided at the CLI layer (not supported on codex-cli 0.120.0 or 0.122.0; see v7.2 entry). Tasks #24 → #25 proved it live: Codex's self-reported summary on #25 said *"refactoring the **previously added** `triangle_number(n)` function"* — first-person evidence of session memory crossing the task boundary. **Cross-task session memory refuted.**

### Summary verdict on the thesis

The prior conclusion is **refuted at all four levels** — feasibility (v6), first-class parity (v7), mid-task reactivity (v7.1), cross-task session memory (v7.2). All four capability levels shipped. The project delivered on the central thesis in full. No level remains scope-bound or deferred.

Two of the four levels (v7.1, v7.2) required ingenuity to ship despite CLI-surface limitations — v7.1's App Server integration had a subprocess-env-inheritance regression caught during sanity testing and fixed via Fix B, and v7.2's `codex exec resume` lacks `--output-schema` so Option A adds Python-side validation. Both are shipped with live first-person evidence, and the team's honest-reporting discipline captured the journey rather than papering over it.

---

## 4. Known limitations carried forward

These are findings that did not block sign-off but should be tracked.

### 4.1 Resolved during the primary arc

These were bugs or gaps that existed at some point and are now fixed. Retained for provenance.

1. **Outbound wrapper MCP regression under App Server default (v7.1).** Caught during task #22 sanity S3 probe (wrapper not reachable from Codex's session under App Server mode due to env-var inheritance on MCP subprocess spawn). Root-caused to `wrapper_server.py::_identity()` reading env vars that the App Server did not forward to MCP child subprocesses. Fixed via Fix B (~15 LoC): wrapper accepts `--team`/`--name` CLI args with env fallback; `codex.py::app_server_invoke` passes identity via `mcp_servers.NAME.args`. Post-fix re-validated on task #30 with three-way verification. Full story in `docs/app-server-sanity.md`.
2. **v7.2 initial block on `codex exec resume` not accepting `--output-schema`.** Verified against codex-cli 0.120.0 and 0.122.0. Resolved via Option A: `codex exec resume` + `--output-last-message` + Python-side `jsonschema` validation with a retry-then-block contract. Live-validated on chained tasks #24 → #25.

### 4.2 Bugs retained as fix-forward

3. **Missing `stdin=subprocess.DEVNULL` in `codex.py::run`** (v6-mode only). Plan-mode invocation consistently hit `Reading additional input from stdin...` on both retry attempts during task #5 validation on the fresh-`codex exec` path. Now mostly moot because the App Server default (post task #21) routes through `codex app-server`'s JSON-RPC stdio — the fresh-`codex exec` path is `--no-app-server` territory. If a user explicitly opts out of App Server, the bug still applies to that path. Relevant if v6-mode is intentionally re-enabled.
4. **`_mark_blocked` writes metadata onto already-completed tasks.** Task #13 ended up `status: completed` AND `metadata.blocked_reason: "plan generation failed twice..."` when the task-execution path completed first and the plan-mode path ran `_mark_blocked` afterward. Minor; recommended fix: gate `_mark_blocked` on current status.

### 4.3 Design findings, not bugs

5. **`planModeRequired: true` is opt-in response, not execution gate.** A plan-mode adapter still claims and executes tasks via the normal path; it answers `plan_approval_request` messages if they arrive. If the project intends "plan mode blocks execution until approved" semantics, that's a design change. Documented per implementer's intended design (architecture-decision §4.5).
6. **Dormant create-then-claim race.** Cross-teammate task claim collisions can happen when multiple adapters run concurrently — task-description prose like "this task is for codex-alice" is not machine-enforced. Mitigation: add task metadata field `claim_restricted_to: [name, ...]` honored by `_find_and_claim`. Became visible when v7 adapter and v6 plan-mode adapter coexisted during validation.
7. **Hot-join UI chrome absence.** Self-registered peers lack `targetColor` on routing output — a harness caching artifact. Documented in architecture-decision §8; cosmetic only.
8. **Flaky `test_concurrent_appends_preserve_all_messages`** (test suite): intermittent failure noted by implementer. Carried in the suite.
9. **Server-originated App Server requests stubbed.** v7.1's `AppServerClient._dispatch` warns and ignores inbound JSON-RPC requests from the server (e.g., approval prompts). With `approvalPolicy="never"` this should rarely fire. If the project wants a richer approval protocol, this stub needs replacing.
10. **Python-side schema validation replaces CLI-side enforcement on v7.2 resume paths.** If `codex exec resume` ever gains `--output-schema`, a ~15-line simplification can restore CLI-level enforcement (see v7.2-notes §2). Until then, `schema_validation.py` is the contract enforcer for resume invocations.

### 4.4 Scope-bounded (not bugs, not limitations)

11. **§9 criterion #1 cold-join timing** not live-exercised — would require team restart. Accepted on combined strength of M0 evidence (harness tolerates self-registration) and hot-join functional equivalence.
12. **§9 criterion #6 reject-while-mid-task** unit-tested, not live. Live window too narrow given Codex's sub-40s completion times.

---

## 5. Recommended next steps

What a productization or "v8" cycle should target, in priority order. Several items the original v1 sign-off listed have since landed (App Server default flip, v7.2 Option A); this list reflects what remains.

1. **Fix the stdin bug (§4.2 item 3).** One-line change in `codex.py::run`: add `stdin=subprocess.DEVNULL` to the `subprocess.run(...)` call on the fresh-`codex exec` path. Only affects `--no-app-server` mode. Trivial; do first if anyone actually plans to use v6-mode explicitly.
2. **Fix `_mark_blocked`'s already-completed-task interaction (§4.2 item 4).** Gate on current status; skip metadata update if task is already `completed`, or use a more specific failure reason that doesn't contradict the status.
3. **`claim_restricted_to` task metadata (§4.3 item 6).** Add the field to task schema and honor it in `_find_and_claim`. Mitigates concurrent-adapter collisions without requiring prose-based coordination in task descriptions.
4. **CLI simplification once `codex exec resume --output-schema` exists.** Track upstream; once supported, `schema_validation.py` can be replaced with the CLI's enforcement for a ~15-line reduction. Correctness is unchanged either way.
5. **Port to TypeScript/Node (productization).** Current architecture is a feasibility prototype. The Python-only topology (cs50victor is Python) was deliberately chosen to minimize surface area; for a shipped product a TS adapter with cs50victor re-implemented or wrapped would give a single static binary and easier distribution. Priority is lower than 1–3 — the prototype works, a port is about packaging.
6. **Harden server-originated App Server requests (§4.3 item 9).** Replace the stub with a policy-driven handler (approvals, interjection requests, etc.) once the use case is concrete.
7. **Richer session registry for v7.2.** Current session-id threading is in-memory on `LoopState`; if the adapter restarts mid-session, memory is lost. A simple persistence layer (JSON file keyed by `{team, name}`) would let restarts resume. Not hard; relevant if the adapter is run as a long-lived service.
8. **End-to-end integration test for cross-adapter races.** Add a test that spawns two adapters, assigns overlapping tasks, and asserts no double-claim (beyond unit tests of `_find_and_claim`'s single-adapter race semantics).
9. **Fix flaky `test_concurrent_appends_preserve_all_messages` (§4.3 item 8).** Minor quality-of-life improvement for the test suite; not blocking anything.

---

## 6. Sign-off

**All four scoped versions shipped with live validation.** v6 and v7 in the primary arc; v7.1 with inbound-steer live-validated at ship-time and outbound-wrapper-tool-access live-validated post-Fix-B; v7.2 via Option A with chained tasks #24 → #25 as first-person evidence of cross-task session memory.

The central thesis is **refuted at all four levels** — feasibility (v6), first-class parity (v7), mid-task reactivity (v7.1), cross-task session memory (v7.2). The prior "only LLM wrapper works" conclusion does not survive.

Implementation is conformant with `protocol-spec.md` v1.1 §5 across every requirement. 130 tests pass. The regression caught during task #22 (outbound wrapper MCP under App Server default) was root-caused, fixed via Fix B, and re-validated on the same day — resolved, not outstanding. Two non-blocking bugs (§4.2: stdin-only-affects-v6-mode, `_mark_blocked` edge-case) recommended as fix-forward items.

**Signed off (amended).** — reviewer, 2026-04-22.

### Original task #6 sign-off (preserved for provenance)

> *"Three of four scoped versions shipped with full live validation or equivalent evidence. The fourth (v7.2) is blocked at the Codex CLI layer with a concrete, reproducible finding (authored autonomously by a v7 Codex teammate, independently verified by team-lead) and a clear re-entry path. The central thesis is refuted at levels 1, 2, and 3 — feasibility, first-class parity, mid-task reactivity. Level 4 (session memory) is scope-bounded pending upstream work. Implementation is conformant with `protocol-spec.md` v1.1 §5 across every requirement. 88 tests pass. No blocking bugs in the critical path; two non-blocking bugs (§4.1) recommended as fix-forward items. **Signed off.** — reviewer, 2026-04-22."*

The v2 amendment upgrades that statement in three ways: v7.1 conformance narrowed then re-expanded post-fix, v7.2 flipped to shipped via Option A, thesis refutation now covers all four levels. The original sign-off was accurate at the time it was written; the amendment reflects what landed after.

---

## 7. App Server default flip (task #21)

**Rationale.** The original v7.1 ship had App Server behind `--app-server` as opt-in. Post-v7.1 user framing was "Agent Teams itself is experimental, so it's fine" — which made flipping the default acceptable. Task #21 made it default-on (`config.py:32` is `app_server: bool = True`; CLI became `--app-server | --no-app-server` via `BooleanOptionalAction`; both forms honored, passing neither falls through to the default).

**Why the flip matters.** App Server's bidirectional semantics (mid-task steer, session memory across `thread/start` calls, streamed agent events, potential for richer approval protocols) are strictly a superset of `codex exec`'s one-shot model. Under v7.1-as-default, every teammate task gets the richer primitives by default; users who need the v6 semantics (e.g., cross-task persistence via `exec resume` per v7.2 Option A) opt out explicitly.

**What nearly blocked the flip.** Task #22 sanity probes surfaced an outbound wrapper MCP regression — under App Server mode, Codex's MCP child subprocess was not inheriting the adapter's env vars, so the wrapper server died during handshake and Codex saw no protocol tools. The probe caught it (Codex's own `task_complete` summary explicitly reported "the requested MCP protocol tools were not exposed in this session's callable tool list"). Root cause and fix are covered in §1's v7.1 entry and in `docs/app-server-sanity.md`; Fix B (~15 LoC touching `wrapper_server.py` identity resolution and `codex.py::app_server_invoke` subprocess args) made the flip safe.

**Sanity evidence summary.** Five probes across two passes (pre-fix S1/S2/S3, post-fix S3-rerun + S4):
- **S1 (multi-file package)** — PASS.
- **S2 (bugfix cycle)** — PASS. Multi-turn Codex reasoning under a single App Server thread works.
- **S3 (minigrep, pre-fix)** — REGRESSION CAUGHT. Load-bearing finding; diagnosis and fix followed.
- **S3-rerun (wordcount, post-fix, task #30)** — PASS. Three-way verification: `server=claude_anyteam_wrapper` tool_call events for 2× `task_update` + 1× `send_message`, structured results with zero errors, `sanity_progress` JSON delivered to team-lead inbox byte-exactly.
- **S4 (shutdown handshake, post-fix)** — PASS. `shutdown_response {approve: true}` → all four deregister events in order → codex-alice removed from config. 1.2s total.

Full detail and log traces in `docs/app-server-sanity.md`.

**Observed caveats under default.** (i) Thread lifecycle is per-task (fresh `thread_started` → `app_server.closed` each time), consistent with v7-architecture §4 Option Y design. Persistent-session semantics intentionally live on `--no-app-server` + v7.2 Option A. (ii) App Server adds ~3 seconds of thread-startup overhead per task that fresh-exec doesn't pay; proportionally larger for very short tasks. Not a regression; recorded as overhead. (iii) Hot-join UI chrome (`targetColor`) remains absent for self-registered peers under the default; a cosmetic harness-caching artifact. (iv) Server-originated approval requests from Codex are stubbed (see §4.3 item 9); if an operator enables approval policies richer than `"never"` this limitation matters.

**Bottom line.** The App Server default flip shipped with honest regression evidence on both sides of Fix B. The sanity test paid for itself: a pre-existing gap (present since v7.1 shipped, undetected because task #19's probe didn't require outbound tool use) was surfaced by live probes, diagnosed within the day, fixed in ~15 LoC, and re-validated with fresh independent evidence before the default was considered stable.
