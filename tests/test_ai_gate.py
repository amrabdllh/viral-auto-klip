"""Uji batas panggilan bersamaan untuk gateway AI lokal (core/ai_providers.provider_gate), tanpa jaringan:
- provider ber-base_url localhost dibatasi AI_LOCAL_PARALLEL panggilan bersamaan
- provider cloud tidak dibatasi
- AI_LOCAL_PARALLEL=0 mematikan batas

Jalankan: python tests/test_ai_gate.py
"""
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from core import ai_providers as ai  # noqa: E402

fails = []


def check(name, cond):
    print(("OK   " if cond else "GAGAL ") + name)
    if not cond:
        fails.append(name)


urls = {"LokalGW": "http://127.0.0.1:3425/v1", "CloudAPI": "https://api.example.com/v1"}
ai._base_url = lambda p: urls.get(p)
ai.PROVIDER_INFO.setdefault("LokalGW", {"kind": "openai_compat"})
ai.PROVIDER_INFO.setdefault("CloudAPI", {"kind": "openai_compat"})

state = {"n": 0, "max": 0}
lock = threading.Lock()


def fake(*_a, **_k):
    with lock:
        state["n"] += 1
        state["max"] = max(state["max"], state["n"])
    time.sleep(0.15)
    with lock:
        state["n"] -= 1
    return "{}"


ai._dispatch_unlimited = fake


def peak(provider, n=8):
    ai._GATES.clear()
    state["max"] = 0
    threads = [threading.Thread(target=ai._dispatch, args=(provider, "m", "x", "", 10, True, None)) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return state["max"]


config.AI_LOCAL_PARALLEL = 2
check("localhost terdeteksi lokal", ai.is_local_endpoint("LokalGW"))
check("API cloud bukan lokal", not ai.is_local_endpoint("CloudAPI"))
check("gateway lokal maks 2 bersamaan", peak("LokalGW") == 2)
check("API cloud tidak dibatasi", peak("CloudAPI") == 8)
config.AI_LOCAL_PARALLEL = 0
check("AI_LOCAL_PARALLEL=0 → tanpa batas", peak("LokalGW") == 8)

# Rantai cadangan: provider sama dengan model lain tetap dipakai (dulu dibuang diam-diam), nama tidak peka huruf
ai.is_configured = lambda name: True
config.AI_FALLBACK_PROVIDERS = "LokalGW:model-b, cloudapi:x, TidakAda:y, LokalGW:model-a"
chain = ai._fallback_chain("LokalGW", "model-a")
check("cadangan provider sama, model beda dipakai", ("LokalGW", "model-b") in chain)
check("nama provider tidak peka huruf besar/kecil", ("CloudAPI", "x") in chain)
check("provider tidak dikenal & duplikat utama dilewati", len(chain) == 3 and chain[0] == ("LokalGW", "model-a"))

from core import settings  # noqa: E402
check("simpan: nama dinormalkan", settings._validate("AI_FALLBACK_PROVIDERS", " cloudapi:x ,lokalgw") == "CloudAPI:x,LokalGW")
try:
    settings._validate("AI_FALLBACK_PROVIDERS", "Open AI:gpt-4o")
    check("simpan: provider tidak dikenal ditolak", False)
except settings.SettingsError as exc:
    check("simpan: provider tidak dikenal ditolak", "Open AI" in str(exc))

# Gateway gaya Anthropic (9router cc/claude-*) melapor finish_reason "max_tokens", bukan "length" → dulu JSON terpotong
from types import SimpleNamespace  # noqa: E402
asked = []


def fake_create(model, messages, **kw):
    asked.append(kw.get("max_tokens") or kw.get("max_completion_tokens"))
    done = len(asked) > 1
    msg = SimpleNamespace(content='{"ok": true}' if done else '{"title": "Dibilang PUT')
    return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop" if done else "max_tokens")])


out = ai._run_attempts(fake_create, "gpt-x", "p", "", 2200, True)
check("finish_reason max_tokens → diulang dengan jatah lebih besar", out == '{"ok": true}' and asked[1] > asked[0])
asked.clear()
ai._run_attempts(fake_create, "cc/claude-sonnet-5-5", "p", "", 2200, True)
check("model Claude lewat gateway: jatah awal ≥ 8000 token (token berpikir)", asked[0] >= 8000)

# Kena limit (429, dibungkus 503 oleh gateway) → model utama diistirahatkan, panggilan berikutnya langsung ke cadangan
config.AI_FALLBACK_PROVIDERS = "LokalGW:gemini"
config.AI_RATE_LIMIT_COOLDOWN = 120
config.AI_PROVIDER, config.LLM_MODEL = "LokalGW", "claude"
ai._COOLDOWN.clear()
hits = []


class LimitErr(Exception):
    status_code = 503


def fake_dispatch(name, mdl, *a, **k):
    hits.append(mdl)
    if mdl == "claude":
        raise LimitErr('Error code: 503 - [claude] [429]: {"type":"error","error":{"type":"rate_limit_error"}}')
    return '{"ok": true}'


ai._dispatch = fake_dispatch
first = ai.complete("p", provider="LokalGW", model="claude")
second = ai.complete("p", provider="LokalGW", model="claude")
check("limit → cadangan dipakai", first == '{"ok": true}' and hits[:2] == ["claude", "gemini"])
check("panggilan berikutnya tidak menembak model yang limit", hits[2:] == ["gemini"] and second == '{"ok": true}')
ai._COOLDOWN[("LokalGW", "claude")] = 0  # masa istirahat habis → model utama dicoba lagi
hits.clear()
ai.complete("p", provider="LokalGW", model="claude")
check("setelah istirahat, model utama dicoba lagi", hits[0] == "claude")

# Saldo habis (402) → dilewati 1 jam; panggilan ke provider khusus yang gagal pindah ke provider UTAMA dulu
ai._COOLDOWN.clear()
hits.clear()


class Billing(Exception):
    status_code = 402


def billing_dispatch(name, mdl, *a, **k):
    hits.append(f"{name}/{mdl}")
    if name == "CloudAPI":
        raise Billing("Error code: 402 - Insufficient Balance")
    return '{"ok": true}'


ai._dispatch = billing_dispatch
ai.complete("p", provider="CloudAPI", model="pro")
check("provider khusus gagal → provider utama pilihan pengguna dipakai dulu", hits == ["CloudAPI/pro", "LokalGW/claude"])
check("saldo habis → diistirahatkan (bukan dicoba tiap panggilan)", ai.is_cooling("CloudAPI", "pro"))
hits.clear()
ai.complete("p", provider="CloudAPI", model="pro")
check("panggilan berikutnya melewati provider yang saldonya habis", hits == ["LokalGW/claude"])

print("\nSemua uji batas gateway AI lulus" if not fails else f"\nGAGAL: {len(fails)} uji")
sys.exit(1 if fails else 0)
