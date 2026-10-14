@echo off
setlocal
cd /d "%~dp0"
rem Sparse source checkout can use a separate matching official runtime.
if not defined SRC_RUNTIME_ZBIN set "SRC_RUNTIME_ZBIN=%~dp0ZBin"
if not defined MATCH_PYTHON set "MATCH_PYTHON=%SRC_RUNTIME_ZBIN%\PythonScripts\.venv\Scripts\python.exe"
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
if "%~1"=="--check-config" exit /b 0
if "%~1"=="--check" (
  "%MATCH_PYTHON%" -X utf8 "ZBin\PythonScripts\run_match.py" --config "configs\v2.3-development.json" --check
  exit /b
)
rem Required vision bridge: grSim 10021 -> Client 10020 + raw observer 10022.
"%MATCH_PYTHON%" -X utf8 "ZBin\PythonScripts\vision_relay.py"
if errorlevel 1 exit /b 1
echo V2.3 LOCAL DEVELOPMENT: full terminal plans. Dynamic fast finish is NOT yet accepted.
"%MATCH_PYTHON%" -X utf8 "ZBin\PythonScripts\match_launcher.py" --config "configs\v2.3-development.json"
exit /b
