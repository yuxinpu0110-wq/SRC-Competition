@echo off
cd /d "%~dp0"
if not exist "..\Competition-baseline-0a20670\ZBin\PythonScripts\match_launcher.py" (
  echo Previous version missing. See the comparison guide.
  pause
  exit /b 1
)
"ZBin\PythonScripts\.venv\Scripts\python.exe" "..\Competition-baseline-0a20670\ZBin\PythonScripts\match_launcher.py"
if errorlevel 1 pause
