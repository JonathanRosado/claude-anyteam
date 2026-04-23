"""System-prompt and task-prompt builders for v7.

v7 gives Codex MCP tool access to the narrowed wrapper (`wrapper_server`),
so the prompt must teach Codex about those tools and when to use them.
Keep the language tight — longer prompts are harder for Codex to follow.

`_plan_prompt` and `_task_prompt` in `loop.py` call into these builders
so the prompt contract is in one place and visible to reviewer at
validation time.
"""

from __future__ import annotations


def v7_task_prompt(task, agent_name: str, team_name: str) -> str:
    """Prompt for a task-completion Codex invocation (v7).

    Teaches Codex about the MCP tools available via the wrapper, asks
    for the usual schema-conformant task-complete response, and stays
    short — one screenful max.
    """
    return (
        f"You are {agent_name}, a Codex teammate on the {team_name} team. "
        f"Execute the task below, then produce a JSON object matching the "
        f"attached schema.\n\n"
        f"# Subject\n{task.subject}\n\n"
        f"# Description\n{task.description}\n\n"
        f"# MCP tools available\n"
        f"You can call these protocol tools while working. Use them when "
        f"it would be useful to your teammates — do not call them by "
        f"default:\n"
        f"- send_message(to, body, summary?) — send a status update or "
        f"clarifying question to a teammate (e.g., 'team-lead').\n"
        f"- task_update(task_id, active_form?, status?) — update your own "
        f"task's `active_form` mid-run ('writing tests', 'running "
        f"verification') so teammates can see progress. Do not set owner; "
        f"do not mark completed from here — the adapter owns completion.\n"
        f"- task_create(subject, description) — create a new task if you "
        f"discover work that should be split off.\n"
        f"- read_inbox(unread_only?) — read your own inbox, useful for "
        f"picking up a teammate's reply.\n"
        f"- task_list(), read_config() — read-only inspection of team state.\n"
        f"\n"
        f"Your current task id is {task.id}. Destructive lifecycle operations "
        f"are deliberately unavailable.\n\n"
        f"# Required response\n"
        f"Produce a JSON object with fields `files_changed` (list of paths "
        f"created or modified) and `summary` (one-paragraph description of "
        f"what you did and why). Do not produce any other output at the end."
    )


def v7_prose_reply_prompt(sender: str, body: str, agent_name: str, team_name: str) -> str:
    """Prompt for a schema-free prose reply to a peer message.

    Used when an idle Codex adapter receives a direct message from a teammate.
    Codex should reply conversationally; no task is being executed, no schema
    is required.
    """
    return (
        f"You are {agent_name}, a Codex teammate on the {team_name} team. "
        f"A teammate named {sender!r} sent you a direct message:\n\n"
        f"{body}\n\n"
        f"Reply briefly and helpfully. Do not execute any code unless explicitly "
        f"asked. Use the `send_message` MCP tool to deliver your reply to "
        f"{sender!r} — call `send_message(to={sender!r}, body=<your reply>)`. "
        f"Do not produce a structured JSON object; plain prose is correct here."
    )


def v7_plan_prompt(task, *, tighten: bool, agent_name: str, team_name: str) -> str:
    """Prompt for plan-mode (opt-in) Codex invocation in v7.

    Same MCP-tool context as v7_task_prompt, but the task is to draft
    a plan rather than execute. Codex should NOT call tools here —
    the output is a plan for the lead to approve, not mid-execution work.
    Tools remain advertised for consistency; the prompt discourages their
    use at plan time.
    """
    header = (
        f"You are {agent_name}, a Codex teammate on the {team_name} team. "
        f"Draft an implementation plan for team task #{task.id}. Do NOT "
        f"execute the work — produce a plan only."
    )
    if tighten:
        header += (
            " PRIOR ATTEMPT FAILED: your previous output did not conform to "
            "the required schema. Produce a minimal, strictly schema-compliant "
            "plan this time — every step must have a `summary`; `risks` must "
            "be a list of strings (use `[]` explicitly if none)."
        )
    return (
        f"{header}\n\n"
        f"# Subject\n{task.subject}\n\n"
        f"# Description\n{task.description}\n\n"
        f"# MCP tools available (do NOT use during plan generation)\n"
        f"Protocol tools are available in this session but you should not "
        f"call them while drafting a plan. The plan is for the lead to "
        f"review; execution happens later.\n\n"
        f"# Required response\n"
        f"Produce a JSON object matching the attached schema with fields:\n"
        f"- steps: ordered list; each step must have a `summary` and may "
        f"have `files_touched` (list of paths).\n"
        f"- risks: list of strings (use `[]` if none).\n"
        f"- estimated_time (optional): rough wall-clock estimate.\n"
        f"Do not produce prose outside the JSON object."
    )
