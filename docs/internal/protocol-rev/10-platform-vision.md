# 10 — Platform vision: agent-teams as the platform

**Author:** opus-vision
**Date:** 2026-04-27
**Type:** vision; longer-arc companion to opus-roadmap's pragmatic v0.7.x plan (09-roadmap.md)
**Status:** aspirational but grounded — every aspirational claim cites prior art (06), our existing substrate (CLAUDE.md / docs/architecture.md / 07-protocol-spec.md), or opus-elegance's leak ledger and SDK design (08).

---

## 0. Executive summary

claude-anyteam started as a Claude Code plugin that lets `codex-*` / `gemini-*` / `kimi-*` CLIs participate in Agent Teams as first-class teammates. Twelve months from now it should be a host-agnostic protocol and SDK that any coding agent can implement, that any coordinator UI can host, and that preserves every harness's unique capabilities natively across heterogeneous teams. The substrate exists today — file-based mailbox, atomic task claims, capability-layered registration — and the moat is structural: routers cannot match it without throwing out their session loop (`08-elegance-and-gaps.md` §A0). The platform is the act of generalizing what we already do and giving it a name other ecosystems can adopt.

This document is the long-horizon companion to opus-roadmap's pragmatic v0.7.x plan. They diverge in time-horizon, not in mechanism: opus-roadmap closes the visibility-parity gap on Claude Code; opus-vision generalizes the same mechanism into a platform.

The three north stars from `CLAUDE.md` are the design lens for the platform exactly as they are for v0.7.x:

- **§1 harness preservation** — the harness IS the teammate. Capabilities flow through; the protocol carries declarations, never flattens. *This is the architectural moat.*
- **§2 visibility parity** — routed teammates are operationally observable at native fidelity. *This is the observer-side consequence.*
- **§3 peer efficiency** — peers coordinate among themselves at native pace. *This is the team-side consequence.*

§1 is the architecture. §2 and §3 are what makes the architecture deliver value to the two audiences that observe a team: the lead and the peers. All three ship together.

What changes when we generalize to a platform: the substrate gets a name (`agent-teams`) separate from any single host; the SDK gets extracted from the adapter so any agent author can implement it; the storage interface gets pluggable so future hosts (Cursor, OpenCode, Sourcegraph, IDE-internal coordinators) can adopt the protocol without inheriting Claude Code's specific filesystem; the capability layer gets first-class manifests (Agent Cards) so heterogeneity becomes legible; and the visibility envelope gets wire-versioned so consumers across ecosystems can build dashboards, replay tools, and audit trails on the same data. Everything follows from invariants we already enforce.

---

## 1. "Beautiful" defined as explicit invariants

"Beautiful" is not aesthetics; it is a compact set of invariants every part of the platform satisfies. They are bucketed under the three north stars they serve.

### §1 invariants — harness preservation

**I1. Two layers, never one.** *Transport* is universal (mailbox JSON, atomic task claims, lifecycle, file shapes, locks). *Capability* is per-harness (`turn_steer`, `thread_fork`, `permission_bridge`, `swarm`, `host_tool_visibility`, `session_persistence`, `cancellation`, `accepts_peer_steer`). Conflating the two produces routers; routers cannot bolt the capability layer back on later because their architecture has no place for it. (`CLAUDE.md` §1 "the two layers"; `07-protocol-spec.md` §0 item 11; `08-elegance-and-gaps.md` §1.1.1.)

**I2. Harness capabilities are first-class.** The protocol carries capability declarations and routes activity; it never flattens to a lowest common denominator. When a backend has a feature its peers don't (Codex `thread/fork`, Gemini ACP permission bridge, Kimi internal swarm, Codex `turn/steer`), the team must (a) surface the capability via registration, (b) teach peers how to invoke it via system-prompt fragments, and (c) never strip it down. (`CLAUDE.md` §1; `07-protocol-spec.md` §0 item 1.)

**I3. The protocol is a coordinator, not a kernel.** It carries identity, lifecycle, mailbox, task state, and capability declarations. It does NOT own the agentic loop, the tool definitions, or the model's prompt tuning. Those belong to each harness. (`CLAUDE.md` §1; `07-protocol-spec.md` §0 item 14.)

**I4. Capability declarations are explicit, not inferred.** Every teammate publishes an Agent Card at registration; peers and the lead read it; behavior is gated on declared capabilities, never inferred from name or model slug. Unknown capability keys are treated as `false`/`unsupported`. (`07-protocol-spec.md` §1.7, §5.6; `08-elegance-and-gaps.md` §4 CD-1..6.)

**I5. Lossy mappings live at the edge, never at call sites.** `--effort` and `--model` are protocol surfaces normalized once; per-backend `invoke.py` modules translate to whatever the harness CLI actually accepts. The lead never has to know that `xhigh` collapses to "thinking on" for Kimi. (`docs/architecture.md` §"protocol-first, lossy backend mappings are acceptable"; `08-elegance-and-gaps.md` E6.)

### §2 invariants — visibility parity

**I6. Every event is enveloped.** `{schema_version, event_id, timestamp, team, agent, backend, task_id, turn_id, seq, severity, kind, summary, visibility, payload}`. No raw `print()`s, no stderr-only signals, no events that exist solely as side effects of file writes. (`07-protocol-spec.md` §7.2 envelope; `bug-triage/B9-visibility-parity-investigation.md` §6.)

**I7. One message, one channel.** Every event has exactly one canonical channel (event log, mailbox, task state, stderr). Fan-out is policy on top of a single source, not duplication of source. (`07-protocol-spec.md` §7.4; B9 §6.1 four-channel split.)

**I8. Backend-specific tool names are preserved verbatim.** A Codex `commandExecution` is reported as `commandExecution`; a Claude native `Bash` is reported as `Bash`; a Gemini ACP tool name is reported with its actual name. The lead seeing `commandExecution` for a Codex teammate next to `Bash` for a Claude teammate is a feature: it tells them which harness produced the event. The protocol normalizes envelope shape, not content. (`07-protocol-spec.md` §7.1.)

**I9. Every error carries diagnostic detail.** No collapse-to-prose-fallback. Errors have `error_class`, `severity`, `recovery_hint`, and `incident_ref` so leads can reproduce. (`08-elegance-and-gaps.md` E9.)

### §3 invariants — peer efficiency

**I10. Peer-to-peer is first-class.** The protocol does not privilege lead-to-peer over peer-to-peer; both surfaces are uniform and complete. A peer can DM a peer, hand off a task to a peer, steer a peer (subject to declared authorization), and read a peer's capability manifest with the same wire shapes the lead uses. (`CLAUDE.md` §3; `07-protocol-spec.md` §5.7 peer-to-peer addendum.)

**I11. Heterogeneity does not slow the team down.** Routed-and-native teams coordinate at native pace — peer-to-peer round trips happen at native-Claude latency, not router-translated latency. The benchmark is concrete: any pair of teammates can DM, steer, hand off, and discover capabilities at <100ms peer-to-peer round trip, regardless of harness. (`CLAUDE.md` §3 "the team is only as fast as its slowest peer-to-peer hand-off.")

