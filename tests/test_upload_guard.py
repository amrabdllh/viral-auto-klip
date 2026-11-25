"""Uji pengaman anti-dobel unggahan, tanpa jaringan (DB sementara, pengunggah palsu):
- unggahan yang berhasil tercatat di riwayat per (klip, platform) — tetap ada walau jadwalnya dihapus dari daftar
- klip yang sudah pernah diunggah tidak diunggah lagi (pengunggah tidak dipanggil), kecuali riwayatnya dilupakan
- unggahan klip yang sama tidak bisa berjalan bersamaan (kunci per klip)
- "Upload sekarang" ditolak (409) saat klip sedang diunggah / sudah terunggah
- jadwal satuan & "Jadwalkan semua" melewati klip yang sudah diunggah; pilihan "unggah ulang" mengizinkan lagi

Jalankan: python tests/test_upload_guard.py
"""
import shutil
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from core import db  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="upguard_"))
db.DB_PATH = tmp / "studio.db"
config.OUTPUT_DIR = str(tmp)

from core import autopublish as ap, scheduler, thumb_ai, tiktok_browser as tb  # noqa: E402
from fastapi import HTTPException  # noqa: E402

fails = []


def check(name, cond):
    print(("OK   " if cond else "GAGAL ") + name)
    if not cond:
        fails.append(name)


calls = []
tb.cookie_status = lambda: {"configured": True}
tb.upload_video = lambda path, **k: (calls.append(path), {"ok": True, "status": "scheduled", "url": "https://tiktok/1",
                                                          "description": k.get("description")})[1]
thumb_ai.upload_image_for = lambda path: None
config.TIKTOK_POST_HOURS, config.TIKTOK_POST_HOURS_WEEKEND = "10:00,14:00,19:00", ""
config.YOUTUBE_POST_HOURS, config.YOUTUBE_POST_HOURS_WEEKEND = "11:00,15:00,20:00", ""  # tanpa riset jaringan
config.AUTO_PUBLISH_PER_DAY, config.AUTO_PUBLISH_MIN_GAP_H = 3, 2.5
ap.ready_platforms = lambda wanted=None: list(wanted or ["tiktok"])
scheduler.platform_status = lambda: {pf: {"ready": True, "label": pf} for pf in ("youtube", "tiktok")}
import studio_api as api  # noqa: E402

