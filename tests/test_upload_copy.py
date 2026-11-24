"""Uji teks upload & penjadwalan massal, tanpa jaringan (DB sementara):
- judul pendek ala thumbnail, caption 1–2 kalimat utuh (tidak terpotong di tengah kalimat)
- riset hashtag: hashtag mati dibuang, topik aktif diutamakan, #fyp/#shorts tidak memenuhi judul, pilihan pengguna tetap
- YouTube: maks 5 hashtag di judul & ≤ 100 karakter; TikTok: judul + hashtag saja
- TikTok: hashtag diketik & dipilih dari saran yang sama persis (bukan ditempel) → bisa diklik
- jam upload manual Senin–Jumat vs Sabtu–Minggu
- "Jadwalkan semua" ikut jam Pengaturan: 26 klip, 4/hari → 7 hari (6×4 + 2), slot terisi & klip terjadwal dilewati

Jalankan: python tests/test_upload_copy.py
"""
import shutil
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from core import db  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="upcopy_"))
db.DB_PATH = tmp / "studio.db"
config.OUTPUT_DIR = str(tmp)

from core import autopublish as ap, hashtag_research as hr, scheduler, tiktok_browser as tb  # noqa: E402

fails = []


def check(name, cond):
    print(("OK   " if cond else "GAGAL ") + name)
    if not cond:
        fails.append(name)


def seed(tag, videos):
    db.kv_set(f"hashtag_stats_{tag.lower()}", {"videos": videos, "channels": None, "dead": videos is None, "at": time.time()})


