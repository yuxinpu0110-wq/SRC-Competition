@echo off
cd /d "%~dp0"
echo LOCAL PRACTICE ONLY: Yellow goalie 2, right side. Close other yellow controllers first.
"ZBin\PythonScripts\.venv\Scripts\python.exe" -X utf8 -X faulthandler -u "ZBin\PythonScripts\run_match.py" --config "%~dp0opponent.json"
pause
