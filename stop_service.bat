@echo off
title Stop Swagbucks Tracker 24/7 Service
cd /d "%~dp0"
echo [*] Stopping Swagbucks Multi Tracker 24/7 Service...
.\.venv\Scripts\python.exe -c "
import os, sys, subprocess
pid_file = 'data/service_runner.pid'
if os.path.exists(pid_file):
    try:
        with open(pid_file) as f:
            pid = int(f.read().strip())
        print(f'Terminating service runner PID {pid}...')
        subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], capture_output=True)
        os.remove(pid_file)
    except Exception as e:
        print('Error removing pid:', e)
subprocess.run(['taskkill', '/IM', 'cloudflared.exe', '/F'], capture_output=True)
print('[✓] Swagbucks Tracker Service stopped successfully.')
"
timeout /t 3
