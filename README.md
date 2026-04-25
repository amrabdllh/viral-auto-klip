# 🚀 Viral Auto Klip - AI-Powered Viral Video Clipper (Inspired by Klipers.pro)

**Buat klip viral otomatis dari video panjang apapun!**  
Paste URL YouTube, TikTok, Instagram, Facebook, atau video langsung → AI deteksi momen viral (lucu, motivasi, shocking, emotional, hooks) → Auto clip, face detection & reframing, auto-caption stylish, output vertical 9:16 siap upload ke TikTok, YouTube Shorts, Instagram Reels.

**Fitur Utama (Sempurna & Lengkap):**
- ✅ **Support Semua URL**: YouTube, TikTok, IG Reels, FB, Twitter/X, direct MP4/MOV via yt-dlp
- ✅ **AI Viral Detection**: Analisis transcript + audio + visual untuk score virality (0-100). Deteksi:
  - Lucu (punchline, laughter)
  - Motivasi / Inspirasi
  - Shocking / Plot twist
  - Educational tips & "cara"
  - Emotional peaks (sedih, excited, angry)
  - Hooks awal/akhir (pertanyaan, bold statement)
- ✅ **Scene & Face Detection**: PySceneDetect + MediaPipe Face Detection → Auto crop & follow wajah utama (reframing pintar seperti OpusClip/Klipers)
- ✅ **Auto-Captions Premium**: Whisper timestamps → Burn-in captions modern (pop-up words, shadow, emoji support, customizable font/color/position). Support multi-language termasuk Bahasa Indonesia.
- ✅ **Smart Clipping**: Pilih top N klip (15-60 detik ideal), non-overlapping, berdasarkan viral score + engagement potential.
- ✅ **Format Output Viral-Ready**:
  - 9:16 vertical (1080x1920) atau custom aspect
  - High quality H.264/AAC
  - Optional: Subtle zoom on face, speed ramp, background music low, watermark, end CTA
  - Metadata JSON dengan score, reason, timestamps
- ✅ **Batch & Advanced**:
  - Proses playlist/channel
  - Multi-clip dari 1 video (10-50+ klip)
  - Gaming mode (facecam + gameplay split view)
  - Podcast mode (speaker diarization sederhana)
  - Export ke folder terstruktur + auto-generate thumbnail
- ✅ **UI Super User-Friendly**: Streamlit web app (jalan di browser lokal) atau CLI powerful
- ✅ **Performance**: Progress bar real-time, resumeable, error handling bagus
- ✅ **Privacy & Cost**: Utamakan local processing (Whisper local). Optional: Groq/OpenAI untuk scoring lebih cerdas (murah & cepat)

**Inspired by Klipers.pro** (scene detection, face detection, auto-caption, viral shorts otomatis). Tool ini bikin kamu punya "Klipers pribadi" yang lebih customizable & powerful!

---

## 🛠️ Setup & Instalasi (5 Menit)

### 1. Prerequisites
- **Python 3.10+** (64-bit) — Download dari python.org
- **FFmpeg** (WAJIB) — Lihat instruksi Windows di bawah
- GPU optional (NVIDIA lebih cepat, tapi CPU juga berjalan normal)

### 2. Clone / Download
```bash
cd /path/to/your/folder
# (atau copy folder viral_auto_klip ini)
```

