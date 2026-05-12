---
name: help
description: Use proactively when the user wants help creating, observing, or managing Agent Teams teammates with claude-anyteam CLI backends. Covers the required spawn setup (`team-agent` before `Agent(...)`, otherwise bare codex-/gemini-/kimi- prefixes are refused), post-spawn repair (`team-patch`, `team-roster`), teardown (`team-kill`), observability (`status`, `diagnose`, `visibility-tail`), and the codex-app-server native tool surface (imagegeneration, websearch, imageview, filechange) that lives outside the wrapper-MCP layer. Prefer this skill over ad-hoc Write/Edit/Bash against `~/.claude/teams/` and over reaching for `codex-jr:codex-rescue` when the user wants a real teammate.
when_to_use: User asks to create, route, configure, or troubleshoot codex-*, gemini-*, or kimi-* Agent Teams teammates; OR the lead is about to spawn one of those prefixes without first running `claude-anyteam team-agent`; OR the lead is about to write per-teammate model/effort config files or patch agentType after spawn; OR the user says "spin up codex teammates", "spawn codex team", "team of codex/gemini/kimi", or any phrase that implies multiple monitorable / pingable teammates rather than a one-shot subagent; OR the user asks "is the team healthy", "how is teammate X doing", "what is the team working on", "check on the team", "any progress from <name>", or otherwise wants to observe a running team; OR the user wants codex to generate images, do model-side web search, view an image, or use any other codex-app-server-native tool; OR a lead is about to reach for `Agent(subagent_type="codex-jr:codex-rescue")` for what is actually team-shaped work; OR the user wants to forcibly tear down a whole team via `team-kill`.
---

claude-anyteam lets Claude Code route selected Agent Teams teammates to external CLI agents through the installed spawn shim.

## Commands at a glance

The plugin ships three kinds of CLI surface; reach for the one that matches the intent.

| Surface         | Command                                  | Use when                                                                  |
|-----------------|------------------------------------------|---------------------------------------------------------------------------|
| Spawn / config  | `claude-anyteam team-agent <name> ...`   | Required BEFORE `Agent(...)` for codex-/gemini-/kimi- names (writes the agent file) |
| Spawn / config  | `claude-anyteam team-patch ...`          | Patch `agentType` on routed members AFTER `Agent(...)` calls land         |
| Spawn / config  | `claude-anyteam team-roster --team T`    | Inspect roster (members, backends, effort, capability versions)           |
| Teardown        | `claude-anyteam team-kill --team T --force [--purge]` | Stop all teammates quickly before creating a fresh team                    |
| Observe         | `claude-anyteam status [--team T]`       | One-screen team snapshot — roster, overrides, incidents, last activity    |
| Observe         | `claude-anyteam diagnose --team T`       | Deeper substrate inspection: manifests, visibility-degraded, MCP probe    |
| Observe         | `claude-anyteam visibility-tail --team T`| Follow the live `VisibilityEvent` JSONL stream as work happens            |
| Lifecycle       | `claude-anyteam install`                 | Re-install the spawn shim / repair `settings.json` drift                  |

When the user asks "is the team healthy?" or "what's teammate X doing right now?", the answer is *not* file-system probing — it's `claude-anyteam status` first, then `claude-anyteam diagnose --team <team>`, then `claude-anyteam visibility-tail` to follow live activity. See the `claude-anyteam:diagnose` skill for the deep-inspection flow.

When the user asks to tear down a team, kill all teammates, or free the substrate before creating a new team, use `claude-anyteam team-kill --team <team> --force`. Add `--purge` only when they want the team config and tasks directory removed too. This is the supported fast path; prefer it over ad-hoc tmux kills or hand-editing `~/.claude/teams`.

## Disambiguation: claude-anyteam vs codex-jr

If both `claude-anyteam` and `codex-jr` are installed, the choice is intent-based, not name-based:

- **claude-anyteam** is for *teammates*: multi-task, monitorable, pingable, persistent in `TaskList` and the presence line, addressable by name via `SendMessage`, mid-turn-steerable. Reach for it when the user says "team", "teammates", "spawn N codex/gemini/kimi", "implementer + tester + reviewer", or implies any orchestrated multi-agent flow.
- **codex-jr (`Agent(subagent_type="codex-jr:codex-rescue")`)** is a one-shot rescue subagent — fire-and-forget, no `TaskList` entry, no persistent presence, no peer messaging. Reach for it only for genuine one-shot rescue passes (its own description says: "should hand a substantial coding task to Codex through the shared runtime").

