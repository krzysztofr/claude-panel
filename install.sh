#!/bin/bash
# Claude Panel - instalator dla macOS. Odpowiednik install.bat:
# sprawdza zaleznosci, opcjonalnie ustawia autostart (launchd) i startuje panel.
set -u
cd "$(dirname "$0")" || exit 1
BASE="$PWD"
AGENTS="$HOME/Library/LaunchAgents"

echo "============================================"
echo "  Claude Panel - installer (macOS)"
echo "============================================"
echo

# --- prerequisites -------------------------------------------------
if ! command -v node >/dev/null 2>&1; then
  echo "[!] Node.js not found."
  echo "    Install it (https://nodejs.org or: brew install node) and rerun."
  exit 1
fi
echo "[ok] Node.js $(node --version)"

if command -v uv >/dev/null 2>&1; then
  echo "[ok] uv $(uv --version | cut -d' ' -f2) - Python deps resolve automatically"
elif command -v python3 >/dev/null 2>&1; then
  echo "[ok] $(python3 --version) (no uv)"
  echo
  echo "[1/3] Installing Python packages (Pillow, pyserial)..."
  if ! python3 -m pip install --quiet Pillow pyserial; then
    echo "[!] pip install failed. Install uv instead (brew install uv)"
    echo "    and rerun - it handles the packages by itself."
    exit 1
  fi
  echo "     done"
else
  echo "[!] Neither uv nor python3 found."
  echo "    Install uv (brew install uv) and rerun."
  exit 1
fi

# --- claude code data ----------------------------------------------
echo
echo "[2/3] Checking Claude Code data..."
if [ -d "$HOME/.claude/projects" ]; then
  echo "     found $HOME/.claude/projects"
else
  echo "[!] $HOME/.claude/projects not found."
  echo "    Is Claude Code installed and used at least once on this machine?"
  echo "    The panel will run, but it will have nothing to show."
fi

chmod +x run-server.sh run-screen.sh start.sh

# --- autostart (launchd) -------------------------------------------
# KeepAlive robi to, co petle w run-*.bat na Windows: podnosi proces po
# padzie; ThrottleInterval to odpowiednik "timeout /t 5".
write_plist() {  # $1 = label suffix (server|screen)
  cat > "$AGENTS/com.claude-panel.$1.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.claude-panel.$1</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>$BASE/run-$1.sh</string></array>
  <key>WorkingDirectory</key><string>$BASE</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>5</integer>
  <key>StandardOutPath</key><string>$BASE/logs/$1.log</string>
  <key>StandardErrorPath</key><string>$BASE/logs/$1.log</string>
</dict>
</plist>
EOF
}

echo
echo "[3/3] Autostart"
read -r -p "     Add Claude Panel to login autostart (launchd)? [y/n] " yn
if [ "${yn:-n}" = "y" ] || [ "${yn:-n}" = "Y" ]; then
  mkdir -p "$AGENTS" logs
  write_plist server
  write_plist screen
  launchctl unload "$AGENTS/com.claude-panel.server.plist" 2>/dev/null
  launchctl unload "$AGENTS/com.claude-panel.screen.plist" 2>/dev/null
  launchctl load "$AGENTS/com.claude-panel.server.plist"
  launchctl load "$AGENTS/com.claude-panel.screen.plist"
  echo "     loaded - runs now and on every login (logs in $BASE/logs)"
  STARTED=1
else
  STARTED=0
fi

# --- start now -----------------------------------------------------
if [ "$STARTED" = "0" ]; then
  echo
  read -r -p "     Start Claude Panel now (foreground, Ctrl+C stops)? [y/n] " yn
  if [ "${yn:-n}" = "y" ] || [ "${yn:-n}" = "Y" ]; then
    exec ./start.sh
  fi
fi

echo
echo "============================================"
echo " Done."
echo " - browser dashboard:  http://127.0.0.1:4747"
echo " - the 3.5\" USB screen can be plugged in at ANY time"
echo "   (before or after install - it is auto-detected and"
echo "   reconnects by itself, the panel works without it too)"
echo " - screen language: PANEL_LANG=en in run-screen.sh (default pl)"
echo " - uninstall autostart:"
echo "     launchctl unload ~/Library/LaunchAgents/com.claude-panel.*.plist"
echo "     rm ~/Library/LaunchAgents/com.claude-panel.*.plist"
echo "============================================"
