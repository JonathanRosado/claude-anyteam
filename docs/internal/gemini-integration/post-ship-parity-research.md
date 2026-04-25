# Post-ship Gemini parity research: repo integration

Date: 2026-04-24  
Scope owner: `codex-parity-researcher`  
Task scope: repo-integration side only; runtime/ACP viability is intentionally left to Researcher A.

## Executive summary

Plan B still holds, with one important drift from the original plan: PR #8 did not thread Gemini into the existing `src/claude_anyteam/loop.py`; it added a parallel Gemini backend under `src/claude_anyteam/backends/gemini/`. That makes the lowest-risk ACP integration point the Gemini backend package, not the Codex loop. The implementable next PR should:

1. Extract the transport-only portion of `src/claude_anyteam/app_server.py` into `src/claude_anyteam/jsonrpc_stdio.py`.
2. Keep Codex-specific methods (`thread/start`, `turn/start`, `turn/steer`, `thread/fork`) in `app_server.py` as a thin subclass/wrapper.
3. Add `src/claude_anyteam/backends/gemini/acp_client.py` and `src/claude_anyteam/backends/gemini/acp.py` that reuse the transport and shape results as `CodexResult`.
4. Add a backend selector to `src/claude_anyteam/backends/gemini/cli.py`, defaulting to the shipped headless path and allowing `--backend acp` when Researcher A confirms the ACP prompt flow.
5. Replace symlinked Gemini state with per-session copies for mutable/auth/session files; keep only stable executable cache or non-secret global preferences shared.
6. Map Codex effort to Gemini model config only if we are willing to write generated `modelConfigs.customAliases`/`customOverrides`; there is no `gemini --effort` or `gemini --thinking` CLI flag in 0.39.0.
7. Harden installer checks by probing required capabilities (`--prompt`, `--output-format stream-json`, `--resume`, `--approval-mode`, and optionally `--acp`) instead of only `--version`.

## Sources checked

- Current repo files: `src/claude_anyteam/app_server.py`, `src/claude_anyteam/codex.py`, `src/claude_anyteam/loop.py`, `src/claude_anyteam/backends/gemini/{cli.py,config.py,invoke.py,loop.py}`, `src/claude_anyteam/installer.py`, and `docs/internal/gemini-plans.md`.
- Local installed Gemini CLI: `gemini --version` reports `0.39.0`; `gemini --help` lists headless, resume, output-format, approval-mode, and ACP flags.
- Gemini CLI ACP documentation says ACP mode is JSON-RPC 2.0 over stdio and started with `gemini --acp`; it lists `initialize`, `authenticate`, `newSession`, `loadSession`, `prompt`, `cancel`, `setSessionMode`, and `unstable_setSessionModel`. Source: https://geminicli.com/docs/cli/acp-mode/
- ACP protocol session setup defines `session/new`, `session/load`, optional `session/resume`, and `session/cancel` as session-id-based operations. Source: https://agentclientprotocol.com/protocol/session-setup
- Gemini CLI model configuration documentation exposes `modelConfigs`, `customAliases`, `customOverrides`, and SDK-level `generateContentConfig.thinkingConfig` (`thinkingBudget`, `thinkingLevel`). Source: installed docs at `/usr/local/lib/node_modules/@google/gemini-cli/bundle/docs/cli/generation-settings.md` and public configuration reference: https://github.com/google-gemini/gemini-cli/blob/main/docs/reference/configuration.md

---

## 1. ACP client integration shape

### Current code facts

`src/claude_anyteam/app_server.py` currently combines two separable concerns:

- Provider-neutral JSON-RPC-over-stdio transport:
  - subprocess start/close (`AppServerClient.start`, lines 76-97; `close`, lines 99-127)
  - request/notify (`request`, lines 138-183; `notify`, lines 185-195)
  - notification queue helpers (`drain_notifications`, lines 199-207; `wait_for_notification`, lines 209-237)
  - stdout/stderr readers and dispatcher (`_read_loop`, lines 241-262; `_drain_stderr`, lines 264-273; `_dispatch`, lines 275-306)