### 3. Virtual Environment & Install
```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 4. (Optional) API Keys untuk Fitur Super AI
Buat file `.env` :
```
GROQ_API_KEY=your_groq_key_here   # Gratis & super cepat (rekomendasi!)
OPENAI_API_KEY=your_openai_key    # Untuk scoring lebih advanced
ASSEMBLYAI_API_KEY=...            # Alternatif transcription (opsional)
```

Dapatkan Groq key gratis di https://console.groq.com/keys (recommended untuk viral scoring)

### 5. Run!

**Web UI (Paling Mudah - Rekomendasi):**
```bash
streamlit run app.py
```
Buka browser http://localhost:8501 → Paste URL → Atur parameter → Process! 

**CLI (Advanced / Automation):**
```bash
python cli.py --url "https://youtube.com/watch?v=..." --num-clips 10 --min-score 65 --face-reframe --captions-style modern
```

---

## 🪟 Instruksi Khusus Windows (Windows 10/11)

**Ya, tool ini 100% bisa jalan di Windows!**

### Langkah Instalasi di Windows:

1. **Install Python 3.10+** (64-bit)
   - Download dari: https://www.python.org/downloads/
   - **PENTING**: Centang "Add Python to PATH" saat install

2. **Install FFmpeg (WAJIB untuk render video)**
   - Download versi Windows: https://www.gyan.dev/ffmpeg/builds/
   - Pilih `ffmpeg-release-essentials.zip`
   - Extract ke folder, misalnya: `C:\ffmpeg`
   - Tambahkan ke PATH:
     - Tekan `Win + S` → ketik "Environment Variables" → Edit System Environment Variables
     - Klik **Environment Variables** → Cari `Path` di System variables → Edit
     - Tambahkan: `C:\ffmpeg\bin`
   - Verifikasi: Buka Command Prompt baru → ketik `ffmpeg -version`

3. **Setup Project**
   ```cmd
   cd C:\path\to\viral_auto_klip
   python -m venv venv
   venv\Scripts\activate
   pip install -r requirements.txt
   ```

4. **Jalankan Backend (untuk versi Real)**
   ```cmd
   uvicorn server:app --reload --port 8000
   ```

5. **Buka UI**
   - Buka file `viral_auto_klip_v2_real.html` di browser, **atau**
   - Jalankan `streamlit run app.py`

**Catatan Penting Windows:**
- Selalu gunakan **Command Prompt** atau **PowerShell** (bukan Git Bash untuk FFmpeg)
- Jika error `ffmpeg not found`, pastikan path sudah benar di Environment Variables
- Whisper + torch biasanya otomatis install versi CPU. Kalau mau GPU, install `torch` dengan CUDA terpisah.

---

## 🐧 Linux / macOS (untuk referensi)

```bash
sudo apt install ffmpeg          # Ubuntu/Debian
brew install ffmpeg              # macOS
```

---

## 📖 Cara Pakai Detail

### Input yang Didukung
- YouTube: https://youtu.be/xxx atau full watch?v=
- TikTok: https://www.tiktok.com/@user/video/123
- Instagram: https://www.instagram.com/reel/ABC/
- Facebook, X/Twitter video posts
- Direct video: https://example.com/video.mp4

### Parameter Penting (di UI/CLI)
- **Jumlah Klip**: 5-20 (default 8)
- **Durasi Klip**: Min 15s, Max 60s (ideal viral)
- **Min Viral Score**: 60-80 (semakin tinggi, semakin "pasti viral")
- **Face Reframe**: On → AI ikuti wajah pembicara utama (perfect untuk talking head/podcast)
- **Caption Style**: 
  - modern (pop words, bold, shadow)
  - minimal (clean subtitle)
  - karaoke (highlight kata per kata)
  - emoji (auto tambah emoji lucu/motivasi)
- **Target Platform**: TikTok / Shorts / Reels (auto optimize: aspect, caption position, music hint)
- **Bahasa**: Auto-detect (ID/EN support terbaik)

### Output
```
output/
  video_title/
    clip_001_viral85_lucu.mp4          # Score 85 - Lucu
    clip_002_viral78_motivasi.mp4
    ...
    metadata.json                       # Detail semua klip + reason
    thumbnails/
