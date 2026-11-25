"""Riset hashtag: pilih hashtag yang benar-benar HIDUP & berpotensi menjangkau penonton, bukan sekadar tebakan AI.

Sumber data: halaman hashtag YouTube (youtube.com/hashtag/<tag>) menampilkan "8.3M videos • 1.5M channels".
- Tidak ada halaman/angka → hashtag mati (tidak ada yang memakai, tidak bisa ditemukan penonton) → dibuang.
- Terlalu kecil (< 1rb video) → hampir tidak ada yang mencari.
- Raksasa umum (#fyp, #viral, #shorts: ratusan juta video) → klip tenggelam, nilainya kecil.
- Paling berpotensi: topik spesifik yang aktif (±50rb–30jt video), mis. #GTA6, #gamingindonesia.
Relevansi tetap diutamakan: urutan dari Editor-AI (paling relevan dulu) ikut dinilai.
Catatan jujur: TikTok tidak membuka jumlah video per hashtag tanpa login; angka YouTube dipakai sebagai perkiraan
popularitas topik (topik yang ramai di Shorts umumnya juga ramai di TikTok).
Hasil disimpan 7 hari (hashtag mati 3 hari) agar tidak membuka halaman yang sama berulang kali.
"""
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional

from loguru import logger

from . import db

_TTL = 7 * 86400
_TTL_DEAD = 3 * 86400
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
_MULT = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
# hashtag platform/umum yang tidak membantu penonton menemukan topik klip — tidak dipakai di judul
GENERIC = {"fyp", "foryou", "foryoupage", "fypシ", "viral", "shorts", "short", "youtubeshorts", "trending", "xyzbca",
           "tiktok", "video", "explore", "reels", "youtubeshorts", "youtubeshort", "shortsfeed", "shortvideo",
           "youtube", "fypage", "fypdong"}


def clean(tag: str) -> str:
    """'#GTA 6!' → 'GTA6'. Hashtag hanya huruf/angka/_ (spasi, tanda baca, emoji membuat hashtag mati)."""
    return re.sub(r"[^\w]", "", str(tag or "").lstrip("#"), flags=re.UNICODE)


def _count(text: str) -> Optional[int]:
    m = re.match(r"([\d.,]+)\s*([KMB]?)", text.strip(), re.IGNORECASE)
    if not m:
        return None
    return int(float(m.group(1).replace(",", "")) * _MULT[m.group(2).upper()])


def stats(tag: str, fetch: bool = True) -> Dict:
    """{"tag", "videos": int|None, "channels": int|None, "dead": bool, "known": bool} dari cache / halaman YouTube."""
    t = clean(tag)
    key = f"hashtag_stats_{t.lower()}"
    cached = db.kv_get(key, None)
    if cached and time.time() - float(cached.get("at", 0)) < (_TTL_DEAD if cached.get("dead") else _TTL):
        return {**cached, "tag": t, "known": True}
    if not fetch or not t:
        return {"tag": t, "videos": None, "channels": None, "dead": False, "known": False}
    try:
        import requests
        r = requests.get(f"https://www.youtube.com/hashtag/{t.lower()}", params={"hl": "en", "gl": "ID"},
                         headers={"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.8"}, timeout=8)
        m = re.search(r'"content":"([\d.,]+\s*[KMB]?) videos?(?: • ([\d.,]+\s*[KMB]?) channels?)?"', r.text)
        out = {"videos": _count(m.group(1)) if m else None, "channels": _count(m.group(2)) if m and m.group(2) else None}
        out["dead"] = r.status_code == 404 or out["videos"] is None
        if r.status_code >= 500 or (r.status_code != 404 and "ytInitialData" not in r.text):
            raise RuntimeError(f"HTTP {r.status_code}")  # halaman tidak lengkap → jangan cap mati
    except Exception as exc:  # noqa: BLE001 — tanpa jaringan: hashtag tetap dipakai, hanya tidak dinilai
        logger.debug(f"Riset hashtag #{t} gagal: {exc}")
        return {"tag": t, "videos": None, "channels": None, "dead": False, "known": False}
    db.kv_set(key, {**out, "at": time.time()})
    return {**out, "tag": t, "known": True}


def popularity(videos: Optional[int]) -> float:
    """0–1: seberapa besar peluang hashtag membawa penonton baru (bukan sekadar 'paling besar')."""
    if videos is None:
        return 0.5
    if videos < 1_000:
        return 0.1
    if videos < 10_000:
        return 0.35
    if videos < 50_000:
        return 0.65
    if videos <= 30_000_000:
        return 1.0
    if videos <= 100_000_000:
        return 0.7
    return 0.4


def rank(tags: List[str], keep: Optional[List[str]] = None, fetch: bool = True) -> List[Dict]:
    """Nilai kandidat hashtag (urutan = relevansi dari Editor-AI). Hashtag mati dibuang kecuali ada di `keep`
    (hashtag pilihan pengguna). Return [{"tag", "videos", "score", "generic"}] urut skor tertinggi."""
    seen, cands = set(), []
    for t in [clean(x) for x in tags]:
        if t and t.lower() not in seen:
            seen.add(t.lower())
            cands.append(t)
    keep_l = {clean(k).lower() for k in keep or []}
    with ThreadPoolExecutor(max_workers=4) as ex:
        info = list(ex.map(lambda t: stats(t, fetch), cands))
    out = []
    for i, (t, s) in enumerate(zip(cands, info)):
        if s.get("dead") and t.lower() not in keep_l:
            logger.info(f"Hashtag #{t} dibuang: tidak ada video yang memakainya (mati)")
            continue
        rel = max(0.3, 1.0 - 0.08 * i)
        generic = t.lower() in GENERIC
        score = 0.55 * rel + 0.45 * popularity(s.get("videos")) - (0.25 if generic else 0)
        out.append({"tag": "#" + t, "videos": s.get("videos"), "score": round(score, 3), "generic": generic})
    out.sort(key=lambda r: -r["score"])
    return out


def pick(tags: List[str], n: int = 5, keep: Optional[List[str]] = None, fetch: bool = True) -> List[str]:
    """n hashtag terbaik (dengan tanda #), hashtag umum (#fyp/#viral/#shorts) hanya bila slot masih tersisa."""
    ranked = rank(tags, keep, fetch)
    best = [r["tag"] for r in ranked if not r["generic"]][:n]
    if len(best) < n:
        best += [r["tag"] for r in ranked if r["generic"]][: n - len(best)]
    return best
