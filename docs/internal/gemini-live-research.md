# Gemini CLI live research

_Author: `codex-researcher` (gpt-5.5 xhigh)_
_Started: 2026-04-23_
_Scope: empirical findings from the installed `gemini` binary on this host. Complements `docs/internal/gemini-research-official.md` and `docs/internal/gemini-research-reverse.md`, which are doc/source-based. **Where this file disagrees with those, this file wins for implementation decisions** (binary is ground truth)._

## Status at a glance

| # | Brief question | Status |
|---|----------------|--------|
| 1 | `gemini --version` / `gemini --help` capture | BLOCKED — binary not installed. Install approval requested from `codex-lead`. |
| 2 | `stream-json` event shape probe | BLOCKED — requires installed binary. |
| 3 | `--resume` / `--acp` / `--experimental-acp` support | BLOCKED — requires installed binary. |
| 4 | OAuth session cache — storage + `HOME`-override behavior | **Answered** (empirical, see §4). |
| 5 | MCP tool-name prefix convention on installed version | BLOCKED — requires installed binary. |

Entries below are filled in as they land. Treat BLOCKED rows as _not yet verified_ — implementers must not assume the doc-based research is correct for those items.

---

## 0) Install state on this host (2026-04-23)

- `which gemini` → not found. `PATH` includes `~/.local/bin`, `/usr/local/bin`, Windows `npm` interop, and plugin dirs — none contain `gemini`.
- Full filesystem find (`find / -name gemini -type f`, `-type l`) returned nothing outside `/proc`.
- `npm ls -g --depth=0` at `/usr/local/lib` shows only:
  - `@anthropic-ai/claude-code@2.1.114`
  - `@openai/codex@0.124.0`
  - `claude-anyteam@0.1.0`
- No `@google/gemini-cli` or equivalent package is installed globally.
- `npx --yes @google/gemini-cli --version` was attempted and **denied by policy** ("Code from External / Untrusted Code Integration without explicit user authorization").

**Conclusion:** the binary has been run on this host previously (auth cache exists and was refreshed today, 2026-04-23) but the CLI is not currently installed. A lead-approved install is required before §§1/2/3/5 can be filled in.

---

## 1) `gemini --version` / `gemini --help` — BLOCKED

Pending install. Will capture verbatim stdout/stderr when available, with version number, short-flag vs long-flag shape (`-p` vs `--prompt`, `-r` vs `--resume`, etc.), and the exact spelling of `--output-format` choices.

## 2) `stream-json` event shape — BLOCKED

Pending install. Will run a trivial `gemini -p 'say hello' --output-format stream-json` and record:
- the precise JSON shape of `init`, `message`, `tool_use`, `tool_result`, `result`, `error` events
- presence/absence of any events not enumerated in `gemini-research-official.md §1`
- whether `session_id` is delivered on `init` (plans doc §"Plan A" assumes yes; `codex.py::run` needs this to return in `CodexResult.session_id`)
- whether non-JSON stderr noise leaks to stdout (the tolerant-parser requirement in feasibility doc §"Adjusted risks")

## 3) `--resume`, `--acp`, `--experimental-acp` — BLOCKED

Pending install. Note the doc mismatch already flagged in `gemini-research-official.md §8` and `gemini-research-reverse.md §3.2`: ACP page uses `--acp`, cheatsheet still shows `--experimental-acp`. Installed-binary check is the only way to settle which the `feature_test()` probe must accept. Expect to try both and record which is rejected with what error.

## 5) MCP tool-name prefix — BLOCKED

