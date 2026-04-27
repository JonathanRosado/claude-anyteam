# 01 — claw-code-agent findings

**Author:** codex-clawclone  
**Date:** 2026-04-27  
**For:** team-lead / proto-rev  
**Vendor path:** `references/external-claude-code-re/clawcode`  
**Scope:** research-only; no engineering changes.

## Executive summary

- **Claude Code-related, but not Agent Teams protocol-related.** `claw-code-agent` is a Python reimplementation of Claude Code's npm agent architecture for local/OpenAI-compatible models, not an adapter for Claude Code's on-disk Agent Teams contract (`~/.claude/teams/{team}/config.json`, per-agent inboxes, task claims, idle/shutdown) documented in `docs/architecture.md:11-13`.
- **Their “team” and “task” surfaces are local analogues.** They persist teams/messages in `.port_sessions/team_runtime.json` and tasks in `.port_sessions/task_runtime.json` (`references/external-claude-code-re/clawcode/src/team_runtime.py:11`, `references/external-claude-code-re/clawcode/src/task_runtime.py:14`), so none of the native Claude lead visibility/presence machinery is exercised.
- **Best visibility idea to steal:** a backend-neutral event taxonomy (`tool_start`, `tool_delta`, `tool_permission_denial`, `tool_result`) plus raw streaming events; this lines up with the B9 four-channel/event-envelope proposal (`bug-triage/B9-visibility-parity-investigation.md:571-588`, `bug-triage/B9-visibility-parity-investigation.md:590-630`).
- **Best lifecycle idea to steal:** their `AgentManager` child/group summaries and delegated-subtask result events would map cleanly into our future event log and task `activeForm` fan-out.
- **Do not import their protocol model.** It lacks mailbox polling, file locks/atomic task claims, lead `task_complete` messages, idle notifications, shutdown, or native TUI `AppState.tasks` presence. It is useful as inspiration for observability and local-model routing, not as Agent Teams RE evidence.
- **Legal/maturity caution:** no `LICENSE` file and no `license` field in `pyproject.toml`; README badges claim “open-source”/“zero dependencies”, but the package declares FastAPI/Uvicorn/Pydantic dependencies (`references/external-claude-code-re/clawcode/README.md:16-18`, `references/external-claude-code-re/clawcode/pyproject.toml:35-38`).

## Project overview

| Field | Finding |
|---|---|
| Repository | `https://github.com/HarnessLab/claw-code-agent` cloned at `references/external-claude-code-re/clawcode` |
| Last cloned commit | `816bc3a2591910f4dd569e3b1fe24c35280abc5e` — 2026-04-23T01:09:27+02:00 — Abdelrahman Abdallah — `Merge pull request #31 from HarnessLab/update_17_4` |
| Language/runtime | Python 3.10+ package (`references/external-claude-code-re/clawcode/pyproject.toml:5-10`) with a browser GUI; project scripts are `claw-code-agent` and `claw-code-gui` (`references/external-claude-code-re/clawcode/pyproject.toml:45-47`). |
| Claimed purpose | “Python reimplementation of the Claude Code agent architecture” (`references/external-claude-code-re/clawcode/README.md:8`) for local open-source models via an OpenAI-compatible API (`references/external-claude-code-re/clawcode/README.md:91-95`). |
| Maturity | Alpha: README status badge and pyproject classifier (`references/external-claude-code-re/clawcode/README.md:17`, `references/external-claude-code-re/clawcode/pyproject.toml:23-24`). |
| License | Ambiguous. README badge says `license-open-source`, but there is no `LICENSE` file in the clone and `pyproject.toml` has no license metadata (`references/external-claude-code-re/clawcode/README.md:18`, `references/external-claude-code-re/clawcode/pyproject.toml:5-44`). |
| Size | `src`: 91 Python files / 35,007 lines. Tests: 77 Python files / 17,596 lines. Benchmarks: 26 Python files / 6,521 lines. GUI static: 3 files / 4,211 lines. Code-ish total including md/json/js/css/html/toml/sh: 208 files / 69,614 lines. |
| Dependencies | README repeatedly claims zero external dependencies (`references/external-claude-code-re/clawcode/README.md:16`, `references/external-claude-code-re/clawcode/README.md:97`, `references/external-claude-code-re/clawcode/README.md:141`), but package metadata declares `fastapi`, `uvicorn`, and `pydantic` (`references/external-claude-code-re/clawcode/pyproject.toml:35-38`). |

