## Angle A: Bundle analysis

### Bundle location
- Active install on this system: `/home/rosado/.local/share/claude/versions/2.1.117` (resolved from `which claude` → `/home/rosado/.local/bin/claude`).
- Legacy npm wrapper also exists at `/usr/local/lib/node_modules/@anthropic-ai/claude-code/`, but the running `claude` command points at the native ELF binary, not the wrapper.

### TUI presence renderer
Local static analysis of the 2.1.117 native binary (`strings -a -n 4`) shows this minified render chain for the teammate presence UI:
- `h87(...)` renders one teammate row and prints `@${H.identity.agentName}`.
- `Li$(...)` builds the teammate tree and maps rows through `h87`.
- `$s9(...)` / the spinner/footer wrapper decides whether there are running teammates at all.
- `ea9(H){return H.tasks}` selects the task map.
- `ujH(H){return Object.values(H).filter(Mj)}` keeps only teammate-like tasks.
- `$e(H){return ujH(H).filter(t => t.status === "running")...}` sorts the running rows.
- `Mj(H)` is the type guard: `H.type === "in_process_teammate"`.

The same logic is visible in the leaked-source mirror with exact file:line mappings:
- `components/Spinner.tsx:184-238` — footer checks `getAllInProcessTeammateTasks(tasks)` / `hasRunningTeammates`.
- `components/Spinner/TeammateSpinnerTree.tsx:31-45,149-201` — tree reads `tasks` and calls `getRunningTeammatesSorted(tasks)`.
- `components/Spinner/TeammateSpinnerLine.tsx:124-145,202-224` — row renders `@{teammate.identity.agentName}` plus idle/activity state.
- `tasks/InProcessTeammateTask/InProcessTeammateTask.tsx:113-124` — `getAllInProcessTeammateTasks()` and `getRunningTeammatesSorted()`.

For the current native binary, the relevant minified symbols are present in the local strings dump around `/tmp/claude-2.1.117.strings.txt:560957-561519`.

### Data source
The presence line is driven by **live `AppState.tasks`**, not by `~/.claude/teams/{team}/config.json`.

More specifically, the TUI reads:
- `tasks[taskId].type === 'in_process_teammate'`
- `tasks[taskId].status === 'running'`
- `tasks[taskId].identity.agentName` for the visible `@name`
- `tasks[taskId].progress`, `isIdle`, and `messages` for status/previews

This is why simply adding a member to the team config does not create a visible row: the spinner tree never enumerates the team file directly.

### Population mechanism
There are two leader-side code paths that populate the task source used by the TUI:

1. **True in-process teammates**
   - `utils/swarm/spawnInProcess.ts:104-203`
   - `spawnInProcessTeammate()` builds an `InProcessTeammateTaskState` and calls `registerTask(taskState, setAppState)`.

2. **Out-of-process tmux/iTerm teammates mirrored into leader state**
   - `tools/shared/spawnMultiAgent.ts:474-486`
   - `tools/shared/spawnMultiAgent.ts:760-834`
   - `registerOutOfProcessTeammateTask()` intentionally creates another `InProcessTeammateTaskState`-shaped mirror and also calls `registerTask(...)`.
   - Important detail: even pane-based teammates are mirrored as `type: 'in_process_teammate'`, so the spinner code treats them exactly like in-process teammates.

That second path is the key result: the TUI row is really a **leader-side mirror task**, not a direct introspection of the child process.

### What the team config does (and does not do)
Team config helpers live in `utils/swarm/teamHelpers.ts:115-159` and only read/write `config.json`.

Teammate CLI identity flags (`--agent-id`, `--agent-name`, `--team-name`, etc.) are wired in `main.tsx:1193-1210`, but they only populate `dynamicTeamContext` for the spawned process itself. `computeInitialTeamContext()` then turns that into `teamContext` with `teammates: {}` in `utils/swarm/reconnection.ts:23-65`.

So:
- **Standalone launch with teammate flags:** sets that process's own team context.
- **Leader TUI presence row:** still requires a leader-side `tasks[...]` registration.

I did not find any `registerTask(...)` call in team-file or mailbox plumbing; the relevant registration sites are the spawn paths above.

### Can an external process make itself visible in the TUI?
**Yes — but only if the leader spawns it through the official teammate spawn flow (or an equivalent in-process task-registration shim).**

The cleanest built-in hook is:
- `CLAUDE_CODE_TEAMMATE_COMMAND`
- defined in `utils/swarm/constants.ts:17-21`
- used by `utils/swarm/spawnUtils.ts:18-28`

