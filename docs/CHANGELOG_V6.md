# Viral Auto Klip Studio v6

Dari pemroses klip satu halaman menjadi studio konten lengkap untuk pemakaian pribadi (tanpa login, kredit, atau billing).
Jalankan `start.bat` lalu buka **http://localhost:8000/app**. UI lama tetap ada di `/legacy`.

## Fondasi
- **Job tahan restart** (`core/db.py`, `core/jobs.py`): semua proses masuk antrean SQLite (`data/studio.db`), progres + log per job, batal, ulangi, dan tidak hilang saat server mati (job yang terputus ditandai "Terputus" dan bisa diulang).
- **Pustaka aset**: setiap hasil (klip, video AI, audio, transkrip, upload) tercatat di "Video Saya": unduh, ZIP, jadwalkan, hapus.
- **Router media AI** (`core/media_ai.py`), dengan fallback otomatis:
  - TTS: Edge TTS (gratis, tanpa key), OpenAI, Gemini, ElevenLabs, dengan timestamp per kata untuk subtitle karaoke
  - Gambar: Gemini (bisa memakai foto tokoh sebagai referensi), OpenAI, Pollinations (gratis), kartu teks lokal
  - Clone suara, SFX, musik: ElevenLabs. Stok: Pexels / Pixabay
- **API baru** `/api/*` (`studio_api.py`): upload ≤2 GB, file serving yang dibatasi ke folder `output/` dan `data/`, job, aset, jadwal, Brand Kit, tool AI.

## Fitur (halaman di UI)
| Kelompok | Fitur |
|---|---|
| Klip dari video | Analisis AI (hingga 25 klip, fokus kata kunci, opsi kartu hook), **Atur Manual** (tandai mulai/selesai di player), **Split Cam** (geser kotak facecam & gameplay), **Highlight Bola** (lonjakan sorakan + kata kunci komentator, opsi teriakan komentator TTS), **Konten Tempel** (teks/gambar yang bisa digeser, dengan waktu muncul) |
| Studio AI | **Content Creator**, **Story Studio**, **Motion Graphic** (7 preset): wizard Konten → Cerita → Video, sumber topik/YouTube/artikel/PDF/naskah, 25 kategori, 9 gaya penyampaian, 18 gaya visual, tokoh konsisten, storyboard yang bisa diedit (narasi, prompt, gerak kamera, urutan, duplikat/sisip/hapus, pratinjau gambar), narasi TTS, subtitle karaoke, teks kinetik, transisi, musik + auto-duck, caption per platform. Naskah sendiri tetap jalan **tanpa LLM** (dipecah lokal). |
| | Studio AI Chat, Generate Prompt (gambar/video AI) |
| Audio | Text to Speech (riwayat, per paragraf), Clone Suara, Music Generator & SFX, pustaka musik |
| Edit cepat | Video Editor (potong, kecepatan, rasio, overlay, caption, musik, logo), Merge (20 transisi), Loop (crossfade mulus), WA Story HD (dipotong per segmen & dikompres di bawah batas ukuran) |
| SEO & riset | Analitik Viral (skor viral, estimasi pendapatan, jam upload, hashtag; YouTube API atau yt-dlp), Saran Viral AI, Optimasi Judul (4 varian + deskripsi + 15 tag), Transcript AI (subtitle YouTube tanpa download, TXT/SRT/timestamp, terjemahan), Thumbnail Download |
| Distribusi | **Auto Upload** dengan kalender, jadwal per klip, Smart Cadence (sebar N slot/hari), jeda/lanjut, upload sekarang; **Brand Kit** (logo, posisi termasuk posisi bebas, ukuran, transparansi, otomatis di semua alur) |

## Perbaikan lain
- Klip Atur Manual tidak lagi mendapat kartu "Wajib tonton sampai habis" bila kartu hook dimatikan.
- Highlight Bola: deteksi puncak tidak lagi tertipu tepi awal/akhir video.
- Pollinations: menunggu otomatis saat kena batas gratis, watermark dipotong.

