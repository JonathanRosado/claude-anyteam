# B4 — Gemini productivity gap: prose-first / no-action turns

## Executive finding

The code does **not** support a pure handshake-root-cause diagnosis. The failing Gemini path was almost certainly the default **headless** backend (`--backend` is not forwarded by the spawn shim, and `GeminiSettings.backend` defaults to `"headless"`), which amplifies the problem because each Gemini task is a single `gemini --prompt ...` shot. But the more actionable root cause is **task/prose routing plus Gemini-specific prompt framing**:

- Direct spawn and wake-up messages that are plain text are handled by `_handle_prose()`, whose prompt tells Gemini to **reply briefly** via `mcp_anyteam_send_message`, not to execute file work.
- Real task execution only happens after `_find_and_claim()` finds a pending task in the shared task store.
- `task_assignment` protocol messages are parsed but ignored by the Gemini loop.
- When a task does run, the task prompt lists `mcp_anyteam_write_file`, but does not force the first assistant action to be a tool call; in headless mode, a planning/acknowledgment/final-JSON turn ends the whole opportunity to act.

So hypothesis #3 (backend mode) wins as the structural amplifier, hypothesis #1 (prompt/action framing) wins as the immediate model-behavior trigger, and hypothesis #2 (handshake idleness) is not supported by the code.

## 1. Review — actual control flow

### Spawn → backend selection

1. `src/claude_anyteam/spawn_shim.py:249-260` builds adapter argv from `--name`, `--team`, optional `--plan-mode`, and per-agent `model`/`effort` only. It does **not** forward `--backend`.
2. `src/claude_anyteam/backends/gemini/config.py:38` sets `backend="headless"`; `from_env()` uses `CLAUDE_ANYTEAM_GEMINI_BACKEND` or defaults to `"headless"` at lines 67-70.
3. Therefore a normal `gemini-*` spawn with no env override uses headless mode. The field team used per-agent configs with `model=gemini-3-pro` and `effort=high`, but no backend key, so the failing run was headless by default.

### Adapter startup / registration

1. `backends/gemini/loop.py:47-76` feature-tests the selected backend, then registers the member metadata as `model="gemini-cli"`.
2. `registration.py:96-150` only writes/ensures team config and inbox files. If the member already exists, it returns the existing entry and does not mutate it.
3. This registration metadata is not a Gemini model system prompt. There is no Gemini assistant turn consumed here.

### Inbox handling

`backends/gemini/loop.py:144-170` loops as follows:

1. Read own inbox.
2. For each message, call `_handle_message()`.
3. Then, if not shutting down, find and claim a pending task from `pio.list_tasks()`.
4. If no claimable tasks, send an idle notification once per minute.

`_handle_message()` at `loop.py:177-188` handles only:

- plain text → `_handle_prose()`
- `shutdown_request`
- `plan_approval_request`
- `steer`
- everything else → debug no-op

Important: `TaskAssignmentIn` exists in `messages.py`, but Gemini never handles it. A JSON `task_assignment` DM is currently a protocol no-op.

### Plain-text spawn/wake-up messages

Plain text enters `_handle_prose()` at `loop.py:254-270`. That function builds `prompts.prose_reply_prompt()`, which says:

- “A teammate named X sent you …”
- “Reply briefly and helpfully using `mcp_anyteam_send_message(...)`.”
- “Plain prose is fine for your local final answer.”

That is a **reply task**, not an execution task. This matters because the problematic live messages were direct wake-ups like “you have not written files; begin now.” Codex sometimes overrode the reply framing when the prompt was extremely action-first; Gemini obeyed the reply framing more literally.

### Claimed task execution

If `_find_and_claim()` finds a pending task, `_execute_task()` at `loop.py:441-491`:

1. Builds `backends/gemini/prompts.py::task_prompt()`.
2. Injects queued `steer` messages only on attempt 1.
3. Appends the task-complete JSON schema inline.
4. Calls the selected backend.
5. If Gemini returns schema-valid JSON, marks the task completed and sends `task_complete` regardless of whether Gemini used tools.

The task prompt at `backends/gemini/prompts.py:27-34` does list the right MCP tool names (`mcp_anyteam_write_file`, etc.), but it does not say “first action must be a tool call” or “do not return final JSON until you have made a state-changing tool call.”

### Headless backend turn