**I12. Typed messages over parsed prose.** Every wire message carries a `kind` discriminator. Consumers route by type, never by reading natural language. Idle pings, lifecycle, substantive DMs, capability invocations, and visibility events are all distinguishable without LLM reasoning. (`CLAUDE.md` §3 idle-noise failure mode; `07-protocol-spec.md` §5.1 payload taxonomy.)

**I13. Capability manifests pre-load at team formation.** Cheap roster discovery (`members[].capabilities`) is one config-read; rich manifests are one MCP round-trip cached at team-formation time. Peers do not pay round-trip costs every time they consider invoking a peer's primitive. (`07-protocol-spec.md` §1.7 "two-layer architecture"; `08-elegance-and-gaps.md` §6.4 hybrid lookup.)

### Cross-cutting platform invariants

**I14. Schema versioning is mandatory from day one.** Every wire envelope carries `schema_version`. Consumers refuse to deserialize `schema_version > supported`. We never have an unversioned-payload moment. (`08-elegance-and-gaps.md` §6.1 design principle 3, A9.)

**I15. No transport coupling.** Files today, ZeroMQ tomorrow, WebSocket the day after. The protocol lives in the data shape, not the substrate. The `TeamStorage` interface (`08-elegance-and-gaps.md` §6.2) makes this explicit. Filesystem default ships; Redis (multi-host), SQLite (single-host high-throughput), in-memory (tests), and host-private storage (Cursor's IPC, OpenCode's WebSocket) all implement the same interface.

**I16. No backend coupling.** A coordinator that handles one teammate handles N. A teammate that talks the protocol works against any coordinator. The kit's `Teammate` base class is host-agnostic by construction; the kit's main entrypoint (`run()`) parses argv from any host that conforms to the spawn contract. (`08-elegance-and-gaps.md` §6.5; `07-protocol-spec.md` §3.4 shim contract.)

These sixteen invariants are the platform's "beautiful." Every API decision, wire format, runtime choice, or migration step in the rest of this document is justified by reference to one of them.

---

## 2. Protocol surface

The protocol is small. The complexity is in the harnesses; the protocol is what they all share. Six surfaces, each carrying one job.

### 2.1 Identity

Every participant has a stable triple: `{team, name, agentId}` where `agentId = "<name>@<team>"`. Sanitized character class is `^[A-Za-z0-9_-]+$`, length ≤ 64. `name == "team-lead"` is reserved for the coordinator role. (`07-protocol-spec.md` §1.6.)

The platform extends this with one optional field: `harness` (e.g. `codex-cli`, `gemini-cli`, `kimi-cli`, `glm-cli`, `deepseek-cli`, `claude-native`). Coordinators that want to render harness badges read it; coordinators that don't can ignore it. The field is in the Agent Card (§2.4); the legacy `members[].agentType` and `backendType` fields stay backward-compatible per `07-protocol-spec.md` §1.2.

### 2.2 Envelope

Every wire message is enveloped. The envelope is small and stable:

```json
{
  "schema_version": 1,
  "kind": "<discriminator>",
  "event_id": "<agent>:<turn_or_task>:<seq>",
  "timestamp": "ISO-8601 UTC ms",
  "team": "<safe-team>",
  "agent": "<safe-agent>",
  "task_id": "<id> | null",
  "turn_id": "<backend-turn-id> | null",
  "seq": 42,
  "severity": "debug | info | warn | error",
  "summary": "short human-readable sentence",
  "visibility": { "mailbox": false, "task_state": false, "event_log": true, "stderr": true },
  "payload": { /* per-kind */ }
}
```

`kind` is the discriminator. Three categories of kind:

- **Lifecycle** — `task_assignment`, `task_complete`, `task_blocked`, `idle_notification`, `shutdown_request`, `shutdown_response`, `plan_approval_request`, `plan_approval_response`, `permission_request`, `permission_response`, `steer`, `steer_ack`. (`07-protocol-spec.md` §5.1.)
- **Visibility events** — `turn_started`, `turn_progress`, `tool_event`, `artifact_event`, `turn_completed`, `turn_failed`, `visibility_degraded`, `capability_changed`. (`07-protocol-spec.md` §7.3.)
- **Coordination** — `message` (peer DM prose), `broadcast` (one→all). (`07-protocol-spec.md` §5.1.)

The envelope does not change across kinds. The payload does. This is LSP's discipline applied to agent teams: tiny base envelope, request ids, progress tokens, notifications, cancellation; no backend-specific method leakage at call sites. (`06-prior-art-survey.md` §14 LSP "very high as protocol grammar.")

### 2.3 Channels

Six channels carry the envelope, each with a distinct purpose, audience, and persistence model. The split is from `07-protocol-spec.md` §5 with one platform-level addition (channel 6 capability discovery promoted from "proposed" to "first-class"):

