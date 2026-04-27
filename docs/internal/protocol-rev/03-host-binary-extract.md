# 03 — Host binary extract: Claude Code Agent Teams protocol

## Scope and method

This note reverse-engineers Agent Teams from the live Claude Code host binary rather than from clone repos or runtime observation. In this shell `$CLAUDE_CODE_EXECPATH` was empty, so the binary was resolved through `PATH` to `/home/rosado/.local/bin/claude` -> `/home/rosado/.local/share/claude/versions/2.1.119`. The vendored extraction directory is `references/external-claude-code-re/host-binary-extract/`.

Binary identity:

- Version: `2.1.119 (Claude Code)`.
- SHA-256: `cca43053f062949495596b11b6fd1b59cf79102adb13bacbe66997e6fae41e4a`.
- ELF Build ID: `8a271a1661cb09cb7811f021a8fa3bd9b72d547d`.
- Embedded build metadata: `BUILD_TIME=2026-04-23T19:08:52Z`, `GIT_SHA=6f68554839756189e277b8285a18fe47acd9a5a1`.

Extraction map:

| Artifact | Evidence |
|---|---|
| Binary metadata | `references/external-claude-code-re/host-binary-extract/raw/binary-identification.txt` |
| ELF `.bun` section | `references/external-claude-code-re/host-binary-extract/symbols/readelf-sections.txt` |
| Embedded CLI bundle | `references/external-claude-code-re/host-binary-extract/bunfs/root-src-entrypoints-cli.bundle.min.js` |
| Offset map | `references/external-claude-code-re/host-binary-extract/bunfs/extracted-bundle-offsets.txt` |
| Targeted snippets | `references/external-claude-code-re/host-binary-extract/fragments/snippets/*.txt` |
| Derived shapes | `references/external-claude-code-re/host-binary-extract/fragments/derived-json-shapes.md` |

The `.bun` section begins at binary offset `0x67a8000`; the first embedded `cli.js` source string begins at binary offset `0x67a845c` and is 13,720,393 bytes long. Snippets include both bundle offsets and binary offsets, so each finding below can be traced back to the host binary.

## Agent/team identity and name sanitization

Claude Code represents a teammate identity as an agent name plus a team name joined with `@`:

```text
agentId = "<agent-name>@<team-name>"
```

The bundle helper `jd(name, team)` constructs that string and `S_$(agentId)` parses it back into `{agentName, teamName}`. Team names used on disk are sanitized by replacing non-alphanumerics with `-` and lowercasing; task list names allow `_` as well. Agent names for team files replace `@` with `-`.

Evidence:

- `fragments/snippets/team_paths_and_mailbox.txt:8-18` — `jd`, `S_$`, and mailbox path helpers.
- `fragments/snippets/team_file_read.txt:25-40` — team filename helpers, sanitizer, read/write/update lock path.
- `fragments/snippets/task_update_rH.txt:36-42` — task list ID and task path sanitizer.

## On-disk files

### Team config

Team state lives under the Claude config directory as:

```text
~/.claude/teams/<safe-team>/config.json
~/.claude/teams/<safe-team>/config.json.lock
~/.claude/teams/<safe-team>/inboxes/<safe-agent>.json
~/.claude/teams/<safe-team>/inboxes/<safe-agent>.json.lock
```

The extracted file helpers build `getTeamDir(team)`, `getTeamFilePath(team)`, create parent directories, and update `config.json` under a `config.json.lock` lockfile. Team deletion documentation in the binary says it removes `~/.claude/teams/{team-name}/` and `~/.claude/tasks/{team-name}/`.

Reconstructed `config.json` shape:

```jsonc
{
  "name": "proto-rev",
  "description": "optional team description",
  "createdAt": 1770000000000,
  "leadAgentId": "team-lead@proto-rev",
  "leadSessionId": "<session id>",
  "members": [
    {
      "agentId": "team-lead@proto-rev",
      "name": "team-lead",
      "agentType": "team-lead",
      "model": "<model>",
      "joinedAt": 1770000000000,
      "tmuxPaneId": "",
      "cwd": "/workspace",
      "subscriptions": []
    },
    {
      "agentId": "codex-extract@proto-rev",
      "name": "codex-extract",
      "color": "blue",
      "joinedAt": 1770000000000,
      "tmuxPaneId": "%12",
      "backendType": "tmux|iterm2|in-process",
      "subscriptions": [],
      "agentType": "researcher",
      "model": "<model>",
      "prompt": "<initial prompt>",
      "planModeRequired": false,
      "cwd": "/workspace",
      "mode": "optional mode",
      "isActive": true
    }
  ],
  "hiddenPaneIds": ["%12"]
}
```

