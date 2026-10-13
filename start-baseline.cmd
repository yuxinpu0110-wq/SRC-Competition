@echo off
cd /d "%~dp0"
call start-v2-baseline.cmd
if errorlevel 1 pause
