# Reverse-engineering klipers.pro — laporan uji fitur

Tanggal uji: 23 Nov 2026. Akun: plan Enterprise (411 → ±370 kredit terpakai selama uji).
Sumber uji: `https://www.youtube.com/live/MWQtXTTAgeM` (Windah Basudara, GTA VI #7, 1:21:27).

Metode:
- **REA** (`capture_browser_scenario`): rekam jaringan halaman publik, ekspor bundle JS `index.BmjhGM2J.js` (2,3 MB)
  dan 86 chunk lazy (aset publik di `/assets/`).
- Ekstraksi statis dari bundle: 75 route, ±425 endpoint, spesifikasi API resmi (139 endpoint, dari chunk halaman `/docs`),
  dan prompt AI yang dirakit di browser.
- Uji langsung di akun (browser pane, sesi login oleh user) + logger `fetch` di halaman untuk menangkap payload/response.

File pendukung di folder ini:

| File | Isi |
|---|---|
| `api_spec_dari_docs.md` | Spesifikasi API resmi klipers (21 kategori, 139 endpoint, request/response contoh) |
| `endpoint_dari_bundle.txt` | Semua path endpoint yang dipanggil frontend + chunk asalnya |
| `prompt_ai_klien.txt` | Prompt AI yang dibangun di browser (Optimasi Judul, Saran Viral, caption multi-platform) |
| `paket_dan_setting_publik.json` | `/api/plans` + `/api/settings/public` |

---

## 1. Arsitektur

- **Frontend**: React + Vite SPA (`klipers.pro`), lazy chunk per halaman, Google Sign-In, Umami (`stats.klipers.pro`), Facebook Pixel.
- **API**: `https://api.klipers.pro/api`, upload besar ke `https://upload.klipers.pro/api`. Auth `Authorization: Bearer <token>`
  (token sesi disimpan di `localStorage.auth_token`; token API publik berprefix `kp_`, khusus Enterprise/Lifetime).
- **Server**: path tampak `/var/www/klipers/backend/...`. Video sumber YouTube **di-cache per videoId**
  (`/backend/cache/MWQtXTTAgeM.mp4`) → proses kedua dari sumber yang sama tidak download ulang.
- **Model job**: semua proses = job (`/jobs/:id`, status `pending → processing → completed`, `progress`, `resultData.phase`).
  Quick Process = job induk `quick_process` (analisis) → anak-anak job `shorts` per klip (`parentJobId`).
  Notifikasi lewat SSE `/notifications/stream`. Webhook `callbackUrl` opsional (HMAC `X-Klipers-Signature`, retry 3×).
- **LLM untuk tool ringan**: prompt dirakit di browser, dikirim ke `POST /ai-proxy/groq/generate` (format OpenAI chat),
  yang membalas dengan `model: "gemini-3.5-flash-lite"`. Analisis momen Quick Process dijalankan di server (prompt tidak terlihat).
- **Subtitle "live"**: klip disimpan **tanpa subtitle burn-in** + data kata per kata; editor subtitle bisa ubah teks/gaya,
  dan burn dilakukan saat unduh (`POST /subtitle/:jobId/reburn`). Transkrip klip = Whisper per kata (`whisperTranscript`, `transcriptionData.words`).

## 2. Kredit & batas (teramati)

| Item | Biaya |
|---|---|
| Quick Process / render klip | 2 kredit per menit output, **dibulatkan ke atas per klip** (37 dtk = 2, 117 dtk = 4, 135 dtk = 6) |
| Estimasi awal Quick Process | ditampilkan "~16 kredit" untuk 10 klip; terpakai 18 |
| Split Cam | base sesuai durasi sumber (<1 jam: 1, 1–2 jam: 2, >2 jam: 3) + 2 kredit/menit segmen |
| Hook B-roll + suara | gratis |
| B-roll otomatis (tengah klip) | +1 kredit per klip |
| Konten Tempel export | 2 kredit (maks 5 menit, 1080p) |
| Merge Video | flat 4 kredit (maks 10 video, total 20 menit, hasil disimpan 24 jam) |
| Loop Video | 3 kredit (maks 500×, crossfade 0–3 dtk, maks 10 menit input) |
| WA Story HD | 2 kredit |
| Transcript AI | 1 (ambil) + 1 (improve) + 1 (translate) |
| Generate Prompt / Clone Suara | 1 kredit |
| Klipers TTS | gratis di UI (API: 1 kredit/100 kata), maks 5.000 kata |
| Auto Pilot (Enterprise) | 10 autopilot, 15 video sumber/hari, maks sumber 3 jam, maks 8 klip, cek channel tiap 15 menit |
| Upload sosial/hari (Enterprise) | TikTok 8 (3 akun), YouTube 10 (10 akun), Facebook 10 (3 akun), IG/Threads 0 |

## 3. Quick Process (fitur inti) — **diuji**

Alur: tempel URL → kartu metadata (judul, durasi, estimasi kredit) → form opsi → "Buat Clips".

Opsi form:
- **Resep** (preset tersimpan, `/qp-recipes`, maks 10), Bahasa output, **Jenis/layout**, Durasi (`auto 30s–3m`, `30s`, `60s`, `90s`), Target clips (UI 5/10; API 1–25).
- **Jenis (genre/layout)**: Auto Detect, Gaming, Deteksi Wajah, Split 2 Wajah, Dynamic (auto layout per scene: 1 orang crop, 2 split, 3+ blur), Highlight Bola,
  rasio 9:16 / 1:1 / 4:3 masing-masing Crop/Blur/Padding, 16:9 Zoom 25%/50%, Keep Original.
  `auto` menganalisis 30 frame sampel lalu memilih layout.
- **Hook Otomatis** (default ON): AI menulis hook pendek per klip, tampil 3/5/8 dtk/penuh, + efek suara.
  - Mode **Teks**: 19 gaya — Breaking Bar, Stabilo, Stiker Pop, Notifikasi HP, Chat Bubble, Blok Bertumpuk, Coret & Ganti,
    Mesin Ketik, Editorial, Balas Komentar, Polling, Stempel Fakta, Postingan X, Angka Besar, Pencarian, Duel VS, Pencapaian,
    Checklist, CapCut Quote; atau "Otomatis/AI pilih per klip".
  - Mode **B-roll + suara**: video freeze di awal, potongan orang dari video ditaruh di latar bergaya (Kolase Stiker, Sampul Majalah,
    Kertas Sobek, Pop Art Komik, Scrapbook, Arsip Investigasi, Sampul Podcast), narator membaca hook (suara: Aita, Abi, Arunika, Ahmad,
    Akbar, Andra, Citra, Deandra Putri; payload mengirim `voice:"Kore"` = nama suara Gemini TTS), maks 7 dtk, gambar pembuka jadi sampul.
- **B-roll otomatis** (tengah klip, +1 kredit/klip; tidak bisa bareng Hook B-roll).
- **Logo Brand Kit**, **Sertakan sumber video** (credit overlay), **Momen spesifik** (maks 20 kata kunci), **Rentang waktu proses**.
- **Subtitle**: 38 preset (lihat §7) + tab Settings (audio/auto-detect bahasa, terjemahkan, font, kapitalisasi ABC/Abc/abc,
  warna teks/highlight/outline/background, ukuran font, tebal garis, kata per baris (auto, maks 4), posisi Y).

Payload `POST /ai/quick-process` (tertangkap persis):
```json
{"youtubeUrl":"…","metadata":{…},"clipModel":"auto","genre":"auto","clipLength":"auto","specificMoments":[],
 "startTime":0,"endTime":4887,"captionPreset":"pill-slide","captionLanguage":"auto","outputLanguage":"id",
 "subtitlePosition":25,"subtitleSettings":{"fontName":"Montserrat","fontWeight":"800","fontColor":"#FFFFFF",
 "highlightColor":"#a3e635","strokeColor":"#000000","backgroundColor":"none","strokeWidth":7,"wordsPerSubtitle":"auto",
 "textCase":"uppercase","animationStyle":"pill-slide","fontSize":60,"emojiEnabled":true,"yOffset":25,"keepUncaptioned":false},
 "aspectRatio":"9:16","gamingCamPosition":"bottom","gamingSourcePosition":"auto","faceTracking":true,"gamingLayout":false,
 "splitScreen":false,"voiceDubbing":false,"targetClips":5,"audioLang":"original","cropMode":"auto","mainRatio":"original",
 "sourceCredit":{"enabled":false,…},"hookOverlay":{"enabled":true,"mode":"broll","style":"auto","voice":"Kore"},
 "autoBroll":null,"branding":{"mode":"kit"},"blurLogos":true,"showScoreboard":true,"sportsLayout":"SQUARE_BLUR"}
```

Hasil job induk (`resultData.clipJobs[]`): `start`, `end`, `duration`, `title`, `description`, `whyViral`, `viralScore`,
`transcript`, `payoffString` (mis. `"49m12s"`), `clipJobId`. Analisis 1:21:27 selesai ±33 dtk setelah submit (sumber sudah di-cache).

Hasil uji run 1 (10 klip, Hook teks auto):

| Klip | Durasi | Skor | Gaya hook (dipilih AI) | Teks hook |
|---|---|---|---|---|
| 598–635 | 37 | 82 | sticker | `Jason *jadi striptis* guys` |
| 720–752 | 32 | 80 | search | `stripties paling *disukai cewek* cewek` |
| 1275–1310 | 35 | 76 | strike-swap | `Gua bunuh \| Tembak ban` |
| 1514–1649 | 135 | 75 | editorial | `*tapi jason* botak *ganteng banget* ya` |
| 2364–2481 | 117 | 78 | big-stat | `10000 \| investasi buat keluarga` |
| 2817–2856 | 39 | 86 | achievement | `Solo squad *paling jago*` |
| 2929–2971 | 42 | 92 | search | `yang nambah darah *cuma 1 dolar*` |
| 3107–3228 | 121 | 85 | chat | `Lebih enak *sendiri* deh` |
| 3660–3695 | 35 | 88 | breaking | `Gua yang *tembak* tuh` |
| 3825–3865 | 40 | 79 | stabilo | `Billy ngasih kita *kerjaan 3000 dolar*` |

Konvensi teks hook: `*kata*` = kata yang di-highlight, `|` = pemisah dua bagian (untuk strike-swap/big-stat/duel).
Hook diambil **dari kutipan ucapan asli** di klip, bukan judul.

Pipeline per klip (`resultData.stageTimes`, detik): `persiapan` 0,4 → `transkrip` 5–25 → `deteksi_wajah` 8–33 → `render_utama` 20–133
→ `logo` 11–116 → `lainnya` 29–68; total 75–363 dtk. Klip dirender **berurutan** (antrean), bukan paralel: 10 klip butuh ±10 menit.
Output 1080×1920, ±0,8–1 MB/detik (bitrate tinggi, 30 MB untuk 37 dtk).

Layout gaming auto-detect pada video Windah: gameplay di atas, facecam di bawah. Face detection disimpan di job
(`cachedFaceDetection.boundingBoxes`, 1 sampel/10 dtk).

Run 2 (5 klip, Hook B-roll + suara): momen yang dipilih **berbeda** dari run 1 (tidak deterministik), kecuali momen bazoka 2929–2971 (skor 92 di kedua run).
Momen terpilih: 234–282 (82), 1121–1248 (88), 2929–2971 (92), 4072–4103 (86), 4557–4604 (95). Hasil render: lihat §10.

## 4. Mode video lain

| Fitur | Status uji | Temuan |
|---|---|---|
| **Split Cam** | ✅ diuji (segmen 48:49–49:31) | Editor drag area gameplay (default 56%×100% kiri) + facecam (20%×26% di 2%,72%), rasio split 60:40/65:35/70:30 atau custom 50–80%, tukar atas/bawah, maks 20 segmen, tab Subtitle/Hook B-roll/Text/Filter/Audio/Overlay. Hasil: gameplay 1080×1152 di atas + facecam 1080×768 di bawah, subtitle preset `pop`. Payload `gaming-split-camera/submit` memuat `facecamArea`, `gameplayArea` (persen), `gameplayHeightPercent:60`, `filterSettings` (brightness/contrast/saturation/vignette), `audioSettings`, `titleOverlaySettings`, `sceneOverlays`. Selesai <1,5 menit. |
| **Atur Manual** | 👀 UI | Tempel URL / upload (2 GB) → editor segmen manual (endpoint `manual-segment/submit`). |
| **Highlight Bola** | 👀 UI | 2 kredit; Format Murni, Mode Blur, Ukuran 16:9; butuh suara komentator; opsi ElevenLabs API pribadi. Payload: `layout STATIC_CROP|SQUARE_BLUR`, `showScoreboard`, `blurLogos`, `antiCopyright`, `targetResolution:1080p`. |
| **Konten Tempel** | 👀 UI | Komposer overlay: Background + Main Video (upload/URL/library) + Overlay Image + Main Text + CTA label + Watermark; template Berita TV / Breaking / Review / Promo / Story; layout ranking; BGM upload + mix (BG 0% / main 100%); subtitle. Export 1080p maks 5 menit, 2 kredit. |
| **Merge Video** | ✅ diuji (2 klip, Fade) | Sumber: upload (maks 100 MB/file) atau Galeri Klipers / Klipers-AI. Transisi: none/fade/dissolve/wipeleft/slideright/circleopen/smoothup (nama xfade ffmpeg), `transition_duration:1`. Alur session: `POST /merge-video/sessions` → `PATCH items` → `POST /submit` → job `merge_video`. |
| **Loop Video** | 👀 UI | Upload MP4/WebM ≤500 MB ≤10 menit; jumlah loop 2–500 atau target durasi; crossfade 0–3 dtk; custom audio; auto-upload Google Drive. 3 kredit. |
| **WA Story HD** | 👀 UI | Preset WhatsApp Story / IG Reels / TikTok; 1080p, ≤16 MB, maks 90 dtk (WA). 2 kredit. |
| **Video Editor** | 👀 UI | NLE timeline: Edit, Media, Audio, Teks, B-Roll, Stiker, **Quran**, Rasio, Filter, Template, AI, Atur, Lajur. Export maks 300 dtk / 100 klip (`editor/export-cost`). |
| **Studio B-roll** | 👀 UI | Pilih klip jadi → rapikan potongan orang di pembuka & B-roll (`/broll/:id`, `moments/:id/shots/:r/ai|search`). |
| **Thumbnail** | 👀 UI | Hanya download thumbnail original YouTube; "AI Thumbnail Director" masih COMING SOON. |

## 5. Studio AI

| Fitur | Status | Temuan |
|---|---|---|
| Story Studio | 👀 UI | 4 langkah: Ide (sinopsis, gaya visual, suara) → Naskah (AI per adegan) → Storyboard (gambar, suara tiap pemeran, SFX per adegan) → Editor (subtitle, musik otomatis, export). Kuota AI 150/hari, 50 proyek. |
| AI Content Creator | 👀 UI | 21 kategori (Storytelling, Misteri & Horror, Gaya Komik, Berita, Anak, Opini, Roasting, Sejarah Nabi, Kisah Sahabat, Sejarah, Biografi, Sains & Tekno, Mental Health, Mitos vs Fakta, Keuangan, Fakta Unik, Kesehatan, Luar Angkasa, Cerita Rakyat, True Crime, Review Film) × 10 gaya; fokus Otomatis/Topik/Tokoh; sumber Topik / YouTube / Artikel-PDF. API 24 endpoint (lihat spec). |
| Motion Graphic | ⛔ dijeda admin | 13 preset (Bedah 3D, Proses 3D, Jurnalisme Visual, Kolase Kartun, Sinematik, Geografi, Finansial, Aksi Berkelanjutan, Sketsa Vintage, Panggung Kartun, Katalog Putih, Tipografi Kinetik, …) — animasi sinkron ke kata narasi. |
| Text to Speech | 👀 UI | Klipers TTS (gratis) = suara Edge-TTS: Gadis, Ava, Emma, Ardi, Andrew, Brian, William; Premium TTS / Fish Audio / ElevenLabs butuh API key sendiri. Riwayat audio 30 hari. |
| Clone Suara | 👀 UI (tidak dijalankan) | Upload 1–2 menit MP3/WAV/M4A/FLAC ≤50 MB, 11 bahasa, opsi bersihkan suara latar, centang izin pemilik suara; 5 slot, 5 clone/hari, 1 kredit. |
| Music Generator | ⛔ dinonaktifkan admin | — |
| Generate Prompt | 👀 UI | Prompt Generator / Product Pro / Ad Generator; 13 gaya (Auto, Creative, Precise, Cinematic, …); gambar referensi opsional; 1 kredit. |

## 6. Riset & distribusi

| Fitur | Status | Temuan |
|---|---|---|
| Analitik Viral | ✅ diuji | 24 video trending YouTube (region ID) + skor viral, views, engagement, estimasi pendapatan; filter 8 kategori; refresh tiap ±3 menit. "Waktu upload terbaik" Jum 18–20 / Sab 10–12 / Min 14–16 (tampak statis). |
| Saran Viral AI | ✅ diuji | `GET /trending/category/<kat>?maxResults=5` (data YouTube nyata) → disisipkan ke prompt → `/ai-proxy/groq/generate`. Prompt memilih acak satu "FOKUS ANALISIS HARI INI". Output JSON: viralExamples, suggestions (judul + 4 alternatif, tags, 30 hashtag, deskripsi, 4 hook, skor, jam upload), trendingKeywords, bestPractices, competitorInsights. Ada fungsi perbaikan JSON terpotong. |
| Optimasi Judul | ✅ diuji | Prompt di klien: 5 opsi judul 80–100 karakter (SEO / Viral / CTR / Balanced / Niche) + skor SEO/viral/CTR, 15 tag, 25 hashtag, deskripsi 250–300 karakter, seoKeywords, hookSuggestions, thumbnailIdeas, bestUploadTime. Tab YouTube Extractor: ambil metadata video, analisis video, regenerasi 5 judul 50–65 karakter. |
| Transcript AI | ✅ diuji | `GET /yt-transcript?videoId=` = caption YouTube (auto-caption bergulir → **baris dobel tidak dibersihkan**). Improve (`POST /transcript/improve`), Translate (15 bahasa), ekspor TXT/SRT/PDF. |
| Data Analysis | ✅ diuji | `GET /data-analysis/analyze`: hanya YouTube berisi data nyata (`REAL_API`); X/TikTok/Google Trends kosong; Facebook berisi tren & URL karangan LLM. |
| Auto Pilot | 👀 (sudah ada 1, dijeda) | Ikuti channel YouTube; filter tipe video/live, durasi 600–10800 dtk, include/exclude kata; opsi sama dengan resep Quick Process; publish mode `review`; `minBalance` 20 kredit; notifikasi Telegram. |
| Auto Upload (Integrations) | 👀 UI (tidak posting apa pun) | Kalender/Daftar/Workflow jadwal post; YouTube/TikTok/Facebook/IG/Threads config; caption per platform dibuat LLM dengan prompt di klien (youtubeTitle ≤80, 5–8 tag, tiktokCaption 3–5 hashtag, instagramCaption + CTA, threadsText <500, variasi anti-deteksi duplikat). `scheduled-uploads/from-job`, `bulk`, `cancel-batch`. |
| Brand Kit | 👀 UI | Satu logo untuk semua klip, posisi kiri atas / kanan atas / bebas, ukuran %, transparansi, peringatan safe-zone tombol Reels/TikTok; otomatis di Quick Process, Atur Manual, Split Cam, Hook B-roll. |

## 7. Preset subtitle

Dari spesifikasi API (`animationStyle`/`preset`): `no_animation, karaoke, pop, bounce, glitch, shake, glow, shadow, fade, wave, box,
klipers-motion, neon-pulse, gradient-slide, color-burst, bold-impact, flicker, outline-pop, minimal-apple, horror-glitch, candy-pop,
street-bold, dynamic, pill-slide, stabilo, focus-blur, sticker, flip-3d, chat-bubble, emoji-swap, counter-roll, marker-circle, slam,
jelly, live-reaction, liquid-glass, word-stack`. UI: 38 (27 "New", 13 "Populer"), termasuk "NO CAP".

Field gaya: `fontName, fontSize, fontColor, highlightColor, strokeColor, strokeWidth, wordsPerSubtitle (auto|n), textCase,
backgroundColor, emojiEnabled, yOffset/marginV, alignment (libass), language, translateTo, keepUncaptioned`.
Default render memakai libass (ASS) — `alignment` = kode libass.

## 8. Kelemahan klipers yang teramati

1. `transcript/improve` mengembalikan blok `<think>…</think>` dari model reasoning (tidak dibuang).
2. Transcript AI tidak membuang baris dobel dari auto-caption YouTube.
3. Data Analysis: Facebook/X/TikTok/Google Trends tidak punya data nyata; Facebook diisi karangan LLM.
4. Pemilihan momen tidak deterministik antar-run.
5. Render klip berurutan per user (10 klip ±10 menit).
6. Judul video di preview Split Cam berbeda dengan metadata (judul live berubah; metadata diambil dari cache yang berbeda).
7. Thumbnail AI & Motion Graphic & Music Generator belum/tidak aktif.

## 9. Perbandingan dengan viral-auto-klip (ringkas)

Yang sudah ada di project: daftar halaman v6 (Analisis AI, Atur Manual, Split Cam, Highlight Bola, Konten Tempel, Content Creator /
Story Studio / Motion Graphic, TTS & clone, Editor, Merge, Loop, WA Story, Analitik Viral, Optimasi Judul, Transcript, Auto Upload, Brand Kit),
face reframing MediaPipe, Whisper per kata, 7 gaya caption (`viral, modern, minimal, karaoke, emoji, tiktok_bold, gradient`), skor viral,
upload YouTube/TikTok headless.

Gap utama terhadap klipers (urut dampak):

1. **Hook overlay bergaya per klip** — 19 template visual + AI memilih gaya per klip, teks dari kutipan asli dengan markup `*highlight*`
   dan `|`. Project belum punya sistem `hookOverlay` setara (tidak ada `hook_overlay`/style template).
2. **Hook B-roll + suara** — freeze frame + cut-out orang + latar bergaya + narasi TTS (maks 7 dtk) yang juga jadi sampul.
3. **Preset subtitle** — klipers 37 animasi (pill-slide, stabilo, word-stack, chat-bubble, liquid-glass, …) vs 7 di project.
4. **Subtitle live + reburn** — simpan klip tanpa burn-in + data kata, edit teks/gaya setelah render, burn saat unduh.
5. **Layout gaming otomatis** (deteksi facecam sudut → panel atas/bawah) dan **Dynamic** (per scene: 1 orang crop / 2 split / 3+ blur), `speakerFocus`.
6. **Resep** (preset opsi tersimpan) dan **Auto Pilot** (pantau channel tiap 15 menit → klip otomatis → antrean review).
7. **Momen spesifik** (maks 20 kata kunci) dan **rentang waktu proses** sebagai input analisis.
8. **Cache sumber per videoId** supaya proses ulang tidak download ulang.
9. Merge dengan transisi xfade + durasi 1 dtk; Loop dengan crossfade.

Catatan: ini daftar perilaku yang teramati, bukan kode klipers. Prompt di `prompt_ai_klien.txt` adalah teks milik klipers —
pakai sebagai acuan struktur/output, bukan disalin mentah.

## 10. Hasil run Hook B-roll + suara

Klip 365905 (234–282, skor 82) selesai; 4 klip lain masih antre saat laporan ditulis (job 365906–365909).

`resultData` klip 365905:
```json
"hookBroll": {"style":"kolase","title":"Brando Kena Teror Gagal Paham Sama Geng Motor?","voice":"Kore","language":"id"},
"hookBrollStatus":"ok", "hookBrollSeconds":3.65, "hookBrollCast":"composed",
"hookBrollRecipe": {"photo":null,"people":[
  {"t":21,"box":{"x":0.8028,"y":0.4969,"w":0.1947,"h":0.2873},"face":{"x":0.8234,"y":0.5541,"w":0.1106,"h":0.1724}},
  {"t":37,"box":{"x":0.2622,"y":0.0197,"w":0.2957,"h":0.9685},"face":{"x":0.3666,"y":0.1403,"w":0.1014,"h":0.1641}}]},
"stageTimes": {"persiapan":6.8,"transkrip":27.9,"deteksi_wajah":40.1,"hook_broll":107.7,"render_utama":53.8,"logo":57.3,"lainnya":57.6,"total":351.4},
"costBreakdown": {"total_cost":2,"addon_credits":0}
```

Cara kerja yang terlihat:
- Teks hook B-roll = **judul klip** (bukan kutipan seperti mode Teks), dibacakan TTS (`Kore`), durasi mengikuti narasi (3,65 dtk).
- AI memilih beberapa frame (`t` detik) berisi orang, menyimpan bbox tubuh + wajah (koordinat ternormalisasi 0–1),
  lalu menyusun cut-out ("composed") di atas latar gaya terpilih.
- Visual (gaya kolase): latar kertas sobek/film strip, cut-out streamer (facecam) + karakter game, judul huruf besar hijau-kuning
  ber-outline di tengah atas.
- Tahap `hook_broll` menambah ±108 dtk per klip; total ±6 menit untuk klip 48 dtk.

Merge (job 365916): 2 klip 40 + 35 dtk dengan "fade" → output 75 dtk (transisi tidak memotong durasi, jadi bukan xfade overlap).
