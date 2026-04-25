# Gemini CLI runtime internals

Date: 2026-04-24. Installed CLI tested: `gemini 0.39.0`.

Scope: Gemini side only. This file combines the checked-in research docs with empirical runs against the installed `gemini` binary in this workspace.

## Capability matrix

| Feature | Supported? | Evidence / notes |
| --- | --- | --- |
| Programmatic one-shot invocation | **Yes** | `gemini -p <prompt>` runs non-interactively. A non-TTY stdin pipe also triggers headless mode. Help says `-p/--prompt` is headless and is appended to stdin. Empirical `printf ... \| gemini -o stream-json` exited `0` and emitted JSONL. |
| Interactive invocation | **Yes, but TUI-oriented** | Default `gemini [query..]` launches interactive mode. `--prompt-interactive` runs an initial prompt and stays interactive. Not suitable as the primary adapter transport because stdout is TUI/user-facing rather than a stable request/response channel. |
| Machine output formats | **Yes** | `--output-format text`, `json`, and `stream-json` are accepted. Bad output format exits `1` before model startup. |
| Streaming response tokens | **Yes** | `stream-json` emits newline-delimited JSON objects, not SSE. Assistant chunks are `type: "message"`, `role: "assistant"`, `delta: true`, `content: ...`. Chunks may contain many tokens, not exactly one token. |
| Tool-call streaming | **Yes** | `stream-json` emits `tool_use` with `tool_name`, `tool_id`, and `parameters`, followed by `tool_result` with `tool_id`, `status`, and sometimes `output`. Built-in tool results may omit output in the stream even when the final answer used the output. |
| MCP support | **Yes** | `gemini mcp` supports `add/remove/list/enable/disable`; config is read from `.gemini/settings.json` and `~/.gemini/settings.json`. Empirical project-local MCP server appeared as tool `mcp_toy_shout` and was callable from headless mode. |
| MCP tool naming | **Yes / transformed** | MCP tools are exposed as `mcp_<server>_<tool>`. Empirical server name `toy`, tool `shout` became `mcp_toy_shout`. Avoid underscores in server aliases per official docs/research. |
| Headless approvals | **Yes** | `--approval-mode` supports `default`, `auto_edit`, `yolo`, `plan`; `-y/--yolo` aliases auto-approve-all. Empirical yolo warnings were printed to stderr, not stdout. |
| Auth cache reuse | **Yes** | Existing sign-in was reused for headless runs. On this machine credentials live under `~/.gemini/`: `oauth_creds.json` mode `0600`, `settings.json`, `google_accounts.json`, `projects.json`, `trustedFolders.json`, `installation_id`. User-level, not project-level. |
| Concurrent sessions sharing auth | **Likely yes** | Auth cache is user-level and read by multiple independent `gemini` processes. Session files are per project under `~/.gemini/tmp/<project-name>/chats/`, so concurrent sessions get separate UUID transcript files. Avoid writing `~/.gemini/settings.json` concurrently from an adapter. |
| Session resume by id | **Yes** | `--resume <uuid>` resumed an earlier session and emitted the same `session_id` in `init`. `--resume latest` and numeric indices are advertised. Invalid id exited `42` with a stderr diagnostic. |
| Durable transcript | **Yes** | JSONL session transcripts are stored at `~/.gemini/tmp/<project-name>/chats/session-<timestamp>-<uuid-prefix>.jsonl`; they include user turns, model turns, thoughts summaries, tool calls, tool results, tokens, and model ids. Treat as sensitive. |
| Checkpoint/restore | **Separate feature** | Research docs say checkpointing exists separately from chat history and is disabled by default. It is not needed for basic `--resume`. |
| Long-lived machine API | **Partial** | `gemini --acp` and deprecated `--experimental-acp` are present. This is JSON-RPC 2.0 over stdio for ACP, not a Codex-style HTTP app server. Reviewer A verified `initialize` returns JSON-RPC 2.0 with protocol version 1 and `gemini-cli` 0.39.0 agent metadata, but full session/prompt flow remains unverified. |
| Codex-style app-server sidecar | **No direct equivalent** | No `gemini app-server` or HTTP sidecar was found. ACP is the closest supported analog, but protocol and transport differ. |
| CLI schema-constrained output | **No / adapter must validate** | No `--output-schema` equivalent in help or research docs. `json`/`stream-json` structure only wraps the CLI protocol; final model text must be prompt-constrained and validated by the adapter. |

## Process lifecycle and stdio contract

### Recommended one-shot shape

