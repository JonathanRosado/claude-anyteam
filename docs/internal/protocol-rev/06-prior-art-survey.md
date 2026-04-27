# 06 — Prior-art survey for a future beautiful protocol

**Author:** codex-priorart  
**Date:** 2026-04-27  
**Scope:** research-only; protocol design patterns for closing the routed-teammate visibility gap.

## Executive summary

The north star from `CLAUDE.md` is **visibility parity**: a lead should see routed teammate prose, tool activity, idle reasons, DMs, errors, and lifecycle transitions at native-teammate fidelity without reading stderr or tmux panes. The strongest prior art converges on one shape: a **thin, capability-negotiated JSON-RPC control plane** plus **typed streaming notifications** and **durable identity/session objects**. ACP, MCP, A2A, AutoGen Core, OpenHands SDK, and LSP all reinforce that the adapter boundary should not be a final-summary CLI wrapper. It should be an event fan-out layer that can normalize backend-specific tool/progress artifacts into the B9-style event envelope.

Vendored specs, limited to the requested 2-3 documents:

- `references/external-claude-code-re/protocol-specs/acp-agent-client-protocol-schema-v0.12.2.json` — Agent Client Protocol schema, current upstream release on 2026-04-23.
- `references/external-claude-code-re/protocol-specs/mcp-schema-2025-11-25.json` — Model Context Protocol schema, latest tagged spec release.
- `references/external-claude-code-re/protocol-specs/a2a-protocol-v1.0.0.proto` — Agent2Agent protocol protobuf, current v1.0.0 release.

## Comparative survey

### 1. ACP — Agent Client Protocol (the Gemini backend path)

**Agent unit.** ACP models an agent as a program, usually a local subprocess of an editor/client, that owns sessions and may run LLM/tool loops. It is called **Agent Client Protocol** in upstream docs; our Gemini docs also call it “ACP.” It should not be confused with AGNTCY’s Agent Connect Protocol.  
**Task delivery.** JSON-RPC over newline-delimited stdio today: `initialize`, optional auth, `session/new` or `session/load`, then `session/prompt`. The client can pass per-session MCP servers and set modes/models.  
**Observation.** The key visibility primitive is `session/update`: agent message chunks, plan updates, `tool_call` and `tool_call_update` notifications, permission requests, and final `session/prompt` stop reasons. Gemini ACP empirically emits these, though built-in tool outputs may be missing while MCP tool output is present.  
**Identity/lifecycle.** Protocol version/capabilities, `agentInfo`, session ids, load/list/close/cancel, and mode state. No teammate roster or task queue.  
**License/maturity.** Apache-2.0; upstream v0.12.2, ~2.9k stars, active editor-agent ecosystem. Remote HTTP remains draft.  
**Adapter fit.** **High.** We already use it for Gemini. Borrow the session/update taxonomy directly, but normalize its gaps: distinguish “tool observed but output absent” from “no tool happened,” and persist turn/session ids into our event log.

### 2. MCP — Model Context Protocol (Anthropic)

**Agent unit.** MCP does not define autonomous agents. It defines clients and servers: an agent/host is usually the **MCP client**, while tools/resources/prompts live behind **MCP servers**.  
**Task delivery.** Tasks are delivered indirectly as tool/resource/prompt requests (`tools/call`, `resources/read`, `prompts/get`) over JSON-RPC transports such as stdio or streamable HTTP. It is a capability surface, not an agent work queue.  
**Observation.** MCP has structured result/error responses, logging notifications, cancellation, progress notifications, sampling, elicitation, and roots. It observes tools well but only if the host chooses to expose those events upstream.  
**Identity/lifecycle.** `initialize` and capability negotiation model protocol version and server/client features; sessions are transport-level. There is no spawn/idle/shutdown lifecycle for an agent.  
**License/maturity.** Licensing is in transition from MIT to Apache-2.0, with docs generally CC-BY-4.0; latest spec tag is 2025-11-25; very mature ecosystem.  
**Adapter fit.** **Already essential but insufficient.** MCP is the right way to expose our narrowed `send_message`/`task_update`/read tools to backends. It cannot itself represent a teammate turn or native-style activity stream unless wrapped by our protocol envelope.

