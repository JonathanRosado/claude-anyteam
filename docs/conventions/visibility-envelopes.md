# Visibility envelopes — contributor write-time guide

**Audience:** anyone opening a PR that adds, renames, or extends a typed
visibility envelope (anything that flows through `VisibilityEvent`,
`task_blocked.reason`, `ERROR_CLASSES`, or any related taxonomy registry),
or that adds a tunable knob governing one.

**Purpose:** the project has settled on a small set of conventions for how
typed events get registered, configured, and tested. They aren't obvious
from any single PR diff — they emerged across **PR #42** (four-leg
taxonomy precedent), **PR #51** (RFC §5.6 four-fold-surface lock-step),
**PR #52** (range validation), and the **follow-up reviews on PR #54 /
#55 / #56 / #60 / #61** (where every one of these patterns caught at
least one gap). This doc captures them so the next PR doesn't have to
rediscover them from review feedback.

Use it as a pre-flight checklist. **Before requesting review on a PR
that touches any typed-visibility surface, confirm every box below
applies or has a one-line written reason why it doesn't.**

The reference example through the rest of this doc is **PR #42** — it
landed the `app_server_initialize_*` envelope set, defined the
four-leg taxonomy precedent, and is the cleanest cross-cutting
implementation to imitate. Where a sharpen from a later review caught
a divergence, it's cited inline ("S-numbers" refer to comments in the
PR-#54, #55+#60, and #61 review threads).

---

## 1. Four-leg taxonomy integration

A typed event has **four registration legs** in the codebase. Adding a
new event without touching all four legs that apply produces a real bug
(`_safe_load` returns `None`, recipient degrades to prose, taxonomy
drift warn fires on the lead).

| Leg | Location | What it does |
| --- | --- | --- |
| **(a) `VisibilityEventKind` Literal** | `src/claude_anyteam/messages.py` (`VisibilityEventKind = Literal[...]`) | Declares the top-level `kind` so envelopes validate. |
| **(b) `parse_protocol_text` dispatch** | `src/claude_anyteam/messages.py` (`parse_protocol_text`) | Routes inbound JSON to the typed payload class so recipients see a structured object, not prose. |
| **(c) `KNOWN_TASK_BLOCKED_REASONS`** | `src/claude_anyteam/messages.py` | Stable token registry for lead-actionable `task_blocked.reason` values. The wrapper-MCP `send_message` validator warns on token-shaped reasons not present here. |
| **(d) `ERROR_CLASSES` + `classify_failure`** | `src/claude_anyteam/diagnostics.py` | Closed taxonomy for the prose-fallback path. Maps backend `result.error` strings to a single class at turn-end. |

**When each leg applies:**

- **(a) always.** If the envelope has a top-level `kind`, it needs a
  Literal entry. No exceptions.
- **(b) when a peer should react.** If any teammate or the lead is ever
  expected to receive this event by inbox / mailbox / event-log fan-out
  and act on it programmatically, register the dispatch. Skipping (b)
  silently turns the event into prose at the recipient — exactly the
  failure mode §2 visibility-parity exists to prevent.
- **(c) when the envelope represents a `task_blocked` reason a peer
  might filter on.** Lead-action tokens like
  `app_server_initialize_timeout` and `wrapper_tool_failure_unrecovered`
  belong here. Diagnostic-only signals do not.
