@echo off
chcp 65001 >nul
setlocal EnableExtensions

title Audion Browsers Portable - Verify

set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
for %%A in ("%SCRIPT_DIR%\..") do set "ROOT=%%~fA"

rem What a release is checked for. The environment: Python and the packages of
rem the engine. The door: it reads the manifest. The window: App\ is not built
rem by a build and travels as it is, so it is checked to be in place and to
rem draw every operation and field of the manifest of this version.
rem
rem The window is not asked to carry the number of the project. The engine and
rem the manifest move on from version to version and the window is rebuilt only
rem when Source\ changes; the number it shows it reads from config\version.json.
rem What tells a window that fell behind is the manifest it can no longer draw.
rem Nothing here needs the .NET SDK.

if not exist "%ROOT%\runtime\python.exe" goto ERR_PYTHON
if not exist "%ROOT%\system_core\doctor.py" goto ERR_DOCTOR

call "%ROOT%\install\Check-CmdEncoding.cmd"
if errorlevel 1 goto ERR_CMD_ENCODING

"%ROOT%\runtime\python.exe" "%ROOT%\system_core\doctor.py" --project-root "%ROOT%"
set "RC=%errorlevel%"
if not "%RC%"=="0" goto DONE_VERIFY

rem The console door must read the manifest: the window draws by what it prints.
echo.
echo [DOOR] Reading the manifest through the console door...
"%ROOT%\runtime\python.exe" -u "%ROOT%\system_core\cli.py" describe >nul
set "RC=%errorlevel%"
if not "%RC%"=="0" goto ERR_DOOR
echo [OK] The door reads the manifest.

echo.
echo [WINDOW] Checking the built window in App\ ...
set "RC=1"
if not exist "%ROOT%\Start.exe" goto ERR_START
if not exist "%ROOT%\App\AudionBrowsersPortable.exe" goto ERR_APP
if not exist "%ROOT%\App\AudionPs.exe" goto ERR_APP

"%ROOT%\App\AudionPs.exe" -NoLogo -NoProfile -NonInteractive -Command "$want = (Get-Content -LiteralPath '%ROOT%\config\version.json' -Raw | ConvertFrom-Json).version; $built = ((Get-Item -LiteralPath '%ROOT%\App\AudionBrowsersPortable.exe').VersionInfo.ProductVersion -split '\+')[0]; Write-Host ('[OK] App\ is in place: the window built as ' + $built + ', the project is ' + $want + '.')"
if errorlevel 1 goto ERR_PS

set "CHECK_FILE=%TEMP%\browsers_self_check_%RANDOM%%RANDOM%.json"
start "" /wait "%ROOT%\App\AudionBrowsersPortable.exe" "--self-check=%CHECK_FILE%"
set "CHECK_RC=%errorlevel%"
if not "%CHECK_RC%"=="0" goto ERR_SELF_CHECK
if not exist "%CHECK_FILE%" goto ERR_SELF_CHECK
"%ROOT%\App\AudionPs.exe" -NoLogo -NoProfile -NonInteractive -Command "$c = Get-Content -LiteralPath '%CHECK_FILE%' -Raw | ConvertFrom-Json; Write-Host ('[OK] The window draws the manifest: sections ' + $c.sections + ', operations with a button ' + $c.operations_with_button + ', fields ' + $c.fields_built + ' of ' + $c.fields + '.')"
del /f /q "%CHECK_FILE%" >nul 2>nul
set "RC=0"

:DONE_VERIFY
if not defined AUDION_NO_PAUSE pause
exit /b %RC%

:ERR_PYTHON
echo [ERROR] runtime\python.exe was not found.
if not defined AUDION_NO_PAUSE pause
exit /b 1

:ERR_DOCTOR
echo [ERROR] doctor.py was not found.
if not defined AUDION_NO_PAUSE pause
exit /b 1

:ERR_CMD_ENCODING
echo [ERROR] CMD encoding check failed.
if not defined AUDION_NO_PAUSE pause
exit /b 1

:ERR_DOOR
echo [ERROR] The console door does not read the manifest.
goto DONE_VERIFY

:ERR_START
echo [ERROR] Start.exe is missing: install\Build-StartLauncher.cmd makes it.
goto DONE_VERIFY

:ERR_APP
echo [ERROR] The built window is missing or incomplete: App\AudionBrowsersPortable.exe and App\AudionPs.exe.
echo [INFO] install\Build-App.cmd builds it from Source\ and needs the .NET 10 SDK.
goto DONE_VERIFY

:ERR_PS
echo [ERROR] The PowerShell of the program, App\AudionPs.exe, does not start.
goto DONE_VERIFY

:ERR_SELF_CHECK
echo [ERROR] The window does not draw the manifest of this version, exit code %CHECK_RC%.
echo [INFO] What it built and what it missed: %CHECK_FILE%
echo [INFO] A manifest with something new for the window needs the window rebuilt: install\Build-App.cmd.
goto DONE_VERIFY
