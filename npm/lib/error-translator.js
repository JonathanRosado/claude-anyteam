const ISSUE_URL = 'https://github.com/JonathanRosado/claude-anyteam/issues/new';
const PYTHON_DOWNLOADS_URL = 'https://www.python.org/downloads/';
const CLAUDE_CODE_SETUP_URL = 'https://docs.claude.com/en/docs/claude-code/setup';

const HARD = 'hard';
const SOFT = 'soft';

const PRERELEASE_BLOCKED = /pre-release.*weren't enabled|--prerelease=allow/i;
const NO_SOLUTION = /no solution found when resolving dependencies/i;
const PYTHON_MISSING = /no.*python.*(found|interpreter)|could not find an interpreter/i;
const TLS_CERT_VALIDATION = /unable to verify the first certificate|SELF_SIGNED_CERT_IN_CHAIN|UNABLE_TO_GET_ISSUER_CERT|certificate verify failed/i;
const CORPORATE_PROXY = /network unreachable|could not connect|failed to fetch.*pypi|connection refused.*443/i;
const NETWORK_TIMEOUT = /connection.*(timed?\s*out|reset)|name resolution|getaddrinfo|EAI_AGAIN|tls handshake/i;
const NETWORK = /connection.*(refused|timed?\s*out|reset)|name resolution|getaddrinfo|EAI_AGAIN|tls handshake/i;
const DISK_FULL = /no space left|ENOSPC|disk.*full/i;
const PERMISSION_DENIED = /permission denied|EACCES|access.*denied/i;
const WINDOWS_LONG_PATH = /path.*(too long|exceeds|260)|filename.*too long/i;
const WINDOWS_STORE_PYTHON = /Microsoft Store|Windows Store|python3?\.exe.*not.*found.*install Python/i;
const CLAUDE_NOT_FOUND = /claude(?: code)? cli.*not (?:detected|found)|claude.*not.*PATH|command not found: claude|ENOENT.*claude/i;
const KIMI_NOT_FOUND = /kimi.*not.*found|command not found.*kimi|'kimi' is not recognized/i;
const KIMI_NOT_SIGNED_IN = /kimi.*credentials|credentials.*kimi|kimi.*not.*signed.*in|not.*signed.*in.*kimi|kimi.*auth/i;
const KIMI_VERSION_OLD = /kimi.*version.*([01]\.|0\.)/i;
const UV_LOCK_CONTENTION = /failed to acquire lock|another uv process|tool-state lock/i;
const READ_ONLY_HOME = /read-only file system|EROFS|Operation not permitted.*home/i;
const WINDOWS_ANTIVIRUS_QUARANTINE = /virus|operation did not complete successfully|defender|threat detected|quarantined/i;
const MACOS_ARCH_MISMATCH = /not a Mach-O binary|bad CPU type in executable|incompatible architecture|arch.*mismatch/i;
const CONDA_INTERFERENCE = /could not find a usable Python interpreter/i;
const NON_ASCII = /[^\x00-\x7F]/;

function detailsFromObject(value) {
  return value.raw ?? value.stderr ?? value.details ?? value.stdout ?? value.message ?? '';
}

function normalize(rawError, context = {}) {
  const objectInput = rawError && typeof rawError === 'object';
  const details = objectInput ? detailsFromObject(rawError) : rawError;
  return {
    text: String(details ?? ''),
    platform: context.platform ?? (objectInput ? rawError.platform : undefined) ?? process.platform,
    pythonVersion: context.pythonVersion ?? (objectInput ? rawError.pythonVersion : undefined),
    kind: context.kind ?? (objectInput ? rawError.kind : undefined),
    step: context.step ?? (objectInput ? rawError.step : undefined),
    env: context.env ?? (objectInput ? rawError.env : undefined) ?? process.env,
    home: context.home ?? (objectInput ? rawError.home : undefined),
  };
}

export function parsePythonVersion(version) {
  const match = String(version ?? '').match(/\b(?:Python\s*)?(\d+)\.(\d+)(?:\.(\d+))?\b/i);
  if (!match) {
    return null;
  }
  return {
    major: Number.parseInt(match[1], 10),
    minor: Number.parseInt(match[2], 10),
    patch: Number.parseInt(match[3] ?? '0', 10),
  };
}

export function isPythonVersionTooOld(version) {
  const parsed = parsePythonVersion(version);
  if (!parsed) {
    return false;
  }
  return parsed.major < 3 || (parsed.major === 3 && parsed.minor < 12);
}

