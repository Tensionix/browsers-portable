@echo off
setlocal EnableExtensions EnableDelayedExpansion

title Audion Browsers Portable - builder

set "BASE_DIR=%~dp0"
if "%BASE_DIR:~-1%"=="\" set "BASE_DIR=%BASE_DIR:~0,-1%"
cd /d "%BASE_DIR%"

rem What a build is for: fresh components. cleanup_project.cmd empties runtime\,
rem wheelhouse\ and tools\; step 01 or 02 brings back the current Python 3.12 and
rem the packages of install\requirements_full.in, step 06 - the portable 7-Zip the
rem engine unpacks installers with. Nothing else is downloaded.
rem
rem App\ - the window and the PowerShell the program carries - is not a product
rem of a build: it stays through a cleanup and travels in a release as it is.
rem Step 05 rebuilds it and is for the day Source\ changes (.NET 10 SDK); a
rem release build does not need the SDK. What a release build runs: 02 brings
rem Python back, 06 - 7-Zip, 71 checks the environment, the door and the built
rem window, 70 clears the cache of the install.
rem
rem Step bodies are read by the orchestrator, not only run from the menu: it
rem cuts everything between a label and `goto MAIN` into its own batch file
rem where only BASE_DIR is set. So a body must stand alone.

set /a EMPTY_TRIES=0

:MAIN
set /a EMPTY_TRIES+=1
rem Without a console the prompt reads nothing and the menu would redraw for
rem ever. Three empty answers in a row mean there is nobody to ask.
if !EMPTY_TRIES! GTR 3 (
  echo [ERROR] No input available - run the builder from a console.
  exit /b 2
)
cls
echo ======================================================================
echo   Audion Browsers Portable - builder
echo ======================================================================
echo Root: %BASE_DIR%
echo.

if defined AUDION_AUTO_EXIT (
  echo [SMOKE] AUDION_AUTO_EXIT is set. Exiting before menu interaction.
  exit /b 0
)

echo [01] PYTHON ENV CMD                        ^| build_cmd         ^| build portable runtime via CMD
echo [02] PYTHON ENV PS                         ^| build_ps          ^| build portable runtime via PowerShell
echo [04] START LAUNCHER                        ^| build_launcher    ^| rebuild Start.exe
echo [05] WINDOW                                ^| build_app         ^| rebuild App\ - only after a change in Source\
echo [06] 7-ZIP                                 ^| install_7zip      ^| portable 7za.exe, required to unpack browsers
echo [09] PORTABLE OFFLINE                      ^| install_offline   ^| install from local runtime/wheelhouse
echo.
echo [70] CLEAN INSTALL CACHE                   ^| clean_cache       ^| install cache, bytecode, bin and obj of Source
echo [71] VERIFY / DOCTOR                       ^| verify            ^| check the environment, the door and the built window
echo [72] CMD ENCODING CHECK                    ^| cmd_encoding      ^| UTF-8 no-BOM CRLF
echo [73] UPDATE REQUIREMENTS LOCK              ^| update_req_lock   ^| compile requirements_lock.txt
echo.
echo [95] OPEN install                          ^| open_install      ^| explorer install
echo [96] OPEN runtime                          ^| open_runtime      ^| explorer runtime
echo [97] OPEN wheelhouse                       ^| open_wheels       ^| explorer wheelhouse
echo [98] OPEN licenses                         ^| open_licenses     ^| explorer licenses
echo.
echo [00] EXIT                                  ^| exit              ^| close builder
echo.
set "RAW="
set /p RAW="Select step number or id: "
call :TRIM RAW
if not defined RAW goto MAIN
set /a EMPTY_TRIES=0
if /I "%RAW%"=="01" goto BUILD_CMD
if /I "%RAW%"=="1" goto BUILD_CMD
if /I "%RAW%"=="build_cmd" goto BUILD_CMD
if /I "%RAW%"=="02" goto BUILD_PS
if /I "%RAW%"=="2" goto BUILD_PS
if /I "%RAW%"=="build_ps" goto BUILD_PS
if /I "%RAW%"=="04" goto BUILD_LAUNCHER
if /I "%RAW%"=="4" goto BUILD_LAUNCHER
if /I "%RAW%"=="build_launcher" goto BUILD_LAUNCHER
if /I "%RAW%"=="05" goto BUILD_APP
if /I "%RAW%"=="5" goto BUILD_APP
if /I "%RAW%"=="build_app" goto BUILD_APP
if /I "%RAW%"=="06" goto INSTALL_7ZIP
if /I "%RAW%"=="6" goto INSTALL_7ZIP
if /I "%RAW%"=="install_7zip" goto INSTALL_7ZIP
if /I "%RAW%"=="09" goto INSTALL_OFFLINE
if /I "%RAW%"=="9" goto INSTALL_OFFLINE
if /I "%RAW%"=="install_offline" goto INSTALL_OFFLINE
if /I "%RAW%"=="70" goto CLEAN_CACHE
if /I "%RAW%"=="clean_cache" goto CLEAN_CACHE
if /I "%RAW%"=="71" goto VERIFY
if /I "%RAW%"=="verify" goto VERIFY
if /I "%RAW%"=="72" goto CMD_ENCODING
if /I "%RAW%"=="cmd_encoding" goto CMD_ENCODING
if /I "%RAW%"=="73" goto UPDATE_REQ_LOCK
if /I "%RAW%"=="update_req_lock" goto UPDATE_REQ_LOCK
if /I "%RAW%"=="95" goto OPEN_INSTALL
if /I "%RAW%"=="open_install" goto OPEN_INSTALL
if /I "%RAW%"=="96" goto OPEN_RUNTIME
if /I "%RAW%"=="open_runtime" goto OPEN_RUNTIME
if /I "%RAW%"=="97" goto OPEN_WHEELS
if /I "%RAW%"=="open_wheels" goto OPEN_WHEELS
if /I "%RAW%"=="98" goto OPEN_LICENSES
if /I "%RAW%"=="open_licenses" goto OPEN_LICENSES
if /I "%RAW%"=="00" exit /b 0
if /I "%RAW%"=="0" exit /b 0
if /I "%RAW%"=="exit" exit /b 0
goto MAIN

