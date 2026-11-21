"""
Robust Whisper transcription helpers.
"""
import gc
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Tuple


def _register_nvidia_dlls() -> None:
    """Windows: DLL CUDA dari wheel pip (nvidia-cublas-cu12, nvidia-cudnn-cu12) tidak otomatis
    ditemukan CTranslate2 — tanpa ini Whisper gagal 'cublas64_12.dll not found' lalu jatuh ke CPU."""
    if os.name != "nt":
        return
    import importlib.util
    spec = importlib.util.find_spec("nvidia")
    for base in (list(spec.submodule_search_locations) if spec and spec.submodule_search_locations else []):
        for bin_dir in Path(base).glob("*/bin"):
            if any(bin_dir.glob("*.dll")):
                os.add_dll_directory(str(bin_dir))
                # PATH juga diperbarui agar worker subprocess ikut mewarisi
                if str(bin_dir) not in os.environ.get("PATH", ""):
                    os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")


_register_nvidia_dlls()

from faster_whisper import WhisperModel  # noqa: E402
from groq import Groq
from loguru import logger

import config


CUDA_DLL_ERROR_HINTS = (
    "cublas64",
    "cudnn",
    "could not load library",
    "cublas64_",
    "cudnn64_",
)

MODEL_FALLBACKS = {
    "large-v3-turbo": ["medium", "small", "base"],
    "large-v3": ["medium", "small", "base"],
    "large": ["medium", "small", "base"],
    "medium": ["small", "base"],
    "small": ["base"],
    "base": ["tiny"],
}


MAX_WORD_SECONDS = 1.0  # kata yang "ditahan" lebih lama dari ini membuat subtitle terlihat nyangkut


def _sanitize_words(transcript: List[Dict]) -> List[Dict]:
    """Rapikan timestamp per kata: urut, tidak tumpang-tindih, dan tidak ada kata yang tampil terlalu lama."""
    fixed = 0
    for seg in transcript:
        words = seg.get("words") or []
        for i, w in enumerate(words):
            start, end = float(w.get("start") or 0), float(w.get("end") or 0)
            if end - start > MAX_WORD_SECONDS:
                end = start + MAX_WORD_SECONDS
                fixed += 1
            nxt = words[i + 1]["start"] if i + 1 < len(words) else None
            if nxt is not None and end > nxt:
                end = max(start + 0.02, float(nxt))
            w["start"], w["end"] = round(start, 3), round(max(end, start + 0.02), 3)
    if fixed:
        logger.info(f"Timestamp {fixed} kata dipendekkan (maks {MAX_WORD_SECONDS}s) agar subtitle tidak nyangkut")
    return transcript


def transcribe_video(video_path: str, model_name: str = None, word_timestamps: bool = False,
                     device: str = None, compute_type: str = None) -> Tuple[List[Dict], Dict]:
    """Transkripsi dengan fallback: cloud (ElevenLabs/Groq) gagal → Whisper lokal (GPU → CPU)."""
    preferred = (device or config.WHISPER_DEVICE or "auto").lower()
    if preferred in ("elevenlabs", "scribe", "elevenlabs-scribe", "groq", "cloud", "groq-whisper"):
        try:
            transcript, info = _transcribe_video_impl(video_path, model_name, word_timestamps, preferred, compute_type)
            return _sanitize_words(transcript), info
        except Exception as exc:
            logger.warning(f"Transkripsi {preferred} gagal ({str(exc)[:200]}) — pindah ke Whisper lokal")
            preferred, compute_type = "auto", (config.WHISPER_COMPUTE_TYPE if compute_type == "cloud" else compute_type)
    transcript, info = _transcribe_video_impl(video_path, model_name, word_timestamps, preferred, compute_type)
    return _sanitize_words(transcript), info


