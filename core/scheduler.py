"""
Penjadwal Auto Upload: cek antrean jadwal tiap SCHEDULER_INTERVAL detik dan upload yang sudah jatuh tempo
ke YouTube Shorts / TikTok lewat core/publisher.py. Status: pending → uploading → done | failed.
Sejak v6.9 jadwal ≥25 menit lagi diunggah LEBIH AWAL dengan jadwal bawaan YouTube/TikTok: pending → scheduled
(dipegang platform) → done saat waktunya lewat. Gagal pra-unggah tetap pending dan dicoba lagi.
"""
from __future__ import annotations

import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger

import config
from . import db

PLATFORMS = ["youtube", "tiktok"]
_STARTED = False
# Hashtag yang hanya relevan di YouTube — jangan ikut ke caption TikTok.
_YT_ONLY_TAGS = re.compile(r"#(?:shorts|youtubeshorts|youtubeshort|shortvideo|shortsfeed|youtube)\b",
                           re.IGNORECASE)


def _caption_for(platform: str, title: str, description: str) -> str:
    """Caption upload per platform. TikTok tidak membawa hashtag khusus YouTube."""
    text = f"{title} {description}".strip()
    if platform != "youtube":
        text = _YT_ONLY_TAGS.sub("", text)
        text = re.sub(r"[ \t]{2,}", " ", text).strip()
    return text
_LOCK = threading.Lock()


def platform_status() -> Dict:
    from . import tiktok_browser, youtube_browser

    yt_secret = config.YOUTUBE_CLIENT_SECRET_FILE or ""
    token = config.BASE_DIR / config.YOUTUBE_OAUTH_TOKEN_FILE
    try:
        import googleapiclient  # noqa: F401
        yt_lib = True
    except ImportError:
        yt_lib = False
    cookies = config.TIKTOK_COOKIES_FILE or ""
    yt_cookie_ok = bool(youtube_browser.cookie_status().get("configured"))
    tt_cookie_ok = bool(tiktok_browser.cookie_status().get("configured"))
    health = {pf: db.kv_get(f"session_health_{pf}", None) for pf in PLATFORMS}
    return {
        "youtube": {"label": "YouTube Shorts", "session": health.get("youtube"),
                    "limit_until": preupload_block("youtube") or None,
                    "ready": bool(yt_cookie_ok or (yt_secret and Path(yt_secret).exists() and yt_lib)),
                    "browser": yt_cookie_ok, "logged_in": token.exists(), "library": yt_lib,
                    "hint": "Tempel cookies di Pengaturan → YouTube (browser headless), atau isi YOUTUBE_CLIENT_SECRET_FILE"},
        "tiktok": {"label": "TikTok", "session": health.get("tiktok"), "ready": bool(tt_cookie_ok or (cookies and Path(cookies).exists())),
                   "browser": tt_cookie_ok, "library": True,
                   "hint": "Tempel cookies di Pengaturan → TikTok (browser headless)"},
    }