## v6.1 — UI baru & perbaikan dari uji end-to-end
**UI/UX (ditulis ulang, satu sistem desain)**
- Ikon SVG seragam menggantikan campuran emoji/glyph (yang tampil berbeda-beda per font).
- Pola halaman sama di semua tool: judul + deskripsi → panel utama (sumber → pengaturan → tombol aksi di footer) + panel samping "cara kerja", hasil selalu di bawah.
- Komponen bersama: field dengan label/satuan/hint yang sejajar, toggle switch, segmented control, kartu pilihan, dropzone drag & drop dengan progres upload, modal dengan header/footer, kartu media dengan badge skor/durasi dan aksi ringkas, panel progres job (persen, waktu berjalan, log, batal, ulangi).
- Stepper wizard untuk Content Creator / Story Studio / Motion Graphic, konfirmasi sebelum aksi destruktif, mode gelap & sidebar mobile.

**Perbaikan dari uji dengan podcast YouTube 57 menit**
- Whisper jatuh ke CPU (`cublas64_12.dll not found`) → DLL CUDA dari `nvidia-cublas-cu12` + `nvidia-cudnn-cu12` didaftarkan otomatis. Transkripsi 57 menit: >15 menit (CPU) → **60 detik** (RTX 3060).
- Deteksi scene untuk snap hanya memindai jendela kecil di sekitar titik potong klip: ±8 menit → **8 detik** (batas scene identik).
- DeepSeek `deepseek-flash` (model reasoning) sering menjawab kosong karena jatah token habis untuk berpikir → otomatis diulang dengan jatah 8192 token. Hasilnya semua klip mendapat judul/caption/hashtag AI (sebelumnya 3 dari 5 kosong).
- `LLM_MODEL` hanya berlaku untuk provider utama (dulu OpenAI ikut memakai nama model DeepSeek).
- Model OpenAI gambar/TTS yang tidak valid (mis. `gpt-4o`) otomatis diganti model yang benar; di Pengaturan kini berupa pilihan.
- Thumbnail diambil dari frame sebelum subtitle dibakar (dulu teks hook bertumpuk dengan subtitle); kartu hook hingga 4 baris (dulu terpotong).
- Content Creator: bila generator gambar gagal total, adegan memakai ulang gambar AI sebelumnya (bukan kartu polos); Pollinations juga diulang saat error 5xx sementara.

## v6.2: Setel otomatis
- Tombol **Pengaturan → Setel otomatis (terbaik)** (`GET/POST /config/recommended`): membaca API key yang terpasang (cek format dan model OpenAI yang benar-benar bisa diakses), lalu memilih provider LLM + cadangan, suara TTS, provider gambar, dan mesin transkripsi (GPU bila ada). Path cookies yang tidak ada dikosongkan.
- ElevenLabs tidak lagi menerima nama suara Edge (mis. `id-ID-ArdiNeural`): otomatis memakai suara Indonesia "Pratama".
- Daftar model DeepSeek diperbarui (`deepseek-flash`, `deepseek-v4-pro`). Default memakai flash: `v4-pro` (model berpikir) butuh lebih dari 17 menit untuk memilih momen dari podcast 57 menit, sedangkan flash ±1 menit.

