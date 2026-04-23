from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from claude_anyteam.config import Settings
from claude_anyteam import registration as registration_mod


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


def test_concurrent_register_calls_preserve_both_members(tmp_path: Path, monkeypatch):
    base = tmp_path / "teams"
    team = "race-team"
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

    first_read_started = threading.Event()
    original_read_text = Path.read_text
    slept_once = False
    slept_once_lock = threading.Lock()

    def slow_first_config_read(self: Path, *args, **kwargs):
        nonlocal slept_once
        text = original_read_text(self, *args, **kwargs)
        if self == cfg_path:
            with slept_once_lock:
                if not slept_once:
                    slept_once = True
                    first_read_started.set()
                    # Deterministically widen the stale-read window. Without
                    # the registration lock, the second thread can read the
                    # same config bytes and clobber the first writer.
                    time.sleep(0.1)
        return text

    monkeypatch.setattr(Path, "read_text", slow_first_config_read)

    results: list[dict] = []
    errors: list[Exception] = []

    def do_register(name: str) -> None:
        try:
            results.append(registration_mod.register(_settings(team, name, tmp_path)))
        except Exception as exc:  # pragma: no cover - assertion below reports details
            errors.append(exc)

    t1 = threading.Thread(target=do_register, args=("codex-alpha",))
    t2 = threading.Thread(target=do_register, args=("codex-beta",))
    t1.start()
    assert first_read_started.wait(timeout=1.0), "first register() call never reached config read"
    t2.start()
    t1.join(timeout=2.0)
    t2.join(timeout=2.0)

    assert not t1.is_alive()
    assert not t2.is_alive()
    assert errors == []
    assert {entry["name"] for entry in results} == {"codex-alpha", "codex-beta"}

    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    names = {member["name"] for member in cfg["members"] if isinstance(member, dict)}
    assert names == {"team-lead", "codex-alpha", "codex-beta"}
