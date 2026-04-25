# Gemini ACP control surface empirical notes

Date: 2026-04-24. Host CLI: `gemini 0.39.0`. Workspace: `/home/rosado/Projects/codex-teammate` on branch `feat/gemini-integration`.

Scope: operational ACP behaviors that matter for Plan B parity: stdout hygiene, cancel/re-prompt as a possible `turn/steer` substitute, restart recovery with `session/load`, and subprocess failure handling.

Artifacts from the final probe run are in `/tmp/acp_suppression.json`, `/tmp/acp_continuity.json`, and `/tmp/acp_load.json` on this host.

## Executive summary

- **ACP method names are the spec names.** Use `initialize`, `session/new`, `session/prompt`, `session/load`, `session/set_mode`. `session/cancel` is accepted only as a JSON-RPC notification, not as a request.
- **Current final probe did not reproduce startup stdout pollution.** With `gemini 0.39.0` in this shell, the first stdout line for `gemini --acp` was the `initialize` JSON-RPC response under baseline, inherited env, the requested env-var variants, and accepted flag variants. Because prior local notes and the task report show non-JSON preambles are possible, the adapter should still be tolerant and skip bounded non-JSON stdout lines.
- **No plausible suppression knob should be relied on.** `--quiet`, `--silent`, and `--no-color` are unknown flags. `GEMINI_LOG=`, `GEMINI_QUIET=1`, `GEMINI_DEBUG=0`, `GEMINI_DEBUG=`, `NO_COLOR=1`, and `CI=1` did not change ACP framing in the final probe. Settings keys found in docs (`general.debugKeystrokeLogging`, `ui.debugRainbow`, `telemetry.enabled`) did not provide a stdout-cleanliness control.
- **`session/cancel` is not Codex `turn/steer`.** Sending the notification during a prompt makes the outstanding `session/prompt` return quickly with `stopReason: "cancelled"`, but the cancelled user request can remain semantically active. In the final run, a cancelled “count to 500” instruction was completed during the next follow-up prompt before the model answered the follow-up.
- **Same-session continuity works, including after cancel.** A seed token from an earlier completed turn was remembered after cancel/re-prompt, but cancelled prompt remnants were also visible. This is continuity, not clean steering.
- **`session/load` recovery worked with the ACP `sessionId` returned by `session/new` in the final run.** After two turns and `SIGKILL` of the ACP subprocess, a fresh `gemini --acp` process loaded the captured id, streamed transcript history as `session/update` events, and answered using preserved context.
- **Recovery is transcript-level, not live-process-state-level.** Transcript/user/model/tool-call history is preserved. Live MCP server process state and any in-memory adapter state are not; the adapter must re-supply `mcpServers` on every `session/load` and expect MCP processes to restart.

## Probe shape

ACP transport is newline-delimited JSON-RPC 2.0 over stdio. The successful initialize request used:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {
    "protocolVersion": 1,
    "clientInfo": {"name": "acp-control-probe", "version": "0.1"},
    "clientCapabilities": {
      "auth": {"terminal": false},
      "fs": {"readTextFile": false, "writeTextFile": false},
      "terminal": false
    }
  }
}
```

Observed `initialize.result` included:

- `protocolVersion: 1`
- `agentInfo.name: "gemini-cli"`, `agentInfo.version: "0.39.0"`
- `agentCapabilities.loadSession: true`
- prompt capabilities for image/audio/embedded context
- MCP capabilities for HTTP and SSE

New sessions use:

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "session/new",
  "params": {
    "cwd": "/home/rosado/Projects/codex-teammate",
    "mcpServers": []
  }
}
```

