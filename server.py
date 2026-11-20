"""
Viral Auto Klip v3 — FastAPI backend (bug-fixed & upgraded)
Run with: uvicorn server:app --reload --host 127.0.0.1 --port 8000

Changelog v3 (backend):
- FIX: configure_llm_clients dipanggil dengan keyword argument yang benar
       (sebelumnya API key salah posisi, tidak pernah terpakai).
- FIX: pipeline job jadi fungsi sync -> BackgroundTasks menjalankannya di
       threadpool (sebelumnya async & memblokir event loop, status polling macet).
- FIX: Pydantic v2 model_dump() (menggantikan .dict() yang deprecated).
- FIX: CORS allow_credentials=False (kombinasi "*" + credentials ditolak browser).
- NEW: endpoint GET /download/{job_id}/{clip_index} + /thumbnail (sebelumnya HTML
       UI memanggilnya tapi endpoint tidak ada -> 404).
- NEW: GET /jobs + auto-prune job lama (TTL 24 jam).
- NEW: publish package otomatis setelah render (folder publish/).
"""

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Optional, Dict
import threading
import uuid
import time
from pathlib import Path
import tempfile
import shutil
from datetime import datetime

# Import our core modules
from core.utils import setup_logging, download_video, get_video_info
from core.analyzer import analyze_viral_potential, configure_llm_clients
from core.clipper import process_clips
from core.transcriber import transcribe_video
from core import ai_providers as ai
import config
from loguru import logger

try:
    from groq import Groq
except ImportError:
    Groq = None

from core import ffx  # noqa: F401  (menambahkan ffmpeg/bin lokal ke PATH bila perlu)
from core import jobs as studio_jobs, scheduler as studio_scheduler
import studio_api
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse

app = FastAPI(title="Viral Auto Klip Studio API", version="6.8")
app.include_router(studio_api.router)
WEB_DIR = Path(__file__).parent / "web"
app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


@app.on_event("startup")
def _start_background_workers():
    studio_jobs.start_worker()
    studio_scheduler.start()

# Keamanan v4: server ini bisa menulis .env (API key) & mengupload video atas namamu.
# Dulu CORS "*" -> website APA PUN yang kamu buka bisa memanggil localhost:8000.
# Sekarang hanya halaman dari localhost/127.0.0.1 (UI di /app) yang diizinkan.
LOCAL_ORIGIN_RE = r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$"
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=LOCAL_ORIGIN_RE,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def block_foreign_writes(request, call_next):
    """Tolak request pengubah (POST/PUT/DELETE) dari origin selain localhost.
    Request tanpa header Origin (CLI, curl, skrip lokal) tetap diizinkan."""
    import re as _re
    from fastapi.responses import JSONResponse
    origin = request.headers.get("origin")
    if request.method not in ("GET", "HEAD", "OPTIONS") and origin and not _re.match(LOCAL_ORIGIN_RE, origin):
        return JSONResponse(
            {"detail": "Ditolak: buka UI lewat http://localhost:8000/app (bukan file HTML langsung / website lain)."},
            status_code=403,
        )
    return await call_next(request)

setup_logging()

# In-memory job store (use Redis/Postgres in production)
JOBS: Dict[str, Dict] = {}
JOB_TTL_SECONDS = 24 * 3600
PIPELINE_LOCK = threading.Lock()


def _prune_jobs():
    """Hapus job yang lebih tua dari TTL agar memori tidak bocor."""
    now = time.time()
    for job_id in list(JOBS.keys()):
        created = JOBS[job_id].get("_created_ts")
        if created and now - created > JOB_TTL_SECONDS:
            JOBS.pop(job_id, None)


class GroqVerifyRequest(BaseModel):
    api_key: str


