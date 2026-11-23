"""(v7) Resep + Auto Pilot — setara "Resep" & "Auto Pilot" klipers.pro.

Resep  : kumpulan opsi form Analisis AI yang disimpan (maks 10) dan bisa dipakai ulang / oleh Auto Pilot.
Auto Pilot: ikuti channel YouTube; tiap N menit (default 15) video/siaran baru yang lolos filter
            (tipe video/live, durasi, kata wajib/dilarang di judul) langsung diproses dengan resep pilihan.
            Mode "review" = klip masuk hasil job untuk ditinjau; "auto" = langsung dijadwalkan upload ke jam FYP.
            Pertama kali channel ditambahkan, video yang sudah ada hanya ditandai (tidak memproses arsip lama),
            kecuali "proses video terbaru sekarang" dicentang.
"""
from __future__ import annotations

import re
import threading
import time
import uuid
from typing import Callable, Dict, List, Optional

from loguru import logger

import config
from . import db

RECIPES_KEY = "qp_recipes"
WATCH_KEY = "autopilot_watchers"
MAX_RECIPES = 10
MAX_WATCHERS = 20
SOURCE_KEYS = {"url", "path", "asset_id", "file", "upload"}
_LOCK = threading.RLock()
_STARTED = False


# ============================================================================ resep

def list_recipes() -> List[Dict]:
    return list(db.kv_get(RECIPES_KEY, []) or [])


def get_recipe(rid: str) -> Optional[Dict]:
    return next((r for r in list_recipes() if r.get("id") == rid), None)


def save_recipe(name: str, params: Dict, rid: str = "") -> Dict:
    name = " ".join(str(name or "").split())[:60]
    if not name:
        raise ValueError("Nama resep wajib diisi")
    clean = {k: v for k, v in (params or {}).items() if k not in SOURCE_KEYS and not str(k).startswith("_")}
    with _LOCK:
        items = list_recipes()
        hit = next((r for r in items if r["id"] == rid or r["name"].lower() == name.lower()), None)
        if hit:
            hit.update({"name": name, "params": clean, "updated": time.time()})
            rec = hit
        else:
            if len(items) >= MAX_RECIPES:
                raise ValueError(f"Maksimal {MAX_RECIPES} resep — hapus salah satu dulu")
            rec = {"id": uuid.uuid4().hex[:10], "name": name, "params": clean, "created": time.time(), "updated": time.time()}
            items.append(rec)
        db.kv_set(RECIPES_KEY, items)
    return rec


def delete_recipe(rid: str) -> bool:
    with _LOCK:
        items = list_recipes()
        keep = [r for r in items if r.get("id") != rid]
        db.kv_set(RECIPES_KEY, keep)
        for w in list_watchers():  # Auto Pilot yang memakai resep ini kembali ke opsi bawaan
            if w.get("recipe_id") == rid:
                update_watcher(w["id"], recipe_id="")
    return len(keep) != len(items)


# ============================================================================ watcher

def list_watchers() -> List[Dict]:
    return list(db.kv_get(WATCH_KEY, []) or [])


def _save_watchers(items: List[Dict]) -> None:
    db.kv_set(WATCH_KEY, items)


def normalize_channel(url: str) -> str:
    """https://www.youtube.com/@handle/videos → https://www.youtube.com/@handle (tab dibuang)."""
    u = str(url or "").strip()
    if u.startswith("@"):
        u = "https://www.youtube.com/" + u
    if not re.match(r"^https?://", u):
        raise ValueError("Masukkan link channel YouTube, mis. https://www.youtube.com/@namachannel")
    m = re.match(r"^(https?://(?:www\.|m\.)?youtube\.com/(?:@[\w.\-%]+|channel/[\w-]+|c/[\w.\-%]+|user/[\w.\-%]+))", u)
    if not m:
        raise ValueError("Link harus link channel YouTube (@handle, /channel/…, /c/…), bukan link video")
    return m.group(1).replace("://m.", "://www.")


