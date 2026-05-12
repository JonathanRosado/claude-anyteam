# Configuration

claude-anyteam supports three routed backend prefixes by default: `codex-*` for Codex, `gemini-*` for Gemini CLI, and `kimi-*` for Kimi CLI. Shared settings such as `CLAUDE_ANYTEAM_TEAM`, `CLAUDE_ANYTEAM_NAME`, `CLAUDE_ANYTEAM_CWD`, `CLAUDE_ANYTEAM_MODEL`, and per-agent `model` config apply across backends. Codex-only settings include `CODEX_BINARY`, `CLAUDE_ANYTEAM_APP_SERVER`, and `CLAUDE_ANYTEAM_EFFORT`. Gemini-only settings include `CLAUDE_ANYTEAM_GEMINI_BINARY`, `CLAUDE_ANYTEAM_GEMINI_HOME`, `CLAUDE_ANYTEAM_GEMINI_BACKEND`, `CLAUDE_ANYTEAM_GEMINI_EFFORT`, `CLAUDE_ANYTEAM_GEMINI_TRUST`, and `CLAUDE_ANYTEAM_GEMINI_APPROVAL_TIMEOUT`; Gemini effort is mapped through adapter-owned `modelConfigs.customAliases` when a Gemini model is configured. Kimi-only settings include `CLAUDE_ANYTEAM_KIMI_BINARY`, `CLAUDE_ANYTEAM_KIMI_HOME`, `CLAUDE_ANYTEAM_KIMI_BACKEND`, and `CLAUDE_ANYTEAM_KIMI_THINKING`; Kimi effort maps coarsely to thinking on/off.

Gemini and Kimi teammates use adapter-owned config/session state and do not mutate the user's real `~/.gemini/settings.json` or `~/.kimi/mcp.json`. See `docs/gemini-adapter-limitations.md` for Gemini auth and app-server parity notes.

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

## Kimi models and thinking

Kimi model slugs use provider/model form from Kimi's own config. The probed default user-facing slug is:

| Slug | Display | Notes |
|---|---|---|
| `kimi-code/kimi-for-coding` | `Kimi-k2.6` | Default Kimi coding model on the probed runtime, OAuth-backed via `managed:kimi-code`, 262k context, thinking/image/video capabilities. |

Kimi has no graded Codex-style reasoning effort or Gemini-style thinking budget in the tested CLI. claude-anyteam maps effort as:

- `minimal` / `low` → pass `--no-thinking`
- `medium` / `high` / `xhigh` → leave thinking enabled (Kimi default)
- `CLAUDE_ANYTEAM_KIMI_THINKING=off` forces `--no-thinking`; `on` forces thinking; unset/`auto` follows the effort mapping

Kimi v1 uses headless print mode (`kimi --print --output-format stream-json`) only. It has no Codex App Server `turn/steer`, no CLI `--output-schema`, and its MCP tools are addressed by bare names (`send_message`, `task_update`) rather than Gemini's `mcp_anyteam_*` naming.

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
| `CLAUDE_ANYTEAM_GEMINI_BACKEND` | Gemini transport: `acp` (default in v0.6.0+) or `headless`. ACP gives mid-turn steering and persistent sessions; headless is the legacy single-shot path for older Gemini CLIs that lack `--acp`/`--experimental-acp`. |
| `CLAUDE_ANYTEAM_GEMINI_EFFORT` | Gemini thinking effort: `minimal`, `low`, `medium`, `high`, or `xhigh` |
| `CLAUDE_ANYTEAM_GEMINI_TRUST` | ACP trust policy: `trusted` (default/backward compatible), `default`, or `plan`; non-trusted modes forward permission requests to team-lead via inbox and block only on `deny` decisions or timeout |
| `CLAUDE_ANYTEAM_GEMINI_APPROVAL_TIMEOUT` | Seconds ACP non-trusted modes wait for a team-lead approval response before failing closed (default: `300`) |
| `CLAUDE_ANYTEAM_KIMI_BINARY` | path to the `kimi` CLI binary used by `kimi-anyteam` (default: `kimi` on PATH) |
| `CLAUDE_ANYTEAM_KIMI_HOME` | adapter-owned Kimi home for isolated config/session state |
| `CLAUDE_ANYTEAM_KIMI_BACKEND` | Kimi transport: `headless` (default) or `acp` (reserved; v1 raises until ACP is wired) |
| `CLAUDE_ANYTEAM_KIMI_THINKING` | Kimi thinking mode: `auto` (default), `on`, or `off` |

## Per-teammate configuration (shim path)

