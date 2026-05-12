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
from claude_teams import tasks as cs_tasks
from claude_teams.models import LeadMember, TeammateMember

from claude_anyteam import protocol_io as pio
from claude_anyteam.wrapper_server import (
    BLOCKED_TOOLS,
    EXPOSED_TOOLS,
    TOOL_CATEGORIES,
    build_server,
)


@pytest.fixture
def identity(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_ANYTEAM_TEAM", "claude-anyteam")
    monkeypatch.setenv("CLAUDE_ANYTEAM_NAME", "contract-test")
    team_root = tmp_path / "teams"
    monkeypatch.setattr("claude_anyteam.wrapper_server._cs_messaging.TEAMS_DIR", team_root)
    monkeypatch.setattr("claude_anyteam.protocol_io._m.TEAMS_DIR", team_root)
    return team_root


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


def _lead() -> LeadMember:
    return LeadMember(
        agentId="team-lead@claude-anyteam",
        name="team-lead",
        agentType="team-lead",
        model="claude-opus",
        joinedAt=0,
        tmuxPaneId="",
        cwd="/tmp",
    )


def _seed_manifest_team(
    team_root,
    *,
    version: str = "1",
    peer_accepts_steer: bool = False,
    peer_manifest: bool = True,
):
    team_dir = team_root / "claude-anyteam"
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "inboxes").mkdir(exist_ok=True)
    config = {
        "name": "claude-anyteam",
        "createdAt": 0,
        "leadAgentId": "team-lead@claude-anyteam",
        "leadSessionId": "lead-session",
        "members": [
            _lead().model_dump(by_alias=True, exclude_none=True),
            _member("contract-test", "magenta").model_dump(by_alias=True, exclude_none=True),
            _member("peer-a", "blue").model_dump(by_alias=True, exclude_none=True),
        ],
    }
    (team_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
    manifest_dir = team_dir / "manifests"
    manifest_dir.mkdir()
    manifest = None
    if peer_manifest:
        capabilities = {
            "permission_bridge": {
                "version": version,
                "schema": {"type": "object"},
                "description": "Approval bridge",
                "when_to_use": "approval needed",
                "when_not_to": "routine reads",
                "failure_modes": ["APPROVAL_TIMEOUT"],
            },
            "turn_steer": {
                "version": version,
                "schema": {"type": "object"},
                "description": "Peer steer",
                "when_to_use": "mid-turn course correction",
                "when_not_to": "recipient does not allow peers",
                "failure_modes": ["PEER_STEER_REJECTED"],
            },
        }
        if peer_accepts_steer:
            capabilities["accepts_peer_steer"] = {
                "version": version,
                "schema": {"type": "boolean"},
                "description": "Allows non-lead peers to send steer messages",
                "when_to_use": "peer steer is allowed",
                "when_not_to": "route through team-lead for lead-owned decisions",
                "failure_modes": ["STEER_PAYLOAD_OVERFLOW"],
            }
        manifest = {
            "schema_version": 1,
            "capability_version": version,
            "team_name": "claude-anyteam",
            "agent_name": "peer-a",
            "agent_id": "peer-a@claude-anyteam",
            "capabilities": capabilities,
        }
        (manifest_dir / "peer-a.json").write_text(json.dumps(manifest), encoding="utf-8")
    (team_dir / "inboxes" / "contract-test.json").write_text("[]", encoding="utf-8")
    return team_dir, manifest


def _seed_protocol_tools_team(
    team_root,
    *,
    self_name: str,
    model: str,
    manifest_host_tool_surface: str | None = None,
    manifest_transport: str | None = None,
):
    team_dir = team_root / "claude-anyteam"
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "inboxes").mkdir(exist_ok=True)
    config = {
        "name": "claude-anyteam",
        "createdAt": 0,
        "leadAgentId": "team-lead@claude-anyteam",
        "leadSessionId": "lead-session",
        "members": [
            _lead().model_dump(by_alias=True, exclude_none=True),
            {
                **_member(self_name, "magenta").model_dump(by_alias=True, exclude_none=True),
                "model": model,
                "backendType": "in-process",
            },
        ],
    }
    (team_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
    (team_dir / "inboxes" / f"{self_name}.json").write_text("[]", encoding="utf-8")
    if manifest_host_tool_surface or manifest_transport:
        manifest_dir = team_dir / "manifests"
        manifest_dir.mkdir(exist_ok=True)
        (manifest_dir / f"{self_name}.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "capability_version": "1",
                    "agent_name": self_name,
                    "model": model,
                    "host_tool_surface": manifest_host_tool_surface,
                    "transport": manifest_transport,
                    "capabilities": {},
                }
            ),
            encoding="utf-8",
        )
    return team_dir


