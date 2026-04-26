"""v7 narrowed MCP server exposing a safe tool subset to the Codex subprocess.

**Why a narrowed MCP surface.** The full team-control surface includes
destructive lifecycle operations (`team_delete`, `force_kill_teammate`,
`spawn_teammate`, `team_create`, `process_shutdown_approved`,
`check_teammate`) that have no business being accessible from a running
teammate's context. A hallucinated tool call to any of them would have
outsized consequences.

Rather than rely on prompt discipline, this wrapper exposes **only the
small tool set a Codex teammate actually needs mid-task**, with descriptions
tuned for the team-protocol context and team/agent identity pre-filled
from startup env so Codex can't accidentally send as the wrong teammate.

The wrapper delegates internally to the `claude_teams` team-protocol
implementation for file I/O, locking, and schema handling. This keeps
the v6 invariants intact while narrowing the surface Codex sees.

Launched as a stdio subprocess by Codex via `-c mcp_servers.*.command=...`
overrides on `codex exec`. Lifetime matches the Codex invocation.

Environment:
- `CLAUDE_ANYTEAM_TEAM` — our team name (required).
- `CLAUDE_ANYTEAM_NAME` — our teammate name within the team (required).

Legacy `CODEX_TEAMMATE_*` identity vars are still honored as fallbacks during the rebrand.
"""

from __future__ import annotations

import fnmatch
import logging
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Literal

from .env import LEGACY_NAME_ENV, LEGACY_TEAM_ENV, NAME_ENV, TEAM_ENV, env_first
from claude_teams import messaging as _cs_messaging  # type: ignore[import-untyped]
from claude_teams import tasks as _cs_tasks  # type: ignore[import-untyped]
from claude_teams import teams as _cs_teams  # type: ignore[import-untyped]
from claude_teams.models import TeammateMember as _TeammateMember  # type: ignore[import-untyped]
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

logger = logging.getLogger("claude_anyteam.wrapper")

# Tool set we deliberately expose to Codex. Checked by a test so additions
# require intent. Order here matches the help-text ordering Codex will see.
EXPOSED_TOOLS: tuple[str, ...] = (
    "send_message",
    "task_update",
    "task_create",
    "read_inbox",
    "task_list",
    "read_config",
    "mcp_anyteam_shell",
    "mcp_anyteam_read_file",
    "mcp_anyteam_write_file",
    "mcp_anyteam_list_directory",
    "mcp_anyteam_edit_file",
    "mcp_anyteam_search",
    "mcp_anyteam_web_fetch",
)

# Full team-control tools that we deliberately do NOT surface. Checked by a
# test so removals are deliberate. If the protocol gains a new tool, the test
# fails and forces a decision about whether it belongs in EXPOSED_TOOLS or
# BLOCKED_TOOLS.
BLOCKED_TOOLS: tuple[str, ...] = (
    "team_create",
    "team_delete",
    "spawn_teammate",
    "force_kill_teammate",
    "process_shutdown_approved",
    "check_teammate",
)


