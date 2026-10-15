@echo off
title SBG Attendance - High-Performance Multi-Threaded Server + Global Tunnel
echo ======================================================================
echo   SRI BALAJI GEARS (SBG) - PRODUCTION LOAD-BALANCED ATTENDANCE SERVER
echo ======================================================================
echo.

:: Change directory to current script folder
cd /d "%~dp0"

echo [1/2] Starting High-Concurrency Waitress Server (16 Worker Threads, 1000 Connection Limit)...
start "SBG Waitress WSGI Server" /min cmd /c ".\venv\Scripts\python.exe run.py"

timeout /t 3 >nul

echo [2/2] Starting Global Cloudflare Tunnel for Worldwide 4G/5G Access...
echo.
echo ======================================================================
echo Server is LIVE with 16 Concurrent Worker Threads!
echo Keep this window open to maintain global 4G/5G connectivity.
echo ======================================================================
echo.

.\cloudflared.exe tunnel --url http://127.0.0.1:5000
pause
