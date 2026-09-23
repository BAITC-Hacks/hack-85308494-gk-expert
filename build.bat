@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" build_portable.py
) else (
  py -3.11 build_portable.py
)
exit /b %errorlevel%
