# Gemini ACP runtime implementation audit

Date: 2026-04-24 (local), verified again at 2026-04-25T00:47Z.  
Workspace: `/home/rosado/Projects/codex-teammate`  
Installed Gemini CLI: `/usr/local/bin/gemini`, version `0.39.0`.

## Scope

This audit reviewed the current ACP runtime implementation in:

- `src/claude_anyteam/jsonrpc_stdio.py`
- `src/claude_anyteam/backends/gemini/acp_client.py`
- `src/claude_anyteam/backends/gemini/acp.py`
- `src/claude_anyteam/backends/gemini/loop.py`

Focus areas were subprocess lifecycle, JSON-RPC framing, error handling, session state, tool-use/tool-result translation, prompt-injection surface, and recovery semantics. I also independently reproduced a real ACP round trip against the installed CLI.

## Empirical ACP round trip

A direct probe using `GeminiAcpClient` successfully drove:

1. `gemini --acp`
2. `initialize`
3. `session/new`
4. `session/set_mode` to `yolo`
5. `session/prompt`
6. client shutdown

Observed initialize result:

- `protocolVersion: 1`
- `agentInfo.name: gemini-cli`
- `agentInfo.version: 0.39.0`
- `agentCapabilities.loadSession: true`
- prompt capabilities: image/audio/embedded context
- MCP capabilities: HTTP/SSE

Observed new session:

- session id: `1f88f47d-10b0-495b-9afa-d63c427918d6`
- initial mode: `default`
- initial model: `auto-gemini-3`
- `session/set_mode(..., yolo)` returned successfully

Observed prompt response:

- `stopReason: end_turn`
- update kinds included `available_commands_update`, `agent_thought_chunk`, and `agent_message_chunk`
- final assistant message chunk contained `ACP_OK_20260424`

This confirms that the implemented method names and newline-delimited JSON-RPC transport work on the installed CLI.

## Static audit findings

### 1. Subprocess lifecycle

What is solid:

- `JsonRpcStdioClient.start()` creates independent reader and stderr-drain threads and uses line buffering.
- `close()` closes stdin, terminates the child, escalates to kill, wakes pending requests, and clears pending state.
- `finally: client.close()` in `acp.run()` ensures the ACP child is not left alive on ordinary success or raised exceptions.

Risks / gaps:

- `close()` does not join reader/stderr threads. They are daemon threads, so process exit is not blocked, but tests do not prove clean thread quiescence.
- A request timeout in `JsonRpcStdioClient.request()` removes the pending request and raises a `GeminiAcpError`; `acp.run()` catches it as a generic exception and reports exit code `1`, not timeout code `124`.
- `acp.run()` has an `except subprocess.TimeoutExpired` branch, but the ACP path does not use `subprocess.run`; prompt timeout currently surfaces as `GeminiAcpError`, making that branch effectively dead for request timeouts.
- On prompt timeout there is no `session/cancel` attempt before closing the process. Process termination is enough for local cleanup, but the remote/durable session may retain a partially processed user turn.
- Notifications are only drained after `session_prompt()` returns successfully. If setup or prompt errors occur after some notifications have arrived, the returned `CodexResult.events` may be empty even though the reader received useful diagnostic events.

Recommendation: catch `GeminiAcpError` timeout text or introduce a typed timeout exception from `request()`, map it to exit code `124`, try `session/cancel` when `session_id` is known, drain notifications before returning errors, and optionally join reader threads after process exit.

### 2. JSON-RPC framing correctness

What is solid:

- The implementation matches the empirically observed Gemini ACP framing: one JSON-RPC object per stdout line, not LSP `Content-Length` framing.
- Non-JSON stdout lines are tolerated and bounded in logs, which is the right defensive posture for CLI startup noise.
- Concurrent writes are protected by `_write_lock`.
- Client requests, server notifications, server-originated requests, JSON-RPC errors, and orphan responses are routed separately.
- The ACP wrapper uses empirical method names: `initialize`, `session/new`, `session/load`, `session/prompt`, `session/set_mode`, `session/set_model`; `session/cancel` is correctly sent as a notification.

Risks / gaps:

- Incoming messages are not validated for `jsonrpc == "2.0"`; malformed but object-shaped data can be dispatched.
- Server-originated requests with unhandled methods are logged but not answered. That can intentionally preserve old behavior, but if Gemini adds a required client request, the server may hang waiting for a response.
- `wait_for_notification()` requeues nonmatching notifications at the back of the same queue. This can reorder events and spin on unmatched notifications; it is currently not used by the ACP high-level path.

Recommendation: validate `jsonrpc`, return JSON-RPC method-not-found for unknown server requests if Gemini expects a response, and avoid requeue-based notification matching for future event consumers.

### 3. Error handling and stop reasons

What is solid:

- JSON-RPC error responses become exceptions with code/message/data included.
- Non-`end_turn` stop reasons are converted to adapter failure after a successful response.
- Schema validation failures are propagated as adapter errors.

