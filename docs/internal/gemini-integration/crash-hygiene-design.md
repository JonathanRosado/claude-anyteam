# Gemini ACP crash hygiene design

## Problem

When the Gemini teammate adapter dies hard (for example `kill -9`, OOM kill, terminal/ssh loss that kills only the adapter, or interpreter abort), the per-invocation `gemini --acp` child can survive long enough to keep file descriptors and session files alive. Today the adapter only calls `client.close()` on a normal Python unwind (`src/claude_anyteam/backends/gemini/acp.py:359-360`), and the transport only terminates the direct process (`src/claude_anyteam/jsonrpc_stdio.py:82-99`). If the adapter never reaches those lines, stale ACP JSONL sessions remain under the isolated Gemini home, typically:

```text
<gemini_home>/.gemini/tmp/<project>/chats/session-*.jsonl
```

The fix should make graceful exits stronger and make next startup recover from ungraceful exits.

## Recommended approach

1. Start `gemini --acp` in its own process group/session on POSIX.
2. Track adapter and child process metadata in the existing adapter state file.
3. Install adapter-level SIGINT/SIGTERM cleanup that terminates any active ACP child group before the loop exits.
4. On next adapter startup, detect an abnormal previous adapter death, reap only this teammate's orphaned Gemini ACP processes, and quarantine stale session JSONL files instead of deleting them.
5. Preserve normal session reuse when the previous adapter appears to have exited cleanly or when a session is explicitly reusable.

This is intentionally per team + per agent because `gemini_home` is already isolated by `_default_gemini_home(team, agent)` (`src/claude_anyteam/backends/gemini/invoke.py:99-102`) and may also be overridden per teammate (`src/claude_anyteam/backends/gemini/config.py:67-85`).

## Subprocess group and signal-safety design

### Should ACP launch with `setsid`?

Yes on POSIX: launch `gemini --acp` with `start_new_session=True` (preferred over `preexec_fn=os.setsid` in Python because it is safer with threads). That causes the child to become process-group leader for a new session. Then cleanup can signal the whole Gemini subtree with `os.killpg(proc.pid, signal.SIGTERM)` and, if needed, `SIGKILL`.

Pros:

- Reliable subtree cleanup: if Gemini spawns helper children, signaling the process group is more complete than `proc.terminate()` on only the direct child.
- Insulates Gemini from terminal-originated signals that might otherwise race with adapter cleanup; the adapter remains responsible for orderly cancellation/termination.
- Makes orphan reaping simpler: the recorded `gemini_pid` is also the process group id on POSIX.

Cons / caveats:

- `setsid` does not make hard adapter death clean by itself. If the adapter is killed by SIGKILL/OOM, the child process group can still become orphaned; next-startup reaping is still required.
- Terminal `Ctrl-C` will not automatically reach Gemini once it is in another session. The adapter SIGINT handler must explicitly terminate the active child/group.
- Windows needs a separate path (`creationflags=subprocess.CREATE_NEW_PROCESS_GROUP` and `CTRL_BREAK_EVENT` or terminate/kill fallback). The repo appears POSIX-oriented, but implement the API so Windows can degrade safely.

### Graceful shutdown

Current loop signal handling only flips `state.shutdown_requested` (`src/claude_anyteam/backends/gemini/loop.py:51-56`). That is not enough while `_backend_run()` is blocked inside `acp.run()` (`src/claude_anyteam/backends/gemini/loop.py:80-107`) because the loop cannot observe the flag until the prompt returns.

Recommended design:

- Add a small ACP process registry/context in `backends/gemini/acp.py` or a new `backends/gemini/crash_hygiene.py` module.
- When `GeminiAcpClient.start()` returns, record `gemini_pid` and process group metadata.
- Register the active client/process in that registry for the duration of `acp.run()`.
- In `loop.run()` signal handler (`src/claude_anyteam/backends/gemini/loop.py:51-56`), call a non-blocking/bounded `acp_invoke.terminate_active_acp_children(reason=...)` after setting `shutdown_requested`.
- Cleanup sequence for graceful signal:
  1. Best effort `session/cancel` if a session id is known and transport still works (`_cancel_session_quietly` already exists at `src/claude_anyteam/backends/gemini/acp.py:262-268`).
  2. Close stdin to encourage ACP EOF.
  3. Send SIGTERM to the Gemini process group.
  4. Wait a short bounded interval (2-5s).
  5. Send SIGKILL to the process group if still alive.
  6. Clear `gemini_pid` from state only after the process is gone.

