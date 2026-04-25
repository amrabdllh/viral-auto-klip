"""
Viral Auto Klip v2 — Real FastAPI Backend
Run with: uvicorn server:app --reload --host 0.0.0.0 --port 8000

This backend handles real video processing:
- yt-dlp download
- Whisper transcription
- Groq LLM viral scoring
- MediaPipe face detection
- FFmpeg clipping + caption burn-in
"""

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Dict
import uuid
import asyncio
import json
from pathlib import Path
import tempfile
import shutil
from datetime import datetime

# Import our core modules
from core.utils import setup_logging, download_video, get_video_info
from core.analyzer import analyze_viral_potential, configure_llm_clients
from core.clipper import process_clips
from core.transcriber import transcribe_video
import config
from groq import Groq
import os

app = FastAPI(title="Viral Auto Klip API", version="2.0")

# CORS for local HTML UI
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production restrict to your domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

setup_logging()

# In-memory job store (use Redis/Postgres in production)
jobs: Dict[str, Dict] = {}

class GroqVerifyRequest(BaseModel):
    api_key: str

@app.post("/verify-groq")
async def verify_groq_key(request: GroqVerifyRequest):
    try:
        client = Groq(api_key=request.api_key)
        # Test dengan list models (ringan)
        models = client.models.list()
        return {"valid": True, "message": "Groq API Key valid"}
    except Exception as e:
        return {"valid": False, "message": str(e)}

class ProcessRequest(BaseModel):
    url: str
    num_clips: int = 8
    min_score: float = 60
    min_duration: int = 15
    max_duration: int = 60
    caption_style: str = "modern"
    enable_face_reframe: bool = True
    use_llm: bool = True
    whisper_model: str = "medium"
    whisper_device: str = "cuda"
    whisper_compute_type: str = "float16"
    categories: Optional[List[str]] = None
    watermark_text: Optional[str] = None
    target_platform: str = "Semua"
    groq_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None

class JobStatus(BaseModel):
    job_id: str
    status: str  # queued, downloading, transcribing, analyzing, rendering, done, error
    progress: int
    current_stage: str
    message: str
    result: Optional[Dict] = None

@app.post("/process-video", response_model=JobStatus)
async def start_processing(req: ProcessRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    
    jobs[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "progress": 0,
        "current_stage": "Initializing",
        "message": "Job queued",
        "request": req.dict(),
        "result": None,
        "created_at": datetime.now().isoformat()
    }
    
    background_tasks.add_task(process_video_job, job_id, req)
    return JobStatus(**jobs[job_id])

@app.get("/status/{job_id}", response_model=JobStatus)
async def get_status(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404, "Job not found")
    return JobStatus(**jobs[job_id])

@app.get("/results/{job_id}")
async def get_results(job_id: str):
    if job_id not in jobs or jobs[job_id]["status"] != "done":
        raise HTTPException(404, "Results not ready")
    return jobs[job_id]["result"]

async def process_video_job(job_id: str, req: ProcessRequest):
    """Real video processing pipeline"""
    try:
        job = jobs[job_id]
        temp_dir = Path(tempfile.mkdtemp())
        config.MIN_VIRAL_SCORE = req.min_score
        config.MIN_CLIP_DURATION = req.min_duration
        config.MAX_CLIP_DURATION = req.max_duration
        config.MAX_CLIPS_PER_VIDEO = req.num_clips
        if req.watermark_text:
            config.WATERMARK_TEXT = req.watermark_text
        llm_available = configure_llm_clients(req.groq_api_key or "", req.openai_api_key or "")
        
        # Stage 1: Download
        job.update({"status": "downloading", "progress": 5, "current_stage": "Downloading video", "message": "yt-dlp fetching video..."})
        await asyncio.sleep(0.1)
        
        video_path = download_video(req.url, temp_dir)
        video_info = get_video_info(video_path)
        
        # Stage 2: Transcription
        job.update({"progress": 20, "current_stage": "Transcribing with Whisper", "message": f"Using {req.whisper_model} model..."})
        transcript, transcription_info = transcribe_video(
            video_path,
            model_name=req.whisper_model,
            word_timestamps=True,
            device=req.whisper_device,
            compute_type=req.whisper_compute_type,
        )
        
        # Stage 3: Viral Analysis (with Groq if enabled)
        job.update({"progress": 45, "current_stage": "Analyzing viral moments", "message": "Groq LLM + rule-based scoring..."})
        clips_data = analyze_viral_potential(
            transcript, 
            video_info, 
            use_llm=req.use_llm and llm_available
        )
        if req.categories:
            clips_data = [clip for clip in clips_data if clip.get("category") in req.categories]
        
        if not clips_data:
            raise Exception("No viral clips found with current settings")
        
        # Stage 4: Clipping & Rendering
        job.update({"progress": 60, "current_stage": "Rendering clips", "message": f"Processing {min(len(clips_data), req.num_clips)} clips..."})
        
        output_dir = Path(config.OUTPUT_DIR) / f"job_{job_id[:8]}"
        output_dir.mkdir(parents=True, exist_ok=True)
        
        processed = process_clips(
            video_path,
            clips_data[:req.num_clips],
            output_dir,
            style_name=req.caption_style,
            enable_face_reframe=req.enable_face_reframe,
            add_watermark=bool(req.watermark_text),
            target_platform=req.target_platform
        )
        
        # Stage 5: Finalize
        job.update({"progress": 95, "current_stage": "Finalizing", "message": "Generating metadata..."})
        
        result = {
            "job_id": job_id,
            "source_url": req.url,
            "total_clips": len(processed),
            "clips": processed,
            "transcription": transcription_info,
            "output_folder": str(output_dir),
            "metadata_file": str(output_dir / "metadata.json"),
            "completed_at": datetime.now().isoformat()
        }
        
        job.update({
            "status": "done",
            "progress": 100,
            "current_stage": "Completed",
            "message": f"✅ {len(processed)} viral clips ready!",
            "result": result
        })
        
        # Cleanup temp files
        shutil.rmtree(temp_dir, ignore_errors=True)
        
    except Exception as e:
        jobs[job_id].update({
            "status": "error",
            "progress": 0,
            "current_stage": "Error",
            "message": str(e)
        })
        print(f"Job {job_id} failed: {e}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
