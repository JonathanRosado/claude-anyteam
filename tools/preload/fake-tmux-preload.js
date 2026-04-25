// fake-tmux-preload.js
//
// BUN_OPTIONS=--preload= shim that makes Claude Code 2.1.119 believe it is inside
// a tmux session and running a real tmux binary, WITHOUT installing tmux and
// WITHOUT shelling out to anything multiplexer-specific. The goal is to let
// Claude Code's pane-backend path complete (so `registerOutOfProcessTeammateTask`
// fires and the Codex-backed teammate appears in the TUI presence line) on hosts
// that don't have tmux — Linux/mac/no-tmux and, in theory, Windows.
//
// Usage:
//   BUN_OPTIONS="--preload=<this-file>" claude --teammate-mode tmux
//
// This file is a STANDALONE artifact. It is NOT wired into the claude-anyteam
// installer; commit to that only after end-to-end TUI-presence validation.
//
// --------------------------------------------------------------------------
// Contract with Claude Code — every subcommand response documented below is
// derived from reading the decoded v2.1.119 pane-backend source (Harzva RE
// tree, `src/utils/swarm/backends/TmuxBackend.ts` and `PaneBackendExecutor.ts`).
// If any format is wrong, Claude Code will bail silently. Citations per entry.
// --------------------------------------------------------------------------
//
// Call sequence when `teammateMode:'tmux'` is forced on a no-tmux host with
// this preload active, derived from TmuxBackend.ts (lines cited):
//
// (detection)
//   tmux -V                                        → exit 0 (detection.ts isTmuxAvailable)
//
// (external-session pre-flight, because $TMUX was faked)
//   tmux -L <sock> has-session -t claude-swarm     → exit non-zero if we want
//                                                     Claude Code to create it.
//                                                     We return exit 1, empty stdout.
//                                                     (TmuxBackend.ts:459-462)
//   tmux -L <sock> new-session -d -s claude-swarm -n swarm-view -P -F '#{pane_id}'
//                                                   → exit 0, stdout = "%0\n"
//                                                     pane-id is what
//                                                     `createExternalSwarmSession`
//                                                     later treats as the
//                                                     "first pane" (TmuxBackend.ts:474-492).
//   tmux -L <sock> list-panes -t claude-swarm:swarm-view -F '#{pane_id}'
//                                                   → stdout = "%0\n"
//                                                     (TmuxBackend.ts:515-522)
//
// (first teammate takes the existing first pane — NO split-window call)
//   tmux -L <sock> set-option -p -t %0 pane-border-style fg=red  → exit 0
//     (and two more set-option calls with pane-active-border-style / format)
//   tmux -L <sock> select-pane -t %0 -P 'bg=default,fg=red'      → exit 0
//   tmux -L <sock> select-pane -t %0 -T alice                    → exit 0
//   tmux -L <sock> set-option -w -t claude-swarm:swarm-view pane-border-status top
//                                                                → exit 0
//   tmux -L <sock> list-panes -t claude-swarm:swarm-view -F '#{pane_id}'
//                                                   → "%0\n"   (rebalance tiled skips if 1 pane)
//
// (now the teammate command is sent via send-keys — this is THE spawn point)
//   tmux -L <sock> send-keys -t %0 '<spawnCommand>' Enter         → exit 0
//      where <spawnCommand> = `cd <cwd> && env VAR=... <binary> --agent-id ... --agent-name ... ...`
//      (PaneBackendExecutor.ts:153; TmuxBackend.ts:151-164).
//      THIS is where we actually spawn the teammate child.
//
// (subsequent teammates — split-window path)
//   tmux -L <sock> split-window -t %0 -v -P -F '#{pane_id}'       → exit 0, stdout = "%N\n"
//      (TmuxBackend.ts:672-682). No command on split-window — the command is
//      sent later via send-keys.
//
// IMPORTANT: split-window does NOT carry the teammate command. The earlier
// version of this preload had that bug — DO NOT RE-INTRODUCE.
//
// --------------------------------------------------------------------------
// Step 0 (FIRST): inject TMUX + force isTTY before any module load.
// --------------------------------------------------------------------------

