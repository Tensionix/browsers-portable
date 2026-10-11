@echo off
chcp 65001 >nul
setlocal EnableExtensions

set "BASE_DIR=%~dp0.."
for %%I in ("%BASE_DIR%") do set "BASE_DIR=%%~fI"

rem The PowerShell the program carries comes first; the one of Windows - where App\ is not built yet.
set "PS_EXE=%BASE_DIR%\App\AudionPs.exe"
if not exist "%PS_EXE%" set "PS_EXE=powershell.exe"

"%PS_EXE%" -NoProfile -ExecutionPolicy Bypass -File "%BASE_DIR%\install\Build-StartLauncher.ps1" %*
exit /b %ERRORLEVEL%
