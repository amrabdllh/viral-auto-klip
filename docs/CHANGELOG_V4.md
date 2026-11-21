# CHANGELOG v4 — Viral Auto Klip (2026-11-18)

Full upgrade AI + perbaikan semua temuan di `AUDIT_LENGKAP_v3.md`.

---

## 🧠 Upgrade AI

| Fitur | File | Detail |
|---|---|---|
| Lapisan provider terpadu | `core/ai_providers.py` (baru) | Satu pintu untuk 10 provider: Groq, OpenAI, **Anthropic Claude** (SDK resmi `anthropic`), Gemini (API key), **Antigravity** (Gemini via login Google/OAuth), **DeepSeek**, **Mistral**, OpenRouter, Ollama, **Custom** (OpenAI-compatible, base URL + API key sendiri) |
| Claude | `ai_providers._call_anthropic` | Default `claude-opus-5-5` (juga sonnet-5-5, haiku-5-5, fable-5-2); `output_config.effort` (ANTHROPIC_EFFORT), structured output JSON schema, refusal ditangani + server-side fallback (`fallbacks="default"`) untuk model yang mendukung |
| Model reasoning | `ai_providers._run_attempts` | Otomatis coba ulang tanpa `temperature` / dengan `max_completion_tokens` / tanpa JSON mode bila endpoint menolak (gpt-5, deepseek-reasoner, endpoint custom) |
| Fallback berantai | `AI_FALLBACK_PROVIDERS` | Provider utama gagal/rate-limit → provider berikutnya |
| Pemilihan model aman | `ai_providers.resolve_model` | Model milik provider lain (mis. default Groq di .env) otomatis diganti default provider terpilih |
| **AI Moment Finder** | `analyzer._ai_moment_candidates` | AI membaca seluruh transkrip bertimestamp (dipotong per `AI_MOMENT_CHUNK_CHARS`) dan mengusulkan start/end + skor + hook sendiri |
| **AI Packaging** | `analyzer._ai_package_clips` | Hook layar, 2 hook A/B, judul YouTube, caption upload & hashtag per klip final; dipakai fyp_agent & publish package |
| ElevenLabs Scribe | `transcriber._transcribe_with_elevenlabs` | Mesin transkripsi cloud baru (timestamp per kata), juga jadi fallback bila Whisper lokal gagal |
| UI & API | `app.py`, `server.py`, HTML, `cli.py` | Pilih provider/model/key/base URL, tes koneksi, login Google, pilih transkripsi & bahasa; endpoint `/providers`, `/test-ai`, `/google-login`, `/upload/{job_id}` |

## 🐛 Bug yang diperbaiki (13 temuan audit + temuan baru)

