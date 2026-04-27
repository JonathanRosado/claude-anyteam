#!/usr/bin/env python3
"""Run one Phase-3 stress scenario and emit the unified scorecard.

The driver is deliberately linear-functional rather than object-oriented: create
sandbox, create the temporary team, spawn or dry-run, archive, call the three
Phase-3 scorer modules in-process, then write ``scorecard.json``.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, TextIO

from claude_anyteam import protocol_io as pio
from claude_anyteam.messages import VisibilityEvent
from claude_teams import messaging as cs_messaging
from claude_teams import tasks as cs_tasks
from claude_teams import teams as cs_teams
from claude_teams.models import COLOR_PALETTE, TeammateMember

DEFAULT_TIMEOUT_SECONDS = 1800
DEFAULT_POLL_SECONDS = 5.0
RUNS_ROOT = Path("references/external-claude-code-re/proto-rev-execution-log/runs")
TEAM_LEAD = "team-lead"
STRESS_SANDBOX_ROOT = Path("/tmp")
STRESS_SANDBOX_PREFIX = "stress-sandbox-"
STRESS_SANDBOX_MARKER = ".stress_sandbox_marker"
ABLATION_ENV_KEYS = (
    "CLAUDE_ANYTEAM_DISABLE_PEER_PROMPT_FRAGMENTS",
    "CLAUDE_ANYTEAM_DISABLE_MANIFEST_CACHE",
    "CLAUDE_ANYTEAM_DISABLE_PEER_STEER_MANIFEST_CHECK",
)

SCENARIOS: dict[str, dict[str, Any]] = {
    "S1": {
        "name": "homogeneous-codex",
        "team_size": 3,
        "members": [
            {"name": "codex-tgt-app", "agent_type": "codex", "model": "gpt-5.5", "effort": "xhigh", "transport": "app_server"},
            {"name": "codex-tgt-exec", "agent_type": "codex", "model": "gpt-5.5", "effort": "xhigh", "transport": "exec"},
            {"name": "codex-tgt-c", "agent_type": "codex", "model": "gpt-5.5", "effort": "xhigh", "transport": "app_server"},
        ],
        "n_tasks": 30,
        "env": {},
    },
    "S2": {
        "name": "homogeneous-claude",
        "team_size": 3,
        "members": [
            {"name": "claude-tgt-a", "agent_type": "claude", "model": "sonnet"},
            {"name": "claude-tgt-b", "agent_type": "claude", "model": "sonnet"},
            {"name": "claude-tgt-c", "agent_type": "claude", "model": "sonnet"},
        ],
        "n_tasks": 30,
        "env": {},
    },
    "S3": {
        "name": "homogeneous-gemini",
        "team_size": 3,
        "members": [
            {"name": "gemini-tgt-a", "agent_type": "gemini", "model": "gemini-2.5-pro", "effort": "high", "transport": "acp"},
            {"name": "gemini-tgt-b", "agent_type": "gemini", "model": "gemini-2.5-pro", "effort": "high", "transport": "acp"},
            {"name": "gemini-tgt-c", "agent_type": "gemini", "model": "gemini-2.5-pro", "effort": "high", "transport": "headless"},
        ],
        "n_tasks": 20,
        "env": {},
    },
    "S4": {
        "name": "homogeneous-kimi",
        "team_size": 3,
        "members": [
            {"name": "kimi-tgt-a", "agent_type": "kimi"},
            {"name": "kimi-tgt-b", "agent_type": "kimi"},
            {"name": "kimi-tgt-c", "agent_type": "kimi"},
        ],
        "n_tasks": 20,
        "env": {},
    },
    "S5": {
        "name": "heterogeneous-mixed",
        "team_size": 4,
        "members": [
            {"name": "codex-tgt-app", "agent_type": "codex", "model": "gpt-5.5", "effort": "xhigh", "transport": "app_server"},
            {"name": "gemini-tgt-acp", "agent_type": "gemini", "model": "gemini-2.5-pro", "effort": "high", "transport": "acp"},
            {"name": "kimi-tgt", "agent_type": "kimi"},
            {"name": "claude-tgt", "agent_type": "claude", "model": "sonnet"},
        ],
        "n_tasks": 30,
        "env": {},
    },
    "S6": {
        "name": "paired-codex-codex",
        "team_size": 2,
        "members": [
            {"name": "codex-pair-a", "agent_type": "codex", "model": "gpt-5.5", "effort": "xhigh", "transport": "app_server"},
            {"name": "codex-pair-b", "agent_type": "codex", "model": "gpt-5.5", "effort": "xhigh", "transport": "app_server"},
        ],
        "n_tasks": 15,
        "env": {},
    },
    "S7": {
        "name": "paired-gemini-codex",
        "team_size": 2,
        "members": [
            {"name": "gemini-pair", "agent_type": "gemini", "model": "gemini-2.5-pro", "effort": "high", "transport": "acp"},
            {"name": "codex-pair", "agent_type": "codex", "model": "gpt-5.5", "effort": "xhigh", "transport": "app_server"},
        ],
        "n_tasks": 15,
        "env": {},
    },
    "S8": {
        "name": "paired-kimi-codex",
        "team_size": 2,
        "members": [
            {"name": "kimi-pair", "agent_type": "kimi"},
            {"name": "codex-pair", "agent_type": "codex", "model": "gpt-5.5", "effort": "xhigh", "transport": "app_server"},
        ],
        "n_tasks": 15,
        "env": {},
    },
    "S9": {
        "name": "mini-mixed",
        "team_size": 3,
        "members": [
            {"name": "claude-tgt", "agent_type": "claude", "model": "sonnet"},
            {"name": "codex-tgt", "agent_type": "codex", "model": "gpt-5.5", "effort": "xhigh", "transport": "app_server"},
            {"name": "gemini-tgt", "agent_type": "gemini", "model": "gemini-2.5-pro", "effort": "high", "transport": "acp"},
        ],
        "n_tasks": 20,
        "env": {},
    },
    "S10a": {
        "name": "ablation-fragments-stripped-manifest-preloaded",
        "description": "Same composition as S5; R14 peer-prompt-fragment injection disabled; R12 manifest cache still loads.",
        "team_size": 4,
        "members": [
            {"name": "codex-tgt-app", "agent_type": "codex", "model": "gpt-5.5", "effort": "xhigh", "transport": "app_server"},
            {"name": "gemini-tgt-acp", "agent_type": "gemini", "model": "gemini-2.5-pro", "effort": "high", "transport": "acp"},
            {"name": "kimi-tgt", "agent_type": "kimi"},
            {"name": "claude-tgt", "agent_type": "claude", "model": "sonnet"},
        ],
        "n_tasks": 30,
        "env": {"CLAUDE_ANYTEAM_DISABLE_PEER_PROMPT_FRAGMENTS": "1"},
        "ablation_against": "S5",
    },
    "S10b": {
        "name": "ablation-manifest-and-fragments-stripped",
        "description": "Same composition as S5; R12 manifest cache disabled and R14 peer fragments disabled.",
        "team_size": 4,
        "members": [
            {"name": "codex-tgt-app", "agent_type": "codex", "model": "gpt-5.5", "effort": "xhigh", "transport": "app_server"},
            {"name": "gemini-tgt-acp", "agent_type": "gemini", "model": "gemini-2.5-pro", "effort": "high", "transport": "acp"},
            {"name": "kimi-tgt", "agent_type": "kimi"},
            {"name": "claude-tgt", "agent_type": "claude", "model": "sonnet"},
        ],
        "n_tasks": 30,
        "env": {
            "CLAUDE_ANYTEAM_DISABLE_PEER_PROMPT_FRAGMENTS": "1",
            "CLAUDE_ANYTEAM_DISABLE_MANIFEST_CACHE": "1",
        },
        "ablation_against": "S5",
    },
    "S10c": {
        "name": "ablation-peer-steer-manifest-check-disabled",
        "description": "Same composition as S6; L3 wrapper-side peer-steer manifest precondition disabled.",
        "team_size": 2,
        "members": [
            {"name": "codex-pair-a", "agent_type": "codex", "model": "gpt-5.5", "effort": "xhigh", "transport": "app_server"},
            {"name": "codex-pair-b", "agent_type": "codex", "model": "gpt-5.5", "effort": "xhigh", "transport": "app_server"},
        ],
        "n_tasks": 15,
        "env": {"CLAUDE_ANYTEAM_DISABLE_PEER_STEER_MANIFEST_CHECK": "1"},
        "ablation_against": "S6",
    },
}

BACKEND_TYPE_BY_MEMBER = {
    ("codex", "app_server"): "codex_app_server",
    ("codex", "exec"): "codex_exec",
    ("gemini", "acp"): "gemini_acp",
    ("gemini", "headless"): "gemini_headless",
    ("kimi", None): "kimi_headless",
    ("claude", None): "claude_native",
}

HEADLINE_KEYS = (
    "M1_team_throughput_per_min",
    "M4_team_cross_peer_ratio",
    "M5_team_failure_rate",
    "M9_team_steer_ack_rate",
    "M11a_team_p95_rtt_seconds",
    "samples_used_for_M11a",
    "M11a_p50",
    "M11a_max",
    "M11b_team_p95_turn_duration_seconds",
    "M12_team_average_coverage_ratio",
    "M13_total_collisions",
)


class ScenarioInputError(RuntimeError):
    """Input error surfaced as CLI exit 1."""


class TeamExistsError(RuntimeError):
    """Team collision surfaced as CLI exit 2."""


class ScorerFailure(RuntimeError):
    """Scorer failure surfaced as CLI exit 3."""


def utc_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _json_dump(path: Path, data: Mapping[str, Any] | list[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _json_load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _deep_replace(value: Any, replacements: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        out = value
        for key, replacement in replacements.items():
            out = out.replace("{" + key + "}", replacement)
        return out
    if isinstance(value, list):
        return [_deep_replace(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: _deep_replace(item, replacements) for key, item in value.items()}
    return value


def _safe_format(template: str, values: Mapping[str, str]) -> str:
    class _SafeDict(dict):
        def __missing__(self, key: str) -> str:
            return "{" + key + "}"

    return template.format_map(_SafeDict(values))


def member_backend_type(member: Mapping[str, Any]) -> str:
    agent_type = str(member.get("agent_type"))
    transport = member.get("transport")
    key = (agent_type, str(transport) if transport is not None else None)
    if key in BACKEND_TYPE_BY_MEMBER:
        return BACKEND_TYPE_BY_MEMBER[key]
    if agent_type == "kimi":
        return "kimi_headless"
    if agent_type == "claude":
        return "claude_native"
    return f"{agent_type}_{transport or 'default'}"


def scenario_environment(scenario: Mapping[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    env_overrides = {key: str(value) for key, value in scenario.get("env", {}).items()}
    spawn_env = os.environ.copy()
    spawn_env.setdefault("CLAUDECODE", "1")
    spawn_env.setdefault("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS", "1")
    for key in ABLATION_ENV_KEYS:
        if key in env_overrides:
            spawn_env[key] = env_overrides[key]
    for key, value in env_overrides.items():
        spawn_env[key] = value
    src_dir = str(Path(__file__).resolve().parents[2] / "src")
    spawn_env["PYTHONPATH"] = src_dir + (os.pathsep + spawn_env["PYTHONPATH"] if spawn_env.get("PYTHONPATH") else "")
    return spawn_env, env_overrides


def cleanup_stress_sandboxes(
    current_sandbox: Path,
    *,
    root: Path | None = None,
) -> list[Path]:
    """Remove stale marked stress sandboxes before a new run starts.

    D1 finding (2026-04-27): Codex pair turns spent budget exploring leftover
    ``/tmp/stress-sandbox-*`` directories from prior runs. Cleanup is therefore
    default-on, but deletion is marker-gated: a directory must match the stress
    prefix and contain ``.stress_sandbox_marker``. The current ``--sandbox`` is
    never removed, even if it already has a marker.
    """

    root = (root or STRESS_SANDBOX_ROOT).expanduser()
    if not root.exists():
        return []

    current_resolved = current_sandbox.expanduser().resolve(strict=False)
    removed: list[Path] = []
    for candidate in sorted(root.glob(f"{STRESS_SANDBOX_PREFIX}*")):
        try:
            if candidate.resolve(strict=False) == current_resolved:
                continue
            # Never follow symlinks for cleanup. We only own real directories
            # that the harness itself marked as stress sandboxes.
            if candidate.is_symlink() or not candidate.is_dir():
                continue
            marker = candidate / STRESS_SANDBOX_MARKER
            if not marker.is_file():
                continue
            shutil.rmtree(candidate)
            removed.append(candidate)
        except FileNotFoundError:
            continue
    return removed


def write_sandbox_marker(
    sandbox: Path,
    *,
    scenario_id: str,
    run_id: str,
) -> Path:
    """Mark ``sandbox`` as harness-owned so future cleanup can identify it."""

    sandbox.mkdir(parents=True, exist_ok=True)
    marker = sandbox / STRESS_SANDBOX_MARKER
    marker.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "claude-anyteam-stress-sandbox",
                "scenario": scenario_id,
                "run_id": run_id,
                "sandbox": str(sandbox),
                "created_at": datetime.now(timezone.utc)
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z"),
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return marker


def init_sandbox_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    if not (repo / "src").exists():
        (repo / "src").mkdir()
    if not (repo / "tests").exists():
        (repo / "tests").mkdir()
    sample = repo / "src" / "sample.py"
    if not sample.exists():
        sample.write_text(
            "def old_name(value: int) -> int:\n"
            "    return value + 1\n",
            encoding="utf-8",
        )
    test_sample = repo / "tests" / "test_sample.py"
    if not test_sample.exists():
        test_sample.write_text(
            "from src.sample import old_name\n\n"
            "def test_old_name():\n"
            "    assert old_name(1) == 2\n",
            encoding="utf-8",
        )
    readme = repo / "README.md"
    if not readme.exists():
        readme.write_text(
            "# Sandbox Repo\n\n"
            "## Team protocol overview\n\n"
            "This section is intentionally terse and can be rewritten by W5.\n",
            encoding="utf-8",
        )
    if not (repo / ".git").exists():
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "stress@example.invalid"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Stress Harness"], cwd=repo, check=True)
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "seed sandbox repo"], cwd=repo, check=True)


def ensure_team_absent(team_name: str) -> None:
    if cs_teams.team_exists(team_name):
        raise TeamExistsError(f"team already exists: {team_name}")


def create_stress_team(team_name: str, scenario: Mapping[str, Any], sandbox: Path) -> Path:
    ensure_team_absent(team_name)
    cs_teams.create_team(
        team_name,
        session_id=f"stress-{team_name}",
        description=f"Phase 3 stress scenario {scenario.get('name', '')}",
    )
    team_dir = cs_teams.TEAMS_DIR / team_name
    for index, member in enumerate(scenario["members"]):
        name = str(member["name"])
        color = COLOR_PALETTE[index % len(COLOR_PALETTE)]
        teammate = TeammateMember(
            agent_id=f"{name}@{team_name}",
            name=name,
            agent_type=str(member.get("agent_type", "claude-anyteam")),
            model=str(member.get("model", "unknown")),
            prompt=f"Phase-3 stress target {name}. Work from tasks in team {team_name}.",
            color=color,
            plan_mode_required=False,
            joined_at=int(time.time() * 1000),
            tmux_pane_id="pending",
            cwd=str(sandbox / "repo"),
            backend_type=member_backend_type(member),
            is_active=False,
        )
        cs_teams.add_member(team_name, teammate)
        cs_messaging.ensure_inbox(team_name, name)
    cs_messaging.ensure_inbox(team_name, TEAM_LEAD)
    write_per_teammate_configs(team_name, scenario["members"])
    apply_agent_type_patches(team_name, scenario["members"])
    return team_dir


def write_per_teammate_configs(team_name: str, members: Iterable[Mapping[str, Any]]) -> None:
    agents_dir = cs_teams.TEAMS_DIR / team_name / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    for member in members:
        config: dict[str, str] = {}
        if member.get("model"):
            config["model"] = str(member["model"])
        if member.get("effort"):
            config["effort"] = str(member["effort"])
        if member.get("transport") == "exec":
            config["app_server"] = "false"
        _json_dump(agents_dir / f"{member['name']}.json", config)


def apply_agent_type_patches(team_name: str, members: Iterable[Mapping[str, Any]]) -> None:
    desired = {str(member["name"]): str(member.get("agent_type", "claude-anyteam")) for member in members}
    config = cs_teams.read_config(team_name)
    for member in config.members:
        if getattr(member, "name", None) in desired:
            member.agent_type = desired[member.name]
    cs_teams.write_config(team_name, config)


def _resolve_backend_binary(name: str) -> str:
    """Resolve a backend's CLI binary via shutil.which, raising loud if missing.

    The stress harness defaults `gemini`/`kimi`/`claude` settings to PATH
    name lookup, but PATH ordering in the spawn context can resolve to
    sibling claude-anyteam shim binaries (e.g. ``gemini-anyteam``) that
    don't accept ``--version``. S3 (homogeneous-gemini) hung 20+ minutes
    with this exact failure on 2026-04-27 (task #42).

    By passing the absolute path explicitly via ``--gemini-binary`` /
    ``--kimi-binary``, the spawned adapter's feature_test runs against
    the real Gemini/Kimi CLI rather than whatever PATH happens to surface.
    """
    path = shutil.which(name)
    if path is None:
        raise FileNotFoundError(
            f"backend binary {name!r} not found on PATH; cannot construct spawn command. "
            f"Install the {name} CLI or set its location explicitly in the scenario member spec."
        )
    return path


def _command_for_member(member: Mapping[str, Any], team_name: str, sandbox: Path) -> list[str] | None:
    agent_type = member.get("agent_type")
    name = str(member["name"])
    cwd = str(sandbox / "repo")
    base = [sys.executable, "-m"]
    if agent_type == "claude":
        model = str(member.get("model") or "sonnet")
        agent_type_arg = str(member.get("agent_type") or "claude")
        color = str(member.get("color") or "cyan")
        return [
            "claude",
            "--agent-id",
            f"{name}@{team_name}",
            "--agent-name",
            name,
            "--team-name",
            team_name,
            "--agent-color",
            color,
            "--parent-session-id",
            f"stress-{team_name}",
            "--agent-type",
            agent_type_arg,
            "--model",
            model,
        ]
    if agent_type == "codex":
        cmd = [*base, "claude_anyteam.cli", "--team", team_name, "--name", name, "--cwd", cwd]
        if member.get("model"):
            cmd += ["--model", str(member["model"])]
        if member.get("effort"):
            cmd += ["--effort", str(member["effort"])]
        if member.get("transport") == "exec":
            cmd.append("--no-app-server")
        elif member.get("transport") == "app_server":
            cmd.append("--app-server")
        return cmd
    if agent_type == "gemini":
        cmd = [*base, "claude_anyteam.backends.gemini.cli", "--team", team_name, "--name", name, "--cwd", cwd]
        cmd += ["--gemini-binary", _resolve_backend_binary("gemini")]
        if member.get("model"):
            cmd += ["--model", str(member["model"])]
        if member.get("effort"):
            cmd += ["--effort", str(member["effort"])]
        if member.get("transport"):
            cmd += ["--backend", str(member["transport"])]
        return cmd
    if agent_type == "kimi":
        cmd = [*base, "claude_anyteam.backends.kimi.cli", "--team", team_name, "--name", name, "--cwd", cwd]
        cmd += ["--kimi-binary", _resolve_backend_binary("kimi")]
        if member.get("model"):
            cmd += ["--model", str(member["model"])]
        if member.get("effort"):
            cmd += ["--effort", str(member["effort"])]
        cmd += ["--backend", str(member.get("transport", "headless"))]
        return cmd
    return None


def spawn_teammates(
    team_name: str,
    members: Iterable[Mapping[str, Any]],
    *,
    env: Mapping[str, str],
    sandbox: Path,
    notes: list[str],
) -> dict[str, subprocess.Popen[Any]]:
    procs: dict[str, subprocess.Popen[Any]] = {}
    proc_dir = cs_teams.TEAMS_DIR / team_name / "procs"
    proc_dir.mkdir(parents=True, exist_ok=True)
    for index, member in enumerate(members):
        name = str(member["name"])
        member_for_cmd = dict(member)
        member_for_cmd.setdefault("color", COLOR_PALETTE[index % len(COLOR_PALETTE)])
        cmd = _command_for_member(member_for_cmd, team_name, sandbox)
        if cmd is None:
            notes.append(f"partial_native_claude:skipped:{name}")
            continue
        (proc_dir / f"{name}.cmd.json").write_text(json.dumps(cmd, indent=2) + "\n", encoding="utf-8")
        stdout = (proc_dir / f"{name}.stdout.log").open("ab")
        stderr = (proc_dir / f"{name}.stderr.log").open("ab")
        procs[name] = subprocess.Popen(cmd, cwd=sandbox / "repo", env=dict(env), stdout=stdout, stderr=stderr)
    return procs


def build_run_workload_manifest(
    workload: Mapping[str, Any],
    *,
    scenario_id: str,
    run_id: str,
    scenario: Mapping[str, Any],
    sandbox: Path,
) -> dict[str, Any]:
    members = list(scenario["members"])
    n_tasks = int(scenario["n_tasks"])
    workload_id = str(workload["workload_id"])
    task_docs: list[dict[str, Any]] = []
    replacements_base = {
        "sandbox": str(sandbox),
        "repo": str(sandbox / "repo"),
    }
    for index in range(n_tasks):
        owner = str(members[index % len(members)]["name"])
        peer = str(members[(index + 1) % len(members)]["name"])
        replacements = {
            **replacements_base,
            "task_index": str(index + 1),
            "assigned_to": owner,
            "assigned_to_a": owner,
            "assigned_to_b": peer,
        }
        success_check = _deep_replace(copy.deepcopy(workload.get("success_check", {})), replacements)
        task_docs.append(
            {
                "task_id": str(index + 1),
                "workload_id": workload_id,
                "workload_name": workload.get("name"),
                "owner_expected": owner,
                "success_check": success_check,
            }
        )
    return {
        "schema_version": 1,
        "scenario": scenario_id,
        "run_id": run_id,
        "workload_id": workload_id,
        "workload_name": workload.get("name"),
        "description": workload.get("description"),
        "tasks": task_docs,
        "base_manifest": dict(workload),
    }


def post_tasks(
    team_name: str,
    workload: Mapping[str, Any],
    run_manifest: Mapping[str, Any],
    *,
    scenario: Mapping[str, Any],
    sandbox: Path,
) -> list[str]:
    members = list(scenario["members"])
    template = str(workload.get("lead_prompt_template") or workload.get("description") or workload.get("name"))
    task_ids: list[str] = []
    for index, task_doc in enumerate(run_manifest["tasks"]):
        owner = str(task_doc["owner_expected"])
        peer = str(members[(index + 1) % len(members)]["name"])
        description = _safe_format(
            template,
            {
                "sandbox": str(sandbox),
                "repo": str(sandbox / "repo"),
                "task_index": str(index + 1),
                "assigned_to": owner,
                "assigned_to_a": owner,
                "assigned_to_b": peer,
            },
        )
        task = cs_tasks.create_task(
            team_name,
            subject=f"{task_doc['workload_id']} {workload.get('name', 'workload')} #{index + 1}",
            description=description,
            metadata={
                "workload_id": task_doc["workload_id"],
                "workload_name": workload.get("name"),
                "owner_expected": owner,
                "success_check": task_doc["success_check"],
            },
        )
        task_ids.append(task.id)
        task_doc["task_id"] = task.id
    return task_ids


def wait_for_completion(team_name: str, task_ids: Iterable[str], deadline: float, *, poll_s: float = DEFAULT_POLL_SECONDS) -> bool:
    task_ids = list(task_ids)
    while time.monotonic() < deadline:
        tasks = [cs_tasks.get_task(team_name, task_id) for task_id in task_ids]
        if all(task.status in {"completed", "deleted"} for task in tasks):
            return True
        time.sleep(poll_s)
    return False


def force_complete_remaining(team_name: str, task_ids: Iterable[str]) -> None:
    for task_id in task_ids:
        task = cs_tasks.get_task(team_name, task_id)
        if task.status not in {"completed", "deleted"}:
            metadata = dict(task.metadata or {})
            metadata["blocked_reason"] = "scenario_timeout"
            cs_tasks.update_task(
                team_name,
                task_id,
                active_form="blocked: scenario_timeout",
                metadata=metadata,
            )


def drain_teammates(team_name: str, procs: Mapping[str, subprocess.Popen[Any]], *, timeout_s: float = 60.0) -> None:
    for name in procs:
        try:
            cs_messaging.send_shutdown_request(team_name, name, reason="stress scenario complete")
        except Exception:
            pass
    deadline = time.monotonic() + timeout_s
    for proc in procs.values():
        remaining = max(0.0, deadline - time.monotonic())
        try:
            proc.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            proc.terminate()
    for proc in procs.values():
        if proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass


def archive_events(team_name: str, dst: Path) -> None:
    src = cs_messaging.TEAMS_DIR / team_name / "events"
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True, exist_ok=True)
    if src.exists():
        for path in src.glob("*.jsonl"):
            shutil.copy2(path, dst / path.name)
    (dst / ".lock").touch(exist_ok=True)


def archive_team_config(team_name: str, dst: Path) -> None:
    src = cs_teams.TEAMS_DIR / team_name / "config.json"
    if src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def archive_tasks(team_name: str, dst: Path) -> None:
    src = cs_tasks.TASKS_DIR / team_name
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True, exist_ok=True)
    if src.exists():
        for path in src.glob("*.json"):
            shutil.copy2(path, dst / path.name)
    (dst / ".lock").touch(exist_ok=True)


def archive_procs(team_name: str, procs: Mapping[str, subprocess.Popen[Any]], dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    src = cs_teams.TEAMS_DIR / team_name / "procs"
    if src.exists():
        for path in src.iterdir():
            if path.is_file():
                shutil.copy2(path, dst / path.name)
    for name, proc in procs.items():
        (dst / f"{name}.exit_code").write_text(f"{proc.poll()}\n", encoding="utf-8")


def seed_dry_run_events(team_name: str, scenario_id: str, scenario: Mapping[str, Any], n_tasks: int) -> None:
    start = datetime(2026, 4, 27, 15, 30, tzinfo=timezone.utc)
    members = list(scenario["members"])
    turns = max(1, min(n_tasks, 4))
    for member_index, member in enumerate(members):
        agent = str(member["name"])
        backend = member_backend_type(member)
        peer = str(members[(member_index + 1) % len(members)]["name"]) if len(members) > 1 else TEAM_LEAD
        seq = 0
        for task_index in range(turns):
            t0 = start + timedelta(seconds=member_index * 11 + task_index * 60)
            turn_id = f"{agent}-turn-{task_index + 1}"
            task_id = str(task_index + 1)
            seq += 1
            pio.append_event(team_name, agent, _visibility_event(team_name, agent, backend, "turn_started", seq, t0, task_id, turn_id, {"mode": "stress_dry_run"}))
            if backend in {"codex_app_server", "gemini_acp", "claude_native"}:
                seq += 1
                pio.append_event(
                    team_name,
                    agent,
                    _visibility_event(
                        team_name,
                        agent,
                        backend,
                        "tool_event",
                        seq,
                        t0 + timedelta(seconds=5),
                        task_id,
                        turn_id,
                        {
                            "tool_name": "send_message",
                            "raw_backend_type": "send_message",
                            "category": "team_tool",
                            "recipient": peer,
                        },
                        summary="ask: dry-run peer coordination",
                    ),
                )
                seq += 1
                native_tool = "commandExecution" if backend == "codex_app_server" else "tool_call"
                pio.append_event(
                    team_name,
                    agent,
                    _visibility_event(
                        team_name,
                        agent,
                        backend,
                        "tool_event",
                        seq,
                        t0 + timedelta(seconds=10),
                        task_id,
                        turn_id,
                        {"tool_name": native_tool, "raw_backend_type": native_tool, "category": "host_tool"},
                    ),
                )
                if scenario_id in {"S5", "S10a", "S10b"} and task_index == 0:
                    seq += 1
                    pio.append_event(
                        team_name,
                        agent,
                        _visibility_event(
                            team_name,
                            agent,
                            backend,
                            "tool_event",
                            seq,
                            t0 + timedelta(seconds=12),
                            task_id,
                            turn_id,
                            {
                                "tool_name": "mcp_anyteam_capability_manifest",
                                "raw_backend_type": "mcp_anyteam_capability_manifest",
                                "category": "mcp_tool",
                                "recipient": peer,
                            },
                        ),
                    )
            seq += 1
            pio.append_event(
                team_name,
                agent,
                _visibility_event(
                    team_name,
                    agent,
                    backend,
                    "steer_ack",
                    seq,
                    t0 + timedelta(seconds=15),
                    task_id,
                    turn_id,
                    {"delivery": "delivered_next_turn", "acknowledged": True},
                ),
            )
            seq += 1
            pio.append_event(
                team_name,
                agent,
                _visibility_event(
                    team_name,
                    agent,
                    backend,
                    "turn_completed",
                    seq,
                    t0 + timedelta(seconds=30),
                    task_id,
                    turn_id,
                    {
                        "status": "ok",
                        "tool_call_events": 1 if backend not in {"kimi_headless", "gemini_headless"} else 0,
                        "last_message_preview": "ok",
                        "partial_events_available": backend not in {"kimi_headless", "gemini_headless"},
                    },
                ),
            )


def _visibility_event(
    team: str,
    agent: str,
    backend: str,
    kind: str,
    seq: int,
    timestamp: datetime,
    task_id: str,
    turn_id: str,
    payload: Mapping[str, Any],
    *,
    summary: str | None = None,
) -> VisibilityEvent:
    return VisibilityEvent.model_validate(
        {
            "kind": kind,
            "event_id": f"{agent}:{turn_id}:{seq:06d}",
            "timestamp": timestamp.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "team": team,
            "agent": agent,
            "backend": backend,
            "task_id": task_id,
            "turn_id": turn_id,
            "seq": seq,
            "severity": "error" if kind == "turn_failed" else "info",
            "summary": summary or f"{kind} {agent}",
            "payload": dict(payload),
        }
    )


def _load_scorers() -> tuple[ModuleType, ModuleType, ModuleType]:
    try:
        from tools.stress import score_collab, score_quality, score_throughput
    except ImportError as exc:
        raise ScorerFailure(f"unable to import Phase-3 scorers: {exc}") from exc
    return score_throughput, score_collab, score_quality


def _invoke_scorer(
    module: Any,
    *,
    name: str,
    events_dir: Path,
    scenario_id: str,
    run_id: str,
    out: Path,
    sandbox: Path | None = None,
    workload_manifest: Path | None = None,
) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    if hasattr(module, "score"):
        if name == "quality":
            result = module.score(
                events_dir=events_dir,
                sandbox=sandbox,
                workload_manifest=workload_manifest,
                scenario=scenario_id,
                run_id=run_id,
                out=out,
            )
        else:
            result = module.score(events_dir=events_dir, scenario=scenario_id, run_id=run_id, out=out)
        if isinstance(result, dict):
            if not (out / "scenario.json").exists():
                _json_dump(out / "scenario.json", result)
            return result
        if isinstance(result, int) and result != 0:
            raise ScorerFailure(f"{name} scorer returned {result}")
    elif name == "throughput" and hasattr(module, "score_source") and hasattr(module, "EventSource"):
        rc = module.score_source(
            source=module.EventSource(events_dir=events_dir),
            scenario=scenario_id,
            run_id=run_id,
            out=out,
        )
        if rc != 0:
            raise ScorerFailure(f"{name} scorer returned {rc}")
    elif name == "collab" and all(hasattr(module, attr) for attr in ("load_dataset", "build_scorecards", "write_outputs")):
        dataset = module.load_dataset(team=None, events_dir=events_dir)
        scenario_doc, pairs_doc, per_agent = module.build_scorecards(dataset, scenario=scenario_id, run_id=run_id)
        module.write_outputs(out, scenario_doc, pairs_doc, per_agent)
    elif hasattr(module, "main"):
        argv = ["--events-dir", str(events_dir), "--scenario", scenario_id, "--run-id", run_id, "--out", str(out)]
        if name == "quality":
            if sandbox is None or workload_manifest is None:
                raise ScorerFailure("quality scorer requires sandbox and workload manifest")
            argv.extend(["--sandbox", str(sandbox), "--workload-manifest", str(workload_manifest)])
        rc = module.main(argv)
        if rc != 0:
            raise ScorerFailure(f"{name} scorer returned {rc}")
    else:
        raise ScorerFailure(f"{name} scorer has no supported in-process API")

    scenario_json = out / "scenario.json"
    if not scenario_json.exists():
        raise ScorerFailure(f"{name} scorer did not write {scenario_json}")
    return _json_load(scenario_json)


def score_all(
    *,
    archive_dir: Path,
    scenario_id: str,
    run_id: str,
    sandbox: Path,
    workload_manifest: Path,
) -> dict[str, dict[str, Any]]:
    score_throughput, score_collab, score_quality = _load_scorers()
    throughput_card = _invoke_scorer(
        score_throughput,
        name="throughput",
        events_dir=archive_dir / "events",
        scenario_id=scenario_id,
        run_id=run_id,
        out=archive_dir / "throughput",
    )
    collab_card = _invoke_scorer(
        score_collab,
        name="collab",
        events_dir=archive_dir / "events",
        scenario_id=scenario_id,
        run_id=run_id,
        out=archive_dir / "collab",
    )
    quality_card = _invoke_scorer(
        score_quality,
        name="quality",
        events_dir=archive_dir / "events",
        scenario_id=scenario_id,
        run_id=run_id,
        out=archive_dir / "quality",
        sandbox=sandbox,
        workload_manifest=workload_manifest,
    )
    return {"throughput": throughput_card, "collab": collab_card, "quality": quality_card}


def _metric_from(card: Mapping[str, Any], path: tuple[str, ...], default: Any = None) -> Any:
    current: Any = card
    for key in path:
        if not isinstance(current, Mapping) or key not in current:
            return default
        current = current[key]
    return current


def _sum_s1_violations(quality_card: Mapping[str, Any], quality_dir: Path) -> int:
    direct = quality_card.get("s1_flatten_violations")
    if isinstance(direct, int):
        return direct
    if isinstance(direct, list):
        return len(direct)
    total = 0
    agents_dir = quality_dir / "agents"
    if agents_dir.exists():
        for path in agents_dir.glob("*.json"):
            try:
                agent_card = _json_load(path)
            except Exception:
                continue
            violations = agent_card.get("s1_flatten_violations", [])
            if isinstance(violations, list):
                total += len(violations)
    return total


def task_counts(team_name: str, task_ids: Iterable[str]) -> tuple[int, int]:
    completed = 0
    blocked = 0
    for task_id in task_ids:
        task = cs_tasks.get_task(team_name, task_id)
        if task.metadata and task.metadata.get("blocked_reason"):
            blocked += 1
        elif task.status == "completed":
            completed += 1
    return completed, blocked


def write_unified_scorecard(
    path: Path,
    *,
    scenario_id: str,
    scenario: Mapping[str, Any],
    run_id: str,
    run_time_git_sha: str,
    team_name: str,
    wall_clock_seconds: float,
    task_ids: Iterable[str],
    env_overrides: Mapping[str, str],
    scorer_cards: Mapping[str, Mapping[str, Any]],
    notes: list[str],
) -> dict[str, Any]:
    task_ids = list(task_ids)
    n_completed, n_blocked = task_counts(team_name, task_ids)
    throughput = scorer_cards.get("throughput", {})
    collab = scorer_cards.get("collab", {})
    quality = scorer_cards.get("quality", {})
    headline_metrics = {
        "M1_team_throughput_per_min": _metric_from(throughput, ("aggregate", "M1_throughput_per_min", "sum")),
        "M4_team_cross_peer_ratio": _metric_from(collab, ("aggregate", "M4_team_cross_peer_ratio")),
        "M5_team_failure_rate": _metric_from(throughput, ("aggregate", "M5_turn_failed_rate", "weighted_mean")),
        "M9_team_steer_ack_rate": _metric_from(collab, ("aggregate", "M9_team_steer_ack_rate")),
        "M11a_team_p95_rtt_seconds": _metric_from(collab, ("aggregate", "M11a_team_p95_rtt_seconds")),
        "samples_used_for_M11a": _metric_from(collab, ("aggregate", "samples_used_for_M11a")),
        "M11a_p50": _metric_from(collab, ("aggregate", "M11a_p50")),
        "M11a_max": _metric_from(collab, ("aggregate", "M11a_max")),
        "M11b_team_p95_turn_duration_seconds": _metric_from(throughput, ("aggregate", "M11b_team_p95_turn_duration_seconds")),
        "M12_team_average_coverage_ratio": quality.get("M12_team_average_coverage_ratio"),
        "M13_total_collisions": _metric_from(collab, ("aggregate", "M13_total_collisions")),
    }
    for key in HEADLINE_KEYS:
        headline_metrics.setdefault(key, None)
    s1_violations = _sum_s1_violations(quality, path.parent / "quality")
    scoring_time_git_sha = _git_sha()
    scorecard = {
        "schema_version": 1,
        "scenario": scenario_id,
        "scenario_name": scenario.get("name"),
        "run_id": run_id,
        "git_sha": run_time_git_sha,
        "run_time_git_sha": run_time_git_sha,
        "scoring_time_git_sha": scoring_time_git_sha,
        "wall_clock_seconds": wall_clock_seconds,
        "team": team_name,
        "n_tasks": len(task_ids),
        "n_completed": n_completed,
        "n_blocked": n_blocked,
        "ablation_against": scenario.get("ablation_against"),
        "env_overrides": dict(env_overrides),
        "scorecards": {
            "throughput": "throughput/scenario.json",
            "collab": "collab/scenario.json",
            "quality": "quality/scenario.json",
        },
        "headline_metrics": headline_metrics,
        "s1_flatten_violations": s1_violations,
        "north_star_signals": {
            "harness_preservation_violations": s1_violations,
            "visibility_degraded_count": quality.get("M6_team_total_visibility_degraded"),
            "peer_efficiency_p95_rtt_s": headline_metrics["M11a_team_p95_rtt_seconds"],
        },
        "notes": notes,
    }
    _json_dump(path, scorecard)
    return scorecard


def write_partial_scorecard(
    path: Path,
    *,
    scenario_id: str,
    scenario: Mapping[str, Any],
    run_id: str,
    run_time_git_sha: str,
    team_name: str,
    started_at: float,
    task_ids: Iterable[str],
    env_overrides: Mapping[str, str],
    scorer_cards: Mapping[str, Mapping[str, Any]],
    notes: list[str],
) -> dict[str, Any]:
    placeholder_cards = {"throughput": {}, "collab": {}, "quality": {}, **dict(scorer_cards)}
    return write_unified_scorecard(
        path,
        scenario_id=scenario_id,
        scenario=scenario,
        run_id=run_id,
        run_time_git_sha=run_time_git_sha,
        team_name=team_name,
        wall_clock_seconds=time.monotonic() - started_at,
        task_ids=task_ids,
        env_overrides=env_overrides,
        scorer_cards=placeholder_cards,
        notes=notes,
    )


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"


def cleanup_team(team_name: str) -> None:
    shutil.rmtree(cs_teams.TEAMS_DIR / team_name, ignore_errors=True)
    shutil.rmtree(cs_tasks.TASKS_DIR / team_name, ignore_errors=True)


def validate_inputs(scenario_id: str, workload_manifest: Path) -> dict[str, Any]:
    if scenario_id not in SCENARIOS:
        raise ScenarioInputError(f"unknown scenario {scenario_id!r}; expected one of {', '.join(sorted(SCENARIOS))}")
    if not workload_manifest.exists():
        raise ScenarioInputError(f"workload manifest missing: {workload_manifest}")
    try:
        workload = _json_load(workload_manifest)
    except json.JSONDecodeError as exc:
        raise ScenarioInputError(f"workload manifest invalid JSON: {workload_manifest}: {exc}") from exc
    if not workload.get("workload_id") or not workload.get("success_check"):
        raise ScenarioInputError(f"workload manifest missing workload_id or success_check: {workload_manifest}")
    return workload


def run_scenario(args: argparse.Namespace, *, stderr: TextIO | None = None) -> int:
    if stderr is None:
        stderr = sys.stderr
    scenario_id = args.scenario
    workload_path = Path(args.workload_manifest).expanduser()
    try:
        workload = validate_inputs(scenario_id, workload_path)
        scenario = SCENARIOS[scenario_id]
        run_id = args.run_id or utc_run_id()
        team_name = f"stress-{scenario_id}-{run_id}"
        sandbox = Path(args.sandbox).expanduser().resolve()
        archive_dir = Path(args.out).expanduser().resolve()
        ensure_team_absent(team_name)
    except TeamExistsError as exc:
        print(f"error: {exc}", file=stderr)
        return 2
    except ScenarioInputError as exc:
        print(f"error: {exc}", file=stderr)
        return 1

    started_at = time.monotonic()
    notes: list[str] = []
    procs: dict[str, subprocess.Popen[Any]] = {}
    task_ids: list[str] = []
    scorer_cards: dict[str, dict[str, Any]] = {}
    spawn_env, env_overrides = scenario_environment(scenario)
    run_time_git_sha = _git_sha()

    try:
        if getattr(args, "cleanup_sandbox", True):
            cleanup_stress_sandboxes(sandbox)
        write_sandbox_marker(sandbox, scenario_id=scenario_id, run_id=run_id)
        init_sandbox_repo(sandbox / "repo")
        create_stress_team(team_name, scenario, sandbox)
        run_manifest = build_run_workload_manifest(
            workload,
            scenario_id=scenario_id,
            run_id=run_id,
            scenario=scenario,
            sandbox=sandbox,
        )
        task_ids = post_tasks(team_name, workload, run_manifest, scenario=scenario, sandbox=sandbox)

        if args.dry_run:
            seed_dry_run_events(team_name, scenario_id, scenario, len(task_ids))
            notes.append("dry_run")
        else:
            procs = spawn_teammates(team_name, scenario["members"], env=spawn_env, sandbox=sandbox, notes=notes)
            completed = wait_for_completion(
                team_name,
                task_ids,
                time.monotonic() + float(args.timeout_seconds),
                poll_s=DEFAULT_POLL_SECONDS,
            )
            if not completed:
                force_complete_remaining(team_name, task_ids)
                notes.append("incomplete_run")
            drain_teammates(team_name, procs, timeout_s=60.0)

        archive_dir.mkdir(parents=True, exist_ok=True)
        archive_events(team_name, archive_dir / "events")
        archive_team_config(team_name, archive_dir / "team-config.json")
        archive_tasks(team_name, archive_dir / "tasks")
        archive_procs(team_name, procs, archive_dir / "procs")
        _json_dump(archive_dir / "workload-manifest.json", run_manifest)

        try:
            scorer_cards = score_all(
                archive_dir=archive_dir,
                scenario_id=scenario_id,
                run_id=run_id,
                sandbox=sandbox,
                workload_manifest=archive_dir / "workload-manifest.json",
            )
        except Exception as exc:
            notes.append(f"scorer_failure:{type(exc).__name__}:{exc}")
            write_partial_scorecard(
                archive_dir / "scorecard.json",
                scenario_id=scenario_id,
                scenario=scenario,
                run_id=run_id,
                run_time_git_sha=run_time_git_sha,
                team_name=team_name,
                started_at=started_at,
                task_ids=task_ids,
                env_overrides=env_overrides,
                scorer_cards=scorer_cards,
                notes=notes,
            )
            print(f"error: scorer failed: {exc}", file=stderr)
            if args.cleanup_team:
                cleanup_team(team_name)
            return 3

        write_unified_scorecard(
            archive_dir / "scorecard.json",
            scenario_id=scenario_id,
            scenario=scenario,
            run_id=run_id,
            run_time_git_sha=run_time_git_sha,
            team_name=team_name,
            wall_clock_seconds=time.monotonic() - started_at,
            task_ids=task_ids,
            env_overrides=env_overrides,
            scorer_cards=scorer_cards,
            notes=notes,
        )
        if args.cleanup_team:
            cleanup_team(team_name)
        print(json.dumps({"scorecard": str(archive_dir / "scorecard.json"), "team": team_name}, sort_keys=True))
        return 0
    except TeamExistsError as exc:
        print(f"error: {exc}", file=stderr)
        return 2
    except Exception as exc:
        print(f"error: run_scenario failed: {exc}", file=stderr)
        return 1
    finally:
        for proc in procs.values():
            if proc.poll() is None:
                try:
                    proc.terminate()
                except OSError:
                    pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", required=True, help="Scenario id, e.g. S1 or S10a")
    parser.add_argument("--run-id", default=None, help="UTC run id, e.g. 20260427T1530Z")
    parser.add_argument("--workload-manifest", required=True, help="Workload manifest JSON path")
    parser.add_argument("--sandbox", required=True, help="Scenario sandbox root")
    parser.add_argument("--out", required=True, help="Archive output directory")
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--cleanup-team", action="store_true")
    parser.add_argument(
        "--cleanup-sandbox",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Before initializing this run, remove old marked "
            "/tmp/stress-sandbox-* directories (default: on; use "
            "--no-cleanup-sandbox to opt out). Safety: only directories "
            f"containing {STRESS_SANDBOX_MARKER} are deleted, symlinks are "
            "skipped, and the current --sandbox path is never removed."
        ),
    )
    parser.add_argument("--dry-run", action="store_true", help="Skip live spawns and seed deterministic synthetic events")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_scenario(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
