"""Regression tests for task #21: App Server is the default mode.

Without any flag or env var, `Settings.app_server` must be True. Explicit
opt-out via `--no-app-server` or `CLAUDE_ANYTEAM_APP_SERVER=false` must
still work. The two opt-in paths (`--app-server`, env=true) must continue
to be recognized for anyone who passes them explicitly.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from claude_anyteam import cli as cli_mod
from claude_anyteam.config import Settings, from_env


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Strip CLAUDE_ANYTEAM_* env vars so each test starts from defaults."""
    for k in list(os.environ):
        if k.startswith("CLAUDE_ANYTEAM_") or k == "CODEX_BINARY":
            monkeypatch.delenv(k, raising=False)


def _baseline_overrides() -> dict:
    return {
        "team_name": "t",
        "agent_name": "a",
        "cwd": str(Path.cwd().resolve()),
    }


def test_default_app_server_is_on():
    """Task #21 default flip: no flag, no env → app_server=True."""
    s = from_env(overrides=_baseline_overrides())
    assert s.app_server is True


def test_env_opt_out_honored():
    os.environ["CLAUDE_ANYTEAM_APP_SERVER"] = "false"
    try:
        s = from_env(overrides=_baseline_overrides())
        assert s.app_server is False
    finally:
        del os.environ["CLAUDE_ANYTEAM_APP_SERVER"]


def test_env_opt_in_still_works():
    os.environ["CLAUDE_ANYTEAM_APP_SERVER"] = "true"
    try:
        s = from_env(overrides=_baseline_overrides())
        assert s.app_server is True
    finally:
        del os.environ["CLAUDE_ANYTEAM_APP_SERVER"]


def test_override_false_beats_env_true():
    os.environ["CLAUDE_ANYTEAM_APP_SERVER"] = "true"
    try:
        overrides = _baseline_overrides() | {"app_server": "false"}
        s = from_env(overrides=overrides)
        assert s.app_server is False
    finally:
        del os.environ["CLAUDE_ANYTEAM_APP_SERVER"]


def test_override_true_beats_env_false():
    os.environ["CLAUDE_ANYTEAM_APP_SERVER"] = "false"
    try:
        overrides = _baseline_overrides() | {"app_server": "true"}
        s = from_env(overrides=overrides)
        assert s.app_server is True
    finally:
        del os.environ["CLAUDE_ANYTEAM_APP_SERVER"]


def test_settings_dataclass_default_is_true():
    """Direct dataclass construction without passing app_server must also
    default to True so test setups that don't explicitly set the flag
    reflect the shipped default."""
    s = Settings(
        team_name="t",
        agent_name="a",
        cwd=Path("/tmp").resolve(),
        poll_interval_s=1.5,
        color="cyan",
        plan_mode_required=False,
        codex_binary="codex",
    )
    assert s.app_server is True


# ---- CLI-flag shape (--app-server / --no-app-server) -----------------------


def test_cli_no_flag_leaves_app_server_default():
    """Without the flag on argv, the CLI must not clobber the default
    (so env/default logic in from_env wins)."""
    ns = cli_mod._parse_args(["--team", "t", "--name", "a"])
    assert ns.app_server is None


def test_cli_app_server_flag_sets_true():
    ns = cli_mod._parse_args(["--team", "t", "--name", "a", "--app-server"])
    assert ns.app_server is True


def test_cli_no_app_server_flag_sets_false():
    ns = cli_mod._parse_args(["--team", "t", "--name", "a", "--no-app-server"])
    assert ns.app_server is False


def test_cli_help_mentions_app_server_default():
    """The --help text must document the default so users know what the
    behavior is without the flag. argparse formats this from the `help`
    string on the action."""
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with pytest.raises(SystemExit):
        with redirect_stdout(buf):
            cli_mod._parse_args(["--help"])
    help_text = buf.getvalue()
    assert "app-server" in help_text
    assert "no-app-server" in help_text
    # The help prose mentions default behavior.
    assert "default: on" in help_text or "default on" in help_text.lower()