| # | Masalah | Perbaikan |
|---|---|---|
| 1 | Provider di config (Anthropic, DeepSeek, Mistral, Antigravity, Custom, ElevenLabs) tidak diimplementasikan; `USE_LLM_SCORING` True walau provider aktif tak punya key | Semua diimplementasikan; `USE_LLM_SCORING` hanya True bila provider aktif punya kredensial |
| 2 | Style `karaoke` tanpa animasi per kata | Tag `\k` per kata dari timing Whisper/ElevenLabs |
| 3 | Substring matching di hook/retensi/CTA/shareability/emoji/breakdown | Word-boundary di semua tempat |
| 4 | Kode mati (`optimize_hooks_with_llm`, `get_viral_score_breakdown`, `_should_try_next_attempt`, `_is_memory_error`, `_group_needs_stage_view`, `_escape_drawtext`, `_probe_fps`, `extract_audio`, `_platform_list`); `openai-whisper` di requirements | Fungsi yang berguna dihidupkan (hook optimizer pakai provider aktif, breakdown nyata dari skor), sisanya dihapus; `openai-whisper` dihapus |
| 5 | `--upload-youtube --file` memproses ulang video | Mode `--upload-from <folder_output>` (baca metadata.json, upload klip jadi) |
| 6 | Streamlit: hasil hilang saat klik download (rerun) | Hasil disimpan di `st.session_state`; requirement streamlit ≥1.50 (`width="stretch"`) |
| 7 | Server default `whisper_model="medium"`; HTML tidak kirim provider; chip Auto Caption tak berfungsi; link `file:///` diblokir | Default ikut config; HTML kirim provider/model/key/base URL/transkripsi/bahasa; `enable_captions` diteruskan ke render; tombol salin path |
| 8 | Thumbnail 320×180 gepeng, emoji kotak, "transparansi" hitam | Thumbnail dari klip final (9:16), font TrueType, overlay RGBA benar, teks tidak menimpa caption |
| 9 | Punch zoom meng-upscale 2× seluruh klip | `scale` per-frame hanya membesar 0.8 detik pertama |
| 10 | Initial prompt Indonesia dipaksakan ke semua video | `WHISPER_LANGUAGE`; prompt hanya bila `id`; bahasa diteruskan ke Whisper/Groq/ElevenLabs |
| 11 | Sintaks CLI `tiktok-uploader` karangan | API Python `tiktok_uploader.upload.upload_video` + CLI `-v/-d/-c`, cookies via `TIKTOK_COOKIES_FILE` |
| 12 | NVENC smoke test ganda saat paralel | `threading.Lock` |
| 13 | start.bat vs folder venv membingungkan | start.bat memberi info bila venv ada tapi tidak dipakai |
| 14 (baru) | Kandidat di luar shortlist rerank (skor lokal murni) bisa mengalahkan kandidat yang sudah dinilai AI | Klip yang dievaluasi AI diurutkan lebih dulu |
| 15 (baru) | Gemini 2.5: token "thinking" memakan `maxOutputTokens=220` → JSON terpotong; API key di query URL | `maxOutputTokens` ≥ 8192, bagian `thought` dilewati, key via header `x-goog-api-key` |
| 16 (baru) | Nilai contoh `.env` (`gsk...`, `your_..._here`) dianggap key valid → AI "aktif" tapi gagal | Placeholder dianggap kosong |
| 17 (baru) | Dua job server berjalan bersamaan saling menimpa `config` global | Job dijalankan bergiliran (lock) |
| 18 (baru) | Teks transkrip berisi `{ } \` merusak file ASS; `force_style FontName` salah bila font berupa path | `_ass_escape`; font diatur di ASS + `fontsdir` |
| 19 (baru) | API key Groq disimpan di localStorage HTML | Tidak disimpan lagi (dihapus saat load) |

## 🎨 UI web baru (`viral_auto_klip_v2_real.html`) + fix transkripsi

- **Dibuka dari backend**: `http://localhost:8000/app` (start.bat membuka browser otomatis) — tidak perlu double-click file HTML.
- Alur 3 langkah (Video → AI → Klip & gaya) + panel **Ringkasan** yang menempel, tombol mulai dengan validasi (URL, durasi, backend online).
- Pemilih provider berupa kartu dengan status siap/belum, model (saran per provider), API key / Base URL / tombol login Google sesuai provider, **Tes koneksi**, peringatan bila provider belum siap ("→ lokal").
- Preset **Cepat / Seimbang / Kualitas**, slider jumlah klip & skor, pratinjau visual 7 gaya caption, toggle caption/face reframe/paket upload, pengaturan lanjutan (mesin transkripsi, bahasa, model Whisper, watermark).
- Progres 6 tahap + timer; **job dilanjutkan otomatis** saat halaman dibuka ulang; link langsung `…/app#job=<id>`.
- Hasil: **video diputar langsung di halaman** (poster thumbnail), skor & peringkat, tag kategori/durasi/grade/"Dipilih AI", tab caption TikTok/Reels/Shorts + salin, download, filter kategori & urutan, upload YouTube/TikTok dengan dialog konfirmasi.
- Tab **Riwayat** (job di server), **Cara Pakai** (langkah + FAQ), **Pengaturan** (alamat backend). Responsif sampai lebar ponsel. API key tidak disimpan di browser.
- `/jobs` kini memuat URL, jumlah klip, pesan, urut terbaru.
- **FIX penting**: faster-whisper 1.2.1 + PyAV 19 (satu-satunya untuk Python 3.14) → `TypeError: open() got an unexpected keyword argument 'metadata_errors'` sehingga **transkripsi Whisper lokal selalu gagal**. Audio kini didekode lewat FFmpeg ke numpy. Ditemukan dari tes end-to-end nyata.
- Hook non-AI tidak lagi berhenti di kata sambung menggantung ("…meeting, and" → "…meeting").

