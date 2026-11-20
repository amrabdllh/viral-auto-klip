@echo off
title Viral Auto Klip Studio v6 - Direct Setup (tanpa venv)
color 0A

echo ================================================
echo   VIRAL AUTO KLIP STUDIO v6 - DIRECT RUN (TANPA VENV)
echo   Claude / GPT / Gemini / DeepSeek / Mistral / Groq / Custom
echo ================================================
echo.
if exist venv\Scripts\python.exe echo [INFO] Folder venv\ terdeteksi tapi TIDAK dipakai: start.bat memakai Python sistem. Untuk venv: venv\Scripts\activate lalu jalankan manual.

:: Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 goto nopython

:: Mode dari argumen pertama: 1 = Backend + HTML UI, 2 = Streamlit (default 1)
set MODE=%1
if "%MODE%"=="" set MODE=1

echo [1/5] Upgrade pip...
python -m pip install --upgrade pip --quiet

echo [2/5] Install dependencies (langsung ke Python sistem, tanpa venv)...
python -m pip install -r requirements.txt
if errorlevel 1 goto piperr
:: GPU NVIDIA: PyTorch CUDA untuk upscale (Real-ESRGAN) + restorasi wajah (GFPGAN). Tanpa GPU: dilewati.
nvidia-smi >nul 2>&1
if errorlevel 1 goto torch_ok
python -c "import torch,sys;sys.exit(0 if torch.cuda.is_available() else 1)" >nul 2>&1
if not errorlevel 1 goto torch_ok
echo GPU NVIDIA terdeteksi: memasang PyTorch CUDA untuk upscale ^& restorasi wajah AI (sekali saja, +-2.5 GB)...
python -m pip install --upgrade torch torchvision --index-url https://download.pytorch.org/whl/cu132
:torch_ok

echo [3/5] Setup FFmpeg (jika belum ada)...
where ffmpeg >nul 2>&1
if not errorlevel 1 goto ffmpeg_ok
if exist ffmpeg\bin\ffmpeg.exe goto ffmpeg_local

echo Downloading FFmpeg...
powershell -Command "Invoke-WebRequest -Uri 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' -OutFile 'ffmpeg.zip'"
powershell -Command "Expand-Archive -Path 'ffmpeg.zip' -DestinationPath 'ffmpeg_temp' -Force"
if not exist ffmpeg mkdir ffmpeg
if exist ffmpeg\bin rmdir /s /q ffmpeg\bin
for /d %%d in (ffmpeg_temp\ffmpeg-*-essentials_build) do move "%%d\bin" "ffmpeg\bin"
if exist ffmpeg\bin\ffmpeg.exe (
    echo FFmpeg berhasil di-download dan di-extract.
) else (
    echo [WARNING] Extract FFmpeg gagal. Install manual dari https://www.gyan.dev/ffmpeg/builds/ lalu tambahkan ke PATH.
)
rmdir /s /q ffmpeg_temp
del ffmpeg.zip

:ffmpeg_local
set "PATH=%CD%\ffmpeg\bin;%PATH%"
echo FFmpeg lokal siap untuk sesi ini.
goto ffmpeg_done

:ffmpeg_ok
echo FFmpeg sudah tersedia di PATH.

:ffmpeg_done
echo [4/5] Siapkan file .env (bila belum ada)...
if not exist .env copy .env.example .env >nul
if not exist .env (echo .env belum dibuat! Salin manual .env.example -^> .env) else (echo .env sudah ada.)

echo [5/5] Menjalankan aplikasi (mode %MODE%)...
echo.

if "%MODE%"=="2" goto streamlitmode

echo Backend berjalan di: http://localhost:8000
echo UI Studio v6: http://localhost:8000/app  (UI lama: /legacy)
echo Tekan Ctrl+C untuk stop server.
echo.
start "" "http://localhost:8000/app"
python -m uvicorn server:app --reload --host 127.0.0.1 --port 8000
goto done

:streamlitmode
echo Menjalankan Streamlit UI di http://localhost:8501
echo Tekan Ctrl+C untuk stop.
echo.
python -m streamlit run app.py
goto done

:nopython
echo [ERROR] Python tidak ditemukan!
echo Silakan install Python 3.10+ dari https://python.org
pause
exit /b

:piperr
echo.
echo [ERROR] Install dependency gagal. Cek koneksi internet / pesan error di atas.
pause
exit /b 1

:done
echo.
echo Server berhenti.
pause
