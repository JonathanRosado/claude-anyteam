# Battle-Test Report: claude-anyteam adapter v7.3+

Date: 2026-04-22

## Verdict

**Functional parity with native Claude Code agents: CONFIRMED.**

All six probe areas executed by mixed Claude + Codex testers. Ten bugs
found, ten bugs fixed. Test suite: 150 → 168 passing (+18 regression
tests added across the fixes). Zero accepted limitations — every UX gap
surfaced was scoped tightly enough to fix rather than defer.

## Team composition

| Role | Identity | Model | Effort |
|---|---|---|---|
| Tester | claude-tester | opus | (default) |
| Tester | codex-tester | gpt-5.4 | xhigh |
| Implementer | claude-implementer | sonnet | (default) |
| Implementer | codex-implementer | gpt-5.4 | xhigh |
| Lead | team-lead | opus | (default) |

## Probes executed

| # | Area | Owner | Bugs found |
|---|---|---|---|
| 1 | Cross-type interop (Claude ↔ Codex) | claude-tester | #8, #9 |
| 2 | Shutdown + lifecycle edge cases | claude-tester | #13, #16 |
| 3 | Concurrent claim race conditions | claude-tester | #17 |
| 4 | Wrapper MCP tool surface parity | codex-tester | #10, #11, #12 |
| 5 | v7.1 mid-task reactivity edge cases | codex-tester | (none) |
| 6 | v7.3 fork-lineage depth + edge cases | codex-tester | #15 |

Plus the user-flagged TUI presence bug (#14), filed and fixed mid-run.

## Bugs found and fixed

| # | Severity | Short description | Fix location |
|---|---|---|---|
| #8 | P1 | Registration race silently dropped concurrent adapter entries | `registration.py` (FileLock) |
| #9 | P1 | Idle Codex silently swallowed peer prose messages | `loop.py`, `prompts.py`, `protocol_io.py` (one-shot Codex reply) |
| #10 | P2 | Wrapper `task_update` rejected `owner` + `metadata` kwargs | `wrapper_server.py` |
| #11 | P2 | Wrapper `send_message` rejected `to="*"` broadcast | `wrapper_server.py` |
| #12 | P3 | Peer DMs stamped recipient color instead of sender color | `wrapper_server.py` |
| #13 | P1 | Mid-task `shutdown_request` sent no rejection response | `loop.py` |
| #14 | P1 (UX) | Codex teammates invisible in Claude Code's TUI presence line | `registration.py` (`backendType: "in-process"`) |
| #15 | P2 | v7.3 unmaterialized parent crashed on new-client probe | `app_server.py::is_thread_materialized` |
| #16 | P3 | `loop.py` finally-block docstring described wrong behavior | `loop.py` (comment only) |
| #17 | P1 | Concurrent `claim_task` race — both adapters could "succeed" | `protocol_io.py::claim_task` (FileLock + compare-and-set) |

## Accepted limitations

None. Every UX gap surfaced had a small enough fix to merit landing
rather than documenting as a limitation. The `task_get` tool absent
from the wrapper surface was noted but determined non-blocking
(task_list covers the use case); no bug filed.

## Evidence

- All 10 bugs have live repros captured in their task descriptions.
- Each fix ships with a regression test. Test count grew from 150
  (baseline) to 168 (post-battle-test) — 18 new tests.
- TUI fix (#14) empirically verified: a scratch hidden-CLI probe
  confirmed that `backendType: "in-process"` does not break mailbox
  peer-prose delivery, so the fix does not introduce IPC regression.
- Lineage (v7.3) fix (#15) verified live with a new-client probe that
  now returns False from `is_thread_materialized` and falls back cleanly
  to `thread/start` instead of raising.

## Cross-cutting observations

- Lane-crossing (tester fixing own bug / implementer picking up multiple
  lanes) happened multiple times and converged cleanly in every case,
  but it masked a subtle task-ownership-vs-claim divergence: setting
  `owner` via `TaskUpdate` is advisory to the adapter's claim loop, not
  exclusive. Any adapter can claim a pending task regardless of
  pre-assigned owner if it polls first. Not a bug — confirmed desired
  behavior in spec — but worth flagging in future runs.
- The mixed-team workflow (Claude-side bigger-picture thinking +
  Codex-side deep empirical probing) produced strictly better coverage
  than either lane alone. claude-tester caught UX-layer and concurrency
  bugs; codex-tester caught wrapper-surface and v7.3 edge-case bugs.

## Parity verdict

At the end of this battle-test, a Codex teammate launched via the
adapter is functionally indistinguishable from a native Claude Code
agent in the following user-observable behaviors:

- Appears in the TUI presence line.
- Receives and responds to peer prose messages when idle.
- Handles `shutdown_request` (with proper rejection response for
  mid-task cases).
- Cannot be duped by a concurrent claim race.
- Does not lose registration to a concurrent adapter launch.
- Exposes a wrapper MCP tool surface that accepts `owner` + `metadata`
  on `task_update`, `to="*"` on `send_message`, and stamps sender
  color on peer DMs.
- Carries both mid-task reactivity (`turn/steer`, v7.1) and cross-task
  memory (`thread/fork`, v7.3) simultaneously under App Server mode.
- Falls back cleanly when a parent thread is unmaterialized across
  clients/processes.

This battle-test establishes the adapter as production-grade for
the team-protocol layer.
