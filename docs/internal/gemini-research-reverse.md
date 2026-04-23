# Gemini CLI reverse/community research

Date: 2026-04-23

Scope note: this report intentionally focuses on community-discovered behavior, source-level findings, bug history, undocumented/under-documented flags, and ecosystem workarounds. It is meant to complement `gemini-research-official.md`, not duplicate it.

---

## 1) Community workarounds for CLI gaps

### Finding 1.1 — `gemini --prompt ... --yolo` is the de facto “background agent” workaround
- **Finding:** Before any native background-agent story is mature, the community is already using headless runs with `--prompt` + `--yolo` for unattended loops, often wrapped in skills or shell automation.
- **Why it matters:** For a Codex-like subprocess adapter, this means Gemini CLI is already being treated as a one-shot worker by the community, even when the product messaging emphasizes interactive use.
- **Confidence:** **HIGH**
- **Sources:**
  - https://github.com/supratikpm/gemini-autoresearch
  - https://github.com/addyosmani/gemini-cli-tips

### Finding 1.2 — Feature/docs drift is real; wrappers should version-gate headless behavior
- **Finding:** In September 2025, users reported that docs advertised `--output-format json`, but stable `0.5.4` rejected `--output-format` entirely.
- **Why it matters:** Any adapter that assumes “docs == installed behavior” will be brittle. Capability probing or minimum-version checks are safer than static assumptions.
- **Confidence:** **HIGH**
- **Sources:**
  - https://github.com/google-gemini/gemini-cli/issues/9009
  - https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/headless.md

### Finding 1.3 — For container/headless OAuth, the community workaround was `--network host` plus `-d`
- **Finding:** In containerized environments, users reported that OAuth only became workable by:
  1. starting the container with `--network host`, and
  2. running `gemini -d` so the auth URL became visible for manual copy/paste.
- **Why it matters:** This is a strong signal that browser OAuth is still a poor default for unattended subprocesses.
- **Confidence:** **HIGH**
- **Sources:**
  - https://github.com/google-gemini/gemini-cli/issues/2515

### Finding 1.4 — Disabling MCP/extensions is a common community triage step for “hangs” on preview models
- **Finding:** Community reports suggest some “this is taking longer” / 429 / no-capacity problems are worse when MCP/tool-use is involved, especially on preview models.
- **Why it matters:** For automation, the safest baseline is probably “minimal extensions, explicit allowlist, stable model” rather than “load everything and hope.”
- **Confidence:** **MEDIUM**
- **Sources:**
  - https://www.reddit.com/r/GeminiCLI/comments/1rtt6w0/getting_stuck_at_this_is_taking_a_bit_longer_with/
  - https://www.reddit.com/r/GeminiAI/comments/1rcqht0/gemini_cli_unusable_constant_high_demand_and_no/

---

## 2) Open source? What the source reveals

### Finding 2.1 — Gemini CLI is genuinely open source, with unusually fast release churn
- **Finding:** The repo is Apache-2.0 and public; as of April 23, 2026 GitHub showed **465 releases** with **v0.39.0** marked latest.
- **Why it matters:** Reverse-engineering is practical because the implementation is inspectable, but wrappers should expect rapid CLI churn.
- **Confidence:** **HIGH**
- **Sources:**
  - https://github.com/google-gemini/gemini-cli

### Finding 2.2 — Headless `stream-json` has a real event taxonomy, not just blob output
- **Finding:** The headless reference documents newline-delimited events: `init`, `message`, `tool_use`, `tool_result`, `error`, `result`.
- **Why it matters:** This is enough structure to build a subprocess adapter closer to `codex exec --jsonl`, but consumers should still expect evolution across versions.
- **Confidence:** **HIGH**
- **Sources:**
  - https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/headless.md

### Finding 2.3 — Source config explicitly disables `ask_user` in headless and ACP mode
- **Finding:** In `packages/cli/src/config/config.ts`, Gemini excludes the internal ask-user tool when running non-interactively or in ACP mode.
- **Why it matters:** This is a crucial hidden behavior for adapter design: in headless/ACP, Gemini expects the outer system to own approval UX rather than surfacing a conversational “ask user” hop.
- **Confidence:** **HIGH**
- **Sources:**
  - https://github.com/google-gemini/gemini-cli/blob/main/packages/cli/src/config/config.ts
  - https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/acp-mode.md