try:
    clip = tmp / "clip_a.mp4"
    clip.write_bytes(b"x")
    item = db.add_schedule(str(clip), "Judul A", "Caption.\n\n#GTA6", ["tiktok"], 4102444800)

    r1 = scheduler.upload_now(item, publish_at=4102444800)
    check("unggahan pertama jalan & tercatat di riwayat", len(calls) == 1 and scheduler.uploaded(str(clip), "tiktok"))
    db.delete_schedule(item["id"])
    check("riwayat tetap ada setelah jadwal dihapus dari daftar", scheduler.uploaded(str(clip), "tiktok") is not None)

    item2 = db.add_schedule(str(clip), "Judul A", "Caption.", ["tiktok"], 4102444800)
    r2 = scheduler.upload_now(item2)
    check("klip yang sama tidak diunggah lagi (pengunggah tidak dipanggil)",
          len(calls) == 1 and r2["tiktok"]["status"] == "duplicate" and scheduler._ok(r2))

    scheduler.forget_upload(str(clip), "tiktok")
    assert scheduler._claim(str(clip), "tiktok")
    r3 = scheduler.upload_now(item2)
    check("klip yang sedang diunggah tidak diunggah bersamaan", len(calls) == 1 and r3["tiktok"]["status"] == "failed"
          and "sedang diunggah" in r3["tiktok"]["reason"])
    try:
        api.run_schedule_now(item2["id"])
        check("Upload sekarang ditolak saat klip sedang diunggah", False)
    except HTTPException as e:
        check("Upload sekarang ditolak saat klip sedang diunggah (409)", e.status_code == 409)
    scheduler._release(str(clip), "tiktok")

    r4 = api.run_schedule_now(item2["id"])
    check("setelah selesai, Upload sekarang jalan normal", len(calls) == 2 and r4["status"] == "done")
    item3 = db.add_schedule(str(clip), "Judul A", "Caption.", ["tiktok"], 4102444800)
    try:
        api.run_schedule_now(item3["id"])
        check("Upload sekarang ditolak untuk klip yang sudah diunggah", False)
    except HTTPException as e:
        check("Upload sekarang ditolak untuk klip yang sudah diunggah (409)", e.status_code == 409 and "sudah pernah" in e.detail)
    db.delete_schedule(item3["id"])
    db.delete_schedule(item2["id"])

    # --- jadwal satuan
    asset = db.add_asset("clip", str(clip), name="A", meta={"ai_title": "Judul A", "hashtags": ["#GTA6"]})
    try:
        api.add_schedule(api.ScheduleRequest(asset_id=asset["id"], platforms=["tiktok"], run_at=4102444800))
        check("jadwal satuan ditolak untuk klip yang sudah diunggah", False)
    except HTTPException as e:
        check("jadwal satuan ditolak untuk klip yang sudah diunggah (409)", e.status_code == 409)
    s = api.add_schedule(api.ScheduleRequest(asset_id=asset["id"], platforms=["tiktok"], run_at=4102444800, reupload=True))
    check("'Unggah ulang' mengizinkan & melupakan riwayat", s and scheduler.uploaded(str(clip), "tiktok") is None)
    db.delete_schedule(s["id"])
    scheduler.record_upload(str(clip), "tiktok", {"status": "scheduled", "url": "https://tiktok/1"})

    # --- Jadwalkan semua
    clip_b = tmp / "clip_b.mp4"
    clip_b.write_bytes(b"x")
    asset_b = db.add_asset("clip", str(clip_b), name="B", meta={"ai_title": "Judul B", "hashtags": ["#GTA6"]})
    start = (datetime.now() + timedelta(days=2)).replace(hour=0, minute=0).timestamp()
    req = api.BulkScheduleRequest(asset_ids=[asset["id"], asset_b["id"]], platforms=["tiktok"], start_at=start,
                                  mode="settings", preview=True)
    r = api.bulk_schedule(req)
    check(f"pratinjau: klip yang sudah diunggah dilaporkan & dilewati ({r['clips']} klip)",
          r["clips"] == 1 and len(r["uploaded"]) == 1 and r["uploaded"][0]["platforms"] == ["tiktok"])
    req.preview = False
    api.bulk_schedule(req)
    rows = db.list_schedules()
    check("simpan: hanya klip yang belum diunggah", len(rows) == 1 and rows[0]["file_path"] == str(clip_b))
    req.reupload = True
    r = api.bulk_schedule(req)
    rows = db.list_schedules()
    check("unggah ulang: klip yang sudah diunggah ikut dijadwalkan & riwayat dilupakan",
          len(rows) == 2 and scheduler.uploaded(str(clip), "tiktok") is None)

    # sebagian platform: sudah di TikTok, belum di YouTube → hanya YouTube yang dijadwalkan
    for s in db.list_schedules():
        db.delete_schedule(s["id"])
    scheduler.record_upload(str(clip), "tiktok", {"status": "scheduled"})
    ap.ready_platforms = lambda wanted=None: list(wanted or ["youtube", "tiktok"])
    req = api.BulkScheduleRequest(asset_ids=[asset["id"]], platforms=["youtube", "tiktok"], start_at=start, mode="settings")
    api.bulk_schedule(req)
    check("klip yang sudah di TikTok hanya dijadwalkan ke YouTube",
          [s["platforms"] for s in db.list_schedules()] == [["youtube"]])
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\nSemua uji pengaman anti-dobel lulus" if not fails else f"\nGAGAL: {len(fails)} uji")
sys.exit(1 if fails else 0)
