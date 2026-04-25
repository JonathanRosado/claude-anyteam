# Configuration

claude-anyteam now supports two routed backend prefixes by default: `codex-*` for Codex and `gemini-*` for Gemini CLI. Shared settings such as `CLAUDE_ANYTEAM_TEAM`, `CLAUDE_ANYTEAM_NAME`, `CLAUDE_ANYTEAM_CWD`, `CLAUDE_ANYTEAM_MODEL`, and per-agent `model` config apply to both. Codex-only settings include `CODEX_BINARY`, `CLAUDE_ANYTEAM_APP_SERVER`, and `CLAUDE_ANYTEAM_EFFORT`. Gemini-only settings include `CLAUDE_ANYTEAM_GEMINI_BINARY`, `CLAUDE_ANYTEAM_GEMINI_HOME`, `CLAUDE_ANYTEAM_GEMINI_BACKEND`, `CLAUDE_ANYTEAM_GEMINI_EFFORT`, and `CLAUDE_ANYTEAM_GEMINI_TRUST`; Gemini effort is mapped through adapter-owned `modelConfigs.customAliases` when a Gemini model is configured.

Gemini teammates use adapter-owned Gemini config/session state and do not mutate the user's real `~/.gemini/settings.json`. See `docs/gemini-adapter-limitations.md` for auth and app-server parity notes.

# Configuration

All configuration is via CLI flags or environment variables. The CLI flags win when both are set.

## Adapter CLI flags

```bash
claude-anyteam \
  --team <team-name>        # required — matches ~/.claude/teams/<team>/ directory
  --name <agent-name>       # required — unique within the team
  --cwd <path>              # working directory for model invocations (default: current)
  --model <slug>            # e.g. gpt-5.5, gpt-5.4, gpt-5.3-codex (default: Codex's own default)
  --effort <level>          # low | medium | high | xhigh (default: Codex's default)
  --plan-mode               # opt into plan approval mode
  --no-app-server           # opt out of App Server mode (use fresh-exec instead)
  --poll-s <float>          # inbox poll interval in seconds (default: 1.5)
  --color <name>            # display color in peer DMs (default: cyan)
  --log <level>             # debug | info | warn | error (default: info)
```

## Codex models

The adapter passes `--model` through verbatim as `-c model="…"` (fresh-exec) or as the `model` JSON-RPC param (App Server). claude-anyteam does not keep its own allowlist — any slug Codex accepts works. The table below reflects OpenAI's current catalog at the time of writing; check Codex's own `/model` picker for the live list.

| Slug | Role | Effort values | Notes |
|---|---|---|---|
| `gpt-5.5` | Recommended default | `low`, `medium`, `high`, `xhigh` | Newest frontier model. Best for coding, tool use, long-horizon planning. |
| `gpt-5.4` | Flagship (reasoning) | `low`, `medium`, `high`, `xhigh` | General-purpose reasoning + agentic workflows. |
| `gpt-5.4-mini` | Fast, cheap | `low`, `medium`, `high`, `xhigh` | Snappy responses for small edits and shell work. |
| `gpt-5.3-codex` | Codex-tuned | `low`, `medium`, `high`, `xhigh` | Coding-specialist; strong on multi-module refactors. |
| `gpt-5.3-codex-spark` | Research preview | `low`, `medium`, `high`, `xhigh` | Text-only, optimized for tight iteration loops. |
| `gpt-5.2` | Legacy | `low`, `medium`, `high`, `xhigh` | Kept for reproducibility of older runs. |

`--effort` maps to Codex's `model_reasoning_effort` setting:

- `low` — tiny edits, quick file ops, simple shell tasks
- `medium` — day-to-day feature work, straightforward bug fixes
- `high` — multi-module refactors, migrations, gnarly debugging
- `xhigh` — large-scale refactors, security review, architectural decisions (slowest, highest cost)

When `--model` or `--effort` is unset the adapter emits no override and Codex falls back to the model's own default from `~/.codex/config.toml`. You can mix model and effort per teammate — a `codex-tester` at `gpt-5.5 xhigh` and a `codex-alice` at `gpt-5.4-mini medium` is a supported setup.

## Environment variables

Every flag has an equivalent env var:

| Variable | Equivalent flag |
|---|---|
| `CLAUDE_ANYTEAM_TEAM` | `--team` |
| `CLAUDE_ANYTEAM_NAME` | `--name` |
| `CLAUDE_ANYTEAM_CWD` | `--cwd` |
| `CLAUDE_ANYTEAM_MODEL` | `--model` |
| `CLAUDE_ANYTEAM_EFFORT` | `--effort` |
| `CLAUDE_ANYTEAM_PLAN_MODE` | `--plan-mode` (set to `true`) |
| `CLAUDE_ANYTEAM_APP_SERVER` | set `false` to match `--no-app-server` |
| `CLAUDE_ANYTEAM_POLL_S` | `--poll-s` |
| `CLAUDE_ANYTEAM_COLOR` | `--color` |
| `CLAUDE_ANYTEAM_LOG` | `--log` |
| `CODEX_BINARY` | path to the `codex` binary (default: `codex` on PATH) |
| `CLAUDE_ANYTEAM_GEMINI_BINARY` | path to the `gemini` CLI binary used by `gemini-anyteam` (default: `gemini` on PATH) |
| `CLAUDE_ANYTEAM_GEMINI_HOME` | adapter-owned Gemini home for isolated config/session state |
| `CLAUDE_ANYTEAM_GEMINI_BACKEND` | Gemini transport: `headless` (default) or `acp` |
| `CLAUDE_ANYTEAM_GEMINI_EFFORT` | Gemini thinking effort: `minimal`, `low`, `medium`, `high`, or `xhigh` |
| `CLAUDE_ANYTEAM_GEMINI_TRUST` | ACP trust policy: `trusted` (default/backward compatible), `default`, or `plan`; non-trusted modes cancel permission requests and block the task |

## Per-teammate configuration (shim path)

Claude Code's Agent Teams UI only passes name, team, and plan-mode to the spawn shim — it has no field for per-teammate model/effort. To bridge that gap, the shim looks up a per-agent config file at spawn time:

```
~/.claude/teams/<team>/agents/<agent-name>.json
```

Example:

```json
{
  "model": "gpt-5.5",
  "effort": "xhigh"
}
```

When the shim dispatches a `codex-*` or `gemini-*` teammate, it reads this file (if present) and appends `--model` / `--effort` to the adapter invocation. For Codex, the effect is identical to typing those flags on the command line — both App Server and fresh-exec modes pick them up through the shared `Settings` object. For Gemini, `--effort` maps through adapter-owned model aliases when supported by the selected Gemini model family.

Behavior:

- Missing file — no-op, teammate starts with env/default config.
- Malformed JSON or unreadable file — logs `spawn_shim.agent_config_error` to stderr and continues; teammate still starts.
- Unknown keys — ignored. Only `model` and `effort` are forwarded today; more keys may be added later.
- Native (`claude-*`) teammates — the file is not consulted; native dispatch is always pass-through.

Precedence (highest wins): per-agent config file → env vars (`CLAUDE_ANYTEAM_MODEL`, `CLAUDE_ANYTEAM_EFFORT` for Codex, `CLAUDE_ANYTEAM_GEMINI_EFFORT` / `CLAUDE_ANYTEAM_GEMINI_TRUST` for Gemini) → adapter defaults → backend CLI defaults.

## Shim configuration

| Variable | Purpose |
|---|---|
| `CLAUDE_CODE_TEAMMATE_COMMAND` | Set by the installer to the shim binary path. Claude Code reads this to route teammate spawns. |
| `CLAUDE_ANYTEAM_BINARY` | Set by the installer to the adapter binary path. The shim uses this to know where to dispatch `codex-*` spawns. |
| `CODEX_TEAMMATE_SHIM_MATCH` | Regex for agent names to route to the Codex adapter. Default `^codex-`. Override if you want a different convention. |
| `CODEX_TEAMMATE_NATIVE_CLAUDE` | Path to the native `claude` binary. Auto-detected; only set if the shim picks the wrong one. |

## Teammate display mode

Claude Code's `teammateMode` key (in `~/.claude.json`, not settings.json) controls how teammates are spawned:

| Value | Behavior |
|---|---|
| `"tmux"` | Spawn teammates as real subprocesses inside a tmux/psmux session. **Required for Codex teammates** — the pane backend is what triggers the TUI presence line registration and lets `claude-anyteam` run as a full process with its own Codex session. |
| `"auto"` | Choose based on environment. On a non-tmux terminal this typically falls back to in-process, which does not fire the shim → Codex teammates would never launch. |
| `"in-process"` | Spawn teammates as coroutines inside the main Claude Code process. Valid for native Claude teammates only — the shim is never invoked, so `codex-*` names cannot route to this adapter. |

### Installing a multiplexer

`claude-anyteam install` verifies a multiplexer is on PATH before writing any config; if missing, install fails loudly with no side effects.

| OS | Install command |
|---|---|
| Debian/Ubuntu | `sudo apt install tmux` |
| Fedora/RHEL | `sudo dnf install tmux` |
| Arch | `sudo pacman -S tmux` |
| macOS | `brew install tmux` |
| Windows | `winget install psmux` (also: `choco install psmux`, `scoop install psmux`) |

### Install flow

`claude-anyteam install` sets `teammateMode` to `"tmux"` as part of the install flow:

- Key absent → written with value `"tmux"`.
- Key already `"tmux"` → no change.
- Key is `"auto"` or `"in-process"` or anything else → installer prompts before overwriting. Pass `--assume-yes` / `-y` to auto-accept in scripted installs.

The installer records what it did in `~/.claude/plugins/data/claude-anyteam-claude-anyteam/install-state.json`. `claude-anyteam uninstall` reads the state file and either restores the previous value or removes the key entirely, depending on how the install left things.

### Viewing teammates

With `teammateMode: "tmux"` and Claude Code launched from a non-tmux shell, teammate panes live inside a detached tmux session on an isolated socket (`claude-swarm-<pid>`). Your terminal sees nothing new. Claude Code will show `View teammates: tmux -L claude-swarm-<pid> a` in your prompt banner so you can attach to observe teammates at any time.

### Inside-tmux caveat

If you launch Claude Code from inside a tmux session, teammates split your current tmux window. To get detached behavior, run `env -u TMUX claude` to strip `$TMUX` before launch.

## Plan mode

Launch with `--plan-mode` (or `CLAUDE_ANYTEAM_PLAN_MODE=true`) to register with `planModeRequired: true`. The adapter will then respond to inbound `plan_approval_request` messages by invoking Codex once with `--output-schema plan.schema.json` and replying with a structured plan.

```bash
setsid nohup claude-anyteam \
  --team my-team --name codex-planner \
  --cwd /path/to/workspace \
  --plan-mode \
  --model gpt-5.5 --effort high \
  </dev/null >/tmp/codex-planner.stdout 2>/tmp/codex-planner.stderr & disown
```

Two schema-validation failures in a row will mark the task `blocked`. No canned stub response is ever sent.

## Execution mode choice

| | App Server (default) | Fresh-exec (`--no-app-server`) |
|---|---|---|
| Mid-task `turn/steer` | ✅ | ❌ |
| Cross-task memory | ✅ `thread/fork` | ✅ `codex exec resume` |
| Startup cost per task | ~10-100ms (thread creation) | ~seconds (full Codex startup) |
| Debugging | Persistent session, richer logs | Simpler, one process per task |

App Server is the richer default. `--no-app-server` is useful if you specifically want the fresh-exec path for operational reasons.

## Sandbox

The adapter invokes Codex with `--dangerously-bypass-approvals-and-sandbox`. Rationale:

- The adapter is operator-run in the user's own trust envelope, same as when they run `codex exec` directly
- The wrapper MCP server (called as a subprocess by Codex) writes to `~/.claude/tasks/` and `~/.claude/teams/`, which are outside Codex's workspace sandbox. With the sandbox enabled, those writes silently fail
- Disabling the sandbox at invocation is cleaner than adding a sandbox bypass rule to Codex's config

Codex's own help text describes the bypass flag as "intended solely for running in environments that are externally sandboxed" — an operator-run adapter qualifies.

## Uninstall

```bash
claude-anyteam uninstall
```

Removes the two env keys from `~/.claude/settings.json`. Preserves everything else.

Or, to fully remove:

```bash
claude-anyteam uninstall
uv tool uninstall claude-anyteam
```