Pending install. Plans doc assumes `mcp_<server>_<tool>` (hence prompts must reference `mcp_anyteam_send_message`, not `send_message` — feasibility doc critical correction #5). Need to verify empirically by registering a dummy MCP server and observing the tool-name that Gemini advertises.

---

## 4) OAuth session cache — storage + `HOME`-override behavior

**Answered empirically** from the live `~/.gemini/` tree on this host. The binary was authed today (2026-04-23, `oauth_creds.json` last modified 15:46 UTC) before being uninstalled or via `npx`.

### 4.1 Directory layout (what exists on disk after an OAuth session)

```
~/.gemini/
├── google_accounts.json        # {"active": <email>, "old": [...]}
├── installation_id             # anonymous install UUID
├── oauth_creds.json            # mode 600; keys: access_token, refresh_token, id_token, expiry_date, scope, token_type
├── projects.json               # {"projects": {"<abs_cwd>": "<project_slug>"}}
├── settings.json               # {"security": {"auth": {"selectedType": "oauth-personal"}}}
├── state.json                  # UI nudge counts, banner counters — not security-relevant
├── trustedFolders.json         # {"<abs_cwd>": "TRUST_FOLDER"}
├── history/
│   └── <project_slug>/
└── tmp/
    ├── bin/rg                  # bundled ripgrep binary (shipped by the CLI itself)
    └── <project_slug>/
        ├── .project_root       # literal absolute path of the project
        ├── logs.json           # [{sessionId, messageId, type, message, timestamp}, ...]
        └── chats/
            └── session-<YYYY-MM-DDTHH-MM>-<short_uuid>.jsonl
```

**Permissions on credential files:** `oauth_creds.json` is mode 600 (`-rw-------`). `settings.json`, `google_accounts.json`, and the rest are 644. The CLI relies on filesystem mode to protect the refresh token — the adapter's wrapper-config generator must not `chmod 644` anything under a Gemini home it creates.

### 4.2 Project-hash derivation — `sha256(abs_cwd)`

The per-project subdirectory name in `~/.gemini/tmp/` is the short slug from `projects.json` (e.g. `nebius-inference`), **not** the hash. But the JSONL session lines contain a `projectHash` field, which is `sha256(abs_cwd)` of the cwd at the time the session started.

Empirical confirmation on this host:

```
abs_cwd = "/home/rosado/Projects/Nebius Inference"
sha256  = 0ab9c6641cc575f6e8f43b7adb2f6923584df69a748a43accefc4059ee580e78
stored  = 0ab9c6641cc575f6e8f43b7adb2f6923584df69a748a43accefc4059ee580e78   ← matches
```

Implication: if the adapter runs Gemini with `HOME=<adapter_home>` but keeps `cwd` set to the real repo (which feasibility doc critical correction #1 requires), Gemini's session continuity for that repo lives at `<adapter_home>/.gemini/tmp/<slug>/chats/`, keyed by `sha256(abs_cwd)`. A different cwd → different hash → no session reuse. This is the per-project scoping the official doc §4 claims, now confirmed at the on-disk layer.

### 4.3 Session file shape

One JSONL file per session at `.gemini/tmp/<slug>/chats/session-<timestamp>-<short_uuid>.jsonl`. First line is the session header:

```json
{"sessionId":"2a07c579-7101-42a3-9f59-ba98dfec56a9","projectHash":"<sha256>","startTime":"...","lastUpdated":"...","kind":"main"}
```

Subsequent lines are message entries (`{"id":..., "timestamp":..., "type":"user","content":[{"text":"..."}]}`) interleaved with `{"$set":{"lastUpdated":"..."}}` update markers — a log-structured append pattern.

The UUID in the filename matches the `sessionId` inside the file (first 8 chars of the UUID), so session IDs are discoverable from filenames without parsing JSON. That matters for the adapter's `--resume <id>` call — the ID the adapter gets from the `init` event should be usable directly.

### 4.4 `HOME`-override behavior — the critical finding

The feasibility doc's critical correction #1 requires the adapter to point Gemini at an adapter-owned home (`~/.claude/teams/<team>/state/<agent>/gemini/`). On-disk evidence says this **works cleanly** because everything Gemini caches — creds, project slugs, session history — is rooted at `$HOME/.gemini/`. No absolute paths outside `$HOME/.gemini/` appear in any of the files inspected on this host.

**However — critical correction #2 is real and essential:** the wrapper MCP server (`claude_teams` / `claude-anyteam-wrapper`) reads `Path.home()` to find `~/.claude/teams/<team>/...`. If Gemini launches the wrapper with the overridden `HOME=<adapter_home>`, the wrapper will look under `<adapter_home>/.claude/teams/...` which does not exist. Plans doc §"Plan A" and feasibility doc §"Critical corrections #2" both say to solve this by passing `env: {"HOME": <real_home>}` in the `mcpServers.anyteam` entry. This matches Gemini's documented server env handling (reverse research §4.2: "Gemini treats explicitly configured server env vars as trusted and does not redact them").

**Open sub-questions (to verify on the installed binary):**

1. Does Gemini **prefer** `oauth_creds.json` silently over `GEMINI_API_KEY` when both are present? Reverse research §6.5 says env precedence can go wrong with Vertex auth. The Q1(c) startup probe in `codex-gemini-loop`'s brief needs to know the actual precedence order — I cannot determine this from on-disk evidence alone.
2. When `HOME` is overridden and the adapter home's `.gemini/` is **empty** (first-run from a clean state), does Gemini attempt interactive OAuth? Reverse research §1.3 says containerized OAuth needs `-d` for URL copy. The adapter must fail-fast with a clear message (plans doc brief §"codex-gemini-loop" item 3), not block waiting for a browser callback that will never come.
3. Does the OAuth refresh_token get rewritten to the overridden HOME, or does Gemini follow a symlink / env override back to the real `~/.gemini`? I expect the former (everything is rooted at `$HOME/.gemini/`), but this must be confirmed.

### 4.5 OAuth first-run behavior — source read

_Source-based (GitHub HEAD of `google-gemini/gemini-cli`). Needs binary confirmation once install lands — do not treat as ground truth yet._

This subsection answers §4.4 open sub-question #2 ("when the adapter home is empty on first run, does Gemini hang waiting for browser OAuth?") by reading the published source directly.

#### 4.5.1 Non-interactive auth resolution order

Two files drive this: `packages/cli/src/gemini.tsx` (call site) and `packages/cli/src/validateNonInterActiveAuth.ts` (resolver).

Call site (both non-interactive entry points, lines ~310 and ~475):

```ts
const authType = await validateNonInteractiveAuth(
  settings.merged.security.auth.selectedType,
  settings.merged.security.auth.useExternal,
  config,
  settings,
);
```

Inside `validateNonInteractiveAuth`:

```ts
const effectiveAuthType = configuredAuthType || getAuthTypeFromEnv();
```

And `getAuthTypeFromEnv` (`packages/core/src/core/contentGenerator.ts`):

```ts
export function getAuthTypeFromEnv(): AuthType | undefined {
  if (process.env['GOOGLE_GENAI_USE_GCA'] === 'true') return AuthType.LOGIN_WITH_GOOGLE;
  if (process.env['GOOGLE_GENAI_USE_VERTEXAI'] === 'true') return AuthType.USE_VERTEX_AI;
  if (process.env['GEMINI_API_KEY']) return AuthType.USE_GEMINI;
  if (process.env['CLOUD_SHELL'] === 'true' || process.env['GEMINI_CLI_USE_COMPUTE_ADC'] === 'true') return AuthType.COMPUTE_ADC;
  return undefined;
}
```

If both resolve empty, the CLI exits with `ExitCodes.FATAL_AUTHENTICATION_ERROR` and this message:

> Please set an Auth method in your [USER_SETTINGS_PATH] or specify one of the following environment variables before running: GEMINI_API_KEY, GOOGLE_GENAI_USE_VERTEXAI, GOOGLE_GENAI_USE_GCA

**Implication for the adapter:** just dropping `oauth_creds.json` into the adapter home is **not sufficient**. `LOGIN_WITH_GOOGLE` is only picked when `settings.security.auth.selectedType` is set OR `GOOGLE_GENAI_USE_GCA=true`. The live user settings on this host (`~/.gemini/settings.json`) do have `{"security": {"auth": {"selectedType": "oauth-personal"}}}`, which is why the user's normal run works. A clean adapter home without that file would not auto-select OAuth.

#### 4.5.2 Headless-mode detection (`isHeadlessMode`)

From `packages/core/src/utils/headless.ts`:

```ts
export function isHeadlessMode(options?: HeadlessModeOptions): boolean {
  if (process.env['GEMINI_CLI_INTEGRATION_TEST'] !== 'true') {
    const isCI = process.env['CI'] === 'true' || process.env['GITHUB_ACTIONS'] === 'true';
    if (isCI) return true;
  }
  const isNotTTY =
    (!!process.stdin && !process.stdin.isTTY) ||
    (!!process.stdout && !process.stdout.isTTY);
  if (isNotTTY || !!options?.prompt || !!options?.query) return true;
  return process.argv.some((arg) => arg === '-p' || arg === '--prompt');
}
```

Under subprocess invocation from Python (`subprocess.Popen`), **stdout is not a TTY**, so the adapter is always headless from Gemini's perspective, regardless of whether we pass `-p`. In the CLI's own `config.ts` this wires `params.interactive = false` for the non-interactive entry point.

#### 4.5.3 The OAuth decision diamond

From `packages/core/src/code_assist/oauth2.ts` (lines ~136-150):

```ts
if (config.isBrowserLaunchSuppressed()) {
  if (!config.isInteractive()) {
    throw new FatalAuthenticationError(
      'Manual authorization is required but the current session is non-interactive. ' +
        'Please run the Gemini CLI in an interactive terminal to log in, ' +
        'provide a GEMINI_API_KEY, or ensure Application Default Credentials are configured.',
    );
  }
  success = await authWithUserCode(client);  // device code: prints URL, reads stdin for 5 min
} else {
  const webLogin = await authWithWeb(client);  // opens browser, runs local HTTP listener for 5 min
}
```

`isBrowserLaunchSuppressed()` (`packages/core/src/config/config.ts`):

```ts
isBrowserLaunchSuppressed(): boolean {
  return this.getNoBrowser() || !shouldAttemptBrowserLaunch();
}
```

And `this.noBrowser` is set once at CLI startup (`packages/cli/src/config/config.ts:1068`):

```ts
noBrowser: !!process.env['NO_BROWSER'],
```

`shouldAttemptBrowserLaunch()` (`packages/core/src/utils/browser.ts`) returns `false` when: `CI` is set, `DEBIAN_FRONTEND=noninteractive`, `BROWSER=www-browser`, SSH on non-Linux, or Linux without any of `DISPLAY`/`WAYLAND_DISPLAY`/`MIR_SOCKET`. In plain WSL under a subprocess, `DISPLAY` is typically unset → suppressed → we fall into the `!isInteractive()` branch → **FatalAuthenticationError, not a hang**.

But this is not safe to rely on without binary confirmation, because:
- If the user's parent shell has `DISPLAY` exported (X server forwarding, WSLg), `shouldAttemptBrowserLaunch()` returns `true` on Linux. Then Gemini falls into `authWithWeb()`, which launches a local HTTP listener on an ephemeral port and spins a **hard 5-minute timeout** (`packages/core/src/code_assist/oauth2.ts`, lines ~315-323, ~397-503):

  ```ts
  const authTimeoutId = setTimeout(() => {
    abortController.abort(new FatalAuthenticationError('Authorization timed out after 5 minutes.'));
  }, 300000);
  ```

  That's not an indefinite hang, but it is a 5-minute block that will look like one to the outer loop.

- On macOS or non-SSH Linux-with-display machines, the 5-minute timeout is the default behavior for a missing `oauth_creds.json`.

#### 4.5.4 Where creds get written

From `oauth2.ts`, on successful auth:

```ts
await fs.writeFile(filePath, credString, { mode: 0o600 });
```

`filePath` comes from `Storage.getOAuthCredsPath()` — which resolves under `$HOME/.gemini/`. **No override.** No code path I read refuses to write creds based on `HOME` being non-standard. So the adapter home will receive its own `oauth_creds.json` if the auth flow succeeds inside Gemini (i.e. if we let it).

#### 4.5.5 The `NO_BROWSER` escape hatch

Setting `NO_BROWSER=true` in the adapter's environment when it launches Gemini forces `isBrowserLaunchSuppressed()` → `true` → the non-interactive branch → immediate `FatalAuthenticationError` with the clear message above. This is the safe default for the adapter: **the probe should always run Gemini with `NO_BROWSER=true`** so that a missing `oauth_creds.json` fails fast (seconds) rather than with the 5-minute timeout.

(Community confirmation: multiple tracked issues — `#23644`, `#20906`, `#4456`, `#3983` — all document `NO_BROWSER=true` as the headless-auth convention. Issue `#3983` specifically patched device-code prompts to go to stderr, which matters because it means the flag is safe even when we parse stdout as JSONL.)

### 4.6 Revised open sub-questions (for installed-binary confirmation)

Sub-question #1 from §4.4 is **answered** by source read: `GEMINI_API_KEY` beats cached OAuth *unless* `settings.security.auth.selectedType` is non-empty OR `GOOGLE_GENAI_USE_GCA=true`. Needs binary confirmation that the observed behavior matches the source.

Sub-question #2 from §4.4 is **largely answered**: with `NO_BROWSER=true` set, a missing `oauth_creds.json` produces `FatalAuthenticationError` in milliseconds, not a hang. Without `NO_BROWSER`, it either produces `FatalAuthenticationError` (WSL with no `DISPLAY`) or a 5-minute browser-callback timeout (Linux with `DISPLAY`, macOS). Needs binary confirmation on exit code and on whether the error message appears on stderr or stdout (relevant to JSONL parsing).

Sub-question #3 from §4.4 remains **open**: does Gemini rewrite `oauth_creds.json` on token refresh, and if so at the adapter home or the real home? The source review suggests always-adapter-home (all paths go through `Storage`), but a live token-refresh cycle is the only way to confirm.

**Still to confirm on installed binary:**
- exit code Gemini returns when `FatalAuthenticationError` is raised (we need it for the probe to distinguish "no auth" from "model/API error")
- whether the message lands on stderr or leaks into stdout
- whether `--output-format stream-json` emits an `error` event before the process exits, or just exits with a non-zero code and no event
- whether `gemini -p 'ok'` is a cheap enough probe, or whether we need a lighter-weight way to verify auth (e.g. does `gemini --help` hit the auth path at all? — my read of `gemini.tsx` says no, but confirm)

### 4.7 Q1(c) startup auth probe — proposed pseudocode

_Proposal, pending binary confirmation. Lives at `src/claude_anyteam/backends/gemini/cli.py` (task T-L3)._

```python
def probe_gemini_auth(
    gemini_binary: str,
    adapter_home: Path,
    real_home: Path,
    model: str,
) -> AuthProbeResult:
    """Decide which auth mode the adapter will run Gemini in.

    Returns one of:
      AuthProbeResult(mode="api_key", env={"GEMINI_API_KEY": ...})
      AuthProbeResult(mode="oauth_seeded", env={"NO_BROWSER": "true"})
      AuthProbeResult(mode="blocked", error=<human-readable message>)

    Never hangs. Total budget ~10 s across both probe attempts.
    """
    # Step 1: API key path. Trust the env var — cheapest reliable signal.
    # (Verified by source: GEMINI_API_KEY → USE_GEMINI without OAuth fallback.)
    if os.environ.get("GEMINI_API_KEY"):
        return AuthProbeResult(mode="api_key", env={})  # inherit parent env

    # Step 2: OAuth-seeded path. Only viable if the user has already authed
    # with `gemini` on this host. Seed the adapter home so Gemini's
    # Storage.getOAuthCredsPath() finds creds, and so settings.security.auth.selectedType
    # resolves to 'oauth-personal' (LOGIN_WITH_GOOGLE).
    real_gemini = real_home / ".gemini"
    creds = real_gemini / "oauth_creds.json"
    settings = real_gemini / "settings.json"
    if creds.is_file() and settings.is_file():
        adapter_gemini = adapter_home / ".gemini"
        adapter_gemini.mkdir(parents=True, exist_ok=True)
        # Copy preserving mode 600 on creds (critical; do not use shutil.copy)
        shutil.copyfile(creds, adapter_gemini / "oauth_creds.json")
        os.chmod(adapter_gemini / "oauth_creds.json", 0o600)
        shutil.copyfile(settings, adapter_gemini / "settings.json")
        # google_accounts.json is not required for auth resolution; copy if present
        # for completeness (feeds telemetry/account-switcher).
        acct = real_gemini / "google_accounts.json"
        if acct.is_file():
            shutil.copyfile(acct, adapter_gemini / "google_accounts.json")

        # Verify with a minimal probe. NO_BROWSER=true prevents the 5-minute
        # browser-callback hang if creds are missing/stale; HOME pinned to the
        # adapter home isolates any refresh writes.
        env = {
            **os.environ,
            "HOME": str(adapter_home),
            "NO_BROWSER": "true",
        }
        result = subprocess.run(
            [gemini_binary, "-p", "ok", "--output-format", "json",
             "--model", model],
            env=env,
            cwd=str(real_home),  # cwd is irrelevant for auth; any valid dir
            capture_output=True,
            text=True,
            timeout=10,  # hard ceiling; token refresh should finish in <3s
        )
        if result.returncode == 0:
            return AuthProbeResult(mode="oauth_seeded",
                                   env={"NO_BROWSER": "true"})
        # Probe failed. Fall through to blocked with the captured stderr so
        # the operator can see the actual Gemini error.
        oauth_err = (result.stderr or result.stdout or "").strip()
    else:
        oauth_err = (
            f"No cached Google OAuth at {creds}. "
            "Run `gemini` once interactively to sign in, or set GEMINI_API_KEY."
        )

    # Step 3: both paths exhausted.
    return AuthProbeResult(
        mode="blocked",
        error=(
            "Gemini adapter cannot authenticate.\n"
            f"  API key:    GEMINI_API_KEY is not set.\n"
            f"  OAuth:      {oauth_err}\n"
            "Set GEMINI_API_KEY, or run `gemini` once to cache OAuth creds."
        ),
    )
```

Design notes for `codex-gemini-loop` implementers:

- **Always pass `NO_BROWSER=true` to the probe.** The 5-minute browser-callback timeout (§4.5.3) is the only way this function hangs. `NO_BROWSER` turns that into a sub-second fail-fast.
- **Do not run the probe on every task.** Run it once per `run()` at loop startup (the spot the brief calls out). If it returns `oauth_seeded`, cache the env dict on `GeminiLoopState` and pass it unchanged to every `run_exec()` call.
- **Do not pass `NO_BROWSER=true` to real task invocations** unless OAuth was the selected mode. Under API-key mode the flag is a no-op but still safe; under OAuth it's required for any subsequent re-auth to fail fast instead of hanging.
- **The probe's `cwd` is deliberately `real_home`, not the repo.** Keeping cwd out of the repo avoids creating a spurious session JSONL under the adapter home's per-project tree for a throwaway "ok" prompt.
- **10-second timeout is belt-and-suspenders.** With `NO_BROWSER=true` and a fresh `oauth_creds.json`, the observed cold start for `gemini -p 'ok' --output-format json` in community reports is 2-3 s. The 10 s ceiling catches a slow network refresh of an expired token. If the `expiry_date` from on-disk creds is already ~3500 s in the past and offline, expect this to fail — that's the correct outcome.
- **Copy, don't symlink.** A symlink back to `~/.gemini/oauth_creds.json` would cause Gemini's token-refresh writes to mutate the user's real home. Copying into the adapter home isolates any refresh side-effects, at the cost of the user needing to re-run interactive `gemini` once per adapter-home recreation to get a fresh refresh_token.

---

## Revision log

- 2026-04-23 — initial doc. Blocked on install approval for §§1/2/3/5. §4 answered empirically from on-disk state; §4.5 open sub-questions flagged for installed-binary follow-up.
- 2026-04-23 — added §4.5 (OAuth first-run source read), §4.6 (revised open sub-questions), §4.7 (Q1(c) probe pseudocode). Source-based findings from HEAD of `google-gemini/gemini-cli`: non-interactive auth resolution order, `isHeadlessMode` logic, `NO_BROWSER` behavior, 5-minute browser-callback timeout, creds-write path. All marked as needing binary confirmation.
