# UX review — installer onboarding (PR #9, branch `feat/installer-onboarding`)

Reviewer: claude-ux-reviewer (Opus). Date: 2026-04-25. Single-pass audit of every user-facing string the installer / hook prints.

Lens applied: the locked design principles in `docs/internal/installer-onboarding/ux-design.md` §"Design principles" (state-first, ELI5-only-when-needed, no bloat, refuse > apologize, plain text + four glyphs). I also re-ran the four scenarios against shaped temp HOMEs (`/tmp/uxtest{1..4}`) to feel the empty-state, refuse, force-empty, and Codex-only-signed-in flows end-to-end.

Verdict: **tweaks-recommended.** No blockers — the core design is honored cleanly. The nits are surface copy and one alignment quirk that the spec itself flagged as "verify before merge" (`ux-design.md:77-80`).

---

## Praise

1. **Refuse path lands exactly per spec.** Exit code 5, no settings written, table → ELI5 → walkthrough → refusal in that order. Confirmed against `/tmp/uxtest2` (empty PATH): no `Updated …/settings.json` line, no `Restart Claude Code` line. (`installer.py:1544-1552`, `cli.py:239-242`.)
2. **ELI5 is correctly sandboxed.** The three-line explainer (`installer.py:1934-1941`) only renders when no provider is ready — so the success path is terse and the refuse path gets the friendly intro. This is the single biggest win: ELI5 tone never leaks into the default flow.
3. **`installed_cell()` / `signin_cell()` separation finally fixes the "Detected = ready" misread.** A `NEEDS_SIGNIN` provider now renders `✅ 0.124.0 / ❌` rather than the old "Detected Codex CLI 0.124.0 at /usr/bin/codex" line that everyone misread as "ready". (`installer.py:152-164`.)
4. **Walkthroughs use a single canonical install command.** Both Codex and Gemini blocks read `npm install -g …` (no more `npm i -g` mismatch). (`installer.py:725, 730` — the spec fix at `ux-design.md:186` landed.)
5. **`--force-empty` ack line is honest, not cheery.** `"Proceeding with --force-empty: claude-anyteam is installed but inert until a CLI is ready."` (`installer.py:2022-2024`) — uses "inert", which is the right word. No "you're all set!" lying.
6. **`session-start.sh` orientation + drift messages are tight.** Both are single-line, factual, no exclamation points, both name the actionable next step. (`hooks/session-start.sh:5-6`.)
7. **No filler.** I grepped for the original brief's tripwires — no "Welcome to claude-anyteam", no "Don't worry", no "easy", no "great", no "🎉". Confirmed clean.

---

## Nits (ordered by likely user impact)

### N1. `(sign in to finish)` is mildly misleading on the refuse path

`installer.py:170` — `summary_entry()` returns `"Codex (sign in to finish)"` for `NEEDS_SIGNIN`. Used in the aggregate summary line:

> `Almost ready: Codex (sign in to finish) · Gemini (sign in to finish).`

…which is followed two screens later by:

> `Refusing to install — no provider is ready.`

"Finish" implies the install is one step from done. But in the refuse path the install just *refused*; the user has multiple steps ahead (sign in, then re-run `claude-anyteam install`). Minor cognitive dissonance.

Suggested replacement: `"sign in to use"` or `"needs sign-in"` (terser).

```python
# installer.py:170
return f"{self.summary_name} (needs sign-in)"
```

### N2. Status-table column drift between `MISSING` and `READY` rows

Empirical render with no providers on PATH:

```
              Installed?        Signed in?
Codex CLI     ❌                 —
```

vs. with both ready:

```
              Installed?        Signed in?
Codex CLI     ✅ 0.124.0         ✅
Gemini CLI    ✅ 0.39.0          ❌
```

