# Cross-platform Codex-teammate UX — team brief

**Context:** We just empirically verified that on a Linux host with real tmux installed, one CLI flag solves the whole problem:

```
BUN_OPTIONS="--preload=<probe>" claude --teammate-mode tmux
```

→ Claude Code forces PaneBackend → `$CLAUDE_CODE_TEAMMATE_COMMAND` (the shim) fires → `claude-anyteam` adapter spawns → Codex runs → `registerOutOfProcessTeammateTask()` runs naturally → `@codex-alice` appears in TUI. Real Codex backend. All good.

Evidence (verbatim from this session's probe):
- `SPAWN tmux ["-L","claude-swarm-...","new-session","-d", ...]` ← TmuxExternalBackend fired
- `SPAWN tmux [..., "send-keys", ..., "... claude-anyteam-spawn-shim --agent-name codex-alice ..."]` ← shim invoked
- `ps -ef` showed `claude-anyteam --name codex-alice --team ...` running as real process
- Team config has `backendType: "tmux"`, `tmuxPaneId: "%0"` (pane metadata, not in-process)
- `@codex-alice` visible in user's TUI; messaging round-trip worked

## The remaining problem

This solution requires **tmux installed on the host.** Works fine on Linux/WSL/macOS where tmux is trivial to install. But:

- **Native Windows users** (PowerShell, cmd.exe, Windows Terminal) — tmux doesn't run natively. Cygwin/MSYS2 ports exist but aren't universal.
- **Locked-down corporate machines** — users may lack sudo and can't install tmux.
- **Minimal container environments** — sometimes tmux isn't available.

## Provisional fallback (Option 3)

If tmux missing → installer offers (a) "run your package manager" OR (b) "ship a bundled fake-tmux shim (~400 LoC, KILD-style)" — that satisfies Claude Code's detection and ~4 subcommands enough for the shim chain to fire.

Works, but adds engineering and bundled binaries. **Can we do better?**

## Team mandate

Find a solution (any solution) that:
1. Gets a real Codex-backed teammate visible in Claude Code's TUI presence line
2. Works on native Windows (not WSL, not requiring tmux or any *nix tool)
3. Requires less engineering than bundled fake-tmux
4. Requires zero user-visible setup beyond the plugin install
5. Backed by empirical evidence on this host (or reasoned + docs-cited for Windows case)

**Creative angles to aggressively probe:**

1. **iTerm2 detection slot.** `detectAndGetBackend()` has Priority 2 = check `$ITERM_SESSION_ID`. Does setting that env var + supplying a fake iTerm2 integration layer work on Windows? What subcommands does Claude Code issue in iTerm2 mode?
2. **Windows Terminal / WezTerm / Ghostty native backends.** Does Claude Code 2.1.119 have ANY Windows-aware pane backend? Check the RE'd source at Harzva/learn-likecc for Windows-specific code paths.
3. **BUN_OPTIONS preload cross-platform.** Bun SEA ships for Windows. Can a preload shim that patches `child_process.spawn` intercept Claude Code's own `tmux` calls BEFORE they hit the OS — returning fake successes — so the pane-backend flow thinks it ran? This is "virtual tmux inside the preload," no external binary required. Empirically verify preload works on Claude Code's Windows binary.
4. **iTerm2-header spoofing.** Since iTerm2 mode might issue escape codes rather than shell commands, perhaps it's EASIER to fake on Windows than tmux.
5. **Native pane-backend forcing via a NEW env var or config key we haven't found.** Re-read Bun SEA strings for `PANE_`, `BACKEND_`, anything not already probed.
6. **Is there a `--teammate-mode` value we haven't tried?** E.g., `--teammate-mode iterm2`, `--teammate-mode tmux-external`, `--teammate-mode detached`. Some might work cross-platform.
7. **Separate Claude Code process in a headless pseudo-tty.** Preload opens a ptmx pair inside the process, makes Claude Code think it has a pane, output is captured and re-emitted in the user's terminal. Complex but platform-agnostic.

## Prior research available

Read first:
- `docs/internal/spawn-research-findings.md` — extensive RE work, treat skeptically (some done by sonnet, may have missed options)
- `docs/internal/appstate-hook-findings.md` — environment baseline from the prior team before it crashed
- The verbatim `SPAWN tmux [...]` log output in this brief above

## Team roster

3 Opus-4.7 teammates:
- **lead** — coordinate, critique claims, sign off on direction
- **engineer** — implement AND verify. Single-point ownership. Bias toward trying things.
- **tester** — independent validation, cross-platform reasoning (what about Windows?), probe experiments

## Ground rules

- No edits to `src/claude_anyteam/**` without lead + user sign-off.
- New files (preloads, shims, scripts) fine.
- Every load-bearing claim cited.
- Bias HEAVILY toward action. Don't burn 30 min on analysis before trying one experiment.
- When something requires user intervention (fresh terminal, config change that affects live session), ASK.

## Definition of done

A concrete solution that an installer can wire up in ONE step, works on:
- Linux with tmux (already verified — use as baseline)
- Linux without tmux
- macOS with/without tmux
- Windows native (PowerShell/Windows Terminal)

AND requires less bundled engineering than a 400-line fake-tmux shim if possible.

If we arrive at "fake-tmux shim is genuinely the lightest path," document that and move to implementing it. Don't spin.
