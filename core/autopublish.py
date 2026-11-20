"""Upload otomatis (v6.8): klip hasil Analisis AI langsung dijadwalkan ke jam FYP terbaik — pengguna tidak perlu
mengisi jadwal sendiri.

Alur: klip selesai dirender → niche ditentukan (otomatis dari isi klip, atau dipilih pengguna) → jendela jam terbaik
per platform & niche (fyp_agent) → slot hari ini (yang belum lewat) lalu hari-hari berikutnya, klip skor tertinggi
di jam paling ramai, maks `AUTO_PUBLISH_PER_DAY` per hari, jarak ≥ `AUTO_PUBLISH_MIN_GAP_H` jam dan tidak bentrok
dengan jadwal yang sudah ada → masuk antrean core/scheduler (upload sendiri saat jamnya tiba).
Judul, caption & hashtag ditulis AI (Editor-AI) + hashtag tambahan pengguna (`GLOBAL_HASHTAGS` & per job).

Catatan jujur: TikTok/YouTube tidak membuka data "jam FYP" real-time. Jendela jam memakai pola waktu ramai per niche
(penelitian kreator) + akhir pekan digeser sedikit lebih siang; menit diacak agar tidak terlihat seperti bot.
"""
import random
import re
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from loguru import logger

import config
from . import db, scheduler

# kategori/niche klip → kunci tabel jam terbaik di fyp_agent
_NICHE = {"humor": "comedy", "comedy": "comedy", "motivasi": "motivation", "motivation": "motivation",
          "educational": "education", "education": "education", "shocking": "general", "emotional": "relationship",
          "engagement": "general", "gaming": "gaming", "gaming_stream": "gaming", "finance": "finance",
          "keuangan": "finance", "tech": "tech", "teknologi": "tech", "beauty": "beauty", "relationship": "relationship"}
_PF_LABEL = {"youtube": "YouTube Shorts", "tiktok": "TikTok"}
CATEGORY_CHOICES = {"": "Otomatis (AI)", "gaming": "Gaming", "comedy": "Komedi", "education": "Edukasi",
                    "finance": "Keuangan", "motivation": "Motivasi", "tech": "Teknologi", "relationship": "Hubungan",
                    "beauty": "Kecantikan", "general": "Umum"}


def ready_platforms(wanted: Optional[List[str]] = None) -> List[str]:
    """Platform yang benar-benar siap upload (library + kredensial + login YouTube)."""
    st = scheduler.platform_status()
    out = []
    for p in (wanted or scheduler.PLATFORMS):
        info = st.get(p) or {}
        if info.get("ready") and (p != "youtube" or info.get("logged_in")):
            out.append(p)
    return out


def niche_for(clip: Dict, override: str = "", content_type: str = "") -> str:
    if override:
        return override
    if content_type == "gaming_stream":
        return "gaming"
    for key in (clip.get("niche"), clip.get("category")):
        k = _NICHE.get(str(key or "").lower())
        if k:
            return k
    return "general"


def _windows(niche: str, platform: str) -> List[Tuple[int, int]]:
    """Jendela jam (mulai, selesai) terbaik, urut dari paling ramai."""
    from .fyp_agent import get_best_upload_schedule
    out = []
    for w in get_best_upload_schedule(niche, _PF_LABEL.get(platform, "TikTok")).get("best_times") or []:
        m = re.findall(r"(\d{1,2}):(\d{2})", str(w))
        if m:
            a = int(m[0][0])
            b = int(m[1][0]) if len(m) > 1 else a + 2
            out.append((a, b if b > a else b + 24))
    return out or [(19, 21), (12, 14)]


