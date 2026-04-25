# Empirical Install Flow Analysis

This document captures the current behavior of the `claude-anyteam` installer across various provider states.

## Versions
- `claude-anyteam`: v0.4.0 (local)
- `gemini`: 0.39.0
- `codex-cli`: 0.124.0

---

## Scenario 8: Help Outputs

### `claude-anyteam --help`
```
usage: claude-anyteam [-h] [--team TEAM] [--name NAME] [--cwd CWD]
                      [--poll-s POLL_S] [--color COLOR] [--plan-mode]
                      [--codex-binary CODEX_BINARY]
                      [--app-server | --no-app-server] [--model MODEL]
                      [--effort {low,medium,high,xhigh}]

Route Codex-powered teammates into Claude Code with the claude-anyteam adapter.

options:
  -h, --help            show this help message and exit
  --team TEAM           Team name (overrides CLAUDE_ANYTEAM_TEAM)
  --name NAME           Teammate name within the team (overrides
                        CLAUDE_ANYTEAM_NAME)
  --cwd CWD             Working directory for Codex invocations
  --poll-s POLL_S       Inbox poll interval in seconds
  --color COLOR         Display color (default: cyan)
  --plan-mode           Register with planModeRequired=true (opt-in path).
  --codex-binary CODEX_BINARY
                        Codex CLI binary name (default: codex)
  --app-server, --no-app-server
                        Invoke Codex via `codex app-server` (default: on; pass
                        --no-app-server to use the legacy `codex exec` path
                        with v7.2 session-memory support).
  --model MODEL         Codex model slug (e.g. gpt-5.5, gpt-5.4,
                        gpt-5.3-codex). Overrides CLAUDE_ANYTEAM_MODEL; when
                        unset, Codex's ~/.codex/config.toml default applies.
                        See docs/configuration.md for the current model
                        catalog.
  --effort {low,medium,high,xhigh}
                        Reasoning effort for Codex. Overrides
                        CLAUDE_ANYTEAM_EFFORT; when unset, Codex's per-model
                        default applies.

Management commands:
  claude-anyteam install    Persist the claude-anyteam shim in ~/.claude/settings.json
  claude-anyteam uninstall  Remove the installed Claude teammate shim settings
```

### `claude-anyteam install --help`
```
usage: claude-anyteam install [-h] [--assume-yes]

Persist the claude-anyteam spawn shim in ~/.claude/settings.json so Claude
Code can launch it in future sessions, and set teammateMode="tmux" in
~/.claude.json so teammates route through the pane backend.

options:
  -h, --help        show this help message and exit
  --assume-yes, -y  auto-accept prompts (needed for scripted installs)
```

**Confusion Points:**
- No mention of `gemini` in the main help text (only `codex-binary` and `Codex-powered teammates`).
- `install --help` doesn't show the hidden path-override flags (`--settings-path`, etc.), which makes manual testing/dry-runs harder to discover.
- No explicit "check" or "dry-run" mode documented, though the instructions suggested one exists.

---

## Scenario 1: No codex on PATH, no gemini on PATH
**Command:** `claude-anyteam install --assume-yes`
**Output:**
```
Updated /tmp/test-home-1/.claude/settings.json
Set env.CLAUDE_CODE_TEAMMATE_COMMAND=/home/rosado/Projects/codex-teammate/.venv/bin/claude-anyteam-spawn-shim
Set env.CLAUDE_ANYTEAM_BINARY=/home/rosado/Projects/codex-teammate/.venv/bin/claude-anyteam
Set env.CLAUDE_ANYTEAM_GEMINI_BINARY=/home/rosado/Projects/codex-teammate/.venv/bin/gemini-anyteam
Set teammateMode="tmux" in /tmp/test-home-1/.claude.json
Warning: the OpenAI Codex CLI (`codex`) was not found on PATH.
  claude-anyteam is installed, but codex-* teammates will fail to launch
  until Codex is installed. Add it with:
    npm i -g @openai/codex
  After installing, run `codex` once to sign in.
  Setup guide: https://github.com/openai/codex#getting-started
Warning: the Gemini CLI (`gemini`) was not found on PATH.
  claude-anyteam is installed, but gemini-* teammates will fail to launch
  until Gemini CLI is installed and authenticated. Add it with:
    npm install -g @google/gemini-cli
  After installing, run `gemini` once to sign in, or configure GEMINI_API_KEY/Vertex auth.
  Setup guide: https://github.com/google-gemini/gemini-cli
Restart Claude Code for the changes to take effect. Use codex-* or gemini-* teammate names to route to the matching backend.
```
**Confusion Points:**
- The installer claims success ("Restart Claude Code...") even though neither provider is available.
- Warnings are mixed in with success messages, making it easy to miss that the installation is "dead on arrival".