Signal handlers should do minimal Python work. It is acceptable in this single-process adapter to set a flag and poke a thread-safe cleanup function, but avoid complex file I/O directly from the signal frame. A robust pattern is:

- handler sets `shutdown_requested` and calls `request_active_acp_shutdown()`;
- the blocked ACP code periodically observes the request if possible, while the registry cleanup path can still terminate the child to unblock JSON-RPC waits.

### Ungraceful shutdown

Nothing inside a SIGKILL/OOM-killed adapter can run. The only reliable mitigation is durable state before/during invocation plus startup recovery in the next adapter process.

## PID/state file design

Use the existing adapter state file path:

```text
<gemini_home>/.claude-anyteam/state.json
```

This is already created by `ensure_adapter_state()` (`src/claude_anyteam/backends/gemini/invoke.py:204-208`), read by `read_adapter_state()` (`src/claude_anyteam/backends/gemini/invoke.py:211-244`), and atomically written via `_write_atomic_json()` (`src/claude_anyteam/backends/gemini/invoke.py:125-140`). The writer already uses tmp file + fsync + `os.replace`, so extend that path rather than adding a second PID file.

Add fields:

```json
{
  "adapter_pid": 12345,
  "adapter_start_time": "2026-04-24T14:12:33Z",
  "adapter_start_monotonic_ns": 123456789000,
  "adapter_generation": "uuid-v4",
  "team": "gemini-postship",
  "agent": "codex-acp-control-researcher",
  "cwd": "/home/rosado/Projects/codex-teammate",
  "gemini_pid": 12357,
  "gemini_pgid": 12357,
  "gemini_started_at": "2026-04-24T14:12:40Z",
  "last_clean_shutdown_at": null,
  "last_reaper_run_at": "2026-04-24T14:15:02Z",
  "last_reaper_summary": {"orphan_processes_killed": 1, "sessions_quarantined": 2}
}
```

Notes:

- Keep existing keys (`headless_session_id`, `acp_session_id`, `acp_storage_session_id`, `backend`, `updated_at`) for compatibility.
- `adapter_generation` avoids PID reuse ambiguity when comparing state written by this process versus a prior process.
- `adapter_start_time` should be wall-clock UTC ISO-8601 for file mtime comparisons; monotonic ns is useful only within the same process.
- Add defaulting in `read_adapter_state()` near `src/claude_anyteam/backends/gemini/invoke.py:239-243` so older state files remain valid.
- Extend `write_adapter_state()` (`src/claude_anyteam/backends/gemini/invoke.py:247-265`) or add a sibling `write_adapter_runtime_state()` that merges arbitrary runtime fields without forcing callers to pass all session ids.

Startup check:

- At adapter startup, read the state file.
- If `adapter_pid` exists and differs from `os.getpid()`, check liveness with `os.kill(pid, 0)`.
- Treat `ProcessLookupError` as dead.
- Treat `PermissionError` as alive/unknown and do not reap; log a warning.
- To reduce PID reuse false positives, optionally compare process start time from `/proc/<pid>/stat` or `psutil.Process(pid).create_time()` against `adapter_start_time` where available.
- If the prior adapter is dead and `last_clean_shutdown_at` is absent/older than `adapter_start_time`, run the startup reaper.

Current process state lifecycle:

- In `loop.run()` after `_backend_feature_test(settings)` and before `register(...)` (`src/claude_anyteam/backends/gemini/loop.py:37-49`), prepare `gemini_home`, run recovery, and write this adapter's `adapter_pid`/`adapter_start_time`/`adapter_generation`.
- In `finally` (`src/claude_anyteam/backends/gemini/loop.py:64-70`), if exiting normally, mark `last_clean_shutdown_at`, clear `gemini_pid`/`gemini_pgid`, and optionally leave `adapter_pid` for diagnostics with a `adapter_exited_at` timestamp. Do not clear session ids unless existing policy says to drop them.
- In `acp.run()`, after `client.start()` (`src/claude_anyteam/backends/gemini/acp.py:318-321`), write `gemini_pid`, `gemini_pgid`, and `gemini_started_at`. In the `finally` block (`src/claude_anyteam/backends/gemini/acp.py:359-360`), clear those process fields after `client.close()` succeeds or after forced group kill.

## Stale-session reaper at startup

### Discovery

Look only under this adapter's isolated `gemini_home`:

```text
<gemini_home>/.gemini/tmp/**/chats/session-*.jsonl
```

This matches `_latest_storage_session_id()` today (`src/claude_anyteam/backends/gemini/acp.py:75-93`). Do not scan the user's real `~/.gemini` unless `gemini_home` explicitly points there; the default path is per team/agent and should remain the safety boundary.

Session files to consider stale:

- prior adapter was detected dead;
- file mtime is `>= previous.adapter_start_time` and, if known, `<= recovery_start_time`;
- file is not the current live `acp_storage_session_id` after successful reuse;
- file is under the same `gemini_home` for this team/agent.

### Graveyard vs. reuse

Default: move stale candidates to a graveyard, do not delete.

Recommended graveyard path:

```text
<gemini_home>/.claude-anyteam/graveyard/acp-sessions/<adapter_generation-or-timestamp>/...
```

Preserve the relative path below `.gemini/tmp` so a user or debugging tool can reconstruct project context:

```text
<graveyard>/tmp/<project>/chats/session-abc.jsonl
```

Why quarantine instead of auto-loading:

- A session whose child process outlived a dead adapter may end mid-turn or contain partially flushed tool activity.
- Automatically loading it can cause confusing continuation from an interrupted state.
- The adapter already has explicit reuse paths (`stored_session_id`, `stored_storage_session_id` in `acp.run()` at `src/claude_anyteam/backends/gemini/acp.py:323-332`). Keep those for clean exits.

Optional future policy:

- If a session file is very recent and maps exactly to the stored `acp_storage_session_id`, try `session/load` once before quarantining; if load fails or stop reason/cancel is observed, reset ACP state and quarantine.
- Gate this behind a config/env flag such as `CLAUDE_ANYTEAM_GEMINI_ACP_RECOVER_RECENT=1`, because deterministic cleanup is safer for first implementation.

### Per-team/per-agent safety

Safety comes from the `gemini_home` boundary. The default home path includes safe team and agent components (`src/claude_anyteam/backends/gemini/invoke.py:99-102`). The reaper must require an explicit `gemini_home` parameter and never scan broad locations such as `Path.home() / ".gemini"` globally.

For defense in depth, write `team`, `agent`, and `cwd` into `state.json`, and before moving files verify the current settings match state values when those fields exist.

## Orphan-process reaper

On startup after detecting a prior dead adapter, scan for orphaned `gemini --acp` processes that belong to this adapter only.

Filtering rules, all required:

1. Command line contains the configured Gemini binary basename and either `--acp` or `--experimental-acp`.
2. Environment or argv indicates this adapter's isolated home:
   - Prefer reading `/proc/<pid>/environ` and requiring `HOME=<gemini_home>`.
   - Also accept `CLAUDE_ANYTEAM_TEAM=<team>` and `CLAUDE_ANYTEAM_NAME=<agent>` if present.
   - If `/proc` env is unavailable, only kill the recorded `gemini_pid`/`gemini_pgid` from state; do not kill by broad argv alone.
3. Parent PID is not the current adapter PID. It may be 1/systemd or a dead prior adapter PID.
4. If a process's parent is an alive adapter with matching team/agent but a different generation, skip it and log; this prevents two adapters sharing a `gemini_home` from fighting.

Termination sequence:

