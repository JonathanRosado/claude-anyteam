"""Cross-backend skills integration test (PoC A + PoC C, v0.8.4).

Locks the empirical contract validated against live codex teammates in the
``marketing-skill-test`` team:

1. The shared ``skill_discovery`` cache picks up both in-repo skills and
   marketplace skills under ``~/.claude/plugins/marketplaces/*/skills/``.
2. The C-side ``compose_project_skills_fragment`` heuristic correctly maps
   diverse domain tasks (cold email, SEO, copywriting, marketing ideas) so
   that the right skill name appears in the top-3 matches the receiving
   teammate sees. The teammate's LLM picks among those top-3 candidates;
   the heuristic's job is "right candidate present", not "uniquely #1".
3. The fragment shape includes the explicit
   ``mcp_anyteam_invoke_skill('<name>')`` invitation so the receiving LLM
   has a concrete next step.
4. The A-side fragment text instructs the LLM to call the MCP tool, NOT to
   inline-read the path. (PoC C does NOT inline SKILL.md bodies — §1-safe.)

The integration of A's MCP tools with C's fragment was validated end-to-end
against live codex-app-server teammates; this test locks the unit-level
invariants that make the integration work. A regression here means the live
seamless-inheritance behavior will break.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Iterable

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent

# Ensure src/ is importable in the test runner's path (tests run from repo root
# with editable install, but be explicit so the test is robust to alternate
# invocation patterns).
sys.path.insert(0, str(REPO_ROOT / "src"))

from claude_anyteam.skill_discovery import discover_skills  # noqa: E402
from claude_anyteam.skills_fragment import (  # noqa: E402
    compose_project_skills_fragment,
    compose_skills_fragment,
    task_text_for_skill_match,
)


# Tasks the v0.8.4 empirical test validated against live codex-app-server
# teammates. Each task is paired with the skill the codex teammate's
# ``mcp_anyteam_invoke_skill`` call actually selected during the live test.
LIVE_TEST_PAIRS: tuple[tuple[str, str], ...] = (
    (
        "I'm the founder of Sentinel, an open-source observability platform "
        "(Prometheus + Loki + Tempo unified, self-hostable). We have product-"
        "market fit but our top-of-funnel acquisition has been flat for three "
        "months. What marketing ideas should I try?",
        "marketing-ideas",
    ),
    (
        "Write me a short cold outreach email I can send to a head of platform "
        "engineering at a Series B fintech. We sell an open-source "
        "observability platform called Sentinel. They just announced a $40M "
        "Series B and are doubling their engineering team.",
        "cold-email",
    ),
    (
        "My SaaS product page isn't ranking on Google. The page targets "
        "\"open source observability platform\" but we're stuck on page 3. "
        "Can you do an SEO audit and tell me what's wrong?",
        "seo",
    ),
    (
        "I need help writing the hero section copy for my SaaS landing page. "
        "The product is Sentinel — an open-source observability platform. "
        "My current headline is \"Modern observability for modern teams\" "
        "and conversion is awful. Rewrite it.",
        "copywriting",
    ),
)


def _names_in_fragment(fragment: str | None) -> list[str]:
    if not fragment:
        return []
    return re.findall(r"^- /(\S+):", fragment, re.MULTILINE)


def _has_marketplace_skills() -> bool:
    """True iff the test host has marketing-skills installed via the marketplace.

    The integration test depends on real marketplace skills being on disk —
    skip cleanly on hosts that haven't installed them, since the contract
    we're locking is the production-shape behavior, not a stub.
    """
    cache = discover_skills()
    return all(name in cache for name in ("cold-email", "marketing-ideas", "copywriting", "seo"))


pytestmark = pytest.mark.skipif(
    not _has_marketplace_skills(),
    reason="marketplace skills (marketing-ideas, cold-email, etc.) not installed on this host",
)


def test_skill_discovery_includes_in_repo_and_marketplace() -> None:
    """A's discovery cache must surface BOTH in-repo and marketplace skills."""
    cache = discover_skills()

    # In-repo skills (claude-anyteam plugin).
    assert "help" in cache, "in-repo claude-anyteam:help skill missing from cache"
    assert "diagnose" in cache, "in-repo claude-anyteam:diagnose skill missing from cache"

    # Marketplace skills (e.g., marketing-skills plugin).
    assert "marketing-ideas" in cache, "marketplace marketing-ideas skill missing"
    assert "cold-email" in cache, "marketplace cold-email skill missing"

    # Each cached entry has the body+source_path needed by the invoke MCP tool.
    for name in ("help", "marketing-ideas", "cold-email"):
        record = cache[name]
        assert record["name"] == name
        assert record.get("source_path"), f"missing source_path for {name}"
        assert record.get("body"), f"missing body for {name}"


