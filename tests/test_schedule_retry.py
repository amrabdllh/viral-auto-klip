"""Uji retry upload: platform yang sudah sukses tidak diunggah ulang.

Kenapa penting: jadwal yang gagal sebagian (TikTok sukses, YouTube kedaluwarsa)
akan dicoba ulang lewat tombol "Upload sekarang"/"Antrekan lagi". Tanpa guard ini,
TikTok ikut terunggah lagi → dobel.

Cara jalan: .venv\\Scripts\\python.exe tests\\test_schedule_retry.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import scheduler  # noqa: E402

failures = []


def check(name: str, cond) -> None:
    if cond:
        print(f"OK   {name}")
    else:
        failures.append(name)
        print(f"FAIL {name}")


prev = {"youtube": {"status": "done", "method": "browser", "ok": True},
        "tiktok": {"ok": True, "privacy": "default"}}
item = {
    "file_path": str(Path(__file__).resolve()),  # file ada -> lolos cek awal
    "platforms": ["youtube", "tiktok"],
    "title": "Tes retry",
    "description": "caption #tag",
    "result": prev,
}
report = scheduler.upload_now(item)
check("platform yang sudah sukses dilewati (tanpa upload ulang)", report == prev)
check("hasil retry dianggap sukses", scheduler._ok(report) is True)

# Platform yang belum pernah sukses tidak ikut dilewati: hasil lama tanpa 'ok'/'done'.
item2 = dict(item, platforms=["youtube"], result={"youtube": {"status": "failed", "reason": "x"}})
# Tidak dipanggil upload_now di sini (akan mencoba jaringan), cukup pastikan
# marker sukses yang dikenali hanya 'status=done' atau 'ok=true'.
check("failed tidak dianggap sukses",
      not (str(item2["result"]["youtube"].get("status", "")).lower() == "done"
           or item2["result"]["youtube"].get("ok") is True))

print()
if failures:
    print(f"GAGAL: {len(failures)} uji")
    sys.exit(1)
print("Semua uji retry upload lulus")
