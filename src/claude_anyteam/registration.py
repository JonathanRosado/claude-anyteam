"""Self-registration with a Claude Code team.

The adapter starts independently of the harness and appends its own member
entry to `~/.claude/teams/{team}/config.json`. M0 confirmed the harness
tolerates this: the config mutation is preserved, and the harness delivers
messages to an inbox file the adapter creates itself.

Registration is idempotent. If a member with our name already exists, we
treat it as an existing registration (do not duplicate, do not mutate).
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile

from claude_teams._filelock import file_lock

from . import logger
from .config import Settings

TEAMS_ROOT = Path.home() / ".claude" / "teams"


class RegistrationError(RuntimeError):
    """Raised when we cannot safely register with the team."""


def team_dir(team: str) -> Path:
    return TEAMS_ROOT / team


def config_path(team: str) -> Path:
    return team_dir(team) / "config.json"


def inbox_path(team: str, name: str) -> Path:
    return team_dir(team) / "inboxes" / f"{name}.json"


def _team_lock_path(team: str) -> Path:
    # Reuse the harness-managed inbox lock so claude-anyteam registration
    # participates in the same mutual exclusion that native teammates use.
    return team_dir(team) / "inboxes" / ".lock"


@contextmanager
def _locked_team_config(team: str) -> Iterator[None]:
    lock_path = _team_lock_path(team)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(lock_path):
        yield


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    try:
        tmp.write(text)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp.close()
        os.replace(tmp.name, path)
    except Exception:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        raise


def register(settings: Settings) -> dict:
    """Register the adapter in the team config. Idempotent.

    Returns the member entry that corresponds to this adapter after
    registration (either freshly written or existing).
    """
    cfg_path = config_path(settings.team_name)
    with _locked_team_config(settings.team_name):
        if not cfg_path.exists():
            raise RegistrationError(
                f"team config not found at {cfg_path}. Start the Claude Code team first."
            )

        raw = cfg_path.read_text(encoding="utf-8")
        try:
            cfg = json.loads(raw)
        except json.JSONDecodeError as e:
            raise RegistrationError(f"team config at {cfg_path} is not valid JSON: {e}") from e

        members = cfg.get("members")
        if not isinstance(members, list):
            raise RegistrationError(f"team config at {cfg_path} has no `members` array")

        added = False
        for existing in members:
            if isinstance(existing, dict) and existing.get("name") == settings.agent_name:
                entry = existing
                break
        else:
            entry = {
                "agentId": f"{settings.agent_name}@{settings.team_name}",
                "name": settings.agent_name,
                "color": settings.color,
                "joinedAt": int(time.time() * 1000),
                # Advertise the same teammate shape native Claude sessions use
                # so the harness can treat Codex teammates as normal visible
                # team members in TUI presence/UI surfaces. Runtime delivery
                # remains mailbox-based; we are only aligning the registry
                # metadata here.
                "tmuxPaneId": "in-process",
                "subscriptions": [],
                "agentType": "claude-anyteam",
                "model": "codex-cli",
                "prompt": (
                    "Codex teammate adapter. Protocol I/O is handled by the adapter; "
                    "coding work is delegated to `codex exec`. No Claude LLM is involved."
                ),
                "planModeRequired": settings.plan_mode_required,
                "cwd": str(settings.cwd),
                "backendType": "in-process",
            }
            members.append(entry)
            serialized = json.dumps(cfg, indent=2) + "\n"
            _atomic_write_text(cfg_path, serialized)
            added = True

    _ensure_inbox(settings.team_name, settings.agent_name)

    if added:
        logger.info(
            "registration.added",
            team=settings.team_name,
            name=settings.agent_name,
            agent_id=entry["agentId"],
        )
    else:
        logger.info(
            "registration.existing",
            team=settings.team_name,
            name=settings.agent_name,
        )
    return entry


def _ensure_inbox(team: str, name: str) -> None:
    path = inbox_path(team, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        _atomic_write_text(path, "[]\n")


def deregister(settings: Settings) -> bool:
    """Remove the adapter's member entry from the team config and delete
    its inbox file. Symmetric with `register()`: the inbox was created
    there, so we clean it up here.

    Returns True if a matching member entry was found and removed. Safe to
    call if the entry has already been removed. Inbox deletion is
    best-effort — we swallow errors so a stale inbox doesn't block a
    clean config deregistration.
    """
    cfg_path = config_path(settings.team_name)
    removed = False
    if cfg_path.exists():
        with _locked_team_config(settings.team_name):
            if cfg_path.exists():
                raw = cfg_path.read_text(encoding="utf-8")
                try:
                    cfg = json.loads(raw)
                    members = cfg.get("members", [])
                    before = len(members)
                    cfg["members"] = [
                        m
                        for m in members
                        if not (isinstance(m, dict) and m.get("name") == settings.agent_name)
                    ]
                    removed = len(cfg["members"]) != before
                    if removed:
                        serialized = json.dumps(cfg, indent=2) + "\n"
                        _atomic_write_text(cfg_path, serialized)
                        logger.info(
                            "registration.removed",
                            team=settings.team_name,
                            name=settings.agent_name,
                        )
                except json.JSONDecodeError:
                    # Config is unreadable — leave it alone but continue to inbox cleanup.
                    logger.warn("registration.removed_config_unreadable", path=str(cfg_path))

    ibx = inbox_path(settings.team_name, settings.agent_name)
    if ibx.exists():
        try:
            ibx.unlink()
            logger.info(
                "registration.inbox_cleaned",
                team=settings.team_name,
                name=settings.agent_name,
            )
        except OSError as e:
            logger.warn("registration.inbox_cleanup_fail", error=str(e))

    return removed