The `Signed in?` glyph for the MISSING row sits one column **left** of where the same glyph sits in the READY row. This is exactly the padding caveat the spec called out at `ux-design.md:77-80`: emoji glyphs render at visual width 2 but Python `:<N` treats them as width 1, so the cell length depends on whether a version string follows the glyph.

Cause: `installed_cell()` returns bare `"❌"` (1 logical char, 2 visual cols) for `MISSING`, and `f"✅ {self.version}"` (8+ logical chars, 9+ visual cols) for `READY`. The `:<18` padding in `_provider_row` (`installer.py:1874`) compensates by logical width, not visual.

Cheap fix: pre-pad the bare-glyph cells with a trailing space so they all start with `glyph + space`:

```python
# installer.py:152-157
def installed_cell(self) -> str:
    if self.state in ("READY", "NEEDS_SIGNIN"):
        return f"✅ {self.version}" if self.version else "✅ "
    if self.state == "NEEDS_UPGRADE":
        return f"⚠️  {self.version}" if self.version else "⚠️ "
    return "❌ "
```

Same treatment for `signin_cell()` if the test-checklist mock alignment is the reference. (Spec line `ux-design.md:50` shows `❌ ` with trailing space, and `—` after 18 cols — currently the actual output deviates by 1 col.)

Test in iTerm2 / gnome-terminal / Windows Terminal before merging — the spec asked for this.

### N3. Refusal block duplicates the explainer

The user reads (in order):
1. ELI5 explainer: *"claude-anyteam routes some Claude Code teammates… You need at least one signed-in CLI for it to do anything useful."*
2. Walkthrough(s).
3. Refusal: *"Refusing to install — no provider is ready. claude-anyteam needs at least one signed-in CLI (Codex or Gemini) to do anything useful."*

Lines 1 and 3 say the same thing twice. The refusal block can shorten to its actionable bits without losing meaning:

```python
# installer.py:1998-2005
def _format_no_provider_refusal_message() -> str:
    return (
        "Refusing to install — no provider is ready.\n"
        "  Follow the steps above, then re-run `claude-anyteam install`.\n\n"
        "  Setting up later? Pass --force-empty to install with no provider ready:\n"
        "    claude-anyteam install --force-empty"
    )
```

Saves three lines per refuse-path render. Aligns with design principle #4 ("every line earns its keep").

### N4. `--no-input` help text overpromises

`cli.py:117` — `"fail instead of prompting; use in CI"`. But the implementer left a comment at `cli.py:213-216` acknowledging the install path is currently non-interactive — the only prompt is the `teammateMode` overwrite, which `--assume-yes` already handles. So `--no-input` is wired forward-looking but does nothing different from `--assume-yes` for the *current* install flow.

Two options:
- Tighten help to truth: `"reserve no-input mode for CI; prompt-free behavior"` (or just `argparse.SUPPRESS` until a real prompt routes through it).
- Accept that the help is forward-looking and document it.

Minor — not user-blocking, but a careful CI engineer reading `--help` and `--assume-yes` next to each other will wonder which to pick.

### N5. Last receipt line packs two thoughts

`installer.py:2087`:
> `Restart Claude Code for the changes to take effect. Use codex-* or gemini-* teammate names to route to the matching backend.`

Action + reference info crammed into one sentence. Easier to scan as two lines:

```python
receipt_lines.append("Restart Claude Code for the changes to take effect.")
receipt_lines.append("Use codex-* or gemini-* teammate names to route to the matching backend.")
```

…or drop the second sentence entirely; it's also in the session-start orientation message and in the README. Receipt blocks earn their keep when they answer "what just changed" — naming convention is reference, not receipt.

### N6. `"Permission allowlist written so spawning teams won't prompt."`

`installer.py:2085`. Clear enough, but "spawning teams" is jargon for someone who hasn't used Agent Teams yet. They'll learn it the first time they hit the prompt — but a slightly grounded version reads better:

> `Wrote permission allowlist (Claude Code won't prompt when teams write to ~/.claude/teams/).`

