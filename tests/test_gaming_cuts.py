"""Uji jump cut sadar-audio untuk siaran gaming (core/editing.plan_jump_cuts + audio_rms).

Masalah yang diperbaiki: jump cut biasa membuang SEMUA jeda bicara > 0,55 dtk. Di siaran gaming jeda itu berisi aksi
game (tembakan, mesin, kejar-kejaran) → klip 35 dtk jadi 14 dtk dan kehilangan isinya.
- tanpa rms: perilaku lama (podcast/talking head) tetap — jeda panjang dibuang
- dengan rms: jeda berisi suara keras dipertahankan; jeda yang benar-benar sepi tetap dibuang
- audio_rms membaca energi dari file sungguhan (FFmpeg) dan membedakan bagian ramai vs sepi

Jalankan: python tests/test_gaming_cuts.py
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import config  # noqa: E402,F401
from core import ffx  # noqa: E402,F401
from core import editing  # noqa: E402

fails = []


def check(name, cond):
    print(("OK   " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)


# kata: 0–3 dtk bicara, jeda 3–8 (aksi game, ramai), 8–11 bicara, jeda 11–15 (sepi), 15–18 bicara
words = []
for a, b in ((0.2, 3.0), (8.0, 11.0), (15.0, 18.0)):
    t = a
    while t < b:
        words.append({"word": "kata", "start": round(t, 2), "end": round(t + 0.35, 2)})
        t += 0.45
dur, hop = 18.5, 0.05
rms = np.full(int(dur / hop), 0.10, dtype=np.float32)   # bicara
rms[int(3.2 / hop):int(7.9 / hop)] = 0.14              # jeda berisi tembakan/mesin: lebih keras dari suara bicara
rms[int(11.2 / hop):int(14.9 / hop)] = 0.01            # jeda benar-benar sepi

old = editing.plan_jump_cuts(words, dur, max_gap=0.55)
check("tanpa rms (podcast): kedua jeda panjang dibuang", editing.kept_duration(old) < 11)
new = editing.plan_jump_cuts(words, dur, max_gap=1.0, rms=rms, hop=hop, quiet_ratio=0.2)
kept = editing.kept_duration(new)
check("gaming: jeda berisi aksi game dipertahankan", any(s <= 4.0 and e >= 7.5 for s, e in new))
check("gaming: jeda sepi tetap dibuang", not any(s <= 12.0 and e >= 14.0 for s, e in new))
check(f"gaming: durasi tersisa wajar ({kept:.1f} dari {dur} dtk)", 13 <= kept <= 15.5)
loud = np.full(int(dur / hop), 0.1, dtype=np.float32)
check("gaming: game ramai terus → tidak ada potongan", len(editing.plan_jump_cuts(words, dur, max_gap=1.0, rms=loud, hop=hop)) == 1)

if shutil.which("ffmpeg"):
    tmp = Path(tempfile.mkdtemp(prefix="gcut_"))
    try:
        src = tmp / "a.wav"
        # 0–2 dtk sinus keras, 2–4 dtk senyap, 4–6 dtk sinus pelan
        subprocess.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                        "aevalsrc='if(lt(t,2),0.6,if(lt(t,4),0,0.15))*sin(2*PI*440*t)':s=48000:d=6", str(src)], check=True)
        r = editing.audio_rms(str(src))
        check("audio_rms: panjang sesuai (±6 dtk / 50 ms)", 115 <= len(r) <= 121)
        check("audio_rms: ramai > pelan > senyap", r[10:30].mean() > r[90:110].mean() > r[50:70].mean() + 0.05
              and r[50:70].mean() < 0.01)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

print()
print("SEMUA LULUS" if not fails else f"{len(fails)} GAGAL: {fails}")
sys.exit(1 if fails else 0)
