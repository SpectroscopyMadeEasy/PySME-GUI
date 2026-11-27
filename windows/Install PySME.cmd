@echo off
rem Double-click to install PySME for your Windows account.
if not exist "%~dp0install.ps1" (
  echo Extract the whole zip file first, then run this from the extracted folder.
  pause
  exit /b 1
)
rem Started from PowerShell 7, Windows PowerShell would inherit module folders
rem that stop it loading its own commands. Without this it uses its own.
setlocal
set "PSModulePath="
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
set RESULT=%ERRORLEVEL%
if not defined PYSME_GUI_NOPAUSE pause
exit /b %RESULT%
