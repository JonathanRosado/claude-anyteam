# Gemini MCP shadow-tool research

Date: 2026-04-24/25  
Host Gemini CLI: `/usr/local/bin/gemini`, installed package `@google/gemini-cli`, ACP agent version reported as `0.39.0`.

## Executive summary

The clean path is **A: disable Gemini built-ins and replace them with anyteam MCP tools**.

Why:

- Gemini CLI has a supported built-in-tool allowlist: `tools.core` in `.gemini/settings.json`.
- Setting `"tools": {"core": []}` removes built-in tools from the model's available tool declarations, but **does not remove MCP tools**.
- MCP tools are always namespaced as `mcp_<serverName>_<toolName>`, so exact-name collision with built-ins is not possible. A server tool named `read_file` becomes `mcp_anyteam_read_file`, not `read_file`.
- Headless `--output-format stream-json` reliably emits MCP `tool_result.output` payloads. ACP reliably emits MCP result text in `session/update` `tool_call_update.content[*].content.text`.
- Previous ACP probes already showed built-in `run_shell_command` and `list_directory` completion updates with `content: []`; MCP completion updates included the payload. My fresh ACP probe reconfirmed the MCP side under `tools.core: []`.

Implementation should therefore provision a no-sandbox passthrough MCP server under alias `anyteam`, disable all built-ins with `tools.core: []`, and prepend system/developer instructions that the agent must use `mcp_anyteam_shell`, `mcp_anyteam_read_file`, etc. for local work.

## Built-in tool surface

Sources: installed Gemini docs under `/usr/local/lib/node_modules/@google/gemini-cli/bundle/docs/`, `gemini --help`, prior local empirical docs, and fresh headless/ACP probes.

`gemini --help` exposes no per-tool CLI flag. Relevant flags are `--acp`, `--approval-mode`, `--allowed-mcp-server-names`, deprecated `--allowed-tools`, `--include-directories`, `--sandbox`, and `--yolo`.

### Core execution and filesystem tools

| Tool name | Kind/display | Parameters | Semantics / notes |
|---|---:|---|---|
| `run_shell_command` | Execute / Shell | `command: string` required; `description?: string`; `dir_path?: string`; `is_background?: boolean` | Runs command via `bash -c` on Unix and `powershell.exe -NoProfile -Command` on Windows. Returns command, directory, stdout, stderr, exit code, background PIDs. Sets `GEMINI_CLI=1`. Confirmation normally required unless yolo/policy allows. |
| `read_file` | Read / ReadFile | `file_path: string` required; `offset?: number` zero-based line offset; `limit?: number` max lines | Reads text plus supported media/PDF files. Filesystem tool is rooted in workspace/current root for Gemini's safety model. |
| `write_file` | Edit / WriteFile | `file_path: string` required; `content: string` required | Creates or overwrites file. Confirmation normally required. |
| `list_directory` | Read / ReadFolder | `dir_path: string` required; `ignore?: string[]`; `file_filtering_options?: object` | Lists direct children of a directory, respecting ignore/filtering options. |
| `glob` | Search / FindFiles | `pattern: string` required; `path?: string`; `case_sensitive?: boolean`; `respect_git_ignore?: boolean` default true | Finds file paths matching glob, sorted newest first; ignores common nuisance dirs. |
| `grep_search` | Search / SearchText | `pattern: string` required regex; `path?: string`; `include?: string` glob filter | Searches file contents. Uses `git grep` where possible, otherwise grep/JS fallback. Legacy alias documented: `search_file_content`. |
| `replace` | Edit / Edit | `file_path: string`; `instruction: string`; `old_string: string`; `new_string: string`; `allow_multiple?: boolean` | Precise literal replacement. Defaults to requiring exactly one occurrence. Confirmation normally required. |
| `read_many_files` | Read | Exact schema not expanded in current file-system doc; manually triggered by `@path` syntax | Reads/concatenates multiple files/directories into context. Should be shadowed only if the agent needs `@`-equivalent bulk reads; otherwise `mcp_anyteam_read_file` + `mcp_anyteam_glob` is enough. |

### Web tools

| Tool name | Parameters | Semantics / notes |
|---|---|---|
| `web_fetch` | `prompt: string` required, containing up to 20 `http://`/`https://` URLs and processing instructions | Uses Gemini API URL context, falls back to local fetch, returns synthesized content with source attribution. Can access private/local network addresses; in plan mode requires confirmation. |
| `google_web_search` | `query: string` required | Uses Google Search grounding and returns a generated summary with source URIs/titles. |

