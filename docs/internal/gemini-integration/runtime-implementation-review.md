# Gemini runtime + implementation review

Date: 2026-04-24
Reviewer: codex-gemini-runtime-reviewer
Scope: Researcher A runtime findings and the Gemini-side implementation in `src/claude_anyteam/backends/gemini/` plus routing/auth/session behavior.

## Runtime research spot-check

I spot-checked the installed CLI in this workspace:

- `gemini --version` reports `0.39.0`.
- `gemini --help` confirms `--prompt`, `--output-format text|json|stream-json`, `--approval-mode default|auto_edit|yolo|plan`, `--acp`, and `--resume`.
- A live built-in tool run with `--output-format stream-json --approval-mode yolo` emitted `init`, `message` user echo, `tool_use`, `tool_result` without `output`, assistant `message` delta, and terminal `result`, matching the runtime doc's tool-result caveat.
- The corrected ACP section now uses `session/new`, `session/load`, `session/prompt`, and `session/cancel` wording and documents stdout preamble pollution. I did not implement or fully probe ACP task flow as part of this review.

Overall, the runtime document is directionally correct for Plan A headless integration. The main caveat is auth: the doc correctly says real user OAuth lives under `~/.gemini`, but implementation must not treat symlinked credential files alone as sufficient when it isolates `HOME`.

## Correctness findings

### 1. Blocking: isolated HOME auth is incomplete for OAuth users

`invoke.write_mcp_settings()` creates an isolated `$HOME/.gemini/settings.json` containing only `mcpServers`, then symlinks/copies selected real auth/cache files (`oauth_creds.json`, `google_accounts.json`, `projects.json`, `trustedFolders.json`, `installation_id`). It deliberately does **not** copy/merge the real `~/.gemini/settings.json` auth selection.

Empirical reproduction using the same shape as the adapter:

```bash
TMP=$(mktemp -d)
mkdir -p "$TMP/.gemini"
printf '{"mcpServers":{}}\n' > "$TMP/.gemini/settings.json"
for f in oauth_creds.json google_accounts.json projects.json trustedFolders.json installation_id; do
  [ -e "$HOME/.gemini/$f" ] && ln -s "$HOME/.gemini/$f" "$TMP/.gemini/$f"
done
HOME="$TMP" gemini --prompt 'Reply exactly: OK' --output-format stream-json --approval-mode yolo
```

Observed exit code: `41`.

Observed stderr:

```text
Please set an Auth method in your /tmp/.../.gemini/settings.json or specify one of the following environment variables before running: GEMINI_API_KEY, GOOGLE_GENAI_USE_VERTEXAI, GOOGLE_GENAI_USE_GCA
```

Impact: Gemini teammates will fail for normal OAuth-authenticated users unless they also provide env-var auth. This is not just a limitation; it breaks the default signed-in CLI path described by the research. Fix by preserving the real auth-selection settings while injecting adapter MCP config, for example: read real `~/.gemini/settings.json`, copy safe auth-related keys into the isolated settings, then overlay `mcpServers.anyteam` without copying user MCP servers; or require env auth at feature test/startup and make OAuth unsupported explicitly.

### 2. Blocking/high: default Gemini home is under `.cache`, so Gemini sessions and credentials are adapter-private but not documented enough at runtime

`_default_gemini_home()` uses `Path.home() / ".cache" / "claude-anyteam" / "gemini" / team / agent`, then runs Gemini with `HOME` set there. That is a good non-mutation strategy, but it means Gemini's `~/.gemini/tmp/<project>/chats` session state is not in the user's normal `~/.gemini`. The implementation does not log the isolated home path in `gemini.invoke`, and failures like invalid resume mention the isolated path only via Gemini stderr. This is diagnosable but rough. Add `gemini_home` to logs and docs, and consider a startup auth probe that can fail with a clear adapter-level message.

### 3. Medium: Gemini feature test only checks flags, not auth or wrapper MCP viability

`feature_test()` validates binary presence and help flags. It does not detect the auth failure above, nor whether Gemini can load the generated MCP server. Codex's feature test probes wrapper importability; Gemini currently waits until first task/prose turn to discover auth/MCP setup failures. A cheap auth-free MCP probe may not exist, but an adapter-owned settings validation and auth-method check would catch the current blocking issue before registration.

### 4. Medium: parser is tolerant enough for Plan A, but final-text extraction assumes all assistant deltas are the desired final answer

For the live built-in tool sample I ran, Gemini did not emit pre-tool assistant commentary in `stream-json`, so concatenating assistant deltas worked. However, if Gemini emits multiple assistant message segments across a turn (for example explanatory text plus final JSON), `_extract_json_candidate()` only strips fences and will fail schema validation. The retry path mitigates this. This is acceptable for v1, but tests should include a stream with a non-final assistant message if Gemini ever emits that shape.

### 5. Medium: tool-call translation is count-only and correctly does not assume `tool_result.output`

The implementation stores raw events and counts `tool_use`. It does not try to synthesize tool results or parse missing `output`, which is correct given the runtime doc and live built-in sample. MCP result payloads are preserved in raw events for diagnostics.

### 6. Low/known gap: no ACP / no mid-turn steering

The implementation is Plan A only and documents no `turn/steer` parity in `docs/gemini-adapter-limitations.md`. That is honest and matches runtime research. Do not add any docs implying Gemini has Codex app-server parity until ACP `session/prompt` / `session/cancel` behavior is actually implemented and tested with stdout preamble tolerance.

## Recommended follow-up tasks

1. Fix isolated OAuth auth handling before considering Gemini runtime production-ready.
2. Add a Gemini startup auth/settings validation test that reproduces the missing auth-method failure without requiring network.
3. Add adapter logs for `gemini_home` and possibly selected auth mode (without secrets).
4. If ACP is pursued later, implement a JSON-RPC reader that filters non-JSON stdout preamble lines before JSON-RPC envelopes.