def upload_now(item: Dict, publish_at: Optional[float] = None) -> Dict:
    """Upload satu jadwal. YouTube memakai browser headless kalau cookies ada
    (bebas kuota API); kalau tidak, jatuh ke API. TikTok selalu browser.
    publish_at: unggah SEKARANG tetapi pakai jadwal bawaan platform (lihat preupload_due)."""
    from . import tiktok_browser, youtube_browser
    from .publisher import upload_to_tiktok, upload_to_youtube

    report: Dict = {}
    path = item["file_path"]
    if not Path(path).exists():
        return {"error": f"File tidak ada: {path}"}
    tags = re.findall(r"#(\w+)", item.get("description", ""))
    from .thumb_ai import upload_image_for
    thumb = upload_image_for(path)  # thumbnail AI → thumbnail frame → (tidak ada)
    title = (item.get("title") or Path(path).stem)[:100]
    description = item.get("description", "")

    # Coba ulang setelah gagal sebagian (mis. TikTok sukses, YouTube kedaluwarsa):
    # jangan unggah ulang platform yang sudah berhasil.
    done_before = item.get("result") or {}
    for platform in item.get("platforms") or []:
        prev = done_before.get(platform) or {}
        if str(prev.get("status", "")).lower() == "done" or prev.get("ok") is True:
            report[platform] = prev
            continue
        try:
            if platform == "youtube":
                method = str(getattr(config, "YOUTUBE_UPLOAD_METHOD", "auto") or "auto").lower()
                browser_ready = bool(youtube_browser.cookie_status().get("configured"))
                secret = str(config.YOUTUBE_CLIENT_SECRET_FILE or "")
                secret_path = Path(secret) if secret else None
                if secret_path is not None and not secret_path.is_absolute():
                    secret_path = config.BASE_DIR / secret_path
                token_ok = (config.BASE_DIR / config.YOUTUBE_OAUTH_TOKEN_FILE).exists()
                # Mode otomatis: API hanya bila sudah login (token ada) — kalau belum, upload di latar
                # akan membuka jendela izin Google tanpa ada yang menunggu.
                api_ready = bool(secret_path and secret_path.exists() and (token_ok or not browser_ready))
                use_browser = method == "browser" or (method == "auto" and not api_ready and browser_ready)
                if use_browser:
                    res = youtube_browser.upload_video(path, title=title, description=description,
                                                       tags=tags, privacy=str(config.YOUTUBE_PRIVACY or "private"),
                                                       publish_at=publish_at, thumbnail=thumb)
                    report["youtube"] = {"status": "done", "method": "browser", **res}
                else:
                    try:
                        report["youtube"] = upload_to_youtube(path, title=title, description=description, tags=tags,
                                                              publish_at=publish_at, thumbnail=thumb)
                    except Exception as api_exc:  # noqa: BLE001
                        # kuota API habis → pakai jalur browser (cookie) bila tersedia, selain itu laporkan
                        if "Kuota YouTube API" in str(api_exc) and browser_ready:
                            try:
                                res = youtube_browser.upload_video(path, title=title, description=description, tags=tags,
                                                                   privacy=str(config.YOUTUBE_PRIVACY or "private"),
                                                                   publish_at=publish_at, thumbnail=thumb)
                            except Exception:  # noqa: BLE001 - cookie juga tidak bisa → yang dilaporkan tetap kuota
                                raise api_exc
                            report["youtube"] = {"status": "done", "method": "browser", **res}
                        else:
                            raise
            elif platform == "tiktok":
                caption = _caption_for("tiktok", title, description)
                if tiktok_browser.cookie_status().get("configured"):
                    report["tiktok"] = tiktok_browser.upload_video(str(path), description=caption, publish_at=publish_at,
                                                                   cover=thumb)
                else:
                    report["tiktok"] = upload_to_tiktok(path, description=caption)
        except Exception as exc:
            report[platform] = {"status": "failed", "reason": str(exc)[:400]}
    return report


def _ok(report: Dict) -> bool:
    if "error" in report or not report:
        return False
    return all(str((v or {}).get("status", "")).lower() not in ("failed", "error", "skipped") for v in report.values())


PREUPLOAD_LEAD_MIN = 25  # jadwal yang tinggal < 25 menit diunggah biasa saat jamnya (TikTok butuh ≥15 mnt)
PREUPLOAD_PAUSE_S = 90   # jeda antar pra-unggah (tidak membanjiri YouTube/TikTok)
PREUPLOAD_RETRY_S = 30 * 60
_last_preupload = 0.0


LIMIT_BACKOFF_H = 6.0


def preupload_block(platform: str) -> float:
    """Waktu (epoch) sampai pra-unggah platform ini ditunda (mis. batas upload harian YouTube), 0 bila tidak."""
    until = float(db.kv_get(f"preupload_block_{platform}", 0) or 0)
    return until if until > time.time() else 0.0


def _preupload_ok(platform: str) -> bool:
    """Hanya jalur yang BISA memasang jadwal bawaan; selain itu unggah biasa saat jamnya (jangan sampai tayang lebih awal)."""
    from . import tiktok_browser
    if preupload_block(platform):
        return False
    if platform == "tiktok":
        # draf tidak bisa dijadwalkan di TikTok → tetap diunggah sebagai draf saat jamnya
        return (str(getattr(config, "TIKTOK_UPLOAD_MODE", "draft")).lower() == "post"
                and bool(tiktok_browser.cookie_status().get("configured")))
    return platform == "youtube"  # browser (Visibilitas → Jadwalkan) & API (publishAt) sama-sama bisa