- Codex App Server methods:
  - `initialize`, lines 310-315
  - `thread_start`, lines 317-350
  - `turn_start`, lines 352-379
  - `turn_steer`, lines 381-394
  - `turn_interrupt`, lines 396-400
  - `thread_fork` and materialization helpers later in the same file

The Gemini backend shipped as a separate package:

- `src/claude_anyteam/backends/gemini/cli.py` has a `gemini-anyteam` entry point with no backend selector.
- `src/claude_anyteam/backends/gemini/loop.py` mirrors the Codex task protocol but directly calls `invoke.run(...)` for headless Gemini (`_execute_task`, lines 295-313) and stores one in-memory `gemini_session_id` (lines 24-31, 314-315).
- `src/claude_anyteam/backends/gemini/invoke.py` builds `gemini --prompt ... --output-format stream-json --approval-mode yolo`, then optionally passes `--model` and `--resume` (lines 149-153).

### Drift from `docs/internal/gemini-plans.md` Plan B

Plan B said to add `gemini_acp.py` / `gemini_acp_client.py` and have `loop.py` choose a backend implementation. That concept still holds, but the file-level target has drifted:

- Do **not** put Gemini ACP selection in root `src/claude_anyteam/loop.py` first. PR #8 created `src/claude_anyteam/backends/gemini/loop.py`, so the ACP path should plug into that loop unless we first do a larger backend abstraction refactor.
- A provider-neutral transport still belongs at root (`src/claude_anyteam/jsonrpc_stdio.py`) because both Codex App Server and Gemini ACP need it.
- Gemini-specific ACP files should live under `src/claude_anyteam/backends/gemini/` to stay consistent with the shipped backend layout, e.g. `acp_client.py` and `acp.py` rather than root-level `gemini_acp_client.py` / `gemini_acp.py`.

### Implementable mitigation

**Transport extraction**

Create `src/claude_anyteam/jsonrpc_stdio.py`:

- Move `_Pending`, the generic exception class (rename to `JsonRpcStdioError`), and the transport methods from `AppServerClient`:
  - constructor should accept `argv: list[str]`, `env`, `log_prefix`, and optional `stderr_log_prefix`.
  - `start()` should run `self._argv`, not hard-code `[codex_binary, "app-server"]`.
  - `request()`, `notify()`, `drain_notifications()`, `wait_for_notification()`, `_read_loop()`, `_drain_stderr()`, and `_dispatch()` can be mechanically moved.
  - Preserve the current behavior of ignoring non-JSON stdout lines; Gemini ACP should be strict eventually, but this keeps parity with `app_server.py` while ACP stdout pollution is still being researched.
- Leave `src/claude_anyteam/app_server.py` as a Codex-specific wrapper:
  - `class AppServerError(JsonRpcStdioError): ...` for compatibility.
  - `class AppServerClient(JsonRpcStdioClient)` whose `__init__` passes `argv=[codex_binary, "app-server", *extra_args]`.
  - Keep all Codex methods starting at current line 310 in `app_server.py`.

**Gemini ACP client**

Create `src/claude_anyteam/backends/gemini/acp_client.py`:

- `class GeminiAcpError(JsonRpcStdioError)`.
- `class GeminiAcpClient(JsonRpcStdioClient)` starts `argv=[gemini_binary, "--acp"]` plus optional `--debug` when configured.
- Methods should follow the protocol names used by ACP, not Codex names:
  - `initialize(client_capabilities, mcp_servers)` or a direct `request("initialize", params)` wrapper once Researcher A confirms Gemini's exact initialize payload.
  - `authenticate()` if needed.
  - `session_new(cwd, mcp_servers) -> session_id` using `session/new` if Gemini follows spec, with tolerance for Gemini docs' client-facing names (`newSession`) if Researcher A finds those are the actual method strings.
  - `session_load(session_id, cwd, mcp_servers)`, guarded by initialize capabilities.
  - `session_prompt(session_id, prompt)`, collecting `session/update` notifications.
  - `session_cancel(session_id)`.
  - optional `set_session_mode(session_id, mode)` and `unstable_set_session_model(session_id, model)`; the Gemini CLI ACP docs list these as supported session control methods.