def _transcribe_video_impl(
    video_path: str,
    model_name: str = None,
    word_timestamps: bool = False,
    device: str = None,
    compute_type: str = None,
) -> Tuple[List[Dict], Dict]:
    """
    Transcribe with faster-whisper using explicit, recoverable device choices.

    Passing device="auto" directly to faster-whisper can hide whether CUDA or CPU
    was selected. This function resolves auto mode first, tries CUDA when it is
    detected, then falls back to CPU and smaller models when needed.
    """
    if not Path(video_path).exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    model_name = _safe_model_for_gpu(model_name or config.WHISPER_MODEL, device or config.WHISPER_DEVICE)
    preferred_device = (device or config.WHISPER_DEVICE or "cpu").lower()
    preferred_compute = (compute_type or config.WHISPER_COMPUTE_TYPE or "int8").lower()
    if preferred_device in ("groq", "cloud", "groq-whisper"):
        return _transcribe_with_groq(video_path, word_timestamps=word_timestamps)
    if preferred_device in ("elevenlabs", "scribe", "elevenlabs-scribe"):
        return _transcribe_with_elevenlabs(video_path)

    plan = _build_transcription_plan(model_name, preferred_device, preferred_compute)
    last_error = None

    for plan_index, (attempt_model, attempt_device, attempt_compute) in enumerate(plan, start=1):
        model = None
        try:
            if attempt_device == "cuda":
                result = _transcribe_in_subprocess(
                    video_path,
                    attempt_model,
                    word_timestamps,
                    attempt_device,
                    attempt_compute,
                )
                if result is not None:
                    transcript, info_dict = result
                    if attempt_model != model_name:
                        logger.warning(f"Model fallback used: {attempt_model}.")
                    return transcript, info_dict
                last_error = RuntimeError("CUDA subprocess failed or crashed")
                logger.warning(
                    f"CUDA transcription failed/crashed for {attempt_model}/{attempt_compute}. Trying next option..."
                )
                continue

            device_index = config.WHISPER_CUDA_DEVICE_INDEX if attempt_device == "cuda" else 0
            logger.info(
                f"Loading Whisper model={attempt_model}, device={attempt_device}, "
                f"compute={attempt_compute}, cuda_device_index={device_index if attempt_device == 'cuda' else '-'}"
            )
            model_kwargs = {"device": attempt_device, "compute_type": attempt_compute}
            if attempt_device == "cuda":
                model_kwargs["device_index"] = device_index
            model = WhisperModel(attempt_model, **model_kwargs)
            logger.info("Whisper model loaded. Starting transcription...")
            segments, info = _run_transcribe(model, video_path, word_timestamps, attempt_device)
            transcript = []
            for segment in segments:
                item = {"start": segment.start, "end": segment.end, "text": segment.text}
                words = getattr(segment, "words", None)
                if words:
                    item["words"] = [
                        {
                            "start": word.start,
                            "end": word.end,
                            "word": getattr(word, "word", "").strip(),
                        }
                        for word in words
                        if getattr(word, "word", "").strip()
                    ]
                transcript.append(item)
            info_dict = {
                "language": getattr(info, "language", None),
                "language_probability": getattr(info, "language_probability", None),
                "duration": getattr(info, "duration", None),
                "device": attempt_device,
                "compute_type": attempt_compute,
                "model": attempt_model,
            }
            if attempt_device == "cpu" and preferred_device != "cpu":
                logger.warning("All CUDA Whisper attempts failed. Transcription continued on CPU int8.")
            if attempt_model != model_name:
                logger.warning(f"Model {model_name} failed. Transcription continued with {attempt_model}.")
            return transcript, info_dict
        except Exception as exc:
            last_error = exc
            message = str(exc)
            if plan_index < len(plan):
                logger.warning(
                    f"Whisper failed for {attempt_model}/{attempt_device}/{attempt_compute}: "
                    f"{message[:160]}. Trying next option..."
                )
                continue
            if config.GROQ_WHISPER_FALLBACK and config.GROQ_API_KEY:
                logger.warning(
                    "All local Whisper attempts failed. Falling back to Groq Whisper cloud transcription."
                )
                return _transcribe_with_groq(video_path, word_timestamps=word_timestamps)
            if config.ELEVENLABS_API_KEY:
                logger.warning("All local Whisper attempts failed. Falling back to ElevenLabs Scribe.")
                return _transcribe_with_elevenlabs(video_path)
            raise
        finally:
            del model
            gc.collect()

    raise RuntimeError(f"Whisper transcription failed after all attempts (last error: {last_error})")


