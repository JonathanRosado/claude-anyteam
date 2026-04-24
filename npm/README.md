# claude-anyteam (npm installer)

A Node-powered bootstrap for the Python `claude-anyteam` tool. The npm package installs uv + the Python tool, then delegates the `~/.claude/settings.json` + `~/.claude.json` writes to `claude-anyteam install` so the Python installer is the single source of truth for prereq checks (tmux/psmux required, Codex CLI 0.120+ warned-if-missing), teammateMode handling, and install-state tracking.

## Quick start

Run exactly this:

```bash
npx --yes claude-anyteam
```

The setup flow shows the banner immediately, checks `python3`, installs `uv` if needed, installs or reuses `claude-anyteam`, runs `claude-anyteam install --assume-yes` via uv, and registers the Claude Code plugin when `claude` is on your `PATH`.

## What it does

`claude-anyteam`:

1. shows a banner immediately
2. checks for `python3`
3. installs `uv` automatically if it is missing
4. installs `claude-anyteam` with `uv tool install`, or reuses an existing install if it is already available
5. runs `uv tool run --from claude-anyteam claude-anyteam install --assume-yes` — the Python installer verifies a terminal multiplexer (tmux or psmux) is on PATH, probes for the OpenAI Codex CLI (non-blocking warning if missing or below 0.120), writes `~/.claude/settings.json` + `~/.claude.json`, and records an install-state file for symmetric uninstall
6. best-effort installs the `claude-anyteam` Claude Code plugin (or reports the exact manual commands if `claude` is unavailable)

If the Python tool is already present in uv's tool bin directory, setup reuses it and re-runs `claude-anyteam install` (idempotent).

The Python installer owns these files:

- `~/.claude/settings.json` — adds `env.CLAUDE_CODE_TEAMMATE_COMMAND` + `env.CLAUDE_ANYTEAM_BINARY`
- `~/.claude.json` — sets `teammateMode` to `"tmux"`
- `~/.claude/plugins/data/claude-anyteam-claude-anyteam/install-state.json` — receipt so `claude-anyteam uninstall` reverses everything cleanly.

## Install / run

### Explicit setup (recommended)

```bash
npx --yes claude-anyteam
```

If the package is installed globally, run either binary — both invoke the same setup flow:

```bash
claude-anyteam
# or
claude-anyteam-setup
```

### Global install

```bash
npm install -g claude-anyteam
claude-anyteam
```

The npm `postinstall` hook is best-effort only:

- silent on success
- non-interactive
- prints a one-line hint if setup could not finish automatically (so `npm install` never blocks on a missing prereq — user re-runs `npx claude-anyteam` to see the full diagnostics)

## Result

After a successful run, `~/.claude/settings.json` contains absolute paths like:

```json
{
  "env": {
    "CLAUDE_CODE_TEAMMATE_COMMAND": "/Users/you/.local/bin/claude-anyteam-spawn-shim",
    "CLAUDE_ANYTEAM_BINARY": "/Users/you/.local/bin/claude-anyteam"
  }
}
```

And `~/.claude.json` has `teammateMode: "tmux"` so Claude Code routes teammates through the pane backend.

Then restart Claude Code.

Running the installer again is safe: it reuses an existing `claude-anyteam` tool install, the Python installer is idempotent, and the Claude Code plugin is verified rather than reinstalled when it is already present.

## Maintainer note

For local development, you can point the installer at a non-PyPI package spec:

```bash
CLAUDE_ANYTEAM_PYTHON_PACKAGE=/absolute/path/to/claude-anyteam \
  node ./bin/setup.js
```
