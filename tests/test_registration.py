from __future__ import annotations

import json
from pathlib import Path

from claude_anyteam import registration as registration_mod
from claude_anyteam.config import Settings
from claude_anyteam.registration import BackendMetadata


def _settings(team: str, name: str, cwd: Path) -> Settings:
    return Settings(
        team_name=team,
        agent_name=name,
        cwd=cwd,
        poll_interval_s=1.5,
        color="cyan",
        plan_mode_required=False,
        codex_binary="codex",
    )


def test_register_writes_tui_visible_teammate_shape(tmp_path: Path, monkeypatch):
    base = tmp_path / "teams"
    team = "shape-team"
    cfg_path = base / team / "config.json"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(
        json.dumps(
            {
                "name": team,
                "members": [{"name": "team-lead", "agentId": f"team-lead@{team}"}],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(registration_mod, "TEAMS_ROOT", base)

    entry = registration_mod.register(_settings(team, "codex-visible", tmp_path))
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    persisted = next(
        member for member in cfg["members"] if isinstance(member, dict) and member["name"] == "codex-visible"
    )

    assert entry["agentId"] == f"codex-visible@{team}"
    assert entry["backendType"] == "in-process"
    assert entry["tmuxPaneId"] == "in-process"
    assert persisted["backendType"] == "in-process"
    assert persisted["tmuxPaneId"] == "in-process"


def test_register_writes_capabilities_from_metadata(tmp_path: Path, monkeypatch):
    base = tmp_path / "teams"
    team = "cap-team"
    cfg_path = base / team / "config.json"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps({"name": team, "members": []}), encoding="utf-8")
    monkeypatch.setattr(registration_mod, "TEAMS_ROOT", base)

    metadata = registration_mod.BackendMetadata(capabilities=["structured_output"])
    entry = registration_mod.register(_settings(team, "codex-visible", tmp_path), metadata)
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    persisted = next(member for member in cfg["members"] if member["name"] == "codex-visible")

    assert entry["capabilities"] == ["structured_output"]
    assert persisted["capabilities"] == ["structured_output"]


def test_register_self_heal_preserves_existing_capabilities(tmp_path: Path, monkeypatch):
    base = tmp_path / "teams"
    team = "cap-preserve-team"
    cfg_path = base / team / "config.json"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(
        json.dumps(
            {
                "name": team,
                "members": [
                    {
                        "name": "codex-visible",
                        "agentId": f"codex-visible@{team}",
                        "agentType": "general-purpose",
                        "backendType": "stale",
                        "capabilities": ["manual_override"],
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(registration_mod, "TEAMS_ROOT", base)

    metadata = registration_mod.BackendMetadata(capabilities=["structured_output"])
    entry = registration_mod.register(_settings(team, "codex-visible", tmp_path), metadata)
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    persisted = next(member for member in cfg["members"] if member["name"] == "codex-visible")

    assert entry["agentType"] == "claude-anyteam"
    assert entry["backendType"] == "in-process"
    assert entry["capabilities"] == ["manual_override"]
    assert persisted["capabilities"] == ["manual_override"]


def test_register_writes_manifest_and_broadcasts_update(tmp_path: Path, monkeypatch):
    base = tmp_path / "teams"
    team = "manifest-team"
    cfg_path = base / team / "config.json"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    (cfg_path.parent / "inboxes").mkdir()
    cfg_path.write_text(
        json.dumps(
            {
                "name": team,
                "members": [
                    {"name": "team-lead", "agentId": f"team-lead@{team}"},
                    {
                        "agentId": f"peer@{team}",
                        "name": "peer",
                        "agentType": "claude-anyteam",
                        "model": "peer-model",
                        "prompt": "peer",
                        "color": "blue",
                        "joinedAt": 0,
                        "tmuxPaneId": "pane",
                        "cwd": str(tmp_path),
                    },
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (cfg_path.parent / "inboxes" / "peer.json").write_text("[]", encoding="utf-8")

    monkeypatch.setattr(registration_mod, "TEAMS_ROOT", base)

    metadata = BackendMetadata(
        model="codex-cli",
        capabilities=["structured_output"],
        capability_manifest={
            "structured_output": {
                "version": "1",
                "schema": {"type": "object"},
                "description": "schema output",
                "when_to_use": "always",
                "when_not_to": "never",
                "failure_modes": ["SCHEMA_VALIDATION_FAILED"],
                "callable_from_peers": False,
            }
        },
        capability_version="1",
        transport="codex-exec",
        host_tool_surface="codex-native",
        coupling_regime="loose",
    )
    registration_mod.register(_settings(team, "codex-a", tmp_path), metadata)

    manifest_path = base / team / "manifests" / "codex-a.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert manifest["capability_version"] == "1"
    assert manifest["agent_name"] == "codex-a"
    assert manifest["transport"] == "codex-exec"
    assert manifest["coupling_regime"] == "loose"
    assert manifest["coupling"]["intent"] == "loose_parallel"
    assert manifest["capabilities"]["structured_output"]["schema"] == {"type": "object"}

    peer_inbox = json.loads((base / team / "inboxes" / "peer.json").read_text(encoding="utf-8"))
    assert peer_inbox[-1]["messageKind"] == "capability_manifest_updated"
    event = json.loads(peer_inbox[-1]["text"])
    assert event["type"] == "capability_manifest_updated"
    assert event["agentName"] == "codex-a"
    assert event["capabilityVersion"] == "1"
    assert event["manifestPath"] == str(manifest_path)


def test_deregister_removes_manifest_and_broadcasts_removed(tmp_path: Path, monkeypatch):
    base = tmp_path / "teams"
    team = "manifest-team"
    cfg_path = base / team / "config.json"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    (cfg_path.parent / "inboxes").mkdir()
    cfg_path.write_text(json.dumps({"name": team, "members": []}), encoding="utf-8")
    monkeypatch.setattr(registration_mod, "TEAMS_ROOT", base)
    settings = _settings(team, "codex-a", tmp_path)

    registration_mod.register(settings, BackendMetadata(capabilities=["structured_output"]))
    assert (base / team / "manifests" / "codex-a.json").exists()

    # Add a remaining peer so deregistration has a recipient for the removal event.
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg["members"].append({
        "agentId": f"peer@{team}",
        "name": "peer",
        "agentType": "claude-anyteam",
        "model": "peer-model",
        "prompt": "peer",
        "color": "blue",
        "joinedAt": 0,
        "tmuxPaneId": "pane",
        "cwd": str(tmp_path),
    })
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    (base / team / "inboxes" / "peer.json").write_text("[]", encoding="utf-8")

    assert registration_mod.deregister(settings) is True
    assert not (base / team / "manifests" / "codex-a.json").exists()
    peer_inbox = json.loads((base / team / "inboxes" / "peer.json").read_text(encoding="utf-8"))
    event = json.loads(peer_inbox[-1]["text"])
    assert peer_inbox[-1]["messageKind"] == "capability_manifest_updated"
    assert event["agentName"] == "codex-a"
    assert event["removed"] is True
