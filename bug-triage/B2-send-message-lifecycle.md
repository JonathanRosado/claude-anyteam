# B2 — `send_message` MCP tool not exposed to existing sessions after `team-patch`

**Author:** opus-architect (anyteam-bug-triage)
**Date:** 2026-04-26
**Severity:** **HIGH (simple-fix surface, deeper-rearchitecture available)**
**Reproducer source:** v0.5.0 field test, codex-write-1/2/3 + codex-research-2/3.
**Status:** Diagnosed end-to-end. Two-line fix recovers; ten-line fix prevents recurrence; one-line model relaxation in vendored `claude_teams` removes the failure class entirely.

---

## 1. Review — what is actually happening

I read in full: `registration.py`, `wrapper_server.py`, `team_cli.py`, `spawn_shim.py`,
`config.py`, `loop.py`, `codex.py` (incl. the App Server invoke path),
`app_server.py`, vendored `claude_teams/{teams,models,spawner,server,tasks}.py`,
and the Mar/Apr "agent-spawn-missing-agentType" known-issue doc. The TUI-presence
project memory was respected (registration.py:118-122 "aspirational" comment is
about TUI metadata alignment, not MCP allowlist enforcement — that comment is
unrelated to this bug; no judgment is built on it).

### 1.1 Where `agentType` lives in the system

