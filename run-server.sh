#!/bin/bash
# Serwer danych panelu (macOS/Linux). Restart po padzie zapewnia launchd
# (KeepAlive w plist z install.sh) - tu nie ma petli.
cd "$(dirname "$0")" || exit 1
# launchd startuje z golym PATH - dopisujemy typowe lokalizacje node
export PATH="$PATH:/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin"
exec node server.js
