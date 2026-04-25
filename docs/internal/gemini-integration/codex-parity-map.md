# Codex parity map for Gemini integration

Researcher B scope: inventory every Codex touchpoint in this repo and turn it into an implementable Gemini parity checklist. This is repo-integration work only; Gemini CLI runtime details are owned by Researcher A. Where Researcher A's final `docs/internal/gemini-integration/gemini-runtime.md` is not present yet, this map cross-references the existing approved feasibility docs and marks the dependency explicitly.

## Implementation stance

- [ ] Prefer an additive Gemini backend over mutating the Codex path in place. Existing docs already recommend Plan A: add `src/claude_anyteam/backends/gemini/...` and a `claude-anyteam-gemini`/`gemini-anyteam` entrypoint rather than threading Gemini branches through the Codex loop (`docs/gemini-adapter-feasibility.md:49-56`, `75-114`).
- [ ] Keep shared protocol surfaces shared: `protocol_io.py`, `messages.py`, `wrapper_server.py`, schemas, task files, inbox files, and the install shim can remain backend-neutral with small routing/config extensions.
- [ ] Keep Codex and Gemini runtime assumptions separate: Codex has `codex exec --json --output-schema`, `codex exec resume`, and `codex app-server`; Gemini Plan A depends on `gemini -p ... --output-format stream-json`, prompt-side schema validation, isolated Gemini home/config, and `--resume` if available (`docs/internal/gemini-plans.md:5-61`).

## Checklist

### 1. Codex adapter module: subprocess invocation, MCP injection, event parsing, result type

- [ ] **Where:** `src/claude_anyteam/codex.py:1-874`.
- [ ] **What Codex does:** Provides the core Codex runtime adapter. `CodexResult` (`52-65`) is the loop-facing result contract. Tool-call classification matches Codex JSONL/App Server event shapes and bare wrapper tool names (`68-151`). `wrapper_mcp_config_args()` injects the wrapper MCP server through Codex inline `-c mcp_servers.*` overrides (`154-190`). `feature_test()` probes `codex --version`, `codex exec --help`, and the wrapper import probe (`193-248`). `_probe_wrapper_mcp()` hardcodes `python -c ...` for the wrapper import probe (`251-307`). `run()` builds `codex exec` or `codex exec resume` argv, uses `--json`, `--output-last-message`, optional `--output-schema`, `-C`, `-c model=...`, `-c model_reasoning_effort=...`, `--dangerously-bypass-approvals-and-sandbox`, `stdin=subprocess.DEVNULL`, parses JSONL and the last-message file, captures `thread.started.thread_id`, and returns `CodexResult` (`310-539`). `app_server_invoke()` drives `codex app-server`, `thread/start`, `thread/fork`, `turn/start`, `turn/steer`, wrapper config, schema parsing, and thread-id capture (`542-843`). `SteerQueue` is Codex App Server mid-turn steering plumbing (`846-874`).
- [ ] **Gemini equivalent:** Add a Gemini runtime module instead of expanding this file. Recommended path: `src/claude_anyteam/backends/gemini/invoke_exec.py` (or a top-level `gemini_headless.py` if implementer chooses the older plan naming). It should return the existing `CodexResult` shape or a renamed shared `BackendResult` alias. Implement `feature_test()` for `gemini` binary presence and required headless flags; implement isolated Gemini settings/home creation; implement wrapper MCP config as Gemini `.gemini/settings.json` `mcpServers` rather than Codex `-c`; implement `run_exec()` for `gemini -p <prompt> --output-format stream-json`, parse Gemini stream events, capture final text, count Gemini wrapper tool calls, capture session id when exposed, handle timeout/nonzero/errors, and never mutate the user's real Gemini config.
- [ ] **Hard Gemini dependency:** Researcher A must confirm exact Gemini CLI headless argv, event shapes, session id field, `--resume` semantics, MCP settings schema, and whether `HOME`/config isolation is sufficient. Existing guidance: `docs/gemini-adapter-feasibility.md:43-67`, `104-114`; `docs/internal/gemini-plans.md:28-61`.

