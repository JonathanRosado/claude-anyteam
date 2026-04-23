# Gemini CLI official research

_As of April 23, 2026. Scope: official Gemini CLI / Gemini API / Google SDK documentation only._

## Executive summary

For a `claude-anyteam` adapter, the official Gemini stack now covers the main building blocks:

- **Headless subprocess mode:** yes, via `gemini -p ...` or any non-TTY run, with `--output-format json` or `--output-format stream-json`.
- **MCP loading:** yes, via `mcpServers` in `settings.json` or `gemini mcp add ...`.
- **Session resume:** yes, via `--resume`, `--list-sessions`, and project-scoped session storage.
- **Long-lived programmatic mode:** **partially** yes, via **ACP mode** (JSON-RPC 2.0 over stdio), but not as a documented HTTP app server equivalent.
- **Schema-constrained output:** **not on the CLI** in Codex-style `--output-schema` form; use the Gemini API / `google-genai` SDK structured-output features instead.

---

## 1) Headless invocation and structured CLI output

Official headless mode is documented as a programmatic interface. It triggers when Gemini runs in a **non-TTY** environment or when you pass a prompt with `-p/--prompt`.

### What to run

- Simple one-shot text:
  - `gemini -p "Explain this repo"`
- Single JSON envelope:
  - `gemini -p "Explain this repo" --output-format json`
- Streaming JSONL events:
  - `gemini -p "Run tests and deploy" --output-format stream-json`

### What the output looks like

- `--output-format json` returns one object with:
  - `response`
  - `stats`
  - optional `error`
- `--output-format stream-json` returns newline-delimited events:
  - `init`
  - `message`
  - `tool_use`
  - `tool_result`
  - `error`
  - `result`

### Adapter notes

- There is **no documented `--json` flag**; the documented interface is `--output-format json`.
- For deterministic automation, use `-p` even though one reference page still calls it “deprecated”; the current official automation docs still use it throughout.
- `output.format` in `settings.json` supports `text` and `json`; the **`stream-json` mode is documented on the CLI flag**, not as a persistent settings value.
- Documented exit codes:
  - `0` success
  - `1` general/API failure
  - `42` input error
  - `53` turn limit exceeded

### Sources

- https://geminicli.com/docs/cli/headless/
- https://geminicli.com/docs/cli/tutorials/automation/
- https://geminicli.com/docs/cli/cli-reference/
- https://geminicli.com/docs/reference/configuration/
- https://github.com/google-gemini/gemini-cli

---

## 2) MCP server loading and config format

Gemini CLI officially supports MCP server loading.

### Supported configuration surfaces

- Persistent JSON config:
  - `~/.gemini/settings.json`
  - `.gemini/settings.json`
- CLI helpers:
  - `gemini mcp add`
  - `gemini mcp remove`
  - `gemini mcp list`

### `mcpServers` format

Each server lives under `mcpServers.<name>` and can use:

- `command`
- `args`
- `env`
- `cwd`
- `url` (SSE)
- `httpUrl` (streamable HTTP)
- `headers`
- `timeout`
- `trust`

Gemini CLI docs also expose:

- `--allowed-mcp-server-names`
- session/user/project scope controls via `gemini mcp ...`
- persistent enable/disable state for servers

### Behavior details that matter for an adapter

- MCP tools are renamed to fully qualified names like `mcp_{server}_{tool}`.
- Official docs explicitly warn **not** to use underscores in server aliases.
- Successful MCP connections are kept **persistent**.
- The docs note Gemini may strip some MCP schema properties for compatibility.

### Adapter notes

- This is strong enough to mirror the Codex-style “inject a wrapper MCP server” pattern.
- Config-driven stdio MCP is the closest match to the current `wrapper_server.py` approach.

### Sources

- https://geminicli.com/docs/tools/mcp-server/
- https://geminicli.com/docs/cli/cli-reference/
- https://geminicli.com/docs/reference/configuration/
- https://github.com/google-gemini/gemini-cli/blob/main/docs/tools/mcp-server.md

---

## 3) Schema-constrained output: CLI vs SDK

### CLI

Official CLI docs expose:

- plain text output
- JSON envelope output
- streaming JSON event output

They do **not** document a Codex-like `--output-schema` flag or any built-in CLI-side schema validator for the model’s final answer.

### Official Google path for real schema constraints

Official Gemini API / SDK docs support structured output through generation config:

- `response_mime_type: application/json`
- `response_json_schema`
- `response_schema`

That is documented in:

- Gemini API structured outputs docs
- Python `google-genai`
- JS `@google/genai`

### Adapter conclusion

- **CLI-only path:** you can parse Gemini’s outer JSON/JSONL protocol, but you must validate the model payload yourself.
- **SDK path:** use `google-genai` if `claude-anyteam` needs true schema-constrained model output comparable to `codex exec --output-schema`.

