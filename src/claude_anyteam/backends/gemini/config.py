"""Runtime configuration for the Gemini-backed claude-anyteam adapter."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from claude_anyteam.config import _pick
from claude_anyteam.env import (
    COLOR_ENV, CWD_ENV, LEGACY_COLOR_ENV, LEGACY_CWD_ENV, LEGACY_MODEL_ENV,
    LEGACY_NAME_ENV, LEGACY_PLAN_MODE_ENV, LEGACY_POLL_ENV, LEGACY_TEAM_ENV,
    MODEL_ENV, NAME_ENV, PLAN_MODE_ENV, POLL_ENV, TEAM_ENV, env_first,
)

GEMINI_BINARY_ENV = "CLAUDE_ANYTEAM_GEMINI_BINARY"
GEMINI_HOME_ENV = "CLAUDE_ANYTEAM_GEMINI_HOME"
GEMINI_BACKEND_ENV = "CLAUDE_ANYTEAM_GEMINI_BACKEND"
GEMINI_EFFORT_ENV = "CLAUDE_ANYTEAM_GEMINI_EFFORT"
GEMINI_TRUST_ENV = "CLAUDE_ANYTEAM_GEMINI_TRUST"

GEMINI_TRUST_MODES = {"trusted", "default", "plan"}
GEMINI_EFFORTS = {"minimal", "low", "medium", "high", "xhigh"}


@dataclass(frozen=True)
class GeminiSettings:
    team_name: str
    agent_name: str
    cwd: Path
    poll_interval_s: float
    color: str
    plan_mode_required: bool
    gemini_binary: str = "gemini"
    model: str | None = None
    effort: str | None = None
    gemini_home: Path | None = None
    # v0.6.0 default flip: ACP is now the default transport. Headless mode
    # is single-shot per invocation and was the structural amplifier for the
    # B4 productivity gap in the field test (Gemini agents emitted only idle
    # pings until forced into action-first prompts). ACP gives the adapter
    # mid-turn steering and persistent sessions, closing the gap to Codex
    # App Server. See bug-triage/B4-gemini-productivity.md (hypothesis #3).
    # Users who need the legacy headless transport can set --backend headless
    # or CLAUDE_ANYTEAM_GEMINI_BACKEND=headless.
    backend: Literal["headless", "acp"] = "acp"
    trust_mode: Literal["trusted", "default", "plan"] = "trusted"


def from_env(overrides: dict[str, object] | None = None) -> GeminiSettings:
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
    model_raw = _pick(overrides, "model", env_first(os.environ, MODEL_ENV, LEGACY_MODEL_ENV))
    effort_raw = _pick(overrides, "effort", os.environ.get(GEMINI_EFFORT_ENV))
    effort = str(effort_raw) if effort_raw else None
    if effort is not None and effort not in GEMINI_EFFORTS:
        raise ValueError(
            f"Gemini effort must be one of minimal|low|medium|high|xhigh, got {effort!r}"
        )
    home_raw = _pick(overrides, "gemini_home", os.environ.get(GEMINI_HOME_ENV))
    backend_raw = str(_pick(overrides, "backend", os.environ.get(GEMINI_BACKEND_ENV, "acp")))
    if backend_raw not in {"headless", "acp"}:
        raise ValueError(f"Gemini backend must be headless or acp, got {backend_raw!r}")
    trust_raw = str(_pick(overrides, "trust_mode", os.environ.get(GEMINI_TRUST_ENV, "trusted")))
    if trust_raw not in GEMINI_TRUST_MODES:
        raise ValueError(f"Gemini trust mode must be trusted, default, or plan, got {trust_raw!r}")

    return GeminiSettings(
        team_name=str(team_name),
        agent_name=str(agent_name),
        cwd=cwd,
        poll_interval_s=poll,
        color=color,
        plan_mode_required=plan_mode_required,
        gemini_binary=str(_pick(overrides, "gemini_binary", os.environ.get(GEMINI_BINARY_ENV, "gemini"))),
        model=str(model_raw) if model_raw else None,
        effort=effort,
        gemini_home=Path(str(home_raw)).expanduser().resolve() if home_raw else None,
        backend=backend_raw,  # type: ignore[arg-type]
        trust_mode=trust_raw,  # type: ignore[arg-type]
    )
