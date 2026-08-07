@echo off
title Claude Panel
cd /d "%~dp0"
echo Startuje Claude Panel...
echo.
echo   ekran:  http://localhost:4747
echo.
node server.js
pause