function extractDetectedPythonVersion(text) {
  const patterns = [
    /(?:detected|found|using|current)\s+(?:Python\s*)?(\d+\.\d+(?:\.\d+)?)/i,
    /Python\s+(\d+\.\d+(?:\.\d+)?)\s+(?:is\s+)?(?:too old|unsupported|not supported)/i,
    /running\s+(?:on\s+)?Python\s+(\d+\.\d+(?:\.\d+)?)/i,
  ];
  for (const pattern of patterns) {
    const match = String(text ?? '').match(pattern);
    if (match) {
      return match[1];
    }
  }
  return null;
}

function isWindows(raw) {
  return raw.platform === 'win32';
}

function prereleaseBlocked(raw) {
  return PRERELEASE_BLOCKED.test(raw.text);
}

function hasCondaEnv(raw) {
  return Boolean(raw.env?.CONDA_DEFAULT_ENV);
}

function homePath(raw) {
  return raw.home ?? raw.env?.USERPROFILE ?? raw.env?.HOME ?? '';
}

export const patterns = [
  {
    id: 'uv-prerelease-blocked',
    match: (raw) => prereleaseBlocked(raw),
    render: () => ({
      title: 'Pre-release dependency blocked by uv',
      explanation: 'We use a pre-release Python dependency, and uv refused to resolve it without explicit pre-release opt-in.',
      action: 'Re-run with `npx --yes claude-anyteam@latest`. If it still fails, run `uv tool install --prerelease=allow claude-anyteam`.',
      severity: HARD,
    }),
  },
  {
    id: 'uv-no-solution',
    match: (raw) => NO_SOLUTION.test(raw.text) && !prereleaseBlocked(raw),
    render: () => ({
      title: 'Python dependency conflict',
      explanation: 'uv could not find a compatible set of Python dependencies. Most often, another installed tool pinned an incompatible dependency version.',
      action: 'Run `uv tool install --reinstall claude-anyteam`, then re-run `npx --yes claude-anyteam@latest`.',
      severity: HARD,
    }),
  },
  {
    id: 'plugin-update-soft',
    match: (raw) => raw.step === 'plugin-update',
    render: () => ({
      title: 'Claude Code plugin update skipped',
      explanation: 'Plugin install succeeded; pulling the latest manifest failed (usually a temporary github.com hiccup).',
      action: 'Re-run `claude plugin update claude-anyteam@claude-anyteam` whenever you have a moment. Settings are already in place; teammates will work.',
      severity: SOFT,
    }),
  },
  {
    id: 'uv-tls-cert-validation',
    match: (raw) => TLS_CERT_VALIDATION.test(raw.text),
    render: () => ({
      title: 'Corporate TLS certificate not trusted',
      explanation: "Your network's HTTPS certificate isn't trusted by Node — typical on corporate networks with traffic inspection (Zscaler, Netskope, etc.).",
      action: 'Get your corporate CA bundle (often `/etc/ssl/certs/ca-certificates.crt` or from IT) and run `NODE_EXTRA_CA_CERTS=/path/to/corp-ca.pem npx --yes claude-anyteam`.',
      severity: HARD,
    }),
  },
  {
    id: 'uv-corporate-proxy',
    match: (raw) => CORPORATE_PROXY.test(raw.text) && !NETWORK_TIMEOUT.test(raw.text),
    render: () => ({
      title: 'Corporate proxy blocked PyPI',
      explanation: "uv couldn't reach PyPI through your corporate network.",
      action: 'Set the proxy explicitly: `HTTPS_PROXY=http://your.proxy:port HTTP_PROXY=$HTTPS_PROXY UV_HTTP_TIMEOUT=120 npx --yes claude-anyteam`. Confirm with `curl -I https://pypi.org`.',
      severity: HARD,
    }),
  },
  {
    id: 'uv-windows-longpath',
    match: (raw) => isWindows(raw) && WINDOWS_LONG_PATH.test(raw.text),
    render: () => ({
      title: 'Windows long-path limit hit',
      explanation: 'Windows rejected a Python package path because it exceeded the legacy 260-character path limit.',
      action: "Open PowerShell as Admin and run `New-ItemProperty -Path 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\FileSystem' -Name 'LongPathsEnabled' -Value 1 -PropertyType DWORD -Force`, then reboot and re-run.",
      severity: HARD,
    }),
  },
  {
    id: 'uv-windows-store-python',
    match: (raw) => isWindows(raw) && WINDOWS_STORE_PYTHON.test(raw.text),
    render: () => ({
      title: 'Windows Store Python shim detected',
      explanation: "Windows pointed uv at the Microsoft Store Python shim instead of a real Python interpreter. claude-anyteam needs Python 3.12+, and Kimi teammates require Python 3.13 for the separate `kimi-cli` uv tool.",
      action: 'Install real Python from https://www.python.org/downloads/ and tick “Add to PATH”, or run `uv python install 3.12`. For Kimi teammates, also run `uv python install 3.13`.',
      severity: HARD,
    }),
  },
  {
    id: 'windows-antivirus-quarantine',
    match: (raw) => isWindows(raw) && WINDOWS_ANTIVIRUS_QUARANTINE.test(raw.text),
    render: () => ({
      title: 'Antivirus quarantined install files',
      explanation: 'Your antivirus quarantined a Python wheel during install.',
      action: 'Add an exclusion for `%LOCALAPPDATA%\\uv` and `%LOCALAPPDATA%\\claude-anyteam` in Windows Defender (Settings → Virus & threat protection → Exclusions). For corp EDR, ask IT to whitelist those paths.',
      severity: HARD,
    }),
  },
  {
    id: 'conda-interference',
    match: (raw) => CONDA_INTERFERENCE.test(raw.text) && hasCondaEnv(raw),
    render: () => ({
      title: 'Conda environment is interfering',
      explanation: "We detected an active Conda environment, which interferes with uv's isolated installs.",
      action: "Run `conda deactivate` first (twice if you have a 'base' env). Then re-run `npx --yes claude-anyteam`.",
      severity: HARD,
    }),
  },
  {
    id: 'uv-python-missing',
    match: (raw) => PYTHON_MISSING.test(raw.text),
    render: () => ({
      title: 'Python 3.12+ not found',
      explanation: 'uv could not find a Python 3.12+ interpreter to install claude-anyteam. Kimi teammates additionally require Python 3.13 for the separate `kimi-cli` uv tool.',
      action: 'Install Python 3.12+ from https://www.python.org/downloads/ (Windows: tick “Add to PATH”) or run `uv python install 3.12`. For Kimi teammates, also run `uv python install 3.13`.',
      severity: HARD,
    }),
  },
  {
    id: 'uv-lock-contention',
    match: (raw) => UV_LOCK_CONTENTION.test(raw.text),
    render: () => ({
      title: 'Another uv install is running',
      explanation: 'Another uv install is already running, or a previous one crashed and left a stale lock.',
      action: 'Wait 30 seconds and try again. If still stuck, kill leftover processes (`pkill uv` on Linux/Mac, Task Manager on Windows) and run `uv cache clean`.',
      severity: HARD,
    }),
  },
  {
    id: 'read-only-home',
    match: (raw) => READ_ONLY_HOME.test(raw.text),
    render: () => ({
      title: 'Home directory is read-only',
      explanation: 'Your home directory is read-only — common in Docker containers without a writable volume mount.',
      action: 'Mount ~/.claude as writable: `docker run -v ~/.claude:/root/.claude:rw …`. Or set `HOME=/tmp/claude-home` before running.',
      severity: HARD,
    }),
  },
  {
    id: 'macos-arch-mismatch',
    match: (raw) => raw.platform === 'darwin' && MACOS_ARCH_MISMATCH.test(raw.text),
    render: () => ({
      title: 'macOS architecture mismatch',
      explanation: "Your terminal is running under Rosetta but you're on Apple Silicon (or the other way around) — uv picked an incompatible Python.",
      action: "Confirm: `arch` should print `arm64` on M1/M2/M3. If it prints `i386`, you're emulated — open a native terminal (Terminal → Get Info → uncheck 'Open using Rosetta'). Then re-run.",
      severity: HARD,
    }),
  },
  {
    id: 'uv-network',
    match: (raw) => NETWORK.test(raw.text),
    render: () => ({
      title: 'Network problem reaching PyPI',
      explanation: 'uv could not reach the Python package index (PyPI), likely because of a proxy, VPN, DNS, TLS, or temporary network issue.',
      action: 'Set `HTTPS_PROXY` and `HTTP_PROXY` if needed, then re-run `npx --yes claude-anyteam@latest`; if you are on a VPN, try disconnecting briefly.',
      severity: HARD,
    }),
  },
  {
    id: 'uv-disk-full',
    match: (raw) => DISK_FULL.test(raw.text),
    render: () => ({
      title: 'Disk is full',
      explanation: 'uv could not finish installing because the filesystem ran out of free space.',
      action: 'Free up disk space (at least 200MB recommended), then re-run `npx --yes claude-anyteam@latest`.',
      severity: HARD,
    }),
  },
  {
    id: 'uv-permission-denied',
    match: (raw) => PERMISSION_DENIED.test(raw.text),
    render: () => ({
      title: 'Filesystem permission denied',
      explanation: 'uv could not write into an install/cache directory, likely because a directory under your home folder is owned by another user.',
      action: 'On macOS/Linux run `sudo chown -R $USER ~/.local`; on Windows, run the terminal as your normal (not Administrator) user.',
      severity: HARD,
    }),
  },
  {
    id: 'python-version-too-old',
    match: (raw) => isPythonVersionTooOld(raw.pythonVersion ?? extractDetectedPythonVersion(raw.text)),
    render: () => ({
      title: 'Python version is too old',
      explanation: 'Your detected Python is older than 3.12, but claude-anyteam needs Python 3.12 or newer.',
      action: 'Run `uv python install 3.12` or install Python 3.12+ from https://www.python.org/downloads/.',
      severity: HARD,
    }),
  },

  // Kimi-specific patterns: order matters. The more-specific signed-in /
  // version-old checks MUST come before the general "not found" so a fixture
  // like "Kimi credentials not found at ..." matches signed-in (which it
  // really is) rather than not-found (which technically also matches the
  // /kimi.*not.*found/ regex).
  {
    id: 'kimi-not-signed-in',
    match: (raw) => KIMI_NOT_SIGNED_IN.test(raw.text),
    render: () => ({
      title: 'Kimi CLI is not signed in',
      explanation: 'The `kimi` command is installed, but the installer could not find usable authentication credentials.',
      action: 'Kimi CLI is installed but not signed in. Run `kimi login` to authenticate.',
      severity: HARD,
    }),
  },
  {
    id: 'kimi-version-old',
    match: (raw) => KIMI_VERSION_OLD.test(raw.text),
    render: () => ({
      title: 'Kimi CLI version is too old',
      explanation: 'The detected Kimi CLI version is too old for claude-anyteam Kimi teammates.',
      action: 'Your Kimi CLI is too old. Update with `uv tool install --reinstall --python 3.13 kimi-cli`.',
      severity: HARD,
    }),
  },
  {
    id: 'kimi-not-found',
    match: (raw) => KIMI_NOT_FOUND.test(raw.text),
    render: () => ({
      title: 'Kimi CLI not installed',
      explanation: 'The installer saw `kimi-*` teammate configuration, but the `kimi` command is not available on PATH.',
      action: 'Kimi CLI is not installed but the user has `kimi-*` teammates configured. Install via `uv tool install --python 3.13 kimi-cli` then run `kimi login`.',
      severity: HARD,
    }),
  },
  {
    id: 'claude-not-found',
    match: (raw) => raw.kind === 'claude-not-found' || CLAUDE_NOT_FOUND.test(raw.text),
    render: () => ({
      title: 'Claude Code CLI not detected',
      explanation: 'The installer skipped Claude Code plugin registration because `claude` was not available on PATH; the core spawn shim install can still be used.',
      action: 'Install Claude Code from https://docs.claude.com/en/docs/claude-code/setup and re-run, or proceed without the plugin by symlinking the spawn shim from your editor.',
      severity: SOFT,
    }),
  },
  {
    id: 'windows-non-ascii-username',
    match: (raw) => isWindows(raw) && NON_ASCII.test(homePath(raw)),
    render: () => ({
      title: 'Windows username has non-ASCII characters',
      explanation: "Your Windows username contains non-ASCII characters; some Python tooling still doesn't handle that correctly.",
      action: 'Set `PYTHONUTF8=1` for the install: `set PYTHONUTF8=1 && npx --yes claude-anyteam`. Or set `UV_TOOL_DIR=C:\\uv-tools` to put the install under an ASCII-only path.',
      severity: HARD,
    }),
  },
];

const fallbackPattern = {
  id: 'fallback-unrecognized',
  match: () => true,
  render: () => ({
    title: 'Unrecognized installer error',
    explanation: 'We hit an installer error we do not recognize yet, and we treat unrecognized installer errors as bugs.',
    action: `Copy-paste the raw details at ${ISSUE_URL}.`,
    severity: HARD,
  }),
};

export function translate(rawError, context = {}) {
  const raw = normalize(rawError, context);
  const pattern = patterns.find((candidate) => candidate.match(raw)) ?? fallbackPattern;
  const rendered = pattern.render(raw);
  return {
    id: pattern.id,
    title: rendered.title,
    explanation: rendered.explanation,
    action: rendered.action,
    severity: rendered.severity,
    raw: raw.text,
  };
}

export const translateInstallError = translate;
export default translate;

export const urls = {
  issues: ISSUE_URL,
  pythonDownloads: PYTHON_DOWNLOADS_URL,
  claudeCodeSetup: CLAUDE_CODE_SETUP_URL,
};