def _seeded_wrapper(
    identity,
    monkeypatch,
    *,
    peer_accepts_steer: bool = False,
    peer_manifest: bool = True,
):
    _seed_manifest_team(
        identity,
        peer_accepts_steer=peer_accepts_steer,
        peer_manifest=peer_manifest,
    )
    monkeypatch.setattr("claude_anyteam.wrapper_server._cs_teams.TEAMS_DIR", identity)
    return build_server()


def _call_existing_tool(mcp, name: str, arguments: dict) -> dict:
    return asyncio.run(mcp.call_tool(name, arguments)).structured_content


def _steer_body(message: str = "Please use the revised constraint.") -> str:
    return json.dumps({"type": "steer", "message": message})


def _peer_a_inbox(identity) -> list[dict]:
    inbox = identity / "claude-anyteam" / "inboxes" / "peer-a.json"
    if not inbox.exists():
        return []
    return json.loads(inbox.read_text(encoding="utf-8"))


def _wrapper_refusals() -> list:
    return [
        event
        for event in pio.read_visibility_events("claude-anyteam", "contract-test")
        if event.kind == "visibility_degraded"
        and event.payload.get("surface") == "peer_steer_refused_at_wrapper"
    ]


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


def test_exposed_count_includes_protocol_shadow_and_manifest_tools(identity):
    """Canary: nineteen tools is intentional: checkpointing plus protocol/shadow/skill tools."""
    assert len(_advertised_tool_names()) == 19


def test_every_exposed_tool_has_visibility_category():
    assert set(TOOL_CATEGORIES) == set(EXPOSED_TOOLS)


def test_exposed_tool_handlers_are_instrumented(identity):
    mcp = build_server()
    tools = asyncio.run(mcp.list_tools())

    for tool in tools:
        assert (
            getattr(tool.fn, "__anyteam_instrumented_category__", None)
            == TOOL_CATEGORIES[tool.name]
        )


def test_send_message_description_contains_visibility_invariant(identity):
    mcp = build_server()
    tools = asyncio.run(mcp.list_tools())
    send_message = next(tool for tool in tools if tool.name == "send_message")

    assert (
        "Your plain text output is NOT visible to other agents"
        in send_message.description
    )
    assert "to communicate, you MUST call this tool" in send_message.description
    assert "Refer to teammates by name, never UUID." in send_message.description


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
    assert "checkpoint_commit" in EXPOSED_TOOLS
    assert "task_create" in EXPOSED_TOOLS
    assert "task_batch_summary" in EXPOSED_TOOLS
    assert "read_inbox" in EXPOSED_TOOLS
    assert "task_list" in EXPOSED_TOOLS
    assert "read_config" in EXPOSED_TOOLS
    assert "mcp_anyteam_capability_manifest" in EXPOSED_TOOLS
    assert "mcp_anyteam_list_skills" in EXPOSED_TOOLS
    assert "mcp_anyteam_invoke_skill" in EXPOSED_TOOLS
    assert "mcp_anyteam_shell" in EXPOSED_TOOLS
    assert "mcp_anyteam_read_file" in EXPOSED_TOOLS
    assert "mcp_anyteam_write_file" in EXPOSED_TOOLS
    assert "mcp_anyteam_list_directory" in EXPOSED_TOOLS
    assert "mcp_anyteam_edit_file" in EXPOSED_TOOLS
    assert "mcp_anyteam_search" in EXPOSED_TOOLS
    assert "mcp_anyteam_grep" in EXPOSED_TOOLS
    assert "mcp_anyteam_web_fetch" in EXPOSED_TOOLS