## v6.3: Subtitle pas, dropdown model, teks UI
- **Transkripsi default ElevenLabs Scribe v2** bila key ada; bila gagal, otomatis pindah ke Whisper GPU. Pada 10 menit podcast tes, Whisper melewatkan 22 detik ucapan dan menahan 23 kata lebih dari 1,5 detik, sedangkan Scribe menangkap 24% lebih banyak kata dan hanya 2 kata panjang. Kata yang hilang juga membuat jump cut memotong bagian yang sebenarnya ada omongannya.
- Timestamp kata dirapikan untuk semua mesin (maks 1 detik per kata, tanpa tumpang tindih). Caption aktif tidak lagi menahan kata selama jeda (maks 0,6 detik setelah diucapkan).
- **Render GPU benar-benar dipakai**: tes NVENC dulu membuat video 64×64 @1 fps selama 0,1 detik (0 frame), jadi selalu gagal dan semua render diam-diam jatuh ke CPU (libx264). Sekarang tesnya valid dan render memakai `h264_nvenc`; klip 50 detik ±80 detik termasuk reframe wajah.
- **Dropdown model**: hasil "Deteksi" disimpan (`data/models_cache.json`) lalu dipakai sebagai pilihan Model di Pengaturan, Tes provider, dan semua form tool (ikut berubah saat provider diganti). Model yang terdaftar tapi ditolak key (mis. OpenAI gpt-5.x tanpa verifikasi organisasi) dikeluarkan dari daftar default.
- "Kemampuan aktif" akurat: suara/gambar OpenAI tidak ditandai siap bila project key tidak punya modelnya; Ollama hanya siap bila servernya menjawab.
- Teks UI: menu konsisten berbahasa Indonesia (Beranda, Teks ke Suara, Gabung Video, Ulang Video, Transkrip AI, Jadwal Upload, Chat AI, Buat Prompt), pilihan di Pengaturan diberi label yang bisa dibaca, label tanpa huruf kapital semua, monospace hanya untuk log/kode, deskripsi kartu tool tidak terpotong, topbar tidak tembus pandang. Field usang Clipping.software dihapus; pengaturan khusus Claude dipindah ke grup Lanjutan.

## v6.4: Klip Live & editing ala editor profesional
**Klip Live** (menu Klip dari video → Klip Live, atau langsung tempel link live di Beranda)
- Siaran YouTube yang masih berlangsung direkam terus dari playlist HLS (`core/live.py`), bisa mundur sampai ±1 jam ke belakang. URL yang kedaluwarsa atau koneksi putus otomatis disambung tanpa celah.
- Tiap 5/10/15 menit rekaman menjadi "jendela" yang dikirim ke antrean (`live_window`): transkrip Scribe → AI pilih momen → render. Klip muncul satu per satu selama live berjalan. Tombol **Selesai rekam** memproses sisa rekaman, **Batalkan** berhenti langsung.
- **Chat live ikut dibaca**: lonjakan pesan ("WKWK", "anjir", "parah"), Super Chat, member, dan gift dipetakan ke waktu video (koreksi ±12 detik jeda penonton). Lonjakan ini, ditambah lonjakan suara streamer, dikirim ke AI sebagai petunjuk momen, menambah skor klip, dan menjadi kandidat sendiri untuk momen game yang minim kata-kata.
- Rekaman memakai thread sendiri, jadi job lain tetap jalan. Potongan lama dihapus setelah diproses (1 jam 1080p60 ±2,5 GB).
- Link live yang ditempel di Analisis AI/Beranda otomatis dialihkan ke Klip Live.

**Layout streamer otomatis**: facecam (wajah kecil yang posisinya tetap di pinggir layar) terdeteksi dari sampel frame, lalu klip dibuat split: facecam di atas, gameplay di bawah, caption di sambungan, kartu hook di panel gameplay. Podcast/vlog (wajah besar) tetap memakai reframe wajah.

**Editing (aktif di preset Dinamis & Maksimal)**
- **Cold open**: potongan 2–4 detik paling nendang (kata penekanan, kalimat utuh, dekat lonjakan chat) diputar di awal, lalu efek rewind bergaya VHS dengan suara kaset sebelum cerita dimulai dari awal.
- **Caption**: tiap baris masuk dengan animasi pop, kata "berat" diberi warna aksen permanen, bayangan lembut agar terbaca di latar terang. **Stiker emoji berwarna** (Segoe UI Emoji) muncul dengan animasi pop di kata reaksi/kata kunci (maks 4 per klip).
- **Sound design**: impact di hook/awal cerita, riser lalu impact di puncak lonjakan, whoosh di tiap punch-in, pop saat stiker muncul. Semua SFX disintesis FFmpeg (tanpa file aset).
- **Audio**: high-pass, denoise ringan, EQ kejelasan suara, kompresor, lalu mastering dua tahap ke **-14 LUFS** + limiter setelah SFX dicampur (sebelumnya -16 LUFS satu tahap, sebelum SFX).

