@echo off
setlocal
title Making Laptop AI
cd /d "%~dp0"
echo.
echo  Laptop AI app maker. This takes a few minutes. Just wait; nothing to type.
echo.

rem ---- 1. find Python
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY ( where python >nul 2>nul && set "PY=python" )
if not defined PY (
  echo  Python was not found. Install it from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^) and run this again.
  goto :fail
)

rem ---- 2. packages + the app builder
echo  [1/3] Installing what is needed...
%PY% -m pip install -r requirements.txt pyinstaller || goto :fail

rem ---- 3. build LaptopAI.exe
echo.
echo  [2/3] Building LaptopAI.exe...
%PY% -m PyInstaller --noconfirm --clean --onefile --windowed --name LaptopAI ^
  --icon laptop_ai\assets\app.ico ^
  --add-data "laptop_ai\assets;laptop_ai\assets" ^
  --collect-all customtkinter ^
  --collect-all pyttsx3 ^
  --collect-data speech_recognition ^
  --collect-all vosk ^
  --collect-all sounddevice ^
  --collect-submodules pystray ^
  --hidden-import comtypes ^
  --hidden-import keyring.backends.Windows ^
  --hidden-import PIL._tkinter_finder ^
  run.py || goto :fail
if not exist "dist\LaptopAI.exe" goto :fail

rem ---- 4. installer (if Inno Setup is installed) or plain shortcuts
echo.
echo  [3/3] Making the installer...
set "ISCC="
for %%P in ("%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" "%ProgramFiles%\Inno Setup 6\ISCC.exe") do (
  if exist %%P set "ISCC=%%~P"
)
for /f %%V in ('%PY% -c "from laptop_ai import __version__; print(__version__)"') do set "VER=%%V"
if defined ISCC (
  "%ISCC%" /DAppVersion=%VER% installer.iss || goto :fail
  echo.
  echo  DONE. Your installer is: %~dp0installer_output\LaptopAI_Setup.exe
  echo  Double-click it to install Laptop AI like any other program.
  start "" "%~dp0installer_output"
) else (
  echo  Inno Setup is not installed, so I will install the app directly instead.
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_shortcuts.ps1" -Exe "%~dp0dist\LaptopAI.exe" || goto :fail
  echo.
  echo  DONE. Laptop AI is installed. Look for "Laptop AI" in the Start menu or on your desktop.
  echo  ^(Optional: install the free Inno Setup 6 from https://jrsoftware.org/isdl.php and run this again to get a proper Setup file.^)
)
echo.
pause
exit /b 0

:fail
echo.
echo  Something went wrong. Read the messages above, or send them to me.
pause
exit /b 1
