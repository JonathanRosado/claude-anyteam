# B1/B5 triage: packaging schemas and respawn slots

Owner: `codex-packaging`  
Date: 2026-04-26  
Scope read: `MEMORY.md` first; then `pyproject.toml`, `src/claude_anyteam/codex.py`, `installer.py`, `loop.py`, `schema_validation.py`, `team_cli.py`, `registration.py`, `spawn_shim.py`, and vendored `src/claude_teams/{spawner,teams,server,models,tmux_introspection}.py` in full or relevant full modules called out by the bug brief. I did not run the test suite.

## 1. B1 review — schema path/package bug

### Confirmed failure mode

`src/claude_anyteam/codex.py:47-49` computes schemas as:

```python
SCHEMAS_DIR = Path(__file__).resolve().parent.parent.parent / "schemas"
TASK_COMPLETE_SCHEMA = SCHEMAS_DIR / "task-complete.schema.json"
PLAN_SCHEMA = SCHEMAS_DIR / "plan.schema.json"
```

In the source tree this accidentally works because `src/claude_anyteam/codex.py -> parent.parent.parent == repo root`, so `repo/schemas` exists.

In the installed uv-tool layout, `__file__` is:

```text
/home/rosado/.local/share/uv/tools/claude-anyteam/lib/python3.12/site-packages/claude_anyteam/codex.py
```

so `parent.parent.parent` resolves to:

```text
/home/rosado/.local/share/uv/tools/claude-anyteam/lib/python3.12
```

and the adapter looks for:

```text
/home/rosado/.local/share/uv/tools/claude-anyteam/lib/python3.12/schemas/{task-complete,plan}.schema.json
```

That is not a package-owned location. The installed `claude_anyteam-0.5.0.dist-info/RECORD` contains `claude_anyteam/codex.py` but no schema entries. On this host, `/home/rosado/.local/share/uv/tools/claude-anyteam/lib/python3.12/schemas/` currently exists with the four schema files, but because RECORD does not own them and there is no `claude_anyteam/schemas/`, that looks like the manual workaround, not a durable wheel artifact.

### Sibling instances

`rg "parent\.parent\.parent" .` found exactly one runtime instance:

```text
src/claude_anyteam/codex.py:47
```

Other schema uses are downstream of those constants:

- `src/claude_anyteam/loop.py:444` uses `codex_mod.PLAN_SCHEMA` for plan generation.
- `src/claude_anyteam/loop.py:653`, `742`, `769` use `codex_mod.TASK_COMPLETE_SCHEMA` for resume validation, fresh Codex exec, and App Server schema loading.
- `src/claude_anyteam/backends/gemini/{invoke.py,acp.py}` import `PLAN_SCHEMA` and `TASK_COMPLETE_SCHEMA` from `claude_anyteam.codex`.
- `src/claude_anyteam/backends/kimi/invoke.py` imports the same constants.
- `src/claude_anyteam/schema_validation.py` opens whichever path it is handed.

So the flawed path assumption is centralized in `codex.py`, but it blocks Codex, Gemini, and Kimi because Gemini/Kimi reuse the shared schema constants and Python validation path.

### Packaging state

`pyproject.toml:45-46` currently has:

```toml
[tool.hatch.build.targets.wheel]
packages = ["src/claude_anyteam", "src/claude_teams"]
```

The root-level `schemas/` directory is outside both package directories. Hatchling will not include it in the wheel under the package from this config. The marketplace plugin copy at:

```text
~/.claude/plugins/marketplaces/claude-anyteam/schemas/
```

is useful as a workaround source, but it is not a Python package resource and not guaranteed to exist for PyPI/uv-tool installs.

### Other runtime files with the same risk

I did not find other non-Python runtime assets under `src/`; `find src -type f ! -name '*.py'` returns no files. Prompt/template material is Python code (`prompts.py` and backend `prompts.py` modules). Docs, skills, assets, hooks, and npm files are outside the wheel but are not imported by the Python runtime in the task execution path.

The four schema files are the only root-level data files with current or near-future runtime relevance:

- `schemas/task-complete.schema.json` — used now by all backends.
- `schemas/plan.schema.json` — used now by all plan paths.
- `schemas/permission_request.schema.json` and `schemas/permission_response.schema.json` — not in the current call sites I found, but should be bundled with the same mechanism to avoid a second packaging bug when permission schemas become runtime inputs.

