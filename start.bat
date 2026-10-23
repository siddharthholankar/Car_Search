@echo off
rem Starts the capture server (its own window) and opens the dashboard.
cd /d "%~dp0"
if not exist .venv\Scripts\car-search.exe (
    echo Run install.bat first.
    pause
    exit /b 1
)
start "car-search server - keep open while you browse" .venv\Scripts\car-search.exe serve
.venv\Scripts\car-search.exe dashboard
