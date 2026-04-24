"""Smoke tests for plugin skill content.

Skills are content, not code — these tests only check that each SKILL.md
parses as valid YAML frontmatter plus a non-empty body, and that the
required frontmatter fields (name, description) are present.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

try:
    import yaml  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover — environment without PyYAML
    yaml = None  # type: ignore[assignment]


REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / "skills"

FRONTMATTER_PATTERN = re.compile(r"\A---\s*\n(?P<front>.*?)\n---\s*\n(?P<body>.*)", re.DOTALL)


def _skill_files() -> list[Path]:
    return sorted(SKILLS_DIR.glob("*/SKILL.md"))


@pytest.mark.parametrize("path", _skill_files(), ids=lambda p: p.parent.name)
def test_skill_has_valid_frontmatter_and_body(path: Path) -> None:
    if yaml is None:
        pytest.skip("PyYAML not available in this environment")
    text = path.read_text(encoding="utf-8")
    match = FRONTMATTER_PATTERN.match(text)
    assert match is not None, f"{path} missing `---` frontmatter delimiters"

    parsed = yaml.safe_load(match.group("front"))
    assert isinstance(parsed, dict), f"{path} frontmatter is not a mapping"

    name = parsed.get("name")
    description = parsed.get("description")
    assert isinstance(name, str) and name.strip(), f"{path} missing non-empty `name`"
    assert isinstance(description, str) and description.strip(), (
        f"{path} missing non-empty `description`"
    )

    body = match.group("body").strip()
    assert body, f"{path} has an empty body after frontmatter"


def test_help_skill_documents_backend_routing() -> None:
    """The help skill is what teaches Claude Code to name CLI-backed teammates.
    Regression guard: the routing convention and the tool-call shape must
    appear in the body so a model reading the skill can act on them.
    """
    path = SKILLS_DIR / "help" / "SKILL.md"
    body = path.read_text(encoding="utf-8")
    assert "^codex-" in body, "help skill must state the codex- name regex"
    assert "^gemini-" in body, "help skill must state the gemini- name regex"
    assert "TeamCreate" in body, "help skill must show the TeamCreate tool call"
    assert "Agent(" in body, "help skill must show the Agent tool call"
    assert "team_name" in body, "help skill must name the team_name parameter"
