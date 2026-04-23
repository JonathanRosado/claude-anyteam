# v7 Architecture — Codex as Full MCP Client

**Status:** current target (supersedes v6 for deployment; v6 remains as proven feasibility baseline).
**Relationship to v6:** additive, not a full redesign. v6 proved end-to-end feasibility; v7 closes the one real capability gap reviewer and lead-analysis identified at the end of v6.

---

## 1. What's changing from v6

**v6 recap:** Python adapter imports cs50victor as a library. The adapter's Python control loop polls inbox, claims tasks, invokes Codex via `codex exec --json --output-schema`, extracts structured output, marks task complete, handles shutdown. **Codex itself has no visibility into the team protocol** — it receives a prompt, produces code, emits a structured response, exits.

**v7 change:** Codex is given **MCP tool access to cs50victor's protocol operations** (`send_message`, `task_update`, `task_create`, `read_inbox`, `task_list`, `read_config`) during task execution. This lets Codex's own reasoning drive protocol decisions mid-task — send a progress update, ask a teammate a clarifying question, create a subtask if the work should be split, update `activeForm` as it progresses.

**What stays from v6:**
- Adapter is still Python.
- Self-registration via direct config write.
- Adapter still owns process lifecycle, polling, claim, shutdown response.
- `planModeRequired: false` default policy.
- Task-complete schema contract.
- All v6 test coverage (33 green).

---

## 2. Why this is the version worth shipping

The v6 proof established that a non-Claude-LLM Codex teammate is feasible. The v7 change establishes that a non-Claude-LLM Codex teammate is **first-class** — indistinguishable from a native Claude teammate in what it can *decide to do* during its own reasoning.

Concretely: a native Claude teammate reasons about code *and* protocol in a single LLM pass ("I'm stuck, let me ask X" happens naturally). v6 split that into two processes: Codex reasons about code only, adapter's hand-written Python reasons about protocol. v7 unifies them back — without reintroducing a Claude LLM — by letting Codex's own reasoning reach the protocol via MCP tool calls.

The feasibility question is answered; v7 is the version you'd actually run in a team where you want a Codex teammate that behaves like a peer.

---

## 3. Component topology

```
+------------------------------------------------------------+
| Codex teammate adapter (Python process)                     |
|                                                            |
|   +------------------------+   +----------------------+    |
|   | Adapter control logic  |-->| cs50victor (library) |    |
|   | - lifecycle            |   +----------------------+    |
|   | - registration         |                               |
|   | - polling / claim      |                               |
|   | - Codex session mgmt   |                               |
|   +------------------------+                               |
|            |                                                |
|            | spawns as subprocess                           |
|            v                                                |
|   +------------------------+                                |
|   | Codex CLI session      |                                |
|   | (long-lived or per-    |                                |
|   |  task; see §4)         |                                |
|   +------------------------+                                |
|            |                                                |
|            | MCP (stdio)                                    |
|            v                                                |
|   +------------------------+                                |
|   | cs50victor MCP server  |  (same codebase as library,   |
|   | subprocess (stdio)     |   different entry point)       |
|   +------------------------+                                |
|            |                                                |
|            v                                                |
+------------------------------------------------------------+
           ~/.claude/teams/{team}/*   (file-based protocol)
```

Both the adapter and Codex reach cs50victor. The adapter imports it as a library (as in v6); Codex talks to it over MCP. They share a single source of truth (the file-based protocol).

---

## 4. Invocation surface: `codex exec` vs App Server

Two options for how Codex is invoked in v7:

**Option X: `codex exec` with inline MCP-server config and sandbox bypass (chosen for v7.0).**
Invoke `codex exec --dangerously-bypass-approvals-and-sandbox --json --output-schema <task-complete.schema> -c 'mcp_servers.claude_anyteam_wrapper.command="..."' -c 'mcp_servers.claude_anyteam_wrapper.args=[]' <prompt>`. One Codex process per task. Codex reads its MCP server list from the `-c` overrides, spawns the configured server as a stdio subprocess of itself, and can call the advertised tools during that invocation.

The `-c key=value` override is Codex's generic TOML-patch mechanism (equivalent to editing `~/.codex/config.toml` just for this one process). There is no `--mcp-server NAME` flag on `codex exec`; the persistent version (`codex mcp add`) was explicitly rejected because it mutates user config and has a long tail of cleanup-failure scenarios (adapter crashes mid-task → dangling entry in `~/.codex/config.toml`). Inline `-c` keeps the MCP wiring ephemeral and adapter-scoped, matching the v6 invariant that user config is never touched.

