@echo off
setlocal
cd /d "%~dp0"
REM Chelsy AI 0.9.3 - local release APK build.
REM Requires: Flutter 3.50+, Android SDK, JDK 17 and android\key.properties (see key.properties.example).
where flutter >nul 2>nul
if errorlevel 1 (
  echo Flutter SDK missing. Install Flutter and Android SDK, then run: flutter doctor
  pause
  exit /b 1
)
if not exist android\key.properties (
  echo android\key.properties not found. Create a release keystore:
  echo   keytool -genkeypair -v -keystore %%USERPROFILE%%\chelsy-release.jks -alias chelsy -keyalg RSA -keysize 4096 -validity 10000
  echo and copy android\key.properties.example to android\key.properties.
  pause
  exit /b 1
)
call flutter pub get || goto :fail
call flutter analyze || goto :fail
call flutter test || goto :fail
for /f "usebackq delims=" %%U in (`powershell -NoProfile -Command "(Get-Content ..\ChelsyAI_0.9_Windows\site_supabase_public.json | ConvertFrom-Json).url"`) do set SB_URL=%%U
for /f "usebackq delims=" %%K in (`powershell -NoProfile -Command "(Get-Content ..\ChelsyAI_0.9_Windows\site_supabase_public.json | ConvertFrom-Json).publishable_key"`) do set SB_KEY=%%K
call flutter build apk --release --obfuscate --split-debug-info=build\symbols --dart-define=SUPABASE_URL=%SB_URL% --dart-define=SUPABASE_KEY=%SB_KEY% || goto :fail
if not exist dist mkdir dist
copy /y build\app\outputs\flutter-apk\app-release.apk dist\ChelsyAI_Android_0.9.3.apk >nul
echo.
echo SUCCESS: %~dp0dist\ChelsyAI_Android_0.9.3.apk
pause
exit /b 0
:fail
echo APK BUILD FAILED. Check flutter doctor, SDK licenses and the Gradle output above.
pause
exit /b 1
