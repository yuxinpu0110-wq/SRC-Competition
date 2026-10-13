@echo off
cd /d "%~dp0"
if not exist "..\Competition-baseline-5dc5d03\ZBin\PythonScripts\match_launcher.py" (
  echo Baseline worktree missing. See the comparison guide.
  pause
  exit /b 1
)
"ZBin\PythonScripts\.venv\Scripts\python.exe" "..\Competition-baseline-5dc5d03\ZBin\PythonScripts\match_launcher.py"
if errorlevel 1 pause