**On `--dangerously-bypass-approvals-and-sandbox`:** empirical M5 finding — when the wrapper MCP server runs as a Codex subprocess (per stdio transport), it inherits Codex's sandbox, which blocked its writes to `~/.claude/tasks/` and `~/.claude/teams/{team}/inboxes/` under `--sandbox workspace-write`. Rather than expand sandbox permissions with a `~/.claude/` carve-out (path 1, viable but fragile), v7 disables the sandbox entirely. This matches the operator's existing pattern on `JonathanRosado/codex-jr-plugin-cc`. The sandbox exists to protect users from untrusted agents; this adapter operates in an environment where the user *is* the operator running Codex as their teammate — sandbox protection adds friction without adding security in that context. External or multi-tenant deployments where Codex is acting on behalf of an untrusted third party would require a different posture.

**Option X's MCP server is *not* cs50victor directly** — see §4a below.

**Option Y: App Server (reserved for v7.1+).**
Run Codex as a persistent JSON-RPC session; the adapter orchestrates threads and turns. Codex stays alive across tasks. Enables streaming events, mid-task interjection by the adapter, approvals. Richer, but significantly more adapter code.

**v7.0 uses Option X.** v7.1 layers in App Server (Option Y) as an opt-in via `--app-server`, without changing the protocol-tool-access design — see `docs/v7.1-notes.md`. **Starting with task #21 (the v7.1 default flip), Option Y is the default**: the adapter runs under App Server unless the user passes `--no-app-server` or sets `CLAUDE_ANYTEAM_APP_SERVER=false`. Mid-task reactivity via `turn/steer` is the v7.1 signature capability; making it opt-out rather than opt-in means users don't silently lose it by not knowing a flag exists. v7.2 (`codex exec resume` cross-task session memory, documented in `docs/v7.2-notes.md`) lives on the fresh-exec path (Option X) and is accessed via `--no-app-server` — the two modes are orthogonal feature profiles.

## 4a. Why a narrowed wrapper, not cs50victor directly

cs50victor exposes 13 MCP tools, six of which are destructive lifecycle operations that a running teammate should not be able to invoke: `team_create`, `team_delete`, `spawn_teammate`, `force_kill_teammate`, `process_shutdown_approved`, `check_teammate`. A hallucinated tool call from Codex to any of them would have outsized consequences (delete a team, kill a peer, fabricate a spawn, etc.).

Rather than rely on prompt discipline to keep Codex away from these tools, v7 ships a **narrowed wrapper MCP server** (`claude_anyteam.wrapper_server`) that exposes only the safe subset Codex actually needs mid-task:

| Tool | Purpose |
|---|---|
| `send_message(to, body, summary?)` | Send a status update or clarifying question to a teammate. Sender is pre-filled from the adapter's identity env, so Codex can't accidentally send as the wrong teammate. |
| `task_update(task_id, active_form?, status?)` | Update your own in-flight task. **No `owner` parameter** — reassignment is the lead's job. |
| `task_create(subject, description)` | Create a new task if work should be split off. Starts unowned/pending for lead assignment. |
| `read_inbox(unread_only?)` | Read your *own* inbox (enforced against other teammates' inboxes). |
| `task_list()` | Read-only inspection. |
| `read_config()` | Read-only inspection; `prompt` fields stripped. |

Everything else cs50victor exposes is filtered out. The wrapper uses cs50victor as a Python library internally, so file I/O, locking, and schema handling are unchanged — only the tool surface Codex sees is narrowed, with descriptions tuned for the team-protocol context.

Lifecycle: the wrapper is a stdio subprocess spawned *by Codex* on each `codex exec` invocation, via the `-c mcp_servers.claude_anyteam_wrapper.command=...` override. Its lifetime matches the Codex subprocess — fresh per task, clean isolation, no long-lived MCP server to orphan.

Identity (team name + our agent name) is passed to the wrapper via env vars (`CLAUDE_ANYTEAM_TEAM`, `CLAUDE_ANYTEAM_NAME`) set on the `codex exec` subprocess by our adapter. The wrapper reads those at `build_server()` time and pre-fills them into every tool call so Codex can't accidentally send as the wrong teammate.

A contract test (`tests/test_wrapper_contract.py`) enforces at test time that the exposed tool set is exactly the six above, the blocked tool set is exactly the six destructive operations, and every cs50victor tool is categorised one way or the other. Accidentally re-exporting a destructive tool fails the build.

---

## 5. Codex system prompt update

Codex needs to be told about its tools and role. Draft prompt framing (implementer owns final wording):

> You are **{name}**, a Codex teammate on the **{team}** team. Your job is to execute the task described below.
>
> The following MCP tools are available and call into your team's protocol:
> - `send_message(to, body)` — send a message to another teammate. Use for progress updates, clarifying questions, or handoffs.
> - `task_update(task_id, activeForm?, status?, owner?)` — update your own task's state. Use `activeForm` to communicate progress.
> - `task_create(subject, description)` — create a new task. Use if you discover work that should be split off.
> - `read_inbox()`, `task_list()`, `read_config()` — read-only inspection of team state.
>
> Do the task, and along the way use the tools when it would be useful to your teammates. Your final output must conform to the task-complete schema.

The system prompt is a first-class artifact in v7 — reviewer will critique it at v7 validation.

---

## 6. Behavioral changes observable to lead

In v6, a Codex teammate was silent during task execution: inbox empty until the final `task_complete` lands. In v7, a Codex teammate may:
- Send progress messages ("starting sieve implementation", "tests passing, about to refactor").
- Call `task_update` to advance `activeForm` mid-task ("generating implementation" → "writing tests" → "verifying output").
- Create a subtask if it decides the work should split.
- Ask a clarifying question to another teammate and wait for reply.

The adapter's structured log will show each MCP call Codex makes. This is the evidence v7 is actually using the new capability, not just having access to it.

---

## 7. Risks and limitations

| Risk | Severity | Mitigation |
|---|---|---|
| Codex ignores the MCP tools entirely (does the task, emits final output, never sends anything mid-task) | Medium | v7 validation must include a task that explicitly requires mid-task communication (e.g., "send a progress update when you're halfway done"). If Codex routinely ignores tools, the system prompt needs more force. |
| Codex over-uses MCP tools (spams messages, creates unnecessary subtasks) | Low | Prompt engineering: "use tools when useful, not by default." Monitor during v7 validation. |
| cs50victor subprocess lifecycle bugs (server dies, adapter can't reach) | Low-Medium | Adapter spawns and monitors the MCP server subprocess; on exit propagate failure to Codex invocation. |
| Codex CLI's MCP client implementation has bugs or missing flags | Low | Feature-test at adapter startup: start MCP server, verify Codex can list the tools, fail closed if not. |
| Regression in v6 behaviors (shutdown, self-registration, etc.) | Low | All 33 existing tests must continue to pass. |

---

## 8. Milestones

- **M4: MCP server wiring.** Spawn cs50victor as an MCP server subprocess. Confirm Codex can list its tools. Adapter feature-tests this at startup.
- **M5: Codex calls a protocol tool in a real run.** Assign a task that makes tool use natural (e.g., "write X, and send `team-lead` a status update when tests pass"). Verify in adapter logs that the MCP tool call round-tripped.
- **M6: Equivalence with v6 + tool-access evidence.** Re-run the three v6 task scenarios (primes, csv2md, wordfreq) with v7 and confirm no regressions; plus show at least one task where Codex used a tool mid-run.

After M6: reviewer runs v7 validation (task #12, analogous to task #5 for v6). Final sign-off at a new task #13 analogous to task #6.

---

## 9. What this doesn't change

- The thesis-refutation: v6 already refuted "only LLM wrapper works." v7 strengthens it by showing the refutation extends to fully-native teammate behaviors, but doesn't re-open the question.
- The spec: `docs/protocol-spec.md` v1.1 is accurate for v7 too. No spec changes.
- The prior-art doc: still current. v7 is just a more ambitious implementation of Option D, not a new option.
- The cs50victor dependency: same fork, same patches.

---

## 10. Decision this doc captures

Ship v7. Option X invocation (`codex exec` with MCP config). Milestones M4→M5→M6. Reviewer validates at a new task when implementer signals M6 done. Kept scope disciplined: additive to v6, not a rewrite from scratch.
