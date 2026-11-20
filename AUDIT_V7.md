# Audit v6.6 → v6.7 (uji nyata: podcast YouTube 42 menit, SLirx7iIbko)

Diuji lewat jalur yang sama dengan tombol "Buat klip" di Beranda (`clip_quick`, hanya URL, semua default).

## Hasil sebelum vs sesudah perbaikan
| | Sebelum | Sesudah |
|---|---|---|
| Total waktu (42 mnt video → 8 klip) | ±42 menit | ±10,5 menit |
| Tahap analisis AI | 7 mnt 20 dtk | 1 mnt 46 dtk |
| Render satu klip 24 dtk | ±190 dtk | ±94 dtk |
| Klip pilihan AI Moment Finder | 2 dari 8 (6 sisanya jendela geser acak) | 8 dari 8 |
| Border hitam + teks branding sumber | ada di semua klip | hilang |

## Temuan & status
| # | Temuan | Status |
|---|---|---|
| 1 | Sumber berbingkai (kartu video + border hitam + "Nizar Monza"/"Self Growth" terpotong) ikut ter-crop 9:16 → bar hitam & teks terpotong di semua klip | **Diperbaiki** `core/framing.py` (deteksi area gambar per baris/kolom; `cropdetect` FFmpeg gagal karena teks putih di border) |
| 2 | 6 dari 8 klip berasal dari jendela geser (awal di tengah pikiran, akhir "Ya, ya. Maksudnya target karir. Ya.") | **Diperbaiki** momen AI diberi bonus, rerank dipangkas bila AI sudah cukup, ekor kalimat ≤2 kata dibuang |
| 3 | Rerank LLM 50 panggilan berurutan (±5 mnt); chunk Moment Finder berurutan | **Diperbaiki** paralel (`AI_RERANK_WORKERS`, 3 chunk bersamaan) |
| 4 | Render polish 100 dtk/klip: b-roll & stiker dibangkitkan sepanjang klip dengan `scale eval=frame` | **Diperbaiki** hanya dibangkitkan di jendela tampilnya |
| 5 | Efek rewind cold open: detektor wajah menebak asal di frame glitch → crop melayang ke mikrofon | **Diperbaiki** kamera ditahan selama rewind |
| 6 | Lower third memakai NAMA CHANNEL ("Insight Berkelas") menimpa tamu/host yang tampil | **Diperbaiki** hanya muncul bila "Nama pembicara" diisi |
| 7 | CTA generik "Follow buat part selanjutnya!" menimpa badan pembicara; hook card menutup mata | **Diperbaiki** CTA di zona aman atas, lebih kecil; hook dinaikkan, teks rata tengah |
| 8 | Logging crash `UnicodeEncodeError` (emoji, cp1252) bila stdout dialihkan | **Diperbaiki** `core/utils.py` |
| 9 | Job ringan (TTS 5 dtk) antre di belakang job klip 40 mnt (satu worker) | **Diperbaiki** `LIGHT` jobs di thread sendiri |
| 10 | `/api/status` melaporkan versi 6.0 | Diperbaiki → 6.7 |

## Belum diperbaiki (disengaja / perlu keputusan)
- Sumber berbingkai: area gambar asli hanya 784 px tinggi → di-upscale ±2,4× ke 1080×1920, agak lembut. Itu batas sumbernya.
- B-roll AI/stok kadang generik (bendera AS untuk kata "Amerika", ruko kosong); mengambilnya ±35 dtk/klip.
- Deteksi wajah ±20–50 dtk/klip dan reframe per frame di Python masih bagian terlama setelah B-roll.
- Stiker emoji (💀🔥💰) terasa murahan untuk konten podcast serius; pertimbangkan mati default untuk kategori edukasi/bisnis.
- Skor lokal memakai VADER (sentimen Inggris) untuk teks Indonesia; hampir tidak informatif.
- Haiku sering menjawab kosong di percobaan pertama (token habis) → +35 dtk retry per chunk.
- Upload YouTube/TikTok belum terpasang (tidak ada OAuth/cookies).
- `start.bat` memakai `--reload` + Python sistem; README masih menulis v3/v6.0 di banyak tempat.
- Banyak perubahan v4–v6 belum di-commit (git status penuh).

## Tidak diuji
Content Creator, Story Studio, Motion Graphic, Editor/Merge/Loop/WA Story, Klip Live, Auto Upload, Brand Kit, Clone Suara, dan sebagian besar halaman UI selain Beranda dan Video Saya. Yang diuji dan OK: video-info, thumbnail, optimasi judul, saran viral, analitik, TTS (ElevenLabs), transkrip YouTube, endpoint jadwal/aset/koneksi.

---

