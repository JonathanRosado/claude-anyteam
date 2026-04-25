# Installer onboarding — UX design

Audience: implementer of task #4. All copy below is **drop-in**. File:line anchors point at `code-surface.md` and `installer.py` / `cli.py` on `main` after v0.4.0.

## Source synthesis
- `code-surface.md` — probe shapes, file:line insertion points, sign-in artifact paths.
- `empirical-flow.md` — current confusion: success-on-blank, no sign-in detection, scattered warnings, "Detected" misreads as "ready".

## Design principles (locked)
1. **Lead with state, not receipt.** The user wants to know *am I ready?* before *what did you write?* — invert the current message order.
2. **ELI5 only when needed.** Default tone is the existing terse-factual cadence. The 3-line explainer renders only when no provider is ready.
3. **Match existing aesthetic.** Plain text, no ANSI color, 2-space indented continuations, 4-space indented copy-paste commands. Status icons (`✅` / `❌` / `⚠️`) are the only glyphs.
4. **No bloat.** Every line earns its keep. If a provider is fully ready, it gets a single line; no walkthrough, no hint.
5. **Refuse > apologize.** When nothing is ready, exit non-zero before touching `settings.json`. Provide `--force-empty` as the override.

---

## 1. Provider state model

Each provider resolves to one of four states:

| State           | Meaning                                          | Source                                                                                |
| --------------- | ------------------------------------------------ | ------------------------------------------------------------------------------------- |
| `READY`         | binary on PATH (≥ floor) **and** signed in       | existing `_check_codex_cli` / `_check_gemini_cli` + new auth probe                    |
| `NEEDS_SIGNIN`  | binary on PATH (≥ floor) but no usable creds     | new auth probe (existence + parseable JSON + non-empty credential keys)               |
| `NEEDS_UPGRADE` | binary on PATH but below floor (**Codex only**)  | `_codex_meets_minimum` returning `False` (`installer.py:510-517`)                     |
| `MISSING`       | not on PATH                                      | `shutil.which` miss (`installer.py:520-523`, `installer.py:616-619`)                  |

**Aggregate readiness:** the install is "ready" iff *at least one* provider is `READY`. Both providers in any non-`READY` state ⇒ **refuse** (see §4).

