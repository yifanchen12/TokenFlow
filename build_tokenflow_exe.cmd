@echo off
setlocal
cd /d "%~dp0"

set "PYTHON=python"
%PYTHON% -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
if errorlevel 1 goto :missing_python

%PYTHON% -m PyInstaller --version >nul 2>nul
if errorlevel 1 (
  echo PyInstaller is not installed.
  echo Install it once with: %PYTHON% -m pip install pyinstaller
  pause
  exit /b 1
)

echo Compiling TokenFlow modules...
%PYTHON% -m py_compile tokenflow.py freetoken_provider.py freetoken_manager.py token_counter.py model_providers.py document_parser.py tokenflow_store.py pc_agent.py jev_provider.py ui_page.py launcher.py
if errorlevel 1 goto :compile_failed

echo Building dist\TokenFlow.exe...
%PYTHON% -m PyInstaller --noconfirm --clean --onefile --windowed --name TokenFlow launcher.py
if errorlevel 1 goto :build_failed

echo Done: dist\TokenFlow.exe
pause
exit /b 0

:missing_python
echo Python 3.10+ was not found.
pause
exit /b 1

:compile_failed
echo Python compilation failed.
pause
exit /b 1

:build_failed
echo PyInstaller build failed.
pause
exit /b 1
