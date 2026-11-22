@echo off
rem Task Scheduler entry point: cd into the repository, run the pipeline with the venv's Python.
rem Output goes to logs\pipeline.log (cron/Task Scheduler would otherwise show it nowhere).
cd /d "%~dp0.."
if not exist logs mkdir logs
"venv\Scripts\python.exe" src\run_pipeline.py >> logs\pipeline.log 2>&1
exit /b %ERRORLEVEL%