| # | Channel | Persistence | Audience | Purpose |
|---|---|---|---|---|
| 1 | **Inbox JSON messages** | durable (file or storage backend) | lead, peers | typed payloads + prose; primary lead-visible signal |
| 2 | **Task state** | durable | lead, peers | "what is this teammate doing now"; status + activeForm + metadata |
| 3 | **Coordinator-private state** | volatile (process memory) | coordinator UI | live presence row, isIdle, progress, message preview (host-internal — Claude Code's `AppState.tasks` is the canonical example) |
| 4 | **Wrapper MCP tool calls** | side-effect-only | backends | how routed CLIs invoke `send_message` / `task_update` / shadow tools |
| 5 | **Append-only event log** | durable | lead, peers, future tools | full-fidelity tool/artifact stream without inbox spam |
| 6 | **Capability discovery** | durable (config) + on-demand (manifest) | lead, peers | what each teammate can do |

The channels are pluggable behind the `TeamStorage` interface (§3 SDK). A Cursor host's channel 3 is its own internal state tree; a future web coordinator's channel 3 is browser memory. Channel 5 is filesystem JSONL today; could be Kafka, Loki, or SQLite tomorrow. The protocol doesn't care.

### 2.4 Identity manifest — the Agent Card

The Agent Card is the protocol's expression of north star §1. Every teammate publishes one at registration; peers and the lead read it. It has two layers, matching the §1 two-layer architecture:

**Cheap roster flags** (`members[].capabilities` in the team config — one file read):

```json
{
  "schema_version": 1,
  "turn_steer": "live | next_turn_boundary | unsupported",
  "thread_fork": true,
  "permission_bridge": "lead_inbox | auto_allow | unsupported",
  "swarm": false,
  "host_tool_visibility": "rich | counted | absent",
  "session_persistence": true,
  "cancellation": "live | turn_boundary | unsupported",
  "accepts_peer_steer": false
}
```

These are the keys `07-protocol-spec.md` §1.7 names; new keys can be added via additive spec increments. Unknown keys are `unsupported` to consumers.

**Rich manifest** (`mcp_anyteam_capability_manifest(agent_name, capability)` — one MCP round-trip, cached at team formation):

```json
{
  "version": "1",
  "schema": { /* JSON Schema for the capability's input */ },
  "description": "<one-sentence what>",
  "when_to_use": "<semantic guidance for invoking>",
  "when_not_to": "<failure-mode-prevention guidance>",
  "failure_modes": ["CLOSED_LIST_OF_NAMES", ...]
}
```

This shape — version + schema + description + when_to_use + when_not_to + failure_modes — is the same shape MCP already uses for tools. The platform extends it one level up: from "what tools can I call" to "what unique primitives does this teammate offer." Adopting the MCP shape lets the existing MCP toolchain (clients, validators, schema generators) work for capability manifests too. (`08-elegance-and-gaps.md` §1.1.1, §6.4.)

The Agent Card precedent is A2A's Agent Card (`06-prior-art-survey.md` §3). The two-layer hybrid is ACP's `initialize` + per-method capabilities (§1) and MCP's `tools/list` + `tools/call` (§2). We do not coin new terms; we synthesize from what works.

### 2.5 Lifecycle

Six lifecycle protocols, all via the envelope. The state machines are in `07-protocol-spec.md` §6:

- **Idle** — teammate emits `idle_notification` every 60s when no claimable task. Coordinators filter by `kind`, never by parsing prose (I12).
- **Shutdown** — lead sends `shutdown_request`; teammate sends `shutdown_response{approve}`; if approved, teammate exits. Idempotent by `request_id`.
- **Plan approval** — plan-mode teammates send `plan_approval_request`; lead replies `plan_approval_response{approved}`. Optional capability per teammate.
- **Permission** — teammate emits `permission_request`; lead emits `permission_response`. Capability `permission_bridge` declares whether a backend supports this.
- **Steer** — lead (or peer, if `accepts_peer_steer`) emits `steer`; teammate's `steer_received` hook delivers (live or next-turn per `delivery_mode`); teammate emits `steer_ack`.
- **Spawn** — host calls the platform's spawn shim; shim routes by name prefix; adapter self-registers; host's mirror task makes the teammate visible in the coordinator UI.

All six work today, with two open questions (07 §10.11 peer-to-peer steer authorization, §10.12 peer-DM exposure consistency). The platform makes the open questions answerable by the same `accepts_peer_steer` and `peer_dm` capability declarations that already exist as gaps in the leak ledger.

### 2.6 Capabilities

The capability vocabulary is the platform's most consequential decision. Initial set (per `07-protocol-spec.md` §1.7 and `08-elegance-and-gaps.md` §4 CD-1..6):

| Capability | Type | Notes |
|---|---|---|
| `turn_steer` | enum: `live`/`next_turn_boundary`/`unsupported` | Codex App Server and Gemini ACP are `live`; Codex exec, Gemini headless, Kimi are `next_turn_boundary`. |
| `thread_fork` | bool | Codex App Server only. Cross-task memory continuity. |
| `permission_bridge` | enum: `lead_inbox`/`auto_allow`/`unsupported` | Gemini ACP non-trusted modes only. |
| `swarm` | bool | Kimi declares true; no other backend exposes internal sub-agents. |
| `host_tool_visibility` | enum: `rich`/`counted`/`absent` | Codex App Server is `counted`; native Claude is `rich`; headless backends are `absent` until v0.7.x event-log lands. |
| `session_persistence` | bool | Resume across restarts. Codex `exec resume`, Gemini `--resume`, Kimi resume hint. |
| `cancellation` | enum: `live`/`turn_boundary`/`unsupported` | Mid-turn cancel is harness-specific. |
| `accepts_peer_steer` | bool | Distinguishes "lead-only steer" from "peer-can-steer" — the §3 leak surfaced by the proto-rev research session. |
| `soft_non_progress_watchdog` | bool | Currently Codex App Server only; declares the 300s checkpoint. |
| `approval_policy` | enum: `never`/`default`/`plan`/`trusted` | Static policy when no permission bridge fires. |
| `host_tool_surface` | enum: `<harness>-native`/`mcp_anyteam`/... | Tells peers what tool surface the harness reasons through. |

New capabilities are added via additive spec increments. The vocabulary is open: any harness can declare a capability the spec doesn't yet name, with the rich manifest providing the schema. The protocol carries the declaration even when the consumer doesn't know what to do with it.

This is the load-bearing decision. Without I4 (capabilities are declared), the heterogeneity that I2 (capabilities flow through) preserves is invisible. The lead sees a roster but cannot tell that `codex-runtime` accepts live steer and `gemini-bob` does not; cannot tell that `kimi-cora` runs an internal swarm; cannot tell that `glm-eve` exposes a code interpreter the others don't. Capability declarations are how the moat becomes operational.

---

## 3. Agent author SDK

The SDK is the artifact that converts the platform from a Claude Code plugin into a host-agnostic library other implementations can adopt. opus-elegance §6 spelled out the Python API in detail; this section presents it as the platform's central asset and adds a TypeScript sketch to show shape-portability.

### 3.1 Design principles (from `08-elegance-and-gaps.md` §6.1, lifted verbatim)

1. **Host-agnostic from day one.** The kit's storage interface accepts any backend. Filesystem (`~/.claude/teams/<team>/`) ships as default. Cursor / OpenCode / future hosts plug in their own.
2. **Convergence point for three implementations.** cs50victor's MCP server, our vendored `claude_teams/` Python, and a future TypeScript port all converge on the kit's wire spec.
3. **Schema versioning is mandatory from day one, not bolted on.** Every wire envelope carries `schema_version`. The kit refuses to deserialize `schema_version > supported`.
4. **Three primitives, not one.** `agent_card()` (self-knowledge), `find_capability()` (cross-team discovery), `peer_prompt_fragment()` (cross-team how-to). Missing any one makes the moat invisible (§2 violation) or unusable (§1 violation).
5. **The kit knows nothing about how the harness produces tokens.** It only carries identity, lifecycle, mailbox, task state, capabilities, and visibility events. Backend `execute_task` / `reply_to_prose` are the only required overrides.

### 3.2 Python sketch

The full API is in `08-elegance-and-gaps.md` §6.2–§6.5. The minimum to ship a new harness adapter:

```python
from agent_teams import Teammate, Task, TaskResult, run

class GlmTeammate(Teammate):
    def agent_card(self) -> dict:
        return {
            "schema_version": 1,
            "harness": "glm-cli",
            "harness_version": _detect_glm_version(),
            "transport": "headless-stream-json",
            "capabilities": {
                "turn_steer": {
                    "version": "1",
                    "schema": {"type": "object", "required": ["text"],
                               "properties": {"text": {"type": "string"}}},
                    "description": "Inject text at next turn boundary.",
                    "when_to_use": "When you need to redirect a glm-* task between turns.",
                    "when_not_to": "Don't expect mid-turn delivery; this is next-boundary.",
                    "failure_modes": ["STEER_BUFFERED_NEXT_BOUNDARY", "STEER_AUTH_REJECTED"],
                    "delivery_mode": "next_turn",
                    "expiry_semantics": "task_count",
                    "authorization": "lead_only",
                },
                "host_tool_surface": {
                    "version": "1",
                    "schema": {"type": "string", "enum": ["glm-native"]},
                    "description": "GLM uses its own native shell/file/web tools.",
                    "when_to_use": "Read-only signal for peers.",
                    "when_not_to": "Not callable.",
                    "failure_modes": [],
                },
                "code_interpreter": {                      # GLM's unique capability
                    "version": "1",
                    "schema": {"type": "object", "required": ["code"],
                               "properties": {"code": {"type": "string"},
                                              "kernel": {"type": "string", "default": "python3"}}},
                    "description": "Execute code in a sandboxed kernel and return value/plot/output.",
                    "when_to_use": "When a peer needs computational verification, plotting, or numeric work.",
                    "when_not_to": "Don't route filesystem-mutating work here; the kernel is sandboxed.",
                    "failure_modes": ["KERNEL_TIMEOUT", "KERNEL_OOM", "CODE_RAISED"],
                },
            },
        }

    async def execute_task(self, task: Task) -> TaskResult:
        self.emit_turn_started(task.id, model="glm-4.5")
        result = await invoke_glm(task.description, cwd=self.cwd)
        for ev in result.events:
            self.emit_tool_event(category="host_tool", tool_name=ev.name,
                                 phase=ev.phase, **ev.payload)
        self.emit_turn_completed(exit_code=result.exit_code)
        return TaskResult(files_changed=result.files_changed,
                          summary=result.summary, exit_code=result.exit_code)

    async def reply_to_prose(self, peer: str, body: str) -> str | None:
        result = await invoke_glm(_prose_prompt(peer, body), ephemeral=True)
        return result.last_message if result.exit_code == 0 else None

if __name__ == "__main__":
    import sys
    sys.exit(run(GlmTeammate))
```

Three primitives the SDK adds value through:

- `agent_card()` — self-knowledge. Subclass override; cheap roster flags derived automatically.
- `Teammate.emit_*` — visibility primitives. The kit fans out per the channel policy in `07-protocol-spec.md` §7.4.
- `Team.find_capability()` and `Team.peer_prompt_fragments_for(requester)` — cross-team discovery. Manifests load at team-formation time; queries are constant-time.

`peer_prompt_fragment()` is auto-generated from the Agent Card by default. The kit concatenates each capability's `description` + `when_to_use` + `when_not_to` and injects the result into peer system prompts at peer-spawn time. Override only when the harness needs richer prose than the manifest can express. (`08-elegance-and-gaps.md` §6.3.)

### 3.3 TypeScript sketch

```typescript
import { Teammate, Task, TaskResult, run } from "@agent-teams/sdk";

class DeepSeekR1Teammate extends Teammate {
  agentCard() {
    return {
      schema_version: 1,
      harness: "deepseek-cli",
      harness_version: detectDeepseekVersion(),
      transport: "headless-stream-json",
      capabilities: {
        turn_steer: {
          version: "1",
          schema: { type: "object", required: ["text"],
                    properties: { text: { type: "string" } } },
          description: "Inject text at next turn boundary.",
          when_to_use: "Between turns; live mid-turn is unsupported.",
          when_not_to: "Don't expect live delivery.",
          failure_modes: ["STEER_BUFFERED_NEXT_BOUNDARY"],
          delivery_mode: "next_turn",
          authorization: "any_peer",                 // peer-steer enabled
        },
        reasoning_trace: {                           // DeepSeek-R1 unique
          version: "1",
          schema: { type: "object",
                    properties: { include_chain: { type: "boolean", default: true } } },
          description: "Emit the model's reasoning chain alongside the answer.",
          when_to_use: "On hard debugging tasks; when a peer needs to see why DeepSeek reached a conclusion.",
          when_not_to: "Routine work; the trace doubles output volume.",
          failure_modes: ["TRACE_TRUNCATED", "TRACE_UNAVAILABLE_THIS_MODEL"],
        },
        host_tool_surface: {
          version: "1",
          schema: { type: "string", enum: ["deepseek-native"] },
          description: "DeepSeek uses its native shell/file tools.",
          when_to_use: "Read-only signal.",
          when_not_to: "Not callable.",
          failure_modes: [],
        },
      },
    };
  }

  async executeTask(task: Task): Promise<TaskResult> {
    this.emitTurnStarted(task.id, { model: "deepseek-r1" });
    const result = await invokeDeepseek(task.description, this.cwd);
    for (const ev of result.events) {
      this.emitToolEvent({ category: "host_tool", toolName: ev.name,
                            phase: ev.phase, ...ev.payload });
    }
    this.emitTurnCompleted({ exitCode: result.exitCode });
    return { filesChanged: result.filesChanged, summary: result.summary,
             exitCode: result.exitCode };
  }

  async replyToProse(peer: string, body: string): Promise<string | null> {
    const result = await invokeDeepseek(prosePrompt(peer, body), { ephemeral: true });
    return result.exitCode === 0 ? result.lastMessage : null;
  }
}

if (require.main === module) {
  process.exit(await run(DeepSeekR1Teammate));
}
```

The shape is identical because the protocol is identical. The SDK is what crystallizes the protocol into idiomatic per-language API. Go and shell sketches follow the same pattern; the latter wraps `agent-teams` as a CLI that takes Agent Card JSON as a flag, suitable for shell-only adapters that don't want to depend on a runtime.

### 3.4 What the SDK deliberately does NOT do

(From `08-elegance-and-gaps.md` §6.7, lifted verbatim because it is the §1 invariant in negative form.)

- **Wrap or unify backend tool surfaces.** The wrapper's narrow team-coordination tool surface stays narrow; harness-native tool surfaces flow through unmodified.
- **Translate model API requests.** We are not a router (§5).
- **Provide a unified prompt template.** Each backend's `prompts.py` is its own; only the kit-injected peer-prompt fragments are shared.
- **Manage the harness's session/turn loop.** App Server's polling, ACP's session/prompt, headless's stream-json parsing all stay in their respective `invoke.py` modules.
- **Define the host UI's rendering of capabilities.** The kit emits Agent Cards; the host UI decides how to display them. (For Claude Code: probably as decoration on the presence row. For Cursor: probably as side-panel metadata. For a web dashboard: probably as a filter facet. The protocol doesn't care.)

