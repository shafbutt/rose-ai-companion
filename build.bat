@echo off
REM ============================================================
REM  ROSE — Build Script for Desktop Packaging (PyInstaller)
REM  Creates a standalone .exe that can be distributed.
REM ============================================================

echo [ROSE] Building desktop package...

REM Activate virtual environment
call venv\Scripts\activate.bat

REM Install PyInstaller if not present
pip install pyinstaller 2>nul

REM Build with PyInstaller
pyinstaller --noconfirm --onedir --windowed ^
    --name "ROSE" ^
    --add-data "rose_interface.html;." ^
    --add-data "voice;voice" ^
    --hidden-import "piper" ^
    --hidden-import "faster_whisper" ^
    --hidden-import "groq" ^
    --hidden-import "psutil" ^
    --hidden-import "sounddevice" ^
    --hidden-import "webview" ^
    --hidden-import "numpy" ^
    rose_app.py

if %ERRORLEVEL% NEQ 0 (
    echo [ROSE] Build failed!
    pause
    exit /b 1
)

echo.
echo [ROSE] Build complete!
echo [ROSE] Executable is in: dist\ROSE\ROSE.exe
echo.
echo To distribute:
echo   1. Copy the entire dist\ROSE folder
echo   2. Add a .env file with GROQ_API_KEY=your_key
echo   3. Run ROSE.exe
echo.
pause
