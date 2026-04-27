from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from claude_teams import messaging as cs_messaging  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claude_anyteam import protocol_io as pio
from tools.stress import score_collab

BASE = datetime(2026, 4, 27, 15, 30, tzinfo=timezone.utc)


@pytest.fixture
def teams_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    base = tmp_path / "teams"
    monkeypatch.setattr(cs_messaging, "TEAMS_DIR", base)
    return base


def ts(seconds: float) -> str:
    return (BASE + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def append_event(
    team: str,
    agent: str,
    kind: str,
    seq: int,
    *,
    at: float = 0,
    backend: str = "codex_app_server",
    turn_id: str | None = None,
    summary: str | None = None,
    payload: dict | None = None,
):
    return pio.append_event(
        team,
        agent,
        {
            "kind": kind,
            "event_id": f"{agent}:{turn_id or 'turn'}:{seq}",
            "timestamp": ts(at),
            "team": team,
            "agent": agent,
            "backend": backend,
            "task_id": "task-1",
            "turn_id": turn_id,
            "seq": seq,
            "severity": "info",
            "summary": summary or f"{kind} {seq}",
            "payload": payload or {},
        },
    )


def send(
    team: str,
    agent: str,
    seq: int,
    to: str | None,
    *,
    at: float = 0,
    turn_id: str | None = None,
    summary: str = "ask: status?",
    backend: str = "codex_app_server",
    payload_extra: dict | None = None,
):
    payload = {"tool_name": "send_message", "phase": "completed", "summary": summary}
    if to is not None:
        payload["recipient"] = to
    if payload_extra:
        payload.update(payload_extra)
    return append_event(
        team,
        agent,
        "tool_event",
        seq,
        at=at,
        backend=backend,
        turn_id=turn_id,
        summary=summary,
        payload=payload,
    )


def steer_ack(team: str, agent: str, seq: int, delivery: str):
    return append_event(
        team,
        agent,
        "steer_ack",
        seq,
        payload={"delivery": delivery, "steer_id": f"steer-{seq}"},
    )


def run_score(
    team: str,
    out: Path,
    *,
    coupling_intent: str | None = None,
) -> tuple[dict, dict, dict[str, dict]]:
    argv = [
        "--team",
        team,
        "--scenario",
        "S5",
        "--run-id",
        "20260427T1530Z",
        "--out",
        str(out),
    ]
    if coupling_intent is not None:
        argv.extend(["--coupling-intent", coupling_intent])
    rc = score_collab.main(argv)
    assert rc == 0
    scenario = json.loads((out / "scenario.json").read_text())
    pairs = json.loads((out / "pairs.json").read_text())
    agents = {
        path.stem: json.loads(path.read_text())
        for path in sorted((out / "agents").glob("*.json"))
    }
    return scenario, pairs, agents


def test_empty_events_dir(teams_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    events_dir = teams_dir / "empty-team" / "events"
    events_dir.mkdir(parents=True)

    rc = score_collab.main(
        [
            "--events-dir",
            str(events_dir),
            "--scenario",
            "S5",
            "--run-id",
            "run-empty",
            "--out",
            str(tmp_path / "out"),
        ]
    )

    assert rc == 1
    assert "no event logs found" in capsys.readouterr().err
    assert not (tmp_path / "out" / "scenario.json").exists()


def test_m3_m4_basic(teams_dir: Path, tmp_path: Path):
    team = "team-basic"
    send(team, "agent-a", 1, "agent-b", summary="ask: where is the test?")
    send(team, "agent-a", 2, "agent-c", summary="answer: it is in tests/x.py")
    send(team, "agent-a", 3, "agent-d", summary="handoff: over to you")
    send(team, "agent-a", 4, "team-lead", summary="fyi: status for lead")

    scenario, _pairs, agents = run_score(team, tmp_path / "out")
    metrics = agents["agent-a"]["metrics"]

    assert metrics["M3_peer_dm_sent"] == 3
    assert metrics["M4_cross_peer_ratio"] == 0.75
    assert metrics["M4_total_send_message_calls"] == 4
    assert metrics["M4_to_lead_count"] == 1
    assert metrics["M4_semantic_breakdown"] == {
        "ask": 1,
        "answer": 1,
        "handoff": 1,
        "fyi": 0,
        "other": 0,
    }
    assert scenario["aggregate"]["M3_total_peer_dms"] == 3


def test_recipient_preserved_verbatim(teams_dir: Path, tmp_path: Path):
    team = "team-verbatim"
    send(team, "codex-tgt-app", 1, "gemini-tgt-acp")
    send(team, "codex-tgt-app", 2, "kimi-tgt")
    send(team, "gemini-tgt-acp", 1, "codex-tgt-app")

    _scenario, pairs, _agents = run_score(team, tmp_path / "out")

    pair_names = {(row["from"], row["to"]) for row in pairs["pairs"]}
    assert ("codex-tgt-app", "gemini-tgt-acp") in pair_names
    assert ("codex-tgt-app", "kimi-tgt") in pair_names
    assert ("gemini-tgt-acp", "codex-tgt-app") in pair_names
    assert "peer" not in {name for pair in pair_names for name in pair}


def test_m9_breakdown(teams_dir: Path, tmp_path: Path):
    team = "team-m9"
    for seq, delivery in enumerate(
        ["delivered_mid_turn", "delivered_next_turn", "queued", "expired", "dropped"],
        start=1,
    ):
        steer_ack(team, "agent-a", seq, delivery)

    _scenario, _pairs, agents = run_score(team, tmp_path / "out")
    metrics = agents["agent-a"]["metrics"]

    assert metrics["M9_delivery_breakdown"] == {
        "delivered_mid_turn": 1,
        "delivered_next_turn": 1,
        "dropped": 1,
        "expired": 1,
        "queued": 1,
    }
    assert metrics["M9_steer_ack_observed"] == 2
    assert metrics["M9_steer_ack_total"] == 4
    assert metrics["M9_steer_ack_rate"] == 0.5


def test_m9_with_inflight_queued(teams_dir: Path, tmp_path: Path):
    team = "team-m9-queued"
    steer_ack(team, "agent-a", 1, "delivered_mid_turn")
    steer_ack(team, "agent-a", 2, "queued")

    _scenario, _pairs, agents = run_score(team, tmp_path / "out")
    metrics = agents["agent-a"]["metrics"]

    assert metrics["M9_steer_ack_observed"] == 1
    assert metrics["M9_steer_ack_total"] == 1
    assert metrics["M9_inflight_count"] == 1
    assert metrics["M9_steer_ack_rate"] == 1.0
    assert "steer_ack_inflight:1" in agents["agent-a"]["notes"]


def test_m11_rtt_basic(teams_dir: Path, tmp_path: Path):
    team = "team-rtt"
    send(team, "agent-a", 1, "agent-b", at=0)
    send(team, "agent-b", 1, "agent-a", at=15)

    _scenario, pairs, agents = run_score(team, tmp_path / "out")
    metrics = agents["agent-a"]["metrics"]
    rtt = metrics["M11a_peer_dm_rtt_seconds"]

    assert "M11_peer_dm_rtt_seconds" not in metrics
    assert rtt["p50"] is None
    assert rtt["p95"] is None
    assert rtt["max"] == 15.0
    assert rtt["samples"] == 1
    assert rtt["unmatched_send_count"] == 0
    assert metrics["samples_used_for_M11a"] == 1
    assert metrics["M11a_p50"] is None
    assert metrics["M11a_max"] == 15.0
    pair = next(row for row in pairs["pairs"] if row["from"] == "agent-a" and row["to"] == "agent-b")
    assert pair["rtt_seconds"]["mean"] == 15.0


def test_m11a_team_percentiles_expose_sample_count_p50_and_max(teams_dir: Path, tmp_path: Path):
    team = "team-m11a-fields"
    for seq, delta in enumerate([10, 20, 30, 40, 50], start=1):
        send(team, "agent-a", seq, "agent-b", at=seq)
        send(team, "agent-b", seq, "agent-a", at=seq + delta)

    scenario, _pairs, agents = run_score(team, tmp_path / "out")
    aggregate = scenario["aggregate"]
    agent_metrics = agents["agent-a"]["metrics"]

    assert aggregate["samples_used_for_M11a"] == 5
    assert aggregate["M11a_p50"] == aggregate["M11a_team_rtt_seconds"]["p50"]
    assert aggregate["M11a_max"] == 50.0
    assert aggregate["M11a_team_p95_rtt_seconds"] == aggregate["M11a_team_rtt_seconds"]["p95"]
    assert agent_metrics["samples_used_for_M11a"] == 5
    assert agent_metrics["M11a_max"] == 50.0
    assert agent_metrics["M11a_peer_dm_rtt_seconds"]["p95"] <= agent_metrics["M11a_max"]


def test_m11a_p95_is_clamped_to_observed_max_for_small_samples():
    triplet = score_collab.percentile_triplet([1, 2, 3, 4, 5])
    assert triplet["p95"] == 5.0
    assert triplet["max"] == 5.0


def test_m11_rtt_unmatched_at_cap(teams_dir: Path, tmp_path: Path):
    team = "team-rtt-cap"
    send(team, "agent-a", 1, "agent-b", at=0)
    send(team, "agent-b", 1, "agent-a", at=700)

    _scenario, pairs, agents = run_score(team, tmp_path / "out")
    rtt = agents["agent-a"]["metrics"]["M11a_peer_dm_rtt_seconds"]

    assert rtt["samples"] == 0
    assert rtt["p95"] is None
    assert rtt["unmatched_send_count"] == 1
    assert "p95_undersampled" in agents["agent-a"]["notes"]
    pair = next(row for row in pairs["pairs"] if row["from"] == "agent-a" and row["to"] == "agent-b")
    assert pair["rtt_seconds"]["unmatched_send_count"] == 1


def test_m11a_per_backend_distinct_distributions(teams_dir: Path, tmp_path: Path):
    team = "team-m11a-backend"
    for idx, delta in enumerate([1, 2, 3, 4, 5], start=1):
        base = idx * 100
        send(team, "agent-a", idx, "codex-b", at=base)
        send(team, "codex-b", idx, "agent-a", at=base + delta, backend="codex_app_server")
    for idx, delta in enumerate([20, 21, 22, 23, 24], start=10):
        base = idx * 100
        send(team, "agent-a", idx, "gemini-c", at=base)
        send(team, "gemini-c", idx, "agent-a", at=base + delta, backend="gemini_acp")

    _scenario, _pairs, agents = run_score(team, tmp_path / "out")
    buckets = agents["agent-a"]["metrics"]["M11a_peer_dm_rtt_seconds_by_recipient_backend"]

    assert set(buckets) == {"codex_app_server", "gemini_acp"}
    assert buckets["codex_app_server"]["samples"] == 5
    assert buckets["gemini_acp"]["samples"] == 5
    assert buckets["codex_app_server"]["max"] == 5.0
    assert buckets["gemini_acp"]["max"] == 24.0
    assert buckets["codex_app_server"]["p50"] != buckets["gemini_acp"]["p50"]


def test_m11a_semantic_buckets_and_classification_coverage(teams_dir: Path, tmp_path: Path):
    team = "team-m11a-semantic"
    send(team, "agent-a", 1, "agent-b", at=0, summary="ask: need review")
    send(team, "agent-b", 1, "agent-a", at=10, summary="answer: reviewed")
    send(team, "agent-a", 2, "agent-b", at=20, summary="fyi: continuing")
    send(team, "agent-b", 2, "agent-a", at=30, summary="answer: noted")
    send(team, "agent-a", 3, "agent-b", at=40, summary="please review without prefix")
    send(team, "agent-b", 3, "agent-a", at=50, summary="answer: done")

    scenario, _pairs, agents = run_score(team, tmp_path / "out")
    metrics = agents["agent-a"]["metrics"]
    by_semantic = metrics["M11a_peer_dm_rtt_seconds_by_semantic"]

    assert by_semantic["classifier_method"] == "prefix_v1"
    assert by_semantic["ask"]["samples"] == 1
    assert by_semantic["ask"]["max"] == 10.0
    assert by_semantic["fyi"]["samples"] == 1
    assert by_semantic["other"]["samples"] == 1
    assert metrics["M11a_classification_coverage"] == 0.667
    assert scenario["aggregate"]["M11a_classification_coverage"] == 0.833
    assert "m11a_unclassified_semantic:1" in agents["agent-a"]["notes"]


def test_m11a_loose_coupling_compliance_uses_structured_reason_and_alias(
    teams_dir: Path,
    tmp_path: Path,
):
    team = "team-m11a-coupling"
    send(team, "agent-a", 1, "agent-b", at=0, turn_id="turn-1", summary="handoff: please take over")
    send(team, "agent-b", 1, "agent-a", at=10, turn_id="turn-2", summary="answer: ok")
    send(team, "agent-a", 2, "agent-b", at=20, turn_id="turn-1", summary="fyi: local shard done")

    scenario, _pairs, agents = run_score(team, tmp_path / "out", coupling_intent="loose")
    compliance = agents["agent-a"]["metrics"]["M11a_coupling_compliance"]
    team_compliance = scenario["aggregate"]["M11a_coupling_compliance"]

    assert compliance["declared_intent"] == "loose_parallel"
    assert compliance["blocking_candidate_peer_dms"] == 1
    assert compliance["nonblocking_peer_dms"] == 1
    assert compliance["violations"] == [
        {
            "event_id": "agent-a:turn-1:1",
            "reason": "blocking_candidate_under_loose_parallel",
            "recipient": "agent-b",
            "semantic": "handoff",
            "sender": "agent-a",
            "turn_id": "turn-1",
        }
    ]
    assert team_compliance["violations"][0]["reason"] == "blocking_candidate_under_loose_parallel"


def test_m11a_undersampled_backend_emits_null_percentiles(teams_dir: Path, tmp_path: Path):
    team = "team-m11a-under"
    send(team, "agent-a", 1, "kimi-b", at=0)
    send(team, "kimi-b", 1, "agent-a", at=42, backend="kimi_headless")

    _scenario, _pairs, agents = run_score(team, tmp_path / "out")
    bucket = agents["agent-a"]["metrics"]["M11a_peer_dm_rtt_seconds_by_recipient_backend"]["kimi_headless"]

    assert bucket["samples"] == 1
    assert bucket["p50"] is None
    assert bucket["p95"] is None
    assert bucket["max"] == 42.0
    assert "m11a_undersampled:kimi_headless" in agents["agent-a"]["notes"]


def test_m11_self_dm_excluded(
    teams_dir: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    team = "team-self-dm"
    send(team, "agent-a", 1, "agent-a")

    _scenario, pairs, agents = run_score(team, tmp_path / "out")

    assert agents["agent-a"]["metrics"]["M3_peer_dm_sent"] == 0
    assert agents["agent-a"]["metrics"]["M4_cross_peer_ratio"] == 0.0
    assert pairs["pairs"] == []
    assert "self-DM excluded" in capsys.readouterr().err


def test_m13_collision_detected(teams_dir: Path, tmp_path: Path):
    team = "team-m13"
    append_event(
        team,
        "agent-a",
        "turn_completed",
        1,
        payload={
            "tool_call_events": 2,
            "last_message_preview": "this is a 50-char canned fallback prose reply, oops",
        },
    )

    scenario, _pairs, agents = run_score(team, tmp_path / "out")

    assert agents["agent-a"]["metrics"]["M13_prose_fallback_collisions"] == 1
    assert agents["agent-a"]["metrics"]["M13_total_send_message_replies"] == 1
    assert scenario["aggregate"]["M13_total_collisions"] == 1


def test_m13_no_collision_when_preview_short(teams_dir: Path, tmp_path: Path):
    team = "team-m13-short"
    append_event(
        team,
        "agent-a",
        "turn_failed",
        1,
        payload={"tool_call_events": 2, "last_message_preview": "ok"},
    )

    _scenario, _pairs, agents = run_score(team, tmp_path / "out")

    assert agents["agent-a"]["metrics"]["M13_prose_fallback_collisions"] == 0
    assert agents["agent-a"]["metrics"]["M13_total_send_message_replies"] == 1


def test_m13_structured_reply_not_collision(teams_dir: Path, tmp_path: Path):
    team = "team-m13-structured"
    append_event(
        team,
        "agent-a",
        "turn_completed",
        1,
        payload={
            "tool_call_events": 2,
            "last_message_preview": "structured task_complete payload with enough text to look prose-like",
            "structured": True,
        },
    )

    scenario, _pairs, agents = run_score(team, tmp_path / "out")

    assert agents["agent-a"]["metrics"]["M13_prose_fallback_collisions"] == 0
    assert agents["agent-a"]["metrics"]["M13_total_send_message_replies"] == 1
    assert scenario["aggregate"]["M13_total_collisions"] == 0


def test_pair_table_built(teams_dir: Path, tmp_path: Path):
    team = "team-pairs"
    send(team, "agent-a", 1, "agent-b", at=0)
    send(team, "agent-a", 2, "agent-c", at=1)
    send(team, "agent-b", 1, "agent-a", at=12)
    send(team, "agent-c", 1, "agent-b", at=20)

    _scenario, pairs, _agents = run_score(team, tmp_path / "out")

    rows = {(row["from"], row["to"]): row for row in pairs["pairs"]}
    assert set(rows) == {
        ("agent-a", "agent-b"),
        ("agent-a", "agent-c"),
        ("agent-b", "agent-a"),
        ("agent-c", "agent-b"),
    }
    assert rows[("agent-a", "agent-b")]["messages_sent"] == 1
    assert rows[("agent-a", "agent-b")]["rtt_seconds"]["samples"] == 1
    assert rows[("agent-a", "agent-c")]["rtt_seconds"]["samples"] == 0
    assert rows[("agent-a", "agent-c")]["prose_fallback_collisions"] == 0


def test_visibility_degraded_peer_steer_recorded(teams_dir: Path, tmp_path: Path):
    team = "team-peer-steer"
    append_event(
        team,
        "agent-a",
        "visibility_degraded",
        1,
        payload={"surface": "peer_steer_rejected", "sender": "agent-b", "recipient": "agent-a"},
    )

    scenario, _pairs, agents = run_score(team, tmp_path / "out")

    assert "peer_steer_rejected:1" in agents["agent-a"]["notes"]
    assert agents["agent-a"]["metrics"]["M4_attribution"]["peer_steer_rejection_observed"] == 1
    assert scenario["aggregate"]["peer_steer_rejected_count"] == 1


def test_m4_attribution_counts(teams_dir: Path, tmp_path: Path):
    team = "team-attribution"
    append_event(
        team,
        "agent-a",
        "tool_event",
        1,
        turn_id="turn-1",
        payload={"tool_name": "mcp_anyteam_capability_manifest", "phase": "completed"},
    )
    append_event(
        team,
        "agent-a",
        "tool_event",
        2,
        turn_id="turn-1",
        payload={"tool_name": "read_inbox", "phase": "completed"},
    )
    send(team, "agent-a", 3, "agent-b", turn_id="turn-1")
    append_event(
        team,
        "agent-a",
        "tool_event",
        4,
        turn_id="turn-2",
        payload={"tool_name": "read_inbox", "phase": "completed"},
    )

    _scenario, _pairs, agents = run_score(team, tmp_path / "out")
    attribution = agents["agent-a"]["metrics"]["M4_attribution"]

    assert attribution == {
        "manifest_consulted_count": 1,
        "inbox_polled_without_peer_send": 1,
        "peer_steer_rejection_observed": 0,
    }
