# 08 — Elegance and gaps: what serves harness preservation and visibility parity

**Author:** opus-elegance
**Date:** 2026-04-27
**Type:** research analytical doc — propose elegant shapes; do NOT change code
**Companion to:** 07-protocol-spec (synthesis), 09-roadmap (work-item plan), 10-platform-vision (SDK chapter)

---

## 0. Executive summary

The architectural moat (CLAUDE.md §1) is **harness preservation**: a `codex-*` teammate IS the Codex CLI process — App Server `turn/steer`, `thread/fork`, OpenAI's prompt tuning, Codex's sandbox semantics — flowing through to the team unmodified. The operational consequences are **visibility parity** (§2 — the lead sees routed teammates at native fidelity) and **peer efficiency** (§3 — teammates coordinate among themselves at native Claude pace). §1 is the architecture; §2 and §3 are the consequences for the two audiences that observe the team. They ship together.

This document evaluates the current adapter through that lens. Three findings:

1. **Eight elegance patterns are load-bearing for the moat** and must be preserved. The most important is E0 (capabilities flow through; the protocol normalizes only what's truly common). The clean separation of spawn-shim-as-dispatcher (`spawn_shim.py`), wrapper-as-narrow-coordination-surface (`wrapper_server.py:54-68`), and per-backend `invoke.py` modules is the structural reason the moat works at all.

2. **33 leaks remain, organized by tier**. They split into two categories: (a) flatten-style violations (e.g. `TaskCompleteOut.codex_exit_code` is a Codex name carrying Gemini/Kimi exit codes — leak), and (b) missing wire infrastructure (no append-only event log, no schema versioning, no shared filelock between vendored writers and our registration path). Many were independently observed by codex-substrate (05) and are cross-cited.

3. **Six capability declarations are missing**. These are NOT leaks under §1 — they are faithful harness preservation. The leak is that the capabilities aren't *declared* anywhere the lead or peers can see. `codex-app-server-*` accepts live mid-turn `turn/steer`; `gemini-headless-*` queues for next-turn; the protocol carries the same `SteerIn` envelope to both, and the lead has no roster-level signal of which is which. CD-1 through CD-6 enumerate these.

The "200-line protocol kit" SDK is the artifact that crystallizes the elegance into a protocol library. Three primitives — Agent Cards (A2A precedent), capability declarations + peer-prompt fragments (ACP `initialize` precedent), and visibility events (B9 §6 envelope) — make harness preservation legible and visibility parity automatic.

The most important anti-pattern (A0) is **no router-style flattening**. HydraTeams (Pickle-Pixel) proxies `ANTHROPIC_BASE_URL` to swap the model behind Claude Code's harness; claude-code-router does the same shape; OpenCode routes through one session loop. All three structurally cannot preserve `codex app-server`'s JSON-RPC envelope, Gemini ACP's permission bridge, or Kimi's internal swarm. Routers can't, by construction. The moat is durable because to match us a router has to throw out its session loop and rebuild around external process orchestration; at that point it's no longer a router.

---

## 1. The three north stars as design lens

### 1.1 §1 harness preservation: what flows through

`docs/architecture.md:9-21` (the new "core principle" section) is canonical. Three operational consequences follow:

1. **Capabilities flow through, they do not flatten.** When Codex has a feature Gemini doesn't (or vice versa), the protocol surfaces and routes that feature; it does not strip it to a lowest common denominator. The five-tier `--effort` and `--model` pass-through are the *only* protocol-side normalizations because they're the only knobs that map cleanly across every backend. Everything else is harness-native.
2. **Peers learn each other's capabilities.** Capability declarations live in the team roster (registration); system prompts teach peers how to invoke unique features ("ask `codex-*` teammates to use `thread/fork` for cross-task memory continuity"). The protocol carries the declarations; the prompts deliver the how-to.
3. **The protocol is a coordinator, not a kernel.** It carries identity, lifecycle, mailbox, task state, and capability declarations. It does *not* own the agentic loop, the tool definitions, or the model's prompt tuning. Those belong to each harness.

### 1.1.1 The two-layer protocol (transport + capability)

`CLAUDE.md:28-46` makes explicit what the elegance pattern E0 implies: the protocol is **two layers, not one**, and conflating them is what makes router approaches collapse.

- **Transport layer** — Agent Teams as Claude Code ships it: mailbox JSON, atomic task claims, lifecycle (idle / shutdown / plan-approval / permission), config and task file shapes, locks. Universal; table stakes for being a teammate at all. Codex-extract (03) gives us the canonical shape from the host binary v2.1.119; codex-substrate (05) maps the substrate; this is what every teammate (native Claude or routed) shares.
- **Capability layer** — Each harness advertises what it *uniquely* can do, and tells peers when and how to invoke those primitives. Per-harness, MCP-style: `version` + `schema` + `description` + `when_to_use` + `when_not_to` + `failure_modes`. The MCP precedent already works at the tool level; we extend it one level up to "what unique primitives does this teammate offer."

The two-layer split has direct consequences for this document:

- The §3 leak ledger is mostly **transport-layer** issues (wire-shape drift, lock semantics, schema versioning). These are foundation cracks in the universal substrate.
- The §4 missing capability declarations (CD-1..CD-6) are **capability-layer** absences. Transport-layer is fine — `SteerIn`, the permission flow, watchdog warnings — they all use the universal mailbox/lifecycle correctly. The leak is the absent capability declaration that would let peers know who can do what, when invoking is the right move, and what failure modes to expect.
- The §6 kit SDK has to make both layers first-class. The Teammate base class handles transport-layer for free; the `agent_card()` / `capabilities()` primitives crystallize the capability layer.
- Anti-pattern A0 (no router-style flattening) is the "skip the capability layer" failure mode. Routers can't bolt the capability layer on later because their architecture has no place for it.

For every entry in this document the lens is now: **is this transport-layer or capability-layer?** If transport, does it preserve universality? If capability, does it preserve harness specificity AND teach peers when to use it? Both layers have failure modes; conflating them produces routers.

### 1.2 §2 visibility parity: the operational consequence (lead-facing)

`CLAUDE.md:48-61`. Harness preservation only delivers if the lead can *see* what each harness's teammate is doing at native fidelity. The lead should never need to read tmux pane stderr to understand teammate state. Errors should never collapse to a generic prose fallback — they should carry diagnostic detail comparable to native errors. Host-tool activity inside the routed CLI should surface to the lead.

### 1.3 §3 peer efficiency: the operational consequence (peer-facing)

`CLAUDE.md:63-80`. Native Claude teammates collaborate at low friction — they `SendMessage` each other directly, hand off tasks atomically, steer each other mid-turn, and idle notifications don't drown out substantive content. Routed teammates must match that. **The team is only as fast as its slowest peer-to-peer hand-off.**

§3 protects the *coordination fidelity* among peers themselves — a distinct surface from §2 with its own failure modes. Concrete failure modes per CLAUDE.md:71-76:

- **Peer-DM gaps** — wrapper MCP must expose `send_message` to peers, not only to lead. Failure: "I don't have access to a `send_message` MCP tool" error prose instead of clean delivery (observed during the proto-rev research session itself).
- **Idle noise crowding signal** — heartbeat idle notifications must be distinguishable from substantive peer messages. Consumers must filter cheaply by message kind, not by parsing prose.
- **Steer authorization too narrow** — lead-only steering blocks peers from interrupting each other on legitimate work. A Codex executor should be able to steer a Codex researcher mid-turn when the lead is offline. Steer authorization is itself a capability declaration (per §1).
- **Capability discovery latency** — manifest must be cached at team formation, not lazily fetched per invocation. Peers should not pay round-trip cost every time they consider invoking a peer's primitive.
- **Task hand-off race conditions** — atomic claim semantics must extend to peer-initiated reassignments, not only lead-initiated ones.
- **Double-send / canned-fallback noise** — the PR-#11/#12 "delivered_via_tool" guards must be uniform across all backends so peers don't receive a structured reply *and* a canned prose fallback for the same answer.

§3 is a distinct lens from §2. §2 asks "what does the lead see?"; §3 asks "what do peers see and how fast is the hand-off?" A change can narrow the §2 visibility gap while widening the §3 efficiency gap (e.g., shouting every event into peer inboxes for visibility crowds out substantive coordination). They balance, but neither yields to the other.

The leak ledger gains a new tier (§3.8 — peer-efficiency leaks PE-1..PE-5) for issues distinct from lead-visibility / capability-flatten.

### 1.4 The lens

For every existing pattern, leak, or proposal in this document, the lens is:

- **§1: Does this preserve every harness's unique capabilities, or does it flatten them?** Flatten = no.
- **§2: Does this narrow the visibility gap or widen it?** Widen = no.
- **§3: Does this change peer-to-peer coordination fidelity?** Match native Claude's pace.
- **Is this a "leak" or a "missing capability declaration"?** Heterogeneity that faithfully preserves a harness capability is not a leak; the leak is the absence of declaration.

When §1 and §2/§3 conflict, §1 wins per `CLAUDE.md:90-92`: "If you ever face a tradeoff between cleaner / more uniform protocol surface and preserves a unique harness capability, you choose the harness capability and grow the protocol to carry it. If you face a tradeoff between fewer wire-message types and peer can distinguish heartbeat from substance without parsing prose, you grow the typed-message catalog." Both subordinate to the moat; both compete with each other only when the moat is intact.

---

## 2. Elegance patterns worth preserving

### E0. Capabilities flow through; the protocol normalizes only what's truly common (load-bearing)

This is the §1 architecture made concrete in code. Evidence:

- The spawn shim (`src/claude_anyteam/spawn_shim.py:269-315`) is a pure dispatcher. It parses `--agent-name`/`--team-name`, applies a name-prefix regex, builds adapter argv, and `os.execv()`'s. It never sees what App Server / ACP / native-skills do. The shim has no awareness of the harness it's launching beyond the binary name. This is the right level of abstraction: routing without translation.
- The wrapper `EXPOSED_TOOLS` (`src/claude_anyteam/wrapper_server.py:54-68`) is intentionally minimal — six team-coordination tools plus seven shadow host tools, deliberately *not* a feature superset of every backend's own tool surface. Each backend keeps its own tool/agentic loop intact.
- `protocol_io.py` knows nothing about App Server / ACP / NDJSON. Backend autonomy is preserved at the seam between the protocol layer and the per-backend `invoke.py` modules.
- The five-tier `--effort` + `--model` pass-through (`docs/architecture.md:134-155`) is the *positive* protocol-side normalization — chosen surgically because it's truly common. The architecture doc explicitly justifies why this is the only kind of normalization we accept: standardize the surface where it maps cleanly; lossy mapping at the adapter boundary; never invent backend-specific user-facing knobs (no `--gemini-thinking-budget`, no `--kimi-thinking`).

### E1. One-sink JSON envelope: `send_json_to_lead`

`src/claude_anyteam/protocol_io.py:164-177`. `send_json_to_lead()` renders any pydantic model with `.model_dump_json()` into `InboxMessage.text`. It is the single seam for all outbound structured payloads — `IdleNotificationOut`, `TaskCompleteOut`, `PermissionRequestOut`, `PlanApprovalRequestOut`, `ShutdownResponseOut`, ad hoc dicts (`task_blocked`) all flow through it. Adding a new outbound message type requires a Pydantic model and one call site; nothing else.

### E2. Tagged-union inbound parser: `parse_protocol_text`

`src/claude_anyteam/messages.py:156-186`. Discriminator on `type`/`kind`. Adding a new inbound message means adding a Pydantic model and one branch in the dispatcher. The parser never raises on malformed input — unrecognized payloads become prose, which preserves the "be liberal in what you accept" half of Postel's law for forward compatibility.

### E3. `read_own_inbox` invariant guard

`src/claude_anyteam/protocol_io.py:59-75`. The `assert agent_name == self_name` turns "don't clobber another teammate's inbox" into a precondition. The mark-as-read path rewrites the entire inbox file with the protocol's Pydantic serializer; touching another teammate's inbox would strip harness-written fields. The assert catches this at call time, not after the fact. Codex-substrate (05) flagged the underlying schema fragility (`InboxMessage` has no `extra="allow"`); our assert is the local defense until the substrate is fixed.

### E4. `BackendMetadata` + idempotent self-healing `register()`

`src/claude_anyteam/registration.py:29-37, 96-187`. `BackendMetadata` is a frozen dataclass each backend customizes (Codex `model="codex-cli"`, Gemini `model="gemini-cli"`, Kimi `model="kimi-cli"`). The `register()` self-heals stale `agentType`/`backendType` rows via diff detection (`registration.py:122-139`) — the PR-#11 unblocker for the `agentType="general-purpose"` race that the lead can't otherwise repair without a respawn. Idempotent and safe to call repeatedly.

### E5. Spawn shim is a pure dispatcher

`src/claude_anyteam/spawn_shim.py:269-315`. Name-regex routing → argv build (with per-agent file overrides) → `os.execv()`. No state, no daemon, no LLM. The shim's job ends the moment the adapter binary takes over. This is the right shape for a name-prefix dispatcher and is the clearest expression of E0 in the codebase.

### E6. Five-tier `--effort` + `--model` pass-through (the *only* protocol-side normalization)

`docs/architecture.md:134-155`. Codex maps identity (graded `model_reasoning_effort`); Gemini maps to thinking-budget aliases; Kimi maps to `--no-thinking` / on (lossy: `xhigh` and `medium` collapse). The lossy mapping is documented and chosen at the boundary, not the call site. The architecture doc explicitly justifies why this is the right shape: "standardize the surface, accept the lossy mapping at the edge." This is the canonical example of E0: protocol-side normalization where it maps cleanly; everything else flows through unmodified.

### E7. `SteerQueue` thread-safe channel

`src/claude_anyteam/codex.py:908-936`. Minimal one-way FIFO between the inbox poller (main thread) and App Server's polling loop (worker thread). No backpressure, no ordering guarantees beyond FIFO. Messages drop on the floor if the turn ends before delivery — the same failure mode as a race-lost SendMessage. Just enough mechanism, no more.

### E8. `identity_env` everywhere

`src/claude_anyteam/env.py` (referenced from `codex.py:419`, `wrapper_server.py:123-124`). Single helper writes both `CLAUDE_ANYTEAM_*` and legacy `CODEX_TEAMMATE_*` env vars. The wrapper has a parallel CLI-flag fallback (`wrapper_server.py:84-131`) for the App-Server-doesn't-forward-env discovery. Backwards compatibility carried in one place, not scattered.

### E9. Closed `ERROR_CLASSES` taxonomy in `diagnostics`

`src/claude_anyteam/diagnostics.py:117-125`. `classify_failure()` keeps the prose-fallback's `error=` field stable across backends. The lead can grep `error=turn_timeout` regardless of which adapter emitted it. `inc-<8 hex>` ids embedded in user-facing prose (`fallback_message()`) so the lead can run `claude-anyteam diagnose --incident <id>` to recover full context.

### E10. `EXPOSED_TOOLS` / `BLOCKED_TOOLS` symmetric allowlist

`src/claude_anyteam/wrapper_server.py:54-81`. Both lists are tested against the implementation set; adding/removing requires intent. `team_delete`/`force_kill_teammate`/`spawn_teammate`/`process_shutdown_approved`/`check_teammate` cannot reach the model by construction. Hallucinated tool calls to destructive operations are structurally impossible from a routed teammate — the wrapper doesn't expose them at all.

### E11. `feature_test(mcp_probe=True)` at adapter startup

`src/claude_anyteam/codex.py:196-251`. The MCP probe runs at adapter startup, not lazily mid-task. If the wrapper binary isn't on PATH, or `build_server()` raises on identity lookup, the adapter fails fast with a clear error. The first task doesn't burn API tokens to discover the wrapper is broken.

### E12. `_handle_prose` "delivered_via_tool" guard

`src/claude_anyteam/loop.py:265-267`, `src/claude_anyteam/backends/gemini/loop.py:289-291`, `src/claude_anyteam/backends/kimi/loop.py:260-262`. When a backend delivered the prose reply via the `send_message` MCP tool (so `last_message` is empty by design), the canned-fallback double-send is suppressed. PR #11 + PR #12 added this in two of three places; the third is the same shape. It's E12 as a pattern even though the implementation is duplicated — see L6 for the corresponding leak.

### E13. Hybrid Option-2-plus-slice-of-Option-4 architecture (deliberate composition)

Per `docs/internal/2026-prototype/protocol-spec.md` § 7.2's four-implementation taxonomy, we are Option 2 (External Self-Registered Peer) for the team-protocol layer + a slice of Option 4 (MCP-bridged) for the inner tool surface. Worth calling out as a deliberate composition rather than an accident:

- Protocol participation stays file-based and host-agnostic (registration.py, protocol_io.py). Nothing about how we read inboxes, claim tasks, or send `task_complete` depends on Claude Code being the host.
- Per-task tool/permission/event surface uses MCP because that's where Codex App Server / Gemini ACP have native plumbing. The wrapper is FastMCP because Codex spawns it as a stdio MCP subprocess via `-c mcp_servers.<name>.command=...`; Gemini reads the same wrapper via `~/.gemini/settings.json`; Kimi reads it via `--mcp-config-file`.

Neither piece alone would work. Pure file-based (Option 1) loses MCP's bidirectional schema-validated tool dispatch. Pure MCP-bridged (Option 4) loses the file-protocol's host-agnosticism — the adapter would only run when Claude Code spawned it. The composition is what lets us preserve harness-native MCP semantics while remaining portable to non-Claude hosts.

---

## 3. The leak ledger

33 leaks organized by substrate tier. Each entry: file:line evidence, abstraction violated, elegant shape proposal, cross-cite to codex-substrate (05) where convergent.

### 3.1 Configuration tier

**L1. `BackendResult` is named `CodexResult`, the first-backend leak.**

`src/claude_anyteam/codex.py:52-65`. The result dataclass is `CodexResult` and lives in `codex.py`. Gemini and Kimi `invoke.py` modules import and return it (`backends/gemini/invoke.py:15`, `backends/kimi/invoke.py:24`). The name carries Codex history and creates the appearance that Gemini/Kimi adapters depend on Codex. Abstraction violated: every backend produces a backend-shaped result but they all wear the first-backend's name.

Elegant shape: rename to `BackendResult` (or `TaskInvokeResult`) and move to a backend-neutral module (`backends/result.py` or `protocol_io.py`). Keep `CodexResult` as a legacy alias for one release.

Convergent with codex-substrate (05) #10.

**L7. `TaskCompleteOut.codex_exit_code` carries Gemini/Kimi exit codes.**

`src/claude_anyteam/messages.py:153`. The field is hard-coded to `codex_exit_code` and Gemini/Kimi launchers pass their own exit code through it (`backends/gemini/loop.py:541`, `backends/kimi/loop.py:496`). The lead inspecting a Gemini `task_complete` sees `"codex_exit_code": 0` for a Gemini task. Confusing and wrong.

Elegant shape: rename to `backend_exit_code` and add a `backend` field to the envelope. Keep `codex_exit_code` as a backwards-compatible alias on the wire (Pydantic `populate_by_name` already enables this).

Convergent with codex-substrate (05) #10.

**L9. Parallel config systems: `~/.claude/teams/<team>/agents/<name>.json` vs `config.json`.**

`src/claude_anyteam/spawn_shim.py:107-115`. Per-teammate `model`, `effort`, and `turn_timeout_s` overrides live in an adapter-only sibling file. The host's `config.json` doesn't read it; native Claude teammates configure model elsewhere (via `--model` argv passed to `claude` at spawn). Two parallel config systems in the same `~/.claude/teams/<team>/` tree.

Per E6, `--effort` and `--model` are protocol-side normalizations — they should live in the canonical roster. The agents/ sidecar exists because we couldn't write to the host-managed `config.json` member entry without racing the host. But the architecture has now stabilized: the registration path's idempotent self-heal proves we can edit member rows safely.

Elegant shape: store `model` and `effort` (and any future protocol-side knob) in the member row in `config.json`, under a namespaced `adapter` object. Drop the agents/ sidecar.

Foundation crack flagged by team-lead. The two-config-systems design will compound as we add more protocol-side knobs.

**L21. Three independent `Settings` classes with no common base.**

`src/claude_anyteam/config.py` (Codex), `backends/gemini/config.py` (`GeminiSettings`), `backends/kimi/config.py` (`KimiSettings`). No shared base class. Each has its own `from_env()`, env precedence, and per-adapter knobs (Gemini `trust_mode`, Kimi `thinking`, Codex `app_server`, `turn_timeout_s`).

Per E0, harness-specific knobs SHOULD live on the per-backend Settings (Gemini's `trust_mode` is a Gemini ACP concept; Kimi's `thinking` is a Kimi CLI flag). But the protocol-side knobs (team_name, agent_name, cwd, poll_interval_s, color, plan_mode_required, model, effort) are duplicated.

Elegant shape: a `TeammateSettings` base class with all protocol-side knobs; per-backend classes extend it with harness-specific fields. ~80 lines of duplication eliminated.

Flagged by team-lead.

**L25 (RETRACTED). `backendType="in-process"` legacy naming.**

Earlier note claimed cs50victor's MCP "claude"/"opencode" canonical naming made our `"in-process"` legacy. **codex-extract (03) verified the host binary v2.1.119 uses `tmux|iterm2|in-process`**. Our value is canonical for the current Claude Code host. cs50victor's MCP impl appears to use different discriminator values for its own coordination layer (possibly to support OpenCode as an alternate host); that's their choice, not a host requirement.

The remaining L25-bis leak is real: we always set `backendType="in-process"` even though we are not actually in-process — we're a different binary entirely (`claude-anyteam`/`gemini-anyteam`/`kimi-anyteam`) that the host launches via the spawn-shim. The host's `"in-process"` value historically meant "runs in the leader process's memory." We are a child process that *registers as if* in-process so the host's TUI machinery treats us as native-shaped (per `docs/internal/spawn-research-findings.md`). It works; it's also a polite lie.

Elegant shape: a future host could grow a `backendType="external-cli"` value that means "child process registered via `CLAUDE_CODE_TEAMMATE_COMMAND`, mirrored into AppState.tasks like an in-process teammate." Until then, `"in-process"` is the only working discriminator and we keep using it. Document the lie in a comment on `registration.py:151`.

### 3.2 Inbox / wire tier

**L10. `task_blocked` has no Pydantic model.**

`src/claude_anyteam/protocol_io.py:201-216`. All three loops build a raw dict inside `send_task_blocked()`. It's a documented payload that escaped the schema layer. Compare the Pydantic-modelled `TaskCompleteOut`/`PermissionRequestOut`. The wire shape is `{"kind": "task_blocked", "task_id": ..., "reason": ...}`.

Elegant shape: add `TaskBlockedOut` Pydantic model in `messages.py` next to `TaskCompleteOut`; route through E1's one-sink envelope.

Convergent with codex-substrate (05) #4. They go further and propose elevating `blocked` to a first-class task status (currently only `pending|in_progress|completed|deleted` per `claude_teams/models.py:82-87`); the adapter encodes blocking as `activeForm = "blocked: ..."` plus metadata while status stays `in_progress`. We should adopt their proposal: add `blocked` to the task-status enum, keep `task_blocked` as the transition event.

**L11. `SteerIn` has dual wire formats.**

`src/claude_anyteam/messages.py:164-165`. `parse_protocol_text` accepts both `text[:6].lower() == "steer:"` (legacy bare-prefix form) AND the JSON `type=="steer"` shape. The legacy was kept because `team-cli` still emits it. Two wire formats for the same logical message.

Elegant shape: emit only one canonical form (JSON); accept both at the parser edge with deprecation logging on the legacy form. After two releases, drop the legacy parse.

**L12. `read_inbox(mark_as_read=True)` rewrites the entire inbox with the protocol's serializer.**

`claude_teams/messaging.py:53-70`. The mark-as-read path deserializes all messages to `InboxMessage`, mutates `read`, and rewrites. `InboxMessage` has no `extra="allow"`, so the rewrite strips any unknown fields — including ones the host might write (e.g. `messageId`, `replyTo`) in a future Claude Code release.

Codex-substrate (05) #2 surfaces the deeper root cause: it's a Pydantic-strict-model issue, not just a file-rewrite issue. The protocol semantically wants "ack this message" but is implemented as "rewrite the array using my own schema." The single-writer-per-inbox invariant is enforced by `read_own_inbox`'s assert (`protocol_io.py:59-75` — see E3), not by the protocol.

Elegant shape: give every `InboxMessage` a stable `messageId` (uuid). Replace `mark_as_read` with `ack_messages(ids)` that updates `read` by raw dict/index without re-serializing. Preserve all unknown fields verbatim. This is a substrate change in `claude_teams`; until then, our assert is the only defense.

**L20. The wrapper MCP `read_inbox` tool mutates the inbox by default.**

`src/claude_anyteam/wrapper_server.py:317-334`. A subagent calling `read_inbox()` via MCP marks unread messages read on the way out. If a model uses the tool to peek at peer replies during a prose-reply turn, it destroys read-state for the next legitimate poll.

Elegant shape: rename to `read_inbox_unread` or split into a non-mutating view by default plus an explicit `mark_inbox_read(ids)` tool. The current behavior is a footgun for routed teammates who use the inbox tool exploratively.

**L11-bis. `type` (inbound) vs `kind` (`TaskCompleteOut` outbound) discriminator inconsistency.**

`src/claude_anyteam/messages.py:142-154` uses `kind="task_complete"`; every other model uses `type=...`. `parse_protocol_text` accepts both (`messages.py:173: t = raw.get("type") or raw.get("kind")`). The inconsistency is invisible to the parser but visible to anyone reading the wire.

Elegant shape: pick one (recommend `type` since it matches the host binary's catalog per codex-extract 03), migrate `TaskCompleteOut` to use it, accept both at the parser edge with a deprecation log.

**L31. JSON Schemas exist for 4 of ~10 wire payloads.**

`src/claude_anyteam/schemas/`. Schemas exist for `task-complete.schema.json`, `plan.schema.json`, `permission_request.schema.json`, `permission_response.schema.json`. None for `idle_notification`, `steer`, `task_blocked`, `shutdown_*`, `plan_approval_*`. The "validate-at-the-edge" pattern of E1/E2 is enforced by Pydantic types in process; nothing on the wire is schema-checked for the unmodelled half.

Elegant shape: emit JSON Schemas for every wire envelope at build time from the Pydantic models. Validate inbound under a debug flag; validate outbound always. This becomes the SchemaPack the kit ships with (see § 6.5).

Convergent with codex-substrate (05).

**L34. `<system_reminder>` body mutation in vendored server but not in wrapper.**

`claude_teams/server.py:253-261, 403-410`. The vendored server's `send_message()` appends a `<system_reminder>` to direct/broadcast message bodies. The wrapper's `send_message()` (`wrapper_server.py:235-243`) writes the body exactly as given. Two senders writing to the same inbox produce different shapes — leads (using vendored MCP) get reminder-decorated text; routed teammates (using the wrapper) get raw text.

Elegant shape: move sender/reminder metadata into structured fields on `InboxMessage`, not mutated text. Both senders write the same shape; rendering decides whether to show reminders.

Cross-cited from codex-substrate (05).

**L25-bis. `messages.py` models 8 of ~13 host-recognized structured mailbox types.**

Per codex-extract (03)'s host-binary catalog, Claude Code v2.1.119 recognizes:

- `idle_notification`, `permission_request`, `permission_response`, `sandbox_permission_request`, `sandbox_permission_response`, `shutdown_request`, `shutdown_approved`, `shutdown_rejected`, `team_permission_update`, `mode_set_request`, `plan_approval_request`, `plan_approval_response`, `task_assignment`.

We model: `task_assignment` (in), `shutdown_request` (in/out), `plan_approval_request` (in/out), `plan_approval_response` (in), `permission_request` (out), `permission_response` (in), `steer` (in), `idle_notification` (out), `task_complete` (out via `TaskCompleteOut`), `task_blocked` (out raw dict).

Missing or mismatched:
- `sandbox_permission_request`/`sandbox_permission_response` — host-side host-pattern-allow flow we don't bridge.
- `team_permission_update` — host-recognized, we don't emit or parse.
- `mode_set_request` — host-recognized (lead changing teammate's mode), we don't parse.
- `shutdown_approved` vs our `shutdown_response` — we send `shutdown_response` per `messages.py:58-69`; host catalog has `shutdown_approved`/`shutdown_rejected` as distinct types. We may be sending a payload the host's `isStructuredProtocolMessage` recognizer doesn't enumerate (per codex-extract 03 §"Mailbox protocol messages").
- Hook events: host fires `TeammateIdle`, `TaskCreated`, `TaskCompleted` hook events when the corresponding mailbox messages arrive. Our adapter doesn't validate that our outbound payloads conform to whatever the host's `isStructuredProtocolMessage` allowlist requires.

Elegant shape: align with the host's catalog. Audit each of our outbound payload shapes against the v2.1.119 binary's recognizers (codex-extract 03 has the offsets). Rename `ShutdownResponseOut` to either `ShutdownApprovedOut`/`ShutdownRejectedOut` (host-aligned) or document why the host accepts our shape. Add models for `team_permission_update` and `mode_set_request` so we can at least parse them rather than dropping them as prose.

**L26. Silent wire-schema growth without versioning.**

The protocol-spec.md v1.1 (`docs/internal/2026-prototype/protocol-spec.md`) lists 6 protocol message kinds. Our `messages.py` defines 10. We've added `task_complete`, `task_blocked`, `permission_request`/`response`, `steer` without amending the spec or versioning the wire.

Per the strategic-roadmap, Phase 2 ("stop being a Claude Code plugin") and Phase 3 (non-Claude host bindings — Cursor / OpenCode) require a versioned wire that other implementations can target. We are growing the wire silently.

Elegant shape: every outbound payload carries `schema_version: 1`. When we add a new kind, increment the spec version, document the addition, and ship a JSON Schema. The kit (§6) makes this automatic — `agent_card()` reports the wire version; `peer_prompt_fragment()` includes it; readers can hard-fail on `schema_version > supported`. Cross-cite to opus-vision (10): `Schema versioning is mandatory from day one, not bolted on`.

### 3.3 Task tier

**L27. Vendored writers don't share the registration lock.**

`claude_teams/teams.py:158-172`. `add_member()` and `remove_member()` are read-modify-write WITHOUT a lock. Anyteam uses `inboxes/.lock` (`registration.py:58-69`); vendored config writers don't share that lock. Concurrent vendored + anyteam writes can lose updates. Real concurrency bug.

Elegant shape: introduce `~/.claude/teams/<team>/config.lock` and require every config read-modify-write path (vendored + anyteam) to use it. Add `configVersion` and CAS write for callers that want optimistic concurrency.

Surfaced by codex-substrate (05) — important enough to lift verbatim. Foundation-level concurrency bug; the host binary doesn't currently exercise it because Claude Code's spawn is serial, but third-party teammates (us) can.

**L28. Member discriminator is implicit (presence/absence of `prompt`).**

`claude_teams/models.py:48-62`. Whether a config member is a `LeadMember` or `TeammateMember` is inferred from whether the raw object has a `prompt` key. Brittle for downstream readers and host-binding ports — a TS port of the protocol would have to replicate the exact discriminator logic.

Elegant shape: add an explicit `role: "lead" | "teammate"` field. Preserve the `prompt`-key fallback for backward compatibility with old configs.

Cross-cited from codex-substrate (05).

**L33. `_filelock` has no timeout/staleness policy.**

`claude_teams/_filelock.py:9-12`. `filelock.FileLock(str(lock_path))` is called with no timeout or staleness handling. A crashed teammate holding the inbox or task lock would deadlock the next adapter startup.

Elegant shape: wrap with a 30-second default timeout. On timeout, log `lock.stale_suspected` with the lock path and the holder's PID (if recoverable from `/proc/locks`) and either fail loudly or proceed with a force-acquire. Cross-cite to opus-roadmap (09): this is a P1 reliability item.

**L32. Wrapper `task_update` doesn't validate new `owner` is a team member.**

`src/claude_anyteam/wrapper_server.py:267-285`. The wrapper checks the existing owner is the caller (or unowned), then calls `tasks.update_task()` with the new `owner` value. The vendored server-side path `claude_teams/server.py:572-579` validates owner-membership; the wrapper skips that check. A routed model can `task_update(owner="ghost-name")` and quietly corrupt task state.

Elegant shape: delegate to `claude_teams.server`'s validated update path, or replicate the owner-membership check at the wrapper boundary. Add a regression test.

Cross-cited from codex-substrate (05).

**L29. `task_get` is unclassified narrowing.**

`src/claude_anyteam/wrapper_server.py:54-81`. The vendored server exposes `task_get` (`claude_teams/server.py:612-619`); the wrapper exposes only `task_list()`. `task_get` is missing from both `EXPOSED_TOOLS` and `BLOCKED_TOOLS` — neither exposed nor explicitly blocked. This breaks the symmetric-test invariant (E10): adding/removing a wrapper tool should require deliberation, but `task_get` slipped through.

Elegant shape: classify it. Either expose `task_get` (it's read-only, no harm) or add to `BLOCKED_TOOLS` with a comment explaining why `task_list` is sufficient for routed models.

Cross-cited from codex-substrate (05).

### 3.4 Lifecycle tier

**L6. Prose double-send guard duplicated across three loops.**

`src/claude_anyteam/loop.py:265-267`, `src/claude_anyteam/backends/gemini/loop.py:289-291`, `src/claude_anyteam/backends/kimi/loop.py:260-262`. Each backend's `_handle_prose` repeats the same logic: "if reply is None and result.exit_code == 0 and tool_call_events > 0: return." PR #11 added it for Kimi; PR #12 added it for Codex; Gemini was added later. No shared helper.

Elegant shape: add `_should_skip_prose_fallback(result) -> bool` as a shared protocol_io helper. Each backend's `_handle_prose` calls it once. Single place to fix when the heuristic needs refinement (e.g. recognizing "tool delivered the reply" via richer signals than `tool_call_events > 0`).

Convergent with codex-substrate (05) #126.

**L13. Diagnostics directory is adapter-private.**

`~/.claude/teams/<team>/diagnostics/<agent>/<incident_id>.json` — written by `diagnostics.record_incident()` (`diagnostics.py:38-46`). The host doesn't know. The CLI `claude-anyteam diagnose` is required to read it. Native Claude crashes don't write here.

This is parity-violating in the *opposite* direction — we provide more visibility for routed crashes than the host does for native crashes. That's not necessarily bad (extra visibility is good per §2), but it's a divergence the kit should formalize.

Elegant shape: keep the diagnostics path, but write incidents through the same event-log channel (§3.7 / §6) so a future host UI can render them uniformly. The `incident_id` becomes a field on `turn_failed` envelopes.

**L14. `incident_id` is embedded in user-visible prose, not a protocol field.**

`src/claude_anyteam/diagnostics.py:152-163`. `fallback_message()` creates `"...incident=inc-abc12345..."` and bakes it into `InboxMessage.text`. The lead is supposed to grep prose to recover the diagnostic. A first-class `incident_ref` field on the envelope would let UIs link directly.

Elegant shape: add `incident_ref` field to `InboxMessage` (or to the `turn_failed` envelope per §6). Keep the in-prose mention as a fallback for plain-text rendering.

**L24. Test seam via module monkeypatch.**

`src/claude_anyteam/backends/gemini/loop.py:24` (`invoke = headless_invoke`), `src/claude_anyteam/backends/kimi/loop.py:24`. Comment says: "Backwards-compatible alias for tests/extensions that monkeypatch loop.invoke." The test seam is the per-backend module name — meaning tests and the runtime see different canonical types depending on monkeypatching state.

Elegant shape: a protocol-side runner that's a class with overridable `invoke()` would let tests hold a single seam (the runner instance). Until the kit lands, document the alias explicitly so future contributors don't accidentally remove it.

### 3.5 Wrapper / MCP tier

**L8 (RECLASSIFIED). Shadow MCP tools have backend-specific prompt policies.**

(Originally L8; reclassified to CD-4 in §4.) Codex prompts don't list `mcp_anyteam_*` shadow tools; Gemini prompts do (`backends/gemini/prompts.py:16-23`); Kimi prompts use Kimi built-ins (`backends/kimi/prompts.py:20-25`). Same wrapper, three policies. Per §1, this is intentional — each backend's prompt naturally addresses its native host-tool surface. The leak is the absence of capability declaration, not the heterogeneity. See CD-4.

**L18. Tool-name extraction is best-effort across three event shapes.**

`src/claude_anyteam/codex.py:138-154` tries `name`, `tool_name`, `function_name` at top and inside `item`. Gemini's parser uses `tool_name` (`backends/gemini/invoke.py:463`). Kimi tries `function.name` then `name` (`backends/kimi/invoke.py:292-300`). Three event shapes, three readers. No normalized "tool ID" the protocol promises.

Elegant shape: each backend's `invoke.py` normalizes tool events into a shared `ToolEvent` shape (per the B9 §6 envelope). The protocol carries `{tool_name, category, phase, status}` regardless of backend. Codex's `_record()` (`codex.py:658-677`) does part of this for App Server; Gemini ACP's `_normalised_tool_event` (`backends/gemini/acp.py:182-227`) does part of it. Lift both into a backend-neutral helper that all three call.

**L19. Gemini ACP has its own `_normalised_tool_event` taxonomy.**

`src/claude_anyteam/backends/gemini/acp.py:182-227`. ACP `session/update` payloads are rewritten into `{type: "tool_use" | "tool_result"}`. Closer to neutral than Codex's substring-counting, but still local — it doesn't feed the lead's mailbox or any cross-backend log.

Elegant shape: subsume into the kit's `emit_tool_event()` primitive (§6). One taxonomy per the B9 envelope; each backend's parser feeds it.

**L22. `JsonRpcStdioClient.handle_server_request` defaults to None — two backend contracts wedged into one hook.**

`src/claude_anyteam/jsonrpc_stdio.py:272-278`. Codex never receives server-originated requests, but we silently ignore unknown ones rather than asserting. Gemini ACP overrides the same hook (`backends/gemini/acp_client.py:254-322`) for permission. Two backends, two contracts wedged into the same default-None hook.

Elegant shape: make the hook abstract (or raise NotImplementedError on unhandled methods by default). Each backend explicitly registers handlers for the methods it supports; unknown methods become `visibility_degraded` events. Cross-cited from team-lead.

**L23. `AppServerClient.thread_start` returns either `result["thread"]["id"]` or `result["threadId"]`.**

`src/claude_anyteam/app_server.py:73-74`. Codex App Server has shipped both shapes; we accept both. The parser doesn't emit a `visibility_degraded` if it falls back. Silent format drift detection.

Elegant shape: emit `visibility_degraded` with `surface: "app_server.thread_start_shape"` when we hit the fallback. Let the lead see when the host upgrades and our parser is in compat mode.

### 3.6 Event log / visibility tier

**L17. No append-only event log; host-tool/agent-message events visible only in stderr.**

The B9 §6.4 envelope proposal (`bug-triage/B9-visibility-parity-investigation.md:865-899`) is unrealized. Host-tool events from Codex App Server (`commandExecution`, `fileChange`, `webSearch`) are *counted* in `tool_call_events` (per the v0.6 substring fix at `codex.py:80-88`) but never fanned out as structured events to the lead. Direct contradiction of `CLAUDE.md:30-39` (the §2 north star).

The cost is measurable: B9 §7 documents a 14m36s incident where a Codex App Server turn produced 383 events, 0 tool_call_events, and no lead-visible signal. The lead saw silence because the `agentMessage` byte count never grew and we have no other surface to fan out App-Server-internal activity.

Elegant shape: implement B9 §6.4. Append-only `~/.claude/teams/<team>/events/<agent>.jsonl`; envelope shape per B9 §6.2-§6.3 (`turn_started`, `turn_progress`, `tool_event`, `artifact_event`, `turn_completed`, `turn_failed`, `visibility_degraded`); fan-out policy per channel (stderr always; event log always; mailbox rate-limited; task `activeForm` for transitions). The kit's `Teammate.emit_*` primitives (§6) make this automatic for every backend.

Convergent with codex-substrate (05) #5.

**L17-bis. Wrapper shadow tools emit no events.**

`src/claude_anyteam/wrapper_server.py:346-581`. The seven shadow tools (`mcp_anyteam_shell`, `read_file`, `write_file`, `list_directory`, `edit_file`, `search`, `web_fetch`) execute and return outputs to the backend, but write no event/log/inbox record themselves. A routed model running 50 file edits via the wrapper produces zero lead-visible signal beyond the eventual `task_complete` summary.

Elegant shape: decorate every `EXPOSED_TOOLS` handler with start/completed/failed visibility-event emission, including shadow host tools, with redacted previews and output-size caps. Per B9 §6.3 `tool_event` payload shape.

Convergent with codex-substrate (05) #6, B9 §6.3 line 691-715.

**L17-ter. No `turn_progress` for headless backends.**

Gemini headless (`backends/gemini/invoke.py`) and Kimi (`backends/kimi/invoke.py`) use blocking `subprocess.run(..., capture_output=True, timeout=...)`. They cannot stream progress mid-turn — by the time `_parse_stdout()` runs, the subprocess has exited. The lead sees silence for the entire turn.

Per §1, this is the harness's actual liveness capability — Gemini headless legitimately can't stream. Per §2, we still owe the lead a signal. The shape: emit `turn_started` before subprocess.run, emit `turn_completed`/`turn_failed`/`turn_digest` after it exits (with full event count and last-message preview). Don't fake live progress.

Elegant shape: B9 §6.5 implementation sequence step 3. The kit's `emit_turn_started` / `emit_turn_completed` primitives make this trivial.

### 3.7 Schema versioning / drift tier

**L30. `minimal` effort tier drift between Codex and Gemini.**

`src/claude_anyteam/config.py:129-134` (Codex CLI) accepts `low|medium|high|xhigh` only — `minimal` raises ValueError. `backends/gemini/config.py:22-23` accepts `minimal|low|medium|high|xhigh`. The architecture doc (`docs/architecture.md:120-137`) promises five tiers as the protocol-side enum.

Direct violation of E6 ("standardize the surface"). The architecture doc also says (`docs/architecture.md:127`) Codex "accepts the five-tier enum natively, so the mapping is identity" — but the CLI parser drops the lowest tier.

Elegant shape: either add `minimal` to Codex (mapping to whatever Codex's lowest reasoning tier is) or amend the protocol enum to four tiers. Per the strategic-roadmap, the enum is the protocol's contract; the contract should match the implementation everywhere.

Surfaced by codex-substrate (05).

**L11-tris. Anti-affinity between vendored server and wrapper send_message.**

L34 covered the `<system_reminder>` mutation difference. Beyond that, the vendored server `send_message()` (`claude_teams/server.py:350-373`) multiplexes a `type` enum (`message`, `broadcast`, `shutdown_request`, `shutdown_response`, `plan_approval_response`); the wrapper's `send_message` (`wrapper_server.py:178-243`) only sends plain text via `type="message"`. Two senders implementing different subsets of the same tool name.

Per §1 this is correct: routed teammates shouldn't send shutdown_request (lifecycle stays in adapter). But the API shape divergence is implicit. A model writing prompts that mention `send_message` against the wrapper docs has no idea the vendored server has a richer schema.

Elegant shape: rename the wrapper tool to `peer_message` to disambiguate. Document that `peer_message` is the wrapper's narrow form of `send_message`; the protocol's full `send_message` lives only in the lead-side server.

### 3.8 Peer-efficiency tier (§3 surface — distinct from lead-visibility leaks)

These leaks are distinct from §3.1-§3.7 entries. The §3.1-§3.7 entries are "the lead can't see X" or "the protocol drops X." The §3.8 entries are "peer-to-peer coordination is slower than native Claude's." Per CLAUDE.md:65-78, the team is only as fast as its slowest peer-to-peer hand-off — and the proto-rev research session itself surfaced concrete failures.

**PE-1. Wrapper MCP `send_message` exposure inconsistency.**

Multiple codex teammates in the proto-rev research session (2026-04-27) couldn't peer-DM because their wrapper MCP `send_message` exposure varied. Symptom: error prose like "I don't have access to a `send_message` MCP tool" instead of clean delivery. The wrapper instance the routed backend has access to is supposed to expose `send_message` per `wrapper_server.py:54-68` (it's in `EXPOSED_TOOLS`). When some sessions saw it and others didn't, the divergence is somewhere between adapter spawn (`spawn_shim.py`), the wrapper's MCP handshake (`feature_test(mcp_probe=True)` at `codex.py:248-251`), and the host's MCP-client implementation in the backend CLI.

This is a §3 leak even though the underlying cause might be a §2/transport bug — the visible symptom is "peer-to-peer coordination drops to zero because the tool isn't available."

Elegant shape: at adapter startup, the kit's `feature_test` (extending E11) must verify the wrapper exposes ALL declared tools, not just import-OK. Fail loudly if `send_message` is missing — peer DMs are not optional. The kit's acceptance test (per codex-prototype task #11) should include a "spawn two adapters, peer-DM in both directions, both succeed" smoke test.

**PE-2. Idle notifications crowd substantive peer messages.**

`src/claude_anyteam/loop.py:154-159`, `backends/gemini/loop.py:179-186`, `backends/kimi/loop.py:151-158`. Heartbeat idle (every 60s, hard-coded across three loops) writes the same `IdleNotificationOut` envelope as substantive idle ("idle, awaiting input on task X"). Consumers (lead, peers) cannot distinguish heartbeat from substance without parsing the `idleReason` prose field.

Per CLAUDE.md:72: "Heartbeat idle notifications must be distinguishable from substantive peer messages; clients (lead and peers) must filter cheaply by message kind, not by parsing prose."

Elegant shape: split into two typed kinds.

```python
# Heartbeat — emitted every 60s while idle. Filterable; a peer can ignore.
{"type": "idle_heartbeat", "from": "...", "timestamp": "..."}

# Substantive — emitted at state transitions. Worth the lead's attention.
{"type": "idle_state", "from": "...", "timestamp": "...",
 "reason": "task_completed", "completedTaskId": "12", "summary": "..."}
```

Per CLAUDE.md:78 ("inbox event types are versioned so heartbeat / substantive / lifecycle can be distinguished by consumers without parsing prose"). The kit's typed envelope catalog (§6.5 SchemaPack) makes this trivial — every kind ships with a JSON Schema and consumers switch on `type`.

**PE-3. Steer authorization is lead-only (peer-to-peer steer blocked).**

`src/claude_anyteam/protocol_io.py:291` (permission_response), `src/claude_anyteam/backends/gemini/loop.py:213-214`, `src/claude_anyteam/backends/kimi/loop.py:185-186`. Gemini and Kimi's `_handle_steer` reject any sender that isn't `team-lead`. Codex App Server lacks the check (see CD-6). The default-deny policy means a Codex executor cannot steer a Codex researcher mid-turn even when both are working on the same task tree and the lead is offline or busy.

Per CLAUDE.md:73-74: "Lead-only steering authorization blocks peers from interrupting each other on legitimate work; a Codex executor should be able to steer a Codex researcher mid-turn when the lead is offline. Steer authorization is itself a capability declaration (per §1)."

This is both a leak (the lead-only restriction is a protocol-side flatten) AND a missing capability declaration (CD-6 partly captures it). The cleaner framing: collapse PE-3 into CD-6, and the v1 default in CD-6 should NOT be lead-only — it should be "peer-allowed unless the harness explicitly opts to lead-only." That inverts the current default. Per §1 the harness owns the policy; the protocol should not impose lead-only by default because that flattens the per-harness choice.

Elegant shape: CD-6 default flips. `turn_steer.authorization` defaults to `"any_peer"`; harnesses that need lead-only declare `"authorization": "lead_only"` explicitly. Existing Gemini/Kimi reject-non-lead becomes opt-in conservatism, not protocol default. Cross-cited from §3.5 / CD-6.

**PE-4. Double-send / canned-fallback inconsistency (peer view of L6).**

`src/claude_anyteam/loop.py:265-267` (Codex), `backends/gemini/loop.py:289-291` (Gemini), `backends/kimi/loop.py:260-262` (Kimi). All three backends now have the "delivered_via_tool" guard, but the heuristic (`tool_call_events > 0`) is fragile — a backend that calls a non-`send_message` tool *and* delivers no `last_message` will still skip the canned fallback (correct), but a backend that delivers `last_message=""` due to a different bug will also skip it (silent failure).

Already in the §3.4 lifecycle ledger as L6 (duplicated guard). Reframing as PE-4: the peer-efficiency cost is that when the heuristic gets it wrong, the peer either receives two replies (one structured, one canned) or zero. Both fail the §3 fidelity bar.

Elegant shape per CLAUDE.md:76: backends report `delivered_via_tool=True` explicitly on the `BackendResult`, instead of inferring from `tool_call_events > 0`. The kit's `TaskResult` adds a `delivered_reply: bool` field; backends set it when their tool-call event stream contains a `send_message` call addressed to the prose sender. Cross-cite to L6.

**PE-5. Capability manifest is fetched lazily, not pre-loaded at team formation.**

The §6.4 hybrid lookup story has `find_capability()` reading the flat list (cheap, frequent) and `capability_manifest()` going through the wrapper MCP for the rich entry (expensive, rare). "Rare" is correct *if* peer invocation of unique primitives is rare; in a team where peers actively invoke each other's specialty (e.g., a Claude lead routing tasks to Codex `thread/fork`-capable peers, or peer-to-peer steers), every invocation pays an MCP round-trip just to load the schema and `when_to_use` text into context.

Per CLAUDE.md:74: "Manifest must be cached at team formation, not lazily fetched per invocation." Per CLAUDE.md:78: "capability manifests are loaded into peer context at team-formation time."

Elegant shape: the kit gains a `Team.broadcast_capability_manifest()` primitive that runs at team formation and on every member-set change. Each `Teammate` caches peer manifests; lookups become O(1) local-dict reads. Stale-on-capability-change is solved by the `version` flag in the manifest entry — when a peer detects a `version` mismatch on a cached manifest, it re-fetches that single entry.

This is an additional kit primitive, detailed in §6.4.2 (added below).

**PE-6. Atomic peer-initiated task reassignment.**

`src/claude_anyteam/protocol_io.py:86-130` (`claim_task`). The CAS protocol supports peer claim of pending tasks (refuses non-pending tasks and tasks owned by someone else). But peer-to-peer *reassignment* — a Codex executor handing a task back to a peer because it discovered the work belongs there — has no atomic primitive. The executor must `update_task(owner=other_peer)`, which the wrapper allows (`wrapper_server.py:267-285`) but doesn't validate atomically against the current owner's state.

Per CLAUDE.md:75: "Atomic claim semantics must extend to peer-initiated reassignments, not only lead-initiated ones."

Elegant shape: extend `Storage.claim_task` (§6.2) into `Storage.transfer_task(from_owner, to_owner, expected_status, expected_owner)` — a CAS-protected ownership transfer that refuses if the source is no longer the owner or the target isn't a team member. Cross-cite to L32 (wrapper task_update doesn't validate target owner-membership).

---

**L11-quad. Member name validation is split across two paths.**

Per codex-extract (03) and codex-substrate (05) #88-89: team names must match `^[A-Za-z0-9_-]+$` and ≤64 chars; agent names match the same and cannot be `team-lead`. Enforced in `claude_teams/teams.py:24-50` and `claude_teams/spawner.py:99-106`. NOT enforced in `TeamConfig` Pydantic models (`claude_teams/models.py:65-74`). NOT enforced by the wrapper's `send_message(to=...)`.

So a routed model can `send_message(to="@lead")` (with the bad `@`) and the wrapper will dispatch it; the destination inbox path will sanitize differently than the lookup, the message lands in a wrong file, and the lead never sees it.

Elegant shape: validation in the type, not in the call site. A `TeammateName` newtype validated at construction. The kit's `Teammate(name=...)` constructor refuses bad names at startup, not at first send.

---

## 4. Missing capability declarations (CD section — the §1 obligation)

These are NOT leaks under the §1 lens. They are faithful harness preservation that lacks an explicit declaration. **Every CD-N below is a missing capability-layer declaration; transport-layer is fine.** The transport-layer correctly carries `SteerIn` envelopes, mailbox idle/shutdown messages, and permission requests/responses — those parts work. The capability-layer is the part that doesn't exist yet: peers and the lead have no way to know which teammates support live `turn_steer`, what shape to invoke it with, when invoking is right, and what failure modes to expect.

The shape of a complete capability-layer entry per CLAUDE.md:28-46 is:

```python
{
  "version": "1",                                  # peer degrades when shape changes
  "schema": {...},                                 # input contract (MCP-style)
  "description": "<one-sentence what>",
  "when_to_use": "<semantic guidance for invoking>",
  "when_not_to": "<failure-mode-prevention guidance>",
  "failure_modes": ["CLOSED_LIST_OF_NAMES", ...],  # what callers must handle
}
```

The kit's `agent_card()` primitive (§6) makes every CD entry tractable to fix: each is a per-capability dict in the Agent Card's `capabilities` field, with the flat list `members[].capabilities` derived as `list(agent_card()["capabilities"].keys())` for cheap roster discovery.

**CD-1. Soft non-progress watchdog: only `codex-app-server-*` self-monitors.**

`src/claude_anyteam/codex.py:740-834`. App Server's polling loop fires a `turn/steer` checkpoint after 300s of no observable progress (no `agentMessage` byte delta, no `tool_call_events` increment). Codex `exec`/Gemini ACP/Gemini headless/Kimi headless can't fire it — App Server is the only path with a polling event loop that interleaves watchdog checks with notification consumption.

Per §1 this is faithful harness preservation. The watchdog is a Codex App Server primitive, not a protocol guarantee. Transport-layer carries the resulting `turn_progress` event correctly when one fires; the capability-layer is missing — the lead has no roster-level signal of which teammates self-monitor.

Per-capability manifest entry (capability-layer; absent for backends without an event-loop transport):
```python
"soft_non_progress_watchdog": {
  "version": "1",
  "schema": {
    "type": "object",
    "properties": {
      "non_progress_warn_s": {"type": "integer", "default": 300, "minimum": 60, "maximum": 900},
    },
  },
  "description": "Adapter self-monitors for non-progress during a turn and emits a checkpoint steer + warn-event after the threshold.",
  "when_to_use": "Long-running exploratory tasks where silent stalls are likely; assign to a teammate with this capability so the lead doesn't need its own watchdog.",
  "when_not_to": "Short tasks with explicit deadlines; tasks where the model is expected to think silently for >5min before any tool call (the watchdog will fire spuriously).",
  "failure_modes": [
    "WATCHDOG_FIRED_NO_PROGRESS",        # checkpoint steer sent; turn continues
    "WATCHDOG_NOT_AVAILABLE_THIS_BACKEND" # capability absent on backend
  ]
}
```
Lead UX once declared: when assigning a long-form research task, prefer a teammate with `soft_non_progress_watchdog` declared. Without the capability layer the lead sees no difference between Codex App Server and Codex exec until a turn stalls and there's no checkpoint.

**CD-2. Live mid-turn `turn_steer`: codex-app-server and gemini-acp accept; gemini-headless and kimi queue for next-turn.**

`src/claude_anyteam/codex.py:807-836` (Codex App Server: `turn_steer()` injected into in-flight turn). `src/claude_anyteam/backends/gemini/loop.py:236-268` and `src/claude_anyteam/backends/kimi/loop.py:208-240`: Gemini headless and Kimi queue steer messages and inject them as next-turn prompt prefix.

Transport-layer is identical across all backends: the lead sends the same `SteerIn` envelope, it lands in the teammate's mailbox, the teammate's loop dequeues it. The capability-layer is what differs: live mid-turn for Codex App Server / Gemini ACP, next-prompt for the rest. Today the lead can't tell which is which without reading the codebase.

Per-capability manifest entry:
```python
"turn_steer": {
  "version": "1",
  "schema": {
    "type": "object",
    "required": ["text"],
    "properties": {
      "text": {"type": "string", "maxLength": 8192},
      "task_id": {"type": ["string", "null"]},
      "priority": {"type": "string", "enum": ["normal", "urgent"], "default": "normal"},
    },
  },
  "description": "Inject text mid-turn (or at next turn boundary) to redirect the model's reasoning.",
  "when_to_use": "When you observe the teammate pursuing a stale path; when a peer needs context delivered before the turn ends; when a discovered constraint must be applied to the in-flight task without restarting it.",
  "when_not_to": "Don't steer if a structured tool-call is in flight whose args depend on stable inputs; don't steer if the wall-clock budget is nearly exhausted; don't steer with low-information messages (e.g., 'are you done?') — that's idle-poll territory.",
  "failure_modes": [
    "RACE_LOST_NO_TURN_IN_FLIGHT",       # sent steer but no active turn — buffered or dropped depending on backend
    "STEER_BUFFERED_NEXT_BOUNDARY",      # backend (Gemini headless / Kimi) buffers to next turn boundary
    "STEER_AUTH_REJECTED",               # only team-lead can steer (CD-6)
    "STEER_PAYLOAD_OVERFLOW",            # text > 8192 chars; truncated with marker
  ]
}
```
The capability is declared by both backends that support it; the difference between live and next-turn is encoded as an additional `delivery_mode` field on the manifest:
```python
"turn_steer": { ..., "delivery_mode": "live" }       # codex-app-server, gemini-acp
"turn_steer": { ..., "delivery_mode": "next_turn" }  # codex-exec, gemini-headless, kimi
```
Peer-prompt fragment generated by the kit: "When you `turn_steer` `<name>`, delivery is `<delivery_mode>`. Live means the model receives your text mid-reasoning; next_turn means delivery happens at the next prompt boundary. Plan urgency accordingly."

**CD-3. Permission bridge: gemini-acp (non-trusted modes) only.**

`src/claude_anyteam/backends/gemini/acp_client.py:254-322`. Gemini ACP's `handle_server_request` routes `session/request_permission` through `protocol_io.send_permission_request_to_lead()` and waits for a `permission_response`. Codex runs `approval_policy=never` + `sandbox=danger-full-access` (`codex.py:692-707`); Kimi headless has no bridge.

Transport-layer carries `PermissionRequestOut` and `PermissionResponseIn` correctly when the bridge fires. The capability-layer is what's missing: each harness's approval posture is its own policy, and the lead currently has no signal of which teammates can be approval-gated.

Per-capability manifest entry:
```python
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
  "description": "Surfaces sensitive tool-use (host shell, file write) to team-lead for interactive approval before execution.",
  "when_to_use": "When assigning tasks that touch production paths, secrets, or external networks; when the lead wants to gate dangerous operations without relying on the harness's own sandbox.",
  "when_not_to": "Don't route to a permission-bridged teammate for routine read-only or test-suite tasks (the approval prompts add latency without value); don't expect approval-gating for harnesses with `approval_policy=never`.",
  "failure_modes": [
    "APPROVAL_TIMEOUT",                  # lead didn't respond within timeout; tool denied
    "APPROVAL_BRIDGE_ERROR",             # MCP/transport failure; tool denied
    "APPROVAL_CONTEXT_MISSING",          # required team_name/agent_name missing; tool denied
    "DENIED_BY_TEAM_LEAD",               # explicit deny from lead
  ]
}
```
Plus a sibling raw-policy field for backends without a bridge (so peers can reason about approval posture even when bridging isn't supported):
```python
"approval_policy": {
  "version": "1",
  "schema": {"type": "string", "enum": ["never", "default", "plan", "trusted"]},
  "description": "Static approval policy of the harness when no permission bridge is active.",
  "when_to_use": "Read-only roster signal — peers don't invoke this; the lead checks it before assigning approval-relevant work.",
  "when_not_to": "Not callable; informational only.",
  "failure_modes": [],
}
```
Lead UX: when assigning a task that touches sensitive files, query `find_capability("permission_bridge")` and route to a teammate that returns truthy. Otherwise read `approval_policy` to know whether the harness will run unconstrained.

**CD-4. Host-tool surface kind: codex-native vs mcp_anyteam_* vs kimi-native.**

Per §3.5 / L8. Each backend's prompt naturally addresses its native host-tool surface. Codex uses its own host shell + file tools (counted by the substring matcher in `codex.py:80-88`); Gemini routes through `mcp_anyteam_*` shadow tools (the wrapper at `wrapper_server.py:346-581`) because Gemini's built-ins competed with our visibility; Kimi prompts steer the model to Kimi-native tools.

Each is correct per §1 — this is faithful capability-layer differentiation. Transport-layer is unaffected; mailbox/task semantics are identical regardless of host-tool surface. The leak is the absence of declaration: the lead doesn't know which event-stream shape to expect from a teammate's `task_complete`, and peers don't know whether to reason about the harness's tool surface or the shadow surface when describing work to do.

Per-capability manifest entry:
```python
"host_tool_surface": {
  "version": "1",
  "schema": {"type": "string", "enum": ["codex-native", "mcp_anyteam", "gemini-native", "kimi-native"]},
  "description": "Identifies which tool surface the harness's reasoning loop uses for shell/file/web work — used by peers to know what kinds of activity to expect in event streams.",
  "when_to_use": "Read-only roster signal. Peers describing work to a teammate use this to phrase requests appropriately ('use Bash to run X' vs 'use mcp_anyteam_shell to run X').",
  "when_not_to": "Not callable; informational only.",
  "failure_modes": [],
}
```
Lead UX: when reading a teammate's `task_complete` summary or `tool_event` log, the lead knows whether the host-tool calls came from Codex's own surface (visible via the App Server `commandExecution`/`fileChange` events per E0 / L2 evidence) or from the wrapper's `mcp_anyteam_*` surface (visible via wrapper-emitted events per L17-bis). Different debuggability stories — the field tells the lead which one to use.

**CD-5. Steer expiration semantics differ across backends.**

`src/claude_anyteam/messages.py:130` declares `expires_after_turns: int = 1` as a `SteerIn` field. Backends interpret it differently:

- Codex App Server: the steer is injected mid-turn via `turn/steer`; no notion of "expiring after turns" because it was already delivered. Field is effectively ignored.
- Gemini headless / Kimi: the queued steer applies to the next claimed task only; it expires after one task transition (`backends/gemini/loop.py:240-244`).
- Gemini ACP: behavior is implementation-defined and currently undocumented.

This is a sub-capability of `turn_steer` (CD-2), not a free-standing one. Roll into `turn_steer.delivery_mode` and `turn_steer.expiry_semantics`:
```python
"turn_steer": {
  ...
  "delivery_mode": "live" | "next_turn",
  "expiry_semantics": "live_only" | "task_count" | "session_managed",
  ...
}
```
A peer reading the manifest sees: "delivery_mode=live, expiry_semantics=live_only" → "I send it once, it lands mid-turn, no need to think about expiry." Or "delivery_mode=next_turn, expiry_semantics=task_count" → "I set `expires_after_turns=N`, it applies to the next N task transitions, then drops." The semantic guidance is the difference between "I know this capability exists" and "I know how to use it correctly."

**CD-6. Steer authorization policy: lead-only vs any-peer.**

`src/claude_anyteam/backends/gemini/loop.py:213-214`, `src/claude_anyteam/backends/kimi/loop.py:185-186`. Gemini and Kimi's `_handle_steer` reject non-lead senders. Codex App Server's `turn_steer` doesn't enforce sender at all — anyone whose message lands in the inbox could trigger `_mid_turn_hook`'s prose path which `steer_queue.push`es. Inconsistent enforcement.

Per §1, this is a policy capability, not a technical limitation. Both shapes are reasonable: lead-only for safety; peer-allowed for richer team dynamics. The capability-layer should declare which. Today the difference is invisible — a peer could `send_message(to="codex-runtime", body="steer: refactor differently")` and have it work, or be silently rejected, with no signal of which.

Like CD-5, this rolls into `turn_steer` rather than standing alone:
```python
"turn_steer": {
  ...
  "authorization": "lead_only" | "any_peer",
  ...
  "failure_modes": [..., "STEER_AUTH_REJECTED"]
}
```
A peer reading "authorization=lead_only" knows their steer attempts will be rejected with `STEER_AUTH_REJECTED`. A reasonable default: lead-only for v1, with explicit opt-in for any-peer. Codex's current behavior is unintentional rather than chosen; the kit should make it explicit and align with the other backends unless we deliberately want any-peer steer for Codex App Server.

---

## 5. Race conditions and lock semantics (table, modeled on codex-substrate 05)

Surfacing the substrate's race surface so the kit's storage abstraction (§6.2) can specify mutual-exclusion guarantees rather than rediscovering them per port.

| Surface | Current semantics | Race / leak | Elegant recommendation |
|---|---|---|---|
| `config.json` vendored writes | `write_config()` uses temp+rename atomic replace (`claude_teams/teams.py:119-135`); `add_member`/`remove_member` are RMW without lock (`teams.py:158-172`) | Lost updates when vendored writer races anyteam's `inboxes/.lock`-held registration | One `config.lock` shared by every config writer; add `configVersion` for CAS |
| `config.json` reads | Unlocked `read_text` + JSON parse + Pydantic validation (`teams.py:93-99`) | Atomic replace usually saves readers, but `create_team()`'s initial write isn't atomic (`teams.py:83-85`) — a reader during init can see partial JSON | Make all writes go through atomic helper; readers retry on `JSONDecodeError` once |
| Inbox append | Append uses `inboxes/.lock` (`messaging.py:146-158`); read-without-mark is unlocked (`messaging.py:72-78`) | Unlocked readers can see partial rewrites and raise `JSONDecodeError`; `protocol_io.read_inbox()` catches and returns `[]` (`protocol_io.py:38-56`) but the wrapper's vendored read can surface the exception | Append-only JSONL OR atomic-array-rewrite under lock; readers retry on parse failure |
| Inbox mark-as-read | Deserialize all to `InboxMessage`, mutate `read`, rewrite full list (`messaging.py:53-70`) | `InboxMessage` lacks `extra="allow"` — rewrite strips harness-added fields. `read_own_inbox`'s assert is the local defense | Stable `messageId` per row; `ack_messages(ids)` updates `read` by index without re-serializing |
| Permission responses | Lock teammate inbox, scan unread `team-lead` messages for matching `permission_response`, mark only the matched entry, rewrite (`protocol_io.py:271-313`) | request-id based but message-id-less — duplicate or malformed responses log-only; first valid match wins | Keyed `requests/<id>.json` files OR `messageId` per inbox row (then `ack_messages`) |
| Task create/update | `tasks/<team>/.lock` for writes (`tasks.py:77-90`); reads unlocked (`tasks.py:95-101`) | Writers serialized; readers can race a partial write | Keep lock for writes; write temp+replace atomically; reader retry. Add `updatedAt`/`version` to TaskFile for optimistic CAS |
| Task claim | `claim_task()` is correctly lock-protected and CASes status/owner under the task lock; hand-writes to avoid `update_task()` re-entrancy deadlock (`protocol_io.py:86-130`) | Safest task-claim primitive lives outside `claude_teams.tasks` — separate impls can drift | Move `claim_task()` into `claude_teams.tasks` as first-class CAS primitive; share with future hosts |
| Task block/complete | `_mark_blocked()` defensively re-reads to skip if already completed (`loop.py:855-875`) | Handles late-completion race for blocked path; `update_task()` still allows stale activeForm/metadata writes | Add `expected_status`/`expected_owner` to update; make terminal transitions CAS |
| File locking primitive | `filelock.FileLock(str(lock_path))` no timeout/staleness (`_filelock.py:9-12`) | Crashed teammate can deadlock next adapter startup | 30s default timeout; on timeout log `lock.stale_suspected` with holder PID; force-acquire policy |

The kit's storage abstraction (§6.2) specifies: every read/write of substrate state goes through a `Storage` interface that promises (a) atomic single-key writes, (b) CAS with version preconditions, (c) lock acquisition with timeout, (d) parse-retry on transient races. Filesystem-backed default ships with `claude_teams._filelock`; future ports (Redis-backed for distributed teams, SQLite-backed for single-host high-throughput) implement the same interface. The race surface above becomes a test suite, not folklore.

---

## 6. The 200-line protocol kit SDK

The kit is the artifact that converts claude-anyteam from a Claude Code plugin into a host-agnostic protocol library. Per the strategic-roadmap (`docs/internal/strategic-roadmap.md`), Phase 2's core deliverable is "stop being a Claude Code plugin" and Phase 3 is "ship a non-Claude host binding." The kit is what makes both real.

This section spells out the API in enough detail that opus-vision (10) can lift it directly into 10-platform-vision.md. opus-roadmap (09) can map it to a phased work plan.

### 6.1 Design principles

1. **Host-agnostic from day one.** The kit's storage interface accepts any backend. Filesystem (`~/.claude/teams/<team>/`) ships as default. Cursor / OpenCode / future hosts plug in their own.
2. **Convergence point for three implementations.** cs50victor's MCP server, our vendored `claude_teams/` Python, and a future TypeScript port can all converge on the kit's wire spec. The kit exists in service of the spec, not the other way around.
3. **Schema versioning is mandatory from day one, not bolted on.** Every wire envelope carries `schema_version`. The kit refuses to deserialize `schema_version > supported`.
4. **Three primitives, not one.** `agent_card()` (self-knowledge), `find_capability()` (cross-team discovery), `peer_prompt_fragment()` (cross-team how-to). Missing any one makes the moat invisible (§2 violation) or unusable (§1 violation).
5. **The kit knows nothing about how the harness produces tokens.** It only carries identity, lifecycle, mailbox, task state, capabilities, and visibility events. Backend `execute_task` / `reply_to_prose` are the only required overrides.

### 6.2 Storage abstraction

```python
class TeamStorage(Protocol):
    """Substrate-agnostic store for team config/inbox/task/event-log state.
    
    Filesystem default targets ~/.claude/teams/<team>/ and ~/.claude/tasks/<team>/.
    Other implementations: distributed-Redis (multi-host teams), SQLite (high-throughput
    single-host), in-memory (test fixtures).
    """
    
    # Config / roster
    def read_config(self, team: str) -> TeamConfig: ...
    def update_config(
        self,
        team: str,
        update: Callable[[TeamConfig], TeamConfig],
        *,
        expected_version: int | None = None,
    ) -> TeamConfig:
        """Atomic CAS update. Raises ConfigVersionConflict if expected_version
        doesn't match current. Holds the team-config lock for the duration."""
    
    # Inbox (per-agent, append-only with stable message IDs)
    def append_message(self, team: str, agent: str, message: InboxMessage) -> str:
        """Returns messageId. Atomic under inbox lock."""
    def read_own_inbox(
        self,
        team: str,
        agent: str,
        *,
        unread_only: bool = True,
    ) -> list[InboxMessage]: ...
    def ack_messages(self, team: str, agent: str, message_ids: list[str]) -> None:
        """Mark by id without re-serializing the array. Preserves unknown fields."""
    
    # Tasks
    def list_tasks(self, team: str) -> list[TaskFile]: ...
    def get_task(self, team: str, task_id: str) -> TaskFile: ...
    def claim_task(
        self,
        team: str,
        task_id: str,
        new_owner: str,
        active_form: str,
        *,
        expected_status: str = "pending",
        expected_owner: str | None = None,
    ) -> TaskFile:
        """CAS: refuses if expected_status/expected_owner don't match.
        First-class kit primitive; replaces our local protocol_io.claim_task."""
    def update_task(
        self,
        team: str,
        task_id: str,
        update: TaskUpdate,
        *,
        expected_version: int | None = None,
    ) -> TaskFile:
        """Optimistic CAS. Block/complete are explicit terminal transitions."""
    
    # Visibility events (append-only JSONL)
    def append_event(self, team: str, agent: str, envelope: VisibilityEvent) -> str:
        """Returns event_id. Appends to ~/.claude/teams/<team>/events/<agent>.jsonl
        in the default filesystem impl."""
    def read_events(
        self,
        team: str,
        agent: str,
        *,
        since_seq: int | None = None,
        limit: int = 100,
    ) -> list[VisibilityEvent]: ...
    
    # Locking with staleness
    @contextmanager
    def lock(
        self,
        path: str,
        *,
        timeout_s: float = 30.0,
    ) -> Iterator[None]:
        """Acquires lock with timeout. On timeout, yields a 'stale-suspected'
        flag to the caller and logs the suspected holder's PID."""
```

### 6.3 The Teammate base class

```python
class Teammate:
    """Base class for a host-agnostic team protocol participant.
    
    Subclass for each harness. Override execute_task() and reply_to_prose();
    optionally override capability hooks. The kit handles registration,
    inbox poll, task claim CAS, idle pings, shutdown lifecycle, and event log.
    """
    
    # ---- Identity (set at __init__ from argv/env) ----
    team: str
    name: str
    cwd: Path
    color: str = "cyan"
    plan_mode_required: bool = False
    storage: TeamStorage
    
    # ---- Capability declaration (A2A Agent Card + MCP-style per-capability manifest) ----
    
    def agent_card(self) -> dict:
        """Override to declare the harness's full capability manifest.
        
        This is the rich, MCP-style entry: per capability, version + schema +
        description + when_to_use + when_not_to + failure_modes. This is what
        the wrapper MCP returns from `mcp_anyteam_capability_manifest(name)`
        when a peer is about to invoke a capability and needs the schema and
        semantic guidance loaded into context.
        
        Top-level required fields:
        - schema_version: int            — wire-format version of the card itself
        - harness: str                   — "codex-cli" | "gemini-cli" | "kimi-cli" | ...
        - harness_version: str           — detected from the binary at startup
        - transport: str                 — "app-server" | "headless-stream-json" | "acp" | ...
        - capabilities: dict[str, dict]  — per-capability manifest entries
        
        Each capability entry shape:
        - version: str                   — peer degrades when shape changes across harness versions
        - schema: dict                   — input contract (JSON Schema; mirrors MCP tool input_schema)
        - description: str               — one-sentence what
        - when_to_use: str               — semantic guidance for invoking
        - when_not_to: str               — failure-mode-prevention guidance
        - failure_modes: list[str]       — closed list of CONSTANT_NAMES callers must handle
        """
        return {
            "schema_version": 1,
            "harness": "unknown",
            "harness_version": "unknown",
            "transport": "unknown",
            "capabilities": {},
        }
    
    def capabilities(self) -> list[str]:
        """Derived flat list of capability names. Written to
        `members[].capabilities` in config.json for cheap roster discovery.
        Default: derived from agent_card()["capabilities"].keys().
        
        This is the "transport-layer" view: any reader of config.json can
        check which capabilities a teammate exposes without spawning the
        wrapper MCP or fetching the rich manifest. Use Team.find_capability()
        for queries.
        """
        return list(self.agent_card().get("capabilities", {}).keys())
    
    def peer_prompt_fragment(self) -> str:
        """Returns one paragraph per declared capability, injected into peer
        system prompts so peers know HOW to invoke this teammate's unique
        features. Default: kit auto-generates from agent_card() — concatenates
        each capability's `description` + `when_to_use` + `when_not_to` into
        a per-teammate prompt fragment.
        
        Override only when the harness needs richer prose than the manifest
        can express. Default auto-generation is preferred — it stays in sync
        with the manifest by construction.
        """
        return _auto_peer_fragment_from_card(self.agent_card())
    
    # ---- Required: the harness's actual work ----
    
    async def execute_task(self, task: Task) -> TaskResult:
        """Run the harness on a claimed task. Return the result.
        
        The kit handles:
        - claim CAS with active_form
        - rate-limited progress events (call self.emit_turn_progress())
        - turn_completed/turn_failed envelope on return
        - task_complete or task_blocked mailbox dispatch
        
        The harness handles: actual reasoning, tool invocation, file edits.
        """
        raise NotImplementedError
    
    async def reply_to_prose(self, peer: str, body: str) -> str | None:
        """Generate a prose reply to a peer DM. Return None to skip
        (e.g., if the harness already delivered the reply via send_message
        tool). The kit's _should_skip_prose_fallback heuristic (lifted from
        the three loops' duplicated guard) decides which.
        """
        raise NotImplementedError
    
    # ---- Optional: capability-specific hooks (default: not supported) ----
    
    async def request_permission(
        self,
        tool_name: str,
        tool_args: dict,
        task_id: str,
    ) -> PermissionDecision:
        """Bridge a host-side permission request to the lead via mailbox.
        Default impl raises PermissionBridgeUnsupported, which the host
        translates per its approval_policy (e.g., codex.approval_policy=never
        means deny; gemini.trust=trusted means allow).
        """
        raise PermissionBridgeUnsupported(self.name)
    
    def steer_received(self, steer: Steer) -> SteerDelivery:
        """Called when a SteerIn message arrives in our inbox. Default
        queues for next-turn delivery. Override to deliver mid-turn for
        backends that support live injection (Codex App Server, Gemini ACP).
        Returns SteerDelivery.LIVE or SteerDelivery.NEXT_TURN."""
        ...
    
    # ---- Visibility primitives (kit fans out to event log + mailbox) ----
    
    def emit_turn_started(self, task_id: str, turn_id: str, **payload) -> None:
        """B9 §6.3 turn_started envelope. Always written to event log;
        not to mailbox."""
        ...
    
    def emit_tool_event(
        self,
        category: Literal["host_tool", "mcp_tool", "team_tool", "shadow_tool"],
        tool_name: str,
        phase: Literal["started", "completed", "failed"],
        **payload,
    ) -> None:
        """B9 §6.3 tool_event. Always to event log; mailbox only on phase=failed
        or rate-limited checkpoint."""
        ...
    
    def emit_artifact_event(
        self,
        path: str,
        action: Literal["modified", "created", "deleted"],
        **payload,
    ) -> None: ...
    
    def emit_turn_progress(
        self,
        elapsed_s: float,
        summary: str,
        **payload,
    ) -> None:
        """B9 §6.3 turn_progress. Mailbox: rate-limited (default 60s).
        Task state: updates active_form. Always event log."""
        ...
    
    def emit_turn_completed(self, exit_code: int, **payload) -> None: ...
    def emit_turn_failed(self, error: str, error_class: str, **payload) -> None: ...
    def emit_visibility_degraded(self, surface: str, reason: str, **payload) -> None:
        """When the kit can prove the lead is missing a native-like surface."""
        ...
    
    # ---- Default lifecycle (kit handles; override only for special cases) ----
    
    def on_shutdown_request(self, request_id: str) -> bool:
        """Returns True to approve, False to reject. Default: reject if
        in-flight task; approve otherwise. Idempotent by request_id."""
        ...
    
    def on_idle(self) -> str:
        """Returns the idle reason. Default: 'available'. Override to
        provide richer status (e.g., 'awaiting test results from peer X')."""
        ...
```

### 6.4 The Team class — hybrid roster discovery + on-demand manifest

The capability layer has two access patterns with different latency budgets:

- **Roster discovery** (cheap, frequent): "which teammates have capability X?" — a lead routing tasks queries this on every assignment decision. Backed by the flat `members[].capabilities` list in `config.json`. One file read.
- **Rich manifest** (expensive, rare): "what's the schema and semantic guidance for capability X on teammate Y?" — a peer about to invoke X loads the full manifest into context. Backed by the wrapper MCP tool `mcp_anyteam_capability_manifest(agent_name)`. One MCP round-trip.

Same MCP precedent that already works at the tool level — extended one level up. The kit codifies the split:

```python
class Team:
    """Cross-team queries. Backed by TeamStorage + wrapper MCP."""
    
    def __init__(self, team: str, storage: TeamStorage):
        ...
    
    # ---- Roster discovery (flat list, fast) ----
    
    def find_capability(self, cap_name: str) -> list[str]:
        """Return teammate names whose flat capabilities list contains cap_name.
        Reads config.json only; does NOT load the rich manifest. Used by leads
        for routing decisions on every task assignment.
        
            >>> team.find_capability("permission_bridge")
            ['gemini-bob', 'gemini-cora']
        
        For the lead to route a sensitive-file-touching task to a
        permission-gated teammate.
        """
        ...
    
    def roster(self) -> list[Member]:
        """Read team config and return members with their flat capabilities list.
        Lead UIs render this as the team status panel. Does NOT include the rich
        manifest — UI fetches that lazily when the user expands a capability."""
        ...
    
    # ---- Rich manifest (per-capability dict, on-demand) ----
    
    async def capability_manifest(
        self,
        agent_name: str,
        cap_name: str | None = None,
    ) -> dict:
        """Fetch the rich per-capability manifest entry for agent_name. If
        cap_name is None, returns all entries. Round-trips through the wrapper
        MCP's `mcp_anyteam_capability_manifest(agent_name)` tool.
        
            >>> await team.capability_manifest("codex-runtime", "turn_steer")
            {
                "version": "1",
                "schema": {...},
                "description": "Inject text mid-turn to redirect the model's reasoning.",
                "when_to_use": "When you observe the teammate pursuing a stale path...",
                "when_not_to": "Don't steer if a structured tool-call is in flight...",
                "failure_modes": ["RACE_LOST_NO_TURN_IN_FLIGHT", "STEER_BUFFERED_NEXT_BOUNDARY", ...],
                "delivery_mode": "live",
                "expiry_semantics": "live_only",
                "authorization": "any_peer",
            }
        """
        ...
    
    def peer_prompt_fragments_for(self, requester: str) -> str:
        """Aggregate peer-fragment text for all capabilities this teammate
        doesn't have but others do. Injected into requester's system prompt
        at spawn time so the requester knows what its peers can do that it
        cannot — and crucially, when invoking those capabilities is the right
        move (the `when_to_use` / `when_not_to` half).
        
        Example output for a codex-* teammate in a team that has gemini-acp-*:
        
            # Capabilities of your peers
            #
            # gemini-bob: permission_bridge
            #   What: Surfaces sensitive tool-use to team-lead for interactive
            #         approval before execution.
            #   When to use: When assigning tasks that touch production paths,
            #         secrets, or external networks.
            #   When not to: Don't route routine read-only tasks here — the
            #         approval prompts add latency without value.
            #   Failure modes: APPROVAL_TIMEOUT, APPROVAL_BRIDGE_ERROR, ...
            #
            # gemini-bob: plan_mode (declared)
            #   ... (same shape)
        """
        ...
```

### 6.4.1 Wrapper MCP additions

The wrapper exposes one new tool to support the hybrid lookup:

```python
@mcp.tool
def mcp_anyteam_capability_manifest(
    agent_name: str,
    capability: str | None = None,
) -> dict:
    """Return the rich per-capability manifest for a teammate.
    
    Args:
        agent_name: target teammate name (must be a member of this team).
        capability: optional capability name; if omitted, returns all entries.
    
    Use this BEFORE invoking a capability you don't have direct experience with —
    it returns the schema (so you can construct valid arguments), description
    (so you know what it does), when_to_use / when_not_to (so you know whether
    invoking is the right move), and failure_modes (so you can handle errors).
    
    Roster discovery uses the flat `members[].capabilities` list in config.json
    (read via `read_config()`); this tool is only for the rich manifest entry.
    """
    ...
```

This is added to `EXPOSED_TOOLS` (currently 13 tools per `wrapper_server.py:54-68`) — the 14th tool, classifiable as a `team_tool` for the visibility taxonomy.

The wrapper-side cost is reading the target teammate's Agent Card (already in the registration path — we self-register with our own card; reading another teammate's card is a config.json read plus a kit-level lookup of their declared manifest). For backends running, the manifest is in-memory; for backends that have shut down, it's cached in the member row.

### 6.4.2 `broadcast_capability_manifest` — eager pre-load to avoid PE-5

Per §3.8 / PE-5 and CLAUDE.md:74: capability manifests must be cached at team formation, not lazily fetched per invocation. The hybrid lookup in §6.4 is correct in shape but wrong in default — round-tripping the wrapper MCP every time a peer considers invoking a peer's primitive is exactly the latency tax §3 forbids.

The kit adds a peer-efficiency primitive:

```python
class Team:
    ...
    
    async def broadcast_capability_manifest(self) -> None:
        """Eagerly fetch every active teammate's full capability manifest and
        cache it locally. Run at team formation time and on member-set changes.
        
        After this, every Teammate has a local cache of all peer manifests.
        Lookups via Team.capability_manifest() return from cache (O(1)) rather
        than round-tripping the wrapper MCP.
        
        Stale-on-capability-change is solved by the per-capability `version`
        field — when a Teammate detects a version mismatch on a cached entry
        (e.g., a peer rolls a Codex CLI upgrade that bumps `turn_steer` to v2),
        it re-fetches that one entry.
        """
        ...

class Teammate:
    ...
    
    # Cache populated by Team.broadcast_capability_manifest() at formation.
    # Each Teammate holds its peers' manifests in memory.
    _peer_manifest_cache: dict[str, dict] = {}
    
    def cached_peer_manifest(self, peer: str, capability: str | None = None) -> dict | None:
        """Local-dict read; no I/O. Returns None if peer is unknown or its
        manifest hasn't been broadcast yet.
        
        On version mismatch (peer's flat-list capabilities show an entry whose
        cached version != current), the kit auto-invalidates the entry and
        triggers a single-entry refetch via wrapper MCP.
        """
        ...
    
    def on_member_added(self, peer: str) -> None:
        """Lifecycle hook: called when a new teammate joins. Default impl
        triggers `Team.broadcast_capability_manifest()` to refresh local
        caches with the new peer's manifest. Can be overridden to throttle
        in high-churn teams."""
        ...
    
    def on_member_removed(self, peer: str) -> None:
        """Lifecycle hook: prune the cached manifest. Default removes the
        peer's entry from `_peer_manifest_cache`."""
        ...
```

Eager-broadcast at team formation costs O(N) wrapper-MCP round-trips for an N-member team, paid once. After that, every peer-to-peer "should I invoke this primitive?" decision is O(1) local read. The §3 latency bar — "match native Claude pace" — requires this; native Claude teammates have the analogous information at zero cost because they're all in one process.

Stale-cache invalidation via `version` field is the standard "ETags but for capability shapes" pattern. The kit emits a `visibility_degraded` event when a peer's manifest version changes mid-session so the lead can see capability evolution.

### 6.5 The kit's main entrypoint

```python
def run(teammate_cls: type[Teammate]) -> int:
    """Console entrypoint. Subclass writes their main as:
    
        if __name__ == '__main__':
            sys.exit(run(MyHarnessTeammate))
    
    The kit handles:
    - argv parsing (--team, --name, --cwd, --color, --effort, --model, ...)
    - Storage selection (default: FilesystemStorage(~/.claude))
    - Idempotent self-registration with Agent Card
    - 1.5s inbox poll loop
    - Atomic task claim with active_form
    - 60s idle pings
    - Shutdown lifecycle (idempotent dedup, mid-task reject)
    - Plan mode (opt-in via planModeRequired)
    - Event log fan-out
    - Mailbox rate-limiting per envelope kind
    - Diagnose integration on uncaught exceptions
    - Peer-prompt-fragment injection into peers' system prompts at peer-spawn time
    """
    ...
```

A new adapter is then ~200 lines, with the capability manifest carrying real semantic guidance:

```python
# Hypothetical claude_anyteam_glm/main.py
import sys
from claude_team_protocol import Teammate, run, Task, TaskResult
from .invoke import invoke_glm  # the harness-specific CLI wrapper (~150 lines)

class GlmTeammate(Teammate):
    def agent_card(self) -> dict:
        return {
            "schema_version": 1,
            "harness": "glm-cli",
            "harness_version": _detect_glm_version(),
            "transport": "headless-stream-json",
            "capabilities": {
                # Capabilities GLM exposes:
                "turn_steer": {
                    "version": "1",
                    "schema": {
                        "type": "object",
                        "required": ["text"],
                        "properties": {
                            "text": {"type": "string", "maxLength": 8192},
                            "task_id": {"type": ["string", "null"]},
                        },
                    },
                    "description": "Queue text to inject at the next turn boundary.",
                    "when_to_use": "When new context arrives between this teammate's tasks; when a peer wants to redirect work without restarting the task.",
                    "when_not_to": "Don't steer mid-task — GLM headless cannot deliver mid-turn; the message will be buffered until the next prompt boundary.",
                    "failure_modes": [
                        "RACE_LOST_NO_TURN_IN_FLIGHT",
                        "STEER_BUFFERED_NEXT_BOUNDARY",
                        "STEER_AUTH_REJECTED",
                    ],
                    "delivery_mode": "next_turn",
                    "expiry_semantics": "task_count",
                    "authorization": "lead_only",
                },
                "host_tool_surface": {
                    "version": "1",
                    "schema": {"type": "string", "enum": ["glm-native"]},
                    "description": "GLM uses its CLI's native shell + file tools.",
                    "when_to_use": "Read-only roster signal — peers describing work say 'use Bash to run X'.",
                    "when_not_to": "Not callable; informational only.",
                    "failure_modes": [],
                },
                "approval_policy": {
                    "version": "1",
                    "schema": {"type": "string", "enum": ["never"]},
                    "description": "GLM CLI runs without approval prompts.",
                    "when_to_use": "Read-only roster signal.",
                    "when_not_to": "Not callable; informational only.",
                    "failure_modes": [],
                },
                # Capabilities GLM does NOT expose are simply absent.
                # No turn_steer.delivery_mode=live, no permission_bridge,
                # no thread_fork, no soft_non_progress_watchdog.
            },
        }
    
    # peer_prompt_fragment() is auto-generated from agent_card() by default;
    # override only if you need richer prose.
    
    async def execute_task(self, task: Task) -> TaskResult:
        self.emit_turn_started(task.id, turn_id=...)
        result = await invoke_glm(task.description, cwd=self.cwd, ...)
        for event in result.events:
            self.emit_tool_event(...)
        self.emit_turn_completed(exit_code=result.exit_code)
        return TaskResult(
            files_changed=result.files_changed,
            summary=result.summary,
            exit_code=result.exit_code,
        )
    
    async def reply_to_prose(self, peer: str, body: str) -> str | None:
        result = await invoke_glm(_prose_prompt(peer, body), ephemeral=True)
        return result.last_message if result.exit_code == 0 else None

if __name__ == '__main__':
    sys.exit(run(GlmTeammate))
```

The protocol-side library lives once; the harness-specific code is the actual `invoke_glm` plus the manifest authoring. The manifest authoring is the work — it's where the harness owner encodes "what's unique about my CLI, and when do peers correctly invoke it." That's the moat made explicit. The 200-line target is for the adapter file (not the kit), and the manifest is the dominant content of `agent_card()` — that's correct, since the manifest IS the value the adapter brings.

### 6.6 What the kit fixes from the leak ledger

Adopting the kit fixes (or makes trivial-to-fix) the following leaks:

| Leak | Resolution via the kit |
|---|---|
| L1 (CodexResult naming) | Backend-neutral `TaskResult` in the kit |
| L6 (prose-fallback duplication) | `_should_skip_prose_fallback` shared helper |
| L7 (codex_exit_code field) | `TaskResult.exit_code` + `backend` field |
| L9 (parallel agents/ config) | Agent Card stores model/effort in member row |
| L10 (task_blocked unmodelled) | First-class `TaskBlocked` envelope |
| L11 (SteerIn dual format) | Single canonical wire form, deprecation log on legacy |
| L12 (mark-as-read clobber) | `ack_messages(ids)` preserves unknown fields |
| L13/L14 (incident artifact path) | `incident_ref` field on `turn_failed` envelope |
| L17 (no event log) | `Storage.append_event` + `Teammate.emit_*` primitives |
| L17-bis (wrapper shadow tools no events) | Decorator on every wrapper tool emits tool_event |
| L17-ter (no headless turn_progress) | `emit_turn_started`/`emit_turn_completed` on every backend |
| L18 (tool-name extraction drift) | Single `ToolEvent` shape consumed by `emit_tool_event` |
| L19 (Gemini ACP local taxonomy) | Subsumed into `emit_tool_event` |
| L21 (three Settings classes) | `TeammateSettings` base + per-backend extensions |
| L22 (handle_server_request hook overload) | Per-method handler registration |
| L24 (test seam via monkeypatch) | Override the runner instance |
| L25-bis (`backendType="in-process"` polite lie) | Agent Card declares `transport` explicitly |
| L26 (silent wire-schema growth) | `schema_version` on every envelope; per-capability `version` field; transport vs capability layer separation makes additions reviewable |
| L27 (vendored writers no shared lock) | All writes through `Storage.update_config` with shared lock |
| L28 (implicit member discriminator) | Explicit `role` field on member |
| L29 (unclassified `task_get` narrowing) | Capability declaration for which tools are exposed |
| L30 (effort-tier drift) | Validation in `TeammateSettings.effort` Literal |
| L31 (incomplete schemas) | Build-time emission from Pydantic models |
| L32 (wrapper task_update no owner check) | `Storage.update_task` validates owner-membership |
| L33 (filelock no staleness) | `Storage.lock(timeout_s=30)` standardizes behavior |
| L34 (`<system_reminder>` body mutation) | Sender metadata in structured fields, not text |
| All CD entries | Resolved by `agent_card()` + `peer_prompt_fragment()` |
| **Peer-efficiency tier (§3.8)** | |
| PE-1 (wrapper send_message exposure inconsistency) | `feature_test` smoke-tests every declared tool at startup |
| PE-2 (idle noise crowding) | Typed `idle_heartbeat` vs `idle_state` envelopes (kit's SchemaPack) |
| PE-3 (steer authorization too narrow) | CD-6 default flips to `any_peer`; harness opts in to `lead_only` |
| PE-4 (double-send heuristic fragility) | `BackendResult.delivered_reply: bool` explicit flag |
| PE-5 (lazy manifest fetch) | `Team.broadcast_capability_manifest()` eager pre-load + version-flag invalidation |
| PE-6 (peer-initiated reassignment) | `Storage.transfer_task` CAS primitive |

The leak ledger is mostly artifact of incremental shipping; the kit is the discipline that prevents the next ledger from accumulating.

### 6.7 What the kit deliberately does NOT do

Per E0 and the architectural lens, the kit does NOT:

- Wrap or unify backend tool surfaces. The wrapper's narrow 6-tool team-coordination surface stays narrow; harness-native tool surfaces flow through unmodified.
- Translate model API requests. We are not a router (see A0).
- Provide a unified prompt template. Each backend's `prompts.py` is its own; only the kit-injected peer-prompt fragments are shared.
- Manage the harness's session/turn loop. App Server's polling, ACP's session/prompt, headless's stream-json parsing all stay in their respective `invoke.py` modules.
- Define the host UI's rendering of capabilities. The kit emits Agent Cards; the host UI decides how to display them. (For Claude Code: probably as decoration on the presence row. For Cursor: probably as side-panel metadata. The protocol doesn't care.)

---

## 7. Anti-patterns we've avoided (and should keep avoiding)

### A0. No router-style flattening (the most important)

We deliberately do NOT proxy `ANTHROPIC_BASE_URL`. We do NOT route through a single Claude session loop. We do NOT expose multiple models from one harness.

Cited examples:

- **HydraTeams (Pickle-Pixel/HydraTeams).** Per codex-clones (02), HydraTeams reverse-engineers the Anthropic Messages API request, lets a non-Claude model produce tool calls, and lets the native Claude Code process execute them via `ANTHROPIC_BASE_URL` redirection. Inherits native TUI visibility for free. Catastrophic capability loss: Codex App Server speaks JSON-RPC, not the Anthropic API — there's no way to carry `turn/steer` or `thread/fork` through an Anthropic-shaped proxy. Gemini ACP's permission bridge has no Anthropic API equivalent. Kimi's native swarm runs inside Kimi's own process model. The "model swap" approach replaces the model behind Claude Code's harness; it does not bring a different harness. Per architecture.md:21: "loses every harness-specific feature."
- **musistudio/claude-code-router.** Per codex-clones (02), 33k stars, biggest public Claude-Code-protocol-mediation project. Routes outbound `/v1/messages` calls through provider/router/transformer layers. Strong model-surface knowledge; weak file/team-protocol knowledge. Same shape as HydraTeams: keeps Claude Code as runtime, rewrites model traffic. Same loss.
- **OpenCode.** Different host, but routes through one session loop. Cs50victor's MCP impl supports `backendType="opencode"` in a way that suggests OpenCode runs its own multi-agent system via a coordinator process. To the extent OpenCode flattens harness capabilities to a single loop, it's structurally limited the same way.
- **Any framework that puts a router between the lead and a model** — LangGraph, CrewAI, AutoGen, OpenAI Swarm. Per codex-priorart (06) §"Patterns to avoid #2": "Framework-internal abstractions masquerading as an interop protocol." LangGraph/CrewAI/AutoGen agents are graph nodes / framework objects, not OS processes. They cannot host Codex App Server as a peer.

The moat is "preserve harness capabilities natively across a team." Routers can't, by construction. To match us a router has to throw out its session loop and rebuild around external process orchestration; at that point it's no longer a router. Per architecture.md:21.

### A1. No LLM wrapper

`docs/architecture.md:157-161`. Every teammate in Claude Code's default Agent Teams is a Claude instance. Common designs for "bringing other models in" wrap those models inside a Claude teammate that treats the external model as a tool. That adds latency, double-charges tokens, and puts Claude's reasoning in the middle of decisions the external model should make directly. We removed Claude from the path; the routed CLI is the teammate.

Don't reintroduce wrapping for "smart routing" — that's where every other multi-model project lost the elegance.

### A2. No backend-specific user-facing knobs

`docs/architecture.md:148-155`. No `--gemini-thinking-budget`. No `--kimi-thinking`. No per-backend effort vocabulary. The five-tier `--effort` is enough. Backend-specific knobs leak the harness to the user surface; protocol-side normalization handles the truly common cases (effort, model). Lossy mapping at the boundary is the right tradeoff per E6.

### A3. No mutation of user CLI configs

Adapter-owned isolated HOME for each backend (Gemini `prepare_isolated_gemini_home`, Kimi `prepare_isolated_kimi_home`). User's `~/.codex/config.toml` / `~/.gemini/settings.json` / `~/.kimi/config.toml` stay untouched. Never write to user's own config tree.

### A4. No model-slug allowlist

`docs/architecture.md:153-155`. `--model` is pass-through; the protocol doesn't gate. Whatever the backend CLI accepts works. claude-anyteam keeps no allowlist of its own. Documentation lists current defaults per backend without enforcing them.

### A5. No destructive lifecycle tools reachable from the model

`src/claude_anyteam/wrapper_server.py:74-81`. `BLOCKED_TOOLS` keeps `team_create`, `team_delete`, `spawn_teammate`, `force_kill_teammate`, `process_shutdown_approved`, `check_teammate` out of the model's reach. Hallucinated tool calls to destructive operations are structurally impossible from a routed teammate.

### A6. No invented spawn surface

`docs/internal/spawn-research-findings.md:163-220`. We use `CLAUDE_CODE_TEAMMATE_COMMAND`, the host's own hook. No filesystem injection, no process puppeteering, no synthetic registration that the host doesn't bless. The host's official escape hatch is the only path we take.

### A7. No per-backend retry policy in `invoke()`

Kimi's `_run_once` no longer self-retries (`backends/kimi/invoke.py:516-525`); the loop owns retry. Codex `run()` similarly never retries. Single layer of policy.

### A8. No silent fallback for unsupported transports

Kimi ACP raises `NotImplementedError` (`backends/kimi/loop.py:99-100`) instead of degrading silently to headless. Better to fail loud at startup than confuse the lead with a transport it didn't ask for.

### A9. Don't grow the wire schema without versioning

Per L26. We've already grown the wire from the spec's 6 protocol message types to 10. The kit's `schema_version` field on every envelope makes this discipline automatic; until the kit lands, every new wire payload addition needs an explicit decision and changelog entry.

### A10. Don't conflate framework-internal abstractions with interop

Per codex-priorart (06). LangGraph state graphs, CrewAI Agents/Crews/Flows, AutoGen AgentChat teams, OpenAI Swarm handoffs are useful execution frameworks but not stable cross-backend wire contracts. A protocol-spec-able abstraction is identity + lifecycle + mailbox + task + capabilities + events. Anything richer (e.g., "agent receives message and returns next agent" — Swarm's handoff model) is a framework concept, not a protocol concept.

### A11. No parse-prose-to-route (typed kinds for everything peer-facing)

Per CLAUDE.md:78: "inbox event types are versioned so heartbeat / substantive / lifecycle can be distinguished by consumers without parsing prose." Every peer-to-peer message carries a typed `kind` discriminator that consumers switch on without natural-language parsing.

Concrete failures this prevents:

- A peer asked to "summarize what teammates are doing" should not need to LLM-parse `idleReason` strings to count distinct states. The `type` field tells them.
- A lead UI rendering the team should filter heartbeat-idle from substantive-idle by `type` ("idle_heartbeat" vs "idle_state"), not by string match against "available" prose.
- A monitoring/dashboard tool (e.g., the claude-team-dashboard project from codex-clones 02 §"message taxonomy") should be able to categorize messages mechanically.

PE-2 (heartbeat-idle indistinguishable from substantive idle) is the canonical instance. The kit's typed-envelope catalog (§6.5 SchemaPack) makes A11 automatic — every kind ships with a JSON Schema and consumers switch on `type`.

The "be liberal in what you accept" half of Postel's law still applies (E2's `parse_protocol_text` returning None on malformed input becomes prose), but the *outbound* surface is strictly typed. We don't emit prose where a typed kind would do; we don't parse prose where a typed kind exists.

---

## 8. Hand-offs to peers

### To opus-vision (10-platform-vision.md)

The §6 kit SDK is the SDK chapter substrate. Lift directly:
- The two-layer protocol framing (§1.1.1, transport + capability) is the structural argument for why this exists. Routers skip the capability layer; we make it first-class.
- The four kit primitives — `agent_card()` (rich manifest), `capabilities()` (flat list), `Team.find_capability()` (roster query), `Team.capability_manifest()` (on-demand rich fetch via wrapper MCP) — are the protocol's value proposition condensed to API surface.
- The per-capability manifest shape (`version` + `schema` + `description` + `when_to_use` + `when_not_to` + `failure_modes`) is the "teach" half of harness preservation. Without it, peers know capabilities exist but not when invoking is right.
- The hybrid lookup story (flat list in `config.json` for cheap roster discovery; rich manifest via wrapper MCP for on-demand invocation context) mirrors MCP's existing tool-discovery shape and avoids the latency tax of always loading rich manifests.
- The `TeamStorage` interface is the host-agnosticism story — Filesystem default, Redis/SQLite/Cursor-private storage as future ports.
- The "capabilities flow through" framing (§1.1, E0) is how the SDK chapter justifies why this exists beyond Claude Code.
- The HydraTeams contrast (A0) is the "why we're different" story.

Phase 2 strategic milestone ("stop being a Claude Code plugin"): the kit IS that. Phase 3 ("non-Claude host binding"): the kit's `TeamStorage` abstraction is what makes Cursor / OpenCode bindings tractable.

### To opus-roadmap (09-roadmap.md)

The leak ledger (~40 entries with the §3.8 tier added) is a 1:1 work-item list. Suggested priority order based on §1/§2/§3 lens:

**P0 (north-star-blocking):**
- L17 / L17-bis / L17-ter — append-only event log, wrapper tool events, headless turn-progress envelopes (§2 visibility). The B9 §6.5 implementation sequence is the work plan.
- All CD entries (CD-1 through CD-6) — capability declarations are required for §1 to be visible.
- **PE-1** (wrapper send_message exposure inconsistency) — §3 peer DMs *failing in the field*. The proto-rev session itself surfaced this. Highest-priority §3 item; teams cannot collaborate without reliable peer DM.
- **PE-2** (idle noise) — §3 typed envelopes. Cheap to fix, immediately improves peer/lead UX.

**P1 (foundation-crack):**
- L9 — parallel config systems
- L21 — Settings base class
- L27 — vendored writers no shared lock (real concurrency bug)
- L33 — filelock staleness (reliability)
- L26 — schema versioning (Phase-2-blocking per strategic-roadmap)
- **PE-5** (lazy manifest fetch) — `Team.broadcast_capability_manifest()` lands as part of the kit; until then peers pay invocation-time round-trips.
- **PE-3** (steer authorization too narrow) — flipping CD-6 default; small change, large §3 unblock for peer-to-peer steers.

**P2 (correctness-quality):**
- L1 / L7 — backend-neutral result types and field names
- L10 — TaskBlocked Pydantic model + `blocked` task status
- L12 / L20 — inbox mark-as-read clobber and wrapper read_inbox mutation
- L25-bis — host-protocol message catalog audit (codex-extract 03)
- L31 — JSON Schemas for every wire payload
- L32 — wrapper task_update owner-membership check
- **PE-4** (double-send heuristic) — explicit `delivered_reply` flag on `BackendResult`; closes L6 too.
- **PE-6** (atomic peer reassignment) — `transfer_task` CAS primitive.

**P3 (polish):**
- L6 — prose-fallback dedup helper (subsumed by PE-4 when implemented)
- L11 / L11-bis — wire-format unification
- L13 / L14 — incident_ref field
- L18 / L19 — tool-name normalization
- L22 / L23 — JSON-RPC hook + thread_start shape drift
- L24 — test seam clarification
- L29 — task_get classification
- L30 — minimal-effort tier alignment
- L34 — system_reminder body mutation

### To opus-synthesis (07-protocol-spec.md)

The CD framing — "the leak is the absence of declaration, not the heterogeneity" — should refine the protocol-spec proposal. Specifically:

- The spec should split into a transport-layer section (universal: mailbox JSON, atomic task claims, lifecycle, lock semantics) and a capability-layer section (per-harness MCP-style manifest). The CLAUDE.md §1 "two layers" framing is canonical.
- The transport-layer section should declare a flat `members[].capabilities: list[str]` field for cheap roster discovery, written to `config.json` at registration time.
- The capability-layer section should declare a rich manifest shape (`version` + `schema` + `description` + `when_to_use` + `when_not_to` + `failure_modes`) per capability, accessed via a wrapper-MCP tool (`mcp_anyteam_capability_manifest(agent_name, capability=None)`).
- The spec should declare an initial capability vocabulary (turn_steer, thread_fork, permission_bridge, approval_policy, host_tool_surface, soft_non_progress_watchdog). New capabilities can be proposed via spec increment.
- The spec should reserve `metadata.capabilities` namespace on member rows for adapter-private extension.
- Cite ACP's `initialize` capability negotiation, A2A's Agent Card, and MCP's tool-description shape as the precedent surfaces; synthesizing from all three rather than coining new terms.
- The wire envelope catalog needs to align with codex-extract 03's host binary v2.1.119 list. Specifically: audit `shutdown_response` vs `shutdown_approved`/`shutdown_rejected` — the host's recognizers may not accept our shape.

### To codex-prototype (11-prototype, task #11)

The §6 kit SDK signatures are intended to be lift-and-implement, not redesign. Specifically:
- `Teammate` base class signatures in §6.3 are stable; implement `agent_card()`, `capabilities()`, `peer_prompt_fragment()` (with auto-generation default), and the `emit_*` visibility primitives matching B9 §6.3 envelope shapes.
- `TeamStorage` Protocol in §6.2 is the contract — filesystem-backed default ships first; in-memory test fixture is the second port.
- `Team.find_capability()` and `Team.capability_manifest()` in §6.4 are the hybrid roster + on-demand-manifest split. Implement both; the wrapper MCP tool in §6.4.1 is the rich-manifest backing.
- Per-capability manifest shape (§4 introduction) is what each adapter's `agent_card()` returns. The example GLM adapter in §6.5 is implementation-grade — it's roughly what a Codex/Gemini/Kimi adapter rewritten against the kit would look like.
- Leak-resolution table (§6.6) is the regression-test checklist: the prototype should fix all 33 leaks by construction, not by patching.

Acceptance criterion: a prototype Codex teammate written against the kit, in <250 lines, that passes the existing tests and emits at least one `tool_event` envelope per `commandExecution`/`fileChange` notification (closing L17 / L17-bis by way of the kit, not the existing adapter).

---

## 9. Closing

The three north stars converge on three questions:

- **§1**: does the protocol carry harness capabilities to the team intact?
- **§2**: does the lead see what each harness's teammate is doing at native fidelity?
- **§3**: do peers coordinate among themselves at native Claude pace?

The architecture (E0, E5, E13) is correct on §1. The §3.1-§3.7 leak ledger is the §2 polish work. The §3.8 peer-efficiency tier (PE-1..PE-6) is the §3 polish work — newer, sparser, but the proto-rev session itself surfaced PE-1 as a field failure rather than a hypothetical. The capability declarations (CD-1 through CD-6) are the §1 obligation we owe — manifest entries on the Agent Card that make the moat visible AND teach peers when to invoke each capability correctly.

The kit (§6) is what crystallizes all three into a library other implementations can adopt. Phase 2 of the strategic roadmap depends on it. The 200-line target is for adapter files written against the kit; the kit itself is closer to 1000 lines (storage abstraction + Teammate base + Team class + visibility primitives + capability manifest broadcast), but it lives once.

A1 (no LLM wrapper) was the first decision that mattered. A0 (no router-style flattening) is the second. A11 (no parse-prose-to-route) is the third — peer-efficiency baked in from day one. Together they describe a moat that no single-vendor competitor can replicate without breaking their own product. The discipline of E0 (flatten only what is truly common; let everything else flow through) makes the moat durable as new harnesses ship.

The visibility gap is solvable; the peer-efficiency gap is solvable; the harness-preservation moat is structural. We invest in the latter and pay down the former two.

---

*End of 08. Total: ~1500 lines.*