## Putaran 2: layanan lain (diuji dengan klip 12–15 dtk dari video yang sama)
| Layanan | Hasil |
|---|---|
| Editor (potong + 1,25× + 9:16) | OK, durasi 8,0 dtk tepat |
| Gabung Video (fade, 9:16) | OK 26,5 dtk; **temuan:** sumber berbingkai tampil kecil di tengah dengan branding ikut → **diperbaiki** `ffx.auto_crop` (juga dipakai Editor & WA Story) |
| Ulang Video (loop 30 dtk) | OK |
| WA Story HD | OK 720×1280, 2 bagian |
| Atur Manual | OK; **temuan:** CTA "Follow buat part selanjutnya!" menempel di klip manual & menimpa kepala → CTA kini nonaktif di preset Dinamis/Maksimal (opsional lewat `end_cta`) |
| Split Cam | OK (kotak facecam ditentukan pengguna) |
| Highlight Bola | Jalan tanpa error (tidak diuji dengan pertandingan sungguhan) |
| Konten Tempel | **Temuan:** `size` di luar 0–1 membuat FFmpeg gagal dengan pesan mentah → **diperbaiki** (dijepit 0,01–0,3) |
| Content Creator (naskah 30 dtk + render) | OK: naskah bagus, 5 gambar AI, TTS, subtitle karaoke, video 42 dtk dalam 107 dtk |
| Jadwal Upload | **Temuan:** jadwal ke YouTube/TikTok diterima padahal belum tersambung (baru gagal saat jam upload) → **diperbaiki**, kini ditolak dengan petunjuk |
| UI | 29 halaman dibuka: tidak ada error JS, tidak ada overflow horizontal |
| Setel otomatis | OK |

Catatan kecil: nama gaya caption di UI masih id mentah (`tiktok_bold`, `gradient`); log TTS Content Creator mencetak voice kosong (`elevenlabs / `); estimasi "1–3 menit per klip" di Analisis AI sebaiknya diperbarui (kini ±1–1,5 menit).

Masih tidak diuji: Klip Live (butuh siaran live), Clone Suara & Musik/SFX (memakai kredit ElevenLabs), Story Studio/Motion Graphic (satu mesin dengan Content Creator, tidak dirender terpisah), upload sungguhan.

---

## Putaran 3: apakah AI benar-benar "berpikir"? (bukti dari uji nyata)
**Sebelum:** AI memilih momen dari teks saja, lalu hook/judul/caption ditulis di panggilan terpisah tanpa pemeriksaan.
Temuan nyata pada 8 klip tes:
- Caption klip "Sekolah vs belajar mandiri" menulis *"belajar di luar kelas bisa jadi modal finansial"* — **tidak pernah diucapkan** di klip (halusinasi).
- Klip dimulai dari jawaban atas pertanyaan yang tidak ikut terpotong (mis. dimulai "Ya. Second choice pasti kosong…"), atau berakhir tanpa payoff.
- Judul berawalan kategori ("Motivasi Finance: …"); judul menjanjikan "Ini penjelasannya" padahal klip tidak menjelaskan.
- Skor "viral" hanyalah tebakan LLM + keyword + VADER (sentimen Inggris). Tidak ada model yang bisa memprediksi viral secara andal; yang bisa diperiksa adalah proksi: kejelasan, hook, payoff, kelengkapan.

**Sekarang (`core/editor_review.py`, Editor-AI):** tiap kandidat ditinjau dengan konteks ±30 dtk (penanda MULAI/AKHIR), rubrik standalone/hook/payoff/emosi/kejelasan, keputusan keep/trim/reject, batas potong baru, dan penanda risiko (medis/keuangan/hukum/menyesatkan).
Hook & judul wajib menyertakan **kutipan persis** dari klip; kode (bukan LLM) memeriksa kutipan dan kata kunci. Bila tidak terdukung → dicoba ulang dengan umpan balik, lalu fallback ke kutipan nyata.
Hasil uji: 12 kandidat ditinjau (±70 dtk), 2 ditolak, 8 batasnya dirapikan; semua hook/judul lolos pemeriksaan kutipan; halusinasi "modal finansial" hilang.

Batas jujur: Editor-AI masih hanya "melihat" transkrip (bukan audio/ekspresi/visual) dan penilaiannya cenderung murah hati (skor 5–8). Ia memperbaiki kualitas dan membuang yang lemah, tetapi tidak bisa menjamin klip viral.

## Putaran 3: tampilan "pecah" dan polos
Penyebab: sumber 1080p hanya menyisakan area gambar 784 px → di-upscale ±2,4×; setiap generasi encode menambah blok; grade "natural" nyaris tak terlihat; zoom me-resample ulang dan melunakkan.
Perbaikan: denoise ringan (`hqdn3d`) sebelum upscale; Lanczos (bukan bicubic); grade baru **"pro"** (kurva-S, saturasi, bayangan dingin/sorotan hangat) sebagai default Dinamis; sharpen + vignette + grain setelah zoom. Hasil: tepi (kacamata, rambut) lebih tajam, kontras dan warna lebih kaya.
Batas: tanpa super-resolution berbasis model (mis. Real-ESRGAN), sumber 784 px tidak bisa benar-benar tajam di 1080×1920.

