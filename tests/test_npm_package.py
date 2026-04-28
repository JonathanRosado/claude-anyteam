from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

ANSI_RE = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')

import pytest


ROOT = Path(__file__).resolve().parents[1]
NPM_DIR = ROOT / 'npm'


def test_npm_package_metadata_matches_installer_contract() -> None:
    package = json.loads((NPM_DIR / 'package.json').read_text(encoding='utf-8'))

    assert package['name'] == 'claude-anyteam'
    assert package['version'] == '0.7.0'
    assert package['bin']['claude-anyteam-setup'] == 'bin/setup.js'
    assert package['bin']['claude-anyteam'] == 'bin/setup.js'
    assert package['scripts']['postinstall'] == 'node bin/setup.js --postinstall'
    assert package['engines']['node'] == '>=18'
    assert package['dependencies'] == {
        'gradient-string': '3.0.0',
        'yocto-spinner': '1.1.0',
        'yoctocolors': '2.1.2',
    }


def test_npm_installer_files_exist() -> None:
    """npm bundle ships with bin/setup.js + the two lib helpers it imports.

    `lib/settings.js` was removed in 0.3.0 — the Python installer owns
    settings.json writes now. Regression guard: test fails if someone
    re-introduces settings.js or removes a file setup.js depends on.
    """
    expected = [
        NPM_DIR / 'README.md',
        NPM_DIR / 'bin' / 'setup.js',
        NPM_DIR / 'lib' / 'art.js',
        NPM_DIR / 'lib' / 'detect.js',
        NPM_DIR / 'lib' / 'error-translator.js',
    ]

    for path in expected:
        assert path.is_file(), path

    assert not (NPM_DIR / 'lib' / 'settings.js').exists(), (
        'lib/settings.js was removed in 0.3.0; settings writes now go through '
        'the Python installer via `uv tool run --from claude-anyteam`.'
    )

    setup_source = (NPM_DIR / 'bin' / 'setup.js').read_text(encoding='utf-8')
    # Claude Code plugin registration still lives in JS (orthogonal to
    # settings writes) and must remain.
    assert 'Registering Claude Code plugin' in setup_source
    assert 'JonathanRosado/claude-anyteam' in setup_source
    assert 'CLAUDE_PLUGIN_SPEC' in setup_source
    assert 'CLAUDE_PLUGIN_MARKETPLACE_NAME' in setup_source
    assert 'CLAUDE CODE PLUGIN SKIPPED' in setup_source
    assert 'Claude Code plugin:' in setup_source


def test_setup_delegates_to_python_installer() -> None:
    """setup.js 0.3.0 delegates settings writes to the Python installer.

    Regression guard: no direct JSON mutation of settings.json from JS; all
    writes flow through `uv tool run --from claude-anyteam claude-anyteam
    install --assume-yes`. Fails if someone re-introduces writeClaudeSettings
    or inlines JSON-file writes in the setup flow.
    """
    setup_source = (NPM_DIR / 'bin' / 'setup.js').read_text(encoding='utf-8')

    # The delegation invocation must be present with --assume-yes propagation.
    assert "'tool'" in setup_source and "'run'" in setup_source, (
        'setup.js must invoke the Python installer via `uv tool run`'
    )
    assert "'--from'" in setup_source and 'TOOL_NAME' in setup_source, (
        'setup.js must pass --from <tool> to uv tool run'
    )
    assert "'install'" in setup_source
    assert "'--assume-yes'" in setup_source, (
        'setup.js must always pass --assume-yes since npx is non-interactive'
    )
    assert "'--prerelease=allow'" in setup_source, (
        'setup.js must allow pre-release Python deps when uv resolves claude-anyteam'
    )
    # stdio must be inherited so the Python installer's messages reach the user.
    assert "stdio:" in setup_source and "'inherit'" in setup_source

    # Symbols that moved to Python ownership must be gone from setup.js.
    assert 'writeClaudeSettings' not in setup_source
    assert "from '../lib/settings.js'" not in setup_source
    assert 'TEAMMATE_COMMAND_KEY' not in setup_source
    assert 'TEAMMATE_BINARY_KEY' not in setup_source


def test_npm_version_banner_reads_package_json() -> None:
    setup_source = (NPM_DIR / 'bin' / 'setup.js').read_text(encoding='utf-8')

    assert "readFileSync(new URL('../package.json', import.meta.url)" in setup_source
    assert 'claude-anyteam installer v${INSTALLER_VERSION}' in setup_source


