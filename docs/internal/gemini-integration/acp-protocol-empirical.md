# Gemini ACP protocol empirical validation

Date: 2026-04-24  
Host cwd: `/home/rosado/Projects/codex-teammate`  
Branch: `feat/gemini-integration`  
Gemini CLI: `gemini 0.39.0`

## Summary

I drove `gemini --acp` directly as a newline-delimited JSON-RPC 2.0 server over stdin/stdout. The full ACP round-trip is empirically proven:

1. `initialize` succeeds.
2. `session/new` succeeds and returns a session id, modes, and models.
3. `session/prompt` succeeds and streams `session/update` notifications before its terminal response.
4. Gemini's docs-style `newSession` method name does **not** work on 0.39.0; the implemented JSON-RPC method is ACP spec `session/new`.
5. Tool telemetry is available as ACP `session/update` events (`tool_call`, `tool_call_update`), not as headless `tool_use` / `tool_result` event names.
6. Built-in tool `tool_call_update` events did **not** include output payloads for `pwd` or `list_directory` in these runs; MCP tool updates **did** include text output in `update.content`.
7. Per-session MCP provisioning through `session/new.params.mcpServers` works. `initialize.params.mcpServers` is ignored/stripped by Gemini 0.39.0's initialize schema.

Verdict: **⚠️ partially viable**. ACP is viable for long-lived session control and MCP tool result payloads, but it does **not** close the built-in tool-output gap for `pwd` / `list_directory` in 0.39.0 because built-in tool completion updates omit raw output.

## Probe client

The probe was a Python JSON-RPC client spawning `gemini --acp`, writing one JSON object per line to stdin, and reading one JSON object per line from stdout. It also auto-responded to ACP client-side `session/request_permission` requests by selecting the allow option so default-mode tool calls could proceed.

MCP server used for the MCP run was a tiny stdio JSON-RPC server with one tool:

```python
# /tmp/gemini-acp-probe/mcp_toy_raw.py
# tools/list -> shout(text: string)
# tools/call shout -> "TOYACP:" + text.upper()
```

## Round-trip: `initialize` -> `session/new` -> `session/prompt`

### `initialize`

Request:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {
    "protocolVersion": 1,
    "clientInfo": {
      "name": "codex-acp-probe",
      "version": "0.1.0"
    },
    "clientCapabilities": {
      "fs": {
        "readTextFile": false,
        "writeTextFile": false
      },
      "terminal": false,
      "auth": {
        "terminal": false
      }
    }
  }
}
```

Response:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "protocolVersion": 1,
    "authMethods": [
      {
        "id": "oauth-personal",
        "name": "Log in with Google",
        "description": "Log in with your Google account"
      },
      {
        "id": "gemini-api-key",
        "name": "Gemini API key",
        "description": "Use an API key with Gemini Developer API",
        "_meta": {
          "api-key": {
            "provider": "google"
          }
        }
      },
      {
        "id": "vertex-ai",
        "name": "Vertex AI",
        "description": "Use an API key with Vertex AI GenAI API"
      },
      {
        "id": "gateway",
        "name": "AI API Gateway",
        "description": "Use a custom AI API Gateway",
        "_meta": {
          "gateway": {
            "protocol": "google",
            "restartRequired": "false"
          }
        }
      }
    ],
    "agentInfo": {
      "name": "gemini-cli",
      "title": "Gemini CLI",
      "version": "0.39.0"
    },
    "agentCapabilities": {
      "loadSession": true,
      "promptCapabilities": {
        "image": true,
        "audio": true,
        "embeddedContext": true
      },
      "mcpCapabilities": {
        "http": true,
        "sse": true
      }
    }
  }
}
```

### `session/new`

Request:

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

