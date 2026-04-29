# Adding a backend to claude-anyteam

This guide is for contributors adding another routed harness after Codex, Gemini, and Kimi: for example `glm-*`, `qwen-*`, or `deepseek-*`. It uses the Kimi integration as the concrete reference because Kimi is the newest backend in this tree and exercises the headless-CLI path, capability manifests, spawn routing, and stress harness.

The rule from [`CLAUDE.md`](../CLAUDE.md) is non-negotiable: **the harness is the teammate**. Do not turn a new backend into a model behind a shared router. Keep its CLI, auth, sessions, tool surface, prompt tuning, and special capabilities native, and adapt only at the team-protocol boundary.

## 1. Architecture overview

Read these first:

- [`CLAUDE.md §1`](../CLAUDE.md#north-star-1-harness-preservation-the-architectural-moat): preserve harness capabilities; do not flatten to a lowest common denominator.
- [`CLAUDE.md §2`](../CLAUDE.md#north-star-2-visibility-parity-the-operational-consequence): routed teammates must be observable like native Claude teammates.
- [`CLAUDE.md §3`](../CLAUDE.md#north-star-3-peer-efficiency-the-team-productivity-invariant): peer-to-peer messaging, steer, and handoff must remain cheap and reliable.
- [`docs/architecture.md §1`](architecture.md#1-harness-preserving-capability-substrate), [`§2`](architecture.md#2-visibility-parity-substrate), and [`§3`](architecture.md#3-peer-efficiency-substrate): concrete substrate primitives for those north stars.
- [`docs/architecture.md`](architecture.md): end-to-end spawn, adapter, backend, and visibility design.

claude-anyteam has two protocol layers:

1. **Transport**: universal Agent Teams files and lifecycle — config, inboxes, tasks, locks, idle/shutdown/plan/permission messages.
2. **Capability**: per-harness declarations — typed primitives, schemas, descriptions, when-to-use guidance, and failure modes.

The backend itself remains native. Kimi shows the intended split:

- `src/claude_anyteam/backends/kimi/loop.py` speaks Agent Teams: registration, inbox polling, task claiming, prose/plan/shutdown handling, capability-cache loading, task completion, and blocking.
- `src/claude_anyteam/backends/kimi/invoke.py` speaks Kimi: CLI feature tests, credential preflight, isolated HOME/MCP config, `kimi --print`, stream parsing, session capture, and visibility events.
- `src/claude_anyteam/backends/kimi/prompts.py` teaches Kimi the anyteam tools in Kimi's tool-name shape.
- `src/claude_anyteam/capabilities.py` declares what Kimi can safely promise to peers.
- `src/claude_anyteam/spawn_shim.py` and `tools/stress/run_scenario.py` route and spawn `kimi-*` members.

## 2. File checklist for a new backend

Assume a new backend named `<name>` with teammate prefix `<name>-`, adapter entry point `<name>-anyteam`, and native CLI binary `<name>`.
For historical breadcrumbs, `git log --oneline --grep="kimi" -20` shows the integration commits this checklist was derived from.

### 2.1 Backend package

Create the backend package:

```text
src/claude_anyteam/backends/<name>/
├── __init__.py
├── loop.py
├── prompts.py
├── invoke.py
└── cli.py
```

Kimi also has `config.py`; most new backends should too, even though the core adapter surface above is the minimum required set.

#### `__init__.py`

Keep it import-only, like `src/claude_anyteam/backends/kimi/__init__.py`:

```python
"""Kimi CLI backend for claude-anyteam."""
```

#### `config.py` (recommended)

Mirror `src/claude_anyteam/backends/kimi/config.py`:

- define env names such as `CLAUDE_ANYTEAM_<NAME>_BINARY`, `CLAUDE_ANYTEAM_<NAME>_HOME`, and `CLAUDE_ANYTEAM_<NAME>_BACKEND`;
- define valid transports and shared `minimal|low|medium|high|xhigh` effort values;
- expose a frozen `<Name>Settings` dataclass;
- expose `from_env(overrides)` that validates `--team`, `--name`, `--cwd`, `--model`, `--effort`, `--backend`, and backend-specific knobs.

Keep `--model` a pass-through slug accepted by the native CLI. Keep `--effort` the claude-anyteam five-tier enum and map it at the backend boundary, even when that mapping is lossy.

#### `cli.py`

Mirror `src/claude_anyteam/backends/kimi/cli.py`: parse common args, parse backend-specific args, build settings with `from_env()`, then return `loop.run(settings)`.

Skeleton for `glm-*`:

```python
p = argparse.ArgumentParser(prog="glm-anyteam", description="Route glm-* teammates through GLM CLI.")
p.add_argument("--team")
p.add_argument("--name")
p.add_argument("--cwd")
p.add_argument("--poll-s", type=float)
p.add_argument("--color")
p.add_argument("--plan-mode", action="store_true")
p.add_argument("--glm-binary")
p.add_argument("--model")
p.add_argument("--effort", choices=sorted(GLM_EFFORTS))
p.add_argument("--glm-home")
p.add_argument("--backend", choices=sorted(GLM_BACKENDS), default="headless")
```

Add a console script in `pyproject.toml`, following Kimi:

```toml
glm-anyteam = "claude_anyteam.backends.glm.cli:main"
```

#### `invoke.py`

This is the harness-native boundary. Model it on `src/claude_anyteam/backends/kimi/invoke.py`, but replace every Kimi assumption with real probes of the new CLI.

Required pieces:

- `feature_test(binary)`: verify the binary exists and required flags are present.
- `credential_preflight(...)`: run a cheap authenticated prompt before registration; classify auth/quota/version failures early.
- isolated HOME setup: copy mutable auth files instead of symlinking if the CLI refreshes tokens.
- wrapper MCP config writing: point the backend at `claude-anyteam-wrapper --team <team> --name <agent>`.
- session-state helpers when the backend supports resume.
- `run(prompt, *, cwd, schema, <name>_binary, timeout_s, wrapper_identity, resume_session_id, model, effort, <name>_home, task_id, event_sink=None, ...)` returning `claude_anyteam.codex.CodexResult`.
- schema validation with `load_schema()` and `parse_and_validate()`.
- `HeadlessTurnVisibility.start(...)` before invocation and `visibility.terminal(...)` on every success, failure, and timeout path.

Kimi examples worth tracing:

- `default_kimi_home()` isolates per-team/per-agent state under `~/.cache/claude-anyteam/kimi/...`.
- `prepare_isolated_kimi_home()` copies OAuth files from `~/.kimi/credentials/kimi-code.json` and `.lock`.
- `write_mcp_config()` writes Kimi's MCP config with the anyteam wrapper server.
- `_thinking_args()` maps anyteam effort to Kimi thinking flags.
- `_parse_stdout()` parses Kimi NDJSON and counts `assistant.tool_calls[]`.
- `_extract_session_id()` reads Kimi's resume hint from stderr.

#### `prompts.py`

Mirror `src/claude_anyteam/backends/kimi/prompts.py` and adapt tool names to the new backend. Kimi exposes bare MCP names (`send_message`, `task_update`), while Gemini exposes `mcp_anyteam_*`; your prompts must match what the CLI actually shows the model.

Provide:

- `task_prompt(task, agent_name, team_name, peer_prompt_fragments="")`;
- `prose_reply_prompt(sender, body, agent_name, team_name, peer_prompt_fragments="")`;
- `plan_prompt(task, *, tighten, agent_name, team_name)`.

Include `TEAM_MESSAGING_BLOCK`, include peer capability fragments, and state that final local prose is not a teammate-visible DM: use `send_message` for peer messages.

#### `loop.py`

Model the loop on `src/claude_anyteam/backends/kimi/loop.py`:

1. run feature/auth preflight before registration;
2. call `register(settings, _backend_metadata(settings))`;
3. read the self manifest with `pio.read_agent_manifest(...)`;
4. create `CapabilityManifestCache(...).load_startup()`;
5. install SIGINT/SIGTERM handlers;
6. use `WatchInbox.for_team(...)` and `adaptive_wait_s(...)`;
7. dispatch `ShutdownRequestIn`, `PlanApprovalRequestIn`, `CapabilityManifestUpdatedIn`, `SteerIn`, and prose;
8. claim tasks with `protocol_io` compare-and-set helpers;
9. build backend prompts, append schema fragments, invoke the backend, retry once on schema failure, and update task state;
10. emit task-complete, blocked, startup-crash, auth-preflight, and diagnostics events.

The metadata function ties the loop to the capability layer. Kimi's version:

```python
capabilities = assert_known_capabilities(KIMI_HEADLESS_CAPABILITIES)
return BackendMetadata(
    model="kimi-cli",
    prompt="Kimi teammate adapter...",
    capabilities=capabilities,
    capability_manifest=rich_capability_manifest(capabilities, host_tool_surface="kimi-native"),
    transport="kimi-headless",
    host_tool_surface="kimi-native",
    coupling_regime="loose",
)
```

For `glm-*`, use `GLM_HEADLESS_CAPABILITIES`, `model="glm-cli"`, `transport="glm-headless"`, and a truthful `host_tool_surface` such as `"glm-native"`.

### 2.2 Capability declarations

Edit `src/claude_anyteam/capabilities.py`.

This checkout uses per-backend constants rather than a single `BACKEND_CAPABILITIES` dict. Kimi declares:

```python
KIMI_HEADLESS_CAPABILITIES = [
    "headless_invocation",
    "session_resume",
    "structured_output",
    "plan_mode",
    "native_skills",
    "large_context",
]
```

Add the new backend's equivalent, for example:

```python
GLM_HEADLESS_CAPABILITIES = [
    "headless_invocation",
    "session_resume",
    "structured_output",
    "plan_mode",
]
```

If a later branch has a `BACKEND_CAPABILITIES` dict, add the same declaration there too. Then update:

- `CAPABILITY_NAMES` only for genuinely new primitives;
- `CAPABILITY_HOOKS` / `CAPABILITY_RUNTIME_REGISTRY` with runtime paths and focused tests for every newly declared primitive;
- `_BASE_CAPABILITY_MANIFEST` for any new primitive, including `version`, `schema`, `description`, `when_to_use`, `when_not_to`, `failure_modes`, and `callable_from_peers`;
- `CAPABILITY_MANIFEST_VERSION` and the `BackendMetadata.capability_version` value if manifest semantics changed and peers need to reload;
- tests that enumerate backend capability constants.

Peer-prompt-fragment registration is automatic once the Agent Card is correct: `rich_capability_manifest()` builds entries, `registration.register()` writes and broadcasts the card, and `peer_prompt_fragment()` / `peer_prompt_fragments_for()` teach peers from those entries.

### 2.3 Routing in name-prefix detection

Edit `src/claude_anyteam/spawn_shim.py`. Search for `kimi-` and mirror all routing points:

```python
DEFAULT_KIMI_MATCH = r"^kimi-"
KIMI_BINARY = "kimi-anyteam"

def _kimi_route(parsed: ParsedArgs) -> bool:
    return _route_match(parsed, env_name=KIMI_SHIM_MATCH_ENV, legacy_env_name=None, default=DEFAULT_KIMI_MATCH)
```

and:

```python
if _kimi_route(parsed):
    binary = _require_binary(_resolve_binary(KIMI_BINARY, KIMI_BINARY_ENV), KIMI_BINARY)
    adapter_argv, agent_config = _adapter_argv(binary, parsed, include_effort=True)
    _log_dispatch("kimi", parsed.agent_name, binary, agent_config or None)
    os.execv(binary, adapter_argv)
```

For GLM, add env constants in `src/claude_anyteam/env.py`, import them, define `DEFAULT_GLM_MATCH = r"^glm-"`, `GLM_BINARY = "glm-anyteam"`, `_glm_route()`, and a dispatch branch before native Claude fallback. Extend `tests/test_spawn_shim.py` for route hit, env override, route conflicts, and forwarded `model`/`effort`.

### 2.4 Member spawn args in stress runs

Edit `tools/stress/run_scenario.py`.

Kimi's `_command_for_member()` branch is the pattern:

```python
if agent_type == "kimi":
    cmd = [*base, "claude_anyteam.backends.kimi.cli", "--team", team_name, "--name", name, "--cwd", cwd]
    cmd += ["--kimi-binary", _resolve_backend_binary("kimi")]
    if member.get("model"):
        cmd += ["--model", str(member["model"])]
    if member.get("effort"):
        cmd += ["--effort", str(member["effort"])]
    cmd += ["--backend", str(member.get("transport", "headless"))]
    return cmd
```

Add a new branch using `claude_anyteam.backends.<name>.cli`, `--<name>-binary`, and a backend type such as `<name>_headless`. Also update `BACKEND_TYPE_BY_MEMBER` and any backend-label helper. Passing the absolute native CLI path prevents the spawned adapter from probing a sibling `*-anyteam` shim on `PATH`.

### 2.5 Stress scenarios

Add scenarios that include the backend in both same-backend and cross-backend settings. Kimi appears in:

- `S4` homogeneous Kimi;
- `S5` heterogeneous Claude/Codex/Gemini/Kimi;
- `S8` paired Kimi/Codex;
- `S10a` and `S10b` ablations based on the mixed team.

For a new backend, add at least a homogeneous scenario, one mixed scenario with Codex App Server and Gemini ACP, and one paired cross-backend scenario. Extend `tests/stress/test_run_scenario.py` so `_command_for_member()` is deterministic and forwards binary/model/effort/transport correctly.

### 2.6 Packaging and docs follow-through

Search for `kimi-*` and update analogous surfaces:

- `pyproject.toml` console scripts;
- `src/claude_anyteam/env.py` env constants;
- `src/claude_anyteam/team_cli.py` routed-prefix validation/help;
- `src/claude_anyteam/diagnose_cli.py` routed-prefix lists;
- `src/claude_anyteam/installer.py` managed binaries, allowlists, and CLI/auth checks;
- `npm/` installer/help text;
- help/status skills and docs that enumerate prefixes.

A backend that lacks a console script, shim route, or installer-managed binary is not launchable from Claude Code, even if the Python module works.

## 3. Capability layer guidance

The full enum is `CAPABILITY_NAMES` in `src/claude_anyteam/capabilities.py`:

- `turn_steer`
- `thread_fork`
- `permission_bridge`
- `live_tool_events`
- `structured_output`
- `headless_invocation`
- `session_resume`
- `plan_mode`
- `trust_modes`
- `native_skills`
- `large_context`
- `accepts_peer_steer`
- `soft_non_progress_watchdog`

Declare only what the backend implements. `assert_known_capabilities()` rejects unknown flags and flags missing runtime/test hooks.

| Capability | Declare when | Teach peers to use it when... |
| --- | --- | --- |
| `headless_invocation` | Noninteractive CLI turns produce parseable output. | Work is one-shot/batchable and does not need live steering. |
| `structured_output` | Task/plan output is schema-validated. | The lead needs machine-readable completion metadata. |
| `session_resume` | The adapter validates and resumes backend-native sessions. | Follow-up work needs prior findings; not after failed/ephemeral turns. |
| `plan_mode` | The loop handles `PlanApprovalRequestIn`. | The teammate was launched with plan approval required. |
| `live_tool_events` | Backend emits tool/progress events before terminal output. | Long work needs operational visibility. |
| `turn_steer` | Backend accepts steer text mid-turn or next-boundary. | A stale path or new constraint must be corrected. |
| `accepts_peer_steer` | Non-lead peers may send `kind="steer"`. | After querying the recipient manifest and seeing peer authorization. |
| `permission_bridge` | Backend approval prompts bridge to team-lead. | Sensitive operations need explicit approval. |
| `trust_modes` | Backend supports trusted/default/plan execution modes. | Work should run in a lower-trust or approval-gated mode. |
| `thread_fork` | Backend can fork persisted context, currently Codex App Server. | Follow-up tasks need substantial prior context. |
| `native_skills` | Backend discovers/uses native skills. | Workflow-rich tasks benefit from backend-native skills. |
| `large_context` | Backend can handle very large prompts/repos. | Broad audits or multi-document synthesis are needed. |
| `soft_non_progress_watchdog` | Adapter can detect stalled in-flight turns. | Long turns need durable no-progress warnings. |

The "teach" half of §1 lives in manifest fields: `description`, `when_to_use`, `when_not_to`, `failure_modes`, plus steer-specific `delivery_mode`, `expiry_semantics`, and `authorization`. `peer_prompt_fragment()` includes those fields in peer prompts. If peers cannot tell when to invoke a primitive, the capability layer is incomplete.

Document concrete failure modes, not vague caveats. Existing examples include:

- `STEER_BUFFERED_NEXT_BOUNDARY`, `STEER_AUTH_REJECTED`, `STEER_PAYLOAD_OVERFLOW`;
- `CLI_BINARY_MISSING`, `HEADLESS_FLAG_UNSUPPORTED`, `MACHINE_OUTPUT_PARSE_FAILED`, `TURN_TIMEOUT`;
- `SESSION_ID_MISSING`, `SESSION_NOT_FOUND`, `RESUME_UNSUPPORTED_FLAG_COMBINATION`;
- `SCHEMA_VALIDATION_FAILED`, `OUTPUT_SCHEMA_UNSUPPORTED`, `RETRY_EXHAUSTED`;
- `SKILL_DISCOVERY_CHANGED_UPSTREAM`, `SKILL_NOT_AVAILABLE_TO_ROOT_AGENT`.

For GLM/Qwen/DeepSeek, add backend-specific modes when useful, such as `GLM_AUTH_CACHE_MISSING` or `QWEN_TOOL_EVENT_SHAPE_CHANGED`. Do not advertise `turn_steer`, `live_tool_events`, `large_context`, or `native_skills` until code and tests prove the behavior.

## 4. Visibility integration

Visibility parity means the lead should not need tmux stderr to know what a routed teammate did.

### Structured event stream: Codex/Gemini pattern

Use this when the backend emits structured tool/progress events.

References:

- `src/claude_anyteam/codex.py::_visibility_for_app_server_item()` normalizes Codex App Server items into `tool_event`, `artifact_event`, `turn_progress`, and `turn_warning` envelopes while preserving raw backend type/preview.
- `src/claude_anyteam/backends/gemini/acp.py::_normalize_tool_events()` turns ACP `session/update` notifications into normalized `tool_use` / `tool_result` records.
- `src/claude_anyteam/headless_visibility.py` writes common `turn_started`, `tool_event`, `turn_completed`, and `turn_failed` events.

For a new structured backend, preserve native data in `raw_backend_type` and `raw_event_preview`; normalize only outer fields such as `category`, `tool_name`, `phase`, `target`, `status`, `exit_code`, and `duration_ms`. Failed tool events that require intervention should set mailbox/task-state visibility.

### Captured stdout/stderr plus parsing: Kimi pattern

Use this when the CLI has no live event stream but headless output is parseable. Kimi's `invoke.py`:

- runs `kimi --print --output-format=stream-json` with captured stdout/stderr;
- parses stdout line-by-line in `_parse_stdout()`;
- treats `assistant.tool_calls[]` as tool-call observations;
- extracts a session id from stderr with `_extract_session_id()`;
- calls `HeadlessTurnVisibility.start(...)` before invocation;
- calls `visibility.terminal(..., events=..., tool_call_event_source="kimi assistant.tool_calls[]")` on timeout and normal exit.

`HeadlessTurnVisibility.terminal()` emits best-effort `tool_event` envelopes and always records a terminal event with raw backend data. If your backend writes important information only to stderr, capture or tee stderr and parse/preserve it; do not leave auth, rate-limit, or session diagnostics only in a pane.

### Startup/crash visibility

Mirror Kimi's `loop.py` startup hygiene:

- preflight before registration;
- classify `AuthPreflightFailure` when possible;
- emit `pio.emit_auth_preflight_failure(...)` or `pio.emit_adapter_startup_crash(...)`;
- record `diagnostics.record_incident(...)`.

A backend that dies before its first inbox poll still needs a durable visibility artifact.

## 5. Test coverage requirements

Minimum test coverage:

1. **Capabilities**: extend `tests/test_capability_declarations.py` and `tests/test_capability_validation.py` for the new backend constant, `_backend_metadata()`, rich manifest fields, runtime hooks, and manifest schema validity.
2. **Spawn routing**: extend `tests/test_spawn_shim.py` for `<name>-` route hits, env overrides, malformed regex, conflicts, `--model`, `--effort`, and `--plan-mode` forwarding.
3. **Invocation**: add `tests/test_<name>_invocation_shape.py` and parser tests for argv construction, schema embedding, isolated HOME/MCP config, model/effort mapping, success, nonzero exit, timeout, schema failure, and session capture.
4. **Loop behavior**: cover registration, task claim/completion, blocked tasks, plan approval, shutdown, prose DMs, capability-manifest updates, and any steer behavior.
5. **Visibility**: cover `turn_started`, `tool_event`, terminal success/failure, timeout, auth preflight failure, and startup crash events.
6. **Stress**: update `tools/stress/run_scenario.py`, add homogeneous/mixed/pair scenarios, and extend `tests/stress/test_run_scenario.py`.
7. **Install/docs**: if local auth or binary install is required, add installer and error-translator tests; update skill/plugin tests that assert supported prefixes.

Useful Kimi references: `tests/test_kimi_invocation_shape.py`, `tests/test_kimi_invoke.py`, `tests/test_kimi_registration.py`, `tests/test_kimi_plan_approval.py`, `tests/test_kimi_loop_session_policy.py`, `tests/test_kimi_mcp_config.py`, and the Kimi cases in `tests/stress/test_run_scenario.py`.

## 6. Worked example: hypothetical `glm-*` addition

Suppose GLM has a CLI binary `glm`, a headless mode, optional `--session`, and JSONL output with `tool_call` events. The implementation would proceed like this, using Kimi files as the concrete reference.

### Step 1: backend files

Create `src/claude_anyteam/backends/glm/` with the Kimi package shape:

```text
__init__.py       # like Kimi's one-line docstring
config.py         # env/default validation, like kimi/config.py
cli.py            # argparse -> GlmSettings -> loop.run(), like kimi/cli.py
invoke.py         # native CLI boundary, like kimi/invoke.py
loop.py           # team protocol loop, like kimi/loop.py
prompts.py        # GLM tool names, like kimi/prompts.py
```

Rename consciously: `KimiSettings` -> `GlmSettings`, `default_kimi_home()` -> `default_glm_home()`, `--kimi-binary` -> `--glm-binary`, `kimi_headless` -> `glm_headless`. Replace Kimi's auth/session/output parsing with probed GLM behavior.

### Step 2: invocation skeleton

```python
def feature_test(glm_binary: str = "glm") -> None:
    if not shutil.which(glm_binary):
        raise RuntimeError("glm binary not found on PATH")
    # Probe --help/--version and assert headless flags exist.
```

```python
def run(prompt: str, *, cwd: Path, schema: Path | None = None, wrapper_identity=None, ...):
    team, agent = wrapper_identity or ("default", "glm")
    visibility = HeadlessTurnVisibility.start(
        team=team,
        agent=agent,
        backend="glm_headless",
        enabled=wrapper_identity is not None,
        cwd=cwd,
        schema=schema,
        timeout_s=timeout_s,
        model=model,
        effort=effort,
        resume_session_id=resume_session_id,
        task_id=task_id,
    )
    # subprocess.run([...])
    # parse stdout/stderr, validate schema, emit visibility.terminal(...)
    # return CodexResult(...)
```

Keep `identity_env()` and wrapper MCP config behavior so GLM can call anyteam protocol tools as the correct teammate.

### Step 3: loop metadata

In `glm/loop.py`, mirror Kimi's `_backend_metadata()`:

```python
def _backend_metadata(settings: GlmSettings) -> BackendMetadata:
    capabilities = assert_known_capabilities(GLM_HEADLESS_CAPABILITIES)
    return BackendMetadata(
        model="glm-cli",
        prompt="GLM teammate adapter. Protocol I/O is handled by the adapter; coding work is delegated to GLM CLI headless mode. No Claude LLM is involved.",
        capabilities=capabilities,
        capability_manifest=rich_capability_manifest(capabilities, host_tool_surface="glm-native"),
        transport="glm-headless",
        host_tool_surface="glm-native",
        coupling_regime="loose",
    )
```

If GLM only accepts next-turn steer, either document that in `turn_steer` with a truthful `delivery_mode`, or do what Kimi currently does and do **not** declare `turn_steer` until peers can rely on it.

### Step 4: capabilities and prompts

In `capabilities.py`:

```python
GLM_HEADLESS_CAPABILITIES = [
    "headless_invocation",
    "session_resume",
    "structured_output",
    "plan_mode",
]
```

Add `native_skills`, `large_context`, `live_tool_events`, or `accepts_peer_steer` only after runtime hooks, tests, and manifest guidance prove them. Then ensure `prompts.py` includes peer fragments so GLM sees what Codex/Gemini/Kimi peers can uniquely do.

### Step 5: spawn and stress

In `spawn_shim.py`, mirror Kimi with `DEFAULT_GLM_MATCH`, `GLM_BINARY`, `_glm_route()`, and a dispatch branch. In `tools/stress/run_scenario.py`, mirror Kimi's `_command_for_member()` branch:

```python
if agent_type == "glm":
    cmd = [*base, "claude_anyteam.backends.glm.cli", "--team", team_name, "--name", name, "--cwd", cwd]
    cmd += ["--glm-binary", _resolve_backend_binary("glm")]
    if member.get("model"):
        cmd += ["--model", str(member["model"])]
    if member.get("effort"):
        cmd += ["--effort", str(member["effort"])]
    cmd += ["--backend", str(member.get("transport", "headless"))]
    return cmd
```

Add homogeneous, mixed, and paired GLM scenarios, then run focused tests:

```bash
pytest tests/test_capability_declarations.py tests/test_capability_validation.py
pytest tests/test_spawn_shim.py
pytest tests/test_glm_invocation_shape.py tests/test_glm_invoke.py
pytest tests/stress/test_run_scenario.py
```

## 7. Common pitfalls

- Declaring capabilities before they are backed by code and tests.
- Flattening backend-native tool/event shapes into Codex/Gemini/Kimi shapes.
- Sharing mutable auth state across teammates instead of copying into an adapter-owned HOME.
- Forgetting peer guidance in the rich manifest.
- Adding backend code without the console script, spawn route, installer binary, and docs that make it launchable.
- Treating final prose as a peer DM; use `send_message`.
- Leaving startup/auth failures visible only in stderr.
