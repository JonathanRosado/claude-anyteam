# 04 — Runtime trace of Agent Teams file protocol

Date: 2026-04-27  
Owner: codex-trace  
Sandbox team: `observe-trace`

## Executive summary

- I captured a fresh `observe-trace` run under `~/.claude/teams/observe-trace/` and `~/.claude/tasks/observe-trace/`, then tore the sandbox team down. The vendored artifacts live under `references/external-claude-code-re/runtime-trace/`.
- The file protocol is simple and visible: `config.json` for membership/session metadata, `inboxes/*.json` for mailbox messages, and `tasks/*.json` for task state. Config mutations after initial create use temp-file + rename; inbox/task mutations rewrite JSON files under `.lock` files.
- The tmux spawn envelope passes identity entirely via argv (`--agent-id`, `--agent-name`, `--team-name`, `--agent-color`, `--parent-session-id`, `--agent-type`, `--model`) plus env (`CLAUDECODE=1`, `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`). The observed `--parent-session-id` exactly matched the `leadSessionId` in `config.json`.
- IPC visible from outside was tmux/PTY only: the spawned child had fd 0/1/2 on `/dev/pts/23`; tmux held a Unix socket under `/tmp/tmux-1000/claude-swarm-871983`. No stable protocol socket or pipe for team state was visible.
- Important limitation: this was the direct-Python fallback path through `src/claude_teams`, with a trace-local fake child binary to avoid launching a second LLM. It is ground truth for file mutations and the tmux spawn envelope, not for native Claude Code's private in-process `AppState.tasks` or live tool-call/delta event stream.

## Methodology

### Background read

I read the requested north-star and prior research docs first:

- `CLAUDE.md`
- `bug-triage/B9-visibility-parity-investigation.md`
- `docs/internal/spawn-research-findings.md`
- `docs/internal/2026-prototype/research.md`
- `src/claude_teams/`

The relevant source mechanics are:

- team create/config write: `src/claude_teams/teams.py:40-85`
- atomic config rewrite: `src/claude_teams/teams.py:119-129`
- teammate add/remove: `src/claude_teams/teams.py:158-172`
- inbox create/append/read-mark: `src/claude_teams/messaging.py:34-39`, `146-178`, `42-79`
- task create/update/reset: `src/claude_teams/tasks.py:63-91`, `104-286`, `306-325`
- tmux spawn command: `src/claude_teams/spawner.py:51-73`

### Tools

`inotifywait` was not installed on this host, so I used a small ctypes-based Linux inotify watcher. Its output format is timestamp/event/path and is vendored at:

- `references/external-claude-code-re/runtime-trace/logs/python-inotify-watcher.py`
- `references/external-claude-code-re/runtime-trace/logs/teamtrace-observe.log`
- grouped summary: `references/external-claude-code-re/runtime-trace/logs/timeline-summary.txt`

I also captured:

- config snapshots: `references/external-claude-code-re/runtime-trace/snapshots/config/`
- inbox snapshots: `references/external-claude-code-re/runtime-trace/snapshots/inbox/`
- task snapshots: `references/external-claude-code-re/runtime-trace/snapshots/tasks/`
- readable diffs: `references/external-claude-code-re/runtime-trace/diffs/`
- tmux/process/IPC snapshots: `references/external-claude-code-re/runtime-trace/tmux/` and `references/external-claude-code-re/runtime-trace/proc/`
- methodology README: `references/external-claude-code-re/runtime-trace/README.md`

### Scenario

The scenario was direct-Python fallback rather than a nested Claude Code TUI session. I used the vendored Python modules to perform the same durable transitions:

1. create team `observe-trace` with parent session id `3917f28f-7e9a-4e14-98ec-815035fa83e4`;
2. add teammate `trace-child` to `config.json`;
3. write the initial inbox message;
4. build the tmux spawn command using `claude_teams.spawner.build_spawn_command()`;
5. spawn a trace-local fake child in tmux so argv/env/PTY could be captured without another LLM;
6. write tmux target `@2` back into `config.json`;
7. create and assign task `1`;
8. read/mark the teammate inbox;
9. write lead prose and idle notification;
10. send and read shutdown request;
11. write shutdown approval;
12. kill tmux child, remove member, reset task owner;
13. delete the sandbox team.

