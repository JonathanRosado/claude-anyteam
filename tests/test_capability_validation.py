from __future__ import annotations

import ast
import importlib
import json
from importlib import resources
from pathlib import Path
from typing import Any

import pytest
from jsonschema import ValidationError, validate

from claude_anyteam import capabilities as cap_mod
from claude_anyteam.capabilities import (
    CAPABILITY_HOOKS,
    CAPABILITY_NAMES,
    CODEX_APP_SERVER_CAPABILITIES,
    CODEX_EXEC_CAPABILITIES,
    GEMINI_ACP_CAPABILITIES,
    GEMINI_HEADLESS_CAPABILITIES,
    KIMI_HEADLESS_CAPABILITIES,
    build_agent_card,
    validate_capability_manifest_entries,
)


ROOT = Path(__file__).resolve().parents[1]


def _schema() -> dict[str, Any]:
    return json.loads(
        resources.files("claude_anyteam.schemas")
        .joinpath("capability_manifest.schema.json")
        .read_text(encoding="utf-8")
    )


def _declared_backend_capabilities() -> set[str]:
    declared: set[str] = set()
    for flags in (
        CODEX_APP_SERVER_CAPABILITIES,
        CODEX_EXEC_CAPABILITIES,
        GEMINI_ACP_CAPABILITIES,
        GEMINI_HEADLESS_CAPABILITIES,
        KIMI_HEADLESS_CAPABILITIES,
    ):
        declared.update(flags)
    return declared


def _resolve_runtime_ref(ref: str) -> object:
    module_name, _, qualname = ref.partition(":")
    assert module_name and qualname, f"runtime hook ref must be module:qualname: {ref!r}"
    obj: object = importlib.import_module(module_name)
    for part in qualname.split("."):
        obj = getattr(obj, part)
    return obj


def _test_function_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def test_every_declared_backend_capability_has_runtime_hook_and_test() -> None:
    missing = _declared_backend_capabilities() - set(CAPABILITY_HOOKS)
    assert not missing, f"declared capabilities without hook registry entries: {sorted(missing)}"

    for name in sorted(_declared_backend_capabilities()):
        hook = CAPABILITY_HOOKS[name]
        assert hook.runtime_paths, f"{name} has no runtime hook path"
        assert hook.test_paths, f"{name} has no regression-test path"
        assert hook.note.strip(), f"{name} should explain what the hook proves"


def test_capability_registry_is_exact_and_refs_resolve() -> None:
    assert set(CAPABILITY_HOOKS) == set(CAPABILITY_NAMES)

    for capability, hook in CAPABILITY_HOOKS.items():
        for runtime_ref in hook.runtime_paths:
            assert _resolve_runtime_ref(runtime_ref) is not None, (capability, runtime_ref)
        for node_id in hook.test_paths:
            path_part, sep, test_name = node_id.partition("::")
            assert sep, f"test hook ref must include ::test_name: {node_id!r}"
            path = ROOT / path_part
            assert path.exists(), f"{capability} test path does not exist: {path_part}"
            assert test_name in _test_function_names(path), (
                f"{capability} test function {test_name!r} missing from {path_part}"
            )


def test_manifest_entry_validation_rejects_unregistered_or_unwired_flags(monkeypatch) -> None:
    good_entry = {
        "version": "1",
        "schema": {"type": "object"},
        "description": "Synthetic capability",
        "when_to_use": "Only in this test.",
        "when_not_to": "Outside this test.",
        "failure_modes": ["SYNTHETIC_FAILURE"],
        "callable_from_peers": False,
    }

    with pytest.raises(ValueError, match="unknown capability"):
        validate_capability_manifest_entries({"made_up": good_entry})

    monkeypatch.setattr(cap_mod, "CAPABILITY_NAMES", frozenset({"synthetic"}))
    monkeypatch.setattr(cap_mod, "CAPABILITY_RUNTIME_REGISTRY", {})
    with pytest.raises(ValueError, match="missing runtime hook/test"):
        cap_mod.assert_known_capabilities(["synthetic"])


def test_manifest_entry_validation_requires_peer_safety_fields() -> None:
    with pytest.raises(ValueError, match="callable_from_peers"):
        validate_capability_manifest_entries(
            {
                "structured_output": {
                    "version": "1",
                    "schema": {"type": "object"},
                    "description": "schema output",
                    "when_to_use": "always",
                    "when_not_to": "never",
                    "failure_modes": ["SCHEMA_VALIDATION_FAILED"],
                }
            }
        )

    with pytest.raises(ValueError, match="authorization"):
        validate_capability_manifest_entries(
            {
                "turn_steer": {
                    "version": "1",
                    "schema": {"type": "object"},
                    "description": "steer",
                    "when_to_use": "mid-turn correction",
                    "when_not_to": "low-information nudges",
                    "failure_modes": ["STEER_AUTH_REJECTED"],
                    "callable_from_peers": False,
                }
            }
        )


def test_strict_capability_manifest_schema_rejects_unknown_and_loose_entries() -> None:
    schema = _schema()
    card = build_agent_card(
        team_name="t",
        agent_name="codex-a",
        agent_id="codex-a@t",
        agent_type="claude-anyteam",
        model="codex-cli",
        backend_type="in-process",
        capabilities=["structured_output"],
        coupling_regime="loose",
    )
    validate(card, schema)

    unknown_cap = json.loads(json.dumps(card))
    unknown_cap["capabilities"]["decorative_flag"] = unknown_cap["capabilities"]["structured_output"]
    with pytest.raises(ValidationError):
        validate(unknown_cap, schema)

    missing_field = json.loads(json.dumps(card))
    del missing_field["capabilities"]["structured_output"]["callable_from_peers"]
    with pytest.raises(ValidationError):
        validate(missing_field, schema)

    extra_root = json.loads(json.dumps(card))
    extra_root["unbounded_context"] = "not allowed"
    with pytest.raises(ValidationError):
        validate(extra_root, schema)