def _transcribe_with_groq(video_path: str, word_timestamps: bool = False) -> Tuple[List[Dict], Dict]:
    if not config.GROQ_API_KEY:
        raise RuntimeError("Groq Whisper selected, but GROQ_API_KEY is empty.")

    audio_path = _extract_cloud_audio(video_path, config.GROQ_WHISPER_AUDIO_BITRATE, "groq", limit_mb=95)
    try:
        logger.info(f"Transcribing with Groq Whisper model={config.GROQ_WHISPER_MODEL}")
        client = Groq(api_key=config.GROQ_API_KEY)
        extra = {"language": config.WHISPER_LANGUAGE} if config.WHISPER_LANGUAGE else {}
        with open(audio_path, "rb") as audio_file:
            response = client.audio.transcriptions.create(
                file=(Path(audio_path).name, audio_file.read()),
                model=config.GROQ_WHISPER_MODEL,
                response_format="verbose_json",
                timestamp_granularities=["segment", "word"] if word_timestamps else ["segment"],
                temperature=0.0,
                **extra,
            )
        payload = _response_to_dict(response)
        transcript = _groq_payload_to_segments(payload)
        info = {
            "language": payload.get("language"),
            "language_probability": None,
            "duration": payload.get("duration"),
            "device": "groq",
            "compute_type": "cloud",
            "model": config.GROQ_WHISPER_MODEL,
        }
        logger.info(f"Groq Whisper transcription complete: {len(transcript)} segments")
        return transcript, info
    finally:
        Path(audio_path).unlink(missing_ok=True)


def _extract_cloud_audio(video_path: str, bitrate: str, label: str, limit_mb: float = 0) -> str:
    audio_path = str(Path(tempfile.gettempdir()) / f"{label}_stt_{os.getpid()}_{Path(video_path).stem}.mp3")
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vn",
        "-ac", "1",
        "-ar", "16000",
        "-b:a", bitrate,
        audio_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    size_mb = Path(audio_path).stat().st_size / (1024 * 1024)
    if limit_mb and size_mb > limit_mb:
        logger.warning(
            f"{label} audio file is {size_mb:.1f}MB, near the API upload limit. "
            "Consider shorter videos or a lower audio bitrate."
        )
    return audio_path


def _transcribe_with_elevenlabs(video_path: str) -> Tuple[List[Dict], Dict]:
    """Transkripsi cloud via ElevenLabs Scribe (timestamp per kata, akurasi tinggi)."""
    if not config.ELEVENLABS_API_KEY:
        raise RuntimeError("ElevenLabs Scribe dipilih, tapi ELEVENLABS_API_KEY kosong.")
    import requests

    audio_path = _extract_cloud_audio(video_path, config.ELEVENLABS_AUDIO_BITRATE, "elevenlabs")
    try:
        logger.info(f"Transcribing with ElevenLabs Scribe model={config.ELEVENLABS_STT_MODEL}")
        data = {
            "model_id": config.ELEVENLABS_STT_MODEL,
            "timestamps_granularity": "word",
            # v6.4: label pembicara per kata (kamera ikut pembicara) + event suara (tawa → efek reaksi)
            "diarize": "true",
            "tag_audio_events": "true",
        }
        if config.WHISPER_LANGUAGE:
            data["language_code"] = config.WHISPER_LANGUAGE
        with open(audio_path, "rb") as audio_file:
            response = requests.post(
                "https://api.elevenlabs.io/v1/speech-to-text",
                headers={"xi-api-key": config.ELEVENLABS_API_KEY},
                data=data,
                files={"file": (Path(audio_path).name, audio_file, "audio/mpeg")},
                timeout=1800,
            )
        if response.status_code >= 400:
            raise RuntimeError(f"ElevenLabs STT error {response.status_code}: {response.text[:300]}")
        payload = response.json()
        transcript = _elevenlabs_payload_to_segments(payload)
        info = {
            "language": payload.get("language_code"),
            "language_probability": payload.get("language_probability"),
            "duration": transcript[-1]["end"] if transcript else None,
            "device": "elevenlabs",
            "compute_type": "cloud",
            "model": config.ELEVENLABS_STT_MODEL,
        }
        logger.info(f"ElevenLabs transcription complete: {len(transcript)} segments")
        return transcript, info
    finally:
        Path(audio_path).unlink(missing_ok=True)