**High-level Gemini ACP invocation**

Create `src/claude_anyteam/backends/gemini/acp.py`:

- Function shape should mirror `invoke.run(...)` enough for `loop.py` to switch without changing completion logic:

```python
def run(
    prompt: str,
    *,
    cwd: Path,
    schema: Path | None = None,
    gemini_binary: str = "gemini",
    timeout_s: float = 900.0,
    wrapper_identity: tuple[str, str] | None = None,
    session_id: str | None = None,
    model: str | None = None,
    gemini_home: Path | None = None,
    effort: str | None = None,
) -> CodexResult:
    ...
```

- It should call shared Gemini settings writer before process start.
- It should build ACP `mcpServers` directly rather than relying only on `~/.gemini/settings.json` if Gemini ACP supports client-provided MCP servers. ACP spec says `session/new` includes MCP servers; Gemini docs say the client provides connection details during initialize. Either way, the repo should use the ACP-native MCP path for the wrapper and keep the settings file only for auth/model config.
- It should parse `session/update` notifications into `last_message`, count tool calls where updates indicate tool calls, validate schema with existing `schema_validation.parse_and_validate`, and return `CodexResult(session_id=<current acp session id>)`.

**Loop plug-in**

Modify `src/claude_anyteam/backends/gemini/config.py`:

- Add `backend: Literal["headless", "acp"] = "headless"` or a plain validated string.
- Add env `CLAUDE_ANYTEAM_GEMINI_BACKEND`.
- Add `effort: str | None = None` only if target 3 below is implemented.

Modify `src/claude_anyteam/backends/gemini/cli.py`:

- Add `--backend {headless,acp}`.
- Add `--effort {minimal,low,medium,high,xhigh}` only if we implement model-config mapping.

Modify `src/claude_anyteam/backends/gemini/loop.py`:

- Import both headless invoke and ACP invoke, e.g. `from . import invoke as headless_invoke` and `from . import acp as acp_invoke`.
- In `run()`, call `headless_invoke.feature_test(...)` for headless, `acp_invoke.feature_test(...)` for ACP.
- In `_execute_task`, replace the hard-coded `invoke.run(...)` at lines 304-313 with `_backend_run(state, prompt, schema=...)`.
- In `_handle_prose` and `_handle_plan_approval`, route through `_backend_run` too. Prose should remain ephemeral for headless; for ACP, decide whether to use the same session or a short-lived session. Safer v1: use same session only for tasks, ephemeral ACP session for prose so peer chatter does not poison task lineage.
- Persist ACP session ID to an adapter state file (see target 2) instead of only `GeminiLoopState.gemini_session_id`; the existing in-memory-only field is fragile on adapter restart.

**Why implementable**: this is mostly a transport split plus a new Gemini-specific method layer. The control-plane task protocol already exists in `backends/gemini/loop.py`; no root-loop rewrite is needed.

---

## 2. Isolated-auth multi-session strategy

### Current code facts

`write_mcp_settings(...)` writes an adapter-owned `$HOME/.gemini/settings.json` under `gemini_home` (lines 79-101). It also:

- symlinks or copies selected files from the real `~/.gemini`: `oauth_creds.json`, `google_accounts.json`, `projects.json`, `trustedFolders.json`, and `installation_id` (lines 31-54)
- merges only `security.auth` from real `settings.json` (lines 57-76, 98)
- sets subprocess `HOME` to the adapter-owned home before launching Gemini (lines 145-157)

Observed real `~/.gemini/` on this host, without printing secret contents:

- Credentials/account files: `oauth_creds.json`, `google_accounts.json`
- Config/preferences: `settings.json`, `trustedFolders.json`, `projects.json`, `state.json`, `installation_id`
- Session/runtime state: `tmp/<project>/chats/session-*.jsonl`, `tmp/<project>/logs.json`, `tmp/<project>/.project_root`, `history/<project>/.project_root`
- Tool cache: `tmp/bin/rg`

