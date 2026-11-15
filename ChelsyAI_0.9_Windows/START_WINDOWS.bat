@echo off
cd /d "%~dp0"
py -3.11 -m pip install -r requirements.txt
py -3.11 launcher.py
pause
