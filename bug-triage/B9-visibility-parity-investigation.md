# B9 — Visibility-parity investigation: routed teammates vs native Claude

**Author:** opus-architect (anyteam-bug-triage)
**Date:** 2026-04-26
**Status:** _draft — section-by-section checkpointed_
**Type:** research, no `src/` edits
**Reframe:** B6 (300s non-progress watchdog) is conditional on this finding. Real question: what visibility do we actually have, and what's the gap to native-Claude parity?

---

## 0. Methodology

- Read the per-backend invocation modules in full (`codex.py`, `app_server.py`,
  `backends/gemini/*`, `backends/kimi/*`).
- Mine `bug-triage/incident-evidence/codex-runtime-pane-2026-04-26.log`
  (3 full Codex turns, observed event stream) as a ground-truth sample.
- Read vendored `src/claude_teams/` for what the host's standalone-MCP
  protocol exposes; consult `CLAUDE.md` and memory pointers for what
  native Claude Code surfaces in the lead's pane.
- Cite file:line for every claim about code behaviour.

## 1. Codex App Server event taxonomy

### 1.1 Methods (envelope) and items (payload)

Codex's App Server speaks JSON-RPC 2.0 with a small set of **methods** (the
notification envelope, mostly `item/started` + `item/completed`) and a much
larger set of **item types** (the payload that actually describes what
Codex is doing). Quoted strings extracted by `strings(1)` from
`/usr/local/lib/node_modules/@openai/codex/node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/codex/codex`
(codex-cli 0.124.0):

| Notification methods (envelope) | What we do |
|---|---|
| `item/started` | enqueued in `notifications.get` (`codex.py:778`); only the `item.type` decides if we count it. |
| `item/completed` | same. |
| `turn/started`, `turn/completed`, `TurnCompletedNotification` | only `turn/completed`/`TurnCompletedNotification` is checked (`codex.py:793`); the others fall through to event accumulation. |
| `thread/started` | unconsumed beyond accumulation. |
| `tokenUsage/updated` *(observed in binary strings)* | unconsumed. |

| Item types (payload `params.item.type`) | Source | Currently surfaced? |
|---|---|---|
| `agentMessage` | model output (final + delta variants) | ✅ captured into `last_message` (`codex.py:788-791`). |
| `mcpToolCall` | MCP tool calls (incl. our wrapper) | ✅ counted via `_TOOL_CALL_TYPE_SUBSTRINGS` containing `mcptoolcall` (`codex.py:80-85`). Logged as `codex.tool_call`. |
| `commandExecution` | **HOST shell tool** — `bash`/`pytest`/`git`/etc. | ❌ dropped. Doesn't match any substring in `_TOOL_CALL_TYPE_SUBSTRINGS = ("mcptoolcall", "toolcall", "tooluse", "functioncall")`. |
| `fileChange` | **HOST file write/edit/patch** | ❌ dropped, same reason. |
| `webSearch` | HOST web-search tool | ❌ dropped. |
| `image`, `imageGeneration`, `imageView`, `imagegen` | image tools | ❌ dropped. |
| `reasoning` | chain-of-thought summaries | ❌ dropped. |
| `plan` | TODO/plan updates | ❌ dropped. |
| `userMessage` | user-input echo | ❌ dropped (and irrelevant). |
| `systemMessage` | model-system messages | ❌ dropped. |
| `error` | error events | ❌ dropped — does NOT raise; just adds to `events` list. |

(Source: `strings(1)` grep for `^"(mcpToolCall|agentMessage|reasoning|...)…"`
on the codex-linux-x64 binary returned exactly the set above. No
`shellCall`, `localShellCall`, or `toolUse` types found in this build —
the host shell channel is **`commandExecution`** in 0.124.0.)

### 1.2 Smoking-gun evidence in the live log

`bug-triage/incident-evidence/codex-runtime-pane-2026-04-26.log` records
two complete turns:

| Turn | Wall-clock | `events` count | `tool_call_events` | `exit_code` | What actually happened |
|---|---|---|---|---|---|
| 1 (B6 prose, 17:23:33→17:38:08) | **14m36s** | **383** | **0** | 124 (timeout) | "no durable artifacts produced" — pure model hidden reasoning. |
| 2 (status check, 17:38:09→17:38:55) | 46s | 80 | 2 | 0 | The 2 counted events were **both** `mcpToolCall`s for `list_mcp_resources` (lines 82-101 of the log). All other 78 events were dropped. |

Plus the *unlogged* third turn referenced in B6 §"Productivity lens" —
~100 lines written to `B6-turn-timeout.md` while still reporting
`tool_call_events: 0`. That turn produced many `commandExecution` /
`fileChange` items that our classifier missed entirely.

### 1.3 What the wrapper *does* drain vs what reaches the lead

The polling loop in `codex.py:737-800` does drain the full notification
stream into a local `events: list[dict]`, but the **only** outputs that
escape `app_server_invoke` are:

| Output | Channel | Visible to lead? |
|---|---|---|
| `app_server.turn_started` log | adapter stderr | ❌ adapter stderr is captured to `/tmp/<name>.stderr` (when shim-launched) or to the spawn-shim pane (when host-spawned). The lead's TUI does not show it. |
| `app_server.done` log with `events`/`tool_call_events`/`structured` counts | adapter stderr | ❌ same — only at terminal. |
| `codex.tool_call` log per recognised tool call | adapter stderr | ❌ same — only for `mcpToolCall`. |
| `CodexResult.last_message` | returned to caller | ⚠️ becomes the prose reply via `_handle_prose` *if* the model produces a final assistant message, OR delivered through `send_message` MCP tool *if* the model used the tool path. |
| `task_update(active_form=...)` | shared task list | ✅ visible — but only set once when claiming (`loop.py:501`), then only on completion or block. No per-step updates. |
| `pio.send_idle_notification` | mailbox → lead | ✅ visible — but only when the loop is idle. |

There is **no** event-forwarding from `app_server_invoke`'s notification
loop to the team protocol. The 383 events from the runaway turn never
reached the lead's mailbox in any form. The lead saw nothing for 14m36s
beyond the initial `task_update(active_form="Running codex on task #...")`
written at claim time — which is invariant for the entire turn, no
matter what Codex does internally.

### 1.4 Architectural summary

- The protocol surface is **rich** — Codex emits item-typed events for
  every shell command, file change, reasoning step, plan update, token
  usage tick, and MCP call.
- The wrapper consumes **two** event categories: `agentMessage`
  (terminal text) and a substring-matched subset of "tool call" types
  that in practice covers only `mcpToolCall`.
- The wrapper forwards **zero** of those events outward as team-protocol
  signals. Even within the locally-consumed subset, only counters reach
  the terminal log line.
- The 0.124.0 host-shell channel is named `commandExecution`, not
  `shellCall` or `localShellCall` as the wrapper's substring list might
  suggest in the future. Hard-coding that name will be more robust than
  generic substrings (we currently miss it for both reasons: substring
  doesn't match, and we don't try the literal name).

## 2. Gemini headless and Kimi headless event taxonomies

### 2.1 Shared shape: one-shot headless turns, not live turns