def _elevenlabs_payload_to_segments(payload: Dict, max_words: int = 22, max_gap: float = 0.9) -> List[Dict]:
    """Kelompokkan kata ElevenLabs menjadi segmen kalimat (akhir kalimat / jeda / panjang maks)."""
    words = [
        {"start": float(w.get("start") or 0), "end": float(w.get("end") or w.get("start") or 0),
         "word": str(w.get("text") or "").strip(), **({"speaker": str(w["speaker_id"])} if w.get("speaker_id") else {})}
        for w in payload.get("words") or []
        if (w.get("type") in (None, "word")) and str(w.get("text") or "").strip()
    ]
    events = [
        {"start": float(w.get("start") or 0), "end": float(w.get("end") or w.get("start") or 0),
         "text": str(w.get("text") or "").strip("() ").lower()}
        for w in payload.get("words") or [] if w.get("type") == "audio_event"
    ]
    if not words:
        text = str(payload.get("text", "")).strip()
        return [{"start": 0.0, "end": 0.1, "text": text}] if text else []
    segments, current = [], []
    for word in words:
        if current and (word["start"] - current[-1]["end"] > max_gap or len(current) >= max_words):
            segments.append(current)
            current = []
        current.append(word)
        if word["word"].endswith((".", "!", "?")) and len(current) >= 4:
            segments.append(current)
            current = []
    if current:
        segments.append(current)
    out = [
        {"start": seg[0]["start"], "end": max(seg[-1]["end"], seg[0]["start"] + 0.1),
         "text": " ".join(w["word"] for w in seg), "words": seg}
        for seg in segments
    ]
    # event suara (laughter, applause, …) disimpan di segmen terdekat — tidak masuk teks/caption
    for ev in events:
        seg = min(out, key=lambda sg: 0 if sg["start"] <= ev["start"] <= sg["end"] else min(abs(sg["start"] - ev["start"]), abs(sg["end"] - ev["start"])))
        seg.setdefault("audio_events", []).append(ev)
    return out


def _response_to_dict(response) -> Dict:
    if isinstance(response, dict):
        return response
    if hasattr(response, "model_dump"):
        return response.model_dump()
    if hasattr(response, "dict"):
        return response.dict()
    return json.loads(json.dumps(response, default=lambda item: getattr(item, "__dict__", str(item))))


def _groq_payload_to_segments(payload: Dict) -> List[Dict]:
    segments = payload.get("segments") or []
    words = payload.get("words") or []
    if not segments:
        text = str(payload.get("text", "")).strip()
        duration = float(payload.get("duration") or 0)
        return [{"start": 0.0, "end": max(duration, 0.1), "text": text}] if text else []

    transcript = []
    for segment in segments:
        start = float(segment.get("start") or 0)
        end = float(segment.get("end") or start)
        segment_words = [
            {
                "start": float(word.get("start") or start),
                "end": float(word.get("end") or end),
                "word": str(word.get("word") or "").strip(),
            }
            for word in words
            if str(word.get("word") or "").strip()
            and float(word.get("end") or 0) > start
            and float(word.get("start") or 0) < end
        ]
        item = {
            "start": start,
            "end": end,
            "text": str(segment.get("text") or "").strip(),
        }
        if segment_words:
            item["words"] = segment_words
        transcript.append(item)
    return transcript


