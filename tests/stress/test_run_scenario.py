from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from claude_teams import messaging as cs_messaging
from claude_teams import tasks as cs_tasks
from claude_teams import teams as cs_teams
from tools.stress import run_scenario


@pytest.fixture
def isolated_protocol_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    teams_root = tmp_path / "home" / ".claude" / "teams"
    tasks_root = tmp_path / "home" / ".claude" / "tasks"
    monkeypatch.setattr(cs_teams, "TEAMS_DIR", teams_root)
    monkeypatch.setattr(cs_teams, "TASKS_DIR", tasks_root)
    monkeypatch.setattr(cs_tasks, "TASKS_DIR", tasks_root)
    monkeypatch.setattr(cs_messaging, "TEAMS_DIR", teams_root)
    return teams_root, tasks_root


def _fake_scorer_modules(*, fail: str | None = None):
    def throughput_score(*, events_dir: Path, scenario: str, run_id: str, out: Path):
        if fail == "throughput":
            raise RuntimeError("boom-throughput")
        doc = {
            "schema_version": 1,
            "scenario": scenario,
            "run_id": run_id,
            "aggregate": {
                "M1_throughput_per_min": {"sum": 1.5, "mean": 0.5, "min": 0.4, "max": 0.6},
                "M5_turn_failed_rate": {"weighted_mean": 0.1},
                "M11b_team_p95_turn_duration_seconds": 84.0,
            },
            "per_agent_files": ["agents/codex-tgt-app.json"],
        }
        (out / "agents").mkdir(parents=True, exist_ok=True)
        (out / "agents" / "codex-tgt-app.json").write_text(json.dumps({"agent": "codex-tgt-app"}) + "\n")
        (out / "scenario.json").write_text(json.dumps(doc) + "\n")
        return doc

    def collab_score(*, events_dir: Path, scenario: str, run_id: str, out: Path):
        if fail == "collab":
            raise RuntimeError("boom-collab")
        doc = {
            "schema_version": 1,
            "scenario": scenario,
            "run_id": run_id,
            "aggregate": {
                "M4_team_cross_peer_ratio": 0.75,
                "M9_team_steer_ack_rate": 0.9,
                "M11a_team_p95_rtt_seconds": 42.0,
                "samples_used_for_M11a": 7,
                "M11a_p50": 12.0,
                "M11a_max": 99.0,
                "M13_total_collisions": 0,
            },
            "per_agent_files": ["agents/codex-tgt-app.json"],
            "pair_file": "pairs.json",
        }
        agent_doc = {
            "agent": "codex-tgt-app",
            "metrics": {
                "M11a_peer_dm_rtt_seconds_by_recipient_backend": {
                    "gemini_acp": {"p50": None, "p95": None, "max": 12.0, "samples": 1},
                    "kimi_headless": {"p50": None, "p95": None, "max": None, "samples": 0},
                }
            },
        }
        (out / "agents").mkdir(parents=True, exist_ok=True)
        (out / "agents" / "codex-tgt-app.json").write_text(json.dumps(agent_doc) + "\n")
        (out / "pairs.json").write_text(json.dumps({"pairs": []}) + "\n")
        (out / "scenario.json").write_text(json.dumps(doc) + "\n")
        return doc

    def quality_score(*, events_dir: Path, sandbox: Path, workload_manifest: Path, scenario: str, run_id: str, out: Path):
        if fail == "quality":
            raise RuntimeError("boom-quality")
        doc = {
            "schema_version": 1,
            "scenario": scenario,
            "run_id": run_id,
            "task_verdicts": {"pass": 1, "fail": 0, "deferred": 0, "manual_pending": 0, "unknown_check": 0},
            "task_pass_rate": 1.0,
            "M6_team_total_visibility_degraded": 0,
            "M7_team_total_capability_manifest_calls": 1,
            "M12_team_average_coverage_ratio": 0.8,
            "M12_backends_below_threshold": [],
            "s1_flatten_violations": [],
            "per_task_files": ["tasks/1.json"],
            "per_agent_files": ["agents/codex-tgt-app.json"],
        }
        (out / "tasks").mkdir(parents=True, exist_ok=True)
        (out / "agents").mkdir(parents=True, exist_ok=True)
        (out / "tasks" / "1.json").write_text(json.dumps({"task_id": "1", "verdict": "pass"}) + "\n")
        (out / "agents" / "codex-tgt-app.json").write_text(json.dumps({"agent": "codex-tgt-app", "s1_flatten_violations": []}) + "\n")
        (out / "scenario.json").write_text(json.dumps(doc) + "\n")
        return doc

    return (
        SimpleNamespace(score=Mock(side_effect=throughput_score)),
        SimpleNamespace(score=Mock(side_effect=collab_score)),
        SimpleNamespace(score=Mock(side_effect=quality_score)),
    )


