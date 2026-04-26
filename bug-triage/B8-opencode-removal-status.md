# B8 opencode integration removal status

## Inventory
- Read `/home/rosado/.claude/projects/-home-rosado-Projects-codex-teammate/memory/MEMORY.md` on 2026-04-26.
- Existing working tree already had unrelated changes in `src/claude_anyteam/`, packaging files, `vendor/`, and `tests/`; this pass avoided those unrelated paths.
- Initial grep found removal-scope references in:
  - `src/claude_teams/opencode_client.py`
  - `src/claude_teams/spawner.py`
  - `src/claude_teams/models.py`
  - `src/claude_teams/server.py` (extra import/lifecycle/spawn/push/cleanup references discovered during inventory; removed so deleting the client keeps imports resolving and final grep is clean)
  - `tests/protocol/test_opencode_client.py`
  - `tests/protocol/test_models.py`
  - `tests/protocol/test_spawner.py`
- Scope-out reference spotted and left untouched: `docs/internal/2026-prototype/protocol-spec.md:860`.
- Generated `__pycache__` files under `src/` and `tests/` also contained stale binary matches after tests; removed generated pycache before final grep.

## Baseline tests
- `uv run pytest`: 684 passed, 2 deselected, 1 warning in 23.17s.

## Plan
1. Run baseline `uv run pytest` and record pass count.
2. Delete the opencode client/test files.
3. Remove opencode plumbing from `spawner.py`, preserving `backend_type` and adding a concise "Adding a new external backend" reference docstring.
4. Remove `opencode_session_id` from `models.py`.
5. Remove opencode-specific tests from `test_models.py` and `test_spawner.py`.
6. Remove remaining opencode wiring from `server.py` so imports resolve after deleting the client.
7. Verify imports, run `uv run pytest`, clear generated pycache, and run final grep.

## Changes applied

### Files deleted
- `src/claude_teams/opencode_client.py`
- `tests/protocol/test_opencode_client.py`

### Files edited
- `src/claude_teams/spawner.py`
  - Removed deleted client import, prompt wrapper, model discovery helper, attach-command helper, opencode-specific `spawn_teammate()` parameters, session-id plumbing, backend branches, prompt send, and cleanup calls.
  - Added hard failure for unsupported `backend_type` values.
  - Added this docstring near `spawn_teammate()`:

```python
"""Spawn a teammate process in tmux.

Adding a new external backend:
- Use `TeammateMember.backendType` as the per-member discriminator.
- Add an explicit per-backend parameter block to `spawn_teammate`.
- Keep the session lifecycle explicit: verify-config → create-session →
  send-prompt → cleanup.
- Provide a per-backend tmux attach command for operator visibility.
"""
```

- `src/claude_teams/models.py`
  - Removed `opencode_session_id` / `opencodeSessionId` from `TeammateMember`.
  - Left `backend_type` / `backendType` intact.
- `src/claude_teams/server.py`
  - Removed deleted client imports, backend detection/listing, session discovery, push notification, spawn-tool forwarding, and cleanup paths.
  - Kept supported spawn backend schema to `Literal["claude"]`; unsupported backend values now fail at schema/tool/spawner level rather than migrating.
- `tests/protocol/test_models.py`
  - Removed opencode backend serialization/deserialization tests, session-id tests, and the shutdown-approved case using that backend value.
- `tests/protocol/test_spawner.py`
  - Removed attach-command tests, model-discovery tests, binary-discovery cases for the removed backend, and all `spawn_teammate(..., backend_type="opencode", ...)` cases.

## Final tests
- Targeted: `uv run pytest tests/protocol/test_models.py tests/protocol/test_spawner.py` → 47 passed in 0.35s.
- Import check: `uv run python - <<'PY' ... import claude_teams.models/spawner/server ... PY` → imports ok.
- Full: `uv run pytest` → 633 passed, 2 deselected, 1 warning in 22.37s.
- Final grep after clearing pycache: `grep -rn "opencode\|OpenCode\|OPENCODE" src/ tests/` → zero hits.
- Scope-out archived doc grep still shows only `docs/internal/2026-prototype/protocol-spec.md:860`.
- `git diff --check` → clean.

## Open questions
- None.
