#!/usr/bin/env bash
# install.sh — install ailimit menu bar app + LaunchAgent on macOS.
#
# What it does:
#   1. Copies the project files to ~/.ailimit/app/
#   2. Creates a venv at ~/.ailimit/venv/ and installs requirements
#   3. Writes a LaunchAgent plist at ~/Library/LaunchAgents/com.ailimit.menubar.plist
#   4. (Re)loads that LaunchAgent so the status bar item appears at login
#
# Re-running is safe: files are overwritten, the venv is reused, the agent is
# reloaded. Your ~/.ailimit/config.json (api keys) is never touched.
#
# Override the home dir with AILIMIT_HOME=/some/path before running.

set -euo pipefail

LABEL="com.ailimit.menubar"
CONFIG_DIR="${AILIMIT_HOME:-$HOME/.ailimit}"
DEST="$CONFIG_DIR/app"
VENV="$CONFIG_DIR/venv"
LOG_DIR="$CONFIG_DIR/logs"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Prefer a python with a rumps wheel (3.11+). System python3 on old macOS
# is 3.8/3.9 and won't install rumps; we surface that as a clear error
# from pip rather than guessing here.
pick_python() {
  for cand in python3.13 python3.12 python3.11 python3; do
    if command -v "$cand" >/dev/null 2>&1; then
      echo "$cand"
      return 0
    fi
  done
  echo "no python3 on PATH" >&2
  return 1
}

PYTHON_BIN="$(pick_python)"
echo ">> using python: $PYTHON_BIN"

# --- 1. Copy project files ---
echo ">> installing app files to $DEST"
mkdir -p "$DEST"
for f in usage.py app.py providers.py settings.py menubar.py requirements.txt README.md config.example.json; do
  cp "$SRC_DIR/$f" "$DEST/$f"
done

# --- 2. venv + pip ---
echo ">> preparing venv at $VENV"
mkdir -p "$CONFIG_DIR" "$LOG_DIR"
"$PYTHON_BIN" -m venv "$VENV" 2>/dev/null || true
if [ ! -x "$VENV/bin/python" ]; then
  echo "failed to create venv at $VENV" >&2
  exit 1
fi
echo ">> installing requirements (this is where rumps / browser-cookie3 get pulled)"
"$VENV/bin/python" -m pip install --upgrade pip >/dev/null
"$VENV/bin/python" -m pip install -r "$DEST/requirements.txt"

# --- 3. LaunchAgent plist ---
VENV_PY="$VENV/bin/python"
echo ">> writing LaunchAgent plist to $PLIST"
mkdir -p "$(dirname "$PLIST")"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${VENV_PY}</string>
    <string>${DEST}/menubar.py</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${DEST}</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <false/>
  <key>ProcessType</key>
  <string>Interactive</string>
  <key>StandardOutPath</key>
  <string>${LOG_DIR}/menubar.out.log</string>
  <key>StandardErrorPath</key>
  <string>${LOG_DIR}/menubar.err.log</string>
</dict>
</plist>
EOF

# --- 4. (Re)load the LaunchAgent ---
UID_VAL="$(id -u)"
echo ">> unloading any existing agent (ignore failures)"
launchctl bootout "gui/${UID_VAL}" "$PLIST" 2>/dev/null || true
launchctl unload "$PLIST" 2>/dev/null || true

echo ">> loading LaunchAgent"
loaded=0
if launchctl bootstrap "gui/${UID_VAL}" "$PLIST" 2>/dev/null; then
  loaded=1
elif launchctl load "$PLIST" 2>/dev/null; then
  loaded=1
fi
if [ "$loaded" -eq 0 ]; then
  echo "warning: launchctl could not load $PLIST — check the plist and Console.app" >&2
fi

cat <<MSG

ailimit installed.

  config:  $CONFIG_DIR/config.json  (your keys, never overwritten)
  app:     $DEST
  venv:    $VENV
  logs:    $LOG_DIR/menubar.{out,err}.log
  plist:   $PLIST

Next steps:
  1. The "AI …" item should appear in your menu bar within a few seconds.
  2. From its dropdown: "Open Settings" → fill in glm / minimax api_key, save.
  3. To uninstall: ./uninstall.sh  (your config.json is preserved)
MSG
