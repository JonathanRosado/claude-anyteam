# Gemini feasibility plans

Basis: `docs/internal/gemini-research-official.md`, `docs/internal/gemini-research-reverse.md`, and the current Codex adapter in `src/claude_anyteam/codex.py`, `src/claude_anyteam/app_server.py`, `src/claude_anyteam/loop.py`, `src/claude_anyteam/wrapper_server.py`, and `src/claude_anyteam/spawn_shim.py`.

## Plan A — Headless Gemini CLI subprocess (`gemini -p ... --output-format stream-json`)

### Architecture overview

Plan A is the fastest path because it keeps the current control plane almost intact. `src/claude_anyteam/loop.py` already knows how to poll inboxes, claim tasks, update team state, and mark failures; the smallest useful Gemini integration is to add a new runner that mirrors `src/claude_anyteam/codex.py::run` but swaps the subprocess shape to Gemini CLI headless mode. In practice that means a new `gemini_headless.py` that launches `gemini -p <prompt> --output-format stream-json`, parses `init/message/tool_use/tool_result/result/error` events, captures the final assistant text, and returns a `CodexResult`-shaped object so the rest of `loop.py` barely changes.

This plan deliberately does **not** reuse `src/claude_anyteam/app_server.py`; that file exists to speak Codex App Server's `thread/start` / `turn/start` / `turn/steer` protocol, and Gemini's official CLI-equivalent for long-lived control is ACP, not headless mode. Instead, Plan A treats `codex.py` as the template for subprocess lifecycle, `schema_validation.py` as the template for Python-side schema enforcement, and `wrapper_server.py` as the unchanged six-tool team-protocol surface. The only new persistent state is a Gemini-owned home/config/session directory per teammate so we can inject MCP config without mutating the user's real `~/.gemini`.

### Files to create/modify

- `src/claude_anyteam/gemini_headless.py` — one-shot Gemini CLI runner mirroring `codex.py::run`.
- `src/claude_anyteam/gemini_settings.py` — writes adapter-owned `.gemini/settings.json` and session home.
- `src/claude_anyteam/loop.py` — add Gemini dispatch and `LoopState.gemini_session_id`.
- `src/claude_anyteam/config.py` — add Gemini binary/backend selection and auth-mode knobs.
- `src/claude_anyteam/cli.py` — expose `--backend gemini-headless` / `--gemini-binary`.
- `src/claude_anyteam/prompts.py` or `src/claude_anyteam/prompts_gemini.py` — Gemini-specific task/prose/plan prompts.
- `src/claude_anyteam/spawn_shim.py` — route `gemini-*` names to the Gemini adapter binary.
- `pyproject.toml` — publish a `gemini-anyteam` console script.
- `hooks/session-start.sh` — update orientation text so the plugin advertises Gemini routing too.
- `tests/test_gemini_headless_invocation.py` — argv/event parsing and exit-code coverage.
- `tests/test_gemini_resume_dispatch.py` — resume-session carry-forward rules.
- `tests/test_spawn_shim.py` — Gemini route selection and fallback behavior.

### MCP injection approach

Use an adapter-owned Gemini home directory such as `~/.claude-anyteam/gemini/<team>/<agent>/`, write `$HOME/.gemini/settings.json` there, and spawn `gemini` with that `HOME` while keeping `cwd` pointed at the real workspace. Concrete settings payload:

```json
{
  "mcpServers": {
    "claude-anyteam-wrapper": {
      "command": "/absolute/path/to/claude-anyteam-wrapper",
      "args": ["--team", "my-team", "--name", "gemini-alice"],
      "env": {
        "CLAUDE_ANYTEAM_TEAM": "my-team",
        "CLAUDE_ANYTEAM_NAME": "gemini-alice"
      },
      "timeout": 600000,
      "trust": true
    }
  }
}
```

Notes: use a dash alias (`claude-anyteam-wrapper`) rather than the Codex-side underscore name; Gemini docs warn about underscore aliases. Keep the wrapper schema flat and small; the current six-tool `wrapper_server.py` contract is a good fit.

### Schema-constraint handling

Gemini CLI does not have a documented `--output-schema` equivalent, so Plan A should generalize the existing `src/claude_anyteam/schema_validation.py` pattern instead of pretending the CLI can enforce shape. Put the schema inline in the prompt, capture the final assistant text from the headless JSON stream, parse it as JSON, validate against `schemas/task-complete.schema.json` or `schemas/plan.schema.json`, and retry once with a stricter prompt exactly like the current `codex exec resume` path in `loop.py::_invoke_codex_for_task`.

