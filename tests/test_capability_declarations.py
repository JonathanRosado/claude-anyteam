from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

import pytest
from jsonschema import validate

from claude_anyteam import loop as codex_loop
from claude_anyteam.backends.claude_native.config import ClaudeNativeSettings
from claude_anyteam.backends.claude_native import loop as claude_native_loop
from claude_anyteam.backends.gemini.config import GeminiSettings
from claude_anyteam.backends.gemini import loop as gemini_loop
from claude_anyteam.backends.kimi.config import KimiSettings
from claude_anyteam.backends.kimi import loop as kimi_loop
from claude_anyteam.capabilities import (
    CLAUDE_NATIVE_HEADLESS_CAPABILITIES,
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
    assert "plan_mode" in metadata.capabilities
    assert "soft_non_progress_watchdog" in metadata.capabilities
    assert "wrapper_tool_failure_discriminator" in metadata.capabilities
    assert metadata.coupling_regime == "tight"
    assert metadata.capability_manifest["turn_steer"]["authorization"] == "lead_only"
    assert metadata.capability_manifest["turn_steer"]["callable_from_peers"] is False
    watchdog = metadata.capability_manifest["soft_non_progress_watchdog"]
    assert watchdog["callable_from_peers"] is False
    # Task #5 / RFC #50 Phase B: ``non_progress_warn_s`` is now opt-in
    # (default None) — see docs/design/timers-vs-visibility.md.
    assert watchdog["schema"]["properties"]["non_progress_warn_s"]["default"] is None
    assert watchdog["schema"]["properties"]["non_progress_warn_s"]["maximum"] == 1800
    discriminator = metadata.capability_manifest[
        "wrapper_tool_failure_discriminator"
    ]
    assert discriminator["callable_from_peers"] is False
    assert (
        discriminator["schema"]["properties"]["wrapper_tool_failure_window_s"][
            "default"
        ]
        == 90
    )
    assert discriminator["schema"]["properties"]["debounce_key"]["const"] == "tool_name"


def test_codex_app_server_metadata_advertises_native_host_tools_inventory(tmp_path: Path):
    """Issue #33 regression guard.

    Codex App Server has codex-native tools (`imagegeneration`, `imageview`,
    `websearch`, `filechange`) that live OUTSIDE the wrapper-MCP surface.
    Per north-star §1 (`feedback_capability_decl_vs_flatten`), the canonical
    discovery surface is the capability manifest, NOT a hardcoded list in
    skill text. The `live_tool_events` entry must declare them in
    `native_host_tools` so peers can introspect via
    `mcp_anyteam_capability_manifest('<codex-name>', 'live_tool_events')`.
    """
    metadata = codex_loop._backend_metadata(_codex_settings(tmp_path, app_server=True))

    live_tool_events = metadata.capability_manifest["live_tool_events"]
    assert live_tool_events.get("host_tool_surface") == "codex-native"
    native_tools = live_tool_events.get("native_host_tools")
    assert isinstance(native_tools, list)
    assert set(native_tools) >= {
        "imagegeneration",
        "imageview",
        "websearch",
        "filechange",
    }, f"missing canonical Codex App Server native tools: {native_tools}"


def test_codex_exec_metadata_omits_native_host_tools_inventory(tmp_path: Path):
    """Codex exec mode does not run on App Server, so it must not advertise
    App-Server-only native tools. The `live_tool_events` capability is also
    absent from `CODEX_EXEC_CAPABILITIES`, so the entry simply isn't there.
    """
    metadata = codex_loop._backend_metadata(_codex_settings(tmp_path, app_server=False))
    assert "live_tool_events" not in metadata.capability_manifest


def test_codex_exec_backend_metadata_declares_headless_capabilities(tmp_path: Path):
    metadata = codex_loop._backend_metadata(_codex_settings(tmp_path, app_server=False))

    assert metadata.capabilities == CODEX_EXEC_CAPABILITIES
    assert metadata.coupling_regime == "loose"
    assert "headless_invocation" in metadata.capabilities
    assert "session_resume" in metadata.capabilities
    assert "structured_output" in metadata.capabilities
    assert "plan_mode" in metadata.capabilities
    assert "soft_non_progress_watchdog" not in metadata.capabilities
    assert "wrapper_tool_failure_discriminator" not in metadata.capabilities


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
    assert "structured_output" in metadata.capabilities
    assert "session_resume" in metadata.capabilities
    assert "plan_mode" in metadata.capabilities
    assert "trust_modes" in metadata.capabilities
    assert "soft_non_progress_watchdog" not in metadata.capabilities
    assert metadata.capability_manifest["turn_steer"]["delivery_mode"] == "next_turn"
    assert metadata.capability_manifest["turn_steer"]["authorization"] == "any_peer"


def test_gemini_headless_backend_metadata_declares_headless_capabilities(tmp_path: Path):
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

    assert metadata.capabilities == GEMINI_HEADLESS_CAPABILITIES
    assert metadata.coupling_regime == "loose"
    assert metadata.capabilities == [
        "headless_invocation",
        "session_resume",
        "structured_output",
        "plan_mode",
    ]
    assert "soft_non_progress_watchdog" not in metadata.capabilities


def test_kimi_headless_backend_metadata_declares_large_context_and_native_skills(tmp_path: Path):
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
    assert metadata.capabilities == [
        "headless_invocation",
        "session_resume",
        "structured_output",
        "plan_mode",
        "native_skills",
        "large_context",
    ]
    assert metadata.coupling_regime == "loose"
    assert "soft_non_progress_watchdog" not in metadata.capabilities


def test_kimi_headless_backend_metadata_declares_native_skills(tmp_path: Path):
    settings = KimiSettings(
        team_name="t",
        agent_name="kimi-a",
        cwd=tmp_path,
        poll_interval_s=1.0,
        color="cyan",
        plan_mode_required=False,
    )

    metadata = kimi_loop._backend_metadata(settings)

    assert "native_skills" in metadata.capabilities
    native = metadata.capability_manifest["native_skills"]
    assert native["callable_from_peers"] is False
    assert "backend-native" in native["description"]


def test_claude_native_backend_metadata_declares_native_tool_surface(tmp_path: Path):
    settings = ClaudeNativeSettings(
        team_name="t",
        agent_name="claude-a",
        cwd=tmp_path,
        poll_interval_s=1.0,
        color="cyan",
        plan_mode_required=False,
    )

    metadata = claude_native_loop._backend_metadata(settings)

    assert metadata.capabilities == CLAUDE_NATIVE_HEADLESS_CAPABILITIES
    assert metadata.capabilities == [
        "headless_invocation",
        "structured_output",
        "live_tool_events",
        "native_skills",
        "large_context",
    ]
    assert "session_resume" not in metadata.capabilities
    assert "plan_mode" not in metadata.capabilities
    assert "Task,Skill,WebFetch,Read,Edit,Write,Bash" in metadata.host_tool_surface
    modes = metadata.capability_manifest["headless_invocation"]["schema"]["properties"]["mode"][
        "enum"
    ]
    assert "claude_native" in modes
    assert (
        metadata.capability_manifest["live_tool_events"]["host_tool_surface"]
        == metadata.host_tool_surface
    )
    native = metadata.capability_manifest["native_skills"]
    assert native["callable_from_peers"] is False
    assert "Claude Code's Skill/Task tools" in native["when_to_use"]


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