'use strict';

// Pre-capture invariants. These MUST happen before the embedded Claude Code
// module graph reads them. The `--preload` contract runs this script to
// completion before the user entrypoint loads; both assignments are safe.
if (!process.env.TMUX) {
  // Any non-empty value satisfies Jd()/isInsideTmuxSync (detection.ts reads
  // process.env.TMUX captured at module load). Value is not validated.
  process.env.TMUX = '/tmp/fake-tmux-socket,0,0';
}
if (!process.env.TMUX_PANE) {
  process.env.TMUX_PANE = '%0';
}

// Force isTTY=true on all three std streams. Claude Code's main.tsx:806 uses
// `!process.stdout.isTTY` to force in-process mode (non-interactive). Under
// Bun SEA (Linux verified; Windows by extension per issue #26244), isTTY can
// start as `undefined` even for a real TTY; undefined is falsy → in-process
// → pane backend never runs. defineProperty with {configurable, writable} is
// confirmed to persist inside Bun SEA.
function forceIsTTY(stream) {
  try {
    Object.defineProperty(stream, 'isTTY', { value: true, configurable: true, writable: true });
  } catch (_) {
    try { stream.isTTY = true; } catch (_) {}
  }
}
forceIsTTY(process.stdout);
forceIsTTY(process.stdin);
forceIsTTY(process.stderr);

// --------------------------------------------------------------------------
// Step 1: logging + deps.
// --------------------------------------------------------------------------

const fs = require('fs');
const path = require('path');
const child_process = require('child_process');

const SCRATCH = process.env.FAKE_TMUX_SCRATCH || '/tmp/appstate-engineer';
try { fs.mkdirSync(SCRATCH, { recursive: true }); } catch (_) {}
const LOG = SCRATCH + '/fake-tmux-preload.log';
function log(...args) {
  try {
    fs.appendFileSync(LOG, args.map(a => typeof a === 'string' ? a : JSON.stringify(a)).join(' ') + '\n');
  } catch (_) {}
}

log('[preload] loaded at', new Date().toISOString(), 'pid', process.pid, 'platform', process.platform);
log('[preload] argv', process.argv);
log('[preload] TMUX', process.env.TMUX, 'isTTY stdout=', process.stdout.isTTY, 'stdin=', process.stdin.isTTY);

// ---------------------------------------------------------------------------
// Windows branch — sketched, UNTESTED, NOT a production path.
//
// The teammate-spawn command string that PaneBackendExecutor.ts:153 emits is
// purely POSIX: `cd <cwd> && env VAR=val <binary> --agent-id ... --agent-name ...`.
// `cmd.exe` cannot parse this (no `env` builtin; `&&` exists but `env` does not).
// A real Windows fallback would need to rewrite the command before spawn:
//   * parse `cd <cwd> && ` prefix → pass via {cwd} option of spawn
//   * parse `env VAR=val VAR2=val2 <binary> <args...>` tail → merge into env
//     and invoke <binary> <args...> directly (argv, not shell)
//   * handle backslashes/quoting consistent with the shellQuote'd input
// psmux already does this translation (`psmux/src/pane.rs:560-640`; tester §4).
// Our Windows recommendation is therefore `winget install psmux`, NOT this
// preload. The sh/cmd switch below exists so the preload doesn't crash on
// import under Windows; it WILL fail to spawn the teammate correctly because
// the command string is POSIX-shaped. If psmux is ever insufficient, extend
// spawnTeammateDetached with the translator sketched above.
// ---------------------------------------------------------------------------
const IS_WIN = process.platform === 'win32';
const SHELL = IS_WIN ? (process.env.COMSPEC || 'cmd.exe') : '/bin/sh';
const SHELL_FLAG = IS_WIN ? '/c' : '-c';