```bash
gemini --prompt "$PROMPT" --output-format stream-json [--model MODEL] [--resume SESSION_ID]
```

Equivalent short flags: `gemini -p "$PROMPT" -o stream-json -m MODEL -r SESSION_ID`.

Observed behavior:

- stdout is reserved for the selected output format.
- stderr carries diagnostics/warnings, e.g. yolo mode warnings and invalid-resume errors.
- stdin is read in headless mode and prepended/appended with `--prompt` content. In the observed run, stdin came first, then blank lines, then the `--prompt` string.
- exit code `0` means successful turn.
- exit code `1` observed for CLI usage error (`--output-format bogus`). Research docs also list `1` as general/API failure.
- exit code `42` observed for invalid resume id; research docs classify this as input error.
- research docs also list `53` for turn-limit exceeded.

### Real stdout sample: stdin plus `--prompt`

Command:

```bash
printf 'Input from stdin: say OK from stdin too.\n' \
  | gemini --prompt 'Reply with exactly: OK prompt' --output-format stream-json
```

Stdout:

```jsonl
{"type":"init","timestamp":"2026-04-24T21:40:15.973Z","session_id":"0ef1a5dd-c0e6-4855-8416-ad0867585237","model":"auto-gemini-3"}
{"type":"message","timestamp":"2026-04-24T21:40:15.977Z","role":"user","content":"Input from stdin: say OK from stdin too.\n\n\nReply with exactly: OK prompt"}
{"type":"message","timestamp":"2026-04-24T21:40:22.092Z","role":"assistant","content":"OK prompt","delta":true}
{"type":"result","timestamp":"2026-04-24T21:40:22.161Z","status":"success","stats":{"total_tokens":10929,"input_tokens":10564,"output_tokens":57,"cached":0,"input":10564,"duration_ms":6188,"tool_calls":0,"models":{"gemini-2.5-flash-lite":{"total_tokens":2897,"input_tokens":2742,"output_tokens":55,"cached":0,"input":2742},"gemini-3-flash-preview":{"total_tokens":8032,"input_tokens":7822,"output_tokens":2,"cached":0,"input":7822}}}}
```

## Wire-format notes

### `--output-format json`

Single pretty-printed JSON object on stdout:

```json
{
  "session_id": "5ae676da-09b5-4dc2-8a31-09f89125af8c",
  "response": "hello",
  "stats": { "models": { "gemini-3-flash-preview": { "api": { "totalRequests": 1 } } } }
}
```

Useful for simple automation, but not enough for live tool-call telemetry.

### `--output-format stream-json`

Newline-delimited JSON events. Observed event types:

- `init`: session id and selected model alias.
- `message`: user echo and assistant deltas.
- `tool_use`: tool name/id/parameters.
- `tool_result`: result status and optional output.
- `result`: terminal status and aggregate stats.

Assistant chunks are deltas. Example count prompt:

```jsonl
{"type":"message","role":"assistant","content":"1\n2\n3\n4\n5\n6\n7\n8","delta":true}
{"type":"message","role":"assistant","content":"\n9\n10","delta":true}
```

A parser should concatenate assistant `message.content` where `role == "assistant"`, tolerate unknown event types, and use the terminal `result.status` plus process exit code for success/failure.

### Built-in tool call sample

Command used `--approval-mode yolo`; stderr contained yolo warnings.

```jsonl
{"type":"tool_use","timestamp":"2026-04-24T21:41:35.918Z","tool_name":"list_directory","tool_id":"list_directory_1777066895918_0","parameters":{"dir_path":"."}}
{"type":"tool_result","timestamp":"2026-04-24T21:41:36.056Z","tool_id":"list_directory_1777066895918_0","status":"success"}
{"type":"result","status":"success","stats":{"tool_calls":1}}
```

Note: this built-in `tool_result` did not include directory contents on stdout; do not assume every successful built-in tool result carries `output`.

### MCP tool call sample

Project-local `.gemini/settings.json`:

```json
{
  "mcpServers": {
    "toy": {
      "command": "python",
      "args": ["/tmp/gemini-mcp-proj/echo_server.py"],
      "trust": true,
      "timeout": 30000
    }
  }
}
```

A FastMCP tool `shout(text: str) -> str` was exposed and called as `mcp_toy_shout`:

```jsonl
{"type":"tool_use","tool_name":"mcp_toy_shout","tool_id":"mcp_toy_shout_1777067007075_0","parameters":{"text":"hello"}}
{"type":"tool_result","tool_id":"mcp_toy_shout_1777067007075_0","status":"success","output":"TOY:HELLO"}
{"type":"message","role":"assistant","content":"TOY:HELLO","delta":true}
```