### 3. A2A — Agent2Agent Protocol (Google / Linux Foundation)

**Agent unit.** A2A models an opaque remote **agentic application** advertised by an Agent Card. It explicitly assumes agents may hide internal memory, tools, and implementation.  
**Task delivery.** A client sends a message with accepted modalities. The server returns or creates a Task; task operations include send, streaming send, get/list, cancel, subscribe, and push-notification config. Transports include JSON-RPC/HTTP(S) and, in v1.0.0, REST/gRPC mappings from the protobuf.  
**Observation.** A2A has streaming and subscription paths for `TaskStatusUpdateEvent` and artifact updates, plus asynchronous webhook-style push notifications. This is good for long-running remote agents, but it standardizes task status/artifacts more than low-level tool telemetry.  
**Identity/lifecycle.** Agent Cards carry endpoint, capabilities, skills, security, and modality info. Tasks have ids, context ids, states, statuses, artifacts, cancellation, and terminal states.  
**License/maturity.** Apache-2.0, Linux Foundation project, v1.0.0 released 2026-03-12, ~23k stars.  
**Adapter fit.** **Medium.** A2A could plug in as a backend for opaque remote agents or as a bridge that exposes anyteam teammates outward. It will not by itself close visibility parity unless the remote agent emits rich task events; we should treat A2A artifacts/status as terminal/digest inputs, not proof of host-tool visibility.

### 4. LangGraph

**Agent unit.** LangGraph’s unit is a graph: state schemas, nodes (functions/runnables/agents), conditional edges, subgraphs, and checkpointers. “Agents” are usually graph nodes or prebuilt ReAct-style graphs rather than OS processes.  
**Task delivery.** Direct in-process calls such as invoke/stream against a graph, or LangGraph Platform/SDK runs. Tasks are represented by input state and graph traversal, not a universal wire protocol.  
**Observation.** Strong internal streaming: graph values, updates, messages, custom events, and LangSmith tracing/observability. Checkpointers give resumable state and time travel.  
**Identity/lifecycle.** Run/thread ids and checkpoints model continuity; node names model internal actor identity. Lifecycle is graph/run-oriented, not spawn/idle/shutdown.  
**License/maturity.** MIT, ~30k stars, very mature for production app graphs.  
**Adapter fit.** **Medium.** A LangGraph backend could run inside a Python adapter and map stream events to our envelope. But LangGraph is not a heterogeneous teammate protocol; using it as the protocol would leak graph concepts and lose file-team semantics. Borrow persistence and event streaming, not its internal graph DSL.

### 5. CrewAI

**Agent unit.** CrewAI’s main unit is an `Agent` object with role, goal, backstory/instructions, tools, memory, delegation, and an LLM. Agents are grouped into `Crew`s; `Flow`s provide event-driven orchestration across crews/functions.  
**Task delivery.** `Task` objects describe work and expected output. Crews execute tasks sequentially, hierarchically, or via process settings; Flows use decorators/listeners to route outcomes.  
**Observation.** CrewAI has an event bus with lifecycle events for crews, agents, tasks, tool usage, and errors, plus callbacks/tracing integrations. The event listener pattern is useful for monitoring, but the protocol is Python-library internal.  
**Identity/lifecycle.** Agent ids/roles, Crew kickoff/completion, Flow lifecycle, task completion/error events. No cross-process spawn or external mailbox semantics.  
**License/maturity.** MIT, v1.14.3, ~50k stars; popular but still a framework surface rather than an interop standard.  
**Adapter fit.** **Low/medium.** A CrewAI crew can be a backend executor if we wrap `kickoff` and listen to events. It cannot be our beautiful protocol because identities and messages are framework-specific and user-visible tool details depend on event listener fidelity.

### 6. AutoGen (Microsoft)

