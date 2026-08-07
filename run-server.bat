@echo off
rem Serwer danych panelu. Petla podnosi go z powrotem, gdyby padl.
title Claude Panel - serwer
cd /d "%~dp0"
if not exist logs mkdir logs
> logs\server.log echo [%date% %time%] start nadzorcy serwera

:loop
node server.js >> logs\server.log 2>&1
echo [%date% %time%] serwer zakonczyl sie, restart za 5 s >> logs\server.log
timeout /t 5 /nobreak >nul
goto loop
