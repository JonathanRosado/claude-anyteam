# Installer code surface map

Scope: this map was produced from full reads of `src/claude_anyteam/installer.py`, `src/claude_anyteam/cli.py`, `npm/bin/setup.js`, and `hooks/session-start.sh` on branch `main` after v0.4.0. Host OAuth files were inspected only for shape/location; do not copy token values into docs or tests.

## Top-level behavior

- The Python installer owns Claude Code config mutations: `~/.claude/settings.json`, `~/.claude.json`, and `~/.claude/plugins/data/claude-anyteam-claude-anyteam/install-state.json` (`installer.py:185-194`, `installer.py:917-1038`).
- The npm setup wrapper installs/locates Python + uv + the uv tool, then invokes the Python installer as `uv --no-config tool run --from claude-anyteam claude-anyteam install --assume-yes` (`setup.js:167-179`, `setup.js:339-350`).
- The session-start hook is an opportunistic self-heal: if `settings.json` does not already point at executable command/binary paths, it runs the bundled plugin binary with `install` and suppresses successful install stdout (`session-start.sh:8-84`).
- There is no provider sign-in probe today. Provider probes are CLI presence/version/capability checks only (`installer.py:520-545`, `installer.py:616-660`).

## CLI flags and what they do

### `claude-anyteam` runtime flags (`cli.py`)

- `--team`: overrides `CLAUDE_ANYTEAM_TEAM` by setting `overrides["team_name"]` (`cli.py:38`, `cli.py:253-254`).
- `--name`: overrides `CLAUDE_ANYTEAM_NAME` by setting `overrides["agent_name"]` (`cli.py:39`, `cli.py:255-256`).
- `--cwd`: working directory for Codex invocations; becomes `overrides["cwd"]` (`cli.py:40`, `cli.py:257-258`).
- `--poll-s`: float inbox poll interval in seconds; becomes `overrides["poll_interval_s"]` (`cli.py:41`, `cli.py:259-260`).
- `--color`: display color; becomes `overrides["color"]` (`cli.py:42`, `cli.py:261-262`).
- `--plan-mode`: store-true flag; sets `plan_mode_required` to string `"true"` (`cli.py:43-47`, `cli.py:263-264`).
- `--codex-binary`: Codex CLI binary name; becomes `overrides["codex_binary"]` (`cli.py:48`, `cli.py:265-266`).
- `--app-server` / `--no-app-server`: `BooleanOptionalAction`, default `None`; only explicit use sets `overrides["app_server"]` to `"true"`/`"false"` (`cli.py:58-67`, `cli.py:267-272`).
- `--model`: Codex model slug; becomes `overrides["model"]` (`cli.py:68-76`, `cli.py:273-274`).
- `--effort`: choices `low|medium|high|xhigh`; becomes `overrides["effort"]` (`cli.py:77-84`, `cli.py:275-276`).
- Normal runtime prints no stdout itself except logs: config errors log `startup.config_error` and exit 2; successful config logs `startup` and calls `run(settings)` (`cli.py:279-294`).

### `claude-anyteam install` flags (`cli.py`)

- `--assume-yes` / `-y`: auto-accepts the teammateMode overwrite prompt by using `lambda _current: True`; otherwise uses the TTY prompt (`cli.py:102-107`, `cli.py:185-187`).
- Hidden `--settings-path`: test/override path for `settings.json`; passed to installer when non-null (`cli.py:108-112`, `cli.py:231-232`).
- Hidden `--claude-json-path`: test/override path for `~/.claude.json`; passed to installer when non-null (`cli.py:113-117`, `cli.py:233-234`).
- Hidden `--state-path`: test/override path for install-state; passed to installer when non-null (`cli.py:118-122`, `cli.py:235-236`).
- On `InstallError`, prints the exception string to stderr and exits `exc.cli_exit_code` or 2; on success, prints `format_install_message(result)` and exits 0 (`cli.py:188-200`).