def test_read_config_protocol_tools_codex_uses_raw_wrapper_names(identity, monkeypatch):
    self_name = "codex-a"
    monkeypatch.setenv("CLAUDE_ANYTEAM_NAME", self_name)
    _seed_protocol_tools_team(
        identity,
        self_name=self_name,
        model="codex-cli",
        manifest_host_tool_surface="codex-native",
        manifest_transport="codex-app-server",
    )
    monkeypatch.setattr("claude_anyteam.wrapper_server._cs_teams.TEAMS_DIR", identity)

    result = _call_tool("read_config", {})

    protocol_tools = result["protocol_tools"]
    assert protocol_tools["backend"] == "codex"
    assert protocol_tools["naming"] == "raw"
    assert protocol_tools["source"] == "claude_anyteam.wrapper_server.EXPOSED_TOOLS"
    assert protocol_tools["tools"] == list(EXPOSED_TOOLS)
    assert protocol_tools["send_message"] == "send_message"
    assert protocol_tools["read_config"] == "read_config"
    assert "send_message" in protocol_tools["tools"]
    assert "mcp_anyteam_send_message" not in protocol_tools["tools"]


def test_read_config_protocol_tools_gemini_uses_anyteam_prefixed_names(identity, monkeypatch):
    self_name = "gemini-a"
    monkeypatch.setenv("CLAUDE_ANYTEAM_NAME", self_name)
    _seed_protocol_tools_team(
        identity,
        self_name=self_name,
        model="gemini-cli",
        manifest_host_tool_surface="mcp_anyteam",
        manifest_transport="gemini-acp",
    )
    monkeypatch.setattr("claude_anyteam.wrapper_server._cs_teams.TEAMS_DIR", identity)

    result = _call_tool("read_config", {})

    protocol_tools = result["protocol_tools"]
    expected = [
        name if name.startswith("mcp_anyteam_") else f"mcp_anyteam_{name}"
        for name in EXPOSED_TOOLS
    ]
    assert protocol_tools["backend"] == "gemini"
    assert protocol_tools["naming"] == "mcp_anyteam_prefixed"
    assert protocol_tools["tools"] == expected
    assert protocol_tools["send_message"] == "mcp_anyteam_send_message"
    assert protocol_tools["read_config"] == "mcp_anyteam_read_config"
    assert "mcp_anyteam_send_message" in protocol_tools["team_tools"]
    assert "send_message" not in protocol_tools["tools"]
    assert "mcp_anyteam_shell" in protocol_tools["tools"]
    assert "mcp_anyteam_mcp_anyteam_shell" not in protocol_tools["tools"]


def test_capability_manifest_tool_returns_cached_entry(identity, monkeypatch, tmp_path):
    team_root = tmp_path / "teams"
    _seed_manifest_team(team_root, version="1")
    monkeypatch.setattr("claude_anyteam.wrapper_server._cs_teams.TEAMS_DIR", team_root)

    mcp = build_server()

    # After wrapper startup, the R13 tool must serve from memory; no manifest
    # file read should occur on a cache-hit lookup.
    def explode(_path):
        raise AssertionError("manifest file read during cache-hit tool call")

    monkeypatch.setattr("claude_anyteam.capability_manifest.read_manifest_file", explode)
    result = asyncio.run(
        mcp.call_tool(
            "mcp_anyteam_capability_manifest",
            {"agent_name": "peer-a", "capability": "permission_bridge"},
        )
    ).structured_content

    assert result["schema"] == {"type": "object"}
    assert result["when_to_use"] == "approval needed"
    assert result["failure_modes"] == ["APPROVAL_TIMEOUT"]


def test_capability_manifest_tool_reloads_on_version_bump_event(identity, monkeypatch, tmp_path):
    team_root = tmp_path / "teams"
    team_dir, manifest = _seed_manifest_team(team_root, version="1")
    monkeypatch.setattr("claude_anyteam.wrapper_server._cs_teams.TEAMS_DIR", team_root)
    mcp = build_server()

    first = asyncio.run(
        mcp.call_tool(
            "mcp_anyteam_capability_manifest",
            {"agent_name": "peer-a", "capability": "permission_bridge"},
        )
    ).structured_content
    assert first["version"] == "1"

    manifest["capability_version"] = "2"
    manifest["capabilities"]["permission_bridge"]["version"] = "2"
    manifest["capabilities"]["permission_bridge"]["schema"] = {"type": "object", "required": ["request_id"]}
    manifest_path = team_dir / "manifests" / "peer-a.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    inbox_event = {
        "from": "peer-a",
        "text": json.dumps({
            "type": "capability_manifest_updated",
            "agentName": "peer-a",
            "capabilityVersion": "2",
            "manifestPath": str(manifest_path),
        }),
        "timestamp": "2026-04-27T00:00:00.000Z",
        "read": False,
        "summary": "capability_manifest_updated:peer-a",
        "messageKind": "capability_manifest_updated",
    }
    (team_dir / "inboxes" / "contract-test.json").write_text(json.dumps([inbox_event]), encoding="utf-8")

    second = asyncio.run(
        mcp.call_tool(
            "mcp_anyteam_capability_manifest",
            {"agent_name": "peer-a", "capability": "permission_bridge"},
        )
    ).structured_content
    assert second["version"] == "2"
    assert second["schema"]["required"] == ["request_id"]


