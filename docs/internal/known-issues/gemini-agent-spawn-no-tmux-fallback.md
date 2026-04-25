# Known issue: `gemini-*` Agent-tool spawn doesn't auto-start without tmux

**First observed:** 2026-04-25 during live end-to-end testing of the v0.4.0 Gemini integration.
**Severity:** P2 product polish — the workaround is a one-line shell command but the friction is visible to first-run Gemini users on hosts without tmux.
**Status:** under-investigated.

## Symptom

On a host with no tmux server running, calling `Agent(team_name=..., name="gemini-alice", prompt=...)` from a leader session creates the team-config entry for `gemini-alice` with `backendType: "tmux"` and `tmuxPaneId: "%7"` (or similar) — but no actual `gemini-anyteam` process ever starts. The teammate appears in the config but is not alive; no `gemini-anyteam` is in `ps`, and no log file is written.

The leader has to manually run the spawn command:

```bash
setsid nohup uv run gemini-anyteam \
  --team <team> --name gemini-alice --cwd <cwd> \
  --backend headless --trust trusted \
  </dev/null >/tmp/gemini-alice.stdout 2>/tmp/gemini-alice.stderr &
```

Once that's running, the teammate registers and behaves correctly.

## Asymmetry vs Codex

In the same session and on the same host, `Agent(team_name=..., name="codex-foo", prompt=...)` *does* successfully start a `codex-teammate` process despite the same lack of tmux. The Codex spawn path appears to fall back to a bare subprocess; the Gemini spawn path apparently does not.

This asymmetry hasn't been root-caused. Possibilities:

- `claude-anyteam`'s spawn dispatcher has a Codex-specific subprocess fallback that wasn't ported when the `^gemini-` route was added in v0.4.0.
- The host-side Agent tool itself is selecting tmux backend for `gemini-*` and a different backend for `codex-*` based on naming heuristics, and the Gemini selection is wrong.
- Settings.json `CLAUDE_ANYTEAM_GEMINI_BINARY` is missing or stale in the running Claude Code session, causing the spawn shim to silently no-op for `^gemini-` while still routing `^codex-` correctly.

## Repro

1. Host with no tmux server (`tmux ls` returns "error connecting to ...").
2. Run `claude-anyteam install` (v0.4.0+) to write `CLAUDE_ANYTEAM_GEMINI_BINARY` into settings.json.
3. Restart Claude Code so it picks up the new settings.
4. From a leader session: `Agent(team_name="repro", name="gemini-test", prompt="hello")`.
5. Observe team-config entry created, no process running.
6. Same session: `Agent(team_name="repro", name="codex-test", prompt="hello")` — process IS running.

The above steps were reproduced informally on 2026-04-25 with v0.3.2 settings still loaded; the v0.4.0 path needs a clean retest after a Claude Code restart.

### Reproduction log

- **2026-04-25 ~14:48**: Reproduced again on the same WSL2 host, this time with v0.4.0 settings loaded (`CLAUDE_ANYTEAM_GEMINI_BINARY` confirmed present in `~/.claude/settings.json`). Spawned `gemini-flow-tester` via the `Agent` tool from a leader Claude Code session; team-config entry was written with `backendType: "tmux"` and `tmuxPaneId: "%19"`, but no `gemini-anyteam` process appeared in `ps`. Codex teammates spawned in the same team (`codex-installer-implementer`, `codex-code-reviewer`, `codex-image-gen`) all started cleanly. Workaround: manual `setsid nohup uv run gemini-anyteam ...` launch resurrected the teammate. **This confirms the bug is NOT a stale-settings artifact — it survives a clean v0.4.0 install.** Promotes Proper-fix-candidate #2 from "if real" to "real, needs fix".

## Workarounds in use today

- Manual `setsid nohup uv run gemini-anyteam ...` shell launch as shown above.
- Document workaround in install instructions for users without tmux.

## Proper fix candidates

1. **Confirm the asymmetry against v0.4.0 settings + restarted Claude Code.** Possible the issue was session-state, not a real bug — needs a clean repro.
2. **If real:** add the same bare-subprocess fallback to the `^gemini-` spawn path that the `^codex-` path uses. Likely a small diff in `spawn_shim.py` or wherever the dispatch picks a backend.
3. **Better long-term:** make `backendType` driven by host availability (psmux > tmux > bare subprocess) rather than baked in at spawn time.

## Why this matters

The "any-team" promise is undermined if Gemini teammates need a manual launch when Codex teammates spawn cleanly. First-time Gemini users on a non-tmux host will think the integration is broken.
