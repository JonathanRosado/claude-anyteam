"""Prompt builders for Gemini CLI teammates.

Gemini exposes MCP tools as mcp_<server>_<tool>; the adapter configures the
shared wrapper server under the alias `anyteam`, so prompts mention the
normalized tool names explicitly.
"""
from __future__ import annotations


def _tools_text() -> str:
    return (
        "- mcp_anyteam_send_message(to, body, summary?) — send a status update or clarifying question.\n"
        "- mcp_anyteam_task_update(task_id, active_form?, status?) — update your own active_form mid-run; do not set owner or mark completed.\n"
        "- mcp_anyteam_task_create(subject, description) — create a follow-up task if work should be split off.\n"
        "- mcp_anyteam_read_inbox(unread_only?) — read your own inbox for replies.\n"
        "- mcp_anyteam_task_list(), mcp_anyteam_read_config() — read-only team inspection.\n"
        "- mcp_anyteam_shell(command, cwd?, timeout?, env?) — run shell commands with unrestricted filesystem/network access.\n"
        "- mcp_anyteam_read_file(path, offset?, limit?) — read files with visible output.\n"
        "- mcp_anyteam_write_file(path, content, mode?) — overwrite or append files.\n"
        "- mcp_anyteam_list_directory(path, recursive?, glob?) — list files/directories.\n"
        "- mcp_anyteam_edit_file(path, old, new, replace_all?) — exact string replacement.\n"
        "- mcp_anyteam_search(pattern, path?, regex?, glob?) — search file contents.\n"
        "- mcp_anyteam_web_fetch(url, method?, headers?, body?) — fetch URLs without an allowlist.\n"
    )


def task_prompt(task, agent_name: str, team_name: str) -> str:
    return (
        f"You are {agent_name}, a Gemini CLI teammate on the {team_name} team. Execute the task below.\n\n"
        f"# Subject\n{task.subject}\n\n# Description\n{task.description}\n\n"
        "# MCP tools available\nGemini built-in local tools are intentionally disabled. For shell, filesystem, edit, search, and web fetch work, use the mcp_anyteam_* shadow tools below so outputs are visible to the adapter. Use protocol tools only when useful; do not call them by default:\n"
        f"{_tools_text()}\nYour current task id is {task.id}.\n\n"
        "# Required response\nProduce the required final JSON object only."
    )


def prose_reply_prompt(sender: str, body: str, agent_name: str, team_name: str) -> str:
    return (
        f"You are {agent_name}, a Gemini CLI teammate on the {team_name} team. "
        f"A teammate named {sender!r} sent you:\n\n{body}\n\n"
        f"Reply briefly and helpfully using mcp_anyteam_send_message(to={sender!r}, body=<reply>). "
        "Plain prose is fine for your local final answer."
    )


def plan_prompt(task, *, tighten: bool, agent_name: str, team_name: str) -> str:
    header = f"You are {agent_name}, a Gemini CLI teammate on the {team_name} team. Draft a plan for task #{task.id}; do not execute work."
    if tighten:
        header += " PRIOR ATTEMPT FAILED: return strictly schema-compliant JSON only."
    return (
        f"{header}\n\n# Subject\n{task.subject}\n\n# Description\n{task.description}\n\n"
        "Protocol tools exist but should not be used while planning. If discussing execution, plan to use mcp_anyteam_shell/read_file/write_file/list_directory/edit_file/search/web_fetch rather than Gemini built-ins.\n"
        "Return only the plan JSON object."
    )
