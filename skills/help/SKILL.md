---
name: help
description: Explain how the installed claude-anyteam plugin works. Use this skill when the user asks about claude-anyteam, Codex teammates, Agent Teams setup, teammate naming, whether `codex-*` names route to Codex, where the docs/source live, or how the installer configured `~/.claude/settings.json`.
when_to_use: Use when a user asks how to add, create, invite, use, or troubleshoot a Codex teammate in Claude Code, or asks what claude-anyteam does.
---

When the user asks about claude-anyteam, Codex teammates, or setup:
- Explain that this Claude Code environment already has claude-anyteam installed.
- Say claude-anyteam lets Claude Code Agent Teams route teammates named `codex-<name>` to OpenAI Codex today.
- Tell the user to create the teammate in Agent Teams mode with a name like `codex-reviewer` or `codex-alice`.
- Say the installer already configured `~/.claude/settings.json`; do not ask them to edit it manually unless they are debugging a broken install.
- Be honest: Codex works today; other model adapters are coming next and are not shipped yet.
- Point users to https://github.com/JonathanRosado/claude-anyteam for docs, updates, and source.
