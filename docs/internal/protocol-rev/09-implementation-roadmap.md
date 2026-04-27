# 09 — Implementation Roadmap

**Author:** opus-roadmap
**Date:** 2026-04-27
**Type:** engineering plan — sequenced work items, sizes, dependencies, acceptance criteria
**Companion to:** 07-protocol-spec (canonical wire), 08-elegance-and-gaps (leak ledger + kit), 10-platform-vision (Phase 2 SDK chapter)
**Audience:** engineers shipping v0.7.x; reviewers evaluating scope; future opus-* who extend it.

---

## 0. Executive summary

1. **The roadmap is sequenced as three v0.7.x release blocks plus deferred v0.8.x and Phase-2 kit work.** v0.7.0 ships the transport-layer foundations and the cheap-flag capability declaration; v0.7.1 ships the visibility envelope and peer-fidelity primitives; v0.7.2 ships the reporting kit + Gemini productivity diagnostics. Each block is independently shippable and lead-visible. The roadmap deliberately defers the 200-line SDK kit (08 §6) to Phase 2 and `opus-vision` 10 — until then we ship discrete protocol primitives the kit will eventually formalize.
2. **36 work items (R1–R36) span 33 leaks (L1–L34) + 6 capability declarations (CD-1..CD-6) + the 5 north-star derivatives (W0–W7) + the v0.7.0 backend-neutral watchdog/event-envelope coupling identified in `docs/roadmap.md`.** Each item carries a triple-axis acceptance assessment (preserves / extends / risks-flattening on §1; improves / neutral / regresses on §2; improves / neutral / regresses on §3) so a reviewer can refuse any item that flattens harness capability or widens the visibility gap.
3. **The two-layer split (transport vs capability, CLAUDE.md §1) drives the sequencing.** Transport-layer items (B1 schemas, B2 pydantic default, schema_version, lock hygiene, message_kind discriminator, host-catalog wire alignment) ship first because every capability-layer item assumes a healthy transport. Capability-layer items (Agent Card, peer-prompt fragments, capability-declared steer authorization, manifest broadcast) build on top.
4. **B6 watchdog and B9 envelope ship together as one envelope-driven workstream, not two primitives.** The §1 lens dictates this: a "backend-neutral watchdog" would be a flattener (it would impose the lowest-common-denominator across App Server / ACP / headless). Instead, the soft non-progress watchdog stays Codex-App-Server-only (its native capability) and emits a `turn_progress` envelope; backends without an event-loop transport simply don't declare the capability and emit terminal `turn_completed`/`turn_failed` digests. This is exactly the harness-preservation moat: capability flows through, peers degrade gracefully via the declaration.
5. **B3 reporting kit and B4 Gemini productivity work both reuse the v0.7.1 envelope** rather than inventing parallel diagnostic surfaces. `task_idle_no_tool_calls` and `task_complete_unverified_tool_count` are envelope kinds with `severity=warn`; `claude-anyteam diagnose` reads the event log; `claude-anyteam status` reads the capability roster. Same primitives, no duplication.
6. **The 200-line SDK kit (08 §6) is explicitly NOT a v0.7.x deliverable.** That work belongs to Phase 2 and `opus-vision` 10. We ship the protocol primitives the kit will eventually crystallize (capability declaration, event envelope, message_kind, schema_version, ack_messages helper) so the kit lift is incremental, not a big-bang refactor. References to the codex-prototype's `references/external-claude-code-re/prototype-sdk/` are aspirational — when that prototype lands, R11/R16/R23 may align their internal shapes against it, but they don't depend on it.
7. **Sequencing prioritizes "ship an observable surface every release."** v0.7.0 lands roster `capabilities=` column + host-catalog-aligned shutdown payloads + filelock timeout (small, foundational, lead-visible). v0.7.1 lands `events.jsonl` + Codex App Server tool-event normalization + peer-DM matrix test (the visibility-parity meat). v0.7.2 lands `diagnose`/`status`/`bundle` CLI + Gemini action-first prompt + B4 idle/no-action diagnostics (the productivity meat). Each v0.7.x ships in 1-3 weeks; nothing waits on the kit.
8. **Risk-management posture: minimum viable change per item; no big-bang refactors.** L1 (CodexResult naming), L21 (three Settings classes), L18/L19 (tool-name extraction drift), L24 (test-seam monkeypatch) are deferred to v0.8.x because the rename/refactor risk is high and the kit will absorb them. Items that touch `src/claude_teams/` substrate (R5 shared config lock, R8 `blocked` task status) ship behind feature flags or as additive-only changes; substrate-breaking changes wait for a coordinated Phase-2 cycle.
9. **Concrete dependency edges:** R2 (B2 Fix S pydantic default) is a hard prerequisite for R11 (capability declaration) — adding a new field to TeammateMember without the default risks the same one-bad-sibling-kills-everyone failure on legacy rows. R3 (message_kind discriminator) is a soft prerequisite for R16 (event envelope) — envelope events appearing in the inbox should declare their kind so clients can filter. R10 (schema_version on every payload) is a soft prerequisite for R6 (shutdown_approved/rejected) so the host-catalog migration is versioned. Beyond those four edges, items are largely parallelizable within a release block.
10. **What this roadmap is NOT:** it is not the spec (07), not the leak ledger (08), not the kit chapter (10). It is the engineering sequencing — sizes, dependencies, file:line touch surface, observable acceptance criteria, and the explicit "what we're NOT doing" boundary that prevents scope creep.

---

## 1. Methodology

### 1.1 Sizing scale

- **S** — ≤200 LOC, single module, no protocol changes, no on-disk schema changes. Examples: 1-line pydantic default; classifier branch; CLI subcommand body; prompt-preamble edit; rename within one file with alias for compat.
- **M** — 200–800 LOC, 2–4 modules, may add a helper module, no new on-disk channel. Examples: capability-declaration round-trip across registration + roster + wrapper MCP tool; envelope helper + sink wiring at one backend; CLI subcommand with subcommands and tests; cross-backend prompt template change with three backend wirings.
- **L** — 800+ LOC OR introduces a new on-disk channel/schema/protocol primitive. Examples: append-only event log (`events.jsonl` + reader API + envelope models + per-backend emitters); rich-manifest broadcast cache + invalidation protocol; substrate-breaking change to `src/claude_teams/`.

Sizes are upper-bound estimates including tests but not docs. A v0.7.x release block targets 1 L + 4-6 M + 6-10 S of net-new code.

### 1.2 Acceptance-criteria pattern

Adopting B9 §6.6 observable-outcome style. Each work item declares 2–4 acceptance criteria of the form:

> "A Codex App Server turn that emits `commandExecution` creates a `tool_event` envelope in the event log AND a coalesced task progress update WITHOUT relying on stderr inspection."

Each item is also assessed on three axes:

- **§1 Harness preservation:** *preserves* / *extends* / *risks-flattening*. An item that risks flattening goes to deferred or gets rescoped.
- **§2 Visibility parity:** *improves* / *neutral* / *regresses*. Anything that regresses is rejected by definition (CLAUDE.md §2 lens).
- **§3 Peer efficiency:** *improves* / *neutral* / *regresses*. Same.

### 1.3 Two-layer organization

Per CLAUDE.md §1 ("the protocol is two layers, not one"), every work item is classified as either:

- **Transport** — universal, table-stakes for being a teammate at all (mailbox JSON, atomic task claims, lifecycle, lock hygiene, schema versioning, file shapes).
- **Capability** — per-harness, heterogeneous, declared not flattened (Agent Card, peer-prompt fragments, capability-declared steer authorization, watchdog as Codex-App-Server-only primitive).

Transport-layer items ship first within each release block because capability-layer items assume a healthy transport. Items that mix layers are split into separate Rs.

### 1.4 What this doc is NOT

- **Not the spec.** Wire shapes, payload taxonomy, lock semantics, and ownership matrix live in 07. This doc cites file:line in 07 rather than restating.
- **Not the leak ledger.** L1–L34 + CD-1..CD-6 live in 08 §3–§4. Each R item cites the L/CD it resolves and inherits 08's diagnosis; the work-item entry adds engineering specifics (modules, sizes, sequencing, acceptance) on top.
- **Not the kit chapter.** The 200-line SDK design (08 §6) is the substance of `opus-vision` 10. v0.7.x roadmap items defer kit-shape decisions; they ship discrete primitives that can be lifted into the kit later.
- **Not a substrate spec.** Where a v0.7.x item touches `src/claude_teams/` (R5, R8 if pursued, R6 host-catalog alignment), the change is conservative and additive. Substrate-breaking proposals from 08 (L12 mark-as-read clobber via `messageId`, L28 explicit `role` field, L33 staleness policy) ship as additive-only or as v0.8.x items behind explicit migration evidence.

---

## 2. Release-block sequencing

### 2.1 v0.7.0 — Transport foundation + capability declaration MVP

Goal: every routed teammate (Codex App Server / Codex exec / Gemini ACP / Gemini headless / Kimi headless) has a host-catalog-aligned wire surface, a capability declaration visible in `team-roster`, and substrate hygiene that prevents the foundation cracks 08 P1 enumerated.

| Item | Layer | Goal | Size |
|---|---|---|---|
| R1 | transport | Bundle JSON schemas as importlib package resources (B1). | S |
| R2 | transport | Pydantic default for `agent_type` (B2 Fix S). | S |
| R3 | transport | Typed `message_kind` discriminator on `InboxMessage` (W6). | S |
| R4 | transport | Filelock 30s timeout + staleness logging (L33). | S |
| R5 | transport | Shared config lock between vendored writers + adapter registration (L27, P1). | M |
| R6 | transport | `shutdown_approved` / `shutdown_rejected` distinct types matching host catalog (W0, L25-bis). | S |
| R7 | transport | `backend_exit_code` + `backend` field on TaskCompleteOut/TaskBlockedOut (L7). | S |
| R8 | transport | `TaskBlockedOut` Pydantic model (L10 partial; first-class `blocked` status deferred). | S |
| R10 | transport | `schema_version: 1` on every typed payload (L26). | S |
| R11 | capability | Agent Card cheap-flags layer: `members[].capabilities` flat list at registration; `team-roster` capabilities column (W1, CD-1..CD-6 cheap-flag half). | M |

Total: 1 M + 9 S, ~1500 LOC including tests.

### 2.2 v0.7.1 — Visibility envelope + peer fidelity

Goal: lead can observe routed-teammate host-tool activity, mid-turn progress, and turn outcomes without reading stderr; peer-DM works uniformly across all five backend instances; capability-declared steer authorization replaces hard-coded lead-only.

