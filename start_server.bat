@echo off
set MONITOR_TOKEN=replace-with-a-long-random-secret
python -m uvicorn server:app --host 0.0.0.0 --port 8000
pause
