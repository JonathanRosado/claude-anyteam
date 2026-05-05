"""Claude Code skill discovery helpers shared by wrapper tools and manifests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import logger

# Per-process snapshot for the default-args ``discover_skills()`` path. Both
# A's wrapper-MCP tools and C's prompt-fragment composer reuse this so the
# host's skill universe is one consistent snapshot per process. New skills
# installed/uninstalled mid-process are not picked up until restart — that's
# the v1 staleness boundary.
_DEFAULT_CACHE: dict[str, dict[str, Any]] | None = None


def decode_bytes(data: bytes) -> tuple[str, str]:
    """Decode arbitrary file/HTTP bytes without raising on bad text."""
    for encoding in ("utf-8", "utf-16"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace"), "utf-8-replacement"


def repo_root() -> Path:
    """Return this checkout's root from the installed source layout."""

    return Path(__file__).resolve().parents[2]


def skill_frontmatter(body: str) -> dict[str, str]:
    """Parse the simple YAML-ish frontmatter used by Claude Code skills.

    The PoC only needs scalar fields (``name``, ``description``,
    ``when_to_use``). Avoid a YAML dependency and deliberately leave markdown
    body text uninterpreted: skill prose passes through verbatim to callers.
    """

    if not body.startswith("---"):
        return {}
    lines = body.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    end_index: int | None = None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_index = index
            break
    if end_index is None:
        return {}

    metadata: dict[str, str] = {}
    for line in lines[1:end_index]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if not key:
            continue
        metadata[key] = value.strip().strip("\"'")
    return metadata


def _marketplace_name(
    skill_file: Path,
    *,
    repo_skills_dir: Path,
    marketplace_root: Path,
) -> str:
    try:
        skill_file.relative_to(repo_skills_dir)
        return "claude-anyteam"
    except ValueError:
        pass

    try:
        relative = skill_file.relative_to(marketplace_root)
    except ValueError:
        return "unknown"
    if relative.parts:
        return relative.parts[0]
    return "unknown"


def discover_skills(
    *,
    repo_skills_dir: Path | None = None,
    marketplace_root: Path | None = None,
    refresh: bool = False,
) -> dict[str, dict[str, Any]]:
    """Discover Claude Code skills, cached per-process for the default-args path.

    Sources match the PoC-A wrapper tools:
    - in-repo ``skills/<name>/SKILL.md``
    - installed marketplace skills at
      ``~/.claude/plugins/marketplaces/<marketplace>/skills/<name>/SKILL.md``

    Skill names are the invoke key, so duplicates are kept deterministic:
    in-repo skills win over marketplace copies of the same skill name.

    The default-args call (no ``repo_skills_dir`` / ``marketplace_root``) is
    memoised in a process-wide snapshot. Both A's wrapper-MCP tools and C's
    fragment composer share that snapshot so the host's skill universe is
    consistent across the boundary, and neither path pays a per-call disk
    scan. Mid-process install / uninstall is not picked up until restart;
    pass ``refresh=True`` to force a rediscover. Custom paths always
    rediscover and never touch the default cache (test friendly).
    """

    global _DEFAULT_CACHE

    use_default_path = repo_skills_dir is None and marketplace_root is None
    if use_default_path and not refresh and _DEFAULT_CACHE is not None:
        return _DEFAULT_CACHE

    repo_skills_dir = (repo_skills_dir or (repo_root() / "skills")).expanduser().resolve()
    marketplace_root = (
        marketplace_root or (Path.home() / ".claude" / "plugins" / "marketplaces")
    ).expanduser().resolve()

    skill_files: list[Path] = []
    if repo_skills_dir.exists():
        skill_files.extend(sorted(repo_skills_dir.glob("*/SKILL.md")))
    if marketplace_root.exists():
        skill_files.extend(sorted(marketplace_root.glob("*/skills/*/SKILL.md")))

    discovered: dict[str, dict[str, Any]] = {}
    for skill_file in skill_files:
        try:
            skill_file = skill_file.resolve()
            body, _encoding = decode_bytes(skill_file.read_bytes())
        except OSError:
            logger.debug("skipping unreadable skill file: %s", skill_file)
            continue
        metadata = skill_frontmatter(body)
        skill_name = (metadata.get("name") or skill_file.parent.name).strip()
        if not skill_name:
            logger.debug("skipping skill with empty name: %s", skill_file)
            continue
        discovered.setdefault(
            skill_name,
            {
                "name": skill_name,
                "description": (metadata.get("description") or "").strip(),
                "when_to_use": (metadata.get("when_to_use") or "").strip(),
                "source_path": str(skill_file),
                "marketplace": _marketplace_name(
                    skill_file,
                    repo_skills_dir=repo_skills_dir,
                    marketplace_root=marketplace_root,
                ),
                "body": body,
            },
        )

    if use_default_path:
        _DEFAULT_CACHE = discovered
    return discovered
