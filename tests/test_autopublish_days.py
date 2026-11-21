"""Uji upload otomatis multi-hari: slot kosong hari yang sudah sebagian terisi dipakai dulu, sisanya pindah ke hari
berikutnya, maks per hari dihormati, jarak antar upload dijaga, tiap hari diriset (tanpa jaringan: data pasar dimatikan).

Jalankan: python tests/test_autopublish_days.py
"""
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from core import autopublish, db, fyp_research, slot_learning  # noqa: E402

store = []
db.list_schedules = lambda: store


def _add(fp, title, desc, pfs, ts):
    item = {"id": str(len(store)), "file_path": fp, "title": title, "platforms": pfs, "run_at": ts, "status": "pending"}
    store.append(item)
    return item


db.add_schedule = _add
fyp_research.market_samples = lambda niche, force=False: []          # tanpa jaringan
slot_learning.insights = lambda platform: {"ready": False, "total": 0, "median": 0, "hours": {}}
autopublish.ready_platforms = lambda wanted=None: list(wanted or ["youtube"])
config.AUTO_PUBLISH_PER_DAY = 5
config.AUTO_PUBLISH_MIN_GAP_H = 2.5

START = datetime(2026, 11, 22, 0, 30).timestamp()  # Minggu 00:30
clips = lambda n, tag: [{"file_path": f"{tag}{i}.mp4", "score": 90 - i, "ai_title": f"{tag}{i}", "niche": "gaming"}
                        for i in range(n)]

a = autopublish.schedule_clips(clips(8, "A"), platforms=["youtube"], content_type="gaming_stream", start=START)
per_day_a = Counter(datetime.fromtimestamp(s["run_at"]).date() for s in store)
assert len(a["scheduled"]) == 8, a
days = sorted(per_day_a)
assert per_day_a[days[0]] == 5 and per_day_a[days[1]] == 3, per_day_a
assert a["plans"] and "riset" in a["plans"][0], a["plans"]

b = autopublish.schedule_clips(clips(12, "B"), platforms=["youtube"], content_type="gaming_stream", start=START)
per_day = Counter(datetime.fromtimestamp(s["run_at"]).date() for s in store)
assert len(b["scheduled"]) == 12, b
assert all(v <= 5 for v in per_day.values()), per_day
assert per_day[days[1]] == 5, f"hari ke-2 harus diisi 2 slot tersisa: {per_day}"
b_day2 = [s for s in store if s["title"].startswith("B") and datetime.fromtimestamp(s["run_at"]).date() == days[1]]
assert len(b_day2) == 2, b_day2
assert "+3 sudah terjadwal" in b["plans"][0], b["plans"]

times = sorted(s["run_at"] for s in store)
assert all(t2 - t1 >= 2.5 * 3600 - 6 * 60 for t1, t2 in zip(times, times[1:])), "jarak antar upload < 2,5 jam (toleransi menit acak 6 mnt)"
assert all(s["run_at"] > START for s in store)
# klip skor tertinggi batch A dapat slot paling awal
first_a = min((s for s in store if s["title"].startswith("A")), key=lambda s: s["run_at"])
assert first_a["title"] == "A0", first_a

# jam berbeda per hari (riset per hari), bukan satu jadwal datar
hours_by_day = {}
for s in store:
    d = datetime.fromtimestamp(s["run_at"])
    hours_by_day.setdefault(d.weekday(), set()).add(d.hour)
assert len({frozenset(v) for v in hours_by_day.values()}) >= 2, hours_by_day

# --- dua platform: satu klip diunggah SEKALI per platform, di HARI YANG SAMA; ≤ 5 klip per hari per platform
store.clear()
c = autopublish.schedule_clips(clips(13, "C"), platforms=["youtube", "tiktok"], content_type="gaming_stream", start=START)
assert len(c["scheduled"]) == 26, len(c["scheduled"])
per = Counter((s["file_path"], s["platforms"][0]) for s in store)
assert all(v == 1 for v in per.values()), "klip yang sama terjadwal dua kali di platform yang sama"
for fp in {s["file_path"] for s in store}:
    days_fp = {datetime.fromtimestamp(s["run_at"]).date() for s in store if s["file_path"] == fp}
    assert len(days_fp) == 1, f"{fp} diunggah di hari berbeda per platform: {days_fp}"
per_pf_day = Counter((datetime.fromtimestamp(s["run_at"]).date(), s["platforms"][0]) for s in store)
assert all(v <= 5 for v in per_pf_day.values()), per_pf_day
for pf in ("youtube", "tiktok"):
    t = sorted(s["run_at"] for s in store if s["platforms"][0] == pf)
    assert all(b - a >= 2.5 * 3600 - 6 * 60 for a, b in zip(t, t[1:])), pf
# urutan klip sama di kedua platform (klip terbaik paling awal di keduanya)
order = {pf: [s["file_path"] for s in sorted((s for s in store if s["platforms"][0] == pf), key=lambda s: s["run_at"])]
         for pf in ("youtube", "tiktok")}
assert order["youtube"] == order["tiktok"], order

print("Semua uji upload otomatis multi-hari lulus")
for line in a["plans"] + b["plans"]:
    print(" ", line)
