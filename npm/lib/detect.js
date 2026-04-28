import { promises as fs, constants as fsConstants } from 'node:fs';
import { homedir, tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(HERE, '..', '..');
const PRIMARY_BINARY = 'claude-anyteam';
const PRIMARY_SHIM = 'claude-anyteam-spawn-shim';
const LEGACY_BINARY = 'codex-teammate';
const LEGACY_SHIM = 'codex-teammate-spawn-shim';

function envFirst(...names) {
  for (const name of names) {
    const value = process.env[name];
    if (value) {
      return value;
    }
  }
  return undefined;
}

export const TOOL_NAME = envFirst('CLAUDE_ANYTEAM_PYTHON_PACKAGE', 'CODEX_TEAMMATE_PYTHON_PACKAGE') || 'claude-anyteam';
export const UV_INSTALL_DIR = envFirst('CLAUDE_ANYTEAM_UV_INSTALL_DIR', 'CODEX_TEAMMATE_UV_INSTALL_DIR') || defaultBinDir();
export const UV_TOOL_BIN_DIR = envFirst('CLAUDE_ANYTEAM_UV_TOOL_BIN_DIR', 'CODEX_TEAMMATE_UV_TOOL_BIN_DIR') || defaultBinDir();
export const UV_TOOL_DIR = envFirst('CLAUDE_ANYTEAM_UV_TOOL_DIR', 'CODEX_TEAMMATE_UV_TOOL_DIR') || defaultToolDir();

function defaultBinDir() {
  if (process.platform === 'win32') {
    return path.join(homedir(), 'AppData', 'Local', 'claude-anyteam', 'bin');
  }
  return path.join(homedir(), '.local', 'bin');
}

function defaultToolDir() {
  if (process.platform === 'win32') {
    return path.join(homedir(), 'AppData', 'Local', 'claude-anyteam', 'tools');
  }
  return undefined;
}

async function resolveInstallTarget() {
  if (TOOL_NAME !== 'claude-anyteam') {
    return TOOL_NAME;
  }
  const localPyproject = path.join(REPO_ROOT, 'pyproject.toml');
  try {
    await fs.access(localPyproject, fsConstants.F_OK);
    return REPO_ROOT;
  } catch {
    return TOOL_NAME;
  }
}

function executableNames(name) {
  if (process.platform !== 'win32') {
    return [name];
  }
  const extnames = (process.env.PATHEXT || '.EXE;.CMD;.BAT;.COM').split(';').filter(Boolean);
  const hasExt = extnames.some((extension) => name.toUpperCase().endsWith(extension));
  return hasExt ? [name] : extnames.map((extension) => `${name}${extension.toLowerCase()}`);
}

async function isExecutable(filePath) {
  try {
    await fs.access(filePath, process.platform === 'win32' ? fsConstants.F_OK : fsConstants.X_OK);
    return true;
  } catch {
    return false;
  }
}

export async function which(name, extraDirs = []) {
  const searchDirs = [];
  const hasSeparator = name.includes(path.sep) || (process.platform === 'win32' && name.includes('/'));
  if (process.platform === 'win32' && /^[A-Za-z]:(?![\\/])/.test(name)) {
    // "D:foo.exe" is drive-relative on Windows, not "D:\foo.exe". Avoid
    // resolving it against Node's hidden per-drive cwd; callers should pass an
    // absolute drive path or let PATH lookup handle a bare executable name.
    return null;
  }
  if (hasSeparator || path.isAbsolute(name)) {
    const candidate = path.resolve(name);
    return (await isExecutable(candidate)) ? candidate : null;
  }
  for (const directory of [...extraDirs, ...(process.env.PATH || '').split(path.delimiter)]) {
    if (!directory) {
      continue;
    }
    searchDirs.push(directory);
  }
  for (const directory of searchDirs) {
    for (const executable of executableNames(name)) {
      const candidate = path.join(directory, executable);
      if (await isExecutable(candidate)) {
        return path.resolve(candidate);
      }
    }
  }
  return null;
}

export function isCI() {
  return Boolean(process.env.CI);
}

export function isInteractive() {
  return Boolean(process.stdin.isTTY && process.stdout.isTTY && !isCI());
}

export function manualInstallLines({ includePython = false } = {}) {
  const lines = [];
  if (process.platform === 'win32') {
    if (includePython) {
      lines.push('PowerShell: winget install Python.Python.3.12');
      lines.push('If winget is not available, download Python 3.12+ from https://www.python.org/downloads/ and tick “Add python.exe to PATH”.');
    }
    lines.push('PowerShell (preferred): winget install Astral-sh.uv');
    // winget ships on Windows 10 1709+ but corporate-locked images often
    // strip it. The official Astral PowerShell installer is the universal
    // fallback — it doesn't require winget or admin and writes uv to the
    // same %USERPROFILE%\.local\bin location uv tools expect.
    lines.push('PowerShell (no winget):  irm https://astral.sh/uv/install.ps1 | iex');
    lines.push('Then rerun from PowerShell: npx --yes --package claude-anyteam claude-anyteam-setup');
    return lines;
  }
  lines.push('macOS/Homebrew: brew install python uv');
  if (includePython) {
    lines.push('Debian/Ubuntu: sudo apt-get update && sudo apt-get install -y python3 curl');
  }
  lines.push('Official uv installer:');
  lines.push('  curl -LsSf https://astral.sh/uv/install.sh -o /tmp/uv-install.sh');
  lines.push(`  UV_INSTALL_DIR=${UV_INSTALL_DIR} UV_NO_MODIFY_PATH=1 sh /tmp/uv-install.sh`);
  lines.push('Then rerun: npx --yes --package claude-anyteam claude-anyteam-setup');
  return lines;
}

export function nodeInstallLines() {
  if (process.platform === 'win32') {
    return [
      'Install Node.js LTS from https://nodejs.org/ (it includes npm).',
      'Then close and reopen PowerShell and rerun: npx --yes claude-anyteam',
    ];
  }
  if (process.platform === 'darwin') {
    return [
      'macOS/Homebrew: brew install node',
      'Or download Node.js LTS from https://nodejs.org/',
      'Then rerun: npx --yes claude-anyteam',
    ];
  }
  return [
    'Debian/Ubuntu: sudo apt-get update && sudo apt-get install -y nodejs npm',
    'Fedora/RHEL:   sudo dnf install nodejs npm',
    'Arch:          sudo pacman -S nodejs npm',
    'Then rerun: npx --yes claude-anyteam',
  ];
}

export function providerPrereqLines() {
  return [
    'Provider CLIs are optional at setup time but required for their prefixes:',
    '  codex-*  → OpenAI Codex CLI 0.120+ (`npm install -g @openai/codex`, then run `codex` to sign in)',
    '  gemini-* → Gemini CLI (`npm install -g @google/gemini-cli`, then run `gemini` or configure GEMINI_API_KEY/Vertex)',
    '  kimi-*   → Kimi CLI (`pip install kimi-cli` or the upstream installer, then run `kimi login`)',
  ];
}

export function formatCommand(command, args = []) {
  if (process.platform === 'win32') {
    const formattedCommand = formatPowerShellCommand(command);
    return [formattedCommand, ...args.map(formatPowerShellArgument)].join(' ');
  }
  return [formatPosixArgument(command), ...args.map(formatPosixArgument)].join(' ');
}

function formatPosixArgument(value) {
  const text = String(value);
  if (/^[A-Za-z0-9_@%+=:,./-]+$/.test(text)) {
    return text;
  }
  return `'${text.replace(/'/g, "'\\''")}'`;
}

function formatPowerShellCommand(value) {
  const formatted = formatPowerShellArgument(value);
  return formatted === String(value) ? formatted : `& ${formatted}`;
}

function formatPowerShellArgument(value) {
  const text = String(value);
  if (/^[A-Za-z0-9_@%+=:,./\\-]+$/u.test(text)) {
    return text;
  }
  return `'${text.replace(/'/g, "''")}'`;
}

export function formatDisplayPath(value) {
  const text = String(value);
  return process.platform === 'win32' ? text.replace(/\//g, '\\') : text;
}

export function uvToolEnv(baseEnv = process.env) {
  const env = {
    ...baseEnv,
    UV_NO_PROGRESS: '1',
    UV_TOOL_BIN_DIR,
  };
  if (UV_TOOL_DIR) {
    env.UV_TOOL_DIR = UV_TOOL_DIR;
  }
  if (process.platform === 'win32') {
    env.PYTHONUTF8 = baseEnv.PYTHONUTF8 || '1';
    env.PYTHONIOENCODING = baseEnv.PYTHONIOENCODING || 'utf-8';
  }
  return env;
}

export function runCommand(command, args = [], options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd: options.cwd,
      env: options.env,
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let stdout = '';
    let stderr = '';
    child.stdout.on('data', (chunk) => {
      stdout += chunk;
    });
    child.stderr.on('data', (chunk) => {
      stderr += chunk;
    });
    child.on('error', reject);
    child.on('close', (code) => resolve({ code: code ?? 0, stdout, stderr }));
  });
}

