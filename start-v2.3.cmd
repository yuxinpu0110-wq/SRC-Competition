@echo off
cd /d "%~dp0"
set "MATCH_PYTHON=ZBin\PythonScripts\.venv\Scripts\python.exe"
if not exist "%MATCH_PYTHON%" (
  echo Matching Python environment missing.
  pause
  exit /b 1
)
"%MATCH_PYTHON%" -X utf8 "ZBin\PythonScripts\run_match.py" --config "configs\v2.3-development.json" --check-config
if errorlevel 1 (
  pause
  exit /b 1
)
echo V2.3 LOCAL DEVELOPMENT: full terminal plans. Dynamic fast finish is NOT yet accepted.
"%MATCH_PYTHON%" -X utf8 "ZBin\PythonScripts\match_launcher.py" --config "configs\v2.3-development.json"
