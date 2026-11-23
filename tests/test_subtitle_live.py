"""Uji subtitle live + burn ulang (core/subtitle_live) dengan FFmpeg sungguhan.

- render_polish menulis 2 keluaran dari 1 encode: final (caption) + clean.mp4 (tanpa caption, tanpa audio)
- save → subtitle.json + live_audio.m4a; editor_view berisi layar caption
- edit teks: jumlah kata sama → timing kata dipertahankan; beda → dibagi rata; kosong → layar dihapus
- burn ulang dengan gaya lain mengganti caption di file final (durasi & audio tetap)
- subtitle dimatikan → area caption sama dengan master bersih; "Kembalikan asli" → gaya asli lagi
- tempo klip ikut diterapkan saat burn ulang; gaya lain mempertahankan posisi khusus klip

Jalankan: python tests/test_subtitle_live.py
"""
import json
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
from core import editing, subtitle_live as sl  # noqa: E402
from core.clipper import _escape_ffmpeg_filter_path, _video_encode_args  # noqa: E402
from core.utils import generate_ass_subtitles, get_video_info  # noqa: E402

fails = []


def check(name, cond):
    print(("OK   " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)


def words_of(text, t0=0.2):
    out, cur = [], t0
    for tok in text.split():
        out.append({"word": tok, "start": round(cur, 2), "end": round(cur + 0.42, 2)})
        cur += 0.48
    return out


# ------------------------------------------------------------ edit teks (tanpa ffmpeg)
seg = {"start": 1.0, "end": 3.0, "text": "satu dua tiga", "words": words_of("satu dua tiga", 1.0)}
e1 = sl.apply_text_edits([seg], {0: "SATU DUA EMPAT"})[0]
check("edit jumlah kata sama → timing kata tetap", [w["start"] for w in e1["words"]] == [w["start"] for w in seg["words"]]
      and e1["words"][2]["word"] == "EMPAT")
e2 = sl.apply_text_edits([seg], {0: "cuma dua kata"})[0]
e3 = sl.apply_text_edits([seg], {0: "jadi satu kalimat yang lebih panjang"})[0]
check("edit beda jumlah kata → dibagi rata di dalam layar",
      len(e3["words"]) == 6 and e3["words"][0]["start"] == 1.0 and e3["words"][-1]["end"] <= 3.0
      and all(a["end"] <= b["start"] for a, b in zip(e3["words"], e3["words"][1:])))
check("edit kosong → layar dihapus", sl.apply_text_edits([seg, seg], {1: "  "}) == [seg])
check("style_overrides: hanya yang beda dari gaya dasar",
      sl.style_overrides("viral", {**config.CAPTION_STYLES["viral"], "vertical_position": 900, "margin_zones": [(0, 1, 500)]})
      == {"margin_zones": [(0, 1, 500)], "vertical_position": 900})

if not shutil.which("ffmpeg"):
    print("SKIP ffmpeg tidak ada di PATH")
    sys.exit(1 if fails else 0)

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="sublive_"))


def frame(path, t, png):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(path), "-frames:v", "1", str(png)],
                   check=True)
    return np.asarray(Image.open(png).convert("RGB")).astype(int)


def has_audio(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0",
                        str(path)], capture_output=True, text=True)
    return bool(r.stdout.strip())