---

## Putaran 4: memaksimalkan AI, software, dan hardware
| Sumber daya | Sebelum | Sekarang |
|---|---|---|
| GPU (upscale) | tidak dipakai | **Real-ESRGAN** `realesr-animevideov3` x2 via Vulkan (`tools/realesrgan`, rilis resmi xinntao/Real-ESRGAN v0.2.5.0): frame reframe 540×960 → 1080×1920 bersih tanpa blok kompresi. `AI_UPSCALE=0` untuk mematikan. |
| GPU (encode akhir) | NVENC p4, cq 18, tanpa AQ | **p7 + tune hq**, VBR + lookahead 32, AQ spasial & temporal, 3 B-frame referensi, multipass penuh |
| Encode file antara | NVENC p1 cq 19 (4–6 generasi berturut-turut) | p1 **cq 14** (kehilangan per generasi jauh lebih kecil) |
| CPU 16 thread | 2 klip paralel, rerank 4 thread | **3 klip paralel** (GPU upscale tetap satu-satu agar VRAM aman), panggilan AI 6 paralel |
| AI | 1 penilaian per klip | **2 penilaian independen** per klip (skor dirata-rata, reject hanya bila mayoritas) — `AI_REVIEW_PASSES` |
| Model AI | claude-haiku-5-5 | Tetap haiku: dari 15 model Claude yang terdaftar di gateway Deepwisdom, **hanya claude-haiku-5-5 yang dilayani** (Sonnet/Opus ditolak 400 oleh upstream). `AI_REVIEW_MODEL` siap dipakai bila gateway membuka model yang lebih kuat; bila ditolak otomatis kembali ke model utama. |
| Fallback CPU (tanpa NVENC) | x264 preset fast | x264 preset slow |

Biaya: waktu proses naik (upscale ±9 fps di RTX 3060 laptop, dijalankan berurutan). Uji 42 menit video → 8 klip: ±14 menit tanpa upscale, ±34 menit dengan upscale (sebelum setelan 3 paralel).

---

## Putaran 5: paket efek visual pro (FFmpeg + MediaPipe, tanpa unduhan baru)
Aktif di preset Dinamis & Maksimal (opsi `pro_fx`; Minimal tetap polos).
| Efek | Cara kerja |
|---|---|
| **LUT sinematik** (`core/looks.py`) | LUT 3D 33³ dihitung sendiri: kurva-S per kanal, bayangan teal, sorotan & kulit hangat, saturasi selektif kulit. `film` (default Dinamis) dan `teal_orange` (default Maksimal), diterapkan dengan `lut3d` tetrahedral. |
| **Teks hook di belakang orang** | MediaPipe Selfie Segmentation (±7 ms/frame) → mask orang 2,6 dtk pertama → teks hook besar (Arial Black, putih + baris terakhir kuning, masuk bergeser + fade) digambar, lalu orang ditempel lagi di atasnya mengikuti zoom yang sama. Posisi dipilih otomatis: baris atas harus bersih (≤12% tertutup), hanya baris terakhir boleh tertutup kepala 6–30%. Bila tidak ada posisi yang terbaca → kartu hook biasa. |
| **Flash transisi** | `eq` brightness 0,14 dtk di awal cerita setelah cold open, masuk b-roll, dan momen goyang. |
| **Motion blur punch-in** | `tmix` 3 frame selama 0,16 dtk di awal punch-in zoom; sengaja tidak dipasang di titik potong (di sana tmix = bayangan ganda). |
| **Light leak** | `gradients` radial hangat dari sudut kanan atas, blur, fade masuk-keluar 0,9 dtk di 1–2 momen puncak (bukan saat b-roll). |
| **Headline hook (close-up)** | Bila kepala memenuhi bagian atas (close-up) sehingga teks di belakang orang tidak terbaca, hook ditulis tebal di DEPAN, di area dada di atas subtitle — wajah tidak lagi tertutup kotak hitam. |
Hasil tes penuh (42 menit → 8 klip, 37 menit): flash/motion blur/light leak di semua klip; gaya hook di belakang orang terpilih di 1 klip (shot lebar), 7 close-up masih memakai kartu kotak → diperbaiki dengan gaya headline, diverifikasi pada 1 klip close-up (belum dijalankan ulang untuk 8 klip).
Biaya: ±+5 dtk per klip (segmentasi + filter), selain upscale.

---

