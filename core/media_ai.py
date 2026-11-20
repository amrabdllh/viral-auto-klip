"""
Capability router media AI: TTS, gambar, clone suara, SFX, musik, stok footage.

Tiap kemampuan punya provider pilihan + fallback otomatis:
- TTS    : edge (gratis, tanpa key) · openai · gemini · elevenlabs
- Gambar : openai · gemini · pollinations (gratis, publik) · card (kartu teks lokal)
- Clone / SFX / Musik : elevenlabs
- Stok   : pexels · pixabay
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import random
import re
import time
import urllib.parse
import wave
from pathlib import Path
from typing import Dict, List, Optional

import requests
from loguru import logger

import config


class MediaAIError(RuntimeError):
    pass


def _key(name: str) -> str:
    value = str(getattr(config, name, "") or "")
    return "" if config._is_placeholder(value) else value.strip()


def _raise_for(resp: requests.Response, label: str) -> None:
    if resp.status_code >= 400:
        try:
            detail = resp.json()
            detail = json.dumps(detail)[:400]
        except ValueError:
            detail = resp.text[:400]
        raise MediaAIError(f"{label} HTTP {resp.status_code}: {detail}")


# ============================================================================ TTS

EDGE_VOICES_DEFAULT = [
    {"id": "id-ID-ArdiNeural", "label": "Ardi — Indonesia (pria)", "lang": "id"},
    {"id": "id-ID-GadisNeural", "label": "Gadis — Indonesia (wanita)", "lang": "id"},
    {"id": "jv-ID-DimasNeural", "label": "Dimas — Jawa (pria)", "lang": "jv"},
    {"id": "jv-ID-SitiNeural", "label": "Siti — Jawa (wanita)", "lang": "jv"},
    {"id": "su-ID-JajangNeural", "label": "Jajang — Sunda (pria)", "lang": "su"},
    {"id": "su-ID-TutiNeural", "label": "Tuti — Sunda (wanita)", "lang": "su"},
    {"id": "ms-MY-OsmanNeural", "label": "Osman — Melayu (pria)", "lang": "ms"},
    {"id": "ms-MY-YasminNeural", "label": "Yasmin — Melayu (wanita)", "lang": "ms"},
    {"id": "en-US-AndrewMultilingualNeural", "label": "Andrew — English multilingual (pria)", "lang": "en"},
    {"id": "en-US-AvaMultilingualNeural", "label": "Ava — English multilingual (wanita)", "lang": "en"},
    {"id": "en-US-GuyNeural", "label": "Guy — English US (pria)", "lang": "en"},
    {"id": "en-GB-SoniaNeural", "label": "Sonia — English UK (wanita)", "lang": "en"},
    {"id": "ar-SA-HamedNeural", "label": "Hamed — Arab (pria)", "lang": "ar"},
    {"id": "ar-SA-ZariyahNeural", "label": "Zariyah — Arab (wanita)", "lang": "ar"},
    {"id": "ja-JP-KeitaNeural", "label": "Keita — Jepang (pria)", "lang": "ja"},
    {"id": "ko-KR-InJoonNeural", "label": "InJoon — Korea (pria)", "lang": "ko"},
]
OPENAI_VOICES = ["alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer", "verse"]
GEMINI_VOICES = ["Kore", "Puck", "Charon", "Fenrir", "Aoede", "Zephyr", "Leda", "Orus", "Achernar", "Callirrhoe",
                 "Enceladus", "Iapetus", "Algieba", "Despina", "Erinome", "Gacrux", "Sulafat", "Vindemiatrix"]

TTS_PROVIDERS = {
    "edge": {"label": "Edge TTS (gratis, tanpa key)", "key": None},
    "openai": {"label": "OpenAI TTS", "key": "OPENAI_API_KEY"},
    "gemini": {"label": "Gemini TTS", "key": "GEMINI_API_KEY"},
    "elevenlabs": {"label": "ElevenLabs", "key": "ELEVENLABS_API_KEY"},
}


def tts_available(provider: str) -> bool:
    if provider == "edge":
        try:
            import edge_tts  # noqa: F401
            return True
        except ImportError:
            return False
    info = TTS_PROVIDERS.get(provider)
    if provider == "openai" and _openai_cap("tts") is False:
        return False  # key ada, tapi project-nya tidak punya akses model TTS
    return bool(info and _key(info["key"]))


def _openai_cap(name: str):
    try:
        from .ai_providers import capability
        return capability("OpenAI", name)
    except Exception:
        return None


def list_voices(provider: str) -> List[Dict]:
    if provider == "edge":
        return EDGE_VOICES_DEFAULT
    if provider == "openai":
        return [{"id": v, "label": v.capitalize(), "lang": "multi"} for v in OPENAI_VOICES]
    if provider == "gemini":
        return [{"id": v, "label": v, "lang": "multi"} for v in GEMINI_VOICES]
    if provider == "elevenlabs":
        key = _key("ELEVENLABS_API_KEY")
        if not key:
            return []
        resp = requests.get("https://api.elevenlabs.io/v1/voices", headers={"xi-api-key": key}, timeout=30)
        _raise_for(resp, "ElevenLabs voices")
        return [{"id": v["voice_id"], "label": f"{v.get('name')} ({v.get('category', '')})", "lang": "multi"}
                for v in resp.json().get("voices", [])]
    return []


def _estimate_words(text: str, duration: float) -> List[Dict]:
    """Timestamp kata perkiraan (proporsional panjang huruf) untuk provider tanpa word boundary."""
    words = text.split()
    if not words or duration <= 0:
        return []
    weights = [max(2, len(re.sub(r"\W", "", w))) + (3 if re.search(r"[.,!?;:]$", w) else 0) for w in words]
    total = sum(weights)
    lead, usable = 0.05, max(0.1, duration - 0.15)
    t = lead
    out = []
    for w, wt in zip(words, weights):
        d = usable * wt / total
        out.append({"word": w, "start": round(t, 3), "end": round(t + d * 0.92, 3)})
        t += d
    return out


def _audio_duration(path: str) -> float:
    from . import ffx
    try:
        return ffx.probe(path)["duration"]
    except Exception:
        return 0.0


def _edge_tts(text: str, out: str, voice: str, rate: str) -> List[Dict]:
    import edge_tts

    async def go():
        try:
            com = edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary")
        except TypeError:
            com = edge_tts.Communicate(text, voice, rate=rate)
        words = []
        with open(out, "wb") as fh:
            async for chunk in com.stream():
                if chunk["type"] == "audio":
                    fh.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    start = chunk["offset"] / 1e7
                    words.append({"word": chunk["text"], "start": round(start, 3),
                                  "end": round(start + chunk["duration"] / 1e7, 3)})
        return words

    try:
        return asyncio.run(go())
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(go())
        finally:
            loop.close()


def _openai_tts(text: str, out: str, voice: str, speed: float, instructions: str = "") -> None:
    model = str(getattr(config, "OPENAI_TTS_MODEL", "") or "")
    if "tts" not in model:  # mis. "gpt-4o" bukan model suara → pakai default yang valid
        model = "gpt-4o-mini-tts"
    payload = {"model": model, "voice": voice or "nova",
               "input": text[:4000], "response_format": "mp3", "speed": speed}
    if instructions:
        payload["instructions"] = instructions
    resp = requests.post("https://api.openai.com/v1/audio/speech", json=payload, timeout=300,
                         headers={"Authorization": f"Bearer {_key('OPENAI_API_KEY')}"})
    if resp.status_code == 400 and "instructions" in payload:
        payload.pop("instructions")
        resp = requests.post("https://api.openai.com/v1/audio/speech", json=payload, timeout=300,
                             headers={"Authorization": f"Bearer {_key('OPENAI_API_KEY')}"})
    _raise_for(resp, "OpenAI TTS")
    Path(out).write_bytes(resp.content)


def _gemini_tts(text: str, out: str, voice: str, instructions: str = "") -> str:
    model = getattr(config, "GEMINI_TTS_MODEL", "gemini-2.5-flash-preview-tts")
    prompt = f"{instructions.strip()}: {text}" if instructions else text
    payload = {"contents": [{"parts": [{"text": prompt}]}],
               "generationConfig": {"responseModalities": ["AUDIO"],
                                    "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice or "Kore"}}}}}
    resp = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                         params={"key": _key("GEMINI_API_KEY")}, json=payload, timeout=300)
    _raise_for(resp, "Gemini TTS")
    parts = resp.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
    data = next((p["inlineData"]["data"] for p in parts if "inlineData" in p), None)
    if not data:
        raise MediaAIError("Gemini TTS tidak mengembalikan audio")
    pcm = base64.b64decode(data)
    wav_path = str(Path(out).with_suffix(".wav"))
    with wave.open(wav_path, "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(24000); wf.writeframes(pcm)
    return wav_path


def _elevenlabs_voice(voice: str) -> str:
    """voice_id ElevenLabs = 20 karakter alfanumerik; nama suara Edge/OpenAI diganti default."""
    for cand in (voice, getattr(config, "TTS_VOICE", "")):
        if cand and re.fullmatch(r"[A-Za-z0-9]{20}", cand):
            return cand
    return "q8qwd1jY2jS3AWOBeq25"  # Pratama — suara Indonesia


def _elevenlabs_tts(text: str, out: str, voice: str, speed: float) -> List[Dict]:
    voice = _elevenlabs_voice(voice)
    payload = {"text": text, "model_id": getattr(config, "ELEVENLABS_TTS_MODEL", "eleven_multilingual_v2"),
               "voice_settings": {"stability": 0.45, "similarity_boost": 0.8, "speed": max(0.7, min(1.2, speed))}}
    resp = requests.post(f"https://api.elevenlabs.io/v1/text-to-speech/{voice}/with-timestamps",
                         headers={"xi-api-key": _key("ELEVENLABS_API_KEY")}, json=payload, timeout=300)
    _raise_for(resp, "ElevenLabs TTS")
    data = resp.json()
    Path(out).write_bytes(base64.b64decode(data["audio_base64"]))
    align = data.get("alignment") or {}
    chars, starts, ends = align.get("characters", []), align.get("character_start_times_seconds", []), align.get("character_end_times_seconds", [])
    words, cur, ws, prev_e = [], "", None, 0.0
    for ch, s, e in zip(chars, starts, ends):
        if ch.isspace():
            if cur:
                words.append({"word": cur, "start": ws, "end": prev_e})
            cur, ws = "", None
        else:
            if ws is None:
                ws = s
            cur += ch
            prev_e = e
    if cur:
        words.append({"word": cur, "start": ws, "end": prev_e})
    return words


def tts(text: str, out: str, provider: str = "", voice: str = "", speed: float = 1.0,
        instructions: str = "", allow_fallback: bool = True) -> Dict:
    """Teks → file audio. Return {path, duration, words, provider, voice}."""
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        raise MediaAIError("Teks kosong")
    provider = provider or getattr(config, "TTS_PROVIDER", "edge") or "edge"
    order = [provider] + ([p for p in ("edge", "openai", "gemini", "elevenlabs") if p != provider] if allow_fallback else [])
    errors = []
    for prov in order:
        if not tts_available(prov):
            errors.append(f"{prov}: belum tersedia")
            continue
        v = voice if prov == provider else ""
        try:
            Path(out).parent.mkdir(parents=True, exist_ok=True)
            words: List[Dict] = []
            path = out
            if prov == "edge":
                v = v or getattr(config, "TTS_VOICE", "") or "id-ID-ArdiNeural"
                if not re.match(r"^[a-z]{2,3}-[A-Z]{2}-", v):
                    v = "id-ID-ArdiNeural"
                pct = int(round((speed - 1.0) * 100))
                words = _edge_tts(text, out, v, f"{pct:+d}%")
            elif prov == "openai":
                v = v if v in OPENAI_VOICES else "nova"
                _openai_tts(text, out, v, speed, instructions)
            elif prov == "gemini":
                v = v if v in GEMINI_VOICES else "Kore"
                path = _gemini_tts(text, out, v, instructions)
            elif prov == "elevenlabs":
                words = _elevenlabs_tts(text, out, v, speed)
            duration = _audio_duration(path)
            if duration <= 0:
                raise MediaAIError("audio kosong")
            if not words:
                words = _estimate_words(text, duration)
            if prov != provider:
                logger.info(f"TTS fallback dipakai: {prov}")
            return {"path": path, "duration": duration, "words": words, "provider": prov, "voice": v}
        except Exception as exc:
            errors.append(f"{prov}: {str(exc)[:200]}")
            logger.warning(f"TTS {prov} gagal: {exc}")
    raise MediaAIError("Semua provider TTS gagal — " + " | ".join(errors))


# ============================================================================ Gambar

IMAGE_PROVIDERS = {
    "openai": {"label": "OpenAI (gpt-image)", "key": "OPENAI_API_KEY"},
    "gemini": {"label": "Gemini (Nano Banana)", "key": "GEMINI_API_KEY"},
    "pollinations": {"label": "Pollinations (gratis: 1 gambar/45 dtk; token = tanpa batas)", "key": None},
    "card": {"label": "Kartu teks lokal (tanpa AI)", "key": None},
}


def _image_conn(provider: str):
    """Koneksi API (core/connections.py) yang punya model gambar, bila `provider` nama koneksi."""
    from . import connections
    conn = connections.get(provider)
    return conn if conn and conn.get("image_models") else None


def image_providers() -> Dict[str, Dict]:
    """Bawaan + tiap Koneksi API yang punya model gambar (mis. gpt-image-2 via gateway)."""
    from . import connections
    out = dict(IMAGE_PROVIDERS)
    for c in connections.load():
        if c.get("image_models"):
            out[c["name"]] = {"label": f"{c['name']} ({c.get('image_model') or c['image_models'][0]})", "key": None,
                              "conn": True, "models": c["image_models"], "model": c.get("image_model", "")}
    return out


def image_available(provider: str) -> bool:
    if _image_conn(provider):
        return True
    info = IMAGE_PROVIDERS.get(provider)
    if not info:
        return False
    if provider == "openai" and _openai_cap("image") is False:
        return False
    return info["key"] is None or bool(_key(info["key"]))


def _openai_image_model() -> str:
    model = str(getattr(config, "OPENAI_IMAGE_MODEL", "") or "")
    # mis. "gpt-4o" bukan model gambar di endpoint images → pakai gpt-image-1
    return model if model.startswith(("gpt-image", "dall-e")) else "gpt-image-1"


def _openai_image(prompt: str, out: str, w: int, h: int) -> None:
    size = "1024x1536" if h > w else ("1536x1024" if w > h else "1024x1024")
    resp = requests.post("https://api.openai.com/v1/images/generations", timeout=300,
                         headers={"Authorization": f"Bearer {_key('OPENAI_API_KEY')}"},
                         json={"model": _openai_image_model(), "prompt": prompt[:3800],
                               "size": size, "n": 1})
    _raise_for(resp, "OpenAI image")
    item = resp.json()["data"][0]
    if item.get("b64_json"):
        Path(out).write_bytes(base64.b64decode(item["b64_json"]))
    else:
        Path(out).write_bytes(requests.get(item["url"], timeout=120).content)


def _gemini_image(prompt: str, out: str, w: int, h: int, refs: Optional[List[str]] = None) -> None:
    model = getattr(config, "GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")
    ratio = "9:16" if h > w else ("16:9" if w > h else "1:1")
    parts: List[Dict] = []
    for ref in (refs or [])[:3]:
        p = Path(ref)
        if p.exists():
            mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
            parts.append({"inlineData": {"mimeType": mime, "data": base64.b64encode(p.read_bytes()).decode()}})
    parts.append({"text": prompt + (" Keep the same character face, hair and outfit as the reference image." if refs else "")})
    payload = {"contents": [{"parts": parts}],
               "generationConfig": {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": ratio}}}
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    resp = requests.post(url, params={"key": _key("GEMINI_API_KEY")}, json=payload, timeout=300)
    if resp.status_code == 400 and "imageConfig" in resp.text:
        payload["generationConfig"].pop("imageConfig")
        payload["generationConfig"]["responseModalities"] = ["TEXT", "IMAGE"]
        resp = requests.post(url, params={"key": _key("GEMINI_API_KEY")}, json=payload, timeout=300)
    _raise_for(resp, "Gemini image")
    for cand in resp.json().get("candidates", []):
        for part in cand.get("content", {}).get("parts", []):
            if "inlineData" in part:
                Path(out).write_bytes(base64.b64decode(part["inlineData"]["data"]))
                return
    raise MediaAIError("Gemini tidak mengembalikan gambar (mungkin diblokir filter keamanan)")


def _pollinations_image(prompt: str, out: str, w: int, h: int, seed: Optional[int] = None) -> None:
    seed = seed if seed is not None else random.randint(1, 10_000_000)
    url = f"https://image.pollinations.ai/prompt/{urllib.parse.quote(prompt[:900])}"
    # tier gratis menempel watermark kecil di pojok bawah → minta 8% lebih tinggi lalu potong bawahnya
    tall = int(h * 1.08)
    params = {"width": w, "height": tall, "nologo": "true", "seed": seed, "model": "flux"}
    token = _key("POLLINATIONS_TOKEN")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    for attempt in range(3):
        resp = requests.get(url, params=params, headers=headers, timeout=180)
        if resp.status_code in (402, 429, 500, 502, 503) and attempt < 2:
            # tanpa token: 1 gambar / ±45 dtk per IP — tunggu sesuai petunjuk server lalu ulangi
            m = re.search(r"[Rr]etry after (\d+)", resp.text)
            wait = min(65, int(m.group(1)) + 2 if m else 46)
            logger.info(f"Pollinations membatasi permintaan — menunggu {wait} dtk")
            time.sleep(wait)
            continue
        break
    _raise_for(resp, "Pollinations")
    if not resp.headers.get("content-type", "").startswith("image"):
        raise MediaAIError("Pollinations tidak mengembalikan gambar")
    from PIL import Image
    with Image.open(io.BytesIO(resp.content)) as img:
        img = img.convert("RGB")
        img.crop((0, 0, img.width, int(img.height * (1 - 0.075)))).save(out, "JPEG", quality=92)


def _fit_image(path: str, w: int, h: int) -> None:
    from PIL import Image
    with Image.open(path) as img:
        img = img.convert("RGB")
        scale = max(w / img.width, h / img.height)
        img = img.resize((max(w, int(img.width * scale)), max(h, int(img.height * scale))))
        left, top = (img.width - w) // 2, (img.height - h) // 2
        img.crop((left, top, left + w, top + h)).save(path, "JPEG", quality=92)


def image(prompt: str, out: str, provider: str = "", w: int = 1080, h: int = 1920, refs: Optional[List[str]] = None,
          card_text: Optional[str] = None, allow_fallback: bool = True, seed: Optional[int] = None) -> Dict:
    """Prompt → file gambar JPG (di-crop pas w×h). Return {path, provider}."""
    if not provider and not getattr(config, "IMAGE_PROVIDER", ""):  # otomatis: koneksi bergambar > Gemini > Pollinations
        provider = next((n for n, i in image_providers().items() if i.get("conn")), "")
    provider = provider or getattr(config, "IMAGE_PROVIDER", "") or ("gemini" if _key("GEMINI_API_KEY") else "pollinations")
    order = [provider] + ([p for p in ("gemini", "openai", "pollinations", "card") if p != provider] if allow_fallback else [])
    errors = []
    out = str(Path(out).with_suffix(".jpg"))
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    for prov in order:
        if not image_available(prov):
            continue
        try:
            conn = _image_conn(prov)
            if conn:
                from . import connections
                model = (getattr(config, "IMAGE_MODEL", "") if prov == getattr(config, "IMAGE_PROVIDER", "") else "") or conn.get("image_model", "")
                if model not in conn["image_models"]:
                    model = conn.get("image_model") or conn["image_models"][0]
                connections.image(conn, model, prompt, out, w, h)
                _fit_image(out, w, h)
                return {"path": out, "provider": prov, "model": model}
            if prov == "openai":
                _openai_image(prompt, out, w, h)
            elif prov == "gemini":
                _gemini_image(prompt, out, w, h, refs)
            elif prov == "pollinations":
                _pollinations_image(prompt, out, w, h, seed)
            else:
                from . import ffx
                ffx.color_card(out, prompt[:90] if card_text is None else card_text, w, h)
            _fit_image(out, w, h)
            return {"path": out, "provider": prov}
        except Exception as exc:
            errors.append(f"{prov}: {str(exc)[:200]}")
            logger.warning(f"Gambar {prov} gagal: {exc}")
    raise MediaAIError("Semua provider gambar gagal — " + " | ".join(errors))


# ============================================================================ Stok

def stock_search(query: str, kind: str = "video", orientation: str = "portrait", per_page: int = 8) -> List[Dict]:
    """Cari footage/foto stok gratis (Pexels → Pixabay). Return [{url, preview, w, h, duration, source}]."""
    results: List[Dict] = []
    pexels = _key("PEXELS_API_KEY")
    if pexels:
        try:
            base = "https://api.pexels.com/videos/search" if kind == "video" else "https://api.pexels.com/v1/search"
            resp = requests.get(base, headers={"Authorization": pexels}, timeout=30,
                                params={"query": query, "orientation": orientation, "per_page": per_page})
            _raise_for(resp, "Pexels")
            data = resp.json()
            if kind == "video":
                for v in data.get("videos", []):
                    files = sorted([f for f in v.get("video_files", []) if f.get("link")],
                                   key=lambda f: abs((f.get("height") or 0) - 1920))
                    if files:
                        results.append({"url": files[0]["link"], "preview": v.get("image"), "w": files[0].get("width"),
                                        "h": files[0].get("height"), "duration": v.get("duration"), "source": "pexels"})
            else:
                for p in data.get("photos", []):
                    results.append({"url": p["src"].get("large2x") or p["src"]["original"], "preview": p["src"].get("medium"),
                                    "w": p.get("width"), "h": p.get("height"), "source": "pexels"})
        except Exception as exc:
            logger.warning(f"Pexels gagal: {exc}")
    pixabay = _key("PIXABAY_API_KEY")
    if pixabay and len(results) < per_page:
        try:
            base = "https://pixabay.com/api/videos/" if kind == "video" else "https://pixabay.com/api/"
            params = {"key": pixabay, "q": query[:100], "per_page": max(3, per_page), "safesearch": "true"}
            if kind != "video":
                params["orientation"] = "vertical" if orientation == "portrait" else "horizontal"
            resp = requests.get(base, params=params, timeout=30)
            _raise_for(resp, "Pixabay")
            for hit in resp.json().get("hits", []):
                if kind == "video":
                    vids = hit.get("videos", {})
                    best = vids.get("large") or vids.get("medium") or {}
                    if best.get("url"):
                        results.append({"url": best["url"], "preview": best.get("thumbnail"), "w": best.get("width"),
                                        "h": best.get("height"), "duration": hit.get("duration"), "source": "pixabay"})
                else:
                    results.append({"url": hit.get("largeImageURL"), "preview": hit.get("previewURL"),
                                    "w": hit.get("imageWidth"), "h": hit.get("imageHeight"), "source": "pixabay"})
        except Exception as exc:
            logger.warning(f"Pixabay gagal: {exc}")
    return results[:per_page]


def stock_available() -> bool:
    return bool(_key("PEXELS_API_KEY") or _key("PIXABAY_API_KEY"))


def download(url: str, out: str) -> str:
    with requests.get(url, stream=True, timeout=180, headers={"User-Agent": "Mozilla/5.0"}) as resp:
        _raise_for(resp, "Download")
        with open(out, "wb") as fh:
            for chunk in resp.iter_content(1 << 16):
                fh.write(chunk)
    return out


# ============================================================================ ElevenLabs: clone, SFX, musik

def _require_eleven() -> str:
    key = _key("ELEVENLABS_API_KEY")
    if not key:
        raise MediaAIError("Butuh ELEVENLABS_API_KEY — isi di Pengaturan → API key")
    return key


def clone_voice(name: str, sample_paths: List[str], description: str = "", remove_noise: bool = True) -> Dict:
    key = _require_eleven()
    files = [("files", (Path(p).name, open(p, "rb"))) for p in sample_paths]
    try:
        resp = requests.post("https://api.elevenlabs.io/v1/voices/add", headers={"xi-api-key": key}, timeout=300,
                             data={"name": name[:80], "description": description[:400],
                                   "remove_background_noise": "true" if remove_noise else "false"}, files=files)
    finally:
        for _, (_, fh) in files:
            fh.close()
    _raise_for(resp, "ElevenLabs clone")
    return {"voice_id": resp.json().get("voice_id"), "name": name}


def delete_voice(voice_id: str) -> None:
    key = _require_eleven()
    resp = requests.delete(f"https://api.elevenlabs.io/v1/voices/{voice_id}", headers={"xi-api-key": key}, timeout=60)
    _raise_for(resp, "ElevenLabs delete voice")


def sound_effect(prompt: str, out: str, duration: float = 0) -> str:
    key = _require_eleven()
    payload: Dict = {"text": prompt[:450], "prompt_influence": 0.4}
    if duration:
        payload["duration_seconds"] = max(0.5, min(30.0, duration))
    resp = requests.post("https://api.elevenlabs.io/v1/sound-generation", headers={"xi-api-key": key},
                         json=payload, timeout=300)
    _raise_for(resp, "ElevenLabs SFX")
    Path(out).write_bytes(resp.content)
    return out


def music(prompt: str, out: str, duration: float = 30, lyrics: str = "", instrumental: bool = True) -> str:
    key = _require_eleven()
    text = prompt.strip()
    if lyrics.strip():
        text += f"\n\nLyrics:\n{lyrics.strip()}"
        instrumental = False
    payload = {"prompt": text[:4000], "music_length_ms": int(max(10, min(300, duration)) * 1000)}
    if instrumental:
        payload["force_instrumental"] = True
    resp = requests.post("https://api.elevenlabs.io/v1/music", headers={"xi-api-key": key}, json=payload, timeout=600)
    _raise_for(resp, "ElevenLabs Music")
    Path(out).write_bytes(resp.content)
    return out


def capabilities() -> Dict:
    return {
        "tts": {p: {"label": i["label"], "ready": tts_available(p)} for p, i in TTS_PROVIDERS.items()},
        "image": {p: {"label": i["label"], "ready": image_available(p)} for p, i in image_providers().items()},
        "stock": stock_available(),
        "elevenlabs": bool(_key("ELEVENLABS_API_KEY")),
        "youtube_api": bool(_key("YOUTUBE_API_KEY")),
        "defaults": {"tts": getattr(config, "TTS_PROVIDER", "edge"), "voice": getattr(config, "TTS_VOICE", ""),
                     "image": getattr(config, "IMAGE_PROVIDER", "")},
    }
