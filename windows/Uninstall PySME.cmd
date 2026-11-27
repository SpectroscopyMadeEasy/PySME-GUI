@echo off
rem Double-click to remove PySME. Model data in your .sme folder is kept.
if not exist "%~dp0install.ps1" (
  echo Extract the whole zip file first, then run this from the extracted folder.
  pause
  exit /b 1
)
rem Started from PowerShell 7, Windows PowerShell would inherit module folders
rem that stop it loading its own commands. Without this it uses its own.
setlocal
set "PSModulePath="
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" -Uninstall %*
set RESULT=%ERRORLEVEL%
if not defined PYSME_GUI_NOPAUSE pause
exit /b %RESULT%
