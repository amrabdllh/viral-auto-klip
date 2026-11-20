# 🔎 Audit Lengkap — Viral Auto Klip v3

> Pembacaan **utuh** (body penuh, bukan head saja) terhadap **semua file** di `E:\projek\viral-auto-klip`, satu per satu.
> Tanggal: 2026-11-18. Kondisi yang diaudit = **sesudah upgrade v3** (lihat `CHANGELOG_V3.md`).
> Dokumen sebelumnya `RINGKASAN_ISI_SEMUA_FILE.md` mendeskripsikan kondisi **pra-v3**.

---

## 1. Daftar file yang dibaca

| File | Baris | Peran |
|---|---|---|
| `README.md` | ~355 | Dokumentasi v3 |
| `CHANGELOG_V3.md` | ~110 | Riwayat fix & upgrade v3 |
| `RINGKASAN_ISI_SEMUA_FILE.md` | 264 | Ringkasan kondisi pra-v3 |
| `config.py` | 421 | Konfigurasi global (Whisper, scoring, 10 provider AI, caption, editing, publisher) |
| `.env.example` / `.env` | 53 | Variabel lingkungan (isi `.env` = placeholder, tidak ada API key asli) |
| `.gitignore` | ~50 | Pengecualian git |
| `requirements.txt` | 32 | Dependensi (+ opsional publisher dikomentari) |
| `start.bat` | 93 | One-click run tanpa venv (mode 1 = backend+HTML, 2 = Streamlit) |
| `app.py` | ~690 | UI Streamlit |
| `server.py` | 331 | Backend FastAPI (job, status, download, jobs) |
| `cli.py` | 196 | CLI (publish package, upload YouTube) |
| `viral_auto_klip_v2_real.html` | ~730 | UI HTML untuk backend FastAPI |
| `core/analyzer.py` | ~815 | Scoring viral (rule-based + LLM rerank) |
| `core/clipper.py` | ~1460 | Render: extract, reframe, caption, bumper, extras, scene snap |
| `core/transcriber.py` | ~530 | faster-whisper (subprocess CUDA) + Groq Whisper |
| `core/utils.py` | ~385 | ffprobe, yt-dlp/pytubefix, thumbnail, ASS subtitle |
| `core/fyp_agent.py` | 329 | Copy per platform, grade, jadwal upload, varian A/B |
| `core/publisher.py` | 287 | Paket publish + upload YouTube/TikTok |
| `core/mediapipe_compat.py` | 171 | Layer kompatibel mediapipe lama/baru (Tasks API) |
| `core/audio_enhancer.py` | ~125 | Noise reduction, loudnorm, bass boost, ducking musik |
| `core/clipping_software.py` | 93 | Integrasi API eksternal clipping.software |

Folder non-kode: `ffmpeg/bin/` (ffmpeg/ffprobe/ffplay 9.0.2, ~300 MB), `models/mediapipe/*.tflite` (2 model kecil hasil auto-download), `venv/`, `output/`, `temp/`, `fonts/` (kosong), `core/__pycache__/` (bytecode).

---

## 2. 🐛 Bug yang DITEMUKAN & DIPERBAIKI di sesi ini (terverifikasi tes nyata)

