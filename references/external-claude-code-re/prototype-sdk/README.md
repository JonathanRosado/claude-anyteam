# Agent Teams Kit Prototype SDK

Research artifact for `docs/internal/protocol-rev/08-elegance-and-gaps.md` §6. This is a concrete, runnable sketch of the proposed Agent Teams kit SDK under `references/`; it is **not** production code and is **not** integrated with `/src/claude_anyteam`.

## What this demonstrates

- A structural two-layer split:
  - `Teammate` owns transport defaults: argv parsing, idempotent registration, inbox polling, atomic task claim, idle/shutdown lifecycle, task completion, and visibility event fan-out.
  - subclasses own capability-layer declarations through `agent_card()`, `capabilities()`, and optional harness work methods.
- Rich capability manifests with semantic guidance: every capability entry carries `version`, `schema`, `description`, `when_to_use`, `when_not_to`, and `failure_modes`.
- Cheap roster discovery: `config.json` member rows receive a derived flat `capabilities` list; rich Agent Cards stay available via kit methods.
- Host-agnostic storage: `TeamStorage` is a `Protocol`; `FilesystemStorage` is the default implementation for the current `~/.claude/teams/<team>` / `~/.claude/tasks/<team>` shape.
- B9 §6 / v2 visibility events as first-class primitives: `emit_tool_event`, `emit_artifact_event`, `emit_turn_progress`, `emit_turn_completed`, `emit_turn_failed`, and `emit_visibility_degraded` fan out to stderr, event log, mailbox, and task state according to prototype policy.
- Peer-efficiency primitives: typed `kind` payloads, `Team.find_capability()`, `Team.broadcast_capability_manifest()`, peer manifest caches, and the `accepts_peer_steer` capability flag.

## What this does not do

- It is not production-ready and does not replace `src/claude_anyteam`.
- It does not expose the wrapper MCP tool described in 08 §6.4.1; `Team.capability_manifest()` reads cached Agent Cards from config instead.
- It does not implement a full plan-approval UI loop; `plan_mode_required` is registered and carried as plumbing only.
- It does not fully solve event-log rotation, stale-lock diagnostics, or host TUI rendering.
- It uses a tiny stdlib fallback for `filelock` only so this prototype can keep the requested dependency budget; production should use the substrate's real lock dependency.

## Layout

```text
agent_teams_kit/   # SDK prototype
examples/          # echo, mock GLM, mock DeepSeek adapters
tests/             # pytest coverage over tmp_path-backed teams
```

## Run tests

From this directory:

```bash
uv run pytest
```

Last verified result in this workspace:

```text
12 passed
```

## Example adapters

```bash
uv run python examples/echo_adapter.py --help
uv run python examples/glm_adapter.py --help
uv run python examples/deepseek_adapter.py --help
```

They are mock harnesses: no GLM or DeepSeek CLI is invoked. Their value is the adapter shape and capability manifest authoring.

## Empirical LOC comparison

Measured with `wc -l` after implementation:

| Area | LOC |
| --- | ---: |
| `agent_teams_kit/*.py` | 992 |
| `examples/echo_adapter.py` | 48 |
| `examples/glm_adapter.py` | 139 |
| `examples/deepseek_adapter.py` | 130 |
| all examples | 317 |
| tests | 305 |

The requested comparison path `src/claude_anyteam/backends/codex/` does not exist in this checkout (0 files). The closest current Codex-related flat implementation files are:

```text
src/claude_anyteam/codex.py           936
src/claude_anyteam/app_server.py      234
src/claude_anyteam/loop.py            899
src/claude_anyteam/protocol_io.py     336
src/claude_anyteam/registration.py    247
src/claude_anyteam/wrapper_server.py  611
src/claude_anyteam/messages.py        193
TOTAL                                3456
```

## Deviations from 08 §6 and rationale

- The kit is ~1k LOC, not 200. 08 §6.7 itself notes the 200-line goal is for adapter files; the reusable kit lives once and is closer to 800+ LOC.
- `FilesystemStorage` delegates to `claude_teams` for team creation and task read/list/update, but inlines config updates, inbox reads, stable `messageId`, event logs, and task claim CAS because the current substrate does not yet preserve the v2 capability/event extras or expose a single CAS claim primitive.
- `Team.broadcast_capability_manifest()` stores `peerManifestCache` in config as a research stand-in. Production should likely keep this in process memory and expose rich manifest fetch through wrapper MCP.
