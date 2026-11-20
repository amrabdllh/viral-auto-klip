# 📋 Rangkuman Lengkap Isi Semua File — Viral Auto Klip

> ⚠️ **CATATAN**: Dokumen ini mendeskripsikan kondisi proyek **SEBELUM upgrade v3** (17 Nov 2026, pra-fix bug).
> Kondisi terbaru setelah perbaikan bug & upgrade besar ada di [CHANGELOG_V3.md](CHANGELOG_V3.md) dan [README.md](README.md).
> Audit terbaru (kondisi v3, 18 Nov 2026) ada di [AUDIT_LENGKAP_v3.md](AUDIT_LENGKAP_v3.md).

> Hasil pembacaan menyeluruh (body penuh, bukan hanya head) terhadap seluruh file di `E:\projek\viral-auto-klip`.
> Tanggal pembacaan: 2026-11-17. Dokumen ini disimpan di dalam dir proyek sesuai instruksi.

---

## 🗂️ Peta File

### Folder Root

| File | Ukuran | Baris | Peran |
|---|---|---|---|
| `README.md` | 11.4 KB | 375 | Dokumentasi utama proyek |
| `requirements.txt` | 463 B | 24 | Daftar dependensi Python |
| `.env.example` | 931 B | 31 | Template environment variables |
| `.gitignore` | 403 B | 42 | Daftar file yang tidak di-commit git |
| `config.py` | 11.8 KB | 312 | Konfigurasi global (scoring, caption, AI, output) |
| `app.py` | 29.2 KB | 654 | Web UI Streamlit |
| `server.py` | 7.4 KB | 214 | Backend FastAPI (untuk HTML UI) |
| `cli.py` | 7.6 KB | 155 | Command Line Interface |
| `start.bat` | 1.8 KB | 62 | One-click setup Windows |
| `viral_auto_klip_v2_real.html` | 41.1 KB | 538 | UI alternatif berbasis HTML + JS |

### Folder `core/` (modul inti)

| File | Ukuran | Baris | Peran |
|---|---|---|---|
| `analyzer.py` | 30.3 KB | 787 | Mesin deteksi & scoring viral |
| `clipper.py` | 50.3 KB | 1229 | Clipping, face reframe, caption, encode (file terbesar) |
| `transcriber.py` | 18.5 KB | 507 | Transkripsi Whisper (faster-whisper + Groq) |
| `utils.py` | 14.6 KB | 358 | Utilitas: download, ffprobe, thumbnail, subtitle ASS |
| `fyp_agent.py` | 12.7 KB | 298 | Packaging FYP: copy platform, jadwal upload, grading |
| `audio_enhancer.py` | 4.1 KB | 118 | Audio enhancement (noise reduction, bass boost, ducking) |
| `clipping_software.py` | 2.9 KB | 92 | Integrasi opsional ke API eksternal clipping.software |

### Folder lain

| Path | Isi |
|---|---|
| `temp/nvenc_smoke.mp4` | **0 byte** (kosong, MD5 = D41D8CD9…, hanya placeholder) |
| `core/__pycache__/*.pyc` | Cache bytecode Python hasil kompilasi (Python 3.12), bukan source code |
| `.git/` | Metadata git (config, pack, hooks, refs) — hasil clone, bukan bagian kode proyek |

---

## 📄 Isi Detail Per File

### 1. `README.md` (375 baris)
- **Judul**: "AI-Powered Viral Video Clipper — Inspired by Klipers.pro & OpusClip"
- **Alur kerja AI** (bagian "Cara Kerja AI"): URL → yt-dlp download → Whisper transkripsi + timestamp → PySceneDetect deteksi scene → MediaPipe deteksi wajah → Viral Scoring Engine (rule-based + LLM opsional: Groq/OpenAI/Gemini/Ollama) → seleksi top-N non-overlap → FFmpeg extract + reframe 9:16 + burn-in caption → output MP4 + metadata.json + thumbnail.
- **Fitur utama**: multi-platform URL (YouTube, TikTok, IG, FB, X, MP4 langsung), AI viral detection score 0–100, face reframing ala OpusClip, auto-caption Whisper (pop-up, karaoke, emoji, minimal), smart clipping 15–60 detik, output 1080×1920 H.264/AAC, batch processing playlist/channel, lokal & privat, Bahasa Indonesia first.
- **Prasyarat**: Python 3.10+ 64-bit, FFmpeg (WAJIB), Git; GPU NVIDIA opsional.
- **3 cara menjalankan**: (1) `streamlit run app.py` di port 8501, (2) `uvicorn server:app --reload --port 8000` + buka HTML, (3) CLI `python cli.py --url ...`.
- **Parameter CLI**: `--num-clips` (default 8), `--min-score` (60), `--min-duration` (15), `--max-duration` (60), `--face-reframe`, `--captions-style` (modern/minimal/karaoke/emoji), `--platform`, `--whisper-model` (medium default di README).
- **Struktur output**: `output/nama_video/clip_XXX_viralNN_kategori.mp4` + `metadata.json` + folder `thumbnails/`.
- **FAQ**: GPU tidak wajib; `No module named 'whisper'` → `pip install -U openai-whisper`; mediapipe error Python 3.12 → `pip install mediapipe==0.10.14 --no-deps`; belum ada auto-upload ke TikTok/IG.
- Lisensi MIT, versi 2.0 April 2026.