If Gemini emits valid outer JSON but noisy inner prose, keep the validation boundary at the inner payload only. If MCP tool schemas turn out to trip Gemini's strict schema conversion, add a tiny sanitizer layer in front of `wrapper_server.py` rather than widening the tool surface.

### Session continuity strategy

Store one Gemini session lineage per teammate in `LoopState.gemini_session_id`, backed by the adapter-owned Gemini home so sessions survive across invocations. First task: plain `gemini -p ...`. Later tasks: `gemini --resume <session-id> -p ...`; if exact ID capture proves flaky, fall back to `gemini --resume` latest inside that dedicated home.

For restart resilience, persist `{team, agent, session_id}` to a small adapter state file such as `~/.claude-anyteam/gemini/<team>/<agent>/state.json`. That keeps Plan A closer to the repo's existing in-memory `codex_session_id`, but upgrades it from process-lifetime only to opt-in on-disk recovery.

### Auth wiring (env vars)

- Developer API path: `GEMINI_API_KEY`.
- Vertex path: `GOOGLE_CLOUD_PROJECT` or `GOOGLE_CLOUD_PROJECT_ID`, `GOOGLE_CLOUD_LOCATION`, and `GOOGLE_APPLICATION_CREDENTIALS`.
- Vertex hygiene: when using Vertex auth, explicitly unset conflicting `GOOGLE_API_KEY` / `GEMINI_API_KEY` before spawning `gemini`.
- Do **not** pass model auth into the MCP wrapper config; only the Gemini subprocess needs it.

### Spawn shim diff (pattern, binary name)

- Pattern: `^gemini-`
- Binary: `gemini-anyteam`
- Dispatch shape: `gemini-anyteam --backend gemini-headless --team <team> --name <agent>`
- New env override: `CLAUDE_ANYTEAM_GEMINI_BINARY` (fallback to `gemini-anyteam` on `PATH`)

### Test list

- `tests/test_gemini_headless_invocation.py` — correct `gemini -p --output-format stream-json` argv and timeout handling.
- `tests/test_gemini_headless_events.py` — parse `tool_use`, `tool_result`, `result`, `error` events.
- `tests/test_gemini_mcp_settings.py` — writes isolated `$HOME/.gemini/settings.json` with the wrapper entry.
- `tests/test_gemini_resume_dispatch.py` — first task fresh, second task `--resume <id>`.
- `tests/test_gemini_schema_retry.py` — invalid JSON first pass, strict retry second pass.
- `tests/test_spawn_shim.py` — `gemini-*` routes to Gemini; `codex-*` and native Claude still behave unchanged.
- `tests/test_plugin_bundle.py` / hook coverage — session-start messaging still succeeds after adding Gemini binaries.

### Risks with mitigations

- **CLI version drift on headless flags.** Mitigation: add a Gemini `feature_test()` like `codex.py::feature_test` that probes `--output-format` and `--resume` before startup.
- **MCP schema rejection kills the whole server.** Mitigation: keep the wrapper tool schemas shallow; add a preflight sanitizer if the stock FastMCP schema is too rich.
- **OAuth/headless auth instability.** Mitigation: treat API-key or Vertex service-account auth as the production path; do not make browser OAuth the default.
- **No mid-task `turn/steer` equivalent.** Mitigation: ship this as a fresh-exec-style backend first and document that Gemini v1 parity is task-to-task memory, not live steer.

---

## Plan B — Gemini ACP daemon (`gemini --acp`)

### Architecture overview

Plan B is the closest conceptual match to the Codex App Server path. The clean version is to split the transport code in `src/claude_anyteam/app_server.py` into a provider-neutral stdio JSON-RPC helper, keep `codex.py::app_server_invoke` as one backend implementation, and add `gemini_acp.py` / `gemini_acp_client.py` for Gemini's ACP wire methods (`initialize`, `authenticate`, `session/new`, `session/load`, `session/prompt`, `session/cancel`). `loop.py` would then choose a backend implementation instead of directly assuming Codex, while still owning task claiming, shutdown, blocked-task behavior, and mailbox I/O.

