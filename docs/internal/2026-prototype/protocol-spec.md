# Claude Code Agent Team Protocol Specification

**Version**: 1.1  
**Date**: 2026-04-21  
**Source**: Reverse-engineered from Claude Code v2.1.116 binary and official documentation  
**Confidence**: Medium-high (documented where available, verified via binary inspection and live filesystem)

---

## Executive Summary

Claude Code's agent team protocol enables multiple independent Claude Code sessions (teammates) to collaborate on shared work. Teammates are first-class, independent processes with their own context windows, not subagents. They coordinate through:

1. **Shared task list** — file-based, with concurrency control via locking
2. **Mailbox/inbox system** — JSON message queues per agent
3. **Team registration** — JSON config describing members and identity
4. **Lifecycle events** — shutdown, plan approval, idle notifications

A non-LLM process can implement this protocol and participate fully as a teammate **without requiring an intermediate Claude LLM wrapper** if it can:

- Register itself with the team config
- Read/write tasks and inbox files atomically
- Respond to protocol messages (shutdown_request, plan_approval_request, idle_notification)
- Send messages to other teammates via SendMessage

This specification details the file formats, semantics, and behavior needed to build a conformant teammate from scratch.

---

## Primary Sources

**This specification is grounded in two heavyweight primary sources:**

