# Changelog

All notable changes to claude-anyteam are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- **`wrapper_tool_failure_unrecovered` visibility envelope** (issue #49 / PR #60) surfaces Codex App Server wrapper-MCP tool failures that are not followed by `turn_progress`, `tool_event`, or `artifact_event` recovery activity within the discriminator window. The window is configurable with `--wrapper-tool-failure-window-s`, `CLAUDE_ANYTEAM_WRAPPER_TOOL_FAILURE_WINDOW_S`, and the per-teammate agents-file key `wrapper_tool_failure_window_s`.
- **Codex sqlite WAL-bloat diagnostics/mitigation** (#43 / PR #54 follow-up): `claude-anyteam diagnose --codex-log-bloat` reports `logs_*.sqlite-wal` size pressure, and Codex App Server spawn now emits typed `visibility_degraded` events when WAL bloat is detected or checkpoint remediation cannot complete. Operator controls are centrally registered env vars:
  - `CLAUDE_ANYTEAM_CODEX_SQLITE_WAL_WARN_THRESHOLD_BYTES` — warn threshold; default `104857600` bytes (100 MiB), bounded `[0, 10737418240]` (10 GiB).
  - `CLAUDE_ANYTEAM_CODEX_SQLITE_WAL_CHECKPOINT` — enable/disable the pre-spawn sqlite checkpoint attempt; default enabled (set `0`, `false`, `no`, `off`, or `disabled` to skip).
  - `CLAUDE_ANYTEAM_CODEX_SQLITE_WAL_CHECKPOINT_TIMEOUT_S` — checkpoint timeout budget; default `10s`, bounded `[0.001, 60]`; the same budget caps the aggregate time spent across all bloated WALs.
- **Fast team teardown surfaces for `team-kill`.** Adds the `claude-anyteam team-kill --team <team> --force [--purge]` CLI verb, the lead-only `force_kill_team` MCP tool, and the wrapper `BLOCKED_TOOLS` extension that keeps destructive teardown unavailable to routed teammates. Follow-up sharpens bound the graceful budget to `[1, 60]` seconds, add `CLAUDE_ANYTEAM_TEAM_KILL_GRACEFUL_TIMEOUT_S`, parallelize remaining force-kills, and record a `team_kill_completed` visibility-envelope audit entry.

### Changed

- **Breaking #48 routed-prefix spawn guard:** spawning a `codex-*`, `gemini-*`, or `kimi-*` teammate without a matching per-teammate agents-file config now exits 2 with `spawn_shim.bare_prefix_refused` instead of silently proceeding to adapter defaults. Set `CLAUDE_ANYTEAM_ALLOW_BARE_PREFIX=1` only when you intentionally want the old bare-prefix default behavior; that override is now audited with `spawn_shim.bare_prefix_allowed_via_override`.
- **`turn_timeout_s` default bumped 900 → 1800s** (task #5). The prior 900s cap interrupted legitimately long Codex turns (large test suites, multi-file refactors at `xhigh` effort) and was the dominant pain the visibility-driven RFC at `docs/design/timers-vs-visibility.md` (issue #50) was written to address. Cap remains 3600s — no single turn should run longer than an hour. Affects Codex App Server (`turn_timeout_s`), Codex Exec, and `claude-native` (`CLAUDE_ANYTEAM_CLAUDE_TURN_TIMEOUT_S`). Set `--turn-timeout-s 900` or `CLAUDE_ANYTEAM_TURN_TIMEOUT_S=900` to restore the prior default for any single teammate.
- **`non_progress_warn_s` default flipped from 300s to None (opt-in)** (task #5 / RFC #50 Phase B). The soft non-progress watchdog now ships disabled by default. Lead reads typed visibility events instead — see `docs/design/timers-vs-visibility.md` for the migration plan and the new envelope taxonomy. Existing users who want the prior behavior set `--non-progress-warn-s 300` or `CLAUDE_ANYTEAM_NON_PROGRESS_WARN_S=300`.
- **`non_progress_warn_s` range upper bumped 900 → 1800s** so opt-in users can scale the watchdog proportionally to the new `turn_timeout_s` default. Range now `[60, 1800]` when explicitly set.
- **Kimi / Gemini subprocess `timeout_s` defaults bumped 600 → 1800s** in `backends/kimi/invoke.py` and `backends/gemini/invoke.py` for consistency with the new `turn_timeout_s` default. Production main loops (`backends/{kimi,gemini}/loop.py`) build kwargs without `timeout_s` and call `headless_invoke.run(**kwargs)`, so production paths inherit the new 1800s default — not headless-only.

### Fixed

- **`non_progress_interrupt_s` decoupled from the soft-watchdog flag** (PR #52 block-fix per opus-reviewer 2026-05-12). The opt-in overnight-kill knob now works correctly with the new `non_progress_warn_s=None` default. **Semantic split** — operators reading the docs should know which path fires when:
  - **When `non_progress_warn_s` is set (opt-in to soft watchdog):** the interrupt preserves the **original semantics** verbatim — fires only after the soft warn has fired, no later checkpoint was observed since the warn, AND total turn elapsed time (`now - turn_started_at`) exceeds `non_progress_interrupt_s`. The anchor is turn-start.
  - **When `non_progress_warn_s` is `None` (new default):** the interrupt uses **silence-window semantics** — fires when no observable progress for `non_progress_interrupt_s` seconds (`now - last_progress_at >= non_progress_interrupt_s`). The anchor is last-progress, not turn-start. This is the §7.1 overnight-kill semantic the RFC carve-out promised.
  - The log payload's `warn_active` field tells which path fired post-hoc.
  - Both semantics are correct for their use case; the split exists because warn-as-precondition is meaningful when warn fires but vacuous when it doesn't, so we substitute the most-direct equivalent ("no progress for interrupt_s seconds") in the absence-case.

### Documentation

- New design RFC at `docs/design/timers-vs-visibility.md` (issue #50): audits every timer in the wrapper, classifies stall-detection vs bounded-I/O vs teardown, proposes four new typed envelopes (`wrapper_tool_failure_unrecovered`, `app_server_idle_quiet`, `subprocess_pressure`, `transport_alive`) and a model-emitted `working_on` contract that together replace the soft watchdog. Cites #40, #43, #49 as the false-positive cases.
- New contributor convention doc at `docs/conventions/visibility-envelopes.md`: write-time guide for PRs that add or extend a typed visibility envelope. Captures the eight checks (four-leg taxonomy, top-level kind vs `visibility_degraded`, `mailbox` flag semantics, four-fold config surface, bilateral range validation, CHANGELOG extension, boundary-case tests, RFC cross-reference) that emerged from PR #42 / #51 / #52 / #54 / #55 / #56 / #60 / #61 reviews. Anchors PR #42's `app_server_initialize_*` envelope set as the reference example.

### Migration notes

This is a **default change**, not an API break. Configs that set `turn_timeout_s` or `non_progress_warn_s` explicitly are unaffected. Teammates spawned with no overrides will see the new defaults next time the adapter starts. To preserve the prior behavior wholesale, add to your team config:

```json
{
  "turn_timeout_s": 900,
  "non_progress_warn_s": 300
}
```

Or set environment variables: `CLAUDE_ANYTEAM_TURN_TIMEOUT_S=900 CLAUDE_ANYTEAM_NON_PROGRESS_WARN_S=300`.

## [0.8.4] — 2026-05-05

**Cross-backend access to Claude Code skills.** Routed teammates (codex-*, gemini-*, kimi-*) can now seamlessly discover and follow Claude Code skills at `skills/<name>/SKILL.md` without any special instruction in the user's task. The first non-Claude backend that meets the user's request — "write me a cold email," "audit my SEO," "rewrite my hero copy" — fetches the relevant skill body via the wrapper-MCP and follows the prose verbatim. Empirically validated across 4 diverse domain tasks (marketing-ideas, cold-email, seo, copywriting) with codex-app-server backend; 4/4 hit rate.

### Added

- **`mcp_anyteam_list_skills`** and **`mcp_anyteam_invoke_skill`** wrapper-MCP tools (`src/claude_anyteam/wrapper_server.py`) backed by a shared discovery cache. `list_skills` returns metadata only; `invoke_skill(name)` returns the SKILL.md body verbatim in a typed envelope (`{skill_name, body, source_path}`) or `{error: "skill_not_found", skill_name}` on miss. The wrapper does not interpret or rewrite skill prose — backends interpret natively (§1 harness preservation).
- **Shared `skill_discovery` module** (`src/claude_anyteam/skill_discovery.py`) — single source of truth for scanning in-repo `skills/` plus installed marketplace skills at `~/.claude/plugins/marketplaces/<marketplace>/skills/<name>/SKILL.md`. Frontmatter parsing via simple YAML-ish scalar reader (no YAML dependency). Duplicates handled deterministically (first-write wins; in-repo skills win over marketplace copies of same name).
- **Skills prompt fragment** (`src/claude_anyteam/skills_fragment.py`) — composes a named `## Available Claude Code skills` block into routed-backend prompts at task-dispatch and prose-turn time. Token-overlap heuristic with stopword filter scores skill relevance to the task text; top 3 matches at score ≥ 2 are inlined as metadata + an explicit `mcp_anyteam_invoke_skill('<name>')` instruction. **No SKILL.md bodies inlined** — bodies fetched only on explicit tool call (§1-safe).
- Wired the prompt fragment into all routed backend loops (`src/claude_anyteam/loop.py`, `backends/gemini/loop.py`, `backends/kimi/loop.py`) on both task-dispatch AND prose-turn paths so the fragment fires whether the work arrives via task ownership OR inbox prose. Toggleable via `CLAUDE_ANYTEAM_DISABLE_SKILLS_PROMPT_FRAGMENTS=1` for ablation.

### Why both A (MCP tool) and C (prompt fragment) are required

A 5-teammate empirical test (`marketing-skill-test` team) confirmed that **neither A nor C alone is sufficient**:

- **A alone** (C hard-disabled): codex did NOT naturally explore the wrapper's tool surface to discover skills. The teammate produced a generic AI-style cold email with `{{FirstName}}`/`{{Company}}` placeholder template + 15-minute meeting ask — exactly the patterns the cold-email SKILL.md explicitly lists under "What to Avoid." Tool surface alone is invisible to the LLM unless something in the prompt invites discovery.
- **C alone** (theoretical): C's fragment includes the explicit instruction `call mcp_anyteam_invoke_skill('<name>')`. Without A, that call fails — the teammate sees metadata they can't act on.
- **A + C together**: 4/4 hit rate across diverse domain tasks. Each codex teammate received the prompt fragment naming the relevant skill, called `mcp_anyteam_invoke_skill('<name>')` with the right name, fetched the body, and produced output that demonstrably followed the SKILL.md.

This empirical evidence overrides the earlier synthesis recommendation that framed A as "core" and C as "optional UX." The honest architecture is: **A and C are two halves of one cohesive cross-backend skill primitive.** Both ship in v1; neither is droppable. PoC B (capability-manifest entry) was empirically never used by either backend in the live test and remains droppable.

### Empirical evidence (live integration test)

5 codex teammates spawned with bare domain tasks on the A+C wrapper. Each teammate's `mcp_anyteam_invoke_skill` calls (extracted from `~/.claude/teams/marketing-skill-test/events/<agent>.jsonl`):

| Teammate | Task | Skill invoked | Match |
|---|---|---|---|
| codex-marketer-3 | "What marketing ideas should I try?" | `marketing-ideas` | ✓ |
| codex-test-cold | "Write a cold outreach email for…" | `cold-email` | ✓ |
| codex-test-seo | "My SaaS product page isn't ranking on Google…" | `seo` | ✓ |
| codex-test-copy | "I need help writing the hero section copy…" | `copywriting` | ✓ |
| codex-test-a-only (C hard-disabled control) | "Write a cold outreach email for…" | (none — A alone insufficient) | ✗ (control) |

### Notes on the discovery flow

When a routed teammate is dispatched a task or receives a prose message, the wrapper:

1. Calls `discover_skills()` (cached at startup; 66 skills discovered on a host with the typical claude-anyteam + marketing-skills + SEO marketplaces installed).
2. Scores each skill against the task text via token overlap + name-mention boost.
3. If any skill scores ≥ 2, composes a `## Available Claude Code skills` fragment with up to 3 top matches: name, description, when_to_use, source_path, and the explicit `mcp_anyteam_invoke_skill('<name>')` call.
4. Prepends the fragment to the existing peer-prompt-fragments before the routed backend invokes its model.

The teammate's LLM sees the fragment, identifies the right skill, calls the MCP tool, and follows the SKILL.md body. End-to-end discovery + use, no manual instruction needed.

## [0.8.3] — 2026-05-04

### Added

- **`claude-anyteam diagnose --bundle`** — wraps the substrate report in a markdown envelope tuned for GitHub-issue submission. Adds a `## Versions` section (auto-detects `claude-anyteam`, `codex`, `gemini`, `kimi` CLIs via subprocess `--version` probe + Python + OS + WSL marker), a `## Scope` summary line, and a `## Suggested next steps` footer pointing the user at `events/<agent>.jsonl` excerpts and `--instrument-spawn` follow-up. The embedded report sits inside a four-backtick fence (so any inner triple-fence content survives GitHub's nested-fence renderer), and the user's home directory is replaced with `~/` in paths to prevent accidental username leak when posting publicly.
  - Mutually exclusive with `--json` (different output shapes) and incident modes (different scope).
  - The version-probe helper bounds subprocess timeouts at 5s and returns `None` on missing binary, non-zero exit, or unparseable output — so a hung CLI cannot stall the bundle.
  - Regression tests at `tests/test_diagnose_cli.py::test_diagnose_bundle_*`.

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