@pytest.fixture
def fake_scorers(monkeypatch: pytest.MonkeyPatch):
    modules = _fake_scorer_modules()
    monkeypatch.setattr(run_scenario, "_load_scorers", lambda: modules)
    return modules


def _workload(name: str = "W1.json") -> Path:
    return Path("tools/stress/workloads") / name


def _invoke(tmp_path: Path, scenario: str, workload: str = "W1.json", run_id: str = "20260427T1530Z", extra: list[str] | None = None) -> tuple[int, Path, Path]:
    out = tmp_path / "runs" / f"{scenario}-{run_id}"
    sandbox = tmp_path / "sandbox"
    argv = [
        "--scenario",
        scenario,
        "--run-id",
        run_id,
        "--workload-manifest",
        str(_workload(workload)),
        "--sandbox",
        str(sandbox),
        "--out",
        str(out),
        "--dry-run",
    ]
    if extra:
        argv.extend(extra)
    return run_scenario.main(argv), out, sandbox


def _scorecard(out: Path) -> dict:
    return json.loads((out / "scorecard.json").read_text())


def test_dry_run_S1(isolated_protocol_roots, fake_scorers, tmp_path: Path) -> None:
    rc, out, _ = _invoke(tmp_path, "S1")

    assert rc == 0
    card = _scorecard(out)
    assert card["scorecards"] == {
        "throughput": "throughput/scenario.json",
        "collab": "collab/scenario.json",
        "quality": "quality/scenario.json",
    }
    assert (out / "events").exists()


