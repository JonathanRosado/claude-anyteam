# Gemini adapter feasibility

## Executive Summary

Plan A is **APPROVED** and should be the first implementation. It is the best fit for the current codebase because it can mirror `codex.run()` and reuse the existing team-protocol modules with limited surface-area changes. Plan B is also **APPROVED**, but only as a second transport layered on top of Plan A’s config, prompt, and wrapper-injection work; the ACP client is feasible because `app_server.py` already solves most of the hard transport problems. Plan C is **REJECTED** for the Gemini adapter track: it is technically possible, but it is a poor fit for a repo whose roadmap and architecture are explicitly CLI-first for Gemini, and it would force the adapter to own much more orchestration than any current backend.

---

## Plan A — one-shot `stream-json` exec

### APPROVED

**Feasibility score:** 8/10

### Structural analysis

This is the cleanest structural match to the current repo.

What already fits:
- `src/claude_anyteam/codex.py:run()` already defines the right result shape: one subprocess per task, parsed event stream, final text payload, optional session id, and tool-call telemetry via `CodexResult`.
- `schema_validation.py` already provides the exact fallback Gemini needs because Gemini CLI has no `--output-schema` equivalent.
- `protocol_io.py`, `messages.py`, `logger.py`, and most of the claim/idle/shutdown logic are backend-agnostic.
- `spawn_shim.py` already routes by teammate-name prefix, which is exactly how Gemini should enter the system.

What does **not** fit as written:
- `loop.py` is not backend-agnostic today. It hard-calls `codex_mod.feature_test()`, `codex_mod.run()`, and `codex_mod.app_server_invoke()`, and its state fields are Codex-specific.
- `registration.py` hardcodes Codex-facing member metadata (`model: "codex-cli"` and a prompt mentioning `codex exec`). A Gemini adapter cannot reuse that file unchanged.
- `prompts.py` cannot be reused verbatim for task/prose/plan prompts because Gemini MCP tools are exposed as `mcp_<server>_<tool>`, not bare `send_message` / `task_update` names.

So the plan is feasible, but only with explicit corrections in loop integration, registration metadata, and Gemini-specific prompts.

### Missed reuse

The plan correctly reuses `schema_validation.py`, `protocol_io.py`, `messages.py`, and the wrapper server concept, but it missed these reuse points:
- **`CodexResult` itself**: no Gemini-specific result type is needed.
- **`codex.py` event-classifier pattern**: the broad event matching logic is reusable, but with Gemini tool names (`mcp_anyteam_*`), not the current bare-tool constant set.
- **`env.py` generic vars**: `TEAM_ENV`, `NAME_ENV`, `CWD_ENV`, `POLL_ENV`, `COLOR_ENV`, `PLAN_MODE_ENV`, and `MODEL_ENV` are already backend-neutral enough to reuse.
- **`installer.py`**: no install-flow change is required just to add Gemini routing; the shim is already what Claude Code launches.
- **Existing test style**: the repo’s tests are currently top-level `tests/test_*.py` files. A new `tests/backends/gemini/` tree would be fine, but it is a new convention, not current practice.

### Critical corrections

1. **Do not patch the repo’s real `.gemini/settings.json`.**  
   The Codex adapter explicitly avoids mutating user config. Gemini cannot use Codex-style `-c mcp_servers...` overrides, but the safer equivalent is an adapter-owned Gemini home/config root, not editing the working tree. Use an adapter state directory under `~/.claude/teams/<team>/state/<agent>/gemini/`, write `settings.json` there, run Gemini with `HOME` pointed at that directory, and keep the task `cwd` set to the real repo.

2. **If `HOME` is overridden for Gemini, restore the real home for the wrapper MCP server.**  
   This matters because the wrapper ultimately talks to `claude_teams`, which resolves `~/.claude/...` from `Path.home()`. Gemini’s `mcpServers.<name>.env` support should be used to pass `HOME=<real_home>` to the wrapper subprocess while still passing wrapper identity via CLI args.

3. **Do not thread Gemini branches through the existing `loop.py` for v1.**  
   That file is too Codex-specific. The safer path is `src/claude_anyteam/backends/gemini/loop.py`, copied from `loop.py` with narrow substitutions for feature-test, prompts, invocation, and session state. Shared-loop extraction can happen later once two backends exist.

4. **Generalize `registration.py` before reusing it.**  
   Registration needs backend-specific member metadata. At minimum, `register()` should accept model/prompt labels from settings rather than hardcoding `codex-cli` and `codex exec`.