### Finding 2.4 — MCP connections are long-lived and dynamically refreshable
- **Finding:** `mcp-client.ts` shows persistent client objects, notification handlers for tool/resource/prompt list changes, and progress-token routing.
- **Why it matters:** Gemini’s long-lived integration story is stronger than “spawn process, dump tools once.” An ACP or daemon-style integration can benefit from dynamic MCP refresh and progress events.
- **Confidence:** **HIGH**
- **Sources:**
  - https://raw.githubusercontent.com/google-gemini/gemini-cli/main/packages/core/src/tools/mcp-client.ts
  - https://github.com/google-gemini/gemini-cli/blob/main/docs/tools/mcp-server.md

### Finding 2.5 — MCP schema handling is strict enough to reject otherwise-valid servers
- **Finding:** Community debugging of issue `#13053` points to `mcpToTool()` / MCP schema conversion failing on `$defs` references before exclude filters are applied, disconnecting the whole server.
- **Why it matters:** One bad tool schema can poison the entire MCP server registration path. A wrapper may need a sanitizing proxy or a preflight transform step.
- **Confidence:** **HIGH**
- **Sources:**
  - https://github.com/google-gemini/gemini-cli/issues/13053
  - https://github.com/google-gemini/gemini-cli/blob/main/docs/tools/mcp-server.md

---

## 3) ACP mode / daemon mode reality

### Finding 3.1 — Gemini has a real stdio JSON-RPC daemon mode: ACP
- **Finding:** `--acp` starts Gemini CLI in Agent Client Protocol mode over stdio using JSON-RPC 2.0.
- **Why it matters:** This is the closest Gemini analogue to `codex app-server`, though the protocol is ACP rather than a Gemini-specific HTTP app server.
- **Confidence:** **HIGH**
- **Sources:**
  - https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/acp-mode.md

### Finding 3.2 — Flag churn matters: older integrations refer to `--experimental-acp`
- **Finding:** Older issue/help text and community integrations refer to `--experimental-acp`; current docs use `--acp`.
- **Why it matters:** Version-sensitive launch code is required if supporting older installations or community adapters.
- **Confidence:** **HIGH**
- **Sources:**
  - https://github.com/google-gemini/gemini-cli/issues/9009
  - https://github.com/google-gemini/gemini-cli/discussions/7540
  - https://github.com/google-gemini/gemini-cli/blob/main/packages/cli/src/config/config.ts

### Finding 3.3 — ACP seems to inherit normal CLI config rather than introducing a distinct server profile
- **Finding:** Current config code routes ACP through the normal config stack: model selection, MCP allowlists, excluded tools, folder trust, etc.
- **Why it matters:** An ACP integration should think of Gemini as “CLI with JSON-RPC front-end,” not as a separately-configured daemon product.
- **Confidence:** **HIGH**
- **Sources:**
  - https://github.com/google-gemini/gemini-cli/blob/main/packages/cli/src/config/config.ts
  - https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/acp-mode.md

### Finding 3.4 — There is no evidence of a built-in HTTP daemon equivalent to Codex App Server
- **Finding:** All surfaced programmatic modes point to headless one-shot or ACP stdio. I did not find an official Gemini-native long-lived HTTP app server equivalent.
- **Why it matters:** If claude-anyteam wants a persistent Gemini transport, ACP is the obvious first-class option; otherwise build your own wrapper around headless mode.
- **Confidence:** **MEDIUM**
- **Sources:**
  - https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/acp-mode.md
  - https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/headless.md

---

## 4) MCP env sanitization quirks

### Finding 4.1 — Gemini aggressively strips sensitive env vars from spawned MCP servers
- **Finding:** Gemini redacts sensitive inherited environment variables before starting third-party MCP servers, including Gemini/Google keys and broad `*TOKEN*`, `*SECRET*`, `*PASSWORD*`, `*KEY*`, `*AUTH*`, `*CREDENTIAL*` patterns.
- **Why it matters:** An MCP server will often “mysteriously” not see secrets that are present in the parent shell unless they are explicitly re-exposed.
- **Confidence:** **HIGH**
- **Sources:**
  - https://github.com/google-gemini/gemini-cli/blob/main/docs/tools/mcp-server.md
  - https://github.com/google-gemini/gemini-cli/blob/main/docs/reference/configuration.md

### Finding 4.2 — The sanctioned bypass is explicit `mcpServers.<name>.env`
- **Finding:** Gemini treats explicitly configured server env vars as trusted and does not redact them.
- **Why it matters:** For claude-anyteam, this is a major difference from Codex-style “just inherit process env”: Gemini wants opt-in re-export per server.
- **Confidence:** **HIGH**
- **Sources:**
  - https://github.com/google-gemini/gemini-cli/blob/main/docs/tools/mcp-server.md
  - https://github.com/github/github-mcp-server/blob/main/docs/installation-guides/install-gemini-cli.md

