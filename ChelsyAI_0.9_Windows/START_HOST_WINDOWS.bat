@echo off
cd /d "%~dp0"
set CHELSY_APPDATA=1
py -3.11 host_server.py
pause