`getTeammateCommand()` checks that env var and, if set, uses it instead of the default Claude executable for spawned teammate processes. The current 2.1.117 native binary still contains this constant in its strings (`CLAUDE_CODE_TEAMMATE_COMMAND`). The lower-level internal injection point is `utils/task/framework.ts:77-99`, where `registerTask(task, setAppState)` writes the teammate mirror into `prev.tasks[task.id]`; the env-var route matters because it reaches that code without patching Claude itself.

That means the viable path is:
1. Run the leader normally.
2. Set `CLAUDE_CODE_TEAMMATE_COMMAND` in the leader environment to a custom shim/external binary.
3. Let Claude Code use its normal teammate spawn path.
4. The leader will still call `registerOutOfProcessTeammateTask(...)`, so the external teammate immediately appears in the TUI.

The external process then needs to honor the teammate contract enough to be useful (CLI identity args, mailbox polling/idle/shutdown behavior), but **visibility itself comes from the leader's mirror-task registration, not from the child self-registering**.

### Negative result: what does **not** work
I found no static evidence that the following can create a visible TUI row by themselves:
- editing `~/.claude/teams/{team}/config.json`
- adding fake members to the team file
- writing inbox/mailbox messages only
- launching a completely separate process with `--agent-id/--agent-name/--team-name` but without going through the leader's spawn path

Those mechanisms affect `teamContext`, mailboxes, or on-disk membership, but the presence renderer consumes `AppState.tasks`.

### Bottom line
The answer to the core question is:
- **Passive file-based injection:** no.
- **Leader-mediated external-process injection:** yes.
- **Best hook:** `CLAUDE_CODE_TEAMMATE_COMMAND` + the existing spawn flow, because that flow both launches the child and registers the leader-side mirror task that the TUI renders.

### Leak / reverse-engineering material consulted
- Axios on the March 31, 2026 Claude Code source-map leak: https://www.axios.com/2026/03/31/anthropic-leaked-source-code-ai
- Engineers Codex overview of the leak and community reverse engineering: https://read.engineerscodex.com/p/diving-into-claude-codes-source-code
- Community mirror used to map minified symbols to source files: https://github.com/renepardon/claude-code
- GitHub gist showing automated extraction from the leaked npm source map: https://gist.github.com/sorrycc/d77bcc8c2bfd0ac04d8d6ad98c413905
- Anthropic issue referencing the pulled 2.1.88 release and source-map exposure: https://github.com/anthropics/claude-code-action/issues/1135

## Angle B: Runtime observation

### Method
Compared a native Agent-tool spawn (`native-probe`, spawned by the leader session at
2026-04-22T21:22:26Z) against disposable external `claude-anyteam` launches. Used
`ps`, `lsof`, `ss`, `find` with mtime snapshots, and `/proc/<claude_pid>/fdinfo`
to observe what the live leader process touched.

### What native spawn leaves on disk
- `~/.claude/teams/<team>/config.json` — member appended
- `~/.claude/projects/<cwd-hash>/subagents/<agentId>.meta.json`
- `~/.claude/projects/<cwd-hash>/subagents/<agentId>.jsonl` (transcript)
- Session jsonl updates

### What the live leader actually watches
Nothing we observed on disk. The leader process is NOT holding `inotify` watches
on the teammate/subagent paths, and no persistent sockets/pipes carry presence
data.

### Negative injection test
Wrote a synthetic `ghost-inject@tui-research` into both `config.json` and
matching `~/.claude/projects/.../subagents/agent-a999runtimeinject.{meta.json,jsonl}`
files. User confirmed TUI showed only `@main @native-probe` — `ghost-inject`
was not picked up. Artifacts cleaned after test.

### Verdict
TUI presence is session-internal / in-memory Agent-runtime state. No externally
writable filesystem path, Unix socket, named pipe, or shm segment feeds it.
Passive injection from outside the leader process is not viable.

## Angle C: Public surfaces

### Inventory of documented/supported surfaces
- **Agent teams** (`~/.claude/teams/<team>/config.json`) — docs explicitly state
  this is auto-generated runtime state; editing is unsupported.
- **Subagent definitions** — only invokable from within a Claude Code session.
- **Plugins** — deliver tools/skills to a Claude session; cannot register a
  teammate at the AppState level.
- **Hooks** (`PreToolUse`, `PostToolUse`, `Stop`, etc.) — fire around Claude's
  own actions; no "on-teammate-spawn" or "external-teammate-announce" hook.
