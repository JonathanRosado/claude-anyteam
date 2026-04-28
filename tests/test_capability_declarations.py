from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

import pytest
from jsonschema import validate

from claude_anyteam import loop as codex_loop
from claude_anyteam.backends.gemini.config import GeminiSettings
from claude_anyteam.backends.gemini import loop as gemini_loop
from claude_anyteam.backends.kimi.config import KimiSettings
from claude_anyteam.backends.kimi import loop as kimi_loop
from claude_anyteam.capabilities import (
    CODEX_APP_SERVER_CAPABILITIES,
    CODEX_EXEC_CAPABILITIES,
    GEMINI_ACP_CAPABILITIES,
    GEMINI_HEADLESS_CAPABILITIES,
    KIMI_HEADLESS_CAPABILITIES,
    assert_known_capabilities,
    build_agent_card,
)
from claude_anyteam.config import Settings


def _codex_settings(tmp_path: Path, *, app_server: bool) -> Settings:
    return Settings(
        team_name="t",
        agent_name="codex-a",
        cwd=tmp_path,
        poll_interval_s=1.0,
        color="cyan",
        plan_mode_required=False,
        codex_binary="codex",
        app_server=app_server,
    )


def test_codex_app_server_backend_metadata_declares_expected_capabilities(tmp_path: Path):
    metadata = codex_loop._backend_metadata(_codex_settings(tmp_path, app_server=True))

    assert metadata.capabilities == CODEX_APP_SERVER_CAPABILITIES
    assert "accepts_peer_steer" not in metadata.capabilities
    assert "soft_non_progress_watchdog" in metadata.capabilities
    assert metadata.coupling_regime == "tight"
    assert metadata.capability_manifest["turn_steer"]["authorization"] == "lead_only"
    assert metadata.capability_manifest["turn_steer"]["callable_from_peers"] is False
    watchdog = metadata.capability_manifest["soft_non_progress_watchdog"]
    assert watchdog["callable_from_peers"] is False
    assert watchdog["schema"]["properties"]["non_progress_warn_s"]["default"] == 300


def test_codex_exec_backend_metadata_declares_structured_output_only(tmp_path: Path):
    metadata = codex_loop._backend_metadata(_codex_settings(tmp_path, app_server=False))

    assert metadata.capabilities == CODEX_EXEC_CAPABILITIES
    assert metadata.coupling_regime == "loose"
    assert "soft_non_progress_watchdog" not in metadata.capabilities


def test_gemini_acp_backend_metadata_accepts_peer_steer(tmp_path: Path):
    settings = GeminiSettings(
        team_name="t",
        agent_name="gemini-a",
        cwd=tmp_path,
        poll_interval_s=1.0,
        color="cyan",
        plan_mode_required=False,
        backend="acp",
    )

    metadata = gemini_loop._backend_metadata(settings)

    assert metadata.capabilities == GEMINI_ACP_CAPABILITIES
    assert metadata.coupling_regime == "tight"
    assert "accepts_peer_steer" in metadata.capabilities
    assert "soft_non_progress_watchdog" not in metadata.capabilities


def test_gemini_headless_backend_metadata_declares_no_capabilities(tmp_path: Path):
    settings = GeminiSettings(
        team_name="t",
        agent_name="gemini-a",
        cwd=tmp_path,
        poll_interval_s=1.0,
        color="cyan",
        plan_mode_required=False,
        backend="headless",
    )

    metadata = gemini_loop._backend_metadata(settings)

    assert metadata.capabilities == GEMINI_HEADLESS_CAPABILITIES == []
    assert metadata.coupling_regime == "loose"
    assert "soft_non_progress_watchdog" not in metadata.capabilities


def test_kimi_headless_backend_metadata_declares_large_context(tmp_path: Path):
    settings = KimiSettings(
        team_name="t",
        agent_name="kimi-a",
        cwd=tmp_path,
        poll_interval_s=1.0,
        color="cyan",
        plan_mode_required=False,
    )

    metadata = kimi_loop._backend_metadata(settings)

    assert metadata.capabilities == KIMI_HEADLESS_CAPABILITIES
    assert metadata.capabilities == ["large_context"]
    assert metadata.coupling_regime == "loose"
    assert "soft_non_progress_watchdog" not in metadata.capabilities


def test_capability_taxonomy_rejects_unknown_flags():
    with pytest.raises(ValueError, match="unknown capability"):
        assert_known_capabilities(["structured_output", "made_up"])


def test_agent_card_carries_coupling_regime_and_canonical_intent():
    card = build_agent_card(
        team_name="t",
        agent_name="codex-a",
        agent_id="codex-a@t",
        agent_type="claude-anyteam",
        model="codex-cli",
        backend_type="in-process",
        capabilities=["structured_output"],
        coupling_regime="tight",
    )

    assert card["coupling_regime"] == "tight"
    assert card["coupling"]["intent"] == "tight_peer_loop"


def test_capability_manifest_schema_accepts_coupling_regime():
    card = build_agent_card(
        team_name="t",
        agent_name="kimi-a",
        agent_id="kimi-a@t",
        agent_type="claude-anyteam",
        model="kimi-cli",
        backend_type="in-process",
        capabilities=["large_context"],
        coupling_regime="loose",
    )
    schema = json.loads(
        resources.files("claude_anyteam.schemas")
        .joinpath("capability_manifest.schema.json")
        .read_text(encoding="utf-8")
    )

    validate(card, schema)
