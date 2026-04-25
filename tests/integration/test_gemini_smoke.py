from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from claude_anyteam.backends.gemini import invoke
from claude_anyteam.backends.gemini.acp_client import (
    GeminiAcpClient,
    GeminiAcpError,
    GeminiAcpTimeoutError,
)

pytestmark = pytest.mark.integration

PROMPT = "Reply exactly: OK"
AUTH_FAILURE_MARKERS = (
    "auth",
    "authenticate",
    "authentication",
    "credential",
    "credentials",
    "login",
    "log in",
    "not logged in",
    "unauthorized",
    "permission denied",
    "api key",
)


def _gemini_binary_or_skip() -> str:
    binary = shutil.which("gemini")
    if binary is None:
        pytest.skip("gemini binary not found on PATH")
    return binary


def _gemini_help_or_skip(binary: str) -> str:
    try:
        proc = subprocess.run(
            [binary, "--help"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        pytest.skip(f"could not probe Gemini CLI help: {exc}")
    return (proc.stdout or "") + (proc.stderr or "")


def _require_acp_capability(binary: str) -> str:
    help_text = _gemini_help_or_skip(binary)
    if "--acp" in help_text:
        return "--acp"
    if "--experimental-acp" in help_text:
        return "--experimental-acp"
    pytest.skip("Gemini CLI does not advertise --acp/--experimental-acp capability")


def _skip_if_auth_failure(detail: str | None) -> None:
    if not detail:
        return
    lowered = detail.lower()
    if any(marker in lowered for marker in AUTH_FAILURE_MARKERS):
        pytest.skip(f"Gemini CLI is not authenticated for smoke test: {detail[:300]}")


def _isolated_gemini_env(tmp_path: Path) -> dict[str, str]:
    gemini_home = tmp_path / "gemini-home"
    invoke.write_mcp_settings(
        gemini_home,
        team="integration",
        agent_name="gemini-smoke-acp",
        real_home=os.environ.get("HOME"),
        cwd=tmp_path,
    )
    env = dict(os.environ)
    env["HOME"] = str(gemini_home)
    env.setdefault("GEMINI_CLI_NO_RELAUNCH", "true")
    if os.environ.get("HOME"):
        env["CLAUDE_ANYTEAM_REAL_HOME"] = os.environ["HOME"]
    return env


def _result_is_error(result: dict[str, Any]) -> bool:
    stop_reason = str(result.get("stopReason") or result.get("stop_reason") or "").lower()
    return bool(result.get("error") or stop_reason == "error")


def test_gemini_headless_smoke_returns_output_and_session_id(tmp_path: Path) -> None:
    binary = _gemini_binary_or_skip()
    _require_acp_capability(binary)

    result = invoke.run(
        PROMPT,
        cwd=tmp_path,
        gemini_binary=binary,
        gemini_home=tmp_path / "gemini-home",
        timeout_s=120,
    )

    _skip_if_auth_failure(result.error)
    assert result.exit_code == 0, result.error
    assert result.error is None
    assert result.session_id, "Gemini stream did not include an init session_id"
    assert result.events, "Gemini stream parser did not capture any JSON events"
    assert any(event.get("type") == "result" for event in result.events)
    assert result.last_message.strip(), "Gemini did not return assistant output"
    assert "OK" in result.last_message


def test_gemini_acp_smoke_prompt_emits_session_updates(tmp_path: Path) -> None:
    binary = _gemini_binary_or_skip()
    acp_flag = _require_acp_capability(binary)
    env = _isolated_gemini_env(tmp_path)

    client = GeminiAcpClient(gemini_binary=binary, env=env, acp_flag=acp_flag)
    try:
        client.start()
        try:
            initialize = client.initialize(timeout=60)
            session = client.session_new(cwd=tmp_path, timeout=120)
            session_id = session.get("sessionId")
            assert isinstance(session_id, str) and session_id
            response = client.session_prompt(session_id=session_id, prompt=PROMPT, timeout=180)
        except (GeminiAcpError, GeminiAcpTimeoutError) as exc:
            _skip_if_auth_failure(str(exc))
            raise

        assert initialize.get("protocolVersion") == 1
        assert isinstance(response, dict)
        if _result_is_error(response):
            _skip_if_auth_failure(repr(response))
        assert not _result_is_error(response), response

        notifications = client.drain_notifications()
        session_updates = [
            note
            for note in notifications
            if note.get("method") == "session/update"
            and isinstance(note.get("params"), dict)
            and note["params"].get("sessionId") in (None, session_id)
        ]
        assert session_updates, notifications
    finally:
        client.close()
