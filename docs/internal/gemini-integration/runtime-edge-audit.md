# Gemini runtime edge audit

Date: 2026-04-24  
Reviewer: codex-gemini-runtime-reviewer  
Scope: follow-up Gemini-runtime lane audit requested after the initial implementation review. This covers stream-json parser edge cases and backend-neutral schema prompt wording. Auth-fix verification is still pending because no post-`9628cc9` auth-fix commit was present after `git fetch --all --prune`.

## Branch / fix availability

Current local/remote state after fetch:

- Current branch: `feat/gemini-integration`
- HEAD: `9628cc9 Review Gemini runtime implementation`
- `main` / `origin/main`: `971cf5e Bump to 0.3.2 to ship codex-cli prereq check`
- No new auth-fix commit was available to pull at review time.

The pre-fix auth failure is already independently reproduced in `runtime-implementation-review.md`: adapter-shaped isolated `HOME` with only symlinked credential/cache files exits `41` because the isolated `.gemini/settings.json` does not preserve Gemini's auth method selection. I have not verified a fix yet because it has not landed in this checkout.

## Stream-json parser audit

Implementation under review: `src/claude_anyteam/backends/gemini/invoke.py:151-190`.

### Covered behavior that matches runtime findings

- **`tool_result` without `output` does not crash.** The parser appends every JSON event to `events` but only branches on `init`, assistant `message`, and `tool_use` (`invoke.py:159-167`). It never indexes `tool_result.output`, so built-in tool results like the earlier `list_directory`/`pwd` samples without payload are tolerated.
- **Multiple/interleaved tool calls are tolerated for current needs.** The parser count is event-based (`tool_call_events += 1` at `invoke.py:165-167`) and raw `tool_use`/`tool_result` events are preserved in order (`invoke.py:159`). It does not correlate tool ids, so concurrent/interleaved `tool_use`/`tool_result` sequences do not break parsing. This is acceptable because the adapter currently only needs a raw event log and tool-call count.
- **Terminal `result` followed by EOF is handled.** `proc.stdout.splitlines()` (`invoke.py:151`) handles a final JSON object without a trailing newline. The terminal result is found by scanning the parsed events in reverse (`invoke.py:176`), and EOF after `result` requires no special handling.
- **Missing `init` is non-fatal.** If no `init.session_id` appears, `captured_session_id` stays `None` (`invoke.py:142`, `161-162`) and the returned `CodexResult.session_id` is `None` (`invoke.py:189`). This means the loop will not enable `--resume` for the next turn, but the current turn can still succeed.
- **Late `init` is non-fatal.** A late `init` after assistant/result would still set `session_id` because the parser does not enforce event ordering (`invoke.py:161-162`). The observed Gemini runtime emits `init` first, so this is mostly a tolerance property; if Gemini ever emitted an unrelated late init, the adapter would trust it.
- **Stdout pollution lines are skipped.** Non-JSON stdout lines are logged and ignored (`invoke.py:154-158`), which is useful for known ACP/stdout-preamble style pollution and harmless banners in headless mode.

I exercised these cases with a monkeypatched `subprocess.run` fixture equivalent to `tests/test_gemini_invoke.py`: interleaved `tool_use`/`tool_result` with one missing `output`, EOF immediately after `result`, missing `init`, and late `init` all returned successful structured task-complete output. The implementation behaved as described above.

### Parser caveats / findings

1. **No hard error when terminal `result` is missing.** If Gemini exits `0` and emits valid assistant JSON but no `result` event, `terminal` is `None` and no error is set (`invoke.py:176-180`). Runtime research says `result` is the terminal event for `stream-json`; treating absence as success may hide truncated stdout or wrapper bugs. Recommended next-PR mitigation: if `events` is non-empty and no `result` event is present, set a warning/error or at least log `gemini.result_missing`.
2. **Late `init` is blindly accepted.** Because any `init.session_id` updates `captured_session_id` (`invoke.py:161-162`), a malformed stream with a late/different session id after `result` would be used for future `--resume`. Low risk given observed CLI behavior, but a stricter parser could only accept `init` before the first non-init event or log late init anomalies.
3. **Assistant text accumulation is broad by design.** Every assistant `message.content` is concatenated (`invoke.py:163-169`). This matches observed delta streaming but can fail schema validation if Gemini emits natural-language assistant text before final JSON. The loop retry (`loop.py:232-250`) mitigates this for task completion. No correctness issue for current Plan A, but worth backfilling a regression if observed in live CLI output.
4. **`feature_test()` still does not check `--approval-mode`.** Runtime invocation always passes `--approval-mode yolo` (`invoke.py:126`), but feature-test only requires `--prompt`, `--output-format`, and `--resume` (`invoke.py:91`). This was previously flagged; it remains a small but concrete probe gap.

## Schema prompt / Codex-language audit

Implementation under review: `src/claude_anyteam/schema_validation.py`, `src/claude_anyteam/backends/gemini/prompts.py`, and the shared schemas.

### Result

- `schema_validation.py` is now backend-neutral in module framing and helper wording. It references Codex only as an example of a backend without CLI-level schema enforcement (`schema_validation.py:3-5`), not as an instruction to Gemini.
- `inline_schema_prompt_fragment()` is backend-neutral and does not say Codex (`schema_validation.py:63-75`).
- Gemini prompt builders correctly frame the agent as a “Gemini CLI teammate” (`prompts.py:20-46`) and use Gemini-normalized MCP tool names such as `mcp_anyteam_send_message` (`prompts.py:10-17`).
- Shared schema titles/descriptions are backend-neutral: `schemas/task-complete.schema.json` title is “Teammate task-complete response” and `schemas/plan.schema.json` title is “Teammate plan”. These compact schemas are embedded into Gemini prompts, so this matters.

No Codex-branded instruction leak was found in the Gemini task, plan, or schema-output prompt path.

## Tests reviewed

`tests/test_gemini_invoke.py` currently covers:

- isolated MCP settings and `anyteam` alias (`test_write_mcp_settings_uses_isolated_home_and_anyteam_alias`),
- stream-json parsing with a non-JSON line, a `tool_use`, a `tool_result` without `output`, assistant deltas, terminal `result`, `--resume`, `--model`, and `stdin=DEVNULL` (`test_run_parses_stream_json_and_validates_schema`),
- required headless flag probing for `--resume` (`test_feature_test_requires_headless_flags`).

Coverage gaps remain for:

- missing terminal `result`,
- late/missing `init` behavior,
- multiple interleaved tool calls/results,
- loop-level schema retry (the invoke test validates parsing once, but loop retry is not covered by `test_gemini_invoke.py`),
- the pending auth fix once it lands.

