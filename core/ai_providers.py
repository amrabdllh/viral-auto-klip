"""
AI provider layer (v4) — satu pintu untuk semua LLM yang dipakai Viral Auto Klip.

Provider yang didukung:
  Groq · OpenAI · Anthropic (Claude) · Gemini (API key) · Antigravity (Gemini via login Google/OAuth)
  · DeepSeek · Mistral · OpenRouter · Ollama (lokal) · Custom (OpenAI-compatible: base URL + API key sendiri)

Fitur:
- complete(): panggil provider aktif, kembalikan teks (JSON bila diminta).
- Fallback berantai: bila provider utama gagal/rate-limit, coba AI_FALLBACK_PROVIDERS berurutan.
- Kompatibel model reasoning OpenAI-style (otomatis pindah ke max_completion_tokens & tanpa temperature).
- Claude via SDK resmi `anthropic` (adaptive thinking bawaan model, effort bisa diatur, refusal ditangani).
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from loguru import logger

import config

# ----------------------------------------------------------------------------- registry

PROVIDER_INFO: Dict[str, Dict] = {
    "Groq": {"kind": "groq", "key_attr": "GROQ_API_KEY", "label": "Groq (Llama/GPT-OSS, tercepat)"},
    "OpenAI": {"kind": "openai_compat", "key_attr": "OPENAI_API_KEY", "base_url": None, "label": "OpenAI (GPT)"},
    "Anthropic": {"kind": "anthropic", "key_attr": "ANTHROPIC_API_KEY", "label": "Anthropic (Claude)"},
    "Gemini": {"kind": "gemini_key", "key_attr": "GEMINI_API_KEY", "label": "Google Gemini (API key)"},
    "Antigravity": {"kind": "gemini_oauth", "key_attr": None, "label": "Antigravity — Gemini via login Google"},
    "DeepSeek": {"kind": "openai_compat", "key_attr": "DEEPSEEK_API_KEY", "base_attr": "DEEPSEEK_BASE_URL", "label": "DeepSeek"},
    "Mistral": {"kind": "openai_compat", "key_attr": "MISTRAL_API_KEY", "base_attr": "MISTRAL_BASE_URL", "label": "Mistral AI"},
    "OpenRouter": {"kind": "openai_compat", "key_attr": "OPENROUTER_API_KEY", "base_url": "https://openrouter.ai/api/v1", "label": "OpenRouter (ratusan model)"},
    "Ollama": {"kind": "ollama", "key_attr": None, "label": "Ollama (lokal, gratis)"},
    "Magpie": {"kind": "openai_compat", "key_attr": "MAGPIE_API_KEY", "base_attr": "MAGPIE_BASE_URL",
               "label": "Magpie (gateway lokal — Claude/GPT)"},
    "Custom": {"kind": "openai_compat", "key_attr": "CUSTOM_AI_API_KEY", "base_attr": "CUSTOM_AI_BASE_URL", "label": "Custom AI (base URL + API key sendiri)"},
}

_BUILTIN = set(PROVIDER_INFO)


def _conn(provider: str) -> Optional[Dict]:
    """Koneksi API (core/connections.py) bila `provider` adalah nama koneksi."""
    info = PROVIDER_INFO.get(provider) or {}
    if not info.get("conn"):
        return None
    from . import connections
    return connections.get(info["conn"])


def refresh_connections() -> None:
    """Daftarkan ulang semua Koneksi API sebagai provider (dipanggil saat start & tiap ada perubahan)."""
    from . import connections
    for name in [n for n, i in PROVIDER_INFO.items() if i.get("conn")]:
        PROVIDER_INFO.pop(name, None)
    for c in connections.load():
        if c["name"] in _BUILTIN:
            continue
        PROVIDER_INFO[c["name"]] = {"kind": "anthropic" if c.get("format") == "anthropic" else "openai_compat",
                                    "conn": c["id"], "key_attr": None, "label": f"{c['name']} (koneksi)"}


def _custom_migrated() -> bool:
    from . import connections
    base = str(config.CUSTOM_AI_BASE_URL or "")
    return bool(base) and any(connections._root(c["base_url"]) == connections._root(base) for c in connections.load())


# Model Claude yang mendukung server-side refusal fallback (fallbacks="default").
_ANTHROPIC_FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-2", "claude-fable-5-1"}
# Model Claude generasi 5.x menerima output_config.effort; model lama (mis. haiku-4-5) tidak.
_ANTHROPIC_EFFORT_PREFIXES = ("claude-opus-5", "claude-sonnet-5", "claude-haiku-5", "claude-fable-5", "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-4-6")

GOOGLE_OAUTH_SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/generative-language.retriever",
]
# Saat login juga minta email agar UI bisa menampilkan akun yang sedang login.
GOOGLE_LOGIN_SCOPES = ["openid", "https://www.googleapis.com/auth/userinfo.email"] + GOOGLE_OAUTH_SCOPES


class AIError(RuntimeError):
    pass


class AIRateLimited(AIError):
    pass


# ----------------------------------------------------------------------------- config helpers

def all_providers() -> List[str]:
    builtin = [p for p in config.ALL_AI_PROVIDERS if not (p == "Custom" and _custom_migrated())]
    return builtin + [n for n, i in PROVIDER_INFO.items() if i.get("conn")]


def default_model(provider: str) -> str:
    conn = _conn(provider)
    if conn:
        return conn.get("default_model") or next(iter(conn.get("models") or []), "")
    if provider == "Custom":
        return config.CUSTOM_AI_MODEL
    models = config.AI_PROVIDER_MODELS.get(provider) or []
    detected = _load_detected().get(provider) or []
    if detected:
        # pilih model bawaan pertama yang benar-benar bisa diakses key ini (mis. gpt-5.x butuh verifikasi)
        return next((m for m in models if m in detected), detected[0])
    return models[0] if models else ""


def provider_models(provider: str) -> List[str]:
    conn = _conn(provider)
    if conn:
        return list(conn.get("models") or [])
    return list(config.AI_PROVIDER_MODELS.get(provider) or [])


def _api_key(provider: str) -> str:
    conn = _conn(provider)
    if conn:
        return str(conn.get("api_key") or "")
    attr = PROVIDER_INFO.get(provider, {}).get("key_attr")
    return (getattr(config, attr, "") or "").strip() if attr else ""


def _base_url(provider: str) -> Optional[str]:
    conn = _conn(provider)
    if conn:
        return conn.get("openai_base") or (conn["base_url"] + "/v1")
    info = PROVIDER_INFO.get(provider, {})
    if info.get("base_attr"):
        return (getattr(config, info["base_attr"], "") or "").strip() or None
    return info.get("base_url")


def set_credentials(provider: str, api_key: str = "", base_url: str = "", model: str = "") -> None:
    """Simpan kredensial runtime (dari UI/CLI) ke config — tidak ditulis ke disk."""
    info = PROVIDER_INFO.get(provider, {})
    if api_key and info.get("key_attr"):
        setattr(config, info["key_attr"], api_key.strip())
    if base_url:
        if info.get("base_attr"):
            setattr(config, info["base_attr"], base_url.strip().rstrip("/"))
        elif provider == "Ollama":
            config.OLLAMA_BASE_URL = base_url.strip().rstrip("/")
        elif provider == "Anthropic":
            config.ANTHROPIC_BASE_URL = base_url.strip().rstrip("/")
    if model and provider == "Custom":
        config.CUSTOM_AI_MODEL = model.strip()


_OLLAMA_CHECK = {"t": 0.0, "ok": False}


def _port_open(base_url: str, default_port: int = 80, timeout: float = 0.4) -> bool:
    try:
        from urllib.parse import urlparse
        import socket
        u = urlparse(base_url or "")
        host = u.hostname or "127.0.0.1"
        port = u.port or default_port
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _ollama_running() -> bool:
    """Ollama dianggap siap hanya bila servernya menjawab (dicek maks sekali per menit)."""
    import time
    if time.time() - _OLLAMA_CHECK["t"] > 60:
        try:
            with urllib.request.urlopen(config.OLLAMA_BASE_URL.rstrip("/") + "/api/tags", timeout=0.6) as resp:
                _OLLAMA_CHECK["ok"] = resp.status == 200
        except Exception:
            _OLLAMA_CHECK["ok"] = False
        _OLLAMA_CHECK["t"] = time.time()
    return _OLLAMA_CHECK["ok"]


def is_configured(provider: str) -> bool:
    info = PROVIDER_INFO.get(provider)
    if not info:
        return False
    kind = info["kind"]
    if info.get("conn"):
        conn = _conn(provider)
        return bool(conn and conn.get("api_key") and conn.get("base_url") and conn.get("models"))
    if kind == "ollama":
        return bool(config.OLLAMA_BASE_URL) and _ollama_running()
    if provider == "Magpie":
        # Gateway lokal: dianggap siap kalau port-nya menjawab (tanpa HTTP call).
        return _port_open(config.MAGPIE_BASE_URL, default_port=3425)
    if kind == "gemini_oauth":
        # FIX: dulu dianggap siap hanya karena path client_secret terisi; sekarang harus sudah login.
        return google_login_status()["logged_in"]
    if provider == "Custom":
        return bool(config.CUSTOM_AI_BASE_URL and config.CUSTOM_AI_API_KEY and (config.CUSTOM_AI_MODEL or config.LLM_MODEL))
    return bool(_api_key(provider))


def provider_status() -> List[Dict]:
    """Ringkasan status semua provider (untuk UI / endpoint /providers)."""
    return [
        {
            "provider": name,
            "label": PROVIDER_INFO[name]["label"],
            "configured": is_configured(name),
            "models": model_options(name),
            "default_model": default_model(name),
            "needs_base_url": name in ("Custom", "Ollama"),
            "login": name == "Antigravity",
            "connection": bool(PROVIDER_INFO[name].get("conn")),
        }
        for name in all_providers()
    ]


def resolve_model(provider: str, requested: str = "") -> str:
    """Pilih model untuk provider. Model milik provider lain (mis. default Groq di .env
    saat provider diganti ke Anthropic) diganti otomatis ke default provider ini."""
    if provider == "Custom":
        return (requested or config.CUSTOM_AI_MODEL or config.LLM_MODEL or "").strip()
    if _conn(provider):  # koneksi: nama model apa pun dihormati (gateway bisa punya model yang belum terdeteksi)
        model = (requested or (config.LLM_MODEL if provider == config.AI_PROVIDER else "") or "").strip()
        return model or default_model(provider)
    # LLM_MODEL di .env milik AI_PROVIDER — jangan dipakai untuk provider lain (fallback, tes, dll.)
    model = (requested or (config.LLM_MODEL if provider == config.AI_PROVIDER else "") or "").strip()
    if not model:
        return default_model(provider)
    if model in provider_models(provider):
        return model
    owned_elsewhere = any(model in models for name, models in config.AI_PROVIDER_MODELS.items() if name != provider)
    return default_model(provider) if owned_elsewhere else model  # nama model custom tetap dihormati


def _fallback_chain(primary: str, primary_model: str) -> List[Tuple[str, str]]:
    chain = [(primary, primary_model or default_model(primary))]
    for item in (config.AI_FALLBACK_PROVIDERS or "").split(","):
        item = item.strip()
        if not item:
            continue
        name, _, model = item.partition(":")
        name = name.strip()
        if name in PROVIDER_INFO and name != primary and is_configured(name):
            chain.append((name, model.strip() or default_model(name)))
    return chain


# ----------------------------------------------------------------------------- public API

def complete(
    prompt: str,
    system: str = "",
    max_tokens: int = 1024,
    json_mode: bool = True,
    json_schema: Optional[Dict] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> str:
    """Panggil LLM aktif (dengan fallback berantai). Kembalikan teks respons."""
    provider = provider or config.AI_PROVIDER
    model = model or resolve_model(provider)
    errors = []
    rate_limited = False
    for name, mdl in _fallback_chain(provider, model):
        if not is_configured(name):
            errors.append(f"{name}: belum dikonfigurasi")
            continue
        try:
            text = _dispatch(name, mdl, prompt, system, max_tokens, json_mode, json_schema)
            if not text or not text.strip():
                raise AIError("respons kosong")
            if name != provider:
                logger.info(f"AI fallback dipakai: {name}/{mdl}")
            return text
        except Exception as exc:
            msg = str(exc)
            if _looks_rate_limited(exc):
                rate_limited = True
            errors.append(f"{name}/{mdl}: {msg[:200]}")
            logger.warning(f"AI {name}/{mdl} gagal: {msg[:200]}")
    if rate_limited and len(errors) == 1:
        raise AIRateLimited(errors[0])
    raise AIError(" | ".join(errors) or "Tidak ada provider AI yang aktif")


# batas waktu klien per thread (tes koneksi memakai batas pendek tanpa coba ulang)
_CLIENT_LIMITS = threading.local()


def _client_timeout(default: float = 180.0) -> float:
    return float(getattr(_CLIENT_LIMITS, "timeout", default))


def _client_retries(default: int = 2) -> int:
    return int(getattr(_CLIENT_LIMITS, "retries", default))


def test_connection(provider: str, model: str = "", timeout: float = 45.0) -> Tuple[bool, str]:
    """Tes singkat provider (tombol 'Tes koneksi' di UI).

    HANYA provider yang dipilih — tanpa rantai cadangan (dulu tes provider yang mati bisa tampil "OK" karena
    dijawab DeepSeek/OpenAI cadangan) — dan batas waktu pendek tanpa coba ulang (dulu 180 dtk × 3 percobaan,
    tombol tampak berputar terus bila endpoint diam)."""
    model = model or default_model(provider)
    if provider not in PROVIDER_INFO:
        return False, f"Provider tidak dikenal: {provider}"
    if not is_configured(provider):
        return False, f"{provider} belum dikonfigurasi (API key/base URL kosong)"
    if not model:
        return False, "Model belum dipilih — klik 'Deteksi model' dulu"
    t0 = time.time()
    _CLIENT_LIMITS.timeout, _CLIENT_LIMITS.retries = float(timeout), 0
    try:
        text = _dispatch(provider, model, 'Balas hanya JSON: {"ok": true}', "", 256, True, None)
        if not text or not text.strip():
            return False, f"{provider}/{model}: respons kosong"
        return True, f"OK ({time.time() - t0:.1f} dtk) — {text.strip()[:80]}"
    except Exception as exc:
        msg = str(exc)
        if "timed out" in msg.lower() or "timeout" in type(exc).__name__.lower():
            msg = f"Tidak ada jawaban dalam {timeout:.0f} dtk — endpoint hidup tapi tidak merespons, atau model terlalu lambat. {msg[:150]}"
        elif "connection" in msg.lower() and ("refused" in msg.lower() or "error" in msg.lower()):
            msg = f"Tidak bisa terhubung ke server provider (cek base URL / server lokal menyala). {msg[:150]}"
        return False, f"{provider}/{model}: {msg[:300]}"
    finally:
        del _CLIENT_LIMITS.timeout, _CLIENT_LIMITS.retries


def _looks_rate_limited(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    msg = str(exc).lower()
    return status == 429 or "rate_limit" in msg or "rate limit" in msg or "429" in msg or "quota" in msg


def _dispatch(provider, model, prompt, system, max_tokens, json_mode, json_schema) -> str:
    kind = PROVIDER_INFO[provider]["kind"]
    if kind == "anthropic":
        return _call_anthropic(model, prompt, system, max_tokens, json_schema, provider=provider)
    if kind == "groq":
        return _call_groq(model, prompt, system, max_tokens, json_mode)
    if kind == "openai_compat":
        return _call_openai_compat(provider, model, prompt, system, max_tokens, json_mode)
    if kind == "gemini_key":
        return _call_gemini(model, prompt, system, max_tokens, json_mode, api_key=_api_key("Gemini"))
    if kind == "gemini_oauth":
        return _call_gemini(model, prompt, system, max_tokens, json_mode, oauth=True)
    if kind == "ollama":
        return _call_ollama(model, prompt, system, max_tokens, json_mode)
    raise AIError(f"Provider tidak dikenal: {provider}")


# ----------------------------------------------------------------------------- Anthropic (Claude)

def _call_anthropic(model, prompt, system, max_tokens, json_schema, provider: str = "Anthropic") -> str:
    try:
        import anthropic
    except ImportError as exc:
        raise AIError("Library anthropic belum terpasang: pip install anthropic") from exc

    conn = _conn(provider)
    client = anthropic.Anthropic(
        api_key=_api_key(provider),
        base_url=(conn["base_url"] if conn else config.ANTHROPIC_BASE_URL) or None,
        timeout=_client_timeout(),
        max_retries=_client_retries(),
    )
    if conn:  # gateway pihak ketiga: hanya parameter standar (fitur beta/effort belum tentu diteruskan)
        kwargs = {"model": model, "max_tokens": max(int(max_tokens), 4000), "messages": [{"role": "user", "content": prompt}]}
        if system:
            kwargs["system"] = system
        response = client.messages.create(**kwargs)
        return "".join(block.text for block in response.content if getattr(block, "type", "") == "text")
    output_config: Dict = {}
    if model.startswith(_ANTHROPIC_EFFORT_PREFIXES) and config.ANTHROPIC_EFFORT:
        output_config["effort"] = config.ANTHROPIC_EFFORT
    if json_schema:
        output_config["format"] = {"type": "json_schema", "schema": json_schema}

    kwargs: Dict = {
        "model": model,
        # Model Claude terbaru selalu berpikir (adaptive thinking) dan token berpikir
        # dihitung dalam max_tokens — jangan terlalu kecil agar JSON tidak terpotong.
        "max_tokens": max(int(max_tokens), 8000),
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        kwargs["system"] = system
    if output_config:
        kwargs["output_config"] = output_config

    if model in _ANTHROPIC_FALLBACK_MODELS and config.ANTHROPIC_REFUSAL_FALLBACK:
        response = client.beta.messages.create(
            betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs
        )
    else:
        response = client.messages.create(**kwargs)

    if response.stop_reason == "refusal":
        category = getattr(getattr(response, "stop_details", None), "category", None)
        raise AIError(f"Claude menolak permintaan (refusal, kategori={category})")
    text = "".join(block.text for block in response.content if getattr(block, "type", "") == "text")
    if response.stop_reason == "max_tokens" and not text.strip():
        raise AIError("Claude kehabisan max_tokens sebelum menjawab")
    return text


# ----------------------------------------------------------------------------- OpenAI-compatible

def _openai_style_attempts(max_tokens: int, json_mode: bool) -> List[Dict]:
    """Model reasoning (gpt-5/o-series, deepseek-reasoner) menolak temperature/max_tokens/json mode
    — coba berurutan dari paling lengkap ke paling minimal."""
    big = max(int(max_tokens), 4000)
    fmt = {"response_format": {"type": "json_object"}} if json_mode else {}
    attempts = [
        {"temperature": 0.3, "max_tokens": max(int(max_tokens), 512), **fmt},
        {"max_completion_tokens": big, **fmt},
        {"max_completion_tokens": big},
        {"max_tokens": max(int(max_tokens), 512)},
    ]
    seen, unique = set(), []
    for item in attempts:
        key = json.dumps(item, sort_keys=True)
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def _messages(prompt: str, system: str) -> List[Dict]:
    msgs = [{"role": "system", "content": system}] if system else []
    msgs.append({"role": "user", "content": prompt})
    return msgs


def _run_attempts(create_fn, model, prompt, system, max_tokens, json_mode) -> str:
    last_exc = None
    for extra in _openai_style_attempts(max_tokens, json_mode):
        try:
            response = create_fn(model=model, messages=_messages(prompt, system), **extra)
            choice = response.choices[0]
            text = choice.message.content or ""
            if getattr(choice, "finish_reason", "") == "length" and (not text.strip() or json_mode):
                # Model reasoning (deepseek-flash/reasoner, o-series): token "berpikir" menghabiskan
                # max_tokens sehingga jawaban kosong / JSON terpotong → ulangi sekali dengan jatah besar.
                key = "max_completion_tokens" if "max_completion_tokens" in extra else "max_tokens"
                bigger = {**extra, key: max(int(extra.get(key) or max_tokens) * 4, 8192)}
                logger.info(f"{model}: jawaban kosong/terpotong (batas token habis) — ulang dengan {bigger[key]} token")
                response = create_fn(model=model, messages=_messages(prompt, system), **bigger)
                text = response.choices[0].message.content or ""
            return text
        except Exception as exc:
            last_exc = exc
            status = getattr(exc, "status_code", None)
            if status in (400, 422):  # parameter tidak didukung -> coba bentuk lain
                continue
            raise
    raise last_exc  # type: ignore[misc]


def _call_openai_compat(provider, model, prompt, system, max_tokens, json_mode) -> str:
    try:
        import openai
    except ImportError as exc:
        raise AIError("Library openai belum terpasang: pip install openai") from exc
    base_url = _base_url(provider)
    if provider == "Custom" and not base_url:
        raise AIError("Custom AI butuh CUSTOM_AI_BASE_URL")
    client = openai.OpenAI(api_key=_api_key(provider), base_url=base_url, timeout=_client_timeout(),
                           max_retries=_client_retries())
    return _run_attempts(client.chat.completions.create, model, prompt, system, max_tokens, json_mode)


def _call_groq(model, prompt, system, max_tokens, json_mode) -> str:
    try:
        from groq import Groq
    except ImportError as exc:
        raise AIError("Library groq belum terpasang: pip install groq") from exc
    client = Groq(api_key=_api_key("Groq"), timeout=_client_timeout(120.0), max_retries=_client_retries())
    return _run_attempts(client.chat.completions.create, model, prompt, system, max_tokens, json_mode)


# ----------------------------------------------------------------------------- Gemini (API key / OAuth)

def _http_json(url: str, payload: Dict, headers: Dict, timeout: int = 120) -> Dict:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:400]
        err = AIError(f"HTTP {exc.code}: {body}")
        err.status_code = exc.code  # type: ignore[attr-defined]
        raise err from exc


def _call_gemini(model, prompt, system, max_tokens, json_mode, api_key: str = "", oauth: bool = False) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    generation = {
        "temperature": 0.3,
        # Gemini 2.5 memakai token "thinking" dari kuota ini -> beri ruang agar JSON tidak terpotong.
        "maxOutputTokens": max(int(max_tokens), 8192),
    }
    if json_mode:
        generation["responseMimeType"] = "application/json"
    payload: Dict = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": generation}
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}

    if oauth:
        token, project = _google_access_token()
        headers = {"Authorization": f"Bearer {token}"}
        if project:
            headers["x-goog-user-project"] = project
    else:
        headers = {"x-goog-api-key": api_key}  # header, bukan query string (key tidak bocor ke log URL)
    result = _http_json(url, payload, headers)
    candidates = result.get("candidates") or []
    if not candidates:
        raise AIError(f"Gemini tidak mengembalikan kandidat: {str(result.get('promptFeedback'))[:200]}")
    parts = candidates[0].get("content", {}).get("parts") or []
    return "".join(part.get("text", "") for part in parts if not part.get("thought"))


# ----------------------------------------------------------------------------- Antigravity: login Google (OAuth)

def _google_token_path() -> Path:
    return config.BASE_DIR / config.GOOGLE_AI_TOKEN_FILE


def _google_client_project() -> str:
    secret = config.GOOGLE_AI_CLIENT_SECRET_FILE
    try:
        data = json.loads(Path(secret).read_text(encoding="utf-8"))
        section = data.get("installed") or data.get("web") or {}
        return section.get("project_id", "")
    except Exception:
        return ""


class GoogleSetupNeeded(AIError):
    """client_secret.json belum ada — UI menampilkan panduan setup, bukan sekadar error."""


def validate_client_secret(raw: str) -> Dict:
    """Cek isi client_secret.json: harus JSON OAuth client tipe 'Desktop app' (kunci 'installed')."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AIError("File bukan JSON yang valid. Pastikan yang dipilih adalah client_secret_xxx.json dari Google Cloud.") from exc
    if "web" in data and "installed" not in data:
        raise AIError("Ini OAuth client tipe 'Web application'. Buat ulang dengan tipe 'Desktop app' lalu download JSON-nya.")
    section = data.get("installed")
    if not section or not section.get("client_id") or not section.get("client_secret"):
        raise AIError("Isi file tidak dikenali. Download JSON dari Google Cloud > Clients > (Desktop app) > Download JSON.")
    return {"client_id": section["client_id"], "project_id": section.get("project_id", "")}