| Item | Layer | Goal | Size |
|---|---|---|---|
| R16 | transport | `VisibilityEvent` envelope models + `protocol_io.append_event()` + `read_events()` + `events/<agent>.jsonl` channel under `events/.lock` (B9 §6.4, L17). | L |
| R17 | capability | Codex App Server tool-event normalization: extend `_record()` to emit `commandExecution`/`fileChange`/`webSearch`/`agentMessage`-delta envelopes (L17, B9 §6.5 step 1). | M |
| R18 | capability | Wrapper shadow-tool instrumentation: decorator on every `EXPOSED_TOOLS` handler emits `tool_event` envelopes (L17-bis, B9 §6.5 step 2). | M |
| R19 | capability | Headless terminal digests: Gemini/Kimi `invoke.py` emit `turn_started` + `turn_completed`/`turn_failed` envelopes after `subprocess.run` exits (L17-ter, B9 §6.5 step 3). | S |
| R20 | capability | Soft non-progress watchdog (Codex App Server only) emits as `turn_progress(severity=warn)` envelope; opt-in hard interrupt as separate config knob (B6, B9 §5). | M |
| R12 | capability | Eager capability-manifest broadcast: per-teammate JSON cache under `teams/<team>/manifests/<agent>.json`; version-bump invalidation via `capability_changed` envelope (W5, CD-1..CD-6 rich-manifest half). | M |
| R13 | capability | New wrapper MCP tool `mcp_anyteam_capability_manifest(agent_name, capability?)` reading from the local manifest cache (08 §6.4.1). | S |
| R14 | capability | Peer-capability prompt fragments: each backend's `prompts.py` reads peer manifests at task-prompt-build time and injects per-peer `description`/`when_to_use`/`when_not_to` paragraphs (W2, 08 §6.4 `peer_prompt_fragments_for`). | M |
| R15 | capability | Capability-declared steer authorization: Gemini/Kimi `_handle_steer` and Codex `SteerQueue.push` honor recipient's `accepts_peer_steer` capability flag; rejection emits `visibility_degraded(surface=peer_steer_rejected)` envelope (W4, CD-6). | S |
| R21 | capability | Wrapper MCP peer-DM consistency audit: matrix integration test (5 backend instances × every peer) asserting `send_message(to=<peer>, ...)` lands in the peer's inbox (W3). | S |
| R22 | transport | Audit Gemini PR-#11/#12 `delivered_via_tool` guard (per 08 L6 it's already shipped; verify and close) (W7). | S |
| R23 | transport | Extract shared `_should_skip_prose_fallback(result)` helper to `protocol_io`; remove duplication across three loops (L6). | S |

Total: 1 L + 5 M + 6 S, ~3500 LOC.

### 2.3 v0.7.2 — Reporting kit + productivity diagnostics

Goal: a lead investigating a routed-teammate failure has a single CLI path to the diagnostic data; Gemini executor productivity recovers via action-first prompts and idle/no-action envelopes.

| Item | Layer | Goal | Size |
|---|---|---|---|
| R26 | transport | Audit `claude-anyteam diagnose` CLI (already partial via `diagnose_cli.py`); add `--tail`, `--team`, `--agent` filters and per-incident JSON dump (B3 §5.1). | S |
| R27 | transport | `team-roster` resolved-config view: `adapter_model`, `effort`, `source` columns + `--no-resolve` flag (B3 §5.5). | S |
| R28 | transport | Audit `claude-anyteam status` CLI (already partial via `status_cli.py`); add per-team table with health status and `last_seen` (B3 §5.4). | S |
| R29 | transport | `claude-anyteam bundle` incident-tar.gz with redaction (B3 §5.3). | M |
| R30 | docs | `bug-triage/REPORT_TEMPLATE.md` template (B3 §5.6). | S |
| R31 | transport | `team-roster --health` HEALTH column showing config validation outcome + missing-field repair hint (B2 D2). | S |
| R32 | capability | Gemini ACTION_FIRST_EXECUTION prompt preamble + Kimi equivalent (B4 §3A). | S |
| R33 | capability | `task_idle_no_tool_calls` + `task_complete_unverified_tool_count` envelope kinds for Gemini AND Kimi (B4 §3C, B9 §2.4). | M |
| R34 | capability | Route action-looking DMs as task steers (consume `TaskAssignmentIn` in Gemini/Kimi `_handle_message`; queue as `QueuedSteer` instead of prose-reply) (B4 §3B). | M |
| R35 | transport | `team-agent` warning for old Gemini models (heuristic: model not matching `^gemini-3.*`); roster `--diagnose` flag flags higher no-action risk (B4 §6). | S |
| R36 | capability | Default Gemini backend headless→ACP after A/B harness ships acceptance (B4 §3D, conditional on R32+R33 evidence). | S |
| R9 | transport | After 2 releases of deprecation warnings, drop legacy `text == "steer:..."` shorthand parser (L11). | S |

Total: 3 M + 9 S, ~2200 LOC.

### 2.4 v0.8.x deferred — substrate cleanup and refactor

Items that touch large surface area or assume the kit (08 §6) will absorb them. Each is sized M+ and would individually delay a v0.7.x ship.

| Item | Source | Reason for deferral |
|---|---|---|
| D1 | L1 | Rename `CodexResult` → `BackendResult`/`TaskInvokeResult`; touches every backend import + tests. Kit's `TaskResult` (08 §6.5) absorbs naturally. |
| D2 | L21 | `TeammateSettings` base class + per-backend extensions; touches three Settings modules + every CLI/env precedence. Kit's `TeammateSettings` (08 §6) is the canonical version. |
| D3 | L18, L19 | Unified tool-name extraction taxonomy; subsumed by R17/R18 envelope work but full normalization across all backends is kit-scope. |
| D4 | L20 | Wrapper `read_inbox` mutation footgun: rename to `read_inbox_unread` or split into non-mutating + `mark_inbox_read(ids)`. Touches model-facing tool surface; defer until kit's `Storage.ack_messages(ids)` lands. |
| D5 | L22 | `JsonRpcStdioClient.handle_server_request` per-method registration. Touches `jsonrpc_stdio.py` + `acp_client.py`; kit's per-method handler abstraction is cleaner. |
| D6 | L23 | `app_server.thread_start_shape` `visibility_degraded` emission. Trivial but better folded into kit's emit_* primitives once they land. |
| D7 | L24 | Class-based test seam (replace module-level `invoke = headless_invoke` aliases). Kit's runner-instance overrideable `invoke()` is the canonical fix. |
| D8 | L25-bis (host) | `backendType="external-cli"` value. Outside our control — host-side change. Document in 07 §10.4 as upstream ask. |
| D9 | L29 | Classify wrapper `task_get` exposure (in EXPOSED_TOOLS or BLOCKED_TOOLS). One-line; rolled into R18 if convenient, otherwise defer. |
| D10 | L30 | Codex `minimal` effort tier alignment: either add Codex `minimal` mapping or amend protocol enum to four tiers. Touches `config.py:129-134` + docs; defer to v0.7.1 or v0.7.2 depending on engineering bandwidth. **Soft-promote candidate** — small enough to land in any release. |
| D11 | L34 | `<system_reminder>` body mutation drift between vendored server and wrapper. Touches `src/claude_teams/server.py:253-261` (substrate); requires substrate spec change. Defer until Phase-2 cross-impl contract test (07 §10.8) gives the migration window. |
| D12 | L11-bis | `kind` vs `type` discriminator unification on `TaskCompleteOut`/`TaskBlockedOut`. Cosmetic; touches every reader. Rolled into Phase-2 wire-cleanup pass. |

### 2.5 Phase 2 — Kit and platform vision

| Item | Source | Notes |
|---|---|---|
| K1 | 08 §6.2 | `TeamStorage` Protocol with filesystem default; Redis/SQLite/in-memory ports. |
| K2 | 08 §6.3 | `Teammate` base class + lifecycle handling. |
| K3 | 08 §6.4 | `Team` class with `find_capability` + `peer_prompt_fragments_for`. |
| K4 | 08 §6.5 | 200-line adapter rewrite (Codex / Gemini / Kimi reduce to ~200 LOC each on top of the kit). |

These are `opus-vision` 10's chapter substance. v0.7.x roadmap items keep kit-shape decisions OUT of scope so the eventual lift is incremental.

---

## 3. Per-work-item details

Every R item below carries: goal (1 line), prerequisites, modules touched (file:line where known), acceptance criteria (B9 §6.6 style), size, triple-axis assessment (§1 / §2 / §3), risk + evidence-of-trouble.

### R1 — Bundle JSON schemas as package resources (B1)

**Goal.** Make `task-complete.schema.json`, `plan.schema.json`, `permission_request.schema.json`, `permission_response.schema.json` first-class wheel artifacts; remove the `parent.parent.parent` path bug.

**Prereqs.** None.

**Modules.**
- `src/claude_anyteam/codex.py:47-49` — replace `parent.parent.parent` with `importlib.resources.files("claude_anyteam.schemas")`.
- `src/claude_anyteam/schemas/__init__.py` — new (already exists per file listing; verify).
- `src/claude_anyteam/schema_validation.py` — accept `Traversable` as well as `Path`.
- `pyproject.toml:45-46` — confirm wheel includes `src/claude_anyteam/schemas/*.json`.
- `src/claude_anyteam/installer.py` — add asset check before declaring success.
- `tests/test_packaged_schemas.py` (new) — assert all four schemas resolve via `importlib.resources`.

**Acceptance.**
- A fresh `uv tool install claude-anyteam` install completes a Codex `--output-schema task-complete.schema.json` invocation without manual file copying.
- `python -m zipfile -l dist/claude_anyteam-*.whl | grep 'claude_anyteam/schemas/'` lists all four schema files.
- Removing `~/.local/share/uv/tools/claude-anyteam/lib/python3.12/schemas/` and re-running a teammate task succeeds.

**Size.** S.

**Axes.** §1 preserves; §2 improves (failed-install errors stop being mysterious); §3 neutral.

**Risk.** Low. The fallback to `~/.claude/plugins/marketplaces/claude-anyteam/schemas/` already exists as a workaround; the package-resource path is purely additive. Evidence-of-trouble: first-task schema-validation failures in CI on a fresh-installed wheel.

---

### R2 — Pydantic default for `agent_type` (B2 Fix S)

**Goal.** Stop one bad sibling member from breaking `read_config` for the entire team.

**Prereqs.** None.

**Modules.**
- `src/claude_teams/models.py:35` — `agent_type: str = Field(alias="agentType", default="claude-anyteam")`.
- `src/claude_teams/models.py:22` — same for `LeadMember` with default `"team-lead"`.
- `tests/protocol/test_models.py` — coverage for missing-`agentType` round-trip.

**Acceptance.**
- A team config where one member entry omits `agentType` validates; `wrapper_server.send_message` succeeds for all other teammates.
- The vendored substrate's `add_member()`/`read_config()` still validate normal cases identically.

**Size.** S (1 line + 1 line + tests).

**Axes.** §1 preserves; §2 improves (silent peer-DM failures stop); §3 improves (peer-to-peer messaging stops collapsing under one bad row).

**Risk.** Diverges vendored `claude_teams` from upstream cs50victor. Mitigations: surgical 1-line diff; documented in commit message; consider proposing upstream as "optional with sane default."

---

### R3 — Typed `message_kind` discriminator on `InboxMessage` (W6)

**Goal.** Consumers filter inbox messages by kind without parsing JSON inside `text`.

**Prereqs.** None.

**Modules.**
- `src/claude_teams/models.py` (substrate) — add `message_kind: str = Field(default="peer_dm", alias="messageKind")` to `InboxMessage`. Backwards-compatible: missing values fall through to `peer_dm`.
- `src/claude_anyteam/protocol_io.py` — set `message_kind` on every `send_*` helper (`send_idle_notification` → `"heartbeat"`; `send_task_complete` → `"task_complete"`; `send_task_blocked` → `"task_blocked"`; `send_permission_request_to_lead` → `"permission_request"`; `send_plan_approval_request` → `"plan_approval_request"`; `send_shutdown_response` → `"shutdown_response"`; `send_json_to_lead`/`send_prose` accept an explicit kind).
- `src/claude_anyteam/wrapper_server.py:178-243` — `send_message` accepts and forwards `message_kind` (default `"peer_dm"`).
- `src/claude_anyteam/loop.py` — inbox poll filters can opt out of `heartbeat` for high-frequency loops.
- Tests: round-trip kind preservation across substrate writers.