Response:

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "sessionId": "bdffb7dc-53f8-48c9-8c3b-77c88f3e3078",
    "modes": {
      "availableModes": [
        {
          "id": "default",
          "name": "Default",
          "description": "Prompts for approval"
        },
        {
          "id": "autoEdit",
          "name": "Auto Edit",
          "description": "Auto-approves edit tools"
        },
        {
          "id": "yolo",
          "name": "YOLO",
          "description": "Auto-approves all tools"
        },
        {
          "id": "plan",
          "name": "Plan",
          "description": "Read-only mode"
        }
      ],
      "currentModeId": "default"
    },
    "models": {
      "availableModels": [
        {
          "modelId": "auto-gemini-3",
          "name": "Auto (Gemini 3)",
          "description": "Let Gemini CLI decide the best model for the task: gemini-3.1-pro, gemini-3-flash"
        },
        {
          "modelId": "auto-gemini-2.5",
          "name": "Auto (Gemini 2.5)",
          "description": "Let Gemini CLI decide the best model for the task: gemini-2.5-pro, gemini-2.5-flash"
        },
        {
          "modelId": "gemini-3.1-pro-preview",
          "name": "gemini-3.1-pro-preview"
        },
        {
          "modelId": "gemini-3-flash-preview",
          "name": "gemini-3-flash-preview"
        },
        {
          "modelId": "gemini-3.1-flash-lite-preview",
          "name": "gemini-3.1-flash-lite-preview"
        },
        {
          "modelId": "gemini-2.5-pro",
          "name": "gemini-2.5-pro"
        },
        {
          "modelId": "gemini-2.5-flash",
          "name": "gemini-2.5-flash"
        },
        {
          "modelId": "gemini-2.5-flash-lite",
          "name": "gemini-2.5-flash-lite"
        }
      ],
      "currentModelId": "auto-gemini-3"
    }
  }
}
```

### `session/prompt`: minimal user turn

Request:

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "session/prompt",
  "params": {
    "sessionId": "bdffb7dc-53f8-48c9-8c3b-77c88f3e3078",
    "prompt": [
      {
        "type": "text",
        "text": "Reply with exactly: OK"
      }
    ]
  }
}
```

Interleaved notifications received before the response included `available_commands_update`, two `agent_thought_chunk` notifications, and the final assistant content. The content-bearing assistant notification was:

```json
{
  "jsonrpc": "2.0",
  "method": "session/update",
  "params": {
    "sessionId": "bdffb7dc-53f8-48c9-8c3b-77c88f3e3078",
    "update": {
      "sessionUpdate": "agent_message_chunk",
      "content": {
        "type": "text",
        "text": "OK"
      }
    }
  }
}
```

Response:

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "stopReason": "end_turn",
    "_meta": {
      "quota": {
        "token_count": {
          "input_tokens": 8921,
          "output_tokens": 1
        },
        "model_usage": [
          {
            "model": "gemini-3-flash-preview",
            "token_count": {
              "input_tokens": 8921,
              "output_tokens": 1
            }
          }
        ]
      }
    }
  }
}
```

## Method-name check: `newSession` vs `session/new`

Gemini 0.39.0's installed docs mention internal names like `newSession`, but the JSON-RPC method name that works is ACP spec `session/new`. Sending `newSession` after initialize produced:

Request:

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "newSession",
  "params": {
    "cwd": "/home/rosado/Projects/codex-teammate",
    "mcpServers": []
  }
}
```

Response:

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "error": {
    "code": -32601,
    "message": "\"Method not found\": newSession",
    "data": {
      "method": "newSession"
    }
  }
}
```

## Tool-use turn: built-in shell `pwd`

Prompt request:

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "session/prompt",
  "params": {
    "sessionId": "56219d32-ee33-4517-bace-feb9de36b2d5",
    "prompt": [
      {
        "type": "text",
        "text": "Use the shell tool to run pwd, then reply with only the raw working directory path."
      }
    ]
  }
}
```

Relevant interleaved events:

```json
{
  "jsonrpc": "2.0",
  "method": "session/update",
  "params": {
    "sessionId": "56219d32-ee33-4517-bace-feb9de36b2d5",
    "update": {
      "sessionUpdate": "agent_thought_chunk",
      "content": {
        "type": "text",
        "text": "[current working directory /home/rosado/Projects/codex-teammate] (get the current working directory)"
      }
    }
  }
}
```

```json
{
  "jsonrpc": "2.0",
  "method": "session/update",
  "params": {
    "sessionId": "56219d32-ee33-4517-bace-feb9de36b2d5",
    "update": {
      "sessionUpdate": "tool_call",
      "toolCallId": "run_shell_command-1777077138997-1",
      "status": "in_progress",
      "title": "pwd",
      "content": [],
      "locations": [],
      "kind": "execute"
    }
  }
}
```

```json
{
  "jsonrpc": "2.0",
  "method": "session/update",
  "params": {
    "sessionId": "56219d32-ee33-4517-bace-feb9de36b2d5",
    "update": {
      "sessionUpdate": "tool_call_update",
      "toolCallId": "run_shell_command-1777077138997-1",
      "status": "completed",
      "title": "pwd",
      "content": [],
      "locations": [],
      "kind": "execute"
    }
  }
}
```

```json
{
  "jsonrpc": "2.0",
  "method": "session/update",
  "params": {
    "sessionId": "56219d32-ee33-4517-bace-feb9de36b2d5",
    "update": {
      "sessionUpdate": "agent_message_chunk",
      "content": {
        "type": "text",
        "text": "/home/rosado/Projects/codex-teammate"
      }
    }
  }
}
```

