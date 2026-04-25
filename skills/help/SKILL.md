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

- Use `codex-*` for the most mature path, including Codex app-server mid-turn steering support.
- Use `gemini-*` when the user specifically wants Gemini CLI models or wants a second non-Claude backend. Gemini supports both `--backend headless` and `--backend acp`; ACP supports `--trust default|plan` with a team-lead approval bridge and next-turn steer via `SendMessage(message={"type":"steer", ...})`.

## Setting model and effort per teammate

The spawn shim reads `~/.claude/teams/<team>/agents/<name>.json` for per-teammate `model` and `effort` overrides and passes them as `--model X --effort Y` to the adapter. Missing file = adapter defaults. Write this file BEFORE calling `Agent(...)` for that teammate.

When the user asks for "best models and effort" or otherwise specifies model/effort intent:

- For each `codex-*` member, write `{"model": "gpt-5.5", "effort": "xhigh"}`.
- For each `gemini-*` member, write `{"model": "gemini-2.5-pro", "effort": "xhigh"}`.
- For each native Claude member, pass `model="opus"` directly to the `Agent(...)` call. Native Claude teammates use the host's Agent-tool model param, not the agent config file.

The user does not interact with the JSON files; the lead writes them as part of the spawn flow.

## Patching agentType after spawn (current workaround)

The host `Agent(...)` tool spawn omits `agentType` from new member entries in `~/.claude/teams/<team>/config.json`. The teammate's MCP probe rejects the config on startup, breaking inter-teammate `SendMessage`. Until this is fixed, the lead must edit each new member entry to add `"agentType": "<role>"` (e.g. `"researcher"`, `"implementer"`, `"reviewer"`) right after the `Agent(...)` calls. A single Python pass over the config covers all members at once. Do this before expecting any teammate to message.

## Example

Mixed-backend team where every member runs at top effort:

```text
TeamCreate(team_name="build-team")

# 1. Write per-teammate agent configs BEFORE Agent(...) calls.
write ~/.claude/teams/build-team/agents/codex-implementer.json   {"model": "gpt-5.5", "effort": "xhigh"}
write ~/.claude/teams/build-team/agents/gemini-reviewer.json     {"model": "gemini-2.5-pro", "effort": "xhigh"}

# 2. Spawn. Native Claude teammates take model via Agent's own param.
Agent(team_name="build-team", name="codex-implementer", prompt="Implement the patch.")
Agent(team_name="build-team", name="gemini-reviewer", prompt="Review from a Gemini perspective.")
Agent(team_name="build-team", name="claude-planner", model="opus", prompt="Plan the approach.")
Agent(team_name="build-team", name="reviewer", model="opus", prompt="Review the final result.")

# 3. Patch agentType on every new member entry in
#    ~/.claude/teams/build-team/config.json before expecting messaging to work.
```

If the user asks why a teammate did not route through claude-anyteam, check the prefix first: `codex-` and `gemini-` are the default routing regexes.