The exact spawn command is vendored at `references/external-claude-code-re/runtime-trace/logs/spawn-command.txt`.

### What was / was not observable

Observable:

- file creates, opens, reads (`ACCESS`), writes (`MODIFY`/`CLOSE_WRITE`), renames, and deletes under the sandbox team/task dirs;
- exact JSON before/after state for config, inboxes, and task files;
- tmux target allocation and pane/window state;
- spawned child argv, env, process tree, PTY fds, and tmux socket lsof output.

Not observable in this run:

- native Claude Code's private `AppState.tasks` updates;
- native assistant prose deltas/tool-call telemetry (`Read`, `Edit`, `Write`, `Bash`) while a real Claude child reasons;
- which process caused each inotify event (inotify path events are not PID-attributed);
- whether a real native child would perform extra project/session JSONL writes outside the watched sandbox paths.

Snapshot copying itself also produced some `OPEN`/`ACCESS` reads in the log; write transitions are identified by the marker boundaries and JSON diffs.

## Reconstructed timeline

All inotify timestamps below are local wall-clock (`America/La_Paz`) with second resolution. Message payload timestamps inside JSON are UTC with milliseconds.

| Time | Marker | Observed file/IPC transition | Evidence |
|---|---|---|---|
| 00:46:46 | `01-team-create` | Created `tasks/observe-trace/.lock`; created and wrote `teams/observe-trace/config.json`. | `logs/teamtrace-observe.log`, `snapshots/config/01-after-team-create.json` |
| 00:46:46 | `02-add-member-config-without-pane` | Read current config; wrote temp `tmp*.tmp`; renamed temp over `config.json`. | `logs/teamtrace-observe.log`, `diffs/config-01-to-02-add-member.diff` |
| 00:46:46 | `03-initial-inbox-write` | Created `inboxes/`, `.lock`, and `trace-child.json`; appended initial lead message. | `snapshots/inbox/03-after-initial-inbox/trace-child.json` |
| 00:46:46 | `04-tmux-spawn` | No watched team-file changes; tmux created window `@2`. | `tmux/spawn-result.txt`, `tmux/list-windows.txt` |
| 00:46:46 | `05-config-update-pane-id` | Read config; temp-file rewrite changed `trace-child.tmuxPaneId` from `""` to `"@2"`. | `diffs/config-02-to-05-pane-id.diff` |
| 00:46:47 | `06-capture-process-and-tmux-state` | Captured child/tmux IPC. Child fds 0/1/2 were `/dev/pts/23`; tmux socket was `/tmp/tmux-1000/claude-swarm-871983`. | `proc/lsof-fake-child.txt`, `proc/lsof-tmux-socket-grep.txt` |
| 00:46:47 | `07-task-create` | Created `tasks/observe-trace/1.json` with pending task. | `snapshots/tasks/07-after-task-create/1.json` |
| 00:46:47 | `08-task-assign` | Rewrote task with `owner=trace-child`, `status=in_progress`, active form; appended structured `task_assignment` to child inbox. | `diffs/task-07-to-08-assign.diff`, `diffs/inbox-child-03-to-08-task-assignment.diff` |
| 00:46:47 | `09-child-read-inbox-mark-read` | Read child inbox under `.lock`; rewrote both unread messages with `read: true`. | `diffs/inbox-child-08-to-09-read-mark.diff` |
| 00:46:47 | `10-child-send-prose-to-lead` | Created `team-lead.json`; wrote child prose message. | `snapshots/inbox/10-after-child-prose/team-lead.json` |
| 00:46:47 | `11-idle-notification` | Appended structured `idle_notification` to lead inbox. | `diffs/inbox-lead-10-to-11-idle.diff` |
| 00:46:47 | `12-shutdown-request` | Appended structured `shutdown_request` to child inbox. | `diffs/inbox-child-11-to-12-shutdown-request.diff` |
| 00:46:47 | `13-child-read-shutdown` | Read child inbox under `.lock`; rewrote shutdown request as read. | `snapshots/inbox/13-after-child-read-shutdown/trace-child.json` |
| 00:46:47 | `14-child-approve-shutdown` | Appended structured `shutdown_response`/approval to lead inbox. | `diffs/inbox-lead-13-to-14-shutdown-approved.diff` |
| 00:46:48 | `15-kill-tmux-and-remove-member` | Killed tmux target; temp-file config rewrite removed `trace-child`; task reset removed owner and returned to pending. | `diffs/config-05-to-15-remove-member.diff`, `diffs/task-08-to-15-reset-owner.diff` |
| 00:46:48 | `16-delete-team` | Deleted `config.json`, inbox files/dir, task `1.json`, and watched sandbox directories. | `logs/teamtrace-observe.log`, `snapshots/config/16-after-delete-team.json` |

