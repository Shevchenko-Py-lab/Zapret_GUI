@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if not exist "main.py" (
  echo [ERROR] main.py not found. Place this file in your Zapret_GUI project root.
  pause
  exit /b 1
)
if not exist "Zapret_backend\f01_power.py" (
  echo [ERROR] Zapret_backend is missing.
  pause
  exit /b 1
)
if not exist "zapret_frontend\main_window.ui" (
  echo [ERROR] zapret_frontend\main_window.ui is missing.
  pause
  exit /b 1
)

if exist ".venv\Scripts\python.exe" (
  set "PYTHON=.venv\Scripts\python.exe"
) else (
  set "PYTHON=python"
)

"%PYTHON%" -m pip install --upgrade pyinstaller
if errorlevel 1 goto :failed
"%PYTHON%" -m pip install -r requirements_distribution.txt
if errorlevel 1 goto :failed

"%PYTHON%" -m PyInstaller --noconfirm --clean --onedir --windowed --contents-directory . --name ZapretControl --add-data "zapret_frontend;zapret_frontend" --collect-submodules PySide6.QtUiTools main.py
if errorlevel 1 goto :failed

if not exist "dist\ZapretControl\ZapretControl.exe" goto :failed
if not exist "Zapret_dir" (
  echo [WARNING] Zapret_dir missing, exe will need this folder alongside it.
) else (
  echo [INFO] Copying existing Zapret_dir next to the exe...
  robocopy "Zapret_dir" "dist\ZapretControl\Zapret_dir" /E /R:1 /W:1 /XD __pycache__ >nul
  if errorlevel 8 goto :failed
)

echo.
echo [OK] Built: dist\ZapretControl\ZapretControl.exe
echo Keep the entire dist\ZapretControl folder when sharing.
pause
exit /b 0

:failed
echo [ERROR] Build failed. See output above.
pause
exit /b 1