**Sign-in probe contract** (per `code-surface.md` §"Host sign-in state inspection"):
- Codex: `~/.codex/auth.json` parses + has non-empty `tokens.access_token` *or* non-empty `OPENAI_API_KEY`.
- Gemini: `~/.gemini/oauth_creds.json` parses + has non-empty `access_token` *or* env `GEMINI_API_KEY` set *or* Vertex env vars set (`GOOGLE_APPLICATION_CREDENTIALS` / `GOOGLE_GENAI_USE_VERTEXAI`).
- Probes never log token values. Expired/malformed → treat as `NEEDS_SIGNIN`. (Don't refuse for expiry alone in v1 — the CLI may auto-refresh on first use.)

---

## 2. Status table

### Format spec
- Header: `Provider status` (no bold, no color).
- Rule: 45 `─` (U+2500) characters above and below the table body.
- Columns: provider name (14 cols, left-aligned), `Installed?` (18 cols), `Signed in?` (remainder).
- Cell rendering — values per state:

| State            | Installed? cell      | Signed in? cell |
| ---------------- | -------------------- | --------------- |
| `READY`          | `✅ <version>`       | `✅`            |
| `NEEDS_SIGNIN`   | `✅ <version>`       | `❌`            |
| `NEEDS_UPGRADE`  | `⚠️  <version>`      | `—`             |
| `MISSING`        | `❌`                  | `—`             |

  - `—` (U+2014) for "not applicable" — never blank, never `N/A`.
  - Version unparseable → fall back to `✅` (no version) so we don't fabricate one. Existing parse-fail policy at `installer.py:583-586` already takes this stance.

### Drop-in template
Render via a single helper near `format_install_message` (`installer.py:1192-1243`). Sketch:

```python
RULE = "─" * 45

def _provider_row(name: str, status: ProviderStatus) -> str:
    return f"{name:<14}{status.installed_cell():<18}{status.signin_cell()}"

def _format_provider_status_table(codex: ProviderStatus, gemini: ProviderStatus) -> str:
    return "\n".join([
        "Provider status",
        RULE,
        f"{'':<14}{'Installed?':<18}{'Signed in?'}",
        _provider_row("Codex CLI", codex),
        _provider_row("Gemini CLI", gemini),
        RULE,
        _aggregate_summary_line(codex, gemini),
    ])
```

> Padding caveat: `✅`/`❌`/`⚠️` render at visual width 2 in most terminals but Python `:<N`
> padding treats them as width 1. Reserve 3 cols for the glyph (`icon + space`) inside each
> cell and pad the trailing label with plain spaces. Verify alignment in iTerm2,
> gnome-terminal, and Windows Terminal before merging.

### Aggregate summary line
One line directly under the bottom rule. Form: `<Lead>: <codex-entry> · <gemini-entry>.`

| Lead            | When                                          |
| --------------- | --------------------------------------------- |
| `Ready`         | ≥1 provider `READY`                           |
| `Almost ready`  | none `READY`, ≥1 `NEEDS_SIGNIN`               |
| `Not ready`     | every provider is `MISSING` or `NEEDS_UPGRADE`|

Per-provider entry by state:
- `READY` → `Codex 0.124.0` (name + version, no parens)
- `NEEDS_SIGNIN` → `Codex (sign in to finish)`
- `NEEDS_UPGRADE` → `Codex (upgrade — 0.119 < 0.120 floor)`
- `MISSING` → `Codex (not installed)`

### Worked examples

**Both ready:**
```
Provider status
─────────────────────────────────────────────
              Installed?        Signed in?
Codex CLI     ✅ 0.124.0         ✅
Gemini CLI    ✅ 0.39.0          ✅
─────────────────────────────────────────────
Ready: Codex 0.124.0 · Gemini 0.39.0.
```

**Codex ready, Gemini missing:**
```
Provider status
─────────────────────────────────────────────
              Installed?        Signed in?
Codex CLI     ✅ 0.124.0         ✅
Gemini CLI    ❌                  —
─────────────────────────────────────────────
Ready: Codex 0.124.0 · Gemini (not installed).
```

**Both installed, neither signed in:**
```
Provider status
─────────────────────────────────────────────
              Installed?        Signed in?
Codex CLI     ✅ 0.124.0         ❌
Gemini CLI    ✅ 0.39.0          ❌
─────────────────────────────────────────────
Almost ready: Codex (sign in to finish) · Gemini (sign in to finish).
```

**Nothing on PATH:**
```
Provider status
─────────────────────────────────────────────
              Installed?        Signed in?
Codex CLI     ❌                  —
Gemini CLI    ❌                  —
─────────────────────────────────────────────
Not ready: Codex (not installed) · Gemini (not installed).
```

---

## 3. ELI5 explainer

Renders **only** when zero providers are `READY` (i.e. the install is about to refuse, or the user passed `--force-empty`). Three lines, blank line above and below. Drop-in:

```
claude-anyteam routes some Claude Code teammates to external AI CLIs (Codex, Gemini).
You need at least one signed-in CLI for it to do anything useful.
Pick whichever you have access to.
```

Do not render this block when ≥1 provider is `READY` — it's noise at that point.

---

## 4. Per-provider walkthrough

Renders for any provider in `MISSING`, `NEEDS_SIGNIN`, or `NEEDS_UPGRADE`. Skip providers that are `READY`. Block per provider, blank line between blocks.

### Codex
```
Codex CLI:
  1. Install:  npm install -g @openai/codex
  2. Sign in:  codex     (opens an OAuth flow on first run)
  Docs: https://github.com/openai/codex#getting-started
```

State variants — only the line(s) that apply render; numbering stays continuous:
- `MISSING` → both steps render.
- `NEEDS_UPGRADE` → step 1 only, with `Upgrade:` instead of `Install:`, and append `(detected 0.119, need ≥ 0.120)` to the command line.
- `NEEDS_SIGNIN` → step 2 only, renumbered to `1.`.

### Gemini
```
Gemini CLI:
  1. Install:  npm install -g @google/gemini-cli
  2. Sign in:  gemini    (or set GEMINI_API_KEY, or configure Vertex)
  Docs: https://github.com/google-gemini/gemini-cli
```

State variants: same rules as Codex (Gemini has no `NEEDS_UPGRADE` state in the current probe).

> **Constant consistency fix:** existing `CODEX_CLI_INSTALL_COMMAND = "npm i -g @openai/codex"` (`installer.py:471`) should change to `"npm install -g @openai/codex"` so both walkthroughs read identically. Tiny diff, big visual win.

---

## 5. Refuse-to-install gate + `--force-empty`

### Trigger
Aggregate `Not ready` or `Almost ready` (i.e. zero providers `READY`), **and** `force_empty=False`. Refusal happens *before* `discover_managed_paths()` and the env-block write — see insertion point in §7.

### Drop-in copy
After the table, ELI5, and walkthroughs have been printed (in that order), append:

```
Refusing to install — no provider is ready.
  claude-anyteam needs at least one signed-in CLI (Codex or Gemini) to do anything
  useful. Follow the steps above, then re-run `claude-anyteam install`.

  Setting up later? Pass --force-empty to install with no provider ready:
    claude-anyteam install --force-empty
```

### Exit code
New: `INSTALL_ERROR_EXIT_NO_PROVIDER = 5` (slot after the existing 2/3/4 ladder at `installer.py:46-52`). Rationale: lets CI scripts distinguish "no provider" from generic install failure (2), prompt-decline (3), and corrupted state (4).

### `--force-empty` flag
- Add to `_build_install_parser` (`cli.py:92-123`) as `action="store_true"`, default `False`.
- Plumb through `_install_command` → `install()` (`cli.py:175-200`, `installer.py:917-930`).
- Help text:
  ```
  --force-empty   install without any provider ready (CI / set up later)
  ```
- When passed: skip refusal, still print the table + walkthrough + a one-liner acknowledgment, then proceed with the writes:
  ```
  Proceeding with --force-empty: claude-anyteam is installed but inert until a CLI is ready.
  ```
- npm `setup.js` should **not** pass `--force-empty` automatically (`setup.js:167-179`). Discussed in §9.

---

## 6. End-of-install summary

The current order (`format_install_message` at `installer.py:1192-1243`) is: receipt → detections → restart. New order is **state-first, receipt-second**:

### Order — success path (≥1 provider `READY`, *or* `--force-empty` set)

```
[Provider status table]
[Aggregate summary line]
                                                ← blank line
[Walkthrough block(s) for any non-READY provider, omitted if all READY]
                                                ← blank line
Updated /home/.../settings.json
Set env.CLAUDE_CODE_TEAMMATE_COMMAND=...
Set env.CLAUDE_ANYTEAM_BINARY=...
Set env.CLAUDE_ANYTEAM_GEMINI_BINARY=...
Set teammateMode="tmux" in /home/.../.claude.json   ← only if mutated; current logic preserved
                                                ← blank line
Restart Claude Code for the changes to take effect. Use codex-* or gemini-* teammate names to route to the matching backend.
```

Drop the existing `Detected Codex CLI <ver> at <path>` lines and the standalone `Warning:` blocks — the table + walkthrough replace both. Keep the existing `Removed legacy env.CODEX_TEAMMATE_BINARY entry.` line and the idempotent `The existing settings already matched this install.` line; both are receipts, not status.

### Order — refuse path (zero `READY`, no `--force-empty`)

```
[Provider status table]
[Aggregate summary line]
                                                ← blank line
[ELI5 explainer — three lines]
                                                ← blank line
[Walkthrough block(s) for every non-READY provider]
                                                ← blank line
Refusing to install — no provider is ready.
  ...
```

Exit `5`. **No** settings written. **No** `Restart Claude Code` line.

### Order — tmux-missing path (existing hard gate)
Unchanged. The tmux-missing error already raises `InstallError` before any provider rendering; current copy at `installer.py:953-960` stays. The provider table is *not* rendered in this branch — the user can't proceed regardless of provider state, and tacking on a table would be noise.

### Worked example — Codex ready only, Gemini missing
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

Updated /home/rosado/.claude/settings.json
Set env.CLAUDE_CODE_TEAMMATE_COMMAND=/home/rosado/.local/bin/claude-anyteam-spawn-shim
Set env.CLAUDE_ANYTEAM_BINARY=/home/rosado/.local/bin/claude-anyteam
Set env.CLAUDE_ANYTEAM_GEMINI_BINARY=/home/rosado/.local/bin/gemini-anyteam
Set teammateMode="tmux" in /home/rosado/.claude.json

Restart Claude Code for the changes to take effect. Use codex-* or gemini-* teammate names to route to the matching backend.
```

### Worked example — refuse path (nothing on PATH)
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
  claude-anyteam needs at least one signed-in CLI (Codex or Gemini) to do anything
  useful. Follow the steps above, then re-run `claude-anyteam install`.

  Setting up later? Pass --force-empty to install with no provider ready:
    claude-anyteam install --force-empty
```
Exit code: `5`.

---

## 7. Color and format guidance

**v1: no ANSI color.** Match existing installer aesthetic — every current line in `format_install_message` is plain text. Adding color in this PR doubles the surface area being reviewed.

- Glyphs `✅` `❌` `⚠️` `—` render via terminal default. Do **not** wrap in SGR escapes.
- Section headers (`Provider status`, `Codex CLI:`) are plain text. No bold, no color.
- The `─` rule renders at default brightness.

**Future enhancement (out of scope for #4):** dim the rule line and the trailing receipt block via SGR `2` (faint) when `sys.stdout.isatty()` is true *and* `os.environ.get("NO_COLOR")` is unset *and* a new opt-in env var (e.g. `CLAUDE_ANYTEAM_COLOR=1`) is set. File a follow-up; do not bundle.

---

## 8. Insertion points (for task #4)

| Concern                          | Insertion point                                                                                                | Notes                                                                                  |
| -------------------------------- | -------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| Sign-in probe types              | Near `CodexCliCheck` / `GeminiCliCheck` (`installer.py:73-101`)                                                | New `ProviderStatus` dataclass that composes CLI check + auth check.                   |
| Sign-in probe functions          | Adjacent to `_check_codex_cli` / `_check_gemini_cli` (`installer.py:520-545`, `installer.py:616-660`)          | New `_check_codex_auth` / `_check_gemini_auth`. Keep injectable like existing probes.  |
| Probe wiring                     | `install()` at `installer.py:946-950`                                                                          | Call sign-in probes **after** CLI probes, **before** the tmux hard-gate.               |
| Refuse-to-install gate           | `install()` at `installer.py:967-972` (between tmux gate and `discover_managed_paths`)                         | Raise `InstallError(cli_exit_code=5)` unless `force_empty=True`.                       |
| `--force-empty` flag             | `_build_install_parser` (`cli.py:92-123`) + `_install_command` (`cli.py:175-200`) + `install()` signature      | Plumb the bool end-to-end.                                                             |
| Status table render              | New helper next to `format_install_message` (`installer.py:1192-1243`), called at top of that function         | Renders for both `claude-anyteam install` and the npm wrapper (which inherits stdout). |
| Walkthrough render               | New helper next to the table; replaces `_codex_cli_warning` / `_gemini_cli_warning` call sites in formatter    | Old warning helpers can be deleted once all references are replaced.                   |
| Aggregate summary line           | Same helper as table.                                                                                          | One function returning the whole `Provider status` block.                              |
| Refusal copy                     | Surfaced from the `InstallError` message in `_install_command` (`cli.py:188-200`)                              | The error string is what `cli.py:196` prints to stderr — keep the table+walkthrough on stdout via the formatter, the refusal text comes via the error. **Or**: print table+walkthrough+refusal all on stderr from CLI, and skip `format_install_message` entirely on the refuse branch. Pick one; recommend the latter for cleanliness. |
| Install-state recording          | `_build_state` in `install_teammate_mode` (`installer.py:721-738`)                                             | Add `codex_signed_in: bool`, `gemini_signed_in: bool`, `force_empty_used: bool`.       |
| Existing `Detected …` lines      | Remove from `format_install_message` (`installer.py:1215-1238`)                                                | Replaced by the status table + walkthrough.                                            |
| Existing `Warning: …` blocks     | Stop calling `_codex_cli_warning` / `_gemini_cli_warning` in formatter; either delete or leave as dead code    | They still get used in the tmux-missing path (`installer.py:959-964`) — keep those uses. |
| Codex install constant fix       | `CODEX_CLI_INSTALL_COMMAND` (`installer.py:471`)                                                               | `npm i -g` → `npm install -g`. Cosmetic but matches walkthrough.                       |
| Help-text Gemini mention         | `cli.py:30` parser `description=` and `cli.py:33-34` epilog                                                    | Replace `"Codex-powered teammates"` with `"Codex- and Gemini-backed teammates"`. Empirical doc flagged this as confusion point #1. |

---

## 9. Open questions / follow-ups

1. **npm wrapper + `--force-empty`.** `setup.js:167-179` always passes `--assume-yes`. Should it also pass `--force-empty` in CI (`isCI()` true) so `npx --yes claude-anyteam` doesn't fail postinstall on a CI box with no CLIs? Recommendation: **yes for `--postinstall`**, **no for interactive npm runs**. Defer to implementer; flag for review by team-lead.
2. **Sign-in expiry.** v1 treats parseable-but-expired tokens as `READY` (let the CLI auto-refresh on first use). Revisit if real users hit silent failures.
3. **Vertex/API-key detection for Gemini.** v1 checks env vars at probe time. If the user sets them in `~/.bashrc` but runs the installer from a non-login shell, we'll mis-detect. Acceptable for v1; document in walkthrough hint (`or set GEMINI_API_KEY, or configure Vertex`).
4. **Session-start hook self-heal.** `hooks/session-start.sh:73-75` re-runs install on broken config and suppresses stdout. With `--force-empty` not set, a session-start with no providers will now exit 5 — the hook treats non-zero (other than 127) as propagated, which would surface a confusing failure on every Claude Code launch. **Recommend the hook pass `--force-empty` for the self-heal path** since it's a re-bootstrap, not a fresh install.
