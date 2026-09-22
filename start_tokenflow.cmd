@echo off
setlocal
cd /d "%~dp0"

set "PYTHON=python"
%PYTHON% -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
if errorlevel 1 (
    set "PYTHON=py -3"
    %PYTHON% -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
    if errorlevel 1 goto :missing_python
)

echo [1/2] Compiling TokenFlow...
%PYTHON% -m py_compile tokenflow.py freetoken_provider.py freetoken_manager.py
if errorlevel 1 goto :compile_failed

echo [2/2] Starting TokenFlow at http://127.0.0.1:8765
if exist "%LOCALAPPDATA%\FreeToken\ft-bin.txt" echo FreeToken engine detected.
if not exist "%LOCALAPPDATA%\FreeToken\ft-bin.txt" echo FreeToken engine not detected. Use install_freetoken.cmd when needed.
%PYTHON% tokenflow.py
goto :eof

:missing_python
echo Python was not found. Please install Python 3.10+ and add it to PATH.
pause
exit /b 1

:compile_failed
echo Compilation failed.
pause
exit /b 1
