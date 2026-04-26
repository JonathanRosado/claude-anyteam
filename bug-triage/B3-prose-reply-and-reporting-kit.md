# B3 — silent `prose_reply` fallback + reporting-kit proposal

Owner: `opus-prose` (anyteam-bug-triage team)
Date: 2026-04-26
Severity B3 (silent prose fallback): **MEDIUM**
Severity reporting-kit (bug noticeability/reportability): **MEDIUM-HIGH** — separate effort from B3

Cross-references:
- Code read in full: `src/claude_anyteam/loop.py`, `src/claude_anyteam/codex.py`, `src/claude_anyteam/backends/gemini/loop.py`, `src/claude_anyteam/backends/kimi/loop.py`, `src/claude_anyteam/messages.py`, `src/claude_anyteam/protocol_io.py` (relevant slice), `src/claude_anyteam/logger.py`, `src/claude_anyteam/schema_validation.py`, `src/claude_anyteam/wrapper_server.py`, `src/claude_anyteam/spawn_shim.py`, `src/claude_anyteam/cli.py`, `src/claude_anyteam/prompts.py` and the per-backend `prompts.py`.
- Recent fix read: `25b04a5 fix(codex): _handle_prose skips fallback when model used send_message tool` (PR #12, mirroring the Kimi guard from PR #11).

---

## 1. B3 review — actual control flow ending in the silent fallback

The fallback string is emitted by exactly one site per backend, but the inputs that funnel into it are several. There is no error class propagated through any of them.

**Codex** — `src/claude_anyteam/loop.py::_handle_prose` (lines 179–265).

```
prompt = v7_prose_reply_prompt(...)
reply: str | None = None
result = None
try:
    if app_server:
        result = codex_mod.app_server_invoke(...)
    else:
        result = codex_mod.run(..., extra_args=wrapper_mcp_config_args(...), ...)
    if result.exit_code == 0 and result.last_message:
        reply = result.last_message
    else:
        logger.warn("prose.codex_fail", exit_code=..., error=result.error)
except Exception as e:
    logger.warn("prose.codex_crash", error=str(e))

# PR #12 guard: skip fallback if model already delivered via send_message tool
if reply is None and result is not None and result.exit_code == 0 and tool_call_events > 0:
    return

if reply is None:
    reply = "I received your message. I am a Codex adapter and ran into a problem generating a reply..."

pio.send_prose(team, agent, sender, reply, summary="prose_reply")
```

The fallback is reached on *any* of these conditions, all collapsed into `reply is None`:

1. `codex exec` exited non-zero (`CodexResult.error = "codex exec exited <rc>; stderr: <head>"`).
2. `codex exec` timed out (`error = "codex exec timed out after Ns"`, `exit_code=124`).
3. App Server turn failed/cancelled (`error = "app_server error: ..."` or `turn.error`).
4. App Server turn timed out (`error = "app_server turn did not complete within Ns"`, `exit_code=124`).
5. JSON Decode failure on a schema-constrained call (irrelevant here — prose path passes `schema=None`).
6. The wrapper MCP server failed handshake (the `_identity()` RuntimeError raised inside the wrapper subprocess; surfaces in Codex stderr; in tests this manifested as `connection closed: initialize response`).
7. Any unhandled Python exception inside the `try` block (caught by `except Exception` → `prose.codex_crash` log only).
8. The model produced empty `last_message` AND made zero MCP tool calls. (Prior to PR #12 the same emptiness *with* tool calls also fell through to the fallback — that is the bug the PR fixed.)

In every case, the rich detail is logged at WARN level via `logger.py` to stderr as JSON, then thrown away. Specifically:

- `prose.codex_fail` carries `exit_code` and the `result.error` string (which itself already includes the codex stderr head, the timeout duration, or the App Server error).
- `prose.codex_crash` carries the exception message.
- The wrapper itself raises `ToolError(...)` with descriptive strings (`refusing to send message to self`, `recipient X is not a member`, `task not found`, `expected exactly one occurrence`, etc.) but those land inside the Codex subprocess and are visible only in `events` (the JSONL stream we *do* keep on `CodexResult.events`) and in codex stderr (which we keep only the first 500 chars of).

**Gemini** — `src/claude_anyteam/backends/gemini/loop.py::_handle_prose` (lines 254–270). Smaller but same shape, with one extra problem:

```
try:
    result = _backend_run(state, prompt, ephemeral=True)
    if result.exit_code == 0 and result.last_message:
        reply = result.last_message
except Exception as e:
    logger.warn("gemini.prose.crash", ...)
if reply is None:
    reply = "I received your message, but the Gemini adapter could not generate a reply."
pio.send_prose(...)
```

Gemini does **not** have the PR #12 tool-call guard. If a Gemini model delivers the reply via the wrapper's `send_message` MCP tool and emits no trailing assistant text, the user gets the same double-message contradiction PR #12 fixed for Codex and PR #11 fixed for Kimi. This is a latent regression for Gemini.

**Kimi** — `src/claude_anyteam/backends/kimi/loop.py::_handle_prose` (lines 228–253). Has the tool-call guard (PR #11) but the same single canned-fallback string and the same lack of error-class propagation.

**What error info is already captured.** A lot:

| Field on `CodexResult` / Gemini-Kimi equivalent | Carries |
|---|---|
| `exit_code` | int (0, 1, 124, codex exec rc) |
| `error` | descriptive string ("codex exec timed out after 600s", "app_server error: ...", "codex exec exited 1; stderr: ...500 chars...", "schema validation failed: ...") |
| `events` | full JSONL event stream (incl. tool-call events with payload) |
| `last_message` | trailing assistant text (often empty in tool-only flows) |
| `tool_call_events` | int count |

`logger.warn("prose.codex_fail", exit_code=..., error=...)` already carries **exactly** the error class the user wants. We just don't put it on the wire.

The fallback string says *nothing* the lead can act on. It does not even say which task or message it is in response to (the `summary` is the static literal `"prose_reply"`, set at `loop.py:262`).

---

## 2. B3 critique — is "include the error class in the prose reply" the right surface?

**The user's suggestion is directionally correct but partial.** Including an abbreviated error class in the prose reply is correct because the prose reply is the *only* surface the lead is guaranteed to read for an idle prose-message exchange. Idle prose exchanges do not produce a `task_complete` or `task_blocked` (no task is in flight). So the prose reply is currently the only place to deliver any signal at all. Hiding the error there means hiding it everywhere visible.

But two corrections to the proposal:

**(a) The prose reply is the wrong place for the *full* error.** Risks of putting too much in chat:

- **PII / secrets in stderr.** `result.error` for the exec path includes 500 chars of Codex stderr. That stderr can contain absolute paths, environment variable hints, oauth tokens echoed by misbehaving CLIs, partial chat logs from the model, or filenames the user didn't intend to share.
- **Stack traces in chat.** A `prose.codex_crash` carries `str(e)` which can include a Python traceback. The chat surface is not the right place — it'll get summarized by the lead's LLM and lose information, and it pollutes the team channel.
- **Fingerprinting noise.** The lead's model has to triage prose; verbose stderr in every fallback message degrades signal-to-noise across many idle-prose interactions.
- **Reproducibility.** The lead can't search for an error class if the error is a free-form 500-char blob that varies per run.

**(b) The right shape is a stable error *class* in the prose reply, with the *details* in a structured, persistent surface the lead (or human) can pull up on demand.**

The stable error class is what makes the symptom searchable, debuggable, and routable. The detail blob (stderr head, full error string, last 20 events, file paths) belongs in a persistent artifact, not in chat.

**(c) `task_blocked` metadata is *complementary*, not a substitute.** B3 is about the *prose* path which never reaches `task_blocked` (no task is claimed). But the same error-class taxonomy should be used for `task_blocked.metadata.error_class` so that across both surfaces — idle prose failures and in-task failures — the lead sees the same vocabulary. That converges the diagnostic surface and makes triage portable.

So: **error class + correlation id in the prose reply (and in `task_blocked` metadata); detail in a per-teammate diagnostics artifact addressable by the correlation id.**

---

## 3. B3 architect — concrete proposal

### 3.1 Error-class taxonomy (additive, stable, lowercase-snake)

A small closed enum so the lead's model can pattern-match. Defined in a new `src/claude_anyteam/diagnostics.py`:

```python
class ErrorClass(str, Enum):
    BACKEND_TIMEOUT      = "backend_timeout"          # exit_code 124 from any backend
    BACKEND_NONZERO_EXIT = "backend_nonzero_exit"     # subprocess exited != 0
    BACKEND_CRASH        = "backend_crash"            # Python exception escaped the invoke
    SCHEMA_VALIDATION    = "schema_validation_failed" # parse/validate failed after retries
    MCP_HANDSHAKE        = "mcp_handshake_failed"     # wrapper subprocess startup failed
    MCP_TOOL_ERROR       = "mcp_tool_error"           # ToolError raised inside the wrapper
    APP_SERVER_TURN_FAIL = "app_server_turn_failed"   # turn.status == failed
    EMPTY_RESPONSE       = "empty_response"           # exit 0, no text, no tool calls
    UNKNOWN              = "unknown"
```

Plus a classifier:

```python
def classify(result: BackendResult, exc: Exception | None) -> ErrorClass: ...
```

`BackendResult` is the existing `CodexResult` / Gemini / Kimi result shape (they already share `exit_code`, `error`, `events`, `tool_call_events`, `last_message`). The classifier inspects in priority order:

1. `exc is not None` → `BACKEND_CRASH`.
2. `exit_code == 124` → `BACKEND_TIMEOUT`.
3. `result.error` string contains `"app_server"` and `"failed"` → `APP_SERVER_TURN_FAIL`.
4. `result.error` contains `"schema validation failed"` or `"was not valid JSON"` → `SCHEMA_VALIDATION`.
5. `result.error` contains `"connection closed: initialize response"` or `"build_server() probe failed"` → `MCP_HANDSHAKE`.
6. Any event in `result.events` is a tool-error-shaped item → `MCP_TOOL_ERROR`.
7. `exit_code != 0` → `BACKEND_NONZERO_EXIT`.
8. `exit_code == 0 and not last_message and tool_call_events == 0` → `EMPTY_RESPONSE`.
9. Else → `UNKNOWN`.

This is intentionally conservative: matches against substrings we *already emit* and own (rule of: never depend on Codex/Gemini's wording — only on strings we constructed in this repo).

### 3.2 Correlation id

Generate a short `incident_id` per failure and emit it everywhere — the prose reply, the `task_blocked.metadata`, the structured log line, and the diagnostics artifact filename. Format: `inc-<adapter_name>-<unix_ms>` (e.g. `inc-codex-runtime-1714159045123`). Cheap, no clock skew issues, sortable, copy-pasteable.

### 3.3 Diagnostics artifact

On any classified failure, write `~/.claude/teams/<team>/diagnostics/<agent>/<incident_id>.json`:

```json
{
  "incident_id": "inc-codex-runtime-1714159045123",
  "ts": "2026-04-26T17:30:45.123Z",
  "team": "anyteam-bug-triage",
  "agent": "codex-runtime",
  "backend": "codex",
  "context": "prose_reply",            // or "task" / "plan"
  "task_id": null,                     // set when in_task
  "from": "team-lead",
  "error_class": "schema_validation_failed",
  "exit_code": 0,
  "error_string": "schema validation failed: ...",
  "tool_call_events": 0,
  "last_events_tail": [ /* last 20 events */ ],
  "last_message_head": "first 400 chars",
  "stderr_tail_500": "..."
}
```

Pruning: keep last 50 incidents per agent; drop oldest. Cheap to implement (sort by mtime, unlink the tail). Failure to write must not propagate (best-effort, like everything in `_mark_blocked`).

### 3.4 Edits

**A. New module** `src/claude_anyteam/diagnostics.py`

- `ErrorClass` enum
- `classify(result, exc) -> ErrorClass`
- `record_incident(team, agent, *, backend, context, task_id, sender, result, exc, error_class) -> str` (returns `incident_id`)
- Both pure-stdlib + `Path`, no new deps.

**B. `src/claude_anyteam/loop.py::_handle_prose`** (lines 179–265). After the existing tool-call-guard early return, replace the bare canned string:

```python
if reply is None:
    incident_id = diagnostics.record_incident(
        s.team_name, s.agent_name,
        backend="codex",
        context="prose_reply",
        task_id=None,
        sender=sender,
        result=result,
        exc=last_exc,        # capture from the except clause (small refactor)
        error_class=diagnostics.classify(result, last_exc),
    )
    error_class = diagnostics.classify(result, last_exc).value
    reply = (
        f"[claude-anyteam] {s.agent_name} could not generate a reply: "
        f"{error_class}. incident_id={incident_id}. "
        f"Run `claude-anyteam diagnose {incident_id}` (or paste the path "
        f"~/.claude/teams/{s.team_name}/diagnostics/{s.agent_name}/{incident_id}.json) "
        f"to inspect details."
    )
```

Capture the caught exception by binding it (`last_exc = e` in the existing `except Exception` so it's available below the try/except — currently dropped on the floor).

Update `summary=` from the static literal `"prose_reply"` to `f"prose_reply:{error_class}"` so the lead's inbox preview shows the class without opening the message.

**C. Same changes in `backends/gemini/loop.py::_handle_prose`** + **add the missing PR #12 tool-call-guard** (Gemini is a latent dual-message regression).

**D. Same changes in `backends/kimi/loop.py::_handle_prose`** (tool-call-guard already present from PR #11).

**E. `_mark_blocked` in all three loops** — when sending `task_blocked`, set `metadata.error_class` and `metadata.incident_id` from the same classifier on the failed `result`. This is the convergence step. Today `_mark_blocked` writes `metadata={"blocked_reason": reason, "blocked_by": agent}` (loop.py:850, kimi/loop.py:485, gemini/loop.py:497). Extend with `error_class` and `incident_id`. The `send_task_blocked` payload should include them too — `protocol_io.py::send_task_blocked` accepts `reason: str` and the JSON payload is built locally; extend the signature with optional `error_class` and `incident_id` and add them to the payload dict (line 211).

**F. Update tests.** The existing
`test_prose_message_codex_fail_sends_fallback_ack` and
`test_prose_message_skips_fallback_when_codex_used_send_message_tool` keep passing if assertions about the fallback string change to assert the new shape (error class + incident id substring). Add coverage for each `ErrorClass` value via `_fake_codex_result` variants. Add coverage for the Gemini tool-call guard (currently absent).

### 3.5 What does NOT change

- Logger format (`logger.py`). Already structured JSON to stderr.
- The fallback envelope (`pio.send_prose`). Still a single `send_message`.
- The user-visible prose body length. Stays under one line.

---

## 4. Reporting kit — review (existing surfaces)

What signals does the lead receive today when a routed teammate misbehaves?

| Surface | Where | When | Detail level | Limitations |
|---|---|---|---|---|
| Inbox `prose_reply` | lead's inbox file | on idle-prose failure | one canned line, no error class, no id | the symptom of B3 |
| Inbox `task_blocked` | lead's inbox file | on in-task failure | reason string only; metadata blob has `blocked_reason`, `blocked_by` | no error class; reason is unbounded free-form |
| Inbox `task_complete` | lead's inbox file | on success | `files_changed`, `summary`, `codex_exit_code` | success path only |
| Inbox `idle_notification` | lead's inbox file | every 60s when idle | `from`, `idle_reason="available"` | does not say "I'm idle BECAUSE I crashed last task"; just "available" |
| Adapter stderr (structured JSON) | wherever Claude Code captured stdout/stderr of the spawned shim | continuously | rich: every `prose.codex_fail`, `task.codex_crash`, `app_server.steer_failed`, `wrapper.tool_call`, etc. | not surfaced anywhere the lead can see; the lead has no path to read it |
| `~/.claude/tasks/<id>.json` `metadata.blocked_reason` | task file | on `_mark_blocked` | free text | only on tasks; not for prose; not classified |
| Codex per-task event JSONL | in-memory only on `CodexResult.events` | per invocation | full JSONL stream | thrown away after each call |
| TUI presence line | host Claude Code UI | always | name + active_form + color | only what `task_update(active_form=)` writes |
| Spawn-shim dispatch log | adapter stderr at spawn | once at spawn | `spawn_shim.dispatch` event | shows route choice; not failures |
| `claude-anyteam team-roster` | stdout when invoked | on demand | name, agentType, model, backendType, color | **misleading**: the `model` column is `members[*].model` from `config.json` — the host Agent-tool param — NOT the `model`/`effort` in `~/.claude/teams/<team>/agents/<agent>.json` that the spawn shim actually forwards to the routed CLI. So a teammate can be shown as `model=claude-opus-4-7` while Codex is in fact running `gpt-5.5 effort=xhigh`. Confirmed live in this triage team. |

The lead can poll `task_list` and `read_inbox` via the wrapper. The lead **cannot** today:

- Pull recent error events for a specific teammate.
- Distinguish "Codex hit a schema error" from "Codex CLI is missing" from "wrapper MCP can't talk to itself" without reading raw stderr from a process they don't have a handle to.
- Know whether a failure was retried or final.
- Get a deterministic id to paste into a GitHub issue.
- Confirm that a `team-agent`-written model/effort override actually took effect (the roster shows the host Agent-tool model, not the spawn-time override).

What does the **human user** watching the Claude Code session see?

- Their team-lead's text response to a message that may or may not include "the codex teammate is being weird" prose, depending on whether the lead's model elects to surface it.
- The presence line of each teammate (color, name, active_form). When something goes wrong, the active_form goes stale (still shows "Running codex on task #N" until the loop crashes or hits the next claim cycle).
- Nothing else. There is no "an adapter just hit an error" toast.

This is the gap the user is calling out: the human and the lead both currently see "silence" when an adapter degrades.

---

## 5. Reporting kit — architect (smallest, highest-leverage additions)

Five additions in priority order. The first two are trivial extensions of the B3 work; the last three are net-new but small.

### 5.1 `claude-anyteam diagnose` CLI subcommand (≈ 80 LOC)

Three forms:

```
claude-anyteam diagnose                                # list recent incidents across all teams
claude-anyteam diagnose --team <team>                  # filter to one team
claude-anyteam diagnose --team <team> --agent <name>   # filter to one teammate
claude-anyteam diagnose <incident_id>                  # dump a single incident JSON
claude-anyteam diagnose --tail 5 --team <team>         # last N per teammate
```

The list output is a short table:

```
INCIDENT_ID                          AGENT          BACKEND  CONTEXT      ERROR_CLASS              WHEN
inc-codex-runtime-1714159045123      codex-runtime  codex    prose_reply  schema_validation_failed 2m ago
inc-codex-runtime-1714158800001      codex-runtime  codex    task         backend_timeout          7m ago
```

The single-incident output prints the JSON file pretty-printed plus a "to share, paste the contents of:" line with the absolute path.

The lead can call this as a shell command from their Claude Code host because `claude-anyteam` is already on PATH after install.

### 5.2 Structured `task_blocked` reasons (already in B3 §3.4 item E)

Extend `send_task_blocked(...)` and `_mark_blocked(...)` to carry `error_class` and `incident_id`. Lead-side code that reads the inbox (or the lead's LLM) sees `{"kind":"task_blocked","task_id":"7","reason":"...","error_class":"schema_validation_failed","incident_id":"inc-..."}`. Trivial to triage.

Idle notifications should also carry a "last_error_at_or_since_spawn" optional field so a teammate that has only failed since spawn announces it on every idle ping. Today `IdleNotificationOut` carries `idle_reason="available"` regardless; overload to `idle_reason="available_with_recent_errors"` when the in-memory error count > 0 since adapter start. Honest signal: "I'm idle, but I am unhealthy."

### 5.3 `claude-anyteam bundle` — incident bundle command (≈ 120 LOC)

```
claude-anyteam bundle                                  # bundle all teammates of all teams
claude-anyteam bundle --team <team>                    # one team
claude-anyteam bundle --team <team> --agent <name>     # one teammate
claude-anyteam bundle --incident <incident_id>         # one incident
```

Produces `claude-anyteam-bundle-<unix>.tar.gz` in the cwd containing:

```
bundle/
  README.md                                  # auto-generated, matches template in 5.5
  versions.txt                               # claude-anyteam, codex, gemini, kimi, uv, python
  teams/<team>/config.json                   # roster (member prompts redacted)
  teams/<team>/agents/<name>.json            # per-teammate model/effort overrides
  teams/<team>/inboxes/<name>.json           # last 50 messages each (PII-aware)
  teams/<team>/diagnostics/<name>/*.json     # last 20 incidents
  spawn_shim.events.jsonl                    # tail of recent spawn_shim dispatch events if accessible
  settings.json.redacted                     # ~/.claude/settings.json with secrets stripped
```

Redaction: drop `apiKey`, `accessToken`, `secret`, `password`, `auth*` keys (case-insensitive). Print "redacted N keys" at the end. The bundle is meant to be GitHub-issue-pasteable.

### 5.4 `claude-anyteam status` — quick health query (≈ 40 LOC)

```
claude-anyteam status [--team <team>]
```

Output:

```
Team: anyteam-bug-triage
  codex-runtime    codex   ok        last_seen=12s    incidents_total=0
  codex-packaging  codex   degraded  last_seen=04s    incidents_total=2  last_error_class=mcp_handshake_failed
  opus-architect   claude  in-process
  opus-prose       claude  in-process
```

`degraded` = at least one incident since spawn. `last_seen` = mtime of the inbox file or of the agent's most recent self-write. Cheap, observable in CI, useful for the lead to query before assigning work.

### 5.5 `team-roster` shows the *effective* spawn-time config (≈ 30 LOC)

**The gap.** `claude-anyteam team-roster --team <T>` (`src/claude_anyteam/team_cli.py:331–381`) builds each row from `members[*]` in `~/.claude/teams/<team>/config.json` only. The `model` column on a routed teammate is therefore the host Agent-tool param (e.g. `claude-opus-4-7`), not the model the spawn shim actually forwards to Codex/Gemini/Kimi. The per-teammate file at `~/.claude/teams/<team>/agents/<agent>.json` (the file `team-agent` writes; the file `spawn_shim._load_agent_config` reads at every spawn) is never consulted. So a user looking at the roster cannot tell whether their `team-agent --model gpt-5.5 --effort xhigh` actually took effect. Confirmed live in this triage team.

**Why this matters for the reporting kit.** The roster is the natural diagnostic surface — it is the first thing both the lead and the user reach for when something looks off. Hiding the spawn-time config from the roster is exactly the kind of "noticeability" gap the reporting-kit workstream is meant to close. A future bug report that says "my codex teammate is using the wrong model" is undiagnosable today because the roster will *agree* with the host config while disagreeing with reality.

**Proposal — extend, do not replace.** Two complementary changes, both in `src/claude_anyteam/team_cli.py`:

1. **Default-on resolved view in `team-roster`.** Extend `_RosterRow` with `adapter_model`, `adapter_effort`, and `config_source` populated by reading `agent_config_path(team, agent)` (the helper already exists at line 55) using the same `_existing_dict` reader. `config_source` is one of `"per-teammate"`, `"env"`, or `"default"` — only the first is actually visible from disk; treat the others as `"default"` for now and leave `"env"` for a follow-up that probes `CLAUDE_ANYTEAM_MODEL` / `CLAUDE_ANYTEAM_EFFORT`. Add a column to the human-readable table:

    ```
    codex-packaging  type=claude-anyteam  host_model=claude-opus-4-7  adapter_model=gpt-5.5  effort=xhigh   source=per-teammate  backend=tmux  color=cyan
    codex-runtime    type=claude-anyteam  host_model=claude-opus-4-7  adapter_model=(default)                 source=default        backend=tmux  color=cyan
    opus-architect   type=general-purpose host_model=claude-opus-4-7  adapter_model=—                          source=in-process     backend=tmux  color=cyan
    ```

    For `claude-*` (in-process Claude) teammates print `adapter_model=—` and `source=in-process` — the per-teammate file does not apply to them. For routed prefixes (`codex-`, `gemini-`, `kimi-`) print the resolved values or `(default)` if the file is absent.

    A `--no-resolve` flag preserves the legacy behaviour for scripts that grep the old format.

2. **`claude-anyteam team-config <agent> --team <T>`** for the deeper view. Prints the resolved spawn-time config the shim *would* use for one teammate, in JSON, including:

    ```
    {
      "team": "anyteam-bug-triage",
      "agent": "codex-packaging",
      "spawn_route": "codex",                      // or "gemini" / "kimi" / "claude-native"
      "adapter_binary": "/home/rosado/.local/bin/claude-anyteam",
      "host_model": "claude-opus-4-7",
      "host_agent_type": "claude-anyteam",
      "adapter_argv": [
        "/home/rosado/.local/bin/claude-anyteam",
        "--name", "codex-packaging",
        "--team", "anyteam-bug-triage",
        "--model", "gpt-5.5",
        "--effort", "xhigh"
      ],
      "config_sources": {
        "model": "/home/rosado/.claude/teams/anyteam-bug-triage/agents/codex-packaging.json",
        "effort": "/home/rosado/.claude/teams/anyteam-bug-triage/agents/codex-packaging.json"
      }
    }
    ```

    This is exactly what the spawn shim builds in `_adapter_argv` (`spawn_shim.py:249–260`); reuse the helper. The `adapter_argv` field is the literal commandline that will be `os.execv`'d on next spawn — copy-pasteable into a bug report and reproducible.

**Test coverage to add:** roster row for a routed teammate with a per-teammate config file shows the resolved values; without the file shows `(default) / source=default`; for a `claude-*` teammate shows the `in-process` placeholder; `--no-resolve` reproduces the current output byte-for-byte; `team-config` matches the argv `spawn_shim` would build (assert via the existing `_adapter_argv` helper).

**Why this is small.** No new files, no protocol changes, no new dependencies. Just open one extra JSON per row in the roster, and reuse the spawn shim's existing argv builder for `team-config`. Total surface change is in one module.

**Bug-template integration.** §5.6 (the report template) is updated to ask for `claude-anyteam team-roster --team <T>` and `claude-anyteam team-config <agent> --team <T>` for the failing teammate. Both run quickly, both are paste-friendly, both would have surfaced the silent mismatch the lead just hit in this triage team without anyone needing to read JSON files by hand.

### 5.6 Bug report template `bug-triage/REPORT_TEMPLATE.md` (template only; no code)

A short, opinionated template:

```markdown
## Bug summary
<one sentence>

## Severity (your guess)
[ ] high — Claude Code session unusable
[ ] medium — one or more teammates degraded
[ ] low — diagnostic / cosmetic

## What happened
<observed behaviour, in chat or in TUI>

## What I expected
<one sentence>

## Repro
<steps; "create a codex- teammate, send it 'hi', it replies '<canned>'", etc.>

## Diagnostics
- Run: `claude-anyteam status` and paste here.
- Run: `claude-anyteam team-roster --team <T>` (resolved view) and paste here.
- Run: `claude-anyteam team-config <agent> --team <T>` for the failing teammate and paste the JSON.
- Run: `claude-anyteam diagnose --tail 5` and paste here.
- If asked: paste the bundle from `claude-anyteam bundle` (redacted by default).

## Versions
- Run: `claude-anyteam --version`, `codex --version`, `gemini --version`, `kimi --version`, `uv --version`, `python --version`.

## Project memory references (optional)
- If you've seen this before, link to the relevant file under `~/.claude/projects/.../memory/`.
```

This template **could have populated this triage's findings** verbatim if it had existed: B1 (`agentType` patch needed) would have surfaced via `incident_class=mcp_handshake_failed` in `claude-anyteam status` instead of as a silent prose failure; the user's "include the error class" suggestion in B3 would have arrived already classified.

### 5.7 What the user pastes into a future bug report

For a typical "my codex teammate is broken" report, the user pastes (in order, all from one terminal):

1. `claude-anyteam --version` (one line)
2. `claude-anyteam status` (a small table)
3. `claude-anyteam diagnose --tail 3 --team <team>` (three rows)
4. `claude-anyteam diagnose <incident_id>` for the one row that looks suspicious (one JSON blob, ≤ 1 KB)
5. If asked: `claude-anyteam bundle` and attach the resulting `.tar.gz`.

That is the complete kit. No log-spelunking, no `tail -f /tmp/<adapter>.stderr`, no asking the user to grep their own home directory.

---

## 6. Productivity lens — Claude vs Codex/Gemini/Kimi

Claude is in-process. When a Claude teammate hits an error:

- The error surfaces in the host Claude Code UI (visible to the human).
- Stack traces are rendered.
- The session is alive — the lead can ask Claude follow-up questions about its own failure.
- The human is signal-rich.

A routed teammate — Codex, Gemini, Kimi — runs as a subprocess that the host Claude Code UI does not own. When it fails:

- Stderr disappears into wherever Claude Code put it (typically not visible to the human at all).
- The lead sees a single canned prose line ("ran into a problem") or a `task_blocked` with a free-form `reason`.
- The teammate keeps polling its inbox; the only signal that anything is wrong is "the response was vague."
- The human watching the session sees an unchanged TUI presence line.

This is a structural disadvantage that compounds:

- **Time cost per failure.** With Claude, the human/lead has the context to act in seconds. With Codex/Gemini/Kimi, the lead spends 5–30 LLM turns guessing what went wrong — or gives up and reassigns to a Claude teammate.
- **Trust cost.** Repeated silent failures train the lead's model to treat routed teammates as unreliable, biasing assignment toward Claude — exactly the regression we're trying to prevent.
- **Reporting cost.** When a real bug needs to be reported upstream, the user has nothing to paste, so reports are vague, and triage stalls (this triage is itself an example).

The B3 fix closes the worst-case (silent prose failures) by promoting the error class into the chat surface and the detail into a persistent artifact. The reporting kit makes Codex/Gemini/Kimi feel **closer** to in-process Claude in observability terms: the human still doesn't get a stack trace in the UI, but they get a one-command path to the same information, and the lead's model has a stable vocabulary to triage with. That removes most of the productivity tax that diagnostic opacity currently imposes.

---

## Appendix A — minimal, non-negotiable acceptance criteria for the B3 fix

1. The canned fallback string is replaced by a one-line message that contains the error class and a copyable `incident_id`.
2. A diagnostics file is written at `~/.claude/teams/<team>/diagnostics/<agent>/<incident_id>.json` on every classified prose-path failure.
3. `task_blocked` payloads carry `error_class` and `incident_id`.
4. Gemini's `_handle_prose` gains the same tool-call guard PR #12 added for Codex (latent regression).
5. All three backends share one classifier (`diagnostics.py`) — no per-backend forks of the taxonomy.
6. Tests assert (a) the new prose message shape, (b) the diagnostics artifact existence, (c) `task_blocked` metadata shape, (d) Gemini does not double-send when the model used `send_message`.

## Appendix B — non-goals (deliberately out of scope)

- Streaming live tails of adapter stderr to the lead's inbox. Too noisy; would defeat the rate-limit on idle pings.
- Persisting incidents to a remote service. Local files are sufficient for self-hosted bug reporting.
- Renaming `task_blocked` / `prose_reply` envelope kinds. Backwards compatibility wins.
- Rebuilding the wrapper's tool surface. B1/B2 cover that; this work is purely about how failures are *reported*.
