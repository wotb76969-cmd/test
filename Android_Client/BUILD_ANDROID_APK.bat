@echo off
setlocal
cd /d "%~dp0"
where flutter >nul 2>nul
if errorlevel 1 (
  echo Flutter SDK missing. Install Flutter and Android SDK then run flutter doctor.
  pause
  exit /b 1
)
if not exist android\app\src\main\AndroidManifest.xml (
  call flutter create --platforms=android --project-name chelsy_ai_client --org ai.chelsy .
  if errorlevel 1 goto :fail
)
REM Android release builds need INTERNET permission too.
powershell -NoProfile -ExecutionPolicy Bypass -File "SET_ANDROID_INTERNET.ps1"
if errorlevel 1 goto :fail
call flutter pub get
if errorlevel 1 goto :fail
call flutter build apk --release --dart-define=SUPABASE_URL=https://mbhqpgmhkrypxevaboih.supabase.co --dart-define=SUPABASE_KEY=sb_publishable_DGCT89JC46vkWcgsV0Wakg_3spLx22r
if errorlevel 1 goto :fail
echo.
echo SUCCESS: build\app\outputs\flutter-apk\app-release.apk
pause
exit /b 0
:fail
echo APK BUILD FAILED. Check flutter doctor, SDK license and Gradle output.
pause
exit /b 1