def test_setup_refreshes_plugin_on_every_run() -> None:
    """Re-running `npx --yes claude-anyteam` must pick up newer plugin versions.

    Fails if someone drops the `claude plugin update` call in
    registerClaudePlugin() — without it, a user who installed at v0.3.0 and
    re-runs the installer after a new release is silently stuck on the old
    cached manifest (the bug this test guards against).
    """
    setup_source = (NPM_DIR / 'bin' / 'setup.js').read_text(encoding='utf-8')

    assert "'plugin', 'install', CLAUDE_PLUGIN_SPEC" in setup_source, (
        'setup.js must still `claude plugin install` to cover the fresh-install path'
    )
    assert "'plugin', 'update', CLAUDE_PLUGIN_SPEC" in setup_source, (
        'setup.js must `claude plugin update` on every run so re-runs pull the '
        'latest manifest; without this, cached skills go stale'
    )


def test_npm_detect_logic_keeps_uv_tool_resolution_deterministic() -> None:
    detect_source = (NPM_DIR / 'lib' / 'detect.js').read_text(encoding='utf-8')

    assert 'UV_TOOL_BIN_DIR' in detect_source
    assert "tool', 'dir', '--bin'" in detect_source
    assert 'cwd: toolWorkingDir()' in detect_source
    assert 'function toolWorkingDir()' in detect_source


def test_npm_installer_contains_windows_hardening_paths() -> None:
    setup_source = (NPM_DIR / 'bin' / 'setup.js').read_text(encoding='utf-8')
    detect_source = (NPM_DIR / 'lib' / 'detect.js').read_text(encoding='utf-8')

    assert 'formatPowerShellArgument' in detect_source
    assert 'PYTHONUTF8' in detect_source
    assert 'ensureWindowsLongPaths' in detect_source
    assert 'checkWindowsSymlinkPrivilege' in detect_source
    assert 'WINDOWS_SYMLINK_PERMISSION' in detect_source
    assert 'Windows Defender PowerShell' in detect_source
    assert 'Windows single-terminal compatibility' in setup_source
    assert 'PowerShell as Administrator' in setup_source


def test_npm_dependency_auto_install_paths_are_cross_platform() -> None:
    detect_source = (NPM_DIR / 'lib' / 'detect.js').read_text(encoding='utf-8')

    assert 'function uvPackageManagerPlans()' in detect_source
    assert "'winget'" in detect_source
    assert "'scoop'" in detect_source
    assert "'choco'" in detect_source
    assert "args: ['install', 'uv']" in detect_source
    assert "command: 'apt-get'" in detect_source
    assert "command: 'dnf'" in detect_source
    assert "command: 'pacman'" in detect_source
    assert "command: 'apk'" in detect_source
    assert "args: ['add', 'uv']" in detect_source
    assert "args: ['add', 'python3', 'py3-pip', 'curl']" in detect_source


def test_npm_art_ascii_fallback_under_c_locale() -> None:
    node = shutil.which('node')
    if node is None:
        pytest.skip('node is required for npm art fallback test')

    script = """
        import { renderBanner, renderBox, supportsUnicode, theme } from './npm/lib/art.js';
        console.log(JSON.stringify({
          unicode: supportsUnicode(),
          success: theme.symbols.success,
          error: theme.symbols.error,
          banner: renderBanner({ columns: 120 }),
          box: renderBox('TITLE', ['body'], 'cyan'),
        }));
    """
    completed = subprocess.run(
        [node, '--input-type=module', '-e', script],
        cwd=ROOT,
        env={**os.environ, 'LC_ALL': 'C', 'LANG': 'C'},
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    rendered = json.loads(completed.stdout)

    assert rendered['unicode'] is False
    assert '[OK]' in rendered['success']
    assert '[X]' in rendered['error']
    assert rendered['banner'].strip().endswith('claude-anyteam')
    box_visible = ANSI_RE.sub('', rendered['box'])
    assert box_visible.startswith('+'), box_visible[:40]
    assert '╭' not in rendered['box']
    assert '│' not in rendered['box']


def test_pyproject_version_matches_npm_version() -> None:
    """Both package manifests ship as one behavior-coupled unit."""
    pyproject = (ROOT / 'pyproject.toml').read_text(encoding='utf-8')
    version_line = next(
        line for line in pyproject.splitlines()
        if line.startswith('version = ')
    )
    pyproject_version = version_line.split('=', 1)[1].strip().strip('"')

    package = json.loads((NPM_DIR / 'package.json').read_text(encoding='utf-8'))
    assert package['version'] == pyproject_version, (
        f'pyproject={pyproject_version!r} npm={package["version"]!r}'
    )