def _slots(niche: str, platform: str, n: int, start: float, taken: List[float], per_day: int, gap_h: float) -> List[float]:
    """n waktu upload: urut kronologis, jendela paling ramai lebih dulu tiap hari."""
    rnd = random.Random(f"{niche}{platform}{int(start // 3600)}")
    wins = _windows(niche, platform)
    out: List[float] = []
    day = datetime.fromtimestamp(start).replace(hour=0, minute=0, second=0, microsecond=0)
    for _ in range(60):  # maks ±2 bulan ke depan
        weekend = day.weekday() >= 5
        today = []
        for a, b in wins:
            a2 = a + (1 if weekend and a < 12 else 0)  # akhir pekan: orang bangun lebih siang
            span = max(1, b - a2) * 60
            t = (day + timedelta(hours=a2, minutes=rnd.randint(5, max(6, span - 20)))).timestamp()
            w_end = (day + timedelta(hours=b)).timestamp()
            if t <= start < w_end - 20 * 60:  # jendela masih terbuka → pakai sisa jendela hari ini
                t = start + rnd.randint(10, 25) * 60
            today.append(t)
        count = sum(1 for x in taken if datetime.fromtimestamp(x).date() == day.date())
        for ts in today:  # urutan = jendela terbaik dulu
            if len(out) >= n or count >= per_day:
                break
            if ts <= start or any(abs(ts - x) < gap_h * 3600 for x in taken + out):
                continue
            out.append(ts)
            count += 1
        if len(out) >= n:
            break
        day += timedelta(days=1)
    return sorted(out)


def _hashtags(clip: Dict, extra: str) -> List[str]:
    tags: List[str] = []
    for t in list(clip.get("hashtags") or []) + re.findall(r"#?[\w]+", f"{getattr(config, 'GLOBAL_HASHTAGS', '')} {extra or ''}"):
        t = "#" + re.sub(r"[^\w]", "", str(t).lstrip("#"), flags=re.UNICODE)
        if len(t) > 1 and t.lower() not in {x.lower() for x in tags}:
            tags.append(t)
    return tags[:15]


def copy_for(clip: Dict, extra_tags: str = "") -> Dict:
    """Judul + deskripsi (caption AI + hashtag AI + hashtag pengguna)."""
    title = str(clip.get("ai_title") or clip.get("hook_text") or "").strip() or "Klip"
    caption = str(clip.get("upload_caption") or clip.get("hook_text") or "").strip()
    tags = _hashtags(clip, extra_tags)
    return {"title": title[:95], "description": f"{caption}\n\n{' '.join(tags)}".strip(), "hashtags": tags}


def schedule_clips(clips: List[Dict], platforms: Optional[List[str]] = None, niche_override: str = "",
                   extra_tags: str = "", content_type: str = "", start: Optional[float] = None) -> Dict:
    """Jadwalkan semua klip ke jam FYP terbaik. Return {"scheduled": [...], "skipped": alasan}."""
    pfs = ready_platforms(platforms)
    if not pfs:
        return {"scheduled": [], "skipped": "Belum ada platform yang tersambung (YouTube/TikTok) — hubungkan di Jadwal Upload"}
    start = start or time.time() + 15 * 60
    per_day = max(1, min(8, int(getattr(config, "AUTO_PUBLISH_PER_DAY", 3))))
    gap = float(getattr(config, "AUTO_PUBLISH_MIN_GAP_H", 2.5))
    pending = [s for s in db.list_schedules() if s.get("status") == "pending"]
    ranked = sorted([c for c in clips if c.get("file_path")], key=lambda c: -float(c.get("score") or 0))
    out = []
    for pf in pfs:
        taken = [float(s["run_at"]) for s in pending if pf in (s.get("platforms") or [])]
        by_niche: Dict[str, List[Dict]] = {}
        for c in ranked:
            by_niche.setdefault(niche_for(c, niche_override, content_type), []).append(c)
        for niche, group in by_niche.items():
            slots = _slots(niche, pf, len(group), start, taken, per_day, gap)
            # klip terbaik → slot paling awal di jam paling ramai
            for c, ts in zip(group, slots):
                cp = copy_for(c, extra_tags)
                item = db.add_schedule(c["file_path"], cp["title"], cp["description"], [pf], ts)
                taken.append(ts)
                out.append({**item, "niche": niche, "score": c.get("score")})
    out.sort(key=lambda s: s["run_at"])
    logger.info(f"Upload otomatis: {len(out)} jadwal ke {', '.join(pfs)}")
    return {"scheduled": out, "skipped": ""}
