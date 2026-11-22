"""Uji pemilihan teks thumbnail/judul (core/clickbait) tanpa jaringan: AI penulis & juri dipalsukan.

- kandidat tidak jujur ditolak, kandidat jelas (≥7) didahulukan, angka karangan ditolak (GTA 6 dari judul 'VI' boleh)
- tiap klip mendapat teks BERBEDA (kandidat mirip klip lain → peringkat berikutnya)
- gaya normal: judul tidak diubah; gaya clickbait: judul diganti

Jalankan: python tests/test_clickbait.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from core import clickbait  # noqa: E402

fails = []


def check(name, cond):
    print(("OK   " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)


GEN = {"thumb": ["NGGAK SLOP, MEN", "LUCIA GENDONG JASON?!", "UFO BENERAN DI GTA 6?!", "LUCIA MENANG 1000 KALI!"],
       "title": ["Ternyata LUCIA Gendong JASON! 🤯", "Lucia Bikin Kaget Semua Orang"]}
JUDGE = {"thumb": [{"i": 0, "jelas": 3, "penasaran": 8, "jujur": True},
                   {"i": 1, "jelas": 9, "penasaran": 8, "jujur": True},
                   {"i": 2, "jelas": 9, "penasaran": 9, "jujur": False},
                   {"i": 3, "jelas": 9, "penasaran": 10, "jujur": True}],
         "title": [{"i": 0, "jelas": 9, "penasaran": 8, "jujur": True}, {"i": 1, "jelas": 6, "penasaran": 5, "jujur": True}]}
calls = []


def fake_ask(prompt):
    calls.append(prompt)
    return json.dumps(GEN if "Balas JSON: {\"thumb\": [\"6 kandidat" in prompt else JUDGE)


clickbait._ask = fake_ask
clip = {"transcript": "gua kira arc lucia bakal slop ternyata lucia gendong jason guys seru banget", "ai_title": "lama",
        "thumb_text": "Nggak slop, men!"}
res = clickbait.write(clip, "GTA VI GAMEPLAY | Windah")
check("kandidat terbaik = jelas & jujur", res["thumb"][:1] == ["LUCIA GENDONG JASON?!"])
check("tidak jujur ditolak", "UFO BENERAN DI GTA 6?!" not in res["thumb"])
check("angka karangan ditolak", "LUCIA MENANG 1000 KALI!" not in res["thumb"])
check("tidak jelas tidak didahulukan", res["thumb"].index("NGGAK SLOP, MEN") > 0 if "NGGAK SLOP, MEN" in res["thumb"] else True)
check("judul terbaik terpilih", res["title"][:1] == ["Ternyata LUCIA Gendong JASON! 🤯"])

# dua klip, kandidat sama → klip kedua dapat peringkat berikutnya (bukan teks kembar)
config.TITLE_STYLE = "normal"
a = {"transcript": clip["transcript"], "ai_title": "judul A", "thumb_text": "x"}
b = {"transcript": clip["transcript"], "ai_title": "judul B", "thumb_text": "y"}
clickbait.apply([a, b], "GTA VI")
check("teks klip A", a["thumb_text"] == "LUCIA GENDONG JASON?!")
check("teks klip B berbeda", b["thumb_text"] != a["thumb_text"])
check("gaya normal: judul tidak diubah", a["ai_title"] == "judul A")
config.TITLE_STYLE = "clickbait"
c = {"transcript": clip["transcript"], "ai_title": "judul C", "thumb_text": "z"}
clickbait.apply([c], "GTA VI")
check("gaya clickbait: judul diganti", c["ai_title"] == "Ternyata LUCIA Gendong JASON! 🤯")
check("GTA 6 dari judul 'VI' lolos pemeriksa angka",
      clickbait._rank(["GTA 6 BIKIN KAGET!"], [{"i": 0, "jelas": 9, "penasaran": 9, "jujur": True}],
                      "isi klip GTA VI 6") == ["GTA 6 BIKIN KAGET!"])

print("\nSemua uji clickbait lulus" if not fails else f"\nGAGAL: {len(fails)} uji")
sys.exit(1 if fails else 0)