**Agent unit.** AutoGen Core defines agents with `AgentId`, metadata, message handlers, and an agent runtime. AgentChat adds higher-level agents and team presets such as round-robin, selector group chat, Magentic-One, and swarm/handoff patterns.  
**Task delivery.** Messages are serializable data objects. Core supports direct send and publish/subscribe topics; AgentChat teams run with a task via `run` or stream with `run_stream`. Distributed runtimes use a host service plus worker runtimes over process boundaries.  
**Observation.** AgentChat supports streaming team messages and final `TaskResult`. Core/AgentChat have structured event logging and OpenTelemetry tracing for agents/tools/runtime events.  
**Identity/lifecycle.** Agent types register factories; the runtime creates instances on first delivery and manages lifecycle, sessions, topics, and distributed worker connections. Team reset clears state.  
**License/maturity.** Code is MIT; docs/content are CC-BY-4.0. v0.7.5 Python release, ~57k stars; distributed runtime remains experimental.  
**Adapter fit.** **Medium/high for ideas.** AutoGen has one of the best identity/runtime models. However, it is a framework runtime, not a CLI-agent protocol; plugging it in means translating AgentId/messages/topics into anyteam task/mail/event envelopes.

### 7. OpenAI Swarm

**Agent unit.** Swarm’s unit is a lightweight `Agent` object: name, instructions, functions/tools, and handoff targets. It deliberately keeps state in the caller.  
**Task delivery.** Direct `client.run(agent, messages, ...)`; a function can return another `Agent` to hand off control. No external queue, no durable task object.  
**Observation.** It supports streaming options and debug-ish caller control, but no comprehensive lifecycle or observability model. It is mainly a teaching implementation for routines and handoffs.  
**Identity/lifecycle.** Identity is just the current `Agent` object and returned handoffs; stateless between calls unless the caller persists messages. No spawn/idle/shutdown.  
**License/maturity.** MIT, ~21k stars, but explicitly experimental/educational and replaced by the OpenAI Agents SDK for production.  
**Adapter fit.** **Low.** Useful only as a conceptual reference for explicit handoffs. Avoid copying its statelessness for teammates; visibility parity requires durable turn ids, cancellation, events, and errors.

### 8. OpenHands / OpenHands Software Agent SDK

**Agent unit.** OpenHands models an `Agent` with LLM, tools, workspace, and conversation. The SDK can run locally or through an Agent Server with ephemeral workspaces such as Docker/Kubernetes. The full OpenHands app adds GUI/API/cloud flows.  
**Task delivery.** A `Conversation` receives user messages and runs; remote server examples use REST/WebSocket-style client/server architecture. The SDK also supports workflows, delegation examples, persistence, pause, fork, ACP-agent examples, and task-tracker tools.  
**Observation.** Strong fit: callbacks receive events, conversation state stores events, and examples demonstrate event histories, forks, hook events, metrics, and custom visualizers. The paper/docs emphasize sandboxed execution and lifecycle control.  
**Identity/lifecycle.** Conversation ids/state, event logs, workspace identity, pause/fork/resume patterns, remote Agent Server processes/containers.  
**License/maturity.** Core OpenHands is MIT except `enterprise/`; SDK is MIT. OpenHands ~72k stars, SDK v1.18.1.  
**Adapter fit.** **High as a backend, not as our protocol.** It can expose rich events and sandboxing, but brings its own runtime and workspace model. We could map Conversation events to B9 envelopes and treat its sandbox/workspace as backend-private implementation detail.

### 9. Aider

**Agent unit.** Aider is a terminal pair-programming CLI operating in a git repo. The “agent” is one CLI/chat session with model, repo map, edit format, and optional architect mode.  
**Task delivery.** Interactive chat or one-shot `--message` / `--message-file`; `--watch-files` supports coding comments; `--load` can execute startup commands. It can auto-commit LLM changes and run lint/test commands.  
**Observation.** Observation is primarily terminal stdout/stderr, git commits/diffs, and command output. There is no stable external tool-event stream comparable to ACP/Codex App Server.  
**Identity/lifecycle.** Process/session plus git repository state; no peer identity, inbox, idle, or shutdown protocol.  
**License/maturity.** Apache-2.0, v0.86.0 latest release, ~44k stars; very mature for local pair programming.  
**Adapter fit.** **Medium/low.** Easy to invoke headlessly, but visibility parity would be poor unless we wrap stdout, git status, and auto-commit events into coarse artifacts. Good fallback executor; not a protocol model.