Failure mode: if the lead reaches for `codex-jr:codex-rescue` when the user wanted a team, the spawned agents have no entry in `TaskList`, no `SendMessage`-able names, and no presence-line visibility — i.e. *the resulting "team" isn't a team at all*. The rescue agent's own surface is `Bash` only; it forwards prompts to `codex-companion.mjs`, which itself runs codex app-server. Even when forwarding works and App Server tools are technically reachable, the rescue agent is one-shot fire-and-forget — no `TaskList` entry, no `SendMessage`-able name, no peer monitoring, no mid-task steering, no roster — so a multi-step or coordinated workflow ("implement + test + review", "spawn N codex teammates", "is the team healthy?") simply has no substrate underneath it. The user's #35 report observed the rescue agent in a team-shaped image-generation context attempting `curl api.openai.com/v1/images/generations` directly (i.e. bypassing forwarding) and failing with missing `OPENAI_API_KEY`; that is one symptom of how team-shaped intent does not survive the rescue path's prompt-shaping and one-shot lifecycle. The exact failure depends on what the rescue agent's model chooses to do with the prompt; the **structural property** is "codex-rescue is a one-shot subagent, not a teammate," which is independent of any specific tool name or error string.

## Routing conventions

- Names matching `^codex-` route to the Codex adapter (`claude-anyteam`).
- Names matching `^gemini-` route to the Gemini CLI adapter (`gemini-anyteam`).
- Names matching `^kimi-` route to the Kimi CLI adapter (`kimi-anyteam`).
- Other teammate names continue to launch native Claude teammates.
- The same Agent Teams `TeamCreate` / `Agent(...)` flow is used; only the teammate name prefix selects the backend.
- Guardrail: routed prefixes require a matching `~/.claude/teams/<team>/agents/<name>.json` created by `claude-anyteam team-agent` before `Agent(...)`. If the file is missing, the spawn shim soft-refuses the bare prefix with an actionable error instead of silently falling back to a native Claude teammate. Advanced override: set `CLAUDE_ANYTEAM_ALLOW_BARE_PREFIX=1` only when intentionally using adapter defaults without a per-teammate file.

## When to choose a backend

- Use `codex-*` for the most mature path, including Codex app-server mid-turn steering support. Codex teammates handle stateful multi-step work (implementer, executor, tester) reliably.
- Use `gemini-*` when the user specifically wants Gemini CLI models or wants a second non-Claude backend. Gemini supports both `--backend headless` and `--backend acp`; ACP supports `--trust default|plan` with a team-lead approval bridge and next-turn steer via `SendMessage(message={"type":"steer", ...})`.
- Use `kimi-*` for architectural-stretch tasks, very large-context reviews, or work that benefits from Kimi's native skills and internal swarm/subagent primitives. Kimi v1 is headless only inside claude-anyteam: no Codex App Server, no live `turn/steer`, and no CLI `--output-schema` support; structured outputs are prompt-plus-validation.

### Role-fit guidance

- **Strong fit for `kimi-*`**: architecture research, large-repo orientation, migration plans, second-opinion design review, broad context synthesis, and tasks where Kimi's native skills/swarm may help internally while still reporting back as one anyteam teammate.
- **Weak fit for `kimi-*`**: tasks that require Codex-style mid-turn steering, exact schema-constrained CLI output, or Gemini ACP permission bridging. Kimi receives steer at the next prompt boundary in v1.
- **Strong fit for `gemini-*`**: single-turn analysis, document review, code review with a written rubric, second-opinion passes — anything where the teammate produces one self-contained deliverable per turn.
- **Weak fit for `gemini-*` on older models (`gemini-2.5-*`)**: stateful executor roles like tester or implementer where the teammate must wait, observe, then act. Older Gemini drifts (re-runs finished work, ignores "stay parked", loses track of state). Prefer `gemini-3-pro-preview` for these — it's substantially better at orchestration. If forced to use 2.5, give it short, complete, self-contained dispatches and explicit disk-state checks.
- **Strong fit for `codex-*`**: stateful executor roles (implementer, tester) — the app-server backend handles tool loops well.

## Native (harness-side) tools beyond the wrapper-MCP surface

