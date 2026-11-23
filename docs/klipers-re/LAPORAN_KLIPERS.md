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

## 11. Perbandingan langsung: link yang sama di viral-auto-klip (23 Nov 2026)

Job `b56836ac0804`, link `MWQtXTTAgeM`. Opsi Analisis AI default, kecuali jumlah klip = 10 (disamakan dengan run 1 klipers)
dan upload otomatis mati. Total 52 menit:
- unduh ±1 menit;
- transkrip ElevenLabs Scribe ±3,5 menit;
- analisis DeepSeek ±6,7 menit;
- paket judul/thumbnail/hook ±12 menit;
- render 10 klip ±28 menit.

| Klip kita | Durasi akhir (dipilih) | Skor | Gaya hook | Teks hook | Sama dengan klipers? |
|---|---|---|---|---|---|
| 0:09:48 | 13 dtk (17) | 68 | sticker | Ayo *NGEDANCE*, guys! Joget? *OKE!* | ya — adegan yang sama dengan r1 0:09:58 "Jason jadi striptis" |
| 0:14:59 | 12 dtk (17) | 64 | stabilo | Gua mau kasih yang *terbaik* buat *ladies* | — |
| 0:20:19 | 21 dtk (21) | 53 | breaking | Yang ngejar *bukan polisi biasa* | — |
| 0:21:44 | 18 dtk (31) | 71 | search | tembak ban dulu... *mesin* juga bisa kena? | ya — r1 0:21:15 "Gua bunuh \| Tembak ban" |
| 0:31:34 | 14 dtk (23) | 60 | stabilo | SERATUS KILO SQUAT PAKAI *JEANS* | — |
| 0:51:49 | 12 dtk (21) | 57 | versus | Sendiri lebih enak \| Berdua banyak blunder | ya — r1 0:51:47 "Lebih enak *sendiri* deh" |
| 1:02:53 | 15 dtk (25) | 61 | breaking | Bisa *nyetir* sambil *nembak* ke kiri | — |
| 1:03:52 | 14 dtk (35) | 59 | strike | Awalnya dendam \| ternyata Billy baik | ya — r1 1:03:45 "Billy ngasih kita kerjaan 3000 dolar" |
| 1:07:51 | 12 dtk (24) | 59 | chat | POV: *gue* udah *nawarin duluan* | ya — r2 1:07:52 |
| 1:16:04 | 29 dtk (42) | 59 | sticker | Gua *takut* sama polisi-polisi ini | ya — r2 1:15:57 |

**Kesimpulan:**
- **Lokasi momen cukup sejalan.** 4 dari 10 adegan run 1 klipers dan 2 dari 5 adegan run 2 juga dipilih kita. Sebagai pembanding, kedua run klipers sendiri hanya sama di 1 momen.
- **Momen terbaik klipers terlewat.** Bazoka "nambah darah cuma 1 dolar" (0:48:49, skor 92 di kedua run klipers) tidak kita ambil. Begitu juga "Solo squad paling jago" (0:46:57) dan "Gua yang tembak tuh" (1:01:00).
- **Perbedaan terbesar ada di durasi.** Klip akhir kita rata-rata 16 dtk (12–29); klipers rata-rata 63 dtk (32–135). Penyebabnya dua:
  1. **Momen yang dipilih memang pendek** (rata-rata 25,7 dtk). Mengisi jumlah klip mengaktifkan standar 15–45 dtk; mode Otomatis memakai 20–90 dtk.
  2. **Jump cut membuang 23–59% isi klip** (rata-rata 38%). Pada siaran gaming, jeda bicara streamer justru berisi gameplay (menyetir, menembak, dikejar). Contoh: momen Billy 34,6 → 14,1 dtk. Klipers tidak memotong jeda: klipnya utuh dan kontinu.
- **Skala skor berbeda** (kita 53–71, klipers 75–92), jadi angkanya tidak bisa dibandingkan langsung.
- **Kecepatan:** klipers ±11 menit (analisis 33 dtk karena sumber sudah di-cache; render 10 klip berurutan ±10 menit). Kita 52 menit, dan tahap paket judul untuk 10 klip saja ±12 menit.

### 11b. Setelah perbaikan durasi gaming (job `ea13e8a6e203`, opsi sama)

Dua perbaikan:
1. **Jump cut sadar-audio untuk siaran gaming.** Jeda bicara hanya dibuang bila suaranya benar-benar sepi
   (energi < 0,2x energi saat bicara). Jeda yang berisi suara game tetap dipertahankan.
2. **Durasi klip gaming 25–90 dtk** bila durasi tidak diisi, juga saat jumlah klip diisi.

