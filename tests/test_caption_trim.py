"""Uji trim_caption: caption upload tidak boleh terpotong di tengah kata/kalimat.

Cara jalan: .venv\\Scripts\\python.exe tests\\test_caption_trim.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.utils import trim_caption  # noqa: E402

failures = []


def check(name: str, cond) -> None:
    if cond:
        print(f"OK   {name}")
    else:
        failures.append(name)
        print(f"FAIL {name}")


LONG = ("Viewer 30 ribu, tapi like-nya bisa 30 ribu juga nggak? Streamer ini dikatain "
        "viewernya bot, lalu nantang penonton tap like. Setuju ini cara paling gila.")
out = trim_caption(LONG, 140)
print(f"hasil ({len(out)}): {out!r}")
check("maksimal 141 karakter (batas + elipsis)", len(out) <= 141)
check("berakhir di akhir kalimat / elipsis", out.endswith(".") or out.endswith("…"))
check("kalimat pertama dipertahankan", out.startswith("Viewer 30 ribu"))
check("tidak berakhir 'Setuju ini cara '",
      "Setuju ini cara " not in out and not out.endswith("cara"))

SHORT = "Dua kalimat pendek. Aman."
check("teks pendek tidak berubah", trim_caption(SHORT) == SHORT)
check("newline caption pendek dipertahankan",
      trim_caption("Baris satu.\nBaris dua.", 140) == "Baris satu.\nBaris dua.")

LINE = "Baris satu.\nBaris dua.\nBaris tiga yang panjang sekali sampai lewat batas."
out2 = trim_caption(LINE, 30)
print(f"multiline ({len(out2)}): {out2!r}")
check("newline jadi titik potong", out2 == "Baris satu.\nBaris dua.")

check("limit lebih besar dari teks", trim_caption("halo", 140) == "halo")
check("kosong aman", trim_caption("", 140) == "" and trim_caption(None) == "")

# Kata super panjang tanpa spasi: tetap dipotong dengan elipsis, bukan crash.
out3 = trim_caption("x" * 300, 140)
check("kata tanpa spasi dipotong + elipsis", len(out3) == 141 and out3.endswith("…"))

# Pemotongan kata: 140 karakter pertama berakhir di tengah kata -> ambil kata utuh.
WORDS = ("satu dua tiga empat lima enam tujuh delapan sembilan sepuluh sebelas dua belas "
         "tiga belas empat belas lima belas enam belas tujuh belas delapan belas")
out4 = trim_caption(WORDS, 60)
print(f"kata ({len(out4)}): {out4!r}")
check("potong di batas kata + elipsis", out4.endswith("…") and " " not in out4[-2:-1])
check("tidak melebihi batas + 1", len(out4) <= 61)

print()
if failures:
    print(f"GAGAL: {len(failures)} uji")
    sys.exit(1)
print("Semua uji trim_caption lulus")
