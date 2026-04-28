from __future__ import annotations

import os
import json
import shlex
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any

from claude_teams import messaging, teams
from claude_teams.models import COLOR_PALETTE, InboxMessage, TeammateMember
from claude_teams.teams import _VALID_NAME_RE

DEFAULT_MANIFEST_READ_CONCURRENCY = 4
DEFAULT_MANIFEST_READ_TIMEOUT_S = 2.0
MANIFEST_READ_CONCURRENCY_ENV = "CLAUDE_TEAMS_MANIFEST_READ_CONCURRENCY"
MANIFEST_READ_TIMEOUT_ENV = "CLAUDE_TEAMS_MANIFEST_READ_TIMEOUT_S"
WORKTREE_ROOT_ENV = "CLAUDE_TEAMS_WORKTREE_ROOT"

# Tests patch ``claude_teams.spawner.subprocess`` to fake tmux spawning.
# Git isolation probes must still use the real subprocess runner.
_REAL_SUBPROCESS_RUN = subprocess.run


def discover_harness_binary(name: str) -> str | None:
    return shutil.which(name)


def _git_output(cwd: Path, *args: str) -> str | None:
    try:
        result = _REAL_SUBPROCESS_RUN(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, ValueError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _git_toplevel(cwd: Path) -> Path | None:
    out = _git_output(cwd, "rev-parse", "--show-toplevel")
    return Path(out).resolve() if out else None


def _git_index_path(cwd: Path) -> Path | None:
    out = _git_output(cwd, "rev-parse", "--git-path", "index")
    if not out:
        return None
    path = Path(out)
    if not path.is_absolute():
        path = cwd / path
    return path.resolve()


def _worktree_root() -> Path:
    override = os.environ.get(WORKTREE_ROOT_ENV)
    if override:
        return Path(override).expanduser().resolve()
    return Path(tempfile.gettempdir()) / "claude-anyteam-worktrees"


def _safe_branch_fragment(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in value).strip(
        "-."
    ) or "agent"


def _member_git_toplevel(member: Any) -> Path | None:
    cwd = getattr(member, "cwd", None)
    if not cwd:
        return None
    return _git_toplevel(Path(str(cwd)))


def ensure_git_worktree_isolation(
    team_name: str,
    agent_name: str,
    requested_cwd: str,
    *,
    base_dir: Path | None = None,
) -> str:
    """Return a cwd that does not share a Git worktree index with teammates.

    The public spawn tool receives a caller-supplied ``cwd``.  Different
    absolute cwd strings are not necessarily isolated: two subdirectories of
    the same Git worktree share one ``.git/index``.  When the requested Git
    top-level is already used by another team member (including the lead), make
    a real Git worktree for this teammate and preserve any requested
    subdirectory within that worktree.

    Non-Git directories, already-distinct Git worktrees, and direct
    ``spawn_teammate(..., cwd=None)`` callers are left to the existing behavior.
    """

    requested_path = Path(requested_cwd).expanduser().resolve()
    requested_top = _git_toplevel(requested_path)
    if requested_top is None:
        return str(requested_path)

    config = teams.read_config(team_name, base_dir)
    collides = any(
        getattr(member, "name", None) != agent_name
        and _member_git_toplevel(member) == requested_top
        for member in config.members
    )
    if not collides:
        return str(requested_path)

    isolation_root = _worktree_root() / _safe_branch_fragment(team_name)
    worktree_path = isolation_root / _safe_branch_fragment(agent_name)
    rel = requested_path.relative_to(requested_top)

    if not (worktree_path / ".git").exists():
        worktree_path.parent.mkdir(parents=True, exist_ok=True)
        branch = (
            "claude-anyteam/"
            f"{_safe_branch_fragment(team_name)}/"
            f"{_safe_branch_fragment(agent_name)}/"
            f"{int(time.time() * 1000)}"
        )
        result = _REAL_SUBPROCESS_RUN(
            [
                "git",
                "-C",
                str(requested_top),
                "worktree",
                "add",
                "-b",
                branch,
                str(worktree_path),
                "HEAD",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "failed to create isolated git worktree for teammate "
                f"{agent_name!r} from {requested_top}: {result.stderr.strip()}"
            )

    isolated_cwd = (worktree_path / rel).resolve()
    isolated_cwd.mkdir(parents=True, exist_ok=True)
    if _git_index_path(isolated_cwd) == _git_index_path(requested_path):
        raise RuntimeError(
            "isolated git worktree did not produce a distinct index for "
            f"teammate {agent_name!r}"
        )
    return str(isolated_cwd)


def use_tmux_windows() -> bool:
    """Return True when teammate processes should be spawned in tmux windows."""
    return os.environ.get("USE_TMUX_WINDOWS") is not None


def build_tmux_spawn_args(command: str, name: str) -> list[str]:
    """Build the tmux command used to spawn a teammate process."""
    if use_tmux_windows():
        return [
            "tmux",
            "new-window",
            "-dP",
            "-F",
            "#{window_id}",
            "-n",
            f"@claude-team | {name}",
            command,
        ]
    return ["tmux", "split-window", "-dP", "-F", "#{pane_id}", command]


def assign_color(team_name: str, base_dir: Path | None = None) -> str:
    config = teams.read_config(team_name, base_dir)
    count = sum(1 for m in config.members if isinstance(m, TeammateMember))
    return COLOR_PALETTE[count % len(COLOR_PALETTE)]


def _teams_dir(base_dir: Path | None = None) -> Path:
    return (base_dir / "teams") if base_dir else teams.TEAMS_DIR


def _positive_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw in (None, ""):
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def _positive_float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw in (None, ""):
        return default
    try:
        return max(0.001, float(raw))
    except ValueError:
        return default


def read_team_manifests_parallel(
    team_name: str,
    *,
    agent_names: list[str] | None = None,
    base_dir: Path | None = None,
    concurrency: int | None = None,
    timeout_s: float | None = None,
) -> dict[str, dict[str, Any]]:
    """Read rich teammate manifests with bounded parallelism.

    This is the substrate-side companion to adapter prewarm: callers that need
    many Agent Cards can fan out without one thread per teammate. Missing,
    invalid, or slow manifests are skipped so a large team with one stale peer
    does not block formation/spawn-time discovery.
    """

    if agent_names is None:
        config = teams.read_config(team_name, base_dir)
        agent_names = [m.name for m in config.members if getattr(m, "name", None)]
    if not agent_names:
        return {}

    max_workers = min(
        max(1, concurrency or _positive_int_env(
            MANIFEST_READ_CONCURRENCY_ENV,
            DEFAULT_MANIFEST_READ_CONCURRENCY,
        )),
        len(agent_names),
    )
    per_manifest_timeout = max(
        0.001,
        float(
            timeout_s
            if timeout_s is not None
            else _positive_float_env(
                MANIFEST_READ_TIMEOUT_ENV,
                DEFAULT_MANIFEST_READ_TIMEOUT_S,
            )
        ),
    )
    manifest_dir = _teams_dir(base_dir) / team_name / "manifests"
    names_iter = iter(agent_names)
    results: dict[str, dict[str, Any]] = {}

    def _read_one(agent_name: str) -> dict[str, Any] | None:
        path = manifest_dir / f"{agent_name}.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        return raw if isinstance(raw, dict) else None

    executor = ThreadPoolExecutor(
        max_workers=max_workers,
        thread_name_prefix="team-manifest-read",
    )
    pending: dict[Future[dict[str, Any] | None], tuple[str, float]] = {}

    def _submit_next() -> bool:
        try:
            agent_name = next(names_iter)
        except StopIteration:
            return False
        pending[executor.submit(_read_one, agent_name)] = (agent_name, time.monotonic())
        return True

    for _ in range(max_workers):
        if not _submit_next():
            break

    try:
        while pending:
            now = time.monotonic()
            for future, (agent_name, started_at) in list(pending.items()):
                if now - started_at >= per_manifest_timeout:
                    pending.pop(future, None)
                    future.cancel()

            if not pending:
                break

            now = time.monotonic()
            next_timeout = min(
                max(0.0, started_at + per_manifest_timeout - now)
                for _agent_name, started_at in pending.values()
            )
            done, _not_done = wait(
                pending.keys(),
                timeout=next_timeout,
                return_when=FIRST_COMPLETED,
            )
            if not done:
                continue
            for future in done:
                agent_name, _started_at = pending.pop(future)
                manifest = future.result()
                if manifest is not None:
                    results[agent_name] = manifest
                _submit_next()
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    return results


def skip_permissions() -> bool:
    """Return True when spawned teammates should skip permission prompts."""
    return os.environ.get("CLAUDE_TEAMS_DANGEROUSLY_SKIP_PERMISSIONS") is not None


def build_spawn_command(
    member: TeammateMember,
    claude_binary: str,
    lead_session_id: str,
) -> str:
    team_name = member.agent_id.split("@", 1)[1]
    cmd = (
        f"cd {shlex.quote(member.cwd)} && "
        f"CLAUDECODE=1 CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1 "
        f"{shlex.quote(claude_binary)} "
        f"--agent-id {shlex.quote(member.agent_id)} "
        f"--agent-name {shlex.quote(member.name)} "
        f"--team-name {shlex.quote(team_name)} "
        f"--agent-color {shlex.quote(member.color)} "
        f"--parent-session-id {shlex.quote(lead_session_id)} "
        f"--agent-type {shlex.quote(member.agent_type)} "
        f"--model {shlex.quote(member.model)}"
    )
    if member.plan_mode_required:
        cmd += " --plan-mode-required"
    if skip_permissions():
        cmd += " --dangerously-skip-permissions"
    return cmd


def spawn_teammate(
    team_name: str,
    name: str,
    prompt: str,
    claude_binary: str,
    lead_session_id: str,
    *,
    model: str = "sonnet",
    subagent_type: str = "general-purpose",
    cwd: str | None = None,
    plan_mode_required: bool = False,
    base_dir: Path | None = None,
    backend_type: str = "claude",
) -> TeammateMember:
    """Spawn a teammate process in tmux.

    Adding a new external backend:
    - Use `TeammateMember.backendType` as the per-member discriminator.
    - Add an explicit per-backend parameter block to `spawn_teammate`.
    - Keep the session lifecycle explicit: verify-config → create-session →
      send-prompt → cleanup.
    - Provide a per-backend tmux attach command for operator visibility.
    """
    if not _VALID_NAME_RE.match(name):
        raise ValueError(
            f"Invalid agent name: {name!r}. Use only letters, numbers, hyphens, underscores."
        )
    if len(name) > 64:
        raise ValueError(f"Agent name too long ({len(name)} chars, max 64)")
    if name == "team-lead":
        raise ValueError("Agent name 'team-lead' is reserved")
    if backend_type != "claude":
        raise ValueError(f"Unsupported backend_type {backend_type!r}.")
    if backend_type == "claude" and not claude_binary:
        raise ValueError(
            "Cannot spawn claude teammate: 'claude' binary not found on PATH. "
            "Install Claude Code or ensure it is in your PATH."
        )

    resolved_cwd = (
        ensure_git_worktree_isolation(
            team_name,
            name,
            cwd,
            base_dir=base_dir,
        )
        if cwd is not None
        else str(Path.cwd())
    )

    color = assign_color(team_name, base_dir)
    now_ms = int(time.time() * 1000)

    member = TeammateMember(
        agent_id=f"{name}@{team_name}",
        name=name,
        agent_type=subagent_type,
        model=model,
        prompt=prompt,
        color=color,
        plan_mode_required=plan_mode_required,
        joined_at=now_ms,
        tmux_pane_id="",
        cwd=resolved_cwd,
        backend_type=backend_type,
        is_active=False,
    )

    member_added = False
    try:
        teams.add_member(team_name, member, base_dir)
        member_added = True

        messaging.ensure_inbox(team_name, name, base_dir)
        initial_msg = InboxMessage(
            from_="team-lead",
            text=prompt,
            timestamp=messaging.now_iso(),
            read=False,
        )
        messaging.append_message(team_name, name, initial_msg, base_dir)

        cmd = build_spawn_command(member, claude_binary, lead_session_id)

        result = subprocess.run(
            build_tmux_spawn_args(cmd, name),
            capture_output=True,
            text=True,
            check=True,
        )
        pane_id = result.stdout.strip()

        with teams.locked_team_config(team_name, base_dir):
            config = teams.read_config(team_name, base_dir)
            for m in config.members:
                if isinstance(m, TeammateMember) and m.name == name:
                    m.tmux_pane_id = pane_id
                    break
            teams.write_config(team_name, config, base_dir)
    except Exception:
        if member_added:
            try:
                teams.remove_member(team_name, name, base_dir)
            except Exception:
                pass
        raise

    member.tmux_pane_id = pane_id
    return member


def kill_tmux_pane(pane_id: str) -> None:
    if pane_id.startswith("@"):
        subprocess.run(["tmux", "kill-window", "-t", pane_id], check=False)
        return
    subprocess.run(["tmux", "kill-pane", "-t", pane_id], check=False)