// --------------------------------------------------------------------------
// Step 2: synthetic pane state.
// --------------------------------------------------------------------------

// Counter for pane IDs. Format is `%N` matching the real tmux `#{pane_id}`
// format (TmuxBackend.ts consumes `splitResult.stdout.trim()` as a token).
let nextPaneIdNum = 0;
function allocPaneId() { return '%' + (nextPaneIdNum++); }

// The external-swarm "first pane" is pre-allocated by `new-session`. Later
// `send-keys -t <firstPaneId>` arrives with the teammate command, at which
// point we actually spawn the child process.
const knownPanes = new Set();   // all pane ids we have handed out
const paneChildren = new Map(); // paneId -> { pid }

function spawnTeammateDetached(command, paneId) {
  const slug = paneId.replace(/[^A-Za-z0-9]/g, '_');
  const outFile = path.join(SCRATCH, 'pane' + slug + '.out');
  const errFile = path.join(SCRATCH, 'pane' + slug + '.err');
  let outFd, errFd;
  try {
    outFd = fs.openSync(outFile, 'a');
    errFd = fs.openSync(errFile, 'a');
    const child = child_process.spawn(SHELL, [SHELL_FLAG, command], {
      detached: true,
      stdio: ['ignore', outFd, errFd],
      windowsHide: true,
    });
    child.unref();
    // Close FDs in parent — child retains its own copies via dup2 on fork.
    try { fs.closeSync(outFd); } catch (_) {}
    try { fs.closeSync(errFd); } catch (_) {}
    paneChildren.set(paneId, { pid: child.pid });
    log('[spawn] pid=', child.pid, 'paneId=', paneId, 'cmd=', command);
    return child.pid;
  } catch (e) {
    if (outFd != null) try { fs.closeSync(outFd); } catch (_) {}
    if (errFd != null) try { fs.closeSync(errFd); } catch (_) {}
    log('[spawn] FAILED for', paneId, e && e.message);
    return -1;
  }
}

// --------------------------------------------------------------------------
// Step 3: tmux argv dispatcher.
// --------------------------------------------------------------------------

// Strip global tmux options: `-L <name>`, `-S <path>`, `-f <file>`, single-tokens
// `-C` `-CC` `-u` `-v` `-2`. `-V` is a command-equivalent in itself.
function parseTmuxArgs(argv) {
  let i = 0;
  while (i < argv.length) {
    const a = argv[i];
    if (a === '-L' || a === '-S' || a === '-f') { i += 2; continue; }
    if (a === '-C' || a === '-CC' || a === '-u' || a === '-v' || a === '-2') { i += 1; continue; }
    if (a === '-V') return { sub: '-V', rest: argv.slice(i + 1) };
    break;
  }
  return { sub: argv[i], rest: argv.slice(i + 1) };
}

// Parse a subcommand's flag portion into { flags, positionals }. Flags with
// values are consumed with their value. Positionals are everything else in
// order. Only the flag letters Claude Code actually uses are modeled.
const FLAGS_WITH_VALUE = new Set(['-t', '-s', '-F', '-c', '-e', '-l', '-b', '-n', '-T', '-P', '-x', '-y']);
//   NOTE: `-P` in tmux takes NO value in our context — it's a single-token
//   "print pane id after creation" flag on new-session/new-window/split-window.
//   But we are lenient and treat it as value-less for those subcommands.
const FLAGS_NO_VALUE = new Set(['-P', '-v', '-h', '-d', '-p', '-w']);

function splitFlagsPositionals(rest, valueFlagsOverride) {
  // Support per-subcommand flag table (some subcommands use `-P` as value-less).
  const flags = {};
  const positionals = [];
  for (let j = 0; j < rest.length; j++) {
    const a = rest[j];
    if (FLAGS_NO_VALUE.has(a)) { flags[a] = true; continue; }
    if ((valueFlagsOverride || FLAGS_WITH_VALUE).has(a)) {
      flags[a] = rest[j + 1];
      j += 1;
      continue;
    }
    positionals.push(a);
  }
  return { flags, positionals };
}