| # | File | Bug | Bukti | Perbaikan |
|---|---|---|---|---|
| 1 | `core/transcriber.py` | `batch_size` dikirim ke `WhisperModel.transcribe()`, padahal parameter itu **tidak ada** (faster-whisper 1.2.1) → `TypeError` → **semua percobaan CUDA gagal**, transkripsi selalu jatuh ke CPU | `inspect.signature(WhisperModel.transcribe)` → `batch_size` tidak ada | Fungsi baru `_run_transcribe()`: batch dipakai lewat `BatchedInferencePipeline` (khusus CUDA), selain itu `model.transcribe()` biasa |
| 2 | `core/clipper.py` `add_intro_bumper` | Teks disisipkan via `text='...'`; `:` di `"Score: 80"` (selalu ada) dan apostrof di hook mematahkan parser filter FFmpeg → bumper + **hook punch zoom tidak pernah jalan** (fallback copy diam-diam) | `ffmpeg ... drawtext=text='✨ Score: 80'` → `No option name near ' 80'` | Pakai `textfile=` + `expansion=none`, file teks dihapus setelahnya, error dicatat di log (bukan `except:` kosong) |
| 3 | `core/clipper.py` (bumper & `add_extras`) | Karakter `%` di hook/watermark (mis. "100%") dianggap ekspansi drawtext → render gagal | Tes dengan hook "…100%" gagal | `expansion=none` pada semua drawtext |
| 4 | `core/audio_enhancer.py` | `sidechaincompress makeup=0.35` — rentang valid 1–64 → **ducking musik selalu gagal**, selalu fallback mix biasa | `Value 0.350000 for parameter 'makeup' out of range [1 - 64]` | `makeup=1` (level musik sudah diatur `volume`) |
| 5 | `core/audio_enhancer.py` | `loudnorm` meng-upsample → audio hasil 96 kHz | ffprobe: `96000` | Tambah `-ar 48000` |
| 6 | `core/clipper.py` split screen | Setengah frame (960×1080) di-scale paksa ke 540×1920 → **gambar melar** | Hitungan rasio | Crop tiap panel dengan rasio panel yang benar, berpusat di wajah kiri/kanan |
| 7 | `core/clipper.py` crop statis + auto_zoom | Crop = ukuran wajah ×0.75 (lebih kecil dari wajah, rasio salah) → melar | Kode | Zoom menjaga rasio output |
| 8 | `core/clipper.py` | Versi antara (`<clip>.mp4`, `_audio`, `_music`, `_broll`, `enhanced.aac`) tidak dihapus → **4–5 salinan video penuh per klip** | Kode | Semua file antara dihapus; tersisa hasil akhir + `captions.ass` + `thumbnail.jpg` (terverifikasi) |
| 9 | `core/utils.py` ASS | Warna style (`neon` cyan, `karaoke` gold, outline `gradient` pink…) **diabaikan** — semua caption putih/hitam | Style line hardcoded `&H00FFFFFF` | `color`/`stroke_color` dari config dikonversi ke format ASS; reset highlight ke warna utama style |
| 10 | `core/analyzer.py` | Panggilan LLM yang **gagal** mengembalikan score 50 → tetap dipakai sebagai rerank 42% → skor klip tertarik ke 50 | Kode | Gagal → score 0 → rerank dilewati |
| 11 | `core/analyzer.py` `detect_niche` | Substring: `"ai"` cocok dengan "sampai/main/pakai", `"hp"`, `"app"`, `"win"`, `"date"` → niche `tech`/`gaming` palsu | Tes: "sampai main pakai" dulu → tech | Word-boundary (`_find_keyword_position`) |
| 12 | `core/analyzer.py` hook | Sapaan `"lo"`/`"lu"` substring cocok dengan "lokasi/lucu/lulus" | Kode | Word-boundary |
| 13 | `viral_auto_klip_v2_real.html` | CSS `.modal-overlay`, `.modal-box`, `.toast` **tidak ada** → kotak "Preview Klip" selalu tampil di bawah halaman, toast tidak pernah hilang | `grep` 0 hasil | CSS modal & toast ditambahkan |
| 14 | HTML | `showToast` memakai `innerHTML` dengan pesan error dari backend | Kode | `textContent` |
| 15 | HTML tab Setup | Masih menyebut venv, padahal start.bat v3 tanpa venv | Kode | Teks diperbarui |
| 16 | `app.py` | `open(clip["file_path"])` tanpa cek → satu file hilang membuat seluruh halaman hasil crash | Kode | Cek `os.path.exists` |
| 17 | `.gitignore` | `ffmpeg/` (~300 MB biner) belum di-ignore → ikut ter-commit; token OAuth juga belum | `git status` → `?? ffmpeg/` | Tambah `ffmpeg/`, `ffmpeg.zip`, `ffmpeg_temp/`, `.yt_upload_token.json`, `.google_ai_token.json` |
| 18 | `README.md` | Contoh `--platform tiktok` & `--playlist` (opsi tidak ada di CLI), default whisper "medium" (aktual large-v3-turbo), nama file output salah, klaim "Batch Processing" & "tidak ada data ke pihak ketiga" | Bandingkan dgn `cli.py` | Diperbaiki sesuai kode |

### Verifikasi yang dijalankan
- `py_compile` semua file Python → OK.
- Skrip uji (di scratchpad, di luar proyek) dengan video sintetis — **10/10 PASS**: niche word-boundary, kwargs tanpa `batch_size`, warna ASS neon, bumper ter-render (teks berisi `:` `'` `%`), file teks sementara terhapus, split screen 1080×1920, audio 48 kHz, ducking tanpa fallback.
- `process_clips()` penuh (2 klip, paralel, style neon, end screen) → 2 klip `_final.mp4` jadi, folder klip bersih.
- **Belum diuji**: jalur `BatchedInferencePipeline` di GPU nyata (butuh CUDA + unduh model Whisper). Signature-nya sudah dicek cocok (`batch_size`, `word_timestamps`, `vad_filter`, `initial_prompt`, `beam_size`).