### Finding 4.3 — `.gemini/.env` is safer than project `.env` for Gemini-specific overrides
- **Finding:** Project `.env` loading excludes some vars like `DEBUG`/`DEBUG_MODE`, but `.gemini/.env` is never subject to that exclusion.
- **Why it matters:** This is a subtle but important operational quirk. Community debugging advice that says “put it in `.env`” is incomplete for Gemini CLI.
- **Confidence:** **HIGH**
- **Sources:**
  - https://github.com/google-gemini/gemini-cli/blob/main/docs/reference/configuration.md
  - https://google-gemini.github.io/gemini-cli/docs/cli/sandbox.html

### Finding 4.4 — Redaction also affects tool execution, not just MCP startup
- **Finding:** Config exposes `allowedEnvironmentVariables`, `blockedEnvironmentVariables`, and redaction toggles at CLI level.
- **Why it matters:** If a wrapper expects shell tools to inherit the full parent env, Gemini may silently redact pieces unless configured otherwise.
- **Confidence:** **HIGH**
- **Sources:**
  - https://github.com/google-gemini/gemini-cli/blob/main/docs/reference/configuration.md
  - https://github.com/google-gemini/gemini-cli/blob/main/packages/cli/src/config/config.ts

### Finding 4.5 — Windows `npx.cmd` MCP launch behavior appears noisy in practice
- **Finding:** Community reports suggest `npx.cmd`-launched MCP servers on Windows can cause visible console popups.
- **Why it matters:** For polished headless/IDE integrations, prefer real executables or `node path/to/server.js` over `npx.cmd` when possible.
- **Confidence:** **LOW**
- **Sources:**
  - https://www.reddit.com/r/google_antigravity/comments/1rmwaiq/opensource_fix_for_antigravitygemini_cli_on/

---

## 5) OpenAI-compatible endpoint options

### Finding 5.1 — Official Gemini CLI still does not natively support OpenAI as a backend
- **Finding:** A long-standing feature request asks for OpenAI backend/provider support; it is not presented as current built-in functionality.
- **Why it matters:** If claude-anyteam wants “Gemini CLI semantics, OpenAI endpoint underneath,” that is currently a fork/bridge story, not stock Gemini CLI.
- **Confidence:** **HIGH**
- **Sources:**
  - https://github.com/google-gemini/gemini-cli/issues/1605

### Finding 5.2 — There are already credible community bridges exposing Gemini as `/v1`
- **Finding:** Two notable projects:
  - `GewoonJaap/gemini-cli-openai` exposes OpenAI-style endpoints from Cloudflare Workers.
  - `Intelligent-Internet/gemini-cli-mcp-openai-bridge` exposes `/v1/chat/completions` and `/v1/models` locally while inheriting Gemini CLI auth/config.
- **Why it matters:** The ecosystem is actively filling this gap; there is demand, and bridge patterns already exist.
- **Confidence:** **HIGH**
- **Sources:**
  - https://github.com/GewoonJaap/gemini-cli-openai
  - https://github.com/Intelligent-Internet/gemini-cli-mcp-openai-bridge

### Finding 5.3 — There is also a fork that flips Gemini CLI to arbitrary OpenAI-compatible backends
- **Finding:** `IndenScale/open-gemini-cli` adds OpenAI-compatible env vars like `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL`.
- **Why it matters:** This shows the core agent loop is adaptable, but also that upstream does not currently want to own this compatibility layer.
- **Confidence:** **HIGH**
- **Sources:**
  - https://github.com/IndenScale/open-gemini-cli

### Finding 5.4 — Security posture degrades in many bridge projects
- **Finding:** The MCP/OpenAI bridge explicitly warns that tool confirmations are not preserved the same way and recommends containers for dangerous modes.
- **Why it matters:** These bridges are useful references, but poor default foundations for an upstream-quality adapter unless we reintroduce approval/sandbox controls ourselves.
- **Confidence:** **HIGH**
- **Sources:**
  - https://github.com/Intelligent-Internet/gemini-cli-mcp-openai-bridge

---

## 6) Known auth landmines in headless/subprocess mode

