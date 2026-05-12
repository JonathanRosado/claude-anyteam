#!/bin/sh
set -eu

SETTINGS_PATH=${HOME}/.claude/settings.json
ORIENTATION_MESSAGE="claude-anyteam is installed; codex-*/gemini-*/kimi-* teammates require \`claude-anyteam team-agent <name> --team <team> ...\` before Agent(...), or the spawn shim refuses the bare prefix (override: CLAUDE_ANYTEAM_ALLOW_BARE_PREFIX=1). After spawn run \`claude-anyteam team-patch --all-external\`; inspect with \`team-roster\`. Docs: https://github.com/JonathanRosado/claude-anyteam"
DRIFT_WARNING='claude-anyteam: settings drifted — run `claude-anyteam install` to repair'

settings_has_required_env() {
  if [ ! -f "$SETTINGS_PATH" ]; then
    return 1
  fi

  if command -v python3 >/dev/null 2>&1; then
    if python3 - "$SETTINGS_PATH" <<'PY'
import json
import os
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    raise SystemExit(1)

if not isinstance(data, dict):
    raise SystemExit(1)

env = data.get("env")
if not isinstance(env, dict):
    raise SystemExit(1)

required_paths = [
    env.get("CLAUDE_CODE_TEAMMATE_COMMAND", ""),
    env.get("CLAUDE_ANYTEAM_BINARY", ""),
    env.get("CLAUDE_ANYTEAM_GEMINI_BINARY", ""),
    env.get("CLAUDE_ANYTEAM_KIMI_BINARY", ""),
]

for value in required_paths:
    if not isinstance(value, str) or not value.strip():
        raise SystemExit(1)
    if not Path(value).is_file() or not os.access(value, os.X_OK):
        raise SystemExit(1)

raise SystemExit(0)
PY
    then
      return 0
    fi
    return 1
  fi

  settings_env_path_ok() {
    key=$1
    line=$(grep -m 1 -E "\"$key\"[[:space:]]*:[[:space:]]*\"[^[:space:]\"][^\"]*\"" "$SETTINGS_PATH") || return 1
    value=${line#*\"$key\"}
    value=${value#*:}
    value=${value#*\"}
    value=${value%%\"*}

    [ -n "$value" ] && [ -f "$value" ] && [ -x "$value" ]
  }

  settings_env_path_ok "CLAUDE_CODE_TEAMMATE_COMMAND" \
    && settings_env_path_ok "CLAUDE_ANYTEAM_BINARY" \
    && settings_env_path_ok "CLAUDE_ANYTEAM_GEMINI_BINARY" \
    && settings_env_path_ok "CLAUDE_ANYTEAM_KIMI_BINARY"
}

if settings_has_required_env; then
  printf '%s\n' "$ORIENTATION_MESSAGE"
  exit 0
fi

printf '%s\n' "$DRIFT_WARNING"
exit 0
