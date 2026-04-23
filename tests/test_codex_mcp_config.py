"""Tests for the codex `-c mcp_servers.*` override plumbing.

These verify the exact flag shape we send Codex. A regression here would
produce silent "Codex booted but has no tools" behavior at runtime
(observed live during v7 development as the exact failure mode we're
defending against by using inline -c instead of `codex mcp add`).

Sandbox-related flag assertions live in `test_codex_invocation_shape.py`
so this file stays focused on the MCP wiring.
"""

from __future__ import annotations

from claude_anyteam import codex as codex_mod


def test_wrapper_mcp_config_args_default_shape():
    args = codex_mod.wrapper_mcp_config_args("my-team", "codex-alice")
    # Two `-c key=value` pairs: command, args. No sandbox carve-out here —
    # that belongs to the codex.run invocation shape (tested separately).
    assert args[0] == "-c"
    assert args[1].startswith("mcp_servers.claude_anyteam_wrapper.command=")
    assert args[2] == "-c"
    assert args[3] == "mcp_servers.claude_anyteam_wrapper.args=[]"
    # The resolved command should end with `claude-anyteam-wrapper` and be
    # quoted so TOML parses it as a string.
    assert args[1].endswith('claude-anyteam-wrapper"') or args[1].endswith(
        "claude-anyteam-wrapper\""
    )


def test_wrapper_mcp_config_args_custom_server_name():
    args = codex_mod.wrapper_mcp_config_args(
        "my-team", "codex-alice", server_name="my_custom"
    )
    assert "mcp_servers.my_custom.command=" in args[1]
    assert "mcp_servers.my_custom.args=[]" in args[3]


def test_wrapper_mcp_config_args_custom_binary_absolute_path():
    """Explicit absolute path is preserved (shutil.which returns it as-is)."""
    args = codex_mod.wrapper_mcp_config_args(
        "my-team", "codex-alice", wrapper_binary="/usr/local/bin/claude-anyteam-wrapper"
    )
    assert 'command="/usr/local/bin/claude-anyteam-wrapper"' in args[1]


def test_wrapper_mcp_config_args_unknown_binary_falls_through():
    """If shutil.which can't resolve, fall through to the bare name so
    Codex's spawn fails loudly instead of us silently hiding the problem."""
    args = codex_mod.wrapper_mcp_config_args(
        "t", "a", wrapper_binary="this-binary-definitely-does-not-exist-xyz"
    )
    assert 'command="this-binary-definitely-does-not-exist-xyz"' in args[1]


def test_wrapper_mcp_config_args_returns_four_tokens():
    """Flag count is load-bearing: `codex exec` parses `-c key=value` as
    two tokens each. Two TOML lines = four tokens total."""
    args = codex_mod.wrapper_mcp_config_args("t", "a")
    assert len(args) == 4
    assert args[0] == "-c"
    assert args[2] == "-c"


def test_wrapper_mcp_config_args_does_not_emit_sandbox_key():
    """The sandbox is disabled at the invocation level via
    `--dangerously-bypass-approvals-and-sandbox`, so no
    `sandbox_workspace_write.writable_roots` belongs here.
    Regression guard: if someone re-adds path 1 (the `writable_roots`
    carve-out), this test fails and forces a conversation about whether
    we're back-pedalling from the bypass policy.
    """
    args = codex_mod.wrapper_mcp_config_args("t", "a")
    joined = " ".join(args)
    assert "writable_roots" not in joined
    assert "sandbox_workspace_write" not in joined
