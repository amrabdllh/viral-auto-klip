"""Upload otomatis (v6.8): klip hasil Analisis AI langsung dijadwalkan ke jam FYP terbaik — pengguna tidak perlu
mengisi jadwal sendiri.

Alur: klip selesai dirender → niche ditentukan (otomatis dari isi klip, atau dipilih pengguna) → jendela jam terbaik
per platform & niche (fyp_agent) → slot hari ini (yang belum lewat) lalu hari-hari berikutnya, klip skor tertinggi
di jam paling ramai, maks `AUTO_PUBLISH_PER_DAY` per hari, jarak ≥ `AUTO_PUBLISH_MIN_GAP_H` jam dan tidak bentrok
dengan jadwal yang sudah ada → masuk antrean core/scheduler (upload sendiri saat jamnya tiba).
Judul, caption & hashtag ditulis AI (Editor-AI) + hashtag tambahan pengguna (`GLOBAL_HASHTAGS` & per job).

Catatan jujur: TikTok/YouTube tidak membuka data "jam FYP" real-time. Jendela jam memakai pola waktu ramai per niche
(penelitian kreator); sejak v6.9 tiap hari DIRISET dulu (core/fyp_research: kurva penonton per hari + data Shorts
nyata), slot kosong hari itu diisi dulu (jadwal lama ikut dihitung), sisanya ke hari berikutnya yang diriset ulang.
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
        if info.get("ready") and (p != "youtube" or info.get("logged_in") or info.get("browser")):
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


def _proven_hours(platform: str, day: datetime) -> List[float]:
    """Jam yang sudah TERBUKTI ramai untuk channel ini (core/slot_learning), bila datanya cukup."""
    from .slot_learning import MIN_SAMPLES_HOUR, day_type, insights
    ins = insights(platform)
    if not ins["ready"]:
        return []
    dtype = day_type(day)
    good = sorted(((h, s["score"]) for (d, h), s in ins["hours"].items()
                   if d == dtype and s["n"] >= MIN_SAMPLES_HOUR and s["score"] >= 1.15), key=lambda x: -x[1])
    return [float(h) for h, _ in good]


def _day_slots(niche: str, platform: str, day: datetime, want: int, start: float, busy: List[float],
               gap_h: float, rnd: random.Random) -> List[Tuple[float, str]]:
    """≤ want waktu upload di tanggal `day` untuk satu platform: jam terbukti (channel ini) dulu, lalu jam hasil
    RISET hari itu (core/fyp_research), berjarak ≥ gap_h dari satu sama lain & dari `busy` (jadwal yang sudah ada)."""
    from .fyp_research import pick_hours
    d0 = day.timestamp()
    # jam yang sudah terisi (termasuk larut malam kemarin & dini hari besok) — dihindari optimizer
    fixed = [(x - d0) / 3600 for x in busy if -6 * 3600 < x - d0 < 30 * 3600]
    min_hour = max(0.0, (start - d0) / 3600 - 1.5) if start > d0 else 0.0
    proven = [(h, "terbukti di channel ini") for h in _proven_hours(platform, day)
              if h >= min_hour and all(abs(h - f) >= gap_h for f in fixed)]
    picked = pick_hours(niche, platform, day, want, gap_h, fixed + [p[0] for p in proven], min_hour)
    cands = sorted(proven + picked, key=lambda c: 0 if c[1].startswith("terbukti") else 1)
    # menit bervariasi agar tidak terlihat bot: geser harian 5–20 mnt + 0–6 mnt per slot (jarak tetap terjaga)
    day_off = rnd.randint(5, 20)
    out: List[Tuple[float, str]] = []
    for h, why in cands:
        if len(out) >= want:
            break
        ts = (day + timedelta(hours=h, minutes=day_off + rnd.randint(0, 6))).timestamp()
        if ts <= start < ts + 100 * 60:  # jendela jam ini masih berjalan → pakai sisanya hari ini
            ts = start + rnd.randint(10, 25) * 60
        if ts <= start:
            continue
        if any(abs(ts - x) < gap_h * 3600 - 6 * 60 for x in busy + [o[0] for o in out]):
            continue
        out.append((ts, why))
    return out  # urut dari jam paling berpeluang


def _plan(niche: str, platforms: List[str], n: int, start: float, taken: Dict[str, List[float]], per_day: int,
          gap_h: float, plans: Optional[List[str]] = None) -> List[Dict[str, Tuple[float, str]]]:
    """Rencana n klip (urut skor) → [{platform: (waktu, alasan)}], hari per hari.

    Satu klip diunggah SEKALI ke tiap platform, di HARI YANG SAMA di semua platform: jumlah klip per hari = slot kosong
    tersempit di antara platform (maks `per_day` per platform, jadwal lama ikut dihitung). Tiap hari diriset dulu;
    yang tidak muat pindah ke hari berikutnya yang diriset ulang."""
    from .fyp_research import research_day
    rnd = random.Random(f"{niche}{'+'.join(platforms)}{int(start // 3600)}")
    busy = {pf: list(taken.get(pf) or []) for pf in platforms}
    out: List[Dict[str, Tuple[float, str]]] = []
    day = datetime.fromtimestamp(start).replace(hour=0, minute=0, second=0, microsecond=0)
    for _ in range(60):  # maks ±2 bulan ke depan
        if len(out) >= n:
            break
        same = {pf: [x for x in busy[pf] if datetime.fromtimestamp(x).date() == day.date()] for pf in platforms}
        # cari jam untuk KAPASITAS penuh hari itu (bukan hanya sisa klip) supaya klip dari link berikutnya
        # masih kebagian slot bagus di hari ini; lalu pakai yang terbaik sebanyak yang dibutuhkan
        cap = min(per_day - len(same[pf]) for pf in platforms)
        if cap > 0:
            picks = {pf: _day_slots(niche, pf, day, cap, start, busy[pf], gap_h, rnd) for pf in platforms}
            k = min([len(v) for v in picks.values()] + [n - len(out)])
            if k:
                for pf in platforms:
                    # k jam terbaik hari itu, lalu kronologis: klip skor tertinggi dapat slot paling awal
                    chosen = sorted(picks[pf][:k])
                    busy[pf].extend(t for t, _ in chosen)
                    picks[pf] = chosen
                    if plans is not None:
                        plans.append(research_day(niche, pf, day)["summary"] + f" → {k} klip"
                                     + (f" (+{len(same[pf])} sudah terjadwal)" if same[pf] else ""))
                for i in range(k):
                    out.append({pf: picks[pf][i] for pf in platforms})
        day += timedelta(days=1)
    return out


def _slots(niche: str, platform: str, n: int, start: float, taken: List[float], per_day: int,
           gap_h: float, plans: Optional[List[str]] = None) -> List[Tuple[float, str]]:
    """Versi satu platform dari _plan (kompatibilitas)."""
    return [p[platform] for p in _plan(niche, [platform], n, start, {platform: taken}, per_day, gap_h, plans)]


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
    out, plans = [], []
    taken = {pf: [float(s["run_at"]) for s in pending if pf in (s.get("platforms") or [])] for pf in pfs}
    by_niche: Dict[str, List[Dict]] = {}
    for c in ranked:
        by_niche.setdefault(niche_for(c, niche_override, content_type), []).append(c)
    if by_niche:
        db.kv_set("autopublish_last_niche", max(by_niche, key=lambda k: len(by_niche[k])))
    for niche, group in by_niche.items():
        # klip terbaik → hari paling awal; tiap klip sekali per platform, di hari yang sama
        for c, slot in zip(group, _plan(niche, pfs, len(group), start, taken, per_day, gap, plans)):
            cp = copy_for(c, extra_tags)
            for pf, (ts, why) in slot.items():
                item = db.add_schedule(c["file_path"], cp["title"], cp["description"], [pf], ts)
                taken[pf].append(ts)
                out.append({**item, "niche": niche, "score": c.get("score"), "slot": why})
    out.sort(key=lambda s: s["run_at"])
    logger.info(f"Upload otomatis: {len(out)} jadwal ke {', '.join(pfs)}")
    return {"scheduled": out, "skipped": "", "plans": plans, "per_day": per_day}
