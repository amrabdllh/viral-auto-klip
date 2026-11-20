---
name: make-clip
description: Generate viral-optimized 9:16 short clips following MotionKlip's official clipping standards (Detik 0 Hook, Brand as Figuran, Audio Ducking, Safe-Zone Watermark, and Retention-First Candidate Selection).
---

# Make Clip Skill (MotionKlip Viral Standard)

This skill implements the verified production rules extracted directly from the official **MotionKlip** training and viral breakdown workshops.

## 1. Core Principles (MotionKlip Formula)

1. **Detik 0 Rule (Instant Hook < 1.0s)**:
   - Viewers swipe within 1 second. The clip must start on the exact peak reaction, punchline, or curiosity trigger.
   - Zero slow lead-in or dead silence.

2. **The 3-Hook Simultaneous Alignment**:
   - **Headline Text Hook**: Cognitive attack / curiosity gap / irony (e.g., *"Lu kalau gak ganteng minimal wangi"*, *"Katanya frustasi tapi malah spend 1.7 M"*). Never hard-sell or say "Review...".
   - **Visual Hook**: High contrast framing (dramatic facial expression, zoom punch-in, 9:16 safe-zone framing).
   - **Audio Hook**: Immediate audio presence; backsound or reaction sound effect starting at 0.0s.

3. **Brand/Campaign as Figuran, Story as Tokoh Utama**:
   - If clipping for a campaign (e.g. GTA VI, Felixir, PUBG), the brand/campaign asset is **supporting context**, not the hero.
   - The hero is the streamer's raw emotion, conflict, comedy, or ego.

4. **Audio Engineering Standards**:
   - Dialogue/Vocals: Normalized near 0 dB peak (loud and crystal clear).
   - Backsound: Set between `-12 dB` and `-18 dB` relative to vocals.
   - Ducking: Music automatically lowers during speech.
   - No silence: Always maintain ambient energy or sound design.

5. **Visual Safe-Zone & Watermark Rules**:
   - Output resolution: `1080x1920` (9:16).
   - Watermark: Centered or mid-upper/mid-lower, 80% opacity, width 864 px.
   - Keep vital elements clear of platform UI (right margin > 120 px for TikTok buttons, bottom margin > 280 px for captions/audio ticker).

---

## 2. Clipper Pipeline Execution Workflow

When executing clip generation using Clipper:

### Step 1: Candidate Selection with LLM Prompt
When querying Magpie (Claude Haiku 5.5) or Gemini to pick clip candidates from `transcript.json`:
- Reject segments with slow build-ups (>2s of filler).
- Prioritize high-energy moments (screaming, laughing, sudden plot twists, punchlines).
- Enforce candidate duration between **15s and 45s** (sweet spot for short-form retention rate > 50%).
- Generate a **Headline Hook** (max 5-7 words, Indonesian, punchy & curious).

### Step 2: Dynamic Reframe & Framing
- For talking streamer / facecam: use `--reframe face` (YuNet auto-centering) or stacked layout (`motion-klip-gtavi-split.json` for facecam top + gameplay bottom).
- Safe area margins: Bottom `260px`, Side `60px`.

### Step 3: Subtitle / Captioning (Optional/Toggle)
- When captions are requested: Karaoke ASS style (`captions.py`), white base to yellow/cyan highlight.
- Font: Bold sans-serif (e.g., Montserrat / Arial Black), max 3-4 words per page, centered at safe height.

### Step 4: Campaign Overlay & Branding
- Apply watermark from campaign preset (`Watermark New Model MotionKlip.png`).
- Concat 3s campaign outro video (`GTAVI-OutroVideo.mov`).
- Concat audio with sample rate matching (`aresample=async=1`).

---

## 3. Implementasi di aplikasi konsolidasi (viral-auto-klip)

Aturan skill ini **dikodekan** di `core/viral_standard.py` (bukan hanya dokumen):

| Aturan skill | Implementasi |
|---|---|
| Durasi 15-45 dtk, sweet spot 25-45 | `MIN_CLIP_SEC`, `MAX_CLIP_SEC`, `SWEET_SPOT_SEC`, `apply_to_params()` |
| Skor minimum | `MIN_SCORE` (55), diterapkan ke parameter job |
| Headline 5-7 kata | `HEADLINE_RULES`, `SELECTION_RULES` untuk prompt analyzer |
| Safe zone: kanan 120 px, bawah 280 px, sisi 60 px | `SAFE_RIGHT_PX`, `SAFE_BOTTOM_PX`, `SAFE_SIDE_PX`, `caption_vertical_position()` |
| Watermark 864 px, 80% opacity | `WATERMARK_WIDTH`, `WATERMARK_OPACITY` |
| Vokal ~0 dB, backsound -12..-18 dB, ducking | `VOCAL_PEAK_DB`, `BACKSOUND_MIN_DB/MAX_DB`, `DUCKING_ENABLED` |
| Brand figuran, cerita utama | `SELECTION_RULES` (prompt) |

## 4. Cara menjalankan

### A. Campaign MotionKlip (watermark + outro + subtitle + hashtag)
Lewat UI: **Campaign** → pilih preset `motion-klip-gtavi-split.json` → isi judul → Export.
Lewat CLI:
```powershell
.venv\Scripts\python.exe cli.py --file "<VIDEO>" --num-clips 5 --min-score 55 --min-duration 15 --max-duration 45
```

### B. Klip bersih (tanpa subtitle)
```powershell
.venv\Scripts\python.exe cli.py --file "<VIDEO>" --no-captions --min-score 55 --max-duration 45
```

### C. Live: spike presisi (momen ramai chat, window tepat)
UI: **Klip Live** → Mode **Spike presisi** → aktifkan campaign bila ingin template MotionKlip.
Window: `[momen-25s, momen+20s]`, bukan potongan 60 detik.

### D. Upload otomatis
UI: kartu video → ikon upload (YouTube headless / TikTok). Atau Campaign → toggle
"Upload ke YouTube setelah render" + "Buat judul & deskripsi otomatis".
Sesi: **Pengaturan → YouTube / TikTok** (tempel cookies). Tidak ada jendela/popup.
