@echo off
setlocal EnableExtensions EnableDelayedExpansion

title Audion Browsers Portable - Build App

rem Builds App\ from Source\: the window (AudionBrowsersPortable.exe) and the
rem PowerShell 7 the program carries (AudionPs.exe - the scripts of the builder
rem and of the cleanup run with it).
rem
rem Self-contained for win-x64: App\ carries its own .NET runtime, so the program
rem runs on a machine with nothing installed, and no PowerShell is downloaded or
rem taken from the machine. The new copy is put together in ._runtime\app_stage
rem and checked there; App\ is replaced only by a copy that is whole, so a failed
rem build leaves the working one in place. The version comes from
rem config\version.json - the project files read it - and is written into the
rem executables.
rem
rem The checks of the window run first (tests\test_window_avalonia.py): a window
rem whose text has no English pair, or whose door no longer answers with the
rem keys the window reads, is not built. The built copy then checks itself
rem (--self-check): every operation and every field of the manifest must have
rem its button and its control.
rem
rem Needs the .NET 10 SDK and the Python of the project. The step is run by hand
rem when Source\ changes: a release does not rebuild the window.
rem
rem Usage: install\Build-App.cmd [/NOPAUSE]

for %%I in ("%~dp0..") do set "BASE_DIR=%%~fI"
set "NO_PAUSE=0"
if /I "%~1"=="/NOPAUSE" set "NO_PAUSE=1"

set "STEP_ERROR=0"
set "STAGE=%BASE_DIR%\._runtime\app_stage"
set "OLD=%BASE_DIR%\._runtime\app_old"
set "CHECK=%BASE_DIR%\._runtime\app_self_check.json"
set "PYTHON=%BASE_DIR%\runtime\python.exe"
set "WINDOW=AudionBrowsersPortable.exe"

if not exist "!PYTHON!" (
  echo [ERROR] Python of the project was not found: !PYTHON!
  echo         Build the environment first: builder_main.cmd
  set "STEP_ERROR=1"
)

set "DOTNET=%ProgramFiles%\dotnet\dotnet.exe"
if not exist "!DOTNET!" set "DOTNET="
if not defined DOTNET for %%P in (dotnet.exe) do if not defined DOTNET set "DOTNET=%%~$PATH:P"
if not defined DOTNET (
  echo [ERROR] .NET SDK was not found. Install the .NET 10 SDK: https://dotnet.microsoft.com/download
  set "STEP_ERROR=1"
)

if "!STEP_ERROR!"=="0" (
  echo [STEP] The checks of the window
  pushd "%BASE_DIR%"
  "!PYTHON!" -B -m pytest -q -p no:cacheprovider tests\test_window_avalonia.py
  if errorlevel 1 (
    echo [ERROR] The checks of the window failed - App\ was not rebuilt.
    set "STEP_ERROR=1"
  )
  popd
)

if "!STEP_ERROR!"=="0" (
  echo [STEP] The PowerShell of the program - into App\
  if exist "!STAGE!" rd /s /q "!STAGE!"
  "!DOTNET!" publish "%BASE_DIR%\Source\Ps\AudionPs.csproj" -c Release -r win-x64 --self-contained true -p:PublishSingleFile=false -p:DebugType=None -o "!STAGE!" --nologo
  if errorlevel 1 (
    echo [ERROR] dotnet publish of Source\Ps failed.
    set "STEP_ERROR=1"
  )
)

if "!STEP_ERROR!"=="0" (
  echo [STEP] The window - into App\
  rem ReadyToRun: the window and Avalonia are compiled here, once, instead of at every start.
  "!DOTNET!" publish "%BASE_DIR%\Source\AudionBrowsersPortable.csproj" -c Release -r win-x64 --self-contained true -p:PublishSingleFile=false -p:PublishReadyToRun=true -p:DebugType=None -o "!STAGE!" --nologo
  if errorlevel 1 (
    echo [ERROR] dotnet publish of Source failed.
    set "STEP_ERROR=1"
  )
)

if "!STEP_ERROR!"=="0" (
  for %%F in ("!WINDOW!" "AudionPs.exe" "config\i18n.json" "Modules\Microsoft.PowerShell.Management" "ref") do (
    if not exist "!STAGE!\%%~F" (
      echo [ERROR] %%~F is missing in the build.
      set "STEP_ERROR=1"
    )
  )
)