Prompts use content blocks:

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "session/prompt",
  "params": {
    "sessionId": "...",
    "prompt": [{"type": "text", "text": "Remember token BLUE-OTTER-731."}]
  }
}
```

Cancel uses a notification; there is intentionally no `id` and no direct response:

```json
{"jsonrpc":"2.0","method":"session/cancel","params":{"sessionId":"..."}}
```

If sent as a request with an `id`, Gemini returns method-not-found:

```json
{"code":-32601,"message":"\"Method not found\": session/cancel"}
```

## 1. Stdout pollution mitigation

### Final probe results

Each case started a new `gemini --acp`, sent `initialize`, waited for the matching response, and recorded non-JSON stdout lines before the response.

| Case | Result |
| --- | --- |
| Baseline with cleaned env | Clean stdout; first stdout line was JSON-RPC. |
| Inherited env (`NO_COLOR=1`, `CODEX_CI=1`) | Clean stdout. |
| `GEMINI_LOG=` | Clean stdout. |
| `GEMINI_QUIET=1` | Clean stdout. |
| `GEMINI_DEBUG=0` | Clean stdout. |
| `GEMINI_DEBUG=` | Clean stdout. |
| `NO_COLOR=1` | Clean stdout. |
| `CI=1` | Clean stdout. |
| `DEBUG=` | Clean stdout. |
| `DEBUG_MODE=0` | Clean stdout. |
| `GOOGLE_SDK_NODE_LOGGING=` | Clean stdout. |
| `GOOGLE_SDK_NODE_LOGGING=*` | Clean stdout in this run. |
| `--debug=false` | Accepted; clean stdout. |
| `--debug 0` | Accepted by yargs as a positional-looking value; clean stdout. Avoid because semantics are unclear. |
| `--raw-output --accept-raw-output-risk` | Accepted; clean stdout. Not relevant to ACP control. |
| `--quiet` | Exit `1`; stderr begins `Unknown argument: quiet`. |
| `--silent` | Exit `1`; stderr begins `Unknown argument: silent`. |
| `--no-color` | Exit `1`; yargs reports `Unknown argument: color`. Use `NO_COLOR=1` instead if color is a concern. |

One oddity: `DEBUG=0` caused no response within the 5s suppression-test timeout in one run, with no stdout/stderr. Do not set `DEBUG` for the ACP subprocess.

### Settings.json checks

The installed docs expose debug/UI/telemetry settings, but not an ACP stdout logging switch. I tested project-local `.gemini/settings.json` candidates and removed the file afterward:

| Settings payload | Result |
| --- | --- |
| `{"general":{"debugKeystrokeLogging":false}}` | Clean stdout. |
| `{"ui":{"debugRainbow":false}}` | Clean stdout. |
| `{"telemetry":{"enabled":false}}` | Clean stdout. |
| `{"hooks":{"suppressOutput":true}}` | Clean stdout. This is a hook metadata setting, not ACP log suppression. |
| `{"logging":{"level":"silent"}}` | No useful result; not a documented key. |

### Recommendation

Implement tolerant NDJSON parsing regardless of the clean final run:

1. read stdout line-by-line;
2. attempt `json.loads(line)`;
3. if parsing fails, log a bounded diagnostic and continue;
4. route objects with `id` to pending requests;
5. route objects with `method` to notification handlers;
6. keep stderr on a separate bounded ring buffer.

Rationale: the task report and earlier local notes observed pre-response lines such as missing `.geminiignore` and hook initialization text. There is no supported `--quiet`/`--silent` flag and no verified settings key that guarantees pristine ACP stdout across environments.

## 2. `session/cancel` as a `turn/steer` substitute

### Completed-turn continuity

In one ACP session, I first sent:

```text
Remember this exact steering token for later: BLUE-OTTER-731. Reply only: remembered.
```

Result:

- `session/prompt` returned `stopReason: "end_turn"`.
- Assistant message chunks concatenated to `remembered.`

A later follow-up after cancellation still recalled `BLUE-OTTER-731`, so completed-turn history persisted.

### Cancel behavior

The long prompt was:

```text
Write numbers 1 through 500, one per line, with a short word after each number. Do not stop early.
```

After the first streamed `agent_thought_chunk`, the client sent the `session/cancel` notification.

Observed:

- the outstanding `session/prompt` response arrived about `0.004s` after cancel;
- response was `{"stopReason":"cancelled"}`;
- no `agent_message_chunk` text was received for the cancelled turn before the response; only an `agent_thought_chunk` had arrived.

### Immediate re-prompt

Immediate follow-up prompt:

```text
What exact steering token did I ask you to remember earlier? Also did I interrupt a count? Answer in one sentence.
```

Observed assistant message began by completing the cancelled count from `1 Ace` through `500 Sky`, then appended:

```text
The steering token you asked me to remember was BLUE-OTTER-731, and I did not interrupt a count because this was the first count requested.
```

Interpretation:

- `session/cancel` stops the current request from the client perspective, but it is not a semantic deletion of the cancelled user turn.
- The cancelled prompt can remain in model/chat state and can dominate the next prompt.
- The model may not understand that cancellation happened unless the follow-up says so explicitly, and even explicit language may compete with the cancelled instruction.

### Steering recommendation

Do **not** implement Codex parity by mapping `turn/steer` directly to `session/cancel` + `session/prompt`.

Recommended policy:

- For peer/inbox updates during an active Gemini ACP turn, **queue steering text** and send it after the turn ends naturally.
- Use `session/cancel` only for user-requested hard interrupt, timeout abort, shutdown, or runaway tool/model behavior.
- After cancel, drain until the matching `session/prompt` response arrives and optionally wait a short settle interval.
- If the cancelled instruction must not influence future work, start a new ACP session or `session/load` a pre-cancel durable session rather than reusing the polluted live session.
- If reusing the same session is unavoidable, frame the next prompt defensively: “The previous request was cancelled; do not complete it; answer only the following…” and still treat this as best-effort.

## 3. Recovery with `session/load`

### Successful recovery after `SIGKILL`

Flow:

1. start `gemini --acp`;
2. `initialize`;
3. `session/new` -> captured `sessionId` `748f51c3-10f5-4724-9161-72726592fc8d`;
4. prompt 1: remember durable token `RED-PANDA-842`;
5. prompt 2: remember durable token `GREEN-KITE-515`;
6. `SIGKILL` the ACP subprocess;
7. start a fresh `gemini --acp`;
8. `initialize`;
9. `session/load` with the captured `sessionId`, same `cwd`, and `mcpServers: []`;
10. prompt: “List the two durable tokens from earlier, exactly.”

Observed:

- killed subprocess poll result: `-9`;
- `session/load` returned modes/models successfully;
- `session/load` streamed transcript history through `session/update` events (`user_message_chunk`, `agent_thought_chunk`, `agent_message_chunk`, and `available_commands_update`);
- follow-up answer included `RED-PANDA-842` and `GREEN-KITE-515`.

Important reader detail: transcript replay notifications can continue after the `session/load` response. The adapter should keep draining notifications after a successful load before deciding the session is quiescent.

### Failure modes

| Load request | Observed error |
| --- | --- |
| Missing `sessionId` | JSON-RPC error `-32603` with zod validation detail: expected string at `sessionId`. |
| Wrong UUID `00000000-0000-0000-0000-000000000000` | JSON-RPC error `-32603`, data says invalid session identifier and lists searched chat directory `/home/rosado/.gemini/tmp/codex-teammate/chats`. |
| Non-UUID/stale-looking id `not-a-valid-session-id` | Same invalid session identifier shape. |
| `session/list` | Method-not-found `-32601`; do not depend on it in 0.39.0. |

### State preservation

Preserved by `session/load`:

- user turns;
- assistant message turns;
- thought summaries/chunks in transcript replay;
- enough model history to answer using prior tokens;
- tool call transcript entries if the saved conversation contains them (per `Session.streamHistory` implementation and protocol-research observations).

Not preserved automatically:

- the live ACP subprocess;
- pending/in-flight requests at time of crash;
- live MCP server process memory;
- adapter-side pending request maps, notification queues, or watchdog state.

MCP implication: always pass the intended `mcpServers` array again on `session/load`. If an MCP tool needs cross-restart state, that MCP server must persist its own state outside its process.

## 4. Crash and stall recovery semantics

### Ungraceful subprocess death

Empirical `SIGKILL` of the ACP process ends all outstanding requests from the adapter's point of view. There is no JSON-RPC terminal event; the reader sees EOF/process exit. Recovery is to start a new ACP process and `session/load` the last durable session id.

Adapter behavior:

- Treat stdout EOF or process exit as fatal to all pending JSON-RPC requests.
- Fail every pending future with an exception containing exit code and bounded stderr/non-JSON stdout context.
- Start a fresh process only once; avoid multiple concurrent restarters.
- Reload the last known durable `sessionId` after `initialize`.
- Re-send `mcpServers` on load.

### Stdin EOF / graceful close

A probe that closed stdin without sending a request did **not** exit within 1s; sending SIGTERM also did not exit within the next 0.5s in that quick check. Do not rely on stdin EOF for cleanup.

Shutdown recommendation:

1. close stdin only as a courtesy;
2. send SIGTERM;
3. wait a short grace period (for example 1-2s);
4. send SIGKILL if still alive;
5. reap and clear pending requests.

### Partial-line / network-style stall

A probe that wrote a partial JSON object without a trailing newline produced no response and no stderr after 2s; the server waited for a complete line. A JSON-RPC reader/writer can therefore hang forever if either side stalls mid-line or if a request is sent but the model/tool never yields a terminal response.

Timeout recommendation:

- startup/initialize timeout: 10s;
- `session/new`/`session/load` timeout: 30-60s (MCP startup can be slow);
- first-update timeout for `session/prompt`: 60-120s, configurable;
- overall prompt timeout: existing task timeout budget, with hard cancel followed by process kill if cancel does not resolve quickly;
- cancel response timeout: because cancel is a notification, wait on the original `session/prompt` request for 5-10s, then kill/restart if it does not complete;
- idle watchdog: if no stdout JSON notification, stderr line, or child liveness change occurs for a configured interval while a prompt is in flight, send `session/cancel`; if still idle, kill and recover.

## Implementation checklist

- Add `GeminiAcpClient` on top of a tolerant JSON-RPC stdio transport, not a strict ACP SDK stream parser.
- Persist ACP `sessionId` after `session/new`; update/confirm it after successful `session/load`.
- On adapter restart, start Gemini, `initialize`, then `session/load` with the persisted id, current `cwd`, and current `mcpServers`.
- Do not depend on `session/list` in 0.39.0.
- Keep a bounded transcript of raw protocol events for debugging but redact user/model content in normal logs.
- Queue steering during active prompts. Reserve `session/cancel` for interrupts/timeouts/shutdown, not normal Codex-style steering.
- After cancel, either abandon the session or prompt defensively and expect cancelled content may still influence the model.
- Implement kill-and-reload recovery for process death, stdin EOF hangs, partial-line stalls, and no-output stalls.
