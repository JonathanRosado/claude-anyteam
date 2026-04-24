# Gemini repo-integration parity audit

Date: 2026-04-24  
Reviewer: codex-gemini-runtime-reviewer  
Scope: parity-map completeness and repo-integration implementation audit against `docs/internal/gemini-integration/codex-parity-map.md`. This is a supplemental audit for task #5; task ownership remains with `codex-parity-reviewer`.

## Summary verdict

Researcher B's parity map is complete enough to use as the implementation/review checklist. It inventories all major Codex integration surfaces that need Gemini treatment: runtime invocation, App Server non-parity, loop/session state, config/env, prompt/tool naming, MCP wrapper injection, registration metadata, TUI presence via spawn path, spawn-shim routing, installer/npm/package entrypoints, protocol field naming, per-agent config, branding, schemas, hook/plugin orientation, docs, and tests.

The implementation covers the core repo integration path: it adds `gemini-anyteam`/`claude-anyteam-gemini` entrypoints, a Gemini backend package, `gemini-*` spawn-shim routing, Gemini binary env/install-state wiring, Gemini registration metadata, shared schema wording cleanup, hook/plugin/help-skill updates, and focused regression tests. However, several parity checklist items are only partially completed or are documented inconsistently.

## What is implemented against the parity checklist

- **Backend/package entrypoints:** `pyproject.toml` adds `gemini-anyteam` and `claude-anyteam-gemini`; `bin/gemini-anyteam` and `bin/claude-anyteam-gemini` wrappers exist.
- **Gemini runtime module:** `src/claude_anyteam/backends/gemini/` contains CLI/config/invoke/loop/prompts modules, keeping Gemini out of the Codex adapter path.
- **MCP wrapper reuse and alias:** `invoke.write_mcp_settings()` writes an adapter-owned `.gemini/settings.json` with `mcpServers.anyteam`, wrapper args `--team/--name`, `trust: true`, and env identity restoration.
- **Registration metadata:** `registration.BackendMetadata` allows Gemini-specific model/prompt, and Gemini loop registers `model="gemini-cli"` with Gemini wording.
- **Spawn routing/TUI path:** `spawn_shim.py` adds `DEFAULT_GEMINI_MATCH = r"^gemini-"`, `CLAUDE_ANYTEAM_GEMINI_BINARY`, route label `gemini`, and dispatches through the same leader-spawn shim/pane path while omitting Codex effort forwarding for Gemini.
- **Installer/package wiring:** installer writes `CLAUDE_ANYTEAM_GEMINI_BINARY`, records Gemini CLI check state, prints Gemini missing warning text, and removes managed Gemini binary env on uninstall. npm setup copy mentions Gemini in several places.
- **Schemas/prompts:** shared schema titles are backend-neutral; Gemini prompts say “Gemini CLI teammate” and use `mcp_anyteam_*` tool names.
- **Hook/plugin/help-skill:** SessionStart orientation and plugin manifests mention both `codex-*` and `gemini-*`; help skill documents both prefixes and the Gemini no-`turn/steer` caveat.
- **Tests:** targeted tests cover Gemini invocation parsing/settings, Gemini registration metadata, spawn-shim Gemini routing/model-not-effort behavior, plugin/skill text, and installer env/state changes.

## Silently skipped or partially completed parity items

### 1. User-facing docs still contradict Gemini shipped status

Several docs were prepended with correct Gemini text but left old Codex-only sections intact. This creates conflicting guidance:

- `README.md:13` says “Codex today. Gemini, Kimi, GLM, DeepSeek next”.
- `README.md:51-59` quickstart only tells users to create `codex-*` teammates and only says Codex-prefixed names appear in TUI.
- `README.md:98-109` still lists “Gemini CLI adapter” under “Coming next” and says pluggable backend routing is coming next.
- `README.md:111-117` requirements list Codex CLI but not Gemini CLI for Gemini users.
- `docs/roadmap.md:3` says Gemini routing is shipped/partial, but `docs/roadmap.md:17-27` still lists Gemini as “Planned”.
- `docs/architecture.md:17-49` diagram and `docs/architecture.md:72-82` per-task flow are still Codex/App-Server-only; `docs/architecture.md:65-67` says the shim matches only `^codex-`.
- `docs/install.md:29-43` says installer writes two env vars and routes only `codex-*`; it omits `CLAUDE_ANYTEAM_GEMINI_BINARY` and `gemini-*`.
- `docs/install.md:47-53` uninstall docs omit removal of `CLAUDE_ANYTEAM_GEMINI_BINARY`.
- `docs/configuration.md:11-25`, `49-65`, `84-112`, and `144-168` remain Codex CLI/App Server oriented and omit Gemini CLI flags/envs in the main tables.

