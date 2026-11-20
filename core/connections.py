"""
Koneksi API (v6.6) — simpan beberapa endpoint AI (base URL + API key) sekaligus.

Tiap koneksi otomatis muncul sebagai:
- pilihan "Provider AI" (cari momen, judul, caption, chat…) dengan dropdown model chat-nya, dan
- pilihan "Mesin gambar" bila endpoint punya model gambar (mis. gpt-image-2).

Cocok untuk gateway seperti one-api/new-api/OpenRouter/LiteLLM yang melayani banyak model
(Claude, GPT, gambar) dengan satu key. Format panggilan dideteksi otomatis: OpenAI
(/v1/chat/completions) atau Anthropic (/v1/messages).

Disimpan di data/connections.json (lokal, tidak ikut git — sama seperti .env). Key tidak pernah
dikirim balik ke UI selain versi tersamar.
"""
from __future__ import annotations

import base64
import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

import requests
from loguru import logger

import config

PATH = config.BASE_DIR / "data" / "connections.json"
_LOCK = threading.Lock()

IMAGE_HINTS = ("gpt-image", "dall-e", "dalle", "imagen", "flux", "sdxl", "stable-diffusion", "midjourney", "seedream",
               "kolors", "recraft", "ideogram", "image-gen", "nano-banana", "-image")
NON_CHAT = ("embed", "tts", "whisper", "transcri", "audio", "speech", "moderation", "rerank", "realtime", "-search",
            "babbage", "davinci", "video", "sora", "veo", "music")
# urutan model chat default (cepat & murah dulu — memilih momen butuh banyak panggilan)
CHAT_PREF = ("claude-haiku-5-5", "claude-sonnet-5-5", "claude-haiku-4-5", "claude-sonnet-4-6", "gpt-4o", "gpt-4.1",
             "gemini-2.5-flash", "deepseek-chat")
IMAGE_PREF = ("gpt-image-2", "gpt-image-1.5", "gpt-image-1", "gemini-2.5-flash-image", "imagen-4", "dall-e-3")


class ConnectionError_(RuntimeError):
    pass


# ----------------------------------------------------------------------------- penyimpanan

def load() -> List[Dict]:
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save(items: List[Dict]) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(PATH)


def get(name_or_id: str) -> Optional[Dict]:
    if not name_or_id:
        return None
    key = name_or_id.strip().lower()
    for c in load():
        if c["id"] == name_or_id or c["name"].lower() == key:
            return c
    return None


def names() -> List[str]:
    return [c["name"] for c in load()]


def mask(key: str) -> str:
    return f"{key[:5]}…{key[-4:]}" if len(key) > 12 else "•" * len(key)


def public(c: Dict) -> Dict:
    """Versi aman untuk UI (tanpa key utuh)."""
    out = {k: v for k, v in c.items() if k != "api_key"}
    out["api_key_masked"] = mask(c.get("api_key", ""))
    return out


# ----------------------------------------------------------------------------- deteksi

