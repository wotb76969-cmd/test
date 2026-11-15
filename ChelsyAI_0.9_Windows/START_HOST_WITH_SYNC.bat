@echo off
cd /d "%~dp0"
set CHELSY_APPDATA=1
set CHELSY_HOST_PORT=8787
python host_server.py
if errorlevel 1 echo Host failed. Check Python and dependencies.
pause