The important difference is that ACP does **not** expose Codex's documented `turn/start` + `turn/steer` model. So this plan gives us a long-lived Gemini process and session continuity, but not a drop-in mid-turn steer primitive. The best v1 mapping is: keep one ACP session per teammate, let `session/prompt` execute the current task, and treat interruptions as queued follow-up prompts or `session/cancel` + replay. That means `src/claude_anyteam/app_server.py` is reusable at the transport layer, but `src/claude_anyteam/codex.py::app_server_invoke` is **not** reusable as-is.

### Files to create/modify

- `src/claude_anyteam/jsonrpc_stdio.py` — provider-neutral request/response + notification loop factored out of `app_server.py`.
- `src/claude_anyteam/gemini_acp_client.py` — ACP client wrappers for `initialize`, `authenticate`, `session/new`, `session/load`, `session/prompt`, `session/cancel`.
- `src/claude_anyteam/gemini_acp.py` — high-level Gemini ACP invocation and result shaping.
- `src/claude_anyteam/loop.py` — add a Gemini ACP execution path and ACP session state.
- `src/claude_anyteam/config.py` — backend selector (`codex-app-server`, `gemini-acp`, etc.).
- `src/claude_anyteam/cli.py` — `--backend gemini-acp` and Gemini binary flags.
- `src/claude_anyteam/gemini_settings.py` — stable ACP config/home writer shared with Plan A.
- `src/claude_anyteam/spawn_shim.py` — `gemini-*` route to ACP-capable binary.
- `pyproject.toml` — publish `gemini-anyteam` or equivalent Gemini adapter script.
- `tests/test_gemini_acp_client.py` — JSON-RPC request/response and notification dispatch.
- `tests/test_gemini_acp_session_reload.py` — `session/new`/`session/load` behavior.
- `tests/test_gemini_acp_cancel.py` — interruption/cancel fallback semantics.

### MCP injection approach

ACP appears to inherit normal Gemini CLI config, so use the same adapter-owned home and prewritten settings file as Plan A, but load it once when the ACP subprocess starts:

```json
{
  "mcpServers": {
    "claude-anyteam-wrapper": {
      "command": "/absolute/path/to/claude-anyteam-wrapper",
      "args": ["--team", "my-team", "--name", "gemini-alice"],
      "env": {
        "CLAUDE_ANYTEAM_TEAM": "my-team",
        "CLAUDE_ANYTEAM_NAME": "gemini-alice"
      },
      "timeout": 600000,
      "trust": true
    }
  }
}
```

The wrapper stays external and narrow. Do not let Gemini auto-discover arbitrary project MCP servers in this mode; the teammate should see exactly one coordination surface unless a later feature explicitly widens it.

### Schema-constraint handling

ACP is still a Gemini CLI surface, so assume the same limitation as Plan A: no documented Codex-style per-turn output schema enforcement. Reuse the current Python-side pattern from `schema_validation.py`: inline the schema in the prompt, parse the final assistant payload from ACP, validate it in Python, retry once, then mark blocked.

If later ACP releases expose a native JSON schema field, hide it behind a feature probe and keep Python validation as the safety net. That preserves the repo's existing “fail closed on bad structured output” behavior.

### Session continuity strategy

Create one ACP session per teammate and keep its session ID in adapter state, analogous to `LoopState.app_server_last_thread_id`. First boot calls `session/new`; normal task turns reuse that session; restart recovery calls `session/load` from a persisted `state.json` under `~/.claude-anyteam/gemini/<team>/<agent>/`.

Because ACP is long-lived, this plan can also keep tool metadata and filesystem context warm, reducing repeated CLI startup cost. The trade-off is harder crash recovery: if the ACP subprocess wedges, the adapter must detect it, restart Gemini, and reload the last saved session ID.

### Auth wiring (env vars)

- Developer API path: `GEMINI_API_KEY`.
- Vertex path: `GOOGLE_CLOUD_PROJECT` / `GOOGLE_CLOUD_PROJECT_ID`, `GOOGLE_CLOUD_LOCATION`, `GOOGLE_APPLICATION_CREDENTIALS`.
- If ACP requires an explicit auth handshake, source credentials from the same env and fail fast at startup rather than during the first task.
- When using Vertex auth, scrub `GOOGLE_API_KEY` / `GEMINI_API_KEY` from the ACP subprocess env before launch.

### Spawn shim diff (pattern, binary name)

- Pattern: `^gemini-`
- Binary: `gemini-anyteam`
- Dispatch shape: `gemini-anyteam --backend gemini-acp --team <team> --name <agent>`
- New env override: `CLAUDE_ANYTEAM_GEMINI_BINARY`

