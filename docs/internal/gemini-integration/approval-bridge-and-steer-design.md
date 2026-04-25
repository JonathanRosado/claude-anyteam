# Gemini ACP approval bridge and next-turn steer design

Date: 2026-04-24

Scope: design only. This document covers two ACP control-flow extensions for the Gemini adapter:

- **P3:** bridge ACP `session/request_permission` to `team-lead` for non-`trusted` Gemini trust modes.
- **P4:** approximate Codex `turn/steer` by consuming team-lead steer messages at the next ACP `session/prompt` boundary.

## Current state summary

The ACP backend currently maps AnyTeam Gemini trust modes as follows:

- `trusted` -> ACP `yolo`; `GeminiAcpClient.handle_server_request()` answers `session/request_permission` with `allow_once`.
- `default` -> ACP `default`; permission requests are answered with `cancel`, `permission_blocked` is recorded, and the claimed task is marked blocked.
- `plan` -> ACP `plan`; same fail-closed behavior as `default`.

The ACP JSON-RPC transport has a stdout reader thread. The reader calls `JsonRpcStdioClient._dispatch()`, which calls `GeminiAcpClient.handle_server_request()` synchronously for server-originated JSON-RPC requests such as `session/request_permission`. The foreground task thread is blocked in `client.session_prompt(...)`, waiting on the pending JSON-RPC response event.

The wrapper MCP exposed to the model currently has `send_message`, `task_update`, `task_create`, `read_inbox`, `task_list`, and `read_config`. It has no blocking wait/notification primitive for “next message of type X”.

## P3: async approval bridge for non-`trusted` trust modes

### Goal

For `CLAUDE_ANYTEAM_GEMINI_TRUST=default` or `plan`, do not fail closed immediately. Instead:

1. Receive ACP `session/request_permission`.
2. Send a structured `permission_request` message to `team-lead`.
3. Wait for a matching `permission_response` for a bounded time.
4. Return the corresponding ACP permission result.
5. If no response arrives, deny, mark the task blocked, and keep the adapter safe by default.

### Recommendation: block the ACP reader thread in v1

Use the simplest correct implementation: **it is acceptable to block the ACP stdout reader thread while waiting for lead approval**, with a bounded timeout.

Rationale:

- ACP permission requests are synchronous: Gemini is asking the client to choose an option before it can continue the tool/action. While Gemini is waiting for the `session/request_permission` response, the useful next ACP event is not expected until the client answers.
- The blocked wait does not need the ACP reader to make progress. The lead response arrives through AnyTeam inbox files, read via `protocol_io`, not through Gemini stdout.
- The current adapter executes one ACP `session/prompt` at a time. There is no supported concurrent prompt/request workload that requires the reader to continue dispatching unrelated JSON-RPC traffic during a permission decision.
- A future/queue architecture would be more complex and only buys value if ACP starts issuing nested independent requests or the adapter supports multiple simultaneous prompts per Gemini process.

Guardrails for this choice:

- The approval wait timeout must be **strictly less than** the enclosing `session_prompt(timeout_s=...)` timeout.
- Log when entering and leaving the wait so a stalled approval is diagnosable.
- Keep `handle_server_request()` narrow: only `session/request_permission` may block; unknown server requests should preserve existing behavior.
- Do not set synthetic errors on every pending request as the current fail-closed path does. For bridge mode, the pending `session/prompt` should remain pending until Gemini receives the permission answer and either continues or returns normally.

If a later implementation needs to avoid blocking the reader, use a two-thread handoff: `handle_server_request()` creates a `PermissionRequestContext`, starts or notifies an approval worker, waits on a per-request `Event`, and then returns the result. That still blocks the handler for the server request, but isolates inbox polling from the reader. A fully non-blocking JSON-RPC server-request response API is not necessary for v1.

### Permission request extraction

Add helpers near `permission_request_label()` in `src/claude_anyteam/backends/gemini/acp_client.py` or a small sibling module:

- `permission_tool_name(params) -> str`
- `permission_tool_args(params) -> dict | str | None`
- `redact_permission_tool_args(value) -> redacted_value`

Expected ACP shapes should be handled defensively. Look for tool metadata in both top-level fields and nested `toolCall` / `tool_call` fields. Preserve the raw label fallback for logging.

