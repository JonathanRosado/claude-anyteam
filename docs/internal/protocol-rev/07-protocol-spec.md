# 07 — Agent Teams Protocol Specification

**Version:** 2.0 (canonical, supersedes `docs/internal/2026-prototype/protocol-spec.md` v1.1)
**Author:** opus-synthesis
**Date:** 2026-04-27
**Source:** Reverse-engineered from Claude Code v2.1.119 binary, vendored MCP implementation in `src/claude_teams/`, the `claude-anyteam` adapter implementation, runtime trace evidence, and a survey of related multi-agent protocols (ACP, MCP, A2A, LSP).
**Audience:** authors of new harnesses (Cursor, OpenCode, Sourcegraph) or new backend adapters (codex-*, gemini-*, kimi-*, glm-*, deepseek-*, qwen-*) who need a single self-contained reference for participating in Agent Teams.

This is a research-grade specification. It is the protocol the codebase already speaks plus the protocol it should speak — the line between "shipped" and "proposed" is drawn explicitly with evidence tags.

---

## Evidence-tag scheme

Every load-bearing claim carries one of three tags:

- `[D]` **documented** — appears in Anthropic public documentation (`code.claude.com/docs`), the published `CHANGELOG`, or this repository's own self-consistent code as authoritative.
- `[E]` **empirically inferred** — confirmed via binary RE (`references/external-claude-code-re/host-binary-extract/`), runtime trace (`references/external-claude-code-re/runtime-trace/`), reverse-engineered clones (`maorinka/claude-rs`, `nwyin/claude-cleanroom-2.1.83`, `cs50victor/claude-code-teams-mcp`), or live filesystem inspection. Not in Anthropic public docs.
- `[O]` **open** — known unknown. Tracked in §10. Future investigation should resolve.

Where a v1.1 claim contradicts current evidence, the v1.1 claim is corrected and the contradiction noted in §9.

---

## 0. Executive summary

1. **The harness IS the teammate, not a wrapped LLM.** A `codex-*` teammate is the OpenAI Codex CLI process — its full tool surface, App Server semantics, prompt tuning, and approval policies — registered into a Claude Code team. The protocol's job is to *carry capability declarations and route activity*, not flatten capabilities to a lowest common denominator. This is the architectural moat (`CLAUDE.md:3-26`) and every other section follows from it. `[D]`

2. **Agent Teams is a file-based on-disk contract.** Team state lives in `~/.claude/teams/<safe-team>/config.json`; per-agent inboxes in `~/.claude/teams/<safe-team>/inboxes/<safe-agent>.json`; tasks in `~/.claude/tasks/<safe-team>/<id>.json`. Mutations are atomic temp+rename for config, .lock-protected JSON-array rewrite for inbox and task files. Any process that speaks this contract can be a teammate. `[D]` (substrate code) `[E]` (host binary)

3. **TUI presence is in-memory, not file-driven.** The presence renderer reads the leader process's React state `AppState.tasks: { [taskId]: TaskState }`, filtered to `type === 'in_process_teammate'` and `status === 'running'`. The on-disk `config.json` is the membership record but is never read by the renderer. Passive file-only registration achieves functional team membership (mailbox/tasks work) but not visual presence. `[E]`

4. **Two spawn paths, one task type.** `spawnInProcessTeammate()` (in-process React coroutine) and `registerOutOfProcessTeammateTask()` (subprocess pane mirror) both write `type: "in_process_teammate"` into `AppState.tasks` and both go through `registerTask()`. The discriminant for routed teammates is "host registers like in-process, but execution lives in our spawned subprocess" — a hybrid the protocol must name explicitly. `[E]`

