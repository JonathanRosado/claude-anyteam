#!/usr/bin/env node

import readline from 'node:readline/promises';
import process from 'node:process';
import yoctoSpinner from 'yocto-spinner';
import {
  TOOL_NAME,
  UV_INSTALL_DIR,
  detectPython,
  detectUv,
  findInstalledTool,
  formatCommand,
  installTool,
  installUv,
  isCI,
  isInteractive,
  manualInstallLines,
} from '../lib/detect.js';
import {
  TEAMMATE_BINARY_KEY,
  TEAMMATE_COMMAND_KEY,
  writeClaudeSettings,
} from '../lib/settings.js';
import { renderBanner, renderBox, theme } from '../lib/art.js';

function parseArgs(argv) {
  const args = { postinstall: false, settingsPath: undefined, help: false };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === '--postinstall') {
      args.postinstall = true;
    } else if (arg === '--settings-path') {
      if (index + 1 >= argv.length) {
        throw new Error('--settings-path requires a value');
      }
      args.settingsPath = argv[index + 1];
      index += 1;
    } else if (arg === '--help' || arg === '-h') {
      args.help = true;
    } else {
      throw new Error(`Unknown option: ${arg}`);
    }
  }
  return args;
}

function usage() {
  return [
    'Usage: codex-teammate-setup [--settings-path <path>] [--postinstall]',
    '',
    'Installs uv if needed, installs the Python codex-teammate tool, and writes',
    '~/.claude/settings.json with absolute launcher paths for Claude Code.',
  ].join('\n');
}

async function confirmInstallUv() {
  const prompt = `${theme.symbols.info} ${theme.heading('uv is missing.')} Install it now into ${theme.accent(UV_INSTALL_DIR)}? ${theme.muted('[Y/n] ')}`;
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  try {
    const answer = (await rl.question(prompt)).trim().toLowerCase();
    return answer === '' || answer === 'y' || answer === 'yes';
  } finally {
    rl.close();
  }
}

async function withSpinner(text, enabled, action) {
  if (!enabled) {
    return action();
  }
  const spinner = yoctoSpinner({ text, color: 'cyan' }).start();
  try {
    const result = await action();
    spinner.success(`${theme.success('done')} ${theme.muted(text)}`);
    return result;
  } catch (error) {
    spinner.error(`${theme.danger('failed')} ${theme.muted(text)}`);
    throw error;
  }
}

function printFailure(title, lines) {
  console.error('');
  console.error(renderBox(theme.danger(title), lines, 'red'));
  console.error('');
}

function printSuccess(lines) {
  console.log('');
  console.log(renderBox(theme.success('INSTALL COMPLETE'), lines, 'green'));
  console.log('');
}

function trimmedDetails(error) {
  return String(error?.details || error?.message || '').trim();
}

