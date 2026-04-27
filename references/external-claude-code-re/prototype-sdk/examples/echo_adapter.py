"""Minimal adapter: transport is inherited; capability layer is tiny."""

from __future__ import annotations

import sys

from agent_teams_kit import TaskResult, Teammate, run


def _short(text: str, limit: int = 80) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


class EchoTeammate(Teammate):
    def agent_card(self) -> dict:
        return {
            "schema_version": 1,
            "harness": "echo",
            "harness_version": "0.1",
            "transport": "in-process-mock",
            "capabilities": {
                "echo_summary": {
                    "version": "1",
                    "schema": {"type": "object", "properties": {"text": {"type": "string"}}},
                    "description": "Return a concise echo of the assigned task.",
                    "when_to_use": "Use for smoke tests of registration, inbox, task claim, and completion.",
                    "when_not_to": "Do not use for real coding or reasoning tasks.",
                    "failure_modes": ["NO_TASK_DESCRIPTION"],
                }
            },
        }

    async def execute_task(self, task):
        subject = _short(task.subject, 48)
        description = _short(task.description, 96)
        summary = f"echo: {subject} — {description}"
        self.emit_turn_progress(0.1, summary, mode="echo")
        return TaskResult(summary=summary)

    async def reply_to_prose(self, peer: str, body: str) -> str | None:
        if not body.strip():
            return None
        return f"echo to {peer}: {_short(body, 120)}"


if __name__ == "__main__":
    sys.exit(run(EchoTeammate))
