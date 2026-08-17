# Claude Panel

Podgląd zużycia Claude Code na komputerze: limity, aktywne sesje i tokeny —
na małym ekranie USB przy biurku oraz w przeglądarce.

---

## Co to pokazuje

**Limity** (oficjalne, nie szacowane): okno 5-godzinne, tygodniowe łączne
i tygodniowe dla Fable — w procentach, z czasem do resetu.

**Sesje** z nazwami: Twoje własne tytuły, a gdy ich nie ma — tytuł nadany
przez model, potem pierwszy prompt, a UUID dopiero w ostateczności.
Stan sygnalizuje kropka:

| Kropka | Znaczenie |
|---|---|
| błękitna, miga | sesja pracuje |
| **pomarańczowa, miga** (+ pomarańczowa nazwa) | **czeka na Twoją decyzję** |
| zielona, świeci | sesja skończyła |

Stan „pracuje" trzyma się od `UserPromptSubmit` do `Stop` — długa komenda
albo długie myślenie (>45 s bez zapisu w transkrypcie) nie zieleni kropki
w połowie tury.

**Wykres godzinowy** — tokeny wyjściowe na godzinę z ostatnich 24 h, na dole;
bieżąca godzina na zielono, czerwona pionowa linia o północy, zielone o
9:00/17:00 w dni robocze. Surowe liczniki
tokenów (okno 5h, model z limitem w 7 dniach) zostały w panelu przeglądarkowym.

---

## Sprzęt

Ekran **Turing Smart Screen 3,5" / klon „USBMonitor"**, natywnie **320×480
w pionie**, na porcie szeregowym (`VID_1A86 & PID_5722`, sygnatura
`USB35INCHIPSV2`).

