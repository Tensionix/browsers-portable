@echo off
setlocal EnableExtensions
chcp 65001 >nul

rem Audion Browsers Portable: the console door. A button of the window and this
rem command run the same thing - an operation of the manifest, with the same defaults.
rem
rem   browsers_cli.cmd describe
rem   browsers_cli.cmd run --operation browsers_build --set browsers=chrome,brave --target D:\Portable
rem   browsers_cli.cmd run --operation browsers_check --source E:\Flash\Browsers
rem
rem Exit codes: 0 done, 1 the operation failed or did only a part, 2 wrong call, 130 stopped.
rem
rem The folder of the caller stays the current one: a relative --params, --result or
rem --cancel-file is counted from it. A relative --source or --target is counted from
rem the program folder - the door does that itself.

set "BASE_DIR=%~dp0"
if "%BASE_DIR:~-1%"=="\" set "BASE_DIR=%BASE_DIR:~0,-1%"

set "PYTHON_CMD="
if exist "%BASE_DIR%\runtime\python.exe" set "PYTHON_CMD=%BASE_DIR%\runtime\python.exe"
if not defined PYTHON_CMD if exist "%BASE_DIR%\runtime\python\python.exe" set "PYTHON_CMD=%BASE_DIR%\runtime\python\python.exe"
rem Not a block in brackets: a path with a bracket in it - "Program Files (x86)" - would close it.
if not defined PYTHON_CMD goto NO_PYTHON

rem The door is started as a file, not as a module: the embedded Python of the
rem project does not look for modules in the current folder, and the door puts
rem the project on the path itself.
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
"%PYTHON_CMD%" -u "%BASE_DIR%\system_core\cli.py" %*
exit /b %ERRORLEVEL%

:NO_PYTHON
echo [ERR] Python of the project was not found: "%BASE_DIR%\runtime\python.exe"
echo       Build the environment first: builder_main.cmd
exit /b 2
