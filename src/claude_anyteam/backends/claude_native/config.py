"""Runtime configuration for native Claude Code headless teammates."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from claude_anyteam.config import _pick
from claude_anyteam.env import (
    COLOR_ENV,
    CWD_ENV,
    LEGACY_COLOR_ENV,
    LEGACY_CWD_ENV,
    LEGACY_MODEL_ENV,
    LEGACY_NAME_ENV,
    LEGACY_PLAN_MODE_ENV,
    LEGACY_POLL_ENV,
    LEGACY_TEAM_ENV,
    MODEL_ENV,
    NAME_ENV,
    NATIVE_CLAUDE_ENV,
    PLAN_MODE_ENV,
    POLL_ENV,
    TEAM_ENV,
    env_first,
)

CLAUDE_EFFORT_ENV = "CLAUDE_ANYTEAM_CLAUDE_EFFORT"
CLAUDE_EFFORTS = frozenset({"minimal", "low", "medium", "high", "xhigh", "max"})


@dataclass(frozen=True)
class ClaudeNativeSettings:
    team_name: str
    agent_name: str
    cwd: Path
    poll_interval_s: float
    color: str
    plan_mode_required: bool
    claude_binary: str = "claude"
    model: str | None = None
    effort: str | None = None
    # Default bumped 900 → 1800 in task #5 to match codex turn_timeout_s.
    turn_timeout_s: float = 1800.0


def from_env(overrides: dict[str, object] | None = None) -> ClaudeNativeSettings:
    overrides = overrides or {}
    team_name = _pick(overrides, "team_name", env_first(os.environ, TEAM_ENV, LEGACY_TEAM_ENV))
    agent_name = _pick(overrides, "agent_name", env_first(os.environ, NAME_ENV, LEGACY_NAME_ENV))
    if not team_name:
        raise ValueError(f"team_name is required (CLI --team or {TEAM_ENV})")
    if not agent_name:
        raise ValueError(f"agent_name is required (CLI --name or {NAME_ENV})")

    cwd_raw = _pick(overrides, "cwd", env_first(os.environ, CWD_ENV, LEGACY_CWD_ENV, default=os.getcwd()))
    cwd = Path(str(cwd_raw)).resolve()
    if not cwd.is_absolute():
        raise ValueError(f"cwd must be absolute, got {cwd}")

    poll = float(_pick(overrides, "poll_interval_s", env_first(os.environ, POLL_ENV, LEGACY_POLL_ENV, default="1.5")))
    color = str(_pick(overrides, "color", env_first(os.environ, COLOR_ENV, LEGACY_COLOR_ENV, default="cyan")))
    plan_raw = str(_pick(overrides, "plan_mode_required", env_first(os.environ, PLAN_MODE_ENV, LEGACY_PLAN_MODE_ENV, default="false")))
    plan_mode_required = plan_raw.lower() in {"1", "true", "yes", "on"}
    model_raw = _pick(overrides, "model", env_first(os.environ, MODEL_ENV, LEGACY_MODEL_ENV, default="sonnet"))
    effort_raw = _pick(overrides, "effort", os.environ.get(CLAUDE_EFFORT_ENV))
    effort = str(effort_raw) if effort_raw else None
    if effort is not None and effort not in CLAUDE_EFFORTS:
        raise ValueError(
            f"Claude effort must be one of {', '.join(sorted(CLAUDE_EFFORTS))}, got {effort!r}"
        )
    timeout_raw = _pick(overrides, "turn_timeout_s", os.environ.get("CLAUDE_ANYTEAM_CLAUDE_TURN_TIMEOUT_S", "1800"))

    return ClaudeNativeSettings(
        team_name=str(team_name),
        agent_name=str(agent_name),
        cwd=cwd,
        poll_interval_s=poll,
        color=color,
        plan_mode_required=plan_mode_required,
        claude_binary=str(_pick(overrides, "claude_binary", os.environ.get(NATIVE_CLAUDE_ENV, "claude"))),
        model=str(model_raw) if model_raw else None,
        effort=effort,
        turn_timeout_s=float(timeout_raw),
    )
