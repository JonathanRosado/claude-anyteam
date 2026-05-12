# RFC: visibility-driven stall handling (replace soft timers with lead-actionable events)

**Status:** draft (task #6) — primary author `opus-architect`, adversarial review `opus-reviewer`
**Date:** 2026-05-12
**Tracks:** north star §2 (visibility parity), §3 (peer efficiency)
**Related issues:** #40 (concurrent initialize hang), #43 (sqlite WAL bloat), #49 (mid-turn stall after wrapper_tool failure)
**Related task:** #5 (bump `turn_timeout_s` default)
**Related PR:** #42 (Phase 1 — typed initialize-timeout events)

## TL;DR

The user has been bitten by wall-clock timers (the 900s `turn_timeout_s`, the 300s `non_progress_warn_s`) more than they have been helped by them. Their meta-ask is sharp: *"if enough visibility is built into the protocol, timers may not be needed."*

The answer is mostly **yes, with caveats**:

- **Stall-detection timers** (`turn_timeout_s`, `non_progress_warn_s`, `non_progress_interrupt_s`) are coping mechanisms for missing visibility. They predate the typed-event work landed in PR #27 (v0.8.0 protocol revision) and PR #42 (Phase 1 initialize events). With four new typed envelopes (`wrapper_tool_failure_unrecovered`, `app_server_idle_quiet`, `subprocess_pressure`, `transport_alive`) and a mandatory model-emitted `working_on` claim, all three can be demoted to *opt-in backstops for the lead-offline case*.
- **Bounded-I/O timers** (`DEFAULT_MANIFEST_READ_TIMEOUT_S`, `DEFAULT_FILE_LOCK_TIMEOUT_S`, JSON-RPC request ceilings, subprocess version probes) are not coping mechanisms — they cap real transport latency, and they stay.
- **Teardown timers** (`JsonRpcStdioClient.close(timeout=5.0)`, process-group SIGTERM grace) are bounded budgets, not stall-detection. They stay, with documented per-call budgets and a separate RFC follow-up under task #7 for cumulative team-teardown.

The architectural distinction is **"timer over IPC you control"** vs **"timer over modeling time you do not."** The first is fine. The second has been the source of every false-positive in our observed corpus.

This RFC proposes the new event taxonomy, the migration plan, and the lead-offline carve-out where timers still earn their keep.

---

## 1 — Inventory of current timers

Each entry: knob, default, range, code site, what failure it claims to catch, what false positives it produces, candidate visibility replacement.

### 1.1 Stall-detection timers (the suspect class)

#### `turn_timeout_s`
- **Default 900s; range [60, 3600].** `src/claude_anyteam/config.py:79`, surfaced via `--turn-timeout-s` and `CLAUDE_ANYTEAM_TURN_TIMEOUT_S`.
- **What it catches:** wall-clock cap on a single Codex App Server turn (`codex.py:app_server_invoke` polling loop, hits `exit_code=124` at `codex.py:2211`).
- **False positives:** any genuinely long turn — large test suite, multi-file refactor, large model on `xhigh` effort. The user's standing complaint ("the 900s for codex is really fucking us over") is the lived experience of this. PR-#42-style typed progress events make it possible to *see* the turn is alive, but the 900s cap still fires regardless and discards in-flight work.
- **Visibility replacement:** `app_server_idle_quiet` event (proposed below) emitted when observable progress is genuinely absent, NOT just when wall-clock has elapsed. Combined with `transport_alive`, the lead can distinguish "model is thinking hard" from "JSON-RPC fd is wedged."

#### `non_progress_warn_s`
- **Default 300s; range [60, 900].** `config.py:84`, surfaced via `--non-progress-warn-s`.
- **What it catches:** Codex App Server-only soft watchdog. After 300s with no agentMessage byte delta and no tool_call_event count delta, the adapter emits a `turn_progress` warn envelope and sends a `turn/steer` nudge (`codex.py:2089-2145`).
- **False positives:** any long-running Codex `commandExecution` (e.g., `pytest -x` on a large suite). The watchdog has no idea that a 7-minute shell exec is making progress because it tracks notification deltas, not subprocess wall-clock state. The steer interrupts the model's working memory.
- **Visibility replacement:** `app_server_idle_quiet` ties the silence to specific event-stream gaps; `tool_event` deltas already flow through the protocol but the watchdog ignores them. Once the watchdog is event-driven, the "I'm running a long test" case becomes self-disambiguating (the tool_event for the `Bash` invocation is still in flight).

#### `non_progress_interrupt_s`
- **Default None (opt-in); range [60, 3600] when set.** `config.py:89`, surfaced via `--non-progress-interrupt-s`.
- **What it catches:** hard early interrupt, only fires after the soft watchdog has warned and no later checkpoint was observed (`codex.py:2146-2180`).
- **False positives:** same as `non_progress_warn_s`; an opt-in escalation of the same primitive.
- **Visibility replacement:** keep as opt-in for **lead-offline / overnight** runs only — this is the one place wall-clock interrupt earns its keep. Document the carve-out (§7).

#### `app_server_initialize_timeout`
- **Default 90s; env-override `CLAUDE_ANYTEAM_APP_SERVER_INITIALIZE_TIMEOUT_S`.** `codex.py:761`, `env.py:55`.
- **What it catches:** JSON-RPC `initialize` handshake budget. Down from 600s pre-PR #42.
- **False positives:** *rare now*. PR #42 made this paired with `app_server_initialize_progress` cadence events so the lead can see the handshake is alive. This is the **right model**: a bounded I/O timer with a visibility stream alongside it. We keep it.
- **Visibility replacement:** none needed — it's already paired with typed events. This is the template for how the others should look.

### 1.2 Bounded-I/O timers (the keep-as-is class)

These cap real transport latency. They are not coping for missing visibility — without them, a wedged filesystem or stuck subprocess hangs the adapter forever.

| Knob | Value | Site | Stays because |
| --- | --- | --- | --- |
| `DEFAULT_MANIFEST_READ_TIMEOUT_S` | 2.0s | `capability_manifest.py:31`, `spawner.py:19` | Per-peer manifest read; a stuck inotify or NFS read shouldn't block roster discovery. |
| `DEFAULT_PREWARM_TIMEOUT_S` | 2.0s | `capability_manifest.py` | Same primitive at prewarm time. |
| `DEFAULT_FILE_LOCK_TIMEOUT_S` | 30.0s | `_filelock.py:11` | Mailbox / task-claim lock acquisition; a crashed peer that never released its lock must not block the whole team. |
| `JsonRpcStdioClient.send_request(timeout=600)` | 600s | `jsonrpc_stdio.py:194` | Wall-clock ceiling on a single JSON-RPC method. A truly wedged App Server gets surfaced as a typed transport error, not as silent hang. |
| `wait_for_notification(timeout=600)` | 600s | `jsonrpc_stdio.py:252` | Same idea for notification reads. |
| Subprocess version probes | 5–10s | `cli.py:128/430/449/564`, `installer.py:1282-1294`, `team_cli.py:731` | `codex --version` etc. — fast operations; if they take 10s something is wrong. |
| `urllib.request.urlopen(timeout=60)` | 60s | `wrapper_server.py:2099` | HTTP fetches; bounded retries handled at call site. |
| `kimi invoke(timeout_s=600)` | 600s | `backends/kimi/invoke.py:685` | Kimi subprocess wall-clock cap; raises a typed `turn_timeout` error class (`backends/kimi/invoke.py:623`). The Kimi backend has no `turn/steer` equivalent so a hard cap is the only escape. |
| `WATCH_RUST_TIMEOUT_MS` | (Rust default) | `watch_inbox.py:178` | inotify polling primitive — internal to the watcher. |

The `JsonRpcStdioClient` 600s ceilings are arguably long enough that they merge with the stall-detection class, but in practice no method call ever approaches them; they exist as catch-alls for a wedged fd. The 600s is fine; the failure shape it produces (a typed transport error, not silent hang) is the part that matters.

### 1.3 Teardown / shutdown timers

| Knob | Value | Site | Disposition |
| --- | --- | --- | --- |
| `JsonRpcStdioClient.close(timeout=5.0)` | 5.0s | `jsonrpc_stdio.py:89-105` | Graceful SIGTERM grace before SIGKILL on the App Server. Stays. Documented separately in task #7. |
| `terminate_process_group(timeout=5.0)` | 5.0s | `jsonrpc_stdio.py:134-180` | Process-group teardown (landed in PR #42). Stays. |
| `app_server_shutdown_timeout` event class | — | `messages.py:286` | Already a typed error class — when shutdown burns the initialize budget, it gets its own envelope distinct from work-turn timeout. Keep. |

Task #7 ("speed up team teardown") owns the per-stage budget discussion; this RFC just notes the knobs exist and don't belong to the stall-detection conversation.

---

## 2 — Failure modes timers actually catch

What does the stall-detection class do that visibility events would not?

1. **Wedged JSON-RPC fd** — App Server process is alive but its stdout pipe has gone quiet (panic, blocked on a stuck syscall, hit a runtime bug). Visibility can catch this if we add a `transport_alive` heartbeat: a cheap "is the fd readable?" probe every N seconds whose absence is itself an event. The current `_app_server_transport_alive` probe (referenced at `codex.py:2081`) is a private one-shot; promoting it to a typed periodic event makes it lead-observable.
2. **Lead-offline overnight runs** — no human is watching the event stream. Events without an observer don't act. This is the **one case where wall-clock interrupt earns its keep** — see §7.
3. **Routed-CLI that ignores the `working_on` prompt** — a backend whose model doesn't cooperate with the visibility contract reverts to "silent thinking is indistinguishable from stall." Mitigation: typed progress events that flow regardless of model compliance (notification cadence, tool_event, artifact_event), plus a per-backend capability declaration of whether `working_on` is reliable.

Everything else the timers nominally catch — "model is taking too long," "wrapper_tool failed and the model is sulking," "JSON-RPC notif silent" — is more sharply caught by an event the lead can read.

---

## 3 — False positives observed

### #40 — concurrent codex-* spawn, initialize hangs

The original symptom: spawning two `codex-*` teammates back-to-back, the first one's App Server `initialize` blocks for ~600s. Pre-PR #42, the lead saw no progress at all during that window; the wall-clock cap fired at 600s with a generic prose error.

What was wrong wasn't the timer — it was the *invisibility*. PR #42 added `app_server_initialize_progress` events at 30s cadence and a typed `app_server_initialize_timeout` envelope (`emit_initialize_timeout_visibility_degraded` at `protocol_io.py:894`). The timer also dropped to 90s on the rationale that the one successful empirical sample was ~17s. The combination shipped: timer + visibility, with the timer demoted to a backstop and the visibility stream doing the explanatory work.

**Lesson:** the timer was load-bearing only because visibility was missing. With visibility, the timer became a backstop. Phase 2 (#40 task #4) will determine whether the timer is still needed at all once we add `transport_alive`.

### #43 — codex sqlite WAL bloat

Symptom: when codex's sqlite WAL grows large (multi-week heavy usage), startup becomes slow. The adapter's wall-clock timers fire; the lead concludes "stuck" when the cause is local-disk pressure (WAL replay, vacuum, fsync stalls).

We have no typed event for "the underlying subprocess is alive but slow because of a local resource problem." The timer can't tell the difference. A `subprocess_pressure` event (§5.4) — emitted when `/proc/<pid>/io`, `/proc/loadavg`, or backend-specific signals indicate non-CPU stall — would let the lead distinguish "WAL is replaying" from "the model is stuck." Wrapper-side WAL truncation (task #3) is the cure; a typed event is the diagnosis.

### #49 — mid-turn stall after wrapper_tool failures

Symptom: codex-* teammates emit a few `turn_progress` events, hit a `wrapper_tool` failure (Errno 2 on `shadow_tool` path, bad task ID on `task_update`), then go silent. `non_progress_warn_s` fires at 300s and sends a steer; the steer often doesn't reach the stuck teammate. `turn_timeout_s` finally kills it at 900s. The lead sees: a few progress events, then 15 minutes of dead air, then exit code 124.

This is the **hard case for "timers are sufficient."** The 300s warn + 900s kill catch the failure eventually, but:
- The lead can't tell at second 60 whether to wait or to intervene.
- The 300s steer carries no recovery_hint — it's a generic nudge.
- The 900s kill discards any partial work and produces a generic prose error, not a typed reason the lead can grep.

A `wrapper_tool_failure_unrecovered` event (§5.1) — emitted ~5s after a wrapper_tool error if no follow-up `turn_progress` arrives — gives the lead a sharp signal in seconds, with the failing tool name and error class in the payload. The lead's `Agent` skill can then issue `task_reassign` or send a recovery_hint *before* 5 minutes of dead air.

**This is the case the user is really complaining about.** It is the strongest argument for the RFC.

---

## 4 — Visibility primitives we already have

Inventory of typed envelopes shipping today (`protocol_io.py`, `codex.py`, `messages.py`):

- **Event-stream kinds:** `turn_started`, `turn_progress`, `turn_completed`, `turn_failed`, `turn_warning`, `tool_event`, `artifact_event`, `visibility_degraded`, `app_server_initialize_progress`, `app_server_initialize_completed`.
- **Mailbox message kinds:** `idle_notification`, `shutdown_approved`, `shutdown_rejected`, `task_blocked`, `plan_blocked`, `task_complete`, `plan_approval_request`, `permission_request`.
- **Diagnostic error classes** (`diagnostics.py:119-148`): `turn_timeout`, `app_server_initialize_timeout`, `mcp_send_message_unavailable`, etc.

What's missing for the stall conversation:

1. No event for "wrapper_tool failed AND no follow-up turn_progress." (Today: `tool_event` for the failure, then silence.)
2. No event for "observable progress stream went quiet" distinct from "agent finished." (Today: silence is the signal; wall-clock distinguishes.)
3. No event for "local subprocess is alive but slow." (Today: indistinguishable from a stall.)
4. No event for "JSON-RPC fd is still readable but quiet." (Today: `_app_server_transport_alive` is private.)
5. No model-emitted `working_on` claim — agents can go silent in the middle of long thinking with no contract requiring a periodic status token.

§5 proposes one event per gap.

---

## 5 — Proposed visibility-driven primitives

All four envelopes follow the existing `VisibilityEvent` shape used by `emit_initialize_timeout_visibility_degraded` (`protocol_io.py:936-957`). The mailbox + event_log fan-out pattern stays.

### 5.1 `wrapper_tool_failure_unrecovered`

**Emitted when:** the wrapper MCP returns an error to the routed CLI (Errno 2 on file ops, schema-validation fail on task_update, etc.) AND no `turn_progress` envelope arrives within a short bounded window (proposed: 5–10s).

**Payload shape:**
```json
{
  "surface": "wrapper_tool_failure_unrecovered",
  "tool_name": "mcp_anyteam_read_file",
  "error_class": "enoent",
  "error_detail": "/path/that/does/not/exist: Errno 2",
  "turn_id": "...",
  "last_progress_at_ms": 12345,
  "silence_window_ms": 8000,
  "recovery_hint_dispatched": false
}
```

**Lead's right action:** issue `task_reassign` or send a recovery_hint via `task_update`. The recovery_hint is the same payload that task #1 (#49) is already building — this event just makes it kickable from outside the wrapper.

**Replaces:** the 300s `non_progress_warn_s` for the most common stall case.

### 5.2 `app_server_idle_quiet`

**Emitted when:** for a configurable window (default 60s, *not* 300s), the App Server has produced no notification AND no `tool_event` delta AND no `artifact_event` delta AND the transport is alive.

**Payload shape:**
```json
{
  "surface": "app_server_idle_quiet",
  "turn_id": "...",
  "elapsed_s": 90,
  "since_last_progress_s": 75,
  "transport_alive": true,
  "tool_calls_in_flight": ["Bash:pytest"],
  "last_working_on": "running test suite"
}
```

**Lead's right action:** *usually nothing* — the in-flight tool tells them why the model is quiet. Action only escalates when `tool_calls_in_flight` is empty AND `last_working_on` is missing or stale. Crucially this event is a **signal**, not an **interrupt**: emission ≠ kill.

**Replaces:** the 900s `turn_timeout_s` cap as the *signal* (the cap remains as a lead-offline backstop, §7).

### 5.3 `subprocess_pressure`

**Emitted when:** OS-level hints indicate the routed CLI's subprocess is alive but I/O- or disk-bound. Detection heuristics (per-backend, declared in capability manifest):
- Codex: sqlite WAL size over threshold (#43 follow-up); `/proc/<pid>/io` write_bytes growing without `tool_event` activity.
- Gemini: ACP transport responding to ping but throughput collapsed.
- Kimi: subprocess CPU≈0 but stat shows recent `mtime`.

**Payload shape:**
```json
{
  "surface": "subprocess_pressure",
  "kind": "sqlite_wal_replay",
  "hint": "codex sqlite WAL is 480MB; startup may be slow",
  "remediation": "claude-anyteam diagnose --codex-wal-truncate"
}
```

**Lead's right action:** distinguish #43-class slowness from stalls; do not kill the teammate. The skill `claude-anyteam:diagnose` can read these events to surface remediation.

### 5.4 `transport_alive` (heartbeat envelope)

**Emitted when:** every N seconds (default 30s, aligned with existing `APP_SERVER_INITIALIZE_PROGRESS_INTERVAL_S`), the wrapper checks the routed CLI's transport is still responsive. Emission shape mirrors `app_server_initialize_progress` (`codex.py:928`).

**Payload shape:**
```json
{
  "surface": "transport_alive",
  "transport": "jsonrpc_stdio",
  "rtt_ms": 4,
  "last_event_at_ms": 12345
}
```

**Mailbox treatment:** routes through `idle_notification`-class delivery so it does not crowd out substantive content (north star §3). Lead clients can filter cheaply by `message_kind`.

**Replaces:** the implicit assumption that "no notification → wedged transport." Now the absence of `transport_alive` is itself the signal.

### 5.5 Model-emitted `working_on` claim (prompt contract)

**Required:** every backend prompt template (`prompts.py`, `backends/kimi/prompts.py`, gemini equivalents) gains a one-line contract: *"emit a `working_on` claim every ~30s of work or after every tool_call, whichever is sooner. Format: a 1-line description of current activity."*

**Surface:** flows as a `turn_progress` envelope with `payload.working_on_claim = "..."`.

**Capability declaration:** each backend declares `working_on_compliance: "strict" | "best_effort" | "absent"` in its capability manifest. `absent` means the stall-detection backstop (§7) cannot be raised against this backend.

This is the **§3 piece**: requiring the *agent* to participate in its own visibility, not making the wrapper guess.

---

## 6 — Proposed reclassification of existing timers

| Today's timer | Disposition under this RFC |
| --- | --- |
| `turn_timeout_s` (900s default) | **Stays as backstop, raised to 1800s default (task #5).** Only relevant when no `app_server_idle_quiet` interpretation is possible (lead offline). Cap remains 3600s. |
| `non_progress_warn_s` (300s default) | **Default flipped to None (off).** Existing users who pinned a value keep working. The signal it provided is dominated by `app_server_idle_quiet` (event-driven, 60s default) + `wrapper_tool_failure_unrecovered` (specific, 5–10s). |
| `non_progress_interrupt_s` (None default) | **Stays opt-in; documented as the overnight-runs knob.** This is the carve-out — see §7. |
| `app_server_initialize_timeout` (90s) | **Stays.** Already paired with typed events per PR #42; template for the others. |
| `JsonRpcStdioClient` 600s ceilings | **Stay.** Transport-level catch-all. |
| `DEFAULT_FILE_LOCK_TIMEOUT_S` (30s) | **Stays.** Bounded I/O. |
| Version-probe timeouts (5–10s) | **Stay.** Bounded I/O. |
| `kimi invoke timeout_s=600` | **Stays** (Kimi has no `turn/steer` so the hard cap is the only escape). Consider raising to 1800s for consistency with the new `turn_timeout_s` default (task #5). |
| Teardown / shutdown 5.0s grace | **Stays; revisited under task #7.** |

---

## 7 — Tradeoffs and carve-outs

### 7.1 Overnight / lead-offline runs

The user has explicitly said "all night is fine" for some long-running tasks. With no human watching the event stream, events without an observer have no effect. Three options:

1. **Keep `turn_timeout_s` and `non_progress_interrupt_s` as opt-in for these scenarios.** This is the RFC's recommendation. The lead's invocation (`/loop`, `schedule`, autonomous mode) is the right place to opt in.
2. **Spawn a Claude lead subagent ("watchdog persona") that consumes events overnight.** Cheap because most idle periods are healthy. This is the §3-aligned solution but requires a new persona; defer to follow-up.
3. **Cron-style heartbeat:** require the team-lead process to emit a periodic `lead_alive` event; if absent for >5min, peers fall back to wall-clock timers. Symmetric to `transport_alive`. Worth prototyping.

Recommendation: (1) for v1, (2) prototyped under a follow-up issue.

### 7.2 Routed-CLI non-compliance with `working_on`

A backend whose model ignores the prompt instruction reverts to "silent thinking is indistinguishable from stall." Mitigations:

- Typed progress events (`turn_progress`, `tool_event`, `artifact_event`) flow regardless of model cooperation.
- Per-backend capability declaration (`working_on_compliance`) tells peers how much to trust the absence of a `working_on` claim.
- For `working_on_compliance: "absent"` backends, the timer backstop (§7.1) is automatic.

### 7.3 Steer-resistant stalls (transport wedged)

A teammate whose JSON-RPC fd is wedged can't emit events AND can't be steered. `transport_alive` catches this; the lead's right action is `task_reassign` + force-kill via teardown. No timer needed — `transport_alive` absence *is* the signal.

### 7.4 Cost of more events

Every new envelope is bytes in the mailbox and rows in the event log. We mitigate:

- All four new envelopes route through `idle_notification`-class delivery semantics when emitted as heartbeats (no UI crowding).
- `app_server_idle_quiet` is rate-limited to one per silence window, not continuous.
- `transport_alive` is the only periodic one (30s); the rest are state-change triggered.

Expected steady-state cost: ~2 envelopes/min/teammate on a healthy team, ~5/min on a degraded one. Mailbox JSON files already handle 10x this; no new pressure.

### 7.5 Backwards compatibility

- Config knobs stay (no removed names) — old `agents/<name>.json` files keep parsing.
- Default values shift (task #5 will land the new numbers); CHANGELOG entry required.
- Wrapper MCP capability manifest gains four new capability strings; old clients that don't know them skip them per the existing degrade-gracefully contract.

---

## 8 — Migration plan

**Phase A — emit the events** (no behavior change).

1. Add `wrapper_tool_failure_unrecovered`, `app_server_idle_quiet`, `subprocess_pressure`, `transport_alive` to `protocol_io.py` as new `VisibilityEvent` emitters mirroring `emit_initialize_timeout_visibility_degraded`.
2. Wire emission in `codex.py` (App Server polling loop), `backends/gemini/loop.py`, `backends/kimi/loop.py` where applicable per per-backend capability.
3. Add `working_on` contract to `prompts.py` and per-backend prompt templates.
4. Declare per-backend `working_on_compliance` and new event capability strings in the capability manifest.
5. Wire `claude-anyteam:diagnose` skill / `visibility_tail` to recognize the four new envelopes.

Ship as a normal feature PR. No defaults change.

**Phase B — flip defaults** (task #5 territory).

1. `turn_timeout_s` default 900 → 1800 (task #5 has this as its primary deliverable).
2. `non_progress_warn_s` default 300 → None (off by default). Keep the knob; opt-in for users who want the soft watchdog steer behavior.
3. CHANGELOG entry; release note documenting the new defaults and the visibility events that replace them.

**Phase C — teach the lead** (skill update).

1. `claude-anyteam:help` skill gains a "stall handling" section: *"when you see `wrapper_tool_failure_unrecovered`, the right move is to send a recovery_hint via task_update or to issue task_reassign. When you see `app_server_idle_quiet` with empty `tool_calls_in_flight`, consider sending a `turn/steer`. When you see `transport_alive` absent for >2 cycles, kill and respawn."*
2. Peers get the same guidance via capability-manifest semantic-guidance fields.

**Phase D — retire the soft watchdog code path** (cleanup).

Once Phase A–C have shipped and the visibility events are observed catching the same cases in production, remove the soft watchdog code path (`codex.py:2084-2180`). Keep `non_progress_interrupt_s` as the opt-in overnight knob, but have it consume `app_server_idle_quiet` events rather than computing its own wall-clock delta.

---

## 9 — Open questions for `team-lead` / `opus-reviewer`

1. **Is the overnight carve-out enough?** Should we also prototype the "watchdog persona" lead subagent (§7.1 option 2) before retiring `non_progress_warn_s`? Argument for: stronger §3 alignment. Argument against: complexity, defer to follow-up.
2. **Should `transport_alive` be a `VisibilityEvent` or a `MailboxMessage`?** It's a heartbeat — feels mailbox-shaped. But it's also lead-only by default, which feels event-shaped. Either works; mailbox is cheaper to filter cheaply per north star §3.
3. **Per-backend `working_on_compliance`** — do we measure this empirically or trust the manifest? Empirical measurement would mean the wrapper tracks `working_on` claim frequency and downgrades the declared compliance if it slips. Probably overkill for v1; declare and trust.
4. **Should `app_server_idle_quiet` ever steer?** Today the soft watchdog auto-steers ("you have produced no externally visible checkpoint for Xs"). The visibility-driven model says: emit the event, let the lead decide. But for autonomous overnight runs the auto-steer is the *only* thing that fires. Recommendation: keep auto-steer opt-in via `non_progress_interrupt_s`-style knob.

---

## 10 — Summary

Stall-detection timers are coping mechanisms for missing visibility. The user has felt this directly: the 900s `turn_timeout_s` is the dominant pain. Four typed events + a prompt-contract `working_on` claim replace the signal the timers provided, more sharply and with better lead-actionability. Wall-clock interrupt earns its keep in exactly one scenario — lead-offline overnight runs — and stays opt-in for that case. Bounded-I/O and teardown timers are a separate category and stay.

The migration is staged so no defaults change until the events are emitting and the lead can see them. After Phase B the user's central complaint ("the 900s for codex is really fucking us over") is resolved: the cap is 1800s, but it almost never fires because `app_server_idle_quiet` + `wrapper_tool_failure_unrecovered` lets the lead intervene at second 10, not second 900.

This RFC is the design layer for tasks #1 (#49 recovery), #4 (#40 Phase 2), #5 (timeout defaults), and is referenced from #7 (teardown speed) where the teardown timer carve-out applies.