if "!STEP_ERROR!"=="0" (
  rem Native packages bring their debug symbols along: megabytes nobody running the program needs.
  del /s /q "!STAGE!\*.pdb" >nul 2>nul
  "!STAGE!\AudionPs.exe" -NoLogo -NoProfile -NonInteractive -Command "exit 0" >nul 2>nul
  if errorlevel 1 (
    echo [ERROR] The built AudionPs.exe does not start.
    set "STEP_ERROR=1"
  )
)

if "!STEP_ERROR!"=="0" (
  rem A script must hand its exit code on through the built PowerShell: the builder judges a step by it.
  "!STAGE!\AudionPs.exe" -NoLogo -NoProfile -NonInteractive -Command "exit 7" >nul 2>nul
  if not "!ERRORLEVEL!"=="7" (
    echo [ERROR] The built AudionPs.exe does not hand on the exit code of a script.
    set "STEP_ERROR=1"
  )
)

if "!STEP_ERROR!"=="0" (
  rem The number of both executables must be the number of config\version.json.
  "!STAGE!\AudionPs.exe" -NoLogo -NoProfile -NonInteractive -Command "$want = (Get-Content -LiteralPath '%BASE_DIR%\config\version.json' -Raw | ConvertFrom-Json).version; foreach ($name in '!WINDOW!', 'AudionPs.exe') { $built = (Get-Item -LiteralPath (Join-Path '!STAGE!' $name)).VersionInfo.ProductVersion; if (-not $built.StartsWith($want)) { Write-Host ('[ERROR] ' + $name + ' is ' + $built + ', not the ' + $want + ' of config\version.json.'); exit 1 } }; Write-Host ('[OK] !WINDOW! and AudionPs.exe ' + $want + ', PowerShell ' + $PSVersionTable.PSVersion)"
  if errorlevel 1 set "STEP_ERROR=1"
)

if "!STEP_ERROR!"=="0" (
  echo [STEP] The built window checks itself against the manifest
  if exist "!CHECK!" del /q "!CHECK!"
  rem "start /wait": a windowed program is not waited for otherwise, and its exit code is lost.
  start "" /wait "!STAGE!\!WINDOW!" --root "%BASE_DIR%" "--self-check=!CHECK!"
  if errorlevel 1 (
    echo [ERROR] The self-check of the window failed: ._runtime\app_self_check.json names what is missing.
    set "STEP_ERROR=1"
  ) else (
    echo [OK] Every operation and field of the manifest is in the window.
  )
)

if "!STEP_ERROR!"=="0" (
  if exist "!OLD!" rd /s /q "!OLD!"
  if exist "%BASE_DIR%\App" move "%BASE_DIR%\App" "!OLD!" >nul 2>nul
  if exist "%BASE_DIR%\App" (
    echo [ERROR] App\ is in use and was not replaced: close the window, then run the step again. The new copy waits in ._runtime\app_stage.
    set "STEP_ERROR=1"
  ) else (
    move "!STAGE!" "%BASE_DIR%\App" >nul 2>nul
    if exist "%BASE_DIR%\App\!WINDOW!" (
      if exist "!OLD!" rd /s /q "!OLD!" >nul 2>nul
      echo [OK] App\ is built: !WINDOW! and AudionPs.exe.
    ) else (
      rem The new copy did not move in - something still holds a file of it. A program without
      rem its App\ starts nothing, so the working copy goes back before anything is said.
      if exist "!OLD!" move "!OLD!" "%BASE_DIR%\App" >nul 2>nul
      if exist "%BASE_DIR%\App\!WINDOW!" (
        echo [ERROR] The new App\ could not be moved into place; the former one was put back. The new copy waits in ._runtime\app_stage - run the step again.
      ) else (
        echo [ERROR] The new App\ could not be moved into place and there is no App\ now. The new copy waits in ._runtime\app_stage, a former one - in ._runtime\app_old: move one of them to App\ by hand before the cleanup of the project removes ._runtime.
      )
      set "STEP_ERROR=1"
    )
  )
)

if "%NO_PAUSE%"=="0" pause
endlocal & exit /b %STEP_ERROR%
