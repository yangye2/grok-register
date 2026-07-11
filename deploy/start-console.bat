@echo off
setlocal EnableExtensions

cd /d "%~dp0.."
set "ROOT_DIR=%CD%"
set "CONSOLE_DIR=%ROOT_DIR%\apps\console"
set "CONSOLE_VENV=%CONSOLE_DIR%\.venv"
set "URL=http://127.0.0.1:18600"

set "GROK_REGISTER_SOURCE_DIR=%ROOT_DIR%"
set "GROK_REGISTER_CONSOLE_HOST=127.0.0.1"
set "GROK_REGISTER_CONSOLE_PORT=18600"
set "GROK_REGISTER_CONSOLE_MAX_CONCURRENT_TASKS=1"

if exist "%ROOT_DIR%\.venv\Scripts\python.exe" (
    set "GROK_REGISTER_PYTHON=%ROOT_DIR%\.venv\Scripts\python.exe"
) else (
    set "GROK_REGISTER_PYTHON=python"
)

if not exist "%CONSOLE_VENV%\Scripts\python.exe" (
    echo Creating console virtual environment...
    where py >nul 2>nul
    if not errorlevel 1 (
        py -3 -m venv "%CONSOLE_VENV%"
    ) else (
        python -m venv "%CONSOLE_VENV%"
    )
    if errorlevel 1 goto :error
)

echo Installing console dependencies...
"%CONSOLE_VENV%\Scripts\python.exe" -m pip install -r "%CONSOLE_DIR%\requirements.txt"
if errorlevel 1 goto :error

echo Starting Grok Register Console at %URL%
start "Grok Register Console" "%URL%"
"%CONSOLE_VENV%\Scripts\python.exe" "%CONSOLE_DIR%\app.py"
goto :end

:error
echo.
echo Console startup failed. Confirm that Python 3 is installed and available in PATH.
pause

:end
endlocal
