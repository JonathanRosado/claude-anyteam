from pathlib import Path
import jsonschema

from claude_anyteam.codex import SCHEMAS_DIR
from claude_anyteam.schema_validation import load_schema


def test_permission_request_schema_accepts_valid_and_rejects_malformed():
    schema = load_schema(SCHEMAS_DIR / "permission_request.schema.json")
    valid = {
        "type": "permission_request",
        "schema_version": 1,
        "request_id": "perm-1",
        "tool_name": "shell_command",
        "tool_args": {"cmd": "git status"},
        "task_id": "7",
        "teammate_name": "gemini-a",
        "trust_mode": "default",
        "label": "Run shell command",
        "timestamp": "2026-04-24T21:30:00.000Z",
    }
    jsonschema.validate(valid, schema)

    invalid = dict(valid)
    invalid.pop("request_id")
    try:
        jsonschema.validate(invalid, schema)
    except jsonschema.ValidationError:
        pass
    else:
        raise AssertionError("permission_request schema should reject missing request_id")

    invalid_decoration = dict(valid)
    invalid_decoration["unexpected"] = True
    try:
        jsonschema.validate(invalid_decoration, schema)
    except jsonschema.ValidationError:
        pass
    else:
        raise AssertionError("permission_request schema should reject additional properties")


def test_permission_response_schema_accepts_valid_and_rejects_malformed():
    schema = load_schema(SCHEMAS_DIR / "permission_response.schema.json")
    valid = {
        "type": "permission_response",
        "schema_version": 1,
        "request_id": "perm-1",
        "decision": "allow_once",
        "reason": "Looks safe",
        "timestamp": "2026-04-24T21:31:00.000Z",
    }
    jsonschema.validate(valid, schema)

    invalid = dict(valid)
    invalid["decision"] = "approve"
    try:
        jsonschema.validate(invalid, schema)
    except jsonschema.ValidationError:
        pass
    else:
        raise AssertionError("permission_response schema should reject unknown decisions")

    missing = dict(valid)
    missing.pop("decision")
    try:
        jsonschema.validate(missing, schema)
    except jsonschema.ValidationError:
        pass
    else:
        raise AssertionError("permission_response schema should reject missing decision")