### 2. App Server JSON-RPC client is Codex-specific

- [ ] **Where:** `src/claude_anyteam/app_server.py:1-510`.
- [ ] **What Codex does:** Owns a `codex app-server` subprocess (`76-89`), JSON-RPC request/response and notification dispatch (`138-307`), and Codex-specific helper methods: `initialize`, `thread/start`, `turn/start`, `turn/steer`, `turn/interrupt`, `thread/fork`, `thread/read`, and materialization checks (`310-510`).
- [ ] **Gemini equivalent:** Do not reuse for Plan A. If Researcher A confirms Gemini ACP is needed later, add a separate Gemini ACP client rather than overloading this Codex App Server client. For initial parity, Gemini will not have Codex's `turn/steer` equivalent unless Researcher A identifies one; document this as a limitation if absent.
- [ ] **Hard Gemini dependency:** Mid-task steering/streaming parity depends on a Gemini capability equivalent to `turn/steer` or ACP. Existing Plan A explicitly does not reuse `app_server.py` (`docs/internal/gemini-plans.md:9-12`).

### 3. Control loop hard-calls Codex and stores Codex session state

- [ ] **Where:** `src/claude_anyteam/loop.py:1-854`.
- [ ] **What Codex does:** Main teammate control loop imports `claude_anyteam.codex as codex_mod` (`31`), calls Codex `feature_test()` before registration (`65-73`), tracks `codex_session_id` and `app_server_last_thread_id` in `LoopState` (`43-63`), handles prose by invoking Codex (`179-255`), generates plans with `codex.run(... PLAN_SCHEMA ...)` (`417-454`), claims tasks with active form `Running codex on task #...` (`457-500`), executes tasks and sends `codex_exit_code` in completion messages (`532-607`), dispatches among App Server, `codex exec resume`, and fresh `codex exec` (`609-743`), and implements App Server mid-turn inbox steering (`745-806`).
- [ ] **Gemini equivalent:** Do not wedge Gemini into this file for v1 unless deliberately refactoring to a backend abstraction. Safer parity path is a new `src/claude_anyteam/backends/gemini/loop.py` copied from this loop with these substitutions: Gemini `feature_test`; `gemini_session_id` only; no App Server branch unless Researcher A provides ACP/steer support; Gemini-specific task/prose/plan prompts; prompt-embedded schemas plus Python validation/retry for all structured outputs; active form `Running gemini on task #...`; completion still uses existing protocol message field for now or generalize `codex_exit_code` in messages/tests later.
- [ ] **Hard Gemini dependency:** Resume branch requires Gemini session id capture and reliable `--resume`; mid-turn prose steering requires an ACP/interrupt capability. Existing approved feasibility says separate Gemini loop is safer (`docs/gemini-adapter-feasibility.md:49-50`).

### 4. Runtime configuration is Codex-named but partly backend-neutral

- [ ] **Where:** `src/claude_anyteam/config.py:37-137`; `src/claude_anyteam/env.py:12-64`; `src/claude_anyteam/cli.py:27-85`, `251-294`.
- [ ] **What Codex does:** `Settings` includes `codex_binary`, `app_server`, optional `model` and `effort` (`37-65`). `from_env()` reads generic `CLAUDE_ANYTEAM_*` vars plus legacy `CODEX_TEAMMATE_*`, and `CODEX_BINARY` for the Codex binary (`68-137`). CLI exposes `--codex-binary`, `--app-server/--no-app-server`, `--model`, `--effort`, and Codex wording in help text (`27-85`, `251-294`). `env.py` still preserves legacy Codex-prefixed env names (`12-64`).
- [ ] **Gemini equivalent:** Add a Gemini settings dataclass/module rather than adding all Gemini knobs to `Settings`. Reuse shared vars for team/name/cwd/poll/color/plan/model. Add Gemini-specific `CLAUDE_ANYTEAM_GEMINI_BINARY` and CLI `--gemini-binary`; add transport only if needed. Do not reuse `CODEX_BINARY`. Decide whether Gemini has an effort analog; if not, ignore/reject `--effort` in Gemini CLI or document unsupported.
- [ ] **Hard Gemini dependency:** Researcher A must define Gemini model/effort flag names and supported auth envs. Existing feasibility says reuse `CLAUDE_ANYTEAM_MODEL` and add only Gemini-specific binary/transport vars (`docs/gemini-adapter-feasibility.md:58-59`).