### 10. Cline

**Agent unit.** Cline is a VS Code extension-side autonomous coding agent with provider LLMs, editor context, terminal/browser/file tools, MCP tools, and an approval-first UI.  
**Task delivery.** User opens a VS Code task/chat; the extension runs the tool loop and asks the human to approve file changes and terminal commands. MCP servers extend its capabilities.  
**Observation.** Strong human-facing visibility inside VS Code: the UI shows tool plans, approvals, diffs, terminal commands, and checkpoints. Cline’s checkpoint docs say it uses git commits under the hood so users can compare/restore workspace states. Externally, however, those events are extension internals.  
**Identity/lifecycle.** Task/session in VS Code; checkpoints; permission prompts; extension state. No open teammate roster, peer DMs, or backend-neutral lifecycle.  
**License/maturity.** Apache-2.0, v3.81.0, ~61k stars, mainstream.  
**Adapter fit.** **Low.** We can borrow approval/checkpoint UX patterns, but Cline is not a CLI protocol backend unless a separate API is exposed.

### 11. Continue

**Agent unit.** Continue now spans IDE assistant plus open-source `cn` CLI agents/checks. A PR check is a markdown agent in `.continue/checks/`; a CLI session is a configurable coding agent.  
**Task delivery.** IDE chat/agent mode, PR status checks, or CLI. The `cn -p` headless flag runs without TUI for scripts/CI/Docker/extension integration and supports JSON output, silent mode, resume, session listing, and HTTP server mode.  
**Observation.** CI status checks give clear pass/fail plus suggested diffs. CLI/extension can show a chat/tool stream, but the stable external contract appears less rich than ACP/OpenHands.  
**Identity/lifecycle.** Session history/resume; PR check files define reusable agents; status-check lifecycle maps well to CI but not peer DMs/idle.  
**License/maturity.** Apache-2.0, v1.2.22 VS Code release, ~33k stars.  
**Adapter fit.** **Medium.** Continue CLI headless is easy to invoke and more automation-friendly than IDE-only tools. For parity, we would need either its JSON stream/server events or our own artifact/status sampler.

### 12. Cursor

**Agent unit.** Cursor’s units are proprietary IDE chat/agent/background-agent sessions plus modes such as Ask/Edit/Agent/custom modes. Agent mode can plan, edit files, run terminal commands, and present changes for review.  
**Task delivery.** User/editor-driven prompts, background agents, and mode-specific chats. No open wire protocol for external coordinators comparable to ACP/MCP/A2A.  
**Observation.** Very strong product UI: live edits, terminal activity, review changes, background work, and codebase context. But the observability surface is proprietary and user-facing, not adapter-facing.  
**Identity/lifecycle.** Editor sessions, background runs, possibly worktree-like flows; no open teammate lifecycle, mailbox, or event schema.  
**License/maturity.** Proprietary; mature commercial product.  
**Adapter fit.** **No direct fit.** Treat Cursor as UX prior art only. Borrow mode separation, background-agent review gates, and per-agent/worktree isolation ideas, but do not depend on proprietary APIs.

### 13. AGNTCY (open agent network standard)

