"""(v7) Terjemahan subtitle klip — setara "Bahasa output / terjemahkan" di subtitle klipers.pro.

Caption dibangun per layar (3–5 kata, timing dari Whisper). Terjemahan dilakukan SEKALIGUS untuk semua layar
satu klip (AI melihat konteks utuh, tapi wajib membalas jumlah layar yang sama), lalu kata hasil terjemahan
dibagi rata di dalam waktu layarnya. Gagal/AI mati → subtitle tetap bahasa asli (tidak pernah merusak render).
"""
from __future__ import annotations

import json
import re
from typing import Dict, List, Optional

from loguru import logger

import config
from . import ai_providers as ai

LANGS = {
    "id": "Bahasa Indonesia", "en": "English", "ms": "Bahasa Melayu", "jv": "Bahasa Jawa", "su": "Bahasa Sunda",
    "ar": "Arabic", "ja": "Japanese", "ko": "Korean", "zh": "Chinese (Simplified)", "es": "Spanish",
    "pt": "Portuguese (Brazil)", "hi": "Hindi", "tl": "Filipino", "th": "Thai", "vi": "Vietnamese",
}
_SYSTEM = ("Kamu penerjemah subtitle video pendek (TikTok/Shorts). Terjemahan harus terdengar seperti orang "
           "berbicara, singkat, dan setia pada makna. Balas HANYA JSON.")


def normalize(code: str) -> str:
    c = str(code or "").strip().lower()
    if c in LANGS:
        return c
    for k, v in LANGS.items():  # nama bahasa → kode
        if c and c in v.lower():
            return k
    return ""


def needed(target: str, source: str = "") -> bool:
    t = normalize(target)
    return bool(t) and t != normalize(source or "")


def translate_segments(segments: List[Dict], target: str, context: str = "", batch: int = 60) -> List[Dict]:
    """Terjemahkan layar caption ke `target` (kode/nama bahasa). Timing layar tetap; kata dibagi ulang."""
    code = normalize(target)
    if not code or not segments:
        return segments
    from .subtitle_live import apply_text_edits
    lang = LANGS[code]
    if not ai.is_configured(config.AI_PROVIDER):
        logger.warning("Terjemahan subtitle dilewati: AI belum diatur")
        return segments
    edits: Dict[int, str] = {}
    for b0 in range(0, len(segments), batch):
        part = segments[b0:b0 + batch]
        payload = [{"i": b0 + j, "t": " ".join(str(s.get("text") or " ".join(w["word"] for w in s.get("words") or [])).split())}
                   for j, s in enumerate(part)]
        prompt = (f"Terjemahkan setiap 't' ke {lang} untuk subtitle klip video{f' ({context[:120]})' if context else ''}.\n"
                  "Aturan: satu item = satu layar subtitle, JANGAN gabung/pisah/lewati item; panjang mirip aslinya "
                  "(maks ±1,5x jumlah kata); pertahankan nama orang, nama game/merek, istilah populer & angka; "
                  "bahasa lisan natural, bukan terjemahan kaku; seruan/umpatan ringan diterjemahkan setara.\n"
                  'Format: {"items": [{"i": 0, "t": "..."}]}\n\n' + json.dumps(payload, ensure_ascii=False))
        try:
            raw = ai.complete(prompt, system=_SYSTEM, max_tokens=min(8000, 120 + 60 * len(part)))
            got = ai.parse_json(raw).get("items") or []
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Terjemahan subtitle gagal ({lang}): {str(exc)[:160]}")
            return segments
        for it in got:
            try:
                i, t = int(it.get("i")), " ".join(str(it.get("t") or "").split())
            except (TypeError, ValueError):
                continue
            if b0 <= i < b0 + len(part) and t:
                edits[i] = _clamp(t, payload[i - b0]["t"])
    if len(edits) < len(segments) * 0.6:  # jawaban AI tidak lengkap → jangan campur dua bahasa
        logger.warning(f"Terjemahan subtitle tidak lengkap ({len(edits)}/{len(segments)} layar) — pakai bahasa asli")
        return segments
    out = apply_text_edits(segments, edits)
    for s in out:
        s["lang"] = code
    return out


def _clamp(t: str, orig: str) -> str:
    """Layar subtitle jangan jadi paragraf: maks 3x kata asli (min 10) — terjemahan wajar tidak dipotong."""
    words = t.split()
    cap = max(10, 3 * len(orig.split()))
    t = " ".join(words[:cap])
    return re.sub(r"\s+([,.!?])", r"\1", t)
