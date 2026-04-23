import gradient from 'gradient-string';
import colors from 'yoctocolors';

export const banner = [
  ' ██████╗ ██████╗ ██████╗ ███████╗██╗  ██╗',
  '██╔════╝██╔═══██╗██╔══██╗██╔════╝╚██╗██╔╝',
  '██║     ██║   ██║██║  ██║█████╗   ╚███╔╝ ',
  '██║     ██║   ██║██║  ██║██╔══╝   ██╔██╗ ',
  '╚██████╗╚██████╔╝██████╔╝███████╗██╔╝ ██╗',
  ' ╚═════╝ ╚═════╝ ╚═════╝ ╚══════╝╚═╝  ╚═╝',
  '',
  '████████╗███████╗ █████╗ ███╗   ███╗███╗   ███╗ █████╗ ████████╗███████╗',
  '╚══██╔══╝██╔════╝██╔══██╗████╗ ████║████╗ ████║██╔══██╗╚══██╔══╝██╔════╝',
  '   ██║   █████╗  ███████║██╔████╔██║██╔████╔██║███████║   ██║   █████╗  ',
  '   ██║   ██╔══╝  ██╔══██║██║╚██╔╝██║██║╚██╔╝██║██╔══██║   ██║   ██╔══╝  ',
  '   ██║   ███████╗██║  ██║██║ ╚═╝ ██║██║ ╚═╝ ██║██║  ██║   ██║   ███████╗',
  '   ╚═╝   ╚══════╝╚═╝  ╚═╝╚═╝     ╚═╝╚═╝     ╚═╝╚═╝  ╚═╝   ╚═╝   ╚══════╝',
].join('\n');

const ansiPattern = /\u001B\[[0-?]*[ -/]*[@-~]/g;
const borderPalette = {
  cyan: colors.cyan,
  green: colors.green,
  red: colors.red,
  yellow: colors.yellow,
  magenta: colors.magenta,
  blue: colors.blue,
};
const headerGradient = gradient(['#22d3ee', '#60a5fa', '#a78bfa', '#f472b6']);

export const theme = {
  colors,
  accent: (value) => colors.bold(colors.cyan(value)),
  success: (value) => colors.bold(colors.green(value)),
  warn: (value) => colors.bold(colors.yellow(value)),
  danger: (value) => colors.bold(colors.red(value)),
  muted: (value) => colors.gray(value),
  heading: (value) => colors.bold(colors.white(value)),
  symbols: {
    success: colors.green('✔'),
    info: colors.cyan('●'),
    warn: colors.yellow('▲'),
    error: colors.red('✖'),
  },
};

export function renderBanner() {
  return headerGradient.multiline(banner);
}

export function stripAnsi(value) {
  return String(value ?? '').replace(ansiPattern, '');
}

export function renderBox(title, lines, color = 'cyan') {
  const paint = borderPalette[color] ?? ((value) => value);
  const bodyRows = (Array.isArray(lines) ? lines : [lines]).flatMap((row) => String(row).split('\n'));
  const rows = [String(title), ...bodyRows];
  const width = Math.max(...rows.map((row) => stripAnsi(row).length), 0);
  const fill = (row) => `${row}${' '.repeat(width - stripAnsi(row).length)}`;
  return [paint(`╭${'─'.repeat(width + 2)}╮`), `${paint('│')} ${fill(rows[0])} ${paint('│')}`, paint(`├${'─'.repeat(width + 2)}┤`), ...rows.slice(1).map((row) => `${paint('│')} ${fill(row)} ${paint('│')}`), paint(`╰${'─'.repeat(width + 2)}╯`)].join('\n');
}