`backends/gemini/invoke.py:405-436` runs:

```text
gemini --prompt <prompt> --output-format stream-json --approval-mode yolo [--model ...] [--resume ...]
```

Then `invoke.py:440-495` parses stream JSON, counts `tool_use` events, concatenates assistant message chunks, validates the final JSON, and returns `CodexResult`. If Gemini produces a plan paragraph or acknowledgment with no tool calls, that one headless shot is over.

### ACP backend turn

`backends/gemini/acp.py:385-480` starts Gemini ACP, initializes, opens or loads a session, sets mode/model, calls `session/prompt`, drains notifications, counts tool events, validates final JSON, then closes the ACP child. ACP gives better session/event semantics, but the current loop still sends one prompt per invocation; it does not implement Codex-style mid-turn steering.

### Where “plan-first paragraph instead of a tool call” comes from

The code does not add a “plan first” system prompt. The likely sources are:

1. Gemini model behavior under an execution prompt that does not explicitly force tool-first action.
2. Plain-text direct-message routing into a reply-only prompt.
3. Headless single-shot mode: a prose-first response consumes the whole turn.
4. Tool-name mismatch in human prompts: a Claude-style “Write tool” instruction is not literally the advertised Gemini tool; Gemini sees `mcp_anyteam_write_file`.

## 2. Critique of hypotheses and suggested fixes

### H1 — System prompt drift / prompt phrasing: partially supported

Gemini has a backend-specific prompt (`backends/gemini/prompts.py`), not the shared Codex prompt in `src/claude_anyteam/prompts.py`. The Gemini task prompt is reasonable but too permissive for executor work. The prose prompt is actively counterproductive for action-first direct messages because it asks for a reply.

Adding “your first response must be a tool call” helps, but only if it is placed in the **Gemini task prompt** and in any direct-message-to-task-brief wrapper. Putting it only in shared `prompts.py` would miss Gemini’s backend-specific prompt and risk Codex regressions.

### H2 — Handshake idleness: not supported

Registration writes metadata and creates inboxes; it does not send a prompt to Gemini. ACP `initialize`/`session_new` also do not consume a user task turn. There is no evidence in code that the first real user brief is displaced by a hidden handshake assistant response.

### H3 — Backend mode: supported as a structural amplifier

The field run used headless unless an env override was set. Headless mode is one-shot: no tool call in that response means no action until the next loop/prompt. ACP would improve observability and session semantics, but switching to ACP alone would not fix plain-text direct messages being handled as prose replies.

### H4 — Tool exposure: mostly not supported, with one wording caveat

Tools are configured:

- `invoke.write_mcp_settings()` writes `mcpServers.anyteam` and disables Gemini core tools.
- The prompt advertises `mcp_anyteam_write_file`, `mcp_anyteam_edit_file`, `mcp_anyteam_shell`, etc.
- Tests cover stream JSON tool parsing and ACP tool-event normalization.

The caveat is human wording: team-lead messages used “Write tool,” while the Gemini prompt advertises `mcp_anyteam_write_file`. Gemini can infer the mapping, but we should make it explicit.

### H5 — Idle-ping interaction: not the root cause, but the diagnostic is bad

The idle loop is doing what it was coded to do: if there is no claimable pending task, send `idle_notification`. The problem is that the lead cannot tell whether Gemini truly had nothing to do, handled the wake-up as a prose reply, or ran a task but made zero tool calls. That should be surfaced explicitly.

## 3. Architect — concrete proposal

### A. Add Gemini action-first execution contract

Edit `src/claude_anyteam/backends/gemini/prompts.py`.

Add a reusable preamble used by `task_prompt()`:

```python
ACTION_FIRST_EXECUTION = """
# Gemini execution contract
If this task requires filesystem, shell, search, or web work, your first assistant action must be an mcp_anyteam_* tool call, not an acknowledgement, plan, or final JSON.
For file creation, a human instruction like "use the Write tool" means call mcp_anyteam_write_file(path, content, mode="overwrite").
Do not return the final JSON until you have either completed the required tool work or are genuinely blocked.
If you are blocked before any tool call, say why in the final JSON summary and leave files_changed empty.
Never list a path in files_changed unless you actually created or edited it.
"""
```

Then insert it before `# MCP tools available`. Keep this Gemini-only.