**Initial closed taxonomy.** `peer_dm`, `heartbeat`, `task_assignment`, `task_complete`, `task_blocked`, `shutdown_request`, `shutdown_response`, `shutdown_approved`, `shutdown_rejected`, `plan_approval_request`, `plan_approval_response`, `permission_request`, `permission_response`, `steer`, `capability_manifest_updated`, `visibility_event` (umbrella for envelope kinds when they appear in the inbox).

**Acceptance.**
- A consumer reading `~/.claude/teams/<team>/inboxes/team-lead.json` can filter for `message_kind != "heartbeat"` and skip 60s idle pings without parsing `text`.
- All existing `send_*` helpers in `protocol_io.py` write the correct kind; tests assert.
- An old client (no `message_kind` field) round-trips through `read_inbox`/`mark_as_read` without dropping the field.

**Size.** S.

**Axes.** §1 preserves; §2 improves (lead can build idle-vs-substantive dashboards trivially); §3 improves (CLAUDE.md §3 explicit failure mode "Idle noise crowding out signal").

**Risk.** L12 (mark-as-read clobber via Pydantic strip) compounds: any new top-level `InboxMessage` field will be preserved on round-trip only because `extra="ignore"` semantics keep it in the model. Verify this empirically before ship; if Pydantic drops the field, fall back to ack-by-`messageId` (the proper L12 fix) instead — that promotes R3 from S to M.

---

### R4 — Filelock 30s timeout + staleness logging (L33)

**Goal.** A crashed teammate holding the inbox or task lock cannot deadlock the next adapter startup.

**Prereqs.** None.

**Modules.**
- `src/claude_teams/_filelock.py:9-12` — wrap `filelock.FileLock(str(lock_path))` with 30s default timeout via `acquire(timeout=30.0)`.
- On timeout, log `lock.stale_suspected` with the lock path and the holder's PID (recoverable from `/proc/locks` on Linux; best-effort on macOS).
- Force-acquire via `os.unlink(lock_path)` with a one-line warning, then retry once.
- Tests: simulate stale lock (write the lockfile but no holder); assert recovery.

**Acceptance.**
- An adapter started while a `.lock` exists with no live holder acquires within 35s and logs `lock.stale_suspected`.
- Two simultaneous well-behaved acquirers serialize; neither sees the staleness path.
- A live holder past 30s causes an explicit `LockTimeout` error, NOT a hang or silent force-acquire.

**Size.** S.