### 5. Prompts mention Codex and bare MCP tool names

- [ ] **Where:** `src/claude_anyteam/prompts.py` (entire module; referenced from `loop.py:199-204`, `424-429`, `631-633`, `725-727`); `src/claude_anyteam/schema_validation.py`.
- [ ] **What Codex does:** Generates task, prose, and plan prompts tailored to a Codex teammate and assumes wrapper tools are callable by their bare names (`send_message`, `task_update`, etc.) because Codex's MCP surface exposes them that way. `schema_validation.py` is also prompt/schema parity-critical: `inline_schema_prompt_fragment()` is the reusable prompt fragment for schema-constrained outputs, and the module docstrings/instructions contain Codex-specific framing that Gemini should not inherit blindly.
- [ ] **Gemini equivalent:** Add `src/claude_anyteam/backends/gemini/prompts.py`. Preserve task/prose/plan semantics but use Gemini's MCP tool names (expected `mcp_<server>_<tool>`, e.g. `mcp_anyteam_send_message` / `mcp_anyteam_task_update`, pending Researcher A confirmation), and always embed output schemas using `schema_validation.inline_schema_prompt_fragment()` because Gemini lacks Codex `--output-schema` parity. Audit `schema_validation.py` labels/docstrings while doing this: either make them backend-neutral or add Gemini-specific wording so retries/errors do not instruct the model as a Codex teammate.
- [ ] **Hard Gemini dependency:** Exact Gemini MCP tool name normalization and any schema/tool-call prompt quirks. Existing feasibility calls this out as critical (`docs/gemini-adapter-feasibility.md:28`, `55-56`).

### 6. MCP wrapper server is shared, but identity/config injection is Codex-shaped at call sites

- [ ] **Where:** `src/claude_anyteam/wrapper_server.py:1-334`; Codex call sites `src/claude_anyteam/codex.py:154-190`, `675-692`; probe `251-307`.
- [ ] **What Codex does:** Wrapper server exposes six team-protocol tools (`send_message`, `task_update`, `task_create`, `read_inbox`, `task_list`, `read_config`) to the model. Codex exec injects it via inline `mcp_servers.claude_anyteam_wrapper.command/args=[]` and identity env. Codex App Server injects command plus `args: ["--team", ..., "--name", ...]` because App Server did not forward env to the wrapper (`codex.py:675-692`).
- [ ] **Gemini equivalent:** Reuse `wrapper_server.py`; do not duplicate tools. Gemini config writer must create an adapter-owned `.gemini/settings.json` with an MCP server alias that is safe for Gemini, wrapper command resolved to an absolute path, `args: ["--team", team, "--name", agent]`, and `env` restoring the real `HOME` if Gemini runs with an isolated `HOME`. Prefer a short alias like `anyteam` to avoid Gemini underscore/dash normalization issues; prompts must use the resulting normalized tool names.
- [ ] **Hard Gemini dependency:** Gemini MCP settings schema, server alias normalization, required trust flag, and child process env behavior. Existing feasibility: do not patch real `.gemini/settings.json`; restore real `HOME` for wrapper (`docs/gemini-adapter-feasibility.md:43-48`).

### 7. MCP probe hardcodes `python`

- [ ] **Where:** `src/claude_anyteam/codex.py:251-307`, especially `282-288`.
- [ ] **What Codex does:** Startup wrapper probe runs `python -c 'from claude_anyteam.wrapper_server import build_server; build_server(); print("OK")'` with adapter identity env. This is a known Python-shim touchpoint and can fail if `python` does not resolve to the installed environment.
- [ ] **Gemini equivalent:** Do not copy this hardcoded `python` probe into Gemini. Use `sys.executable` if probing Python importability, or better probe the resolved `claude-anyteam-wrapper` binary directly. Add/update tests so both Codex and Gemini probes avoid interpreter ambiguity if implementer decides to fix Codex too.
- [ ] **Hard Gemini dependency:** None; repo-side correctness.