class ProcessRequest(BaseModel):
    url: str
    num_clips: int = 8
    min_score: float = 60
    min_duration: int = 15
    max_duration: int = 60
    caption_style: str = "modern"
    enable_captions: bool = True
    enable_face_reframe: bool = True
    use_llm: bool = True
    whisper_model: str = config.WHISPER_MODEL  # FIX: dulu "medium" (beda dengan config)
    whisper_device: str = "auto"               # auto | cuda | cpu | groq | elevenlabs
    whisper_compute_type: str = "int8"
    language: Optional[str] = None
    categories: Optional[List[str]] = None
    watermark_text: Optional[str] = None
    target_platform: str = "Semua"
    groq_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None
    elevenlabs_api_key: Optional[str] = None
    ai_provider: str = ""
    ai_model: str = ""
    ai_api_key: Optional[str] = None   # key untuk provider terpilih (Anthropic, DeepSeek, Mistral, Custom, ...)
    ai_base_url: Optional[str] = None  # Custom / Ollama / proxy
    publish_package: bool = True
    # Pro editing (v4): preset + override per fitur (None = ikut preset)
    edit_style: str = ""               # minimal | dinamis | maksimal (kosong = EDIT_STYLE di .env)
    jump_cuts: Optional[bool] = None
    dynamic_zoom: Optional[bool] = None
    progress_bar: Optional[bool] = None
    sfx: Optional[bool] = None
    color_grade: Optional[str] = None  # none | natural | vibrant | cinematic
    smart_thumbnail: Optional[bool] = None
    end_cta: Optional[bool] = None


class AITestRequest(BaseModel):
    provider: str
    model: str = ""
    api_key: Optional[str] = None
    base_url: Optional[str] = None


class UploadRequest(BaseModel):
    youtube: bool = False
    tiktok: bool = False
    limit: int = 0
    youtube_client_secret: Optional[str] = None


class JobStatus(BaseModel):
    job_id: str
    status: str  # queued, downloading, transcribing, analyzing, rendering, done, error
    progress: int
    current_stage: str
    message: str
    result: Optional[Dict] = None


UI_FILE = Path(__file__).parent / "viral_auto_klip_v2_real.html"


@app.get("/")
async def health(request: Request):
    if "text/html" in request.headers.get("accept", ""):
        return RedirectResponse("/app")
    return {"status": "ok", "service": "Viral Auto Klip Studio API", "version": "6.8", "ui": "/app", "legacy_ui": "/legacy"}


@app.get("/app", include_in_schema=False)
async def web_ui():
    """UI Studio v6: http://localhost:8000/app"""
    return FileResponse(WEB_DIR / "index.html", media_type="text/html", headers={"Cache-Control": "no-cache"})


@app.get("/legacy", include_in_schema=False)
async def legacy_ui():
    """UI v4 lama (satu halaman) tetap tersedia."""
    return FileResponse(UI_FILE, media_type="text/html")


@app.get("/providers")
async def list_providers():
    """Status semua provider AI + model + mesin transkripsi (untuk dropdown UI)."""
    return {
        "active_provider": config.AI_PROVIDER,
        "active_model": config.LLM_MODEL,
        "providers": ai.provider_status(),
        "transcribers": {
            "local": True,
            "groq": bool(config.GROQ_API_KEY),
            "elevenlabs": bool(config.ELEVENLABS_API_KEY),
        },
        "google_login": ai.google_login_status(),
    }


@app.post("/test-ai")
def test_ai(req: AITestRequest):
    if req.api_key or req.base_url:  # hanya mengganti kredensial yang perlu dikunci dari pipeline lama
        if not PIPELINE_LOCK.acquire(timeout=5):
            return {"valid": False, "message": "Sedang ada proses klip berjalan — coba tes lagi setelah selesai."}
        try:
            ai.set_credentials(req.provider, api_key=req.api_key or "", base_url=req.base_url or "", model=req.model)
        finally:
            PIPELINE_LOCK.release()
    ok, message = ai.test_connection(req.provider, ai.resolve_model(req.provider, req.model))
    return {"valid": ok, "message": message}


class DetectModelsRequest(BaseModel):
    provider: str
    api_key: Optional[str] = None
    base_url: Optional[str] = None


@app.post("/detect-models")
def detect_models(req: DetectModelsRequest):
    """Daftar model yang tersedia untuk API key provider ini (key dari form tidak disimpan)."""
    try:
        return ai.detect_models(req.provider, api_key=req.api_key or "", base_url=req.base_url or "")
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        msg = str(exc)
        if status == 401 or "401" in msg or "invalid" in msg.lower() and "key" in msg.lower():
            msg = "API key ditolak provider (401) — cek lagi key-nya. " + msg[:150]
        raise HTTPException(400, msg[:400])


