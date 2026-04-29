# Kimi peer-DM investigation: S8 W7 post-fix verification

Run investigated: `/home/rosado/Projects/codex-teammate/references/external-claude-code-re/proto-rev-execution-log/runs/S8-W7-20260429T0040Z-postfix-verify-v2/`

## Executive summary

`kimi-pair` sent 0 peer-DMs because the Kimi adapter never reached the task/prose loop. It crashed during startup auth preflight at `2026-04-29T00:40:44.480Z`, before registration, inbox polling, prompt delivery, or any Kimi headless turn. The failure was an API authentication error from the `kimi --print ... -p ping` preflight (`401`, invalid/expired API key). Therefore the 60 `codex-pair -> kimi-pair` DMs were successfully emitted by Codex and scored as addressed to Kimi, but they were not visible to a running Kimi loop and could not be answered.

This run does **not** support a prompt-level fix for Kimi peer-DM behavior: Kimi never saw the W7 task prompt or the inbound DMs. The main fix should be runtime/scoring: treat Kimi auth-preflight failure as a scenario validity/availability failure rather than a peer-efficiency finding against Kimi behavior. A secondary bug is that the auth classifier labeled this invalid-auth error as `quota_exhausted`, likely because the diagnostic text contained the run timestamp `20260429...` and the classifier searches for bare substring `"429"`.

## Evidence

### 1. Kimi crashed before registration/main loop

`procs/kimi-pair.stderr.log` contains only six log records. The sequence is:

- `2026-04-29T00:40:35.194Z` — `kimi.version`
- `2026-04-29T00:40:35.206Z` — `kimi.auth_preflight.start`
- `2026-04-29T00:40:44.480Z` — `kimi.startup.crash`, `phase=auth_preflight`
- `2026-04-29T00:40:44.480Z` — `visibility.event`, `kind=visibility_degraded`
- `2026-04-29T00:40:44.484Z` — diagnostic incident recorded
- `2026-04-29T00:40:44.484Z` — `kimi.loop.exit_without_deregister`, `in_flight_task=null`

The failed preflight command was:

```text
/home/rosado/.local/bin/kimi --print --output-format=stream-json \
  --work-dir /tmp/stress-sandbox-S8-W7-20260429T0040Z-postfix-verify-v2/repo \
  --mcp-config-file /home/rosado/.cache/claude-anyteam/kimi/stress-S8-S8-W7-20260429T0040Z-postfix-verify-v2/kimi-pair/.kimi/anyteam-mcp.json \
  -p ping
```

It exited `1` with stdout:

```text
Error code: 401 - {'error': {'message': 'The API Key appears to be invalid or may have expired. Please verify your credentials and try again.', 'type': 'invalid_authentication_error'}}
```

`procs/kimi-pair.stdout.log` is empty, and `procs/kimi-pair.exit_code` is `1`.

There is no `kimi.loop.start`, no `kimi.task.claimed`, no `kimi.invoke`, no `kimi.inbox.prose`, no `kimi.inbox.prose_batch`, and no `kimi.task.completed` in the Kimi logs. In the event stream, there is no `agent_registered`, `turn_started`, `tool_event`, or `turn_completed` for Kimi.

### 2. Kimi event stream has exactly one event and zero tools

`events/kimi-pair.jsonl` is a single `visibility_degraded` envelope:

- `kind`: `visibility_degraded`
- `summary`: `kimi auth preflight failed: quota_exhausted`
- `payload.surface`: `adapter_spawn_auth_preflight`
- `payload.reason`: `auth_failure`
- `payload.raw_backend_error.returncode`: `1`
- `payload.raw_backend_error.stdout`: the `401` invalid/expired API key message above

A JSONL pass over `events/kimi-pair.jsonl` produced:

```text
kind counts: {'visibility_degraded': 1}
tool_event count: 0
send_message tool events: 0
```

So Kimi used no tools in this run. There is no evidence of attempted-and-failed `send_message`; grepping `procs/kimi-pair.{stderr,stdout}.log` for `send_message`/`SendMessage` found no Kimi attempt.

### 3. Codex did send 60 peer-DMs addressed to Kimi

`collab/agents/codex-pair.json` reports:

```json
"M3_peer_dm_sent": 60,
"M4_total_send_message_calls": 60,
"M4_cross_peer_ratio": 1.0,
"M11a_peer_dm_rtt_seconds": {"samples": 0, "unmatched_send_count": 60}
```

`collab/pairs.json` contains one pair:

```json
{"from": "codex-pair", "to": "kimi-pair", "messages_sent": 60, "unmatched_send_count": 60}
```

A parse of `events/codex-pair.jsonl` found 15 Codex turns and 576 tool events. Tool counts included `send_message` events and wrapper `team_tool` send-message completions; all sampled send-message targets were `to='kimi-pair'` and successful. Example first Codex DM body: Codex said it was starting the `mcp_anyteam_grep` implementation and asked Kimi to DM the API/test expectations.

### 4. Why `M3_peer_dm_received=60` is misleading here

`collab/agents/kimi-pair.json` reports:

```json
"M3_peer_dm_received": 60,
"M3_peer_dm_sent": 0,
"notes": ["no_send_message_calls", "no_steer_ack"]
```

That `received` count reflects peer-DMs sent to the agent name `kimi-pair`; it does not mean a live Kimi loop drained those messages. The Kimi adapter exited roughly nine seconds after startup, before registration and before its `_main_loop` could call `pio.read_own_inbox(...)`. Thus the messages were addressed/delivered at the substrate level, but not visible to Kimi's model loop in this run.

### 5. Tool exposure check

Kimi's adapter is configured to expose the wrapper MCP server:

- `src/claude_anyteam/backends/kimi/invoke.py` writes `anyteam-mcp.json` with `mcpServers.anyteam.command = claude-anyteam-wrapper` and passes it to Kimi with `--mcp-config-file` for both auth preflight and real turns.
- `src/claude_anyteam/wrapper_server.py` has `EXPOSED_TOOLS` including `send_message`, `task_update`, `checkpoint_commit`, `read_inbox`, `read_config`, and the `mcp_anyteam_*` shadow tools.
- Kimi prompt text uses bare tool names, which matches the Kimi backend's documented naming behavior (`send_message`, not `mcp_anyteam_send_message`).

Because Kimi failed before a real prompt/turn, this run does not prove or disprove Kimi CLI's runtime display of `send_message`; the source-level exposure path is present.

### 6. Prompt comparison

Current prompt builders already mention team messaging:

- Codex shared prompt (`src/claude_anyteam/prompts.py`) includes `TEAM_MESSAGING_BLOCK`: plain prose is not visible and teammates must call `send_message`.
- Gemini prompt (`src/claude_anyteam/backends/gemini/prompts.py`) has the same instruction with `mcp_anyteam_send_message` and stronger `mcp_anyteam_*` tool naming guidance.
- Kimi prompt (`src/claude_anyteam/backends/kimi/prompts.py`) imports the same `TEAM_MESSAGING_BLOCK` and lists `send_message(to, body, summary?)` as a teammate coordination tool.

Kimi's task prompt is slightly shorter than Codex's and lacks the `kind?` argument text, but the run failure happened before the prompt was delivered. A Kimi prompt tweak would not have changed S8 W7 v2.

### 7. Workload check

`tools/stress/workloads/W7.json` is intentionally coupled and symmetric:

```json
"description": "Two teammates pair-program a new wrapper tool... Both must commit and coordinate via peer-DM.",
"lead_prompt_template": "... {assigned_to_a} designs the API surface and writes the test; {assigned_to_b} implements. Both must commit on the same branch. Use peer-DM, not lead pivot, to coordinate."
```

The generated run alternated expected roles: odd tasks expected Kimi as API/test designer and Codex as implementer; even tasks expected Codex as designer and Kimi as implementer. However, since Kimi never started, Codex eventually claimed and completed all 15 tasks. W7 framing is not the cause of Kimi's silence in this run.

## Hypothesis ranking

1. **Runtime auth/preflight failure (root cause; not one of the original four prompt/workload hypotheses).** High confidence. Kimi crashed at auth preflight before it could register, poll inbox, receive prompts, call tools, or send DMs.
2. **Tool exposure issue.** Low for source wiring. The wrapper exposes `send_message`, and Kimi gets the wrapper MCP config via `--mcp-config-file`. Not exercised in this run because auth failed.
3. **Prompt issue.** Low for this run. Kimi never saw its task/prose prompt. Kimi prompt wording may still be worth improving in a future calibration run, but it cannot explain S8 W7 v2.
4. **Workload artifact.** Low. W7 explicitly requires peer-DM coordination and alternating paired roles.
5. **Backend behavior: Kimi prefers `task_complete` over peer-DMs.** Not supported by this artifact. There were zero Kimi turns and zero `task_complete` messages from Kimi, so this behavioral hypothesis remains untested here.

## Recommended fix scope

Do **not** ship a Kimi prompt-level fix based on this run. The immediate remediation should be harness/scoring/runtime oriented:

1. **Scenario validity gate:** if a routed backend emits `visibility_degraded` during `adapter_spawn_auth_preflight` and never registers/starts a turn, mark that agent/run segment as backend-unavailable/auth-failed. Do not interpret its zero outbound peer-DMs as a §3 peer-efficiency behavior result.
2. **Metric attribution:** distinguish "DMs addressed to agent" from "DMs processed by a live recipient loop". `M3_peer_dm_received=60` should either be renamed/qualified or accompanied by an inbox-drained/recipient-active metric so the scorecard does not imply Kimi saw those messages.
3. **Auth classifier bug:** tighten `classify_auth_error` so bare `"429"` does not match timestamps/paths such as `S8-W7-20260429T0040Z...`. In this run, the underlying Kimi API error was `401 invalid_authentication_error`, but the visibility summary reported `quota_exhausted` because the diagnostic contained `20260429`. Match HTTP status codes on token boundaries or parse structured API error fields before falling back to substring checks.
4. **Operational preflight:** before S8 paired runs, validate each backend credential outside the stress window (or skip/xfail Kimi participants with an explicit auth-unavailable reason) so one dead agent cannot create a misleading collaboration gap.

A prompt-level Kimi nudge can be revisited only after a run where Kimi passes auth, starts turns, receives peer-DMs, and still chooses not to answer.

## Tests to add

Suggested follow-up tests (not implemented here because the root cause is not a small prompt fix):

- `tests/test_auth_preflight_classifier.py`: verify a diagnostic containing both `20260429` in a path and `401 invalid_authentication_error` classifies as `invalid_authentication`, not `quota_exhausted`; verify real `429`/`rate_limit_exceeded` diagnostics still classify as quota.
- Stress scorer regression: a synthetic run with an agent that only emits `visibility_degraded` at `adapter_spawn_auth_preflight` and never registers should be flagged as backend/auth unavailable; peer-DMs addressed to that agent should not be treated as recipient-loop acknowledgable samples.
- Optional future Kimi calibration: with valid Kimi auth, inject one plain peer-DM into an idle Kimi adapter and assert the prose handler produces a `send_message` tool call or a diagnostic fallback reply.

## Minimal patch decision

No code patch is included in this task. The observed 0 peer-DMs are explained by startup auth failure, not by Kimi prompt wording. The auth/scoring fixes above are larger than a 1-3 line prompt change and should be handled as explicit follow-up work.