5. **Gemini needs backend-specific prompt builders.**  
   Reuse the current prompt structure, but the tool names in the prompt must be `mcp_anyteam_send_message`, `mcp_anyteam_task_update`, etc., not the bare names used by Codex.

6. **Reuse `CLAUDE_ANYTEAM_MODEL`; add only Gemini-specific binary/transport env vars.**  
   The shared `--model` flag and `CLAUDE_ANYTEAM_MODEL` env already fit Gemini. New env vars should be limited to things the shared config does not already represent, such as `CLAUDE_ANYTEAM_GEMINI_BINARY` and optional transport selection.

### Adjusted risks

- **Higher than stated:** config isolation and wrapper `HOME` leakage. This is the main Gemini-specific operational risk.
- **Higher than stated:** CLI flag drift. `--output-format stream-json` and `--resume` need a real startup probe, not just an assumption.
- **Added:** event-shape drift in `stream-json`; the parser must tolerate unknown events and non-JSON lines.
- **Added:** prompt/tool-name mismatch; Gemini will not infer that `send_message` means `mcp_anyteam_send_message`.
- **Removed:** “wrapper identity env might be stripped” is not a real blocker if wrapper identity is passed via `args`.

### Effort

**Estimated effort:** 3–4 days

---

## Full implementation spec — Plan A

### New files

- `src/claude_anyteam/backends/__init__.py`
- `src/claude_anyteam/backends/gemini/__init__.py`
- `src/claude_anyteam/backends/gemini/config.py`
- `src/claude_anyteam/backends/gemini/prompts.py`
- `src/claude_anyteam/backends/gemini/invoke_exec.py`
- `src/claude_anyteam/backends/gemini/loop.py`
- `src/claude_anyteam/backends/gemini/cli.py`

### Modified files

- `src/claude_anyteam/spawn_shim.py`
- `src/claude_anyteam/env.py`
- `src/claude_anyteam/registration.py`
- `pyproject.toml`

### Settings / CLI shape

`GeminiSettings` should be a separate frozen dataclass, not a mutation of the existing Codex settings. Reuse the shared fields (`team_name`, `agent_name`, `cwd`, `poll_interval_s`, `color`, `plan_mode_required`, `model`) and add:
- `gemini_binary: str = "gemini"`
- `transport: Literal["exec"] = "exec"` for Plan A
- auth selection fields only if needed for explicit non-env wiring
- backend registration labels such as `member_model_label` / `member_prompt_label`

`claude-anyteam-gemini` should mirror the current CLI flags for team/name/cwd/poll/color/plan-mode/model. No installer changes are required.

### Invocation contract

`invoke_exec.py` should expose:
- `feature_test()` — verify the binary exists and `--output-format` / `--resume` are available.
- `build_gemini_settings_home()` — create the adapter-owned Gemini config root.
- `build_wrapper_server_config()` — emit `mcpServers.anyteam` with:
  - resolved wrapper command
  - `args: ["--team", <team>, "--name", <agent>]`
  - `env: {"HOME": <real_home>}`
  - `trust: true`
- `run_exec()` — run `gemini -p <prompt> --output-format stream-json`, optionally `--resume <session_id>`, parse JSONL, count wrapper tool calls, capture the `init` session id, and return a `CodexResult`.

### Prompt / schema rules

- Always embed the task-complete or plan schema in the prompt using `inline_schema_prompt_fragment()`.
- Always validate the final Gemini text with `parse_and_validate()`.
- Retry once with a stricter prompt if validation fails.
- Prose prompts must tell Gemini to use `mcp_anyteam_send_message`, not bare `send_message`.

### Loop integration

`backends/gemini/loop.py` should be a v1 fork of the current `loop.py`.

Required differences:
- `GeminiLoopState` replaces Codex session fields with `gemini_session_id`.
- `run()` calls Gemini `feature_test()`.
- prose handling uses Gemini exec transport and Gemini prompt builders.
- task execution uses Gemini exec transport for both first-run and resumed runs.
- plan mode uses Gemini exec transport plus schema validation, same retry policy as the existing Codex path.

Everything else should stay structurally identical to the current loop:
- inbox drain
- task claim
- shutdown handling
- idle notification
- task-complete / task-blocked messaging

### Registration change

Refactor `registration.py` so the member entry’s `model` and `prompt` come from settings (or helper functions) instead of hardcoded Codex strings. Codex should keep its current values; Gemini should advertise something like `gemini-cli` and a Gemini-specific prompt description.

