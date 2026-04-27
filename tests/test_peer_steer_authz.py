from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from claude_anyteam import codex as codex_mod
from claude_anyteam import loop as codex_loop
from claude_anyteam.backends.gemini import loop as gemini_loop
from claude_anyteam.backends.gemini.config import GeminiSettings
from claude_anyteam.backends.kimi import loop as kimi_loop
from claude_anyteam.backends.kimi.config import KimiSettings
from claude_anyteam.config import Settings


TEAM = "peer-steer-authz"
PEER = "codex-exec"


def _codex_settings(tmp_path: Path) -> Settings:
    return Settings(
        team_name=TEAM,
        agent_name="codex-app-server",
        cwd=tmp_path,
        poll_interval_s=1.0,
        color="red",
        plan_mode_required=False,
        codex_binary="codex",
        app_server=True,
    )


def _gemini_settings(tmp_path: Path, *, backend: str) -> GeminiSettings:
    return GeminiSettings(
        team_name=TEAM,
        agent_name=f"gemini-{backend}",
        cwd=tmp_path,
        poll_interval_s=1.0,
        color="green",
        plan_mode_required=False,
        backend=backend,
    )


def _kimi_settings(tmp_path: Path) -> KimiSettings:
    return KimiSettings(
        team_name=TEAM,
        agent_name="kimi-headless",
        cwd=tmp_path,
        poll_interval_s=1.0,
        color="orange",
        plan_mode_required=False,
    )


def _steer_msg(sender: str, message: str = "Please use the revised constraint."):
    return SimpleNamespace(
        from_=sender,
        text=json.dumps({"type": "steer", "from": sender, "message": message}),
    )


def _prose_msg(sender: str, text: str = "FYI: I found the relevant file."):
    return SimpleNamespace(from_=sender, text=text, summary="info")


def _codex_result() -> codex_mod.CodexResult:
    return codex_mod.CodexResult(
        exit_code=0,
        structured={"files_changed": [], "summary": "done"},
        last_message='{"files_changed":[],"summary":"done"}',
        events=[],
        error=None,
    )


def test_peer_steer_from_non_lead_succeeds_for_gemini_acp(tmp_path: Path):
    state = gemini_loop.GeminiLoopState(
        settings=_gemini_settings(tmp_path, backend="acp"),
    )

    gemini_loop._handle_message(state, _steer_msg(PEER))

    assert len(state.queued_steers) == 1
    assert state.queued_steers[0].message == "Please use the revised constraint."


def test_peer_steer_from_non_lead_rejected_for_kimi_headless(
    monkeypatch, tmp_path: Path
):
    state = kimi_loop.KimiLoopState(settings=_kimi_settings(tmp_path))
    warnings: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        kimi_loop.logger,
        "warn",
        lambda msg, **fields: warnings.append((msg, fields)),
    )

    kimi_loop._handle_message(state, _steer_msg(PEER))

    assert state.queued_steers == []
    assert warnings == [
        (
            "kimi.steer.rejected",
            {
                "sender": PEER,
                "reason": "not_team_lead_and_capability_not_declared",
            },
        )
    ]


def test_peer_steer_from_non_lead_rejected_for_codex_app_server(
    monkeypatch, tmp_path: Path
):
    metadata = codex_loop._backend_metadata(_codex_settings(tmp_path))
    queue = codex_mod.SteerQueue(capabilities=metadata.capabilities)
    warnings: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        codex_mod.logger,
        "warn",
        lambda msg, **fields: warnings.append((msg, fields)),
    )

    assert "accepts_peer_steer" not in metadata.capabilities
    assert queue.push("mid-task peer steer", sender=PEER) is False
    assert queue.pop_nowait() is None
    assert warnings == [
        (
            "app_server.steer.rejected",
            {
                "sender": PEER,
                "reason": "not_team_lead_and_capability_not_declared",
            },
        )
    ]


def test_codex_app_server_mid_turn_peer_steer_rejected_by_runtime_capabilities(
    monkeypatch, tmp_path: Path
):
    schema_path = tmp_path / "task-complete.schema.json"
    schema_path.write_text("{}", encoding="utf-8")
    state = codex_loop.LoopState(settings=_codex_settings(tmp_path))
    warnings: list[tuple[str, dict]] = []

    def fake_invoke(**kwargs):
        kwargs["mid_turn_hook"]()
        assert kwargs["steer_queue"].pop_nowait() is None
        return _codex_result()

    monkeypatch.setattr(codex_loop.codex_mod, "TASK_COMPLETE_SCHEMA", schema_path)
    monkeypatch.setattr(
        codex_loop.pio,
        "read_own_inbox",
        lambda *_args: [_steer_msg(PEER)],
    )
    monkeypatch.setattr(codex_loop.codex_mod, "app_server_invoke", fake_invoke)
    monkeypatch.setattr(
        codex_mod.logger,
        "warn",
        lambda msg, **fields: warnings.append((msg, fields)),
    )

    codex_loop._execute_task_app_server(
        state,
        SimpleNamespace(id="42"),
        prompt="do work",
    )

    assert warnings == [
        (
            "app_server.steer.rejected",
            {
                "sender": PEER,
                "reason": "not_team_lead_and_capability_not_declared",
            },
        )
    ]


