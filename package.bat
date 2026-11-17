@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

echo.
echo   Local Voice - build a Windows installer
echo   ======================================
echo.

if not exist ".venv\Scripts\python.exe" (
  echo   [x] No Python environment found. Run start.bat once to create it.
  goto :fail
)

where node >nul 2>&1
if errorlevel 1 (
  echo   [x] Node.js is required to build the desktop app.
  goto :fail
)

REM ---- versions ------------------------------------------------------------
for /f "usebackq delims=" %%v in (`".venv\Scripts\python.exe" scripts\read_version.py`) do set "CURRENT=%%v"
if not defined CURRENT set "CURRENT=1.0.0"

for /f "tokens=1,2,3 delims=." %%a in ("!CURRENT!") do (
  set /a NEXT_PATCH=%%c+1
  set "RECOMMENDED=%%a.%%b.!NEXT_PATCH!"
)

echo   Current version   : !CURRENT!
echo   Recommended       : !RECOMMENDED!
echo.
set "VERSION="
set /p "VERSION=  Release version [X.X.X], Enter for !RECOMMENDED!: "
if not defined VERSION set "VERSION=!RECOMMENDED!"

powershell -NoProfile -Command "if ('!VERSION!' -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$') { exit 1 }"
if errorlevel 1 (
  echo.
  echo   [x] "!VERSION!" is not a X.X.X version, for example 1.4.0.
  goto :fail
)
echo.
echo   Building Local Voice !VERSION!
echo.

REM ---- tooling -------------------------------------------------------------
call :step "1/5  Writing version !VERSION! into the manifests"
".venv\Scripts\python.exe" scripts\set_version.py !VERSION!
if errorlevel 1 goto :fail

call :step "2/5  Checking the build tooling"
set "UV=uv"
where uv >nul 2>&1
if errorlevel 1 (
  for /d %%d in ("%USERPROFILE%\AppData\Roaming\Python\Python3*") do (
    if exist "%%d\Scripts\uv.exe" set "UV=%%d\Scripts\uv.exe"
  )
)
".venv\Scripts\python.exe" -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
  echo        installing PyInstaller into .venv
  "!UV!" pip install --python ".venv\Scripts\python.exe" "pyinstaller>=6.11"
  if errorlevel 1 (
    echo   [x] Could not install PyInstaller.
    goto :fail
  )
)
if not exist "node_modules\@tauri-apps\cli" (
  echo        installing the Tauri CLI
  call npm install --no-audit --no-fund
  if errorlevel 1 (
    echo   [x] npm install failed.
    goto :fail
  )
)

call :step "3/5  Freezing the Python engine and staging the model"
".venv\Scripts\python.exe" scripts\build_sidecar.py
if errorlevel 1 goto :fail

call :step "4/5  Building the installer (several minutes on the first run)"
set "CARGO_TARGET_DIR=%CD%\.cargo-target"
set "RUSTFLAGS="
call npx --no-install tauri build --bundles nsis
if errorlevel 1 goto :fail

call :step "5/5  Collecting the installer"
if not exist "releases" mkdir "releases"
set "BUNDLE=%CARGO_TARGET_DIR%\release\bundle\nsis"
if not exist "!BUNDLE!" set "BUNDLE=%CD%\src-tauri\target\release\bundle\nsis"
set "INSTALLER="
for %%f in ("!BUNDLE!\*.exe") do set "INSTALLER=%%f"
if not defined INSTALLER (
  echo   [x] The build finished but no installer was found in !BUNDLE!
  goto :fail
)
copy /y "!INSTALLER!" "releases\" >nul
if errorlevel 1 goto :fail

echo.
echo   Done.
for %%f in ("releases\*.exe") do (
  echo   Installer: releases\%%~nxf
  echo   Size     : %%~zf bytes
)
echo.
echo   Double-click the installer to put Local Voice on this PC.
echo.
pause
exit /b 0

:step
echo   %~1
exit /b 0

:fail
echo.
echo   Build stopped.
echo.
pause
exit /b 1
