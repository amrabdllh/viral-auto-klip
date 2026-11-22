"""Uji pra-unggah dengan jadwal bawaan platform (core/scheduler.preupload_due) tanpa jaringan:
- jadwal ≥25 menit lagi diunggah sekarang dengan publish_at = waktu jadwal → status 'scheduled'
- jadwal yang terlalu dekat / terlalu jauh tidak disentuh
- batas upload harian YouTube → pra-unggah YouTube ditunda, item tetap 'pending' tanpa tanda gagal
- 'scheduled' berubah 'done' setelah waktunya lewat; scheduler tidak mengunggah ulang

Jalankan: python tests/test_preupload.py
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from core import db, scheduler, tiktok_browser  # noqa: E402

NOW = time.time()
store = {}
kv = {}


def item(sid, pf, run_at, status="pending"):
    store[sid] = {"id": sid, "file_path": __file__, "title": sid, "description": "", "platforms": [pf],
                  "run_at": run_at, "status": status, "result": {}}


def upd(sid, **f):
    store[sid].update(f)


db.list_schedules = lambda: list(store.values())
db.update_schedule = upd
db.due_schedules = lambda now: [s for s in store.values() if s["status"] == "pending" and s["run_at"] <= now]
db.kv_get = lambda k, d=None: kv.get(k, d)
db.kv_set = lambda k, v: kv.__setitem__(k, v)
tiktok_browser.cookie_status = lambda: {"configured": True}
config.UPLOAD_NATIVE_SCHEDULE = True
config.PREUPLOAD_DAYS = 10
config.TIKTOK_UPLOAD_MODE = "post"

calls = []
limit = {"youtube": False}


def fake_upload(it, publish_at=None):
    pf = it["platforms"][0]
    calls.append((it["id"], publish_at))
    if limit.get(pf):
        return {pf: {"status": "failed", "reason": "Upload browser gagal: Batas upload harian YouTube tercapai"}}
    return {pf: {"status": "scheduled", "ok": True, "publish_at": publish_at}}


scheduler.upload_now = fake_upload

fails = []


def check(name, cond):
    print(("OK   " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)


item("dekat", "youtube", NOW + 10 * 60)          # < 25 mnt → diunggah biasa saat jamnya
item("yt1", "youtube", NOW + 3 * 3600)
item("tt1", "tiktok", NOW + 4 * 3600)
item("jauh", "tiktok", NOW + 12 * 86400)          # > 10 hari → nanti
item("yt2", "youtube", NOW + 5 * 3600)

for _ in range(6):
    scheduler._last_preupload = 0.0
    scheduler.preupload_due()

check("yt1 terjadwal di platform", store["yt1"]["status"] == "scheduled")
check("tt1 terjadwal di platform", store["tt1"]["status"] == "scheduled")
check("publish_at = waktu jadwal", dict(calls)["yt1"] == store["yt1"]["run_at"])
check("jadwal < 25 mnt tidak dipra-unggah", store["dekat"]["status"] == "pending" and "dekat" not in dict(calls))
check("jadwal > 10 hari tidak dipra-unggah", store["jauh"]["status"] == "pending" and "jauh" not in dict(calls))

# batas upload harian YouTube
item("yt3", "youtube", NOW + 6 * 3600)
item("yt4", "youtube", NOW + 7 * 3600)
limit["youtube"] = True
calls.clear()
for _ in range(4):
    scheduler._last_preupload = 0.0
    scheduler.preupload_due()
check("batas harian: hanya 1 percobaan, lalu ditunda", [c[0] for c in calls] == ["yt3"])
check("batas harian: item tetap pending", store["yt3"]["status"] == "pending" and store["yt4"]["status"] == "pending")
check("batas harian: tanpa tanda gagal di item", not (store["yt3"].get("result") or {}).get("_preupload_error_at"))
check("batas harian: status platform menampilkan waktu lanjut", scheduler.preupload_block("youtube") > NOW)

# yang sudah dipegang platform → done setelah waktunya, tidak diunggah ulang
store["yt1"]["run_at"] = NOW - 60
calls.clear()
scheduler._last_preupload = time.time()  # jeda: tick tidak memicu pra-unggah baru
scheduler.tick()
check("scheduled → done setelah waktunya", store["yt1"]["status"] == "done")
check("tidak diunggah ulang", not calls)

print("\nSemua uji pra-unggah lulus" if not fails else f"\nGAGAL: {len(fails)} uji")
sys.exit(1 if fails else 0)
