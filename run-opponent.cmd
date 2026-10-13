@echo off
cd /d "%~dp0"
"PythonScripts\.venv\Scripts\python.exe" -X utf8 PythonScripts\run_match.py --config opponent.json
if errorlevel 1 pause
