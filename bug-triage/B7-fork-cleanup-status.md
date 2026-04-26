# B7 fork cleanup status

## Inventory
- Project memory read: `/home/rosado/.claude/projects/-home-rosado-Projects-codex-teammate/memory/MEMORY.md`.
- Initial shipped-code grep command run exactly as requested.
- In-scope `src/claude_anyteam` / `pyproject.toml` hits to edit: `src/claude_anyteam/protocol_io.py`, `src/claude_anyteam/wrapper_server.py`, `src/claude_anyteam/loop.py`, `src/claude_anyteam/messages.py`, `src/claude_anyteam/smoke.py`, `src/claude_anyteam/roundtrip_m1.py`, `src/claude_anyteam/shutdown_probe.py`, `pyproject.toml`.
- Out-of-scope hit flagged, not edited: `src/claude_teams/opencode_client.py` contains a `git+https://github.com/cs50victor/...` command string, but this pass explicitly says not to touch `src/claude_teams/`.
- Vendor tree inventoried: `vendor/claude-teams/{LICENSE,UPSTREAM-README.md,UPSTREAM-pyproject.toml,VENDORING.md,stress_test_lifecycle.py,upstream-tests/}`.
- Read in full before edit/delete decisions: `src/claude_anyteam/protocol_io.py`, `src/claude_anyteam/wrapper_server.py`, `src/claude_anyteam/loop.py`, `src/claude_anyteam/messages.py`, `src/claude_anyteam/smoke.py`, `src/claude_anyteam/roundtrip_m1.py`, `src/claude_anyteam/shutdown_probe.py`, `pyproject.toml`, `vendor/claude-teams/stress_test_lifecycle.py`, and every file in `vendor/claude-teams/upstream-tests/`.

## Baseline tests
- `git status --short` before changes: only `?? .claude/` and `?? bug-triage/`.
- `pytest` before changes: collection failed before running tests because `fastmcp` is missing in the current Python environment.
  - Reported collection: `collected 484 items / 1 error / 2 deselected / 482 selected`.
  - Error: `tests/test_wrapper_contract.py` import failed with `ModuleNotFoundError: No module named 'fastmcp'`.
  - Effective pass/fail count before: 0 passed, 1 collection error, 2 deselected.

## Plan
1. Delete fixed upstream metadata files under `vendor/claude-teams/`.
2. Delete or move `stress_test_lifecycle.py` based on usefulness; move/rerun upstream tests under `tests/protocol/`, keeping only passing/active tests.
3. Remove emptied vendor directories.
4. Rewrite in-scope `src/claude_anyteam` comments/docstrings to refer to the team protocol / `claude_teams`, preserving the safety and locking rationale.
5. Rewrite `pyproject.toml` dependency comments without vendoring language.
6. Rerun `pytest`; rerun grep and record remaining in-scope/out-of-scope hits.

## Changes applied
- See step-by-step entries and final file summary below.

## Final tests
- See final test results below.

## Open questions
- See final open questions below.

### Step a — fixed vendor metadata deletion
- Deleted `vendor/claude-teams/LICENSE` per explicit user directive.
- Deleted `vendor/claude-teams/UPSTREAM-README.md`.
- Deleted `vendor/claude-teams/UPSTREAM-pyproject.toml`.
- Deleted `vendor/claude-teams/VENDORING.md`.

### Step b — stress script / upstream tests decision, first pass
- `vendor/claude-teams/stress_test_lifecycle.py` read and executed once before moving/deleting: 15/15 scenario checks passed. It is useful but was a print-driven script, so I will preserve the scenarios as a proper pytest test at `tests/test_lifecycle_stress.py` instead of carrying top-level side effects forward.
- Moved `vendor/claude-teams/upstream-tests/` to `tests/protocol/` for pass/fail evaluation.
- Recreated the lifecycle stress scenarios as `tests/test_lifecycle_stress.py` and deleted the original print-driven script from `vendor/claude-teams/stress_test_lifecycle.py`.
- Ran preserved protocol tests excluding the server MCP suite: `pytest tests/test_lifecycle_stress.py tests/protocol/test_messaging.py tests/protocol/test_models.py tests/protocol/test_opencode_client.py tests/protocol/test_spawner.py tests/protocol/test_tasks.py tests/protocol/test_teams.py -q` → 182 passed, 1 warning.
- Ran `pytest tests/protocol/test_server.py -q` after move; it failed at collection with `ModuleNotFoundError: No module named 'fastmcp'`. Per keep-only-passing instruction, deleted `tests/protocol/test_server.py` instead of carrying a currently failing moved test forward.

### Step c — empty vendor directory removal
- Removed empty `vendor/claude-teams/`.
- Removed empty `vendor/`.

### Steps d-g — shipped-code comment/docstring rewrites
- Rewrote `src/claude_anyteam/protocol_io.py` module docstring and inline comments to describe the team-protocol I/O surface and serializer/locking rationale without upstream framing.
- Rewrote `src/claude_anyteam/wrapper_server.py` docstring as “Why a narrowed MCP surface” and updated blocked-tool comments while preserving the destructive-lifecycle safety argument.
- Rewrote `src/claude_anyteam/loop.py` comments to refer to team-protocol calls / protocol mark-as-read behavior.
- Rewrote `src/claude_anyteam/messages.py` docstring and shutdown-request note to refer to `claude_teams.models` / team protocol.
- Rewrote additional in-scope `src/claude_anyteam` hits found by inventory: `smoke.py`, `roundtrip_m1.py`, `shutdown_probe.py`.
- Rewrote `pyproject.toml` dependency comments to describe runtime purposes without vendored framing.

