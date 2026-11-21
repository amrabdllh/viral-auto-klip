"""Uji caption per platform: hashtag khusus YouTube tidak bocor ke TikTok.

Cara jalan: .venv\\Scripts\\python.exe tests\\test_platform_caption.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from core.scheduler import _caption_for  # noqa: E402

failures = []


def check(name: str, cond) -> None:
    if cond:
        print(f"OK   {name}")
    else:
        failures.append(name)
        print(f"FAIL {name}")


TITLE = "Strategi buzzer di game"
DESC = "Cerita lengkapnya seru. #windahbasudara #GTAVI #shorts #youtubeshorts #fyp"

tt = _caption_for("tiktok", TITLE, DESC)
print("tiktok:", tt)
check("TikTok tanpa #shorts", "#shorts" not in tt.lower())
check("TikTok tanpa #youtubeshorts", "youtubeshorts" not in tt.lower())
check("TikTok tetap memakai hashtag campaign", "#windahbasudara" in tt and "#GTAVI" in tt)
check("TikTok tetap punya #fyp", "#fyp" in tt)
check("judul ikut terpasang", tt.startswith(TITLE))
check("tidak ada spasi dobel", "  " not in tt)

yt = _caption_for("youtube", TITLE, DESC)
check("YouTube tetap membawa #shorts", "#shorts" in yt.lower())
check("YouTube tidak diubah", yt == f"{TITLE} {DESC}")

check("caption tanpa hashtag aman", _caption_for("tiktok", "Halo", "Dunia") == "Halo Dunia")
check("default mode TikTok = draft", getattr(config, "TIKTOK_UPLOAD_MODE", "") == "draft")

print()
if failures:
    print(f"GAGAL: {len(failures)} uji")
    sys.exit(1)
print("Semua uji caption platform lulus")