The leader entry is created by `TeamCreate`; teammate entries are reserved before spawn and then patched with `tmuxPaneId` and `backendType`. `hiddenPaneIds`, member `mode`, and `isActive` are maintained by separate mutators.

Evidence:

- `fragments/snippets/team_config_create_tool_actual.txt:10-17` — `TeamCreate` config object, leader member, and TeamDelete path prompt.
- `fragments/snippets/pane_spawn_extra_flags_BM7.txt:69-78` — reservation and backend/pane patch.
- `fragments/snippets/team_file_read.txt:48-83` — hidden panes, member mode, and active-state update helpers.
- `fragments/derived-json-shapes.md` — normalized config shape.

### Mailboxes

Each teammate has an inbox JSON array:

```jsonc
[
  {
    "from": "team-lead",
    "text": "plain text or JSON protocol string",
    "timestamp": "2026-04-27T00:00:00.000Z",
    "read": false,
    "color": "cyan",
    "summary": "optional one-line summary"
  }
]
```

`writeToMailbox` ensures the inbox directory exists, creates an empty `[]` file if needed, locks `<inbox>.json.lock`, appends `{...message, read:false}`, and writes pretty JSON. Reads return `[]` on ENOENT. The same file has helpers to mark by index, mark all, mark by predicate, and clear an inbox.

Evidence:

- `fragments/snippets/team_paths_and_mailbox.txt:18-40` — inbox path, read, unread filter, write semantics.
- `fragments/snippets/mailbox_mark_read.txt:32-57` — mark-read and clear helpers.
- `fragments/snippets/mailbox_write.txt:64-106` — protocol object constructors and type recognizers.

### Task-list files

The persistent team task list lives at:

```text
~/.claude/tasks/<safe-task-list>/<task-id>.json
~/.claude/tasks/<safe-task-list>/.lock
~/.claude/tasks/<safe-task-list>/.highwatermark
```

The task-list ID is `CLAUDE_CODE_TASK_LIST_ID` if set; otherwise current team context, current team name, a dynamic in-memory task list, or the session ID. Task IDs are monotonic decimal strings. `.highwatermark` prevents reuse after deletion/reset, and `.lock` serializes task directory updates.

Reconstructed task JSON shape:

```jsonc
{
  "id": "3",
  "subject": "Reverse-engineer host binary",
  "description": "optional details",
  "activeForm": "Reverse-engineering host binary",
  "owner": "codex-extract",
  "status": "pending|in_progress|completed",
  "blocks": [],
  "blockedBy": [],
  "metadata": { "any": "json value" }
}
```

`TaskUpdate` accepts a transient `deleted` status but that deletes the JSON file instead of being persisted.

Evidence:

- `fragments/snippets/task_update_rH.txt:18-25` — `.highwatermark` helpers.
- `fragments/snippets/task_update_rH.txt:36-42` — task list ID, directory, and file path.
- `fragments/snippets/task_update_rH.txt:56-75` — create/update write path and per-task lock.
- `fragments/snippets/task_update_rH.txt:94-143` — `.lock` and Zod shape for persisted task records.
- `fragments/snippets/taskupdate_schema.txt:138-164` — `TaskUpdate` input schema, auto-owner behavior, assignment message emission.

## Tool API surface

The binary contains Agent Teams/coordinator tool names:

- `TeamCreate`
- `TeamDelete`
- `SendMessage`
- `TaskCreate`
- `TaskGet`
- `TaskList`
- `TaskUpdate`
- `Skill` (not team-specific, but adjacent in public tool constants)

The extracted artifact `analysis/tool-names-from-bundle.txt` records these names and evidence offsets.

### TeamCreate / TeamDelete

`TeamCreate` input is:

```jsonc
{
  "team_name": "string",
  "description": "optional string",
  "agent_type": "optional string for the lead role"
}
```

It refuses to create a second active team in the same leader session, writes `config.json` exclusively, initializes the task directory for the sanitized team, stores `teamContext` in app state, and returns `team_name`, `team_file_path`, and `lead_agent_id`.