This is the largest repo-integration parity gap: code behavior and docs disagree, which is exactly the kind of silent parity skip the checklist warned about.

### 2. `docs/gemini-adapter-limitations.md` is honest but incomplete

The limitations doc correctly states Plan A headless CLI, no Codex app-server equivalent, no `turn/steer`, prompt+Python schema validation, built-in `tool_result` payload differences, legacy `codex_exit_code`, and auth/config isolation tradeoffs.

Missing or under-specified gaps that should be added:

- **No Codex `thread/fork` parity:** Gemini uses CLI `--resume` when a session id is captured; it does not have Codex App Server `thread/fork` semantics.
- **No true mid-task inbox processing:** while Gemini is inside a blocking headless subprocess, the loop cannot read/proactively inject inbox messages until the subprocess returns. This is related to, but more operationally specific than, no `turn/steer`.
- **No Gemini reasoning-effort parity:** spawn shim deliberately drops `effort` for Gemini and Gemini CLI has no effort flag in this adapter.
- **Feature/probe limitations:** installer Gemini check is presence/version-only; runtime `feature_test()` checks headless flags but not auth, wrapper MCP viability, or currently `--approval-mode` even though invocation uses it.
- **Auth status needs stronger wording:** the current doc says links/copies existing auth cache files and prefers env auth; the runtime review showed OAuth can fail with exit 41 if the isolated settings do not preserve auth method selection. If the auth fix has not landed, this is a blocker; if it has, the limitations doc should describe the actual merged strategy.

### 3. Installer Gemini CLI check is weaker than the parity checklist target

The parity map asked for Gemini version/flag capability guidance analogous to Codex once Researcher A identified flags. Current installer `_check_gemini_cli()` only runs `gemini --version` and parses with the Codex semver parser (`installer.py:587-601`); `_gemini_cli_warning()` only warns when missing (`installer.py:604-614`). It does not check the required flags (`--prompt`, `--output-format`, `--resume`, `--approval-mode`) or auth readiness. Runtime `feature_test()` covers some flags later, but installer parity remains weaker than the checklist and docs imply a broad Gemini CLI availability check.

### 4. Per-agent config comments/docs remain Codex-biased

The code correctly forwards only `model` to Gemini and drops `effort` (`spawn_shim.py:241-251`, `283-287`), but `_load_agent_config()` still says fallback is `~/.codex/config.toml` (`spawn_shim.py:117-120`) and `docs/configuration.md:67-93` only describes Codex behavior. This is a small docs/comment parity gap that can confuse Gemini users.

### 5. Registration comments still say Codex where the code is now backend-neutral

`registration.py:131-134` comments say the metadata shape lets the harness treat “Codex teammates” as visible team members. The data path is generalized via `BackendMetadata`, so this is only a comment/maintainer-doc gap, not a behavior bug.

### 6. Test parity is targeted, not checklist-complete

The focused Gemini tests are useful, and this subset passed locally with `PYTHONPATH=src`:

```text
PYTHONPATH=src pytest -q tests/test_gemini_invoke.py tests/test_gemini_registration.py tests/test_spawn_shim.py tests/test_install_command.py tests/test_plugin_bundle.py tests/test_skills.py
80 passed, 1 warning
```

Remaining checklist-level gaps:

- no Gemini loop unit tests for task execution, prose reply, plan approval retry, missing plan target behavior, and resume propagation;
- no installer tests for Gemini flag capability/auth warnings;
- no tests for missing terminal `result`, late/missing `init`, or interleaved tool results beyond the single current parser fixture;
- no docs tests that catch the stale README/architecture/roadmap/install/configuration contradictions listed above.

## Conclusion

The repo integration is structurally in place, but it is not yet clean parity documentation. The highest-priority follow-up is to reconcile user-facing docs and expand `docs/gemini-adapter-limitations.md` so users can distinguish: (1) shipped Gemini `gemini-*` headless support, (2) explicit non-parity with Codex App Server/`thread-fork`/mid-turn steering, and (3) operational caveats around auth, effort, installer probes, and resume semantics.