## Putaran 6 (v6.8): upscale untuk video nyata + restorasi wajah AI (PyTorch CUDA)
Masalah: `realesr-animevideov3` dilatih untuk anime, sehingga kulit di klip podcast terlihat licin seperti lilin dan detail mata/kacamata tetap kabur.
| Komponen | Sebelum | Sekarang |
|---|---|---|
| PyTorch | 2.15.0 CPU | **2.15.0 + CUDA 13.2** (RTX 3060 terpakai). `start.bat` memasangnya otomatis bila ada GPU NVIDIA. |
| Model upscale | animevideov3 x2 (ncnn, PNG ke disk) | **realesr-general-x4v3** (model Real-ESRGAN untuk video nyata), fp16, frame 432×768 → 1728×3072 → bicubic antialias 1080×1920. Semua di memori GPU, tanpa PNG perantara. ncnn tetap jadi cadangan. |
| Wajah | tidak ada | **GFPGAN v1.4** (`core/face_restore.py`): YuNet 5 landmark → align FFHQ 512 lewat `grid_sample` → GFPGAN bf16 (fp16 rusak, bf16 ≈ fp32) → campur 60% dengan wajah asli (`AI_FACE_WEIGHT`) → mask elips lembut. Wajah kecil (<72 px), menyamping, atau terpotong dilewati. |
| Stabilitas antar frame | – | Noise GAN tetap, pelacak wajah (landmark dikunci saat diam, diredam saat gerak kecil), kekuatan naik/turun bertahap (tidak "pop"), penghalusan temporal peka-gerak di ruang wajah ter-align. |

Uji (potongan 6–8 dtk + 1 klip penuh 24 dtk, klip 2 dari job cffeb6c4b35a):
- Kecepatan: x4plus (ncnn) 0,34 fps, jadi ditolak. Jalur baru 7,8–8,9 fps dengan wajah, 13,9 fps tanpa wajah (jalur lama 9,4 fps). Satu klip penuh: 248 dtk vs 224 dtk (+11%).
- Kedipan pada frame diam (selisih rata-rata area wajah, asli 0,16): versi awal 1,24, lalu 1,08, setelah zona mati landmark **0,30**. Perubahan lapisan detail sama dengan upscale saja (2,57), jadi GAN tidak menambah flicker.
- Visual: wajah menghadap kamera/tiga-perempat jelas lebih tajam (bingkai kacamata, bulu mata, tekstur kulit, bayangan janggut). Bobot 1.0 mengubah bentuk bibir, jadi default 0,6. Wajah menyamping ke mic sengaja tidak diproses, hasilnya sama dengan versi lama.
- Pembanding: `temp/srtest/perbandingan_wajah.jpg`, `temp/srtest/perbandingan_full.jpg`, video `temp/srtest/old|new/`.

Batas: GFPGAN bisa sedikit "merapikan" wajah (kulit lebih mulus dari aslinya). Wajah menyamping dan wajah kecil di shot lebar tidak dipulihkan. Belum dijalankan ulang untuk 8 klip penuh.

---

## Putaran 7 (v6.8): "telinga" untuk AI, momen dipilih juga dari cara bicara
Masalah: pemilihan momen hanya membaca teks. Nada yang naik, kata yang ditekan, jeda dramatis, dan tawa tidak ada di transkrip. Scribe sebenarnya sudah menandai tawa (`audio_events`), tapi tidak pernah dipakai untuk skor.

`core/audio_signals.py` (numpy, CPU, **6 dtk untuk video 42 menit**):
| Sinyal | Cara ukur |
|---|---|
| Energi & nada (F0) | tiap 20 ms; F0 lewat autokorelasi ternormalisasi (uji nada sintetis 85–320 Hz: galat < 1,5%; diam tidak terbaca bersuara). Dinormalisasi **per pembicara** (median 147 Hz & 111 Hz di video uji). |
| Kata yang ditekan (`stress` 0–3) | menonjol dari kata-kata pembicara yang sama ±4 dtk (keras ≥3/6 dB, nada ≥2,5/5 semiton); kata fungsi ("aku", "itu", "kalau", "kek") dibatasi. Versi awal yang memakai median global justru memilih kata fungsi di awal frasa, sudah diperbaiki. |
| Jeda dramatis | jeda ≥0,6 dtk lalu kata yang ditekan (pola punchline). |
| Ekspresivitas segmen | persentil terhadap semua jendela 30 dtk di video yang sama (relatif, bukan ambang mutlak). |
| Tawa / tepuk tangan | dari event Scribe. |
| Efek transisi sumber | bunyi ketik/efek editorial video asli = batas bab; klip yang melintasinya rawan lompat topik. |

