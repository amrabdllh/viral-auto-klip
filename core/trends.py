"""Riset tren dengan data TERBARU (v6.8) — dipakai Analitik Viral, Saran Viral AI, dan Optimasi Judul.

Masalah lama: pencarian YouTube diurutkan menurut relevansi tanpa filter waktu (video lama populer ikut tampil),
cache hanya diperbarui saat halaman dibuka, dan AI menulis ide dari pengetahuannya sendiri yang tertinggal
berbulan-bulan — topik yang baru meledak (mis. rilis game besar) tidak terdeteksi.

Sumber (semua gratis, tanpa API key):
- YouTube search dengan filter "hari ini" / "minggu ini" diurutkan jumlah views (parameter `sp` resmi YouTube).
- Google Trends Indonesia (RSS pencarian real-time).
- Topik yang berulang di judul video segar dari banyak channel berbeda (n-gram + alias, mis. "GTA VI" = "GTA 6").
Pembaruan otomatis: thread latar menyegarkan kategori yang dipantau tiap `TRENDS_REFRESH_MIN` menit.
"""
import html
import math
import re
import threading
import time
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional
from urllib.parse import quote_plus

from loguru import logger

import config
from . import db

# sp = urutkan views (CAM) + filter tanggal unggah (SAgg: B=1 jam, C=hari ini, D=minggu ini, E=bulan ini)
_SP = {"hour": "CAMSAggB", "today": "CAMSAggC", "week": "CAMSAggD", "month": "CAMSAggE"}
_AGE = {"hour": 0.05, "today": 0.5, "week": 3.5, "month": 15.0}  # perkiraan umur (hari) — mode cepat tanpa tanggal

_STOP = set("""yang dan di ke dari untuk dengan ini itu aku kamu kita kami saya dia mereka ada jadi apa bikin bisa sama
juga lagi udah sudah belum akan mau harus banget aja saja nya pun kok sih deh dong ya yah gak nggak enggak tidak bukan
cara kenapa gimana siapa kapan semua satu dua tiga hari jam menit tahun baru terbaru full part eps episode ep season
official video videos music musik lagu live streaming stream shorts short tiktok youtube fyp viral trending terviral
indonesia indo gameplay game games gaming play playing main mainin review reaction react vs versi new day night first
the a an of in on at to and or with my me i you your we our they this that is are was be for from by it its not no yes
how what why who when best top most ever all one two three vlog podcast ft feat x official mv lyric lirik cover
naik turun calon cuma unik tentang menarik negara jarang diketahui keluarga kompilasi asli beneran agar supaya pintar enak didengar galau united pop hits hit album santai populer anak anak2 kids kid film movie movies series serial drama komedi comedy lucu
bahasa teknologi tech fakta facts dalam jika kalau dunia world cup investasi dana uang tips trik tutorial belajar edukasi
sepak bola football soccer highlights highlight goal gol musik lagu song songs playlist cerita kisah story horor horror
masa depan terbaik terbaru2 resep cara mudah cepat gratis ytshorts ytshort shortsyoutube youtubeshort mabar pushrank finally making made gamer gamers pro noob challenge part1 part2 indonesian indonesians gas out fun mod mobile pc ps5 android hp seru lucu kocak gila parah keren mantap asik wow omg
youtubeshorts viralvideo viralshorts beritaterkini beritaviral shortsvideo shortvideo shortsfeed foryou foryoupage
fypシ fypage trend trends berita news update info terkini hot breaking bangga momen detik detik
""".split())
_ALIASES = [
    (re.compile(r"\bgrand theft auto\s*(vi|6)\b"), "gta 6"),
    (re.compile(r"\bgta\s*(vi|6)\b"), "gta 6"),
    (re.compile(r"\bgta(6|vi)\b"), "gta 6"),
    (re.compile(r"\bu\s+(1[5-9]|2[0-3])\b"), r"u\1"),  # "U 17" = "U17"
    (re.compile(r"\basian\b"), "asia"),
]
# kata sehari-hari yang sering muncul di Google Trends/judul tapi bukan topik spesifik
_COMMON = set("""sekarang hari besok kemarin malam pagi siang sore minggu bulan tahun orang rumah anak ibu bapak
manusia hidup mati cinta uang kerja kerja sekolah kampus jalan mobil motor kota desa negara dunia berita kabar
harga gaji promo diskon gratis cuaca hujan banjir gempa jadwal hasil skor live link nonton download aplikasi""".split())
_LOCK = threading.Lock()
_STARTED = False