Both routed adapters run a whole teammate turn inside a single CLI subprocess and
then collapse the observed stream into the shared `CodexResult` shape
(`exit_code`, `structured`, `last_message`, `events`, `error`,
`tool_call_events`, `session_id`; see `src/claude_anyteam/codex.py:52-65`).
Gemini headless launches `gemini --prompt <prompt> --output-format
stream-json --approval-mode yolo ...` (`src/claude_anyteam/backends/gemini/invoke.py:405-436`).
Kimi launches `kimi --print --output-format=stream-json --work-dir <cwd>
--mcp-config-file <path> ... -p <prompt>` (`src/claude_anyteam/backends/kimi/invoke.py:422-470`).
The control loops only see the collapsed result after the subprocess exits: task
turns retry/block/complete in `_execute_task()` and prose turns reply/fallback in
`_handle_prose()` (`src/claude_anyteam/backends/gemini/loop.py:254-270`,
`src/claude_anyteam/backends/gemini/loop.py:441-491`,
`src/claude_anyteam/backends/kimi/loop.py:228-253`,
`src/claude_anyteam/backends/kimi/loop.py:413-461`). That means neither
headless backend currently gives the lead native-style live visibility while the
model is thinking or using tools.

### 2.2 Gemini headless parser

Gemini's parser treats stdout as newline-delimited JSON event objects
(`src/claude_anyteam/backends/gemini/invoke.py:440-448`). It **retains** every
valid JSON object in `result.events`, but only acts on a small subset:

| Stream item | Parsed into adapter state | What is dropped / only retained raw |
|---|---|---|
| Empty stdout lines | skipped | no event retained (`invoke.py:440-442`). |
| Non-JSON stdout lines | ignored after a debug log | line text is not stored in `result.events` (`invoke.py:443-447`). |
| `type: "init"` | captures `session_id` only when it arrives before any non-init event; duplicate/late init is warned | late/duplicate session ids are not used for resume, though their raw events remain retained (`invoke.py:449-458`). |
| `type: "message"`, `role: "assistant"`, string `content` | appended to `last_message_parts`; final `last_message` is the concatenation | user echoes, non-assistant messages, and assistant content that is not a string are retained raw but not used for final text (`invoke.py:459-465`). |
| `type: "tool_use"` | increments `tool_call_events` and logs `tool_name` plus the raw event | no separate tool-start summary is surfaced to the lead while the subprocess is running (`invoke.py:461-463`). |
| `type: "tool_result"` | retained in `events` | `status` / `output` are not summarized, counted, or promoted into task/prose messages; a failed tool result has no special classifier here (`invoke.py:448-463`). |
| `type: "result"` | last such event is the required terminal event; non-success `status` sets `error`; missing `result` now turns an otherwise-zero exit into adapter failure | `stats`, including `stats.tool_calls`, is not used to cross-check `tool_call_events` (`invoke.py:472-482`). |
| Other valid JSON event types, including any future `error` shape | retained raw only | no specialized parsing; unless the terminal `result` or process exit reports failure, the event has no lead-visible effect (`invoke.py:448-482`). |

So Gemini's headless visibility is better than a final blob — it can count
explicit `tool_use` events and preserve raw `tool_result` events — but it drops
non-JSON diagnostics and does not normalize tool results, errors, or terminal
stats into stable lead-facing telemetry.

### 2.3 Kimi headless parser

Kimi's `stream-json` is not Gemini-style `init/message/tool_use/tool_result/result`;
it is per-message NDJSON. The module comment calls this out explicitly, and
session ids come from stderr resume hints rather than stdout events
(`src/claude_anyteam/backends/kimi/invoke.py:1-7`,
`src/claude_anyteam/backends/kimi/invoke.py:273-275`). `_parse_stdout()` retains
every JSON object plus a synthetic event for non-JSON stdout, but only derives
assistant final text and a tool-call count (`src/claude_anyteam/backends/kimi/invoke.py:327-352`).

| Stream item | Parsed into adapter state | What is dropped / only retained raw |
|---|---|---|
| Empty stdout lines | skipped | no event retained (`invoke.py:331-333`). |
| Malformed JSON or non-dict JSON stdout | appended as `{"type": "non_json_stdout", "line": ...}` and debug-logged | not interpreted, but unlike Gemini it is at least preserved in `result.events` (`invoke.py:334-338`). |
| `role: "assistant"` with `tool_calls[]` | counts each dict call in `tool_call_events`; logs the tool name from `function.name` or top-level `name` | non-dict calls are ignored; arguments are only JSON-validated for a warning, not normalized or surfaced (`invoke.py:340-348`, `invoke.py:292-324`). |
| `role: "assistant"` with string `content` | becomes `last_message` when non-empty | earlier assistant text is overwritten by later assistant text, which is correct for final-message capture but loses intermediate narration as a derived field (`invoke.py:349-352`). |
| `role: "assistant"` with list `content` parts | concatenates only `{type: "text", text: ...}` parts | `think`, `encrypted`, non-dict, and non-text parts are retained raw only and omitted from `last_message` (`invoke.py:278-289`). |
| `role: "tool"` | retained raw in `events` | tool result text/status is not parsed into a result taxonomy or error class; tests inspect it via helper only, not the loop (`invoke.py:327-352`; examples in `tests/test_kimi_invoke.py:148-168`). |
| stderr resume hint | extracts `session_id` with `To resume this session: kimi -r <id>` | other stderr on successful runs is discarded; stderr/stdout is only folded into `error` on non-zero exit (`invoke.py:273-275`, `invoke.py:474-490`). |
| Durable Kimi `wire.jsonl` events | not read by the adapter | richer wire-only data such as step begin/end, streamed tool argument fragments, status/context usage, plan mode, and structured tool-result display is unavailable to the lead from this path (by omission from `invoke.py:327-500`; see empirical map in `docs/internal/kimi-integration/kimi-runtime.md:128-143`). |

Kimi therefore preserves non-JSON stdout better than Gemini and can count both
built-in and wrapper tool calls because they all appear in `assistant.tool_calls[]`.
But it has no terminal stdout `result` event, no stdout session event, and no
normalized `tool_result` status. Process exit plus schema validation is the
only completion gate.

### 2.4 Loop-level visibility consequences and B4 cross-reference

The most important B4 connection is that both headless loops already have just
enough parser signal to detect no-action turns, but neither loop uses it. A task
is marked complete whenever the backend returned exit `0` plus schema-valid
`{files_changed, summary}` JSON; neither Gemini nor Kimi checks whether
`tool_call_events == 0`, whether `files_changed` is empty, or whether the raw
event tail shows any action (`src/claude_anyteam/backends/gemini/loop.py:453-488`,
`src/claude_anyteam/backends/kimi/loop.py:426-458`). That is exactly the B4
productivity failure mode: a model can plan/acknowledge and still end the
one-shot turn with valid JSON, leaving the lead with a `task_complete` or idle
state rather than a visible "responded but did not act" diagnosis
(`bug-triage/B4-gemini-productivity.md:170-201`).

Two related routing gaps also mirror B4. First, both backend loops parse only
shutdown, plan-approval, and steer protocol messages; every other protocol
payload is a debug-only no-op (`src/claude_anyteam/backends/gemini/loop.py:177-188`,
`src/claude_anyteam/backends/kimi/loop.py:151-162`). Second, plain-text DMs go
through prose prompts that explicitly ask for a brief `send_message` reply, not
execution (`src/claude_anyteam/backends/gemini/prompts.py:37-43`,
`src/claude_anyteam/backends/kimi/prompts.py:30-36`). Kimi has the PR #11 guard
that suppresses the canned fallback when a prose reply was delivered via an MCP
tool (`src/claude_anyteam/backends/kimi/loop.py:241-247`); Gemini still lacks
that guard (`src/claude_anyteam/backends/gemini/loop.py:254-270`).