Response:

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "stopReason": "end_turn",
    "_meta": {
      "quota": {
        "token_count": {
          "input_tokens": 17725,
          "output_tokens": 31
        },
        "model_usage": [
          {
            "model": "gemini-3-flash-preview",
            "token_count": {
              "input_tokens": 17725,
              "output_tokens": 31
            }
          }
        ]
      }
    }
  }
}
```

Finding: the built-in shell tool's `tool_call_update` did **not** include `rawOutput` or content payload; `content` was `[]`. The model's assistant text contained the pwd result, but the tool result event itself did not.

## Tool-use turn: built-in `list_directory`

Prompt request:

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "session/prompt",
  "params": {
    "sessionId": "491894c7-bfdd-4082-b379-355f6998ffd1",
    "prompt": [
      {
        "type": "text",
        "text": "Use the list_directory tool on '.', then reply with exactly: LISTED."
      }
    ]
  }
}
```

Relevant tool events:

```json
{
  "jsonrpc": "2.0",
  "method": "session/update",
  "params": {
    "sessionId": "491894c7-bfdd-4082-b379-355f6998ffd1",
    "update": {
      "sessionUpdate": "tool_call",
      "toolCallId": "list_directory-1777077165390-1",
      "status": "in_progress",
      "title": ".",
      "content": [],
      "locations": [],
      "kind": "search"
    }
  }
}
```

```json
{
  "jsonrpc": "2.0",
  "method": "session/update",
  "params": {
    "sessionId": "491894c7-bfdd-4082-b379-355f6998ffd1",
    "update": {
      "sessionUpdate": "tool_call_update",
      "toolCallId": "list_directory-1777077165390-1",
      "status": "completed",
      "title": ".",
      "content": [],
      "locations": [],
      "kind": "search"
    }
  }
}
```

Assistant content:

```json
{
  "jsonrpc": "2.0",
  "method": "session/update",
  "params": {
    "sessionId": "491894c7-bfdd-4082-b379-355f6998ffd1",
    "update": {
      "sessionUpdate": "agent_message_chunk",
      "content": {
        "type": "text",
        "text": "LISTED."
      }
    }
  }
}
```

Finding: same as shell `pwd`: built-in `list_directory` completion carried no output payload (`content: []`, no `rawOutput`).

## Tool-use turn: per-session MCP tool

### MCP provisioning shape that works

Request:

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "session/new",
  "params": {
    "cwd": "/home/rosado/Projects/codex-teammate",
    "mcpServers": [
      {
        "name": "toy",
        "command": "python3",
        "args": [
          "/tmp/gemini-acp-probe/mcp_toy_raw.py"
        ],
        "env": []
      }
    ]
  }
}
```

Response: normal `session/new` response with session id `13274fa3-b0e2-423c-af3f-e4a7f4b3c1ae`.

Prompt request:

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "session/prompt",
  "params": {
    "sessionId": "13274fa3-b0e2-423c-af3f-e4a7f4b3c1ae",
    "prompt": [
      {
        "type": "text",
        "text": "Use the mcp_toy_shout tool with text 'hello acp', then reply with only the tool output."
      }
    ]
  }
}
```

Permission request from agent to client, received before the tool ran:

```json
{
  "jsonrpc": "2.0",
  "id": 4,
  "method": "session/request_permission",
  "params": {
    "sessionId": "13274fa3-b0e2-423c-af3f-e4a7f4b3c1ae",
    "options": [
      {
        "optionId": "allow",
        "name": "Allow",
        "kind": "allow_once"
      },
      {
        "optionId": "cancel",
        "name": "Reject",
        "kind": "reject_once"
      }
    ],
    "toolCall": {
      "toolCallId": "mcp_toy_shout-1777077221818-1",
      "status": "pending",
      "title": "shout (toy MCP Server)",
      "content": [],
      "locations": [],
      "kind": "other"
    }
  }
}
```

Probe response to permission request:

```json
{
  "jsonrpc": "2.0",
  "id": 4,
  "result": {
    "outcome": {
      "outcome": "selected",
      "optionId": "allow"
    }
  }
}
```

MCP tool completion update:

```json
{
  "jsonrpc": "2.0",
  "method": "session/update",
  "params": {
    "sessionId": "13274fa3-b0e2-423c-af3f-e4a7f4b3c1ae",
    "update": {
      "sessionUpdate": "tool_call_update",
      "toolCallId": "mcp_toy_shout-1777077221818-1",
      "status": "completed",
      "title": "shout (toy MCP Server)",
      "content": [
        {
          "type": "content",
          "content": {
            "type": "text",
            "text": "TOYACP:HELLO ACP"
          }
        }
      ],
      "locations": [],
      "kind": "other"
    }
  }
}
```

Assistant content:

```json
{
  "jsonrpc": "2.0",
  "method": "session/update",
  "params": {
    "sessionId": "13274fa3-b0e2-423c-af3f-e4a7f4b3c1ae",
    "update": {
      "sessionUpdate": "agent_message_chunk",
      "content": {
        "type": "text",
        "text": "TOYACP:HELLO ACP"
      }
    }
  }
}
```