def test_capability_manifest_tool_unknown_member_mentions_read_config(identity, monkeypatch, tmp_path):
    team_root = tmp_path / "teams"
    _seed_manifest_team(team_root)
    monkeypatch.setattr("claude_anyteam.wrapper_server._cs_teams.TEAMS_DIR", team_root)

    with pytest.raises(Exception, match="read_config"):
        _call_tool("mcp_anyteam_capability_manifest", {"agent_name": "missing-peer"})


def test_mcp_anyteam_shell_exists_and_produces_output(identity):
    result = _call_tool("mcp_anyteam_shell", {"command": "printf shell-ok"})

    assert result == {"stdout": "shell-ok", "stderr": "", "exit_code": 0}


def test_mcp_anyteam_shell_emits_started_and_completed_events(identity):
    result = _call_tool("mcp_anyteam_shell", {"command": "printf shell-ok"})

    assert result["exit_code"] == 0
    events = pio.read_visibility_events("claude-anyteam", "contract-test")
    assert [e.kind for e in events] == ["tool_event", "tool_event"]
    assert [e.payload["phase"] for e in events] == ["started", "completed"]
    assert [e.payload["tool_name"] for e in events] == [
        "mcp_anyteam_shell",
        "mcp_anyteam_shell",
    ]
    assert all(e.payload["category"] == "shadow_tool" for e in events)
    assert events[0].payload["target"] == "printf shell-ok"
    assert events[1].payload["status"] == "success"
    assert events[1].payload["exit_code"] == 0
    assert all(e.visibility.stderr and e.visibility.event_log for e in events)
    assert not any(e.visibility.mailbox for e in events)


def test_mcp_anyteam_shell_failure_emits_failed_event_and_visibility_degraded_mailbox(
    identity,
):
    result = _call_tool(
        "mcp_anyteam_shell",
        {"command": "sh -c 'echo shell-bad >&2; exit 7'"},
    )

    assert result["exit_code"] == 7
    events = pio.read_visibility_events("claude-anyteam", "contract-test")
    tool_events = [e for e in events if e.kind == "tool_event"]
    assert [e.payload["phase"] for e in tool_events] == ["started", "failed"]
    failed = tool_events[-1]
    assert failed.payload["tool_name"] == "mcp_anyteam_shell"
    assert failed.payload["status"] == "error"
    assert failed.payload["exit_code"] == 7
    assert "shell-bad" in failed.payload["stderr_preview"]

    degraded_events = [e for e in events if e.kind == "visibility_degraded"]
    assert len(degraded_events) == 1
    assert degraded_events[0].payload["tool_name"] == "mcp_anyteam_shell"
    assert degraded_events[0].payload["failed_event_id"] == failed.event_id

    inbox = identity / "claude-anyteam" / "inboxes" / "team-lead.json"
    raw = json.loads(inbox.read_text(encoding="utf-8"))
    assert len(raw) == 1
    assert raw[0]["messageKind"] == "visibility_degraded"
    body = json.loads(raw[0]["text"])
    assert body["kind"] == "visibility_degraded"
    assert body["payload"]["tool_name"] == "mcp_anyteam_shell"



def test_shadow_file_tools_contract(identity, tmp_path):
    file_path = tmp_path / "sample.txt"

    write = _call_tool("mcp_anyteam_write_file", {"path": str(file_path), "content": "one\ntwo\none\n"})
    assert write["mode"] == "overwrite"
    assert write["chars_written"] == len("one\ntwo\none\n")

    read = _call_tool("mcp_anyteam_read_file", {"path": str(file_path), "offset": 1, "limit": 1})
    assert read["content"] == "two\n"
    assert read["encoding"] == "utf-8"
    assert read["truncated"] is True

    edit = _call_tool("mcp_anyteam_edit_file", {"path": str(file_path), "old": "one", "new": "ONE", "replace_all": True})
    assert edit["replacements"] == 2
    assert file_path.read_text() == "ONE\ntwo\nONE\n"

    append = _call_tool("mcp_anyteam_write_file", {"path": str(file_path), "content": "tail\n", "mode": "append"})
    assert append["mode"] == "append"
    assert file_path.read_text().endswith("tail\n")