### Tests

Minimum required tests:
- `tests/test_gemini_exec_invocation_shape.py`
- `tests/test_gemini_stream_parse.py`
- `tests/test_gemini_settings_injection.py`
- `tests/test_gemini_prompts.py`
- `tests/test_gemini_loop_unit.py`
- extend `tests/test_spawn_shim.py`
- add a registration regression test covering backend-specific member metadata

### Exit criteria

Plan A is done when:
- a `gemini-*` teammate is routed by the shim
- the adapter registers without Codex branding
- task/prose/plan flows all complete through Gemini exec
- wrapper MCP tools are callable from Gemini
- schema validation and retry behavior match the current Codex guarantees

---

## Plan B — long-lived ACP JSON-RPC daemon

### APPROVED

**Feasibility score:** 6/10

### Structural analysis

This is feasible because the repo already contains the right transport template: `app_server.py`.

What fits well:
- `AppServerClient` already implements the exact reader-thread / pending-request / notification-queue pattern ACP needs.
- `codex.py.app_server_invoke()` already models the outer control shape: start client, initialize, create/load session, send work, drain notifications, optionally steer, return `CodexResult`.
- `SteerQueue` already exists and is reusable if ACP turns out to support a real mid-task follow-up primitive.

What still makes it harder than Plan A:
- ACP semantics are not Codex App Server semantics. The transport pattern is reusable; the session and turn model is not.
- The current repo has no Gemini backend yet, so Plan B still depends on the same settings, prompt, registration, and wrapper-injection work that Plan A must introduce first.
- Mid-task “steer parity” is not proven. The plan is structurally viable even without exact parity, but not if parity is treated as guaranteed.

### Missed reuse

- **`app_server.py` should be copied, not re-imagined.** The plan is right to mirror it, but it should say that explicitly.
- **Plan A’s config/home/wrapper-injection helpers should be shared.** ACP should not get its own parallel settings-file logic.
- **`SteerQueue` is reusable.** No new queue type is needed.
- **The same registration generalization from Plan A is still required.**

### Critical corrections

1. **Treat Plan B as an extension of Plan A, not a separate foundation.**  
   `GeminiSettings`, Gemini prompts, backend registration labels, shim routing, and wrapper config should be shared.

2. **Copy `AppServerClient`’s transport mechanics nearly verbatim.**  
   The reader loop, stderr drain, pending-response bookkeeping, and close semantics are already solved.

3. **ACP-side MCP injection must have a tested fallback.**  
   If Gemini ACP accepts inline MCP config at `initialize`/`session/new`, use it. If not, fall back to the same adapter-owned Gemini home/settings path from Plan A.

4. **Do not promise mid-task steer parity in v1.**  
   If ACP supports a same-session follow-up while a task is live, use it. If not, queue the message and inject it at the next safe boundary on the same session, or cancel/re-prompt if that is the only supported path.

5. **Feature-test `--acp` and `--experimental-acp`.**  
   Startup probing must decide which flag the installed binary supports.

### Adjusted risks

- **Higher than stated:** protocol drift between Gemini ACP docs and installed binaries.
- **Higher than stated:** missing or weaker-than-expected mid-turn steer support.
- **Added:** session-load fallback behavior needs explicit observability so silent context loss is diagnosable.
- **Lower than stated:** stdout corruption / stray non-JSON lines; `app_server.py` already shows the correct tolerant parser pattern.

### Effort

**Estimated effort:** 4–5 days after Plan A lands

---

## Full implementation spec — Plan B

### New files

- `src/claude_anyteam/backends/gemini/acp_client.py`
- `src/claude_anyteam/backends/gemini/invoke_acp.py`

### Modified files beyond Plan A

- `src/claude_anyteam/backends/gemini/config.py` — allow `transport="acp"`
- `src/claude_anyteam/backends/gemini/loop.py` — add ACP task path
- `src/claude_anyteam/backends/gemini/cli.py` — add `--transport exec|acp`

### Transport design

`acp_client.py` should be a Gemini-specific adaptation of `AppServerClient` with:
- `start()` launching `gemini --acp` or `gemini --experimental-acp`
- `initialize()`
- `authenticate()` if Gemini ACP requires it
- `new_session()`
- `load_session()`
- `prompt()`
- `cancel()`

The concurrency and IO model should stay the same as `AppServerClient`:
- one reader thread
- one stderr drain thread
- synchronous `request()` over async stdio JSON-RPC
- notification queue for long-running work

