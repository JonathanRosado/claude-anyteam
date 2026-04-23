# Shim restart-resilience follow-up

Today the leader-spawned shim path is not restart-resilient. The shim launches the adapter as a normal child in the leader's process group and does not call `setsid()` or otherwise detach before `execv`. That creates two separate failure modes:

- **Functional:** if Claude Code exits by killing its child/process group, the adapter dies and any in-flight work stops.
- **UX-only:** the teammate row in Claude Code's TUI is a leader-side in-memory mirror task, not durable state ([research note](research.md)). Even if the adapter survives, a restarted leader will not recreate the row automatically.

This split matters: process survival is the hard requirement; TUI reappearance is only visibility.

## Public evidence on Claude Code exit behavior (searched 2026-04-22)

I found **no public Anthropic statement or changelog entry** that defines the leader's exit-time signal policy for spawned teammates.

Public evidence is mixed:

- Official [agent-team docs](https://code.claude.com/docs/en/agent-teams) say cleanup is not a universal kill path: cleanup "fails if any [teammates] are still running," so users must shut them down first. The same page documents **"orphaned tmux sessions"** after a team ends and says `/resume` and `/rewind` **do not restore in-process teammates**.
- Official issue [#1935](https://github.com/anthropics/claude-code/issues/1935) reports Claude Code leaving **orphaned MCP server processes after normal exit**.
- Official issues [#2148](https://github.com/anthropics/claude-code/issues/2148) and [#6594](https://github.com/anthropics/claude-code/issues/6594) report other flows (parallel **subagents**, not teammates) where Claude Code **does terminate multiple child processes together**.

**Inference:** Claude Code clearly has child-kill paths, but public sources do **not** establish whether leader exit for teammate children is always kill, never kill, or path-dependent. That uncertainty determines how necessary a detach is versus a pure reconnect path.

| Approach | What it does / resolves | Complexity | Tradeoffs |
|---|---|---:|---|
| **A. `setsid` in shim** | Detach before `execv`; adapter survives terminal close and leader process-group signals. Resolves the **functional** risk locally. TUI row still disappears on leader restart. | **Low** (~1 LoC) | Leader loses direct lifecycle control; shutdown can no longer assume a simple SIGTERM reaches the adapter. |
| **B. Adapter self-daemonization** | Adapter detaches only after `register()` succeeds, so a launched teammate can outlive leader exit. Also resolves the **functional** risk. TUI row still needs separate reconnect logic. | **Medium/High** | Touches adapter core, complicates testing, and requires stdout/stderr redirection plus daemon lifecycle hygiene. |
| **C. Leader reconnect path** | On leader startup, re-register the in-memory mirror task for an already-running `codex-*` member with a writable inbox. Resolves the **UX-only** visibility gap and restores messaging/task presence **if** the adapter survived. | **High / upstream** | Does not save a dead adapter; requires Claude Code changes outside this repo. |

If we want the cheapest repo-local hedge against worst-case exit behavior, **Approach A** is the pragmatic default. If public behavior already leaves children alive in practice, **Approach C** becomes the more valuable follow-up because it restores visibility without changing adapter lifecycle.
