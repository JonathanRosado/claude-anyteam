# Architecture

claude-anyteam is now a multi-backend spawn-shim adapter. The same Claude Code teammate pane path provides TUI presence; backend routing is selected by teammate name (`codex-*`, `gemini-*`, or `kimi-*`). Codex retains its app-server path for mid-turn steering. Gemini and Kimi both use CLI-native transports with documented non-parity where their CLIs differ from Codex.

# Architecture

claude-anyteam is a protocol adapter, not an LLM wrapper. It lets external coding agents participate in Claude Code's [Agent Teams](https://code.claude.com/docs/en/agent-teams) protocol as first-class teammates without routing their reasoning through a Claude instance.

## The core principle: the harness IS the teammate

This is the architectural choice the rest of the design follows from, and it is also the moat. See [CLAUDE.md §1](../CLAUDE.md) for the full north-star statement.

A `codex-*` teammate does not run an LLM that "acts like Codex." It IS the Codex CLI process — full tool surface, App Server `turn/steer` mid-task injection and `thread/fork` cross-task memory, OpenAI's prompt tuning for the specific model slug, Codex's own approval/sandbox/working-directory semantics. Same for `gemini-*` (ACP transport, mid-turn permission bridge, Google's prompt tuning), `kimi-*` (native skills / swarm primitives, large-context behavior, Moonshot's prompt tuning), and any future `glm-*` / `deepseek-*` / `qwen-*` adapter.

What this means in practice:

1. **Capabilities flow through, they do not flatten.** When Codex has a feature Gemini does not (or vice versa), the protocol surfaces and routes that feature; it does not strip it to a lowest common denominator. The five-tier `--effort` and `--model` pass-through are protocol-side normalizations; *everything else is harness-native*.
2. **Peers learn each other's capabilities.** Capability declarations live in the team roster (registration); system prompts teach peers how to invoke unique features (for example, "ask `codex-*` teammates to use `thread/fork` for cross-task memory continuity"). The protocol carries the declarations; the prompts deliver the how-to.
3. **The protocol is a coordinator, not a kernel.** It carries identity, lifecycle, mailbox, task state, and capability declarations. It does *not* own the agentic loop, the tool definitions, or the model's prompt tuning. Those belong to each harness.

This implies two distinct protocol layers — see [CLAUDE.md §1 "The two layers"](../CLAUDE.md) for the canonical statement:

- **Transport** is the file-based Agent Teams contract everyone speaks (mailbox, tasks, lifecycle, locks). Universal; table stakes.
- **Capability** is per-harness: identity declaration, typed capability inventory (`turn_steer`, `thread_fork`, `permission_bridge`, `live_tool_events`, `large_context`, `native_swarm`, …), invocation schemas, and semantic guidance (when to use, when not to, failure modes). Lightweight flags live in `config.json` for roster discovery; the rich manifest is exposed via the wrapper MCP and loaded into peer context on demand — same precedent that already works for MCP tool descriptions, extended one level up.

The router-style alternative (proxy `ANTHROPIC_BASE_URL`, route through a single session loop, expose multiple models from one harness) loses every harness-specific feature: no Codex App Server, no Gemini ACP permission bridge, no Kimi swarm. It also has no capability layer at all — every teammate gets the host's tool surface, period. To match the claude-anyteam differentiator a router would have to throw out its session loop and rebuild around external process orchestration; at that point it is no longer a router.

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
│  • shared MCP wrapper for team tools    │
└─────────────────────────────────────────┘
```

Each layer has one job. The shim is a dispatcher. The adapter is the protocol implementation. Codex, Gemini, or Kimi handles the backend reasoning based on the teammate name prefix.

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
2. Adapter picks it up in its poll loop (1.5s default)
3. Adapter claims it via compare-and-set under a file lock
4. Adapter sends the task description to the selected backend: Codex via App Server or fresh `codex exec`; Gemini via headless `gemini --prompt ... --output-format stream-json`; Kimi via headless `kimi --print --output-format stream-json`
5. The backend executes: reads files, writes files, runs commands, calls wrapper MCP tools to update task status / send messages to peers
6. Task completes; adapter writes `task_complete` to lead's inbox
7. Codex App Server teammates can incorporate peer messages mid-execution via `turn/steer`; Gemini and Kimi teammates currently receive peer messages on the next poll / prompt boundary rather than live App Server steering.

The wrapper MCP server exposes a narrowed 6-tool surface to external backends (`send_message`, `task_update`, `task_create`, `read_inbox`, `task_list`, `read_config`). Destructive tools like `team_delete` and `force_kill_teammate` are deliberately blocked — Codex, Gemini, or Kimi has full coding access but cannot break the team.

## Extending to new models

The same architecture supports any CLI-native model. Each new adapter is:

1. A Python module that implements the shared protocol interface (inbox polling, task claiming, result writing — most of this is already shared code)
2. A model-specific invocation path (e.g. headless `gemini --prompt ...`, `kimi --print --output-format stream-json ...`)
3. One entry in the spawn shim's routing table (e.g. `gemini-*` → gemini adapter)

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
