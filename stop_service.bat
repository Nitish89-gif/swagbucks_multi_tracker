@echo off
title Stop Swagbucks Tracker 24/7 Service
cd /d "%~dp0"
echo [*] Stopping Swagbucks Multi Tracker 24/7 Service...
taskkill /IM cloudflared.exe /F >nul 2>&1
if exist "data\service_runner.pid" (
    for /f "usebackq delims=" %%P in ("data\service_runner.pid") do (
        taskkill /PID %%P /T /F >nul 2>&1
    )
    del /f /q "data\service_runner.pid" >nul 2>&1
)
taskkill /IM pythonw.exe /F >nul 2>&1
echo [✓] Swagbucks Tracker Service stopped successfully.
ping 127.0.0.1 -n 2 >nul
