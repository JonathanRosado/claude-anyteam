"""Backend-neutral schema validation helpers.

Some backends cannot enforce our JSON schemas at the CLI layer (for example,
Codex resume mode and Gemini headless mode). The adapter recovers rigor in
Python: parse the subprocess final text as JSON, validate against the shared
team protocol schema, and let the caller retry with a firmer prompt before
escalating to `task_blocked`.

This module is deliberately narrow: one function per concern, no mutable
module state, and no side effects beyond raising for programmer errors.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema


def load_schema(path: Path) -> dict[str, Any]:
    """Read and parse a JSON Schema file. Raises the usual FileNotFoundError
    / JSONDecodeError — callers should treat those as programmer errors."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def parse_and_validate(text: str, schema: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """Parse `text` as JSON and validate against `schema`.

    Returns `(parsed, None)` on success. Returns `(None, reason)` on any
    failure — the reason string is suitable for logging and for inclusion
    in a retry prompt. Does not raise (callers already have enough
    decision points).

    Accepts slightly-dirty JSON: strips surrounding whitespace and trims
    one trailing newline before parsing. Does NOT try to unwrap markdown
    fences; that's deliberately firm — the prompt tells the backend not to
    emit them, and silent unwrapping would mask a prompt-discipline
    failure we'd want to see.
    """
    if not text or not text.strip():
        return None, "empty output-last-message"

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        head = text[:120].replace("\n", " ")
        return None, f"output was not valid JSON ({e.msg}); first 120 chars: {head!r}"

    if not isinstance(parsed, dict):
        return None, f"output was valid JSON but not an object: {type(parsed).__name__}"

    try:
        jsonschema.validate(instance=parsed, schema=schema)
    except jsonschema.ValidationError as e:
        return None, f"schema validation failed: {e.message} (at path: {list(e.absolute_path)})"

    return parsed, None


def inline_schema_prompt_fragment(schema: dict[str, Any]) -> str:
    """Render the inline schema preamble for backend prompts.

    Backends without a CLI-level schema flag receive the schema in the prompt
    itself. This helper keeps the exact wording in one place so stricter
    retry prompts can reuse the same schema rendering.
    """
    compact = json.dumps(schema, separators=(",", ":"))
    return (
        "Your final response MUST be a single JSON object matching this schema:\n"
        f"  {compact}\n"
        "Return ONLY the JSON object. Do not wrap in markdown. Do not include "
        "prose before or after. Do not emit commentary after the JSON."
    )
