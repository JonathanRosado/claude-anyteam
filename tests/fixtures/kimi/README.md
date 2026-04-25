# Kimi fixture inventory contract

These fixtures are the raw stdout transcripts that `tests/test_kimi_invoke.py`
and the Kimi session-policy tests will parse.  Each transcript should be
captured from repo root, committed as UTF-8 JSONL, and kept close to the raw
Kimi `--output-format=stream-json` stream.  Do not use `--final-message-only`
for these captures unless a future test explicitly says so; it hides the tool
lifecycle that the parser must verify.

Capture conventions:

- Set `KIMI_CLI_NO_AUTO_UPDATE=1` for every command so update prompts do not
  pollute stdout.
- Use the exact command shown for each fixture, with paths interpreted relative
  to the repo root.  The fixture-producing teammate may create the referenced
  `tests/fixtures/kimi/workspaces/*` files as needed.
- Preserve stdout line order.  Redact only volatile values: session IDs to the
  stable names called out below, absolute repo paths to
  `/tmp/kimi-fixture-work`, and timestamps to `2026-04-25T00:00:00Z`.
- Parser tests should assert structural behavior and sentinel strings, not the
  model's incidental prose.

Expected transcripts:

- `simple_assistant_text.jsonl`
  - Invocation: `KIMI_CLI_NO_AUTO_UPDATE=1 kimi --print --output-format=stream-json --work-dir tests/fixtures/kimi/workspaces/empty --no-thinking --max-steps-per-turn 1 -p 'Reply exactly: KIMI_SIMPLE_OK'`
  - Assertions: exit code is treated as success; at least one assistant content
    record is present; parsed `last_message` is exactly `KIMI_SIMPLE_OK`; no
    tool call events are counted; no structured schema result is required.

- `schema_task_complete.jsonl`
  - Invocation: `KIMI_CLI_NO_AUTO_UPDATE=1 kimi --print --output-format=stream-json --work-dir tests/fixtures/kimi/workspaces/empty --no-thinking --max-steps-per-turn 1 -p 'Return only this JSON object with no markdown: {"files_changed":[],"summary":"schema fixture complete"}'`
  - Assertions: final assistant text parses as JSON and validates against
    `schemas/task-complete.schema.json`; `structured == {"files_changed": [],
    "summary": "schema fixture complete"}`; `last_message` is the raw JSON
    string Kimi emitted.

- `schema_invalid_final.jsonl`
  - Invocation: `KIMI_CLI_NO_AUTO_UPDATE=1 kimi --print --output-format=stream-json --work-dir tests/fixtures/kimi/workspaces/empty --no-thinking --max-steps-per-turn 1 -p 'Reply exactly: this is not json'`
  - Assertions: final assistant text is captured as `last_message`, but schema
    parsing returns `structured is None` with an error beginning `output was not
    valid JSON`; the parser does not crash or fabricate a structured result.

- `multi_chunk_text.jsonl`
  - Invocation: `KIMI_CLI_NO_AUTO_UPDATE=1 kimi --print --output-format=stream-json --work-dir tests/fixtures/kimi/workspaces/empty --no-thinking --max-steps-per-turn 1 -p 'Write exactly five short lines. Each line must be CHUNK_FIXTURE_LINE_1 through CHUNK_FIXTURE_LINE_5. Do not use tools.'`
  - Assertions: fixture contains at least two assistant content-bearing records
    or deltas from the same turn; the adapter concatenates them in order; the
    final `last_message` contains `CHUNK_FIXTURE_LINE_1` through
    `CHUNK_FIXTURE_LINE_5` in ascending order; no tool calls are counted.

- `tool_call_lifecycle.jsonl`
  - Invocation: `KIMI_CLI_NO_AUTO_UPDATE=1 kimi --print --output-format=stream-json --work-dir tests/fixtures/kimi/workspaces/tool_call_lifecycle --no-thinking --max-steps-per-turn 4 -p 'Use exactly one file-reading or shell tool to read ./sentinel.txt, then reply exactly: TOOL_SENTINEL=<contents of sentinel.txt>'`
  - Assertions: stream contains an assistant tool-call record, a matching tool
    result record, and a final assistant record; the tool result contains the
    sentinel file contents; `tool_call_events >= 1`; the final `last_message`
    starts with `TOOL_SENTINEL=` and preserves the sentinel value.

