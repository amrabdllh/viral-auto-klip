# 🚀 Viral Auto Klip Studio v6

> **Repo ini adalah hasil konsolidasi**: viral-auto-klip v6.8 dipakai sebagai codebase,
> lalu fitur yang hanya ada di proyek **Clipper** dipindahkan masuk. Detail lengkap ada di
> [docs/CONSOLIDATION.md](docs/CONSOLIDATION.md).

## Yang ditambahkan dari Clipper

| Fitur | Di mana |
|---|---|
| **Campaign / MotionKlip** — facecam + gameplay, banner, judul bernarasi, subtitle karaoke, outro, hashtag | `core/campaign.py`, halaman **Campaign** |
| **Live spike presisi** — rolling-median chat detector + capture `[momen−25s, momen+20s]` | `core/chat_spikes.py`, `core/live_media.py`, `core/live_spike.py` |
| **Template MotionKlip di mode live** (jendela & spike) | `core/live.py` `_render_campaign_window` |
| **Upload YouTube headless** — bypass kuota API, tanpa jendela/popup | `core/youtube_browser.py` |
| **Upload TikTok headless** — Playwright, bukan Selenium | `core/tiktok_browser.py` |
| **Cookies di UI** — tempel, tes sesi, hapus (nilai tidak pernah ditampilkan) | Pengaturan → YouTube / TikTok |
| **Penjaga publish** — cegah upload dobel & salah akun | `core/publish_guard.py` |
| **Standar viral MotionKlip** — 15–45 dtk, headline 5–7 kata, safe zone, watermark 864px | `core/viral_standard.py`, `docs/MAKE_CLIP_SKILL.md` |
| **Batch campaign** — satu job, banyak rentang | halaman Campaign (textarea Batch) |
| **Judul & deskripsi otomatis** — dari transkrip, untuk upload | `_auto_upload_meta()` |

## Hook bergaya (v7)

Teks hook di detik-detik pertama klip kini memakai salah satu dari 19 gaya ala klipers.pro — Breaking Bar, Stabilo,
Stiker Pop, Notifikasi HP, Chat Bubble, Blok Bertumpuk, Coret & Ganti, Mesin Ketik, Editorial, Balas Komentar, Polling,
Stempel Fakta, Postingan X, Angka Besar, Pencarian, Duel VS, Pencapaian, Checklist, Quote (`core/hook_styles.py`).

- **Otomatis** (default): AI memilih gaya sesuai isi klip dan menandai kata terpenting (`*kata*`, bagian dipisah `|`);
  jawaban yang mengarang angka/kejadian ditolak. Tanpa AI, gaya ditebak dari bentuk kalimat & kategori. Gaya antar-klip dijaga beda.
- Dirender Pillow sebagai urutan PNG transparan dan ditimpa di encode utama (tanpa encode tambahan), plus SFX pembuka sesuai gaya.
- Pengaturan → Gaya editing: gaya (Otomatis / Kartu klasik / gaya tetap, dengan pratinjau), lama tampil, efek suara, AI on/off.
- Uji: `python tests/test_hook_styles.py`

## Resep, Auto Pilot, momen spesifik & rentang waktu (v7)

- **Resep** (`core/autopilot.py`): di Analisis AI, "Simpan resep" menyimpan semua opsi form, tanpa link atau file video.
  Maksimal 10 resep; nama yang sama akan menimpa resep lama. Pilih resep untuk mengisi form lagi.
- **Auto Pilot** (menu "Auto Pilot"): ikuti channel YouTube. Channel dicek tiap 15 menit/30 menit/1 jam/3 jam tanpa
  mengunduh apa pun.
  - Video atau siaran baru yang lolos filter langsung diproses dengan resep pilihan. Filternya: jenis (video/live),
    durasi min–maks (default 10–180 menit), kata wajib/dilarang di judul.
  - Siaran yang masih live otomatis masuk Mode Live; siaran terjadwal ditunggu.
  - Hasil: "Tinjau dulu" (klip menunggu di Antrean Job) atau "Langsung upload ke jam FYP".
  - Arsip lama tidak diproses saat channel baru diikuti, kecuali "Proses video terbaru sekarang" dicentang
    (diambil video terbaru yang lolos filter). Maksimal 2 job per channel per cek.
- **Momen spesifik**: isi hingga 20 kata kunci (pisahkan koma). AI wajib mengambil momen yang membahasnya, dan klip
  yang menyebutnya didahulukan.
- **Rentang waktu proses**: "Proses mulai dari / Sampai" (mis. `1:00:00`–`1:30:00`). Video dipotong tanpa encode
  ulang, lalu hanya bagian itu yang ditranskrip, dianalisis, dan dirender.
