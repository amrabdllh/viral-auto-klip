"""
B-roll otomatis (v6.4): sisipan gambar 1.4–2.2 dtk saat pembicara menyebut hal yang konkret
(benda, tempat, makanan, uang, hewan, aktivitas) — gaya editor Shorts/Reels.

1. AI membaca kata-kata klip (bertimestamp) dan memilih maks 2 momen + kueri gambar.
2. Gambar diambil dari stok (Pexels/Pixabay bila key ada), atau dibuat AI (Gemini/OpenAI/Pollinations).
Gagal di langkah mana pun = klip tetap jadi tanpa b-roll.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from loguru import logger

import config
from . import ai_providers as ai
from . import media_ai

_SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {
        "t": {"type": "number"}, "dur": {"type": "number"}, "query": {"type": "string"}, "prompt": {"type": "string"}},
        "required": ["t", "dur", "query", "prompt"], "additionalProperties": False}}},
    "required": ["items"], "additionalProperties": False,
}


def plan_broll(words_rel: List[Dict], duration: float, skip_until: float = 0.0, limit: int = 2,
               title: str = "") -> List[Dict]:
    """Momen b-roll dari AI: [{t, dur, query, prompt}] (waktu relatif klip)."""
    if not words_rel or not ai.is_configured(config.AI_PROVIDER) or duration < 12:
        return []
    lines, cur, t0 = [], [], None
    for w in words_rel:
        if t0 is None:
            t0 = w["start"]
        cur.append(w["word"])
        if str(w["word"]).endswith((".", "!", "?")) or len(cur) >= 12:
            lines.append(f"[{t0:.1f}] {' '.join(cur)}")
            cur, t0 = [], None
    if cur:
        lines.append(f"[{t0:.1f}] {' '.join(cur)}")
    prompt = (
        f"Klip vertikal {duration:.0f} detik{f' berjudul “{title}”' if title else ''}. Transkrip (detik mulai kalimat):\n"
        + "\n".join(lines)
        + f"\n\nPilih maksimal {limit} momen di mana editor profesional akan menyisipkan gambar b-roll 1.4–2.2 detik "
          "karena pembicara menyebut sesuatu yang KONKRET dan bisa digambarkan (benda, tempat, makanan, uang, hewan, kendaraan, "
          "gedung, alat). Gambar TIDAK BOLEH berisi orang/tubuh manusia (pembicara sendiri sudah tampil) — pilih benda atau "
          "tempatnya. Jangan pilih kalimat abstrak/perasaan, lelucon fisik, atau hal sensitif. Jangan di 2 detik pertama"
          f"{f' atau sebelum detik {skip_until:.1f}' if skip_until else ''}, jangan di 2 detik terakhir, jarak antar momen ≥5 detik. "
          "t = detik saat kata itu diucapkan (perkiraan dari timestamp kalimat). query = 2–4 kata bahasa Inggris untuk cari foto stok. "
          "prompt = deskripsi foto realistis bahasa Inggris (tanpa teks/tulisan, tanpa wajah orang terkenal). "
          "Bila tidak ada yang cocok, kembalikan items kosong.\n"
          'Format JSON: {"items": [{"t": 8.5, "dur": 1.8, "query": "stack of money", "prompt": "..."}]}'
    )
    try:
        data = ai.parse_json(ai.complete(prompt, system="Kamu editor video short-form. Jawab hanya JSON.",
                                         max_tokens=900, json_mode=True, json_schema=_SCHEMA))
    except Exception as exc:
        logger.warning(f"Rencana b-roll gagal: {str(exc)[:160]}")
        return []
    out: List[Dict] = []
    for it in (data.get("items") or [])[:limit]:
        try:
            t, dur = float(it["t"]), min(max(float(it.get("dur", 1.8)), 1.2), 2.4)
        except (KeyError, TypeError, ValueError):
            continue
        if t < max(2.0, skip_until + 0.5) or t + dur > duration - 1.5:
            continue
        if any(abs(t - o["t"]) < 5 for o in out):
            continue
        out.append({"t": round(t, 2), "dur": round(dur, 2), "query": str(it.get("query", ""))[:60],
                    "prompt": str(it.get("prompt", ""))[:400]})
    return out


def fetch_broll(items: List[Dict], out_dir: Path) -> List[Dict]:
    """Ambil gambar tiap momen. Item tanpa gambar dibuang."""
    ready = []
    for k, it in enumerate(items):
        target = Path(out_dir) / f"broll_{k + 1}.jpg"
        try:
            if media_ai.stock_available() and it.get("query"):
                hits = media_ai.stock_search(it["query"], kind="photo", orientation="portrait", per_page=3)
                if hits:
                    media_ai.download(hits[0]["url"], str(target))
                    ready.append({**it, "img": str(target), "source": hits[0].get("source")})
                    continue
            res = media_ai.image(f"{it['prompt']}. Photorealistic, cinematic lighting, vertical 9:16 composition, "
                                 "no people, no text, no watermark.",
                                 str(target), w=1080, h=1920, allow_fallback=True)
            if res.get("provider") == "card":  # kartu teks polos tidak layak jadi b-roll
                continue
            ready.append({**it, "img": res["path"], "source": res.get("provider")})
        except Exception as exc:
            logger.warning(f"B-roll '{it.get('query')}' dilewati: {str(exc)[:160]}")
    return ready
