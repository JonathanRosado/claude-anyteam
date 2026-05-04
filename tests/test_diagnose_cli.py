"""Coverage for ``claude-anyteam diagnose`` substrate reports."""

from __future__ import annotations

import io
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from claude_anyteam import diagnose_cli, team_cli
from claude_anyteam.cli import main as cli_main


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(team_cli, "_live_tmux_pane_ids", lambda: None)
    return tmp_path


def _team_dir(home: Path, team: str = "build") -> Path:
    return home / ".claude" / "teams" / team


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _append_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _epoch(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def _seed_team(home: Path) -> Path:
    root = _team_dir(home)
    _write_json(
        root / "config.json",
        {
            "name": "build",
            "members": [
                {
                    "name": "codex-a",
                    "agentType": "claude-anyteam",
                    "model": "gpt-5.5",
                    "backendType": "in-process",
                    "color": "green",
                    "tmuxPaneId": "in-process",
                    "cwd": str(root / "repo-a"),
                    "capabilities": ["structured_output"],
                },
                {
                    "name": "codex-b",
                    "agentType": "claude-anyteam",
                    "model": "gpt-5.4",
                    "backendType": "in-process",
                    "color": "blue",
                    "tmuxPaneId": "in-process",
                    "cwd": str(root / "repo-b"),
                    "capabilities": ["live_tool_events"],
                },
            ],
        },
    )
    for name, capability in (("codex-a", "structured_output"), ("codex-b", "live_tool_events")):
        manifest = root / "manifests" / f"{name}.json"
        _write_json(
            manifest,
            {
                "agent_name": name,
                "capability_version": "2",
                "capabilities": {capability: {"description": capability}},
            },
        )
        os.utime(manifest, (_epoch("2026-04-28T15:19:36Z"), _epoch("2026-04-28T15:19:36Z")))
    _append_jsonl(
        root / "events" / "codex-a.jsonl",
        [
            {
                "kind": "visibility_degraded",
                "timestamp": "2026-04-28T15:20:00Z",
                "event_id": "a-vis",
                "team": "build",
                "agent": "codex-a",
                "summary": "peer steer rejected",
                "payload": {"surface": "peer_steer_rejected"},
            },
            {
                "kind": "turn_progress",
                "timestamp": "2026-04-28T15:21:00Z",
                "event_id": "a-repair",
                "team": "build",
                "agent": "codex-a",
                "summary": "prose.repaired_via_send_message_tool",
                "payload": {"surface": "send_message_repair"},
            },
        ],
    )
    _append_jsonl(
        root / "events" / "codex-b.jsonl",
        [
            {
                "kind": "visibility_degraded",
                "timestamp": "2026-04-28T16:00:00Z",
                "event_id": "b-vis",
                "team": "build",
                "agent": "codex-b",
                "summary": "transport recovered",
                "payload": {"surface": "codex_app_server_transport"},
            }
        ],
    )
    _append_jsonl(
        root / "diagnostics" / "wrapper-mcp-tools.jsonl",
        [
            {
                "schema_version": 1,
                "timestamp": "2026-04-28T15:19:36Z",
                "team": "build",
                "agent": "codex-a",
                "pid": 123,
                "event": "server_registered_snapshot",
                "payload": {
                    "send_message_registered": True,
                    "missing_expected_tools": [],
                    "tool_count": 17,
                },
            },
            {
                "schema_version": 1,
                "timestamp": "2026-04-28T16:00:01Z",
                "team": "build",
                "agent": "codex-b",
                "pid": 456,
                "event": "list_tools",
                "payload": {
                    "send_message_registered": True,
                    "missing_expected_tools": [],
                    "tool_count": 17,
                },
            },
        ],
    )
    return root


def test_diagnose_human_golden_output(fake_home):
    _seed_team(fake_home)
    out = io.StringIO()
    err = io.StringIO()

    rc = diagnose_cli.main(["--team", "build", "--agent", "codex-a", "--limit", "5"], stdout=out, stderr=err)

    assert rc == 0
    assert err.getvalue() == ""
    normalized = out.getvalue().replace(str(fake_home), "<HOME>")
    assert normalized == """claude-anyteam diagnose team=build scope=codex-a mode=read-only
team_dir=<HOME>/.claude/teams/build

[roster]
- codex-a type=claude-anyteam backend=in-process model=gpt-5.5 pid=123 capability_version=2 capabilities=structured_output

[manifest-cache]
- codex-a capability_version=2 mtime=2026-04-28T15:19:36Z status=current

[visibility-degraded:last-50]
total=1
- peer_steer_rejected: count=1 latest=2026-04-28T15:20:00Z agents=codex-a

[flap-repair:#51]
total=1
- 2026-04-28T15:21:00Z codex-a prose.repaired_via_send_message_tool

[wrapper-mcp-diagnostics]
path=<HOME>/.claude/teams/build/diagnostics/wrapper-mcp-tools.jsonl
- 2026-04-28T15:19:36Z agent=codex-a event=server_registered_snapshot pid=123 send_message_registered=True missing=[]

[sandbox-markers]
(none)

[health]
🟢 adapter_mcp_responsive: latest wrapper snapshots registered all expected tools
🟢 manifest_cache_populated: manifests present for all scoped routed members
🟢 capability_hooks_registered: advertised capabilities have runtime hooks
🟡 sandbox_markers_expected: no stress sandbox marker found for scoped cwd
"""


def test_diagnose_json_routes_agent_and_since_filters(fake_home):
    _seed_team(fake_home)
    out = io.StringIO()

    rc = diagnose_cli.main(
        [
            "--team",
            "build",
            "--agent",
            "codex-b",
            "--since",
            "2026-04-28T15:30:00Z",
            "--json",
        ],
        stdout=out,
        stderr=io.StringIO(),
    )

    assert rc == 0
    payload = json.loads(out.getvalue())
    assert [row["name"] for row in payload["roster"]] == ["codex-b"]
    assert [row["agent"] for row in payload["manifest_cache"]] == ["codex-b"]
    assert payload["visibility_degraded_last_50"]["total"] == 1
    assert set(payload["visibility_degraded_last_50"]["categories"]) == {"codex_app_server_transport"}
    assert payload["flap_repair_51"]["total"] == 0
    assert payload["wrapper_mcp_diagnostics"]["recent"][0]["agent"] == "codex-b"
    assert payload["wrapper_mcp_diagnostics"]["recent"][0]["pid"] == 456


def test_diagnose_instrument_spawn_writes_settings_env(fake_home):
    _seed_team(fake_home)
    settings = fake_home / ".claude" / "settings.json"
    _write_json(settings, {"env": {"EXISTING": "kept"}})
    out = io.StringIO()

    rc = diagnose_cli.main(
        ["--team", "build", "--agent", "codex-a", "--instrument-spawn", "--json"],
        stdout=out,
        stderr=io.StringIO(),
    )

    assert rc == 0
    payload = json.loads(out.getvalue())
    assert payload["mode"] == "mutating"
    assert payload["instrumentation"]["env_key"] == diagnose_cli.INSTRUMENT_ENV_KEY
    stored = json.loads(settings.read_text(encoding="utf-8"))
    assert stored["env"] == {
        "EXISTING": "kept",
        diagnose_cli.INSTRUMENT_ENV_KEY: diagnose_cli.INSTRUMENT_ENV_VALUE,
    }


def test_team_roster_json_includes_diagnostic_enrichments(fake_home, capsys):
    _seed_team(fake_home)

    rc = cli_main(["team-roster", "--team", "build", "--json"])

    assert rc == 0
    rows = {row["name"]: row for row in json.loads(capsys.readouterr().out)}
    assert rows["codex-a"]["capability_version"] == "2"
    assert rows["codex-a"]["adapter_pid"] == 123
    assert rows["codex-a"]["adapter_pid_source"] == "wrapper-mcp-tools.jsonl"


def test_diagnose_bundle_wraps_report_and_sanitizes_home(fake_home, monkeypatch):
    """`--bundle` produces a markdown envelope with versions + sanitized paths.

    Locks the operational invariants the bundle is meant to support: the
    `## Versions` section is present, the user's home directory is replaced
    with `~/` in the embedded report (preventing accidental username leak when
    the bundle is pasted into a public GitHub issue), the inner report sits
    inside a four-backtick fence (so any nested triple-fence content survives
    GitHub's renderer), and the bundle docstring tells the user to scrub team
    or agent names manually if sensitive.

    Stubbing `_collect_versions_and_env` keeps the test deterministic against
    hosts that may or may not have codex / gemini / kimi installed.
    """
    _seed_team(fake_home)

    monkeypatch.setattr(
        diagnose_cli,
        "_collect_versions_and_env",
        lambda: {
            "claude_anyteam": "0.8.3",
            "codex": "0.124.0",
            "gemini": None,  # not installed → "(not installed)"
            "kimi": "1.40.0",
            "python": "3.12.3",
            "os_system": "Linux",
            "os_release": "6.6.87.2-microsoft-standard-WSL2",
            "wsl": True,
        },
    )

    out = io.StringIO()
    err = io.StringIO()
    rc = diagnose_cli.main(["--team", "build", "--bundle"], stdout=out, stderr=err)

    assert rc == 0
    assert err.getvalue() == ""
    body = out.getvalue()

    # Markdown envelope
    assert body.startswith("# claude-anyteam diagnose bundle\n")
    assert "## Versions" in body
    assert "## Scope" in body
    assert "## Report" in body
    assert "## Suggested next steps" in body

    # Versions
    assert "- claude-anyteam: 0.8.3" in body
    assert "- codex CLI: 0.124.0" in body
    assert "- gemini CLI: (not installed)" in body
    assert "- kimi CLI: 1.40.0" in body
    assert "- Python: 3.12.3" in body
    assert "- OS: Linux 6.6.87.2-microsoft-standard-WSL2 (WSL detected)" in body

    # Inner report — four-backtick fence (so triple-fences inside the rendered
    # human report do not break GitHub's nested-fence rendering).
    assert "\n````\n" in body
    assert body.count("````") >= 2  # opening + closing

    # Path sanitization — user's home dir is replaced with `~/` in the
    # embedded report. The seeded team's `team_dir` is `<fake_home>/.claude/...`
    # so that exact prefix should not appear in the bundle.
    assert str(fake_home) not in body, (
        "user home directory leaked into bundle output despite sanitize step"
    )
    assert "team_dir=~/.claude/teams/build" in body

    # Footer guidance the bundle docstring promises — keeps the user informed
    # about manual scrubs and instrumentation follow-up.
    assert "scrub team/agent names" in body
    assert "events/<agent>.jsonl" in body
    assert "--instrument-spawn" in body


def test_diagnose_bundle_rejects_combined_with_json(fake_home):
    """`--bundle` and `--json` are mutually exclusive (different output shapes)."""
    _seed_team(fake_home)
    out = io.StringIO()
    err = io.StringIO()
    rc = diagnose_cli.main(["--team", "build", "--bundle", "--json"], stdout=out, stderr=err)
    assert rc == 2
    assert "cannot be combined with --json" in err.getvalue()
    assert out.getvalue() == ""


def test_diagnose_bundle_rejects_combined_with_incident_modes(fake_home):
    """`--bundle` is meant for the substrate report, not the legacy incident path."""
    _seed_team(fake_home)
    out = io.StringIO()
    err = io.StringIO()
    rc = diagnose_cli.main(["--bundle", "--incidents"], stdout=out, stderr=err)
    assert rc == 2
    assert "cannot be combined with incident mode" in err.getvalue()
    assert out.getvalue() == ""


def test_diagnose_probe_cli_version_returns_none_for_missing_binary():
    """The version-probe helper is defensive: missing binary → None, no exception."""
    assert diagnose_cli._probe_cli_version("definitely-not-a-real-binary-xyz") is None