Routed teammates may carry harness-native tools that live OUTSIDE the wrapper-MCP layer — Codex App Server tools like `imagegeneration` / `imageview` / `websearch` / `filechange` are the most consequential example. The model decides when to invoke them based on prompt content; no `agentType` patch or extra config is required. They are NOT exposed by the wrapper MCP, so they do **not** appear in any `mcp_anyteam_*` tool listing — but they DO appear in event-log telemetry, `visibility-tail`, and `diagnose`.

### Discover what's available — query the manifest

**The canonical inventory of native host tools is the per-teammate capability manifest at runtime, not any list in this skill.** Per north-star §1 (capability declarations flow per-backend, not flattened), each backend declares its own native inventory natively, peers and leads discover it by querying the manifest, and any prose enumeration here is at best a v0.8.0 snapshot that will go stale as backends evolve.

Ask the peer's manifest:

```text
mcp_anyteam_capability_manifest('<routed-teammate>', 'live_tool_events')
```

The response carries:

- `host_tool_surface` — a label string (e.g. `"codex-native"`) identifying the harness's native tool surface.
- `native_host_tools` — the authoritative array of harness-native tool names available beyond the wrapper MCP. Read THIS as the source of truth. As a v0.8.0 illustrative snapshot, a `codex-*` teammate on the App Server backend currently declares `["imagegeneration", "imageview", "websearch", "filechange"]` — but treat that as a hint, not a contract; trust whatever the live manifest reports for the specific teammate you are about to invoke.

If a teammate's manifest is missing the field, treat it as "no native host tools advertised" — fall back to wrapper-MCP equivalents (`mcp_anyteam_web_fetch`, `mcp_anyteam_write_file`, etc.). Do not assume the inventory; always query.

Other backend adapters (`kimi-*`, `gemini-*`, native-Claude stress teammates) declare their own surfaces (`kimi-native`, `mcp_anyteam`, `claude-code-native(...)+mcp_anyteam`) and may or may not populate `native_host_tools` in v0.8.0. The same lookup pattern applies — query the manifest, don't assume.

### Prompting a teammate to use a native tool

Once the manifest confirms a tool is available, prompt the teammate naturally — the harness's model decides when to invoke. For codex-app-server `imagegeneration` (authenticated via the existing `codex login` ChatGPT session, **no `OPENAI_API_KEY` required**):

> Use `imagegeneration` to produce a 1024×768 PNG of [concept]; save to `<path>` and reference it as `![caption](<path>)` in `<chapter file>`.

For `websearch` (model-side fresh web search, distinct from `mcp_anyteam_web_fetch` which fetches a known URL):

> Use `websearch` to find the current state of [topic], then summarize the top three findings with citations.

### When to reach for native tools vs wrapper-MCP equivalents

- For **binary/image output**, use the codex-app-server `imagegeneration` tool — the wrapper has no image-generation surface, and there's no host CLI fallback that beats codex-native here.
- For **fresh model-side web search**, prefer the harness's native `websearch`; for a known URL fetch, prefer wrapper-MCP `mcp_anyteam_web_fetch`.
- For **most file writes inside the lead's project tree**, prefer wrapper-MCP `mcp_anyteam_write_file` — it goes through the audited shadow-tool path. Reach for `filechange` only when you specifically want App-Server-native semantics.

## Setting model and effort per teammate

**Use the `claude-anyteam team-agent` CLI, not raw file writes.** The spawn shim reads `~/.claude/teams/<team>/agents/<name>.json` for per-teammate `model` and `effort` overrides and passes them as `--model X --effort Y` to the adapter; the CLI is the typed contract for writing those files. Run it BEFORE calling `Agent(...)` for that teammate. Missing file = bare prefix soft-refuse for `codex-*` / `gemini-*` / `kimi-*` unless `CLAUDE_ANYTEAM_ALLOW_BARE_PREFIX=1` is set deliberately.

```bash
claude-anyteam team-agent codex-implementer --team build-team --model gpt-5.5 --effort xhigh
claude-anyteam team-agent gemini-reviewer   --team build-team --model gemini-3-pro-preview --effort xhigh
claude-anyteam team-agent kimi-architect    --team build-team --model kimi-code/kimi-for-coding --effort xhigh
```

When the user asks for "best models and effort" or otherwise specifies model/effort intent:

- For each `codex-*` member: `claude-anyteam team-agent <name> --team <team> --model gpt-5.5 --effort xhigh`.
- For each `gemini-*` member: `claude-anyteam team-agent <name> --team <team> --model gemini-3-pro-preview --effort xhigh`. The Gemini CLI's `Auto (Gemini 3)` UI label shows `gemini-3.1-pro` but that string is **not** a direct-API model — it only works via the CLI's auto-router. Pass `gemini-3-pro-preview` for explicit `--model` selection, or fall back to `gemini-2.5-pro` if Gemini 3 quota is exhausted.
- For each `kimi-*` member: `claude-anyteam team-agent <name> --team <team> --model kimi-code/kimi-for-coding --effort xhigh`. This is the probed Kimi default user-facing model slug (`display_name = "Kimi-k2.6"`, 262k context). Kimi effort is lossy: `minimal`/`low` map to `--no-thinking`; `medium`/`high`/`xhigh` leave thinking on.
- For each native Claude member, pass `model="opus"` directly to the `Agent(...)` call. Native Claude teammates use the host's Agent-tool model param, not the agent config file.

The user does not interact with the CLI directly; the lead invokes it as part of the spawn flow. To remove a config: `claude-anyteam team-agent <name> --team <team> --remove`.

## Briefing teammates for orchestration and memory

External-CLI teammates (`codex-*`, `gemini-*`, `kimi-*`) don't share the lead's context. Effective dispatches give them:

