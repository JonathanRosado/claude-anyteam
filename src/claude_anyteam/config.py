"""Runtime configuration for the adapter.

Resolved from (in order of precedence): CLI flags > environment variables >
defaults. Produced once at startup; the rest of the adapter treats it as
immutable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .env import (
    APP_SERVER_ENV,
    COLOR_ENV,
    CWD_ENV,
    EFFORT_ENV,
    LEGACY_APP_SERVER_ENV,
    LEGACY_COLOR_ENV,
    LEGACY_CWD_ENV,
    LEGACY_EFFORT_ENV,
    LEGACY_MODEL_ENV,
    LEGACY_NAME_ENV,
    LEGACY_NON_PROGRESS_INTERRUPT_ENV,
    LEGACY_NON_PROGRESS_WARN_ENV,
    LEGACY_POLL_ENV,
    LEGACY_PLAN_MODE_ENV,
    LEGACY_TEAM_ENV,
    LEGACY_TURN_TIMEOUT_ENV,
    MODEL_ENV,
    NAME_ENV,
    NON_PROGRESS_INTERRUPT_ENV,
    NON_PROGRESS_WARN_ENV,
    POLL_ENV,
    PLAN_MODE_ENV,
    TEAM_ENV,
    TURN_TIMEOUT_ENV,
    env_first,
)


@dataclass(frozen=True)
class Settings:
    team_name: str
    agent_name: str
    cwd: Path
    poll_interval_s: float
    color: str
    plan_mode_required: bool
    codex_binary: str
    # v7.1: when True, invoke Codex via `codex app-server` with a long-lived
    # JSON-RPC session so the adapter can inject mid-task turns via
    # `turn/steer`. Default was False in v7.1 (opt-in); flipped to True
    # in task #21 per user direction — mid-task reactivity is a default-
    # capability rather than a flag that has to be discovered. Users
    # wanting v7.2 `codex exec resume` session memory must opt out via
    # `--no-app-server` (the two modes are orthogonal; see
    # docs/v7.2-notes.md §6).
    app_server: bool = True
    # Codex model slug (e.g. "gpt-5.5", "gpt-5.4", "gpt-5.3-codex") and reasoning effort
    # (low|medium|high|xhigh). Both optional: when unset the adapter passes
    # no override and Codex falls back to `~/.codex/config.toml` defaults,
    # preserving the pre-v7.3 behavior. When set they flow through both the
    # App Server path (as `model`/`effort` JSON-RPC params on thread/start
    # and turn/start) and the fresh-exec path (as `-c model="…"` /
    # `-c model_reasoning_effort="…"` CLI overrides). Per-teammate tuning:
    # different adapter processes on the same host can run at different
    # model/effort without any global config surgery.
    model: str | None = None
    effort: str | None = None
    # Per-teammate Codex App Server turn timeout in seconds. Bounds the
    # wall-clock duration of a single turn (`app_server_invoke` polling
    # loop). Default 1800s as of task #5 — the prior 900s default
    # interrupted legitimately long Codex turns (large test suites,
    # multi-file refactors at xhigh effort) and was the dominant pain
    # the RFC at docs/design/timers-vs-visibility.md (issue #50) was
    # written to address. Configurable via `team-agent --turn-timeout-s`,
    # the `CLAUDE_ANYTEAM_TURN_TIMEOUT_S` env, or the per-teammate
    # `agents/<name>.json` shim config. Range [60, 3600] enforced at
    # parse time; cap stays 3600 (we have no business running a single
    # turn longer than an hour).
    turn_timeout_s: float = 1800.0
    # Codex App Server-only soft non-progress watchdog. When set, emits a
    # turn_progress warning envelope and checkpoint steer after this many
    # seconds with no App Server-visible progress. ``None`` (the default
    # as of task #5) disables the soft watchdog — see the RFC at
    # docs/design/timers-vs-visibility.md (issue #50) for why visibility
    # events (e.g. a future ``app_server_idle_quiet``) are the preferred
    # signal. Existing users who want the prior behavior can re-enable
    # by setting any value in [60, 1800]. Range upper raised from the
    # prior 900s so opt-in users can scale proportionally to the new
    # 1800s ``turn_timeout_s`` default.
    non_progress_warn_s: float | None = None
    # Optional Codex App Server-only hard early interrupt threshold. None is
    # the default and means the watchdog never interrupts before the normal
    # turn_timeout_s cap. When set, R20 only uses it after the soft watchdog
    # has fired and no later checkpoint is observed.
    non_progress_interrupt_s: float | None = None


def from_env(overrides: dict[str, object] | None = None) -> Settings:
    overrides = overrides or {}

    team_name = _pick(overrides, "team_name", env_first(os.environ, TEAM_ENV, LEGACY_TEAM_ENV))
    agent_name = _pick(overrides, "agent_name", env_first(os.environ, NAME_ENV, LEGACY_NAME_ENV))
    if not team_name:
        raise ValueError(f"team_name is required (CLI --team or {TEAM_ENV})")
    if not agent_name:
        raise ValueError(f"agent_name is required (CLI --name or {NAME_ENV})")

    cwd_raw = _pick(
        overrides,
        "cwd",
        env_first(os.environ, CWD_ENV, LEGACY_CWD_ENV, default=os.getcwd()),
    )
    cwd = Path(str(cwd_raw)).resolve()
    if not cwd.is_absolute():
        raise ValueError(f"cwd must be absolute, got {cwd}")

    poll = float(
        _pick(overrides, "poll_interval_s", env_first(os.environ, POLL_ENV, LEGACY_POLL_ENV, default="1.5"))
    )
    color = str(_pick(overrides, "color", env_first(os.environ, COLOR_ENV, LEGACY_COLOR_ENV, default="cyan")))

    plan_raw = str(
        _pick(
            overrides,
            "plan_mode_required",
            env_first(os.environ, PLAN_MODE_ENV, LEGACY_PLAN_MODE_ENV, default="false"),
        )
    )
    plan_mode_required = plan_raw.lower() in {"1", "true", "yes", "on"}

    codex_binary = str(_pick(overrides, "codex_binary", os.environ.get("CODEX_BINARY", "codex")))

    # v7.1 default (task #21): App Server is ON unless explicitly disabled
    # via --no-app-server or CLAUDE_ANYTEAM_APP_SERVER=false. Users who
    # want the v7.2 `codex exec resume` session-memory path must opt out
    # explicitly (the two modes are orthogonal; see docs/v7.2-notes.md §6).
    app_server_raw = str(
        _pick(
            overrides,
            "app_server",
            env_first(os.environ, APP_SERVER_ENV, LEGACY_APP_SERVER_ENV, default="true"),
        )
    )
    app_server = app_server_raw.lower() in {"1", "true", "yes", "on"}

    model_raw = _pick(overrides, "model", env_first(os.environ, MODEL_ENV, LEGACY_MODEL_ENV))
    model = str(model_raw) if model_raw else None

    effort_raw = _pick(overrides, "effort", env_first(os.environ, EFFORT_ENV, LEGACY_EFFORT_ENV))
    effort = str(effort_raw) if effort_raw else None
    if effort is not None and effort not in {"low", "medium", "high", "xhigh"}:
        raise ValueError(
            f"effort must be one of low|medium|high|xhigh, got {effort!r}"
        )

    turn_timeout_raw = _pick(
        overrides,
        "turn_timeout_s",
        env_first(os.environ, TURN_TIMEOUT_ENV, LEGACY_TURN_TIMEOUT_ENV, default="1800"),
    )
    try:
        turn_timeout_s = float(turn_timeout_raw)
    except (TypeError, ValueError) as e:
        raise ValueError(f"turn_timeout_s must be numeric, got {turn_timeout_raw!r}") from e
    if not (60.0 <= turn_timeout_s <= 3600.0):
        raise ValueError(
            f"turn_timeout_s must be in [60, 3600] seconds, got {turn_timeout_s}"
        )

    non_progress_warn_raw = _pick(
        overrides,
        "non_progress_warn_s",
        env_first(
            os.environ,
            NON_PROGRESS_WARN_ENV,
            LEGACY_NON_PROGRESS_WARN_ENV,
        ),
    )
    non_progress_warn_s: float | None
    if non_progress_warn_raw in (None, ""):
        # Task #5 / RFC #50 Phase B: the soft watchdog is opt-in. Default
        # None disables warn-time steering; the lead reads typed visibility
        # events instead (see docs/design/timers-vs-visibility.md). Existing
        # users who want the prior behavior pass an explicit value here, via
        # ``--non-progress-warn-s``, or via ``CLAUDE_ANYTEAM_NON_PROGRESS_WARN_S``.
        non_progress_warn_s = None
    else:
        try:
            non_progress_warn_s = float(non_progress_warn_raw)
        except (TypeError, ValueError) as e:
            raise ValueError(
                f"non_progress_warn_s must be numeric, got {non_progress_warn_raw!r}"
            ) from e
        if not (60.0 <= non_progress_warn_s <= 1800.0):
            raise ValueError(
                "non_progress_warn_s must be in [60, 1800] seconds, "
                f"got {non_progress_warn_s}"
            )

    non_progress_interrupt_raw = _pick(
        overrides,
        "non_progress_interrupt_s",
        env_first(
            os.environ,
            NON_PROGRESS_INTERRUPT_ENV,
            LEGACY_NON_PROGRESS_INTERRUPT_ENV,
        ),
    )
    non_progress_interrupt_s: float | None
    if non_progress_interrupt_raw in (None, ""):
        non_progress_interrupt_s = None
    else:
        try:
            non_progress_interrupt_s = float(non_progress_interrupt_raw)
        except (TypeError, ValueError) as e:
            raise ValueError(
                "non_progress_interrupt_s must be numeric when set, "
                f"got {non_progress_interrupt_raw!r}"
            ) from e
        if not (60.0 <= non_progress_interrupt_s <= 3600.0):
            raise ValueError(
                "non_progress_interrupt_s must be in [60, 3600] seconds when set, "
                f"got {non_progress_interrupt_s}"
            )

    return Settings(
        team_name=str(team_name),
        agent_name=str(agent_name),
        cwd=cwd,
        poll_interval_s=poll,
        color=color,
        plan_mode_required=plan_mode_required,
        codex_binary=codex_binary,
        app_server=app_server,
        model=model,
        effort=effort,
        turn_timeout_s=turn_timeout_s,
        non_progress_warn_s=non_progress_warn_s,
        non_progress_interrupt_s=non_progress_interrupt_s,
    )


def _pick(overrides: dict[str, object], key: str, fallback: object | None) -> object | None:
    if key in overrides and overrides[key] is not None:
        return overrides[key]
    return fallback
