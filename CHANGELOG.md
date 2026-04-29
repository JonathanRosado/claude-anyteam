# Changelog

All notable changes to claude-anyteam are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [0.7.0] — 2026-04-29

The protocol-revision drop. Substrate hardening across the three north stars (`CLAUDE.md` §1 harness preservation, §2 visibility parity, §3 peer efficiency), measured against the S6/S7/S8/S5 cross-backend stress harness and validated head-to-head against a native-Claude pair (S6n).

Headline numbers (post-fix, integration HEAD `bd1818e`):

- **5 cross-backend stress scenarios verified**: S6 codex+codex 15/15, S6n claude+claude 15/15, S7 gemini+codex 15/15, S8 kimi+codex 15/15, S5+W10 4-backend 30/30.
- **M5 turn-failure rate**: 0.000 across all scenarios.
- **M13 collisions**: 0 across all scenarios.
- **s1_flatten_violations / harness_preservation_violations**: 0 across all scenarios.
- **Test suite**: 803 → **1055 passed**.
- **Native-Claude head-to-head (S6n vs S6)**: substrate-comparable. Wall clock 1149s vs 1404s (native 18% faster); M11a p50 73s vs 36.5s (native 2x slower per-DM, model-driven). Substrate failure metrics tied at zero.

### Added

- **§1 capability layer**: typed capability declarations + hook registry (`src/claude_anyteam/capabilities.py`, +982 LOC) and capability-manifest cache (`capability_manifest.py`, +631 LOC) with peer-prompt-fragment composition, eager prewarm, and bounded supervisor.
  - Capability vocabulary: `turn_steer`, `thread_fork`, `permission_bridge`, `live_tool_events`, `large_context`, `native_swarm`, `headless_invocation`, `session_resume`, `plan_mode`, `trust_modes`, `coupling_intent`.
  - Manifest-gated peer-steer enforcement (recipient interpretation, not sender structure).
- **§2 visibility surface**: `visibility-tail` filesystem CLI with JSON/filter/since/color/multi-line tri-card and WebSocket `--serve` mode; `headless_visibility.py` backend-agnostic event normalizer (+392 LOC); `wrapper_mcp_diagnostics.py` instrumented tool-discovery; `checkpoint_commit` MCP tool for app-server-turn-timeout work salvage; `claude-anyteam diagnose` skill + 902-line read-only inspector CLI; uniform `recipient`/`to` field stamping on all `send_message` tool_events.
- **§3 peer efficiency**: WatchInbox `fs.watch` event-driven inbox (`src/claude_anyteam/watch_inbox.py`); BatchedSender 50ms debounce; attachment protocol (4096-char auto-spill); typed lifecycle payloads; L4 `messageKind` discriminator across codex/gemini/kimi; SendMessage flap repair (#51).
- **`claude_native` backend**: 992-line bridge at `src/claude_anyteam/backends/claude_native/` (cli, config, invoke, loop, prompts) + 402-line test suite. Wraps `claude --print --output-format stream-json --verbose --mcp-config <wrapper>` so native Claude becomes a peer of codex/gemini/kimi, with full Claude Code tool surface preserved end-to-end.
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

[0.7.0]: https://github.com/JonathanRosado/claude-anyteam/pull/27
