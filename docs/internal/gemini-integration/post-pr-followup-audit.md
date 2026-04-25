# Gemini ACP post-PR follow-up audit

Date: 2026-04-24 local / 2026-04-25 UTC  
Workspace: `/home/rosado/Projects/codex-teammate`  
Installed CLI: `/usr/local/bin/gemini`, version `0.39.0`

## Questions

1. Are ACP sessions properly cleaned up on adapter shutdown — is `close()` called, is the subprocess reaped, and are there no orphan `gemini --acp` processes?
2. Is there a feasible per-teammate or per-task plan-mode opt-out for untrusted task text via ACP `session/set_mode`?

## Short answers

1. **Normal turn cleanup: yes.** The ACP invocation path creates a `GeminiAcpClient` per turn and always calls `client.close()` in `finally`. Empirical probes showed the `gemini --acp` process exits after `close()`, with no matching ACP process left afterward. With the adapter's `GEMINI_CLI_NO_RELAUNCH=true` environment, Gemini ran as a single node process and was reaped cleanly.
2. **Adapter shutdown while idle: yes by construction.** There is no long-lived ACP subprocess between turns, so idle shutdown has no ACP child to reap.
3. **In-flight graceful shutdown: delayed, not immediate.** The Gemini loop's signal handler sets `shutdown_requested`, but if a synchronous ACP turn is already blocked in `session/prompt`, shutdown does not cancel/reap that subprocess until the prompt returns or times out. The `finally` cleanup still runs on ordinary return/exception, but SIGTERM is not currently translated into ACP `session/cancel`.
4. **Abrupt process death: no guarantee.** If the Python adapter is killed with SIGKILL or the host dies, Python `finally` blocks do not run. This is outside normal graceful shutdown semantics.
5. **Plan-mode opt-out is feasible.** Gemini ACP advertises `plan` mode and accepts `session/set_mode` with `modeId: "plan"`. A real probe set plan mode and then asked Gemini to run `pwd`; no `tool_call` events were emitted, and the assistant reported the shell tool was unavailable. The current adapter, however, always sets ACP sessions to `yolo`, so a small API/config change is required.

## Current implementation review

Relevant runtime flow:

- `src/claude_anyteam/backends/gemini/acp.py::run()` constructs one `GeminiAcpClient` per call.
- It calls `client.start()`, `initialize()`, optional `authenticate()`, `session/load` or `session/new`, `session/set_mode`, optional `session/set_model`, then `session/prompt()`.
- It catches timeouts and generic exceptions and returns `CodexResult` failures.
- `finally: client.close()` is unconditional.
- `JsonRpcStdioClient.close()` sets `_stopping`, closes stdin, sends `terminate()`, waits up to 5 seconds, escalates to `kill()`, clears pending requests, wakes waiters, and logs closed.

Current cleanup strengths:

- Cleanup is centralized and unconditional for normal Python control flow.
- Timeouts now map to `GeminiAcpTimeoutError` and ACP `run()` returns exit code `124` while dropping persisted ACP session state.
- Cancelled stop reasons also clear persisted ACP session state, avoiding reuse of a potentially polluted cancelled conversation.
- `GEMINI_CLI_NO_RELAUNCH=true` is set in the ACP subprocess environment, which avoids the extra node relaunch child observed when using `GeminiAcpClient` directly without the adapter environment.

Remaining cleanup gaps:

- `close()` terminates only the tracked `Popen` pid, not the entire process group. With `GEMINI_CLI_NO_RELAUNCH=true` this was sufficient in the probe; without that env, Gemini spawned a child process but it also exited when the parent was closed. A future CLI helper process that ignores parent/stdin death could survive.
- `close()` does not join the reader and stderr-drain daemon threads. The child process is reaped, but thread quiescence is not explicitly asserted.
- Signal handling in `loop.py` does not interrupt an in-flight ACP prompt. Shutdown during a long prompt waits for natural completion or timeout.
- ACP `run()` does not currently send `session/cancel` before closing on timeout or external shutdown. This is local-process safe, but the durable transcript may contain a partially processed user turn, so the current state-dropping behavior is important.

## Empirical cleanup probes

### Probe A: adapter-like environment

Probe shape: start `GeminiAcpClient` with `GEMINI_CLI_NO_RELAUNCH=true`, initialize, then close and inspect `ps`/`pgrep`.

Observed process before close:

```text
334711  ...  node  node /usr/local/bin/gemini --acp
```

Observed children before close: none.

Observed after `client.close()` and a short settle interval:

- the tracked pid no longer existed;
- no `pgrep -P <pid>` children existed;
- `ps ... | grep 'gemini --acp'` returned no matching process.

Conclusion: the adapter-shaped environment cleans up the ACP subprocess normally.