Redaction policy:

- Keep command names, executable names, file paths under the workspace, MCP tool names, and high-level action descriptions.
- Redact obvious secrets by key name: `api_key`, `apikey`, `token`, `secret`, `password`, `credential`, `authorization`, `cookie`, `private_key`, `access_token`, `refresh_token`.
- Redact environment values except allowlisted non-secret names needed for review, such as `PWD`, `HOME` only if already synthetic, and AnyTeam identity variables.
- Truncate large strings and collections. Suggested caps: 2 KiB per string, 16 KiB total serialized `tool_args` in the lead message.
- If redaction/truncation occurs, include `_redactions` metadata with counts/reasons.

### Lead message protocol

Use JSON carried in the existing prose message body, matching the repository’s existing pattern for adapter-owned protocol messages.

#### Outbound `permission_request`

Send to `team-lead` with `summary=f"permission_request:{request_id}"`.

Shape:

```json
{
  "type": "permission_request",
  "request_id": "perm-<uuid>",
  "tool_name": "shell_command",
  "tool_args": {
    "cmd": "python scripts/update.py",
    "cwd": "/repo",
    "_redactions": [{"path": "env.API_TOKEN", "reason": "secret-like key"}]
  },
  "task_id": "42",
  "teammate_name": "gemini-researcher",
  "trust_mode": "default",
  "label": "Run shell command",
  "timestamp": "2026-04-24T21:30:00.000Z"
}
```

Required fields for the lead contract:

- `type: "permission_request"`
- `request_id`
- `tool_name`
- `tool_args`
- `task_id`
- `teammate_name`

Recommended extra fields:

- `trust_mode`
- `label`
- `timestamp`
- `session_id` if available to the caller
- `schema_version: 1`

#### Inbound `permission_response`

The lead replies by sending a normal DM to the teammate. Clean lead-side shape:

```json
{
  "type": "permission_response",
  "request_id": "perm-<uuid>",
  "decision": "allow_once",
  "reason": "Command only reads repo files and writes the requested doc."
}
```

Allowed `decision` values:

- `allow_once`: approve this ACP request only.
- `allow_session`: approve this request and subsequent matching permission requests for the current Gemini ACP session/task, according to the adapter cache policy below.
- `deny`: deny the request.

Lead ergonomic primitive:

```text
SendMessage(
  to="<teammate_name>",
  body='{"type":"permission_response","request_id":"perm-...","decision":"allow_once","reason":"..."}',
  summary="permission_response:perm-..."
)
```

The adapter should accept only messages from `team-lead` for permission responses. Malformed or non-matching responses should be ignored for the approval wait and then processed normally, if still unread, by the outer loop. Because current `read_inbox(..., mark_as_read=True)` marks all unread messages read, prefer a response-specific inbox helper as described below.

### ACP response mapping

ACP empirical results show the selected outcome uses `optionId` strings. Map lead decisions to ACP results as follows:

- `allow_once` -> `{"outcome": {"outcome": "selected", "optionId": "allow_once"}}`
- `allow_session` -> `{"outcome": {"outcome": "selected", "optionId": "allow_always"}}` if Gemini advertises such an option in the request; otherwise return `allow_once` and cache the approval adapter-side for matching requests in the same task/session.
- `deny` -> `{"outcome": {"outcome": "selected", "optionId": "cancel"}}`
- timeout -> same as `deny`

Implementation detail: inspect the `session/request_permission` params for advertised options before choosing `allow_always`. If there is no advertised session-level option, never invent one on the ACP wire.

### `allow_session` cache policy

Keep `allow_session` scoped narrowly:

- Key by `(task_id, acp_session_id, normalized_tool_name, normalized_tool_args_fingerprint)`.
- Fingerprint the **redacted but structurally stable** args plus the unredacted safe identifiers needed to distinguish commands/paths.
- Clear cache when the task completes, blocks, or `state.gemini_session_id` is dropped.
- Do not persist this cache across adapter restarts.

Before sending a new lead request, check the cache. If a matching `allow_session` entry exists, return the ACP approval immediately and log `gemini.permission.cached_allow_session`.

### Timeout policy

Default timeout: **300 seconds**.

Configuration:

- Environment variable: `CLAUDE_ANYTEAM_GEMINI_APPROVAL_TIMEOUT`
- Parse as seconds; allow integer or float.
- Invalid values should log a warning and fall back to 300 seconds.
- Minimum suggested clamp: 1 second.
- Maximum suggested clamp: no more than the current `session_prompt` timeout minus a small safety margin. If the configured timeout exceeds that, clamp and log.

On timeout:

1. Return ACP `cancel` for the permission request.
2. Record permission-denied details on the client result, e.g. `permission_blocked = {"reason": "approval_timeout", ...}`.
3. Cause `acp.run()` to return a `permission_blocked` result after the prompt settles or after a short cancel wait.
4. In `backends/gemini/loop.py`, `_execute_task()` should mark the task blocked with a clear reason, for example: `Gemini permission request timed out after 300s: <tool_name>/<label>`.
5. Send `task_blocked` to `team-lead` using the existing path.

### Inbox polling vs notification

There is no current wrapper MCP primitive for “wait for next message of type X”. Do **not** overload `task_update` for this; task state is the wrong transport for a point-to-point approval response.

Recommended v1 implementation: add an **adapter-internal** helper, not a model-facing Gemini MCP tool:

```python
def wait_for_permission_response(
    *,
    team: str,
    teammate_name: str,
    request_id: str,
    timeout_s: float,
    poll_interval_s: float,
) -> PermissionResponse | None:
    ...
```

The helper should poll the teammate inbox directly using cs50victor/protocol I/O. Important behavior:

- Match only JSON messages with `type == "permission_response"` and exact `request_id`.
- Require `from == "team-lead"` / message sender `team-lead`.
- Validate `decision` against the schema.
- Preserve unrelated unread messages if possible.

The last point is important because the current public `read_own_inbox(... mark_as_read=True)` drains all unread messages. For this helper, implement one of these options:

1. Best option: add a filtered read/mark helper using cs50victor’s lower-level inbox file lock, marking only the matched response read while leaving unrelated messages unread.
2. Acceptable short-term option: read with `mark_as_read=False`, remember message IDs/timestamps already seen during this wait, and after a matched response is found let the outer loop drain the inbox on the next poll. This can re-see the response later, so `_handle_message()` must treat `permission_response` as a known no-op outside an active wait.

If the project wants a reusable primitive, add a narrow protocol helper named `wait_for_permission_response`; do not expose it in `EXPOSED_TOOLS` unless a model genuinely needs to call it. The ACP bridge is adapter-owned control flow, so exposing the wait tool to Gemini is unnecessary.

### Schema files to add

Add two JSON schemas. These should be implementation artifacts, not generated by the model.

#### `schemas/permission_request.schema.json`

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "AnyTeam permission request",
  "type": "object",
  "additionalProperties": false,
  "required": ["type", "request_id", "tool_name", "tool_args", "task_id", "teammate_name"],
  "properties": {
    "type": {"const": "permission_request"},
    "schema_version": {"type": "integer", "const": 1},
    "request_id": {"type": "string", "minLength": 1},
    "tool_name": {"type": "string", "minLength": 1},
    "tool_args": {
      "description": "Redacted permission arguments. May be an object, array, string, number, boolean, or null depending on ACP payload shape. Secret values must be redacted before serialization."
    },
    "task_id": {"type": "string", "minLength": 1},
    "teammate_name": {"type": "string", "minLength": 1},
    "trust_mode": {"enum": ["default", "plan"]},
    "label": {"type": "string"},
    "session_id": {"type": "string"},
    "timestamp": {"type": "string", "format": "date-time"}
  }
}
```

#### `schemas/permission_response.schema.json`

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "AnyTeam permission response",
  "type": "object",
  "additionalProperties": false,
  "required": ["type", "request_id", "decision"],
  "properties": {
    "type": {"const": "permission_response"},
    "schema_version": {"type": "integer", "const": 1},
    "request_id": {"type": "string", "minLength": 1},
    "decision": {"enum": ["allow_once", "allow_session", "deny"]},
    "reason": {"type": "string"},
    "timestamp": {"type": "string", "format": "date-time"}
  }
}
```