`TeamDelete` has empty input. It refuses to delete a team while non-leader members are still active, then removes team/task resources and clears session team context.

Evidence: `fragments/snippets/team_config_create_tool_actual.txt:10-20`.

### SendMessage

`SendMessage` writes either plain text or a small set of structured control messages to a teammate inbox. Important behavioral constraints extracted from the validation path:

- `to` is a single teammate name, not `*`, and not an `@`-qualified agent ID.
- Plain string messages require a `summary`.
- `shutdown_response` messages must target `team-lead`.
- User-facing guidance tells agents not to hand-write structured JSON status messages; system code emits those automatically.

Plain messages become mailbox rows with `from`, `text`, `summary`, `timestamp`, and `color`.

Evidence:

- `fragments/snippets/send_message_tool_actual.txt:6` — `SendMessage` schema, validation, and dispatch window.
- `fragments/snippets/team_config_members_create.txt:10-15` — prompt rules for teammate communication.
- `fragments/snippets/mailbox_write.txt:29-36` — write path used by SendMessage and other emitters.

### TaskCreate / TaskGet / TaskList / TaskUpdate

`TaskCreate` input: `subject`, `description`, optional `activeForm`, optional `metadata`. It persists a pending task with empty `blocks` and `blockedBy` arrays.

`TaskGet` input: `taskId`; output: `task` or `null`.

`TaskList` output: summaries with `id`, `subject`, `status`, optional `owner`, and `blockedBy`.

`TaskUpdate` input includes `taskId`, optional `subject`, `description`, `activeForm`, `status`, `owner`, `metadata`, `addBlocks`, and `addBlockedBy`. In team mode, if an unowned task is set to `in_progress` without an explicit owner, the current agent is set as owner. When an owner is assigned in team mode, the host sends that teammate a `task_assignment` mailbox message.

Evidence:

- `fragments/snippets/task_file_tools_schema.txt` — task tool and hook constants window.
- `fragments/snippets/task_update_rH.txt:56-87` — create/read/list/update filesystem implementation.
- `fragments/snippets/taskupdate_schema.txt:138-164` — `TaskUpdate` schema and assignment notification path.

## Spawn protocol and CLI arguments

Claude Code supports pane-backed teammates (`tmux`/`iterm2`) and in-process teammates. The execution mode is controlled by `teammateMode` in settings or CLI (`auto`, `tmux`, `in-process`). The settings schema includes `teammateMode` as an optional value described as how spawned teammates execute.

Hidden teammate CLI args are parsed only when Agent Teams is enabled:

```text
--agent-id <agentId>
--agent-name <name>
--team-name <team>
--agent-color <color>
--parent-session-id <sessionId>
--plan-mode-required
--agent-type <agentType>
--teammate-mode auto|tmux|in-process
```

`--agent-id`, `--agent-name`, and `--team-name` must be supplied together. If present, the host calls `setDynamicTeamContext` with the parsed identity. `--teammate-mode` provides a mode override.

Pane-backed spawn command shape:

```text
cd <cwd> && env \
  CLAUDECODE=1 \
  CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1 \
  <selected pass-through env> \
  <claude-executable> \
  --agent-id <id> \
  --agent-name <safe-name> \
  --team-name <team> \
  --agent-color <color> \
  --parent-session-id <session> \
  [--plan-mode-required] \
  [--agent-type <type>] \
  [--dangerously-skip-permissions | --permission-mode acceptEdits|auto] \
  [--model <model>] \
  [--settings <settings>] \
  [--plugin-dir <dir> ...] \
  [--chrome|--no-chrome]
```

Executable resolution uses `CLAUDE_CODE_TEAMMATE_COMMAND` when set; otherwise it uses the native `process.execPath` in packaged mode or `process.argv[1]`. Spawn sets `CLAUDECODE=1` and `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` and passes through selected provider, proxy, certificate, remote, and config env vars. Before sending the command to the pane, the host clears the teammate inbox and writes the initial prompt as a mailbox message from `team-lead`.

In-process spawn reserves a member with `backendType:"in-process"`, registers an `AppState.tasks` entry directly, and passes the prompt to the in-process runner rather than bootstrapping through a pane command.

Evidence:

- `fragments/snippets/cli_hidden_agent_args_parser_nk5.txt:19-21` — hidden arg object parser.
- `fragments/snippets/pane_spawn_extra_flags_BM7.txt:8-32` — executable/env/extra-flag construction.
- `fragments/snippets/pane_spawn_extra_flags_BM7.txt:84-99` — split-pane spawn command and initial inbox message.
- `fragments/snippets/pane_spawn_extra_flags_hM7.txt:61-83` — `--teammate-mode` and env injection in alternate extra-flags helper.
- `fragments/snippets/spawnInProcessTeammate_Mr_function.pretty.txt:4-109` — in-process task registration.
- `fragments/snippets/settings_schema_teammate.txt` — `teammateMode` setting.

## `registerTask`, mirror task path, and `AppState.tasks`

The task registry wraps `AppState.tasks`. `register` inserts a task, emits a `system/task_started` stream event, and `update` emits `system/task_updated` with a patch. `remove` and `evictTerminal` clean up task output/terminal state. The persisted stream patch exposes only safe task fields such as `status`, `description`, `end_time`, `total_paused_ms`, `error`, and `is_backgrounded`.

Evidence:

- `fragments/snippets/registerTask_emit.txt` — task registry `register`, `update`, `remove`, `evictTerminal`, and emitted stream events.
- `fragments/snippets/base_task_x2.txt:46-61` — base task factory: IDs, `status:"pending"`, `description`, `toolUseId`, `startTime`, `outputFile`, `outputOffset`, `notified`.
- `fragments/snippets/task_patch_pp.txt` — stream patch field selection.

### In-process teammate task shape

The in-memory `AppState.tasks` record for an in-process teammate has the base task fields plus teammate-specific state:

```jsonc
{
  "id": "t<random>",
  "type": "in_process_teammate",
  "status": "running|completed|failed|killed",
  "description": "codex-extract: <prompt prefix>",
  "toolUseId": "<tool use id>",
  "startTime": 1770000000000,
  "outputFile": "<task-output-path>",
  "outputOffset": 0,
  "notified": false,
  "cwd": "/workspace", // present on out-of-process mirror tasks; pure in-process task construction omits this
  "identity": {
    "agentId": "codex-extract@proto-rev",
    "agentName": "codex-extract",
    "teamName": "proto-rev",
    "color": "blue",
    "planModeRequired": false,
    "parentSessionId": "<session id>"
  },
  "prompt": "<initial prompt>",
  "model": "<model>",
  "abortController": "<AbortController>",
  "unregisterCleanup": "<function>",
  "awaitingPlanApproval": false,
  "spinnerVerb": "Working",
  "pastTenseVerb": "Worked",
  "permissionMode": "default|plan|acceptEdits|auto|bypassPermissions",
  "isIdle": false,
  "shutdownRequested": false,
  "lastReportedToolCount": 0,
  "lastReportedTokenCount": 0,
  "pendingUserMessages": [],
  "messages": [],
  "progress": "optional progress object",
  "inProgressToolUseIDs": [],
  "currentWorkAbortController": "optional AbortController",
  "totalPausedMs": 0,
  "endTime": 1770000000000,
  "error": "optional"
}
```

Selectors identify tasks by `type === "in_process_teammate"`, filter running teammates, sort by `identity.agentName`, and find tasks by `identity.agentId`. Message injection appends to `pendingUserMessages` and the task-local `messages` transcript. Shutdown flips `shutdownRequested`.

Evidence:

- `fragments/snippets/spawnInProcessTeammate_Mr_function.pretty.txt:56-87` — in-process task construction and registry call.
- `fragments/snippets/appstate_tasks_selector.txt:53-67` — selectors, injection, append, shutdown, and kill path.
- `fragments/snippets/inprocess_runner_QBH.txt` and `fragments/snippets/inprocess_runner_poll_bk1.txt` — runner/poll loop and idle behavior.

### Out-of-process mirror path

Pane-backed teammates are mirrored into the same `AppState.tasks` collection via `FM7`, which creates a task with `type:"in_process_teammate"` even though execution is out of process. This mirror/control task holds the identity, prompt, `cwd`, permission mode, and an `AbortController`; aborting it kills the backing pane when `backendType` is `tmux` or `iterm2`. The mirror task does not run the model loop itself.

Evidence: `fragments/snippets/mirror_out_of_process_FM7_full.txt:32-73`.