### Sources

- https://geminicli.com/docs/cli/headless/
- https://geminicli.com/docs/reference/configuration/
- https://ai.google.dev/gemini-api/docs/structured-output
- https://googleapis.github.io/python-genai/
- https://googleapis.github.io/js-genai/release_docs/interfaces/types.GenerateContentConfig.html

---

## 4) Session continuity across invocations

Official session continuity is now well documented.

### What exists

- Automatic session saving
- `--resume` / `-r`
- `--list-sessions`
- `--delete-session`
- interactive `/resume`
- manual named checkpoints via `/resume save <name>`

### Storage model

- Sessions are stored at:
  - `~/.gemini/tmp/<project_hash>/chats/`
- Sessions are **project-specific**.

### Resume options

- latest:
  - `gemini --resume`
- by numeric index:
  - `gemini --resume 1`
- by UUID:
  - `gemini --resume <uuid>`

### Separate feature: checkpointing

Checkpointing is a different feature from session history:

- disabled by default
- saves a shadow Git snapshot plus conversation/tool context
- restore with `/restore`

### Adapter notes

- For a subprocess adapter, `--resume latest` and explicit session IDs are sufficient to model “continue previous thread”.
- Session state is documented; this is no longer an undocumented workaround.

### Sources

- https://geminicli.com/docs/cli/session-management/
- https://geminicli.com/docs/cli/checkpointing/
- https://geminicli.com/docs/cli/cli-reference/
- https://geminicli.com/docs/reference/configuration/

---

## 5) Authentication that works in headless / subprocess mode

Official auth docs are explicit about headless behavior.

### Headless rule

- If cached auth already exists, headless mode reuses it.
- If not, official docs say you must configure auth with environment variables.

### Environment variables officially documented

#### Gemini Developer API

- `GEMINI_API_KEY`

#### Vertex AI / Google Cloud

- project selection:
  - `GOOGLE_CLOUD_PROJECT`
  - fallback: `GOOGLE_CLOUD_PROJECT_ID`
- location:
  - `GOOGLE_CLOUD_LOCATION`
- credentials:
  - ADC via `gcloud auth application-default login`
  - or `GOOGLE_APPLICATION_CREDENTIALS`
  - or `GOOGLE_API_KEY`

### Important official caveats

- For Vertex ADC or service-account JSON flows, the docs say to **unset**
  - `GOOGLE_API_KEY`
  - `GEMINI_API_KEY`
- Browser-based “Sign in with Google” is convenient for local users, but it is not the right first-run bootstrap for CI/subprocess automation.
- Cloud Shell and some Compute Engine environments can authenticate automatically.

### Adapter notes

- For reliable non-interactive subprocess use, the cleanest official choices are:
  1. `GEMINI_API_KEY`
  2. Vertex AI env vars + `GOOGLE_APPLICATION_CREDENTIALS`
  3. Vertex AI env vars + ADC

### Sources

- https://geminicli.com/docs/get-started/authentication/
- https://geminicli.com/docs/cli/headless/

---

## 6) Available Gemini 2.x / 3.x model slugs

The official docs expose both **aliases** and **concrete model IDs**.

### Stable user-facing aliases

- `auto`
- `pro`
- `flash`
- `flash-lite`

### Clearly documented concrete slugs

- `gemini-2.5-pro`
- `gemini-2.5-flash`
- `gemini-2.5-flash-lite`
- `gemini-3-pro-preview`
- `gemini-3-flash-preview`
- `gemini-3.1-pro-preview`

### Additional official config-default slugs visible in the configuration reference

- `gemini-3.1-flash-lite-preview`
- `gemini-3.1-pro-preview-customtools`

### Availability caveats

- Preview access is feature-gated.
- The `/model` page says manual selection can pick “any available model”.
- The Gemini 3 docs explicitly say `gemini-3.1-pro-preview` only appears if the account has access.
- `gemini-3.1-pro-preview-customtools` appears in official config defaults but looks hidden / internal-facing rather than a normal public target.

### Adapter notes

- Safe initial adapter support should accept any user-supplied model string, but treat these as the currently documented core set:
  - `gemini-2.5-pro`
  - `gemini-2.5-flash`
  - `gemini-2.5-flash-lite`
  - `gemini-3-pro-preview`
  - `gemini-3-flash-preview`
  - `gemini-3.1-pro-preview`

### Sources

- https://geminicli.com/docs/cli/model/
- https://geminicli.com/docs/get-started/gemini-3/
- https://geminicli.com/docs/cli/cli-reference/
- https://geminicli.com/docs/reference/configuration/

---

## 7) Official SDK alternatives: `google-genai` and Google ADK

## `google-genai` (recommended when schema control matters)

Google’s official low-level SDKs are:

- Python: `google-genai`
- TypeScript/JavaScript: `@google/genai`

