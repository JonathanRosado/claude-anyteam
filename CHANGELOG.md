# Changelog

All notable changes to claude-anyteam are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [0.8.2] — 2026-05-04

Patch release shipping the v0.8.1 plugin-manifest lock-step work plus a release-CI fix-forward. v0.8.1's auto-release tagged at the merge commit but the `test` job failed on a hardcoded `assert package['version'] == '0.8.0'` literal in `tests/test_npm_package.py` that the v0.8.1 PR didn't touch (the literal was redundant with two existing lock-step tests, and was missed by my version-string grep because the test file used single-quoted Python literals while the grep regex only covered double-quoted JSON literals). The v0.8.1 tag and GitHub release were deleted; v0.8.2 replays the manifest bumps cleanly with the test fix included.

### Fixed

- **`tests/test_npm_package.py:15`** — removed the hardcoded `assert package['version'] == '0.8.0'`. Version is already locked in step by `tests/test_manifest_versions_locked.py` (four-way) and `test_pyproject_version_matches_npm_version` (two-way) in the same file. The hardcoded literal forced a manual edit on every release and was the proximate cause of the v0.8.1 test failure. Single source of truth for version equality lives in the lock-step tests; this contract test now covers only npm-specific fields (name, bin, scripts, engines, dependencies).

### Carried over from the never-published v0.8.1

- **All four user-facing manifests in lock-step at `0.8.2`** (`npm/package.json`, `pyproject.toml`, `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`).
- **Four-way manifest version lock-step in CI** — `.github/workflows/auto-release.yml` now triggers on changes to any of the four manifests and fails the build if any disagree.
- **`tests/test_manifest_versions_locked.py`** — pytest-time lock-step assertion (defense-in-depth alongside the CI gate).

### Net effect for users

After v0.8.2 ships to npm + PyPI, the marketplace tree at `~/.claude/plugins/marketplaces/claude-anyteam/` will pull manifests advertising 0.8.2. The next `/plugin update claude-anyteam@claude-anyteam` will repin from `cache/.../0.5.0/` to a new `cache/.../0.8.2/` directory containing all 3 skills (including `diagnose`), the manifest-driven `help` skill from PR #41, and every other change shipped between v0.5.0 and now.

## [0.8.1] — 2026-05-04

Patch release fixing a quiet plugin-marketplace version-drift bug that pinned every user on the marketplace install path to the v0.5.0 skill set. No code-behavior changes; this is pure release-process hardening.

### Fixed

