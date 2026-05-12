# RFC: visibility-driven stall handling (replace soft timers with lead-actionable events)

**Status:** draft v2 (task #6) — primary author `opus-architect`, adversarial review `opus-reviewer`
**Date:** 2026-05-12 (v1); 2026-05-12 revised (v2)
**Tracks:** north star §2 (visibility parity), §3 (peer efficiency)
**Related issues:** #40 (concurrent initialize hang), #43 (sqlite WAL bloat), #49 (mid-turn stall after wrapper_tool failure)
**Related task:** #5 (bump `turn_timeout_s` default)
**Related PR:** #42 (Phase 1 — typed initialize-timeout events)

**v2 revision log** (response to opus-reviewer's 2026-05-12 REQUEST CHANGES, items 1, 2, 3, 4 blocking + 5–10 sharpening):

- §5.1 — window widened from 5–10s to **60–90s** (default 90s), anchored to the empirical `codex-implementer-a` recovery trace (item 1). New §5.1.1 adds the Mode A/B discriminator pseudocode (item 5). §5.1 prescriptive "right action" language demoted to "potential lead-side responses (informational; lead decides)" (item 3).
- §0 (new) — explicit engagement with invariant 11: *signal-emitting wall-clock windows are not the same primitive as action-triggering soft timers* (item 3).
- §5 — every new envelope now ships with a four-leg taxonomy integration table covering `VisibilityEventKind` Literal, `parse_protocol_text` dispatch case, `KNOWN_TASK_BLOCKED_REASONS` token, and `ERROR_CLASSES + classify_failure` entry. Each envelope's top-level `kind` value (distinct kind vs `visibility_degraded` with `payload.surface`) is explicit (item 2).
- §7.4 — cardinality argument backed by the per-team aggregate `visibility.jsonl` file (`protocol_io.py:514`). `transport_alive` cadence raised to **default 90s, range [30, 600]**, per-teammate configurable. Combined cardinality with §5.5 `working_on` is bounded to ≤ 4 envelopes/min/teammate at default settings (item 4).
- §1 inventory — adds codex `tools/call` 120s ceiling as a bounded-I/O entry with documented workaround (per team-lead's late note).
- §1.2 — Kimi 600s reclassification commits to "future-phase demotion conditional on `working_on_compliance: best_effort`" (item 6).
- §6 / §7.5 — `non_progress_warn_s` default flip ships with **one release of deprecation envelope warning** before becoming the default (item 9 backwards-compat).
- §7.1 — overnight watchdog persona is **filed as a follow-up task at RFC merge**, not left as an open question (item 7). Open questions §9 trimmed accordingly.
- §8 Phase D — clarified to "unifies the action-triggering wall-clock onto the signal-emitting wall-clock," not "removes" the wall-clock delta (item 8).
- §9 Q2 — answered: `transport_alive` is a `VisibilityEvent`, not a `MailboxMessage` (item 10).

## 0 — Signal-emitting wall-clock windows ≠ action-triggering soft timers

Before the inventory: the architectural distinction this RFC depends on. opus-reviewer's item 3 correctly flagged that §5.1, §5.2, and §5.4 all contain wall-clock windows. That is true. The defensible distinction is **what fires when the clock expires**:

| Today's soft timer | Proposed visibility window |
| --- | --- |
| `non_progress_warn_s` fires at 300s → adapter **steers the model and writes to its working memory**. Action lives inside the wrapper. The model has to reconcile the unsolicited steer against its own state. | `app_server_idle_quiet` fires at 60s → emits a typed envelope to the lead. **No model-side action.** The lead reads the envelope and may or may not act. |
| `turn_timeout_s` fires at 900s → adapter **interrupts the turn and discards in-flight work**. Hard kill from the wrapper. | `wrapper_tool_failure_unrecovered` fires at 90s → emits a typed envelope. **No interrupt, no kill.** Optional lead-side action (task_reassign, recovery_hint) is a *separate* invocation, not a side effect of the timer. |
| `non_progress_interrupt_s` opt-in → adapter **issues `turn_interrupt`** if the window passes. | Same opt-in flag becomes a **consumer** of `app_server_idle_quiet` events, not its own wall-clock loop. The interrupt is still wall-clock-triggered (item 8) but the *delta computation* lives in one place. |

The user's meta-ask was *"timers may not be needed if visibility is enough."* That is shorthand for *"don't have the wrapper take destructive action on a wall-clock."* The new envelopes preserve that — they emit signal, they don't act. Reviewers and implementers should read this RFC as **"replace destructive-wall-clock-actions with observable-wall-clock-signals,"** not as *"remove all wall-clock primitives."* The latter is impossible (PR #42's `app_server_initialize_timeout` is wall-clock and we still need it).

This framing is load-bearing for the whole RFC. If a follow-up PR cites this doc to argue "no new wall-clock windows," that PR is misreading §0.

## TL;DR

The user has been bitten by wall-clock timers that **act on the model** (the 900s `turn_timeout_s` interrupts, the 300s `non_progress_warn_s` steers). Their meta-ask is sharp: *"if enough visibility is built into the protocol, timers may not be needed."*

The answer is mostly **yes, with the §0 distinction**:

- **Stall-detection timers** (`turn_timeout_s`, `non_progress_warn_s`, `non_progress_interrupt_s`) are coping mechanisms for missing visibility. They predate the typed-event work landed in PR #27 (v0.8.0 protocol revision) and PR #42 (Phase 1 initialize events). With four new typed envelopes (`wrapper_tool_failure_unrecovered`, `app_server_idle_quiet`, `subprocess_pressure`, `transport_alive`) and a mandatory model-emitted `working_on` claim, all three can be demoted to *opt-in backstops for the lead-offline case*. The new envelopes are still wall-clock-driven (§0); they just emit signal instead of acting on the model.
- **Bounded-I/O timers** (`DEFAULT_MANIFEST_READ_TIMEOUT_S`, `DEFAULT_FILE_LOCK_TIMEOUT_S`, JSON-RPC request ceilings, subprocess version probes, codex `tools/call` 120s ceiling) are not coping mechanisms — they cap real transport latency, and they stay.
- **Teardown timers** (`JsonRpcStdioClient.close(timeout=5.0)`, process-group SIGTERM grace) are bounded budgets, not stall-detection. They stay, with documented per-call budgets and a separate RFC follow-up under task #7 for cumulative team-teardown.

The architectural distinction is **"wall-clock that emits signal"** vs **"wall-clock that acts on the model."** The first is fine. The second has been the source of every false-positive in our observed corpus.

This RFC proposes the new event taxonomy, the migration plan, and the lead-offline carve-out where action-triggering timers still earn their keep.

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
| `kimi invoke(timeout_s=600)` | 600s | `backends/kimi/invoke.py:685` | Kimi subprocess wall-clock cap; raises a typed `turn_timeout` error class (`backends/kimi/invoke.py:623`). The Kimi backend has no `turn/steer` equivalent so a hard cap is the only escape today. **Future-phase demotion path** (per opus-reviewer item 6): once §5.5's `working_on` contract ships and Kimi declares `working_on_compliance: best_effort` or `strict` in its capability manifest, this 600s can be demoted to a backstop with `app_server_idle_quiet`-style signal driving lead action. Until then, hard cap stays. The migration is gated on Kimi-side cooperation, not on this RFC. |
| `WATCH_RUST_TIMEOUT_MS` | (Rust default) | `watch_inbox.py:178` | inotify polling primitive — internal to the watcher. |
| Codex `tools/call` 120s ceiling | 120s | codex-app-server internal (not a wrapper-side timer) | Codex's MCP client enforces a 120s ceiling on any `tools/call` regardless of the shell command's own `timeout` parameter. Empirical evidence: codex-implementer-a called `mcp_anyteam_shell` with `command="uv run pytest -q"` and `timeout=600`; codex killed at 120s with error `"timed out awaiting tools/call after 120s"`. This is **not** a claude-anyteam-side timer (we don't enforce it), but it surprises any teammate trying to run a long suite. **Lead/peer workaround**: chunk long-running commands into ≤120s slices, or land a wrapper-level `chunked_shell` MCP helper (tracked separately, does not gate this RFC). The workaround is documented here so the inventory is complete and so the skill-prompt update in Phase C can teach peers about the ceiling. |

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

**Four-leg taxonomy integration** (per opus-reviewer item 2 — PR #42 precedent): every new envelope must coherently update FOUR registration points or `_safe_load` returns None and the receiver degrades to prose. The table below documents each envelope's commitments. Phase A implementation PRs MUST touch every "yes" cell in lock-step.

| Envelope | top-level `kind` | `VisibilityEventKind` Literal (`messages.py:365`) | `parse_protocol_text` dispatch (`messages.py:489`) | `KNOWN_TASK_BLOCKED_REASONS` (`messages.py:275`) | `ERROR_CLASSES` + `classify_failure` (`diagnostics.py:117`) |
| --- | --- | --- | --- | --- | --- |
| `wrapper_tool_failure_unrecovered` | new top-level kind (not `visibility_degraded` — this is a discrete state-change signal, not an ambient degradation) | **add** | **add** to dispatch set | **add** the token (a lead acting on this event may choose `task_reassign` with this as the typed `reason`) | **add** `wrapper_tool_failure_unrecovered` to `ERROR_CLASSES`; extend `classify_failure` to recognize the substring shape |
| `app_server_idle_quiet` | new top-level kind (heartbeat semantics don't map onto "degraded"; the model may be doing legitimate work) | **add** | **add** | **no** (signal, not interrupt — the lead may steer or wait, neither closes the task) | **no** (not a failure shape; healthy long turns also emit it) |
| `subprocess_pressure` | new top-level kind | **add** | **add** | **no** (signal; remediation lives in `claude-anyteam diagnose`) | **no** (not a failure unless the underlying #43 issue manifests, which would then route through existing classes) |
| `transport_alive` | new top-level kind (heartbeat; per opus-reviewer item 10, stays a `VisibilityEvent` not a `MailboxMessage` — see §9 Q2 answer) | **add** | **add** | **no** | **no** |

**Why not `visibility_degraded` with `payload.surface`?** PR #42 chose `visibility_degraded` for `app_server_initialize_timeout` because that envelope represents an *ambient degraded state* (the handshake is broken; observing it doesn't change with time). The four new envelopes are *discrete state-change signals* — `wrapper_tool_failure_unrecovered` fires once per qualifying tool failure; `transport_alive` is a heartbeat; `app_server_idle_quiet` is a debounced edge-trigger. Distinct `kind` values let downstream consumers filter cheaply (north star §3) without parsing `payload.surface`.

### 5.1 `wrapper_tool_failure_unrecovered`

**Emitted when:** the wrapper MCP returns an error to the routed CLI (Errno 2 on file ops, schema-validation fail on task_update, etc.) AND no observable event of any kind arrives within a bounded window **W** after the failure. Default `W = 90s`, configurable per-team in [60, 300]s.

**Window grounding (opus-reviewer item 1, empirical):** the original draft proposed W=5–10s. That is empirically falsified. Live telemetry from `/home/rosad/.claude/teams/anyteam-issues/events/codex-implementer-a.jsonl` (2026-05-12T16:35:36Z–16:37:15Z, ~17 events including 3 `visibility_degraded` from `wrapper_tool` errors) shows codex's natural Mode A recovery pattern:

| Wall-clock after first failure | Observation |
| --- | --- |
| seq=7 (16:35:36.629Z) | first `wrapper_tool` failure (`mcp_anyteam_read_file`) |
| seq=8 (16:35:37.559Z, +0.9s) | next `tool_event` — model immediately tried another tool |
| seq=14 (16:35:38.273Z, +1.6s) | continued tool activity (`task_list completed`) |
| seq=15 (16:35:55.872Z, +19.2s) | **17.6s natural quiet gap** during Mode A recovery |
| seq=21 (16:36:17.528Z, +40.9s) | **19.7s gap** between productive bursts |
| seq=31 (16:36:30.700Z, +54.1s) | another `wrapper_tool` failure mid-recovery — model still producing work |
| seq=32 (16:36:36.437Z, +59.8s) | model recovered from that failure too |

The **longest observed natural quiet gap during productive recovery was 19.7s**. The total productive recovery span before any "real" stall was ≥ 60s. Setting W=5–10s would fire `wrapper_tool_failure_unrecovered` during every Mode A recovery in our captured corpus, priming the lead to issue `task_reassign` against a model that's actively working. False-positive engine.

**Conservative default W=90s** = 4.5× the longest observed natural gap (19.7s × 4.5 ≈ 89s) and ≥ the total productive recovery span (~60s). This is the cheapest defensible setting; tighter is a sharpening question for Phase A's measurement task (see §8 Phase A.5).

### 5.1.1 Mode A/B discriminator (item 5)

The full algorithm. opus-reviewer flagged §3's bimodality acknowledgment didn't operationalize; this is the operationalization. Implementation lives in the wrapper's event-emission loop alongside the existing `_app_server_transport_alive` probe.

```python
def on_wrapper_tool_error(error_event: ToolEvent) -> None:
    """
    Triggered when the wrapper MCP returns an error to the routed CLI.
    Schedules a delayed Mode A/B discriminator check.
    """
    schedule_after(window_s=W, fn=lambda: _discriminate(error_event))

def _discriminate(error_event: ToolEvent) -> None:
    """
    Run W seconds after a wrapper_tool failure. Decide Mode A (recovery
    in progress) vs Mode B (model gave up / stalled).

    Mode A: at least one of:
      - new turn_progress envelope (any severity)
      - new tool_event (success or failure — a retry counts as progress)
      - new artifact_event (file change, commit)
      - agentMessage byte-length delta > 0

      All four are observable in the existing event stream. The exact
      OR is the wrapper's existing "any progress?" predicate
      (codex.py:2196-2199), lifted into a shared helper for clarity.

    Mode B: none of the above within W. Emit wrapper_tool_failure_unrecovered.
    """
    window_start = error_event.timestamp_ms
    window_end = window_start + (W * 1000)

    progress_events = read_events_since(
        team=error_event.team,
        agent=error_event.agent,
        since_ms=window_start,
        kinds=("turn_progress", "tool_event", "artifact_event"),
    )

    if any(e.timestamp_ms <= window_end for e in progress_events):
        return  # Mode A — model recovered. No emission.

    if last_agent_message_byte_delta_since(window_start) > 0:
        return  # Mode A — model still producing prose. No emission.

    emit_visibility_event(
        kind="wrapper_tool_failure_unrecovered",
        severity="warn",
        summary=f"wrapper tool {error_event.tool_name} failed; no recovery activity in {W}s",
        payload={
            "tool_name": error_event.tool_name,
            "error_class": error_event.error_class,
            "error_detail": _bounded_text(error_event.error_detail, 600),
            "turn_id": error_event.turn_id,
            "last_progress_at_ms": last_progress_at_ms(),
            "silence_window_ms": W * 1000,
            "recovery_hint_dispatched": False,
        },
    )
```

**Why ANY event kind (not just `turn_progress`)?** Because the empirical evidence shows productive recovery is dominated by `tool_event` (retry attempts) not `turn_progress` (model prose). A `turn_progress`-only check would still fire false-positively against the codex-implementer-a corpus. The four-kind disjunction matches what `codex.py:2196-2199` already treats as "observable progress."

**Future-phase positive-signal alternative (opus-reviewer item 1b):** once §5.5 `working_on` is shipping and backends declare `working_on_compliance: best_effort` or `strict`, the discriminator can grow a `gave_up` claim from the model side — e.g., the model emits `working_on: gave_up — wrapper_tool failures repeatedly` and we trigger emission on the positive signal instead of inferring from quietude. This is filed as a follow-up task (see §7.6) and is the §1-respecting endgame. For v1 we ship the quietude-based discriminator with the empirical W=90s default.

**Payload shape:**
```json
{
  "kind": "wrapper_tool_failure_unrecovered",
  "severity": "warn",
  "payload": {
    "tool_name": "mcp_anyteam_read_file",
    "error_class": "enoent",
    "error_detail": "/path/that/does/not/exist: Errno 2",
    "turn_id": "019e1d0a-473c-7061-ac0e-22ba789eca62",
    "last_progress_at_ms": 1715541336629,
    "silence_window_ms": 90000,
    "recovery_hint_dispatched": false
  }
}
```

**Potential lead-side responses (informational; lead decides):** the lead may, depending on context and corroborating signals, choose to `task_reassign` (the typed `KNOWN_TASK_BLOCKED_REASONS` token is `wrapper_tool_failure_unrecovered`), send a recovery_hint via `task_update`, or do nothing if the model is observed to recover after envelope emission. The envelope itself does **not** act on the model — there is no wrapper-side steer, interrupt, or kill (§0). This is informational signal; action authority stays with the lead (and, per north-star §3, with peers when the lead is offline).

**Relationship to soft watchdog:** signals roughly the same failure as the 300s `non_progress_warn_s`, at 3.3× earlier and without auto-steering the model. The soft watchdog is removed in Phase D (§8) once this envelope is observed catching the same cases.

### 5.2 `app_server_idle_quiet`

**Emitted when:** for a configurable window (default 60s, range [30, 600]), the App Server has produced no notification AND no `tool_event` delta AND no `artifact_event` delta AND the transport is alive. Re-emitted at most once per window per turn (debounced — see §7.4 cardinality).

**Payload shape:**
```json
{
  "kind": "app_server_idle_quiet",
  "severity": "info",
  "payload": {
    "turn_id": "...",
    "elapsed_s": 90,
    "since_last_progress_s": 75,
    "transport_alive": true,
    "tool_calls_in_flight": ["Bash:pytest"],
    "last_working_on": "running test suite"
  }
}
```

**Potential lead-side responses:** *usually nothing* — the in-flight tool tells the lead why the model is quiet. The lead may escalate (send a steer, or `task_reassign`) when `tool_calls_in_flight` is empty AND `last_working_on` is missing or stale. This envelope is **signal**, not interrupt; emission ≠ kill (§0).

**Replaces:** the 900s `turn_timeout_s` cap as the *signal* (the cap stays as a lead-offline backstop, §7.1).

### 5.3 `subprocess_pressure`

**Emitted when:** OS-level hints indicate the routed CLI's subprocess is alive but I/O- or disk-bound. Detection heuristics (per-backend, declared in capability manifest):
- Codex: sqlite WAL size over threshold (#43 follow-up); `/proc/<pid>/io` write_bytes growing without `tool_event` activity.
- Gemini: ACP transport responding to ping but throughput collapsed.
- Kimi: subprocess CPU≈0 but stat shows recent `mtime`.

**Payload shape:**
```json
{
  "kind": "subprocess_pressure",
  "severity": "info",
  "payload": {
    "kind": "sqlite_wal_replay",
    "hint": "codex sqlite WAL is 480MB; startup may be slow",
    "remediation": "claude-anyteam diagnose --codex-wal-truncate"
  }
}
```

**Potential lead-side responses:** distinguish #43-class slowness from stalls; *do not kill* the teammate; optionally run the suggested `remediation` command. The `claude-anyteam:diagnose` skill can read these events to surface remediation automatically.

### 5.4 `transport_alive` (heartbeat envelope)

**Emitted when:** every N seconds (**default 90s**, range [30, 600], **per-teammate configurable** — bumped from the original 30s draft per opus-reviewer item 4 cardinality concern), the wrapper checks the routed CLI's transport is still responsive. Emission shape mirrors `app_server_initialize_progress` (`codex.py:928`).

**Why 90s default?** No lead reacts to a heartbeat within 30s anyway, and 30s × N teammates × the per-team aggregate `visibility.jsonl` lock (see §7.4) was the cardinality concern. 90s detects a wedged fd within 2–3 cycles (~3–5 min) — still within the lead's reaction loop.

**Payload shape:**
```json
{
  "kind": "transport_alive",
  "severity": "info",
  "payload": {
    "transport": "jsonrpc_stdio",
    "rtt_ms": 4,
    "last_event_at_ms": 12345,
    "cadence_s": 90
  }
}
```

**Routing (item 10 answered):** `transport_alive` is a `VisibilityEvent`, not a `MailboxMessage`. Rationale: post-hoc forensics needs heartbeat data in the event log (matches `app_server_initialize_progress` precedent from PR #42). Mailbox-side filtering for "don't crowd substantive content" is handled by message_kind, not by routing through a different channel.

**Replaces:** the implicit assumption that "no notification → wedged transport." Now the absence of `transport_alive` is itself the signal.

### 5.5 Model-emitted `working_on` claim (prompt contract)

**Required:** every backend prompt template (`prompts.py`, `backends/kimi/prompts.py`, gemini equivalents) gains a one-line contract: *"emit a `working_on` claim every ~60s of work or after every tool_call, whichever is sooner. Format: a 1-line description of current activity."* Cadence raised from the original 30s draft per the same cardinality concern (§7.4).

**Surface:** flows as a `turn_progress` envelope with `payload.working_on_claim = "..."`. Does not introduce a new `VisibilityEventKind`; reuses the existing `turn_progress` kind with a structured payload field. (Four-leg taxonomy: zero new entries — adds a payload field on an existing kind. This is the cheapest possible integration.)

**Capability declaration:** each backend declares `working_on_compliance: "strict" | "best_effort" | "absent"` in its capability manifest. `absent` means the stall-detection backstop (§7.1) cannot be raised against this backend (and §5.1's future positive-signal `gave_up` discriminator can't be used).

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

### 7.1 Overnight / lead-offline runs (item 7)

The user has explicitly said "all night is fine" for some long-running tasks. With no human watching the event stream, events without an observer have no effect. Three options:

1. **Keep `turn_timeout_s` and `non_progress_interrupt_s` as opt-in for these scenarios.** This is the RFC's recommendation for v1. The lead's invocation (`/loop`, `schedule`, autonomous mode) is the right place to opt in. This is the §0-aligned hold: action-triggering timers are appropriate when no observer exists.
2. **Spawn a Claude lead subagent ("watchdog persona") that consumes events overnight.** Cheap because most idle periods are healthy. This is the §3-aligned solution.
3. **Cron-style heartbeat:** require the team-lead process to emit a periodic `lead_alive` event; if absent for >5min, peers fall back to wall-clock timers. Symmetric to `transport_alive`. Lighter-weight than option 2; loses the "interpret events intelligently" benefit.

**v1 commitment:** option (1). The opt-in flag `non_progress_interrupt_s` stays, documented as the overnight-runs knob.

**Follow-up commitment (per opus-reviewer item 7):** option (2) — the watchdog persona — **is filed as a tracked task at this RFC's merge**, not left as a §9 open question. The task title is "META: lead-watchdog persona for overnight/autonomous runs" and references this RFC §7.1. Option (3) is sketched there as an alternative; both will be evaluated empirically before §7.1 v2 of this RFC. Without that commitment, option (2) would become "the ghost design that justifies leaving the timer in indefinitely" (opus-reviewer's exact phrasing). The follow-up task makes the revisit concrete.

### 7.2 Routed-CLI non-compliance with `working_on`

A backend whose model ignores the prompt instruction reverts to "silent thinking is indistinguishable from stall." Mitigations:

- Typed progress events (`turn_progress`, `tool_event`, `artifact_event`) flow regardless of model cooperation.
- Per-backend capability declaration (`working_on_compliance`) tells peers how much to trust the absence of a `working_on` claim.
- For `working_on_compliance: "absent"` backends, the timer backstop (§7.1) is automatic.

### 7.3 Steer-resistant stalls (transport wedged)

A teammate whose JSON-RPC fd is wedged can't emit events AND can't be steered. `transport_alive` absence catches this; potential lead-side response is `task_reassign` + force-kill via teardown. No new wrapper-side timer is added — `transport_alive` absence (= no envelope for >2 cadence windows) *is* the signal.

### 7.4 Cardinality and lock contention (item 4)

opus-reviewer's original concern: the per-team aggregate `visibility.jsonl` file (`protocol_io.py:514`, `team_visibility_event_path`) sees every visibility-event write. The fan-out path is: `append_event` → write to `events/<agent>.jsonl` (per-agent) AND mirror to `team/visibility.jsonl` (aggregate), both under `events/.lock`. For **M** teammates emitting at cadence **C** seconds, the steady-state write rate to that single lock is **2M/C writes/second** (per-agent + aggregate).

**The original draft's "~2 envelopes/min/teammate" was wrong.** Combining `transport_alive` (30s cadence → 2/min) + `working_on` claims (30s cadence → 2/min) + occasional state-change envelopes ≈ **4–6/min/teammate**. At M=10 ⇒ 1.3 writes/s on the single lock. At M=100 ⇒ 13 writes/s. opus-reviewer is correct: the original cost claim was hand-waved.

**v2 mitigations** (committing to option (b) from opus-reviewer's three choices, plus per-teammate configurability):

| Envelope | Original cadence | v2 cadence | Per-teammate configurable? |
| --- | --- | --- | --- |
| `transport_alive` | 30s | **default 90s, range [30, 600]** | yes |
| `working_on` claim | 30s | **default 60s, range [30, 300]** | yes (via `working_on_compliance` capability + per-team override) |
| `app_server_idle_quiet` | 60s window, debounced ≤1/window | **unchanged** (already debounced) | yes (window configurable) |
| `wrapper_tool_failure_unrecovered` | state-change, 5–10s window | state-change, **W=90s** window | yes (W configurable) |
| `subprocess_pressure` | state-change | state-change, **debounced ≥ 60s** | yes |

**Resulting cardinality at defaults:** ~2 envelopes/min/teammate steady-state (1 `transport_alive` per 90s + 1 `working_on` per 60s). At M=100, that's 200 envelopes/min total = ~3.3 writes/s on the aggregate lock. **Still high, but bounded and acceptable.** For comparison, today's `app_server_initialize_progress` cadence is 30s during init and we've not seen aggregate-lock contention in production.

**If empirical measurement (Phase A.5) shows the aggregate lock is the bottleneck at M ≥ 50,** the fallback is opus-reviewer's option (a): decouple `transport_alive` and `working_on` from the aggregate mirror (write only to per-agent jsonl). Trade-off: the lead's TUI projection loses heartbeat visibility unless it attaches per-agent. Documented as a Phase A.5 contingency, not the default.

**Phase A.5 (new):** measurement task — synthetic load at M={10, 50, 100} with the v2 cadence. Report aggregate-lock held-time and write-rate. Gate Phase B on the measurement: if M=100 lock contention exceeds 50ms/lock-acquisition, fall back to per-agent-only routing for `transport_alive` and `working_on`.

### 7.5 Backwards compatibility (item 9)

opus-reviewer's correct flag: anyone who didn't pin `non_progress_warn_s` (the majority) gets a silent behavior change when the default flips 300 → None — they lose the soft watchdog. CHANGELOG alone is weak.

**v2 commitment:** one release of explicit warning before the flip:

- **Release N (RFC ships, Phase A emits events):** no defaults change. The new envelopes start flowing. Users with `non_progress_warn_s` at its default 300 see the soft watchdog continue to fire AND the new envelopes alongside. Logs a `visibility_degraded` envelope at first turn with `surface: "non_progress_warn_s_default_deprecated"` payload pointing at this RFC.
- **Release N+1 (Phase B flips defaults):** default flips to None. Users who saw the N-release warning have one cycle to opt in explicitly. Users who didn't (no prior runs) get the new default cleanly.

This pattern matches the deprecation rung-2-then-rung-3 ladder used elsewhere (e.g., `KNOWN_TASK_BLOCKED_REASONS` enforcement). PR for task #5 (which lands the Phase B default flip) is technically jumping straight to rung 3 — that PR (#52) was filed before this v2 RFC revision. **The mitigation:** v2 RFC notes that PR #52's default flip pre-empted the one-release warning cycle. CHANGELOG already documents the env-var restore knob (`CLAUDE_ANYTEAM_NON_PROGRESS_WARN_S=300`). Acceptable risk because the prior default (300s steer) was *itself* a user-pain source the user explicitly wanted gone — flipping straight to None is closer to user intent than gradual deprecation. If reviewers disagree, PR #52 can be amended pre-merge to emit the deprecation envelope for one release before the flip.

### 7.6 Future: model-side `gave_up` claim (positive-signal alternative to §5.1 quietude inference)

(per opus-reviewer item 1b deferred path)

Once §5.5 `working_on` is shipping and backends declare `working_on_compliance: best_effort` or `strict`, §5.1's quietude-based discriminator can be **replaced** by a model-side positive signal: the model emits `working_on: gave_up — wrapper_tool failures repeatedly` and the wrapper emits `wrapper_tool_failure_unrecovered` on the positive signal, not on inferring from silence.

This is the §1-respecting endgame: the *agent* declares its state, the wrapper does not guess. It's deferred to follow-up because (a) it requires `working_on_compliance: best_effort` minimum across all backends we ship, (b) the prompt contract for `gave_up` needs design work (false-claim risk, taxonomy of give-up reasons), and (c) the v1 quietude discriminator is sufficient to ship the #49-class win immediately.

**Filed as a follow-up task at RFC merge:** "RFC: positive-signal `gave_up` claim — §5.1 successor."

---

## 8 — Migration plan

**Phase A — emit the events** (no behavior change).

For each of the four new envelopes, the implementation PR MUST update all four taxonomy registration points (see §5 integration table) in lock-step or the receiver degrades to prose. The PR-#42 lock-step pattern is the precedent.

1. **Per-envelope four-leg taxonomy registration** in `messages.py` and `diagnostics.py` — table at §5.
2. Add `wrapper_tool_failure_unrecovered`, `app_server_idle_quiet`, `subprocess_pressure`, `transport_alive` to `protocol_io.py` as new `VisibilityEvent` emitters mirroring `emit_initialize_timeout_visibility_degraded`.
3. Wire emission in `codex.py` (App Server polling loop, including the §5.1.1 Mode A/B discriminator), `backends/gemini/loop.py`, `backends/kimi/loop.py` where applicable per per-backend capability.
4. Add `working_on` contract to `prompts.py` and per-backend prompt templates.
5. Declare per-backend `working_on_compliance` and new event capability strings in the capability manifest.
6. Wire `claude-anyteam:diagnose` skill / `visibility_tail` to recognize the four new envelopes.

Ship as a normal feature PR. No defaults change.

**Phase A.5 — cardinality measurement** (gate for Phase B; per opus-reviewer item 4).

Synthetic load at M={10, 50, 100} teammates with v2 cadences (§7.4 table). Report aggregate-lock held-time and write-rate per second on `events/.lock`. Gate Phase B on the measurement: if M=100 lock contention exceeds 50ms per acquisition or 10 writes/s sustained, fall back to per-agent-only routing for `transport_alive` and `working_on` (skip aggregate mirror).

**Phase B — flip defaults** (task #5 territory, partially landed as PR #52).

1. `turn_timeout_s` default 900 → 1800 (task #5 / PR #52 lands this).
2. `non_progress_warn_s` default 300 → None (off by default). Keep the knob; opt-in for users who want the soft watchdog steer behavior. (PR #52 lands this; the one-release deprecation envelope mitigation discussed in §7.5 is a follow-up amendment if reviewers prefer.)
3. CHANGELOG entry; release note documenting the new defaults and the visibility events that replace them. (Already in PR #52.)

**Phase C — teach the lead and peers** (skill + manifest update).

1. `claude-anyteam:help` skill gains a "stall handling" section. Crucial framing (per §0): the text describes **what each envelope means and what *potential* lead-side responses exist**, not "the right action." Example: *"`wrapper_tool_failure_unrecovered` indicates the model didn't produce observable progress for W=90s after a wrapper tool failure. **Potential responses** (lead decides): inspect `events/<agent>.jsonl` around the event timestamp to check Mode A vs Mode B; if Mode B, send a recovery_hint via `task_update` or issue `task_reassign`; if Mode A, wait for the model's natural recovery."*
2. Peers get the same guidance via capability-manifest semantic-guidance fields. Per north-star §3, peers should be able to react when the lead is offline — same envelope, same potential responses, same authority chain.
3. Add the codex `tools/call` 120s ceiling warning to the skill body (per §1.2 inventory entry).

**Phase D — unify wall-clock loops** (cleanup; not "remove all wall-clock," per item 8).

Once Phase A–C have shipped and the visibility events are observed catching the same cases in production, the redundant wall-clock loop in `codex.py:2084-2180` (the soft watchdog's `(now - last_progress_at) >= non_progress_warn_s` check) becomes redundant with `app_server_idle_quiet`'s emission loop. Phase D **unifies these two wall-clock loops onto one** — `non_progress_interrupt_s` (when opt-in by overnight users) becomes a consumer of `app_server_idle_quiet` events rather than computing its own delta.

**Clarification (opus-reviewer item 8):** this is not "Phase D removes the wall-clock delta" — the wrapper still has to know wall-clock time to emit `app_server_idle_quiet`. What's unified is **the count of wall-clock loops** (one signal-emitter instead of two parallel deltas). The action-triggering loop collapses onto the signal-emitting loop. The wrapper still has wall-clock state; we just don't maintain two parallel copies of it.

---

## 9 — Open questions for `team-lead` (revised after opus-reviewer's items 7 and 10 closed)

1. **Per-backend `working_on_compliance` — measured or trusted?** Empirical measurement would mean the wrapper tracks `working_on` claim frequency and downgrades the declared compliance if it slips. Probably overkill for v1; declare and trust. **Recommendation:** declare-and-trust for v1; revisit if Phase A.5 measurement reveals systematic non-compliance.
2. **Should `app_server_idle_quiet` ever auto-steer?** Today the soft watchdog auto-steers ("you have produced no externally visible checkpoint for Xs"). The visibility-driven model says: emit the event, let the lead decide. For autonomous overnight runs the auto-steer is the *only* thing that fires. **Recommendation:** keep auto-steer opt-in via `non_progress_interrupt_s`-style knob (i.e., the existing knob's new behavior in Phase D unification — see §8 Phase D).
3. **Is Phase A.5's measurement budget worth the wait?** Phase A is large enough already (four envelopes + working_on + 4-leg taxonomy x 3 backends). Adding a measurement gate before Phase B delays the user-visible win (the default flip). **Alternative:** ship Phase B in parallel with A.5; if A.5 finds aggregate-lock contention, follow-up PR routes around it. **Recommendation:** ship in parallel; A.5 is a gate on the *cadence-tightening* decisions, not on the default flip itself.

**Closed by v2:**
- ~~"Is the overnight carve-out enough?"~~ — closed by §7.1 commitment to file watchdog-persona follow-up task at merge (item 7).
- ~~"Should `transport_alive` be a `VisibilityEvent` or a `MailboxMessage`?"~~ — closed by §5.4 answer: `VisibilityEvent` (item 10).

---

## 10 — Summary

Stall-detection timers that **act on the model** are coping mechanisms for missing visibility. The user has felt this directly: the 900s `turn_timeout_s` interrupt was the dominant pain. Four typed envelopes + a model-emitted `working_on` claim **replace the action** (steer / kill) with **signal** (lead-observable envelope, lead decides). The envelopes are still wall-clock-driven (§0); they just emit instead of acting.

After Phase B (already landed by PR #52) the user's central complaint ("the 900s for codex is really fucking us over") is structurally resolved: cap is 1800s. After Phase A (the four envelopes) the lead can intervene at second 90 (`wrapper_tool_failure_unrecovered` window W=90s, grounded in the codex-implementer-a empirical trace), not second 900. After Phase D the two parallel wall-clock loops collapse onto one.

Wall-clock action-triggering interrupts earn their keep in exactly one scenario — **lead-offline overnight runs**, where no observer exists — and stay opt-in for that case via `non_progress_interrupt_s`. A follow-up task to design the lead-watchdog persona (option 2) lands at this RFC's merge, so option (1) is not the permanent answer by default. Bounded-I/O timers and teardown timers are separate categories and stay.

This RFC is the design layer for tasks #1 (#49 recovery — implementer-b should use §5.1's `wrapper_tool_failure_unrecovered` envelope with W=90s and the §5.1.1 Mode A/B discriminator), #4 (#40 Phase 2 — implementer-a should use `transport_alive` + the lead-offline carve-out), #5 (timeout defaults — already landed as PR #52), and is referenced from #7 (teardown speed) where the teardown timer carve-out applies.