### Test list

- `tests/test_gemini_acp_client.py` — request IDs, notification queueing, timeout behavior.
- `tests/test_gemini_acp_session_reload.py` — persist/reload ACP session IDs.
- `tests/test_gemini_acp_prompt_flow.py` — task prompt in, structured result out.
- `tests/test_gemini_acp_cancel.py` — interruption fallback via `session/cancel`.
- `tests/test_gemini_mcp_settings.py` — wrapper config injected before ACP startup.
- `tests/test_gemini_schema_retry.py` — same validation/retry semantics as Plan A.
- `tests/test_spawn_shim.py` — `gemini-*` route selection.

### Risks with mitigations

- **No documented `turn/steer` equivalent.** Mitigation: explicitly scope v1 ACP interruptions to `session/cancel` + replay or queued follow-up prompts.
- **CLI/doc flag churn (`--acp` vs `--experimental-acp`).** Mitigation: startup feature probe against the actual binary, not docs alone.
- **Persistent-process failure modes.** Mitigation: watchdog + restart + `session/load` recovery.
- **Higher implementation cost than Plan A.** Mitigation: factor transport code once (`jsonrpc_stdio.py`) and keep the rest of the control loop unchanged.

---

## Plan C — Hybrid CLI worker + `google-genai` schema finisher

### Architecture overview

Plan C keeps the strongest operational parts of Plan A and uses the official SDK only where the CLI is weakest: final structured output. The worker half still looks like `src/claude_anyteam/codex.py::run`, except the subprocess is Gemini CLI headless mode with native MCP and native `--resume`. The finisher half is a new in-process `gemini_structured.py` that takes the raw final assistant text (or a compact transcript summary), calls `google-genai` with `response_mime_type=application/json` plus `response_json_schema`, and returns the exact `task-complete` or `plan` object that `loop.py` expects.

This plan intentionally leaves `src/claude_anyteam/app_server.py` out of the critical path. `wrapper_server.py`, `spawn_shim.py`, the team protocol in `loop.py`, and Gemini CLI session continuity all stay exactly as in Plan A. The only extra moving part is the finisher step, which means we get native Gemini CLI MCP/tool use without trusting prompt-only schema discipline. In repo terms: `codex.py` remains the subprocess template, `schema_validation.py` becomes a final safety net instead of the primary enforcement mechanism, and the SDK handles only the last mile.

### Files to create/modify

- `src/claude_anyteam/gemini_headless.py` — same worker runner as Plan A.
- `src/claude_anyteam/gemini_structured.py` — `google-genai` finisher for schema-critical outputs.
- `src/claude_anyteam/gemini_settings.py` — isolated Gemini CLI config/home writer.
- `src/claude_anyteam/loop.py` — call the finisher only for task-complete / plan-mode results.
- `src/claude_anyteam/config.py` — add SDK/CLI auth knobs and backend mode selection.
- `src/claude_anyteam/cli.py` — `--backend gemini-hybrid`.
- `src/claude_anyteam/prompts.py` or `prompts_gemini.py` — separate worker prompts from finisher prompts.
- `src/claude_anyteam/spawn_shim.py` — `gemini-*` route to hybrid mode.
- `pyproject.toml` — add `google-genai` dependency and Gemini adapter script.
- `tests/test_gemini_hybrid_finish.py` — valid worker output bypasses finisher; invalid worker output routes through finisher.
- `tests/test_gemini_hybrid_auth.py` — CLI and SDK share the same auth-mode selection.

### MCP injection approach

Use the same isolated Gemini CLI settings injection as Plan A, because the worker still depends on CLI-native MCP:

```json
{
  "mcpServers": {
    "claude-anyteam-wrapper": {
      "command": "/absolute/path/to/claude-anyteam-wrapper",
      "args": ["--team", "my-team", "--name", "gemini-alice"],
      "env": {
        "CLAUDE_ANYTEAM_TEAM": "my-team",
        "CLAUDE_ANYTEAM_NAME": "gemini-alice"
      },
      "timeout": 600000,
      "trust": true
    }
  }
}
```

The SDK finisher does not need MCP. It should see only the raw worker output and the target JSON schema.

### Schema-constraint handling

