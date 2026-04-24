# Architecture

claude-anyteam is now a multi-backend spawn-shim adapter. The same Claude Code teammate pane path provides TUI presence; backend routing is selected by teammate name (`codex-*` or `gemini-*`). Codex retains its app-server path for mid-turn steering. Gemini currently uses headless CLI Plan A and documents non-parity in `docs/gemini-adapter-limitations.md`.

# Architecture

claude-anyteam is a protocol adapter, not an LLM wrapper. It lets external coding agents participate in Claude Code's [Agent Teams](https://code.claude.com/docs/en/agent-teams) protocol as first-class teammates without routing their reasoning through a Claude instance.

## The core insight

Claude Code's Agent Teams feature is file-based. Team state lives in `~/.claude/teams/{team}/config.json` and inbox messages in `~/.claude/teams/{team}/inboxes/{name}.json`. The team protocol is an on-disk contract — mailbox polling, atomic task claims, idle notifications, shutdown requests. Any process that speaks this contract can be a teammate.

claude-anyteam speaks the contract directly. It reads your inbox, claims tasks, delegates them to an external model, and writes results back. No Claude LLM sits between you and the external model.

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
│  • routes `codex-*` → adapter           │
│  • forwards anything else → native claude
└──────────────┬──────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────┐
│  claude-anyteam (Python adapter)        │
│  • self-registers in config.json        │
│  • polls inbox, claims tasks            │
│  • invokes codex via JSON-RPC           │
│  • writes task_complete to inbox        │
└──────────────┬──────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────┐
│  codex app-server                       │
│  • long-lived JSON-RPC session          │
│  • turn/steer for mid-task reactivity   │
│  • thread/fork for cross-task memory    │
└─────────────────────────────────────────┘
```

Each layer has one job. The shim is a 180-line dispatcher. The adapter is the protocol implementation. Codex handles all reasoning.

## Two execution modes

**App Server (default).** `codex app-server` runs as a long-lived JSON-RPC session. The adapter manages the thread lifecycle, injects mid-task input via `turn/steer`, and forks cross-task memory via `thread/fork`. This is where the native-teammate behaviors live: if a peer messages you while you're working, the in-flight turn reshapes instead of losing the message; each new task inherits your conversational history from the previous one.

**Fresh-exec (opt-out).** Each task spawns `codex exec` fresh. Second and subsequent tasks use `codex exec resume <session_id>` so context carries forward. No mid-task reactivity, but simpler operationally. Enable with `--no-app-server` or `CLAUDE_ANYTEAM_APP_SERVER=false`.

## How teammates become visible

The TUI presence line (`@main @codex-alice`) renders from the leader's in-memory state, not from `config.json`. That state is only populated when Claude Code's own spawn flow is what launched the teammate.

claude-anyteam hooks into that spawn flow via `CLAUDE_CODE_TEAMMATE_COMMAND`. When Agent Teams mode spawns a teammate:

1. Claude Code invokes `$CLAUDE_CODE_TEAMMATE_COMMAND` (our shim) instead of the default `claude` binary
2. The shim checks the agent name. Matches `^codex-`? Dispatches to the adapter
3. Claude Code's internal spawn-completion callback registers a mirror task in its state — this is what the TUI renders
4. The adapter self-registers in `config.json` with `backendType: "in-process"` so its entry matches what the leader expects

Both pieces (leader mirror + adapter entry) are required. The shim enables step 1. The adapter handles step 4.

## What happens per task

1. Lead creates a task via Claude Code's task list
2. Adapter picks it up in its poll loop (1.5s default)
3. Adapter claims it via compare-and-set under a file lock
4. Adapter sends the task description to Codex via App Server
5. Codex executes: reads files, writes files, runs commands, calls wrapper MCP tools to update task status / send messages to peers
6. Task completes; adapter writes `task_complete` to lead's inbox
7. If a peer sent a message mid-execution, adapter injected it via `turn/steer` and Codex incorporated it

The wrapper MCP server exposes a narrowed 6-tool surface to Codex (`send_message`, `task_update`, `task_create`, `read_inbox`, `task_list`, `read_config`). Destructive tools like `team_delete` and `force_kill_teammate` are deliberately blocked — Codex has full coding access but cannot break the team.

## Extending to new models

The same architecture supports any CLI-native model. Each new adapter is:

1. A Python module that implements the shared protocol interface (inbox polling, task claiming, result writing — most of this is already shared code)
2. A model-specific invocation path (e.g. `gemini exec`, `kimi run`)
3. One entry in the spawn shim's routing table (e.g. `gemini-*` → gemini adapter)

The protocol layer doesn't care which model is backing a teammate. The shim routes by name prefix. Each adapter gets its own binary but shares the same team-protocol semantics.

## Why no LLM wrapper

Every teammate in Claude Code's default Agent Teams is a Claude instance. Common designs for "bringing other models in" wrap those models inside a Claude teammate that treats the external model as a tool. That adds latency, double-charges tokens, and puts Claude's reasoning in the middle of decisions the external model should make directly.

claude-anyteam removes Claude from the path entirely. The external model is the teammate. The lead is still Claude, orchestrating — but the executor is whatever model you're pointing at. One layer of reasoning, not two.