# ----------------------------------------------------------------------------- YouTube segar

def fresh_search(query: str, period: str = "week", limit: int = 20) -> List[Dict]:
    """Video YouTube terbaru (hari ini / minggu ini / bulan ini) diurutkan views. Tanpa API key."""
    import yt_dlp
    url = f"https://www.youtube.com/results?search_query={quote_plus(query)}&sp={_SP.get(period, _SP['week'])}"
    opts = {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist", "skip_download": True,
            "playlistend": int(limit)}
    with yt_dlp.YoutubeDL(opts) as ydl:
        data = ydl.extract_info(url, download=False)
    out = []
    for e in data.get("entries") or []:
        if not e or not e.get("id"):
            continue
        out.append({"id": e["id"], "title": e.get("title") or "", "channel": e.get("channel") or e.get("uploader") or "",
                    "views": int(e.get("view_count") or 0), "duration": e.get("duration") or 0,
                    "url": f"https://www.youtube.com/watch?v={e['id']}",
                    "thumbnail": (e.get("thumbnails") or [{}])[-1].get("url", "") if e.get("thumbnails") else "",
                    "upload_date": e.get("upload_date"), "age_days": _AGE.get(period, 7.0), "period": period,
                    "live": not e.get("view_count") and not e.get("duration")})
    return out[:limit]


def fresh_videos(query: str, limit: int = 30) -> List[Dict]:
    """Gabungan 'hari ini' + 'minggu ini' (dedupe; versi 'hari ini' menang karena lebih segar)."""
    seen, out = set(), []
    for period, n in (("today", 15), ("week", limit)):
        try:
            for v in fresh_search(query, period, n):
                if v["id"] not in seen:
                    seen.add(v["id"])
                    out.append(v)
        except Exception as exc:
            logger.warning(f"Pencarian YouTube segar '{query}' ({period}) gagal: {str(exc)[:120]}")
    return out


# ----------------------------------------------------------------------------- Google Trends

