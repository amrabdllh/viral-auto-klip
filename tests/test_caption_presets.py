"""Uji preset subtitle bergaya (core/caption_presets) — render libass sungguhan via FFmpeg.

- 37 preset terdaftar di config.CAPTION_STYLES, katalog UI memberi label; gaya lama tetap ada
- tiap preset menulis .ass valid (dengan/ tanpa timing kata, dengan margin_zones) dan kata tidak keluar layar
- metrik font = metrik libass (tinggi kapital & baseline hasil render cocok ±2 px)
- pil (pill-slide) benar-benar tergambar di belakang kata aktif; stabilo di belakang kata kunci
- override Pengaturan (kapitalisasi, kata per layar, warna sorot) dihormati
- generate_ass_subtitles meneruskan preset, gaya lama tidak tersentuh
- pratinjau PNG untuk preset & gaya lama

Jalankan: python tests/test_caption_presets.py
"""
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
from core import ffx  # noqa: E402,F401  (ffmpeg/bin lokal ke PATH)
from core import caption_presets as cp  # noqa: E402
from core import creator  # noqa: E402
from core.utils import generate_ass_subtitles  # noqa: E402

fails = []
BS = "\\"


def check(name, cond):
    print(("OK   " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)


def words_of(text, t0=0.0):
    out, cur = [], t0
    for tok in text.split():
        d = 0.3 + 0.03 * len(tok)
        out.append({"word": tok, "start": round(cur, 2), "end": round(cur + d - 0.04, 2)})
        cur += d
    return out


def segs_of(text):
    ws = words_of(text)
    segs = []
    for g in range(0, len(ws), 5):
        part = ws[g:g + 5]
        segs.append({"start": part[0]["start"], "end": part[-1]["end"], "text": " ".join(w["word"] for w in part),
                     "words": part})
    return segs


def dialogues(path):
    return [ln for ln in Path(path).read_text(encoding="utf-8").splitlines() if ln.startswith("Dialogue:")]


def ass_secs(stamp):
    h, m, s = stamp.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


tmp = Path(tempfile.mkdtemp(prefix="cap_test_"))
saved = {k: getattr(config, k) for k in ("CAPTION_FONT", "CAPTION_CASE", "CAPTION_WORDS", "CAPTION_HIGHLIGHT", "CAPTION_SCALE")}
try:
    # ------------------------------------------------------------ registrasi
    check("37 preset", len(cp.PRESETS) == 37)
    # (v7) parameter persis klipers (tabel u2): font, warna sorot, garis, kapitalisasi
    pr = cp.resolve("pill-slide")
    check("klipers: pill-slide = Montserrat 800, sorot #A3E635, garis 7, ABC",
          (pr["font"], pr["act_color"], pr["bord"], pr["case"]) == ("montserrat800", "A3E635", 7, "upper"))
    check("klipers: 37 preset punya padanan parameter", all(cp.KLIPERS.get(cp._KLIPERS_ALIAS.get(k, k)) for k in cp.PRESETS))
    check("klipers: word-stack Anton, chat-bubble Poppins biru, minimal Quicksand huruf biasa",
          cp.resolve("word-stack")["font"] == "anton" and cp.resolve("chat-bubble")["bubble_color"] == "0A84FF"
          and (cp.resolve("minimal-apple")["font"], cp.resolve("minimal-apple")["case"]) == ("quicksand", "asis"))
    check("semua preset ada di CAPTION_STYLES", all(config.CAPTION_STYLES.get(p, {}).get("preset") == p for p in cp.PRESETS))
    check("gaya lama tetap ada", all(k in config.CAPTION_STYLES and not config.CAPTION_STYLES[k].get("preset")
                                     for k in ("viral", "modern", "tiktok_bold", "neon", "gradient", "minimal", "emoji")))
    cat = creator.catalog()["caption_styles"]
    labels = {c["v"]: c["l"] for c in cat if isinstance(c, dict)}
    check("katalog UI: preset berlabel", labels.get("pill-slide", "").endswith("Pill Slide") and "viral" in cat)
    check("label & deskripsi tiap preset terisi", all(p["label"] and p.get("desc") for p in cp.PRESETS.values()))

    # ------------------------------------------------------------ tiap preset menulis ASS valid
    text = "Ternyata rahasia konten viral cuma 3 langkah ini dan semua orang bisa langsung coba sekarang"
    W, H = 1080, 1920
    bad, outside = [], []
    for pid in cp.PRESETS:
        out = tmp / f"{pid}.ass"
        info = cp.write_ass(segs_of(text), dict(config.CAPTION_STYLES[pid]), str(out), W, H)
        rows = dialogues(out)
        ok = info["events"] == len(rows) > 0 and all(r.count("{") == r.count("}") for r in rows)
        ok = ok and all(ass_secs(r.split(",")[2]) > ass_secs(r.split(",")[1]) for r in rows)
        if not ok:
            bad.append(pid)
        for r in rows:
            m = re.search(r"\\pos\(([-\d.]+),([-\d.]+)\)", r)
            if m and "\\p1" not in r and not (0 <= float(m.group(1)) <= W and H * 0.4 <= float(m.group(2)) <= H):
                outside.append(pid)
                break
    check("37 preset menulis .ass valid (kurung seimbang, durasi > 0)", not bad)
    check("posisi kata di dalam layar (bawah)", not outside)

    # tanpa timing kata (jalur fallback creator/tools) + margin_zones
    out = tmp / "nowords.ass"
    st = dict(config.CAPTION_STYLES["pop"], margin_zones=[(0.0, 1.0, 900)])
    cp.write_ass([{"start": 0.0, "end": 4.0, "text": "satu dua tiga empat lima enam tujuh delapan"}], st, str(out), W, H)
    rows = dialogues(out)
    ys0 = [float(re.search(r"\\pos\([-\d.]+,([-\d.]+)\)", r).group(1)) for r in rows
           if ass_secs(r.split(",")[1]) < 1.0 and re.search(r"\\pos\(", r)]
    ys1 = [float(re.search(r"\\pos\([-\d.]+,([-\d.]+)\)", r).group(1)) for r in rows
           if ass_secs(r.split(",")[1]) >= 2.05 and re.search(r"\\pos\(", r)]
    check("tanpa timing kata: tetap menulis layar", len(rows) >= 8)
    check("margin_zones menaikkan caption di rentangnya", ys0 and ys1 and max(ys0) < min(ys1) - 300)

    # ------------------------------------------------------------ override Pengaturan
    config.CAPTION_CASE, config.CAPTION_WORDS, config.CAPTION_HIGHLIGHT = "lower", "2", "red"
    out = tmp / "ovr.ass"
    ws6 = words_of("Satu Dua Tiga Empat Lima Enam")
    one = [{"start": ws6[0]["start"], "end": ws6[-1]["end"], "text": "", "words": ws6}]
    info = cp.write_ass(one, dict(config.CAPTION_STYLES["pop"]), str(out), W, H)
    body = Path(out).read_text(encoding="utf-8")
    check("CAPTION_CASE=lower", "}satu" in body and "}SATU" not in body)
    check("CAPTION_WORDS=2 → 3 layar untuk 6 kata", info["chunks"] == 3)
    check("CAPTION_HIGHLIGHT=red dipakai kata aktif", "&H3B3BFF&" in body)
    config.CAPTION_CASE, config.CAPTION_WORDS, config.CAPTION_HIGHLIGHT = "auto", "auto", "auto"

    # ------------------------------------------------------------ dispatch generate_ass_subtitles
    out = tmp / "disp.ass"
    generate_ass_subtitles(segs_of("halo semua apa kabar"), dict(config.CAPTION_STYLES["stabilo"]), str(out), W, H)
    check("generate_ass_subtitles meneruskan preset", "(preset)" in Path(out).read_text(encoding="utf-8"))
    generate_ass_subtitles(segs_of("halo semua apa kabar"), dict(config.CAPTION_STYLES["viral"]), str(out), W, H)
    check("gaya lama tetap generator lama", "(preset)" not in Path(out).read_text(encoding="utf-8"))

    # ------------------------------------------------------------ kata kunci
    toks = ["CUMA", "3", "LANGKAH", "DOANG"]
    check("kata kunci: angka diutamakan", cp._keywords(toks, [{} for _ in toks], set(), True)[0] == 1)

    if shutil.which("ffmpeg"):
        import numpy as np
        from PIL import Image

        def render(ass, t, png, w=W, h=H):
            esc = str(ass).replace(BS, "/").replace(":", BS + ":")
            fonts = Path(config.FONT_DIR)  # sama dengan render asli: font klipers di fonts/
            fd = (":fontsdir='" + str(fonts).replace(BS, "/").replace(":", BS + ":") + "'") if any(fonts.glob("*.[ot]tf")) else ""
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=black:s={w}x{h}:d=5",
                            "-vf", f"subtitles='{esc}'{fd}", "-ss", f"{t:.2f}", "-frames:v", "1", str(png)], check=True)
            return np.asarray(Image.open(png).convert("RGB")).astype(int)

        # metrik: render 'HHHH' di \pos dan bandingkan dengan prediksi
        okm = []
        for kind in ("black", "impact", "bold", "semi", "serif", "montserrat800", "anton", "poppins", "fredoka", "bebas", "teko", "bangers"):
            f = cp.font(kind)
            if not f.path:
                continue
            tags = f"{BS}an5{BS}pos(540,1000){BS}fn{f.family}{BS}b{f.bold}{BS}fs{f.fs(100):.1f}{BS}bord0{BS}shad0"
            ass = tmp / "m.ass"
            ass.write_text(
                "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n[V4+ Styles]\n"
                "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
                "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
                "MarginR, MarginV, Encoding\nStyle: Default,Arial,50,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,"
                "100,100,0,0,1,0,0,5,10,10,10,1\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, "
                "MarginV, Effect, Text\nDialogue: 0,0:00:00.00,0:00:02.00,Default,,0,0,0,,{" + tags + "}HHHH\n",
                encoding="utf-8")
            a = render(ass, 0.5, tmp / "m.png").mean(axis=2)
            ys = np.where(a > 128)[0]
            okm.append(abs((ys.max() + 1) - (1000 + f.center_off(100))) <= 2 and
                       abs((ys.max() + 1 - ys.min()) - f.cap * 100) <= 2)
        check(f"metrik font = libass ({len(okm)} font)", okm and all(okm))

        # pill-slide: pil lime di belakang kata aktif (kata ke-3)
        out = tmp / "pill.ass"
        segs = segs_of("satu dua tiga empat")
        cp.write_ass(segs, dict(config.CAPTION_STYLES["pill-slide"]), str(out), W, H)
        t = segs[0]["words"][2]["start"] + 0.25
        img = render(out, t, tmp / "pill.png")
        lime = (img[:, :, 1] > 180) & (img[:, :, 0] > 120) & (img[:, :, 0] < 200) & (img[:, :, 2] < 90)
        check("pill-slide: pil lime tergambar", lime.sum() > 2000)
        ys, xs = np.where(lime)
        check("pill-slide: pil di zona caption bawah", ys.size and 1300 < ys.mean() < 1600)

        # stabilo: blok kuning di belakang kata kunci, teksnya gelap
        out = tmp / "stab.ass"
        segs = segs_of("ternyata harganya cuma 3000 doang")
        cp.write_ass(segs, dict(config.CAPTION_STYLES["stabilo"]), str(out), W, H)
        img = render(out, segs[0]["end"] - 0.05, tmp / "stab.png")
        yellow = (img[:, :, 0] > 220) & (img[:, :, 1] > 200) & (img[:, :, 2] < 110)
        check("stabilo: coretan kuning tergambar", yellow.sum() > 3000)

        # pratinjau
        p1 = cp.preview_png("word-stack", str(tmp / "prev1.png"))
        p2 = cp.preview_png("viral", str(tmp / "prev2.png"))
        im1, im2 = Image.open(p1), Image.open(p2)
        check("pratinjau preset & gaya lama (PNG 540 lebar)", im1.size[0] == 540 and im2.size[0] == 540)
        check("pratinjau berisi teks", np.asarray(im1.convert("L")).max() > 200)
    else:
        print("SKIP ffmpeg tidak ada di PATH")
finally:
    for k, v in saved.items():
        setattr(config, k, v)
    shutil.rmtree(tmp, ignore_errors=True)

print()
print("SEMUA LULUS" if not fails else f"{len(fails)} GAGAL: {fails}")
sys.exit(1 if fails else 0)