## Key file diffs

### Config: team create → teammate add

`config.json` after team create contained only lead metadata (`snapshots/config/01-after-team-create.json`). Adding the teammate appended a full `TeammateMember` object with prompt, color, cwd, backend, and empty `tmuxPaneId` (`snapshots/config/02-after-add-member-no-pane.json`). Full diff:

- `references/external-claude-code-re/runtime-trace/diffs/config-01-to-02-add-member.diff`

Key fields added:

```diff
+      "agentId": "trace-child@observe-trace",
+      "agentType": "general-purpose",
+      "backendType": "claude",
+      "model": "haiku",
+      "name": "trace-child",
+      "prompt": "Trace prompt: stay idle; this run observes transport only.",
+      "tmuxPaneId": ""
```

### Config: spawn target write-back

After tmux returned `@2`, `config.json` was atomically rewritten via temp-file rename. Full diff:

- `references/external-claude-code-re/runtime-trace/diffs/config-02-to-05-pane-id.diff`

```diff
-      "tmuxPaneId": ""
+      "tmuxPaneId": "@2"
```

### Spawn argv/env and parent-session flow

The `leadSessionId` in `snapshots/config/01-after-team-create.json` was:

```text
3917f28f-7e9a-4e14-98ec-815035fa83e4
```

The same value was passed to the child as:

```text
--parent-session-id 3917f28f-7e9a-4e14-98ec-815035fa83e4
```

Evidence:

- `references/external-claude-code-re/runtime-trace/logs/parent-session-id.txt`
- `references/external-claude-code-re/runtime-trace/logs/spawn-command.txt`
- `references/external-claude-code-re/runtime-trace/proc/fake-child-env-and-argv.txt`

The child environment included `CLAUDECODE=1` and `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`; it also inherited this session's `CLAUDE_CODE_TEAMMATE_COMMAND`.

### Task: create → assign

Task assignment rewrote task state and appended a structured inbox message. Full diffs:

- `references/external-claude-code-re/runtime-trace/diffs/task-07-to-08-assign.diff`
- `references/external-claude-code-re/runtime-trace/diffs/inbox-child-03-to-08-task-assignment.diff`

```diff
-  "activeForm": "",
-  "status": "pending",
+  "activeForm": "Tracing runtime file protocol",
+  "owner": "trace-child",
+  "status": "in_progress",
```

### Inbox read / mark-read

Reading the teammate inbox with `mark_as_read=True` rewrote the same JSON array, flipping both unread lead messages to `read: true`. Full diff:

- `references/external-claude-code-re/runtime-trace/diffs/inbox-child-08-to-09-read-mark.diff`

```diff
-    "read": false,
+    "read": true,
```

### Idle and shutdown messages

Idle notification appended a JSON payload to `team-lead.json` with summary `idle`. Full diff:

- `references/external-claude-code-re/runtime-trace/diffs/inbox-lead-10-to-11-idle.diff`

Shutdown request appended a JSON payload to `trace-child.json`; shutdown approval appended a JSON payload to `team-lead.json`. Full diffs:

- `references/external-claude-code-re/runtime-trace/diffs/inbox-child-11-to-12-shutdown-request.diff`
- `references/external-claude-code-re/runtime-trace/diffs/inbox-lead-13-to-14-shutdown-approved.diff`

### Cleanup

Member removal deleted the teammate object from config, and `reset_owner_tasks()` removed task owner/status progress. Full diffs:

- `references/external-claude-code-re/runtime-trace/diffs/config-05-to-15-remove-member.diff`
- `references/external-claude-code-re/runtime-trace/diffs/task-08-to-15-reset-owner.diff`

The final `team_delete()` removed the sandbox directories; `snapshots/config/16-after-delete-team.json` records the missing config path.

## Reconstructed message sequence

```mermaid
sequenceDiagram
    participant LeadAPI as Direct Python driver / lead role
    participant FS as ~/.claude file protocol
    participant Tmux as tmux server
    participant Child as trace-child process

    LeadAPI->>FS: create_team(observe-trace) writes config.json + tasks/.lock
    LeadAPI->>FS: add_member(trace-child) temp-write + rename config.json
    LeadAPI->>FS: append initial prompt to inboxes/trace-child.json
    LeadAPI->>Tmux: new-window command from build_spawn_command()
    Tmux->>Child: exec fake child with Agent Teams argv/env
    Child-->>LeadAPI: pid/env/argv captured to vendored trace files
    LeadAPI->>FS: write tmuxPaneId @2 into config.json
    LeadAPI->>FS: create tasks/1.json
    LeadAPI->>FS: update task owner/status; append task_assignment to child inbox
    Child->>FS: read_inbox(mark_as_read=True); rewrite child inbox read flags
    Child->>FS: append prose message to team-lead inbox
    Child->>FS: append idle_notification to team-lead inbox
    LeadAPI->>FS: append shutdown_request to child inbox
    Child->>FS: read shutdown; rewrite child inbox read flag
    Child->>FS: append shutdown_response approve=true to lead inbox
    LeadAPI->>Tmux: kill target @2
    LeadAPI->>FS: remove member from config; reset task owner/status
    LeadAPI->>FS: delete observe-trace team/task directories
```

## IPC / process observations

- Tmux created window `@2` named `@claude-team | trace-child` (`tmux/list-windows.txt`).
- `tmux/list-panes.txt` showed `window=@2 pane=%11 pid=936186 tty=/dev/pts/23 command=bash` for the child.
- `proc/lsof-fake-child.txt` showed child fd `0u`, `1u`, and `2u` all on `/dev/pts/23`; no open descriptors to team config/inbox/task files were held.
- `proc/lsof-tmux-socket-grep.txt` showed tmux server `874960` listening on `/tmp/tmux-1000/claude-swarm-871983` plus connected Unix-stream fds.
- The file protocol itself was not IPC over a socket; state transfer happened through JSON file rewrites and advisory lock files.

## Cannot-observe-from-outside gaps

1. **Native UI presence and progress.** Prior research says native teammate rows are backed by the leader's in-memory `AppState.tasks`, not by passive reads of `config.json`. This trace cannot see that in-memory map.
2. **Native tool-call/delta stream.** There is no file in `~/.claude/teams/<team>` that records live `Read`/`Edit`/`Write`/`Bash` deltas comparable to what the lead TUI renders for native Claude teammates.
3. **PID attribution for file events.** inotify reports path events, not the process that caused them. The marker boundaries and JSON diffs identify our driver steps, but not kernel-level causality by PID.
4. **Real child Claude side effects.** The fake child captured the spawn envelope without running an LLM. A real child Claude process may also touch project session JSONL, subagent metadata, caches, or other `~/.claude` paths outside this sandbox watch.
5. **Lock internals beyond path events.** The log shows `.lock` create/open/modify/delete around inbox/task operations, but not lock owner, wait time, or contention without deeper tracing (`strace`, audit/fanotify, or instrumented code).
6. **Renderer-only state.** Idle reasons, message previews, and progress strings shown in a native lead pane may be transformed or throttled before rendering; those transformations are not represented in the file protocol.