def _transcribe_in_subprocess(
    video_path: str,
    model_name: str,
    word_timestamps: bool,
    device: str,
    compute_type: str,
):
    """Run CUDA Whisper in a child process so Streamlit survives native CUDA crashes."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        result_path = handle.name
    cmd = [
        sys.executable,
        "-m",
        "core.transcriber",
        "--video",
        video_path,
        "--model",
        model_name,
        "--device",
        device,
        "--compute",
        compute_type,
        "--result",
        result_path,
    ]
    if word_timestamps:
        cmd.append("--word-timestamps")
    try:
        completed = subprocess.run(
            cmd,
            cwd=str(config.BASE_DIR),
            capture_output=True,
            text=True,
            timeout=None,
        )
        if completed.returncode != 0:
            logger.warning(f"Whisper CUDA worker exited {completed.returncode}: {completed.stderr[-800:]}")
            return None
        with open(result_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        return payload["transcript"], payload["info"]
    except Exception as exc:
        logger.warning(f"Whisper CUDA worker failed: {exc}")
        return None
    finally:
        try:
            Path(result_path).unlink(missing_ok=True)
        except Exception:
            pass


def drop_foreign_segments(segments: List[Dict], language: str = "id") -> List[Dict]:
    """Buang segmen yang jelas bukan bahasa siaran (mis. dialog game berbahasa Inggris).

    Audio siaran adalah satu campuran mikrofon streamer + suara game, jadi Whisper
    ikut menuliskan dialog game. Tanpa diarisasi, cara paling andal yang tersedia
    adalah membuang segmen yang kosakatanya didominasi kata fungsi bahasa lain.
    Kalimat streamer (Indonesia) tetap lolos; dialog game Inggris dibuang.
    """
    lang = str(language or "").split("-")[0].lower()
    if lang not in ("id", "ms"):
        return segments
    id_words = {
        "aku", "kamu", "yang", "dan", "di", "ke", "dari", "ini", "itu", "tidak", "nggak", "gak", "ga",
        "apa", "saya", "kita", "dia", "untuk", "dengan", "bisa", "sudah", "mau", "aja", "sih",
        "dong", "ya", "kan", "kok", "banget", "jadi", "kalau", "tapi", "lagi", "juga", "sama", "ada",
        "buat", "nih", "deh", "lah", "pakai", "pake", "terus", "gimana", "kenapa", "bener", "beneran",
        "anjir", "wkwk", "wkwkwk", "nya", "punya", "orang", "banyak", "masih", "belum", "harus",
    }
    en_words = {
        "the", "you", "i", "to", "and", "is", "it", "that", "what", "we", "they", "he", "she", "my",
        "your", "have", "can", "not", "of", "in", "on", "with", "for", "this", "was", "are", "be",
        "do", "don't", "it's", "i'm", "we're", "you're", "got", "just", "like", "know", "here",
        "there", "all", "so", "no", "yes", "come", "go", "look", "get", "them", "our", "his", "her",
        "will", "would", "could", "should", "there's", "let's", "out", "up", "down", "at", "by",
    }
    kept = []
    for seg in segments:
        words = re.findall(r"[a-z']+", str(seg.get("text") or "").lower())
        en = sum(1 for w in words if w in en_words)
        idn = sum(1 for w in words if w in id_words)
        if len(words) >= 4 and en >= 2 and en > idn:
            continue
        kept.append(seg)
    return kept


def _transcribe_direct_worker(video_path, model_name, word_timestamps, device, compute_type):
    device_index = config.WHISPER_CUDA_DEVICE_INDEX if device == "cuda" else 0
    model_kwargs = {"device": device, "compute_type": compute_type}
    if device == "cuda":
        model_kwargs["device_index"] = device_index
    model = WhisperModel(model_name, **model_kwargs)
    segments, info = _run_transcribe(model, video_path, word_timestamps, device)
    transcript = []
    for segment in segments:
        item = {"start": segment.start, "end": segment.end, "text": segment.text}
        words = getattr(segment, "words", None)
        if words:
            item["words"] = [
                {
                    "start": word.start,
                    "end": word.end,
                    "word": getattr(word, "word", "").strip(),
                }
                for word in words
                if getattr(word, "word", "").strip()
            ]
        transcript.append(item)
    info_dict = {
        "language": getattr(info, "language", None),
        "language_probability": getattr(info, "language_probability", None),
        "duration": getattr(info, "duration", None),
        "device": device,
        "compute_type": compute_type,
        "model": model_name,
    }
    return transcript, info_dict


def _safe_model_for_gpu(model_name: str, device: str) -> str:
    if model_name not in ("large-v3", "large"):
        return model_name
    if config.WHISPER_ALLOW_LARGE_ON_GPU:
        logger.warning(
            f"Whisper {model_name} on GPU is enabled. If CUDA crashes, the app will retry fallback models."
        )
        return model_name
    if (device or "").lower() == "cpu":
        return model_name
    vram_gb = _cuda_vram_gb()
    if vram_gb and vram_gb < config.WHISPER_GPU_SAFE_VRAM_GB:
        logger.warning(
            f"GPU VRAM {vram_gb:.1f}GB is risky for Whisper {model_name}. "
            "Using medium first to avoid native CUDA crashes."
        )
        return "medium"
    return model_name


def _cuda_vram_gb() -> float:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        first_value = result.stdout.strip().splitlines()[config.WHISPER_CUDA_DEVICE_INDEX]
        return float(first_value) / 1024
    except Exception:
        return 0.0


def _model_attempts(model_name: str) -> List[str]:
    attempts = [model_name]
    for fallback in MODEL_FALLBACKS.get(model_name, []):
        if fallback not in attempts:
            attempts.append(fallback)
    return attempts


def _build_transcription_plan(model_name: str, device: str, compute_type: str) -> List[Tuple[str, str, str]]:
    """Prioritize every GPU model attempt before any CPU fallback."""
    models = _model_attempts(model_name)
    device_attempts = _build_attempts(device, compute_type)
    gpu_computes = [compute for attempt_device, compute in device_attempts if attempt_device == "cuda"]
    cpu_computes = [compute for attempt_device, compute in device_attempts if attempt_device == "cpu"]
    plan: List[Tuple[str, str, str]] = []

    for attempt_model in models:
        for attempt_compute in gpu_computes:
            attempt = (attempt_model, "cuda", attempt_compute)
            if attempt not in plan:
                plan.append(attempt)

    for attempt_model in models:
        for attempt_compute in cpu_computes:
            attempt = (attempt_model, "cpu", attempt_compute)
            if attempt not in plan:
                plan.append(attempt)

    if not plan:
        plan.append((model_name, "cpu", "int8"))
    logger.info(
        "Whisper attempt plan: "
        + " -> ".join(f"{model}/{device_name}/{compute}" for model, device_name, compute in plan)
    )
    return plan


def _build_attempts(device: str, compute_type: str) -> List[Tuple[str, str]]:
    device = (device or "cpu").lower()
    compute_type = (compute_type or "int8").lower()
    attempts: List[Tuple[str, str]] = []

    def add(attempt_device: str, attempt_compute: str) -> None:
        if attempt_device == "cpu" and attempt_compute in ("float16", "float32"):
            attempt_compute = "int8"
        attempt = (attempt_device, attempt_compute)
        if attempt not in attempts:
            attempts.append(attempt)

    if device == "cuda":
        add("cuda", compute_type)
        add("cuda", "int8")
        add("cpu", "int8")
        return attempts

    if device == "auto":
        if _cuda_runtime_available():
            logger.info("CUDA detected: Whisper auto mode will try GPU first, then CPU fallback.")
            add("cuda", compute_type)
            add("cuda", "int8")
        elif platform.system().lower() == "windows":
            logger.info("CUDA not detected by CTranslate2 on Windows. Whisper auto mode will use CPU.")
        add("cpu", "int8")
        return attempts

    add("cpu", "int8")
    return attempts


def _cuda_runtime_available() -> bool:
    if os.getenv("FORCE_WHISPER_CUDA", "").lower() in ("1", "true", "yes"):
        return True
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception as exc:
        logger.warning(f"CUDA check failed, using CPU: {str(exc)[:120]}")
        return False


def _transcribe_kwargs(word_timestamps: bool) -> Dict:
    """Bangun argumen transcribe dengan tuning dari config (beam, bahasa, initial prompt)."""
    kwargs = {
        "beam_size": config.WHISPER_BEAM_SIZE,
        "word_timestamps": word_timestamps,
        "vad_filter": True,
    }
    if config.WHISPER_LANGUAGE:
        kwargs["language"] = config.WHISPER_LANGUAGE
    # FIX: prompt berbahasa Indonesia hanya dipakai untuk video yang memang dinyatakan Indonesia.
    if config.WHISPER_INITIAL_PROMPT and config.WHISPER_LANGUAGE == "id":
        kwargs["initial_prompt"] = config.WHISPER_INITIAL_PROMPT
    return kwargs


def _run_transcribe(model, video_path: str, word_timestamps: bool, device: str):
    """Jalankan transkripsi.

    FIX: `batch_size` TIDAK diterima oleh WhisperModel.transcribe (TypeError) —
    sebelumnya membuat SEMUA percobaan CUDA gagal lalu jatuh ke CPU. Batch hanya
    didukung lewat BatchedInferencePipeline, jadi dipakai di sana (CUDA saja).
    """
    kwargs = _transcribe_kwargs(word_timestamps)
    # FIX v4: faster-whisper mendekode audio lewat PyAV dengan argumen `metadata_errors`
    # yang sudah DIHAPUS di PyAV 19 (satu-satunya versi untuk Python 3.14) -> TypeError dan
    # transkripsi lokal selalu gagal. Audio sekarang didekode dengan FFmpeg menjadi array numpy.
    audio = _load_audio_ffmpeg(video_path)
    if (device or "").lower() == "cuda" and config.WHISPER_BATCH_SIZE > 0:
        try:
            from faster_whisper import BatchedInferencePipeline
            return BatchedInferencePipeline(model).transcribe(
                audio, batch_size=config.WHISPER_BATCH_SIZE, **kwargs
            )
        except ImportError:
            logger.warning("BatchedInferencePipeline tidak tersedia (faster-whisper lama). Pakai mode biasa.")
    return model.transcribe(audio, **kwargs)


def _load_audio_ffmpeg(path: str, sample_rate: int = 16000):
    """Dekode audio apa pun ke float32 mono 16 kHz (format input Whisper) via FFmpeg."""
    import numpy as np

    cmd = [
        "ffmpeg", "-nostdin", "-v", "error", "-i", str(path),
        "-vn", "-ac", "1", "-ar", str(sample_rate), "-f", "f32le", "-acodec", "pcm_f32le", "-",
    ]
    completed = subprocess.run(cmd, capture_output=True, check=False)
    if completed.returncode != 0 or not completed.stdout:
        raise RuntimeError(f"FFmpeg gagal mendekode audio: {completed.stderr.decode(errors='replace')[-300:]}")
    return np.frombuffer(completed.stdout, dtype=np.float32).copy()


def _worker_main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--compute", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--word-timestamps", action="store_true")
    args = parser.parse_args()

    transcript, info = _transcribe_direct_worker(
        args.video,
        args.model,
        args.word_timestamps,
        args.device,
        args.compute,
    )
    with open(args.result, "w", encoding="utf-8") as f:
        json.dump({"transcript": transcript, "info": info}, f, ensure_ascii=False)


if __name__ == "__main__":
    _worker_main()
