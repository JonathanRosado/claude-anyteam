"""Compose Claude Code skill metadata into routed-backend prompt fragments.

Native Claude Code can discover and load ``skills/<name>/SKILL.md`` by itself.
Routed non-Claude backends cannot, so this module is the §1-safe bridge: inject
only skill metadata into the peer/task prompt so the receiving model can choose
whether to read or invoke the skill body. The wrapper never interprets or
rewrites the skill body here.

PoC C uses the SAME skill cache as PoC A's wrapper-MCP tools (single source of
truth). The fragment includes an explicit instruction to call
``mcp_anyteam_invoke_skill('<name>')`` to fetch the body — without that nudge,
non-Claude teammates see metadata but don't know they should fetch.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping
import re

from .skill_discovery import discover_skills

FRAGMENT_TITLE = "## Available Claude Code skills"

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Stopwords filter cheap routing/protocol prose that would otherwise produce
# false positives. We deliberately keep DOMAIN words (marketing, observability,
# email, growth, copy, etc.) so domain-specific tasks match domain skills.
_STOPWORDS = frozenset(
    {
        "about", "after", "agent", "agents", "also", "and", "any", "are",
        "ask", "asks", "available", "based", "because", "been", "being",
        "body", "but", "can", "code", "concrete", "create", "creating",
        "current", "did", "does", "doing", "done", "for", "from", "get",
        "give", "going", "got", "has", "have", "having", "help", "her",
        "here", "him", "his", "how", "i'm", "into", "its", "just", "lead",
        "like", "make", "may", "might", "more", "most", "much", "must",
        "need", "needs", "new", "next", "non", "not", "now", "one", "only",
        "our", "ours", "out", "over", "please", "prose", "read", "really",
        "route", "say", "see", "seem", "seems", "should", "since", "some",
        "state", "stuck", "such", "task", "team", "teams", "teammate",
        "teammates", "than", "that", "the", "their", "them", "then",
        "there", "these", "they", "this", "those", "three", "through",
        "tool", "tools", "two", "use", "used", "user", "using", "very",
        "want", "wants", "was", "way", "ways", "week", "well", "were",
        "what", "when", "where", "which", "while", "who", "whom", "why",
        "will", "with", "work", "would", "write", "you", "your", "yours",
    }
)


def _field(skill: Mapping[str, Any] | Any, *names: str) -> str:
    for name in names:
        value: Any
        if isinstance(skill, Mapping):
            value = skill.get(name)
        else:
            value = getattr(skill, name, None)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in _TOKEN_RE.findall(text.lower())
        if len(token) >= 3 and token not in _STOPWORDS
    }


def _skill_name_matches(task_text: str, name: str) -> bool:
    """True if the skill name appears as a word/slash-command in the task text.

    Bare single-token stopword names (e.g. ``help``) only count as an explicit
    mention when the task text uses the slash-command form (``/help``).
    Otherwise generic prose like "I need help with…" would falsely score
    ``/help`` as the dominant match. Compound names (``cold-email``,
    ``marketing-ideas``) bypass the stopword check by definition.
    """

    normalized = name.strip().lstrip("/").lower()
    if not normalized:
        return False
    lower = task_text.lower()
    # Slash-prefixed mentions are deliberate; always honor them.
    if re.search(rf"(?<![a-z0-9])/{re.escape(normalized)}(?![a-z0-9])", lower):
        return True
    # Non-slash mentions: skip if the bare name is also a stopword (single
    # token, not compound). Compound names like "cold-email" pass through.
    if "-" not in normalized and "_" not in normalized and normalized in _STOPWORDS:
        return False
    return (
        re.search(rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])", lower)
        is not None
    )


def _score_skill(task_text: str, task_tokens: set[str], skill: Mapping[str, Any] | Any) -> int:
    name = _field(skill, "name")
    description = _field(skill, "description")
    when_to_use = _field(skill, "when_to_use")
    if not name:
        return 0

    score = 0
    if _skill_name_matches(task_text, name):
        score += 10  # explicit mention is the strongest signal

    name_tokens = _tokens(name.replace("-", " ").replace("_", " ").replace(":", " "))
    if task_tokens & name_tokens:
        score += 4  # name-token overlap

    metadata_tokens = _tokens(f"{description}\n{when_to_use}")
    score += len(task_tokens & metadata_tokens)
    return score


def _normalize_task_text(task: Any) -> str:
    """Coerce a task representation into a flat text blob for matching.

    Accepts either a plain string OR a task object with ``subject`` and
    ``description`` attrs (compatible with the original PoC C signature).
    """

    if task is None:
        return ""
    if isinstance(task, str):
        return task.strip()
    if isinstance(task, Mapping):
        parts = [str(task.get(k, "")).strip() for k in ("subject", "description")]
        return "\n".join(p for p in parts if p)
    parts = [
        str(getattr(task, "subject", "") or "").strip(),
        str(getattr(task, "description", "") or "").strip(),
    ]
    return "\n".join(p for p in parts if p)


def task_text_for_skill_match(task: Any) -> str:
    """Return a text blob suitable for skill-relevance scoring.

    Accepts plain strings, mappings, or task-shaped objects. Always returns a
    string (possibly empty).
    """

    return _normalize_task_text(task)


def compose_skills_fragment(
    task_text: Any,
    available_skills: Iterable[Mapping[str, Any] | Any],
    *,
    max_matches: int = 3,
    min_score: int = 2,
) -> str | None:
    """Return a prompt fragment describing skills relevant to ``task_text``.

    Fragment shape:

        ## Available Claude Code skills

        Three skills look relevant to this task. To follow one, call
        ``mcp_anyteam_invoke_skill('<name>')`` to fetch the body, then follow
        the instructions in the returned markdown.

        - /<name>: <description>
          Use when: <when_to_use>
          Skill body: call mcp_anyteam_invoke_skill('<name>') (source path: <path>)

    The §1 boundary is preserved: only metadata is inlined; the SKILL.md body
    is fetched on explicit tool call by the receiving teammate. The fragment
    includes the call instruction so the teammate's LLM has a concrete next
    step rather than a passive "skill exists" notice.
    """

    text = _normalize_task_text(task_text)
    skills = tuple(available_skills)
    if not text or not skills:
        return None

    task_tokens = _tokens(text)
    matches: list[tuple[int, str, Mapping[str, Any] | Any]] = []
    for skill in skills:
        name = _field(skill, "name")
        if not name:
            continue
        score = _score_skill(text, task_tokens, skill)
        if score >= min_score:
            matches.append((score, name.lower(), skill))

    if not matches:
        return None

    matches.sort(key=lambda item: (-item[0], item[1]))
    top = matches[:max_matches]

    if len(top) == 1:
        intro = (
            "One Claude Code skill looks relevant to this task. To follow it, "
            "call `mcp_anyteam_invoke_skill('<name>')` with the skill's name "
            "to fetch the markdown body, then follow the instructions in the "
            "returned text."
        )
    else:
        intro = (
            f"{len(top)} Claude Code skills look relevant to this task. To "
            f"follow any of them, call `mcp_anyteam_invoke_skill('<name>')` "
            f"with the skill's name to fetch its markdown body, then follow "
            f"the instructions in the returned text."
        )

    lines: list[str] = [FRAGMENT_TITLE, "", intro, ""]
    for _score, _sort_name, skill in top:
        name = _field(skill, "name")
        description = _field(skill, "description")
        when_to_use = _field(skill, "when_to_use")
        path = _field(skill, "path", "source_path", "file")
        lines.append(f"- /{name}: {description}")
        if when_to_use:
            lines.append(f"  Use when: {when_to_use}")
        lines.append(
            f"  Skill body: call `mcp_anyteam_invoke_skill('{name}')` (source: {path})"
        )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def compose_project_skills_fragment(
    task_text: Any,
    cwd: str | Path | None = None,
) -> str | None:
    """Compose a skill-fragment using the same cache as PoC A's wrapper tools.

    `cwd` is preserved in the signature for backward compatibility with the
    earlier PoC C wiring but is no longer used to scope discovery — both A and
    C share the global discovery roots (in-repo + marketplace). One skill
    universe per host.
    """

    cache = discover_skills()
    skills = sorted(cache.values(), key=lambda r: str(r.get("name") or ""))
    return compose_skills_fragment(task_text, skills)
