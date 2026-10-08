@echo off
title Swagbucks Multi Tracker - Production Server
cd /d "%~dp0"
echo [*] Starting Swagbucks Multi Tracker in Production Mode...
start /b .\.venv\Scripts\python.exe web_app.py
timeout /t 3 >nul
echo [*] Launching Cloudflare Public HTTPS Tunnel...
.\cloudflared.exe tunnel --url http://localhost:8080
pause
