from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from claude_anyteam import diagnostics
from claude_anyteam import loop as codex_loop
from claude_anyteam import protocol_io as pio
from claude_anyteam.backends.gemini import loop as gemini_loop
from claude_anyteam.backends.gemini.config import GeminiSettings
from claude_anyteam.backends.kimi import loop as kimi_loop
from claude_anyteam.backends.kimi.config import KimiSettings
from claude_anyteam.capability_manifest import CapabilityManifestCache
from claude_anyteam.config import Settings
from claude_anyteam.messages import CapabilityManifestUpdatedIn


TEAM = "prewarm-team"
SELF = "self"
PEER = "peer-one"
OTHER_PEER = "peer-two"


def _codex_settings(tmp_path: Path) -> Settings:
    return Settings(
        team_name=TEAM,
        agent_name=SELF,
        cwd=tmp_path,
        poll_interval_s=0.01,
        color="cyan",
        plan_mode_required=False,
        codex_binary="codex",
        app_server=True,
    )


def _gemini_settings(tmp_path: Path) -> GeminiSettings:
    return GeminiSettings(
        team_name=TEAM,
        agent_name=SELF,
        cwd=tmp_path,
        poll_interval_s=0.01,
        color="cyan",
        plan_mode_required=False,
        backend="headless",
    )


def _kimi_settings(tmp_path: Path) -> KimiSettings:
    return KimiSettings(
        team_name=TEAM,
        agent_name=SELF,
        cwd=tmp_path,
        poll_interval_s=0.01,
        color="cyan",
        plan_mode_required=False,
    )


def _write_config(teams_root: Path) -> None:
    team_root = teams_root / TEAM
    team_root.mkdir(parents=True, exist_ok=True)
    (team_root / "config.json").write_text(
        json.dumps(
            {
                "members": [
                    {"name": SELF},
                    {"name": PEER},
                    {"name": OTHER_PEER},
                    # Native/unmanifested teammates should be read and skipped
                    # without breaking routed-peer prewarm.
                    {"name": "team-lead"},
                ]
            }
        ),
        encoding="utf-8",
    )