Dipakai di 4 tempat:
1. **Skor kandidat**: −5…+6 menurut persentil ekspresi, +3 per tawa (maks +6), −4 bila melintasi transisi bab sumber.
2. **AI Moment Finder**: daftar titik tawa dan "suara meninggi" ikut dikirim sebagai petunjuk terukur.
3. **Editor-AI**: catatan audio per klip (ekspresif/datar, kata yang ditekan, tawa, jeda) untuk skor emotion & hook.
4. **Render**: punch-in zoom, SFX, dan freeze jatuh di kata yang benar-benar ditekan pembicara, bukan hanya kata kunci.

Uji (transkrip yang sama, AI Moment Finder + Editor-AI 2 pass):
- Klip teratas baru: 1720–1762 "Aku penuh privilege, gak bisa bilang uang gak penting" (tawa + ekspresi persentil 98, audio +8,8, emosi Editor-AI 8/10). Dua run sebelumnya tidak memilih momen ini.
- 5 dari 8 klip terpilih berekspresi persentil ≥59. Klip datar (persentil 21–28) tetap bisa lolos bila isinya kuat (payoff 7,5), karena audio hanya sinyal tambahan.
- Contoh punch-in: "Ngapain → Keluar → Buktiin → sukses" (sebelumnya dari kata kunci teks saja).
- Perbaikan sampingan: Editor-AI `max_tokens` 1400 → 2200 (2 dari 24 penilaian gagal karena JSON terpotong).

Batas: ekspresivitas ≠ viral. Isi tetap yang utama. Event tawa bergantung pada Scribe (hanya 6 di video ini). Belum dirender ulang untuk 8 klip penuh.

---

## Putaran 8 (v6.8): audit ulang + clickbait jujur + watermark gambar 9 posisi + penutup klip

### Audit ulang
| Lapisan | Hasil |
|---|---|
| Statis (pyflakes, semua .py) | 1 temuan semu (`prev_e` di TTS ElevenLabs), diberi nilai awal |
| UI (29 halaman) | Tidak ada error JS/console. `favicon.ico` 404 di setiap halaman, diperbaiki dengan ikon inline |
| Smoke test 10 jenis job (via API, klip 12–15 dtk) | Semua selesai: editor, gabung, loop, WA story, TTS, klip manual, split cam, konten tempel, highlight, transkrip |

Bug yang ditemukan & diperbaiki:
- **Highlight Bola**: sumber berbingkai (border + logo channel) tampil kecil di tengah. Sekarang `auto_crop` dipasang seperti di Editor/Gabung/WA Story.
- **Transkrip**: label asal selalu "Whisper" walau memakai ElevenLabs/Groq. Sekarang nama mesin sebenarnya.
- **Klip manual**: nama file `clip_001_viral0_manual1`. Sekarang `clip_001_manual1`.
- Catatan lama: nama gaya caption tampil ramah ("Viral (kuning tebal)", "Gradasi"…), estimasi waktu "±3–5 menit per klip", log TTS "suara default".
- Sebelumnya di sesi ini: form Tambah koneksi API menolak isian valid, dan Tes koneksi berputar terus / lolos lewat provider cadangan.

### Clickbait yang jujur (Editor-AI)
- Prompt paket upload memakai 5 teknik (celah penasaran, kontras, detail spesifik, pernyataan berani, tantangan ke penonton) dan melarang judul generik.
- Field baru **`thumb_text`** (2–5 kata, khusus thumbnail) + 2 alternatif untuk thumbnail B.
- Pemeriksaan keaslian tidak lagi menghukum kata pembingkai ("ternyata", "alasan", "rahasia"…), tapi **angka wajib diucapkan** di klip.
- Thumbnail: teks ≤3 kata memakai huruf raksasa tebal (Arial Black/Impact), mengecil otomatis bila tidak muat.
- Uji (deepseek-flash, 4 momen): "Keluar kuliah baru semester tujuh? Lucu.", "'Uang tidak bisa membeli kebahagiaan' — tapi siapa yang ngomong?", thumbnail "Privilege atau minus?". 1 caption gagal pemeriksaan → cadangan kini kutipan asli + pertanyaan (bukan "Setuju? Komen di bawah!").

### Brand Kit
- **Watermark gambar 9 posisi** (kisi 3×3) + geser bebas. Baris bawah ditaruh di atas ±13% tinggi bawah (area tombol & deskripsi TikTok/Reels).
- **Penutup klip (end card)**: gambar (1–10 dtk) atau video ≤15 dtk milik pengguna, disambung dengan fade 0,4 dtk (video & audio), rasio bebas diberi latar blur. Saklar "Brand Kit (logo + penutup)" di semua proses.
- Uji: klip 27,6 dtk + gambar 3 dtk = 30,2 dtk; + video 4 dtk = 31,2 dtk; klip manual 8 dtk + video 4 dtk = 11,6 dtk. Durasi di metadata klip ikut diperbarui.

