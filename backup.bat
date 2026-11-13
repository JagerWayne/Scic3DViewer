@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe call setup.bat
.venv\Scripts\python backup.py
pause