Recommendation for the B9 visibility workstream: implement B4's
`task_idle_no_tool_calls` / `task_complete_unverified_tool_count` diagnostics
for **both** Gemini and Kimi headless. The raw fields are already present on
`CodexResult`; the missing piece is a loop policy that emits a lead-visible
structured message when a task turn produces schema-valid JSON with zero tool
calls and zero files, plus a raw-event-tail/last-message preview for debugging.
Also align Gemini with Kimi by retaining non-JSON stdout as synthetic events,
and consider normalizing retained tool results into a small backend-neutral
`tool_use` / `tool_result` / `non_json_stdout` taxonomy before later watchdog
logic depends on it.

## 3. Native-Claude baseline (what the host surfaces for native teammates)

### 3.1 Working baseline

For a **native Claude teammate spawned by the Claude Code host**, the target
baseline is stronger than "it appears as `@name`": the lead should see native
teammate tool calls, prose deltas, idle reasons, and DMs as live operational
signals, not by reading the child pane/stderr after the fact. That is the
project north star in `CLAUDE.md:3-16`, especially the explicit requirements
that host-tool activity (`Read` / `Edit` / `Write` / `Bash`) surface to the
lead and that the lead should not need tmux stderr to understand what a
teammate is doing (`CLAUDE.md:5-12`).

The important constraint is that this native surfacing is **host-owned**. The
repo-visible protocol can model team membership, mailboxes, tasks, and tmux
targets, but it does not expose a structured native-Claude event stream for
assistant prose deltas or host tool calls.

### 3.2 What `src/claude_teams` proves

`src/claude_teams` gives us the file-protocol floor, not the full native UI
ceiling:

- **Team membership / spawn identity.** A teammate member stores identity,
  model, prompt, color, plan-mode flag, tmux target, cwd, backend type, and
  `isActive` (`src/claude_teams/models.py:30-45`). The native tmux spawn path
  then runs the real `claude` binary with `--agent-id`, `--agent-name`,
  `--team-name`, `--agent-color`, `--parent-session-id`, `--agent-type`, and
  `--model` (`src/claude_teams/spawner.py:51-73`). This is enough to launch a
  native teammate and associate it with the team; it is **not** a stream of the
  teammate's tool calls or assistant text.
- **Durable prose / DM shape.** The durable mailbox primitive is
  `InboxMessage(from, text, timestamp, read, summary?, color?)`
  (`src/claude_teams/models.py:90-99`). `send_plain_message()` writes that
  object to the recipient inbox (`src/claude_teams/messaging.py:161-178`), and
  `send_message(type="message")` validates sender/recipient, appends a reply
  reminder, and returns routing metadata (`src/claude_teams/server.py:350-421`).
  This proves how explicit messages/DMs persist; it does not prove how native
  free-form assistant prose deltas are rendered in the lead UI.
- **Task/status floor.** Tasks carry `subject`, `description`, `activeForm`,
  `status`, `blocks`, `blockedBy`, `owner`, and optional `metadata`
  (`src/claude_teams/models.py:76-87`). `task_update()` mutates these fields and
  assignment changes notify an owner via inbox (`src/claude_teams/server.py:556-599`).
  So a teammate can expose coarse progress through task state, but there is no
  task field for per-tool-call telemetry.
- **Idle reason schema exists.** `IdleNotification` has
  `type="idle_notification"` plus `from`, `timestamp`, and `idleReason`
  (`src/claude_teams/models.py:101-108`). That gives the adapter a compatible
  shape to emit; it is still only a message-schema clue, not a native host
  rendering contract.
- **Manual pane/status diagnostic.** `check_teammate()` can read unread messages
  from a teammate, count that teammate's unread inbox, and optionally capture
  tmux output (`src/claude_teams/server.py:692-780`). The capture helper uses
  `tmux capture-pane` (`src/claude_teams/tmux_introspection.py:51-90`). This is
  useful for debugging but should not be confused with native real-time
  surfacing; it is a pull-based pane scrape.

### 3.3 What the host-side memory/research adds

The memory pointer says TUI presence is not driven by writing
`~/.claude/teams/*/config.json`; it is driven by the lead process's in-memory
`AppState.tasks` (`~/.claude/projects/-home-rosado-Projects-codex-teammate/memory/MEMORY.md:4-5`,
`~/.claude/projects/-home-rosado-Projects-codex-teammate/memory/project_tui_presence_mechanism.md:7-17`).
The archived reverse-engineering note is more specific: the presence renderer
reads `tasks[taskId].type`, `status`, `identity.agentName`, plus
`progress`, `isIdle`, and `messages` for status/previews
(`docs/internal/2026-prototype/research.md:25-34`). Both in-process teammates
and out-of-process pane teammates become visible only after a leader-side
`registerTask(...)`/mirror-task path populates that state
(`docs/internal/2026-prototype/research.md:36-49`); standalone file injection
and inbox writes do not create a row (`docs/internal/2026-prototype/research.md:80-93`,
`docs/internal/2026-prototype/research.md:121-130`).

That is the practical native-Claude baseline for this investigation:

| Surface | Native Claude baseline | Repo-visible floor | Opaque part |
|---|---|---|---|
| Presence / activity | Host row is backed by live `AppState.tasks`, including `progress`, `isIdle`, and `messages` previews. | `src/claude_teams` can create config/inbox/task files and remember a tmux target. | Exact upstream task-state schema and renderer behavior beyond the RE notes. |
| Prose / DMs | `CLAUDE.md` asserts native teammates surface prose deltas and DMs to the lead in real time. | Explicit DMs are inbox `InboxMessage.text` plus optional `summary`/`color`. | Whether every native assistant token/delta is represented as `tasks[*].messages`, transcript JSONL, another private channel, or a filtered combination. |
| Host tool calls | `CLAUDE.md` treats native `Read` / `Edit` / `Write` / `Bash` surfacing as the parity target. | No `src/claude_teams` model has a structured tool-call/progress event; only tmux scrape can reveal whatever the child pane rendered. | Native tool-call event taxonomy, throttling, error detail, and whether the lead sees full args/results or summaries. |
| Idle reasons | Host row has an `isIdle` input per RE; repo schema has `idleReason`. | `IdleNotification` gives a compatible file-message shape. | Native idle-state derivation and exact display text. |

### 3.4 Bounds / what not to infer

- Do **not** infer native visibility from `config.json` membership alone. The
  memory explicitly warns that the adapter's registration metadata is
  aspirational, not the TUI mechanism
  (`~/.claude/projects/-home-rosado-Projects-codex-teammate/memory/project_tui_presence_mechanism.md:11-17`).
- Do **not** infer native tool-call surfacing from `check_teammate(include_output=True)`.
  That tool is an on-demand tmux pane scrape, while the parity target is
  host-owned live surfacing in the lead pane.
- Do **not** claim an exact native event taxonomy from this repo. `src/claude_teams`
  has typed schemas for membership, tasks, inbox messages, shutdown, and idle,
  but no typed native-Claude `tool_call`, `tool_result`, or assistant-delta
  event model.
- The RE/memory evidence is enough to set the baseline direction
  (leader-owned `AppState.tasks` + live previews), but not enough to specify
  byte-for-byte native rendering behavior across Claude Code versions.
- Do **not** assume one global TaskList. Observed during B9 itself: the
  team-shared task list the lead queries is *not* the same as the per-teammate
  TaskList scope individual teammates see — the architect's local
  `TaskList` returned `No tasks found` while the lead simultaneously saw
  task-completion notifications for the same nominal IDs. A future implementer
  reasoning about "task list as coordination substrate" should treat
  per-teammate vs team-shared as distinct surfaces with no automatic merge,
  and should not assume task-ID stability across the two.

