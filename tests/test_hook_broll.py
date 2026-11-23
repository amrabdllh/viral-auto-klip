"""Uji Hook B-roll + suara (core/hook_broll) — TTS dipalsukan, render & sambung FFmpeg sungguhan.

- judul untuk narasi: emoji/hashtag dibuang, dibatasi ±12 kata
- gaya: setting tetap dihormati, otomatis bervariasi antar-klip sesuai kategori
- 7 gaya menyusun sampul (tidak kosong, ukuran benar) dari potongan orang sintetis
- build(): intro video + audio narasi + cover; durasi mengikuti narasi (dibatasi HOOK_BROLL_MAX)
- prepend(): intro + klip tersambung (durasi = jumlah, audio ada, decode bersih)
- subtitle live: burn ulang memasang intro lagi di depan
- mode default tetap 'text' (hook bergaya), 'broll' hanya bila dipilih

Jalankan: python tests/test_hook_broll.py
"""
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
from core import clipper, hook_broll as hb, media_ai  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402
import numpy as np  # noqa: E402

fails = []


def check(name, cond):
    print(("OK   " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)


# ------------------------------------------------------------ teks & gaya
t = hb.title_text({"ai_title": "🔥 Streamer ini kaget banget pas mobilnya hilang di tengah jalan raya kota besar malam itu #fyp #viral"})
check("judul: emoji & hashtag dibuang", "#" not in t and "🔥" not in t and t.startswith("Streamer"))
check("judul: maks 12 kata", len(t.split()) <= 12)
check("judul cadangan", hb.title_text({}) == "Wajib tonton sampai habis")
saved = {k: getattr(config, k) for k in ("HOOK_BROLL_STYLE", "HOOK_BROLL_MAX", "HOOK_MODE")}
config.HOOK_BROLL_STYLE = "auto"
recent = []
for _ in range(4):
    recent.append(hb.pick_style({"category": "humor"}, recent))
check("gaya otomatis bervariasi (tidak sama berturut-turut)", all(a != b for a, b in zip(recent, recent[1:])))
check("gaya humor mulai dari popart", recent[0] == "popart")
config.HOOK_BROLL_STYLE = "majalah"
check("gaya tetap dari Pengaturan", hb.pick_style({"category": "humor"}, []) == "majalah")
config.HOOK_BROLL_STYLE = "auto"
config.HOOK_MODE = "text"
check("mode default = hook teks", clipper._hook_mode() == "text")

# ------------------------------------------------------------ 7 gaya menyusun sampul
W, H = 540, 960
still = Image.new("RGB", (W, H), (70, 90, 120))
ImageDraw.Draw(still).rectangle((0, H // 2, W, H), fill=(40, 120, 60))
fig = Image.new("RGBA", (300, 260), (0, 0, 0, 0))
ImageDraw.Draw(fig).ellipse((100, 0, 200, 100), fill=(235, 190, 160, 255))
ImageDraw.Draw(fig).rounded_rectangle((30, 100, 270, 260), radius=60, fill=(30, 100, 200, 255))
people = [{"cut": fig, "frame": still, "t": 1.0, "box": [0, 0, 1, 1]}, {"cut": fig, "frame": still, "t": 5.0, "box": [0, 0, 1, 1]}]
bad = []
for st in hb.STYLES:
    sc = hb.build_scene(st, "Ternyata begini *rahasia* mereka", people, still, W, H, label="humor")
    a = np.asarray(sc.render(2.5, 1.03)).astype(int)
    b = np.asarray(sc.render(0.33, 1.0)).astype(int)
    if a.shape != (H, W, 3) or a.std() < 20 or np.abs(a - b).mean() < 2:
        bad.append(st)
check("7 gaya: sampul tersusun & beranimasi", not bad)
check("pratinjau sampul tanpa video", Path(hb.preview_png("kolase", str(Path(tempfile.gettempdir()) / "hb_prev.png"))).exists())

if not shutil.which("ffmpeg"):
    print("SKIP ffmpeg tidak ada di PATH")
    sys.exit(1 if fails else 0)

tmp = Path(tempfile.mkdtemp(prefix="hbroll_"))
real_tts = media_ai.tts


def fake_tts(text, out, provider="", voice="", speed=1.0, **kw):
    d = round(2.4 / max(speed, 0.5), 2)
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency=330:duration={d}",
                    "-ac", "1", out], check=True)
    return {"path": out, "duration": d, "words": [], "provider": "fake", "voice": voice or "Kore"}


def dur_of(p):
    return hb._duration(str(p))


try:
    media_ai.tts = fake_tts
    src = tmp / "clip.mp4"
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc2=s={W}x{H}:d=5:r=30",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=5", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-ar", "48000", "-ac", "2", "-shortest", str(src)], check=True)
    clip_dir = tmp / "clip_001"
    clip_dir.mkdir()
    enc = clipper._video_encode_args(crf=17)
    config.HOOK_BROLL_MAX = 7.0
    info = hb.build({"start": 10.0, "end": 15.0, "ai_title": "Mobilnya hilang di tengah jalan", "category": "humor"},
                    str(src), W, H, clip_dir, enc)
    check("build: intro video, audio & cover jadi", info and all(Path(info[k]).exists() for k in ("video", "audio", "cover")))
    check("build: durasi = jeda + narasi + ekor", info and abs(info["dur"] - (0.38 + 2.4 + 0.45)) < 0.1)
    check("build: tanpa orang → cast 'frame' (testsrc tanpa orang)", info and info["cast"] in ("frame", "composed"))
    config.HOOK_BROLL_MAX = 2.6
    info2 = hb.build({"start": 0, "end": 5, "ai_title": "Judul panjang sekali untuk dibacakan", "category": "story"},
                     str(src), W, H, clip_dir, enc)
    check("build: dibatasi HOOK_BROLL_MAX", info2 and info2["dur"] <= 2.62)
    config.HOOK_BROLL_MAX = 7.0

    final = clip_dir / "clip_001_final.mp4"
    shutil.copy2(src, final)
    intro = clip_dir / "intro.mp4"
    hb.mux_intro(info["video"], info["audio"], str(final), str(intro))
    d_intro, d_clip = dur_of(intro), dur_of(final)
    hb.prepend(str(final), str(intro), enc)
    chk = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(final), "-f", "null", "-"], capture_output=True, text=True)
    check("prepend: durasi = intro + klip", abs(dur_of(final) - (d_intro + d_clip)) < 0.25)
    check("prepend: decode bersih", not chk.stderr.strip())
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0",
                        str(final)], capture_output=True, text=True)
    check("prepend: audio ada", bool(r.stdout.strip()))
    first = clip_dir / "f0.png"
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-ss", "1.5", "-i", str(final), "-frames:v", "1", str(first)], check=True)
    cov = np.asarray(Image.open(info["cover"]).convert("RGB").resize((W, H))).astype(int)
    f0 = np.asarray(Image.open(first).convert("RGB")).astype(int)
    check("detik 1,5 = sampul B-roll (bukan klip)", np.abs(cov - f0).mean() < 40)

    # subtitle live: burn ulang memasang intro lagi
    from core import editing, subtitle_live as sl
    from core.utils import generate_ass_subtitles
    live_dir = tmp / "job" / "clip_002"
    live_dir.mkdir(parents=True)
    body = live_dir / "clip_002_final.mp4"
    segs = [{"start": 0.3, "end": 2.0, "text": "HALO SEMUA", "words": [{"word": "HALO", "start": 0.3, "end": 0.9},
                                                                       {"word": "SEMUA", "start": 1.0, "end": 1.9}]}]
    ass = live_dir / "c.ass"
    generate_ass_subtitles(segs, dict(config.CAPTION_STYLES["viral"]), str(ass), W, H)
    editing.render_polish(str(src), str(body), width=W, height=H, duration=5.0, ass_path=str(ass), grade="none",
                          zoom_chain="", progress_bar=False, encode_args=enc, escape_path=clipper._escape_ffmpeg_filter_path,
                          overlays=[], clean_path=str(live_dir / sl.CLEAN_FILE))
    sl.save(live_dir, style_name="viral", clip_style=dict(config.CAPTION_STYLES["viral"]), segments=segs, width=W, height=H,
            enabled=True, final_path=str(body))
    d_body = dur_of(body)
    shutil.copy2(intro, live_dir / "intro.mp4")
    hb.prepend(str(body), str(live_dir / "intro.mp4"), enc)
    sl.set_intro(live_dir, "intro.mp4")
    sl.reburn(str(body), style="pop")
    check("subtitle live: burn ulang tetap dengan intro", abs(dur_of(body) - (d_intro + d_body)) < 0.3)
finally:
    media_ai.tts = real_tts
    for k, v in saved.items():
        setattr(config, k, v)
    shutil.rmtree(tmp, ignore_errors=True)

print()
print("SEMUA LULUS" if not fails else f"{len(fails)} GAGAL: {fails}")
sys.exit(1 if fails else 0)