### Safety classification

| State | Share? | Reason |
| --- | --- | --- |
| `settings.json.security.auth` | Copy/merge selected field | Current behavior is correct as a minimum; avoid importing user MCP servers into teammates. |
| `oauth_creds.json`, `google_accounts.json` | Copy at session-home creation; do **not** symlink | These are mutable auth/cache files. Two Gemini teammate processes following symlinks to the same files can race token refresh/account writes. Copying avoids write races and accidental auth-mode churn. |
| `projects.json` | Copy per adapter home | Small mutable project/account preference file. Sharing by symlink is unnecessary and can leak project selection across teammates. |
| `trustedFolders.json` | Copy/filter per adapter home | Sharing the user's entire trusted-folder map broadens trust. For yolo mode, the adapter should trust only the current `cwd` and explicit include dirs. |
| `installation_id` | Copy or generate per adapter home | It is stable identity/telemetry-ish state; sharing is not needed. Generate a UUID if absent to avoid multiple teammates presenting the same install identity. |
| `state.json` | Per adapter home | Contains UI/startup counters and tips; safe but mutable. Copying creates harmless divergence. Sharing risks small write races. |
| `tmp/<project>/chats/session-*.jsonl` | Must be per session home | This is conversation/session history. Sharing breaks `--resume latest`, leaks context between two Gemini teammates in the same repo, and risks concurrent appends. |
| `tmp/<project>/logs.json`, `.project_root`, `history/<project>` | Must be per session home | Runtime/session index state; same reasons as chat JSONL. |
| `tmp/bin/rg` | Can be shared by copy or re-download; do not symlink by default | Executable cache is not logically session state. Sharing is low risk if immutable, but simplest safe v1 is let each home populate it. |
| User-level MCP servers/extensions/skills | Do not import by default | Teammates should see only `anyteam` wrapper unless explicitly configured. |

### Implementable mitigation

Replace `_link_auth_cache(...)` with a more explicit `prepare_isolated_gemini_home(...)` in `src/claude_anyteam/backends/gemini/invoke.py` or a new `state.py`:

1. Create home root as now: `~/.cache/claude-anyteam/gemini/<team>/<agent>/` (`_default_gemini_home`, lines 21-24).
2. On first creation, copy (not symlink) only:
   - `oauth_creds.json`
   - `google_accounts.json`
   - `projects.json`
   - `state.json`
   - `installation_id` (or generate if absent)
3. Write a filtered `trustedFolders.json` containing only `cwd` and any future explicit include dirs. If exact schema is unknown, omit and rely on `--approval-mode yolo`/ACP session mode rather than importing global trust.
4. Continue to merge `security.auth` only from real `settings.json`; do not merge `mcpServers`, hooks, extensions, model configs, or UI settings unless the adapter owns those keys.
5. Never copy `tmp/` or `history/` from the real home. Let Gemini create fresh per-teammate session stores under the adapter home.
6. Persist adapter-owned state separate from Gemini's own files:
   - `gemini_home/.claude-anyteam/state.json` containing `{ "headless_session_id": ..., "acp_session_id": ..., "backend": ..., "updated_at": ... }`.
   - Use atomic write (`tmp` + replace) because the adapter may be interrupted.
7. For currently shipped code, change `_link_auth_cache` lines 31-54 from symlink-first to copy-first and expand the copied set to include `state.json`; add tests that two distinct agents get distinct physical files (`Path.is_symlink() == False`) and isolated `tmp/` directories.

**Current risk**: The symlinks in lines 45-54 are the main multi-session hazard. Two Gemini teammates can currently share the same OAuth/account files and the same user trust/project files. The session chats are already isolated only because `HOME` is changed and `tmp/` is not linked; keep it that way.

---

## 3. Effort mapping

### Current code facts

Codex supports model/effort in two paths:

- Root config accepts `effort` and validates `low|medium|high|xhigh` (currently no `minimal`) in `src/claude_anyteam/config.py` lines 119-124.
- Codex exec maps effort to `-c model_reasoning_effort="..."` in `src/claude_anyteam/codex.py` lines 374-378.
- Codex App Server passes `effort` as a JSON-RPC `turn/start` param in `app_server.py` lines 352-371.

