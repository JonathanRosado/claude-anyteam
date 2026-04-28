# Post-everything regression sweep (#56)

Date: 2026-04-28
Lane: `proto-rev/impl/post-everything-regression-sweep`
Integration HEAD verified: `2900940` (`diagnostics: trace wrapper MCP tool discovery`)
Base range surveyed: `eff6a3d..2900940` (28-commit cumulative protocol-revision stack)

## Verdict

Clean. No fixes required; report-only.

## Verification results

### 1. Full pytest

Command:

```bash
PYTHONPATH=src /home/rosado/Projects/codex-teammate/.venv/bin/pytest -q
```

Result:

```text
998 passed, 2 deselected, 1 warning in 46.46s
```

### 2. Lint/typecheck

`pyproject.toml` contains no `[tool.ruff]` or `[tool.mypy]` configuration, so ruff/mypy were skipped per task instructions.

### 3. Cross-backend smoke checks

Grep/read-only smoke checks passed:

- #27 send-message visibility invariant: `src/claude_anyteam/wrapper_server.py` `send_message` tool docstring states plain text output is not visible and models must call the tool; prompt fragments in `src/claude_anyteam/prompts.py` and backend prompts retain the same invariant.
- #32 protocol tool discovery: `read_config` returns top-level `protocol_tools` in `src/claude_anyteam/wrapper_server.py`; live wrapper call also returned `protocol_tools` with raw Codex tool names.
- #40 capability startup validation: Codex, Gemini, and Kimi backend metadata paths call `assert_known_capabilities(...)` before registration/rich manifest construction; registry validation is backed by `CAPABILITY_HOOKS` / `CAPABILITY_RUNTIME_REGISTRY` in `src/claude_anyteam/capabilities.py`.
- #28 event-driven inbox watcher: `WatchInbox.for_team(...)` and `wait_for_change(adaptive_wait_s(...))` are wired in all three loops:
  - `src/claude_anyteam/loop.py`
  - `src/claude_anyteam/backends/gemini/loop.py`
  - `src/claude_anyteam/backends/kimi/loop.py`
- #41 delegated batch summary visibility: `BatchSummaryChild`, `BatchSummaryPayload`, and `batch_summary` kind are present in `src/claude_anyteam/messages.py`; wrapper `task_batch_summary` emits `kind="batch_summary"`.

## Git / merge disposition

No code issues found. No fix branch commit or FF-merge performed; this is report-only.
