# CHANGELOG v3 — Viral Auto Klip

Perbaikan bug + upgrade besar-besaran dari v2. Semua perubahan disimpan dalam proyek ini.

---

## 🐛 BUG YANG DIPERBAIKI

| # | File | Bug | Perbaikan |
|---|---|---|---|
| 1 | `server.py` | HTML UI memanggil `GET /download/{job_id}/{i}` tapi endpoint tidak ada → tombol download selalu 404 | Ditambah endpoint `/download/{job_id}/{clip_index}` + `/download/.../thumbnail` (FileResponse) |
| 2 | `server.py` | `configure_llm_clients(req.groq_api_key or "", req.openai_api_key or "")` — argumen posisi salah (terbaca sebagai provider/model), API key tidak pernah terpakai | Dipanggil dengan keyword args `provider=`, `model=`, `groq_api_key=`, `openai_api_key=` yang benar |
| 3 | `server.py` | Pipeline job `async def` berisi kerja berat sinkron → memblokir event loop, polling status macet | Diubah jadi fungsi sync → `BackgroundTasks` menjalankannya di threadpool |
| 4 | `server.py` | `req.dict()` deprecated di Pydantic v2 | Diganti `req.model_dump()` |
| 5 | `server.py` | CORS `allow_origins=["*"]` + `allow_credentials=True` — kombinasi yang ditolak browser | `allow_credentials=False` |
| 6 | `server.py` | Job store in-memory tanpa batas → bocor memori | Auto-prune job > 24 jam + endpoint `GET /jobs` |
| 7 | `viral_auto_klip_v2_real.html` | `showRealResults` memakai field `clip.filename`/`clip.title` yang tidak ada di payload → tampil `undefined` | Pakai `clip.file_path` (basename) + `clip.reason`, render pakai DOM API (aman dari injeksi teks) |
| 8 | HTML | `detectPlatform()` stub kosong | Implementasi deteksi platform (YouTube/TikTok/IG/FB/X/MP4) + tips per platform |
| 9 | HTML | Tab Setup berisi path lama `/home/workdir/artifacts/...` | Diganti instruksi Windows `E:\projek\viral-auto-klip` + start.bat |
| 10 | `analyzer.py` | Pencocokan keyword pakai substring (`"cara" in text` cocok dengan "acara") → false positive | Word-boundary regex `(?<![a-z0-9])kata(?![a-z0-9])` |
| 11 | `analyzer.py` | `SCORING_WEIGHTS` berjumlah >100 saat LLM aktif (llm_score 15 ditambahkan aditif lalu dinormalisasi) | Bobot dasar = 100; LLM dipakai murni sebagai **rerank** `final = dasar×0.58 + LLM×0.42` (bobot bisa diatur via `LLM_RERANK_WEIGHT`) |
| 12 | `analyzer.py` | Parse JSON LLM gagal bila model membungkus jawaban dengan ```json fences | `_parse_json_object` strip markdown fences sebelum parse |
| 13 | `analyzer.py` | `textblob` diimport tapi tidak dipakai | Import dihapus |
| 14 | `clipper.py` | `moviepy` diimport tapi tidak dipakai (moviepy 1.0.3 juga bermasalah dengan numpy baru) | Import dihapus |
| 15 | `clipper.py` | Smoke test NVENC menulis ke `-f null` (file `temp/nvenc_smoke.mp4` 0-byte sampah ikut ter-commit) | Smoke test menulis ke file temp lalu dihapus otomatis; file sampah di repo dihapus |
| 16 | `config.py` | HTML menawarkan caption style "emoji" tapi config tidak punya → jatuh ke style default | Style `emoji` ditambahkan + pemetaan `EMOJI_KEYWORDS` |
| 17 | `cli.py` | `--caption-style` hanya 3 pilihan padahal config punya 6+ | Choices diperluas ke 7 style |

---

## 🚀 UPGRADE BESAR v3

### 1. Editing video lebih pintar
- **Scene Snap** (`clipper.py`): batas klip di-snap ke scene cut PySceneDetect (toleransi 1.5 dtk) — potongan tidak patah di tengah adegan. Fallback diam-diam bila library tidak ada.
- **Peak-Moment Thumbnail**: thumbnail diambil pada kata paling "panas" (kata kunci viral), bukan tengah klip.
- **Hook Punch Zoom**: zoom-in 1.10→1.0 pada 0.8 detik pertama, digabung ke bumper intro (tanpa encode tambahan), via `zoompan`.
- **Emoji Captions**: style caption baru + emoji otomatis per baris berdasarkan `EMOJI_KEYWORDS`.
- **Parallel Rendering**: klip diproses paralel dengan `ThreadPoolExecutor` sesuai `PARALLEL_CLIPS` (default 2, bisa `--parallel 1` untuk sekuensial).

### 2. Publisher & auto-upload (modul baru `core/publisher.py`)
- `build_publish_package()`: folder `publish/` berisi video, thumbnail, `upload_captions.json` per platform, `publish_plan.json` (urutan upload, jam terbaik per niche, varian A/B test), dan `README.txt`.
- `upload_to_youtube()`: auto-upload YouTube Shorts via YouTube Data API v3 (OAuth desktop, token disimpan lokal di `.yt_upload_token.json`, resume upload).
- `upload_to_tiktok()`: lewat `tiktok-uploader` bila terpasang; fallback panduan manual.
- Wire ke CLI (`--publish-package`, `--upload-youtube`, `--youtube-client-secret`), server (paket publish otomatis), dan Streamlit (checkbox + download ZIP paket publish).

### 3. Scoring AI lebih jujur
- Keyword matching word-boundary + bonus `keyword_in_opening` (kata kunci di 25% awal segmen).
- LLM rerank murni (bukan bonus kecil), `LLM_RERANK_WEIGHT` bisa diatur.
- Parse JSON LLM tahan markdown fences.

### 4. Transkripsi lebih baik
- `WHISPER_BEAM_SIZE`, `WHISPER_BATCH_SIZE` (CUDA), `WHISPER_INITIAL_PROMPT` (tanda baca Bahasa Indonesia lebih baik).
- Semua percobaan transkripsi (CUDA → CPU → model kecil → Groq cloud) tetap dipertahankan.

### 5. Backend & UI
- Endpoint download + thumbnail + jobs + health (`GET /`).
- HTML UI: stages progress real-time, badge deteksi platform, backend URL bisa diubah & disimpan, metadata download, publish folder, caption per platform bisa dilihat langsung per kartu klip.
- `start.bat` v3: menu pilih mode (Backend+HTML / Streamlit), auto-create `.env` dari template.

### 6. Dokumentasi
- README v3 lengkap + CHANGELOG ini.
- `.env.example` + `requirements.txt` diperbarui (dependensi publisher opsional dikomentari agar instalasi dasar tetap ringan).

---

## 🧪 CATATAN KOMPATIBILITAS

- Semua fitur baru punya **fallback otomatis** (scene snap, punch zoom, NVENC, publisher) — bila dependensi/encoder tidak tersedia, pipeline lama tetap jalan.
- Dependensi opsional publisher: `google-api-python-client`, `google-auth-oauthlib`, `google-auth-httplib2` (YouTube); `tiktok-uploader` (TikTok).

---

## 🔧 PATCH TES NYATA (start.bat tanpa venv + Python 3.14)

Dites langsung di mesin ini (Python 3.14.8, tanpa venv) — semua jalur diperbaiki sampai server benar-benar hidup.

### start.bat — ditulis ulang jadi DIRECT RUN (tanpa venv)
- Tidak lagi membuat/aktivasi venv: install & jalankan langsung ke Python sistem.
- **FIX bug parse batch**: `%PATH%` berisi `C:\Program Files (x86)\NVIDIA...` — kurung `(x86)` mematahkan blok `if (...)` saat di-expand di dalamnya (error nyata: `\NVIDIA was unexpected at this time`). Alur ditulis ulang dengan `goto`/label, tanpa blok kurung bersarang, dan `set "PATH=..."` dengan kutipan.
- **FIX**: `uvicorn`/`streamlit` dari pip user-site tidak ada di PATH → dipanggil via `python -m uvicorn` / `python -m streamlit`.
- **FIX**: langkah FFmpeg — `move` dengan wildcard gagal diam-diam (folder tujuan tidak dibuat, hasil ekstrak dihapus, pesan sukses palsu). Sekarang: buat folder `ffmpeg`, loop `for /d`, verifikasi `ffmpeg.exe`, warning jujur bila gagal.
- Mode dipilih via argumen: `start.bat 1` (backend + HTML, default) atau `start.bat 2` (Streamlit).

### Kompatibilitas Python 3.14 & mediapipe 0.10.35
- `mediapipe==0.10.14` tidak punya wheel Python 3.14 → requirements jadi `mediapipe>=0.10.14,<1.0` (terpasang 0.10.35).
- `scenedetect` 0.7 menghapus extra `[opencv]` → di-pin `scenedetect[opencv]>=0.6.4,<0.7` (0.6.7.1).
- mediapipe ≥0.10.30 **menghapus** API legacy `mp.solutions` → modul baru `core/mediapipe_compat.py`: otomatis pilih legacy `mp.solutions` (mediapipe lama) atau Tasks API (`mp.tasks.vision.FaceDetector` / `ImageSegmenter`), plus auto-download model kecil (~230–250 KB) ke `models/mediapipe/`. `clipper.py` dimigrasi ke layer ini.
- `mp.Image` di 0.10.35 ada di top-level `mediapipe.Image` (bukan `mediapipe.tasks.python.Image`) — sudah diakomodasi.
- Mask segmentasi Tasks API berbentuk (H,W,1) → di-squeeze ke 2D agar kompatibel dengan pipeline lama.
- `models/` + `mentahan/` ditambahkan ke `.gitignore`.

### Tes nyata yang sudah dijalankan di mesin ini
- `pip install -r requirements.txt` — **SUKSES** di Python 3.14.8 (torch 2.15, opencv 5.1, ctranslate2 4.9, dll).
- `py_compile` 13/13 file Python — OK.
- Smoke test FaceDetector Tasks API dengan **foto wajah asli** — 1 wajah terdeteksi (score 0.92, koordinat piksel benar).
- Smoke test SelfieSegmenter — mask (H,W) float32 rentang 0–1 benar.
- `start.bat 1` end-to-end — pip OK → FFmpeg 9.0.2 terpasang di `ffmpeg\bin` → `.env` dibuat → uvicorn **"Application startup complete"**.
- API live dicek: `GET http://localhost:8000/` → `{"status":"ok","service":"Viral Auto Klip API","version":"3.0"}`; `GET /jobs` → `[]`.
- FFmpeg fungsional: berhasil render frame uji (203 bytes PNG).

---

## 🩹 PATCH AUDIT 2026-11-18 (detail lengkap di [AUDIT_LENGKAP_v3.md](AUDIT_LENGKAP_v3.md))

- **transcriber**: `batch_size` dipindah ke `BatchedInferencePipeline` — sebelumnya semua percobaan CUDA gagal (TypeError) dan selalu jatuh ke CPU.
- **clipper**: intro bumper + punch zoom kini benar-benar jalan (teks via `textfile` + `expansion=none`; dulu `"Score: 80"` mematahkan filter); split screen & crop auto-zoom tidak melar lagi; file antara per klip dibersihkan.
- **audio_enhancer**: ducking musik jalan (`makeup` 0.35 → 1, di luar rentang 1–64); output 48 kHz.
- **utils**: warna caption per style (neon/karaoke/gradient) kini dipakai.
- **analyzer**: LLM gagal tidak lagi menarik skor ke 50; `detect_niche` & sapaan langsung pakai word-boundary.
- **HTML**: CSS modal & toast ditambahkan; toast pakai `textContent`; tab Setup tanpa venv.
- **app.py**: hasil tidak crash bila file klip hilang. **.gitignore**: `ffmpeg/` + token OAuth. **README**: opsi CLI disesuaikan dengan kode.
