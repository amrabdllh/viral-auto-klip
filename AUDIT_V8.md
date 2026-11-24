# Audit v8 — full audit + E2E live GTA VI 4 jam 41 menit (z6sBvvg_004)

Tanggal: 24 Nov 2026. Jalur uji sama dengan pemakaian sehari-hari: Beranda → tempel link → **Buat klip**
(`clip_quick`, hanya URL, semua default). Tanpa upload ke akun asli (sesuai permintaan).

Sumber: *AKHIRNYA KITA PERGI KE VICE CITY.... Grand Theft Auto VI GAMEPLAY #8* — Windah Basudara, arsip live
16.884 dtk (4 j 41 m), 1080p, 8,55 GB.

## Ringkasan

| Area | Hasil |
|---|---|
| Uji otomatis | 16 file lama + 3 baru (`test_transcript_cache`, `test_ai_gate`, `test_render_progress`) — **19/19 lulus**, 0 skip (termasuk render campaign 1080×1920 60 fps) |
| Audit statis | `compileall` OK, pyflakes tanpa nama undefined, `node --check` 3 file JS OK, rahasia tidak ter-track git |
| Endpoint API | 28 GET + 15 POST/DELETE: semua berfungsi; input salah ditolak dengan pesan jelas; 0 respons 5xx |
| UI | 30 halaman: 0 error JS, 0 request gagal, tanpa scroll horizontal di 375 px |
| E2E | **30 klip** 1080×1920 (26,6–79,7 dtk, median 41 dtk), −14 LUFS, thumbnail AI 30/30, 6 hashtag/klip, 0 jadwal upload terbuat |
| Tool lain | TTS, SFX, Loop, Gabung, WA Story, Transkrip AI, Atur Manual, Split Cam, Editor, Konten Tempel, Highlight Bola, Campaign (60 fps) — **semua OK** |
| Tool AI | Optimasi judul, saran viral, prompt, chat, info video, thumbnail, naskah Content Creator — OK |
| Sesi upload | TikTok **valid**; cookie YouTube (browser) **kedaluwarsa** (YouTube API masih login) |

## E2E: waktu per tahap

| Tahap | Run 1 (kode lama) | Run 2 (kode baru, sumber & transkrip sama) |
|---|---|---|
| Download | 3 mnt (8,55 GB) | 2 dtk (cache sumber) |
| Transkripsi ElevenLabs | 4 mnt 42 dtk | **0 dtk** (cache transkrip, tanpa biaya) |
| Moment Finder | 3,5 mnt (6 paralel) | 4 mnt |
| Editor-AI (2 penilaian/klip + visual) | ±75 mnt, penilaian visual gagal* | 39 mnt, **termasuk** penilaian visual |
| Filter dialog game | ±25 mnt (berurutan) | **27 dtk** |
| Total tahap 42% | **±97 mnt** | **44 mnt** |
| Rencana gaya hook | **22 mnt** (berurutan, GPU menganggur) | **1 mnt 50 dtk** |
| Render | 121 mnt / 30 klip (Real-ESRGAN + GFPGAN ±4 mnt/klip) | dibatalkan di 6/25 (uji tombol Batal) |
| Brand Kit + thumbnail AI | 23 mnt | — |
| **Total** | **4 j 38 mnt** | — |

\* Kesalahan uji, bukan bug aplikasi: `vision_review.py` diedit saat server berjalan dan dimuat pertama kali sesudahnya,
sementara `ai_providers` di memori masih versi lama → penilaian visual run 1 gagal aman (klip tetap diproses).

Catatan: di tengah run 2 konfigurasi AI diubah pengguna ke `9router` (`cc/claude-sonnet-5-5`), jadi paket judul
(18 mnt) dan gaya hook run 2 memakai provider itu.

## Temuan & status

