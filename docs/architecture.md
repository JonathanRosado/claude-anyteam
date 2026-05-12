# Architecture

claude-anyteam is a protocol adapter, not an LLM wrapper. It lets external coding agents participate in Claude Code's [Agent Teams](https://code.claude.com/docs/en/agent-teams) protocol as first-class teammates without routing their reasoning through a Claude instance.

## The core principle: the harness IS the teammate

This is the architectural choice the rest of the design follows from, and it is also the moat. See [CLAUDE.md §1](../CLAUDE.md) for the full north-star statement.

A `codex-*` teammate does not run an LLM that "acts like Codex." It IS the Codex CLI process — full tool surface, App Server `turn/steer` mid-task injection and `thread/fork` cross-task memory, OpenAI's prompt tuning for the specific model slug, Codex's own approval/sandbox/working-directory semantics. Same for `gemini-*` (ACP transport, mid-turn permission bridge, Google's prompt tuning), `kimi-*` (native skills / swarm primitives, large-context behavior, Moonshot's prompt tuning), native-Claude stress teammates (Claude Code's own Task/Skill/WebFetch/Read/Edit/Write/Bash surface), and any future `glm-*` / `deepseek-*` / `qwen-*` adapter.

What this means in practice:

1. **Capabilities flow through, they do not flatten.** When Codex has a feature Gemini does not (or vice versa), the protocol surfaces and routes that feature; it does not strip it to a lowest common denominator. The five-tier `--effort` and `--model` pass-through are protocol-side normalizations; *everything else is harness-native*.
2. **Peers learn each other's capabilities.** Capability declarations live in the team roster (registration); system prompts teach peers how to invoke unique features (for example, "ask `codex-*` teammates to use `thread/fork` for cross-task memory continuity"). The protocol carries the declarations; the prompts deliver the how-to.
3. **The protocol is a coordinator, not a kernel.** It carries identity, lifecycle, mailbox, task state, and capability declarations. It does *not* own the agentic loop, the tool definitions, or the model's prompt tuning. Those belong to each harness.

This implies two distinct protocol layers — see [CLAUDE.md §1 "The two layers"](../CLAUDE.md) for the canonical statement:

- **Transport** is the file-based Agent Teams contract everyone speaks (mailbox, tasks, lifecycle, locks). Universal; table stakes.
- **Capability** is per-harness: identity declaration, typed capability inventory (`turn_steer`, `thread_fork`, `permission_bridge`, `live_tool_events`, `large_context`, `native_skills`, …), invocation schemas, and semantic guidance (when to use, when not to, failure modes). Lightweight flags live in `config.json` for roster discovery; the rich manifest is exposed via the wrapper MCP and loaded into peer context on demand — same precedent that already works for MCP tool descriptions, extended one level up.

The router-style alternative (proxy `ANTHROPIC_BASE_URL`, route through a single session loop, expose multiple models from one harness) loses every harness-specific feature: no Codex App Server, no Gemini ACP permission bridge, no Kimi swarm. It also has no capability layer at all — every teammate gets the host's tool surface, period. To match the claude-anyteam differentiator a router would have to throw out its session loop and rebuild around external process orchestration; at that point it is no longer a router.

## Proto-rev substrate primitives by north star

The 2026-04-28 closure set adds concrete substrate primitives that operationalize the three north stars from `CLAUDE.md`. Each paragraph below names the code surface, the comparison-matrix lift ID from `references/external-claude-code-re/proto-rev-execution-log/research-digest/_comparison-matrix.md`, and the source pattern it adopts.

### §1 Harness-preserving capability substrate

**Startup capability-manifest prewarm and bounded supervisor.** Every routed teammate writes a rich Agent Card at registration and broadcasts `capability_manifest_updated` before peers try to invoke harness-specific primitives (`src/claude_anyteam/registration.py:224-239`). Codex, Gemini, Kimi, native Claude, and the wrapper MCP all call `CapabilityManifestCache.load_startup()` before normal inbox/tool handling (`src/claude_anyteam/loop.py:171-179`, `src/claude_anyteam/backends/gemini/loop.py:127-137`, `src/claude_anyteam/backends/kimi/loop.py:94-104`, `src/claude_anyteam/backends/claude_native/loop.py:76-80`, `src/claude_anyteam/wrapper_server.py:582-587`), and the cache loader walks the roster through bounded `ThreadPoolExecutor` fan-out with per-manifest timeouts (`src/claude_anyteam/capability_manifest.py:204-220`, `src/claude_anyteam/capability_manifest.py:241-317`, `src/claude_anyteam/capability_manifest.py:522-554`). Matrix lifts **L2** and **L10**; source pattern: HydraTeams' pre-hydrated control context plus maorinka-claude-rs' bounded supervisor/channel prewarm, with clawcode's per-call MCP startup called out as the counterexample.

**`read_config()` protocol-tool enumeration.** The wrapper exposes a self-healing `protocol_tools` object that computes the exact callable names for the caller's backend, including Gemini's `mcp_anyteam_*` prefix, and embeds direct entries for `send_message`, `read_config`, and capability lookup (`src/claude_anyteam/wrapper_server.py:231-270`). The `read_config()` MCP tool attaches that object to the sanitized team roster so a model can verify the protocol surface instead of hallucinating missing tools (`src/claude_anyteam/wrapper_server.py:1666-1692`). Matrix lift **L11**; source pattern: Piebald's ToolSearch/load-then-call and “look before assert” prompt pattern.

**Capability hook registry validation.** Capability flags are treated as promises, not marketing copy: `CAPABILITY_HOOKS` ties each declared primitive to runtime paths and focused regression tests (`src/claude_anyteam/capabilities.py:72-87`), `assert_known_capabilities()` rejects unknown or unbacked roster flags (`src/claude_anyteam/capabilities.py:344-361`), and `validate_capability_manifest_entries()` requires schema, guidance, failure modes, and peer-callability before an Agent Card is written (`src/claude_anyteam/capabilities.py:377-430`). Matrix lift **L12**; source pattern: clawcode's advertised-but-ignored feature fields, maorinka's unregistered SendMessage drift, and Piebald's exact-context tool boundary as the validation bar.

**Manifest-gated peer steer.** Peer-to-peer steer is opt-in and recipient-defined: the wrapper checks the recipient manifest before delivering `send_message(kind="steer")`, treats missing data as `manifest_not_queried`, and treats denied manifests as `manifest_denies_peer_steer` (`src/claude_anyteam/wrapper_server.py:738-775`, `src/claude_anyteam/wrapper_server.py:1052-1079`). The authorization bit is parsed conservatively from the rich Agent Card by `manifest_accepts_peer_steer()` (`src/claude_anyteam/capabilities.py:433-470`). Matrix lift **L5**; source pattern: aproto-codex-bridge's “all active-turn inbound becomes steer” anti-pattern, corrected with nwyin-cleanroom's typed lifecycle/authorization stance.

### §2 Visibility-parity substrate

**Attachment/preview protocol.** Inbox rows now keep bounded previews while large message bodies spill to artifacts: `InboxAttachment` records absolute/relative path, MIME type, char counts, and checksum (`src/claude_teams/models.py:131-147`), the default spill threshold is 4096 characters (`src/claude_teams/messaging.py:36`), and `_spill_message_if_needed()` writes the full text under `artifacts/inbox/` while replacing `text` with a preview plus pointer (`src/claude_teams/messaging.py:127-156`). Protocol readers only materialize attachments when control-flow parsing requires the full body (`src/claude_anyteam/protocol_io.py:194-244`), and `send_message` returns attachment metadata for oversize deliveries (`src/claude_anyteam/wrapper_server.py:1017-1020`, `src/claude_anyteam/wrapper_server.py:1123-1128`). Matrix lift **L7**; source pattern: aproto-codex-bridge's results-directory plus mailbox preview and nwyin-cleanroom's prompt attachments as structured deltas.

**Visibility-tail filesystem CLI projector.** `claude-anyteam visibility-tail` is deliberately a projector over the canonical `VisibilityEvent` JSONL substrate rather than a replacement socket or pane protocol (`src/claude_anyteam/visibility_tail.py:1-7`). The CLI accepts team/agent/kind/time filters (`src/claude_anyteam/visibility_tail.py:61-128`), renders compact ARGS/RESULT/ERROR cards from typed payloads (`src/claude_anyteam/visibility_tail.py:422-446`), and tails the aggregate `visibility.jsonl` plus per-agent fallback streams with event-id de-duplication (`src/claude_anyteam/visibility_tail.py:518-586`). Matrix lift **L8**; source pattern: clawcode GUI tool cards and aproto Ink/native-TUI event renderers, implemented as projection over our structured event log.

**App Server transport recovery.** Codex App Server remains the native Codex harness path; the adapter now probes stdio/reader liveness, can restart the child process, and can reconnect then resume the existing thread (`src/claude_anyteam/app_server.py:35-90`). During a turn, a closed notification transport triggers one recovery attempt that emits visibility warnings, resumes thread state, recovers a completed terminal turn if possible, or starts a continuation turn with a transport-recovery prompt (`src/claude_anyteam/codex.py:1075-1091`, `src/claude_anyteam/codex.py:1402-1607`, `src/claude_anyteam/codex.py:1647-1652`). Matrix lift **L9**; source pattern: aproto-codex-bridge's WebSocket close/restart and TUI-only restart logic, translated into typed visibility events.

**SIGTERM-aware stress sandbox marker.** Stress runs now write a marker that includes `state="active"` and the owner PID before spawning teammates (`tools/stress/run_scenario.py:379-404`, `tools/stress/run_scenario.py:1331-1332`), preserve a terminal `completed` state in `finally`, and add an explicit `aborted` terminal state when SIGTERM/SIGINT arrives (`tools/stress/run_scenario.py:407-420`, `tools/stress/run_scenario.py:423-483`, `tools/stress/run_scenario.py:1419-1426`). That keeps crash/kill forensics visible instead of inferring them from a dead PID. Matrix lift **L9**; source pattern: aproto-codex-bridge transport-health crash recovery plus maorinka-style cancellation semantics applied to the stress harness.

**Delegated batch summaries and parent task linkage.** Delegated work keeps normal per-task files while adding a one-way `parentTaskId` link with acyclicity validation (`src/claude_teams/models.py:102-110`, `src/claude_teams/tasks.py:52-63`). The visibility layer adds `BatchSummaryPayload` and the `batch_summary` event kind (`src/claude_anyteam/messages.py:298-325`), and the wrapper's `task_batch_summary()` tool wires child links and emits a mailbox-visible summary event with per-child status/session/stop-reason detail (`src/claude_anyteam/wrapper_server.py:1293-1343`). Matrix lift **L13**; source pattern: clawcode delegated group result summaries with child session ids/status/stop reasons, but grounded in real Agent Teams task files.

### §3 Peer-efficiency substrate

**Event-driven `WatchInbox`.** The control loops no longer pay a fixed poll sleep after every empty drain: `WatchInbox` watches the current teammate's inbox basename, wakes on file changes, and falls back to bounded sleep when the watcher is unavailable (`src/claude_anyteam/watch_inbox.py:47-87`, `src/claude_anyteam/watch_inbox.py:116-193`). Codex, Gemini, Kimi, and native-Claude loops all install the watcher and use adaptive active/idle waits at the loop boundary (`src/claude_anyteam/loop.py:237-285`, `src/claude_anyteam/backends/gemini/loop.py:296-329`, `src/claude_anyteam/backends/kimi/loop.py:252-286`, `src/claude_anyteam/backends/claude_native/loop.py:139-175`). Matrix lift **L1**; source pattern: aproto-codex-bridge's `fs.watch` plus active/fallback polling, with maorinka-claude-rs' evented supervisor shape as the durability model.

**Per-target `BatchedSender` and `append_messages_batch()`.** High-volume fan-out can opt into a per-recipient buffer with a 50ms debounce (`DEFAULT_DEBOUNCE_S = 0.05`) and explicit `flush()`/`close()` hooks (`src/claude_teams/messaging.py:340-430`). The underlying `append_messages_batch()` performs one locked read-modify-write for N messages and still applies the attachment spill contract independently to each row (`src/claude_teams/messaging.py:295-337`). Matrix lift **L6**; source pattern: aproto-codex-bridge's MessageRouter per-target debounce and CAS writer, adapted to the file-lock JSON inbox substrate.

**SendMessage visibility invariant.** Routed prompts now repeat the native-team invariant that final/prose output is not a teammate-visible DM and that `send_message` must be called instead; the prompt also tells agents to use `read_config().protocol_tools` when tool availability is uncertain (`src/claude_anyteam/prompts.py:15-23`, `src/claude_anyteam/prompts.py:51-68`, `src/claude_anyteam/prompts.py:96-108`). The wrapper tool description carries the same invariant and explains typed lifecycle/attachment behavior at the schema boundary (`src/claude_anyteam/wrapper_server.py:991-1031`). Matrix lift **L3**; source pattern: Piebald's extracted SendMessageTool wording and nwyin-cleanroom's team prompt invariant.

**L4 `messageKind` discriminator across Codex/Gemini/Kimi.** `InboxMessage` persists an explicit `messageKind` field with default `peer_dm`, so routing decisions filter by a cheap discriminator rather than parsing prose (`src/claude_teams/models.py:159-166`). Codex App Server converts mid-turn peer prose into steer only when `messageKind=steer` and the recipient advertises peer-steer capability (`src/claude_anyteam/loop.py:74-97`, `src/claude_anyteam/loop.py:1283-1326`), while Gemini ACP and Kimi headless mirror that gate before prose batching or JSON-steer handling (`src/claude_anyteam/backends/gemini/loop.py:341-372`, `src/claude_anyteam/backends/gemini/loop.py:416-432`, `src/claude_anyteam/backends/kimi/loop.py:298-329`, `src/claude_anyteam/backends/kimi/loop.py:380-396`). Matrix lift **L5**; source pattern: aproto-codex-bridge's unconditional busy-turn steer anti-pattern, corrected by nwyin-cleanroom's typed lifecycle discriminator.

**Typed lifecycle payloads on `send_message`.** Lifecycle messages are now first-class protocol payloads rather than prose conventions: the message module defines idle, task-complete, task-blocked, plan-blocked, approval, permission, shutdown, and capability-manifest variants (`src/claude_anyteam/messages.py:224-268`), `protocol_io` sends those variants with matching `messageKind` values (`src/claude_anyteam/protocol_io.py:981-1183`), and the wrapper validates `send_message(kind=...)` bodies by parsing JSON and ensuring `kind`/`type` matches the requested discriminator (`src/claude_anyteam/wrapper_server.py:426-451`, `src/claude_anyteam/wrapper_server.py:1036-1039`). Matrix lift **L4**; source pattern: nwyin-cleanroom's `SendMessage.message` union and named lifecycle hooks, with maorinka/clawcode's prose-ish mail as the counterexample.

## The core insight

Claude Code's Agent Teams feature is file-based. Team state lives in `~/.claude/teams/{team}/config.json` and inbox messages in `~/.claude/teams/{team}/inboxes/{name}.json`. The team protocol is an on-disk contract — mailbox polling, atomic task claims, idle notifications, shutdown requests. Any process that speaks this contract can be a teammate.

claude-anyteam speaks the contract directly. It reads your inbox, claims tasks, delegates them to an external model, and writes results back. No Claude LLM sits between you and the external model — and crucially, no claude-anyteam-owned reasoning sits between the lead and Codex / Gemini / Kimi either. The CLI is the teammate.

## The two-piece design

```
┌─────────────────────────────────────────┐
│  Claude Code leader (your main session) │
│  • orchestrates work                    │
│  • creates teammates via Agent Teams    │
│  • spawns via CLAUDE_CODE_TEAMMATE_COMMAND
└──────────────┬──────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────┐
│  claude-anyteam-spawn-shim              │
│  • inspects agent name                  │
│  • routes `codex-*` → Codex adapter     │
│  • routes `gemini-*` → Gemini adapter   │
│  • routes `kimi-*` → Kimi adapter       │
│  • forwards anything else → native claude
└──────────────┬──────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────┐
│  claude-anyteam (Python adapter)        │
│  • self-registers in config.json        │
│  • polls inbox, claims tasks            │
│  • invokes backend-specific CLI         │
│  • writes task_complete to inbox        │
└──────────────┬──────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────┐
│  Backend CLI                            │
│  • Codex: app-server or exec resume     │
│  • Gemini: headless stream-json CLI     │
│  • Kimi: headless stream-json CLI       │
│  • Claude: native headless stream-json  │
│  • shared MCP wrapper for team tools    │
└─────────────────────────────────────────┘
```

Each layer has one job. The shim is a dispatcher. The adapter is the protocol implementation. Codex, Gemini, Kimi, or native Claude handles the backend reasoning based on the teammate name prefix or stress member type.

## Backend invocation paths

### Codex path (headless + app-server)

**App Server (default for Codex).** `codex app-server` runs as a long-lived JSON-RPC session. The adapter manages the thread lifecycle, injects mid-task input via `turn/steer`, and forks cross-task memory via `thread/fork`. This is where the native-teammate behaviors live: if a peer messages a Codex teammate while it is working, the in-flight turn reshapes instead of losing the message; each new task inherits conversational history from the previous one.

**Fresh-exec (Codex opt-out).** Each task spawns `codex exec` fresh. Second and subsequent tasks use `codex exec resume <session_id>` so context carries forward. No mid-task reactivity, but simpler operationally. Enable with `--no-app-server` or `CLAUDE_ANYTEAM_APP_SERVER=false`.

### Gemini path (ACP by default since v0.6.0)

Gemini teammates default to the ACP transport (`gemini --acp`/`--experimental-acp`), which gives the adapter mid-turn steering and persistent sessions and closes most of the productivity gap relative to Codex App Server. The legacy headless path (`gemini --prompt ... --output-format stream-json`) is still available via `--backend headless` or `CLAUDE_ANYTEAM_GEMINI_BACKEND=headless` for older Gemini CLIs that lack the `--acp` flag.

In either mode, the adapter writes an isolated `.gemini/settings.json` exposing the narrowed anyteam MCP wrapper, streams Gemini output back through the same team protocol, and records task completion in the leader inbox.

Documented limitations live in [Gemini adapter limitations](gemini-adapter-limitations.md). The default flip from headless → ACP is tracked in `bug-triage/B4-gemini-productivity.md` (the structural amplifier finding).

### Kimi path (headless stream-json)

Kimi teammates run through the Kimi CLI in print mode: `kimi --print --output-format stream-json -p ...`. The adapter prepares an isolated Kimi HOME, copies the user's OAuth token bundle from `~/.kimi/credentials/kimi-code.json` when present, writes an adapter-owned MCP config file for the shared anyteam wrapper, and invokes Kimi with `--mcp-config-file`.

Kimi's default user-facing model slug from the probed runtime is `kimi-code/kimi-for-coding` (`Kimi-k2.6`, 262k context). Kimi is a strong fit for large-context architecture review and tasks that can benefit from Kimi's native skills and internal swarm/subagent primitives while still presenting as one anyteam teammate.

Kimi v1 intentionally does not use ACP or a Codex App Server equivalent. Limitations:

- no live mid-turn `turn/steer`; steer messages are queued and injected into the next prompt boundary
- no CLI `--output-schema`; structured task and plan outputs are enforced by prompt-embedded schemas plus Python validation/retry
- Kimi stream-json is per-message NDJSON (`assistant` / `tool`) rather than Codex or Gemini event taxonomies
- MCP tools are addressed by their bare declared names (`send_message`, `task_update`), not Gemini-style `mcp_anyteam_*` names

### Native Claude path (headless stream-json for stress baselines)

Native-Claude stress teammates run through Claude Code itself in print mode: `claude --print --verbose --output-format stream-json --mcp-config <adapter-owned-wrapper> --strict-mcp-config -p ...`. The adapter keeps protocol I/O visible to the stress harness while preserving Claude Code's own Task, Skill, WebFetch, Read, Edit, Write, Bash, and prompt-tuning surface inside the headless turn.

Native Claude is deliberately loose-coupled in this adapter: it exposes headless invocation, structured output, live tool-event visibility, native skills/tools, and large context, but it does **not** currently advertise cross-task `session_resume`, `plan_mode`, or peer `turn_steer` because the control loop starts fresh task sessions and does not implement those protocol handlers.

## How teammates become visible

The TUI presence line (`@main @codex-alice @gemini-bob @kimi-cora`) renders from the leader's in-memory state, not from `config.json`. That state is only populated when Claude Code's own spawn flow is what launched the teammate.

claude-anyteam hooks into that spawn flow via `CLAUDE_CODE_TEAMMATE_COMMAND`. When Agent Teams mode spawns a teammate:

1. Claude Code invokes `$CLAUDE_CODE_TEAMMATE_COMMAND` (our shim) instead of the default `claude` binary
2. The shim checks the agent name. Matches `^codex-` dispatch to the Codex adapter; matches `^gemini-` dispatch to the Gemini adapter; matches `^kimi-` dispatch to the Kimi adapter; anything else forwards to native Claude.
3. Claude Code's internal spawn-completion callback registers a mirror task in its state — this is what the TUI renders
4. The adapter self-registers in `config.json` with `backendType: "in-process"` so its entry matches what the leader expects

Both pieces (leader mirror + adapter entry) are required. The shim enables step 1. The adapter handles step 4.

## What happens per task

1. Lead creates a task via Claude Code's task list
2. Adapter picks it up in its event-driven inbox/task loop (with polling fallback)
3. Adapter claims it via compare-and-set under a file lock
4. Adapter sends the task description to the selected backend: Codex via App Server or fresh `codex exec`; Gemini via ACP or headless `gemini --prompt ... --output-format stream-json`; Kimi via headless `kimi --print --output-format stream-json`; native Claude via headless `claude --print --output-format stream-json`
5. The backend executes: reads files, writes files, runs commands, calls wrapper MCP tools to update task status / send messages to peers
6. Task completes; adapter writes `task_complete` to lead's inbox
7. Codex App Server teammates can incorporate peer messages mid-execution via `turn/steer`; Gemini, Kimi, and native-Claude teammates currently receive peer messages on the next poll / prompt boundary rather than live App Server steering.

The wrapper MCP server exposes a narrowed protocol surface to external backends: core team tools (`send_message`, `task_update`, `task_create`, `task_batch_summary`, `read_inbox`, `task_list`, `read_config`), rich capability lookup (`mcp_anyteam_capability_manifest`), and shadow helpers for filesystem/search/web access. Destructive team-control tools like `team_delete`, `force_kill_teammate`, and `force_kill_team` are deliberately blocked — Codex, Gemini, Kimi, or native Claude has full coding access but cannot break the team.

## Extending to new models

The same architecture supports any CLI-native model. Each new adapter is:

1. A Python module that implements the shared protocol interface (inbox polling, task claiming, result writing — most of this is already shared code)
2. A model-specific invocation path (e.g. headless `gemini --prompt ...`, `kimi --print --output-format stream-json ...`)
3. One entry in the spawn shim's routing table (e.g. `gemini-*` → gemini adapter)

For a step-by-step contributor checklist, see [Adding a backend](adding-a-backend.md).

The protocol layer doesn't care which model is backing a teammate. The shim routes by name prefix. Each adapter gets its own binary but shares the same team-protocol semantics.

## Design principle: protocol-first, lossy backend mappings are acceptable

The `--model` and `--effort` knobs are **protocol surfaces**, not backend leak-throughs. Every adapter accepts the same five-tier effort enum (`minimal`, `low`, `medium`, `high`, `xhigh`) and a `--model <slug>` pass-through. Each backend then maps those values to whatever its own CLI exposes — and that mapping is allowed to be lossy.

Examples already in the tree:

- **Codex** has graded `model_reasoning_effort` and accepts the five-tier enum natively, so the mapping is identity.
- **Gemini** maps the five tiers to adapter-owned `customAliases` thinking budgets (`minimal=0`, `low=512`, `medium=2048`, `high=4096`, `xhigh=8192`). Lossy on Gemini 3 (only three real thinking levels), faithful on Gemini 2.5.
- **Kimi** has only binary `--thinking` / `--no-thinking` — no graded effort. The adapter maps `minimal`/`low` → `--no-thinking` and `medium`/`high`/`xhigh` → thinking on. So `xhigh` and `medium` collapse to the same internal Kimi state. Lossy and intentional.

The right tradeoff for a multi-backend protocol is **standardize the surface, accept the lossy mapping at the edge.** The alternative — per-backend effort vocabularies — would force coordinating leads to know "Codex effort tiers" vs "Gemini thinking budgets" vs "Kimi thinking on/off" vs whatever GLM/DeepSeek/Qwen will ship next. That's the wrong direction.

Apply the same principle to future adapters and to any new protocol knob:

1. **Define the protocol-side enum once, in the broadest reasonable shape.** (Five tiers for effort because that's what Codex's range covers; that becomes the contract.)
2. **Map at the adapter boundary, not at call sites.** The control loop and team-cli both pass `--effort xhigh` blindly; only the per-backend `invoke.py` knows that for Kimi this means "no flag, use thinking-on default".
3. **Document the lossy mapping** in `docs/configuration.md` and the help skill so users understand `xhigh` is a request, not a guarantee.
4. **Don't leak backend-specific enums into the user surface.** No `--gemini-thinking-budget`, no `--kimi-thinking`. The five-tier `--effort` is enough.

The same applies to model slugs: every adapter accepts `--model <slug>` pass-through. claude-anyteam keeps no allowlist of its own — whatever the backend CLI accepts works. Documentation lists current defaults per backend without enforcing them.

When you add the next adapter (GLM, DeepSeek, generic API), default to this pattern. Reach for a backend-specific knob only when the protocol enum genuinely cannot represent the capability — and even then, prefer extending the protocol over adding a one-off escape hatch.

## Why no LLM wrapper

Every teammate in Claude Code's default Agent Teams is a Claude instance. Common designs for "bringing other models in" wrap those models inside a Claude teammate that treats the external model as a tool. That adds latency, double-charges tokens, and puts Claude's reasoning in the middle of decisions the external model should make directly.

claude-anyteam removes Claude from the path entirely. The external model is the teammate. The lead is still Claude, orchestrating — but the executor is whatever model you're pointing at. One layer of reasoning, not two.
