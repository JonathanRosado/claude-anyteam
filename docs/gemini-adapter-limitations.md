# Gemini adapter limitations

The Gemini backend has meaningful feature parity with the Codex backend, but it does not claim complete Codex app-server parity. This document lists the known gaps so users and maintainers know what is supported and what still needs follow-up research.

## App-server / TUI presence

- **No direct Codex `app-server` sidecar equivalent:** ACP (`gemini --acp`) is JSON-RPC 2.0 over stdio, not an HTTP app server. The existing TUI/app-server client is not compatible with it.
- **No `turn/steer` parity:** Codex app-server can inject mid-turn inbox prose with `turn/steer`. ACP's closest known primitive is `session/cancel`, but we have not verified that it can provide equivalent mid-turn steering.

## Protocol and streaming

- **Built-in tool result payloads may be missing:** Built-in `tool_result` events in `stream-json` output omit the tool output payload even when the assistant later depended on it; this has been empirically verified with `pwd` and `list_directory`. MCP `tool_result` events do include output. The adapter parser tolerates missing output but cannot recover the omitted built-in payload.
- **ACP stdout can include a non-JSON preamble:** ACP stdout emits startup log lines before the JSON-RPC response, such as `Ignore file not found: ... .geminiignore` and `Hook registry initialized...`. Any future ACP client must filter or tolerate this preamble.
- **Late `init` events are ignored:** If Gemini emits an `init` event after the first `message` event, the adapter logs a warning and discards it rather than overwriting the session id.
- **Protocol field naming is still legacy:** Task-complete messages continue to use the legacy `codex_exit_code` protocol field for backwards compatibility, even when the value is a Gemini process exit code.

## Session semantics

- **Resume behavior is not fully characterized:** Session resume via `--resume` works, but we have not quantified behavioral differences from Codex's `thread/start` plus `turn/start` flow. Known unknowns include inbox replay, partial-turn state, and tool-call state after resume.
- **No thread/fork cross-task memory parity:** Gemini sessions do not currently provide a Codex-equivalent thread/fork memory model across tasks.
- **No mid-turn inbox delivery in headless mode:** Gemini headless turns cannot receive inbox prose during an in-flight turn.

## Model / effort

- **No plumbed effort tiers:** Gemini teammates receive `--model` but not `--effort`; the spawn shim intentionally drops effort with `include_effort=False`. No Gemini equivalent of Codex effort tiers such as `xhigh` or `high` is currently plumbed.

## Schema-constrained output

- **Schema validation is prompt plus Python validation:** Gemini CLI has no `--output-schema` equivalent. The adapter embeds schemas in the prompt and validates final text in Python with retries, which is weaker than Codex's native schema enforcement.

## Installer

- **CLI validation is shallow:** `_check_gemini_cli` only runs `gemini --version` and parses a semver-ish token. It does not enforce a minimum version or verify capability flags, unlike the Gemini-adjacent Codex check. A missing or outdated Gemini CLI surfaces as a non-blocking warning only.

## Auth

- **Only the known auth subtree is merged into isolated settings:** The adapter merges only `security.auth` from the user's real `~/.gemini/settings.json` into isolated per-session settings. Other auth-related state, such as account selection beyond `selectedType` or device codes, is not propagated. If Gemini introduces richer auth state, this merge will need to widen.
- **Config isolation remains a tradeoff:** The adapter writes MCP settings under an isolated Gemini home and links or copies existing Gemini auth cache files when present. Operators should still prefer `GEMINI_API_KEY` or Vertex/ADC environment auth for unattended teammates.

## Open questions

These are tracked for post-ship research:

- ACP `session/new` plus `session/prompt` full round-trip viability.
- Whether stdout pollution can be suppressed with a quiet flag.
- Whether `session/cancel` gives usable mid-turn steering.
- Isolated-auth strategy for multi-session safety.
