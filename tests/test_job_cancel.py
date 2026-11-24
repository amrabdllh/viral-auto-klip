"""Uji pembatalan job di tengah tahap panjang (Analisis AI / Editor-AI), tanpa jaringan:
- setelah Batal, panggilan AI baru langsung ditolak (AICancelled), provider tidak dipanggil lagi
- Editor-AI berhenti mengerjakan klip yang belum mulai
- job berakhir "cancelled" dalam hitungan detik, bukan setelah seluruh analisis selesai
- sinyal batal dilepas setelah job selesai (job berikutnya tidak ikut terhenti)

Jalankan: python tests/test_job_cancel.py
"""
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from core import ai_providers as ai, db, editor_review as er, jobs  # noqa: E402

fails = []


def check(name, cond):
    print(("OK   " if cond else "GAGAL ") + name)
    if not cond:
        fails.append(name)


tmp = Path(tempfile.mkdtemp(prefix="jcancel_"))
db.DB_PATH = tmp / "studio.db"  # database sementara, bukan data/studio.db asli
config.OUTPUT_DIR = str(tmp)

ai.PROVIDER_INFO.setdefault("GW", {"kind": "openai_compat"})
ai.is_configured = lambda p: True
ai._base_url = lambda p: "https://api.example.com/v1"
config.AI_PROVIDER, config.AI_FALLBACK_PROVIDERS = "GW", ""
ai.resolve_model = lambda p: "m"
calls = []


def fake(*a, **k):
    calls.append(1)
    time.sleep(0.05)
    return "{}"


ai._dispatch_unlimited = fake

# --- unit: sinyal batal
ai.ABORT = lambda: True
try:
    ai.complete("x")
    check("panggilan AI ditolak setelah batal", False)
except ai.AICancelled:
    check("panggilan AI ditolak setelah batal", not calls)
ai.ABORT = None
ai.complete("x")
check("tanpa batal → AI dipanggil normal", len(calls) == 1)

# --- unit: Editor-AI berhenti mengerjakan klip yang belum mulai
flag = {"stop": False}
ai.ABORT = lambda: flag["stop"]
done = []


def work(i):
    if i == 3:
        flag["stop"] = True
    try:
        ai.complete("x")
        done.append(i)
    except ai.AICancelled:
        pass
    return i


out = er._run_logged(work, list(range(40)), 2, "Editor-AI menilai")
check(f"klip yang belum mulai tidak dikerjakan ({len(done)}/40 dinilai)", len(done) < 10)
check("hasil tetap sepanjang input", len(out) == 40)
ai.ABORT = None

# --- integrasi: tombol Batal di tengah "analisis"
state = {"started": threading.Event()}


@jobs.handler("uji_batal")
def _uji(ctx):
    ctx.progress(40, "Analisis AI", "mulai")
    state["started"].set()
    er._run_logged(lambda i: er._ask({"start": float(i), "end": i + 30.0, "text": "x"},
                                     [{"start": 0.0, "end": 1.0, "text": "x"}], "uji"), list(range(200)), 4, "uji")
    ctx.progress(60, "Paket", "sesudah analisis")  # pembatalan diperiksa di sini → JobCancelled
    return {}


er.an()._call_selected_llm = lambda prompt, **kw: ai.complete(prompt, **kw)
jobs.start_worker() if hasattr(jobs, "start_worker") else None
job = jobs.submit("uji_batal", {}, title="uji")
state["started"].wait(10)
time.sleep(0.2)
t0 = time.time()
jobs.cancel(job["id"])
for _ in range(200):
    if db.get_job(job["id"])["status"] != "running":
        break
    time.sleep(0.05)
elapsed = time.time() - t0
st = db.get_job(job["id"])["status"]
check(f"job berhenti cepat setelah Batal ({elapsed:.1f} dtk, status {st})", st == "cancelled" and elapsed < 5)
check("sinyal batal dilepas setelah job selesai", ai.ABORT is None)
n = len(calls)
ai.complete("x")
check("panggilan AI berikutnya normal lagi", len(calls) == n + 1)

shutil.rmtree(tmp, ignore_errors=True)
print("\nSemua uji pembatalan job lulus" if not fails else f"\nGAGAL: {len(fails)} uji")
sys.exit(1 if fails else 0)