def test_scorecard_records_run_time_and_scoring_time_git_shas(
    isolated_protocol_roots,
    fake_scorers,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    git_sha = Mock(side_effect=["run-start-sha", "score-time-sha"])
    monkeypatch.setattr(run_scenario, "_git_sha", git_sha)

    rc, out, _ = _invoke(tmp_path, "S1")

    assert rc == 0
    card = _scorecard(out)
    assert card["run_time_git_sha"] == "run-start-sha"
    assert card["scoring_time_git_sha"] == "score-time-sha"
    assert card["git_sha"] == "run-start-sha"


def test_dry_run_S5_mixed(isolated_protocol_roots, fake_scorers, tmp_path: Path) -> None:
    rc, out, _ = _invoke(tmp_path, "S5", workload="W9.json")

    assert rc == 0
    agent_doc = json.loads((out / "collab" / "agents" / "codex-tgt-app.json").read_text())
    by_backend = agent_doc["metrics"]["M11a_peer_dm_rtt_seconds_by_recipient_backend"]
    assert "gemini_acp" in by_backend
    headline = _scorecard(out)["headline_metrics"]
    assert headline["M11a_team_p95_rtt_seconds"] == 42.0
    assert headline["samples_used_for_M11a"] == 7
    assert headline["M11a_p50"] == 12.0
    assert headline["M11a_max"] == 99.0
    assert headline["M11b_team_p95_turn_duration_seconds"] == 84.0


def test_dry_run_S10a_ablation(isolated_protocol_roots, fake_scorers, tmp_path: Path) -> None:
    rc, out, _ = _invoke(tmp_path, "S10a")

    assert rc == 0
    card = _scorecard(out)
    assert card["env_overrides"] == {"CLAUDE_ANYTEAM_DISABLE_PEER_PROMPT_FRAGMENTS": "1"}
    assert card["ablation_against"] == "S5"


def test_dry_run_S10b_ablation(isolated_protocol_roots, fake_scorers, tmp_path: Path) -> None:
    rc, out, _ = _invoke(tmp_path, "S10b")

    assert rc == 0
    assert _scorecard(out)["env_overrides"] == {
        "CLAUDE_ANYTEAM_DISABLE_PEER_PROMPT_FRAGMENTS": "1",
        "CLAUDE_ANYTEAM_DISABLE_MANIFEST_CACHE": "1",
    }


def test_dry_run_S10c_ablation(isolated_protocol_roots, fake_scorers, tmp_path: Path) -> None:
    rc, out, _ = _invoke(tmp_path, "S10c")

    assert rc == 0
    card = _scorecard(out)
    assert card["env_overrides"] == {
        "CLAUDE_ANYTEAM_DISABLE_PEER_STEER_MANIFEST_CHECK": "1"
    }
    assert card["ablation_against"] == "S6"


def test_unknown_scenario_id(isolated_protocol_roots, fake_scorers, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc, _, _ = _invoke(tmp_path, "S404")

    assert rc == 1
    assert "unknown scenario" in capsys.readouterr().err


def test_workload_manifest_missing(isolated_protocol_roots, fake_scorers, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "runs" / "missing"
    sandbox = tmp_path / "sandbox"
    rc = run_scenario.main(
        [
            "--scenario",
            "S1",
            "--run-id",
            "MISSING",
            "--workload-manifest",
            str(tmp_path / "missing.json"),
            "--sandbox",
            str(sandbox),
            "--out",
            str(out),
            "--dry-run",
        ]
    )

    assert rc == 1
    assert "workload manifest missing" in capsys.readouterr().err


def test_command_for_member_constructs_claude_invocation(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    cmd = run_scenario._command_for_member(
        {"name": "claude-tgt-a", "agent_type": "claude", "model": "sonnet"},
        "stress-S2-20260427T1530Z",
        sandbox,
    )

    assert cmd is not None
    assert cmd[:3] == ["claude", "--print", "--append-system-prompt"]
    assert "--model" in cmd
    assert cmd[cmd.index("--model") + 1] == "sonnet"
    assert "claude_anyteam" not in cmd
    assert "claude-tgt-a" in cmd[3]
    assert "stress-S2-20260427T1530Z" in cmd[3]
    assert str(sandbox / "repo") in cmd[3]


def test_command_for_member_passes_explicit_gemini_binary(monkeypatch, tmp_path: Path) -> None:
    """§3 spawn-correctness: gemini members must pass --gemini-binary with the
    absolute path resolved at construction time, NOT default to PATH-resolved
    `gemini` (which under stress-spawn context can shadow with a sibling
    `gemini-anyteam` shim that doesn't accept --version, killing S3 at the
    feature_test step). Filed as task #42 from the S3 launch failure."""
    fake_gemini = tmp_path / "fake_gemini"
    fake_gemini.write_text("#!/bin/sh\necho gemini 2.5\n", encoding="utf-8")
    fake_gemini.chmod(0o755)
    monkeypatch.setattr(run_scenario.shutil, "which", lambda name: str(fake_gemini) if name == "gemini" else None)

    cmd = run_scenario._command_for_member(
        {"name": "gemini-tgt-a", "agent_type": "gemini", "model": "gemini-2.5-pro", "transport": "acp"},
        "stress-S3-test",
        tmp_path,
    )

    assert cmd is not None
    assert "--gemini-binary" in cmd, "gemini spawn must pass --gemini-binary explicitly"
    assert cmd[cmd.index("--gemini-binary") + 1] == str(fake_gemini), \
        "gemini-binary must be an absolute path, not a name to PATH-resolve"
    assert "--model" in cmd
    assert cmd[cmd.index("--model") + 1] == "gemini-2.5-pro"


def test_command_for_member_passes_explicit_kimi_binary(monkeypatch, tmp_path: Path) -> None:
    """Same §3 spawn-correctness as gemini: kimi members must pass --kimi-binary
    explicitly. Defensive: kimi-cli could resolve to a sibling shim under PATH
    pressure too. Same fix shape per task #42."""
    fake_kimi = tmp_path / "fake_kimi"
    fake_kimi.write_text("#!/bin/sh\necho kimi-cli 0.1\n", encoding="utf-8")
    fake_kimi.chmod(0o755)
    monkeypatch.setattr(run_scenario.shutil, "which", lambda name: str(fake_kimi) if name == "kimi" else None)

    cmd = run_scenario._command_for_member(
        {"name": "kimi-tgt-a", "agent_type": "kimi", "model": "kimi-k2"},
        "stress-S4-test",
        tmp_path,
    )

    assert cmd is not None
    assert "--kimi-binary" in cmd, "kimi spawn must pass --kimi-binary explicitly"
    assert cmd[cmd.index("--kimi-binary") + 1] == str(fake_kimi)
    assert "--backend" in cmd  # default headless transport


def test_kimi_scenario_members_dont_specify_unknown_model(tmp_path: Path) -> None:
    """SCENARIOS spec for kimi members must NOT pass `model` to the kimi adapter.

    Background: S4 (homogeneous-kimi, 20260427T0929Z) failed when every kimi
    turn exited with `"LLM not set"` to stdout. Root cause: the SCENARIOS dict
    specified `model: "kimi-k2"` for every kimi member, which the adapter
    forwarded as `--model kimi-k2` to the kimi CLI; the kimi CLI's config
    (~/.kimi/config.toml) defines models like `kimi-code/kimi-for-coding` and
    rejected `kimi-k2` as unknown. Each turn died in 5.979s with structured=False.

    Fix: drop the `model` field from kimi scenario specs; let kimi use its
    config.toml `default_model`. Stress runs measure the harness as it ships,
    not a model the user doesn't have configured.

    This test asserts the SCENARIOS dict invariant so future edits don't
    re-introduce a model spec the kimi CLI doesn't recognize.
    """
    for scenario_id, scenario in run_scenario.SCENARIOS.items():
        for member in scenario.get("members", []):
            if member.get("agent_type") == "kimi":
                assert "model" not in member, (
                    f"SCENARIOS[{scenario_id!r}] kimi member {member['name']!r} "
                    f"specifies model={member.get('model')!r}; remove `model` "
                    f"so kimi CLI uses its config.toml default. See task #46 "
                    f"(S4 'LLM not set' root cause, 2026-04-27)."
                )


def test_command_for_member_raises_when_backend_binary_missing(monkeypatch, tmp_path: Path) -> None:
    """Fail loud if the gemini/kimi binary isn't on PATH — better than silently
    spawning a broken adapter that wastes 1800s on a feature_test timeout."""
    monkeypatch.setattr(run_scenario.shutil, "which", lambda name: None)

    with pytest.raises(FileNotFoundError) as exc_info:
        run_scenario._command_for_member(
            {"name": "gemini-tgt-a", "agent_type": "gemini"},
            "stress-test",
            tmp_path,
        )
    assert "gemini" in str(exc_info.value)
    assert "not found on PATH" in str(exc_info.value)


def test_archive_paths(isolated_protocol_roots, fake_scorers, tmp_path: Path) -> None:
    rc, out, _ = _invoke(tmp_path, "S1")

    assert rc == 0
    assert (out / "events").is_dir()
    assert (out / "tasks").is_dir()
    assert (out / "team-config.json").is_file()
    assert (out / "workload-manifest.json").is_file()
    assert (out / "throughput" / "scenario.json").is_file()
    assert (out / "collab" / "scenario.json").is_file()
    assert (out / "quality" / "scenario.json").is_file()


def test_unified_scorecard_headline_metrics_present(isolated_protocol_roots, fake_scorers, tmp_path: Path) -> None:
    rc, out, _ = _invoke(tmp_path, "S1")

    assert rc == 0
    card = _scorecard(out)
    for key in run_scenario.HEADLINE_KEYS:
        assert key in card["headline_metrics"]
    assert "s1_flatten_violations" in card
    assert "north_star_signals" in card


def test_scorer_failure_caught(isolated_protocol_roots, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    modules = _fake_scorer_modules(fail="collab")
    monkeypatch.setattr(run_scenario, "_load_scorers", lambda: modules)

    rc, out, _ = _invoke(tmp_path, "S1")

    assert rc == 3
    card = _scorecard(out)
    assert any(note.startswith("scorer_failure:") for note in card["notes"])


def test_team_already_exists(isolated_protocol_roots, fake_scorers, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cs_teams.create_team("stress-S1-EXISTS", session_id="preexisting")

    rc, _, _ = _invoke(tmp_path, "S1", run_id="EXISTS")

    assert rc == 2
    assert "team already exists" in capsys.readouterr().err


def test_workload_manifest_w8_chained_delegation() -> None:
    manifest = json.loads(_workload("W8.json").read_text())

    assert manifest["workload_id"] == "W8"
    assert manifest["success_check"]["type"] == "files_modified"
    assert manifest["success_check"]["args"]["min_distinct_authors"] == 2
    assert manifest["success_check"]["args"]["handoff_required"] is True


def test_workload_manifest_w9_capability_aware() -> None:
    manifest = json.loads(_workload("W9.json").read_text())

    assert manifest["workload_id"] == "W9"
    assert manifest["metric_check"]["metric"] == "M7"
    assert "M7" in json.dumps(manifest)


def test_workload_manifest_w10_cross_backend() -> None:
    manifest = json.loads(_workload("W10.json").read_text())

    assert manifest["workload_id"] == "W10"
    assert "RENDEZVOUS_AGREEMENT.txt" in manifest["success_check"]["args"]["must_include"]
    assert manifest["success_check"]["args"]["agreement_marker_required"] is True
