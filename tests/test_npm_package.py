from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NPM_DIR = ROOT / 'npm'


def test_npm_package_metadata_matches_installer_contract() -> None:
    package = json.loads((NPM_DIR / 'package.json').read_text(encoding='utf-8'))

    assert package['name'] == 'claude-anyteam'
    assert package['bin']['claude-anyteam-setup'] == './bin/setup.js'
    assert package['scripts']['postinstall'] == 'node ./bin/setup.js --postinstall'
    assert package['engines']['node'] == '>=18'
    assert package['dependencies'] == {
        'gradient-string': '3.0.0',
        'yocto-spinner': '1.1.0',
        'yoctocolors': '2.1.2',
    }


def test_npm_installer_files_exist() -> None:
    expected = [
        NPM_DIR / 'README.md',
        NPM_DIR / 'bin' / 'setup.js',
        NPM_DIR / 'lib' / 'art.js',
        NPM_DIR / 'lib' / 'detect.js',
        NPM_DIR / 'lib' / 'settings.js',
    ]

    for path in expected:
        assert path.is_file(), path


def test_npm_detect_logic_keeps_uv_tool_resolution_deterministic() -> None:
    detect_source = (NPM_DIR / 'lib' / 'detect.js').read_text(encoding='utf-8')

    assert 'UV_TOOL_BIN_DIR' in detect_source
    assert "tool', 'dir', '--bin'" in detect_source
    assert 'cwd: toolWorkingDir()' in detect_source
    assert 'function toolWorkingDir()' in detect_source