## RE methodology assessment

### What they appear to have done

- The README says this is not a distribution of original npm source, but a Python reimplementation of the full agent flow: prompt assembly, context building, slash commands, tool calling, session persistence, and local model execution (`references/external-claude-code-re/clawcode/README.md:91-99`, `references/external-claude-code-re/clawcode/README.md:896-900`).
- The project explicitly tracks parity against “npm `src`” (`references/external-claude-code-re/clawcode/README.md:888-892`). The parity checklist says it is functionality-oriented, not a line-by-line equivalence claim, and lists the Python runtime files that are actually active (`references/external-claude-code-re/clawcode/PARITY_CHECKLIST.md:1-5`).
- Their reference-data snapshots record an archived TypeScript-like tree at `archive/claude_code_ts_snapshot/src`, with 1,902 TS-like files, 207 command entries, and 184 tool entries (`references/external-claude-code-re/clawcode/src/reference_data/archive_surface_snapshot.json:1-63`). The parity audit points at that local archive (`references/external-claude-code-re/clawcode/src/parity_audit.py:7-11`).
- Tool snapshots retain source hints into archived Claude Code-style TypeScript paths, including task tools and `tools/shared/spawnMultiAgent.ts` (`references/external-claude-code-re/clawcode/src/reference_data/tools_snapshot.json:708-790`, `references/external-claude-code-re/clawcode/src/reference_data/tools_snapshot.json:903-910`).

### What I did *not* find

- No `bunfs`, sourcemap, or `strings(1)` extraction workflow in the cloned source. Search hits are parity/inventory snapshots, not a reproducible extraction pipeline.
- The actual `archive/claude_code_ts_snapshot/src` tree referenced by `parity_audit.py` is absent from the shallow clone, so the bundled code cannot reproduce its own upstream diff without out-of-band data.
- This is **not clean-room in the strict sense**: it is openly driven by npm-source parity snapshots/source hints. It may be acceptable as external inspiration, but team-lead should not treat it as independent confirmation of Agent Teams wire/file protocol details.

## Protocol surfaces they expose

### Baseline: what claude-anyteam needs to speak

Our target protocol is file-first: `config.json` and per-agent inboxes under `~/.claude/teams/{team}`, with mailbox polling, atomic task claims, idle notifications, and shutdown requests (`docs/architecture.md:11-13`). TUI visibility requires more than files: the leader presence row is driven by live `AppState.tasks`, not passive config edits (`docs/architecture.md:85-96`, `docs/internal/2026-prototype/research.md:25-34`). B9 reframes visibility as four channels — stderr, lead mailbox, task state, and an append-only event log (`bug-triage/B9-visibility-parity-investigation.md:571-588`).

### File shapes

