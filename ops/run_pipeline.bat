@echo off
cd /d "%~dp0.."
if not exist logs mkdir logs
if exist "venv\Scripts\python.exe" (
  "venv\Scripts\python.exe" src\run_pipeline.py >> logs\pipeline.log 2>&1
) else if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" src\run_pipeline.py >> logs\pipeline.log 2>&1
) else (
  python src\run_pipeline.py >> logs\pipeline.log 2>&1
)
exit /b %ERRORLEVEL%