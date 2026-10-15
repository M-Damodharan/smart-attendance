@echo off
title SBG Attendance - 24/7 Watchdog Service
cd /d "d:\smart-attendance"
echo ===================================================
echo   SBG Smart Attendance 24/7 Background Service
echo   Local Server: http://127.0.0.1:5000
echo   Live Cloud:   https://smart-attendance-u7e6.onrender.com
echo ===================================================
python scripts\watchdog_24_7.py
pause