### 8. Registration hardcodes Codex member metadata

- [ ] **Where:** `src/claude_anyteam/registration.py:84-155`; deregistration `165-215`.
- [ ] **What Codex does:** Self-registers in `~/.claude/teams/{team}/config.json`, creates an inbox, and writes a member entry with `agentType: "claude-anyteam"`, `model: "codex-cli"`, prompt saying work is delegated to `codex exec`, `backendType: "in-process"`, `tmuxPaneId: "in-process"`, cwd and plan mode (`113-134`). Deregistration removes the member and inbox (`165-215`).
- [ ] **Gemini equivalent:** Generalize `register()` to accept backend-specific metadata from settings (model label, prompt label, maybe agentType), or add a `register_backend(settings, metadata)` wrapper. Gemini member metadata should say `model: "gemini-cli"` (or exact Researcher A model label) and prompt should mention Gemini CLI/headless mode, not Codex. Preserve inbox creation, locks, atomic writes, deregistration, cwd, color, planModeRequired, and presence-compatible fields.
- [ ] **Hard Gemini dependency:** Exact Gemini display model label only; otherwise repo-side. Existing feasibility explicitly requires registration metadata generalization (`docs/gemini-adapter-feasibility.md:52-53`).

### 9. TUI presence depends on Claude Code spawn path, not config.json

- [ ] **Where:** `docs/architecture.md:57-72`; `src/claude_anyteam/installer.py:1-7`, `38-40`, `879-886`; `src/claude_anyteam/spawn_shim.py:229-270`; internal findings `docs/internal/2026-prototype/research.md:26-32`, `70-87`, `178-187`; `docs/internal/appstate-hook-findings.md:240-251`.
- [ ] **What Codex does:** Installer sets `teammateMode="tmux"` so Claude Code uses the pane backend. Spawn shim is called by Claude Code's teammate spawn flow, and that leader-side spawn path populates in-memory `AppState.tasks`; self-registering in `config.json` alone is insufficient for TUI presence. Registration metadata only aligns the on-disk member shape.
- [ ] **Gemini equivalent:** Ensure `gemini-*` names route through the same spawn shim and pane backend, not a detached process. No separate config.json-only registration strategy will achieve TUI presence. Update docs/tests to state Gemini presence has same requirement.
- [ ] **Hard Gemini dependency:** None; this is Claude Code/spawn-shim integration. Gemini process must be runnable as the shim child.

### 10. Spawn shim routes only `^codex-` names to the adapter

- [ ] **Where:** `src/claude_anyteam/spawn_shim.py:25-27`, `96-149`, `190-199`, `229-270`; tests in `tests/test_spawn_shim.py`; docs/skill tests `tests/test_skills.py:53-62`, `tests/test_plugin_bundle.py:44-57`.
- [ ] **What Codex does:** Default regex is `^codex-` (`25`). If Claude Code passes `--agent-name` and the name matches, shim resolves `claude-anyteam` via `CLAUDE_ANYTEAM_BINARY` / legacy `CODEX_TEAMMATE_BINARY`, forwards `--name`, `--team`, `--plan-mode`, per-agent `model`/`effort` from `~/.claude/teams/{team}/agents/{agent}.json`, logs route `codex`, and `execv`s the adapter (`248-264`). Nonmatching names fall back to native `claude` (`267-270`).
- [ ] **Gemini equivalent:** Extend routing to support `^gemini-` without breaking `^codex-`. Recommended: add `DEFAULT_CODEX_MATCH = r"^codex-"`, `DEFAULT_GEMINI_MATCH = r"^gemini-"`, `CLAUDE_ANYTEAM_GEMINI_BINARY`, and route label `gemini`. Resolve Gemini adapter binary (`gemini-anyteam` or `claude-anyteam-gemini`) and forward shared flags plus model; only forward effort if Gemini supports it. Preserve native fallback and startup probe behavior. Add tests for codex route, gemini route, conflict/order, env override, and malformed regex.
- [ ] **Hard Gemini dependency:** Adapter binary name and supported flags. Existing Plan A suggests `gemini-anyteam --backend gemini-headless --team <team> --name <agent>` and `CLAUDE_ANYTEAM_GEMINI_BINARY` (`docs/internal/gemini-plans.md:70-75`).