| Klip kita | Durasi akhir | Skor | Gaya hook | Teks hook | Sama dengan klipers? |
|---|---|---|---|---|---|
| 0:21:44 | 34 dtk | 74 | sticker | NON LETHAL, GUA *SENENG BANGET* CUY | beririsan dengan r1 0:21:15 "Tembak ban" |
| 0:55:31 | 40 dtk | 60 | chat | Cuma Lima Ratus Dolar, *Mending Balap!* | — |
| 1:00:42 | 39 dtk | 66 | breaking | Dibegal, ban ditembak *anti peluru*?! | ya — r1 1:01:00 "Gua yang *tembak* tuh" (skor 88) |
| 1:03:46 | 45 dtk | 59 | stack | GILA *SERU* *OFF ROAD* BRO | ya — r1 1:03:45 Billy (IoU 0,81) |
| 1:07:51 | 37 dtk | 59 | notif | Dia *polisi!* Gue kira bukan... | ya — r2 1:07:52 |
| 1:12:16 | 35 dtk | 59 | sticker | Gua ditabrak *babi* polisi makin *gila* | — |
| 1:16:04 | 59 dtk | 61 | stabilo | semua *rata* sama *tanah* | ya — r2 1:15:57 |

**Hasil:**
- **Durasi:** rata-rata **41 dtk** (34–59), sebelumnya 16 dtk. Jump cut hanya membuang ≤1,3 dtk dead air per klip, sebelumnya rata-rata 38%.
- **Jumlah klip 7, bukan 10.** Editor-AI menolak 8 dari 15 kandidat (rubrik 32–46). Jumlah klip adalah batas atas, bukan target.
- **Momen yang sama:** 3/10 adegan klipers run 1 dan 2/5 adegan run 2. Dari momen terbaik klipers, "Gua yang tembak tuh" (skor 88) sekarang ikut. Bazoka "cuma 1 dolar" (skor 92) masih tidak masuk kandidat AI Moment Finder.
- **Durasi masih lebih pendek dari klipers** (rata-rata 63 dtk, sampai 135 dtk). Batas 90 dtk sengaja dipertahankan untuk retensi Shorts/TikTok.

## 12. Lapisan dalam — bundle klien, CSS template, changelog (518 entri)

Sumber: bundle `index.BmjhGM2J.js`, CSS publik `index.THCjFetH.css`, dan chunk `ChangelogPage` (518 entri,
10 Des 2025 – 23 Nov 2026). Hasil ekstraksi (JSON) ada di scratchpad sesi; yang dicatat di sini hanya parameter dan perilakunya.

### 12.1 Preset subtitle (37, tabel `u2` di bundle)
Semua preset dirender sebagai **ASS/libass**; pratinjau di browser memakai libass WebAssembly (JASSUB).
Font yang tersedia: Montserrat, Orbitron, Teko, Archivo Black, Fredoka One, Anton, Bebas Neue, Creepster,
Quicksand, Rubik Glitch, Roboto, Lato, Open Sans, Poppins, Oswald, Roboto Mono, Bangers (semua Google Fonts).
Default pengguna baru: **pill-slide** dengan emoji aktif. Posisi bawaan `yOffset` 25, ukuran 60.

