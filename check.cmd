@echo off
cd /d "%~dp0"
"ZBin\PythonScripts\.venv\Scripts\python.exe" "ZBin\PythonScripts\run_match.py" --check
if errorlevel 1 goto end
"ZBin\PythonScripts\.venv\Scripts\python.exe" "ZBin\PythonScripts\test_match.py"
:end
pause