try:
    # --- judul & caption
    check("judul pendek dibiarkan", ap.short_title("Lucia Malah Gendong Jason?! 😭") == "Lucia Malah Gendong Jason?! 😭")
    long_t = '"Nggak Gua Bunuh" — Gua Cuma Tembak Tangannya Biar Dia Nyerah Tanpa Perlawanan Sama Sekali'
    st = ap.short_title(long_t)
    check(f"judul panjang dipotong di jeda alami ({st!r})", len(st) <= 60 and st.startswith('"Nggak Gua Bunuh"'))
    check("hashtag dibuang dari judul", "#" not in ap.short_title("Judul seru #GTA6 #fyp"))
    cap = ap.short_caption('Tadi bilang gelap, sekarang terang… mau yang mana? 😭 Dibolak-balik sampai akhirnya "Terang aja '
                           'ya." Kalian tim mana? #GTA6 #fyp')
    check(f"caption = kalimat utuh, ≤ 90, tanpa hashtag ({cap!r})", len(cap) <= 90 and cap.endswith("?") and "#" not in cap)

    # --- riset hashtag (data dari cache, tanpa jaringan)
    for t, v in {"GTA6": 8_300_000, "GTAVI": 3_100_000, "gamingindonesia": 493_000, "reaksilucu": 34_000,
                 "fyp": 217_000_000, "klipstreamerindo": None, "gaming": 192_000_000}.items():
        seed(t, v)
    got = hr.pick(["#GTAVI", "#GTA6", "#klipstreamerindo", "#reaksilucu", "#fyp", "#gaming", "#gamingindonesia"], 5,
                  fetch=False)
    check(f"hashtag mati dibuang ({got})", "#klipstreamerindo" not in got)
    check("topik spesifik di atas hashtag umum", got[:2] == ["#GTAVI", "#GTA6"] and got.index("#gamingindonesia") < got.index("#gaming"))
    check("#fyp hanya mengisi sisa slot", "#fyp" not in got)
    check("hashtag pilihan pengguna tidak dibuang walau mati",
          "#klipstreamerindo" in hr.pick(["#klipstreamerindo", "#GTA6"], 5, keep=["klipstreamerindo"], fetch=False))
    check("tanda baca/spasi dibersihkan", hr.clean("#GTA 6!") == "GTA6")

    clip = {"file_path": "x.mp4", "ai_title": "KIRA PESAWAT JATUH padahal ternyata sudah sampai?! 🤣",
            "upload_caption": "KOK JATUH BRO?! Nggak mau naik, ditebak gara-gara berasep. Kamu tahu?",
            "hashtags": ["#GTAVI", "#GTA6", "#klipstreamerindo", "#reaksilucu", "#fyp"], "niche": "gaming"}
    cp = ap.copy_for(clip, research=False)
    check(f"deskripsi: caption + 5 hashtag hidup ({cp['hashtags']})", len(cp["hashtags"]) == 5
          and "#klipstreamerindo" not in cp["hashtags"])
    config.GLOBAL_HASHTAGS = "#namachannel"
    cp2 = ap.copy_for(clip, research=False)
    check("hashtag global pengguna selalu ikut", "#namachannel" in cp2["hashtags"] and len(cp2["hashtags"]) <= 5)
    config.GLOBAL_HASHTAGS = ""

    yt = scheduler.platform_copy("youtube", cp["title"], cp["description"])
    tt = scheduler.platform_copy("tiktok", cp["title"], cp["description"])
    n_tags = yt["title"].count("#")
    check(f"YouTube: hashtag di judul (1–5) & ≤ 100 karakter ({yt['title']!r})", 1 <= n_tags <= 5 and len(yt["title"]) <= 100)
    check("TikTok: judul + hashtag saja", tt["description"].startswith(cp["title"]) and "KOK JATUH" not in tt["description"])
    many = scheduler.platform_copy("youtube", "Judul", "x #a1 #a2 #a3 #a4 #a5 #a6 #a7")
    check("YouTube: maks 5 hashtag di judul", many["title"].count("#") == 5)

    # --- TikTok: hashtag dipilih dari saran
    plain, tags = tb._split_caption("Judul seru 😭 #GTA6 #gamingindonesia")
    check("caption dipecah: teks + hashtag", plain == "Judul seru 😭" and tags == ["#GTA6", "#gamingindonesia"])

    class FakeKb:
        def __init__(self, log): self.log = log
        def type(self, text, delay=0): self.log.append(("type", text))
        def press(self, key): self.log.append(("press", key))

    class FakePage:
        """Meniru editor TikTok: daftar saran = [[#tag, 'N posts'], ...] (struktur yang diamati di TikTok Studio)."""
        def __init__(self, items): self.items, self.log = items, []; self.keyboard = FakeKb(self.log)
        def evaluate(self, js): return self.items
        def wait_for_timeout(self, ms): self.log.append(("wait", ms))

    p = FakePage([["#gta6trailer", "266K posts"], ["#GTA6", "7.6M posts"], ["#gta6new", "135K posts"]])
    r = tb._type_hashtag(p, "#GTA6")
    keys = [k for k in p.log if k[0] != "wait"]
    check("'#' diketik dulu lalu jeda sebelum nama tag (agar saran TikTok muncul)",
          keys[0] == ("type", "#") and p.log[1][0] == "wait" and p.log[1][1] >= 500 and keys[1] == ("type", "GTA6"))
    check("saran yang SAMA PERSIS dipilih lewat panah bawah + Enter",
          r["picked"] and keys[2:] == [("press", "ArrowDown"), ("press", "Enter")] and r["posts"] == "7.6M posts")
    p = FakePage([["#gtavicecity", "900K posts"], ["#gtavi6", "10K posts"]])
    r = tb._type_hashtag(p, "#GTAVI")
    check("saran yang hanya mirip TIDAK dipilih (#GTAVI ≠ #gtavicecity)",
          not r["picked"] and ("press", "Enter") not in p.log and ("type", " ") in p.log)
    p = FakePage([])
    check("tanpa saran → tetap teks (tidak gagal upload)", tb._type_hashtag(p, "#langka")["picked"] is False)

    # --- jam manual Senin–Jumat / Sabtu–Minggu
    config.TIKTOK_POST_HOURS, config.TIKTOK_POST_HOURS_WEEKEND = "09:45,12:45,18:45,20:45", "11:00,15:00,19:00,21:30"
    wed, sat = datetime(2026, 11, 25), datetime(2026, 11, 28)
    check("Rabu pakai jam Senin–Jumat", ap.fixed_hours("tiktok", wed) == [9.75, 12.75, 18.75, 20.75])
    check("Sabtu pakai jam Sabtu–Minggu", ap.fixed_hours("tiktok", sat) == [11.0, 15.0, 19.0, 21.5])
    r_sat = ap.rule_for_day("gaming", "tiktok", sat)
    check("panel menampilkan jam akhir pekan", [h["time"] for h in r_sat["hours"]][0] == "11:00" and "Sabtu" in r_sat["label"])
    config.TIKTOK_POST_HOURS_WEEKEND = ""
    check("akhir pekan kosong → sama dengan Senin–Jumat", ap.fixed_hours("tiktok", sat) == [9.75, 12.75, 18.75, 20.75])

    # --- Jadwalkan semua: ikut jam Pengaturan
    config.YOUTUBE_POST_HOURS, config.YOUTUBE_POST_HOURS_WEEKEND = "10:00,13:00,17:00,20:00", ""
    config.AUTO_PUBLISH_PER_DAY, config.AUTO_PUBLISH_MIN_GAP_H = 4, 2.5
    ap.ready_platforms = lambda wanted=None: list(wanted or ["youtube", "tiktok"])
    scheduler.platform_status = lambda: {pf: {"ready": True, "label": pf} for pf in ("youtube", "tiktok")}
    import studio_api as api  # noqa: E402
    assets = []
    for i in range(26):
        f = tmp / f"clip_{i:02d}.mp4"
        f.write_bytes(b"x")
        assets.append(db.add_asset("clip", str(f), name=f"Klip {i}", meta={"ai_title": f"Klip seru nomor {i}", "score": 90 - i,
                                                                         "hashtags": ["#GTA6"], "niche": "gaming"}))
    start = (datetime.now() + timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    req = api.BulkScheduleRequest(asset_ids=[a["id"] for a in assets], platforms=["youtube", "tiktok"], start_at=start,
                                  mode="settings", preview=True)
    r = api.bulk_schedule(req)
    check(f"26 klip, 4/hari → 7 hari ({[d['clips'] for d in r['days']]})",
          r["clips"] == 26 and [d["clips"] for d in r["days"]] == [4, 4, 4, 4, 4, 4, 2])
    check("pratinjau tidak menyimpan apa pun", not db.list_schedules())
    d0 = r["days"][0]
    tt_times = d0["slots"]["tiktok"]
    check(f"jam TikTok mengikuti Pengaturan ({tt_times})", all(any(abs((int(t[:2]) * 60 + int(t[3:])) - (h * 60 + m)) <= 16
                                                                  for h, m in ((9, 45), (12, 45), (18, 45), (20, 45)))
                                                              for t in tt_times))
    req.preview = False
    r = api.bulk_schedule(req)
    rows = db.list_schedules()
    check("simpan: 52 jadwal (26 klip × 2 platform)", len(rows) == 52)
    top = min(rows, key=lambda s: s["run_at"])
    check("klip skor tertinggi dapat jam paling awal", top["file_path"].endswith("clip_00.mp4"))
    r2 = api.bulk_schedule(req)
    check("dijadwalkan lagi → klip yang sudah terjadwal dilewati", r2["already"] == 26 and len(db.list_schedules()) == 52)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\nSemua uji teks upload & jadwal massal lulus" if not fails else f"\nGAGAL: {len(fails)} uji")
sys.exit(1 if fails else 0)
