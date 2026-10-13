@echo off
cd /d "%~dp0"
"ZBin\PythonScripts\.venv\Scripts\python.exe" -X utf8 "ZBin\PythonScripts\match_launcher.py" --config "%~dp0configs\v2-foundation.json"
if errorlevel 1 pause
