@echo off
setlocal
cd /d "%~dp0"

echo TokenFlow - FreeToken installer
echo.
if exist "%LOCALAPPDATA%\FreeToken\ft-bin.txt" (
  echo FreeToken engine is already installed:
  type "%LOCALAPPDATA%\FreeToken\ft-bin.txt"
  echo.
  echo You can return to start_tokenflow.cmd and use the Start FreeToken button.
  pause
  exit /b 0
)

set "INSTALLER=%TOKENFLOW_FREETOKEN_INSTALLER%"
if not defined INSTALLER if exist "%~dp0FreeToken-Setup-win-x64.exe" set "INSTALLER=%~dp0FreeToken-Setup-win-x64.exe"
if not defined INSTALLER if exist "%USERPROFILE%\Downloads\FreeToken-Setup-win-x64.exe" set "INSTALLER=%USERPROFILE%\Downloads\FreeToken-Setup-win-x64.exe"

if defined INSTALLER (
  echo Opening the official FreeToken installer. Please confirm installation in its window.
  start "FreeToken Setup" "%INSTALLER%"
  exit /b 0
)

echo FreeToken installer was not found.
echo Put FreeToken-Setup-win-x64.exe beside this script, or set TOKENFLOW_FREETOKEN_INSTALLER.
echo Official download page: https://www.flashml.ai/
start "FreeToken download" "https://www.flashml.ai/"
pause
exit /b 1