| Surface | `claw-code-agent` shape | Agent Teams delta |
|---|---|---|
| Team state | Single workspace-local `.port_sessions/team_runtime.json` (`references/external-claude-code-re/clawcode/src/team_runtime.py:11`). `TeamDefinition` has `name`, optional `description`, `members`, `metadata`, `created_at` (`references/external-claude-code-re/clawcode/src/team_runtime.py:18-24`). Runtime loads manifests and that one state file (`references/external-claude-code-re/clawcode/src/team_runtime.py:121-162`). | Not `~/.claude/teams/{team}/config.json`; no Claude member schema, `backendType`, lead/member roles, or native registration semantics. |
| Team manifests | Discovers `.claw-teams.json`, `.claw-team.json`, and `.claude/teams.json` in cwd/additional dirs (`references/external-claude-code-re/clawcode/src/team_runtime.py:333-347`). | Similar naming, but not the runtime `~/.claude/teams/{team}` contract. |
| Mail/messages | `TeamMessage` fields are `message_id`, `team_name`, `sender`, `text`, optional `recipient`, `metadata`, `created_at` (`references/external-claude-code-re/clawcode/src/team_runtime.py:64-72`). Sending appends to the global messages array and persists the same state file (`references/external-claude-code-re/clawcode/src/team_runtime.py:231-253`). | No per-teammate inbox files, no read/unread cursor, no structured `task_complete`/idle/permission envelopes, no lead-visible Claude mailbox semantics. |
| Task state | Single workspace-local `.port_sessions/task_runtime.json` (`references/external-claude-code-re/clawcode/src/task_runtime.py:14`). `PortingTask` includes `task_id`, `title`, `status`, `description`, `priority`, `active_form`, `owner`, dependencies, metadata, timestamps (`references/external-claude-code-re/clawcode/src/task.py:17-34`). | Not `~/.claude/tasks/{team}/{id}.json`; no CAS/lock claim protocol. |
| Persistence safety | Team state writes JSON directly with `write_text` (`references/external-claude-code-re/clawcode/src/team_runtime.py:321-330`). Task runtime also writes direct JSON, although it computes before/after SHA previews (`references/external-claude-code-re/clawcode/src/task_runtime.py:470-504`). | Unsafe for multi-process teammates; claude-anyteam's task loop relies on file locks/compare-and-set for atomic claims (`docs/architecture.md:100-106`). |

### RPC surfaces

| Surface | Findings | Agent Teams relevance |
|---|---|---|
| Model RPC | Uses OpenAI-compatible chat completions; default `base_url` is `http://127.0.0.1:8000/v1` (`references/external-claude-code-re/clawcode/src/agent_types.py:94-101`) and stream requests POST `/chat/completions` (`references/external-claude-code-re/clawcode/src/openai_compat.py:181-207`). SSE deltas are normalized into `content_delta`, `tool_call_delta`, `message_stop`, and `usage` (`references/external-claude-code-re/clawcode/src/openai_compat.py:344-414`). | Useful for local-model backend routing; unrelated to Claude Agent Teams file protocol. |
| MCP RPC | Implements a generic stdio MCP client. It loads resources/servers from manifests (`references/external-claude-code-re/clawcode/src/mcp_runtime.py:52-72`), calls `resources/read` and `tools/call` (`references/external-claude-code-re/clawcode/src/mcp_runtime.py:108-190`), and initializes with protocol version `2025-11-25` (`references/external-claude-code-re/clawcode/src/mcp_runtime.py:13`, `references/external-claude-code-re/clawcode/src/mcp_runtime.py:760-790`). | Different from our narrowed wrapper MCP. claude-anyteam deliberately exposes only six safe team tools to routed models (`docs/architecture.md:98-108`). |
| GUI RPC | FastAPI server includes routers for tasks, plans, MCP, plugins, team, diagnostics, etc. (`references/external-claude-code-re/clawcode/src/gui/server.py:446-522`). `/api/chat` runs/resumes the local agent and returns a serialized run result (`references/external-claude-code-re/clawcode/src/gui/server.py:650-686`). | Useful UI ideas, but not visible to a Claude leader process. |

### Task list

- Tool registry includes local `task_list`, `task_get`, `task_create`, `task_update`, `task_start`, `task_complete`, `task_block`, and `task_cancel` tools (`references/external-claude-code-re/clawcode/src/agent_tools.py:889-1008`).
- Task creation/update/start/complete are ordinary local runtime mutations (`references/external-claude-code-re/clawcode/src/task_runtime.py:100-130`, `references/external-claude-code-re/clawcode/src/task_runtime.py:132-195`, `references/external-claude-code-re/clawcode/src/task_runtime.py:197-258`).
- Rendered task output includes `status`, `owner`, `title`, `description`, `active_form`, and dependency fields (`references/external-claude-code-re/clawcode/src/task_runtime.py:385-421`).
- `task_complete` returns a `ToolExecutionResult` metadata object describing the mutation, but does not send a lead inbox `task_complete` message (`references/external-claude-code-re/clawcode/src/agent_tools.py:2698-2718`).