Worker phase: prompt Gemini CLI to return a compact, machine-friendly completion summary, but do **not** trust it as the only schema boundary. Finisher phase: call `google-genai` with the repo's existing JSON schemas and force the response into `application/json`. If the finisher returns a valid object, `loop.py` proceeds; if not, fall back to `schema_validation.py` + one stricter finisher retry before marking blocked.

This gives the best schema guarantees of the three plans without giving up Gemini CLI's session store or tool support. It also isolates schema bugs from the worker prompt, which is useful if Gemini CLI remains chatty even in JSON-oriented headless mode.

### Session continuity strategy

Use Gemini CLI sessions exactly as in Plan A: keep `LoopState.gemini_session_id`, store sessions under the adapter-owned Gemini home, and resume worker tasks with `gemini --resume <id>`. The finisher is stateless by design and should not participate in task memory; it is a pure formatter/validator.

That split keeps the continuity story easy to reason about: the worker remembers the codebase and prior task context; the finisher remembers nothing and cannot contaminate future turns.

### Auth wiring (env vars)

- Worker CLI: `GEMINI_API_KEY` **or** Vertex bundle (`GOOGLE_CLOUD_PROJECT` / `GOOGLE_CLOUD_PROJECT_ID`, `GOOGLE_CLOUD_LOCATION`, `GOOGLE_APPLICATION_CREDENTIALS`).
- Finisher SDK: use the **same** auth mode as the worker; do not let CLI and SDK point at different projects/accounts.
- Vertex hygiene: scrub `GOOGLE_API_KEY` / `GEMINI_API_KEY` when intentionally using service-account/ADC Vertex auth.
- Add a startup check that both the CLI probe and a trivial SDK probe succeed under the chosen auth mode.

### Spawn shim diff (pattern, binary name)

- Pattern: `^gemini-`
- Binary: `gemini-anyteam`
- Dispatch shape: `gemini-anyteam --backend gemini-hybrid --team <team> --name <agent>`
- New env override: `CLAUDE_ANYTEAM_GEMINI_BINARY`

### Test list

- `tests/test_gemini_headless_invocation.py` — worker subprocess shape.
- `tests/test_gemini_hybrid_finish.py` — invalid worker JSON triggers SDK finisher.
- `tests/test_gemini_hybrid_skip_finish.py` — already-valid worker JSON skips the extra call.
- `tests/test_gemini_hybrid_auth.py` — CLI/SDK auth-mode alignment.
- `tests/test_gemini_resume_dispatch.py` — worker session carry-forward unaffected by the finisher.
- `tests/test_spawn_shim.py` — `gemini-*` dispatch to hybrid mode.

### Risks with mitigations

- **Two-model-call latency/cost on task completion.** Mitigation: only invoke the finisher when the worker output is absent/invalid, or make it opt-in for plan mode first.
- **Worker/finisher disagreement.** Mitigation: feed the finisher only the worker's final answer plus the exact schema; do not let it reinterpret the entire task from scratch.
- **Dual-stack auth bugs.** Mitigation: single auth-mode config, single startup health check that exercises both surfaces.
- **More dependencies than Plan A.** Mitigation: keep the SDK isolated to one module and one narrow purpose.

---

## Comparison table

| Plan | Main transport | Schema strength | Session continuity | Mid-task parity | Implementation cost | Main risk |
|---|---|---:|---:|---:|---:|---|
| **A. Headless CLI** | `gemini -p ... --output-format stream-json` | Medium | Good (`--resume`) | Low | **Low** | CLI churn + prompt-only schema discipline |
| **B. ACP** | `gemini --acp` stdio JSON-RPC | Medium | **High** | Medium-low | **High** | No true `turn/steer` equivalent; protocol churn |
| **C. Hybrid CLI + SDK** | Headless CLI worker + `google-genai` finisher | **High** | Good (`--resume` on worker) | Low | Medium | Extra latency/cost and dual auth surface |

## Implementation-order recommendation

1. **Start with Plan A.** It is the smallest diff against `codex.py::run` + `loop.py`, proves spawn-shim routing, auth, MCP injection, and session resume quickly, and gives us a real Gemini teammate sooner.
2. **Promote to Plan C if schema compliance becomes the blocker.** Plan C preserves Plan A's operational shape and only adds an SDK finisher where the CLI is weakest.
3. **Treat Plan B as a second-wave R&D track, not first ship.** It is the most elegant long-lived architecture, but ACP currently lacks Codex-style `turn/steer` parity and has the highest protocol uncertainty.

**Recommended order: A → C → B.**
