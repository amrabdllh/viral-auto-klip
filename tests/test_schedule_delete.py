"""Uji tombol di tabel Jadwal Upload (hapus / upload sekarang / jeda / antrekan lagi), tanpa jaringan:
- API: hapus jadwal yang ada → hilang; ID yang tidak ada (mis. "undefined") → 404, bukan 200 diam-diam
- Frontend: tombol mengambil ID dari .sched-line[data-id] (dulu dari <tr> yang tak punya data-id → "undefined")

Jalankan: python tests/test_schedule_delete.py
"""
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from core import db  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="schdel_"))
db.DB_PATH = tmp / "studio.db"  # database sementara, bukan data/studio.db asli
config.OUTPUT_DIR = str(tmp)

import studio_api as api  # noqa: E402
from fastapi import HTTPException  # noqa: E402

fails = []


def check(name, cond):
    print(("OK   " if cond else "GAGAL ") + name)
    if not cond:
        fails.append(name)


try:
    a = db.add_schedule(str(tmp / "a.mp4"), "A", "", ["youtube"], 4102444800)
    b = db.add_schedule(str(tmp / "a.mp4"), "A", "", ["tiktok"], 4102444800)
    r = api.delete_schedule(a["id"])
    check("hapus jadwal → hilang dari daftar", r["ok"] and db.get_schedule(a["id"]) is None)
    check("jadwal platform lain untuk klip sama tetap ada", db.get_schedule(b["id"]) is not None)
    try:
        api.delete_schedule("undefined")
        check("ID tidak dikenal → 404", False)
    except HTTPException as e:
        check("ID tidak dikenal → 404", e.status_code == 404)

    # --- aksi massal
    ids = {}
    for st in ("pending", "paused", "failed", "scheduled", "done", "uploading"):
        s = db.add_schedule(str(tmp / f"{st}.mp4"), st, "", ["youtube"], 4102444800)
        db.update_schedule(s["id"], status=st)
        ids[st] = s["id"]
    r = api.batch_schedules(api.ScheduleBatch(ids=[ids["pending"], ids["scheduled"]], action="pause"))
    check("jeda massal: hanya yang menunggu", r["done"] == [ids["pending"]] and db.get_schedule(ids["pending"])["status"] == "paused")
    r = api.batch_schedules(api.ScheduleBatch(ids=[ids["pending"], ids["failed"], ids["done"]], action="resume"))
    check("antrekan lagi massal: dijeda & gagal → menunggu", set(r["done"]) == {ids["pending"], ids["failed"]}
          and db.get_schedule(ids["failed"])["status"] == "pending")
    r = api.batch_schedules(api.ScheduleBatch(ids=list(ids.values()) + ["hilang"], action="delete"))
    check("hapus massal: semua kecuali yang sedang diunggah", len(r["done"]) == 5
          and db.get_schedule(ids["uploading"]) is not None and all(db.get_schedule(ids[s]) is None for s in ids if s != "uploading"))
    check("hapus massal: alasan dilewati dilaporkan", {s["reason"] for s in r["skipped"]} == {"sedang diunggah", "tidak ditemukan"})
    try:
        api.batch_schedules(api.ScheduleBatch(ids=[], action="rusak"))
        check("aksi tidak dikenal → 400", False)
    except HTTPException as e:
        check("aksi tidak dikenal → 400", e.status_code == 400)

    # --- panel "Jam upload hari ini" memakai aturan dari Pengaturan
    from datetime import datetime
    from core import autopublish
    config.AUTO_PUBLISH_PER_DAY, config.AUTO_PUBLISH_MIN_GAP_H = 4, 2.5
    config.TIKTOK_POST_HOURS = "09:45,12:45,18:45,20:45"
    wed = datetime(2026, 11, 25)
    r = autopublish.rule_for_day("gaming", "tiktok", wed)
    check("jam tetap TikTok dari Pengaturan ditampilkan apa adanya",
          r["source"] == "akun" and [h["time"] for h in r["hours"]] == ["09:45", "12:45", "18:45", "20:45"])
    config.YOUTUBE_POST_HOURS, config.UPLOAD_SLOT_MODE = "", "jangkar"
    db.kv_set("slot_anchors_gaming_youtube_weekday_4_2.5",
              {"at": __import__("time").time(), "hours": [[12.0, 80], [16.0, 90], [18.5, 95], [21.0, 100]]})
    r = autopublish.rule_for_day("gaming", "youtube", wed)
    check("mode jangkar: jam jangkar tersimpan dipakai (maks 4)", r["source"] == "jangkar" and len(r["hours"]) == 4
          and not r["pending"])
    config.UPLOAD_SLOT_MODE = "riset"
    r = autopublish.rule_for_day("gaming", "youtube", wed)
    check("mode riset tanpa data pasar: tidak memicu riset, ditandai belum dihitung", r["pending"] and not r["hours"])
finally:
    shutil.rmtree(tmp, ignore_errors=True)

js = (ROOT / "web" / "pages.js").read_text(encoding="utf-8")
handler = js[js.index('$("#tb").onclick = async (e) => {', js.index("Daftar jadwal")):][:1500]
check("tombol jadwal mengambil ID dari [data-id]", 'closest("[data-id]")' in handler)
check("tidak lagi mengambil ID dari <tr>", 'closest("tr").dataset.id' not in handler)
check("baris per platform punya data-id", re.search(r'class="sched-line" data-id="\$\{s\.id\}"', js) is not None)

print("\nSemua uji hapus jadwal lulus" if not fails else f"\nGAGAL: {len(fails)} uji")
sys.exit(1 if fails else 0)