To **nie jest monitor** — Windows nie widzi go jako ekranu i nie da się na
niego przeciągnąć okna. Obraz wysyła się protokołem szeregowym jako
RGB565 little-endian. Protokół odtworzony z
[turing-smart-screen-python](https://github.com/mathoudebine/turing-smart-screen-python)
(MIT), rewizja A.

Numer portu **nie jest wpisany na sztywno** — `turing.auto_detect()` znajduje
go po sygnaturze sprzętowej, więc zmiana COM po przelogowaniu USB nic nie psuje.

👉 **Dokładnie ten ekran, którego używam, kupisz tu: https://temu.to/k/ezbo6d0qn7j**
*(link afiliacyjny — dla Ciebie ta sama cena, dla mnie drobna prowizja.
Każdy klon `1A86:5722` zadziała tak samo.)*

---

## Pliki

| Plik | Rola |
|---|---|
| `server.js` | zbiera dane, serwuje API i stronę na porcie **4747** |
| `public/index.html` | dashboard w przeglądarce |
| `render.py` | rysuje klatkę 320×480 i wysyła na ekran |
| `turing.py` | sterownik ekranu + wykrywanie portu |
| `run-server.bat` / `run-screen.bat` | (Windows) nadzorcy — restartują proces po awarii |
| `panel-start.vbs` | (Windows) uruchamia oba bez okien konsoli |
| `start.bat` | (Windows) ręczne odpalenie samego serwera |
| `install.sh` / `start.sh` / `run-*.sh` | (macOS) instalator, ręczny start i skrypty startowe dla launchd |
| `usage-cache.json` | ostatni udany odczyt limitów (tworzony automatycznie) |
| `logs/` | logi serwera i ekranu, czyszczone przy starcie |

---

## Skąd biorą się dane

**Limity** — z `https://api.anthropic.com/api/oauth/usage`, czyli tego samego
endpointu, który wywołuje Claude Code. Token OAuth czytany jest z
`~/.claude/.credentials.json` **przy każdym odpytaniu** (dzięki temu sam
podchwytuje odświeżenie tokenu) i **nigdzie nie jest zapisywany ani logowany**;
leci wyłącznie do `api.anthropic.com`.

Zapasowo panel czyta `cachedUsageUtilization` z `~/.claude.json`. Wygrywa
zawsze **świeższy** odczyt, nie źródło. Panel pokazuje wiek danych i ich
pochodzenie („API" albo „plik").

**Sesje i tokeny** — z transkryptów w `~/.claude/projects/**/*.jsonl`,
czytanych przyrostowo (serwer pamięta, ile bajtów już przeczytał).
Podkatalogi `subagents/` i `workflows/` należą do sesji-rodzica i doklejają
się do niej jako licznik agentów.

---

## Częstotliwości

| Co | Jak często |
|---|---|
| tick animacji stworka | 0,3 s |
| miganie kropek | 0,6 s |
| przeglądarka | 2 s |
| **odświeżenie danych + diff na ekran** | **2 s** |
| skan transkryptów | 4 s |
| licznik procesów | 10 s |
| **odpytanie API o limity** | **120 s** |

## Rysowanie przyrostowe (od 2026-08-04)

Pełna klatka (300 KB = ~3 s przy ~100 KB/s kabla) idzie **tylko** na start
i po reconnect. Potem `dirty_rects()` porównuje nową klatkę z modelem tego,
co wisi na ekranie (`model` w `main()`), i wysyła wyłącznie zmienione
prostokąty — typowa zmiana to **~200 B zamiast 307 200 B**. Gdy zmiana
przekracza 45% ekranu (np. pojawienie się banera przesuwa cały układ),
idzie pełna klatka.

Strefa `MASCOT_ZONE` w nagłówku jest **wyłączona z diffa** — jedynym jej
pisarzem jest animacja stworka. Po każdej pełnej klatce stworek dostaje
`force = True`, bo baza rysuje jego strefę pustą.

## Stworek (nagłówek)

Pikselowa maskotka (siatka 2×2 px) zamiast napisu CLAUDE:

| Stan | Kiedy | Wygląd |
|---|---|---|
| śpi | tokeny ≈ 0 i nic nie pracuje | leży, „Z" nad głową |
| stoi | sesja pracuje, tokeny nie lecą | mruga co ~3 s |
| chodzi/biega | tokeny lecą (tempo z okna 90 s) | 1–4 px/tick, przy biegu kurz |
| czeka | sesja w stanie „czeka" | stoi, miga błękitnym dymkiem „!" |
| + pot | najgorszy limit ≥ 85% | pomarańczowe krople przy głowie |

Tempo tokenów liczone z przyrostu `window5h.out` w oknie 90 s (spadki —
przesuwanie się okna, resety — obcinane do zera). Każdy tick wysyła tylko
mały prostokąt (unia starej i nowej pozycji, ~2–4 KB), nigdy pełną klatkę.

---

## Autostart

Skrót **„Claude Panel"** w `shell:startup` wskazuje na `panel-start.vbs`.
Ten uruchamia oba nadzorcze `.bat` bez okien konsoli; każdy trzyma swój
proces w pętli i podnosi go po 5 sekundach, gdyby padł.

Zatrzymanie: usuń skrót z Autostartu i ubij procesy (`node`, `python`
i dwa `cmd`).

Na macOS autostart robi `install.sh`: dwa agenty launchd
(`com.claude-panel.server` / `.screen` w `~/Library/LaunchAgents`)
z `KeepAlive` — launchd sam podnosi proces 5 s po awarii, logi w `logs/`.
Zatrzymanie: `launchctl unload ~/Library/LaunchAgents/com.claude-panel.*.plist`
i usunięcie plists.

---

## Zmienne środowiskowe

| Zmienna | Działanie |
|---|---|
| `PANEL_PORT` | port serwera (domyślnie 4747) |
| `PANEL_USAGE_POLL_MS` | częstotliwość odpytywania API (domyślnie 120000) |
| `PANEL_NO_API=1` | wyłącza odpytywanie API, zostaje samo czytanie pliku |

---

## Pułapki, które już kosztowały czas

**Ekran nie jest monitorem.** Żadna metoda „przeciągnij okno" nie zadziała.

**Wypięcie USB unieważnia uchwyt portu na zawsze.** Pętla otwierająca port raz
przy starcie wpada w nieskończoną serię błędów „Urządzenie nie rozpoznaje
polecenia". Dlatego `ScreenLink` przy błędzie zrywa połączenie i buduje je od
nowa — **razem z orientacją i jasnością**, które ekran gubi po odcięciu
zasilania, oraz z wymuszeniem pełnej klatki (samo miganie kropek nie odbuduje
pustego ekranu).

**Świeży odczyt limitów tylko w pamięci = procent potrafi się cofnąć.** Po
restarcie serwera nowsza wartość znikała i panel wracał do starszego pliku,
pokazując spadek zużycia, którego nie było. Stąd `usage-cache.json`.
Z tego samego powodu meldunki hooków trafiają do `alerts-cache.json` —
restart bez niego wskrzeszał zamknięte sesje i gubił stany kropek.

**Endpoint zużycia potrafi odpowiedzieć HTTP 429.** Po odmowie panel czeka
coraz dłużej (2, 4, 8… do 10 min) i jawnie przełącza się na plik, żółcąc wiek
danych. Ekran dopisuje wtedy kod HTTP obok wieku (np. „8h 429"). Pauza jest sprawdzana **przed** licznikiem czasu — inaczej po jej
końcu trzeba by odczekać jeszcze całe okno 120 s.

**Zmiana w `render.py` nie działa, dopóki nie ubijesz pętli.** Działający
proces nadpisuje `preview.png` starą wersją co kilka sekund. Ubij `python`,
nadzorca podniesie go z nowym kodem.

**Nie migaj przez przerysowywanie całej klatki.** Pełny obraz to 300 KB
i ~3 s transmisji. Kropki migają jako kwadraciki 16×16 (512 bajtów).

**Twardy kill w połowie DISPLAY_BITMAP desynchronizuje parser ekranu.**
Urządzenie czeka na brakujące piksele i zjada kolejne komendy jako dane —
śmieci na ekranie, a model w RAM kłamie, że klatka wisi. Dlatego:
(a) zatrzymanie pętli TYLKO przez utworzenie pliku `stop.flag` (wychodzi
łagodnie między zapisami), (b) `Turing.resync()` przy każdym connect wysyła
pełną klatkę zer — najgorszy zaległy licznik to 307 199 bajtów, więc po tym
parser NA PEWNO jest w trybie komend, niezależnie od przesunięcia ramki.

**`write_timeout` na porcie jest obowiązkowy.** Bez niego zawieszony ekran
(rtscts!) blokuje `write()` na zawsze — proces żyje, nadzorca nie reaguje,
panel martwy do restartu komputera. Z timeoutem (5 s) write rzuca wyjątek
i ScreenLink robi reconnect.

**Próg pełnej klatki wysoko (0.85).** Pełna klatka to ~3 s blokady petli;
prostokąty do ~85% ekranu i tak kosztują mniej.

**`127.0.0.1`, nigdy `localhost`.** Windows dla `localhost` próbuje najpierw
IPv6 `::1`, na którym serwer NIE słucha (bind na 0.0.0.0 = tylko IPv4),
i każde żądanie łapie ~2 s kary zanim spadnie na IPv4. Objaw był podstępny:
żadnego błędu, wszystko „działa", tylko pętla ekranu chodziła 15× wolniej
(tick 2,3 s zamiast 0,15 s) i stworek pełzał niezauważalnie. Diagnoza
wyłącznie przez profilowanie per-komponent: fetch() 2063 ms vs reszta <20 ms.

**Porcje zapisu na port muszą być DUŻE (64 KB).** Referencyjna biblioteka
dzieli dane na `szerokość×8` bajtów — przy wąskiej łatce stworka to
288-bajtowe strzępki, a każdy osobny write na USB (CH34x) ma stały narzut
~25 ms. Efekt: łatka 2,7 KB kosztowała ~280 ms zamiast ~30 ms i animacja
zwalniała ~4×, mimo poprawnego kodu logiki.

**Awaria musi być widoczna.** Strona w przeglądarce po utracie kontaktu
z serwerem przykrywa się nakładką „DANE ZAMROŻONE" z licznikiem sekund.
Wcześniej zostawiała stare liczby wyglądające na aktualne — przy 92%
i statusie krytycznym mogło to wpuścić prosto w ścianę limitu.

---

## Czego brakuje

**Hooki `Notification` / `Stop` / `UserPromptSubmit` / `SessionEnd`** muszą
być wpięte w `~/.claude/settings.json` (u autora już są). Bez nich **żadna
sesja nie wejdzie w stan „czeka"**, więc pomarańczowa kropka i baner
„CLAUDE CZEKA NA CIEBIE" nigdy się nie zapalą, a stany zgaduje sam ruch
w transkrypcie. Serwer ma gotowy endpoint `POST /api/hook`.

Do wklejenia obok istniejących kluczy w `~/.claude/settings.json`
(dla każdego z trzech zdarzeń ta sama komenda):

```json
"hooks": {
  "Notification": [
    { "hooks": [ { "type": "command", "command": "curl.exe -s -m 2 -X POST -H \"Content-Type: application/json\" --data-binary @- http://127.0.0.1:4747/api/hook" } ] }
  ],
  "Stop": [ { "hooks": [ { "type": "command", "command": "curl.exe -s -m 2 -X POST -H \"Content-Type: application/json\" --data-binary @- http://127.0.0.1:4747/api/hook" } ] } ],
  "UserPromptSubmit": [ { "hooks": [ { "type": "command", "command": "curl.exe -s -m 2 -X POST -H \"Content-Type: application/json\" --data-binary @- http://127.0.0.1:4747/api/hook" } ] } ]
}
```

`-m 2` jest obowiązkowe: gdyby panel nie działał, hook bez limitu czasu
**zablokowałby Claude Code przy każdym prompcie**.

Test bez hooków — wyślij zdarzenie ręcznie, podstawiając `sid` z `/api/state`:

```powershell
$b = @{ session_id='<sid>'; hook_event_name='Notification'; cwd='C:\test'; message='test' } | ConvertTo-Json -Compress
Invoke-RestMethod http://localhost:4747/api/hook -Method Post -Body $b -ContentType 'application/json'
```

Sprzątnięcie: to samo z `hook_event_name='Stop'`.

---

## Notatka z pomiarów (dlaczego przyrostowo)

Konwersja pikseli w Pythonie to tylko 0,135 s na klatkę — wąskim gardłem
jest kabel (~100 KB/s), nie procesor. Limitów ani wykrywania sesji ekran
**nie spowalnia** — te idą przez sieć i dysk w procesie Node, zupełnie
osobno od kabla USB.
