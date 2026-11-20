"""
REST API Studio v6 (prefix /api) — dipakai UI baru di /app (folder web/).
"""
from __future__ import annotations

import io
import os
import re
import shutil
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

import config
from core import ai_providers as ai
from core import creator, db, ffx, jobs, media_ai, scheduler, tools

router = APIRouter(prefix="/api")

ALLOWED_ROOTS = [Path(config.OUTPUT_DIR).resolve(), db.DATA_DIR.resolve(), (config.BASE_DIR / "mentahan").resolve()]
UPLOAD_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac",
              ".png", ".jpg", ".jpeg", ".webp", ".gif", ".txt", ".srt", ".vtt", ".pdf", ".json"}
MAX_UPLOAD = 2 * 1024 ** 3


def _safe_path(raw: str) -> Path:
    try:
        p = Path(raw).resolve()
    except (OSError, ValueError):
        raise HTTPException(400, "Path tidak valid")
    if not any(p == root or root in p.parents for root in ALLOWED_ROOTS):
        raise HTTPException(403, "Akses file di luar folder output/data ditolak")
    if not p.exists() or not p.is_file():
        raise HTTPException(404, "File tidak ditemukan")
    return p


def _wrap(fn, *args):
    try:
        return fn(*args)
    except HTTPException:
        raise
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(502, str(exc)[:800])


# ----------------------------------------------------------------------------- status & katalog

@router.get("/status")
def status():
    current = jobs.CURRENT.get("id")
    return {
        "version": "6.8",
        "ffmpeg": ffx.available(),
        "ai": {"provider": config.AI_PROVIDER, "model": config.LLM_MODEL, "providers": ai.provider_status()},
        "media": media_ai.capabilities(),
        "platforms": scheduler.platform_status(),
        "current_job": db.get_job(current) if current else None,
        "queued": len(db.queued_job_ids()),
    }


@router.get("/catalog")
def catalog():
    return {**creator.catalog(), "edit_styles": ["minimal", "dinamis", "maksimal"],
            "color_grades": ["none", "natural", "vibrant", "cinematic"], "job_kinds": sorted(jobs.HANDLERS)}


class YouTubeCookiesRequest(BaseModel):
    text: str = ""


@router.get("/youtube/cookies")
def youtube_cookies_status():
    """Status cookies YouTube — tidak pernah mengembalikan isi cookie."""
    from core import youtube_browser
    return youtube_browser.cookie_status()


@router.post("/youtube/cookies")
def youtube_cookies_save(req: YouTubeCookiesRequest):
    """Simpan cookies dari teks (JSON atau Netscape cookies.txt)."""
    from core import youtube_browser
    return _wrap(youtube_browser.save_cookies, req.text)


@router.delete("/youtube/cookies")
def youtube_cookies_clear():
    from core import youtube_browser
    return _wrap(youtube_browser.clear_cookies)


@router.post("/youtube/test")
def youtube_test():
    """Cek sesi cookies tanpa upload (headless, tanpa popup)."""
    from core import youtube_browser
    return _wrap(youtube_browser.test_session)


@router.get("/campaign/presets")
def campaign_presets():
    """Daftar preset MotionKlip di presets/, lengkap dengan ringkasan isinya."""
    from core import campaign

    folder = Path(config.BASE_DIR) / "presets"
    out: List[Dict[str, Any]] = []
    if folder.is_dir():
        for path in sorted(folder.glob("*.json")):
            try:
                preset = campaign.load_preset(path)
            except Exception as exc:  # noqa: BLE001 - preset rusak jangan mematikan halaman
                out.append({"file": path.name, "error": str(exc)[:200]})
                continue
            out.append({
                "file": path.name,
                "layout": preset["layout"],
                "subtitle_provider": preset.get("subtitle_provider") or "none",
                "intro_voice": preset.get("intro_voice") or "none",
                "fps": preset.get("fps"),
                "hashtags": preset.get("hashtags", ""),
                "discord_tag": preset.get("discord_tag", ""),
                "watermark": Path(preset["watermark"]).name,
                "outro": Path(preset["outro"]).name,
            })
    return {"presets": out, "folder": str(folder)}