def test_shadow_list_and_search_contract(identity, tmp_path):
    (tmp_path / "a.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "b.py").write_text("print('alpha')\n", encoding="utf-8")

    listed = _call_tool("mcp_anyteam_list_directory", {"path": str(tmp_path), "recursive": True, "glob": "*.py"})
    assert [entry["path"] for entry in listed["entries"]] == ["nested/b.py"]

    literal = _call_tool("mcp_anyteam_search", {"pattern": "alpha", "path": str(tmp_path), "glob": "*.txt"})
    assert [(m["path"].endswith("a.txt"), m["line"], m["text"]) for m in literal["matches"]] == [(True, 1, "alpha")]

    regex = _call_tool("mcp_anyteam_search", {"pattern": "pr.nt", "path": str(tmp_path), "regex": True, "glob": "*.py"})
    assert len(regex["matches"]) == 1
    assert regex["matches"][0]["line"] == 1

    grep = _call_tool("mcp_anyteam_grep", {"regex": "alpha", "directory": str(tmp_path)})
    assert [
        (m["path"].endswith(suffix), m["line"], m["text"])
        for m, suffix in zip(grep["matches"], ["a.txt", "nested/b.py"])
    ] == [
        (True, 1, "alpha"),
        (True, 1, "print('alpha')"),
    ]


def test_shadow_web_fetch_contract(identity, monkeypatch):
    class Headers(dict):
        def items(self):
            return super().items()

    class Response:
        status = 201
        headers = Headers({"Content-Type": "text/plain"})
        def __enter__(self): return self
        def __exit__(self, exc_type, exc, tb): return False
        def read(self): return b"fetch-ok"
        def geturl(self): return "https://example.test/final"

    seen = {}

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["method"] = request.get_method()
        seen["data"] = request.data
        seen["timeout"] = timeout
        return Response()

    monkeypatch.setattr("claude_anyteam.wrapper_server.urllib.request.urlopen", fake_urlopen)
    result = _call_tool(
        "mcp_anyteam_web_fetch",
        {"url": "https://example.test", "method": "POST", "headers": {"X-Test": "1"}, "body": "payload"},
    )

    assert seen == {"url": "https://example.test", "method": "POST", "data": b"payload", "timeout": 60}
    assert result["status"] == 201
    assert result["headers"]["Content-Type"] == "text/plain"
    assert result["body"] == "fetch-ok"

def test_all_cs50victor_tools_are_categorised():
    """Every cs50victor tool must be either EXPOSED or BLOCKED by name.
    A new tool in an upstream cs50victor version that's neither will
    fail this test, forcing an explicit decision on whether to surface
    it to Codex.
    """
    # We hard-code cs50victor's current 15-tool surface. If upstream grows
    # the tool surface, this list will diverge from live — that's the point.
    cs50victor_current_tools = frozenset({
        "team_create",
        "team_delete",
        "spawn_teammate",
        "send_message",
        "task_create",
        "task_update",
        "task_batch_summary",
        "task_list",
        "task_get",
        "read_inbox",
        "read_config",
        "force_kill_teammate",
        "force_kill_team",
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


def test_task_batch_summary_wrapper_emits_visibility_event(identity, monkeypatch, tmp_path):
    team_root = identity
    _seed_manifest_team(team_root)
    tasks_root = tmp_path / "tasks"
    monkeypatch.setattr("claude_anyteam.wrapper_server._cs_teams.TEAMS_DIR", team_root)
    monkeypatch.setattr("claude_anyteam.wrapper_server._cs_tasks.TASKS_DIR", tasks_root)
    (tasks_root / "claude-anyteam").mkdir(parents=True)
    (tasks_root / "claude-anyteam" / ".lock").touch()
    parent = cs_tasks.create_task("claude-anyteam", "Parent", "delegation root")
    child = cs_tasks.create_task("claude-anyteam", "Child", "delegated")

    result = _call_tool(
        "task_batch_summary",
        {
            "parent_task_id": parent.id,
            "child_tasks": [
                {
                    "task_id": child.id,
                    "status": "completed",
                    "session_id": "session-child",
                    "stop_reason": "task_complete",
                }
            ],
            "summary": "delegated child complete",
        },
    )

    assert result["kind"] == "batch_summary"
    assert result["task_id"] == parent.id
    assert result["payload"]["childTaskIds"] == [child.id]
    assert cs_tasks.get_task("claude-anyteam", child.id).parent_task_id == parent.id
    events = pio.read_visibility_events("claude-anyteam", "contract-test")
    batch_events = [event for event in events if event.kind == "batch_summary"]
    assert len(batch_events) == 1
    assert batch_events[0].payload == result["payload"]
    inbox = json.loads((team_root / "claude-anyteam" / "inboxes" / "team-lead.json").read_text())
    assert inbox[-1]["messageKind"] == "batch_summary"


def test_send_message_accepts_broadcast_star(identity, monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_ANYTEAM_DISABLE_PEER_STEER_MANIFEST_CHECK", "1")
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
        assert payload[0]["messageKind"] == "informational"

    own_inbox = team_root / "claude-anyteam" / "inboxes" / "contract-test.json"
    assert not own_inbox.exists(), "broadcast must not send to the sender's own inbox"


def test_send_message_direct_message_uses_sender_color(identity, monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_ANYTEAM_DISABLE_PEER_STEER_MANIFEST_CHECK", "1")
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
    assert payload[0]["messageKind"] == "informational"


def test_informational_peer_dm_allowed_without_manifest_query(
    identity,
    monkeypatch,
):
    mcp = _seeded_wrapper(identity, monkeypatch)

    result = _call_existing_tool(
        mcp,
        "send_message",
        {"to": "peer-a", "body": "Please use the revised constraint.", "summary": "coordination"},
    )

    assert result == {"delivered_to": "peer-a", "sender": "contract-test"}
    inbox = _peer_a_inbox(identity)
    assert len(inbox) == 1
    assert inbox[0]["text"] == "Please use the revised constraint."
    assert inbox[0]["messageKind"] == "informational"
    assert _wrapper_refusals() == []


def test_default_informational_kind_allows_json_steer_body_without_interrupt_gate(
    identity,
    monkeypatch,
):
    mcp = _seeded_wrapper(identity, monkeypatch)

    result = _call_existing_tool(
        mcp,
        "send_message",
        {"to": "peer-a", "body": _steer_body(), "summary": "coordination"},
    )

    assert result == {"delivered_to": "peer-a", "sender": "contract-test"}
    inbox = _peer_a_inbox(identity)
    assert len(inbox) == 1
    assert inbox[0]["text"] == _steer_body()
    assert inbox[0]["messageKind"] == "informational"
    assert _wrapper_refusals() == []


def test_handoff_peer_dm_allowed_without_manifest_query(
    identity,
    monkeypatch,
):
    mcp = _seeded_wrapper(identity, monkeypatch)

    result = _call_existing_tool(
        mcp,
        "send_message",
        {
            "to": "peer-a",
            "body": "Handoff: I updated the helper; please continue from there.",
            "summary": "handoff",
            "kind": "handoff",
        },
    )

    assert result == {"delivered_to": "peer-a", "sender": "contract-test"}
    inbox = _peer_a_inbox(identity)
    assert len(inbox) == 1
    assert inbox[0]["messageKind"] == "handoff"
    assert _wrapper_refusals() == []


def test_send_message_accepts_typed_lifecycle_payload(
    identity,
    monkeypatch,
):
    mcp = _seeded_wrapper(identity, monkeypatch)
    body = json.dumps(
        {
            "kind": "task_complete",
            "task_id": "7",
            "files_changed": ["src/foo.py"],
            "summary": "done",
            "codex_exit_code": 0,
        }
    )

    result = _call_existing_tool(
        mcp,
        "send_message",
        {
            "to": "peer-a",
            "body": body,
            "summary": "task complete",
            "kind": "task_complete",
        },
    )

    assert result == {"delivered_to": "peer-a", "sender": "contract-test"}
    inbox = _peer_a_inbox(identity)
    assert len(inbox) == 1
    assert inbox[0]["messageKind"] == "task_complete"
    payload = json.loads(inbox[0]["text"])
    assert payload["kind"] == "task_complete"
    assert payload["schema_version"] == 1
    assert payload["task_id"] == "7"


def test_send_message_rejects_lifecycle_kind_with_prose_body(
    identity,
    monkeypatch,
):
    mcp = _seeded_wrapper(identity, monkeypatch)

    with pytest.raises(Exception, match="requires a JSON protocol payload"):
        _call_existing_tool(
            mcp,
            "send_message",
            {
                "to": "peer-a",
                "body": "done",
                "summary": "task complete",
                "kind": "task_complete",
            },
        )

    assert _peer_a_inbox(identity) == []


def test_peer_steer_allowed_for_prose_body_when_recipient_accepts(
    identity,
    monkeypatch,
):
    mcp = _seeded_wrapper(identity, monkeypatch, peer_accepts_steer=True)

    result = _call_existing_tool(
        mcp,
        "send_message",
        {
            "to": "peer-a",
            "body": "Please use the revised constraint.",
            "summary": "peer steer",
            "kind": "steer",
        },
    )

    assert result == {"delivered_to": "peer-a", "sender": "contract-test"}
    inbox = _peer_a_inbox(identity)
    assert len(inbox) == 1
    assert inbox[0]["text"] == "Please use the revised constraint."
    assert inbox[0]["messageKind"] == "steer"
    assert _wrapper_refusals() == []


def test_send_message_visibility_events_include_recipient_aliases(
    identity,
    monkeypatch,
):
    mcp = _seeded_wrapper(identity, monkeypatch)

    result = _call_existing_tool(
        mcp,
        "send_message",
        {
            "to": "peer-a",
            "body": "Answer: telemetry regression probe.",
            "summary": "Answer: telemetry probe",
        },
    )

    assert result == {"delivered_to": "peer-a", "sender": "contract-test"}
    send_events = [
        event
        for event in pio.read_visibility_events("claude-anyteam", "contract-test")
        if event.kind == "tool_event"
        and event.payload.get("tool_name") == "send_message"
    ]
    assert {event.payload.get("phase") for event in send_events} == {
        "started",
        "completed",
    }
    for event in send_events:
        assert event.payload["recipient"] == "peer-a"
        assert event.payload["to"] == "peer-a"
        assert event.payload["target"] == "to='peer-a'"


def test_peer_steer_refused_for_unknown_recipient_capability(
    identity,
    monkeypatch,
):
    mcp = _seeded_wrapper(identity, monkeypatch, peer_manifest=False)

    with pytest.raises(Exception, match="manifest_not_queried"):
        _call_existing_tool(
            mcp,
            "send_message",
            {
                "to": "peer-a",
                "body": "Plain peer steer without a cached manifest.",
                "summary": "peer steer",
                "kind": "steer",
            },
        )

    assert _peer_a_inbox(identity) == []
    refusals = _wrapper_refusals()
    assert len(refusals) == 1
    assert refusals[0].payload["recipient"] == "peer-a"


def test_send_message_rejects_unknown_kind(
    identity,
    monkeypatch,
):
    mcp = _seeded_wrapper(identity, monkeypatch)

    with pytest.raises(Exception, match="kind"):
        _call_existing_tool(
            mcp,
            "send_message",
            {"to": "peer-a", "body": "hello", "summary": "bad kind", "kind": "urgent"},
        )

    assert _peer_a_inbox(identity) == []
    assert _wrapper_refusals() == []


def test_wrapper_peer_steer_refused_when_manifest_denies_peer_steer(
    identity,
    monkeypatch,
):
    mcp = _seeded_wrapper(identity, monkeypatch)

    with pytest.raises(Exception, match="manifest_denies_peer_steer"):
        _call_existing_tool(
            mcp,
            "send_message",
            {
                "to": "peer-a",
                "body": "Please use the revised constraint.",
                "summary": "peer steer",
                "kind": "steer",
            },
        )

    assert _peer_a_inbox(identity) == []
    refusals = _wrapper_refusals()
    assert len(refusals) == 1
    assert refusals[0].payload["sender"] == "contract-test"
    assert refusals[0].payload["recipient"] == "peer-a"
    assert refusals[0].payload["reason"] == "manifest_denies_peer_steer"


def test_wrapper_peer_steer_allowed_with_recent_manifest_query(
    identity,
    monkeypatch,
):
    mcp = _seeded_wrapper(identity, monkeypatch, peer_accepts_steer=True)

    manifest = _call_existing_tool(
        mcp,
        "mcp_anyteam_capability_manifest",
        {"agent_name": "peer-a", "capability": "turn_steer"},
    )
    assert manifest["description"] == "Peer steer"
    result = _call_existing_tool(
        mcp,
        "send_message",
        {"to": "peer-a", "body": "use the fresh plan", "summary": "peer steer", "kind": "steer"},
    )

    assert result == {"delivered_to": "peer-a", "sender": "contract-test"}
    inbox = _peer_a_inbox(identity)
    assert len(inbox) == 1
    assert inbox[0]["text"] == "use the fresh plan"
    assert inbox[0]["messageKind"] == "steer"
    assert _wrapper_refusals() == []


def test_wrapper_peer_steer_manifest_query_stays_fresh_after_intervening_tools(
    identity,
    monkeypatch,
):
    mcp = _seeded_wrapper(identity, monkeypatch, peer_accepts_steer=True)

    _call_existing_tool(
        mcp,
        "mcp_anyteam_capability_manifest",
        {"agent_name": "peer-a", "capability": "turn_steer"},
    )
    _call_existing_tool(mcp, "read_config", {})

    result = _call_existing_tool(
        mcp,
        "send_message",
        {"to": "peer-a", "body": _steer_body(), "summary": "peer steer", "kind": "steer"},
    )

    assert result == {"delivered_to": "peer-a", "sender": "contract-test"}
    assert len(_peer_a_inbox(identity)) == 1
    assert _wrapper_refusals() == []


def test_wrapper_peer_steer_still_refused_after_query_when_manifest_denies(
    identity,
    monkeypatch,
):
    mcp = _seeded_wrapper(identity, monkeypatch)

    manifest = _call_existing_tool(
        mcp,
        "mcp_anyteam_capability_manifest",
        {"agent_name": "peer-a", "capability": "turn_steer"},
    )
    assert manifest["description"] == "Peer steer"

    with pytest.raises(Exception, match="manifest_denies_peer_steer"):
        _call_existing_tool(
            mcp,
            "send_message",
            {"to": "peer-a", "body": _steer_body(), "summary": "peer steer", "kind": "steer"},
        )

    assert _peer_a_inbox(identity) == []
    refusals = _wrapper_refusals()
    assert len(refusals) == 1
    assert refusals[0].payload["reason"] == "manifest_denies_peer_steer"


def test_wrapper_enforce_disabled_ablation(
    identity,
    monkeypatch,
):
    monkeypatch.setenv("CLAUDE_ANYTEAM_DISABLE_PEER_STEER_MANIFEST_CHECK", "1")
    mcp = _seeded_wrapper(identity, monkeypatch)

    result = _call_existing_tool(
        mcp,
        "send_message",
        {"to": "peer-a", "body": _steer_body(), "summary": "peer steer", "kind": "steer"},
    )

    assert result == {"delivered_to": "peer-a", "sender": "contract-test"}
    assert len(_peer_a_inbox(identity)) == 1
    assert _wrapper_refusals() == []


def test_visibility_envelope_emitted_on_wrapper_refusal(
    identity,
    monkeypatch,
):
    mcp = _seeded_wrapper(identity, monkeypatch)

    with pytest.raises(Exception, match="manifest_denies_peer_steer"):
        _call_existing_tool(
            mcp,
            "send_message",
            {"to": "peer-a", "body": _steer_body(), "summary": "peer steer", "kind": "steer"},
        )

    event = _wrapper_refusals()[0]
    assert event.backend == "wrapper_mcp"
    assert event.visibility.mailbox is True
    assert event.payload["surface"] == "peer_steer_refused_at_wrapper"
    assert event.payload["reason"] == "manifest_denies_peer_steer"
    assert event.payload["primitive"] == "turn_steer"
    assert event.payload["max_age_turns"] == 1
    assert "mcp_anyteam_capability_manifest('peer-a', 'turn_steer')" in event.payload["guidance"]

    lead_inbox = identity / "claude-anyteam" / "inboxes" / "team-lead.json"
    raw = json.loads(lead_inbox.read_text(encoding="utf-8"))
    lead_events = [
        json.loads(message["text"])
        for message in raw
        if message.get("messageKind") == "visibility_degraded"
    ]
    assert len(lead_events) == 1
    assert lead_events[0]["event_id"] == event.event_id
    assert lead_events[0]["payload"]["surface"] == "peer_steer_refused_at_wrapper"
