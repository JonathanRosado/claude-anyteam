# CLAUDE.md

## North star: visibility parity

When a Claude lead spawns **native Claude** teammates, they see tool calls, prose deltas, idle reasons, and peer DMs in real time. Routed teammates (`codex-*`, `gemini-*`, `kimi-*`) should give the lead the **same operational visibility** as native Claude teammates.

This is the design lens for every observability, diagnostic, wrapper, prompt, or protocol decision in this repo:

- A bug or quality gap in a routed teammate should be **as visible to the lead** as the same gap in a native teammate.
- Host-tool activity (Read / Edit / Write / Bash) inside the routed CLI should **surface to the lead**, not stay hidden inside the wrapper.
- Errors and timeouts should never collapse to a generic prose fallback — they should carry diagnostic detail comparable to what the host shows for native errors.
- The lead should not need to read tmux pane stderr to understand what their teammate is doing.

When evaluating any change: ask **"does this narrow the visibility gap or widen it?"** Push back if it widens.

This is stronger than the existing "TUI parity" goal in `docs/architecture.md`. TUI parity says "routed teammates *appear* in the presence line like natives." Visibility parity says "routed teammates are *operationally observable* like natives."

## Project shape (quick orientation)

claude-anyteam routes Claude Code teammates by name prefix (`codex-*`, `gemini-*`, `kimi-*`) to external CLI agents. See `docs/architecture.md` for the full design.

Key directories:

- `src/claude_anyteam/` — adapter, wrapper server, backends, spawn shim, CLI
- `src/claude_teams/` — team-protocol implementation (file-based mailbox, locking, config)
- `schemas/` — JSON schemas validated by the adapter (must ship in the wheel)
- `docs/architecture.md`, `docs/roadmap.md` — design rationale and shipping plan
