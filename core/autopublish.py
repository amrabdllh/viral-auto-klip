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


def slot_mode() -> str:
    """"riset" (jam dipilih ulang tiap hari) atau "jangkar" (jam tetap per jenis hari + 1 slot eksplorasi)."""
    m = str(getattr(config, "UPLOAD_SLOT_MODE", "riset") or "riset").strip().lower()
    return m if m in ("riset", "jangkar") else "riset"


def parse_hours(text: str) -> List[float]:
    """"09:00, 13:30 18 20.30" → [9.0, 13.5, 18.0, 20.5] (urut, unik). Menit dibulatkan ke 0,25 jam."""
    out = set()
    for h, m in re.findall(r"(?<!\d)(\d{1,2})(?:[:.](\d{2}))?(?!\d)", str(text or "")):
        hh, mm = int(h), int(m or 0)
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            out.add(hh + round(mm / 15) * 0.25)
    return sorted(x for x in out if x < 24)


def format_hours(hours: List[float]) -> str:
    return ",".join(f"{int(h):02d}:{int(round(h % 1 * 60)):02d}" for h in hours)


def fixed_hours(platform: str, day: Optional[datetime] = None) -> List[float]:
    """Jam tetap akun dari Pengaturan; kosong = pakai riset/jangkar.
    Senin–Jumat: TIKTOK_POST_HOURS / YOUTUBE_POST_HOURS. Sabtu–Minggu: *_POST_HOURS_WEEKEND (kosong = sama dengan
    Senin–Jumat). Tanpa `day` → jam Senin–Jumat."""
    weekday = parse_hours(getattr(config, f"{platform.upper()}_POST_HOURS", "") or "")
    if day is not None and day.weekday() >= 5:
        return parse_hours(getattr(config, f"{platform.upper()}_POST_HOURS_WEEKEND", "") or "") or weekday
    return weekday


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


def rule_for_day(niche: str, platform: str, day: datetime, quick: bool = True) -> Dict:
    """Jam yang DIPAKAI pengaturan saat ini untuk `day` (sebelum menit diacak & tanpa memperhitungkan jadwal yang
    sudah ada) — untuk ditampilkan di halaman Jadwal Upload, sama dengan sumber yang dipakai _day_slots.
    quick=True: tidak memicu riset pasar ±20 dtk; bila jamnya belum bisa dihitung, "hours" kosong & "pending" True."""
    from .fyp_research import anchor_plan, anchors_cached, market_fresh, pick_hours
    from .slot_learning import day_type
    day = day.replace(hour=0, minute=0, second=0, microsecond=0)
    cap = max(1, min(8, int(getattr(config, "AUTO_PUBLISH_PER_DAY", 3))))
    gap = float(getattr(config, "AUTO_PUBLISH_MIN_GAP_H", 2.5))
    hhmm = lambda t: f"{int(t):02d}:{int(round(t % 1 * 60)):02d}"  # noqa: E731
    base = {"per_day": cap, "gap_h": gap, "pending": False}
    own = fixed_hours(platform, day)
    if own:
        weekend = day.weekday() >= 5
        own_we = bool(parse_hours(getattr(config, f"{platform.upper()}_POST_HOURS_WEEKEND", "") or ""))
        return {**base, "source": "akun", "label": "Jam tetap dari Pengaturan · " + (
                    "Sabtu–Minggu" if weekend and own_we else "Senin–Jumat" if not weekend else "Sabtu–Minggu (sama dengan Senin–Jumat)"),
                "hours": [{"time": hhmm(h), "why": "jam akun"} for h in own],
                "note": f"menit diacak 0–15 · maks {cap}/hari" + (f" (dari {len(own)} jam, dipakai yang paling ramai)"
                                                                  if len(own) > cap else "")}
    dtype = day_type(day)
    mode = slot_mode()
    proven = _proven_hours(platform, day)
    if mode == "jangkar":
        label = f"Jam jangkar {'akhir pekan' if dtype == 'weekend' else 'hari kerja'}"
        ok = not quick or market_fresh(niche) or bool(anchors_cached(niche, platform, dtype, cap, gap))
        picked = anchor_plan(niche, platform, day, cap, gap) if ok else []
        note = f"jam tetap + 1 slot eksplorasi bergiliran · maks {cap}/hari, jarak ≥ {gap:g} jam"
    else:
        label = "Riset hari ini"
        ok = not quick or market_fresh(niche)
        picked = pick_hours(niche, platform, day, cap, gap, list(proven), 0.0) if ok else []
        note = f"dipilih ulang tiap hari · maks {cap}/hari, jarak ≥ {gap:g} jam"
    rows = [(h, "terbukti di channel ini") for h in proven]
    rows += [(h, why) for h, why in picked if all(abs(h - p) >= gap for p in proven)]
    rows = sorted(rows[:cap])
    return {**base, "source": mode, "label": label, "note": note, "pending": not ok,
            "hours": [{"time": hhmm(h), "why": why} for h, why in rows]}