:BUILD_CMD
call "%BASE_DIR%\install\Build_Portable_Env_Build.cmd"
goto MAIN

:BUILD_PS
call "%BASE_DIR%\install\Build_Portable_Env.cmd"
goto MAIN

:BUILD_LAUNCHER
call "%BASE_DIR%\install\Build-StartLauncher.cmd"
goto MAIN

:BUILD_APP
call "%BASE_DIR%\install\Build-App.cmd" /NOPAUSE
goto MAIN

:INSTALL_7ZIP
call "%BASE_DIR%\install\Install-Portable-7Zip.cmd"
goto MAIN

:INSTALL_OFFLINE
call "%BASE_DIR%\install\install_portable_offline.cmd"
goto MAIN

:CLEAN_CACHE
call "%BASE_DIR%\install\Clean-Install-Cache.cmd"
goto MAIN

:VERIFY
call "%BASE_DIR%\install\verify_portable_env.cmd"
goto MAIN

:CMD_ENCODING
call "%BASE_DIR%\install\Check-CmdEncoding.cmd" -Fix
set "RC=%errorlevel%"
echo.
echo [INFO] CMD encoding repair finished with exit code %RC%.
echo [INFO] Restart builder_main.cmd before continuing: a file that was fixed is read anew only then.
if not defined AUDION_NO_PAUSE pause
exit /b %RC%
goto MAIN

:UPDATE_REQ_LOCK
call "%BASE_DIR%\install\Update-Requirements-Lock.cmd"
goto MAIN

:OPEN_INSTALL
start "" explorer "%BASE_DIR%\install"
goto MAIN

:OPEN_RUNTIME
start "" explorer "%BASE_DIR%\runtime"
goto MAIN

:OPEN_WHEELS
start "" explorer "%BASE_DIR%\wheelhouse"
goto MAIN

:OPEN_LICENSES
if not exist "%BASE_DIR%\licenses" mkdir "%BASE_DIR%\licenses" >nul 2>nul
start "" explorer "%BASE_DIR%\licenses"
goto MAIN

:TRIM
for /f "tokens=* delims= " %%z in ("!%~1!") do set "%~1=%%z"
:TRIM_R
if "!%~1:~-1!"==" " set "%~1=!%~1:~0,-1!" & goto TRIM_R
goto :eof
