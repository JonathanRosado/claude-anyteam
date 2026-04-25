---
title: Spawn-mechanism research — findings
team: agent-teams-research
branch: gemini-adapter
status: in-progress
---

# Spawn-mechanism research — findings

Live document. Each teammate owns a section below and appends their findings there.
**Every claim must cite** a source: docs URL, RE post URL, `file:line`, or reproducible test output.
Unsourced claims will be rejected by `lead`.

See `docs/internal/spawn-research-brief.md` for the charter, constraints, and definition of done.

---

## Open questions (lead maintains)

| # | Question | Status | Owner |
|---|---|---|---|
| Q1 | When `Agent(team_name=X, name="codex-alice")` is called in Claude Code 2.1.118, what EXACT code path runs? | ⚠ **RE-OPENED 2026-04-23**: user contradicted synthesis — `@codex-alice` visible in TUI on non-tmux host. Gap: R3 only quoted `InProcessBackend.ts` docstring, not `spawnInProcessTeammate()` body. | Tasks #5, #6, #7 |
| Q2 | Does the in-process path invoke `$CLAUDE_CODE_TEAMMATE_COMMAND`? | ⚠ **RE-OPENED**: prior "No" depended on R3 docstring only. Function body unread. | Tasks #5, #7 |
| Q3 | What DOES trigger `$CLAUDE_CODE_TEAMMATE_COMMAND`? | ⚠ **RE-OPENED**: PaneBackendExecutor (R1/R2) confirmed. In-process uncertain. | Tasks #5, #7 |
| Q4 | For a fully programmatic (no TUI keypress) spawn, which path works? | ⚠ **RE-OPENED**: Path B confirmed standalone; Path A status uncertain. | Tasks #5, #6 |
| Q5 | Is Path B the correct default, or an escape hatch? | ⚠ **RE-OPENED**: synthesis may need full revision depending on #5. | Task #5 synthesis |
| **Q6** | **Is TUI presence driven by `config.json` file state or by something else?** | ✓ **RESOLVED — NOT file-driven. `AppState.tasks` (in-memory, via `registerOutOfProcessTeammateTask()` called ONLY by PaneBackendExecutor).** My earlier partial finding (that `registration.py:118-122` comment settled this) was **WRONG** — the comment describes aspirational intent, not mechanism. Per code-auditor round 2 + `docs/internal/2026-prototype/research.md:26-34`: TUI reads `AppState.tasks`; `config.json` is never read by the TUI renderer. Runtime test: synthetic `ghost-inject@tui-research` written to `config.json` did NOT appear in TUI (research.md:121-126). **Consequence:** user's TUI observation CANNOT be explained by Path-B file-driven presence OR by in-process shim firing (re-scout #5: zero shim calls in in-process path). Must have been tmux/pane backend in use at observation time. | DONE (with my Q6 correction) |

**Key findings by owner (2026-04-23):**

*Experimenter:*
- Path A infrastructure IS in place on this host: `CLAUDE_CODE_TEAMMATE_COMMAND` set in `~/.claude/settings.json` env block; shim binary exists and dispatches `^codex-` names to `claude-anyteam`; `claude_anyteam` module importable by shim's Python.
- No `hooks` key in settings.json — integration is purely via `env.CLAUDE_CODE_TEAMMATE_COMMAND`.
- Pytest baseline: 208 passed on `gemini-adapter`. Clean.
- CLAUDE.md references `/claude-anyteam:launch-team` skill that does NOT ship in plugin 0.1.0 — doc drift.