class SettingsUpdate(BaseModel):
    values: Dict[str, object] = {}
    clear: List[str] = []


@app.get("/config")
def read_config():
    """Pengaturan yang bisa diedit dari UI (API key hanya tersamar)."""
    from core import settings
    return settings.get_settings()


@app.post("/config")
def write_config(req: SettingsUpdate):
    """Simpan pengaturan ke .env + terapkan langsung tanpa restart."""
    from core import settings
    try:
        return settings.save_settings(req.values, req.clear)
    except settings.SettingsError as exc:
        raise HTTPException(400, str(exc))


@app.get("/config/recommended")
def preview_recommended_config():
    """Pratinjau setelan terbaik berdasarkan API key yang terpasang (belum disimpan)."""
    from core import settings
    return settings.recommended()


@app.post("/config/recommended")
def apply_recommended_config():
    from core import settings
    try:
        return settings.apply_recommended()
    except settings.SettingsError as exc:
        raise HTTPException(400, str(exc))


class ClientSecretUpload(BaseModel):
    content: str


@app.get("/google-status")
def google_status():
    return ai.google_login_status()


@app.post("/google-client-secret")
def upload_google_client_secret(req: ClientSecretUpload):
    """Simpan client_secret.json (OAuth Desktop app) yang dipilih user di UI — tanpa edit .env."""
    if len(req.content) > 50_000:
        raise HTTPException(400, "File terlalu besar untuk client_secret.json")
    try:
        return {"ok": True, **ai.save_client_secret(req.content)}
    except ai.AIError as exc:
        raise HTTPException(400, str(exc))


@app.post("/google-login")
def google_login():
    """Login Google (Antigravity). Membuka browser di komputer yang menjalankan server (pemakaian lokal)."""
    try:
        return {"ok": True, **ai.google_login()}
    except ai.GoogleSetupNeeded as exc:
        raise HTTPException(409, {"setup_needed": True, "message": str(exc)})
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.post("/google-logout")
def google_logout():
    return {"ok": True, **ai.google_logout()}


@app.post("/upload/{job_id}")
def upload_job(job_id: str, req: UploadRequest):
    """Upload klip hasil job ke YouTube Shorts / TikTok (tanpa render ulang)."""
    if job_id not in JOBS or JOBS[job_id]["status"] != "done":
        raise HTTPException(404, "Job belum selesai / tidak ditemukan")
    from core.publisher import upload_clips
    clips = JOBS[job_id]["result"].get("clips") or []
    return upload_clips(clips, youtube=req.youtube, tiktok=req.tiktok,
                        client_secrets_path=req.youtube_client_secret, limit=req.limit)


@app.post("/verify-groq")
async def verify_groq_key(request: GroqVerifyRequest):
    if Groq is None:
        return {"valid": False, "message": "Library groq belum terpasang. Jalankan: pip install groq"}
    try:
        client = Groq(api_key=request.api_key)
        # Test dengan list models (ringan)
        models = client.models.list()
        return {"valid": True, "message": "Groq API Key valid"}
    except Exception as e:
        return {"valid": False, "message": str(e)}


@app.post("/process-video", response_model=JobStatus)
async def start_processing(req: ProcessRequest, background_tasks: BackgroundTasks):
    _prune_jobs()
    job_id = str(uuid.uuid4())

    JOBS[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "progress": 0,
        "current_stage": "Initializing",
        "message": "Job queued",
        "request": req.model_dump(),
        "result": None,
        "created_at": datetime.now().isoformat(),
        "_created_ts": time.time(),
    }

    background_tasks.add_task(process_video_job, job_id, req)
    return JobStatus(**JOBS[job_id])


@app.get("/status/{job_id}", response_model=JobStatus)
async def get_status(job_id: str):
    _prune_jobs()
    if job_id not in JOBS:
        raise HTTPException(404, "Job not found")
    return JobStatus(**JOBS[job_id])


