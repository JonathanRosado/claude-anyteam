"""Coverage for the ``claude-anyteam team-*`` subcommands.

Each test patches HOME via monkeypatch so writes land in tmp_path; no real
~/.claude state is touched.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from claude_anyteam import team_cli
from claude_anyteam.cli import main as cli_main
from claude_teams import teardown as team_teardown


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path


def _agent_path(home: Path, team: str, agent: str) -> Path:
    return home / ".claude" / "teams" / team / "agents" / f"{agent}.json"


def _team_path(home: Path, team: str) -> Path:
    return home / ".claude" / "teams" / team / "config.json"


def _task_path(home: Path, team: str, task_id: str) -> Path:
    return home / ".claude" / "tasks" / team / f"{task_id}.json"


def _seed_task(home: Path, team: str, task_id: str, *, owner: str, status: str = "in_progress") -> None:
    path = _task_path(home, team, task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    (path.parent / ".lock").touch()
    path.write_text(json.dumps({
        "id": task_id,
        "subject": f"task {task_id}",
        "description": "seed task",
        "status": status,
        "owner": owner,
    }))


# --------------------------------------------------------------------------- #
# team-agent
# --------------------------------------------------------------------------- #


def test_team_agent_writes_model_and_effort(fake_home, capsys):
    rc = cli_main(["team-agent", "codex-alice", "--team", "build", "--model", "gpt-5.5", "--effort", "xhigh"])
    assert rc == 0
    cfg = json.loads(_agent_path(fake_home, "build", "codex-alice").read_text())
    assert cfg == {"model": "gpt-5.5", "effort": "xhigh"}
    out = capsys.readouterr().out
    assert "wrote " in out
    assert "model=gpt-5.5" in out
    assert "effort=xhigh" in out


def test_team_agent_model_only_omits_effort(fake_home):
    rc = cli_main(["team-agent", "kimi-cara", "--team", "build", "--model", "kimi-for-coding"])
    assert rc == 0
    cfg = json.loads(_agent_path(fake_home, "build", "kimi-cara").read_text())
    assert cfg == {"model": "kimi-for-coding"}


def test_team_agent_effort_only_omits_model(fake_home):
    rc = cli_main(["team-agent", "gemini-bob", "--team", "build", "--effort", "high"])
    assert rc == 0
    cfg = json.loads(_agent_path(fake_home, "build", "gemini-bob").read_text())
    assert cfg == {"effort": "high"}


def test_team_agent_writes_non_progress_watchdog_keys(fake_home, capsys):
    rc = cli_main(
        [
            "team-agent",
            "codex-alice",
            "--team",
            "build",
            "--non-progress-warn-s",
            "180",
            "--non-progress-interrupt-s",
            "420",
        ]
    )
    assert rc == 0
    cfg = json.loads(_agent_path(fake_home, "build", "codex-alice").read_text())
    assert cfg == {
        "non_progress_warn_s": 180.0,
        "non_progress_interrupt_s": 420.0,
    }
    out = capsys.readouterr().out
    assert "non_progress_warn_s=180.0" in out
    assert "non_progress_interrupt_s=420.0" in out


def test_team_agent_writes_wrapper_tool_failure_window(fake_home, capsys):
    rc = cli_main(
        [
            "team-agent",
            "codex-alice",
            "--team",
            "build",
            "--wrapper-tool-failure-window-s",
            "120",
        ]
    )
    assert rc == 0
    cfg = json.loads(_agent_path(fake_home, "build", "codex-alice").read_text())
    assert cfg == {"wrapper_tool_failure_window_s": 120.0}
    assert "wrapper_tool_failure_window_s=120.0" in capsys.readouterr().out


def test_team_agent_neither_model_nor_effort_is_an_error(fake_home, capsys):
    rc = cli_main(["team-agent", "codex-alice", "--team", "build"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "at least one of --model/--effort" in err


def test_team_agent_overwrites_existing_keys(fake_home):
    cli_main(["team-agent", "codex-alice", "--team", "build", "--model", "gpt-5.4", "--effort", "low"])
    cli_main(["team-agent", "codex-alice", "--team", "build", "--model", "gpt-5.5"])
    cfg = json.loads(_agent_path(fake_home, "build", "codex-alice").read_text())
    # Effort persists from the first call; model is updated by the second
    assert cfg == {"model": "gpt-5.5", "effort": "low"}


def test_team_agent_strips_unknown_keys_on_write(fake_home):
    path = _agent_path(fake_home, "build", "codex-alice")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"model": "gpt-5.5", "effort": "high", "rogue_key": "bad"}))
    cli_main(["team-agent", "codex-alice", "--team", "build", "--effort", "xhigh"])
    cfg = json.loads(path.read_text())
    assert cfg == {"model": "gpt-5.5", "effort": "xhigh"}
    assert "rogue_key" not in cfg


def test_team_agent_remove_deletes_existing(fake_home, capsys):
    cli_main(["team-agent", "codex-alice", "--team", "build", "--model", "gpt-5.5"])
    rc = cli_main(["team-agent", "codex-alice", "--team", "build", "--remove"])
    assert rc == 0
    assert not _agent_path(fake_home, "build", "codex-alice").exists()
    assert "removed " in capsys.readouterr().out


def test_team_agent_remove_missing_is_idempotent(fake_home, capsys):
    rc = cli_main(["team-agent", "codex-alice", "--team", "build", "--remove"])
    assert rc == 0
    assert "no config to remove" in capsys.readouterr().out


def test_team_agent_rejects_path_traversal_in_team(fake_home, capsys):
    with pytest.raises(SystemExit) as exc:
        cli_main(["team-agent", "codex-alice", "--team", "../etc", "--model", "x"])
    assert exc.value.code == 2


def test_team_agent_rejects_path_traversal_in_agent(fake_home, capsys):
    with pytest.raises(SystemExit) as exc:
        cli_main(["team-agent", "codex/../alice", "--team", "build", "--model", "x"])
    assert exc.value.code == 2


def test_team_agent_print_path_emits_only_path(fake_home, capsys):
    rc = cli_main(["team-agent", "codex-alice", "--team", "build", "--model", "gpt-5.5", "--print-path"])
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert out == str(_agent_path(fake_home, "build", "codex-alice"))


def test_team_agent_invalid_effort_is_rejected(fake_home, capsys):
    with pytest.raises(SystemExit) as exc:
        cli_main(["team-agent", "codex-alice", "--team", "build", "--effort", "absurd"])
    assert exc.value.code == 2


def test_team_agent_invalid_non_progress_values_are_rejected(fake_home, capsys):
    rc = cli_main(
        [
            "team-agent",
            "codex-alice",
            "--team",
            "build",
            "--non-progress-warn-s",
            "30",
        ]
    )
    assert rc == 2
    assert "--non-progress-warn-s" in capsys.readouterr().err

    rc = cli_main(
        [
            "team-agent",
            "codex-alice",
            "--team",
            "build",
            "--non-progress-interrupt-s",
            "30",
        ]
    )
    assert rc == 2
    assert "--non-progress-interrupt-s" in capsys.readouterr().err

    rc = cli_main(
        [
            "team-agent",
            "codex-alice",
            "--team",
            "build",
            "--wrapper-tool-failure-window-s",
            "30",
        ]
    )
    assert rc == 2
    assert "--wrapper-tool-failure-window-s" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# team-patch
# --------------------------------------------------------------------------- #


def _seed_team_config(home: Path, team: str, members: list[dict]) -> None:
    p = _team_path(home, team)
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({
        "name": team,
        "description": "",
        "createdAt": 1,
        "leadAgentId": f"team-lead@{team}",
        "leadSessionId": "test-session",
        "members": members,
    }))


def test_team_patch_sets_agent_type_for_named_agent(fake_home, capsys):
    _seed_team_config(fake_home, "build", [
        {"name": "codex-alice", "agentType": "general-purpose"},
        {"name": "codex-bob", "agentType": "claude-anyteam"},
    ])
    rc = cli_main(["team-patch", "codex-alice", "--team", "build"])
    assert rc == 0
    cfg = json.loads(_team_path(fake_home, "build").read_text())
    members = {m["name"]: m for m in cfg["members"]}
    assert members["codex-alice"]["agentType"] == "claude-anyteam"
    assert members["codex-bob"]["agentType"] == "claude-anyteam"
    assert "patched agentType=claude-anyteam on 1 member" in capsys.readouterr().out


def test_team_patch_all_external_patches_routed_prefixes(fake_home, capsys):
    _seed_team_config(fake_home, "build", [
        {"name": "team-lead", "agentType": "tech-lead"},
        {"name": "codex-alice", "agentType": "general-purpose"},
        {"name": "gemini-bob", "agentType": "general-purpose"},
        {"name": "kimi-cara", "agentType": "general-purpose"},
        {"name": "researcher", "agentType": "research"},
    ])
    rc = cli_main(["team-patch", "--team", "build", "--all-external"])
    assert rc == 0
    cfg = json.loads(_team_path(fake_home, "build").read_text())
    members = {m["name"]: m for m in cfg["members"]}
    assert members["codex-alice"]["agentType"] == "claude-anyteam"
    assert members["gemini-bob"]["agentType"] == "claude-anyteam"
    assert members["kimi-cara"]["agentType"] == "claude-anyteam"
    # Non-routed members untouched
    assert members["team-lead"]["agentType"] == "tech-lead"
    assert members["researcher"]["agentType"] == "research"


def test_team_patch_idempotent_no_changes(fake_home, capsys):
    _seed_team_config(fake_home, "build", [
        {"name": "codex-alice", "agentType": "claude-anyteam"},
    ])
    rc = cli_main(["team-patch", "codex-alice", "--team", "build"])
    assert rc == 0
    assert "no changes needed" in capsys.readouterr().out


def test_team_patch_unknown_agent_warns_and_exits_nonzero(fake_home, capsys):
    _seed_team_config(fake_home, "build", [
        {"name": "codex-alice", "agentType": "general-purpose"},
    ])
    rc = cli_main(["team-patch", "codex-bob", "--team", "build"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "members not found: codex-bob" in err


def test_team_patch_requires_either_agent_or_all_external(fake_home, capsys):
    _seed_team_config(fake_home, "build", [{"name": "codex-alice", "agentType": "general-purpose"}])
    rc = cli_main(["team-patch", "--team", "build"])
    assert rc == 2


def test_team_patch_rejects_both_agent_and_all_external(fake_home, capsys):
    _seed_team_config(fake_home, "build", [{"name": "codex-alice", "agentType": "general-purpose"}])
    rc = cli_main(["team-patch", "codex-alice", "--team", "build", "--all-external"])
    assert rc == 2


def test_team_patch_missing_team_config(fake_home, capsys):
    rc = cli_main(["team-patch", "codex-alice", "--team", "missing"])
    assert rc == 1
    assert "no team config" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# team-roster
# --------------------------------------------------------------------------- #


def test_team_roster_human_readable(fake_home, capsys):
    _seed_team_config(fake_home, "build", [
        {"name": "team-lead", "agentType": "tech-lead", "model": "claude-opus-4-7", "backendType": "tmux", "color": "blue"},
        {
            "name": "codex-alice",
            "agentType": "claude-anyteam",
            "model": "codex-cli",
            "backendType": "in-process",
            "color": "green",
            "capabilities": ["structured_output", "thread_fork"],
        },
    ])
    rc = cli_main(["team-roster", "--team", "build"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "team-lead" in out
    assert "codex-alice" in out
    assert "codex-cli" in out
    assert "tech-lead" in out
    assert "capabilities=-" in out
    assert "capabilities=structured_output,thread_fork" in out


def test_team_roster_json_output(fake_home, capsys):
    _seed_team_config(fake_home, "build", [
        {"name": "codex-alice", "agentType": "claude-anyteam", "model": "codex-cli", "backendType": "in-process", "color": "green"},
    ])
    rc = cli_main(["team-roster", "--team", "build", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["name"] == "codex-alice"
    assert payload[0]["model"] == "codex-cli"


def test_team_roster_native_claude_capabilities_empty_or_host_supplied(fake_home, capsys):
    _seed_team_config(fake_home, "build", [
        {
            "name": "claude-a",
            "agentType": "claude",
            "model": "sonnet",
            "backendType": "claude_native",
            "color": "blue",
        },
        {
            "name": "claude-b",
            "agentType": "claude",
            "model": "opus",
            "backendType": "claude_native",
            "color": "purple",
            "capabilities": ["host_supplied"],
        },
    ])

    rc = cli_main(["team-roster", "--team", "build", "--json"])

    assert rc == 0
    payload = {row["name"]: row for row in json.loads(capsys.readouterr().out)}
    assert payload["claude-a"]["agent_type"] == "claude"
    assert payload["claude-a"]["capabilities"] == []
    assert payload["claude-b"]["agent_type"] == "claude"
    assert payload["claude-b"]["capabilities"] == ["host_supplied"]


def test_team_roster_empty_members(fake_home, capsys):
    _seed_team_config(fake_home, "build", [])
    rc = cli_main(["team-roster", "--team", "build"])
    assert rc == 0
    assert "has no members" in capsys.readouterr().out


def test_team_roster_missing_team(fake_home, capsys):
    rc = cli_main(["team-roster", "--team", "missing"])
    assert rc == 1
    assert "no team config" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# team-kill
# --------------------------------------------------------------------------- #


def test_team_kill_requires_force(fake_home, capsys):
    _seed_team_config(fake_home, "build", [
        {"name": "team-lead", "agentId": "team-lead@build", "agentType": "team-lead", "model": "opus", "joinedAt": 1, "tmuxPaneId": "", "cwd": "/tmp"},
        {
            "name": "codex-alice",
            "agentId": "codex-alice@build",
            "agentType": "claude-anyteam",
            "model": "codex-cli",
            "prompt": "work",
            "color": "green",
            "joinedAt": 2,
            "tmuxPaneId": "%1",
            "cwd": "/tmp",
            "backendType": "in-process",
        },
    ])

    rc = cli_main(["team-kill", "--team", "build", "--timeout-s", "0"])

    assert rc == 2
    assert "re-run with --force" in capsys.readouterr().err
    cfg = json.loads(_team_path(fake_home, "build").read_text())
    assert [m["name"] for m in cfg["members"]] == ["team-lead", "codex-alice"]


def test_team_kill_force_removes_members_and_resets_tasks(fake_home, monkeypatch, capsys):
    _seed_team_config(fake_home, "build", [
        {"name": "team-lead", "agentId": "team-lead@build", "agentType": "team-lead", "model": "opus", "joinedAt": 1, "tmuxPaneId": "", "cwd": "/tmp"},
        {
            "name": "codex-alice",
            "agentId": "codex-alice@build",
            "agentType": "claude-anyteam",
            "model": "codex-cli",
            "prompt": "work",
            "color": "green",
            "joinedAt": 2,
            "tmuxPaneId": "%1",
            "cwd": "/tmp",
            "backendType": "in-process",
        },
        {
            "name": "gemini-bob",
            "agentId": "gemini-bob@build",
            "agentType": "claude-anyteam",
            "model": "gemini-cli",
            "prompt": "work",
            "color": "yellow",
            "joinedAt": 3,
            "tmuxPaneId": "in-process",
            "cwd": "/tmp",
            "backendType": "in-process",
        },
    ])
    _seed_task(fake_home, "build", "1", owner="codex-alice")
    killed_panes: list[str] = []
    monkeypatch.setattr(team_teardown, "kill_tmux_pane", lambda pane_id: killed_panes.append(pane_id))
    monkeypatch.setattr(team_teardown, "_kill_validated_wrapper_pid", lambda *args, **kwargs: (False, []))

    rc = cli_main(["team-kill", "--team", "build", "--force", "--timeout-s", "0"])

    assert rc == 0
    assert killed_panes == ["%1"]
    cfg = json.loads(_team_path(fake_home, "build").read_text())
    assert [m["name"] for m in cfg["members"]] == ["team-lead"]
    task = json.loads(_task_path(fake_home, "build", "1").read_text())
    assert task["status"] == "pending"
    assert "owner" not in task
    out = capsys.readouterr().out
    assert "shutdown_request sent to 2 teammate" in out
    assert "2 force-killed" in out


def test_team_kill_purge_deletes_team_and_tasks_dirs(fake_home, monkeypatch):
    _seed_team_config(fake_home, "build", [
        {"name": "team-lead", "agentId": "team-lead@build", "agentType": "team-lead", "model": "opus", "joinedAt": 1, "tmuxPaneId": "", "cwd": "/tmp"},
        {
            "name": "codex-alice",
            "agentId": "codex-alice@build",
            "agentType": "claude-anyteam",
            "model": "codex-cli",
            "prompt": "work",
            "color": "green",
            "joinedAt": 2,
            "tmuxPaneId": "%1",
            "cwd": "/tmp",
            "backendType": "in-process",
        },
    ])
    _seed_task(fake_home, "build", "1", owner="codex-alice")
    monkeypatch.setattr(team_teardown, "kill_tmux_pane", lambda pane_id: None)
    monkeypatch.setattr(team_teardown, "_kill_validated_wrapper_pid", lambda *args, **kwargs: (False, []))

    rc = cli_main(["team-kill", "--team", "build", "--force", "--purge", "--timeout-s", "0"])

    assert rc == 0
    assert not (fake_home / ".claude" / "teams" / "build").exists()
    assert not (fake_home / ".claude" / "tasks" / "build").exists()


def test_force_kill_team_counts_members_that_deregister_during_grace_period(fake_home, monkeypatch):
    _seed_team_config(fake_home, "build", [
        {"name": "team-lead", "agentId": "team-lead@build", "agentType": "team-lead", "model": "opus", "joinedAt": 1, "tmuxPaneId": "", "cwd": "/tmp"},
        {
            "name": "codex-alice",
            "agentId": "codex-alice@build",
            "agentType": "claude-anyteam",
            "model": "codex-cli",
            "prompt": "work",
            "color": "green",
            "joinedAt": 2,
            "tmuxPaneId": "%1",
            "cwd": "/tmp",
            "backendType": "in-process",
        },
    ])
    base_dir = fake_home / ".claude"

    def fake_send_shutdown(team_name, recipient, reason="", base_dir=None):
        from claude_teams import teams
        teams.remove_member(team_name, recipient, base_dir=base_dir)
        return f"shutdown-test@{recipient}"

    monkeypatch.setattr(team_teardown.messaging, "send_shutdown_request", fake_send_shutdown)
    monkeypatch.setattr(
        team_teardown,
        "kill_tmux_pane",
        lambda pane_id: pytest.fail("graceful exits must not be force-killed"),
    )

    result = team_teardown.force_kill_team(
        "build",
        force=True,
        graceful_timeout_s=0.2,
        base_dir=base_dir,
    )

    assert result["graceful"] == ["codex-alice"]
    assert result["forced"] == []
    assert result["members"][0]["graceful"] is True


def test_force_kill_team_four_member_stuck_path_finishes_under_budget(fake_home, monkeypatch):
    members = [
        {"name": "team-lead", "agentId": "team-lead@build", "agentType": "team-lead", "model": "opus", "joinedAt": 1, "tmuxPaneId": "", "cwd": "/tmp"},
    ]
    for idx in range(4):
        name = f"codex-{idx}"
        members.append({
            "name": name,
            "agentId": f"{name}@build",
            "agentType": "claude-anyteam",
            "model": "codex-cli",
            "prompt": "work",
            "color": "green",
            "joinedAt": idx + 2,
            "tmuxPaneId": f"%{idx + 1}",
            "cwd": "/tmp",
            "backendType": "in-process",
        })
    _seed_team_config(fake_home, "build", members)
    task_dir = fake_home / ".claude" / "tasks" / "build"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / ".lock").touch()
    killed_panes: list[str] = []
    monkeypatch.setattr(team_teardown, "kill_tmux_pane", lambda pane_id: killed_panes.append(pane_id))
    monkeypatch.setattr(team_teardown, "_kill_validated_wrapper_pid", lambda *args, **kwargs: (False, []))

    result = team_teardown.force_kill_team(
        "build",
        force=True,
        graceful_timeout_s=0.01,
        base_dir=fake_home / ".claude",
    )

    assert result["elapsed_s"] < 10.0
    assert len(result["forced"]) == 4
    assert killed_panes == ["%1", "%2", "%3", "%4"]
    cfg = json.loads(_team_path(fake_home, "build").read_text())
    assert [m["name"] for m in cfg["members"]] == ["team-lead"]


# --------------------------------------------------------------------------- #
# Atomic write semantics
# --------------------------------------------------------------------------- #


def test_team_agent_write_is_atomic(fake_home):
    """The .tmp sibling must not survive a successful write."""
    cli_main(["team-agent", "codex-alice", "--team", "build", "--model", "gpt-5.5"])
    parent = _agent_path(fake_home, "build", "codex-alice").parent
    leftovers = [p for p in parent.iterdir() if p.suffix == ".tmp"]
    assert leftovers == []