**Agent unit.** AGNTCY focuses on Internet-of-Agents infrastructure: OASF records describe agents/MCP servers/A2A agents; Agent Directory stores/discovers signed records; identity/messaging/observability components surround them. AGNTCY also has an Agent Connect Protocol, but docs currently mark that component as not under active development.  
**Task delivery.** Agent Connect Protocol is REST/OpenAPI-oriented: configure/invoke remote agents, start runs, block/poll/stream/callback, interrupt/resume, and continue thread runs. Directory/OASF delivery is discovery, not task execution.  
**Observation.** Network-level observability and event streaming are part of the stack, but not a direct host-tool telemetry protocol.  
**Identity/lifecycle.** Strong discovery identity: names, versions, capabilities/skills, locators, signatures, CIDs/records. Runs/threads exist in Agent Connect, but lifecycle standard maturity is lower than A2A/ACP.  
**License/maturity.** Apache-2.0 repos; AGNTCY launched publicly in 2025 and moved toward Linux Foundation governance. `agntcy/acp-spec` is small (~164 stars).  
**Adapter fit.** **Medium for manifests, low for runtime.** Borrow OASF/Agent Card-style descriptors for backend capabilities and trust. Do not anchor our runtime on the inactive Agent Connect Protocol.

### 14. JSON-RPC + LSP as the heterogeneous-backend analog

**Agent unit.** LSP’s unit is a language server process behind a stable editor protocol; JSON-RPC itself is transport-agnostic request/response/notification framing. This is the closest historical analogy for hosting many heterogeneous backends behind one client UI.  
**Task delivery.** Requests with ids expect responses; notifications are fire-and-forget. LSP adds `initialize`, capability negotiation, text document sync, diagnostics, code actions, work-done progress, cancellation, shutdown, and exit.  
**Observation.** Mature pattern: servers emit diagnostics/progress notifications while clients correlate long-running work by ids/tokens. Errors are structured and request-scoped.  
**Identity/lifecycle.** Initialization handshake, server capabilities, workspace roots, cancellation, shutdown/exit, and request ids. No “agent” semantics, but excellent lifecycle grammar.  
**License/maturity.** JSON-RPC 2.0 dates to 2010; LSP 3.17 is widely implemented across editors/languages.  
**Adapter fit.** **Very high as protocol grammar.** Our beautiful protocol should look like LSP for agents: tiny base envelope, explicit capabilities, request ids, progress tokens, notifications, cancellation, and no backend-specific method leakage at call sites.

### 15. Other relevant: OpenAI Agents SDK, Goose, and the older Agent Protocol

**Agent unit.** The OpenAI Agents SDK (MIT, v0.14.6, ~25k stars) is Swarm’s maintained successor: agents with instructions/tools/guardrails/handoffs, sandbox agents, sessions, MCP, human-in-the-loop, and tracing. Goose (Apache-2.0, v1.32.0, ~43k stars) is an open local/desktop/CLI agent with MCP and ACP ecosystem relevance. The older AGI/E2B Agent Protocol is an MIT OpenAPI “common interface for interacting with AI agents,” but it is far less active.  
**Task delivery.** SDK direct runner/handoffs; Goose CLI/app/API; Agent Protocol run endpoints.  
**Observation.** Agents SDK has built-in tracing; Goose and ACP adapters can surface tool loops if their protocol mode is used; old Agent Protocol is mostly final-state/run oriented.  
**Identity/lifecycle.** Sessions, handoffs, traces, and sandbox/container agents in OpenAI SDK; CLI/app sessions in Goose.  
**Adapter fit.** Use these as backend candidates and pattern references. Agents SDK’s “agents as tools vs handoffs” distinction is a useful warning: toolizing agents is easy but can collapse peer identity unless the outer protocol preserves it.

## Patterns worth borrowing

1. **Capability-negotiated sessions as the adapter contract.** Adopt an ACP/LSP/MCP-style `initialize` handshake for every backend adapter with `implementationInfo`, protocol version, model/effort support, tool-telemetry fidelity, permission modes, session persistence support, and cancellation support. Make `session/new`, `session/load`, `session/close`, and `turn/cancel` explicit even when a backend maps them lossy.

2. **Typed progress notifications with durable event ids.** Borrow ACP `session/update`, A2A task events, and LSP work-done progress, but normalize them into B9 envelopes: `turn_started`, `agent_message_chunk`, `plan_update`, `tool_event`, `artifact_event`, `turn_progress`, `turn_completed`, `turn_failed`, `visibility_degraded`. Every event needs `agent`, `backend`, `task_id`, `turn_id`, `seq`, timestamp, severity, short summary, payload, and raw-backend reference.