| # | Temuan | Status |
|---|---|---|
| 1 | **Gateway AI lokal (`magpai`, 127.0.0.1:3425) kewalahan**: 6 panggilan paralel → separuh dijawab **503**, sisanya melambat; tiap 503/timeout di-retry (180 dtk × 3) lalu fallback. Tahap "42% mencari momen viral" ±97 mnt — kemungkinan besar penyebab job sebelumnya (`b16574d3f7c6`) tampak macet di 42% | **Diperbaiki** `ai_providers.provider_gate`: endpoint localhost/Ollama maks `AI_LOCAL_PARALLEL` (default 4, terukur aman) panggilan bersamaan; API cloud tidak dibatasi. Request ke gateway lokal tidak di-retry (langsung ke cadangan). Uji: `test_ai_gate.py` |
| 2 | UI diam di "42% mencari momen viral…" selama analisis (>1 jam) | **Diperbaiki** log `analyzer`/`audio_signals`/`editor_review` diteruskan ke log & status job |
| 3 | Filter dialog game berurutan (±50 dtk × 30 klip) | **Diperbaiki** paralel + progres (27 dtk) |
| 4 | Rencana gaya hook berurutan (22 mnt sebelum render mulai) | **Diperbaiki** per kelompok 4 paralel, variasi gaya tetap terjaga (1 mnt 50 dtk) |
| 5 | Progres job diam di **58% selama 2 jam render** | **Diperbaiki** "Render n/N klip — sisa ±X menit", 58→90%. Uji: `test_render_progress.py` |
| 6 | Tombol **Batal** baru berlaku setelah semua klip selesai dirender | **Diperbaiki** klip yang belum mulai dibatalkan; terbukti berhenti di 6/25 |
| 7 | Brand Kit (logo/penutup) encode ulang tiap klip **berurutan** (±40 dtk × 30) | **Diperbaiki** paralel sebanyak `PARALLEL_CLIPS` |
| 8 | **Transkrip tidak di-cache** → "Ulangi" membayar ElevenLabs lagi untuk 4,7 jam audio | **Diperbaiki** cache `data/source_cache/transcripts/` (kunci: path + ukuran + sidik 1 MB awal/akhir + mesin + model + bahasa). Terbukti: run 2 = 0 dtk. Uji: `test_transcript_cache.py` |
| 9 | 9 dari 30 judul (30%) jatuh ke potongan transkrip mentah ("Kanan kiri dua puluh sembilan kilo"): pemeriksa kejujuran angka menolak "29"/"5x" karena ElevenLabs menulis angka dengan **kata** | **Diperbaiki** `editor_review.spoken_numbers`: "dua puluh sembilan"→29, "seribu"→1000, "seratus lima puluh juta"→150; angka karangan tetap ditolak. Uji di `test_clickbait.py` |
| 10 | Semua klip live GTA berlabel niche **finance** (kata "uang/modal/rugi" pasti muncul di obrolan 4,7 jam) — ikut masuk prompt AI packaging | **Diperbaiki** tipe konten live/gaming → niche `gaming` |
| 11 | Respons AI ditolak karena karakter kontrol mentah di string JSON | **Diperbaiki** `parse_json` & `vision_review` memakai `strict=False` |
| 12 | **Keamanan:** `/api/file`, `/api/frame`, `/api/zip` menyajikan cookie sesi YouTube/TikTok, `ytdlp_cookies.txt`, `connections.json` (API key) dan `studio.db`; header `Host` tidak dicek → situs lain via **DNS rebinding** bisa membacanya | **Diperbaiki** file rahasia 403; Host selain localhost/127.0.0.1/[::1] ditolak 403 |
| 13 | Proses ffmpeg yatim dari uji Hook B-roll kemarin (19:13) berjalan ±6 jam CPU, menulis `intro_audio.wav` **140 GB** di `%TEMP%` (versi lama `apad` tanpa batas) | Proses dihentikan, sampah dihapus (C: +132 GB). Pengaman `-t` + `timeout=180` ditambah |
| 14 | Jadwal yang file klipnya sudah dihapus dicoba pra-unggah tiap 30 mnt (kasus nyata `47a7796650c3`) | **Diperbaiki** langsung `failed` + alasan; terbukti di server |
| 15 | `live_media._tool` tidak menemukan `ffmpeg/bin` → 2 uji capture ter-skip | **Diperbaiki** |
| 16 | README: perintah `.venv\Scripts\python.exe` (folder tidak ada; dependency di Python sistem) | **Diperbaiki** + daftar uji diperbarui |

## Perlu tindakan / keputusanmu

- **Cookie YouTube (browser) kedaluwarsa** — perbarui di Pengaturan → YouTube.
- **Saldo DeepSeek habis** (402 Insufficient Balance) — fallback lanjut ke OpenAI gpt-4o; isi saldo atau hapus dari `AI_FALLBACK_PROVIDERS`.
- **Gateway lokal tetap bottleneck**: satu penilaian Editor-AI ±63 dtk dan throughput gateway hampir tetap walau paralel
  ditambah. Untuk video berjam-jam: provider cloud cepat, atau `AI_REVIEW_PASSES=1` (memangkas ±separuh panggilan Editor-AI).
- **Layout gaming otomatis**: panel gameplay masih memuat facecam bawaan stream (wajah tampil dua kali) dan sepertiga
  bawah hanya latar blur. Template campaign & Split Cam sudah benar (kotak eksplisit) — mode otomatis bisa meniru crop itu.
- **Ukuran file**: ±40 Mbps (klip 35 dtk = 175 MB) + `clean.mp4` sama besar untuk subtitle-live → 30 klip ±11 GB
  (output total 16,4 GB). `ENCODE_CQ` 18–20 memangkas ±3×.
- Gaya hook **Polling** menampilkan persentase dekoratif (mis. 52%/46%) yang bukan data nyata.
- Salah eja nama dari transkrip ("Bang **Winda**") ikut ke judul — kosakata kustom/nama channel bisa dikirim ke STT.
- `start.bat` memakai `--reload`: menyimpan file `.py` saat job berjalan me-restart server dan memutus job.
- Kartu "Video sumber 0 B" di Video Saya menyesatkan (sumber ada di `data/source_cache`, terhitung di "Data & upload").
- Sisa `%TEMP%\hbroll_*` (5 folder, ±17 MB) dari uji kemarin — aman dihapus. `/api/status` masih versi `6.8`.

## Biaya uji

ElevenLabs Scribe terpakai **2×** untuk video 4,7 jam: run 1, dan run uji ulang pertama — kunci cache versi awal
memakai mtime file sumber, padahal unduhan dari cache menyentuh file itu (bug di perbaikan sendiri, sudah dibetulkan
dan diuji; transkrip hasil run itu dipakai untuk mengisi cache sehingga tidak terbuang).

## Tidak diuji
- Upload/publish sungguhan (sengaja). Jadwal diuji dengan item 30 hari ke depan lalu dihapus.
- Klip Live (perlu siaran yang sedang live), Auto Pilot dengan channel sungguhan, Clone Suara, Musik AI, burn ulang subtitle
  lewat editor UI (API baca subtitle diuji).
