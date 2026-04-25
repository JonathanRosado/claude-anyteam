# Gemini adapter limitations

The Gemini backend has meaningful feature parity with the Codex backend, but it does not claim complete Codex app-server parity. This document lists the known gaps so users and maintainers know what is supported and what still needs follow-up research.

## App-server / TUI presence

- **No direct Codex `app-server` sidecar equivalent:** ACP (`gemini --acp`) is JSON-RPC 2.0 over stdio, not an HTTP app server. The existing TUI/app-server client is not compatible with it.
- **No `turn/steer` parity:** Codex app-server can inject mid-turn inbox prose with `turn/steer`. Gemini ACP has `session/cancel`, but empirical testing showed cancelled prompt text can remain influential in later turns, so the ACP backend queues prose outside active task turns rather than treating cancel as a safe steering primitive.

## Protocol and streaming

- **Built-in tool result payloads may be missing:** Built-in tool updates in both headless `stream-json` and ACP can omit the tool output payload even when the assistant later depended on it; this has been empirically verified with shell commands. MCP tool updates do include output in ACP. The adapter parser tolerates missing built-in output but cannot recover the omitted payload.
- **ACP stdout can include a non-JSON preamble:** ACP stdout emits startup log lines before the JSON-RPC response, such as `Ignore file not found: ... .geminiignore` and `Hook registry initialized...`. The shared JSON-RPC stdio transport filters non-JSON lines, but strict third-party ACP clients may still fail on this host.
- **Late `init` events are ignored:** If Gemini emits an `init` event after the first `message` event, the adapter logs a warning and discards it rather than overwriting the session id.
- **Protocol field naming is still legacy:** Task-complete messages continue to use the legacy `codex_exit_code` protocol field for backwards compatibility, even when the value is a Gemini process exit code.

## Session semantics

- **Resume/reload is weaker than Codex thread lineage:** Headless `--resume` works, and the ACP backend attempts `session/load` using the adapter-persisted ACP `sessionId`. Recovery replays transcript state but cannot preserve live MCP process memory, so MCP servers are re-supplied on load and a new session is created if load fails.
- **No thread/fork cross-task memory parity:** Gemini sessions do not currently provide a Codex-equivalent thread/fork memory model across tasks.
- **No mid-turn inbox delivery in headless mode:** Gemini headless turns cannot receive inbox prose during an in-flight turn; the ACP backend also avoids unsafe mid-turn steering and handles prose with ephemeral sessions.

## Model / effort

- **No plumbed effort tiers:** Gemini teammates receive `--model` but not `--effort`; the spawn shim intentionally drops effort with `include_effort=False`. No Gemini equivalent of Codex effort tiers such as `xhigh` or `high` is currently plumbed.
- **Effort mapping is intentionally deferred:** [Post-ship parity research §3](internal/gemini-integration/post-ship-parity-research.md#3-effort-mapping) identified `modelConfigs.customAliases` / `customOverrides` with SDK-level `thinkingBudget` / `thinkingLevel` as the theoretical mapping path, including a proposed Codex-effort-to-Gemini-thinking table. We are deferring that path because it may require writing generated model config rather than simply passing a per-turn CLI flag, and because the semantic mapping from Codex effort tiers to Gemini thinking budgets has not been empirically validated.

## Schema-constrained output

- **Schema validation is prompt plus Python validation:** Gemini CLI has no `--output-schema` equivalent. The adapter embeds schemas in the prompt and validates final text in Python with retries, which is weaker than Codex's native schema enforcement.

## Installer

- **CLI validation is shallow:** `_check_gemini_cli` only runs `gemini --version` and parses a semver-ish token. It does not enforce a minimum version or verify capability flags, unlike the Gemini-adjacent Codex check. A missing or outdated Gemini CLI surfaces as a non-blocking warning only.

## Auth

- **Only the known auth subtree is merged into isolated settings:** The adapter merges only `security.auth` from the user's real `~/.gemini/settings.json` into isolated per-session settings. Other auth-related state, such as account selection beyond `selectedType` or device codes, is not propagated. If Gemini introduces richer auth state, this merge will need to widen.
- **Config isolation remains a tradeoff:** The adapter writes MCP settings under an isolated Gemini home and copies selected Gemini auth cache files when present. Operators should still prefer `GEMINI_API_KEY` or Vertex/ADC environment auth for unattended teammates.

## Open questions

These are tracked for follow-up work:

- Whether Gemini ACP will expose `session/list` or richer recovery metadata for inspecting available sessions.
- Whether a future Gemini ACP release provides a safe mid-turn steer primitive distinct from lossy `session/cancel`.
