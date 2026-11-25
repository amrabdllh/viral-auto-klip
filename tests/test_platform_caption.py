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

from core.scheduler import platform_copy  # noqa: E402
yt = platform_copy("youtube", TITLE, DESC)
print("youtube:", yt)
check("YouTube: hashtag topik masuk judul", yt["title"] == f"{TITLE} #windahbasudara #GTAVI")
check("YouTube: hashtag umum (#shorts/#fyp) tidak memenuhi judul", "#shorts" not in yt["title"] and "#fyp" not in yt["title"])
check("YouTube: deskripsi tetap membawa #shorts", "#shorts" in yt["description"].lower())
check("TikTok: kalimat deskripsi tidak ikut (caption pendek)", "Cerita lengkapnya" not in tt)

check("caption tanpa hashtag aman", _caption_for("tiktok", "Halo", "Dunia") == "Halo")
# default BAWAAN kode = draft (aman); nilai aktif boleh 'post' bila pengguna memilihnya di Pengaturan
_src = (Path(config.__file__)).read_text(encoding="utf-8")
check("default mode TikTok = draft", 'os.getenv("TIKTOK_UPLOAD_MODE", "draft")' in _src)
check("mode TikTok aktif valid", getattr(config, "TIKTOK_UPLOAD_MODE", "") in ("draft", "post"))

print()
if failures:
    print(f"GAGAL: {len(failures)} uji")
    sys.exit(1)
print("Semua uji caption platform lulus")
