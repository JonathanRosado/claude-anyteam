"""Assertions on the `codex exec` argv produced by `codex.run`.

Particularly: the `--dangerously-bypass-approvals-and-sandbox` flag is
load-bearing for v7. Without it, the wrapper MCP server (which runs as
Codex's subprocess and inherits its sandbox) can't write to
`~/.claude/tasks/` or `~/.claude/teams/*/inboxes/`, and mid-task tool
calls silently fail — exactly the failure mode observed at M5 attempt 1.

The regression guard here is to fail loudly if anyone swaps the bypass
for `--full-auto` or `--sandbox workspace-write`: path 1 was explicitly
rejected in favour of path 2 per the user's direction.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from claude_anyteam import codex as codex_mod


class _FakeCompletedProcess:
    def __init__(self) -> None:
        self.stdout = ""
        self.stderr = ""
        self.returncode = 0


def _build_argv(**kwargs) -> list[str]:
    """Capture the argv `codex.run` would pass to `subprocess.run`."""
    captured: dict = {}

    def fake_run(args, **_):
        captured["args"] = args
        return _FakeCompletedProcess()

    call_kwargs: dict = {
        "prompt": "noop",
        "cwd": Path("/tmp"),
        "schema": None,
        "codex_binary": "codex",
    }
    call_kwargs.update(kwargs)

    with patch.object(codex_mod.subprocess, "run", side_effect=fake_run):
        codex_mod.run(**call_kwargs)
    return captured["args"]


def test_bypass_sandbox_flag_is_present():
    argv = _build_argv()
    assert "--dangerously-bypass-approvals-and-sandbox" in argv


def test_full_auto_flag_is_absent():
    """Path 1 used `--full-auto` (= `--sandbox workspace-write`). Path 2
    uses the full bypass instead. If someone re-adds `--full-auto`, this
    test fails — the two flags would conflict and the intent is unclear."""
    argv = _build_argv()
    assert "--full-auto" not in argv


def test_sandbox_flag_is_absent():
    """No explicit `--sandbox <mode>` either — the bypass supersedes."""
    argv = _build_argv()
    assert "--sandbox" not in argv


def test_core_flags_still_present():
    """Invariants that M1–M4 established must not be silently dropped:
    `--json`, `--skip-git-repo-check`, `--output-last-message`, `-C`."""
    argv = _build_argv()
    assert "--json" in argv
    assert "--skip-git-repo-check" in argv
    assert "--output-last-message" in argv
    assert "-C" in argv


def test_argv_order_puts_bypass_before_prompt():
    """The bypass flag must precede the prompt positional, otherwise
    Codex parses it as part of the prompt text."""
    argv = _build_argv()
    bypass_idx = argv.index("--dangerously-bypass-approvals-and-sandbox")
    prompt_idx = argv.index("noop")
    assert bypass_idx < prompt_idx


# ---- v7.2: resume-path argv shape -------------------------------------------
# `codex exec resume` on codex-cli 0.122.0 has a narrower flag set than
# `codex exec`. Specifically: no --output-schema, no -C/--cd. The test suite
# is the regression guard against silently reverting to the fresh-exec shape.


def test_resume_argv_starts_with_exec_resume_session_id():
    argv = _build_argv(resume_session_id="019db604-bdb9-75e2-a894-65ebc2214c37")
    assert argv[1] == "exec"
    assert argv[2] == "resume"
    assert argv[3] == "019db604-bdb9-75e2-a894-65ebc2214c37"


def test_resume_argv_has_no_output_schema():
    """Documented CLI limitation: resume rejects --output-schema.
    v7.2 validates the output in Python instead."""
    argv = _build_argv(
        resume_session_id="sid-1",
        schema=Path("/anywhere/task-complete.schema.json"),
    )
    assert "--output-schema" not in argv
    assert "/anywhere/task-complete.schema.json" not in argv


def test_resume_argv_has_no_cwd_flag():
    """-C/--cd isn't accepted by resume; must not be emitted."""
    argv = _build_argv(resume_session_id="sid-1")
    assert "-C" not in argv
    assert "--cd" not in argv


def test_resume_argv_keeps_bypass_and_output_last_message():
    argv = _build_argv(resume_session_id="sid-1")
    assert "--dangerously-bypass-approvals-and-sandbox" in argv
    assert "--output-last-message" in argv
    assert "--json" in argv
    assert "--skip-git-repo-check" in argv


def test_resume_argv_passes_extra_args_and_prompt_at_end():
    argv = _build_argv(
        resume_session_id="sid-1",
        extra_args=["-c", 'mcp_servers.claude_anyteam_wrapper.command="foo"'],
    )
    c_idx = argv.index("-c")
    prompt_idx = argv.index("noop")
    assert c_idx < prompt_idx
    assert argv[3] == "sid-1"


def test_fresh_exec_still_includes_schema_and_cwd():
    """Regression guard: adding the resume branch must not change the
    fresh-exec shape."""
    argv = _build_argv(schema=Path("/tmp/s.json"))
    assert "--output-schema" in argv
    assert "/tmp/s.json" in argv
    assert "-C" in argv
    # No `resume` positional.
    assert "resume" not in argv
