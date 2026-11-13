@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe call setup.bat
if not "%~1"=="" (
  .venv\Scripts\python admin.py %*
  pause
  exit /b
)
:menu
echo.
echo  Scic3DViewer license admin
echo  [1] New license
echo  [2] List licenses
echo  [3] Revoke a license
echo  [4] Upgrade a license
echo  [5] Quit
set c=
set /p c=Choose:
if "%c%"=="1" .venv\Scripts\python admin.py new
if "%c%"=="2" .venv\Scripts\python admin.py list
if "%c%"=="3" goto revoke
if "%c%"=="4" goto upgrade
if "%c%"=="5" exit /b
goto menu

:revoke
set /p e=Email:
.venv\Scripts\python admin.py revoke "%e%"
goto menu

:upgrade
set /p e=Email:
set /p t=Tier (FREE/PRO/ENTERPRISE):
.venv\Scripts\python admin.py upgrade "%e%" "%t%"
goto menu