try:
    W, H, D = 540, 960, 4.0
    src = tmp / "src.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=0x203040:s={W}x{H}:d={D}:r=30",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={D}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(src)], check=True)
    clip_dir = tmp / "job" / "clip_001_viral80_humor"
    clip_dir.mkdir(parents=True)
    segs = [{"start": 0.2, "end": 1.6, "text": "INI CONTOH SUBTITLE", "words": words_of("INI CONTOH SUBTITLE", 0.2)},
            {"start": 1.8, "end": 3.6, "text": "BISA DIEDIT NANTI", "words": words_of("BISA DIEDIT NANTI", 1.8)}]
    style = dict(config.CAPTION_STYLES["viral"])
    style_px = {**style, "font_size": int(style["font_size"] * W / 1080), "vertical_position": int(420 * H / 1920)}
    ass = clip_dir / "captions.ass"
    generate_ass_subtitles(segs, style_px, str(ass), W, H)
    final = clip_dir / "clip_001_viral80_humor_final.mp4"
    clean = clip_dir / sl.CLEAN_FILE
    editing.render_polish(str(src), str(final), width=W, height=H, duration=D, ass_path=str(ass), grade="none",
                          zoom_chain="", progress_bar=False, encode_args=_video_encode_args(crf=17),
                          escape_path=_escape_ffmpeg_filter_path, overlays=[], clean_path=str(clean))
    check("render_polish: final + master bersih dari 1 encode", final.exists() and clean.exists())
    check("master bersih tanpa audio, final dengan audio", not has_audio(clean) and has_audio(final))
    zone = slice(int(H * 0.62), int(H * 0.86))
    f_fin, f_cln = frame(final, 1.0, tmp / "a.png"), frame(clean, 1.0, tmp / "b.png")
    check("caption hanya di final", (f_fin[zone] > 200).all(axis=2).sum() > 500 and (f_cln[zone] > 200).all(axis=2).sum() < 20)

    ok = sl.save(clip_dir, style_name="viral", clip_style=style_px, segments=segs, width=W, height=H, enabled=True,
                 tempo=1.0, final_path=str(final))
    view = sl.editor_view(str(final))
    check("save → data + audio tersimpan, editor siap", ok and sl.available(str(final)) and view["available"]
          and [s["text"] for s in view["segments"]] == ["INI CONTOH SUBTITLE", "BISA DIEDIT NANTI"])
    dur0 = float(get_video_info(str(final))["duration"])

    # burn ulang: gaya pill-slide + teks layar 2 diganti
    r = sl.reburn(str(final), style="pill-slide", edits={1: "SUDAH DIGANTI LHO"})
    data = json.loads((clip_dir / sl.DATA_FILE).read_text(encoding="utf-8"))
    check("burn ulang: data tersimpan (gaya & teks baru)", r["style"] == "pill-slide" and data["segments"][1]["text"] == "SUDAH DIGANTI LHO")
    img = frame(final, 2.1, tmp / "c.png")
    lime = (img[:, :, 1] > 180) & (img[:, :, 0] > 120) & (img[:, :, 0] < 200) & (img[:, :, 2] < 90)
    check("burn ulang: pil lime pill-slide tampil di final", lime.sum() > 400)
    check("burn ulang: durasi & audio tetap", abs(float(get_video_info(str(final))["duration"]) - dur0) < 0.1 and has_audio(final))
    check("burn ulang: posisi khusus klip ikut (vertical_position)", data["style_overrides"].get("vertical_position") == style_px["vertical_position"])

    # matikan subtitle → area caption = master bersih
    sl.reburn(str(final), enabled=False)
    f_off = frame(final, 1.0, tmp / "d.png")
    check("subtitle dimatikan → sama dengan master bersih", np.abs(f_off[zone] - f_cln[zone]).mean() < 3)

    # kembalikan asli
    sl.reburn(str(final), reset=True)
    data = json.loads((clip_dir / sl.DATA_FILE).read_text(encoding="utf-8"))
    f_rst = frame(final, 1.0, tmp / "e.png")
    check("kembalikan asli → gaya & teks asli", data["style"] == "viral" and data["segments"][1]["text"] == "BISA DIEDIT NANTI"
          and np.abs(f_rst[zone] - f_fin[zone]).mean() < 6)

    # tempo ikut diterapkan
    data["tempo"] = 1.08
    sl._write(clip_dir, data)
    sl.reburn(str(final))
    check("tempo klip ikut saat burn ulang", abs(float(get_video_info(str(final))["duration"]) - dur0 / 1.08) < 0.15)

    try:
        sl.reburn(str(final), style="tidak-ada")
        check("gaya tak dikenal ditolak", False)
    except ValueError:
        check("gaya tak dikenal ditolak", True)
    nolive = tmp / "x.mp4"
    shutil.copy2(src, nolive)
    check("klip tanpa data live → tidak tersedia", not sl.editor_view(str(nolive))["available"])
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
print("SEMUA LULUS" if not fails else f"{len(fails)} GAGAL: {fails}")
sys.exit(1 if fails else 0)
