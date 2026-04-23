# Roadmap

## Shipped

**Codex adapter** — OpenAI Codex CLI (0.120+) as a first-class Claude Code teammate. gpt-5.x models, configurable reasoning effort. App Server mid-task `turn/steer` and `thread/fork` cross-task memory. Fresh-exec `codex exec resume` as opt-out. 198 passing tests including a live battle-test against native Claude agents.

**Install surfaces** — npm (`npx --yes --package claude-anyteam claude-anyteam-setup`), direct (`uv tool install`), and Claude Code plugin (marketplace install + self-healing SessionStart hook). All three write the same settings and interop.

**TUI parity** — Codex teammates appear in Claude Code's Agent Teams presence line exactly like native teammates. Works in tmux and single-terminal modes. Peer messages, task claiming, idle signaling, shutdown lifecycle all behave identically.

**Plan mode** — opt-in structured plan approval with JSON-schema-validated plan artifacts.

## Coming next

These are the adapters planned on the same architecture. Each one is a Python adapter module + a line in the spawn shim's routing table.

| Adapter | Model(s) | Backend CLI | Status |
|---|---|---|---|
| **Gemini** | Gemini 2.x family | `gemini` | Planned — targeting same App Server semantics via Google's CLI protocol |
| **Kimi** | Kimi K2 family | Moonshot's CLI or API-direct | Planned |
| **GLM** | GLM-4.x family | Zhipu's CLI | Planned |
| **DeepSeek** | DeepSeek V3 / R1 | DeepSeek's CLI or API-direct | Planned |
| **Generic API adapter** | Any OpenAI-compatible endpoint | Direct HTTP | Planned — covers OpenRouter, LM Studio, local vLLM, etc. |

## Contributing a new adapter

The shared protocol is implemented once. A new adapter needs:

1. A new module under `src/claude_anyteam/backends/<name>/` implementing:
   - `async def run_task(task, context) -> TaskResult`
   - `async def handle_prose(message, state) -> str`
2. A shim routing rule: `<name>-*` → new adapter binary
3. Adapter-specific install flags (model id, api key / oauth, effort)
4. Regression tests under `tests/backends/<name>/`

Protocol semantics (inbox polling, task claiming, mailbox I/O, lifecycle) are inherited from the shared base. No re-implementing the team protocol.

If you're thinking of adding an adapter, open an issue first — we can scope the minimum viable integration and flag any protocol surface we want to generalize before committing.

## Deferred / out of scope

- **Custom rendering in the Claude Code TUI beyond presence line.** Claude Code's TUI renders agent types uniformly; we don't fight that.
- **Multi-team coordination.** One adapter instance serves one team. Multi-team overlays belong in a higher-level orchestration tool, not here.
- **LLM wrapping.** We don't wrap external models inside a Claude instance. That's the anti-pattern this project exists to avoid.

## Longer term

- Telemetry (opt-in) to understand which adapters are popular
- Shared session memory across a team (currently each teammate has its own thread lineage)
- "Hot-swap model" mid-team for A/B comparisons on the same task list