### Mailbox / messages

- Tool registry exposes `team_list`, `team_get`, `team_create`, `team_delete`, `send_message`, and `team_messages` (`references/external-claude-code-re/clawcode/src/agent_tools.py:810-887`).
- The `send_message` handler requires write permission, appends a local `TeamMessage`, and returns metadata with the state-file path (`references/external-claude-code-re/clawcode/src/agent_tools.py:2416-2451`).
- There is no inbox polling loop, no `read_inbox`, no `send_json_to_lead` equivalent, and no peer-message steering into a running Claude lead. That is a critical gap vs claude-anyteam's per-task flow where the adapter polls, claims, executes, writes `task_complete`, and optionally live-steers Codex App Server (`docs/architecture.md:98-108`).

### Presence / lifecycle

- Local child-agent state is tracked by `AgentManager`: records have `agent_id`, parent/group, `status`, `turns`, `tool_calls`, `stop_reason`; groups have `group_id`, strategy, status, child counts, batch/dependency fields (`references/external-claude-code-re/clawcode/src/agent_manager.py:6-35`). Summaries report managed agents, children, groups, turns, tool calls, and stop reasons (`references/external-claude-code-re/clawcode/src/agent_manager.py:246-275`).
- The GUI provides a Tasks tab against `.port_sessions/task_runtime.json` (`references/external-claude-code-re/clawcode/README.md:793-803`) and a transcript renderer with tool cards (`references/external-claude-code-re/clawcode/src/gui/static/app.js:307-340`, `references/external-claude-code-re/clawcode/src/gui/static/app.js:543-574`).
- None of that registers native Claude teammate presence. Prior internal RE shows passive config/subagent file injection did not create a TUI presence row (`docs/internal/2026-prototype/research.md:121-130`), and the presence source is leader-internal `AppState.tasks` (`docs/internal/2026-prototype/research.md:25-34`).

### Event/visibility stream

- `StreamEvent` carries `type`, text delta, tool-call deltas, finish reason, usage, and `raw_event`; `AgentRunResult` stores `events` next to final output, transcript, usage, stop reason, and file history (`references/external-claude-code-re/clawcode/src/agent_types.py:120-143`, `references/external-claude-code-re/clawcode/src/agent_types.py:180-193`).
- The main loop collects model stream events and emits lifecycle events for tools: `tool_start` (`references/external-claude-code-re/clawcode/src/agent_runtime.py:852-866`), `tool_delta` (`references/external-claude-code-re/clawcode/src/agent_runtime.py:948-971`), `tool_permission_denial` (`references/external-claude-code-re/clawcode/src/agent_runtime.py:1022-1036`), and `tool_result` (`references/external-claude-code-re/clawcode/src/agent_runtime.py:1037-1057`).
- Query-engine streaming can yield runtime-agent events and a cumulative runtime summary (`references/external-claude-code-re/clawcode/src/query_engine.py:200-220`, `references/external-claude-code-re/clawcode/src/query_engine.py:400-545`).
- Red flag: the GUI `/api/chat` serialized result omits raw `events`; it returns final output, turns, tool calls, transcript, session, usage, cost, and stop reason only (`references/external-claude-code-re/clawcode/src/gui/server.py:715-725`). The GUI reconstructs tool cards from transcript, not the richer event stream.

## Deltas vs claude-anyteam

