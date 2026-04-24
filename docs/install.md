# Install

The quickstart in the [README](../README.md#quickstart) is the normal path:

```bash
npx --yes claude-anyteam
```

That's the entire install. The npm installer:

- Detects `python3` and installs `uv` if missing (non-interactive, no shell profile edits)
- Installs the `claude-anyteam` Python tool via `uv tool install`
- Delegates the config writes to `claude-anyteam install --assume-yes`, which:
  - Verifies a terminal multiplexer (tmux or psmux) is on PATH — see [configuration.md](configuration.md#teammate-display-mode) for install commands
  - Writes the Claude Code hook (`CLAUDE_CODE_TEAMMATE_COMMAND`) and binary path to `~/.claude/settings.json`
  - Sets `teammateMode="tmux"` in `~/.claude.json` (prompts before overwriting a non-default value)
  - Records what it changed in `~/.claude/plugins/data/claude-anyteam-claude-anyteam/install-state.json` so uninstall can cleanly reverse it
- Registers the Claude Code plugin (marketplace + install) when the `claude` CLI is on PATH

## How installation works

The Python installer writes two env vars to `~/.claude/settings.json`:

```json
{
  "env": {
    "CLAUDE_CODE_TEAMMATE_COMMAND": "/absolute/path/to/claude-anyteam-spawn-shim",
    "CLAUDE_ANYTEAM_BINARY": "/absolute/path/to/claude-anyteam"
  }
}
```

When Agent Teams mode spawns a teammate, Claude Code invokes `$CLAUDE_CODE_TEAMMATE_COMMAND` (the shim) instead of native `claude`. The shim inspects the agent name:

- `codex-*` → dispatches to the `claude-anyteam` adapter (Codex)
- Anything else → forwards to native `claude` (unchanged)

Every step is reversible. `claude-anyteam uninstall` cleanly removes the env vars, reverts `teammateMode` to whatever was there before (or removes the key if install added it from scratch), and deletes the state file plus its plugin-data directory if they're now empty.

## Uninstall

`claude-anyteam uninstall` is designed to leave no trace of what the installer wrote. It touches the same three files the installer did:

- `~/.claude/settings.json` — removes the `CLAUDE_CODE_TEAMMATE_COMMAND` and `CLAUDE_ANYTEAM_BINARY` keys from the `env` block. Any other keys you have there (including unrelated `env` entries like `KEEP_ME`) are preserved. If `settings.json` didn't exist before install and is now empty after our removal, the file is deleted.
- `~/.claude.json` — reverts `teammateMode` to whatever it held before install, or removes the key entirely if install added it from scratch. If the file didn't exist before install and is now empty, it is deleted.
- `~/.claude/plugins/data/claude-anyteam-claude-anyteam/install-state.json` — deleted. The enclosing plugin-data directory is removed too, but only if empty. Parent directories (`~/.claude/plugins/data/`, `~/.claude/plugins/`) are left alone.

### What uninstall does NOT do

- **Does not uninstall the Python tool.** Run `uv tool uninstall claude-anyteam` to remove the `claude-anyteam` binary itself.
- **Does not uninstall the Claude Code plugin** (if you installed via the plugin path). Run `claude plugin uninstall claude-anyteam@claude-anyteam` inside Claude Code.
- **Does not remove tmux or psmux.** These are system-level tools installed via your package manager; uninstall them with `apt remove tmux` / `brew uninstall tmux` / `winget uninstall psmux` as appropriate.

### Safety

If the installer's state file has been manually edited into a form the uninstaller cannot parse, uninstall refuses to touch any config and exits with a distinct error code so you can inspect the state file and either fix or delete it by hand. Uninstall is idempotent — running it twice is a clean no-op.

## Alternative install methods

```bash
# via uv directly (if you already have uv + Python)
uv tool install claude-anyteam && claude-anyteam install

# via Claude Code plugin (self-heals settings on every session)
claude plugin marketplace add JonathanRosado/claude-anyteam
claude plugin install claude-anyteam@claude-anyteam
```

All paths write the same settings. Pick whichever fits your workflow.

Installing the plugin also gives Claude a plugin-provided `/claude-anyteam:help` skill, so the assistant can explain that `codex-<name>` teammates route to Codex, that `~/.claude/settings.json` is already wired by the installer, and that the GitHub repo is the source of truth.

## Headless / persistent teammates

For headless and persistent background adapters (run across multiple Claude Code sessions):

```bash
setsid nohup claude-anyteam \
  --team my-team --name codex-alice \
  --cwd /path/to/workspace \
  --model gpt-5.5 --effort high \
  </dev/null >/tmp/codex-alice.stdout 2>/tmp/codex-alice.stderr & disown
```

This mode is fully messageable (inbox, task claim, peer replies) but does NOT render in Claude Code's TUI presence line — TUI visibility requires the leader-spawn path via the shim. Useful when you want the adapter running continuously regardless of the Claude Code session lifecycle.