## 2. B1 critique — package resource vs installer copy

Both user-suggested fixes can make the current machine work, but only bundling schemas inside the Python package is durable.

### Why package resources are the durable fix

- **Correct ownership boundary:** `TASK_COMPLETE_SCHEMA` is code-level runtime data. The wheel that ships `claude_anyteam.codex` should also ship the schema that `codex.py` references.
- **Works for every install path:** uv tool, pipx, venv, editable installs, CI, and PyPI all get the same files. Installer copy only helps users who run `claude-anyteam install`, and only after the copy succeeds.
- **Avoids environment-specific paths:** copying to `lib/python3.12/schemas` depends on the current interpreter layout and the current mistaken `parent.parent.parent` calculation. It bakes in the bug instead of removing it.
- **Avoids hidden plugin coupling:** falling back to `~/.claude/plugins/marketplaces/claude-anyteam/schemas` assumes the Claude plugin marketplace exists and is in sync with the Python package. PyPI/uv users may not have that tree.
- **Testable at import/package time:** `importlib.resources.files("claude_anyteam.schemas")` can be asserted in unit tests and wheel-inspection CI.

### Installer copy as fallback only

An installer self-check is still valuable, but not as the primary fix. `claude-anyteam install` should detect missing packaged schemas and fail/warn with a clear action, e.g. “schemas missing from installed claude-anyteam package; reinstall/upgrade.” It should not silently copy data from the plugin marketplace into the Python lib directory as the normal path.

### Backwards compatibility for existing v0.5.0 installs

Existing v0.5.0 uv-tool installs will not self-heal until upgraded/reinstalled. For a stuck user, the manual workaround is to copy the marketplace schemas into the path that v0.5.0 incorrectly resolves, e.g.:

```text
~/.local/share/uv/tools/claude-anyteam/lib/python3.12/schemas/
```

That workaround matches what appears to have happened on this host, but it should be documented as temporary. A fixed release should use package resources and stop depending on that external directory.

## 3. B1 architect — concrete fix plan

### File layout

Add package data under `src/claude_anyteam`:

```text
src/claude_anyteam/schemas/__init__.py
src/claude_anyteam/schemas/task-complete.schema.json
src/claude_anyteam/schemas/plan.schema.json
src/claude_anyteam/schemas/permission_request.schema.json
src/claude_anyteam/schemas/permission_response.schema.json
```

Keep root `schemas/` for one release if the marketplace/plugin flow still expects it, but add a drift test so the root copies and package copies cannot diverge. Long term, make the package path the source of truth and have the marketplace/release copy derive from it.

### `pyproject.toml`

Because the schemas would live under `src/claude_anyteam`, the existing Hatch wheel package list should include them when they are tracked files. To make the intent explicit and guard against future Hatch config changes, add an explicit artifact/include stanza if desired:

```toml
[tool.hatch.build.targets.wheel]
packages = ["src/claude_anyteam", "src/claude_teams"]
artifacts = ["src/claude_anyteam/schemas/*.json"]
```

If Hatchling’s artifact semantics are considered too broad for tracked files, a wheel smoke test is enough; the key is that the files live under the package directory.

### `src/claude_anyteam/codex.py`

Replace `parent.parent.parent` with an import-resources idiom. Because `codex exec --output-schema` needs a real filesystem path, use `as_file()` at subprocess invocation time rather than assuming resources are always normal files.

Sketch:

```python
from contextlib import ExitStack
from importlib import resources
from importlib.resources.abc import Traversable

SCHEMA_PACKAGE = "claude_anyteam.schemas"


def schema_resource(filename: str) -> Traversable:
    return resources.files(SCHEMA_PACKAGE).joinpath(filename)


TASK_COMPLETE_SCHEMA = schema_resource("task-complete.schema.json")
PLAN_SCHEMA = schema_resource("plan.schema.json")
```

Then widen `run()` from `schema: Path | None` to `schema: Path | Traversable | None`, and materialize the schema only while the subprocess runs:

```python
with ExitStack() as stack:
    schema_arg: Path | None = None
    if schema is not None:
        schema_arg = stack.enter_context(resources.as_file(schema))
    ...
    if schema_arg is not None:
        args += ["--output-schema", str(schema_arg)]
    proc = subprocess.run(...)
```