Belum diuji: penutup pada Content Creator/Highlight secara langsung (memakai fungsi yang sama); clickbait pada analisis penuh 8 klip.

---

## Putaran 9 (v6.8): pacing & caption yang lebih menahan penonton
| Upgrade | Sebelum | Sekarang |
|---|---|---|
| Kata pengisi | daftar kaku: "eeeh", "hmmm", "ehmmm" lolos (di video uji: "hmm" 13×, "ah" 7×, "eeeh" 3×) | pola (`e+h`, `hm+`, `a+h`, `u+hm`, `e+rm`); kata asli ("emang", "umur", "hem", "era") tidak tersentuh |
| Tempo | tidak ada | **tempo adaptif**: kata/detik setelah jump cut diukur; di bawah 3,15 dipercepat halus ≤1,08× (`setpts`+`atempo`, nada tetap). Data uji: median 2,93 kata/dtk. Klip cepat tidak diubah |
| Caption: kata yang ditekan | warna aksen dari daftar kata teks saja; data tekanan suara hilang saat kata dipecah per layar | kata yang benar-benar ditekan (audio) → warna aksen + "pop" 128% (biasa 120%); gaya baris (Modern dll.) juga menyorotnya |
| Caption: pemecahan layar | tiap 5 kata / tanda titik → kata yatim ("tujuh.") & layar menyeberang jeda/pembicara | potong di jeda >0,45 dtk, ganti pembicara, ≤30 karakter; kata yatim digabung tanpa menyeberang kalimat. 6 klip uji: kata yatim 21 → 8 (sisanya kalimat satu kata), 0 layar menyeberang kalimat |
| Animasi gaya baris | fade 0,15 dtk | pop masuk 86%→100% + fade |
| Headline hook vs caption | caption menempel di bawah headline (dua blok teks berdesakan) | selama headline tampil caption digeser turun dengan jarak 40 px, lalu kembali normal |
| Default gaya caption API | `modern` (beda dengan form UI) | `viral`, sama dengan UI |
| Durasi di metadata | `end - start` (tidak menghitung cold open/freeze/tempo) | durasi nyata file akhir |

Uji: klip 2470–2493 dirender penuh: jump cut 0,6 dtk, tempo 1,06×, 15 kata ditekan dengan pop besar, headline & caption terpisah; ±4 menit per klip (sama seperti sebelumnya, tempo +±10 dtk).

---

## Putaran 10 (v6.8): riset tren pakai data terbaru + update otomatis
Masalah (ditemukan karena GTA VI yang sedang booming tidak muncul):
- Analitik Viral tanpa `YOUTUBE_API_KEY` memakai pencarian YouTube **urut relevansi tanpa filter waktu** dengan kata kunci tetap ("viral indonesia"), sehingga video lama ikut tampil dan topik spesifik tidak terlihat.
- Cache 1 jam, **tidak ada pembaruan otomatis**.
- Saran Viral AI memakai 15 judul hasil pencarian yang sama; Optimasi Judul **murni pengetahuan AI** (tertinggal berbulan-bulan).

Perbaikan (`core/trends.py`, semua gratis tanpa API key):
| Komponen | Isi |
|---|---|
| Video segar | filter resmi YouTube "hari ini" + "minggu ini" diurutkan views (±2 dtk per pencarian) |
| Topik yang naik | n-gram judul yang muncul di ≥2 channel berbeda, dibobot views; alias ("Grand Theft Auto VI" = "GTA VI" = "GTA 6"); kata umum disaring |
| Google Trends | RSS pencarian real-time Indonesia (cache 30 menit) |
| Konteks AI | Saran Viral & Optimasi Judul menerima tanggal hari ini + topik naik + judul tercepat + Google Trends, dengan instruksi bahwa data terbaru mengalahkan pengetahuan lama AI |
| Auto-update | thread latar menyegarkan "Semua" + kategori yang dibuka 24 jam terakhir tiap 60 menit (`TRENDS_REFRESH_MIN`) |
| UI | panel "Topik yang lagi naik minggu ini" (klik → Saran Viral terisi), "Pencarian Google yang melonjak", "diperbarui X menit lalu · otomatis tiap 60 menit"; Saran Viral & Optimasi Judul menampilkan data tren yang dipakai |

Uji: Analitik Gaming → topik naik **GTA 6 (5 channel)**, Roblox, Bus Simulator; video GTA VI di peringkat atas. Saran Viral "gaming" (data 20 Nov 2026) → "Jam Pertama GTA 6 di Indonesia — Ada yang Bikin Kaget", "Berapa Sih Modal Main GTA 6 di Indonesia?", "Aku Bikin Vice City di Minecraft". Auto-update tercatat 20 dtk setelah server menyala.

Batas: TikTok tidak menyediakan data tren publik tanpa login, jadi tren TikTok belum masuk. Umur video dari pencarian cepat diperkirakan dari filter (hari ini ≈0,5 hari, minggu ini ≈3,5 hari).

