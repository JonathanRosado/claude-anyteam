import { promises as fs, constants as fsConstants } from 'node:fs';
import { homedir, tmpdir } from 'node:os';
import path from 'node:path';
import { spawn } from 'node:child_process';

export const TOOL_NAME = process.env.CODEX_TEAMMATE_PYTHON_PACKAGE || 'codex-teammate';
export const UV_INSTALL_DIR = process.env.CODEX_TEAMMATE_UV_INSTALL_DIR || defaultBinDir();
export const UV_TOOL_BIN_DIR = process.env.CODEX_TEAMMATE_UV_TOOL_BIN_DIR || defaultBinDir();

function defaultBinDir() {
  if (process.platform === 'win32') {
    return path.join(homedir(), 'AppData', 'Local', 'codex-teammate', 'bin');
  }
  return path.join(homedir(), '.local', 'bin');
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
  if (name.includes(path.sep) || path.isAbsolute(name)) {
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
      lines.push('Install Python 3: winget install Python.Python.3.12');
    }
    lines.push('Install uv: winget install Astral-sh.uv');
    lines.push('Then rerun: npx --yes --package codex-teammate codex-teammate-setup');
    return lines;
  }
  lines.push('macOS/Homebrew: brew install python uv');
  if (includePython) {
    lines.push('Debian/Ubuntu: sudo apt-get update && sudo apt-get install -y python3 curl');
  }
  lines.push('Official uv installer:');
  lines.push('  curl -LsSf https://astral.sh/uv/install.sh -o /tmp/uv-install.sh');
  lines.push(`  UV_INSTALL_DIR=${UV_INSTALL_DIR} UV_NO_MODIFY_PATH=1 sh /tmp/uv-install.sh`);
  lines.push('Then rerun: npx --yes --package codex-teammate codex-teammate-setup');
  return lines;
}

export function formatCommand(command, args = []) {
  return [command, ...args].join(' ');
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

export async function detectPython() {
  const command = await which(process.platform === 'win32' ? 'python' : 'python3');
  if (!command) {
    return null;
  }
  const result = await runCommand(command, ['-c', 'import sys; print(sys.executable); print(sys.version.split()[0])']);
  if (result.code !== 0) {
    return null;
  }
  const [resolvedPath, version] = result.stdout.trim().split(/\r?\n/);
  return { path: path.resolve(resolvedPath || command), version: version || 'unknown' };
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
    throw new Error('Automatic uv installation is only wired up for macOS/Linux shells right now.');
  }
  const workingDir = await fs.mkdtemp(path.join(tmpdir(), 'codex-teammate-uv-'));
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

function toolExecutablePath(binDir, name) {
  return path.join(binDir, process.platform === 'win32' ? `${name}.exe` : name);
}

function toolWorkingDir() {
  return homedir();
}

export async function resolveToolBinDir({ uvPath }) {
  const env = {
    ...process.env,
    UV_NO_PROGRESS: '1',
    UV_TOOL_BIN_DIR,
  };
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
  const binaryPath = toolExecutablePath(binDir, 'codex-teammate');
  const shimPath = toolExecutablePath(binDir, 'codex-teammate-spawn-shim');
  if (await isExecutable(binaryPath) && await isExecutable(shimPath)) {
    return { env, binDir, binaryPath, shimPath, installMode: 'existing' };
  }
  return null;
}

export async function installTool({ uvPath, pythonPath }) {
  const existing = await findInstalledTool({ uvPath }).catch(() => null);
  if (existing) {
    return existing;
  }

  const { env, binDir } = await resolveToolBinDir({ uvPath });
  const args = ['--no-config', 'tool', 'install', '--force'];
  if (pythonPath) {
    args.push('--python', pythonPath);
  }
  args.push(TOOL_NAME);
  const result = await runCommand(uvPath, args, { env, cwd: toolWorkingDir() });
  if (result.code !== 0) {
    const fallback = await findInstalledTool({ uvPath }).catch(() => null);
    if (fallback) {
      return fallback;
    }
    const error = new Error(`uv could not install ${TOOL_NAME}.`);
    error.details = (result.stderr || result.stdout).trim();
    error.command = formatCommand(uvPath, args);
    throw error;
  }
  const binaryPath = toolExecutablePath(binDir, 'codex-teammate');
  const shimPath = toolExecutablePath(binDir, 'codex-teammate-spawn-shim');
  for (const filePath of [binaryPath, shimPath]) {
    if (!(await isExecutable(filePath))) {
      const error = new Error(`Expected tool executable missing: ${filePath}`);
      error.details = `uv reported bin directory ${binDir}, but ${path.basename(filePath)} was not there.`;
      throw error;
    }
  }
  return { env, binDir, binaryPath, shimPath, installMode: 'installed' };
}
