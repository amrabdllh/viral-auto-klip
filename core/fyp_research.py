"""Riset jam FYP per HARI & niche (v6.9) — dipakai upload otomatis sebelum menjadwalkan tiap hari.

Kenapa: TikTok/YouTube tidak membuka data "jam FYP". Yang bisa dilakukan dengan jujur:
1. KURVA PENONTON Indonesia (WIB) per jam, berbeda tiap hari: hari kerja (puncak 19–22, istirahat siang 12, pulang
   sekolah/kerja 16–18), Jumat (siang turun karena salat Jumat, malam lebih larut), Sabtu (siang ramai, malam paling
   larut), Minggu (siang ramai, puncak malam lebih awal karena besok Senin). Dikalikan pengubah niche (gaming kuat
   sore & larut malam, keuangan pagi, motivasi subuh, dst.) dan jendela niche lama (fyp_agent).
2. DATA NYATA: Shorts populer terbaru (≤120 hari) dari halaman hashtag niche itu di YouTube Indonesia — jam unggah & performanya
   (views ÷ umur). Jam yang terbukti perform di HARI YANG SAMA dinaikkan, yang lemah diturunkan (maks ±25%).
   Disimpan 24 jam per niche (±35 permintaan sekali riset) agar tidak memicu batas permintaan YouTube.
3. Data channel sendiri (core/slot_learning) tetap menang bila sudah cukup.

Output: daftar jam kandidat untuk satu tanggal, urut dari paling berpeluang, plus alasan yang bisa dibaca pengguna.
"""
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from loguru import logger

from . import db

_DAYS = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]

# Kurva aktivitas penonton (0–100) per jam WIB. Hari kerja / Jumat / Sabtu / Minggu.
_WEEKDAY = [35, 20, 10, 6, 6, 12, 25, 35, 35, 35, 38, 48, 62, 58, 48, 52, 62, 68, 72, 88, 100, 96, 80, 55]
_FRIDAY = [35, 20, 10, 6, 6, 12, 25, 35, 35, 36, 40, 42, 40, 48, 50, 55, 64, 70, 74, 88, 98, 100, 90, 68]
_SATURDAY = [50, 35, 18, 8, 6, 8, 15, 22, 30, 45, 58, 65, 68, 70, 66, 66, 70, 70, 72, 88, 98, 100, 92, 72]
_SUNDAY = [45, 28, 14, 7, 6, 8, 16, 24, 38, 50, 62, 68, 70, 72, 68, 66, 70, 72, 78, 95, 100, 90, 70, 45]

# Pengubah niche: (jam mulai, jam selesai, pengali)
_NICHE_MOD = {
    "gaming": [(6, 9, 0.7), (15, 18, 1.12), (21, 24, 1.15), (0, 2, 1.2)],
    "finance": [(6, 9, 1.3), (12, 13, 1.15), (17, 19, 1.1), (22, 24, 0.8), (0, 3, 0.7)],
    "motivation": [(5, 7, 1.5), (21, 23, 1.05), (0, 3, 0.7)],
    "education": [(7, 9, 1.15), (18, 20, 1.1), (0, 3, 0.7)],
    "comedy": [(12, 14, 1.1), (20, 24, 1.1)],
    "relationship": [(21, 24, 1.2), (0, 1, 1.1)],
    "tech": [(7, 9, 1.1), (18, 20, 1.05)],
    "beauty": [(9, 11, 1.1), (18, 20, 1.1)],
}
# Halaman hashtag Shorts YouTube (berisi Shorts aktual, bukan video panjang seperti hasil pencarian biasa)
_TAGS = {"gaming": ["gamingindonesia", "gameindonesia"], "comedy": ["lucu", "ngakak"], "education": ["edukasi", "faktaunik"],
         "finance": ["keuangan", "investasi"], "motivation": ["motivasi", "motivasihidup"], "tech": ["teknologi", "gadget"],
         "relationship": ["relationship", "bucin"], "beauty": ["makeup", "skincare"], "general": ["viral", "fyp"]}
_CACHE_H = 24.0


def _base(dt: datetime) -> List[float]:
    wd = dt.weekday()
    return list(_FRIDAY if wd == 4 else _SATURDAY if wd == 5 else _SUNDAY if wd == 6 else _WEEKDAY)