These exclusions are the SDK enforcing I3 (the protocol is a coordinator, not a kernel) at the API boundary.

---

## 4. Coordinator UX

The coordinator is whatever consumes the four observable channels (task state, presence/in-memory mirror, event log, inbox). Today the only coordinator is Claude Code's TUI. Twelve months out, the platform supports terminal coordinators (Claude Code, OpenCode, custom-shell), web coordinators (browser dashboards, CI replay tools), and IDE coordinators (Cursor, Zed, VS Code extensions). All consume the same protocol; none are special.

### 4.1 The four channels a coordinator renders

A coordinator's job is to present the team's state to a human. Four primary surfaces, each derived from one or more of the protocol's six channels:

- **Roster panel** — derived from channel 6 (capability discovery) + channel 1 (presence on idle). Shows each teammate's name, harness, capabilities, idle/busy state. Renders the Agent Card's cheap flags; lazily expands the rich manifest on hover/click.
- **Task panel** — derived from channel 2 (task state). Shows pending/in-progress/completed/blocked tasks, owner, activeForm. The lead's primary planning surface; hand-off via `task_update(owner=peer)` is one click.
- **Event feed** — derived from channel 5 (event log). Shows live tool events, artifact events, turn progress, errors. Filterable by teammate, kind, severity. The lead's primary observability surface — the §2 visibility-parity surface materialized.
- **Conversation panel** — derived from channel 1 (inbox messages of `kind: "message"` or `"broadcast"`). Shows DM threads. The lead's panel includes lead↔peer threads; each peer's panel includes the lead↔peer plus peer↔peer threads it participates in. **Peer↔peer is a first-class thread, not a sub-feature of "lead inspects teammate"** (I10).