### `claude-anyteam uninstall` flags (`cli.py`)

- Hidden `--settings-path`, `--claude-json-path`, `--state-path`: same override roles as install, passed to `uninstall_settings` when present (`cli.py:135-149`, `cli.py:241-248`).
- On `InstallError`, prints the exception string to stderr and exits `exc.cli_exit_code` or 2; on success, prints `format_uninstall_message(result)` and exits 0 (`cli.py:210-222`).

### `npx --yes claude-anyteam` setup flags (`setup.js`)

- `--postinstall`: marks the run as postinstall/silent; errors become hints and exit 0 instead of hard failures (`setup.js:39-43`, `setup.js:243-245`, `setup.js:430-433`).
- `--settings-path <path>`: forwarded only to the Python installer as hidden `--settings-path`; missing value throws `--settings-path requires a value` (`setup.js:44-49`, `setup.js:167-179`).
- `--help` / `-h`: prints usage and exits 0 (`setup.js:50-51`, `setup.js:59-67`, `setup.js:236-239`).
- Any other option throws `Unknown option: ...` and is handled by the final rejection block (`setup.js:52-53`, `setup.js:424-438`).

### `hooks/session-start.sh`

- No CLI flags. It reads `CLAUDE_PLUGIN_ROOT` as an optional environment override for locating the plugin root, otherwise derives it from the hook path (`session-start.sh:4`).

## Probes and failure handling

### JSON/path helpers (`installer.py`)

- `_resolve_executable(name_or_path)` returns a resolved `Path` for existing explicit paths or `shutil.which` hits; returns `None` when input is empty, path does not exist, or PATH lookup fails (`installer.py:202-218`).
- `discover_managed_paths(...)` resolves the settings path plus claude-anyteam binary and spawn shim from explicit args, current executable siblings, or PATH. It raises `InstallError` if either binary or shim cannot be resolved (`installer.py:230-273`).
- `_load_settings`, `_load_claude_json`, and `_load_state` parse JSON objects and raise `InstallError` on malformed JSON or non-object top levels; `_load_state` returns `None` for an absent state file (`installer.py:283-296`, `installer.py:345-358`, `installer.py:370-387`).
- `_env_block` returns/creates the `env` dict, raising if `env` is not an object or any env key/value is non-string (`installer.py:299-317`).
- `_atomic_write_json` writes indented JSON through a temp file + fsync + `os.replace` (`installer.py:320-339`).

### Terminal multiplexer probe (`_check_terminal_multiplexer`)

- Return type `PrereqCheck` has fields `found`, `binary`, `path`, and `platform` (`installer.py:63-69`).
- `_platform_name` maps Linux to `linux`, macOS to `darwin`, Win32/Cygwin to `windows`, otherwise raw `sys.platform` (`installer.py:407-415`).
- On Windows it probes `psmux`, then `tmux`; elsewhere it probes only `tmux` (`installer.py:418-435`).
- Found case returns `found=True`, chosen binary name, resolved path, and platform; missing case returns `found=False`, `binary=None`, `path=None`, platform (`installer.py:437-445`).
- Missing tmux/psmux is the only hard prereq gate in `install()`: it builds an error with platform install instructions, appends Codex/Gemini warnings if any, then raises `InstallError` before writing settings (`installer.py:948-966`).

### Codex CLI probe (`_check_codex_cli`)

