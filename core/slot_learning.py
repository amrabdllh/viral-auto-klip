"""Jam upload yang BELAJAR dari hasil channel sendiri (v6.8).

Tiga lapis (studi publik saling bertentangan — Buffer: TikTok terbaik Sabtu/Minggu pagi; Sprout: Selasa–Kamis sore —
dan semuanya menyimpulkan analitik channel sendiri lebih menentukan):
1. Jam JANGKAR stabil per platform & niche (konsisten tiap hari → penonton tahu kapan ada konten baru).
2. Penyesuaian per HARI: Jumat & Sabtu malam lebih larut (+1 jam), Minggu malam lebih awal (−30 mnt, besok sekolah/
   kerja), pagi akhir pekan lebih siang (+1 jam).
3. BELAJAR: views 24 jam tiap upload dicatat per (platform, jenis hari, jam). Setelah cukup data (≥3 sampel per jam,
   ≥8 total), jam yang terbukti di atas median channel menggantikan jangkar; ±20% slot tetap dipakai mencoba jam lain
   (eksplorasi) supaya jadwal tidak terkunci di tebakan awal.

Sumber views (tanpa login tambahan): YouTube → YouTube Data API (YOUTUBE_API_KEY) atau yt-dlp dari URL Shorts;
TikTok → yt-dlp atas profil `TIKTOK_PROFILE` (dicocokkan dengan jam upload ±20 menit). Tanpa data → lapis 1–2 saja.
"""
import random
import statistics
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from loguru import logger

import config
from . import db

_KEY = "slot_perf"          # catatan hasil per upload
_MEASURED = "slot_perf_ids"  # id jadwal yang sudah diukur
MIN_SAMPLES_HOUR, MIN_SAMPLES_TOTAL, EXPLORE = 3, 8, 0.2


def day_type(dt: datetime) -> str:
    return "weekend" if dt.weekday() >= 5 else "weekday"


def adjust_hour(hour: float, dt: datetime) -> float:
    """Penyesuaian jam jangkar menurut hari."""
    wd = dt.weekday()
    if hour >= 17 and wd in (4, 5):   # Jumat & Sabtu malam: orang begadang
        hour += 1.0
    elif hour >= 17 and wd == 6:      # Minggu malam: lebih awal
        hour -= 0.5
    if hour < 12 and wd >= 5:         # pagi akhir pekan: bangun lebih siang
        hour += 1.0
    return min(hour, 23.5)


# ----------------------------------------------------------------------------- data hasil

def records() -> List[Dict]:
    return db.kv_get(_KEY, []) or []


def insights(platform: str) -> Dict:
    """{"ready": bool, "total": n, "median": views, "hours": {(daytype, hour): {"n", "score"}}} — score = median jam ÷ median channel."""
    rows = [r for r in records() if r.get("platform") == platform and r.get("views") is not None]
    out: Dict = {"ready": False, "total": len(rows), "median": 0, "hours": {}}
    if not rows:
        return out
    med = statistics.median(r["views"] for r in rows) or 1
    out["median"] = med
    buckets: Dict[Tuple[str, int], List[int]] = {}
    for r in rows:
        buckets.setdefault((r["daytype"], int(r["hour"])), []).append(int(r["views"]))
    for k, v in buckets.items():
        out["hours"][k] = {"n": len(v), "score": round(statistics.median(v) / med, 2)}
    out["ready"] = len(rows) >= MIN_SAMPLES_TOTAL
    return out