| Preset | Font | Teks / sorot / garis | Tebal garis | Kapital |
|---|---|---|---|---|
| pill-slide | Montserrat 800 | #FFF / #a3e635 / #000 | 7 | ABC |
| stabilo | Montserrat 800 | #FFF / #fde047 / #000 | 7 | ABC |
| word-stack | Anton | #FFF / #FACC15 / #000 | 7 | ABC |
| sticker | Fredoka One | #FFF / #fde047 / #111 | 8 | ABC |
| chat-bubble | Poppins | #FFF / #0A84FF / — | 0 | Abc |
| liquid-glass | Poppins | #FFF / #FFF / — | 0 | Abc |
| marker-circle | Montserrat 800 | #FFF / #FF3B30 / #000 | 7 | ABC |
| slam | Anton | #FFF / #FF2D55 / #000 | 8 | ABC |
| jelly | Fredoka One | #FFF / #FDE68A / #7C3AED | 8 | Abc |
| counter-roll | Poppins | #FFF / #4ADE80 / #000 | 6 | Abc |
| live-reaction | Poppins | #FFF / #FDE047 / #000 | 3 | Abc |
| emoji-swap / flip-3d | Montserrat 800 | #FFF / #FACC15 · #22d3ee / #000 | 7 | ABC |
| focus-blur | Poppins | #FFF / #FFF / #000 | 4 | Abc |
| karaoke / pop / bounce / shake / box | Montserrat | sorot #FBBF24 · #22c55e · #eab308 · #ef4444 · box #22c55e | 6–8 | ABC |
| glitch | Roboto Mono | #00FFFF / #FF00FF / #FF00FF | 8 | ABC |
| neon-pulse | Orbitron | #00FFFF / #FF00FF / #0a0a2e | 8 | ABC |
| gradient-slide | Teko | #FFF / #f97316 / #000 | 8 | ABC |
| color-burst | Bangers | #FFF / #ff3366 / #000 | 8 | ABC |
| bold-impact | Archivo Black | #FFF / #fbbf24 / #000 | 8 | ABC |
| outline-pop | Bebas Neue | #FFF / #22c55e / #000 | 8 | ABC |
| street-bold | Anton | #FFD700 / #FFF / #000 | 10 | ABC |
| minimal-apple | Quicksand | #FFF / #60a5fa / #000 | 4 | Abc |
| horror-glitch | Creepster | #FF6B6B / #FF1744 / #1a0000 | 8 | Abc |
| candy-pop | Fredoka One | #FFF / #f472b6 / #000 | 3 | ABC |
| klipers-motion / dynamic | Montserrat 800 | #FFF / #22c55e / #000 (motion: 5 kata/layar) | 8 | ABC |
| glow / wave / shadow / fade / flicker | Montserrat | sorot #f0abfc · #0ea5e9 · #facc15 · #FFF · #fbbf24 | 8 | ABC |

### 12.2 Template teks hook & B-roll (22, array `hm` di bundle + 201 aturan CSS `tm-*`, 67 keyframe)
- **Daftar template:** 18 hook + CapCut Quote + 3 template teks B-roll yang belum kita punya: **Lakban** (Oswald, miring −4°),
  **Pin Lokasi** (ikon pin + pil putih, posisi bawah), dan **Label Sinematik** (Bebas Neue, spasi 0,32em, garis tumbuh).
- **Parameter per template:** `accent`, `position`, `topFrac`/`bottomFrac` (zona aman), `textCase`, dan format bagian `|`.
- **Ukuran:** relatif lebar video (`cqw`, satuan `--u`).
- **Font:** Inter (kartu UI), Archivo (Breaking), Montserrat 800 (Stabilo / Coret), Bangers (Stiker), Anton (Blok, Angka, VS, Stempel),
  Instrument Serif italic (Editorial), mono (Mesin Ketik).
- **Timing kunci (detik):**
  - Breaking: label slide 0,45 → bar wipe 0,55 @0,3 → teks @0,75.
  - Stabilo: kata naik 0,4 → marker menyapu 0,45 @0,7.
  - Chat: titik mengetik 1,15 → pesan pop @1,05.
  - Coret & Ganti: lama 0,4 → coret @0,55 → baru pop @1,05.
  - Polling: pilihan A/B menyala bergantian @0,9 → CTA @2,0.
  - Stempel: kartu 0,45 → stempel slam @1,05 + getar @1,27 + cincin @1,42.
  - Pencarian: ketik per huruf → tombol ditekan → loading → hasil.
  - VS: kubu A/B masuk dari dua sisi → "VS" slam @0,55 + kilat.
  - Pencapaian: koin pop → pil membuka @0,55 → koin berputar @1,05 → bintang @1,15–1,55.
  - Checklist: butir masuk bertahap → kotak dicentang berurutan.
- **Render server:** changelog 14 Okt 2026 menyebut "pratinjau di layar sama dengan hasil video karena memakai font dan animasi yang sama",
  jadi template HTML/CSS yang sama dirender di server.

### 12.3 Perilaku pipeline (dari changelog)
- **Durasi klip:** sejak 15 Okt ditentukan AI per momen, 30 dtk–3 mnt, mengikuti awal dan akhir momen. Klip tidak dipaksa masuk rentang tertentu.
- **Teaser:** momen terkuat diputar dulu sebagai pembuka (otomatis untuk klip ber-hook), dan durasi klip bertambah sepanjang teaser.
- **Buat B-roll:**
  - 6 gaya kolase; AI memilih 2–3 momen per klip dan menyisipkan kolase 1,5–3 dtk dengan judul besar. Suara asli tetap jalan.
  - Latar diambil dari koleksi per gaya × topik (umum, teknologi, makanan, jalan-jalan, bisnis, gaya hidup), tanpa kredit.
  - Orang dipotong dengan garis stiker putih. Bila di detik itu tidak ada orang, wajah diambil dari bagian lain klip.
  - Judul `*kata*` beraksen.