@router.get("/tts/voices")
def tts_voices(provider: str = "edge"):
    voices = _wrap(media_ai.list_voices, provider)
    if provider == "elevenlabs":
        cloned = db.kv_get("cloned_voices", []) or []
        known = {v["id"] for v in voices}
        voices += [{"id": c["voice_id"], "label": f"{c['name']} (clone)", "lang": "multi"} for c in cloned if c["voice_id"] not in known]
    return {"provider": provider, "voices": voices, "ready": media_ai.tts_available(provider)}


# ----------------------------------------------------------------------------- file

@router.post("/upload")
async def upload(file: UploadFile = File(...)):
    name = re.sub(r"[^\w.\- ]", "_", Path(file.filename or "file").name)[-120:]
    ext = Path(name).suffix.lower()
    if ext not in UPLOAD_EXT:
        raise HTTPException(400, f"Tipe file {ext or '?'} tidak didukung")
    dest = db.UPLOAD_DIR / f"{db.new_id()}_{name}"
    size = 0
    with open(dest, "wb") as fh:
        while True:
            chunk = await file.read(1 << 20)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_UPLOAD:
                fh.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(413, "File melebihi 2 GB")
            fh.write(chunk)
    info: Dict[str, Any] = {}
    if ext in {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}:
        try:
            info = ffx.probe(str(dest))
        except Exception:
            info = {}
    kind = "upload_audio" if ext in {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"} else \
        "upload_image" if ext in {".png", ".jpg", ".jpeg", ".webp", ".gif"} else \
        "upload_doc" if ext in {".txt", ".srt", ".vtt", ".pdf", ".json"} else "upload_video"
    asset = db.add_asset(kind, str(dest), name=name, meta=info)
    return {"path": str(dest), "name": name, "size": size, "asset_id": asset["id"], "kind": kind, **info}


@router.get("/file")
def get_file(path: str, download: bool = False):
    p = _safe_path(path)
    return FileResponse(p, filename=p.name if download else None,
                        content_disposition_type="attachment" if download else "inline")


@router.get("/frame")
def frame(path: str, t: float = 1.0, w: int = 640):
    p = _safe_path(path)
    out = db.DATA_DIR / "frames" / f"{abs(hash((str(p), round(t, 1), w)))}.jpg"
    out.parent.mkdir(exist_ok=True)
    if not out.exists():
        _wrap(ffx.extract_frame, str(p), t, str(out), w)
    return FileResponse(out, media_type="image/jpeg")


class ZipRequest(BaseModel):
    paths: List[str] = []
    asset_ids: List[str] = []
    name: str = "klip"


@router.post("/zip")
def make_zip(req: ZipRequest):
    files = [_safe_path(p) for p in req.paths]
    for aid in req.asset_ids:
        a = db.get_asset(aid)
        if a and Path(a["path"]).exists():
            files.append(_safe_path(a["path"]))
    if not files:
        raise HTTPException(400, "Tidak ada file")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        seen = set()
        for f in files:
            arc = f.name if f.name not in seen else f"{f.parent.name}_{f.name}"
            seen.add(arc)
            zf.write(f, arc)
            thumb = f.with_name("thumbnail.jpg")
            if thumb.exists() and f"{f.stem}_thumb.jpg" not in seen:
                zf.write(thumb, f"{f.stem}_thumb.jpg")
    buf.seek(0)
    safe = re.sub(r"[^\w\-]", "_", req.name)[:40] or "klip"
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{safe}.zip"'})


# ----------------------------------------------------------------------------- jobs

class JobRequest(BaseModel):
    kind: str
    params: Dict[str, Any] = {}
    title: str = ""


@router.post("/jobs")
def submit_job(req: JobRequest):
    if req.kind not in jobs.HANDLERS:
        raise HTTPException(400, f"Jenis job tidak dikenal: {req.kind}")
    return jobs.submit(req.kind, req.params, title=req.title or req.kind)


@router.get("/jobs")
def list_jobs(limit: int = 60, kind: str = ""):
    rows = db.list_jobs(limit=limit, kind=kind)
    for r in rows:
        res = r.get("result") or {}
        r["summary"] = {"total_clips": res.get("total_clips"), "file": res.get("file"), "thumbnail": res.get("thumbnail")}
        r.pop("result", None)
        r["params"] = {k: v for k, v in (r.get("params") or {}).items() if k in ("url", "source", "path", "mode")}
    return rows


@router.get("/jobs/{job_id}")
def get_job(job_id: str, log: bool = False):
    job = db.get_job(job_id, with_log=log)
    if not job:
        raise HTTPException(404, "Job tidak ditemukan")
    return job


# ----------------------------------------------------------------------------- Koneksi API (v6.6)

class ConnectionRequest(BaseModel):
    name: str = ""
    base_url: str = ""
    api_key: str = ""
    format: str = "auto"
    default_model: Optional[str] = None
    image_model: Optional[str] = None


def _conn_call(fn, *args, **kwargs):
    from core import connections
    try:
        return fn(*args, **kwargs)
    except connections.ConnectionError_ as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(400, f"Koneksi gagal: {str(exc)[:300]}")


@router.get("/connections")
def list_connections():
    from core import connections
    return [connections.public(c) for c in connections.load()]


@router.post("/connections")
def add_connection(req: ConnectionRequest):
    from core import connections
    conn = _conn_call(connections.add, req.name, req.base_url, req.api_key, req.format or "auto")
    return connections.public(conn)


@router.patch("/connections/{conn_id}")
def update_connection(conn_id: str, req: ConnectionRequest):
    from core import connections
    fields = {k: v for k, v in req.model_dump().items() if v not in (None, "")}
    fields.pop("format", None) if req.format == "auto" else None
    conn = _conn_call(connections.update, conn_id, **fields)
    if req.api_key or req.base_url:
        conn = _conn_call(connections.refresh, conn_id)
    return connections.public(conn)


@router.post("/connections/{conn_id}/detect")
def detect_connection(conn_id: str):
    from core import connections
    return connections.public(_conn_call(connections.refresh, conn_id))


@router.delete("/connections/{conn_id}")
def delete_connection(conn_id: str):
    from core import connections
    _conn_call(connections.remove, conn_id)
    return {"ok": True}


@router.post("/jobs/{job_id}/stop-live")
def stop_live(job_id: str):
    """Mode Live: berhenti merekam, proses sisa rekaman, lalu selesai (beda dengan Batalkan)."""
    from core import live
    job = db.get_job(job_id)
    if not job or job["kind"] != "live_clip":
        raise HTTPException(404, "Job live tidak ditemukan")
    if job["status"] != "running":
        raise HTTPException(400, "Job live tidak sedang berjalan")
    live.request_stop(job_id)
    db.update_job(job_id, message="Menghentikan rekaman… sisa rekaman tetap diproses jadi klip")
    return db.get_job(job_id)


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    try:
        return jobs.cancel(job_id)
    except KeyError:
        raise HTTPException(404, "Job tidak ditemukan")


@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: str):
    try:
        return jobs.retry(job_id)
    except KeyError:
        raise HTTPException(404, "Job tidak ditemukan")


