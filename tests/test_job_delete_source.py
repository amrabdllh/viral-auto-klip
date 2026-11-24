"""Uji "Hapus juga video sumber" saat menghapus job (studio_api), tanpa jaringan, memakai DB & cache sementara:
- job selesai: video sumber (hasil job / aset 'source') dihapus
- job gagal/dibatalkan sebelum selesai: dicari dari ID video di link, termasuk unduhan setengah jadi (.part)
- transkrip di source_cache/transcripts TIDAK PERNAH ikut terhapus
- video yang juga dipakai job berjalan/antre dilewati
- file di luar folder cache (upload pengguna) tidak disentuh
- tanpa centang → video sumber tetap

Jalankan: python tests/test_job_delete_source.py
"""
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from core import db, utils  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="jdelsrc_"))
db.DB_PATH = tmp / "studio.db"
config.OUTPUT_DIR = str(tmp / "output")
cache = tmp / "source_cache"
(cache / "transcripts").mkdir(parents=True)
utils.SOURCE_CACHE_DIR = cache

import studio_api as api  # noqa: E402

fails = []


def check(name, cond):
    print(("OK   " if cond else "GAGAL ") + name)
    if not cond:
        fails.append(name)


def make(name, size=1024):
    f = cache / name
    f.write_bytes(b"x" * size)
    return f


def job(status, url="", source=None, asset=None):
    jid = db.insert_job("clip_quick", "uji", {"url": url} if url else {})
    fields = {"status": status}
    if source:
        fields["result"] = {"source": str(source)}
    db.update_job(jid, **fields)
    if asset:
        db.add_asset("source", str(asset), name="src", job_id=jid)
    return jid


transcript = cache / "transcripts" / "abc.json"
transcript.write_text("{}", encoding="utf-8")
try:
    # 1) job selesai, sumber dari hasil job
    a = make("AAAAAAAAAAA_1080.mp4", 5000)
    j1 = job("done", "https://www.youtube.com/live/AAAAAAAAAAA?si=x", source=a)
    r = api.delete_job(j1, source=True)
    check("job selesai: video sumber dihapus", not a.exists() and r["source_freed"] == 5000)
    check("job ikut terhapus dari daftar", db.get_job(j1) is None)
    check("transkrip tidak ikut terhapus", transcript.exists())

    # 2) job dibatalkan sebelum selesai: hanya ada link → cari dari ID video, termasuk .part
    b, bpart = make("BBBBBBBBBBB_1080.mp4"), make("BBBBBBBBBBB_1080.f137.mp4.part")
    other = make("CCCCCCCCCCC_1080.mp4")
    j2 = job("cancelled", "https://youtu.be/BBBBBBBBBBB")
    api.delete_job(j2, source=True)
    check("job dibatalkan: video & sisa unduhan dari ID link dihapus", not b.exists() and not bpart.exists())
    check("video job lain tidak tersentuh", other.exists())

    # 3) sumber yang sama dipakai job yang masih antre → dilewati
    d = make("DDDDDDDDDDD_1080.mp4")
    j3 = job("error", "https://www.youtube.com/watch?v=DDDDDDDDDDD", asset=d)
    job("queued", "https://www.youtube.com/watch?v=DDDDDDDDDDD")
    r = api.delete_job(j3, source=True)
    check("dipakai job antre → tidak dihapus & dilaporkan", d.exists() and r["source_kept"] and "antre" in r["source_kept"][0])

    # 4) tanpa centang
    e = make("EEEEEEEEEEE_1080.mp4")
    j4 = job("done", "https://youtu.be/EEEEEEEEEEE", source=e)
    api.delete_job(j4, source=False)
    check("tanpa centang → video sumber tetap", e.exists())

    # 5) upload pengguna di luar cache tidak disentuh
    up = tmp / "upload_saya.mp4"
    up.write_bytes(b"y" * 10)
    j5 = job("done", source=up, asset=up)
    api.delete_job(j5, source=True)
    check("file di luar source_cache tidak disentuh", up.exists())

    # 6) massal
    f1, f2 = make("FFFFFFFFFFF_1080.mp4", 100), make("GGGGGGGGGGG_1080.mp4", 200)
    ids = [job("done", "https://youtu.be/FFFFFFFFFFF", source=f1), job("interrupted", "https://youtu.be/GGGGGGGGGGG")]
    r = api.bulk_jobs(api.BulkJobRequest(ids=ids, action="delete", source=True))
    check("hapus massal: semua video sumber dihapus", not f1.exists() and not f2.exists() and r["source_freed"] == 300)
    check("hapus massal: job terhapus", len(r["done"]) == 2 and all(db.get_job(i) is None for i in ids))
    check("transkrip masih ada di akhir", transcript.exists())
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\nSemua uji hapus video sumber lulus" if not fails else f"\nGAGAL: {len(fails)} uji")
sys.exit(1 if fails else 0)