def test_codex_app_server_mid_turn_peer_prose_without_peer_steer_capability_is_deferred(
    monkeypatch, tmp_path: Path
):
    schema_path = tmp_path / "task-complete.schema.json"
    schema_path.write_text("{}", encoding="utf-8")
    state = codex_loop.LoopState(settings=_codex_settings(tmp_path))
    msg = _prose_msg(PEER)
    deferred: list[tuple[str, str]] = []
    warnings: list[tuple[str, dict]] = []

    def fake_invoke(**kwargs):
        kwargs["mid_turn_hook"]()
        assert kwargs["steer_queue"].pop_nowait() is None
        return _codex_result()

    monkeypatch.setattr(codex_loop.codex_mod, "TASK_COMPLETE_SCHEMA", schema_path)
    monkeypatch.setattr(codex_loop.pio, "read_own_inbox", lambda *_args: [msg])
    monkeypatch.setattr(codex_loop.codex_mod, "app_server_invoke", fake_invoke)
    monkeypatch.setattr(
        codex_loop,
        "_handle_prose",
        lambda _state, prose_msg: deferred.append((prose_msg.from_, prose_msg.text)),
    )
    monkeypatch.setattr(
        codex_mod.logger,
        "warn",
        lambda msg, **fields: warnings.append((msg, fields)),
    )

    codex_loop._execute_task_app_server(
        state,
        SimpleNamespace(id="42"),
        prompt="do work",
    )

    assert deferred == [(PEER, msg.text)]
    assert warnings == []


def test_codex_app_server_mid_turn_lead_prose_still_becomes_steer(
    monkeypatch, tmp_path: Path
):
    schema_path = tmp_path / "task-complete.schema.json"
    schema_path.write_text("{}", encoding="utf-8")
    state = codex_loop.LoopState(settings=_codex_settings(tmp_path))
    msg = _prose_msg("team-lead", "Please checkpoint now.")
    deferred: list[tuple[str, str]] = []

    def fake_invoke(**kwargs):
        kwargs["mid_turn_hook"]()
        assert (
            kwargs["steer_queue"].pop_nowait()
            == "mid-task message from team-lead: Please checkpoint now."
        )
        return _codex_result()

    monkeypatch.setattr(codex_loop.codex_mod, "TASK_COMPLETE_SCHEMA", schema_path)
    monkeypatch.setattr(codex_loop.pio, "read_own_inbox", lambda *_args: [msg])
    monkeypatch.setattr(codex_loop.codex_mod, "app_server_invoke", fake_invoke)
    monkeypatch.setattr(
        codex_loop,
        "_handle_prose",
        lambda _state, prose_msg: deferred.append((prose_msg.from_, prose_msg.text)),
    )

    codex_loop._execute_task_app_server(
        state,
        SimpleNamespace(id="42"),
        prompt="do work",
    )

    assert deferred == []


def test_codex_app_server_mid_turn_peer_prose_becomes_steer_when_recipient_accepts(
    monkeypatch, tmp_path: Path
):
    schema_path = tmp_path / "task-complete.schema.json"
    schema_path.write_text("{}", encoding="utf-8")
    state = codex_loop.LoopState(settings=_codex_settings(tmp_path))
    msg = _prose_msg(PEER, "Use the revised constraint.")
    deferred: list[tuple[str, str]] = []

    def fake_invoke(**kwargs):
        kwargs["mid_turn_hook"]()
        assert (
            kwargs["steer_queue"].pop_nowait()
            == f"mid-task message from {PEER}: Use the revised constraint."
        )
        return _codex_result()

    monkeypatch.setattr(codex_loop.codex_mod, "TASK_COMPLETE_SCHEMA", schema_path)
    monkeypatch.setattr(codex_loop.pio, "read_own_inbox", lambda *_args: [msg])
    monkeypatch.setattr(codex_loop.codex_mod, "app_server_invoke", fake_invoke)
    monkeypatch.setattr(
        codex_loop,
        "_backend_metadata",
        lambda _settings: SimpleNamespace(capabilities=["accepts_peer_steer"]),
    )
    monkeypatch.setattr(
        codex_loop,
        "_handle_prose",
        lambda _state, prose_msg: deferred.append((prose_msg.from_, prose_msg.text)),
    )

    codex_loop._execute_task_app_server(
        state,
        SimpleNamespace(id="42"),
        prompt="do work",
    )

    assert deferred == []


def test_team_lead_steer_allowed_without_peer_steer_capability(tmp_path: Path):
    metadata = codex_loop._backend_metadata(_codex_settings(tmp_path))
    queue = codex_mod.SteerQueue(capabilities=metadata.capabilities)

    assert queue.push("lead steer", sender="team-lead") is True
    assert queue.pop_nowait() == "lead steer"


# ──────────────────────────────────────────────────────────────────────────
# 09 R15-vis-followup (08 CD-6 / 07 §6.5): rejection emits visibility_degraded
# envelope to lead's mailbox + event log alongside the existing logger.warn.
# ──────────────────────────────────────────────────────────────────────────


