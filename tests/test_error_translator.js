import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { translate } from '../npm/lib/error-translator.js';

function assertTranslation(raw, context, expected) {
  const result = translate(raw, context);
  assert.equal(result.id, expected.id);
  assert.equal(result.title, expected.title);
  assert.equal(result.severity, expected.severity);
  assert.match(result.action, expected.action);
  if (expected.explanation) {
    assert.match(result.explanation, expected.explanation);
  }
}

function fixture(name) {
  return readFileSync(new URL(`./fixtures/installer/${name}`, import.meta.url), 'utf8')
    .split('\n')
    .filter((line) => !line.startsWith('#'))
    .join('\n')
    .trim();
}

test('uv-prerelease-blocked', () => {
  assertTranslation(
    "Because pre-release versions weren't enabled, try --prerelease=allow",
    {},
    {
      id: 'uv-prerelease-blocked',
      title: 'Pre-release dependency blocked by uv',
      severity: 'hard',
      action: /uv tool install --prerelease=allow claude-anyteam/,
    },
  );
});

test('uv-no-solution', () => {
  assertTranslation(
    'error: no solution found when resolving dependencies for claude-anyteam',
    {},
    {
      id: 'uv-no-solution',
      title: 'Python dependency conflict',
      severity: 'hard',
      action: /uv tool install --reinstall claude-anyteam/,
    },
  );
});


test('plugin-update-soft', () => {
  assertTranslation(
    { stderr: fixture('plugin-update-soft.txt'), step: 'plugin-update' },
    {},
    {
      id: 'plugin-update-soft',
      title: 'Claude Code plugin update skipped',
      severity: 'soft',
      action: /claude plugin update claude-anyteam@claude-anyteam/,
    },
  );
});

test('uv-tls-cert-validation', () => {
  assertTranslation(
    fixture('uv-tls-cert-validation.txt'),
    {},
    {
      id: 'uv-tls-cert-validation',
      title: 'Corporate TLS certificate not trusted',
      severity: 'hard',
      action: /NODE_EXTRA_CA_CERTS=.*corp-ca\.pem/,
    },
  );
});

test('uv-corporate-proxy', () => {
  assertTranslation(
    fixture('uv-corporate-proxy.txt'),
    {},
    {
      id: 'uv-corporate-proxy',
      title: 'Corporate proxy blocked PyPI',
      severity: 'hard',
      action: /HTTPS_PROXY=.*UV_HTTP_TIMEOUT=120.*curl -I https:\/\/pypi\.org/,
    },
  );
});


test('windows-antivirus-quarantine', () => {
  assertTranslation(
    fixture('windows-antivirus-quarantine.txt'),
    { platform: 'win32' },
    {
      id: 'windows-antivirus-quarantine',
      title: 'Antivirus quarantined install files',
      severity: 'hard',
      action: /%LOCALAPPDATA%\\uv.*%LOCALAPPDATA%\\claude-anyteam.*Windows Defender/,
    },
  );
});

test('uv-python-missing', () => {
  assertTranslation(
    'error: could not find an interpreter for Python 3.12',
    {},
    {
      id: 'uv-python-missing',
      title: 'Python 3.12+ not found',
      severity: 'hard',
      action: /uv python install 3\.12.*uv python install 3\.13/,
      explanation: /Kimi teammates.*Python 3\.13/,
    },
  );
});


test('uv-lock-contention', () => {
  assertTranslation(
    fixture('uv-lock-contention.txt'),
    {},
    {
      id: 'uv-lock-contention',
      title: 'Another uv install is running',
      severity: 'hard',
      action: /Wait 30 seconds.*pkill uv.*uv cache clean/,
    },
  );
});

test('read-only-home', () => {
  assertTranslation(
    fixture('read-only-home.txt'),
    {},
    {
      id: 'read-only-home',
      title: 'Home directory is read-only',
      severity: 'hard',
      action: /docker run -v ~\/\.claude:\/root\/\.claude:rw.*HOME=\/tmp\/claude-home/,
    },
  );
});

test('macos-arch-mismatch', () => {
  assertTranslation(
    fixture('macos-arch-mismatch.txt'),
    { platform: 'darwin' },
    {
      id: 'macos-arch-mismatch',
      title: 'macOS architecture mismatch',
      severity: 'hard',
      action: /arch.*arm64.*i386.*Rosetta/,
    },
  );
});

test('conda-interference', () => {
  assertTranslation(
    fixture('conda-interference.txt'),
    { env: { CONDA_DEFAULT_ENV: 'base' } },
    {
      id: 'conda-interference',
      title: 'Conda environment is interfering',
      severity: 'hard',
      action: /conda deactivate.*npx --yes claude-anyteam/,
    },
  );
});

