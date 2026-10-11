@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul

title Audion Browsers Portable - Cleanup

set "BASE_DIR=%~dp0"
if "%BASE_DIR:~-1%"=="\" set "BASE_DIR=%BASE_DIR:~0,-1%"
cd /d "%BASE_DIR%" || exit /b 1

set "BASE_PREFIX=%BASE_DIR%\"
set "BASE_PREFIX_WORK=%BASE_PREFIX%"
set /a BASE_PREFIX_LEN=0
:base_prefix_len_loop
if defined BASE_PREFIX_WORK (
  set "BASE_PREFIX_WORK=!BASE_PREFIX_WORK:~1!"
  set /a BASE_PREFIX_LEN+=1
  goto base_prefix_len_loop
)
set "BASE_PREFIX_WORK="

set "AUTO_YES=0"
set "DRY_RUN=0"
set "ERROR_COUNT=0"

:parse_args
if "%~1"=="" goto args_done
if /I "%~1"=="/?" goto usage
if /I "%~1"=="-h" goto usage
if /I "%~1"=="--help" goto usage
if /I "%~1"=="/Y" set "AUTO_YES=1" & shift & goto parse_args
if /I "%~1"=="/YES" set "AUTO_YES=1" & shift & goto parse_args
if /I "%~1"=="-Y" set "AUTO_YES=1" & shift & goto parse_args
if /I "%~1"=="-YES" set "AUTO_YES=1" & shift & goto parse_args
if /I "%~1"=="--yes" set "AUTO_YES=1" & shift & goto parse_args
if /I "%~1"=="/DRYRUN" set "DRY_RUN=1" & shift & goto parse_args
if /I "%~1"=="/DRY-RUN" set "DRY_RUN=1" & shift & goto parse_args
if /I "%~1"=="--dry-run" set "DRY_RUN=1" & shift & goto parse_args
echo [ERROR] Unknown argument: %~1
echo.
goto usage_error

:args_done
rem The door of the program marks its folder: the former window, whose file was
rem the marker until 2.0.0, is gone.
call :CheckMarker "%BASE_DIR%\system_core\cli.py" || goto abort
call :CheckMarker "%BASE_DIR%\config\tool_manifest.yaml" || goto abort

echo ======================================================================
echo   AUDION BROWSERS PORTABLE - CLEANUP
echo ======================================================================
echo Root:
echo   %BASE_DIR%
echo.
echo This script returns the project to a small source-only state.
echo It keeps source scripts, documentation, permanent configs and release policies.
echo It removes generated workspace content, build/runtime payloads and caches:
echo   - input, output, logs, report, workspace, release contents
echo     (output and input hold builds of browsers WITH THEIR PROFILES - move them away first)
echo   - runtime, wheelhouse, install\download contents
echo   - all folders named .runtime, _runtime, ._runtime, __pycache__
echo   - pytest/mypy/ruff caches, root build/dist folders
echo   - tools\ entirely (7-Zip is rebuilt by the builder; libraries are downloaded)
echo   - bin and obj under Source\: what building the window and its PowerShell leaves behind
echo   - config\window.json: the look of the window on this machine
echo   - Git history is preserved
echo   - root licenses\ is preserved unchanged
echo   - generated *.pyc, *.pyo, *.tmp, *.bak, *.log, Thumbs.db, desktop.ini
echo.
echo Protected: system_core source, config, docs, install scripts, licenses.
echo The built window, App\, stays: it travels in a release as it is.
if "%DRY_RUN%"=="1" echo Dry-run: ON. Nothing will be deleted.
echo.

if "%AUTO_YES%"=="1" goto clean
choice /C YNQ /N /M "Proceed with project cleanup? [Y/N/Q]: "
if errorlevel 3 (
  echo.
  echo [QUIT] Nothing was deleted.
  call :WAIT_KEY
  exit /b 0
)
if errorlevel 2 (
  echo.
  echo [CANCELLED] Nothing was deleted.
  call :WAIT_KEY
  exit /b 0
)
if errorlevel 1 goto clean
echo.
echo [CANCELLED] Nothing was deleted.
call :WAIT_KEY
exit /b 0

:clean
echo.
echo [CLEAN] Starting project cleanup...
echo.

rem A sweep by name or pattern walks the whole tree, and so does `attrib /s /d`.
rem A folder link inside the project - a junction, a symlink: a build in output
rem whose Data points elsewhere - would lead them to files outside the project.
rem With any link present the sweeps are skipped and no attributes are changed
rem down a tree: only the listed folders are cleared, and `rd /s` removes a link
rem as a link, without going through it.
set "SWEEP=1"
for /f "delims=" %%L in ('dir /aL /b /s "%BASE_DIR%" 2^>nul') do (
  if "!SWEEP!"=="1" echo [SKIP] Folder link found, sweeps by name and pattern are skipped: %%~fL
  set "SWEEP=0"
)

call :RemoveNamedDirs ".runtime"
call :RemoveNamedDirs "_runtime"
call :RemoveNamedDirs "._runtime"
call :RemoveNamedDirs "__pycache__"
call :RemoveNamedDirs ".pytest_cache"
call :RemoveNamedDirs ".mypy_cache"
call :RemoveNamedDirs ".ruff_cache"

