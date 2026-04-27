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
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import NamedTemporaryFile

from claude_teams._filelock import config_lock

from . import logger
from .capabilities import CAPABILITY_MANIFEST_VERSION, build_agent_card
from .capability_manifest import (
    broadcast_manifest_event,
    delete_manifest,
    manifest_version as _manifest_version,
    read_manifest_file,
    write_manifest,
)
from .config import Settings
from .messages import CapabilityManifestUpdatedOut


@dataclass(frozen=True)
class BackendMetadata:
    agent_type: str = "claude-anyteam"
    model: str = "codex-cli"
    prompt: str = (
        "Codex teammate adapter. Protocol I/O is handled by the adapter; "
        "coding work is delegated to `codex exec`. No Claude LLM is involved."
    )
    backend_type: str = "in-process"
    capabilities: list[str] = field(default_factory=list)
    capability_manifest: dict[str, dict] | None = None
    capability_version: str = CAPABILITY_MANIFEST_VERSION
    transport: str | None = None
    host_tool_surface: str | None = None
    coupling_regime: str | None = None

TEAMS_ROOT = Path.home() / ".claude" / "teams"


class RegistrationError(RuntimeError):
    """Raised when we cannot safely register with the team."""


def team_dir(team: str) -> Path:
    return TEAMS_ROOT / team


def config_path(team: str) -> Path:
    return team_dir(team) / "config.json"


def inbox_path(team: str, name: str) -> Path:
    return team_dir(team) / "inboxes" / f"{name}.json"


@contextmanager
def _locked_team_config(team: str) -> Iterator[None]:
    with config_lock(team_dir(team)):
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


def _member_names(members: list) -> list[str]:
    names: list[str] = []
    for member in members:
        if isinstance(member, dict):
            name = member.get("name")
            if isinstance(name, str) and name:
                names.append(name)
    return names


def _build_manifest(settings: Settings, metadata: BackendMetadata, entry: dict) -> dict:
    agent_id = str(entry.get("agentId") or f"{settings.agent_name}@{settings.team_name}")
    return build_agent_card(
        team_name=settings.team_name,
        agent_name=settings.agent_name,
        agent_id=agent_id,
        agent_type=metadata.agent_type,
        model=metadata.model,
        backend_type=metadata.backend_type,
        capabilities=list(metadata.capabilities),
        capability_manifest=metadata.capability_manifest,
        capability_version=metadata.capability_version,
        transport=metadata.transport,
        host_tool_surface=metadata.host_tool_surface,
        coupling_regime=metadata.coupling_regime,
    )


def _prior_manifest_version(team: str, agent_name: str) -> str | None:
    from .capability_manifest import manifest_path_for_team_dir

    path = manifest_path_for_team_dir(team_dir(team), agent_name)
    try:
        return _manifest_version(read_manifest_file(path))
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
        return None


def register(settings: Settings, metadata: BackendMetadata | None = None) -> dict:
    """Register the adapter in the team config. Idempotent.

    Returns the member entry that corresponds to this adapter after
    registration (either freshly written or existing).
    """
    metadata = metadata or BackendMetadata()
    previous_manifest_version = _prior_manifest_version(settings.team_name, settings.agent_name)
    cfg_path = config_path(settings.team_name)
    recipients: list[str] = []
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
        upgraded_fields: list[str] = []
        for existing in members:
            if isinstance(existing, dict) and existing.get("name") == settings.agent_name:
                entry = existing
                # Self-heal: if the host's Agent-tool spawn left stale
                # agentType/backendType on our row (e.g. agentType="general-purpose"
                # before team-patch ran), upgrade in place. Without this, the
                # wrapper's send_message MCP allowlist stays wrong for the rest
                # of the session even though the on-disk config has been corrected.
                if existing.get("agentType") != metadata.agent_type:
                    existing["agentType"] = metadata.agent_type
                    upgraded_fields.append("agentType")
                if existing.get("backendType") != metadata.backend_type:
                    existing["backendType"] = metadata.backend_type
                    upgraded_fields.append("backendType")
                # 09 R11 cheap-flags layer: add capabilities to legacy rows
                # only when absent. If an operator manually set the 07 §1.7
                # roster declaration (e.g. to hold back an 08 CD-1..CD-6
                # primitive), preserve it exactly.
                if "capabilities" not in existing:
                    existing["capabilities"] = list(metadata.capabilities)
                    upgraded_fields.append("capabilities")
                if upgraded_fields:
                    serialized = json.dumps(cfg, indent=2) + "\n"
                    _atomic_write_text(cfg_path, serialized)
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
                "agentType": metadata.agent_type,
                "model": metadata.model,
                "prompt": metadata.prompt,
                "planModeRequired": settings.plan_mode_required,
                "cwd": str(settings.cwd),
                "backendType": metadata.backend_type,
                "capabilities": list(metadata.capabilities),
            }
            members.append(entry)
            serialized = json.dumps(cfg, indent=2) + "\n"
            _atomic_write_text(cfg_path, serialized)
            added = True

        recipients = _member_names(members)

    _ensure_inbox(settings.team_name, settings.agent_name)

    manifest = _build_manifest(settings, metadata, entry)
    manifest_path = write_manifest(team_dir(settings.team_name), settings.agent_name, manifest)
    current_manifest_version = str(manifest.get("capability_version") or metadata.capability_version)
    should_broadcast_manifest = added or bool(upgraded_fields) or (previous_manifest_version != current_manifest_version)
    if should_broadcast_manifest:
        event = CapabilityManifestUpdatedOut(
            agent_name=settings.agent_name,
            capability_version=current_manifest_version,
            manifest_path=str(manifest_path),
        )
        delivered = broadcast_manifest_event(
            team_dir(settings.team_name),
            sender=settings.agent_name,
            recipients=recipients,
            event=event,
        )
        logger.info(
            "capability_manifest.broadcasted",
            team=settings.team_name,
            name=settings.agent_name,
            capability_version=current_manifest_version,
            recipients=delivered,
        )

    if added:
        logger.info(
            "registration.added",
            team=settings.team_name,
            name=settings.agent_name,
            agent_id=entry["agentId"],
        )
    elif upgraded_fields:
        logger.info(
            "registration.upgraded",
            team=settings.team_name,
            name=settings.agent_name,
            fields=upgraded_fields,
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
    recipients: list[str] = []
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
                        recipients = _member_names(cfg["members"])
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

    manifest_deleted = delete_manifest(team_dir(settings.team_name), settings.agent_name)
    if removed:
        event = CapabilityManifestUpdatedOut(
            agent_name=settings.agent_name,
            capability_version="0",
            manifest_path=str(team_dir(settings.team_name) / "manifests" / f"{settings.agent_name}.json"),
            removed=True,
        )
        delivered = broadcast_manifest_event(
            team_dir(settings.team_name),
            sender=settings.agent_name,
            recipients=recipients,
            event=event,
        )
        logger.info(
            "capability_manifest.removed",
            team=settings.team_name,
            name=settings.agent_name,
            manifest_deleted=manifest_deleted,
            recipients=delivered,
        )

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
