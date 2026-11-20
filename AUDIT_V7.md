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