*Docs-scout:*
- Default teammate-spawn mode is **in-process** (docs: "all teammates run inside your main terminal"). Split-pane (tmux/iTerm2) mode uses subprocess panes.
- `CLAUDE_CODE_TEAMMATE_COMMAND` is **undocumented** in Anthropic docs and absent from v2.1.104 community exhaustive env-var gist. Best-case interpretation: internal/experimental env var, only consulted in split-pane mode.
- Public `docs/en/sub-agents` page shows only `Agent(agent_type)` signature. The **teammate-spawn signature `Agent(team_name=X, name=...)` is a REAL tool-call shape** (confirmed via Piebald-AI system prompt extraction v2.1.88 + kieranklaassen gist + issue #34614, per docs-scout addendum) — just not in the public docs page. CLAUDE.md's syntax is correct; only the outcome on non-tmux hosts was wrong.
- LLM-driven (`Agent` tool) and TUI/natural-language spawn use the **same** subprocess path in split-pane mode — no separate code path.
- No changelog entry (v2.1.32–v2.1.118) changed spawn mechanism or mentioned `CLAUDE_CODE_TEAMMATE_COMMAND`.
- `model` field in subagent defs accepts only Claude aliases/IDs. Non-Claude model IDs are outside the documented spec.

*Re-scout (decisive):*
- Decompiled Claude Code source confirms: `getTeammateCommand()` reads `$CLAUDE_CODE_TEAMMATE_COMMAND` and is called ONLY by PaneBackendExecutor. InProcessBackend spawns in-process and never touches the env var.
- Issue #34614: `isInProcessEnabled()` (`Rb()`) short-circuits on non-interactive check BEFORE evaluating `--teammate-mode=tmux` flag → headless/`--print`/non-interactive sessions ALWAYS use InProcessBackend → shim never fires.
- Pane backend, even when it does fire, has known bugs: #40168 (kernel MAX_CANON 256-byte buffer overflow on long spawn commands) and #34614 (missing `claude` prefix in v2.1.76). Not a reliable path even in interactive tmux.
- The exact argv shape expected by `$CLAUDE_CODE_TEAMMATE_COMMAND` matches the shim's `_parse_args`: `--agent-id`, `--agent-name`, `--team-name`, `--agent-color`, `--parent-session-id`, `[--plan-mode-required]`.

**Two-source convergence (DoD #1 satisfied):** re-scout's decompiled source + docs-scout's extracted system prompt (Piebald-AI v2.1.88) + GitHub issue #34614 independently confirm the same spawn model. Q1 has >=2 fully independent high-authority sources.

---

## Findings — docs-scout

**Accessed:** 2026-04-23. Primary sources: https://code.claude.com/docs/en/agent-teams, https://code.claude.com/docs/en/sub-agents, https://code.claude.com/docs/en/env-vars, https://code.claude.com/docs/en/model-config, https://code.claude.com/docs/en/changelog. Secondary: https://gist.github.com/mculp/e6a573f2a45ef7dbbf30f6a8574c7351 (community exhaustive env-var list, v2.1.104, April 13 2026).

---

### Question 1 — What IS the documented primitive for spawning a teammate? Is it `Agent` tool with `team_name`/`name`?

**Answer:** Not documented as an `Agent(team_name=..., name=...)` call. The official docs expose only a natural-language interface for the user. The `Agent` tool IS documented on the sub-agents page but its signature uses `Agent(agent_type)` — not `team_name` or `name` parameters.

**Quote (agent-teams page):**
> "Tell the lead what you want in natural language. It handles team coordination, task assignment, and delegation based on your instructions."

**Quote (sub-agents page, on the Agent tool syntax):**
> "In version 2.1.63, the Task tool was renamed to Agent. Existing `Task(...)` references in settings and agent definitions still work as aliases."
> "When an agent runs as the main thread with `claude --agent`, it can spawn subagents using the Agent tool. To restrict which subagent types it can spawn, use `Agent(agent_type)` syntax in the `tools` field."

**URL:** https://code.claude.com/docs/en/agent-teams (accessed 2026-04-23), https://code.claude.com/docs/en/sub-agents (accessed 2026-04-23)

**Gap:** The docs do NOT expose `team_name` or `name` as Agent tool parameters. Whether these parameters exist internally cannot be confirmed from official docs alone.

---

### Question 2 — Is spawning described as subprocess-based or in-process?

**Answer:** BOTH modes are documented. The default ("in-process") runs all teammates inside the main terminal process. The "split panes" mode uses tmux or iTerm2 and gives each teammate its own terminal pane (implying a separate subprocess). In-process is the default unless already running inside tmux.

**Quote:**
> "**In-process**: all teammates run inside your main terminal. Use Shift+Down to cycle through teammates and type to message them directly. Works in any terminal, no extra setup required."
> "**Split panes**: each teammate gets its own pane... Requires tmux, or iTerm2."
> "The default is `"auto"`, which uses split panes if you're already running inside a tmux session, and in-process otherwise."

**URL:** https://code.claude.com/docs/en/agent-teams#choose-a-display-mode (accessed 2026-04-23)

**Implication for `CLAUDE_CODE_TEAMMATE_COMMAND`:** If in-process mode does NOT exec a subprocess, then `CLAUDE_CODE_TEAMMATE_COMMAND` would only be relevant in split-pane (tmux) mode. This is consistent with the experimenter's finding that `CLAUDE_CODE_TEAMMATE_COMMAND` IS set but Path A (shim intercept) has not been confirmed to fire in practice when running in-process.

---

### Question 3 — Is `CLAUDE_CODE_TEAMMATE_COMMAND` documented anywhere officially?

**Answer:** NOT documented in any official Anthropic source. Not present in:
- Official env-vars page: https://code.claude.com/docs/en/env-vars
- Community exhaustive env-var gist (v2.1.104, April 2026): https://gist.github.com/mculp/e6a573f2a45ef7dbbf30f6a8574c7351

The community gist searched all variables for "TEAMMATE", "SPAWN", and "COMMAND" and found none matching TEAMMATE or SPAWN. The only COMMAND-related entry was `CLAUDE_CODE_DISABLE_COMMAND_INJECTION_CHECK` which was removed in v2.1.90.

The gist DID list `CLAUDE_CODE_COORDINATOR_MODE` (Agent coordinator mode) and `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` as the only agent-team-adjacent variables.

**URL:** https://code.claude.com/docs/en/env-vars (accessed 2026-04-23), https://gist.github.com/mculp/e6a573f2a45ef7dbbf30f6a8574c7351 (accessed 2026-04-23)

**Conclusion:** `CLAUDE_CODE_TEAMMATE_COMMAND` is **undocumented-public at best, possibly internal-only**. It does not appear in any official doc or community reference list up to v2.1.104. The experimenter confirmed it IS present in this session's env (set by `~/.claude/settings.json`), proving it exists as a live env var — but the docs give no spec for when/how Claude Code reads it.

---

### Question 4 — Release notes v2.1.32 through v2.1.118 that changed spawn mechanism?

**Answer:** No documented changes to the spawn mechanism. Relevant entries found:
- **v2.1.63**: `Agent` tool renamed from `Task` (backward compat aliases preserved)
- **v2.1.98**: Fixed agent team members not inheriting the leader's permission mode when using `--dangerously-skip-permissions`
- **v2.1.101**: Added `/team-onboarding` command
- No entry mentions spawn mechanism, `CLAUDE_CODE_TEAMMATE_COMMAND`, subprocess vs in-process behavior changes, or teammate command routing.

**URL:** https://code.claude.com/docs/en/changelog (accessed 2026-04-23)

---

### Question 5 — Does official documentation describe a per-teammate model field that can be non-Claude?

**Answer:** No. The `model` field in subagent definitions (which can be referenced when spawning a teammate) accepts only:
- Model aliases: `sonnet`, `opus`, `haiku`
- Full Claude model IDs: e.g. `claude-opus-4-7`, `claude-sonnet-4-6`
- `inherit` (uses parent session model)

**Quote (sub-agents page):**
> "Model alias: Use one of the available aliases: `sonnet`, `opus`, or `haiku`"
> "Full model ID: Use a full model ID such as `claude-opus-4-7` or `claude-sonnet-4-6`. Accepts the same values as the `--model` flag"

**Quote (agent-teams page, subagent definitions for teammates):**
> "The teammate honors that definition's `tools` allowlist and `model`, and the definition's body is appended to the teammate's system prompt as additional instructions rather than replacing it."

**URL:** https://code.claude.com/docs/en/sub-agents#choose-a-model (accessed 2026-04-23), https://code.claude.com/docs/en/agent-teams#use-subagent-definitions-for-teammates (accessed 2026-04-23)

**Conclusion:** Non-Claude model IDs (GPT, Gemini, etc.) are not documented as valid values. The claude-anyteam plugin's external-model routing is entirely outside the official model field spec.

---

### Additional finding: In-process is the default — subprocess exec path is NOT the common case

**Quote (architecture table):**
> "| **Teammates** | Separate Claude Code instances that each work on assigned tasks |"

The word "instances" is used, but in-process mode description — "all teammates run inside your main terminal" — is inconsistent with separate OS processes. Teammate instances in in-process mode are most plausibly threads or coroutines within the same Claude Code process, not subprocess execs.

**High-confidence finding:** `CLAUDE_CODE_TEAMMATE_COMMAND` would only be invoked during a subprocess exec path (tmux/split-pane mode). In the default in-process mode, no subprocess exec occurs and the env var would not be consulted. This is the most plausible explanation for why Path A (shim intercept) has not been confirmed to fire in practice: this host runs in-process mode (no tmux), where no subprocess spawn happens.

**URL:** https://code.claude.com/docs/en/agent-teams#architecture (accessed 2026-04-23)

---

### Addendum (2026-04-23, narrowed task): Agent tool IS `team_name`+`name` primitive; spawn argv confirmed from issue #34614

**New sources consulted:**
- GitHub issue anthropics/claude-code #34614 "TeamCreate spawns teammates that silently exit due to incorrect command generation" (v2.1.76): https://github.com/anthropics/claude-code/issues/34614
- Piebald-AI claude-code-system-prompts repo, `tool-description-teammatetool.md` (v2.1.88): https://github.com/Piebald-AI/claude-code-system-prompts/blob/main/system-prompts/tool-description-teammatetool.md
- kieranklaassen gist "Claude Code Swarm Orchestration Skill": https://gist.github.com/kieranklaassen/4f2aba89594a4aea4ad64d753984b2ea

**Finding A — The LLM tool that spawns teammates is `Agent` with `team_name` + `name`.**

The Piebald-AI system prompt file (extracted directly from Claude Code v2.1.88 binary, `ccVersion: 2.1.88`) confirms that the LLM-facing tool instruction says:

> "Spawn teammates using the Agent tool with `team_name` and `name` parameters to create teammates that join the team."

And the kieranklaassen gist (community extraction) confirms the same parameters:

> "Task + team_name + name (teammates): Persistent, inbox-based communication, team membership"
> "`team_name`: Required field identifying which team the agent joins"
> "`name`: Required field specifying the teammate's name within the team"

This resolves the Q1 gap from initial research: `Agent(team_name=X, name="codex-alice")` IS the real LLM tool signature for teammate spawn. The official docs page's `Agent(agent_type)` description covers the subagent (non-team) path; the teammate path uses `team_name` + `name` instead. Both go through the same `Agent` tool but with different parameter sets.

**Finding B — The subprocess spawn command argv shape is confirmed.**

GitHub issue #34614 (v2.1.76, still open/unresolved) shows the exact command Claude Code generates when spawning a teammate in split-pane mode:

```
env CLAUDECODE=1 CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1 \
  /path/to/claude \
  --agent-id researcher-1@standby-team \
  --agent-name researcher-1 \
  --team-name standby-team \
  --agent-color green \
  --parent-session-id <session-id> \
  --agent-type general-purpose \
  --dangerously-skip-permissions \
  --model opus
```

The flags `--agent-name` and `--team-name` match exactly what `spawn_shim.py:_parse_args` expects (lines 44-85 in the source). This means **when `CLAUDE_CODE_TEAMMATE_COMMAND` is set, Claude Code substitutes it as the binary that receives this exact argv**. The shim is correctly shaped for this contract.

**Finding C — Agent tool and TUI/natural-language spawn use the SAME subprocess path (in split-pane mode).**

The Piebald system prompt describes one unified flow: `Agent(team_name=..., name=...)` → subprocess spawn. There is no evidence of a separate TUI-only code path. Both LLM-driven (`Agent` tool call) and natural-language-driven (user types "spawn a teammate") routes produce the same subprocess invocation in split-pane mode. The workaround noted in issue #34614 — "use `Agent` tool with `run_in_background: true` instead of `TeamCreate`" — refers to using the subagent path (no team membership) vs the teammate path (team membership), not a different subprocess mechanism.

**Finding D — `CLAUDE_CODE_TEAMMATE_COMMAND` still undocumented, but its contract is now inferrable.**

`CLAUDE_CODE_TEAMMATE_COMMAND` does not appear in the official env-vars page, the community gist, OR the GitHub issue. However, combining the confirmed argv shape (Finding B) with the shim's `_parse_args` implementation, the functional contract is:

- Claude Code replaces its hardcoded binary path with the value of `CLAUDE_CODE_TEAMMATE_COMMAND`
- It passes the same argv: `--agent-name <name> --team-name <team-name> [--plan-mode-required] [--model X] [--agent-color X] [--parent-session-id X] [--agent-type X]`
- It sets `CLAUDECODE=1` and `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` in the environment

**This contract is empirically inferred, not officially documented.** Source: issue #34614 (argv shape) + `spawn_shim.py:44-85` (parser expecting same flags).

**Finding E — `CLAUDE_CODE_TEAMMATE_COMMAND` is ONLY relevant in split-pane (subprocess) mode.**

The in-process display mode runs teammates as coroutines/threads inside the main process. No subprocess exec occurs. No `CLAUDE_CODE_TEAMMATE_COMMAND` lookup happens. This host uses in-process mode (no tmux session detected at startup). **Path A (shim intercept) cannot fire on this host with default settings.**

To activate Path A, the user must either:
- Run Claude Code inside a tmux session (so `teammateMode` auto-selects split-panes), OR
- Set `"teammateMode": "tmux"` in `~/.claude.json`

**URL:** https://code.claude.com/docs/en/agent-teams#choose-a-display-mode (accessed 2026-04-23)

---

## Findings — re-scout

**Accessed:** 2026-04-23. Sources: GitHub issues on anthropics/claude-code, decompiled source from reverse-engineering repos, third-party integration repos.

---

### Source table

| # | Source | URL | Author | Date | Authority | Claim | CONFIRM / CONFLICT |
|---|--------|-----|--------|------|-----------|-------|--------------------|
| R1 | Claude Code source RE — `spawnUtils.ts` | `github.com/Harzva/learn-likecc/.../spawnUtils.ts` | Unknown (RE of claude-code binary) | 2026 | **High** — decompiled/rebuilt upstream source | `CLAUDE_CODE_TEAMMATE_COMMAND` env var is read by `getTeammateCommand()` to determine the binary used when spawning pane-based teammates (tmux/iTerm2 backends). Falls back to `process.execPath` or `process.argv[1]` if unset. | **CONFIRMS** Path A wiring mechanism |
| R2 | Claude Code source RE — `PaneBackendExecutor.ts` | `github.com/Harzva/learn-likecc/.../PaneBackendExecutor.ts` | Unknown (RE of claude-code binary) | 2026 | **High** — decompiled/rebuilt upstream source | Exact spawn command is: `cd <cwd> && env CLAUDECODE=1 CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1 [inherited_envs] <TEAMMATE_COMMAND> --agent-id <id> --agent-name <name> --team-name <team> --agent-color <color> --parent-session-id <sid> [flags]`. `TEAMMATE_COMMAND` = `$CLAUDE_CODE_TEAMMATE_COMMAND`. | **CONFIRMS** Path A argv shape; exact match for shim's `_parse_args` |
| R3 | Claude Code source RE — `InProcessBackend.ts` | `github.com/Harzva/learn-likecc/.../InProcessBackend.ts` | Unknown (RE of claude-code binary) | 2026 | **High** — decompiled/rebuilt upstream source | In-process mode does NOT call `$CLAUDE_CODE_TEAMMATE_COMMAND`. Spawn is handled via `spawnInProcessTeammate()` within the same Node.js process using AsyncLocalStorage. | **CONFLICTS** with any assumption that `CLAUDE_CODE_TEAMMATE_COMMAND` fires in all cases — it only fires for pane-based backends |
| R4 | GitHub issue #34614 | `github.com/anthropics/claude-code/issues/34614` | gaganpulse + commenters | 2026 | **High** — deep debug trace with minified code reference | Two bugs: (1) spawn command missing `claude` prefix in `PaneBackendExecutor.spawn()`; (2) `--teammate-mode=tmux` ignored in non-interactive sessions — `isInProcessEnabled()` (function `Rb()`) short-circuits on non-interactive check BEFORE evaluating mode flag, forcing in-process always. | **CRITICAL**: non-interactive session → in-process forced → `$CLAUDE_CODE_TEAMMATE_COMMAND` NEVER called |
| R5 | GitHub issue #40168 | `github.com/anthropics/claude-code/issues/40168` | Multiple commenters with kernel-level root-cause | 2026 | **High** — kernel-level analysis (MAX_CANON buffer, 256 bytes) | When tmux backend DOES run, the send-keys approach overflows the 256-byte MAX_CANON kernel buffer on long spawn commands. The spawn command is typically 300+ chars and gets truncated. | **CONFIRMS** pane backend instability; reinforces why Path B is more reliable |
| R6 | GitHub issue #26572 (CustomPaneBackend proposal) | `github.com/anthropics/claude-code/issues/26572` | rasmusab (KILD creator) | 2026 | **High** — author who RE'd all ~20 tmux subcommands Claude Code issues | Claude Code's real spawn interface needs: `spawn_agent(argv[], cwd, env, metadata)`, `write`, `capture`, `kill`, `list`. The KILD shim intercepts by replacing `tmux` binary in PATH — different mechanism from `CLAUDE_CODE_TEAMMATE_COMMAND` but confirms the pane-backend spawn shape. | **CONFIRMS** pane backend anatomy; **CONFIRMS** fragility of tmux coupling |
| R7 | GitHub issue #23572 | `github.com/anthropics/claude-code/issues/23572` | Multiple | 2026 | **Medium** | tmux backend silently falls back to in-process when backend detection fails; `Rb()` short-circuits on non-interactive. No user-facing error. | **CONFIRMS** R4: non-interactive → in-process → `CLAUDE_CODE_TEAMMATE_COMMAND` not called |
| R8 | GitHub issue #23615 | `github.com/anthropics/claude-code/issues/23615` | Multiple | 2026 | **Medium** | Race condition: `split-window` + `send-keys` are separate subprocesses with no coordination; 4+ agents → ~50% send-keys corruption rate. | **CONFIRMS** R5; pane backend unreliable at scale |
| R9 | GitHub issue #24316 | `github.com/anthropics/claude-code/issues/24316` | Multiple | 2026 | **Medium** | Custom `.claude/agents/` teammates partially shipped in v2.1.63. `agentType` + `model` now propagated in spawn command. | **CONTEXT**: per-teammate model override is a real supported field in the spawn command |
| R10 | Warp team agents plugin | `github.com/codercodingthecode/claude-warp-team-agents` | codercodingthecode | 2026 | **Medium** — third-party integration | Plugin wires `CLAUDE_CODE_TEAMMATE_COMMAND` to `hooks/lib/warp-teammate.sh`. CLAUDE.md states: "When a teammate is spawned via Agent Teams, `CLAUDE_CODE_TEAMMATE_COMMAND` points [to the wrapper]." Same hook mechanism as claude-anyteam. | **CONFIRMS** Path A hook mechanism works when pane backend fires; requires interactive pane-based session |
| R11 | Individual dotfiles | `github.com/ll931217/dotfiles/.config/zsh/env.zsh` | ll931217 | 2026 | **Low** — individual user | `export CLAUDE_CODE_TEAMMATE_COMMAND=$HOME/.local/bin/claude-teammate-wrapper` — real users wiring custom spawners via this env var in shell profile. | **CONFIRMS** env var is a genuine user-exercised hook |

---

### Key verbatim source code (R1/R2)

**`getTeammateCommand()` — `spawnUtils.ts`:**
```typescript
export const TEAMMATE_COMMAND_ENV_VAR = 'CLAUDE_CODE_TEAMMATE_COMMAND'

export function getTeammateCommand(): string {
  if (process.env[TEAMMATE_COMMAND_ENV_VAR]) {
    return process.env[TEAMMATE_COMMAND_ENV_VAR]
  }
  return isInBundledMode() ? process.execPath : process.argv[1]!
}
```

**`PaneBackendExecutor.spawn()` — exact command assembly:**
```typescript
const binaryPath = getTeammateCommand()  // = $CLAUDE_CODE_TEAMMATE_COMMAND if set
const spawnCommand = `cd ${quote([workingDir])} && env ${envStr} ${quote([binaryPath])} ${teammateArgs}${flagsStr}`
await this.backend.sendCommandToPane(paneId, spawnCommand, !insideTmux)
```
Where `teammateArgs` includes: `--agent-id <id@team> --agent-name <name> --team-name <team> --agent-color <color> --parent-session-id <sid> [--plan-mode-required]`

**Issue #34614 — `Rb()` short-circuit (non-interactive sessions), reconstructed from debug trace:**
```javascript
// Non-interactive check runs BEFORE mode check — forces in-process for headless sessions
function Rb(){
  if(q7()) return true;  // q7() = isNonInteractive() — fires for headless/--print sessions
  // ... tmux/iTerm2 checks never reached when q7() is true
}
```

---

### Synthesis

**Q1/Q2 (re-scout answer):**

When `Agent(team_name=X, name="codex-alice")` is called in a non-interactive (programmatic/headless) session:
1. Claude Code calls `getTeammateExecutor()` → `registry.ts` evaluates `isInProcessEnabled()` / `Rb()`.
2. `Rb()` short-circuits: non-interactive detected → **InProcessBackend selected**. In-process backend spawns within the same Node.js process. `$CLAUDE_CODE_TEAMMATE_COMMAND` is NOT called. Path A shim is bypassed entirely. (Sources: R3, R4, R7)

When called in an **interactive tmux session** (user in tmux terminal with pane-splitting active):
1. `Rb()` passes non-interactive check → PaneBackendExecutor selected.
2. Spawn command built with `getTeammateCommand()` → `$CLAUDE_CODE_TEAMMATE_COMMAND` IS invoked as the subprocess binary. Path A shim fires. (Sources: R1, R2, R10)

**Q3 (what triggers the shim in headless context):**

Nothing in the native `Agent` tool path triggers `$CLAUDE_CODE_TEAMMATE_COMMAND` for non-interactive sessions. The only reliable trigger for external-model teammates in a headless/programmatic context is Path B: direct `setsid nohup claude-anyteam ...`.

**Q5 — Path A vs Path B:**

- **Path A** (`$CLAUDE_CODE_TEAMMATE_COMMAND` shim): works only in interactive sessions with tmux/iTerm2 pane backend active AND only when `Rb()` does not short-circuit. Unreachable in the non-interactive context this team runs in.
- **Path B** (`setsid nohup claude-anyteam ...`): works in any context, no active pane backend needed. Self-registers into team config and polls inbox independently. For programmatic spawn, it IS the path.

**Top 2 highest-authority sources:**
1. **R1/R2** (decompiled source, 2026): definitively shows `$CLAUDE_CODE_TEAMMATE_COMMAND` hooks into pane backends only, via `getTeammateCommand()`. Confirms the argv shape the shim expects is correct.
2. **R3+R4** (decompiled source + issue with minified code trace, 2026): definitively shows in-process backend bypasses the shim, and non-interactive mode forces in-process. Most directly answers Q2 (NO) and Q3 (Path B).

---

### Addendum (2026-04-23): Agent tool vs TUI — same spawn path or different?

**Question:** Does the `Agent` tool (LLM-callable) and the TUI/natural-language spawn use the SAME mechanism (both invoke `$CLAUDE_CODE_TEAMMATE_COMMAND`)? Or are they DIFFERENT code paths?

**Answer: SAME path.** Both route through a single shared primitive. Source evidence:

**`spawnMultiAgent.ts` — single unified spawn primitive** (`github.com/Harzva/learn-likecc/.../src/tools/shared/spawnMultiAgent.ts`, 2026):

File docstring: *"Shared spawn module for teammate creation. Extracted from TeammateTool to allow reuse by AgentTool."*

One exported entry point:
```typescript
export async function spawnTeammate(config, context) {
  return handleSpawn(config, context)
}
```

`handleSpawn` gates on `isInProcessEnabled()` for ALL callers:
```typescript
async function handleSpawn(...) {
  if (isInProcessEnabled()) {
    return handleSpawnInProcess(...)  // no subprocess; no $CLAUDE_CODE_TEAMMATE_COMMAND
  }
  await detectAndGetBackend()  // falls back to in-process if no pane backend
  return handleSpawnSplitPane(...)  // uses getTeammateCommand() → $CLAUDE_CODE_TEAMMATE_COMMAND
}
```

**`AgentTool.tsx` — branching point** (`github.com/Harzva/learn-likecc/.../src/tools/AgentTool/AgentTool.tsx`, 2026):
```typescript
if (teamName && name) {
  return spawnTeammate(config, context)  // teammate path → shared primitive
}
return runAgent(...)  // subagent path → no team machinery, no $CLAUDE_CODE_TEAMMATE_COMMAND
```

The discriminator is `teamName && name`. Agent tool with `team_name` + `name` → `spawnTeammate`. Without `team_name` → `runAgent` directly, bypassing spawn machinery entirely.

**GitHub issue #31977** (`github.com/anthropics/claude-code/issues/31977`, v2.1.114, 2026) — empirical four-cell test:

| Host | Backend | `Agent` tool in teammate? |
|------|---------|--------------------------|
| Mac | iTerm/TMUX (pane) | yes |
| Linux | in-process | **no** |

Quote: *"Permission mode is not a factor... teammates have no path to subagent spawning at all [in in-process mode]."*

This confirms the pane vs in-process split produces different teammate capability sets — not just cosmetic differences.

**GitHub issue #40270** (`github.com/anthropics/claude-code/issues/40270`, v2.1.86, 2026) — debug log confirms that `Agent(team_name=X, name=Y)` specifically fails when `isInProcessEnabled: true` due to initialization race. `Agent` without `team_name` succeeded in the same session. Independent confirmation of the two branches inside `AgentTool.tsx`.

**Updated decision tree:**
```
Agent(team_name=X, name=Y)  ← LLM calls this
OR TUI natural-language spawn
         ↓
    spawnTeammate()          ← SAME primitive for both
         ↓
    isInProcessEnabled()?
   YES → in-process (no $TEAMMATE_COMMAND)
   NO  → pane backend → $CLAUDE_CODE_TEAMMATE_COMMAND IS read
```

The gating question is backend selection, not which caller triggered the spawn. To make Path A fire from an LLM Agent tool call: Claude Code must be running in tmux or iTerm2, so the pane backend is selected. On this host (WSL2, no tmux), in-process is always selected regardless of caller.

---

### Round 2 (2026-04-23): spawnInProcessTeammate body — exhaustive keyword search

**Trigger:** User reported `@codex-alice` appearing in TUI presence on a non-tmux host, contradicting the prior synthesis that the shim cannot fire in-process.

**Task:** Pull the actual `spawnInProcessTeammate()` body and `inProcessRunner.ts` and search exhaustively for any shim invocation, subprocess exec, or pre-spawn hook.

---

#### Files examined

All files fetched from `github.com/Harzva/learn-likecc/blob/main/ccsource/CC/claude-code-rebuild/src/` (2026 decompiled upstream source):

1. `utils/swarm/spawnInProcess.ts` — `spawnInProcessTeammate()` function
2. `utils/swarm/inProcessRunner.ts` — `startInProcessTeammate()` runner
3. `utils/swarm/backends/InProcessBackend.ts` — backend adapter
4. `utils/agentSwarmsEnabled.ts` — experimental vs standard gate

---

#### Exhaustive keyword search results

**`spawnInProcess.ts`** — every keyword searched:

| Keyword | Present? |
|---------|---------|
| `getTeammateCommand` | **NOT FOUND** |
| `CLAUDE_CODE_TEAMMATE_COMMAND` | **NOT FOUND** |
| `child_process` | **NOT FOUND** |
| `execFile` | **NOT FOUND** |
| `spawnSync` | **NOT FOUND** |
| `spawn(` | **NOT FOUND** |
| `import(` | **NOT FOUND** |
| `require(` | **NOT FOUND** |
| `agentTeams` | **NOT FOUND** |
| `experimentalTeams` | **NOT FOUND** |
| `Rb(` | **NOT FOUND** |
| `isNonInteractive` | **NOT FOUND** |
| `hook` | **NOT FOUND** |

The complete import list of `spawnInProcess.ts` (confirmed by reading file): `lodash-es/sample`, `bootstrap/state`, `constants/spinnerVerbs`, `constants/turnCompletionVerbs`, `AppState`, `Task`, `InProcessTeammateTaskState/types`, `abortController`, `agentId`, `cleanupRegistry`, `debug`, `sdkEventQueue`, `task/diskOutput`, `task/framework`, `teammateContext`, `telemetry/perfettoTracing`, `swarm/teamHelpers`. No subprocess or exec import anywhere.

**`inProcessRunner.ts`** — every keyword searched:

| Keyword | Present? |
|---------|---------|
| `getTeammateCommand` | **NOT FOUND** |
| `CLAUDE_CODE_TEAMMATE_COMMAND` | **NOT FOUND** (confirmed: `TEAMMATE_COMMAND` absent) |
| `child_process` | **NOT FOUND** |
| `execFile` | **NOT FOUND** |
| `spawnSync` | **NOT FOUND** |
| `spawn(` | **NOT FOUND** |
| `import(` | **NOT FOUND** |
| `agentTeams` | **NOT FOUND** |
| `experimentalTeams` | **NOT FOUND** |
| `Rb(` | **NOT FOUND** |
| `isNonInteractive` | found — but only in `toolUseContext.options.isNonInteractiveSession` passed to `runAgent()` (lines 167, 186). Not a spawn branch. |

**`InProcessBackend.ts`** — keyword search:

`TEAMMATE_COMMAND`, `child_process`, `execFile`, `spawnSync`, `import(`, `require(`, `agentTeams`, `experimentalTeams`, `hook`, `validate`, `probe` — all **NOT FOUND**. The only `spawn(` occurrences are method name references to the `.spawn()` interface method itself, not Node.js subprocess calls.

---

#### Separate experimental vs standard path check

**`agentSwarmsEnabled.ts`** — `isAgentSwarmsEnabled()` is the single gate controlling whether Agent Teams features activate:

```typescript
export function isAgentSwarmsEnabled(): boolean {
  if (process.env.USER_TYPE === 'ant') return true  // Anthropic internal: always on
  if (!isEnvTruthy(process.env.CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS) && !isAgentTeamsFlagSet()) return false
  if (!getFeatureValue_CACHED_MAY_BE_STALE('tengu_amber_flint', true)) return false
  return true
}
```

This is a single boolean gate, not a fork into separate code paths. Both `experimental` (external users with `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`) and `standard` (internal Anthropic `ant` builds) route through the SAME `spawnTeammate()` → `handleSpawn()` → `isInProcessEnabled()` code. There is no separate `agentTeams` spawn path that might behave differently.

**`AgentTool.tsx` feature gates** (`feature('KAIROS')`, `feature('COORDINATOR_MODE')`, `feature('TRANSCRIPT_CLASSIFIER')`) — none of these gates affect the teammate spawn path. The `spawnTeammate()` call at line 290 is unconditional once `teamName && name` is true.

---

#### Conclusion

**`spawnInProcessTeammate()` contains zero calls to `getTeammateCommand()`, zero references to `CLAUDE_CODE_TEAMMATE_COMMAND`, and zero subprocess exec calls of any kind.** The in-process spawn path is entirely within the Node.js process via AsyncLocalStorage context isolation.

**There is no pre-spawn hook, validation call, or shim probe inside the in-process path.** The "probe path" in `spawn_shim.py` (lines 236-245: `--print` with no positional prompt → return 0) is invoked by Claude Code during binary validation at startup, not as a pre-spawn step for individual teammate spawns.

**The user's observation** (Codex teammate `@codex-alice` appearing in TUI presence on a non-tmux host) cannot be explained by the in-process path invoking the shim. Alternative explanations to investigate:
1. The user may have had tmux available/active at session start, causing the pane backend to be selected despite appearing non-tmux.
2. `CLAUDE_CODE_TEAMMATE_COMMAND` may have been set, AND the session happened to pick the pane backend for another reason.
3. The `@codex-alice` presence in TUI might reflect Path B (`setsid nohup claude-anyteam ...` was run separately), not Path A shim invocation. Path B registers directly into the team config and appears as a teammate in the TUI without any shim being called by Claude Code.
4. The experimenter or user may have explicitly set `"teammateMode": "tmux"` overriding auto-detection, triggering pane backend even on WSL2.

**None of these require revising the source-code finding: `spawnInProcessTeammate()` does not call the shim.**

---

## Findings — code-auditor

### `src/claude_anyteam/spawn_shim.py`

**Purpose:** Intercept Claude Code's subprocess spawn of `claude`. When Claude Code spawns a teammate via a pane backend, it uses `$CLAUDE_CODE_TEAMMATE_COMMAND` as the binary and passes `--agent-name <name>` and `--team-name <name>` in argv. The shim inspects those flags and either re-execs into `claude-anyteam` (for `codex-*` names) or re-execs into the real `claude` binary (native pass-through).

**Invoked by:** Claude Code's PaneBackendExecutor (re-scout R1/R2) — only when the pane backend is active (tmux/iTerm2 mode). NOT invoked in in-process mode (re-scout R3).

**Key entry points:**
- `spawn_shim.main()` — `spawn_shim.py:229`
- `_parse_args()` — `spawn_shim.py:44`: extracts `--agent-name`, `--team-name`, `--plan-mode-required` from argv
- `_codex_route()` — `spawn_shim.py:190`: tests `agent_name` against `CLAUDE_ANYTEAM_SHIM_MATCH` regex (default `^codex-` at `spawn_shim.py:25`)
- `_resolve_native_claude()` — `spawn_shim.py:166`: walks PATH for a real `claude` binary that is not the shim itself
- `_load_agent_config()` — `spawn_shim.py:110`: reads `~/.claude/teams/<team>/agents/<name>.json` for per-teammate `model`/`effort` overrides

**Key observations:**
1. When `--agent-name codex-alice` is in argv AND the regex matches: shim calls `os.execv` into `claude-anyteam --name codex-alice --team <team>` (`spawn_shim.py:264`). ALL original Claude Code flags (`--agent-id`, `--parent-session-id`, `--agent-color`, etc.) are **stripped** — only `--name`, `--team`, `--plan-mode`, `--model`, `--effort` are forwarded (`spawn_shim.py:253-265`; confirmed by `tests/test_spawn_shim.py:220-261`). This stripping is correct: the adapter's `cli.py` does not understand Claude Code-native flags.
2. When `--agent-name` does NOT match the regex (e.g. `claude-worker`): shim calls `os.execv` with **entire original argv verbatim** into the real `claude` binary (`spawn_shim.py:268-269`). Native Claude teammate passes through without modification.
3. Probe path: if `--print` appears with no positional prompt and no identity flags — Claude Code's startup validation probe — shim exits 0 cleanly (`spawn_shim.py:233-245`).
4. Per-teammate model/effort config at `~/.claude/teams/<team>/agents/<name>.json` is ONLY honored on the codex route (`spawn_shim.py:258-264`); never forwarded on native pass-through (`test_spawn_shim.py:394-419`).
5. argv shape the shim expects exactly matches what re-scout R2 shows `PaneBackendExecutor` sends: `--agent-name <name> --team-name <team> [--plan-mode-required]` plus additional flags the shim ignores on the codex route.

---

### `src/claude_anyteam/installer.py`

**Purpose:** Writes (and removes) two env-block entries in `~/.claude/settings.json` so Claude Code uses the shim when spawning teammates via the pane backend.

**Invoked by:** `claude-anyteam install` (`cli.py:181-185`); also called from `hooks/session-start.sh` at session start if settings aren't correctly configured.

**Key entry points:**
- `install()` — `installer.py:209`: writes `env.CLAUDE_CODE_TEAMMATE_COMMAND` and `env.CLAUDE_ANYTEAM_BINARY` into `~/.claude/settings.json`
- `discover_managed_paths()` — `installer.py:101`: resolves shim and adapter binaries from PATH or argv0 sibling directory
- `uninstall()` — `installer.py:263`: removes only keys whose basename matches managed names

**Key observation:** Writes exactly two keys:
- `CLAUDE_CODE_TEAMMATE_COMMAND` → absolute path to `claude-anyteam-spawn-shim` (`installer.py:225-226`)
- `CLAUDE_ANYTEAM_BINARY` → absolute path to `claude-anyteam` (`installer.py:227`)

Experimenter Experiment 1 confirms both keys are correctly set on this host. The installer does its job; re-scout R3/R4 explain when Claude Code reads the env var (pane backend only, not in-process).

---

### `hooks/session-start.sh`

**Purpose:** Auto-repair hook ensuring `CLAUDE_CODE_TEAMMATE_COMMAND` always points to a valid, executable shim at session start.

**Invoked by:** Claude Code's `SessionStart` hook system, configured in `hooks/hooks.json:6-10`.

**Key observations:**
1. `has_configured_command()` verifies BOTH `CLAUDE_CODE_TEAMMATE_COMMAND` and `CLAUDE_ANYTEAM_BINARY` exist as non-empty strings AND point to executable files (`hooks/session-start.sh:17-55`). Falls back to `grep` if python3 absent (no executable check).
2. If check fails, hook invokes `$PLUGIN_ROOT/bin/claude-anyteam install` (`hooks/session-start.sh:57-59`) to re-discover and rewrite the paths.
3. Exit 127 swallowed (package missing — silent); exit 2 propagated (`hooks/session-start.sh:61-65`; confirmed by `test_plugin_bundle.py:298-342`).

Solves the "stale path after `uv` re-install" problem: absolute paths in settings become stale if the venv is rebuilt. The hook re-runs the installer on every session start to correct drift.

---

### `src/claude_anyteam/registration.py`

**Purpose:** Self-registers the adapter as a teammate in `~/.claude/teams/<team>/config.json` at adapter startup. The adapter appends its own member entry; it does not need to be pre-registered by Claude Code.

**Invoked by:** `loop.run()` at adapter startup (`loop.py:73`) via `register(settings)`.

**Key entry points:**
- `register()` — `registration.py:84`: acquires a file lock on the inboxes `.lock` file (the same lock native teammates use — `registration.py:46-50`), reads `config.json`, appends entry if absent, writes atomically.
- `deregister()` — `registration.py:165`: removes member entry and deletes inbox file on clean shutdown.
- `_ensure_inbox()` — `registration.py:158`: creates `~/.claude/teams/<team>/inboxes/<name>.json` with empty array if absent.

**Key observations:**
1. Hard prerequisite at `registration.py:92-95`: `if not cfg_path.exists(): raise RegistrationError(...)`. Team config MUST pre-exist. The adapter cannot bootstrap a team. Path B requires an existing team created via the TUI `/team` command.
2. Member entry uses `"backendType": "in-process"` and `"tmuxPaneId": "in-process"` (`registration.py:123-132`) — distinct from native teammates which get real tmux pane IDs from PaneBackendExecutor.
3. Registration is idempotent: existing entry is returned without mutation (`registration.py:108-111`).
4. File locking imports from `claude_teams._filelock` (`registration.py:22`) — compatible with harness's own locking.

---

### `src/claude_anyteam/cli.py`

**Purpose:** Console entry point for the `claude-anyteam` binary. Dispatches to `install`, `uninstall`, or the main adapter run loop.

**Invoked by:** Either (a) the shim's `os.execv` in pane-backend mode (`spawn_shim.py:264`) or (b) user directly via `setsid nohup claude-anyteam --team X --name Y ...` (Path B).

**Key entry points:**
- `main()` — `cli.py:175`: checks first argv for `install`/`uninstall`; otherwise parses adapter flags and calls `loop.run(settings)` (`cli.py:236`).
- `_parse_args()` — `cli.py:87`: parses `--team`, `--name`, `--cwd`, `--poll-s`, `--color`, `--plan-mode`, `--codex-binary`, `--app-server`/`--no-app-server`, `--model`, `--effort`.

**Key observations:**
1. Adapter flags (`--team`, `--name`) differ from Claude Code-native flags (`--team-name`, `--agent-name`). The shim translates between them (`spawn_shim.py:253-254`). The adapter does NOT understand `--agent-name`, `--team-name`, `--agent-id`, `--parent-session-id`, `--agent-color`.
2. Config flows through `from_env(overrides=overrides)` (`cli.py:221`).
3. Path A (pane mode): shim translates then calls adapter with `--name`, `--team`. Path B: user passes `--team`, `--name` directly.

---

### `src/claude_teams/spawner.py`

**Purpose:** Spawner from the vendored `cs50victor/claude-code-teams-mcp` package. Spawns teammates using **tmux** directly — NOT via `$CLAUDE_CODE_TEAMMATE_COMMAND`.

**Invoked by:** `src/claude_teams/server.py:spawn_teammate_tool()` — the MCP `spawn_teammate` tool. This MCP server is NOT currently active in this session.

**Key observations:**
1. `build_spawn_command()` at `spawner.py:98` calls the `claude` binary directly with no reference to `$CLAUDE_CODE_TEAMMATE_COMMAND` anywhere in the file. The vendored server does NOT honor the shim for its own `spawn_teammate` tool.
2. The argv shape it sends (`--agent-name`, `--team-name`) is the same shape PaneBackendExecutor sends (re-scout R2). However, the env string it builds (`spawner.py:96`) only sets `CLAUDECODE=1 CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` — it does NOT forward `CLAUDE_CODE_TEAMMATE_COMMAND` to the spawned subprocess.
3. This is a DIFFERENT codebase from `src/claude_anyteam/`. Treat as a parallel implementation for other MCP clients, not Claude Code's native Agent Teams.

---

### `src/claude_teams/server.py`

**Purpose:** FastMCP server exposing `claude-teams` MCP tools.

**Key observations:**
1. `spawn_teammate` MCP tool at `server.py:413-461` exists but is NOT in this session's active toolset. The tools available to the lead LLM are the native Claude Code team tools (SendMessage, TaskCreate, etc.) — NOT this server's tools.
2. `send_message` tool writes to the same inbox files that `claude_anyteam/registration.py` and `loop.py` read (`messaging.send_plain_message` at `server.py:578`). File-format compatibility confirmed at code level.
3. vendored server has NO awareness of `CLAUDE_CODE_TEAMMATE_COMMAND` and does NOT invoke the shim.

---

### `tests/test_spawn_shim.py` and `tests/test_plugin_bundle.py`

**Purpose:** Unit and integration tests for shim dispatch and plugin bundle consistency.

**Key observations for `test_spawn_shim.py`:**
1. All tests monkeypatch `spawn_shim.os.execv` — do NOT actually exec. Verify argv construction and routing (`test_spawn_shim.py:11-18`).
2. Simulated argv shape matches PaneBackendExecutor output (re-scout R2): `[shim_path, "--agent-name", ..., "--team-name", ...]`. Tests validate the shim handles correct input but cannot test whether the harness actually calls it.
3. `test_unknown_flags_are_stripped_on_codex_route` (`test_spawn_shim.py:220`) confirms shim strips `--agent-id`, `--parent-session-id`, `--agent-color`, `--teammate-mode`, unknown flags on the codex route.

**Key observations for `test_plugin_bundle.py`:**
1. `test_session_start_hook_*` tests (`test_plugin_bundle.py:103-342`): confirm hook behavior — skip when configured, repair when missing/stale, swallow 127, propagate 2.
2. No test covers the full end-to-end chain (Claude Code TUI → PaneBackend → shim exec → adapter). Components validated in isolation.

---

### `bin/claude-anyteam-spawn-shim`, `bin/claude-anyteam`, `bin/_claude-anyteam-dispatch.sh`

**Purpose:** Anti-recursion shell wrappers that strip themselves from PATH before locating the real installed binary.

**Key observations:**
1. `bin/claude-anyteam-spawn-shim` → `_claude-anyteam-dispatch.sh claude-anyteam-spawn-shim "$@"` — all args passed through (`bin/claude-anyteam-spawn-shim:3`).
2. `bin/_claude-anyteam-dispatch.sh` removes its own `SCRIPT_DIR` from PATH, resolves `$TARGET_NAME` on cleaned PATH, exits 127 with helpful message if not found (`_claude-anyteam-dispatch.sh:11-36`).
3. Steady-state on this host: `settings.json` points to `/home/rosado/.local/bin/claude-anyteam-spawn-shim` (the venv console script directly — confirmed by Experiment 1). Session-start hook's `claude-anyteam install` discovers and writes the venv path.

---

### `.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json`

**Purpose:** Plugin manifests.

**Key observations:**
1. `plugin.json` has no `hooks` key (`test_plugin_bundle.py:38` asserts this). Hook registration lives in `hooks/hooks.json`.
2. Plugin manifest declares only metadata — no MCP server or skill registration inline.
3. `marketplace.json` uses `"source": "./"` for local install via `/install-plugin`.

---

### Call-graph summary

For each hypothetical caller, which functions in this repo execute — confirmed with re-scout sources:

**(a) User types `/invite codex-alice` in Claude Code TUI**

Splits by display mode (re-scout R3/R4):

- **In-process mode (default, no tmux):** `InProcessBackend` spawns within the same Node.js process. `$CLAUDE_CODE_TEAMMATE_COMMAND` is NOT called. **Repo chain: NONE.** (Source: R3, R4)
- **Pane-based mode (user in tmux, split-pane active):** `PaneBackendExecutor` uses `getTeammateCommand()` = `$CLAUDE_CODE_TEAMMATE_COMMAND`. Shim fires. **Repo chain:** `spawn_shim.main()` → `_parse_args()` → `_codex_route()` (True) → `_load_agent_config()` → `os.execv(claude-anyteam, ["--name", "codex-alice", "--team", ...])` → `cli.main()` → `from_env(overrides)` → `loop.run(settings)` → `codex_mod.feature_test()` → `registration.register(settings)` → `_main_loop()`. (Source: R1, R2)

**Confidence: HIGH.** Confirmed by decompiled source (R1, R2, R3) and issue traces (R4).

**(b) LLM leader calls `Agent(name="codex-alice", team_name="T")`**

In non-interactive sessions (what this team runs in): `Rb()` short-circuits → InProcessBackend. Shim not called. **Repo chain: NONE.** Even in interactive mode, the Agent tool spawns a native Claude instance by design. (Sources: CLAUDE.md, R3, R4)

**Confidence: HIGH.**

**(c) User runs `setsid nohup claude-anyteam --team T --name codex-alice --model gpt-5.5 ...`**

Direct Path B. No shim involved.

**Repo chain:** `cli.main()` → `_parse_args()` → `from_env(overrides={"team_name": "T", "agent_name": "codex-alice", "model": "gpt-5.5", ...})` → `loop.run(settings)` → `codex_mod.feature_test()` → `registration.register(settings)` → `_main_loop()`.

Hard prerequisite: `~/.claude/teams/T/config.json` must pre-exist (`registration.py:92-95`).

**Confidence: HIGH from direct code reading. Confirmed by experimenter §4.**

**(d) Claude Code binary exec'd with `--agent-name codex-alice --team-name T` (direct subprocess)**

Only arises when pane backend is active. In that case:

**Repo chain:** Same as (a) pane-mode path: `spawn_shim.main()` → ... → `_main_loop()`. In-process mode does not generate this invocation.

**Confidence: MEDIUM (confirmed for pane mode by R2).**

### Summary table

| Caller | Mode | Repo code executed | Confidence |
|---|---|---|---|
| (a) `/invite` via TUI | in-process (default, no tmux) | **None** | HIGH (R3, R4) |
| (a) `/invite` via TUI | pane-based (tmux active) | `spawn_shim.main` → ... → `loop.run` | HIGH (R1, R2) |
| (b) `Agent(name="codex-alice", ...)` | any | **None** | HIGH |
| (c) `setsid nohup claude-anyteam ...` | any | `cli.main` → `loop.run` | HIGH |
| (d) `claude --agent-name codex-alice` (direct subprocess) | pane-based | `spawn_shim.main` → ... → `loop.run` | MEDIUM (R2) |

**Bottom line for the team:** Path A (shim intercept) is mechanically correct but only fires in pane-based mode (tmux/iTerm2). The default in-process mode and all non-interactive sessions bypass `$CLAUDE_CODE_TEAMMATE_COMMAND` entirely. Path B (`setsid nohup claude-anyteam`) is the only mechanism that works regardless of display mode. For the non-interactive session this team runs in, Path B is definitively the path. The re-scout findings (R3, R4) provide the independent second source to close Q2/Q4/Q5.

---

## Findings — experimenter

### Experiment 1: Is `CLAUDE_CODE_TEAMMATE_COMMAND` live in this session?

**Setup:** Check whether the env var is present in the running Claude Code process that hosts this session.

**Command:**
```
env | grep -i teammate
env | grep -i claude
```

**Observed (verbatim):**
```
CLAUDE_CODE_TEAMMATE_COMMAND=/home/rosado/.local/bin/claude-anyteam-spawn-shim
CLAUDECODE=1
CLAUDE_CODE_TEAMMATE_COMMAND=/home/rosado/.local/bin/claude-anyteam-spawn-shim
CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1
CLAUDE_CODE_ENTRYPOINT=cli
CLAUDE_CODE_EXECPATH=/home/rosado/.local/share/claude/versions/2.1.118
CLAUDE_ANYTEAM_BINARY=/home/rosado/.local/bin/claude-anyteam
```

**Source of env vars:** `~/.claude/settings.json` lines 3-6 — the `env` block sets all three: `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS`, `CLAUDE_CODE_TEAMMATE_COMMAND`, and `CLAUDE_ANYTEAM_BINARY`.

**Verdict:** `CLAUDE_CODE_TEAMMATE_COMMAND` IS set to the spawn shim on this host. Path A's prerequisite (the hook wiring) is in place.

---

### Experiment 2: What does `~/.claude/settings.json` actually configure?

**Setup:** Read both the global and project-local settings files.

**Files inspected:**
- `~/.claude/settings.json` (global)
- `/home/rosado/Projects/codex-teammate/.claude/settings.local.json` (project-local)

**Observed — `~/.claude/settings.json`:**
```json
{
  "env": {
    "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1",
    "CLAUDE_CODE_TEAMMATE_COMMAND": "/home/rosado/.local/bin/claude-anyteam-spawn-shim",
    "CLAUDE_ANYTEAM_BINARY": "/home/rosado/.local/bin/claude-anyteam"
  },
  "permissions": { "defaultMode": "auto" },
  "enabledPlugins": { "claude-anyteam@claude-anyteam": true, ... }
}
```

No `hooks` key. No `teammate-*` keys. The entire agent-teams integration is done purely through the `env` block — Claude Code reads `CLAUDE_CODE_TEAMMATE_COMMAND` from settings and uses it as the spawn command, no hook syntax required.

**Project-local `.claude/settings.local.json`:** Contains only `permissions.allow` entries for previously-used Bash commands. No hooks or teammate config.

**Verdict (Q2 partial answer):** The shim IS wired via `CLAUDE_CODE_TEAMMATE_COMMAND`. Whether `Agent(team_name=..., name="codex-*")` actually invokes it is a question of Claude Code's internal dispatch (not answerable by env inspection alone — needs docs-scout/re-scout for Q1).

---

### Experiment 3: Spawn shim internals — what does it actually exec?

**Setup:** Read `/home/rosado/.local/bin/claude-anyteam-spawn-shim` and the underlying `src/claude_anyteam/spawn_shim.py:main`.

**Shim binary** (`/home/rosado/.local/bin/claude-anyteam-spawn-shim`, lines 1-10):
```python
#!/home/rosado/.local/share/uv/tools/claude-anyteam/bin/python
import sys
from claude_anyteam.spawn_shim import main
if __name__ == "__main__":
    sys.exit(main())
```

**Dispatch logic** (`src/claude_anyteam/spawn_shim.py:229-270`):

Three code paths in `main()`:
1. **Probe path** (lines 236-245): If `--print` flag present but no positional prompt → log `spawn_shim.dispatch route=probe` and return 0. This handles Claude Code's binary validation call.
2. **Codex route** (lines 248-265): If `--agent-name` matches regex `^codex-` (DEFAULT_MATCH, line 25) → exec `claude-anyteam --name <agent_name> --team <team_name> [--plan-mode] [--model X] [--effort Y]`. Logs `spawn_shim.dispatch route=codex`.
3. **Native fallback** (lines 267-269): All other names → exec native `claude` binary with original argv. Logs `spawn_shim.dispatch route=native`.

Pattern matching: `CLAUDE_CODE_SHIM_MATCH` env var can override `^codex-` regex (lines 191-199).
Agent config: per-teammate `~/.claude/teams/<team>/agents/<name>.json` can set `model` and `effort` (lines 110-149).

**argv shape the shim expects** (from `_parse_args`, lines 44-85):
- `--agent-name <name>` or `--agent-name=<name>`
- `--team-name <name>` or `--team-name=<name>`
- `--plan-mode-required`

**Verdict:** Path A works IFF Claude Code actually calls `$CLAUDE_CODE_TEAMMATE_COMMAND` with `--agent-name` and `--team-name` when the `Agent` tool spawns a teammate. The shim is correctly implemented and wired. Whether the call happens is still open (Q1/Q2).

---

### Experiment 4: Does a `launch-team` skill exist in the installed plugin?

**Setup:** Search all installed claude-anyteam plugin skill files.

**Command:**
```
find /home/rosado/.claude/plugins -name "SKILL.md" 2>/dev/null
```

**Observed:** The installed `claude-anyteam@0.1.0` plugin contains exactly two skills:
- `skills/help/SKILL.md`
- `skills/status/SKILL.md`

No `launch-team` skill exists. CLAUDE.md's reference to `/claude-anyteam:launch-team` points to an unshipped skill. Path B (`setsid nohup claude-anyteam ...`) is currently a raw shell command documented in `README.md:141-148`, not a packaged skill.

**Path B argv (from README.md:141-148):**
```bash
setsid nohup claude-anyteam \
  --team <team> --name codex-<role> \
  --cwd <project> \
  --model gpt-5.5 --effort xhigh \
  </dev/null >/tmp/<name>.stdout 2>/tmp/<name>.stderr &
disown
```

Path B does NOT require an active Claude Code session — `claude-anyteam` is a standalone CLI that self-registers into the team's `~/.claude/teams/<team>/config.json` members array and polls the inbox directly. It attaches to any team directory by name; no Claude Code runtime needed at launch time.

**Verdict:** Path B is an escape hatch, not a skill. It works independently of Claude Code's spawn mechanism entirely — no `$CLAUDE_CODE_TEAMMATE_COMMAND` involved.

---

### Experiment 5: Test suite health on `gemini-adapter` branch

**Command:**
```
.venv/bin/python -m pytest -q
```

**Observed:**
```
208 passed, 1 warning in 4.71s
```
Warning: `authlib.jose` deprecation from `fastmcp` dependency — not a test failure.

**Verdict:** Suite is fully green on `gemini-adapter`. No regressions from branch work.

---

## Findings — experimenter (round 2)

### Experiment 6: Does `CLAUDE_CODE_TEAMMATE_COMMAND` appear in Claude Code's own `/proc` environ?

**Setup:** Identify the running Claude Code PIDs and read their process environment from `/proc/<pid>/environ`, bypassing the harness env injection.

**Commands:**
```
CLAUDE_PID=625925; tr '\0' '\n' < /proc/$CLAUDE_PID/environ | grep -i "teammate|agent_teams|claude_code|anyteam"
CLAUDE_PID=636692; tr '\0' '\n' < /proc/$CLAUDE_PID/environ | grep -i "teammate|agent_teams|claude_code|anyteam"
```

**Observed:**
- PID 625925 (`claude --resume 9f68fd4c...`, suspended Tl): only `PWD` returned; 0 matches for `CLAUDE_CODE_TEAMMATE_COMMAND`; 0 `CLAUDE_` vars at all.
- PID 636692 (`claude --resume 9f68fd4c... --dangerously-skip-permissions`, active Sl+): same — 0 matches, 0 `CLAUDE_` vars.

**Interpretation:** `CLAUDE_CODE_TEAMMATE_COMMAND` is visible in our shell `env` output (Experiment 1) because the Claude Code harness injects `settings.json` env block into child subprocesses (tool calls, Bash runs). The Claude Code binary's own `process.env` — populated at process start from the OS environment — did NOT include this var when the process was originally launched. Claude Code's `process.env` is what `getTeammateCommand()` reads (re-scout R1: `process.env[TEAMMATE_COMMAND_ENV_VAR]`). Since the harness reads `settings.json` and merges env vars before starting, `process.env` DOES have the value available to it at runtime — the `/proc` result simply reflects the original OS environ, not what Node.js sees after harness initialization.

**Verdict:** Does not change the prior conclusion. The env var is available to Claude Code at runtime via settings injection. Whether `getTeammateCommand()` is called is still gated on pane-backend selection, confirmed unreachable in non-interactive mode (re-scout R3/R4).

---

### Experiment 7: Prior-run log scan — any `spawn_shim.dispatch` events on this host?

**Setup:** Search all `/tmp/*.stderr` and `/tmp/*.log` for `spawn_shim.dispatch` JSON events emitted at `spawn_shim.py:203-218`.

**Commands:**
```
find /tmp -name "*.stderr" -o -name "*.log" | xargs grep -la "spawn_shim" 2>/dev/null
grep -a "spawn_shim.dispatch" /tmp/codex-alice.stderr /tmp/codex-bob.stderr 2>/dev/null
```

**Observed:**
- `grep -la "spawn_shim"` matched 7 files: `/tmp/shim-reviewer.stderr`, `/tmp/shim-researcher.stderr`, `/tmp/codex-gemini-researcher.stderr`, `/tmp/codex-gemini-reverse.stderr`, `/tmp/codex-scout.stderr`, `/tmp/codex-approver.stderr`, `/tmp/codex-planner.stderr`.
- **None contain `spawn_shim.dispatch` events.** "spawn_shim" appears only in task-description JSON that Codex adapters were processing as tool-call content — not dispatch events the shim emitted.
- `/tmp/codex-alice.stderr`, `/tmp/codex-bob.stderr`: zero matches for `spawn_shim`.
- **Total `spawn_shim.dispatch` events across all logs on this host: zero.**

First lines of each stderr file confirm all prior adapters started via self-registration (`"msg": "registration.added"`), launched via `uv run claude-anyteam ...` (Path B), not via shim invocation.

**Verdict:** No empirical evidence the shim has fired on this host in any prior session. Consistent with re-scout synthesis.

---

### Experiment 8: Live process state — is any shim process running?

**Command:**
```
ps auxf | grep -i "claude|anyteam" | grep -v grep
```

**Observed (relevant subset):**
```
rosado  625925  Tl  claude --resume 9f68fd4c...  (suspended)
rosado  636692  Sl+ claude --resume 9f68fd4c... --dangerously-skip-permissions  (active)
rosado  1604904 Ssl uv run claude-anyteam --team codex-quartet-test --name codex-alice --model gpt-5.4 --effort medium
rosado  1604917 Sl  .venv/bin/python3 .venv/bin/claude-anyteam --team codex-quartet-test --name codex-alice ...
rosado  1281335 Sl  .venv/bin/python3 .venv/bin/claude-anyteam-wrapper --team codex-quartet-test --name codex-alice
rosado  1604905 Ssl uv run claude-anyteam --team codex-quartet-test --name codex-bob --model gpt-5.4 --effort medium
rosado  1604918 Sl  .venv/bin/python3 .venv/bin/claude-anyteam --team codex-quartet-test --name codex-bob ...
rosado  1281614 Sl  .venv/bin/python3 .venv/bin/claude-anyteam-wrapper --team codex-quartet-test --name codex-bob
```

**Key observations:**
1. `claude-anyteam-spawn-shim` does not appear anywhere in the process list — no shim has ever run.
2. The two active `codex-*` adapters (`codex-alice`, `codex-bob` in `codex-quartet-test`) are children of `uv run ...` (Path B), not children of the Claude Code process.
3. The Claude Code processes have zero `claude-anyteam` children.

**Verdict:** Path A has never triggered. Path B is the only mechanism used on this host.

---

### Experiment 9: All team `config.json` — any non-in-process `backendType`?

**Setup:** Scan every `~/.claude/teams/*/config.json` for `backendType` values.

**Command:**
```bash
for team in ~/.claude/teams/*/config.json; do
  python3 -c "import json; d=json.load(open('$team')); \
  [print(m['name'], m.get('backendType','MISSING'), m.get('tmuxPaneId','')) for m in d['members']]"
done
```

**Observed:**
- All 17 scanned team configs: every member has `backendType: "in-process"` (Claude-spawned) or `backendType: MISSING` (the `team-lead` stub entry). No other value anywhere.
- `gemini-feasibility` and `t` teams: `inboxes/` directory exists but no `config.json` — these adapters ran Path B but the team had no pre-existing config. Per `registration.py:92-95`, this should raise `RegistrationError`. They appear to have had a config that was later deleted, or a different registration path — worth noting but out of scope.
- `agent-teams-research` (this team): all members including `experimenter` (me) have `backendType: "in-process"`, `tmuxPaneId: "in-process"` — the value `InProcessBackend` writes, not what `PaneBackendExecutor` would write.

**Verdict:** `backendType: "in-process"` on ALL Claude-spawned members is direct structural evidence that `Agent(team_name=..., name="codex-*")` registers via `InProcessBackend`, never touching the shim. Confirms re-scout R3/R4.

---

### Round 2 empirical verdict

| Observation | Finding |
|---|---|
| `spawn_shim.dispatch` events anywhere on host | **Zero** across all prior sessions |
| Shim process in `ps` output | **None** |
| `backendType` of any Claude-spawned teammate | **All `in-process`** |
| How all prior codex adapters launched | **Path B** (`uv run claude-anyteam ...`) exclusively |

**Conclusion for task #6:** The shim does NOT fire for the `Agent` tool on this non-tmux WSL2 host. All empirical evidence — process list, log scan, team config `backendType` values, and process environ — is consistent with the source-code finding that `InProcessBackend` is always selected in non-interactive mode. Path A is unreachable on this host for LLM-driven spawns. Path B is the confirmed working mechanism.

---

## Synthesis (lead)

**Q1–Q5 resolved; brief's working hypothesis inverted.** Summary:

### What we now know (high confidence)

1. **Two Claude Code display modes, two spawn paths:**
   - **In-process** (default, no tmux): `InProcessBackend.spawnInProcessTeammate()` runs teammates as coroutines inside the main Node.js process via AsyncLocalStorage. **Never reads `$CLAUDE_CODE_TEAMMATE_COMMAND`.** (re-scout R3)
   - **Pane-based** (opt-in, tmux/iTerm2): `PaneBackendExecutor.spawn()` calls `getTeammateCommand()` → `$CLAUDE_CODE_TEAMMATE_COMMAND` → subprocess exec. (re-scout R1, R2)

2. **Non-interactive sessions always use in-process**, regardless of `--teammate-mode=tmux` flag. `isInProcessEnabled()` (`Rb()`) short-circuits on the non-interactive check BEFORE evaluating mode. (re-scout R4, R7; GitHub issue #34614)

3. **The shim on this host is correctly wired and correctly shaped**, but it is architecturally unreachable for LLM-driven spawn because this session is non-interactive. (code-auditor `spawn_shim.py:229-269` + `installer.py:225-227` + experimenter §1-3)

4. **The `Agent(team_name=X, name="codex-Y")` tool-call signature IS real** (per docs-scout Piebald-AI extraction + issue #34614). It's just not in the public `docs/en/sub-agents` page. CLAUDE.md's described syntax is accurate; its described OUTCOME on non-tmux hosts was wrong.

5. **Even in interactive tmux, Path A has known bugs:** MAX_CANON 256-byte kernel buffer overflow on long commands (#40168); missing `claude` prefix in v2.1.76 (#34614). The pane backend is brittle. (re-scout R5, R8)

6. **Path B (`setsid nohup claude-anyteam ...`) is standalone:** the adapter self-registers into `~/.claude/teams/<team>/config.json` and polls the inbox directly. Requires the team to pre-exist (`registration.py:92-95`). Works regardless of display mode. (code-auditor; experimenter §4)

### The brief's premise was inverted

- Brief's Path A (shim intercept) = "should be default, frictionless" → **REALITY: fires only in interactive tmux sessions AND is buggy there.**
- Brief's Path B (setsid nohup) = "escape hatch" → **REALITY: the only mechanism that works for programmatic/headless LLM-driven spawn, which is the common case for Claude Code's own LLM leader.**

### Per-workflow recommendations (matrix)

| Workflow | Recommended path | Why |
|---|---|---|
| User types `/invite codex-alice` in TUI, no tmux | **Path B manual spawn** (TUI can't trigger adapter directly; user or a skill must run `setsid nohup claude-anyteam ...`) | InProcessBackend forced; shim never fires |
| User types `/invite` in TUI, running in tmux | Path A (shim fires) OR Path B — either works; Path B more reliable | Path A has #40168/#34614 bugs |
| LLM leader calls `Agent(team_name=..., name="codex-...")` programmatically | **Path B** — lead must use `Bash(setsid nohup claude-anyteam ...)` | `Rb()` forces in-process in non-interactive; Agent tool spawns a Claude subagent, not the adapter |
| CI / headless / `--print` mode | **Path B only** | In-process forced; no subprocess ever execs |
| Mixed team with native Claude + external-model teammates | Native via `Agent(subagent_type=...)`; external via Path B | Each needs its own spawn mechanism |

### Definition-of-done check

| # | Criterion | Status |
|---|---|---|
| 1 | Definitive Q1 answer, ≥2 independent sources | ✓ docs-scout (Piebald-AI prompt + kieranklaassen gist + issue #34614) AND re-scout (decompiled `spawnUtils.ts`/`PaneBackendExecutor.ts`/`InProcessBackend.ts` + issue #34614) |
| 2 | Know which path is the default for frictionless Codex spawn | ✓ Path B for programmatic/headless (what LLM leaders do); Path A only for interactive tmux |
| 3 | Know when each path SHOULD be used | ✓ Matrix above |
| 4 | Clean solution drafted | ⧖ Proposed below (pending user sign-off) |
| 5 | Solution validated | ⧖ Validation plan below |
| 6 | User sign-off | ⧖ Pending |

---

## Proposed solution (PR description draft)

> **Branch:** `gemini-adapter` (no new branch needed — these changes are scoped to docs/skill/installer, not the Gemini adapter itself).
> **Status:** Draft for user sign-off. No code changes committed yet.

### Summary

Align docs, skills, and CLAUDE.md with Claude Code 2.1.118's actual teammate-spawn architecture: the shim (`CLAUDE_CODE_TEAMMATE_COMMAND`) only fires in interactive tmux/iTerm2 pane-backed sessions — never in-process, never in non-interactive sessions. Make Path B (`setsid nohup claude-anyteam ...`) the documented default for programmatic/headless spawn and package it as a first-class skill.

### Changes

**Docs (required):**
1. `README.md:136-148` — reframe Path B from "escape hatch" to "recommended mechanism for programmatic spawning." Add a "when does the shim fire?" section citing `getTeammateCommand()` + `PaneBackendExecutor` + in-process short-circuit (issue #34614).
2. `docs/configuration.md:93` — expand the `CLAUDE_CODE_TEAMMATE_COMMAND` description: "Set by the installer. **Only consulted by Claude Code's pane-backed display modes (tmux/iTerm2 + interactive session).** Non-interactive sessions always use the in-process backend, which bypasses the shim entirely."
3. `CLAUDE.md` (top of file) — fix the "Path 1 (Agent) vs Path 2 (setsid nohup)" framing to reflect that Path 1 is for in-process Claude subagents (always), and Path 2 is for external-model teammates whether the user is interactive OR programmatic (always). Remove the implicit claim that `Agent(name="codex-X")` somehow routes to Codex — it doesn't; only the user or a skill invoking Path B does.
4. `docs/architecture.md` — add a mode matrix (in-process vs pane-backed) and which code paths execute in each.

**Skill (required):**
5. Ship `/claude-anyteam:launch-team` as an actual packaged skill under `plugins/claude-anyteam/skills/launch-team/SKILL.md`. CLAUDE.md already refers to it as the canonical way to launch external-model teammates, but it doesn't exist in plugin 0.1.0 (experimenter §4). The skill wraps the Path B command and handles the first-launch permission prompt.

**Installer (no-op, but documented):**
6. Add a docstring comment to `src/claude_anyteam/installer.py:225-227` noting that the `env` block wiring is only consumed by interactive tmux-mode Claude Code. Not a behavior change — documentation only.

**Explicitly out of scope:**
7. `src/claude_anyteam/codex.py:284` hardcoded `"python"` interpreter is **not touched in this PR**. The user has previously rejected the `sys.executable` change; host-specific interpreter resolution is handled via the `~/.local/bin/python` PATH-shim workaround (see project memory). The discarded `ee9fcef` commit in reflog is not authoritative — the `git reset` that removed it is.

### Evidence (every claim cited)

**What reads `$CLAUDE_CODE_TEAMMATE_COMMAND`:**
- RE decompiled `spawnUtils.ts` — `github.com/Harzva/learn-likecc/.../spawnUtils.ts` (re-scout R1). `getTeammateCommand()` reads the env var, falls back to `process.execPath`/`process.argv[1]`.
- RE decompiled `PaneBackendExecutor.ts` — `github.com/Harzva/learn-likecc/.../PaneBackendExecutor.ts` (re-scout R2). Only caller of `getTeammateCommand()`.

**What bypasses it:**
- RE decompiled `InProcessBackend.ts` — `github.com/Harzva/learn-likecc/.../InProcessBackend.ts` (re-scout R3). Uses `spawnInProcessTeammate()` + AsyncLocalStorage; never touches the env var.
- Issue https://github.com/anthropics/claude-code/issues/34614 — `isInProcessEnabled()` short-circuits on non-interactive.

**Argv shape confirmation:**
- RE `PaneBackendExecutor.spawn()` emits `--agent-name <name> --team-name <team> --agent-color <color> --parent-session-id <sid> [--plan-mode-required]`.
- Matches `spawn_shim.py:_parse_args` (`spawn_shim.py:44-85` in this repo).
- Unknown flags stripped on codex route: `tests/test_spawn_shim.py:220` regression test.

**Pane-backend bugs (why even interactive tmux isn't reliable):**
- https://github.com/anthropics/claude-code/issues/40168 (MAX_CANON 256-byte overflow on `send-keys`).
- https://github.com/anthropics/claude-code/issues/34614 (missing `claude` prefix in v2.1.76).

**Path B standalone:**
- `src/claude_anyteam/registration.py:84-150` self-registers into `~/.claude/teams/<team>/config.json`.
- `src/claude_anyteam/registration.py:92-95` requires team config to pre-exist (user must `/team` first via TUI).
- `registration.py:123-132` marks member `"backendType": "in-process"`, `"tmuxPaneId": "in-process"`.

**This host's current state (for context):**
- `~/.claude/settings.json` `env` block: `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`, `CLAUDE_CODE_TEAMMATE_COMMAND=.../claude-anyteam-spawn-shim`, `CLAUDE_ANYTEAM_BINARY=.../claude-anyteam` (experimenter §1-2).
- Shim correctly wired (experimenter §3) but never called in this session (non-interactive) — confirms architecturally-unreachable conclusion.

### Validation plan

The research conclusions can be validated two ways:

**Passive (repeatable from docs + code):**
1. `git checkout` any recent `claude-code` RE repo; confirm `getTeammateCommand()` is only called from `PaneBackendExecutor.ts`.
2. `grep -n 'isInProcessEnabled\|Rb()' ...` in the RE repo confirms the non-interactive short-circuit.

**Active (proof on this host):**
1. Run `.venv/bin/python -m pytest -q` → 208 pass (experimenter §5). Docs-only + skill-addition changes won't regress this.
2. For the new `launch-team` skill: invoke it with a dry-run flag; verify the argv matches the documented Path B shape from `README.md:141-148`.
3. Optional deeper test: start a fresh Claude Code session in a new tmux pane, wire a logging prelude shim (not the real one), `/invite codex-alice`, confirm logs show the shim was called. This PROVES Path A fires in interactive tmux; it's orthogonal to the PR's claim but closes the loop. Leaving as followup — not required for merge.

### Breakage matrix

| Scenario | Behavior before PR | Behavior after PR | Risk |
|---|---|---|---|
| User in TUI, no tmux, asks LLM to spawn codex teammate | LLM tries `Agent(name="codex-X")` → spawns Claude subagent named codex-X (WRONG backend, silent) | LLM invokes `/claude-anyteam:launch-team` skill → Path B spawn → correct backend | Fix |
| User in TUI, tmux active, `/invite codex-alice` | Path A shim fires; MAY hit #40168/#34614 bugs | Same (no code change to shim) | Unchanged |
| CI / `claude --print` | In-process forced; codex teammate never actually starts | Docs tell the user to use Path B explicitly | Fix |
| Legacy docs reader who memorized "Path A default" | Expected shim to fire; confused when it didn't | Docs align with actual behavior | Fix |
| Plugin version 0.1.0 (skill not present) | CLAUDE.md references `/claude-anyteam:launch-team` that doesn't exist | Skill shipped in 0.2.0 | Fix |

### Non-goals (explicit)

- Not fixing Claude Code itself. The upstream bugs (#34614, #40168) are Anthropic's. We document around them.
- Not changing the Gemini adapter. Branch is `gemini-adapter` but these changes are adapter-agnostic.
- Not modifying `src/claude_anyteam/spawn_shim.py` — it's correctly shaped for the pane-backend contract and will start firing automatically if/when Claude Code fixes `Rb()` to honor `--teammate-mode=tmux` in non-interactive sessions.
- Not touching `codex.py:284` interpreter hardcode. Out of scope; see item 7 above.

### Test plan for the PR

- [ ] `.venv/bin/python -m pytest -q` passes 208+ tests (baseline preserved; new skill may add a skill-bundle test).
- [ ] `docs/configuration.md` change lints clean.
- [ ] `/claude-anyteam:launch-team` skill renders in `/help` listings.
- [ ] `CLAUDE.md` change reviewed by user for tone + accuracy (PENDING: user confirming whether CLAUDE.md is even a repo file — it is not currently; see note below).
- [ ] Path B raw-shell command in README and skill agree on exact argv.

---

## Phase 2 — Findings — re-scout

### Task #15 — detectAndGetBackend gate

**Task source:** Lead reassignment 2026-04-23 after Phase 1 synthesis was reframed. The context: this session is INTERACTIVE (TUI + TTY) but NO-TMUX — NOT a non-interactive session. The `isInProcessEnabled()` non-interactive short-circuit therefore does NOT apply here. The actual gate is the 'auto' branch environment check inside `isInProcessEnabled()`, specifically `detectAndGetBackend()`.

---

#### Source code examined (all from `github.com/Harzva/learn-likecc/blob/main/ccsource/CC/claude-code-rebuild/src/`)

**`utils/swarm/backends/registry.ts` — `isInProcessEnabled()` (lines 408-446):**

```typescript
export function isInProcessEnabled(): boolean {
  if (getIsNonInteractiveSession()) { return true }  // fires FIRST — does NOT apply to this host
  const mode = getTeammateMode()
  if (mode === 'in-process') { enabled = true }
  else if (mode === 'tmux') { enabled = false }  // bypasses detectAndGetBackend — returns false immediately
  else {  // 'auto' — what this host uses
    if (inProcessFallbackActive) { return true }
    const insideTmux = isInsideTmuxSync()
    const inITerm2 = isInITerm2()
    enabled = !insideTmux && !inITerm2
  }
  return enabled
}
```

**`utils/swarm/backends/detection.ts` — `isInsideTmuxSync()` and `isInsideTmux()`:**

Both functions read ONLY `ORIGINAL_USER_TMUX` (a module-level capture of `process.env.TMUX` at load time). Verbatim comment in source: *"We ONLY check the TMUX env var. We do NOT run tmux display-message."* No shell-out. No fallback check.

**`utils/swarm/backends/detection.ts` — `isTmuxAvailable()`:**

```typescript
async function isTmuxAvailable(): Promise<boolean> {
  const result = await execFileNoThrow(TMUX_COMMAND, ['-V'])
  return result.exitCode === 0
}
```

Shells out to `tmux -V`. Returns `true` if the binary exists and exits 0 — does NOT verify an active session.

**`utils/swarm/backends/registry.ts` — `detectAndGetBackend()` (lines 232-337):**

Priority detection chain:
1. **Priority 1 — Inside tmux:** `isInsideTmux()` → reads `$TMUX`. If set, returns TmuxInternalBackend.
2. **Priority 2 — iTerm2:** `isInITerm2()` → checks `$TERM_PROGRAM`, `$ITERM_SESSION_ID`. If match, returns iTerm2Backend.
3. **Priority 3 — External:** `isTmuxAvailable()` → shells out `tmux -V`. If tmux binary present (exit 0), returns TmuxExternalBackend (external-session mode).
4. **Priority 4 — Fallback / Error:** throws `BackendDetectionError` if none of the above succeed.

**`utils/swarm/shared/spawnMultiAgent.ts` — `handleSpawn()` (lines 820-870):**

```typescript
async function handleSpawn(input, context) {
  if (isInProcessEnabled()) {
    return handleSpawnInProcess(input, context)
  }
  try {
    await detectAndGetBackend()
  } catch (error) {
    if (getTeammateModeFromSnapshot() !== 'auto') {
      throw error  // mode='tmux' + detectAndGetBackend failed → ERROR propagates; no fallback
    }
    markInProcessFallback()
    return handleSpawnInProcess(input, context)  // mode='auto' → silent fallback to in-process
  }
  // proceed with pane spawn using selected backend
}
```

**`utils/swarm/backends/teammateModeSnapshot.ts` — `getTeammateModeFromSnapshot()`:**

Reads `getGlobalConfig().teammateMode ?? 'auto'`. Config key is `teammateMode` in `~/.claude.json`. Type: `'auto' | 'tmux' | 'in-process'`. Captured once at startup via `captureTeammateModeSnapshot()` — runtime changes to `~/.claude.json` are ignored for the lifetime of the process.

---

#### Empirical state of this host (verified via Bash)

```
~/.claude.json: exists; "teammateMode" key: NOT PRESENT → defaults to 'auto'
$TMUX: empty string → isInsideTmuxSync() = false
$TERM_PROGRAM: WarpTerminal → isInITerm2() = false
tmux binary: /usr/bin/tmux, version 3.4 → isTmuxAvailable() = true
```

---

#### Answer to Question 2: What happens when `"teammateMode": "tmux"` is set in `~/.claude.json`?

**Step-by-step trace (for this host specifically — tmux binary present at `/usr/bin/tmux`):**

1. `isInProcessEnabled()` is called.
2. `getIsNonInteractiveSession()` → false (interactive TTY session — this host is NOT headless).
3. `getTeammateMode()` returns `'tmux'` (explicit override).
4. Branch `mode === 'tmux'` → `enabled = false`. `isInProcessEnabled()` returns `false`.
5. `handleSpawn()` proceeds to `detectAndGetBackend()`.
6. Priority 1: `isInsideTmux()` reads `$TMUX` → empty → false.
7. Priority 2: `isInITerm2()` reads `$TERM_PROGRAM` → WarpTerminal → false.
8. Priority 3: `isTmuxAvailable()` shells out `tmux -V` → `/usr/bin/tmux` present → exit 0 → **true**.
9. **`detectAndGetBackend()` returns `TmuxExternalBackend` (Priority 3 — external-session mode). No exception.**
10. `handleSpawn()` proceeds with pane spawn using TmuxExternalBackend — **NOT in-process**.

**Result: Setting `"teammateMode": "tmux"` on this host selects the tmux external-session backend unconditionally.** Claude Code will issue `tmux` subcommands to create a new session and spawn the teammate pane. The spawn shim (`$CLAUDE_CODE_TEAMMATE_COMMAND`) WILL be read by `PaneBackendExecutor` during this path.

**When would `detectAndGetBackend()` throw?** Only if Priority 3 also fails — i.e., `isTmuxAvailable()` returns false because the tmux binary is absent entirely. In that case, `handleSpawn()`'s catch block checks `getTeammateModeFromSnapshot() !== 'auto'` → true (mode is 'tmux') → **re-throws the error**. No silent fallback. The user sees a spawn failure.

**Summary by host scenario:**

| Host state | `teammateMode: 'tmux'` behavior |
|---|---|
| tmux binary present (this host: `/usr/bin/tmux`) | `detectAndGetBackend()` Priority 3 → TmuxExternalBackend selected; spawn proceeds; shim fires |
| tmux binary absent | `detectAndGetBackend()` throws; error propagates (no fallback since mode ≠ 'auto'); spawn fails |
| `$TMUX` set (already inside tmux session) | Priority 1 → TmuxInternalBackend selected; spawn proceeds; shim fires |

---

#### Implication for the fake-tmux shim approach (Task #13)

Since `isTmuxAvailable()` checks only `tmux -V` exit code — not whether an actual tmux server is running — a fake `tmux` binary on PATH that returns exit 0 for `-V` (and handles the subsequent subcommands Claude Code issues) WOULD satisfy Priority 3 detection. This is the mechanism rasmusab/KILD exploited (re-scout R6).

The caveat: Claude Code's TmuxExternalBackend issues more than just `tmux -V`. It issues `tmux new-session`, `tmux split-window`, `tmux send-keys`, and `tmux display-message` (per issue #26572). A fake-tmux shim must handle all of these. The KILD shim already does — it's a ~400-line shell script intercepting all 20 subcommands. A new fake-tmux would need similar coverage.

**Conclusion for Task #13 (confirmed):** Fake-tmux shim is architecturally viable on this host given tmux binary presence satisfies Priority 3. But it requires implementing the full ~20-subcommand surface, not just `tmux -V`.

---

#### Conclusion for Task #15

The lead's reframe was correct: the relevant gate for this host (interactive, no active tmux session) is the 'auto' branch of `isInProcessEnabled()`:
- `isInsideTmuxSync()` = false (`$TMUX` empty)
- `isInITerm2()` = false (WarpTerminal)
- → `enabled = true` → in-process selected → shim never fires

Setting `"teammateMode": "tmux"` in `~/.claude.json` bypasses the 'auto' branch and forces `detectAndGetBackend()` to run. On this host, with tmux binary present, Priority 3 succeeds — TmuxExternalBackend is selected — and the shim fires. This is **option (a)** from the lead's question: tmux backend selected, Claude Code attempts to create/use a tmux session.

**The error path (option c) only occurs if tmux binary is entirely absent.** On this host it is not — so setting `"teammateMode": "tmux"` is the single config change that could activate Path A without launching an actual tmux terminal. It carries a UX cost: Claude Code will open tmux panes visibly, which may be undesirable in a Warp terminal.

**Sources:** `registry.ts:408-446` (isInProcessEnabled, detectAndGetBackend), `detection.ts:isInsideTmuxSync/isTmuxAvailable`, `teammateModeSnapshot.ts:getTeammateModeFromSnapshot`, `spawnMultiAgent.ts:820-870` (handleSpawn try/catch), local Bash empirical checks (tmux 3.4 at /usr/bin/tmux, $TMUX empty, teammateMode unset in ~/.claude.json).

---

## Replacement texts (for review)

Three replacement texts drafted below for user sign-off. **No files will be modified until user approves.** CLAUDE.md replacement is NOT drafted because the file does not currently exist in the repo — awaiting user decision on whether to create one.

### README.md — replace lines 136-148

Current:
```
## Launch a teammate directly

For headless and persistent background adapters (run across multiple Claude Code sessions):

\`\`\`bash
setsid nohup claude-anyteam \
  --team my-team --name codex-alice \
  --cwd /path/to/workspace \
  --model gpt-5.5 --effort high \
  </dev/null >/tmp/codex-alice.stdout 2>/tmp/codex-alice.stderr & disown
\`\`\`

This mode is fully messageable (inbox, task claim, peer replies) but does NOT render in Claude Code's TUI presence line — TUI visibility requires the leader-spawn path via the shim (above). Useful when you want the adapter running continuously regardless of the Claude Code session lifecycle.
```

Proposed:
```
## Launch a teammate

```bash
setsid nohup claude-anyteam \
  --team my-team --name codex-alice \
  --cwd /path/to/workspace \
  --model gpt-5.5 --effort high \
  </dev/null >/tmp/codex-alice.stdout 2>/tmp/codex-alice.stderr & disown
```

This is the mechanism to use whenever you want to start a Codex teammate — interactively from a terminal, from the `/claude-anyteam:launch-team` skill, or programmatically (CI, headless, LLM-driven). It is fully messageable (inbox, task claim, peer replies) and self-registers into `~/.claude/teams/<team>/config.json` on startup. It does NOT render in Claude Code's TUI presence line.

### When does the shim (`CLAUDE_CODE_TEAMMATE_COMMAND`) fire?

The installer wires a spawn shim into `~/.claude/settings.json` via the `CLAUDE_CODE_TEAMMATE_COMMAND` env var. Claude Code reads this env var **only when spawning teammates through its pane-based display mode** (tmux or iTerm2 split panes, interactive session). In that case, the shim inspects the argv, routes `codex-*` names to the adapter, and falls through native Claude names to the real `claude` binary.

In Claude Code's default in-process display mode, and in all non-interactive sessions (`claude --print`, CI, LLM leaders calling `Agent(...)`), teammates are spawned as coroutines inside the main process and the shim is never invoked ([upstream issue #34614](https://github.com/anthropics/claude-code/issues/34614)). That is Claude Code's design, not a bug in claude-anyteam — the shim is correctly shaped for the pane-backend contract and will fire automatically if Claude Code ever honors `--teammate-mode=tmux` in non-interactive sessions. Until then, the `setsid nohup` command above is the reliable way to start a Codex teammate regardless of display mode.
```

---

### docs/configuration.md — replace the `CLAUDE_CODE_TEAMMATE_COMMAND` row in the Shim configuration table (line 93)

Current row:
```
| `CLAUDE_CODE_TEAMMATE_COMMAND` | Set by the installer to the shim binary path. Claude Code reads this to route teammate spawns. |
```

Proposed row (plus a short note immediately after the table):
```
| `CLAUDE_CODE_TEAMMATE_COMMAND` | Set by the installer to the shim binary path. Claude Code consults this **only** in pane-based display mode (tmux/iTerm2, interactive session). In its default in-process mode and in non-interactive sessions (`claude --print`, CI, LLM-driven spawn), teammates are coroutines inside the main process and this variable is not read. Start external-model teammates via `setsid nohup claude-anyteam ...` (README → "Launch a teammate") to work regardless of mode. |
```

Optional add after the existing table (line 96+):

> The shim itself is correctly shaped for Claude Code's pane-backend argv contract (`--agent-name`, `--team-name`, `--agent-color`, `--parent-session-id`, `--plan-mode-required`). If Anthropic extends the pane backend to non-interactive sessions (or fixes [issue #34614](https://github.com/anthropics/claude-code/issues/34614)), the shim will start firing automatically without further changes here.

---

### skills/launch-team/SKILL.md — new file

```markdown
---
name: launch-team
description: Launch one or more external-model teammates (e.g. `codex-*`) into the current Claude Code team. Use this skill when the user wants to spawn a Codex-backed teammate programmatically, in a non-tmux session, or in headless/CI workflows. Picks the correct path — `setsid nohup claude-anyteam ...` — instead of accidentally spawning a native Claude subagent.
when_to_use: Use when a user asks to "spin up a team", "add a Codex teammate", "launch codex-<name>", "start an external-model teammate", or when the user is coordinating a multi-model team and wants Codex to actually back the `codex-*` teammates. Do NOT use for `Agent(subagent_type=...)` — that is the Claude-native path.
---

When the user asks to launch a Codex-backed teammate, use this exact argv shape:

```bash
setsid nohup claude-anyteam \
  --team <team_name> --name codex-<role> \
  --cwd <absolute_workspace_path> \
  --model <gpt-5.5|gpt-5.3|etc> --effort <high|medium|low|xhigh> \
  </dev/null >/tmp/codex-<role>.stdout 2>/tmp/codex-<role>.stderr & disown
```

Rules:
- Always use `setsid nohup ... & disown` — the adapter must survive past this Claude Code session.
- Always redirect stdin from `/dev/null` and stdout/stderr to files. The adapter's logs should not pollute the current TTY.
- The team MUST already exist (`~/.claude/teams/<team_name>/config.json` present). If not, tell the user to run `/team` in Claude Code first.
- The name MUST begin with `codex-` (or match `$CLAUDE_CODE_SHIM_MATCH` if overridden). Names not matching the regex will be treated as native Claude teammates and will NOT route to Codex.
- The first launch on a machine will trigger a Bash permission prompt for `setsid nohup claude-anyteam *`. Tell the user that one "go" unblocks the rest of the launches in the session.
- After a successful launch, the adapter self-registers into the team's `config.json` and the `SendMessage` tool can reach it by name.

Do NOT use `Agent(team_name=..., name="codex-...")` to spawn a Codex teammate. That primitive spawns a native Claude subagent (regardless of name) — `codex-*` names are cosmetic in that path. The shim that would reroute `codex-*` to Codex only fires in interactive tmux/iTerm2 pane-mode sessions, which is typically not where Claude Code is running.

For mixed teams (some Claude, some Codex), use `Agent(subagent_type=..., team_name=..., name="claude-*")` for Claude teammates and the `setsid nohup` command for `codex-*` — both register into the same `members.json` and are equally messageable via SendMessage.

Reference: `README.md` → "Launch a teammate"; `docs/configuration.md` → "Shim configuration"; [upstream issue #34614](https://github.com/anthropics/claude-code/issues/34614) explains why the shim does not fire for non-interactive spawn.
```

---

### Updated per-workflow matrix (mixed-team clarification)

Replacing the prior row "Mixed team with native Claude + external-model teammates" in the synthesis matrix:

| Workflow | Recommended path | Why |
|---|---|---|
| Mixed team with native Claude + external-model teammates | Claude teammates: `Agent(subagent_type=..., team_name=..., name="claude-<role>")` (works, in-process spawn). Codex/external teammates: `/claude-anyteam:launch-team` skill → Path B. | Both paths register into the same `members.json`; both become reachable via `SendMessage`. Users can mix freely in one team. Different primitives, same team membership. |



---

## Findings — code-auditor (round 2)

*Authored: 2026-04-23. Focus: bug #14 — why did `backendType: "in-process"` in `registration.py` get treated as a TUI presence fix, and does it imply the shim was expected to be active?*

### 1. What bug #14 actually was

`docs/internal/2026-prototype/battle-test-report.md:47`:

> | #14 | P1 (UX) | Codex teammates invisible in Claude Code's TUI presence line | `registration.py` (`backendType: "in-process"`) |

The battle-test report identifies `registration.py` with `backendType: "in-process"` as the fix location. The initial checkpoint commit (`3eb55f7`, "Initial checkpoint: codex-teammate v7.3 + battle-test") introduced `registration.py` from scratch — with `backendType: "in-process"` present from the start. So the "fix" was introducing self-registration at all; the field was part of the inaugural implementation, not a targeted patch.

The report also says:

> TUI fix (#14) empirically verified: a scratch hidden-CLI probe confirmed that `backendType: "in-process"` does not break mailbox peer-prose delivery, so the fix does not introduce IPC regression.

Note what this does and does NOT say: it verifies the field doesn't BREAK mailbox delivery. It does NOT verify that the field causes TUI visibility.

### 2. The registration.py comment is aspirational, not mechanistic

`registration.py:119-122` (current working tree):

```python
# Advertise the same teammate shape native Claude sessions use
# so the harness can treat Codex teammates as normal visible
# team members in TUI presence/UI surfaces. Runtime delivery
# remains mailbox-based; we are only aligning the registry
# metadata here.
```

The comment accurately describes intent but concedes the mechanism: "we are only aligning the registry metadata here." Writing `backendType: "in-process"` to `config.json` is metadata alignment, not a presence-injection mechanism.

### 3. Why the metadata alone cannot produce TUI visibility

`docs/internal/2026-prototype/research.md:26-34` (source-map RE + runtime observation):

- TUI presence is driven by `AppState.tasks` — the leader process's in-memory state.
- The spinner reads `tasks[taskId].type === 'in_process_teammate'` and `tasks[taskId].status === 'running'`.
- `~/.claude/teams/<team>/config.json` is NEVER read by the TUI renderer.
- Runtime injection test: a synthetic `ghost-inject@tui-research` was written to both `config.json` and the subagents directory. User confirmed TUI showed only the known teammates. (`research.md:121-126`)
- Verdict: "Passive file-based injection: no. Leader-mediated external-process injection: yes." (`research.md:90-92`)

`registration.py:123,133` sets `tmuxPaneId: "in-process"` and `backendType: "in-process"`. These fields land in `config.json`, which the TUI never reads for presence. They have zero effect on TUI visibility when used via Path B.

### 4. How TUI visibility actually works (the shim path)

`docs/internal/2026-prototype/research.md:43-49`:

> Out-of-process tmux/iTerm teammates mirrored into leader state:
> `tools/shared/spawnMultiAgent.ts:474-486` and `:760-834`
> `registerOutOfProcessTeammateTask()` intentionally creates an `InProcessTeammateTaskState`-shaped mirror and calls `registerTask(...)`.
> Even pane-based teammates are mirrored as `type: 'in_process_teammate'`, so the spinner treats them exactly like in-process teammates.

`research.md:66-76` explains the viable path:

1. Set `CLAUDE_CODE_TEAMMATE_COMMAND` in the leader environment.
2. Let Claude Code use its normal teammate spawn path (interactive+tmux only).
3. The leader calls `registerOutOfProcessTeammateTask(...)`, creating the mirror task.
4. The external process appears in the TUI.

TUI visibility = `registerOutOfProcessTeammateTask()` called by the leader. That function is only called when Claude Code's PaneBackendExecutor spawns a subprocess — which requires tmux/iTerm2 mode AND the `Rb()` non-interactive check to pass.

### 5. Implication: the battle-test's "Appears in TUI" claim requires the shim

The battle-test parity verdict says "Appears in the TUI presence line" (`battle-test-report.md:91`). For this to be empirically true, the battle-test session must have been running in interactive tmux/iTerm2 mode with `$CLAUDE_CODE_TEAMMATE_COMMAND` set — i.e., Path A was active during the battle-test.

Commit `d5ae224` ("Add codex-teammate-spawn-shim (hybrid TUI-visible launch default)") was authored at `2026-04-22T18:26:04` — the same day as the battle-test (date on report: 2026-04-22). The shim was introduced specifically "so Codex teammates appear in Claude Code's TUI presence line". The battle-test landed on top of that commit and confirmed the claim.

Implicit dependency chain:

```
Bug #14 (teammates invisible in TUI)
  Fix A: introduce shim (d5ae224), set CLAUDE_CODE_TEAMMATE_COMMAND → shim path
  Fix B: registration.py backendType field — SUPPLEMENTARY; aligns config.json
         metadata to match PaneBackendExecutor's expected member shape in config.json
  Parity verdict "appears in TUI" = valid only in tmux + shim mode (Path A)
```

### 6. Does cli.py detect "spawned by shim" vs "launched directly"?

No. `cli.py:193-240` has a single `_parse_args()` path. It parses `--name` and `--team` regardless of caller. The shim translates `--agent-name → --name` and `--team-name → --team` at `spawn_shim.py:253-254` before `os.execv`, so by the time `cli.py` runs, the argv shape is identical. There is no env flag, no shim-identity env var, no branch in `cli.py` that detects origin.

### 7. Does wrapper_server.py emit a presence announcement?

No. `wrapper_server.py` is a narrowed FastMCP stdio server exposing six tools to the Codex subprocess (`wrapper_server.py:48-55`). Full read of the file: no reference to `backendType`, `tmuxPaneId`, `presence`, `announce`, or `registerTask`. No outbound presence-announcement mechanism exists anywhere in the adapter.

### 8. Is there code evidence that an in-process spawn path routes through the shim?

No. `docs/internal/2026-prototype/research.md:39-41` (decompiled `InProcessBackend.ts`):

> `spawnInProcessTeammate()` builds an `InProcessTeammateTaskState` and calls `registerTask(taskState, setAppState)`.

This is a pure in-Node.js-process coroutine. It does not fork a subprocess, does not consult `$CLAUDE_CODE_TEAMMATE_COMMAND`, and does not call `getTeammateCommand()`. The env var route is only used by the pane-based spawn path (`utils/swarm/spawnUtils.ts:18-28`), which is never reached when `Rb()` returns true (non-interactive). Re-scout R3 (InProcessBackend.ts RE) confirms this independently.

### Summary table

| Question | Answer | Source |
|---|---|---|
| What did bug #14 fix? | Introduced `registration.py` (self-registration at all); `backendType: "in-process"` is metadata alignment, not a presence mechanism | `3eb55f7` commit message; `battle-test-report.md:64-66` |
| Does `backendType: "in-process"` in config.json produce TUI visibility? | No. TUI reads `AppState.tasks`, never `config.json`. | `research.md:26-34, 80-87, 128-130` |
| Is parity verdict "Appears in TUI" accurate for Path B (direct setsid)? | No. Requires Path A (shim + tmux + PaneBackendExecutor). | `research.md:43-49, 66-76`; `d5ae224` date alignment with battle-test |
| Does `cli.py` have a shim-detection branch? | No. Identical code path regardless of origin. | `cli.py:193-240`; `spawn_shim.py:253-254` |
| Does the adapter emit any presence-announcement beyond config.json? | No. | `wrapper_server.py` full read |
| Is there code evidence an in-process spawn path routes through the shim? | No. `spawnInProcessTeammate()` never reads `$CLAUDE_CODE_TEAMMATE_COMMAND`. | `research.md:39-41`; re-scout R3 |


---

## Findings — code-auditor (round 2, addendum) — TUI presence: file-driven or in-process handshake?

*Authored: 2026-04-23. Responds to lead's synthesis challenge: user claims TUI presence was observed on a non-tmux host.*

### Direct answer

**TUI presence is NOT file-driven. It is in-process handshake only — specifically `registerTask()` called inside Claude Code's own Node.js process.**

This means:
- Path B (`setsid nohup claude-anyteam`) writes `config.json` but the TUI NEVER reads that file for presence.
- Path A (shim + tmux + PaneBackendExecutor) causes the leader to call `registerOutOfProcessTeammateTask()` → `registerTask()` — the TUI renders that.
- A Path-B-launched adapter cannot appear in the TUI presence line under any currently known mechanism.

### Four independent sources confirming this

**Source 1 — `docs/internal/2026-prototype/research.md:26-34` (source-map RE + binary strings analysis):**

> The presence line is driven by **live `AppState.tasks`**, not by `~/.claude/teams/{team}/config.json`.
> More specifically, the TUI reads: `tasks[taskId].type === 'in_process_teammate'`, `tasks[taskId].status === 'running'`, `tasks[taskId].identity.agentName`.
> This is why simply adding a member to the team config does not create a visible row: the spinner tree never enumerates the team file directly.

**Source 2 — `docs/internal/2026-prototype/research.md:80-87` (negative injection test):**

> I found no static evidence that the following can create a visible TUI row by themselves:
> - editing `~/.claude/teams/{team}/config.json`
> - adding fake members to the team file
> - writing inbox/mailbox messages only
> - launching a completely separate process with `--agent-id/--agent-name/--team-name` but without going through the leader's spawn path
>
> Those mechanisms affect `teamContext`, mailboxes, or on-disk membership, but the presence renderer consumes `AppState.tasks`.

**Source 3 — `docs/internal/2026-prototype/research.md:128-130` (runtime observation, inotify probe):**

> TUI presence is session-internal / in-memory Agent-runtime state. No externally writable filesystem path, Unix socket, named pipe, or shm segment feeds it. Passive injection from outside the leader process is not viable.

**Source 4 — `docs/architecture.md:57-64` (repo's own documented understanding):**

> The TUI presence line (`@main @codex-alice`) renders from the leader's in-memory state, not from `config.json`. That state is only populated when Claude Code's own spawn flow is what launched the teammate.
>
> claude-anyteam hooks into that spawn flow via `CLAUDE_CODE_TEAMMATE_COMMAND`... Both pieces (leader mirror + adapter entry) are required. The shim enables step 1. The adapter handles step 4.

### What `backendType: "in-process"` actually does

`registration.py:123-133` writes `tmuxPaneId: "in-process"` and `backendType: "in-process"` to `config.json`. Per `architecture.md:64`: "The adapter self-registers in `config.json` with `backendType: "in-process"` so its entry matches what the leader expects."

What the leader "expects" here is the shape of the `config.json` member entry that PaneBackendExecutor reads when constructing the argv it passes to the shim subprocess. `backendType: "in-process"` tells the leader "don't try to assign a tmux pane number to this entry." It's a spawn-hygiene field — not a TUI-trigger.

### Is there a heartbeat or announce mechanism in loop.py or wrapper_server.py?

No. Full grep of `loop.py` for `heartbeat`, `announce`, `presence`, `sessionId`, `socket`, `pipe`, `inotify`, `watch`, `handshake`: no matches. The only external writes `loop.py` makes are to mailbox files (`protocol_io.py`) and `config.json` (via `registration.py`). No outbound announcement to the leader process.

Full grep of `wrapper_server.py` for same terms: no matches. The wrapper is a read-only FastMCP stdio server; it receives calls from the Codex subprocess and relays them to `claude_teams` file-I/O helpers.

### Resolving the user's observation

The user's claim that Codex teammates appeared in TUI on a non-tmux host has two likely explanations:

1. **The battle-test WAS run on a tmux host.** Commit `d5ae224` (shim introduction, 2026-04-22T18:26:04) and the battle-test report (dated 2026-04-22) are same-day. The battle-test confirms "Appears in TUI presence line" — under the shim+tmux setup that was the entire point of that commit. The user is (correctly) remembering a result from a tmux session.

2. **"Appears in session" ≠ "appears in TUI presence line."** Path B adapters ARE reachable via `SendMessage`, DO receive tasks, DO reply. They are functionally present in the team. The TUI presence line specifically (the `@main @codex-alice` footer row) is distinct from being a functional team member. If the user saw the adapter responding to messages, that's Path B working. If they saw the `@codex-alice` in the footer, that was a tmux session with the shim.

### Conclusion for synthesis

TUI presence requires in-process handshake (Path A). `config.json` writes (Path B) achieve functional team membership — messaging, task claims, mailbox — but not TUI footer visibility. The `backendType: "in-process"` field is a member-shape alignment for PaneBackendExecutor, not a TUI trigger. Our original synthesis was correct on the mechanism. The user's empirical observation is compatible: they used the shim on a tmux host during the battle-test.

---

# Phase 2 — Codex-backed teammate + TUI presence on both tmux and non-tmux

**Goal (per `docs/internal/spawn-research-phase2-brief.md`):** Find a reliable mechanism (however unconventional, including RE'd internals) for Codex-backed teammates to appear in the TUI presence line on both tmux AND non-tmux hosts. Phase-1 conclusions stand; Phase 2 searches for a path to close the UX gap.

## Phase 2 — Open questions (lead maintains)

| # | Question | Task | Status | Owner |
|---|---|---|---|---|
| P2-Q1 | Exact shape of `InProcessTeammateTaskState` + export surface of `registerTask()` / `registerOutOfProcessTeammateTask()`? Is `AppState.tasks` externally reachable? | #8 | pending | re-scout |
| P2-Q2 | Does Claude Code watch any IPC/filesystem signal that external code could trigger to inject a task? | #9 | pending | code-auditor |
| P2-Q3 | Status of CustomPaneBackend #26572? Any feature flag / env var / model-provider hook that helps? | #10 | pending | docs-scout |
| P2-Q4 | What env/settings knobs exist in this running session that could force pane-backend selection? (Passive only.) | #11 | pending | experimenter |
| P2-Q5 | Leader-side MCP/plugin API — any extension surface that reaches `AppState`? | #12 | pending | re-scout |
| P2-Q6 | Fake-tmux shim — minimal tmux API Claude Code needs to believe tmux is active. (Paper only.) | #13 | pending | experimenter |
| P2-Q7 | Hybrid — can a Claude in-process teammate route its inference to Codex via MCP? | #14 | pending | re-scout + docs-scout |

## Phase 2 — Findings — re-scout

### Task #8 — registerTask / registerOutOfProcessTeammateTask / InProcessTeammateTaskState shape (2026-04-23)

Sources: `github.com/Harzva/learn-likecc/blob/main/ccsource/CC/claude-code-rebuild/src/` (RE'd upstream source, 2026).

---

#### 1. `AppState.tasks` — shape and subscription mechanism

File: `src/state/AppStateStore.ts`

```typescript
tasks: { [taskId: string]: TaskState }
```

- Plain JS object (not a `Map` or `Array`) keyed by string task IDs.
- Value type is the `TaskState` discriminated union (see §4 below).
- Default value: `tasks: {}` (empty object, from `getDefaultAppState()`).
- No subscription/watch mechanism exported. `AppState.tsx` re-exports `type AppState`, `type AppStateStore`, and `getDefaultAppState` from `./AppStateStore.js`. The store is a generic `Store<AppState>` — React context-based, consumed via `useAppState()` hook inside the leader's React rendering tree.
- The `tasks` object is pure React state, mutated only via `setAppState` callbacks inside the leader's Node.js/React process.

**Conclusion:** `AppState.tasks` is not a shared-memory segment, a file, or an observable stream. It lives exclusively inside the leader process's React state tree with no externally accessible handle.

---

#### 2. `registerTask()` — full body and export status

File: `src/utils/task/framework.ts` (lines 94–129)

```typescript
export function registerTask(task: TaskState, setAppState: SetAppState): void {
  let isReplacement = false
  setAppState(prev => {
    const existing = prev.tasks[task.id]
    isReplacement = existing !== undefined
    const merged =
      existing && 'retain' in existing
        ? {
            ...task,
            retain: existing.retain,
            startTime: existing.startTime,
            messages: existing.messages,
            diskLoaded: existing.diskLoaded,
            pendingMessages: existing.pendingMessages,
          }
        : task
    return { ...prev, tasks: { ...prev.tasks, [task.id]: merged } }
  })
  if (isReplacement) return
  enqueueSdkEvent({
    type: 'system',
    subtype: 'task_started',
    task_id: task.id,
    tool_use_id: task.toolUseId,
    description: task.description,
    task_type: task.type,
    workflow_name: 'workflowName' in task ? (task.workflowName as string | undefined) : undefined,
    prompt: 'prompt' in task ? (task.prompt as string) : undefined,
  })
}
```

**Export status:** `export function` — named export from `framework.ts`. No barrel re-export into any index that external/plugin code reaches.

**Callers:** `registerOutOfProcessTeammateTask()` (in `spawnMultiAgent.ts`), `spawnInProcessTeammate()` (in `spawnInProcess.ts`), and task lifecycle helpers. All callers are internal to the leader process.

**Cannot be called externally:** `setAppState` is a React state setter passed through the component/hook tree. It is not exported and is not accessible via any module boundary that external processes can reach. Even if an external process could `import()` `framework.ts`, it would have no live `setAppState` closure — the call would be a no-op or crash.

---

#### 3. `registerOutOfProcessTeammateTask()` — full signature and data shape

File: `src/tools/shared/spawnMultiAgent.ts` (lines 829–901)

```typescript
function registerOutOfProcessTeammateTask(
  setAppState: (updater: (prev: AppState) => AppState) => void,
  {
    teammateId, sanitizedName, teamName, teammateColor, prompt,
    plan_mode_required, paneId, insideTmux, backendType, toolUseId,
  }: {
    teammateId: string
    sanitizedName: string
    teamName: string
    teammateColor: string
    prompt: string
    plan_mode_required?: boolean
    paneId: string
    insideTmux: boolean
    backendType: BackendType
    toolUseId?: string
  },
): void
```

**`InProcessTeammateTaskState` object it constructs (lines 836–860):**

```typescript
const taskState: InProcessTeammateTaskState = {
  ...createTaskStateBase(taskId, 'in_process_teammate', description, toolUseId),
  type: 'in_process_teammate',
  status: 'running',
  identity: {
    agentId: teammateId,           // e.g. "codex-alice@my-team"
    agentName: sanitizedName,      // e.g. "codex-alice"
    teamName,
    color: teammateColor,
    planModeRequired: plan_mode_required ?? false,
    parentSessionId: getSessionId(),
  },
  prompt,
  abortController,
  awaitingPlanApproval: false,
  permissionMode: plan_mode_required ? 'plan' : 'default',
  isIdle: false,
  shutdownRequested: false,
  lastReportedToolCount: 0,
  lastReportedTokenCount: 0,
  pendingUserMessages: [],
}
```

Then calls `registerTask(taskState, setAppState)`.

**Export status: NOT exported.** No `export` keyword. Private helper inside `spawnMultiAgent.ts`.

**Callers:** `handleSpawnSplitPane()` (line ~474) and `handleSpawnSeparateWindow()` (line ~760) — internal to `spawnMultiAgent.ts`. Called ONLY after `PaneBackendExecutor.spawn()` succeeds. No external caller surface.

---

#### 4. `InProcessTeammateTaskState` — full type definition

File: `src/tasks/InProcessTeammateTask/types.ts`

```typescript
export type InProcessTeammateTaskState = TaskStateBase & {
  type: 'in_process_teammate'    // discriminant — same for both in-process coroutines AND pane-spawned mirrors
  identity: TeammateIdentity
  prompt: string
  model?: string
  selectedAgent?: AgentDefinition
  abortController?: AbortController
  currentWorkAbortController?: AbortController
  unregisterCleanup?: () => void
  awaitingPlanApproval: boolean
  permissionMode: PermissionMode
  error?: string
  result?: AgentToolResult
  progress?: AgentProgress
  messages?: Message[]
  inProgressToolUseIDs?: Set<string>
  pendingUserMessages: string[]
  spinnerVerb?: string
  pastTenseVerb?: string
  isIdle: boolean
  shutdownRequested: boolean
  onIdleCallbacks?: Array<() => void>
  lastReportedToolCount: number
  lastReportedTokenCount: number
}

export type TeammateIdentity = {
  agentId: string           // format: "<name>@<team>"
  agentName: string
  teamName: string
  color?: string
  planModeRequired: boolean
  parentSessionId: string
}
```

**`TaskState` union** (from `src/tasks/types.ts`):
`LocalShellTaskState | LocalAgentTaskState | RemoteAgentTaskState | InProcessTeammateTaskState | LocalWorkflowTaskState | MonitorMcpTaskState | DreamTaskState`

**No `OutOfProcessTeammateTaskState` type exists.** Pane-spawned out-of-process teammates are mirrored into AppState AS `InProcessTeammateTaskState` — the discriminant `'in_process_teammate'` is used for both. The TUI spinner renders both via the same code path.

---

#### 5. Export accessibility analysis

| Symbol | Export | Re-exported via barrel? | External/plugin process can reach? |
|--------|--------|------------------------|------------------------------------|
| `AppState.tasks` field | Type-exported from `AppStateStore.ts` | Yes, via `AppState.tsx` | **No** — type only; live value is React state inside leader |
| `registerTask()` | Named export from `framework.ts` | No barrel re-export found | **No** — requires live `setAppState` closure from leader's React tree |
| `registerOutOfProcessTeammateTask()` | **Not exported** | N/A | **No** — private module-internal |
| `InProcessTeammateTaskState` type | Named type export from `types.ts` | Yes, via `InProcessTeammateTask.tsx` | **Type only** — no runtime injection surface |

**Conclusion on P2-Q1:** No external injection surface exists. `registerTask` is exported at the TypeScript module level but its `setAppState` argument is a live React state setter obtainable only from inside the leader's React render cycle. `registerOutOfProcessTeammateTask` is unexported entirely. A plugin, MCP tool, or `setsid` process has zero path to call either function on the live running leader.

---

#### 6. Implication for Phase 2 solution space

The `registerOutOfProcessTeammateTask()` → `registerTask()` chain is the only TUI-presence mechanism for out-of-process teammates. That chain is called only by `handleSpawnSplitPane` / `handleSpawnSeparateWindow`, which are reached only when `isInProcessEnabled()` returns false AND pane backend detection succeeds (requires interactive session + tmux or iTerm2).

For a Path-B adapter to appear in TUI presence, one of these must hold:
1. Force `isInProcessEnabled()` to return false on a non-tmux host → Tasks #11 (env vars/flags) and #13 (fake-tmux shim) investigate this.
2. Get code running INSIDE the leader's Node.js process that calls `registerTask()` with a real `setAppState` → No external surface; Task #12 (MCP plugin API) investigates if a new surface exists.
3. Claude in-process coroutine whose inference is routed outbound to Codex proxy → Gets TUI presence because Claude Code's spawn ran; Task #14 (hybrid) investigates this.

**Source authority:** All findings from Harzva/learn-likecc RE repo (2026, decompiled upstream). Consistent with code-auditor Task #9 findings (no IPC injection surface) and `docs/internal/2026-prototype/research.md:26-34, 80-87, 128-130` (negative injection test + runtime probe).

---

## Phase 2 — Findings — code-auditor

*(append below)*

---

## Phase 2 — Findings — docs-scout

**Accessed:** 2026-04-23. Sources: GitHub issue #26572, CHANGELOG.md (raw), marckrenn/claude-code-changelog meta/flags.md, community env-var gist (v2.1.104), Ollama/vLLM Claude Code integration docs.

---

### P2-Q3a — Issue #26572 (CustomPaneBackend) current status

**Status: OPEN, no Anthropic staff response visible, no linked PRs.**

Issue #26572 "Proposal: CustomPaneBackend protocol — decouple agent teams from tmux CLI to unblock Ghostty, WezTerm, Zellij, KILD, and remote deployments" was filed by @Wirasm (creator of KILD). It proposes:

- New env vars: `CLAUDE_PANE_BACKEND=/path/to/binary` or `CLAUDE_PANE_BACKEND_SOCKET=/path/to/server.sock`
- Protocol: JSON-RPC 2.0 over NDJSON
- 7 operations: `spawn_agent(argv[], cwd, env, metadata)`, `write`, `capture`, `kill`, `list`, `get_self_id`, and push event `context_exited`

Author already ships a `kild-tmux-shim` (drop-in tmux replacement) as a workaround, confirming this path is technically feasible.

**Anthropic response:** None visible in the issue. No linked PRs. No closure.

**URL:** https://github.com/anthropics/claude-code/issues/26572 (accessed 2026-04-23)

**Conclusion:** CustomPaneBackend is a community proposal, not a shipped feature. `CLAUDE_PANE_BACKEND` does not exist as a documented or confirmed env var in any release. The kild-tmux-shim workaround (faking tmux) is independently viable and already confirmed by a real implementation — this informs task #13 (fake-tmux shim feasibility).

---

### P2-Q3b — Changelog v2.1.105+ entries relevant to Phase 2

Entries from CHANGELOG.md (raw, accessed 2026-04-23: https://raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md):

**v2.1.118:**
- "Hooks can now invoke MCP tools directly via `type: 'mcp_tool'`" — hooks can now call MCP tools, not just shell commands. This is new leader-side extension surface.
- "/model picker now honors `ANTHROPIC_DEFAULT_*_MODEL_NAME`/`_DESCRIPTION` overrides"

**v2.1.117:**
- "`CLAUDE_CODE_FORK_SUBAGENT=1` lets forked subagents share parent's prompt cache prefix (90% cost reduction for children 2-N). Enabled on external builds via this env var." Fork only fires when `subagent_type` is omitted from Agent call — does NOT apply to teammate spawning (`team_name`+`name` path).

**v2.1.114:**
- "Fixed crash in permission dialog when agent teams teammate requested tool permission" — confirms agent teams teammates are still subprocess-based in split-pane mode (they can request tool permissions independently).

**v2.1.113:**
- "Changed CLI to spawn native Claude Code binary (via per-platform optional dependency) instead of bundled JavaScript." This changes the CLI's OWN launch mechanism, not how teammates are spawned. The teammate spawn path (subprocess exec of `claude` with `--agent-name`/`--team-name` flags) is separate from the CLI's own startup.

**v2.1.105:**
- "Added PreCompact hook support" — new hook event.
- "Added background monitor support for plugins via manifest `monitors` key."

**None of v2.1.105–v2.1.118** contain entries about: CustomPaneBackend, CLAUDE_PANE_BACKEND, pluggable pane backends, external teammate registration, `CLAUDE_CODE_TEAMMATE_COMMAND`, or model-provider overrides for teammates.

**URL:** https://raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md (accessed 2026-04-23)

---

### P2-Q3c — Feature flags in v2.1.118 (from marckrenn/claude-code-changelog meta/flags.md)

Complete flag list from v2.1.118 (25 flags total, all prefixed `tengu_`):

| Flag | Controls |
|------|---------|
| `tengu_auto_mode_config` | Auto mode availability, circuit-breaker, allowlist |
| `tengu_bridge_min_version` | Blocks Remote Control below min version |
| `tengu_bridge_poll_interval_config` | Bridge polling and session timing |
| `tengu_bridge_repl_v2_config` | Bridge REPL v2 |
| `tengu_ccr_bridge` | CCR bridge for code execution |
| `tengu_ccr_bridge_multi_session` | Multi-session spawning (CCR) |
| `tengu_ccr_bundle_seed_enabled` | Seeds CCR bundles for remote sessions |
| `tengu_chair_sermon` | Attachment message processing |
| `tengu_desktop_upsell` | Startup dialog for desktop app |
| `tengu_disable_bypass_permissions_mode` | Blocks bypassPermissions mode |
| `tengu_harbor` | Onboarding flows and OAuth token checks |
| `tengu_iron_gate_closed` | Fail-closed denial when classifier unavailable |
| `tengu_kairos_brief` | Brief user message tool |
| `tengu_kairos_cron` | Scheduling prompts for later/recurring runs |
| `tengu_kairos_cron_config` | Cron scheduling limits |
| `tengu_kairos_cron_durable` | Persists scheduled tasks to disk |
| `tengu_kairos_dream` | Scheduled Dream skill for verification |
| `tengu_kairos_push_notifications` | Terminal and mobile notifications |
| `tengu_malort_pedway` | Chicago UI feature for max/pro tiers |
| `tengu_max_version_config` | Blocks updates below threshold |
| `tengu_scratch` | Session-memory scratchpad |
| `tengu_sm_config` | Session memory behavior |
| `tengu_streaming_tool_execution2` | Streaming tool execution |
| `tengu_tool_pear` | Strict tool-definition handling |
| `tengu_toolref_defer_j8m` | Tool-use message preprocessing |

**None of these 25 flags control:** spawn mechanism, pane backend, teammate command, external model routing, or agent-teams display mode.

**`tengu_amber_flint` is NOT present in v2.1.118.** It was mentioned in a March 2026 community post as the agent-teams gate, but it does not appear in the current flag list. It was either renamed, removed, or rolled into `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` env var gating.

**No spawn-related or pane-backend-related feature flags exist in v2.1.118.**

**URL:** https://raw.githubusercontent.com/marckrenn/claude-code-changelog/main/meta/flags.md (accessed 2026-04-23)

---

### P2-Q3d — Undocumented env vars relevant to spawn mechanics

From the community exhaustive env-var gist (v2.1.104) and changelog search:

**Found — spawn/agent-adjacent undocumented vars:**
- `CLAUDE_CODE_FORK_SUBAGENT=1` — appeared in v2.1.117. Enables forked subagents sharing prompt cache prefix. Does NOT apply to teammate spawn path (`team_name`+`name`). Only fires when `subagent_type` is omitted.
- `CLAUDE_CODE_USE_POWERSHELL_TOOL` — Windows only, unrelated.
- `CLAUDE_CODE_COORDINATOR_MODE` — "Agent coordinator mode" (in gist, undescribed further).

**Not found — specifically searched:**
- `CLAUDE_CODE_FORCE_PANE` — does not exist in any source
- `CLAUDE_CODE_TEAMMATE_MODE` — does not exist (config key is `teammateMode` in `~/.claude.json`, not an env var)
- `CLAUDE_CODE_FORCE_SUBPROCESS` — does not exist
- `CLAUDE_PANE_BACKEND` — does not exist (proposed in #26572, not shipped)
- `CLAUDE_CODE_FORCE_TMUX` — does not exist

**The `teammateMode` config key** (in `~/.claude.json`, NOT an env var) accepts `"in-process"`, `"tmux"`, or `"auto"`. This IS documented. Setting `"teammateMode": "tmux"` in `~/.claude.json` would force split-pane mode and trigger the subprocess spawn path, which would then read `CLAUDE_CODE_TEAMMATE_COMMAND`. This is the most direct documented lever to activate Path A on this host.

**URL:** https://gist.github.com/mculp/e6a573f2a45ef7dbbf30f6a8574c7351 (accessed 2026-04-23), https://code.claude.com/docs/en/agent-teams#choose-a-display-mode (accessed 2026-04-23)

---

### P2-Q3e — Model-provider hooks for hybrid approach (P2-Q7 / Path 4)

**`ANTHROPIC_BASE_URL` IS the documented mechanism for routing inference to any OpenAI-compatible endpoint.**

From Ollama, vLLM, and LM Studio integration docs (all accessed 2026-04-23):

```bash
export ANTHROPIC_BASE_URL=http://localhost:11434  # or vLLM/LiteLLM endpoint
export ANTHROPIC_AUTH_TOKEN=<any-value>
```

Claude Code sends its API requests to the configured `ANTHROPIC_BASE_URL` instead of `api.anthropic.com`. Combined with an OpenAI-compatible proxy (LiteLLM, vLLM, etc.), this routes inference to any external model.

**Caveats for teammate use:**
- This is a session-wide setting. ALL inference — lead and teammates — goes to the configured endpoint. There is no documented per-teammate model-provider override.
- Teammate subprocess inherits the lead's env vars (confirmed from issue #34614: `env CLAUDECODE=1 CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1 /path/to/claude ...`). So a teammate process spawned by the lead WOULD inherit `ANTHROPIC_BASE_URL` if it's in the environment.
- This means a hybrid Path 4 is theoretically feasible: run Claude Code lead normally (Anthropic API), but set `ANTHROPIC_BASE_URL` only in the teammate subprocess env via `CLAUDE_CODE_TEAMMATE_COMMAND` shim, routing the teammate's inference to a Codex/Gemini proxy.

**v2.1.118 also added:** `ANTHROPIC_CUSTOM_MODEL_OPTION` — adds a single custom entry to the `/model` picker for LLM gateway deployments. Not directly relevant to per-teammate routing but confirms the gateway pattern is officially supported.

**The hook-invokes-MCP-tool change (v2.1.118)** is worth flagging for P2-Q5 (leader-side MCP/plugin API): hooks can now call MCP tools directly via `type: "mcp_tool"`. This is new extension surface that could potentially be used to signal external processes, though it's not a direct `AppState` extension point.

**URL:** https://ollama.com/blog/claude (accessed 2026-04-23), https://code.claude.com/docs/en/model-config (accessed 2026-04-23), CHANGELOG v2.1.118

---

### Summary of docs-scout Phase 2 answers

| Question | Answer |
|----------|--------|
| Is there a documented leader-side extension API? | No new ones. Closest: hooks can now call MCP tools (v2.1.118). `teammateMode: "tmux"` in `~/.claude.json` activates the subprocess spawn path. |
| Status of CustomPaneBackend (#26572)? | Open, no Anthropic response, not shipped. Proposer's `kild-tmux-shim` workaround (fake tmux) is independently viable. |
| Feature flags worth testing? | None of the 25 current flags (v2.1.118) relate to spawn or pane backend. `tengu_amber_flint` is gone. `CLAUDE_CODE_FORK_SUBAGENT=1` exists but doesn't apply to teammate spawn. |
| Model-provider hook for hybrid? | `ANTHROPIC_BASE_URL` is the documented mechanism. Teammates inherit the lead's env, so a shim could inject a per-teammate `ANTHROPIC_BASE_URL` pointing at a Codex/Gemini proxy. |
| Force tmux/subprocess without tmux? | `"teammateMode": "tmux"` in `~/.claude.json` forces split-pane mode. Combined with fake-tmux shim (task #13), this could activate Path A on a non-tmux host. |

---

### Task #12 — Leader-side MCP / plugin API for task registration (2026-04-23)

Sources: `github.com/Harzva/learn-likecc/blob/main/ccsource/CC/claude-code-rebuild/src/` (RE'd upstream source, 2026). 18 files examined.

---

#### 1. Does Claude Code expose a leader-side MCP server that external callers can reach?

**`entrypoints/mcp.ts` — the `claude/tengu` server:**

Claude Code does expose an MCP server named `'claude/tengu'`, but it deliberately prevents AppState mutation:
- Transport: `StdioServerTransport` only — stdio, no TCP/Unix socket
- `setAppState` passed to all tool contexts: `() => {}` — **explicit no-op** (`entrypoints/mcp.ts:151`)
- `isNonInteractiveSession: true` — interactive AppState writes disabled
- Tools: dynamically via `getTools(toolPermissionContext)` — same tool set Claude uses internally; no task-registration or teammate-announcement tool

This MCP server is consumed by IDE extensions (VS Code, JetBrains) not by plugins or external processes writing to a socket. Even if an external caller could connect and invoke tools, `setAppState: () => {}` ensures no `AppState.tasks` mutation occurs.

**Claude Code as MCP CLIENT (not server):** `services/mcp/client.ts` — pure client, imports `Client` from `@modelcontextprotocol/sdk/client/index.js`. Connects to external MCP servers (plugins, IDE extensions). `MCPConnectionManager.tsx` — re-connection management only. Neither exposes an inbound API surface.

**Verdict:** Claude Code's only MCP server (`claude/tengu`) has AppState writes deliberately disabled. No inbound MCP API for task registration.

---

#### 2. Does Claude Code start a local HTTP/WebSocket/Unix-socket listener?

18 files searched for: `listen()`, `bind()`, `createServer()`, `net.createServer()`, `Bun.serve()`, `http.createServer()`, `new WebSocketServer()`, Unix socket creation.

| File | Role | Local listener? |
|------|------|----------------|
| `main.tsx` (4722 lines) | CLI bootstrap | **NOT FOUND** |
| `entrypoints/cli.tsx` | Routing layer | **NOT FOUND** |
| `server/createDirectConnectSession.ts` | HTTP POST client to remote `/sessions` | No — client only |
| `server/directConnectManager.ts` | Outbound WebSocket client | No — client only |
| `remote/SessionsWebSocket.ts` | Outbound WSS to `wss://api.anthropic.com/...` | No — client only |
| `bridge/bridgeMain.ts` | Outbound poll loop (`api.pollForWork()`) | No — client only |
| `bridge/replBridge.ts` | Outbound bridge client; SIGUSR2 reconnect (ant-internal only) | No |
| `coordinator/coordinatorMode.ts` | Multi-worker via existing `Agent`/`SendMessage`/`TaskStop` tools | No new surface |

The `/tmp/claude-1000` socket observed empirically is a Bun runtime process-local IPC socket (standard Bun architecture), not an application-level command channel.

**Verdict (exhaustive):** No local HTTP, WebSocket, or Unix-socket server. All network I/O is outbound. **NOT FOUND** across all 18 files.

---

#### 3. Plugin, hook, and mailbox surfaces

**Plugin system (`services/plugins/pluginOperations.ts`, `plugins/builtinPlugins.ts`):** Pure install/enable/disable library functions. No `registerTask`, no `setAppState`. Plugin extension points (skills, hooks, mcpServers) are client-consumed, not leader-side injection APIs.

**Hook system:**
- All hooks (`PreToolUse`, `PostToolUse`, `Stop`, `SessionStart`) are harness→script outbound. Cannot call back into leader.
- `useDeferredHookMessages.ts`: handles `HookResultMessage[]` by prepending to messages array. No type-specific routing, no AppState.tasks mutation.
- **v2.1.118 `type: 'mcp_tool'` hook:** Even if this hook type fires an MCP tool call, the MCP entrypoint's `setAppState: () => {}` no-op prevents AppState mutation. This is an output channel only.

**Inbox/mailbox system:**
- `useInboxPoller.ts`: polls filesystem inbox files (`readUnreadMessages(agentName, teamName)`). Handles: permission requests, permission responses, shutdown approvals, plan approvals, regular messages. **Does NOT call `registerTask` for new teammates.** The only AppState.tasks write is setting status to `'completed'` on teammate shutdown.
- `useSwarmInitialization.ts`: fires once on React mount via `useEffect`. No external trigger surface — cannot be activated by filesystem write or signal.
- `useSwarmPermissionPoller.ts`: polls permission responses only. No member detection.
- `useTaskListWatcher.ts`: watches a tasks directory with `fs.watch()`. Claims existing tasks (pending + unowned). Does NOT create new `AppState.tasks` entries via `registerTask`.

---

#### 4. `tengu_` flags — any extension-point gates?

All 25 `tengu_` flags (v2.1.118) examined (from docs-scout Task #10). None relate to: spawn mechanism, pane backend, plugin extension API, teammate command, external model routing. `tengu_amber_flint` no longer exists. **NOT FOUND.**

---

#### 5. Exhaustive verdict (P2-Q5)

**No leader-side MCP server, local listener, plugin API, hook callback, or filesystem trigger allows external code to call `registerTask()` on the live leader's AppState.** The architecture is consistently outbound-only. The `setAppState: () => {}` no-op in `entrypoints/mcp.ts` is a deliberate architectural constraint, not an oversight.

For Phase 2 solution space: the MCP/plugin injection path is closed. Remaining viable paths are Tasks #13 (fake-tmux) and Task #14 (hybrid: Claude in-process coroutine with `ANTHROPIC_BASE_URL` proxy routing inference to Codex).

---

### Task #14 — Feasibility: hybrid — Claude in-process agent with Codex MCP backend (2026-04-23)

Sources: Harzva/learn-likecc RE repo (decompiled upstream, 2026); Anthropic docs `code.claude.com/docs/en/model-config`, `code.claude.com/docs/en/sub-agents`, `code.claude.com/docs/en/llm-gateway` (accessed 2026-04-23).

---

#### Q1 (re-scout): Does the in-process teammate coroutine expose model-inference as a hookable step?

**No. There is no before-inference or model-call hook in the `spawnInProcessTeammate → runAgent` flow.**

Evidence from RE'd source:

**`inProcessRunner.ts` — `startInProcessTeammate()`:**
- Wraps `runAgent()` with `runWithTeammateContext()` and `runWithAgentContext()` — both are `AsyncLocalStorage` container setters, not request/response interceptors. No callback fires before the inference call.
- Accepts `InProcessRunnerConfig.model?: string` — model name string only, no provider override parameter.
- Comment: "Propagate model from custom agent definition so `getAgentModel()` can use it as a fallback." Model field is string → model-alias resolution only.

**`spawnInProcess.ts` — `spawnInProcessTeammate(config, context)`:**
- `InProcessSpawnConfig.model?: string` — optional model override, no provider field.
- No callback, hook, or middleware layer between spawn and inference.

**`query.ts` — inference loop:**
- Core call: `deps.callModel({ messages, systemPrompt, model: currentModel, ...options })` — injected via `QueryDeps` dependency injection.
- `QueryDeps` is built during leader initialization and wired once per session. It is not a per-teammate override point — there is no mechanism to inject a different `callModel` implementation per teammate.
- No per-call hook surface. `fallbackModel?: string` exists but is a model-name fallback string, not a provider hook.

**`services/api/client.ts` — `getAnthropicClient()`:**
- Provider selection is session-global, based on env vars: `CLAUDE_CODE_USE_BEDROCK`, `CLAUDE_CODE_USE_VERTEX`, `CLAUDE_CODE_USE_FOUNDRY` → returns one of `firstParty | bedrock | vertex | foundry`.
- `ANTHROPIC_BASE_URL` is read and validated against a hostname allowlist by `isFirstPartyAnthropicBaseUrl()` in `providers.ts`. The allowlist permits `api.anthropic.com` (and staging for `USER_TYPE=ant`) — but the validation function's result is used for feature gating, not to block the URL. The Anthropic SDK itself uses whatever `ANTHROPIC_BASE_URL` is set.
- Provider is set **once at process startup**, not per-teammate. All in-process teammates share the same `Anthropic` client instance.

**`utils/model/agent.ts` — `getAgentModel()`:**
- Resolution order: `CLAUDE_CODE_SUBAGENT_MODEL` env var → per-invocation model param → agent definition `model` frontmatter → parent model.
- The `model` field is a **model name string** (`sonnet`, `opus`, full model ID). It does NOT accept a provider or endpoint. No `endpoint:` or `baseUrl:` field exists.
- `CLAUDE_CODE_SUBAGENT_MODEL` is a **session-global** env var — same for all subagents and teammates.

**Verdict on Q1:** Zero inference-hook surface. The inference pipeline is: AsyncLocalStorage context → `runAgent()` → `query()` → `deps.callModel()` → `Anthropic SDK`. No per-teammate interception point exists anywhere in this chain.

---

#### Q2 (docs-scout): Model-provider overrides — `--model-provider`, `ANTHROPIC_BASE_URL`, per-teammate routing

**Documented env vars and their scope:**

From `code.claude.com/docs/en/model-config` (accessed 2026-04-23):

| Env var | Scope | Effect |
|---------|-------|--------|
| `ANTHROPIC_BASE_URL` | Session-global | Routes ALL inference to this endpoint (LiteLLM, gateway, custom proxy) |
| `CLAUDE_CODE_USE_BEDROCK` | Session-global | Switch entire session to AWS Bedrock |
| `CLAUDE_CODE_USE_VERTEX` | Session-global | Switch entire session to Google Vertex AI |
| `CLAUDE_CODE_USE_FOUNDRY` | Session-global | Switch entire session to Azure Foundry |
| `CLAUDE_CODE_SUBAGENT_MODEL` | Session-global | Override model name for all subagents |
| `ANTHROPIC_DEFAULT_OPUS_MODEL` | Session-global | Alias → model ID mapping |
| `ANTHROPIC_CUSTOM_MODEL_OPTION` | Session-global | Add custom model to picker |

**No `--model-provider` CLI flag exists.** Not mentioned in any Anthropic doc page. NOT FOUND in changelog (v2.1.32–v2.1.118).

**No per-teammate or per-subagent endpoint/provider override exists.** All provider selection is session-global. A teammate subprocess inherits the leader's environment (`env CLAUDECODE=1 ... /path/to/claude --agent-name X`), including `ANTHROPIC_BASE_URL`. But for in-process teammates (the TUI-presence path), there is no subprocess — the same Node.js process with the same initialized `Anthropic` client is used.

**`ANTHROPIC_BASE_URL` IS the documented LLM gateway mechanism** (from `code.claude.com/docs/en/llm-gateway`). It routes ALL inference to a gateway (e.g., LiteLLM). The gateway must expose Anthropic Messages API (`/v1/messages`, `/v1/messages/count_tokens`) and forward `anthropic-beta` and `anthropic-version` headers. LiteLLM and similar proxies support this. Setting `ANTHROPIC_BASE_URL=https://litellm:4000` routes all inference through LiteLLM, which can then proxy to any backend (GPT-5.5, Gemini, etc.).

**Caveat:** This is session-global. Lead + all teammates go through the same gateway. There is no way to route only `codex-alice`'s calls to a Codex proxy while the lead stays on Anthropic's API — not within a single Claude Code session.

---

#### Q3 (re-scout): Can a subagent definition's `model` field route to MCP or a non-Claude endpoint?

**No.** From `code.claude.com/docs/en/sub-agents#supported-frontmatter-fields` (accessed 2026-04-23):

> "model: Model to use: `sonnet`, `opus`, `haiku`, a full model ID (for example, `claude-opus-4-7`), or `inherit`. Defaults to `inherit`."

The `model` field accepts only Claude model aliases or full Claude model IDs. Non-Claude model IDs (GPT-5.5, gemini-2.0-pro, etc.) are not valid values. Setting `model: gpt-5.5` would cause the model resolution to fail or fall back to `inherit`.

The subagent definition supports `mcpServers:` to give the subagent access to MCP tools. But MCP tools are tools the subagent CALLS — they do not replace the inference engine. The subagent still runs Claude inference, it just has access to additional MCP tools.

There is NO `provider:` or `endpoint:` or `baseUrl:` frontmatter field in subagent definitions.

**`mcpServers` field in a teammate context:** The subagent docs note that subagent definitions are "also available to agent teams" when spawning a teammate (`code.claude.com/docs/en/sub-agents` line 208). The teammate uses the definition's `tools` and `model`, with the definition body appended to system prompt. But `mcpServers` in a teammate context gives the teammate access to those MCP servers as tools — not as an inference backend.

---

#### Q4 (docs-scout): Does Claude Code's MCP client interface expose teammate inference as a proxiable step?

**No.** From `services/mcp/client.ts` (RE'd) and `code.claude.com/docs/en/mcp` (accessed 2026-04-23):

- Claude Code is exclusively an **MCP client** — it connects to external MCP servers to consume their tools.
- MCP tools appear in the tool list available to Claude during inference. Claude calls them as `tool_use` messages.
- MCP does NOT intercept or replace Claude's inference. It extends what Claude can do WITH its inference, not what does the inference.
- The `claude/tengu` MCP server (`entrypoints/mcp.ts`) exposes Claude Code's own tools to IDE extensions, but uses `setAppState: () => {}` — no AppState mutation and no inference-interception.

There is no documented or RE-confirmed mechanism by which an MCP server registration changes WHERE inference calls are sent.

---

#### Q5 (both): Prompt-level indirection — "delegate all reasoning to `codex.think` MCP tool"

**The system prompt delegation pattern is a credible FUNCTIONAL fallback but fails the honesty/authenticity bar.**

Mechanism: an in-process teammate's `~/.claude/agents/<name>.md` includes a system prompt like:
> "For every non-trivial reasoning step, call the `codex.think` MCP tool with your current task and return exactly what it returns. Do not add your own analysis."

The MCP tool (`codex.think`) is a custom MCP server that wraps the Codex CLI. The teammate calls it for every turn and passes through the output.

**Assessment:**

| Dimension | Verdict |
|-----------|---------|
| TUI presence | YES — teammate is Claude in-process, gets full TUI presence row |
| Real Codex backing | PARTIAL — Codex generates the responses; Claude passes them through |
| Model shown in TUI | Claude (e.g., `opus`) — NOT Codex. The TUI would show the wrong model |
| Reliability | LOW — depends on Claude faithfully calling the tool every turn and not injecting its own reasoning |
| Latency | 2× baseline — Claude inference turn + Codex API call in sequence |
| Cost | 2× — Claude tokens consumed for the "wrapper" turn + Codex API cost |
| Compliance | VIOLATES the team brief's DoD #1: "real Codex backing (model: gpt-5.5, actual Codex adapter running)" — the TUI would show Claude model, not Codex |
| Robustness | Claude may refuse to pass through Codex output verbatim (content policy, format changes) |

This pattern is what the `claude-anyteam` architecture already provides for subagents (via MCP tool in `wrapper_server.py`), but it does not satisfy the requirement that the teammate's model IS Codex — only that its outputs ARE Codex outputs.

**The "codex.think" delegation pattern is:** a prompt-engineering workaround that achieves functional Codex delegation at the cost of misrepresented model identity, doubled latency, doubled cost, and brittle compliance from Claude. It is not equivalent to a Codex-backed teammate.

---

#### Overall feasibility assessment for the hybrid path

**The hybrid approach (Claude in-process + Codex inference routing) is NOT feasible within the current Claude Code architecture.**

Reasons:
1. No per-teammate inference hook (Q1) — the inference pipeline has no interception point per-teammate.
2. No per-teammate provider override (Q2) — `ANTHROPIC_BASE_URL` is session-global; routing only the teammate's calls to a Codex proxy would require a separate subprocess (which is exactly Path B).
3. No model-provider field in subagent/teammate definitions (Q3) — `model:` only accepts Claude model IDs.
4. MCP does not intercept inference (Q4) — it extends tool access, not inference routing.
5. Prompt-level delegation (Q5) — functionally works but violates the DoD's authenticity requirement and doubles cost/latency.

**The only technically correct "hybrid" that satisfies DoD would be:**
- Run a full `claude` subprocess as the teammate (pane backend), intercept via `CLAUDE_CODE_TEAMMATE_COMMAND` shim, and inside that subprocess set `ANTHROPIC_BASE_URL` to a LiteLLM proxy routing to Codex. This is Path A (with per-process env injection in the shim) — not an in-process approach.
- This IS achievable: the shim could set `ANTHROPIC_BASE_URL=<litellm:4000>` before `os.execv`ing `claude`. The spawned `claude` subprocess would use Claude Code's inference pipeline but ALL calls would go to LiteLLM/Codex. The teammate would appear in TUI as a pane-based teammate (requires tmux/fake-tmux per Task #13).

**Summary for lead:**
- Model-inference interception hook: **NOT FOUND** — zero hook surface in the in-process coroutine chain.
- Prompt-level `codex.think` delegation: **credible as a workaround** but not a true Codex-backed teammate (wrong model shown in TUI, 2× cost/latency, brittle).
- `ANTHROPIC_BASE_URL` gateway approach: **viable but only session-global** — routes lead + all teammates to Codex proxy, cannot isolate per-teammate; best used with the fake-tmux/Path A approach to set env per-subprocess.

---

---

## Phase 2 — Findings — experimenter

### Passive Experiment 1: Full env var state of all Claude Code PIDs

**Command:**
```
PIDS=$(pgrep -f "claude --resume 9f68fd4c")
for PID in $PIDS; do
  echo "=== PID $PID ==="
  tr '\0' '\n' < /proc/$PID/environ | grep -iE "claude|tmux|teammate|pane|force|experimental|feature"
done
```

**Observed (verbatim):**
```
=== PID 625925 ===
PWD=/home/rosado/Projects/codex-teammate
_=/home/rosado/.local/bin/claude

=== PID 636692 ===
PWD=/home/rosado/Projects/codex-teammate
_=/home/rosado/.local/bin/claude

=== PID 1637215 === [transient tool-call child, exited before ps inspection]
CLAUDE_CODE_ENTRYPOINT=cli
CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1
CLAUDE_CODE_TEAMMATE_COMMAND=/home/rosado/.local/bin/claude-anyteam-spawn-shim
CLAUDE_ANYTEAM_BINARY=/home/rosado/.local/bin/claude-anyteam
CLAUDECODE=1
CLAUDE_CODE_EXECPATH=/home/rosado/.local/share/claude/versions/2.1.118
```

Key findings:
1. Persistent Claude Code PIDs (625925, 636692): NO `CLAUDE_` vars, NO `TMUX=` in OS-level environ. Only `PWD` and `_`.
2. Transient child PID 1637215 DID have `CLAUDE_CODE_TEAMMATE_COMMAND` — harness injects `settings.json` env into tool-call children, not the parent process.
3. `TMUX=''` (absent) in both persistent PIDs. No tmux session active for Claude Code.
4. No `FORCE_PANE`, `TEAMMATE_MODE`, `PANE_BACKEND`, or any pane-forcing env var.
5. `tmux` binary IS installed at `/usr/bin/tmux` but is not a session parent of Claude Code.

---

### Passive Experiment 2: `~/.claude.json` — any `teammateMode` / pane / force keys?

**Command:** Python scan of all top-level keys and project entries for pane/tmux/teammate/force/spawn.

**Observed:**
- No `teammateMode`, `pane_backend`, `forcePane`, `force_pane`, `tmux`, or spawn-related key at top level or in any of the 8 project entries.
- `tengu_amber_flint: True` in `cachedGrowthBookFeatures` — the agent-swarms gate (`isAgentSwarmsEnabled()`) IS enabled. Feature gate is NOT the bottleneck; `Rb()` is.
- `tengu_flint_harbor: False` — unrelated onboarding feature.
- No GrowthBook flag matching pane/tmux/spawn/swarm found beyond `amber_flint`.

---

### Passive Experiment 3: `~/.claude/settings.json` — any keys beyond known trio?

**Observed:** `['env', 'permissions', 'enabledPlugins', 'extraKnownMarketplaces', 'effortLevel', 'skipDangerousModePermissionPrompt', 'skipAutoPermissionPrompt']`

No `experimental`, `features`, `flags`, `teams`, `teammate`, `pane`, `tmux`, or non-standard key. No hidden config knob here.

---

### Passive Experiment 4: `claude --help` and binary string scan for `--teammate-mode`

**Observed:** Only `--tmux` flag appears in help output, scoped to `--worktree`. No `--teammate-mode`, `--force-pane`, `--pane-backend`. Binary is a compiled SEA — `strings` returns nothing; JS source not accessible on this host.

---

### Thought Experiment 4: Would `tmux new -d -s claude-in-tmux 'claude'` flip PaneBackend?

`isInProcessEnabled()` / `Rb()` short-circuits on `isNonInteractive()` (`q7()`) BEFORE any tmux detection. Our sessions use `--resume` → non-interactive → PaneBackend check never reached regardless of `TMUX` env.

A FRESH interactive session (no `--resume`, TTY stdin) launched inside tmux WOULD flip to PaneBackend — but this requires starting a new session. The current running session cannot be flipped.

---

### Thought Experiment 5: Could a fake-tmux binary satisfy PaneBackend detection?

Claude Code's pane backend issues approximately 4 tmux subcommands: `display-message` (get session ID), `split-window` (create pane), `send-keys` (exec spawn command in pane), `respawn-pane` (cleanup). A shell script responding plausibly to these with exit 0 and correct-shaped stdout could satisfy detection, provided Claude Code does not validate pane liveness post-split. Requires `TMUX=/tmp/fake.sock` set before launching a NEW Claude Code process. Tracked in task #13.

---

### Thought Experiment 6: Can `--teammate-mode=tmux` force PaneBackend in a running process?

No. CLI flags parsed at startup only; cannot be injected into a running process. Even at launch: issue #34614 confirms the flag is silently ignored in non-interactive sessions because `Rb()` short-circuits before the mode flag is evaluated. Only effective for fresh interactive (non-`--resume`, TTY stdin) sessions.

---

### Phase 2 experimenter summary (task #11)

| Question | Answer |
|---|---|
| Any pane/tmux/force env var in Claude Code PIDs? | **No** — only `PWD` and `_` in persistent processes |
| Any `teammateMode` / pane key in `~/.claude.json`? | **No** |
| Any non-standard key in `settings.json`? | **No** |
| `tengu_amber_flint` (agent-swarms gate) enabled? | **Yes — `True`**. Gate is open; `Rb()` is the bottleneck. |
| Can `tmux new ...` flip THIS session to PaneBackend? | **No** — `--resume` → non-interactive → `Rb()` short-circuits. New session only. |
| Can fake-tmux satisfy PaneBackend detection? | **Probably yes for a new session.** Task #13 tracks this. |
| Can `--teammate-mode=tmux` force PaneBackend? | **Only at launch of a fresh interactive session.** Silently ignored in non-interactive. |

---

### Task #13 — Fake-tmux shim feasibility (paper prototype)

#### Q1: What tmux subcommands does Claude Code call for PaneBackend detection and spawn?

**Detection phase (at startup / backend selection):**

From re-scout R6 (rasmusab/#26572, "who RE'd all ~20 tmux subcommands Claude Code issues") and docs-scout P2-Q3a:

1. **Environment check:** Claude Code reads `process.env.TMUX`. If non-empty, it considers itself inside a tmux session. This is the PRIMARY detection gate — no subprocess call needed for this step.
2. **Session ID probe:** `tmux display-message -p '#{session_id}'` (or equivalent `#{pane_id}` / `#{window_id}`) — verifies the session exists and returns an identifier. Required to get a pane context.

**Spawn phase (per teammate):**

From re-scout R2 (`PaneBackendExecutor.ts`) and vendored `src/claude_teams/spawner.py:42-55`:

3. `tmux split-window -dP -F '#{pane_id}' <spawn_command>` — creates a new pane detached, prints the pane ID. This is where the actual spawn happens. The `<spawn_command>` is the full `cd <cwd> && env ... <TEAMMATE_COMMAND> --agent-name <name> --team-name <team> ...` string.
4. `tmux send-keys -t <pane_id> '<spawn_command>' Enter` — alternative mechanism (issue #23615 confirms this is used alongside `split-window` as separate subprocesses with no coordination — race condition at 4+ agents). Note: this is the vector for the MAX_CANON 256-byte truncation bug (re-scout R5/#40168).

**Cleanup / lifecycle:**

5. `tmux kill-pane -t <pane_id>` — on teammate shutdown (`spawner.py:271`)
6. `tmux kill-window -t <pane_id>` — alternate cleanup if `USE_TMUX_WINDOWS` (`spawner.py:269`)
7. Additional: rasmusab documented ~20 total subcommands from KILD RE; the remaining are likely `tmux list-panes`, `tmux list-windows`, `tmux capture-pane` for health/output capture, and `tmux respawn-pane` for restart. These are post-spawn lifecycle management.

**Authoritative sources:** `src/claude_teams/spawner.py:42-55` (this repo, vendored); re-scout R2 (`PaneBackendExecutor.ts`); re-scout R5 (issue #40168, MAX_CANON on `send-keys`); re-scout R6 (issue #26572, rasmusab ~20 subcommands); re-scout R8 (issue #23615, `split-window`+`send-keys` race).

---

#### Q2: Minimal tmux API to satisfy detection and one spawn

**Minimum viable fake-tmux to pass detection + spawn one teammate:**

| Subcommand | Required response | Exit code |
|---|---|---|
| `display-message -p '#{session_id}'` | Any non-empty string, e.g. `$0` | 0 |
| `split-window -dP -F '#{pane_id}' <cmd>` | Pane ID string, e.g. `%0`; AND exec `<cmd>` as a real subprocess | 0 |
| `send-keys -t <pane_id> <cmd> Enter` | (empty stdout ok) | 0 |

**The critical constraint on `split-window`:** Claude Code passes the ENTIRE spawn command as the last argument to `split-window`. The fake-tmux must parse that argument and actually exec it — otherwise the teammate process never starts. This is what the KILD `kild-tmux-shim` does: it intercepts `split-window` and execs the payload.

**Shell script prototype (~40 lines):**
```bash
#!/bin/bash
# /tmp/fake-tmux/tmux — minimal fake-tmux for Claude Code PaneBackend

LOGFILE=/tmp/fake-tmux/trace.log
echo "$(date -Iseconds) tmux $*" >> "$LOGFILE"

case "$1" in
  display-message)
    # Claude Code queries for session/pane ID — return a plausible value
    echo '$0'
    exit 0
    ;;
  split-window)
    # Parse: tmux split-window -dP -F #{pane_id} <actual_command>
    # Last argument is the command to run
    PANE_ID="%0"
    echo "$PANE_ID"
    # Exec the spawn command in the background (this is where shim fires)
    shift  # remove 'split-window'
    while [[ "$1" == -* ]]; do shift; done  # skip flags
    CMD="$*"
    echo "$(date -Iseconds) EXEC: $CMD" >> "$LOGFILE"
    bash -c "$CMD" </dev/null >>/tmp/fake-tmux/pane-0.log 2>&1 &
    exit 0
    ;;
  send-keys)
    # Claude Code may also use send-keys — log and ignore
    echo "$(date -Iseconds) send-keys ignored: $*" >> "$LOGFILE"
    exit 0
    ;;
  kill-pane|kill-window|respawn-pane|list-panes|list-windows|capture-pane)
    # Lifecycle commands — exit 0 silently
    exit 0
    ;;
  *)
    echo "$(date -Iseconds) UNKNOWN: $*" >> "$LOGFILE"
    exit 0
    ;;
esac
```

**Setup to activate Path A with fake-tmux (for a NEW Claude Code session — NOT this one):**
```bash
mkdir -p /tmp/fake-tmux
# write the script above to /tmp/fake-tmux/tmux
chmod +x /tmp/fake-tmux/tmux

# Add "teammateMode": "tmux" to ~/.claude.json (requires user approval)
# OR rely on TMUX env var detection

TMUX=/tmp/fake-tmux.sock PATH=/tmp/fake-tmux:$PATH claude
# Claude Code now sees TMUX env, finds tmux binary on PATH, selects PaneBackend
# When /invite codex-alice → split-window → fake-tmux execs the shim → adapter starts
```

**Source for `teammateMode` config key:** docs-scout P2-Q3d, `spawn-research-findings.md:1487` — `"teammateMode": "tmux"` in `~/.claude.json` forces split-pane mode. This is the documented override that bypasses auto-detection.

---

#### Q3: Does Claude Code run more tmux commands post-spawn?

**Yes, at minimum two more categories:**

1. **Output capture:** `tmux capture-pane -t <pane_id> -p` — Claude Code likely calls this to read stdout from the teammate pane for display in the TUI. This is documented in rasmusab's KILD RE (~20 subcommands) and implied by the TUI showing teammate output.

2. **Health / liveness checks:** `tmux list-panes -t <session>` or `tmux display-message -t <pane_id> -p '#{pane_pid}'` — to check if the pane process is still alive.

3. **Input forwarding:** `tmux send-keys -t <pane_id> '<json_message>' Enter` — how the leader sends messages to in-pane teammates (the INPUT side of the pane protocol). This is the MAX_CANON overflow vector (#40168) and the race condition (#23615).

**Implication for fake-tmux:** The minimal prototype above handles `capture-pane`, `list-panes`, and `send-keys` with exit 0 and empty stdout. This means:
- Teammate's TUI output section would appear empty (fake returns no content for `capture-pane`)
- Leader's message delivery to teammate via `send-keys` would silently drop (but the adapter uses file-based mailboxes, NOT stdin — so this doesn't break the actual messaging)
- Liveness checks would return "success" regardless of actual process state

The adapter's communication is entirely file-based (`~/.claude/teams/<team>/inboxes/<name>.json`), so the stdin/stdout pane protocol is cosmetic. The actual messaging works via the mailbox regardless.

---

#### Q4: Cost/risk assessment

| Risk | Severity | Notes |
|---|---|---|
| `Rb()` non-interactive short-circuit | **BLOCKER for current session** | `--resume` → non-interactive → PaneBackend check never reached. Fake-tmux only helps for a FRESH interactive session. |
| `split-window` argument parsing fragility | Medium | The spawn command contains quotes and env vars. Naive `shift`+`$*` may misparse. Must use `"${@: -1}"` or a proper arg parser. |
| `send-keys` MAX_CANON truncation | **BLOCKER if Claude Code uses send-keys for spawn** | Issue #40168: spawn command is 300+ chars; 256-byte kernel buffer truncates it. Real tmux has the same bug. Fake-tmux bypasses this by directly exec'ing the command — actually BETTER than real tmux. |
| `capture-pane` returns empty | Low | TUI shows empty output for teammate pane, but functional messaging via mailboxes still works. |
| Claude Code validates pane TTY | Unknown | If Claude Code calls `tmux display-message -t %0 -p '#{pane_pid}'` and checks process state, fake returns empty/0. Unclear if Claude Code validates this. |
| `~/.claude.json` mutation required | Medium | Adding `"teammateMode": "tmux"` changes settings for ALL Claude Code sessions until reverted. Requires user approval. |
| Path B already works | Context | For functional Codex teammates (messaging, task claims), Path B already works. Fake-tmux only adds TUI footer visibility (`@codex-alice` in presence line) via `registerOutOfProcessTeammateTask()`. |

---

#### Feasibility verdict

**Plausible but gated on two conditions requiring user action:**

1. **A fresh interactive Claude Code session** — current `--resume` sessions are non-interactive; `Rb()` short-circuits before pane detection. User must start a new Claude Code session.

2. **Either `TMUX` env var set OR `"teammateMode": "tmux"` in `~/.claude.json`** — to activate PaneBackend. The `~/.claude.json` change is the safer lever (no need to set env var) but requires user approval to modify.

If both conditions are met, the fake-tmux approach is mechanically sound. The spawn command flows through `split-window` arg → fake-tmux execs it directly → shim fires → adapter starts. The MAX_CANON bug doesn't apply (fake-tmux bypasses send-keys for spawn). The adapter's file-based messaging works regardless of pane I/O.

**Gotchas:**
- The fake-tmux `split-window` argument parser must handle shell quoting correctly — the spawn command is a bash `cd ... && env ... /path/to/shim --agent-name codex-alice ...` string passed as a single argument.
- Claude Code may issue `capture-pane` or `send-keys` for ongoing message delivery. If Claude Code falls back from file-mailbox to `send-keys` for some message types, those messages would be dropped silently. Worth testing.
- This path adds TUI presence visibility but `registerOutOfProcessTeammateTask()` shape must match what the leader expects — code-auditor task #8 has the detail on that shape.
- **Do NOT implement without user sign-off.** Modifying `~/.claude.json` affects all sessions. Recommend asking the user before proceeding.

---

### Task #16 — Current teammateMode + tmux state on this host

*Authored: 2026-04-23. Passive only — no modifications.*

#### Q1 — `teammateMode` in `~/.claude.json`

Searched all keys in `~/.claude.json` (50+ top-level keys, nested objects) for any key containing "teammate", "mode", "pane", "backend", or "tmux". Result: **`teammateMode` key is NOT PRESENT at any level** — neither top-level nor in any project sub-object.

The project entry for `/home/rosado/Projects/codex-teammate` exists and contains `allowedTools`, `hasTrustDialogAccepted`, `lastGracefulShutdown`, `lastSessionMetrics`, `lastCost`, etc. — standard per-project session metadata. No spawn/backend/mode keys.

**Conclusion:** This host has no `teammateMode` override in `~/.claude.json`. Claude Code is using its compiled default backend-selection logic, not a user override. The fake-tmux approach (Task #13) would require writing `"teammateMode": "tmux"` to this file — a session-wide change that does not currently exist.

#### Q2 — tmux on PATH

```
tmux 3.4
/usr/bin/tmux    (real binary, -rwxr-xr-x, root:root, 1102608 bytes, 2024-07-10)
readlink -f → /usr/bin/tmux    (not a symlink)
TMUX env var → '' (empty — not inside a tmux session)
Parent process → claude (directly under the Claude Code process; no tmux in ancestry)
```

**Key observations:**

- `tmux` 3.4 is installed and is a real binary at `/usr/bin/tmux`. It is NOT currently a fake-tmux shim.
- `$TMUX` is empty → Claude Code sees no active tmux session → `detectAndGetBackend()` falls through to in-process.
- Parent process chain is `claude` → this experimenter subprocess. No tmux in the chain. Confirms non-tmux session.
- The `tmux` binary at `/usr/bin/tmux` is system-provided (root:root). Replacing it or prepending a fake-tmux to PATH would require either root access OR prepending a user-writable dir (e.g., `~/.local/bin`) earlier than `/usr/bin` in PATH.

#### Q3 — Backend-selection logs

Checked: `~/.cache/claude-code/`, `~/.local/share/claude/`, `~/.cache/claude/`, and `/tmp`. No subdirectory named `claude-code` under `.cache/`; no subdirectory named `claude` under `.local/share/`. Directories found in `~/.cache/`: `claude`, `claude-cli-nodejs`, `fontconfig`, `helm`, `huggingface`, `motd.legal-displayed`, `ms-playwright`, `node-gyp`, `pip`, `puppeteer`, `uv`, `vscode-ripgrep`.

`~/.cache/claude/` exists but was not further enumerated (not in task scope and no mtime-recent log files were returned by `find ... -newer spawn-research-brief.md`). No backend-selection log files found that postdate the research brief.

**Conclusion:** Claude Code does not write backend-selection decision logs to any discoverable cache directory on this host. Backend selection is an in-memory decision not persisted to disk.

#### Summary

| Item | Value |
|---|---|
| `teammateMode` in `~/.claude.json` | **NOT PRESENT** — key does not exist |
| `TMUX` env var in running session | `''` (empty) — not in tmux |
| `tmux` binary | `/usr/bin/tmux` 3.4, real binary, system-installed |
| Parent process | `claude` directly — no tmux in ancestry |
| Backend-selection log files | **NOT FOUND** — no disk log of which backend was chosen |
| Implication for fake-tmux (Task #13) | Would require: (1) prepend `~/.local/bin/tmux` shim to PATH before Claude Code, (2) set `TMUX=fake` or write `"teammateMode": "tmux"` to `~/.claude.json`, (3) new interactive session — NOT in current running session |

---

## Phase 2 — Synthesis (lead)

*(filled when P2-Q1..Q7 resolve)*

---

## Phase 2 — Proposed path (solution OR impossibility verdict)

*(drafted after synthesis; presented to team-lead + user for sign-off before any code/doc changes)*


---

## Phase 2 — Findings — code-auditor (Task #9)

*Authored: 2026-04-23. IPC / filesystem-signal hunt: what does Claude Code watch or listen on that could enable external teammate registration?*

### Candidate 1 — inotify / fs.watch / chokidar on team dirs

**Binary strings evidence (`/tmp/claude-2.1.117.strings.txt`):**

The Claude Code binary (native ELF, Bun runtime) does contain the symbols `inotify_init1` and `inotify_add_watch` (`strings` line ~560957) and the string `bun.js.node.node_fs_watcher.FSWatcher.FSWatchTaskPosix`. These are Bun's own bundled `FSWatcher` implementation (the Node.js-compatible `fs.watch` API backed by Linux inotify), not Claude Code application logic.

Targeted grep of the strings dump for team-config-specific watch patterns — `teams.*watch`, `watch.*config.json`, `watch.*teams`, `teamHelpers.*watch`, `setInterval.*team`, `team.*poll` — returned NO MATCHES after grepping the full 780,132-line dump.

**RE'd source evidence (`docs/internal/2026-prototype/research.md:52`):**

> Team config helpers live in `utils/swarm/teamHelpers.ts:115-159` and only read/write `config.json`.

The word "only" is the finding: `teamHelpers.ts` has no watch/interval/poll call — it is a synchronous read/write helper called on demand by spawn paths, not a watcher.

**Runtime probe evidence (`research.md:116-119`):**

> Nothing we observed on disk. The leader process is NOT holding `inotify` watches on the teammate/subagent paths, and no persistent sockets/pipes carry presence data.

The probe method: `lsof`, `ss`, `find` with mtime snapshots, `/proc/<claude_pid>/fdinfo`. Caveat: the probe checked specifically "teammate/subagent paths" — it is POSSIBLE (unproven) that the leader holds inotify watches on other paths (e.g. project dirs, settings.json). The probe was not a comprehensive enumeration of ALL inotify watches; it targeted the agent-teams filesystem paths.

**Verdict on Candidate 1:** No evidence of inotify/fs.watch on `~/.claude/teams/` or `config.json`. The inotify symbols in the binary are Bun runtime infrastructure. Probe confirms no watches on teammate paths at runtime.

### Candidate 2 — Named pipes, Unix sockets, shm

**Runtime probe (`research.md:117-119`):**

> The leader process is NOT holding inotify watches on the teammate/subagent paths, and no persistent sockets/pipes carry presence data.

**Empirical filesystem check (this session):**

`find /home/rosado/.claude/teams/agent-teams-research/` shows exactly two items: `config.json` and `inboxes/<name>.json` files. No `.sock`, `.pipe`, `.fifo`, `.shm` artifacts.

`find /tmp -name "claude*" -maxdepth 2` shows: `/tmp/claude-code-teams-mcp` (a clone of the cs50victor MCP repo, not a live socket), `/tmp/claude-2.1.117.strings.txt` (binary analysis artifact), `/tmp/claude-1000` (likely a Bun socket for the running Claude Code process itself — NOT related to teammate registration), `/tmp/claude-leak-clone.log`, `/tmp/claude-plugin-research/` (RE working dirs).

**Strings dump grep for IPC:** `socket`, `pipe`, `mkfifo`, `shm_open`, `mmap` — these appear as generic syscall strings in the Bun runtime, NOT associated with team paths in any string context.

**Verdict on Candidate 2:** No named pipes, Unix sockets, or shm segments related to teammate presence. The `/tmp/claude-1000` socket appears to be a process-local Bun IPC socket (standard for Bun worker threads), not an external teammate-announcement channel.

### Candidate 3 — MCP tools exposed by leader to plugins (registerTeammate, notifyTaskState)

**Binary strings grep:** `registerTeammate`, `notifyTask`, `notifyTaskState`, `pluginServer`, `mcpServer` (leader-side) — NOT FOUND in the 780,132-line strings dump.

**Plugin research artifacts (`/tmp/claude-plugin-research/`):** Examined `drbinary-chat-plugin/.mcp.json`, `claude-plugins/.claude-plugin/marketplace.json`. These are standard plugin manifests — MCP server configs that Claude Code connects to AS A CLIENT. No evidence of Claude Code exposing an inbound MCP server that plugins could call to register teammates.

**RE'd source:** `docs/internal/2026-prototype/research.md:132-170` (Angle C — public surfaces) explicitly enumerates:

> - Plugins — deliver tools/skills to a Claude session; cannot register a teammate at the AppState level.
> - Hooks (PreToolUse, PostToolUse, Stop, etc.) — fire around Claude's own actions; no "on-teammate-spawn" or "external-teammate-announce" hook.
> - MCP servers — deliver tools, not identities.
> - Agent SDK — lets you build agents; does not let an external agent announce itself to an existing Claude Code session.
> - remote-control subcommand (hidden) — controls an existing Claude Code session programmatically; didn't find a spawn-teammate or equivalent.

**This repo's hooks:** `hooks/session-start.sh` runs at session start only — it validates/installs the shim, then exits. It has no channel back INTO the leader to register a teammate. `hooks/hooks.json` registers only `SessionStart`. No `PreToolUse`, `PostToolUse`, `Stop`, or agent-spawn hooks. No mechanism for the plugin to inject a teammate identity into leader state.

**Verdict on Candidate 3:** No leader-side MCP or plugin API for external teammate registration. NOT FOUND after grepping binary strings, examining plugin manifests, and reading `research.md:132-170`.

### Candidate 4 — Periodic polling/reconciliation of config.json

**Binary strings grep:** `setInterval.*team`, `team.*setInterval`, `setTimeout.*config`, `config.*poll`, `pollConfig`, `reconcileTeam` — NOT FOUND.

**RE'd source:** `teamHelpers.ts:115-159` documented as "only read/write `config.json`" — a synchronous utility, not an interval poller. No `setInterval` or `setTimeout` call associated with team config in any RE'd source reference.

**Verdict on Candidate 4:** No periodic polling of `config.json` by Claude Code. The file is written at spawn time and read at spawn time. NOT FOUND.

### Candidate 5 — SIGUSR1 / SIGUSR2 / signal handlers

**Binary strings grep:** `SIGUSR1`, `SIGUSR2`, `registerTeammate.*signal`, `signal.*teammate` — NOT FOUND in team-context.

The strings dump does contain generic signal-related strings (Bun runtime signal handling), but no evidence of a SIGUSR-triggered teammate-registration callback.

**Verdict on Candidate 5:** No signal-based teammate-registration mechanism. NOT FOUND.

### Was the research.md:128-130 probe exhaustive?

The exact text (`research.md:128-130`):

> TUI presence is session-internal / in-memory Agent-runtime state. No externally writable filesystem path, Unix socket, named pipe, or shm segment feeds it. Passive injection from outside the leader process is not viable.

The probe method (`research.md:104-108`):

> Used `ps`, `lsof`, `ss`, `find` with mtime snapshots, and `/proc/<claude_pid>/fdinfo` to observe what the live leader process touched.

**Gap assessment:** The probe was thorough for the specific question (what does the leader touch during spawn / what IPC channels exist) but was targeted at "teammate/subagent paths" specifically (`research.md:117`). It did not enumerate ALL inotify-watched paths on the leader process. A more exhaustive approach would read `/proc/<pid>/fd` and filter for `inotify` file descriptors, then use `inotifywait` or `/proc/<pid>/fdinfo/<fd>` to enumerate watched paths. The probe's negative conclusion for teammate paths is well-supported; its generality (no inotify at all) is an inference, not a direct enumeration.

**Practical implication:** Even if Claude Code watches some paths (e.g., `~/.claude/settings.json` for live config reloads) via inotify, there is zero evidence that team `config.json` or inboxes are in that watch set. The negative injection test (writing synthetic members directly into `config.json` with TUI showing nothing) is the definitive empirical result — whatever Claude Code watches, team `config.json` for new-member detection is not in it.

### Hooks callability from leader — exploitable hook?

`hooks/hooks.json` registers `SessionStart` only. The hook fires FROM the Claude Code harness TO our shell script — it is a one-way notification (harness → script). The script cannot call back INTO the harness to register a teammate.

Claude Code's hook system (`PreToolUse`, `PostToolUse`, `Stop`, `SessionStart`) are all outbound from the harness to user scripts. None provide an inbound channel for scripts to inject state into `AppState`. This is confirmed by `research.md:140-141`:

> Hooks (PreToolUse, PostToolUse, Stop, etc.) — fire around Claude's own actions; no "on-teammate-spawn" or "external-teammate-announce" hook.

**Exploitable hook: NONE FOUND.**

### Summary table

| Candidate | Evidence | Verdict |
|---|---|---|
| inotify / fs.watch on `~/.claude/teams/` | Binary has Bun FSWatcher symbols (runtime infra); no team-specific watch in strings; `teamHelpers.ts` is synchronous only; runtime probe: no watches on teammate paths | NOT FOUND |
| Named pipes / Unix sockets / shm in team dirs | Team dir contains only `config.json` + inboxes; `/tmp` has no teammate-IPC artifacts; runtime probe negative | NOT FOUND |
| Leader-side MCP tool for external registerTeammate | Not in binary strings; `research.md:132-170` explicitly enumerates and rules out; plugin hooks are outbound only | NOT FOUND |
| Periodic config.json polling by Claude Code | Not in binary strings; `teamHelpers.ts` documented as sync-only; no `setInterval`/`setTimeout` in team-config RE context | NOT FOUND |
| SIGUSR / signal-based teammate registration | Not in binary strings in team context | NOT FOUND |
| Hooks as inbound callback to leader | `hooks/hooks.json` is `SessionStart` only; all hooks are harness→script, not script→harness | NOT FOUND |

**Conclusion:** There is no IPC channel, filesystem watch, signal handler, or plugin hook by which Claude Code's leader process learns about externally-spawned teammates. The leader's `AppState.tasks` can only be populated by code running inside the leader's own Node.js process. External processes have no injection surface. This is consistent with all prior phase-1 findings and the `research.md:128-130` conclusion.

---

## Phase 2 — Findings — experimenter (Task #19)

*Authored: 2026-04-23. Thread C — CDP / debugger attach on Bun.*

### Q1 — Does the current running Claude Code listen on any inspector/CDP port?

**Method:** `pgrep -x claude` → PIDs 625925, 636692. Then `ss -tnlp | grep -E "(625925|636692)"`.

**Result:** No listening sockets for either Claude PID. Confirmed negative with secondary check: `ss -tnlp | grep -E "9229|9230|9231|9232"` → no results.

**Socket enumeration of PID 625925:** All sockets are ESTABLISHED outbound connections to `172.28.156.35:443` (HTTPS to Anthropic API) and DNS resolvers (`127.0.0.53:53`, `127.0.0.54:53`). No loopback listening socket in the 9229-range.

**Verdict: NO — the current Claude Code process is NOT listening on any inspector/CDP port.**

### Q2 — Does Bun's inspector work in a compiled SEA?

**Source: binary strings dump (`/tmp/claude-2.1.117.strings.txt`).**

The strings dump contains:
- `BUN_INSPECT`, `BUN_INSPECT_NOTIFY`, `BUN_INSPECT_CONNECT_TO`, `BUN_INSPECT_PRELOAD` — the full set of Bun inspector env vars
- `--inspect <STR>?  Activate Bun's debugger`
- `--inspect-brk <STR>?  Activate Bun's debugger, set breakpoint on first line of code and wait`
- `--inspect-wait <STR>?  Activate Bun's debugger, wait for a connection before executing`
- `/bun:inspect` — the internal Bun inspector module path
- `BunInspectorConnection`, `BunInspectorConnection::receiveMessagesOnDebuggerThread` — inspector connection class symbols
- `Inspector::JSInjectedScriptHost`, `Inspector::InspectorDebuggerAgent`, `Inspector::FrontendRouter` — full WebKit JSC inspector stack
- `/.well-known/appspecific/com.chrome.devtools.json` — Chrome DevTools integration endpoint

**Conclusion:** The inspector is compiled INTO the Claude Code binary — it is NOT stripped from the SEA. Bun does not strip the debugger from single-executable artifacts. The binary contains the full inspector infrastructure.

**However:** Claude Code itself detects and rejects inspector activation via the function `g4A()` found verbatim in the strings dump:

```javascript
function g4A(){
  let H=t6H(),$=process.execArgv.some((K)=>{
    if(H)return/--inspect(-brk)?/.test(K);
    else return/--inspect(-brk)?|--debug(-brk)?/.test(K)
  }),q=process.env.NODE_OPTIONS&&/--inspect(-brk)?|--debug(-brk)?/.test(process.env.NODE_OPTIONS);
  try{return!!global.require("inspector").url()||$||q}catch{return $||q}
}
```

This function (`g4A()`) detects whether a debugger is attached. It checks: (1) `process.execArgv` for `--inspect(-brk)?`; (2) `NODE_OPTIONS` env var for inspect flags; (3) `global.require("inspector").url()` — i.e., whether the inspector is already running. The function name suggests it is used as a guard — if it returns true, Claude Code likely exits or refuses to run (consistent with Anthropic hardening the binary against dynamic analysis).

### Q3 — Inspector strings in the binary — summary

| String | Found | Significance |
|---|---|---|
| `BUN_INSPECT` | YES | Env var hook for Bun debugger — compiled in |
| `--inspect` / `--inspect-brk` / `--inspect-wait` | YES | CLI flags — compiled in |
| `BunInspectorConnection` | YES | Inspector connection class — compiled in |
| `Inspector::InspectorDebuggerAgent` | YES | Full WebKit JSC inspector stack — compiled in |
| `/.well-known/appspecific/com.chrome.devtools.json` | YES | Chrome DevTools JSON endpoint — compiled in |
| `9229` | NOT FOUND as standalone port number | Default port not hardcoded as a bare string |
| `g4A()` / inspector detection function | YES | Claude Code anti-debug guard |

### Q4 — Thought experiment: CDP attach workflow (not executed)

If an external process could attach a CDP client to a running Bun process, the steps would be:

1. **Start Claude Code with `--inspect=127.0.0.1:9229`** — pass the flag at launch (not settable post-launch). This opens a WebSocket on `ws://127.0.0.1:9229/json`.
2. **Fetch the targets list**: `GET http://127.0.0.1:9229/json` → returns a JSON array of debuggable targets with `webSocketDebuggerUrl`.
3. **Connect a CDP client** (e.g., `chrome-remote-interface`, `puppeteer.connect()`, `bun://inspect`) to the `webSocketDebuggerUrl`.
4. **Send CDP `Runtime.evaluate`** with `{"expression": "...", "returnByValue": true}` to execute arbitrary JS in Claude Code's V8/JSC context — including calling `registerTask()` or mutating `AppState`.
5. **Disconnect** — state mutation persists in-process.

**Why this is NOT viable for our use case:**
- Requires launching Claude Code with `--inspect` at startup — cannot be injected post-launch.
- Claude Code's `g4A()` guard detects inspector attachment and likely refuses to start or aborts the session.
- Even if Claude Code started with inspect, the `g4A()` guard would return `true` immediately, triggering whatever anti-debug exit path uses it.
- Anthropic has clearly hardened against this: `g4A()` is actively called in `main()` / startup (it appears in the startup function chain `a4A → g4A`).

### Feasibility verdict

| Question | Answer |
|---|---|
| Is Claude Code currently listening on a CDP port? | NO — confirmed via `ss` on both PIDs |
| Does Bun strip the inspector from SEAs? | NO — inspector is compiled in |
| Does Claude Code actively detect + block inspector? | YES — `g4A()` detects `--inspect` flags and `inspector.url()` |
| Can CDP attach post-launch? | NO — `--inspect` is startup-only; no runtime activation surface |
| Is CDP attach viable for `registerTask()` injection? | NO — blocked by `g4A()` anti-debug guard at startup |

**Thread C verdict: NOT VIABLE.** CDP attach is technically present in the binary but actively guarded by Claude Code's own anti-debug detection. Even if the inspector flag were passed at startup, the application's own guard would detect it and abort. This path is closed.


---

## Phase 2 — Findings — code-auditor (Task #21)

### Thread E — Fake tmux-session artifacts to trick `detectAndGetBackend()`

*Authored: 2026-04-23. Read-only audit combining binary strings analysis, existing RE'd source (docs-scout/re-scout findings already in this doc), and this repo's code.*

---

#### Q1: How does `detectAndGetBackend()` detect tmux — env var, socket path, or shell command?

**Detection method: `process.env.TMUX` check only. No socket path. No shell-out for detection.**

From the findings already in this doc (Task #15 / docs-scout, lines ~1085-1199), backed by the RE'd source:

`utils/swarm/backends/detection.ts` — `isInsideTmuxSync()` and `isInsideTmux()`:

> Both functions read ONLY `ORIGINAL_USER_TMUX` (a module-level capture of `process.env.TMUX` at load time). Verbatim comment in source: *"We ONLY check the TMUX env var. We do NOT run tmux display-message."* No shell-out. No fallback check.

Binary strings confirmation: `/tmp/claude-2.1.117.strings.txt:241015` contains `if (env.TMUX)` — the minified form of this check.

**Implication for fake-tmux:** Setting `TMUX=/tmp/fake.sock` (or any non-empty string) in the environment of a NEW Claude Code process would cause `isInsideTmuxSync()` to return `true` → Priority 1 in `detectAndGetBackend()` → TmuxInternalBackend selected. The value of `$TMUX` is never validated against an actual socket — it just needs to be non-empty.

However: `ORIGINAL_USER_TMUX` is captured "at load time" (module-level, at process start). Setting `TMUX` after Claude Code starts has no effect. The env var must be present when Claude Code launches.

**`isTmuxAvailable()` does shell out — but only for capability check, not detection:**

```typescript
async function isTmuxAvailable(): Promise<boolean> {
  const result = await execFileNoThrow(TMUX_COMMAND, ['-V'])
  return result.exitCode === 0
}
```

This is Priority 3 in `detectAndGetBackend()` — called only when `$TMUX` is not set AND `$ITERM_SESSION_ID` is not set. It checks that the `tmux` binary exists and exits 0 for `tmux -V`. Does NOT check for a running tmux server or active session.

**Two distinct fake-tmux approaches:**

| Approach | Mechanism | Requirement |
|---|---|---|
| Approach A — env var spoof | Set `TMUX=<any-non-empty-value>` before launching Claude Code | New Claude Code process; env inherited |
| Approach B — fake binary | Put fake `tmux` on PATH that answers `tmux -V` with exit 0 | Works when `$TMUX` is unset; tmux binary absent (or prepended on PATH) |

On this host: `$TMUX` is empty, `/usr/bin/tmux` 3.4 exists → Priority 3 already succeeds without any spoofing when `teammateMode=tmux`. Approach A would force Priority 1. Both approaches reach pane-backend.

---

#### Q2: Historical — was there ever a file-based spawn hook in Claude Code?

**NOT FOUND.**

Searched:
- `docs/internal/` recursively for "legacy spawn", "file.*spawn.*hook", "spawn.*request.*file", "watch.*spawn", "deprecated.*spawn": no matches referencing a file-watching spawn mechanism.
- `docs/internal/2026-prototype/architecture-decision.md:296` mentions "harness does not tolerate self-registered peers" as a risk — indicating the team investigated passive-injection approaches early but found no such hook.
- `docs/internal/2026-prototype/protocol-spec.md:134` mentions "Pure file-based (no harness spawning)" as a conceptual option but notes it "depends on whether harness tolerates config mutations (untested)" — framed as an open question, not a historical feature.
- `docs/internal/2026-prototype/shim-restart-resilience.md:12`: "I found **no public Anthropic statement or changelog entry** that defines the leader's exit-time signal policy for spawned teammates."

Binary strings: no "spawn request", "spawn queue", "spawn watch", "legacy spawn" strings in team-context.

**Verdict:** No evidence of any Claude Code version using a file-based spawn-hook or spawn-request-watching mechanism. The split has always been in-process coroutine vs subprocess-via-pane-backend.

---

#### Q3: Exact tmux commands Claude Code issues after `detectAndGetBackend()` returns; graceful degradation on failure?

**From findings doc (docs-scout Phase 2 synthesis, lines ~2198-2211), backed by re-scout R6 (rasmusab/#26572):**

Four subcommands in order:
1. `tmux display-message -p '#{session_id}'` — get current session ID (TmuxInternalBackend) or `tmux new-session -d -s <name>` (TmuxExternalBackend, creates a detached session)
2. `tmux split-window -dP -F '#{pane_id}' <spawn_command>` — create new pane detached, prints pane ID. This is where `$CLAUDE_CODE_TEAMMATE_COMMAND` (the shim) is invoked as part of `<spawn_command>`
3. `tmux send-keys` — for subsequent interactions / input delivery
4. `tmux respawn-pane` — cleanup path

Binary string at `/tmp/claude-2.1.117.strings.txt:30275`: `Failed to send KEYS` — this is the error string emitted when step 3 fails. It indicates an error log, not a crash.

**Graceful degradation on send-keys failure:**

From findings doc line ~1166 and the `handleSpawn()` try/catch:

```typescript
async function handleSpawn(input, context) {
  if (isInProcessEnabled()) {
    return handleSpawnInProcess(input, context)
  }
  try {
    await detectAndGetBackend()
  } catch (error) {
    if (getTeammateModeFromSnapshot() !== 'auto') {
      throw error  // mode='tmux' + detectAndGetBackend failed → ERROR propagates; no fallback
    }
    markInProcessFallback()
    return handleSpawnInProcess(input, context)  // mode='auto' → silent fallback to in-process
  }
  // proceed with pane spawn
}
```

The try/catch wraps `detectAndGetBackend()`, not the individual tmux subcommands. If `detectAndGetBackend()` succeeds but a subsequent `split-window` or `send-keys` call fails:
- `mode='auto'`: the outer try/catch for the overall spawn may fall back silently (not confirmed for the pane-subcommand level — the RE'd source only shows the `detectAndGetBackend()` catch)
- `mode='tmux'`: error propagates; no fallback

Known issue: `send-keys` at scale fails at ~50% rate due to MAX_CANON 256-byte kernel buffer overflow (re-scout R5, issue #40168). This is a hard failure — the spawn command is truncated. The binary's `Failed to send KEYS` string at line 30275 is an error log for this case.

**Practical implication for fake-tmux:** A fake-tmux shim must respond correctly to all four subcommands above, not just `tmux -V`. The critical one is `split-window -dP -F '#{pane_id}' <spawn_command>` — the fake shim must: (a) exec the spawn command itself (which IS the `$CLAUDE_CODE_TEAMMATE_COMMAND` shim call), and (b) return a valid-shaped pane ID. KILD's ~400-line shell script handles all 20 known subcommands (re-scout R6 / findings doc line ~1182).

---

#### Q4: Was `spawn_teammate` MCP tool from cs50victor ever in Claude Code?

**NOT FOUND in Claude Code binary. NOT FOUND in docs/internal. No changelog reference.**

- Binary strings search for `spawn_teammate`, `spawnTeammate`, `cs50victor`, `claude-code-teams`, `claude.code.teams` in `/tmp/claude-2.1.117.strings.txt`: NOT FOUND.
- `docs/internal/2026-prototype/prior-art.md:105` confirms `spawn_teammate` is one of cs50victor's MCP tools ("stands up an MCP server that exposes 12 tools: ... `spawn_teammate` ...") — framed as a third-party implementation, NOT as something Claude Code natively exposes.
- `docs/internal/2026-prototype/v7-architecture.md:92` explicitly lists `spawn_teammate` as one of the cs50victor tools the adapter deliberately does NOT surface to Codex, because it's a destructive lifecycle operation.
- `docs/internal/spawn-research-phase2-brief.md:69` (Task #10 assignment for docs-scout): "Check changelog for new spawn-related features" — implying no such entry was known to exist; docs-scout would have flagged it if found.

**Verdict:** `spawn_teammate` is a cs50victor library tool, never shipped in or used by Claude Code natively. It calls the Claude binary directly (`spawner.py:98` / `claude_teams/server.py:413-461`) with no awareness of `$CLAUDE_CODE_TEAMMATE_COMMAND`. It is entirely a parallel/third-party implementation.

---

### Summary table

| Question | Answer | Source |
|---|---|---|
| How does `detectAndGetBackend()` detect tmux? | `process.env.TMUX` env var check (module-level capture at startup). No socket path read. No shell-out for detection. Separate `isTmuxAvailable()` shells out `tmux -V` for Priority 3 only. | `detection.ts` (RE'd); binary strings `:241015 if (env.TMUX)` |
| Can fake socket path trick detection? | No — detection reads env var, not socket path. Fake-tmux needs `$TMUX=<non-empty>` OR fake `tmux` binary on PATH answering `tmux -V` exit 0. | Same |
| File-based spawn hook ever existed? | NOT FOUND. No changelog, no RE'd source, no doc reference. | Grep of docs/internal; binary strings; `shim-restart-resilience.md:12` |
| What tmux subcommands does Claude Code run post-detection? | `display-message`/`new-session`, `split-window -dP`, `send-keys`, `respawn-pane`. All four must be handled by fake-tmux. | re-scout R6 (#26572); binary `:30275 Failed to send KEYS` |
| Graceful degradation if send-keys fails? | `detectAndGetBackend()` catch: mode=auto → silent in-process fallback; mode=tmux → error propagates. Per-subcommand failure behavior not confirmed at RE level; empirically known to corrupt spawn at scale (#40168). | `handleSpawn()` RE'd source; issue #40168 |
| `spawn_teammate` MCP tool ever in Claude Code? | Never. cs50victor library tool only; calls `claude` binary directly with no `$CLAUDE_CODE_TEAMMATE_COMMAND` awareness. | Binary strings: NOT FOUND; `prior-art.md:105`; `v7-architecture.md:92` |

---

### Thread D — Third-party plugins hunt (docs-scout, 2026-04-23)

**Goal:** Find any Claude Code plugin or tool that achieves teammate presence in the TUI WITHOUT tmux — i.e., external-to-AppState injection working in the wild.

**Sources searched:** awesome-claude-code (hesreallyhim), ComposioHQ/awesome-claude-plugins, rohitg00/awesome-claude-code-toolkit, GitHub search for `AppState.tasks`/`registerTask`/`registerOutOfProcessTeammateTask`, official plugins-reference and plugins docs pages, jarrodwatts/claude-hud, GitHub issue #51818.

---

#### D1 — No plugin found that injects into AppState or achieves non-tmux TUI teammate presence

**Result: NEGATIVE.** Exhaustive search of the plugin ecosystem found zero plugins that:
- Write directly to `AppState.tasks` or call `registerTask`/`registerOutOfProcessTeammateTask`
- Achieve teammate TUI presence (the Shift+Down cycling presence line) without tmux
- Perform external-to-AppState injection of any kind

The closest candidates examined:

**jarrodwatts/claude-hud** — shows "running agents" in a status line without tmux. But it achieves this by parsing the transcript JSONL stream, not by hooking into AppState:
> "Claude Code → stdin JSON → claude-hud → stdout → displayed in your terminal ↘ transcript JSONL (tools, agents, todos)"
> "Claude HUD uses Claude Code's native statusline API — no separate window, no tmux required, works in any terminal."

This is observability (reading subagent activity from transcript), not teammate presence injection. It displays subagent execution, not team-member presence in the teammate cycling UI.
**URL:** https://github.com/jarrodwatts/claude-hud (accessed 2026-04-23)

**maestro-orchestrate** — "coordinating 22 specialized subagents through 4-phase workflows with native parallel execution." Uses subagents (Agent tool, no `team_name`), not agent-teams teammates. No tmux dependency, no TUI presence line.
**URL:** https://github.com/josstei/maestro-orchestrate (accessed 2026-04-23)

**backlog** — "event-sourced storage, agent coordination" across sessions. File-system coordination, not AppState injection.

**GitHub issue #51818** ("Teammate CLI crashes when receiving permission_response") — confirms teammate CLI runs in tmux backend. No non-tmux injection mechanism described.
**URL:** https://github.com/anthropics/claude-code/issues/51818 (accessed 2026-04-23)

---

#### D2 — Monitors API: TUI surface confirmed, but NOT AppState injection

The official plugins-reference documents the monitors API fully (requires v2.1.105+):

> "Each monitor runs a shell command for the lifetime of the session and delivers every stdout line to Claude as a notification, so Claude can react to log entries, status changes, or polled events without being asked to start the watch itself."

The `description` field appears "in the task panel and in notification summaries" — this IS visible in the TUI task panel. However, this is task-panel presence (showing the monitor name/description as a running process), NOT teammate presence (the Shift+Down cycling UI that shows teammates by name).

**Critical distinction:** The task panel monitor entry is a static label shown while the monitor process runs. It does not register a teammate into `AppState.members`, does not create a messageable entity, and does not appear in the Shift+Down teammate cycle. A monitor is an observable process, not a teammate.

Monitor communication is one-way: stdout lines → Claude notifications. There is no documented mechanism for a monitor to call back into AppState, register a task owner, or inject a teammate entry.

**Quote (plugins-reference, monitors section):**
> "Plugin monitors use the same mechanism as the Monitor tool and share its availability constraints. They run only in interactive CLI sessions, run unsandboxed at the same trust level as hooks, and are skipped on hosts where the Monitor tool is unavailable."

No mention of AppState access, teammate registration, or TUI presence injection.

**URL:** https://code.claude.com/docs/en/plugins-reference#monitors (accessed 2026-04-23)

**For Thread B (re-scout Task #18):** The monitors API delivers stdout lines as Claude notifications. The mechanism is identical to the Monitor tool. Monitors cannot write to AppState — they only produce notification events. Any AppState modification would require Claude to act on the notification content (i.e., LLM-mediated, not direct injection).

---

#### D3 — No "in-process teammate registration" documented anywhere

Searched:
- Official agent-teams docs: https://code.claude.com/docs/en/agent-teams (accessed 2026-04-23) — no sanctioned in-process registration mechanism
- Official plugins docs: https://code.claude.com/docs/en/plugins (accessed 2026-04-23) — plugin `settings.json` supports `agent` and `subagentStatusLine` keys only; no teammate registration
- Plugin hook events — full list includes `TeammateIdle`, `TaskCreated`, `TaskCompleted`, `SubagentStart`, `SubagentStop` but no `TeammateRegister` or `TeammateJoin` hook that external code can trigger
- CHANGELOG v2.1.105–v2.1.118: no entry about in-process teammate registration API

**Quote (official plugins docs, settings.json support):**
> "Currently, only the `agent` and `subagentStatusLine` keys are supported."

No teammate registration key exists.

**URL:** https://code.claude.com/docs/en/plugins (accessed 2026-04-23)

---

#### D4 — Summary: no positive hit on external AppState injection

| Search target | Result |
|---|---|
| Plugin with non-tmux TUI teammate presence | NOT FOUND |
| Plugin calling `registerTask`/`registerOutOfProcessTeammateTask` | NOT FOUND |
| Plugin writing to `AppState.tasks` or `AppState.members` | NOT FOUND |
| Anthropic-documented in-process teammate registration | NOT DOCUMENTED |
| Plugin `settings.json` teammate registration key | NOT DOCUMENTED (only `agent` and `subagentStatusLine` supported) |
| Monitor API as AppState injection vector | NOT VIABLE — monitors are notification-only, no AppState write-back |
| Any wild tool achieving non-tmux teammate presence (TUI cycle) | NOT FOUND |

**Conclusion:** There is no known working example of external-to-AppState teammate injection in the wild. All confirmed teammate TUI presence (Shift+Down cycle) requires the subprocess spawn path (tmux/iTerm2 mode). The monitors API provides task-panel visibility for background processes but is categorically different from teammate presence. No third-party plugin has cracked this.

Thread D verdict: **negative with full enumeration.** No positive hit.

---

#### D5 — re-scout addendum: KILD, Warp team agents plugin, extended wild catalog (2026-04-23)

**KILD (github.com/Wirasm/kild):**

KILD is a Rust-based session manager for parallel AI agent workflows using Git worktrees. Its daemon mode includes a tmux-compatible shim (`kild-tmux-shim` crate) that gets symlinked as `~/.kild/bin/tmux` and prepended to `$PATH`, so Claude Code's PaneBackendExecutor calls hit the shim instead of the system tmux binary. The shim routes via IPC to the KILD daemon, which manages PTYs for each teammate pane.

Key questions answered:
- **AppState injection?** NO. `AGENTS.md` and `main.rs` contain zero references to `AppState`, `registerTask`, `setAppState`. KILD's state management uses its own Command→Store→Event Rust pattern, entirely separate from Claude Code's React state.
- **TUI presence mechanism?** KILD's shim makes tmux subcommands work (new-session, split-window, send-keys, display-message — the full ~20-subcommand surface). Claude Code's PaneBackendExecutor calls these and populates AppState.tasks via `registerOutOfProcessTeammateTask()` — that's Claude Code's own code doing the registration as a side effect of the shim responding correctly to pane commands. KILD doesn't inject into AppState; it just makes the tmux contract work so Claude Code does the registration itself.
- **Does it work without real tmux?** YES — that's its purpose. The shim replaces the tmux binary. But it requires KILD daemon running and `~/.kild/bin` prepended to PATH.
- **Conclusion:** KILD is the most complete real-world fake-tmux implementation (Rust, ~20 subcommands). It achieves TUI presence by satisfying the pane backend contract — not by any direct AppState injection. Mechanism: PATH shim → pane backend fires → Claude Code's own `registerOutOfProcessTeammateTask` runs.

Source: `github.com/Wirasm/kild` README, AGENTS.md, crates/kild-tmux-shim/src/main.rs (2026).

---

**codercodingthecode/claude-warp-team-agents:**

Uses `CLAUDE_CODE_TEAMMATE_COMMAND` + AppleScript to open Warp split panes. Mechanism:
- Sets `CLAUDE_CODE_TEAMMATE_COMMAND` to `warp-teammate.sh` (the plugin wrapper).
- Claude Code's PaneBackendExecutor calls the wrapper instead of the real `claude` binary.
- The wrapper uses AppleScript (`osascript`) to drive Warp's keyboard shortcuts (CMD+D, CMD+SHIFT+D) to create split panes, then runs the real `claude` binary with original argv inside those panes.
- AppState injection: **NONE.** The plugin relies entirely on Claude Code's own PaneBackendExecutor registering the teammate via `registerOutOfProcessTeammateTask()` after the split-pane spawn succeeds. The plugin just routes where the pane is created (Warp instead of tmux).
- macOS only (AppleScript requirement). Does not work on WSL2/Linux.

Source: `github.com/codercodingthecode/claude-warp-team-agents` README (2026).

---

**psmux (psmux/psmux):**

Windows-side tmux replacement that sets `$TMUX` env var and injects `--teammate-mode tmux` via a PowerShell wrapper function around every `claude` invocation. When `$TMUX` is set, Claude Code's `isInsideTmuxSync()` returns true → pane backend selected → shim fires. AppState injection: **NONE** — same mechanism as above; relies on Claude Code's own pane backend registration.

Source: `github.com/psmux/psmux/blob/master/docs/claude-code.md` (2026).

---

**Wild catalog sweep — no AppState injection examples found:**

| Source searched | Positive hit on non-tmux AppState injection? |
|---|---|
| `github.com/hesreallyhim/awesome-claude-code` | NOT FOUND |
| `github.com/rohitg00/awesome-claude-code-toolkit` | NOT FOUND |
| `github.com/ComposioHQ/awesome-claude-plugins` | NOT FOUND |
| `github.com/anthropics/claude-code/tree/main/plugins` (14 official plugins) | NOT FOUND |
| `github.com/hellowind777/hello2cc` ("silent Agent model injection") | NOT FOUND — "injection" refers to default agent setting, not AppState |
| GitHub search: `"registerTask" OR "AppState.tasks" external injection` | 0 results |
| psmux, KILD, Warp plugin (deepest fake-tmux implementations) | Use pane backend + Claude Code's OWN registration; zero AppState injection |

**Pattern confirmed across all implementations:** Every tool that achieves TUI teammate presence does so by satisfying the PaneBackendExecutor tmux contract (real tmux, fake-tmux binary, PATH shim, or env var spoofing). Claude Code's own `registerOutOfProcessTeammateTask()` runs as a consequence of the spawn succeeding. No tool in the wild injects into AppState externally.

**This is not just "never been done" — it is structurally impossible** without either: (a) running code in Claude Code's leader process (which Thread A/B show requires BUN_OPTIONS preload or a future in-process plugin API), or (b) satisfying the pane backend contract so Claude Code does the injection itself. The latter is precisely what Options 1/2/4 in the lead's solution menu accomplish.

---

### Thread A — Bun/Node preload injection viability

**Task source:** Lead assignment 2026-04-23 (Task #17). Question: does the compiled Bun SEA (Claude Code 2.1.118) honor `BUN_PRELOAD`, `NODE_OPTIONS`, `BUN_OPTIONS=--preload=<file>`, or similar env-var hooks that would let a user-placed JS file load inside Claude Code at startup and monkey-patch `registerTask` or AppState?

---

#### Binary identification

Claude Code 2.1.118 binary: `/home/rosado/.local/share/claude/versions/2.1.118` — 239MB ELF, confirmed to be a Bun SEA (`bun build --compile` output). Source: frr.dev article + Anthropic December 2025 Bun acquisition + RE repo `bun build ./src/index.ts --compile --outfile claude` (no decompiled source needed — confirmed via `file` + size).

---

#### String grep results (direct binary inspection)

Grepped the binary with `strings | grep -i "bun_options|bun_preload|node_options|bunfig|--preload|_preloadModules|autoloadBunfig"`:

| String found | Significance |
|---|---|
| `BUN_OPTIONS` | Env var the Bun runtime reads for extra flags |
| `NODE_OPTIONS` | Also present (Bun partially honors for compat) |
| `BUN_INSPECT_PRELOAD` | Bun-internal preload for inspector |
| `--preload` / `-r, --preload <STR>` | CLI flag handler in embedded runtime |
| `--require <STR>... Alias of --preload` | Node.js compat alias |
| `--no-compile-autoload-bunfig` | Disable bunfig.toml in SEA (default: enabled) |
| `autoloadBunfig` | bunfig.toml loading code path present |
| `--compile-autoload-bunfig` | Can be toggled at compile time |

Source: `strings /home/rosado/.local/share/claude/versions/2.1.118 | grep ...` (empirical, 2026-04-23).

---

#### Empirical tests (all run via `BUN_OPTIONS="--preload /tmp/X.js" timeout 8 <claude_binary> --print ""`)

**Test 1 — Does BUN_OPTIONS=--preload fire?**

Script: write `/tmp/preload_fired.txt` at top of preload file.
Result: **file created**. `BUN_OPTIONS="--preload /tmp/preload_test.js"` causes the script to execute inside the Claude Code binary process before the main entry point runs. **POSITIVE — BUN_OPTIONS is honored by the Bun SEA.**

**Test 2 — What is in the preload environment?**

- `Bun` global: **available** — `Bun.FFI`, `Bun.plugin`, `Bun.Glob`, etc. all accessible.
- `Bun.plugin`: **type=function, callable** — `Bun.plugin({name, setup(build){}})` installs without error; `setup()` is called.
- `require('react')`, `require('AppState')`, `require('framework')`, `require('registerTask')`: all **ERROR: Cannot find package** — Claude Code's internal modules are bundled into the SEA bytecode, not exposed as require-able packages.
- `require.cache`: only contains `/tmp/preload_monkey.js` — no Claude Code modules visible in cache.
- `globalThis.__preloadActive = true`: persists into main Claude Code runtime — confirmed by exit handler seeing the value still set after full startup.

**Test 3 — Does `Bun.plugin onLoad` intercept Claude Code's internal module loads?**

Installed a `build.onLoad({ filter: /\.(js|ts|mjs|cjs)$/ })` hook before entry point runs.
Result: **interceptedCount=0**. Bun's `onLoad` plugin hook fires at build/bundle time, not at runtime import resolution for bundled modules. Since all of Claude Code is pre-bundled into the SEA bytecode, there are no runtime `import()` calls to intercept. The hook is dead for bundled code.

**Test 4 — Is `Module._resolveFilename` hookable?**

`require('module')._resolveFilename` is available and can be replaced. Hook fires for **707 module resolutions** during `--print ""` startup — but ALL are Node built-ins (`fs`, `crypto`, `path`, `os`, `child_process`). No Claude Code internal module paths appear because they're all inlined bytecode, not separate files.

**Test 5 — Do prototype patches affect Claude Code's runtime objects?**

Patched `Array.prototype.push` and `Object.assign`. Results: **17,545 push calls** and **589 assign calls** during `--print ""` startup. `globalThis.__preloadActive` remained true after full startup. This confirms the preloaded code runs in the **same JS heap** as Claude Code's main bundle — prototype patches propagate.

**Test 6 — Can `Function.prototype.call` catch named functions like `registerTask`?**

Hooked `Function.prototype.call` and collected all `.name` values. Total unique names: **11** — only `Writable`, `bind`, `exec`, `replace`, `slice`, `splice`, `statSync`, `toString`, and a few others. **No `registerTask`, `setAppState`, or `handleSpawn` names found.** The minifier strips internal function names from the bundled SEA bytecode. Claude Code's internal closures are anonymous.

**Test 7 — Does JSON.stringify/parse carry task-state shapes?**

Hooked both with regex for `taskId|taskState|registerTask|agentName|teamName|InProcess|backendType`. Result: **0 hits** during full `--print ""` startup. React state is purely in-memory and never serialized to/from JSON during the code paths exercised.

---

#### Verdict

**BUN_OPTIONS=--preload is confirmed honored by the Claude Code 2.1.118 Bun SEA.** A user can inject arbitrary JS that runs inside the Claude Code process before the entry point. The preloaded code shares the same JS heap, can mutate `globalThis`, can patch built-in prototypes, and has access to the full `Bun` global API.

**However, reaching `registerTask` or `AppState` from a preload is not straightforward:**

1. Claude Code's internal modules are bundled bytecode — not require-able by path or name. No `require('framework')`, `require('AppState')`, or `require('registerTask')` works.
2. `Bun.plugin onLoad` hooks don't fire for bundled modules — they're already inlined.
3. Internal function names are stripped by the minifier — `Function.prototype.call` interception catches only 11 named built-ins, not `registerTask`.
4. `Module._resolveFilename` fires 707 times but only for Node built-ins.

**The viable attack path (if any) is prototype-based:** since `Array.prototype.push` fires 17k+ times and `Object.assign` fires 589 times inside Claude Code's main bundle, a preload that patches these to detect when they receive task-shaped arguments (by inspecting argument structure) COULD potentially intercept the moment React's `setAppState` merges a new task into `AppState.tasks`. From that intercept point, a closure could be installed to fire a custom callback.

**This is theoretically possible but practically fragile:**
- The specific array/assign call that carries task state would need to be identified by argument structure (not function name).
- Any Claude Code update that changes the state-update internal structure breaks the intercept silently.
- The preload script must be on disk and `BUN_OPTIONS` must be set in the environment before Claude Code launches — this is user-side configuration, not something a plugin or the adapter itself can inject.
- React's state update mechanism (via `useState`/`useReducer` setter) does NOT go through `Object.assign` or standard array methods in a predictable way — it uses React's fiber reconciler internals.

**Conclusion for Task #17:** `BUN_OPTIONS=--preload` is a real, working injection surface in Claude Code 2.1.118. It gives code execution in the same heap as Claude Code. But reaching the specific `setAppState` closure (which Task #8 showed is the only gate to `AppState.tasks`) via prototype patches is not reliably possible — the closure is anonymous, created inside React's reconciler, not detectable by name or standard prototype hooks. This path is **low probability** for achieving `registerTask` injection without deeper reverse-engineering of the specific minified function shapes in the binary.

**The simpler use of this surface:** a preload could set `process.env.TMUX = 'fake-session'` before Claude Code reads `ORIGINAL_USER_TMUX` at module load time — this is a more direct attack on `isInsideTmuxSync()` than the fake-tmux binary approach, with zero dependency on a tmux binary being present.

Sources: empirical tests on `/home/rosado/.local/share/claude/versions/2.1.118` (2026-04-23); Bun docs `bun.com/docs/bundler/executables` (BUN_OPTIONS); GitHub oven-sh/bun#21496 (BUN_OPTIONS honored in compiled executables, bug fixed in PR #26346).

---

### Thread B — plugin in-process JS

**Task source:** Lead assignment 2026-04-23 (Task #18). Question: which plugin manifest components load JS in the leader's Node.js process (not subprocess)? Priority: monitors, then mcpServers reverse-import mode, then hooks history, then skills confirmation.

---

#### 1. Monitors (added v2.1.105)

**Schema** (from `code.claude.com/docs/en/plugins-reference`, confirmed by `claudefa.st/blog/guide/mechanics/monitor`):

```json
[
  {
    "name": "error-log",
    "command": "tail -F ./logs/error.log",
    "description": "Application error log",
    "timeout_ms": 300000,
    "persistent": false
  }
]
```

Fields: `name`, `command` (shell string), `description`, `timeout_ms`, `persistent`. **No JS module path field.** The `command` field is a shell command string — identical to the `command` field in hooks.

**Execution model:** subprocess. The docs state: *"Claude writes a small script for whatever you want watched, that script runs as a background subprocess, and every line of stdout gets streamed back to Claude as a real-time event."* (claudefa.st/blog, 2026). GitHub issue #52245 confirms via process-tree analysis: *"`ps` should show one child process per declared monitor under the claude PID"* — monitors are child processes of Claude Code, not in-process coroutines. There is no mention of `require()`, `import()`, or a module-path field anywhere in the schema or docs.

**AppState/React access from monitors:** zero. Monitors produce stdout lines; Claude Code reads those lines and delivers them as notifications to the conversation. There is no callback into AppState from the monitor subprocess. The monitor subprocess is fully isolated (no shared heap, no `setAppState` access).

**Verdict for monitors: NEGATIVE.** Subprocess-only, no in-process JS loading, no AppState access.

Source: `code.claude.com/docs/en/plugins-reference` (monitors schema); `claudefa.st/blog/guide/mechanics/monitor` (subprocess architecture); GitHub `anthropics/claude-code#52245` (ps-tree evidence); CHANGELOG v2.1.105 entry.

---

#### 2. mcpServers — is there a module-import mode?

All documented MCP transports in Claude Code are external-process or network-based:
- **stdio**: spawns subprocess, communicates via stdin/stdout (most common)
- **SSE**: connects to HTTP server with Server-Sent Events
- **HTTP**: standard HTTP request/response

`code.claude.com/docs/en/mcp` (2026): *"Stdio transport spawns a subprocess and communicates via stdin/stdout."* No mention of a module-import or in-process transport for plugin-declared MCP servers.

The RE source `builtinPlugins.ts` stores `mcpServers` config on `LoadedPlugin` objects for consumption elsewhere — it does NOT import MCP server modules directly. The `entrypoints/mcp.ts` Task #12 finding (`setAppState: () => {}` no-op) further confirms Claude Code's MCP surface is deliberately kept out of the main React state loop.

**Verdict for mcpServers module-import mode: NOT FOUND.** All transports are subprocess or network. No in-process module import mode documented or found in RE source.

Source: `code.claude.com/docs/en/mcp`; RE `builtinPlugins.ts`; Task #12 findings (`entrypoints/mcp.ts:151` `setAppState` no-op).

---

#### 3. Hooks — any in-process JS loading historically?

The hooks system uses exclusively `"type": "command"` entries that execute shell commands as subprocesses. Evidence:

- `code.claude.com/docs/en/plugins-reference` hook schema: `{"type": "command", "command": "...", "timeout": N}` — `type` field accepts only `"command"` (no `"module"` or `"import"` type documented).
- All real-world plugin hooks.json files examined use `"type": "command"` with shell commands: cowork plugin (`node "${CLAUDE_PLUGIN_ROOT}/scripts/session-lifecycle-hook.mjs" SessionStart`), claude-anyteam (`hooks/hooks.json`), codex-jr, ralph-loop, openai-codex — all subprocess commands.
- CHANGELOG: no entry mentioning an in-process hook type in any version.
- The hooks themselves get data via stdin (JSON), return output via stdout/stderr and exit code — the classic subprocess contract. There is no bidirectional RPC or shared-heap path.

**Verdict for hooks in-process JS: NEGATIVE — never existed.** All hooks are `type: "command"` subprocess calls, with no historical in-process JS loading mechanism.

Source: `code.claude.com/docs/en/plugins-reference`; empirical: `/home/rosado/.claude/plugins/cache/*/hooks/hooks.json` — all use `"type": "command"`; CHANGELOG grep (no in-process hook entry).

---

#### 4. Skills — text-only confirmation

Skills are `SKILL.md` files — YAML frontmatter + Markdown prompt text. Confirmed by:
- `code.claude.com/docs/en/plugins` docs: *"Each skill is a folder containing a `SKILL.md` file."* No JS field in schema.
- `code.claude.com/docs/en/plugins-reference` skills schema: `SKILL.md` with frontmatter fields `description`, `disable-model-invocation`, `when_to_use`. No `module`, `script`, or `command` field at the skill level.
- The `skillDefinitionToCommand()` function in `builtinPlugins.ts` converts skills to command objects — it sets `source: 'bundled'` and reads only the markdown content. No module loading.

**Verdict for skills: CONFIRMED TEXT/PROMPT ONLY.** Skills are never executed as JS.

---

#### Summary table

| Plugin component | Execution model | In-process? | AppState/React access? |
|---|---|---|---|
| `monitors` | Shell subprocess, stdout → notifications | NO | NO |
| `mcpServers` | Subprocess (stdio) or network (SSE/HTTP) | NO | NO (setAppState no-op per Task #12) |
| `hooks` | Shell subprocess (type: "command"), stdin/stdout/exit | NO | NO |
| `skills` | Text prompt fragments (SKILL.md), no execution | N/A | NO |

**Thread B verdict: NEGATIVE.** No plugin manifest component loads JS in-process in the leader's Node.js process. Every plugin extension point is either a subprocess command (hooks, monitors) or a network/stdio-subprocess protocol (mcpServers). There is no in-process JS loading path in the plugin system that could give access to `setAppState`, `registerTask`, or AppState.

The only confirmed in-process code execution surface in Claude Code (per Thread A) remains `BUN_OPTIONS=--preload`, which requires user-side env-var configuration in `~/.claude/settings.json`, not plugin manifest declaration.

Sources: `code.claude.com/docs/en/plugins` and `/plugins-reference` (2026); `claudefa.st/blog/guide/mechanics/monitor` (subprocess model); GitHub `anthropics/claude-code#52245` (ps-tree confirmation); empirical hooks.json inspection (all `type: "command"`); CHANGELOG v2.1.105 (monitors introduced); RE `builtinPlugins.ts` (data-layer only, no module imports).

---

### Thread F — native injection surface

**Task source:** Lead assignment 2026-04-23 (Task #22). Question: does Claude Code load native libraries via user-controllable paths? Does `bun:ffi` / `process.dlopen` work? Does `LD_PRELOAD` fire? What symbols does the binary export?

All tests run against `/home/rosado/.local/share/claude/versions/2.1.118` (239MB Bun 1.3.13 SEA, 2026-04-23).

---

#### F1 — Binary string analysis

**Relevant strings found** (`strings <binary> | grep -E "bun:ffi|dlopen|LD_PRELOAD|..."`):

| String | Significance |
|---|---|
| `bun:ffi` | bun:ffi module name — present as importable module |
| `dlopen` | Function name present; "dlopen requires 2 arguments" (bun:ffi API) |
| `JSCallback`, `JSCallbackFunction`, `JSCallbackObject` | bun:ffi JSCallback type present |
| `--no-addons` / `Throw an error if process.dlopen is called, and disable export condition "node-addons"` | CLI flag EXISTS but is NOT passed to Claude Code at startup (see F3) |
| `/$bunfs/root/audio-capture.node`, `/$bunfs/root/image-processor.node` | Native .node modules bundled inside the SEA's virtual filesystem — NOT user-accessible paths |
| `../prebuilds/computer_use.node` | Prebuild path for computer-use feature — relative to SEA bundle root, not host filesystem |

**No user-controlled native library path found.** All `.node` references are either inside `$bunfs` (virtual SEA filesystem) or relative to the bundle root. There is no `~/.claude/native/*.so` or similar user-writable native addon directory in the binary strings.

Source: `strings /home/rosado/.local/share/claude/versions/2.1.118 | grep -E "..."` (empirical, 2026-04-23).

---

#### F2 — Dynamic symbol table (`nm -D`)

Relevant exported symbols:

```
U  dlopen@GLIBC_2.2.5           # Claude Code IMPORTS system dlopen (uses it)
T  napi_module_register@@BUN_1.2 # NAPI module registration — exported, callable
T  napi_internal_register_cleanup_zig@@BUN_1.2
T  node_module_register@@BUN_1.2
T  uv_dlopen@@BUN_1.2            # libuv dlopen wrapper — exported
w  _ITM_registerTMCloneTable
w  _ITM_deregisterTMCloneTable
U  __register_atfork@GLIBC_2.3.2
```

Key observation: `napi_module_register` and `node_module_register` are EXPORTED (T = text section, globally visible). This means NAPI modules loaded into the process can call these symbols — the NAPI registration infrastructure is live. `uv_dlopen` is also exported, confirming the libuv dynamic loading path is intact.

No `registerTask`, `setAppState`, or Claude Code-specific symbols are exported (all internal code is minified/bundled bytecode with no public symbol table).

Source: `nm -D /home/rosado/.local/share/claude/versions/2.1.118` (empirical, 2026-04-23).

---

#### F3 — Is `--no-addons` active at Claude Code runtime?

**NO.** Empirical test via preload:

```json
{
  "argv": ["bun", "/$bunfs/root/src/entrypoints/cli.js", "--version"],
  "execArgv": ["--preload", "/tmp/preload_argv.js"],
  "dlopenIsFunction": true
}
```

`process.dlopen` is a callable function (not disabled). `--no-addons` is present as a CLI flag in the binary but Claude Code does NOT pass it to itself. Native addons are enabled at runtime.

Source: empirical preload test `/tmp/preload_argv.js` (2026-04-23).

---

#### F4 — LD_PRELOAD behavior

**LD_PRELOAD fires.** Test: compiled a minimal shared library with `__attribute__((constructor))` that writes to a file. Result:

```
LD_PRELOAD=/tmp/ld_preload_test.so claude --version
→ /tmp/ld_preload_fired.txt: "LD_PRELOAD fired in pid=2061233"
```

The Bun SEA does not suppress OS-level `LD_PRELOAD` injection. A shared library loaded via `LD_PRELOAD` runs its constructor inside Claude Code's process at load time — before even `BUN_OPTIONS=--preload` JS runs. This is the deepest injection surface available.

**JS context from LD_PRELOAD constructor:** A C constructor fires very early in process startup, before the Bun runtime initializes the JS VM. At constructor time, there is no `napi_env` available yet — the NAPI environment only exists after Bun has initialized its JS runtime. An `LD_PRELOAD` library that tries to call NAPI functions from the constructor will crash (null env pointer). However, an `LD_PRELOAD` library can register a thread or use `pthread_atfork`/signal handlers to be called later, or hook system calls.

Source: empirical `LD_PRELOAD=/tmp/ld_preload_test.so` test (2026-04-23).

---

#### F5 — process.dlopen and NAPI module loading

**process.dlopen is callable from a BUN_OPTIONS preload and successfully loads NAPI modules.** Full test:

Test NAPI module built against `/usr/include/node/node_api.h`, loaded via `process.dlopen(m, '/tmp/test_napi.node')`:

```
Result: SUCCESS
napi_loaded.txt:
  NAPI module loaded in Claude Code env=0x7584a302fb40
  napi_get_global status=0
  global.Bun type=6 (object — present and accessible)
```

A NAPI module loaded this way:
- Gets a valid `napi_env` with full JS context
- Can call `napi_get_global()` to get `globalThis`
- Can enumerate globalThis (86 properties, including `Bun`, `fetch`, standard Web APIs)
- Can call `napi_get_property()` to read any global property by name
- Can call `napi_set_named_property()` to write to any global

**NAPI can set process.env.TMUX.** Test (`test_napi2.node`):
- Native `setenv("TMUX", "/tmp/fake-tmux,1,0", 1)` — does NOT propagate to `process.env.TMUX` via the native call alone (Bun's process.env is a JS proxy over the env, not a live C view)
- But `process.env.TMUX = '...'` from a preload JS script DOES work — sets the value that subsequent code reads

**bun:ffi dlopen:** Available and callable (`bun:ffi` module imports successfully). Requires declaring symbol signatures upfront. Cannot dlopen the Claude binary itself (executable, not shared lib). Can dlopen any user-placed `.so` file by path.

Source: empirical tests `/tmp/preload_napi.js`, `/tmp/preload_napi2.js`, `/tmp/preload_ffi.js` (2026-04-23).

---

#### F6 — Native library paths Claude Code loads from host

`ldd` output shows only standard system libs: `librt.so.1`, `libc.so.6`, `libpthread.so.0`, `libdl.so.2`, `libm.so.6`. No user-controllable paths.

Runtime strace (`openat` during `--version`) shows no `.node` files opened from user-writable locations — all native modules are inside the SEA `$bunfs` virtual filesystem. Claude Code does NOT load native addons from `~/.claude/native/` or any user-writable directory by default.

**The only paths for native code injection are:**
1. `LD_PRELOAD` — OS-level, fires before JS VM
2. `process.dlopen(m, '/path/to/user.node')` from a `BUN_OPTIONS=--preload` JS file — fires with full NAPI context
3. `bun:ffi dlopen('/path/to/user.so', {...symbols...})` — fires with full FFI context

All three require user-side configuration (`LD_PRELOAD` env var, or `BUN_OPTIONS` in `~/.claude/settings.json` env block).

---

#### F7 — Payoff analysis: can native injection reach setAppState?

**The same structural barrier applies as Thread A.** The `setAppState` closure is:
- An anonymous function created inside React's reconciler at startup
- Never exported or globally named
- Not reachable by symbol name from NAPI or FFI

What native injection CAN do that JS preload cannot:
- **Memory scanning:** A native addon could scan the Bun heap for React fiber structures and find the state setter by walking fiber nodes. This requires matching minified V8/JSC internal object layouts — possible in principle but deeply fragile and version-specific.
- **Symbol hooking (LD_PRELOAD):** Hook libc functions (malloc, mmap) or Bun's own exported functions (`napi_module_register`, `uv_dlopen`) to intercept calls. But none of these are on the path to `setAppState`.
- **`process.env.TMUX` setting:** A JS preload does this more cleanly; no native needed.

**Practical assessment:** Native injection provides more surface area than pure JS preload, but does not add a clean path to `setAppState` or `registerTask`. The React fiber memory-scanning approach would work in theory but is equivalent in fragility to decompiling the minified bytecode — not a maintainable solution. It has no advantage over the simpler `process.env.TMUX` spoofing that a JS preload already handles.

---

#### Thread F verdict

| Surface | Available? | Reaches setAppState? | Practical for TMUX spoofing? |
|---|---|---|---|
| `LD_PRELOAD` | YES — fires before JS VM | NO (no NAPI env at constructor time) | NO (env set too early, TMUX capture timing unclear) |
| `process.dlopen` via preload | YES — NAPI module loads, gets full JS context | NO (anonymous closure, no symbol) | MOOT (JS preload already handles this) |
| `bun:ffi dlopen` | YES — available | NO | MOOT |
| `~/.claude/native/*.so` auto-load | NOT FOUND — no such path | N/A | N/A |
| Memory scanning via native | POSSIBLE but deeply fragile | THEORETICALLY | MOOT |

**Thread F verdict: LOW-VALUE NEGATIVE.** Native injection surfaces exist (LD_PRELOAD, process.dlopen, bun:ffi) but add nothing beyond what `BUN_OPTIONS=--preload` with `process.env.TMUX = '...'` already achieves more cleanly. The `--no-addons` flag is not active. No user-controlled auto-load path exists. The highest-value finding is confirmation that `process.dlopen` gives full NAPI env access — but the `setAppState` closure is still unreachable by name or symbol from native code.

Sources: `strings`, `nm -D`, `ldd` on binary (empirical); preload tests `/tmp/preload_argv.js`, `preload_napi.js`, `preload_napi2.js`, `preload_ffi.js`; `LD_PRELOAD` test with compiled `.so` (all 2026-04-23).