```

Setiap klip siap upload langsung! Thumbnail auto-generated dari frame terbaik.

---

## 🧠 Bagaimana AI Deteksi Viral? (Teknis)

1. **Download** video + audio (yt-dlp, high quality)
2. **Transcription** akurat dengan Whisper (large-v3 atau medium) → timestamp per kata/segmen
3. **Scene Detection** (PySceneDetect) → potong di scene change natural
4. **Face & Speaker Detection** (MediaPipe) → identifikasi wajah utama, bounding box per frame
5. **Viral Scoring Engine** (Hybrid):
   - **Rule-based** (cepat): 
     - Keyword viral (Bahasa Indonesia & English): "rahasia", "jangan", "cara mudah", "shocking", "you won't believe", "tips rahasia", "motivasi", "sukses", dll.
     - Sentiment analysis (VADER/TextBlob): excitement, surprise
     - Hook detection: kalimat pendek di awal/akhir segmen
     - Length score: 20-45s = +poin
   - **AI LLM Scoring** (opsional via Groq/OpenAI):
     Prompt: "Beri score virality 0-100 untuk segmen ini untuk TikTok/Shorts. Alasan: lucu/motivasi/shocking? Berikan 1 kalimat reason."
   - **Visual/Audio**: Motion energy, face close-up ratio, audio peak (laughter/energy)
6. **Selection**: Top skor, non-overlap, diverse category (max 1 lucu berurutan, dll.)
7. **Processing**:
   - Extract exact segment (ffmpeg)
   - Reframe: Smooth crop ke 9:16 mengikuti wajah utama (kalau on)
   - Caption burn-in dengan timing presisi + style
   - Encode optimal (fast, high quality)
8. **Post**: Generate reason text, suggest caption upload, thumbnail

**Hasil**: Klip yang "pasti bikin orang scroll stop" karena AI pilih momen terbaik + presentasi pro.

---

## 🔧 Advanced Usage & Customization

### Edit Config
Buka `config.py` untuk ubah:
- Viral keywords list (tambah kata Indonesia spesifik niche kamu)
- Scoring weights
- Caption font path (pakai font keren gratis dari Google Fonts)
- Default output folder

### Batch Processing
```bash
python cli.py --playlist "https://youtube.com/playlist?list=xxx" --num-clips 5
```

### Integrate dengan Klipers.pro (Jika Punya Akun)
Tool ini bisa extended untuk call Klipers API (setelah kamu baca docs mereka). Hubungi support Klipers untuk API key, lalu tambah opsi `--use-klipers-api`.

### Tips Biar Lebih Viral
- Pilih video dengan hook kuat di awal
- Gunakan --min-score 70+ untuk kualitas tinggi
- Tambah music trending di post-production (CapCut atau manual)
- Test A/B: Upload beberapa klip, lihat analytics

---

## ❓ FAQ & Troubleshooting

**Q: Butuh GPU?**  
A: Tidak wajib. CPU ok untuk video <1 jam. Untuk lebih cepat, pakai `--whisper-model medium` atau Groq transcription.

**Q: Error "No module named 'whisper'"?**  
A: `pip install -U openai-whisper` atau gunakan faster-whisper (lebih cepat).

**Q: Klip kurang "viral"?**  
A: Naikkan min-score atau tambah Groq key untuk AI scoring lebih pintar. Atau pilih video yang memang bagus.

**Q: Support Bahasa Indonesia bagus?**  
A: Ya! Whisper trained bagus untuk ID, dan scoring keywords + LLM support ID.

**Q: Bisa auto upload ke TikTok/IG?**  
A: Belum built-in (butuh API official + approval). Tapi output sudah siap, tinggal manual upload atau pakai tools seperti Buffer nanti.

**Q: Biaya?**  
A: Gratis 100% untuk local mode. Groq ~$0.01 per video panjang. Klipers API berbayar sesuai usage.

---

## 🏆 Kenapa Tool Ini Lebih Bagus dari Lainnya?

- **Lokal & Private**: Video kamu tidak upload ke server pihak ketiga (kecuali optional LLM)
- **Customizable Total**: Ubah semua parameter, style, scoring logic
- **Face Reframing Pintar**: Ikuti wajah seperti pro editor (jarang di tool gratis)
- **Bahasa Indonesia First**: Keywords, prompt, support optimal
- **Production Ready**: Error handling, logging, metadata lengkap, scalable
- **Inspired Klipers + OpusClip + Custom Magic**: Gabungan fitur terbaik + ekstra

**Dibuat dengan ❤️ untuk creator Indonesia yang mau scaling konten viral tanpa ribet editing!**

---

## 📞 Support & Kontribusi

- Issue / Feature request: (buat GitHub repo kamu)
- Update: Tool ini terus di-update. Cek versi terbaru.
- Ingin versi lebih advanced (multi AI agent, auto post, analytics)? Hubungi saya!

**Selamat bikin klip viral! 🔥**  
Contoh hasil: Banyak creator pakai sistem serupa dapat 1M+ views per klip.

---

*Version 1.0 - April 2026 | Perfect Auto Viral Clipper*