Coordinator-private state (channel 3) is internal; it backs the renderer but is not a wire surface.

### 4.2 Three rendering modes

**Terminal.** Today's Claude Code TUI is the prototype. The platform's terminal renderer is a thin curses/ratatui-style program that reads the four channels via the `TeamStorage` interface and renders four panels. Multiplexing across teams is trivial; the terminal renderer is one reader, not a coupled coordinator.

**Web.** A browser dashboard reading the same four channels via SSE/WebSocket. Server side is a thin proxy over `TeamStorage`; client side is a single-page app with the same four panels. Useful for: lead-overview when running long teams overnight, replay of past sessions (event log is durable), audit trails for regulated environments.

**IDE.** VS Code, Cursor, Zed extensions present the same four panels as side-panel views. The IDE owns its own coordinator-private state (channel 3); the extension reads channels 1/2/5/6 from the platform's storage interface. The IDE's spawn affordance ("create teammate") writes the Agent Card and triggers the platform's spawn shim. The IDE doesn't have to invent a new protocol — it adopts the existing one.

The key claim: **all three modes consume the same data and emit no new wire shapes.** Adding a new coordinator is at most ~500 lines of UI code over the existing storage interface. The protocol is the moat; the coordinator is renderable.

### 4.3 Native-pace peer-to-peer in the coordinator

I11 (heterogeneity does not slow the team down) is the coordinator-side benchmark. A team of `claude-native + codex-runtime + gemini-acp + deepseek-r1` should coordinate at native-Claude pace from the lead's seat. The coordinator does this by:

- Pre-loading capability manifests at team formation (I13). When the lead opens a task-assignment dialog, the dropdown shows "deepseek-r1 (reasoning_trace, large_context)" already, no MCP round-trip.
- Routing peer↔peer DMs through the same channel 1 the lead↔peer DMs use (I10). Latency is one file write + one file read on the default storage backend — sub-100ms on local SSD.
- Surfacing typed event kinds (I12) so the coordinator's event feed can group, filter, and rate-limit without parsing prose. An idle ping is rendered as "(idle)" badge update; a substantive `tool_event` is rendered in the feed; both are routed by `kind`, not by reading their text.

The coordinator is what makes north star §3 visible. Without a coordinator that renders the peer↔peer thread as a first-class panel, peer efficiency is structurally present but socially invisible — leads will continue to think of the team as "lead drives N teammates" instead of "lead orchestrates a team of N peers who coordinate among themselves."

---

## 5. Why we are not a router

This section exists because the temptation to router is real, the precedent is widespread, and the structural argument against it is the moat.

### 5.1 What a router does

A router puts a translation layer between the lead and a model. The lead's harness keeps running; the model behind it changes per-request. Examples from `02-clones-survey.md` and `08-elegance-and-gaps.md` §A0:

- **HydraTeams (Pickle-Pixel)** — proxies `ANTHROPIC_BASE_URL` to a non-Claude model server that produces tool calls in Anthropic Messages API shape. Claude Code's harness executes them. Inherits TUI visibility for free.
- **claude-code-router** — 33k stars. Routes outbound `/v1/messages` through provider/router/transformer layers. Same shape as HydraTeams: keeps Claude Code as runtime, rewrites model traffic.
- **Generic OpenAI-compatible bridges** (gemini-cli-openai, open-gemini-cli forks) — wrap one CLI's reasoning loop to expose `/v1/chat/completions`. Same shape; router pattern at endpoint level.
- **LangGraph / CrewAI / AutoGen / OpenAI Swarm as substrate** — frameworks where "agents" are graph nodes / framework objects, not OS processes. Routing happens inside one runtime.

The router approach is *real* and *productive* for some use cases (model variety inside one harness, cheap A/B at the model layer). It is not what we are.

### 5.2 What a router cannot do

A router cannot preserve harness capabilities, by construction:

- **Codex App Server speaks JSON-RPC, not Anthropic Messages.** There is no way to carry `turn/steer` mid-turn injection or `thread/fork` cross-task memory through an Anthropic-shaped proxy. The Codex CLI's polling event loop has no analog in the Anthropic API surface.
- **Gemini ACP's permission bridge has no Anthropic API equivalent.** The `session/request_permission` round-trip routes through the host editor; flattening to Messages-API loses the bridge.
- **Kimi's internal swarm runs inside Kimi's own process model.** Routing the model traffic does not route the swarm; the swarm exists only inside Kimi's runtime.
- **Native Claude tool events** (the rich live stream the lead currently sees from native Claude teammates) are emitted by the Claude Code harness's own pane process. A router replacing the *model* behind that harness still sees the same events; a router replacing the *harness* loses them.

The router's virtue (model variety) is its constraint (one harness loop). Routers that try to add the capability layer end up rebuilding their session loop around external process orchestration — at which point they are no longer routers, they are the platform.

### 5.3 What routers DO have

Routers are not bad designs. They have one strength the platform must be deliberate about matching: **peer efficiency within their session loop is excellent.** When two "agents" are graph nodes in one process, peer-to-peer is a function call. Latency is microseconds.

Heterogeneous teams have to match this without paying the in-process cost. The platform's strategy: I13 (pre-load capability manifests at team formation) + I11 (native-pace peer-to-peer benchmark). On the default filesystem storage backend, peer↔peer round trips are file write + file read, sub-100ms on local SSD. On a future Redis storage backend, they are sub-10ms. The protocol does not require slow peer-to-peer; the substrate determines the latency, and the substrate is pluggable.

The router shortcut is "skip the capability layer, get cheap peer efficiency, lose all harness specificity." The platform is "carry both layers, pay the engineering cost, win the moat *and* match peer efficiency." The harder problem is the bigger product.

### 5.4 The structural argument

The moat is durable because to match it a router has to throw out its session loop and rebuild around external process orchestration. They won't, because that breaks their entire model. (`CLAUDE.md` §1; `docs/architecture.md` §"the core principle"; `08-elegance-and-gaps.md` §A0.)

The platform's competitive position is not "more models" or "easier integration" — those are router pitches. The platform's competitive position is **"every harness's full power, in a team."** That is the only durable differentiator in this space, and it is invisible to anyone evaluating purely on model coverage.

---

## 6. Migration story: claude-anyteam → platform

The platform is not a fork. It is the act of generalizing what we already do. Forward-compat throughout; nothing the user has today breaks.

### 6.1 Phase 0 (now) — what we have

- File-based substrate at `~/.claude/teams/<team>/`. Mailbox JSON, atomic task claims, capability declarations as roster flags (proposed v2). Three backends shipping: Codex (App Server + exec), Gemini (ACP + headless), Kimi (headless).
- Spawn shim binding to Claude Code's `CLAUDE_CODE_TEAMMATE_COMMAND`. Adapter self-registration. Wrapper MCP with narrowed coordination tools + shadow host tools.
- Three north stars in CLAUDE.md. Two-layer architecture in docs/architecture.md. opus-synthesis 07-protocol-spec.md and opus-elegance 08-elegance-and-gaps.md as the canonical spec and SDK design.