@router.delete("/jobs/{job_id}")
def delete_job(job_id: str, files: bool = False):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job tidak ditemukan")
    if job["status"] == "running":
        raise HTTPException(409, "Job sedang berjalan — batalkan dulu")
    if files:
        folder = (Path(config.OUTPUT_DIR) / f"job_{job_id}").resolve()
        if folder.exists() and Path(config.OUTPUT_DIR).resolve() in folder.parents:
            shutil.rmtree(folder, ignore_errors=True)
    db.delete_job(job_id)
    return {"ok": True}


# ----------------------------------------------------------------------------- library / aset

def _dir_size(path: Path) -> int:
    total = 0
    if path.exists():
        for f in path.rglob("*"):
            if f.is_file():
                try:
                    total += f.stat().st_size
                except OSError:
                    pass
    return total


@router.get("/assets")
def list_assets(kind: str = "", limit: int = 500):
    kinds = [k for k in kind.split(",") if k]
    rows = []
    for k in kinds or [""]:
        rows += db.list_assets(k, limit)
    rows.sort(key=lambda a: -(a.get("created") or 0))
    for a in rows:
        a["exists"] = Path(a["path"]).exists()
        try:
            a["size"] = Path(a["path"]).stat().st_size if a["exists"] else 0
        except OSError:
            a["size"] = 0
    return rows


