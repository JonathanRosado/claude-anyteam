from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .capabilities import peer_prompt_fragment
from .storage import TeamStorage


@dataclass(frozen=True)
class Member:
    name: str
    capabilities: list[str]
    raw: dict[str, Any]


class Team:
    """08 §6.4 hybrid roster discovery + cached rich manifests."""

    def __init__(self, team: str, storage: TeamStorage):
        self.team = team
        self.storage = storage
        self.manifest_cache: dict[str, dict[str, Any]] = {}

    def roster(self) -> list[Member]:
        cfg = self.storage.read_config(self.team)
        members: list[Member] = []
        for raw in cfg.get("members", []):
            if not isinstance(raw, dict) or "prompt" not in raw:
                continue
            caps = raw.get("capabilities", [])
            if isinstance(caps, dict):
                caps = [k for k, v in caps.items() if v not in (False, "unsupported", None)]
            members.append(Member(raw.get("name", ""), sorted(caps), raw))
        return members

    def find_capability(self, cap_name: str) -> list[str]:
        return [m.name for m in self.roster() if cap_name in m.capabilities]

    def capability_manifest(self, agent_name: str, cap_name: str | None = None) -> dict[str, Any]:
        if not self.manifest_cache:
            self.broadcast_capability_manifest()
        card = self.manifest_cache.get(agent_name, {})
        caps = card.get("capabilities", {}) if isinstance(card, dict) else {}
        return caps.get(cap_name, {}) if cap_name else caps

    def broadcast_capability_manifest(self) -> dict[str, dict[str, Any]]:
        """Cache all Agent Cards once so peers do O(1) capability lookup."""
        cfg = self.storage.read_config(self.team)
        cards = {
            m["name"]: m.get("agentCard", {})
            for m in cfg.get("members", [])
            if isinstance(m, dict) and m.get("name") and m.get("agentCard")
        }

        # Store peer caches in config as a research stand-in for wrapper MCP
        # fan-out. Production may keep this in process memory only.
        def _update(raw: dict[str, Any]) -> dict[str, Any]:
            for member in raw.get("members", []):
                if isinstance(member, dict) and member.get("name") in cards:
                    member["peerManifestCache"] = {
                        name: card for name, card in cards.items() if name != member["name"]
                    }
            return raw

        self.storage.update_config(self.team, _update)
        self.manifest_cache = cards
        return cards

    def peer_prompt_fragments_for(self, requester: str) -> str:
        cfg = self.storage.read_config(self.team)
        requester_caps: set[str] = set()
        cards: dict[str, dict[str, Any]] = {}
        for member in cfg.get("members", []):
            if not isinstance(member, dict):
                continue
            if member.get("name") == requester:
                requester_caps = set(member.get("capabilities", []))
            if member.get("agentCard"):
                cards[member["name"]] = member["agentCard"]
        fragments: list[str] = ["# Capabilities of your peers"]
        for name, card in sorted(cards.items()):
            if name == requester:
                continue
            unique = {
                cap: entry
                for cap, entry in card.get("capabilities", {}).items()
                if cap not in requester_caps
            }
            if unique:
                peer_card = dict(card)
                peer_card["capabilities"] = unique
                fragments.append(peer_prompt_fragment(name, peer_card))
        return "\n\n".join(fragments)