5. **Routed-teammate spawn is via the `CLAUDE_CODE_TEAMMATE_COMMAND` shim.** The host calls a configurable binary instead of `claude` for pane-backed teammates; the shim inspects argv (`--agent-name`, `--team-name`), routes name-prefixed agents to backend adapters, and falls through to native `claude` for the rest. The shim only fires when the host selects pane-mode (interactive tmux/iTerm2); non-interactive sessions force in-process and the shim is bypassed. `[E]` (host binary, RE, this repo's `spawn_shim.py`)

6. **Communication is six channels, not one.** Inbox JSON messages (typed payloads inside `text`); durable task state with `activeForm` and `metadata`; in-memory `AppState.tasks` mirror (host-only); MCP wrapper tool calls (side-effect-visible); stderr JSON logs (forensic); and the proposed append-only `events/<agent>.jsonl` event log (visibility-parity v2). The four-channel split from B9 §6 expanded to acknowledge capability discovery as a discrete sixth channel. `[D]` (channels 1-4) `[O]` (event log + capability discovery shape)

7. **Capability declarations are the v2 protocol primitive.** Each backend adapter must declare what it can do — `turn_steer`, `thread_fork`, `permission_bridge`, `swarm`, host-tool visibility fidelity, session persistence, cancellation safety. Peers learn these via roster read and via system-prompt guidance ("ask `codex-*` teammates to use `thread/fork` for cross-task memory"). Today the registration row is unstructured; v2 reserves a `capabilities: {...}` sub-object. `[O]`

8. **Wire schema has grown past v1.1.** Beyond the six payload kinds documented in v1.1 (`task_assignment`, `shutdown_request`, `shutdown_response`, `plan_approval_request`, `plan_approval_response`, `idle_notification`), the codebase and host now emit `task_complete`, `task_blocked`, `permission_request`, `permission_response`, `steer`, `shutdown_approved`, `shutdown_rejected`, `sandbox_permission_request`, `sandbox_permission_response`, `mode_set_request`, and `team_permission_update`. v2 documents all of them. `[D]` (codebase) `[E]` (host binary)

9. **Three north stars, one architecture.** The protocol must satisfy three invariants together: §1 harness preservation (capabilities flow through), §2 visibility parity (lead sees routed teammates at native fidelity), §3 peer efficiency (peers coordinate among themselves at native fidelity). §2 and §3 are distinct surfaces with distinct failure modes — lead-to-peer visibility ≠ peer-to-peer coordination. A team can satisfy §2 and still fail §3 (peer-DM gaps, capability-discovery latency, peer-steer authorization too narrow). All three ship together. `[D]` (`CLAUDE.md`)

10. **Visibility parity is the operational consequence of harness preservation.** When the lead spawns a native Claude teammate they see live tool calls, prose deltas, idle reasons, and peer DMs. Routed teammates today emit final summaries plus rare in-band events. The v2 envelope (B9 §6) — versioned, fan-out across stderr/mailbox/task-state/event-log, with backend-specific tool names preserved — is the way to close the gap without flattening harness richness. `[O]`

11. **The protocol is two layers, not one.** *Transport* is the universal Agent Teams contract: mailbox JSON, atomic task claims, lifecycle, file shapes, locks. Universal; table stakes for being a teammate at all. *Capability* is per-harness: each harness advertises what it uniquely can do (`turn_steer`, `thread_fork`, `permission_bridge`, `live_tool_events`, `large_context`, `native_swarm`) plus invocation schema and semantic guidance. Conflating the two produces routers, which can't bolt the capability layer back on later because their architecture has no place for it. The capability layer is the moat. `[D]`

12. **The protocol has no schema version on disk.** Host binary 2.1.119 has no `schemaVersion`/`schema_version` in any team file. The only stable version handle is the host binary version itself. v2 preserves this for backward compatibility but proposes an additive `protocolVersion` field on `TeamConfig` and `schema_version` on every typed payload. `[E]` (binary), `[D]` (PermissionRequestOut shipped with `schema_version: 1`).

13. **Three locks, two write disciplines, one shared substrate.** `~/.claude/tasks/<team>/.lock` for tasks, `~/.claude/teams/<team>/inboxes/.lock` for inbox+anyteam-config registration, `~/.claude/teams/<team>/config.json.lock` for host config writes. Atomic temp+rename for config; in-place rewrite under .lock for inbox/tasks. The host and adapter currently disagree on which lock guards config (`config.json.lock` host vs `inboxes/.lock` adapter) — a substrate-level race surfaced in §9. `[E]`

14. **The protocol is a coordinator, not a kernel.** It carries identity, lifecycle, mailbox, task state, and capability declarations. It does *not* own the agentic loop, the tool definitions, or the model's prompt tuning. Those belong to each harness. The right LSP analogy: agents-as-language-servers — a tiny base envelope, explicit capabilities, request ids, progress tokens, notifications, cancellation, and no backend-specific method leakage at call sites. `[D]`

---

## 1. On-disk substrate

### 1.1 Directory layout

```
~/.claude/
├── teams/
│   └── <safe-team>/
│       ├── config.json                   # team membership + lead session
│       ├── config.json.lock              # host-side mutex (host RE only)
│       ├── inboxes/
│       │   ├── .lock                     # shared mutex (host + adapter)
│       │   ├── <safe-agent>.json         # one per member
│       │   └── <safe-agent>.json.lock    # per-inbox mutex (host RE only)
│       └── agents/                       # adapter extension (anyteam-only)
│           └── <agent>.json              # per-teammate model/effort overrides
└── tasks/
    └── <safe-task-list>/
        ├── .lock                          # shared task list mutex
        ├── .highwatermark                 # host-only id allocator
        └── <id>.json                      # one per task
```

`<safe-team>` and `<safe-agent>` are sanitized: non-alphanumerics replaced with `-`, lowercased; agent `@` is replaced with `-`. Task list names additionally permit `_`. `[E]` (host binary `jd()`/`S_$()` helpers; vendored `_VALID_NAME_RE = ^[A-Za-z0-9_-]+$`).

`<safe-task-list>` defaults to the team name unless `CLAUDE_CODE_TASK_LIST_ID` overrides; the host falls back to `teamContext`, team name, dynamic in-memory list, or session id in that order. `[E]`

### 1.2 `config.json` — the team manifest

**Owner:** host on team create; host on native spawn; adapter on self-registration; both on member updates.

**Atomicity:** atomic temp+rename via `os.replace()` (`src/claude_teams/teams.py:119-135` `[D]`; runtime trace confirms `tmp*.tmp` followed by rename, `references/external-claude-code-re/runtime-trace/diffs/config-02-to-05-pane-id.diff`).

**Lock discipline:**
- Host: `<team>/config.json.lock` (`fragments/snippets/team_file_read.txt:25-40` `[E]`).
- Adapter: `<team>/inboxes/.lock` (`src/claude_anyteam/registration.py:58-69` `[D]`).
- **Drift:** host and adapter use *different* locks for config writes. Adapters and the host can race a config write today; the only saving grace is that both writers do atomic replace. See §9.

**Schema (canonical, host-emitted, v2.1.119):**

```json
{
  "name": "string",
  "description": "string",
  "createdAt": 1770000000000,
  "leadAgentId": "team-lead@<team>",
  "leadSessionId": "<UUIDv4>",
  "members": [ /* MemberUnion */ ],
  "hiddenPaneIds": [ "%12" ]
}
```

`hiddenPaneIds` is host-maintained and tracks tmux panes the host has elided from its UI; not authored by adapters. `[E]`

**`MemberUnion`** is discriminated on the presence of `prompt`:

`LeadMember` (no `prompt`):
```json
{
  "agentId": "team-lead@<team>",
  "name": "team-lead",
  "agentType": "team-lead",
  "model": "<model>",
  "joinedAt": 1770000000000,
  "tmuxPaneId": "",
  "cwd": "/path",
  "subscriptions": []
}
```

`TeammateMember` (has `prompt`):
```json
{
  "agentId": "<name>@<team>",
  "name": "<name>",
  "agentType": "general-purpose|<custom>",
  "model": "<model>",
  "prompt": "<initial prompt>",
  "color": "<color>",
  "planModeRequired": false,
  "joinedAt": 1770000000000,
  "tmuxPaneId": "%12 | @4 | in-process | \"\"",
  "cwd": "/path",
  "subscriptions": [],
  "backendType": "tmux | iterm2 | in-process | claude | opencode",
  "isActive": false,
  "mode": "<optional>"
}
```

`[E]` (host binary `fragments/snippets/team_config_create_tool_actual.txt:10-17`, `pane_spawn_extra_flags_BM7.txt:69-78`)
`[D]` (vendored `src/claude_teams/models.py:30-45`)

**`backendType` value space — clarified.** v1.1 conflated three concerns. They are distinct:

| Concept | Lives in | Values | Meaning |
|---|---|---|---|
| **spawn-mode** (host-side selector) | `~/.claude.json` `teammateMode`, runtime `Rb()` gate | `auto \| tmux \| in-process` | Which backend the host's `spawnTeammate()` should pick |
| **backendType** (per-row config field) | `members[].backendType` | `tmux \| iterm2 \| in-process` (host); `claude \| opencode` (cs50victor MCP); legacy `claude-anyteam` writes `"in-process"` | What kind of process is hosting this teammate at runtime |
| **tmuxPaneId** (runtime handle) | `members[].tmuxPaneId` | tmux pane id `%N`, window id `@N`, literal `"in-process"`, or `""` | Where to send keys / capture output |

The codebase emits `backendType: "in-process"` for routed teammates (`src/claude_anyteam/registration.py:141-159` `[D]`). The cs50victor canonical reference uses `"claude"` as the default for native teammates and `"opencode"` for an alternative harness — i.e. `backendType` semantically tags *which harness owns this teammate*. The adapter's "in-process" emission is legacy naming the canonical reference has moved past. **Forward-compatibility risk:** if the host adds strict validation, adapter rows could be rejected. See §10. `[E]`

### 1.3 Inbox files — `inboxes/<safe-agent>.json`

**Owner:** any process can append; only the inbox owner should mark-as-read.

**Atomicity:** in-place JSON-array rewrite under `inboxes/.lock` (`src/claude_teams/messaging.py:53-70`, `:146-158` `[D]`). Host RE shows a per-inbox `.lock` sidecar (`<inbox>.json.lock`) is also created (`fragments/snippets/team_paths_and_mailbox.txt:18-40` `[E]`); adapter does not currently use it.

**Schema (each row):**

```json
{
  "from": "team-lead | <peer-name>",
  "text": "plain prose OR JSON-encoded protocol payload",
  "timestamp": "2026-04-27T00:00:00.000Z",
  "read": false,
  "summary": "optional one-line label for notifications",
  "color": "blue|green|yellow|orange|pink|cyan|red|purple"
}
```

`[D]` (`src/claude_teams/models.py:90-99`) `[E]` (host binary `fragments/snippets/team_paths_and_mailbox.txt:18-40`)

**Pydantic-strip hazard.** The vendored model has `extra="ignore"` semantics; mark-as-read deserializes all messages, mutates `read`, and rewrites — *stripping unknown fields the host may have added*. Adapter mitigates by asserting it only marks its own inbox (`src/claude_anyteam/protocol_io.py:59-75` `[D]`). v2 fix: add a stable `id` field per message and update `read` by id without reserialization. See §9.

**Structured payloads live inside `text`.** `text` is opaque to the host's mailbox writer; it is parsed by recipients. The host has type-recognizers (`isStructuredProtocolMessage`) but mostly to decide UI presentation, not to validate. The complete recognized type table is in §5.

### 1.4 Task files — `tasks/<safe-task-list>/<id>.json`

**Owner:** host on `TaskCreate`/`TaskUpdate`; adapter on `claim_task` (compare-and-set).

**Atomicity:** host write under `<task-list>/.lock` (`src/claude_teams/tasks.py:77-90`, `:118-123` `[D]`). Adapter `claim_task()` is a single read-check-write under that same lock (`src/claude_anyteam/protocol_io.py:86-130` `[D]`).

**Id allocation:**
- Host: monotonic via `<task-list>/.highwatermark` file. **Persists across deletes.** `[E]` (`fragments/snippets/task_update_rH.txt:18-25`)
- Vendored adapter substrate: scans dir for `<n>.json`, picks `max(n)+1`. **Does not persist across deletes.** `[D]` (`src/claude_teams/tasks.py:52-60`)

**Drift:** host and vendored substrate disagree about how to allocate ids after a delete. v1.1 spec is wrong on this point — it claimed no `.highwatermark` exists, citing only the vendored substrate. The host *does* maintain one, and a multi-process scenario (host + adapter sharing a task list) could collide. See §9 and §10.

**Schema:**

```json
{
  "id": "3",
  "subject": "Reverse-engineer host binary",
  "description": "optional details",
  "activeForm": "Reverse-engineering host binary",
  "owner": "<agent-name> | null",
  "status": "pending | in_progress | completed | deleted",
  "blocks": ["1"],
  "blockedBy": ["2"],
  "metadata": { "any": "json value" }
}
```

`[D]` (`src/claude_teams/models.py:76-87`) `[E]` (host binary `fragments/snippets/task_update_rH.txt:94-143`)

**Status semantics.**
- `pending → in_progress → completed`: monotonic; backward transitions rejected (`src/claude_teams/tasks.py:19-20`, `:161-169` `[D]`).
- `deleted` removes the file from disk and unwires `blocks`/`blockedBy` references (`src/claude_teams/tasks.py:253-284` `[D]`).
- Tasks with non-empty `blockedBy` cannot transition to `in_progress` or `completed` until each blocker is `completed`. **Substrate disagreement:** vendored treats only `completed` as unblocking; the adapter loop also treats `deleted` as unblocking (`src/claude_anyteam/loop.py:564-574` `[D]`). v2 should pick one. See §9.

**No `blocked` status today.** When an adapter cannot proceed it sets `activeForm: "blocked: <reason>"` and emits a `task_blocked` mailbox message, leaving status at `in_progress` (`src/claude_anyteam/loop.py:855-899` `[D]`). v2 proposes adding `blocked` as a first-class status. See §10.

### 1.5 Per-agent overrides — `teams/<team>/agents/<agent>.json`

**Adapter extension, not host substrate.** Used by the spawn shim to apply per-teammate model/effort overrides at exec time. Whitelisted keys: `model`, `effort`, `turn_timeout_s` (`src/claude_anyteam/spawn_shim.py:104-126` `[D]`).

```json
{
  "model": "gpt-5.5",
  "effort": "xhigh",
  "turn_timeout_s": 900
}
```

The host does not read this file; it is consumed only by `claude-anyteam-spawn-shim` immediately before re-execing into the adapter binary.

### 1.6 Naming and identity

```
agentId   = "<agent-name>@<team-name>"     # host helper jd(name, team)
parsing   = S_$(agentId) -> {agentName, teamName}
```

Constraints (`src/claude_teams/spawner.py:99-106` `[D]`, host binary `[E]`):

- `agent-name` and `team-name` match `^[A-Za-z0-9_-]+$`.
- Length ≤ 64 each.
- `agent-name == "team-lead"` is reserved.
- For task list directory names, `_` is also permitted.

The adapter's spawn-shim regex matchers are `^codex-` (Codex), `^gemini-` (Gemini), `^kimi-` (Kimi); overridable via `CLAUDE_CODE_SHIM_MATCH` (`src/claude_anyteam/spawn_shim.py:29-35` `[D]`).

### 1.7 Capability declaration (proposed, evidence type `[O]`)

The substrate today carries no per-teammate capability declaration. Each backend has unique features (Codex `turn/steer` and `thread/fork`, Gemini ACP permission bridge, Kimi swarm/skills) but the protocol does not surface them; peers cannot programmatically discover what a given teammate can do. v2 reserves an additive sub-object on `TeammateMember`:

```json
{
  "capabilities": {
    "schema_version": 1,
    "turn_steer": "live | next_turn_boundary | unsupported",
    "thread_fork": true,
    "permission_bridge": "lead_inbox | auto_allow | unsupported",
    "swarm": false,
    "host_tool_visibility": "rich | counted | absent",
    "session_persistence": true,
    "cancellation": "live | turn_boundary | unsupported",
    "accepts_peer_steer": false
  }
}
```

The host MUST treat unknown capability keys as `false`/`unsupported`. The lead's system prompt SHOULD teach peers how to invoke unique capabilities ("`codex-*` teammates support `thread/fork`; ask them to fork to inherit prior task context").

**Two-layer architecture.** The capability declaration is the *cheap roster lookup* surface — lightweight typed flags on `members[].capabilities`, suitable for "does this peer support X?" checks at message-construction time. The *rich manifest* (input schema, semantic guidance, when-to-use, failure modes, version flag) is exposed via the wrapper MCP and loaded into peer context only when a peer is about to invoke. This mirrors how MCP already works for tools: `tools/list` is cheap, `tools/call` carries the rich invocation schema.

```
Transport layer (universal)        Capability layer (per-harness)
──────────────────────────         ──────────────────────────────
mailbox JSON                       members[].capabilities  (cheap flags)
atomic task claims                 wrapper MCP capability tools  (rich manifest)
lifecycle (idle/shutdown/...)      semantic guidance in lead system prompt
config + task file shapes
locks
```

Conflating the two produces routers — every teammate gets the host's tool surface, period, and there is no place for capability-specific invocation schemas to live. Adapters that distinguish the layers can extend the capability layer without breaking transport-layer compatibility.

This is the protocol primitive that operationalizes north star §1: **flatten = no.** When a backend has a feature its peers don't, the protocol carries the declaration; it never strips the feature down. See §11 compliance criterion #1.

**Capability discovery latency** is a peer-efficiency concern (north star §3). Manifests SHOULD be cached at team formation time and refreshed only on `capability_changed` events (§7.3); peers SHOULD NOT pay round-trip MCP costs every time they consider invoking a peer's primitive. See §11 criterion #9.

---

## 2. In-memory state (host-only)

The host process maintains a React-state map `AppState.tasks: { [taskId: string]: TaskState }`. The TUI presence renderer reads this map directly. **No file substrate corresponds to this state**; it is created and updated only by code running inside the host's Node.js process.

This section documents the shape and lifecycle of `AppState.tasks` so that adapter authors understand *why* the file substrate alone is presence-incomplete.

### 2.1 Schema

`TaskState` is a discriminated union; the `in_process_teammate` variant is the only one relevant to teammate presence:

```typescript
type InProcessTeammateTaskState = {
  // base task fields
  id: string                  // "t<random>"
  type: "in_process_teammate"
  status: "running" | "completed" | "failed" | "killed"
  description: string
  toolUseId: string
  startTime: number
  outputFile: string
  outputOffset: number
  notified: boolean

  // teammate identity
  identity: {
    agentId: string                 // "<name>@<team>"
    agentName: string
    teamName: string
    color: string
    planModeRequired: boolean
    parentSessionId: string         // === leadSessionId in config.json
  }

  // teammate state
  prompt: string
  model: string
  abortController: AbortController
  unregisterCleanup?: () => void
  awaitingPlanApproval: boolean
  spinnerVerb: string
  pastTenseVerb: string
  permissionMode: "default" | "plan" | "acceptEdits" | "auto" | "bypassPermissions"
  isIdle: boolean
  shutdownRequested: boolean
  lastReportedToolCount: number
  lastReportedTokenCount: number
  pendingUserMessages: string[]
  messages: Message[]              // task-local transcript

  // optional
  cwd?: string                     // present on out-of-process mirrors
  progress?: AgentProgress
  inProgressToolUseIDs?: Set<string>
  currentWorkAbortController?: AbortController
  totalPausedMs?: number
  endTime?: number
  error?: string
}
```

`[E]` (host binary `fragments/snippets/spawnInProcessTeammate_Mr_function.pretty.txt:56-87`, `fragments/snippets/appstate_tasks_selector.txt:53-67`).

### 2.2 The presence renderer

The TUI selector flow:

1. `getAllInProcessTeammateTasks(tasks)` — filter to `type === 'in_process_teammate'`.
2. `getRunningTeammatesSorted(tasks)` — filter to `status === 'running'`, sort by `identity.agentName`.
3. For each row, render `@${identity.agentName}` plus optional badges driven by `isIdle`, `progress`, and a preview drawn from `messages`.

`[E]` (`docs/internal/2026-prototype/research.md:25-34`; minified renderers `h87`, `Li$`, `$s9`, `ea9`, `ujH`, `$e`, `Mj` per host strings dump).

### 2.3 Task stream events

The host's `registerTask()` emits `system` stream events on a private SDK/JSON-RPC channel. `[E]` (`fragments/snippets/registerTask_emit.txt`, `rpc_*_schema.txt`):

| Event | Fields |
|---|---|
| `task_started` | `task_id`, `tool_use_id?`, `description`, `task_type?`, `workflow_name?`, `prompt?`, `skip_transcript?` |
| `task_updated` | `task_id`, `patch` (status, description, end_time, total_paused_ms, error, is_backgrounded) |
| `task_progress` | `task_id`, `tool_use_id?`, `description`, usage counters, `last_tool_name?`, `summary?` |
| `task_notification` | `task_id`, `tool_use_id?`, `status: completed|failed|stopped`, `output_file`, `summary`, usage |
| `session_state_changed` | `state: idle|running|requires_action` |

**Adapter access:** none. These events are emitted on the host's MCP channel `claude/tengu`, but the host's MCP server entry deliberately sets `setAppState: () => {}` (no-op) for inbound calls — there is no external write path into AppState.

### 2.4 The two register paths

```
host process boundary
─────────────────────────────────
                                                     ┌──────────────────────┐
                                                     │  AppState.tasks      │
                                                     │  React state map     │
                                                     └──────────▲───────────┘
                                                                │ registerTask()
                                                                │ (setAppState
                                                                │  closure)
                                                ┌───────────────┴───────────────┐
                                                │                               │
                            ┌───────────────────┴────────────────┐  ┌──────────┴────────────┐
                            │ spawnInProcessTeammate()            │  │ registerOutOfProcess  │
                            │ (in-process React coroutine)        │  │ TeammateTask()        │
                            │   - real Claude inference inline    │  │ (pane subprocess)     │
                            └─────────────────────────────────────┘  └───────────────────────┘
                                          │                                     │
                                          │                                     │ exec via
                                          │                                     │ $CLAUDE_CODE_TEAMMATE_COMMAND
                                          │                                     ▼
                                          │                         ┌────────────────────────┐
                                          │                         │ shim → adapter         │
                                          │                         │ (codex-*/gemini-*/...)  │
                                          │                         └────────────────────────┘
                                          │
                                          ▼
                              (Claude inference loop, in same Node heap as lead)
```

**Both paths produce `type: "in_process_teammate"`.** The TUI cannot tell the difference between an in-process Claude coroutine and a pane subprocess mirror. For routed teammates, this means: `host registers like in-process, but execution lives in our spawned subprocess`. This hybrid is the architectural seam the spec must name explicitly. See §3.

### 2.5 No external injection surface

The host's `setAppState` is a React closure created once during render bootstrap. There is no:

- file watcher / inotify on team paths the host would detect external mutations on (`docs/internal/2026-prototype/research.md:116-119` `[E]`);
- inbound MCP server method that mutates AppState (the host's `claude/tengu` server has explicit `setAppState: () => {}` no-op `[E]`);
- Unix socket, named pipe, or shm region carrying presence data;
- SIGUSR-style trigger to register an external teammate;
- documented plugin / hook / Bun preload path that reaches `registerTask` reliably.

`[E]` (`docs/internal/spawn-research-findings.md` Phase-2 §1.x, exhaustive enumeration).

**Consequence for adapter authors:** functional team membership (mailbox, tasks, idle/shutdown) works regardless of how the teammate process started. *Visual presence* (the `@name` row in the lead's TUI) requires the leader's spawn flow to have been the one that started the teammate process. The `CLAUDE_CODE_TEAMMATE_COMMAND` shim is the only known production-grade hook into that flow.

---

## 3. Spawn lifecycle

### 3.1 The unified spawn primitive

All teammate spawns — LLM `Agent(team_name=X, name=Y)`, TUI natural-language ("spawn a researcher"), `/invite`, programmatic — route through one host function `spawnTeammate()`. The branching point is `handleSpawn()`:

```
spawnTeammate(input, context)
    │
    └── handleSpawn(input, context)
            │
            ├── isInProcessEnabled() ?
            │     yes → handleSpawnInProcess(...)
            │            └── spawnInProcessTeammate()
            │                  └── runs Claude coroutine in-heap
            │                  └── registerTask(InProcessTeammateTaskState)
            │
            └── no  → detectAndGetBackend()
                       ├── PaneBackendExecutor.spawn()
                       │     └── shellCommand = "cd $cwd && env $envs $TEAMMATE_COMMAND <flags>"
                       │     └── tmux split-window | iterm2 send | window
                       └── registerOutOfProcessTeammateTask()
                             └── registerTask(InProcessTeammateTaskState)
                                  (mirrored, same type)
```

`[E]` (RE'd `tools/shared/spawnMultiAgent.ts:474-486, 760-834`).

### 3.2 The in-process gate

`isInProcessEnabled()` decides which branch fires:

```typescript
function isInProcessEnabled(): boolean {
  if (getIsNonInteractiveSession()) return true        // headless, --print, --resume
  const mode = getTeammateMode()                       // ~/.claude.json teammateMode
  if (mode === 'in-process') return true
  if (mode === 'tmux') return false
  // mode === 'auto'
  if (inProcessFallbackActive) return true
  return !isInsideTmuxSync() && !isInITerm2()
}
```

`[E]` (`registry.ts:408-446` reconstructed). Critical implications:

1. **Non-interactive sessions force in-process.** The shim never fires; the routed adapter can't be the executor for any teammate spawned from a `claude --print` / CI / scheduled session. `[E]` (issue #34614, runtime trace).
2. `teammateMode: "tmux"` overrides auto-detection but only for fresh sessions; a `--resume`'d session captured `getIsNonInteractiveSession()` at startup and cannot flip.
3. Fake-tmux PATH shims (KILD's `kild-tmux-shim`, psmux on Windows) work by satisfying Priority-3 detection (`tmux -V` exit 0) and serving the spawn subcommands; they do not inject into AppState directly.

### 3.3 Spawn argv contract

When `handleSpawnSplitPane()` runs, the host shells out:

```
cd <cwd> && env \
  CLAUDECODE=1 \
  CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1 \
  <selected pass-through env> \
  <executable> \
  --agent-id <name>@<team> \
  --agent-name <safe-name> \
  --team-name <team> \
  --agent-color <color> \
  --parent-session-id <session> \
  [--plan-mode-required] \
  [--agent-type <type>] \
  [--teammate-mode auto|tmux|in-process] \
  [--dangerously-skip-permissions | --permission-mode acceptEdits|auto] \
  [--model <model>] \
  [--settings <settings>] \
  [--plugin-dir <dir>] ... \
  [--chrome|--no-chrome]
```

`[E]` (host binary `fragments/snippets/pane_spawn_extra_flags_BM7.txt:8-32, 84-99`; runtime trace `references/external-claude-code-re/runtime-trace/logs/spawn-command.txt`).

**Executable resolution priority:**
1. `$CLAUDE_CODE_TEAMMATE_COMMAND` if set (the shim hook).
2. `process.execPath` in packaged-bundle mode.
3. `process.argv[1]` as fallback.

`[E]` (`spawnUtils.ts:18-28`).

**Pre-spawn protocol:** before running the spawn command, the host writes the initial prompt as a `team-lead`-from `InboxMessage` into the new teammate's inbox (`fragments/snippets/pane_spawn_extra_flags_BM7.txt:84-99` `[E]`; runtime trace `diffs/inbox-child-03-after-initial-inbox` confirms). The pane process then drains its own inbox at startup to receive the prompt.

**Identity binding via parent session.** `--parent-session-id <session>` matches the `leadSessionId` in `config.json` (runtime trace `references/external-claude-code-re/runtime-trace/logs/parent-session-id.txt` `[E]`). This is the canonical handle the teammate uses to identify which lead it serves and which inbox to write back to.

### 3.4 The `CLAUDE_CODE_TEAMMATE_COMMAND` shim

The shim sits between the host's pane spawn and the eventual backend process:

```
host PaneBackendExecutor
    │
    │ spawn argv (above)
    ▼
$CLAUDE_CODE_TEAMMATE_COMMAND  (claude-anyteam-spawn-shim)
    │
    │ parse --agent-name, --team-name, --plan-mode-required from argv
    │ ignore --agent-id, --agent-color, --parent-session-id, --agent-type,
    │   --teammate-mode, --dangerously-skip-permissions, --permission-mode,
    │   --model, --settings, --plugin-dir, --chrome
    │
    ▼
match agent-name regex?
  ^codex-   → exec claude-anyteam       --name <agent> --team <team> [--plan-mode] [--model X] [--effort Y]
  ^gemini-  → exec gemini-anyteam       (same shape)
  ^kimi-    → exec kimi-anyteam         (same shape)
  *         → exec native claude         (with original argv preserved)
```

`[D]` (`src/claude_anyteam/spawn_shim.py:52-93, 254-269, 288-309`).

**Probe path.** Claude Code calls the shim binary with `--print` and no positional prompt at startup as a binary-validation probe; the shim returns 0 cleanly to satisfy the check (`spawn_shim.py:233-245` `[D]`).

### 3.5 The hybrid: registered like in-process, executing like pane

This is the seam the spec must name explicitly.

For a routed teammate (e.g. `codex-alice`):

1. The host calls `spawnTeammate()` → because pane mode was selected, `handleSpawnSplitPane()` runs.
2. `handleSpawnSplitPane()` shells the spawn command (above). Because `CLAUDE_CODE_TEAMMATE_COMMAND` is set to our shim, the host's *own* binary resolution skips `claude` and runs the shim.
3. The shim re-execs into `claude-anyteam` (or sibling adapter binary). The pane's PID is now an adapter, not a Claude process.
4. The host calls `registerOutOfProcessTeammateTask()` regardless — because *from the host's perspective*, it spawned a pane subprocess and that's all it knows.
5. The host writes `InProcessTeammateTaskState` with `type: "in_process_teammate"`, `identity.agentName: "codex-alice"`, `permissionMode` derived from the spawn flags. The TUI renders `@codex-alice`.
6. Meanwhile, the adapter polls its inbox, claims tasks, executes them via Codex / Gemini / Kimi — the actual reasoning happens in a separate process the host knows nothing about.

**The host believes the teammate is a Claude pane process.** The adapter is the teammate; the host just thinks it spawned one of its own. The TUI row, idle status, peer DM routing, mailbox plumbing all work because the host's bookkeeping is intact. The visibility gap appears precisely because the host's *event channel* — the live tool-call stream native Claude pane processes emit — does not flow from the routed adapter.

**Consequence for §7:** visibility-parity v2 is the project of feeding that event channel back to the host (or to a parallel observer-friendly channel) without breaking the host's "this is just a pane teammate" assumption.

### 3.6 Self-registration and prompt delivery

After the shim execs the adapter, the adapter performs:

1. **Self-register** in `config.json`: take the inbox `.lock`, validate config JSON shape, append a `TeammateMember` entry if absent or upgrade `agentType`/`backendType` if stale, atomic temp+rename. Idempotent. (`src/claude_anyteam/registration.py:96-187` `[D]`)
2. **Ensure inbox file exists:** create `inboxes/<safe-agent>.json = "[]\n"` if missing, atomic write.
3. **Drain initial inbox:** the host wrote the initial prompt as an `InboxMessage` from `team-lead` before spawning. The adapter reads it (mark-as-read), uses it as the system prompt, and proceeds to its poll loop.

The adapter does NOT call `registerTask()` — that is host-internal. Its membership is `config.json`-only; the host already has the task registered from step 4 above.

### 3.7 Sequence diagram — routed pane spawn

```mermaid
sequenceDiagram
    participant Lead as Lead (host process)
    participant FS as ~/.claude file substrate
    participant Pane as Pane backend (tmux/iterm2)
    participant Shim as claude-anyteam-spawn-shim
    participant Adapter as claude-anyteam (codex-alice)
    participant Backend as Codex CLI (gpt-5.5)

    Lead->>Lead: Agent(team_name=T, name=codex-alice)
    Lead->>Lead: spawnTeammate -> handleSpawn -> isInProcessEnabled? false
    Lead->>FS: append TeammateMember to config.json (atomic temp+rename)
    Lead->>FS: ensure inboxes/codex-alice.json = []
    Lead->>FS: append initial-prompt InboxMessage to codex-alice inbox
    Lead->>Pane: PaneBackendExecutor.spawn(shellCommand)
    Pane->>Shim: exec with --agent-id, --agent-name codex-alice, ...
    Shim->>Shim: regex match ^codex- -> codex route
    Shim->>Shim: read teams/T/agents/codex-alice.json (model/effort)
    Shim->>Adapter: execv(claude-anyteam --name codex-alice --team T ...)
    Lead->>Lead: registerOutOfProcessTeammateTask() [InProcessTeammateTaskState in AppState.tasks]
    Lead-->>Lead: TUI now renders @codex-alice
    Adapter->>FS: register() — self-add or self-heal in config.json
    Adapter->>FS: read inbox; consume initial prompt
    loop poll loop (1.5s)
        Adapter->>FS: read own inbox; read tasks/T/*.json
        Adapter->>Adapter: claim pending unowned task (CAS)
        Adapter->>Backend: codex app-server ... --model gpt-5.5
        Backend-->>Adapter: agentMessage / tool events / final
        Adapter->>FS: write task_complete to team-lead inbox; mark task completed
    end
    Lead->>FS: poll team-lead inbox; render task_complete
```

---

## 4. Task lifecycle

### 4.1 Create

`TaskCreate` (host tool) or `task_create` (wrapper / vendored MCP):

```jsonc
// input
{
  "subject": "string (required, non-empty)",
  "description": "string (required)",
  "active_form": "string (optional)",
  "metadata": { "any": "json" }
}

// effect
// 1. Take tasks/<team>/.lock
// 2. Allocate id (host: .highwatermark; adapter: max(<n>.json) + 1)
// 3. Write tasks/<team>/<id>.json with status=pending, owner=null, blocks=[], blockedBy=[]
```

`[D]` (`src/claude_teams/tasks.py:63-92`, `src/claude_anyteam/wrapper_server.py:296-315`).

### 4.2 Claim (compare-and-set)

The most safety-critical primitive. Multiple teammates can race for the same pending task; exactly one wins.

```python
def claim_task(team, task_id, owner, active_form):
    with file_lock(tasks/<team>/.lock):
        current = read_task(team, task_id)
        if current.status != "pending":
            raise ValueError("not pending")
        if current.owner not in (None, "", owner):
            raise ValueError("already owned by ...")
        current.status = "in_progress"
        current.owner = owner
        current.active_form = active_form
        atomic_write(current)
```

`[D]` (`src/claude_anyteam/protocol_io.py:86-130`).

**Why inlined and not via `update_task()`:** `filelock.FileLock` instances pointing at the same path are not mutually reentrant across separate instances within one process; calling `update_task` (which would re-acquire) deadlocks. The CAS helper is therefore code-duplicated. v2 should hoist this into `claude_teams.tasks` as a first-class primitive (§10).

### 4.3 Update

`TaskUpdate` accepts `status`, `owner`, `subject`, `description`, `active_form`, `add_blocks`, `add_blocked_by`, `metadata`. The vendored substrate (`src/claude_teams/tasks.py:104-286` `[D]`) enforces:

- monotonic status (`pending → in_progress → completed`);
- cycle prevention on add_blocks/add_blocked_by (DAG validation under lock);
- automatic blocker-to-blocked rewriting on `completed`/`deleted`;
- metadata merge-by-key with `null` deletes.

**Auto-owner.** When the host sets a task to `in_progress` *without* an explicit owner, the host fills the current agent (`fragments/snippets/taskupdate_schema.txt:138-164` `[E]`). This is host-side convenience the vendored substrate does not implement.

**Assignment notification.** When `owner` is set on an existing task, the host emits a `task_assignment` mailbox message to the new owner (`src/claude_teams/server.py:597-599` `[D]`; host binary same `[E]`).

### 4.4 Block

Adapters that cannot complete a task today:
1. Set `activeForm: "blocked: <reason>"` (or similar) via `task_update`.
2. Emit a `task_blocked` mailbox message with `task_id` and `reason` to the lead.
3. Leave `status: in_progress`, do not flip to `completed`.

`[D]` (`src/claude_anyteam/loop.py:855-899`, `src/claude_anyteam/protocol_io.py:201-217`).

**v2 proposal:** add `blocked` as a first-class status. Today the substrate has no terminal "did not finish" state distinct from `pending`; "blocked but in progress" is an awkward signal that requires reading inbox and task state together to interpret.

### 4.5 Per-teammate vs team-shared task list (open)

B9 §3.4 observed during a live Agent Teams session:

> the team-shared task list the lead queries is *not* the same as the per-teammate TaskList scope individual teammates see — the architect's local TaskList returned `No tasks found` while the lead simultaneously saw task-completion notifications for the same nominal IDs.

`[E]` (live observation, B9). The host appears to maintain a per-teammate TaskList scope distinct from the team-shared file-backed list. The relationship between the two — whether per-teammate is a filter, a separate store, or a UI-only construct — is not yet documented. Tracked in §10.

### 4.6 Deletion and cleanup

- `update_task(status="deleted")` removes the task file from disk and unwires `blocks`/`blockedBy` references in sibling tasks (`src/claude_teams/tasks.py:253-284` `[D]`).
- `reset_owner_tasks(team, agent_name)` reverts every task owned by `agent_name`: clears owner, sets `status: pending` if not already `completed` (`src/claude_teams/tasks.py:306-325` `[D]`). Called on teammate force-kill, shutdown approval, or member removal.
- The host's `TaskDelete` (host UI shorthand for `update_task(status=deleted)`) preserves the highwatermark; ids do not get reused.

### 4.7 Sequence — single task, single teammate

```mermaid
sequenceDiagram
    participant Lead
    participant FS as tasks/<team>/<id>.json
    participant Adapter
    participant Backend

    Lead->>FS: TaskCreate -> 5.json {pending, owner=null}
    Adapter->>FS: read tasks/<team>/*.json (poll)
    Adapter->>FS: claim_task(5, owner=codex-alice, active_form="...")
    note right of FS: under .lock: CAS pending,null -> in_progress,codex-alice
    Adapter->>Backend: codex exec / app-server prompt
    loop visibility events (today: stderr only)
        Backend->>Adapter: agentMessage / commandExecution / fileChange
    end
    Backend-->>Adapter: final result
    Adapter->>FS: update task 5.json -> completed
    Adapter->>FS: append task_complete InboxMessage to team-lead inbox
    Lead->>FS: poll team-lead inbox; UI shows task_complete
```

---

## 5. Communication channels

The protocol comprises six communication channels. Each has a distinct purpose, audience, and persistence model.

| # | Channel | Persistence | Audience | Purpose |
|---|---|---|---|---|
| 1 | **Inbox JSON messages** | durable (file) | lead, peers | typed payloads + prose; primary lead-visible signal |
| 2 | **Task state files** | durable (file) | lead, peers | "what is this teammate doing now"; status + activeForm + metadata |
| 3 | **AppState.tasks (host mirror)** | volatile (process memory) | TUI renderer | live presence row, isIdle, progress, message preview |
| 4 | **MCP wrapper tool calls** | side-effect-only | backends | how routed CLIs invoke send_message / task_update / shadow tools |
| 5 | **Append-only event log** *(proposed)* | durable (file) | lead, future tools | full-fidelity tool/artifact stream without inbox spam |
| 6 | **Capability discovery** *(proposed)* | durable (config field) | lead, peers | what each teammate can do |

§5.1–§5.4 cover today's surfaces; §5.5–§5.6 cover proposed surfaces.

### 5.1 Inbox JSON messages

**File:** `~/.claude/teams/<safe-team>/inboxes/<safe-agent>.json` — array of `InboxMessage` rows.
**Append:** under `inboxes/.lock`.
**Read with mark-as-read:** under `inboxes/.lock`; reserializes whole array (the Pydantic-strip hazard, §1.3).

Structured payloads ride inside `text` as JSON-encoded strings. Recipients parse `text` defensively. `parse_protocol_text()` (`src/claude_anyteam/messages.py:156-186` `[D]`) is a closed dispatch table; unknown `type`/`kind` values fall through to "prose" — a tolerance that is operationally important and a visibility risk (§9).

**Complete payload taxonomy** (host emits, adapter handles, both):

| Type / kind | Direction | Schema | Notes / evidence |
|---|---|---|---|
| `task_assignment` | lead → teammate | `{type, taskId, subject, description, assignedBy, timestamp}` | Emitted on `TaskUpdate(owner=...)` `[D] [E]` |
| `idle_notification` | teammate → lead | `{type, from, timestamp, idleReason?, summary?, completedTaskId?, completedStatus?, failureReason?}` | Adapter emits every 60s when no claimable task `[D] [E]` |
| `shutdown_request` | lead → teammate | `{type, requestId, from, reason?, timestamp}` | Both `requestId` (host-canonical) and `request_id` (legacy) accepted on inbound `[E]` (host) `[D]` (adapter) |
| `shutdown_response` | teammate → lead | `{type, request_id, approve, feedback?, timestamp}` | `approve: true/false`; teammate exits gracefully on true `[D]` |
| `shutdown_approved` | teammate → lead | `{type, requestId, from, timestamp, paneId?, backendType?}` | Distinct from `shutdown_response` in host taxonomy `[E]` |
| `shutdown_rejected` | teammate → lead | `{type, requestId, from, reason, timestamp}` | Only constructed by host helper; adapter emits via `shutdown_response(approve=false)` instead `[E]` |
| `plan_approval_request` | teammate → lead | `{type, from, timestamp, planFilePath, planContent, requestId}` | Plan-mode teammates only `[E]` |
| `plan_approval_response` | lead → teammate | `{type, requestId, approved, feedback?, timestamp, permissionMode?}` | `[E]` |
| `permission_request` | teammate → lead | `{type, schema_version: 1, request_id, agent_id, tool_name, tool_use_id, description, input, permission_suggestions: []}` | First versioned payload. Gemini ACP emits in non-trusted modes `[D]` |
| `permission_response` | lead → teammate | error: `{type, request_id, subtype: "error", error}`; success: `{type, request_id, subtype: "success", response: {updated_input?, permission_updates?}}` | `[D]` |
| `sandbox_permission_request` | teammate → lead | `{type, requestId, workerId, workerName, workerColor, hostPattern: {host}, createdAt}` | Network-host approval `[E]` |
| `sandbox_permission_response` | lead → teammate | `{type, requestId, host, allow, timestamp}` | `[E]` |
| `mode_set_request` | teammate → lead | `{type, mode, from}` | Permission/agent mode changes `[E]` |
| `team_permission_update` | host → teammate | type-recognized; payload permission-update specific | `[E]` |
| `steer` | lead → teammate | `{type, message, taskId?, priority: "normal|urgent", expiresAfterTurns: 1, from?, timestamp?}` | Plus legacy `text == "steer:..."` shorthand `[D]` |
| `task_complete` | teammate → lead | `{kind: "task_complete", task_id, files_changed: [], summary, codex_exit_code}` | **adapter-emitted, kind not type.** `codex_exit_code` is a backend-leak; v2 should rename to `backend_exit_code` `[D]` |
| `task_blocked` | teammate → lead | `{kind: "task_blocked", task_id, reason}` | adapter-emitted; today's stand-in for missing `blocked` status `[D]` |
| `message` | any → any | plain prose in `text`; `summary` carries label | host wraps with `<system_reminder>` (`src/claude_teams/server.py:253-261`); wrapper does not (`src/claude_anyteam/wrapper_server.py:235-243`); drift `[D]` |
| `broadcast` | lead → all | plain prose in `text`; recipient is each teammate | host: lead-only (`src/claude_teams/server.py:423-445`); wrapper: any caller via `to="*"` (`src/claude_anyteam/wrapper_server.py:206-221`); policy drift `[D]` |

**Wire ambiguity to be aware of:**

- `request_id` (snake) vs `requestId` (camel): host emits camel, adapter accepts both via Pydantic alias and echoes whichever was sent. Spec target: emit camel canonically, accept both at the parser edge for one major version.
- `approve` (adapter) vs `approved` (host plan_approval_response): both observed. Adapters should accept both.
- `kind` (adapter task_complete/task_blocked) vs `type` (host structured messages): tracked as v2 wart; eventual unification recommended.

### 5.2 Task state files

Already covered in §1.4 and §4. Worth stating separately as a channel because:
- `activeForm` is the canonical "what now" handle. Lead UIs read it for the spinner caption.
- `metadata` is an opaque dict with no namespace convention. v2 reserves `metadata.visibility.*` for visibility events (§7) and `metadata.adapter.*` for adapter-internal state, leaving the root for user-supplied keys.
- `owner` is the routing handle for `task_assignment` notifications. Setting `owner` is itself the cross-channel signal.

### 5.3 AppState.tasks (host mirror)

Already covered in §2. Channel #3 because it is the highest-fidelity surface in the system, and it is the only one that drives the TUI presence renderer. Adapters cannot write here. Visibility v2 (§7) is the project of routing event data through observable side channels so the lead can see what the AppState mirror cannot reflect.

### 5.4 MCP wrapper tool calls

The `claude-anyteam` adapter exposes a narrowed FastMCP server (`src/claude_anyteam/wrapper_server.py` `[D]`) to the backend CLI. The backend invokes these tools to interact with the team substrate.

**EXPOSED_TOOLS** (currently 13, despite `docs/architecture.md` claim of 6 — drift, see §9):

Coordination (6):
1. `send_message(to, body, summary?)` — sender always self; refuses self-send; wildcard `to="*"` broadcasts.
2. `task_update(task_id, active_form?, status?, owner?, metadata?)` — limited to non-deleted statuses.
3. `task_create(subject, description)` — no `active_form`/`metadata` exposed.
4. `read_inbox(unread_only=true)` — own inbox only.
5. `task_list()` — all tasks.
6. `read_config()` — members with `prompt` redacted.

Shadow host tools (7) — present so the backend can do work without coupling to the harness's filesystem/shell semantics:
7. `mcp_anyteam_shell(command, cwd?, timeout?, env?)`
8. `mcp_anyteam_read_file(path, offset?, limit?)`
9. `mcp_anyteam_write_file(path, content, mode)`
10. `mcp_anyteam_list_directory(path, recursive?, glob?)`
11. `mcp_anyteam_edit_file(path, old, new, replace_all?)`
12. `mcp_anyteam_search(pattern, path, regex?, glob?)`
13. `mcp_anyteam_web_fetch(url, method?, headers?, body?)`

**BLOCKED_TOOLS** — explicitly not exposed (lifecycle/destructive):
- `team_create`, `team_delete`
- `spawn_teammate`
- `force_kill_teammate`, `process_shutdown_approved`
- `check_teammate`

`task_get` is in neither set today — accidental omission tracked in §9.

**Visibility limitation today:** wrapper tool calls produce side effects (writes to inbox / tasks / disk / network), but the call-and-result trace is not surfaced to the lead. A `mcp_anyteam_shell` failure that bubbles up to the backend's reasoning is invisible to the lead unless the backend chooses to summarize it. v2 (§7) wraps every `EXPOSED_TOOLS` handler with start/completed/failed event emission.

### 5.5 Append-only event log (proposed, `[O]`)

**File:** `~/.claude/teams/<safe-team>/events/<safe-agent>.jsonl`
**Append:** under `events/.lock` or the team's existing inbox lock.
**Read:** offset-based; one envelope per line (NDJSON).

Reasoning, drawn from B9 §6:

- A single Codex App Server turn can emit hundreds of events. Writing those to the inbox would make `read_inbox` unusable for human-readable communication.
- The mailbox semantic is "human/coordinator notification queue with `read` flag." Events are a different shape: high-volume, machine-readable, replay-friendly.
- Native Claude teammates emit equivalent richness via the host's private `task_progress` / `task_notification` stream; that stream is host-internal but not file-based. The event log gives routed teammates a comparable durable surface that other tools (dashboards, CI replay) can consume.

The event-envelope schema is in §7.

### 5.6 Capability discovery (proposed, `[O]`)

The capability sub-object on `TeammateMember` (§1.7) is the *value*; the *channel* is the act of reading the team config.

Workflow:
1. **At spawn:** the adapter writes its `capabilities` block during self-registration.
2. **At registration:** the lead's system prompt is augmented to mention each teammate's standout capabilities ("ask `codex-*` teammates to use `thread/fork` for cross-task memory continuity").
3. **At runtime:** peers reading the team config (via `read_config` MCP tool or direct file read) see capability declarations and can address each peer accordingly.

This is distinct from MCP `tools/list` because capabilities are about the *teammate's harness* — its protocol-level features — not about MCP tool surfaces. A capability declaration says "this teammate can fork its thread"; an MCP tool list says "this teammate exposes a `web_fetch` tool." Both matter.

### 5.7 Peer-to-peer addendum (north star §3)

Lead-to-peer visibility (§7) and peer-to-peer coordination are distinct surfaces with distinct failure modes. A team can satisfy north star §2 (visibility) and still fail north star §3 (peer efficiency) — the proto-rev research session itself surfaced concrete peer-efficiency leaks (multiple codex teammates couldn't `send_message` peer-to-peer because the wrapper MCP exposure varied, producing "I don't have access to a `send_message` MCP tool" error prose instead of clean delivery). This subsection documents which channels carry which direction today.

**Direction taxonomy:**

| Direction | Sender | Recipient | Purpose |
|---|---|---|---|
| `lead → peer` | team-lead | one teammate | task assignment, shutdown request, plan/permission response, broadcast |
| `peer → lead` | one teammate | team-lead | task completion, idle, plan/permission request, prose status |
| `peer → peer` | one teammate | one teammate | clarifying questions, hand-offs, capability invocation, mid-turn steer (proposed) |
| `peer → all` | one teammate | all peers (and lead) | broadcast (today: only allowed for lead in vendored server, exposed to peers in wrapper — policy drift, see §9.5) |

**Per-channel direction support today:**

| Channel | Lead→peer | Peer→lead | Peer→peer | Peer→all | Notes |
|---|---|---|---|---|---|
| **Inbox JSON (1)** | full | full | partial — wrapper exposes `send_message(to, body)` to backend; vendored server forbids teammate-to-teammate direct (`src/claude_teams/server.py:396-397` `[D]`) | wrapper allows `to="*"`; vendored server lead-only | Wrapper-vs-server policy drift is a peer-efficiency leak: peers using the vendored MCP path get blocked, peers using the wrapper succeed. |
| **Task state (2)** | yes (lead can `TaskUpdate(owner=peer)`) | yes (any teammate can `TaskUpdate` own task) | partial — peer can `TaskUpdate(owner=other_peer)` only via wrapper; not all backends use wrapper | yes (visible to all readers) | Task hand-off is peer-efficiency-critical; CAS semantics extend to peer-initiated reassignments only when wrapper enforces it. |
| **AppState.tasks (3)** | n/a (host-internal) | n/a | n/a | n/a | Host-only mirror; not a wire surface. |
| **Wrapper MCP (4)** | n/a (lead is not an MCP client) | n/a | full — `send_message`, `task_update`, `task_create`, `read_inbox`, `read_config` are all peer-callable | yes (`to="*"`) | The peer-efficiency surface lives here. Variance in *which* tools each backend wrapper exposes is a §3 leak. |
| **Event log (5, proposed)** | n/a (lead reads, doesn't write) | yes | optional — peers can subscribe to peer event logs for cross-task awareness | implicit (log is per-agent, but readable) | Peer-to-peer event observation is a v2 capability; not all teams will want it. |
| **Capability discovery (6, proposed)** | yes (lead writes capabilities at registration as observer) | yes (peer writes own at self-register) | yes (peers read others' capabilities) | yes (capability roster is global) | The cheap-flags layer is roster-wide; the rich-manifest layer is loaded on-demand by the inviting peer. |

**Payload-by-payload direction:**

| Payload | Lead→peer | Peer→lead | Peer→peer | Notes |
|---|---|---|---|---|
| `task_assignment` | yes (canonical: lead emits when setting `owner`) | rarely (peer can hand off, see below) | yes (peer hand-off via wrapper `task_update(owner=peer2)`) | Hand-off semantics need explicit `assignedBy` to track who originated. |
| `idle_notification` | n/a | yes (canonical) | rarely (peer can broadcast going-idle but not standard) | Should be filterable by message kind, not prose, to avoid drowning out signal. |
| `shutdown_request` | yes (canonical) | n/a | rarely (peer-can-request-peer-shutdown is a v2 capability question) | Today peer cannot shut down peer; lifecycle authority is lead-only. Open question: should it be? |
| `shutdown_response` / `shutdown_approved` / `shutdown_rejected` | n/a | yes | n/a | Always peer→lead. |
| `plan_approval_request` | n/a | yes (canonical) | n/a | Always peer→lead. |
| `plan_approval_response` | yes | n/a | n/a | Always lead→peer. |
| `permission_request` | n/a | yes (Gemini ACP only today) | rarely (cross-peer permission delegation is a future capability) | Could v2 admit peer→peer permission requests for cross-peer file ops? Open. |
| `permission_response` | yes | n/a | n/a | Always lead→peer today. |
| `steer` | yes (canonical) | n/a | **NO** (peer→peer steer is rejected today; only `team-lead` may emit, see `src/claude_anyteam/backends/gemini/loop.py:211-268` `[D]`) | Known §3 leak. A Codex executor cannot steer a Codex researcher mid-turn even when both peers have legitimate work to coordinate. Open question §10.11. |
| `task_complete` / `task_blocked` | n/a | yes | n/a | Adapter-emitted, lead-bound. |
| Plain prose `message` | yes | yes | yes (via wrapper) | Vendored server: peer→peer direct forbidden; wrapper: allowed. Drift. |
| Broadcast | yes (lead-only in vendored) | n/a | yes (any peer in wrapper, `to="*"`) | Wrapper-vs-server drift. |

**Six concrete §3 failure modes the protocol must address** (CLAUDE.md §3 enumeration):

1. **Peer-DM gaps.** Wrapper MCP must expose `send_message` to peers uniformly. Documented today: present in `claude_anyteam/wrapper_server.py`. Failure surface: a teammate spawned without wrapper access (or with a misconfigured wrapper) emits "I don't have access to a `send_message` MCP tool" error prose. v2: capability declaration `peer_dm: "wrapper_mcp" | "unsupported"`; system prompt teaches alternatives if unsupported.
2. **Idle noise crowding out signal.** `idle_notification` and substantive prose ride the same inbox today. Distinguishing them requires parsing `text` and inspecting `type`. v2: clients filter by `summary` prefix or by structured payload presence; mailbox readers MUST not require LLM reasoning to triage.
3. **Steer authorization too narrow.** Lead-only steer authorization is the most concrete §3 leak in the codebase today. Tracked as open question §10.11.
4. **Capability discovery latency.** Manifests cached at team formation, not lazily fetched. See §1.7 two-layer architecture.
5. **Task hand-off race conditions.** Atomic claim semantics extend to peer-initiated reassignments. Today `wrapper.task_update(owner=peer2)` doesn't enforce CAS; a v2 wrapper should.
6. **Double-send / canned-fallback noise.** Backend loops must uniformly skip canned prose fallback when a structured reply was delivered via `send_message` (the PR-#11/#12 "delivered_via_tool" guard). Codex and Kimi have it; Gemini's status varies. See `src/claude_anyteam/loop.py:260-267` and `src/claude_anyteam/backends/gemini/loop.py:284-291` `[D]`.

`[O]` for the v2 design; `[D]` for the present state surfaces.

---

## 6. Lifecycle protocols

This section gives the wire format and state machine for each of the six lifecycle protocols.

### 6.1 Idle

**Trigger:** at most every 60 seconds while no claimable task exists and the inbox is empty.

**Schema (out):**
```json
{
  "type": "idle_notification",
  "from": "<self-name>",
  "timestamp": "ISO-8601 UTC ms",
  "idleReason": "available | waiting_for_task | etc.",
  "summary": "optional",
  "completedTaskId": "optional",
  "completedStatus": "optional",
  "failureReason": "optional"
}
```

**Adapter implementation:** `src/claude_anyteam/protocol_io.py:180-182`, `messages.py:135-139`, throttle in `loop.py:152-159` `[D]`.

**Lead expectation:** receive in `team-lead.json` inbox; presence renderer flips `isIdle: true` on the corresponding `AppState.tasks` row (host-internal).

### 6.2 Shutdown

State machine:

```
                    shutdown_request                        shutdown_response(approve=true)
   [working] ───────────────────────────────▶ [pending_shutdown_decision] ─────────────────────────▶ [shutting_down] ───▶ [exited]
       │                                              │                                              ▲
       │                                              │ shutdown_response(approve=false, feedback)   │
       │                                              ▼                                              │
       └─────────────────────────────────────── [working] ──────────────────────────────────────────┘
```

**Sequence:**

```mermaid
sequenceDiagram
    participant Lead
    participant LFS as inboxes/teammate.json
    participant Teammate
    participant TFS as inboxes/team-lead.json

    Lead->>LFS: append shutdown_request {requestId, reason}
    Teammate->>LFS: read inbox; mark request as read
    Teammate->>Teammate: decide approve/reject (mid-task = reject)
    alt approve
        Teammate->>TFS: append shutdown_response {request_id, approve: true}
        Teammate->>Teammate: drain in-flight + deregister + exit
        Lead->>TFS: poll; receive shutdown_response
        Lead->>Lead: process_shutdown_approved -> kill pane + remove member + reset_owner_tasks
    else reject
        Teammate->>TFS: append shutdown_response {request_id, approve: false, feedback: "in-flight task"}
        Teammate->>Teammate: continue working
    end
```

**Idempotency.** Each `shutdown_request.request_id` is processed at most once. Adapter remembers the id; duplicate requests are ignored. Mid-task shutdown is rejected by default; the adapter sets a `shutdown_requested` flag and exits after the current task. `[D]`

### 6.3 Plan approval

For teammates spawned with `planModeRequired: true`. The teammate generates a plan (typically using its backend CLI's reasoning), sends `plan_approval_request`, blocks for `plan_approval_response`.

**Wire format (out):**
```json
{
  "type": "plan_approval_request",
  "from": "<self-name>",
  "requestId": "plan-<id>",
  "planFilePath": "/path/to/plan.md",
  "planContent": "<markdown>",
  "timestamp": "..."
}
```

**Wire format (in):**
```json
{
  "type": "plan_approval_response",
  "requestId": "plan-<id>",
  "approved": true,
  "feedback": "optional rejection feedback",
  "permissionMode": "optional override"
}
```

`[E]` (host binary `mailbox_protocol_zod_schemas.txt:35-40`) `[D]` (`src/claude_anyteam/messages.py:72-99`).

**Schema for the plan content** is validated by the adapter via `src/claude_anyteam/schemas/plan.schema.json`.

### 6.4 Permission

`PermissionRequestOut` is the only payload in the codebase with `schema_version: 1` declared explicitly. It is the pattern v2 should generalize to all payloads.

**Wire format (out, schema version 1):**
```json
{
  "type": "permission_request",
  "schema_version": 1,
  "request_id": "perm-<id>",
  "tool_name": "Read | Edit | Bash | Write | mcp_anyteam_shell | ...",
  "tool_args": { /* tool-specific */ },
  "task_id": "5",
  "teammate_name": "<self-name>",
  "trust_mode": "default | plan",
  "label": "optional human label",
  "session_id": "optional",
  "timestamp": "..."
}
```

**Wire format (in):**
```json
{
  "type": "permission_response",
  "request_id": "perm-<id>",
  "decision": "allow_once | allow_session | deny",
  "reason": "optional",
  "timestamp": "..."
}
```

`[D]` (`src/claude_anyteam/messages.py:101-120`, `src/claude_anyteam/schemas/permission_request.schema.json`).

**Backend disparity.** Codex App Server runs with `approval_policy="never"` and `sandbox="danger-full-access"`; it never emits `permission_request`. Gemini ACP emits in `default`/`plan` trust modes (auto-allows in `trusted`). Kimi has no permission bridge today. This is a clear surface for capability declaration: each adapter's capability block should declare its permission posture.

**Polling vs notification.** The adapter polls its own inbox under lock for a matching `permission_response.request_id`, marks only that single message read on match, and times out after a configurable interval (`src/claude_anyteam/protocol_io.py:271-336` `[D]`). Future v2 work should add a stable per-message id so request/response correlation is independent of array index.

### 6.5 Steer (mid-task injection)

Two delivery modes today, neither carries an ack.

**Mode 1 — Codex App Server live steer.** The adapter's task loop drains its own inbox while the backend turn is in flight, parses prose messages and `steer:` text-prefix shorthand into `SteerIn` payloads, and calls `turn_steer()` on the App Server JSON-RPC channel. Steers in flight at turn end are dropped (`src/claude_anyteam/codex.py:757-836, 908-918` `[D]`).

**Mode 2 — Gemini / Kimi next-turn-boundary steer.** Steer messages are queued and prepended to the next prompt. `SteerIn.expiresAfterTurns` controls how long a queued steer remains valid (`src/claude_anyteam/backends/gemini/loop.py:208-268` `[D]`).

**Wire format (in):**
```json
{
  "type": "steer",
  "message": "<text>",
  "taskId": "optional task scope",
  "priority": "normal | urgent",
  "expiresAfterTurns": 1,
  "from": "team-lead",
  "timestamp": "..."
}
```

Plus the `text == "steer:..."` legacy shorthand parser (`src/claude_anyteam/messages.py:164-166` `[D]`). Parsed steers should carry a `source: "legacy_text_prefix" | "structured"` field for auditability (recommendation, not yet implemented).

**No durable ack today.** A queued / delivered / expired / dropped steer is not reflected in mailbox or task state; the lead has no way to know whether its steer reached the backend turn. v2 should add an explicit `steer_ack` envelope with status (`queued | delivered_mid_turn | delivered_next_turn | expired | dropped`).

### 6.6 Subagent / task hooks (host-side)

Hook events exposed by the host for plugins:

```
PreToolUse, PostToolUse, PostToolUseFailure, PostToolBatch,
Notification, UserPromptSubmit, UserPromptExpansion,
SessionStart, SessionEnd, Stop, StopFailure,
SubagentStart, SubagentStop,
PreCompact, PostCompact,
PermissionRequest, PermissionDenied,
Setup,
TeammateIdle, TaskCreated, TaskCompleted,
Elicitation, ElicitationResult,
ConfigChange, WorktreeCreate, WorktreeRemove,
InstructionsLoaded, CwdChanged, FileChanged
```

`[E]` (host binary `fragments/snippets/hook_teammate_task_events_context.txt:6`).

**Agent Teams-specific hook payloads:**

```jsonc
{ "hook_event_name": "TeammateIdle",   "teammate_name": "...", "team_name": "..." }
{ "hook_event_name": "TaskCreated",    "task_id": "...", "task_subject": "...", "task_description": "...", "teammate_name": "...", "team_name": "..." }
{ "hook_event_name": "TaskCompleted",  "task_id": "...", "task_subject": "...", "task_description": "...", "teammate_name": "...", "team_name": "..." }
```

`[E]` (host binary `fragments/snippets/hooks_input.txt:48-51`).

These hooks fire from harness → user-provided shell scripts; they are *outbound* (the script cannot inject state back into the lead's AppState). v2.1.118 added `type: "mcp_tool"` for hooks to call MCP servers, but the inbound MCP server (`claude/tengu`) has `setAppState: () => {}` no-op, preserving the outbound-only pattern.

For routed teammates, these hooks fire on the host's representation of the teammate (which it believes is a Claude pane process). The hook payloads are therefore real and useful as observability inputs even though the actual teammate is the adapter's backend.

---

## 7. Visibility-parity v2 (the event-envelope spec)

This section is the v2 protocol expansion. It is the operational consequence of north star §1: harness preservation only delivers value if the lead can see what each harness is doing.

### 7.1 Design principle

**The envelope normalizes for routing, dedup, and channel fan-out — not for content.** A Codex `commandExecution` event carries `payload.tool_name = "commandExecution"`; a Gemini ACP `tool_call` carries `payload.tool_name = "<gemini-tool-name>"`; a Kimi `assistant.tool_calls[]` entry carries the literal Kimi tool name. The lead consumes harness-specific names. **The protocol never collapses backend-specific tool names into a generic `tool_call` only.**

The B9 §6 four-channel split is preserved:

| Channel | Purpose | Volume tolerance |
|---|---|---|
| stderr JSON logs | full forensic stream; replay; debugger | unlimited |
| Inbox mailbox | low-frequency lead-visible warnings, errors, DMs, permissions | rate-limited |
| Task `activeForm` + `metadata.visibility` | "what now" snapshot + rolling counters | sampled |
| Append-only event log (§5.5) | full-fidelity machine-readable | unlimited |

### 7.2 Event envelope

```json
{
  "kind": "turn_started | turn_progress | tool_event | artifact_event | turn_warning | turn_completed | turn_failed | visibility_degraded | steer_ack | capability_changed",
  "schema_version": 1,
  "event_id": "<agent>:<turn_or_task>:<seq>",
  "timestamp": "ISO-8601 UTC ms",
  "team": "<team-name>",
  "agent": "<agent-name>",
  "backend": "codex_app_server | codex_exec | gemini_acp | gemini_headless | kimi_headless | claude_native",
  "task_id": "<id> | null",
  "turn_id": "<backend-turn-id> | null",
  "seq": 42,
  "severity": "debug | info | warn | error",
  "visibility": {
    "mailbox": false,
    "task_state": false,
    "event_log": true,
    "stderr": true
  },
  "summary": "short human sentence for mailbox/task headers",
  "payload": { /* per-kind, see §7.3 */ }
}
```

Base fields are mandatory. `payload` is typed per `kind`. `raw_event_ref` or a redacted `raw_event_preview` MAY appear inside `payload` for forensic replay; large stdout/stderr should be referenced, not embedded.

### 7.3 Payload kinds

#### `turn_started`
```json
{
  "kind": "turn_started",
  "payload": {
    "mode": "task | prose",
    "prompt_kind": "task_complete | prose_reply | steer_only",
    "timeout_s": 900,
    "non_progress_soft_s": 300,
    "cwd": "/path",
    "model": "gpt-5.5",
    "effort": "xhigh"
  }
}
```

#### `tool_event`
```json
{
  "kind": "tool_event",
  "severity": "info",
  "payload": {
    "category": "host_tool | mcp_tool | team_tool | shadow_tool",
    "tool_name": "commandExecution | fileChange | webSearch | mcp_anyteam_shell | send_message | <gemini-acp-tool-name>",
    "phase": "started | completed | failed",
    "target": "uv run pytest tests/test_x.py",
    "status": "success | error",
    "exit_code": 0,
    "duration_ms": 1234,
    "bytes_read": 0,
    "bytes_written": 0,
    "stdout_preview": "...",
    "stderr_preview": "...",
    "raw_backend_type": "commandExecution"
  }
}
```

`category` distinguishes:
- `host_tool` — the backend's own host tools (Codex `commandExecution`/`fileChange`/`webSearch`; Gemini ACP built-ins; Kimi built-ins)
- `mcp_tool` — backend-side MCP tool calls
- `team_tool` — wrapper coordination tools (`send_message`, `task_update`, etc.)
- `shadow_tool` — wrapper host-shadow tools (`mcp_anyteam_*`)

`raw_backend_type` is intentionally preserved verbatim. The lead UI may display `commandExecution` for Codex teammates and `Bash` for native Claude teammates side-by-side; that asymmetry is a feature, not a bug — it tells the lead which harness produced the event.

#### `artifact_event`
```json
{
  "kind": "artifact_event",
  "payload": {
    "source": "codex_app_server.fileChange | adapter.git_status_sample",
    "path": "bug-triage/B9-foo.md",
    "action": "created | modified | deleted",
    "bytes_delta": 4182,
    "line_delta": 93
  }
}
```

#### `turn_progress`
```json
{
  "kind": "turn_progress",
  "summary": "running 5m01s; no visible checkpoint yet; asked Codex to checkpoint",
  "payload": {
    "elapsed_s": 301,
    "timeout_s": 900,
    "risk": "timeout_possible | none",
    "app_server_events": 383,
    "agent_message_bytes": 0,
    "mcp_tool_calls": 0,
    "host_tool_events": 0,
    "artifact_delta_bytes": 0,
    "last_checkpoint_at": null,
    "action_taken": "turn_steer_sent | none"
  }
}
```

#### `turn_completed` / `turn_failed`
```json
{
  "kind": "turn_failed",
  "severity": "error",
  "summary": "Codex App Server timed out after 900s with no final response",
  "payload": {
    "exit_code": 124,
    "error": "...",
    "elapsed_s": 900,
    "structured": false,
    "events": 383,
    "tool_call_events": 0,
    "last_message_preview": ""
  }
}
```

#### `visibility_degraded`
```json
{
  "kind": "visibility_degraded",
  "severity": "warn",
  "summary": "peer DM MCP unavailable; using protocol_io fallback",
  "payload": {
    "surface": "peer_dm | host_tool_stream | permission_bridge",
    "reason": "...",
    "impact": "...",
    "suggested_fix": "..."
  }
}
```

#### `steer_ack`
```json
{
  "kind": "steer_ack",
  "payload": {
    "steer_id": "...",
    "delivery": "queued | delivered_mid_turn | delivered_next_turn | expired | dropped",
    "task_id": "5",
    "delivered_at": "ISO"
  }
}
```

#### `capability_changed`
```json
{
  "kind": "capability_changed",
  "payload": {
    "capabilities": { "turn_steer": "live", /* ... */ }
  }
}
```

### 7.4 Channel fan-out policy

| Event kind | stderr | event log | mailbox | task state |
|---|---|---|---|---|
| `turn_started` | yes | yes | no | yes (set `activeForm`) |
| `tool_event(category=host_tool, phase=completed, status=success)` | yes | yes | no | sampled (every Nth) |
| `tool_event(phase=failed)` | yes | yes | yes | yes |
| `tool_event(category=team_tool, phase=*)` | yes | yes | no | no |
| `artifact_event` | yes | yes | coalesced (every 60-120s) | yes (rolling counter) |
| `turn_progress` | yes | yes | rate-limited (1/60s) | yes |
| `turn_completed` | yes | yes | yes (`task_complete`) | yes (`status=completed`) |
| `turn_failed` | yes | yes | yes (`task_blocked` or new failure msg) | yes |
| `visibility_degraded` | yes | yes | yes (when actionable) | no |
| `steer_ack` | yes | yes | yes (only on `expired`/`dropped`) | no |
| `capability_changed` | yes | yes | no | no (capability lives in config) |

### 7.5 Backend status today

| Backend | Events captured | Events forwarded |
|---|---|---|
| Codex App Server | `agentMessage`, `mcpToolCall`, `commandExecution` (counted), `fileChange` (counted), `webSearch` (counted) | None forwarded; counts go to stderr only. Soft non-progress steer at 300s ships (`src/claude_anyteam/codex.py:74-88, 742-835` `[D]`). |
| Codex `exec` | parsed post-exit | post-hoc digest only |
| Gemini ACP | `tool_call`, `tool_call_update`, `session/update` chunks, permission requests | permission_request bridged to mailbox; tool events drained post-prompt only |
| Gemini headless | `tool_use`, `tool_result`, `result` from stream-json | post-hoc digest only |
| Kimi headless | `assistant.tool_calls[]` from NDJSON | post-hoc digest only |

**Partial-ship status:** Codex App Server's classifier in `codex.py:74-88` is the closest the codebase has to v2-shape event capture. It counts the right things (host tool events, file changes, web searches) but does not yet emit normalized envelopes, write to `events/<agent>.jsonl`, or fan out to mailbox / task state beyond the existing soft-steer.

### 7.6 The acceptance criteria

A Codex App Server turn that emits `commandExecution` or `fileChange` produces a `tool_event` / `artifact_event` in the event log AND a coalesced task progress update without requiring stderr inspection.

A 300s no-checkpoint Codex App Server turn sends one mailbox `turn_progress` warning AND updates task `activeForm` AND does not early-interrupt unless opt-in hard interrupt is enabled.

A wrapper `mcp_anyteam_shell` failure creates a `tool_event(phase=failed)` entry in the event log AND a concise lead-visible mailbox warning if it caused the task to fail.

Gemini and Kimi headless completions produce a terminal `turn_failed | turn_completed` digest with whatever events were captured; if no partial events are available, the digest explicitly carries `partial_events_available: false`.

Existing `task_complete`, `task_blocked`, `permission_request`, `idle_notification` payloads continue to work unchanged. The visibility envelope is purely additive and explicitly versioned.

---

## 8. Ownership matrix

Who owns each surface, today and proposed.

| Surface | Host owns | Adapter owns | Substrate (claude_teams) owns | Open / shared |
|---|---|---|---|---|
| TUI presence row | yes (React + registerTask) | no | no | unreachable from outside host |
| AppState.tasks shape | yes | no (read-only via host UI) | no | E |
| Spawn argv shape | yes (PaneBackendExecutor) | no (consumes via shim) | no | D |
| `$CLAUDE_CODE_TEAMMATE_COMMAND` resolution | yes | sets via installer | no | D |
| Shim routing logic | no | yes | no | D |
| `config.json` schema | yes (canonical) | reads + writes own row | reifies as Pydantic models | D |
| `config.json` lock | yes (`config.json.lock`) | uses different lock (`inboxes/.lock`) | no | DRIFT (§9) |
| Inbox file shape | yes (defines append) | reads (own) + writes (any) | reifies | D |
| Inbox `.lock` | shared | shared | claude_teams._filelock impl | D |
| Per-inbox `<inbox>.json.lock` | yes (host RE) | not used by adapter | no | DRIFT (§9) |
| Task file shape | yes | reads + writes via update_task / claim_task | reifies | D |
| Task `.lock` | shared | shared | claude_teams._filelock | D |
| `.highwatermark` | yes (host) | n/a | no (vendored uses scan) | DRIFT (§9) |
| Native tool-call stream (Read/Edit/Bash/Write) | yes (inline in pane process) | no | no | OPEN (visibility gap) |
| Codex App Server event stream | no | yes (consumes JSON-RPC) | no | yes |
| Routed-teammate event log | n/a | proposed (events.jsonl) | no | OPEN (§7) |
| Mailbox payload taxonomy | yes (canonical for shutdown/plan/permission/sandbox/mode/team_perm) | extends with `task_complete`, `task_blocked` | reifies subset | D |
| Idle notification semantics | host renders | adapter emits | reifies model | D |
| Permission flow | yes (canonical), Gemini ACP bridges | adapter emits + scans response | schemas | D |
| Steer semantics | requires App Server / ACP | adapter implements queue | n/a | D |
| Task ownership / claim CAS | n/a (host has its own CAS internally) | yes (`protocol_io.claim_task`) | should hoist to `tasks.py` | recommendation |
| `task_assignment` notification | yes (host emits on owner=) | yes (vendored server emits on owner=) | yes | D |
| MCP wrapper exposure | n/a | yes (`wrapper_server.py`) | no | D |
| Shadow host tools (`mcp_anyteam_*`) | n/a | yes | no | D |
| Capability declaration | n/a | should write at registration | should reify as Pydantic model | OPEN (§1.7, §7) |
| `--effort` enum | n/a | five-tier protocol surface | n/a | DRIFT — Codex CLI accepts only four (§9) |
| `--model` enum | n/a (passes through) | n/a (passes through) | n/a | open per backend |
| Hook events | yes | n/a | n/a | D |
| `parent-session-id` ↔ `leadSessionId` binding | yes | reads via spawn shim | n/a | D |
| `backendType` value space | yes (`tmux`/`iterm2`/`in-process`) | writes legacy `"in-process"` | n/a | DRIFT — see §1.2 |
| `tmuxPaneId` runtime handle | yes (assigns real pane ids) | writes literal `"in-process"` | n/a | DRIFT |
| Per-teammate task list scope | yes | n/a | n/a | OPEN (§4.5) |

**Substrate-vs-host disagreements** (DRIFT) are tracked in §9.

---

## 9. Schema versions, version skew, wire ambiguity

### 9.1 No on-disk schema version

Host binary 2.1.119 has no `schemaVersion` / `schema_version` field on:
- `TeamConfig`
- `InboxMessage`
- `TaskFile`
- any structured payload except `permission_request`/`permission_response` (which carry `schema_version: 1`)

`[E]` (host binary `fragments/snippets-index.tsv` records `schema_version` searches as not found except in payloads above).

**Recommendation:** add additive `protocolVersion` on `TeamConfig` and `schema_version: 1` on every typed payload with sensible defaults so unversioned payloads continue to validate at v1.

### 9.2 Host binary version markers

Stable handles for compatibility checks:

| Marker | Source | Stability |
|---|---|---|
| Host binary version (`2.1.119`) | `process.version` exposed via env or `--version` | per release |
| ELF Build ID (`8a271a1661cb09cb7811f021a8fa3bd9b72d547d`) | binary | per build |
| `BUILD_TIME=2026-04-23T19:08:52Z` | binary metadata | per build |
| `GIT_SHA=6f68554839...` | binary metadata | per build |
| `tengu_amber_flint` flag | GrowthBook | feature gate (now removed/inlined) |
| `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` | env var | required to enable Agent Teams |

`[E]` (host binary 03 §version_markers, configuration extracts).

### 9.3 Version markers (host releases)

| Version | Change | Wire-format implication |
|---|---|---|
| v2.1.32 | Agent Teams baseline | first appearance of `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` |
| v2.1.63 | `Task` tool renamed `Agent` | both names accepted as aliases; `Task(...)` still works |
| v2.1.76 | bug — spawn command missing `claude` prefix | issue #34614; not a wire change |
| v2.1.86–88 | `Agent` tool's `team_name`+`name` parameters extracted from system prompts | LLM-facing tool surface stable |
| v2.1.98 | agent teams members inherit lead's permission mode | runtime behavior |
| v2.1.101 | `/team-onboarding` command added | UI |
| v2.1.105 | `PreCompact` hook; plugin manifest `monitors` key | extension surface |
| v2.1.113 | CLI spawns native binary via per-platform optional dependency | not a teammate spawn change |
| v2.1.114 | crash fix in permission dialog when teammate requests permission | confirms teammates can request permissions |
| v2.1.117 | `CLAUDE_CODE_FORK_SUBAGENT=1` for prompt-cache sharing | subagent path only; teammate spawn unaffected |
| v2.1.118 | hooks can call MCP tools via `type:"mcp_tool"`; `ANTHROPIC_CUSTOM_MODEL_OPTION` | new outbound hook surface; inbound MCP still no-op for AppState |
| v2.1.119 | RE'd binary the spec is grounded in (host binary 03) | baseline for all `[E]` claims |

### 9.4 Wire ambiguity

**Camel vs snake on request ids.** The host emits `requestId` (camel). Adapter Pydantic models accept both `requestId` and `request_id` via alias and round-trip whichever was sent. Adapters should:
1. Emit camel canonically for outbound payloads to the host.
2. Accept either on inbound at the parser edge.
3. Echo the form the request used in its response (so a v1.x lead with snake gets snake back).

**`type` vs `kind`.** Host structured payloads use `type` (e.g. `"type": "shutdown_request"`). Adapter `task_complete` and `task_blocked` use `kind`. v2 should pick one (preferred: `type`) and add aliases.

**`approve` vs `approved`.** `shutdown_response` uses `approve`. `plan_approval_response` uses `approved`. Both observed. v2 unification optional; the inconsistency is loud enough to be obvious.

### 9.5 Substrate drift (between host and vendored substrate)

| Drift | Host | Vendored substrate (`src/claude_teams/`) | Impact | Resolution |
|---|---|---|---|---|
| Config lock file | `config.json.lock` | uses `inboxes/.lock` (in `claude-anyteam`) | races possible if both write config concurrently | converge on `config.json.lock`; coordinate with anyteam registration |
| Per-inbox lock | `<inbox>.json.lock` sidecar | not used | host RE-only feature; adapter writes don't take it | adapter should add for parity |
| Task id allocation | `.highwatermark` file | `max(<n>.json) + 1` scan | id reuse possible after delete in vendored substrate | vendored should adopt `.highwatermark` |
| `backendType` canonical | `tmux | iterm2 | in-process` | adapter writes legacy `"in-process"` | cs50victor uses `claude | opencode` | converge on a canonical taxonomy; today host accepts `"in-process"` for routed teammates as graceful tolerance |
| Blocker resolution | `completed` only | adapter loop also accepts `deleted` | rare but possible | pick one; either accept both at substrate or always in loop |
| `<system_reminder>` wrap | host wraps direct/broadcast text | wrapper does not | inconsistent peer prompts | move to structured field, not text mutation |
| Effort tier | five-tier surface (`minimal..xhigh`) per docs | Codex CLI parses four (no `minimal`) | `--effort minimal` rejected by Codex | either add Codex `minimal` mapping or document four-tier-on-Codex |
| Wrapper tool count | `docs/architecture.md` says 6 | code exposes 13 | doc drift, not wire drift | regen docs from `EXPOSED_TOOLS` |

`[D]+[E]` for each row.

### 9.6 Recommended versioning policy for v2

1. Add `protocolVersion: 2` to `TeamConfig`. Absent => assumed v1.
2. Add `schema_version: 1` (or higher) to every typed payload. Absent => assumed v1; readers MUST tolerate.
3. Mailbox readers preserve unknown keys (the inbox-clobber leak in §1.3): never reserialize through a strict schema; update `read` by id or by raw-list index without dropping unknown fields.
4. Adapters that depend on a feature MUST gate via `capabilities.<feature>` (§1.7), not by host-version detection. Example: an adapter that needs live `turn/steer` should require `capabilities.turn_steer == "live"`, not `host_version >= 2.1.119`.

---

## 10. Open questions

These are unresolved gaps. Each is tracked here as a research line for future codex/opus work.

### 10.1 Shape of `AppState.tasks` for routed in-process teammates

**Question:** when our shim execs into `claude-anyteam` instead of `claude`, does the host's `registerOutOfProcessTeammateTask()` still produce the same `InProcessTeammateTaskState` shape, or does it carry differing fields (e.g. `cwd` is documented to appear on out-of-process mirrors but not pure in-process — does our shim path get one, the other, or both)?

**Why it matters:** the lead UI may render different badges or progress states for the two; the adapter's emitted events should target whatever shape the renderer actually consumes.

**Status:** `[O]`. Codex-trace 04 used direct-Python fallback, not nested-native-session, so the empirical answer is still not in evidence. Need a controlled test in a fresh native session with our shim active.

### 10.2 Native tool-call event taxonomy on the leader side

**Question:** when a *native Claude* teammate runs `Read` / `Edit` / `Bash`, what does the lead render and from where? Is it `lastReportedToolCount` only, or is there a host-side stream channel between the in-process Claude coroutine and the renderer that we're missing?

**Why it matters:** §7's envelope must produce events the lead UI can consume (or at least display) the same way it consumes native ones. If the host has a stream we don't see, we should either expose ourselves to it or persuade Anthropic to make it adapter-pluggable.

**Status:** `[O]`. Codex-extract 03 found `task_progress` and `task_notification` on the host's MCP/JSON-RPC channel, but the renderer's exact consumption pattern is not yet enumerated.

### 10.3 Per-teammate vs team-shared TaskList scope

**Question:** the per-teammate TaskList scope a teammate sees when calling `TaskList()` from inside a native Claude pane is *not* the same as the team-shared task file list. What is the relationship — filter, separate store, UI-only construct?

**Why it matters:** routed teammates today see only the file-backed list; if the host has a parallel scope we're not surfacing, we should either bridge it or document that routed teammates are intentionally out of scope of that view.

**Status:** `[O]`. Empirically observed in B9 §3.4. Mechanism not yet RE'd.

### 10.4 `backendType` validation by the host

**Question:** the host accepts our adapter's `backendType: "in-process"` today. Will it continue to accept arbitrary values (e.g. `"codex"`, `"gemini"`)? Or will future versions enforce a canonical enum and reject our row?

**Why it matters:** if the host's enum tightens, our registrations will fail. Gating capability declarations on `backendType` (e.g. `"backendType": "codex_app_server"`) might be the right path forward, but only if the host tolerates the value space.

**Status:** `[O]`. Recommend: capability declaration on `TeammateMember.capabilities` instead of overloading `backendType`, with `backendType` reserved for harness identity (`claude` / `codex` / `gemini` / `kimi`).

### 10.5 `events/<agent>.jsonl` lock and rotation

**Question:** the proposed event log will accumulate. Does it need rotation? Does it share `inboxes/.lock` or get its own `events/.lock`? Is there a retention policy?

**Why it matters:** at hundreds of events per Codex turn, a multi-day team session could produce gigabytes. The substrate should not be the storage of last resort; the event log might need a configurable retention (24h default) with an opt-in archive.

**Status:** `[O]`. Recommend: `events/.lock`; rotation by size (100MB) or age (24h) whichever first; opt-in archive to `events/<agent>-<date>.jsonl.gz`.

### 10.6 Capability declaration shape and migration

**Question:** §1.7 sketches a `capabilities: {...}` sub-object. The exact list of capabilities, default values, and how peers' system prompts learn about them needs more design.

**Why it matters:** this is the v2 protocol primitive that operationalizes harness preservation. Getting the shape right is load-bearing.

**Status:** `[O]`. Cross-reference: opus-elegance is filing 08 in parallel and will sharpen the recommended shape.

### 10.7 Wire-schema unification for v2

**Question:** the codebase has grown `task_complete` / `task_blocked` (kind=) alongside host's `shutdown_*` / `permission_*` / `plan_approval_*` (type=). Should v2 unify on `type` everywhere, on `kind` everywhere, or accept both with one canonical?

**Why it matters:** a clean wire schema lowers the barrier for new adapter authors. Today's split is honest historical accumulation; v2 is the moment to converge.

**Status:** `[O]`. Recommend: `type` for protocol payloads; `kind` reserved for envelope (§7.2). Adapters should emit `type` going forward and accept `kind` for legacy.

### 10.8 Cross-implementation contract test

**Question:** there is no test suite that runs cs50victor's MCP, the vendored substrate, and the adapter against each other and confirms wire compatibility. Should there be?

**Why it matters:** strategic-roadmap Phase 2 calls for spec adoption by non-Claude harnesses. A reproducible cross-impl test is the credibility artifact.

**Status:** `[O]`. Recommend: build it as part of the Phase 1 nightly CI.

### 10.9 Artifact-clobber visibility (the B9 §7.4 incident)

**Question:** when two teammates write the same file and the second clobbers the first, the substrate today provides no signal (`artifact_event(action=overwrite, previous_writer_inferred=...)` would surface it). Is this a substrate fix (file substrate gains writer attribution) or an event-log fix (adapters detect and emit)?

**Why it matters:** parallel multi-author workflows are exactly the use case the project enables. Silent clobbers are a coordination tax on parallelism.

**Status:** `[O]`. Recommend: adapter-side `artifact_event` with peer-write detection sampling; substrate-side `peerWritersSeen` field on adapter-emitted events.

### 10.10 Visibility-event acknowledgment / replay

**Question:** if the lead is restarted or the session reloaded, does it replay the event log to reconstruct teammate state? Is the event log idempotent on replay? Does it carry replay-safe `event_id`s?

**Why it matters:** session continuity is a real workflow. The envelope's `event_id` (`<agent>:<turn_or_task>:<seq>`) should be deterministic enough for de-dup; the channel design should accommodate replay.

**Status:** `[O]`.

### 10.11 Peer-to-peer steer authorization

**Question:** today only `team-lead` may emit `SteerIn` (`src/claude_anyteam/backends/gemini/loop.py:211-268` `[D]`; equivalent guard in Codex App Server path). A Codex executor cannot steer a Codex researcher mid-turn even when both have legitimate cross-task work to coordinate. Should peer→peer steer be admitted? Under what authorization?

**Why it matters:** north star §3 (peer efficiency). Lead-only steering is fine for small teams where the lead is always present, but for long-running teams or scheduled/autonomous workflows where the lead is offline, peers need to coordinate without lead mediation. The proto-rev session itself made this concrete — opus teammates wanted to steer codex teammates while team-lead was synthesizing.

**Recommended resolution:**
- Add `accepts_peer_steer: bool` to the capability declaration (§1.7). Default `false`.
- Each adapter independently decides whether to accept steers from non-lead peers; the answer is published in capabilities.
- Peer-emitted steer messages MUST carry `from: <peer-name>` so the recipient adapter can authorize per its declared policy.
- Peer-steer rejection is silent at the wire level (no error prose); the adapter logs the rejection and continues.
- Capability-flagged peer steer is the right level of indirection: it preserves harness preservation (each harness can decide), satisfies peer efficiency (peers can coordinate when both opt in), and remains visible to the lead via `steer_ack` envelopes.

**Status:** `[O]`. Cross-reference: opus-elegance leak catalog for the tracking entry.

### 10.12 Peer-DM exposure consistency

**Question:** wrapper MCP `send_message` exposure varies across backends (Gemini's ACP-mode adapter, Kimi's headless, Codex App Server). When a backend's wrapper does not expose `send_message` to its model context, the model emits "I don't have access to a `send_message` MCP tool" error prose. How should the protocol detect and surface this configuration leak?

**Why it matters:** north star §3 (peer efficiency). Concrete failure observed during the proto-rev research session, 2026-04-27. The team appeared to function (lead saw task progress) while peers silently couldn't reach each other.

**Recommended resolution:**
- Capability declaration `peer_dm: "wrapper_mcp" | "lead_relay" | "unsupported"`.
- `lead_relay` is a fallback where a peer asks the lead to forward a message; `unsupported` is the explicit "this teammate cannot DM peers."
- The wrapper builder asserts `send_message` is exposed when `peer_dm == "wrapper_mcp"` is declared, else the registration fails fast.
- The lead's system prompt teaches peers about each other's `peer_dm` posture: "`gemini-bob` cannot DM peers directly; relay through team-lead."

**Status:** `[O]`.

---

## 11. Compliance checklist

To be conformant with v2, an adapter and harness combination MUST satisfy these criteria. Criterion #1 is the architectural criterion; everything else flows from it.

### Criterion 1 — Preserves harness capabilities natively (north star §1)

- [ ] The teammate process IS the harness CLI (or its native subprocess), not an LLM wrapping the harness as a tool.
- [ ] The adapter does not strip the harness's tool surface, prompt tuning, approval policy, or session semantics to fit a homogeneous protocol surface.
- [ ] Where the harness has a unique capability not shared by peers (e.g. Codex `thread/fork`, Gemini ACP permission bridge, Kimi swarm), the adapter MUST surface it in the `capabilities` declaration (§1.7) and the lead's system prompt SHOULD teach peers how to invoke it.
- [ ] The protocol-side normalizations (`--effort` five-tier enum, `--model` slug pass-through) are the only flat-projection knobs. Every other capability flows through unmodified.

### Criterion 2 — Visibility (north star §2, operational consequence of #1)

- [ ] The lead can observe the teammate's host-tool activity (Read/Edit/Bash equivalents inside the harness) without reading stderr or tmux scrape.
- [ ] Errors and timeouts carry diagnostic detail comparable to native errors, not generic prose fallbacks.
- [ ] The visibility envelope (§7) preserves backend-specific tool names; the lead consumes harness-specific richness rather than a flattened `tool_call` only.
- [ ] Idle reasons, permission requests, mid-task steers, and task completions are first-class typed payloads, not opaque prose.

### Criterion 3 — On-disk substrate

- [ ] Reads `~/.claude/teams/<safe-team>/config.json` at startup; extracts own identity (`agentId == "<name>@<team>"`) from spawn argv.
- [ ] Self-registers into `members[]` if absent, idempotently; uses atomic temp+rename under the canonical lock (`config.json.lock` recommended; `inboxes/.lock` accepted as legacy).
- [ ] Polls `~/.claude/teams/<safe-team>/inboxes/<safe-agent>.json` at 1.5s default cadence.
- [ ] Reads task files from `~/.claude/tasks/<safe-task-list>/<id>.json`; never claims a task with non-empty `blockedBy` whose blockers are not yet `completed` (or `deleted`, by configurable policy).
- [ ] Uses the documented sanitization rules for team and agent names in directory paths.
- [ ] Preserves unknown fields on inbox messages, task files, and config rows on round-trip (no Pydantic-strip clobbering).

### Criterion 4 — Lifecycle protocols

- [ ] Sends `idle_notification` at most every 60 seconds when no claimable task and no inbox messages.
- [ ] Responds to `shutdown_request` with `shutdown_response`; idempotent on `request_id`; mid-task shutdown defers to next idle.
- [ ] If `planModeRequired: true`, sends `plan_approval_request` and blocks for `plan_approval_response`; tolerates rejection feedback.
- [ ] Permission requests/responses use `schema_version: 1` and are scanned by `request_id` (not by inbox index).
- [ ] Steers are processed live where the harness supports it, and queued at the next-turn boundary otherwise; emits `steer_ack` (proposed v2).
- [ ] Task claims use compare-and-set under `tasks/<team>/.lock`.

### Criterion 5 — Wire compatibility

- [ ] Accepts both `requestId` (camel) and `request_id` (snake) on inbound payloads.
- [ ] Emits camel by default for outbound payloads to the host; echoes the form the request used in responses.
- [ ] Tolerates unknown payload `type`/`kind` values by treating them as prose, not by raising; logs a `protocol_error` diagnostic for malformed known types (proposed v2).
- [ ] Honors per-teammate `agents/<name>.json` overrides when they exist (whitelisted keys: `model`, `effort`, `turn_timeout_s`).

### Criterion 6 — Visibility envelope (proposed v2)

- [ ] Emits `turn_started` and `turn_completed | turn_failed` for every backend turn.
- [ ] Emits `tool_event` for every observable host-tool, MCP-tool, team-tool, and shadow-tool invocation.
- [ ] Emits `artifact_event` for file mutations the backend reports or the adapter samples.
- [ ] Emits `turn_progress` at least every 60s during long turns; sends a mailbox warning at 300s no-checkpoint (Codex App Server today; generalize to all backends per v0.7.0 roadmap).
- [ ] Emits `visibility_degraded` when an expected surface (peer DM, host-tool stream, permission bridge) is missing or fails.
- [ ] Writes envelopes to `events/<agent>.jsonl` regardless of channel policy.
- [ ] Rate-limits mailbox writes per the §7.4 fan-out table.

### Criterion 7 — Capability declaration (proposed v2)

- [ ] Writes `capabilities: {...}` block to its `members[]` row at registration.
- [ ] Declares at minimum: `turn_steer`, `thread_fork`, `permission_bridge`, `host_tool_visibility`, `session_persistence`, `cancellation`.
- [ ] Treats unknown capability values as `unsupported` / `false`; never crashes on capability values it doesn't understand.

### Criterion 8 — Substrate hygiene

- [ ] Atomic temp+rename for `config.json` writes (whatever lock).
- [ ] Append under lock for inbox writes.
- [ ] Compare-and-set for task claim and terminal transitions.
- [ ] Best-effort retry on inbox / task / config read parse failures (transient race tolerance).
- [ ] Cleans up its inbox file on `deregister()` after approved shutdown; leaves config + inbox intact on crash for forensic inspection.

### Criterion 9 — Peer efficiency (north star §3, the team productivity invariant)

This criterion ensures peers coordinate among themselves at native Claude pace, not just that the lead can observe them. Failure modes here are operationally distinct from §2 visibility leaks: a team can satisfy §2 and still fail §3.

- [ ] Wrapper MCP exposes `send_message` to all peers, not just to the lead. Peer-DM is a first-class wrapper surface, not a privileged subset of lead-to-peer routing.
- [ ] If `send_message` is intentionally not exposed for a backend, the capability declaration MUST set `peer_dm: "lead_relay"` or `peer_dm: "unsupported"` and the lead's system prompt MUST teach peers the alternative.
- [ ] Inbox messages are filterable by message kind (`type` / `kind` / `summary` prefix) without parsing prose. Heartbeat `idle_notification` MUST be distinguishable from substantive peer messages by structured field, not by text classification.
- [ ] Capability manifests are loaded into peer context at team-formation time, not lazily fetched per invocation. Peers should not pay round-trip MCP costs every time they consider invoking a peer's primitive.
- [ ] Atomic claim semantics extend to peer-initiated task hand-offs (`task_update(owner=peer2)` from a peer wrapper). CAS is the primitive; ownership rotations are still gated on it.
- [ ] Backend loops uniformly skip the canned prose fallback when a structured reply was delivered via `send_message` (the "delivered_via_tool" guard). Peers must not receive a structured reply *and* a redundant prose fallback for the same answer.
- [ ] Peer-to-peer steer authorization is declared per teammate via `capabilities.accepts_peer_steer` (proposed v2). Peers respect each other's declared posture.
- [ ] Wrapper-vs-server policy drift on direct/broadcast routing is resolved (today: vendored server forbids peer→peer direct, wrapper allows it; pick one and align). See §9.5.

---

## 12. Sources and evidence

### 12.1 Primary sources (this repository)

- `CLAUDE.md` — project north stars (harness preservation §1, visibility parity §2)
- `docs/architecture.md` — narrative architecture
- `docs/roadmap.md` — shipping plan
- `docs/internal/strategic-roadmap.md` — long-term thesis (Phase 2 protocol publication)
- `docs/internal/2026-prototype/protocol-spec.md` — v1.1 prior art (superseded by this doc)
- `bug-triage/B9-visibility-parity-investigation.md` — visibility taxonomy + envelope proposal
- `docs/internal/spawn-research-findings.md` — Phase 1+2 spawn-mechanism research
- `src/claude_teams/` — vendored MCP substrate (file protocol implementation)
- `src/claude_anyteam/` — adapter, wrapper, shim, backends
- `src/claude_anyteam/schemas/` — JSON schemas (task_complete, plan, permission_request, permission_response)

### 12.2 Codex teammate outputs (this synthesis is derived from)

- `docs/internal/protocol-rev/01-clawclone-findings.md` — claw-code-agent (not an Agent Teams impl, but event-taxonomy ideas)
- `docs/internal/protocol-rev/02-clones-survey.md` — 13 candidate clones surveyed; cs50victor canonical, maorinka/claude-rs Rust modules, Pickle-Pixel/HydraTeams native-host/routed-brain alternative, aproto/codex-bridge same-problem comparator, Piebald-AI prompt corpus, nwyin cleanroom
- `docs/internal/protocol-rev/03-host-binary-extract.md` — Claude Code v2.1.119 binary RE: SHA, Build ID, file shapes with binary-offset evidence, mailbox protocol payload taxonomy, hook event enum, MCP/JSON-RPC method surface, task stream events
- `docs/internal/protocol-rev/04-runtime-trace.md` — file-protocol ground truth: atomic temp+rename for config, .lock-protected rewrites for inbox/tasks, `--parent-session-id` ↔ `leadSessionId` binding empirically confirmed
- `docs/internal/protocol-rev/05-claudeteams-substrate.md` — full layered map (substrate → vendored MCP → wrapper → adapter → host); 23 code-only invariants; race-condition catalog; B9 §6 envelope drift analysis
- `docs/internal/protocol-rev/06-prior-art-survey.md` — ACP / MCP / A2A / LSP / LangGraph / CrewAI / AutoGen / OpenHands / Cursor / Cline / Continue / AGNTCY: capability-negotiated session pattern, typed progress notifications, manifest-style identity discovery

### 12.3 Vendored external references

Under `references/external-claude-code-re/`:
- `host-binary-extract/` — extracted from Claude Code v2.1.119 binary (SHA-256 `cca43053f062949495596b11b6fd1b59cf79102adb13bacbe66997e6fae41e4a`)
- `runtime-trace/` — sandbox `observe-trace` team file mutations
- `protocol-specs/` — ACP v0.12.2, MCP 2025-11-25, A2A v1.0.0
- `maorinka-claude-rs/`, `pickle-pixel-hydrateams/`, `aproto-codex-bridge/`, `piebald-claude-code-system-prompts/`, `nwyin-claude-cleanroom-2-1-83/`, `clawcode/`

### 12.4 Public references

- Anthropic Claude Code docs: `code.claude.com/docs/en/agent-teams`, `/sub-agents`, `/env-vars`, `/model-config`, `/mcp`, `/llm-gateway`, `/plugins-reference`, `/changelog`
- cs50victor MCP: `github.com/cs50victor/claude-code-teams-mcp`
- nwyin Agent Teams article: `dev.to/nwyin/reverse-engineering-claude-code-agent-teams-architecture-and-protocol-o49`
- GitHub issues: `anthropics/claude-code` #34614 (non-interactive in-process forcing), #40168 (MAX_CANON tmux truncation), #26572 (CustomPaneBackend proposal), #31977 (in-process subagent gap), #51818, #52245
- Piebald-AI system prompts: `github.com/Piebald-AI/claude-code-system-prompts`
- ACP: `agentclientprotocol.com`, `github.com/agentclientprotocol/agent-client-protocol`
- MCP: `modelcontextprotocol.io`, `github.com/modelcontextprotocol/modelcontextprotocol`
- A2A: `a2a-protocol.org`, `github.com/a2aproject/A2A`
- LSP: `microsoft.github.io/language-server-protocol/`

### 12.5 Evidence-tag distribution

Of the load-bearing claims in this document:

- `[D]` (documented in code or public docs): substrate file shapes, lock semantics, payload schemas, lifecycle state machines, MCP wrapper tool list, adapter lifecycle helpers.
- `[E]` (empirically inferred): host binary's `agentId` construction helpers, `backendType` host enum values, per-inbox `.json.lock` sidecar, host's `.highwatermark`, `task_progress`/`task_notification` stream events, hook event enum, in-memory `AppState.tasks` shape.
- `[O]` (open): registerTask sequence under live routed spawn (§10.1); native tool-call render channel (§10.2); per-teammate TaskList scope (§10.3); `backendType` future enforcement (§10.4); event log lock and rotation (§10.5); capability declaration shape (§10.6); v2 wire unification (§10.7); cross-impl contract test (§10.8); artifact-clobber visibility (§10.9); event replay (§10.10); the entire visibility envelope (§7) until shipped.

---

**End of specification.**

This document supersedes `docs/internal/2026-prototype/protocol-spec.md` v1.1. Subsequent revisions should update §9.3 with new host releases, narrow `[O]` claims as research closes them, and incrementally evolve §7 as the visibility envelope ships behind capability flags. The substrate sections (§1–§4) are stable; the visibility and capability sections (§5.5, §5.6, §7, §10.6) are the active surface.

The protocol is a coordinator, not a kernel.
