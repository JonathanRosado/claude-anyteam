"""Unit tests for ``claude_anyteam.skills_fragment`` (v0.8.4).

These cover the C-side compose path in isolation. The integration with A's
MCP tools and the live cross-backend flow is locked by
``tests/test_cross_backend_skills_integration.py``.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from claude_anyteam import loop as codex_loop
from claude_anyteam.codex import CodexResult
from claude_anyteam.config import Settings
from claude_anyteam.skills_fragment import (
    compose_project_skills_fragment,
    compose_skills_fragment,
    task_text_for_skill_match,
)


REPO_ROOT = Path(__file__).resolve().parent.parent

SKILLS = [
    {
        "name": "diagnose",
        "description": "Read-only claude-anyteam substrate inspector for leads.",
        "when_to_use": "User asks to diagnose claude-anyteam substrate state or manifest cache freshness.",
        "path": "skills/diagnose/SKILL.md",
    },
    {
        "name": "help",
        "description": "Help creating, observing, or managing Agent Teams teammates.",
        "when_to_use": "User asks to create, route, configure, or troubleshoot teammates.",
        "path": "skills/help/SKILL.md",
    },
]


def _task(subject: str = "Diagnose", description: str = "diagnose the team"):
    return SimpleNamespace(
        id="skill-task",
        subject=subject,
        description=description,
        owner="codex-poc-3",
        status="pending",
        blocked_by=[],
    )


def _settings() -> Settings:
    return Settings(
        team_name="protocol-skills-research-2026-05",
        agent_name="codex-poc-3",
        cwd=REPO_ROOT,
        poll_interval_s=0.01,
        color="cyan",
        plan_mode_required=False,
        codex_binary="codex",
        app_server=True,
    )


def _success_result() -> CodexResult:
    return CodexResult(
        exit_code=0,
        structured={"files_changed": [], "summary": "done"},
        last_message='{"files_changed": [], "summary": "done"}',
        events=[],
        error=None,
        session_id="session-1",
    )


def test_compose_skills_fragment_matches_diagnose_metadata() -> None:
    fragment = compose_skills_fragment("diagnose the team", SKILLS)

    assert fragment is not None
    # v0.8.4 fragment uses the named header so peers can audit-log injection.
    assert fragment.startswith("## Available Claude Code skills\n")
    # The diagnose entry should be matched and the explicit invoke call
    # instruction must appear so the LLM has a concrete next step.
    assert "- /diagnose: Read-only claude-anyteam substrate inspector for leads." in fragment
    assert (
        "Use when: User asks to diagnose claude-anyteam substrate state"
        in fragment
    )
    assert "mcp_anyteam_invoke_skill('diagnose')" in fragment
    # /help should NOT match the narrow "diagnose the team" task.
    assert "/help" not in fragment


def test_compose_skills_fragment_returns_none_for_unrelated_task() -> None:
    assert compose_skills_fragment("write a haiku", SKILLS) is None


def test_compose_skills_fragment_accepts_source_path_shape() -> None:
    fragment = compose_skills_fragment(
        "inspect manifest cache health",
        [
            {
                "name": "diagnose",
                "description": "Read-only substrate inspector.",
                "when_to_use": "Use for manifest cache health and visibility-degraded diagnostics.",
                "source_path": "/repo/skills/diagnose/SKILL.md",
            }
        ],
    )

    assert fragment is not None
    assert "- /diagnose: Read-only substrate inspector." in fragment
    assert "Use when: Use for manifest cache health and visibility-degraded diagnostics." in fragment
    # The fragment now references the source path as the body's location AND
    # tells the LLM how to fetch via the MCP tool. Both should appear.
    assert "/repo/skills/diagnose/SKILL.md" in fragment
    assert "mcp_anyteam_invoke_skill('diagnose')" in fragment


def test_compose_project_skills_fragment_uses_shared_discovery() -> None:
    """v0.8.4: C uses A's shared `skill_discovery` cache (one skill universe).

    Previously C scanned ``<cwd>/skills/`` only, missing marketplace skills
    that A's wrapper-MCP tools could see. They now share the same discovery
    so cross-backend skill access is consistent across both surfaces.
    """
    fragment = compose_project_skills_fragment("diagnose the team substrate")

    # The in-repo `diagnose` skill must be discoverable from the shared cache
    # regardless of `cwd` (the parameter is now legacy / preserved for
    # backward signature compatibility).
    assert fragment is not None
    assert "/diagnose" in fragment


def test_codex_task_dispatch_injects_matching_skill_fragment() -> None:
    state = codex_loop.LoopState(settings=_settings(), peer_manifest_cache=None)
    prompts_seen: list[str] = []

    with patch.object(
        codex_loop,
        "_execute_task_app_server",
        side_effect=lambda _state, _task, prompt: prompts_seen.append(prompt)
        or _success_result(),
    ):
        result = codex_loop._invoke_codex_for_task(state, _task())

    assert result is not None and result.exit_code == 0
    assert prompts_seen
    # The dispatched prompt must contain the named fragment header AND the
    # explicit invoke instruction. Both are required for the live A+C flow
    # to work end-to-end.
    assert "## Available Claude Code skills" in prompts_seen[0]
    assert "/diagnose" in prompts_seen[0]
    assert "mcp_anyteam_invoke_skill" in prompts_seen[0]


def test_task_text_for_skill_match_handles_strings_and_objects() -> None:
    """The matcher accepts both plain strings (prose path) and task objects."""
    assert task_text_for_skill_match("diagnose the team") == "diagnose the team"

    text = task_text_for_skill_match(_task(subject="Cold email", description="growth"))
    assert "Cold email" in text
    assert "growth" in text

    assert task_text_for_skill_match(None) == ""
    assert task_text_for_skill_match("") == ""


def test_compose_skills_fragment_does_not_bonus_bare_stopword_name() -> None:
    """Bare prose like "I need help…" must not bonus a ``help``-named skill.

    Regression: PR #47 review v1 noted that ``_skill_name_matches`` bypassed
    the stopword filter, so generic ``help``-bearing prose (e.g.
    "I need help writing the hero section copy…") spuriously matched
    ``/help`` as the strongest skill mention even though copywriting was
    the right domain match. Compound names ("cold-email", "marketing-ideas")
    must still match.
    """

    skills = [
        {
            "name": "help",
            "description": "Help with claude-anyteam teammates.",
            "when_to_use": "User asks to create or troubleshoot teammates.",
            "source_path": "skills/help/SKILL.md",
        },
        {
            "name": "copywriting",
            "description": "Landing-page copy and conversion playbook.",
            "when_to_use": "User wants hero section / headline / body copy rewrites.",
            "source_path": "skills/copywriting/SKILL.md",
        },
    ]

    fragment = compose_skills_fragment(
        "I need help writing the hero section copy for my SaaS landing page",
        skills,
    )

    assert fragment is not None
    # /copywriting should win over /help — not by tie-break luck but because
    # the bare "help" token in stopwords no longer bonuses /help.
    matched = [
        line.split(":", 1)[0].strip()
        for line in fragment.splitlines()
        if line.startswith("- /")
    ]
    assert matched, "expected at least one skill match"
    assert matched[0] == "- /copywriting", (
        f"expected /copywriting at the top of matches, got: {matched}"
    )


def test_compose_skills_fragment_honors_explicit_slash_help() -> None:
    """Explicit ``/help`` slash mention must still match the help skill.

    The stopword guard only suppresses bare-word mentions. Slash-prefixed
    forms are deliberate by the user and should still be honored.
    """

    skills = [
        {
            "name": "help",
            "description": "Help with claude-anyteam teammates.",
            "when_to_use": "User asks to create or troubleshoot teammates.",
            "source_path": "skills/help/SKILL.md",
        },
    ]

    fragment = compose_skills_fragment("Run /help on the team substrate", skills)
    assert fragment is not None
    assert "/help" in fragment


def test_compose_skills_fragment_caps_matches_at_three_with_controlled_fixture() -> None:
    """With 5 equally-matching skills, exactly 3 must appear (not the live count).

    Regression: PR #47 review v1 noted that the existing
    ``test_fragment_caps_at_three_matches`` was weak because it asserted
    ``<= 3`` against the live marketplace, which can also legitimately yield
    fewer than 3 matches. This controlled-fixture variant proves the cap
    actually fires when raw matches > 3.
    """

    raw_matches = [
        {
            "name": f"playbook-{token}",
            "description": f"Frobnicate {token} pipelines for marketing growth.",
            "when_to_use": f"User wants frobnication on {token} marketing growth.",
            "source_path": f"skills/playbook-{token}/SKILL.md",
        }
        for token in ("alpha", "bravo", "charlie", "delta", "echo")
    ]

    fragment = compose_skills_fragment(
        "frobnicate marketing growth pipelines",
        raw_matches,
    )

    assert fragment is not None
    matched = [
        line for line in fragment.splitlines() if line.startswith("- /playbook-")
    ]
    assert len(matched) == 3, (
        f"expected exactly 3 matches under the cap, got {len(matched)}: {matched}"
    )