def save_client_secret(raw: str) -> Dict:
    """Simpan client_secret.json yang di-upload lewat UI ke folder proyek (di-gitignore)."""
    info = validate_client_secret(raw)
    target = config.BASE_DIR / config.GOOGLE_AI_SAVED_SECRET
    target.write_text(raw, encoding="utf-8")
    config.GOOGLE_AI_CLIENT_SECRET_FILE = str(target)
    logger.info(f"client_secret.json tersimpan untuk project '{info['project_id'] or '?'}'")
    return google_login_status()


def google_login(client_secret_file: str = "") -> Dict:
    """Buka jendela login Google di browser untuk memakai Gemini tanpa API key.

    Google mewajibkan setiap aplikasi punya OAuth client sendiri — login bawaan aplikasi
    Antigravity hanya berlaku untuk Antigravity. Jadi dipakai OAuth client 'Desktop app'
    milikmu (client_secret.json). Token disimpan lokal di .google_ai_token.json (di-gitignore).
    """
    import os
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise AIError("Butuh: pip install google-auth-oauthlib") from exc
    secret = client_secret_file or config.GOOGLE_AI_CLIENT_SECRET_FILE
    if not secret or not Path(secret).exists():
        raise GoogleSetupNeeded(
            "Belum ada client_secret.json. Ikuti panduan setup (sekali saja, ±5 menit) lalu upload file-nya, "
            "atau pakai provider Gemini dengan API key gratis dari aistudio.google.com."
        )
    validate_client_secret(Path(secret).read_text(encoding="utf-8"))
    config.GOOGLE_AI_CLIENT_SECRET_FILE = str(secret)
    os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")  # Google kadang mengembalikan urutan scope berbeda
    flow = InstalledAppFlow.from_client_secrets_file(str(secret), GOOGLE_LOGIN_SCOPES)
    creds = flow.run_local_server(
        port=0, prompt="select_account consent", open_browser=True,
        success_message="Login berhasil! Kamu boleh menutup tab ini dan kembali ke Viral Auto Klip.",
    )
    token = json.loads(creds.to_json())
    token["account_email"] = _email_from_id_token(getattr(creds, "id_token", None))
    _google_token_path().write_text(json.dumps(token), encoding="utf-8")
    logger.success(f"Login Google berhasil: {token['account_email'] or '(email tidak tersedia)'}")
    return google_login_status()


