<div align="center">

<img src="assets/logo/wordmark.svg" alt="claude-anyteam" width="420" />

### Native Claude Code teammates, any LLM.

**Codex today.** Gemini, Kimi, GLM, DeepSeek next — on the same team-native architecture.

[![License](https://img.shields.io/badge/license-MIT-000?style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12%2B-3776AB?style=flat-square&logo=python&logoColor=white)](pyproject.toml)
[![Node](https://img.shields.io/badge/node-%E2%89%A518-339933?style=flat-square&logo=node.js&logoColor=white)](npm/package.json)
[![Codex](https://img.shields.io/badge/codex-supported%20today-10a37f?style=flat-square&logo=openai&logoColor=white)](#supported-today--coming-next)
[![Tests](https://img.shields.io/badge/tests-202%20passing-22c55e?style=flat-square)](tests)

[**Quickstart**](#quickstart) · [**Architecture**](docs/architecture.md) · [**Roadmap**](docs/roadmap.md)

<img src="assets/diagrams/architecture.png" alt="claude-anyteam architecture" width="820" />

</div>

---

## What it is

Claude Code's [Agent Teams](https://code.claude.com/docs/en/agent-teams) feature is built for multi-agent collaboration — but every teammate is a Claude instance. **claude-anyteam** makes it possible for *any* external model to join the same team, with the same native UX, without wrapping it inside a Claude LLM.

Your Claude Code session orchestrates. External models execute. No chat-wrapper overhead. No "Claude pretending to be Codex." Real models, real teammates.

## Quickstart

```bash
npx --yes claude-anyteam
```

That's the entire install. The installer:

- Detects `python3` and installs `uv` if missing (non-interactive, no shell profile edits)
- Installs the `claude-anyteam` Python tool via `uv tool install`
- Runs `claude-anyteam install` (verifies tmux/psmux, writes `~/.claude/settings.json` + `~/.claude.json`, records install-state for symmetric uninstall)

Restart Claude Code, enable Agent Teams mode, and create a teammate named `codex-<anything>`:

```
codex-alice     → routed to claude-anyteam + Codex
codex-reviewer  → routed to claude-anyteam + Codex
alice           → native Claude (unchanged)
```

Codex-prefixed names appear in your TUI presence line exactly like native teammates. Single-terminal mode or tmux — both work.

## Why it feels native

<table>
<tr>
<td width="50%">

**Real teammate protocol**

Not a chat wrapper. The adapter speaks Claude Code's agent-team protocol directly: mailbox I/O, atomic task claims, idle notifications, shutdown lifecycle. A Codex teammate is functionally indistinguishable from a native Claude teammate.

</td>
<td width="50%">

**Mid-task reactivity**

When a peer messages a working teammate, the adapter injects the message mid-turn via Codex's `turn/steer` App Server call. Codex reshapes the in-flight turn instead of discarding it. v7.1.

</td>
</tr>
<tr>
<td width="50%">

**Cross-task memory**

Each new task forks from the previous task's Codex thread via `thread/fork`. The teammate carries its own conversational context forward across the team's task list. v7.3.

</td>
<td width="50%">

**Battle-tested parity**

202 passing tests. Ten parity bugs caught by a live 4-teammate hunt (mixed Claude + Codex) and fixed. Zero accepted limitations on the protocol layer.

</td>
</tr>
</table>

## Supported today / Coming next

| Supported today | Coming next |
|---|---|
| ✅ Codex via `codex-cli` (0.120+) | ⏳ Gemini CLI adapter |
| ✅ gpt-5.5 / gpt-5.4 / gpt-5.3-codex, configurable effort | ⏳ Kimi adapter |
| ✅ App Server mode (mid-task steer) | ⏳ GLM adapter |
| ✅ Fresh-exec mode (`codex exec resume`) | ⏳ DeepSeek adapter |
| ✅ Claude Code hook + TUI presence | ⏳ Pluggable backend routing |
| ✅ Opt-in structured plan mode | ⏳ Generic CLI adapter template |

Codex is shipping. Everything in "coming next" is on the same architectural surface — each new model is a new adapter binary + one line in the spawn shim's routing table. See [docs/roadmap.md](docs/roadmap.md).

## Requirements

- Python 3.12+
- Node 18+ (for the npm installer; not required at runtime)
- OpenAI Codex CLI 0.120+ on PATH
- Claude Code 2.1+ with Agent Teams mode
- Terminal multiplexer on PATH (tmux or psmux) — see [configuration.md](docs/configuration.md#teammate-display-mode)

## Docs

- [Install](docs/install.md) — how the installer wires Claude Code, alternative install methods, headless launches
- [Architecture](docs/architecture.md) — how the adapter integrates with Claude Code's team protocol
- [Roadmap](docs/roadmap.md) — supported today vs coming next, contribution pointers
- [Configuration](docs/configuration.md) — CLI flags, env vars, advanced modes

## License

MIT
