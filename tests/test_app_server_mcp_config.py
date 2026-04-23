"""Contract tests for `app_server_invoke`'s mcp_config shape.

Regression guard against the "wrapper MCP silently doesn't start under
App Server" bug observed in task #22 sanity probes. The fix: identity
goes in `args` (CLI flags), not env, because App Server doesn't forward
our adapter's env into the wrapper subprocess.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from claude_anyteam import app_server as app_server_mod
from claude_anyteam import codex as codex_mod


def _capture_thread_start_config(*, settings_team: str, settings_agent: str) -> dict:
    """Invoke app_server_invoke with enough mocking that no real Codex
    runs, and capture the `config` dict passed to `thread_start`."""
    captured: dict = {}

    class _Q:
        def __init__(self):
            self._items = [
                {
                    "method": "turn/completed",
                    "params": {"turn": {"status": "ok"}},
                }
            ]

        def get(self, timeout=None):
            if self._items:
                return self._items.pop(0)
            raise RuntimeError("empty (test)")

    class _FakeClient:
        notifications = _Q()

        def __init__(self, *a, **k):
            pass

        def start(self):
            pass

        def initialize(self):
            return {}

        def thread_start(self, **kwargs):
            captured["thread_start_kwargs"] = kwargs
            return "thread-id-fake"

        def turn_start(self, **kwargs):
            return "turn-id-fake"

        def drain_notifications(self):
            return []

        def turn_interrupt(self, **kwargs):
            pass

        def close(self, **kwargs):
            pass

    with patch.object(app_server_mod, "AppServerClient", _FakeClient):
        codex_mod.app_server_invoke(
            task_prompt="noop",
            cwd=Path("/tmp"),
            schema=None,
            settings_team=settings_team,
            settings_agent=settings_agent,
        )
    return captured["thread_start_kwargs"]["config"]


def test_mcp_config_wrapper_args_include_team_and_name():
    """Identity must be in args, not env. Regression guard for the App
    Server env-forwarding gap."""
    config = _capture_thread_start_config(
        settings_team="claude-anyteam", settings_agent="codex-alice"
    )
    mcp = config["mcp_servers"]["claude_anyteam_wrapper"]
    assert "--team" in mcp["args"]
    assert "claude-anyteam" in mcp["args"]
    assert "--name" in mcp["args"]
    assert "codex-alice" in mcp["args"]
    team_idx = mcp["args"].index("--team")
    assert mcp["args"][team_idx + 1] == "claude-anyteam"
    name_idx = mcp["args"].index("--name")
    assert mcp["args"][name_idx + 1] == "codex-alice"


def test_mcp_config_wrapper_command_is_resolved_path():
    """Absolute path (via shutil.which) or the bare name; either way,
    there's a command field and it ends with `claude-anyteam-wrapper`."""
    config = _capture_thread_start_config(
        settings_team="t", settings_agent="a"
    )
    cmd = config["mcp_servers"]["claude_anyteam_wrapper"]["command"]
    assert cmd.endswith("claude-anyteam-wrapper")


def test_mcp_config_has_no_env_field():
    """Under Fix B (CLI args), we don't rely on a `config.mcp_servers.*.env`
    key — the wrapper's identity comes through args. This regression guard
    fails if someone later re-adds `env` (option A from the diagnostic),
    forcing a conversation about which path is in effect."""
    config = _capture_thread_start_config(
        settings_team="t", settings_agent="a"
    )
    mcp = config["mcp_servers"]["claude_anyteam_wrapper"]
    assert "env" not in mcp, (
        "If you added an env key, you're mixing Fix A and Fix B. "
        "Fix B (CLI args) is what ships; either remove env or update "
        "this test with a conscious decision."
    )
