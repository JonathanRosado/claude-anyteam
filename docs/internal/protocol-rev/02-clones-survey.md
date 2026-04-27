# 02 — Claude Code clone / reverse-engineering survey

**Date:** 2026-04-27  
**Owner:** codex-clones  
**Scope:** research-only survey of Claude Code CLI / Agent Teams reverse-engineering and clone projects, excluding `HarnessLab/claw-code-agent` because a peer is handling it. I also treated `Harzva/learn-likecc` as prior context from `docs/internal/spawn-research-findings.md`, not as a new find.

## Executive summary

- The highest-signal protocol references are not generic “Claude Code clones”; they are projects that either reimplement Agent Teams (`maorinka/claude-rs`, `cacaview/py-claw`) or exploit native Claude Code as the runtime while changing the model boundary (`Pickle-Pixel/HydraTeams`, `aproto9787/codex-bridge`).
- `Piebald-AI/claude-code-system-prompts` is the best prompt-contract reference: it captures the LLM-facing TeamCreate/Agent/TaskUpdate/SendMessage guidance, including idle semantics and peer-DM visibility expectations.
- `nwyin/claude-cleanroom-2.1.83` plus the nwyin Agent Teams post remains the clearest compact description of the file protocol (`~/.claude/teams`, inbox JSON arrays, task JSON files), but its process-model claims predate the in-process-default findings in `spawn-research-findings.md`.
- `codex-bridge` independently converged on our problem statement: route `codex-*` via `CLAUDE_CODE_TEAMMATE_COMMAND`, speak the file protocol, use Codex App Server / `turn/steer`, and emit status/idle messages. It is the closest same-problem comparator.
- `HydraTeams` is the architectural outlier worth rereading: by proxying `ANTHROPIC_BASE_URL`, it keeps the native Claude Code teammate process and therefore may inherit native visibility surfaces that wrappers lose.
- Monitor/dashboard projects (`claude-team-dashboard`, `amux`) are less useful for exact protocol RE but high-signal for the north-star: they show product demand for live agent status, peekable output, message categorization, and self-healing / stuck-session detection.

## Candidate 1 — maorinka/claude-rs