### Finding 6.1 — Browser OAuth remains a poor fit for headless/remote machines
- **Finding:** There are multiple reports of browser-based auth failing on SSH/headless systems because Gemini expects a local browser/callback.
- **Why it matters:** For unattended subprocess use, API key or Vertex auth looks materially safer than OAuth.
- **Confidence:** **HIGH**
- **Sources:**
  - https://github.com/google-gemini/gemini-cli/issues/1696
  - https://github.com/google-gemini/gemini-cli/issues/2515

### Finding 6.2 — Older versions had CWD-dependent OAuth credential reuse bugs
- **Finding:** `v0.1.16` reportedly only reused OAuth credentials reliably when run from `~/.gemini`, not arbitrary project directories.
- **Why it matters:** Historical but important: session/auth file handling has been fragile enough that wrappers should avoid assuming filesystem behavior is bug-free across versions.
- **Confidence:** **HIGH**
- **Sources:**
  - https://github.com/google-gemini/gemini-cli/issues/5474

### Finding 6.3 — Personal Google AI Pro + `oauth-personal` can still fail due to backend project binding
- **Finding:** In April 2026, a user documented a 403 path where backend `loadCodeAssist` returned a server-side `cloudaicompanionProject`, which Gemini then reused for subsequent calls.
- **Why it matters:** This is not just “bad local config”; some failures are server-side account-state problems that a subprocess wrapper cannot paper over.
- **Confidence:** **HIGH**
- **Sources:**
  - https://github.com/google-gemini/gemini-cli/issues/25189
  - https://github.com/google-gemini/gemini-cli/issues/16435
  - https://github.com/google-gemini/gemini-cli/issues/10110

### Finding 6.4 — API-key mode is the common workaround when OAuth gets weird
- **Finding:** In the April 2026 403 investigation, switching to API-key mode eliminated the OAuth-specific permission failure (though quota/rate-limit issues could still remain).
- **Why it matters:** For automation, “prefer explicit key/project auth” is supported by community debugging practice, not just abstract principle.
- **Confidence:** **HIGH**
- **Sources:**
  - https://github.com/google-gemini/gemini-cli/issues/25189

### Finding 6.5 — Headless auth problems can be compounded by env precedence
- **Finding:** A recent issue reports service-account/Vertex auth being undermined by other lingering env vars such as `GOOGLE_API_KEY`.
- **Why it matters:** The safest adapter startup is likely to sanitize or explicitly set auth env vars, not inherit an arbitrary developer shell.
- **Confidence:** **MEDIUM**
- **Sources:**
  - https://github.com/google-gemini/gemini-cli/issues/15823

---

## 7) Community adapters/plugins that bridge Gemini to other agent frameworks

### Finding 7.1 — Claude/Gemini orchestration via MCP is already a live community pattern
- **Finding:** `dnnyngyen/gemini-cli-orchestrator` is an MCP server meant to let Claude Code orchestrate Gemini for analysis-heavy or context-heavy tasks.
- **Why it matters:** The market is already treating Gemini CLI as a specialist subprocess/sidecar, which aligns with claude-anyteam’s roadmap.
- **Confidence:** **HIGH**
- **Sources:**
  - https://github.com/dnnyngyen/gemini-cli-orchestrator

### Finding 7.2 — There are direct MCP wrappers that expose Gemini CLI to other clients
- **Finding:** Examples include:
  - `ZainRizvi/gemini-cli-mcp`
  - `centminmod/gemini-cli-mcp-server`
- **Why it matters:** These projects are practical evidence that “Gemini as tool-provider / delegated worker” is already useful enough to justify wrappers.
- **Confidence:** **HIGH**
- **Sources:**
  - https://github.com/ZainRizvi/gemini-cli-mcp
  - https://github.com/centminmod/gemini-cli-mcp-server

### Finding 7.3 — Cross-agent launchers are emerging
- **Finding:** `mkXultra/ai-cli-mcp` advertises a single MCP surface that can launch Claude, Codex, and Gemini CLI agents.
- **Why it matters:** Gemini is already being normalized as one interchangeable coding-agent backend among many, which is strategically close to claude-anyteam’s positioning.
- **Confidence:** **MEDIUM**
- **Sources:**
  - https://github.com/mkXultra/claude-code-mcp/

### Finding 7.4 — Skills are being used as reusable agent behaviors, not just prompt templates
- **Finding:** `gemini-autoresearch` installs as a Gemini skill and turns headless Gemini into an overnight optimization loop.
- **Why it matters:** This suggests a richer extension surface than “send prompt, get text”; Gemini’s skills ecosystem can change how the subprocess behaves.
- **Confidence:** **HIGH**
- **Sources:**
  - https://github.com/supratikpm/gemini-autoresearch