- **MCP servers** — deliver tools, not identities.
- **Channels** (`--channels`, `--dangerously-load-development-channels`) —
  developer-only; no public teammate-registration affordance.
- **Agent SDK** — lets you build agents; does not let an external agent
  announce itself to an existing Claude Code session.
- **`remote-control`** subcommand (hidden) — controls an existing Claude Code
  session programmatically; didn't find a `spawn-teammate` or equivalent.

### Closest-but-not-viable surfaces
- **Plugin subagents** (how `codex-jr`, `openai-codex` plugins work): the plugin
  registers a slash command that invokes a Claude sub-agent which then talks to
  `codex app-server`. The sub-agent IS visible in the TUI, but it's Claude-LLM-
  driven — exactly what this project avoids.
- **MCP tools** that a plugin can add — visible in tool call logs, but not in
  the teammate presence line.

### Hidden CLI flags discovered
Functional but omitted from `claude --help`:
- `--teammate-mode`
- `--channels`
- `--dangerously-load-development-channels`
- `remote-control`

None of these provide a teammate-registration affordance when probed.

### Verdict
No documented/supported public surface lets an arbitrary external process
register as a visible teammate in the Claude Code TUI without also being
LLM-driven from inside a Claude Code session.

## Summary + recommendation

Three angles converge on the same structural finding and one narrow escape
hatch.

### Finding
The Claude Code TUI presence line (`@main @<teammate>`) is rendered from the
leader session's in-memory `AppState.tasks` map, filtered to
`type === 'in_process_teammate'` and `status === 'running'`. The only code
paths that populate that map are `registerTask(...)` calls inside
`spawnInProcess.ts` and `spawnMultiAgent.ts`. There is no filesystem, socket,
pipe, MCP tool, hook, or plugin surface that a process outside the leader can
use to inject an entry into that map from the side.

An externally-launched `claude-anyteam` adapter can therefore never appear in
the TUI, regardless of what it writes to `config.json` or any other on-disk
state.

### Escape hatch: `CLAUDE_CODE_TEAMMATE_COMMAND`
Angle A found a **single** practical hook. The leader's teammate-spawn flow
(`utils/swarm/spawnUtils.ts:18-28`, `getTeammateCommand()`) honors the env var
`CLAUDE_CODE_TEAMMATE_COMMAND`. When set, the leader launches *that* binary
instead of the default Claude executable for every teammate spawn, and still
calls `registerOutOfProcessTeammateTask(...)` — which creates the leader-side
mirror task that the TUI reads. The child then becomes TUI-visible.

This is leader-mediated, not external: Claude Code must initiate the spawn
(via the Agent tool or equivalent) for the mirror task to be registered.

### Tradeoffs of the hook
- **Works:** TUI visibility for Codex teammates spawned through the leader.
- **Requires:** user runs Claude Code with `CLAUDE_CODE_TEAMMATE_COMMAND=<our-shim>`
  exported. Every teammate spawn from that session routes through the shim.
- **Shim responsibility:** inspect the spawn args (agent name, team name, etc.)
  and decide whether to exec `claude-anyteam` (for Codex-identified teammates)
  or forward to the real `claude` binary (for native teammates). Without this
  dispatch, you'd turn every Claude teammate into a Codex teammate by accident.
- **Loses:** the current externally-launched workflow (`setsid nohup uv run
  claude-anyteam`) — that flow can't benefit from this hook. Teammates must
  be spawned by the leader.
- **Does not reintroduce a Claude LLM wrapper:** the shim is a launch-time
  dispatcher, not a reasoning layer. The Codex process still talks directly
  to the adapter's protocol code with no Claude in the loop.

### Recommendation
**Viable clean path found.** If TUI visibility matters enough to shift the
spawn model from "user-launched external" to "leader-launched via shim," the
`CLAUDE_CODE_TEAMMATE_COMMAND` hook is the mechanism. Estimated adapter cost:

- A small shim binary (`claude-anyteam-spawn-shim`) that dispatches on the
  leader's spawn args.
- Documentation for the user to export `CLAUDE_CODE_TEAMMATE_COMMAND` before
  launching Claude Code.
- No changes to the protocol plumbing in the adapter itself.

If the current externally-launched workflow is preferred for operational
reasons (adapters surviving across Claude Code restarts, explicit launch
control, etc.), TUI invisibility is a structural consequence. Accept it or
run a parallel leader-spawned adapter for TUI-visible sessions.