@app.get("/results/{job_id}")
async def get_results(job_id: str):
    _prune_jobs()
    if job_id not in JOBS or JOBS[job_id]["status"] != "done":
        raise HTTPException(404, "Results not ready")
    return JOBS[job_id]["result"]


def _job_clip_file(job_id: str, clip_index: str, field: str) -> Path:
    _prune_jobs()
    if job_id not in JOBS:
        raise HTTPException(404, "Job not found")
    result = JOBS[job_id].get("result") or {}
    clips = result.get("clips") or []
    try:
        index = int(clip_index)
    except ValueError:
        raise HTTPException(400, "clip_index harus angka")
    if index < 0 or index >= len(clips):
        raise HTTPException(404, "Clip tidak ditemukan")
    path = clips[index].get(field) or ""
    file_path = Path(path)
    if not path or not file_path.exists():
        raise HTTPException(404, f"File {field} tidak ditemukan di disk")
    return file_path


@app.get("/download/{job_id}/{clip_index}")
async def download_clip(job_id: str, clip_index: str):
    """Download MP4 klip hasil (dipakai HTML UI)."""
    file_path = _job_clip_file(job_id, clip_index, "file_path")
    return FileResponse(file_path, media_type="video/mp4", filename=file_path.name)


@app.get("/download/{job_id}/{clip_index}/thumbnail")
async def download_thumbnail(job_id: str, clip_index: str):
    """Download thumbnail klip hasil."""
    file_path = _job_clip_file(job_id, clip_index, "thumbnail")
    return FileResponse(file_path, media_type="image/jpeg", filename=file_path.name)


@app.get("/download/{job_id}/{clip_index}/thumbnail_b")
async def download_thumbnail_b(job_id: str, clip_index: str):
    """Cover varian B (frame & hook alternatif) untuk A/B test."""
    file_path = _job_clip_file(job_id, clip_index, "thumbnail_alt")
    return FileResponse(file_path, media_type="image/jpeg", filename=file_path.name)


@app.get("/jobs")
async def list_jobs():
    _prune_jobs()
    return [
        {
            "job_id": job["job_id"],
            "status": job["status"],
            "progress": job["progress"],
            "current_stage": job["current_stage"],
            "message": job.get("message"),
            "created_at": job.get("created_at"),
            "url": (job.get("request") or {}).get("url"),
            "total_clips": (job.get("result") or {}).get("total_clips"),
        }
        for job in sorted(JOBS.values(), key=lambda j: j.get("_created_ts", 0), reverse=True)
    ]


def _update_job(job_id: str, **kwargs):
    JOBS[job_id].update(kwargs)


def process_video_job(job_id: str, req: ProcessRequest):
    """Real video processing pipeline (sync -> threadpool via BackgroundTasks).

    FIX v4: config bersifat global — dua job paralel dulu saling menimpa pengaturan
    (durasi, provider AI, API key). Sekarang job dijalankan bergiliran (status 'queued').
    """
    if PIPELINE_LOCK.locked():
        _update_job(job_id, message="Menunggu job lain selesai...")
    with PIPELINE_LOCK:
        _run_pipeline(job_id, req)