def _identity(argv: list[str] | None = None) -> tuple[str, str]:
    """Resolve (team, name) for this wrapper process.

    Precedence: CLI flags (`--team`, `--name`) > env vars
    (`CLAUDE_ANYTEAM_TEAM`, `CLAUDE_ANYTEAM_NAME`). Raises RuntimeError
    if neither provides both values.

    CLI args exist because when App Server spawns the wrapper as its
    own MCP subprocess, it does NOT forward our adapter's env into the
    wrapper's env (observed live during task #22 sanity probes — the
    wrapper handshake failed with "connection closed: initialize
    response" because `_identity()` raised). CLI args route around the
    env-forwarding question entirely.
    """
    team: str | None = None
    name: str | None = None

    # Parse only --team/--name without failing on argv we don't recognise,
    # since FastMCP may pass its own stdio-runtime args through sys.argv
    # at some future point. Conservative: accept only the two flags we own.
    args = list(argv) if argv is not None else sys.argv[1:]
    i = 0
    while i < len(args):
        tok = args[i]
        if tok == "--team" and i + 1 < len(args):
            team = args[i + 1]
            i += 2
        elif tok.startswith("--team="):
            team = tok.split("=", 1)[1]
            i += 1
        elif tok == "--name" and i + 1 < len(args):
            name = args[i + 1]
            i += 2
        elif tok.startswith("--name="):
            name = tok.split("=", 1)[1]
            i += 1
        else:
            i += 1

    team = team or env_first(os.environ, TEAM_ENV, LEGACY_TEAM_ENV)
    name = name or env_first(os.environ, NAME_ENV, LEGACY_NAME_ENV)
    if not team or not name:
        raise RuntimeError(
            "claude_anyteam wrapper: team and name are required. "
            "Pass --team/--name as CLI args or set "
            f"{TEAM_ENV}/{NAME_ENV} env vars."
        )
    return team, name



