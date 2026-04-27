from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


# 08 §6.3 Teammate.agent_card: per-capability schema + semantic guidance.
class CapabilityEntry(BaseModel):
    model_config = ConfigDict(extra="allow")

    version: str
    schema_: dict[str, Any] = Field(alias="schema")
    description: str
    when_to_use: str
    when_not_to: str
    failure_modes: list[str] = Field(default_factory=list)

    @field_validator("failure_modes")
    @classmethod
    def _constantish(cls, modes: list[str]) -> list[str]:
        for mode in modes:
            if not mode or any(ch.isspace() for ch in mode):
                raise ValueError(f"failure mode must be a constant-like token: {mode!r}")
        return modes


class CapabilityManifest(BaseModel):
    """Rich Agent Card / capability manifest from 08 §6.3."""

    model_config = ConfigDict(extra="allow")

    schema_version: int = 1
    harness: str
    harness_version: str = "unknown"
    transport: str = "unknown"
    capabilities: dict[str, CapabilityEntry] = Field(default_factory=dict)

    def flat_capabilities(self) -> list[str]:
        """Cheap roster list written to config.json members[].capabilities."""
        caps = set(self.capabilities.keys())
        extras = getattr(self, "__pydantic_extra__", {}) or {}
        if extras.get("accepts_peer_steer") is True:
            caps.add("accepts_peer_steer")
        return sorted(caps)

    def peer_prompt_fragment(self, agent_name: str) -> str:
        return peer_prompt_fragment(agent_name, self.model_dump(by_alias=True))


def validate_manifest(card: dict[str, Any]) -> dict[str, Any]:
    """Return a normalized dict but keep extra fields (e.g. accepts_peer_steer)."""
    return CapabilityManifest.model_validate(card).model_dump(by_alias=True)


def flat_capabilities(card: dict[str, Any]) -> list[str]:
    try:
        return CapabilityManifest.model_validate(card).flat_capabilities()
    except Exception:
        caps = card.get("capabilities", {}) if isinstance(card, dict) else {}
        names = set(caps.keys()) if isinstance(caps, dict) else set()
        if isinstance(card, dict) and card.get("accepts_peer_steer") is True:
            names.add("accepts_peer_steer")
        return sorted(names)


def peer_prompt_fragment(agent_name: str, card: dict[str, Any]) -> str:
    """08 §6.3 default auto-generation: one paragraph per capability."""
    caps = card.get("capabilities", {}) if isinstance(card, dict) else {}
    lines = [f"# Capabilities of {agent_name}"]
    for name in sorted(caps):
        entry = caps[name]
        failures = ", ".join(entry.get("failure_modes", [])) or "none declared"
        lines.extend(
            [
                f"- {name} (v{entry.get('version', 'unknown')}): {entry.get('description', '')}",
                f"  When to use: {entry.get('when_to_use', '')}",
                f"  When not to: {entry.get('when_not_to', '')}",
                f"  Failure modes: {failures}",
            ]
        )
    return "\n".join(lines)
