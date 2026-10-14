@echo off
title SBG Attendance - Global 4G/5G Network Launcher
echo ========================================================
echo   SBG Smart Attendance - Starting Server + Global Tunnel
echo ========================================================
echo.

:: 1. Start Python Flask server in background
start "SBG Server" /min .\venv\Scripts\python run.py

echo [1/2] Local server started on port 5000...
timeout /t 2 >nul

:: 2. Start Cloudflare Tunnel for worldwide 4G/5G mobile access
echo [2/2] Connecting to Global 4G/5G Network...
echo.
echo ========================================================
echo Keep this window open while employees are working!
echo Public URL will appear below:
echo ========================================================
echo.
.\cloudflared.exe tunnel --url http://127.0.0.1:5000
pause