3. **Separate human-visible summaries from full-fidelity logs.** MCP/CrewAI/OpenHands show that full event streams get noisy. Use the B9 four-channel split: append-only event log always; stderr for forensics; task `activeForm`/metadata for “what now”; mailbox only for warnings, errors, DMs, permissions, and rate-limited checkpoints.

4. **Manifests for identity, trust, and capability discovery.** Borrow A2A Agent Cards and AGNTCY OASF records. Each backend adapter should declare not just model name, but whether host tools are visible, whether tool results are complete, whether cancellation is safe, what modes exist (`trusted/default/plan`), whether sessions survive restarts, and what identity the lead should display.

## Patterns to avoid

1. **Final-result polling as the primary protocol.** Aider/headless CLIs and some REST run APIs are easy to wrap, but they reproduce the exact B9 failure: the lead sees silence until a final summary or timeout. Final-result backends are acceptable only with explicit `visibility_degraded` events, artifact sampling, and timeout/checkpoint policy.

2. **Framework-internal abstractions masquerading as an interop protocol.** LangGraph, CrewAI, AutoGen AgentChat, Swarm, and Agents SDK are useful execution frameworks, but their graph/team/handoff objects are not a stable cross-backend wire contract. Our user surface should not expose per-framework terms; adapters should map them to a small backend-neutral lifecycle and event vocabulary.

## Source map

- ACP: <https://agentclientprotocol.com/get-started/introduction>, <https://agentclientprotocol.com/protocol/overview>, <https://agentclientprotocol.com/protocol/prompt-turn>, <https://github.com/agentclientprotocol/agent-client-protocol>
- MCP: <https://modelcontextprotocol.io/specification/draft/basic>, <https://github.com/modelcontextprotocol/modelcontextprotocol>
- A2A: <https://a2a-protocol.org/latest/specification/>, <https://github.com/a2aproject/A2A>
- AGNTCY: <https://docs.agntcy.org/index.html>, <https://docs.agntcy.org/dir/overview/>, <https://github.com/agntcy/acp-spec>
- LangGraph: <https://docs.langchain.com/oss/python/langgraph/overview>, <https://docs.langchain.com/oss/python/langgraph/streaming>, <https://github.com/langchain-ai/langgraph>
- CrewAI: <https://docs.crewai.com/concepts/agents>, <https://docs.crewai.com/concepts/tasks>, <https://docs.crewai.com/concepts/crews>, <https://docs.crewai.com/en/concepts/event-listener>, <https://github.com/crewAIInc/crewAI>
- AutoGen: <https://microsoft.github.io/autogen/dev/user-guide/core-user-guide/core-concepts/agent-identity-and-lifecycle.html>, <https://microsoft.github.io/autogen/dev/user-guide/core-user-guide/framework/distributed-agent-runtime.html>, <https://github.com/microsoft/autogen>
- Swarm / OpenAI Agents SDK: <https://github.com/openai/swarm>, <https://github.com/openai/openai-agents-python>
- OpenHands: <https://github.com/OpenHands/OpenHands>, <https://github.com/OpenHands/software-agent-sdk>, <https://docs.openhands.dev/sdk>
- Aider: <https://github.com/Aider-AI/aider>
- Cline: <https://github.com/cline/cline>, <https://github.com/cline/cline/wiki/Installing-Git-for-Checkpoints>
- Continue: <https://github.com/continuedev/continue>, <https://github.com/continuedev/continue/tree/main/extensions/cli>
- Cursor: <https://docs.cursor.com/en/chat/agent>, <https://www.cursor.com/en>
- JSON-RPC/LSP: <https://www.jsonrpc.org/specification>, <https://microsoft.github.io/language-server-protocol/>, <https://github.com/microsoft/language-server-protocol/blob/gh-pages/_specifications/lsp/3.17/specification.md>
