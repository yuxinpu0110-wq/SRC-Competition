@echo off
cd /d "%~dp0"
"ZBin\PythonScripts\.venv\Scripts\python.exe" "ZBin\PythonScripts\match_launcher.py"
if errorlevel 1 pause
