#!/usr/bin/env node

import { spawn } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { arch, release } from 'node:os';
import readline from 'node:readline/promises';
import process from 'node:process';
import yoctoSpinner from 'yocto-spinner';
import {
  TOOL_NAME,
  UV_INSTALL_DIR,
  UV_TOOL_BIN_DIR,
  detectPython,
  detectUv,
  ensureWindowsLongPaths,
  findInstalledTool,
  formatDisplayPath,
  formatCommand,
  installTool,
  installPython,
  installUv,
  isCI,
  isInteractive,
  manualInstallLines,
  nodeInstallLines,
  providerPrereqLines,
  runCommand,
  uvToolEnv,
  windowsInstallAdvice,
  which,
} from '../lib/detect.js';
import { renderBanner, renderBox, theme } from '../lib/art.js';
import { formatInstallerDiagnostic, isPythonVersionTooOld, translate } from '../lib/error-translator.js';

const CLAUDE_PLUGIN_MARKETPLACE_SOURCE = 'JonathanRosado/claude-anyteam';
const CLAUDE_PLUGIN_MARKETPLACE_NAME = 'claude-anyteam';
const CLAUDE_PLUGIN_SPEC = `${CLAUDE_PLUGIN_MARKETPLACE_NAME}@${CLAUDE_PLUGIN_MARKETPLACE_NAME}`;
const CLAUDE_PLUGIN_MANUAL_COMMANDS = [
  formatCommand('claude', ['plugin', 'marketplace', 'add', CLAUDE_PLUGIN_MARKETPLACE_SOURCE]),
  formatCommand('claude', ['plugin', 'install', CLAUDE_PLUGIN_SPEC]),
  // Idempotent when already up-to-date; pulls the latest manifest otherwise.
  // Running it on every install is how re-runs of the installer pick up new
  // skill content without forcing the user to uninstall by hand.
  formatCommand('claude', ['plugin', 'update', CLAUDE_PLUGIN_SPEC]),
];
const CLAUDE_PLUGIN_MARKETPLACE_ALREADY_EXISTS = /\balready (?:on disk|exists)\b/i;
const CLAUDE_PLUGIN_ALREADY_INSTALLED = /\balready installed\b/i;
const PACKAGE_JSON = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8'));
const INSTALLER_VERSION = PACKAGE_JSON.version || 'unknown';

let detectedPythonVersion = 'not checked';
let detectedUvVersion = 'not checked';

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
  const settingsPath = process.platform === 'win32' ? '~\\.claude\\settings.json' : '~/.claude/settings.json';
  const claudeJsonPath = process.platform === 'win32' ? '~\\.claude.json' : '~/.claude.json';
  const prereqSummary = process.platform === 'win32'
    ? 'uses Windows single-terminal mode when tmux/psmux is unavailable, probes for the Codex CLI 0.120+, Gemini CLI, and Kimi CLI'
    : 'verifies tmux/psmux on PATH and probes for the Codex CLI 0.120+, Gemini CLI, and Kimi CLI';
  return [
    'Usage: npx --yes claude-anyteam [--settings-path <path>] [--postinstall]',
    '',
    'Installs uv if needed, installs the Python claude-anyteam tool, delegates',
    `the ${settingsPath} + ${claudeJsonPath} writes to the Python installer`,
    `(which ${prereqSummary}),`,
    'and registers the Claude Code plugin when the claude CLI is available on PATH.',
  ].join('\n');
}

