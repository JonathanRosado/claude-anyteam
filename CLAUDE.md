# CLAUDE.md

## North star §1: harness preservation (the architectural moat)

**The harness IS the teammate, not a wrapped LLM.** claude-anyteam's competitive advantage is that we bring the actual CLI / agent / harness as a first-class team member, not a model behind a router. That is the architecture, not an optimization layered on top of it.

When you spawn a `codex-*` teammate you inherit, intact and natively:

- Codex CLI's full tool surface (host shell + file tools, plus whatever Codex ships next)
- App Server's `turn/steer` mid-task injection and `thread/fork` cross-task memory
- OpenAI's prompt tuning for the specific model slug (gpt-5.5, gpt-5.4-codex, etc.)
- Codex's own approval policy, sandbox, working-directory semantics

Same for `gemini-*` (ACP transport, mid-turn permission bridge, Google's prompt tuning), `kimi-*` (native skills/swarm primitives, large-context behavior, Moonshot's prompt tuning), and any future `glm-*` / `deepseek-*` / `qwen-*`.

**The design rule, absolute: never constrain a teammate to a feature subset that is representable across all backends.** The protocol's job is to *carry capability declarations and route activity*, not flatten capabilities to a lowest common denominator. Where a protocol-side knob can standardize cleanly (the five-tier `--effort`, `--model` pass-through), do that. Where it can't, the unique capability flows through to the team unmodified.

When a teammate has a feature its peers don't (Codex `thread/fork`, Gemini ACP permission bridge, Kimi internal swarm, Codex `turn/steer`), the team must:

1. **Surface the capability** via registration / capability-declaration so peers know it exists.
2. **Teach peers how to invoke it** (system prompt guidance: "ask `codex-*` teammates to use `thread/fork` for cross-task memory continuity").
3. **Never pretend it doesn't exist or strip it down** to a homogeneous shape.

To match this, a router-based competitor (OpenCode, claude-code-router, ANTHROPIC_BASE_URL proxies, every framework that puts a router between the lead and a model) would have to throw out their session loop and rebuild around external process orchestration. They won't, because that breaks their entire model. **The moat is "preserve harness capabilities natively across a team," not "expose more models from a single loop."** That's the only differentiator durable enough to invest in.

When evaluating any change: ask **"does this preserve every harness's unique capabilities, or does it flatten them?"** Flatten = no.

### The two layers

The protocol is two layers, not one — conflating them is what makes router approaches collapse:

- **Transport** — Agent Teams as Claude Code ships it: mailbox JSON, atomic task claims, lifecycle (idle / shutdown / plan-approval / permission), config and task file shapes, locks. Universal; table stakes for being a teammate at all.
- **Capability** — Each harness advertises what it *uniquely* can do, and tells peers when and how to invoke those primitives. Per-harness, MCP-style: schema + description + when-to-use + failure modes + version flag.

Routers skip the capability layer entirely; every teammate gets the host's tool surface, period. They cannot bolt it on later because their architecture has no place for it. We can — that's the moat.

The capability layer needs, per teammate:

1. **Identity** — harness name, version, transport, model slug.
2. **Capabilities** — typed list of unique primitives (`turn_steer`, `thread_fork`, `permission_bridge`, `live_tool_events`, `large_context`, `native_swarm`, …) with version flags so peers degrade gracefully when a capability is absent or its shape changes.
3. **Invocation schema** — for each capability, the input shape and call surface (mirrors MCP tool descriptions).
4. **Semantic guidance** — when to use, when not to, expected failure modes. This is the "teach" half: without it peers know a capability exists but not when invoking is the right move.

Implementation: lightweight flags in `config.json` (`members[].capabilities`) for cheap roster discovery; the rich manifest (schema + description + guidance + failure modes) is exposed via the wrapper MCP and loaded into peer context only when a peer is about to invoke. Same MCP precedent that already works as a capability surface across heterogeneous tools — extend it from "what tools can I call" to "what unique primitives does this teammate offer."

When evaluating a change: ask **"is this transport-layer or capability-layer? If transport, does it preserve universality? If capability, does it preserve harness specificity AND teach peers when to use it?"** Both layers have failure modes; conflating them produces routers.

## North star §2: visibility parity (the operational consequence)

Harness preservation only delivers if the lead can *see* what each harness's teammate is doing. When a Claude lead spawns **native Claude** teammates, they see tool calls, prose deltas, idle reasons, and peer DMs in real time. Routed teammates (`codex-*`, `gemini-*`, `kimi-*`) should give the lead the **same operational visibility** as native Claude teammates.

This is the design lens for every observability, diagnostic, wrapper, prompt, or protocol decision in this repo:

- A bug or quality gap in a routed teammate should be **as visible to the lead** as the same gap in a native teammate.
- Host-tool activity (Read / Edit / Write / Bash) inside the routed CLI should **surface to the lead**, not stay hidden inside the wrapper.
- Errors and timeouts should never collapse to a generic prose fallback — they should carry diagnostic detail comparable to what the host shows for native errors.
- The lead should not need to read tmux pane stderr to understand what their teammate is doing.

When evaluating any change: ask **"does this narrow the visibility gap or widen it?"** Push back if it widens.

This is stronger than the existing "TUI parity" goal in `docs/architecture.md`. TUI parity says "routed teammates *appear* in the presence line like natives." Visibility parity says "routed teammates are *operationally observable* like natives."

## North star §3: peer efficiency (the team productivity invariant)

Native Claude teammates collaborate at low friction — they `SendMessage` each other directly, hand off tasks atomically, steer each other mid-turn, and idle notifications don't drown out substantive content. Routed teammates must match that. **The team is only as fast as its slowest peer-to-peer hand-off.**

§1 protects the *capabilities* peers can declare; §2 protects the *visibility* the lead has into peers; §3 protects the *coordination fidelity* among peers themselves. Peer-to-peer is a distinct surface from lead-to-peer and has its own failure modes.

Concrete failure modes to avoid:

- **Peer-DM gaps.** A teammate's wrapper MCP must expose `send_message` to peers, not only to lead; missing exposure produces "I don't have access to a `send_message` MCP tool" error prose instead of clean delivery (observed during the proto-rev research session, 2026-04-27).
- **Idle noise crowding out signal.** Heartbeat idle notifications must be distinguishable from substantive peer messages; clients (lead and peers) must filter cheaply by message kind, not by parsing prose.
- **Steer authorization too narrow.** Lead-only steering authorization (`protocol_io.py:291`) blocks peers from interrupting each other on legitimate work; a Codex executor should be able to steer a Codex researcher mid-turn when the lead is offline. Steer authorization is itself a capability declaration (per §1).
- **Capability discovery latency.** Manifest must be cached at team formation, not lazily fetched per invocation; peers should not pay round-trip costs every time they consider invoking a peer's primitive.
- **Task hand-off race conditions.** Atomic claim semantics must extend to peer-initiated reassignments, not only lead-initiated ones.
- **Double-send / canned-fallback noise.** The PR-#11/#12 "delivered_via_tool" guards must be uniform across all backends so peers don't receive a structured reply *and* a canned prose fallback for the same answer.

Design implications: peer-to-peer messaging is a first-class wrapper MCP surface, not a privileged subset of lead-to-peer; inbox event types are versioned so heartbeat / substantive / lifecycle can be distinguished by consumers without parsing prose; capability manifests are loaded into peer context at team-formation time; steer authorization is capability-declared (some teammates accept peer steers, some don't, and that fact is in the manifest).

When evaluating any change: ask **"does this change peer-to-peer coordination fidelity?"** Match native Claude's pace.

## How the three north stars relate

§1 is the architecture; §2 and §3 are the consequences for the two audiences that observe the team:

- §1 (harness preservation) — capabilities flow through; the protocol carries declarations, not flattens them.
- §2 (visibility parity) — the lead sees routed teammates as clearly as native Claude teammates.
- §3 (peer efficiency) — teammates coordinate among themselves at native Claude pace.

Without §1 we are building a router and the moat collapses. Without §2 we are preserving capabilities the lead can't observe and the moat is invisible. Without §3 we are preserving capabilities and lead visibility but the *team* is slower than a pure-Claude team — which means heterogeneous teams are technically feasible but practically inferior, and adoption stalls. All three ship together.

If you ever face a tradeoff between "cleaner / more uniform protocol surface" and "preserves a unique harness capability," you choose the harness capability and grow the protocol to carry it. If you face a tradeoff between "fewer wire-message types" and "peer can distinguish heartbeat from substance without parsing prose," you grow the typed-message catalog.

## Project shape (quick orientation)

claude-anyteam routes Claude Code teammates by name prefix (`codex-*`, `gemini-*`, `kimi-*`) to external CLI agents. See `docs/architecture.md` for the full design.

Key directories:

- `src/claude_anyteam/` — adapter, wrapper server, backends, spawn shim, CLI
- `src/claude_teams/` — team-protocol implementation (file-based mailbox, locking, config)
- `schemas/` — JSON schemas validated by the adapter (must ship in the wheel)
- `docs/architecture.md`, `docs/roadmap.md` — design rationale and shipping plan
