---
name: help
description: Use proactively when the user wants help creating or managing Agent Teams teammates with claude-anyteam CLI backends.
when_to_use: User asks to create, route, or troubleshoot codex-* or gemini-* Agent Teams teammates.
---

claude-anyteam lets Claude Code route selected Agent Teams teammates to external CLI agents through the installed spawn shim.

## Routing conventions

- Names matching `^codex-` route to the Codex adapter (`claude-anyteam`).
- Names matching `^gemini-` route to the Gemini CLI adapter (`gemini-anyteam`).
- Other teammate names continue to launch native Claude teammates.
- The same Agent Teams `TeamCreate` / `Agent(...)` flow is used; only the teammate name prefix selects the backend.

## When to choose a backend

- Use `codex-*` for the most mature path, including Codex app-server mid-turn steering support. Codex teammates handle stateful multi-step work (implementer, executor, tester) reliably.
- Use `gemini-*` when the user specifically wants Gemini CLI models or wants a second non-Claude backend. Gemini supports both `--backend headless` and `--backend acp`; ACP supports `--trust default|plan` with a team-lead approval bridge and next-turn steer via `SendMessage(message={"type":"steer", ...})`.

### Role-fit guidance

- **Strong fit for `gemini-*`**: single-turn analysis, document review, code review with a written rubric, second-opinion passes — anything where the teammate produces one self-contained deliverable per turn.
- **Weak fit for `gemini-*` on older models (`gemini-2.5-*`)**: stateful executor roles like tester or implementer where the teammate must wait, observe, then act. Older Gemini drifts (re-runs finished work, ignores "stay parked", loses track of state). Prefer `gemini-3-pro-preview` for these — it's substantially better at orchestration. If forced to use 2.5, give it short, complete, self-contained dispatches and explicit disk-state checks.
- **Strong fit for `codex-*`**: stateful executor roles (implementer, tester) — the app-server backend handles tool loops well.

## Setting model and effort per teammate

The spawn shim reads `~/.claude/teams/<team>/agents/<name>.json` for per-teammate `model` and `effort` overrides and passes them as `--model X --effort Y` to the adapter. Missing file = adapter defaults. Write this file BEFORE calling `Agent(...)` for that teammate.

When the user asks for "best models and effort" or otherwise specifies model/effort intent:

- For each `codex-*` member, write `{"model": "gpt-5.5", "effort": "xhigh"}`.
- For each `gemini-*` member, write `{"model": "gemini-3-pro-preview", "effort": "xhigh"}`. The Gemini CLI's `Auto (Gemini 3)` UI label shows `gemini-3.1-pro` but that string is **not** a direct-API model — it only works via the CLI's auto-router. Pass `gemini-3-pro-preview` for explicit `--model` selection, or fall back to `gemini-2.5-pro` if Gemini 3 quota is exhausted.
- For each native Claude member, pass `model="opus"` directly to the `Agent(...)` call. Native Claude teammates use the host's Agent-tool model param, not the agent config file.

The user does not interact with the JSON files; the lead writes them as part of the spawn flow.

## Briefing teammates for orchestration and memory

External-CLI teammates (`codex-*`, `gemini-*`) don't share the lead's context. Effective dispatches give them:

1. **Self-contained instructions.** Don't reference earlier conversation; restate the goal, file paths, and exact deliverable in each message.
2. **Explicit disk-state checks.** Tell the teammate to run `git log --oneline -5`, `ls <dir>`, or read the relevant file BEFORE starting work. This prevents redoing finished work — especially important for older Gemini models.
3. **Memory access.** Both backends can read `~/.claude/projects/<project-slug>/memory/MEMORY.md` for persistent team context. For non-trivial roles, instruct the teammate to load `MEMORY.md` at the start of their first turn and save new persistent facts there.
4. **Task discipline.** Teammates should use `TaskList`/`TaskUpdate` to claim and progress tasks. Tell them: "Mark `in_progress` only when actually working, `completed` only when verifiably done. Don't move tasks while waiting for triggers."
5. **Park instructions.** When a teammate must wait (e.g., a tester waiting for an implementer's PR), tell them explicitly: "Do nothing — no file reads, no fixture setup, no progress reports. Idle until I message you with a go-signal."

## Patching agentType after spawn (current workaround)

The host `Agent(...)` tool spawn omits `agentType` from new member entries in `~/.claude/teams/<team>/config.json`. The teammate's MCP probe rejects the config on startup, breaking inter-teammate `SendMessage`. Until this is fixed, the lead must edit each new member entry to add `"agentType": "<role>"` (e.g. `"researcher"`, `"implementer"`, `"reviewer"`) right after the `Agent(...)` calls. A single Python pass over the config covers all members at once. Do this before expecting any teammate to message.

## Example

Mixed-backend team where every member runs at top effort:

```text
TeamCreate(team_name="build-team")

# 1. Write per-teammate agent configs BEFORE Agent(...) calls.
write ~/.claude/teams/build-team/agents/codex-implementer.json   {"model": "gpt-5.5", "effort": "xhigh"}
write ~/.claude/teams/build-team/agents/gemini-reviewer.json     {"model": "gemini-3-pro-preview", "effort": "xhigh"}

# 2. Spawn. Native Claude teammates take model via Agent's own param.
Agent(team_name="build-team", name="codex-implementer", prompt="Implement the patch.")
Agent(team_name="build-team", name="gemini-reviewer", prompt="Review from a Gemini perspective.")
Agent(team_name="build-team", name="claude-planner", model="opus", prompt="Plan the approach.")
Agent(team_name="build-team", name="reviewer", model="opus", prompt="Review the final result.")

# 3. Patch agentType on every new member entry in
#    ~/.claude/teams/build-team/config.json before expecting messaging to work.
```

If the user asks why a teammate did not route through claude-anyteam, check the prefix first: `codex-` and `gemini-` are the default routing regexes.