def _write_manifest(
    teams_root: Path,
    agent: str,
    *,
    version: str,
    description: str,
) -> Path:
    path = teams_root / TEAM / "manifests" / f"{agent}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "capability_version": version,
                "team_name": TEAM,
                "agent_name": agent,
                "capabilities": {
                    "thread_fork": {
                        "description": description,
                        "when_to_use": "Use when a peer needs warmed context.",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def _seed_team(teams_root: Path) -> None:
    _write_config(teams_root)
    _write_manifest(teams_root, SELF, version="1", description="self manifest")
    _write_manifest(teams_root, PEER, version="1", description="peer one v1")
    _write_manifest(teams_root, OTHER_PEER, version="1", description="peer two v1")


class FirstInboxPoll(RuntimeError):
    pass


@pytest.mark.parametrize(
    ("backend", "module_name", "settings_factory"),
    [
        ("codex", "codex", _codex_settings),
        ("gemini", "gemini", _gemini_settings),
        ("kimi", "kimi", _kimi_settings),
    ],
)
def test_backend_loops_prewarm_manifests_before_first_inbox_poll(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    module_name: str,
    settings_factory: Callable[[Path], Any],
) -> None:
    teams_root = tmp_path / "teams"
    _seed_team(teams_root)

    events: list[str] = []
    caches: list[CapabilityManifestCache] = []
    read_calls: list[str] = []
    original_read_agent_manifest = pio.read_agent_manifest

    def recording_read_agent_manifest(
        team: str,
        agent: str,
        *,
        teams_root: Path | None = None,
    ) -> dict[str, Any] | None:
        read_calls.append(agent)
        return original_read_agent_manifest(team, agent, teams_root=teams_root)

    class RecordingCache(CapabilityManifestCache):
        def __init__(self, team: str, self_name: str | None = None, **_: Any) -> None:
            super().__init__(team, self_name=self_name, root=teams_root)
            caches.append(self)

        def load_startup(self) -> None:
            events.append("load_startup:start")
            super().load_startup()
            events.append("load_startup:end")

    def first_poll(*_args: Any, **_kwargs: Any) -> list[Any]:
        events.append("first_inbox_poll")
        raise FirstInboxPoll("stop after asserting startup ordering")

    monkeypatch.setattr(pio, "read_agent_manifest", recording_read_agent_manifest)
    monkeypatch.setattr(pio, "read_own_inbox", first_poll)
    monkeypatch.setattr(diagnostics, "record_incident", lambda **_kwargs: "inc-test")

    settings = settings_factory(tmp_path)
    if module_name == "codex":
        monkeypatch.setattr(codex_loop.codex_mod, "feature_test", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(codex_loop, "register", lambda *_args, **_kwargs: {"name": SELF})
        monkeypatch.setattr(codex_loop, "CapabilityManifestCache", RecordingCache)
        monkeypatch.setattr(codex_loop.signal, "signal", lambda *_args, **_kwargs: None)
        exit_code = codex_loop.run(settings)
    elif module_name == "gemini":
        monkeypatch.setattr(gemini_loop, "_backend_feature_test", lambda _settings: None)
        monkeypatch.setattr(gemini_loop, "_backend_auth_preflight", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(gemini_loop, "register", lambda *_args, **_kwargs: {"name": SELF})
        monkeypatch.setattr(gemini_loop, "CapabilityManifestCache", RecordingCache)
        monkeypatch.setattr(gemini_loop.signal, "signal", lambda *_args, **_kwargs: None)
        exit_code = gemini_loop.run(settings)
    else:
        monkeypatch.setattr(kimi_loop, "_backend_feature_test", lambda _settings: None)
        monkeypatch.setattr(kimi_loop, "_backend_auth_preflight", lambda _settings: None)
        monkeypatch.setattr(kimi_loop, "register", lambda *_args, **_kwargs: {"name": SELF})
        monkeypatch.setattr(kimi_loop, "CapabilityManifestCache", RecordingCache)
        monkeypatch.setattr(kimi_loop.signal, "signal", lambda *_args, **_kwargs: None)
        exit_code = kimi_loop.run(settings)

    assert exit_code == 1, f"{backend} exits through the intentional first-poll sentinel"
    assert events == ["load_startup:start", "load_startup:end", "first_inbox_poll"]
    # First read is the backend's own self_capability_manifest. The startup
    # prewarm then walks the roster and calls read_agent_manifest for every
    # member before the first inbox poll.
    assert read_calls[0] == SELF
    assert set(read_calls[1:]) == {SELF, PEER, OTHER_PEER, "team-lead"}
    assert caches and {PEER, OTHER_PEER}.issubset(caches[0].entries)
    assert caches[0].capability_versions[PEER] == "1"


def test_roster_prewarm_uses_bounded_parallel_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    teams_root = tmp_path / "teams"
    _seed_team(teams_root)
    for i in range(6):
        _write_manifest(
            teams_root,
            f"peer-{i}",
            version="1",
            description=f"peer {i}",
        )
    team_root = teams_root / TEAM
    raw = json.loads((team_root / "config.json").read_text(encoding="utf-8"))
    raw["members"] = [{"name": f"peer-{i}"} for i in range(6)]
    (team_root / "config.json").write_text(json.dumps(raw), encoding="utf-8")

    active = 0
    max_active = 0
    lock = threading.Lock()
    original_read_agent_manifest = pio.read_agent_manifest

    def slow_read_agent_manifest(
        team: str,
        agent: str,
        *,
        teams_root: Path | None = None,
    ) -> dict[str, Any] | None:
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        try:
            time.sleep(0.05)
            return original_read_agent_manifest(team, agent, teams_root=teams_root)
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(pio, "read_agent_manifest", slow_read_agent_manifest)
    started = time.monotonic()
    cache = CapabilityManifestCache(TEAM, self_name=SELF, root=teams_root)
    cache.load_startup(concurrency=3, timeout_s=1.0)
    elapsed = time.monotonic() - started

    assert set(cache.entries) == {f"peer-{i}" for i in range(6)}
    assert 1 < max_active <= 3
    assert elapsed < 0.25


def test_roster_prewarm_timeout_skips_slow_peer_without_blocking_fast_peers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    teams_root = tmp_path / "teams"
    _write_config(teams_root)
    team_root = teams_root / TEAM
    raw = json.loads((team_root / "config.json").read_text(encoding="utf-8"))
    raw["members"] = [{"name": "slow-peer"}, {"name": PEER}, {"name": OTHER_PEER}]
    (team_root / "config.json").write_text(json.dumps(raw), encoding="utf-8")
    _write_manifest(teams_root, "slow-peer", version="1", description="slow")
    _write_manifest(teams_root, PEER, version="1", description="fast one")
    _write_manifest(teams_root, OTHER_PEER, version="1", description="fast two")

    original_read_agent_manifest = pio.read_agent_manifest

    def read_agent_manifest_with_slow_peer(
        team: str,
        agent: str,
        *,
        teams_root: Path | None = None,
    ) -> dict[str, Any] | None:
        if agent == "slow-peer":
            time.sleep(0.25)
        return original_read_agent_manifest(team, agent, teams_root=teams_root)

    monkeypatch.setattr(pio, "read_agent_manifest", read_agent_manifest_with_slow_peer)
    started = time.monotonic()
    cache = CapabilityManifestCache(TEAM, self_name=SELF, root=teams_root)
    cache.load_startup(concurrency=2, timeout_s=0.05)
    elapsed = time.monotonic() - started

    assert PEER in cache.entries
    assert OTHER_PEER in cache.entries
    assert "slow-peer" not in cache.entries
    assert elapsed < 0.2


@pytest.mark.parametrize(
    ("backend", "state_factory", "handle_message"),
    [
        (
            "codex",
            lambda settings, cache: codex_loop.LoopState(
                settings=settings,
                peer_manifest_cache=cache,
            ),
            codex_loop._handle_message,
        ),
        (
            "gemini",
            lambda settings, cache: gemini_loop.GeminiLoopState(
                settings=settings,
                peer_manifest_cache=cache,
            ),
            gemini_loop._handle_message,
        ),
        (
            "kimi",
            lambda settings, cache: kimi_loop.KimiLoopState(
                settings=settings,
                peer_manifest_cache=cache,
            ),
            kimi_loop._handle_message,
        ),
    ],
)
def test_capability_manifest_update_event_refreshes_backend_cache(
    tmp_path: Path,
    backend: str,
    state_factory: Callable[[Any, CapabilityManifestCache], Any],
    handle_message: Callable[[Any, Any], None],
) -> None:
    teams_root = tmp_path / "teams"
    _seed_team(teams_root)
    cache = CapabilityManifestCache(TEAM, self_name=SELF, root=teams_root)
    cache.load_startup()
    assert cache.capability_versions[PEER] == "1"
    assert cache.entries[PEER]["capabilities"]["thread_fork"]["description"] == "peer one v1"

    manifest_path = _write_manifest(
        teams_root,
        PEER,
        version="2",
        description=f"{backend} observed peer one v2",
    )
    update = CapabilityManifestUpdatedIn(
        agent_name=PEER,
        capability_version="2",
        manifest_path=str(manifest_path),
    )
    if backend == "codex":
        settings = _codex_settings(tmp_path)
    elif backend == "gemini":
        settings = _gemini_settings(tmp_path)
    else:
        settings = _kimi_settings(tmp_path)
    state = state_factory(settings, cache)

    handle_message(
        state,
        SimpleNamespace(text=update.model_dump_json(by_alias=True, exclude_none=True)),
    )

    assert cache.capability_versions[PEER] == "2"
    assert (
        cache.entries[PEER]["capabilities"]["thread_fork"]["description"]
        == f"{backend} observed peer one v2"
    )