## Mailbox protocol messages

Mailbox `text` is normally plain text, but the binary recognizes these structured JSON messages:

| Type | Shape / notes |
|---|---|
| `idle_notification` | `{type, from, timestamp, idleReason?, summary?, completedTaskId?, completedStatus?, failureReason?}` |
| `permission_request` | `{type, request_id, agent_id, tool_name, tool_use_id, description, input, permission_suggestions: []}` |
| `permission_response` | Error: `{type, request_id, subtype:"error", error}`; success: `{type, request_id, subtype:"success", response:{updated_input?, permission_updates?}}` |
| `sandbox_permission_request` | `{type, requestId, workerId, workerName, workerColor, hostPattern:{host}, createdAt}` |
| `sandbox_permission_response` | `{type, requestId, host, allow, timestamp}` |
| `shutdown_request` | `{type, requestId, from, reason?, timestamp}` |
| `shutdown_approved` | `{type, requestId, from, timestamp, paneId?, backendType?}` |
| `shutdown_rejected` | `{type, requestId, from, reason, timestamp}` |
| `team_permission_update` | Recognized by type; payload is permission-update specific. |
| `mode_set_request` | `{type, mode, from}` |
| `plan_approval_request` | `{type, from, timestamp, planFilePath, planContent, requestId}` |
| `plan_approval_response` | `{type, requestId, approved, feedback?, timestamp, permissionMode?}` |
| `task_assignment` | Emitted when `TaskUpdate` sets `owner`: `{type, taskId, subject, description, assignedBy, timestamp}`. |

`isStructuredProtocolMessage` recognizes most of the control messages but not all parsed helpers; for example `shutdown_rejected`, `idle_notification`, and `task_assignment` have dedicated recognizers/constructors but are not in the simple `$r$` allow-list in this snippet.

Evidence:

- `fragments/snippets/mailbox_write.txt:70-106` — constructors/recognizers and the structured-message allow-list.
- `fragments/snippets/mailbox_protocol_zod_schemas.txt:35-40` — Zod schemas for shutdown, mode, and plan approval messages.
- `fragments/snippets/taskupdate_schema.txt:162-164` — `task_assignment` emission.
- `fragments/snippets/appstate_tasks_selector.txt:91-102` — permission request/response mailbox emission.

## RPC/IPC and stream events

### MCP/JSON-RPC method surface

The bundle contains MCP/JSON-RPC methods listed in `analysis/rpc-methods-clean.txt`, including:

```text
initialize
ping
tools/list
tools/call
prompts/list
prompts/get
resources/list
resources/read
roots/list
sampling/createMessage
elicitation/create
tasks/list
tasks/get
tasks/cancel
tasks/result
notifications/tasks/status
```

This surface is not Agent Teams-specific, but it is the host RPC substrate around the CLI/SDK.

### SDK/direct-connect transport

The SDK direct-connect websocket transport frames JSON messages as UTF-8 JSON followed by a blank line (`\n\n`). The reader buffers partial chunks until the blank-line separator, parses each JSON frame, and drops malformed frames. SDK-to-CLI MCP bridge messages use a control envelope:

```jsonc
{
  "type": "control_request",
  "request_id": "<uuid>",
  "request": {
    "subtype": "mcp_message",
    "server_name": "<server>",
    "message": { "jsonrpc": "2.0" }
  }
}
```

Evidence:

- `fragments/snippets/directconnect.txt:61-80` — blank-line websocket framing and malformed JSON handling.
- `fragments/snippets/mcp_bridge.txt:39-47` — MCP message control envelope written to the transport.
- `analysis/control-subtypes-from-bundle.txt` — distinct `subtype:"..."` values extracted from the bundle.

### Task stream events

The task registry emits `system` stream events for task lifecycle and progress:

- `task_started`: `task_id`, optional `tool_use_id`, `description`, optional `task_type`, `workflow_name`, `prompt`, `skip_transcript`, plus stream metadata.
- `task_updated`: `task_id` and `patch` with `status`, `description`, `end_time`, `total_paused_ms`, `error`, and `is_backgrounded`.
- `task_progress`: `task_id`, optional `tool_use_id`, `description`, usage counters, `last_tool_name?`, `summary?`.
- `task_notification`: `task_id`, optional `tool_use_id`, `status:"completed|failed|stopped"`, `output_file`, `summary`, optional usage and `skip_transcript`.
- `session_state_changed`: `state:"idle|running|requires_action"`.