- **Hook B-roll:** potongan orang bisa dirapikan (tambah/buang area); posisi dan ukuran orang serta judul bisa diatur; judul tidak pernah menutupi wajah.
- **Layout:**
  - Gaming dipilih hanya bila gambar menunjukkan facecam kecil. Facecam dikenali dari **ukuran ±1% layar**, bukan posisi; kata "gaming" di judul tidak lagi menentukan.
  - Split hanya saat dua orang terlihat bersama; 3+ orang → blur.
  - Kamera bertahan di pembicara terakhir saat jeda; perpindahan tepat di frame ganti shot.
- **Analisis:**
  - Video >15 menit dianalisis per bagian, lalu kandidat diseleksi ulang; "momen spesifik" kini ikut ke tahap seleksi akhir.
  - Transkrip memakai Groq Whisper; mesin pemilih momennya `generateShortsIdeas`; admin bisa mengganti "otak AI" analisis.
  - Maksimal 3 Quick Process per akun berjalan paralel.
- **Highlight Bola:**
  - Gemini 2.0 Flash membaca video (gawang, aksi, skor), dengan cadangan transkrip komentator.
  - Puncak gol berpatokan pada teriakan komentator; gol offside/VAR dibuang.
  - Micro-clip 2,5–3 dtk menjaga total rekaman di bawah batas Content ID; reel 60–90 dtk (maks 120) dengan Square Blur dan papan skor.
- **Subtitle:**
  - Editor live (17 Nov): ubah kata, geser waktu per kata/kalimat, gabung/pecah kalimat, ganti preset/ukuran/posisi tanpa render ulang.
  - Terjemahan (26 Okt) ke Indonesia, English, Malaysia; bila gagal, tetap bahasa asli.
  - Worker memulihkan font yang tidak valid ke font resmi preset.
- **Infrastruktur:**
  - Backend Node.js (cluster PM2, 3 proses), PostgreSQL, Nginx (prerender untuk bot SEO), SSE untuk notifikasi, bot Telegram dua arah.
  - Deteksi wajah lokal TinyFaceDetector + COCO-SSD.
  - TTS gratis = Edge-TTS; premium = ElevenLabs / Fish Audio / MiniMax.
  - Unduhan YouTube memakai yt-dlp + failover proxy lokal (geo-block).

## 13. Lapisan luar — semua fitur dijalankan sampai jadi (23 Nov 2026, lanjutan)

Dijalankan lewat API resmi dari sesi login (payload sesuai spesifikasi/bundle). Kredit 359 → 338 (21 terpakai).

| Fitur | Job | Biaya | Hasil |
|---|---|---|---|
| Atur Manual (`/manual-segment/submit`) | 367328 | 2 | 42 dtk, `9:16-gaming`, Pill Slide ter-burn (`captioned-local`), 73 kata Whisper, Brand Kit otomatis terpasang |
| Buat B-roll (`/broll-collage/plans` → `/apply`) | plan 5777 → 367436 | 0 | 3 momen @2,5 dtk: komik ("Kena *Scam* Lagi" + label "RUGI DONG!"), kolase, sobek. Latar dari koleksi `gaya:topik-N` |
| Highlight Bola (`/sports-highlight/submit`) | 367413 | 2 | Reel 111 dtk 25 fps, Square Blur, papan skor + daftar pencetak gol bertambah (0–0 → 3–1). Narasi Gemini TTS gratis |
| WA Story (`/wa-story/optimize`) | 367415 | 2 | 35,7 → 15,7 MB, 1080×1920 30 fps |
| Loop (`/seamless-loop/submit`) | 367416 | 3 | 2× + crossfade 0,5 → 83,18 dtk (overlap nyata ±0,95 dtk), 88 MB |
| Konten Tempel (`/overlay/upload-video` → `/overlay/export`) | 367423 | 2 | Latar video blur + video utama + teks + CTA; 28 MB |
| AI Content Creator (`/content-creator/create`) | 367425 | ±8 | 45 dtk, gambar stok/AI + Ken Burns + Pill Slide, Edge-TTS |
| TTS (`/tts/generate`) | — | 0 | Edge-TTS gratis (56 suara); Fish Audio 14; ElevenLabs perlu kunci |
| Generate Prompt (`/prompt-generator/generate`) | — | 1 | Prompt jadi, **tapi blok `<think>` model reasoning ikut terkirim ke pengguna** |

Tidak dijalankan:
- **Story Studio dan export Video Editor:** alur proyek multi-langkah. Biayanya: gambar 2, TTS 1, video 4–6, export 4; editor 1 kredit/menit.
- **Clone Suara:** perlu suara orang + izin pemiliknya.
- **Auto Upload:** akan memposting ke akun sosial.
- **Motion Graphic / Music Generator:** dimatikan admin.