### 6.2 Phase 1 (v0.7.x — opus-roadmap territory) — close visibility-parity gap

opus-roadmap (09) owns this. Briefly, per `08-elegance-and-gaps.md` §8 "to opus-roadmap":

- L17 / L17-bis / L17-ter — append-only event log (`events/<agent>.jsonl`), wrapper tool events, headless turn-progress envelopes. Visibility envelope in production for all three backends.
- All CD entries (CD-1..6) — capability declarations land as Agent Card v1 in registration.
- L9, L21, L26, L27, L33 — foundation-crack work: parallel config systems unified, Settings base class, schema versioning, vendored-writer shared lock, filelock staleness.

This is the v0.7.x pragmatic plan. opus-vision's job is to ensure each of these lands in a way that is **forward-compat to platform**: the Agent Card schema is the platform's Agent Card; the event-log file path can become a `TeamStorage` channel without renaming; the Settings base class becomes the platform's `TeammateSettings`. No fork; the v0.7.x landings are platform v0 by another name.

### 6.3 Phase 2 (months 3–6) — extract the SDK

Per the strategic-roadmap Phase 2 ("stop being a Claude Code plugin"):

1. **Extract `agent_teams_sdk_python` from `claude_anyteam`.** Move `protocol_io`, `messages`, `registration`, `loop` (the protocol-side parts), and the new visibility-event primitives into a standalone package. claude-anyteam becomes the first SDK consumer.
2. **Define `TeamStorage` interface formally.** Filesystem default ships; the interface is documented and tested. Reference Redis and SQLite implementations land for early adopters.
3. **Specification document.** The protocol gets a public spec at `agent-teams/spec/v1`. Versioned, CC-BY-licensed. Independent reimplementations are now possible. opus-synthesis's 07-protocol-spec.md is the reference draft.
4. **Reference TypeScript port.** A second-language SDK proves the spec is implementable beyond Python and accelerates editor-side adoption (Cursor, VS Code extensions).
5. **Rename.** `claude-anyteam` becomes `claude-anyteam` — the Claude Code binding of `agent-teams`. The umbrella project gets a vendor-neutral name. Every downstream package retains backward-compat imports for one major version.

### 6.4 Phase 3 (months 6–12) — non-Claude host bindings

Per strategic-roadmap Phase 3 ("ship a non-Claude host binding"):

1. **OpenCode binding.** OpenCode already supports a `backendType="opencode"` value in cs50victor's MCP impl, suggesting OpenCode runs its own multi-agent system. The binding's job: implement a `TeamStorage` over OpenCode's internal state + spawn-shim equivalent + Agent Card registration. The SDK's `Teammate` base does the rest.
2. **Cursor binding.** Cursor's coordinator-private state is a proprietary tree; the binding implements `TeamStorage` over it. Cursor's spawn affordance writes Agent Cards; Cursor's UI reads channel 5 (event log) for the new "team observability" panel.
3. **Sourcegraph / IDE-internal coordinators.** Same pattern; the SDK is host-agnostic by construction.

**The moment a non-Claude host runs a `codex-*` teammate via the platform SDK and sees the same visibility envelope a Claude lead sees, the platform is real.** This is the single most important strategic milestone in the entire vision.

### 6.5 Phase 4 (year 2+) — ecosystem maturity

Once the platform is multi-host and multi-language, the value compounds:

- **Cross-host teams.** A team led by a Claude Code lead with peers spawned from a Cursor session and a CI process. The protocol carries identity and capability declarations across hosts; the storage backend is shared (Redis, S3, or a hosted control plane).
- **Adapter marketplace.** GLM, DeepSeek, Qwen, OpenHands, Continue, Aider, GooGoose adapters are each ~200 LOC against the SDK. Community contributions are the natural growth path.
- **Replay tooling.** The append-only event log is durable. Bug reports include a replay file; CI runs replay regression suites against new SDK versions; teams audit historical sessions.
- **Hosted control plane.** Optional (open-source binary remains fully functional). Provides federated identity, unified billing across providers, shared session memory across teams. Per strategic-roadmap, this is the revenue surface.

### 6.6 Forward-compat invariants

The migration MUST preserve:

- **Existing `~/.claude/teams/<team>/` layout.** v0 sessions keep working without migration. The platform's storage interface defaults to this layout; new fields are additive.
- **Existing wire shapes.** v1 envelopes are a strict superset of v0 with `schema_version: 1` added. v0 consumers (the host binary as it exists today, native Claude teammates, current adapters) keep parsing v1 messages because Pydantic `extra="ignore"` handles the new fields.
- **Existing spawn contract.** `CLAUDE_CODE_TEAMMATE_COMMAND` continues to work. The platform extends to Cursor/OpenCode equivalents *additively*; Claude Code users see no change.
- **Existing wrapper MCP tool surface.** `send_message` / `task_update` / `task_create` / `read_inbox` / `task_list` / `read_config` continue to work. The 14th tool `mcp_anyteam_capability_manifest` is additive.

What the migration explicitly does NOT preserve: the `codex-anyteam` framing where Codex was special. The platform treats every harness equally; there is no "first backend" in the API.

---

## 7. What we keep from Claude Code

Claude Code did one decisive architectural thing right that the entire platform is built on: **it made the team-protocol file-based**. That choice is what lets the protocol be host-agnostic at all. Without it we would be building atop one vendor's runtime and the moat would not exist.

The specific Claude Code design decisions worth preserving as platform invariants:

**The file-as-substrate.** `~/.claude/teams/<team>/{config.json, inboxes/, tasks/, events/}` is OS-native, language-neutral, trivially observable. Anyone with `cat`+`jq` can debug a team. The web doesn't need a web protocol because everyone speaks HTTP; agents don't need a vendor protocol because everyone speaks the filesystem. (`docs/architecture.md` §"the core insight"; `07-protocol-spec.md` §0 item 2.)

**Atomic primitives.** Temp+rename for config, lock-protected JSON-array rewrite for inbox and task files, monotonic task ids. These are the right primitives for multi-process coordination on a shared filesystem, and they are why the substrate is reliable at >99.9% even under load. (`07-protocol-spec.md` §1.2, §1.3, §1.4.)

**The narrow wrapper MCP surface.** Six team-coordination tools plus seven shadow host tools. Deliberately not a feature superset of any backend's own tool surface. The wrapper is the *coordination* surface; the harness owns *execution*. (`08-elegance-and-gaps.md` E10.)

**The two-piece registration pattern.** Leader-spawned mirror task + adapter self-registration both required. Acknowledges the host-state/file-state split explicitly instead of pretending one is the other. (`07-protocol-spec.md` §3.5 "the hybrid.")

**`CLAUDE_CODE_TEAMMATE_COMMAND` as the spawn hook.** Claude Code's official escape hatch. We use it; we do not invent a synthetic registration that the host doesn't bless. (`docs/internal/spawn-research-findings.md` §synthesis; `08-elegance-and-gaps.md` §A6.)

**No-LLM-wrapper invariant.** The external CLI is the teammate. One layer of reasoning, not two. This is the floor that makes the moat structurally possible. (`docs/architecture.md` §"why no LLM wrapper"; `08-elegance-and-gaps.md` §A1.)