def add_watcher(channel_url: str, *, recipe_id: str = "", kind: str = "both", min_dur: int = 600, max_dur: int = 10800,
                include: str = "", exclude: str = "", mode: str = "review", interval_min: int = 15,
                process_latest: bool = False, name: str = "") -> Dict:
    url = normalize_channel(channel_url)
    with _LOCK:
        items = list_watchers()
        if any(w["url"] == url for w in items):
            raise ValueError("Channel ini sudah diikuti")
        if len(items) >= MAX_WATCHERS:
            raise ValueError(f"Maksimal {MAX_WATCHERS} channel")
        w = {"id": uuid.uuid4().hex[:10], "url": url, "name": name or url.rsplit("/", 1)[-1], "recipe_id": recipe_id,
             "kind": kind if kind in ("video", "live", "both") else "both",
             "min_dur": int(min_dur or 0), "max_dur": int(max_dur or 0), "include": include.strip(), "exclude": exclude.strip(),
             "mode": "auto" if mode == "auto" else "review", "interval_min": max(5, min(int(interval_min or 15), 1440)),
             "enabled": True, "primed": False, "process_latest": bool(process_latest), "seen": [], "history": [],
             "created": time.time(), "last_check": 0.0, "last_error": ""}
        items.append(w)
        _save_watchers(items)
    return w


def update_watcher(wid: str, **fields) -> Optional[Dict]:
    allowed = {"recipe_id", "kind", "min_dur", "max_dur", "include", "exclude", "mode", "interval_min", "enabled", "name"}
    with _LOCK:
        items = list_watchers()
        w = next((x for x in items if x["id"] == wid), None)
        if not w:
            return None
        for k, v in fields.items():
            if k in allowed:
                w[k] = v
        _save_watchers(items)
        return w


def delete_watcher(wid: str) -> bool:
    with _LOCK:
        items = list_watchers()
        keep = [w for w in items if w["id"] != wid]
        _save_watchers(keep)
        return len(keep) != len(items)


# ============================================================================ cek channel

def channel_entries(url: str, kind: str = "both", limit: int = 8) -> List[Dict]:
    """Video & siaran terbaru channel (yt-dlp flat, tanpa unduh). Terbaru dulu."""
    import yt_dlp

    from .utils import _cookie_opts, js_runtime_opts

    tabs = {"video": ["videos"], "live": ["streams"], "both": ["videos", "streams"]}[kind if kind in ("video", "live") else "both"]
    out: List[Dict] = []
    opts = {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist", "playlistend": limit, "skip_download": True,
            **js_runtime_opts()}
    try:
        opts.update(_cookie_opts())
    except Exception:
        pass
    for tab in tabs:
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(f"{url}/{tab}", download=False) or {}
        except Exception as exc:  # channel tanpa tab live → abaikan
            if "does not have a" in str(exc) or "This channel does not have" in str(exc):
                continue
            raise
        for e in (info.get("entries") or [])[:limit]:
            vid = e.get("id")
            if not vid:
                continue
            out.append({"id": vid, "url": e.get("url") if str(e.get("url", "")).startswith("http") else f"https://www.youtube.com/watch?v={vid}",
                        "title": e.get("title") or "", "duration": e.get("duration"), "live_status": e.get("live_status") or "",
                        "tab": tab, "channel": info.get("channel") or info.get("uploader") or ""})
    return out


def _words(s: str) -> List[str]:
    return [w.strip().lower() for w in re.split(r"[,;\n]+", s or "") if w.strip()]


def verdict(w: Dict, e: Dict) -> str:
    """'' = lolos, selain itu alasan ditolak. 'wait' = belum bisa diproses (siaran terjadwal)."""
    ls = e.get("live_status") or ""
    if ls in ("is_upcoming", "upcoming"):
        return "wait"
    is_live = ls in ("is_live", "post_live") or e.get("tab") == "streams"
    if w.get("kind") == "video" and is_live:
        return "bukan video biasa"
    if w.get("kind") == "live" and not is_live:
        return "bukan siaran"
    dur = e.get("duration")
    if dur and ls != "is_live":
        if w.get("min_dur") and dur < int(w["min_dur"]):
            return f"terlalu pendek ({int(dur) // 60} mnt)"
        if w.get("max_dur") and dur > int(w["max_dur"]):
            return f"terlalu panjang ({int(dur) // 60} mnt)"
    title = (e.get("title") or "").lower()
    inc = _words(w.get("include", ""))
    if inc and not any(k in title for k in inc):
        return "judul tanpa kata wajib"
    exc = _words(w.get("exclude", ""))
    if any(k in title for k in exc):
        return "judul memuat kata dilarang"
    return ""