def google_trends(geo: str = "ID", limit: int = 20, max_age_min: int = 30) -> List[Dict]:
    """Pencarian yang sedang melonjak di Google (real-time, RSS publik). Cache 30 menit."""
    key = f"gtrends:{geo}"
    cached = db.kv_get(key)
    if cached and time.time() - cached.get("_ts", 0) < max_age_min * 60:
        return cached["items"][:limit]
    import requests
    resp = requests.get(f"https://trends.google.com/trending/rss?geo={geo}", timeout=20,
                        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    resp.raise_for_status()
    items = []
    for block in re.findall(r"<item>(.*?)</item>", resp.text, re.S):
        def tag(name):
            m = re.search(rf"<{name}>(.*?)</{name}>", block, re.S)
            return html.unescape(m.group(1).strip()) if m else ""
        traffic = tag("ht:approx_traffic")
        num = int(re.sub(r"\D", "", traffic) or 0)
        items.append({"topic": tag("title"), "traffic": traffic, "traffic_num": num, "published": tag("pubDate"),
                      "news": [html.unescape(t) for t in re.findall(r"<ht:news_item_title>(.*?)</ht:news_item_title>", block, re.S)][:2]})
    items.sort(key=lambda x: -x["traffic_num"])
    db.kv_set(key, {"items": items, "_ts": time.time()})
    return items[:limit]


# ----------------------------------------------------------------------------- topik dari judul

def _norm(title: str) -> str:
    t = str(title or "").lower()
    t = re.sub(r"[^\w\s]", " ", t, flags=re.UNICODE)
    t = re.sub(r"_", " ", t)
    for rx, rep in _ALIASES:
        t = rx.sub(rep, t)
    return re.sub(r"\s+", " ", t).strip()


def topics_from_titles(videos: List[Dict], limit: int = 12, min_channels: int = 2, exclude: str = "") -> List[Dict]:
    """Topik/entitas yang muncul berulang di judul video segar dari BANYAK channel (bukan satu channel spam).
    `exclude`: kata kunci pencarian kategori (mis. "komedi lucu indonesia") — tidak dihitung sebagai topik."""
    stop = _STOP | set(_norm(exclude).split())
    chans: Dict[str, set] = defaultdict(set)
    views: Dict[str, int] = defaultdict(int)
    vids: Dict[str, List[str]] = defaultdict(list)
    for v in videos:
        toks = [t for t in _norm(v.get("title", "")).split() if t]
        grams = set()
        for n in (1, 2, 3):
            for i in range(len(toks) - n + 1):
                g = toks[i:i + n]
                if all(w in stop or w.isdigit() for w in g) or g[0] in stop or g[-1] in stop:
                    continue
                if n == 1 and (len(g[0]) < 3 or g[0].isdigit()):
                    continue
                grams.add(" ".join(g))
        for g in grams:
            chans[g].add(v.get("channel") or v.get("id"))
            views[g] += int(v.get("views") or 0)
            if len(vids[g]) < 3:
                vids[g].append(v.get("title", ""))
    cands = [(g, len(c)) for g, c in chans.items() if len(c) >= min_channels]
    # skor: banyak channel berbeda + total views (log); n-gram lebih panjang sedikit diutamakan (lebih spesifik)
    scored = sorted(cands, key=lambda gc: -(gc[1] * 2 + math.log10(1 + views[gc[0]]) + 0.4 * (gc[0].count(" "))))
    picked: List[str] = []
    for g, _ in scored:
        # frasa yang lebih spesifik menang bila didukung hampir sama banyak channel ("GTA" → "GTA 6")
        longer = [h for h, c in cands if h != g and f" {g} " in f" {h} " and c >= 0.7 * len(chans[g])]
        if longer:
            g = max(longer, key=lambda h: (len(chans[h]), h.count(" ")))
        # buang sub/super n-gram dari topik yang sudah dipilih dengan dukungan channel serupa
        if any(f" {p} " in f" {g} " and len(chans[g]) < len(chans[p]) for p in picked):
            continue  # turunan topik yang sudah ada ("Tamatin GTA 6" di bawah "GTA 6")
        if g in picked or any((f" {g} " in f" {p} " or f" {p} " in f" {g} ") and abs(len(chans[g]) - len(chans[p])) <= 1 for p in picked):
            continue
        picked.append(g)
        if len(picked) >= limit:
            break
    return [{"topic": g.upper() if len(g) <= 5 else g.title(), "channels": len(chans[g]), "views": views[g],
             "examples": vids[g]} for g in picked]


def cached_topics(max_age_h: float = 6.0) -> List[str]:
    """Topik naik dari cache Analitik (semua kategori, diperbarui otomatis) + Google Trends — tanpa jaringan."""
    out: List[str] = []
    for key in ("Semua", "Gaming", "Musik", "Komedi", "Hiburan", "Tutorial", "Olahraga", "Teknologi", "Keuangan", "Edukasi"):
        d = db.kv_get(f"analytics:{key}") or {}
        if time.time() - d.get("_ts", 0) <= max_age_h * 3600:
            out += [t["topic"] for t in d.get("trending_topics") or []]
            out += [g["topic"] for g in (d.get("google_trends") or [])[:12]]
    gt = db.kv_get("gtrends:ID") or {}
    if time.time() - gt.get("_ts", 0) <= max_age_h * 3600:
        out += [g["topic"] for g in gt.get("items") or []][:20]
    seen, uniq = set(), []
    for t in out:
        k = _norm(t)
        if " " not in k and (len(k) < 4 or k in _COMMON):
            continue  # satu kata umum ("sekarang", "orang") bukan topik
        if len(k) >= 3 and k not in seen and k not in _STOP:
            seen.add(k)
            uniq.append(t)
    return uniq


def boost_trending(clips: List[Dict], bonus: float = 6.0) -> List[str]:
    """Naikkan skor klip yang menyebut topik yang sedang naik (urutan klip disusun ulang). Return topik yang cocok."""
    topics = cached_topics()
    if not topics or not clips:
        return []
    hits: List[str] = []
    for c in clips:
        text = f" {_norm(c.get('transcript') or c.get('text') or '')} "
        found = [t for t in topics if f" {_norm(t)} " in text]
        if found:
            c["score"] = round(min(100.0, float(c.get("score") or 0) + bonus), 1)
            c["trending_topics"] = found[:3]
            c.setdefault("fyp_signals", []).append("trending_topic")
            c["reason"] = f"Menyebut topik yang lagi naik: {found[0]} · " + str(c.get("reason") or "")
            hits += found[:1]
    clips.sort(key=lambda c: -float(c.get("score") or 0))
    return hits


# ----------------------------------------------------------------------------- konteks untuk AI

def ai_context(query: str, max_titles: int = 15) -> Dict:
    """Data segar ringkas untuk prompt AI (Saran Viral / Optimasi Judul)."""
    vids = fresh_videos(query, 25)
    vids.sort(key=lambda v: -(v["views"] / max(v["age_days"], 0.3)))
    topics = topics_from_titles(vids, limit=8, exclude=query)
    try:
        gt = google_trends(limit=10)
    except Exception as exc:
        logger.warning(f"Google Trends gagal: {str(exc)[:120]}")
        gt = []
    today = datetime.now().strftime("%d %B %Y")
    lines = [f"Tanggal hari ini: {today}. Data di bawah DIAMBIL HARI INI dan lebih baru dari pengetahuanmu — "
             "bila ada rilis/peristiwa baru yang tidak kamu kenal, anggap itu nyata dan manfaatkan."]
    if topics:
        lines.append("Topik yang sedang naik di video minggu ini (jumlah channel berbeda): "
                     + ", ".join(f"{t['topic']} ({t['channels']})" for t in topics))
    if vids:
        lines.append("Judul video paling cepat naik (hari ini/minggu ini):\n- " +
                     "\n- ".join(f"{v['title']} — {v['views']:,} views".replace(",", ".") for v in vids[:max_titles]))
    if gt:
        lines.append("Pencarian Google yang melonjak di Indonesia saat ini: " + ", ".join(f"{g['topic']} ({g['traffic']})" for g in gt))
    return {"text": "\n".join(lines), "videos": vids[:max_titles], "topics": topics, "google_trends": gt, "date": today}


# ----------------------------------------------------------------------------- auto-update

def watch_category(category: str) -> None:
    """Catat kategori yang dibuka pengguna → ikut disegarkan otomatis selama 24 jam."""
    watched = db.kv_get("trends:watched", {}) or {}
    watched[category] = time.time()
    db.kv_set("trends:watched", watched)


def _loop() -> None:
    from .tools import _CAT_QUERY, viral_analytics
    time.sleep(20)  # biarkan server selesai menyala dulu
    while True:
        try:
            # SEMUA kategori diperbarui (dulu hanya "Semua" + kategori yang pernah dibuka 24 jam terakhir).
            # Kategori yang baru dibuka didahulukan; satu kategori gagal tidak menghentikan yang lain.
            watched = db.kv_get("trends:watched", {}) or {}
            cats = sorted(_CAT_QUERY, key=lambda c: (c != "Semua", -watched.get(c, 0)))
            t0, done = time.time(), []
            for cat in cats:
                try:
                    viral_analytics({"category": cat, "refresh": True, "_auto": True})
                    done.append(cat)
                except Exception as exc:
                    logger.warning(f"Tren {cat} gagal diperbarui: {str(exc)[:120]}")
            logger.info(f"Tren diperbarui otomatis ({len(done)}/{len(cats)} kategori, {time.time() - t0:.0f} dtk): {', '.join(done)}")
        except Exception as exc:
            logger.warning(f"Pembaruan tren otomatis gagal: {str(exc)[:160]}")
        time.sleep(max(15, int(getattr(config, "TRENDS_REFRESH_MIN", 60))) * 60)


def start() -> None:
    global _STARTED
    with _LOCK:
        if _STARTED or not getattr(config, "TRENDS_AUTO_REFRESH", True):
            return
        _STARTED = True
        threading.Thread(target=_loop, name="trends-refresh", daemon=True).start()