Related transcript/mirror events include `post_turn_summary`, `task_summary`, `transcript_mirror`, and `mirror_error`.

Evidence:

- `fragments/snippets/registerTask_emit.txt` — registry emission path.
- `fragments/snippets/rpc_task_started_schema.txt`
- `fragments/snippets/rpc_task_updated_schema.txt`
- `fragments/snippets/rpc_task_progress_schema.txt`
- `fragments/snippets/rpc_task_notification_schema.txt`
- `fragments/snippets/rpc_session_state_changed_schema.txt`
- `fragments/snippets/rpc_post_turn_summary_schema.txt`

## Hooks

The hook event enum extracted from the bundle includes standard tool/session hooks plus Agent Teams hooks:

```text
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

Base hook input contains `session_id`, `transcript_path`, `cwd`, optional `permission_mode`, and optional subagent fields. Agent Teams-specific hook inputs are:

```jsonc
{ "hook_event_name": "TeammateIdle", "teammate_name": "codex-extract", "team_name": "proto-rev" }
{ "hook_event_name": "TaskCreated", "task_id": "3", "task_subject": "...", "task_description": "...", "teammate_name": "...", "team_name": "..." }
{ "hook_event_name": "TaskCompleted", "task_id": "3", "task_subject": "...", "task_description": "...", "teammate_name": "...", "team_name": "..." }
```

Hook settings are keyed by hook event name and contain matcher entries with a list of hook definitions. Hook types include command, prompt, MCP tool, HTTP, and agent hooks. Hook output supports generic `continue`, `suppressOutput`, `stopReason`, `decision`, `systemMessage`, `reason`, and event-specific `hookSpecificOutput` payloads for tool permission decisions, prompt/session context, subagent start context, MCP tool output rewrites, permission request decisions, elicitation responses, and worktree paths.

Evidence:

- `fragments/snippets/hook_teammate_task_events_context.txt:6` — event enum.
- `fragments/snippets/hooks_input.txt:16-19` and `fragments/snippets/hooks_input.txt:48-51` — session/subagent and Agent Teams hook inputs.
- `fragments/snippets/hooks_settings_schema.txt` — settings shape for hook matchers and hook types.
- `fragments/snippets/hook_output_schema.txt` — hook output union.

## Schema versions

No explicit `schemaVersion` or `schema_version` field was found for team config, mailboxes, or task-list JSON in Claude Code 2.1.119. The extracted shape appears versionless and tolerant: readers parse JSON and then validate task files with a Zod shape, while team and mailbox files are read and mutated structurally.

The only stable version identifiers in this extraction are the host binary/package version (`2.1.119`), embedded build metadata, and the surrounding MCP/JSON-RPC method names. Compatibility for a clone/adapter should therefore key off host version and observed fields rather than an on-disk schema version.

Evidence:

- `fragments/snippets/version_markers.txt` — embedded package metadata.
- `fragments/snippets-index.tsv` — targeted `schema_version` search recorded as not found.
- `fragments/snippets/task_update_rH.txt:61-64` and `fragments/snippets/task_update_rH.txt:139-143` — task JSON validation but no version field.

## Compatibility implications for proto-rev

1. Persist team state exactly under `~/.claude/teams/<safe-team>/config.json` if compatibility with the host is required. Use the host sanitizers: team names replace non-alphanumerics with `-` and lower-case; task-list names allow `_`.
2. Treat mailbox rows as append-only JSON array records with a `read` bit and lock sidecars. Structured messages are ordinary JSON strings in `text`, not separate records.
3. For spawned teammates, reproduce the hidden CLI triplet `--agent-id`, `--agent-name`, and `--team-name` together; include `--parent-session-id`, `--agent-color`, optional `--plan-mode-required`, and optional `--agent-type`.
4. Mirror out-of-process teammates into `AppState.tasks` as `type:"in_process_teammate"` if building a compatible UI/control plane; do not assume that type means the model loop is in-process.
5. Use `TaskUpdate` assignment behavior as the source of truth for owner notifications: assigning `owner` should send a `task_assignment` mailbox protocol message.
6. Do not invent an on-disk schema version for interop; record the binary version and preserve unknown fields in team/member records and task `metadata`.