@pytest.mark.parametrize("task_text,expected_skill", LIVE_TEST_PAIRS)
def test_compose_fragment_maps_live_test_tasks_to_correct_skill(
    task_text: str, expected_skill: str
) -> None:
    """C's heuristic must rank the live-test skill in the top-3 matches.

    The live integration run had codex teammates invoke the listed skill for
    the listed task. The fragment is what told them which skill to call, so
    the mapping must hold at the unit level too.
    """
    fragment = compose_project_skills_fragment(task_text)
    assert fragment is not None, (
        f"no skills matched task — heuristic regression for task: {task_text[:80]}"
    )
    matched_names = _names_in_fragment(fragment)
    assert expected_skill in matched_names, (
        f"expected skill '{expected_skill}' not in matched names {matched_names} "
        f"for task: {task_text[:80]}"
    )


def test_fragment_includes_explicit_invoke_skill_instruction() -> None:
    """The fragment must instruct the LLM to call mcp_anyteam_invoke_skill.

    This is the load-bearing line that makes A+C work together: without the
    explicit call instruction, the LLM sees skill metadata but doesn't know
    the MCP tool is how to fetch the body.
    """
    fragment = compose_project_skills_fragment(
        "Write me a cold outreach email for our SaaS"
    )
    assert fragment is not None
    # The fragment must reference the MCP tool name AND name the matched skill.
    assert "mcp_anyteam_invoke_skill" in fragment, (
        "fragment missing the explicit invoke_skill call instruction; "
        "without it, codex teammates see metadata but don't know to call the "
        "fetch tool"
    )
    assert "## Available Claude Code skills" in fragment


def test_fragment_does_not_inline_skill_md_bodies() -> None:
    """Bodies must NOT be inlined in the fragment — §1-safe metadata only.

    Inlining bodies introduces prompt-injection risk and bloat. The whole
    point of A's read_skill MCP tool is on-demand body fetch; C must not
    short-circuit that by inlining.
    """
    fragment = compose_project_skills_fragment(
        "Write me a cold outreach email for our SaaS"
    )
    assert fragment is not None
    cache = discover_skills()
    cold_email_body = cache["cold-email"]["body"]
    # Pick a body-only sentence that doesn't appear in metadata.
    body_only_marker = "Cold email is ruthlessly short."
    assert body_only_marker in cold_email_body, "test fixture: body marker missing"
    assert body_only_marker not in fragment, (
        "fragment includes raw SKILL.md body content — §1 metadata-only "
        "boundary violated"
    )


def test_fragment_caps_at_three_matches() -> None:
    """Heuristic must cap inlined matches at 3 to keep prompt overhead bounded."""
    # A broad task that legitimately matches many skills.
    fragment = compose_project_skills_fragment(
        "I need marketing copy and SEO and email outreach and growth ideas"
    )
    assert fragment is not None
    names = _names_in_fragment(fragment)
    assert 1 <= len(names) <= 3, (
        f"fragment match count {len(names)} outside expected [1, 3] cap; "
        f"unbounded growth would inflate every task prompt"
    )


def test_compose_fragment_returns_none_when_nothing_matches() -> None:
    """Tasks with no domain overlap should produce no fragment at all."""
    # Pure protocol-level task — no domain words that overlap with skills.
    fragment = compose_project_skills_fragment(
        "echo hello world to stdout and exit"
    )
    # `echo` etc. shouldn't trigger any skills; if they do, heuristic too loose.
    if fragment is not None:
        names = _names_in_fragment(fragment)
        assert not names, (
            f"unexpected matches for protocol-level task: {names} — "
            f"heuristic too generous"
        )


def test_task_text_for_skill_match_handles_strings_and_objects() -> None:
    """The matcher must accept plain strings (prose path) AND task objects."""
    # Plain string (the prose / first-turn path).
    assert task_text_for_skill_match("write me a cold email") == "write me a cold email"

    # Task-shaped object (task-dispatch path).
    class _StubTask:
        subject = "growth ideas"
        description = "marketing brainstorm"

    text = task_text_for_skill_match(_StubTask())
    assert "growth ideas" in text
    assert "marketing brainstorm" in text

    # None / empty — must not crash.
    assert task_text_for_skill_match(None) == ""
    assert task_text_for_skill_match("") == ""


def test_compose_skills_fragment_with_empty_skills_returns_none() -> None:
    """Defensive: if the skill universe is empty, fragment is None (no header)."""
    fragment = compose_skills_fragment("write a cold email", [])
    assert fragment is None
