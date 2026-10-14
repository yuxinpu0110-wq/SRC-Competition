@echo off
cd /d "%~dp0"
set "PYTHON=ZBin\PythonScripts\.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
  echo Matching .venv interpreter missing. Configure the supplied Python 3.12 environment first.
  pause
  exit /b 1
)
"%PYTHON%" -X utf8 "ZBin\PythonScripts\run_match.py" --config "configs\v2.2-development.json" --check-config
if errorlevel 1 (
  pause
  exit /b 1
)
echo V2.2 DEVELOPMENT ONLY. No physical acceptance; an uncalibrated model blocks all kicks.
if not exist "ZBin\PythonScripts\CppPackage\CppPackage.pyd" (
  echo Native SDK/runtime missing. Install the matching official runtime before simulation.
  pause
  exit /b 1
)
"%PYTHON%" -X utf8 "ZBin\PythonScripts\match_launcher.py" --config "configs\v2.2-development.json"