def _scoped_teams_dir(tmp_path: Path, monkeypatch):
    """Monkeypatch substrate's TEAMS_DIR so emit-and-readback is hermetic."""
    from claude_teams import messaging as cs_messaging

    teams_root = tmp_path / "home" / ".claude" / "teams"
    teams_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cs_messaging, "TEAMS_DIR", teams_root)
    # Pre-create lead inbox + per-agent dirs so emit doesn't need ensure_inbox racing.
    (teams_root / TEAM).mkdir(parents=True, exist_ok=True)
    (teams_root / TEAM / "inboxes").mkdir(parents=True, exist_ok=True)
    return teams_root


def _read_lead_visibility_event(teams_root: Path) -> dict:
    """Read first visibility_event from lead's inbox; assert one is present."""
    inbox = teams_root / TEAM / "inboxes" / "team-lead.json"
    raw = json.loads(inbox.read_text(encoding="utf-8"))
    vis = [m for m in raw if m.get("messageKind") == "visibility_degraded"]
    assert vis, f"no visibility_degraded event in lead inbox; raw={raw}"
    return vis[0]


def test_codex_app_server_peer_steer_rejection_emits_visibility_degraded(
    monkeypatch, tmp_path: Path
):
    teams_root = _scoped_teams_dir(tmp_path, monkeypatch)
    metadata = codex_loop._backend_metadata(_codex_settings(tmp_path))
    queue = codex_mod.SteerQueue(
        capabilities=metadata.capabilities,
        team=TEAM,
        agent="codex-app-server",
    )

    assert queue.push("mid-task peer steer", sender=PEER) is False

    vis = _read_lead_visibility_event(teams_root)
    body = json.loads(vis["text"])
    assert body["kind"] == "visibility_degraded"
    assert body["payload"]["surface"] == "peer_steer_rejected"
    assert body["payload"]["reason"] == "accepts_peer_steer_not_declared"
    assert body["payload"]["sender"] == PEER
    assert body["payload"]["recipient"] == "codex-app-server"
    assert body["backend"] == "codex"

    # Event log also has the envelope per 07 §7.4 fan-out.
    log = (teams_root / TEAM / "events" / "codex-app-server.jsonl").read_text(
        encoding="utf-8"
    )
    assert '"kind":"visibility_degraded"' in log
    assert '"surface":"peer_steer_rejected"' in log


def test_kimi_peer_steer_rejection_emits_visibility_degraded(
    monkeypatch, tmp_path: Path
):
    teams_root = _scoped_teams_dir(tmp_path, monkeypatch)
    state = kimi_loop.KimiLoopState(settings=_kimi_settings(tmp_path))
    payload = SimpleNamespace(
        from_=PEER, message="peer steer", task_id=None, priority="normal", expires_after_turns=1,
    )
    msg = SimpleNamespace(from_=PEER)

    kimi_loop._handle_steer(state, payload, msg)

    vis = _read_lead_visibility_event(teams_root)
    body = json.loads(vis["text"])
    assert body["kind"] == "visibility_degraded"
    assert body["payload"]["sender"] == PEER
    assert body["payload"]["recipient"] == "kimi-headless"
    assert body["backend"] == "kimi"


def test_gemini_headless_peer_steer_rejection_emits_visibility_degraded(
    monkeypatch, tmp_path: Path
):
    teams_root = _scoped_teams_dir(tmp_path, monkeypatch)
    state = gemini_loop.GeminiLoopState(
        settings=_gemini_settings(tmp_path, backend="headless"),
    )
    payload = SimpleNamespace(
        from_=PEER, message="peer steer", task_id=None, priority="normal", expires_after_turns=1,
    )
    msg = SimpleNamespace(from_=PEER)

    gemini_loop._handle_steer(state, payload, msg)

    vis = _read_lead_visibility_event(teams_root)
    body = json.loads(vis["text"])
    assert body["kind"] == "visibility_degraded"
    assert body["payload"]["sender"] == PEER
    assert body["payload"]["recipient"] == "gemini-headless"
    assert body["backend"] == "gemini"


def test_lead_steer_does_not_emit_visibility_degraded(
    monkeypatch, tmp_path: Path
):
    """Negative: when lead steers, no visibility_degraded fires (lead-bypass)."""
    teams_root = _scoped_teams_dir(tmp_path, monkeypatch)
    metadata = codex_loop._backend_metadata(_codex_settings(tmp_path))
    queue = codex_mod.SteerQueue(
        capabilities=metadata.capabilities,
        team=TEAM,
        agent="codex-app-server",
    )

    assert queue.push("lead steer", sender="team-lead") is True

    inbox = teams_root / TEAM / "inboxes" / "team-lead.json"
    if inbox.exists():
        raw = json.loads(inbox.read_text(encoding="utf-8"))
        vis = [m for m in raw if m.get("messageKind") == "visibility_degraded"]
        assert not vis, f"unexpected visibility_degraded for lead steer: {vis}"
