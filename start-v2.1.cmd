@echo off
cd /d "%~dp0"
"ZBin\PythonScripts\.venv\Scripts\python.exe" "ZBin\PythonScripts\vision_relay.py"
if errorlevel 1 (
  pause
  exit /b 1
)
"ZBin\PythonScripts\.venv\Scripts\python.exe" "ZBin\PythonScripts\match_launcher.py" --config "configs\v2.1-foundation.json"
