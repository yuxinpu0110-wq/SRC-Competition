@echo off
setlocal
if not defined SRC_RUNTIME_ZBIN set "SRC_RUNTIME_ZBIN=%~dp0"
if not exist "%SRC_RUNTIME_ZBIN%\Client.exe" (
  echo Matching Client runtime missing. Set SRC_RUNTIME_ZBIN to the official ZBin directory.
  exit /b 1
)
cd /d "%SRC_RUNTIME_ZBIN%"
start "" "%SRC_RUNTIME_ZBIN%\Client.exe"