### 2. `requirements.txt` (24 baris)
Dependensi kunci:
- Download: `yt-dlp>=2024.12.0`, `pytubefix>=8.12.0` (fallback downloader)
- Transkripsi: `openai-whisper>=20231117`, `faster-whisper>=1.0.0`
- Video: `moviepy==1.0.3`, `scenedetect[opencv]>=0.6.4`, `opencv-python>=4.9.0.80`, `mediapipe==0.10.14` (versi di-pin), `ffmpeg-python>=0.2.0`
- UI/Backend: `streamlit>=1.32.0`, `fastapi>=0.110.0`, `uvicorn[standard]>=0.29.0`, `pydantic>=2.7.1`
- AI: `groq>=0.8.0`, `openai>=1.30.0`
- Sentiment: `textblob>=0.18.0`, `vaderSentiment>=3.3.2`
- Lain: `numpy`, `Pillow`, `tqdm`, `python-dotenv`, `requests`, `rich`, `loguru`

### 3. `.env.example` (31 baris)
Variabel yang didukung:
- Whisper: `WHISPER_MODEL=large-v3-turbo`, `WHISPER_COMPUTE_TYPE=int8`, `WHISPER_DEVICE=cuda`, `WHISPER_ALLOW_LARGE_ON_GPU=1`, `WHISPER_GPU_SAFE_VRAM_GB=5.5`, `WHISPER_CUDA_DEVICE_INDEX=0`
- yt-dlp: `YTDLP_COOKIES=cookies.txt`
- API key AI: `GROQ_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, `OLLAMA_BASE_URL`, `OPENAI_API_KEY`
- Default provider: `AI_PROVIDER=Groq`, `LLM_MODEL=llama-3.3-70b-versatile`
- Opsional: `ASSEMBLYAI_API_KEY` (berbayar, dikomentari), `CLIPPING_SOFTWARE_API_KEY`

### 4. `.gitignore` (42 baris)
Mengabaikan: `.env`/`*.env` (secret), `__pycache__/`, `*.pyc`, `venv/`, `output/`, `temp/`, `*.mp4/*.mp3/*.wav/*.srt`, `.DS_Store`, `Thumbs.db`, `.vscode/`, `*.log`, `logs/`. Catatan penting: **`temp/` ada di .gitignore**, jadi `temp/nvenc_smoke.mp4` yang ikut ter-clone berarti file itu sudah di-commit sebelum aturan gitignore berlaku.

### 5. `start.bat` (62 baris)
One-click setup Windows, 6 langkah:
1. Cek Python terinstall (kalau tidak → error + pause)
2. Buat venv jika belum ada (`python -m venv venv`)
3. Aktivasi venv + `pip install -r requirements.txt`
4. Download FFmpeg otomatis dari gyan.dev jika `ffmpeg\bin\ffmpeg.exe` belum ada (pakai PowerShell `Invoke-WebRequest`)
5. Set PATH FFmpeg lokal untuk sesi itu
6. Jalankan `uvicorn server:app --reload --port 8000`

### 6. `config.py` (312 baris) — Jantung konfigurasi
- **PATH**: `OUTPUT_DIR`, `TEMP_DIR`, `FONT_DIR` dibuat otomatis di samping proyek. `DEFAULT_FONT = "Arial"`.
- **WHISPER**: default `large-v3-turbo`, device `auto`, compute `int8` (disebut paling stabil untuk RTX 3060 6GB), `WHISPER_GPU_SAFE_VRAM_GB=5.5`, fallback Groq Whisper (`GROQ_WHISPER_MODEL=whisper-large-v3-turbo`, bitrate `64k`).
- **Durasi klip**: `MIN_CLIP_DURATION=15`, `MAX_CLIP_DURATION=60`, `IDEAL_CLIP_DURATION=30`.
- **VIRAL_KEYWORDS**: 6 kategori × kata kunci Indonesia+Inggris:
  - humor: lucu, gokil, ngakak, rofl, haha, wtf, shocking, gila, aneh, funny, joke, punchline, kocak, absurd
  - motivasi: sukses, motivasi, rahasia, cara, tips, how to, jangan lewatkan, ubah hidup, dream, goal, inspirasi, bangkit, mindset
  - shocking: tidak percaya, you won't believe, rahasia, terkejut, plot twist, ending, akhirnya, baru tau, ternyata, fakta
  - emotional: sedih, menangis, haru, bangga, cinta, keluarga, perjuangan, emotional, touching, kehilangan, takut
  - educational: tutorial, cara mudah, step by step, belajar, rahasia, pro tips, hacks, kesalahan, strategi, framework
  - engagement: comment, like, share, tag temen, kamu setuju?, pilih mana, tebak, quiz, menurut kamu, setuju nggak
- **HOOK_PATTERNS** (14): jangan, stop, ini alasan, kenapa, ternyata, rahasia, kamu harus, pernah nggak, gue kasih tahu, listen, watch this, the truth, here is why, you need to, nobody tells you
- **RETENTION_KEYWORDS** (16): tapi, namun, akhirnya, terus, lalu, ternyata, masalahnya, plot twist, bagian paling, ini yang, wait, but, then, finally
- **CTA_KEYWORDS** (13): follow, subscribe, komen, comment, share, save, simpan, tag, bagikan, like, setuju, pilih
- **SCORING_WEIGHTS** (total ~115 saat LLM aktif): keyword_match 25, sentiment_intensity 20, hook_presence 15, length_score 10, visual_interest 10, retention_curve 10, shareability 10, llm_score 15.
- **MIN_VIRAL_SCORE = 55** (default ambang).
- **AI provider & model**: Groq (`llama-3.3-70b-versatile`, `openai/gpt-oss-120b`, `llama-3.1-8b-instant`), Gemini (gemini-2.5-flash dst.), OpenRouter (`openrouter/free`), Ollama (`qwen2.5:7b`, `llama3.1:8b`, `gemma3:4b`). `USE_LLM_SCORING` aktif otomatis jika ada API key. `AI_MAX_CANDIDATES=24` (cap panggilan AI per video).
- **VIRAL_PROMPT**: prompt Bahasa Indonesia untuk LLM; meminta jawaban JSON ketat `{"score", "category", "reason", "suggestion"}`; aturan: naikkan score jika segmen utuh (setup-konflik-payoff), turunkan jika potongan random/gantung.
- **FACE & REFRAMING**: `ENABLE_FACE_DETECTION=True`, confidence 0.5, `SMOOTHING_FRAMES=15`, target 9:16, output 1080×1920.
- **CAPTION_STYLES**: 6 gaya — `modern`, `minimal`, `karaoke`, plus 3 baru "Klipers.pro-inspired": `tiktok_bold`, `gradient`, `neon`. Masing-masing punya font_size, warna, stroke, posisi, animasi (pop/fade/karaoke), emoji. `MAX_WORDS_PER_LINE=8`.
- **OUTPUT**: codec libx264/AAC, bitrate video 5000k, audio 192k, preset fast; **NVENC default ON** (`USE_NVENC=1`, `NVENC_PRESET=p4`, `NVENC_CQ=20`); `KEEP_SOURCE_VIDEO=1`; watermark default OFF (`@yourhandle`, opacity 0.6); end screen ON (`Follow for more! 🔥`).
- **Fitur Klipers.pro**: audio enhance ON, background music OFF (volume 0.12, auto duck ON), B-roll bumper ON, hook optimizer ON, niche detector ON, `PLATFORM_SAFE_ZONES` (TikTok lower_third, Reels bottom_safe, Shorts center).
- **Lain**: `MAX_CLIPS_PER_VIDEO=20`, `PARALLEL_CLIPS=2`.
- **FYP PACK**: `DEFAULT_HASHTAGS` per kategori + `PLATFORM_PROFILES` (TikTok/Reels/Shorts/Semua dengan caption_limit, style, CTA, preferred_duration, safe_caption_zone) + `FYP_AGENT_CHECKS` (5 checklist kualitas).
- Di bagian bawah ada placeholder untuk mode niche: `# GAMING_MODE`, `# PODCAST_MODE` (dikomentari).

### 7. `app.py` (654 baris) — Web UI Streamlit
- **UI**: header gradien (merah→toska), sidebar pengaturan lengkap, custom CSS (metric-card, clip-card, tombol gradien), `st.balloons()` saat hasil jadi.
- **Sidebar**: preset laptop ditampilkan hardcoded ("Ryzen 7 5800H | RAM 24GB | RTX 3060 6GB").
- **Kontrol utama**: slider jumlah klip (3–20), min score (40–90), durasi min/max; pilihan **Engine Clip**: "Local Advanced Engine" vs "Clipping.software API" (butuh API key, hanya untuk URL publik — file upload otomatis dialihkan ke lokal).
- **Style & fitur**: 6 caption style (modern, tiktok_bold, gradient, neon, minimal, karaoke), slider font size (5–70), posisi vertikal (10–260), border thickness (0–8), face reframing checkbox.
- **Mode video**: Single Speaker, Conversation/Podcast (2 orang), Group/Multi Person (3+), Auto Detect.
- **Output type**: Vertical (9:16), Wide (16:9), Fit (semua frame + background blur).
- **Efek lanjutan**: Auto Zoom on Speaker, Split Screen, Background Blur, "Klipers-style Reframe Mode", Eye Contact Correction, upload Virtual Background (jpg/png).
- **Mode transkripsi** (5 pilihan): Auto, GPU NVIDIA (cuda), **Groq Whisper Cloud** (tanpa GPU laptop), CPU aman, 🛡️ Super Stable CPU Mode.
- **Model Whisper**: tiny/base/small/medium/large-v3-turbo/large-v3 + checkbox "Paksa izinkan large-v3 di GPU".
- **Provider AI**: Groq/Gemini/OpenRouter/Ollama + input API key per provider.
- **Output**: watermark, NVENC checkbox, simpan source video, target platform.
- **Audio**: Audio Enhancer (noise reduction + bass boost) default ON; Background Music + Auto Duck dengan upload mp3/wav.
- **Fungsi `process_video()`** (baris 276–558): mengatur semua config, memetakan transcribe_mode ke device/compute, mengonfigurasi LLM, download/upload video (download disimpan ke folder tetap `mentahan/`, bukan %TEMP%), transcribe → analisis → render dengan `process_clips()`, lalu `show_results()`.
- **Fungsi `show_results()`** (baris 560–651): metrik ringkasan (total klip, rata-rata score, total durasi, kategori terpopuler), kartu per klip dengan video player, thumbnail, tombol download MP4, download metadata.json.
- **Cleanup**: hapus temp upload; hapus otomatis source video di `mentahan/` setelah sukses jika `KEEP_SOURCE_VIDEO=False`.

### 8. `server.py` (214 baris) — Backend FastAPI
- **Endpoint**:
  - `POST /verify-groq` — tes validitas Groq API key via `client.models.list()`.
  - `POST /process-video` — menerima `ProcessRequest` (url, num_clips, min_score, min/max_duration, caption_style, enable_face_reframe, use_llm, whisper_model/device/compute_type, categories, watermark_text, target_platform, groq_api_key, openai_api_key), membuat job UUID, menjalankan pipeline sebagai BackgroundTask.
  - `GET /status/{job_id}` — polling status job (queued → downloading → transcribing → analyzing → rendering → done/error) + progress 0–100.
  - `GET /results/{job_id}` — ambil hasil bila selesai.
- **Job store**: dict in-memory (`jobs`), disarankan Redis/Postgres untuk produksi.
- **Pipeline `process_video_job()`**: download ke temp dir → Whisper → `analyze_viral_potential` (dengan filter kategori opsional) → `process_clips` ke `output/job_xxxx/` → hasil JSON (total_clips, clips, transcription, output_folder, metadata_file) → cleanup temp.
- **CORS**: `allow_origins=["*"]` (untuk UI HTML lokal; di produksi harus dibatasi).
- ⚠️ Catatan ketidaksesuaian: HTML UI memanggil `GET /download/{job_id}/{i}` untuk tombol download, tapi endpoint ini **tidak ada** di server.py — download lewat HTML akan 404; Streamlit UI punya tombol download sendiri yang jalan.
- ⚠️ `configure_llm_clients(req.groq_api_key or "", req.openai_api_key or "")` dipanggil posisi (provider, model) — argumen terbaca sebagai provider/model, bukan API key; kemungkinan bug kecil.

### 9. `cli.py` (155 baris)
- Argumen lengkap: `--url`, `--file`, `--output`, `--num-clips`, `--min-score`, `--min-duration`, `--max-duration`, `--caption-style` (modern/minimal/karaoke), `--face-reframe`, `--use-llm`, `--ai-provider`, `--ai-model`, API key args (groq/gemini/openrouter), `--ollama-base-url`, `--watermark`, `--whisper-model`, `--whisper-device` (cuda/cpu/auto), `--whisper-compute-type`, `--target-platform`, `--cookies-from-browser` (chrome/edge/firefox/brave/opera), `--cookies-file`.
- Alur: validasi url/file → override config → download (atau file lokal) → transcribe → `configure_llm_clients` + `analyze_viral_potential` → `process_clips` → cetak top 5 klip (score, kategori, durasi, reason, hook, caption, hashtag, FYP grade) → simpan `summary.json`.
- Output default: `output/klip_YYYYMMDD_HHMMSS/`.

### 10. `viral_auto_klip_v2_real.html` (538 baris)
- **Desain**: dark mode premium (background #080810), font Google (Syne, DM Sans, JetBrains Mono), efek orb blur animasi, 4 tab (Generator, Config, Setup, Preview), modal preview "phone", toast, progress bar shimmer + stage list.
- **Integrasi backend**: `BACKEND_URL = "http://localhost:8000"`; `startRealProcessing()` POST `/process-video`, lalu polling `GET /status/{job_id}` tiap 1.2 detik; `verifyGroq()` POST `/verify-groq`; Groq key disimpan di `localStorage`.
- **Hasil**: `showRealResults()` me-render kartu klip (thumbnail placeholder, score bar, reason) + tombol download (→ `/download/...` yang tidak ada di backend) + copy metadata JSON.
- **Tab Setup**: berisi instruksi shell (masih memakai path Linux lama `/home/workdir/artifacts/viral_auto_klip` — sisa dari lingkungan pengembangan).
- Beberapa fungsi JS sengaja disederhanakan: `detectPlatform()` kosong ("same as before"), `updateProgressUI` hanya membuat 1 item stage statis.

### 11. `core/utils.py` (358 baris)
- `setup_logging()` — format loguru ke stdout (waktu | level | module:function - pesan).
- `get_video_info()` — metadata via **ffprobe** (durasi, lebar, tinggi); fallback 1920×1080 bila gagal.
- `download_video()` — yt-dlp ke folder tetap `mentahan/`; mencoba 3 tingkat format berurutan: 1080p+ → 720p+ → best available; dukungan cookies (file / browser / dari .env `YTDLP_COOKIES`); extractor args YouTube `player_client: android,web,ios`; error khusus bila cookies Chrome terkunci; **fallback ke pytubefix** (`_download_video_with_pytubefix`) bila yt-dlp gagal total; pesan error final menyarankan cobalt.tools.
- `extract_audio()` — ffmpeg PCM 16kHz mono.
- `create_thumbnail()` — ffmpeg 1 frame + overlay PIL (bar hitam bawah, badge score 🔥, hook text ≤35 karakter).
- `smooth_face_trajectory()` — moving-average window 15 frame untuk stabilisasi bounding box.
- `generate_ass_subtitles()` — membuat file **ASS** (Advanced SubStation) dengan wrapping kata per `MAX_WORDS_PER_LINE`, animasi fade (`{\fad(150,150)}`) untuk style "pop", emoji otomatis "🔥" untuk kata lucu/wow/gila/shocking/rahasia, dan highlight kata kunci.
- `_highlight_caption_keywords()` — memberi highlight kuning (`{\c&H00D7FF&}`) maksimal 2 kata per baris; kriteria: kata kunci (tidak/nggak/gak/belum/jangan/harus/kenapa/rahasia/tumbuh/dewasa/model/betul/bener/ada/karena/ternyata/tips/cara/salah/penting/viral/fyp/uang/sukses) +3, kata ≥7 huruf +1, KAPITAL +1; kalau tidak ada yang menang, kata terpanjang di-highlight.

### 12. `core/audio_enhancer.py` (118 baris)
- `enhance_audio()` — filter chain FFmpeg: `afftdn=nr=12:nf=-50` (noise reduction) → `loudnorm=I=-16:TP=-1.5:LRA=11` (normalisasi EBU R128) → `equalizer` 80Hz (+4.0) dan 150Hz (+2.4) bass boost → AAC 192k. Gagal → fallback copy asli.
- `add_background_music_with_duck()` — konversi musik ke wav 48kHz, **sidechaincompress** (threshold 0.03, ratio 8, attack 50ms, release 300ms) agar musik mengecil saat ada suara bicara; `-stream_loop -1` agar musik loop; fallback mix sederhana bila ducking gagal.

### 13. `core/clipping_software.py` (92 baris)
Integrasi opsional ke `https://api.clipping.software`:
- `submit_clip_job()` — POST `/generate` dengan payload `finalVideo_type` (Vertical/Wide/Fit), `numberOfClips`, `captions`, `ai_clipping`, `captionStyle` {font_size, vertical_position, border_thickness}, `yt_link`/`videoUrl`.
- `get_clip_status()` — GET `/status?taskID=...`.
- `wait_for_clip_job()` — polling tiap 8 detik, timeout 30 menit, status COMPLETED/DONE/SUCCESS dianggap selesai, FAILED/ERROR → RuntimeError.
- `_map_output_type()` — Wide→Wide, Fit→Fit, lainnya→Vertical.

### 14. `core/transcriber.py` (507 baris)
- **Dual engine**: faster-whisper lokal + Groq Whisper cloud.
- `transcribe_video()` — validasi file → `_safe_model_for_gpu()` (large/large-v3 diturunkan ke medium bila VRAM < `WHISPER_GPU_SAFE_VRAM_GB` 5.5GB) → bila device groq/cloud → `_transcribe_with_groq()` → selain itu susun `_build_transcription_plan()`.
- **Rencana percobaan berlapis**: semua model (dengan fallback: large-v3-turbo→medium→small→base; medium→small→base; dst.) × GPU (compute pilihan + int8) dulu, baru semua × CPU int8. Setiap percobaan gagal → coba berikutnya; bila semua gagal & `GROQ_WHISPER_FALLBACK=1` + ada GROQ_API_KEY → fallback Groq.
- **CUDA di subprocess**: `_transcribe_in_subprocess()` menjalankan `python -m core.transcriber --video ... --result ...` sebagai child process supaya Streamlit tidak crash bila CUDA crash native (mis. cublas64/cudnn error).
- `_worker_main()` — entry point CLI internal untuk subprocess, hasil ditulis JSON ke file result.
- **Groq path**: `_extract_groq_audio()` (ffmpeg mono 16kHz, bitrate env 64k, peringatan bila >95MB mendekati limit API 100MB) → `client.audio.transcriptions.create(..., response_format="verbose_json", timestamp_granularities=["segment","word"])` → `_groq_payload_to_segments()` normalisasi.
- Util GPU: `_cuda_vram_gb()` via `nvidia-smi --query-gpu=memory.total`, `_cuda_runtime_available()` via ctranslate2 (`get_cuda_device_count()`) dengan env override `FORCE_WHISPER_CUDA`.
- Deteksi error: `_is_cuda_load_error()` (cublas64, cudnn, could not load library), `_is_memory_error()` (bad allocation, out of memory, std::bad_alloc).

### 15. `core/analyzer.py` (787 baris) — Otak viral scoring
- **Client AI global**: Groq, OpenAI, OpenRouter (base_url openrouter.ai), dibuat di import; `configure_llm_clients()` bisa rekonfigurasi runtime (untuk key dari UI), dukung provider Groq/Gemini/OpenRouter/Ollama/OpenAI.
- **`analyze_viral_potential()`** alur: buat kandidat segmen (`_create_candidate_segments`: segmen natural Whisper yang 15–60 dtk + sliding window ukuran [min,25,35,45,55,max] step 5–12 dtk, di-snap ke batas ucapan, di-dedupe) → scoring lokal semua kandidat → shortlist top (max 4× MAX_CLIPS atau 24) untuk **re-scoring LLM** → filter `>= MIN_VIRAL_SCORE` → sort skor → `_remove_overlapping_clips` (gap 5 dtk) → `_apply_diversity_ranking` (penalti -4 per klip kategori yang sama, agar output beragam) → deteksi niche → potong ke `MAX_CLIPS_PER_VIDEO`.
- **`_score_segment()`** — 9 komponen skor:
  1. Keyword match (+8 per kata viral, cap 25)
  2. Sentimen VADER (intensitas |compound| ×0.4, cap 20)
  3. Hook (tanda tanya/seru +12, angka/%/juta +10, sapaan langsung kamu/anda/lo/lu/bro/sist +8, pola HOOK_PATTERNS di 18 kata pertama +12, cap 15)
  4. Kesesuaian durasi (10/8/6)
  5. Kelengkapan momen (jumlah kata, durasi ≥35, intro hook, outro payoff, konjungsi alur; penalti -2 bila mulai dengan dan/atau/tapi/dst., max 10)
  6. Retention curve (densitas kata/detik 1.7–3.8 ideal, kalimat ≤18 kata, kata payoff; max 10)
  7. Shareability (CTA +4, frasa tanya-opini +4, listicle/rahasia/tips +3; max 10)
  8. Visual interest (rasio frame berwajah; base 5)
  9. LLM score (×15/100; lalu **reweight final = skor×0.58 + LLM×0.42** bila LLM aktif)
  - Bonus: ≥2 keyword + |sentimen|>0.4 → +8; strong hook + retention ≥6 → +6. Final cap 100.
- **LLM callers**: `_call_selected_llm()` (Groq/OpenRouter/OpenAI via SDK chat.completions dengan `response_format json_object`; Gemini via REST `generativelanguage.googleapis.com/v1beta/models/...:generateContent`; Ollama via `/api/chat` format json). Rate limit (429) → `LLM_RATE_LIMITED=True` nonaktifkan LLM untuk sisa video.
- **Fitur Klipers.pro**: `detect_niche()` (8 niche: gaming, finance, beauty, motivation, education, comedy, relationship, tech; butuh ≥2 keyword), `get_viral_score_breakdown()` (untuk visualisasi UI), `optimize_hooks_with_llm()` (3 hook alternatif via Groq, fallback template bila tanpa LLM).

### 16. `core/clipper.py` (1229 baris) — Mesin rendering terbesar
- **Encoder**: `_video_encode_args()` — NVENC (`h264_nvenc`, preset p1/p4, cq) bila `USE_NVENC=1` dan `_has_nvenc()` lulus: cek daftar encoder ffmpeg + **smoke test encode 64×64** (inilah yang menghasilkan `temp/nvenc_smoke.mp4` 0-byte bila gagal); fallback `libx264` preset ultrafast/fast.
- **`process_clips()`** alur per klip:
  1. `extract_segment()` (ffmpeg -ss/-t, crf 18, aac 192k)
  2. Opsional: `apply_virtual_background()` (MediaPipe selfie segmentation, alpha smoothing), `apply_eye_contact_correction()` (unsharp+contrast; **bukan** model gaze neural — didokumentasikan jujur di docstring)
  3. `detect_faces_in_clip()` — MediaPipe FaceDetection model_selection=1, sample rate 3 frame; single mode (tracking wajah terbesar/terdekat) atau group mode (union box ≤5 wajah); gap diisi + smoothing 15 frame; metadata per frame bila klipers_reframe
  4. `reframe_video()` → cabang: group stage view (full frame + blur bg) / `klipers_style_reframe_video()` (shot type: closeup, two_person, group_context, reaction; crop size per tipe; caption-safe y) / `dynamic_reframe_video()` (ikuti trayektori wajah per-frame, EMA alpha 0.18, auto zoom berdasarkan tinggi wajah/0.24) / split screen (2 speaker kiri-kanan) / crop statis
  5. `enhance_audio()` → `mux_audio()` ganti track audio
  6. `build_subtitle_segments_for_clip()` — timing kata Whisper di-rebase ke timeline klip (chunk 5 kata atau akhir kalimat); fallback ke subtitle_segments; fallback terakhir teks penuh
  7. `generate_ass_subtitles()` + `burn_subtitles()` (filter ffmpeg `subtitles=...:force_style`, crf 18, `-movflags +faststart`; path di-escape `:` dan `'` untuk Windows)
  8. Opsional background music + ducking
  9. `add_intro_bumper()` — bumper 2.8 detik bergaya niche (emoji+hook, skor berwarna, fade in) via drawtext
  10. `create_thumbnail()` dari tengah klip
  11. `add_extras()` — watermark + end screen (hook text) via drawtext textfile, dengan escape khusus
  12. Bersihkan file intermediate (raw, virtual_bg, eye_contact, reframed)
  13. `enrich_clip_for_platforms()` → metadata per platform
- **metadata.json**: `video_source`, `total_clips`, `fyp_summary` (klip terbaik, rata-rata skor, hashtag rekomendasi, urutan upload), `upload_strategy`, `clips` lengkap; `json_safe()` menormalkan numpy/Path/tuple agar bisa di-serialize.
- Util penting lain: `_choose_tracking_face()` (ranking confidence×1000 + ukuran − jarak×6), `_union_face_boxes()` (pad 30%/85%), `_clamp_bbox()` (buang box <0.2% area), `mux_video_with_source_audio()`, `_group_needs_stage_view()`.

### 17. `core/fyp_agent.py` (298 baris)
- `enrich_clip_for_platforms()` — menambah `platform_copy` (TikTok/Reels/Shorts), `quality_checklist` (5 cek), `fyp_grade` (A+/A/B/C/Needs Review berdasarkan skor).
- `build_upload_strategy()` — urutan upload berdasar skor, tip batching, jadwal upload terbaik per niche+platform.
- `_build_platform_copy()` — caption per platform dengan CTA sesuai niche (8 niche × 3 platform, mis. TikTok-finance: "Comment 'MAU' biar gue kasih tau! 💰"), title YouTube dari template (mis. "🔥 {hook} (Endingnya Gila!)"), hashtag di-boost (`_boost_hashtags`, max 8).
- `get_best_upload_schedule()` — tabel jam upload terbaik per niche per platform (mis. TikTok gaming 19:00–21:00; motivation 05:00–07:00), lengkap untuk 3 platform × 9 niche.

---

## 🔍 Catatan & Temuan Penting

1. **Proyek fungsional lengkap**: pipeline nyata dari download → transkripsi → scoring → render, bukan template kosong.
2. **Ketergantungan wajib eksternal**: FFmpeg (ffprobe/ffmpeg harus di PATH), Python 3.10+ (pycache menunjukkan pernah dijalankan di Python 3.12), model Whisper diunduh saat pertama jalan (bisa besar).
3. **Bug/ketidaksesuaian ditemukan saat pembacaan**:
   - HTML UI memanggil `GET /download/{job_id}/{i}` tapi endpoint itu tidak didefinisikan di `server.py`.
   - `server.py` baris 130: `configure_llm_clients(req.groq_api_key or "", req.openai_api_key or "")` — posisi argumen tidak sesuai tanda tangan fungsi (provider, model) sehingga key tidak pernah terpakai lewat jalur ini.
   - `config.py`: `SCORING_WEIGHTS` berjumlah ~115 bila LLM aktif, tapi hasil akhir di-normalisasi 0–100, jadi bukan total pembobotan murni.
   - Path lama `/home/workdir/artifacts/viral_auto_klip` masih muncul di tab Setup HTML.
   - `temp/` dan `output/` sudah di-.gitignore, tetapi `temp/nvenc_smoke.mp4` (0 byte) ikut ter-commit di repo — sisa smoke test NVENC.
   - `moviepy==1.0.3` di requirements tetapi clipper.py sebenarnya memakai OpenCV + FFmpeg langsung, bukan moviepy (diimport tapi tidak dipakai di jalur utama).
4. **Privasi**: README mengklaim video tidak dikirim ke pihak ketiga — benar untuk mode lokal, tapi perlu dicatat: mode "Groq Whisper Cloud" dan "AI scoring" mengirim transkrip/audio ke Groq/Gemini/OpenRouter, dan engine "Clipping.software API" mengirim URL ke pihak ketiga.
5. **Keamanan**: CORS `*` di server.py dan API key disimpan di localStorage HTML — aman untuk pemakaian lokal saja, tidak untuk produksi.
6. **Mode paling stabil** (menurut kode): Whisper CPU int8 ("Super Stable CPU Mode"), NVENC dimatikan bila smoke test gagal, yt-dlp punya fallback pytubefix, dan Whisper lokal punya fallback Groq.

---

## 🚀 Cara Menjalankan (ringkas)

```cmd
cd E:\projek\viral-auto-klip
start.bat                :: setup otomatis + jalankan backend, atau:
python -m venv venv && venv\Scripts\activate && pip install -r requirements.txt
streamlit run app.py     :: UI utama (http://localhost:8501)
:: alternatif: uvicorn server:app --reload --port 8000 + buka viral_auto_klip_v2_real.html
python cli.py --url "<url video>" --num-clips 8   :: mode CLI
```

Hasil klip tersimpan di `output\klip_<timestamp>\`, source video di `mentahan\`, thumbnail + `metadata.json` + `summary.json` ikut dibuat.
