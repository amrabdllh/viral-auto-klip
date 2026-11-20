"""Video AI generatif (v6.8): gambar → video dan teks → video untuk Content Creator / Story Studio / Motion Graphic.

Tanpa modul ini adegan hanya "gambar diam + gerak kamera". Dengan modul ini adegan penting dihidupkan sungguhan.

Provider (spesifikasi dicek dari dokumentasi resmi, November 2026):
- **Gemini Omni Flash** (`gemini-omni-1.2-flash`, Google) — pengganti Veo 3.1 Preview yang dimatikan 22 Okt 2026.
  `POST /v1beta/interactions`, gambar sebagai bingkai pertama di `input`, `response_format` {type: video,
  aspect_ratio 9:16|16:9, resolution 360p–4k, duration "3s"–"15s"}. Sinkron; hasil base64 (atau URI Files API).
  Memakai GEMINI_API_KEY yang sudah ada.
- **Kling 3.0 Turbo** (Kuaishou) — API standar baru (API Key, `Authorization: Bearer`), `POST /image-to-video/{model}`
  {contents: [prompt, first_frame], settings: {resolution, duration 3–15}} atau `POST /text-to-video/{model}`
  {prompt, settings: {aspect_ratio, duration, resolution}}; status lewat `GET /tasks?task_ids=`.

Biaya dibayar per detik video di akun provider masing-masing; pemakaian dicatat lokal (`usage()`).
Setiap kegagalan dilempar sebagai VideoAIError → pemanggil kembali ke gerak kamera (gratis).
"""
import base64
import time
from pathlib import Path
from typing import Dict, Optional

import requests
from loguru import logger

import config
from . import db

PROVIDERS = {
    "gemini": {"label": "Gemini Omni Flash (Google)", "key": "GEMINI_API_KEY"},
    "kling": {"label": "Kling 3.0 Turbo", "key": "KLING_API_KEY"},
}
_GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta"


class VideoAIError(RuntimeError):
    pass


def _key(name: str) -> str:
    return str(getattr(config, name, "") or "").strip()


def available(provider: str) -> bool:
    info = PROVIDERS.get(provider)
    return bool(info and _key(info["key"]))


def status() -> Dict:
    return {p: {"label": i["label"], "ready": available(p)} for p, i in PROVIDERS.items()}


def usage() -> Dict:
    return db.kv_get("video_ai_usage", {}) or {}


def _record(provider: str, seconds: float) -> None:
    u = usage()
    row = u.get(provider) or {"videos": 0, "seconds": 0.0}
    row["videos"] += 1
    row["seconds"] = round(row["seconds"] + seconds, 1)
    u[provider] = row
    db.kv_set("video_ai_usage", u)


def _b64(path: str) -> str:
    return base64.b64encode(Path(path).read_bytes()).decode()


def _mime(path: str) -> str:
    return "image/png" if str(path).lower().endswith(".png") else "image/jpeg"


# ----------------------------------------------------------------------------- Gemini Omni Flash

