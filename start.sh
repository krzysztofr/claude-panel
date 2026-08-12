#!/bin/bash
# Reczny start obu czesci na pierwszym planie (do testow, Ctrl+C konczy).
# Docelowo w tle: install.sh ustawia launchd, ktory startuje je sam.
cd "$(dirname "$0")" || exit 1

./run-server.sh &
SERVER_PID=$!
trap 'kill $SERVER_PID 2>/dev/null' EXIT

echo
echo "  panel: http://127.0.0.1:4747"
echo

# ekran pobiera dane z serwera - dajemy mu chwile na wstanie
sleep 2
./run-screen.sh
