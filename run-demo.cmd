@echo off
setlocal
cd /d "%~dp0"
if not exist "PythonScripts\.venv\Scripts\python.exe" (
    echo Python environment is missing. Run uv sync --locked in PythonScripts first.
    pause
    exit /b 1
)
"PythonScripts\.venv\Scripts\python.exe" -u -X utf8 "PythonScripts\run_demo.py" %*
set "demo_exit=%ERRORLEVEL%"
echo.
echo Demo exited with code %demo_exit%.
pause
exit /b %demo_exit%