### Session model

- first task: `new_session()`
- subsequent tasks: `load_session(session_id)`
- on load failure: log `gemini.acp_session_load_fallback` and create a fresh session
- `GeminiLoopState` stores only ACP session state, not Codex thread ids

### Wrapper / MCP strategy

Preferred order:
1. inline ACP-session MCP config, if verified by implementation probe
2. otherwise, Plan A’s adapter-owned Gemini home/settings file

Either way:
- use server alias `anyteam`
- pass wrapper identity through CLI args
- preserve the real `HOME` for the wrapper subprocess

### Loop behavior

Use the same `backends/gemini/loop.py` module as Plan A, but add an ACP transport branch.

Rules:
- prose handling may use ACP or stay on exec transport; either is acceptable, but be explicit and keep it consistent
- task execution should prefer ACP when `transport="acp"`
- mid-task inbox polling can reuse the `_execute_task_app_server()` pattern from the existing loop
- if real live steer is unsupported, queue prose and inject it as the next follow-up on the same session

### Tests

Minimum required tests:
- `tests/test_gemini_acp_client.py`
- `tests/test_gemini_acp_invoke.py`
- `tests/test_gemini_acp_notifications.py`
- extend `tests/test_gemini_loop_unit.py`
- extend `tests/test_spawn_shim.py` only if transport selection affects routing or argv

### Exit criteria

Plan B is done when:
- ACP startup is feature-tested reliably
- session create/load works across multiple tasks in one adapter process
- wrapper MCP tools remain callable under ACP
- mid-task messages are either injected live or explicitly queued at the next session boundary
- ACP failures degrade to a blocked task with a clear reason, not silent hangs

---

## Plan C — in-process `google-genai` SDK backend

### REJECTED

**Feasibility score:** 4/10

### Structural analysis

This plan is technically possible, but it is the weakest structural fit to this repo.

Why:
- `docs/roadmap.md` explicitly places Gemini on the CLI track (`gemini`) and reserves API-direct work for a future **Generic API adapter**.
- `docs/architecture.md` describes new model adapters as CLI-native peers routed by the shim. Plan C is not a CLI adapter; it is the first API-direct backend.
- The current codebase delegates tool orchestration and long-lived session behavior to the model CLI. Plan C would move that responsibility into claude-anyteam itself: function-call loop, transcript management, retry behavior, and tool result reinsertion.

That does not make Plan C impossible. It makes it the wrong plan for the current Gemini adapter milestone.

### Missed reuse

The strongest reusable idea in the plan is **`tool_surface.py` extraction**. That refactor is good on its own and would make both the wrapper server and any future API-direct adapter cleaner.

Other valid reuse:
- `protocol_io.py`
- `messages.py`
- `registration.py` once generalized
- `prompts.py` structure
- `CodexResult` as the backend result envelope

### Critical corrections

If this work is ever revived, it should be reframed as a separate roadmap item:

1. **Make it the Generic API adapter, not the Gemini adapter.**
2. **Extract `tool_surface.py` first as an independent refactor.**
3. **Define a backend-owned tool/function-call loop explicitly before writing code.**
4. **Specify transcript/session persistence and pruning up front.**
5. **Add the `google-genai` dependency only when that adapter is intentionally adopted.**

### Adjusted risks

- **Much higher than stated:** adapter-owned orchestration complexity.
- **Added:** direct conflict with the repo’s CLI-first Gemini roadmap.
- **Added:** the work naturally overlaps with the future generic API adapter and risks creating two competing abstractions.
- **Added:** wrapper/tool-surface extraction touches a safety-critical boundary and deserves its own test-focused change.

### Effort

**Estimated effort:** 5–7 days, and it should be treated as a separate project

---

## Implementation order recommendation

1. **Shared prep for approved plans**
   - add Gemini shim routing
   - generalize registration metadata away from Codex-only labels
   - add Gemini settings / prompt builders / wrapper-config helpers

2. **Implement Plan A**
   - smallest structural delta
   - proves auth, wrapper MCP injection, Gemini prompts, schema validation, and task/prose/plan flows

3. **Implement Plan B on top of Plan A**
   - reuse the same Gemini backend package
   - add ACP transport only after exec transport works end-to-end

4. **Do not implement Plan C on the Gemini track**
   - if desired later, open a separate Generic API adapter task and cherry-pick the `tool_surface.py` idea into it
