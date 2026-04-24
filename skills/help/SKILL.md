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
- Use `gemini-*` when the user specifically wants Gemini CLI models or wants a second non-Claude backend. Gemini currently uses headless `gemini -p ... --output-format stream-json`; it does not have Codex app-server `turn/steer` parity.

## Example

Create a mixed team with explicit prefixes:

```text
TeamCreate(
  team_name="build-team",
  agents=[
    Agent(name="codex-implementer", prompt="Implement the patch."),
    Agent(name="gemini-reviewer", prompt="Review the patch from a Gemini perspective."),
  ],
)
```

If the user asks why a teammate did not route through claude-anyteam, check the prefix first: `codex-` and `gemini-` are the default routing regexes.
