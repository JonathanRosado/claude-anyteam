from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NPM_DIR = ROOT / 'npm'


def test_npm_package_metadata_matches_installer_contract() -> None:
    package = json.loads((NPM_DIR / 'package.json').read_text(encoding='utf-8'))

    assert package['name'] == 'claude-anyteam'
    assert package['version'] == '0.3.0'
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
    # stdio must be inherited so the Python installer's messages reach the user.
    assert "stdio:" in setup_source and "'inherit'" in setup_source

    # Symbols that moved to Python ownership must be gone from setup.js.
    assert 'writeClaudeSettings' not in setup_source
    assert "from '../lib/settings.js'" not in setup_source
    assert 'TEAMMATE_COMMAND_KEY' not in setup_source
    assert 'TEAMMATE_BINARY_KEY' not in setup_source


def test_npm_detect_logic_keeps_uv_tool_resolution_deterministic() -> None:
    detect_source = (NPM_DIR / 'lib' / 'detect.js').read_text(encoding='utf-8')

    assert 'UV_TOOL_BIN_DIR' in detect_source
    assert "tool', 'dir', '--bin'" in detect_source
    assert 'cwd: toolWorkingDir()' in detect_source
    assert 'function toolWorkingDir()' in detect_source


def test_pyproject_version_matches_npm_version() -> None:
    """Both package manifests ship as one behavior-coupled unit."""
    pyproject = (ROOT / 'pyproject.toml').read_text(encoding='utf-8')
    # Light TOML match — we already know pyproject has `version = "X.Y.Z"` on a
    # single line and don't want a new dep just for this one assertion.
    version_line = next(
        line for line in pyproject.splitlines()
        if line.startswith('version = ')
    )
    assert version_line == 'version = "0.3.0"', version_line

    package = json.loads((NPM_DIR / 'package.json').read_text(encoding='utf-8'))
    assert package['version'] == '0.3.0'
