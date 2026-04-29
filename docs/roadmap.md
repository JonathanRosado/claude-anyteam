# Roadmap

Shipped/partial: multi-backend routing now supports Codex (`codex-*`), Gemini CLI (`gemini-*`), and Kimi CLI (`kimi-*`). Remaining Gemini work is ACP hardening and closing documented parity gaps in `docs/gemini-adapter-limitations.md`; remaining Kimi work is ACP/turn-steer research beyond the v1 headless adapter.

# Roadmap

## Shipped

**Codex adapter** — OpenAI Codex CLI (0.120+, latest tested 0.124.0) as a first-class Claude Code teammate. gpt-5.5 / gpt-5.4 / gpt-5.4-mini / gpt-5.3-codex / gpt-5.2 with low/medium/high/xhigh reasoning effort. App Server mid-task `turn/steer` and `thread/fork` cross-task memory. Fresh-exec `codex exec resume` as opt-out. 198 passing tests including a live battle-test against native Claude agents.

**Gemini adapter** — Gemini CLI teammates through the `gemini-*` prefix. Shipped with documented limitations: headless `gemini --prompt ... --output-format stream-json` execution is supported, while ACP / mid-turn steering and Codex-style `thread/fork` parity remain tracked in [Gemini adapter limitations](gemini-adapter-limitations.md).

**Kimi adapter** — Kimi CLI teammates through the `kimi-*` prefix. Shipped with headless `kimi --print --output-format stream-json`, adapter-owned HOME isolation, OAuth token copy from `~/.kimi/credentials/kimi-code.json`, bare-name MCP wrapper tools, and the default user-facing model slug `kimi-code/kimi-for-coding` (`Kimi-k2.6`, 262k context). Best for architectural-stretch tasks, large-context review, and Kimi-native skills/swarm workflows.

**Install surfaces** — npm (`npx --yes --package claude-anyteam claude-anyteam-setup`), direct (`uv tool install`), and Claude Code plugin (marketplace install + self-healing SessionStart hook). All three write the same settings and interop.

**TUI parity** — Codex, Gemini, and Kimi teammates appear in Claude Code's Agent Teams presence line exactly like native teammates. Works in tmux and single-terminal modes. Task claiming, idle signaling, and shutdown lifecycle share the same protocol path; Gemini/Kimi peer-message steering has the documented limitations above.

**Plan mode** — opt-in structured plan approval with JSON-schema-validated plan artifacts.

## Coming next

### Post-v0.8.0 follow-ups (canonical tracker)

Consolidated work items surfaced during the v0.8.0 protocol-revision session and the overnight pre-merge verification ladder. Each item is empirically motivated — file paths, run-ids, and failure modes are cited so the rationale survives the next refactor.

**Capability layer**

- **Capability versioning negotiation handshake.** Each capability carries a `capability_version` field, but there's no formal "I support v2, peer supports v1, fall back to common subset" mechanism. Mismatch handling is currently per-backend prose. Needs a versioned contract before a 5th-party backend (`glm-*`, `qwen-*`, `deepseek-*`) lands and starts versioning capabilities independently. (Surfaced in v0.8.0 capability-layer review.)
- **`kind_v1` classifier mapping breadth.** v0.8.0 extended `score_collab` from `prefix_v1` to `kind_v1`. Rescore on S6 lifted `M11a_classification_coverage` from 0.0 → 0.367; the remaining 0.633 are codex envelopes whose `kind` value isn't in the mapping (not in `informational`/`fyi`/`question`/`ask`/`inquiry`/`answer`/`response`/`handoff`/`delegate`). Future enhancement: surface unrecognized `kind` values for triage and broaden the mapping. Doc: `references/external-claude-code-re/proto-rev-execution-log/d1-validation-final.md`.

**Visibility and metrics**