### 11. Installer writes a single shim and checks Codex CLI prereq

- [ ] **Where:** `src/claude_anyteam/installer.py:25-40`, `448-560`, `794-904`, `1060-1092`; CLI install wrapper `src/claude_anyteam/cli.py:92-123`, `175-200`, `225-240`; npm bootstrap `npm/README.md:3-23`, `npm/lib/detect.js:10-26`, `278`, `npm/bin/setup.js`; release workflow `.github/workflows/release.yml`.
- [ ] **What Codex does:** Install writes `CLAUDE_CODE_TEAMMATE_COMMAND=<claude-anyteam-spawn-shim>` and `CLAUDE_ANYTEAM_BINARY=<claude-anyteam>` into `~/.claude/settings.json`, sets `teammateMode="tmux"` in `~/.claude.json`, removes managed legacy Codex binary env, records state, and performs an informational nonblocking Codex CLI check for `codex` >= 0.120.0 with install/sign-in hints (`448-560`, `794-904`, `1060-1092`). npm package delegates install to Python and documents Codex CLI warning. `npm/bin/setup.js` is part of the bootstrap/install surface too: it contains Codex prerequisite text, a `codex-alice` launch template, and Codex-prefixed success copy.
- [ ] **Gemini equivalent:** The same installed shim can route both backends. Installer must additionally discover/write the Gemini adapter binary env if the spawn shim needs one, or rely on PATH. Add an informational Gemini CLI check analogous to `CodexCliCheck`: binary `gemini`, min version/flag capability from Researcher A, warning text with install/auth hints, and install state keys (`gemini_cli_found`, `gemini_cli_version`). Do not make Gemini missing block Codex users unless install mode explicitly requests Gemini-only. Update npm detect/bootstrap docs and `npm/bin/setup.js` copy/examples if new binary env is required; success/next-step text should mention both `codex-*` and `gemini-*` where the installed package supports both.
- [ ] **Hard Gemini dependency:** Gemini CLI package/install command, version floor, version output parse shape, first-run auth instructions, and required flags.

### 12. Package entry points and legacy Codex script names

- [ ] **Where:** `pyproject.toml:1-46`; `bin/claude-anyteam`, `bin/claude-anyteam-spawn-shim`, `bin/claude-anyteam-wrapper`; legacy bin names `bin/codex-teammate*`; npm detect references `npm/lib/detect.js:10-26`.
- [ ] **What Codex does:** Python package publishes `claude-anyteam`, `claude-anyteam-spawn-shim`, `claude-anyteam-wrapper`, and legacy `codex-teammate`, `codex-teammate-spawn-shim`, `codex-teammate-wrapper` scripts (`pyproject.toml:35-39`). Description still markets Codex-powered teammates (`pyproject.toml:8`).
- [ ] **Gemini equivalent:** Add a Gemini adapter console script (name to align with spawn shim, e.g. `gemini-anyteam = "claude_anyteam.backends.gemini.cli:main"` or `claude-anyteam-gemini = ...`). Keep wrapper server shared. Update package description to mention Gemini/multi-backend. Add bin wrappers only if this repo's bin directory is part of plugin packaging for all scripts.
- [ ] **Hard Gemini dependency:** Chosen CLI module and binary name.

### 13. Protocol completion message still names `codex_exit_code`

