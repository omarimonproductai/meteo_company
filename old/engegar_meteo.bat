@echo off
REM ─── Cooltra Meteo: engegar servidor i obrir el dashboard ──────────────────
cd /d "%~dp0"

REM Matar qualsevol servidor que ja estigui usant el port 8000
echo Aturant servidors antics al port 8000...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8000" ^| findstr "LISTENING"') do (
    taskkill /F /PID %%a >nul 2>&1
)

REM Engegar el servidor en una finestra nova
start "Cooltra Meteo Server" cmd /k python3 server.py

REM Esperar uns segons perque el servidor estigui llest
timeout /t 3 /nobreak >nul

REM Obrir el dashboard amb Chrome (cache-busting amb timestamp)
start chrome "http://localhost:8000/?v=%RANDOM%%RANDOM%"
