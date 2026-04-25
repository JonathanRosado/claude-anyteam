# Installer onboarding code review

Reviewed branch state at HEAD (`87e7638`) for the requested range `3b29a1f^..HEAD`. I read the full touched files at HEAD: `src/claude_anyteam/installer.py`, `src/claude_anyteam/cli.py`, `hooks/session-start.sh`, `tests/test_install_command.py`, and `tests/test_plugin_bundle.py`. I did not run the test suite.

## Verdict: fix-required

### 1. Provider auth probes are too permissive, so the no-provider gate can be bypassed incorrectly

**Where:**
- Spec requires Codex readiness from parseable `~/.codex/auth.json` with non-empty `tokens.access_token` or top-level `OPENAI_API_KEY`: `docs/internal/installer-onboarding/ux-design.md:31-34`.
- Spec requires Gemini readiness from parseable `~/.gemini/oauth_creds.json` with non-empty `access_token`, or `GEMINI_API_KEY` / Vertex env: `docs/internal/installer-onboarding/ux-design.md:31-34`.
- Codex implementation accepts `tokens.id_token` and `tokens.refresh_token` too: `src/claude_anyteam/installer.py:939-943`.
- Gemini implementation accepts `id_token`, `refresh_token`, and then treats `~/.gemini/google_accounts.json` `active` as signed in even when OAuth credentials are missing/unusable: `src/claude_anyteam/installer.py:981-993`.

**Why this matters:** a machine with only a refresh/id token, or only `google_accounts.json`, can be marked `READY`, making `install()` skip the exit-5 refusal and print a green status for a provider that may not actually be usable. That directly violates the core hand-holding contract: install should only proceed without `--force-empty` when at least one provider is installed **and signed in** under the documented credential shape.

**Required fix:** make the probes match the contract exactly unless the spec is updated: Codex should require `tokens.access_token` or top-level `OPENAI_API_KEY`; Gemini should require OAuth `access_token` or the documented env-based auth. Add negative tests for Codex refresh-only/id-token-only, Gemini refresh-only/id-token-only, and Gemini `google_accounts.json`-only.

### 2. SessionStart drift warning does not detect stale or non-executable installer paths

**Where:**
- Hook validation only checks that the three settings env values are non-empty strings: `hooks/session-start.sh:32-45`.
- The grep fallback has the same presence-only behavior: `hooks/session-start.sh:55-57`.
- The test for a “complete” configuration uses fake `/configured/...` paths that do not exist and still expects the orientation message: `tests/test_plugin_bundle.py:116-150`.

**Why this matters:** the shipped hook says it warns when settings drift, but a common drift case is a stale `CLAUDE_CODE_TEAMMATE_COMMAND` / `CLAUDE_ANYTEAM_BINARY` / `CLAUDE_ANYTEAM_GEMINI_BINARY` after reinstalling, moving a venv, or removing a tool. Today the hook will print “claude-anyteam is installed...” and exit 0 as long as the strings are non-empty, even though teammate spawning will fail.

**Required fix:** restore executable/path validation in the Python branch at minimum (`Path(value).is_file()` and executable bit where meaningful), and change the test to use real executable temp files for the orientation case plus a stale-path test that expects `DRIFT_WARNING`.

## Test coverage against `test-checklist.md`

Static coverage review only; I did not execute pytest.