def _day_slots(niche: str, platform: str, day: datetime, want: int, start: float, busy: List[float],
               gap_h: float, rnd: random.Random) -> List[Tuple[float, str]]:
    """≤ want waktu upload di tanggal `day` untuk satu platform: jam terbukti (channel ini) dulu, lalu jam hasil
    RISET hari itu (core/fyp_research), berjarak ≥ gap_h dari satu sama lain & dari `busy` (jadwal yang sudah ada)."""
    from .fyp_research import anchor_plan, pick_hours
    d0 = day.timestamp()
    # jam yang sudah terisi (termasuk larut malam kemarin & dini hari besok) — dihindari optimizer
    fixed = [(x - d0) / 3600 for x in busy if -6 * 3600 < x - d0 < 30 * 3600]
    min_hour = max(0.0, (start - d0) / 3600 - 1.5) if start > d0 else 0.0
    own = fixed_hours(platform, day)
    if own:
        return _fixed_day_slots(niche, platform, day, own, want, start, busy, gap_h, rnd)
    proven =[(h, "terbukti di channel ini") for h in _proven_hours(platform, day)
              if h >= min_hour and all(abs(h - f) >= gap_h for f in fixed)]
    if slot_mode() == "jangkar":
        # jam tetap per jenis hari (+1 slot eksplorasi bergiliran); yang bentrok/lewat dibuang
        cap = max(1, min(8, int(getattr(config, "AUTO_PUBLISH_PER_DAY", 3))))
        picked = [(h, why) for h, why in anchor_plan(niche, platform, day, cap, gap_h)
                  if h >= min_hour and all(abs(h - f) >= gap_h for f in fixed + [p[0] for p in proven])]
    else:
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