function fakeTmuxRun(argv) {
  const { sub, rest } = parseTmuxArgs(argv);
  log('[tmux]', sub, rest);
  switch (sub) {

    case '-V':
      // detection.ts isTmuxAvailable only checks exit 0. No regex on stdout.
      return { code: 0, stdout: 'tmux 3.4\n', stderr: '' };

    case 'has-session': {
      // TmuxBackend.ts:460 — returns true iff exit 0. Returning non-zero forces
      // new-session to run, which is what we want.
      return { code: 1, stdout: '', stderr: "can't find session" };
    }

    case 'new-session': {
      // TmuxBackend.ts:474-492 — expects stdout = pane_id when `-P -F '#{pane_id}'`.
      // `-d` detached, `-s <name>` session, `-n <name>` window.
      const paneId = allocPaneId();
      knownPanes.add(paneId);
      return { code: 0, stdout: paneId + '\n', stderr: '' };
    }

    case 'new-window': {
      // TmuxBackend.ts:527-545 — same shape as new-session for our purposes.
      const paneId = allocPaneId();
      knownPanes.add(paneId);
      return { code: 0, stdout: paneId + '\n', stderr: '' };
    }

    case 'split-window': {
      // TmuxBackend.ts:571-611, 672-682 — always used with `-P -F '#{pane_id}'`.
      // Never carries the teammate command; that arrives later via send-keys.
      const paneId = allocPaneId();
      knownPanes.add(paneId);
      return { code: 0, stdout: paneId + '\n', stderr: '' };
    }

    case 'list-panes': {
      // TmuxBackend.ts:439, 515, 584, 660, 740 — always with `-F '#{pane_id}'`.
      // Consumed as `.stdout.trim().split('\n').filter(Boolean)`. Line-separated.
      if (knownPanes.size === 0) {
        // If no panes known yet (shouldn't happen after new-session) return one
        // anyway to avoid a downstream NPE on `panes[0]`.
        return { code: 0, stdout: '%0\n', stderr: '' };
      }
      return { code: 0, stdout: Array.from(knownPanes).join('\n') + '\n', stderr: '' };
    }

    case 'list-windows': {
      // TmuxBackend.ts:503-509 — `-F '#{window_name}'`. Return just swarm-view
      // so the caller skips the "create window" branch.
      return { code: 0, stdout: 'swarm-view\n', stderr: '' };
    }

    case 'send-keys': {
      // send-keys invocation contract, derived from a full grep of the swarm
      // tree: there is EXACTLY ONE site in Claude Code v2.1.119 that emits
      // `send-keys`, at `TmuxBackend.ts:157`:
      //     runTmux(['send-keys', '-t', paneId, command, 'Enter'])
      // Always 5-arg (after the sub), always with trailing literal 'Enter',
      // never with `-l` (literal-keys), `-R` (reset-mode), `-X` (command-mode),
      // `-K` (key-table), `-N` (repeat), `-H` (hex), `-M` (mouse). Source:
      // `grep -rn 'send-keys\|sendKeys' .../utils/swarm/` returns a single hit.
      //
      // That single call site is invoked from `PaneBackendExecutor.spawn()`
      // (PaneBackendExecutor.ts:158) EXACTLY ONCE per teammate, at creation.
      // Subsequent messaging (lead→teammate, shutdown, permission responses)
      // all go through `writeToMailbox` (file-based), NOT send-keys. Comment
      // at PaneBackendExecutor.ts:214 is explicit: "All teammates (pane and
      // in-process) use the same mailbox mechanism." So the stdin-piping
      // concern for repeated send-keys into a live teammate is not a concern
      // on v2.1.119 — `stdio:['ignore', ...]` is correct.
      //
      // Parsing rules (restricted to the one actual invocation shape):
      //   argv = ['-t', <paneId>, <command>, 'Enter']
      //   Extract -t value → paneId. Extract first non-flag non-keypress
      //   positional → command. Ignore trailing keypress tokens ('Enter',
      //   'C-m', 'C-c', 'C-d'). Defensive: tolerate `-l` / `-X` as value-less
      //   boolean flags (value-taking in some tmux versions' docs but
      //   value-less in `send-keys`; Claude Code doesn't use them anyway).
      //
      // Spawn-once guard: we track `paneChildren: Map<paneId, {pid}>`. First
      // send-keys on a pane → spawn the teammate detached. Subsequent
      // send-keys on the same pane → log + no-op (belt-and-suspenders only,
      // since the code path above proves this cannot happen on v2.1.119).

      let paneId = null;
      let command = null;
      const KEYPRESS_TOKENS = new Set(['Enter', 'C-m', 'C-c', 'C-d']);
      const BOOLEAN_FLAGS = new Set(['-F', '-H', '-l', '-M', '-R', '-X']);
      const VALUE_FLAGS = new Set(['-t', '-K', '-N']);
      for (let j = 0; j < rest.length; j++) {
        const a = rest[j];
        if (a === '-t') { paneId = rest[j + 1]; j += 1; continue; }
        if (VALUE_FLAGS.has(a)) { j += 1; continue; }
        if (BOOLEAN_FLAGS.has(a)) continue;
        if (KEYPRESS_TOKENS.has(a)) continue;
        if (command === null) command = a;
      }
      if (paneId && command && !paneChildren.has(paneId)) {
        spawnTeammateDetached(command, paneId);
      } else if (paneId && paneChildren.has(paneId)) {
        log('[send-keys] already-spawned (expected no-op on v2.1.119):', paneId);
      } else {
        log('[send-keys] no spawn — paneId=', paneId, 'command=', command);
      }
      return { code: 0, stdout: '', stderr: '' };
    }

    case 'kill-pane': {
      // TmuxBackend.ts:273 — `kill-pane -t <paneId>`.
      for (let j = 0; j < rest.length; j++) {
        if (rest[j] === '-t') {
          const paneId = rest[j + 1];
          if (paneId) {
            knownPanes.delete(paneId);
            const child = paneChildren.get(paneId);
            if (child && child.pid > 0) {
              try { process.kill(child.pid, 'SIGTERM'); } catch (_) {}
            }
            paneChildren.delete(paneId);
          }
          break;
        }
      }
      return { code: 0, stdout: '', stderr: '' };
    }

    // All these are best-effort OK — Claude Code only checks exit code.
    case 'display-message':
    case 'set-option':
    case 'select-layout':
    case 'select-pane':
    case 'resize-pane':
    case 'break-pane':
    case 'join-pane':
    case 'capture-pane':
      return { code: 0, stdout: '', stderr: '' };

    default:
      // Unknown subcommand — log and return OK. If it matters, validation will
      // reveal it in the log and we can add a dedicated handler.
      log('[tmux] UNHANDLED', sub, rest);
      return { code: 0, stdout: '', stderr: '' };
  }
}

