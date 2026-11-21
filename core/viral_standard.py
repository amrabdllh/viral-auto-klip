#!/usr/bin/env python3
"""Standar produksi MotionKlip — implementasi skill `make-clip`.

Sumber: `.claude/skills/make-clip/SKILL.md` di proyek Clipper lama.
Modul ini mengubah aturan skill menjadi konstanta + helper yang dipakai job
clip dan campaign, supaya standarnya konsisten dan tidak tersebar di prompt.

Aturan inti:
1. Detik 0 — klip mulai di puncak reaksi, tanpa lead-in.
2. Tiga hook selaras — headline (5-7 kata), visual (punch-in/9:16), audio (0.0s).
3. Brand sebagai figuran, cerita sebagai tokoh utama.
4. Audio — vokal dekat 0 dB, backsound -12..-18 dB, ducking, tanpa senyap.
5. Safe zone 1080x1920 — kanan > 120 px, bawah > 280 px, watermark 864 px.
"""
from __future__ import annotations

from typing import Any, Dict

# ---- Durasi & skor (skill: 15-45 dtk, sweet spot 25-45) ----
MIN_CLIP_SEC = 15
MAX_CLIP_SEC = 45
SWEET_SPOT_SEC = (25, 45)
MIN_SCORE = 55

# ---- Headline hook ----
HEADLINE_MIN_WORDS = 5
HEADLINE_MAX_WORDS = 7
HEADLINE_LANGUAGE = "id"

# ---- Safe zone (piksel pada 1080x1920) ----
OUT_W, OUT_H = 1080, 1920
SAFE_RIGHT_PX = 120
SAFE_BOTTOM_PX = 280
SAFE_SIDE_PX = 60
CAPTION_MAX_WORDS = 4

# ---- Watermark ----
WATERMARK_WIDTH = 864
WATERMARK_OPACITY = 0.8

# ---- Audio ----
VOCAL_PEAK_DB = 0.0
BACKSOUND_MIN_DB = -18.0
BACKSOUND_MAX_DB = -12.0
DUCKING_ENABLED = True

# ---- Prompt ----
SELECTION_RULES = (
    "Kriteria pemilihan (standar MotionKlip):\n"
    f"- Durasi klip {MIN_CLIP_SEC}-{MAX_CLIP_SEC} detik; utamakan {SWEET_SPOT_SEC[0]}-{SWEET_SPOT_SEC[1]} detik.\n"
    "- Klip HARUS mulai di puncak reaksi, punchline, atau pemicu rasa penasaran (detik 0).\n"
    "- Tolak pembukaan lambat: lebih dari 2 detik filler/hening sebelum momen = gugurkan.\n"
    "- Prioritaskan energi tinggi: teriakan, tawa, plot twist mendadak, punchline.\n"
    f"- Headline hook {HEADLINE_MIN_WORDS}-{HEADLINE_MAX_WORDS} kata, bahasa Indonesia, memancing rasa penasaran.\n"
    "- Jangan jualan keras atau menulis 'Review ...' — brand hanya figuran.\n"
)

HEADLINE_RULES = (
    f"Headline hook: {HEADLINE_MIN_WORDS}-{HEADLINE_MAX_WORDS} kata, bahasa Indonesia, "
    "punchy dan memancing rasa penasaran. Jangan menyebut brand sebagai tokoh utama."
)


def apply_to_params(params: Dict[str, Any]) -> Dict[str, Any]:
    """Terapkan default standar viral ke parameter job clip (tanpa menimpa yang sudah diisi)."""
    out = dict(params)
    out.setdefault("min_duration", MIN_CLIP_SEC)
    out.setdefault("max_duration", MAX_CLIP_SEC)
    out.setdefault("min_score", MIN_SCORE)
    out.setdefault("viral_standard", True)
    # Batasi kalau user memberi nilai di luar standar.
    try:
        out["min_duration"] = max(MIN_CLIP_SEC, int(float(out.get("min_duration") or MIN_CLIP_SEC)))
        out["max_duration"] = min(MAX_CLIP_SEC, int(float(out.get("max_duration") or MAX_CLIP_SEC)))
        if out["min_duration"] >= out["max_duration"]:
            out["min_duration"], out["max_duration"] = MIN_CLIP_SEC, MAX_CLIP_SEC
    except (TypeError, ValueError):
        out["min_duration"], out["max_duration"] = MIN_CLIP_SEC, MAX_CLIP_SEC
    return out


def caption_vertical_position() -> int:
    """Titik Y caption yang aman dari UI platform (bawah > 280 px)."""
    return OUT_H - SAFE_BOTTOM_PX


def watermark_overlay_opacity() -> float:
    return WATERMARK_OPACITY


def summary() -> Dict[str, Any]:
    """Ringkasan untuk /api/status atau dokumentasi UI."""
    return {
        "min_clip_sec": MIN_CLIP_SEC,
        "max_clip_sec": MAX_CLIP_SEC,
        "sweet_spot_sec": list(SWEET_SPOT_SEC),
        "min_score": MIN_SCORE,
        "headline_words": [HEADLINE_MIN_WORDS, HEADLINE_MAX_WORDS],
        "safe": {"right_px": SAFE_RIGHT_PX, "bottom_px": SAFE_BOTTOM_PX, "side_px": SAFE_SIDE_PX},
        "watermark": {"width": WATERMARK_WIDTH, "opacity": WATERMARK_OPACITY},
        "audio": {"vocal_peak_db": VOCAL_PEAK_DB,
                  "backsound_db": [BACKSOUND_MIN_DB, BACKSOUND_MAX_DB]},
    }


__all__ = [
    "MIN_CLIP_SEC", "MAX_CLIP_SEC", "SWEET_SPOT_SEC", "MIN_SCORE",
    "HEADLINE_MIN_WORDS", "HEADLINE_MAX_WORDS", "HEADLINE_LANGUAGE",
    "OUT_W", "OUT_H", "SAFE_RIGHT_PX", "SAFE_BOTTOM_PX", "SAFE_SIDE_PX",
    "CAPTION_MAX_WORDS", "WATERMARK_WIDTH", "WATERMARK_OPACITY",
    "VOCAL_PEAK_DB", "BACKSOUND_MIN_DB", "BACKSOUND_MAX_DB", "DUCKING_ENABLED",
    "SELECTION_RULES", "HEADLINE_RULES",
    "apply_to_params", "caption_vertical_position", "watermark_overlay_opacity", "summary",
]