def _fixed_day_slots(niche: str, platform: str, day: datetime, hours: List[float], want: int, start: float,
                     busy: List[float], gap_h: float, rnd: random.Random) -> List[Tuple[float, str]]:
    """Jam TETAP pilihan pengguna (dari Analisis akunnya): dipakai apa adanya tiap hari, menit diacak 0–15 agar tidak
    terlihat bot. Jarak minimal mengikuti jarak antar jam pilihan itu sendiri (pengguna yang menentukan).
    Urut jam paling ramai dulu (kurva riset), supaya hari yang klipnya sedikit memakai jam terbaik."""
    from .fyp_research import _score_at, research_day
    spacing = min([b - a for a, b in zip(hours, hours[1:])] or [gap_h])
    gap = max(0.5, min(gap_h, spacing) - 0.5)
    curve = {int(h): s for h, s in research_day(niche, platform, day, use_market=False)["hours"]}
    order = sorted(hours, key=lambda h: -_score_at(curve, h))
    hhmm = lambda t: f"{int(t):02d}:{int(round(t % 1 * 60)):02d}"  # noqa: E731
    day_off = rnd.randint(0, 10)
    out: List[Tuple[float, str]] = []
    for h in order:
        if len(out) >= want:
            break
        ts = (day + timedelta(hours=h, minutes=day_off + rnd.randint(0, 5))).timestamp()
        if ts <= start + 25 * 60:  # sudah lewat / terlalu mepet untuk jadwal bawaan platform
            continue
        if any(abs(ts - x) < gap * 3600 for x in busy + [o[0] for o in out]):
            continue
        out.append((ts, f"jam akun {hhmm(h)}"))
    return out


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
                        own = fixed_hours(pf, day)
                        from .fyp_research import _DAYS
                        head = (f"{_DAYS[day.weekday()]} {day.day} {day.strftime('%b')} · {pf}: jam akun "
                                f"{format_hours(own).replace(',', ', ')}") if own else research_day(niche, pf, day)["summary"]
                        plans.append(head + f" → {k} klip"
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


# hashtag niche yang ikut dinilai riset (setelah hashtag Editor-AI, jadi relevansinya sedikit lebih rendah)
_SEED_TAGS = {"gaming": ["#gamingindonesia", "#gaming"], "comedy": ["#lucu", "#ngakak"], "education": ["#edukasi", "#faktaunik"],
              "motivation": ["#motivasi"], "finance": ["#keuangan", "#investasi"], "tech": ["#teknologi"],
              "relationship": ["#relationship"], "beauty": ["#skincare"]}
TITLE_MAX = 60  # judul inti — YouTube: + hashtag di judul (maks 100 karakter), TikTok: + hashtag di caption


def short_title(text: str, limit: int = TITLE_MAX) -> str:
    """Judul sependek teks thumbnail: hashtag dibuang, lalu dipotong di jeda alami (— : ? ! ,) bila terlalu panjang."""
    t = re.sub(r"\s*#\w+", "", str(text or ""), flags=re.UNICODE).strip()
    t = re.sub(r"\s{2,}", " ", t)
    if len(t) <= limit:
        return t
    for sep in (" — ", " – ", " - ", ": ", "?! ", "? ", "! ", "… ", ". ", ", "):
        k = t.find(sep)
        if 20 <= k <= limit - len(sep.strip()):
            return (t[:k] + sep.strip()).rstrip(" ,.:—–-")
    cut = t[:limit].rsplit(" ", 1)[0].rstrip(" ,.:;—–-")
    return cut + "…"


def short_caption(text: str, limit: Optional[int] = None) -> str:
    """Caption pendek dari KALIMAT UTUH (tidak berhenti di tengah kalimat): kalimat demi kalimat selama muat."""
    from .utils import trim_caption
    limit = int(limit or getattr(config, "UPLOAD_CAPTION_MAX_CHARS", 90))
    t = re.sub(r"\s*#\w+", "", str(text or ""), flags=re.UNICODE)
    t = re.sub(r"\s+", " ", t).strip()
    out = ""
    for sent in re.split(r"(?<=[.?!…])\s+", t):
        nxt = f"{out} {sent}".strip()
        if len(nxt) > limit:
            break
        out = nxt
    return out if len(out) >= 15 else trim_caption(t, limit)


def copy_for(clip: Dict, extra_tags: str = "", research: bool = True) -> Dict:
    """Judul pendek + deskripsi (1 kalimat caption + hashtag hasil riset). Hashtag: kandidat Editor-AI + niche dinilai
    core/hashtag_research (yang mati dibuang, topik aktif diutamakan) → 5 terbaik, + hashtag pilihan pengguna."""
    from . import hashtag_research
    title = short_title(clip.get("ai_title") or clip.get("hook_text") or "") or "Klip"
    caption = short_caption(clip.get("upload_caption") or clip.get("hook_text") or "")
    user = [t for t in re.findall(r"#?\w+", f"{getattr(config, 'GLOBAL_HASHTAGS', '')} {extra_tags or ''}") if t.strip("#")]
    niche = niche_for(clip)
    cands = list(clip.get("hashtags") or []) + _SEED_TAGS.get(niche, [])
    # total ±5 hashtag: hashtag pilihan pengguna selalu ikut, sisanya dari riset (minimal 3)
    picked = hashtag_research.pick(cands, max(3, 5 - len(user)), keep=user, fetch=research) if cands else []
    tags: List[str] = []
    for t in picked + ["#" + hashtag_research.clean(u) for u in user]:
        if len(t) > 1 and t.lower() not in {x.lower() for x in tags}:
            tags.append(t)
    return {"title": title[:95], "description": f"{caption}\n\n{' '.join(tags)}".strip(), "hashtags": tags}


def schedule_clips(clips: List[Dict], platforms: Optional[List[str]] = None, niche_override: str = "",
                   extra_tags: str = "", content_type: str = "", start: Optional[float] = None,
                   dry_run: bool = False) -> Dict:
    """Jadwalkan semua klip ke jam FYP terbaik. Return {"scheduled": [...], "skipped": alasan}.
    dry_run=True: hanya rencana (pratinjau), tidak ada yang disimpan."""
    pfs = ready_platforms(platforms)
    if not pfs:
        return {"scheduled": [], "skipped": "Belum ada platform yang tersambung (YouTube/TikTok) — hubungkan di Jadwal Upload"}
    start = start or time.time() + 15 * 60
    per_day = max(1, min(8, int(getattr(config, "AUTO_PUBLISH_PER_DAY", 3))))
    gap = float(getattr(config, "AUTO_PUBLISH_MIN_GAP_H", 2.5))
    # slot yang sudah terisi: menunggu, sedang diunggah, ATAU sudah terjadwal di platform (jadwal bawaan YouTube/TikTok)
    pending = [s for s in db.list_schedules() if s.get("status") in ("pending", "uploading", "scheduled")]
    ranked = sorted([c for c in clips if c.get("file_path")], key=lambda c: -float(c.get("score") or 0))
    out, plans = [], []
    taken = {pf: [float(s["run_at"]) for s in pending if pf in (s.get("platforms") or [])] for pf in pfs}
    by_niche: Dict[str, List[Dict]] = {}
    for c in ranked:
        by_niche.setdefault(niche_for(c, niche_override, content_type), []).append(c)
    if by_niche and not dry_run:
        db.kv_set("autopublish_last_niche", max(by_niche, key=lambda k: len(by_niche[k])))
    for niche, group in by_niche.items():
        # klip terbaik → hari paling awal; tiap klip sekali per platform, di hari yang sama
        for c, slot in zip(group, _plan(niche, pfs, len(group), start, taken, per_day, gap, plans)):
            cp = copy_for(c, extra_tags)
            for pf, (ts, why) in slot.items():
                if pf in (c.get("_skip_platforms") or []):
                    continue  # klip ini sudah ada di platform itu (riwayat unggahan) → jangan dobel
                item = ({"file_path": c["file_path"], "title": cp["title"], "description": cp["description"],
                         "platforms": [pf], "run_at": ts, "status": "preview"} if dry_run
                        else db.add_schedule(c["file_path"], cp["title"], cp["description"], [pf], ts))
                taken[pf].append(ts)
                out.append({**item, "niche": niche, "score": c.get("score"), "slot": why})
    out.sort(key=lambda s: s["run_at"])
    if not dry_run:
        logger.info(f"Upload otomatis: {len(out)} jadwal ke {', '.join(pfs)}")
    return {"scheduled": out, "skipped": "", "plans": plans, "per_day": per_day, "platforms": pfs}


def clip_from_asset(asset: Dict) -> Dict:
    """Aset Video Saya → bentuk klip yang dipahami schedule_clips (judul/caption/hashtag/skor dari meta hasil job)."""
    meta = asset.get("meta") or {}
    return {"file_path": asset["path"], "score": meta.get("score") or 0, "category": meta.get("category"),
            "niche": meta.get("niche"), "hashtags": meta.get("hashtags") or [],
            "ai_title": meta.get("ai_title") or meta.get("title") or asset.get("name") or "",
            "hook_text": meta.get("hook_text") or "", "thumb_text": meta.get("thumb_text") or "",
            "upload_caption": meta.get("upload_caption") or meta.get("description") or ""}
