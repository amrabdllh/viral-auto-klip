# Skill `make-clip` → implementasi

Sumber aturan: `.claude/skills/make-clip/SKILL.md` (dipindahkan dari proyek Clipper).
Modul: `core/viral_standard.py`.

Tujuan dokumen ini: memastikan setiap aturan skill punya **titik implementasi yang bisa
dicek**, bukan cuma tertulis di prompt.

## Peta aturan

| # | Aturan skill | Konstanta / fungsi | Dipakai di |
|---|---|---|---|
| 1 | Detik 0 — mulai di puncak reaksi | `SELECTION_RULES` (prompt) | `core/analyzer.py` `_ask()` |
| 2 | Headline 5–7 kata, bahasa Indonesia | `HEADLINE_RULES`, `HEADLINE_MIN/MAX_WORDS` | prompt packaging + `_auto_upload_meta()` |
| 3 | Brand figuran, cerita tokoh utama | `SELECTION_RULES` | prompt analyzer |
| 4 | Vokal ~0 dB, backsound −12..−18 dB, ducking | `VOCAL_PEAK_DB`, `BACKSOUND_MIN/MAX_DB`, `DUCKING_ENABLED` | `core/audio_enhancer.py`, `core/ffx.mux_voice_music` |
| 5 | Safe zone: kanan 120 px, bawah 280 px, sisi 60 px | `SAFE_RIGHT_PX`, `SAFE_BOTTOM_PX`, `SAFE_SIDE_PX`, `caption_vertical_position()` | style caption, campaign |
| 6 | Watermark 864 px, 80% opacity | `WATERMARK_WIDTH`, `WATERMARK_OPACITY` | preset campaign |
| 7 | Durasi 15–45 dtk (sweet spot 25–45) | `MIN_CLIP_SEC`, `MAX_CLIP_SEC`, `SWEET_SPOT_SEC`, `apply_to_params()` | job clip |
| 8 | Skor minimum | `MIN_SCORE` | `apply_to_params()` |

## Yang sudah ada di vak sebelum modul ini

- Skor durasi 25–45 dtk sudah tertinggi di `analyzer.py:760`.
- Prompt analyzer sudah membatasi `MIN_CLIP_DURATION`–`MAX_CLIP_DURATION`.
- Karaoke ASS + 8 style caption ada di `core/utils.py` & `config.CAPTION_STYLES`.
- Audio enhancer (loudness, bass, ducking) ada di `core/audio_enhancer.py`.
- Watermark/outro campaign ada di `core/campaign.py` + `presets/`.

Modul ini **menyatukan** angka-angkanya supaya tidak divergen, dan menambah aturan
yang belum ada (detik 0, headline 5–7 kata, safe zone eksplisit).

## Perbedaan angka yang disengaja

| | vak default | Standar skill |
|---|---|---|
| Durasi maksimum | 60 dtk | **45 dtk** |
| Skor minimum | 55 | 55 |
| Watermark lebar | 864 (full preset) / 1080 strip (split) | 864 (mengambang) |

`apply_to_params()` tidak menimpa nilai user yang sudah valid — hanya membatasi ke
rentang standar. Kalau user memilih 60 dtk, nilainya dipotong ke 45.

## Cara mengecek

```powershell
.venv\Scripts\python.exe -c "from core import viral_standard as v; print(v.summary())"
```

Keluaran harus memuat `max_clip_sec: 45`, `headline_words: [5, 7]`,
`safe: {right_px: 120, bottom_px: 280}`.