### Verification
- Requested grep command after edits:
  - `grep -rn "cs50victor\|upstream\|vendored\|vendor" src/ pyproject.toml --include="*.py" --include="*.toml"`
  - Remaining hit: `src/claude_teams/opencode_client.py:81` command string pointing at `github.com/cs50victor/claude-code-teams-mcp`.
  - This file is under `src/claude_teams/`, which was explicitly out of scope for this pass, so it is flagged but not edited.
- In-scope shipped-code grep restricted to `src/claude_anyteam pyproject.toml`: zero hits.
- Historical docs grep found references in these out-of-scope docs, not edited: `docs/internal/2026-prototype/architecture-decision.md`, `docs/internal/2026-prototype/final-review.md`, `docs/internal/2026-prototype/prior-art.md`, `docs/internal/2026-prototype/protocol-spec.md`, `docs/internal/2026-prototype/shim-restart-resilience.md`, `docs/internal/2026-prototype/v7-architecture.md`, `docs/internal/2026-prototype/v7.3-implementation-notes.md`, `docs/internal/2026-prototype/v7.3-live-acceptance.md`, `docs/internal/appstate-hook-findings.md`, `docs/internal/gemini-integration/approval-bridge-and-steer-design.md`, `docs/internal/gemini-research-reverse.md`, `docs/internal/kimi-integration/kimi-runtime.md`, `docs/internal/kimi-integration/kimi-skill-and-agent-research.md`, `docs/internal/kimi-rationale.md`, `docs/internal/known-issues/leader-outbound-addressing.md`, `docs/internal/spawn-research-brief.md`, `docs/internal/spawn-research-findings.md`, `docs/internal/spawn-research-phase2-brief.md`, `docs/internal/strategic-roadmap.md`.

## Final tests
- `pytest` after changes: collection failed before running tests because `fastmcp` is still missing in the current Python environment.
  - Reported collection: `collected 666 items / 1 error / 2 deselected / 664 selected`.
  - Error: `tests/test_wrapper_contract.py` import failed with `ModuleNotFoundError: No module named 'fastmcp'`.
  - Effective pass/fail count after: 0 passed, 1 collection error, 2 deselected.
  - This is the same blocker as the baseline `pytest`; no additional collection error appeared.
- Added/moved active protocol coverage check: `pytest tests/test_lifecycle_stress.py tests/protocol/test_messaging.py tests/protocol/test_models.py tests/protocol/test_opencode_client.py tests/protocol/test_spawner.py tests/protocol/test_tasks.py tests/protocol/test_teams.py -q` → 182 passed, 1 warning.
- Syntax check: `python -m py_compile` on edited/moved Python files completed successfully.

## Open questions
- The original required grep over `src/ pyproject.toml` is not zero because `src/claude_teams/opencode_client.py` still references `github.com/cs50victor/claude-code-teams-mcp`. I left it untouched due the explicit out-of-scope instruction for all `src/claude_teams/` code.
- Full `pytest` remains blocked by missing `fastmcp` in this environment, same as baseline.

## Final file summary

### Deleted files (14)
- `vendor/claude-teams/LICENSE`
- `vendor/claude-teams/UPSTREAM-README.md`
- `vendor/claude-teams/UPSTREAM-pyproject.toml`
- `vendor/claude-teams/VENDORING.md`
- `vendor/claude-teams/stress_test_lifecycle.py`
- `vendor/claude-teams/upstream-tests/__init__.py`
- `vendor/claude-teams/upstream-tests/conftest.py`
- `vendor/claude-teams/upstream-tests/test_messaging.py`
- `vendor/claude-teams/upstream-tests/test_models.py`
- `vendor/claude-teams/upstream-tests/test_opencode_client.py`
- `vendor/claude-teams/upstream-tests/test_server.py`
- `vendor/claude-teams/upstream-tests/test_spawner.py`
- `vendor/claude-teams/upstream-tests/test_tasks.py`
- `vendor/claude-teams/upstream-tests/test_teams.py`
- Removed empty directories: `vendor/claude-teams/`, `vendor/`.

### Moved / preserved files (8)
- `vendor/claude-teams/upstream-tests/__init__.py` → `tests/protocol/__init__.py`
- `vendor/claude-teams/upstream-tests/conftest.py` → `tests/protocol/conftest.py`
- `vendor/claude-teams/upstream-tests/test_messaging.py` → `tests/protocol/test_messaging.py`
- `vendor/claude-teams/upstream-tests/test_models.py` → `tests/protocol/test_models.py`
- `vendor/claude-teams/upstream-tests/test_opencode_client.py` → `tests/protocol/test_opencode_client.py`
- `vendor/claude-teams/upstream-tests/test_spawner.py` → `tests/protocol/test_spawner.py`
- `vendor/claude-teams/upstream-tests/test_tasks.py` → `tests/protocol/test_tasks.py`
- `vendor/claude-teams/upstream-tests/test_teams.py` → `tests/protocol/test_teams.py`

### Added files (1)
- `tests/test_lifecycle_stress.py` — pytest version of the useful lifecycle stress scenarios.

### Edited files (8)
- `pyproject.toml`
- `src/claude_anyteam/loop.py`
- `src/claude_anyteam/messages.py`
- `src/claude_anyteam/protocol_io.py`
- `src/claude_anyteam/roundtrip_m1.py`
- `src/claude_anyteam/shutdown_probe.py`
- `src/claude_anyteam/smoke.py`
- `src/claude_anyteam/wrapper_server.py`
