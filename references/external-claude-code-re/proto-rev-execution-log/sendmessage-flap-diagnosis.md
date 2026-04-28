# SendMessage tool-discovery flap diagnosis (Task #51)

Date: 2026-04-28  
Branch: `proto-rev/impl/sendmessage-flap-fix` from integration `2900940`

## What #44 diagnostics record

`src/claude_anyteam/wrapper_mcp_diagnostics.py` writes best-effort JSONL rows to:

```text
~/.claude/teams/<team>/diagnostics/wrapper-mcp-tools.jsonl
```

The current wiring records two independent views:

- `src/claude_anyteam/codex.py`
  - `codex_exec_session_start`
  - `codex_app_server_mcp_config_prepared`
  - `codex_app_server_session_start`
- `src/claude_anyteam/wrapper_server.py`
  - `server_build_start`
  - `register_tool`
  - `server_registered_snapshot`
  - `list_tools` / `list_tools_failed`
  - `call_tool_started` / `call_tool_completed` / failure variants

The important discriminator for this incident is `list_tools.payload`: it includes the observed tool names, count, expected list, missing/unexpected tool names, and booleans such as `send_message_registered`.

## Fresh gpt-5.5/xhigh probe

I ran a fresh Codex App Server probe from `/tmp/flap-fix` using local `uv run` so `claude-anyteam-wrapper` resolved to the worktree virtualenv. The probe created team `test-flap-51-89569`, agent `codex-test`, model `gpt-5.5`, effort `xhigh`, and exercised five peer-DM-style prose turns to `team-lead`, carrying the returned thread id forward via fork/resume.

Diagnostic log:

```text
~/.claude/teams/test-flap-51-89569/diagnostics/wrapper-mcp-tools.jsonl
```

Summary:

```text
events {
  codex_app_server_mcp_config_prepared: 5,
  codex_app_server_session_start: 5,
  server_build_start: 5,
  register_tool: 80,
  server_registered_snapshot: 5,
  list_tools: 5,
  call_tool_started: 5,
  call_tool_completed: 5
}
```

Each turn listed the wrapper tools and saw `send_message`:

```text
turn 1: server_registered_snapshot tool_count=17 send_message_registered=True missing=[]
turn 1: list_tools                 tool_count=17 send_message_registered=True missing=[]
turn 1: call_tool_started/completed tool_name=send_message

turn 2: server_registered_snapshot tool_count=17 send_message_registered=True missing=[]
turn 2: list_tools                 tool_count=17 send_message_registered=True missing=[]
turn 2: call_tool_started/completed tool_name=send_message

turn 3: server_registered_snapshot tool_count=17 send_message_registered=True missing=[]
turn 3: list_tools                 tool_count=17 send_message_registered=True missing=[]
turn 3: call_tool_started/completed tool_name=send_message

turn 4: server_registered_snapshot tool_count=17 send_message_registered=True missing=[]
turn 4: list_tools                 tool_count=17 send_message_registered=True missing=[]
turn 4: call_tool_started/completed tool_name=send_message

turn 5: server_registered_snapshot tool_count=17 send_message_registered=True missing=[]
turn 5: list_tools                 tool_count=17 send_message_registered=True missing=[]
turn 5: call_tool_started/completed tool_name=send_message
```

The model successfully called the tool on all five turns. The App Server notification shape observed during this run names the wrapper tool under `params.item.tool` rather than `params.item.name`/`tool_name`.

## Existing diagnostic corpus check

I also scanned the current local diagnostic corpus under `~/.claude/teams/*/diagnostics/wrapper-mcp-tools.jsonl`. In teams with wrapper `list_tools` or `server_registered_snapshot` events, I found no row where `send_message_registered` was false and no row with missing expected tools.

Examples:

- `994dcbec-7f00-4a7b-b7ce-4ab62f7129fa`: 20 `server_registered_snapshot`, 12 `list_tools`, no bad rows.
- `claude-anyteam`: 65 `server_registered_snapshot`, 15 `list_tools`, no bad rows.
- `sendmsg-flap-probe-1777383279`: 5 `server_registered_snapshot`, 5 `list_tools`, no bad rows.

## Hypothesis result

- (a) **Tool-listing cache going stale**: ruled out by observed data. Across repeated gpt-5.5/xhigh App Server turns, each `list_tools` snapshot contained `send_message`.
- (b) **Wrapper MCP losing registration silently**: ruled out by observed data. Every wrapper process emitted a complete `server_registered_snapshot`, and successful `call_tool_started/completed` rows for `send_message` appeared in the fresh probe.
- (c) **Model hallucination / prompt handling**: confirmed as the remaining layer. The data shows the tool is registered and visible when Codex asks. Therefore prose claiming the tool is unavailable is not evidence of substrate/tool loss; it is an invalid model/adapter output that should be repaired or suppressed.

## Additional implementation finding

The live App Server event shape for tool calls is:

```json
{"params":{"item":{"type":"mcpToolCall","server":"claude_anyteam_wrapper","tool":"send_message", ...}}}
```

Existing helper comments expected `name` in this shape. Any adapter guard that only recognizes `name` / `tool_name` can miss successful App Server `send_message` calls and may allow redundant prose fallback after a real delivery. The fix should recognize the `tool` field and treat a hallucinated “missing send_message” final answer as a repairable/suppressible invalid prose output, not as a message to relay to teammates.