Also tighten the second-attempt text in `loop.py:451-452` from schema-only retry to action retry:

```text
PRIOR ATTEMPT FAILED: you did not produce valid task output. Do not acknowledge or plan. If work is still required, call mcp_anyteam_* tools first; only then return the JSON object.
```

### B. Route action-looking direct messages as task steers, not prose replies

Edit `src/claude_anyteam/backends/gemini/loop.py`.

1. Import and handle `TaskAssignmentIn` in `_handle_message()`:
   - Queue a `QueuedSteer` with `task_id=payload.task_id` and a message containing subject + description.
   - This makes task-assignment DMs meaningful instead of debug no-ops.

2. Add `_looks_like_task_brief(text: str) -> bool` for lead DMs containing signals such as:
   - “Your immediate first action”
   - “use the Write tool” / “call the Write tool”
   - “Begin now” plus file paths
   - “Your task is task #...”
   - multiple markdown sections (`# Subject`, `# Description`, `## Workflow`) and absolute repo paths

3. In `_handle_prose()`, before invoking Gemini for a prose reply:
   - If sender is `team-lead` and `_looks_like_task_brief(msg.text)`, queue it as a `QueuedSteer` for the next claimable task and send a short structured diagnostic to lead (`task_brief_queued`) instead of asking Gemini to reply.
   - If no claimable task exists, emit `task_brief_no_claimable_task` so the lead sees that the adapter needs an actual task or `steer` message.

This targets the live failure shape without relying on Gemini to disobey a “reply briefly” prompt.

### C. Detect and surface no-tool task turns

Edit `src/claude_anyteam/backends/gemini/loop.py` after `_backend_run()` returns in `_execute_task()`.

Recommended policy:

1. If `result.exit_code == 0`, `result.structured is not None`, but `result.tool_call_events == 0` and `files_changed` is empty:
   - On attempt 1, send `task_idle_no_tool_calls` to lead, retry with stricter action-first text.
   - On attempt 2, mark blocked with reason `Gemini produced schema-valid output without tool calls or file changes`.
2. If `files_changed` is non-empty but `tool_call_events == 0`, log and send a warning diagnostic (`task_complete_unverified_tool_count`) but do not necessarily fail; event parsing could be incomplete.
3. Include `last_message_head`, `backend`, `model`, `effort`, `session_id`, `attempt`, `tool_call_events`, and `files_changed_count` in the diagnostic payload.

Example lead-facing payload:

```json
{
  "kind": "task_idle_no_tool_calls",
  "backend": "gemini",
  "transport": "headless",
  "task_id": "7",
  "attempt": 1,
  "model": "gemini-3-pro",
  "effort": "high",
  "session_id": "...",
  "tool_call_events": 0,
  "files_changed_count": 0,
  "last_message_head": "I will start by reading the backlog...",
  "next_action": "retrying with action-first prompt"
}
```

Implementation surface: add a small helper in `loop.py`, using `pio.send_json_to_lead()`.

### D. Backend default: do not treat ACP as the whole fix

Current failing mode: headless. I recommend:

1. **Short term:** keep the default `headless` for compatibility, but document that Gemini executor roles should be tested with `CLAUDE_ANYTEAM_GEMINI_BACKEND=acp`.
2. **Medium term:** switch default to `acp` only after an A/B run shows fewer no-tool turns and no crash/recovery regressions. The code already has ACP crash hygiene and permission handling; it is the better long-term executor transport, but it is not a substitute for action-first routing.
3. **If switching default:** edit `config.py:38` and `config.py:68` from `"headless"` to `"acp"`, and update `cli.py` help text plus tests that assert the default.

### E. Backend-specific task-brief intro wrapper

Yes: add this for Gemini, not Codex. Codex already has its own prompts and App Server path; tuning the shared Codex prompt for Gemini would risk regressing the path that performed well after B1/B2.

A safe pattern is:

- `backends/gemini/prompts.py::task_prompt(..., action_first=True)`
- `backends/gemini/loop.py` passes `action_first=True` for executor tasks.
- Future A/B env: `CLAUDE_ANYTEAM_GEMINI_PROMPT_VARIANT=baseline|action_first`.

### F. A/B test plan

Add a small benchmark harness, e.g. `scripts/ab_gemini_productivity.py` or an integration test gated behind `GEMINI_LIVE_TEST=1`:

Matrix:

- backend: `headless`, `acp`
- prompt variant: `baseline`, `action_first`
- model: configured Gemini model
- N repetitions per cell: at least 5

Task: create 3 small markdown files in a temp directory and return task-complete JSON.

Metrics:

- time-to-first-tool-call
- time-to-first-file
- final files produced
- tool_call_events
- schema success rate
- no-tool/prose-only rate
- task-complete latency

Acceptance target before default changes: action-first reduces no-tool turns to near zero and ACP does not regress latency/crash rate. Keep this Gemini-only so Codex prompt tuning remains isolated.

## 4. Diagnostic surface

Current real-time signal is insufficient. The lead sees idle pings, but not the distinction among:

1. Gemini received a direct message and treated it as prose.
2. Gemini ran a task and produced no tool calls.
3. Gemini ran tools but event parsing missed them.
4. Gemini had no claimable task.

Add these structured lead-visible events:

- `task_brief_queued`: direct task-looking DM was queued as a next-turn steer.
- `task_brief_no_claimable_task`: direct task-looking DM arrived, but no pending task exists.
- `task_assignment_queued`: JSON task assignment was converted to a task-specific steer.
- `task_idle_no_tool_calls`: task turn completed without any tool call events.
- `task_complete_unverified_tool_count`: final JSON listed files but no tool events were observed.

Yes: `task_idle_no_tool_calls` is the right surface for B4. It turns “Gemini is idle” into “Gemini responded but did not act,” which is what the lead needed in real time.

## 5. Productivity lens

Observed rough numbers from the field report and inbox timestamps:

- Original `gemini-research-1`: spawned at 2026-04-26 07:14:56Z-equivalent team timestamp; shutdown at 07:31:36. No files in ~16m40s and multiple wake-ups.
- Original `gemini-research-2`: spawned at 07:15:17; shutdown at 07:31:38. No files in ~16m20s.
- Re-spawn `gemini-research-1-2`: action-first prompt at 07:32:35; action nudge at 07:36:07; by 07:54:39 the foundational directory had many files, though authorship was ambiguous. Time-to-visible-output upper bound: ~22m from respawn, ~18.5m from nudge.
- Re-spawn `gemini-research-2-2`: action-first prompt at 07:32:56; action nudge at 07:36:09; by 07:54:30 only two named notes were clearly observed. That is roughly 2 files / 18.3 min = ~0.11 files/min; if the field report’s “2-4 ref notes” is used, ~0.11-0.22 files/min.
- Original Gemini rate before action-first prompts: 0 files / ~32 aggregate agent-minutes = 0 files/min.
- Codex comparison from the field report: after B1/B2 were unblocked, `codex-research-3` produced 20+ ref notes, and Codex writers produced ~32-35 files each. Exact wall-clock is not instrumented here, but output volume was an order of magnitude higher than Gemini’s 2-4 clearly attributed notes.
- Claude comparison: `opus-researcher` produced 24 reference/index artifacts and took over foundational backup when Gemini stayed silent; `opus-author` produced 25+ chapters.

Productivity cost: Gemini consumed at least ~16 minutes of lead wait time per original researcher with zero file output, then required re-spawn plus an action-first template. In practical team terms, Opus had to cover Gemini’s queue, and team-lead had to spend coordination turns designing prompts that effectively bypassed Gemini’s plan/acknowledge tendency.

## 6. Open question — model default and warnings

If the remaining root cause is Gemini model preference for planning/acknowledgment, then model choice matters. The field run already used `gemini-3-pro` at high effort and still lagged, so model selection alone will not fix B4. But older Gemini models are a known weak fit for executor roles.

`team-agent` currently says it “does not validate the model slug against any backend catalog” (`team_cli.py` help text). Add a non-blocking warning when:

- agent name starts with `gemini-`, and
- model is missing or appears older than the recommended executor family, e.g. not `gemini-3*`, or explicitly `gemini-2*` / legacy preview.

Suggested warning:

```text
warning: gemini-* executor roles are sensitive to model drift. For file-writing tasks, prefer the current Gemini 3 Pro executor recommendation and action-first prompts; older Gemini models may plan/acknowledge instead of using tools.
```

Also consider a roster diagnostic: if a Gemini teammate has `backend=headless` and an older model, `team-roster --diagnose` should flag “higher no-action risk.”