@router.get("/storage")
def storage():
    return {"output_bytes": _dir_size(Path(config.OUTPUT_DIR)), "data_bytes": _dir_size(db.DATA_DIR),
            "mentahan_bytes": _dir_size(config.BASE_DIR / "mentahan"),
            "disk_free_bytes": shutil.disk_usage(str(config.BASE_DIR)).free}


@router.delete("/assets/{asset_id}")
def delete_asset(asset_id: str, file: bool = True):
    asset = db.delete_asset(asset_id)
    if not asset:
        raise HTTPException(404, "Aset tidak ditemukan")
    if file:
        try:
            p = _safe_path(asset["path"])
            p.unlink(missing_ok=True)
            for side in (p.with_suffix(".jpg"),):
                side.unlink(missing_ok=True)
        except HTTPException:
            pass
    return {"ok": True}


# ----------------------------------------------------------------------------- creator

class CreatorRequest(BaseModel):
    params: Dict[str, Any] = {}
    scene: Dict[str, Any] = {}


@router.post("/creator/script")
def creator_script(req: CreatorRequest):
    return _wrap(creator.generate_script, req.params)


@router.post("/creator/scene-image")
def creator_scene_image(req: CreatorRequest):
    folder = db.DATA_DIR / "storyboard"
    folder.mkdir(exist_ok=True)
    return _wrap(creator.preview_scene_image, req.params, req.scene, folder)


# ----------------------------------------------------------------------------- tool AI sinkron

class ToolRequest(BaseModel):
    params: Dict[str, Any] = {}


@router.post("/tools/title")
def tool_title(req: ToolRequest):
    return _wrap(tools.optimize_title, req.params)


@router.post("/tools/prompt")
def tool_prompt(req: ToolRequest):
    return _wrap(tools.generate_prompts, req.params)


@router.post("/tools/viral-ideas")
def tool_viral(req: ToolRequest):
    return _wrap(tools.viral_suggestions, req.params)


@router.post("/tools/chat")
def tool_chat(req: ToolRequest):
    return _wrap(tools.assistant_chat, req.params)


@router.post("/tools/thumbnail")
def tool_thumbnail(req: ToolRequest):
    if not req.params.get("url"):
        raise HTTPException(400, "Masukkan link video")
    return _wrap(tools.thumbnail_download, req.params)


@router.post("/tools/video-info")
def tool_video_info(req: ToolRequest):
    from core import sources
    if not req.params.get("url"):
        raise HTTPException(400, "Masukkan link video")
    info = _wrap(sources.video_info, req.params["url"])
    info.pop("subtitles", None)
    info.pop("automatic_captions", None)
    return info


@router.get("/analytics")
def analytics(category: str = "Semua", refresh: bool = False):
    return _wrap(tools.viral_analytics, {"category": category, "refresh": refresh})


@router.get("/stock")
def stock(q: str, kind: str = "video", orientation: str = "portrait"):
    if not media_ai.stock_available():
        raise HTTPException(400, "Isi PEXELS_API_KEY atau PIXABAY_API_KEY di Pengaturan")
    return _wrap(media_ai.stock_search, q, kind, orientation, 12)


# ----------------------------------------------------------------------------- Brand Kit

class BrandKit(BaseModel):
    logo: Optional[str] = None
    position: str = "top-left"
    size: float = 0.22
    opacity: float = 0.9
    auto: bool = True
    x: Optional[float] = None
    y: Optional[float] = None
    remove_logo: bool = False
    outro: Optional[str] = None          # penutup klip: gambar atau video pendek
    outro_duration: float = 3.0         # lama tampil bila gambar (detik)
    outro_auto: bool = True
    remove_outro: bool = False


@router.get("/brand-kit")
def get_brand_kit():
    kit = db.kv_get("brand_kit", {}) or {}
    kit["has_logo"] = bool(kit.get("logo") and Path(kit["logo"]).exists())
    kit["has_outro"] = bool(kit.get("outro") and Path(kit["outro"]).exists())
    if kit["has_outro"]:
        kit["outro_kind"] = "image" if Path(kit["outro"]).suffix.lower() in ffx.OUTRO_IMAGE_EXT else "video"
    return kit


