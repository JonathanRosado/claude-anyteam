from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path

from claude_teams import messaging, teams
from claude_teams.models import COLOR_PALETTE, InboxMessage, TeammateMember
from claude_teams.teams import _VALID_NAME_RE


def discover_harness_binary(name: str) -> str | None:
    return shutil.which(name)


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

    resolved_cwd = cwd or str(Path.cwd())

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
