# Configuration

All configuration is via CLI flags or environment variables. The CLI flags win when both are set.

## Adapter CLI flags

```bash
claude-anyteam \
  --team <team-name>        # required — matches ~/.claude/teams/<team>/ directory
  --name <agent-name>       # required — unique within the team
  --cwd <path>              # working directory for model invocations (default: current)
  --model <slug>            # e.g. gpt-5.4, gpt-5.3-codex (default: Codex's own default)
  --effort <level>          # low | medium | high | xhigh (default: Codex's default)
  --plan-mode               # opt into plan approval mode
  --no-app-server           # opt out of App Server mode (use fresh-exec instead)
  --poll-s <float>          # inbox poll interval in seconds (default: 1.5)
  --color <name>            # display color in peer DMs (default: cyan)
  --log <level>             # debug | info | warn | error (default: info)
```

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

## Shim configuration

| Variable | Purpose |
|---|---|
| `CLAUDE_CODE_TEAMMATE_COMMAND` | Set by the installer to the shim binary path. Claude Code reads this to route teammate spawns. |
| `CLAUDE_ANYTEAM_BINARY` | Set by the installer to the adapter binary path. The shim uses this to know where to dispatch `codex-*` spawns. |
| `CODEX_TEAMMATE_SHIM_MATCH` | Regex for agent names to route to the Codex adapter. Default `^codex-`. Override if you want a different convention. |
| `CODEX_TEAMMATE_NATIVE_CLAUDE` | Path to the native `claude` binary. Auto-detected; only set if the shim picks the wrong one. |

## Plan mode

Launch with `--plan-mode` (or `CLAUDE_ANYTEAM_PLAN_MODE=true`) to register with `planModeRequired: true`. The adapter will then respond to inbound `plan_approval_request` messages by invoking Codex once with `--output-schema plan.schema.json` and replying with a structured plan.

```bash
setsid nohup claude-anyteam \
  --team my-team --name codex-planner \
  --cwd /path/to/workspace \
  --plan-mode \
  --model gpt-5.4 --effort high \
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
