@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Creating virtual environment...
  python -m venv .venv || (echo Python 3.10+ is required & pause & exit /b 1)
)
.venv\Scripts\python -m pip install -q --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt || (echo Install failed & pause & exit /b 1)
echo.
echo Setup complete. Start the server with run.bat
pause