- Return type `CodexCliCheck` has fields `found`, `path`, `version`, and `raw_output` (`installer.py:90-101`).
- Constants: binary `codex`, install command `npm i -g @openai/codex`, docs URL, minimum version `(0,120,0)`, timeout 5s (`installer.py:470-474`).
- `_parse_cli_version` scans whitespace tokens for semver-ish text; `_parse_version_tuple` normalizes missing patch to 0; `_codex_meets_minimum` returns `True`, `False`, or `None` for unknown/unparseable (`installer.py:479-517`).
- If `codex` is absent on PATH, returns `found=False, path=None, version=None, raw_output=None` (`installer.py:520-523`).
- If present, runs `<resolved codex> --version` with capture, text, timeout, `check=False`; `OSError` and `subprocess.SubprocessError` are swallowed by treating `completed=None` (`installer.py:525-538`).
- Only returncode 0 populates `raw_output` from stdout and parses `version`; nonzero/exception still returns `found=True` with resolved path and `version/raw_output=None` (`installer.py:540-545`).
- `_codex_cli_warning` returns a warning string only when Codex is missing or below the floor; unparseable present versions get a presence-only line from `format_install_message` (`installer.py:547-586`, `installer.py:1218-1225`).

### Gemini CLI probe (`_check_gemini_cli`)

- Return type `GeminiCliCheck` has fields `found`, `path`, `version`, `raw_output`, `capabilities`, and `missing_capabilities` (`installer.py:73-87`).
- Constants: binary `gemini`, install command `npm install -g @google/gemini-cli`, docs URL, timeout 5s (`installer.py:475-478`).
- Required headless capabilities are `--prompt`, `--output-format stream-json`, `--resume`, and `--approval-mode yolo`; ACP is detected but not in the required tuple (`installer.py:590-596`, `installer.py:606-613`).
- If `gemini` is absent on PATH, returns `found=False, path=None, version=None, raw_output=None`, default empty capabilities and missing tuple (`installer.py:616-619`).
- If present, runs `<resolved gemini> --version`, captures stdout/stderr, swallows `OSError`/`SubprocessError`, and only returncode 0 populates `raw_output` and parsed `version` (`installer.py:620-637`).
- It then runs `<resolved gemini> --help`; only returncode 0 contributes help text. Exceptions/nonzero leave `help_text=""`, causing all required capabilities to be missing (`installer.py:639-652`).
- Capability booleans are substring checks: `--prompt`; `--output-format` plus `stream-json`; `--resume`; `--approval-mode` plus `yolo`; and `--acp` or `--experimental-acp` for ACP support (`installer.py:598-613`).
- Return always preserves `found=True` once PATH lookup succeeds, even when version/help subprocesses fail; missing capabilities are computed from the capability dict (`installer.py:653-660`).
- `_gemini_cli_warning` returns a missing warning or a missing-headless-capabilities warning; otherwise `format_install_message` emits detected version/path and optionally an ACP-not-detected note (`installer.py:662-686`, `installer.py:1227-1238`).

### Session-start config probe (`hooks/session-start.sh`)

- `has_configured_command` first requires `~/.claude/settings.json` to exist (`session-start.sh:8-12`).
- With `python3`, it parses JSON, requires top-level object and object `env`, then validates all three env vars exist, are non-empty strings, point to existing executable files, and sets `CONFIG_VALIDATED=1` only on success (`session-start.sh:14-58`).
- Without `python3`, it falls back to grep-only presence/non-empty checks for the three env keys; this does not validate executability and does not set `CONFIG_VALIDATED=1` (`session-start.sh:61-63`).
- If config is valid, the hook prints the orientation message only when the Python validation path was used, then exits 0 (`session-start.sh:66-70`).
- If config is invalid/missing, it runs `$PLUGIN_ROOT/bin/claude-anyteam install >/dev/null`; success prints the orientation message and exits 0, status 127 is ignored as exit 0, any other status is propagated (`session-start.sh:73-84`).

### NPM setup probes/delegations (`setup.js`)

