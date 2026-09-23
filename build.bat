@echo off
chcp 65001 >nul
echo ===================================================
echo   QazaqProtocol AI - Standalone EXE Builder
echo ===================================================

cd /d "%~dp0"

echo [1/2] Building executable with PyInstaller...
py -3.11 -m PyInstaller --noconfirm --windowed --onefile --name QazaqProtocol --add-data "ui;ui" --add-data "core;core" --add-data "storage;storage" --add-data ".env.example;." --hidden-import=pyaudiowpatch --hidden-import=webview --hidden-import=reportlab --hidden-import=reportlab.lib --hidden-import=reportlab.platypus --hidden-import=reportlab.graphics --hidden-import=docx --hidden-import=dotenv --hidden-import=soundfile --hidden-import=sounddevice --hidden-import=mss --hidden-import=PIL --hidden-import=openai --hidden-import=numpy --hidden-import=scipy --collect-all reportlab --collect-all webview app.py

if errorlevel 1 (
    echo [ERROR] Build failed!
    exit /b 1
)

echo.
echo [2/2] Build successful!
echo Binary created at dist\QazaqProtocol.exe
exit /b 0
