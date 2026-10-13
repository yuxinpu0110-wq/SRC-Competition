@echo off
cd /d "%~dp0"
"PythonScripts\.venv\Scripts\python.exe" -X utf8 tools\version_entry.py v2.1
if errorlevel 1 pause
