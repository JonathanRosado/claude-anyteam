"""Wrapper MCP server contract tests.

Per team-lead's ask: fail loudly if the exposed tool set drifts from the
safe subset — especially if someone accidentally re-exports a destructive
tool like `force_kill_teammate`. Blast-radius discipline enforced at
test time, not just prompt time.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from claude_teams.models import TeammateMember

from claude_anyteam.wrapper_server import (
    BLOCKED_TOOLS,
    EXPOSED_TOOLS,
    build_server,
)


@pytest.fixture
def identity(monkeypatch):
    monkeypatch.setenv("CLAUDE_ANYTEAM_TEAM", "claude-anyteam")
    monkeypatch.setenv("CLAUDE_ANYTEAM_NAME", "contract-test")


def _clear_identity_env(monkeypatch) -> None:
    for key in (
        "CLAUDE_ANYTEAM_TEAM",
        "CLAUDE_ANYTEAM_NAME",
        "CODEX_TEAMMATE_TEAM",
        "CODEX_TEAMMATE_NAME",
    ):
        monkeypatch.delenv(key, raising=False)


def _advertised_tool_names() -> list[str]:
    mcp = build_server()
    tools = asyncio.run(mcp.list_tools())
    return sorted(t.name for t in tools)


def _call_tool(name: str, arguments: dict) -> dict:
    mcp = build_server()
    result = asyncio.run(mcp.call_tool(name, arguments))
    return result.structured_content


def _member(name: str, color: str) -> TeammateMember:
    return TeammateMember(
        agentId=f"{name}@claude-anyteam",
        name=name,
        agentType="claude",
        model="test-model",
        prompt="irrelevant",
        color=color,
        joinedAt=0,
        tmuxPaneId="pane",
        cwd="/tmp",
    )


def test_exposed_tools_exactly_the_safe_subset(identity):
    names = _advertised_tool_names()
    assert set(names) == set(EXPOSED_TOOLS), (
        f"wrapper tool set drifted: {set(names) ^ set(EXPOSED_TOOLS)}"
    )


def test_blocked_tools_not_exposed(identity):
    names = set(_advertised_tool_names())
    leaked = names & set(BLOCKED_TOOLS)
    assert not leaked, f"wrapper leaked blocked tools: {leaked}"


def test_exposed_and_blocked_do_not_overlap():
    overlap = set(EXPOSED_TOOLS) & set(BLOCKED_TOOLS)
    assert not overlap, f"EXPOSED_TOOLS and BLOCKED_TOOLS overlap: {overlap}"


def test_exposed_count_is_seven(identity):
    """Canary: seven tools is the intentional count. Adding an eighth should
    be a deliberate decision, not an accident — if this fails, update
    EXPOSED_TOOLS explicitly and re-check the blast radius."""
    assert len(_advertised_tool_names()) == 7


def test_identity_required_at_build_time(monkeypatch):
    """With neither env nor argv providing identity, build_server raises."""
    _clear_identity_env(monkeypatch)
    with pytest.raises(RuntimeError, match="team and name are required"):
        build_server([])


def test_identity_resolves_from_cli_args(monkeypatch):
    """CLI args (as passed by app_server_invoke's mcp_config) resolve
    identity even when env is empty. This is the fix for the observed
    'MCP handshake failed: connection closed' when App Server spawns
    the wrapper without forwarding our adapter's env."""
    from claude_anyteam.wrapper_server import _identity
    _clear_identity_env(monkeypatch)
    team, name = _identity(["--team", "my-team", "--name", "codex-alice"])
    assert team == "my-team"
    assert name == "codex-alice"


def test_identity_cli_args_equals_form(monkeypatch):
    """`--team=foo` form also works (argparse-compatible shape)."""
    from claude_anyteam.wrapper_server import _identity
    _clear_identity_env(monkeypatch)
    team, name = _identity(["--team=foo", "--name=bar"])
    assert team == "foo"
    assert name == "bar"


def test_identity_cli_args_preferred_over_env(monkeypatch):
    """If both CLI and env are set, CLI wins (more specific; set per-spawn)."""
    from claude_anyteam.wrapper_server import _identity
    monkeypatch.setenv("CLAUDE_ANYTEAM_TEAM", "env-team")
    monkeypatch.setenv("CLAUDE_ANYTEAM_NAME", "env-name")
    team, name = _identity(["--team", "cli-team", "--name", "cli-name"])
    assert team == "cli-team"
    assert name == "cli-name"


def test_identity_env_fallback_preserves_backward_compat(monkeypatch):
    """Old callers that only set env (no CLI args) must still work."""
    from claude_anyteam.wrapper_server import _identity
    monkeypatch.setenv("CLAUDE_ANYTEAM_TEAM", "env-team")
    monkeypatch.setenv("CLAUDE_ANYTEAM_NAME", "env-name")
    team, name = _identity([])
    assert team == "env-team"
    assert name == "env-name"


def test_identity_partial_cli_args_fall_back_to_env(monkeypatch):
    """If only --team is passed via CLI, --name falls back to env."""
    from claude_anyteam.wrapper_server import _identity
    monkeypatch.setenv("CLAUDE_ANYTEAM_TEAM", "env-team")
    monkeypatch.setenv("CLAUDE_ANYTEAM_NAME", "env-name")
    team, name = _identity(["--team", "cli-only-team"])
    assert team == "cli-only-team"
    assert name == "env-name"


def test_exposed_tools_covers_cs50victor_safe_subset():
    """The set of cs50victor tools we *deliberately* expose to Codex. If
    cs50victor ships a new tool we haven't categorised, either the
    union-check in test_all_cs50victor_tools_are_categorised fails (below)
    or this one does — both force a decision."""
    # This is the positive assertion; the negative one is next.
    assert "send_message" in EXPOSED_TOOLS
    assert "task_update" in EXPOSED_TOOLS
    assert "task_create" in EXPOSED_TOOLS
    assert "read_inbox" in EXPOSED_TOOLS
    assert "task_list" in EXPOSED_TOOLS
    assert "read_config" in EXPOSED_TOOLS
    assert "mcp_anyteam_shell" in EXPOSED_TOOLS


def test_mcp_anyteam_shell_exists_and_produces_output(identity):
    result = _call_tool("mcp_anyteam_shell", {"command": "printf shell-ok"})

    assert result == {"stdout": "shell-ok", "stderr": "", "exit_code": 0}


def test_all_cs50victor_tools_are_categorised():
    """Every cs50victor tool must be either EXPOSED or BLOCKED by name.
    A new tool in an upstream cs50victor version that's neither will
    fail this test, forcing an explicit decision on whether to surface
    it to Codex.
    """
    # We hard-code cs50victor's current 13-tool surface. If upstream grows
    # the tool surface, this list will diverge from live — that's the point.
    cs50victor_current_tools = frozenset({
        "team_create",
        "team_delete",
        "spawn_teammate",
        "send_message",
        "task_create",
        "task_update",
        "task_list",
        "task_get",
        "read_inbox",
        "read_config",
        "force_kill_teammate",
        "process_shutdown_approved",
        "check_teammate",
    })
    categorised = set(EXPOSED_TOOLS) | set(BLOCKED_TOOLS)
    # task_get is deliberately neither — we don't expose it (Codex should
    # use task_list), and it's not destructive so not in BLOCKED either.
    # This is fine; the test permits "known unclassified" by excluding it.
    known_unclassified = {"task_get"}
    uncategorised = cs50victor_current_tools - categorised - known_unclassified
    assert not uncategorised, (
        f"cs50victor tools not categorised by EXPOSED or BLOCKED: {uncategorised}"
    )


def test_task_update_forwards_owner_and_metadata(identity):
    existing = SimpleNamespace(owner="contract-test")
    updated = SimpleNamespace(
        id="7",
        status="in_progress",
        active_form="writing tests",
        owner="",
        metadata={"blocked_reason": "waiting on review"},
    )
    with (
        patch("claude_anyteam.wrapper_server._cs_tasks.get_task", return_value=existing),
        patch("claude_anyteam.wrapper_server._cs_tasks.update_task", return_value=updated) as m,
    ):
        result = _call_tool(
            "task_update",
            {
                "task_id": "7",
                "active_form": "writing tests",
                "owner": "",
                "metadata": {"blocked_reason": "waiting on review"},
            },
        )

    assert m.call_args.kwargs["owner"] == ""
    assert m.call_args.kwargs["metadata"] == {"blocked_reason": "waiting on review"}
    assert result["owner"] == ""
    assert result["metadata"] == {"blocked_reason": "waiting on review"}


def test_task_update_rejects_when_owned_by_other_teammate(identity):
    with patch(
        "claude_anyteam.wrapper_server._cs_tasks.get_task",
        return_value=SimpleNamespace(owner="someone-else"),
    ):
        with pytest.raises(Exception, match="owned by 'someone-else', not 'contract-test'"):
            _call_tool("task_update", {"task_id": "7", "owner": "team-lead"})


def test_send_message_accepts_broadcast_star(identity, monkeypatch, tmp_path):
    members = [
        SimpleNamespace(name="contract-test"),
        SimpleNamespace(name="team-lead"),
        SimpleNamespace(name="peer-a"),
        SimpleNamespace(name="peer-b"),
    ]
    config = SimpleNamespace(members=members)
    team_root = tmp_path / "teams"
    monkeypatch.setattr("claude_anyteam.wrapper_server._cs_messaging.TEAMS_DIR", team_root)

    with patch("claude_anyteam.wrapper_server._cs_teams.read_config", return_value=config):
        result = _call_tool(
            "send_message",
            {
                "to": "*",
                "body": "heads up",
                "summary": "broadcast",
            },
        )

    assert result == {"delivered_to": "*", "sender": "contract-test", "count": 3}
    for peer in ("team-lead", "peer-a", "peer-b"):
        inbox = team_root / "claude-anyteam" / "inboxes" / f"{peer}.json"
        assert inbox.exists(), f"broadcast should create inbox entry for {peer}"
        payload = json.loads(inbox.read_text(encoding="utf-8"))
        assert len(payload) == 1
        assert payload[0]["from"] == "contract-test"
        assert payload[0]["text"] == "heads up"
        assert payload[0]["summary"] == "broadcast"

    own_inbox = team_root / "claude-anyteam" / "inboxes" / "contract-test.json"
    assert not own_inbox.exists(), "broadcast must not send to the sender's own inbox"


def test_send_message_direct_message_uses_sender_color(identity, monkeypatch, tmp_path):
    config = SimpleNamespace(
        members=[
            _member("contract-test", "magenta"),
            _member("peer-a", "blue"),
        ]
    )
    team_root = tmp_path / "teams"
    monkeypatch.setattr("claude_anyteam.wrapper_server._cs_messaging.TEAMS_DIR", team_root)

    with patch("claude_anyteam.wrapper_server._cs_teams.read_config", return_value=config):
        result = _call_tool(
            "send_message",
            {
                "to": "peer-a",
                "body": "hello",
                "summary": "dm",
            },
        )

    assert result == {"delivered_to": "peer-a", "sender": "contract-test"}
    inbox = team_root / "claude-anyteam" / "inboxes" / "peer-a.json"
    payload = json.loads(inbox.read_text(encoding="utf-8"))
    assert len(payload) == 1
    assert payload[0]["from"] == "contract-test"
    assert payload[0]["color"] == "magenta"