function postinstallHint(error) {
  const reason = trimmedDetails(error) || error.message;
  console.warn(`codex-teammate: automatic setup skipped (${reason.split(/\r?\n/, 1)[0]}). Run npx --yes --package codex-teammate codex-teammate-setup to finish.`);
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    console.log(usage());
    return 0;
  }

  const postinstall = args.postinstall || process.env.npm_lifecycle_event === 'postinstall';
  const interactive = isInteractive();
  const silent = postinstall;

  if (!silent) {
    console.log(renderBanner());
    console.log(theme.heading('Zero-friction Codex teammate setup for Claude Code.'));
    console.log(theme.muted('We will check Python, install uv if needed, wire up codex-teammate, and patch Claude settings.'));
    console.log('');
  }

  const python = await detectPython();
  if (!python) {
    const instructions = manualInstallLines({ includePython: true });
    if (silent) {
      postinstallHint(new Error('python3 was not found'));
      return 0;
    }
    printFailure('PYTHON 3 REQUIRED', [
      `${theme.symbols.error} ${theme.heading('codex-teammate needs python3 before anything else can happen.')}`,
      `${theme.symbols.info} Install Python 3, then rerun ${theme.accent('npx --yes --package codex-teammate codex-teammate-setup')}.`,
      '',
      ...instructions.map((line) => `${theme.symbols.info} ${line}`),
    ]);
    return 1;
  }

  if (!silent) {
    console.log(`${theme.symbols.success} ${theme.heading('python3 detected')} ${theme.muted(`(${python.version})`)} ${theme.accent(python.path)}`);
  }

  let uv = await detectUv();
  if (!uv) {
    const autoInstall = postinstall || !interactive || (await confirmInstallUv());
    if (!autoInstall) {
      if (silent) {
        postinstallHint(new Error('uv is not installed'));
        return 0;
      }
      printFailure('UV NOT INSTALLED', [
        `${theme.symbols.warn} ${theme.heading('uv is required to install the Python codex-teammate tool.')}`,
        `${theme.symbols.info} Install it manually, then rerun ${theme.accent('npx --yes --package codex-teammate codex-teammate-setup')}.`,
        '',
        ...manualInstallLines().map((line) => `${theme.symbols.info} ${line}`),
      ]);
      return 1;
    }

    try {
      uv = await withSpinner(`Installing uv into ${UV_INSTALL_DIR}`, !silent, () => installUv());
    } catch (error) {
      if (silent) {
        postinstallHint(error);
        return 0;
      }
      printFailure('UV INSTALL FAILED', [
        `${theme.symbols.error} ${theme.heading('Automatic uv installation did not complete.')}`,
        `${theme.symbols.info} Installer output: ${trimmedDetails(error) || 'No extra diagnostics.'}`,
        '',
        ...manualInstallLines().map((line) => `${theme.symbols.info} ${line}`),
      ]);
      return 1;
    }
  }

  if (!silent) {
    console.log(`${theme.symbols.success} ${theme.heading('uv ready')} ${theme.muted(uv.version)} ${theme.accent(uv.path)}`);
  }

  let tool;
  const existingTool = await findInstalledTool({ uvPath: uv.path }).catch(() => null);
  if (existingTool) {
    tool = existingTool;
    if (!silent) {
      console.log(`${theme.symbols.success} ${theme.heading('existing codex-teammate tool detected')} ${theme.accent(tool.binaryPath)}`);
    }
  } else {
    try {
      tool = await withSpinner(`Installing ${TOOL_NAME} with uv tool install`, !silent, () => installTool({ uvPath: uv.path, pythonPath: python.path }));
    } catch (error) {
      if (silent) {
        postinstallHint(error);
        return 0;
      }
      printFailure('TOOL INSTALL FAILED', [
        `${theme.symbols.error} ${theme.heading(`uv could not install ${TOOL_NAME}.`)}`,
        `${theme.symbols.info} Command: ${theme.accent(formatCommand(uv.path, ['--no-config', 'tool', 'install', '--force', '--python', python.path, TOOL_NAME]))}`,
        `${theme.symbols.info} Details: ${trimmedDetails(error) || 'No extra diagnostics.'}`,
      ]);
      return 1;
    }
  }

  let settings;
  try {
    settings = await withSpinner(`Writing Claude settings`, !silent, () => writeClaudeSettings({
      settingsPath: args.settingsPath,
      shimPath: tool.shimPath,
      binaryPath: tool.binaryPath,
    }));
  } catch (error) {
    if (silent) {
      postinstallHint(error);
      return 0;
    }
    printFailure('SETTINGS WRITE FAILED', [
      `${theme.symbols.error} ${theme.heading('Claude settings could not be updated safely.')}`,
      `${theme.symbols.info} Details: ${trimmedDetails(error) || error.message}`,
      `${theme.symbols.info} Target file: ${theme.accent(args.settingsPath || '~/.claude/settings.json')}`,
    ]);
    return 1;
  }

  if (silent) {
    return 0;
  }

  const launchTemplate = `${tool.binaryPath} --team my-team --name codex-alice --cwd /path/to/workspace`;
  const settingsVerb = settings.createdFile ? 'created' : settings.changedAnything ? 'updated' : 'verified';
  const toolVerb = tool.installMode === 'existing' ? 'reused existing install' : 'installed with uv tool install';
  printSuccess([
    `${theme.symbols.success} Claude settings ${settingsVerb}: ${theme.accent(settings.settingsPath)}`,
    `${theme.symbols.info} env.${TEAMMATE_COMMAND_KEY} = ${theme.accent(tool.shimPath)}`,
    `${theme.symbols.info} env.${TEAMMATE_BINARY_KEY} = ${theme.accent(tool.binaryPath)}`,
    `${theme.symbols.info} Tool status = ${theme.accent(toolVerb)}`,
    `${theme.symbols.info} uv tool bin directory = ${theme.accent(tool.binDir)}`,
    '',
    `${theme.symbols.info} Launch template:`,
    `    ${theme.accent(launchTemplate)}`,
    '',
    `${theme.symbols.warn} Restart Claude Code so it reloads ${theme.accent('~/.claude/settings.json')}.`,
  ]);
  console.log(`${theme.symbols.success} ${theme.heading('Your Codex teammate launcher is live.')} Name teammates with a ${theme.accent('codex-')} prefix in Claude Code's external spawn flow.`);
  return 0;
}

main().then(
  (code) => {
    process.exitCode = code;
  },
  (error) => {
    if (process.argv.includes('--postinstall') || process.env.npm_lifecycle_event === 'postinstall' || isCI()) {
      postinstallHint(error);
      process.exitCode = 0;
      return;
    }
    printFailure('UNEXPECTED INSTALLER ERROR', [
      `${theme.symbols.error} ${theme.heading(error.message)}`,
      ...(trimmedDetails(error) && trimmedDetails(error) !== error.message ? [`${theme.symbols.info} ${trimmedDetails(error)}`] : []),
    ]);
    process.exitCode = 1;
  },
);