Risks / gaps:

- `stopReason: cancelled` is treated as a generic failure only after a completed response; this is correct enough for now, but there is no higher-level recovery policy.
- `initialize()` result is not checked for compatible `protocolVersion`, ACP capability support, or authentication requirements; the code proceeds if the request returns any dict.
- Authentication methods are listed by `initialize`, and `authenticate()` exists, but `acp.run()` never attempts authentication or produces a targeted auth error.

Recommendation: explicitly check `initialize.result.protocolVersion == 1`, recognize auth failures distinctly, and add tests for `stopReason` handling.

### 4. Session state and recovery semantics

What is solid:

- `_ensure_session()` tries explicit resume id, persisted live ACP id, and persisted storage id before creating a new session.
- Load failures are logged and recover by creating a fresh session.
- The ACP session id is persisted in adapter state on success.
- The `ephemeral` flag prevents persisted session reuse and persistence for prose/plan-style one-offs.
- On `session/load`, the implementation re-supplies `mcpServers`, which is necessary because MCP process state is not durable.

Risks / gaps:

- `acp_storage_session_id` is read but never refreshed in the ACP success path. `_latest_storage_session_id()` exists but is unused, so fallback to Gemini's storage transcript id may become stale or remain null.
- Recovery is transcript-level only. Live MCP process state, any in-memory tool state, and partially completed tool actions are not recovered.
- Reusing a session after `session/cancel` can preserve cancelled prompt semantics. Prior empirical notes showed cancelled instructions can influence a later turn.

Recommendation: persist both live ACP id and discovered storage id when available; after hard cancel or timeout, prefer a new session unless the user explicitly wants continuation.

### 5. Tool-use / tool-result translation

What is solid:

- `_assistant_text_and_tools()` correctly filters assistant text to `session/update` events with `sessionUpdate == agent_message_chunk` and counts `tool_call` / `tool_call_update` events.
- Existing empirical evidence shows MCP tool results can appear under ACP `tool_call_update.content[*].content.text`, while built-in shell/list-directory tool updates often have empty `content`.

Risks / gaps:

- The ACP adapter does not translate ACP tool updates into the headless stream-json event vocabulary (`tool_use` / `tool_result`). It only returns raw JSON-RPC notifications in `CodexResult.events` and a count.
- Built-in Gemini tool result payloads are not reliably available in ACP events on CLI 0.39.0, so true `tool_result` parity cannot be reconstructed for built-in tools from the current event stream.
- MCP tool result extraction is possible but not implemented. Consumers wanting normalized tool telemetry must parse raw ACP events themselves.

Recommendation: add a small normalization layer that emits internal `tool_use`-like events for `tool_call` and `tool_result`-like events for `tool_call_update` when content is present. Document that built-in tool output may be unavailable and should not be promised as parity until Gemini emits it.

### 6. Prompt-injection and permission surface

What is solid:

- The Gemini prompt tells teammates not to use protocol tools by default and restricts completion to the requested JSON schema.
- The wrapper MCP server runs with explicit teammate identity in environment variables.

Risks / gaps:

- ACP sets session mode to `yolo` when possible.
- `GeminiAcpClient.handle_server_request()` auto-approves every `session/request_permission` with `allow_once`.
- The exposed MCP tools include messaging, task updates, and task creation. A malicious task prompt could instruct Gemini to send misleading messages, create tasks, or perform broad filesystem/shell actions under YOLO mode.
- There is no adapter-side allowlist/denylist policy for Gemini built-in tools in ACP mode beyond Gemini's mode and the broad auto-approval response.

Recommendation: treat ACP Gemini teammates as fully trusted code-execution agents, not sandboxed readers. For untrusted task text, use plan/default mode or add adapter-side permission policy that denies unexpected `session/request_permission` tool requests. Consider narrowing tool exposure per task phase.

## Verification performed

Command-level verification:

```bash
which gemini
# /usr/local/bin/gemini

gemini --version
# 0.39.0

gemini --help
# includes --acp and --experimental-acp
```

Unit tests run:

```bash
uv run pytest \
  tests/test_jsonrpc_stdio.py \
  tests/test_gemini_acp_client.py \
  tests/test_gemini_acp_prompt_flow.py \
  tests/test_gemini_acp_recovery.py \
  tests/test_gemini_acp_cancel.py \
  tests/test_gemini_acp_session_reload.py -q
```

Result: `9 passed in 0.59s`.

## Overall verdict

The ACP runtime is viable for real Gemini CLI round trips and has the right broad architecture: tolerant NDJSON JSON-RPC client, empirical ACP method names, session creation/loading, MCP provisioning per session, assistant text extraction, and durable state persistence. The main issues before relying on it as a production-quality runtime are timeout classification/recovery, lack of normalized tool_use/tool_result translation, over-broad auto-approval/YOLO permission posture, incomplete storage-session persistence, and cancellation semantics that can leave cancelled prompts in the conversation state.