// --------------------------------------------------------------------------
// Step 4: monkey-patch spawn/execFile for `tmux` argv.
// --------------------------------------------------------------------------

function isTmux(file) {
  if (!file) return false;
  const base = path.basename(String(file));
  return base === 'tmux' || base === 'tmux.exe';
}

const origExecFile = child_process.execFile;
child_process.execFile = function (file, args, options, cb) {
  if (isTmux(file)) {
    log('[patch] execFile(tmux)', args);
    if (typeof args === 'function') { cb = args; args = []; }
    if (typeof options === 'function') { cb = options; options = {}; }
    const res = fakeTmuxRun(args || []);
    const err = res.code === 0 ? null : Object.assign(new Error('tmux exit ' + res.code), { code: res.code });
    if (cb) process.nextTick(() => cb(err, res.stdout, res.stderr));
    return { on() {}, stdout: null, stderr: null, pid: -1 };
  }
  return origExecFile.apply(this, arguments);
};

const origSpawn = child_process.spawn;
child_process.spawn = function (file, args, options) {
  if (isTmux(file)) {
    log('[patch] spawn(tmux)', args);
    const res = fakeTmuxRun(args || []);
    const { EventEmitter } = require('events');
    const { Readable } = require('stream');
    const fake = new EventEmitter();
    fake.pid = -1;
    fake.stdout = Readable.from([Buffer.from(res.stdout)]);
    fake.stderr = Readable.from([Buffer.from(res.stderr)]);
    fake.stdin = { end() {}, write() { return true; } };
    fake.kill = () => true;
    process.nextTick(() => {
      fake.emit('exit', res.code, null);
      fake.emit('close', res.code, null);
    });
    return fake;
  }
  return origSpawn.apply(this, arguments);
};

