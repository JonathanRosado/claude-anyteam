"""Lock-step assertion: all four user-facing manifests must agree on version.

History motivating this test:

Pre-v0.8.1, only `npm/package.json` and `pyproject.toml` were checked in lock-step
by `.github/workflows/auto-release.yml`. The two `.claude-plugin/` manifests were
not in the workflow's `paths:` filter and not in its version-comparison step.
Consequence: across v0.6 → v0.8.0, those manifests stayed pinned at 0.5.0 and 0.1.0
respectively. Claude Code's plugin marketplace, which keys upgrade decisions off
`.claude-plugin/marketplace.json`'s advertised version, never saw a newer version
to advertise — so users on the marketplace path stayed on the v0.5.0 plugin cache
(missing the `diagnose` skill, the reshaped `help` skill, and every subsequent
skill change).

This test makes the same drift impossible at developer-loop time. If the four
manifests disagree, `pytest` fails locally before any push, in addition to
`auto-release.yml` failing on push to main. Defense-in-depth: CI is the gate;
this test is the early-warning rapid-feedback layer.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent

NPM_PACKAGE_JSON = REPO_ROOT / "npm" / "package.json"
PYPROJECT_TOML = REPO_ROOT / "pyproject.toml"
PLUGIN_JSON = REPO_ROOT / ".claude-plugin" / "plugin.json"
MARKETPLACE_JSON = REPO_ROOT / ".claude-plugin" / "marketplace.json"


def _read_npm_version() -> str:
    return json.loads(NPM_PACKAGE_JSON.read_text())["version"]


def _read_pyproject_version() -> str:
    for line in PYPROJECT_TOML.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("version"):
            # `version = "0.8.1"` — split on `"` and pick the literal.
            return stripped.split('"')[1]
    raise AssertionError(f"no version field found in {PYPROJECT_TOML}")


def _read_plugin_version() -> str:
    return json.loads(PLUGIN_JSON.read_text())["version"]


def _read_marketplace_metadata_version() -> str:
    return json.loads(MARKETPLACE_JSON.read_text())["metadata"]["version"]


def _read_marketplace_plugin_version() -> str:
    plugins = json.loads(MARKETPLACE_JSON.read_text())["plugins"]
    assert plugins, f"{MARKETPLACE_JSON} has no plugins entry"
    return plugins[0]["version"]


@pytest.fixture(scope="module")
def manifest_versions() -> dict[str, str]:
    return {
        "npm/package.json": _read_npm_version(),
        "pyproject.toml": _read_pyproject_version(),
        ".claude-plugin/plugin.json": _read_plugin_version(),
        ".claude-plugin/marketplace.json:metadata.version": _read_marketplace_metadata_version(),
        ".claude-plugin/marketplace.json:plugins[0].version": _read_marketplace_plugin_version(),
    }


def test_all_manifests_agree_on_version(manifest_versions: dict[str, str]) -> None:
    """All five version fields across the four manifest files must be identical.

    This includes BOTH version fields inside `.claude-plugin/marketplace.json`
    (`metadata.version` and `plugins[0].version`), since Claude Code's plugin
    marketplace reads them independently.
    """
    distinct = set(manifest_versions.values())
    assert len(distinct) == 1, (
        "Manifest version drift detected — all four user-facing manifests must "
        "agree in lock-step, but found:\n  "
        + "\n  ".join(f"{path}: {ver}" for path, ver in manifest_versions.items())
    )


def test_version_is_pep440_shape(manifest_versions: dict[str, str]) -> None:
    """The agreed-upon version must look like a PEP 440 / SemVer release token.

    Catches typos like `0.8.1-rc.1` (we don't ship pre-releases through this
    auto-release workflow) and outright garbage.
    """
    version = next(iter(set(manifest_versions.values())))
    parts = version.split(".")
    assert len(parts) == 3, f"expected MAJOR.MINOR.PATCH, got {version!r}"
    for part in parts:
        assert part.isdigit(), (
            f"version part {part!r} in {version!r} is not a digit-only segment "
            f"(pre-release / build metadata not supported by auto-release.yml)"
        )