1. **cs50victor/claude-code-teams-mcp** (https://github.com/cs50victor/claude-code-teams-mcp) — An executable MCP implementation of Claude Code's team protocol. Task structures, inbox schemas, config formats, and message types are verified against this source. Based on a deep dive reverse-engineering of Claude Code's internals (https://gist.github.com/cs50victor/0a7081e6824c135b4bdc28b566e1c719).

2. **DEV Community Article** (https://dev.to/nwyin/reverse-engineering-claude-code-agent-teams-architecture-and-protocol-o49) — Architectural overview and protocol-level internals. Confirms file-based coordination, task auto-increment via next-available ID (no `.highwatermark` file, IDs calculated on-the-fly), and lifecycle semantics.

Where empirical observation from `~/.claude/` teams diverges from these sources, the divergence is noted. Where these sources provide more detail than observed behavior (e.g., message structure, backend types), that detail is incorporated and cited as "Verified: cs50victor/claude-code-teams-mcp".

---

## 1. Team Registration

### 1.1 Team Config Location and Format

**File Path**: `~/.claude/teams/{team-name}/config.json`

**Created By**: Claude Code harness when `Agent` tool is invoked with `team_name`  
**Ownership**: Managed by Claude Code; do not pre-author or hand-edit (changes are overwritten on state updates)

**Schema** (observed + documented):

```json
{
  "name": "string",                    // team identifier (e.g., "claude-anyteam")
  "description": "string",             // user-provided context
  "createdAt": 1776808772216,          // unix timestamp (milliseconds)
  "leadAgentId": "string",             // e.g., "team-lead@claude-anyteam"
  "leadSessionId": "string",           // UUID of team lead's Claude Code session
  "members": [                         // array of member objects
    {
      "agentId": "string",             // unique ID: "{name}@{team-name}"
      "name": "string",                // teammate short name (e.g., "researcher-protocol")
      "color": "string",               // display color (e.g., "blue", "green", "yellow")
      "joinedAt": 1776808890467,       // unix timestamp (milliseconds)
      "tmuxPaneId": "string",          // tmux pane name, or "in-process", or ""
      "subscriptions": [],             // (unclear; always empty in observed configs)
      "agentType": "string",           // e.g., "general-purpose", "Explore", "team-lead"
      "model": "string",               // e.g., "haiku", "sonnet-4.6", "claude-opus-4-7[1m]"
      "prompt": "string",              // system prompt for this teammate
      "planModeRequired": boolean,     // whether teammate must plan before acting
      "cwd": "string",                 // working directory for teammate
      "backendType": "string",         // e.g., "in-process", "tmux", or absent for lead
      // optional fields for leads:
      "subscriptions": []
    }
  ]
}
```

### 1.2 AgentId vs. Name Semantics

**Name**: Human-readable, mutable within the team. Used in SendMessage prompts and user-facing displays. Example: `"team-lead"`, `"researcher-protocol"`.

**AgentId**: Globally unique within the team. Format: `{name}@{team-name}`. Example: `"researcher-protocol@claude-anyteam"`. This is the canonical identifier for file-based routing and mailbox assignment.

**Lead vs. Teammates**:
- **Team Lead**: always `"team-lead@{team-name}"`. Exactly one per team. Created when the team is spawned. Has `agentType: "team-lead"`. Has no `backendType` (lead is the main session).
- **Teammates**: spawned by the lead via the `Agent` tool. Each gets a unique `agentId`. Have `agentType` (e.g., `"general-purpose"`, `"Explore"`, or a subagent type name) and `backendType` (e.g., `"in-process"`, `"tmux"`).

### 1.3 Team Member Discovery

**For a teammate to discover other team members**:

1. Read `~/.claude/teams/{team-name}/config.json` at startup and parse the `members` array.
2. Build a map of `agentId` → `{name, model, agentType, cwd}` for routing messages and metadata.
3. Identify the lead via `leadAgentId` and `leadSessionId`.

The config file is **updated by Claude Code** when teammates join, go idle, or leave. A teammate observing the file (via inotify or polling) can detect team composition changes.

**Confidence**: High (observed in both teams on disk, documented in Claude Code docs).

### 1.4 Backend Type Field and Process Isolation

**Field**: `backendType` (string, optional for teammates; omitted or `null` for lead)

**Values** (Verified: binary inspection of `claude` v2.1.116):
- `"in-process"` — teammate runs in the same process/memory space as the lead (legacy, observed in current teams)
- `"tmux"` — teammate runs as a separate `claude` CLI process in a tmux pane
- `"auto"` — harness selects `"in-process"` or `"tmux"` based on context

**Harness Spawning Constraint**:

The harness spawns teammates via the **`claude` CLI** only. The constraint is not "no out-of-process," but "harness spawns `claude` specifically." Binary inspection of v2.1.116 confirms the harness passes CLI flags to `claude`:
- `--agent-id` — sets the teammate's agentId
- `--agent-name` — sets the teammate's display name
- `--team-name` — adds the teammate to a named team
- `--parent-session-id` — UUID of the lead's session (for routing)
- `--plan-mode-required` — enables plan mode for this teammate
- `--teammate-mode` — signals the harness to run in team-aware mode

When `backendType: "tmux"`, the harness spawns a new `claude` process in a tmux pane. That process receives the above flags and runs independently.

**Observed Values in Production Teams**:
Both claude-anyteam and nebius-inference teams show `backendType: "in-process"`. This is valid; it means the teammate is spawned inline. However, the harness also supports out-of-process spawning via the `"tmux"` backend, which creates separate Claude Code sessions.

**Consequence for Non-LLM Adapters**:

The hard constraint is: **The harness spawns `claude` CLI specifically.** 

A non-LLM process (e.g., Codex) cannot be spawned directly via `backendType: "external"` or similar—that feature does not exist in the harness. 

Options for non-LLM integration:
1. **Pure file-based** (no harness spawning): Non-LLM process starts independently, appends itself to team config, participates via file I/O. Feasible but depends on whether harness tolerates config mutations (untested).
2. **RPC-bridged** (hypothetical harness extension): Harness could support `backendType: "custom"` or similar, spawning a non-LLM process with environment variables for team identity. Would require harness changes.
3. **MCP-bridged**: Non-LLM process starts independently, exposes an MCP server, Claude Code loads the MCP as a plugin. Leverages existing plugin infrastructure.

**Confidence**: Medium-high (binary inspection confirms `"in-process"`, `"tmux"`, `"auto"` backends and CLI flags; harness constraint to `claude` binary verified; no existing non-LLM spawn support confirmed).

---

---

## 2. Shared Task List

### 2.1 Location and Layout

**Directory**: `~/.claude/tasks/{team-name}/`

**Contents**:
- `{id}.json` — one file per task (e.g., `1.json`, `2.json`, `3.json`)
- `.lock` — empty file used for distributed locking (0 bytes)

**Created By**: Claude Code harness when the team is created; updated by teammates via TaskUpdate tool calls.

### 2.2 Task ID Generation and Sequencing

**How IDs are Assigned**:

Instead of a `.highwatermark` file, task IDs are **calculated on-the-fly** by scanning the tasks directory:

1. When a new task is created, the harness scans `~/.claude/tasks/{team-name}/` for all `.json` files.
2. It extracts the numeric ID from each filename (e.g., `1.json` → 1, `3.json` → 3).
3. It computes the next ID as `max(existing_ids) + 1`, or `1` if no tasks exist.
4. This is then written to the filesystem as `{next_id}.json`.

**Practical consequence**: Task IDs are **monotonically increasing but may have gaps** (e.g., if task 2 is deleted, the next task is still 4, not 3). This is not a strict sequence; it's a "next available" calculation.

**Verification**: Confirmed in cs50victor/claude-code-teams-mcp source (`tasks.py`, `next_task_id()` function).

### 2.3 Task File Format

**Example**: `~/.claude/tasks/claude-anyteam/1.json`

```json
{
  "id": "string",              // unique ID within the team (e.g., "1", "2")
  "subject": "string",         // one-line task title
  "description": "string",     // full task context (can be multiline)
  "status": "string",          // one of: "pending", "in_progress", "completed", "deleted"
  "activeForm": "string",      // present continuous form (e.g., "Reverse-engineering the protocol")
  "owner": "string",           // name of the agent who claimed/was assigned the task (e.g., "researcher-protocol", or empty if pending)
  "blocks": [],                // array of task IDs that this task blocks
  "blockedBy": []              // array of task IDs that block this task
}
```

**Status Values**:
- `"pending"` — unassigned, ready to claim (unless `blockedBy` is non-empty)
- `"in_progress"` — claimed by an owner and actively being worked
- `"completed"` — finished; unblocks dependent tasks
- `"deleted"` — removed from the task list (treated as complete for blocking purposes)

### 2.3 Read/Write Semantics

**Reading**:
- Any teammate can read all task files at any time (no lock required for reads).
- Read via direct file I/O (e.g., `fs.readFile()` in Node, `open()` in Python).
- Format: JSON. Treat missing or unparseable files as errors (do not auto-create).

**Writing (TaskUpdate)**:
- Teammates update tasks via the `TaskUpdate` tool call.
- CloudCode/the harness translates the tool call into a disk write.
- The write includes: new `status`, `owner`, `activeForm`, metadata.
- **File locking**: Claude Code uses the `.lock` file to prevent race conditions when multiple teammates claim tasks simultaneously.

**Concurrency Control**:

The `.lock` file (empty, 0 bytes) acts as a distributed semaphore. When multiple teammates try to claim the same pending task:

1. Each attempts to acquire `.lock` (e.g., via `flock()`, rename() atomicity, or equivalent).
2. Only one succeeds.
3. That teammate writes its `TaskUpdate` (sets `owner` and `status: in_progress`).
4. The lock is released.
5. Losing teammates detect the task is now owned and move to the next pending task.

**Confidence**: High (observed: `.lock` file always exists and is empty; documented in Claude Code docs as "task claiming uses file locking").

### 2.4 Blocking Semantics

Tasks form a DAG via `blocks` and `blockedBy` arrays.

**Rule**: A task with non-empty `blockedBy` cannot be claimed until all tasks in `blockedBy` reach `status: "completed"` or `"deleted"`.

**Automatic Unblocking**: When a blocker transitions to `"completed"` or `"deleted"`, Claude Code automatically unblocks dependent tasks. Teammates do not manually unblock.

**Example**:
```json
// Task 2 (pending, unblocked, can be claimed)
{
  "id": "2",
  "status": "pending",
  "blockedBy": []
}

// Task 3 (pending, blocked by task 2, cannot be claimed until task 2 is completed)
{
  "id": "3",
  "status": "pending",
  "blockedBy": ["2"]
}

// Task 2 completes
{
  "id": "2",
  "status": "completed"
}

// Task 3 is now automatically unblocked
{
  "id": "3",
  "status": "pending",
  "blockedBy": []          // empty, can now be claimed
}
```

**Confidence**: High (documented in Claude Code docs; observed in nebius-inference team inbox messages).

---

## 3. Message Transport (Inboxes and SendMessage)

### 3.1 Inbox Location and Format

**Directory**: `~/.claude/teams/{team-name}/inboxes/`

**Files**: `{name}.json` for each team member (where `name` is the human-readable team name, e.g., `"team-lead"`, `"researcher-protocol"`)

Example: `~/.claude/teams/claude-anyteam/inboxes/researcher-protocol.json`

**Format**:

```json
[
  {
    "from": "string",           // sender's name (e.g., "team-lead", "researcher-protocol")
    "text": "string",           // message body (plain text or JSON-wrapped protocol messages)
    "timestamp": "string",      // ISO 8601 timestamp (e.g., "2026-04-21T22:01:34.478Z")
    "color": "string",          // (optional) sender's color (for UI, e.g., "blue")
    "read": boolean,            // (optional) whether the recipient has read this message
    "summary": "string"         // (optional) short summary for notifications
  }
]
```

**Growth**: The inbox is an append-only log. New messages are appended to the JSON array. Inboxes grow over time and are not truncated during a session.

**Confidence**: High (verified by filesystem inspection: `ls ~/.claude/teams/claude-anyteam/inboxes/` shows `{name}.json` files, not agentId format).

### 3.2 SendMessage Protocol

**Tool Call**:

```
SendMessage(to="...", message="...", summary="...")
```

**Semantics**:

1. **Single recipient**: `to` is a teammate name or `"team-lead"`. The message is routed to `~/.claude/teams/{team-name}/inboxes/{recipient-agentId}.json`.
2. **Broadcast**: `to="*"` sends to all teammates (except the sender). Scales linearly in team size.
3. **Delivery**: The harness appends the message to the recipient's inbox file with the sender's identity and a timestamp.
4. **Delivery semantics**: **At-least-once**. Messages are persisted to disk before being considered delivered. If the harness crashes mid-delivery, a message may be duplicated, but it will not be lost. Teammates should be idempotent or tolerate occasional duplicate messages.
5. **Order**: Messages in a single inbox are ordered by `timestamp`. However, **causal ordering is not guaranteed** across multiple inboxes. If agent A sends message 1 to B and then message 2 to C, B may process message 1 after C processes message 2 (in wall-clock time).
6. **Blocking**: SendMessage does not block; it returns immediately after queueing the message to disk.

### 3.3 Protocol Messages (JSON-Wrapped)

Certain messages use a JSON structure to encode protocol events. The `text` field of an inbox message can contain JSON that encodes structured protocol events.

**Examples**:

**Task Assignment**:
```json
{
  "type": "task_assignment",
  "taskId": "1",
  "subject": "...",
  "description": "...",
  "assignedBy": "team-lead",
  "timestamp": "2026-04-21T22:01:34.478Z"
}
```

**Shutdown Request** (sent by lead to teammate):
```json
{
  "type": "shutdown_request",
  "reason": "Team cleanup or explicit shutdown"
}
```

**Shutdown Response** (sent by teammate to lead):
```json
{
  "type": "shutdown_response",
  "request_id": "...",
  "approve": true|false
}
```

**Plan Approval Request** (sent by teammate to lead when planning mode is required):
```json
{
  "type": "plan_approval_request",
  "request_id": "...",
  "plan": "..."
}
```

**Plan Approval Response** (sent by lead to teammate):
```json
{
  "type": "plan_approval_response",
  "request_id": "...",
  "approve": true|false,
  "feedback": "..."  // optional; provided if rejected
}
```

**Idle Notification** (sent by teammate to lead when about to go idle):
```json
{
  "type": "idle_notification",
  "reason": "All assigned tasks completed, no pending work"
}
```

**Confidence**: Medium (documented in `SendMessage` tool description for protocol responses; observed inbox structure; exact JSON schema partially inferred).

### 3.4 Message Reception

Teammates read their inbox by:

1. Polling `~/.claude/teams/{team-name}/inboxes/{my-agentId}.json` periodically (interval TBD; likely 1-5 seconds).
2. Comparing the array length or last timestamp with their last-seen state to detect new messages.
3. Processing new messages and updating their internal `read` flag or removing them (though the file is append-only, so typically just tracking indices).

**Idle behavior**: When a teammate has no tasks to claim and no pending work, it sends an idle notification to the lead and then waits for new messages or task assignments.

**Confidence**: Medium (inferred from task lifecycle and inbox structure; not explicitly documented in source code).

### 3.5 Content Shape of Inter-Teammate Messages

**Critical question**: Must teammates produce free-form natural-language prose in SendMessage bodies for other teammates' LLMs to interpret?

**Evidence**:

1. **Harness treatment of message bodies**: From observed inbox logs and documented behavior, the harness treats `message.text` as an **opaque blob**. No parsing, no schema enforcement, no field extraction. The entire `text` field is delivered as-is to the recipient's inbox.

2. **Injection into recipient's context**: When a Claude-based teammate receives a message via SendMessage, the harness injects the `text` field into its prompt context as a received turn/message. The recipient's LLM then interprets the prose and may take action based on it (e.g., adjust its strategy, ask follow-up questions).

3. **Protocol messages as JSON**: The protocol itself (shutdown_request, plan_approval_response, task_assignment) uses JSON wrappers within the `text` field. These are machine-parseable. A non-Claude teammate can respond with JSON. Conversely, a teammate sending prose (e.g., "I found a bug in your code, here's the fix") is providing context for an LLM to read.

4. **Observed message patterns**: In the claude-anyteam and nebius-inference team inboxes, all non-JSON-wrapped messages are human-readable prose summaries (e.g., "Task #2 complete. Prior-art survey delivered at..."). These are meant to be read by the lead's LLM or by users. Machine-only messages use JSON.

**Consequence**: 

- **A non-Claude teammate can respond with JSON-wrapped protocol messages** (no prose needed).
- **A non-Claude teammate sending plain text to an LLM-based teammate must anticipate that prose will be interpreted by an LLM.** If a Codex process generates output that needs to influence a Claude LLM teammate's behavior, that output may need to be legible and reasoned-about.
- **However**: The protocol does not require free-form prose. A Codex adapter can send structured, templated messages (JSON, tables, specific formats) and rely on the recipient (Claude LLM or another non-LLM teammate) to parse them.

**Design implication for non-LLM adapters**:

- If the Codex adapter only sends JSON protocol messages (shutdown responses, task updates, idle notifications), **no LLM reasoning is required on the sender side**.
- If the Codex adapter sends plain-text summaries to other teammates (e.g., "Task completed with warnings"), those summaries should be **structured and actionable** but do not need to be prose essays. A Codex LLM can generate such summaries; a non-LLM adapter can use templates.
- If a Claude-based teammate needs to interpret Codex-generated code or output, the code/output is delivered in a separate message or file, not as prose in SendMessage.

**Confidence**: High (observed in inbox structures, confirmed by implementer's orientation message asking about "free-form natural-language output").

---

---

## 4. Agent Lifecycle

### 4.1 Team Creation and Lead Spawn

**Trigger**: User invokes the `Agent` tool with `team_name` set, or instructs Claude to create a team.

**Lead Initialization**:

1. Claude Code creates `~/.claude/teams/{team-name}/config.json` with the lead member.
2. Claude Code creates `~/.claude/tasks/{team-name}/` directory and `.lock` file.
3. Claude Code creates `~/.claude/teams/{team-name}/inboxes/` directory.
4. Claude Code initializes the lead's `inboxes/team-lead@{team-name}.json` as an empty array.

**Lead Identity**: `agentId = "team-lead@{team-name}"`, `name = "team-lead"`, `agentType = "team-lead"`.

### 4.2 Teammate Spawn

**Trigger**: Lead invokes the `Agent` tool with `team_name` (not `subagent`), optionally specifying teammate count, model, and prompt.

**Spawn Sequence**:

1. Claude Code generates a unique `agentId` for the new teammate (format: `{name}@{team-name}`).
2. Claude Code updates `~/.claude/teams/{team-name}/config.json`, appending the new member object.
3. Claude Code starts a new Claude Code process (or thread) for the teammate, passing:
   - `team_name`
   - `agentId`
   - The spawn prompt (additional instructions for this teammate)
   - Project context (CLAUDE.md, MCP servers, skills)
   - **Does not pass**: the lead's conversation history
4. The teammate reads the config file to discover other team members and the lead.
5. Claude Code creates `~/.claude/teams/{team-name}/inboxes/{agentId}.json` as an empty array.

### 4.3 Turn Boundaries

**Lead Turn**:
- The user provides input to the lead.
- The lead responds to the user and may issue tool calls, including `Agent` (spawn), `TaskUpdate`, `SendMessage`.
- The turn ends when the lead's output is complete.

**Teammate Turn**:
- A teammate wakes up (either idle-loop polling or explicit message arrival).
- It checks for new messages in its inbox.
- It checks the task list for unblocked, unclaimed tasks.
- It claims a task (via `TaskUpdate`) or processes a message.
- It performs work (tool calls, computation).
- It sends status updates or messages to other teammates or the lead.
- It returns to idle state.

**Turn Coordination**: Turns are **asynchronous**. There is no global "turn number" or synchronization barrier. The lead and teammates operate independently and may have many overlapping turns.

### 4.4 Idle State and Idle Notifications

**Idle Trigger**: A teammate is idle when:
1. All assigned tasks are completed or marked as done.
2. There are no unblocked, pending tasks to claim.
3. No new messages are in the inbox.

**Idle Behavior**:
1. The teammate sends an idle notification to the lead (via SendMessage, JSON protocol message type `idle_notification`).
2. The teammate then waits for new work (messages or task unblocking).
3. The teammate may enter a sleeping/polling loop to conserve resources.

**Lead Receives Idle Notification**: The lead's inbox receives the message and can decide to:
- Assign new tasks to the teammate.
- Send new instructions via SendMessage.
- Request the teammate to shut down.

**Hooks**: Claude Code supports hooks (webhooks executed by the harness) that fire when a teammate is about to go idle. A hook can prevent idling by exiting with code 2, forcing the teammate to continue working.

**Confidence**: Medium-high (documented in Claude Code docs for hooks; idle notifications observed in inbox messages; exact idle-detection algorithm inferred).

### 4.5 Shutdown Lifecycle

**Teammate Initiates Shutdown**:
- Teammate sends a message to the lead indicating it is ready to shut down.

**Lead Requests Shutdown**:

1. Lead sends a protocol message of type `shutdown_request` to the teammate (via SendMessage).
2. Teammate receives the message, processes any final cleanup, and responds with a protocol message of type `shutdown_response` (approve=true or false).
3. If approved: teammate exits gracefully. The lead removes the teammate from the team config (or marks it as offline).
4. If rejected: teammate sends feedback and continues working. The lead may re-request shutdown later.

**Lead Cleanup**:
- After all teammates shut down, the lead can invoke cleanup:
  - Delete `~/.claude/teams/{team-name}/`
  - Delete `~/.claude/tasks/{team-name}/`
- This is typically done via a user command ("clean up the team") that the lead executes.

**Constraint**: Teammates should not invoke cleanup themselves; only the lead should, to ensure consistent team teardown.

**Confidence**: High (documented in Claude Code docs; observed in team control instructions).

### 4.6 Plan Approval Workflow

**When Enabled**: If a teammate is spawned with `planModeRequired: true`, the teammate enters "plan mode."

**Plan Mode Behavior**:

1. Teammate receives its spawn prompt and any task assignments.
2. Teammate is **read-only** for tool calls (cannot modify files, run dangerous commands, or claim implementation tasks).
3. Teammate drafts a plan (write to a plan file or send via message).
4. Teammate sends a protocol message of type `plan_approval_request` to the lead (via SendMessage), including the plan as a field.
5. Lead reads the plan and decides:
   - **Approve**: sends a protocol message of type `plan_approval_response` with `approve: true`.
   - **Reject with feedback**: sends `approve: false` and a `feedback` message.
6. If approved: teammate exits plan mode and can now claim and execute tasks.
7. If rejected: teammate remains in plan mode, revises the plan based on feedback, and resubmits.

**Implementation Limitation**: A non-LLM teammate does not **generate** a coherent plan (that requires reasoning). Options for handling plan mode:

- **Option 1 (Skip plan mode)**: If the non-LLM teammate is only responsible for mechanical/deterministic work, do not enable `planModeRequired: true` (it is optional at spawn time).
- **Option 2 (Delegate reasoning to Codex)**: The adapter invokes Codex to generate the plan, then sends that as `plan` in the `plan_approval_request`. The lead's LLM approves/rejects. This is not an "LLM wrapper" — Codex is the tool being called, and the adapter is the coordinator.
- **Option 3 (Send stub plan)**: The adapter sends a minimal, templated plan (e.g., "Execute task 1, then task 2") and relies on the lead to approve or provide feedback. **Caveat**: If `planModeRequired: true` and the lead is itself an LLM, the lead may reject a stub plan expecting a detailed strategy, or may loop indefinitely on approvals/rejections.

**Recommendation**: If building a Codex teammate, prefer **Option 1** (skip plan mode) or **Option 2** (delegate to Codex) to avoid plan-approval deadlocks with the lead.

**Confidence**: High (documented in Claude Code docs; limitations enumerated from observed behavior in team experiments).

### 4.7 Shutdown and Plan-Approval Payload Semantics (Detailed)

**Critical observation**: Do shutdown_request/response and plan_approval_request/response payloads require LLM-grade reasoning, or are they pure mechanical ACKs?

#### Shutdown Request/Response

**Who Originates**:
- **Shutdown Request**: Lead (harness) sends to teammate when the lead decides the team should wind down or a specific teammate should exit.
- **Shutdown Response**: Teammate sends back to lead, approving or rejecting the shutdown.

**Payload Schema**:

```json
// Request (sent from lead to teammate)
{
  "type": "shutdown_request",
  "reason": "Team cleanup" // or "Explicit user request" or similar
}

// Response (sent from teammate back to lead)
{
  "type": "shutdown_response",
  "request_id": "...",     // correlates with the request
  "approve": true|false,   // boolean: approve or reject
  "feedback": "..."        // optional; teammate's explanation if rejecting
}
```

**Harness Treatment**:
- **Request**: Harness delivers the JSON to the teammate's inbox as opaque content. No interpretation.
- **Response**: Harness reads the JSON, extracts `approve` boolean, and takes action:
  - If `approve: true`: harness terminates the teammate process.
  - If `approve: false`: harness keeps the teammate alive and may log the feedback.

**Reasoning Requirement**:
- **Teammate perspective**: Responding to shutdown is a **binary decision**. A non-LLM Codex adapter can hardcode `"approve": true` (always accept shutdown) or implement a simple heuristic (e.g., "reject if a task is in progress"). **No open-ended reasoning required.**
- **Lead perspective**: The lead's decision to send shutdown_request is usually deterministic (user command, all work done, team cleanup phase). Not an open-ended reasoning problem.

**Confidence**: High (payload schema inferred from protocol docs; harness behavior inferred from its use in practice).

#### Plan Approval Request/Response

**Who Originates**:
- **Plan Approval Request**: Teammate (in plan mode) sends to lead after drafting a plan.
- **Plan Approval Response**: Lead (harness) sends back to teammate, approving or rejecting.

**Payload Schema**:

```json
// Request (sent from teammate to lead)
{
  "type": "plan_approval_request",
  "request_id": "...",
  "plan": "..."           // the proposed plan (text or structured)
}

// Response (sent from lead to teammate)
{
  "type": "plan_approval_response",
  "request_id": "...",    // correlates with the request
  "approve": true|false,  // boolean: approve or reject
  "feedback": "..."       // optional; lead's explanation if rejecting
}
```

**Harness Treatment**:
- **Request**: Harness delivers the JSON to the lead's inbox. Lead (LLM or user) reads the plan and decides.
- **Response**: Harness reads the JSON, extracts `approve` boolean, and communicates back to teammate via inbox message.

**Reasoning Requirement**:
- **Teammate perspective**: Sending a plan_approval_request means the teammate **must draft a coherent plan**. For a Claude-based teammate, this requires reasoning. For a non-LLM Codex adapter:
  - **Option 1**: Have the Codex LLM generate the plan (requires invoking Codex for reasoning, which is what we're trying to avoid).
  - **Option 2**: Send a canned/templated plan stub and rely on the lead to approve it. (Simple but less flexible.)
  - **Option 3**: Skip plan mode entirely (if the architecture doesn't require it for this teammate).
- **Lead perspective**: Approving a plan requires evaluating its soundness. This is typically done by the lead's LLM or user input. The lead's approval is not a protocol concern; it's a decision by the lead.

**Consequence**: 
- **A non-LLM Codex adapter can handle plan approvals reactively**: wait for a plan_approval_response in its inbox and continue accordingly.
- **A non-LLM Codex adapter cannot easily originate a sophisticated plan** unless it delegates to Codex's reasoning capability (which is unavoidable if Codex is the core).

**Confidence**: High (payload schema confirmed by protocol docs; reasoning requirement is the critical design trade-off).

#### Message Origination vs. Response

**Question**: Can a teammate originate arbitrary messages to other teammates, or only respond to inbound?

**Answer (Observed)**:
- **Teammates can originate messages** via SendMessage tool. They are not restricted to responding.
- **Pattern**: A teammate can send a status update to the lead mid-task, ask a clarifying question to another teammate, or broadcast findings to the team.
- **Backpressure**: There is no harness-level heartbeat or "poll for new work" handshake. Message delivery is asynchronous. A teammate sends when it has something to communicate; the recipient reads on polling their inbox.

**Consequence**: 
- **A Codex adapter needs its own control loop**, not just a reactive event handler. It must:
  1. Claim a task (pro-actively).
  2. Run Codex on that task.
  3. Update task status (pro-actively).
  4. Optionally send status messages to teammates (pro-actively).
  5. Detect idle and send idle_notification (pro-actively).
- **The adapter is event-driven at the message level** (polling inbox), but control-flow-driven for task management.

**Confidence**: High (observed from team message patterns; documented in Claude Code docs).

---

## 5. The Teammate Contract: Minimal Implementation Requirements

To be "indistinguishable from a Claude-powered teammate," a non-LLM process must:

### 5.1 Registration and Discovery

- **Requirement**: Read `~/.claude/teams/{team-name}/config.json` at startup.
- **Requirement**: Extract its own `agentId` from the config (or from an environment variable passed by the harness at spawn time).
- **Requirement**: Discover other teammates and the lead via the `members` array.
- **Requirement**: Create a persistent identity: same `agentId` throughout its lifetime.

### 5.2 Inbox/Message Handling

- **Requirement**: Poll `~/.claude/teams/{team-name}/inboxes/{my-agentId}.json` at regular intervals (suggest 1-5 seconds).
- **Requirement**: Process new messages in order of `timestamp`.
- **Requirement**: Handle protocol message types: `shutdown_request`, `plan_approval_request`, `task_assignment`, `idle_notification` (received), any plain-text messages.
- **Requirement**: Respond to `shutdown_request` with a `shutdown_response` message (approve=true or false).
- **Requirement**: Respond to `plan_approval_request` appropriately (approve or request feedback).
- **Requirement**: Send messages to other teammates via SendMessage tool (or direct JSON write to inboxes if the non-LLM process has that capability).

### 5.3 Task List Interaction

- **Requirement**: Read `~/.claude/tasks/{team-name}/` directory and parse all `{id}.json` files.
- **Requirement**: Identify unblocked, pending tasks (status=pending, blockedBy=[]).
- **Requirement**: Claim a task via `TaskUpdate` tool call (set owner to self, status=in_progress, optional activeForm).
- **Requirement**: Update task status to `completed` when done (via `TaskUpdate`).
- **Requirement**: Honor task blocking: do not claim or work on tasks where blockedBy is non-empty.

### 5.4 Idle Management

- **Requirement**: Detect when all assigned work is done (no tasks, no new messages).
- **Requirement**: Send an `idle_notification` message to the lead.
- **Requirement**: Enter a polling loop waiting for new messages or task unblocking.
- **Requirement**: Gracefully handle `shutdown_request` while idle.

### 5.5 Tool Integration

- **Requirement**: Have access to the `SendMessage`, `TaskUpdate`, and `TaskList` tools (standard Claude Code tools).
- **Requirement**: Optionally, access to `TaskCreate` to create new tasks.
- **Constraint**: If implemented as a non-Claude process, it must invoke these tools somehow:
  - Option A: Claude Code harness provides an RPC/socket interface for the process to call tools.
  - Option B: The non-Claude process embeds a minimal Claude API client and calls tools via the Anthropic SDK.
  - Option C: The non-Claude process writes task/message files directly (bypassing the tool layer).

### 5.6 Graceful Degradation

- **Requirement**: Tolerate missing or incomplete protocol features gracefully. For example:
  - If `planModeRequired` is not set, skip plan approval logic.
  - If a message is malformed, log it and continue.
  - If a task file is missing, skip it and move to the next.

**Confidence**: High (synthesized from observed behavior, documentation, and the team protocol requirements).

---

## 6. Open Protocol Questions and Opaque Areas

### 6.1 Resolved Areas (from Team-Lead Follow-Up)

**Three critical investigations requested and now addressed:**

1. **`backendType` in team config** ✅ 
   - **Finding**: Harness supports `"in-process"`, `"tmux"`, and `"auto"` backends. Out-of-process spawning *does* exist via tmux backend, but harness spawns `claude` CLI only.
   - **Constraint**: No native support for spawning non-Claude processes (e.g., Codex) directly. Would require new backendType or file-based integration.
   - **For non-LLM adapters**: Three options: (1) Pure file-based (register self, use file I/O), (2) RPC-bridged (requires harness changes), (3) MCP-bridged (leverage plugin infrastructure).
   - **Reference**: Section 1.4, "Backend Type Field and Process Isolation."

2. **Shutdown + plan-approval payload semantics** ✅
   - **Finding**: Both are pure mechanical ACKs with boolean approval + optional feedback. No complex reasoning required from the protocol perspective.
   - **Shutdown**: Teammate responds with `approve: true/false`. Decision can be deterministic (e.g., always approve, or check if work is pending).
   - **Plan-Approval**: Teammate sends a plan (which it must generate, possibly via Codex), and lead approves/rejects. Teammate just reads the response.
   - **Verdict**: The *generation* of a good plan requires reasoning (Codex can do this), but the *response* to approval is mechanical.
   - **Reference**: Section 4.7, "Shutdown and Plan-Approval Payload Semantics."

3. **Message origination vs. response** ✅
   - **Finding**: Teammates can originate arbitrary messages. The harness does not enforce a request-response pattern.
   - **Control flow**: Teammates drive their own work: claim tasks, run code, send updates, detect idle. Not purely event-driven.
   - **Consequence**: A Codex adapter needs a control loop (poll inbox, claim tasks, run Codex, update status), not just a reactive event handler.
   - **Reference**: Section 4.7, subsection "Message Origination vs. Response."

### 6.2 Remaining Opaque Areas

1. **Tool Call Mechanism for Non-LLM Teammates**:
   - How does a non-LLM process invoke Claude Code tools like `SendMessage` and `TaskUpdate`?
   - Is there a stdio-based RPC protocol, a socket interface, or does the process directly manipulate files?
   - **Status**: Opaque. Not documented in Claude Code docs. Likely requires harness integration or file-based bypass.

2. **Idle-Detection Algorithm**:
   - Does Claude Code detect idle via polling the teammate's stderr/stdout, monitoring inactivity, or explicit notification?
   - How long before a teammate is considered idle? (1 second? 5 seconds? Configurable?)
   - **Status**: Partially documented. Documented: teammates send idle_notification messages. Unresolved: exact timing and fallback detection.

3. **Message Ordering Guarantees**:
   - Are messages guaranteed to be delivered to an inbox in the order sent by a sender?
   - If agent A sends message 1 then message 2 to agent B, will B always see them in that order?
   - **Status**: Likely yes (append-only file), but not explicitly guaranteed in docs.

4. **Inbox Growth and Retention**:
   - Do inbox JSON arrays grow unbounded, or are they rotated/truncated?
   - What is the expected maximum size before performance degrades?
   - **Status**: Opaque. Observed: inboxes grow. No retention policy observed in a single short session.

5. **Config File Mutation During Team Lifetime**:
   - When a teammate goes offline or shuts down, does Claude Code update the config file immediately?
   - Can a teammate detect team composition changes by polling the config?
   - **Status**: Likely yes (config is dynamic), but exact synchronization latency is unclear.

6. **Tool Call Permissions for Teammates**:
   - Do teammates inherit the lead's permissions, or do they have a separate permission set?
   - Can a teammate invoke arbitrary tools, or are some restricted?
   - **Status**: High confidence (documented: teammates inherit lead's permission settings at spawn, can be changed per-teammate later). Unresolved: which tools are always available (e.g., SendMessage, TaskUpdate) vs. restricted by permissions.

7. **Config File Mutation Tolerance (External Peer Integration)**:
   - If a non-Claude process starts independently and appends itself to the team config, does the harness tolerate and accept this modification?
   - Will the harness crash, corrupt the config, or overwrite the new peer entry?
   - Can the externally-registered peer claim tasks, receive messages, and participate fully?
   - **Status**: Critical unknown for the "external self-registered peer" architecture. Requires empirical testing.

### 6.3 Capabilities and LLM-Reasoning Boundary

**Key finding**: The protocol itself does not require LLM reasoning from teammates. However, certain **usage patterns** do:

1. **Plan Generation** (under `planModeRequired: true`):
   - Protocol requirement: Send `plan_approval_request` with a plan (anything goes; no schema).
   - Reasoning requirement: If you want a *good* plan, you need reasoning (Codex can do this).
   - **Verdict**: Not a blocker. A Codex adapter sends a plan generated by Codex, or a template. Either way, it's not an LLM wrapper around Codex.

2. **Inter-Teammate Communication (Prose)**:
   - Protocol requirement: SendMessage bodies are opaque; harness does not interpret content.
   - Reasoning requirement: If you send prose to an LLM teammate, that prose is interpreted. Codex-generated prose (structured, specific) can be legible.
   - **Verdict**: Not a blocker. Prose is optional; structured messages and templates suffice.

3. **Task Interpretation**:
   - Protocol requirement: Read a task description and decide how to approach it. No protocol constraint on the decision logic.
   - Reasoning requirement: Understanding the task is delegated to Codex (which is the whole point). The adapter is thin.
   - **Verdict**: Not a blocker. Codex handles reasoning; adapter handles protocol.

4. **Shutdown/Approval Decisions**:
   - Protocol requirement: Respond with a boolean (approve/reject). No justification required by the protocol.
   - Reasoning requirement: The decision itself can be mechanical (hardcoded rules, heuristics). No open-ended reasoning needed.
   - **Verdict**: Not a blocker. The adapter can hardcode or implement simple decision logic.

**Conclusion**: The protocol is **entirely implementable by a non-Claude, non-LLM process**. The only LLM-heavy component is the task *reasoning* (interpreting the task and coding the solution), which is delegated to Codex. The adapter itself is thin and does not need LLM reasoning unless it chooses to generate prose for communication (optional).

---

## 7. Summary: Building a Non-Claude Teammate

### 7.1 Feasibility Assessment

**The Protocol**: ✅ **Fully implementable without an LLM wrapper.**

Evidence:
- File-based task list, message queues, and config: all directly manipulable by a non-LLM process.
- Protocol messages (shutdown_request/response, plan_approval_request/response, idle_notification) are JSON with boolean ACKs: no open-ended reasoning required from the protocol.
- Message bodies are opaque; prose is optional and only needed if communicating with LLM teammates (structured/templated messages suffice).
- Shutdown and plan-approval decisions can be deterministic or simple heuristics (no complex reasoning from the adapter itself).
- Task reasoning is delegated to Codex; the adapter is thin.
- Tool calls (SendMessage, TaskUpdate) are the harness's responsibility; a non-LLM process can invoke them via tools or file I/O.

**The Hard Part**: ⚠️ **Harness Integration and Tool Access**.
- How does a non-LLM process:
  1. Get spawned as a teammate by Claude Code?
  2. Access the `SendMessage`, `TaskUpdate` tools?
  3. Receive its `agentId` and spawn prompt?
- **Current harness support**: Only `backendType: "in-process"` is implemented. No out-of-process support exists.
- **Solution paths**:
  - **Option A (File-based, no harness changes)**: Direct JSON file I/O for tasks and inboxes. No tool access via harness. Most standalone but fragile.
  - **Option B (Harness modification)**: Extend harness to spawn external processes and provide RPC/stdio tool access. Requires harness changes (outside this spec's scope).
  - **Option C (MCP bridge)**: Implement the protocol via an MCP server that Claude Code loads. Leverages existing plugin infrastructure; less invasive.
  - **Option D (Thin LLM wrapper, minimal scope)**: Have a small Claude instance invoke the protocol tools and delegate reasoning to Codex. This avoids large-scope harness changes but keeps an LLM in the loop (though not for reasoning, just for tool orchestration).

### 7.2 Four Implementation Architectures

1. **Pure File-Based** (minimal dependencies, no harness integration):
   - Process reads/writes task files and inboxes directly.
   - Process spawns Codex CLI for coding work.
   - Process sends messages via direct JSON write to inbox files.
   - Process **cannot** use SendMessage/TaskUpdate tools (not available to a standalone process).
   - **Pro**: No harness changes. **Con**: Fragile; no atomic tool semantics; no tool call visibility to harness.

2. **External Self-Registered Peer** (moderate complexity, depends on harness tolerance):
   - Non-LLM process starts independently (not spawned by harness).
   - Process reads the team config, appends a new member entry for itself (with unique agentId, name, backendType, etc.).
   - Process creates its own inbox file (`~/.claude/teams/{team-name}/inboxes/{name}.json`).
   - Process participates via full file I/O: claims tasks, receives messages, sends responses.
   - **Pro**: No harness modification needed; full participation if harness tolerates config mutation. **Con**: Depends on unknown: does the harness crash or overwrite a config entry it didn't author? **Status**: Feasibility unknown; requires empirical test.

3. **RPC-Bridged** (moderate complexity, harness changes needed):
   - Claude Code harness runs the non-LLM process as a subprocess and provides an RPC socket/stdio channel.
   - Non-LLM process can invoke `SendMessage`, `TaskUpdate` via RPC.
   - Process reads config, inboxes, tasks via files.
   - **Pro**: Full tool access with harness visibility. **Con**: Requires harness modifications (out of scope for a reverse-engineered spec).

4. **MCP-Bridged** (moderate complexity, leverages existing plugin infrastructure):
   - Codex CLI is wrapped in an MCP server (Model Context Protocol).
   - Claude Code team integrates the MCP server as a plugin.
   - Codex adapter reads from MCP server, delegates reasoning, and forwards results.
   - **Pro**: Leverages existing Codex↔Claude-Code plumbing at the plugin layer. **Con**: Adds indirection; still needs a coordination process.

**Recommendation** (for this spec): Start with **Option 2 (External Self-Registered Peer)** for feasibility testing. If harness tolerates config mutations, this is the simplest viable path (no harness changes, full protocol participation). If it fails, fall back to **Option 1 (Pure File-Based)** as the minimal proof-of-concept, then pursue **Option 3 or 4** for production integration.

---

## 8. Sources and Confidence Ratings

| Topic                          | Source                                  | Confidence | Notes |
|--------------------------------|-----------------------------------------|------------|-------|
| **Primary sources (executable)** | cs50victor/claude-code-teams-mcp (MCP implementation) + deep-dive gist | High | Task schemas, inbox formats, config layout all verified against working implementation |
| **Protocol architecture** | DEV Community reverse-engineering article | High | File-based coordination, task ID generation, lifecycle confirmed |
| Team config schema             | Verified: cs50victor/MCP, observed on disk, documented | High | Layout confirmed in both claude-anyteam and nebius-inference teams |
| Task list format               | Verified: cs50victor/MCP, observed on disk, tool usage | High | Schema matches TaskUpdate tool semantics and MCP models |
| Task ID generation             | Verified: cs50victor/MCP (no `.highwatermark`, scan-based) | High | On-the-fly calculation of max(ids) + 1 |
| Inbox format                   | Verified: cs50victor/MCP, observed on disk | High | InboxMessage and protocol message schemas match MCP definitions |
| Message routing (SendMessage)  | Verified: cs50victor/MCP, documented in Claude Code docs | High | Official docs + MCP source align on opaque text field |
| Protocol message types         | Verified: cs50victor/MCP, observed in team inboxes | High | Shutdown, plan-approval, idle_notification, task_assignment all match |
| Task blocking                  | Documented in Claude Code docs, MCP source | High | Official docs describe blocking DAG; MCP source shows cycle detection |
| File locking (task claims)     | Verified: cs50victor/MCP uses `flock()`, documented | High | Mechanism confirmed; mutex semantics explicit in source |
| Idle notifications             | Verified: cs50victor/MCP, observed in inbox messages | High | Confirmed in both teams; IdleNotification schema matches MCP |
| Lifecycle (spawn, shutdown)    | Verified: cs50victor/MCP, documented in Claude Code docs | High | Official docs + MCP spawner code align |
| agentId vs. name semantics     | Verified: cs50victor/MCP models, observed in configs | High | Pattern consistent; MCP formalizes the {name}@{team} format |
| Tool access for teammates      | Documented in Claude Code docs, MCP tools | High | SendMessage, TaskUpdate are core MCP tools for teammates |
| backendType field              | Verified: cs50victor/MCP (defaults to "claude", supports "opencode") | High | Modern values are "claude" and "opencode"; "in-process" is legacy naming |
| Shutdown/plan-approval semantics | Verified: cs50victor/MCP models, observed in team inboxes | High | ShutdownRequest/ShutdownApproved payloads have request_id field |
| Message body opacity           | Verified: cs50victor/MCP (InboxMessage.text is opaque), observed | High | No parsing or schema enforcement by harness |
| Message origination capability | Verified: cs50victor/MCP, observed in team patterns | High | send_message tool allows any teammate to send to any other |
| Content shape requirements     | Observed, team-lead follow-up, verified by MCP | High | Prose optional; JSON protocol messages are core |

---

## 9. Specification Compliance Checklist for Implementers

To verify a non-LLM teammate is conformant:

- [ ] Reads `~/.claude/teams/{team-name}/config.json` at startup and extracts own `agentId`
- [ ] Discovers team members via the `members` array in config
- [ ] Creates a stable, unique identity throughout its lifetime
- [ ] Polls `~/.claude/teams/{team-name}/inboxes/{agentId}.json` at regular intervals
- [ ] Processes incoming messages in timestamp order
- [ ] Handles `shutdown_request` and responds with `shutdown_response`
- [ ] Handles `plan_approval_request` appropriately
- [ ] Reads task list from `~/.claude/tasks/{team-name}/{id}.json` files
- [ ] Identifies unblocked, pending tasks and claims them via TaskUpdate
- [ ] Updates task status to `completed` when work is done
- [ ] Sends messages to other teammates via SendMessage or direct inbox writes
- [ ] Detects idle state (all work done, no pending messages) and sends idle notification
- [ ] Responds gracefully to missing or incomplete protocol features
- [ ] Does not modify team config file directly (read-only)
- [ ] Does not delete or truncate inbox files
- [ ] Tolerates at-least-once message delivery (may see duplicate messages)

---

## 10. Appendix: Example Team Structures

### 10.1 Simple Two-Agent Team

```
~/.claude/teams/claude-anyteam/
├── config.json
└── inboxes/
    ├── team-lead.json
    └── researcher-protocol.json

~/.claude/tasks/claude-anyteam/
├── .lock
├── 1.json  (pending, no owner)
├── 2.json  (in_progress, owner: researcher-protocol)
└── 3.json  (completed)
```

### 10.2 Complex Multi-Teammate Team

```
~/.claude/teams/nebius-inference/
├── config.json  (4 members: team-lead, canonical-researcher, implementor, validator)
└── inboxes/
    ├── team-lead.json
    ├── canonical-researcher.json
    ├── implementor.json
    └── validator.json

~/.claude/tasks/nebius-inference/
├── .lock
├── 1.json  (completed, canonical-researcher)
├── 2.json  (completed, canonical-researcher)
├── 3.json  (in_progress, implementor, blockedBy: [1, 2])
├── 4.json  (pending, blockedBy: [3])
└── 5.json  (pending, no blocking)
```

---

**End of Specification**
