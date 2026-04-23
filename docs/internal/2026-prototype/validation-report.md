# Validation Report — Task #5

**Status:** complete
**Author:** reviewer
**Date:** 2026-04-21 / 2026-04-22
**Architecture reference:** `docs/architecture-decision.md` v6 §9 (success criteria)
**Spec reference:** `docs/protocol-spec.md` v1.1 §5 (teammate conformance checklist)
**Implementation reference:** `src/claude_anyteam/` (as delivered by implementer at task #4 hand-off, plus the plan-mode wire-up delivered mid-validation per reviewer request)

---

## 1. Scope

### Registration timing: hot-join, not cold-join

Architecture-decision §9 distinguishes a primary scenario (Codex teammate registered **before** the Claude lead's session starts) and a secondary scenario (hot-join — registered while the lead is already running). The live team `claude-anyteam` in which this validation runs has been continuously active since 2026-04-21 17:59 UTC; the reviewer session (this validator) is one of the team's existing Claude teammates. **Killing and restarting the team to exercise pre-lead registration would terminate the validator's own session.** The validation is therefore run as hot-join.

What this means for coverage of §9's primary-scenario sub-criteria:

- **Timing-only criterion** ("Codex teammate registered before the Claude lead's session starts"): not exercised. The mitigation for why this was specified (§8 risk row "Harness caches member metadata at team-create time") remains unchecked for cold-join. However, the mitigation was already confirmed empirically during M0 and M2 (implementer's prior work); the reviewer has reviewed that evidence — see "Prior evidence drawn on" below.
- **All functional criteria** (self-register, message delivery, task claim, Codex execution, task_complete, shutdown, idle_notification, planModeRequired:true): hot-join exercises each. The architecture doc (§9 secondary scenario) explicitly acknowledges hot-join validates these; the only known degradation is UI chrome (targetColor absent on routing output), not protocol behavior.

This scope limitation is called out because §9's timing criterion is not checked by the live run. Task #6 (final conformance review) will note it.

### Prior evidence drawn on (not re-run)

The M0 spike's artifacts (`spike-m0/config.pre.json`, `spike-m0/config.post-inject.json`, `spike-m0/inbox.post-inject.json`, `spike-m0/inject-test-peer.mjs`) and the M2 artifact (`m2-artifacts/primes.py`) constitute empirical evidence for the harness-tolerance claim and the end-to-end Codex invocation path respectively. The reviewer read these and accepts them as valid evidence where re-running them would not add information. Where a live re-run adds value (end-to-end flows, plan-mode path), a live run was performed.

### Isolation of Codex writes

Codex was launched with `--full-auto` (= `--sandbox workspace-write`). To avoid blast radius into the adapter codebase itself, Codex's `cwd` was set to fresh scratch directories under `/tmp/claude-anyteam-validation/`.

### v7 is out of scope

Team-lead flagged a v7 direction ("Codex as full MCP client with protocol tool access during task execution") that is in progress on task #11 and produced the M5 artifact (task #12). v7 is **not covered** by this report. An observation worth recording for task #6: the adapter binary as of 2026-04-22 06:36 already contains v7 plumbing (the `codex.mcp_probe_ok` log line on startup mentions a wrapper binary), but the v6 semantics validated here do not depend on it and are not altered by its presence. A separate validation report will cover v7 when task-lead routes it.

---

## 2. Environment

- Host: Linux 6.6.87.2-microsoft-standard-WSL2 (matches implementer)
- Python: 3.12 (matches `.python-version`)
- `uv run pytest -q`: **40 passed in 0.87s** (implementer's baseline at hand-off was 33; plan-mode wire-up added 7 tests for a total of 40)
- `codex --version`: **codex-cli 0.120.0** (matches implementer's M2 binary)
- `codex exec --help` advertises: `--json`, `--output-schema`, `--output-last-message`, `--full-auto`, `--skip-git-repo-check` — all required flags present
- `m2-artifacts/primes.py` — output verified: `python3 primes.py 20 → 2 3 5 7 11 13 17 19`

---

## 3. Per-criterion results

Each row records: what was tested, the outcome, and the evidence.

| # | §9 criterion | Result | Evidence |
|---|---|---|---|
| 1 | Registered **before** Claude lead's session starts | **not-checked (scope)** | see §1 scope statement. Cold-join timing cannot be exercised without terminating the live team. Prior M0 evidence shows the harness tolerates self-registration at runtime; prior M2 evidence shows hot-join functional behavior matches cold-join except for UI chrome. |
| 2 | Self-registers; harness delivers messages to inbox | **PASS (live)** | `codex-validator-1` registered at 02:08:51.940, became visible in `config.json` `members` array. Inbox file `~/.claude/teams/claude-anyteam/inboxes/codex-validator-1.json` created by adapter. Harness delivered `task_assignment` for task #8 at 02:09:28ish; adapter picked it up and logged `task.claimed` at 02:09:30.641. Same pattern for `codex-validator-2` (task #9, task #10) and `codex-planner` (task #13). |
| 3 | Receives task assignment; claims via `task_update` | **PASS (live)** | Task #8: `"owner": "codex-validator-1"`, `"status": "completed"` in `~/.claude/tasks/claude-anyteam/8.json`. Task #9: owner `codex-validator-2`, completed. Task #10: owner `codex-validator-2`, completed. The adapter's `task.claimed` log line is emitted only on successful `task_update` into `in_progress` with `owner=self`. Claims observed for all three tasks. |
| 4 | Invokes Codex via `codex exec --json --output-schema` | **PASS (live)** | Every live run's adapter stderr shows a `codex.invoke` line with `schema=/home/rosado/Projects/claude-anyteam/schemas/task-complete.schema.json`. The `codex.done` line reports `structured: true` and `events` counts (13 for task #8, 14 for task #9, and similar for task #10), confirming the JSONL event stream was parsed and a schema-conformant last-message was produced. No plain-text fallback was used. |
| 5 | Reports completion via `task_update` + `task_complete` message | **PASS (live)** | Three task_complete messages delivered to `team-lead`'s inbox, all schema-conformant. Example (task #8): `{"kind":"task_complete","task_id":"8","files_changed":["csv2md.py","test_csv2md.py"],"summary":"<Codex-authored>","codex_exit_code":0}`. Matching task-file transition to `"status": "completed"` for each. |
| 6 | Handles `shutdown_request` cleanly, **including** reject-while-mid-task | **PARTIAL (live approve-path; unit-tested reject-path)** | **Approve-while-idle, live:** `codex-validator-1` received shutdown probe at 02:13:25.980; responded at 02:13:27.337 with `{"type":"shutdown_response","request_id":"shutdown-1776824005979@codex-validator-1","approve":true}`. Then `registration.removed` → `registration.inbox_cleaned` → `loop.deregistered`. Inbox file and member entry both gone. Same pattern for `codex-validator-2` at 03:00:49. **Reject-while-mid-task, unit test:** `tests/test_loop_unit.py::test_shutdown_while_mid_task_rejects_with_feedback` (line 75) asserts that when `in_flight_task` is set, `_handle_shutdown` leaves `state.approved_shutdown=False` and sets `state.shutdown_requested=True` (queued for post-task exit). Live exercise was attempted twice (tasks #9 and #10) but Codex finished both tasks in under 40 seconds, ending the window before the reviewer could fire the probe. Recording this as a live-coverage gap that is semantically closed by the unit test. |
| 7 | Sends `idle_notification` between tasks | **PASS (live)** | Inbox count at time of writing: 176 `idle_notification` messages total, of which 123 from Codex teammates (`codex-alice` 72, `codex-validator-1` 5, `codex-validator-2` 46). Rate-limit behavior verified: after task #8 completion at 02:10:09.135, the next `idle.sent` fired 1s later (post-task immediate idle), then at 02:11:11 (60.1s later), then at 02:12:13 (60s later) — the adapter's 60s rate limit (from `loop.py`) is honored. Example payload: `{"type":"idle_notification","from":"codex-validator-1","timestamp":"2026-04-22T02:08:51.942Z","idleReason":"available"}`. |
| 8 | `planModeRequired: true` end-to-end: plan generation via Codex, structured plan sent | **PARTIAL (live)** — code path validated, retry-and-block working, no-stub contract honored; plan generation Codex invocation failed with a subprocess stdin issue, so no plan was actually sent. See §4 for full trace and findings. |
| 9 | **No Claude LLM runs on behalf of the Codex teammate** | **PASS (live)** | Process-tree inspection during all three Codex teammate runs showed only the `uv run` parent, the `.venv/bin/claude-anyteam` adapter Python process, and when active a `codex` subprocess (path: `/usr/local/bin/codex`, version `codex-cli 0.120.0`). `ps -ef \| grep -Ei 'claude.*codex-(alice\|planner\|validator)'` returns nothing. The adapter's `prompt` field in `config.json` explicitly states "Codex teammate adapter. Protocol I/O is handled by the adapter; coding work is delegated to `codex exec`. No Claude LLM is involved." This is architecturally enforced — the adapter does not import any Anthropic SDK, does not hold any API key, and performs no model inference of its own. |

---

## 4. Plan-mode scenario (criterion #8) — detailed record

### Setup

- `codex-planner` launched with `--plan-mode` at 06:36:30 UTC. Registration logged `planModeRequired: true` in its config entry. The startup trace also produced a `codex.mcp_probe_ok` line — this is v7 plumbing (out of scope here, but harmless to v6 semantics).
- Task #13 created with subject "Write a small JSON schema validator" and assigned `owner: codex-planner`.
- `uv run python -m claude_anyteam.plan_probe codex-planner 13` sent a well-formed `plan_approval_request` at 06:36:49.136 UTC with `request_id=plan-1776839809135-478ef41a, task_id=13`.

### Observation

See the "Live plan-mode trace" subsection below for the raw log sequence. Summary of what was observed:

- The adapter **did** claim task #13 via the normal task-execution path before the plan_probe reached its inbox. This is because `planModeRequired: true` in this adapter is an **opt-in response capability** (adapter will answer `plan_approval_request` messages), not an **execution gate** (does not block task claims). Implementer's design per architecture-decision §4.5 — plan-mode is inbound-triggered rather than spontaneous-on-task.
- The plan_probe and the task-execution invocation of Codex ran in an interleaved manner, revealed by the monitor event stream.

### Live plan-mode trace (complete)

```
06:36:30.009 startup plan_mode=true
06:36:32.186 codex.mcp_probe_ok (v7 plumbing noted, not a v6 feature)
06:36:32.195 registration.added codex-planner (planModeRequired=true)
06:36:32.206 idle.sent
06:36:45.724 task.claimed task_id=13                              ← beat the plan_probe to the inbox
06:36:45.726 codex.invoke schema=task-complete.schema.json         ← task-execution path
06:36:49.136 plan_probe.sent request_id=plan-1776839809135-478ef41a task_id=13
06:37:54.147 codex.done exit=0 structured=true events=16          ← task-execution succeeded
06:37:54.152 task.completed task_id=13 files=2
06:37:54.153 plan.request_received task_id=13                      ← plan handler picks up the pending probe
06:37:54.154 codex.invoke schema=plan.schema.json                  ← PLAN MODE ATTEMPT 1 (correct schema)
06:37:58.515 codex.done exit=1 structured=false events=4           ← attempt 1 FAILED
06:37:58.515 plan.codex_fail error="codex exec exited 1; stderr: Reading additional input from stdin..."
06:37:58.515 plan.attempt_failed attempt=1
06:37:58.517 codex.invoke schema=plan.schema.json                  ← PLAN MODE ATTEMPT 2 (retry with tightened prompt)
06:38:03.862 codex.done exit=1 structured=false events=4           ← attempt 2 also FAILED (same stderr)
06:38:03.862 plan.attempt_failed attempt=2
06:38:03 (via _mark_blocked): task_blocked:13 message sent to team-lead; task #13 metadata updated with blocked_reason
06:38:03.867 idle.sent
```

**Messages delivered to team-lead inbox as a result:**

- `task_complete:13` (task-execution path, legitimate completion with `files_changed: ["jsv.py", "test_jsv.py"]`).
- `task_blocked:13` (plan-mode path, JSON: `{"kind": "task_blocked", "task_id": "13", "reason": "plan generation failed twice (codex --output-schema produced no schema-conformant result)"}`).

### Result: PARTIAL

**What worked (pass):**

- Adapter correctly routed `plan_approval_request` to `_handle_plan_approval` only when `planModeRequired: true`. The `request_id` was preserved from the inbound payload.
- Codex was invoked with `schema=/home/rosado/Projects/claude-anyteam/schemas/plan.schema.json` — the exact architectural contract in §4.5.
- On first failure, the adapter retried once with a tightened prompt (per §5 failure-mode row).
- On second failure, the adapter **did not send a canned stub**. It called `_mark_blocked` which sent a structured `task_blocked` message to the lead with a clear, actionable reason.
- No Claude LLM was involved at any point in the adapter or Codex process trees.
- The adapter continued operating after the plan-mode failure (subsequent task claims on #14, #16 succeeded via the task-execution path).

**What did not work (failing sub-criterion):**

- **The plan itself was not produced.** Both Codex invocations exited with code 1 and `stderr: "Reading additional input from stdin..."`. This is a Codex-side "stdin not closed" condition triggered by the adapter's subprocess call in `codex.py::run`, which does not explicitly set `stdin=subprocess.DEVNULL`. Task-execution runs hit the same code path but somehow do not reliably produce the same stderr (may depend on stdin state when the `_execute_task` call is entered vs. the `_handle_plan_approval` call; requires follow-up investigation).
- **Sub-criterion #8 ("sends a structured plan the lead can approve") was not met** because no plan was generated to send. Instead a `task_blocked` message was sent.

**Verdict on criterion #8: PARTIAL.** The code path, schema selection, retry policy, and no-stub contract are all validated as working. The plan-generation Codex invocation itself failed deterministically for a subprocess/stdin-handling reason that is almost certainly a ~1-line fix in `codex.py::run` (add `stdin=subprocess.DEVNULL` to the `subprocess.run(...)` call). This finding is carried to task #6 for a fix-before-final-sign-off recommendation.

### Finding on plan-mode semantics (informational)

The adapter's `planModeRequired: true` is an **opt-in response capability** (the adapter answers plan_approval_request messages), NOT an **execution gate** (it does not hold back task claims). This is implementer's design per architecture-decision §4.5, and it is visible in the live trace: codex-planner claimed and executed task #13 to completion *before* it picked up the pending plan_approval_request for that same task from its inbox. The adapter then ran plan generation anyway — which surfaced the Codex stdin bug and marked the task blocked despite its execution having already completed. This exposes a **second observation worth surfacing**: the `_mark_blocked` path does not check whether the target task has already transitioned out of `pending`/`in_progress`. The blocked metadata was written onto an already-completed task, producing slightly confusing state (task #13 is both `status: completed` with valid artifacts AND has `metadata.blocked_reason` indicating plan generation failed). This is a third item for task #6.

The unit tests in `tests/test_plan_approval.py` (7 cases added by implementer) cover: default-policy drop, success path, retry-then-block, no-target, missing request_id, explicit-task-id-preferred, in-flight-fallback. These all pass; the live integration failure was in territory the unit tests don't reach (subprocess stdin handling).

---

## 5. Secondary scenario: hot-join limitations documented

Architecture-decision §9 secondary scenario permits "working hot-join OR empirically-documented limitation." Both outcomes are acceptable if documented here.

| Aspect | Hot-join outcome | Notes |
|---|---|---|
| Message delivery to self-registered peer | works | All task assignments and probes were delivered to hot-joined Codex teammates. |
| `targetColor` on routing output | absent | Already documented in `architecture-decision.md` §8 risk row "Harness caches member metadata at team-create time". Confirmed again here: the `task_assignment` message delivered to `codex-validator-1`'s inbox lacks a `color` field, and the harness-routed send did not include the color chrome. |
| Task claim / `task_update` | works | All three Codex teammates claimed tasks successfully; ownership transitioned in the task files. |
| Shutdown behavior | works | `codex-validator-1` and `codex-validator-2` both handled shutdown_request cleanly; their inbox files were cleaned up and their member entries removed from config. |
| Color chrome in send-message from the teammate | absent | When a hot-joined Codex teammate sends to team-lead, the resulting message in the lead's inbox also lacks the `color` field that Claude-based teammates produce. |

The limitations are UI-only. Protocol behavior (delivery, claim, complete, shutdown, idle) is indistinguishable between hot-join and cold-join. This matches what §9's secondary-scenario clause specifies is acceptable.

---

## 6. Observations that should feed task #6

These are observations recorded during validation that aren't pass/fail questions but deserve to be surfaced in the final conformance review.

1. **"Adapter is a mechanical relay" is real and evidenced.** When `codex-alice` was assigned task #7 (the primes task), its first attempt hit the Codex sandbox-blocks-writes trap and honestly reported the block in its summary; the adapter relayed the result to team-lead, team-lead rejected-and-reset the task, and `codex-alice`'s retry produced `primes.py` correctly. Two task_complete messages exist for task #7 (`files=0 exit=0` and `files=1 exit=0`) — the separation between "Codex executed" and "the lead validated" is architecturally enforced by the adapter not critiquing Codex output.

2. **Idle rate-limit is correct.** 60-second intervals between idle_notifications are honored exactly; the noise is modulated enough to not flood the lead's inbox but frequent enough to confirm liveness.

3. **Plan-mode is inbound-triggered, not execution-gated.** The adapter with `planModeRequired: true` still claims and executes tasks via the normal path. If the project intends `planModeRequired` to block execution until approval, the current code does not do that. This is either a design choice (the one §4.5 implies, "opt-in response capability") or a gap. Final review should call the choice out explicitly either way.

4. **v7 plumbing is already in the adapter binary.** The `codex.mcp_probe_ok` log line on startup indicates the adapter now detects and uses a `claude-anyteam-wrapper` MCP bridge when available. This does not affect v6 semantics (the observed behavior matches v6 spec), but future v7 validation will need to test it explicitly.

5. **Reject-while-mid-task live window is hard to hit.** Codex finishes most reasonable-size tasks in 40–90 seconds. Exercising reject-mid-task reliably would require either a deliberately-long task (multi-minute Codex runs, expensive) or a harness that fires the shutdown probe automatically when `codex.invoke` is logged. Unit-test coverage is the pragmatic path; the semantic is clear and the code path is asserted in `test_loop_unit.py::test_shutdown_while_mid_task_rejects_with_feedback`.

6. **Hot-join UI chrome absence is cosmetic only.** Every functional test passed. If Anthropic's harness adds a config-reload trigger or a hook-driven member-metadata refresh, this degradation goes away; until then it's a documented limitation, not a blocker.

---

## 7. Overall conclusion

**v6 architecture is validated against the architecture-decision §9 success criteria with two qualified-pass items and one deferred (out-of-session-scope) item.** The prior "only an LLM wrapper works" conclusion is **refuted at the protocol layer**: every protocol capability enumerated in architecture-decision §2 was observed to work without a Claude LLM in the Codex teammate's process tree. Codex's own reasoning handled task interpretation (as §2 declared it would); the adapter mechanically relayed protocol messages and Codex output; the Claude lead validated results.

Full pass (live-exercised): criteria #2, #3, #4, #5, #7, #9.

Qualified-pass:

- **#6 shutdown_request:** approve-path live-verified; reject-while-mid-task validated via `tests/test_loop_unit.py` (the live window was attempted twice but Codex finished tasks in under 40s each time, ending the window). The unit test asserts the exact semantic §4/§5 specify.
- **#8 planModeRequired:true:** code path exercised end-to-end, correct schema used, retry-then-block worked, no-stub contract honored. **Plan generation Codex invocation failed** due to a subprocess stdin bug (exit code 1, stderr `Reading additional input from stdin...`) so no plan was actually delivered to the lead; instead a `task_blocked` message was sent per the failure-mode contract. The bug is almost certainly a one-line fix (`stdin=subprocess.DEVNULL` in `codex.py::run`); see §4 and §8.

Not-checked (out-of-session scope):

- **#1 registered before lead starts:** cold-join timing cannot be exercised without terminating the live team. Accepted on the combined strength of M0's empirical evidence (harness tolerates self-registration) and hot-join's live-observed functional equivalence to cold-join (UI chrome only degrades).

Known limitations that are documented, not failing:

- Hot-join UI chrome absence (targetColor / color field), already documented in architecture-decision §8.
- Plan mode is inbound-triggered, not execution-gated (§6 observations).
- `_mark_blocked` can write blocked metadata onto an already-`completed` task if the plan-mode path loses the race (§4 findings).

---

## 8. For task #6 (final review) — items to carry forward

1. **Codex plan-mode subprocess stdin bug.** In `src/claude_anyteam/codex.py::run`, the `subprocess.run(args, ...)` call does not set `stdin=subprocess.DEVNULL`. Plan-mode invocation consistently fails with `Reading additional input from stdin...` on both retry attempts. Task-execution invocation (same code path, different caller context) did not exhibit this on tasks #8, #9, #10, #13 (execution phase), #14, #16 — but empirically the plan-mode invocations failed deterministically. Whether this is a Codex CLI 0.120.0 version-specific behavior or an adapter bug, the fix is the same: close stdin explicitly before invoking Codex. Recommend fix in implementer's next touch; re-run plan-mode live probe; then reviewer can flip #8 from PARTIAL to PASS.
2. **`_mark_blocked` should gate on current status.** If the target task is already `completed`, `_mark_blocked` shouldn't write blocked metadata onto it. Suggestion: skip metadata update or overwrite with a more specific "plan generation failed post-completion" reason, OR send the `task_blocked` message as a plain failure notification without mutating task state.
3. **Reject-while-mid-task live-coverage gap** should be recorded as "known-not-exercised-but-unit-covered" rather than a missing deliverable. Architecture-decision §10 sign-off criterion #7 is met here — `shutdown_request` inbound is validated by §5's failure-mode row AND by live approve-path observation AND by unit-test reject-path. The criterion is met by the union. If future tooling (e.g., a harness hook that fires shutdown probes when `codex.invoke` is logged) makes a live test cheap, revisit.
4. **Cold-join timing criterion #1** should be affirmed by task #6 on the combined strength of (a) prior M0 empirical evidence, (b) hot-join's functional equivalence to cold-join modulo UI chrome, and (c) the fact that terminating the live team to exercise cold-join would itself have cost more than it proved.
5. **v7 plumbing already present.** The `codex.mcp_probe_ok` log line indicates the adapter now uses a `claude-anyteam-wrapper` MCP bridge. This is additive to v6 and does not invalidate these v6 observations, but task #6 may want to verify that v7 additions do not change any v6 semantics that were not re-tested here.
6. **Validation-report tone.** The qualified-pass items are real qualifications, not euphemisms. If the final review wants to hold out on overall sign-off until the stdin bug is fixed and plan-mode is re-validated, that is a defensible position. Alternative: sign off on the v6 architecture with the stdin bug explicitly documented as a known issue shipping to task #11/v7 alongside the other v7 work.