def _niche_windows(niche: str, platform: str) -> List[Tuple[int, int]]:
    try:
        from .autopublish import _windows
        return _windows(niche, platform)
    except Exception:  # noqa: BLE001
        return []


# ----------------------------------------------------------------------------- data nyata YouTube

def _video_meta(vid: str) -> Optional[Dict]:
    import yt_dlp
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as y:
            i = y.extract_info(f"https://www.youtube.com/watch?v={vid}", download=False)
        if i and i.get("timestamp") and i.get("view_count") is not None:
            return {"ts": float(i["timestamp"]), "views": int(i["view_count"])}
    except Exception:  # noqa: BLE001
        pass
    return None


def market_fresh(niche: str) -> bool:
    """Data pasar niche ini masih segar di cache (memakainya tidak memicu riset ±20 dtk)."""
    cached = db.kv_get(f"fyp_research_{niche}", None)
    return bool(cached) and time.time() - float(cached.get("at", 0)) < _CACHE_H * 3600


def market_samples(niche: str, force: bool = False) -> List[Dict]:
    """Shorts populer ≤120 hari untuk niche: [{ts, views, hour, weekday, perf}]. Cache 24 jam."""
    key = f"fyp_research_{niche}"
    cached = db.kv_get(key, None)
    if cached and not force and time.time() - float(cached.get("at", 0)) < _CACHE_H * 3600:
        return cached.get("rows") or []
    import yt_dlp
    rows: List[Dict] = []
    try:
        ids: List[str] = []
        for tag in _TAGS.get(niche, _TAGS["general"]):
            with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "extract_flat": "in_playlist",
                                   "playlistend": 20}) as y:
                data = y.extract_info(f"https://www.youtube.com/hashtag/{tag}/shorts", download=False)
            ids += [e["id"] for e in data.get("entries") or [] if e and e.get("id") and e["id"] not in ids]
        with ThreadPoolExecutor(max_workers=3) as ex:
            metas = [m for m in ex.map(_video_meta, ids[:32]) if m and time.time() - m["ts"] <= 120 * 86400]
        now = time.time()
        for m in metas:
            age_h = max(6.0, (now - m["ts"]) / 3600)
            local = datetime.fromtimestamp(m["ts"])
            rows.append({"ts": m["ts"], "views": m["views"], "hour": local.hour, "weekday": local.weekday(),
                         "perf": m["views"] / (age_h ** 0.6)})
        db.kv_set(key, {"at": time.time(), "rows": rows})
        logger.info(f"Riset jam FYP '{niche}': {len(rows)} Shorts dianalisis")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Riset jam FYP '{niche}' gagal (pakai kurva saja): {str(exc)[:160]}")
        if cached:
            return cached.get("rows") or []
    return rows


def _market_multipliers(rows: List[Dict], weekday: int) -> Tuple[Dict[int, float], int, bool]:
    """Pengali per jam dari data nyata. Pakai hari yang sama bila sampelnya cukup, selain itu semua hari."""
    same = [r for r in rows if r["weekday"] == weekday]
    use, same_day = (same, True) if len(same) >= 8 else (rows, False)
    if len(use) < 8:
        return {}, len(use), same_day
    med = statistics.median(r["perf"] for r in use) or 1.0
    by_h: Dict[int, List[float]] = {}
    for r in use:
        # jam unggah h → video itu ramai di jam h..h+2; sebar ke jam tetangga
        for dh, w in ((0, 1.0), (1, 0.6), (-1, 0.4)):
            by_h.setdefault((r["hour"] + dh) % 24, []).extend([r["perf"]] * (2 if w == 1.0 else 1))
    out = {}
    for h, v in by_h.items():
        if len(v) >= 3:
            out[h] = max(0.75, min(1.25, (statistics.median(v) / med) ** 0.5))
    return out, len(use), same_day


# ----------------------------------------------------------------------------- rencana per hari