def _decode_bytes(data: bytes) -> tuple[str, str]:
    """Decode arbitrary file/HTTP bytes without raising on bad text."""
    for encoding in ("utf-8", "utf-16"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace"), "utf-8-replacement"


def _entry_for(path: Path, *, base: Path | None = None) -> dict[str, Any]:
    try:
        stat = path.stat()
    except OSError as e:
        return {"path": str(path if base is None else path.relative_to(base)), "error": str(e)}
    kind = "directory" if path.is_dir() else "file" if path.is_file() else "other"
    return {
        "path": str(path if base is None else path.relative_to(base)),
        "name": path.name,
        "type": kind,
        "size": stat.st_size,
        "mtime": stat.st_mtime,
    }


def build_server(argv: list[str] | None = None) -> FastMCP:
    """Construct the FastMCP app with the narrowed tools."""
    team, self_name = _identity(argv)

    mcp = FastMCP(
        name="claude-anyteam-wrapper",
        instructions=(
            "Narrowed MCP surface for a Codex teammate. Team: "
            f"{team!r}; identity: {self_name!r}. Call these tools when it "
            "would be useful to your teammates — progress updates via "
            "send_message, activeForm/owner/metadata changes via task_update, "
            "subtask creation via task_create, inspection via read_inbox / "
            "task_list / read_config. Destructive lifecycle operations "
            "(shutdown, spawn, kill) are not available here by design; the "
            "Python adapter owns those."
        ),
    )

    @mcp.tool
    def send_message(
        to: str,
        body: str,
        summary: str = "status update",
    ) -> dict:
        """Send a message to another teammate. Use for progress updates,
        clarifying questions, or handoffs. The sender is always you;
        do not try to impersonate another teammate.

        Args:
            to: recipient teammate name (e.g., 'team-lead'). Must be a
                member of this team.
            body: message content. Plain prose or JSON-serialized protocol
                payload both work.
            summary: optional short label shown in notifications (5-10 words).
        """
        if not to:
            raise ToolError("`to` must not be empty")
        if not body:
            raise ToolError("`body` must not be empty")
        if to == self_name:
            raise ToolError(f"refusing to send message to self ({self_name!r})")
        try:
            cfg = _cs_teams.read_config(team)
        except FileNotFoundError:
            raise ToolError(f"team {team!r} not found on disk")
        member_names = {m.name for m in cfg.members}
        if to == "*":
            delivered = 0
            for m in cfg.members:
                target = getattr(m, "name", None)
                if not target or target == self_name:
                    continue
                _cs_messaging.send_plain_message(
                    team,
                    from_name=self_name,
                    to_name=target,
                    text=body,
                    summary=summary,
                    color=None,
                )
                delivered += 1
            return {"delivered_to": "*", "sender": self_name, "count": delivered}
        if to not in member_names:
            raise ToolError(
                f"recipient {to!r} is not a member of team {team!r}; "
                f"members: {sorted(member_names)}"
            )
        # Stamp the sender's colour onto the wire payload. `send_plain_message`
        # stores this value directly on the inbox message, so using the
        # recipient's colour (the old behavior) misattributes who spoke.
        sender_color = None
        for m in cfg.members:
            if m.name == self_name and isinstance(m, _TeammateMember):
                sender_color = m.color
                break
        _cs_messaging.send_plain_message(
            team,
            from_name=self_name,
            to_name=to,
            text=body,
            summary=summary,
            color=sender_color,
        )
        return {"delivered_to": to, "sender": self_name}

    @mcp.tool
    def task_update(
        task_id: str,
        active_form: str | None = None,
        status: Literal["pending", "in_progress", "completed"] | None = None,
        owner: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict:
        """Update your own in-flight task. Use `active_form` to tell
        teammates what you're currently doing ('writing tests',
        'refactoring helper', etc.). Use `status` only to advance
        toward completion — do not set `deleted` here. `owner` and
        `metadata` are forwarded to the underlying TaskUpdate call for
        parity with native Claude agents.

        Args:
            task_id: id of the task to update. You must own it.
            active_form: short present-continuous description of current work.
            status: one of 'pending', 'in_progress', 'completed'.
            owner: optional owner override to forward to TaskUpdate.
            metadata: optional metadata patch to forward to TaskUpdate.
        """
        if status is not None and status not in ("pending", "in_progress", "completed"):
            raise ToolError(f"invalid status {status!r}")
        try:
            existing = _cs_tasks.get_task(team, task_id)
        except FileNotFoundError:
            raise ToolError(f"task {task_id!r} not found in team {team!r}")
        if existing.owner not in (self_name, None, ""):
            raise ToolError(
                f"refusing to update task {task_id!r}: owned by {existing.owner!r}, not {self_name!r}"
            )
        try:
            result = _cs_tasks.update_task(
                team,
                task_id,
                status=status,
                owner=owner,
                active_form=active_form,
                metadata=metadata,
            )
        except ValueError as e:
            raise ToolError(str(e))
        return {
            "id": result.id,
            "status": result.status,
            "active_form": result.active_form,
            "owner": result.owner,
            "metadata": result.metadata,
        }

    @mcp.tool
    def task_create(subject: str, description: str) -> dict:
        """Create a new task in your team. Use when work you discovered
        during a task should be split off rather than bundled into the
        current one. The new task starts unowned and pending; the lead
        will assign it.

        Args:
            subject: one-line task title (imperative form).
            description: full task context and scope.
        """
        if not subject.strip():
            raise ToolError("subject must not be empty")
        if not description.strip():
            raise ToolError("description must not be empty")
        try:
            t = _cs_tasks.create_task(team, subject, description)
        except ValueError as e:
            raise ToolError(str(e))
        return {"id": t.id, "status": t.status, "subject": t.subject}

    @mcp.tool
    def read_inbox(unread_only: bool = True) -> list[dict]:
        """Read your own inbox. Useful if you want to see whether a
        teammate replied to a clarifying question you sent.

        By default returns unread only and marks them read on the way
        out. Pass `unread_only=False` to see everything in chronological
        order (does not re-mark anything).

        Other teammates' inboxes are not accessible from this tool.
        """
        msgs = _cs_messaging.read_inbox(
            team,
            self_name,
            unread_only=unread_only,
            mark_as_read=unread_only,
        )
        return [m.model_dump(by_alias=True, exclude_none=True) for m in msgs]

    @mcp.tool
    def task_list() -> list[dict]:
        """List all tasks in your team with current status and owners."""
        try:
            result = _cs_tasks.list_tasks(team)
        except ValueError as e:
            raise ToolError(str(e))
        return [t.model_dump(by_alias=True, exclude_none=True) for t in result]

    @mcp.tool
    def mcp_anyteam_shell(
        command: str,
        cwd: str | None = None,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
    ) -> dict:
        """Run a shell command for the teammate with unrestricted filesystem
        and network access.

        Args:
            command: shell command to execute.
            cwd: optional working directory for the command.
            timeout: optional timeout in seconds.
            env: optional environment variables to add/override.
        """
        completed = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            timeout=timeout,
            env={**os.environ, **env} if env is not None else None,
            capture_output=True,
            text=True,
        )
        return {
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "exit_code": completed.returncode,
        }


    @mcp.tool
    def mcp_anyteam_read_file(path: str, offset: int = 0, limit: int | None = None) -> dict:
        """Read a local file as text with safe decoding fallback.

        Args:
            path: filesystem path to read. No workspace restriction is applied.
            offset: zero-based line offset to start reading from.
            limit: optional maximum number of lines to return.
        """
        if offset < 0:
            raise ToolError("offset must be >= 0")
        if limit is not None and limit < 0:
            raise ToolError("limit must be >= 0")
        file_path = Path(path)
        try:
            raw = file_path.read_bytes()
        except OSError as e:
            raise ToolError(str(e))
        text, encoding = _decode_bytes(raw)
        lines = text.splitlines(keepends=True)
        selected = lines[offset : None if limit is None else offset + limit]
        return {
            "path": str(file_path),
            "content": "".join(selected),
            "encoding": encoding,
            "bytes": len(raw),
            "line_count": len(lines),
            "offset": offset,
            "limit": limit,
            "truncated": limit is not None and offset + limit < len(lines),
        }

    @mcp.tool
    def mcp_anyteam_write_file(
        path: str,
        content: str,
        mode: Literal["overwrite", "append"] = "overwrite",
    ) -> dict:
        """Write text to a local file with no filesystem sandbox.

        Args:
            path: filesystem path to write.
            content: text content to write.
            mode: overwrite the file or append to it.
        """
        if mode not in ("overwrite", "append"):
            raise ToolError("mode must be 'overwrite' or 'append'")
        file_path = Path(path)
        existed = file_path.exists()
        try:
            if mode == "append":
                with file_path.open("a", encoding="utf-8") as f:
                    written = f.write(content)
            else:
                with file_path.open("w", encoding="utf-8") as f:
                    written = f.write(content)
        except OSError as e:
            raise ToolError(str(e))
        return {
            "path": str(file_path),
            "mode": mode,
            "existed": existed,
            "chars_written": written,
            "bytes_written": len(content.encode("utf-8")),
        }

    @mcp.tool
    def mcp_anyteam_list_directory(path: str, recursive: bool = False, glob: str | None = None) -> dict:
        """List directory entries with optional recursion and glob filtering.

        Args:
            path: directory path to list.
            recursive: when true, walk the whole subtree.
            glob: optional glob pattern matched against relative paths and names.
        """
        root = Path(path)
        if not root.exists():
            raise ToolError(f"path does not exist: {path}")
        if not root.is_dir():
            raise ToolError(f"path is not a directory: {path}")
        try:
            candidates = root.rglob("*") if recursive else root.iterdir()
            entries = []
            for child in candidates:
                rel = str(child.relative_to(root))
                if glob and not (fnmatch.fnmatch(rel, glob) or fnmatch.fnmatch(child.name, glob)):
                    continue
                entries.append(_entry_for(child, base=root))
        except OSError as e:
            raise ToolError(str(e))
        entries.sort(key=lambda item: item.get("path", ""))
        return {"path": str(root), "recursive": recursive, "glob": glob, "entries": entries}

    @mcp.tool
    def mcp_anyteam_edit_file(path: str, old: str, new: str, replace_all: bool = False) -> dict:
        """Replace an exact string in a text file and return the replacement count.

        Args:
            path: filesystem path to edit.
            old: exact text to replace.
            new: replacement text.
            replace_all: replace every occurrence instead of requiring exactly one.
        """
        if old == "":
            raise ToolError("old must not be empty")
        file_path = Path(path)
        try:
            raw = file_path.read_bytes()
            text, encoding = _decode_bytes(raw)
        except OSError as e:
            raise ToolError(str(e))
        count = text.count(old)
        if not replace_all and count != 1:
            raise ToolError(f"expected exactly one occurrence of old text, found {count}")
        updated = text.replace(old, new) if replace_all else text.replace(old, new, 1)
        try:
            file_path.write_text(updated, encoding="utf-8")
        except OSError as e:
            raise ToolError(str(e))
        return {"path": str(file_path), "replacements": count if replace_all else 1, "encoding_read": encoding}

    @mcp.tool
    def mcp_anyteam_search(
        pattern: str,
        path: str = ".",
        regex: bool = False,
        glob: str | None = None,
    ) -> dict:
        """Search files under a path for text or regex matches.

        Args:
            pattern: literal text or regex to search for.
            path: file or directory path to search.
            regex: interpret pattern as a regular expression when true.
            glob: optional file glob matched against relative paths and names.
        """
        root = Path(path)
        if not root.exists():
            raise ToolError(f"path does not exist: {path}")
        try:
            rx = re.compile(pattern) if regex else None
        except re.error as e:
            raise ToolError(f"invalid regex: {e}")
        files = [root] if root.is_file() else [p for p in root.rglob("*") if p.is_file()]
        matches: list[dict[str, Any]] = []
        for file_path in files:
            rel = str(file_path.relative_to(root)) if root.is_dir() else file_path.name
            if glob and not (fnmatch.fnmatch(rel, glob) or fnmatch.fnmatch(file_path.name, glob)):
                continue
            try:
                text, encoding = _decode_bytes(file_path.read_bytes())
            except OSError:
                continue
            for line_no, line in enumerate(text.splitlines(), start=1):
                if (rx.search(line) if rx is not None else pattern in line):
                    matches.append({
                        "path": str(file_path),
                        "line": line_no,
                        "text": line,
                        "encoding": encoding,
                    })
        return {"pattern": pattern, "path": str(root), "regex": regex, "glob": glob, "matches": matches}

    @mcp.tool
    def mcp_anyteam_web_fetch(
        url: str,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        body: str | None = None,
    ) -> dict:
        """Fetch a URL with unrestricted network access and return response data.

        Args:
            url: http(s) URL to fetch. No allowlist is applied.
            method: HTTP method to use.
            headers: optional request headers.
            body: optional request body text encoded as UTF-8.
        """
        data = body.encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read()
                text, encoding = _decode_bytes(raw)
                return {
                    "url": response.geturl(),
                    "status": response.status,
                    "headers": dict(response.headers.items()),
                    "body": text,
                    "encoding": encoding,
                    "bytes": len(raw),
                }
        except urllib.error.HTTPError as e:
            raw = e.read()
            text, encoding = _decode_bytes(raw)
            return {
                "url": url,
                "status": e.code,
                "headers": dict(e.headers.items()) if e.headers else {},
                "body": text,
                "encoding": encoding,
                "bytes": len(raw),
            }
        except (urllib.error.URLError, OSError) as e:
            raise ToolError(str(e))

    @mcp.tool
    def read_config() -> dict:
        """Read the team config — useful to discover teammate names and
        roles before sending messages. Member `prompt` fields are
        omitted since they're irrelevant to a peer."""
        try:
            cfg = _cs_teams.read_config(team)
        except FileNotFoundError:
            raise ToolError(f"team {team!r} not found")
        data = cfg.model_dump(by_alias=True)
        for m in data.get("members", []):
            m.pop("prompt", None)
        return data

    return mcp


def main() -> None:
    """Entry point for `claude-anyteam-wrapper` stdio MCP server."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    mcp = build_server()
    mcp.run()


if __name__ == "__main__":
    main()
