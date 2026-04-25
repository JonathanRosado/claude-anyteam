"""Runtime configuration for the Kimi-backed claude-anyteam adapter."""
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

KIMI_BINARY_ENV = "CLAUDE_ANYTEAM_KIMI_BINARY"
KIMI_HOME_ENV = "CLAUDE_ANYTEAM_KIMI_HOME"
KIMI_BACKEND_ENV = "CLAUDE_ANYTEAM_KIMI_BACKEND"
KIMI_EFFORT_ENV = "CLAUDE_ANYTEAM_KIMI_EFFORT"
KIMI_THINKING_ENV = "CLAUDE_ANYTEAM_KIMI_THINKING"

KIMI_THINKING = frozenset({"on", "off", "auto"})
KIMI_EFFORTS = frozenset({"minimal", "low", "medium", "high", "xhigh"})
KIMI_BACKENDS = frozenset({"headless", "acp"})


@dataclass(frozen=True)
class KimiSettings:
    team_name: str
    agent_name: str
    cwd: Path
    poll_interval_s: float
    color: str
    plan_mode_required: bool
    kimi_binary: str = "kimi"
    model: str | None = None
    effort: str | None = None
    kimi_home: Path | None = None
    backend: Literal["headless", "acp"] = "headless"
    thinking: Literal["on", "off", "auto"] = "auto"


def from_env(overrides: dict[str, object] | None = None) -> KimiSettings:
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
    effort_raw = _pick(overrides, "effort", os.environ.get(KIMI_EFFORT_ENV))
    effort = str(effort_raw) if effort_raw else None
    if effort is not None and effort not in KIMI_EFFORTS:
        raise ValueError(
            f"Kimi effort must be one of minimal|low|medium|high|xhigh, got {effort!r}"
        )
    home_raw = _pick(overrides, "kimi_home", os.environ.get(KIMI_HOME_ENV))
    backend_raw = str(_pick(overrides, "backend", os.environ.get(KIMI_BACKEND_ENV, "headless")))
    if backend_raw not in KIMI_BACKENDS:
        raise ValueError(f"Kimi backend must be headless or acp, got {backend_raw!r}")
    thinking_raw = str(_pick(overrides, "thinking", os.environ.get(KIMI_THINKING_ENV, "auto")))
    if thinking_raw not in KIMI_THINKING:
        raise ValueError(f"Kimi thinking must be on, off, or auto, got {thinking_raw!r}")

    return KimiSettings(
        team_name=str(team_name),
        agent_name=str(agent_name),
        cwd=cwd,
        poll_interval_s=poll,
        color=color,
        plan_mode_required=plan_mode_required,
        kimi_binary=str(_pick(overrides, "kimi_binary", os.environ.get(KIMI_BINARY_ENV, "kimi"))),
        model=str(model_raw) if model_raw else None,
        effort=effort,
        kimi_home=Path(str(home_raw)).expanduser().resolve() if home_raw else None,
        backend=backend_raw,  # type: ignore[arg-type]
        thinking=thinking_raw,  # type: ignore[arg-type]
    )