async function confirmInstallDependency({ name, reason }) {
  const prompt = `${theme.symbols.info} ${theme.heading(`${name} is missing.`)} ${reason} ${theme.muted('Want me to try installing it for you? [Y/n] ')}`;
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

function issueContext(rawError) {
  return {
    installerVersion: INSTALLER_VERSION,
    os: `${process.platform} ${release()} ${arch()}`,
    pythonVersion: detectedPythonVersion,
    uvVersion: detectedUvVersion,
    nodeVersion: process.version,
    rawError,
  };
}

function fallbackNextSteps() {
  return [
    `Read the message above and follow the command it suggests.`,
    `Re-run ${theme.accent('npx --yes claude-anyteam')} after that finishes.`,
    `If the same error comes back, open the report link below so we can help.`,
  ];
}

function hasGuidanceLine(lines) {
  return lines.some((line) => /try this next|next step|install it|rerun|re-run|run these commands|run these commands/i.test(String(line)));
}

function diagnosticTail(title, rawError, options = {}) {
  const context = issueContext(rawError);
  return formatInstallerDiagnostic({
    title,
    summary: options.summary || title,
    rawError,
    nextStepsPerOs: options.nextStepsPerOs || fallbackNextSteps(),
    ...context,
    includeWhatHappened: options.includeWhatHappened ?? false,
  }).map((line) => (line.startsWith('  ') ? `    ${theme.accent(line.trim())}` : `${theme.symbols.info} ${theme.heading(line)}`));
}

function printFailure(title, lines, options = {}) {
  const body = [...lines];
  const rawError = String(options.rawError || lines.join('\n'));
  if (!hasGuidanceLine(body)) {
    body.push('');
    body.push(`${theme.symbols.info} ${theme.heading('Try this next')}:`);
    body.push(...fallbackNextSteps().map((step, index) => `    ${index + 1}. ${step}`));
  }
  if (!body.some((line) => /Still stuck\? Report it/i.test(String(line)))) {
    const tail = diagnosticTail(title, rawError, {
      summary: options.summary,
      nextStepsPerOs: options.nextStepsPerOs,
      includeWhatHappened: false,
    });
    const stuckIndex = tail.findIndex((line) => /Still stuck\? Report it/i.test(String(line)));
    body.push('');
    body.push(...tail.slice(stuckIndex));
  }
  console.error('');
  console.error(renderBox(theme.danger(title), body, 'red'));
  console.error('');
}

function printSuccess(lines) {
  console.log('');
  console.log(renderBox(theme.success('INSTALL COMPLETE'), lines, 'green'));
  console.log('');
}

function printWarning(title, lines, options = {}) {
  const body = [...lines];
  if (options.issue !== false) {
    const rawError = String(options.rawError || lines.join('\n'));
    const tail = diagnosticTail(title, rawError, {
      summary: options.summary,
      nextStepsPerOs: options.nextStepsPerOs,
      includeWhatHappened: false,
    });
    const tryIndex = tail.findIndex((line) => /Try this next/i.test(String(line)));
    if (!hasGuidanceLine(body) && tryIndex >= 0) {
      const stuckIndex = tail.findIndex((line) => /Still stuck\? Report it/i.test(String(line)));
      body.push('');
      body.push(...tail.slice(tryIndex, stuckIndex));
    }
    const stuckIndex = tail.findIndex((line) => /Still stuck\? Report it/i.test(String(line)));
    body.push('');
    body.push(...tail.slice(stuckIndex));
  }
  console.log('');
  console.log(renderBox(theme.warn(title), body, 'yellow'));
  console.log('');
}

function printVersionBanner() {
  console.log(theme.muted(`claude-anyteam installer v${INSTALLER_VERSION}`));
}

function printSection(title, detail) {
  console.log('');
  console.log(`${theme.symbols.info} ${theme.accent(title)} ${theme.muted(detail)}`);
}

function rawDetailsLines(raw) {
  const text = String(raw || '');
  if (!text) {
    return ['    No extra diagnostics.'];
  }
  return text.split(/\r?\n/).map((line) => `    ${line}`);
}

function translatedDiagnosticLines(translated, { command, recovered } = {}) {
  const statusSymbol = translated.severity === 'soft' ? theme.symbols.success : theme.symbols.error;
  const lines = [
    `${statusSymbol} ${theme.heading(translated.title)}`,
    `${theme.symbols.info} ${theme.heading('What happened')}: ${translated.explanation}`,
  ];
  if (recovered) {
    lines.push(`${theme.symbols.success} ${theme.heading('Recovered')}: ${recovered}`);
  }
  lines.push(`${theme.symbols.info} ${theme.heading('Try this next')}:`);
  const actionLines = String(translated.action || 'Re-run the installer after fixing the problem.').split(/\r?\n/).filter(Boolean);
  lines.push(...actionLines.map((line, index) => `    ${index + 1}. ${line}`));
  if (command) {
    lines.push(`${theme.symbols.info} ${theme.heading('Command')}: ${theme.accent(command)}`);
  }
  lines.push(`${theme.symbols.info} ${theme.heading('Raw details')}:`);
  lines.push(...rawDetailsLines(translated.raw));
  return lines;
}

function trimmedDetails(error) {
  return String(error?.details || error?.message || '').trim();
}

function pythonLabel() {
  return process.platform === 'win32' ? 'Python 3' : 'python3';
}

function settingsDisplayPath() {
  return process.platform === 'win32' ? '~\\.claude\\settings.json' : '~/.claude/settings.json';
}

function claudeJsonDisplayPath() {
  return process.platform === 'win32' ? '~\\.claude.json' : '~/.claude.json';
}

function workspaceExamplePath() {
  return process.platform === 'win32' ? 'C:\\path\\to\\workspace' : '/path/to/workspace';
}

function powershellLongPathsCommand() {
  return 'New-ItemProperty -Path "HKLM:\\SYSTEM\\CurrentControlSet\\Control\\FileSystem" -Name LongPathsEnabled -Value 1 -PropertyType DWord -Force';
}

function pythonMissingTranslation(issue) {
  if (issue?.kind === 'windows-store-python-stub') {
    return {
      id: 'uv-windows-store-python',
      title: 'Windows Store Python shim detected',
      explanation: 'Windows resolved python.exe to the Microsoft Store app execution alias, not a working Python interpreter.',
      action: 'From PowerShell run `winget install Python.Python.3.12`; if Windows still opens the Store, disable python.exe and python3.exe under Settings > Apps > Advanced app settings > App execution aliases.',
      severity: 'hard',
      raw: `Microsoft Store Python app execution alias at ${formatDisplayPath(issue.path)}: ${issue.details}`,
    };
  }
  return translate('no python interpreter found');
}

function windowsAdvice(error, options = {}) {
  return windowsInstallAdvice(error, options);
}

function windowsAdviceLines(advice) {
  return advice.map((line) => `${theme.symbols.info} ${line}`);
}

function postinstallHint(error) {
  const reason = trimmedDetails(error) || error.message;
  const rawError = reason || 'automatic setup skipped';
  const lines = [
    `claude-anyteam: automatic setup skipped (${rawError.split(/\r?\n/, 1)[0]}).`,
    ...formatInstallerDiagnostic({
      title: 'Automatic setup skipped',
      summary: 'npm could not finish claude-anyteam setup automatically.',
      rawError,
      nextStepsPerOs: ['Run `npx --yes claude-anyteam` in a normal terminal to finish setup.'],
      ...issueContext(rawError),
    }),
  ];
  console.warn(lines.join('\n'));
}

function claudePluginManualSummary() {
  return `skipped (install manually: ${CLAUDE_PLUGIN_MANUAL_COMMANDS.join(' && ')})`;
}

function claudePluginManualLines() {
  return CLAUDE_PLUGIN_MANUAL_COMMANDS.map((command) => `    ${theme.accent(command)}`);
}

function claudeNotFoundTranslation() {
  return translate({
    kind: 'claude-not-found',
    raw: 'claude CLI not detected on PATH',
  });
}

function skippedClaudePluginResult(reasonLines) {
  return {
    status: 'skipped',
    summary: claudePluginManualSummary(),
    warningTitle: 'CLAUDE CODE PLUGIN SKIPPED',
    warningLines: reasonLines,
  };
}

function failedClaudePluginResult(error) {
  return skippedClaudePluginResult([
    `${theme.symbols.warn} ${theme.heading('Claude settings were written, but the Claude Code plugin could not be registered automatically.')}`,
    `${theme.symbols.info} Details: ${trimmedDetails(error) || 'No extra diagnostics.'}`,
    `${theme.symbols.info} Run these commands manually:`,
    '',
    ...claudePluginManualLines(),
  ]);
}

// Delegate the three-file install (~/.claude/settings.json, ~/.claude.json,
// install-state.json + plugin-data dir) to the Python installer. The Python
// installer is the single source of truth for prereq checks, display-mode
// install instructions, teammateMode handling, and state-file writes.
//
// Primary invocation is `uv tool run --from claude-anyteam claude-anyteam
// install --assume-yes`: it resolves the tool's venv without depending on
// the user's shell PATH being refreshed post-`uv tool install`.
//
// stdio is fully inherited so the Python installer's platform-aware
// instructions reach the user verbatim. We do not re-wrap Python's errors; its messages
// are more actionable than any JS prose we could layer on top.
//
// The caller is expected to pass --settings-path only when explicitly
// overridden by a user; the Python installer already defaults to
// ~/.claude/settings.json.
function runPythonInstaller({ uvPath, settingsPath, stdio = 'inherit' }) {
  const args = [
    '--no-config',
    'tool',
    'run',
    '--prerelease=allow',
    '--from', TOOL_NAME,
    TOOL_NAME,
    'install',
    '--assume-yes',
  ];
  if (settingsPath) {
    args.push('--settings-path', settingsPath);
  }
  return new Promise((resolve, reject) => {
    const child = spawn(uvPath, args, {
      env: {
        ...uvToolEnv(process.env),
        CLAUDE_ANYTEAM_NPM_PARENT: '1',
        CLAUDE_ANYTEAM_NPM_VERSION: INSTALLER_VERSION,
        // Belt-and-suspenders: the Python child often loses TTY detection
        // through the npx → uv tool run chain (especially on Windows).
        // Set FORCE_COLOR + CLAUDE_ANYTEAM_FORCE_COLOR explicitly so the
        // Python theme module's _supports_color() always says yes here,
        // independent of whether sys.stdout.isatty() returns True.
        FORCE_COLOR: process.env.NO_COLOR ? '0' : '1',
        CLAUDE_ANYTEAM_FORCE_COLOR: process.env.NO_COLOR ? '0' : '1',
      },
      stdio,
    });
    child.on('error', reject);
    child.on('exit', (code, signal) => {
      if (signal) {
        reject(new Error(`Python installer terminated by signal ${signal}`));
        return;
      }
      resolve({ code: code ?? 1 });
    });
  });
}

async function runClaudePluginCommand(claudePath, args, alreadyPattern) {
  const result = await runCommand(claudePath, args, { env: process.env });
  const combined = `${result.stdout}\n${result.stderr}`;
  if (result.code !== 0 && !alreadyPattern.test(combined)) {
    const error = new Error(`Command failed: ${formatCommand('claude', args)}`);
    error.details = combined.trim();
    error.command = formatCommand('claude', args);
    throw error;
  }
  return { verified: alreadyPattern.test(combined) };
}

// Run `install` then `update` on every invocation so a re-run of
// `npx --yes claude-anyteam` picks up the latest plugin manifest (and its
// cached skills). `install` is a no-op when the plugin is already present;
// `update` is idempotent when already on the latest version and pulls the
// newer manifest otherwise. Together they cover fresh and upgrade paths
// without a destructive uninstall step.
async function registerClaudePlugin({ claudePath }) {
  await runClaudePluginCommand(
    claudePath,
    ['plugin', 'marketplace', 'add', CLAUDE_PLUGIN_MARKETPLACE_SOURCE],
    CLAUDE_PLUGIN_MARKETPLACE_ALREADY_EXISTS,
  );
  await runClaudePluginCommand(
    claudePath,
    ['plugin', 'install', CLAUDE_PLUGIN_SPEC],
    CLAUDE_PLUGIN_ALREADY_INSTALLED,
  );
  await runClaudePluginCommand(
    claudePath,
    ['plugin', 'update', CLAUDE_PLUGIN_SPEC],
    // Exits 0 on "already at the latest version" — no tolerance pattern
    // needed. Any non-zero here is a genuine update failure.
    /^$/,
  );
  return { status: 'refreshed', summary: 'installed + checked for updates' };
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
  const nodeMajor = Number.parseInt(process.versions.node.split('.', 1)[0] || '0', 10);

  if (!silent) {
    console.log(renderBanner());
    printVersionBanner();
    console.log(theme.heading('Friendly claude-anyteam setup for Claude Code.'));
    console.log(theme.muted('I will check the tools you need, offer to install anything missing, wire up Claude Code, and keep the raw details visible if something breaks.'));
    console.log('');
    printSection('1/3 Detect', 'First I check what is already on this computer.');
  }

  if (nodeMajor < 18) {
    if (silent) {
      postinstallHint(new Error(`Node.js ${process.versions.node} is too old; Node.js 18+ is required`));
      return 0;
    }
    printFailure('NODE.JS VERSION TOO OLD', [
      `${theme.symbols.error} ${theme.heading(`This installer is running on Node.js ${process.versions.node}, but it needs Node.js 18 or newer.`)}`,
      `${theme.symbols.info} Node.js is the JavaScript runtime that runs npx. Install the current LTS version, then rerun ${theme.accent('npx --yes claude-anyteam')}.`,
      '',
      ...nodeInstallLines().map((line) => `${theme.symbols.info} ${line}`),
    ], { rawError: `Node.js ${process.versions.node} < 18` });
    return 1;
  }

  let pythonCheck = await detectPython({ diagnostics: true });
  let python = pythonCheck.python;
  detectedPythonVersion = python?.version || 'not found';
  if (!python) {
    if (silent) {
      postinstallHint(new Error(`${pythonLabel()} was not found`));
      return 0;
    }
    let shouldInstall = false;
    if (!interactive) {
      printWarning('SKIPPING PYTHON AUTO-INSTALL', [
        `${theme.symbols.info} I cannot ask questions because this terminal is non-interactive (for example, stdin is piped or CI is running).`,
        `${theme.symbols.info} Please install Python manually using the steps below, then rerun ${theme.accent('npx --yes claude-anyteam')}.`,
      ]);
    } else {
      shouldInstall = await confirmInstallDependency({
        name: 'Python 3.12+',
        reason: 'Python is the programming language that runs claude-anyteam.',
      });
    }
    if (shouldInstall) {
      try {
        python = await withSpinner('Installing Python 3.12+ with your system package manager', !silent, () => installPython());
        pythonCheck = { python, issue: null };
        detectedPythonVersion = python.version || 'unknown';
      } catch (error) {
        const translated = pythonMissingTranslation(pythonCheck.issue);
        printFailure('PYTHON INSTALL FAILED', [
          `${theme.symbols.error} ${theme.heading('I tried to install Python, but the computer would not let me finish.')}`,
          `${theme.symbols.info} Installer output: ${trimmedDetails(error) || 'No extra diagnostics.'}`,
          '',
          `${theme.symbols.info} ${theme.heading('Try this next')}:`,
          ...manualInstallLines({ includePython: true }).map((line, index) => `    ${index + 1}. ${line}`),
          '',
          ...translatedDiagnosticLines(translated),
        ], { rawError: trimmedDetails(error) || String(error) });
        return 1;
      }
    }
  }

  if (!python) {
    const translated = pythonMissingTranslation(pythonCheck.issue);
    printFailure('PYTHON 3.12+ NOT FOUND', [
      `${theme.symbols.warn} ${theme.heading('We need Python 3.12 or newer because claude-anyteam is a Python tool.')}`,
      ...translatedDiagnosticLines(translated),
      '',
      ...manualInstallLines({ includePython: true }).map((line) => `${theme.symbols.info} ${line}`),
    ], { rawError: translated.raw });
    return 1;
  }

  if (!silent) {
    console.log(`${theme.symbols.success} ${theme.heading(`${pythonLabel()} detected`)} ${theme.muted(`(${python.version})`)} ${theme.accent(formatDisplayPath(python.path))}`);
  }

  if (isPythonVersionTooOld(python.version)) {
    if (silent) {
      postinstallHint(new Error(`Python ${python.version} is too old; Python 3.12+ is required`));
      return 0;
    }
    if (interactive && await confirmInstallDependency({
      name: 'a newer Python',
      reason: `I found Python ${python.version}, but claude-anyteam needs Python 3.12 or newer.`,
    })) {
      try {
        python = await withSpinner('Installing a newer Python 3.12+', !silent, () => installPython());
        detectedPythonVersion = python.version || 'unknown';
      } catch (error) {
        const translated = translate({
          raw: `Detected Python ${python.version} at ${formatDisplayPath(python.path)}\n${trimmedDetails(error)}`,
          pythonVersion: python.version,
        });
        printFailure('PYTHON UPGRADE FAILED', [
          ...translatedDiagnosticLines(translated),
          '',
          ...manualInstallLines({ includePython: true }).map((line) => `${theme.symbols.info} ${line}`),
        ], { rawError: translated.raw });
        return 1;
      }
      if (!isPythonVersionTooOld(python.version)) {
        console.log(`${theme.symbols.success} ${theme.heading(`${pythonLabel()} updated`)} ${theme.muted(`(${python.version})`)} ${theme.accent(formatDisplayPath(python.path))}`);
      }
    }
  }

  if (isPythonVersionTooOld(python.version)) {
    const translated = translate({
      raw: `Detected Python ${python.version} at ${formatDisplayPath(python.path)}`,
      pythonVersion: python.version,
    });
    printFailure('PYTHON VERSION TOO OLD', translatedDiagnosticLines(translated), { rawError: translated.raw });
    return 1;
  }

  let uv = await detectUv();
  detectedUvVersion = uv?.version || 'not found';
  if (!uv) {
    if (silent) {
      postinstallHint(new Error('uv is not installed'));
      return 0;
    }

    let autoInstall = false;
    if (!interactive) {
      printWarning('SKIPPING UV AUTO-INSTALL', [
        `${theme.symbols.info} I cannot ask questions because this terminal is non-interactive (for example, stdin is piped or CI is running).`,
        `${theme.symbols.info} Please install uv manually using the steps below, then rerun ${theme.accent('npx --yes claude-anyteam')}.`,
      ]);
    } else {
      autoInstall = await confirmInstallDependency({
        name: 'uv',
        reason: 'uv is the small installer that downloads and runs the Python claude-anyteam tool.',
      });
    }

    if (!autoInstall) {
      printFailure('UV NOT INSTALLED', [
        `${theme.symbols.warn} ${theme.heading('uv is required because it installs claude-anyteam safely in its own Python environment.')}`,
        `${theme.symbols.info} Install uv manually, then rerun ${theme.accent('npx --yes claude-anyteam')}.`,
        '',
        ...manualInstallLines().map((line) => `${theme.symbols.info} ${line}`),
      ], { rawError: 'uv was not found on PATH' });
      return 1;
    }

    try {
      uv = await withSpinner(`Installing uv into ${UV_INSTALL_DIR}`, !silent, () => installUv());
      detectedUvVersion = uv.version || 'unknown';
    } catch (error) {
      if (silent) {
        postinstallHint(error);
        return 0;
      }
      printFailure('UV INSTALL FAILED', [
        `${theme.symbols.error} ${theme.heading('I tried to install uv, but the computer would not let me finish.')}`,
        `${theme.symbols.info} Installer output: ${trimmedDetails(error) || 'No extra diagnostics.'}`,
        '',
        ...manualInstallLines().map((line) => `${theme.symbols.info} ${line}`),
      ], { rawError: trimmedDetails(error) || String(error) });
      return 1;
    }
  }

  if (!silent) {
    detectedUvVersion = uv.version || 'unknown';
    console.log(`${theme.symbols.success} ${theme.heading('uv ready')} ${theme.muted(uv.version)} ${theme.accent(formatDisplayPath(uv.path))}`);
    printSection('2/3 Install', 'Now I install or reuse the claude-anyteam command.');
  }

  if (process.platform === 'win32') {
    const longPaths = await ensureWindowsLongPaths();
    if (!silent && longPaths.changed) {
      printWarning('WINDOWS LONG PATHS ENABLED', [
        `${theme.symbols.success} ${theme.heading('Enabled LongPathsEnabled in the Windows registry for uv tool installs.')}`,
        `${theme.symbols.info} If uv still reports path-too-long errors, close and reopen PowerShell or reboot Windows, then rerun ${theme.accent('npx --yes claude-anyteam')}.`,
      ]);
    } else if (!silent && longPaths.supported && !longPaths.enabled) {
      printWarning('WINDOWS LONG PATHS MAY BE DISABLED', [
        `${theme.symbols.warn} ${theme.heading('uv tool install can exceed the default 260-character Windows path limit.')}`,
        `${theme.symbols.info} Automatic registry update did not succeed: ${trimmedDetails(longPaths) || 'No extra diagnostics.'}`,
        `${theme.symbols.info} ${theme.heading('Try this next')}:`,
        `    1. Open PowerShell as Administrator and run: ${theme.accent(powershellLongPathsCommand())}`,
      ]);
    }
  }

  let tool;
  const existingTool = await findInstalledTool({ uvPath: uv.path }).catch(() => null);
  // Always run `uv tool install --force --prerelease=allow` so a cached older
  // claude-anyteam binary gets refreshed to the latest published wheel. Without
  // this, a user who installed at v0.5.x and re-runs `npx --yes claude-anyteam`
  // never sees handholding/prompt logic shipped in later releases.
  const installLabel = existingTool ? 'Refreshing' : 'Installing';
  try {
    tool = await withSpinner(`${installLabel} ${TOOL_NAME} with uv tool install`, !silent, () => installTool({ uvPath: uv.path, pythonPath: python.path, refresh: true }));
    if (!silent) {
      const status = tool.installMode === 'refreshed' ? 'refreshed to latest' : 'installed';
      console.log(`${theme.symbols.success} ${theme.heading(`claude-anyteam tool ${status}`)} ${theme.accent(formatDisplayPath(tool.binaryPath))}`);
    }
  } catch (error) {
    if (existingTool) {
      // Refresh failed but we still have a working older copy — degrade
      // gracefully rather than blocking on a transient registry error.
      tool = existingTool;
      if (!silent) {
        console.log(`${theme.symbols.warn} ${theme.heading('Could not refresh claude-anyteam')} ${theme.muted('— continuing with existing copy')} ${theme.accent(formatDisplayPath(tool.binaryPath))}`);
        const refreshDetail = (error.details || error.message || '').split(/\r?\n/, 1)[0];
        if (refreshDetail) {
          console.log(`${theme.symbols.info} ${theme.muted(`Refresh error: ${refreshDetail}`)}`);
        }
      }
    } else {
      if (silent) {
        postinstallHint(error);
        return 0;
      }
      const command = error.command || formatCommand(uv.path, ['--no-config', 'tool', 'install', '--force', '--prerelease=allow', '--python', python.path, TOOL_NAME]);
      const translated = translate(error.details || error.message, {
        platform: process.platform,
        pythonVersion: python.version,
      });
      const advice = windowsAdvice(error, { binDir: UV_TOOL_BIN_DIR });
      const translatedForDisplay = process.platform === 'win32' && translated.id === 'uv-permission-denied' && advice.length
        ? { ...translated, action: advice.join(' ') }
        : translated;
      printFailure('TOOL INSTALL FAILED', [
        ...translatedDiagnosticLines(translatedForDisplay, { command }),
        ...(advice.length ? ['', ...windowsAdviceLines(advice)] : []),
      ], { rawError: translated.raw || trimmedDetails(error) || error.message });
      return 1;
    }
  }

  // Delegate Claude settings + claude.json + state file to the Python installer.
  // We do NOT wrap this in withSpinner — the Python installer writes its own
  // status lines to stdout, and overlaying a spinner would fight with that output.
  if (!silent) {
    console.log('');
    printSection('3/3 Wire up', 'Now I connect claude-anyteam to Claude Code settings.');
    const pythonInstallerSummary = process.platform === 'win32'
      ? `— Windows single-terminal compatibility, Codex/Gemini/Kimi CLI prereq checks, ${settingsDisplayPath()}, ${claudeJsonDisplayPath()}, install-state.json`
      : `— tmux + Codex/Gemini/Kimi CLI prereq checks, ${settingsDisplayPath()}, ${claudeJsonDisplayPath()}, install-state.json`;
    console.log(`${theme.symbols.info} ${theme.heading('Running claude-anyteam install (Python)')} ${theme.muted(pythonInstallerSummary)}`);
  }
  let installerResult;
  try {
    installerResult = await runPythonInstaller({
      uvPath: uv.path,
      settingsPath: args.settingsPath,
      stdio: silent ? 'ignore' : 'inherit',
    });
  } catch (error) {
    if (silent) {
      postinstallHint(error);
      return 0;
    }
    // Only fires when the Python installer could not be spawned at all
    // (e.g. uv binary vanished mid-run). Real install errors arrive via
    // non-zero exit code with inherited stderr already printed to the user.
    printFailure('UNABLE TO RUN PYTHON INSTALLER', [
      `${theme.symbols.error} ${theme.heading('Could not invoke the Python installer through uv.')}`,
      `${theme.symbols.info} Details: ${trimmedDetails(error) || error.message}`,
      `${theme.symbols.info} Retry with ${theme.accent(formatCommand(tool.binaryPath, ['install', '--assume-yes']))} after ensuring uv is on PATH.`,
      ...windowsAdviceLines(windowsAdvice(error, { binDir: tool.binDir })),
    ], { rawError: trimmedDetails(error) || error.message });
    return 1;
  }
  if (installerResult.code !== 0) {
    if (silent) {
      postinstallHint(new Error(`Python installer exited with code ${installerResult.code}`));
      return 0;
    }
    // Python installer already streamed its own actionable message (tmux
    // install hints, teammateMode conflict explanation, etc.) through
    // inherited stderr. Do not re-wrap; exit non-zero quietly.
    if (installerResult.code === 3) {
      printFailure('PYTHON INSTALLER ABORTED', [
        `${theme.symbols.warn} The Python installer stopped with exit code 3 even though the npm wrapper passed --assume-yes.`,
        `${theme.symbols.info} This is probably a real installer bug, not something you did wrong.`,
      ], { rawError: `Python installer exited with code ${installerResult.code}` });
    }
    return 1;
  }

  let claudePlugin = null;
  const claudePath = await which('claude');
  if (!claudePath) {
    const translated = claudeNotFoundTranslation();
    claudePlugin = skippedClaudePluginResult([
      ...translatedDiagnosticLines(translated, {
        recovered: 'Skipped Claude Code plugin registration and continued with the core claude-anyteam install.',
      }),
      '',
      `${theme.symbols.info} Run these commands once ${theme.accent('claude')} is available:`,
      '',
      ...claudePluginManualLines(),
    ]);
    claudePlugin.warningTitle = 'CLAUDE CODE PLUGIN RECOVERED';
    if (!silent) {
      printWarning(claudePlugin.warningTitle, claudePlugin.warningLines);
    }
  } else {
    try {
      claudePlugin = await withSpinner(`Registering Claude Code plugin`, !silent, () => registerClaudePlugin({ claudePath }));
    } catch (error) {
      claudePlugin = failedClaudePluginResult(error);
      if (!silent) {
        printWarning(claudePlugin.warningTitle, claudePlugin.warningLines);
      }
    }
  }

  if (silent) {
    return 0;
  }

  // The Python installer already printed the env-var and teammateMode changes
  // to stdout. Success box avoids duplicating that — it covers only what Python
  // doesn't know about (plugin registration status, launch template, restart
  // reminder) so there's no noise.
  const launchTemplate = `${formatCommand(tool.binaryPath, ['--team', 'my-team', '--name', 'codex-alice', '--cwd', workspaceExamplePath()])}  # or --name gemini-alice / kimi-architect`;
  const toolVerb = tool.installMode === 'existing' ? 'reused existing install' : 'installed with uv tool install';
  const providerLines = providerPrereqLines().map((line) => `${theme.symbols.info} ${line}`);
  printSuccess([
    `${theme.symbols.success} Tool status = ${theme.accent(toolVerb)}`,
    `${theme.symbols.info} Claude Code plugin: ${theme.accent(claudePlugin.summary)}`,
    `${theme.symbols.info} uv tool bin directory = ${theme.accent(formatDisplayPath(tool.binDir))}`,
    '',
    `${theme.symbols.info} Launch template:`,
    `    ${theme.accent(launchTemplate)}`,
    '',
    ...providerLines,
    '',
    `${theme.symbols.warn} Restart Claude Code so it reloads ${theme.accent(settingsDisplayPath())}.`,
  ]);
  console.log(`${theme.symbols.success} ${theme.heading('Your claude-anyteam launcher is live.')} Codex/Gemini/Kimi-powered teammates use the ${theme.accent('codex-')}, ${theme.accent('gemini-')}, or ${theme.accent('kimi-')} prefix today in Claude Code's external spawn flow.`);
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
    ], { rawError: trimmedDetails(error) || error.stack || error.message });
    process.exitCode = 1;
  },
);
