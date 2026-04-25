"""Real Gemini CLI smoke tests.

These tests intentionally exercise the installed ``gemini`` binary instead of
mocking subprocess/ACP traffic, so they are marked ``integration`` and excluded
from the default suite. Run explicitly with:

    uv run pytest -m integration tests/integration/test_gemini_smoke.py
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from claude_anyteam.backends.gemini import acp, invoke
from claude_anyteam.backends.gemini.acp_client import detect_acp_flag

pytestmark = pytest.mark.integration

EXPECTED_GEMINI_VERSION = "0.39.0"
SMOKE_TOKEN = "ANYTEAM_SMOKE_OK"


_AUTH_ERROR_RE = re.compile(
    r"(auth|authenticate|authentication|unauthenticated|login|log in|sign in|api key|oauth|credentials?)",
    re.IGNORECASE,
)


def _gemini_binary() -> str:
    return os.environ.get("CLAUDE_ANYTEAM_GEMINI_BINARY", "gemini")


def _require_gemini_039() -> str:
    binary = _gemini_binary()
    resolved = shutil.which(binary)
    if not resolved:
        pytest.skip(f"Gemini CLI binary {binary!r} not found on PATH")

    try:
        proc = subprocess.run(
            [resolved, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        pytest.skip(f"could not run {resolved} --version: {exc}")

    version_output = f"{proc.stdout}\n{proc.stderr}".strip()
    match = re.search(r"\b(\d+\.\d+\.\d+)\b", version_output)
    assert match, f"could not parse Gemini CLI version from: {version_output!r}"
    assert match.group(1) == EXPECTED_GEMINI_VERSION, (
        f"Gemini CLI version drift: expected {EXPECTED_GEMINI_VERSION}, "
        f"got {match.group(1)} from {resolved} ({version_output!r})"
    )
    return resolved


def _skip_if_unauthenticated(error: str | None) -> None:
    if error and _AUTH_ERROR_RE.search(error):
        pytest.skip(f"Gemini CLI is present but not authenticated: {error}")


def _smoke_prompt(path_name: str) -> str:
    return (
        f"This is a claude-anyteam {path_name} integration smoke test. "
        f"Do not call tools. Reply with only this exact token: {SMOKE_TOKEN}"
    )


def test_real_gemini_headless_smoke(tmp_path: Path) -> None:
    """The headless adapter can invoke real Gemini CLI stream-json output."""
    gemini = _require_gemini_039()

    result = invoke.run(
        _smoke_prompt("headless"),
        cwd=tmp_path,
        gemini_binary=gemini,
        gemini_home=tmp_path / "gemini-home-headless",
        wrapper_identity=("integration", "gemini-smoke-headless"),
        timeout_s=180,
    )

    _skip_if_unauthenticated(result.error)
    assert result.error is None
    assert result.exit_code == 0
    assert any(event.get("type") == "result" for event in result.events)
    assert SMOKE_TOKEN in result.last_message
    assert result.session_id


def test_real_gemini_acp_smoke(tmp_path: Path) -> None:
    """The ACP adapter can initialize, create a session, and prompt real Gemini."""
    gemini = _require_gemini_039()
    acp_flag = detect_acp_flag(gemini)
    assert acp_flag in {"--acp", "--experimental-acp"}

    result = acp.run(
        _smoke_prompt("ACP"),
        cwd=tmp_path,
        gemini_binary=gemini,
        gemini_home=tmp_path / "gemini-home-acp",
        wrapper_identity=("integration", "gemini-smoke-acp"),
        timeout_s=180,
        ephemeral=True,
    )

    _skip_if_unauthenticated(result.error)
    assert result.error is None
    assert result.exit_code == 0
    assert any(event.get("method") == "session/update" for event in result.events)
    assert SMOKE_TOKEN in result.last_message
    assert result.session_id
