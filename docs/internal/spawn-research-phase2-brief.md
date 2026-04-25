# Spawn research — phase 2 brief

**Goal:** Find a reliable way for a genuine Codex-backed teammate to appear in the TUI presence line on BOTH tmux AND non-tmux hosts.

**What we've established (phase 1, SOLID, do not re-litigate):**
- Shim fires only when PaneBackendExecutor subprocess-spawns the teammate (i.e. tmux/iTerm2 + interactive + not-short-circuited)
- In-process `spawnInProcessTeammate()` path never calls the shim, but DOES populate `AppState.tasks` via internal `registerTask()` → gives TUI presence — but the backend is Claude, always
- Path B (`setsid nohup claude-anyteam`) gives real Codex backing but never populates `AppState.tasks` → no TUI presence on non-tmux
- This is the UX gap: you can have TUI presence OR real Codex backing on non-tmux, not both

**Phase 2 question:** Is there an approach — however unconventional, including reverse-engineered internals — that makes Path B adapters appear in TUI presence, OR that forces Path A to fire on non-tmux hosts?

## Research threads

### 1. Can we get a Path B adapter into `AppState.tasks`?

`AppState.tasks` is a Node.js in-memory object inside Claude Code's process. `registerTask()` and `registerOutOfProcessTeammateTask()` are the known insertion functions. Questions:

- What EXACT data shape does each function insert? Full type definition of `InProcessTeammateTaskState`.
- Is there ANY IPC/RPC/filesystem signal Claude Code watches that triggers a `registerTask` call externally?
- Does Claude Code re-read `~/.claude/teams/<team>/config.json` on any event (member add, heartbeat, timer)? If so, under what conditions does it mirror new members into `AppState.tasks`?
- Is there a documented or reverse-engineered plugin/MCP hook that lets a plugin call `registerTask()` on the leader's behalf?

### 2. Can we force `PaneBackendExecutor` to fire on a non-tmux host?

`isInProcessEnabled()` (`Rb()`) short-circuits on non-interactive. Questions:

- Does `--teammate-mode=tmux` CLI flag bypass the non-interactive check? Or does the flag only apply after the check?
- Are there env vars (`CLAUDE_CODE_FORCE_PANE_BACKEND`, `CLAUDE_CODE_TEAMMATE_MODE`, `TMUX`) that can make `Rb()` return false?
- Can we start Claude Code inside a tmux session programmatically? A thin "fake tmux" wrapper? An expect-script that drops Claude Code into a tmux pane?
- Does `process.stdout.isTTY` / `process.stdin.isTTY` matter for the interactive check? Can we fake TTY via `script` or `socat`?

### 3. Is there a NEW or undocumented hook?

- Issue #26572 (CustomPaneBackend proposal by rasmusab/KILD): is there a PR or implementation? Did it ship?
- Does Claude Code expose any MCP server on the leader side that plugins can call to register teammates?
- Are there any env vars prefixed `CLAUDE_CODE_` or settings keys in `settings.json` that look relevant and are undocumented?
- Is there a feature flag like `tengu_amber_flint` that enables a different spawn mechanism?

### 4. Hybrid: hijack the in-process spawn

What if `Agent(name="codex-alice")` is called by the leader, `spawnInProcessTeammate()` runs, but the in-process coroutine's prompts get routed to Codex via an MCP tool or similar indirection? The teammate is "in-process" from Claude Code's perspective but delegates actual generation to Codex. Questions:

- Does Claude Code expose the model-inference call as a hookable primitive?
- Can a subagent declare its own backend via MCP stdio?
- Is there a `--model-provider` or similar that can point at an OpenAI-compatible endpoint?

### 5. Alternative: run Claude Code INSIDE tmux transparently

What if the plugin's installer detects tmux absence and offers to wrap Claude Code launches in a tmux session automatically?

- Can the plugin install a shell function/alias that transparently starts tmux if not already in one?
- Is there a documented way to auto-attach a started Claude Code to a tmux session?
- What's the UX impact of always-in-tmux (copy-paste, scrollback, keybindings)?

## Constraints (unchanged from phase 1)

- Hard rule: no modifications to `src/claude_anyteam/**` without reading the module in full and getting user sign-off.
- Every claim cited — docs URL, RE repo file:line, empirical observation.
- Memory is polluted. Verify with web + code, not recall.
- If an experiment needs user approval (env var change, tmux launch, new subprocess pattern), ASK via SendMessage to team-lead. Don't assume.

## Task breakdown

| # | Task | Owner | Priority |
|---|------|-------|----------|
| 8 | Pull full source of `registerTask`, `registerOutOfProcessTeammateTask`, `InProcessTeammateTaskState` type from RE repo. Map the exact data shape. | re-scout | P0 |
| 9 | Hunt for any IPC/filesystem-signal mechanism by which Claude Code learns of external teammates (members.json watchers, inotify, MCP tool exposed to plugins). | code-auditor | P0 |
| 10 | Research issue #26572 status, any related PRs, any Anthropic mentions of CustomPaneBackend. Check changelog for new spawn-related features. | docs-scout | P0 |
| 11 | Empirical: can `TMUX=<fake>` env var, `--teammate-mode=tmux` flag, or similar force pane-backend selection? Test without restarting Claude Code — see what's settable from inside a running session via tool invocations. | experimenter | P0 |
| 12 | Research: does Claude Code have a leader-side MCP or plugin API where a custom tool could call something analogous to `registerTask()` on behalf of an external process? | re-scout | P1 |
| 13 | Research: feasibility of a "fake tmux" shim — a thin `tmux` binary on PATH that claims tmux is active to satisfy Claude Code's detection. | experimenter | P1 |
| 14 | Research: hybrid approach — can `Agent()` spawn produce a Claude subagent whose model inference is actually routed to Codex via an MCP tool or model-provider override? | re-scout + docs-scout | P2 |

## Definition of done

We are done when we can produce a concrete solution OR a firm "it's not possible without Claude Code changes" verdict backed by evidence. A solution qualifies if:

1. On BOTH tmux and non-tmux hosts, `codex-*` teammates appear in TUI presence with real Codex backing (model: gpt-5.5, actual Codex adapter running).
2. The solution is reproducible, documented, and doesn't require the user to manually run setsid commands.
3. The solution doesn't break any existing claude-anyteam behavior.
4. Backed by at least 2 independent evidence sources for every load-bearing claim.

A "not possible" verdict must cite the specific reason (architectural, licensed, unhookable) and describe what Anthropic would need to change upstream.
