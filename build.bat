@echo off
chcp 65001 >nul
REM ============================================================
REM  アプリランチャー  exe ビルドスクリプト
REM  実行すると dist\AppLauncher.exe が生成されます。
REM ============================================================
cd /d "%~dp0"

echo [1/2] PyInstaller / certifi / pystray / pillow を確認しています...
python -m pip install --quiet --upgrade pyinstaller certifi pystray pillow

echo [2/2] exe をビルドしています...
python -m PyInstaller --noconfirm --onefile --windowed ^
  --name "AppLauncher" ^
  --hidden-import tkinter ^
  --hidden-import certifi ^
  --collect-data certifi ^
  --hidden-import pystray._win32 ^
  --collect-submodules pystray ^
  main.py

echo.
echo 完了しました。
echo 配布用 exe: %~dp0dist\AppLauncher.exe
pause
