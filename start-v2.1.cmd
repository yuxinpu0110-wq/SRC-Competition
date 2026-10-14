@echo off
cd /d "%~dp0"
set "BASELINE=%~dp0..\Competition-v2.1-before-v2.2-20261014"
if not exist "%BASELINE%\start-v2.1.cmd" (
  echo Frozen V2.1 source missing. Restore the before-v2.2-integration-20261014 worktree and matching runtime first.
  pause
  exit /b 1
)
call "%BASELINE%\start-v2.1.cmd"
