@echo off
rem Task Scheduler entry point for the monitor (separate task = dead man's switch).
rem Waits 5 minutes so that a pipeline run started at the same moment has finished.
cd /d "%~dp0.."
if not exist logs mkdir logs
ping -n 301 127.0.0.1 >nul
if exist "venv\Scripts\python.exe" (
  "venv\Scripts\python.exe" src\monitor.py >> logs\monitor.log 2>&1
) else if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" src\monitor.py >> logs\monitor.log 2>&1
) else (
  python src\monitor.py >> logs\monitor.log 2>&1
)
exit /b %ERRORLEVEL%