- [ ] **Where:** `src/claude_anyteam/messages.py` and `src/claude_anyteam/protocol_io.py` (message construction); Codex caller `src/claude_anyteam/loop.py:593-601`; test `tests/test_messages.py:111-117`.
- [ ] **What Codex does:** Task completion messages include `codex_exit_code` for adapter evidence/reporting.
- [ ] **Gemini equivalent:** Either preserve `codex_exit_code` for backwards compatibility and pass Gemini's process exit code there (least disruptive but semantically ugly), or add a backend-neutral field such as `backend_exit_code` while keeping `codex_exit_code` optional for compatibility. Implementer should inspect consumers before renaming.
- [ ] **Hard Gemini dependency:** None.

### 14. Per-agent config currently forwards only model/effort to Codex adapter

- [ ] **Where:** `src/claude_anyteam/spawn_shim.py:96-149`, `258-262`; docs `docs/configuration.md:19-92`.
- [ ] **What Codex does:** Reads `~/.claude/teams/{team}/agents/{agent}.json`, whitelists `model` and `effort`, and forwards to the Codex adapter. Missing/bad config falls back to Codex defaults, often `~/.codex/config.toml` (`110-149`).
- [ ] **Gemini equivalent:** Reuse `model`; decide whether to whitelist Gemini-specific keys (e.g. auth/profile/transport) only if needed. Do not mention `~/.codex/config.toml` in Gemini fallback; Gemini fallback is adapter-owned `.gemini/settings.json` plus Gemini CLI defaults.
- [ ] **Hard Gemini dependency:** Gemini model flag and any auth/profile settings.

### 15. Settings/config files specific to Codex and legacy names

- [ ] **Where:** `src/claude_anyteam/env.py:12-64`; `src/claude_anyteam/config.py:101`; `src/claude_anyteam/codex.py:164-167`, `340-350`, `706`; `src/claude_anyteam/logger.py`; docs `docs/configuration.md:13-92`, `156-174`.
- [ ] **What Codex does:** Honors `CODEX_BINARY`; legacy `CODEX_TEAMMATE_*`; optionally dumps Codex events; relies on user `~/.codex/config.toml` for unset model/effort; writes no Codex MCP config globally; Codex sessions persist under `~/.codex/sessions/` in App Server mode. `logger.py` still carries legacy environment-variable wording and should be treated as part of the config/naming surface, not just an implementation detail.
- [ ] **Gemini equivalent:** Add Gemini-specific env names; do not use legacy Codex env names for Gemini. Use adapter-owned Gemini home/config/session root. Update docs to distinguish Codex defaults from Gemini defaults and to describe where Gemini state lives. Make logger/env help text backend-neutral or add Gemini-specific wording where logs are configured through Gemini adapter entrypoints.
- [ ] **Hard Gemini dependency:** Gemini config/session paths and envs.

### 16. Package-level branding still assumes Codex

- [ ] **Where:** `src/claude_anyteam/__init__.py`; package metadata in `pyproject.toml:1-46`.
- [ ] **What Codex does:** Package-level names/descriptions still brand the project as Codex-powered even though many internals are now backend-neutral.
- [ ] **Gemini equivalent:** Update package-level branding to describe Agent Teams / multi-backend routing rather than Codex-only behavior. Preserve backwards-compatible package/module names; do not rename `claude_anyteam` just for Gemini.
- [ ] **Hard Gemini dependency:** None.

### 17. Dev/probe helpers are Codex-only unless explicitly expanded

- [ ] **Where:** `src/claude_anyteam/plan_probe.py`; `src/claude_anyteam/shutdown_probe.py`; `src/claude_anyteam/roundtrip_m1.py`.
- [ ] **What Codex does:** These are developer/probe helpers for exercising Codex teammate flows and shutdown/roundtrip behavior outside the main adapter path.
- [ ] **Gemini equivalent:** Treat these as non-goals for initial Gemini runtime parity unless the implementer wants equivalent Gemini smoke probes. If retained as Codex-only helpers, label them clearly so they are not mistaken for shared backend coverage. If expanded, add Gemini-specific helper names/flags rather than overloading Codex defaults.
- [ ] **Hard Gemini dependency:** Only needed if Gemini smoke probes are added; otherwise none.