def research_day(niche: str, platform: str, dt: datetime, use_market: bool = True) -> Dict:
    """{"hours": [(jam_float, skor)], "summary": str, "samples": n} untuk tanggal `dt`."""
    curve = _base(dt)
    for a, b, m in _NICHE_MOD.get(niche, []):
        for h in range(a, b):
            curve[h % 24] *= m
    for a, b in _niche_windows(niche, platform):
        for h in range(a, b):
            curve[h % 24] *= 1.08
    if platform == "youtube":  # Shorts: puncak malam sedikit lebih larut, siang sedikit lebih lemah
        for h in (21, 22):
            curve[h] *= 1.05
        for h in (11, 12):
            curve[h] *= 0.95
    rows = market_samples(niche) if use_market else []
    mult, n, same_day = _market_multipliers(rows, dt.weekday())
    for h, m in mult.items():
        curve[h] *= m
    ranked = sorted(((float(h), round(s, 1)) for h, s in enumerate(curve)), key=lambda x: -x[1])
    top = ", ".join(f"{int(h):02d}:00" for h, _ in ranked[:4])
    src = (f"{n} Shorts {niche} hari {_DAYS[dt.weekday()]}" if same_day else f"{n} Shorts {niche} terbaru") if mult \
        else "kurva penonton Indonesia"
    return {"hours": ranked, "samples": n, "market": bool(mult),
            "summary": f"{_DAYS[dt.weekday()]} {dt.strftime('%d %b')} · {niche}/{platform}: puncak {top} (riset: {src})"}


def pick_hours(niche: str, platform: str, dt: datetime, n: int, gap_h: float,
               fixed: List[float], min_hour: float = 0.0) -> List[Tuple[float, str]]:
    """≤ n jam (kelipatan 30 menit) di tanggal `dt`, berjarak ≥ gap_h satu sama lain & dari `fixed` (jam yang sudah
    terisi), tidak sebelum `min_hour`. Dipilih kombinasi dengan JUMLAH slot terbanyak lalu total skor tertinggi
    (bukan rakus satu per satu — rakus sering hanya muat 3–4 slot). Jam sepi (< 50% puncak hari itu) tidak dipakai:
    klip lebih baik pindah ke hari berikutnya. Urutan hasil: skor tertinggi dulu (klip terbaik → jam terbaik)."""
    plan = research_day(niche, platform, dt)
    curve = {int(h): s for h, s in plan["hours"]}
    floor = 0.5 * max(curve.values() or [0])
    late_ok = niche in ("gaming", "relationship", "comedy")
    cands: List[Tuple[float, float]] = []
    for k in range(48):
        t = k / 2
        score = curve.get(int(t), 0) if k % 2 == 0 else (curve.get(int(t), 0) + curve.get((int(t) + 1) % 24, 0)) / 2
        if score < floor or t < min_hour or (t < 6 and not late_ok):
            continue
        if any(abs(t - f) < gap_h for f in fixed):
            continue
        cands.append((t, score))
    # DP: best[j][i] = (total skor, jalur) untuk j slot dengan slot terakhir di kandidat i
    best: List[List[Optional[Tuple[float, List[int]]]]] = [[None] * len(cands) for _ in range(n + 1)]
    for i, (_, s) in enumerate(cands):
        best[1][i] = (s, [i])
    for j in range(2, n + 1):
        for i, (t, s) in enumerate(cands):
            prev = [best[j - 1][p] for p in range(i) if best[j - 1][p] and t - cands[p][0] >= gap_h]
            if prev:
                top = max(prev, key=lambda x: x[0])
                best[j][i] = (top[0] + s, top[1] + [i])
    pickset: List[int] = []
    for j in range(n, 0, -1):
        row = [b for b in best[j] if b]
        if row:
            pickset = max(row, key=lambda x: x[0])[1]
            break
    chosen = [(cands[i][0], f"riset {_DAYS[dt.weekday()]} {int(cands[i][0]):02d}:{'30' if cands[i][0] % 1 else '00'} "
                            f"(skor {cands[i][1]:.0f})", cands[i][1]) for i in pickset]
    chosen.sort(key=lambda c: -c[2])
    return [(t, why) for t, why, _ in chosen]



# ----------------------------------------------------------------------------- mode jam jangkar
_ANCHOR_DAYS = 7.0  # jangkar dikunci seminggu → jam tetap stabil, tetap ikut riset terbaru tiap minggu


def _canonical(dtype: str) -> datetime:
    """Tanggal wakil jenis hari: hari kerja → Rabu, akhir pekan → Sabtu (minggu ini/depan)."""
    from datetime import timedelta
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    target = 2 if dtype == "weekday" else 5
    return today + timedelta(days=(target - today.weekday()) % 7)


