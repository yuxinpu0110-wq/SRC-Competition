@echo off
cd /d "%~dp0"
set "MATCH_PYTHON=ZBin\PythonScripts\.venv\Scripts\python.exe"
if not exist "%MATCH_PYTHON%" (
  echo Matching Python environment missing.
  pause
  exit /b 1
)
"%MATCH_PYTHON%" -X utf8 "ZBin\PythonScripts\run_match.py" --config "configs\v2.2-reactive-development.json" --check-config
if errorlevel 1 (
  pause
  exit /b 1
)
echo REACTIVE LOCAL EXPERIMENT. Not competition accepted. Uses coarse shot and short-pass bounds.
"%MATCH_PYTHON%" -X utf8 "ZBin\PythonScripts\match_launcher.py" --config "configs\v2.2-reactive-development.json"