def job_params(w: Dict, entry: Dict) -> Dict:
    rec = get_recipe(w.get("recipe_id") or "") if w.get("recipe_id") else None
    params = dict((rec or {}).get("params") or {})
    params["url"] = entry["url"]
    params["auto_publish"] = w.get("mode") == "auto"  # review = klip ditinjau dulu, tidak diupload
    params["_autopilot"] = {"watcher": w["id"], "video": entry["id"]}
    return params


def check(wid: str, submit: Optional[Callable] = None, fetch: Optional[Callable] = None, max_new: int = 2) -> Dict:
    """Cek satu channel sekarang. Return ringkasan {new, submitted, skipped, error}."""
    if submit is None:
        from .jobs import submit as _submit
        submit = _submit
    fetch = fetch or channel_entries
    with _LOCK:
        w = next((x for x in list_watchers() if x["id"] == wid), None)
    if not w:
        return {"error": "watcher tidak ada"}
    summary = {"new": 0, "submitted": [], "skipped": [], "error": ""}
    try:
        entries = fetch(w["url"], w.get("kind", "both"))
    except Exception as exc:  # noqa: BLE001
        summary["error"] = str(exc)[:300]
        _patch(wid, last_check=time.time(), last_error=summary["error"])
        logger.warning(f"Auto Pilot {w['name']}: cek channel gagal: {summary['error']}")
        return summary
    seen = set(w.get("seen") or [])
    fresh = [e for e in entries if e["id"] not in seen]
    history = list(w.get("history") or [])
    if not w.get("primed"):  # pertama kali: arsip lama hanya ditandai
        newest = next((e for e in fresh if not verdict(w, e)), None) if w.get("process_latest") else None
        if newest:  # video terbaru yang LOLOS filter
            fresh = [newest]
            seen |= {e["id"] for e in entries if e["id"] != newest["id"]}
        else:
            seen |= {e["id"] for e in entries}
            fresh = []
    summary["new"] = len(fresh)
    n_sub = 0
    for e in reversed(fresh):  # lama → baru
        why = verdict(w, e)
        if why == "wait":
            continue
        if why:
            seen.add(e["id"])
            summary["skipped"].append({"id": e["id"], "title": e["title"], "reason": why})
            history.append({"at": time.time(), "video": e["id"], "title": e["title"], "status": "dilewati", "reason": why})
            continue
        if n_sub >= max_new:  # sisanya di cek berikutnya
            continue
        title = f"Auto Pilot · {(e.get('channel') or w['name'])[:30]} · {e['title'][:60]}"
        job = submit("clip_quick", job_params(w, e), title=title)
        n_sub += 1
        seen.add(e["id"])
        summary["submitted"].append({"id": e["id"], "title": e["title"], "job": job.get("id")})
        history.append({"at": time.time(), "video": e["id"], "title": e["title"], "status": "diproses", "job": job.get("id")})
        logger.info(f"Auto Pilot {w['name']}: video baru diproses → job {job.get('id')}: {e['title'][:80]}")
    _patch(wid, seen=list(seen)[-400:], history=history[-50:], primed=True, last_check=time.time(), last_error="",
           channel_title=(entries[0].get("channel") if entries else w.get("channel_title", "")))
    return summary


def _patch(wid: str, **fields) -> None:
    with _LOCK:
        items = list_watchers()
        for x in items:
            if x["id"] == wid:
                x.update(fields)
        _save_watchers(items)


def tick(now: Optional[float] = None) -> int:
    """Cek semua channel yang sudah jatuh tempo. Return jumlah job yang dibuat."""
    now = now or time.time()
    n = 0
    for w in list_watchers():
        if not w.get("enabled", True):
            continue
        if now - float(w.get("last_check") or 0) < int(w.get("interval_min") or 15) * 60:
            continue
        n += len(check(w["id"]).get("submitted") or [])
    return n


def _loop() -> None:
    time.sleep(20)  # biarkan server siap dulu
    while True:
        try:
            tick()
        except Exception:
            logger.exception("Auto Pilot tick gagal")
        time.sleep(60)


def start() -> None:
    global _STARTED
    with _LOCK:
        if _STARTED or not getattr(config, "AUTOPILOT_ENABLED", True):
            return
        _STARTED = True
    threading.Thread(target=_loop, name="autopilot", daemon=True).start()