- **Repo:** https://github.com/maorinka/claude-rs
- **Last commit:** 2026-04-26T23:49:16Z
- **Stars:** 4
- **License:** MIT (`LICENSE` file)
- **Languages:** Rust, Python
- **Interesting file:** [`crates/claude-core/src/teams/backends.rs`](https://github.com/maorinka/claude-rs/blob/main/crates/claude-core/src/teams/backends.rs)

`claude-rs` is a Rust reimplementation of Claude Code internals, apparently source-map-informed but organized as a clean implementation with typed modules. Its Agent Teams surface is unusually rich: `crates/claude-core/src/teams/` contains `backends.rs`, `mailbox.rs`, `coordinator.rs`, `permission_sync.rs`, `spawn.rs`, and `types.rs`, and `crates/claude-tools/` includes task and send-message tool files. Methodologically, it has extracted the host’s backend split (`tmux` / pane vs in-process), the env vars (`CLAUDE_CODE_TEAMMATE_COMMAND`, color, plan mode), the file mailbox layout, and tool prompts/names. Ideas worth stealing: explicit typed spawn-result structures, a single backend enum, constants for host env vars, and a retrying lock abstraction around mailbox writes. Delta vs claude-anyteam: `claude-rs` tries to recreate the host; claude-anyteam is an adapter for external backends. We should not copy the rewrite strategy, but we should re-read the team modules against our `src/claude_teams/` models and B9’s visibility gaps.

## Candidate 2 — Pickle-Pixel/HydraTeams

- **Repo:** https://github.com/Pickle-Pixel/HydraTeams
- **Last commit:** 2026-02-08T17:29:20Z
- **Stars:** 62
- **License:** MIT (`LICENSE` file)
- **Languages:** TypeScript, JavaScript
- **Interesting file:** [`architecture/ARCHITECTURE.md`](https://github.com/Pickle-Pixel/HydraTeams/blob/main/architecture/ARCHITECTURE.md)

HydraTeams reverse-engineers the *model boundary* rather than the file protocol. Its core claim is that a native Claude Code teammate sends Anthropic Messages API requests, complete with system prompt, message history, and tool definitions; if `ANTHROPIC_BASE_URL` points at a translation proxy, a non-Claude model can produce tool calls while the native Claude Code process still executes tools. The extracted surfaces are the Anthropic request schema, SSE response schema, tool schema translation (`input_schema` → OpenAI function `parameters`), tool result mapping, and teammate/lead routing heuristics from system-prompt markers. Ideas worth stealing: a “native-host / routed-brain” mode could preserve native pane/TUI/tool visibility while swapping the model, which directly targets visibility parity. Delta vs claude-anyteam: anyteam replaces the teammate process with an external CLI and then reconstructs team protocol visibility; HydraTeams leaves the native teammate process in place and only translates model traffic. The risk is API-schema drift and exact event/tool-call ID fidelity.

## Candidate 3 — aproto9787/codex-bridge

- **Repo:** https://github.com/aproto9787/codex-bridge
- **Last commit:** 2026-04-07T13:00:29Z
- **Stars:** 5
- **License:** MIT (`LICENSE` file)
- **Languages:** JavaScript, Shell
- **Interesting file:** [`codex-bridge.mjs`](https://github.com/aproto9787/codex-bridge/blob/main/codex-bridge.mjs)

`codex-bridge` is the closest independent same-problem implementation. It routes agent names by prefix (`codex-*` to Codex, `claude-*` passthrough), expects Claude Code to call it via `CLAUDE_CODE_TEAMMATE_COMMAND`, parses `--agent-name` / `--team-name` / color / model flags, writes `~/.claude/teams` and `~/.claude/tasks`, and runs a Codex App Server session with `turn/steer` for live steering. It also introduces per-target message queues, 50ms micro-batching, result-file storage with inbox previews, status/idle protocol messages, native TUI recovery, and a `codex-bridge send` CLI for worker communication. Ideas worth stealing: durable full-output files plus short inbox previews; configurable worker policy in `teamagent.md`; more aggressive recovery around App Server / TUI lifecycle; and message-dedup/TTL tracking. Delta vs claude-anyteam: it is a single-file Node bridge with a bespoke send CLI, while anyteam exposes a narrowed MCP protocol surface to Codex. Its split-pane dependency shares the same Path-A fragility documented in spawn findings.

## Candidate 4 — Piebald-AI/claude-code-system-prompts

- **Repo:** https://github.com/Piebald-AI/claude-code-system-prompts
- **Last commit:** 2026-04-25T01:03:07Z
- **Stars:** 9,556
- **License:** MIT (`LICENSE` file)
- **Languages:** JavaScript plus Markdown prompt corpus
- **Interesting file:** [`system-prompts/tool-description-teammatetool.md`](https://github.com/Piebald-AI/claude-code-system-prompts/blob/main/system-prompts/tool-description-teammatetool.md)

Piebald’s repo is not a clone runtime; it is a versioned extraction of Claude Code’s prompts and tool descriptions. For protocol work, the key extraction is the LLM-facing TeamCreate/Agent Teams contract: create a team, create tasks, spawn teammates with `Agent` plus `team_name` and `name`, assign via `TaskUpdate(owner=...)`, rely on automatic message delivery, treat idle as normal, and expect peer-DM summaries in idle notifications. Ideas worth stealing: preserve these native expectations in our wrapper prompts; remind routed teammates to address by `name`, not `agentId`; and make “idle is normal” / “use SendMessage for team communication” explicit. Delta vs claude-anyteam: this is prompt/spec evidence, not runtime evidence. It should be used as the user-visible contract to compare against B9’s observed visibility gap: native prompt text promises peer DM visibility and automatic delivery, while routed backends only expose what our adapter forwards.

## Candidate 5 — nwyin/claude-cleanroom-2.1.83 + Agent Teams post

- **Repo:** https://github.com/nwyin/claude-cleanroom-2.1.83
- **Last commit:** 2026-03-26T02:28:14Z
- **Stars:** 0
- **License:** no LICENSE file found
- **Languages:** Markdown documentation
- **Interesting file:** [`ARCHITECTURE.md`](https://github.com/nwyin/claude-cleanroom-2.1.83/blob/main/ARCHITECTURE.md); companion article: https://nwyin.com/blogs/claude-code-agent-teams-reverse-engineered

nwyin’s cleanroom docs and article combine official docs, on-disk artifact observation, and binary analysis. The Agent Teams article is especially useful because it enumerates the file protocol: `~/.claude/teams/{team}/config.json`, per-agent inbox arrays, `~/.claude/tasks/{team}/{id}.json`, `.lock`, `.highwatermark`, task fields (`activeForm`, `blocks`, `blockedBy`), JSON-in-JSON inbox payloads, message types, and hook payloads like `TeammateIdle` / `TaskCompleted`. Ideas worth stealing: use a compact “coordination substrate” table in our docs; treat hook payloads as an observability surface; and preserve task dependency semantics. Delta vs claude-anyteam: the post predates the current in-process-default finding and says teammates are separate CLI processes, so use it for file/message schema and not for modern spawn path truth. B9/spawn findings supersede the process-model piece.

## Candidate 6 — SeifBenayed/cloclo

- **Repo:** https://github.com/SeifBenayed/cloclo
- **Last commit:** 2026-04-05T20:03:07Z
- **Stars:** 113
- **License:** MIT (`LICENSE` file)
- **Languages:** JavaScript, Rust, Python, Go, Shell, Java, HTML, C#, Ruby
- **Interesting file:** [`src/teams.mjs`](https://github.com/SeifBenayed/cloclo/blob/main/src/teams.mjs); context article: https://agent-wars.com/news/2026-03-24-reverse-engineered-claude-code-sdk-4-language-clis-pro-max-auth

Cloclo began as a reverse-engineered Claude Code SDK / runtime and now presents itself as a multi-agent substrate. Agent Wars reports that it maps subscription-auth/OAuth headers, streaming, tool calling, and an NDJSON bridge; the repo itself contains a generic `TaskBoard`, `TeamAgent`, message/artifact sharing, sub-agent definitions, provider normalization, and an AICL compact inter-agent language. Protocol surfaces extracted are mostly *agent-loop and transport* surfaces rather than Claude Agent Teams’ exact file protocol: NDJSON stdin/stdout, tool registry semantics, sub-agent prompts, provider-aware instruction placement, and shared task-board injection into prompts. Ideas worth stealing: a typed, line-oriented bridge for non-TUI automation; a compact status/handoff language for peer updates; and provider capability descriptors (`apiStyle`, `toolCallStyle`, `instructionPlacement`). Delta vs claude-anyteam: cloclo is a new runtime, not a native-team adapter, and its board is in-memory/runtime-owned rather than Claude’s `~/.claude/tasks` contract.

## Candidate 7 — musistudio/claude-code-router

- **Repo:** https://github.com/musistudio/claude-code-router
- **Last commit:** 2026-03-04T05:48:17Z
- **Stars:** 33,012
- **License:** MIT (`LICENSE` file)
- **Languages:** TypeScript, JavaScript, CSS, Shell, Dockerfile, HTML
- **Interesting file:** [`packages/core/src/server.ts`](https://github.com/musistudio/claude-code-router/blob/main/packages/core/src/server.ts)

Claude Code Router is not Agent Teams-specific, but it is the biggest public project built around Claude Code protocol mediation. It reverse-engineers the outbound `/v1/messages` model call surface and builds a provider/router/transformer layer: request routing, config APIs, SSE rewriting, tokenization, and transformers for Anthropic, OpenAI, Gemini, OpenRouter, Vertex, reasoning, max tokens, and tool use. The `tooluse` transformer is interesting because it forces tool mode with a synthetic `ExitTool`, then rewrites that tool call back into assistant content. Ideas worth stealing: a transformer registry for backend-specific protocol normalization; request/response logging as an observability baseline; and explicit model/provider routing separate from the team protocol. Delta vs claude-anyteam: router leaves Claude Code as the UI/runtime and rewrites model traffic, while anyteam replaces teammates with external CLIs. Router has strong model-surface knowledge, weak file/team-protocol knowledge.

## Candidate 8 — icebear0828/clio

- **Repo:** https://github.com/icebear0828/clio
- **Last commit:** 2026-04-03T06:14:43Z
- **Stars:** 177
- **License:** MIT (`LICENSE` file)
- **Languages:** TypeScript, JavaScript, Shell
- **Interesting file:** [`src/tools/teams.ts`](https://github.com/icebear0828/clio/blob/master/src/tools/teams.ts)

Clio is an open-source Claude Code-like CLI with TypeScript modules for tools, MCP, skills, settings, sessions, permissions, sandboxing, subagents, tasks, and teams. Its `teams.ts` is a small but readable in-memory team registry: teams have members, messages, statuses, and a message hook that injects `<team-messages>` into agent context. Tests cover creation, broadcast, filtering, status updates, and formatting. The extracted surface is not Claude’s exact Agent Teams protocol; it is a simplified clone of the coordination concepts. Ideas worth stealing: minimal unit tests for the message-filtering semantics, status formatting helpers, and the simple hook that converts pending peer messages into a context block. Delta vs claude-anyteam: clio owns the whole CLI and can make its own team model; anyteam must remain compatible with Claude’s disk protocol and host TUI expectations. It is useful as a clean conceptual model, not as a source of exact field names.

## Candidate 9 — mukul975/claude-team-dashboard

- **Repo:** https://github.com/mukul975/claude-team-dashboard
- **Last commit:** 2026-04-13T09:54:12Z
- **Stars:** 30
- **License:** MIT (`LICENSE` file)
- **Languages:** JavaScript, CSS, HTML
- **Interesting file:** [`src/utils/messageParser.js`](https://github.com/mukul975/claude-team-dashboard/blob/main/src/utils/messageParser.js)

This dashboard project targets real-time monitoring of Claude Code Agent Teams. It is a visibility product more than a protocol clone: Express/WebSocket server, chokidar file watching, React components for agent cards, inboxes, live communication, task dependency graph, timelines, activity stream, and message categorization. The strongest extracted surface is user-facing message taxonomy: `idle_notification`, `task_completed`, `task_assignment`, `shutdown_request/response`, `plan_approval_request/response`, question, coordination, completion, assignment, and system categories. Ideas worth stealing: normalize raw JSON/prose messages into human-readable categories before showing the lead; build a live activity/event model over task/inbox changes; and treat authentication / file permissions seriously for local dashboards. Delta vs claude-anyteam: dashboard reads and visualizes state; it does not route external models or guarantee exact native TUI parity. It reinforces B9’s north-star: leads want operational visibility, not just final prose.

## Candidate 10 — mixpeek/amux

- **Repo:** https://github.com/mixpeek/amux
- **Last commit:** 2026-04-26T10:08:57Z
- **Stars:** 154
- **License:** MIT + Commons Clause (`LICENSE` file)
- **Languages:** Python primary, plus mobile/web assets
- **Interesting file:** [`README.md`](https://github.com/mixpeek/amux/blob/main/README.md)

amux is an open-source control plane for running many Claude Code sessions via tmux, with a browser/mobile dashboard, kanban board, notes, channels, REST API orchestration, and self-healing watchdog. It does not appear to reimplement Agent Teams’ `~/.claude/teams` protocol; instead, it treats Claude Code sessions as tmux-managed processes and parses ANSI-stripped terminal output for status, while offering its own SQLite-backed task/board/channel API. Ideas worth stealing: live peek output, session cards, token/spend tracking, REST endpoints for `send` / `peek` / task claim, auto-compact/restart/replay watchdogs, and explicit “needs input / idle / working” session states. Delta vs claude-anyteam: amux is an external process manager/control plane, not a teammate adapter. It is high-value for visibility parity UX and watchdog patterns, but low-value for exact Agent Teams file schema.

## Candidate 11 — cacaview/py-claw

- **Repo:** https://github.com/cacaview/py-claw
- **Last commit:** 2026-04-24T16:07:56Z
- **Stars:** 0
- **License:** no LICENSE file found
- **Languages:** Python
- **Interesting file:** [`src/py_claw/swarm/in_process_runner.py`](https://github.com/cacaview/py-claw/blob/main/src/py_claw/swarm/in_process_runner.py)

`py-claw` is a Python Claude Code clone/port with many modules matching host concepts: messages, query engine, permissions, hooks, MCP, services, fork/process isolation, and a swarm in-process runner. The in-process runner explicitly says it is based on Claude Code’s `inProcessRunner.ts`, and it extracts context isolation via `contextvars`, `agent_id = name@team`, spawn config/result objects, task state fields (`type: in_process_teammate`, `is_idle`, `shutdown_requested`, `pending_user_messages`, `last_reported_tool_count`), and AppState registration. Ideas worth stealing: this is a compact, Python-native mirror of the in-process task state we need to reason about for visibility parity; its dataclasses are easier to compare with our Pydantic models than decompiled TS. Delta vs claude-anyteam: py-claw reimplements the host’s in-process runner; anyteam currently depends on an out-of-process routed backend and only self-registers enough file state to look native.

## Candidate 12 — JacquesGariepy/ClaudeCode-Reverse-Engineering

- **Repo:** https://github.com/JacquesGariepy/ClaudeCode-Reverse-Engineering
- **Last commit:** 2026-04-23T03:53:59Z
- **Stars:** 1
- **License:** no LICENSE detected by GitHub metadata
- **Languages:** JavaScript plus Markdown catalogs
- **Interesting file:** [`cfg/agent_team_dispatch.md`](https://github.com/JacquesGariepy/ClaudeCode-Reverse-Engineering/blob/main/cfg/agent_team_dispatch.md)

JacquesGariepy’s repo is a deep v2.1.117 reverse-engineering dump with catalogs, diffs, CFG notes, and internal analyses. It includes raw and beautified JS artifacts, so I did **not** vendor it, but `cfg/agent_team_dispatch.md` is high signal: it identifies callsites for `spawnInProcessTeammate`, `TaskCreate`, `TaskUpdate`, `TaskList`, `TaskStop`, `TaskOutput`, `SendMessage`, `TeammateIdle`, and the `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` gate; it also records pseudo-code for identity reservation, AppState task registration, flat-topology enforcement, permission polling, and the `<teammate-message>` envelope. Ideas worth stealing: a CFG-style “blocks and transitions” format for documenting native semantics; explicit failure/rollback paths; and naming current-version deltas vs earlier findings. Delta vs claude-anyteam: it documents native internals, not an external adapter. Use it for current in-process semantics, but verify every claim before encoding behavior.

## Candidate 13 — ruvnet/open-claude-code

- **Repo:** https://github.com/ruvnet/open-claude-code
- **Last commit:** 2026-04-24T04:06:05Z
- **Stars:** 221
- **License:** MIT listed in repo; GitHub API returned NOASSERTION
- **Languages:** JavaScript
- **Interesting file:** [`v2/src/agents/teams.mjs`](https://github.com/ruvnet/open-claude-code/blob/main/v2/src/agents/teams.mjs)

Open Claude Code markets itself as a clean-room, nightly decompiled-and-verified rebuild. Its README says the v2 implementation mirrors an async generator agent loop, tools, MCP transports, permissions, hooks, settings, sessions, and team/agent concepts, with nightly verification against Claude Code npm releases. The extracted surfaces are broad: agents loader/parser/teams, OAuth, env/settings, agent loop, MCP transports, permissions/sandbox, plugins, skills, and a `send-message` tool. Ideas worth stealing: verified regression pipeline against host releases, ADRs that track “path to parity,” and modular v2 layout separating core loop from tools/transports. Delta vs claude-anyteam: it is a replacement CLI, not a participant in native Agent Teams; it has much larger legal/provenance risk than docs-only or original adapter projects. I recommend reading for architectural patterns only, not vendoring or depending on it.

## What to vendor and re-read in depth

Vendored under `references/external-claude-code-re/` in this round:

1. `maorinka-claude-rs/` — full Rust reimplementation with the richest team backend/mailbox modules.
2. `pickle-pixel-hydrateams/` — model-translation proxy that may preserve native visibility by keeping native teammate processes.
3. `aproto-codex-bridge/` — same-problem Codex bridge using `CLAUDE_CODE_TEAMMATE_COMMAND`, Codex App Server, file IPC, status/idle messages.
4. `piebald-claude-code-system-prompts/` — prompt/tool-contract corpus for TeamCreate, Agent, TaskUpdate, SendMessage, idle, and peer-DM visibility wording.
5. `nwyin-claude-cleanroom-2-1-83/` — compact protocol primer and cleanroom orchestration docs; use for schema layout, not modern spawn truth.

Next reread order for visibility parity: first `aproto-codex-bridge` for practical event/status forwarding, then `HydraTeams` for the native-host/routed-brain alternative, then `maorinka/claude-rs` and `py-claw` for current in-process state fields, with Piebald/nwyin open as the user-facing protocol contract.