1. **Self-contained instructions.** Don't reference earlier conversation; restate the goal, file paths, and exact deliverable in each message.
2. **Explicit disk-state checks.** Tell the teammate to run `git log --oneline -5`, `ls <dir>`, or read the relevant file BEFORE starting work. This prevents redoing finished work — especially important for older Gemini models.
3. **Memory access.** All backends can read `~/.claude/projects/<project-slug>/memory/MEMORY.md` for persistent team context. For non-trivial roles, instruct the teammate to load `MEMORY.md` at the start of their first turn and save new persistent facts there.
4. **Task discipline.** Teammates should use `TaskList`/`TaskUpdate` to claim and progress tasks. Tell them: "Mark `in_progress` only when actually working, `completed` only when verifiably done. Don't move tasks while waiting for triggers."
5. **Park instructions.** When a teammate must wait (e.g. a tester waiting for an implementer's PR), tell them explicitly: "Do nothing — no file reads, no fixture setup, no progress reports. Idle until I message you with a go-signal."

## Patching agentType after spawn

The host `Agent(...)` tool spawn omits `agentType` from new member entries in `~/.claude/teams/<team>/config.json` (it writes `agentType="general-purpose"`). The teammate's MCP probe expects `agentType="claude-anyteam"` for routed-adapter members; without the patch, inter-teammate `SendMessage` breaks. Use the CLI:

```bash
claude-anyteam team-patch --team build-team --all-external      # patches every codex-/gemini-/kimi- member
claude-anyteam team-patch codex-alice --team build-team          # or one at a time
```

Run this once after all `Agent(...)` calls land. The CLI is idempotent — re-running on already-patched members is a no-op.

## Inspecting team state

To see the current roster without reading and parsing config.json by hand:

```bash
claude-anyteam team-roster --team build-team           # human-readable
claude-anyteam team-roster --team build-team --json    # JSON array for scripted callers
```

## Observing a running team (run-time surface)

When the user asks "is the team healthy?", "what's the team working on?", "how is teammate X doing?", or "any progress from <name>?" — the answer comes from the run-time surface, not from `team-roster` (which is a static snapshot) or from OS-level process inspection. Reach for, in order:

1. **`claude-anyteam status [--team <team>]`** — one-screen team snapshot: roster + adapter overrides + recent incidents + last-activity per member. Best first poke.
2. **`claude-anyteam diagnose --team <team>`** — substrate inspection: capability-manifest cache freshness, recent `visibility_degraded` events, wrapper-MCP tool-discovery diagnostics, substrate-health checklist. Use when `status` shows yellow/red. The `claude-anyteam:diagnose` skill walks through the report.
3. **`claude-anyteam visibility-tail --team <team> [--agent <name>]`** — follow the live `VisibilityEvent` JSONL stream as work happens. Useful for "what is teammate X doing right now?" and for catching tool calls / errors as they fire.

`visibility-tail` is a follower by default — it streams future events. For a one-shot "what's happened recently?" probe, prefer `status` or `diagnose --since=<iso-time>`. Wrapping `visibility-tail` in a `timeout` works, but understand it may capture zero events in a short window if no tool fires in that interval.

The surface is intentionally projector-style over the canonical `events/*.jsonl` substrate — read-only by default. The one mutating flag is `claude-anyteam diagnose --instrument-spawn`, which writes `env.CLAUDE_ANYTEAM_WRAPPER_MCP_DIAGNOSTICS=1` to `~/.claude/settings.json` so the next teammate spawn captures wrapper-MCP tool-discovery diagnostics. Use it only when explicitly asked.

### Healthy-team checklist (one-glance)

`claude-anyteam diagnose` is the canonical "is my team healthy?" answer; the dedicated `claude-anyteam:diagnose` skill walks through its report. As a quick reference for what *should* be true of every routed (`codex-*` / `gemini-*` / `kimi-*`) teammate, look in `claude-anyteam team-roster --team <team> --json` for these registration fields:

- `registration_status: "upgraded"` — the adapter took over from the placeholder spawn.
- `agent_type: "claude-anyteam"` — `team-patch` was run after spawn.
- `backend_type: "in-process"` — the routed adapter is registered.
- `capability_version: "2"` — current rich-manifest revision.
- `adapter_model`, `adapter_effort`, `adapter_turn_timeout_s` — per-teammate overrides flowed through.

And `live_tool_events` (when present) plus the typed-capability set Codex App Server advertises — `turn_steer`, `thread_fork`, `live_tool_events`, `structured_output`, `plan_mode`, `soft_non_progress_watchdog` — should appear in the cheap roster `capabilities` list. Any deviation (e.g. `agent_type: "general-purpose"`, missing capabilities, stale `capability_version`) is a signal to re-run `team-patch` or to dig deeper with `claude-anyteam diagnose`.

## Example

Mixed-backend team where every member runs at top effort:

```text
TeamCreate(team_name="build-team")

# 1. Write per-teammate agent configs BEFORE Agent(...) calls.
claude-anyteam team-agent codex-implementer --team build-team --model gpt-5.5 --effort xhigh
claude-anyteam team-agent gemini-reviewer   --team build-team --model gemini-3-pro-preview --effort xhigh
claude-anyteam team-agent kimi-architect    --team build-team --model kimi-code/kimi-for-coding --effort xhigh

# 2. Spawn. Native Claude teammates take model via Agent's own param.
Agent(team_name="build-team", name="codex-implementer", prompt="Implement the patch.")
Agent(team_name="build-team", name="gemini-reviewer", prompt="Review from a Gemini perspective.")
Agent(team_name="build-team", name="kimi-architect", prompt="Do large-context architecture review.")
Agent(team_name="build-team", name="claude-planner", model="opus", prompt="Plan the approach.")
Agent(team_name="build-team", name="reviewer", model="opus", prompt="Review the final result.")

# 3. Patch agentType on every new routed-adapter member in one call.
claude-anyteam team-patch --team build-team --all-external

# 4. (Optional) Confirm the roster looks right.
claude-anyteam team-roster --team build-team

# 5. Dispatch a codex-native image-generation pass on a codex-* teammate.
Agent(team_name="build-team", name="codex-illustrator",
      prompt="Use imagegeneration to produce a 1024x768 PNG of the FSDP collective topology; save to ./_images/fsdp.png; embed `![FSDP topology](_images/fsdp.png)` in chapters/04-fsdp.md replacing any existing diagram.")

# 6. Observe the team while work is in flight.
claude-anyteam status --team build-team
claude-anyteam visibility-tail --team build-team   # follow live tool events
```

If the user asks why a teammate did not route through claude-anyteam, check the prefix first: `codex-`, `gemini-`, and `kimi-` are the default routing regexes. If the user reached for `Agent(subagent_type="codex-jr:codex-rescue")` and ended up without a real team (no `TaskList` entries, no `SendMessage`-able names), see the "Disambiguation: claude-anyteam vs codex-jr" section above and re-spawn through the claude-anyteam flow.

## Why the CLI exists

Earlier versions of this skill instructed leads to write the JSON config files and patch `config.json` by hand. That worked but left a wide blast radius — the lead had to know the magic file paths, JSON shape, and which post-spawn fixups to apply. The `claude-anyteam team-*` subcommands are a typed contract: they validate inputs, write atomically, are idempotent, and — most importantly — are allowlisted in the installer (`Bash(claude-anyteam team-* *)`) so they never trigger permission prompts. **Future sessions: prefer the CLI over `Write`/`Edit`/`Bash` against `~/.claude/teams/`.**
