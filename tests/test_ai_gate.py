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

print("\nSemua uji batas gateway AI lulus" if not fails else f"\nGAGAL: {len(fails)} uji")
sys.exit(1 if fails else 0)
