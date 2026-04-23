#!/bin/sh
set -eu

if [ "$#" -lt 1 ]; then
  echo "usage: $0 <command-name> [args...]" >&2
  exit 64
fi

TARGET_NAME=$1
shift

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

FILTERED_PATH=''
OLD_IFS=$IFS
IFS=:
for entry in $PATH; do
  [ -n "$entry" ] || entry=.
  if [ "$entry" = "$SCRIPT_DIR" ]; then
    continue
  fi
  if [ -z "$FILTERED_PATH" ]; then
    FILTERED_PATH=$entry
  else
    FILTERED_PATH=${FILTERED_PATH}:$entry
  fi
done
IFS=$OLD_IFS

RESOLVED=''
if [ -n "$FILTERED_PATH" ]; then
  RESOLVED=$(PATH=$FILTERED_PATH command -v -- "$TARGET_NAME" 2>/dev/null || true)
else
  RESOLVED=$(command -v -- "$TARGET_NAME" 2>/dev/null || true)
fi

if [ -z "$RESOLVED" ]; then
  echo "claude-anyteam plugin: '$TARGET_NAME' is not installed outside the plugin wrappers." >&2
  echo "Install the Python package first (for example: uv add claude-anyteam), then restart Claude Code or run /reload-plugins." >&2
  exit 127
fi

exec "$RESOLVED" "$@"
