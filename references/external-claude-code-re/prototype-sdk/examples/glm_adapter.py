"""Mock GLM adapter showing harness-specific capability declarations.

The important part is not the fake execution below; it is the Agent Card. GLM
keeps its unique primitives in the capability layer instead of flattening to a
lowest-common-denominator task runner.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass

from agent_teams_kit import TaskResult, Teammate, run


@dataclass
class MockGlmRun:
    summary: str
    code_cells: int
    files_changed: list[str]


def wants_code_interpreter(text: str) -> bool:
    triggers = ["calculate", "csv", "dataframe", "plot", "analyze", "statistic"]
    return any(trigger in text.lower() for trigger in triggers)


def handoff_note(task_description: str) -> str:
    if wants_code_interpreter(task_description):
        return "GLM will use native code-interpreter scratchpad; exported files are copied back."
    return "GLM will answer through its normal headless JSON stream."


async def invoke_glm_mock(prompt: str, *, code_interpreter: bool = False) -> MockGlmRun:
    await asyncio.sleep(0)
    cells = 1 if code_interpreter and ("calculate" in prompt.lower() or "plot" in prompt.lower()) else 0
    files = ["glm-output.txt"] if "write" in prompt.lower() else []
    return MockGlmRun(summary=f"GLM mock handled {len(prompt.split())} words", code_cells=cells, files_changed=files)


class GlmTeammate(Teammate):
    def agent_card(self) -> dict:
        return {
            "schema_version": 1,
            "harness": "glm-cli",
            "harness_version": "mock-0.1",
            "transport": "headless-stream-json",
            "accepts_peer_steer": True,
            "capabilities": {
                "code_interpreter_native": {
                    "version": "1",
                    "schema": {
                        "type": "object",
                        "required": ["prompt"],
                        "properties": {
                            "prompt": {"type": "string", "maxLength": 12000},
                            "artifacts_dir": {"type": "string"},
                        },
                    },
                    "description": "Run short analytical Python snippets in GLM's native code-interpreter mode.",
                    "when_to_use": "Route data analysis, quick calculations, CSV inspection, and plot-generation subtasks here when native execution context matters.",
                    "when_not_to": "Do not use for repository-wide edits; GLM's code-interpreter scratchpad is separate from the working tree.",
                    "failure_modes": ["INTERPRETER_TIMEOUT", "ARTIFACT_EXPORT_FAILED", "UNSUPPORTED_PACKAGE"],
                    "accepts_peer_steer": True,
                },
                "turn_steer": {
                    "version": "1",
                    "schema": {
                        "type": "object",
                        "required": ["text"],
                        "properties": {
                            "text": {"type": "string", "maxLength": 8192},
                            "task_id": {"type": ["string", "null"]},
                        },
                    },
                    "description": "Queue steering text for the next prompt boundary.",
                    "when_to_use": "Use when a peer has new constraints before GLM starts its next harness turn.",
                    "when_not_to": "Do not expect live mid-token interruption; this mock headless transport only checks between turns.",
                    "failure_modes": ["STEER_BUFFERED_NEXT_BOUNDARY", "STEER_AUTH_REJECTED"],
                    "delivery_mode": "next_turn",
                    "authorization": "any_peer",
                    "accepts_peer_steer": True,
                },
                "host_tool_surface": {
                    "version": "1",
                    "schema": {"type": "string", "enum": ["glm-native"]},
                    "description": "Informational roster signal that GLM uses its own shell/file tool names.",
                    "when_to_use": "Use when explaining a task handoff to GLM in its native vocabulary.",
                    "when_not_to": "Not callable; peers should not send a tool invocation for this capability.",
                    "failure_modes": [],
                },
                "scratchpad_artifact_export": {
                    "version": "1",
                    "schema": {
                        "type": "object",
                        "required": ["artifact_name"],
                        "properties": {"artifact_name": {"type": "string"}, "target_path": {"type": "string"}},
                    },
                    "description": "Copy a GLM code-interpreter artifact from scratchpad storage into the repository.",
                    "when_to_use": "Use after GLM creates a plot, table, or generated report that peers need in the shared working tree.",
                    "when_not_to": "Do not export transient logs or large raw datasets; summarize those in the final task result instead.",
                    "failure_modes": ["ARTIFACT_NOT_FOUND", "TARGET_PATH_UNSAFE", "EXPORT_IO_ERROR"],
                },
            },
        }

    async def execute_task(self, task):
        wants_code = wants_code_interpreter(task.description)
        self.emit_turn_progress(0.0, "GLM mock received task", wants_code=wants_code, note=handoff_note(task.description))
        if wants_code:
            self.emit_tool_event(
                "host_tool",
                "glm.code_interpreter",
                "started",
                target="scratchpad.py",
                status="running",
            )
        result = await invoke_glm_mock(task.description, code_interpreter=wants_code)
        if wants_code:
            self.emit_tool_event(
                "host_tool",
                "glm.code_interpreter",
                "completed",
                status="success",
                exit_code=0,
                code_cells=result.code_cells,
            )
        for path in result.files_changed:
            self.emit_artifact_event(path, "created", source="glm_mock")
        return TaskResult(summary=result.summary, files_changed=result.files_changed)

    async def reply_to_prose(self, peer: str, body: str) -> str | None:
        result = await invoke_glm_mock(body, code_interpreter=False)
        return f"GLM mock reply to {peer}: {result.summary}"


if __name__ == "__main__":
    sys.exit(run(GlmTeammate))