| Axis | claude-anyteam current/prior art | claw-code-agent | Delta / implication |
|---|---|---|---|
| Visibility | North star is native parity: lead should see tool calls, prose deltas, idle reasons, peer DMs, host-tool activity, and errors without reading stderr (`CLAUDE.md:3-16`). B9 proposes normalized events fanned to stderr, mailbox, task state, and event log (`bug-triage/B9-visibility-parity-investigation.md:571-588`). | Has a strong internal event/transcript model (`StreamEvent`, tool lifecycle events), but events stay inside local runtime/GUI and are not bridged to Claude lead files/AppState. | Steal taxonomy and summaries, not transport. Use as evidence that event normalization is ergonomic, not as Agent Teams protocol evidence. |
| Lifecycle | Adapter self-registers, polls inbox, claims tasks, invokes backend, writes task_complete, supports idle/shutdown semantics (`docs/architecture.md:37-41`, `docs/architecture.md:98-108`). | Local tasks/messages only; no per-agent inbox polling, idle notification, shutdown, task claim CAS, or lead notification. | Not a replacement for `src/claude_teams/`; at most a local task-model inspiration. |
| Spawn | claude-anyteam hooks pane spawn via `CLAUDE_CODE_TEAMMATE_COMMAND` and routes names by prefix (`docs/architecture.md:85-96`). Prior RE says in-process spawn does not call the shim; pane backend does (`docs/internal/spawn-research-findings.md:453-465`, `docs/internal/spawn-research-findings.md:469-489`). | `Agent` tool launches inline/local `LocalCodingAgent` children; snapshot mentions upstream `spawnMultiAgent`, but implemented child execution is local and sequential (`references/external-claude-code-re/clawcode/src/agent_tools.py:1106-1145`, `references/external-claude-code-re/clawcode/src/agent_runtime.py:2387-2472`). | No evidence about Claude Code Agent Teams spawn wire contract. Good ideas for child summaries; no shim/AppState path. |
| MCP wrapper | Our wrapper is intentionally narrowed to six safe team tools (`send_message`, `task_update`, `task_create`, `read_inbox`, `task_list`, `read_config`) and blocks destructive team controls (`docs/architecture.md:98-108`). | Generic local MCP client discovers manifests, lists resources/tools, and calls arbitrary stdio MCP tools (`references/external-claude-code-re/clawcode/src/mcp_runtime.py:358-386`, `references/external-claude-code-re/clawcode/src/mcp_runtime.py:655-790`). | Nice test-harness/client reference, but too broad for routed teammate guardrails. |
| Model routing | claude-anyteam routes by teammate name/backend and exposes protocol-level `--model` / five-tier `--effort` (`docs/architecture.md:27-32`, `docs/architecture.md:122`). | Model config is OpenAI-compatible with per-child override; `_resolve_child_model_config` accepts any nonblank override, but the `Agent` tool schema enum lists only `sonnet`, `opus`, `haiku` (`references/external-claude-code-re/clawcode/src/agent_types.py:94-101`, `references/external-claude-code-re/clawcode/src/agent_runtime.py:2156-2173`, `references/external-claude-code-re/clawcode/src/agent_tools.py:1127-1130`). | Useful local-model pattern; schema mismatch is a red flag. Does not model our prefix/effort routing. |

## Ideas worth stealing

1. **Adopt a normalized event taxonomy close to their runtime events.** Their `tool_start`/`tool_delta`/`tool_permission_denial`/`tool_result` events are concrete and minimal (`references/external-claude-code-re/clawcode/src/agent_runtime.py:852-866`, `references/external-claude-code-re/clawcode/src/agent_runtime.py:948-1057`). Map these into B9's proposed `tool_event` payload kind and keep raw backend payload references (`bug-triage/B9-visibility-parity-investigation.md:590-630`).
2. **Keep raw stream payloads with normalized fields.** `StreamEvent` stores both normalized fields and `raw_event` (`references/external-claude-code-re/clawcode/src/agent_types.py:120-143`), matching B9's requirement that backend raw payloads remain available for forensics (`bug-triage/B9-visibility-parity-investigation.md:592-594`).
3. **Use child/group lifecycle summaries for delegated work.** `AgentManager.group_summary()` records status, child counts, resumed children, batch count, dependency skips, and stop-reason counts (`references/external-claude-code-re/clawcode/src/agent_manager.py:214-239`); runtime emits `delegate_batch_result`, `delegate_subtask_result`, and `delegate_group_result` events (`references/external-claude-code-re/clawcode/src/agent_runtime.py:2848-2895`). These would be high-signal task-state/event-log updates for routed spawned subagents.
4. **Make task mutations auditable.** `TaskMutation` carries before/after SHA-256, previews, and counts (`references/external-claude-code-re/clawcode/src/task_runtime.py:19-29`, `references/external-claude-code-re/clawcode/src/task_runtime.py:470-504`). We could add similar metadata to `task_update`/event-log records for easier replay and debugging.
5. **Render tool cards from transcript.** The GUI’s transcript renderer turns assistant tool calls and tool messages into collapsible cards (`references/external-claude-code-re/clawcode/src/gui/static/app.js:307-340`, `references/external-claude-code-re/clawcode/src/gui/static/app.js:543-574`). If we build a future `read_events`/dashboard, this is the right display primitive.
6. **Manifest discovery ergonomics for MCP test fixtures.** Their MCP runtime discovers `.claw-mcp.json`, `.mcp.json`, `.codex-mcp.json`, and `mcp.json` up the directory tree (`references/external-claude-code-re/clawcode/src/mcp_runtime.py:358-386`). For adapter tests, that flexible discovery is handy; for production teammate wrapper, keep our narrower allowlist.