- `detectPython`, `detectUv`, `findInstalledTool`, `which`, `isCI`, and `isInteractive` are imported from `npm/lib/detect.js`; this file delegates to them and does not implement their internals (`setup.js:4-18`).
- Python missing: postinstall prints a skip hint and exits 0; interactive setup prints a failure box and exits 1 (`setup.js:253-266`).
- uv missing: postinstall or non-interactive auto-installs; interactive prompts `[Y/n]`; decline prints `UV NOT INSTALLED` and exits 1 (`setup.js:70-78`, `setup.js:273-287`).
- uv install failure: postinstall skip hint/0; normal failure box/1 (`setup.js:290-303`).
- Existing uv tool is reused; otherwise `installTool` is run with a spinner and failures are handled as postinstall hint/0 or failure box/1 (`setup.js:312-331`).
- Python installer spawn errors are wrapped in `UNABLE TO RUN PYTHON INSTALLER`; nonzero Python installer exits are not rewrapped, except exit code 3 gets a bug-warning despite `--assume-yes` (`setup.js:342-375`).
- Claude plugin registration probes `which('claude')`; if missing, warns with manual commands; if present, runs marketplace add/install/update and tolerates known already-present strings (`setup.js:214-232`, `setup.js:378-397`).

## `settings.json` env vars written

- Constants define current keys: `CLAUDE_CODE_TEAMMATE_COMMAND`, `CLAUDE_ANYTEAM_BINARY`, `CLAUDE_ANYTEAM_GEMINI_BINARY`; legacy key `CODEX_TEAMMATE_BINARY` is recognized for cleanup (`installer.py:25-28`).
- `install()` writes `env.CLAUDE_CODE_TEAMMATE_COMMAND = str(paths.shim_path)`, `env.CLAUDE_ANYTEAM_BINARY = str(paths.binary_path)`, and `env.CLAUDE_ANYTEAM_GEMINI_BINARY = str(paths.binary_path.with_name("gemini-anyteam"))` (`installer.py:978-990`).
- If `env.CODEX_TEAMMATE_BINARY` exists and `_looks_managed` says its basename is managed, install removes it and records the removal in `removed_legacy_keys` (`installer.py:992-996`, `installer.py:1070-1076`).
- Writes happen through `_write_settings` only if values changed, the legacy key was removed, or the file did not exist (`installer.py:998-1000`).
- The session-start hook validates the same three current env keys before deciding whether to self-heal (`session-start.sh:34-36`, `session-start.sh:46-52`, `session-start.sh:61-63`).

## Install-state keys recorded

State path default is `~/.claude/plugins/data/claude-anyteam-claude-anyteam/install-state.json` (`installer.py:193-194`). The state dict is built inside `install_teammate_mode` (`installer.py:721-738`):

- `schema_version`: current value `2` (`installer.py:41`, `installer.py:723`).
- `teammateMode_original`: previous `teammateMode`, or `None` when absent (`installer.py:724`).
- `teammateMode_set_by_anyteam`: bool ownership marker (`installer.py:725`).
- `settings_file_created_by_anyteam`: bool, passed from `install()` as `not existed` for settings.json (`installer.py:728`, `installer.py:1017`).
- `claude_json_created_by_anyteam`: bool, true if `~/.claude.json` did not exist before loading (`installer.py:716`, `installer.py:729`).
- `codex_cli_found`: bool, only included when a Codex probe result was supplied (`installer.py:731-733`).
- `codex_cli_version`: parsed version string or `None`, only included with Codex probe (`installer.py:731-733`).
- `gemini_cli_found`: bool, only included when a Gemini probe result was supplied (`installer.py:734-737`).
- `gemini_cli_version`: parsed version string or `None`, only included with Gemini probe (`installer.py:734-737`).
- `gemini_cli_capabilities`: capability bool dict, only included with Gemini probe (`installer.py:734-737`).

## User-facing message strings and emitters

### Python CLI wrapper (`cli.py`)

