"""Editor-AI yang MELIHAT (v6.8): penilaian visual kandidat klip untuk live/gaming.

Masalah: Editor-AI hanya membaca transkrip. Di live streaming gaming nilainya ada di layar (kejadian di game,
ekspresi streamer), sehingga momen bagus dinilai rendah atau ditolak — uji pada live GTA 6 110 menit: 31 dari 37
kandidat ditolak berdasarkan teks saja.

Cara kerja: 4 frame per kandidat (15/40/65/90% durasi, 512 px) + potongan transkrip dikirim ke model multimodal
(otomatis: koneksi API yang punya model Claude/GPT-4o/GPT-5/Gemini, atau VISION_PROVIDER/VISION_MODEL). Model menilai
`visual` (seberapa menarik kejadian di layar) dan `clarity` (paham tanpa ikut live) lalu menulis apa yang terjadi.
Hasilnya dicampur ke rubrik Editor-AI (40% visual). Gagal/tidak ada model → penilaian teks saja (perilaku lama).
Uji koneksi: claude-haiku-5-5 lewat gateway mendeskripsikan frame dengan benar (webcam streamer, teks HUD game).
"""
import base64
import json
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from loguru import logger

import config

_VISION_HINT = re.compile(r"claude|gpt-4o|gpt-4\.1|gpt-5|gemini|vision|vl", re.I)


def pick_model() -> Optional[Tuple[str, str]]:
    """(provider, model) multimodal yang tersedia, atau None."""
    from . import ai_providers as ai
    from . import connections
    prov, model = getattr(config, "VISION_PROVIDER", ""), getattr(config, "VISION_MODEL", "")
    if prov:
        return prov, model or ai.default_model(prov)
    for c in connections.load():
        models = c.get("models") or []
        pref = [m for m in models if "haiku" in m.lower()] + [m for m in models if _VISION_HINT.search(m)]
        if pref:
            return c["name"], pref[0]
    return None


def available() -> bool:
    return bool(getattr(config, "VISION_REVIEW", True)) and pick_model() is not None


def _frames(video: str, start: float, end: float, n: int = 4) -> List[str]:
    out = []
    for frac in (0.15, 0.4, 0.65, 0.9)[:n]:
        t = start + (end - start) * frac
        proc = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", video, "-frames:v", "1",
                               "-vf", "scale=512:-2", "-q:v", "5", "-f", "image2", "-c:v", "mjpeg", "-"],
                              capture_output=True, timeout=60)
        if proc.returncode == 0 and proc.stdout:
            out.append(base64.b64encode(proc.stdout).decode())
    return out


def review(clip: Dict, video: str) -> Optional[Dict]:
    """{"visual": 0-10, "clarity": 0-10, "what": "...", "hook": "..."} atau None."""
    from . import ai_providers as ai
    picked = pick_model()
    if not picked:
        return None
    prov, model = picked
    frames = _frames(video, float(clip["start"]), float(clip["end"]))
    if len(frames) < 2:
        return None
    text = str(clip.get("transcript") or clip.get("text") or "")[:1500]
    content = [{"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b}"}} for b in frames]
    content.append({"type": "text", "text": (
        "Ini 4 frame berurutan dari kandidat klip live streaming gaming (wajah streamer + gameplay), beserta ucapannya:\n"
        f"\"{text}\"\n\nNilai sebagai editor TikTok yang jujur:\n"
        "- visual 0-10: seberapa menarik KEJADIAN di layar (aksi, kejadian lucu/epik/gagal, cutscene penting, ekspresi kuat). "
        "Menu, peta, loading, jalan biasa tanpa kejadian = 2-4.\n"
        "- clarity 0-10: apakah penonton yang tidak ikut live paham apa yang terjadi dari gambar + ucapan.\n"
        "- what: 1 kalimat apa yang benar-benar terlihat terjadi.\n"
        "- hook: teks layar maks 8 kata yang memancing penasaran, berdasar kejadian yang TERLIHAT & DIUCAPKAN.\n"
        'Balas hanya JSON: {"visual": 0, "clarity": 0, "what": "...", "hook": "..."}')})
    try:
        import openai
        client = openai.OpenAI(api_key=ai._api_key(prov), base_url=ai._base_url(prov), timeout=120, max_retries=1)
        resp = client.chat.completions.create(model=model, max_tokens=1500,  # model ber-penalaran butuh ruang
                                              messages=[{"role": "user", "content": content}])
        raw = resp.choices[0].message.content or ""
        m = re.search(r"\{.*\}", raw, re.S)
        d = json.loads(m.group(0)) if m else {}
        v, c = float(d.get("visual", 0) or 0), float(d.get("clarity", 0) or 0)
        if not (0 <= v <= 10 and 0 <= c <= 10) or (v == 0 and c == 0):
            return None
        return {"visual": v, "clarity": c, "what": str(d.get("what", ""))[:200], "hook": str(d.get("hook", ""))[:80],
                "model": f"{prov}/{model}"}
    except Exception as exc:
        logger.warning(f"Penilaian visual klip {clip.get('start', 0):.0f}s gagal: {str(exc)[:160]}")
        return None