test('uv-network', () => {
  assertTranslation(
    'error: connection timed out while fetching https://pypi.org/simple/claude-anyteam',
    {},
    {
      id: 'uv-network',
      title: 'Network problem reaching PyPI',
      severity: 'hard',
      action: /HTTPS_PROXY/,
    },
  );
});

test('uv-disk-full', () => {
  assertTranslation(
    'OSError: [Errno 28] No space left on device',
    {},
    {
      id: 'uv-disk-full',
      title: 'Disk is full',
      severity: 'hard',
      action: /200MB/,
    },
  );
});

test('uv-permission-denied', () => {
  assertTranslation(
    'Permission denied (os error 13) while writing ~/.local/bin',
    {},
    {
      id: 'uv-permission-denied',
      title: 'Filesystem permission denied',
      severity: 'hard',
      action: /sudo chown -R \$USER ~\/\.local/,
    },
  );
});

test('uv-windows-longpath', () => {
  assertTranslation(
    'The specified path exceeds the maximum supported path length',
    { platform: 'win32' },
    {
      id: 'uv-windows-longpath',
      title: 'Windows long-path limit hit',
      severity: 'hard',
      action: /LongPathsEnabled/,
    },
  );
});

test('uv-windows-store-python', () => {
  assertTranslation(
    'python.exe not found. Install Python from the Microsoft Store?',
    { platform: 'win32' },
    {
      id: 'uv-windows-store-python',
      title: 'Windows Store Python shim detected',
      severity: 'hard',
      action: /uv python install 3\.12.*uv python install 3\.13/,
      explanation: /Kimi teammates.*Python 3\.13/,
    },
  );
});

test('python-version-too-old', () => {
  assertTranslation(
    'Detected Python 3.11.8 at /usr/bin/python3',
    { pythonVersion: '3.11.8' },
    {
      id: 'python-version-too-old',
      title: 'Python version is too old',
      severity: 'hard',
      action: /Python 3\.12\+/,
    },
  );
});


test('kimi-not-found', () => {
  assertTranslation(
    fixture('kimi-not-found.txt'),
    {},
    {
      id: 'kimi-not-found',
      title: 'Kimi CLI not installed',
      severity: 'hard',
      action: /uv tool install --python 3\.13 kimi-cli.*kimi login/,
    },
  );
});

test('kimi-not-found windows cmd shape', () => {
  assertTranslation(
    "'kimi' is not recognized as an internal or external command, operable program or batch file.",
    { platform: 'win32' },
    {
      id: 'kimi-not-found',
      title: 'Kimi CLI not installed',
      severity: 'hard',
      action: /uv tool install --python 3\.13 kimi-cli/,
    },
  );
});

test('kimi-not-signed-in', () => {
  assertTranslation(
    fixture('kimi-not-signed-in.txt'),
    {},
    {
      id: 'kimi-not-signed-in',
      title: 'Kimi CLI is not signed in',
      severity: 'hard',
      action: /kimi login/,
    },
  );
});

test('kimi-version-old', () => {
  assertTranslation(
    fixture('kimi-version-old.txt'),
    {},
    {
      id: 'kimi-version-old',
      title: 'Kimi CLI version is too old',
      severity: 'hard',
      action: /uv tool install --reinstall --python 3\.13 kimi-cli/,
    },
  );
});


test('windows-non-ascii-username', () => {
  assertTranslation(
    fixture('windows-non-ascii-username.txt'),
    { platform: 'win32', home: 'C:\\Users\\José' },
    {
      id: 'windows-non-ascii-username',
      title: 'Windows username has non-ASCII characters',
      severity: 'hard',
      action: /PYTHONUTF8=1.*UV_TOOL_DIR/,
    },
  );
});

test('claude-not-found', () => {
  assertTranslation(
    'Claude Code CLI not detected on PATH',
    {},
    {
      id: 'claude-not-found',
      title: 'Claude Code CLI not detected',
      severity: 'soft',
      action: /claude-code\/setup/,
    },
  );
});

test('fallback-unrecognized', () => {
  const raw = 'mystery installer failure\nwith untouched raw output';
  const result = translate(raw);

  assert.equal(result.id, 'fallback-unrecognized');
  assert.equal(result.title, 'Unrecognized installer error');
  assert.equal(result.severity, 'hard');
  assert.match(result.action, /issues\/new/);
  assert.equal(result.raw, raw);
});