---

## Putaran 11 (v6.8): video AI generatif — IDE > SCRIPT > AI > IMAGE > VIDEO bergerak
Sebelumnya Content Creator/Story Studio menghasilkan "gambar diam + gerak kamera". Sekarang adegan bisa dihidupkan:
| | Gemini Omni Flash (`gemini-omni-1.2-flash`) | Kling 3.0 Turbo |
|---|---|---|
| Key | `GEMINI_API_KEY` | `KLING_API_KEY` (API Key standar baru; AK/SK lama ditolak vendor) |
| Gambar→video | gambar adegan = bingkai pertama (tokoh tetap konsisten) | `first_frame` |
| Teks→video | ya | ya |
| Durasi / rasio | 3–15 dtk, 9:16 / 16:9 | 3–15 dtk, 9:16 / 16:9 / 1:1 |
Catatan: Veo 3.1 Preview **dimatikan Google 22 Okt 2026**; dokumentasi resmi menyarankan Gemini Omni Flash.

Cara kerja (`core/video_ai.py`): naskah AI kini juga menulis `video_prompt` (aksi + gerak kamera); pengguna memilih "Gerak adegan" (gerak kamera gratis / Gemini / Kling), sumber (gambar→video atau teks→video) dan **maks adegan video AI** (default 4: adegan pembuka + tersebar merata). Klip AI disesuaikan ke durasi narasi (dipotong, atau diperlambat ≤1,25× lalu frame akhir ditahan). Gagal apa pun → adegan itu kembali ke gerak kamera. Pemakaian (jumlah video & detik) dicatat lokal.

Uji: format permintaan & pembacaan jawaban kedua provider diuji dengan respons tiruan sesuai dokumentasi (gambar→video, teks→video, polling Kling, penyesuaian durasi, fallback saat 429). **Belum diuji dengan panggilan sungguhan**: `GEMINI_API_KEY` di `.env` masih berisi teks contoh/placeholder (24 karakter, bukan format `AIza…`), jadi dianggap kosong oleh aplikasi — gambar & suara Gemini juga belum aktif karenanya; `KLING_API_KEY` belum diisi.

---

## Putaran 12 (v6.8): mode Otomatis untuk Analisis AI
Masalah: jumlah klip, durasi, skor minimum, tipe video, dan gaya editing diisi manual. Untuk video panjang lebih parah:
- AI Moment Finder hanya membaca **8 potongan transkrip** (±192 rb karakter). Transkrip ±1.200 karakter/menit → video 4 jam (±288 rb karakter, 12 potongan) **±1/3 bagian akhirnya tidak pernah dibaca AI**.
- Jumlah klip 8 → AI hanya mengusulkan ≤8 momen per potongan dan Editor-AI hanya meninjau ±12 kandidat.

Mode Otomatis (isian dikosongkan = default baru):
| Isian | Otomatis |
|---|---|
| Jumlah klip | ditentukan kualitas: semua klip yang lolos rubrik Editor-AI (≥56), batas ±1 klip per 4,5 menit video (3–30), minimal 3 |
| Durasi | 15–90 dtk; AI memilih awal/akhir alami, Editor-AI merapikan |
| Seluruh video dibaca | batas 30 potongan; bila lebih, potongan diperbesar (tidak dibuang); diproses paralel (6) |
| Skor minimum | gerbang kandidat 45, kualitas akhir oleh Editor-AI |
| Tipe video | default "Deteksi otomatis" |
| Fokus kata kunci | kosong → klip yang menyebut topik yang lagi naik (cache riset tren: semua kategori + Google Trends) mendapat +6 skor; kata umum disaring |
| Gaya editing | "Otomatis (AI per klip)": topik medis/hukum/kebencian → Minimal; ada tawa, emosi ≥7,5, atau lucu/mengejutkan dengan emosi ≥6,5 → Maksimal; lainnya Dinamis |

Uji:
- Transkrip uji dilipatkan jadi 253 menit (tanpa biaya AI): seluruh 253 menit terbaca (14 panggilan); perilaku lama berhenti ±menit 168.
- Analisis penuh video 42 menit (deepseek-flash): batas 10 klip, 26 momen diusulkan, 15 lolos Editor-AI, 10 terbaik diambil (rubrik 59–71), durasi alami 27–63 dtk (dulu mentok 60), 244 dtk.
- Perbaikan setelah uji: kata umum ("Sekarang", "Ytshorts") tidak lagi dianggap topik tren; syarat gaya Maksimal diperketat (dulu 6/10 klip, termasuk emosi 4,5).

Catatan biaya waktu: render ±4 menit per klip (upscale + wajah AI) → 25 klip ±1,5–2 jam; perkiraan ditampilkan di progres job.