Biaya Content Creator: 8 kredit/menit (stok + AI), 12 (gambar AI saja), AI Video Full nonaktif; Enterprise 2×/jam, 2×/hari, maks 72 dtk.

### 13.1 Forensik file output (9 file, diunduh dengan izin)

| File | Video | Encoder x264 | Bitrate | Audio | Loudness |
|---|---|---|---|---|---|
| Klip Quick Process / Atur Manual / B-roll | H.264 High@4.0 1080×1920 30 fps | CPU core 165, 24 thread, **CRF 18**, setara preset *fast* (subme 6, ref 2, b 3), VBV maks 8 Mbps, GOP 2 dtk | 6–7 Mbps | AAC 48 kHz 2ch 130 kbps | −13,7 LUFS, **true peak 0 sampai +0,2 dBFS (clipping)**, LRA 15–16 |
| Klip ber-Hook B-roll (365905) | sama | sama | 4,9 Mbps | sama | **−9,0 LUFS** (jauh lebih keras), LRA 5,6 |
| Loop & Konten Tempel | **Constrained Baseline** | **ultrafast** (subme 0, tanpa B-frame / AQ / deblock), CRF 23 | 5,5–8,3 Mbps | AAC 44,1/48 kHz | −13,7 |
| WA Story | High@4.1 | **2-pass 2,8 Mbps**, subme 8, AQ 2, psy-trellis 0,15, deblock −1 | 2,8 Mbps | AAC 44,1 kHz | −13,7 |
| Highlight Bola | High 25 fps bt709 | CRF 22 veryfast (subme 2) | 3,7 Mbps | AAC 44,1 kHz | **−20,6 LUFS** (pelan) |
| Content Creator | High **yuvj420p (full range)** | CRF 23 veryfast, GOP 2 dtk | 2,3 Mbps | AAC 44,1 kHz **mono** | −17,3 LUFS |

Semua file: muxer Lavf 62.12 (FFmpeg 8.x). **Tidak ada encode GPU (NVENC)**; semuanya x264 di CPU 24 thread.

### 13.2 Bug & kelemahan baru yang terlihat
1. **B-roll menghapus subtitle.** Klip yang diberi Buat B-roll (365866 → 367436) dirender ulang tanpa subtitle sama sekali,
   padahal klip aslinya ber-subtitle.
2. **Brand Kit menutupi wajah.** Logo ditempatkan di `y=1455` (mulut/dagu streamer) di semua klip gaming, juga di sisipan B-roll.
3. **Tepi potongan orang di B-roll patah-patah** (mask beresolusi rendah).
4. **Konten Tempel:** teks widget jatuh ke font serif cadangan, spasinya rusak di sekitar angka.
5. **Audio tidak konsisten:** true peak > 0 dBFS (clipping) di klip utama; klip Hook B-roll −9 LUFS, Bola −20,6 LUFS,
   Content Creator mono −17,3 LUFS.
6. **Kualitas encode:** Loop & Konten Tempel memakai preset ultrafast Baseline (blok/artefak di bitrate tinggi).
   Content Creator full-range `yuvj420p` (berisiko warna pucat/kontras salah di pemutar).
7. **Token stream** disimpan di memori 1 proses, sementara backend berjalan di 3 proses PM2. Unduhan/pemutaran ±2/3 kali
   gagal 401 "Link sudah tidak valid" (perlu sampai 8 percobaan).
8. **Generate Prompt** membocorkan blok `<think>` (sama dengan Transcript Improve).
9. **Loop:** crossfade 0,5 dtk menghasilkan overlap ±0,95 dtk.

### 13.3 Pelajaran untuk viral-auto-klip
- **Yang sudah lebih baik di kita:**
  - encode NVENC p7 + CQ 17 dengan VBR, atau x264 *slow*;
  - mastering −14 LUFS dengan limiter, intro B-roll ikut −14 LUFS;
  - logo/hook menghindari wajah;
  - subtitle tetap ada setelah burn ulang (tes subtitle live).
- **Yang layak ditiru:**
  - parameter preset subtitle persis (12.1) dan font Google (Montserrat 800, Anton, Poppins, Fredoka One, dll.);
  - template teks B-roll (Lakban, Pin Lokasi, Label Sinematik);
  - durasi klip ditentukan AI sampai 3 menit;
  - Buat B-roll per momen di tengah klip dengan latar per topik;
  - deteksi facecam berbasis ukuran (±1% layar).