- Send SIGTERM to the process group if `pgid` is known and positive; otherwise send SIGTERM to the process.
- Wait up to 5 seconds.
- Send SIGKILL to the group/process if still alive.
- Record counts and pids in `last_reaper_summary`.

Implementation options:

- Without dependency: use `/proc` on Linux for `cmdline`, `environ`, `stat` (ppid, start time), plus `os.kill(pid, 0)`.
- With optional dependency: `psutil` makes cross-platform process scanning easier, but avoid adding it unless the project already accepts new runtime dependencies.

Important: do not kill arbitrary user Gemini sessions. If the process cannot be tied to this exact `gemini_home`, skip it. The isolated `HOME` assignment already happens in `acp.run()` (`src/claude_anyteam/backends/gemini/acp.py:304-310`), so this is a strong discriminator.

## Concrete implementation sketch

### 1. Extend process management in `jsonrpc_stdio.py`

Target: `src/claude_anyteam/jsonrpc_stdio.py:33-72` and `src/claude_anyteam/jsonrpc_stdio.py:82-99`.

Add constructor options:

- `start_new_session: bool = False`
- `terminate_process_group: bool = False`

Use in `start()`:

```python
self._proc = subprocess.Popen(..., start_new_session=self._start_new_session)
```

Use in `close()`:

- if POSIX and `terminate_process_group`, call helper `_terminate_pgrp(proc, timeout)`;
- otherwise keep current direct `terminate()`/`kill()` behavior.

Expose read-only properties:

- `pid`
- `pgid` (POSIX only; `os.getpgid(proc.pid)` with exception handling)
- `argv`

### 2. Enable process groups for Gemini ACP client

Target: `src/claude_anyteam/backends/gemini/acp_client.py:72-99`.

Pass the new transport flags to `super().__init__()`:

```python
super().__init__(
    argv=argv,
    env=env,
    log_prefix="gemini_acp",
    stderr_log_prefix="gemini_acp.stderr",
    start_new_session=(os.name == "posix"),
    terminate_process_group=(os.name == "posix"),
)
```

### 3. Add crash hygiene helpers

Preferred new file: `src/claude_anyteam/backends/gemini/crash_hygiene.py`.

Functions/classes:

- `adapter_runtime_fields(settings, generation) -> dict`
- `write_runtime_state(gemini_home, **fields) -> None`
- `pid_alive(pid: int) -> bool | None`
- `previous_adapter_died(state, current_pid) -> bool`
- `run_startup_recovery(settings, state) -> ReaperSummary`
- `reap_orphan_acp_processes(gemini_home, team, agent, recorded_pid=None, recorded_pgid=None) -> list[int]`
- `quarantine_stale_acp_sessions(gemini_home, previous_start_time, recovery_start_time, generation) -> list[Path]`
- `register_active_client(client)` / `unregister_active_client(client)` / `terminate_active_acp_children(reason)` if not kept in `acp.py`.

Keep this module free of protocol-loop concerns so unit tests can exercise it with temp dirs and fake process table adapters.

### 4. Wire startup recovery in the loop

Target: `src/claude_anyteam/backends/gemini/loop.py:37-49`.

After feature test and before `register(...)`:

1. Resolve `home = settings.gemini_home or invoke._default_gemini_home(settings.team_name, settings.agent_name)`.
2. Ensure adapter state exists.
3. Read previous state.
4. If backend is ACP and `previous_adapter_died(...)`, run startup recovery.
5. Write current adapter runtime fields.
6. Continue registration.

Target signal handler: `src/claude_anyteam/backends/gemini/loop.py:51-56`.

After setting `state.shutdown_requested = True`, request active ACP child termination. This is what makes `Ctrl-C`/SIGTERM responsive during an in-flight ACP prompt.

Target finally block: `src/claude_anyteam/backends/gemini/loop.py:64-70`.

Mark clean exit and clear process fields. Keep the existing deregistration behavior unchanged.

### 5. Wire per-invocation runtime updates in ACP

Target: `src/claude_anyteam/backends/gemini/acp.py:318-321`.

Immediately after `client.start()`:

- register active client;
- write `gemini_pid`, `gemini_pgid`, `gemini_started_at` to state.

Target: `src/claude_anyteam/backends/gemini/acp.py:348-360`.

In every exit path, ensure:

- timeout/permission/error paths call `_cancel_session_quietly` where appropriate;
- `client.close()` uses process-group termination;
- unregister client;
- clear `gemini_pid`/`gemini_pgid` once the process is gone.

Target: `src/claude_anyteam/backends/gemini/acp.py:378-387`.

When persisting successful session ids, preserve runtime fields rather than overwriting them. If `write_adapter_state()` remains session-specific, add a merge writer so process metadata is not accidentally dropped.

### 6. Extend state helpers

Target: `src/claude_anyteam/backends/gemini/invoke.py:211-244`.

Default new keys to `None` or empty values in `read_adapter_state()`.

Target: `src/claude_anyteam/backends/gemini/invoke.py:247-279`.

Refactor `write_adapter_state()` and `reset_acp_adapter_state()` to preserve unknown/runtime keys from `previous`. Add a helper:

```python
def merge_adapter_state(gemini_home: Path, **updates: Any) -> Path:
    data = read_adapter_state(gemini_home)
    data.update(updates)
    data["updated_at"] = utc_now()
    _write_atomic_json(_adapter_state_path(gemini_home), data)
    return _adapter_state_path(gemini_home)
```

Then use `merge_adapter_state()` for PID updates and existing session writes.

## Test approach

Add focused unit tests plus one integration-style subprocess test.

Suggested files:

- `tests/test_gemini_acp_crash_hygiene.py` for helper logic.
- Extend `tests/test_jsonrpc_stdio.py` for process-group close behavior.
- Extend `tests/test_gemini_acp_recovery.py` for state/session recovery policy if that file already covers ACP recovery.

Unit tests:

1. State compatibility:
   - old state file missing PID fields loads with defaults;
   - merge writer preserves existing session ids and unknown keys;
   - writes are atomic enough that a malformed temp file is ignored.
2. Previous adapter detection:
   - missing `adapter_pid` means no abnormal-death reaper;
   - dead pid triggers recovery;
   - alive pid skips recovery;
   - current pid/generation does not self-reap.
3. Stale session quarantine:
   - create temp `gemini_home/.gemini/tmp/project/chats/session-a.jsonl` with mtime inside previous run window;
   - assert it moves under `.claude-anyteam/graveyard/acp-sessions/...` preserving relative path;
   - assert files outside the mtime window are left alone;
   - assert no files outside the supplied `gemini_home` are touched.
4. Process filter:
   - inject fake process table entries;
   - only entries with `HOME=<gemini_home>` and ACP argv are selected;
   - user `gemini --acp` with a different HOME is skipped;
   - another teammate's home is skipped.

Integration-style test:

- Create a tiny fake `gemini` executable that accepts `--acp`, writes its pid/environment to a file, and sleeps while keeping stdio open.
- Start a short-lived adapter parent process that invokes ACP and records state.
- Kill the parent with SIGKILL.
- Assert fake `gemini` is still alive (or at least state records it) before recovery.
- Start the adapter/recovery hook again with the same `gemini_home`.
- Assert the orphan fake Gemini receives SIGTERM, then SIGKILL if it ignores SIGTERM.
- Assert stale `session-*.jsonl` files are moved to graveyard.
- Assert a fake Gemini with a different `HOME` is not killed.

Mark true SIGKILL/process-group tests POSIX-only (`pytest.mark.skipif(os.name != "posix", ...)`) and keep pure helper tests platform-neutral.

## Open decisions

- First implementation should quarantine stale sessions by default. Auto-loading recent crash sessions can be added later behind an opt-in flag after empirical validation.
- Prefer no new runtime dependency. If process scanning becomes too brittle, consider optional `psutil` but keep `/proc`/recorded-pid fallback for Linux CI.
- If multiple adapter processes intentionally share one `gemini_home`, the state file becomes a lock/ownership problem. Current design assumes one team/agent adapter per isolated home, matching the default path and intended deployment.