export async function detectPython(options = {}) {
  const diagnostics = Boolean(options.diagnostics);
  const probe = 'import sys; print(sys.executable); print(sys.version.split()[0])';
  const candidates = process.platform === 'win32'
    ? [
        { name: 'python', prefixArgs: [] },
        { name: 'py', prefixArgs: ['-3'] },
        { name: 'python3', prefixArgs: [] },
      ]
    : [{ name: 'python3', prefixArgs: [] }];
  let issue = null;

  for (const candidate of candidates) {
    const command = await which(candidate.name);
    if (!command) {
      continue;
    }

    if (isWindowsStorePythonAlias(command)) {
      issue ||= {
        kind: 'windows-store-python-stub',
        path: command,
        details: 'Windows App Execution Alias points python.exe at the Microsoft Store stub.',
      };
      continue;
    }

    const result = await runCommand(command, [...candidate.prefixArgs, '-c', probe]);
    if (result.code !== 0) {
      const stubIssue = windowsStorePythonIssue(command, result);
      if (stubIssue) {
        issue ||= stubIssue;
      }
      continue;
    }

    const [resolvedPath, version] = result.stdout.trim().split(/\r?\n/);
    const python = { path: path.resolve(resolvedPath || command), version: version || 'unknown' };
    return diagnostics ? { python, issue: null } : python;
  }

  return diagnostics ? { python: null, issue } : null;
}

