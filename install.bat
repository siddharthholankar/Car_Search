@echo off
rem One-time setup on Windows: creates .venv, installs car-search, writes config.toml.
setlocal
cd /d "%~dp0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY goto nopython
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul || goto nopython

echo Creating virtual environment...
if not exist .venv %PY% -m venv .venv || goto failed
echo Installing car-search (this takes a minute or two)...
.venv\Scripts\python.exe -m pip install --quiet --upgrade pip || goto failed
.venv\Scripts\python.exe -m pip install --quiet -e ".[dashboard]" || goto failed
if not exist config.toml copy config.example.toml config.toml >nul

echo.
echo Done. Next:
echo   1. Edit config.toml with your zip, cars, max price and miles (Notepad is fine).
echo   2. In Chrome or Edge open chrome://extensions, turn on Developer mode,
echo      click "Load unpacked" and choose: %CD%\extension
echo   3. Double-click start.bat, then browse car listings.
pause
exit /b 0

:nopython
echo Python 3.11 or newer is needed.
echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH",
echo then run install.bat again.
pause
exit /b 1

:failed
echo.
echo Setup failed - see the messages above.
pause
exit /b 1