@router.post("/brand-kit")
def save_brand_kit(req: BrandKit):
    kit = db.kv_get("brand_kit", {}) or {}
    if req.remove_logo:
        kit.pop("logo", None)
    elif req.logo and req.logo != kit.get("logo"):
        src = _safe_path(req.logo)
        if src.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
            raise HTTPException(400, "Logo harus PNG/JPG/WebP")
        dest = db.ASSET_DIR / f"brand_logo{src.suffix.lower()}"
        shutil.copy2(src, dest)
        kit["logo"] = str(dest)
    if req.remove_outro:
        old = kit.pop("outro", None)
        if old:
            Path(old).unlink(missing_ok=True)
    elif req.outro and req.outro != kit.get("outro"):
        src = _safe_path(req.outro)
        ext = src.suffix.lower()
        if ext not in ffx.OUTRO_IMAGE_EXT + ffx.OUTRO_VIDEO_EXT:
            raise HTTPException(400, "Penutup harus gambar (PNG/JPG/WebP) atau video (MP4/MOV/WebM)")
        if ext in ffx.OUTRO_VIDEO_EXT:
            try:
                info = ffx.probe(str(src))
            except Exception:
                raise HTTPException(400, "Video penutup tidak bisa dibaca")
            if not info["width"]:
                raise HTTPException(400, "File penutup tidak berisi video")
            if info["duration"] > ffx.OUTRO_MAX_SEC + 0.5:
                raise HTTPException(400, f"Video penutup maksimal {ffx.OUTRO_MAX_SEC:.0f} detik "
                                         f"(file ini {info['duration']:.0f} dtk) — potong dulu di Video Editor")
        for old in db.ASSET_DIR.glob("brand_outro.*"):
            old.unlink(missing_ok=True)
        dest = db.ASSET_DIR / f"brand_outro{ext}"
        shutil.copy2(src, dest)
        kit["outro"] = str(dest)
    positions = ("top-left", "top-center", "top-right", "middle-left", "center", "middle-right",
                 "bottom-left", "bottom-center", "bottom-right", "custom")
    if req.position not in positions:
        raise HTTPException(400, "Posisi tidak valid")
    kit.update({"position": req.position, "size": max(0.05, min(0.6, req.size)),
                "opacity": max(0.05, min(1.0, req.opacity)), "auto": req.auto, "x": req.x, "y": req.y,
                "outro_duration": max(1.0, min(10.0, float(req.outro_duration or 3.0))), "outro_auto": req.outro_auto})
    db.kv_set("brand_kit", kit)
    return get_brand_kit()


# ----------------------------------------------------------------------------- Clone suara

class CloneRequest(BaseModel):
    name: str
    paths: List[str]
    description: str = ""
    remove_noise: bool = True


@router.get("/voices/cloned")
def cloned_voices():
    return {"voices": db.kv_get("cloned_voices", []) or [], "ready": bool(media_ai._key("ELEVENLABS_API_KEY"))}


@router.post("/voices/clone")
def clone_voice(req: CloneRequest):
    paths = [str(_safe_path(p)) for p in req.paths]
    if not paths:
        raise HTTPException(400, "Upload minimal satu rekaman suara (10 detik – 3 menit)")
    total = sum(ffx.probe(p)["duration"] for p in paths)
    if total < 8:
        raise HTTPException(400, f"Rekaman terlalu pendek ({total:.0f} dtk) — minimal ±10 detik")
    res = _wrap(media_ai.clone_voice, req.name, paths, req.description, req.remove_noise)
    voices = db.kv_get("cloned_voices", []) or []
    voices.append({**res, "created": time.time(), "duration": total})
    db.kv_set("cloned_voices", voices)
    return res


@router.delete("/voices/cloned/{voice_id}")
def delete_cloned(voice_id: str):
    try:
        media_ai.delete_voice(voice_id)
    except Exception:
        pass
    voices = [v for v in (db.kv_get("cloned_voices", []) or []) if v["voice_id"] != voice_id]
    db.kv_set("cloned_voices", voices)
    return {"ok": True}