| Checklist scenario | Pytest coverage |
| --- | --- |
| (a) No providers installed | `test_install_with_no_providers_refuses_before_settings_mutation` (`tests/test_install_command.py:332-365`) and `test_install_no_input_refuses_with_no_providers` (`tests/test_install_command.py:368-397`) cover exit 5 and no writes. |
| (b) Both installed, neither signed in | `test_install_with_both_installed_but_not_signed_in_refuses` (`tests/test_install_command.py:488-518`). |
| (c) Only Codex signed in | `test_install_with_codex_signed_in_only_prints_gemini_walkthrough_and_updates_settings` (`tests/test_install_command.py:458-485`). |
| (d) Only Gemini signed in | `test_install_warns_when_codex_cli_missing_but_still_succeeds` (`tests/test_install_command.py:1522-1560`). Note: this asserts Codex-first summary order, while the checklist example shows Gemini first. |
| (e) Both providers signed in | `test_install_with_both_providers_signed_in_updates_settings` (`tests/test_install_command.py:400-425`). |
| (f) Provider binary missing entirely | Same behavioral case as (a), covered by `test_install_with_no_providers_refuses_before_settings_mutation`. |
| (g) Force install with no providers | `test_install_force_empty_with_no_providers_updates_settings` (`tests/test_install_command.py:521-552`). |

Coverage gap that matters for merge: the tests cover happy-path `access_token` auth (`tests/test_install_command.py:1869-1887`, `tests/test_install_command.py:2140-2155`) and empty/malformed/expired files, but not the false-positive credential shapes called out in finding #1.

## Security review

No new shell-injection issue found in the provider probes: subprocess calls pass argv lists and use resolved binaries (`src/claude_anyteam/installer.py:795-801`, `src/claude_anyteam/installer.py:1092-1113`). The auth readers do not print token values; diagnostics include only paths and parse/error labels (`src/claude_anyteam/installer.py:829-847`). The main security-adjacent risk is correctness-driven: over-permissive auth detection can route users into non-working external CLIs while claiming readiness.

## Code quality / polish

- The render-only helpers `_codex_render_status`, `_gemini_render_status`, and `_render_provider_*` duplicate the real `ProviderStatus` path and ignore the Codex `NEEDS_UPGRADE` state (`src/claude_anyteam/installer.py:1869-1950`). They are currently test-only helpers, but they make it easy for tests to assert behavior that production formatting does not use. Prefer deleting them or making tests build real `ProviderStatus` objects through `_codex_provider_status` / `_gemini_provider_status`.
- Hidden `--self-heal` still bypasses the no-provider refusal by passing `force_empty=force_empty or self_heal` (`src/claude_anyteam/cli.py:228-238`), even though the current hook only warns and never invokes install. If self-heal is no longer part of the design, remove the flag; if it is, add explicit tests for its output/state semantics.

## Re-review (post-fix)

Reviewed only the post-fix auth probe and hook drift changes requested. I did not run tests per instruction.

### Verdict: ship

- `69114cc`: `_check_codex_signin` now gates token auth on `tokens.access_token` and still accepts top-level `OPENAI_API_KEY` (`src/claude_anyteam/installer.py:929-932`). `_check_gemini_signin` still accepts `GEMINI_API_KEY` and Vertex env (`src/claude_anyteam/installer.py:950-952`), ignores `google_accounts_path` as an auth signal (`src/claude_anyteam/installer.py:947`), and requires `oauth_creds.json` `access_token` (`src/claude_anyteam/installer.py:963-968`). Negative coverage exists for Gemini id/refresh-token-only (`tests/test_install_command.py:1889-1908`), Gemini `google_accounts.json`-only (`tests/test_install_command.py:1912-1928`), and Codex id/refresh-token-only (`tests/test_install_command.py:2201-2220`).
- `3c1c65d`: the Python hook branch now rejects configured values unless `Path(value).is_file()` and `os.access(value, os.X_OK)` both pass (`hooks/session-start.sh:38-43`); the grep fallback uses equivalent `-f` and `-x` checks (`hooks/session-start.sh:51-62`), so any missing/non-file/non-executable value falls through to `DRIFT_WARNING` (`hooks/session-start.sh:69-75`). Tests cover the real executable happy path plus stale-path and non-executable temp-file warnings (`tests/test_plugin_bundle.py:137-248`).
- Static regression pass found no new issue and no tightening that breaks the documented legitimate auth paths: Codex `tokens.access_token` or auth-file `OPENAI_API_KEY`; Gemini OAuth `access_token`, `GEMINI_API_KEY`, or Vertex.
