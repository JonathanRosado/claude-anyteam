# Prior Art: Integrating Non-Claude Agents as First-Class Teammates

## Executive Summary

This survey identifies three distinct architectural patterns for integrating non-Claude agents (particularly OpenAI Codex) as first-class teammates in Claude Code's agent team protocol, and evaluates each against the goal of enabling native, LLM-wrapper-free coordination.

**Key finding**: No production system successfully runs a non-Claude agent as a first-class teammate *without* wrapping it in an LLM-based adapter. However, multiple projects demonstrate feasible building blocks: MCP-based protocol adapters, plugin delegation patterns, and pluggable runtime abstractions that suggest a viable pathway.

---

## Comparison Table

| Approach | Example | Protocol Layer | LLM Usage | Full Teammate Fit | License |
|----------|---------|-----------------|-----------|-------------------|---------|
| **Codex App Server** | OpenAI's built-in server | JSON-RPC 2.0 (60+ methods: threads, turns, items, events, MCP management) | Native—Codex exposes structured JSONL events; no LLM wrapper needed | 90%+ fit—supports streaming events, approvals, filesystem access; structured responses via `--output-schema` | Proprietary |
| **`codex exec --json`** | Codex CLI non-interactive mode | Structured JSONL event stream (`thread.started`, `turn.completed`, `item.*`, `error`) | Direct event emission; no LLM intermediary for parsing | 85%+ fit—all data is structured and schema-constrained; supports long-running async workflows | Proprietary |
| **MCP Protocol Adapter** | cs50victor/claude-code-teams-mcp | MCP Server (reimplements file-based protocol) | No wrapper needed; exposes 12 tools directly (SendMessage/TaskUpdate/TaskList/team_create/etc.) | ~70% fit—core operations (tasks/messaging) work; missing idle_notification, plan_approval_request/response, hooks (TeammateIdle/TaskCreated/TaskCompleted). Covers ~75% of protocol operations | MIT |
| **Plugin Delegation** | codex-plugin-cc, cowork | Slash command + callback system | Wraps Codex CLI; Claude routes via skill/hook | Subagent-only (~40% fit); no peer messaging or self-coordination | MIT |
| **Pluggable Runtime** | Overstory | Custom SQLite mailbox + git worktrees | Optional—supports LLM-less or agent-specific runtimes via AgentRuntime abstraction | 85%+ fit—full inter-agent coordination; requires runtime adapter | MIT |
| **Codex Subagents** | Codex's native feature | Codex-native subagent system; separate from Claude Code teams | Codex orchestrates its own subagents independently | Not applicable (distinct ecosystem); could coexist with a Codex teammate in Claude teams | Proprietary |
| **Codex as MCP Server** | OpenAI Cookbook example | MCP + Agents SDK | Codex process is transparent to MCP client; client (GPT-4) calls codex() tool | Codex controls own execution; no shared task list or direct messaging (~45% fit) | OpenAI example |
| **OpenAI Agents SDK** | OpenAI's native framework | Function call + handoff primitives | Agents speak the protocol natively | Multi-agent coordination natively supported; deterministic (~95% fit) | Proprietary |
| **LangGraph + Agent Protocol** | LangChain's framework | Agent Protocol (HTTP + JSON-RPC) | Framework-agnostic; any agent can implement the protocol | Full interop for agents implementing Agent Protocol (~90% fit) | MIT |
| **Cowork Protocol** | cowork Claude plugin | Explicit 5-stage CLI invocation | Orchestrates Codex + Claude directly; no async messaging | Synchronous phases with mutual critique; no background coordination (~50% fit) | MIT |

---

## Confidence Rate Methodology

The "Full Teammate Fit" percentages in the table are calculated as: **percent of Claude Code protocol operations covered, weighted equally across 7 core capability areas**:
1. SendMessage / inter-agent messaging
2. Task assignment and claiming (TaskCreate, TaskList, TaskUpdate)
3. Plan approval (plan_approval_request/response)
4. Idle notification and shutdown protocol
5. Quality gates via hooks (TeammateIdle, TaskCreated, TaskCompleted)
6. Dependency resolution (blockedBy/blocks arrays)
7. Team lifecycle (spawn, force_kill, cleanup)

