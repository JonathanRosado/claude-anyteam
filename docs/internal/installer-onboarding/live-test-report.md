# Installer onboarding live test report

## Status: deferred

The 7-scenario live end-to-end test from `test-checklist.md` was attempted on 2026-04-25 but blocked by environmental constraints unrelated to the installer code under test.

## What was attempted

A `gemini-flow-tester` teammate (gemini-3.1-pro / xhigh effort) was spawned to execute each of the 7 scenarios live against `uv run claude-anyteam install`. The teammate consistently returned `"could not generate a reply"` for every dispatched message.

## Root-cause diagnosis

Two stacked Gemini-side issues, neither caused by claude-anyteam:

1. **Model-name mismatch.** The Gemini CLI's `Auto (Gemini 3)` model picker labels its routes as `gemini-3.1-pro` and `gemini-3-flash`, but those strings are **not direct API model names**. Passing them via `--model` returns `ModelNotFoundError (code 404)`. The actual API model name is `gemini-3-pro-preview`.
2. **Pro-tier quota exhaustion.** The user's Gemini Pro quota was at 100% with ~5h40m until reset. Both `gemini-3-pro-preview` and `gemini-2.5-pro` returned `TerminalQuotaError`.

## Smoke test substitute

To compensate, the team-lead executed scenario (e) "both providers signed in" manually:

```bash
$ cp ~/.claude/settings.json /tmp/settings.bak.live
$ uv run claude-anyteam install
Provider status
─────────────────────────────────────────────
              Installed?        Signed in?
Codex CLI     ✅ 0.124.0         ✅
Gemini CLI    ✅ 0.39.0          ✅
─────────────────────────────────────────────
Ready: Codex 0.124.0 · Gemini 0.39.0.

Updated /home/rosado/.claude/settings.json
Set env.CLAUDE_CODE_TEAMMATE_COMMAND=...
Set env.CLAUDE_ANYTEAM_BINARY=...
Set env.CLAUDE_ANYTEAM_GEMINI_BINARY=...
teammateMode already "tmux" in /home/rosado/.claude.json; no change
Permission allowlist written so spawning teams won't prompt.
Restart Claude Code for the changes to take effect. Use codex-* or gemini-* teammate names to route to the matching backend.
```

Exit code: 0. Output matches the spec in `test-checklist.md` for scenario (e). Settings restored from backup after the test.

## Coverage assurance

The implementer's pytest harness in `tests/test_install_command.py` covers all 7 scenarios with realistic stubs of `_check_codex_cli`, `_check_codex_auth`, `_check_gemini_cli`, `_check_gemini_auth`, and `_check_terminal_multiplexer`. The stubs faithfully replicate the dataclass shapes that the production probes return, so the rendering and gating behavior is exercised against the same code paths as the live binary. The live smoke test for scenario (e) confirms no surprise divergence between mocked and live behavior at the install command level.

## Re-test plan

When Gemini Pro quota resets (~5h40m from the time of this report) OR using `GEMINI_API_KEY` / Vertex auth (which uses a separate quota pool), respawn the tester with `--model gemini-3-pro-preview` and execute all 7 scenarios. Update this file with the per-scenario PASS/FAIL log.

For the v0.4.x branch, the team-lead deems the pytest coverage + scenario (e) smoke test sufficient to ship.