def plan_hours(anchors: List[float], extra: List[float], platform: str, dt: datetime, n: int,
               rnd: random.Random) -> List[Tuple[float, str]]:
    """Jam upload untuk hari `dt` (urut prioritas) + alasannya: 'jangkar' | 'terbukti' | 'eksplorasi'."""
    base = [(adjust_hour(h, dt), "jangkar") for h in anchors] + [(adjust_hour(h, dt), "jangkar") for h in extra]
    ins = insights(platform)
    if ins["ready"]:
        dtype = day_type(dt)
        proven = sorted(((h, s) for (d, h), s in ins["hours"].items()
                         if d == dtype and s["n"] >= MIN_SAMPLES_HOUR and s["score"] >= 1.15), key=lambda x: -x[1]["score"])
        weak = {h for (d, h), s in ins["hours"].items() if d == dtype and s["n"] >= MIN_SAMPLES_HOUR and s["score"] < 0.8}
        chosen = [(float(h) + 0.25, "terbukti") for h, _ in proven]
        chosen += [b for b in base if int(b[0]) not in weak and all(abs(b[0] - c[0]) >= 1.5 for c in chosen)]
        # eksplorasi: sesekali coba jam ramai lain yang belum cukup diuji
        if rnd.random() < EXPLORE:
            tested = {h for (d, h), s in ins["hours"].items() if d == dtype and s["n"] >= MIN_SAMPLES_HOUR}
            options = [h for h in (11, 13, 15, 16, 17, 18, 20, 21, 22) if h not in tested]
            if options:
                chosen.insert(min(1, len(chosen)), (float(rnd.choice(options)), "eksplorasi"))
        base = chosen or base
    seen, out = set(), []
    for h, why in base:
        key = round(h * 2) / 2
        if key not in seen:
            seen.add(key)
            out.append((h, why))
    return out[:max(n, 1) + 2]


# ----------------------------------------------------------------------------- pengukuran views 24 jam

def _yt_views(video_id: str) -> Optional[int]:
    key = str(getattr(config, "YOUTUBE_API_KEY", "") or "")
    if key:
        import requests
        r = requests.get("https://www.googleapis.com/youtube/v3/videos",
                         params={"part": "statistics", "id": video_id, "key": key}, timeout=30)
        items = r.json().get("items") or []
        if items:
            return int(items[0]["statistics"].get("viewCount", 0))
    import yt_dlp
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as y:
        info = y.extract_info(f"https://www.youtube.com/shorts/{video_id}", download=False)
    return int(info.get("view_count") or 0) if info else None


def _tiktok_profile() -> List[Dict]:
    user = str(getattr(config, "TIKTOK_PROFILE", "") or "").strip().lstrip("@")
    if not user:
        return []
    import yt_dlp
    opts = {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist", "skip_download": True, "playlistend": 60}
    with yt_dlp.YoutubeDL(opts) as y:
        data = y.extract_info(f"https://www.tiktok.com/@{user}", download=False)
    return [{"ts": float(e.get("timestamp") or 0), "views": int(e.get("view_count") or 0)}
            for e in (data.get("entries") or []) if e and e.get("timestamp")]


def measure_due(now: Optional[float] = None) -> int:
    """Ukur views upload yang sudah tayang 24 jam–7 hari dan belum diukur. Return jumlah yang tercatat."""
    now = now or time.time()
    done_ids = set(db.kv_get(_MEASURED, []) or [])
    due = [s for s in db.list_schedules() if s.get("status") == "done" and s["id"] not in done_ids
           and 24 * 3600 <= now - float(s["run_at"]) <= 7 * 86400]
    if not due:
        return 0
    recs, added = records(), 0
    tiktok_rows: Optional[List[Dict]] = None
    for s in due:
        res = s.get("result") or {}
        when = datetime.fromtimestamp(float(s["run_at"]))
        for pf in s.get("platforms") or []:
            views = None
            try:
                if pf == "youtube" and (res.get("youtube") or {}).get("video_id"):
                    views = _yt_views(res["youtube"]["video_id"])
                elif pf == "tiktok":
                    if tiktok_rows is None:
                        tiktok_rows = _tiktok_profile()
                    near = [r for r in tiktok_rows if abs(r["ts"] - float(s["run_at"])) <= 20 * 60]
                    views = near[0]["views"] if near else None
            except Exception as exc:
                logger.warning(f"Ukur views {pf} gagal: {str(exc)[:120]}")
            if views is not None:
                recs.append({"id": s["id"], "platform": pf, "run_at": s["run_at"], "hour": when.hour,
                             "weekday": when.weekday(), "daytype": day_type(when), "views": views, "measured": now})
                added += 1
        done_ids.add(s["id"])
    db.kv_set(_KEY, recs[-2000:])
    db.kv_set(_MEASURED, list(done_ids)[-5000:])
    if added:
        logger.info(f"Jam upload: {added} hasil views 24 jam dicatat (total {len(recs)})")
    return added
