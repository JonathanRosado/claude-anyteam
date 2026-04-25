# AppState hook — findings

Shared findings doc for the `appstate-hook` team. Each author appends under their own heading. Every claim must cite source (file:line, URL, command output).

## Tester

Environment baseline:
- Host: WSL2, Linux 6.6.87.2 (non-tmux by default, tmux binary present)
- `claude --version` → `2.1.119 (Claude Code)` (binary at `/home/rosado/.local/share/claude/versions/2.1.119`; brief cites 2.1.118 — prior-research observations may need re-verification on the newer build)
- CWD for all tests: `/home/rosado/Projects/codex-teammate`
- Scratch workspace: `/tmp/appstate-tester/`
- Live session (user) PIDs to AVOID touching: 625925, 636692 (both `claude --resume 9f68fd4c-...`) — these are the session that spawned this team.

## tester — Windows / cross-platform probe (2026-04-23)

**Headline:** Claude Code 2.1.119 has **exactly one** pane backend that works on native Windows today: **none**. Every non-in-process backend in 2.1.119 either requires a Unix binary (`tmux`) or a CLI that doesn't exist yet (`it2`, Ghostty IPC, WezTerm CLI, `wt.exe` integration). Windows users currently have three real options: WSL+tmux, fake-tmux shim, or a **native ConPTY-based tmux-API replacement (`psmux`)** that already exists in the wild. The psmux path is the lowest-engineering win for native Windows, because it moves the bundled-binary problem from "we ship it" to "it's in winget/scoop/choco."

### 1. What backends Claude Code 2.1.119 actually ships

Confirmed from prior RE (spawn-research-findings.md:1104-1195, `utils/swarm/backends/registry.ts` → `detectAndGetBackend()`):

```
Priority 1 — TmuxInternalBackend   (process.env.TMUX non-empty)
Priority 2 — iTerm2Backend         (TERM_PROGRAM=iTerm.app OR ITERM_SESSION_ID non-empty; requires `it2` CLI)
Priority 3 — TmuxExternalBackend   (isTmuxAvailable(): `tmux -V` exit 0)
Fallback   — InProcessBackend
```