function isWindowsStorePythonAlias(command) {
  if (process.platform !== 'win32') {
    return false;
  }
  const normalized = command.replace(/\//g, '\\').toLowerCase();
  return normalized.includes('\\microsoft\\windowsapps\\') && /\\python(?:3)?\.exe$/.test(normalized);
}

function windowsStorePythonIssue(command, result) {
  if (process.platform !== 'win32') {
    return null;
  }
  const combined = `${result.stdout || ''}\n${result.stderr || ''}`;
  if (
    isWindowsStorePythonAlias(command)
    || /microsoft store|app execution alias|python was not found|disable this shortcut/i.test(combined)
  ) {
    return {
      kind: 'windows-store-python-stub',
      path: command,
      details: combined.trim() || 'python.exe resolved to the Microsoft Store app execution alias.',
    };
  }
  return null;
}

export async function detectUv() {
  const candidate = await which('uv', [UV_INSTALL_DIR]);
  if (!candidate) {
    return null;
  }
  const result = await runCommand(candidate, ['--version']);
  if (result.code !== 0) {
    return null;
  }
  return { path: candidate, version: result.stdout.trim() || result.stderr.trim() || 'uv' };
}

export async function installUv() {
  if (process.platform === 'win32') {
    const attempts = [
      { command: 'winget', args: ['install', '--id', 'Astral-sh.uv', '-e', '--accept-package-agreements', '--accept-source-agreements'] },
      { command: 'scoop', args: ['install', 'uv'] },
      { command: 'choco', args: ['install', 'uv', '-y'] },
    ];
    const failures = [];
    for (const attempt of attempts) {
      if (!(await which(attempt.command))) {
        failures.push(`${attempt.command}: not found`);
        continue;
      }
      const result = await runCommand(attempt.command, attempt.args, { env: process.env });
      if (result.code === 0) {
        const installed = await detectUv();
        if (installed) {
          return installed;
        }
        failures.push(`${attempt.command}: completed, but uv was not found on PATH yet`);
      } else {
        failures.push(`${attempt.command}: ${(result.stderr || result.stdout).trim() || `exit ${result.code}`}`);
      }
    }

    const shell = await which('pwsh') || await which('powershell.exe') || await which('powershell');
    if (shell) {
      const result = await runCommand(shell, [
        '-NoProfile',
        '-ExecutionPolicy',
        'Bypass',
        '-Command',
        'irm https://astral.sh/uv/install.ps1 | iex',
      ], { env: { ...process.env, UV_INSTALL_DIR } });
      if (result.code === 0) {
        const installed = await detectUv();
        if (installed) {
          return installed;
        }
        failures.push('official PowerShell installer: completed, but uv was not found on PATH yet');
      } else {
        failures.push(`official PowerShell installer: ${(result.stderr || result.stdout).trim() || `exit ${result.code}`}`);
      }
    } else {
      failures.push('PowerShell: not found');
    }

    const error = new Error('Automatic uv installation did not finish on Windows.');
    error.details = failures.join('\n');
    throw error;
  }
  const workingDir = await fs.mkdtemp(path.join(tmpdir(), 'claude-anyteam-uv-'));
  const scriptPath = path.join(workingDir, 'install-uv.sh');
  try {
    const response = await fetch('https://astral.sh/uv/install.sh');
    if (!response.ok) {
      throw new Error(`Unable to download the official uv installer (${response.status} ${response.statusText}).`);
    }
    const script = await response.text();
    await fs.mkdir(UV_INSTALL_DIR, { recursive: true });
    await fs.writeFile(scriptPath, script, 'utf8');
    const result = await runCommand('sh', [scriptPath], {
      env: {
        ...process.env,
        UV_INSTALL_DIR,
        UV_NO_MODIFY_PATH: '1',
      },
    });
    if (result.code !== 0) {
      const error = new Error('The official uv installer exited unsuccessfully.');
      error.details = (result.stderr || result.stdout).trim();
      throw error;
    }
    const installed = await detectUv();
    if (!installed) {
      throw new Error(`uv did not appear at ${path.join(UV_INSTALL_DIR, 'uv')} after installation.`);
    }
    return installed;
  } finally {
    await fs.rm(workingDir, { recursive: true, force: true });
  }
}

export async function installPython() {
  const attempts = [];
  if (process.platform === 'win32') {
    attempts.push(
      { command: 'winget', args: ['install', '--id', 'Python.Python.3.12', '-e', '--accept-package-agreements', '--accept-source-agreements'] },
      { command: 'scoop', args: ['install', 'python'] },
      { command: 'choco', args: ['install', 'python', '-y'] },
    );
  } else if (process.platform === 'darwin') {
    attempts.push({ command: 'brew', args: ['install', 'python'] });
  } else {
    attempts.push(
      { command: 'apt-get', args: ['update'] },
      { command: 'apt-get', args: ['install', '-y', 'python3', 'python3-venv', 'curl'] },
      { command: 'dnf', args: ['install', '-y', 'python3', 'curl'] },
      { command: 'pacman', args: ['-S', '--noconfirm', 'python', 'curl'] },
    );
  }

  const failures = [];
  for (const attempt of attempts) {
    if (!(await which(attempt.command))) {
      failures.push(`${attempt.command}: not found`);
      continue;
    }
    const command = process.platform === 'win32' || process.platform === 'darwin'
      ? attempt.command
      : (process.getuid?.() === 0 ? attempt.command : (await which('sudo') ? 'sudo' : attempt.command));
    const args = command === attempt.command ? attempt.args : [attempt.command, ...attempt.args];
    const result = await runCommand(command, args, { env: process.env });
    if (result.code !== 0) {
      failures.push(`${command} ${args.join(' ')}: ${(result.stderr || result.stdout).trim() || `exit ${result.code}`}`);
      continue;
    }
    const detected = await detectPython();
    if (detected) {
      return detected;
    }
    failures.push(`${command} ${args.join(' ')}: completed, but Python was not found on PATH yet`);
  }

  const error = new Error('Automatic Python installation did not finish.');
  error.details = failures.join('\n');
  throw error;
}

function toolExecutablePath(binDir, name) {
  return path.join(binDir, process.platform === 'win32' ? `${name}.exe` : name);
}

function toolWorkingDir() {
  return homedir();
}

async function resolveToolPaths(binDir) {
  const primaryBinary = toolExecutablePath(binDir, PRIMARY_BINARY);
  const primaryShim = toolExecutablePath(binDir, PRIMARY_SHIM);
  if (await isExecutable(primaryBinary) && await isExecutable(primaryShim)) {
    return { binaryPath: primaryBinary, shimPath: primaryShim };
  }

  const legacyBinary = toolExecutablePath(binDir, LEGACY_BINARY);
  const legacyShim = toolExecutablePath(binDir, LEGACY_SHIM);
  if (await isExecutable(legacyBinary) && await isExecutable(legacyShim)) {
    return { binaryPath: legacyBinary, shimPath: legacyShim };
  }

  return null;
}

export async function resolveToolBinDir({ uvPath }) {
  const env = uvToolEnv(process.env);
  const binDirResult = await runCommand(uvPath, ['--no-config', 'tool', 'dir', '--bin'], { env, cwd: toolWorkingDir() });
  if (binDirResult.code !== 0) {
    const error = new Error('uv could not resolve its tool bin directory.');
    error.details = (binDirResult.stderr || binDirResult.stdout).trim();
    throw error;
  }
  return { env, binDir: path.resolve(binDirResult.stdout.trim()) };
}

export async function findInstalledTool({ uvPath }) {
  const { env, binDir } = await resolveToolBinDir({ uvPath });
  const resolvedPaths = await resolveToolPaths(binDir);
  if (resolvedPaths) {
    return { env, binDir, ...resolvedPaths, installMode: 'existing' };
  }
  return null;
}

export async function installTool({ uvPath, pythonPath, refresh = false, version = null }) {
  if (!refresh) {
    const existing = await findInstalledTool({ uvPath }).catch(() => null);
    if (existing) {
      return existing;
    }
  }

  const { env, binDir } = await resolveToolBinDir({ uvPath });
  const symlinkCheck = await checkWindowsSymlinkPrivilege();
  if (!symlinkCheck.ok) {
    const error = new Error('Windows blocked symlink creation, which uv tool install needs.');
    error.code = 'WINDOWS_SYMLINK_PERMISSION';
    error.details = symlinkCheck.details || symlinkCheck.message || 'Enable Developer Mode or rerun from an elevated PowerShell session.';
    throw error;
  }
  // --prerelease=allow: claude-anyteam currently pins fastmcp==3.0.0b1 (a
  // beta), and uv refuses pre-release deps by default. Without this flag the
  // install fails on a clean machine with the cryptic uv "no solution found"
  // dependency-resolver error. Until fastmcp ships a stable 3.x we always
  // allow pre-releases for our own tool install.
  const args = ['--no-config', 'tool', 'install', '--force', '--prerelease=allow'];
  if (pythonPath) {
    args.push('--python', pythonPath);
  }
  let installTarget = await resolveInstallTarget();
  // Pin the Python tool to the npm wrapper's version. PyPI's index can lag
  // npm publish by minutes; without a pin, uv may resolve to a stale older
  // version even though our wrapper is new. Result: wrapper bug-fixes don't
  // run because the Python tool is older. Skip pinning when installing
  // from a local checkout (resolveInstallTarget returns a path).
  if (version && installTarget === TOOL_NAME) {
    installTarget = `${TOOL_NAME}==${version}`;
  }
  args.push(installTarget);
  const result = await runCommand(uvPath, args, { env, cwd: toolWorkingDir() });
  if (result.code !== 0) {
    const fallback = await findInstalledTool({ uvPath }).catch(() => null);
    if (fallback) {
      return fallback;
    }
    const error = new Error(`uv could not install ${installTarget}.`);
    error.details = (result.stderr || result.stdout).trim();
    error.command = formatCommand(uvPath, args);
    throw error;
  }
  const resolvedPaths = await resolveToolPaths(binDir);
  if (!resolvedPaths) {
    const error = new Error(`Expected tool executables missing in ${binDir}`);
    error.details = `uv reported bin directory ${binDir}, but neither the claude-anyteam nor legacy codex-teammate binaries were available.`;
    throw error;
  }
  return { env, binDir, ...resolvedPaths, installMode: refresh ? 'refreshed' : 'installed' };
}

export async function ensureWindowsLongPaths() {
  if (process.platform !== 'win32') {
    return { supported: false, enabled: true, changed: false };
  }

  const key = 'HKLM\\SYSTEM\\CurrentControlSet\\Control\\FileSystem';
  const value = 'LongPathsEnabled';
  try {
    const query = await runCommand('reg', ['query', key, '/v', value]);
    const queryOutput = `${query.stdout || ''}\n${query.stderr || ''}`;
    if (query.code === 0 && /LongPathsEnabled\s+REG_DWORD\s+0x0*1\b/i.test(queryOutput)) {
      return { supported: true, enabled: true, changed: false };
    }

    const add = await runCommand('reg', ['add', key, '/v', value, '/t', 'REG_DWORD', '/d', '1', '/f']);
    if (add.code === 0) {
      return { supported: true, enabled: true, changed: true };
    }
    return {
      supported: true,
      enabled: false,
      changed: false,
      details: (add.stderr || add.stdout || queryOutput).trim(),
    };
  } catch (error) {
    return {
      supported: true,
      enabled: false,
      changed: false,
      details: error.message,
    };
  }
}

export async function checkWindowsSymlinkPrivilege() {
  if (process.platform !== 'win32') {
    return { ok: true };
  }

  const workingDir = await fs.mkdtemp(path.join(tmpdir(), 'claude-anyteam-symlink-'));
  const target = path.join(workingDir, 'target.txt');
  const link = path.join(workingDir, 'link.txt');
  try {
    await fs.writeFile(target, 'probe', 'utf8');
    await fs.symlink(target, link);
    return { ok: true };
  } catch (error) {
    return {
      ok: false,
      code: error.code,
      message: error.message,
      details: `${error.code || 'symlink failed'}: ${error.message}`,
    };
  } finally {
    await fs.rm(workingDir, { recursive: true, force: true });
  }
}

export function windowsInstallAdvice(error, { binDir = UV_TOOL_BIN_DIR } = {}) {
  if (process.platform !== 'win32') {
    return [];
  }

  const details = `${error?.code || ''}\n${error?.message || ''}\n${error?.details || ''}`;
  const lines = [];

  const symlinkBlocked = /WINDOWS_SYMLINK_PERMISSION|EPERM.*symlink|privilege.*symbolic|symbolic link/i.test(details);
  if (symlinkBlocked) {
    lines.push('Windows blocked symlink creation. Enable Developer Mode (Settings > System > For developers) or rerun from an Administrator PowerShell session.');
  }

  if (/ENAMETOOLONG|MAX_PATH|path too long|filename or extension is too long|ERROR_FILENAME_EXCED_RANGE|0x80010135/i.test(details)) {
    lines.push('Windows long paths appear to be disabled. Open PowerShell as Administrator and run: New-ItemProperty -Path "HKLM:\\SYSTEM\\CurrentControlSet\\Control\\FileSystem" -Name LongPathsEnabled -Value 1 -PropertyType DWord -Force');
    lines.push('Then close and reopen your terminal before rerunning npx --yes claude-anyteam.');
  }

  if (!symlinkBlocked && /EBUSY|EPERM|EACCES|permission denied|being used by another process|resource busy|locked|access is denied/i.test(details)) {
    const exclusionTargets = [...new Set([binDir, UV_TOOL_DIR].filter(Boolean).map(formatDisplayPath))];
    const exclusionArgs = exclusionTargets.map((target) => `'${String(target).replace(/'/g, "''")}'`).join(', ');
    lines.push(`Antivirus or Windows Defender may be locking a uv tool file. Retry once; if it persists, add ${exclusionTargets.join(' and ')} to your AV exclusion list.`);
    lines.push(`Windows Defender PowerShell (Admin): Add-MpPreference -ExclusionPath ${exclusionArgs}`);
  }

  return [...new Set(lines)];
}
