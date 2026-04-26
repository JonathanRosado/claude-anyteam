from __future__ import annotations

import errno
import json
from pathlib import Path
from typing import Any

import pytest

from claude_anyteam import cli as cli_mod
from claude_anyteam import installer as installer_mod


@pytest.fixture(autouse=True)
def _skip_real_plugin_registration(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit tests should not invoke a real `claude plugin install`."""

    monkeypatch.setattr(installer_mod, "_register_claude_plugin", lambda: None)


def _make_executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


_GEMINI_ALL_CAPABILITIES = {
    "--prompt": True,
    "--output-format stream-json": True,
    "--resume": True,
    "--approval-mode yolo": True,
    "--acp": True,
}


def _codex_cli_ready(
    version: str | None = "0.124.0",
    *,
    signed_in: bool = False,
) -> installer_mod.CodexCliCheck:
    return installer_mod.CodexCliCheck(
        found=True,
        path=Path("/usr/local/bin/codex"),
        version=version,
        raw_output=f"codex-cli {version}" if version else "codex-cli unknown",
        signed_in=signed_in,
    )


def _codex_cli_missing() -> installer_mod.CodexCliCheck:
    return installer_mod.CodexCliCheck(found=False, path=None, version=None, raw_output=None)


def _gemini_cli_ready(
    version: str | None = "0.39.0",
    *,
    signed_in: bool = False,
) -> installer_mod.GeminiCliCheck:
    return installer_mod.GeminiCliCheck(
        found=True,
        path=Path("/usr/local/bin/gemini"),
        version=version,
        raw_output=version,
        capabilities=_GEMINI_ALL_CAPABILITIES,
        signed_in=signed_in,
    )


def _gemini_cli_missing() -> installer_mod.GeminiCliCheck:
    return installer_mod.GeminiCliCheck(found=False, path=None, version=None, raw_output=None)


def _kimi_cli_ready(
    version: str | None = "1.2.3",
    *,
    signed_in: bool = False,
) -> installer_mod.KimiCliCheck:
    return installer_mod.KimiCliCheck(
        found=True,
        path=Path("/usr/local/bin/kimi"),
        version=version,
        raw_output=f"kimi {version}" if version else "kimi unknown",
        signed_in=signed_in,
    )


def _kimi_cli_missing() -> installer_mod.KimiCliCheck:
    return installer_mod.KimiCliCheck(found=False, path=None, version=None, raw_output=None)


def _auth(signed_in: bool) -> installer_mod.AuthCheck:
    return installer_mod.AuthCheck(signed_in=signed_in)


def _stub_provider_checks(
    monkeypatch: pytest.MonkeyPatch,
    *,
    codex_cli: installer_mod.CodexCliCheck | None = None,
    codex_signed_in: bool = True,
    gemini_cli: installer_mod.GeminiCliCheck | None = None,
    gemini_signed_in: bool = False,
    kimi_cli: installer_mod.KimiCliCheck | None = None,
    kimi_signed_in: bool = False,
) -> None:
    monkeypatch.setattr(installer_mod, "_check_codex_cli", lambda: codex_cli or _codex_cli_ready())
    monkeypatch.setattr(installer_mod, "_check_codex_auth", lambda: _auth(codex_signed_in))
    monkeypatch.setattr(installer_mod, "_check_gemini_cli", lambda: gemini_cli or _gemini_cli_missing())
    monkeypatch.setattr(installer_mod, "_check_gemini_auth", lambda: _auth(gemini_signed_in))
    monkeypatch.setattr(installer_mod, "_check_kimi_cli", lambda: kimi_cli or _kimi_cli_missing())
    monkeypatch.setattr(installer_mod, "_check_kimi_auth", lambda: _auth(kimi_signed_in))


def _stub_prereq_found(
    monkeypatch: pytest.MonkeyPatch,
    binary: str = "tmux",
    *,
    stub_providers: bool = True,
) -> None:
    """Replace the real terminal-multiplexer probe with a "found" stub.

    Keeps the existing-test pattern of "stub the resolution, assert the rest".
    """

    def _stub() -> installer_mod.PrereqCheck:
        return installer_mod.PrereqCheck(
            found=True,
            binary=binary,
            path=Path(f"/usr/bin/{binary}"),
            platform="linux",
        )

    monkeypatch.setattr(installer_mod, "_check_terminal_multiplexer", _stub)
    if stub_providers:
        _stub_provider_checks(monkeypatch)


def _stub_prereq_missing(monkeypatch: pytest.MonkeyPatch, platform: str = "linux") -> None:
    def _stub() -> installer_mod.PrereqCheck:
        return installer_mod.PrereqCheck(found=False, binary=None, path=None, platform=platform)

    monkeypatch.setattr(installer_mod, "_check_terminal_multiplexer", _stub)


def _fresh_paths(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    """Standard path fixtures used across install/uninstall tests."""
    settings_path = tmp_path / "home" / ".claude" / "settings.json"
    claude_json_path = tmp_path / "home" / ".claude.json"
    state_path = (
        tmp_path / "home" / ".claude" / "plugins" / "data"
        / installer_mod.PLUGIN_DATA_DIR_NAME / installer_mod.STATE_FILE_NAME
    )
    bin_dir = tmp_path / "venv" / "bin"
    return settings_path, claude_json_path, state_path, bin_dir


def _install_argv(
    settings_path: Path,
    claude_json_path: Path,
    state_path: Path,
    *extra: str,
) -> list[str]:
    return [
        "install",
        "--settings-path",
        str(settings_path),
        "--claude-json-path",
        str(claude_json_path),
        "--state-path",
        str(state_path),
        *extra,
    ]


def _uninstall_argv(
    settings_path: Path,
    claude_json_path: Path,
    state_path: Path,
) -> list[str]:
    return [
        "uninstall",
        "--settings-path",
        str(settings_path),
        "--claude-json-path",
        str(claude_json_path),
        "--state-path",
        str(state_path),
    ]


def _ready_install_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path, Path]:
    settings_path, claude_json_path, state_path, bin_dir = _fresh_paths(tmp_path)
    _make_executable(bin_dir / "claude-anyteam")
    _make_executable(bin_dir / "claude-anyteam-spawn-shim")
    _stub_prereq_found(monkeypatch)
    monkeypatch.setattr(installer_mod.shutil, "which", lambda name: None)
    return settings_path, claude_json_path, state_path, bin_dir


def _assert_install_error_fields(
    exc: installer_mod.InstallError,
    *,
    title: str,
    explanation: str,
    action: str,
    severity: installer_mod.InstallSeverity,
) -> None:
    assert exc.title == title
    assert explanation in exc.explanation
    assert action in exc.action
    assert exc.severity == severity


# ---------------------------------------------------------------------------
# Structured InstallError diagnostics (Python-side installer hardening)
# ---------------------------------------------------------------------------

def test_install_error_fields_for_settings_json_corrupt(tmp_path: Path, monkeypatch):
    settings_path, claude_json_path, state_path, bin_dir = _ready_install_paths(tmp_path, monkeypatch)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text("{not json", encoding="utf-8")

    with pytest.raises(installer_mod.InstallError) as excinfo:
        installer_mod.install(
            settings_path=settings_path,
            claude_json_path=claude_json_path,
            state_path=state_path,
            argv0=str(bin_dir / "claude-anyteam"),
        )

    _assert_install_error_fields(
        excinfo.value,
        title="Your Claude Code settings file is corrupted",
        explanation="Your Claude Code settings file is corrupted (not valid JSON).",
        action="settings.json.broken",
        severity="hard-stop",
    )
    assert "is not valid JSON" in (excinfo.value.details or "")


def test_install_error_fields_for_settings_json_locked(tmp_path: Path, monkeypatch):
    settings_path, claude_json_path, state_path, bin_dir = _ready_install_paths(tmp_path, monkeypatch)

    def _raise_locked(path: Path, _payload: dict[str, Any]) -> None:
        if path == settings_path.resolve():
            raise OSError(errno.EACCES, "Permission denied")
        raise AssertionError(f"unexpected write to {path}")

    monkeypatch.setattr(installer_mod, "_atomic_write_json", _raise_locked)

    with pytest.raises(installer_mod.InstallError) as excinfo:
        installer_mod.install(
            settings_path=settings_path,
            claude_json_path=claude_json_path,
            state_path=state_path,
            argv0=str(bin_dir / "claude-anyteam"),
        )

    _assert_install_error_fields(
        excinfo.value,
        title="Can't write your Claude Code settings file",
        explanation="likely Claude Code is running",
        action="Quit Claude Code",
        severity="hard-stop",
    )
    assert "Permission denied" in (excinfo.value.details or "")


def test_install_error_fields_for_claude_json_malformed(tmp_path: Path, monkeypatch):
    settings_path, claude_json_path, state_path, bin_dir = _ready_install_paths(tmp_path, monkeypatch)
    claude_json_path.parent.mkdir(parents=True, exist_ok=True)
    claude_json_path.write_text("[", encoding="utf-8")

    with pytest.raises(installer_mod.InstallError) as excinfo:
        installer_mod.install(
            settings_path=settings_path,
            claude_json_path=claude_json_path,
            state_path=state_path,
            argv0=str(bin_dir / "claude-anyteam"),
        )

    _assert_install_error_fields(
        excinfo.value,
        title="Your Claude Code config file is corrupted",
        explanation="Your Claude Code config",
        action=".claude.json.broken",
        severity="hard-stop",
    )
    assert "is not valid JSON" in (excinfo.value.details or "")
    assert not settings_path.exists(), "post-settings failure should roll back settings.json"


def test_install_error_fields_for_provider_version_probe_failure(
    tmp_path: Path,
    monkeypatch,
):
    codex_cli = installer_mod.CodexCliCheck(
        found=True,
        path=Path("/usr/local/bin/codex"),
        version=None,
        raw_output="codex exploded",
        signed_in=True,
        version_probe_error="`codex --version` exited with code 2.",
    )

    result, _claude_json_path, _state_path = _install_with_codex_stub(
        tmp_path,
        monkeypatch,
        codex_cli,
    )

    assert len(result.diagnostics) == 1
    _assert_install_error_fields(
        result.diagnostics[0],
        title="Found `codex` but couldn't read its version",
        explanation="didn't return a recognizable version",
        action="codex --version",
        severity="soft",
    )


def test_install_error_fields_for_provider_not_signed_in(tmp_path: Path, monkeypatch):
    settings_path, claude_json_path, state_path, bin_dir = _ready_install_paths(tmp_path, monkeypatch)
    codex_cli = _codex_cli_ready(signed_in=False)
    gemini_cli = _gemini_cli_ready(signed_in=False)

    with pytest.raises(installer_mod.InstallError) as excinfo:
        installer_mod.install(
            settings_path=settings_path,
            claude_json_path=claude_json_path,
            state_path=state_path,
            argv0=str(bin_dir / "claude-anyteam"),
            codex_cli_check_fn=lambda: codex_cli,
            codex_auth_check_fn=lambda: _auth(False),
            gemini_cli_check_fn=lambda: gemini_cli,
            gemini_auth_check_fn=lambda: _auth(False),
            kimi_cli_check_fn=lambda: _kimi_cli_missing(),
        )

    _assert_install_error_fields(
        excinfo.value,
        title="No provider CLI is signed in",
        explanation="none of the detected providers are ready",
        action="codex login",
        severity="blocker",
    )
    assert "gemini login" in excinfo.value.action
    assert "kimi login" in excinfo.value.action
    assert getattr(excinfo.value, "cli_exit_code") == installer_mod.INSTALL_ERROR_EXIT_NO_PROVIDER


def test_install_error_fields_for_plugin_registration_failure(tmp_path: Path, monkeypatch):
    settings_path, claude_json_path, state_path, bin_dir = _ready_install_paths(tmp_path, monkeypatch)

    result = installer_mod.install(
        settings_path=settings_path,
        claude_json_path=claude_json_path,
        state_path=state_path,
        argv0=str(bin_dir / "claude-anyteam"),
        register_plugin=True,
        plugin_registration_fn=lambda: installer_mod._plugin_registration_error(
            "`claude` was not found on PATH."
        ),
    )

    assert len(result.diagnostics) == 1
    _assert_install_error_fields(
        result.diagnostics[0],
        title="Couldn't register the Claude Code plugin",
        explanation="spawn shim still works without plugin registration",
        action="claude plugin install JonathanRosado/claude-anyteam",
        severity="soft",
    )


@pytest.mark.parametrize(
    ("platform", "expected_command"),
    [
        ("linux", "sudo apt install tmux"),
        ("darwin", "brew install tmux"),
    ],
)
def test_install_error_fields_for_multiplexer_missing(
    tmp_path: Path,
    monkeypatch,
    platform: str,
    expected_command: str,
):
    settings_path, claude_json_path, state_path, bin_dir = _fresh_paths(tmp_path)
    _make_executable(bin_dir / "claude-anyteam")
    _make_executable(bin_dir / "claude-anyteam-spawn-shim")
    _stub_prereq_missing(monkeypatch, platform=platform)
    _stub_provider_checks(monkeypatch)

    with pytest.raises(installer_mod.InstallError) as excinfo:
        installer_mod.install(
            settings_path=settings_path,
            claude_json_path=claude_json_path,
            state_path=state_path,
            argv0=str(bin_dir / "claude-anyteam"),
        )

    _assert_install_error_fields(
        excinfo.value,
        title="A required terminal tool is missing",
        explanation="claude-anyteam needs tmux on Linux/macOS",
        action=expected_command,
        severity="blocker",
    )


def test_install_windows_skips_multiplexer_failure_path(tmp_path: Path, monkeypatch):
    settings_path, claude_json_path, state_path, bin_dir = _fresh_paths(tmp_path)
    _make_executable(bin_dir / "claude-anyteam")
    _make_executable(bin_dir / "claude-anyteam-spawn-shim")
    monkeypatch.setattr(installer_mod, "_platform_name", lambda: "windows")
    _stub_provider_checks(monkeypatch)
    monkeypatch.setattr(installer_mod.shutil, "which", lambda name: None)

    result = installer_mod.install(
        settings_path=settings_path,
        claude_json_path=claude_json_path,
        state_path=state_path,
        argv0=str(bin_dir / "claude-anyteam"),
    )

    assert result.prereq is not None
    assert result.prereq.platform == "windows"
    assert result.prereq.found is True
    assert settings_path.exists()


def test_install_command_renders_structured_install_error_box(monkeypatch, capsys):
    structured = installer_mod.InstallError(
        title="Friendly title",
        explanation="Plain English explanation.",
        action="Run `fix-it`.\nThen retry.",
        severity="blocker",
        details="raw traceback line",
    )

    def _raise_install_error(**_kwargs: Any) -> installer_mod.InstallResult:
        raise structured

    monkeypatch.setattr(cli_mod, "install_settings", _raise_install_error)

    exit_code = cli_mod._install_command(no_input=True)

    assert exit_code == installer_mod.INSTALL_ERROR_EXIT_GENERIC
    err = capsys.readouterr().err
    assert "INSTALL FAILED" in err
    assert "Friendly title" in err
    assert "Plain English explanation." in err
    assert "Next step: Run `fix-it`." in err
    assert "Then retry." in err
    assert "Severity: blocker" in err
    assert "Raw details (for debugging):" in err


# ---------------------------------------------------------------------------
# Existing coverage, updated to stub the prereq check.
# ---------------------------------------------------------------------------

def test_install_help_documents_no_input(capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli_mod.main(["install", "--help"])

    assert excinfo.value.code == 0
    assert "--no-input" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("codex_cli", "gemini_cli", "expected"),
    [
        (
            _codex_cli_ready(signed_in=True),
            _gemini_cli_ready(signed_in=True),
            "\n".join(
                [
                    "Provider status",
                    "─────────────────────────────────────────────",
                    "              Installed?        Signed in?",
                    "Codex CLI     ✅ 0.124.0         ✅",
                    "Gemini CLI    ✅ 0.39.0          ✅",
                    "─────────────────────────────────────────────",
                ]
            ),
        ),
        (
            _codex_cli_ready(signed_in=False),
            _gemini_cli_ready(signed_in=False),
            "\n".join(
                [
                    "Provider status",
                    "─────────────────────────────────────────────",
                    "              Installed?        Signed in?",
                    "Codex CLI     ✅ 0.124.0         ❌",
                    "Gemini CLI    ✅ 0.39.0          ❌",
                    "─────────────────────────────────────────────",
                ]
            ),
        ),
        (
            _codex_cli_missing(),
            _gemini_cli_missing(),
            "\n".join(
                [
                    "Provider status",
                    "─────────────────────────────────────────────",
                    "              Installed?        Signed in?",
                    "Codex CLI     ❌" + (" " * 17) + "—",
                    "Gemini CLI    ❌" + (" " * 17) + "—",
                    "─────────────────────────────────────────────",
                ]
            ),
        ),
    ],
)
def test_render_provider_status_buckets(
    codex_cli: installer_mod.CodexCliCheck,
    gemini_cli: installer_mod.GeminiCliCheck,
    expected: str,
):
    assert installer_mod._render_provider_status(codex_cli, gemini_cli) == expected


@pytest.mark.parametrize(
    ("codex_cli", "gemini_cli", "expected"),
    [
        (
            _codex_cli_ready(signed_in=True),
            _gemini_cli_ready(signed_in=True),
            "Ready: Codex 0.124.0 · Gemini 0.39.0.",
        ),
        (
            _codex_cli_ready(signed_in=False),
            _gemini_cli_ready(signed_in=False),
            "Almost ready: Codex (needs sign-in) · Gemini (needs sign-in).",
        ),
        (
            _codex_cli_missing(),
            _gemini_cli_missing(),
            "Not ready: Codex (not installed) · Gemini (not installed).",
        ),
    ],
)
def test_render_provider_summary_buckets(
    codex_cli: installer_mod.CodexCliCheck,
    gemini_cli: installer_mod.GeminiCliCheck,
    expected: str,
):
    assert installer_mod._render_provider_summary(codex_cli, gemini_cli) == expected


def test_render_provider_walkthrough_buckets():
    assert installer_mod._render_provider_walkthrough(
        _codex_cli_ready(signed_in=True),
        _gemini_cli_ready(signed_in=True),
    ) == ""

    assert installer_mod._render_provider_walkthrough(
        _codex_cli_ready(signed_in=True),
        _gemini_cli_missing(),
    ) == "\n".join(
        [
            "Gemini CLI:",
            "  1. Install:  npm install -g @google/gemini-cli",
            "  2. Sign in:  gemini login    (or set GEMINI_API_KEY, or configure Vertex)",
            "  Docs: https://github.com/google-gemini/gemini-cli",
        ]
    )

    assert installer_mod._render_provider_walkthrough(
        _codex_cli_ready(signed_in=False),
        _gemini_cli_ready(signed_in=False),
    ) == "\n\n".join(
        [
            "\n".join(
                [
                    "Codex CLI:",
                    "  1. Sign in:  codex login",
                    "  Docs: https://github.com/openai/codex#getting-started",
                ]
            ),
            "\n".join(
                [
                    "Gemini CLI:",
                    "  1. Sign in:  gemini login    (or set GEMINI_API_KEY, or configure Vertex)",
                    "  Docs: https://github.com/google-gemini/gemini-cli",
                ]
            ),
        ]
    )


def test_install_prints_provider_status_before_settings_update(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    settings_path, claude_json_path, state_path, bin_dir = _fresh_paths(tmp_path)
    codex_binary = _make_executable(bin_dir / "claude-anyteam")
    _make_executable(bin_dir / "claude-anyteam-spawn-shim")

    _stub_prereq_found(monkeypatch)
    monkeypatch.setattr(installer_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(cli_mod.sys, "argv", [str(codex_binary)])

    assert cli_mod.main(_install_argv(settings_path, claude_json_path, state_path)) == 0

    stdout = capsys.readouterr().out
    expected_status = installer_mod._render_provider_status(
        _codex_cli_ready(signed_in=True),
        _gemini_cli_missing(),
    )
    expected_summary = installer_mod._render_provider_summary(
        _codex_cli_ready(signed_in=True),
        _gemini_cli_missing(),
    )
    updated_line = f"Updated {settings_path.resolve()}"

    assert stdout.count("Provider status") == 1
    assert (
        stdout.index(expected_status)
        < stdout.index(expected_summary)
        < stdout.index("Gemini CLI:")
        < stdout.index(updated_line)
    )


def test_install_with_no_providers_refuses_before_settings_mutation(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    settings_path, claude_json_path, state_path, bin_dir = _fresh_paths(tmp_path)
    codex_binary = _make_executable(bin_dir / "claude-anyteam")
    _make_executable(bin_dir / "claude-anyteam-spawn-shim")

    _stub_prereq_found(monkeypatch, stub_providers=False)
    _stub_provider_checks(
        monkeypatch,
        codex_cli=_codex_cli_missing(),
        codex_signed_in=False,
        gemini_cli=_gemini_cli_missing(),
        gemini_signed_in=False,
    )
    monkeypatch.setattr(cli_mod.sys, "argv", [str(codex_binary)])

    exit_code = cli_mod.main(_install_argv(settings_path, claude_json_path, state_path))

    captured = capsys.readouterr()
    assert exit_code == installer_mod.INSTALL_ERROR_EXIT_NO_PROVIDER
    assert "Not ready: Codex (not installed) · Gemini (not installed)." in captured.out
    assert "Codex CLI:" in captured.out
    assert "  1. Install:  npm install -g @openai/codex" in captured.out
    assert "Gemini CLI:" in captured.out
    assert "  1. Install:  npm install -g @google/gemini-cli" in captured.out
    assert "Refusing to install — no provider is ready." in captured.out
    assert "claude-anyteam install --force-empty" in captured.out
    assert "Updated " not in captured.out
    assert not settings_path.exists()
    assert not claude_json_path.exists()
    assert not state_path.exists()


def test_install_no_input_refuses_with_no_providers(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    settings_path, claude_json_path, state_path, bin_dir = _fresh_paths(tmp_path)
    codex_binary = _make_executable(bin_dir / "claude-anyteam")
    _make_executable(bin_dir / "claude-anyteam-spawn-shim")

    _stub_prereq_found(monkeypatch, stub_providers=False)
    _stub_provider_checks(
        monkeypatch,
        codex_cli=_codex_cli_missing(),
        codex_signed_in=False,
        gemini_cli=_gemini_cli_missing(),
        gemini_signed_in=False,
    )
    monkeypatch.setattr(cli_mod.sys, "argv", [str(codex_binary)])

    exit_code = cli_mod.main(
        _install_argv(settings_path, claude_json_path, state_path, "--no-input")
    )

    stdout = capsys.readouterr().out
    assert exit_code == installer_mod.INSTALL_ERROR_EXIT_NO_PROVIDER
    assert "Refusing to install — no provider is ready." in stdout
    assert "claude-anyteam install --force-empty" in stdout
    assert not settings_path.exists()
    assert not claude_json_path.exists()
    assert not state_path.exists()


def test_install_with_both_providers_signed_in_updates_settings(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    settings_path, claude_json_path, state_path, bin_dir = _fresh_paths(tmp_path)
    codex_binary = _make_executable(bin_dir / "claude-anyteam")
    _make_executable(bin_dir / "claude-anyteam-spawn-shim")

    _stub_prereq_found(monkeypatch, stub_providers=False)
    _stub_provider_checks(
        monkeypatch,
        codex_cli=_codex_cli_ready(signed_in=True),
        codex_signed_in=True,
        gemini_cli=_gemini_cli_ready(signed_in=True),
        gemini_signed_in=True,
    )
    monkeypatch.setattr(installer_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(cli_mod.sys, "argv", [str(codex_binary)])

    assert cli_mod.main(_install_argv(settings_path, claude_json_path, state_path)) == 0

    stdout = capsys.readouterr().out
    assert "Ready: Codex 0.124.0 · Gemini 0.39.0." in stdout
    assert "Refusing to install" not in stdout
    assert settings_path.exists()


def test_install_no_input_with_ready_provider_updates_settings(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    settings_path, claude_json_path, state_path, bin_dir = _fresh_paths(tmp_path)
    codex_binary = _make_executable(bin_dir / "claude-anyteam")
    _make_executable(bin_dir / "claude-anyteam-spawn-shim")

    _stub_prereq_found(monkeypatch, stub_providers=False)
    _stub_provider_checks(
        monkeypatch,
        codex_cli=_codex_cli_ready(signed_in=True),
        codex_signed_in=True,
        gemini_cli=_gemini_cli_missing(),
        gemini_signed_in=False,
    )
    monkeypatch.setattr(installer_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(cli_mod.sys, "argv", [str(codex_binary)])

    assert cli_mod.main(
        _install_argv(settings_path, claude_json_path, state_path, "--no-input")
    ) == 0

    stdout = capsys.readouterr().out
    assert "Ready: Codex 0.124.0 · Gemini (not installed)." in stdout
    assert "Refusing to install" not in stdout
    assert settings_path.exists()


def test_install_with_codex_signed_in_only_prints_gemini_walkthrough_and_updates_settings(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    settings_path, claude_json_path, state_path, bin_dir = _fresh_paths(tmp_path)
    codex_binary = _make_executable(bin_dir / "claude-anyteam")
    _make_executable(bin_dir / "claude-anyteam-spawn-shim")

    _stub_prereq_found(monkeypatch, stub_providers=False)
    _stub_provider_checks(
        monkeypatch,
        codex_cli=_codex_cli_ready(signed_in=True),
        codex_signed_in=True,
        gemini_cli=_gemini_cli_missing(),
        gemini_signed_in=False,
    )
    monkeypatch.setattr(installer_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(cli_mod.sys, "argv", [str(codex_binary)])

    assert cli_mod.main(_install_argv(settings_path, claude_json_path, state_path)) == 0

    stdout = capsys.readouterr().out
    assert "Ready: Codex 0.124.0 · Gemini (not installed)." in stdout
    assert "Gemini CLI:" in stdout
    assert "  1. Install:  npm install -g @google/gemini-cli" in stdout
    assert "Refusing to install" not in stdout
    assert settings_path.exists()


def test_install_with_gemini_signed_in_only_prints_codex_walkthrough_and_updates_settings(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    settings_path, claude_json_path, state_path, bin_dir = _fresh_paths(tmp_path)
    codex_binary = _make_executable(bin_dir / "claude-anyteam")
    _make_executable(bin_dir / "claude-anyteam-spawn-shim")

    _stub_prereq_found(monkeypatch, stub_providers=False)
    _stub_provider_checks(
        monkeypatch,
        codex_cli=_codex_cli_missing(),
        codex_signed_in=False,
        gemini_cli=_gemini_cli_ready(signed_in=True),
        gemini_signed_in=True,
    )
    monkeypatch.setattr(installer_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(cli_mod.sys, "argv", [str(codex_binary)])

    assert cli_mod.main(_install_argv(settings_path, claude_json_path, state_path)) == 0

    stdout = capsys.readouterr().out
    assert "Ready: Codex (not installed) · Gemini 0.39.0." in stdout
    assert "Codex CLI:" in stdout
    assert "  1. Install:  npm install -g @openai/codex" in stdout
    assert "Refusing to install" not in stdout
    assert settings_path.exists()


def test_install_with_both_installed_but_not_signed_in_refuses(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    settings_path, claude_json_path, state_path, bin_dir = _fresh_paths(tmp_path)
    codex_binary = _make_executable(bin_dir / "claude-anyteam")
    _make_executable(bin_dir / "claude-anyteam-spawn-shim")

    _stub_prereq_found(monkeypatch, stub_providers=False)
    _stub_provider_checks(
        monkeypatch,
        codex_cli=_codex_cli_ready(signed_in=False),
        codex_signed_in=False,
        gemini_cli=_gemini_cli_ready(signed_in=False),
        gemini_signed_in=False,
    )
    monkeypatch.setattr(cli_mod.sys, "argv", [str(codex_binary)])

    exit_code = cli_mod.main(_install_argv(settings_path, claude_json_path, state_path))

    stdout = capsys.readouterr().out
    assert exit_code == installer_mod.INSTALL_ERROR_EXIT_NO_PROVIDER
    assert "Almost ready: Codex (needs sign-in) · Gemini (needs sign-in)." in stdout
    assert "  1. Sign in:  codex login" in stdout
    assert "  1. Sign in:  gemini login    (or set GEMINI_API_KEY, or configure Vertex)" in stdout
    assert "Refusing to install — no provider is ready." in stdout
    assert "Updated " not in stdout
    assert not settings_path.exists()
    assert not claude_json_path.exists()
    assert not state_path.exists()


def test_install_force_empty_with_no_providers_updates_settings(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    settings_path, claude_json_path, state_path, bin_dir = _fresh_paths(tmp_path)
    codex_binary = _make_executable(bin_dir / "claude-anyteam")
    _make_executable(bin_dir / "claude-anyteam-spawn-shim")

    _stub_prereq_found(monkeypatch, stub_providers=False)
    _stub_provider_checks(
        monkeypatch,
        codex_cli=_codex_cli_missing(),
        codex_signed_in=False,
        gemini_cli=_gemini_cli_missing(),
        gemini_signed_in=False,
    )
    monkeypatch.setattr(installer_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(cli_mod.sys, "argv", [str(codex_binary)])

    assert cli_mod.main(
        _install_argv(settings_path, claude_json_path, state_path, "--force-empty")
    ) == 0

    stdout = capsys.readouterr().out
    assert (
        "Proceeding with --force-empty: claude-anyteam is installed but inert until a CLI is ready."
        in stdout
    )
    assert "Refusing to install" not in stdout
    assert settings_path.exists()
    assert json.loads(state_path.read_text(encoding="utf-8"))["force_empty_used"] is True


def test_install_creates_settings_and_sets_required_env_keys(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    settings_path, claude_json_path, state_path, bin_dir = _fresh_paths(tmp_path)
    codex_binary = _make_executable(bin_dir / "claude-anyteam")
    shim_binary = _make_executable(bin_dir / "claude-anyteam-spawn-shim")

    _stub_prereq_found(monkeypatch)
    monkeypatch.setattr(installer_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(cli_mod.sys, "argv", [str(codex_binary)])

    assert cli_mod.main(_install_argv(settings_path, claude_json_path, state_path)) == 0

    payload = json.loads(settings_path.read_text(encoding="utf-8"))
    assert payload == {
        "env": {
            installer_mod.TEAMMATE_COMMAND_KEY: str(shim_binary.resolve()),
            installer_mod.TEAMMATE_BINARY_KEY: str(codex_binary.resolve()),
            installer_mod.GEMINI_TEAMMATE_BINARY_KEY: str(codex_binary.resolve().with_name("gemini-anyteam")),
        },
        "permissions": {
            "allow": list(installer_mod.RECOMMENDED_ALLOWLIST_ENTRIES),
        }
    }

    stdout = capsys.readouterr().out
    assert f"Updated {settings_path.resolve()}" in stdout
    assert f"Set env.{installer_mod.TEAMMATE_COMMAND_KEY}={shim_binary.resolve()}" in stdout
    assert f"Set env.{installer_mod.TEAMMATE_BINARY_KEY}={codex_binary.resolve()}" in stdout
    assert "Restart Claude Code for the changes to take effect." in stdout
    assert "Permission allowlist written so spawning teams won't prompt." in stdout


def test_install_preserves_other_settings_and_env_entries(tmp_path: Path, monkeypatch):
    settings_path, claude_json_path, state_path, _ = _fresh_paths(tmp_path)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(
            {
                "theme": "dark",
                "env": {
                    "KEEP_ME": "yes",
                    installer_mod.TEAMMATE_COMMAND_KEY: "/old/shim",
                },
            }
        ),
        encoding="utf-8",
    )

    _stub_prereq_found(monkeypatch)
    monkeypatch.setattr(
        installer_mod.shutil,
        "which",
        lambda name: {
            installer_mod.SHIM_BASENAME: "/opt/tools/claude-anyteam-spawn-shim",
            installer_mod.BINARY_BASENAME: "/opt/tools/claude-anyteam",
        }.get(name),
    )

    result = installer_mod.install(
        settings_path=settings_path,
        claude_json_path=claude_json_path,
        state_path=state_path,
    )

    payload = json.loads(settings_path.read_text(encoding="utf-8"))
    assert payload["theme"] == "dark"
    assert payload["env"] == {
        "KEEP_ME": "yes",
        installer_mod.TEAMMATE_COMMAND_KEY: "/opt/tools/claude-anyteam-spawn-shim",
        installer_mod.TEAMMATE_BINARY_KEY: "/opt/tools/claude-anyteam",
        installer_mod.GEMINI_TEAMMATE_BINARY_KEY: "/opt/tools/gemini-anyteam",
    }
    assert payload["permissions"] == {
        "allow": list(installer_mod.RECOMMENDED_ALLOWLIST_ENTRIES),
    }
    assert result.paths.settings_path == settings_path.resolve()


def test_uninstall_removes_only_target_env_keys(
    tmp_path: Path,
    capsys,
):
    settings_path, claude_json_path, state_path, _ = _fresh_paths(tmp_path)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(
            {
                "theme": "dark",
                "env": {
                    "KEEP_ME": "yes",
                    installer_mod.TEAMMATE_COMMAND_KEY: "/opt/tools/claude-anyteam-spawn-shim",
                    installer_mod.TEAMMATE_BINARY_KEY: "/opt/tools/claude-anyteam",
                },
            }
        ),
        encoding="utf-8",
    )

    assert cli_mod.main(_uninstall_argv(settings_path, claude_json_path, state_path)) == 0

    payload = json.loads(settings_path.read_text(encoding="utf-8"))
    assert payload == {
        "theme": "dark",
        "env": {
            "KEEP_ME": "yes",
        },
    }

    stdout = capsys.readouterr().out
    assert f"Updated {settings_path.resolve()}" in stdout
    assert "Removed env.CLAUDE_CODE_TEAMMATE_COMMAND, env.CLAUDE_ANYTEAM_BINARY" in stdout
    assert "Restart Claude Code for the changes to take effect." in stdout


def test_main_install_uses_subcommand_path(tmp_path: Path, monkeypatch):
    settings_path, claude_json_path, state_path, bin_dir = _fresh_paths(tmp_path)
    codex_binary = _make_executable(bin_dir / "claude-anyteam")
    _make_executable(bin_dir / "claude-anyteam-spawn-shim")

    _stub_prereq_found(monkeypatch)
    monkeypatch.setattr(installer_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(cli_mod.sys, "argv", [str(codex_binary)])

    assert cli_mod.main(_install_argv(settings_path, claude_json_path, state_path)) == 0
    assert settings_path.exists()


# ---------------------------------------------------------------------------
# Prereq gate
# ---------------------------------------------------------------------------

def test_install_fails_when_multiplexer_missing(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    settings_path, claude_json_path, state_path, bin_dir = _fresh_paths(tmp_path)
    _make_executable(bin_dir / "claude-anyteam")
    _make_executable(bin_dir / "claude-anyteam-spawn-shim")

    _stub_prereq_missing(monkeypatch, platform="linux")
    monkeypatch.setattr(installer_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(cli_mod.sys, "argv", [str(bin_dir / "claude-anyteam")])

    exit_code = cli_mod.main(_install_argv(settings_path, claude_json_path, state_path))
    assert exit_code != 0

    captured = capsys.readouterr()
    assert "requires a terminal multiplexer" in captured.err
    assert "sudo apt install tmux" in captured.err
    assert not settings_path.exists()
    assert not claude_json_path.exists()
    assert not state_path.exists()


def test_install_skips_multiplexer_check_on_windows(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    settings_path, claude_json_path, state_path, bin_dir = _fresh_paths(tmp_path)
    codex_binary = _make_executable(bin_dir / "claude-anyteam")
    _make_executable(bin_dir / "claude-anyteam-spawn-shim")

    monkeypatch.setattr(installer_mod, "_platform_name", lambda: "windows")
    _stub_provider_checks(monkeypatch)
    monkeypatch.setattr(installer_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(cli_mod.sys, "argv", [str(codex_binary)])

    exit_code = cli_mod.main(_install_argv(settings_path, claude_json_path, state_path))
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "winget install psmux" not in captured.err
    assert "single-terminal mode" in captured.out
    assert settings_path.exists()


def test_windows_corrupt_settings_action_uses_powershell_paths(
    tmp_path: Path,
    monkeypatch,
):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(installer_mod.sys, "platform", "win32")

    error = installer_mod._settings_corrupt_error(
        home / ".claude" / "settings.json",
        "bad json",
        not_json=True,
    )

    assert "Move-Item -LiteralPath" in error.action
    assert "~\\.claude\\settings.json" in error.action
    assert "`mv " not in error.action


def test_windows_settings_lock_mentions_antivirus(
    tmp_path: Path,
    monkeypatch,
):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(installer_mod.sys, "platform", "win32")

    error = installer_mod._settings_write_error(
        home / ".claude" / "settings.json",
        OSError(errno.EBUSY, "resource busy"),
    )

    assert "antivirus" in error.explanation
    assert "PowerShell" in error.action
    assert "Windows Defender" in error.action


# ---------------------------------------------------------------------------
# teammateMode install branches
# ---------------------------------------------------------------------------

def test_install_writes_teammate_mode_when_absent(tmp_path: Path, monkeypatch, capsys):
    settings_path, claude_json_path, state_path, bin_dir = _fresh_paths(tmp_path)
    codex_binary = _make_executable(bin_dir / "claude-anyteam")
    _make_executable(bin_dir / "claude-anyteam-spawn-shim")

    _stub_prereq_found(monkeypatch)
    monkeypatch.setattr(installer_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(cli_mod.sys, "argv", [str(codex_binary)])

    assert cli_mod.main(_install_argv(settings_path, claude_json_path, state_path)) == 0

    claude_json = json.loads(claude_json_path.read_text(encoding="utf-8"))
    assert claude_json == {installer_mod.TEAMMATE_MODE_KEY: "tmux"}

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state == {
        "schema_version": installer_mod.STATE_SCHEMA_VERSION,
        "teammateMode_original": None,
        "teammateMode_set_by_anyteam": True,
        "settings_file_created_by_anyteam": True,
        "claude_json_created_by_anyteam": True,
        "force_empty_used": False,
        "permissions_allow_added_by_anyteam": list(installer_mod.RECOMMENDED_ALLOWLIST_ENTRIES),
        "permissions_allowlist_skipped": False,
        "permissions_created_by_anyteam": True,
        "permissions_allow_created_by_anyteam": True,
        "codex_cli_found": True,
        "codex_cli_version": "0.124.0",
        "codex_signed_in": True,
        "gemini_cli_found": False,
        "gemini_cli_version": None,
        "gemini_cli_capabilities": {},
        "gemini_signed_in": False,
    }

    stdout = capsys.readouterr().out
    assert f"Set {installer_mod.TEAMMATE_MODE_KEY}=\"tmux\" in {claude_json_path.resolve()}" in stdout


def test_install_is_noop_when_teammate_mode_already_tmux(tmp_path: Path, monkeypatch, capsys):
    settings_path, claude_json_path, state_path, bin_dir = _fresh_paths(tmp_path)
    codex_binary = _make_executable(bin_dir / "claude-anyteam")
    _make_executable(bin_dir / "claude-anyteam-spawn-shim")

    claude_json_path.parent.mkdir(parents=True, exist_ok=True)
    claude_json_path.write_text(
        json.dumps({"theme": "dark", installer_mod.TEAMMATE_MODE_KEY: "tmux"}) + "\n",
        encoding="utf-8",
    )

    _stub_prereq_found(monkeypatch)
    monkeypatch.setattr(installer_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(cli_mod.sys, "argv", [str(codex_binary)])

    assert cli_mod.main(_install_argv(settings_path, claude_json_path, state_path)) == 0

    claude_json = json.loads(claude_json_path.read_text(encoding="utf-8"))
    assert claude_json == {"theme": "dark", installer_mod.TEAMMATE_MODE_KEY: "tmux"}

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state == {
        "schema_version": installer_mod.STATE_SCHEMA_VERSION,
        "teammateMode_original": "tmux",
        "teammateMode_set_by_anyteam": False,
        "settings_file_created_by_anyteam": True,  # we did create settings.json
        "claude_json_created_by_anyteam": False,  # claude.json was pre-existing
        "force_empty_used": False,
        "permissions_allow_added_by_anyteam": list(installer_mod.RECOMMENDED_ALLOWLIST_ENTRIES),
        "permissions_allowlist_skipped": False,
        "permissions_created_by_anyteam": True,
        "permissions_allow_created_by_anyteam": True,
        "codex_cli_found": True,
        "codex_cli_version": "0.124.0",
        "codex_signed_in": True,
        "gemini_cli_found": False,
        "gemini_cli_version": None,
        "gemini_cli_capabilities": {},
        "gemini_signed_in": False,
    }

    stdout = capsys.readouterr().out
    assert f"{installer_mod.TEAMMATE_MODE_KEY} already \"tmux\"" in stdout


def test_install_prompts_and_overwrites_auto_when_accepted(tmp_path: Path, monkeypatch):
    settings_path, claude_json_path, state_path, bin_dir = _fresh_paths(tmp_path)
    _make_executable(bin_dir / "claude-anyteam")
    _make_executable(bin_dir / "claude-anyteam-spawn-shim")

    claude_json_path.parent.mkdir(parents=True, exist_ok=True)
    claude_json_path.write_text(
        json.dumps({"theme": "dark", installer_mod.TEAMMATE_MODE_KEY: "auto"}) + "\n",
        encoding="utf-8",
    )

    _stub_prereq_found(monkeypatch)
    monkeypatch.setattr(installer_mod.shutil, "which", lambda name: None)

    seen: list[str] = []

    def prompt(current: str) -> bool:
        seen.append(current)
        return True

    result = installer_mod.install(
        settings_path=settings_path,
        claude_json_path=claude_json_path,
        state_path=state_path,
        argv0=str(bin_dir / "claude-anyteam"),
        prompt_fn=prompt,
    )
    assert seen == ["auto"]
    assert result.teammate_mode is not None
    assert result.teammate_mode.previous_value == "auto"

    claude_json = json.loads(claude_json_path.read_text(encoding="utf-8"))
    assert claude_json == {"theme": "dark", installer_mod.TEAMMATE_MODE_KEY: "tmux"}

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state == {
        "schema_version": installer_mod.STATE_SCHEMA_VERSION,
        "teammateMode_original": "auto",
        "teammateMode_set_by_anyteam": True,
        "settings_file_created_by_anyteam": True,  # we created settings.json (no pre-existing)
        "claude_json_created_by_anyteam": False,  # claude.json was pre-existing
        "force_empty_used": False,
        "permissions_allow_added_by_anyteam": list(installer_mod.RECOMMENDED_ALLOWLIST_ENTRIES),
        "permissions_allowlist_skipped": False,
        "permissions_created_by_anyteam": True,
        "permissions_allow_created_by_anyteam": True,
        "codex_cli_found": True,
        "codex_cli_version": "0.124.0",
        "codex_signed_in": True,
        "gemini_cli_found": False,
        "gemini_cli_version": None,
        "gemini_cli_capabilities": {},
        "gemini_signed_in": False,
    }


def test_install_prompts_on_in_process_and_overwrites_when_accepted(tmp_path: Path, monkeypatch):
    settings_path, claude_json_path, state_path, bin_dir = _fresh_paths(tmp_path)
    _make_executable(bin_dir / "claude-anyteam")
    _make_executable(bin_dir / "claude-anyteam-spawn-shim")

    claude_json_path.parent.mkdir(parents=True, exist_ok=True)
    claude_json_path.write_text(
        json.dumps({installer_mod.TEAMMATE_MODE_KEY: "in-process"}) + "\n",
        encoding="utf-8",
    )

    _stub_prereq_found(monkeypatch)
    monkeypatch.setattr(installer_mod.shutil, "which", lambda name: None)

    installer_mod.install(
        settings_path=settings_path,
        claude_json_path=claude_json_path,
        state_path=state_path,
        argv0=str(bin_dir / "claude-anyteam"),
        prompt_fn=lambda _current: True,
    )

    claude_json = json.loads(claude_json_path.read_text(encoding="utf-8"))
    assert claude_json[installer_mod.TEAMMATE_MODE_KEY] == "tmux"

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["teammateMode_original"] == "in-process"
    assert state["teammateMode_set_by_anyteam"] is True


def test_install_rolls_back_when_prompt_declined(tmp_path: Path, monkeypatch):
    settings_path, claude_json_path, state_path, bin_dir = _fresh_paths(tmp_path)
    codex_binary = _make_executable(bin_dir / "claude-anyteam")
    _make_executable(bin_dir / "claude-anyteam-spawn-shim")

    claude_json_path.parent.mkdir(parents=True, exist_ok=True)
    claude_json_path.write_text(
        json.dumps({"theme": "dark", installer_mod.TEAMMATE_MODE_KEY: "auto"}) + "\n",
        encoding="utf-8",
    )

    _stub_prereq_found(monkeypatch)
    monkeypatch.setattr(installer_mod.shutil, "which", lambda name: None)

    with pytest.raises(installer_mod.InstallError) as excinfo:
        installer_mod.install(
            settings_path=settings_path,
            claude_json_path=claude_json_path,
            state_path=state_path,
            argv0=str(codex_binary),
            prompt_fn=lambda _current: False,
        )

    assert getattr(excinfo.value, "cli_exit_code", None) == 3

    # Rollback: settings.json must be absent (we just created it and then rolled back).
    assert not settings_path.exists()
    # claude.json untouched — still has 'auto'.
    claude_json = json.loads(claude_json_path.read_text(encoding="utf-8"))
    assert claude_json[installer_mod.TEAMMATE_MODE_KEY] == "auto"
    # State file never written.
    assert not state_path.exists()


def test_install_rolls_back_preserving_preexisting_env_block(tmp_path: Path, monkeypatch):
    settings_path, claude_json_path, state_path, bin_dir = _fresh_paths(tmp_path)
    codex_binary = _make_executable(bin_dir / "claude-anyteam")
    _make_executable(bin_dir / "claude-anyteam-spawn-shim")

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    pre_settings = {
        "theme": "dark",
        "env": {"KEEP_ME": "yes", installer_mod.TEAMMATE_COMMAND_KEY: "/old/shim"},
    }
    settings_path.write_text(json.dumps(pre_settings) + "\n", encoding="utf-8")

    claude_json_path.parent.mkdir(parents=True, exist_ok=True)
    claude_json_path.write_text(
        json.dumps({installer_mod.TEAMMATE_MODE_KEY: "in-process"}) + "\n",
        encoding="utf-8",
    )

    _stub_prereq_found(monkeypatch)
    monkeypatch.setattr(installer_mod.shutil, "which", lambda name: None)

    with pytest.raises(installer_mod.InstallError):
        installer_mod.install(
            settings_path=settings_path,
            claude_json_path=claude_json_path,
            state_path=state_path,
            argv0=str(codex_binary),
            prompt_fn=lambda _current: False,
        )

    restored = json.loads(settings_path.read_text(encoding="utf-8"))
    assert restored == pre_settings, "rollback must restore the original env block byte-for-byte"
    claude_json = json.loads(claude_json_path.read_text(encoding="utf-8"))
    assert claude_json[installer_mod.TEAMMATE_MODE_KEY] == "in-process"
    assert not state_path.exists()


def test_install_assume_yes_bypasses_prompt(tmp_path: Path, monkeypatch):
    settings_path, claude_json_path, state_path, bin_dir = _fresh_paths(tmp_path)
    codex_binary = _make_executable(bin_dir / "claude-anyteam")
    _make_executable(bin_dir / "claude-anyteam-spawn-shim")

    claude_json_path.parent.mkdir(parents=True, exist_ok=True)
    claude_json_path.write_text(
        json.dumps({installer_mod.TEAMMATE_MODE_KEY: "auto"}) + "\n",
        encoding="utf-8",
    )

    _stub_prereq_found(monkeypatch)
    monkeypatch.setattr(installer_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(cli_mod.sys, "argv", [str(codex_binary)])

    assert cli_mod.main(
        _install_argv(settings_path, claude_json_path, state_path, "--assume-yes")
    ) == 0

    claude_json = json.loads(claude_json_path.read_text(encoding="utf-8"))
    assert claude_json[installer_mod.TEAMMATE_MODE_KEY] == "tmux"


# ---------------------------------------------------------------------------
# teammateMode uninstall branches
# ---------------------------------------------------------------------------

def _write_state(state_path: Path, state: dict[str, Any]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state) + "\n", encoding="utf-8")


def test_uninstall_restores_original_auto(tmp_path: Path):
    settings_path, claude_json_path, state_path, _ = _fresh_paths(tmp_path)
    claude_json_path.parent.mkdir(parents=True, exist_ok=True)
    claude_json_path.write_text(
        json.dumps({"theme": "dark", installer_mod.TEAMMATE_MODE_KEY: "tmux"}) + "\n",
        encoding="utf-8",
    )
    _write_state(
        state_path,
        {
            "schema_version": installer_mod.STATE_SCHEMA_VERSION,
            "teammateMode_original": "auto",
            "teammateMode_set_by_anyteam": True,
        },
    )

    assert cli_mod.main(_uninstall_argv(settings_path, claude_json_path, state_path)) == 0

    claude_json = json.loads(claude_json_path.read_text(encoding="utf-8"))
    assert claude_json == {"theme": "dark", installer_mod.TEAMMATE_MODE_KEY: "auto"}
    assert not state_path.exists()


def test_uninstall_removes_key_when_we_added_it(tmp_path: Path):
    settings_path, claude_json_path, state_path, _ = _fresh_paths(tmp_path)
    claude_json_path.parent.mkdir(parents=True, exist_ok=True)
    claude_json_path.write_text(
        json.dumps({"theme": "dark", installer_mod.TEAMMATE_MODE_KEY: "tmux"}) + "\n",
        encoding="utf-8",
    )
    _write_state(
        state_path,
        {
            "schema_version": installer_mod.STATE_SCHEMA_VERSION,
            "teammateMode_original": None,
            "teammateMode_set_by_anyteam": True,
        },
    )

    assert cli_mod.main(_uninstall_argv(settings_path, claude_json_path, state_path)) == 0

    claude_json = json.loads(claude_json_path.read_text(encoding="utf-8"))
    assert installer_mod.TEAMMATE_MODE_KEY not in claude_json
    assert claude_json == {"theme": "dark"}
    assert not state_path.exists()


def test_uninstall_leaves_teammate_mode_alone_when_not_managed(tmp_path: Path):
    settings_path, claude_json_path, state_path, _ = _fresh_paths(tmp_path)
    claude_json_path.parent.mkdir(parents=True, exist_ok=True)
    claude_json_path.write_text(
        json.dumps({installer_mod.TEAMMATE_MODE_KEY: "tmux"}) + "\n",
        encoding="utf-8",
    )
    _write_state(
        state_path,
        {
            "schema_version": installer_mod.STATE_SCHEMA_VERSION,
            "teammateMode_original": "tmux",
            "teammateMode_set_by_anyteam": False,
        },
    )

    assert cli_mod.main(_uninstall_argv(settings_path, claude_json_path, state_path)) == 0

    claude_json = json.loads(claude_json_path.read_text(encoding="utf-8"))
    assert claude_json == {installer_mod.TEAMMATE_MODE_KEY: "tmux"}
    assert not state_path.exists()


def test_uninstall_graceful_when_state_missing(tmp_path: Path, capsys):
    settings_path, claude_json_path, state_path, _ = _fresh_paths(tmp_path)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(
            {
                "env": {
                    "KEEP_ME": "yes",
                    installer_mod.TEAMMATE_COMMAND_KEY: "/opt/tools/claude-anyteam-spawn-shim",
                    installer_mod.TEAMMATE_BINARY_KEY: "/opt/tools/claude-anyteam",
                }
            }
        ),
        encoding="utf-8",
    )
    claude_json_path.parent.mkdir(parents=True, exist_ok=True)
    claude_json_path.write_text(
        json.dumps({installer_mod.TEAMMATE_MODE_KEY: "tmux"}) + "\n",
        encoding="utf-8",
    )
    # No state file — mirrors an existing install that predates this feature.

    assert cli_mod.main(_uninstall_argv(settings_path, claude_json_path, state_path)) == 0

    # claude.json untouched (we don't know whether we "own" the value).
    claude_json = json.loads(claude_json_path.read_text(encoding="utf-8"))
    assert claude_json == {installer_mod.TEAMMATE_MODE_KEY: "tmux"}

    # settings.json env block still unwound correctly.
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    assert settings == {"env": {"KEEP_ME": "yes"}}

    stdout = capsys.readouterr().out
    assert "Restart Claude Code for the changes to take effect." in stdout


# ---------------------------------------------------------------------------
# Round-trip smoke: install → inspect state → uninstall → everything reverted.
# ---------------------------------------------------------------------------

def test_install_then_uninstall_round_trip_preserves_user_state(tmp_path: Path, monkeypatch):
    settings_path, claude_json_path, state_path, bin_dir = _fresh_paths(tmp_path)
    codex_binary = _make_executable(bin_dir / "claude-anyteam")
    _make_executable(bin_dir / "claude-anyteam-spawn-shim")

    pre_settings = {"theme": "dark", "env": {"KEEP_ME": "yes"}}
    pre_claude_json = {
        "projects": {"/home/user/foo": {"allowedTools": []}},
        installer_mod.TEAMMATE_MODE_KEY: "auto",
    }
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(pre_settings) + "\n", encoding="utf-8")
    claude_json_path.parent.mkdir(parents=True, exist_ok=True)
    claude_json_path.write_text(json.dumps(pre_claude_json) + "\n", encoding="utf-8")

    _stub_prereq_found(monkeypatch)
    monkeypatch.setattr(installer_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(cli_mod.sys, "argv", [str(codex_binary)])

    assert cli_mod.main(
        _install_argv(settings_path, claude_json_path, state_path, "--assume-yes")
    ) == 0

    # State: we overwrote auto.
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["teammateMode_original"] == "auto"
    assert state["teammateMode_set_by_anyteam"] is True

    assert cli_mod.main(_uninstall_argv(settings_path, claude_json_path, state_path)) == 0

    # Everything back to pre-install. settings.json: env block stripped but
    # other keys intact.
    post_settings = json.loads(settings_path.read_text(encoding="utf-8"))
    assert post_settings == pre_settings
    post_claude_json = json.loads(claude_json_path.read_text(encoding="utf-8"))
    assert post_claude_json == pre_claude_json
    assert not state_path.exists()


# ---------------------------------------------------------------------------
# Uninstall audit — "leave no trace" coverage (schema v2)
# ---------------------------------------------------------------------------

def test_uninstall_removes_settings_file_when_we_created_it(tmp_path: Path, monkeypatch):
    """Install on a fresh system creates settings.json; uninstall should unlink
    it when no non-managed keys remain."""
    settings_path, claude_json_path, state_path, bin_dir = _fresh_paths(tmp_path)
    codex_binary = _make_executable(bin_dir / "claude-anyteam")
    _make_executable(bin_dir / "claude-anyteam-spawn-shim")

    _stub_prereq_found(monkeypatch)
    monkeypatch.setattr(installer_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(cli_mod.sys, "argv", [str(codex_binary)])

    assert cli_mod.main(_install_argv(settings_path, claude_json_path, state_path)) == 0
    assert settings_path.exists()

    assert cli_mod.main(_uninstall_argv(settings_path, claude_json_path, state_path)) == 0
    assert not settings_path.exists(), "settings.json we created + that would be empty should be removed"


def test_uninstall_keeps_settings_file_when_user_had_one(tmp_path: Path, monkeypatch):
    """If settings.json existed pre-install with user keys, uninstall strips our
    keys but leaves the file in place (even if `env` itself becomes empty)."""
    settings_path, claude_json_path, state_path, bin_dir = _fresh_paths(tmp_path)
    codex_binary = _make_executable(bin_dir / "claude-anyteam")
    _make_executable(bin_dir / "claude-anyteam-spawn-shim")

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    pre_settings = {"theme": "dark", "env": {"KEEP_ME": "yes"}}
    settings_path.write_text(json.dumps(pre_settings) + "\n", encoding="utf-8")

    _stub_prereq_found(monkeypatch)
    monkeypatch.setattr(installer_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(cli_mod.sys, "argv", [str(codex_binary)])

    assert cli_mod.main(_install_argv(settings_path, claude_json_path, state_path)) == 0
    assert cli_mod.main(_uninstall_argv(settings_path, claude_json_path, state_path)) == 0

    assert settings_path.exists()
    assert json.loads(settings_path.read_text(encoding="utf-8")) == pre_settings


def test_uninstall_removes_claude_json_when_we_created_it(tmp_path: Path, monkeypatch):
    """If we added the only key claude.json held, uninstall removes the key AND
    deletes the now-empty file (we created it)."""
    settings_path, claude_json_path, state_path, bin_dir = _fresh_paths(tmp_path)
    codex_binary = _make_executable(bin_dir / "claude-anyteam")
    _make_executable(bin_dir / "claude-anyteam-spawn-shim")

    _stub_prereq_found(monkeypatch)
    monkeypatch.setattr(installer_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(cli_mod.sys, "argv", [str(codex_binary)])

    assert cli_mod.main(_install_argv(settings_path, claude_json_path, state_path)) == 0
    assert claude_json_path.exists()

    assert cli_mod.main(_uninstall_argv(settings_path, claude_json_path, state_path)) == 0
    assert not claude_json_path.exists(), "claude.json we created + now empty should be removed"


def test_uninstall_keeps_claude_json_when_user_had_one(tmp_path: Path, monkeypatch):
    """Even if uninstall pops teammateMode and other keys remain, the file stays."""
    settings_path, claude_json_path, state_path, bin_dir = _fresh_paths(tmp_path)
    codex_binary = _make_executable(bin_dir / "claude-anyteam")
    _make_executable(bin_dir / "claude-anyteam-spawn-shim")

    claude_json_path.parent.mkdir(parents=True, exist_ok=True)
    pre_claude = {"somethingElse": "x"}
    claude_json_path.write_text(json.dumps(pre_claude) + "\n", encoding="utf-8")

    _stub_prereq_found(monkeypatch)
    monkeypatch.setattr(installer_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(cli_mod.sys, "argv", [str(codex_binary)])

    assert cli_mod.main(_install_argv(settings_path, claude_json_path, state_path)) == 0
    assert cli_mod.main(_uninstall_argv(settings_path, claude_json_path, state_path)) == 0

    assert claude_json_path.exists()
    assert json.loads(claude_json_path.read_text(encoding="utf-8")) == pre_claude


def test_uninstall_removes_empty_plugin_data_dir(tmp_path: Path, monkeypatch):
    """After state file delete, the plugin-data dir should be rmdir'd if empty."""
    settings_path, claude_json_path, state_path, bin_dir = _fresh_paths(tmp_path)
    codex_binary = _make_executable(bin_dir / "claude-anyteam")
    _make_executable(bin_dir / "claude-anyteam-spawn-shim")

    _stub_prereq_found(monkeypatch)
    monkeypatch.setattr(installer_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(cli_mod.sys, "argv", [str(codex_binary)])

    assert cli_mod.main(_install_argv(settings_path, claude_json_path, state_path)) == 0
    plugin_data_dir = state_path.parent
    assert plugin_data_dir.exists()

    assert cli_mod.main(_uninstall_argv(settings_path, claude_json_path, state_path)) == 0
    assert not plugin_data_dir.exists(), "empty plugin-data dir should be rmdir'd"


def test_uninstall_leaves_plugin_data_dir_when_nonempty(tmp_path: Path, monkeypatch):
    """If the user placed their own file in our plugin-data dir, rmdir refuses
    (OSError on ENOTEMPTY) and we leave it alone."""
    settings_path, claude_json_path, state_path, bin_dir = _fresh_paths(tmp_path)
    codex_binary = _make_executable(bin_dir / "claude-anyteam")
    _make_executable(bin_dir / "claude-anyteam-spawn-shim")

    _stub_prereq_found(monkeypatch)
    monkeypatch.setattr(installer_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(cli_mod.sys, "argv", [str(codex_binary)])

    assert cli_mod.main(_install_argv(settings_path, claude_json_path, state_path)) == 0

    plugin_data_dir = state_path.parent
    stranger_file = plugin_data_dir / "user-notes.txt"
    stranger_file.write_text("user's own file\n", encoding="utf-8")

    assert cli_mod.main(_uninstall_argv(settings_path, claude_json_path, state_path)) == 0
    assert plugin_data_dir.exists()
    assert stranger_file.exists()
    assert stranger_file.read_text(encoding="utf-8") == "user's own file\n"
    # Our state file IS gone; the user's file is not.
    assert not state_path.exists()


def test_uninstall_leaves_plugins_and_data_parents_alone(tmp_path: Path, monkeypatch):
    """rmdir on our leaf dir must not cascade into plugins/data/ or plugins/."""
    settings_path, claude_json_path, state_path, bin_dir = _fresh_paths(tmp_path)
    codex_binary = _make_executable(bin_dir / "claude-anyteam")
    _make_executable(bin_dir / "claude-anyteam-spawn-shim")

    _stub_prereq_found(monkeypatch)
    monkeypatch.setattr(installer_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(cli_mod.sys, "argv", [str(codex_binary)])

    assert cli_mod.main(_install_argv(settings_path, claude_json_path, state_path)) == 0

    plugin_data_dir = state_path.parent  # .../plugins/data/claude-anyteam-claude-anyteam
    data_dir = plugin_data_dir.parent     # .../plugins/data
    plugins_dir = data_dir.parent         # .../plugins

    # Plant a sibling in data/ so the parent is non-empty even after we clean up
    # our leaf — proves we'd have stopped at our own dir regardless.
    sibling = data_dir / "some-other-plugin"
    sibling.mkdir()

    assert cli_mod.main(_uninstall_argv(settings_path, claude_json_path, state_path)) == 0

    assert not plugin_data_dir.exists()
    assert data_dir.exists()
    assert plugins_dir.exists()
    assert sibling.exists()


def test_uninstall_refuses_on_corrupted_state(tmp_path: Path):
    """Malformed state ('teammateMode_original' of wrong type) → exit code 4,
    state file preserved, claude.json untouched."""
    settings_path, claude_json_path, state_path, _ = _fresh_paths(tmp_path)
    claude_json_path.parent.mkdir(parents=True, exist_ok=True)
    claude_json_path.write_text(
        json.dumps({installer_mod.TEAMMATE_MODE_KEY: "tmux"}) + "\n",
        encoding="utf-8",
    )
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "schema_version": installer_mod.STATE_SCHEMA_VERSION,
                "teammateMode_original": 42,  # malformed: not a string or null
                "teammateMode_set_by_anyteam": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    exit_code = cli_mod.main(_uninstall_argv(settings_path, claude_json_path, state_path))
    assert exit_code == 4

    # State file preserved for the user to inspect.
    assert state_path.exists()
    # claude.json untouched.
    claude_json = json.loads(claude_json_path.read_text(encoding="utf-8"))
    assert claude_json == {installer_mod.TEAMMATE_MODE_KEY: "tmux"}


def test_uninstall_state_schema_v1_forward_compat(tmp_path: Path, monkeypatch):
    """A state file written by the v1 installer (no created-flags) must be
    handled safely by the v2 uninstaller: missing flags default to False, so
    we never delete files we didn't record creating."""
    settings_path, claude_json_path, state_path, _ = _fresh_paths(tmp_path)

    # Simulate a v1 install result: user had neither file pre-install but the
    # v1 installer didn't track that. Post-install, both files exist and the
    # v1 state file is on disk with only the v1 fields.
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(
            {
                "env": {
                    installer_mod.TEAMMATE_COMMAND_KEY: "/opt/tools/claude-anyteam-spawn-shim",
                    installer_mod.TEAMMATE_BINARY_KEY: "/opt/tools/claude-anyteam",
                }
            }
        ),
        encoding="utf-8",
    )
    claude_json_path.parent.mkdir(parents=True, exist_ok=True)
    claude_json_path.write_text(
        json.dumps({installer_mod.TEAMMATE_MODE_KEY: "tmux"}) + "\n",
        encoding="utf-8",
    )
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "teammateMode_original": None,
                "teammateMode_set_by_anyteam": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert cli_mod.main(_uninstall_argv(settings_path, claude_json_path, state_path)) == 0

    # teammateMode removed (the v1 state knows we set it). But because the
    # created-flags were missing (v1), we default to False and leave the now-
    # empty files on disk.
    assert settings_path.exists()
    assert claude_json_path.exists()
    assert json.loads(claude_json_path.read_text(encoding="utf-8")) == {}


def test_uninstall_roundtrip_on_fresh_system_leaves_no_trace(tmp_path: Path, monkeypatch):
    """Headline audit test: install on a genuinely fresh system then uninstall
    → no file or directory under ~/.claude that we created should remain."""
    settings_path, claude_json_path, state_path, bin_dir = _fresh_paths(tmp_path)
    codex_binary = _make_executable(bin_dir / "claude-anyteam")
    _make_executable(bin_dir / "claude-anyteam-spawn-shim")

    _stub_prereq_found(monkeypatch)
    monkeypatch.setattr(installer_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(cli_mod.sys, "argv", [str(codex_binary)])

    # Pre-state: no ~/.claude anywhere under tmp_path/home.
    home_claude = tmp_path / "home" / ".claude"
    assert not home_claude.exists()

    assert cli_mod.main(_install_argv(settings_path, claude_json_path, state_path)) == 0
    # Post-install: all three artifacts present.
    assert settings_path.exists()
    assert claude_json_path.exists()
    assert state_path.exists()

    assert cli_mod.main(_uninstall_argv(settings_path, claude_json_path, state_path)) == 0

    # Post-uninstall: all three artifacts gone.
    assert not settings_path.exists()
    assert not claude_json_path.exists()
    assert not state_path.exists()
    assert not state_path.parent.exists(), "plugin-data dir should be cleaned up"


def test_uninstall_is_idempotent(tmp_path: Path, monkeypatch):
    """install → uninstall → uninstall: the second uninstall is a clean no-op."""
    settings_path, claude_json_path, state_path, bin_dir = _fresh_paths(tmp_path)
    codex_binary = _make_executable(bin_dir / "claude-anyteam")
    _make_executable(bin_dir / "claude-anyteam-spawn-shim")

    _stub_prereq_found(monkeypatch)
    monkeypatch.setattr(installer_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(cli_mod.sys, "argv", [str(codex_binary)])

    assert cli_mod.main(_install_argv(settings_path, claude_json_path, state_path)) == 0
    assert cli_mod.main(_uninstall_argv(settings_path, claude_json_path, state_path)) == 0
    # Second uninstall: everything already gone; should still return 0, not raise.
    assert cli_mod.main(_uninstall_argv(settings_path, claude_json_path, state_path)) == 0


# ---------------------------------------------------------------------------
# Codex CLI prereq check (informational — non-blocking)
# ---------------------------------------------------------------------------

def _install_with_codex_stub(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    codex_cli: installer_mod.CodexCliCheck,
) -> tuple[installer_mod.InstallResult, Path, Path]:
    settings_path, claude_json_path, state_path, bin_dir = _fresh_paths(tmp_path)
    codex_binary = _make_executable(bin_dir / "claude-anyteam")
    _make_executable(bin_dir / "claude-anyteam-spawn-shim")

    _stub_prereq_found(monkeypatch)
    monkeypatch.setattr(installer_mod.shutil, "which", lambda name: None)

    result = installer_mod.install(
        settings_path=settings_path,
        claude_json_path=claude_json_path,
        state_path=state_path,
        argv0=str(codex_binary),
        prompt_fn=lambda _current: True,
        codex_cli_check_fn=lambda: codex_cli,
    )
    return result, claude_json_path, state_path



def _install_with_gemini_stub(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    gemini_cli: installer_mod.GeminiCliCheck,
) -> tuple[installer_mod.InstallResult, Path, Path]:
    settings_path, claude_json_path, state_path, bin_dir = _fresh_paths(tmp_path)
    codex_binary = _make_executable(bin_dir / "claude-anyteam")
    _make_executable(bin_dir / "claude-anyteam-spawn-shim")

    _stub_prereq_found(monkeypatch)
    monkeypatch.setattr(installer_mod.shutil, "which", lambda name: None)

    result = installer_mod.install(
        settings_path=settings_path,
        claude_json_path=claude_json_path,
        state_path=state_path,
        argv0=str(codex_binary),
        prompt_fn=lambda _current: True,
        codex_cli_check_fn=lambda: installer_mod.CodexCliCheck(
            found=True,
            path=Path("/usr/local/bin/codex"),
            version="0.124.0",
            raw_output="codex-cli 0.124.0",
        ),
        gemini_cli_check_fn=lambda: gemini_cli,
        gemini_auth_check_fn=lambda: _auth(True),
    )
    return result, claude_json_path, state_path


def test_install_detects_codex_cli_when_present(tmp_path: Path, monkeypatch):
    codex_cli = installer_mod.CodexCliCheck(
        found=True,
        path=Path("/usr/local/bin/codex"),
        version="0.124.0",
        raw_output="codex-cli 0.124.0",
    )
    result, _claude_json_path, state_path = _install_with_codex_stub(tmp_path, monkeypatch, codex_cli)

    assert result.codex_cli is not None
    assert result.codex_cli.found is True
    assert result.codex_cli.version == "0.124.0"

    message = installer_mod.format_install_message(result)
    assert "Codex CLI     ✅ 0.124.0" in message
    assert "Ready: Codex 0.124.0 · Gemini (not installed)." in message
    assert "Detected Codex CLI" not in message
    assert "Warning: detected Codex" not in message

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["codex_cli_found"] is True
    assert state["codex_cli_version"] == "0.124.0"
    assert state["codex_signed_in"] is True


def test_install_warns_when_codex_cli_missing_but_still_succeeds(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    settings_path, claude_json_path, state_path, bin_dir = _fresh_paths(tmp_path)
    codex_binary = _make_executable(bin_dir / "claude-anyteam")
    _make_executable(bin_dir / "claude-anyteam-spawn-shim")

    _stub_prereq_found(monkeypatch)
    monkeypatch.setattr(installer_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        installer_mod,
        "_check_codex_cli",
        lambda: installer_mod.CodexCliCheck(
            found=False, path=None, version=None, raw_output=None
        ),
    )
    monkeypatch.setattr(installer_mod, "_check_gemini_cli", lambda: _gemini_cli_ready())
    monkeypatch.setattr(installer_mod, "_check_gemini_auth", lambda: _auth(True))
    monkeypatch.setattr(cli_mod.sys, "argv", [str(codex_binary)])

    exit_code = cli_mod.main(
        _install_argv(settings_path, claude_json_path, state_path, "--assume-yes")
    )
    assert exit_code == 0, "missing codex-cli must not block install"

    stdout = capsys.readouterr().out
    assert "Ready: Codex (not installed) · Gemini 0.39.0." in stdout
    assert "Codex CLI:" in stdout
    assert "1. Install:  npm install -g @openai/codex" in stdout
    assert "https://github.com/openai/codex" in stdout
    assert "Warning: the OpenAI Codex CLI" not in stdout

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["codex_cli_found"] is False
    assert state["codex_cli_version"] is None
    assert state["codex_signed_in"] is False


def test_install_handles_codex_version_parse_failure(tmp_path: Path, monkeypatch):
    """Parse-fail warns in plain English but does not block install."""
    codex_cli = installer_mod.CodexCliCheck(
        found=True,
        path=Path("/usr/local/bin/codex"),
        version=None,
        raw_output="weird unexpected output",
        version_probe_error="`codex --version` output was not recognizable: weird unexpected output",
    )
    result, _claude_json_path, state_path = _install_with_codex_stub(tmp_path, monkeypatch, codex_cli)

    assert result.codex_cli is not None
    assert result.codex_cli.found is True
    assert result.codex_cli.version is None

    message = installer_mod.format_install_message(result)
    assert "Codex CLI     ✅" in message
    assert "Ready: Codex · Gemini (not installed)." in message
    assert "Detected Codex CLI" not in message
    assert "Warning: Found `codex` but couldn't read its version" in message
    assert "The installer will warn but continue" in message

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["codex_cli_found"] is True
    assert state["codex_cli_version"] is None


def test_install_refuses_when_codex_cli_below_floor_and_no_provider_ready(
    tmp_path: Path,
    monkeypatch,
):
    codex_cli = installer_mod.CodexCliCheck(
        found=True,
        path=Path("/usr/local/bin/codex"),
        version="0.118.0",
        raw_output="codex-cli 0.118.0",
    )
    settings_path, claude_json_path, state_path, bin_dir = _fresh_paths(tmp_path)
    codex_binary = _make_executable(bin_dir / "claude-anyteam")
    _make_executable(bin_dir / "claude-anyteam-spawn-shim")

    _stub_prereq_found(monkeypatch)

    with pytest.raises(installer_mod.InstallError) as excinfo:
        installer_mod.install(
            settings_path=settings_path,
            claude_json_path=claude_json_path,
            state_path=state_path,
            argv0=str(codex_binary),
            prompt_fn=lambda _current: True,
            codex_cli_check_fn=lambda: codex_cli,
            codex_auth_check_fn=lambda: _auth(True),
            gemini_cli_check_fn=lambda: _gemini_cli_missing(),
            gemini_auth_check_fn=lambda: _auth(False),
        )

    assert getattr(excinfo.value, "cli_exit_code", None) == installer_mod.INSTALL_ERROR_EXIT_NO_PROVIDER
    message = str(excinfo.value)
    assert "Not ready: Codex (upgrade — 0.118.0 < 0.120.0 floor) · Gemini (not installed)." in message
    assert "Upgrade:  npm install -g @openai/codex (detected 0.118.0, need ≥ 0.120.0)" in message
    assert installer_mod.CODEX_CLI_DOCS_URL in message
    assert not settings_path.exists()
    assert not claude_json_path.exists()
    assert not state_path.exists()


def test_install_accepts_codex_cli_exactly_at_floor(tmp_path: Path, monkeypatch):
    codex_cli = installer_mod.CodexCliCheck(
        found=True,
        path=Path("/usr/local/bin/codex"),
        version="0.120.0",
        raw_output="codex-cli 0.120.0",
    )
    result, _claude_json_path, _state_path = _install_with_codex_stub(tmp_path, monkeypatch, codex_cli)

    message = installer_mod.format_install_message(result)
    assert "Ready: Codex 0.120.0 · Gemini (not installed)." in message
    assert "Warning: detected Codex" not in message


def test_install_combines_tmux_and_codex_warnings_on_tmux_halt(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    """User missing BOTH tmux and codex-cli should see both warnings at once."""
    settings_path, claude_json_path, state_path, bin_dir = _fresh_paths(tmp_path)
    _make_executable(bin_dir / "claude-anyteam")
    _make_executable(bin_dir / "claude-anyteam-spawn-shim")

    _stub_prereq_missing(monkeypatch, platform="linux")
    monkeypatch.setattr(installer_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        installer_mod,
        "_check_codex_cli",
        lambda: installer_mod.CodexCliCheck(
            found=False, path=None, version=None, raw_output=None
        ),
    )
    monkeypatch.setattr(cli_mod.sys, "argv", [str(bin_dir / "claude-anyteam")])

    exit_code = cli_mod.main(_install_argv(settings_path, claude_json_path, state_path))
    assert exit_code != 0

    err = capsys.readouterr().err
    assert "requires a terminal multiplexer" in err
    assert "sudo apt install tmux" in err
    assert "Additionally:" in err
    assert "Codex CLI (`codex`) was not found" in err



# ---------------------------------------------------------------------------
# Gemini CLI prereq check (informational — non-blocking)
# ---------------------------------------------------------------------------

def test_install_detects_gemini_cli_when_present(tmp_path: Path, monkeypatch):
    gemini_cli = installer_mod.GeminiCliCheck(
        found=True,
        path=Path("/usr/local/bin/gemini"),
        version="0.3.1",
        raw_output="0.3.1",
        capabilities=_GEMINI_ALL_CAPABILITIES,
    )
    result, _claude_json_path, state_path = _install_with_gemini_stub(
        tmp_path, monkeypatch, gemini_cli
    )

    assert result.gemini_cli is not None
    assert result.gemini_cli.found is True
    assert result.gemini_cli.version == "0.3.1"

    message = installer_mod.format_install_message(result)
    assert "Gemini CLI    ✅ 0.3.1" in message
    assert "Ready: Codex 0.124.0 · Gemini 0.3.1." in message
    assert "Warning: the Gemini CLI" not in message

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["gemini_cli_found"] is True
    assert state["gemini_cli_version"] == "0.3.1"
    assert state["gemini_cli_capabilities"] == _GEMINI_ALL_CAPABILITIES
    assert state["gemini_signed_in"] is True


def test_install_warns_on_missing_gemini_capabilities_but_still_succeeds(
    tmp_path: Path,
    monkeypatch,
):
    gemini_cli = installer_mod.GeminiCliCheck(
        found=True,
        path=Path("/usr/local/bin/gemini"),
        version="0.3.1",
        raw_output="0.3.1",
        capabilities={
            "--prompt": True,
            "--output-format stream-json": True,
            "--resume": False,
            "--approval-mode yolo": False,
            "--acp": False,
        },
        missing_capabilities=("--resume", "--approval-mode yolo"),
    )
    result, _claude_json_path, state_path = _install_with_gemini_stub(
        tmp_path, monkeypatch, gemini_cli
    )

    message = installer_mod.format_install_message(result)
    assert "Ready: Codex 0.124.0 · Gemini 0.3.1." in message
    assert "Warning: detected Gemini CLI" not in message
    assert "Gemini CLI is missing required flag" not in message

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["gemini_cli_found"] is True
    assert state["gemini_cli_version"] == "0.3.1"
    assert state["gemini_cli_capabilities"] == gemini_cli.capabilities


def test_install_warns_when_gemini_cli_missing_but_still_succeeds(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    settings_path, claude_json_path, state_path, bin_dir = _fresh_paths(tmp_path)
    codex_binary = _make_executable(bin_dir / "claude-anyteam")
    _make_executable(bin_dir / "claude-anyteam-spawn-shim")

    _stub_prereq_found(monkeypatch)
    monkeypatch.setattr(installer_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        installer_mod,
        "_check_codex_cli",
        lambda: installer_mod.CodexCliCheck(
            found=True,
            path=Path("/usr/local/bin/codex"),
            version="0.124.0",
            raw_output="codex-cli 0.124.0",
        ),
    )
    monkeypatch.setattr(
        installer_mod,
        "_check_gemini_cli",
        lambda: installer_mod.GeminiCliCheck(
            found=False, path=None, version=None, raw_output=None
        ),
    )
    monkeypatch.setattr(cli_mod.sys, "argv", [str(codex_binary)])

    exit_code = cli_mod.main(
        _install_argv(settings_path, claude_json_path, state_path, "--assume-yes")
    )
    assert exit_code == 0, "missing Gemini CLI must not block install"

    stdout = capsys.readouterr().out
    assert "Ready: Codex 0.124.0 · Gemini (not installed)." in stdout
    assert "Gemini CLI:" in stdout
    assert "npm install -g @google/gemini-cli" in stdout
    assert "https://github.com/google-gemini/gemini-cli" in stdout
    assert "GEMINI_API_KEY, or configure Vertex" in stdout
    assert "Warning: the Gemini CLI" not in stdout

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["gemini_cli_found"] is False
    assert state["gemini_cli_version"] is None


def test_install_combines_tmux_and_gemini_warnings_on_tmux_halt(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    """User missing BOTH tmux and Gemini CLI should see both warnings at once."""
    settings_path, claude_json_path, state_path, bin_dir = _fresh_paths(tmp_path)
    _make_executable(bin_dir / "claude-anyteam")
    _make_executable(bin_dir / "claude-anyteam-spawn-shim")

    _stub_prereq_missing(monkeypatch, platform="linux")
    monkeypatch.setattr(installer_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        installer_mod,
        "_check_codex_cli",
        lambda: installer_mod.CodexCliCheck(
            found=True,
            path=Path("/usr/local/bin/codex"),
            version="0.124.0",
            raw_output="codex-cli 0.124.0",
        ),
    )
    monkeypatch.setattr(
        installer_mod,
        "_check_gemini_cli",
        lambda: installer_mod.GeminiCliCheck(
            found=False, path=None, version=None, raw_output=None
        ),
    )
    monkeypatch.setattr(cli_mod.sys, "argv", [str(bin_dir / "claude-anyteam")])

    exit_code = cli_mod.main(_install_argv(settings_path, claude_json_path, state_path))
    assert exit_code != 0

    err = capsys.readouterr().err
    assert "requires a terminal multiplexer" in err
    assert "sudo apt install tmux" in err
    assert "Additionally:" in err
    assert "Gemini CLI (`gemini`) was not found" in err


def test_check_gemini_cli_returns_missing_when_not_on_path(monkeypatch):
    monkeypatch.setattr(installer_mod.shutil, "which", lambda _name: None)
    result = installer_mod._check_gemini_cli()
    assert result.found is False
    assert result.path is None
    assert result.version is None
    assert result.raw_output is None
    assert result.capabilities == {}
    assert result.missing_capabilities == ()
    assert result.signed_in is False
    assert "not found" in (result.signed_in_detail or "")


def test_check_gemini_signin_accepts_valid_oauth_shape(tmp_path: Path):
    oauth_path = tmp_path / ".gemini" / "oauth_creds.json"
    accounts_path = tmp_path / ".gemini" / "google_accounts.json"
    oauth_path.parent.mkdir(parents=True)
    oauth_path.write_text(
        json.dumps({"access_token": "token-value", "expiry_date": 4102444800000}) + "\n",
        encoding="utf-8",
    )

    signed_in, detail = installer_mod._check_gemini_signin(
        tmp_path / "bin" / "gemini",
        oauth_creds_path=oauth_path,
        google_accounts_path=accounts_path,
        environ={},
    )

    assert signed_in is True
    assert detail is None


@pytest.mark.parametrize("token_key", ["id_token", "refresh_token"])
def test_check_gemini_signin_rejects_oauth_without_access_token(
    tmp_path: Path,
    token_key: str,
):
    oauth_path = tmp_path / ".gemini" / "oauth_creds.json"
    accounts_path = tmp_path / ".gemini" / "google_accounts.json"
    oauth_path.parent.mkdir(parents=True)
    oauth_path.write_text(json.dumps({token_key: "token-value"}) + "\n", encoding="utf-8")

    signed_in, detail = installer_mod._check_gemini_signin(
        tmp_path / "bin" / "gemini",
        oauth_creds_path=oauth_path,
        google_accounts_path=accounts_path,
        environ={},
    )

    assert signed_in is False
    assert detail is not None
    assert "missing credentials" in detail.lower()


@pytest.mark.parametrize("active_value", [True, "user@example.com", {"email": "user@example.com"}])
def test_check_gemini_signin_ignores_google_accounts_without_oauth_creds(
    tmp_path: Path,
    active_value: object,
):
    oauth_path = tmp_path / ".gemini" / "oauth_creds.json"
    accounts_path = tmp_path / ".gemini" / "google_accounts.json"
    accounts_path.parent.mkdir(parents=True)
    accounts_path.write_text(json.dumps({"active": active_value}) + "\n", encoding="utf-8")

    signed_in, detail = installer_mod._check_gemini_signin(
        tmp_path / "bin" / "gemini",
        oauth_creds_path=oauth_path,
        google_accounts_path=accounts_path,
        environ={},
    )

    assert signed_in is False
    assert detail is not None
    assert "missing" in detail.lower()


@pytest.mark.parametrize(
    ("contents", "expected_detail"),
    [
        ("", "empty"),
        ("{", "malformed"),
        (json.dumps({}), "missing credentials"),
    ],
)
def test_check_gemini_signin_reports_unusable_oauth_file(
    tmp_path: Path,
    contents: str,
    expected_detail: str,
):
    oauth_path = tmp_path / ".gemini" / "oauth_creds.json"
    accounts_path = tmp_path / ".gemini" / "google_accounts.json"
    oauth_path.parent.mkdir(parents=True)
    oauth_path.write_text(contents, encoding="utf-8")

    signed_in, detail = installer_mod._check_gemini_signin(
        tmp_path / "bin" / "gemini",
        oauth_creds_path=oauth_path,
        google_accounts_path=accounts_path,
        environ={},
    )

    assert signed_in is False
    assert detail is not None
    assert expected_detail in detail.lower()


def test_check_gemini_signin_reports_missing_auth_files(tmp_path: Path):
    signed_in, detail = installer_mod._check_gemini_signin(
        tmp_path / "bin" / "gemini",
        oauth_creds_path=tmp_path / ".gemini" / "oauth_creds.json",
        google_accounts_path=tmp_path / ".gemini" / "google_accounts.json",
        environ={},
    )

    assert signed_in is False
    assert detail is not None
    assert "missing" in detail.lower()


def test_check_gemini_signin_reports_expired_oauth_file(tmp_path: Path):
    oauth_path = tmp_path / ".gemini" / "oauth_creds.json"
    accounts_path = tmp_path / ".gemini" / "google_accounts.json"
    oauth_path.parent.mkdir(parents=True)
    oauth_path.write_text(
        json.dumps({"access_token": "token-value", "expiry_date": 0}) + "\n",
        encoding="utf-8",
    )

    signed_in, detail = installer_mod._check_gemini_signin(
        tmp_path / "bin" / "gemini",
        oauth_creds_path=oauth_path,
        google_accounts_path=accounts_path,
        environ={},
    )

    assert signed_in is False
    assert detail is not None
    assert "expired" in detail.lower()


def test_gemini_capabilities_from_help_detects_required_and_optional_flags():
    help_text = """
    Usage: gemini [options]
      --prompt <prompt>
      --output-format <text|json|stream-json>
      --resume [session]
      --approval-mode <default|yolo>
      --experimental-acp
    """

    assert installer_mod._gemini_capabilities_from_help(help_text) == _GEMINI_ALL_CAPABILITIES
    assert installer_mod._gemini_acp_flag_from_help(help_text) == "--experimental-acp"


def test_gemini_acp_flag_from_help_prefers_stable_flag():
    assert installer_mod._gemini_acp_flag_from_help("--experimental-acp --acp") == "--acp"


def test_check_gemini_cli_parses_version_from_subprocess(monkeypatch, tmp_path: Path):
    fake_gemini = tmp_path / "gemini"
    fake_gemini.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_gemini.chmod(0o755)

    monkeypatch.setattr(installer_mod.shutil, "which", lambda _name: str(fake_gemini))
    monkeypatch.setattr(installer_mod, "_check_gemini_signin", lambda _path: (True, None))

    class _Completed:
        def __init__(self, stdout: str, stderr: str = "") -> None:
            self.returncode = 0
            self.stdout = stdout
            self.stderr = stderr

    def _fake_run(args, **_kwargs):
        if args[-1] == "--version":
            return _Completed("gemini 0.3.1\n")
        if args[-1] == "--help":
            return _Completed(
                "--prompt --output-format stream-json --resume --approval-mode yolo --acp\n"
            )
        raise AssertionError(f"unexpected subprocess args: {args!r}")

    monkeypatch.setattr(installer_mod.subprocess, "run", _fake_run)

    result = installer_mod._check_gemini_cli()
    assert result.found is True
    assert result.path == fake_gemini.resolve()
    assert result.version == "0.3.1"
    assert result.raw_output == "gemini 0.3.1"
    assert result.capabilities == _GEMINI_ALL_CAPABILITIES
    assert result.missing_capabilities == ()
    assert result.signed_in is True
    assert result.signed_in_detail is None


def test_check_gemini_cli_records_missing_capabilities(monkeypatch, tmp_path: Path):
    fake_gemini = tmp_path / "gemini"
    fake_gemini.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_gemini.chmod(0o755)

    monkeypatch.setattr(installer_mod.shutil, "which", lambda _name: str(fake_gemini))

    class _Completed:
        def __init__(self, stdout: str, stderr: str = "") -> None:
            self.returncode = 0
            self.stdout = stdout
            self.stderr = stderr

    def _fake_run(args, **_kwargs):
        if args[-1] == "--version":
            return _Completed("gemini 0.3.1\n")
        if args[-1] == "--help":
            return _Completed("--prompt --output-format stream-json\n")
        raise AssertionError(f"unexpected subprocess args: {args!r}")

    monkeypatch.setattr(installer_mod.subprocess, "run", _fake_run)

    result = installer_mod._check_gemini_cli()
    assert result.capabilities == {
        "--prompt": True,
        "--output-format stream-json": True,
        "--resume": False,
        "--approval-mode yolo": False,
        "--acp": False,
    }
    assert result.missing_capabilities == ("--resume", "--approval-mode yolo")


def test_parse_cli_version_rejects_garbage_tokens():
    # Direct unit coverage for the reviewer-requested branch: weird strings
    # must parse to None rather than returning a bogus token.
    for bad in (
        "weird unexpected output",
        "12abc",
        "v0.124.0",  # leading-v prefix
        "0..1",
        "",
        "0",  # major-only, not semver-ish enough
        "codex-cli unknown",
    ):
        assert installer_mod._parse_cli_version(bad) is None, f"expected None for {bad!r}"


def test_parse_cli_version_accepts_various_valid_shapes():
    cases = {
        "codex-cli 0.124.0": "0.124.0",
        "codex-cli 0.120": "0.120",
        "Codex 1.2.3-rc1": "1.2.3-rc1",
        "codex 10.20.30": "10.20.30",
    }
    for raw, expected in cases.items():
        assert installer_mod._parse_cli_version(raw) == expected, f"{raw!r} → {expected!r}"


def test_parse_cli_version_picks_correct_token_from_noisy_output():
    # Regression: the v1 "first token starting with a digit" heuristic would
    # report "0" here. The tightened regex must skip "0" / "errors" / headings
    # and land on "0.124.0".
    noisy = "0 errors — codex-cli 0.124.0 (release build)"
    assert installer_mod._parse_cli_version(noisy) == "0.124.0"


def test_check_codex_cli_falls_back_when_subprocess_stdout_unparseable(
    monkeypatch,
    tmp_path: Path,
):
    """Real `_check_codex_cli()` parse-fail branch: returncode=0 + unparseable stdout.

    Covers the branch the reviewer flagged as previously only exercised via a
    pre-built `CodexCliCheck` fixture.
    """
    fake_codex = tmp_path / "codex"
    fake_codex.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_codex.chmod(0o755)

    monkeypatch.setattr(installer_mod.shutil, "which", lambda _name: str(fake_codex))

    class _Completed:
        returncode = 0
        stdout = "weird unexpected output\n"
        stderr = ""

    def _fake_run(*_args, **_kwargs):
        return _Completed()

    monkeypatch.setattr(installer_mod.subprocess, "run", _fake_run)

    result = installer_mod._check_codex_cli()
    assert result.found is True
    assert result.version is None, "unparseable stdout must leave version=None"
    assert result.raw_output == "weird unexpected output"


def test_codex_meets_minimum_branches():
    def _mk(version: str | None) -> installer_mod.CodexCliCheck:
        return installer_mod.CodexCliCheck(
            found=version is not None,
            path=Path("/usr/local/bin/codex") if version is not None else None,
            version=version,
            raw_output=None,
        )

    assert installer_mod._codex_meets_minimum(_mk("0.120.0")) is True
    assert installer_mod._codex_meets_minimum(_mk("0.124.0")) is True
    assert installer_mod._codex_meets_minimum(_mk("1.0.0")) is True
    assert installer_mod._codex_meets_minimum(_mk("0.119.999")) is False
    assert installer_mod._codex_meets_minimum(_mk("0.0.1")) is False
    assert installer_mod._codex_meets_minimum(_mk(None)) is None
    # Not-found: meets_minimum returns None (there's nothing to compare).
    assert (
        installer_mod._codex_meets_minimum(
            installer_mod.CodexCliCheck(found=False, path=None, version=None, raw_output=None)
        )
        is None
    )


def test_check_codex_cli_returns_missing_when_not_on_path(monkeypatch):
    monkeypatch.setattr(installer_mod.shutil, "which", lambda _name: None)
    result = installer_mod._check_codex_cli()
    assert result.found is False
    assert result.path is None
    assert result.version is None
    assert result.raw_output is None
    assert result.signed_in is False
    assert "not found" in (result.signed_in_detail or "")


def test_check_codex_signin_accepts_valid_auth_shape(tmp_path: Path):
    auth_path = tmp_path / ".codex" / "auth.json"
    auth_path.parent.mkdir(parents=True)
    auth_path.write_text(
        json.dumps({"tokens": {"access_token": "token-value"}, "account_id": "acct"}) + "\n",
        encoding="utf-8",
    )

    signed_in, detail = installer_mod._check_codex_signin(
        tmp_path / "bin" / "codex",
        auth_path=auth_path,
    )

    assert signed_in is True
    assert detail is None


@pytest.mark.parametrize("token_key", ["id_token", "refresh_token"])
def test_check_codex_signin_rejects_auth_without_access_token(
    tmp_path: Path,
    token_key: str,
):
    auth_path = tmp_path / ".codex" / "auth.json"
    auth_path.parent.mkdir(parents=True)
    auth_path.write_text(
        json.dumps({"tokens": {token_key: "token-value"}}) + "\n",
        encoding="utf-8",
    )

    signed_in, detail = installer_mod._check_codex_signin(
        tmp_path / "bin" / "codex",
        auth_path=auth_path,
    )

    assert signed_in is False
    assert detail is not None
    assert "missing credentials" in detail.lower()


@pytest.mark.parametrize(
    ("contents", "expected_detail"),
    [
        ("", "empty"),
        ("{", "malformed"),
        (json.dumps({}), "missing credentials"),
    ],
)
def test_check_codex_signin_reports_unusable_auth_file(
    tmp_path: Path,
    contents: str,
    expected_detail: str,
):
    auth_path = tmp_path / ".codex" / "auth.json"
    auth_path.parent.mkdir(parents=True)
    auth_path.write_text(contents, encoding="utf-8")

    signed_in, detail = installer_mod._check_codex_signin(
        tmp_path / "bin" / "codex",
        auth_path=auth_path,
    )

    assert signed_in is False
    assert detail is not None
    assert expected_detail in detail.lower()


def test_check_codex_signin_reports_missing_auth_file(tmp_path: Path):
    signed_in, detail = installer_mod._check_codex_signin(
        tmp_path / "bin" / "codex",
        auth_path=tmp_path / ".codex" / "auth.json",
    )

    assert signed_in is False
    assert detail is not None
    assert "missing" in detail.lower()


def test_check_codex_signin_reports_expired_auth_file(tmp_path: Path):
    auth_path = tmp_path / ".codex" / "auth.json"
    auth_path.parent.mkdir(parents=True)
    auth_path.write_text(
        json.dumps({"tokens": {"access_token": "token-value", "expires_at": 0}}) + "\n",
        encoding="utf-8",
    )

    signed_in, detail = installer_mod._check_codex_signin(
        tmp_path / "bin" / "codex",
        auth_path=auth_path,
    )

    assert signed_in is False
    assert detail is not None
    assert "expired" in detail.lower()


def test_check_codex_cli_parses_version_from_subprocess(monkeypatch, tmp_path: Path):
    fake_codex = tmp_path / "codex"
    fake_codex.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_codex.chmod(0o755)

    monkeypatch.setattr(installer_mod.shutil, "which", lambda _name: str(fake_codex))
    monkeypatch.setattr(installer_mod, "_check_codex_signin", lambda _path: (True, None))

    class _Completed:
        returncode = 0
        stdout = "codex-cli 0.124.0\n"
        stderr = ""

    def _fake_run(*_args, **_kwargs):
        return _Completed()

    monkeypatch.setattr(installer_mod.subprocess, "run", _fake_run)

    result = installer_mod._check_codex_cli()
    assert result.found is True
    assert result.version == "0.124.0"
    assert result.raw_output == "codex-cli 0.124.0"
    assert result.signed_in is True
    assert result.signed_in_detail is None


def test_check_codex_cli_survives_subprocess_timeout(monkeypatch, tmp_path: Path):
    fake_codex = tmp_path / "codex"
    fake_codex.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_codex.chmod(0o755)

    monkeypatch.setattr(installer_mod.shutil, "which", lambda _name: str(fake_codex))

    def _raise_timeout(*_args, **_kwargs):
        raise installer_mod.subprocess.TimeoutExpired(cmd="codex --version", timeout=5)

    monkeypatch.setattr(installer_mod.subprocess, "run", _raise_timeout)

    result = installer_mod._check_codex_cli()
    assert result.found is True
    assert result.version is None
    assert result.raw_output is None


def test_install_npx_flow_with_assume_yes_warns_on_missing_codex(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    """The npx flow passes --assume-yes; warning must still surface."""
    settings_path, claude_json_path, state_path, bin_dir = _fresh_paths(tmp_path)
    codex_binary = _make_executable(bin_dir / "claude-anyteam")
    _make_executable(bin_dir / "claude-anyteam-spawn-shim")

    _stub_prereq_found(monkeypatch)
    monkeypatch.setattr(installer_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        installer_mod,
        "_check_codex_cli",
        lambda: installer_mod.CodexCliCheck(
            found=False, path=None, version=None, raw_output=None
        ),
    )
    monkeypatch.setattr(installer_mod, "_check_gemini_cli", lambda: _gemini_cli_ready())
    monkeypatch.setattr(installer_mod, "_check_gemini_auth", lambda: _auth(True))
    monkeypatch.setattr(cli_mod.sys, "argv", [str(codex_binary)])

    # Simulate an existing claude.json so the --assume-yes branch of the
    # prompt exercises too.
    claude_json_path.parent.mkdir(parents=True, exist_ok=True)
    claude_json_path.write_text(
        json.dumps({installer_mod.TEAMMATE_MODE_KEY: "auto"}) + "\n",
        encoding="utf-8",
    )

    exit_code = cli_mod.main(
        _install_argv(settings_path, claude_json_path, state_path, "--assume-yes")
    )
    assert exit_code == 0

    stdout = capsys.readouterr().out
    assert "Ready: Codex (not installed) · Gemini 0.39.0." in stdout
    assert "Codex CLI:" in stdout
    assert "1. Install:  npm install -g @openai/codex" in stdout