- Runtime parser description/epilog mention routing Codex-powered teammates and management commands (`cli.py:29-36`).
- Install parser description says it persists the spawn shim and sets `teammateMode="tmux"` (`cli.py:94-100`).
- Uninstall parser description says it removes managed settings entries and reverts teammateMode (`cli.py:132-133`).
- Interactive prompt: `claude-anyteam wants to set teammateMode="tmux" in ~/.claude.json. Current value: ... Overwrite? [y/N]` (`cli.py:158-172`).
- Install/uninstall errors print the exception text to stderr (`cli.py:196`, `cli.py:218`).
- Install/uninstall success print the formatter output (`cli.py:199`, `cli.py:221`).
- Runtime config errors are log events, not plain stdout/stderr strings: `startup.config_error`; success logs `startup` (`cli.py:279-294`).

### Python installer (`installer.py`)

- Path resolution errors: unable to resolve `claude-anyteam` binary or spawn shim (`installer.py:259-270`).
- JSON/config errors: invalid JSON, non-object top-level, non-object `env`, non-string env entries (`installer.py:288-316`, `installer.py:350-357`, `installer.py:381-386`).
- Terminal multiplexer missing error: `claude-anyteam requires a terminal multiplexer on PATH; none was found`, platform install instructions, and rerun instruction (`installer.py:448-464`, `installer.py:953-960`).
- Codex missing warning: `Warning: the OpenAI Codex CLI (codex) was not found on PATH...` plus install command, sign-in hint, setup guide (`installer.py:565-571`).
- Codex below-floor warning: `Warning: detected Codex CLI ... but claude-anyteam requires ... or newer` plus upgrade/sign-in/docs (`installer.py:576-581`).
- Gemini missing warning: `Warning: the Gemini CLI (gemini) was not found on PATH...` plus install command, sign-in/API-key/Vertex hint, setup guide (`installer.py:665-671`).
- Gemini missing-capabilities warning: detected path, one line per missing required flag, upgrade command, setup guide (`installer.py:674-684`).
- Non-string `teammateMode` error refuses to touch the file (`installer.py:717-719`).
- Prompt-declined error: `Install aborted: existing teammateMode=...`, explains need for `tmux`, and suggests `--assume-yes` or manual edit; carries exit code 3 (`installer.py:769-776`).
- Corrupted state error: malformed `teammateMode_original`, refuses to touch config, asks user to inspect/delete state; carries exit code 4 (`installer.py:832-840`).
- Install success summary strings: updated settings, set three env vars, removed legacy env key, set/restored/already tmux teammateMode lines, provider warnings/detections, ACP note, restart/routing reminder, and idempotent `The existing settings already matched this install.` (`installer.py:1192-1243`).
- Uninstall summary strings: no settings file, removed/deleted settings, updated/removed env keys, skipped unmanaged env keys, no env keys present, deleted/removed/restored teammateMode, not-managed teammateMode, restart reminder (`installer.py:1247-1282`).

### NPM setup (`setup.js`)

- Usage text includes `Usage: npx --yes claude-anyteam [--settings-path <path>] [--postinstall]` and summarizes uv/Python installer/plugin registration (`setup.js:59-67`).
- Argument errors: `--settings-path requires a value`; `Unknown option: ...` (`setup.js:44-53`).
- uv prompt: `uv is missing. Install it now into ...? [Y/n]` (`setup.js:70-78`).
- Spinner success/failure prefixes use `done`/`failed` plus the spinner text (`setup.js:81-94`).
- Postinstall hint: `claude-anyteam: automatic setup skipped (...). Run npx --yes claude-anyteam to finish.` (`setup.js:118-121`).
- Plugin manual summary and lines include `claude plugin marketplace add`, `claude plugin install`, and `claude plugin update` commands (`setup.js:27-33`, `setup.js:123-128`).
- Startup banner text: `Zero-friction claude-anyteam setup for Claude Code.` and `We will check Python...` (`setup.js:247-250`).
- Failure boxes/titles: `PYTHON 3 REQUIRED`, `UV NOT INSTALLED`, `UV INSTALL FAILED`, `TOOL INSTALL FAILED`, `UNABLE TO RUN PYTHON INSTALLER`, `UNEXPECTED INSTALLER ERROR` (`setup.js:260-266`, `setup.js:281-287`, `setup.js:297-303`, `setup.js:326-331`, `setup.js:357-362`, `setup.js:435-438`).
- Status lines: `python3 detected`, `uv ready`, `existing claude-anyteam tool detected`, and `Running claude-anyteam install (Python) — tmux + Codex/Gemini CLI prereq checks...` (`setup.js:270`, `setup.js:308`, `setup.js:316`, `setup.js:339-340`).
- Python installer abnormal exit code 3 warning asks to file a bug (`setup.js:369-374`).
- Plugin warnings: `CLAUDE CODE PLUGIN SKIPPED`; missing Claude Code CLI; settings written but plugin registration failed; details and manual commands (`setup.js:131-145`, `setup.js:378-397`).
- Success box title `INSTALL COMPLETE` and lines: tool status, plugin status, uv bin dir, launch template, restart warning; final line says launcher is live and `codex-`/`gemini-` prefixes are used (`setup.js:102-105`, `setup.js:410-421`).