The durable session transcript stores a richer internal representation:

```jsonl
{"toolCalls":[{"id":"mcp_toy_shout_...","name":"mcp_toy_shout","args":{"text":"hello"},"result":[{"functionResponse":{"response":{"output":"TOY:HELLO"}}}],"status":"success","displayName":"shout (toy MCP Server)"}]}
```

## Auth and config persistence

Observed user-level Gemini files on this host:

```text
~/.gemini/settings.json              # selected auth type: oauth-personal
~/.gemini/oauth_creds.json           # OAuth access/id/refresh tokens, mode 0600
~/.gemini/google_accounts.json
~/.gemini/projects.json
~/.gemini/trustedFolders.json
~/.gemini/installation_id
~/.gemini/tmp/<project-name>/chats/  # durable chat sessions
~/.gemini/history/<project-name>/
```

Implications:

- Signed-in OAuth is per Unix user, not per project.
- Project/session state is separated below `~/.gemini/tmp/<project-name>/`.
- Multiple Gemini subprocesses can share the credential cache for reads, but an adapter should avoid mutating the real `~/.gemini/settings.json` for MCP injection.
- Preferred adapter pattern remains an adapter-owned Gemini home/config root or project-local `.gemini/settings.json`; if `HOME` is changed, remember that OAuth credentials in real `~/.gemini` will not be visible unless copied or auth is supplied through env vars.
- For reliable unattended use, research docs recommend explicit `GEMINI_API_KEY` or Vertex/ADC env vars rather than relying on first-run browser OAuth.

## MCP support and discovery

CLI help:

```text
gemini mcp add <name> <commandOrUrl> [args...]
gemini mcp remove <name>
gemini mcp list
gemini mcp enable <name>
gemini mcp disable <name>
```

Config supports `mcpServers.<name>` entries with `command`, `args`, `env`, `cwd`, `url`/`httpUrl`, headers, timeout, and trust according to the research docs.

Discovery behavior:

- Gemini loads configured servers at startup for a headless turn.
- Tools are surfaced to the model with transformed names (`mcp_<server>_<tool>`).
- In the empirical project-local MCP run, the model knew and called `mcp_toy_shout`.
- `gemini mcp list` returned exit `0` but no visible output in my project-local test despite the server being usable in a turn; implementation should treat an actual prompt/tool probe as stronger than `mcp list` output.

## Session resumption and transcripts

Observed list output:

```text
Available sessions for this project (8):
  1. Input from stdin: say OK from stdin too. Reply with exactly: OK prompt (...) [0ef1a5dd-c0e6-4855-8416-ad0867585237]
```

Observed resume by UUID:

```jsonl
{"type":"init","session_id":"0ef1a5dd-c0e6-4855-8416-ad0867585237","model":"auto-gemini-3"}
{"type":"message","role":"user","content":"What exact phrase did I ask you to reply with in the previous turn? Answer only that phrase."}
{"type":"message","role":"assistant","content":"OK prompt","delta":true}
{"type":"result","status":"success", ...}
```

Invalid resume:

```text
exit code: 42
stderr: Error resuming session: Invalid session identifier "no-such-session".
        Searched for sessions in /home/rosado/.gemini/tmp/codex-teammate/chats.
```

Durable session files are JSONL. First records look like:

```jsonl
{"sessionId":"a9c4f58e-eecf-41e8-a43f-a9de908e979d","projectHash":"...","startTime":"2026-04-24T21:41:12.092Z","lastUpdated":"2026-04-24T21:41:12.092Z","kind":"main"}
{"type":"user","content":[{"text":"Use a tool to run pwd, then answer with the current working directory."}]}
{"type":"gemini","content":"I will run `pwd` to determine the current working directory.","tokens":{...},"model":"gemini-3-flash-preview"}
```

These files can be used for diagnostics, but the supported resume interface is the CLI `--resume` flag, not direct transcript editing.

## App-server analog

Gemini 0.39.0 exposes:

- `--acp`: starts the agent in ACP mode.
- `--experimental-acp`: deprecated alias.

This is the closest analog to Codex `app-server`, but it is **not** equivalent:

| Codex app-server dependency | Gemini equivalent | Gap |
| --- | --- | --- |
| Long-lived subprocess | `gemini --acp` | Yes, but ACP protocol differs. |
| JSON-RPC over stdio | ACP JSON-RPC 2.0 over stdio | Likely usable, but method names/session semantics differ. |
| Codex `thread/start`, `turn/start`, `turn/steer` | ACP wire methods `initialize`, `authenticate`, `session/new`, `session/load`, `session/prompt`, `session/cancel` | No direct `turn/steer` parity found. Earlier internal handler names like `newSession`, `loadSession`, `prompt`, and `cancel` are not the JSON-RPC method strings. |
| HTTP/app-server-style sidecar | None found | Documented limitation. |
| TUI presence sidecar compatible with existing Codex client | None | Would require a separate ACP client or headless subprocess loop. |

Empirical startup test:

```bash
timeout 2 gemini --acp
# exit 124 from timeout; no stdout/stderr before a JSON-RPC request was sent
```

Reviewer A's `initialize` probe found an important framing caveat: stdout can emit non-JSON startup log lines before the JSON-RPC response, e.g. `Ignore file not found: ... .geminiignore` and `Hook registry initialized...`. A robust ACP client must tolerate and filter a non-JSON stdout preamble, or implementation must first find a quiet/debug setting before relying on pure NDJSON/JSON-RPC framing.

Verified `initialize` response facts for `gemini 0.39.0`:

- JSON-RPC envelope: `jsonrpc: "2.0"`.
- ACP protocol version: `1`.
- Agent metadata: `agentInfo` reports `gemini-cli` version `0.39.0`.
- Session capability: `loadSession: true`.
- Prompt capabilities: image, audio, and embedded context are true.
- MCP transports: HTTP and SSE are true.

## Explicit gaps vs Codex features this repo relies on

| Codex feature relied on | Gemini status | Adapter consequence |
| --- | --- | --- |
| `codex exec --json` JSONL event stream | `gemini -p ... -o stream-json` | Supported with different event taxonomy: `init/message/tool_use/tool_result/result/error`. Parser must be Gemini-specific. |
| `--output-schema <schema>` | No CLI equivalent | Prompt with schema and validate in Python; retry on invalid model payload. Use `google-genai` only if hard schema guarantees become mandatory. |
| Inline ephemeral MCP config via Codex `-c mcp_servers...` | No equivalent CLI override found | Use adapter-owned `.gemini/settings.json` / HOME or project-local `.gemini/settings.json`. Avoid mutating real user config. |
| Wrapper MCP tool names are bare (`send_message`) in Codex prompts | Gemini prefixes MCP tools as `mcp_<server>_<tool>` | Gemini prompts must name `mcp_anyteam_send_message`, `mcp_anyteam_task_update`, etc. Pick a server alias without underscores if possible, then confirm exact generated names. |
| Tool-call count/event telemetry | Supported | Count `tool_use` events and/or terminal `result.stats.tool_calls`. Tool results may be sparse for built-ins. |
| Resume session/thread id from events | Supported | Capture `init.session_id`; pass it to `--resume <uuid>` on later calls. |
| Long-lived Codex app-server with turn steering | Partial via ACP | Headless Plan A cannot steer mid-turn. ACP may support cancel/follow-up prompts, but no direct `turn/steer` equivalent is confirmed. |
| App-server JSON schema generation / known 60-method protocol | No Gemini equivalent found | ACP needs separate protocol implementation and testing. |
| User config not mutated for wrapper injection | Achievable but different | Need isolated Gemini config root or temporary project `.gemini/settings.json`; if HOME is isolated, solve auth visibility. |
| `--dangerously-bypass-approvals-and-sandbox` / full-auto | `--approval-mode yolo` or `-y`; sandbox flag exists | Map carefully. `--approval-mode plan` gives read-only planning mode. |
| `codex app-server` TUI presence surface | No direct equivalent | Document limitation or implement ACP sidecar separately. |

## Open questions

1. **Full ACP task exchange:** `initialize` is verified, but full `session/new` + `session/prompt` request/response and notification behavior still needs an implementation probe.
2. **ACP stdout-pollution avoidability:** determine whether a quiet/debug flag or environment setting can prevent non-JSON stdout preamble; otherwise the ACP client must filter it.
3. **`session/cancel` parity:** verify whether `session/cancel` can support useful mid-turn interruption/steering behavior or only cancel-and-replay semantics.
4. **Best isolated-auth strategy:** overriding `HOME` isolates Gemini config but also hides `~/.gemini/oauth_creds.json`. Decide whether to copy OAuth files, use project-local `.gemini/settings.json`, or require env-var auth for Gemini teammates.