Optional. If you ship as-is it's fine; the receipt block tone is appropriately terse.

### N7. Hardcoded line wrap in refusal copy

`installer.py:2001-2002`:
```
  claude-anyteam needs at least one signed-in CLI (Codex or Gemini) to do anything
  useful. Follow the steps above, then re-run `claude-anyteam install`.
```

The wrap dangles `"useful."` on its own line because the wrap is hardcoded. Becomes ugly on a 60-col terminal (already-wrapped further) or if anyone tweaks the sentence later. If you take N3 above this disappears anyway. Otherwise, prefer one long line and let the terminal wrap, or rewrite to break at a natural clause boundary.

---

## Things I deliberately did **not** flag

- **Tmux-missing path still uses the old `_codex_cli_warning` / `_gemini_cli_warning` copy** (`installer.py:1525-1530`). This is per spec — `ux-design.md` §6 explicitly preserves the tmux-missing branch unchanged, and the warnings only render in that one path. Inconsistent with the new aesthetic, but intentional and out of scope.
- **`✅` / `❌` / `⚠️` width caveat across terminals.** The spec said "verify alignment in iTerm2, gnome-terminal, and Windows Terminal before merging." I tested in WSL2 / Windows Terminal only — flag for whoever has a Mac to confirm before merge.
- **Help text describes `~/.claude/settings.json` and `~/.claude.json` mutations in `--help`'s description** — accurate, useful for a power-user reading `--help`, and consistent with the existing pre-onboarding help. No change.

---

## Tweak status (post-review)

- **N1 ✅ Applied.** `(sign in to finish)` → `(needs sign-in)` in `installer.py:170`, plus updates to test assertions and design docs.
- **N2 ⏸ Deferred.** Adding trailing space to bare-glyph cells introduces trailing whitespace at end-of-line and breaks `:<18` padding alignment in the existing test fixtures. The reviewer's diagnosis (column drift) was based on WSL2/Windows Terminal observation; needs empirical validation in iTerm2, gnome-terminal, and macOS Terminal before a fix can be selected. Tracked as a follow-up.
- **N3 ✅ Applied.** Refusal block trimmed to drop the duplicate explainer; saves three lines per refuse-path render.
- **N4-N7 ⏸ Deferred** as explicitly noted in the review.

## Files audited

- `src/claude_anyteam/installer.py` — all of `_format_*`, `_provider_row`, `_aggregate_summary_line`, `_codex_cli_warning`, `_gemini_cli_warning`, `format_install_message`, `format_uninstall_message`, every `raise InstallError(...)` string.
- `src/claude_anyteam/cli.py` — `_build_install_parser`, `_install_command`, `_interactive_prompt`, the help epilog.
- `hooks/session-start.sh` — `ORIENTATION_MESSAGE`, `DRIFT_WARNING`.
- `docs/internal/installer-onboarding/ux-design.md` (the spec) and `test-checklist.md` (expected outputs).

## Empirical runs

| HOME | PATH state | Flags | Exit | Outcome |
|---|---|---|---|---|
| `/tmp/uxtest1` | both CLIs on PATH, neither signed in | `--no-input` | 5 | refuse, table shows `Almost ready`, no settings written |
| `/tmp/uxtest2` | empty PATH | `--no-input` | 5 | refuse, table shows `Not ready`, walkthroughs render with both Install + Sign in steps |
| `/tmp/uxtest3` | empty PATH | `--no-input --force-empty` | 0 | proceeds, "inert" line printed, settings written |
| `/tmp/uxtest4` | both CLIs on PATH, fake Codex auth.json | `--no-input` | 0 | success, only Gemini walkthrough renders (Codex omitted as `READY`) |

All runs honored: state-first rendering order, ELI5 only when no provider ready, walkthrough omits ready providers, refuse-before-write, force-empty proceeds-line. The four nits above are independent of any single scenario.