- **"DMs received by live loop" metric.** v0.8.0's `M3_peer_dm_received` reflects DMs *addressed* to an agent name, not DMs *drained by a live recipient loop*. The kimi peer-DM investigation showed this matters: in S8 v2 kimi-pair was credited with 60 received DMs even though it crashed at auth_preflight before its loop could read inbox. Either rename the metric or pair it with a recipient-active signal (e.g. `M3_peer_dm_processed`). Doc: `references/external-claude-code-re/proto-rev-execution-log/kimi-peer-dm-investigation.md`.
- **Scenario validity gate for auth-preflight failures.** When a routed backend emits `visibility_degraded` during `adapter_spawn_auth_preflight` and never registers, the run should mark that agent as backend-unavailable rather than reporting peer-efficiency outcomes for it. v0.8.0 has the auth classifier; this is the consumer-side gate. Same investigation doc.
- **Wrapper read_config doc accuracy.** `read_config().protocol_tools` exposes shadow tools (`mcp_anyteam_shell`, `mcp_anyteam_write_file`, `mcp_anyteam_edit_file`); current docs may call these "non-destructive" but they're not. Update wording to be accurate. Surfaced by the pre-merge review at `references/external-claude-code-re/proto-rev-execution-log/pr27-pre-merge-review.md`.

**Operational / runner**

- **`--require-auth` runner flag.** Before launching long stress runs, validate each backend credential outside the stress window. Today an expired kimi key only surfaces 9 seconds into the run. The flag should fail fast with a clear error before sandbox creation.
- **Auto-retry-with-backoff for quota/auth.** Today the auth classifier correctly identifies `quota_exhausted` (`(?<!\d)429(?!\d)`) and `invalid_authentication` (`(?<!\d)401(?!\d)`), but the runner doesn't auto-retry — agents that hit gemini RESOURCE_EXHAUSTED or kimi 401 simply burn the budget. Add a retry policy parameterized per error class. (Run S3 documented this at d1-validation-final.md "S3 / S4 homogeneous backend runs — external service state".)

**Native Claude backend**

- **Granular turn-lifecycle integration test.** v0.8.0 ships `tests/test_claude_native_backend.py` (33 tests covering config, MCP wiring, spawn cmd shape, loop init, _execute_task, _handle_shutdown, _handle_prose, terminal visibility, feature_test). Still missing: a single integration test that exercises the full happy-path turn lifecycle (claim → invoke → tool_event stream → task_complete) end-to-end with a recorded stream-JSON fixture. Caught by the pre-merge review.
- **Schema-recovery strictness comment vs behavior.** `_embedded_json_object_candidates()` docstring says arbitrary trailing prose still fails, but `_parse_and_validate_final_message()` accepts any embedded valid object including with trailing prose. Decide whether to tighten to preamble-only recovery or update the comment to match. Pre-merge review LOW item.

**Coverage gaps (verification harness)**

- **Long-running stability.** Longest stress run to date: S5+W10 at 89.8 min wall (30/30). 8h+ / multi-day stability behavior is unknown. Plan a 6h+ S5+W10 run with periodic checkpointing.
- **Within-backend model variance.** v0.8.0 verified `gpt-5.5/codex`, `sonnet/claude`, `gemini-2.5-pro`, kimi default. Other models in each backend (gpt-5.4, opus, gemini-3-pro-preview, kimi-thinking) untested.
- **Workload coverage beyond W7 + W10.** Verified: W7 (pair-program-tool) and W10 (rendezvous-coordination). Other workload shapes (long-running tasks, deep dependency chains, large-context handoffs) need stress runs against the v0.8.0 substrate.
- **Multi-team concurrent scenarios.** No cross-team interaction or resource contention testing. Two anyteam runners on the same host today share `/tmp/stress-sandbox-*` cleanup logic; verify isolation under contention.

**Investigation follow-ups**

- **Kimi v1 send_message rate under W7 (post-auth-fix monitoring).** S8 v2 had kimi-pair sent=0; investigation showed 100% explained by auth failure. S8 rerun (post-auth-fix) had kimi-pair sent=23/codex-pair sent=88. Continue monitoring on multi-run averages to confirm the 23/88 ratio is steady-state, not run-of-the-mill. Doc: kimi-peer-dm-investigation.md.

These items are not v0.8.0 ship-blockers — substrate failure metrics tied at zero across all six successful stress scenarios, and 1075-test regression coverage locks the empirical fixes in place. They're the next-iteration improvements the verification ladder surfaced.

### v0.7.0 — backend-neutral progress watchdog and event envelope