- **`.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json` were drifted to v0.5.0 / v0.1.0** while `pyproject.toml` and `npm/package.json` had been bumped through v0.6 → v0.7 → v0.8 in lock-step. Claude Code's plugin marketplace keys upgrade decisions off the manifests it reads (`marketplace.json`'s advertised version + the per-plugin `version`), so it never advertised v0.6 / v0.7 / v0.8 to users. Result: every user on the marketplace install path remained pinned to the v0.5.0 plugin cache directory (`~/.claude/plugins/cache/claude-anyteam/claude-anyteam/0.5.0/`), missing every skill change since — including the `diagnose` skill (added in v0.8.0), the manifest-driven `help` skill reshape (#41), and the prompt updates for `codex-jr` disambiguation. Both manifests are now bumped to **0.8.1** in lock-step with the python and npm package versions.

### Added

- **Four-way manifest version lock-step in CI** (`.github/workflows/auto-release.yml`). Pre-v0.8.1 the workflow only checked `npm/package.json` against `pyproject.toml` and only fired on changes to those two files. The two `.claude-plugin/` manifests were ignored entirely — no trigger path, no version comparison. Now all four (`npm/package.json`, `pyproject.toml`, `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`) are in the workflow's `paths:` filter and the `Read manifest versions` step fails the build if any disagree. Workflow comment explains the historical motivation so future contributors understand why the four-way check exists.
- **`tests/test_manifest_versions_locked.py`** — pytest-time lock-step assertion that catches the same drift in the developer loop, before any push reaches `auto-release.yml`. Defense-in-depth: CI is the gate, this test is the rapid-feedback layer. Asserts all five version fields (the four manifests, with `marketplace.json`'s `metadata.version` and `plugins[0].version` checked independently) are identical, plus a PEP-440 shape sanity check.

### Why this matters in operational terms

Users on the marketplace install path who upgraded the python tool via `uv tool install --reinstall claude-anyteam` (or `pipx upgrade`, etc.) saw new behavior in the CLI but kept the v0.5.0 skill set in their Claude Code session. The skill content discovery (per `feedback_capability_decl_vs_flatten` and the v0.8.0 manifest-driven discovery work) only manifests if Claude Code reads the new SKILL.md files, which it can't until the plugin cache repins. Lock-step CI plus the pytest-time check make this class of drift impossible going forward; one bumps all four or the build fails before merge.

## [0.8.0] — 2026-04-29

The protocol-revision drop. Substrate hardening across the three north stars (`CLAUDE.md` §1 harness preservation, §2 visibility parity, §3 peer efficiency), measured against the S6/S7/S8/S5 cross-backend stress harness and validated head-to-head against a native-Claude pair (S6n). Bumped to **0.8.0** because main shipped 0.7.2-0.7.11 (installer hardening, CTA polish) while this proto-rev branch was open; the protocol-revision is a major-style change relative to the 0.7.x patch series.

Headline numbers (post-fix, integration HEAD `294eb24`):

- **5 cross-backend stress scenarios verified**: S6 codex+codex 15/15, S6n claude+claude 15/15, S7 gemini+codex 15/15, S8 kimi+codex 15/15, S5+W10 4-backend 30/30.
- **M5 turn-failure rate**: 0.000 across all scenarios.
- **M13 collisions**: 0 across all scenarios.
- **s1_flatten_violations / harness_preservation_violations**: 0 across all scenarios.
- **Test suite**: 803 → **1059 passed** after pre-merge cleanup.
- **Native-Claude head-to-head (S6n vs S6)**: substrate-comparable. Wall clock 1149s vs 1404s (native 18% faster); M11a p50 73s vs 36.5s (native 2x slower per-DM, model-driven). Substrate failure metrics tied at zero.

### Added

- **§1 capability layer**: typed capability declarations + hook registry (`src/claude_anyteam/capabilities.py`) and capability-manifest cache (`capability_manifest.py`) with peer-prompt-fragment composition, eager prewarm, and bounded supervisor.
  - Capability vocabulary: `turn_steer`, `thread_fork`, `permission_bridge`, `live_tool_events`, `structured_output`, `headless_invocation`, `session_resume`, `plan_mode`, `trust_modes`, `native_skills`, `large_context`, `accepts_peer_steer`, `soft_non_progress_watchdog`.
  - Manifest-gated peer-steer enforcement (recipient interpretation, not sender structure).
- **§2 visibility surface**: `visibility-tail` filesystem CLI with JSON/filter/since/color/multi-line tri-card and WebSocket `--serve` mode; `headless_visibility.py` backend-agnostic event normalizer (+392 LOC); `wrapper_mcp_diagnostics.py` instrumented tool-discovery; `checkpoint_commit` MCP tool for app-server-turn-timeout work salvage; `claude-anyteam diagnose` skill + 902-line read-only inspector CLI; uniform `recipient`/`to` field stamping on all `send_message` tool_events.
- **§3 peer efficiency**: WatchInbox `fs.watch` event-driven inbox (`src/claude_anyteam/watch_inbox.py`); BatchedSender 50ms debounce; attachment protocol (4096-char auto-spill); typed lifecycle payloads; L4 `messageKind` discriminator across codex/gemini/kimi; SendMessage flap repair (#51).
- **`claude_native` backend**: bridge at `src/claude_anyteam/backends/claude_native/` (cli, config, invoke, loop, prompts) + focused test coverage. Wraps `claude --print --output-format stream-json --verbose --mcp-config <wrapper>` so native Claude becomes a peer of codex/gemini/kimi, with Claude Code's native Task/Skill/WebFetch/Read/Edit/Write/Bash surface preserved end-to-end.
- **`docs/adding-a-backend.md`**: 492-line contributor walkthrough for adding a 5th harness, modeled on the kimi backend addition.
- **Stress / verification harness**: scenarios S5–S10; workloads W1–W10; `score_collab` / `score_quality` / `score_throughput` test suites (+1,274 LOC combined).
- **App Server / backend integration**: `app_server.py` `turn/steer` mid-task injection plumbing; codex `task_complete` payload schema + mid-turn prose handler; gemini ACP `--trust default|plan` with team-lead approval bridge; kimi v1 headless prompt-plus-validation structured outputs.

### Changed

- Auth-classifier (`auth_preflight.py`) now uses regex with digit boundaries (`(?<!\d)401(?!\d)` / `(?<!\d)429(?!\d)`) so timestamps like `20260429` no longer mis-tag a 401 as a 429.
- `score_collab` extended from `prefix_v1` to `kind_v1` classifier — reads structured `kind` envelope field; fallback to body prefix preserved.
- `tools/stress/run_scenario.py` `_load_scorers` is now self-sufficient on `PYTHONPATH` (resolves project root from `__file__`) so detached `setsid nohup` launches auto-score without manual env setup.
- Stress sandbox marker now carries `state=active|completed|aborted` with owning PID; cleanup respects live markers.

### Fixed

- M13 peer-prose-as-steer false positives + `send_plain_message` bypass (#50).
- App-server turn-timeout work loss: `checkpoint_commit` MCP tool + configurable turn timeout plumbing.
- Worktree-per-teammate isolation guard (#48).

### Documented

- `references/external-claude-code-re/proto-rev-execution-log/d1-validation-final.md`: full ladder of stress runs + per-scenario verification appendices for S6, S6n, S7, S8 v2, S8 rerun, S5+W10.
- `references/external-claude-code-re/proto-rev-execution-log/kimi-peer-dm-investigation.md`: root-cause analysis of the kimi-pair zero-send pattern (auth preflight failure + auth-classifier mis-tag).
- `docs/adding-a-backend.md`: 492-line guide for contributors adding a new harness.

### Known follow-ups (post-ship)

- Native-Claude turn-completion test coverage (currently the new claude_native backend is locked at the unit-test level and exercised end-to-end via S6n + S2; no granular integration test for full turn lifecycle).
- M11a classifier coverage on S6 with kind_v1 is 0.367; remaining 0.633 are codex envelopes whose `kind` value isn't yet in the mapping. Future enhancement: surface unrecognized kind values for triage.
- Kimi v1 "no send_message" pattern under W7 — was 100% explained by auth failure in S8 v2; monitor on future runs to confirm post-fix behavior is stable.

### Fixed in-flight (stress runs informed the fix)

- **M13 native-Claude false positives** (#3 night-shift task; commit `9310c44`): Diagnosed the 4 S2 collisions as native-Claude schema preambles being mis-flagged as prose-fallback collisions. Fix in `tools/stress/score_collab.py` (narrow guard for archived schema-preamble outputs) + `src/claude_anyteam/backends/claude_native/invoke.py` (recovery path for embedded schema JSON in prose-preambled output). Re-scoring S2 with the guard drops M13 from 4 → 0 (collision rate 0.0). Suite 1055 → 1058 with regression coverage.

[0.8.0]: https://github.com/JonathanRosado/claude-anyteam/pull/27
