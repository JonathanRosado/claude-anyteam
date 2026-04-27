from __future__ import annotations

from agent_teams_kit import FilesystemStorage, Teammate
from agent_teams_kit.team import Team


class CapMate(Teammate):
    cap_name = "base"

    def agent_card(self):
        return {
            "schema_version": 1,
            "harness": self.cap_name,
            "harness_version": "1",
            "transport": "test",
            "capabilities": {
                self.cap_name: {
                    "version": "1",
                    "schema": {"type": "object"},
                    "description": f"{self.cap_name} desc",
                    "when_to_use": "when useful",
                    "when_not_to": "when not useful",
                    "failure_modes": [],
                }
            },
        }

    async def execute_task(self, task):  # pragma: no cover
        raise NotImplementedError

    async def reply_to_prose(self, peer, body):  # pragma: no cover
        return None


class GlmMate(CapMate):
    cap_name = "code_interpreter_native"


class DeepMate(CapMate):
    cap_name = "reasoning_trace_export"


def test_find_capability_and_broadcast_manifest(tmp_path):
    storage = FilesystemStorage(tmp_path)
    storage.create_team("t")
    GlmMate(team="t", name="glm-1", storage=storage).register()
    DeepMate(team="t", name="deep-1", storage=storage).register()

    team = Team("t", storage)
    assert team.find_capability("code_interpreter_native") == ["glm-1"]
    caches = team.broadcast_capability_manifest()
    assert sorted(caches) == ["deep-1", "glm-1"]
    assert team.capability_manifest("deep-1", "reasoning_trace_export")["description"] == "reasoning_trace_export desc"

    cfg = storage.read_config("t")
    glm_row = next(m for m in cfg["members"] if m.get("name") == "glm-1")
    assert "deep-1" in glm_row["peerManifestCache"]


def test_peer_prompt_fragments_skip_requester_capabilities(tmp_path):
    storage = FilesystemStorage(tmp_path)
    storage.create_team("t")
    GlmMate(team="t", name="glm-1", storage=storage).register()
    DeepMate(team="t", name="deep-1", storage=storage).register()

    fragment = Team("t", storage).peer_prompt_fragments_for("glm-1")

    assert "reasoning_trace_export" in fragment
    assert "code_interpreter_native desc" not in fragment