## 🔐 Login Google (Antigravity) lebih mudah

- Klik "Login dengan Google" tanpa client_secret tidak lagi berakhir di pesan error: UI menampilkan **panduan setup 5 langkah** (dengan link langsung ke Google Cloud) dan tombol **📂 Pilih client_secret.json** — file disimpan otomatis (`google_client_secret.json`, di-gitignore), lalu tab login Google langsung terbuka. Tidak perlu edit `.env`.
- Alternatif cepat: tombol **"Pakai API key Gemini"** (membuka aistudio.google.com/apikey dan pindah ke provider Gemini).
- Validasi file: menolak JSON rusak dan client tipe *Web application* (harus *Desktop app*) dengan pesan jelas.
- Setelah login tampil **email akun Google** + tombol Logout; endpoint baru `/google-status`, `/google-client-secret`, `/google-logout`.
- Antigravity baru dianggap "siap" setelah benar-benar login (dulu cukup path file terisi). Login tidak lagi dipicu diam-diam di tengah proses render.
- `client_secret*.json` di folder proyek terdeteksi otomatis.

## 💾 Pengaturan dari UI → otomatis tersimpan ke `.env`

- Tab **Pengaturan** di UI web (`/app#settings`): 41 pengaturan dalam 6 grup (AI, API key, Custom AI & endpoint, Transkripsi, Render & editing, Upload & publish). Tombol **💾 Simpan ke .env** + penanda perubahan belum disimpan + peringatan sebelum menutup halaman.
- Tombol **💾 Jadikan default** di kartu AI: menyimpan provider, model, API key & base URL yang sedang dipilih.
- Modul baru `core/settings.py`: hanya kunci whitelist yang bisa diubah; validasi tipe/pilihan/rentang/URL; **menolak baris baru** (mencegah penyisipan variabel lain ke .env); komentar & urutan .env dipertahankan; ditulis atomik; nilai langsung diterapkan ke config yang berjalan (tanpa restart). Format kutipan terverifikasi kompatibel dengan python-dotenv.
- API key tidak pernah dikirim balik utuh (hanya `sk-ant…abcd`); kolom kosong = tidak diubah, tombol **Hapus** untuk mengosongkan.
- Endpoint: `GET /config`, `POST /config`.
- **Keamanan**: CORS `*` diganti hanya localhost/127.0.0.1, dan semua request pengubah dari origin lain (termasuk `null` dari file:// / iframe sandbox) ditolak 403 — website lain yang kamu buka tidak bisa diam-diam mengubah .env, mencuri status, atau memicu upload. UI harus dibuka via `http://localhost:8000/app` (banner petunjuk bila dibuka sebagai file).
- Link langsung per tab: `/app#settings`, `#history`, `#help`.

## 🔍 Deteksi model otomatis

- Tombol **🔍 Deteksi** di kartu AI (dan `python cli.py --detect-models --ai-provider X`): memanggil endpoint resmi daftar model tiap provider dengan API key kamu — Anthropic (SDK `models.list`), OpenAI / DeepSeek / Mistral / Groq / OpenRouter / Custom (`/models`), Gemini & Antigravity (`v1beta/models`, hanya yang mendukung generateContent), Ollama (`/api/tags`).
- Model non-chat disaring (embedding, whisper/tts/audio, image, moderation/guard, rerank, dll.). Model rekomendasi ditandai ★; hasil bisa dicari & diklik. Sekaligus jadi tes bahwa API key valid.
- Endpoint `POST /detect-models` (key dari form hanya dipakai untuk deteksi, tidak disimpan).

## ✨ Pro editing — hasil terasa diedit manusia (`core/editing.py`, baru)

| Fitur | Detail |
|---|---|
| ✂️ Jump cut | Membuang jeda > `JUMP_CUT_MAX_GAP` (0.55 dtk) & kata pengisi (eh, em, anu, um…); timing caption/thumbnail di-remap otomatis; micro-fade audio anti "klik" |
| 🔍 Zoom dinamis | Zoom bergantian per kalimat (kesan multi-kamera) + punch-in 13% dengan ease di kata penekanan + hook punch 0.8 dtk pertama; diterapkan **sebelum** caption sehingga teks tidak ikut goyang |
| 💬 Caption "Viral (aktif)" | Style baru default: kata yang sedang diucapkan menyala kuning & "pop" (animasi skala), huruf kapital, posisi di atas zona UI platform |
| 🎨 Color grade | none / natural / vibrant / cinematic |
| ▬ Progress bar | Bar tipis di bawah (warna `PROGRESS_BAR_COLOR`) |
| 🔊 Whoosh SFX | Efek suara disintesis FFmpeg (tanpa file aset) di titik punch-in |
| 🖼️ Thumbnail pintar | Menilai ±17 frame (ketajaman, eksposur, ukuran/keyakinan wajah), teks hook diletakkan menjauhi wajah, 1 kata kunci di-highlight kuning, warna dinaikkan; **cover B** (frame lain + hook alternatif AI) untuk A/B |
| 🎬 Kartu judul hook | Bumper dirombak: teks besar terbungkus otomatis, kotak gelap + garis aksen warna niche; skor internal tidak lagi tampil ke penonton; tanpa fade-dari-hitam yang melemahkan hook |
| 📣 End CTA | Ajakan follow (`END_SCREEN_TEXT`) di 2.5 dtk terakhir |

- Preset **Minimal / Dinamis (default) / Maksimal** (`EDIT_STYLE`) + atur per efek di UI, `--edit-style`, `--color-grade`, `--no-jump-cuts` di CLI; tersimpan via tab Pengaturan.
- Grade + zoom + caption + progress bar dirender dalam **satu encode** (tidak menambah waktu render berarti).

### Bug yang ditemukan & diperbaiki selama pengujian
- **"End screen" tampil di AWAL klip** (`lte(t,2.8)`) dan mengulang hook bumper → kini CTA di akhir.
- Emoji di bumper/CTA/watermark tampil kotak (font drawtext tidak punya glyph emoji) → dibuang dari teks video.
- File teks drawtext di Windows tertulis `\r\n` → jarak antar baris dobel → ditulis dengan `\n`.
- `-filter_complex_script` sudah dihapus di FFmpeg 9 → memakai `-/filter_complex <file>`.

## 🔬 Analisis hasil nyata (job_381dcef8: YouTube + DeepSeek + ElevenLabs + Maksimal) & perbaikan

| # | Temuan di hasil | Akar masalah (terbukti) | Perbaikan |
|---|---|---|---|
| 1 | Klip buram | Video sumber hanya **640×360**: `player_client=android,web,ios` (warisan v2) membuat YouTube hanya memberi format 18; default yt-dlp untuk video yang sama tersedia s.d. 4K | Client default + fallback (tv/web_safari, mweb) bila < 720p; `format_sort res:1080, h264, m4a` (terverifikasi memilih 1920×1080 avc1); post-processor konversi (encode ulang) dihapus; peringatan di UI bila sumber < 720p |
| 2 | ±12 detik pertama klip 7 hanya dinding/meja kosong | MediaPipe *short-range* hanya mendeteksi wajah di **49–72%** frame wide shot; smoothing merata-ratakan posisi 2 orang (kiri↔kanan) → crop di tengah keduanya; "two_person" memusatkan crop di antara 2 orang yang tidak muat | Detektor **YuNet** (OpenCV, 230 KB) — **100%** frame terdeteksi pada footage yang sama; smoothing tidak melintasi pergantian orang; 2 orang yang tidak muat 9:16 → closeup pembicara; mulai dari wajah pertama |
| 3 | Klip mulai/berakhir di tengah kalimat ("…Aku ringkasin", "…kita mauin", "Nah, tapi…") | Akhir kandidat dipotong di ujung jendela; scene snap bisa menggeser batas ke tengah kata | Semua kandidat (sliding window & AI Moment Finder) dibulatkan ke **batas kalimat**; kalimat yang diawali kata sambung menyertakan kalimat sebelumnya; scene snap hanya di area hening |
| 4 | Crop tidak mengikuti yang bicara | Memilih wajah terbesar | **Deteksi pembicara aktif** dari gerak area mulut (landmark sudut mulut YuNet) + hysteresis 0.7 dtk; pindah pembicara = potongan keras (bukan pan melewati dinding) |
| 5 | Kualitas turun, fps 29.93/29.95 tidak beraturan | 7× encode per klip, salah satunya OpenCV `mp4v`; mux encode ulang | Writer FFmpeg pipe (x264 CRF 16) gantikan `mp4v`; mux = copy; kartu hook + watermark + CTA digabung ke polish render (dari 7 → 4 encode); **30 fps CFR**; upscale bicubic |
| 6 | Caption kecil & terlalu bawah (zona tombol TikTok), emoji tampil ⊙ | Font 42–52 px di kanvas 1920, MarginV 95 | Semua style 60–80 px, ±420 px dari bawah; emoji hanya di style "emoji" |
| 7 | `metadata.json` tidak ada (caption/hashtag AI hilang) | Proses berhenti di klip 10; metadata hanya ditulis di akhir | Metadata ditulis setiap klip selesai (`status: in_progress/done`) |
| 8 | Klip 39–51 dtk | Skor durasi memprioritaskan 38–60 dtk | Titik manis 25–45 dtk |
| 9 | Orang kecil di bawah frame pada wide shot | Batas zoom-in hanya 1.33× | Zoom s.d. ±1.7× (tinggi crop min 600 px) |

Diuji ulang pada potongan footage asli (raw klip 10): wajah 100% terdeteksi (dulu 49%), seluruh frame menampilkan pembicara (dulu 4 dari 6 frame sampel kosong), akhir klip di titik kalimat, output 1080×1920 @30 fps CFR.

## ✅ Verifikasi
- `py_compile` semua file Python OK.
- Tes lapisan AI (24/24): Claude via SDK resmi ke server HTTP tiruan (cek `fallbacks`, beta header, `output_config.effort` + `json_schema`, tanpa `temperature`), Custom/DeepSeek/Mistral dengan retry parameter reasoning, fallback berantai, Gemini (key di header, thought dilewati), parsing ElevenLabs, analyzer end-to-end (moment finder, packaging, normalisasi kategori, LLM gagal → skor lokal utuh).
- Regresi perbaikan sebelumnya 10/10.
- Pipeline render penuh (karaoke, punch zoom baru, thumbnail 9:16) → 1080×1920, audio 48 kHz.
- CLI `--list-providers` / `--test-ai`, endpoint server (`/`, `/providers`, `/test-ai`, `/upload`), Streamlit `AppTest` tanpa exception.
- **Belum diuji dengan API key asli** (tidak ada key di `.env`): panggilan nyata ke Anthropic/OpenAI/Gemini/DeepSeek/Mistral/ElevenLabs, login Google OAuth, upload YouTube/TikTok, transkripsi GPU.