These are the parts of Claude Code that survive the platform extraction unchanged. They are not Anthropic-specific; they are the right design for the problem.

---

## 8. What we change or extend

The platform extends what Claude Code shipped without disturbing the parts above. Extensions, not rewrites:

**Visibility envelope.** Today the lead sees task state and inbox messages; the rich event stream from each backend stays inside the adapter. The platform adds the append-only event log as channel 5, with versioned envelopes, channel fan-out policy, and the event kinds from `07-protocol-spec.md` §7.3. Visibility parity becomes structural rather than aspirational. (B9 §6 is the seed; opus-roadmap 09 is the v0.7.x landing path; the platform makes it part of the protocol.)

**Capability declarations.** Today the protocol carries identity and lifecycle; the rich heterogeneity that the moat depends on is invisible at the protocol layer. The platform adds Agent Cards as channel 6 — cheap roster flags + rich on-demand manifests. Without this, peer efficiency degrades to "ask the LLM if it can do X" round-trips. With it, peer-to-peer coordination is constant-time discovery + one capability invocation. (`07-protocol-spec.md` §1.7, §5.6; `08-elegance-and-gaps.md` §4 CD-1..6.)

**Schema versioning.** Today no on-disk schema version exists; only `permission_request`/`permission_response` carry `schema_version: 1`. The platform makes versioning mandatory on every envelope from day one. We never have an unversioned-payload moment again. (`08-elegance-and-gaps.md` L26.)

**Plan mode as a first-class capability.** Today plan mode is a per-teammate flag; plan-mode teammates emit `plan_approval_request` / `plan_approval_response`. The platform treats it as one capability among many; backends that support it declare `plan_mode` with a manifest, and peers can route plan-required work to plan-capable teammates explicitly. (`07-protocol-spec.md` §6.3.)

**Host-tool surface declaration.** Today the lead sees `task_complete` summaries but cannot tell whether a backend's host-tool calls came from its native surface (Codex `commandExecution`) or the wrapper's shadow surface (`mcp_anyteam_shell`). The platform adds `host_tool_surface` capability so peers know what to expect in event streams and how to phrase work requests. (`08-elegance-and-gaps.md` §4 CD-4.)

**Peer-to-peer steer authorization.** Today steer authorization is implicit per backend (lead-only on Gemini/Kimi, unenforced on Codex). The platform makes it a declared capability (`accepts_peer_steer: bool` + `turn_steer.authorization`); the §3 leak observed during the proto-rev research session becomes structurally addressable. (`07-protocol-spec.md` §10.11.)

**Storage abstraction.** Today the substrate is the filesystem at `~/.claude/teams/`. The platform makes storage pluggable behind `TeamStorage`. Filesystem default ships; Redis (multi-host), SQLite (single-host high-throughput), in-memory (tests), and host-private storage (Cursor's IPC, OpenCode's WebSocket) all implement the same interface. (`08-elegance-and-gaps.md` §6.2.)

**Coordinator-host abstraction.** Today the coordinator is Claude Code's TUI. The platform makes it pluggable: terminal, web, IDE coordinators each consume the same six channels. The host-specific bindings (Claude Code's `CLAUDE_CODE_TEAMMATE_COMMAND`, Cursor's spawn affordance, OpenCode's coordinator process) are thin shims over a host-agnostic SDK. (§4 of this doc.)

The pattern across all extensions: **add capability layers on top of stable transport.** No rewrites of what works; no new surfaces that compete with the file substrate. Every extension is one of (a) a new typed envelope kind, (b) a new capability declaration, or (c) a new pluggable interface. The protocol grows additively; backwards compatibility is preserved by I14 (mandatory schema versioning) at every step.

---

## 9. Worked example: implementing DeepSeek-R1

The clearest measure of the platform's value is how much code it takes to ship a new harness as a first-class team member. This worked example contrasts the cost under today's adapter against the cost under the SDK.

### 9.1 DeepSeek-R1's distinctive capabilities

DeepSeek-R1 is a useful test case because it has features none of our current backends have:

- **Reasoning trace.** R1 emits a chain-of-thought trace alongside the final answer. Useful for hard debugging tasks where peers want to see why DeepSeek reached a conclusion.
- **Large context.** Multi-hundred-K context window suitable for whole-codebase review.
- **Fast iteration on tool calls.** R1's tool-use loop is faster per turn than reasoning models.

A router cannot expose these as capabilities — they are inseparable from the harness's reasoning loop. Under the platform, they are first-class declared capabilities.

### 9.2 Today (without SDK) — measured 3,456 LOC

In the current `claude-anyteam` checkout, the flat sum of Codex-related backend files (loop, codex.py, app_server.py, jsonrpc_stdio, config, prompts, cli, plus shared infrastructure not extracted into a kit) is **3,456 LOC**, measured by `codex-prototype` against this repo on 2026-04-27. A new DeepSeek-R1 backend would replicate the same shape — config, loop, invoke, prompts, cli, tests — duplicating roughly 800-1200 of those lines per author judgment, with the rest staying shared.

Critically: that backend would *flatten DeepSeek-R1's reasoning trace into the same `task_complete` summary every other backend produces*. The unique capability is invisible to peers.

### 9.3 Under the SDK — measured 130 LOC adapter + 992 LOC reusable kit

The SDK prototype at `references/external-claude-code-re/prototype-sdk/` lands the real numbers. Author writes one adapter file:

- **deepseek_adapter.py** — **130 LOC**. Agent Card declaring `reasoning_trace`, `large_context`, `accepts_peer_steer: true`, plus `execute_task()` wrapping the CLI subprocess and emitting `tool_event` / `turn_completed` envelopes, plus `reply_to_prose()`. (`references/external-claude-code-re/prototype-sdk/examples/deepseek_adapter.py`.)

The kit it inherits from:

- **agent_teams_kit/ (reusable, lives once)** — **992 LOC** total across `teammate.py` (15 KB, base class with polling loop / idle / claim CAS / prose dispatch / shutdown lifecycle / event-log fan-out), `storage.py` (10 KB, `TeamStorage` Protocol with FilesystemStorage default), `team.py`, `capabilities.py`, `events.py`, `messages.py`, `lifecycle.py`, `runner.py`. Verified by 12 passing pytest tests.

Comparable adapters in the same prototype:

- **echo_adapter.py** — 48 LOC (minimal — proves the transport layer alone is sufficient).
- **glm_adapter.py** — 139 LOC (full feature declaration).

**The productivity argument is structural, not asymptotic.** Per-adapter cost drops from ~1000 lines (today) to ~130 lines (under SDK) — about 7-8x. The 992-line kit lives once across all adapters, so the *total team cost* (kit + N adapters) drops 60%+ once you have three adapters and continues to favor the platform as N grows.

The unique capability — DeepSeek's `reasoning_trace`, GLM's `code_interpreter_native`, Kimi's `native_swarm` — is **structurally legible to peers** via the manifest, not flattened away. That is the qualitative point §1 (harness preservation) makes operational.

### 9.4 What changes in the team's behavior

The peer-efficiency benefit is the part that matters. With DeepSeek under the SDK:

1. **Team formation.** `glm-eve`, `codex-runtime`, `gemini-bob`, `deepseek-r1`, and `claude-lead` form a team. Each declares its Agent Card. The lead's roster panel shows: "deepseek-r1: reasoning_trace, large_context, accepts_peer_steer." The kit auto-injects per-teammate peer-prompt fragments into every other teammate's system prompt.

2. **Mid-task coordination.** `codex-runtime` is debugging a tricky concurrency issue. It has a peer-prompt fragment in its context that says: "When you observe deepseek-r1 in the team, you can request a `reasoning_trace` from them on hard subproblems. Use this when you've narrowed the bug and need to see why a particular code path matters."

3. **Peer-steer.** `codex-runtime` sends `{kind: "peer_steer_request", to: "deepseek-r1", capability: "reasoning_trace", input: {context: "..."}}`. Because deepseek-r1 declared `accepts_peer_steer: true`, the request is routed without lead intervention.

4. **DeepSeek responds in a turn.** Emits `{kind: "peer_steer_response", trace: "..."}` plus full `tool_event` / `artifact_event` envelopes per the visibility spec.

5. **codex-runtime consumes the trace.** Continues its turn; emits `tool_event` envelopes that include the cross-teammate steer in its own trace.

6. **The lead sees both via the visibility envelope.** Channel 5 (event log) carries both teammates' streams; channel 1 (inbox) carries the typed peer-steer request and response. The coordinator UI's event feed shows the cross-peer interaction as a first-class event, not as opaque file activity.

Throughout: no parsed prose to route, no manifest fetch round-trip (manifests pre-loaded at team formation per I13), no canned-fallback noise (per the `delivered_via_tool` guard the SDK surfaces). The team coordinates at native pace; the heterogeneity is preserved; the lead has full visibility.

That is the moment the platform claim — "heterogeneous teams that work as fast as a pure-Claude team while preserving every harness's strength" — becomes a benchmark. <100ms peer-to-peer round trip on the default storage backend, <10ms on a Redis backend, with full visibility parity and structurally legible capability declarations.

---

## 10. What "good" looks like 12 months out

A concrete picture of the platform mid-2027.

### 10.1 Adoption

- **Eight or more shipping adapters.** Codex (App Server + exec), Gemini (ACP + headless), Kimi, GLM, DeepSeek, Qwen, OpenHands, Continue. Each ~200–300 LOC against the SDK plus ~100–200 LOC of harness-specific invoke logic. Community-contributed adapters land monthly.
- **Two non-Claude host bindings.** OpenCode and one other (Cursor, Sourcegraph, or a new IDE-internal coordinator). The cross-host story is real.
- **A reference TypeScript SDK in addition to Python.** Editor extensions can integrate without bridging through Python.
- **A spec at `agent-teams/spec/v1.0`.** Versioned, CC-BY-licensed, with a reference test suite. Independent reimplementations exist.

### 10.2 Capabilities every shipping adapter declares

Mature Agent Cards for each backend, including:

- All §2.6 cheap roster flags (`turn_steer`, `thread_fork`, `permission_bridge`, `swarm`, `host_tool_visibility`, `session_persistence`, `cancellation`, `accepts_peer_steer`, `soft_non_progress_watchdog`, `approval_policy`, `host_tool_surface`).
- Rich manifests for every declared capability with schema, when_to_use, when_not_to, failure_modes.
- Auto-generated peer-prompt fragments injected into peer system prompts at team formation.

The lead sees a roster that is *legible*: "this team has two teammates with `permission_bridge`, three with live `turn_steer`, one with `thread_fork`, one with `large_context`." Routing decisions are explicit; capability gaps are visible.

### 10.3 Peer-efficiency benchmark

Concrete and testable: **any pair of teammates can DM, steer, hand off tasks, and discover each other's capabilities at <100ms peer-to-peer round trip on the default filesystem storage backend** (sub-10ms on a Redis backend). The benchmark is a CI test in the SDK repo; regressions block releases.

### 10.4 Visibility-parity benchmark

Concrete and testable: **every routed teammate emits the same envelope kinds the lead would see from a native Claude teammate.** A `commandExecution` from a Codex teammate produces a `tool_event` with `category=host_tool, tool_name=commandExecution`. A `Bash` from a native Claude teammate produces a `tool_event` with `category=host_tool, tool_name=Bash`. Both surface in the lead's event feed with identical structure; only `tool_name` differs (per I8). The benchmark is a runtime acceptance test against each backend.

### 10.5 Coordinator multiplicity

- **Claude Code TUI** (today's prototype, evolved to consume channel 5 directly).
- **A web dashboard** for replay, audit, and overnight long-running team monitoring.
- **At least one IDE extension** (Cursor or VS Code) rendering the four panels as side-panel views.

All three render the same data; none is special.

### 10.6 Strategic position

Per `docs/internal/strategic-roadmap.md`:

- The platform is the recognized open standard for multi-vendor agent collaboration.
- Two non-Claude host maintainers actively engage with the spec.
- One conference talk accepted; one architectural blog post in the developer-tools commentary cycle.
- The hosted control plane (federated identity, unified billing, audit trails) is in public beta as the revenue surface; the open-source binary remains fully functional without it.
- Quarterly state-of-the-ecosystem reports document which adapters work with which hosts and what changed.

The moat is unambiguous: every shipping adapter declares unique capabilities; the protocol carries them; the coordinator surfaces them. Routers can match on model variety and never on capability fidelity. The differentiator is structural and repeatedly demonstrable.

### 10.7 What "good" deliberately does not include

- A unified prompt template across backends. Each harness's prompts.py is its own (per `08-elegance-and-gaps.md` §6.7).
- A unified tool surface across backends. The wrapper's narrow team-coordination tools stay narrow; harness-native tool surfaces flow through unmodified.
- Routing of model API requests. We are not a router, ever (§5).
- A single coordinator UI. The protocol is host-agnostic by construction; insisting on one coordinator would re-couple to one runtime.

These exclusions are not omissions — they are I3 (the protocol is a coordinator, not a kernel) at the strategic level. The platform's value is in what it deliberately leaves to harnesses and coordinators, not in what it claims to do for them.

---

## 11. Closing

The platform is small. The substrate exists today. The protocol is a thin envelope, six channels, sixteen invariants. The SDK is ~800 lines once and ~200 lines per harness adapter. The migration is forward-compat throughout; nothing the user has today breaks.

What makes it durable is the same thing that makes it small: it does one thing well — *carry capability declarations and route activity across heterogeneous harnesses* — and refuses to do anything that would make routers cheaper to match. We are not a router because routers cannot preserve harness capabilities. We are not a framework because frameworks couple agent identity to one runtime. We are not a host because hosts couple coordinator UI to one editor.

We are the protocol that lets every harness be a first-class team member, that lets every coordinator render the same observability, and that lets every agent author ship a new harness in ~200 lines. That is the platform.

The three north stars (`CLAUDE.md` §1, §2, §3) are how we know we are still on the right track:

- §1 — **Does this preserve every harness's unique capabilities, or does it flatten them?** Flatten = no.
- §2 — **Does this narrow the visibility gap or widen it?** Widen = no.
- §3 — **Does this change peer-to-peer coordination fidelity?** Match native Claude's pace.

Twelve months out, every question in the roadmap reduces to one of these three. If we keep the discipline, the platform is real.

---

*End of 10. ~830 lines.*
