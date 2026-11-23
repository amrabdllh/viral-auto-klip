"""Uji Resep + Auto Pilot (core/autopilot) dan momen spesifik + rentang waktu (core/tools, core/analyzer).

Tanpa jaringan: daftar video channel & pembuatan job dipalsukan; penyimpanan memakai dict (DB asli tidak disentuh).
- resep: simpan/timpa per nama, maks 10, link/file video tidak ikut, hapus
- link channel dinormalkan; link video ditolak; channel ganda ditolak
- cek pertama hanya menandai arsip (atau memproses 1 terbaru bila diminta)
- video baru → job clip_quick dengan opsi resep + url; mode review/auto → auto_publish
- filter: durasi, jenis video/live, kata wajib/dilarang; siaran terjadwal ditunggu; maks 2 job per cek
- tick menghormati interval & saklar aktif
- parse_time / parse_focus, potong rentang waktu (FFmpeg sungguhan), aturan momen spesifik masuk prompt AI

Jalankan: python tests/test_autopilot.py
"""
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import config  # noqa: E402
from core import ffx  # noqa: E402,F401
from core import analyzer, autopilot as ap, db, tools  # noqa: E402

fails = []


def check(name, cond):
    print(("OK   " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)


store = {}
real_get, real_set = db.kv_get, db.kv_set
db.kv_get = lambda k, d=None: store.get(k, d)
db.kv_set = lambda k, v: store.__setitem__(k, v)

try:
    # ------------------------------------------------------------ resep
    r1 = ap.save_recipe("Gaming", {"url": "https://x", "path": "C:/a.mp4", "caption_style": "pill-slide", "num_clips": 5,
                                   "_tmp": 1})
    check("resep: link/file & kunci internal tidak disimpan", r1["params"] == {"caption_style": "pill-slide", "num_clips": 5})
    r1b = ap.save_recipe("gaming", {"caption_style": "stabilo"})
    check("resep: nama sama → ditimpa", r1b["id"] == r1["id"] and len(ap.list_recipes()) == 1
          and ap.get_recipe(r1["id"])["params"]["caption_style"] == "stabilo")
    for i in range(9):
        ap.save_recipe(f"R{i}", {})
    try:
        ap.save_recipe("kesebelas", {})
        check("resep: maks 10", False)
    except ValueError:
        check("resep: maks 10", True)
    try:
        ap.save_recipe("  ", {})
        check("resep: nama wajib", False)
    except ValueError:
        check("resep: nama wajib", True)

    # ------------------------------------------------------------ channel
    check("normalisasi @handle/tab", ap.normalize_channel("https://m.youtube.com/@windahbasudara/streams")
          == "https://www.youtube.com/@windahbasudara")
    check("normalisasi @handle singkat", ap.normalize_channel("@abc") == "https://www.youtube.com/@abc")
    try:
        ap.normalize_channel("https://www.youtube.com/watch?v=abc")
        check("link video ditolak", False)
    except ValueError:
        check("link video ditolak", True)

    w = ap.add_watcher("https://www.youtube.com/@demo", recipe_id=r1["id"], min_dur=600, max_dur=7200,
                       exclude="trailer", mode="review")
    try:
        ap.add_watcher("https://www.youtube.com/@demo/videos")
        check("channel ganda ditolak", False)
    except ValueError:
        check("channel ganda ditolak", True)

    feed = [{"id": "old1", "url": "https://www.youtube.com/watch?v=old1", "title": "Lama", "duration": 3000, "live_status": "",
             "tab": "videos", "channel": "Demo"}]
    jobs = []

    def fetch(url, kind):
        return list(feed)

    def submit(kind, params, title=""):
        jobs.append((kind, params, title))
        return {"id": f"job{len(jobs)}"}

    res = ap.check(w["id"], submit=submit, fetch=fetch)
    check("cek pertama: arsip hanya ditandai", not jobs and res["new"] == 0 and ap.list_watchers()[0]["primed"])

    def entry(i, title, dur=3000, ls="", tab="videos"):
        return {"id": i, "url": f"https://www.youtube.com/watch?v={i}", "title": title, "duration": dur, "live_status": ls,
                "tab": tab, "channel": "Demo"}
    feed[:0] = [entry("n4", "Video baru 4"), entry("n3", "Official trailer"), entry("n2", "Upload pendek", dur=200),
                entry("up", "Live nanti malam", dur=None, ls="is_upcoming", tab="streams"), entry("n1", "Video baru 1"),
                entry("n0", "Video baru 0")]
    res = ap.check(w["id"], submit=submit, fetch=fetch)
    check("video baru → job clip_quick (maks 2 per cek, lama dulu)",
          [j[1]["url"].rsplit("=", 1)[1] for j in jobs] == ["n0", "n1"] and all(j[0] == "clip_quick" for j in jobs))
    check("job memakai opsi resep + mode tinjau", jobs[0][1]["caption_style"] == "stabilo" and jobs[0][1]["auto_publish"] is False)
    reasons = {s["id"]: s["reason"] for s in res["skipped"]}
    check("filter: durasi & kata dilarang", "pendek" in reasons.get("n2", "") and "dilarang" in reasons.get("n3", ""))
    seen = set(ap.list_watchers()[0]["seen"])
    check("siaran terjadwal ditunggu (belum ditandai)", "up" not in seen)
    check("video lolos yang melebihi kuota → cek berikutnya", "n4" not in seen)
    ap.update_watcher(w["id"], mode="auto")
    ap.check(w["id"], submit=submit, fetch=fetch)
    check("cek berikutnya memproses sisa + mode upload otomatis", jobs[-1][1]["url"].endswith("n4") and jobs[-1][1]["auto_publish"] is True)
    hist = ap.list_watchers()[0]["history"]
    check("riwayat tercatat (diproses & dilewati)", {h["status"] for h in hist} == {"diproses", "dilewati"})

    # jenis live saja
    w2 = ap.add_watcher("https://www.youtube.com/@demo2", kind="live", process_latest=True)
    feed2 = [entry("v9", "Video biasa"), entry("l1", "Siaran", dur=None, ls="is_live", tab="streams")]
    j0 = len(jobs)
    ap.check(w2["id"], submit=submit, fetch=lambda u, k: list(feed2))
    check("proses terbaru sekarang: 1 video terbaru yang lolos filter", len(jobs) == j0 + 1 and jobs[-1][1]["url"].endswith("l1"))
    check("proses terbaru: sisanya hanya ditandai", "v9" in set(ap.list_watchers()[1]["seen"]))
    check("jenis 'live': video biasa dilewati", ap.verdict({"kind": "live"}, entry("x", "a")) == "bukan siaran"
          and ap.verdict({"kind": "video"}, entry("y", "b", ls="is_live", tab="streams")) == "bukan video biasa")

    # tick: interval & saklar
    calls = []
    real_check = ap.check
    ap.check = lambda wid, **kw: calls.append(wid) or {"submitted": []}
    ap.tick(now=time.time())
    check("tick: belum jatuh tempo → tidak dicek", calls == [])
    ap.tick(now=time.time() + 16 * 60)
    check("tick: jatuh tempo → dicek", w["id"] in calls)
    calls.clear()
    ap.update_watcher(w["id"], enabled=False)
    ap.tick(now=time.time() + 16 * 60)
    check("tick: dijeda → tidak dicek", w["id"] not in calls)
    ap.check = real_check
    ap.delete_recipe(r1["id"])
    check("hapus resep → Auto Pilot kembali ke opsi bawaan", ap.list_watchers()[0]["recipe_id"] == "")

    # ------------------------------------------------------------ momen spesifik & rentang waktu
    check("parse_time", [tools.parse_time(x) for x in ("1:02:30", "62:30", "90", "", "x")] == [3750.0, 3750.0, 90.0, None, None])
    check("parse_focus: koma/baris, unik, maks 20",
          tools.parse_focus("gol, Gol; penalti\n#kartu merah") == ["gol", "penalti", "kartu merah"]
          and len(tools.parse_focus(",".join(str(i) for i in range(40)))) == 20)
    seen_prompt = {}
    real_llm, real_cfg = analyzer._call_selected_llm, analyzer._llm_configured
    analyzer._call_selected_llm = lambda prompt, **kw: seen_prompt.setdefault("p", prompt) and '{"moments": []}' or '{"moments": []}'
    analyzer._llm_configured = lambda: True
    analyzer.FOCUS = ["penalti", "kartu merah"]
    try:
        analyzer._ai_moment_candidates([{"start": i * 5.0, "end": i * 5.0 + 4.5, "text": f"kalimat nomor {i} tentang penalti"}
                                        for i in range(40)], 200.0)
    except Exception as exc:  # noqa: BLE001
        print("  (moment finder:", exc, ")")
    finally:
        analyzer._call_selected_llm, analyzer._llm_configured = real_llm, real_cfg
        analyzer.FOCUS = []
    check("momen spesifik masuk aturan prompt AI", "penalti, kartu merah" in seen_prompt.get("p", ""))

    if shutil.which("ffmpeg"):
        tmp = Path(tempfile.mkdtemp(prefix="aprange_"))
        try:
            src = tmp / "long.mp4"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=320x240:d=90:r=30", "-f", "lavfi",
                            "-i", "sine=duration=90", "-c:v", "libx264", "-g", "30", "-c:a", "aac", "-shortest", str(src)], check=True)

            class Ctx:
                dir = tmp
                logs = []

                def log(self, m):
                    self.logs.append(m)
            ctx = Ctx()
            p = {"range_start": "0:20", "range_end": "1:00"}
            out = tools.trim_range(ctx, str(src), p)
            d = tools.ffx.probe(out)["duration"] if hasattr(tools.ffx, "probe") else 0
            check("rentang waktu: video dipotong ±40 dtk", out != str(src) and 38 <= float(d) <= 42 and p["_range_offset"] == 20)
            check("rentang terlalu pendek diabaikan", tools.trim_range(ctx, str(src), {"range_start": "10", "range_end": "15"}) == str(src))
            check("tanpa rentang → video asli", tools.trim_range(ctx, str(src), {}) == str(src))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
finally:
    db.kv_get, db.kv_set = real_get, real_set

print()
print("SEMUA LULUS" if not fails else f"{len(fails)} GAGAL: {fails}")
sys.exit(1 if fails else 0)