## Scenario 2: codex on PATH but not signed in
**Command:** `claude-anyteam install --assume-yes`
**Output:**
```
Updated /tmp/test-home-2/.claude/settings.json
Set env.CLAUDE_CODE_TEAMMATE_COMMAND=/home/rosado/Projects/codex-teammate/.venv/bin/claude-anyteam-spawn-shim
Set env.CLAUDE_ANYTEAM_BINARY=/home/rosado/Projects/codex-teammate/.venv/bin/claude-anyteam
Set env.CLAUDE_ANYTEAM_GEMINI_BINARY=/home/rosado/Projects/codex-teammate/.venv/bin/gemini-anyteam
Set teammateMode="tmux" in /tmp/test-home-2/.claude.json
Detected Codex CLI 0.124.0 at /usr/local/lib/node_modules/@openai/codex/bin/codex.js
Warning: the Gemini CLI (`gemini`) was not found on PATH.
  claude-anyteam is installed, but gemini-* teammates will fail to launch
  until Gemini CLI is installed and authenticated. Add it with:
    npm install -g @google/gemini-cli
  After installing, run `gemini` once to sign in, or configure GEMINI_API_KEY/Vertex auth.
  Setup guide: https://github.com/google-gemini/gemini-cli
Restart Claude Code for the changes to take effect. Use codex-* or gemini-* teammate names to route to the matching backend.
```
**Confusion Points:**
- "Detected Codex CLI" implies it is ready to use, but it is not signed in. The user will only find out when they try to launch a teammate and it hangs/fails.

## Scenario 3: gemini on PATH but not signed in
**Command:** `claude-anyteam install --assume-yes`
**Output:**
```
Updated /tmp/test-home-3/.claude/settings.json
Set env.CLAUDE_CODE_TEAMMATE_COMMAND=/home/rosado/Projects/codex-teammate/.venv/bin/claude-anyteam-spawn-shim
Set env.CLAUDE_ANYTEAM_BINARY=/home/rosado/Projects/codex-teammate/.venv/bin/claude-anyteam
Set env.CLAUDE_ANYTEAM_GEMINI_BINARY=/home/rosado/Projects/codex-teammate/.venv/bin/gemini-anyteam
Set teammateMode="tmux" in /tmp/test-home-3/.claude.json
Warning: the OpenAI Codex CLI (`codex`) was not found on PATH.
  claude-anyteam is installed, but codex-* teammates will fail to launch
  until Codex is installed. Add it with:
    npm i -g @openai/codex
  After installing, run `codex` once to sign in.
  Setup guide: https://github.com/openai/codex#getting-started
Detected Gemini CLI 0.39.0 at /usr/local/lib/node_modules/@google/gemini-cli/bundle/gemini.js
Restart Claude Code for the changes to take effect. Use codex-* or gemini-* teammate names to route to the matching backend.
```
**Confusion Points:**
- Same as Scenario 2, but for Gemini.

## Scenario 4: Both CLIs on PATH, neither signed in
**Command:** `claude-anyteam install --assume-yes`
**Output:**
```
Updated /tmp/test-home-4/.claude/settings.json
Set env.CLAUDE_CODE_TEAMMATE_COMMAND=/home/rosado/Projects/codex-teammate/.venv/bin/claude-anyteam-spawn-shim
Set env.CLAUDE_ANYTEAM_BINARY=/home/rosado/Projects/codex-teammate/.venv/bin/claude-anyteam
Set env.CLAUDE_ANYTEAM_GEMINI_BINARY=/home/rosado/Projects/codex-teammate/.venv/bin/gemini-anyteam
Set teammateMode="tmux" in /tmp/test-home-4/.claude.json
Detected Codex CLI 0.124.0 at /usr/local/lib/node_modules/@openai/codex/bin/codex.js
Detected Gemini CLI 0.39.0 at /usr/local/lib/node_modules/@google/gemini-cli/bundle/gemini.js
Restart Claude Code for the changes to take effect. Use codex-* or gemini-* teammate names to route to the matching backend.
```
**Confusion Points:**
- Looks like a perfect installation, but both providers will fail at runtime due to lack of authentication.

## Scenarios 5, 6, 7 (Sign-in variants)
**Results:** All outputs for Scenarios 5, 6, and 7 are **identical** to Scenario 4.
**Confusion Points:**
- The installer has no concept of "signed in" state. It only checks for binary existence on PATH.
- There is no visual difference between a fully authenticated setup and one that will fail immediately.

---

## Overall Summary of UX Problems
1. **No Sign-in Detection:** The biggest gap. The installer assumes presence == readiness.
2. **Success Over-reporting:** Even when neither binary is found, it reports "Updated settings" and "Restart Claude Code" as the primary outcome.
3. **No Provider Status Table:** Warnings are scattered. A unified table showing (Installed, Signed-in) for each would be much clearer.
4. **No Gate:** There is no "refuse-to-install-blank" gate. It happily installs an adapter that does nothing.
5. **Help Text Inconsistency:** `gemini` is a first-class citizen in the code but a second-class citizen in the help text/docs.

---