## 4. Visibility-gap matrix

Legend: ✅ = lead-visible at native-like fidelity/timing; ⚠️ = visible only
indirectly, after the turn, via side effect, or in adapter stderr; ❌ = no
lead-visible surface today. The grading is deliberately lead-centric: adapter
logs are not parity by themselves, because the repo north star says the lead
should not have to read tmux/stderr for routed-teammate state (`CLAUDE.md:5-12`),
and the shared logger writes diagnostics to stderr (`src/claude_anyteam/logger.py:1-3`,
`src/claude_anyteam/logger.py:26-37`).

| Backend / event | Roster / TUI presence | Task + idle state | Peer DMs / prose | Assistant deltas | Host tools (Read/Edit/Bash equivalents) | Team-protocol / MCP tools | Mid-turn steer / interruption | Permission prompts | Terminal completion / failure |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Native Claude teammate | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Codex `exec --json` | ✅ | ⚠️ | ⚠️ | ❌ | ❌ | ⚠️ | ❌ | ❌ | ⚠️ |
| Codex App Server | ✅ | ⚠️ | ⚠️ | ⚠️ | ❌ | ⚠️ | ⚠️ | ❌ | ⚠️ |
| Gemini headless | ✅ | ⚠️ | ⚠️ | ❌ | ⚠️ | ⚠️ | ⚠️ | ❌ | ⚠️ |
| Gemini ACP | ✅ | ⚠️ | ⚠️ | ⚠️ | ⚠️ | ⚠️ | ⚠️ | ⚠️ | ⚠️ |
| Kimi headless | ✅ | ⚠️ | ⚠️ | ❌ | ⚠️ | ⚠️ | ⚠️ | ❌ | ⚠️ |
| Kimi ACP | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |

Notes/evidence for the cells above:

- **Roster / TUI presence is the strongest routed surface.** The spawn shim
  routes `codex-*`, `gemini-*`, and `kimi-*` names to backend adapters
  (`src/claude_anyteam/spawn_shim.py:281-302`), and self-registration writes
  a native-shaped teammate entry (`tmuxPaneId`, `agentType`, `model`,
  `backendType`) into the team config (`src/claude_anyteam/registration.py:126-143`).
  Kimi ACP is red because the Kimi loop raises `NotImplementedError` for
  `backend == "acp"` (`src/claude_anyteam/backends/kimi/loop.py:83-100`).
- **Task/idle state is visible but coarse.** All routed loops claim a task with
  a backend-specific `active_form`, send 60-second idle notifications, and
  send terminal `task_complete` / `task_blocked` messages; examples are Codex
  idle + claim + completion (`src/claude_anyteam/loop.py:137-146`, `src/claude_anyteam/loop.py:542-616`,
  `src/claude_anyteam/loop.py:819-861`), Gemini claim/completion/block (`src/claude_anyteam/backends/gemini/loop.py:388-400`,
  `src/claude_anyteam/backends/gemini/loop.py:441-500`), Kimi claim/completion/block (`src/claude_anyteam/backends/kimi/loop.py:128-141`,
  `src/claude_anyteam/backends/kimi/loop.py:413-493`), and the protocol helpers that serialize idle/complete/block
  payloads (`src/claude_anyteam/protocol_io.py:180-233`). This is ⚠️ rather
  than ✅ because there is no native-like live progress stream between those
  coarse updates.
- **Peer DMs/prose are delayed or summarized.** Codex handles idle prose by
  starting a whole backend turn and then sending one reply or a canned fallback
  (`src/claude_anyteam/loop.py:179-260`). Gemini does the same without the
  Codex/Kimi "already delivered via tool" guard (`src/claude_anyteam/backends/gemini/loop.py:254-270`),
  while Kimi has that guard (`src/claude_anyteam/backends/kimi/loop.py:228-253`).
  Codex App Server can inject mid-task prose as `turn/steer`, but the receipt
  is only logged and a race-lost steer is dropped (`src/claude_anyteam/loop.py:774-815`;
  `src/claude_anyteam/codex.py:740-765`). Gemini/Kimi only queue structured
  steers for the next turn boundary (`src/claude_anyteam/backends/gemini/loop.py:194-250`;
  `src/claude_anyteam/backends/kimi/loop.py:168-224`).
- **Assistant deltas are not lead-visible.** Codex `exec` parses JSONL only
  after `subprocess.run` completes and keeps the final last-message file
  (`src/claude_anyteam/codex.py:430-539`). Codex App Server and Gemini ACP
  have event streams/chunks internally, but only retain the latest/assembled
  message for result parsing (`src/claude_anyteam/codex.py:776-791`;
  `src/claude_anyteam/backends/gemini/acp.py:444-480`). Gemini/Kimi headless
  use captured stdout and final message assembly after process return
  (`src/claude_anyteam/backends/gemini/invoke.py:436-465`;
  `src/claude_anyteam/backends/kimi/invoke.py:327-352`, `src/claude_anyteam/backends/kimi/invoke.py:461-475`).
- **Host-tool visibility is the largest gap.** The baseline explicitly requires
  Read/Edit/Write/Bash activity inside routed CLIs to surface to the lead
  (`CLAUDE.md:9-12`). Codex only classifies/logs events that look like
  tool/MCP calls (`src/claude_anyteam/codex.py:68-132`, `src/claude_anyteam/codex.py:457-502`, `src/claude_anyteam/codex.py:654-673`),
  and the live Codex App Server incident shows a 900s turn with 383 internal
  events and `tool_call_events: 0`, followed by a separate turn where only an
  MCP resource-list call surfaced (`bug-triage/incident-evidence/codex-runtime-pane-2026-04-26.log:54-64`,
  `bug-triage/incident-evidence/codex-runtime-pane-2026-04-26.log:81-107`). Gemini improves capture by disabling built-ins and prompting use
  of `mcp_anyteam_*` shadow tools (`src/claude_anyteam/backends/gemini/prompts.py:15-35`;
  wrapper shadow tools are defined at `src/claude_anyteam/wrapper_server.py:54-68`,
  `src/claude_anyteam/wrapper_server.py:346-496`), and Gemini ACP normalizes `tool_call` / `tool_call_update`
  into events (`src/claude_anyteam/backends/gemini/acp.py:169-214`). Kimi
  headless can count `tool_calls` from stream-json (`src/claude_anyteam/backends/kimi/invoke.py:327-352`),
  but its prompt tells the model to use Kimi built-ins for local work
  (`src/claude_anyteam/backends/kimi/prompts.py:20-31`), so the lead still
  does not get native-like Read/Edit/Bash cards.
- **Team-protocol/MCP calls are side-effect-visible, not trace-visible.** The
  wrapper exposes coordination tools (`send_message`, `task_update`,
  `task_create`, `read_inbox`, `task_list`, `read_config`) plus shadow tools
  (`src/claude_anyteam/wrapper_server.py:54-68`). Side effects such as messages
  and task updates are visible because the wrapper writes them (`src/claude_anyteam/wrapper_server.py:178-240`,
  `src/claude_anyteam/wrapper_server.py:280-334`), but the call/result trace itself is logged to stderr in the
  adapters rather than streamed to the lead (`src/claude_anyteam/codex.py:495-502`;
  `src/claude_anyteam/backends/gemini/invoke.py:461-463`;
  `src/claude_anyteam/backends/kimi/invoke.py:340-348`).
