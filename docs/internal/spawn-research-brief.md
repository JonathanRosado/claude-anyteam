# Spawn-mechanism research — team brief

**Team name:** `agent-teams-research`
**Purpose:** Determine the definitive, frictionless way to spawn Codex-backed (and other external-model) teammates in Claude Code Agent Teams. Deliver a clean solution that ALWAYS works when the user wants Codex-backed agents.

**Status:** Live research. No repo changes without team consensus + user sign-off.

---

## The question we have to answer

The claude-anyteam plugin installed in this session promises that a teammate named `codex-alice` gets routed to Codex via a spawn shim (`$CLAUDE_CODE_TEAMMATE_COMMAND`). Two paths have surfaced in prior investigation:

- **Path A** — the shim intercepts Claude Code's built-in teammate-spawn subprocess. No user action beyond creating the teammate normally. **Should be the default.**
- **Path B** — `setsid nohup claude-anyteam --team X --name codex-Y --model gpt-5.5 --effort xhigh ...` launched directly. Documented as the "headless and persistent" escape hatch in `README.md:136-148`.

**We must confirm empirically:**

1. In Claude Code 2.1.118 (current), what EXACT code path gets triggered when an LLM leader calls the `Agent` tool with `team_name="X"` and `name="codex-alice"`?
2. Does that path invoke `$CLAUDE_CODE_TEAMMATE_COMMAND`, or bypass it?
3. If bypassed, what OTHER primitive (tool, command, env var) DOES trigger `$CLAUDE_CODE_TEAMMATE_COMMAND`?
4. For a fully-programmatic, no-human-TUI-action flow (what this session is), which path is actually achievable, and how?

## Constraints — read before acting

### 1. Hard rule (from user, CLAUDE.md top):
> "understand that you are a fucking idiot, and you are not allowed to make any judgements before understanding the entire codebase"

No edits to `src/claude_anyteam/**` without (a) reading the relevant module in full, (b) tracing the call path, (c) consensus from the team-lead + user sign-off. Treat host-specific symptoms as LOCAL environment issues until proven otherwise.

### 2. Every claim must be backed by one of:
- **Official Anthropic docs** (prefer `code.claude.com/docs/en/agent-teams.md` and adjacent pages)
- **Community reverse-engineering** (e.g., https://dev.to/nwyin/reverse-engineering-claude-code-agent-teams-architecture-and-protocol-o49, issue #26572 on `anthropics/claude-code`)
- **Direct code reading** in this repo OR upstream (`cs50victor/claude-code-teams-mcp`, `anthropics/claude-code` if any public traces)
- **Empirical observation** with a reproducible test

Don't assert "the shim fires here" or "Agent tool uses subprocess" without one of these backing it. Prefer 2026 sources over older ones (Claude Code has evolved significantly).

### 3. Host-specific gotchas to be aware of (SAVE your time, don't re-discover):
- The `codex.py` MCP probe on line ~284 hardcodes the string `"python"` as the subprocess interpreter. If spawn fails with `ModuleNotFoundError: claude_anyteam`, it's likely that `python` on the host doesn't have the module importable. **Do not modify codex.py.** Workarounds: add a `~/.local/bin/python` shim or report to user.
- The vendored `cs50victor/claude-code-teams-mcp` (at `src/claude_teams/`) is NOT used by Claude Code's native Agent Teams — it's a parallel implementation for other MCP clients. Don't confuse the two.

## Team roster

| Name | Model | Responsibility |
|---|---|---|
| `lead` | opus | Coordinate research, synthesize findings, make the final call, draft the solution |
| `docs-scout` | sonnet | Anthropic docs, Claude Code changelogs, official primitives |
| `re-scout` | sonnet | Community RE posts, GitHub issues, Reddit/HN threads |
| `code-auditor` | sonnet | Deep-read `src/claude_anyteam/`, `src/claude_teams/`, hooks, installer |
| `experimenter` | sonnet | Hands-on spawn experiments — reproducible, logged, no code changes |

## Definition of "done"

We are done only when ALL of these hold:

1. We have a definitive answer to "when `Agent(team_name=..., name='codex-alice')` is called, what happens?" backed by at least 2 independent sources (docs + code OR RE + empirical).
2. We know which of Path A, Path B, or some third mechanism is the DEFAULT for frictionless Codex teammate spawning.
3. We know when each path SHOULD be used (user workflows: interactive dev, CI, long-running, etc.)
4. We have a clean solution — whether it's docs, a skill update, an installer change, a new spawn command, or a minimal repo patch — that makes the frictionless case always work.
5. The solution has been VALIDATED (either by rerunning the spawn and observing success, or by a reproducible test).
6. User signs off.

## Explicit non-goals

- Don't attempt to "fix" Claude Code itself — we can only change what's in claude-anyteam's repo + skills + installer.
- Don't do the Gemini adapter work. That's a separate thread. Focus is SPAWN MECHANISM ONLY.
- Don't rewrite the wrapper, the registration, or the loop. These are not in scope.

## Coordination protocol

- `lead` creates tasks via TaskCreate and assigns them (TaskUpdate owner=).
- Teammates claim assigned tasks and mark in_progress → completed.
- Teammates SendMessage the lead when they have findings or need unblocking.
- All findings go into `docs/internal/spawn-research-findings.md` (lead maintains, teammates append sections).
- No git commits without lead + user approval.
