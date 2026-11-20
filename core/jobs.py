"""
Antrean job tahan restart.

- Job disimpan di SQLite (core/db.py) — tidak hilang saat server mati.
- Satu worker menjalankan job berurutan: modul `config` bersifat global, jadi dua job
  paralel akan saling menimpa pengaturan (durasi, provider AI, dsb).
- Handler didaftarkan per `kind` lewat @handler("kind"); handler menerima JobContext.
"""
from __future__ import annotations

import queue
import threading
import time
import traceback
from pathlib import Path
from typing import Callable, Dict, Optional

from loguru import logger

import config
from . import db

HANDLERS: Dict[str, Callable] = {}
# Job "background" (mis. rekaman Mode Live berjam-jam) jalan di thread sendiri, tidak memblokir antrean.
# Handler-nya hanya boleh merekam/menjadwalkan — pekerjaan berat dikirim sebagai job biasa ke antrean.
BACKGROUND: set = set()
_QUEUE: "queue.Queue[str]" = queue.Queue()
_CANCEL: set = set()
_STARTED = False
_START_LOCK = threading.Lock()
CURRENT: Dict[str, Optional[str]] = {"id": None}


class JobCancelled(Exception):
    pass


class JobContext:
    def __init__(self, job_id: str, params: Dict):
        self.id = job_id
        self.params = params
        self.dir = Path(config.OUTPUT_DIR) / f"job_{job_id}"
        self.dir.mkdir(parents=True, exist_ok=True)

    def progress(self, pct: int, stage: str = "", message: str = "") -> None:
        self.check_cancel()
        fields = {"progress": max(0, min(100, int(pct)))}
        if stage:
            fields["stage"] = stage
        if message:
            fields["message"] = message
        db.update_job(self.id, **fields)
        if message or stage:
            db.append_log(self.id, f"{pct}% {stage} — {message}".strip(" —"))

    def log(self, line: str) -> None:
        db.append_log(self.id, line)

    def check_cancel(self) -> None:
        if self.id in _CANCEL:
            raise JobCancelled()

    def cancel_requested(self) -> bool:
        return self.id in _CANCEL

    def asset(self, kind: str, path, name: str = "", meta: Optional[Dict] = None) -> Dict:
        return db.add_asset(kind, str(path), name=name, job_id=self.id, meta=meta)


def handler(kind: str, background: bool = False):
    def deco(fn):
        HANDLERS[kind] = fn
        if background:
            BACKGROUND.add(kind)
        return fn
    return deco


# Job ringan (API/ffmpeg singkat) jalan di thread sendiri: dulu TTS 5 detik harus antre di belakang
# job klip yang bisa 40 menit karena semua job non-live berbagi satu worker.
LIGHT = {"tts", "music", "sfx", "transcript", "fetch_source", "wa_story", "loop", "merge"}


def _dispatch(job_id: str, kind: str) -> None:
    if kind in BACKGROUND or kind in LIGHT:
        threading.Thread(target=_run, args=(job_id, True), name=f"job-{kind}-{job_id}", daemon=True).start()
    else:
        _QUEUE.put(job_id)


def submit(kind: str, params: Dict, title: str = "") -> Dict:
    if kind not in HANDLERS:
        raise ValueError(f"Jenis job tidak dikenal: {kind}")
    start_worker()
    job_id = db.insert_job(kind, title or kind, params)
    _dispatch(job_id, kind)
    return db.get_job(job_id)


def cancel(job_id: str) -> Dict:
    job = db.get_job(job_id)
    if not job:
        raise KeyError(job_id)
    if job["status"] == "queued":
        db.update_job(job_id, status="cancelled", message="Dibatalkan sebelum mulai")
    elif job["status"] == "running":
        _CANCEL.add(job_id)
        db.update_job(job_id, message="Membatalkan… (berhenti di tahap berikutnya)")
    return db.get_job(job_id)


def retry(job_id: str) -> Dict:
    job = db.get_job(job_id)
    if not job:
        raise KeyError(job_id)
    return submit(job["kind"], job["params"], title=job["title"])


def _run(job_id: str, background: bool = False) -> None:
    job = db.get_job(job_id)
    if not job or job["status"] != "queued":
        return
    fn = HANDLERS.get(job["kind"])
    if not fn:
        db.update_job(job_id, status="error", message=f"Handler '{job['kind']}' tidak ada")
        return
    if not background:
        CURRENT["id"] = job_id
    db.update_job(job_id, status="running", stage="Mulai", message="Job dimulai")
    ctx = JobContext(job_id, job["params"])
    started = time.time()
    try:
        result = fn(ctx) or {}
        result.setdefault("output_folder", str(ctx.dir))
        result["elapsed_sec"] = round(time.time() - started, 1)
        db.update_job(job_id, status="done", progress=100, stage="Selesai",
                      message=result.pop("_message", "Selesai"), result=result)
        db.append_log(job_id, f"Selesai dalam {result['elapsed_sec']} dtk")
    except JobCancelled:
        db.update_job(job_id, status="cancelled", stage="Dibatalkan", message="Dibatalkan oleh pengguna")
    except Exception as exc:
        logger.exception(f"Job {job_id} ({job['kind']}) gagal")
        db.update_job(job_id, status="error", stage="Error", message=str(exc)[:1000])
        db.append_log(job_id, traceback.format_exc()[-4000:])
    finally:
        _CANCEL.discard(job_id)
        if not background:
            CURRENT["id"] = None


def _worker() -> None:
    while True:
        job_id = _QUEUE.get()
        try:
            _run(job_id)
        except Exception:
            logger.exception("Worker job crash")


def start_worker() -> None:
    global _STARTED
    with _START_LOCK:
        if _STARTED:
            return
        _STARTED = True
        try:
            from .live import cleanup_orphan_recorders
            cleanup_orphan_recorders()
        except Exception:
            pass
        interrupted = db.mark_interrupted()
        if interrupted:
            logger.warning(f"{interrupted} job terputus saat server mati ditandai 'interrupted'")
        threading.Thread(target=_worker, name="job-worker", daemon=True).start()
        for job_id in db.queued_job_ids():
            job = db.get_job(job_id)
            _dispatch(job_id, job["kind"] if job else "")
