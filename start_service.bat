@echo off
title Start Swagbucks Tracker 24/7 Service
cd /d "%~dp0"

echo [*] Starting Swagbucks Multi Tracker 24/7 Background Service...
start "" ".\.venv\Scripts\pythonw.exe" "service_runner.py"

ping 127.0.0.1 -n 3 >nul
echo [✓] Swagbucks Tracker Service started in background.
echo Check current_public_url.txt or your phone push notification for the live link.
ping 127.0.0.1 -n 2 >nul