- `mcp_wrapper_tool_call.jsonl`
  - Invocation: `KIMI_CLI_NO_AUTO_UPDATE=1 kimi --print --output-format=stream-json --work-dir tests/fixtures/kimi/workspaces/mcp_wrapper --mcp-config-file tests/fixtures/kimi/mcp/anyteam-wrapper.json --no-thinking --max-steps-per-turn 4 -p 'Call the anyteam read_config tool exactly once, then reply exactly: MCP_READ_CONFIG_OK'`
  - Assertions: stream shows one wrapper MCP call using the `anyteam` server
    alias, expected to normalize to `mcp_anyteam_read_config`; the matching
    tool result is present; wrapper `tool_call_events == 1`; final
    `last_message` is exactly `MCP_READ_CONFIG_OK`.  The MCP config fixture
    should point at the absolute `claude-anyteam-wrapper` path with
    `--team kimi-fixtures --name kimi-fixture-agent`.

- `plan_mode_constrained.jsonl`
  - Invocation: `KIMI_CLI_NO_AUTO_UPDATE=1 kimi --print --plan --output-format=stream-json --work-dir tests/fixtures/kimi/workspaces/plan_mode --no-thinking --max-steps-per-turn 4 -p 'Plan only; do not edit files. Return only this JSON object with no markdown: {"steps":[{"summary":"Inspect the fixture workspace","files_touched":[]}],"risks":[],"estimated_time":"1 minute"}'`
  - Assertions: invocation includes `--plan`; final assistant text validates
    against `schemas/plan.schema.json`; parsed plan has one step with summary
    `Inspect the fixture workspace`; any tool calls in the transcript are
    read-only/exploratory, and no write/edit tool call is present.

- `fresh_session.jsonl`
  - Invocation: `KIMI_CLI_NO_AUTO_UPDATE=1 kimi --print --output-format=stream-json --work-dir tests/fixtures/kimi/workspaces/session --no-thinking --max-steps-per-turn 1 -p 'Start a fixture session. Remember the token alpha. Reply exactly: FRESH_SESSION_TOKEN=alpha'`
  - Assertions: adapter captures the first Kimi session identifier emitted by
    the stream and, after redaction, exposes it as `fixture-fresh-session`;
    `last_message` is exactly `FRESH_SESSION_TOKEN=alpha`; adapter state would
    persist that session ID for the next turn.

- `resume_session.jsonl`
  - Invocation: `KIMI_CLI_NO_AUTO_UPDATE=1 kimi --print --output-format=stream-json --work-dir tests/fixtures/kimi/workspaces/session --session <raw session id captured for fresh_session.jsonl> --no-thinking --max-steps-per-turn 1 -p 'What token did I ask you to remember in the previous turn? Reply exactly: RESUMED_TOKEN=alpha'`
  - Assertions: command uses explicit `--session`; captured/redacted session ID
    remains `fixture-fresh-session`; final `last_message` is exactly
    `RESUMED_TOKEN=alpha`; the loop would keep the same persisted session ID
    after success.

- `invalid_session_recovery.jsonl`
  - Invocation: `KIMI_CLI_NO_AUTO_UPDATE=1 kimi --print --output-format=stream-json --work-dir tests/fixtures/kimi/workspaces/session --session kimi-fixture-session-does-not-exist --no-thinking --max-steps-per-turn 1 -p 'Reply exactly: INVALID_SESSION_RECOVERED'`
  - Assertions: parser handles Kimi's documented behavior for an unknown
    explicit session ID by treating the run as a fresh successful session;
    captured session ID is present and is not
    `kimi-fixture-session-does-not-exist` (redact to
    `fixture-recovered-session`); final `last_message` is exactly
    `INVALID_SESSION_RECOVERED`; loop policy would replace the stale stored
    session ID with the recovered one.

- `error_or_warning.jsonl`
  - Invocation: `KIMI_CLI_NO_AUTO_UPDATE=1 kimi --print --output-format=stream-json --work-dir tests/fixtures/kimi/workspaces/error_or_warning --no-thinking --max-steps-per-turn 4 -p 'Use a shell command to run: sh -c "echo fixture warning >&2; exit 0". Then reply exactly: WARNING_HANDLED'`
  - Assertions: stream contains a tool result or diagnostic content with
    `fixture warning`; subprocess exit code is still treated as success; parser
    preserves the warning-looking content in `events` without turning it into a
    top-level adapter error; final `last_message` is exactly `WARNING_HANDLED`.

- `stdout_noise.jsonl`
  - Invocation: `KIMI_CLI_NO_AUTO_UPDATE=1 kimi --verbose --print --output-format=stream-json --work-dir tests/fixtures/kimi/workspaces/empty --no-thinking --max-steps-per-turn 1 -p 'Reply exactly: STDOUT_NOISE_OK'`
  - Assertions: fixture includes at least one non-JSON stdout line before or
    between JSON records; parser ignores/logs the non-JSON line and still
    captures final `last_message == "STDOUT_NOISE_OK"`; no tool calls are
    counted.  This mirrors Gemini's startup-banner regression guard.