- **Permission prompts exist only for Gemini ACP, and even there are
  config-dependent.** ACP `trusted` mode auto-allows (`src/claude_anyteam/backends/gemini/acp_client.py:254-257`);
  `default`/`plan` modes send `permission_request` to `team-lead` and wait for
  a `permission_response` (`src/claude_anyteam/backends/gemini/acp_client.py:268-321`;
  `src/claude_anyteam/protocol_io.py:246-268`). Codex uses `approval_policy=never`
  / full-access execution in both exec and App Server paths (`src/claude_anyteam/codex.py:430-443`,
  `src/claude_anyteam/codex.py:694-709`), Gemini headless runs `--approval-mode yolo`
  (`src/claude_anyteam/backends/gemini/invoke.py:405`), and Kimi headless has
  no permission bridge.

**Bottom line for section 4:** routed teammates have acceptable roster/task
presence, but they are not visibility-parity peers. The hard gaps are live
assistant deltas, host-tool call/result cards, and pre-terminal diagnostics.
Codex App Server is the only shipped path with an actual mid-turn injection
primitive, and Gemini ACP is the only shipped path with a lead-facing
permission bridge; neither currently turns backend event streams into the
lead-visible operational feed that native Claude teammates get.

## 5. Watchdog recommendation

### 5.1 Verdict

**Ship a default-on _soft_ non-progress watchdog for Codex App Server; do not
ship a default-on early-kill watchdog.** Keep the existing wall-clock hard cap
as the only default interrupt. Make early non-progress interrupt an opt-in
operator/debug setting. Drop any watchdog whose trip condition is only
`events == 0`, `tool_call_events == 0`, or "no MCP calls for N seconds."

