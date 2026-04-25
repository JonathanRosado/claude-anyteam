# Installer Onboarding Test Checklist

This checklist outlines the test scenarios for the new installer flow, based on the UX design specified in `ux-design.md`.

## Test Scenarios

### (a) No providers installed

**Description:** Neither `codex` nor `gemini` CLIs are on `PATH`.

**Command:** `claude-anyteam install`

**Expected Output:**
The installer should refuse to install and exit with code 5. The output should clearly state that no providers are ready and provide instructions on how to install them.

```
Provider status
─────────────────────────────────────────────
              Installed?        Signed in?
Codex CLI     ❌                  —
Gemini CLI    ❌                  —
─────────────────────────────────────────────
Not ready: Codex (not installed) · Gemini (not installed).

claude-anyteam routes some Claude Code teammates to external AI CLIs (Codex, Gemini).
You need at least one signed-in CLI for it to do anything useful.
Pick whichever you have access to.

Codex CLI:
  1. Install:  npm install -g @openai/codex
  2. Sign in:  codex     (opens an OAuth flow on first run)
  Docs: https://github.com/openai/codex#getting-started

Gemini CLI:
  1. Install:  npm install -g @google/gemini-cli
  2. Sign in:  gemini    (or set GEMINI_API_KEY, or configure Vertex)
  Docs: https://github.com/google-gemini/gemini-cli

Refusing to install — no provider is ready.
  Follow the steps above, then re-run `claude-anyteam install`.

  Setting up later? Pass --force-empty to install with no provider ready:
    claude-anyteam install --force-empty
```

**Failure modes to grep for:**
*   `Updated /home/.*/.claude/settings.json` (should not be present)
*   `Restart Claude Code` (should not be present)
*   Check for exit code `5`.

### (b) Both providers installed, but neither signed in

**Description:** Both `codex` and `gemini` CLIs are on `PATH`, but neither is signed in.

**Command:** `claude-anyteam install`

**Expected Output:**
The installer should refuse to install and exit with code 5. The output should show both CLIs as installed but not signed in, and provide instructions on how to sign in.

```
Provider status
─────────────────────────────────────────────
              Installed?        Signed in?
Codex CLI     ✅ 0.124.0         ❌
Gemini CLI    ✅ 0.39.0          ❌
─────────────────────────────────────────────
Almost ready: Codex (needs sign-in) · Gemini (needs sign-in).

claude-anyteam routes some Claude Code teammates to external AI CLIs (Codex, Gemini).
You need at least one signed-in CLI for it to do anything useful.
Pick whichever you have access to.

Codex CLI:
  1. Sign in:  codex     (opens an OAuth flow on first run)
  Docs: https://github.com/openai/codex#getting-started

Gemini CLI:
  1. Sign in:  gemini    (or set GEMINI_API_KEY, or configure Vertex)
  Docs: https://github.com/google-gemini/gemini-cli

Refusing to install — no provider is ready.
  Follow the steps above, then re-run `claude-anyteam install`.

  Setting up later? Pass --force-empty to install with no provider ready:
    claude-anyteam install --force-empty
```

**Failure modes to grep for:**
*   `Updated /home/.*/.claude/settings.json` (should not be present)
*   `Restart Claude Code` (should not be present)
*   Check for exit code `5`.

### (c) Only Codex signed in

**Description:** `codex` CLI is installed and signed in. `gemini` CLI is not installed.

**Command:** `claude-anyteam install`

**Expected Output:**
The installer should successfully install and report Codex as ready and Gemini as not installed. It should provide instructions for installing Gemini.

```
Provider status
─────────────────────────────────────────────
              Installed?        Signed in?
Codex CLI     ✅ 0.124.0         ✅
Gemini CLI    ❌                  —
─────────────────────────────────────────────
Ready: Codex 0.124.0 · Gemini (not installed).

Gemini CLI:
  1. Install:  npm install -g @google/gemini-cli
  2. Sign in:  gemini    (or set GEMINI_API_KEY, or configure Vertex)
  Docs: https://github.com/google-gemini/gemini-cli

Updated /home/user/.claude/settings.json
Set env.CLAUDE_CODE_TEAMMATE_COMMAND=...
Set env.CLAUDE_ANYTEAM_BINARY=...
Set env.CLAUDE_ANYTEAM_GEMINI_BINARY=...
Set teammateMode="tmux" in /home/user/.claude.json

Restart Claude Code for the changes to take effect. Use codex-* or gemini-* teammate names to route to the matching backend.
```