### 18. Shared JSON schemas have Codex teammate labels

- [ ] **Where:** `schemas/plan.schema.json`; `schemas/task-complete.schema.json`.
- [ ] **What Codex does:** The schema shapes are shared protocol contracts, but titles/descriptions include "Codex teammate ..." labels that are copied into prompt fragments and validation diagnostics.
- [ ] **Gemini equivalent:** Reuse the schema shapes unless Gemini needs different structured output fields, but make labels backend-neutral or provide backend-specific title/description overrides before embedding them in Gemini prompts. Avoid showing "Codex teammate" in Gemini schema instructions, retry prompts, or validation errors.
- [ ] **Hard Gemini dependency:** None unless Researcher A finds Gemini requires schema-shape changes.

### 19. Hook/plugin orientation text teaches only Codex routing

- [ ] **Where:** `hooks/session-start.sh:5-74`; `.claude-plugin/marketplace.json:14-21`; `.claude-plugin/plugin.json:1-14`; tests `tests/test_plugin_bundle.py:17-20`, `44-57`.
- [ ] **What Codex does:** Session-start hook prints: `claude-anyteam is installed; Agent Teams teammates named codex-* route to Codex...` (`hooks/session-start.sh:7`). Marketplace description says it routes `codex-*` teammates (`.claude-plugin/marketplace.json:14`). Tests assert that exact Codex orientation string.
- [ ] **Gemini equivalent:** Update hook and manifests to mention both `codex-*` and `gemini-*` routing. Update tests to accept the new text. Keep hook's settings detection logic unchanged unless new env keys require validation.
- [ ] **Hard Gemini dependency:** None.

### 20. User-facing docs are Codex-first

- [ ] **Where:** `README.md:1-123`; `docs/architecture.md:1-94`; `docs/roadmap.md:1-50`; `docs/configuration.md:1-187`; `docs/install.md:1-86`; `skills/help/SKILL.md:1-49`; `skills/status/SKILL.md` if it mentions backend status; npm docs `npm/README.md:3-23`.
- [ ] **What Codex does:** README title/value prop, quickstart, prefix guidance, sandbox posture, and feature matrix are Codex-oriented (`README.md:1-100`). Architecture describes Codex App Server default and Codex fresh-exec opt-out (`docs/architecture.md:48-55`). Configuration documents Codex model catalog, `codex-*` teammate naming, `CODEX_BINARY`, `CLAUDE_ANYTEAM_APP_SERVER`, and Codex sandbox bypass (`docs/configuration.md:13-174`). Install docs mention Codex CLI prereq and TUI presence (`docs/install.md:1-86`). Help skill teaches Claude to create `codex-*` teammates and includes `^codex-` regex (`skills/help/SKILL.md:1-49`). Roadmap says Codex adapter is the current shipped backend and pluggable backend routing is future (`docs/roadmap.md:5-26`).
- [ ] **Gemini equivalent:** Update docs after implementation to describe two first-class backends. Add Gemini quickstart, `gemini-*` naming, Gemini CLI prereq/auth, config isolation, known limitations vs Codex App Server, model/env settings, TUI presence requirement, and examples of mixed Codex/Gemini teams. Help skill must teach both prefixes and when to choose Gemini vs Codex. Roadmap should move pluggable backend routing from future to shipped/partial and link any limitations doc.
- [ ] **Hard Gemini dependency:** Runtime limitations from Researcher A and implementer, especially MCP/tool-call and mid-turn parity.

### 21. Tests specific to Codex adapter need Gemini counterparts