Gemini currently accepts only `model`:

- `GeminiSettings` has `model` but no `effort`.
- `invoke.run` accepts `model` but no `effort` (lines 132-142).
- Args include `--model` only when set (lines 149-153).

Gemini CLI 0.39.0 help has no `--effort`, `--thinking`, or `--thinking-budget` flag. However, its advanced model configuration supports `modelConfigs.customAliases`/`customOverrides` that pass SDK-level `generateContentConfig.thinkingConfig` with `thinkingBudget`, `includeThoughts`, and for Gemini 3 aliases `thinkingLevel`. Built-in docs show `chat-base-2.5` using `thinkingBudget: 8192`, `chat-base-3` using `thinkingLevel: "HIGH"`, and examples of an override with `thinkingBudget: 4096`.

### Implementable mitigation options

**Option A — document gap only (lowest risk)**

- Keep Gemini CLI parity as `--model` only.
- Update Gemini docs/limitations to say: no CLI flag equivalent to Codex `--effort`; users can supply model aliases manually in `~/.gemini/settings.json`, but the adapter intentionally does not mutate global model configs.
- This is honest but leaves a user-visible parity gap.

**Option B — generated per-teammate model aliases (implementable, behind feature flag)**

Because we already write adapter-owned `settings.json`, we can add adapter-owned `modelConfigs.customAliases` there without mutating the user's real settings.

Proposed mapping for Gemini 2.5-style `thinkingBudget` models:

| Codex effort | Gemini alias config |
| --- | --- |
| `minimal` | `thinkingBudget: 0`, low `maxOutputTokens` only if needed |
| `low` | `thinkingBudget: 512` |
| `medium` | `thinkingBudget: 2048` |
| `high` | `thinkingBudget: 4096` |
| `xhigh` | `thinkingBudget: 8192` |

Proposed mapping for Gemini 3-style `thinkingLevel` models:

| Codex effort | Gemini alias config |
| --- | --- |
| `minimal` / `low` | request a non-thinking/base alias if available; otherwise `thinkingLevel: "LOW"` only if supported by the current model config schema |
| `medium` | `thinkingLevel: "MEDIUM"` if supported; otherwise omit |
| `high` / `xhigh` | `thinkingLevel: "HIGH"` |

Concrete file changes:

- `src/claude_anyteam/backends/gemini/config.py`: add `effort` and validate `{minimal,low,medium,high,xhigh}`. Consider also widening root Codex config to accept `minimal` if the user requirement is now Codex supports it.
- `src/claude_anyteam/backends/gemini/cli.py`: add `--effort`.
- `src/claude_anyteam/backends/gemini/invoke.py`: extend `write_mcp_settings(..., model=None, effort=None)` to create an alias name like `claude-anyteam-{agent}-effort-high`, then set:

```json
"modelConfigs": {
  "customAliases": {
    "claude-anyteam-effort-high": {
      "extends": "gemini-2.5-pro",
      "modelConfig": {
        "generateContentConfig": {
          "thinkingConfig": { "thinkingBudget": 4096 }
        }
      }
    }
  }
}
```

- Pass `--model <alias>` instead of the raw model when effort is set.
- For Gemini 3, use a separate alias branch with `thinkingLevel`. This should be feature-probed against the installed Gemini docs/settings schema or guarded by model-family matching (`gemini-3` vs `gemini-2.5`).

**Recommendation**: Implement Option B only after one manual smoke test confirms Gemini CLI applies adapter-local `modelConfigs.customAliases` in headless mode. If not confirmed, ship Option A docs plus a warning that `--effort` is ignored for Gemini.

**Gap to document**: Even with Option B, this is not true Codex `--effort` parity. It is a best-effort model-generation-config mapping; Gemini model support differs by family and account tier, and there is no stable CLI switch equivalent to Codex `model_reasoning_effort`.

---

## 4. Installer probe hardening