- Uji: `python tests/test_autopilot.py`
- **Bahasa subtitle** (`core/subtitle_translate.py`): pilih bahasa lain dari bahasa video (15 bahasa). Semua layar caption satu
  klip diterjemahkan AI sekaligus, sehingga konteksnya utuh. Waktu tiap layar tetap; jumlah kata sama → timing kata tetap,
  beda → kata dibagi di dalam layarnya. Bila AI gagal atau jawabannya tidak lengkap, subtitle tetap memakai bahasa asli.
- **Sertakan sumber video**: teks kecil "Sumber: <channel> (YouTube)" di kiri atas (atau teks sendiri). Muncul setelah hook
  dan hilang saat CTA akhir. Uji: `python tests/test_subtitle_translate.py`

## Hook B-roll + suara (v7)

Pembuka alternatif untuk hook teks, setara mode "B-roll + suara" di klipers.pro (`core/hook_broll.py`):

- Video berhenti sebentar (freeze), lalu sampul bergaya muncul. Isinya potongan orang dari klip
  (segmentasi MediaPipe, hanya bagian yang menyambung dengan wajah; di layout gaming hanya panel facecam).
  Gayanya 7: Kolase Stiker, Sampul Majalah, Kertas Sobek, Pop Art Komik, Scrapbook, Arsip Investigasi, Sampul Podcast.
- Narator TTS membacakan judul klip. Durasi intro mengikuti narasi; narasi yang terlalu panjang dibaca lebih cepat
  (maks 1,25×), dan total intro dibatasi `HOOK_BROLL_MAX` (default 7 dtk). Ada SFX whoosh dan dentum.
- Intro disambung di depan klip final, sebisa mungkin tanpa encode ulang. Frame sampul disimpan sebagai `hook_cover.jpg`.
  Burn ulang subtitle live memasang intro lagi.
- Pengaturan → Gaya editing: "Jenis hook pembuka" = B-roll + suara, gaya sampul (Otomatis per kategori / tetap,
  dengan pratinjau), suara narator, dan durasi maks. Saat mode ini aktif, hook teks dan cold open dilewati.
- Uji: `python tests/test_hook_broll.py`

## Preset subtitle bergaya (v7)

37 animasi subtitle setara daftar preset klipers.pro (`core/caption_presets.py`): Tanpa Animasi, Karaoke, Pop, Bounce,
Glitch, Getar, Glow, Bayangan, Fade, Gelombang, Kotak, Motion, Neon Pulse, Gradasi Geser, Ledakan Warna, Bold Impact,
Kedip, Outline Pop, Minimal Clean, Horor Glitch, Candy Pop, Street Bold, Dinamis, Pill Slide, Stabilo, Fokus Blur,
Stiker, Flip 3D, Chat Bubble, Emoji, Gulir, Lingkar Spidol, Slam, Jelly, Live Reaction, Liquid Glass, Word Stack.

- Tiap kata ditempatkan sendiri di file `.ass` memakai metrik font yang sama dengan libass. Karena itu pil bisa bergeser
  ke kata aktif, stabilo dan lingkaran spidol bisa menempel di kata kunci, dan gelembung, kaca, atau kotak per baris
  pas di belakang teks. Semuanya tetap di-burn di encode utama.
- Pilih di "Gaya caption" (tanda ✨) di setiap form. Di bawah pilihan itu muncul pratinjau hasil render libass
  sungguhan (`/api/caption-preview`).
- Pengaturan → Subtitle: font, kapitalisasi (ABC/Abc/abc), kata per layar, warna sorot, dan ukuran.
  Untuk tampilan paling mirip klipers, taruh `Montserrat-ExtraBold.ttf` / `Montserrat-Black.ttf` di folder `fonts/`.
- **Parameter persis klipers:** font, warna teks/sorot/garis, tebal garis, kapitalisasi, dan kata per layar
  diambil dari tabel preset di bundle klipers. Font Google (OFL) ada di `fonts/`; ukuran tampak disamakan antar-font
  (√ tinggi kapital × lebar huruf).
- Gaya lama (Viral, Modern, TikTok Bold, ...) tidak berubah.
- Uji: `python tests/test_caption_presets.py`

## Subtitle live + burn ulang (v7)

Subtitle bisa diedit setelah klip jadi, tanpa render ulang klip (`core/subtitle_live.py`):

