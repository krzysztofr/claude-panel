@echo off
setlocal
title Claude Panel - installer
cd /d "%~dp0"
echo ============================================
echo   Claude Panel - installer
echo ============================================
echo.

rem --- prerequisites -------------------------------------------------
where node >nul 2>nul
if errorlevel 1 (
  echo [!] Node.js not found.
  echo     Install it from https://nodejs.org and run install.bat again.
  echo.
  pause
  exit /b 1
)
where python >nul 2>nul
if errorlevel 1 (
  echo [!] Python not found.
  echo     Install it from https://python.org - tick "Add python.exe to PATH" -
  echo     and run install.bat again.
  echo.
  pause
  exit /b 1
)
for /f "delims=" %%v in ('node --version') do echo [ok] Node.js %%v
for /f "delims=" %%v in ('python --version') do echo [ok] %%v

rem --- python packages -----------------------------------------------
echo.
echo [1/3] Installing Python packages (Pillow, pyserial)...
python -m pip install --quiet Pillow pyserial
if errorlevel 1 (
  echo [!] pip install failed - check your internet connection and try again.
  pause
  exit /b 1
)
echo      done

rem --- claude code data ----------------------------------------------
echo.
echo [2/3] Checking Claude Code data...
if exist "%USERPROFILE%\.claude\projects" (
  echo      found %USERPROFILE%\.claude\projects
) else (
  echo [!] %USERPROFILE%\.claude\projects not found.
  echo     Is Claude Code installed and used at least once on this machine?
  echo     The panel will run, but it will have nothing to show.
)

rem --- autostart -----------------------------------------------------
echo.
echo [3/3] Autostart
choice /c YN /m "     Add Claude Panel to Windows autostart"
if errorlevel 2 goto :skipauto
powershell -NoProfile -Command "$q=[char]34; $s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Startup')+'\Claude Panel.lnk'); $s.TargetPath='wscript.exe'; $s.Arguments=$q+'%~dp0panel-start.vbs'+$q; $s.WorkingDirectory='%~dp0'; $s.Description='Claude Panel - usage monitor'; $s.Save()"
if errorlevel 1 (
  echo [!] could not create the shortcut - add it manually: shell:startup
) else (
  echo      shortcut created in the Startup folder
)
:skipauto

rem --- start now -----------------------------------------------------
echo.
choice /c YN /m "     Start Claude Panel now"
if errorlevel 2 goto :done
wscript.exe "%~dp0panel-start.vbs"
echo      started - dashboard: http://127.0.0.1:4747
:done

echo.
echo ============================================
echo  Done.
echo  - browser dashboard:  http://127.0.0.1:4747
echo  - the 3.5" USB screen can be plugged in at ANY time
echo    (before or after install - it is auto-detected and
echo    reconnects by itself, the panel works without it too)
echo  - screen language: edit --lang in run-screen.bat
echo ============================================
echo.
pause
