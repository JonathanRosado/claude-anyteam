# App Server default sanity test — task #22

**Status:** complete (PASS)
**Author:** reviewer
**Date:** 2026-04-22
**Adapters under test:** two fresh codex-alice instances, pre-fix and post-fix.
- Pre-fix: PID 1377723, spawned 12:51 local. Codex CLI 0.122.0. 122 tests green. Used for S1, S2, S3.
- Post-fix (Fix B): PID 1389788, spawned 14:08 local, after `wrapper_server.py` + `codex.py::app_server_invoke` patches. 130 tests green (+8 for Fix B wiring). Used for S3-rerun, S4.
- Both under the new default (App Server on via `app_server: bool = True` in `config.py`).
**Team-lead's #21 flip:** verified — `config.py:32` is `app_server: bool = True`; CLI advertises `--app-server | --no-app-server` via `BooleanOptionalAction`; `uv run claude-anyteam --help` confirms "default: on".

## Scope

Five probes executed in two passes. Pass 1 (S1/S2/S3) validated the App Server default on varied workloads and caught a regression in outbound wrapper MCP tool access. Pass 2 (S3-rerun/S4) validated the fix for that regression and the shutdown handshake. Purpose: evidence that App Server as default handles varied workloads cleanly. Explicitly NOT re-validating mid-task steer (proven in task #19) or v7.2 resume (orthogonal on this default path per implementer's Option (c)).

Probes are tagged "do NOT reassign" and point outputs into `sanity-artifacts/{slug,fizzbuzz,minigrep,wordcount}/` so Codex's workspace writes stay off the source tree.

## Probe results

| Probe | Shape | Expected signals | Outcome | Notes |
|---|---|---|---|---|
| S1 (#26) | Multi-file package: `__init__.py` + `slug.py` + `test_slug.py` | claim → app_server.start → thread_started → codex.done exit=0 structured=true → task.completed files=3 → app_server.closed | **PASS** | 40.4s; 3 files; all 6 unittest cases green |
| S2 (#27) | Bugfix cycle: deliberate bug → failing test → fix → passing test | claim → same App Server lifecycle → codex.done with multi-turn event count → task.completed files=2 → summary explicitly mentions bug-fix | **PASS** | 57.4s; bug→fail→fix→pass cycle observed in Codex summary |
| S3 (#28, pre-fix) | minigrep with directed MCP tool use | wrapper MCP tool-call log lines + a `sanity_progress` JSON message in team-lead inbox + task.completed files=2 | **REGRESSION CAUGHT** | Wrapper MCP tools not exposed to Codex's session — Codex introspected, found zero resources, reported the gap. Code still correct; regression scope is outbound tool access only. Root-caused and fixed (see §"Load-bearing finding"). |
| S3-rerun (#30, post-fix) | wordcount with directed MCP tool use | `server=claude_anyteam_wrapper` tool_call events for 2× `task_update` + 1× `send_message`; `sanity_progress` JSON in team-lead inbox; files=2 | **PASS** | 44s; 6 tool_call_events total (3 started + 3 completed, all `server=claude_anyteam_wrapper`, zero errors); exact `S3-rerun` JSON delivered to team-lead inbox at 18:23:55.867Z; 3 unittest cases green |
| S4 | Runtime: shutdown_probe against post-fix codex-alice | shutdown.approve → app_server.closed → registration.removed → registration.inbox_cleaned → loop.deregistered; no orphan subprocess tied to the adapter | **PASS** | Shutdown approved 1.2s after probe at 18:31:46.938Z; all four deregister events fired in order; codex-alice removed from config members list |

## Per-probe evidence

### S1 — URL slugifier — PASS

Live trace (`/tmp/codex-alice.log`):

```
16:54:21.918  task.claimed task_id=26
16:54:21.975  app_server.start args=[codex, app-server]
16:54:25.270  app_server.thread_started thread_id=019db61d-46b0-7fa3-bec6-695893b4b3b2
16:55:02.345  app_server.closed
16:55:02.345  app_server.done exit_code=0 events=210 structured=true tool_call_events=0
16:55:02.352  task.completed task_id=26 files=3
```

Elapsed: 40.4s. App Server thread lifecycle clean (start → thread/start → done → closed, no events leaking). No wrapper tool calls (expected — S1 is pure code-write, doesn't warrant MCP tool use).

Artifacts produced at `sanity-artifacts/slug/`: `__init__.py`, `slug.py`, `test_slug.py`. Codex ran `python3 -m unittest test_slug.py` itself inside the probe dir; reviewer re-ran and confirmed all 6 cases pass.

`task_complete` message (in `team-lead` inbox, ts=16:55:02.347) schema-conformant: `kind=task_complete, task_id=26, files_changed=[3 paths relative to cwd], summary=<Codex-authored>, codex_exit_code=0`. Same shape as v6/v7 fresh-exec `task_complete` — no structural difference.

No writes outside `sanity-artifacts/slug/` — directive honored. No regression.

### S2 — FizzBuzz bugfix cycle — PASS

Live trace:

```
16:55:02.354  task.claimed task_id=27
16:55:02.355  app_server.start
16:55:02.847  app_server.thread_started thread_id=019db61d-d9ad-...
16:55:02.849  app_server.turn_started turn_id=019db61d-d9c1-...
16:55:59.709  app_server.closed
16:55:59.710  app_server.done exit_code=0 events=250 structured=true tool_call_events=0
16:55:59.722  task.completed task_id=27 files=2
```

Elapsed: 57.4s. Artifacts at `sanity-artifacts/fizzbuzz/`. Codex's summary describes the bug-fix cycle explicitly:

> "intentionally introduced the bug where multiples of 3 returned `"Fiz"`... Running `python -m unittest -v`... produced the expected failure in `test_fizz_case`... I then fixed the bug... by changing `"Fiz"` to `"Fizz"` and reran the tests successfully, with all six passing."

Multi-turn Codex reasoning inside a single App Server thread — write bug → run test → observe failure → fix → rerun — worked cleanly. No regression in multi-turn behavior under App Server default.

### S3 — minigrep with MCP tool use — **REGRESSION**

Live trace:

```
16:55:59.724  task.claimed task_id=28
16:55:59.724  app_server.start
16:56:00.500  app_server.thread_started thread_id=019db61e-bae5-...
16:56:00.502  app_server.turn_started turn_id=019db61e-baf6-...
16:56:10.875  codex.tool_call server=codex tool=list_mcp_resources (inProgress)
16:56:11.619  codex.tool_call server=codex tool=list_mcp_resources (completed) → {"resources":[]}
16:56:51.414  app_server.closed
16:56:51.414  app_server.done exit_code=0 events=257 structured=true tool_call_events=2
16:56:51.420  task.completed task_id=28 files=2
```

The 2 tool_call_events are `list_mcp_resources` against Codex's built-in `codex` server — Codex introspecting its own tool list. Result: `{"resources":[]}`. Codex's built-in MCP list contained zero resources — **the wrapper MCP server (`claude_anyteam_wrapper`) was not reachable from this thread**. Zero calls to `task_update`, zero calls to `send_message`.

Codex's own `task_complete` summary confirms this directly:

> "Note: the requested MCP protocol tools `task_update` and `send_message` were not exposed in this session's callable tool list, so I could not invoke them directly."

Team-lead inbox contains zero `sanity_progress` messages — consistent with Codex's report. The probe's directive (use `task_update` 3+ times, `send_message` 1 time) was cleanly specified and Codex tried to introspect the tool list; the tools weren't there.

Elapsed: 51.7s. The code Codex produced (`minigrep.py` + `test_minigrep.py`) still works — S3 functionally completed; the missing MCP tool availability is the regression, not the code output.

### S3-rerun — wordcount with wrapper MCP tools (post-Fix-B) — PASS

Live trace (post-fix adapter, PID 1389788):

```
18:23:19.760  task.claimed task_id=30
18:23:19.762  app_server.start
18:23:21.485  app_server.thread_started thread_id=019db66e-b353-...
18:23:21.488  app_server.turn_started turn_id=019db66e-b390-...
18:23:32.629  codex.tool_call started  server=claude_anyteam_wrapper tool=task_update   args={task_id:"30", active_form:"designing cli"}
18:23:32.637  codex.tool_call completed server=claude_anyteam_wrapper tool=task_update   (11ms)
18:23:46.465  codex.tool_call started  server=claude_anyteam_wrapper tool=task_update   args={task_id:"30", active_form:"running tests"}
18:23:46.471  codex.tool_call completed server=claude_anyteam_wrapper tool=task_update   (6ms)
18:23:55.862  codex.tool_call started  server=claude_anyteam_wrapper tool=send_message  args={to:"team-lead", body:'{"kind":"sanity_progress","probe":"S3-rerun","phase":"tests_passing","task_id":"30"}'}
18:23:55.878  codex.tool_call completed server=claude_anyteam_wrapper tool=send_message  (17ms, result={delivered_to:"team-lead", sender:"codex-alice"})
18:24:03.705  app_server.closed
18:24:03.705  app_server.done exit_code=0 events=225 structured=true tool_call_events=6
18:24:03.715  task.completed task_id=30 files=2
```

**Three-way verification (all three required signals landed):**

1. **Wrapper reachable** — three distinct `server=claude_anyteam_wrapper` tool_call events, all returning structured content with zero errors. The pre-fix regression signal (`list_mcp_resources` returning `{"resources":[]}`) does not appear in this log.
2. **Tool calls firing** — 2× `task_update` with the exact `active_form` values specified by the probe directive ("designing cli", "running tests") and 1× `send_message` with the exact JSON body shape specified.
3. **Inbox delivery** — `sanity_progress` message in team-lead inbox at 18:23:55.867Z with body `{"kind":"sanity_progress","probe":"S3-rerun","phase":"tests_passing","task_id":"30"}`. Byte-exact match to the probe directive.

Codex's `task_complete` summary for #30 corroborates:

> "The required wrapper MCP calls also succeeded: `task_update` at start with `designing cli`, `task_update` before verification with `running tests`, and a single `send_message` to `team-lead` with the exact `S3-rerun` JSON payload."

Artifacts at `sanity-artifacts/wordcount/`: `wordcount.py` + `test_wordcount.py`. Reviewer re-ran `python3 -m unittest test_wordcount.py` independently; all 3 cases pass.

Elapsed: 44s. Fix B resolves the S3 regression.

### S4 — shutdown handshake (post-Fix-B adapter) — PASS

Live trace (same codex-alice after S3-rerun completed):

```
18:31:45.775  shutdown_probe.sent request_id=shutdown-1776882705774@codex-alice
18:31:46.938  shutdown.approve request_id=shutdown-1776882705774@codex-alice
18:31:46.955  registration.removed team=claude-anyteam name=codex-alice
18:31:46.956  registration.inbox_cleaned
18:31:46.956  loop.deregistered
```

All four deregister events fired in order. `shutdown_response` with `approve: true` landed in team-lead inbox at 18:31:46.938Z. Member entry removed from `config.json` (verified: 7 canonical members remain, codex-alice absent). Inbox file deleted. Elapsed from probe to full deregistration: 1.2 seconds.

**Orphan subprocess note:** `pgrep -af 'codex app-server'` returned multiple processes on the host, but none of them correspond to the post-S3-rerun adapter's thread — the adapter's own App Server subprocess exited cleanly at 18:24:03.705 (before the shutdown probe was even fired). The stale processes are from earlier sessions/other users of the system and are not an adapter-lifecycle issue.

## Load-bearing finding: outbound wrapper-MCP regression (caught + diagnosed)

The sanity test's load-bearing finding is the S3 regression — exactly the kind of behavior this testing was designed to surface. The probe did its job.

**Root cause (diagnosed by implementer, 2026-04-22):** App Server's MCP-child subprocess does not inherit the adapter's environment. When the adapter spawns `codex app-server` and Codex in turn spawns the wrapper MCP server as a stdio child, the wrapper's `_identity()` reads `CLAUDE_ANYTEAM_TEAM` and `CLAUDE_ANYTEAM_NAME` from `os.environ` — but those are absent in the MCP child's env. `_identity()` raises, the MCP subprocess dies during handshake, and Codex's MCP client sees no wrapper tools. Implementer's diagnostic session captured `mcpServer/startupStatus/updated` notifications showing the exact failure mode.

Under fresh-exec (v7.0), the same wrapper was spawned via `codex exec -c mcp_servers.claude_anyteam_wrapper.command=...` with the adapter's env inherited differently through the CLI path, so the env-var-visibility issue never materialized there.

**Fix in flight:** implementer is patching the wrapper to accept `--team` / `--name` as CLI args (backward-compat fallback to env) and updating `codex.py::app_server_invoke` to pass identity via `mcp_servers.NAME.args` on the `thread/start` JSON-RPC call. Scope is ~15 LoC.

**Scope of the regression:**
- **Affected:** outbound MCP tool use by Codex (`task_update`, `send_message`, `task_create`, `read_inbox`, `task_list`, `read_config` — the six wrapper tools).
- **NOT affected:** inbound `turn/steer` (v7.1's signature feature — live-validated on task #19 independently).
- **NOT affected:** basic task execution (S1, S2 both passed cleanly).
- **NOT affected:** structural `task_complete` shape or per-task thread lifecycle.

**Evidence of the regression (captured from S3 live run):**
- S3 Codex summary, quoted from team-lead inbox ts 2026-04-22T16:56:51.416Z: *"Note: the requested MCP protocol tools `task_update` and `send_message` were not exposed in this session's callable tool list, so I could not invoke them directly."*
- Live log: `list_mcp_resources` on Codex's default server returned `{"resources":[]}` — no wrapper.
- Team-lead inbox: zero `sanity_progress` messages arrived.

**Historical implication for v7.1 conformance:** v7.1's live acceptance on task #19 proved the INBOUND direction of the App Server integration (adapter→Codex `turn/steer`). The OUTBOUND direction (Codex→wrapper→protocol) was not separately exercised at that time because task #19's description and final-output requirements didn't mandate wrapper tool use. The regression likely pre-exists task #21's default flip; the flip is what made it observable.

Implementer independently confirmed from task #19's log: `tool_call_events=0` and zero outbound wrapper tool messages to team-lead's inbox. The `final-review.md` v7.1 conformance row needs correction (inbound-only, not end-to-end) — will land in task #23 with this wording (mirrored from `v7.1-notes.md` §7):

> *"v7.1 shipped; live validation covered `turn/steer` (adapter→Codex inbound direction) only. Outbound wrapper MCP tool access — Codex calling the team protocol from its own reasoning — was regressed from v7's fresh-exec semantics to App Server mode due to env-var inheritance on MCP subprocess spawn. Regression caught during task #22 sanity probes (2026-04-22); fixed via wrapper CLI-args identity patch; re-validated under task #22 rerun."*

**Note on task #19 log artifact:** reviewer's own check of `/tmp/codex-alice.log` was inconclusive — the current log is from today's 12:51 adapter startup and task #19's trace had been overwritten. Implementer had retained the data and confirmed definitively. Uncheckable from reviewer's vantage; resolved by implementer's retention.

## Other observations worth recording

**Thread lifecycle is per-task.** Each of S1, S2, S3 produced a fresh `thread_started` → `app_server.closed` sequence. Confirms v7-architecture §4 Option Y default = thread-per-task (not persistent across tasks). Matches v7.2 orthogonality note — persistent-session semantics intentionally live on the `--no-app-server` path, not here.

**App Server timing vs. fresh-exec baseline.** S1–S3 took 40–57s each; baseline from task #5 (csv2md=39s, wordfreq=36s, rational=~90s for similar shapes, all fresh-exec). App Server overhead is in the same order of magnitude; thread startup adds ~3s per task that fresh-exec doesn't pay. Not a regression; a recorded overhead.

**No orphan subprocesses.** After each task, `app_server.closed` fires cleanly; the `codex app-server` subprocess exits and does not linger into the next task's poll cycle. Process-lifecycle hygiene is intact.

**Adapter `--cwd` is the project root.** codex-alice was spawned with `--cwd /home/rosado/Projects/claude-anyteam` (team-lead's decision). Codex's workspace-write sandbox scopes to this directory. All three sanity probes explicitly directed outputs into `sanity-artifacts/{slug,fizzbuzz,minigrep}/` and all three honored the directive. No blast radius observed.

## Verdict

**PASS.** App Server is stable enough as default for the current project scope.

Evidence summary:
- **S1 + S2 (pre-fix):** varied task execution (multi-file output, multi-turn bugfix cycle) worked correctly under App Server default with no regressions vs. fresh-exec baseline.
- **S3 (pre-fix):** outbound wrapper MCP tool use was regressed — caught by the sanity probe, exactly the behavior the probe was designed to surface.
- **S3-rerun (post-Fix-B):** outbound wrapper MCP tool use works correctly. Three-way verification (wrapper reachable + tool calls firing + inbox delivery) passed.
- **S4 (post-Fix-B):** shutdown handshake clean; adapter deregisters in order.

**The regression-caught-and-fixed story is part of the value delivered.** The sanity test paid for itself: the pre-existing wrapper-env-inheritance gap (present since v7.1 shipped, unobserved because task #19 didn't exercise it) was surfaced by S3, root-caused by implementer's diagnostic session, fixed via Fix B (~15 LoC across `wrapper_server.py` and `codex.py::app_server_invoke`), and re-validated under S3-rerun with fresh independent evidence. The team's honest-reporting discipline held throughout — pre-fix evidence was captured before the diagnosis; post-fix evidence was captured after; the doc records both.

**Outstanding item for task #23:** the `final-review.md` claim about v7.1 live-validation needs narrowing to "inbound `turn/steer` only" (not end-to-end) with the post-fix re-validation noted. Wording mirrored from `v7.1-notes.md` §7; captured earlier in this doc's §"Load-bearing finding."