| Layer | Read/Write | Code |
|---|---|---|
| Adapter spawn (shim → `claude-anyteam`) | **never reads agentType** (routes purely on `--agent-name` prefix) | `spawn_shim.py:198-218`, `_codex_route`/`_gemini_route`/`_kimi_route` |
| Adapter `register()` writes its own member entry | **writes** `agentType="claude-anyteam"` only when the name is **not yet** in `members`. If the entry already exists (Agent-tool spawn path), `register()` keeps the existing entry unchanged. | `registration.py:120-124` and 138 |
| Wrapper MCP `build_server()` startup | **does not read team config**; only resolves identity from CLI args / env. All tools registered unconditionally as `@mcp.tool` decorators. | `wrapper_server.py:160-176` |
| Wrapper `send_message` and `read_config` tools (per-call) | **read the entire `~/.claude/teams/<team>/config.json`** via `_cs_teams.read_config(team)` → `TeamConfig.model_validate(raw)`. Pydantic visits every member through the discriminated `MemberUnion`. | `wrapper_server.py:202`, `586-589`; `claude_teams/teams.py:93-99` |
| Vendored `TeammateMember` model | `agent_type: str = Field(alias="agentType")` — **required, no default**. Any member missing `agentType` causes pydantic `ValidationError` for the **whole** `TeamConfig`. | `claude_teams/models.py:35` |
| `team-patch` CLI | rewrites `members[*].agentType` → `claude-anyteam` (or `--agent-type` value) and atomic-writes the file. **Does not signal running adapters.** | `team_cli.py:236-296` |
| Host (Claude Code) `--agent-type` CLI | passed to a *spawned-from-tmux* `claude` instance via `spawner.py:102` (vendored claude-teams' tmux flow). Only relevant for native Claude teammates spawned by cs50victor's path. **Not** relevant to claude-anyteam-shim teammates. | `claude_teams/spawner.py:87-109` |

### 1.2 Adapter lifecycle vs wrapper lifecycle

Each Codex turn (App Server **or** legacy `codex exec`) opens a brand-new
`AppServerClient`, spawns a fresh `codex app-server` subprocess, and that
subprocess in turn spawns a fresh wrapper MCP via the
`mcp_servers.claude_anyteam_wrapper.command/args` config injected on
`thread/start` (`codex.py:678-692`). `client.close()` in the `finally` of
`app_server_invoke` tears the whole tree down (`codex.py:824-825`).

**Consequence:** the wrapper subprocess is *not* long-lived. There is no
"handshake-time decision" inside the wrapper that pins agentType. Every
Codex turn re-launches the wrapper, which re-opens `config.json` on the
first `send_message` / `read_config` call.

### 1.3 What actually broke during the field test

Reconstructed mechanism (matches symptom exactly):

1. Lead spawned Codex teammates via `Agent(team_name=..., name=codex-..., …)`.
   The host Agent-tool spawn path appends each member to `config.json`
   **without** setting `agentType` (documented in
   `docs/internal/known-issues/agent-spawn-missing-agenttype.md`).
2. Adapters started up. `register()` walked `members`, found their own name
   already present, and took the *existing-entry* branch
   (`registration.py:121-124`). The pre-existing entry's `agentType` was
   missing — `register()` did **not** repair it. The adapter has no
   self-heal step here.
3. While idle, Codex teammates poll inbox; no problem. As soon as a Codex
   teammate handled prose or a task and tried to call wrapper
   `send_message`, the wrapper's `_cs_teams.read_config(team)` raised
   `pydantic.ValidationError` because **some sibling** member was missing
   `agentType`. (One bad sibling kills the whole config validation; this
   is the most insidious property — it is not localized to the misconfigured
   teammate.)
4. FastMCP surfaced the ValidationError as a tool-call error. The Codex LLM
   observed the failure, then in subsequent turns Codex's own conversational
   memory carried the "send_message returned an error" observation forward
   and the model began *describing* the tool as "not available in this
   session" — a hallucinated diagnosis on top of a real error.
5. `team-patch --all-external` ran. The on-disk config was repaired.
   The next-turn wrapper subprocess would have read the patched config
   correctly. But because the affected Codex sessions had already accreted
   "send_message broken" into their own thread/turn context, the LLM kept
   reporting unavailability even after the file was fixed. Re-spawning
   them resets that context and the symptom disappears.

This explains the user-observed lifecycle ("re-spawning is required") without
needing any handshake-time pinning of agentType. The pinning is in **Codex's
in-context belief**, not in the wrapper or in any host MCP allowlist.

### 1.4 What enforces what

- The MCP allowlist for the **wrapper** is the `EXPOSED_TOOLS` constant
  (`wrapper_server.py:54-68`). It is **static**. agentType has no effect.
- The MCP allowlist for **host-side claude-teams** (cs50victor's standalone
  server in `claude_teams/server.py`) is also unconditional `@mcp.tool`
  registration; agentType is not consulted there either.
- The closest thing to "agentType-as-MCP-gate" is in upstream Claude Code's
  Agent-tool harness (outside this repo), where `subagent_type` chooses
  which built-in tool whitelist a sub-agent inherits. claude-anyteam's
  shim-spawned teammates don't touch that path at all.

So the user's mental model — "wrapper validates agentType at session-handshake
time" — does **not** match the code. The actual gate is pydantic's
required-field validation on **every read of the team config**, by **any**
caller, regardless of which member triggered it.

---

## 2. Critique — is the user's diagnosis correct?

Mostly **no**, in important ways. Symptom-correct, mechanism-incorrect.

| User's claim | Reality |
|---|---|
| "Wrapper validates `agentType` at session-handshake time" | Wrapper does **not** validate at handshake (`build_server` never reads `config.json`). Validation happens lazily inside `send_message` / `read_config` per call, via the pydantic model. |
| "Existing sessions retain the original `agentType=general-purpose` MCP allowlist" | There is no allowlist that varies by `agentType` for shim-routed Codex teammates. The wrapper exposes the same 13 tools to everyone. |
| "Re-spawning is required to pick up the patched config" | The on-disk file IS picked up next turn (wrapper is per-turn). Re-spawn is required to clear *Codex's own conversational memory* of the prior failure. That's an LLM-context problem, not a config-cache problem. |
| "team-patch updates `agentType`" | True. |

### 2.1 Is option (a) — "document team-patch only takes effect on subsequent spawns" — sufficient?

**No.** It papers over the actual sharp edge:

1. The Agent-tool spawn omitting `agentType` is **upstream of team-patch**.
   Documenting "patch before you spawn" doesn't help, because the
   Agent-tool flow is "Agent() → member written to config.json with no
   agentType → adapter starts → adapter's register() declines to mutate
   the existing entry". The user can't team-patch *before* spawning when
   the Agent tool itself is creating the broken row.
2. Even if the lead patches *immediately after* every Agent() call, there is
   a TOCTOU window where the adapter starts up and the next Codex turn
   tries `send_message` before the patch lands. We've already seen this on
   2026-04-24 (`feedback_team_config_agenttype.md`).
3. Documentation does nothing for the **insidious one-bad-sibling-kills-everyone**
   property: a team where teammate B has a malformed entry breaks teammate
   A's `send_message` even though A is fine.

### 2.2 Is option (b) — "wrapper re-evaluates agentType per message" — feasible?

**Yes, trivially**, because the wrapper *already* re-reads the config per
call. The only question is what to do with the value once read. There is
nothing in the wrapper that *needs* `agentType`. It is a pure pydantic
schema-conformance failure, not a logic gate.

So the right framing is not "re-evaluate per message" — that's already
happening — but "**don't fail validation on a non-load-bearing field**".

### 2.3 The insidious underlying problem

There are actually **three** stacked problems, only the first of which the
user noticed:

1. **Schema strictness on a non-essential field.** `agent_type` is required
   in `TeammateMember`, but no claude-anyteam code path actually consumes
   it — neither the wrapper, the adapter, nor the Codex subprocess uses
   the member's `agent_type` for any decision. It's metadata. Making it
   strictly required forces the *entire team config* to fail validation
   when one entry is malformed, breaking unrelated teammates.

2. **`register()` is silently idempotent on the wrong axis.** When `register()`
   finds an existing entry under our name, it preserves *whatever was there*
   — even if the existing entry violates the schema we care about. The
   intent of "idempotent" was "don't duplicate"; the implementation also
   "don't repair", which is a different choice we never explicitly made.
   This is config-as-handshake-snapshot semantics colliding with
   config-as-live-state semantics. The adapter has the metadata to write a
   correct entry; it should at minimum *upgrade* the broken one.

3. **`team-patch` is fire-and-forget.** It mutates the file but tells no
   one. Running adapters have no signal that the file changed. team-roster
   has no "stale config" indicator. There is no health-check the lead can
   run that says "your Codex teammate's wrapper would fail right now if it
   tried to send a message." The bug was *invisible* from the lead's seat
   until a Codex teammate spoke up — which a Codex teammate, hallucinating
   "tool unavailable", may not be able to articulate accurately.

Problems 1 and 2 are about the **principle that configuration changes
should not require restart**: a fresh adapter load picks up the right
state, but a re-run of an already-loaded adapter uses stale state. Today,
that's only a problem because Codex's own LLM context keeps the dead
observation around — but it's the kind of problem that festers under any
future "long-lived wrapper" optimization or any caching layer added to
read_config.

---

## 3. Architect — concrete proposal

I propose a **layered fix**: surface (Fix S) + structure (Fix R) + diagnostic
(Fix D). Layered so we can ship S in v0.5.1, R in v0.5.2, D in v0.6.0 without
any of them blocking the others.

### 3.1 Fix S — surface, 1 line, ships immediately

**File:** `src/claude_teams/models.py:35`

**Change:**
```python
# before
agent_type: str = Field(alias="agentType")
# after
agent_type: str = Field(alias="agentType", default="claude-anyteam")
```

(Same change at line 22 for `LeadMember` for symmetry, default
`"team-lead"` matching `teams.py:67`.)

**What this buys:** wrapper `send_message` / `read_config` no longer
explode when any sibling member is missing `agentType`. Pydantic fills
the default and validation passes. Behaviour is unchanged for correctly-
configured teams. Existing v0.5.0 installs auto-recover on next adapter
turn — no migration step required.

**Why this is safe:** zero claude-anyteam code path consumes
`member.agent_type` for any decision. The field is metadata-only. Grep
confirms: only mention is `claude_teams/spawner.py:102` (tmux spawn —
unused on the shim path) and `models.py` itself.

**Risk:** divergence from upstream cs50victor's `claude_teams`. We already
vendor (`vendor/claude-teams/UPSTREAM-README.md`); the divergence is
documented locally and acceptable. If we ever upstream, frame it as
"required→optional with sane default for adapter-spawned teammates".

### 3.2 Fix R — structural self-heal, ~10 lines

**File:** `src/claude_anyteam/registration.py:120-148`

**Change:** when the existing-entry branch fires, **upgrade in place**
any field that doesn't match `metadata`. Specifically, if
`existing.get("agentType") != metadata.agent_type`, set it and re-write
the config under the same lock we already hold.

**Pseudo-diff:**
```python
for existing in members:
    if isinstance(existing, dict) and existing.get("name") == settings.agent_name:
        entry = existing
        # NEW: repair non-load-bearing identity fields if the entry was
        # written by a path that didn't know our metadata (Agent-tool
        # spawn, manual edits, older adapter versions).
        repaired_keys = []
        if existing.get("agentType") != metadata.agent_type:
            existing["agentType"] = metadata.agent_type
            repaired_keys.append("agentType")
        if existing.get("backendType") != metadata.backend_type:
            existing["backendType"] = metadata.backend_type
            repaired_keys.append("backendType")
        if repaired_keys:
            serialized = json.dumps(cfg, indent=2) + "\n"
            _atomic_write_text(cfg_path, serialized)
            logger.info(
                "registration.repaired",
                team=settings.team_name,
                name=settings.agent_name,
                fields=repaired_keys,
            )
        break
else:
    # ... existing add-new-entry branch unchanged ...
```

**What this buys:** every adapter restart self-heals its own row. Combined
with Fix S, the team becomes resilient to the entire failure class:
- Agent-tool spawn omits agentType → adapter starts → register() repairs.
- Manual edit corrupts a row → adapter restart repairs.
- Future fields added to BackendMetadata → automatically populated on
  restart.

**Migration path:** no migration needed — it just works on next adapter
process. Existing v0.5.0 adapters that are still running won't self-heal,
but Fix S already covers them at the validation layer.

**Risk:** we are now mutating an entry the lead may have edited
intentionally. Mitigations:
- Limit repair to fields in `BackendMetadata` (agentType, backendType),
  never touch `color`, `prompt`, `subscriptions`, `cwd`.
- Log the repair so it's visible in adapter stderr.
- Make the repair list overridable via a `--no-repair-config` flag if a
  user really needs it (low priority — I'd ship without).

### 3.3 Fix D — diagnostic surface, ~20 lines

The bug took the user a long time to triage because it was silent from the
lead's seat. Two diagnostic improvements pay for themselves the next time
something else goes wrong with the wrapper:

**D1. Wrapper logs config validation outcome at first read, surface to lead.**

In `wrapper_server.py::send_message`, wrap the `_cs_teams.read_config`
call:
```python
try:
    cfg = _cs_teams.read_config(team)
except FileNotFoundError:
    raise ToolError(f"team {team!r} not found on disk")
except ValidationError as e:
    # Surface a repair command, not just a stack trace, so Codex can
    # relay an actionable suggestion to the lead.
    raise ToolError(
        f"team config at ~/.claude/teams/{team}/config.json failed "
        f"validation: {e.errors()[0]['msg']!r} on "
        f"{'.'.join(str(p) for p in e.errors()[0]['loc'])}. "
        f"Repair with: `claude-anyteam team-patch --team {team} --all-external`"
    )
```
This converts a pydantic stack trace into a Codex-readable repair hint.
Codex teammates will then either run the repair themselves (they have
shell tool access — `mcp_anyteam_shell`) or relay the hint to the lead.

**D2. `team-roster` grows a `health` column.**

In `team_cli.py::_team_roster_command`, after listing rows, attempt
`TeamConfig.model_validate(cfg)` and surface any validation errors in a
trailing block:
```
HEALTH: 2 member(s) missing required fields:
  codex-write-1: agentType
  codex-research-2: agentType
Fix: claude-anyteam team-patch --team <team> --all-external
```
This makes the bug visible to the lead at lead-roster-check time,
before any teammate hits it.

**D3 (optional). Adapter logs at first inbox poll.**

In `loop.py::_main_loop`, after the first inbox poll, do a one-shot
sanity validation of the team config and log `loop.config_validation_warning`
with the same repair hint. Goes to adapter stderr → /tmp logs → visible
during incident triage.

### 3.4 Should `team-patch` warn/error when run after spawn?

**No.** `team-patch` is the *cure*, not the cause; running it after spawn
is the entire point. What we want instead:

- After-the-fact diagnostic (Fix D2) so the lead can run team-patch
  *proactively* on a freshly-spawned team without waiting for a teammate
  to fail.
- Optional: `team-patch` could print a hint *if* it actually changed
  anything, naming any teammates that may have prior in-context "tool
  broken" memory and recommending re-spawn. Two lines of output, maybe
  one if-statement. Worth it.

### 3.5 Should `team-roster` surface "needs re-spawn"?

Only on the diagnostic axis covered by Fix D2 ("HEALTH: ... missing
required fields"). A general "needs re-spawn" status is hard to surface
truthfully — we don't know whether a Codex teammate has dead context
without inspecting its codex-cli session history, which is out of scope.
The HEALTH row at least tells the lead *"there is a problem here that
team-patch will fix; consider re-spawning afterwards if a Codex teammate
was already affected."*

### 3.6 Alternatives I considered and rejected

- **Make the wrapper bypass `_cs_teams.read_config` and parse the raw
  JSON directly to extract member names.** Works around pydantic
  strictness but loses schema coverage entirely. Fix S keeps validation
  intact; this would weaken it.
- **Have `team-patch` send a "config-changed" inbox message to every
  teammate.** Useless — adapters don't cache config; they re-read per
  call. The signalling has no recipient with state to update.
- **Make `register()` *always* overwrite the entry**, not just repair
  specific fields. Too aggressive; would clobber lead edits to color,
  cwd, etc. Field-scoped repair (Fix R) is the conservative version.
- **Per-message agentType re-evaluation as the user suggested.** This is
  technically already happening (per-call read), so there's nothing to
  add — the right fix is at the schema layer (S) and the writer layer
  (R), not the reader layer.

### 3.7 Recommended ship order

1. **v0.5.1 — Fix S only.** One-line model relaxation. Stops the bleeding
   for every existing v0.5.0 install on next adapter turn. Zero risk.
2. **v0.5.2 — Fix R + Fix D2.** Adapter self-heal + roster HEALTH row.
   Medium effort, high diagnostic value.
3. **v0.6.0 — Fix D1 + D3 + optional team-patch hint.** Polish; no longer
   in the critical path because S+R already cover the failure.

---

## 4. Productivity lens — does this make Codex teammates less productive than Claude?

**Yes. Materially. Quantifiably.**

When B2 fires:

| Capability | Claude teammate | Codex teammate |
|---|---|---|
| Receive task assignment | ✅ via host-managed inbox | ✅ via inbox (adapter polls) |
| Read inbox | ✅ host built-in | ✅ wrapper `read_inbox` (does not call `read_config`, unaffected) |
| Update tasks | ✅ host built-in | ✅ wrapper `task_update` (uses `_cs_tasks`, no team-config validation) |
| **Send message to peer** | ✅ host built-in `SendMessage` | ❌ wrapper `send_message` raises ValidationError on first call |
| **Read team roster** | ✅ host built-in | ❌ wrapper `read_config` same failure |
| Self-coordinate with peers | ✅ DM another teammate freely | ❌ blocked — every cross-teammate message goes through lead |
| Acknowledge prose | ✅ tool-mediated | ⚠️ falls back to adapter `pio.send_prose` because `_handle_prose` catches the failure (loop.py:241-265). So the LLM-generated reply is lost; only a stub ack is sent. |

**Concrete productivity cost:**

1. **Self-coordination collapses.** Codex teammates cannot DM each other.
   Every cross-teammate message is forced through team-lead, which
   serializes coordination on the lead's bandwidth. A team of 5 Codex
   teammates that would normally have 10 peer channels collapses to a
   star topology with the lead as bottleneck.

2. **Prose replies degrade to stub acks.** `_handle_prose` (loop.py:179-265)
   relies on Codex calling wrapper `send_message` to deliver the LLM-
   composed reply (the tool-call path); when that fails, the fallback
   path (`pio.send_prose`) sends a canned "I received your message"
   instead of the actual reply Codex composed. The peer sees a generic
   ack, not the real answer. **The information was generated and then
   thrown away.**

3. **Hallucinated "tool unavailable" infects the LLM context.** Once
   Codex sees the failure, subsequent turns describe the tool as
   unavailable — even after `team-patch` fixes the on-disk config. This
   means the only reliable recovery is **re-spawn**, which loses the
   teammate's accumulated task context. Re-spawn cost is non-trivial:
   the teammate forgets prior task history, file context, and whatever
   reasoning chain they had built.

4. **Lead burns cycles diagnosing.** The lead sees Codex teammates
   reporting "tool unavailable" with no trace pointing at agentType.
   Without Fix D, the lead's only debug route is to read
   `~/.claude/teams/<team>/config.json` and notice the missing field
   manually — which presumes the lead already knows this is the problem.
   The user reports this happened during the v0.5.0 field test; the
   triage time is the productivity cost, not the fix time.

**Net effect:** in a mixed team, Claude teammates run at full peer-to-peer
bandwidth while Codex teammates degrade to lead-mediated single-point
coordination plus stub-ack replies — a structural disadvantage that maps
1:1 onto the "Codex should be as productive as Claude" goal. Fix S alone
closes most of the gap (peer DMs work, prose replies deliver). Fixes R + D
close the rest by making the failure mode obsolete and the next failure
of similar shape diagnosable.

---

## 5. Summary

- **Root cause:** `agent_type` is a required pydantic field on
  `TeammateMember` (`vendor/claude-teams` model), and one bad sibling
  member (often produced by Agent-tool spawn that omits the field) breaks
  `read_config` for the **entire team**, which in turn breaks wrapper
  `send_message` for **all** Codex teammates. The adapter's
  `register()` sees an existing entry and refuses to repair it; the user's
  belief about "handshake-time pinning" is wrong but the symptom is real.
  Re-spawning helps only because it clears Codex's own LLM-context memory
  of the prior failure — the on-disk fix from `team-patch` is already
  picked up per-turn by the wrapper subprocess.
- **Recommended path:** ship Fix S (1-line `default="claude-anyteam"` on
  the pydantic field) in v0.5.1; ship Fix R (registration self-heal) +
  Fix D2 (team-roster HEALTH row) in v0.5.2; ship Fix D1 (Codex-readable
  repair hint in ToolError) + Fix D3 (adapter startup config-validation
  log) in v0.6.0.
- **Biggest risk:** Fix S diverges vendored `claude_teams` from upstream
  cs50victor — must be documented in `vendor/claude-teams/VENDORING.md`
  and re-checked when next pulling upstream. Mitigation: keep the diff
  surgical (one default value), call it out in the commit message,
  consider proposing upstream as "optional with sane default".