**Failure modes to grep for:**
*   `Refusing to install` (should not be present)
*   Absence of `Updated /home/user/.claude/settings.json`

### (d) Only Gemini signed in

**Description:** `gemini` CLI is installed and signed in. `codex` CLI is not installed.

**Command:** `claude-anyteam install`

**Expected Output:**
The installer should successfully install and report Gemini as ready and Codex as not installed. It should provide instructions for installing Codex.

```
Provider status
─────────────────────────────────────────────
              Installed?        Signed in?
Codex CLI     ❌                  —
Gemini CLI    ✅ 0.39.0          ✅
─────────────────────────────────────────────
Ready: Gemini 0.39.0 · Codex (not installed).

Codex CLI:
  1. Install:  npm install -g @openai/codex
  2. Sign in:  codex     (opens an OAuth flow on first run)
  Docs: https://github.com/openai/codex#getting-started

Updated /home/user/.claude/settings.json
Set env.CLAUDE_CODE_TEAMMATE_COMMAND=...
Set env.CLAUDE_ANYTEAM_BINARY=...
Set env.CLAUDE_ANYTEAM_GEMINI_BINARY=...
Set teammateMode="tmux" in /home/user/.claude.json

Restart Claude Code for the changes to take effect. Use codex-* or gemini-* teammate names to route to the matching backend.
```

**Failure modes to grep for:**
*   `Refusing to install` (should not be present)
*   Absence of `Updated /home/user/.claude/settings.json`

### (e) Both providers signed in

**Description:** Both `codex` and `gemini` CLIs are installed and signed in.

**Command:** `claude-anyteam install`

**Expected Output:**
The installer should successfully install and report both providers as ready. No walkthroughs should be displayed.

```
Provider status
─────────────────────────────────────────────
              Installed?        Signed in?
Codex CLI     ✅ 0.124.0         ✅
Gemini CLI    ✅ 0.39.0          ✅
─────────────────────────────────────────────
Ready: Codex 0.124.0 · Gemini 0.39.0.

Updated /home/user/.claude/settings.json
Set env.CLAUDE_CODE_TEAMMATE_COMMAND=...
Set env.CLAUDE_ANYTEAM_BINARY=...
Set env.CLAUDE_ANYTEAM_GEMINI_BINARY=...
Set teammateMode="tmux" in /home/user/.claude.json

Restart Claude Code for the changes to take effect. Use codex-* or gemini-* teammate names to route to the matching backend.
```

**Failure modes to grep for:**
*   `Refusing to install` (should not be present)
*   `Gemini CLI:` or `Codex CLI:` (walkthroughs should not be present)
*   Absence of `Updated /home/user/.claude/settings.json`

### (f) Provider binary missing entirely (same as (a))

This scenario is identical to (a) where neither provider is installed.

### (g) Force install with no providers

**Description:** Neither `codex` nor `gemini` CLIs are on `PATH`, but the user forces the installation.

**Command:** `claude-anyteam install --force-empty`

**Expected Output:**
The installer should proceed with the installation but print a warning that the installation is inert.

```
Provider status
─────────────────────────────────────────────
              Installed?        Signed in?
Codex CLI     ❌                  —
Gemini CLI    ❌                  —
─────────────────────────────────────────────
Not ready: Codex (not installed) · Gemini (not installed).

claude-anyteam routes some Claude Code teammates to external AI CLIs (Codex, Gemini).
You need at least one signed-in CLI for it to do anything useful.
Pick whichever you have access to.

Codex CLI:
  1. Install:  npm install -g @openai/codex
  2. Sign in:  codex     (opens an OAuth flow on first run)
  Docs: https://github.com/openai/codex#getting-started

Gemini CLI:
  1. Install:  npm install -g @google/gemini-cli
  2. Sign in:  gemini    (or set GEMINI_API_KEY, or configure Vertex)
  Docs: https://github.com/google-gemini/gemini-cli

Proceeding with --force-empty: claude-anyteam is installed but inert until a CLI is ready.

Updated /home/user/.claude/settings.json
Set env.CLAUDE_CODE_TEAMMATE_COMMAND=...
Set env.CLAUDE_ANYTEAM_BINARY=...
Set env.CLAUDE_ANYTEAM_GEMINI_BINARY=...
Set teammateMode="tmux" in /home/user/.claude.json

Restart Claude Code for the changes to take effect. Use codex-* or gemini-* teammate names to route to the matching backend.
```

**Failure modes to grep for:**
*   `Refusing to install` (should not be present)
*   Absence of `Proceeding with --force-empty`
*   Absence of `Updated /home/user/.claude/settings.json`
