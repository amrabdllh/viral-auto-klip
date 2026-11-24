"""Uji cache transkrip (core/tools.transcribe) tanpa jaringan:
- transkripsi pertama memanggil mesin & menyimpan cache
- job kedua untuk file yang sama memakai cache (tidak membayar ElevenLabs/Groq lagi)
- file sumber berubah / mesin beda → transkripsi ulang
- hasil kosong tidak di-cache

Jalankan: python tests/test_transcript_cache.py
"""
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from core import db, tools, transcriber  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="tcache_"))
db.DATA_DIR = tmp  # cache ditulis ke folder sementara, bukan data/ asli
video = tmp / "src.mp4"
video.write_bytes(b"video-palsu-1")

calls = []
result = {"segs": [{"start": 0.0, "end": 1.0, "text": "halo", "words": [{"word": "halo", "start": 0.0, "end": 0.5}]}]}


def fake_transcribe(path, model_name=None, word_timestamps=False, device=None, compute_type=None):
    calls.append(device)
    return list(result["segs"]), {"device": device, "language": "id"}


transcriber.transcribe_video = fake_transcribe


class Ctx:
    def __init__(self):
        self.logs = []

    def progress(self, *a, **k):
        pass

    def log(self, msg):
        self.logs.append(msg)


fails = []


def check(name, cond):
    print(("OK   " if cond else "GAGAL ") + name)
    if not cond:
        fails.append(name)


params = {"whisper_device": "elevenlabs"}
config.WHISPER_DEVICE = "elevenlabs"
try:
    t1, _ = tools.transcribe(Ctx(), str(video), params)
    check("transkripsi pertama memanggil mesin", calls == ["elevenlabs"] and t1)
    c2 = Ctx()
    t2, info2 = tools.transcribe(c2, str(video), params)
    check("job kedua memakai cache (mesin tidak dipanggil lagi)", calls == ["elevenlabs"])
    check("isi cache sama", t2 == t1 and info2.get("cached") is True)
    check("log menyebut cache", any("cache" in m for m in c2.logs))

    import os
    st = video.stat()
    os.utime(video, (st.st_atime + 120, st.st_mtime + 120))  # unduhan dari cache sumber menyentuh file (LRU)
    tools.transcribe(Ctx(), str(video), params)
    check("file hanya disentuh (mtime berubah) → tetap pakai cache", calls == ["elevenlabs"])

    time.sleep(0.05)
    video.write_bytes(b"video-palsu-2-lebih-panjang")
    tools.transcribe(Ctx(), str(video), params)
    check("file sumber berubah → transkripsi ulang", len(calls) == 2)

    tools.transcribe(Ctx(), str(video), {"whisper_device": "cpu"})
    check("mesin beda → transkripsi ulang", len(calls) == 3)

    result["segs"] = []
    empty = tmp / "kosong.mp4"
    empty.write_bytes(b"x")
    tools.transcribe(Ctx(), str(empty), params)
    tools.transcribe(Ctx(), str(empty), params)
    check("hasil kosong tidak di-cache", len(calls) == 5)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\nSemua uji cache transkrip lulus" if not fails else f"\nGAGAL: {len(fails)} uji")
sys.exit(1 if fails else 0)
