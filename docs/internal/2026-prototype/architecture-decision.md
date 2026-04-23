# Architecture Decision: Codex as First-Class Teammate

**Status:** v6 — SUPERSEDED by v7 for deployment. See `docs/v7-architecture.md`. This doc remains as the proven feasibility baseline.
**Author:** team-lead
**Date:** 2026-04-21
**Revision notes (v6):** M1 prep revealed `cs50victor/claude-code-teams-mcp` is Python 100%. With user's assent, pivoted from TypeScript/Node adapter + MCP-over-stdio to **Python adapter importing cs50victor as a library**. Architecture simplifies: one process, one runtime, no MCP subprocess, no stdio transport. Protocol guarantees are unchanged (and if anything strengthened — one fewer failure surface). Reviewer's approval of v5 stands semantically — architecture is still "external self-registered peer" with cs50victor as the protocol I/O layer; the only change is collapsing the MCP stdio boundary into an in-process function-call boundary. Reviewer has been notified. User traded TS type safety + single-binary distribution potential for system simplicity; the feasibility prototype does not benefit enough from the former to justify the bilingual install story.
**Revision notes (v5):** reviewer approved v3 explicitly ("§10 #7/#8 both in", "§9 planModeRequired:true locked in"), which reaffirmed the stricter validation position and superseded a momentary retraction in their prior review. v5 restored v3's §9 plan-mode opt-in requirement, §10 criteria #7/#8, and §5/§6 opt-in-aware plan-approval wording. v5 also applied reviewer's two newly-flagged stale-text cleanups: (a) §4.2 pseudocode comment on `handlePlanApproval`, (b) §7 step 5 Codex invocation priority — align with §4.3.
**Revision notes (v3):** addressed reviewer's revise-and-resubmit: (1) resolved §6 contradiction on adapter join path — direct config write for self-registration, cs50victor as protocol I/O library; (2) rewrote §3 Option B rejection on code reuse, added §8 "Option D collapses to Option B" risk; (3) named Codex CLI flags in §4.3; (4) dropped plan-approval stub; (5) clarified MCP server is adapter-owned stdio subprocess, not in `.mcp.json`; (6) added §10 #7/#8; (7) required `planModeRequired: true` scenario in §9 and task #5.
**Inputs:** `docs/protocol-spec.md` v1.1 (task #1, revised and ready for reviewer spot-check), `docs/prior-art.md` with reviewer flags being folded in by researcher-canonical (task #2)
**Chooses between options for:** task #4 (implementation)

---

## 1. Decision

**Adopted architecture: External self-registered peer (Python), importing `cs50victor/claude-code-teams-mcp` as a library for protocol I/O, invoking OpenAI's Codex CLI for coding work.**

A single Python process (the *Codex teammate adapter*) joins a Claude Code team by self-registering (appending its entry to `~/.claude/teams/{team}/config.json`) and performing all protocol I/O — inbox reads, task updates, sending messages — through direct function calls into `cs50victor`'s Python library. The adapter invokes OpenAI's Codex CLI via subprocess for actual coding work and never routes any decision through a Claude LLM. cs50victor is used with narrow patches (in a clearly-named fork) where its schema or behavior diverges from the live Claude Code v2.1+ contract.

**Rejected options:** pure LLM wrapper (baseline), RPC-bridged with harness modification (out of scope), file-based adapter that reimplements cs50victor's work (wasteful duplication).

**Scope** was set by the user (MCP-shimmed, robust). **Runtime pivoted from TypeScript/Node to Python** after M1 prep surfaced that cs50victor is Python-only; user approved the pivot to collapse the MCP-subprocess boundary and run one homogeneous process. The MCP tool surface is unchanged in semantics — it becomes a set of Python function calls instead of stdio MCP RPCs.

---

## 2. Why This Is Defensible

The prior conclusion we are challenging — "only an LLM wrapper around Codex-MCP works" — is **refuted at the protocol layer** per `protocol-spec.md §6.2, §7.1`. No protocol capability (message composition, task interpretation, plan generation, shutdown/plan-approval responses) *requires* LLM-grade reasoning from a teammate:

| Capability | LLM needed? | Why |
|---|---|---|
| Register in team config | No | Deterministic JSON read |
| Poll inbox | No | File I/O |
| Claim / update / complete task | No | File I/O with `.lock` semantics |
| Respond to `shutdown_request` | No | Boolean decision, stub-able (`approve: true` default) |
| Respond to `plan_approval_response` | No | Boolean outcome; adapter reacts |
| Originate `plan_approval_request` | No — we skip plan mode | Policy in §4.5: spawn Codex teammate with `planModeRequired: false`. Stubbing was rejected by reviewer as non-neutral (lead LLM would either approve meaningless stubs or reject indefinitely). |
| Compose messages to teammates | No prose required | §3.5: harness treats body as opaque blob; JSON-wrapped or templated content is valid |
| Interpret assigned task | Yes, but by Codex — not Claude | Task description is passed to Codex CLI; Codex's own reasoning handles it |
| Idle detection | No | Adapter's own control loop decides when work is done |

**Codex's own reasoning counts as "the agent," not as "the LLM wrapper."** The user's objection was specifically to wrapping Codex inside a *Claude* model; delegating reasoning to Codex itself is exactly what we want.

The *actual* hard problem, per `protocol-spec.md §6.1` and §7.1, is **harness integration**: spawning a non-Claude process from Claude Code and giving it access to tool-call semantics. The external-self-registered-peer architecture sidesteps this by having the adapter not be spawned by the harness at all — it joins as a peer via direct config-file registration (M0 empirically validated) and performs all protocol I/O via cs50victor's library functions, which the harness never sees.

---

## 3. Options Considered

### Option A — LLM wrapper around Codex-MCP (the baseline)

Claude Haiku (or similar small Claude model) runs as the teammate. It uses Codex-MCP to execute coding work. The Claude LLM handles protocol messages, task interpretation, and plan generation.

- **Protocol fulfillment:** 100% (it *is* a Claude teammate)
- **Cost per turn:** Claude tokens + Codex compute
- **Latency:** Claude forward pass on every message, even protocol ACKs
- **Complexity to build:** low — it's the same as a normal Claude teammate with a Codex tool
- **Why rejected:** wastes Claude tokens on mechanical protocol handling the spec proves doesn't need reasoning; Codex becomes a subagent, not the driver; fails the "first-class teammate that *is* Codex" goal.

### Option B — Pure file-based adapter

A Node process reads and writes `~/.claude/teams/{team}/*` directly. No MCP, no RPC, no harness coordination. Bootstraps by appending itself to the team config's `members` array.

- **Protocol fulfillment:** ~80% — everything *observable* works, but there is no tool-call layer (SendMessage/TaskUpdate semantics are simulated via direct writes).
- **Cost per turn:** only Codex API/compute
- **Latency:** low (file I/O + Codex)
- **Complexity to build:** medium — lots of edge cases in file locking, inbox polling, and schema details (inbox filename is `{name}.json`, task `owner` is a name not agentId, etc.).
- **Why rejected:** forces us to reimplement file I/O, locking, JSON schema handling, and concurrency control that `cs50victor/claude-code-teams-mcp` has already written and stress-tested. Option D uses the same underlying files — the fragility concerns apply there too — but delegates the file handling to a reusable protocol I/O library. (Note: Option D *collapses to* Option B in the failure case where cs50victor is unusable or self-registration is rejected by the harness — see §8 risks.)

### Option C — RPC-bridged via existing `tmux` backend

**Reframed in v2.** Claude Code *already* has an out-of-process backend: `tmux`. The harness spawns teammates as separate `claude` processes in tmux panes, passing `--agent-id`, `--agent-name`, `--team-name`, `--parent-session-id`, `--plan-mode-required`, `--teammate-mode` as CLI flags. The teammate process uses Claude's normal tool dispatcher for SendMessage/TaskUpdate.

- **Protocol fulfillment:** 100% — this is how normal out-of-process Claude teammates work today.
- **Why rejected for our use case:** the harness spawns **`claude` specifically**, not arbitrary binaries. There is no observed hook for substituting a Codex adapter for the `claude` binary in a tmux pane. Taking this path still requires harness modification or a binary-substitution wrapper that pretends to be `claude`. Latter is feasible but deceptive and still fragile. Out of scope unless a cleaner hook is discovered.
- **Implication:** the real constraint we're designing around is "the harness only knows how to spawn `claude`." For a Codex teammate, that forces the adapter to start *independently* of the harness and *self-register* with the team — see Option D.

### Option D — MCP-mediated external self-registered peer **(chosen)**

An MCP server exposes the team protocol operations as tools (`send_message`, `read_inbox`, `task_update`, etc.). The Codex teammate adapter — a Node process — starts **independently** of the harness (not spawned by Claude Code), **self-registers** with a running team by adding its entry to the team config via the MCP server, uses the MCP server for all protocol I/O, and invokes Codex CLI for coding work.

- **Protocol fulfillment:** ~95% — proper tool-call semantics via MCP; the remaining ~5% is edges (idle detection timing, any undocumented harness-originated events, and the empirically-untested assumption that the harness tolerates config additions from a peer).
- **Cost per turn:** only Codex API/compute + small MCP overhead
- **Latency:** medium (MCP roundtrip + Codex; still far below an LLM forward pass)
- **Complexity to build:** medium-high
- **Why chosen:** uses existing infrastructure (`cs50victor/claude-code-teams-mcp`), stays within Claude Code's plugin contract (no harness fork), provides real tool semantics, is upgradable, and sidesteps the "harness only spawns `claude`" constraint by having the adapter be a peer rather than a child of the harness.

### Option E — Hybrid (file-based core with MCP upgrade path)

Considered but not chosen — user explicitly asked for the robust MCP path up-front. Retained as the fallback if Option D's self-registration assumption fails empirical test.

### Option F — File-based external self-registered peer

Same as Option D's registration and participation mechanism, but bypasses the MCP server entirely — the adapter reads and writes `~/.claude/teams/{team}/*` files directly with its own locking logic. Simpler in one sense (no MCP dependency) but duplicates file-handling logic that `cs50victor` already provides, and loses the tool-call abstraction. Retained in the options catalog only as a fallback if the MCP server turns out to be unusable; not recommended.

---

## 4. Design of the Chosen Architecture

### 4.1 Components

```
+-----------------------------------------------------------+
| Codex teammate adapter (Python process)                    |
|                                                           |
|   +--------------------------+   +--------------------+   |
|   | Adapter control logic    |-->| cs50victor library |   |
|   | - control loop           |   | (imported;          |   |
|   | - inbox poller           |   |  patches in fork)   |   |
|   | - task claimer           |   |                     |   |
|   | - Codex CLI invoker      |   | - file I/O          |   |
|   +--------------------------+   | - locking           |   |
|            |                     | - schema            |   |
|            | subprocess          +--------------------+   |
|            v                              |                |
|   +--------------------------+             |                |
|   | OpenAI Codex CLI         |             | reads/writes   |
|   | (codex exec --json       |             v                |
|   |  --output-schema ...)    |   ~/.claude/teams/{team}/*  |
|   +--------------------------+   ~/.claude/tasks/{team}/*  |
|                                                           |
+-----------------------------------------------------------+
                                          ^
                                          | same files
                                          |
                          +---------------+------------------+
                          | Claude Code harness (separate    |
                          | process; writes inbox files,     |
                          | reads task updates, etc.)         |
                          +----------------------------------+
```

**Hosting model (important clarification):**
- The adapter is a **single Python process**. cs50victor is imported as a Python library; there is no MCP subprocess, no stdio transport, no IPC between the adapter and cs50victor. They share memory and run as one.
- **The Claude Code harness is a separate process.** It interacts with the adapter only through the filesystem — writing messages to the adapter's inbox file, reading task state, etc. M0 empirically confirmed the harness only cares about file contents, not authoring process.
- The adapter uses cs50victor purely as a **protocol I/O library**, not as a teammate spawner. Registration (adding the adapter's entry to `config.json`) is performed by the adapter directly — see §4.2.
- cs50victor upstream is used as a dependency. Where it diverges from the live Claude Code contract (inbox filename format, owner-as-name, additional optional fields), a narrow fork patches it with clearly-scoped commits; upstream PRs are desirable but not required for M1–M3.

### 4.2 Adapter responsibilities

The adapter is a long-running Python process. On startup it:

1. Reads `CLAUDE_AGENT_ID` and `CLAUDE_TEAM_NAME` from env (or CLI args).
2. Imports cs50victor as a library (no subprocess, no stdio).
3. Self-registers by appending its entry to the team config file (direct config write, per M0-validated approach).
4. Enters its main control loop.

The control loop (pseudocode):

```python
while not shutdown_approved:
    for m in cs50victor.read_inbox(team=team_name, name=self_name):
        match m.get("type"):
            case "shutdown_request":
                cs50victor.send_message(
                    team=team_name,
                    to=lead,
                    body={"type": "shutdown_response",
                          "request_id": m["request_id"],
                          "approve": True},
                )
                shutdown_approved = True
            case "plan_approval_request":
                # per §4.5: never stub. Default policy is planModeRequired=false so this
                # branch is typically unreachable. On opt-in, handle_plan_approval invokes
                # Codex once with --output-schema <plan-schema.json> and sends a real plan.
                handle_plan_approval(m)
            case _:
                enqueue(m)

    claimable = find_unblocked_unowned_task()
    if claimable:
        cs50victor.task_update(task_id=claimable.id, owner=self_name, status="in_progress")
        result = run_codex_on_task(claimable)  # subprocess.run(["codex", "exec", "--json", ...])
        cs50victor.task_update(task_id=claimable.id, status="completed")
        cs50victor.send_message(to=lead, body=completion_summary(result))
    elif all_work_done():
        cs50victor.send_message(to=lead, body={"type": "idle_notification"})
        wait_for_inbox_activity()
```

### 4.3 Codex CLI invocation

Codex exposes multiple integration surfaces; **for M1–M3 we use `codex exec` with structured flags**, and document the Codex App Server as a future upgrade path.

**Primary invocation (M1–M3): `codex exec` with structured output**

- **`codex exec --json <prompt>`** — emits JSONL events: `thread.started`, `turn.completed`, `item.*`, `error`. The adapter parses events to drive `activeForm` updates on the in-progress task (e.g., "generating file X", "running tests") via `task_update`.
- **`codex exec --json --output-schema <schema.json> <prompt>`** — constrains the final response to a schema. We use this for task-completion messages: the schema matches the `task_complete` template in §4.4 (fields: `files_changed`, `summary`, `codex_exit_code`, etc.), so the adapter extracts them deterministically with zero parsing logic.
- **`codex exec resume <session_id> <prompt>`** — continues an existing Codex session. Not needed in M1 (each task is a fresh `exec`), but reserved for multi-turn workflows (e.g., clarifying-question roundtrips with teammates).

**Upgrade path: Codex App Server**

- JSON-RPC 2.0 server built into Codex (stdio default; WebSocket experimental) exposing ~60+ methods across threads, turns, items, streamed agent events, approvals, filesystem, commands, and MCP server management.
- Bidirectional and streamable — the right surface if the adapter ever needs to interject mid-task (e.g., when a teammate sends a clarifying question while Codex is running).
- **Deferred to post-M3.** The `codex exec --json --output-schema` pair is enough for the validation scenarios in task #5; App Server adds significant surface area we don't need for the first end-to-end proof.

**Principles regardless of surface:**

- Pin a specific Codex CLI version in the adapter's README; feature-test at startup.
- Pass the task description as the prompt; stream progress via JSONL events where available; capture final diff/result from the schema-constrained output.
- Working directory: whatever the team's lead passes in team config, or the adapter's CWD.
- Deterministic invocation: no Claude-side temperature choices, no mid-task re-prompting. Codex decides when it's done.

### 4.4 Message content policy

Committed policy (reviewer asked for an explicit choice):

- **All protocol messages** (`shutdown_response`, `idle_notification`, etc.) are JSON objects with fixed schemas. Zero prose.
- **Task-completion and status messages to teammates** use a single structured template with one optional free-text field:

  ```json
  {
    "kind": "task_complete",
    "task_id": "4",
    "files_changed": ["src/foo.ts"],
    "summary": "Implemented X by doing Y",
    "codex_exit_code": 0
  }
  ```

  `summary` is a one-paragraph natural-language description authored *by Codex* as part of its task output — the adapter extracts it from Codex's result, it does not author prose itself. If the recipient is Claude-based, its LLM reads the `summary` field naturally; if the recipient is another non-LLM teammate, the structured fields are sufficient.

- **No free-form prose is authored by the adapter.** Every outbound byte is either JSON protocol, a structured template, or a field populated by Codex output.

### 4.5 Plan mode policy

Reviewer pushed back twice on the spec's "stub plan" fallback, and correctly. A canned plan to a Claude lead either gets rubber-stamped (defeating plan mode) or rejected indefinitely (blocking the teammate). **The canned stub is dropped entirely.** Committed policy:

- **Default: Codex teammates self-register with `planModeRequired: false`.** Plan mode is opt-in, not default, for Codex teammates. The adapter writes `planModeRequired: false` into its own config entry at registration (§4.2).
- **Opt-in path (user sets `planModeRequired: true` on the Codex teammate):** when the adapter receives `plan_approval_request` flow trigger (or when its task lifecycle reaches the plan-approval stage), it invokes Codex **once** with a plan-generation prompt constrained by `--output-schema <plan-schema.json>`. The resulting structured plan is sent via `send_message` to the lead. This is real Codex reasoning — no Claude LLM in the loop, no canned text. The lead gets an actionable plan to review.
- **Never send a generic stub.** "Execute assigned task via Codex CLI" or equivalent is forbidden. It's the worst of both worlds.
- **Rationale:** plan mode is a Claude-convention that asks a teammate's LLM to draft an approvable plan. Codex can produce one; a deterministic stub cannot. If we opt out of plan mode (default), the lead can still review Codex's work after completion via the `summary` field in §4.4.
- **Task #5 (validation) must exercise the `planModeRequired: true` path explicitly.** Otherwise §9's success criterion declares victory by avoiding the test that matters most.

---

## 5. Per-Protocol-Message Failure Modes

(Addressing reviewer's requirement: named failure mode for every protocol message type.)

| Message type | Adapter produces | Failure mode | Adapter's response |
|---|---|---|---|
| `shutdown_request` (inbound) | — | N/A | Reply with `shutdown_response {approve: true}` unless mid-task; in that case reply `{approve: false, feedback: "in-flight task #N"}` |
| `shutdown_response` (outbound) | JSON | Lead ignores / network failure | Retry once, then assume approved and exit cleanly |
| `plan_approval_request` (outbound, opt-in path only) | JSON with plan generated by Codex via `codex exec --output-schema <plan-schema.json>` | Codex fails to produce a schema-conformant plan | Retry once with a tighter prompt; on second failure, update task to `blocked` with `activeForm: "plan generation failed"` and message lead. **Never send a generic stub.** Under default policy (`planModeRequired: false`) this row is unreachable. |
| `plan_approval_response` (inbound) | — | Malformed or missing `approve` field | Log, treat as rejected, send a follow-up request |
| `idle_notification` (outbound) | JSON | Lost in transport | Re-send on next poll cycle |
| `task_assignment` (inbound) | — | Task ID not in task list | Log warning, drop message, do not claim |
| `send_message` (outbound, prose) | Structured JSON template | Recipient expects prose | Include a `summary` field with one-line NL description as fallback |
| Codex invocation failure | — | Codex exits non-zero, hangs, or emits malformed output | Update task to `in_progress` with `activeForm: "retrying"`, retry once, then mark task blocked and message lead |
| cs50victor library exception | — | Uncaught in adapter loop | Catch at loop boundary; log; if persistent for same op, write `status: blocked` task note and exit cleanly |
| Config corruption | — | `~/.claude/teams/{team}/config.json` invalid JSON | Fail closed — do not write; log; exit |

---

## 6. Scoping Answers for Implementer

Addressing the five questions from implementer's orientation:

1. **Language/runtime:** Python (pivoted from TS/Node in v6 after cs50victor was confirmed Python-only; user approved). Use `cs50victor` as a library import (plus a narrow fork for patches). Use `subprocess` or `asyncio.subprocess` for Codex invocation. Standard library + minimal dependencies.
2. **Installation target:** Pip-installable Python package with a console-script entry point (e.g., `claude-anyteam --team <team-name>`). The adapter is started **independently by the user**, not by the Claude Code harness. On startup the adapter (a) imports cs50victor — no subprocess, no MCP stdio — and (b) **self-registers** by appending its own member entry to `~/.claude/teams/{team}/config.json` with `planModeRequired: false`. From that point on, it uses cs50victor's library functions directly for all inbox/task/message I/O. M0 (§7) **already validated** empirically that the harness tolerates this self-registration.
3. **Codex CLI contract:** pin OpenAI Codex CLI (whatever version is current at implementation time — document in README). Use headless mode with JSON output where available; fall back to text parsing. All invocations happen in a subprocess, not via the Codex MCP server (keep one abstraction layer at a time).
4. **"First-class" bar:** adapter has its own control loop (per spec §4.7). It **originates** task claims, task updates, idle notifications, and optional mid-task status messages. It **responds** to shutdown and plan-approval requests. Full peer, not reactive-only.
5. **Plan approvals / shutdown semantics:**
   - **Shutdown:** deterministic — approve unless a task is mid-flight; in that case reject with `{approve: false, feedback: "in-flight task #N"}`.
   - **Plan approvals:** per §4.5 — default `planModeRequired: false` (skip). If opt-in, invoke Codex once with `--output-schema <plan-schema.json>` and send the structured plan. No canned stubs. Ever.

---

## 7. Implementation Plan

In order, owned by `implementer`:

1. ~~**Self-registration spike (BLOCKING).**~~ **Done — PASSED.** M0 validated empirically: the harness delivers messages to a self-registered peer and preserves the config addition. Test-peer fixture remains under `spike-m0/` as a live artifact.
2. **Bootstrap.** Python project skeleton (pyproject.toml, pinned Python version). Add cs50victor as a dependency.
3. **Fork cs50victor narrowly.** Verify every tool/function against `protocol-spec.md` v1.1 and the live disk on this machine. **Close known gaps:** cs50victor does not cover idle_notification emission, plan_approval_request/response as structured types, or hook integration (TeammateIdle / TaskCreated / TaskCompleted). Patch these. Verify schema: inbox filenames are `{name}.json` (not `{agentId}.json`), `owner` field is `name` (not agentId). cs50victor may have these wrong; live disk is ground truth.
4. **Adapter control loop.** Implement the Python pseudocode in §4.2. Stub out Codex invocation with `echo` initially so the protocol layer can be tested in isolation.
5. **Codex invocation layer.** Integrate `codex exec --json --output-schema <schema.json>` as the primary surface for M1–M3 (matches §4.3). `codex exec --json` without output-schema is the fallback if schema constraints cause issues. Plain `codex exec` (stdout parsing) is the last resort. Codex App Server is the post-M3 upgrade path; do not integrate in M1–M3.
6. **End-to-end smoke.** Solo team: one Claude lead + one Codex adapter. Assign a small task. Verify claim → execute → complete → idle → shutdown.
7. **Hand off to reviewer** for task #5 (live validation) and task #6 (final conformance).

### Milestones visible to the team

- **M0: self-registration spike.** ✅ Passed.
- **M1: protocol layer works (Codex stubbed)** — tested against the live `claude-anyteam` team we are already running. Exercises the cs50victor fork with correct inbox/task schemas.
- **M2: Codex invocation works end-to-end.**
- **M3: shutdown, `idle_notification`, plan-mode-skipped behaviors all clean.**

---

## 8. Known Risks and Mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| **Harness does not tolerate self-registered peers** — the central assumption of Option D. If the harness overwrites config mutations or refuses to route to a member it didn't spawn, the whole approach fails. | **Critical** | M0 spike (§7) validates this before any other work. If fails, pivot to Option E (hybrid) or Option F (direct file-based) — but both hinge on the same assumption, so the pivot is less about architecture and more about accepting that a Codex teammate requires the harness to gain `--teammate-mode`-equivalent support for external binaries. That becomes a request upstream, not a project we can finish ourselves. |
| **Option D collapses to Option B on failure** — if cs50victor turns out to be unusable (schema drift we can't patch, unrecoverable gaps), the adapter still needs to read and write the same `~/.claude/teams/{team}/*` files. We'd be doing Option B's work with a different abstraction. | High | Honest acknowledgment: Option D's real win over Option B is code reuse for protocol I/O, not a different protocol surface. If cs50victor is abandoned mid-implementation, pivot to Option F (file-based self-registered peer) — the adapter's control loop and Codex invocation code remain unchanged; only the protocol I/O layer is replaced. |
| `cs50victor/claude-code-teams-mcp` is partially stale — confirmed by reviewer's binary inspection. The repo's schema has drifted from current Claude Code (e.g., it may use agentId where the live format uses name). It also does not cover idle_notification, plan_approval_request/response as structured types, or hook integration. | High | In M1, validate every MCP tool against live observation on this machine. Patch the fork to cover the missing protocol features before using it. Treat cs50victor as a starting scaffold, not a finished product. |
| Plan-mode escape hatch required | Medium | Primary policy (§4.5) is `planModeRequired: false`. If a user needs a Codex teammate in plan mode, document how to route plan generation through Codex with `--output-schema` and revisit. |
| Idle detection semantics are opaque (spec §6.1 #2) | Medium | Start with explicit `idle_notification` messages; monitor whether the harness accepts them; instrument to observe what the harness expects. M3 milestone covers this. |
| Codex CLI version churn, App Server stability | Medium | Pin Codex CLI version in README; feature-test App Server availability at adapter startup; fall back to `codex exec --json` if the App Server interface changes. |
| Tool-call permissions for non-LLM teammates (spec §6.1 #6) | Medium | Start with minimum-privilege tool set (SendMessage, TaskUpdate, TaskList, TaskCreate); discover restrictions empirically. |
| Adapter crash leaves orphaned task in `in_progress` | Low | Heartbeat mechanism: write `updatedAt` on each loop iteration; lead can reclaim stale tasks. |
| **Harness caches member metadata at team-create time** — observed empirically in M0: the `targetColor` field on `SendMessage` routing results was absent when sending to a self-registered member, even though the config entry had `color: "cyan"`. This suggests the harness does not re-read `config.json` on each send — it uses an in-memory member table populated at team init. Practical implication: Codex teammates that hot-join a running team may be missing UI affordances (color chrome, possibly subscription-based notifications) until the harness restarts or discovers a trigger that forces a config reload. | Medium | Document as a known limitation for hot-join. For the validation test (task #5), spawn the Codex teammate before starting the Claude lead's session so it's present at team init. Investigate during M1 whether any harness event (TeammateIdle hook? inbox write?) triggers a member-table refresh. If not, document the restart-required workflow. |
| Inbox filename / owner field drift — architecture doc must not hardcode `{agentId}.json` paths; live format uses `{name}.json` | Low (caught) | §4.2 pseudocode is tool-mediated (`mcp.readInbox()`); no hardcoded paths. MCP server fork enforces the correct format. |

---

## 9. What Success Looks Like

**Primary scenario (cold-join, happy path).** The Codex teammate passes the validation test in task #5 when **all of the following hold**:

- **Registration timing: the Codex teammate is registered before the Claude lead's session starts** (per §8 mitigation for harness member-caching). This is the happy-path scope.
- The adapter self-registers in a Claude Code team and the harness delivers messages to its inbox (M0 validated).
- It receives a task assignment from the Claude lead and claims it via `task_update`.
- It invokes Codex CLI (via `codex exec --json --output-schema`), which produces a working code artifact.
- It reports completion via `task_update` and a structured `task_complete` summary message (template in §4.4).
- It handles `shutdown_request` cleanly, including the reject-while-mid-task scenario.
- It sends `idle_notification` between tasks.
- **`planModeRequired: true` scenario explicitly tested:** a Codex teammate is registered with plan mode on, receives a plan-approval flow, invokes Codex once with `--output-schema <plan-schema.json>`, and sends a structured plan the lead can approve. This scenario exercises the one protocol capability most prone to requiring LLM reasoning, so its success is load-bearing for the "no Claude wrapper needed" verdict.
- Throughout, **no Claude LLM runs on behalf of the Codex teammate** — only the lead and other Claude teammates use Claude.

**Secondary scenario (hot-join).** A secondary validation scenario tests hot-join registration — the Codex adapter registers while the Claude lead is already running. Expected outcome is **EITHER** working hot-join **OR** an empirically-documented limitation on which protocol behaviors degrade for hot-joined members. Failure of hot-join does not invalidate the primary scenario's success; it becomes a known limitation captured in `validation-report.md` and the adapter's README.

The final conformance review (task #6) determines whether the prior "only LLM wrapper works" conclusion is refuted (yes, if the primary scenario passes and all protocol operations are mechanical) or merely deferred (if one or more capabilities ended up needing Claude-LLM help after all).

---

## 10. What Reviewer Is Expected to Sign Off On

1. The rejection of Options A, B, C, F is adequately justified.
2. The chosen architecture (Option D) has realistic success criteria.
3. Every protocol-message type has a named failure mode (§5).
4. No capability listed in §2's table has been mislabeled as "LLM not needed" when it actually is.
5. The implementation plan (§7) is executable without additional research, with M0 as the blocking empirical gate.
6. Risks (§8) are not understating any known blocker from the spec.
7. Every "No" or "Optional" row in §2's table is either validated by §5's failure-mode entry OR exercised by a task #5 validation scenario.
8. The decision doc references the corrected `protocol-spec.md` (v1.1), or inlines the corrected schema so implementer can proceed without waiting.

If reviewer rejects, revisions happen here, not downstream.
