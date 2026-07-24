@echo off
setlocal
REM ============================================================
REM  App Launcher - Publish Patch
REM  Usage: publish_patch.bat ["commit/release notes"] [/dryrun]
REM    - Reads version from version.py (bump it BEFORE running)
REM    - Build exe -> selftest gate -> commit -> push -> gh release
REM    - /dryrun: build + selftest only (no commit/push/release)
REM  Messages are ASCII only (CP932-safe, runs unattended).
REM ============================================================
cd /d "%~dp0"

set REPO=kngmincorp-stack/app-launcher
set EXE=dist\AppLauncher.exe
set "NOTES=%~1"
set "DRYRUN="
if /i "%~1"=="/dryrun" ( set "DRYRUN=1" & set "NOTES=" )
if /i "%~2"=="/dryrun" set "DRYRUN=1"
if "%NOTES%"=="" set "NOTES=patch update"

REM ---- read version from version.py ----
set VER=
for /f "delims=" %%v in ('python -c "from version import __version__; print(__version__)"') do set VER=%%v
if "%VER%"=="" (
    echo [ERROR] could not read version from version.py
    exit /b 1
)
echo Publishing version: v%VER%

REM ---- refuse to overwrite an existing release ----
if not defined DRYRUN (
    gh release view "v%VER%" --repo %REPO% >nul 2>&1
    if not errorlevel 1 (
        echo [ERROR] release v%VER% already exists. Bump version.py first.
        exit /b 1
    )
)

REM ---- build ----
echo [1/4] building exe...
python -m PyInstaller --noconfirm --onefile --windowed --name "AppLauncher" --hidden-import tkinter --hidden-import certifi --collect-data certifi --hidden-import pystray._win32 --collect-submodules pystray main.py >nul
if errorlevel 1 (
    echo [ERROR] build failed
    exit /b 1
)
if not exist "%EXE%" (
    echo [ERROR] %EXE% not found after build
    exit /b 1
)

REM ---- selftest gate: frozen exe must detect a running process ----
echo [2/4] running selftest...
del "%TEMP%\al_proc_selftest.txt" >nul 2>&1
"%EXE%" --selftest-proc
findstr /c:"detected=True" "%TEMP%\al_proc_selftest.txt" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] selftest failed - see %TEMP%\al_proc_selftest.txt
    exit /b 1
)
findstr /c:"tray=True" "%TEMP%\al_proc_selftest.txt" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] selftest failed: pystray not bundled - see %TEMP%\al_proc_selftest.txt
    exit /b 1
)
echo   selftest OK

if defined DRYRUN (
    echo [DRYRUN] skipping commit/push/release. done.
    exit /b 0
)

REM ---- commit and push (skip commit if nothing changed) ----
echo [3/4] commit and push...
git add -A
git diff --cached --quiet
if errorlevel 1 (
    git commit -m "v%VER%: %NOTES%"
    if errorlevel 1 (
        echo [ERROR] git commit failed
        exit /b 1
    )
) else (
    echo   nothing to commit, publishing current HEAD
)
git push origin master
if errorlevel 1 (
    echo [ERROR] git push failed
    exit /b 1
)

REM ---- github release ----
echo [4/4] creating GitHub release v%VER%...
gh release create "v%VER%" "%EXE%" --repo %REPO% --title "v%VER%" --notes "%NOTES%"
if errorlevel 1 (
    echo [ERROR] gh release create failed
    exit /b 1
)

echo.
echo ==== Published v%VER% successfully ====
exit /b 0
