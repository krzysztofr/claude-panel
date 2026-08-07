@echo off
rem Rysowanie na ekranie 3.5" - tryb przyrostowy + stworek.
rem Port wykrywany po sygnaturze sprzetowej (AUTO), zmiana numeru COM nie psuje.
rem Zatrzymanie BEZ smieci na ekranie: utworz plik stop.flag w tym katalogu.
title Claude Panel - ekran
cd /d "%~dp0"
if not exist logs mkdir logs
> logs\screen.log echo [%date% %time%] start nadzorcy ekranu

:loop
python -u render.py --serial AUTO --interval 2 --blink 0.6 --tick 0.15 >> logs\screen.log 2>&1
echo [%date% %time%] petla ekranu zakonczyla sie, restart za 5 s >> logs\screen.log
timeout /t 5 /nobreak >nul
goto loop
