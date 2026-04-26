"""Unit tests for v7.2's Python-side schema validation.

`codex exec resume` on codex-cli 0.122.0 doesn't accept `--output-schema`.
v7.2 recovers the rigor by validating the `--output-last-message` file
against `schemas/task-complete.schema.json` in Python. These tests
exercise the validator for success, each relevant failure mode, and
the inline-schema prompt helper.
"""

from __future__ import annotations

from pathlib import Path

from claude_anyteam.schema_validation import (
    inline_schema_prompt_fragment,
    load_schema,
    parse_and_validate,
)


def _task_complete_schema() -> dict:
    from claude_anyteam.codex import TASK_COMPLETE_SCHEMA
    return load_schema(TASK_COMPLETE_SCHEMA)


def test_valid_output_parses_and_returns_dict():
    schema = _task_complete_schema()
    text = '{"files_changed": ["a.py"], "summary": "did X"}'
    parsed, err = parse_and_validate(text, schema)
    assert err is None
    assert parsed == {"files_changed": ["a.py"], "summary": "did X"}


def test_empty_output_is_rejected():
    schema = _task_complete_schema()
    parsed, err = parse_and_validate("", schema)
    assert parsed is None
    assert err is not None
    assert "empty" in err.lower()


def test_whitespace_only_output_is_rejected():
    schema = _task_complete_schema()
    parsed, err = parse_and_validate("   \n\n   ", schema)
    assert parsed is None
    assert err is not None
    assert "empty" in err.lower()


def test_non_json_output_is_rejected_with_head():
    schema = _task_complete_schema()
    parsed, err = parse_and_validate("not json at all", schema)
    assert parsed is None
    assert err is not None
    assert "not valid JSON" in err
    # The error message includes a head of the text so the retry prompt
    # can cite it back to Codex.
    assert "not json at all" in err


def test_non_object_json_is_rejected():
    """The task-complete schema expects an object; a JSON array or scalar
    must fail even though the JSON itself parses."""
    schema = _task_complete_schema()
    for text in ("[]", '"just a string"', "42", "true"):
        parsed, err = parse_and_validate(text, schema)
        assert parsed is None, f"{text!r} should have been rejected"
        assert err is not None


def test_missing_required_field_rejected():
    schema = _task_complete_schema()
    text = '{"summary": "did X"}'  # missing files_changed
    parsed, err = parse_and_validate(text, schema)
    assert parsed is None
    assert err is not None
    assert "schema validation failed" in err


def test_empty_summary_rejected_due_to_minLength():
    schema = _task_complete_schema()
    text = '{"files_changed": [], "summary": ""}'
    parsed, err = parse_and_validate(text, schema)
    assert parsed is None
    assert err is not None
    assert "schema validation failed" in err


def test_additional_fields_rejected():
    """task-complete schema has additionalProperties: false, so an
    extra field must fail."""
    schema = _task_complete_schema()
    text = '{"files_changed": [], "summary": "x", "extra": 1}'
    parsed, err = parse_and_validate(text, schema)
    assert parsed is None
    assert err is not None


def test_surrounding_whitespace_is_tolerated():
    """Output from --output-last-message may have a trailing newline; that
    should not cause a validation failure."""
    schema = _task_complete_schema()
    text = '\n{"files_changed": [], "summary": "ok"}\n\n'
    parsed, err = parse_and_validate(text, schema)
    assert err is None
    assert parsed == {"files_changed": [], "summary": "ok"}


def test_markdown_fences_are_NOT_unwrapped():
    """Deliberately firm contract: if Codex wraps JSON in ```json fences,
    that's a prompt-discipline failure we want to surface, not silently
    fix. The retry prompt then gets stricter."""
    schema = _task_complete_schema()
    text = '```json\n{"files_changed": [], "summary": "x"}\n```'
    parsed, err = parse_and_validate(text, schema)
    assert parsed is None
    assert err is not None


def test_inline_schema_prompt_fragment_is_strict():
    schema = _task_complete_schema()
    frag = inline_schema_prompt_fragment(schema)
    # Must mention the schema (exact compact JSON).
    assert '"files_changed"' in frag
    assert '"summary"' in frag
    # Must make the no-markdown, no-prose instruction explicit so Codex
    # doesn't produce the exact shape we reject in the test above.
    assert "Do not wrap in markdown" in frag
    assert "ONLY the JSON object" in frag


def test_load_schema_reads_task_complete_schema():
    """Sanity check that the schema we rely on actually exists on disk
    and is a proper JSON Schema object."""
    schema = _task_complete_schema()
    assert schema.get("type") == "object"
    assert "files_changed" in schema.get("properties", {})
    assert "summary" in schema.get("properties", {})
    assert set(schema.get("required", [])) >= {"files_changed", "summary"}
