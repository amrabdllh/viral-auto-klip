"""Uji terjemahan subtitle klip (core/subtitle_translate) + kredit sumber video (core/clipper.credit_filters).

- kode/nama bahasa dinormalkan; tidak menerjemahkan ke bahasa yang sama
- semua layar diterjemahkan sekaligus; timing layar tetap; jumlah kata sama → timing kata tetap
- jawaban AI tidak lengkap / error / AI belum diatur → subtitle bahasa asli (render tidak rusak)
- layar >60 dibagi beberapa panggilan AI; layar yang 'meledak' jadi paragraf dipangkas
- render_options hanya meneruskan bahasa bila beda dari bahasa video
- teks kredit: isian pengguna / 'Sumber: <channel> (YouTube)'; overlay muncul setelah hook & hilang saat CTA (FFmpeg)

Jalankan: python tests/test_subtitle_translate.py
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import config  # noqa: E402
from core import ffx  # noqa: E402,F401
from core import ai_providers as ai, clipper, subtitle_translate as st, tools  # noqa: E402

fails = []


def check(name, cond):
    print(("OK   " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)


def seg(i, text):
    words, t = [], i * 2.0
    for w in text.split():
        words.append({"word": w, "start": round(t, 2), "end": round(t + 0.3, 2)})
        t += 0.35
    return {"start": i * 2.0, "end": i * 2.0 + 1.8, "text": text, "words": words}


check("normalisasi kode & nama bahasa", st.normalize("EN") == "en" and st.normalize("indonesia") == "id" and st.normalize("xx") == "")
check("bahasa sama → tidak diterjemahkan", not st.needed("id", "id") and st.needed("id", "en") and not st.needed("", "en"))

calls = []
real_complete, real_conf = ai.complete, ai.is_configured
ai.is_configured = lambda p: True


def fake(mode):
    def _complete(prompt, system="", max_tokens=0, **kw):
        payload = json.loads(prompt.rsplit("\n\n", 1)[1])  # JSON layar ada di blok terakhir prompt
        calls.append(len(payload))
        if mode == "error":
            raise RuntimeError("rate limit")
        items = []
        for it in payload:
            if mode == "partial" and it["i"] % 2:
                continue
            t = {"halo semua apa kabar": "hello everyone how are", "ini rahasia nya": "this is the secret behind it all"}.get(
                it["t"], "EN " + it["t"])
            if mode == "long":
                t = " ".join(["kata"] * 40)
            items.append({"i": it["i"], "t": t})
        return json.dumps({"items": items})
    return _complete


try:
    segs = [seg(0, "halo semua apa kabar"), seg(1, "ini rahasia nya")]
    ai.complete = fake("ok")
    out = st.translate_segments(segs, "en", context="judul")
    check("terjemahan dipakai", [s["text"] for s in out] == ["hello everyone how are", "this is the secret behind it all"])
    check("jumlah kata sama → timing kata tetap", [w["start"] for w in out[0]["words"]] == [w["start"] for w in segs[0]["words"]])
    check("jumlah kata beda → kata dibagi di dalam layar",
          len(out[1]["words"]) == 7 and out[1]["words"][0]["start"] == 2.0 and out[1]["words"][-1]["end"] <= 3.8)
    check("timing layar tetap & ditandai bahasa", out[1]["start"] == 2.0 and out[1]["end"] == 3.8 and out[0]["lang"] == "en")
    check("input tidak diubah", segs[0]["text"] == "halo semua apa kabar")

    ai.complete = fake("partial")
    many = [seg(i, f"kalimat nomor {i}") for i in range(10)]
    check("jawaban tidak lengkap → bahasa asli", st.translate_segments(many, "en") == many)
    ai.complete = fake("error")
    check("AI error → bahasa asli", st.translate_segments(segs, "en") == segs)
    ai.is_configured = lambda p: False
    check("AI belum diatur → bahasa asli", st.translate_segments(segs, "en") == segs)
    ai.is_configured = lambda p: True
    calls.clear()
    ai.complete = fake("ok")
    big = [seg(i, f"layar {i}") for i in range(130)]
    out = st.translate_segments(big, "en")
    check("130 layar → 3 panggilan AI", calls == [60, 60, 10] and out[129]["text"] == "EN layar 129")
    ai.complete = fake("long")
    out = st.translate_segments(segs, "en")
    check("layar yang jadi paragraf dipangkas", len(out[0]["words"]) <= 12)
finally:
    ai.complete, ai.is_configured = real_complete, real_conf

ro = tools.render_options({"caption_language": "en", "language": "id"})
check("render_options: bahasa beda diteruskan", ro["caption_language"] == "en")
check("render_options: bahasa sama tidak", tools.render_options({"caption_language": "id", "language": "id"})["caption_language"] == "")

check("kredit: isian pengguna", tools.credit_text({"source_credit": True, "source_credit_text": "Klip dari @abc"}, "X") == "Klip dari @abc")
check("kredit: dari channel + situs", tools.credit_text({"source_credit": True, "url": "https://youtu.be/x"}, "Windah Basudara")
      == "Sumber: Windah Basudara (YouTube)")
check("kredit: mati / file lokal → kosong", tools.credit_text({"url": "https://youtu.be/x"}, "W") == ""
      and tools.credit_text({"source_credit": True}, "W") == "")

if shutil.which("ffmpeg"):
    import numpy as np
    from PIL import Image
    tmp = Path(tempfile.mkdtemp(prefix="credit_"))
    try:
        out = tmp / "c.mp4"
        flt, files = clipper.credit_filters(str(out), "Sumber: Windah Basudara (YouTube)", t0=1.0, t1=3.0)
        check("filter kredit: muncul setelah hook, hilang saat CTA", "gte(t,1.00)*lte(t,3.00)" in flt[0])
        subprocess.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=0x30a050:s=540x960:d=4:r=30",
                        "-vf", flt[0], "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)], check=True)

        def box_dark(t):
            png = tmp / f"f{t}.png"
            subprocess.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-ss", str(t), "-i", str(out), "-frames:v", "1", str(png)], check=True)
            a = np.asarray(Image.open(png).convert("RGB")).astype(int)
            zone = a[int(960 * 0.088):int(960 * 0.088) + 14, 30:200]
            return zone.mean()
        bg = box_dark(0.5)
        check("kredit tampil di kiri atas (t=2)", abs(box_dark(2.0) - bg) > 15)
        check("kredit belum tampil saat hook (t=0,5) & hilang saat CTA (t=3,5)", abs(box_dark(3.5) - bg) < 3)
        for f in files:
            Path(f).unlink(missing_ok=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

print()
print("SEMUA LULUS" if not fails else f"{len(fails)} GAGAL: {fails}")
sys.exit(1 if fails else 0)