Claude Code's Agent Teams UI only passes name, team, and plan-mode to the spawn shim — it has no field for per-teammate model/effort. To bridge that gap, the shim looks up a per-agent config file at spawn time:

```
~/.claude/teams/<team>/agents/<agent-name>.json
```

**Use the `claude-anyteam team-agent` CLI to write this file.** It is the typed, allowlisted contract for managing per-teammate config — preferred over hand-written JSON or `Write` tool calls:

```bash
claude-anyteam team-agent codex-alice --team build --model gpt-5.5 --effort xhigh
claude-anyteam team-agent gemini-bob  --team build --model gemini-3-pro-preview --effort high
claude-anyteam team-agent codex-alice --team build --remove                       # delete the file
```

The CLI writes atomically, validates the agent/team names against path-traversal, drops unknown keys (only `model` / `effort` are honored), and is idempotent — re-running with the same args is a no-op. The on-disk shape is JSON that the shim reads at spawn time:

```json
{
  "model": "kimi-code/kimi-for-coding",
  "effort": "xhigh"
}
```

When the shim dispatches a `codex-*`, `gemini-*`, or `kimi-*` teammate, it reads this file (if present) and appends `--model` / `--effort` to the adapter invocation. For Codex, the effect is identical to typing those flags on the command line — both App Server and fresh-exec modes pick them up through the shared `Settings` object. For Gemini, `--effort` maps through adapter-owned model aliases when supported by the selected Gemini model family. For Kimi, `--model` is passed as `--model <slug>` and effort controls thinking on/off as above.

Behavior:

- Missing file — no-op, teammate starts with env/default config.
- Malformed JSON or unreadable file — logs `spawn_shim.agent_config_error` to stderr and continues; teammate still starts.
- Unknown keys — ignored. Only `model` and `effort` are forwarded today; more keys may be added later.
- Native (`claude-*`) teammates — the file is not consulted; native dispatch is always pass-through.

Precedence (highest wins): per-agent config file → env vars (`CLAUDE_ANYTEAM_MODEL`, `CLAUDE_ANYTEAM_EFFORT` for Codex/Kimi, `CLAUDE_ANYTEAM_GEMINI_EFFORT` / `CLAUDE_ANYTEAM_GEMINI_TRUST` for Gemini, `CLAUDE_ANYTEAM_KIMI_THINKING` for Kimi thinking override) → adapter defaults → backend CLI defaults.

## Kimi auth and isolated state

Install Kimi CLI with the upstream package (`pip install kimi-cli` or the current upstream installer), then run:

```bash
kimi login
```

The installer detects sign-in by checking the real user home for `~/.kimi/credentials/kimi-code.json`. The runtime copies that OAuth token bundle into the adapter-owned Kimi home on first use; it does not symlink the file, so concurrent `kimi-*` teammates do not race token refreshes. Sessions and Kimi's `kimi.json` work-dir map live under `CLAUDE_ANYTEAM_KIMI_HOME` (default: `~/.cache/claude-anyteam/kimi/<team>/<agent>`), keeping each teammate's Kimi history separate.

The adapter passes the anyteam MCP wrapper through an ephemeral `--mcp-config-file` and leaves the user's persistent `~/.kimi/mcp.json` untouched.

## Team management CLI

Three subcommands of `claude-anyteam` cover the routine team-management writes that previously required `Write`/`Edit`/`Bash` calls. They are all allowlisted by the installer (`Bash(claude-anyteam team-* *)`) so coordinating leads never hit a permission prompt.

| Command | Purpose |
|---|---|
| `claude-anyteam team-agent <name> --team <team> [--model X] [--effort Y]` | Write `~/.claude/teams/<team>/agents/<name>.json`. Whitelisted keys: `model`, `effort`. Use `--remove` to delete. `--print-path` emits the file path on stdout. |
| `claude-anyteam team-patch <name> --team <team>` <br> `claude-anyteam team-patch --team <team> --all-external` | Set `agentType="claude-anyteam"` on a routed-adapter member in `~/.claude/teams/<team>/config.json`. The host `Agent(...)` tool spawns external-LLM teammates with `agentType="general-purpose"` which the wrapper MCP rejects; this command is the post-spawn fixup. `--all-external` patches every `codex-*`, `gemini-*`, or `kimi-*` member at once. |
| `claude-anyteam team-roster --team <team>` <br> `claude-anyteam team-roster --team <team> --json` | Print a one-line-per-member roster summary (or a JSON array) so a coordinating LLM can introspect the team without parsing config.json. |

All three commands fail with exit 1 (or 2 for argparse errors) on missing teams, missing members, or malformed input — never silently. They are safe to run from automation and idempotent on no-op runs.