---

## 8) Hooks system and other observability surfaces

### Finding 8.1 — Hooks intentionally mirror Claude Code’s JSON-over-stdin model
- **Finding:** The hook design/issues explicitly call out JSON-over-stdin contracts and Claude-compatibility semantics.
- **Why it matters:** For claude-anyteam, this means Gemini’s customization model is closer to Claude Code than it first appears.
- **Confidence:** **HIGH**
- **Sources:**
  - https://github.com/google-gemini/gemini-cli/issues/9070
  - https://github.com/google-gemini/gemini-cli/issues/11703
  - https://github.com/google-gemini/gemini-cli/blob/main/docs/hooks/writing-hooks.md

### Finding 8.2 — Gemini exposes more lifecycle hooks than simple pre/post tool events
- **Finding:** Current docs/config surface `BeforeTool`, `AfterTool`, `BeforeAgent`, `AfterAgent`, `Notification`, `SessionStart`, `SessionEnd`, `PreCompress`, `BeforeModel`, `AfterModel`, and `BeforeToolSelection`.
- **Why it matters:** This is a surprisingly rich observability/control layer; it can be used to inject policy, context, or logging around the agent loop.
- **Confidence:** **HIGH**
- **Sources:**
  - https://github.com/google-gemini/gemini-cli/blob/main/docs/reference/configuration.md
  - https://github.com/google-gemini/gemini-cli/issues/11703

### Finding 8.3 — Hook output discipline is strict: log to stderr, JSON only on stdout
- **Finding:** Hook docs explicitly warn authors to reserve stdout for final JSON and use stderr for logs.
- **Why it matters:** This is the kind of low-level behavioral contract an adapter must respect if it relies on hooks or expects stable machine output.
- **Confidence:** **HIGH**
- **Sources:**
  - https://github.com/google-gemini/gemini-cli/blob/main/docs/hooks/writing-hooks.md

### Finding 8.4 — There are at least three machine-readable observability surfaces
- **Finding:**
  1. `--output-format stream-json`
  2. telemetry / OTLP
  3. `--session-summary <path>` JSON output
- **Why it matters:** Gemini is not just parse-the-TUI anymore; there are multiple paths for wrapper-friendly instrumentation.
- **Confidence:** **HIGH**
- **Sources:**
  - https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/headless.md
  - https://github.com/google-gemini/gemini-cli/blob/main/docs/reference/configuration.md
  - https://github.com/google-gemini/gemini-cli/blob/main/docs/changelogs/index.md

### Finding 8.5 — Community tools exist because native logging still leaves gaps
- **Finding:** Projects like `ai-cli-log` capture full Gemini terminal sessions for later review/export.
- **Why it matters:** Even with JSON/telemetry, community demand still exists for transcript-grade logging, which may matter for reproducibility in multi-agent workflows.
- **Confidence:** **MEDIUM**
- **Sources:**
  - https://github.com/alingse/ai-cli-log

---

## Hidden Gems

1. **Headless mode has a structured JSONL event stream now**  
   - Not just final text. You can observe init, tool calls, tool results, errors, and final stats.  
   - **Confidence:** HIGH  
   - Source: https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/headless.md

2. **ACP is likely the cleanest long-lived integration point**  
   - If claude-anyteam wants a persistent Gemini worker, ACP is much closer to that goal than scraping TUI output.  
   - **Confidence:** HIGH  
   - Source: https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/acp-mode.md

3. **`.gemini/.env` is a tactical power move**  
   - Useful for Gemini-only auth/debug knobs without polluting project `.env`.  
   - **Confidence:** HIGH  
   - Sources:
     - https://github.com/google-gemini/gemini-cli/blob/main/docs/reference/configuration.md
     - https://google-gemini.github.io/gemini-cli/docs/cli/sandbox.html

4. **`--session-summary <path>` is underrated for automation accounting**  
   - Good fit for CI or agent-run accounting without parsing terminal logs.  
   - **Confidence:** HIGH  
   - Source: https://github.com/google-gemini/gemini-cli/blob/main/docs/changelogs/index.md

5. **MCP refresh/progress notifications are better than most CLI integrations imply**  
   - Source code shows dynamic updates, not just startup-time discovery.  
   - **Confidence:** HIGH  
   - Source: https://raw.githubusercontent.com/google-gemini/gemini-cli/main/packages/core/src/tools/mcp-client.ts