The v0.6.0 soft non-progress watchdog ships only for the Codex App Server path (`codex.py:app_server_invoke`). The trip condition uses App Server-specific signals (agentMessage byte deltas + tool_call_event count) that Gemini ACP and Kimi don't emit the same way. v0.7.0 generalizes this into a backend-neutral primitive grounded in the event-envelope design from `bug-triage/B9-visibility-parity-investigation.md` §6. Two concrete asks:

- **Backend-neutral watchdog primitive.** Lift the soft watchdog out of `codex.py` into a `progress_watchdog` helper that consumes a normalized "observable progress" stream from any backend's polling loop. Each adapter (Codex App Server / Codex exec / Gemini ACP / Gemini headless / Kimi headless) emits per-backend progress events into the helper, which decides when to log/steer. This is a step toward the `teammate_activity` mailbox class P1 in B9 §6.
- **Watchdog fires generate diagnostic incidents.** Today the soft watchdog logs and steers but does not call `diagnostics.record_incident`. That's deliberate (frequent-but-benign), but it means a lead querying `claude-anyteam diagnose` doesn't see the watchdog's history. Add a `non_progress_steer` error class with `severity="warn"` so leads can audit "where did this teammate stall this week?" without grepping stderr. Gate behind a config knob if the volume turns out to be noisy in practice.

Both items land alongside the broader visibility-parity work (B9 §6 P1–P5: shared event envelope, append-only event log, `teammate.tool_started`/`tool_completed` mailbox class, structured failure prose). Any adapter added between v0.6.x and v0.7.0 should hook into the existing Codex-only path so the v0.7.0 lift-and-generalize is a uniform refactor rather than a per-backend retrofit.

### Adapters planned on the same architecture

These are the adapters planned on the same architecture. Each one is a Python adapter module + a line in the spawn shim's routing table. Gemini and Kimi have moved out of planned status and are shipping with documented limitations.

| Adapter | Model(s) | Backend CLI | Status |
|---|---|---|---|
| **GLM** | GLM-4.x family | Zhipu's CLI | Planned (after Kimi) |
| **DeepSeek** | DeepSeek V3 / R1 | DeepSeek's CLI or API-direct | Planned (after Kimi) |
| **Generic API adapter** | Any OpenAI-compatible endpoint | Direct HTTP | Planned — covers OpenRouter, LM Studio, local vLLM, etc. |

Kimi was sequenced first deliberately: it's the most architecturally distinct mainstream coding CLI, with its own multi-agent model. The v1 decision is now shipped: one anyteam teammate maps to one root Kimi session, while Kimi's internal skills/swarm remain private implementation details unless a future protocol extension bridges them. GLM / DeepSeek / Qwen become easier after this. See [`docs/internal/kimi-rationale.md`](internal/kimi-rationale.md) for the full reasoning.

## Contributing a new adapter

The shared protocol is implemented once. A new adapter needs:

1. A new module under `src/claude_anyteam/backends/<name>/` implementing:
   - `async def run_task(task, context) -> TaskResult`
   - `async def handle_prose(message, state) -> str`
2. A shim routing rule: `<name>-*` → new adapter binary
3. Adapter-specific install flags (model id, api key / oauth, effort)
4. Regression tests under `tests/backends/<name>/`

Protocol semantics (inbox polling, task claiming, mailbox I/O, lifecycle) are inherited from the shared base. No re-implementing the team protocol.

If you're thinking of adding an adapter, open an issue first — we can scope the minimum viable integration and flag any protocol surface we want to generalize before committing.

## Deferred / out of scope

- **Custom rendering in the Claude Code TUI beyond presence line.** Claude Code's TUI renders agent types uniformly; we don't fight that.
- **Multi-team coordination.** One adapter instance serves one team. Multi-team overlays belong in a higher-level orchestration tool, not here.
- **LLM wrapping.** We don't wrap external models inside a Claude instance. That's the anti-pattern this project exists to avoid.
- **Kimi ACP / live steering in v1.** Kimi ACP exists but is deferred until stdout flushing and method semantics are proven. Kimi v1 uses headless print mode and next-prompt steer only.

## Longer term

- Telemetry (opt-in) to understand which adapters are popular
- Shared session memory across a team (currently each teammate has its own thread lineage)
- "Hot-swap model" mid-team for A/B comparisons on the same task list