call :ClearDir "%BASE_DIR%\input"
call :ClearDir "%BASE_DIR%\output"
call :ClearDir "%BASE_DIR%\logs"
call :ClearDir "%BASE_DIR%\report"
call :ClearDir "%BASE_DIR%\workspace"
call :ClearDir "%BASE_DIR%\release"
call :ClearDir "%BASE_DIR%\runtime"
call :ClearDir "%BASE_DIR%\wheelhouse"
call :ClearDir "%BASE_DIR%\install\download"
rem licenses\ is intentionally preserved by release cleanup.
call :RemoveDir "%BASE_DIR%\build"
call :RemoveDir "%BASE_DIR%\dist"
rem What building the window leaves behind, and the look of the window on this machine.
rem App\ is the built window itself: it ships as it is and is not touched here.
call :RemoveDir "%BASE_DIR%\Source\bin"
call :RemoveDir "%BASE_DIR%\Source\obj"
call :RemoveDir "%BASE_DIR%\Source\Ps\bin"
call :RemoveDir "%BASE_DIR%\Source\Ps\obj"
call :RemoveFile "%BASE_DIR%\config\window.json"
call :RemoveFile "%BASE_DIR%\config\window.json.bak"
call :RemoveDir "%BASE_DIR%\tools"

call :RemoveFilesByPattern "*.pyc"
call :RemoveFilesByPattern "*.pyo"
call :RemoveFilesByPattern "*.tmp"
call :RemoveFilesByPattern "*.bak"
call :RemoveFilesByPattern "*.log"
call :RemoveFilesByPattern "Thumbs.db"
call :RemoveFilesByPattern "desktop.ini"

if exist "%BASE_DIR%\install\init_folders.cmd" (
  if "%DRY_RUN%"=="1" (
    echo [DRY] call install\init_folders.cmd
  ) else (
    call "%BASE_DIR%\install\init_folders.cmd" >nul 2>nul
  )
)

echo.
if not "%ERROR_COUNT%"=="0" goto cleanup_failed
if "%DRY_RUN%"=="1" goto dry_run_done
echo [OK] Cleanup finished.
echo [OK] Template is back to source-only LEAN shape.
echo [OK] runtime, wheelhouse, install\download and generated work folders were recreated empty.
goto cleanup_done

:dry_run_done
echo [OK] Dry run finished. Nothing was deleted.

:cleanup_done
if not "%AUTO_YES%"=="1" call :WAIT_KEY
exit /b 0

:cleanup_failed
echo [ERROR] Cleanup finished with %ERROR_COUNT% error(s).
echo Close GUI, terminals, Python processes, and try again.
if not "%AUTO_YES%"=="1" call :WAIT_KEY
exit /b 1

:cancelled
echo.
echo [CANCELLED] Nothing was deleted.
call :WAIT_KEY
exit /b 0

:quit
echo.
echo [QUIT] Nothing was deleted.
call :WAIT_KEY
exit /b 0

:abort
echo.
echo [ABORTED] Cleanup refused to run outside the expected project root.
if not "%AUTO_YES%"=="1" call :WAIT_KEY
exit /b 1

:usage
echo Usage:
echo   cleanup_project.cmd [/Y] [/DRYRUN]
echo.
echo Options:
echo   /Y, /YES, --yes       Run without confirmation.
echo   /DRYRUN, --dry-run    Print actions without deleting anything.
exit /b 0

:usage_error
call :WAIT_KEY
exit /b 1

:CheckMarker
if exist "%~1" exit /b 0
echo [ERROR] Project marker not found:
echo   %~1
exit /b 1

:RemoveNamedDirs
set "DIR_NAME=%~1"
if not "%SWEEP%"=="1" exit /b 0
echo [DIRS] Removing folders named %DIR_NAME%
for /f "delims=" %%D in ('dir /ad /b /s "%BASE_DIR%" 2^>nul') do (
  if /I "%%~nxD"=="%DIR_NAME%" (
    if "%DRY_RUN%"=="1" (
      echo   [DRY] rmdir /s /q "%%~fD"
    ) else (
      echo   rmdir "%%~fD"
      if "%SWEEP%"=="1" attrib -r -s -h "%%~fD\*" /s /d >nul 2>nul
      rd /s /q "%%~fD" >nul 2>nul
      if exist "%%~fD\" ( echo   [ERROR] Could not remove directory: %%~fD & set /a ERROR_COUNT+=1 )
    )
  )
)
exit /b 0

