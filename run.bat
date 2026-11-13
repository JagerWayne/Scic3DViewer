@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe call setup.bat
start "" http://127.0.0.1:6711/studio
.venv\Scripts\python -m uvicorn app:app --host 127.0.0.1 --port 6711
pause