---

## 3. ⚠️ Temuan yang BELUM diperbaiki (perlu keputusan / di luar perbaikan bug kecil)

> ✅ **Update 2026-11-18 (v4): ke-13 temuan di bawah sudah diperbaiki** — lihat [CHANGELOG_V4.md](CHANGELOG_V4.md).

1. **Provider AI setengah jadi** — `config.py` mendeklarasikan Anthropic, DeepSeek, Mistral, Antigravity (OAuth Google), Custom (OpenAI-compatible), ElevenLabs STT, tapi `analyzer.py` hanya mendukung Groq/Gemini/OpenRouter/Ollama/OpenAI. Memilih provider baru → `_llm_configured()` = False → diam-diam hanya scoring lokal. Juga `USE_LLM_SCORING` bisa True padahal provider tidak didukung. UI Streamlit & CLI hanya menawarkan 4 provider (CLI tidak punya `OpenAI`).
2. **Animasi karaoke tidak diimplementasikan** — style `karaoke` hanya beda warna; tidak ada highlight per kata (`\k`).
3. **Substring matching masih tersisa** di `HOOK_PATTERNS`, `RETENTION_KEYWORDS`, `CTA_KEYWORDS` (mis. `"but"`, `"tag"`, `"like"`), `_score_shareability`, `_pick_caption_emoji`, `get_viral_score_breakdown`.
4. **Kode mati (tidak dipanggil di mana pun)**: `optimize_hooks_with_llm`, `get_viral_score_breakdown` (analyzer), `_should_try_next_attempt`, `_is_memory_error` (transcriber), `_group_needs_stage_view`, `_escape_drawtext` (clipper), `extract_audio` (utils), `_platform_list` (publisher). `openai-whisper` di requirements tidak diimport (yang dipakai faster-whisper) — dependensi berat tanpa guna.
5. **CLI `--upload-youtube` dengan `--file`** (contoh di README/publish README.txt) sebenarnya **memproses ulang** file itu sebagai video sumber (transkripsi + klip baru), bukan meng-upload klip yang sudah jadi.
6. **Streamlit**: menekan tombol download memicu rerun → hasil hilang dari layar (tidak memakai `st.session_state`). `st.image(width="stretch")` butuh Streamlit versi baru, sementara requirements `>=1.32`.
7. **Server/HTML**: default `whisper_model="medium"` (beda dengan config `large-v3-turbo`); HTML tidak mengirim `ai_provider`; chip "Auto Caption" tidak berpengaruh; tombol "Buka Folder" (`file:///`) diblokir browser.
8. **Thumbnail**: ukuran tetap 320×180, emoji 🔥 tidak ter-render oleh font default PIL, `fill` RGBA di gambar RGB (tidak transparan).
9. **Performa punch zoom**: `scale` 2× + `zoompan` diterapkan ke **seluruh** durasi klip (bukan hanya 0.8 detik) → render bumper lambat untuk klip panjang.
10. **`WHISPER_INITIAL_PROMPT`** berbahasa Indonesia dipakai untuk semua video — bisa memengaruhi transkripsi video berbahasa lain.
11. **`upload_to_tiktok`**: sintaks CLI `tiktok-uploader upload --video …` belum diverifikasi terhadap tool aslinya.
12. **NVENC smoke test** bisa berjalan bersamaan di 2 thread paralel saat pertama kali (tidak berbahaya, hanya duplikat tes).
13. `start.bat` menginstal ke Python sistem, padahal ada folder `venv/` di proyek — dua lingkungan berbeda bisa membingungkan.

---

## 4. Alur kerja (ringkas, kondisi terbaru)

```
URL / file
  → utils.download_video (yt-dlp 1080p→720p→best, cookies, fallback pytubefix) → mentahan/
  → transcriber.transcribe_video (CUDA di subprocess → CPU int8 → model lebih kecil → Groq cloud)
  → analyzer.analyze_viral_potential (kandidat natural + sliding window → skor lokal → rerank LLM top-N
                                      → filter min score → non-overlap → diversity → niche)
  → clipper.process_clips (paralel) per klip:
        scene snap → extract → [virtual bg] → [eye contact] → deteksi wajah (mediapipe_compat)
        → reframe (group stage / klipers / dynamic / split / crop) → audio enhance
        → ASS caption → burn → mux audio → [musik + ducking] → bumper + punch zoom
        → thumbnail peak → watermark/end screen → hapus file antara → fyp_agent (copy per platform)
  → metadata.json → publisher.build_publish_package (publish/) → [upload YouTube/TikTok]
```
