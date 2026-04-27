"""Mock DeepSeek adapter with a reasoning-trace export capability.

This demonstrates a capability that should not be projected onto every backend.
Peers discover `reasoning_trace_export`, load the manifest, then decide whether
its safety/size tradeoffs match the task.
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass

from agent_teams_kit import TaskResult, Teammate, run


@dataclass
class MockDeepSeekRun:
    summary: str
    trace: list[str]
    confidence: float


def should_export_trace(text: str) -> bool:
    return any(token in text.lower() for token in ["why", "rationale", "decision", "tradeoff"])


def redact_trace(trace: list[str], policy: str = "public_summary") -> list[str]:
    if policy == "none":
        return trace
    if policy == "secrets":
        return [item.replace("secret", "[REDACTED]") for item in trace]
    return [f"step {idx + 1}: {item}" for idx, item in enumerate(trace)]


async def invoke_deepseek_mock(prompt: str, *, export_trace: bool = False) -> MockDeepSeekRun:
    await asyncio.sleep(0)
    trace = []
    if export_trace:
        trace = [
            "identified task constraints",
            "compared implementation options",
            "selected smallest working patch",
        ]
    return MockDeepSeekRun(
        summary=f"DeepSeek mock completed request with {len(prompt)} characters",
        trace=trace,
        confidence=0.74 if export_trace else 0.66,
    )


class DeepSeekTeammate(Teammate):
    def agent_card(self) -> dict:
        return {
            "schema_version": 1,
            "harness": "deepseek-cli",
            "harness_version": "mock-0.1",
            "transport": "headless-jsonl",
            "accepts_peer_steer": False,
            "capabilities": {
                "reasoning_trace_export": {
                    "version": "1",
                    "schema": {
                        "type": "object",
                        "required": ["task_id", "redaction"],
                        "properties": {
                            "task_id": {"type": "string"},
                            "redaction": {"type": "string", "enum": ["none", "secrets", "public_summary"]},
                            "max_tokens": {"type": "integer", "minimum": 256, "maximum": 12000},
                        },
                    },
                    "description": "Export a bounded reasoning trace artifact for peer review and debugging.",
                    "when_to_use": "Use after complex design choices when a peer needs auditable rationale before building on the result.",
                    "when_not_to": "Avoid for secrets, private user data, or routine tasks where a final summary is enough.",
                    "failure_modes": ["TRACE_REDACTION_REQUIRED", "TRACE_TOO_LARGE", "TRACE_NOT_AVAILABLE"],
                    "accepts_peer_steer": False,
                },
                "long_context_review": {
                    "version": "1",
                    "schema": {
                        "type": "object",
                        "required": ["files"],
                        "properties": {"files": {"type": "array", "items": {"type": "string"}}},
                    },
                    "description": "Review many related files in one DeepSeek long-context pass.",
                    "when_to_use": "Route architecture audits or cross-file consistency checks when context breadth matters more than live tool interactivity.",
                    "when_not_to": "Do not use for fast targeted edits; startup and context packing add overhead.",
                    "failure_modes": ["CONTEXT_PACK_TOO_LARGE", "FILE_READ_FAILED"],
                },
                "trace_redaction_policy": {
                    "version": "1",
                    "schema": {
                        "type": "object",
                        "required": ["policy"],
                        "properties": {"policy": {"type": "string", "enum": ["none", "secrets", "public_summary"]}},
                    },
                    "description": "Declare how exported traces are redacted before peers or lead inspect them.",
                    "when_to_use": "Load this before asking DeepSeek for a reasoning trace in a task involving private or sensitive context.",
                    "when_not_to": "Do not treat redaction as a security boundary; avoid trace export entirely for secrets-heavy tasks.",
                    "failure_modes": ["POLICY_UNSUPPORTED", "REDACTION_INCOMPLETE"],
                },
            },
        }

    async def execute_task(self, task):
        wants_trace = should_export_trace(task.description)
        self.emit_turn_progress(0.0, "DeepSeek mock started", wants_trace=wants_trace)
        result = await invoke_deepseek_mock(task.description, export_trace=wants_trace)
        if result.trace:
            redacted = redact_trace(result.trace, "public_summary")
            trace_path = f"deepseek-trace-{task.id}.json"
            self.emit_artifact_event(trace_path, "created", source="deepseek.reasoning_trace_export")
            self.emit_tool_event(
                "host_tool",
                "deepseek.reasoning_trace_export",
                "completed",
                status="success",
                stdout_preview=json.dumps(redacted)[:160],
            )
            return TaskResult(summary=result.summary, files_changed=[trace_path])
        return TaskResult(summary=result.summary)

    async def reply_to_prose(self, peer: str, body: str) -> str | None:
        result = await invoke_deepseek_mock(body, export_trace=False)
        return f"DeepSeek mock reply to {peer}: {result.summary}; confidence={result.confidence}"


if __name__ == "__main__":
    sys.exit(run(DeepSeekTeammate))