## v6.5: Kamera pembicara, B-roll, efek reaksi
- **Kamera ikut pembicara** (`core/camera.py`): Scribe kini mengirim label pembicara per kata (diarization). Label suara dicocokkan ke wajah kiri/kanan lewat voting gerak mulut, lalu tiap giliran bicara jadi close-up dengan potongan keras. Saling sahut cepat (≥3 giliran pendek) = **layar dibagi atas/bawah**, dan caption pindah ke sambungan agar tidak menutup wajah orang bawah. Monolog >9 dtk diselingi 1 shot reaksi pendengar. Sumber multi-kamera aman: crop dihitung dari wajah yang benar-benar tampil di frame itu. Bila pemetaan suara→wajah tidak yakin, klip memakai reframe biasa.
- **B-roll otomatis** (`core/broll.py`): AI memilih maks 2 momen saat pembicara menyebut hal konkret, gambar diambil dari stok (Pexels/Pixabay bila key ada) atau dibuat AI, tampil layar penuh 1.4–2.2 dtk dengan zoom pelan + whoosh. Caption tetap di atasnya. Tidak dipakai di layout gaming (gameplay sudah jadi visualnya).
- **Layar goyang** di puncak momen (lonjakan chat, tawa dari event suara Scribe, teriakan) + impact.
- **Freeze frame** (preset Maksimal): gambar dibekukan 0.7 dtk di puncak terkuat dengan record scratch dan emoji besar.
- **Nama pembicara (lower third)**: kartu nama geser-masuk. Diisi dari kolom "Nama pembicara" (urut sesuai siapa yang bicara duluan) atau nama channel; dengan kamera pembicara, tiap orang diberi nama saat shot pertamanya.
- **Sensor kata kasar**: kata seperti "anjing", "bangsat", "kontol" di-beep di audio dan disamarkan di caption (a****g).
- Preset: Minimal = + kamera pembicara; Dinamis = + b-roll, goyang, nama pembicara, sensor; Maksimal = + freeze frame. Semua bisa diatur per job di Pengaturan lanjutan.

## v6.6: Koneksi API (beberapa API key sekaligus)
- **Pengaturan → Koneksi API** (`core/connections.py`, `/api/connections`): simpan beberapa endpoint (base URL + API key), mis. gateway yang melayani Claude dan gpt-image dengan satu key. Saat ditambahkan, alamat `/v1` yang benar, format panggilan (OpenAI `/chat/completions` atau Anthropic `/messages`), dan daftar model dideteksi otomatis, lalu dipisah jadi model chat dan model gambar.
- Tiap koneksi muncul sebagai pilihan **Provider AI** (dengan dropdown modelnya sendiri, juga di form tiap tool dan sebagai provider cadangan) dan sebagai **Mesin gambar** (`IMAGE_MODEL`, mis. gpt-image-2 via `/images/generations`). Tombol "Pakai" langsung menjadikannya AI utama / mesin gambar.
- Slot lama "Custom AI" otomatis dipindah jadi koneksi pertama. Setel otomatis mengutamakan koneksi. Disimpan di `data/connections.json` (lokal, tidak ikut git); UI hanya menerima key tersamar.

## Kebutuhan
- Baru: `edge-tts`, `python-multipart` (sudah di `requirements.txt`). Opsional: `pypdf` untuk PDF.
- Isi API key di **Pengaturan** (minimal satu LLM: Gemini dan Groq gratis). Key yang sekarang ada di `.env` masih placeholder.
- Auto Upload butuh `google-api-python-client` + OAuth client (YouTube) atau `tiktok-uploader` + cookies (TikTok).

## Belum dibuat (sengaja / di luar jangkauan)
- Billing, kredit, akun, affiliate, dan API publik: tidak dibuat karena studio ini untuk pemakaian sendiri.
- Editor timeline multi-track penuh, Studio B-roll (segmentasi orang), Motion Graphic 3D sungguhan, dan video AI per adegan (Veo/Kling). Motion Graphic memakai gambar AI + teks kinetik + gerak kamera.
- Upload ke Facebook/Instagram dan Google Drive.