### Session hook (`hooks/session-start.sh`)

- Only user-facing text is `ORIENTATION_MESSAGE`: `claude-anyteam is installed; Agent Teams teammates named codex-* route to Codex and gemini-* route to Gemini CLI. Docs: https://github.com/JonathanRosado/claude-anyteam` (`session-start.sh:7`).
- It prints that message when config validates through Python or when hook-triggered install succeeds (`session-start.sh:66-75`).

## Uninstall path symmetry

- Install writes `settings.json` env keys, `~/.claude.json` `teammateMode`, and install-state; uninstall reverses those three areas and only removes files it can prove it created (`installer.py:917-1038`, `installer.py:1080-1188`).
- Env symmetry: uninstall checks current and legacy keys, removes only values whose basename is managed, and records unmanaged values in `skipped` (`installer.py:1140-1150`, `installer.py:1070-1076`).
- Settings file deletion symmetry: install records `settings_file_created_by_anyteam`; uninstall peeks state before deleting it and deletes settings.json only if that flag is true and the file becomes logically empty (`installer.py:1104-1120`, `installer.py:1154-1165`).
- teammateMode symmetry: install records original value and ownership; uninstall no-ops without state, no-ops on claude.json when `teammateMode_set_by_anyteam` is false, restores/removes when true, and refuses corrupted non-string originals (`installer.py:702-709`, `installer.py:792-840`, `installer.py:842-884`).
- Claude JSON deletion symmetry: install records `claude_json_created_by_anyteam`; uninstall deletes `~/.claude.json` only if that flag is true and no keys remain (`installer.py:716-729`, `installer.py:858-873`).
- State cleanup symmetry: uninstall deletes the state file after a successful revert and tries a non-recursive `rmdir` of the plugin-data dir only if empty (`installer.py:394-400`, `installer.py:875-884`).
- CLI symmetry: both install and uninstall surface `InstallError` on stderr and use `cli_exit_code` fallback 2; install success uses `format_install_message`, uninstall success uses `format_uninstall_message` (`cli.py:188-221`).

## Host sign-in state inspection

- Codex OAuth state exists at `/home/rosado/.codex/auth.json`. Shape observed: top-level `auth_mode`, `OPENAI_API_KEY`, nested `tokens` object containing ID/access/refresh token material and `account_id`, plus `last_refresh` (`/home/rosado/.codex/auth.json:2-10`, values redacted from this doc).
- Gemini OAuth state exists at `/home/rosado/.gemini/oauth_creds.json`. Shape observed: `access_token`, `scope`, `token_type`, `id_token`, `expiry_date`, `refresh_token` (`/home/rosado/.gemini/oauth_creds.json:2-7`, values redacted from this doc).
- Gemini account selection exists at `/home/rosado/.gemini/google_accounts.json` with `active` and `old` keys (`/home/rosado/.gemini/google_accounts.json:2-3`, values redacted from this doc).
- For implementation, sign-in detection should be conservative: existence + parseable JSON + expected non-empty credential keys. Do not log token values. Treat expired/malformed as not signed in if checking expiry is added.