def _email_from_id_token(id_token: Optional[str]) -> str:
    """Ambil email dari id_token (hanya untuk ditampilkan di UI; tidak dipakai untuk otorisasi)."""
    if not id_token:
        return ""
    import base64
    try:
        payload = id_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("email", "")
    except Exception:
        return ""


def google_logout() -> Dict:
    _google_token_path().unlink(missing_ok=True)
    return google_login_status()


def google_login_status() -> Dict:
    path = _google_token_path()
    email = ""
    if path.exists():
        try:
            email = json.loads(path.read_text(encoding="utf-8")).get("account_email", "")
        except Exception:
            pass
    secret = config.GOOGLE_AI_CLIENT_SECRET_FILE
    return {
        "logged_in": path.exists(),
        "email": email,
        "has_client_secret": bool(secret and Path(secret).exists()),
        "token_file": str(path),
    }


def _google_access_token() -> Tuple[str, str]:
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError as exc:
        raise AIError("Butuh: pip install google-auth-oauthlib") from exc
    path = _google_token_path()
    if not path.exists():
        # Jangan membuka jendela login di tengah proses render — minta login lewat UI/CLI.
        raise AIError("Belum login Google. Klik 'Login dengan Google' di UI atau jalankan: python cli.py --google-login")
    creds = Credentials.from_authorized_user_file(str(path), GOOGLE_OAUTH_SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            path.write_text(creds.to_json(), encoding="utf-8")
        else:
            raise AIError("Token Google tidak valid — login ulang.")
    project = config.GOOGLE_AI_PROJECT_ID or _google_client_project()
    return creds.token, project


# ----------------------------------------------------------------------------- Ollama

def _call_ollama(model, prompt, system, max_tokens, json_mode) -> str:
    url = f"{config.OLLAMA_BASE_URL.rstrip('/')}/api/chat"
    payload: Dict = {
        "model": model,
        "messages": _messages(prompt, system),
        "stream": False,
        "options": {"temperature": 0.3, "num_predict": max(int(max_tokens), 1024)},
    }
    if json_mode:
        payload["format"] = "json"
    result = _http_json(url, payload, {}, timeout=300)
    return result.get("message", {}).get("content", "")


# ----------------------------------------------------------------------------- deteksi model otomatis

# Pola model NON-chat yang disaring (embedding, suara, gambar, moderasi, dll.)
_NON_CHAT = (
    "embed", "embedding", "whisper", "tts", "transcribe", "audio", "realtime", "dall-e", "dalle", "image",
    "moderation", "guard", "ocr", "rerank", "search", "babbage", "davinci", "computer-use", "sora", "veo", "imagen",
    "aqa", "learnlm", "distil-whisper", "playai", "orpheus",
)


def _is_chat_model(model_id: str) -> bool:
    lowered = model_id.lower()
    return not any(token in lowered for token in _NON_CHAT)


def detect_models(provider: str, api_key: str = "", base_url: str = "") -> Dict:
    """Ambil daftar model yang benar-benar tersedia untuk API key ini (endpoint resmi /models).

    api_key/base_url opsional: bila diisi dipakai hanya untuk deteksi (tidak disimpan).
    Return: {"provider", "models": [...], "recommended": str, "total": int}
    """
    info = PROVIDER_INFO.get(provider)
    if not info:
        raise AIError(f"Provider tidak dikenal: {provider}")
    key = (api_key or _api_key(provider)).strip()
    kind = info["kind"]
    ids: List[str] = []
    if info.get("conn"):
        from . import connections
        conn = connections.refresh(info["conn"])
        return {"provider": provider, "models": conn["models"], "image_models": conn["image_models"],
                "recommended": conn.get("default_model", ""), "total": len(conn["models"])}

    if kind == "anthropic":
        if not key:
            raise AIError("Isi API key Anthropic dulu")
        import anthropic
        client = anthropic.Anthropic(api_key=key, base_url=(base_url or config.ANTHROPIC_BASE_URL or None), timeout=30.0)
        ids = [m.id for m in client.models.list(limit=100)]
    elif kind in ("groq", "openai_compat"):
        base = (base_url or _base_url(provider) or "").rstrip("/") or None
        if provider == "Groq":
            base = base or "https://api.groq.com/openai/v1"
        if provider == "Custom" and not base:
            raise AIError("Isi Base URL Custom dulu")
        if not key and provider != "Custom":
            raise AIError(f"Isi API key {provider} dulu")
        import openai
        client = openai.OpenAI(api_key=key or "none", base_url=base, timeout=30.0, max_retries=1)
        ids = [m.id for m in client.models.list()]
    elif kind in ("gemini_key", "gemini_oauth"):
        if kind == "gemini_key":
            if not key:
                raise AIError("Isi API key Gemini dulu")
            headers = {"x-goog-api-key": key}
        else:
            token, project = _google_access_token()
            headers = {"Authorization": f"Bearer {token}", **({"x-goog-user-project": project} if project else {})}
        url, page = "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000", None
        for _ in range(5):
            req = urllib.request.Request(url + (f"&pageToken={page}" if page else ""), headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                raise AIError(f"HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')[:300]}") from exc
            for model in data.get("models", []):
                if "generateContent" in (model.get("supportedGenerationMethods") or []):
                    ids.append(str(model.get("name", "")).replace("models/", ""))
            page = data.get("nextPageToken")
            if not page:
                break
    elif kind == "ollama":
        base = (base_url or config.OLLAMA_BASE_URL).rstrip("/")
        try:
            with urllib.request.urlopen(f"{base}/api/tags", timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            raise AIError(f"Ollama tidak bisa dihubungi di {base} — sudah jalan? ({str(exc)[:120]})") from exc
        ids = [m.get("name", "") for m in data.get("models", [])]
        if not ids:
            raise AIError("Ollama jalan, tapi belum ada model. Contoh: ollama pull qwen2.5:7b")

    models = sorted({m for m in ids if m and _is_chat_model(m)}, key=lambda m: _model_sort_key(provider, m))
    known = provider_models(provider)
    recommended = next((m for m in known if m in models), models[0] if models else "")
    if models and not api_key and not base_url:
        _save_detected(provider, models)  # hanya untuk key yang tersimpan → jadi isi dropdown model
    return {"provider": provider, "models": models, "recommended": recommended, "total": len(models)}


# ----------------------------------------------------------------------------- cache model terdeteksi

_MODELS_CACHE = config.BASE_DIR / "data" / "models_cache.json"


def _load_detected() -> Dict[str, List[str]]:
    try:
        return json.loads(_MODELS_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_detected(provider: str, models: List[str]) -> None:
    data = _load_detected()
    data[provider] = models
    _MODELS_CACHE.parent.mkdir(parents=True, exist_ok=True)
    _MODELS_CACHE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def save_capabilities(provider: str, caps: Dict[str, bool]) -> None:
    data = _load_detected()
    data.setdefault("_caps", {})[provider] = caps
    _MODELS_CACHE.parent.mkdir(parents=True, exist_ok=True)
    _MODELS_CACHE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def capability(provider: str, name: str) -> Optional[bool]:
    """True/False bila sudah pernah dicek (Setel otomatis), None bila belum diketahui."""
    return (_load_detected().get("_caps") or {}).get(provider, {}).get(name)


def model_options(provider: str) -> List[str]:
    """Isi dropdown model: hasil deteksi (bila pernah dideteksi) atau daftar bawaan."""
    if _conn(provider):
        return provider_models(provider)
    detected = _load_detected().get(provider) or []
    if not detected:
        return provider_models(provider)
    return sorted(set(detected), key=lambda m: _model_sort_key(provider, m))


def all_model_options() -> Dict[str, Dict]:
    detected = _load_detected()
    return {p: {"models": model_options(p), "detected": bool(detected.get(p)), "default": default_model(p)}
            for p in all_providers()}


def detect_all_configured() -> Dict[str, int]:
    """Deteksi model untuk semua provider yang punya key (dipakai Setel otomatis)."""
    found = {}
    for p in all_providers():
        if p in ("Ollama", "Antigravity", "Custom") or PROVIDER_INFO.get(p, {}).get("conn") or not is_configured(p):
            continue
        try:
            found[p] = detect_models(p)["total"]
            _drop_unusable_defaults(p)
        except Exception as exc:
            logger.warning(f"Deteksi model {p} gagal: {str(exc)[:120]}")
    return found


def _drop_unusable_defaults(provider: str, tries: int = 3) -> None:
    """Model yang terdaftar di /models belum tentu boleh dipakai (mis. OpenAI gpt-5.x butuh organisasi
    terverifikasi). Coba panggil kandidat default; yang ditolak dikeluarkan dari daftar."""
    if PROVIDER_INFO.get(provider, {}).get("kind") != "openai_compat":
        return
    import openai
    client = openai.OpenAI(api_key=_api_key(provider), base_url=_base_url(provider), timeout=30.0, max_retries=0)
    for _ in range(tries):
        model = default_model(provider)
        if not model:
            return
        try:
            client.chat.completions.create(model=model, messages=[{"role": "user", "content": "hi"}], max_completion_tokens=16)
            return
        except Exception as exc:
            if getattr(exc, "status_code", None) not in (403, 404):
                return  # error lain (rate limit, jaringan) — jangan buang modelnya
            logger.info(f"Model {provider}/{model} tidak bisa dipakai key ini — dikeluarkan dari dropdown")
            data = _load_detected()
            data[provider] = [m for m in data.get(provider, []) if m != model]
            _MODELS_CACHE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def _model_sort_key(provider: str, model_id: str):
    """Model yang sudah dikenal (rekomendasi config) di atas, sisanya alfabetis; versi terbaru dulu."""
    known = provider_models(provider)
    if model_id in known:
        return (0, known.index(model_id), "")
    return (1, 0, "".join(chr(255 - ord(c)) if c.isdigit() else c for c in model_id.lower()))


# ----------------------------------------------------------------------------- JSON helper

def parse_json(content: str) -> Dict:
    """Parse JSON dari respons LLM (tahan ```json fences & teks tambahan)."""
    import re
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", (content or "").strip())
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            raise
        value = json.loads(match.group(0))
    if isinstance(value, list):
        return {"items": value}
    return value


# Koneksi API: slot lama Custom dipindah sekali, lalu semua koneksi didaftarkan sebagai provider.
try:
    from . import connections as _connections
    _connections.migrate_custom()
    refresh_connections()
except Exception as _exc:  # jangan sampai koneksi rusak membuat aplikasi gagal start
    logger.warning(f"Koneksi API tidak dimuat: {_exc}")