### Interaction, memory, planning, task tools

These are not all required for filesystem/shell-output recovery, but they are built-ins visible in the tool reference and are affected by broad built-in disabling.

| Tool name | Parameters | Semantics / notes |
|---|---|---|
| `ask_user` | `questions: object[]` required. Each has `question`, `header`, optional `type` (`choice`, `text`, `yesno`), optional `options`, `multiSelect`, `placeholder` | Interactive clarification dialog. If we set `tools.core: []`, Gemini loses this built-in. ACP already has client-side permission/interaction surfaces, so do not rely on Gemini's `ask_user` unless explicitly re-enabled. |
| `write_todos` | `todos: {description: string, status: pending|in_progress|completed|cancelled|blocked}[]` | Updates Gemini CLI todo/progress UI. Not necessary for anyteam visibility. |
| `save_memory` | `fact: string` | Appends to `~/.gemini/GEMINI.md`. Should probably remain disabled for isolated teammates. |
| `activate_skill` | `name: enum/string` | Loads Gemini skills. Disabling built-ins may disable Gemini native skill activation. |
| `get_internal_docs` | `path?: string` | Reads Gemini CLI bundled docs. Useful to Gemini itself, but not needed for teammate execution. |
| `enter_plan_mode` | `reason?: string` | Switches approval mode to plan. Not available in yolo. |
| `exit_plan_mode` | `plan_path: string` under Gemini temp plans dir | Presents final plan for formal approval and leaves plan mode on approval. |
| `complete_task` | internal | Finalizes Gemini subagent task; not user-facing. |
| `tracker_create_task`, `tracker_update_task`, `tracker_get_task`, `tracker_list_tasks`, `tracker_add_dependency`, `tracker_visualize`, `update_topic` | task-tracker schemas | Internal task tracking/progress tools in current tool reference. Not needed for anyteam wrapper parity. |

## Disabling built-ins

### Working settings key: `tools.core`

Installed docs say:

- `tools.core` is an allowlist for **all built-in tools**.
- Once `tools.core` is set, only listed built-ins are enabled.
- It supports command-prefix entries such as `run_shell_command(git)` for shell restrictions, but generic tool names are also accepted.
- `tools.exclude` exists but is deprecated; policy `deny` rules are now preferred for blocklisting.

Fresh empirical result:

```json
{
  "security": {"auth": {"selectedType": "oauth-personal"}},
  "tools": {"core": []}
}
```

Running headless with this isolated home and asking to use shell produced no tool call; Gemini only wrote text resembling a call:

```jsonl
{"type":"message","role":"assistant","content":"`run_shell_command(\"pwd\")`","delta":true}
{"type":"result","status":"success","stats":{"tool_calls":0}}
```

That confirms `tools.core: []` removes built-ins from the declared tool set.

### MCP is still available with `tools.core: []`

With the same `tools.core: []`, I configured a FastMCP server `shadow` exposing `echo_payload`, `shell`, and `read_file`. Headless prompt requested the MCP tools. Gemini emitted:

```jsonl
{"type":"tool_use","tool_name":"mcp_shadow_echo_payload","parameters":{"text":"alpha"}}
{"type":"tool_use","tool_name":"mcp_shadow_shell","parameters":{"command":"pwd"}}
{"type":"tool_result","tool_id":"mcp_shadow_echo_payload_...","status":"success","output":"SHADOW::alpha::END"}
{"type":"tool_result","tool_id":"mcp_shadow_shell_...","status":"success","output":"{\"command\":\"pwd\",\"cwd\":\"/home/rosado/Projects/codex-teammate\",\"stdout\":\"/home/rosado/Projects/codex-teammate\\n\",\"stderr\":\"\",\"exit_code\":0}"}
```

ACP with isolated settings `tools.core: []` and per-session MCP server `shadow` also worked. The completed MCP update included full text payload:

```json
{
  "sessionUpdate": "tool_call_update",
  "toolCallId": "mcp_shadow_echo_payload-...",
  "status": "completed",
  "title": "echo_payload (shadow MCP Server)",
  "content": [
    {"type": "content", "content": {"type": "text", "text": "ACP_SHADOW::beta::END"}}
  ],
  "kind": "other"
}
```

ACP note: stdio MCP objects in `session/new.params.mcpServers` must include `env: []`; omitting `env` failed schema validation. Working shape:

```json
{
  "name": "shadow",
  "command": "python",
  "args": ["/path/to/server.py"],
  "env": []
}
```

### Policy engine alternative

The policy engine can deny tools and, per docs, global `deny` excludes matching tools from model memory. This is probably robust, but `tools.core: []` is simpler for an adapter-owned isolated Gemini home. Use policy only if we need conditional/session-specific built-in suppression without mutating settings.

No ACP `initialize` capability was found that asks Gemini not to expose built-ins. Our current initialize advertises client FS/terminal capabilities as false, but that does **not** remove Gemini's own built-in tools; earlier ACP probes still saw built-in tool updates. Use settings/policy, not ACP capabilities, for tool availability.

No environment variable for disabling tools was found in local docs/source search. CLI flags do not expose `coreTools`/`excludeTools`; `--allowed-tools` only affects confirmation and is deprecated.

## MCP naming and precedence

MCP tools cannot literally collide with built-ins in Gemini CLI 0.39.0.

Installed MCP docs state: every discovered MCP tool is unconditionally assigned `mcp_{serverName}_{toolName}`. Examples from local empirical runs:

- server `toy`, tool `shout` -> `mcp_toy_shout`
- server `probe`, tool `echo_payload` -> `mcp_probe_echo_payload`
- server `shadow`, tool `shell` -> `mcp_shadow_shell`

The docs also warn not to use underscores in MCP server names because policy parsing splits FQNs after `mcp_`. The current anyteam alias is safe: `mcp_anyteam_*`.

Therefore:

- A tool named `read_file` on server `anyteam` is exposed as `mcp_anyteam_read_file`.
- It does not overwrite or shadow Gemini's built-in `read_file` by name.
- If built-ins remain enabled, the model may still choose built-ins for natural-language requests like "read this file" or "run pwd". Prompt instructions can help, but this is not a hard guarantee.
- With `tools.core: []`, there is no built-in competing option, so prompt steering only has to teach the names/semantics of the MCP replacements.

## Recommended strategy: disable + replace

Use **A: Disable + replace**.

Adapter-owned Gemini home/settings should set:

```json
{
  "security": {"auth": {"selectedType": "oauth-personal"}},
  "tools": {"core": []},
  "mcpServers": {
    "anyteam": {
      "command": "claude-anyteam-wrapper",
      "args": ["--team", "...", "--name", "..."],
      "env": {"HOME": "..."},
      "trust": true,
      "timeout": 30000
    }
  }
}
```

For ACP, pass the same server in `session/new.params.mcpServers` with ACP's array-style env:

```json
{
  "name": "anyteam",
  "command": "claude-anyteam-wrapper",
  "args": ["--team", "...", "--name", "..."],
  "env": [{"name": "HOME", "value": "..."}]
}
```

Keep `--approval-mode yolo` if the teammate loop already owns safety/approval semantics. The shadow tools themselves must be transparent passthroughs and **must not add filesystem/network sandboxing**.

## Shadow tool set to expose

Minimum `anyteam` MCP tools and suggested schemas:

| MCP tool | Replaces | Suggested parameters | Output contract |
|---|---|---|---|
| `shell` -> `mcp_anyteam_shell` | `run_shell_command` | `command: string`; `description?: string`; `dir_path?: string`; `is_background?: boolean`; optional `timeout_ms?: number`; optional `stdin?: string` | JSON text/object containing `command`, resolved `cwd`, `stdout`, `stderr`, `exit_code`, signal/error, background PID/session info if applicable. Use no sandbox; execute as the Gemini teammate user. |
| `read_file` -> `mcp_anyteam_read_file` | `read_file` | `file_path: string`; `offset?: integer`; `limit?: integer`; optional `encoding?: string` | Text for normal files; for binary/media either base64/resource content or explicit unsupported-binary metadata. Do not root-restrict beyond OS permissions. |
| `write_file` -> `mcp_anyteam_write_file` | `write_file` | `file_path: string`; `content: string`; optional `encoding?: string`; optional `create_dirs?: boolean` | JSON with path, bytes/chars written, created/overwrote flag. No additional confirmation inside MCP in yolo loop. |
| `list_directory` -> `mcp_anyteam_list_directory` | `list_directory` | `dir_path: string`; `ignore?: string[]`; optional `include_hidden?: boolean`; optional `recursive?: boolean`; optional `max_entries?: integer` | JSON/text list with names, type, size, mtime when cheap. |
| `replace` -> `mcp_anyteam_replace` | `replace` | `file_path`; `old_string`; `new_string`; `instruction?: string`; `allow_multiple?: boolean` | JSON with replacements count and maybe unified diff preview. Exact-match semantics should mirror Gemini: fail when default single match is not exactly one. |
| `glob` -> `mcp_anyteam_glob` | `glob` | `pattern`; `path?: string`; `case_sensitive?: boolean`; `respect_git_ignore?: boolean` | JSON/text list of paths, preferably sorted newest first for parity. |
| `grep_search` -> `mcp_anyteam_grep_search` | `grep_search` / `search_file_content` | `pattern`; `path?: string`; `include?: string`; optional `case_sensitive?: boolean`; optional `max_matches?: integer` | Text/JSON matches with file, line number, line. Use `rg`/`git grep` passthrough if available. |
| `web_fetch` -> `mcp_anyteam_web_fetch` | `web_fetch` | `prompt: string` or stricter `urls: string[]`, `prompt?: string` | Fetch from local network/internet without Gemini URL-context magic; return status, headers subset, text excerpt/full body subject to adapter truncation. No network sandbox. |
| `google_web_search` -> `mcp_anyteam_web_search` | `google_web_search` | `query: string`; optional `limit?: integer` | If we do not have a search backend, either do not expose this tool or implement via configured search API. Do not fake Google grounding. |
| `read_many_files` -> `mcp_anyteam_read_many_files` | `read_many_files` | `paths: string[]`; optional `include?: string[]`; `exclude?: string[]`; `recursive?: boolean`; `limit_per_file?: integer` | Concatenated text or structured list of `{path, content}`. Useful replacement for Gemini `@` behavior. |

Optional native-UI tools (`ask_user`, `write_todos`, planning/memory/task tracker) should generally stay disabled. If needed, expose anyteam-native equivalents separately; do not re-enable Gemini built-ins just for UI niceties unless we accept losing visibility for local operations again.

## Prompting changes

With built-ins disabled, add a high-priority instruction to Gemini teammate prompts, for example:

> Gemini built-in local tools are intentionally disabled. For all filesystem, shell, search, edit, and fetch operations, use the MCP anyteam tools: `mcp_anyteam_shell`, `mcp_anyteam_read_file`, `mcp_anyteam_write_file`, `mcp_anyteam_list_directory`, `mcp_anyteam_replace`, `mcp_anyteam_glob`, `mcp_anyteam_grep_search`, `mcp_anyteam_web_fetch`, and related `mcp_anyteam_*` tools. These are transparent passthroughs with no additional sandboxing and their outputs are visible to the adapter.

Do not name the tools only as `shell`/`read_file`; Gemini sees the fully qualified names.

## Integration sketch

1. Extend the anyteam MCP server with the shadow tools above. Keep existing messaging tools. Return plain text or compact JSON strings so both headless and ACP surfaces carry the payload.
2. In `prepare_isolated_gemini_home()` / `write_mcp_settings()`, overlay `"tools": {"core": []}` into the isolated `.gemini/settings.json`. Preserve auth-selection keys as today.
3. For ACP, continue passing MCP server config through `session/new.params.mcpServers`; include `env: []` or array env entries. Do not rely on `initialize` for MCP provisioning.
4. Update Gemini prompt preamble to require `mcp_anyteam_*` tools for local operations.
5. Update event parsing:
   - Headless: consume `tool_use` and `tool_result.output` for `mcp_anyteam_*`.
   - ACP: consume `session/update` `tool_call` and completed `tool_call_update.content[*].content.text` for `mcp_anyteam_*`.
6. Add regression tests:
   - isolated settings with `tools.core: []` produces no built-in shell call for a shell request;
   - same settings plus anyteam MCP allows `mcp_anyteam_shell` and emits visible output in stream-json;
   - ACP session/new with anyteam MCP and `tools.core: []` emits visible `tool_call_update.content`.

## Risks / caveats

- Disabling all built-ins removes Gemini-native `ask_user`, `write_todos`, memory, skills, internal docs, and planning tools. For teammate execution this is acceptable and likely desirable, but it changes Gemini CLI behavior.
- Some Gemini syntactic conveniences (`!cmd`, `@file`) target built-ins. The prompt should tell the model not to use them; users/teammate tasks should not depend on those shorthands.
- MCP server alias should remain `anyteam`, not `any_team`, to avoid policy/FQN parser ambiguity.
- `tools.exclude` is deprecated; prefer `tools.core: []` for our isolated home or policy `deny` rules if a future version changes allowlist behavior.