There is **no** WezTerm, Ghostty, Windows Terminal, or Warp backend in 2.1.119. Every such backend is either an open feature request (issues #24189 Ghostty, #24384 Windows Terminal, #23574 WezTerm, #24122 Zellij) or blocked on the upstream terminal shipping an IPC API first. Source: `github.com/anthropics/claude-code/issues/24189`, `#24384`, `#26572`.

### 2. Claude Code Windows binary shape

- Distribution on Windows is the same Bun Single-File Executable (SFE) pattern as Linux/macOS. `BUN_OPTIONS=--preload=...` applies there too in principle — **but** issue #26244 (referenced from #34150) documents that under Bun SFE on Windows, `process.stdout.isTTY` is `undefined`, which **forces in-process mode regardless of `teammateMode` or env vars**. Source: `github.com/anthropics/claude-code/issues/34150` (summary, 2026-03).
- Implication: a BUN_OPTIONS-preload fake-tmux shim alone is **insufficient** on Windows in 2.1.119 — the TTY gate trips before backend selection ever runs. Any Windows solution must also fix the TTY detection (or bypass `isInProcessEnabled()`) OR the user must be inside a terminal where Bun correctly reports TTY (e.g., Windows Terminal running Claude from a PowerShell that has a real ConPTY).
- 2026 Windows-specific changelog entries I could find: none that add a new pane backend. `CLAUDE_CODE_USE_POWERSHELL_TOOL` is Windows-only but unrelated to backends (spawn-research-findings.md:1818).

### 3. cs50victor/claude-code-teams-mcp — what `USE_TMUX_WINDOWS` actually does

Clone + grep (`/tmp/ccteams/src/claude_teams/spawner.py:37-55`):

```python
def use_tmux_windows() -> bool:
    return os.environ.get("USE_TMUX_WINDOWS") is not None

def build_tmux_spawn_args(command: str, name: str) -> list[str]:
    if use_tmux_windows():
        return ["tmux", "new-window", "-dP", "-F", "#{window_id}", "-n",
                f"@claude-team | {name}", command]
    return ["tmux", "split-window", "-dP", "-F", "#{pane_id}", command]
```

**Finding:** `USE_TMUX_WINDOWS` is **not** about Microsoft Windows. It is about tmux **windows vs panes** (spawn a new tmux window instead of splitting a pane). It shells out to `tmux` unconditionally either way. cs50victor ships zero native-Windows code path; on real Windows it requires a tmux binary (WSL, Cygwin, MSYS2, or psmux). The env-var name is misleading.

### 4. Options for native Windows, ranked by engineering cost

| Option | What | Effort | Blockers |
|---|---|---|---|
| **A. psmux (native ConPTY tmux replacement)** | Install `psmux` via winget/scoop/choco. It already sets `TMUX=`, `TMUX_PANE=`, `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` and implements 76 tmux subcommands incl. every one Claude Code issues. Teams "just works" once installed and user launches `claude` inside a psmux session. | Near zero for us (recommend + detect). | Still requires `process.stdout.isTTY` to be defined under Bun SFE — psmux is ConPTY so this is expected to work, but unverified by us. |
| **B. Bundled fake-tmux shim (KILD-style, ~400 LoC)** | We ship a Windows-native PTY manager that impersonates tmux CLI. | High — rewrite for ConPTY, distribute binary, maintain. | We own the binary forever; Bun SFE TTY-gate still applies. |
| **C. BUN_OPTIONS preload "virtual tmux"** | Preload patches `child_process.spawn` to fake tmux success + manage PTYs inside the Claude process. | Medium. Cross-platform in theory. | Bun SFE on Windows: `stdout.isTTY === undefined` forces in-process BEFORE our preload gets a say; we'd need to also patch `process.stdout` descriptor — fragile. |
| **D. Wait for Anthropic** | Issues #24384 (Windows Terminal), #34150 (psmux), #26572 (CustomPaneBackend) are all open. | Zero now, unknown later. | Not a solution we ship. |
| **E. iTerm2 spoof on Windows** | Set `ITERM_SESSION_ID`, supply fake `it2` CLI. | Medium. | iTerm2 backend shells out to `it2 session split` (see issue #24292) — a Python-API-mediated command that expects an iTerm2 app to exist. On Windows there is no iTerm2 host, so `it2 split` has nothing to split. Non-starter without also faking the rendering. |

**Recommendation:** Ship Option A as the documented Windows path — add a single line to the installer: "on Windows, if psmux not detected, recommend `winget install psmux`." Keep Option B as a private fallback only if psmux adoption is insufficient. Options C/E are research rabbit-holes — the TTY gate makes them worse than A, not better.

### 5. Passive probe results (this host)

Probe: CJS preload (`/tmp/appstate-tester/probe.cjs`) that wraps `child_process.spawn`/`spawnSync` and logs every invocation. Ran `BUN_OPTIONS=--preload=... claude --teammate-mode tmux --print "hi"` in a loop with these env-var permutations: `baseline`, `ITERM_SESSION_ID+TERM_PROGRAM=iTerm.app`, `TERM_PROGRAM=iTerm.app` alone, `TERM_PROGRAM=ghostty`, `WEZTERM_PANE+TERM_PROGRAM=WezTerm`, `WT_SESSION`, `WT_SESSION+TERM_PROGRAM=Windows_Terminal`.

**Result: all seven runs produce byte-identical spawn logs** (git, rg-index, plugin session hooks, powershell.exe-probe, stop hooks). **Zero `tmux`, `it2`, `wt.exe`, or any terminal-specific subcommands are issued in any run.** No backend-detection code path fires. This matches prior RE finding (spawn-research-findings.md:635, issue #34614): `--print` is non-interactive → `isInProcessEnabled()` short-circuits on the non-interactive check → pane backend selection is **never reached**. Env-var spoofing cannot override this gate from outside the process — only a patch of `process.stdout.isTTY` / the non-interactive detector inside the preload can, and we haven't verified that works under Bun SFE.

One incidental confirmation: Claude Code itself probes `powershell.exe -NoProfile -NonInteractive -Command '$env:USERPROFILE'` during every startup, even on Linux/WSL. This is WSL-interop detection logic, not a Windows Terminal pane backend. It does not change by terminal env vars.

### 6. Definition-of-done scorecard

| Target | Status |
|---|---|
| Linux + tmux | ✅ already verified (brief) |
| Linux no-tmux | ❌ — only Path B (out-of-process `setsid nohup`) works; TUI presence requires pane backend. |
| macOS + tmux | ✅ inherits Linux-with-tmux path. |
| macOS no-tmux (iTerm2 only) | ⚠ iTerm2 backend exists but issue #24292 shows `teammateMode:"tmux"` does not actually pick iTerm2 even when all prerequisites met — upstream bug. Needs user-side `teammateMode:"tmux"` AND correct `it2` install AND Python API enabled. Flaky. |
| Windows native | ❌ in 2.1.119 without external help. Recommended path: **install psmux**, launch claude inside it. We should document this in the installer. |

### 7. Open questions for lead

1. Are we willing to take a soft dependency on psmux on Windows (point users to winget/scoop) rather than bundle a fake-tmux shim? This is the single highest-leverage decision.
2. Should the installer ship a `--windows-check` that probes for psmux / tmux / WSL-tmux and prints a ranked recommendation, rather than us picking one path?
3. Is macOS-without-tmux (iTerm2-only) in scope for "done"? The upstream #24292 bug suggests we may need to document `teammateMode:"tmux"` + `it2` install as the path AND accept flakiness.

## tester — Track 1: Windows-awareness RE crawl (2026-04-23)

Source: `Harzva/learn-likecc` cloned to `/tmp/harzva` at commit on 2026-04-18 (51万 LoC RE'd rebuild of Claude Code). Crawled `ccsource/claude-code-main/src/utils/swarm/backends/` — the canonical pane-backend directory.

### Every pane backend class in 2.1.119 source

| File | Class / factory | Detection predicate | Platform coupling |
|---|---|---|---|
| `TmuxBackend.ts` | `createTmuxBackend()` | `isInsideTmux()` (Priority 1) OR `isTmuxAvailable()` (Priority 3, `tmux -V` exit 0) | Shells out to `tmux` binary — POSIX only. |
| `ITermBackend.ts` | `createITermBackend()` | `isInITerm2()` (Priority 2) AND `isIt2CliAvailable()` (`it2 session list` exit 0) | Shells out to `it2` Python CLI. `it2` connects to iTerm2 Python API over a UNIX domain socket. **macOS-only in practice.** |
| `InProcessBackend.ts` | fallback | — | Cross-platform; no external pane visible. |

**There is NO other pane backend class.** No `WindowsTerminalBackend`, `WezTermBackend`, `GhosttyBackend`, `ConPTYBackend`, `ZellijBackend`, or `WarpBackend` in the RE'd source tree.

### Detection predicates — every env var Claude Code 2.1.119 checks

From `backends/detection.ts:1-128` (full file read):

```
isInsideTmuxSync() → !!process.env.TMUX            // captured at module load
isInsideTmux()     → !!process.env.TMUX            // cached wrapper
isInITerm2()       → TERM_PROGRAM === 'iTerm.app'
                     || !!ITERM_SESSION_ID
                     || env.terminal === 'iTerm.app'
isTmuxAvailable()  → execFileNoThrow('tmux', ['-V']).code === 0
isIt2CliAvailable()→ execFileNoThrow('it2', ['session','list']).code === 0
```

**That is the complete set.** No `WT_SESSION`, no `WEZTERM_PANE`, no `TERM_PROGRAM=ghostty`, no `TERM_PROGRAM=WezTerm`, no `process.platform==='win32'` branches anywhere in the backend code.

### Windows platform mentions in the entire swarm/backends tree

`grep -rn "win32\|Windows\|platform" /tmp/harzva/ccsource/claude-code-main/src/utils/swarm/backends/` → **exactly one hit**:

- `registry.ts:260-279` — `getTmuxInstallInstructions()` has a `case 'windows'` arm that returns a string telling the user to install WSL. That is the full extent of Windows support in the pane-backend subsystem. No code path selects a Windows-native backend.

### Closing implications for lead

1. **There is no hidden `--teammate-mode` value to discover.** `getBackendByType()` (registry.ts:295-310) accepts exactly `'tmux'` and `'iterm2'`. `--teammate-mode windows-terminal` / `wezterm` / anything else falls through to default or error. Worth a one-line empirical confirmation by engineer.
2. **`TMUX` env-var spoofing works for Priority 1** (confirmed by prior research, spawn-research-findings.md:2633): any non-empty `$TMUX` makes Claude Code select TmuxBackend — no socket-path validation. That's the single fastest lever; a fake-tmux shim OR `psmux` intercepts the resulting `tmux send-keys`/`split-window`/etc.
3. **`it2` spoofing is viable cross-platform**: `ITERM_SESSION_ID=fake` + a PATH-shimmed `it2` that implements `session list/split/run` as PTY spawns. Fewer subcommands than tmux (simpler surface). But iTerm2 backend is Mac-conventional; we'd ship a lie users must accept. Worth trying if tmux shim proves brittle.

### Track 3 — cross-platform risk table

| Concern | Linux/macOS | Windows native |
|---|---|---|
| BUN_OPTIONS preload fires | ✅ verified on this host | ⚠ expected yes but untested. Bun SFE Windows has known TTY detection quirk (#26244). |
| Preload can monkey-patch `child_process.spawn` | ✅ verified | ⚠ should work (Bun is same engine) but needs empirical proof. |
| Preload can fake `process.stdout.isTTY` | ⚠ on Bun SFE, `tty` is built-in — late-binding visibility unclear. Engineer must probe before committing. | ⚠ same concern, amplified by `undefined` quirk. |
| `CLAUDE_CODE_TEAMMATE_COMMAND` shim binary | Single POSIX shell script works. | Needs `.cmd`/`.bat` wrapper or a packaged `.exe`. Current `claude-anyteam-spawn-shim` is `#!/usr/bin/env python3` — **will not run on Windows cmd.exe** as-is. |
| `PaneBackendExecutor` command shape (`cd X && env VAR=val bin ...`) | POSIX works. | ⚠ `env VAR=val` is NOT a cmd.exe builtin. Engineer must audit decompiled `PaneBackendExecutor.ts` for `process.platform` branches. If none, the pane-backed Windows path is **broken upstream**, regardless of what we do. |
| `setsid nohup` fallback (Path B) | ✅ POSIX | ❌ Neither exists on Windows. Path B needs `START /B` or `pythonw`-detach equivalent. |

**Upshot:** native Windows needs work on at least three independent axes (TTY gate, shim binary format, shell-command assembly). Fake-tmux/psmux solves only one (backend selection). Lead should treat "Windows done" as requiring a bundled-or-assumed multiplexer PLUS shim packaging PLUS Path B rewrite.

## tester — Track 2: psmux coverage + isTTY preload override (2026-04-23)

Two verifications requested by lead. Both resolved with strong evidence.

### (1) BUN_OPTIONS preload CAN override `process.stdout.isTTY` — verified on this host

Probe `/tmp/isTTY-probe.js`:
```
Object.defineProperty(process.stdout, 'isTTY', { value: true, configurable: true, writable: true });
console.error('[probe] BEFORE/AFTER isTTY=...');
```

Runs:

```
$ claude --version                                          # baseline, piped
2.1.119 (Claude Code)

$ BUN_OPTIONS=--preload=/tmp/isTTY-probe.js claude --version
[probe] BEFORE override: stdout.isTTY= undefined stdin.isTTY= undefined stderr.isTTY= undefined
[probe] AFTER override: stdout.isTTY= true
2.1.119 (Claude Code)

$ BUN_OPTIONS=--preload=/tmp/isTTY-probe.js claude --print "hi"
[probe] BEFORE override: stdout.isTTY= undefined ...
[probe] AFTER override: stdout.isTTY= true
Hi! What would you like to work on?
```

**Findings:**
1. The preload **fires before** Claude Code's code runs — `[probe] BEFORE` lines appear first.
2. `Object.defineProperty` with `configurable: true, writable: true` successfully mutates `process.stdout.isTTY`. Bun's built-in `tty` binding does NOT freeze the descriptor.
3. On **Linux with piped stdout**, `process.stdout.isTTY === undefined` — the **exact same symptom** that issue #26244 attributes to Bun SEA on Windows. If Linux doesn't fall into in-process because of this (Claude works fine), then the Windows "undefined forces in-process" narrative deserves re-investigation — it may be a specific Bun-on-Windows bug distinct from `undefined` per se, OR issue #26244 may have been misattributed, OR Claude Code's check is `!!isTTY` (undefined → falsy → in-process) but something else gates interactivity on Linux. **Engineer's Probe A should include an `AFTER: isTTY=true` check inside a piped `--print` run and verify that backend selection proceeds past `isInProcessEnabled()`.** We have one half-confirmed assumption; we need the other half.

**Implication:** the preload-on-Windows path is **not a priori blocked** by the TTY issue. It's worth engineer's time to try, not write off.

### (2) psmux vs Claude Code TmuxBackend — coverage verified

**Every tmux subcommand Claude Code 2.1.119 issues** (grep `/tmp/harzva/ccsource/claude-code-main/src/utils/swarm/backends/TmuxBackend.ts`):

| Subcommand | TmuxBackend uses | psmux implements (file:line) |
|---|---|---|
| `send-keys` | line 157 | `src/util.rs:413` (handler + Claude Code-specific test) |
| `split-window` | 572, 603, 675 | `src/help.rs:39-40` + `src/commands.rs` |
| `list-panes` | 348, 439, 516, 585, 660, 709, 741 | `src/commands.rs:298` (generates format output) |
| `kill-pane` | 273 | `src/help.rs:288` |
| `new-session` | 285, 475 | `src/types.rs:633, 1000` |
| `display-message` | 379, 408 | `src/help.rs:326` + `src/format.rs` |
| `select-layout` | 344, 722, 753 | `src/commands.rs:538, 1419` |
| `select-pane` | 179, 218 | `src/commands.rs:505, 632` |
| `set-option` | 187, 196, 222, 245 | `src/commands.rs:535, 1268` |
| `has-session` | 460 | `src/help.rs:258` + `src/commands.rs:566` |
| `resize-pane` | (grep'd, matches one site) | listed in psmux cli.rs |
| `capture-pane` | used elsewhere in swarm/ | `src/cli.rs:139, 432` |

**10-of-10 coverage on the core set, 12-of-12 including the two prior-research adds.** psmux docs claim "83 tmux-compatible commands" (`docs/features.md`).

**Control-mode / `-CC`:** `grep -rn "'-CC'\|control.mode" /tmp/harzva/ccsource/claude-code-main/src/utils/swarm/` → **zero hits**. Claude Code never invokes tmux in control mode. psmux's lack of `-CC` support is therefore irrelevant.

**Version-string format:** `isTmuxAvailable()` at `detection.ts:73-76` runs `execFileNoThrow('tmux', ['-V'])` and checks `result.code === 0`. **No regex on stdout.** psmux's `main.rs:239-240` handles `-V` and exits 0 (`print_version()` at `cli.rs:422`). Claude Code's detector doesn't care what string psmux prints — the exit code alone satisfies the check.

**Bonus find — psmux specifically solves the `env VAR=val` problem we flagged in Track 3:**
- `src/util.rs:197` — `parse_env_assignment(s)` parses `FOO=bar` prefixes.
- `src/pane.rs:571` — comment reads: "POSIX `env VAR=val ... command args` invocations into PowerShell equivalents."
- `src/types.rs:596` — comment reads: "so that `env VAR=val command` syntax works (required by Claude Code, etc.)."
- `src/util.rs:413` — a dedicated unit test titled `test_send_keys_claude_code_agent_command_preserves_backslashes` exercises the exact command shape `PaneBackendExecutor` emits, including Windows paths like `C:\Users\foo\.local\bin\claude.exe`.

psmux was purpose-built for Claude Code agent-team compatibility on Windows. My Track 3 concern #4 ("`env VAR=val` is not cmd.exe builtin") is specifically addressed by psmux's `env` translator. That's a significant de-risking of the Windows recommend-psmux path.

### Updated recommendation

| Path | Verdict |
|---|---|
| Preload (Linux/mac) | Likely works — isTTY override verified on Linux; engineer is finalizing. |
| Preload (Windows) | **Re-open as viable.** isTTY override is known to work on Linux even with `undefined` start-state, which is structurally the same as the Windows-SEA report. Needs Windows-host empirical confirmation, but don't write it off. |
| psmux on Windows (winget) | **Validated.** Full subcommand coverage, no version-regex mismatch, no -CC requirement, built-in `env VAR=val` translator. User runs `winget install psmux`; we detect it; agent teams work. Lowest engineering cost of any Windows path. |
| Bundled fake-tmux (our binary) | **De-prioritize** unless psmux adoption fails. psmux does our work for us. |

### Sources cited for Track 2

- `/tmp/isTTY-probe.js` + the 3 Bash transcripts above (this session).
- `/tmp/harzva/ccsource/claude-code-main/src/utils/swarm/backends/TmuxBackend.ts` (full subcommand enumeration).
- `/tmp/harzva/ccsource/claude-code-main/src/utils/swarm/backends/detection.ts:73-76` (no version regex).
- `/tmp/psmux/src/util.rs:197-217, 407-430` (env parser + Claude Code spawn test).
- `/tmp/psmux/src/pane.rs:571`, `/tmp/psmux/src/types.rs:596` (env-translator docstrings).
- `/tmp/psmux/src/main.rs:239-240`, `/tmp/psmux/src/cli.rs:422` (-V handling).
- `/tmp/psmux/docs/features.md` (83 commands claim).
- [github.com/psmux/psmux](https://github.com/psmux/psmux).

## tester — Track 2 addendum: Round-3 send-keys unit test (2026-04-23)

Engineer reported a Round-2→3 bug: Round-2's preload was not spawning the teammate because the real command arrives via `send-keys`, not as a `split-window` positional. My earlier "independently reproduced" Probe B passed on interception but would have silently failed end-to-end — a classic shim-fires-but-no-one-spawns trap. Engineer is right.

Round-3 fix: `tools/preload/fake-tmux-preload.js:275-299` parses `-t <pane>` and command positional in the `send-keys` handler, spawns via `child_process.spawn(SHELL, [SHELL_FLAG, cmd], { detached:true, stdio:['ignore', fd, fd], windowsHide:true }).unref()`.

**Unit test** (`/tmp/appstate-engineer/unit-test-send-keys.js`): load the preload in plain Node, synthesize the exact argv sequence `PaneBackendExecutor` emits. Results:

1. `cp.spawnSync('tmux', [...,'new-session','-d','-s','claude-swarm-test','-x','80','-y','24','-F','#{pane_id}'])` → exit 0, **stdout = `"%0\n"`** (format expected by TmuxBackend.ts:288-318).
2. `cp.spawnSync('tmux', [...,'send-keys','-t','%0','echo "hello from fake teammate pid $$" > marker.txt','Enter'])` → exit 0.
3. Post-spawn: `marker.txt` exists; content = `hello from fake teammate pid 63654` + timestamp. **Detached child ran, pid 63654 distinct from harness.**

Round-3's three invariants confirmed:
- `new-session` stdout is pane id + newline ✅
- `send-keys` spawns a real child ✅
- `has-session` handler returns `{code:1}` (preload line 258, matching TmuxBackend.ts:460) ✅

**Unchanged remaining gap:** end-to-end chain `PaneBackendExecutor.spawn()` → `registerOutOfProcessTeammateTask(paneId='%0', …)` → `AppState.tasks` → TUI presence. Requires interactive TTY — engineer's validation kit under `## engineer — Round 2` is the correct final step.

### Round-4 re-verification

Engineer pushed Round-4 after a lead-requested parser audit: explicit `BOOLEAN_FLAGS={-l,-R,-X,-H,-M}`, `VALUE_FLAGS={-t,-K,-N}`, `KEYPRESS_TOKENS={Enter,C-m,C-c,C-d}` at preload lines 322-324 (replacing Round-3's implicit consumption which had incorrectly treated `-l`/`-X` as value-taking). Rerun of the same unit test: exit 0, `"%0\n"` stdout, fresh child pid 64845, marker written. No regression. Parser matches tmux(1) man synopsis for `send-keys [-FHlMRX] [-K delay] [-N repeat-count] [-t target-pane] key ...`. One cosmetic gap (`-F` boolean, never emitted by Claude Code v2.1.119 — confirmed via engineer's single-call-site grep at `TmuxBackend.ts:157`) flagged and declined as no-op.

### Sources cited

- Code/issues: `anthropics/claude-code#24189` (Ghostty), `#24292` (iTerm2 teammate-mode bug), `#24384` (Windows Terminal), `#26572` (CustomPaneBackend proposal, KILD), `#34150` (psmux proposal), `#34614` (non-interactive short-circuit), `#26244` (Bun SFE Windows isTTY).
- Prior RE: `docs/internal/spawn-research-findings.md:1063-1199` (detectAndGetBackend gate), `:2615-2884` (fake-tmux thread + psmux row), `:1818` (Windows env vars).
- Local: `/tmp/ccteams/src/claude_teams/spawner.py:37-55` (USE_TMUX_WINDOWS semantics); `/tmp/appstate-tester/probe.cjs` + probe runs summarized in §5.
- Web: `claudefa.st/blog/guide/agents/agent-teams`, `dev.to/wong2kim/wmux-...` (wmux — Electron-based alternative, not Claude-compatible), `github.com/queil/psmux` (psmux repo).

---

## engineer — 2026-04-23

Arrived after tester's Windows pass. Converged on same map from an independent angle (string-extracted the v2.1.119 SEA binary directly, decoded `fzH()`/`bp`/`mX6`/`yr`/`Jd`/`BAH`/`d_$`). Confirms tester's finding #1: **only TmuxBackend and ITermBackend exist.** No wezterm/ghostty/zellij/WindowsTerminal/separate-window backend code. The "ghostty"/"WezTerm" strings in the binary are all in an unrelated `TERM_PROGRAM` switch inside the color-depth detector (supports-color). I will not re-derive what tester already proved; noting agreement so lead can discount duplicate scouting.

### Candidate: in-process fake-tmux via BUN_OPTIONS preload

Wrote `/tmp/appstate-engineer/fake-tmux-preload.js` (~200 LoC). Strategy:

1. **Satisfy detection without `tmux -V`** — set `process.env.TMUX='/fake,0,0'` at preload time, before the backend module captures `qr$=process.env.TMUX`. `Jd()` returns true → `fzH()` Priority 1 → TmuxBackend selected, **no shell-out to `tmux`**.
2. **Intercept subsequent `tmux <subcmd>` calls** — monkey-patch `child_process.{execFile,execFileSync,spawn,spawnSync}` AND `Bun.spawn`/`Bun.spawnSync`. Recognized subcmds: `-V`, `new-session`, `split-window`, `send-keys`, `display-message`, `list-panes`, `kill-pane`, `select-layout`, `select-pane`, `set-option`. `split-window <cmd>` extracts the trailing positional (the `CLAUDE_CODE_TEAMMATE_COMMAND` invocation) and `child_process.spawn('/bin/sh', ['-c', cmd], {detached:true})`, returning synthetic `%N\n` pane id — which `PaneBackendExecutor.spawn()` parses → `registerOutOfProcessTeammateTask()` fires → TUI presence.

If the approach works on Linux, it's ~200 LoC JS replacing ~400 LoC bundled shell script (KILD/psmux class). Cross-platform blockers (after reading tester §3–§4):

- **Windows Bun SFE `stdout.isTTY === undefined` gate (issue #26244 / #34150).** Forces in-process BEFORE backend selection. Preload must additionally patch `process.stdout.isTTY = true` (and likely `process.stdin.isTTY`) early. Unclear if Bun SFE allows this — `tty` is built-in. Needs empirical test on Windows; cannot validate from this host.
- **`/bin/sh -c` in the split-window handler is not Windows-portable.** Trivial `if (process.platform === 'win32') use cmd.exe /c` branch needed.
- **`PaneBackendExecutor` command assembly** — tester §Track-3 flags that the cmd looks like `cd X && env VAR=val bin ...`, which `cmd.exe` can't run. If true, the pane-spawn argv is broken on Windows regardless of whether we produce a fake pane id. **I have not verified this in the binary yet** — it's the single load-bearing question for any Windows preload approach. Next step if we pursue this track: dump the decoded `PaneBackendExecutor.spawn()` string and check for `process.platform` branches.

### Ranking (matches tester's, with preload scored)

| Option | Cross-platform reach | LoC we own | External deps | Validation status |
|---|---|---|---|---|
| A. psmux (winget/scoop recommend) | Win-native only | ~10 (detector) | psmux | Unverified by us; trusted by #34150 proposal |
| B. Bundled fake-tmux binary/script (KILD-style) | Linux/mac yes, Win needs ConPTY rewrite | ~400 | none | Exists in wild (KILD), confirmed viable |
| **C. BUN_OPTIONS fake-tmux preload** | **Linux/mac likely; Win blocked on isTTY + cmd.exe + PaneBackendExecutor** | **~250** | none | Candidate written, **untested** |
| D. iTerm2 spoof (`it2` shim) | mac native; Linux/Win faked | ~300 | none | Same shape as B |

**Assessment:** preload (C) is attractive for Linux/mac (no bundled binary, zero user setup). For Windows, psmux (A) remains the cleanest bet. A hybrid — preload on Linux/mac, psmux recommendation on Windows — could ship with least engineering. I do **not** recommend trying to make preload work through the Windows Bun SFE TTY gate until tester or I can empirically confirm it's patchable; the decompiled gate logic needs verification first.

### What I need from lead before proceeding

1. **Green-light empirical validation of the Linux preload.** Requires a user-initiated `claude --teammate-mode tmux` session with `PATH` sanitized of real tmux and `BUN_OPTIONS=--preload=/tmp/appstate-engineer/fake-tmux-preload.js`. I cannot run this myself — it's a live-session action and prior feedback (`feedback_team_launch.md`) says to never start parallel sessions.
2. **Scope decision:** is a Linux/mac-only preload solution acceptable for v1 (with Windows → "install psmux"), or must v1 cover Windows natively? If Windows native is required, I believe the honest answer is "fake-tmux bundled + psmux detector," matching tester's Option A.

### Artifacts

- `/tmp/appstate-engineer/fake-tmux-preload.js` — candidate preload
- Source-of-truth for backend decoded: `/home/rosado/.claude/projects/-home-rosado-Projects-codex-teammate/9f68fd4c-2ac9-4ffa-a18c-52b1cc1ae713/tool-results/bzasz53or.txt` (v2.1.119 binary strings with `fzH`/TmuxBackend/ITermBackend decoded).

---

## engineer — Round 2 (2026-04-23, post-lead-kickoff)

### Probe A — `--teammate-mode` variants: DEFINITIVE

Empirical on this host (`claude --version` = 2.1.119):

```
$ claude --teammate-mode iterm2 --print "x"
error: option '--teammate-mode <mode>' argument 'iterm2' is invalid. Allowed choices are auto, tmux, in-process.
```

Same verbatim rejection for `tmux-external`, `detached`, `none`, `external`, `separate-window`, `split-pane`, `ITerm.app`. **The CLI parser accepts exactly three values: `auto`, `tmux`, `in-process`.** No hidden modes. Probe A closed.

### Probe C — iTerm2 slot: NOT cheaper than tmux

Answered by binary decoding (Round 1 §1; tester §4 Option E). `ITermBackend.createTeammatePaneInSwarmView()` decoded from SEA strings shells out to `it2 session split -v`, `it2 session split -s <id>`, `it2 session run -s <id> <cmd>`, `it2 session list`, `it2 session close -f -s <id>`. `isAvailable()` gates on `it2 session list` exit 0. iTerm2 requires the external `it2` Python CLI — not escape codes. Probe C closed.

### Probe B — BUN_OPTIONS preload: PARTIALLY VALIDATED empirically

Ran the preload against 2.1.119 SEA binary with REAL tmux entirely absent from PATH:

```
$ D=/tmp/appstate-engineer/nopath-bin          # clean bin dir, no tmux
$ env -i HOME=... PATH="$D:/home/rosado/.local/bin" \
    CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1 \
    BUN_OPTIONS="--preload=/tmp/appstate-engineer/fake-tmux-preload.js" \
    /usr/bin/timeout 10 claude --teammate-mode tmux --print "hi"
```

Verbatim preload log output:

```
[preload] loaded at 2026-04-24T03:05:19.791Z pid 60573
[preload] argv ["bun","/$bunfs/root/src/entrypoints/cli.js","--teammate-mode","tmux","--print","hi"]
[preload] TMUX(before) <empty>
[preload] TMUX(after) /tmp/fake-tmux-socket,0,0
[preload] patched Bun.spawn and Bun.spawnSync
[preload] initialization complete
[patch] spawnSync(tmux) ["display-message","-p","#{client_control_mode}"]
[fake-tmux] sub= display-message rest= ["-p","#{client_control_mode}"]
```

**What this empirically proves on this host:**

1. ✅ `BUN_OPTIONS=--preload=` fires inside Claude Code 2.1.119 Bun SEA. Entrypoint is `bun /$bunfs/root/src/entrypoints/cli.js`, so `BUN_OPTIONS` is honored by the embedded Bun runtime.
2. ✅ `process.env.TMUX` injection takes effect before the backend module captures it. Claude Code issued `tmux display-message` → it believes it is inside a tmux session.
3. ✅ `child_process.spawnSync` monkey-patch is reached by Claude Code's tmux calls. The SEA routes at least some `tmux` invocations through Node-compat `child_process`. Our handler returned synthetic OK. No real `tmux` binary is on the sanitized PATH (`command -v tmux` → exit 1).

**Still untested (interactive-only, user-runnable):**

- Whether the pane-backend path proceeds to `split-window <teammate-cmd>` (gated on `getIsNonInteractiveSession()===false` — `--print` forces in-process).
- Whether the fake pane id propagates through `PaneBackendExecutor.spawn()` → `registerOutOfProcessTeammateTask()` → TUI presence.
- Whether parsing of the trailing positional in `split-window` correctly extracts the shim invocation.

I cannot drive an interactive TUI from Bash, and per feedback rules I will not start parallel teams.

### Reconciling with tester's Track 2 isTTY finding

Tester just confirmed (Track 2 §1) that `Object.defineProperty(process.stdout, 'isTTY', {value: true, configurable: true, writable: true})` **succeeds inside the Bun SEA on Linux** — Bun does NOT freeze the tty descriptor. This means my earlier "Windows blocked by isTTY gate" claim is softer than stated: if the same `defineProperty` trick works on the Windows Bun SEA, the `isTTY===undefined` quirk from #26244 becomes patchable from inside the preload. I'll fold an `isTTY` force into the preload unconditionally (cheap no-op on Linux if already set).

Also: tester confirmed **psmux ships a built-in `env VAR=val` → PowerShell translator** and has full coverage of every tmux subcommand Claude Code issues (10-of-10 core, 12-of-12 including resize/capture). My Track-3 Windows-shell concern is already solved upstream by psmux. Windows recommendation stiffens.

### Validation kit for the user (60-second test)

Fresh terminal (does not touch the running session):

```bash
D=/tmp/appstate-engineer/nopath-bin   # already prepared (no tmux)
env -i HOME=$HOME USER=$USER TERM=$TERM SHELL=$SHELL \
    PATH="$D:/home/rosado/.local/bin" \
    CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1 \
    CLAUDE_CODE_TEAMMATE_COMMAND="$HOME/.claude/plugins/cache/claude-anyteam/claude-anyteam/0.1.0/bin/claude-anyteam-spawn-shim" \
    BUN_OPTIONS="--preload=/tmp/appstate-engineer/fake-tmux-preload.js" \
    claude --teammate-mode tmux
```

In the session: ask for `spawn codex-alice on <team> to say hi` (or `Agent(team_name='<team>', name='codex-alice', prompt='say hi')`). Then in a separate shell:

```bash
cat /tmp/appstate-engineer/fake-tmux-preload.log      # should show split-window intercept + spawn
ps -ef | grep claude-anyteam                           # should show adapter running
```

Success = both present, and `@codex-alice` in TUI presence line. Failure modes are diagnostic (see tail of log).

### Revised recommendation

- **Linux/mac:** preload-based fake-tmux is the lowest-cost candidate (~250 LoC JS, no bundled binary, zero user setup beyond env wiring). Firing + env injection + spawn interception empirically confirmed. End-to-end TUI validation needs user to run the kit.
- **Windows:** **Option A (psmux) is now the clear recommendation** given tester's coverage validation. `winget install psmux` + installer detector (~10 LoC). Preload-on-Windows remains a possible future research item (isTTY trick is patchable per tester) but psmux does the work for us today.
- **Hybrid v1 ship:** preload on Linux/mac + psmux detection on Windows. Total engineering ~260 LoC. Beats bundled fake-tmux (~400 LoC + binary packaging). Also beats requiring psmux on all platforms (which would be unnecessary on Linux where preload works).

---

## engineer — Round 3 (2026-04-23): tmux call-sequence spec and preload bug fix

Read `/tmp/harzva/ccsource/claude-code-main/src/utils/swarm/backends/TmuxBackend.ts` in full (764 lines) and the full `PaneBackendExecutor.ts` spawn path. Compiled the complete subcommand sequence Claude Code issues when `teammateMode:'tmux'` is forced on a host with `$TMUX` set (via the preload). This is lead's pre-commit probe #1 — the subcommand spec.

### Complete tmux call sequence (external-session path, single teammate)

All line numbers are `TmuxBackend.ts` unless noted.

1. `tmux -V` — detection (detection.ts isTmuxAvailable). Exit-0 only, no regex on stdout.
2. `tmux -L <sock> has-session -t claude-swarm-<id>` — line 460. Exit-0 = exists; non-zero = create it.
3. If missing: `tmux -L <sock> new-session -d -s claude-swarm-<id> -n swarm-view -P -F '#{pane_id}'` — line 474. **Returns pane_id on stdout** (parsed at line 492 via `result.stdout.trim()`).
4. `tmux -L <sock> list-panes -t claude-swarm-<id>:swarm-view -F '#{pane_id}'` — line 515. Returns pane ids **newline-separated**, parsed via `.trim().split('\n').filter(Boolean)` (line 523).
5. First teammate: uses the initial pane from step 3 (no split). Subsequent: `tmux -L <sock> split-window -t <target> (-v|-h) -P -F '#{pane_id}'` — line 674. **Returns pane_id on stdout** (line 688).

   **NOTE — prior-version bug:** `split-window` does **NOT** carry the teammate command. It only creates an empty pane and returns its id. The teammate command arrives in a later `send-keys` call (step 14). My Round-2 preload incorrectly treated `split-window`'s trailing positional as the teammate command; that was wrong and is now fixed.

6. `tmux -L <sock> select-pane -t <paneId> -P 'bg=default,fg=<color>'` — line 178.
7. `tmux -L <sock> set-option -p -t <paneId> pane-border-style fg=<color>` — line 186.
8. `tmux -L <sock> set-option -p -t <paneId> pane-active-border-style fg=<color>` — line 195.
9. `tmux -L <sock> select-pane -t <paneId> -T <teammateName>` — line 218.
10. `tmux -L <sock> set-option -p -t <paneId> pane-border-format <fmt>` — line 221.
11. First teammate only: `tmux -L <sock> set-option -w -t <windowTarget> pane-border-status top` — line 244.
12. `tmux -L <sock> list-panes -t <windowTarget> -F '#{pane_id}'` — rebalance tiled (line 740). If ≤ 1 pane, skip next. Else: `tmux -L <sock> select-layout -t <windowTarget> tiled` (line 753).
13. **sleep 200ms** (PANE_SHELL_INIT_DELAY_MS, line 33). Internal sleep, no tmux call.
14. **`tmux -L <sock> send-keys -t <paneId> '<spawnCommand>' 'Enter'`** — line 157. `<spawnCommand>` is `cd <cwd> && env VAR=val <binary> --agent-id ... --agent-name ... ...` per `PaneBackendExecutor.ts:153`. **This is THE spawn point for the teammate.**

### Pane-id format Claude Code expects

`splitResult.stdout.trim()` is the parser (TmuxBackend.ts:617, 688). Format is exactly `%N` (the tmux `#{pane_id}` substitution). No `@N` prefix, no session name, no window index. Single token per line; multi-line when multiple panes. My preload emits `"%N\n"` on single calls, `"%0\n%1\n..."` for `list-panes`. Correct.

### isActive poll behavior (lead's probe #3)

`PaneBackendExecutor.ts:329-344`: `isActive(agentId)` checks only the in-memory `spawnedTeammates` Map, does NOT shell out to tmux. Verbatim comment at line 341: *"A more robust check would query the backend for pane existence but that would require adding a new method to PaneBackend"*. The preload therefore does NOT need to synthesize `list-panes` output that reflects child exit. Lead's concern #3 is null on v2.1.119.

### What the old preload got wrong (postmortem)

My Round-2 preload had three bugs the new RE pass revealed:

1. **`split-window` command spawn (CRITICAL).** The old handler tried to parse and spawn the trailing positional of `split-window` as the teammate command. This is wrong — `split-window` has NO teammate command; it just creates an empty pane. The teammate spawn happens later via `send-keys`. The old preload would have spawned tmux-flag-garbage as a shell command and never spawned the real teammate. Fixed in Round-3.
2. **`new-session` response had empty stdout.** But TmuxBackend.ts:492 parses `result.stdout.trim()` to get the initial pane id. Empty string → `paneId = ""` → all downstream `-t ""` calls would fail silently. Fixed.
3. **`has-session` default "OK" would short-circuit session creation.** Old preload returned exit 0 for unknown subcommands including `has-session`, so Claude Code would believe the session existed → skip `new-session` → later queries on a session-that-never-was. New preload returns exit 1 for `has-session`, forcing Claude Code into the create-session branch.

### Round-3 preload artifact

**Committed to `tools/preload/fake-tmux-preload.js`** per lead's instruction (standalone, NOT wired into installer). Also copied to `/tmp/appstate-engineer/fake-tmux-preload.js` for the validation kit path. Re-smoke-tested on this host: preload loads, TMUX injection works, isTTY forced to true, monkey-patch intercepts Claude Code's first `tmux display-message` call identically to Round-2.

Every subcommand response in the new preload is cited (see the long file-header comment in `tools/preload/fake-tmux-preload.js`).

### Validation-kit diff vs lead's version

Lead's kit: `PATH_NOTMUX=$(echo $PATH | tr ':' '\n' | grep -v tmux | paste -sd:)` + inherited env + BUN_OPTIONS preload + `--teammate-mode tmux`.

My kit: `env -i` with explicit vars + preload + `--teammate-mode tmux`. The only functional diff is `CLAUDE_CODE_TEAMMATE_COMMAND` — my kit sets it explicitly; lead's kit inherits from `~/.claude/settings.json` which the claude process reads on startup regardless. **No gap.** Lead's kit is simpler and more realistic; use lead's.

### `--debug` probe (Round-3 ask)

CLI accepts `-d, --debug [filter]` + `--debug-file <path>`. Ran:
```
env -i PATH=<clean> BUN_OPTIONS=--preload=... \
  claude --teammate-mode tmux -d "swarm,backend,pane" \
  --debug-file /tmp/appstate-engineer/claude-debug.log --print "hi"
```
Debug log grep for `backend|swarm|pane|tmux|teammate` — **only one backend-related line fires before exit**:
```
[DEBUG] [TeammateModeSnapshot] Captured from CLI override: tmux
```
No `[BackendRegistry] Starting backend detection...`, no `[TmuxBackend]`, no `[PaneBackendExecutor]`. Consistent with prior finding: `--print` is non-interactive → backend init is deferred until an actual teammate spawn is requested, which `--print` never does. The RE-derived call-sequence spec above remains authoritative. The user's interactive validation run will produce the complete trace in the preload log.

### Windows-branch preload status (Round-3 ask)

Preload has a `process.platform==='win32'` branch that switches shell to `cmd.exe /c` and sets `windowsHide:true`. **Marked UNTESTED and stub-only.** Reason: `PaneBackendExecutor.ts:153` emits a POSIX command string (`cd X && env VAR=val bin --flag`) that `cmd.exe` cannot parse regardless of shell choice (no `env` builtin). A real Windows preload would need to parse-and-translate the incoming command — exactly the ~400-line Rust translator psmux already ships (`psmux/src/pane.rs:560-640`, tester §Track-2 §2). Preload header now includes a 20-line comment explaining the limitation and sketching what a future translator would need to do. Windows recommendation stands as `winget install psmux`; preload Windows branch is a stub that will not spawn teammates correctly.

### Decode citations (Round-3 ask)

- **split-window output parser:** `splitResult.stdout.trim()` — `TmuxBackend.ts:617, 688`. No regex. Format = raw `#{pane_id}` substitution = `%N` literal.
- **list-panes pane-liveness polling during teammate lifetime:** NONE on v2.1.119. `PaneBackendExecutor.isActive(agentId)` (`PaneBackendExecutor.ts:329-344`) only checks the in-memory `spawnedTeammates` Map. Verbatim comment line 341: *"A more robust check would query the backend for pane existence but that would require adding a new method to PaneBackend"*. The preload does not need to track child-exit state.

### Round 4 — send-keys audit (preload hardening)

Lead asked for a paranoid audit of the preload's `send-keys` handler before user validation, and for a decode of post-spawn messaging in case `send-keys` is a repeat-input channel. Two findings.

**Messaging flow — single-call, file-mailbox for everything else.**

`grep -rn 'send-keys\|sendKeys' /tmp/harzva/ccsource/claude-code-main/src/utils/swarm/` returns **exactly one** hit:

```
TmuxBackend.ts:157:    const result = await runTmux(['send-keys', '-t', paneId, command, 'Enter'])
```

That emitter (`TmuxBackend.sendCommandToPane`) is called from **exactly one** site: `PaneBackendExecutor.spawn()` line 158, at teammate-creation time only. All post-spawn messaging is file-based via `writeToMailbox`:

- `PaneBackendExecutor.sendMessage(agentId, message)` — lines 216-230 — `writeToMailbox(...)`, no send-keys.
- `PaneBackendExecutor.terminate(agentId)` — lines 267-275 — shutdown request via `writeToMailbox(...)`, no send-keys.
- Verbatim at `PaneBackendExecutor.ts:214`: *"All teammates (pane and in-process) use the same mailbox mechanism."*

**Conclusion: `send-keys` fires EXACTLY ONCE per teammate on v2.1.119.** Not a repeat-input channel. The preload's `stdio: ['ignore', fd, fd]` is correct — no stdin piping, no fork-bomb risk. If Anthropic ever adds a second call site, the `paneChildren: Map<paneId, {pid}>` guard in the handler degrades to "drop the extra input" rather than re-spawn; if messaging-via-send-keys becomes load-bearing in a future version, we would need to switch to `stdio: ['pipe', ...]` + `child.stdin.write`.

**Argv parser — one real bug fixed.**

Round-3 treated `-l` and `-X` as value-taking flags (consuming the next arg). In tmux `send-keys`, both are value-LESS boolean flags. Claude Code v2.1.119 never emits them, so the bug was latent; any future version that did emit them would have had its real command silently eaten. Fixed via explicit sets in the preload:

```js
const KEYPRESS_TOKENS = new Set(['Enter', 'C-m', 'C-c', 'C-d']);
const BOOLEAN_FLAGS   = new Set(['-F', '-H', '-l', '-M', '-R', '-X']);  // `-F` added per tester for tmux(1) synopsis symmetry; unused by Claude Code v2.1.119
const VALUE_FLAGS     = new Set(['-t', '-K', '-N']);
```

Also added `'C-m'` to `KEYPRESS_TOKENS` defensively (semantically equivalent to `Enter`; `TmuxBackend.ts:157` uses `Enter` literally).

**Unit-level empirical proof (tester).** Tester bypassed the interactive-TTY gate by loading the preload and calling `cp.spawnSync('tmux', ['-L', 'fake-sock', 'new-session', ...])` then `cp.spawnSync('tmux', ['-L', 'fake-sock', 'send-keys', '-t', '%0', '<marker-writing-shell-cmd>', 'Enter'])`. Results: `new-session` returned exit 0 with stdout `"%0\n"` (parser-compatible); `send-keys` returned exit 0 and the detached shell child ran, wrote a marker file with its pid, and the preload `unref`'d cleanly. That is mechanical proof of the spawn chain. Only `registerOutOfProcessTeammateTask(paneId=%0, ...)` → TUI presence remains untested; that chain requires an interactive Claude Code session (user-run validation kit).

**Preload artifact state:** `tools/preload/fake-tmux-preload.js` (Round-4). Mirror at `/tmp/appstate-engineer/fake-tmux-preload.js`. `tools/` untracked (per lead's no-installer-wire rule).

---

## ux-invariants (2026-04-23)

Shared section for the UX-invariant cross-check. Tester writes first; engineer appends their sub-section with `### engineer` heading to avoid clobber.

### tester — TmuxExternalBackend detachment invariant

**Hypothesis:** with `teammateMode:"tmux"` globally, user NOT in tmux, tmux on PATH → TmuxExternalBackend creates a DETACHED session; teammates are invisible in user's terminal.

**Verdict: hypothesis is TRUE for 2.1.119.** Detached by construction on every path. User opt-in to see panes via a banner-hinted `tmux -L … a` command.

### Axis 1 — `-d` flag on every `new-session`

Ran `grep -n "new-session" /tmp/harzva/ccsource/claude-code-main/src/utils/swarm/backends/TmuxBackend.ts` → **exactly two sites**:

- `TmuxBackend.ts:285` — `runTmux(['new-session', '-d', '-s', HIDDEN_SESSION_NAME])`. This is `hidePane()`. `-d` present.
- `TmuxBackend.ts:474-484` — `createExternalSwarmSession()`:
  ```
  runTmuxInSwarm(['new-session', '-d', '-s', SWARM_SESSION_NAME,
                  '-n', SWARM_VIEW_WINDOW_NAME, '-P', '-F', '#{pane_id}'])
  ```
  `-d` present. No other `new-session` call anywhere in the swarm tree (I also grepped `/tmp/harzva/ccsource/claude-code-main/src/` broadly — only `worktree.ts` has non-swarm `new-session` calls which are irrelevant here).

**No non-detached code path.** Both swarm sites are `-d`. `runTmuxInSwarm` adds `-L <getSwarmSocketName()>` (constants.ts:12-14 → `claude-swarm-<pid>`) — a **separate socket** from the user's default tmux, so the swarm session is also isolated from any existing tmux server the user has running.

### Axis 2 — backend selection Priority 1 vs Priority 3 routing

`registry.ts:158-254`, detection at `detection.ts`:

- **Priority 1 — inside tmux (`$TMUX` set).** registry.ts:158-171: `if (insideTmux) { backend = createTmuxBackend(); return ... }`. Same `TmuxBackend` class. Dispatching logic diverges at call time via `TmuxBackend.createTeammatePaneInSwarmView` (line 129-146): `if (insideTmux) return createTeammatePaneWithLeader(...); else return createTeammatePaneExternal(...)`.
- **Priority 3 — tmux available, not inside tmux.** registry.ts:233-249. Returns same `TmuxBackend`, but `isNative = false`, which feeds the swarm banner.

Single class, two runtime paths decided by `$TMUX` presence at spawn time.

### Axis 3 — `tmux attach` / `switch-client` / popups / visible menus

Exhaustive grep across the entire `claude-code-main` source tree:

- `attach-session` / `attach` — `utils/worktree.ts:1379, 1390, 1448, 1455, 1469, 1491, 1496` (worktree flow, **not swarm**), `utils/terminalPanel.ts:147` (terminal panel feature, not swarm), `components/WorktreeExitDialog.tsx:112, 208` (worktree UX hint), `tools/ExitWorktreeTool/*` (worktree only).
  - **Swarm tree (`src/utils/swarm/`, `src/tools/shared/spawnMultiAgent.ts`, `src/components/PromptInput/useSwarmBanner.ts`): zero `attach` calls.** TmuxBackend never attaches or switches to the swarm session.
- `switch-client` — only in `utils/worktree.ts` (worktree flow). Not in swarm.
- `display-popup` / `display-menu` — **zero hits anywhere in the source tree.** Claude Code does not use them.
- `list-panes -a` — zero hits. Only `-t <target>` targeted queries.

**Confirmed: swarm-view is background-only. The user's terminal is never automatically taken over.**

### Axis 3b — the visible UX surface (important)

`components/PromptInput/useSwarmBanner.ts:74-91` — when leader has teammates AND leader is NOT in tmux AND pane-backend is active (`!inProcessMode && !nativePanes`), Claude Code displays this banner in the prompt input:

```
View teammates: `tmux -L claude-swarm-<pid> a`
```

(source: line 88 — `text: \`View teammates: \\\`tmux -L ${getSwarmSocketName()} a\\\`\``).

So the UX is: teammates invisible by default; Claude Code tells the user the exact command to attach and see panes if they want. Opt-in visibility. This actively confirms the hypothesis — if detachment weren't the contract, there'd be no point in showing this banner.

### Axis 2b — Priority 1 (`$TMUX` set): does it split user's current session?

`TmuxBackend.ts:129-146` + `createTeammatePaneWithLeader()` (551-620):
```
const currentPaneId = await this.getCurrentPaneId()   // reads TMUX_PANE env from module load
const windowTarget = await this.getCurrentWindowTarget()
splitResult = await execFileNoThrow(TMUX_COMMAND, ['split-window', '-t', currentPaneId, '-h', '-l', '70%', ...])
```

**Yes — Priority 1 splits the user's current pane.** No `-L <socket>` prefix on the split-window call (line 80: `runTmuxInUserSession` = bare `tmux` without `-L`), so it operates on the **default tmux server** (the user's own). Teammates appear **inline in the user's current tmux window**, consuming 70% width.

This is a materially different UX from Priority 3. Relevant for the user's decision: if they already run Claude inside tmux, teammates will hijack their window real-estate unless they explicitly `export TMUX=` to a detached swarm before launch. Lead and engineer should note this split-UX case clearly in the ship doc — "if you're already in tmux, teammates appear in your current window; if not, they're in a detached swarm with opt-in view."

### Axis 3c — failure modes

Full spawnMultiAgent.ts:1040-1078 (verbatim read earlier):

```
async function handleSpawn(...) {
  if (isInProcessEnabled()) return handleSpawnInProcess(input, context)
  try {
    await detectAndGetBackend()
  } catch (error) {
    if (getTeammateModeFromSnapshot() !== 'auto') throw error
    markInProcessFallback()
    return handleSpawnInProcess(input, context)
  }
  ...
}
```

Per-teammate split-window failure (TmuxBackend.ts:614, 685): `throw new Error('Failed to create teammate pane: ' + splitResult.stderr)`. Swarm-session creation failure (lines 487-489, 539-541): throws `'Failed to create swarm session'` / `'Failed to create swarm-view window'`. These all propagate up to `handleSpawn` and then to the AgentTool caller, which reports the error in the TUI (Agent tool's error-message channel).

Failure-mode table:

| Scenario | User-visible behavior | Source |
|---|---|---|
| tmux not on PATH, `teammateMode:"auto"` | Silent fallback to in-process. Banner suppressed via `markInProcessFallback()`. | spawnMultiAgent.ts:1055-1068 |
| tmux not on PATH, `teammateMode:"tmux"` | Error message shown with install instructions from `getTmuxInstallInstructions()` (registry.ts:259-285). | spawnMultiAgent.ts:1059 throws |
| `new-session` non-zero (socket locked, name clash in swarm socket) | Error thrown: `"Failed to create swarm session: <stderr>"` — propagates to AgentTool → visible in TUI. | TmuxBackend.ts:487-489 |
| `split-window` non-zero mid-spawn | Error thrown: `"Failed to create teammate pane: <stderr>"` — visible. | TmuxBackend.ts:614, 685 |
| `send-keys` non-zero | Error thrown: `"Failed to send command to pane <id>: <stderr>"`. | TmuxBackend.ts:159-163 |
| Terminal corruption risk | None observed in code path. All tmux commands are `-L <sock>` on a dedicated socket (swarm mode) or inherit user's default (inside-tmux mode, splits are explicit `-t <paneid>`). No stdin/stdout passthrough that would corrupt leader's terminal. |

**No hang-without-error path exists** — every tmux failure throws into the surrounding `try/catch` or bubbles to the Agent tool result.

**Silent fallback caveat:** in `teammateMode:"auto"` only, tmux absence silently routes to in-process. User would not know tmux wasn't found unless they check. Worth surfacing in installer docs if we ship tmux-required.

### Axis 4 — cross-platform

- **Linux + tmux (brew/apt):** baseline. All above applies.
- **macOS + tmux (brew):** identical. `TMUX_COMMAND = 'tmux'` (constants.ts:4) — no platform branching. `brew install tmux` puts tmux on PATH. Priority-3 flow identical.
- **Windows + psmux:** verified detachment works correctly.
  - psmux `new-session -d` (`/tmp/psmux/src/commands.rs:1935-2105`): `detached = false` default; `"-d" => { detached = true; }` on line 1961. If `detached`, psmux spawns the session server with `stdin(null), stdout(null), stderr(null)` (lines 2101-2104) and **does NOT call `switch-client`**. Verbatim at line 2117: `if (!detached) { /* switch-client ... */ }`. So with `-d`, the user's foreground terminal is untouched.
  - Detaches identically to tmux. Teammate panes invisible by default.
  - psmux has its own attach UX (`psmux attach -t claude-swarm-...`). The banner hint from `useSwarmBanner.ts:88` would still say `tmux -L ... a` on Windows; under psmux, `tmux` on PATH is the psmux binary, and `tmux -L <sock> a` forwards to `psmux attach` semantics because psmux aliases the `tmux` command name. **Minor: we should confirm this final hop works; if psmux's `tmux` wrapper doesn't forward `-L` to the right socket, the banner hint becomes subtly wrong on Windows.**

**One caveat worth flagging:** I did not run psmux on a Windows host. The `-d` code path is clear in source, but the banner-hint-forwarding for the opt-in view command is unverified and is the one user-facing element that could differ.

### Summary for lead

1. Detachment invariant **HOLDS** for Priority 3 (user-not-in-tmux). `-d` on every swarm `new-session`. No `attach` / `switch-client` / `display-popup` calls anywhere in swarm. Teammates invisible until user runs the opt-in banner command.
2. Priority 1 (user-already-in-tmux) **BREAKS** the invariant — teammates split into user's current window at 70% width. Needs explicit documentation for this case.
3. Failure modes are all `throw`-or-fallback, never hang or corrupt. Silent-fallback only in `auto` mode.
4. psmux on Windows preserves detachment at the source level. Final opt-in-view banner command may need a psmux-specific wording, but that's a small UX polish item, not a blocker.

### Sources

- `/tmp/harzva/ccsource/claude-code-main/src/utils/swarm/backends/TmuxBackend.ts` (285, 474-484, 129-146, 551-620, 614, 685, 159-163)
- `/tmp/harzva/ccsource/claude-code-main/src/utils/swarm/backends/registry.ts:158-254, 259-285`
- `/tmp/harzva/ccsource/claude-code-main/src/utils/swarm/backends/detection.ts:10-38`
- `/tmp/harzva/ccsource/claude-code-main/src/utils/swarm/constants.ts:1-14`
- `/tmp/harzva/ccsource/claude-code-main/src/tools/shared/spawnMultiAgent.ts:1040-1078`
- `/tmp/harzva/ccsource/claude-code-main/src/components/PromptInput/useSwarmBanner.ts:74-91`
- `/tmp/psmux/src/commands.rs:1935-2127`

### engineer — independent RE, full convergence with tester

Cross-checked all four axes independently before reading tester's writeup; we agree on every load-bearing claim. Not re-deriving the spec; noting only the things I independently verified and one element tester surfaced that I missed.

**Independently verified (no conflict):**
- Priority-1/Priority-3 routing through the same `TmuxBackend` class with runtime branching on `isRunningInside()` (`TmuxBackend.ts:136-145`). `createTeammatePaneWithLeader` vs `createTeammatePaneExternal`.
- `new-session -d` in `createExternalSwarmSession` (`TmuxBackend.ts:473-484`).
- Zero `attach-session` / `tmux attach` / `attach -t` / `attachSession` hits in the entire swarm tree (`src/utils/swarm/`, `src/tools/shared/spawnMultiAgent.ts`). Matches tester's broader grep.
- `runTmuxInSwarm` always prefixes `-L <claude-swarm-$pid>` on an isolated socket (`TmuxBackend.ts:87-91`, `constants.ts:12-14`).
- `hidePane` exists but has no caller (`grep -rn 'hidePane' /tmp/harzva/ccsource/claude-code-main/src/` → only declarations). Teammates do NOT get moved to `claude-hidden`.
- Failure-mode scope nuance: `handleSpawn`'s try/catch in `spawnMultiAgent.ts:1053-1069` covers only `detectAndGetBackend()` (binary-detection phase). Errors from `new-session -d`, `split-window`, `send-keys` happen LATER inside `PaneBackendExecutor.spawn()` (`TmuxBackend.ts:487-489, 614, 685, 159-163` throw paths). These bubble to the Agent tool's error channel; there is NO silent in-process fallback on a runtime-tmux failure in explicit `teammateMode:"tmux"`. Tester's failure-mode table matches.

**Thing tester caught that I missed — material:**
- `useSwarmBanner.ts:88` actively surfaces `View teammates: tmux -L claude-swarm-<pid> a` in the leader's prompt banner when `!inProcessMode && !nativePanes`. So the opt-in-view workflow isn't just theoretical — Claude Code tells the user exactly how to observe the detached session. Strengthens the UX claim significantly.

**Validation-kit note:** tester's Axis-3b finding about the banner means the user's validation run can *also* visually confirm the banner text appears in their TUI after spawning a teammate in Priority-3 mode. Worth adding as a fifth validation criterion to our kit:
  5. Claude Code's prompt banner shows `View teammates: tmux -L claude-swarm-<pid> a` (or equivalent with their PID).

**Full convergence.** No follow-ups from me. Tester's Summary §1-§4 stands as the authoritative conclusion.

