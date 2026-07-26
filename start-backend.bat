@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "ACTION=%~1"
set "RELOAD_FLAG="
if "%ACTION%"=="" set "ACTION=start"
if /I "%ACTION%"=="--reload" (
  set "ACTION=start"
  set "RELOAD_FLAG=--reload"
)
if /I "%ACTION%"=="start" if /I "%~2"=="--reload" set "RELOAD_FLAG=--reload"

if /I "%ACTION%"=="status" goto control
if /I "%ACTION%"=="stop" goto control
if /I "%ACTION%"=="restart" goto restart
if /I not "%ACTION%"=="start" goto usage

set "PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
  echo Project Python environment not found: .venv
  pause
  exit /b 1
)

"%PYTHON%" -c "import uvicorn" >nul 2>&1
if errorlevel 1 (
  echo Backend dependencies are missing. Run:
  echo   ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0backend-control.ps1" -Action guard -ProjectRoot "%~dp0." -Port 8000
set "GUARD_RESULT=%ERRORLEVEL%"
if "%GUARD_RESULT%"=="1" (
  echo Backend is already managed on port 8000. Use start-backend.bat status.
  exit /b 0
)
if "%GUARD_RESULT%"=="3" (
  echo Backend is running stale source. Use start-backend.bat restart.
  exit /b 3
)
if not "%GUARD_RESULT%"=="0" (
  echo Port 8000 is occupied by an unmanaged process. No backend was started.
  exit /b 2
)

echo Starting backend from %~dp0
"%PYTHON%" -m uvicorn app.main:app --app-dir "%~dp0." --host 127.0.0.1 --port 8000 %RELOAD_FLAG%
set "EXIT_CODE=%ERRORLEVEL%"
echo Backend stopped with exit code %EXIT_CODE%.
exit /b %EXIT_CODE%

:control
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0backend-control.ps1" -Action "%ACTION%" -ProjectRoot "%~dp0." -Port 8000
exit /b %ERRORLEVEL%

:restart
call "%~f0" stop
if errorlevel 2 exit /b %ERRORLEVEL%
timeout /t 1 /nobreak >nul
call "%~f0" start %~2
exit /b %ERRORLEVEL%

:usage
echo Usage:
echo   start-backend.bat start [--reload]
echo   start-backend.bat stop
echo   start-backend.bat restart [--reload]
echo   start-backend.bat status
exit /b 2