def preupload_due(limit: int = 1) -> int:
    """JADWAL BAWAAN PLATFORM: jadwal yang masih ≥25 menit lagi (s/d PREUPLOAD_DAYS hari) diunggah SEKARANG dengan
    waktu publikasi diatur di YouTube Studio / TikTok Studio. Setelah itu YouTube/TikTok sendiri yang mempublikasikan —
    sesi cookie mati atau laptop mati tidak lagi menggagalkan jadwal. Satu per putaran, berjeda, terdekat dulu."""
    global _last_preupload
    if not getattr(config, "UPLOAD_NATIVE_SCHEDULE", True) or time.time() - _last_preupload < PREUPLOAD_PAUSE_S:
        return 0
    now = time.time()
    horizon = now + float(getattr(config, "PREUPLOAD_DAYS", 10)) * 86400
    done = 0
    for item in sorted(db.list_schedules(), key=lambda s: float(s["run_at"])):
        if done >= limit:
            break
        run_at = float(item["run_at"])
        if item.get("status") != "pending" or not (now + PREUPLOAD_LEAD_MIN * 60 <= run_at <= horizon):
            continue
        if not Path(item["file_path"]).exists():
            # file klip sudah dihapus: tidak akan pernah bisa diunggah → gagal sekarang, bukan dicoba ulang tiap 30 mnt
            db.update_schedule(item["id"], status="failed",
                               result={**(item.get("result") or {}), "error": f"File tidak ada: {item['file_path']}"})
            logger.warning(f"Jadwal {item['id']} dibatalkan: file klip sudah tidak ada ({Path(item['file_path']).name})")
            continue
        pfs = item.get("platforms") or []
        if not pfs or not all(_preupload_ok(p) for p in pfs):
            continue
        last_err = (item.get("result") or {}).get("_preupload_error_at", 0)
        if now - float(last_err or 0) < PREUPLOAD_RETRY_S:
            continue
        db.update_schedule(item["id"], status="uploading")
        logger.info(f"Pra-unggah (jadwal bawaan platform {datetime.fromtimestamp(run_at):%a %d %H:%M}): "
                    f"{Path(item['file_path']).name} → {pfs}")
        report = upload_now(item, publish_at=run_at)
        _last_preupload = time.time()
        done += 1
        if _ok(report):
            db.update_schedule(item["id"], status="scheduled", result=report)
        elif any(re.search(r"batas upload harian|kuota youtube api", str((v or {}).get("reason", "")), re.I)
                 for v in report.values()):
            # bukan salah item ini: tunda SEMUA pra-unggah platform itu, item tetap pending tanpa tanda gagal
            for pf in pfs:
                db.kv_set(f"preupload_block_{pf}", time.time() + LIMIT_BACKOFF_H * 3600)
            db.update_schedule(item["id"], status="pending")
            logger.warning(f"Batas upload harian {pfs} tercapai — pra-unggah ditunda {LIMIT_BACKOFF_H:.0f} jam")
        else:
            # gagal pra-unggah ≠ gagal jadwal: tetap 'pending' (dicoba lagi 30 mnt lagi, atau diunggah biasa saat jamnya)
            db.update_schedule(item["id"], status="pending", result={**report, "_preupload_error_at": time.time()})
            logger.warning(f"Pra-unggah gagal, dicoba lagi nanti: {str(report)[:300]}")
    return done


def tick() -> int:
    # jadwal yang sudah dipegang YouTube/TikTok: tandai selesai begitu waktunya lewat
    for item in db.list_schedules():
        if item.get("status") == "scheduled" and float(item["run_at"]) <= time.time():
            db.update_schedule(item["id"], status="done")
    due = db.due_schedules(time.time())
    for item in due:
        db.update_schedule(item["id"], status="uploading")
        logger.info(f"Auto Upload: {Path(item['file_path']).name} → {item['platforms']}")
        report = upload_now(item)
        db.update_schedule(item["id"], status="done" if _ok(report) else "failed", result=report)
    if not due:
        preupload_due()
    return len(due)


SESSION_CHECK_H = 3.0


