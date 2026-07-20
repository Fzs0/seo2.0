@echo off
setlocal

cd /d "%~dp0"
set "DB_USER=seo"
set "DB_PASS=seo_dev_local"
set "DB_NAME=seo_workbench"
set "PG_CONTAINER=pg-workbench"
set "DATABASE_URL=postgresql+asyncpg://%DB_USER%:%DB_PASS%@localhost:5433/%DB_NAME%"

where docker >nul 2>nul
if errorlevel 1 (
  if exist "%ProgramFiles%\Docker\Docker\resources\bin\docker.exe" (
    set "PATH=%ProgramFiles%\Docker\Docker\resources\bin;%PATH%"
  ) else (
    echo [FAIL] docker command not found. Open Docker Desktop, then run this bat again.
    pause
    exit /b 1
  )
)

echo [1/4] starting local Postgres / Redis...
docker compose up -d postgres redis
if errorlevel 1 goto fail

echo [2/4] waiting for database...
for /l %%i in (1,1,40) do (
  docker exec %PG_CONTAINER% pg_isready -U %DB_USER% -d %DB_NAME% >nul 2>nul
  if not errorlevel 1 goto pg_ready
  timeout /t 1 /nobreak >nul
)
echo [FAIL] database was not ready after 40 seconds.
pause
exit /b 1

:pg_ready
echo [3/4] applying database migrations...
for %%f in (db\migrations\*.sql) do (
  echo   - %%~nxf
  docker cp "%%f" %PG_CONTAINER%:/tmp/%%~nxf >nul
  if errorlevel 1 goto fail
  docker exec %PG_CONTAINER% psql -U %DB_USER% -d %DB_NAME% -v ON_ERROR_STOP=1 -f /tmp/%%~nxf
  if errorlevel 1 goto fail
)

where node >nul 2>nul
if not errorlevel 1 (
  echo [seed] loading rule baseline...
  node db\scripts\seed_rule_baseline.mjs
) else (
  echo [seed] node not found, skip rule baseline.
)

echo [4/4] starting backend: http://127.0.0.1:8000
call "%~dp0start-backend.bat"
exit /b %errorlevel%

:fail
echo [FAIL] startup failed. The last error above is the reason.
pause
exit /b 1