:ClearDir
set "TARGET_DIR=%~1"
call :AssertInside "%TARGET_DIR%" || (
  set /a ERROR_COUNT+=1
  exit /b 1
)
call :IsLink "%TARGET_DIR%" && (
  echo [SKIP] Folder link, its target is not cleared: %TARGET_DIR%
  exit /b 0
)
if not exist "%TARGET_DIR%\" (
  if "%DRY_RUN%"=="1" (
    echo [DRY] mkdir "%TARGET_DIR%"
  ) else (
    echo [CREATE] %TARGET_DIR%
    md "%TARGET_DIR%" >nul 2>nul
    if not exist "%TARGET_DIR%\" call :MarkError "Could not create directory: %TARGET_DIR%"
  )
  exit /b 0
)
echo [CLEAR] %TARGET_DIR%
for /f "delims=" %%I in ('dir /a /b "%TARGET_DIR%" 2^>nul') do (
    if exist "%TARGET_DIR%\%%I\" (
      if "%DRY_RUN%"=="1" (
        echo   [DRY] rmdir /s /q "%TARGET_DIR%\%%I"
      ) else (
        echo   rmdir "%TARGET_DIR%\%%I"
        if "%SWEEP%"=="1" attrib -r -s -h "%TARGET_DIR%\%%I\*" /s /d >nul 2>nul
        rd /s /q "%TARGET_DIR%\%%I" >nul 2>nul
        if exist "%TARGET_DIR%\%%I\" ( echo   [ERROR] Could not remove directory: %TARGET_DIR%\%%I & set /a ERROR_COUNT+=1 )
      )
    ) else (
      if "%DRY_RUN%"=="1" (
        echo   [DRY] del "%TARGET_DIR%\%%I"
      ) else (
        echo   del "%TARGET_DIR%\%%I"
        attrib -r -s -h "%TARGET_DIR%\%%I" >nul 2>nul
        del /f /q "%TARGET_DIR%\%%I" >nul 2>nul
        if exist "%TARGET_DIR%\%%I" ( echo   [ERROR] Could not remove file: %TARGET_DIR%\%%I & set /a ERROR_COUNT+=1 )
      )
    )
)
exit /b 0

:RemoveDir
set "TARGET=%~1"
if not exist "%TARGET%\" exit /b 0
call :AssertInside "%TARGET%" || (
  set /a ERROR_COUNT+=1
  exit /b 1
)
if "%DRY_RUN%"=="1" (
  echo   [DRY] rmdir /s /q "%TARGET%"
  exit /b 0
)
echo   rmdir "%TARGET%"
if "%SWEEP%"=="1" attrib -r -s -h "%TARGET%\*" /s /d >nul 2>nul
rd /s /q "%TARGET%" >nul 2>nul
if exist "%TARGET%\" call :MarkError "Could not remove directory: %TARGET%"
exit /b 0

:RemoveFile
set "TARGET=%~1"
if not exist "%TARGET%" exit /b 0
call :AssertInside "%TARGET%" || (
  set /a ERROR_COUNT+=1
  exit /b 1
)
if "%DRY_RUN%"=="1" (
  echo   [DRY] del "%TARGET%"
  exit /b 0
)
echo   del "%TARGET%"
attrib -r -s -h "%TARGET%" >nul 2>nul
del /f /q "%TARGET%" >nul 2>nul
if exist "%TARGET%" call :MarkError "Could not remove file: %TARGET%"
exit /b 0

:RemoveFilesByPattern
set "PATTERN=%~1"
if not "%SWEEP%"=="1" exit /b 0
echo [FILES] Removing %PATTERN%
for /f "delims=" %%F in ('dir /a-d /b /s "%BASE_DIR%\%PATTERN%" 2^>nul') do (
  if "%DRY_RUN%"=="1" (
    echo   [DRY] del "%%~fF"
  ) else (
    echo   del "%%~fF"
    attrib -r -s -h "%%~fF" >nul 2>nul
    del /f /q "%%~fF" >nul 2>nul
    if exist "%%~fF" ( echo   [ERROR] Could not remove file: %%~fF & set /a ERROR_COUNT+=1 )
  )
)
exit /b 0

:ClearGeneratedLicenses
rem Safety guard: release cleanup must never touch licenses\.
echo [LICENSES] Preserving licenses\ unchanged
exit /b 0

:KeepLicenseFile
if /I "%~1"=="_records.json" exit /b 0
if /I "%~1"=="THIRD_PARTY_NOTICES.md" exit /b 0
if /I "%~1"=="RELEASE_CHECKLIST.md" exit /b 0
if /I "%~1"=="RELEASE_POLICY.md" exit /b 0
exit /b 1

:IsLink
rem Exit code 0 when the path itself is a folder link.
set "LINK_ATTR="
for %%A in ("%~1") do set "LINK_ATTR=%%~aA"
if not defined LINK_ATTR exit /b 1
if "!LINK_ATTR:l=!"=="!LINK_ATTR!" exit /b 1
exit /b 0

:AssertInside
set "TARGET_ABS=%~f1"
if /I "%TARGET_ABS%"=="%BASE_DIR%" (
  echo [SKIP] Refusing to clean project root directly.
  exit /b 1
)
set "TARGET_HEAD=!TARGET_ABS:~0,%BASE_PREFIX_LEN%!"
if /I "!TARGET_HEAD!"=="!BASE_PREFIX!" exit /b 0
echo [SKIP] Outside project root: %TARGET_ABS%
exit /b 1

:MarkError
echo   [ERROR] %~1
set /a ERROR_COUNT+=1
exit /b 0

:WAIT_KEY
echo Press any key to continue . . .
pause >nul
goto :eof