Note: JSON Schema cannot fully express “any JSON value” portably without an explicit `true` schema. If the existing schema tooling accepts boolean schemas, use `"tool_args": true`; otherwise leave `tool_args` with only a description as above or enumerate JSON primitive/object/array types.

### Concrete implementation touch points

No implementation is included here, but an implementer can execute roughly this sequence:

1. Add Pydantic message models in `src/claude_anyteam/messages.py`:
   - `PermissionRequestOut`
   - `PermissionResponseIn`
   - Update `parse_protocol_text()` to recognize `permission_response` and ignore/parse `permission_request` if needed for diagnostics.
2. Add schema files under `schemas/`.
3. Add `protocol_io.send_permission_request_to_lead(...)` and `protocol_io.wait_for_permission_response(...)`.
4. Extend `GeminiAcpClient.__init__` with optional approval context:
   - `team_name`
   - `agent_name`
   - `task_id`
   - `approval_timeout_s`
   - `poll_interval_s`
   - optional `allow_session_cache`
5. In `GeminiAcpClient.handle_server_request()`:
   - Keep `trusted` behavior unchanged.
   - For `default`/`plan`, build and send `permission_request`.
   - Wait for response.
   - Return mapped ACP result.
   - Set `permission_blocked` only for `deny`, timeout, malformed approved response, or send/wait failure.
6. In `backends/gemini/acp.py::run()`:
   - Parse `CLAUDE_ANYTEAM_GEMINI_APPROVAL_TIMEOUT`.
   - Pass approval context into `GeminiAcpClient` when `trust_mode != "trusted"`.
   - Preserve existing `_permission_block_result()` path for denied/timeout cases.
7. In `backends/gemini/loop.py`, ensure `_execute_task()` blocks tasks on permission-denied/timeout results exactly once and clears session/cache state.
8. Add tests around:
   - `trusted` still auto-approves.
   - `default` sends `permission_request` and maps `allow_once`.
   - `allow_session` caches for same task/session/tool fingerprint.
   - `deny` maps to `cancel` and blocks.
   - timeout maps to `cancel` and blocks.
   - unrelated inbox messages are not lost or are safely no-op’d later.

### Failure behavior matrix

| Condition | ACP response | Task outcome | Notes |
| --- | --- | --- | --- |
| Lead replies `allow_once` | approve once | continue | No task state change. |
| Lead replies `allow_session` | approve once or advertised session option | continue | Cache narrowly if ACP lacks session option. |
| Lead replies `deny` | cancel | block task | Include lead reason in block reason. |
| No response before timeout | cancel | block task | Reason: approval timeout. |
| Malformed response | ignore until timeout | block on timeout | Log malformed response. |
| Non-lead response | ignore | unchanged | Only team-lead can approve. |
| Sending request to lead fails | cancel | block task | Fail closed. |

## P4: pseudo turn-steer / next-turn steer

### Goal

Gemini ACP does not provide Codex App Server `turn/steer` semantics. Mid-token-stream steering remains unavailable. The closest safe feature is **next-turn steer**: team-lead sends a structured steer message; the Gemini adapter stores it and injects it into the next `session/prompt` it sends to Gemini.

This is not immediate interruption. It is useful because it gives the lead a way to influence the next task prompt or next retry without relying on generic prose handling.

### Lead message protocol

Lead sends a normal DM to the Gemini teammate:

```json
{
  "type": "steer",
  "message": "Do not modify generated files; only update the design document.",
  "task_id": "42",
  "priority": "normal",
  "timestamp": "2026-04-24T21:35:00.000Z"
}
```

Fields:

- `type: "steer"` required.
- `message` required, non-empty string.
- `task_id` optional. If present, applies only to that task. If absent, applies to the next claimed task.
- `priority` optional: `normal` or `urgent`. This does **not** make steer mid-turn; it is for logging/display only.
- `expires_after_turns` optional, default `1`. V1 should consume steer once.

Only accept `steer` messages from `team-lead`.

### Where to check for queued steer messages

Check in the adapter control loop, not inside the Gemini model:

1. At the top of each `_main_loop()` iteration, the adapter already drains the inbox.
2. Extend `_handle_message()` in `src/claude_anyteam/backends/gemini/loop.py` to recognize `SteerIn`.
3. Store accepted steer messages in `GeminiLoopState`, for example:

```python
queued_steers: list[QueuedSteer] = field(default_factory=list)
```

4. Immediately before `_execute_task()` calls `_backend_run(...)`, collect applicable queued steers for the task.
5. Inject those steers into the prompt for that `session/prompt`.
6. Mark consumed steers as consumed after the prompt is constructed. If the Gemini invocation crashes before writing to stdin, either requeue them or leave them consumed and rely on lead resend; prefer requeue only if construction/send definitely failed before `session_prompt`.

This means the adapter observes steer messages only when it returns to the loop. If a task is already inside a long `session/prompt`, the steer waits until the next turn/task/retry.

### Injection format

Prefer prepending the steer text to the next user prompt as an adapter-owned instruction block, rather than sending a separate `session/prompt` first.

Recommended prompt prefix:

```text
# Team-lead next-turn steer
The following instruction(s) were sent by team-lead after the previous turn boundary. Treat them as higher priority than the task description where they conflict, but do not violate system/developer instructions or repository safety rules.

- [steer_id=<id>; task_id=<task-or-next>] Do not modify generated files; only update the design document.

# Original task prompt
...
```

Why prepend instead of a separate `session/prompt`:

- A separate prompt would create an extra Gemini turn that might elicit a response before the real task, complicating schema-constrained task completion.
- Prepending keeps the steer and task in one final prompt, so `task-complete.schema.json` validation remains attached to the real task turn.
- It mirrors how the current prompt builders already add adapter instructions and output contracts.

If multiple steer messages are queued, preserve chronological inbox order and include all applicable messages. V1 should cap total steer text, for example 8 KiB, and include a truncation note if exceeded.

### Applicability and lifecycle

- `task_id` present and equals current task id: inject.
- `task_id` present and not current task id: keep queued until that task is claimed or until it expires.
- no `task_id`: inject into the next non-ephemeral task prompt and consume.
- Do not inject steer into ephemeral prose replies or plan-generation turns unless the steer explicitly says it applies to that task and the plan flow is the next prompt for that task. Simpler v1: task execution only.
- Clear queued steers on approved shutdown.

### Honest semantics

Document and log this as **next-turn steer**:

- It does not interrupt an in-flight Gemini `session/prompt`.
- It does not steer mid-token-stream.
- It is closer to Codex `turn/steer` than having no steer at all because lead intent is captured and injected automatically at the next prompt boundary.
- It is not equivalent to Codex App Server live steering.

### Concrete implementation touch points

1. Add `SteerIn` in `src/claude_anyteam/messages.py` and parse `type == "steer"`.
2. Add `queued_steers` to `GeminiLoopState` in `src/claude_anyteam/backends/gemini/loop.py`.
3. In `_handle_message()`, accept `SteerIn` from `team-lead`, enqueue it, and log `gemini.steer.queued`.
4. Add helper:

```python
def _steer_prefix_for_task(state: GeminiLoopState, task) -> str:
    ...
```

5. In `_execute_task()`, before appending the output contract, prepend the steer prefix to `prompts.task_prompt(...)` if non-empty.
6. Add tests:
   - lead steer without `task_id` is injected into next task once.
   - lead steer with matching `task_id` is injected.
   - nonmatching `task_id` is retained.
   - non-lead steer is ignored.
   - steer is not described as mid-turn in user-facing docs/logs.

## Combined behavior with approval bridge

If both features are present, they interact cleanly:

- Steer is consumed at `session/prompt` construction time.
- Permission approval can happen later inside that same prompt when Gemini requests an action.
- If the lead denies or times out a permission request, the task is blocked even if steer was applied.
- If the task is retried by the lead as a new task, the lead can send a fresh steer message or rely on task description changes.

## Open implementation choices

- Whether to introduce Pydantic models for `permission_request` and `permission_response` first or validate only with JSON Schema in tests. Recommendation: Pydantic for runtime parse; JSON Schema for contract docs/tests.
- Whether `allow_session` should ever map to ACP `allow_always`. Recommendation: only if the option is explicitly advertised by Gemini in the permission request; otherwise adapter cache.
- Whether inbox filtered marking should be implemented now. Recommendation: implement filtered read/mark if small; otherwise read without marking and make stale permission responses no-op in `_handle_message()`.