def anchors_cached(niche: str, platform: str, dtype: str, n: int, gap_h: float) -> List[Tuple[float, float]]:
    """Jam jangkar yang sudah tersimpan & masih berlaku (kosong bila perlu dihitung ulang)."""
    cached = db.kv_get(f"slot_anchors_{niche}_{platform}_{dtype}_{n}_{gap_h:g}", None)
    if cached and time.time() - float(cached.get("at", 0)) < _ANCHOR_DAYS * 86400 and cached.get("hours"):
        return [(float(h), float(s)) for h, s in cached["hours"]]
    return []


def anchor_hours(niche: str, platform: str, dtype: str, n: int, gap_h: float) -> List[Tuple[float, float]]:
    """Jam jangkar TETAP untuk jenis hari `dtype` ("weekday" = Sen–Jum, "weekend" = Sab–Min): kombinasi n jam
    terbaik berjarak ≥ gap_h dari riset hari wakilnya. Disimpan seminggu agar tiap hari kerja/akhir pekan jamnya
    sama persis. Return [(jam, skor)] urut kronologis."""
    cached = anchors_cached(niche, platform, dtype, n, gap_h)
    if cached:
        return cached
    key = f"slot_anchors_{niche}_{platform}_{dtype}_{n}_{gap_h:g}"
    day = _canonical(dtype)
    curve = {int(h): s for h, s in research_day(niche, platform, day)["hours"]}
    picked = pick_hours(niche, platform, day, n, gap_h, [], 0.0)
    hours = sorted((t, _score_at(curve, t)) for t, _ in picked)
    db.kv_set(key, {"at": time.time(), "hours": hours})
    return hours


def _score_at(curve: Dict[int, float], t: float) -> float:
    if t % 1 == 0:
        return float(curve.get(int(t), 0))
    return (curve.get(int(t), 0) + curve.get((int(t) + 1) % 24, 0)) / 2


def anchor_plan(niche: str, platform: str, dt: datetime, n: int, gap_h: float) -> List[Tuple[float, str]]:
    """Jam jangkar untuk tanggal `dt` + SATU slot eksplorasi: satu jangkar (bergiliran tiap hari, bukan jam terbaik)
    digeser ±1 jam untuk menguji jam tetangga — hasilnya dipelajari core/slot_learning. Urut skor tertinggi dulu."""
    dtype = "weekend" if dt.weekday() >= 5 else "weekday"
    label = "akhir pekan" if dtype == "weekend" else "hari kerja"
    anchors = anchor_hours(niche, platform, dtype, n, gap_h)
    if not anchors:
        return []
    curve = {int(h): s for h, s in research_day(niche, platform, dt, use_market=False)["hours"]}
    floor = 0.5 * max(curve.values() or [0])
    hhmm = lambda t: f"{int(t):02d}:{'30' if t % 1 else '00'}"  # noqa: E731
    # jangkar yang jatuh di jam sepi HARI INI (mis. Jumat siang saat salat Jumat) dilewati, bukan dipaksakan
    keep = [(t, s) for t, s in anchors if _score_at(curve, t) >= floor]
    out = [[t, s, f"jangkar {label} {hhmm(t)}"] for t, s in keep]
    if len(keep) >= 2:
        best = max(range(len(keep)), key=lambda i: keep[i][1])
        feasible = []  # (indeks jangkar, geser) yang tetap berjarak ≥ gap_h & tidak jatuh di jam sepi
        for i, (t, _) in enumerate(keep):
            if i == best:
                continue
            others = [o for j, (o, _) in enumerate(keep) if j != i]
            for shift in (1.0, -1.0, 0.5, -0.5):
                t2 = t + shift
                if 0 <= t2 < 24 and _score_at(curve, t2) >= floor and all(abs(t2 - o) >= gap_h for o in others):
                    feasible.append((i, t2))
        if feasible:  # bergiliran tiap hari → tiap jam tetangga ikut teruji
            i, t2 = feasible[dt.toordinal() % len(feasible)]
            out[i] = [t2, _score_at(curve, t2), f"eksplorasi {hhmm(t2)} (geser jangkar {hhmm(keep[i][0])})"]
    out.sort(key=lambda x: -x[1])
    return [(t, why) for t, _, why in out]