## Right insertion points for new behaviors

### 1. Sign-in detection

- Add new dataclasses near `CodexCliCheck`/`GeminiCliCheck` or extend them, but avoid changing existing probe semantics unexpectedly (`installer.py:73-101`).
- Add pure probes near `_check_codex_cli` and `_check_gemini_cli`: e.g. `_check_codex_auth(path=Path.home()/".codex/auth.json")` and `_check_gemini_auth(...)` (`installer.py:520-545`, `installer.py:616-660`). Keep them injectable like the existing `codex_cli_check_fn`/`gemini_cli_check_fn` parameters so tests can stub them (`installer.py:927-946`).
- Call sign-in probes in `install()` immediately after CLI probes and before the tmux hard gate/write phase. Current install already collects all provider info before deciding to halt, so this is the natural status-gathering point (`installer.py:941-966`).
- Record non-secret auth booleans/diagnostics in install-state inside `_build_state`, adjacent to `codex_cli_found` and `gemini_cli_capabilities` (`installer.py:721-738`).

### 2. Status table render

- Existing install summary is emitted by `format_install_message(result)`, called from `_install_command` (`installer.py:1192-1243`, `cli.py:199`).
- Add the provider status table in `format_install_message` so direct Python installs and npm setup (which inherits Python stdout) both show it (`setup.js:339-350`).
- Avoid adding it only in `setup.js`, because session-start direct install and `claude-anyteam install` would diverge (`session-start.sh:73-75`, `cli.py:188-200`).

### 3. Refuse-to-install-blank gate + `--force-empty`

- The right gate is in `install()` after collecting tmux/Codex/Gemini/sign-in probes, after the existing hard tmux gate, and before `discover_managed_paths()` / settings writes. That preserves the current “no writes before prereq failure” rule (`installer.py:941-972`).
- Add `force_empty: bool = False` to `install()` and `_install_command`, plus `--force-empty` to `_build_install_parser` (`installer.py:917-930`, `cli.py:92-123`, `cli.py:175-200`).
- Condition should be based on provider readiness, not just CLI found: neither Codex ready nor Gemini ready should raise `InstallError` unless `force_empty` is true. Use a new exit code only if scripts need to distinguish it; otherwise default 2 matches generic install failure (`installer.py:45-50`, `cli.py:196-197`).
- NPM setup currently always passes `--assume-yes`; it must pass `--force-empty` only if a new npm flag or CI/silent policy explicitly chooses that behavior (`setup.js:167-179`, `setup.js:342-375`).

### 4. Interactive provider walk-through

- There is only one Python interactive mode today: `_interactive_prompt` for overwriting an existing `teammateMode`; non-TTY returns false, so scripted installs fail rather than hang (`cli.py:157-172`).
- The npm wrapper has an interactive uv prompt and detects `isInteractive()`, but it delegates provider details to Python (`setup.js:70-78`, `setup.js:243-245`, `setup.js:339-350`).
- Introduce provider walkthrough in `_install_command`, not deep in `install()`: `install()` should remain a mostly pure config mutation/probe function with injectable probes and no stdin, while CLI can decide whether TTY interaction is allowed (`cli.py:175-200`, `installer.py:917-1038`).
- Non-interactive safety: gate the walkthrough on `sys.stdin.isatty()` and not `--assume-yes`/CI-equivalent. For npm, be careful: it invokes Python with `--assume-yes`, so walkthrough would be skipped unless npm gains an explicit interactive flow before calling Python (`setup.js:167-179`).
- If the walkthrough needs to run before refusal, implement as: collect status in `install()` or a new probe-only helper, render table/walkthrough in CLI, then either re-call install with `force_empty` or exit. This avoids partially writing settings while guiding users (`installer.py:941-1000`, `cli.py:188-200`).