Why they matter:

- direct model invocation
- structured output / JSON schema support
- streaming APIs
- easier auth wiring than shelling out when you need precise request control

The JS SDK README explicitly says older packages like `@google/generative_language` and `@google-cloud/vertexai` are older iterations and do **not** receive new Gemini 2.0+ features.

## Google ADK

Google’s official higher-level agent framework is the **Agent Development Kit (ADK)**.

Official docs position ADK as:

- an agent framework for building, evaluating, and deploying agents
- broader than the CLI
- available across multiple language stacks
- flexible about model backends

ADK’s model docs show support across:

- Python
- TypeScript
- Go
- Java

### Adapter implication

- If `claude-anyteam` wants a **CLI-compatible subprocess adapter**, Gemini CLI is still the closest fit.
- If it wants **strong schema guarantees, tighter request control, or in-process integration**, `google-genai` is the better official path.
- If it wants a **full agent framework**, ADK is the official strategic platform, but it is a bigger architectural jump than a CLI adapter.

### Sources

- https://github.com/googleapis/python-genai
- https://googleapis.github.io/python-genai/
- https://github.com/googleapis/js-genai
- https://googleapis.github.io/js-genai/release_docs/interfaces/types.GenerateContentConfig.html
- https://adk.dev/get-started/about/
- https://adk.dev/agents/models/

---

## 8) Long-lived JSON-RPC server mode

Officially, Gemini CLI does have a long-lived programmatic mode: **ACP mode**.

### What is documented

- ACP mode is for programmatic control and IDE integrations.
- Transport: **JSON-RPC 2.0 over stdio**
- Start command on the ACP page:
  - `gemini --acp`

### Core documented methods

- `initialize`
- `authenticate`
- `newSession`
- `loadSession`
- `prompt`
- `cancel`

The ACP docs also document:

- session control
- file system proxying
- MCP-based extension from the client side during initialization

### Important doc mismatch

There is an official-doc inconsistency:

- ACP page / configuration reference describe `--acp`
- current cheatsheet still shows `--experimental-acp`

That should be re-verified against the actual binary during implementation, but the **existence of ACP mode itself is officially documented**.

### Adapter conclusion

- Gemini has a documented long-lived machine interface.
- It is **not** a documented Codex-style HTTP app server.
- The closest official equivalent is **stdio JSON-RPC ACP mode**, which is good enough to investigate as a `codex app-server` alternative but not a drop-in match.

### Sources

- https://geminicli.com/docs/cli/acp-mode/
- https://geminicli.com/docs/cli/cli-reference/
- https://geminicli.com/docs/reference/configuration/

---

## Compatibility scorecard

| Capability | Rating | Notes |
| --- | --- | --- |
| 1. Headless subprocess invocation | **SUPPORTED** | Official headless mode, `-p`, non-TTY behavior, `json` and `stream-json` output, exit codes all documented. |
| 2. MCP server loading | **SUPPORTED** | `mcpServers` config, stdio/SSE/HTTP transports, `gemini mcp add/remove/list`, allow-listing, persistent connections documented. |
| 3. Schema-constrained final output on CLI | **PARTIAL** | CLI exposes JSON/JSONL wrappers, but no documented `--output-schema` equivalent. Real schema control exists in Gemini API / `google-genai`. |
| 4. Session continuity across invocations | **SUPPORTED** | Automatic save, `--resume`, session browser, manual checkpoints, retention settings documented. |
| 5. Headless auth wiring | **SUPPORTED** | Official env-var guidance exists for `GEMINI_API_KEY`, Vertex AI envs, ADC, service-account JSON. |
| 6. Gemini 2.x / 3.x model slugs | **PARTIAL** | Core slugs and aliases are documented, but some concrete 3.1 variants are preview/hidden/gated and availability is account-dependent. |
| 7. Official SDK alternatives | **SUPPORTED** | `google-genai` and Google ADK are both official, current, and documented. |
| 8. Long-lived JSON-RPC server mode | **PARTIAL** | ACP mode is officially documented and long-lived, but it is stdio JSON-RPC, not an HTTP app-server equivalent. |

---

## Recommended implementation takeaways for `claude-anyteam`

1. **Best CLI subprocess starting point:** `gemini -p <prompt> --output-format stream-json`
2. **Best MCP strategy:** inject a stdio MCP wrapper through `mcpServers` config
3. **Best resume strategy:** use documented Gemini sessions (`--resume`)
4. **Best auth for CI/headless:** `GEMINI_API_KEY` or Vertex AI env vars + service account / ADC
5. **Best path if schema validation is mandatory:** do not rely on CLI alone; use `google-genai`
6. **Best long-lived alternative to Codex app-server:** evaluate ACP mode, but expect stdio JSON-RPC rather than HTTP JSON-RPC
