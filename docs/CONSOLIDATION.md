# Konsolidasi Clipper → viral-auto-klip

Dokumen ini mencatat **arah baru proyek**: viral-auto-klip (vak) dipakai sebagai
codebase, lalu fitur yang hanya ada di Clipper dipindahkan masuk.

Dibuat: 2026-11-20 · Basis: vak v6.8 · Sumber Clipper: branch `refactor/core-modular`

---

## 1. Kenapa arahnya dibalik

Audit 8 pembaca paralel atas kedua codebase menyimpulkan:

| | Clipper | vak |
|---|---|---|
| Baris kode | ~7.600 (2.263 di `clipper.py`) | 16.913 |
| Test | 17 suite hijau | **0** |
| Encoder | libx264 CPU (crf16) | NVENC GPU (~1,7× lebih cepat, VMAF setara/lebih baik) |
| Fitur | 4 stage + campaign | 30 tool + UI web + job queue |
| Campaign/MotionKlip | **ada** | tidak ada |

Jadi: vak menang di kecepatan, cakupan, dan UI. Clipper hanya punya 3 hal yang
tidak ada di vak.

---

## 2. Yang dipindahkan (MOVE)

| Fitur | Sumber Clipper | Tujuan di vak | Status |
|---|---|---|---|
| Campaign / MotionKlip | `clipper.py:636–972` (339 baris) + 2 preset | `core/campaign.py` | **selesai** |
| Job handler campaign | `run_campaign` | `core/tools.py` → `campaign_render` (`background=True`) | **selesai** |
| Halaman UI | — | `web/pages.js` → `PAGES.campaign`, NAV, `/api/campaign/presets` | **selesai** |
| Aset campaign | `C:/Users/ipank/Downloads/*` | `assets/campaign/` | **selesai** |
| Preset campaign | `motion-klip-gtavi*.json` | `presets/`, path relatif + `schema_version` | **selesai** |
| Spike detector | `chat_spikes.py` (119 baris) | `core/chat_spikes.py` + dipakai di `core/live.py` | **selesai** |
| YouTube browser uploader | `upload_youtube_browser.py` (748) | `core/youtube_browser.py` | belum |
| Cookie refresher | `refresh_youtube_cookies.py` (191) | `core/youtube_browser.py` | belum |
| Test terpilih | 17 suite | `tests/` | sebagian (`test_campaign.py`) |

---

## 3. Yang diputuskan vak menang (jangan port)

- **Pipeline viral** — `core/analyzer.py` + `core/tools.py` (skoring 8 komponen, Moment Finder, packaging)
- **Caption** — `core/utils.generate_ass_subtitles` (karaoke `\k`, 8 gaya)
- **Transkripsi** — `core/transcriber.py` (Scribe + Groq + Whisper lokal)
- **TTS** — `core/media_ai.py` (edge/openai/gemini/elevenlabs)
- **Upload YouTube/TikTok** — `core/publisher.py` + `core/scheduler.py`
- **Live capture** (orchestration) — `core/live.py` (Recorder, ChatReader, state, stop)
- **Config & kredensial** — `config.py` + `core/settings.py` + `core/connections.py`
- **CLI/API** — `cli.py` + `studio_api.py`

Catatan: **buffer HLS presisi** dari Clipper (`live_media.py`) masih kandidat
MOVE berikutnya — vak hanya punya jendela kasar 5/10/15 menit.

---

## 4. Yang dibuang (DROP)

`vav_klip_src/` (duplikat vendored), `framing.py` root (dead code), `login_zoro.py`,
`publish_batch.py`, `upload_tiktok_queue.py`, `upload_youtube_queue.py`,
dukungan Twitch, test e2e pipeline lama, tool backfill deskripsi YouTube.

---

## 5. Perubahan pada core vak

| File | Perubahan |
|---|---|
| `core/ffx.py` | `run(..., cwd=)`, `display_size()` — additive |
| `core/campaign.py` | **baru** — port campain |
| `core/chat_spikes.py` | **baru** — port SpikeDetector + adapter batch |
| `core/tools.py` | handler `campaign_render` |
| `core/live.py` | hint chat = `chat_signals` + `chat_spike_moments` (dedupe 12 dtk) |
| `studio_api.py` | `GET /api/campaign/presets` |
| `web/pages.js`, `web/app.js` | halaman Campaign + entri NAV |
| `presets/*.json` | preset campaign (ter-version) |
| `assets/campaign/*` | watermark + outro |

---

## 6. Menjalankan

```powershell
cd C:\Users\ipank\viral-auto-klip
.venv\Scripts\python.exe server.py      # http://127.0.0.1:8420/app
```

Campaign: menu **Klip dari video → Campaign**.

---

## 7. Yang masih harus dibereskan

1. `requirements.txt` vak masih menarik `streamlit` (~100 MB) padahal server memakai FastAPI.
2. `playwright` belum ada (dibutuhkan port uploader browser).
3. Buffer HLS presisi Clipper belum dipindah.
4. Test campaign belum dijalankan setelah dependency lengkap.
5. `output/` Clipper: **jangan disentuh** — tetap jadi arsip hasil lama.