const origSpawnSync = child_process.spawnSync;
child_process.spawnSync = function (file, args, options) {
  if (isTmux(file)) {
    log('[patch] spawnSync(tmux)', args);
    const res = fakeTmuxRun(args || []);
    return {
      pid: -1,
      status: res.code,
      signal: null,
      stdout: Buffer.from(res.stdout),
      stderr: Buffer.from(res.stderr),
      output: [null, res.stdout, res.stderr],
    };
  }
  return origSpawnSync.apply(this, arguments);
};

const origExecFileSync = child_process.execFileSync;
child_process.execFileSync = function (file, args, options) {
  if (isTmux(file)) {
    log('[patch] execFileSync(tmux)', args);
    const res = fakeTmuxRun(args || []);
    if (res.code !== 0) {
      throw Object.assign(new Error('tmux exit ' + res.code), {
        status: res.code,
        stdout: Buffer.from(res.stdout),
        stderr: Buffer.from(res.stderr),
      });
    }
    return Buffer.from(res.stdout);
  }
  return origExecFileSync.apply(this, arguments);
};

// Bun.spawn / Bun.spawnSync mirror — Claude Code SEA may route through either.
try {
  if (typeof Bun !== 'undefined' && Bun && Bun.spawn) {
    const origBunSpawn = Bun.spawn.bind(Bun);
    Bun.spawn = function (arg1, arg2) {
      let cmd = null;
      if (Array.isArray(arg1)) cmd = arg1;
      else if (arg1 && Array.isArray(arg1.cmd)) cmd = arg1.cmd;
      if (cmd && isTmux(cmd[0])) {
        log('[patch] Bun.spawn(tmux)', cmd.slice(1));
        const res = fakeTmuxRun(cmd.slice(1));
        return {
          pid: -1,
          exitCode: res.code,
          exited: Promise.resolve(res.code),
          stdout: new Response(res.stdout).body,
          stderr: new Response(res.stderr).body,
          stdin: { write() {}, end() {}, flush() {}, close() {} },
          kill() {},
          ref() {}, unref() {},
        };
      }
      return origBunSpawn(arg1, arg2);
    };
    if (Bun.spawnSync) {
      const origBunSpawnSync = Bun.spawnSync.bind(Bun);
      Bun.spawnSync = function (arg1, arg2) {
        let cmd = null;
        if (Array.isArray(arg1)) cmd = arg1;
        else if (arg1 && Array.isArray(arg1.cmd)) cmd = arg1.cmd;
        if (cmd && isTmux(cmd[0])) {
          log('[patch] Bun.spawnSync(tmux)', cmd.slice(1));
          const res = fakeTmuxRun(cmd.slice(1));
          return {
            pid: -1,
            exitCode: res.code,
            success: res.code === 0,
            stdout: Buffer.from(res.stdout),
            stderr: Buffer.from(res.stderr),
          };
        }
        return origBunSpawnSync(arg1, arg2);
      };
    }
    log('[preload] patched Bun.spawn / Bun.spawnSync');
  } else {
    log('[preload] Bun global not present at preload time');
  }
} catch (e) {
  log('[preload] Bun patch error', e && e.message);
}

log('[preload] initialization complete');