def _run_pipeline(job_id: str, req: ProcessRequest):
    temp_dir = None
    try:
        temp_dir = Path(tempfile.mkdtemp())
        if req.language is not None:
            config.WHISPER_LANGUAGE = req.language.strip().lower()
        if req.elevenlabs_api_key:
            config.ELEVENLABS_API_KEY = req.elevenlabs_api_key.strip()
        config.MIN_VIRAL_SCORE = max(0.0, min(float(req.min_score), 100.0))
        config.MIN_CLIP_DURATION = max(5, int(req.min_duration))
        config.MAX_CLIP_DURATION = max(config.MIN_CLIP_DURATION + 5, int(req.max_duration))
        config.MAX_CLIPS_PER_VIDEO = max(1, min(int(req.num_clips), 20))
        if req.watermark_text:
            config.WATERMARK_TEXT = req.watermark_text

        provider = req.ai_provider or config.AI_PROVIDER
        llm_available = configure_llm_clients(
            provider=provider,
            model=req.ai_model or "",
            groq_api_key=req.groq_api_key or "",
            openai_api_key=req.openai_api_key or "",
            api_key=req.ai_api_key or "",
            base_url=req.ai_base_url or "",
        )
        model = config.LLM_MODEL

        # Stage 1: Download
        _update_job(job_id, status="downloading", progress=5, current_stage="Downloading video", message="yt-dlp fetching video...")

        video_path = download_video(req.url, temp_dir)
        video_info = get_video_info(video_path)

        # Stage 2: Transcription
        _update_job(job_id, progress=20, current_stage="Transcribing with Whisper", message=f"Using {req.whisper_model} model...")
        transcript, transcription_info = transcribe_video(
            video_path,
            model_name=req.whisper_model,
            word_timestamps=True,
            device=req.whisper_device,
            compute_type=req.whisper_compute_type,
        )

        # Stage 3: Viral Analysis (with LLM if enabled)
        llm_msg = (f"{provider}/{model}: AI moment finder + rerank + packaging..."
                   if (req.use_llm and llm_available) else "Rule-based scoring (AI tidak aktif)...")
        _update_job(job_id, progress=45, current_stage="Analyzing viral moments", message=llm_msg)
        clips_data = analyze_viral_potential(
            transcript,
            video_info,
            use_llm=req.use_llm and llm_available
        )
        if req.categories:
            clips_data = [clip for clip in clips_data if clip.get("category") in req.categories]

        if not clips_data:
            raise RuntimeError("No viral clips found with current settings")

        # Stage 4: Clipping & Rendering
        _update_job(job_id, progress=60, current_stage="Rendering clips", message=f"Processing {min(len(clips_data), req.num_clips)} clips...")

        output_dir = Path(config.OUTPUT_DIR) / f"job_{job_id[:8]}"
        output_dir.mkdir(parents=True, exist_ok=True)

        processed = process_clips(
            video_path,
            clips_data[:req.num_clips],
            output_dir,
            style_name=req.caption_style,
            enable_face_reframe=req.enable_face_reframe,
            add_watermark=bool(req.watermark_text),
            target_platform=req.target_platform,
            enable_captions=req.enable_captions,
            edit_opts={
                "style": req.edit_style, "jump_cuts": req.jump_cuts, "dynamic_zoom": req.dynamic_zoom,
                "progress_bar": req.progress_bar, "sfx": req.sfx, "color_grade": req.color_grade,
                "smart_thumbnail": req.smart_thumbnail, "end_cta": req.end_cta,
            },
        )

        # Stage 5: Publish package (opsional)
        publish_folder = None
        if req.publish_package and processed:
            _update_job(job_id, progress=88, current_stage="Building publish package", message="Menyiapkan paket upload per platform...")
            try:
                from core.publisher import build_publish_package
                platforms = [req.target_platform] if req.target_platform != "Semua" else None
                publish_folder = str(build_publish_package(processed, output_dir, platforms=platforms))
            except Exception as exc:
                logger.warning(f"Publish package gagal dibuat: {exc}")

        # Stage 6: Finalize
        _update_job(job_id, progress=95, current_stage="Finalizing", message="Generating metadata...")

        result = {
            "job_id": job_id,
            "source_url": req.url,
            "total_clips": len(processed),
            "clips": processed,
            "transcription": transcription_info,
            "source_resolution": {"width": video_info.get("width"), "height": video_info.get("height")},
            "ai": f"{provider}/{model}" if (req.use_llm and llm_available) else "local-only",
            "output_folder": str(output_dir),
            "publish_folder": publish_folder,
            "metadata_file": str(output_dir / "metadata.json"),
            "completed_at": datetime.now().isoformat()
        }

        _update_job(
            job_id,
            status="done",
            progress=100,
            current_stage="Completed",
            message=f"✅ {len(processed)} viral clips ready!",
            result=result
        )

    except Exception as e:
        _update_job(
            job_id,
            status="error",
            progress=0,
            current_stage="Error",
            message=str(e)
        )
        logger.exception(f"Job {job_id} failed")
    finally:
        if temp_dir is not None:
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)  # localhost saja: tidak ada auth