This keeps zip-safe/resource-safe semantics while still giving Codex CLI a path.

### `src/claude_anyteam/schema_validation.py`

Change `load_schema(path: Path)` to accept a `Traversable` as well:

```python
from importlib.resources.abc import Traversable

SchemaSource = Path | Traversable


def load_schema(path: SchemaSource) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return json.load(f)
```

`pathlib.Path` also has `.open()`, so existing tests that patch schema constants to temp paths keep working.

### `src/claude_anyteam/loop.py`

At `loop.py:769`, replace the direct `open(codex_mod.TASK_COMPLETE_SCHEMA)` with the shared helper:

```python
from . import schema_validation as _sv
schema = _sv.load_schema(codex_mod.TASK_COMPLETE_SCHEMA)
```

The resume path already calls `_sv.load_schema(codex_mod.TASK_COMPLETE_SCHEMA)`, so it benefits from the helper change.

### Gemini/Kimi call sites

`src/claude_anyteam/backends/gemini/{invoke.py,acp.py}` and `src/claude_anyteam/backends/kimi/invoke.py` import the schema constants and call `load_schema(schema)`. They should not need path-specific changes once `load_schema()` accepts `Traversable`; update type hints if they currently say `Path`.

### Installer diagnostic

Add a package asset check near `installer.install()` before declaring success:

```python
def _collect_runtime_asset_diagnostics() -> tuple[InstallError, ...]:
    missing = [name for name in (...) if not codex.schema_resource(name).is_file()]
    if missing:
        return (InstallError(
            title="claude-anyteam package is missing JSON schemas",
            explanation="The installed package lacks schema files required by codex-*, gemini-*, and kimi-* teammates.",
            action="Upgrade/reinstall claude-anyteam; temporary workaround: copy schemas from the plugin marketplace into the old resolved path.",
            severity="blocker",
            details=", ".join(missing),
        ),)
    return ()
```

Whether this is hard-blocking or attached as diagnostics is a product choice. Since a missing schema is a first-task hard failure for all routed backends, I would make it a hard blocker when any provider is ready.

### Smoke test that fails today and passes after

Add `tests/test_packaged_schemas.py`:

```python
from importlib import resources


def test_runtime_schemas_are_package_resources():
    schema_dir = resources.files("claude_anyteam").joinpath("schemas")
    for name in (
        "task-complete.schema.json",
        "plan.schema.json",
        "permission_request.schema.json",
        "permission_response.schema.json",
    ):
        assert schema_dir.joinpath(name).is_file(), name
```

This fails today because `src/claude_anyteam/schemas/` does not exist. It passes after bundling.

Add a release/wheel smoke check in CI or release notes:

```bash
uv build --wheel
python -m zipfile -l dist/claude_anyteam-*.whl | grep 'claude_anyteam/schemas/task-complete.schema.json'
```

No backend CLI invocation is required for this smoke.

## 4. B5 review — who owns slot naming?

### Current repo behavior

I found no auto-`-2`/`-3` disambiguation in `claude_anyteam` or vendored `claude_teams`.

- `src/claude_anyteam/spawn_shim.py` parses `--agent-name` and passes that exact value to `claude-anyteam --name ...`. It does not canonicalize, de-suffix, or allocate names.
- `src/claude_anyteam/registration.py:96-165` is idempotent only for the exact `settings.agent_name`. If that exact name exists in `config.json`, it reuses the existing row and does not append a duplicate. If the host already changed the name to `codex-research-2`, registration treats that as a different identity.
- `src/claude_teams/teams.py:158-164` explicitly rejects duplicate member names with `ValueError`; it does not suffix.
- `src/claude_teams/spawner.py:126-244` builds a `TeammateMember` with the requested `name` and calls `teams.add_member`; again, no suffix path.
- `src/claude_teams/server.py:413-461` exposes `spawn_teammate` and propagates `ValueError` as a tool error. It does not rewrite names.

Therefore the observed `codex-research-2` / `codex-research-3` behavior is almost certainly owned by the Claude Code host/Agent-tool spawn path before `$CLAUDE_CODE_TEAMMATE_COMMAND` invokes our shim. The host likely sees an existing member/task identity for `codex-research`, chooses a unique display/agent name, and passes the already-disambiguated name to the shim.

