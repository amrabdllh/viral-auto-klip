"""Uji paket judul + thumbnail + hook (core/clickbait) tanpa jaringan: AI penulis & juri dipalsukan.

- paket tidak jujur / tidak sinkron / angka karangan ditolak; paket jelas didahulukan
- judul yang sudah tayang & hook yang sudah tercetak DIKUNCI (paket memakai persis itu)
- sebelum render hook ikut diganti; sesudah render hook tidak disentuh
- teks thumbnail tiap klip berbeda

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


GEN = {"paket": [
    {"thumb": "MOBIL PERTAMA GUA", "title": "Mencuri mobil cuma pakai slim jim, gampang banget", "hook": "Segampang ini ternyata"},
    {"thumb": "CURI MOBIL PAKAI SLIM JIM?!", "title": "NYOLONG Mobil Cuma Modal SLIM JIM?! 😱", "hook": "Lihat seberapa cepat pintunya kebuka"},
    {"thumb": "UFO MUNCUL DI GTA 6?!", "title": "UFO Beneran Muncul?!", "hook": "Ini beneran terjadi"},
    {"thumb": "CURI 1000 MOBIL SEHARI!", "title": "Curi 1000 mobil sehari!", "hook": "Rekor baru"},
]}
NILAI = {"nilai": [
    {"i": 0, "jelas": 6, "penasaran": 6, "sinkron": 3, "jujur": True},   # thumbnail tidak nyambung dengan judul
    {"i": 1, "jelas": 9, "penasaran": 9, "sinkron": 9, "jujur": True},
    {"i": 2, "jelas": 9, "penasaran": 9, "sinkron": 9, "jujur": False},  # tidak ada di klip
    {"i": 3, "jelas": 9, "penasaran": 10, "sinkron": 9, "jujur": True},  # angka karangan
]}
prompts = []


def fake_ask(prompt):
    prompts.append(prompt)
    return json.dumps(GEN if "Tulis 5 PAKET" in prompt else NILAI)


clickbait._ask = fake_ask
TX = "mobil bang waibi kebuka cuma pakai slim jim mencuri dengan sangat gampang coy gila tarikan mobilnya"
clip = {"transcript": TX, "ai_title": "lama", "thumb_text": "lama", "hook_text": "Mencuri mobil, ternyata segampang ini"}

ranked = clickbait.write(clip, "GTA VI GAMEPLAY | Windah")
check("paket terbaik = jelas, jujur & sinkron", ranked[:1] and ranked[0]["thumb"] == "CURI MOBIL PAKAI SLIM JIM?!")
check("tidak sinkron ditolak", all(p["thumb"] != "MOBIL PERTAMA GUA" for p in ranked))
check("tidak jujur ditolak", all(p["thumb"] != "UFO MUNCUL DI GTA 6?!" for p in ranked))
check("angka karangan ditolak", all(p["thumb"] != "CURI 1000 MOBIL SEHARI!" for p in ranked))

prompts.clear()
ranked = clickbait.write(clip, "GTA VI", fixed_title="'Mencuri dengan sangat gampang coy' — cuma pakai slim jim",
                         fixed_hook="Mencuri mobil, ternyata segampang ini")
check("judul terkunci diberitahukan ke penulis", "TIDAK BOLEH DIUBAH" in prompts[0])
check("judul terkunci dipakai", ranked and all(p["title"].startswith("'Mencuri dengan sangat gampang") for p in ranked))
check("hook terkunci dipakai", ranked and all(p["hook"] == "Mencuri mobil, ternyata segampang ini" for p in ranked))

# sebelum render: hook ikut paket · sesudah render: hook tidak disentuh
config.TITLE_STYLE = "clickbait"
a = {"transcript": TX, "ai_title": "A", "thumb_text": "x", "hook_text": "hook lama A"}
clickbait.apply([a], "GTA VI", prerender=True)
check("sebelum render: hook diganti paket", a["hook_text"] == "Lihat seberapa cepat pintunya kebuka")
check("judul & thumbnail dari paket yang sama", a["ai_title"].startswith("NYOLONG") and a["thumb_text"].startswith("CURI MOBIL"))
b = {"transcript": TX, "ai_title": "B", "thumb_text": "y", "hook_text": "hook lama B"}
c = {"transcript": TX, "ai_title": "C", "thumb_text": "z", "hook_text": "hook lama C"}
clickbait.apply([b, c], "GTA VI", prerender=False)
check("sesudah render: hook tidak berubah", b["hook_text"] == "hook lama B" and c["hook_text"] == "hook lama C")
check("teks thumbnail tiap klip berbeda", b["thumb_text"] != c["thumb_text"])
check("GTA 6 dari judul 'VI' lolos pemeriksa angka",
      clickbait._rank([{"thumb": "GTA 6 BIKIN KAGET!", "title": "GTA 6 bikin kaget semua", "hook": "Lihat ini"}],
                      [{"i": 0, "jelas": 9, "penasaran": 9, "sinkron": 9, "jujur": True}], "isi klip GTA VI 6") != [])

# Transkrip (ElevenLabs) menulis angka dengan kata; judul AI memakai digit → tetap dianggap diucapkan.
# Dulu 9 dari 30 klip uji nyata jatuh ke judul mentah karena "angka tidak diucapkan".
from core.editor_review import numbers_supported  # noqa: E402
for claim, said, ok in [("29 KG?!", "kanan kiri dua puluh sembilan kilo", True), ("Pull up 5x", "pull up lima kali", True),
                        ("61% bocil", "enam puluh satu persen", True), ("Rp1.000 doang", "seribu doang", True),
                        ("12 polisi", "dua belas polisi", True), ("150 juta", "seratus lima puluh juta", True),
                        ("3000", "tiga ribu", True), ("100 kali", "lima kali", False), ("7 bintang", "bintang lima", False),
                        ("5 menit", "dua tiga menit", False)]:
    check(f"angka terucap: {claim!r} ← {said!r} = {ok}", numbers_supported(claim, said) is ok)

print("\nSemua uji paket judul/thumbnail/hook lulus" if not fails else f"\nGAGAL: {len(fails)} uji")
sys.exit(1 if fails else 0)
