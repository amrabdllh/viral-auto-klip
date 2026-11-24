"""Uji progres & pembatalan render (core/clipper.process_clips on_progress) tanpa ffmpeg/GPU:
- on_progress dipanggil sekali per klip dengan (selesai, total)
- callback yang error biasa tidak menghentikan render
- RenderCancelled menghentikan render: klip yang belum mulai tidak dirender

Jalankan: python tests/test_render_progress.py
"""
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from core import clipper  # noqa: E402

fails = []


def check(name, cond):
    print(("OK   " if cond else "GAGAL ") + name)
    if not cond:
        fails.append(name)


tmp = Path(tempfile.mkdtemp(prefix="rprog_"))
started = []
lock = threading.Lock()


def fake_single(idx, clip_info, output_dir, *a, **k):
    with lock:
        started.append(idx)
    time.sleep(0.05)
    return idx, {"file_path": str(Path(output_dir) / f"clip_{idx}.mp4"), "score": 1}


clipper._process_single_clip = fake_single
clipper._detect_scene_cuts = lambda *a, **k: None
clipper.get_video_info = lambda p: {"width": 1920, "height": 1080, "duration": 600, "fps": 30}
clipper._plan_hook_styles = lambda clips: None
clipper.detect_stream_layout = lambda *a, **k: None
config.PARALLEL_PROCESSING, config.PARALLEL_CLIPS = True, 2

clips = [{"start": i * 10.0, "end": i * 10.0 + 8, "text": "halo", "score": 50} for i in range(8)]
try:
    calls = []
    out = clipper.process_clips("src.mp4", [dict(c) for c in clips], tmp, layout="off", enable_face_reframe=False,
                                on_progress=lambda d, t, c: calls.append((d, t)))
    check("8 klip selesai", len(out) == 8)
    check("on_progress dipanggil per klip", [c[0] for c in calls] == list(range(1, 9)) and {c[1] for c in calls} == {8})

    def broken(*_):
        raise ValueError("UI mati")
    out = clipper.process_clips("src.mp4", [dict(c) for c in clips], tmp, layout="off", enable_face_reframe=False,
                                on_progress=broken)
    check("callback error biasa tidak menghentikan render", len(out) == 8)

    started.clear()

    def cancel_after_two(done, total, clip):
        if done >= 2:
            raise clipper.RenderCancelled()
    try:
        clipper.process_clips("src.mp4", [dict(c) for c in clips], tmp, layout="off", enable_face_reframe=False,
                              on_progress=cancel_after_two)
        check("RenderCancelled diteruskan", False)
    except clipper.RenderCancelled:
        check("RenderCancelled diteruskan", True)
    check(f"klip yang belum mulai tidak dirender ({len(started)}/8 mulai)", len(started) < 8)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\nSemua uji progres render lulus" if not fails else f"\nGAGAL: {len(fails)} uji")
sys.exit(1 if fails else 0)
