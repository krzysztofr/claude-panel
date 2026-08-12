#!/bin/bash
# Rysowanie na ekranie 3.5" (macOS/Linux) - tryb przyrostowy + stworek.
# Port wykrywany po sygnaturze sprzetowej (AUTO), zmiana nazwy portu nie psuje.
# Zatrzymanie BEZ smieci na ekranie: utworz plik stop.flag w tym katalogu.
# Restart po padzie zapewnia launchd (KeepAlive w plist z install.sh).
# Jezyk ekranu: zmienna PANEL_LANG=pl|en.
cd "$(dirname "$0")" || exit 1
export PATH="$PATH:/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin"

# uv czyta zaleznosci (pillow, pyserial) z naglowka render.py i sam je
# dostarcza; bez uv trzeba je miec zainstalowane w python3 (patrz install.sh)
if command -v uv >/dev/null 2>&1; then
  exec uv run render.py --serial AUTO --interval 2 --blink 0.6 --tick 0.15
else
  exec python3 -u render.py --serial AUTO --interval 2 --blink 0.6 --tick 0.15
fi