def _root(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    url = re.sub(r"/(chat/completions|messages|images/generations|models)$", "", url)
    return re.sub(r"/v1$", "", url)


def _headers(key: str) -> Dict:
    return {"Authorization": f"Bearer {key}", "x-api-key": key, "Content-Type": "application/json"}


def classify(ids: List[str]) -> Dict[str, List[str]]:
    chat, image = [], []
    for m in sorted(set(i for i in ids if i)):
        low = m.lower()
        if any(h in low for h in IMAGE_HINTS):
            image.append(m)
        elif not any(h in low for h in NON_CHAT):
            chat.append(m)
    pref = lambda order: (lambda m: (order.index(m) if m in order else len(order), m))  # noqa: E731
    return {"chat": sorted(chat, key=pref(CHAT_PREF)), "image": sorted(image, key=pref(IMAGE_PREF))}


def _try_chat(openai_base: str, root: str, key: str, model: str, fmt: str) -> bool:
    prompt = "Sebutkan nama ibukota Indonesia dalam satu kata, tanpa penjelasan."
    try:
        if fmt == "openai":
            r = requests.post(f"{openai_base}/chat/completions", headers=_headers(key), timeout=60,
                              json={"model": model, "max_tokens": 32, "messages": [{"role": "user", "content": prompt}]})
            return r.ok and bool(r.json().get("choices"))
        r = requests.post(f"{root}/v1/messages", timeout=60,
                          headers={**_headers(key), "anthropic-version": "2023-06-01"},
                          json={"model": model, "max_tokens": 32, "messages": [{"role": "user", "content": prompt}]})
        return r.ok and isinstance(r.json().get("content"), list)
    except Exception:
        return False


def probe(base_url: str, api_key: str, fmt: str = "auto") -> Dict:
    """Cek endpoint: alamat /v1 yang benar, daftar model (chat vs gambar), dan format panggilan."""
    root = _root(base_url)
    if not re.match(r"^https?://", root):
        raise ConnectionError_("Base URL harus diawali http:// atau https://")
    if not api_key.strip():
        raise ConnectionError_("API key kosong")
    ids, openai_base, last = [], f"{root}/v1", ""
    for base in (f"{root}/v1", root):
        try:
            r = requests.get(f"{base}/models", headers=_headers(api_key), timeout=30)
        except requests.RequestException as exc:
            last = str(exc)[:160]
            continue
        if r.status_code in (401, 403):
            raise ConnectionError_(f"API key ditolak ({r.status_code}) — cek lagi key-nya")
        if r.ok:
            try:
                data = r.json()
            except ValueError:
                continue
            items = data.get("data") if isinstance(data, dict) else data
            ids = [str(m.get("id") or m.get("name") or "") for m in (items or []) if isinstance(m, dict)]
            if ids:
                openai_base = base
                break
        last = f"HTTP {r.status_code}"
    if not ids:
        raise ConnectionError_(f"Daftar model tidak bisa dibaca dari {root} ({last}). Pastikan Base URL benar.")
    groups = classify(ids)
    chat_model = next(iter(groups["chat"]), "")
    use = fmt if fmt in ("openai", "anthropic") else ""
    if not use and chat_model:
        if _try_chat(openai_base, root, api_key, chat_model, "openai"):
            use = "openai"
        elif chat_model.startswith("claude") and _try_chat(openai_base, root, api_key, chat_model, "anthropic"):
            use = "anthropic"
    return {"root": root, "openai_base": openai_base, "format": use or "openai", "chat_ok": bool(use),
            "models": groups["chat"], "image_models": groups["image"], "total": len(ids)}


# ----------------------------------------------------------------------------- CRUD

def add(name: str, base_url: str, api_key: str, fmt: str = "auto") -> Dict:
    name = re.sub(r"\s+", " ", (name or "").strip())[:40]
    info = probe(base_url, api_key, fmt)
    if not name:
        host = re.sub(r"^https?://(api\.|newapi\.|www\.)?", "", info["root"]).split("/")[0]
        name = host.split(".")[0].capitalize() or "Koneksi"
    from . import ai_providers
    reserved = set(config.ALL_AI_PROVIDERS) | {"gemini", "openai", "pollinations", "card"}
    with _LOCK:
        items = load()
        base_name, n = name, 2
        while name in reserved or any(c["name"] == name for c in items):
            name, n = f"{base_name} {n}", n + 1
        conn = {"id": uuid.uuid4().hex[:8], "name": name, "base_url": info["root"], "api_key": api_key.strip(),
                "format": info["format"], "openai_base": info["openai_base"], "models": info["models"],
                "image_models": info["image_models"], "default_model": next(iter(info["models"]), ""),
                "image_model": next(iter(info["image_models"]), ""), "chat_ok": info["chat_ok"], "updated": time.time()}
        items.append(conn)
        _save(items)
    ai_providers.refresh_connections()
    logger.info(f"Koneksi '{name}' ditambahkan: {len(conn['models'])} model chat, {len(conn['image_models'])} model gambar")
    return conn


def update(conn_id: str, **fields) -> Dict:
    from . import ai_providers
    allowed = {"name", "default_model", "image_model", "format", "api_key", "base_url"}
    with _LOCK:
        items = load()
        conn = next((c for c in items if c["id"] == conn_id), None)
        if not conn:
            raise ConnectionError_("Koneksi tidak ditemukan")
        old_name = conn["name"]
        for k, v in fields.items():
            if k in allowed and v is not None and (k != "api_key" or str(v).strip()):
                conn[k] = str(v).strip()
        if fields.get("base_url"):
            conn["base_url"] = _root(conn["base_url"])
        conn["updated"] = time.time()
        _save(items)
    if conn["name"] != old_name:
        _rename_refs(old_name, conn["name"])
    ai_providers.refresh_connections()
    return conn


def refresh(conn_id: str) -> Dict:
    """Deteksi ulang model (mis. setelah gateway menambah model baru)."""
    conn = get(conn_id)
    if not conn:
        raise ConnectionError_("Koneksi tidak ditemukan")
    info = probe(conn["base_url"], conn["api_key"], conn.get("format", "auto"))
    fields = {"openai_base": info["openai_base"], "models": info["models"], "image_models": info["image_models"],
              "chat_ok": info["chat_ok"]}
    with _LOCK:
        items = load()
        for c in items:
            if c["id"] == conn["id"]:
                c.update(fields)
                if c.get("default_model") not in c["models"]:
                    c["default_model"] = next(iter(c["models"]), "")
                if c.get("image_model") not in c["image_models"]:
                    c["image_model"] = next(iter(c["image_models"]), "")
                c["updated"] = time.time()
                conn = c
        _save(items)
    from . import ai_providers
    ai_providers.refresh_connections()
    return conn


def remove(conn_id: str) -> None:
    from . import ai_providers
    with _LOCK:
        items = load()
        conn = next((c for c in items if c["id"] == conn_id), None)
        _save([c for c in items if c["id"] != conn_id])
    if conn:
        _rename_refs(conn["name"], "")
    ai_providers.refresh_connections()


def _rename_refs(old: str, new: str) -> None:
    """Pengaturan yang menunjuk ke koneksi ikut diganti (atau dikosongkan bila dihapus)."""
    from . import settings
    vals: Dict[str, object] = {}
    if config.AI_PROVIDER == old:
        vals["AI_PROVIDER"] = new or "DeepSeek"
        if not new:
            vals["LLM_MODEL"] = ""
    if getattr(config, "IMAGE_PROVIDER", "") == old:
        vals["IMAGE_PROVIDER"] = new
    chain = [x.strip() for x in str(config.AI_FALLBACK_PROVIDERS or "").split(",") if x.strip()]
    fixed = []
    for item in chain:
        name, sep, model = item.partition(":")
        if name.strip() == old:
            if new:
                fixed.append(f"{new}{sep}{model}")
        else:
            fixed.append(item)
    if fixed != chain:
        vals["AI_FALLBACK_PROVIDERS"] = ",".join(fixed)
    if vals:
        try:
            settings.save_settings(vals)
        except Exception as exc:
            logger.warning(f"Pengaturan koneksi tidak diperbarui: {exc}")


def migrate_custom() -> Optional[Dict]:
    """Pindahkan slot lama 'Custom AI' (CUSTOM_AI_*) jadi koneksi pertama — tanpa jaringan, model dideteksi nanti."""
    base, key = str(config.CUSTOM_AI_BASE_URL or "").strip(), str(config.CUSTOM_AI_API_KEY or "").strip()
    if not base or not key or config._is_placeholder(key):
        return None
    if any(_root(c["base_url"]) == _root(base) and c["api_key"] == key for c in load()):
        return None
    host = re.sub(r"^https?://(api\.|newapi\.|www\.)?", "", _root(base)).split("/")[0]
    model = str(config.CUSTOM_AI_MODEL or "")
    conn = {"id": uuid.uuid4().hex[:8], "name": (host.split(".")[0].capitalize() or "Custom"), "base_url": _root(base),
            "api_key": key, "format": "openai", "openai_base": _root(base) + "/v1", "models": [model] if model else [],
            "image_models": [], "default_model": model, "image_model": "", "chat_ok": True, "updated": time.time(),
            "migrated_from_custom": True}
    with _LOCK:
        items = load()
        items.append(conn)
        _save(items)
    logger.info(f"Slot Custom AI dipindah ke koneksi '{conn['name']}'")
    return conn


# ----------------------------------------------------------------------------- gambar

def _size_for(model: str, w: int, h: int) -> str:
    if "dall-e-3" in model:
        return "1024x1792" if h > w else ("1792x1024" if w > h else "1024x1024")
    return "1024x1536" if h > w * 1.1 else ("1536x1024" if w > h * 1.1 else "1024x1024")


def image(conn: Dict, model: str, prompt: str, out: str, w: int = 1080, h: int = 1920) -> str:
    """Buat gambar lewat /images/generations (format OpenAI). Return path file."""
    model = model or conn.get("image_model") or ""
    if not model:
        raise ConnectionError_(f"Koneksi {conn['name']} tidak punya model gambar")
    url = f"{conn.get('openai_base') or conn['base_url'] + '/v1'}/images/generations"
    payload = {"model": model, "prompt": prompt[:3800], "n": 1, "size": _size_for(model, w, h)}
    r = requests.post(url, headers=_headers(conn["api_key"]), json=payload, timeout=300)
    if r.status_code == 400 and "size" in r.text.lower():
        payload.pop("size")
        r = requests.post(url, headers=_headers(conn["api_key"]), json=payload, timeout=300)
    if not r.ok:
        raise ConnectionError_(f"{conn['name']}/{model} HTTP {r.status_code}: {r.text[:240]}")
    item = (r.json().get("data") or [{}])[0]
    if item.get("b64_json"):
        Path(out).write_bytes(base64.b64decode(item["b64_json"]))
    elif item.get("url"):
        Path(out).write_bytes(requests.get(item["url"], timeout=120).content)
    else:
        raise ConnectionError_(f"{conn['name']}/{model}: respons tanpa gambar")
    return out
