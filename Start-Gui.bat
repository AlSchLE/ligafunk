@echo off
REM ============================================================
REM  Sleeper Web GUI
REM  Start server in background and open browser at http://localhost:5000
REM ============================================================
setlocal

REM --- Find Python ---
set "PYTHON_EXE="
where python >nul 2>&1 && set "PYTHON_EXE=python"
if not defined PYTHON_EXE (
    where python3 >nul 2>&1 && set "PYTHON_EXE=python3"
)
if not defined PYTHON_EXE (
    for /f "delims=" %%i in ('where py 2^>nul') do (
        set "PYTHON_EXE=%%i"
        goto :found_python
    )
)
:found_python

if not defined PYTHON_EXE (
    echo [ERROR] Python not found. Please install Python 3.10+ from https://python.org
    pause
    exit /b 1
)

REM --- Check Flask ---
"%PYTHON_EXE%" -c "import flask" >nul 2>&1
if errorlevel 1 (
    echo [INFO] Installing Flask...
    "%PYTHON_EXE%" -m pip install flask
)

REM --- Start server in background ---
echo [INFO] Starting Sleeper Web...
start "Sleeper Web Server" /min "%PYTHON_EXE%" app.py

REM --- Wait for server to start ---
timeout /t 3 /nobreak >nul

REM --- Open browser ---
start http://localhost:5000

echo [INFO] Browser opened at http://localhost:5000
echo [INFO] Server is running in background. Close the server window to stop.
pause