---

## Putaran 13 (v6.8): live/gaming, Editor-AI yang melihat, mode farming, upload otomatis
Diuji pada job pengguna: live GTA VI 110 menit (QSPPyA1736U), hasil awal 9 klip yang dinilai "masih kurang".

Temuan dari klip pengguna:
- Klip 14–20 dtk tanpa pengantar/payoff; skor lokal (kata kunci) 28–45 karena penilai dirancang untuk obrolan.
- **Bug**: hook ditulis dari teks sebelum Editor-AI memangkas klip → hook membahas bagian yang terbuang ("Nyari Bigfoot…" padahal isinya loot sniper).
- Topik tren palsu: kata biasa "Langsung" dianggap topik.
- Tata letak facecam + gameplay sudah benar (split), tidak diubah.
- Akar masalah: Editor-AI hanya membaca teks. Penilaian visual (claude-haiku-5-5) atas klip lama: "cowok belanja" visual 3/10 ("tanpa kejadian berarti"), "loot sniper" 4/10, "Lucia punya anak" 3/10 (dibuka menu jeda) — cocok dengan penilaian pengguna.

Perbaikan:
| | |
|---|---|
| Tipe konten | judul/link (gta, minecraft, gameplay, live, /live/…) → `gaming_stream`: kriteria momen reaksi/kejadian game, pengantar + reaksi tuntas, 25–60 dtk |
| Kandidat dari suara | puncak reaksi (persentil ≥90) jadi kandidat tambahan (22 kandidat pada uji) |
| **Editor-AI melihat** (`core/vision_review.py`) | 4 frame per kandidat → model multimodal (otomatis: koneksi dengan Claude/GPT-4o/Gemini) menilai kejadian di layar & kejelasan; rubrik = 60% teks + 40% visual; teks lemah tapi visual ≥7 tidak dibuang |
| Grounding setelah trim | hook/judul/caption diperiksa ulang ke teks hasil pangkas; pakai alternatif yang terdukung atau kutipan asli |
| Durasi | minimum otomatis 20 dtk (gaming 22), trim tidak boleh < 85% minimum |
| Ambang gaming | standalone ≥4, payoff ≥4,5, rubrik ≥46 (teks tidak melihat gameplay) |
| Mode farming | "Otomatis — farming": klip ≥46 rubrik, ±1 per 3 menit, maks 40 — untuk kampanye klip berbayar per views |
| Topik tren | topik satu kata harus nama diri (berhuruf kapital di tengah kalimat) |

Uji ulang (transkrip tersimpan, tanpa render): catatan "live gaming" versi pertama terlalu keras (31/37 ditolak, rubrik "cowok belanja" 62→50) → diperhalus. Dengan penilaian visual: 37 kandidat dilihat, 16 lolos Editor-AI, 6 lolos saringan kualitas (rubrik 52–62; mis. "Gua udah ngegym, pukulan segitu doang?" — adegan pukul-pukulan). Mode farming akan mengambil ke-16. Kesimpulan jujur: live ini sebagian besar playthrough cerita, sedikit momen yang benar-benar kuat.

### Skor
Skor = 35% skor awal (kata kunci + penilaian momen AI) + 65% rubrik Editor-AI (rubrik = rata-rata berbobot hook/payoff/standalone/emosi/kejelasan ×10). Rubrik sengaja jujur ("klip biasa 4–6/10"), jadi 60–70 = layak, 70–80 = bagus, ≥80 = istimewa (semua aspek ≥8). Skor rendah di job ini konsisten dengan penilaian pengguna, bukan salah hitung.

### Upload otomatis (`core/autopublish.py`)
Centang "Upload otomatis ke jam FYP terbaik" di Analisis AI → klip langsung masuk antrean penjadwal (upload sendiri saat jamnya): jendela jam ramai per niche & platform (niche dari isi klip, gaming untuk live gaming, atau dipilih pengguna), akhir pekan pagi digeser +1 jam, menit diacak, klip skor tertinggi di jam paling ramai, maks `AUTO_PUBLISH_PER_DAY` (3) per platform per hari, jarak ≥ `AUTO_PUBLISH_MIN_GAP_H` (2,5 jam), tidak bentrok dengan jadwal yang ada. Judul/caption/hashtag dari Editor-AI + `GLOBAL_HASHTAGS` (Pengaturan) + hashtag per job; hashtag global juga dipakai jadwal manual. Hanya platform yang benar-benar tersambung (YouTube perlu login). Uji (antrean tiruan, tanpa upload sungguhan): 7 klip × 2 platform → maks 3/hari, jarak minimum 2,9 jam, slot hari yang sama dipakai bila jendela masih terbuka. Catatan: platform tidak membuka data "jam FYP" real-time; jendela memakai pola waktu ramai per niche.