Finding: MCP tool results are substantially better than built-in tool results under ACP. The output payload was present in `tool_call_update.content[0].content.text`.

## MCP provisioning: initialize vs per-session

### Per-session works

The working shape is `session/new.params.mcpServers` as an array of ACP MCP server objects:

```json
{
  "cwd": "/home/rosado/Projects/codex-teammate",
  "mcpServers": [
    {
      "name": "toy",
      "command": "python3",
      "args": ["/tmp/gemini-acp-probe/mcp_toy_raw.py"],
      "env": []
    }
  ]
}
```

For stdio servers, `env` is required by Gemini's ACP schema and must be an array of `{ "name": ..., "value": ... }` objects; an empty array is accepted.

### Initialize does not provision MCP servers in 0.39.0

I sent `mcpServers` inline in `initialize.params` with the same server object, then sent `session/new` with `mcpServers: []`. Initialize succeeded, which indicates the unknown `mcpServers` field was accepted/stripped rather than applied. The later prompt asking for `mcp_toy_shout` did not produce an MCP tool call and hung until killed. This matches the installed 0.39.0 schema: `zInitializeRequest` has only `_meta`, `clientCapabilities`, `clientInfo`, and `protocolVersion`; `zNewSessionRequest` has `cwd` and required `mcpServers`.

## Error paths

### Malformed initialize parameter type

Request:

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"1"}}
```

Response:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "error": {
    "code": -32603,
    "message": "Internal error",
    "data": [
      {
        "expected": "number",
        "code": "invalid_type",
        "path": ["protocolVersion"],
        "message": "Invalid input: expected number, received string"
      }
    ]
  }
}
```

### Missing `session/new.params.mcpServers`

Request:

```json
{"jsonrpc":"2.0","id":2,"method":"session/new","params":{"cwd":"/home/rosado/Projects/codex-teammate"}}
```

Response:

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "error": {
    "code": -32603,
    "message": "Internal error",
    "data": [
      {
        "expected": "array",
        "code": "invalid_type",
        "path": ["mcpServers"],
        "message": "Invalid input: expected array, received undefined"
      }
    ]
  }
}
```

### Bad prompt shape

Request:

```json
{"jsonrpc":"2.0","id":2,"method":"session/prompt","params":{"sessionId":"nope","prompt":"hi"}}
```

Response:

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "error": {
    "code": -32603,
    "message": "Internal error",
    "data": [
      {
        "expected": "array",
        "code": "invalid_type",
        "path": ["prompt"],
        "message": "Invalid input: expected array, received string"
      }
    ]
  }
}
```

## Implementation notes for Plan B

- Use newline-delimited JSON-RPC over stdio, not Content-Length framing.
- Always send `initialize` first.
- Use `session/new`, `session/prompt`, `session/set_mode`, etc. Do not use docs/internal names like `newSession`.
- `session/new.params.mcpServers` is required even when empty.
- Prompt content is an array of content blocks, e.g. `[{"type":"text","text":"..."}]`.
- Stream assistant text from `session/update.update.sessionUpdate == "agent_message_chunk"` and concatenate `update.content.text`.
- Treat `agent_thought_chunk` as separate telemetry; do not include it in final assistant text.
- Count tools from `tool_call` / `tool_call_update`, keyed by `toolCallId`.
- For permissioned tool calls in `default` mode, be prepared to answer client request `session/request_permission`. Alternatively call `session/set_mode` to `yolo` after session creation if the adapter wants auto-approval semantics.
- Do not depend on built-in tool outputs being present in ACP events; for built-ins, 0.39.0 exposed only status/title/kind in completion updates in my runs.
- For MCP tools, parse `tool_call_update.content[*].content` for returned text/resources.

## Plan B viability verdict

**⚠️ partially viable.**

ACP Plan B is viable for:

- long-lived Gemini subprocesses,
- explicit session creation/loading/prompting,
- model/mode discovery,
- per-session MCP server injection,
- MCP tool result payloads in structured session updates.

Specific gaps/risks:

- Built-in tools do **not** provide output payloads in `tool_call_update` for `pwd` or `list_directory` on Gemini CLI 0.39.0. This means ACP does not fully close the headless stream-json built-in `tool_result.output` omission.
- The method names in Gemini's prose docs are misleading for JSON-RPC clients; the implemented wire methods are ACP spec names.
- `initialize`-level MCP provisioning does not work on 0.39.0; use per-session provisioning.
- Validation errors come back as `-32603 Internal error` with Zod details, not as clean `-32602 Invalid params`, so the adapter should surface the nested `error.data` when debugging.