---

## Landmines

1. **Docs can get ahead of installed stable behavior**  
   - Example: `--output-format` documented before it worked in some stable builds.  
   - **Confidence:** HIGH  
   - Source: https://github.com/google-gemini/gemini-cli/issues/9009

2. **A single bad MCP schema can knock out the whole server**  
   - `$defs` / schema strictness is a real compatibility trap.  
   - **Confidence:** HIGH  
   - Source: https://github.com/google-gemini/gemini-cli/issues/13053

3. **OAuth is still the least reliable auth mode for unattended/headless use**  
   - Browser callback, random localhost ports, container weirdness, account-state bugs.  
   - **Confidence:** HIGH  
   - Sources:
     - https://github.com/google-gemini/gemini-cli/issues/2515
     - https://github.com/google-gemini/gemini-cli/issues/1696
     - https://github.com/google-gemini/gemini-cli/issues/25189

4. **Flag names and CLI affordances have churned**  
   - `--experimental-acp` vs `--acp` is the clearest example.  
   - **Confidence:** HIGH  
   - Sources:
     - https://github.com/google-gemini/gemini-cli/issues/9009
     - https://github.com/google-gemini/gemini-cli/discussions/7540

5. **Community bridges often weaken native approval/sandbox guarantees**  
   - Great references, risky defaults.  
   - **Confidence:** HIGH  
   - Source: https://github.com/Intelligent-Internet/gemini-cli-mcp-openai-bridge

6. **Tool-use + preview-model capacity appears fragile in the field**  
   - Especially when MCP extensions are enabled.  
   - **Confidence:** MEDIUM  
   - Sources:
     - https://www.reddit.com/r/GeminiCLI/comments/1rtt6w0/getting_stuck_at_this_is_taking_a_bit_longer_with/
     - https://www.reddit.com/r/GeminiAI/comments/1rcqht0/gemini_cli_unusable_constant_high_demand_and_no/

---

## Gap Analysis vs. the current Codex adapter pattern

| Area | Gemini CLI reality | Impact vs Codex adapter |
|---|---|---|
| One-shot subprocess mode | `--prompt` + `--output-format json/stream-json` exists, but historical version drift is real | Similar overall shape to `codex exec`; add version probing/minimum-version checks |
| Long-lived programmatic mode | ACP (`--acp`) over stdio JSON-RPC | Better analogue to `codex app-server` than expected, but protocol is different and stdio-based |
| Approval handling | Headless/ACP explicitly exclude `ask_user` | Outer wrapper must own approvals; do not expect the model to hand control back conversationally |
| MCP integration | Native MCP config/discovery is rich, but schema validation and env redaction are strict | Better built-in MCP story than Codex injection hacks, but more preprocessing/sanitization may be required |
| Env passing | Sensitive env vars stripped unless explicitly re-exported | More secure than naive inheritance, but more surprising and likely to break community MCP servers |
| Auth for subprocesses | OAuth is brittle in headless/containerized use; API-key/Vertex modes are more dependable | Strong reason to prefer explicit non-browser auth in any production adapter |
| OpenAI-compatible backend | No stock support; bridges/forks fill the gap | Not a reliable upstream dependency; only use as inspiration/reference |
| Observability | Stream JSON, hooks, telemetry, session-summary | Potentially stronger than Codex in instrumentation, but also more moving parts |
| Ecosystem posture | Community already wraps Gemini via MCP/OpenAI bridges and cross-agent orchestrators | Confirms market demand for claude-anyteam-style delegation/orchestration |

### Practical takeaways for claude-anyteam

1. **If building the simplest adapter first, use headless `stream-json`, but gate on version.**
2. **If building the best long-lived adapter, ACP looks like the serious path.**
3. **Prefer API key / Vertex auth over browser OAuth for unattended use.**
4. **Treat MCP env injection as explicit configuration, not inherited process state.**
5. **Consider an MCP schema-sanitizing layer if you want broad MCP compatibility.**

---

## Bottom line

Gemini CLI is more adapter-friendly than it first appears: it has a real JSONL headless mode, a real stdio JSON-RPC daemon mode (ACP), native MCP support, and a growing hooks/telemetry surface. The catch is reliability and churn: auth edge cases, version drift, strict MCP schema handling, and aggressive env sanitization are all sharp edges the community keeps rediscovering.

For claude-anyteam, the strongest reverse-side conclusion is: **Gemini is viable, but only if the adapter is opinionated about version checks, auth mode, MCP env handling, and approval ownership.**