### Probe B: direct client without no-relaunch env

Probe shape: start `GeminiAcpClient` with inherited environment, initialize, create a session, then close and inspect process tree.

Observed process tree before close:

```text
321951 ... node  node /usr/local/bin/gemini --acp
321974 ... node  /usr/bin/node --max-old-space-size=3892 /usr/local/bin/gemini --acp
```

Observed after `client.close()`:

- `close_elapsed_s`: `0.215` seconds;
- tracked parent pid no longer existed after 0.5 seconds;
- `pgrep -P <pid>` returned no children;
- no `gemini --acp` process remained.

Conclusion: even the relaunching shape cleaned up in this run, but the adapter's no-relaunch environment is preferable because it reduces the process-tree surface.

## Plan-mode opt-out feasibility

### ACP mode surface

Gemini 0.39.0 `session/new` returned these modes:

```json
[
  {"id":"default","name":"Default","description":"Prompts for approval"},
  {"id":"autoEdit","name":"Auto Edit","description":"Auto-approves edit tools"},
  {"id":"yolo","name":"YOLO","description":"Auto-approves all tools"},
  {"id":"plan","name":"Plan","description":"Read-only mode"}
]
```

Calling:

```json
{"method":"session/set_mode","params":{"sessionId":"...","modeId":"plan"}}
```

returned `{}` successfully.

A follow-up prompt asking the model to run `pwd` while in plan mode produced no `tool_call` or `tool_call_update` events. The assistant replied that shell execution was unavailable in the current environment. That is the behavior we want for untrusted task text.

### Current adapter state

Current ACP execution always does:

```python
client.set_session_mode(session_id=session_id, mode_id="yolo")
```

This applies equally to normal task execution, prose replies, and plan generation. Therefore, there is **no current per-teammate/per-task opt-out**; there is only one hard-coded ACP mode.

### Feasible design options

Recommended minimal implementation:

1. Add an ACP session mode setting, e.g. `CLAUDE_ANYTEAM_GEMINI_ACP_MODE`, accepted values `default|autoEdit|yolo|plan`, defaulting to current `yolo` for backwards compatibility.
2. Add a `session_mode: str = "yolo"` parameter to `acp.run()` and pass it from `loop._backend_run()`.
3. For plan/prose/ephemeral calls, consider passing `plan` or `default` automatically unless the caller explicitly overrides.
4. For task execution, keep `yolo` by default, but allow a per-task metadata flag such as `metadata.gemini_acp_mode = "plan"` or `metadata.untrusted = true` to force `plan`.
5. When `plan` mode is selected for a task, do not expect file changes. Treat it as an analysis/planning pass that must return a plan, risk assessment, or blocked result rather than completing code edits.

Per-teammate feasibility:

- Straightforward: config/env is already loaded into `GeminiSettings`, and `loop._backend_run()` centralizes calls to `acp.run()`.
- This is low-risk and does not require protocol changes.

Per-task feasibility:

- Feasible if task metadata is available on the task objects returned by `protocol_io` and preserved by the task store.
- `loop._execute_task()` already receives the full task object before building the prompt, so it can inspect metadata and choose a session mode.
- If metadata support is inconsistent, a conservative fallback is subject/description convention or a task owner/team policy, but metadata is cleaner.

Security caveat:

- ACP `plan` mode reduces tool execution from untrusted prompts, but it is not a complete prompt-injection defense. The model still reads untrusted text and can produce misleading output. Treat plan mode as a tool-execution opt-out, not as content sanitization.

## Verification run

Relevant unit subset:

```bash
uv run pytest \
  tests/test_jsonrpc_stdio.py \
  tests/test_gemini_acp_client.py \
  tests/test_gemini_acp_prompt_flow.py \
  tests/test_gemini_acp_recovery.py \
  tests/test_gemini_acp_cancel.py \
  tests/test_gemini_acp_session_reload.py \
  tests/test_gemini_loop_session_policy.py -q
```

Result: `18 passed in 6.25s`.

## Recommendations

1. Keep the current per-turn ACP process model; it makes idle shutdown cleanup simple.
2. Add process-group cleanup hardening if Gemini ever runs child helpers despite `GEMINI_CLI_NO_RELAUNCH=true`: launch with a new session/process group and terminate the group on close.
3. Join reader/stderr threads after process exit or add tests proving they stop, to make cleanup more auditable.
4. On graceful shutdown during an active ACP turn, consider sending `session/cancel`, dropping persisted ACP state, and then closing the subprocess rather than waiting for natural completion.
5. Implement ACP session mode as an explicit config/call parameter, defaulting to `yolo` for compatibility but allowing `plan` for untrusted text.
6. Prefer `plan` or `default` for ephemeral planning/prose calls; reserve `yolo` for trusted execution tasks.