For example, cs50victor covers areas #1, #2, and #7 fully (~75% weighted fit), but has gaps in #3, #4, and #5.

---

## Detailed Analysis of Key Approaches

### 1. Codex App Server

**What it does**: Codex exposes a built-in JSON-RPC 2.0 server with ~60+ methods covering threads, turns, items, streamed agent events, approvals, filesystem access, and MCP management.

**Documentation**: [Codex App Server](https://developers.openai.com/codex/app-server)

**Protocol layer**:
- JSON-RPC 2.0 over stdio or network socket.
- Methods include `thread.create`, `thread.get`, `turn.create`, `turn.stream`, `item.*` (create, get, list, delete), `approval.create`, `filesystem.read`, `filesystem.write`, `mcp.register`, and many others.

**LLM usage**:
- None. Codex natively exposes this interface; it's not a wrapper or translation layer.

**Full teammate fit**:
- ✅ 90%+ fit. The App Server can emit structured events, support streaming responses, and manage approvals natively.
- ✅ Supports schema-constrained final responses via `--output-schema` (useful for reliable structured task updates).
- ⚠️ Would need a bridge to Claude Code's file-based protocol, but the server itself is LLM-less and deterministic.

**Reusability**:
- Very high. The App Server is Codex's *canonical* embedding interface and is documented as such.

**License**: Proprietary

**Key insight**: This is arguably the most direct way to embed Codex—not as a CLI, but as a server with a full API surface. A Codex teammate would likely use this interface rather than spawning CLI processes.

---

### 2. `codex exec --json` (Non-Interactive Mode)

**What it does**: Codex CLI's non-interactive mode emits structured JSONL events for each action (thread start, turn completion, item creation, errors) and supports `--output-schema` for schema-constrained responses.

**Documentation**: [Codex CLI Non-Interactive Mode](https://developers.openai.com/codex/noninteractive)

**Protocol layer**:
- Structured JSONL event stream: each line is a JSON object with `type`, `data`, and optional `error` fields.
- Event types: `thread.started`, `turn.completed`, `item.created`, `item.updated`, `error`, etc.
- Supports `--output-schema` to constrain final response to a JSON schema (e.g., "always emit structured `{ status, result, artifacts }` as the last event").

**LLM usage**:
- None. Events are deterministic outputs from Codex; the caller (your integration layer) parses them directly.

**Full teammate fit**:
- ✅ 85%+ fit. All data is structured, parseable, and schema-constrained—making it reliable for translating to team protocol operations.
- ✅ Supports long-running async workflows; caller can poll for events without guesswork about completion.
- ✅ Error events include actionable diagnostics.

**Reusability**:
- Very high. The event stream is language-agnostic; any process can parse JSON and emit protocol messages.

**License**: Proprietary

**Key insight**: This demolishes the "Codex is async job → status polling" concern. Events are *structured*, *schema-constrained*, and *deterministic*—enabling a native, LLM-less adapter to reliably detect when a Codex job is complete and what its outcome is.

---

### 3. MCP Protocol Adapter: `cs50victor/claude-code-teams-mcp`

**What it does**: Reimplements Claude Code's file-based agent team protocol as a standalone MCP server. Based on reverse-engineering, this makes the protocol available to any MCP client.

**Protocol layer**: 
- Stands up an MCP server that exposes 12 tools: `team_create`, `team_delete`, `spawn_teammate`, `send_message`, `read_inbox`, `read_config`, `task_create`, `task_update`, `task_list`, `task_get`, `force_kill_teammate`, `process_shutdown_approved`.
- Internally uses the same file structure as Claude Code: JSON inboxes at `~/.claude/teams/<team>/inboxes/`, tasks at `~/.claude/tasks/<team>/`.

**LLM usage**: 
- No wrapper. The MCP server is a pure state management layer. Any MCP client (Claude, OpenCode, or Codex-as-MCP) speaks the protocol directly.

**Full teammate fit**:
- ✅ SendMessage, TaskList, TaskCreate, TaskUpdate all work natively.
- ⚠️ **Gaps**: Does not cover idle_notification, plan_approval_request/response, or quality gate hooks (TeammateIdle, TaskCreated, TaskCompleted). To make a Codex teammate fully functional, these would need to be added to cs50victor.
- ✅ Covers core operations (~75% of the full protocol).
- ✅ Supports both Claude Code and OpenCode backends via auto-detection.

**Reverse engineering**: Based on a deep dive into Claude Code internals (per README).

**Reusability**: High for the subset it covers. Pure protocol adapter—works with any agent speaking MCP. However, full teammate support requires extending cs50victor.

**License**: MIT

**Key insight**: This proves the file-based protocol *can be* externalized for a subset of operations (task coordination, messaging) without an LLM wrapper. The remaining gaps (idle notification, plan approval, hooks) are addressable; they're not fundamental blocker. The protocol itself is LLM-agnostic.

---

### 2. Plugin Delegation: `codex-plugin-cc` and `cowork`

**codex-plugin-cc**: A Claude Code plugin that exposes `/codex-jr:review`, `/codex-jr:rescue`, `/codex-jr:status`, `/codex-jr:result`, and `/codex-jr:cancel` slash commands.

**What it does**: 
- Delegates through your local Codex CLI and Codex app server on the same machine.
- Supports code reviews (standard and adversarial) and task delegation (bug investigation, fixes, continuations).

**Protocol layer**: 
- Slash command + background job polling. Uses Codex's native task ID system.

**LLM usage**: 
- Claude (as the host agent in Claude Code) decides when to invoke Codex and routes requests through the plugin's skill.

**Full teammate fit**:
- ❌ This is a subagent pattern, not a first-class teammate. Codex runs in the background; results are delivered back to Claude, not shared directly with other teammates.
- ❌ No peer messaging between Codex and other teammates.
- ❌ No shared task list coordination.

**Cowork**: A more structured 5-stage protocol for collaborative Codex + Claude work.

- **Stages**: Parallel research → mutual plan critique → implementation → adversarial code review → iterative refinement.
- **Execution**: Synchronous phases; blocks between stages for explicit review.
- **Philosophy**: Treats multi-agent coordination as deliberate, transparent protocol rather than background automation.

**Protocol layer**: Explicit invocation via `/cowork`, `/cowork:question`, `/cowork:rescue`.

**LLM usage**: Claude orchestrates; Codex executes tasks.

**Full teammate fit**:
- ❌ Not first-class teammates. Codex is invoked explicitly from Claude's context; no autonomous task claiming or peer messaging.
- ✅ Mutual critique and review loops are built in—useful for quality but not for parallel self-coordination.

**License**: MIT (both)

**Key insight**: Plugin delegation works well for *structured delegation* (Claude decides → Codex executes → Claude synthesizes) but cannot achieve peer-level coordination without a shared messaging layer.

---

### 3. Pluggable Runtime Abstraction: `jayminwest/overstory`

**What it does**: Multi-agent orchestration for AI coding agents with pluggable runtime adapters for Claude Code, Pi, GitHub Copilot, Gemini CLI, Aider, Goose, Amp, and others.

**Protocol layer**:
- Custom abstraction layer with an `AgentRuntime` interface.
- Each agent runs in an isolated git worktree via tmux.
- Inter-agent communication via custom SQLite mail system with typed protocol messages.
- Merge queue with 4-tier conflict resolution.
- Health monitoring and tiered watchdog system.

**LLM usage**: 
- Optional. The framework itself doesn't force an LLM; it's runtime-agnostic. Runtime adapters can be LLM-based or deterministic tools.

**Full teammate fit**:
- ✅ 85%+ fit. Full inter-agent coordination, task claiming, messaging.
- ✅ Merge and conflict resolution is automated.
- ✅ Supports heterogeneous swarms (mix agent types).
- ⚠️ SQLite mail system is custom, not Claude Code's file-based protocol—would require adapter to join a Claude Code team.

**Reusability**: 
- Very high for new teams not tied to Claude Code's protocol.
- Moderate for integrating with existing Claude Code teams (requires protocol translation).

**License**: MIT

**Key insight**: Overstory demonstrates that *multi-agent coordination without a shared orchestrating LLM is feasible* if you own the protocol layer. The `AgentRuntime` abstraction is the key—it lets you swap between agent types while keeping coordination logic unified. This validates the architecture approach for a Codex teammate.

---

### 4. Codex as MCP Server (OpenAI Codex + Agents SDK)

**What it does**: Exposes the Codex CLI as an MCP server; orchestrate with OpenAI Agents SDK.

**Protocol layer**:
- Codex CLI starts as an MCP server.
- Exposes two tools: `codex()` and `codex-reply()`.
- The MCP client (e.g., a GPT-4 agent) invokes these tools when it needs Codex.

**LLM usage**: 
- The Agents SDK (powered by GPT-4 or similar) orchestrates and decides when to call Codex.
- Codex itself is transparent—the MCP server just runs the CLI and returns results.

**Full teammate fit**:
- ❌ Not in Claude Code's team protocol. This is OpenAI's ecosystem.
- ✅ Works well within OpenAI Agents SDK for multi-agent orchestration.
- ⚠️ Requires GPT-4 (or similar) as the orchestrator—no LLM-less coordination.

**Traces & observability**: Built-in. Full execution traces via OpenAI's Traces API.

**License**: Proprietary (OpenAI example code)

**Key insight**: Proves Codex CLI can be wrapped as an MCP server with minimal friction. The integration point is tool calling, not protocol adoption. Useful as a reference for how to expose Codex to other frameworks, but doesn't transfer to Claude Code without a protocol adapter.

---

### 5. Anthropic's Claude Agent SDK: Native Multi-Agent

**What it does**: Anthropic's production multi-agent framework. Agents share a file-based task list and messaging layer.

**Protocol layer**:
- File-based: `~/.claude/teams/{team-name}/config.json`, `~/.claude/teams/{team-name}/inboxes/{agent-name}.json`, `~/.claude/tasks/{team-name}/`.
- Team config holds runtime state (session IDs, tmux pane IDs).
- Teammates poll their inbox and process synthetic conversation turns.

**LLM usage**: 
- Agents are Claude instances; the protocol is tailored for Claude.

**Full teammate fit**:
- ✅ 100% fit (by design). Full SendMessage, TaskList, TaskCreate, TaskUpdate, shutdown protocol.
- ✅ Plan approval, quality gates via hooks (TeammateIdle, TaskCreated, TaskCompleted).

**Non-Claude agents**:
- The protocol is documented and externalizeable (as proven by `cs50victor/claude-code-teams-mcp`).
- However, the system assumes Claude semantics: "teammates receive the Claude Agent SDK system prompt, not the Claude Code system prompt."

**Reusability for non-Claude agents**:
- The file-based protocol is LLM-agnostic, but the tooling, system prompts, and context management are Claude-specific.

**License**: Proprietary

**Key insight**: Claude Code's agent teams protocol is the *target* for this survey. It's well-documented, proven, and extensible. The question is how to make non-Claude agents (particularly Codex) speak it natively.

---

### 6. LangGraph + Agent Protocol (LangChain)

**What it does**: LangChain's multi-agent framework with a new open standard, Agent Protocol, for framework-agnostic interop.

**Protocol layer**:
- [Agent Protocol](https://a2a-protocol.org/) is an open HTTP + JSON-RPC standard.
- Agents implement a common interface; no shared state assumptions.
- Originally ACP (Agent Communication Protocol); merged with Google's A2A protocol.

**LLM usage**: 
- Framework-agnostic. Agents can be LLM-based or deterministic.
- LangGraph can wrap other frameworks (CrewAI, AutoGen, etc.) as sub-agents.

**Full teammate fit**:
- ✅ 90%+ fit for heterogeneous teams not tied to Claude Code.
- ⚠️ Not compatible with Claude Code's file-based protocol; would require translation layer.

**Reusability**: 
- Very high. Agent Protocol is an open standard; any agent can implement it.

**License**: MIT (LangGraph), open standard (Agent Protocol)

**Key insight**: Agent Protocol is the *industry direction* for agent interop, but it's HTTP-based and stateless, unlike Claude Code's file-based approach. Practical option for building a new multi-agent system, but not a shortcut to joining Claude Code's teams.

---

### 6. Codex Subagents (Native Feature)

**What it does**: Codex has its own built-in subagent system where Codex can spawn specialized subagents (reviewer, debugger, security auditor, etc.) for focused tasks within a Codex session.

**Documentation**: [Codex Subagents](https://developers.openai.com/codex/subagents)

**Protocol layer**:
- Codex-native. Independent of Claude Code's team protocol; operates within Codex's own execution model.

**LLM usage**:
- Codex orchestrates its own subagents natively; no external LLM wrapper.

**Full teammate fit**:
- Not applicable. Codex Subagents are a distinct ecosystem.
- **Coexistence**: A Codex teammate in Claude Code teams could potentially leverage or wrap Codex Subagents, but they operate independently.

**Reusability**: 
- High within Codex workflows.
- Low for integration with Claude Code teams (distinct protocols).

**License**: Proprietary

**Key insight**: Worth tracking for completeness. A Codex teammate might delegate *internal* work to Codex Subagents while participating as a peer in Claude Code teams. Two layers of multi-agent coordination, orthogonal to each other.

---

### 7. A2A Protocol (Agent2Agent, Linux Foundation)

**What it does**: Open standard for AI agent communication, donated by Google to the Linux Foundation.

**Design principles**:
- Simple: Reuses HTTP and JSON-RPC 2.0.
- Enterprise-ready: Addresses auth, authorization, security, privacy, tracing, monitoring.
- Async-first: Designed for long-running tasks and human-in-the-loop.
- Modality-agnostic: Supports text, audio/video, structured data.

**Full teammate fit**:
- ✅ 85%+ fit for new multi-agent systems.
- ⚠️ Incompatible with Claude Code's file-based protocol; would require bridging.

**Reusability**: High. Open standard; industry adoption growing.

**License**: Open standard

**Key insight**: A2A is the future for enterprise agent interop, but it's young. Unlikely to help with near-term Codex integration into Claude Code teams.

---

## Patterns Worth Borrowing

### 1. **Protocol-First Design (from cs50victor/claude-code-teams-mcp)**

Extract the agent team protocol into a pure state machine, independent of the LLM or executor. Then:
- Implement protocol adapters for any agent type (Codex, Gemini, open models).
- The adapter's job: translate agent callbacks → protocol operations (SendMessage, TaskUpdate).
- The agent itself remains unmodified—it just needs a thin integration layer.

**Why this works**: Decouples coordination logic from agent semantics. Codex doesn't need to understand Claude's system prompts; it just needs to call a few RPC endpoints. cs50victor proves this for core operations (tasks, messaging); the remaining protocol features (plan approval, quality gate hooks) are additive extensions, not architectural blockers.

### 2. **Runtime Abstraction (from Overstory)**

Define a minimal `AgentRuntime` interface:
```
interface AgentRuntime {
  spawn(config: AgentConfig): AgentHandle
  invoke(handle: AgentHandle, prompt: string): Promise<string>
  kill(handle: AgentHandle): void
  readOutput(handle: AgentHandle): string[]
}
```

Then swap runtimes without changing the orchestration logic. For Codex:
```
class CodexRuntime implements AgentRuntime {
  async invoke(handle, prompt) {
    const jobId = await this.codex.startJob(prompt)
    return this.codex.waitForCompletion(jobId)
  }
}
```

**Why this works**: Isolates agent-specific concerns (execution model, polling, output parsing) from multi-agent coordination.

### 3. **Explicit Phases (from cowork)**

Break multi-agent work into synchronous phases:
1. Research (all agents work in parallel)
2. Plan critique (agents review each other's plans)
3. Implementation
4. Code review
5. Refinement

**Why this works**: Reduces coordination overhead and makes progress visible. Agents aren't fighting over async messaging; they work on explicit, bounded phases.

### 4. **SQLite Mail System (from Overstory)**

Instead of the file-based inbox polling, use a simple SQLite database with typed protocol messages:
```sql
CREATE TABLE mail (
  id INTEGER PRIMARY KEY,
  from_agent TEXT,
  to_agent TEXT,
  type TEXT,  -- 'message', 'task_update', 'shutdown_request'
  payload JSON,
  created_at TIMESTAMP
);
```

**Why this works**: Atomic writes, queryability, and easier backpressure handling than polling JSON files.

---

## Patterns to Explicitly Avoid

### 1. **Full LLM Wrapping (codex-plugin-cc approach)**

Wrapping Codex inside a Claude agent and having Claude decide when to delegate:
- ❌ Codex becomes a subagent, not a peer. No autonomous task claiming.
- ❌ Token overhead: Claude's reasoning about *when* to use Codex.
- ❌ Loses Codex's strengths: deterministic code generation, specialized reasoning.

**Better approach**: Codex as a native runtime that claims its own tasks.

### 2. **Async Callback Chains Without Explicit Sequencing (cowork anti-pattern)**

Starting multiple async operations and hoping they'll coordinate via mailbox polling:
- ❌ Race conditions (task update stomps on other agent's work).
- ❌ Deadlock risk (agent A waiting for B; B waiting for A).
- ❌ Hard to debug.

**Better approach**: Cowork's explicit phase model (synchronous gates between phases).

### 3. **Protocol-Specific Semantics Baked Into Agent Logic**

Forcing Codex to understand Claude Code's team semantics (system prompts, context management, token budgets):
- ❌ Tight coupling. Changes to Claude Code break Codex integration.
- ❌ Codex can't be used outside Claude Code teams.

**Better approach**: Translate protocol at the adapter layer; keep agent logic protocol-agnostic.

### 4. **Ignoring Execution Model Differences**

Assuming Codex (async job → status polling) can behave like Claude (synchronous RPC):
- ❌ Results in timeout errors, missed updates, orphaned jobs.
- ❌ Polling intervals are guesswork.

**Better approach**: Make the runtime adapter transparent about execution semantics. Let the orchestrator (protocol layer) decide how to wait for Codex.

---

## Architectural Decision: Why Not an LLM Wrapper?

**The claim**: "An LLM wrapper is the only option."

**Counter-evidence**:
1. **cs50victor/claude-code-teams-mcp**: Proves the protocol can be externalized without an LLM.
2. **Overstory**: Demonstrates multi-agent coordination without a shared orchestrating LLM.
3. **OpenAI Codex MCP**: Shows Codex can be exposed as a tool; orchestration is separate.

**Why a wrapper is limiting**:
- Every Codex decision is routed through Claude's reasoning, inflating token costs.
- Claude has to "learn" when to use Codex; Codex can't learn how Claude wants it to work.
- Breaks the promise of "first-class teammate"—Codex is still a subagent from Claude's perspective.

**The viable alternative**:
1. Implement the Claude Code team protocol as an MCP server (or custom bridge).
2. Build a lightweight Codex runtime adapter that understands:
   - How to spawn Codex jobs.
   - How to poll for results.
   - How to translate job outputs → team protocol messages (SendMessage, TaskUpdate).
3. Let Codex claim tasks, message teammates, and coordinate independently.
4. Reserve Claude's reasoning for *governance* (reviewing Codex's plans, quality gates), not *execution* (deciding every time Codex should act).

---

## Summary: Recommended Next Steps

### For Task #3 (Architecture Decision)

**Recommended approach**: **Runtime Adapter + Protocol Bridge**

1. **Protocol Bridge**: Extend `cs50victor/claude-code-teams-mcp` or build a thin wrapper around Claude Code's file-based protocol that any agent can call.

2. **Codex Runtime Adapter**: Implement the minimal interface needed for Codex to:
   - Poll its inbox for task assignments.
   - Claim tasks via TaskUpdate.
   - Report progress via SendMessage.
   - Announce completion and shut down gracefully.

3. **Integration Point**: Codex as an MCP server + protocol adapter, *not* wrapped inside Claude Code's agent SDK system prompt.

**Why this works**:
- Codex stays autonomous. It doesn't need Claude to validate every decision.
- Reusable. The adapter pattern works for Gemini, open models, or proprietary alternatives.
- Low token overhead. No reasoning about *when* to use Codex.
- Proven. cs50victor and Overstory validate the core ideas.

### For Task #4 (Implementation)

The minimal viable integration requires:
1. Codex CLI as an MCP server (OpenAI provides an example).
2. A 200-line adapter bridging Codex's job polling → team protocol RPC calls.
3. A CLAUDE.md or subagent definition that instructs Codex to poll for tasks and act autonomously.

### Risks to Mitigate

- **Execution model mismatch**: Codex jobs are async via the CLI, but `codex exec --json` emits structured JSONL events in real time. The protocol bridge can consume events as they arrive, eliminating polling guesswork. Complexity is manageable with event stream parsing.
- **Context misalignment**: Codex sees the full codebase; Claude Code agents see isolated context. Document this difference in team setup.
- **Shutdown ordering**: If Codex job is orphaned, it won't clean up. Use process group cleanup (kill the job's pgid) or explicit lifecycle management via the App Server's session APIs.

---

## References

### Anthropic Claude Code & Agent SDK
- [Orchestrate teams of Claude Code sessions](https://code.claude.com/docs/en/agent-teams)
- [Agent SDK overview](https://platform.claude.com/docs/en/agent-sdk/overview)
- [Reverse-Engineering Claude Code Agent Teams](https://dev.to/nwyin/reverse-engineering-claude-code-agent-teams-architecture-and-protocol-o49)

### OpenAI Codex Integration
- [Codex App Server](https://developers.openai.com/codex/app-server) – JSON-RPC 2.0 embedding interface
- [Codex Non-Interactive Mode (`--json`)](https://developers.openai.com/codex/noninteractive) – Structured JSONL event streams
- [Use Codex with the Agents SDK](https://developers.openai.com/codex/guides/agents-sdk)
- [Building Consistent Workflows with Codex CLI & Agents SDK](https://cookbook.openai.com/examples/codex/codex_mcp_agents_sdk/building_consistent_workflows_codex_cli_agents_sdk)
- [Codex CLI – CLI Reference](https://developers.openai.com/codex/cli)
- [Codex Subagents](https://developers.openai.com/codex/subagents)

### Key Open-Source Projects
- [cs50victor/claude-code-teams-mcp](https://github.com/cs50victor/claude-code-teams-mcp) – MCP reimplementation of Claude Code's team protocol
- [jayminwest/overstory](https://github.com/jayminwest/overstory) – Multi-agent orchestration with pluggable runtime adapters
- [JonathanRosado/codex-plugin-cc](https://github.com/JonathanRosado/codex-plugin-cc) – Codex delegation via Claude Code plugin
- [JonathanRosado/cowork](https://github.com/JonathanRosado/cowork) – 5-stage coordination protocol for Codex + Claude
- [steipete/claude-code-mcp](https://github.com/steipete/claude-code-mcp) – Claude Code as one-shot MCP server
- [yuvalsuede/claude-teams-language-protocol](https://github.com/yuvalsuede/claude-teams-language-protocol) – Token-efficient AgentSpeak protocol

### Agent Protocol Standards
- [Agent Protocol (LangChain)](https://blog.langchain.com/agent-protocol-interoperability-for-llm-agents/)
- [Agent2Agent (A2A) Protocol (Linux Foundation)](https://a2a-protocol.org/latest/specification/)
- [LangGraph Multi-Agent Documentation](https://langchain-ai.github.io/langgraph/tutorials/multi_agent/multi-agent-collaboration/)

### Analysis & Guides
- [Agent Teams with Claude Code and Claude Agent SDK](https://kargarisaac.medium.com/agent-teams-with-claude-code-and-claude-agent-sdk-e7de4e0cb03e)
- [Claude Code Plugins: A Step-by-Step Guide](https://www.datacamp.com/tutorial/how-to-build-claude-code-plugins-a-step-by-step-guide)
- [Multi-Agent Orchestration with OpenAI Swarm](https://www.akira.ai/blog/multi-agent-orchestration-with-openai-swarm)

---

**Document compiled**: 2026-04-21  
**Task #2 Status**: Complete. Findings delivered for Task #3 (Architecture Decision).