## Red flags / anti-patterns

- **Ambiguous license.** README badge is not a license grant; no `LICENSE` file and no package license metadata.
- **Documentation drift.** README claims “zero external dependencies,” while package metadata declares FastAPI, Uvicorn, and Pydantic (`references/external-claude-code-re/clawcode/README.md:97`, `references/external-claude-code-re/clawcode/pyproject.toml:35-38`).
- **Not an Agent Teams implementation.** Local `.port_sessions/*` state should not be mistaken for Claude Code's on-disk team protocol (`docs/architecture.md:11-13`).
- **Unsafe multi-process persistence.** Direct `write_text` for team/task state has no file lock or atomic rename (`references/external-claude-code-re/clawcode/src/team_runtime.py:321-330`, `references/external-claude-code-re/clawcode/src/task_runtime.py:470-504`).
- **MCP process model is expensive/noisy.** Each stdio request creates a fresh subprocess (`references/external-claude-code-re/clawcode/src/mcp_runtime.py:655-663`, `references/external-claude-code-re/clawcode/src/mcp_runtime.py:675-697`), and discovery silently ignores `OSError` for remote resource/tool listing (`references/external-claude-code-re/clawcode/src/mcp_runtime.py:313-340`).
- **Model schema mismatch.** The `Agent` tool schema restricts model override to `sonnet|opus|haiku`, while the resolver accepts any string and the runtime is built for OpenAI-compatible local models (`references/external-claude-code-re/clawcode/src/agent_tools.py:1127-1130`, `references/external-claude-code-re/clawcode/src/agent_runtime.py:2156-2173`).
- **“Batching” is sequential.** Delegated subtasks are described as topological batching, but the implementation loops batches and subtasks inline, running `child_agent.run()`/`resume()` sequentially (`references/external-claude-code-re/clawcode/src/agent_runtime.py:2303-2315`, `references/external-claude-code-re/clawcode/src/agent_runtime.py:2469-2472`). Do not infer concurrency semantics.
- **Parity source is not reproducible from clone.** The parity audit expects a local archived Claude Code TS snapshot (`references/external-claude-code-re/clawcode/src/parity_audit.py:7-11`) that is not shipped; snapshots/source hints are useful but not independently verifiable here.
- **Rich events are underexposed.** `AgentRunResult.events` exists, but the GUI API serializer omits it (`references/external-claude-code-re/clawcode/src/agent_types.py:180-193`, `references/external-claude-code-re/clawcode/src/gui/server.py:715-725`). For our visibility-parity north star, that is exactly the trap to avoid.

## Bottom line for team-lead

`claw-code-agent` is valuable as a *Claude Code architecture imitation* and as an observability idea mine. It does **not** add credible new facts about Claude Code Agent Teams file protocol beyond what `docs/architecture.md`, `B9`, and the 2026 prototype RE already captured. The concrete pieces I would pull forward are: normalized event taxonomy, raw-event retention, child/group lifecycle summaries, task mutation audit metadata, and tool-card rendering. I would not copy its local team/task protocol, MCP breadth, persistence model, or licensing assumptions.
