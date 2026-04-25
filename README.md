# 🚀 Viral Auto Klip

**AI-Powered Viral Video Clipper — Inspired by Klipers.pro & OpusClip**

> Paste URL video panjang → AI deteksi momen viral → Output klip 9:16 siap upload TikTok, YouTube Shorts, Instagram Reels. Gratis & lokal!

![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python)
![FFmpeg](https://img.shields.io/badge/FFmpeg-required-green?logo=ffmpeg)
![License](https://img.shields.io/badge/License-MIT-yellow)
![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)

---

## 📌 Daftar Isi

- [Fitur Utama](#-fitur-utama)
- [Cara Kerja AI](#-cara-kerja-ai)
- [Prasyarat](#-prasyarat)
- [Instalasi](#️-instalasi)
  - [Windows](#windows)
  - [Linux / macOS](#linux--macos)
- [Konfigurasi API Key](#-konfigurasi-api-key-opsional-tapi-direkomendasikan)
- [Cara Menjalankan](#-cara-menjalankan)
  - [Web UI (Streamlit)](#1-web-ui-streamlit--paling-mudah)
  - [Web UI (HTML + Server)](#2-web-ui-html--server-alternatif)
  - [CLI](#3-cli-command-line-interface)
- [Parameter & Output](#-parameter--output)
- [Advanced Usage](#-advanced-usage)
- [FAQ & Troubleshooting](#-faq--troubleshooting)
- [Kontribusi](#-kontribusi)

---

## ✨ Fitur Utama

| Fitur | Detail |
|---|---|
| 🌐 **Multi-platform URL** | YouTube, TikTok, Instagram Reels, Facebook, X/Twitter, direct MP4/MOV |
| 🤖 **AI Viral Detection** | Score 0–100 per segmen: lucu, motivasi, shocking, emotional, hook |
| 👤 **Face Reframing** | MediaPipe auto-follow wajah utama, crop cerdas ke 9:16 seperti OpusClip |
| 📝 **Auto-Captions** | Whisper timestamps → burn-in caption modern (pop-up, karaoke, emoji, minimal) |
| 🎬 **Smart Clipping** | Pilih top-N klip non-overlapping (15–60 detik ideal) |
| 📤 **Output Viral-Ready** | 1080×1920 H.264/AAC + metadata JSON + auto thumbnail |
| 🔁 **Batch Processing** | Proses playlist/channel sekaligus |
| 🔒 **Local & Private** | Whisper jalan di lokal, tidak ada video yang dikirim ke server pihak ketiga |
| 💬 **Bahasa Indonesia First** | Keyword scoring + LLM prompt support Bahasa Indonesia optimal |

---

## 🧠 Cara Kerja AI

```
URL Video
    │
    ▼
[yt-dlp] Download video + audio (high quality)
    │
    ▼
[Whisper] Transkripsi akurat → timestamp per kata/segmen
    │
    ▼
[PySceneDetect] Deteksi scene change alami
    │
    ▼
[MediaPipe] Deteksi wajah utama + bounding box per frame
    │
    ▼
[Viral Scoring Engine]
    ├── Rule-based: keyword viral (ID+EN), sentiment, hook, panjang
    └── AI LLM (opsional): Groq / OpenAI / Gemini / Ollama
    │
    ▼
[Selection] Top-N skor, non-overlap, kategori beragam
    │
    ▼
[FFmpeg] Extract segmen → Reframe 9:16 → Burn-in caption → Encode
    │
    ▼
Output: Klip MP4 + metadata.json + thumbnail
```

---

## 📋 Prasyarat

Sebelum instalasi, pastikan sudah ada:

- **Python 3.10+** (64-bit) — [python.org](https://www.python.org/downloads/)
- **FFmpeg** — **WAJIB** untuk proses video (lihat instruksi di bawah)
- **Git** — untuk clone repo ([git-scm.com](https://git-scm.com/))
- GPU NVIDIA (opsional, mempercepat Whisper)

---

## 🛠️ Instalasi

### Windows

#### 1. Install Python 3.10+

- Download dari https://www.python.org/downloads/
- ⚠️ **WAJIB centang "Add Python to PATH"** saat install

#### 2. Install FFmpeg

- Download dari https://www.gyan.dev/ffmpeg/builds/ → pilih `ffmpeg-release-essentials.zip`
- Extract ke folder, contoh: `C:\ffmpeg`
- Tambahkan ke PATH:
  - Tekan `Win + S` → cari "Environment Variables"
  - Edit System Environment Variables → **Environment Variables**
  - Di bagian **System variables**, cari `Path` → Edit → **New**
  - Tambahkan: `C:\ffmpeg\bin`
- Verifikasi di Command Prompt baru:
  ```cmd
  ffmpeg -version
  ```

#### 3. Clone & Install Project

```cmd
git clone https://github.com/USERNAME/viral-auto-klip.git
cd viral-auto-klip

python -m venv venv
venv\Scripts\activate

pip install -r requirements.txt
```

> **Tips Windows:** Selalu gunakan Command Prompt atau PowerShell (bukan Git Bash) untuk menjalankan FFmpeg.

---

### Linux / macOS

```bash
# Install FFmpeg
sudo apt install ffmpeg          # Ubuntu/Debian
brew install ffmpeg              # macOS

# Clone & setup
git clone https://github.com/USERNAME/viral-auto-klip.git
cd viral-auto-klip

python -m venv venv
source venv/bin/activate

pip install -r requirements.txt
```

---

## 🔑 Konfigurasi API Key (Opsional tapi Direkomendasikan)

Tool ini berjalan **100% gratis** tanpa API key (mode lokal). Tapi untuk viral scoring lebih cerdas, tambahkan API key:

```bash
# Salin template ke file .env
cp .env.example .env
```

Kemudian edit file `.env`:

```env
# Groq - GRATIS & TERCEPAT (sangat direkomendasikan)
# Daftar di: https://console.groq.com/keys
GROQ_API_KEY=your_groq_key_here
LLM_MODEL=llama-3.3-70b-versatile

# Pilih salah satu provider AI
AI_PROVIDER=Groq   # Groq | OpenAI | Gemini | OpenRouter | Ollama

# Alternatif lain (opsional)
GEMINI_API_KEY=your_gemini_key
OPENROUTER_API_KEY=your_openrouter_key
OPENAI_API_KEY=your_openai_key

# Untuk Ollama (lokal, gratis)
OLLAMA_BASE_URL=http://localhost:11434

# Untuk fitur clipping software tambahan
CLIPPING_SOFTWARE_API_KEY=your_key_here
```

> ⚠️ **Jangan pernah commit file `.env` ke GitHub!** File ini sudah ada di `.gitignore`.

---

## ▶️ Cara Menjalankan

### 1. Web UI (Streamlit) — Paling Mudah

```bash
# Aktifkan virtual environment dulu
source venv/bin/activate       # Linux/macOS
venv\Scripts\activate          # Windows

streamlit run app.py
```

Buka browser → http://localhost:8501

**Cara pakai di UI:**
1. Paste URL video (YouTube, TikTok, IG, dsb.)
2. Atur parameter (jumlah klip, durasi, min score, dll.)
3. Klik **Process** → tunggu progress bar selesai
4. Download klip dari folder `output/`

---

### 2. Web UI (HTML + Server) — Alternatif

```bash
# Terminal 1: Jalankan backend API
uvicorn server:app --reload --port 8000

# Terminal 2 (Windows, bisa dobel-klik):
# Buka file viral_auto_klip_v2_real.html di browser
```

---

### 3. CLI (Command Line Interface)

Untuk pengguna advanced / otomasi:

```bash
# Basic — proses 1 video, ambil 8 klip terbaik
python cli.py --url "https://youtube.com/watch?v=XXXX"

# Dengan semua opsi
python cli.py \
  --url "https://youtube.com/watch?v=XXXX" \
  --num-clips 10 \
  --min-score 70 \
  --min-duration 15 \
  --max-duration 50 \
  --face-reframe \
  --captions-style modern \
  --platform tiktok \
  --output-dir ./hasil_klip

# Batch — proses seluruh playlist
python cli.py --playlist "https://youtube.com/playlist?list=XXXX" --num-clips 5
```

---

## ⚙️ Parameter & Output

### Parameter Utama

| Parameter | Default | Deskripsi |
|---|---|---|
| `--num-clips` | 8 | Jumlah klip yang dihasilkan |
| `--min-duration` | 15 | Durasi minimum klip (detik) |
| `--max-duration` | 60 | Durasi maksimum klip (detik) |
| `--min-score` | 60 | Minimum viral score (0–100) |
| `--face-reframe` | off | Aktifkan smart face reframing |
| `--captions-style` | modern | `modern` / `minimal` / `karaoke` / `emoji` |
| `--platform` | tiktok | `tiktok` / `shorts` / `reels` |
| `--whisper-model` | medium | `tiny` / `base` / `medium` / `large-v3` |

### Struktur Output

```
output/
└── nama_video/
    ├── clip_001_viral85_lucu.mp4         ← Score 85, kategori lucu
    ├── clip_002_viral78_motivasi.mp4
    ├── clip_003_viral72_shocking.mp4
    ├── ...
    ├── metadata.json                      ← Detail semua klip + reason AI
    └── thumbnails/
        ├── clip_001.jpg
        └── clip_002.jpg
```

---

## 🔧 Advanced Usage

### Edit Config

Buka `config.py` untuk kustomisasi:
- Viral keywords list (tambah kata niche kamu)
- Bobot scoring (audio, visual, text)
- Font caption & posisi
- Default output folder

### Tips Biar Lebih Viral

- Gunakan `--min-score 70+` untuk kualitas tinggi
- Pilih video dengan hook kuat di awal (pertanyaan, bold statement)
- Aktifkan `--face-reframe` untuk talking head / podcast
- Test A/B: upload beberapa klip, lihat yang paling engage

---

## ❓ FAQ & Troubleshooting

**Q: Apakah butuh GPU?**  
A: Tidak wajib. CPU sudah cukup untuk video di bawah 1 jam. Gunakan `--whisper-model medium` untuk keseimbangan kecepatan & akurasi.

**Q: Error `No module named 'whisper'`?**  
A: Jalankan: `pip install -U openai-whisper`

**Q: Error `ffmpeg not found`?**  
A: Pastikan FFmpeg sudah ada di PATH. Cek dengan `ffmpeg -version` di terminal baru setelah install.

**Q: Klip kurang viral?**  
A: Tambahkan Groq API key (gratis), naikkan `--min-score`, atau pilih video sumber yang lebih engaging.

**Q: Support Bahasa Indonesia?**  
A: Ya! Whisper trained sangat baik untuk Bahasa Indonesia, dan scoring keywords + LLM prompt support ID.

**Q: Apakah bisa auto-upload ke TikTok/IG?**  
A: Belum built-in (butuh API resmi + approval). Output sudah siap, tinggal upload manual atau pakai tools seperti Buffer.

**Q: Berapa biayanya?**  
A: Gratis 100% untuk mode lokal. Groq ~$0.01 per video panjang. OpenAI/Gemini sesuai harga masing-masing.

**Q: `mediapipe` error di Python 3.12?**  
A: Coba: `pip install mediapipe==0.10.14 --no-deps` lalu install dependensi manual jika perlu.

---

## 📁 Struktur Project

```
viral-auto-klip/
├── app.py                    ← Streamlit web UI
├── server.py                 ← FastAPI backend (untuk HTML UI)
├── cli.py                    ← Command line interface
├── config.py                 ← Konfigurasi global
├── requirements.txt          ← Dependensi Python
├── .env.example              ← Template environment variables
├── viral_auto_klip_v2_real.html ← UI alternatif (HTML)
├── start.bat                 ← Quick start Windows
├── core/
│   ├── analyzer.py           ← Viral scoring engine
│   ├── clipper.py            ← Video clipping & encoding
│   ├── transcriber.py        ← Whisper transcription
│   ├── fyp_agent.py          ← FYP / viral detection agent
│   ├── audio_enhancer.py     ← Audio processing
│   ├── utils.py              ← Utility functions
│   └── clipping_software.py  ← Clipping software integration
├── fonts/                    ← Font untuk caption
├── temp/                     ← File sementara (auto-clean)
└── output/                   ← Hasil klip (dibuat otomatis)
```

---

## 🤝 Kontribusi

Pull request, issue, dan saran sangat diterima!

1. Fork repo ini
2. Buat branch baru: `git checkout -b fitur-baru`
3. Commit perubahan: `git commit -m "Tambah fitur X"`
4. Push: `git push origin fitur-baru`
5. Buat Pull Request

---

## 📄 Lisensi

MIT License — bebas digunakan, dimodifikasi, dan didistribusikan dengan menyebut sumber.

---

**Dibuat dengan ❤️ untuk creator Indonesia yang mau scaling konten viral tanpa ribet editing!**

*Version 2.0 — April 2026*