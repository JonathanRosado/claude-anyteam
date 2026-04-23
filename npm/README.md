# claude-anyteam (npm installer)

A flashy Node-powered installer for the Python `claude-anyteam` tool.

## Quick start

Run exactly this:

```bash
npx --yes --package claude-anyteam claude-anyteam-setup
```

The setup flow shows the banner immediately, checks `python3`, installs `uv` if needed, installs or reuses `claude-anyteam`, and writes the Claude Code launcher paths into `~/.claude/settings.json`.

## What it does

`claude-anyteam-setup`:

1. shows a banner immediately
2. checks for `python3`
3. installs `uv` automatically if it is missing
4. installs `claude-anyteam` with `uv tool install`, or reuses an existing install if it is already available
5. resolves absolute paths to `claude-anyteam` and `claude-anyteam-spawn-shim`
6. writes them into `~/.claude/settings.json`

If the Python tool is already present in uv's tool bin directory, setup reuses it and only refreshes Claude Code settings.

It manages these Claude Code settings keys:

- `env.CLAUDE_CODE_TEAMMATE_COMMAND`
- `env.CLAUDE_ANYTEAM_BINARY`

## Install / run

### Explicit setup (recommended)

```bash
npx --yes --package claude-anyteam claude-anyteam-setup
```

If the package is installed globally, run:

```bash
claude-anyteam-setup
```

### Global install

```bash
npm install -g claude-anyteam
claude-anyteam-setup
```

The npm `postinstall` hook is best-effort only:

- silent on success
- non-interactive
- prints a one-line hint if setup could not finish automatically

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

Then restart Claude Code.

Running the installer again is safe: it reuses an existing `claude-anyteam` install when available and reports the settings as verified when nothing changed.

## Maintainer note

For local development, you can point the installer at a non-PyPI package spec:

```bash
CLAUDE_ANYTEAM_PYTHON_PACKAGE=/absolute/path/to/claude-anyteam \
  node ./bin/setup.js
```
