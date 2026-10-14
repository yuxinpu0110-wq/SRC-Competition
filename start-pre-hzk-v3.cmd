@echo off
cd /d "%~dp0"
"ZBin\PythonScripts\.venv\Scripts\python.exe" "ZBin\PythonScripts\vision_relay.py"
"ZBin\PythonScripts\.venv\Scripts\python.exe" "tools\run_v2_baseline.py"