# ----------------------------------------------------------------------------- Auto Upload

class ScheduleRequest(BaseModel):
    file_path: str = ""
    asset_id: str = ""
    title: str = ""
    description: str = ""
    platforms: List[str] = ["youtube"]
    run_at: float = 0


class BulkScheduleRequest(BaseModel):
    asset_ids: List[str]
    platforms: List[str] = ["youtube"]
    start_at: float = 0
    per_day: int = 3
    hours: List[int] = []


def _asset_copy(asset: Dict) -> Dict:
    meta = asset.get("meta") or {}
    caption = meta.get("upload_caption") or meta.get("description") or ""
    tags = " ".join(meta.get("hashtags") or [])
    return {"title": meta.get("ai_title") or meta.get("title") or asset.get("name") or "",
            "description": f"{caption}\n\n{tags}".strip()}


@router.get("/platforms")
def platforms():
    return scheduler.platform_status()


@router.get("/schedules")
def list_schedules():
    return db.list_schedules()


def _require_ready(platforms: List[str]) -> None:
    """Tolak jadwal ke platform yang belum tersambung — dulu diterima diam-diam lalu gagal saat jam upload tiba."""
    status = scheduler.platform_status()
    for name in platforms:
        info = status.get(name) or {}
        if not info.get("ready"):
            raise HTTPException(400, f"{info.get('label', name)} belum tersambung. {info.get('hint', '')}".strip())


@router.post("/schedules")
def add_schedule(req: ScheduleRequest):
    path, copy = req.file_path, {"title": req.title, "description": req.description}
    if req.asset_id:
        asset = db.get_asset(req.asset_id)
        if not asset:
            raise HTTPException(404, "Aset tidak ditemukan")
        path = asset["path"]
        auto = _asset_copy(asset)
        copy = {"title": req.title or auto["title"], "description": req.description or auto["description"]}
    _safe_path(path)
    platforms = [p for p in req.platforms if p in scheduler.PLATFORMS]
    if not platforms:
        raise HTTPException(400, "Pilih minimal satu platform")
    _require_ready(platforms)
    return db.add_schedule(path, copy["title"], copy["description"], platforms, req.run_at or time.time())


@router.post("/schedules/bulk")
def bulk_schedule(req: BulkScheduleRequest):
    assets = [a for a in (db.get_asset(i) for i in req.asset_ids) if a and Path(a["path"]).exists()]
    if not assets:
        raise HTTPException(400, "Tidak ada klip valid")
    slots = scheduler.smart_cadence([a["path"] for a in assets], req.start_at or time.time(), max(1, min(req.per_day, 6)),
                                    req.hours or None)
    platforms = [p for p in req.platforms if p in scheduler.PLATFORMS] or ["youtube"]
    _require_ready(platforms)
    out = []
    for asset, ts in zip(assets, slots):
        copy = _asset_copy(asset)
        out.append(db.add_schedule(asset["path"], copy["title"], copy["description"], platforms, ts))
    return out


@router.post("/schedules/{sid}/run-now")
def run_schedule_now(sid: str):
    item = db.get_schedule(sid)
    if not item:
        raise HTTPException(404, "Jadwal tidak ditemukan")
    db.update_schedule(sid, status="uploading")
    report = scheduler.upload_now(item)
    db.update_schedule(sid, status="done" if scheduler._ok(report) else "failed", result=report)
    return db.get_schedule(sid)


class ScheduleUpdate(BaseModel):
    run_at: Optional[float] = None
    title: Optional[str] = None
    description: Optional[str] = None
    platforms: Optional[List[str]] = None
    status: Optional[str] = None


@router.patch("/schedules/{sid}")
def update_schedule(sid: str, req: ScheduleUpdate):
    if not db.get_schedule(sid):
        raise HTTPException(404, "Jadwal tidak ditemukan")
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    if "status" in fields and fields["status"] not in ("pending", "paused"):
        raise HTTPException(400, "Status hanya bisa pending/paused")
    db.update_schedule(sid, **fields)
    return db.get_schedule(sid)


@router.delete("/schedules/{sid}")
def delete_schedule(sid: str):
    db.delete_schedule(sid)
    return {"ok": True}
