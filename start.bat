@echo off
title Viral Auto Klip v2 - One Click Setup
color 0A

echo ================================================
echo   VIRAL AUTO KLIP v2 - ONE CLICK SETUP
echo   Powered by Groq + Whisper + FFmpeg
echo ================================================
echo.

:: Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python tidak ditemukan!
    echo Silakan install Python 3.10+ dari https://python.org
    pause
    exit /b
)

echo [1/6] Membuat virtual environment...
if not exist venv (
    python -m venv venv
    echo Virtual environment dibuat.
) else (
    echo Virtual environment sudah ada.
)

echo [2/6] Mengaktifkan virtual environment...
call venv\Scripts\activate.bat

echo [3/6] Menginstall dependencies...
pip install --upgrade pip >nul
pip install -r requirements.txt

echo [4/6] Setup FFmpeg (jika belum ada)...
if not exist ffmpeg\bin\ffmpeg.exe (
    echo Downloading FFmpeg...
    powershell -Command "Invoke-WebRequest -Uri 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' -OutFile 'ffmpeg.zip'"
    powershell -Command "Expand-Archive -Path 'ffmpeg.zip' -DestinationPath 'ffmpeg_temp' -Force"
    move ffmpeg_temp\ffmpeg-*-essentials_build\bin ffmpeg\bin >nul 2>&1
    rmdir /s /q ffmpeg_temp
    del ffmpeg.zip
    echo FFmpeg berhasil di-download dan di-extract.
) else (
    echo FFmpeg sudah tersedia.
)

:: Add FFmpeg to PATH for this session
set PATH=%CD%\ffmpeg\bin;%PATH%

echo [5/6] Menjalankan Backend Server...
echo.
echo Backend akan berjalan di: http://localhost:8000
echo Tekan Ctrl+C untuk stop server.
echo.

:: Run the server
uvicorn server:app --reload --port 8000

echo.
echo Server berhenti.
pause