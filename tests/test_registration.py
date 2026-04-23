from __future__ import annotations

import json
from pathlib import Path

from claude_anyteam import registration as registration_mod
from claude_anyteam.config import Settings


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

