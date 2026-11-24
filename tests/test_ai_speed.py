"""Uji pindah ke cadangan saat AI utama LAMBAT (core/ai_providers._prefer_fast) dan log progres Editor-AI, tanpa
jaringan:
- utama rata-rata ≥ AI_SLOW_SWITCH_SEC dan cadangan ≤ separuhnya → cadangan dipanggil duluan
- utama cepat / sampel < 3 / fitur dimatikan → urutan tetap
- cadangan yang belum pernah diukur dicoba sekali (lalu dipakai bila memang cepat)
- utama tetap dicoba sesekali, dan dipakai lagi begitu cepat kembali
- panggilan besar (pencari momen) dan kecil diukur terpisah
- _run_logged: urutan hasil sama dengan map, progres tercatat

Jalankan: python tests/test_ai_speed.py
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from core import ai_providers as ai, editor_review as er  # noqa: E402
from loguru import logger  # noqa: E402

fails = []


def check(name, cond):
    print(("OK   " if cond else "GAGAL ") + name)
    if not cond:
        fails.append(name)


ai.PROVIDER_INFO.setdefault("GW", {"kind": "openai_compat"})
ai.is_configured = lambda p: True
ai._base_url = lambda p: "https://api.example.com/v1"  # bukan lokal → tanpa gerbang
config.AI_PROVIDER, config.LLM_MODEL = "GW", "lambat"
ai.resolve_model = lambda p: "lambat"
config.AI_FALLBACK_PROVIDERS = "GW:cepat"
config.AI_SLOW_SWITCH_SEC = 45

calls = []
speed = {"lambat": 70.0, "cepat": 10.0}


def fake(provider, model, *a, **k):
    calls.append(model)
    return "{}"


ai._dispatch_unlimited = fake


def reset():
    ai._LATENCY.clear()
    ai._PROBE.clear()
    calls.clear()


def measured(model, sec, n=3, big=False, ago=0.0):
    for _ in range(n):
        ai._note_latency("GW", model, 9000 if big else 2200, sec)
    if ago:
        avg, cnt, _ = ai._LATENCY[("GW", model, big)]
        ai._LATENCY[("GW", model, big)] = (avg, cnt, time.time() - ago)


def first_model(max_tokens=2200):
    calls.clear()
    ai.complete("x", max_tokens=max_tokens)
    return calls[0]


reset()
measured("lambat", 70)
measured("cepat", 10, n=1)
check("utama 70 dtk, cadangan 10 dtk → cadangan duluan", first_model() == "cepat")

reset()
measured("lambat", 20)
measured("cepat", 5, n=1)
check("utama cepat (20 dtk) → tetap utama", first_model() == "lambat")

reset()
measured("lambat", 70, n=2)
measured("cepat", 10, n=1)
check("baru 2 sampel → belum dianggap lambat", first_model() == "lambat")

reset()
measured("lambat", 70)
measured("cepat", 50, n=1)
check("cadangan tidak jauh lebih cepat (50 vs 70) → tetap utama", first_model() == "lambat")

reset()
measured("lambat", 70)
check("cadangan belum diukur → dicoba sekali", first_model() == "cepat")
check("lalu terukur cepat → terus dipakai", first_model() == "cepat")

reset()
measured("lambat", 70, ago=200)
measured("cepat", 10, n=1)
check("utama lama tidak diukur → dicoba lagi sekali", first_model() == "lambat")
check("percobaan berikutnya kembali ke cadangan", first_model() == "cepat")

reset()
measured("lambat", 70)
measured("cepat", 10, n=1)
for _ in range(6):
    ai._note_latency("GW", "lambat", 2200, 8.0)  # utama pulih (hasil percobaan sesekali)
check("utama sudah cepat lagi → dipakai kembali", first_model() == "lambat")

reset()
measured("lambat", 70, big=True)
measured("cepat", 10, n=1, big=True)
check("panggilan kecil tidak terpengaruh ukuran panggilan besar", first_model(2200) == "lambat")
check("panggilan besar pindah ke cadangan", first_model(9000) == "cepat")

reset()
config.AI_SLOW_SWITCH_SEC = 0
measured("lambat", 70)
measured("cepat", 10, n=1)
check("AI_SLOW_SWITCH_SEC=0 → mati", first_model() == "lambat")
config.AI_SLOW_SWITCH_SEC = 45


# --- _dispatch mencatat durasi, termasuk timeout
reset()
ai._dispatch("GW", "lambat", "x", "", 2200, True, None)
check("_dispatch mencatat durasi", ai.latency("GW", "lambat") is not None)


def slow_timeout(*a, **k):
    raise TimeoutError("Request timed out.")


ai._dispatch_unlimited = slow_timeout
try:
    ai._dispatch("GW", "putus", "x", "", 2200, True, None)
except TimeoutError:
    pass
check("timeout ikut dicatat sebagai lambat", ai.latency("GW", "putus") is not None)
ai._dispatch_unlimited = fake


# --- log progres Editor-AI
lines = []
sink = logger.add(lambda m: lines.append(str(m.record["message"])), level="INFO", format="{message}")
out = er._run_logged(lambda x: (time.sleep(0.01 * (5 - x)), x * 2)[1], list(range(5)), 3, "Editor-AI menilai",
                     every=0)
logger.remove(sink)
check("urutan hasil sama dengan map", out == [0, 2, 4, 6, 8])
check("progres dilaporkan sampai 5/5", any("5/5" in m for m in lines) and any("1/5" in m for m in lines))
check("perkiraan sisa waktu ditampilkan", any("lagi" in m for m in lines))

print("\nSemua uji kecepatan AI lulus" if not fails else f"\nGAGAL: {len(fails)} uji")
sys.exit(1 if fails else 0)
