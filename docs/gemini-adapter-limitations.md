# Gemini adapter limitations

The Gemini adapter is implemented as Plan A headless CLI integration: `gemini --prompt ... --output-format stream-json --approval-mode yolo`, with adapter-owned Gemini settings for the shared anyteam MCP wrapper. It intentionally does **not** claim full Codex app-server parity.

Known gaps vs the Codex backend:

- **No Codex app-server equivalent:** Gemini exposes ACP over stdio, not an HTTP app-server. This PR does not implement an ACP client.
- **No `turn/steer` parity:** Codex app-server can inject mid-turn inbox prose with `turn/steer`. Gemini headless turns cannot be steered mid-turn; the closest researched ACP primitive is cancellation/follow-up, not direct steering.
- **Schema validation is prompt + Python validation:** Gemini CLI has no `--output-schema` flag, so the adapter embeds schemas in prompts and retries once if final text fails JSON Schema validation.
- **Tool result streams differ:** Gemini `tool_result` events from built-in tools may omit `output`; MCP tool results generally include it. The adapter counts `tool_use` events and does not assume every result event contains payload text.
- **Protocol field naming:** task-complete messages still use the legacy `codex_exit_code` protocol field for backwards compatibility, even for Gemini process exit codes.
- **Auth/config isolation tradeoff:** the adapter writes MCP settings under an isolated Gemini HOME and links/copies existing Gemini auth cache files when present. Operators should prefer `GEMINI_API_KEY` or Vertex/ADC environment auth for unattended teammates.
