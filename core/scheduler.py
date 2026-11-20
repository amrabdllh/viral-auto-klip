"""
Penjadwal Auto Upload: cek antrean jadwal tiap SCHEDULER_INTERVAL detik dan upload yang sudah jatuh tempo
ke YouTube Shorts / TikTok lewat core/publisher.py. Status: pending → uploading → done | failed.
"""
from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Dict, List

from loguru import logger

import config
from . import db

PLATFORMS = ["youtube", "tiktok"]
_STARTED = False
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
    return {
        "youtube": {"label": "YouTube Shorts",
                    "ready": bool(yt_cookie_ok or (yt_secret and Path(yt_secret).exists() and yt_lib)),
                    "browser": yt_cookie_ok, "logged_in": token.exists(), "library": yt_lib,
                    "hint": "Tempel cookies di Pengaturan → YouTube (browser headless), atau isi YOUTUBE_CLIENT_SECRET_FILE"},
        "tiktok": {"label": "TikTok", "ready": bool(tt_cookie_ok or (cookies and Path(cookies).exists())),
                   "browser": tt_cookie_ok, "library": True,
                   "hint": "Tempel cookies di Pengaturan → TikTok (browser headless)"},
    }


def upload_now(item: Dict) -> Dict:
    """Upload satu jadwal. YouTube memakai browser headless kalau cookies ada
    (bebas kuota API); kalau tidak, jatuh ke API. TikTok selalu browser."""
    from . import tiktok_browser, youtube_browser
    from .publisher import upload_to_tiktok, upload_to_youtube

    report: Dict = {}
    path = item["file_path"]
    if not Path(path).exists():
        return {"error": f"File tidak ada: {path}"}
    tags = re.findall(r"#(\w+)", item.get("description", ""))
    title = (item.get("title") or Path(path).stem)[:100]
    description = item.get("description", "")

    for platform in item.get("platforms") or []:
        try:
            if platform == "youtube":
                if youtube_browser.cookie_status().get("configured"):
                    res = youtube_browser.upload_video(path, title=title, description=description,
                                                       tags=tags, privacy="private")
                    report["youtube"] = {"status": "done", **res}
                else:
                    report["youtube"] = upload_to_youtube(path, title=title,
                                                          description=description, tags=tags)
            elif platform == "tiktok":
                if tiktok_browser.cookie_status().get("configured"):
                    report["tiktok"] = tiktok_browser.upload_video(str(path), description=f"{title} {description}".strip())
                else:
                    report["tiktok"] = upload_to_tiktok(path, description=f"{title} {description}".strip())
        except Exception as exc:
            report[platform] = {"status": "failed", "reason": str(exc)[:400]}
    return report


def _ok(report: Dict) -> bool:
    if "error" in report or not report:
        return False
    return all(str((v or {}).get("status", "")).lower() not in ("failed", "error", "skipped") for v in report.values())


def tick() -> int:
    due = db.due_schedules(time.time())
    for item in due:
        db.update_schedule(item["id"], status="uploading")
        logger.info(f"Auto Upload: {Path(item['file_path']).name} → {item['platforms']}")
        report = upload_now(item)
        db.update_schedule(item["id"], status="done" if _ok(report) else "failed", result=report)
    return len(due)


def _loop() -> None:
    while True:
        try:
            tick()
        except Exception:
            logger.exception("Scheduler tick gagal")
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
