@echo off
cd /d "%~dp0"
"PythonScripts\.venv\Scripts\python.exe" -X utf8 tools\verify_v21.py
if errorlevel 1 pause