## Diagnostic skill

The `/claude-anyteam:diagnose` skill is a read-only substrate inspector for coordinating leads. It tells the lead to run:

```bash
claude-anyteam diagnose --team <team-name>
```

The report combines `team-roster --json`, the rich manifest cache under `~/.claude/teams/<team>/manifests/`, recent `visibility_degraded` events, #51 SendMessage flap-repair evidence, wrapper MCP tool-discovery snapshots from `diagnostics/wrapper-mcp-tools.jsonl`, and a green/yellow/red health checklist. Use `--agent <name>` to scope the report and `--since <iso-time>` to filter event/log rows.

`claude-anyteam diagnose` is read-only by default. The only mutating option is `--instrument-spawn`, which writes `env.CLAUDE_ANYTEAM_WRAPPER_MCP_DIAGNOSTICS=1` to `~/.claude/settings.json` so the next teammate spawn captures wrapper MCP diagnostics; use it only when the user explicitly asks to enable instrumentation.

For Codex App Server initialize hangs that mention sqlite/WAL bloat (#43), run:

```bash
claude-anyteam diagnose --codex-log-bloat
```

This scans Codex's sqlite home (`CODEX_SQLITE_HOME`, else `CODEX_HOME`, else `~/.codex`) for `logs_*.sqlite-wal` files over the 100 MiB warning threshold. At runtime, claude-anyteam emits a typed `visibility_degraded` warning before spawning `codex app-server` when the threshold is exceeded and attempts a bounded `PRAGMA wal_checkpoint(TRUNCATE)` through sqlite's locking API; it never deletes Codex-owned log files directly.

## Shim configuration

| Variable | Purpose |
|---|---|
| `CLAUDE_CODE_TEAMMATE_COMMAND` | Set by the installer to the shim binary path. Claude Code reads this to route teammate spawns. |
| `CLAUDE_ANYTEAM_BINARY` | Set by the installer to the adapter binary path. The shim uses this to know where to dispatch `codex-*` spawns. |
| `CLAUDE_ANYTEAM_GEMINI_BINARY` | Set by the installer to the Gemini adapter binary path. The shim uses this for `gemini-*` spawns. |
| `CLAUDE_ANYTEAM_KIMI_BINARY` | Set by the installer to the Kimi adapter binary path. The shim uses this for `kimi-*` spawns. |
| `CLAUDE_ANYTEAM_SHIM_MATCH` / `CLAUDE_ANYTEAM_GEMINI_SHIM_MATCH` / `CLAUDE_ANYTEAM_KIMI_SHIM_MATCH` | Regexes for `codex-*`, `gemini-*`, and `kimi-*` routing. Defaults: `^codex-`, `^gemini-`, `^kimi-`. |
| `CODEX_TEAMMATE_NATIVE_CLAUDE` | Path to the native `claude` binary. Auto-detected; only set if the shim picks the wrong one. |

## Teammate display mode

Claude Code's `teammateMode` key (in `~/.claude.json`, not settings.json) controls how teammates are spawned:

| Value | Behavior |
|---|---|
| `"tmux"` | Spawn teammates as real subprocesses inside a tmux/psmux session. **Required for external CLI teammates** — the pane backend is what triggers the TUI presence line registration and lets `claude-anyteam`, `gemini-anyteam`, or `kimi-anyteam` run as full processes with their own backend sessions. |
| `"auto"` | Choose based on environment. On a non-tmux terminal this typically falls back to in-process, which does not fire the shim → external CLI teammates would never launch. |
| `"in-process"` | Spawn teammates as coroutines inside the main Claude Code process. Valid for native Claude teammates only — the shim is never invoked, so `codex-*`, `gemini-*`, and `kimi-*` names cannot route to this adapter. |

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

Launch with `--plan-mode` (or `CLAUDE_ANYTEAM_PLAN_MODE=true`) to register with `planModeRequired: true`. The adapter will then respond to inbound `plan_approval_request` messages with a structured plan. Codex can use `--output-schema`; Gemini and Kimi embed the schema in the prompt and validate the final message in Python.

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

Removes the managed env keys from `~/.claude/settings.json` (`CLAUDE_CODE_TEAMMATE_COMMAND`, `CLAUDE_ANYTEAM_BINARY`, `CLAUDE_ANYTEAM_GEMINI_BINARY`, and `CLAUDE_ANYTEAM_KIMI_BINARY`). Preserves everything else.

Or, to fully remove:

```bash
claude-anyteam uninstall
uv tool uninstall claude-anyteam
```