- Encode utama menulis dua video sekaligus: klip final, dan `clean.mp4` (klip yang sama tanpa subtitle, video saja).
  Ikut disimpan `subtitle.json` (layar caption + timing per kata, gaya, posisi) dan `live_audio.m4a` di folder klip.
- Di kartu klip, tombol ✎ membuka editor. Di sana kamu bisa ganti teks per layar (kosong = layar dihapus), ganti gaya
  (ada pratinjau), matikan subtitle, atau kembalikan ke versi asli. Setelah "Burn ulang", file final yang sama diganti
  dalam satu encode, ±10–30 detik.
- Hal lain ikut terjaga: tempo klip, audio final, posisi caption khusus layout, dan Brand Kit (dipasang ulang).
  Subtitle sekarang selalu lapisan teratas, jadi burn ulang tanpa perubahan identik dengan aslinya.
- API: `GET /api/subtitle?path=…`, `POST /api/subtitle/reburn {path, style, edits:{indeks: teks}, enabled, reset}`.
- Pengaturan → Subtitle → "Subtitle bisa diedit setelah render" (default aktif; ruang disk ±2x per klip).
- Uji: `python tests/test_subtitle_live.py`

## Menjalankan

Cara termudah: `start.bat` (install dependency ke Python sistem, siapkan FFmpeg & `.env`, lalu buka UI).
Manual (dependency terpasang di Python sistem, bukan di folder `venv\`):

```powershell
python -m uvicorn server:app --host 127.0.0.1 --port 8000
```

Buka **http://127.0.0.1:8000/app**. Hanya host `localhost` / `127.0.0.1` / `[::1]` yang dilayani (perlindungan DNS rebinding).

Server **sengaja hanya listen di 127.0.0.1** — aplikasi ini tidak punya auth, jadi
jangan di-bind ke `0.0.0.0`.

## Test

Semua uji (16 file, tanpa jaringan; render campaign penuh dengan `CAMPAIGN_RENDER_TEST=1`):

```powershell
Get-ChildItem tests\test_*.py | ForEach-Object { python $_.FullName; if ($LASTEXITCODE) { "GAGAL: $($_.Name)" } }
```

Contoh satu per satu:

```powershell
python tests\test_campaign.py
python tests\test_live_media.py         # 24 test buffer HLS (2 uji capture butuh ffmpeg di PATH)
python tests\test_chat_spikes.py        # 13 cek spike detector
python tests\test_caption_trim.py       # caption/judul tidak terpotong di tengah kata
python tests\test_platform_caption.py   # caption per platform (TikTok vs YouTube)
python tests\test_schedule_retry.py     # retry upload tidak mengunggah ulang
python tests\test_preupload.py          # pra-unggah jadwal bawaan platform + jadwal yang filenya hilang
python tests\test_transcript_cache.py   # transkrip dipakai ulang saat "Ulangi" (tanpa bayar ElevenLabs lagi)
python tests\test_ai_gate.py            # gateway AI lokal dibatasi AI_LOCAL_PARALLEL panggilan bersamaan
python tests\test_ai_speed.py           # AI utama lambat → cadangan yang jauh lebih cepat; log progres Editor-AI
python tests\test_job_cancel.py         # tombol Batal menghentikan job di tengah Analisis AI dalam hitungan detik
python tests\test_job_delete_source.py  # hapus job + video sumber (transkrip tetap, video job aktif dilewati)
python tests\test_render_progress.py    # progres "Render n/N" + tombol Batal berlaku saat render
python tests\test_editor_ranking.py     # rubrik live gaming + ranking pembanding S/A/B/C seperti editor manusia
```

## Kebutuhan

`requirements.txt` (vak) + `playwright` (sudah ditambahkan). Setelah install:

```powershell
python -m playwright install chromium
```

ffmpeg: pakai `tools/ffmpeg` bila ada, kalau tidak dari PATH.

### Dependensi opsional (di-import lazy, tidak wajib untuk start)

| Paket | Dibutuhkan untuk |
|---|---|
| `google-api-python-client` + `google-auth-httplib2` | upload YouTube lewat **API** (jalur browser tidak butuh) |
| `tiktok-uploader` | upload TikTok lewat API (jalur browser tidak butuh) |
| `pypdf` | membaca PDF di Transcript / Content Creator |
| `torch` | upscale Real-ESRGAN / face restore (dipasang `start.bat` bila ada GPU) |

Tanpa paket ini aplikasi tetap jalan; hanya fitur tersebut yang memberi pesan "belum terpasang".


> **Baru di v6:** studio konten lengkap di **http://localhost:8000/app**: Analisis AI, Atur Manual, Split Cam, Highlight Bola, Konten Tempel, Content Creator / Story Studio / Motion Graphic, TTS & clone suara, musik/SFX, Editor, Merge, Loop, WA Story, Analitik Viral, Optimasi Judul, Transcript, Auto Upload terjadwal, dan Brand Kit. UI lama: `/legacy`.

**AI-Powered Viral Video Clipper — Inspired by Klipers.pro & OpusClip**

> Paste URL video panjang → AI membaca seluruh transkrip & memilih momen terbaik → klip 9:16 + caption + hook/judul/hashtag buatan AI → upload ke TikTok, YouTube Shorts, Instagram Reels.

## 🆕 v4 — Full upgrade AI

| Upgrade | Detail |
|---|---|
| 🧠 **10 provider AI** | **Anthropic Claude**, **OpenAI GPT**, **Google Gemini**, **Antigravity (login Google, tanpa API key)**, **DeepSeek**, **Mistral**, Groq, OpenRouter, Ollama (lokal), **Custom** (bawa base URL + API key sendiri) |
| 🎯 **AI Moment Finder** | AI membaca *seluruh* transkrip bertimestamp lalu memilih start/end momen sendiri (bukan cuma menilai potongan sliding-window) |
| ✍️ **AI Packaging** | Tiap klip final dapat hook layar, 2 hook A/B, judul YouTube, caption upload & hashtag tulisan AI |
| 🔁 **Fallback berantai** | `AI_FALLBACK_PROVIDERS=Gemini,Groq` — bila provider utama gagal/rate-limit, otomatis pindah |
| 🎙️ **ElevenLabs Scribe** | Transkripsi cloud akurasi tinggi + timestamp per kata (selain Whisper lokal & Groq Whisper) |
| 🎤 **Karaoke sungguhan** | Caption `karaoke` menyala per kata mengikuti timing ucapan |
| 🖼️ **Thumbnail 9:16** | Thumbnail dari klip final (vertikal) + hook text besar + badge skor |
| ⬆️ **Upload tanpa render ulang** | `python cli.py --upload-from output/klip_xxx --upload-youtube --upload-tiktok` + tombol upload di UI |
| 🔍 **Deteksi model** | Tombol "Deteksi" mengambil daftar model yang tersedia untuk API key kamu (semua provider) |
| ✨ **Pro editing** | Jump cut (buang jeda & "eh/em"), zoom dinamis + punch-in, caption kata-aktif, color grade, progress bar, whoosh SFX, thumbnail pintar + cover B, CTA akhir — preset Minimal / Dinamis / Maksimal |
| 🐛 Bug fix | Semua temuan audit diperbaiki |

### Pilih provider AI

| Provider | Isi di `.env` | Model default | Catatan |
|---|---|---|---|
| Anthropic (Claude) | `ANTHROPIC_API_KEY` | `claude-opus-5-5` | Opsi lain: `claude-sonnet-5-5`, `claude-haiku-5-5`, `claude-fable-5-2`. `ANTHROPIC_EFFORT` = low/medium/high/xhigh/max |
| OpenAI | `OPENAI_API_KEY` | `gpt-5.2` | Model reasoning ditangani otomatis |
| Gemini | `GEMINI_API_KEY` | `gemini-2.5-flash` | |
| Antigravity | `GOOGLE_AI_CLIENT_SECRET_FILE` | `gemini-2.5-flash` | Login akun Google (OAuth) — lihat di bawah |
| DeepSeek | `DEEPSEEK_API_KEY` | `deepseek-chat` | |
| Mistral | `MISTRAL_API_KEY` | `mistral-large-latest` | |
| Groq | `GROQ_API_KEY` | `llama-3.3-70b-versatile` | Tercepat, ada tier gratis |
| OpenRouter | `OPENROUTER_API_KEY` | `openrouter/free` | Ratusan model, ketik nama model sendiri |
| Ollama | `OLLAMA_BASE_URL` | `qwen2.5:7b` | 100% lokal |
| Custom | `CUSTOM_AI_BASE_URL` + `CUSTOM_AI_API_KEY` + `CUSTOM_AI_MODEL` | — | Endpoint OpenAI-compatible apa pun (vLLM, LM Studio, Together, proxy kantor, …) |

Paling mudah: buka **http://localhost:8000/app#settings**, tempel API key, klik **💾 Simpan ke .env** — langsung berlaku tanpa restart. Atau edit `.env` manual lalu set `AI_PROVIDER=<nama>`. Cek semua status: `python cli.py --list-providers`. Tes satu provider: `python cli.py --test-ai --ai-provider Anthropic`.

**Antigravity (login Google):** buat OAuth client tipe *Desktop app* di console.cloud.google.com, aktifkan *Generative Language API*, simpan `client_secret.json`, isi `GOOGLE_AI_CLIENT_SECRET_FILE`, lalu `python cli.py --google-login` (atau tombol 🔐 di UI). Token tersimpan lokal di `.google_ai_token.json` (di-gitignore). Ini memakai OAuth client milikmu sendiri ke Gemini API resmi — bukan backend privat aplikasi Antigravity.

> 🔒 Privasi: bila AI aktif, **transkrip** dikirim ke provider terpilih; mode Groq/ElevenLabs mengirim **audio**. Mode lokal penuh: Whisper lokal + Ollama.

![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python)
![FFmpeg](https://img.shields.io/badge/FFmpeg-required-green?logo=ffmpeg)
![License](https://img.shields.io/badge/License-MIT-yellow)
![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)
![Version](https://img.shields.io/badge/Version-3.0-purple)

---

## 📌 Daftar Isi

- [Apa yang Baru di v3](#-apa-yang-baru-di-v3)
- [Fitur Utama](#-fitur-utama)
- [Cara Kerja AI](#-cara-kerja-ai)
- [Prasyarat](#-prasyarat)
- [Instalasi](#️-instalasi)
- [Konfigurasi API Key](#-konfigurasi-api-key-opsional-tapi-direkomendasikan)
- [Cara Menjalankan](#-cara-menjalankan)
- [Paket Upload & Auto-Upload](#-paket-upload--auto-upload-v3)
- [Parameter & Output](#-parameter--output)
- [Advanced Usage](#-advanced-usage)
- [FAQ & Troubleshooting](#-faq--troubleshooting)
- [Kontribusi](#-kontribusi)

---

## ✨ Apa yang Baru di v3

| Upgrade | Detail |
|---|---|
| 🎬 **Scene Snap** | Batas klip otomatis di-snap ke scene cut (PySceneDetect) — potongan tidak lagi patah di tengah adegan |
| 🖼️ **Peak Thumbnail** | Thumbnail diambil di momen paling "panas" (kata kunci viral), bukan asal tengah klip |
| 💥 **Hook Punch Zoom** | Zoom-in punch halus 0.8 detik pertama klip (digabung bumper intro, tanpa encode tambahan) |
| 😂 **Emoji Captions** | Style caption baru "emoji" + pemetaan kata kunci → emoji otomatis |
| ⚡ **Parallel Rendering** | Klip dirender paralel (ThreadPoolExecutor) sesuai `PARALLEL_CLIPS` |
| 🪟 **start.bat Tanpa Venv** | One-click DIRECT RUN: install & jalan langsung ke Python sistem (tanpa venv), auto FFmpeg + auto .env |
| 🧩 **MediaPipe Compat Layer** | Dukung mediapipe lama (`mp.solutions`) DAN baru 0.10.30+ (Tasks API, Python 3.14) — auto-download model kecil |
| 📤 **Publisher Module** | `core/publisher.py`: paket publish per platform, rencana posting, jam upload terbaik, varian caption A/B test |
| ⬆️ **Auto-Upload YouTube** | Upload otomatis ke YouTube Shorts via YouTube Data API v3 (OAuth) — `python cli.py --upload-youtube` |
| 🎵 **TikTok Auto-Upload** | Lewat `tiktok-uploader` bila terpasang; fallback panduan manual |
| 🐛 **Semua bug v2 diperbaiki** | — |

---

## ✨ Fitur Utama

| Fitur | Detail |
|---|---|
| 🌐 **Multi-platform URL** | YouTube, TikTok, Instagram Reels, Facebook, X/Twitter, direct MP4/MOV |
| 🤖 **AI Viral Detection** | Score 0–100 per segmen: lucu, motivasi, shocking, emotional, hook (rule-based + LLM rerank 42%) |
| 👤 **Face Reframing** | MediaPipe auto-follow wajah utama, crop cerdas ke 9:16 seperti OpusClip |
| 📝 **Auto-Captions** | Whisper timestamps → burn-in caption modern (pop-up, karaoke, emoji, minimal, neon, gradient, tiktok_bold) |
| 🎬 **Smart Clipping** | Top-N klip non-overlapping + scene snap + diversity ranking kategori |
| 📤 **Output Viral-Ready** | 1080×1920 H.264/AAC + metadata JSON + thumbnail peak-moment + publish package |
| 🔁 **Batch Processing** | *(belum diimplementasikan — jalankan per video)* |
| 🔒 **Local & Private** | Whisper jalan di lokal. Pengecualian: mode Groq Whisper Cloud (audio dikirim ke Groq), AI scoring (transkrip dikirim ke provider), engine Clipping.software (URL dikirim) |
| 💬 **Bahasa Indonesia First** | Keyword scoring + LLM prompt + initial prompt transkripsi support Bahasa Indonesia optimal |

---

## 🧠 Cara Kerja AI

```
URL Video
    │
    ▼
[yt-dlp] Download video + audio (high quality, fallback pytubefix)
    │
    ▼
[Whisper] Transkripsi akurat → timestamp per kata/segmen (faster-whisper / Groq cloud)
    │
    ▼
[PySceneDetect] Deteksi scene change alami → snap batas klip
    │
    ▼
[MediaPipe] Deteksi wajah utama + bounding box per frame
    │
    ▼
[Viral Scoring Engine]
    ├── Rule-based: keyword viral (word-boundary), sentiment, hook, kelengkapan momen
    └── AI LLM rerank (opsional): Groq / OpenAI / Gemini / OpenRouter / Ollama
    │
    ▼
[Selection] Top-N skor, non-overlap, kategori beragam
    │
    ▼
[FFmpeg] Extract → Reframe 9:16 → Punch zoom → Caption burn-in → Encode (NVENC/libx264)
    │
    ▼
[Publisher] Paket publish per platform + rencana posting + (opsional) auto-upload YouTube
```

---

## 📋 Prasyarat

- **Python 3.10+** (64-bit) — [python.org](https://www.python.org/downloads/), **teruji hingga 3.14**
- **FFmpeg** — **WAJIB** untuk proses video (start.bat mendownload otomatis ke `ffmpeg\bin`)
- **Git** — untuk clone repo ([git-scm.com](https://git-scm.com/))
- GPU NVIDIA (opsional, mempercepat Whisper & render NVENC)

---

## 🛠️ Instalasi

### Windows (paling mudah)

```cmd
git clone https://github.com/amrabdllh/viral-auto-klip.git
cd viral-auto-klip
start.bat            ← TANPA VENV: langsung install + jalankan (FFmpeg otomatis)
```

> `start.bat 1` = Backend + HTML UI (default), `start.bat 2` = Streamlit UI.
> Tidak butuh venv — semua dependency di-install langsung ke Python sistem.

### Manual (Windows / Linux / macOS)

```bash
python -m venv venv
venv\Scripts\activate        # Windows  (Linux/macOS: source venv/bin/activate)
pip install -r requirements.txt
```

FFmpeg harus ada di PATH (Linux: `sudo apt install ffmpeg`; macOS: `brew install ffmpeg`).

---

## 🔑 Konfigurasi API Key (Opsional tapi Direkomendasikan)

```bash
cp .env.example .env   # Windows: copy .env.example .env
```

Isi `.env`:

```env
AI_PROVIDER=Anthropic                 # atau Groq | OpenAI | Gemini | Antigravity | DeepSeek | Mistral | OpenRouter | Ollama | Custom
ANTHROPIC_API_KEY=sk-ant-...          # isi key provider yang dipakai saja
LLM_MODEL=                            # kosong = model default provider
AI_FALLBACK_PROVIDERS=Groq            # opsional: cadangan otomatis
```

Daftar lengkap variabel ada di `.env.example`.

> ⚠️ **Jangan pernah commit file `.env` atau `.yt_upload_token.json` ke GitHub!**

---

## ▶️ Cara Menjalankan

### 1. Web UI (Streamlit) — Paling Lengkap

```bash
streamlit run app.py          # http://localhost:8501
```

### 2. Web UI (HTML + Server)

```bash
uvicorn server:app --reload --port 8000
# lalu buka viral_auto_klip_v2_real.html di browser
```

### 3. CLI (Command Line Interface)

```bash
# Basic — proses 1 video, ambil 8 klip terbaik + paket publish
python cli.py --url "https://youtube.com/watch?v=XXXX" --publish-package

# Lengkap
python cli.py \
  --url "https://youtube.com/watch?v=XXXX" \
  --num-clips 10 --min-score 70 --min-duration 15 --max-duration 50 \
  --face-reframe --caption-style emoji --target-platform TikTok \
  --publish-package --parallel 2 --scene-snap auto

# File lokal
python cli.py --file "D:\video\podcast.mp4" --num-clips 5
```

> Catatan: opsi `--playlist` (batch playlist) **belum diimplementasikan** di `cli.py`. Untuk batch, jalankan CLI per video.

---

## 📤 Paket Upload & Auto-Upload (v3)

### Paket publish (otomatis / `--publish-package`)

```
output/klip_<timestamp>/publish/
├── video/                         ← semua mp4 + thumbnail siap upload
├── TikTok/upload_captions.json
├── YouTube Shorts/upload_captions.json
├── Instagram Reels/upload_captions.json
├── publish_plan.json              ← urutan upload + A/B test variants
└── README.txt                     ← panduan upload manual
```

Setiap `upload_captions.json` berisi title, caption, hashtag, jam upload terbaik per niche, dan 3 varian caption A/B test.

### Auto-upload ke YouTube Shorts

```bash
pip install google-api-python-client google-auth-oauthlib google-auth-httplib2
# Buat OAuth client (Desktop) di console.cloud.google.com → enable YouTube Data API v3
# Upload klip yang SUDAH dirender (tidak memproses ulang video):
python cli.py --upload-from output/klip_20261118_120000 --upload-youtube --youtube-client-secret client_secret.json --upload-limit 3
```

Atau dari Python:

```python
from core.publisher import upload_to_youtube, upload_to_tiktok
result = upload_to_youtube("clip.mp4", title="...", description="...", tags=["#fyp"])
```

### TikTok

- Otomatis bila `tiktok-uploader` terpasang: `pip install tiktok-uploader`, export cookies tiktok.com ke `cookies.txt`, isi `TIKTOK_COOKIES_FILE`, lalu `python cli.py --upload-from <folder> --upload-tiktok`.
- Tanpa itu: pakai folder `publish/` + `upload_captions.json` untuk upload manual (2 menit).

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
| `--caption-style` | modern | `modern` / `minimal` / `karaoke` / `emoji` / `tiktok_bold` / `gradient` / `neon` |
| `--target-platform` | Semua | `TikTok` / `Instagram Reels` / `YouTube Shorts` / `Semua` |
| `--whisper-model` | large-v3-turbo (dari `.env`) | `tiny` / `base` / `small` / `medium` / `large-v3-turbo` / `large-v3` |
| `--use-llm` | off | Aktifkan AI: moment finder + rerank + packaging |
| `--ai-provider` | `.env` | `Groq` `OpenAI` `Anthropic` `Gemini` `Antigravity` `DeepSeek` `Mistral` `OpenRouter` `Ollama` `Custom` |
| `--ai-model` / `--ai-api-key` / `--ai-base-url` | — | Model, key & base URL untuk provider terpilih |
| `--transcriber` | local | `local` / `groq` / `elevenlabs` |
| `--language` | otomatis | Kode bahasa video (`id`, `en`, …) |
| `--no-captions` | off | Render tanpa caption |
| `--edit-style` | dinamis | `minimal` / `dinamis` / `maksimal` |
| `--color-grade` | preset | `none` / `natural` / `vibrant` / `cinematic` |
| `--no-jump-cuts` | off | Jangan buang jeda & kata pengisi |
| `--detect-models` | — | Daftar model yang tersedia untuk API key provider |
| `--upload-from <folder>` | — | Upload klip yang sudah jadi (pakai dengan `--upload-youtube` / `--upload-tiktok`) |
| `--list-providers` / `--test-ai` / `--google-login` | — | Status provider / tes koneksi / login Antigravity |
| `--publish-package` | off | Buat paket upload + rencana posting |
| `--upload-youtube` | off | Auto-upload ke YouTube Shorts |
| `--scene-snap` | auto | Snap batas klip ke scene cut (`auto`/`on`/`off`) |
| `--parallel` | 2 | Klip dirender paralel (0/1 = sekuensial) |

### Struktur Output

```
output/
└── klip_YYYYMMDD_HHMMSS/          (CLI/Streamlit)  atau  job_xxxxxxxx/ (server)
    ├── clip_001_viral85_humor/
    │   ├── clip_001_viral85_humor_final.mp4  ← hasil akhir (nama akhiran tergantung fitur aktif)
    │   ├── captions.ass                      ← subtitle
    │   └── thumbnail.jpg                     ← thumbnail peak moment
    ├── ...
    ├── metadata.json                         ← detail semua klip + strategi upload
    └── publish/                              ← paket upload per platform
```

---

## 🔧 Advanced Usage

### Edit Config

Buka `config.py` untuk kustomisasi:
- Viral keywords list & `EMOJI_KEYWORDS` (tambah kata niche kamu)
- Bobot scoring & `LLM_RERANK_WEIGHT`
- `SCENE_SNAP_TOLERANCE`, `ENABLE_HOOK_PUNCH`, `PEAK_THUMBNAIL`, `PARALLEL_CLIPS`
- Font caption, posisi, gaya caption
- Publisher: `YOUTUBE_PRIVACY`, `TIKTOK_UPLOADER_PATH`

### Tips Biar Lebih Viral

- Gunakan `--min-score 70+` untuk kualitas tinggi
- Pilih video dengan hook kuat di awal (pertanyaan, bold statement)
- Aktifkan `--face-reframe` untuk talking head / podcast
- Test A/B: pakai 3 varian caption dari `publish_plan.json`, upload di jam berbeda (lihat `get_best_upload_schedule`)

---

## ❓ FAQ & Troubleshooting

**Q: Apakah butuh GPU?**  
A: Tidak wajib. CPU cukup untuk video di bawah 1 jam. Gunakan `--whisper-model medium` untuk keseimbangan kecepatan & akurasi.

**Q: Error `No module named 'faster_whisper'`?**  
A: Jalankan: `pip install -U faster-whisper` (v4 tidak lagi memakai `openai-whisper`).

**Q: AI tidak jalan / "belum dikonfigurasi"?**  
A: `python cli.py --list-providers` untuk melihat provider yang siap. Nilai contoh seperti `your_..._here` / `gsk...` dianggap kosong. Tes: `python cli.py --test-ai --ai-provider <nama>`.

**Q: Error `ffmpeg not found`?**  
A: Pastikan FFmpeg di PATH (`ffmpeg -version`). start.bat menyediakan FFmpeg lokal otomatis.

**Q: Scene snap tidak jalan?**  
A: Pastikan `scenedetect[opencv]` terpasang (`pip install "scenedetect[opencv]"`). Fitur ini fallback diam-diam bila tidak tersedia.

**Q: `mediapipe` error di Python 3.12?**  
A: Coba: `pip install mediapipe==0.10.14 --no-deps` lalu install dependensi manual jika perlu.

**Q: Upload YouTube gagal?**  
A: Pastikan OAuth client bertipe Desktop + YouTube Data API v3 aktif. Token tersimpan di `.yt_upload_token.json` (hapus file itu untuk login ulang).

**Q: Berapa biayanya?**  
A: Gratis 100% untuk mode lokal. Groq ~$0.01 per video panjang. OpenAI/Gemini sesuai harga masing-masing.

---

## 📁 Struktur Project

```
viral-auto-klip/
├── app.py                       ← Streamlit web UI
├── server.py                    ← FastAPI backend v3 (untuk HTML UI)
├── cli.py                       ← Command line interface (publish + auto-upload)
├── config.py                    ← Konfigurasi global v3
├── requirements.txt             ← Dependensi Python (+ opsional publisher)
├── .env.example                 ← Template environment variables v3
├── viral_auto_klip_v2_real.html ← UI alternatif (HTML, v3-fixed)
├── start.bat                    ← One click setup Windows v3
├── core/
│   ├── ai_providers.py          ← NEW v4: 10 provider AI, fallback berantai, login Google
│   ├── analyzer.py              ← AI moment finder + scoring + rerank + AI packaging
│   ├── clipper.py               ← Clipping & encoding (paralel, scene snap, punch zoom)
│   ├── transcriber.py           ← Whisper transcription (beam/batch/initial prompt)
│   ├── publisher.py             ← NEW v3: publish package + auto-upload
│   ├── mediapipe_compat.py      ← NEW v3: legacy mp.solutions + Tasks API (Python 3.14)
│   ├── fyp_agent.py             ← FYP copy, jadwal upload, A/B variants
│   ├── audio_enhancer.py        ← Audio processing
│   ├── utils.py                 ← Utility (emoji captions, ASS subtitle)
│   └── clipping_software.py     ← Clipping software integration (opsional)
├── fonts/                       ← Font untuk caption
├── models/                      ← Model MediaPipe kecil (auto-download, di-gitignore)
├── mentahan/                    ← Source video download (di-gitignore)
├── temp/                        ← File sementara (auto-clean)
└── output/                      ← Hasil klip (dibuat otomatis)
```

---

## 🤝 Kontribusi

1. Fork repo ini
2. Buat branch baru: `git checkout -b fitur-baru`
3. Commit perubahan: `git commit -m "Tambah fitur X"`
4. Push & buat Pull Request

---

## 📄 Lisensi

MIT License — bebas digunakan, dimodifikasi, dan didistribusikan dengan menyebut sumber.

---

**Dibuat dengan ❤️ untuk creator Indonesia yang mau scaling konten viral tanpa ribet editing!**

*Version 4.0 — November 2026*
