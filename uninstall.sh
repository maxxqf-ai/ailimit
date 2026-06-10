#!/usr/bin/env bash
# uninstall.sh — stop and remove the ailimit LaunchAgent.
#
# Default behaviour (safe):
#   1. Unloads the LaunchAgent (gui/$UID form on modern macOS, else legacy).
#   2. Deletes the plist at ~/Library/LaunchAgents/com.ailimit.menubar.plist.
#   3. Leaves ~/.ailimit/{config.json, app/, venv/, logs/} untouched.
#      Your api keys are NEVER removed.
#
# Pass --purge to additionally remove the app/ and venv/ directories
# (config.json is still preserved — pass --purge-all to remove everything).

set -euo pipefail

LABEL="com.ailimit.menubar"
CONFIG_DIR="${AILIMIT_HOME:-$HOME/.ailimit}"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
UID_VAL="$(id -u)"

PURGE_APP=0
PURGE_ALL=0
case "${1:-}" in
  --purge)    PURGE_APP=1 ;;
  --purge-all) PURGE_APP=1; PURGE_ALL=1 ;;
  "")         ;;
  *) echo "usage: $0 [--purge|--purge-all]" >&2; exit 2 ;;
esac

echo ">> unloading LaunchAgent"
launchctl bootout "gui/${UID_VAL}" "$PLIST" 2>/dev/null || true
launchctl unload "$PLIST" 2>/dev/null || true

if [ -f "$PLIST" ]; then
  echo ">> removing $PLIST"
  rm -f "$PLIST"
else
  echo ">> no plist at $PLIST (already removed?)"
fi

if [ "$PURGE_ALL" -eq 1 ]; then
  echo ">> --purge-all: removing $CONFIG_DIR/{app,venv,logs,config.json}"
  rm -rf "$CONFIG_DIR/app" "$CONFIG_DIR/venv" "$CONFIG_DIR/logs" "$CONFIG_DIR/config.json"
elif [ "$PURGE_APP" -eq 1 ]; then
  echo ">> --purge: removing $CONFIG_DIR/app and $CONFIG_DIR/venv"
  rm -rf "$CONFIG_DIR/app" "$CONFIG_DIR/venv"
else
  echo ">> leaving $CONFIG_DIR/{app,venv,logs,config.json} in place"
  echo "   re-run with --purge to remove app+venv, --purge-all to remove everything"
fi

cat <<MSG

ailimit LaunchAgent removed.

  config preserved: $CONFIG_DIR/config.json
  app preserved:    $CONFIG_DIR/app        (--purge removes this)
  venv preserved:   $CONFIG_DIR/venv       (--purge removes this)
MSG
