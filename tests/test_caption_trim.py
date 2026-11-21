"""Uji trim_caption: caption upload tidak boleh terpotong di tengah kata/kalimat.

Cara jalan: .venv\\Scripts\\python.exe tests\\test_caption_trim.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from core.utils import trim_caption  # noqa: E402

failures = []


def check(name: str, cond) -> None:
    if cond:
        print(f"OK   {name}")
    else:
        failures.append(name)
        print(f"FAIL {name}")


def words_complete(out: str, source: str) -> bool:
    """Semua kata di output (tanpa elipsis) adalah kata utuh dari sumber."""
    return all(w in source.split() for w in out.rstrip("…").strip().split())


LONG = ("Viewer 30 ribu, tapi like-nya bisa 30 ribu juga nggak? Streamer ini dikatain "
        "viewernya bot, lalu nantang penonton tap like. Setuju ini cara paling gila.")
out = trim_caption(LONG, 140)
print(f"hasil ({len(out)}): {out!r}")
check("maksimal 141 karakter (batas + elipsis)", len(out) <= 141)
check("berakhir di akhir kalimat / elipsis", out.endswith(".") or out.endswith("…"))
check("kalimat pertama dipertahankan", out.startswith("Viewer 30 ribu"))
check("tidak berakhir 'Setuju ini cara '",
      "Setuju ini cara " not in out and not out.endswith("cara"))
check("semua kata utuh", words_complete(out, LONG))

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

# Batas kata: keluaran harus kata utuh dari sumber, bukan potongan.
WORDS = ("satu dua tiga empat lima enam tujuh delapan sembilan sepuluh sebelas dua belas "
         "tiga belas empat belas lima belas enam belas tujuh belas delapan belas")
out4 = trim_caption(WORDS, 60)
print(f"kata ({len(out4)}): {out4!r}")
check("potong di batas kata + elipsis",
      out4.endswith("…") and WORDS.startswith(out4.rstrip("…")))
check("kata terakhir tidak terbelah", out4.rstrip("…").split()[-1] in WORDS.split())
check("tidak melebihi batas + 1", len(out4) <= 61)

# Jalur klip manual dulu memakai text[:120] mentah — pastikan sekarang aman.
MANUAL = ("Jadi tadi gue ngeliat ada yang aneh di game ini pas karakter di layar melakukan hal "
          "aneh banget dan gue bingung harus ngapain lagi sekarang")
out5 = trim_caption(MANUAL, 120)
print(f"manual ({len(out5)}): {out5!r}")
check("caption klip manual tidak mid-kata", words_complete(out5, MANUAL))

# Emoji gabungan (ZWJ) tidak boleh menyisakan U+200D menggantung.
emoji = "👨‍👩‍👧‍👦"
out6 = trim_caption("halo " + emoji * 20, 21)
check("emoji ZWJ tidak terbelah", not out6.endswith("‍") and out6.endswith("…"))

# limit ekstrem: 0/negatif tetap menghasilkan teks pendek, bukan 140 karakter.
check("limit 0 tetap dipotong", len(trim_caption("x" * 50, 0)) <= 2)
check("limit negatif aman", len(trim_caption("abcdef", -5)) <= 2)

# Caption fallback analyzer harus tetap memuat CTA walau kalimat pertama panjang.
from core.analyzer import _build_upload_caption  # noqa: E402

long_first = " ".join(["abcdefghijkl"] * 14)
cap = _build_upload_caption(long_first, "general")
print(f"fallback ({len(cap)}): {cap!r}")
check("fallback caption memuat CTA", cap.endswith("Menurut kamu gimana?"))
check("fallback caption <= batas", len(cap) <= config.UPLOAD_CAPTION_MAX_CHARS)

print()
if failures:
    print(f"GAGAL: {len(failures)} uji")
    sys.exit(1)
print("Semua uji trim_caption lulus")
