@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0BUILD_CLIENT_ONLY.ps1"
set err=%errorlevel%
if not "%err%"=="0" echo Build failed with code %err%.
pause
exit /b %err%
