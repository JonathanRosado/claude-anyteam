# Installer error fixtures

Fixture files in this directory are raw stderr captures for installer hardening.
Each `.txt` filename is the canonical pattern id. The first `#` line records
whether the stderr is captured or synthesized; tests should strip leading `#`
metadata before handing the remaining text to an error translator.

| Fixture | Source | Scenario | Assertions the translator must support |
| --- | --- | --- | --- |
| `uv-prerelease-blocked.txt` | Captured from user-reported `uv tool install` failure | uv's PubGrub resolver refuses `fastmcp==3.0.0b1` because pre-releases were not enabled. | Pattern id `uv-prerelease-blocked`; non-empty `title`, `explanation`, `action`, `severity`; `action` mentions `--prerelease=allow`. |
| `uv-no-solution-conflict.txt` | Synthesized from uv/PubGrub resolver wording | Two requested packages have incompatible `fastmcp` constraints. | Pattern id `uv-no-solution-conflict`; actionable dependency-conflict guidance; non-fallback severity. |
| `uv-network-timeout.txt` | Synthesized from uv download/retry wording | PyPI wheel download times out after retries. | Pattern id `uv-network-timeout`; action should suggest retrying/checking network or proxy/VPN configuration. |
| `uv-tls-cert-validation.txt` | Synthesized from Node/uv TLS certificate validation failures | Corporate TLS interception makes PyPI HTTPS certificates untrusted. | Pattern id `uv-tls-cert-validation`; action should mention `NODE_EXTRA_CA_CERTS` and a corporate CA bundle. |
| `uv-corporate-proxy.txt` | Synthesized from uv fetch failures behind a proxy | uv cannot reach PyPI because proxy environment variables are missing. | Pattern id `uv-corporate-proxy`; action should include `HTTPS_PROXY`, `HTTP_PROXY`, `UV_HTTP_TIMEOUT=120`, and `curl -I https://pypi.org`. |
| `uv-lock-contention.txt` | Synthesized from uv tool-state lock failures | Another uv process is running or a stale lock remains. | Pattern id `uv-lock-contention`; action should suggest waiting, killing leftover uv processes, and `uv cache clean`. |
| `uv-windows-longpath.txt` | Synthesized from uv Windows wheel copy failure | Windows install fails with `os error 206` because long paths are disabled. | Pattern id `uv-windows-longpath`; `action` mentions `LongPathsEnabled`. |
| `python-store-stub.txt` | Captured Windows Python app-execution-alias stderr | The `python` command resolves to the Microsoft Store stub instead of a real interpreter. | Pattern id `python-store-stub`; action should tell users to install Python or disable App execution aliases. |
| `settings-json-corrupt.txt` | Synthesized from Python `json.decoder.JSONDecodeError` + installer wrapper | `~/.claude/settings.json` is malformed before install. | Pattern id `settings-json-corrupt`; action should point at fixing/backing up `settings.json`. |
| `read-only-home.txt` | Synthesized from Docker/read-only home filesystem errors | Installer cannot write `~/.claude` because the home directory is read-only. | Pattern id `read-only-home`; action should suggest a writable `~/.claude` mount or temporary `HOME`. |
| `plugin-update-soft.txt` | Synthesized from Claude Code plugin manifest update failure | Settings/install succeeded but pulling the newest plugin manifest failed. | Pattern id `plugin-update-soft`; action should suggest `claude plugin update claude-anyteam@claude-anyteam`; severity should be soft. |
| `claude-cli-not-found.txt` | Synthesized from shell/Node command failure | Claude Code CLI is missing from `PATH` during plugin registration. | Pattern id `claude-cli-not-found`; action should tell users to install/open Claude Code CLI or rerun plugin commands after `claude` is available. |
| `claude-plugin-marketplace-failed.txt` | Synthesized from `claude plugin marketplace add` failure | Plugin marketplace registration cannot fetch the GitHub-hosted manifest. | Pattern id `claude-plugin-marketplace-failed`; action should suggest retrying auth/network and manual plugin commands. |
| `windows-antivirus-quarantine.txt` | Synthesized from Windows Defender/EDR quarantine wording | Antivirus quarantined a Python wheel during install. | Pattern id `windows-antivirus-quarantine`; action should mention Defender/EDR exclusions for `%LOCALAPPDATA%\\uv` and `%LOCALAPPDATA%\\claude-anyteam`. |
| `macos-arch-mismatch.txt` | Synthesized from macOS Rosetta/architecture mismatch errors | uv selected a Python binary incompatible with the running terminal architecture. | Pattern id `macos-arch-mismatch`; action should mention `arch`, `arm64`, `i386`, and Rosetta. |
| `conda-interference.txt` | Synthesized from uv interpreter failure with Conda active | An active Conda environment interferes with uv isolated installs. | Pattern id `conda-interference`; action should suggest `conda deactivate` before rerunning. |
| `windows-non-ascii-username.txt` | Synthesized from Windows non-ASCII profile path failures | Windows username contains non-ASCII characters that can trip Python tooling. | Pattern id `windows-non-ascii-username`; action should mention `PYTHONUTF8=1` or `UV_TOOL_DIR=C:\\uv-tools`. |
| `kimi-not-found.txt` | Synthesized from POSIX shell command-not-found output plus Windows variant metadata | Kimi CLI is missing from `PATH` during provider prerequisite checks. | Pattern id `kimi-not-found`; action should tell users to install `kimi-cli` with Python 3.13 and run `kimi login`. |
| `kimi-not-signed-in.txt` | Synthesized from Kimi CLI credential/auth output | Kimi CLI is installed but `~/.kimi/credentials/kimi-code.json` is missing. | Pattern id `kimi-not-signed-in`; action should tell users to run `kimi login` before retrying. |
| `kimi-version-old.txt` | Synthesized from stale `kimi info` output | Kimi CLI reports `kimi-cli version: 0.9.x`. | Pattern id `kimi-version-old`; action should tell users to reinstall `kimi-cli` with Python 3.13. |
| `uv-python-not-found.txt` | Synthesized from uv interpreter-resolution wording | uv cannot find the requested Python version. | Pattern id `uv-python-not-found`; action should suggest installing the requested Python or allowing uv-managed Python downloads. |
| `tmux-not-found.txt` | Synthesized from claude-anyteam installer prereq failure | No supported terminal multiplexer (`tmux`/`psmux`) is on `PATH`. | Pattern id `tmux-not-found`; action should include platform install commands for `tmux`/`psmux`. |
| `permission-denied-settings.txt` | Synthesized from Python `PermissionError` + installer wrapper | Installer cannot replace `~/.claude/settings.json`. | Pattern id `permission-denied-settings`; action should address file ownership/permissions without recommending blind `sudo` on the installer. |
| `uv-cache-permission-denied.txt` | Synthesized from uv cache directory failure | uv cannot create files under its cache directory. | Pattern id `uv-cache-permission-denied`; action should tell users to fix cache ownership/permissions or clear the uv cache. |

Fallback coverage belongs in translator tests: an unrecognized stderr should
return a soft fallback, include the raw output, and link to
<https://github.com/JonathanRosado/claude-anyteam/issues>.