- **(d) when the envelope is a *terminal* turn failure** (the turn ends
  and the prose-fallback path must classify it). **`ERROR_CLASSES = NO`
  for mid-turn signals** is the precedent set in RFC §5.1 sharpen #2
  (and re-asserted in PR #60): `wrapper_tool_failure_unrecovered` is a
  discrete state-change signal emitted *during* a turn — the turn may
  recover and complete normally afterwards, or fail later with its own
  reason. Synthesizing a fake `result.error` to feed `classify_failure`
  would conflate the in-flight signal with the terminal failure shape.
  If your envelope is in-flight, leave `ERROR_CLASSES` alone.

**Rule of thumb:** decide up front which of the four legs apply, write
them into the PR body as a 4-row table (copying RFC §5 / PR #51's table
shape), and touch each "yes" cell in lock-step.

---

## 2. Top-level `kind` vs `visibility_degraded` with `payload.surface`

`visibility_degraded` is for **ambient degraded state** — the
handshake is broken, the WAL is bloated, the manifest is stale. A
consumer observing the envelope at time T sees roughly the same
condition as at time T+10s (until remediated).

A new top-level `kind` is for **discrete state-change signals** —
`wrapper_tool_failure_unrecovered` fires once per qualifying tool
failure; `transport_alive` is a heartbeat; `app_server_idle_quiet` is
a debounced edge-trigger. Distinct kinds let downstream consumers
filter cheaply (north star §3) without parsing `payload.surface`.

**Decision flow:**

1. Is the envelope an *edge trigger* / *state-change signal*? → new
   top-level `kind`. Update (a) and (b) of the four-leg taxonomy.
2. Is the envelope an *ambient degraded condition*? → reuse
   `visibility_degraded` with a `payload.surface` discriminator. No
   new top-level kind needed; (a) and (b) already cover
   `visibility_degraded`.

**S2 from PR-#54 review** caught the inverse mistake: PR #54's
checkpoint-success envelope used `kind="turn_progress"` for a
pre-spawn event that is neither in-turn nor model-emitted. Consumers
filtering by `turn_progress` would see a false "model is making
progress" event. The fix is to either (a) use `visibility_degraded`
with `severity="info"` and a distinct `payload.surface`, or (b) mint
a new top-level kind if the discrete-state-change criterion applies.

**`turn_progress` / `turn_warning` semantic constraints:** these kinds
carry strong implicit semantics from existing consumers. Don't use
`turn_progress` for events that aren't actually inside a turn. Don't
use `turn_warning` for pre-turn-start events (e.g. before
`turn_started` has emitted). The taxonomy is partially conventional
and partially type-enforced; the conventional half is where reviews
catch things.

---

## 3. `mailbox` flag semantics

`VisibilityChannels.mailbox` controls whether the envelope lands in
the lead's inbox (forcing acknowledgment) or only in the event log
(passive observation).

**Convention:**

- **`mailbox=True`** when the event is **lead-actionable AND not
  self-recovering** — the lead needs to see it before the next
  meaningful turn cycle. PR #42 set this on
  `emit_initialize_timeout_visibility_degraded`
  (`protocol_io.py:949-953`) because an `initialize` timeout blocks
  the turn and the lead must act.
- **`mailbox=False`** when the event is **purely informational AND
  recoverable** — the wrapper or backend will mitigate before the
  next user-visible interaction. PR #54's WAL-bloat envelope uses
  `mailbox=False` because the in-line checkpoint usually drains the
  WAL before the spawn proceeds.

**S1 from PR-#54 review** raised the failure-of-recovery edge case:
when the checkpoint itself fails (`status="busy"` or
`status="timeout"`), the second `visibility_degraded` envelope still
defaults to `mailbox=False` — but at that point the bloat will likely
cause an `initialize` timeout anyway. Either flip `mailbox=True` on
the failed-recovery branch, OR add a code comment justifying
`mailbox=False` (typically: "the downstream `initialize_timeout` event
is the lead-facing surface, and it's already `mailbox=True`"). The
choice is fine either way; the failure mode is leaving it implicit so
a future reader can't tell whether `mailbox=False` was deliberate.

**Rule of thumb:** every `mailbox=False` envelope deserves a one-line
code comment naming the downstream lead-facing surface (or asserting
that the recovery is reliable enough not to need one). Implicit
choices get flagged in review.

---

## 4. Configuration surface — two knob classes, two checklists

Not every tunable needs every surface. There are **two classes of
config knob** in this codebase, and they have different requirements.
Decide which class your tunable falls into before adding surfaces.

### 4.a Per-teammate behavioral knobs (full four-fold surface)

Tunables that can reasonably differ **per teammate** within a single
team — turn budgets, non-progress thresholds, discriminator windows
that the lead might want to set tighter for one routed teammate than
another. These get the full four-fold surface in lock-step:

| Surface | Example (from `wrapper_tool_failure_window_s`) |
| --- | --- |
| **CLI flag** | `--wrapper-tool-failure-window-s` |
| **Environment variable** | `CLAUDE_ANYTEAM_WRAPPER_TOOL_FAILURE_WINDOW_S` |
| **`agents/<name>.json` key** | `wrapper_tool_failure_window_s` |
| **Capability-manifest field** (when backend-specific) | n/a for wrapper-side primitives; reuse existing fields like `working_on_compliance` for backend-declared variants |

**Examples in this class:** `turn_timeout_s`, `non_progress_warn_s`,
`non_progress_interrupt_s`, `wrapper_tool_failure_window_s`.
Per-teammate overrides are the whole point — a `codex-implementer`
running long Codex turns wants a different `turn_timeout_s` than a
`codex-reviewer` doing quick reviews.

### 4.b Wrapper-process-wide mitigation knobs (env-var-only acceptable)

Tunables that govern **wrapper-side mitigation policy** the operator
sets globally for their installation — sqlite WAL thresholds,
checkpoint enable/disable flags, app-server initialize budgets. These
typically have one correct value per host (the operator's tolerance
for bloat, the cold-start budget the operator's hardware supports);
varying them per-teammate would be operator confusion, not flexibility.

For this class, **env-var-only is acceptable** (with `env.py`
registration still required). CLI flag and `agents/<name>.json` key
are optional and should be skipped unless there's a concrete reason
to vary per spawn.

**Examples in this class:**
- `CLAUDE_ANYTEAM_APP_SERVER_INITIALIZE_TIMEOUT_S` — env-only since
  PR #42; matches the operator's tolerance for cold-start latency,
  not a per-teammate tuning surface.
- `CLAUDE_ANYTEAM_APP_SERVER_INITIALIZE_PROGRESS_INTERVAL_S` —
  env-only; cadence of progress events is a host-wide observability
  preference.
- `CLAUDE_ANYTEAM_CODEX_SQLITE_WAL_WARN_THRESHOLD_BYTES`,
  `CLAUDE_ANYTEAM_CODEX_SQLITE_WAL_CHECKPOINT`,
  `CLAUDE_ANYTEAM_CODEX_SQLITE_WAL_CHECKPOINT_TIMEOUT_S` — env-only
  since PR #65; the WAL bloat threshold and checkpoint policy belong
  to the operator's Codex install, not a per-teammate decision.

### Decide which class you're in — a one-question test

Ask: **"Would a single team realistically want different values for
this knob across two of its teammates?"**

- **Yes** → class 4.a, full four-fold surface in lock-step.
- **No** → class 4.b, env-var-only is acceptable; `env.py`
  registration is still required either way.

If unsure, default to 4.a — adding surfaces later is harder than
trimming unused ones, and per-teammate overrides are forward-compatible
even when no one is using them.

### Naming convention (both classes — RFC §5.6)

- CLI flag (class 4.a only) is `--<knob-name>-s` (terminal `-s`
  indicates seconds; matches `--turn-timeout-s`,
  `--non-progress-warn-s`).
- Env var prefix is always `CLAUDE_ANYTEAM_*` (the rebrand preserves
  `CODEX_TEAMMATE_*` as a fallback for legacy installs — see
  `env.py` — but new vars do NOT need a legacy alias).
- JSON key (class 4.a only) is the env var name minus the prefix,
  lowercased with underscores.
- Env var name **must** be registered as a module-level constant in
  `src/claude_anyteam/env.py` (both classes), not inline in the
  caller. **S3 from PR-#54 review** caught the inverse:
  `codex_log_bloat.py` defined three new env var names as local
  constants, which made them invisible to anyone reading `env.py`
  for the catalog of tunables. Move them all to `env.py`. **PR #65
  closed that gap** for the WAL-bloat env vars; the pattern is the
  precedent for class 4.b.

**Cross-reference:** when adding a class-4.a tunable, also add it to
the RFC's existing-knobs table
(`docs/design/timers-vs-visibility.md` §5.6) so the inventory stays
current. Class-4.b tunables don't need to appear in §5.6 (the RFC's
scope is per-teammate behavioral knobs), but they MUST be documented
in the `CHANGELOG.md` `## [Unreleased]` entry with their default and
range — operators read the changelog to learn what they can set.

---

## 5. Range validation with bilateral bounds

Tunables that take a numeric value need **explicit upper AND lower
bound validation that raises `ValueError`**. The precedent is PR #52's
`turn_timeout_s` validation in `config.py`:

```python
if not (60.0 <= turn_timeout_s <= 3600.0):
    raise ValueError(
        f"turn_timeout_s must be in [60, 3600] seconds, got {turn_timeout_s}"
    )
```

**Why bilateral, not just non-negative:**

- An operator setting `WAL_WARN_THRESHOLD_BYTES=0` would fire the
  bloat envelope on every spawn — a false-positive engine.
- A user setting `wrapper_tool_failure_window_s=2` would fire the
  envelope during every Mode A recovery (the natural quiet gap can
  reach ~20s for codex), priming the lead to act against a working
  model.
- A `turn_timeout_s=99999` is clearly an operator error.

Both directions are real failure modes. **S4 from PR-#54 review**
caught `_int_env`/`_float_env` helpers in `codex_log_bloat.py` using
`max(0, value)` style clamping — the upper bound was missing
entirely.

**Range conventions used elsewhere in the codebase:**

- `turn_timeout_s`: `[60, 3600]` seconds (one minute to one hour;
  the user's stated upper-bound preference per MEMORY.md).
- `non_progress_warn_s`: `[60, 1800]` seconds (proportional to the
  new `turn_timeout_s` default after PR #52).
- `wrapper_tool_failure_window_s`: `[60, 300]` seconds (empirically
  grounded in the natural Mode A recovery gap distribution — see
  RFC §5.1).

**Rule of thumb:** pick a range based on empirical or doctrinal
grounding, document the grounding in the docstring or RFC, and raise
`ValueError` on out-of-range. Don't use `max(min(...))` clamping;
clamping hides operator errors. Tests should cover both the lower
and upper boundary (one value just inside, one just outside, both
directions).

---

## 6. CHANGELOG `## [Unreleased]` extension

Every PR that adds, changes, or removes a user-visible surface (env
var, CLI flag, JSON key, default value, envelope kind, taxonomy
token, diagnose subcommand) extends `CHANGELOG.md`'s `## [Unreleased]`
section under the appropriate Keep-a-Changelog heading
(`### Added`, `### Changed`, `### Fixed`, `### Removed`,
`### Documentation`).

**S8 from PR-#54 review** caught the omission: PR #54 added a new
`--codex-log-bloat` diagnose subcommand and three new env vars but
extended none of them into the changelog. The release script bumps
the version and renames `[Unreleased]` to the version tag at release
time; if `[Unreleased]` is empty, the surface change becomes
invisible to anyone reading the changelog after release.

**Convention:** PRs that ONLY change internal implementation
(refactor, test, comment) don't need a changelog entry. PRs that
add, change, or remove anything an operator would set, see, or
configure DO. When in doubt, add an entry — a too-detailed changelog
is recoverable; a missing entry isn't.

---

## 7. Test conventions: boundary cases and negative-path coverage

Test the **mechanism**, not the **magic number**. A test that asserts
`assert config.window == 90` proves the constant didn't change; it
doesn't prove the code does the right thing when the constant *does*
change. PR #42 set the precedent with its initialize-visibility
suite: progress events fire at the configured cadence (regardless of
what that cadence is), the timeout error message contains the
configured timeout value, the env-var override lands correctly.

**Coverage matrix every visibility-envelope PR should hit:**

- **Lower-bound + upper-bound range validation** — one value just
  inside each bound (no raise), one value just outside (raises
  `ValueError`). Skipping the negative-path tests is the most common
  gap caught in review.
- **Default-when-unset** — when no CLI / env / JSON value is given,
  the documented default applies.
- **Override precedence** — CLI beats env beats JSON beats default
  (or whatever the documented precedence is; assert it).
- **Failure-branch payloads** — if the code path has distinct status
  values (e.g. `busy` / `timeout` / `error` / `missing_db` in PR
  #54's checkpoint result), each branch needs a test. **S6 from
  PR-#54 review** caught the gap: the success branch was covered;
  the four failure branches were not. The fix is fixtures that
  force each branch (long-running transaction for `busy`,
  `timeout_s=0.001` for `timeout`, non-existent path for
  `missing_db`).
- **Proof-of-repro and proof-of-fix as tests, not transcripts** —
  per the user's standing requirement (MEMORY.md
  `feedback_repro_and_fix_proof`). The repro test asserts the
  failure shape pre-fix; the fix test asserts the behavior change
  post-fix. PR #42's `test_pre_spawn_warning_emits_visibility_degraded_before_initialize`
  is the cleanest precedent — it asserts both that the envelope IS
  emitted AND that its sequence position is before
  `app_server_initialize_completed`.

---

## 8. Quick pre-flight checklist

Before opening a PR that adds or extends a typed visibility envelope,
confirm each line below applies (or has a one-line reason it doesn't):

- [ ] **Four-leg taxonomy** — does this envelope need to update (a)
      `VisibilityEventKind`, (b) `parse_protocol_text`, (c)
      `KNOWN_TASK_BLOCKED_REASONS`, (d) `ERROR_CLASSES`? Decide each
      cell in the PR body using the RFC §5 table shape.
      `ERROR_CLASSES = NO` is the right answer for mid-turn signals.
- [ ] **Top-level kind vs `visibility_degraded`** — is the envelope a
      *discrete state-change signal* (new kind) or *ambient degraded
      state* (reuse `visibility_degraded` + `payload.surface`)?
      Don't use `turn_progress` / `turn_warning` for events that
      aren't actually in a turn.
- [ ] **`mailbox` flag** — `mailbox=True` for lead-actionable +
      not-self-recovering; `mailbox=False` only when purely
      informational AND the downstream recovery is reliable. Every
      `mailbox=False` deserves a one-line code comment naming the
      downstream lead-facing surface.
- [ ] **Config surface — decide the knob class first.** Per-teammate
      behavioral knobs (class 4.a) get the full four-fold surface
      (CLI flag + env var + `agents/<name>.json` key +
      capability-manifest field where applicable). Wrapper-process-wide
      mitigation knobs (class 4.b — e.g. `APP_SERVER_INITIALIZE_TIMEOUT_ENV`,
      WAL bloat thresholds) get env-var-only and that's fine. Either
      way: env var name **must** live in `src/claude_anyteam/env.py`,
      not inline in the caller.
- [ ] **Range validation** — bilateral lower AND upper bounds,
      explicit `ValueError`, no `max(min(...))` clamping. Empirical
      or doctrinal grounding in the docstring / RFC.
- [ ] **CHANGELOG `## [Unreleased]`** — extended with the new
      surface under the right Keep-a-Changelog heading.
- [ ] **Tests** — boundary cases (just-inside + just-outside, both
      bounds), default-when-unset, override precedence, every
      failure branch, proof-of-repro + proof-of-fix as tests not
      transcripts.
- [ ] **RFC cross-reference** — if the tunable belongs to the §5.6
      surface table, the RFC table is updated in the same PR (or the
      PR body explains why deviation is acceptable).

---

## 9. Where these conventions were sharpened

Concrete review history — read these threads when you want the
context for *why* a convention exists, not just *what* it is:

- **PR #42** — established the four-leg taxonomy precedent;
  `app_server_initialize_*` is the cleanest implementation to
  imitate.
- **PR #51** (RFC) — §5 integration table; §5.6 four-fold config
  surface lock-step; §5.1 sharpen #2 set the `ERROR_CLASSES=NO`
  precedent for mid-turn signals.
- **PR #52** — `[60, 3600]` range validation precedent for
  `turn_timeout_s`; the `## [Unreleased]` extension convention.
- **PR #54 / #43 follow-up** — sharpens S1 (mailbox flag implicit
  default), S2 (`turn_progress` semantic mismatch), S3 (env vars
  registered in `env.py`), S4 (range validation missing upper
  bound), S6 (failure-branch test coverage), S8 (CHANGELOG entry
  missing).
- **PR #60 / #49** — applied the `ERROR_CLASSES=NO` decision for
  `wrapper_tool_failure_unrecovered` cleanly; reference for how a
  mid-turn signal looks when the four legs are decided correctly.
- **PR #55 / #48** — `spawn_shim.bare_prefix_refused` shape;
  reference for the structured-error-envelope-with-actionable-CTA
  pattern.
- **PR #56 / #40** — `turn_warning(surface=app_server_initialize_retry)`
  cadence + payload; reference for the "instrumented mitigation"
  pattern where the wrapper emits typed events around its own
  retries so the lead can observe them.
- **PR #61 / #7** — fast team-teardown; reference for how
  teardown-class envelopes diverge from turn-class envelopes (they
  often justify `mailbox=False` because the lead initiated the
  action).
- **PR #65 / #54 follow-up** — empirically validated the §4 split
  between class 4.a (per-teammate behavioral knobs, full four-fold
  surface) and class 4.b (wrapper-process-wide mitigation knobs,
  env-var-only acceptable). The three WAL-bloat env vars
  (`*_WARN_THRESHOLD_BYTES`, `*_CHECKPOINT`,
  `*_CHECKPOINT_TIMEOUT_S`) landed env-only with `env.py`
  registration; the initial draft of this doc treated all four
  surfaces as mandatory, but the `APP_SERVER_INITIALIZE_TIMEOUT_ENV`
  precedent and PR #65's clean implementation made the distinction
  explicit.

---

## 10. North-star alignment

These conventions exist because they preserve the three north stars
in `CLAUDE.md`:

- **§1 (harness preservation):** the four-leg taxonomy lets the
  capability layer carry per-backend signals without flattening
  them. New top-level kinds are how heterogeneous harness signals
  reach the lead's event log without being shoehorned into a
  pre-existing envelope's payload.
- **§2 (visibility parity):** the dispatch leg (b) of the taxonomy
  is what makes the difference between "the lead sees a structured
  event in their log" and "the recipient sees prose and has to grep
  stderr." Every envelope skipped on leg (b) widens the visibility
  gap.
- **§3 (peer efficiency):** distinct top-level kinds let peers and
  the lead filter cheaply without parsing prose substrates. The
  `mailbox=False` discipline keeps the inbox high-signal so
  substantive content isn't crowded by informational noise.

When a convention here ever conflicts with a north star, the north
star wins — surface the conflict in the PR body and propose how to
resolve it. The conventions are how the north stars cash out in
day-to-day code; they aren't the source of truth.
