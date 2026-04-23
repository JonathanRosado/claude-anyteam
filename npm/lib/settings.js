import { randomUUID } from 'node:crypto';
import { promises as fs } from 'node:fs';
import { homedir } from 'node:os';
import path from 'node:path';

export const TEAMMATE_COMMAND_KEY = 'CLAUDE_CODE_TEAMMATE_COMMAND';
export const TEAMMATE_BINARY_KEY = 'CODEX_TEAMMATE_BINARY';

export function defaultSettingsPath() {
  return path.resolve(homedir(), '.claude', 'settings.json');
}

async function loadSettings(settingsPath) {
  try {
    const raw = await fs.readFile(settingsPath, 'utf8');
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      throw new Error(`${settingsPath} must contain a JSON object at the top level.`);
    }
    return { settings: parsed, existed: true };
  } catch (error) {
    if (error.code === 'ENOENT') {
      return { settings: {}, existed: false };
    }
    if (error instanceof SyntaxError) {
      throw new Error(`${settingsPath} is not valid JSON: ${error.message}`);
    }
    throw error;
  }
}

function envBlock(settings, settingsPath, create) {
  const current = settings.env;
  if (current === undefined) {
    if (!create) {
      return {};
    }
    settings.env = {};
    return settings.env;
  }
  if (!current || typeof current !== 'object' || Array.isArray(current)) {
    throw new Error(`${settingsPath} has an 'env' entry, but it is not a JSON object.`);
  }
  for (const [key, value] of Object.entries(current)) {
    if (typeof key !== 'string' || typeof value !== 'string') {
      throw new Error(`${settingsPath} has a non-string entry under 'env'; refusing to overwrite it.`);
    }
  }
  return current;
}

async function writeSettings(settingsPath, settings) {
  await fs.mkdir(path.dirname(settingsPath), { recursive: true });
  const tempPath = path.join(path.dirname(settingsPath), `.${path.basename(settingsPath)}.${randomUUID()}.tmp`);
  const handle = await fs.open(tempPath, 'w');
  try {
    await handle.writeFile(`${JSON.stringify(settings, null, 2)}\n`, 'utf8');
    await handle.sync();
    await handle.close();
    await fs.rename(tempPath, settingsPath);
  } catch (error) {
    await handle.close().catch(() => {});
    await fs.rm(tempPath, { force: true }).catch(() => {});
    throw error;
  }
}

export async function writeClaudeSettings({ settingsPath = defaultSettingsPath(), shimPath, binaryPath }) {
  const resolvedSettings = path.resolve(settingsPath);
  const resolvedShim = path.resolve(shimPath);
  const resolvedBinary = path.resolve(binaryPath);
  const { settings, existed } = await loadSettings(resolvedSettings);
  const env = envBlock(settings, resolvedSettings, true);
  const desired = {
    [TEAMMATE_COMMAND_KEY]: resolvedShim,
    [TEAMMATE_BINARY_KEY]: resolvedBinary,
  };
  const changed = {};
  for (const [key, value] of Object.entries(desired)) {
    if (env[key] !== value) {
      env[key] = value;
      changed[key] = value;
    }
  }
  if (!existed || Object.keys(changed).length > 0) {
    await writeSettings(resolvedSettings, settings);
  }
  return {
    settingsPath: resolvedSettings,
    shimPath: resolvedShim,
    binaryPath: resolvedBinary,
    createdFile: !existed,
    changed,
    changedAnything: !existed || Object.keys(changed).length > 0,
  };
}