def _gemini(prompt: str, out: str, image: Optional[str], duration: int, aspect: str, resolution: str) -> Dict:
    key = _key("GEMINI_API_KEY")
    model = getattr(config, "GEMINI_VIDEO_MODEL", "") or "gemini-omni-1.2-flash"
    if image:
        inp = [{"type": "image", "data": _b64(image), "mime_type": _mime(image)},
               {"type": "text", "text": f"Use this image as the first frame. {prompt}"}]
    else:
        inp = prompt
    body = {"model": model, "input": inp,
            "response_format": {"type": "video", "aspect_ratio": aspect, "resolution": resolution, "duration": f"{duration}s"}}
    resp = requests.post(f"{_GEMINI_URL}/interactions", json=body, timeout=900,
                         headers={"x-goog-api-key": key, "Content-Type": "application/json"})
    if resp.status_code >= 400:
        raise VideoAIError(f"Gemini {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    video = None
    for step in data.get("steps") or []:
        if step.get("type") == "model_output":
            video = next((c for c in step.get("content") or [] if c.get("type") == "video"), None) or video
    if not video:
        raise VideoAIError(f"Gemini tidak mengembalikan video (status {data.get('status')}): {str(data)[:300]}")
    if video.get("data"):
        Path(out).write_bytes(base64.b64decode(video["data"]))
    elif video.get("uri"):
        file_id = video["uri"].rstrip("/").split("/")[-1].split(":")[0]
        for _ in range(120):  # tunggu file ACTIVE
            st = requests.get(f"{_GEMINI_URL}/files/{file_id}", headers={"x-goog-api-key": key}, timeout=30).json()
            if st.get("state") == "ACTIVE":
                break
            if st.get("state") == "FAILED":
                raise VideoAIError("Gemini: pembuatan video gagal")
            time.sleep(5)
        dl = requests.get(f"{_GEMINI_URL}/files/{file_id}:download", params={"alt": "media"},
                          headers={"x-goog-api-key": key}, timeout=300)
        dl.raise_for_status()
        Path(out).write_bytes(dl.content)
    else:
        raise VideoAIError("Gemini: video tanpa data/uri")
    return {"model": model}


# ----------------------------------------------------------------------------- Kling

def _kling(prompt: str, out: str, image: Optional[str], duration: int, aspect: str, resolution: str) -> Dict:
    key = _key("KLING_API_KEY")
    base = (getattr(config, "KLING_BASE_URL", "") or "https://api-singapore.klingai.com").rstrip("/")
    model = getattr(config, "KLING_VIDEO_MODEL", "") or "kling-3.0-turbo"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    res = resolution if resolution in ("720p", "1080p") else "720p"
    if image:
        url = f"{base}/image-to-video/{model}"
        body = {"contents": [{"type": "prompt", "text": prompt[:2500]}, {"type": "first_frame", "url": _b64(image)}],
                "settings": {"resolution": res, "duration": int(duration)}}
    else:
        url = f"{base}/text-to-video/{model}"
        body = {"prompt": prompt[:2500], "settings": {"aspect_ratio": aspect, "duration": int(duration), "resolution": res}}
    resp = requests.post(url, json=body, headers=headers, timeout=120)
    if resp.status_code >= 400:
        raise VideoAIError(f"Kling {resp.status_code}: {resp.text[:300]}")
    task_id = ((resp.json() or {}).get("data") or {}).get("id")
    if not task_id:
        raise VideoAIError(f"Kling: tidak ada task id: {resp.text[:300]}")
    deadline = time.time() + 15 * 60
    while time.time() < deadline:
        time.sleep(5)
        q = requests.get(f"{base}/tasks", params={"task_ids": task_id}, headers=headers, timeout=60)
        if q.status_code >= 400:
            raise VideoAIError(f"Kling status {q.status_code}: {q.text[:200]}")
        rows = (q.json() or {}).get("data") or []
        row = rows[0] if isinstance(rows, list) and rows else {}
        st = str(row.get("status", "")).lower()
        if st in ("succeeded", "succeed"):
            vid = next((o for o in row.get("outputs") or [] if o.get("type") == "video" and o.get("url")), None)
            if not vid:
                raise VideoAIError("Kling: tugas selesai tanpa URL video")
            dl = requests.get(vid["url"], timeout=300)
            dl.raise_for_status()
            Path(out).write_bytes(dl.content)
            return {"model": model, "task_id": task_id}
        if st == "failed":
            raise VideoAIError(f"Kling gagal: {str(row.get('error') or row.get('message') or row)[:200]}")
    raise VideoAIError("Kling: melebihi 15 menit")


# ----------------------------------------------------------------------------- API umum

def generate(prompt: str, out: str, provider: str, image: Optional[str] = None, duration: float = 6,
             aspect: str = "9:16", resolution: str = "") -> Dict:
    """Buat klip video AI. `image` diisi → gambar jadi bingkai pertama (gambar→video); kosong → teks→video."""
    if provider not in PROVIDERS:
        raise VideoAIError(f"Provider video tidak dikenal: {provider}")
    if not available(provider):
        raise VideoAIError(f"{PROVIDERS[provider]['label']}: API key belum diisi ({PROVIDERS[provider]['key']})")
    dur = int(max(3, min(15, round(float(duration)))))
    aspect = aspect if aspect in ("9:16", "16:9") else "9:16"
    res = resolution or getattr(config, "VIDEO_AI_RESOLUTION", "720p")
    t0 = time.time()
    fn = _gemini if provider == "gemini" else _kling
    try:
        meta = fn(prompt, out, image, dur, aspect, res)
    except VideoAIError:
        raise
    except Exception as exc:
        raise VideoAIError(f"{PROVIDERS[provider]['label']}: {str(exc)[:300]}") from exc
    if not Path(out).exists() or Path(out).stat().st_size < 1000:
        raise VideoAIError("File video hasil kosong")
    _record(provider, dur)
    logger.info(f"Video AI {PROVIDERS[provider]['label']}: {dur} dtk {'gambar→video' if image else 'teks→video'} "
                f"({time.time() - t0:.0f} dtk)")
    return {"path": out, "provider": provider, "seconds": dur, **meta}


def fit_to_scene(src: str, out: str, duration: float, w: int, h: int) -> str:
    """Klip AI → durasi adegan: dipotong bila lebih panjang; bila lebih pendek diperlambat halus (≤1,25×)
    lalu frame terakhir ditahan (bukan diulang — klip AI yang diulang terlihat 'meloncat')."""
    from . import ffx
    d = ffx.probe(src)["duration"] or duration
    slow = min(1.25, max(1.0, duration / max(d, 0.1)))
    hold = max(0.0, duration - d * slow)
    vf = f"setpts={slow:.4f}*PTS,{ffx.fit_filter(w, h)},fps=30,format=yuv420p"
    if hold > 0.05:
        vf += f",tpad=stop_mode=clone:stop_duration={hold + 0.1:.3f}"
    ffx.run(["-i", src, "-t", f"{duration:.3f}", "-vf", vf, "-an", *ffx.venc(18), out])
    return out