This is a change from the first B6 instinct ("interrupt 60s after a
non-progress steer") because the actual signal boundary is weaker than native
Claude's. The adapter can observe App Server notifications, final/assistant
messages, wrapper/MCP tool events, and local filesystem deltas it chooses to
sample. It **cannot** observe Codex's internal host `Read` / `Edit` / `Write` /
`Bash` calls the way the native Claude host UI can (see the Section 3 baseline).
Killing on an absence of visible tool calls would therefore turn a visibility
gap into data loss.

### 5.2 Why default hard interrupt is unsafe

Current Codex App Server control flow has the primitives we need but not enough
truth for destructive policy:

- The adapter starts a turn, drains notifications, can inject `turn/steer`, and
  can interrupt (`src/claude_anyteam/codex.py:725-731`,
  `src/claude_anyteam/codex.py:734-809`).
- It records App Server notifications and counts only events that look like
  tool calls in `params.item` (`src/claude_anyteam/codex.py:654-673`).
- The only assistant text currently promoted to `last_message` is
  `params.item.type == "agentMessage"` (`src/claude_anyteam/codex.py:786-792`).
- The existing hard cap is purely wall-clock: if the turn has not completed by
  `overall_timeout_s`, it returns exit code 124 and attempts `turn_interrupt`
  (`src/claude_anyteam/codex.py:737-809`).

Field evidence shows why notification count and MCP tool count are not a safe
hard-progress oracle. The live incident started a turn at 17:23:34 and returned
only at 17:38:08 with exit code 124, 383 App Server events, and
`tool_call_events: 0` (`bug-triage/incident-evidence/codex-runtime-pane-2026-04-26.log:50-64`).
B6's follow-up notes add the missing negative control: a later successful turn
wrote real content while still logging `tool_call_events: 0`, because Codex
host tools are not surfaced as App Server MCP events
(`bug-triage/B6-turn-timeout.md:68-77`,
`bug-triage/B6-turn-timeout.md:145-148`).

So:

- **Event churn** means "the App Server is alive," not "useful work is
  visible."
- **MCP tool calls** mean "Codex used an MCP/server tool," not "Codex used or
  did not use host file tools."
- **No artifact delta yet** may mean "stuck," but it may also mean "reading,
  planning, or composing a large final edit."

That signal quality is good enough to warn/steer by default and not good
enough to early-kill by default.

### 5.3 Concrete default-on trip condition (soft)

Implement this only in the Codex App Server path at first. A turn enters
`soft_non_progress` when all of the following are true:

1. `elapsed_s >= non_progress_soft_s` (default **300s**, configurable
   **60-900s**).
2. No previous soft watchdog fired for this turn.
3. Since `turn/start`, there has been no **host-visible checkpoint**, defined
   as any of:
   - non-empty `agentMessage` text or an increase in `agentMessage` byte count;
   - a wrapper/MCP tool event (diagnostic only, not required for success);
   - a durable artifact delta sampled by the adapter (for git repos: changed
     `git status --porcelain=v1` hash or tracked/untracked file size/mtime
     change under `cwd`; for non-git repos: a bounded mtime/size sample, or
     "artifact signal unavailable");
   - a protocol side effect the adapter can see directly, e.g. wrapper
     `send_message`, `task_update`, or task-file mutation.

When the soft condition trips:

- log `app_server.non_progress` with elapsed time, total notification count,
  `agentMessage` bytes, MCP tool-call count, and artifact-delta status;
- surface the same data to the lead:
  - for a task turn, `task_update(active_form="running codex: no visible checkpoint for 300s; asked to checkpoint")`;
  - for a prose/DM turn, send a short diagnostic interim reply rather than
    waiting silently for the final fallback;
- send one `turn/steer`:

```text
You have produced no externally visible checkpoint for 300 seconds. If you have useful findings or partial work, summarize them now and either write a small durable artifact or finish in the requested output format. Do not continue hidden reasoning without a visible checkpoint.
```

After this steer, **continue the turn**. If any host-visible checkpoint appears,
clear the risk marker and let the normal wall-clock cap govern. If none appears,
continue to the existing hard cap unless opt-in early interrupt is enabled.

### 5.4 Opt-in hard trip condition

Expose a separate opt-in setting such as
`CLAUDE_ANYTEAM_NON_PROGRESS_INTERRUPT=true` / `--non-progress-interrupt`.
Only when it is enabled, interrupt early if:

1. the soft watchdog already fired;
2. `elapsed_s >= non_progress_soft_s + non_progress_grace_s`
   (default grace **120s**);
3. there is still no host-visible checkpoint;
4. the adapter's artifact signal is available and unchanged, or the operator
   explicitly chose to allow hard interrupts without artifact sampling.

Return a distinct error, e.g.
`app_server turn made no host-visible progress for 420s after checkpoint steer`,
so this is distinguishable from the absolute 900s timeout. This makes the
destructive behavior available for CI/smoke/test roles while avoiding false
interrupts for long-form executor/research roles.

### 5.5 Backend scope

- **Codex App Server:** default-on soft watchdog is shippable now. This backend
  already has a polling loop that can interleave notifications, `mid_turn_hook`,
  `turn/steer`, and `turn_interrupt` (`src/claude_anyteam/codex.py:734-809`).
- **Codex `exec` resume/fresh path:** do **not** add a non-progress watchdog.
  It uses `subprocess.run(..., capture_output=True, timeout=timeout_s)` and only
  parses stdout after process exit (`src/claude_anyteam/codex.py:429-539`).
  Keep a wall-clock timeout only.
- **Gemini headless / Kimi headless:** do **not** add a non-progress watchdog.
  Both use blocking `subprocess.run(..., capture_output=True, timeout=...)` and
  parse stream output after exit (`src/claude_anyteam/backends/gemini/invoke.py:435-463`,
  `src/claude_anyteam/backends/kimi/invoke.py:460-474`).
- **Gemini ACP:** possible later, but not in this checkpoint. The transport
  stores notifications in a queue (`src/claude_anyteam/jsonrpc_stdio.py:316-320`),
  yet the current Gemini ACP run blocks in `session_prompt(...)` and drains
  notifications only after the response (`src/claude_anyteam/backends/gemini/acp.py:420-421`).
  It needs an App-Server-like polling loop before a soft watchdog can be made
  truthful.

### 5.6 Ship/drop summary

| Policy | Verdict | Reason |
|---|---|---|
| Keep 900s wall-clock timeout | **Default-on** | Existing containment; does not infer progress. |
| Soft non-progress checkpoint/lead diagnostic | **Default-on for Codex App Server** | Low-risk visibility improvement; uses `turn/steer` instead of killing. |
| Early non-progress interrupt | **Opt-in** | Signal is incomplete; host tool activity is opaque. |
| `tool_call_events == 0` watchdog | **Drop** | False negatives for real host-tool work. |
| Event-count/no-event watchdog | **Drop** | 383-event timeout proves event churn is not progress. |

## 6. Visibility-parity workstream — concrete proposals

### 6.1 Design principle: four channels, not one

Do **not** try to make stderr, the lead inbox, task `activeForm`, or a future
event log carry the whole routed-teammate event stream alone. They have
different jobs:

| Channel | Purpose | Why / current code |
|---|---|---|
| **stderr JSON logs** | Full forensic/debug stream; can be noisy. | Existing logger emits JSON lines to stderr (`src/claude_anyteam/logger.py:1-3`, `:26-37`). Good for replay, bad as the lead-facing product because the north star says the lead should not read stderr. |
| **Lead mailbox** | Low-frequency, lead-visible summaries/warnings/errors. | `protocol_io.send_json_to_lead()` already serializes JSON payloads into `InboxMessage.text` with a `summary` header (`src/claude_anyteam/protocol_io.py:164-178`); idle/task/permission messages already use it (`src/claude_anyteam/protocol_io.py:180-233`, `:246-268`). |
| **Task state (`activeForm` + metadata)** | Durable "what is this teammate doing right now?" status. | Claim/update paths already mutate `active_form` through `protocol_io.update_task()` (`src/claude_anyteam/protocol_io.py:133-147`), and wrapper `task_update` exposes the same concept to routed models (`src/claude_anyteam/wrapper_server.py:245-294`). |
| **New append-only event log** | Full-fidelity, machine-readable event stream for future UI/tools without mailbox spam. | New file-protocol extension; proposed path below. Needed because mailbox is a human notification queue, not a 100s-of-events transport. |

So the workstream should land as **event normalization + fan-out policy**:
normalize backend events once, then decide whether each event goes to stderr
only, task state, lead mailbox, and/or the new append-only event log.

### 6.2 Event envelope (backend-neutral)

Add a versioned envelope and a small number of payload kinds. Keep it permissive:
we need stable fields for UI/filtering, but backend raw payloads must remain
available for forensics.

```json
{
  "kind": "turn_progress",
  "schema_version": 1,
  "event_id": "codex-runtime:task-13:000042",
  "timestamp": "2026-04-26T17:29:01.123Z",
  "team": "anyteam-bug-triage",
  "agent": "codex-runtime",
  "backend": "codex_app_server",
  "task_id": "13",
  "turn_id": "019dcad1-6729-7441-b869-31ca9c629886",
  "seq": 42,
  "severity": "info",
  "visibility": {
    "mailbox": true,
    "task_state": true,
    "event_log": true,
    "stderr": true
  },
  "summary": "no visible checkpoint for 300s; checkpoint steer sent",
  "payload": {}
}
```

Base fields:

- `kind`: one of `turn_started`, `turn_progress`, `tool_event`,
  `artifact_event`, `turn_warning`, `turn_completed`, `turn_failed`,
  `visibility_degraded`.
- `schema_version`: start at `1`.
- `event_id`: deterministic enough for de-duping (`{agent}:{turn_or_task}:{seq}`).
- `team`, `agent`, `backend`, `task_id`, `turn_id`, `seq`, `timestamp`.
- `severity`: `debug|info|warn|error`.
- `summary`: short human-facing sentence for mailbox/task status.
- `payload`: typed per kind; may include `raw_event_ref` or a redacted
  `raw_event_preview`, but not unbounded stdout/stderr.

### 6.3 Payload shapes

#### `turn_started`

Use for the start of any routed turn (task or prose). Mailbox only for tasks if
the lead needs high-verbosity mode; otherwise event-log + stderr.

```json
{
  "kind": "turn_started",
  "payload": {
    "mode": "task",
    "prompt_kind": "task_complete",
    "timeout_s": 900,
    "non_progress_soft_s": 300,
    "cwd": "/home/rosado/Projects/codex-teammate",
    "model": "gpt-5.5",
    "effort": "xhigh"
  }
}
```

Where to land:

- Codex App Server: immediately after `turn_start()` logs
  `app_server.turn_started` (`src/claude_anyteam/codex.py:725-732`).
- Codex exec / Gemini / Kimi headless: emit before `subprocess.run(...)`
  (`src/claude_anyteam/codex.py:429-444`,
  `src/claude_anyteam/backends/gemini/invoke.py:435-438`,
  `src/claude_anyteam/backends/kimi/invoke.py:460-472`).

#### `tool_event`

Use one normalized shape for host, MCP, team-protocol, and shadow tools.

```json
{
  "kind": "tool_event",
  "severity": "info",
  "payload": {
    "category": "host_tool",
    "tool_name": "commandExecution",
    "phase": "completed",
    "target": "uv run pytest tests/test_x.py",
    "status": "success",
    "exit_code": 0,
    "duration_ms": 1234,
    "bytes_read": 0,
    "bytes_written": 0,
    "stdout_preview": "12 passed",
    "stderr_preview": "",
    "raw_backend_type": "commandExecution"
  }
}
```

Categories:

- `host_tool`: Codex App Server `commandExecution`, `fileChange`, `webSearch`,
  image events (Section 1 found these are currently dropped).
- `mcp_tool`: Codex App Server `mcpToolCall`; Gemini/Kimi wrapper calls.
- `team_tool`: wrapper `send_message`, `task_update`, `task_create`, etc.
- `shadow_tool`: wrapper host-tool replacements such as
  `mcp_anyteam_shell`, `mcp_anyteam_read_file`, `mcp_anyteam_write_file`,
  `mcp_anyteam_edit_file`, `mcp_anyteam_search`, `mcp_anyteam_web_fetch`
  (the exposed shadow tool list is `src/claude_anyteam/wrapper_server.py:54-68`,
  implementations start at `:346-581`).

Channel policy:

- Always stderr + event log.
- Mailbox only for `phase=failed`, permission/security-relevant actions, or
  rate-limited task checkpoints.
- Task state only for meaningful phase shifts ("running tests",
  "editing 3 files", "tool failed").

Where to land:

- Codex App Server: extend `_record()` to classify `params.item.type` beyond
  the current MCP-only subset (`src/claude_anyteam/codex.py:654-673`).
- Wrapper server: wrap each exposed tool in an event-emitting helper so
  Gemini/Kimi shadow tools are captured even when the backend stream is
  post-hoc or opaque (`src/claude_anyteam/wrapper_server.py:178-595`).
- Gemini/Kimi parsers: post-hoc normalize retained `tool_use` /
  `tool_calls[]` into `tool_event` digests after subprocess exit
  (`src/claude_anyteam/backends/gemini/invoke.py:440-463`,
  `src/claude_anyteam/backends/kimi/invoke.py:327-352`).

#### `artifact_event`

Use when a file-level change is observed either from backend events or local
artifact sampling.

```json
{
  "kind": "artifact_event",
  "payload": {
    "source": "codex_app_server.fileChange",
    "path": "bug-triage/B9-visibility-parity-investigation.md",
    "action": "modified",
    "bytes_delta": 4182,
    "line_delta": 93
  }
}
```

Channel policy:

- Event log always.
- Mailbox only as a coalesced checkpoint, e.g. "modified 2 files; running
  tests" every 60-120s or at turn completion.
- Task metadata should store a small rolling summary:
  `metadata.visibility.artifacts_changed_count`,
  `metadata.visibility.last_artifact_path`.

Where to land:

- Codex App Server: normalize `fileChange` items in `_record()`.
- Generic fallback: add a cheap sampler around `app_server_invoke()` for git
  repos (`git status --porcelain=v1` hash + file size/mtime summaries) and
  expose only deltas. This is the same signal the Section 5 soft watchdog uses.

#### `turn_progress`

Use for low-frequency progress snapshots and soft watchdog warnings.

```json
{
  "kind": "turn_progress",
  "summary": "running 5m01s; no visible checkpoint yet; asked Codex to checkpoint",
  "payload": {
    "elapsed_s": 301,
    "timeout_s": 900,
    "risk": "timeout_possible",
    "app_server_events": 383,
    "agent_message_bytes": 0,
    "mcp_tool_calls": 0,
    "host_tool_events": 0,
    "artifact_delta_bytes": 0,
    "last_checkpoint_at": null,
    "action_taken": "turn_steer_sent"
  }
}
```

Channel policy:

- Mailbox **yes**, but rate-limited. This is the lead-facing parity repair for
  the 14m36s silent incident.
- Task state **yes**: set `activeForm` to a compact string and merge the full
  counters into `metadata.visibility`.
- Stderr + event log always.

Where to land:

- Add `progress_hook` / `event_sink` argument to `app_server_invoke()` in
  `src/claude_anyteam/codex.py:599-843`.
- In `src/claude_anyteam/loop.py:_execute_task_app_server()` (`:755-816`),
  provide the sink that writes to `protocol_io.update_task()` and
  `send_json_to_lead()`.
- In `_handle_prose()` (Codex prose path at `src/claude_anyteam/loop.py:179-260`),
  provide a prose-turn sink that sends a single interim diagnostic to lead
  instead of waiting silently for the final fallback.

#### `turn_completed` / `turn_failed`

Use for terminal results and failures.

```json
{
  "kind": "turn_failed",
  "severity": "error",
  "summary": "Codex App Server timed out after 900s with no final response",
  "payload": {
    "exit_code": 124,
    "error": "app_server turn did not complete within 900.0s",
    "elapsed_s": 900,
    "structured": false,
    "events": 383,
    "tool_call_events": 0,
    "last_message_preview": ""
  }
}
```

Channel policy:

- Mailbox yes for failures and task completions.
- Task state yes for task turns (`blocked` / completed metadata).
- Stderr + event log always.

Where to land:

- Codex: right before returning `CodexResult` from `app_server_invoke()`
  (`src/claude_anyteam/codex.py:827-843`) and after `run()` parses exec output
  (`src/claude_anyteam/codex.py:521-539`).
- Loops: replace/augment terminal-only `task.codex_fail` / prose fallback
  handling with a lead-visible `turn_failed` payload. Current task blocking and
  prose fallback logic lives in Codex loop paths at `src/claude_anyteam/loop.py:179-260`,
  `:819-861`; Gemini/Kimi equivalents are in their backend loops per Section 2.

#### `visibility_degraded`

Use when the adapter can prove the lead is missing a native-like surface.

```json
{
  "kind": "visibility_degraded",
  "severity": "warn",
  "summary": "peer DM MCP unavailable; using protocol_io fallback",
  "payload": {
    "surface": "peer_dm",
    "reason": "wrapper_send_message_unavailable",
    "impact": "model may believe it cannot message peers",
    "suggested_fix": "respawn after team-patch or repair agentType rows"
  }
}
```

Channel policy:

- Mailbox yes when actionable.
- Event log + stderr always.

Where to land:

- Wrapper MCP probe / startup path: after `codex.mcp_probe_ok` and around
  wrapper config failures (`src/claude_anyteam/codex.py:245-307`,
  `src/claude_anyteam/codex.py:675-692`).
- Prose fallback path: when Codex/Gemini/Kimi did not use `send_message` but
  the adapter sends via `protocol_io.send_prose_to_lead()`.

### 6.4 New event log channel

Add an append-only JSONL file under the team directory:

```text
~/.claude/teams/<team>/events/<agent>.jsonl
```

Write one normalized envelope per line under the existing team `.lock` or a new
`events/.lock`. This is intentionally **not** a user notification surface. It is
for:

- later `check_teammate` / `read_events` tooling;
- regression tests that assert event-forwarding without scraping stderr;
- future host/UI integration if Claude Code ever reads an external event feed.

Why not only mailbox?

- A single Codex turn can emit hundreds of events (the live timeout had 383);
  inbox spam would make DMs unusable.
- Mailboxes mark messages read and are optimized for human/coordinator messages,
  not high-volume traces (`protocol_io.read_own_inbox()` warns about the
  mark-as-read rewrite hazard at `src/claude_anyteam/protocol_io.py:59-75`).

Minimal helper:

```python
protocol_io.append_visibility_event(team, agent, envelope)
```

Optional reader later:

```python
protocol_io.read_visibility_events(team, agent, since_seq=None, limit=100)
```

### 6.5 Implementation sequence

1. **Codex App Server MVP (highest value).**
   - Add event envelope models to `src/claude_anyteam/messages.py` (near
     `TaskCompleteOut` / `PermissionRequestOut`).
   - Add `send_visibility_event_to_lead()` and `append_visibility_event()` to
     `src/claude_anyteam/protocol_io.py`.
   - Extend `codex.py:_record()` to normalize `agentMessage`, `mcpToolCall`,
     `commandExecution`, `fileChange`, `webSearch`, `plan`, and `error`.
   - Add `event_sink` to `app_server_invoke()`; fan out via loop-provided sink.
   - Rate-limit mailbox progress to one message per 60s plus warnings/errors.

2. **Wrapper shadow-tool instrumentation (backend-neutral).**
   - Wrap exposed wrapper tools (`EXPOSED_TOOLS`) with start/completed/failed
     event emission.
   - For `send_message` / `task_update`, mark category `team_tool`.
   - For `mcp_anyteam_*`, mark category `shadow_tool`.
   - Write to the new event log by default; mailbox only on failures unless the
     model explicitly sends a progress message.

3. **Terminal digests for headless backends.**
   - Gemini/Kimi headless cannot stream today, so emit only `turn_started` and
     terminal `turn_completed`/`turn_failed`/`turn_digest`.
   - Include counts and raw-event-tail previews, not fake live progress.
   - Add B4 diagnostics (`task_idle_no_tool_calls`,
     `task_complete_unverified_tool_count`) here, because the needed
     `tool_call_events` and `files_changed` fields already exist on
     `CodexResult`.

4. **Gemini ACP live mode later.**
   - Refactor `session_prompt()` usage so the adapter can process
     `session/update` notifications while the prompt is in flight instead of
     draining only after return (`src/claude_anyteam/backends/gemini/acp.py:420-421`).
   - Reuse the same envelope/sink once ACP has a polling loop like Codex App
     Server.

### 6.6 Acceptance criteria

- A Codex App Server turn that emits `commandExecution` or `fileChange` creates
  a `tool_event` / `artifact_event` in the event log and a coalesced task
  progress update without relying on stderr.
- A 300s no-checkpoint Codex App Server turn sends one mailbox
  `turn_progress` warning and updates task `activeForm`; it does **not**
  early-interrupt unless opt-in hard interrupt is enabled.
- A wrapper `mcp_anyteam_shell` failure creates a `tool_event` failure entry
  and a concise lead-visible warning if it affects task completion.
- Gemini/Kimi headless timeout returns a terminal `turn_failed` digest with
  whatever events were captured; if no partial events are available, the digest
  explicitly says `partial_events_available: false`.
- Existing `task_complete`, `task_blocked`, `permission_request`, and
  `idle_notification` payloads continue to work unchanged; visibility events
  are additive and versioned.

## 7. Productivity lens

The visibility gap has a measurable cost in this bug-triage run. The most
concrete Codex sample is the B6 App Server incident: one prose turn consumed
**14m36s** and produced **0 durable artifacts**, then an explicit
checkpointing pattern produced roughly **240 lines** over the next **7.5
minutes** (~**32 lines/minute**) (`bug-triage/B6-turn-timeout.md:267-279`).
If the dead turn is included in the accounting, the same episode becomes
~240 lines over ~22.1 minutes, or ~**10.9 lines/minute**. In other words, the
silent visibility failure cut observed throughput by about **66%** for that
workstream, even though the productive turns proved the backend could write
quickly once forced into visible checkpoints. A default soft watchdog at 300s
would not have guaranteed better model behavior, but it would have exposed the
failure **9m36s earlier** than the observed 14m36s terminal timeout; an opt-in
420s hard non-progress interrupt would have saved about **7m36s** of wall-clock
wait on that specific no-artifact turn.

The Gemini productivity sample shows the same cost through a different symptom:
not a long hidden Codex turn, but no-action headless turns plus ambiguous idle
state. The original two Gemini researchers produced **0 files across ~32
aggregate agent-minutes**; after re-spawn/action-first prompting, the clearest
observed Gemini output was **2-4 notes after ~18.3 minutes** for one re-spawn
(~**0.11-0.22 files/minute** from the re-spawn point), while the original
zero-output wait pushed end-to-end effective rate closer to **0.06-0.12
files/minute** for that lane (`bug-triage/B4-gemini-productivity.md:267-277`).
The field comparison in B4 says Codex/Claude peers produced tens of artifacts
in the same campaign, so the issue was not merely final quality; it was that
the lead could not distinguish "not claimed", "prose-only reply", "running
but no tools", and "event parser missed tools" until enough time had passed
to abandon or re-spawn (`bug-triage/B4-gemini-productivity.md:248-263`).

That suggests three additive productivity taxes, each visible in the evidence:

1. **Silence tax:** the lead waits for the backend's terminal condition rather
   than seeing native-like tool/prose deltas. In B6 that was 14m36s; the
   proposed 300s soft diagnostic would reduce the blind interval by about
   two-thirds for similar failures.
2. **False-zero tax:** counters such as `tool_call_events == 0` are not a safe
   proxy for "no work happened" because Codex host tools are opaque and
   Gemini/Kimi parse different event taxonomies. That forces conservative
   policy: warn/steer first, hard-interrupt only when an operator opts into the
   risk.
3. **Recovery-turn tax:** once a hidden/prose-only turn fails, the team pays
   for status checks, nudges, re-spawns, and reassignment. In the Gemini case
   that meant at least ~32 zero-output agent-minutes plus lead-authored
   action-first prompts before reliable output could be judged.

The productivity conclusion is therefore narrower than "routed models are
worse": the routed adapters are losing throughput at the coordination layer.
A long-form/checkpoint preamble, B4's no-tool/no-file diagnostics, and Section
5's soft progress watchdog would not make every turn faster, but they would
convert hidden stalls into early, lead-visible decisions. Based on the B6
sample, that is worth on the order of **7-10 minutes saved per failed long
turn**; based on B4, it can prevent **tens of aggregate agent-minutes** from
being spent before the team even knows an executor did not act.

### 7.4 Live example — silent-clobber tax during B9 itself

This deliverable produced its own evidence of the coordination tax it is
documenting. While drafting B9 in parallel, the architect (`opus-architect`)
wrote §1 and §2 via Edit, returned a successful tool-call result, and moved
on. Another teammate independently wrote a stronger §2 over the top of the
first. **The first writer received no signal that their content was
overwritten** — no merge conflict, no clobber notification, no diff. The
overwrite was discovered only when the team-lead later read the file and
flagged §1/§2 as appearing "still placeholders" in their own snapshot, and
the architect grepped the file directly to verify state.

From the team-lead's seat the same gap was visible from a different angle:
they observed two `task_complete` notifications arrive for what looked like
the same nominal task ID at different timestamps, and **had no protocol
signal to distinguish "second teammate finalized the work" from "second
teammate clobbered the first teammate's earlier write."** The mailbox carries
`task_completed`; it does not carry `artifact_clobbered_by_peer`.

This is a structurally distinct failure mode from the false-positive
`task_complete` race (where a teammate marks a task done without doing the
work). Here both teammates *did* the work; the file substrate just doesn't
serialize that work into a per-section ownership model, and the mailbox
doesn't surface filesystem-level conflicts. Both teammates and the
coordinating architect were operating on stale assumptions about file state
for the entire window between the clobber and the manual file inspection.

This is exactly the failure mode P1 (`teammate_activity` mailbox class, §6.1)
would have surfaced. An emitted `artifact_event` with shape:

```json
{
  "type": "teammate_activity",
  "from": "<second writer>",
  "kind": "artifact_op",
  "tool": {"name": "Edit", "category": "host"},
  "extra": {
    "path": "bug-triage/B9-visibility-parity-investigation.md",
    "op": "overwrite",
    "previous_writer_inferred": "<first writer>",
    "bytes_in": 4321,
    "bytes_out": 6789
  }
}
```

…would have made the clobber visible at the moment it happened, in the
team-lead's inbox, in the same channel that already carries
`task_completed`. The first writer would have seen "your earlier write to
this path was overwritten" by polling their own inbox for activity events
addressed to them. Neither party would have needed to grep the file
manually to discover state drift. Quantified productivity cost for this
specific incident: ~3 minutes of coordination back-and-forth between
team-lead, opus-architect, and inferred peer writers to confirm the file
was actually intact — small per incident, but multiplied across every
multi-author file in a routed-team workflow this becomes a real recurring
tax on parallelism. The visibility gap, eating its own architects.

## 8. Summary (3 bullets for team-lead reply)

- **Visibility parity is not here yet:** routed teammates register and complete
  tasks, but Codex/Gemini/Kimi mostly keep assistant/tool telemetry inside the
  adapter result/logs; host-tool work, assistant deltas, no-action turns, and
  parser ambiguity are not surfaced to the lead with native-Claude fidelity.
- **The productivity cost is measurable:** B6 lost 14m36s for 0 artifacts before
  checkpointed turns delivered ~240 lines in ~7.5m, and B4 shows Gemini burned
  ~32 zero-output agent-minutes before action-first re-spawns; the main loss is
  coordination/recovery time, not necessarily model capability.
- **Recommended policy:** ship lead-visible soft progress diagnostics/checkpoint
  steers by default, keep early hard interrupts opt-in, and add the B4
  `task_idle_no_tool_calls` / `task_complete_unverified_tool_count` surfaces so
  "idle", "working", "responded but did not act", and "parser missed
  action" stop looking identical.