def check_sessions(force: bool = False, only: Optional[str] = None) -> Dict:
    """Tes sesi cookie YouTube/TikTok (headless, tanpa upload) — sekalian MENYEGARKAN cookie yang dirotasi situs
    (youtube_browser/tiktok_browser.persist_refreshed). Hasil disimpan agar UI bisa memperingatkan SEBELUM jadwal gagal."""
    from . import tiktok_browser, youtube_browser
    out = {}
    for pf, mod in (("youtube", youtube_browser), ("tiktok", tiktok_browser)):
        if (only and pf != only) or not mod.cookie_status().get("configured"):
            continue
        prev = db.kv_get(f"session_health_{pf}", None) or {}
        if not force and time.time() - float(prev.get("at", 0)) < SESSION_CHECK_H * 3600:
            out[pf] = prev
            continue
        try:
            res = mod.test_session()
        except Exception as exc:  # noqa: BLE001
            res = {"ok": False, "error": str(exc)[:200]}
        rec = {"ok": bool(res.get("ok")), "error": str(res.get("error") or "")[:200], "at": time.time(),
               "who": str(res.get("channel") or res.get("username") or "")}
        db.kv_set(f"session_health_{pf}", rec)
        if not rec["ok"]:
            logger.warning(f"Sesi {pf} tidak valid: {rec['error']} — perbarui cookies di Pengaturan sebelum jadwal berikutnya")
        out[pf] = rec
    return out


def _next_upload_soon(within_s: float = 45 * 60) -> bool:
    now = time.time()
    return any(s.get("status") == "pending" and now < float(s["run_at"]) <= now + within_s for s in db.list_schedules())


def _loop() -> None:
    last_measure = 0.0
    last_session = 0.0
    last_thumb = time.time()
    while True:
        try:
            tick()
        except Exception:
            logger.exception("Scheduler tick gagal")
        if time.time() - last_measure > 30 * 60:  # v6.8: ukur views 24 jam → jam upload belajar dari hasil
            last_measure = time.time()
            try:
                from .slot_learning import measure_due
                measure_due()
            except Exception as exc:
                logger.warning(f"Pengukuran jam upload gagal: {str(exc)[:160]}")
        if time.time() - last_thumb > 3600:  # thumbnail YouTube yang tertunda (batas ganti thumbnail sementara)
            last_thumb = time.time()
            try:
                from .publisher import retry_thumbnails
                retry_thumbnails()
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Coba ulang thumbnail gagal: {str(exc)[:160]}")
        # sesi upload: tiap 3 jam, dan sekali lagi ±45 mnt sebelum upload berikutnya (bila cek terakhir > 1 jam lalu)
        if time.time() - last_session > SESSION_CHECK_H * 3600 or (
                time.time() - last_session > 3600 and _next_upload_soon()):
            last_session = time.time()
            try:
                check_sessions(force=True)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Cek sesi upload gagal: {str(exc)[:160]}")
        time.sleep(max(10, int(getattr(config, "SCHEDULER_INTERVAL", 30))))


def start() -> None:
    global _STARTED
    with _LOCK:
        if _STARTED:
            return
        _STARTED = True
        # item yang macet di 'uploading' saat server mati dikembalikan ke antrean
        for item in db.list_schedules():
            if item["status"] == "uploading":
                db.update_schedule(item["id"], status="pending")
        threading.Thread(target=_loop, name="upload-scheduler", daemon=True).start()


def smart_cadence(files: List[str], start_ts: float, per_day: int = 3, hours: List[int] = None) -> List[float]:
    """Sebar upload: per_day slot per hari di jam terbaik (default 12, 18, 20 WIB/lokal)."""
    from datetime import datetime, timedelta
    hours = hours or [12, 18, 20, 7, 21][:max(1, per_day)]
    hours = sorted(hours[:per_day])
    slots: List[float] = []
    day = datetime.fromtimestamp(start_ts).replace(minute=0, second=0, microsecond=0)
    while len(slots) < len(files):
        for h in hours:
            t = day.replace(hour=h)
            if t.timestamp() > start_ts:
                slots.append(t.timestamp())
            if len(slots) >= len(files):
                break
        day = (day + timedelta(days=1)).replace(hour=0)
    return slots
