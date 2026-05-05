"""Tests for the per-process skill-discovery cache (§3 peer efficiency).

A's wrapper-MCP tools and C's prompt-fragment composer both go through
``claude_anyteam.skill_discovery.discover_skills``. The default-args call
must be memoised so neither path pays a per-invocation disk scan and the
two surfaces share a single host-level snapshot.

Regression context: the v0.8.4 PR claimed startup-only discovery, but C
originally rescanned disk every prompt composition. The cache lock here
prevents that drift from coming back.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_anyteam import skill_discovery
from claude_anyteam.skill_discovery import discover_skills


def test_default_args_call_returns_cached_snapshot() -> None:
    """Two default-args calls must return the SAME dict object (cache hit)."""
    snap1 = discover_skills(refresh=True)
    snap2 = discover_skills()
    assert snap2 is snap1, (
        "default-args discover_skills must reuse the cached snapshot; "
        "rescanning per call regresses §3 peer efficiency"
    )


def test_refresh_rediscovers_and_replaces_cache() -> None:
    """``refresh=True`` must produce a fresh dict and update the shared cache."""
    snap1 = discover_skills(refresh=True)
    snap2 = discover_skills(refresh=True)
    assert snap2 is not snap1, "refresh must rediscover, not reuse the prior snapshot"
    # Subsequent default-args call must follow the refreshed cache.
    snap3 = discover_skills()
    assert snap3 is snap2, "post-refresh default-args call must hit the new cache"


def test_custom_paths_bypass_cache(tmp_path: Path) -> None:
    """Custom roots must always rediscover and never touch the default cache."""
    # Prime the default cache.
    default_snap = discover_skills(refresh=True)

    empty_repo = tmp_path / "skills"
    empty_repo.mkdir()
    empty_market = tmp_path / "marketplaces"
    empty_market.mkdir()

    custom_snap = discover_skills(
        repo_skills_dir=empty_repo,
        marketplace_root=empty_market,
    )
    assert custom_snap == {}, "empty fixture roots must yield no skills"
    assert custom_snap is not default_snap

    # Default cache must be unchanged by the custom-args call.
    again = discover_skills()
    assert again is default_snap, (
        "custom-path discovery must not poison the default-args cache"
    )


def test_cache_populated_after_first_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """Module-level ``_DEFAULT_CACHE`` must be set after a default-args call."""
    monkeypatch.setattr(skill_discovery, "_DEFAULT_CACHE", None, raising=False)
    assert skill_discovery._DEFAULT_CACHE is None
    snap = discover_skills()
    assert skill_discovery._DEFAULT_CACHE is snap
    # Reset for downstream tests so they're not affected.
    discover_skills(refresh=True)