### Teardown/reuse paths that already exist

There are three cleanup paths, with different owners:

1. **Adapter graceful shutdown:** `loop._handle_shutdown()` approves a shutdown when idle, sets `approved_shutdown=True`, and `loop.run()` then calls `registration.deregister()`. `registration.deregister()` removes the exact member row and inbox file.
2. **Vendored server graceful cleanup:** `src/claude_teams/server.py:870-886` has `process_shutdown_approved()`, which removes the teammate and resets its tasks after the lead sees `shutdown_approved`.
3. **Vendored server force cleanup:** `src/claude_teams/server.py:847-867` has `force_kill_teammate()`, which kills the tmux target, removes the teammate, and resets tasks when graceful shutdown is impossible.

If the old exact-name row is gone before spawning, the host should have no reason to suffix. If the old row remains because the agent crashed or never processed shutdown, the host suffixing is expected from its “names must be unique” perspective.

### Options

- **Upstream/host fix:** Ask Claude Code/Agent tool to support “replace existing after acknowledged shutdown” or “reuse exact name when previous task state is completed/dead.” This is the real fix for the observed auto-suffix path.
- **Vendored `claude_teams` patch:** Useful only for `spawn_teammate` MCP-server users. It would not affect Claude Code’s built-in Agent tool if the suffixing happens before our shim.
- **`spawn_shim` monkey-patch/canonicalization:** Not recommended. Stripping `-2` or rewriting `codex-research-2` to `codex-research` can collide with a live old process, corrupt inbox ownership, and misroute tasks. The shim lacks enough context to know user intent.
- **Doc/CLI workaround:** Document a pre-spawn cleanup workflow and improve `team-roster` diagnostics so stale rows are obvious.

## 5. B5 critique — slot reuse vs `--replace-existing`

### Slot reuse after acknowledged shutdown

This causes fewer surprises. If the prior agent has approved shutdown and its row has been removed (by adapter deregistration or `process_shutdown_approved`), spawning the same name should reuse the human-visible slot naturally. It preserves identity continuity without surprising live processes.

The main failure mode is incomplete cleanup: the old process approves shutdown but the host/lead does not remove the row, or the adapter crashes before deregistering. That is a diagnostics/cleanup problem, not a reason to silently alias names.

### `--replace-existing`

A flag is safer than implicit replacement only if it is exact-name and explicit. It should:

- refuse to replace `team-lead`;
- check whether the existing row has a live tmux target before removing;
- require a `--force`-style acknowledgement if live/unresponsive;
- reset tasks owned by that exact agent;
- preserve/merge per-agent config if appropriate.

However, `--replace-existing` on `claude-anyteam --name codex-research` mainly helps manual adapter launches. It does not fix Agent-tool auto-suffix if Claude Code has already renamed the spawn to `codex-research-2` before invoking the shim.

### Old agent alive but unresponsive

Do not reuse implicitly. Two processes with the same mailbox identity are worse than a `-2` suffix: they can both read the same inbox, both claim/reset tasks under the same owner, and both send messages as the same name. The correct recovery path is explicit force cleanup (`force_kill_teammate` / future `claude-anyteam team-remove --force`) followed by a fresh spawn with the original name.

## 6. B5 architect — concrete recommendation

### Recommendation: doc + diagnostics now; host/upstream for true naming reuse

Because the observed suffixing is not in this repo’s spawn code, the immediate fix should be documented workflow plus better local diagnostics, not a hidden name rewrite.

#### Documentation to add

Add a “Reusing a teammate name” note to README/docs:

```md
To restart a codex-*/gemini-*/kimi-* teammate with the same name:

1. Ask it to shut down gracefully.
2. Wait for shutdown approval / adapter deregistration.
3. Run `claude-anyteam team-roster --team <team>` and confirm the old name is gone.
4. If the old row is still present and the process is dead, remove it via the lead lifecycle tool (`force_kill_teammate`) or a future `claude-anyteam team-remove` command.
5. Spawn the teammate with the original name. If you spawn while the stale row exists, Claude Code may create `<name>-2` instead.
```

#### CLI diagnostic to add

Extend `team-roster` rather than changing spawn identity:

- Add fields to `_RosterRow`: `tmux_pane_id`, `alive`, `error`, maybe `base_name`.
- Optional flag: `claude-anyteam team-roster --team T --check-alive`.
- Use vendored `tmux_introspection.resolve_pane_target()` / `peek_pane()` for rows with a tmux target.
- Flag likely stale routed teammates: `codex-*`, `gemini-*`, `kimi-*` with `alive=false`, no `tmuxPaneId`, or names that look like suffix siblings (`codex-research`, `codex-research-2`).

This directly answers “why did I get `-2`?” at the surface the user can inspect.

#### Optional future command

Add an explicit cleanup command, not an implicit spawn rewrite:

```bash
claude-anyteam team-remove codex-research --team anyteam-bug-triage --reset-tasks --force-if-dead
```

Internally it should mirror `force_kill_teammate()` / `process_shutdown_approved()` semantics: kill target if present, remove the member row, remove/leave inbox per policy, and reset tasks owned by that name. This helps non-MCP/manual recovery but still requires explicit intent.

#### Upstream ask

For Claude Code/Agent-tool naming itself, file an upstream/host request: “When spawning an Agent with the same requested name, if the prior out-of-process teammate has acknowledged shutdown or is completed/removed, reuse the requested slot instead of auto-suffixing. If the old teammate still exists, fail with an actionable cleanup message or require an explicit replace flag.”

## 7. Diagnostic surface

### B1 diagnostics that would have made the issue obvious

- `claude-anyteam install` should verify package schemas and print/fail:
  - `claude-anyteam package is missing schemas: task-complete.schema.json, plan.schema.json`
  - `codex-*, gemini-*, and kimi-* teammates will fail on first structured output`
  - `upgrade/reinstall claude-anyteam; temporary workaround: copy schemas from plugin marketplace to the legacy resolved path`
- Adapter startup `feature_test()` should also validate `TASK_COMPLETE_SCHEMA` and `PLAN_SCHEMA` before running Codex/Gemini/Kimi, so the log shows a local packaging error instead of a downstream `codex exec --output-schema` failure.
- `logger.info("codex.invoke", schema=...)` already logs the schema path; surfacing `schema_exists=false` before subprocess launch would have shortened diagnosis.

### B5 diagnostics that would have made the issue obvious

- `team-roster` should show liveness/staleness, not just static config fields. Current rows are `name`, `agent_type`, `model`, `backend_type`, and `color`; they do not tell the lead whether the row is dead.
- `team-roster` should group suffix siblings: e.g. `codex-research`, `codex-research-2`, `codex-research-3` and mark “possible stale previous slots.”
- Spawn failure/suffix documentation should explicitly say: “If a stale member row exists, Claude Code may choose a suffixed name. Clean up the old row first to reuse the original name.”
- If we add `team-remove`, make it print the tasks reset and inbox removed/preserved so the recovery is auditable.

## 8. Productivity lens — Codex/Gemini drag vs Claude

### B1 drag

B1 is a hard install/runtime blocker for non-Claude teammates:

- Codex fresh task path needs `--output-schema task-complete.schema.json` before it can complete a task.
- Codex plan path needs `plan.schema.json`.
- Codex resume/App Server and Gemini/Kimi headless paths validate output in Python using the same schema files.

When schemas are missing, a non-Claude teammate can spawn and then fail on first real task/plan completion. That costs at least one spawn cycle plus diagnosis/reinstall/workaround time. Claude in-process teammates do not hit this because their structured team behavior is inside Claude Code; they do not depend on these external JSON schema files being packaged in our wheel.

### B5 drag

B5 is lower severity but recurring friction during recovery:

- A respawn that becomes `codex-research-2` loses the original expected identity.
- Existing tasks, per-agent config files, inboxes, and human mental model often still point at `codex-research`.
- The lead has to patch/retarget messages and may need to clean several stale rows.

For Claude in-process teammates, the host owns the full lifecycle and in-memory task state, so teardown/reuse tends to be coherent. External Codex/Gemini/Kimi teammates split lifecycle across Claude Code host state, config files, tmux/processes, and our adapter registration, so stale rows are much easier to produce and harder to diagnose.

### Relative severity

- **B1:** high, simple root cause, durable package-resource fix required before non-Claude path feels first-class.
- **B5:** low-to-medium, deeper ownership boundary. The real suffixing behavior is host-side; our best near-term contribution is cleanup workflow and diagnostics, not implicit replacement.