### Current code facts

There are two probes today:

- Runtime Gemini `feature_test` already probes `--version` and `--help`, then requires `--prompt`, `--output-format`, `--resume`, and `--approval-mode` in help text (`src/claude_anyteam/backends/gemini/invoke.py` lines 104-117).
- Installer `_check_gemini_cli` only runs `gemini --version`, parses version, and returns found/version/raw output (`src/claude_anyteam/installer.py` lines 587-601). It does not warn on missing headless flags, output-format choices, or ACP support.

Local Gemini CLI 0.39.0 supports:

- `--prompt`
- `--output-format` with choices `text`, `json`, `stream-json`
- `--resume`
- `--approval-mode` with choices `default`, `auto_edit`, `yolo`, `plan`
- `--acp` and deprecated `--experimental-acp`

Gemini public docs list stable release v0.38.2 as of April 17, 2026. The local 0.39.0 appears to be newer/nightly. Because Gemini CLI is moving quickly, capability probes are more reliable than semver-only checks.

### Implementable mitigation

Enhance `GeminiCliCheck` in `src/claude_anyteam/installer.py` to include:

```python
@dataclass(frozen=True)
class GeminiCliCheck:
    found: bool
    path: Path | None
    version: str | None
    raw_output: str | None
    has_headless: bool = False
    has_stream_json: bool = False
    has_resume: bool = False
    has_yolo_approval: bool = False
    has_acp: bool = False
    missing_capabilities: tuple[str, ...] = ()
```

Modify `_check_gemini_cli` at lines 587-601:

1. Keep `--version` for display and semver parsing.
2. Run `gemini --help` with the same timeout.
3. Parse help text for required Plan A capabilities:
   - `--prompt` or `-p, --prompt`
   - `--output-format` and `stream-json`
   - `--resume`
   - `--approval-mode` and `yolo`
4. Parse optional Plan B capability:
   - `--acp` or `--experimental-acp`
5. Return missing capabilities. The install warning should distinguish:
   - missing binary: current hard warning
   - binary present but missing Plan A capabilities: warn that `gemini-*` teammates are unsupported until upgrade
   - binary present with Plan A but missing ACP: no warning for current headless default, but include an informational note if user selected/configured ACP backend

Suggested logic:

```python
help_text = ""
try:
    help_completed = subprocess.run([str(resolved), "--help"], ...)
    if help_completed.returncode == 0:
        help_text = (help_completed.stdout or "") + (help_completed.stderr or "")
except ...:
    pass

capabilities = {
    "--prompt": "--prompt" in help_text,
    "--output-format stream-json": "--output-format" in help_text and "stream-json" in help_text,
    "--resume": "--resume" in help_text,
    "--approval-mode yolo": "--approval-mode" in help_text and "yolo" in help_text,
}
has_acp = "--acp" in help_text or "--experimental-acp" in help_text
missing = tuple(name for name, ok in capabilities.items() if not ok)
```

Minimum-version policy:

- Do not enforce semver as the primary gate. Use capability checks.
- If semver is available, warn when version `< 0.39.0` **only for ACP backend selection**, not for headless Plan A, because ACP behavior has been changing and local validation target is 0.39.0.
- For headless, capability checks are sufficient and avoid blocking a newer/preview build with unusual version strings.

Add tests:

- `tests/test_installer_gemini_probe.py::test_gemini_probe_headless_ok`
- `test_gemini_probe_missing_stream_json_warns`
- `test_gemini_probe_acp_optional`
- `test_gemini_warning_distinguishes_missing_binary_from_missing_capability`

---

## Notify-before-implementation items

Promising implementable mitigations found:

1. Extract `jsonrpc_stdio.py` and add Gemini ACP client under `src/claude_anyteam/backends/gemini/`.
2. Replace symlinked auth/project files with per-adapter-home copies and never import real `tmp/`/`history/`.
3. Add generated model-config aliases for effort mapping, behind smoke-test/feature flag.
4. Harden installer probe with help/capability checks.

Per instruction, I am not implementing these in this research pass.