**Axes.** §1 preserves; §2 improves (failures become loud); §3 improves (stuck teammate doesn't stall the team).

**Risk.** Force-acquire could corrupt a write in progress on a slow filesystem (NFS). Mitigation: document that the 30s default is overrideable per-call; recommend leaving force-acquire OFF in production and using only the explicit `LockTimeout`.

---

### R5 — Shared config lock between vendored writers and adapter registration (L27)

**Goal.** Stop concurrent vendored `add_member`/`remove_member` from racing adapter `register()`.

**Prereqs.** R4 (filelock timeout) — so we have a sane lock primitive.

**Modules.**
- `src/claude_teams/teams.py:158-172` — `add_member()`/`remove_member()` take `~/.claude/teams/<team>/config.lock` for the read-modify-write window.
- `src/claude_anyteam/registration.py:58-69` — `register()` acquires the same `config.lock` instead of `inboxes/.lock` for config writes.
- Tests: stress test (40 concurrent `add_member` + `register` cycles) asserts no lost updates.

**Acceptance.**
- Concurrent `add_member` + `register` on the same team produce a config with both members present (no lost update).
- The lock file lives at `~/.claude/teams/<team>/config.lock` and is shared by every config write path.
- `team-roster` after the stress test shows the expected member count.

**Size.** M (~300 LOC including stress test fixture).

**Axes.** §1 preserves; §2 neutral; §3 improves (config races corrupt teammate state silently).

**Risk.** Touching `src/claude_teams/` substrate. Mitigation: additive lock (no schema change); existing callers unaffected; vendored upstream divergence documented.

---

### R6 — `shutdown_approved` / `shutdown_rejected` distinct types matching host catalog (W0, L25-bis)

**Goal.** Adapter emits the host-canonical wire shape so future `isStructuredProtocolMessage` enforcement doesn't reject our payloads.

**Prereqs.** R10 (schema_version) — so the migration is versioned.

**Modules.**
- `src/claude_anyteam/messages.py:58-69` — split `ShutdownResponseOut` into `ShutdownApprovedOut` (`type="shutdown_approved"`) and `ShutdownRejectedOut` (`type="shutdown_rejected"`); keep `ShutdownResponseOut` as a deprecated alias that maps to the appropriate split type.
- `src/claude_anyteam/protocol_io.py:185-198` — `send_shutdown_response(approve, ...)` dispatches to the new shape.
- `src/claude_anyteam/loop.py` — handler unchanged (still emits via the helper).
- Tests: round-trip both types; assert the legacy `shutdown_response` emit produces a deprecation warn-log; assert host-binary fragment recognition (cite codex-extract 03 §"Mailbox protocol messages").

**Acceptance.**
- An adapter approving shutdown emits `{"type": "shutdown_approved", ...}`; rejecting emits `{"type": "shutdown_rejected", ...}`.
- The host's `isStructuredProtocolMessage` recognizer (per codex-extract 03 catalog) accepts both.
- Backward compatibility: an adapter that still emits `shutdown_response` produces a warn-log; consumers (lead, peer) handle all three types.

**Size.** S.

**Axes.** §1 preserves; §2 improves (host UI rendering is now correct); §3 neutral.

**Risk.** Existing leads in production may expect `shutdown_response`; the codex-extract 03 catalog says the host accepts the host-canonical names but doesn't confirm that `shutdown_response` is also accepted forever. Mitigation: keep the legacy emit path for one release; assert host catalog acceptance via extracted-binary fragment grep.

---

### R7 — `backend_exit_code` + `backend` field on TaskCompleteOut/TaskBlockedOut (L7)

**Goal.** `task_complete` payloads from Gemini/Kimi stop carrying a misleading `codex_exit_code` field.

**Prereqs.** R10 (schema_version).

**Modules.**
- `src/claude_anyteam/messages.py:142-154` — `TaskCompleteOut` adds `backend: str` and `backend_exit_code: int = Field(alias="codex_exit_code")` for compat. (Pydantic `populate_by_name=True` already enables alias-or-name population.)
- `src/claude_anyteam/protocol_io.py:219-233` — `send_task_complete(... backend=...)` accepts and forwards.
- `src/claude_anyteam/loop.py` (Codex), `src/claude_anyteam/backends/gemini/loop.py:541`, `src/claude_anyteam/backends/kimi/loop.py:496` — pass backend identifier (`"codex"` / `"gemini"` / `"kimi"`) at construction.
- Tests: assert outbound payload carries both fields; consumer reading the legacy `codex_exit_code` field still works.

**Acceptance.**
- A Gemini `task_complete` payload contains `"backend": "gemini"` AND `"backend_exit_code": 0` (and, for one-release-compat, `"codex_exit_code": 0` as alias).
- The lead UI rendering `codex_exit_code` continues to display correctly.
- Adding a new backend (GLM, DeepSeek) extends the field with the correct backend tag with one constructor arg, no schema change.

**Size.** S.

**Axes.** §1 preserves (each harness's identity is now visible on its own outbound); §2 improves (lead can disambiguate); §3 neutral.

**Risk.** Old leads/dashboards keying on `codex_exit_code` only. Mitigation: alias keeps it working for one release; deprecation warn-log in v0.7.1.

---

### R8 — `TaskBlockedOut` Pydantic model (L10 partial)

**Goal.** `task_blocked` payloads are typed, not raw dicts; first-class `blocked` task status deferred per L10 §3.3.

**Prereqs.** R10 (schema_version), R7 (backend field).

**Modules.**
- `src/claude_anyteam/messages.py` — add `TaskBlockedOut` next to `TaskCompleteOut`. Fields: `kind` (or `type`, see L11-bis — pick `type`), `task_id`, `reason`, `backend`, `error_class` (optional, ties to diagnostics), `incident_id` (optional, ties to diagnostics).
- `src/claude_anyteam/protocol_io.py:201-216` — `send_task_blocked()` constructs the model and routes through `send_json_to_lead`.
- `src/claude_anyteam/loop.py` — `_mark_blocked` passes `error_class` + `incident_id` from `diagnostics.classify_failure(result)`.
- Tests: round-trip; assert raw-dict-emission test from B3 covers the new typed shape.

**Acceptance.**
- A `task_blocked` payload contains `error_class` + `incident_id` so the lead can `claude-anyteam diagnose --incident <id>` without searching prose.
- `TaskBlockedOut.model_validate({...})` accepts the legacy raw-dict shape (backward-compat).

**Size.** S.

**Axes.** §1 preserves; §2 improves (B3 reporting kit becomes a structured surface); §3 neutral.

**Risk.** Scope creep into "first-class `blocked` task status." Explicitly out of scope for R8 — that requires substrate change in `claude_teams/models.py:82-87` (status enum) plus host coordination. Park as deferred.

**Deferred sub-item (D8a, v0.8.x):** `blocked` as first-class task status. Cite L10 / 07 §1.4. Requires host-binary acceptance check and substrate spec change.

---

### R9 — Drop legacy `text == "steer:..."` shorthand parser (L11)

**Goal.** Single canonical wire form for `steer`; legacy parser removed after deprecation.

**Prereqs.** Two prior releases of `parse_protocol_text` emitting deprecation warn-log on the legacy form.

**Modules.**
- `src/claude_anyteam/messages.py:164-165` — drop the `text[:6].lower() == "steer:"` branch.
- `team-cli` — emit only the JSON form.
- Tests: assert the canonical JSON form parses; assert legacy text is no longer recognized.

**Acceptance.**
- A `text == "steer: foo"` message lands as prose (kind `peer_dm` per R3) instead of being interpreted.
- All call sites in `team-cli` emit JSON.

**Size.** S.

**Axes.** §1 preserves; §2 neutral; §3 neutral.

**Risk.** Users with shell aliases or scripts emitting the legacy form. Mitigation: the deprecation warn-log in earlier releases gives users two cycles to migrate; document in CHANGELOG.

**Sequencing.** Ships in v0.7.2 only if v0.7.0 + v0.7.1 both ship the deprecation warn-log. Otherwise defer one release.

---

### R10 — `schema_version: 1` on every typed payload (L26)

**Goal.** Wire envelopes carry an explicit version so future readers can hard-fail on unsupported versions and the spec migration is auditable.

**Prereqs.** None.

**Modules.**
- `src/claude_anyteam/messages.py:26-27` — `_Base` adds `schema_version: int = 1`.
- All outbound payloads inherit; absent on inbound = 1 (Pydantic default).
- `protocol_io.send_json_to_lead` — confirm `model_dump_json` includes the field (it should by default).
- Tests: every typed model round-trips with `schema_version: 1`.

**Acceptance.**
- Every JSON payload emitted to the inbox carries `"schema_version": 1`.
- A consumer reading a payload with a higher `schema_version` than supported can hard-fail loudly (recommendation, not enforced this release).
- Inbound payloads without `schema_version` validate as version 1.

**Size.** S.

**Axes.** §1 preserves; §2 neutral; §3 neutral. (Foundation for §1/§2/§3 long-term — Phase 2 spec evolution requires this.)

**Risk.** Adds one field to every outbound payload (~10 bytes). Negligible.

---

### R11 — Agent Card cheap-flags layer at registration (W1, CD-1..CD-6 cheap-flag half)

**Goal.** Every routed teammate writes a flat `capabilities` list to its `members[].capabilities` row at registration; `team-roster` shows it; peers can discover capabilities without spawning the wrapper MCP.

**Prereqs.** R2 (pydantic default `agent_type` — same vulnerability for any new field on `TeammateMember`).

**Modules.**
- `src/claude_teams/models.py` — add `capabilities: list[str] = Field(default_factory=list)` to `TeammateMember`.
- `src/claude_anyteam/registration.py:96-187` — `register()` (and self-heal path) writes the list per-backend.
- `src/claude_anyteam/codex.py` — Codex App Server registers `["turn_steer", "thread_fork", "live_tool_events", "structured_output", "soft_non_progress_watchdog", "host_tool_surface:codex-native"]`; Codex exec registers `["structured_output"]` only.
- `src/claude_anyteam/backends/gemini/loop.py` — Gemini ACP registers `["turn_steer", "permission_bridge", "live_tool_events", "structured_output", "host_tool_surface:mcp_anyteam"]`; Gemini headless `["structured_output", "host_tool_surface:mcp_anyteam"]`.
- `src/claude_anyteam/backends/kimi/loop.py` — Kimi headless registers `["large_context", "host_tool_surface:kimi-native"]`.
- `src/claude_anyteam/team_cli.py:331-381` — `team-roster` adds `capabilities=` column; `--capabilities` flag prints the full list per row.
- Tests: each backend's `register()` writes the expected list; legacy rows without `capabilities` validate (default empty list); `team-roster` formatting.

**Initial closed taxonomy** (transport-side cheap flags; semantic guidance lives in the rich manifest per R12-R13):

| Flag | Meaning |
|---|---|
| `turn_steer` | adapter accepts mid-turn or next-turn `SteerIn` |
| `thread_fork` | adapter supports cross-task memory continuation (Codex App Server only) |
| `permission_bridge` | adapter forwards `permission_request` to lead and waits for `permission_response` (Gemini ACP only) |
| `live_tool_events` | adapter emits `tool_event` envelopes mid-turn (Codex App Server, Gemini ACP) |
| `structured_output` | adapter validates schema-constrained final outputs (all backends) |
| `large_context` | adapter accepts >100k token context windows (Kimi) |
| `accepts_peer_steer` | adapter honors `steer` messages from non-lead senders (default false; opt-in per R15) |
| `soft_non_progress_watchdog` | adapter self-monitors for non-progress and emits `turn_progress(severity=warn)` (Codex App Server only) |
| `host_tool_surface:<kind>` | identifies the harness's host-tool surface (`codex-native` / `mcp_anyteam` / `gemini-native` / `kimi-native`) |

**Acceptance.**
- `claude-anyteam team-roster --team <T>` shows a `capabilities=` column listing each routed teammate's flat capability list.
- A team config with one routed teammate AND one native-claude teammate shows the routed teammate's capabilities and the native one's empty (or host-supplied; phase 2 might fill this in).
- Adding a new backend extends the taxonomy via one constant + one register() wiring; no schema change to `claude_teams/models.py`.
- A v0.6.x adapter (no `capabilities` field) registers and the team config validates (default empty list).

**Size.** M (~400 LOC including 4 backend wirings + roster column + tests).

**Axes.** §1 *extends* (this IS the §1 obligation; harness uniqueness becomes legible); §2 improves (lead can route based on capability); §3 improves (peers can query before invoking).

**Risk.** One bad sibling row missing `capabilities` would normally break `read_config` for the whole team — R2 prevents this for the legacy `agent_type` case; the same default-factory pattern protects `capabilities`. Verify in tests.

**Cross-ref.** The cheap-flags layer is the W1 / 08 §1.7 transport-layer contribution. The rich manifest (per-capability `description`, `when_to_use`, `failure_modes`) lives in R12-R13 and is exposed via wrapper MCP.

---

### R12 — Eager capability-manifest broadcast at team formation (W5, CD-1..CD-6 rich-manifest half)

**Goal.** Every peer's rich Agent Card manifest is cached locally per teammate so peer-to-peer invocation costs zero round-trip.

**Prereqs.** R11 (cheap flags exist).

**Modules.**
- `src/claude_anyteam/registration.py` — `register()` writes own manifest to `~/.claude/teams/<team>/manifests/<agent>.json` (atomic temp+rename).
- `src/claude_anyteam/messages.py` — new `CapabilityManifestUpdatedOut` envelope (kind `capability_manifest_updated` per R3 taxonomy) carries `agent_name`, `capability_version`, `manifest_path`.
- `src/claude_anyteam/wrapper_server.py` — at startup, scan `~/.claude/teams/<team>/manifests/*.json` and load into in-memory cache; on `capability_manifest_updated` event arrival, invalidate and reload the affected entry.
- Tests: integration test asserts a peer-to-peer capability invocation completes with zero `manifests/` file reads in the trace (cache hit) AND that bumping a teammate's `capability_version` triggers cache invalidation in all peers within one inbox-poll cycle.

**Manifest shape (per CD-1..CD-6 in 08 §4):** the per-teammate JSON contains a flat dict of capability name → `{version, schema, description, when_to_use, when_not_to, failure_modes, ...optional sub-fields}`. Each adapter's per-backend `agent_card()` method (or equivalent constant) is the source of truth; the registration path serializes it to disk.

**Acceptance.**
- After `register()` completes, `~/.claude/teams/<team>/manifests/<agent>.json` exists with `schema_version: 1`, the harness identity, and the full capability manifest.
- A peer's wrapper MCP server, started after a `capability_manifest_updated` event arrives in its inbox, has the new version cached without round-trip.
- Removing a teammate (deregistration) deletes its manifest file.

**Size.** M (~500 LOC including the cache-invalidation event flow + tests).

**Axes.** §1 *extends*; §2 improves; §3 improves (CLAUDE.md §3 explicit failure mode "Capability discovery latency").

**Risk.** Manifest files accumulate on long-running sessions. Mitigation: deregistration cleans up; lifecycle owner (R5 self-heal also handles stale manifest detection at startup). Open question: rotation policy if a teammate's manifest grows unboundedly — defer to L33-style staleness handling.

---

### R13 — Wrapper MCP `mcp_anyteam_capability_manifest` tool (08 §6.4.1)

**Goal.** A backend can fetch a peer's rich manifest via MCP without direct file access.

**Prereqs.** R12 (the local cache the tool reads from).

**Modules.**
- `src/claude_anyteam/wrapper_server.py:54-68` — add `mcp_anyteam_capability_manifest` to `EXPOSED_TOOLS`. Tool signature: `(agent_name: str, capability: str | None = None) -> dict`.
- Implementation: read from local cache (R12). If `capability` is None, return all entries; else return one entry.
- Tests: tool returns the cached manifest; tool errors with descriptive message if `agent_name` is not a team member; tool errors gracefully if cache is empty (recommend `read_config` to discover roster first).

**Acceptance.**
- A Codex teammate calls `mcp_anyteam_capability_manifest("gemini-bob", "permission_bridge")` and receives the rich entry with `schema`, `when_to_use`, `failure_modes`.
- The tool round-trips through Codex App Server / Gemini ACP / Gemini headless / Kimi headless wrappers identically.
- Calling the tool with an unknown `agent_name` returns a clear error mentioning `read_config()` for discovery.

**Size.** S.

**Axes.** §1 *extends*; §2 neutral; §3 improves.

**Risk.** Wrapper tool count grows from 13 to 14 (08 §3.5 L29 already flags `task_get` as unclassified — bundle that classification with this PR for symmetry).

---

### R14 — Peer-capability prompt fragments (W2, 08 §6.4)

**Goal.** Each backend's task prompt includes a paragraph per peer capability so the model knows HOW (not just WHETHER) to invoke peer features.

**Prereqs.** R12 (local cache), R13 (MCP tool, optional — direct cache read works too).

**Modules.**
- `src/claude_anyteam/prompts.py` (Codex) — add `peer_capability_section(team)` that reads the local manifest cache, emits one block per (peer × capability) pair containing `description` + `when_to_use` + `when_not_to` + `failure_modes` summary.
- `src/claude_anyteam/backends/gemini/prompts.py` — same.
- `src/claude_anyteam/backends/kimi/prompts.py` — same.
- The fragment is gated on `callable_from_peers: true` per CD entry (a sub-field on the manifest; default false unless explicitly opted in by the capability author). For initial taxonomy: `permission_bridge` is `callable_from_peers: false` (lead-only); `turn_steer` honors `accepts_peer_steer` flag; `thread_fork` is callable from peers (Codex App Server can fork on a peer's request); `host_tool_surface` is read-only informational.
- Tests: a Codex task prompt in a team containing `gemini-acp-bob` includes a sentence about Gemini's permission bridge if the capability is callable; the same in reverse for Codex's `thread_fork` exposed to Gemini.

**Acceptance.**
- A Codex task prompt that runs in a team with one Gemini ACP teammate and one Kimi teammate includes per-peer capability fragments with `when_to_use` guidance.
- Removing the Gemini teammate (deregistration → manifest deleted → cache invalidated via R12) results in the next Codex task prompt no longer mentioning Gemini's capabilities.
- A capability with `callable_from_peers: false` is omitted from peer prompts.

**Size.** M (~300 LOC across three prompts.py modules + tests for cross-backend prompt content).

**Axes.** §1 *extends* (this IS the §1 "teach peers how to invoke" obligation); §2 neutral; §3 improves.

**Risk.** Prompt bloat — a team of 10 teammates each declaring 5 capabilities could add 50 paragraphs to every task prompt. Mitigation: cap at 3 fragments per peer (longest `when_to_use`); add `--no-peer-fragments` opt-out for token-budget-constrained teammates.

---

### R15 — Capability-declared steer authorization (W4, CD-6)

**Goal.** A peer can steer another peer when the recipient's manifest declares `accepts_peer_steer: true`; rejection is a structured `visibility_degraded` envelope, not a hang.

**Prereqs.** R11 (capabilities in manifest), R16 (envelope models).

**Modules.**
- `src/claude_anyteam/backends/gemini/loop.py:213-214` — replace lead-only check with: `if sender == "team-lead" or recipient_manifest.get("accepts_peer_steer", False): accept; else emit visibility_degraded(surface="peer_steer_rejected", reason="accepts_peer_steer_false")`.
- `src/claude_anyteam/backends/kimi/loop.py:185-186` — same.
- `src/claude_anyteam/codex.py` `SteerQueue.push` — same check (Codex App Server's existing acceptance is unintentional per CD-6; align to the new rule).
- Tests: a Codex executor steering a Codex researcher with `accepts_peer_steer: true` succeeds; same with `false` produces a `visibility_degraded` envelope in the lead's mailbox.

**Acceptance.**
- Codex executor `send_message(to="codex-researcher", body="steer: try X instead")` lands in the researcher's `SteerQueue` if `codex-researcher` declares `accepts_peer_steer: true`.
- The same with `false` emits `visibility_degraded(surface=peer_steer_rejected, reason=accepts_peer_steer_false, sender=codex-executor, recipient=codex-researcher)` to lead's mailbox; the executor's send returns success (the steer was accepted by the wrapper; rejection is on the recipient side).
- A native Claude teammate (no manifest, no `accepts_peer_steer` declared) rejects peer steers by default per the closed-taxonomy rule.

**Size.** S.

**Axes.** §1 *extends* (capability-declared rather than hardcoded policy); §2 improves (rejections are visible diagnostics); §3 *improves* (CLAUDE.md §3 explicit failure mode "Steer authorization too narrow").

**Risk.** Adapters that aren't using R16 envelope yet need a fallback rejection path. Mitigation: emit a plain inbox message in the interim; full envelope flows through after R16 lands.

---

### R16 — `VisibilityEvent` envelope + `events.jsonl` channel (B9 §6.4, L17)

**Goal.** A new append-only event log per teammate that carries the v2 visibility envelope; foundational for R17/R18/R19/R20.

**Prereqs.** R3 (message_kind discriminator — envelope events that also appear in mailbox declare `kind=visibility_event`), R4 (lock with timeout — needed for `events/.lock`).

**Modules.**
- `src/claude_anyteam/messages.py` — Pydantic models for `VisibilityEvent` per 07 §7.2; closed-list `kind` literal: `turn_started`, `turn_progress`, `tool_event`, `artifact_event`, `turn_warning`, `turn_completed`, `turn_failed`, `visibility_degraded`, `steer_ack`, `capability_changed`, `capability_manifest_updated`. Per-kind payload sub-models per 07 §7.3.
- `src/claude_anyteam/protocol_io.py` — new `append_event(team, agent, envelope)` (writes one line to `~/.claude/teams/<team>/events/<agent>.jsonl` under `events/.lock`); new `read_events(team, agent, since_seq=None, limit=100)` (offset-based reader).
- Channel fan-out per 07 §7.4 lives in the per-backend `emit_*` helpers (R17/R18/R19/R20 use them).
- Tests: round-trip of every envelope kind; concurrent writers serialize correctly under `events/.lock`; reader offset semantics.

**Acceptance.**
- A test writes 100 `turn_progress` envelopes via `append_event`; `read_events(since_seq=50)` returns the last 50 in order.
- Two adapters writing to the same team's events directory (different agents) serialize correctly under `events/.lock`.
- A malformed envelope raises Pydantic validation error before write.

**Size.** L (~900 LOC including envelope models + helpers + tests + open-question flags for rotation policy).

**Axes.** §1 preserves (envelope's `raw_backend_type` field per 07 §7.3 explicitly preserves harness-specific event names); §2 *improves* (this IS the §2 surface); §3 improves (peers can subscribe to peer event logs for cross-task awareness per 07 §5.7).

**Risk.** Volume — Codex App Server can emit hundreds of events per turn. Mitigation: append-only is cheap; rotation policy parked as open question (07 §10.5); ship with no rotation in v0.7.1, add config knob in v0.7.2.

---

### R17 — Codex App Server tool-event normalization (L17, B9 §6.5 step 1)

**Goal.** Every Codex App Server `commandExecution`, `fileChange`, `webSearch`, `mcpToolCall`, and `agentMessage` emits a normalized `tool_event` or `artifact_event` envelope.

**Prereqs.** R16 (envelope models + helpers).

**Modules.**
- `src/claude_anyteam/codex.py:74-88` — extend the substring matcher to recognize `commandExecution`, `fileChange`, `webSearch`, `imageGeneration`, `plan`, `error` items in addition to current `mcpToolCall`.
- `src/claude_anyteam/codex.py:_record()` — for each recognized item type, build the envelope (preserving `raw_backend_type` verbatim per 07 §7.3) and call the `event_sink` callback.
- `src/claude_anyteam/codex.py:app_server_invoke()` — new `event_sink: Callable[[VisibilityEvent], None]` parameter; default is a stderr-only sink for backwards compat; `loop.py` provides a sink that writes to the event log (R16) AND coalesces mailbox warnings.
- `src/claude_anyteam/loop.py:_execute_task_app_server()` — provide the sink that writes to event log + rate-limited task `activeForm` updates per 07 §7.4.
- Tests: a Codex App Server fixture emits `commandExecution` → assert `tool_event(category=host_tool, raw_backend_type=commandExecution)` in the event log; same for `fileChange` → `artifact_event`.

**Acceptance.**
- A Codex App Server turn that runs `bash uv run pytest` produces a `tool_event` envelope with `payload.tool_name="commandExecution"`, `payload.raw_backend_type="commandExecution"`, `payload.target="uv run pytest"`, `payload.exit_code=0` in `~/.claude/teams/<team>/events/codex-runtime.jsonl`.
- A Codex App Server turn that edits 3 files produces 3 `artifact_event` envelopes with correct `path`, `action`, `bytes_delta`.
- Stderr-only sink for backward compat: an adapter that hasn't wired the new sink path still works.

**Size.** M (~500 LOC including extended substring/type matcher + per-item envelope builders + sink wiring + tests).

**Axes.** §1 preserves (envelope carries native event names); §2 *improves* (this is the lead's first live host-tool visibility for routed teammates); §3 improves.

**Risk.** Codex App Server item-type list may evolve in future versions. Mitigation: substring matcher already tolerates additions; new item types fall through with a debug-log; document upstream-watch list in the codex.py module docstring.

---

### R18 — Wrapper shadow-tool instrumentation (L17-bis, B9 §6.5 step 2)

**Goal.** Every wrapper MCP tool call (team coordination + shadow host tools) emits start/completed/failed `tool_event` envelopes.

**Prereqs.** R16, R11 (so the events know which backend to attribute).

**Modules.**
- `src/claude_anyteam/wrapper_server.py:178-595` — decorator (or inline wrapping) on every `EXPOSED_TOOLS` handler emits `tool_event(phase=started)` before the handler body and `tool_event(phase=completed|failed)` after.
- Categories per 07 §7.3:
  - `team_tool` for `send_message`, `task_update`, `task_create`, `read_inbox`, `task_list`, `read_config`, `mcp_anyteam_capability_manifest` (R13).
  - `shadow_tool` for `mcp_anyteam_shell`, `mcp_anyteam_read_file`, `mcp_anyteam_write_file`, `mcp_anyteam_edit_file`, `mcp_anyteam_list_directory`, `mcp_anyteam_search`, `mcp_anyteam_web_fetch`.
- Channel fan-out per 07 §7.4: stderr always, event log always, mailbox only on `phase=failed`.
- Tests: a `mcp_anyteam_shell("ls")` produces two events (`started`, `completed`); a failing shell tool produces `failed` + a mailbox `visibility_degraded` per fan-out rules.

**Acceptance.**
- Every wrapper tool invocation produces matched `started`/`completed` envelopes in the event log; the lead can audit "what wrapper tools did this teammate call" without reading stderr.
- A `mcp_anyteam_shell` failure produces `tool_event(phase=failed)` in the event log AND a concise `visibility_degraded` warning in the lead's mailbox.
- Adding a new wrapper tool requires the decorator (or inline wrapping); a missing decorator is detected by the existing `EXPOSED_TOOLS`/`BLOCKED_TOOLS` symmetric test (E10).

**Size.** M (~400 LOC).

**Axes.** §1 preserves; §2 *improves*; §3 improves (`team_tool` events make peer-to-peer coordination observable).

**Risk.** Wrapper tool latency rises by the cost of two file appends per invocation. Mitigation: `events/.lock` is short-held; benchmark before/after; under 5ms additional latency is acceptable for the visibility win.

---

### R19 — Headless terminal digests (L17-ter, B9 §6.5 step 3)

**Goal.** Gemini headless and Kimi headless emit `turn_started` + `turn_completed`/`turn_failed` envelopes with whatever events were captured post-exit.

**Prereqs.** R16.

**Modules.**
- `src/claude_anyteam/backends/gemini/invoke.py:435-438` — emit `turn_started` envelope before `subprocess.run`.
- `src/claude_anyteam/backends/gemini/invoke.py:472-482` — emit `turn_completed` (success) or `turn_failed` (non-zero exit / missing terminal `result` event) after exit. Include `partial_events_available: false` if no JSON events were captured.
- `src/claude_anyteam/backends/kimi/invoke.py:460-472` — same for Kimi.
- `src/claude_anyteam/codex.py:429-444` (Codex exec path) — same.
- Tests: a Gemini headless turn that exits zero produces a `turn_completed` envelope; a turn that exits 124 produces `turn_failed` with `error_class=turn_timeout`.

**Acceptance.**
- A Gemini headless turn produces `turn_started` then `turn_completed` envelopes in `events/<agent>.jsonl`.
- A Gemini headless timeout produces `turn_failed` with full counters (`tool_call_events`, `last_message_preview`, `partial_events_available: false` if no stream events).
- A Kimi headless turn that captured 5 `tool_calls[]` includes them in the `turn_completed.payload.events` for replay.

**Size.** S.

**Axes.** §1 preserves (per 07 §7.3 each backend's terminal digest carries its own counts/preview); §2 improves (terminal-only is better than nothing); §3 neutral.

**Risk.** None significant.

---

### R20 — Soft non-progress watchdog as `turn_progress` envelope (B6, B9 §5)

**Goal.** Codex App Server's existing soft watchdog emits a structured `turn_progress(severity=warn)` envelope and a coalesced lead mailbox notification; opt-in hard interrupt is a separate config knob; default-on for Codex App Server only.

**Prereqs.** R16, R17.

**Modules.**
- `src/claude_anyteam/codex.py:740-834` — watchdog trip emits `turn_progress(severity=warn, summary="no visible checkpoint for Ns; checkpoint steer sent", payload={elapsed_s, timeout_s, risk:"timeout_possible", action_taken:"turn_steer_sent"})` via the R17 sink.
- `src/claude_anyteam/config.py` — add `Settings.non_progress_warn_s` (default 300, range 60-900) and `Settings.non_progress_interrupt_s` (default None — opt-in only). CLI flags `--non-progress-warn-s` / `--non-progress-interrupt-s`. Env `CLAUDE_ANYTEAM_NON_PROGRESS_WARN_S` / `CLAUDE_ANYTEAM_NON_PROGRESS_INTERRUPT_S`.
- `src/claude_anyteam/spawn_shim.py:104-126` — add `non_progress_warn_s` and `non_progress_interrupt_s` to per-agent JSON whitelist.
- `src/claude_anyteam/team_cli.py` — `team-agent` accepts and writes the new keys.
- Backend declares `soft_non_progress_watchdog` capability in R11 only when the watchdog is wired (Codex App Server).
- Tests: trip the watchdog with mocked time → assert envelope written + mailbox notification; assert opt-in interrupt fires only when `non_progress_interrupt_s` is set.

**Acceptance.**
- A 300s no-checkpoint Codex App Server turn writes one `turn_progress(severity=warn)` to events.jsonl AND one mailbox warning AND updates task `activeForm` to "running codex: no visible checkpoint for 300s; checkpoint steer sent."
- The same turn does NOT early-interrupt unless `--non-progress-interrupt-s 420` is set.
- Backends without the capability (Codex exec / Gemini / Kimi) do NOT trip the watchdog and their `members[].capabilities` reflects the absence (no `soft_non_progress_watchdog` flag).

**Size.** M (~400 LOC including settings/CLI/env/shim wiring + tests).

**Axes.** §1 *extends* (capability-declared per CD-1; backends without an event-loop transport simply don't declare it); §2 improves (lead sees impending timeout 9m36s earlier per B6 productivity numbers); §3 improves (peers querying "who can run a long task" see the capability flag).

**Risk.** Default 300s may be too aggressive for legitimate long-form thinking. Mitigation: configurable per-teammate via `team-agent`; ships with verbose stderr log to make false positives loud.

---

### R21 — Wrapper MCP peer-DM consistency audit (W3)

**Goal.** Every routed adapter can `send_message` peer-to-peer; integration test guards against the proto-rev silent failure.

**Prereqs.** R11 (so we can target peers by capability).

**Modules.**
- `src/claude_anyteam/wrapper_server.py:178-243` — audit `send_message`'s recipient-membership check; ensure `to=<peer-name>` works as well as `to="team-lead"` and `to="*"`.
- `src/claude_anyteam/prompts.py`, `src/claude_anyteam/backends/gemini/prompts.py`, `src/claude_anyteam/backends/kimi/prompts.py` — kill any "you can only message team-lead" wording.
- New test `tests/test_peer_dm_matrix.py` — fixtures spawn 5 mock-backend wrappers (Codex App Server / Codex exec / Gemini ACP / Gemini headless / Kimi headless) and assert each can `send_message(to=<each-peer>)` and the peer's inbox shows the message.

**Acceptance.**
- A Codex teammate calls `send_message(to="gemini-bob", body="hi")` and `gemini-bob`'s inbox contains the message with `from=codex-runtime`.
- The matrix test passes for all 5 × 4 peer-DM combinations (each backend instance × every other peer).
- Prompt audit: no mention of "team-lead-only" in any of the three prompts.py files.

**Size.** S.

**Axes.** §1 preserves; §2 improves; §3 *improves* (CLAUDE.md §3 explicit failure mode "Peer-DM gaps").

**Risk.** May discover a wrapper bug requiring code unblock — bumps R21 from S to M.

---

### R22 — Audit Gemini PR-#11/#12 `delivered_via_tool` guard (W7)

**Goal.** Confirm 08 L6's claim that Gemini already has the guard; verify and close.

**Prereqs.** None.

**Modules.**
- `src/claude_anyteam/backends/gemini/loop.py:289-291` — verify the guard is present and equivalent to Codex's at `src/claude_anyteam/loop.py:265-267`.
- If missing: add it.
- Tests: peer receives one reply not two for any prose response Gemini delivered via `mcp_anyteam_send_message`.

**Acceptance.**
- Gemini's `_handle_prose` skips the canned fallback when `tool_call_events > 0` and `last_message` is empty.
- Tests cover the case across all three backends; cross-backend test in `tests/test_prose_fallback.py`.

**Size.** S (likely already shipped; just verify).

**Axes.** §1 preserves; §2 improves; §3 improves.

**Risk.** None significant.

---

### R23 — Extract shared `_should_skip_prose_fallback(result)` helper (L6)

**Goal.** Single place to fix the prose-fallback heuristic; remove duplication across three loops.

**Prereqs.** None.

**Modules.**
- `src/claude_anyteam/protocol_io.py` (or new `src/claude_anyteam/prose.py`) — add `should_skip_prose_fallback(result) -> bool`.
- `src/claude_anyteam/loop.py:265-267` — replace inline check with helper.
- `src/claude_anyteam/backends/gemini/loop.py:289-291` — same.
- `src/claude_anyteam/backends/kimi/loop.py:260-262` — same.
- Tests: helper covers all three backend result shapes.

**Acceptance.**
- All three loops call the same helper; fixing the heuristic is a one-file change.
- Existing prose-fallback tests pass unchanged.

**Size.** S.

**Axes.** §1 preserves; §2 neutral; §3 neutral. (Code-hygiene; no observable surface change.)

**Risk.** None.

---

### R24 — (deferred to v0.8.x — see D4 / D5 for related items)

(Reserved.)

---

### R25 — (deferred — see D5 for related substrate-spec change)

(Reserved.)

---

### R26 — Audit + extend `claude-anyteam diagnose` CLI (B3 §5.1)

**Goal.** A lead investigating a routed-teammate failure runs `claude-anyteam diagnose --tail 5` and gets a structured table.

**Prereqs.** R16 (event log — enables `--from-events` future flag); R7/R8 (typed payloads with `incident_id`).

**Modules.**
- `src/claude_anyteam/diagnose_cli.py` (already exists per file listing) — audit existing scope; add `--tail`, `--team`, `--agent` filters; per-incident JSON dump.
- New test `tests/test_diagnose_cli.py` covering each subcommand variant.

**Acceptance.**
- `claude-anyteam diagnose` lists recent incidents across all teams (table format).
- `claude-anyteam diagnose --tail 5 --team <T>` lists last 5 incidents per teammate in that team.
- `claude-anyteam diagnose <incident_id>` prints the per-incident JSON pretty-printed.

**Size.** S.

**Axes.** §1 preserves; §2 *improves* (lead's primary forensic CLI); §3 neutral.

**Risk.** Existing `diagnose_cli.py` may already cover most of this — audit first.

---

### R27 — `team-roster` resolved-config view (B3 §5.5)

**Goal.** `team-roster` shows the spawn-time effective `model` and `effort` (read from per-teammate JSON) in addition to host-config `model`.

**Prereqs.** R11 (capabilities column already added).

**Modules.**
- `src/claude_anyteam/team_cli.py:331-381` — extend `_RosterRow` with `adapter_model`, `effort`, `source` fields populated by reading `~/.claude/teams/<team>/agents/<agent>.json`.
- New `--no-resolve` flag preserves legacy output for scripts grepping the old format.
- New `claude-anyteam team-config <agent> --team <T>` subcommand prints the resolved spawn argv (reuse `spawn_shim._adapter_argv` helper).
- Tests: roster row for routed teammate with per-teammate config shows resolved values; without it shows `(default) / source=default`; for native-claude teammate shows `in-process` placeholder; `--no-resolve` reproduces legacy byte-for-byte.

**Acceptance.**
- `claude-anyteam team-roster --team <T>` shows columns `name | type | host_model | adapter_model | effort | capabilities | source`.
- `claude-anyteam team-config codex-packaging --team <T>` prints the literal argv the shim would build at next spawn.

**Size.** S.

**Axes.** §1 preserves; §2 *improves* (lead can verify a `team-agent` override took effect); §3 neutral.

**Risk.** None.

---

### R28 — Audit + extend `claude-anyteam status` CLI (B3 §5.4)

**Goal.** Per-team table with health status and `last_seen`.

**Prereqs.** R16 (event log enables liveness signal).

**Modules.**
- `src/claude_anyteam/status_cli.py` (already exists) — audit; add per-team filter, last_seen via inbox-file mtime, incident count via diagnostics directory scan.
- Tests.

**Acceptance.**
- `claude-anyteam status --team <T>` prints a table: `name | backend | health | last_seen | incidents_total | last_error_class`.
- `health=ok|degraded|in-process` distinguishes by recent incident presence.

**Size.** S.

**Axes.** §1 preserves; §2 improves; §3 neutral.

**Risk.** None.

---

### R29 — `claude-anyteam bundle` incident-tar.gz (B3 §5.3)

**Goal.** A user reporting a bug runs one command to produce a sharable, redacted bundle.

**Prereqs.** R16 (event log included in bundle).

**Modules.**
- New `src/claude_anyteam/bundle_cli.py` — produces `claude-anyteam-bundle-<unix>.tar.gz` per B3 §5.3 layout.
- Redaction: drop `apiKey`, `accessToken`, `secret`, `password`, `auth*` keys (case-insensitive).
- Tests: bundle includes the right files; redaction works.

**Acceptance.**
- `claude-anyteam bundle --team <T>` produces a tar.gz with team config (prompts redacted), inboxes (last 50 messages each), diagnostics (last 20 incidents), event log tail (last 1000 events), settings.json.redacted.
- "Redacted N keys" message printed at end.

**Size.** M (~250 LOC).

**Axes.** §1 preserves; §2 improves; §3 neutral.

**Risk.** PII leakage if redaction misses a key. Mitigation: comprehensive test suite + `--dry-run` flag listing what would be included before bundling.

---

### R30 — `bug-triage/REPORT_TEMPLATE.md` (B3 §5.6)

**Goal.** A standard template for bug reports that asks for the right diagnostics output.

**Prereqs.** R26, R27, R28, R29 (so the template can reference real CLIs).

**Modules.**
- New `bug-triage/REPORT_TEMPLATE.md` per B3 §5.6 layout.

**Acceptance.**
- Template lists the four CLI commands a user should run + what to paste.

**Size.** S (docs only).

**Axes.** All neutral; meta-improvement.

**Risk.** None.

---

### R31 — `team-roster --health` HEALTH column (B2 D2)

**Goal.** Lead running `team-roster --health` sees config-validation outcome and missing-field repair hint.

**Prereqs.** R2 (default `agent_type` already mitigates the original failure mode).

**Modules.**
- `src/claude_anyteam/team_cli.py` — after listing rows, attempt `TeamConfig.model_validate(cfg)`; surface validation errors in a trailing block per B2 §3.3.
- Repair hint: `claude-anyteam team-patch --team <T> --all-external`.
- Tests: corrupt a sibling row (in-test) → assert HEALTH block prints expected error.

**Acceptance.**
- `team-roster --team <T> --health` prints any validation errors per row + repair hint.
- A clean team prints "HEALTH: OK" line.

**Size.** S.

**Axes.** §1 preserves; §2 improves (B2 silent failures stop being silent); §3 improves.

**Risk.** None.

---

### R32 — Gemini ACTION_FIRST_EXECUTION prompt preamble (B4 §3A)

**Goal.** Gemini executor turns lead with a tool call, not an acknowledgment.

**Prereqs.** None.

**Modules.**
- `src/claude_anyteam/backends/gemini/prompts.py` — add `ACTION_FIRST_EXECUTION` preamble per B4 §3A; insert before `# MCP tools available`.
- `src/claude_anyteam/backends/gemini/loop.py:451-452` — tighten retry-attempt text.
- Same for Kimi (B9 §2.4 calls out Kimi has the same failure mode).
- Tests: snapshot the constructed task prompt; assert preamble present.

**Acceptance.**
- Gemini `task_prompt()` for an executor task includes the action-first preamble.
- A Gemini A/B test (R33's harness) shows action-first reduces no-tool turns.

**Size.** S (~50 LOC).

**Axes.** §1 preserves (Gemini-specific tuning, not a flattener); §2 improves (no-action turns become rare); §3 improves.

**Risk.** Action-first may regress some research-only tasks. Mitigation: gated by `action_first=True` parameter; default opt-in for executor roles.

---

### R33 — `task_idle_no_tool_calls` + `task_complete_unverified_tool_count` envelope kinds (B4 §3C, B9 §2.4)

**Goal.** Gemini AND Kimi loops emit a structured envelope when a task turn produces schema-valid JSON without tool calls or files.

**Prereqs.** R16.

**Modules.**
- `src/claude_anyteam/backends/gemini/loop.py:441-491` — after `_backend_run` returns: if `result.exit_code == 0` AND `result.structured` AND `tool_call_events == 0` AND `files_changed` empty → emit `turn_progress(severity=warn, payload={kind:"task_idle_no_tool_calls", ...})`; on attempt 2, mark blocked.
- Same for `src/claude_anyteam/backends/kimi/loop.py:413-461`.
- New envelope sub-kinds (rolled into the main `turn_progress`/`turn_failed` envelope per 07 §7.3, distinguished by `payload.kind`).
- Tests: simulate a no-tool turn → assert envelope; simulate retry path.

**Acceptance.**
- A Gemini turn that returns `{summary, files_changed: []}` JSON with zero tool events writes a `turn_progress` envelope to events.jsonl with `payload.kind="task_idle_no_tool_calls"` AND a mailbox warning to lead.
- Same for Kimi.
- After 2 such attempts, the task is marked blocked with `error_class="task_idle_no_tool_calls"` in `task_blocked` payload.

**Size.** M (~300 LOC across two backend loops + envelope sub-kinds + tests).

**Axes.** §1 preserves (Gemini/Kimi-specific diagnostic; doesn't apply to Codex which has different signals); §2 *improves* (turns "no-action" into a structured signal); §3 improves.

**Risk.** Tool-call counter may have false negatives for the same reason Codex's does. Mitigation: include `tool_call_events_low_confidence: true` flag when the counter is known-unreliable.

---

### R34 — Route action-looking DMs as task steers (B4 §3B)

**Goal.** A lead-authored DM that looks like a task brief is queued as a steer for the next claimable task instead of producing a prose reply.

**Prereqs.** None.

**Modules.**
- `src/claude_anyteam/backends/gemini/loop.py:177-188` — `_handle_message()` consumes `TaskAssignmentIn` (currently a debug no-op): queue `QueuedSteer(task_id, message=subject+description)`.
- `src/claude_anyteam/backends/gemini/loop.py:254-270` — `_handle_prose()`: if sender is `team-lead` AND `_looks_like_task_brief(text)`, queue as steer and emit `task_brief_queued` envelope; if no claimable task, emit `task_brief_no_claimable_task`.
- Same for Kimi.
- Helper `_looks_like_task_brief(text)` — heuristic on signals like "use the Write tool", "Begin now", task-like markdown headers, absolute repo paths.
- Tests: a synthetic action-looking DM produces a queued steer + envelope; a non-action prose DM goes through normal `_handle_prose`.

**Acceptance.**
- A lead DM containing "Your immediate first action: call mcp_anyteam_write_file" produces a `task_brief_queued` envelope and queues the steer; no prose reply is generated.
- The next claimed task receives the steer prefix.

**Size.** M (~200 LOC).

**Axes.** §1 preserves (per-backend behavior); §2 improves (Gemini/Kimi stop "responding-but-not-acting"); §3 improves.

**Risk.** Heuristic false-positives: a legitimate prose discussion gets routed as a steer. Mitigation: heuristic is conservative (requires multiple signals); `task_brief_queued` envelope makes the routing visible to lead for immediate correction.

---

### R35 — `team-agent` warning for old Gemini models (B4 §6)

**Goal.** A user spawning `gemini-research-1` with `--model gemini-2.5-pro` sees a warning that this model is sensitive to drift on executor roles.

**Prereqs.** None.

**Modules.**
- `src/claude_anyteam/team_cli.py` — `team-agent` warns when `name` starts with `gemini-` AND `model` doesn't match `^gemini-3.*`.
- Roster `--diagnose` flag flags Gemini teammates with `backend=headless` AND old model as "higher no-action risk."

**Acceptance.**
- `team-agent codex/teammate config --name gemini-research-1 --model gemini-2.5-pro` prints the warning.
- `team-roster --team <T> --diagnose` shows risk flag for Gemini teammate with old model.

**Size.** S.

**Axes.** §1 preserves; §2 improves; §3 neutral.

**Risk.** None.

---

### R36 — Default Gemini backend headless→ACP (B4 §3D)

**Goal.** Conditional on R32+R33+R34 evidence: switch `GeminiSettings.backend` default from `"headless"` to `"acp"`.

**Prereqs.** R32, R33, R34 + A/B evidence (run a fresh productivity test post-R32 + R33 + R34 ship).

**Modules.**
- `src/claude_anyteam/backends/gemini/config.py:38, 67-70` — change default from `"headless"` to `"acp"`.
- `src/claude_anyteam/cli.py` — update help text.
- Tests that assert default backend.

**Acceptance.**
- A `gemini-*` teammate spawned without explicit backend uses ACP.
- Productivity benchmark (5 file-creation tasks) shows ACP at least matches headless on time-to-first-tool-call.

**Size.** S (config change; tests update).

**Axes.** §1 *extends* (ACP carries more harness capability than headless); §2 improves; §3 improves.

**Risk.** ACP-incompatible Gemini CLI versions. Mitigation: keep headless reachable via `--backend headless` / env override; document fallback.

---

## 4. Sequencing argument

### Why v0.7.0 ships transport + cheap-flag capability declaration first

Three reasons:

1. **R2 (pydantic default `agent_type`) is a hard prerequisite for R11 (capability declaration).** Adding a new field to `TeammateMember` without `default_factory` would risk the same one-bad-sibling-kills-everyone failure on legacy v0.6.x rows. R2 is one line; pairing it with the capabilities-list field write avoids two breaking-change windows for the substrate.
2. **R3 (message_kind discriminator) is foundational for R16 (event envelope) and R20 (watchdog mailbox surface).** Envelope events that also appear in the inbox should declare their `message_kind` so consumers can filter; shipping R3 in v0.7.0 means R16/R20 in v0.7.1 inherit a typed surface from day one.
3. **R6 (shutdown_approved/rejected) is a real wire bug** per W0; ship it in v0.7.0 alongside the other transport corrections so v0.7.1's visibility envelope work doesn't have to coordinate with a wire migration.

R11 (capability declaration cheap flags) ships in v0.7.0 even though it's a capability-layer item because:

- The cheap-flags layer is small (one new field on `TeammateMember`, a `team-roster` column, four backend wirings).
- It unlocks the routing-decision use case immediately (lead can `team-roster --capabilities` and assign tasks based on declared capability).
- v0.7.1's R12-R15 build on the cheap-flag layer, so shipping R11 first means the rich-manifest work doesn't need to invent a parallel discovery mechanism.

### Why v0.7.1 ships the event envelope before the reporting kit

R16 (envelope channel) is L-sized and foundational. Shipping it in v0.7.1 means:

- B3 reporting kit (R26-R31) in v0.7.2 reads from the event log instead of inventing parallel diagnostic surfaces.
- B4 productivity diagnostics (R33) emit envelope sub-kinds rather than ad-hoc payloads.
- B6 watchdog (R20) is a `turn_progress(severity=warn)` envelope, not a separate primitive.

Bundling R12-R15 (capability rich manifest + steer auth) into the same release means peer-fidelity work lands together: the manifest cache (R12) + the wrapper MCP tool (R13) + the prompt fragments (R14) + the steer authorization (R15) are all needed for a complete §3 surface.

### Why v0.7.2 is reporting + Gemini productivity

Both depend on the v0.7.1 envelope. Both are user-facing observable wins (a CLI subcommand; a Gemini executor that finally writes files). Bundling them gives the v0.7.2 release a clear theme ("the productivity release") that maps to user-visible improvements.

### Why the kit (K1-K4) is Phase 2

The kit's design (08 §6) requires:

- A Storage abstraction that filesystem, Redis, SQLite, and host-private backends all implement.
- A Teammate base class that subsumes registration, inbox poll, claim CAS, idle pings, shutdown lifecycle, event log, mailbox rate-limiting.
- A Team class with capability discovery and peer-prompt aggregation.

Each is L-sized; together they're a v0.8.x or Phase-2 ship cycle of their own. v0.7.x roadmap items deliberately ship discrete primitives (R3 message_kind, R10 schema_version, R11 capabilities, R12 manifest broadcast, R16 event envelope) so the eventual kit lift is incremental — each kit class can be built from the discrete primitives without breaking changes.

---

## 5. Mapping table A: B9 §6 P1–P5 → R items

The B9 visibility-parity investigation enumerated five prioritized proposals. Each maps to a v0.7.x item:

| B9 §6 priority | Proposal | Roadmap item | Notes |
|---|---|---|---|
| P1 | `teammate_activity` mailbox class | R16 (envelope) + R17/R18 (emitters) + R3 (message_kind) | Renamed to `tool_event` / `artifact_event` envelope kinds per 07 §7.3; same outcome, harness-name-preserving. |
| P2 | Append-only event log | R16 | `~/.claude/teams/<team>/events/<agent>.jsonl` |
| P3 | Backend-neutral envelope | R16 | With explicit `raw_backend_type` preservation per 07 §7.3 (NOT a flattener) |
| P4 | Structured failure prose | R7 (backend field) + R8 (TaskBlockedOut) + R26 (diagnose CLI) | Carries `error_class` + `incident_id` per B3 §3.2 |
| P5 | Watchdog generalization | R20 (App Server only — NOT generalized; capability-declared per R11) | Reframed: capability declaration carries the gap, not a generic primitive |

### B9 §6.6 acceptance criteria → roadmap items

| B9 §6.6 criterion | Roadmap item |
|---|---|
| Codex commandExecution → tool_event in event log | R17 + R16 |
| 300s no-checkpoint → mailbox warning, no early-interrupt | R20 |
| mcp_anyteam_shell failure → tool_event(failed) + warning | R18 |
| Gemini/Kimi headless timeout → terminal turn_failed digest | R19 |
| Existing payloads work unchanged (additive) | R10 (schema_version) + R3 (message_kind) — both backwards-compat by design |

---

## 6. Mapping table B: 08 leak ledger → roadmap items

Every leak from 08 §3 + every CD from 08 §4 mapped to v0.7.x ship, v0.8.x deferral, or kit (K).

### Configuration tier

| Leak | Item | Notes |
|---|---|---|
| L1 | D1 (v0.8.x) | `CodexResult` rename — kit absorbs as `TaskResult` |
| L7 | R7 (v0.7.0) | `backend_exit_code` + `backend` field |
| L9 | K1/K2 (Phase 2) | Parallel agents/ config — kit's Agent Card stores model/effort in member row |
| L21 | D2 (v0.8.x) | Three Settings classes — kit's `TeammateSettings` |
| L25 (RETRACTED) | n/a | Withdrawn per 08 §3.1 |
| L25-bis | R6 (v0.7.0) + D8 (host-side) | Wire-shape (R6) + `backendType="external-cli"` host ask (D8) |

### Inbox / wire tier

| Leak | Item | Notes |
|---|---|---|
| L10 | R8 (v0.7.0) — partial; first-class `blocked` status deferred to D8a (v0.8.x) | TaskBlockedOut Pydantic |
| L11 | R9 (v0.7.2) — after deprecation period | Drop legacy steer text-prefix |
| L11-bis | D12 (v0.8.x) | `kind` vs `type` unification |
| L11-tris | D5 (v0.8.x) | Wrapper `send_message` rename to `peer_message` |
| L11-quad | K2 (Phase 2) — kit's `TeammateName` newtype | For now, validation in `wrapper_server.py:send_message` recipient check (rolled into R21) |
| L12 | K1 (Phase 2) — Storage.ack_messages(ids) | For now, R3's local guard via `read_own_inbox` assert holds |
| L20 | D4 (v0.8.x) | `read_inbox` mutation footgun |
| L25-bis (catalog audit) | R6 (v0.7.0) + future audit (v0.7.x continuous) | Each new wire payload audited against codex-extract 03 catalog |
| L26 | R10 (v0.7.0) | schema_version on every payload |
| L31 | K1 (Phase 2) | JSON Schemas for every wire payload — kit emits at build time |
| L34 | D11 (v0.8.x) | `<system_reminder>` body mutation — substrate change |

### Task tier

| Leak | Item | Notes |
|---|---|---|
| L27 | R5 (v0.7.0) | Shared config lock |
| L28 | K1 (Phase 2) | Explicit `role` field — substrate change; wait for kit migration window |
| L29 | D9 (any release) | Wrapper `task_get` classification |
| L32 | K2 (Phase 2) — Storage.update_task validates owner-membership | For now, wrapper `task_update` audited as part of R21 peer-DM consistency |
| L33 | R4 (v0.7.0) | Filelock timeout |

### Lifecycle tier

| Leak | Item | Notes |
|---|---|---|
| L6 | R23 (v0.7.1) | Shared `_should_skip_prose_fallback` helper |
| L13 | R26 (v0.7.2) | Diagnose CLI reads diagnostics directory |
| L14 | R8 (v0.7.0) — partial via `incident_id` field on `TaskBlockedOut` | Full `incident_ref` on every envelope is K (kit) |
| L24 | D7 (v0.8.x) | Test-seam class-based — kit's runner-instance overrideable invoke() |

### Wrapper / MCP tier

| Leak | Item | Notes |
|---|---|---|
| L8 (RECLASSIFIED) | CD-4 → R11 (v0.7.0 cheap-flag) + R12 (v0.7.1 rich-manifest) | `host_tool_surface` capability declaration |
| L18 | D3 (v0.8.x) — partial coverage by R17/R18 envelope | Full normalization is kit |
| L19 | D3 (v0.8.x) — partial via R17/R18 | Same |
| L22 | D5 (v0.8.x) | `handle_server_request` per-method registration — kit |
| L23 | D6 (any release) | thread_start shape `visibility_degraded` |

### Event log / visibility tier

| Leak | Item | Notes |
|---|---|---|
| L17 | R16 (v0.7.1) | Append-only event log |
| L17-bis | R18 (v0.7.1) | Wrapper shadow-tool instrumentation |
| L17-ter | R19 (v0.7.1) | Headless terminal digests |

### Schema versioning / drift tier

| Leak | Item | Notes |
|---|---|---|
| L30 | D10 (any release) | `minimal` effort tier — small enough to land opportunistically |

### Capability declarations (CD-1..CD-6)

| CD | Item | Notes |
|---|---|---|
| CD-1 | R11 (cheap flag `soft_non_progress_watchdog`) + R12 (rich manifest) + R20 (watchdog wiring) | Codex App Server only |
| CD-2 | R11 (`turn_steer`) + R12 (rich manifest with `delivery_mode`) + R14 (peer prompts) | All backends |
| CD-3 | R11 (`permission_bridge`, `approval_policy`) + R12 (rich manifest) | Gemini ACP only |
| CD-4 | R11 (`host_tool_surface`) + R12 (rich manifest) | All backends — informational |
| CD-5 | R12 (rolled into `turn_steer.expiry_semantics`) | Sub-capability |
| CD-6 | R11 (`accepts_peer_steer`) + R15 (capability-declared steer authorization) | All backends; default false |

---

## 7. What we're NOT doing

Per the §1 / §2 / §3 lens and the explicit deferral discipline:

- **NOT building a router.** No model dispatch logic in claude-anyteam. No `ANTHROPIC_BASE_URL` proxy. The harness IS the teammate.
- **NOT exposing one harness with multiple models.** One harness, one model slug, one teammate. (Per A0 in 08 §7.)
- **NOT flattening tool surfaces.** R16 envelope explicitly preserves backend-native event names via `payload.raw_backend_type`. The lead UI may render `commandExecution` for Codex side-by-side with `Bash` for native Claude — that's a feature, not a bug.
- **NOT making the watchdog backend-agnostic.** R20 stays Codex-App-Server-only because the watchdog's signal source (mid-turn `agentMessage` byte deltas + artifact deltas) is App Server-specific. Other backends declare the absence via `soft_non_progress_watchdog` capability flag (R11).
- **NOT extracting a "common backend interface" beyond `run_task`/`handle_prose`.** Anything richer would be a flattener. The kit (K2) eventually formalizes this; v0.7.x ships discrete primitives.
- **NOT building protocol primitives that hide capability deltas.** No `is_steerable=true/false` boolean. Capabilities are an explicit enumerable list per R11 so future backends can extend without schema change.
- **NOT building peer routing logic that flattens "all peers are equivalent".** Peer capabilities are heterogeneous; the right peer for a job depends on the manifest (R12).
- **NOT promoting peer-DM to a privileged subset of lead-to-peer.** Peer-DM is its own first-class surface with the same `send_message` semantics (R21).
- **NOT keeping steer authorization hardcoded as lead-only.** Capability-declared per recipient (R15).
- **NOT making heartbeat/substance distinguishable only by prose parsing.** The `message_kind` discriminator (R3) is a §3 obligation.
- **NOT lazily fetching capability manifests per invocation.** Eager broadcast + local cache + version-bump invalidation (R12).
- **NOT shipping the 200-line SDK kit in v0.7.x.** The kit (08 §6, opus-vision 10) is Phase 2. v0.7.x ships the discrete primitives the kit will eventually formalize.
- **NOT changing the substrate's task status enum to add `blocked` in v0.7.x.** Substrate change requires host coordination; deferred to D8a (v0.8.x) behind explicit migration evidence.
- **NOT renaming `CodexResult` to `BackendResult` in v0.7.x.** Touches every backend import + every test; kit absorbs naturally as `TaskResult` (D1).
- **NOT extracting `TeammateSettings` base class in v0.7.x.** Touches three Settings modules + every CLI/env precedence; kit absorbs (D2).
- **NOT reaching for AppState injection.** Per 07 §3 / §7, the host's `setAppState` is a no-op for inbound calls; the only viable visibility-parity strategy is the parallel observer-friendly event channel (R16).
- **NOT building cross-impl contract test infrastructure.** Per 07 §10.8, this requires Phase-2 spec adoption; deferred.
- **NOT building artifact-clobber peer-write detection.** Per B9 §7.4 / 07 §10.9, the sampler design is open; deferred.
- **NOT building event-log replay/idempotency.** Per 07 §10.10, the design is open; ship under-spec'd in v0.7.1, defer formal replay semantics.
- **NOT building per-inbox `<inbox>.json.lock` sidecar.** Per 07 §1.6 / §9.5, the host RE has it; the substrate race exists; mitigation depends on R5 (shared config lock); the per-inbox sidecar is parity-with-host but not a v0.7.x ship-blocker. Tracked as D8b (v0.8.x).
- **NOT solving the `.highwatermark` cross-process collision.** Per 07 §1.5 / §9.5, the host uses `.highwatermark`; vendored substrate uses scan; collision is rare in practice (host + adapter rarely share a task list). Document the constraint; defer hardening to a follow-up.

---

## 8. Open questions parked

These remain `[O]` per 07 §10. Each is a research line; resolving them may add or rescope roadmap items.

| Open question | Source | Impact on roadmap |
|---|---|---|
| AppState.tasks shape under routed shim | 07 §10.1 | If the host renders our shim path differently than native in-process, R16 envelope `visibility` fan-out may need adjustment. |
| Native tool-call event taxonomy | 07 §10.2 | If the host has a private channel we don't see, R17/R18 envelope may need to bridge into it. |
| Per-teammate vs team-shared TaskList scope | 07 §10.3 | Peer hand-off semantics (R21 audit) may need to bridge per-teammate scope. |
| `backendType` host enforcement | 07 §10.4 | If the host tightens enum, R11 capability declaration may need to migrate from `members[].capabilities` to `members[].backendType` extension. |
| Event log lock + rotation | 07 §10.5 | R16 ships without rotation; v0.7.2 adds a knob. |
| Capability declaration shape | 07 §10.6 | This roadmap commits to a specific shape (R11/R12); if codex-prototype or opus-vision propose a different shape, R11/R12 may need to align. |
| v2 wire unification (`type` vs `kind`) | 07 §10.7 | D12 (v0.8.x) — wait for cross-impl contract test pressure. |
| Cross-impl contract test | 07 §10.8 | Phase 2; required before R10 schema_version becomes load-bearing for non-Claude harnesses. |
| Artifact-clobber visibility | 07 §10.9 / B9 §7.4 | Future capability declaration `peer_writers_seen` on `artifact_event`; not in v0.7.x scope. |
| Event log acknowledgment / replay | 07 §10.10 | R16 ships idempotent `event_id`; full replay semantics deferred. |

---

## 9. Cross-references

- **CLAUDE.md** — north stars §1 (harness preservation), §2 (visibility parity), §3 (peer efficiency); two-layer split (transport vs capability).
- **docs/architecture.md** — core principle, design principle (protocol-first, lossy mappings acceptable), why no LLM wrapper.
- **docs/roadmap.md** — current shipping plan; this doc supersedes the v0.7.0 section ("backend-neutral progress watchdog and event envelope") with the harness-preservation reframe (R20 stays App-Server-only; envelope is R16).
- **docs/internal/protocol-rev/07-protocol-spec.md** — canonical wire spec; this doc cites file:line throughout.
- **docs/internal/protocol-rev/08-elegance-and-gaps.md** — leak ledger + kit design; mapping tables in §6 link every L/CD to a roadmap item.
- **docs/internal/protocol-rev/10-platform-vision.md** (in-flight, opus-vision) — Phase-2 SDK chapter; K1-K4 link to its substance.
- **docs/internal/strategic-roadmap.md** — long-term thesis (Phase 2 protocol publication); this roadmap's v0.7.x prepares the primitives Phase 2 needs.
- **bug-triage/B1-B5-packaging-and-slots.md** — B1 (R1) + B5 (deferred to docs/CLI workflow per 08).
- **bug-triage/B2-send-message-lifecycle.md** — B2 Fix S (R2) + Fix R (rolled into R5 / R11 self-heal) + Fix D (R31).
- **bug-triage/B3-prose-reply-and-reporting-kit.md** — B3 (R26-R31).
- **bug-triage/B4-gemini-productivity.md** — B4 (R32-R36 + R33's Kimi parity).
- **bug-triage/B6-turn-timeout.md** — B6 (R20).
- **bug-triage/B7-fork-cleanup-status.md** — shipped; remaining leaf hits in `src/claude_teams/opencode_client.py` already removed per B8.
- **bug-triage/B8-opencode-removal-status.md** — shipped.
- **bug-triage/B9-visibility-parity-investigation.md** — B9 (R16-R20).
- **references/external-claude-code-re/prototype-sdk/** (in-flight, codex-prototype) — when the 200-line SDK prototype lands, R11 (capability declaration) and R16 (event envelope) MAY align internal shapes against it; not blocking.

---

**End of roadmap.**

This document is the engineering plan to close the visibility-parity gap (CLAUDE.md §2) while preserving harness capabilities (§1) and peer efficiency (§3). It is sequenced into three v0.7.x release blocks plus deferred v0.8.x and Phase-2 work. Each work item carries observable acceptance criteria, sizing, prerequisites, and triple-axis assessment. The roadmap deliberately defers the kit (08 §6) to Phase 2 and ships discrete primitives the kit will eventually formalize.

Subsequent revisions should: (a) close out items as they ship by linking PR + version; (b) update §6 mapping tables when new leaks are filed; (c) promote deferred items (D1-D12) into v0.7.x release blocks if engineering bandwidth allows; (d) re-evaluate the v0.7.x → Phase-2 boundary as kit work progresses.
