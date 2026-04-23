# codex-teammate (npm installer)

A flashy Node-powered installer for the Python `codex-teammate` tool.

## Quick start

Run exactly this:

```bash
npx --yes --package codex-teammate codex-teammate-setup
```

The setup flow shows the banner immediately, checks `python3`, installs `uv` if needed, installs or reuses `codex-teammate`, and writes the Claude Code launcher paths into `~/.claude/settings.json`.

## What it does

`codex-teammate-setup`:

1. shows a banner immediately
2. checks for `python3`
3. installs `uv` automatically if it is missing
4. installs `codex-teammate` with `uv tool install`, or reuses an existing install if it is already available
5. resolves absolute paths to `codex-teammate` and `codex-teammate-spawn-shim`
6. writes them into `~/.claude/settings.json`

It manages these Claude Code settings keys:

- `env.CLAUDE_CODE_TEAMMATE_COMMAND`
- `env.CODEX_TEAMMATE_BINARY`

## Install / run

### Explicit setup (recommended)

```bash
npx --yes --package codex-teammate codex-teammate-setup
```

If the package is installed globally, run:

```bash
codex-teammate-setup
```

### Global install

```bash
npm install -g codex-teammate
codex-teammate-setup
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
    "CLAUDE_CODE_TEAMMATE_COMMAND": "/Users/you/.local/bin/codex-teammate-spawn-shim",
    "CODEX_TEAMMATE_BINARY": "/Users/you/.local/bin/codex-teammate"
  }
}
```

Then restart Claude Code.

Running the installer again is safe: it reuses an existing `codex-teammate` install when available and reports the settings as verified when nothing changed.

## Maintainer note

For local development, you can point the installer at a non-PyPI package spec:

```bash
CODEX_TEAMMATE_PYTHON_PACKAGE=/absolute/path/to/codex-teammate \
  node ./bin/setup.js
```