- [ ] **Where:** Codex-focused tests include `tests/test_codex_event_matching.py:1-104`, `tests/test_codex_invocation_shape.py:1-120`, `tests/test_codex_mcp_config.py:1-77`, `tests/test_app_server_client.py:1-510`, `tests/test_app_server_default.py:1-120`, `tests/test_model_effort_flags.py:1-190`, `tests/test_resume_dispatch.py:1-220`, `tests/test_fix_forward.py:1-52`, `tests/test_registration.py:22-52`, `tests/test_registration_live.py:61-132`, `tests/test_install_command.py:940-1080`, `tests/test_spawn_shim.py` (routing), `tests/test_skills.py:53-62`, `tests/test_plugin_bundle.py:17-20`, `44-57`. Additional shared or integration tests that need Gemini counterpart/adjustment review: `tests/test_loop_unit.py`, `tests/test_plan_approval.py`, `tests/test_fork_dispatch.py`, `tests/test_app_server_mcp_config.py`, `tests/test_schema_validation.py`, `tests/test_wrapper_contract.py`, `tests/test_registration_race.py`.
- [ ] **What Codex does:** Guards Codex argv shape, sandbox bypass, JSONL event classifier, MCP `-c` config, App Server JSON-RPC, App Server default, model/effort plumbing, resume dispatch, stdin DEVNULL, registration metadata, Codex CLI installer warnings, prefix routing, loop planning/approval behavior, fork dispatch, schema prompt/validation behavior, wrapper contract, registration race behavior, and docs/skill Codex guidance.
- [ ] **Gemini equivalent:** Add tests mirroring each relevant Codex category: Gemini invocation argv and timeout; Gemini stream-json event parsing; Gemini MCP settings writer and isolated HOME/real HOME for wrapper; Gemini schema validation/retry; Gemini resume dispatch; Gemini feature-test/version warning; spawn shim `gemini-*`; registration Gemini metadata; docs/skill/plugin text; installer Gemini prereq state; loop/plan approval behavior using Gemini prompts; fork/resume behavior only where Gemini supports it; schema labels that do not say Codex in Gemini prompts; wrapper contract remains shared. Do not duplicate Codex App Server tests unless Gemini ACP is implemented; instead adjust App Server-only tests to document Codex-only scope or skip Gemini where appropriate.
- [ ] **Hard Gemini dependency:** Runtime event fixtures and version/flag probes from Researcher A.

### 22. Existing Gemini planning docs already define approved constraints

- [ ] **Where:** `docs/gemini-adapter-feasibility.md:1-120`; `docs/internal/gemini-plans.md:1-90`; `docs/internal/gemini-research-official.md`; `docs/internal/gemini-research-reverse.md`.
- [ ] **What Codex does:** These docs compare Gemini plans to Codex's integration and already identify key repo changes: Gemini runner, settings isolation, loop split, registration generalization, prompt split, spawn shim route, pyproject entrypoint, docs/tests.
- [ ] **Gemini equivalent:** Treat these as constraints, but update with Researcher A's final findings once `docs/internal/gemini-integration/gemini-runtime.md` exists. If final runtime research contradicts older plan docs, implementer should prefer the final runtime doc and update the older docs or note drift.
- [ ] **Hard Gemini dependency:** Researcher A's final runtime report.

## Suggested implementation order

1. Add Gemini runtime module and tests for feature-test, config isolation, MCP settings, invocation/event parsing, schema validation/retry.
2. Add Gemini loop/CLI using shared protocol I/O and wrapper server.
3. Generalize registration metadata and add Gemini registration tests.
4. Extend spawn shim routing and pyproject/bin entrypoints.
5. Extend installer prereq/state/reporting if Gemini should be checked at install time.
6. Update docs, plugin manifests, hook text, and skills.
7. Run full test suite and add a limitations doc for any missing Codex parity (especially App Server `turn/steer`).

## Open dependencies for Researcher A

- Exact Gemini CLI version floor and install/auth guidance.
- Exact headless command line and whether `--output-format stream-json` is stable.
- Exact stream event types for final assistant text, tool calls, tool results, errors, and session ids.
- Whether Gemini supports a reliable `--resume <session_id>` and how to select adapter-owned session storage.
- Exact MCP settings schema, server alias normalization, trust/approval flags, and child env behavior.
- Whether Gemini has any ACP/mid-turn steering capability comparable to Codex App Server `turn/steer`.
