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

# Judul harus tentang MOMEN UTAMA (ringkasan Editor-AI + yang terlihat di layar), bukan ucapan sampingan
prompts.clear()
focus_clip = {"transcript": TX, "ai_title": "lama", "thumb_text": "lama", "hook_text": "lama",
              "review": {"summary": "Mobil dicuri pakai slim jim dalam 3 detik", "visual": {"what": "pintu mobil kebuka"},
                         "rank_why": "cepat & bikin kaget"}}
clickbait.write(focus_clip, "GTA VI")
check("penulis & juri menerima momen utama", len(prompts) == 2 and all("MOMEN UTAMA" in p and "slim jim dalam 3 detik" in p
                                                                     and "pintu mobil kebuka" in p for p in prompts))
check("tanpa data Editor-AI: prompt tetap jalan tanpa blok momen", "MOMEN UTAMA" not in clickbait.main_moment({}))
side = clickbait._rank([{"thumb": "SLIM JIM SAKTI?!", "title": "NYOLONG pakai SLIM JIM?!", "hook": "Lihat ini"},
                        {"thumb": "TARIKAN MOBIL GILA", "title": "Tarikan mobilnya gila", "hook": "Gas pol"}],
                       [{"i": 0, "jelas": 8, "penasaran": 8, "sinkron": 9, "tepat": 9, "jujur": True},
                        {"i": 1, "jelas": 9, "penasaran": 9, "sinkron": 9, "tepat": 3, "jujur": True}], TX)
check("paket yang membahas hal sampingan (tepat < 6) ditolak", [p["thumb"] for p in side] == ["SLIM JIM SAKTI?!"])

# Nama streamer (riset outlier: judul yang menyebut streamer menang di 3 channel) & pola judul per niche
from datetime import date  # noqa: E402
from core import title_patterns  # noqa: E402

prompts.clear()
clickbait.write(clip, "AKHIRNYA RAMPOK BANK GTA 6 GAMEPLAY #11", streamer="Windah Basudara")
check("penulis diberi nama streamer", "STREAMER pemilik siaran" in prompts[0] and "Windah Basudara" in prompts[0])
check("juri tahu nama streamer = jujur", "Streamer pemilik siaran: Windah Basudara" in prompts[1])
check("pola GTA 6 masuk prompt penulis", "POLA JUDUL YANG TERBUKTI" in prompts[0] and "Ternyata bisa" in prompts[0])
check("pola tidak masuk prompt juri", "POLA JUDUL" not in prompts[1])

prompts.clear()
clickbait.write(clip, "Podcast santai bareng teman", streamer="")
check("tanpa streamer & niche lain: tidak ada blok nama/pola",
      "STREAMER pemilik" not in prompts[0] and "POLA JUDUL" not in prompts[0] and "Streamer pemilik" not in prompts[1])

prompts.clear()
clickbait.write(clip, "GTA VI", streamer="Windah Clips Official")
check("channel kumpulan klip tidak dipakai sebagai nama streamer",
      "STREAMER pemilik" not in prompts[0] and "Streamer pemilik" not in prompts[1])

prompts.clear()
clickbait.write(clip, "GTA VI", streamer="Windah Basudara", fixed_title="Judul yang sudah tayang di YouTube")
check("judul terkunci: nama & pola tidak ditawarkan", "STREAMER pemilik" not in prompts[0] and "POLA JUDUL" not in prompts[0])

check("niche GTA 6 dikenali dari 'GTA VI' / 'gta 6' / 'Grand Theft Auto VI'",
      all(title_patterns.niche_for(t) for t in ("GTA VI GAMEPLAY", "main gta 6", "Grand Theft Auto VI")))
check("GTA 5 / GTA Online bukan niche GTA 6", not title_patterns.niche_for("GTA 5 RP") and
      not title_patterns.niche_for("GTA Online heist") and not title_patterns.niche_for("GTA 60fps"))
check("pola kedaluwarsa tidak dipakai", title_patterns.prompt_block("GTA 6", today=date(2027, 6, 1)) == ""
      and title_patterns.prompt_block("GTA 6", today=date(2026, 12, 1)) != "")

clickbait.apply([dict(a)], "GTA VI", prerender=True, streamer="Tara Arts Game Indonesia")
check("apply meneruskan nama streamer", any("Tara Arts Game Indonesia" in p for p in prompts))

# Aturan sinkron: SATU kata kunci yang sama di thumbnail, judul, dan hook
sk = clickbait.shared_keyword
check("kata kunci ditemukan (BOTOL)", sk({"thumb": "RAMPOK GTA 6 PAKE BOTOL", "title": "Windah RAMPOK toko modal BOTOL?!",
                                       "hook": "Taktik gila lempar botol ke kasir."}) == "botol")
check("imbuhan boleh beda (rampok ~ ngerampok)", sk({"thumb": "RAMPOK BANK GTA 6", "title": "Nekat banget ngerampok bank",
                                                    "hook": "Baru masuk langsung merampok"}) == "rampok")
check("pilihan penulis dipakai bila ada di ketiganya", sk({"kunci": "polisi", "thumb": "KABUR DARI POLISI GTA 6",
                                                          "title": "Kabur dari POLISI pakai motor", "hook": "Polisinya sampai bingung"}) == "polisi")
check("nama game & kata umum bukan kata kunci", sk({"thumb": "GTA 6 GILA BANGET", "title": "GTA 6 gila banget sih",
                                                   "hook": "Ini GTA 6 gila"}) == "")
check("tanpa kata bersama di hook = tidak ada kata kunci", sk({"thumb": "CURI MOBIL PAKAI SLIM JIM?!",
                                                              "title": "NYOLONG Mobil Cuma Modal SLIM JIM?!",
                                                              "hook": "Lihat seberapa cepat pintunya kebuka"}) == "")
both = [{"thumb": "SLIM JIM SAKTI?!", "title": "NYOLONG pakai SLIM JIM?!", "hook": "Lihat ini"},
        {"thumb": "SLIM JIM BUKA MOBIL", "title": "Buka mobil cuma pakai SLIM JIM", "hook": "Slim jim ini sakti banget"}]
both = [clickbait._clean_pkg(p, None, None) for p in both]
order = clickbait._rank(both, [{"i": 0, "jelas": 10, "penasaran": 10, "sinkron": 9, "tepat": 9, "jujur": True},
                               {"i": 1, "jelas": 8, "penasaran": 8, "sinkron": 9, "tepat": 9, "jujur": True}], TX)
check("paket dengan kata kunci bersama didahulukan walau skornya sedikit lebih rendah",
      [p["thumb"] for p in order] == ["SLIM JIM BUKA MOBIL", "SLIM JIM SAKTI?!"])
check("tanpa satu pun kata kunci: paket terbaik tetap dipakai", clickbait._rank(both[:1],
      [{"i": 0, "jelas": 10, "penasaran": 10, "sinkron": 9, "tepat": 9, "jujur": True}], TX) == both[:1])
# Nama streamer DI DEPAN judul (pilihan pengguna: nama ditonjolkan)
nf = clickbait.name_first
check("judul diawali nama panggilan streamer", nf("Windah KAGET Tarikan BUGATTI GTA 6! 😱", "Windah Basudara"))
check("'Bang Windah …' juga dihitung diawali nama", nf("Bang Windah NEKAT rampok toko", "Windah Basudara"))
check("nama di tengah judul tidak dihitung", not nf("KAGET! TARIKAN Bugatti Bikin Windah Histeris🔥", "Windah Basudara"))
check("kata pengisi nama channel bukan nama panggilan", not nf("GAMING paling seru", "Tara Arts Game Indonesia")
      and nf("Tara SYOK lihat GTA 6", "Tara Arts Game Indonesia"))
check("tanpa streamer / channel klip: tidak ada syarat nama", not nf("Windah KAGET", "") and not nf("Windah KAGET", "Windah Clips"))
two = [clickbait._clean_pkg(p, None, None) for p in (
    {"thumb": "TARIKAN BUGATTI GTA 6", "title": "KAGET! TARIKAN Bugatti Bikin Windah Histeris🔥", "hook": "Tarikan bugatti ini gila"},
    {"thumb": "BUGATTI GTA 6 NGEBUT", "title": "Windah KAGET Tarikan BUGATTI GTA 6! 😱", "hook": "Bugatti ini kenceng banget"})]
sc2 = [{"i": 0, "jelas": 10, "penasaran": 10, "sinkron": 9, "tepat": 9, "jujur": True},
       {"i": 1, "jelas": 8, "penasaran": 8, "sinkron": 9, "tepat": 9, "jujur": True}]
check("judul berawalan nama streamer menang walau skornya sedikit lebih rendah",
      clickbait._rank(two, sc2, "bugatti tarikan gta 6", streamer="Windah Basudara")[0]["title"].startswith("Windah KAGET"))
check("tanpa streamer: urutan skor biasa", clickbait._rank(two, sc2, "bugatti tarikan gta 6")[0]["title"].startswith("KAGET!"))
prompts.clear()
clickbait.write(clip, "GTA VI", streamer="Windah Basudara")
check("penulis diminta SEMUA judul diawali nama", "SEMUA 5 JUDUL WAJIB DIAWALI" in prompts[0])
check("penulis & juri mengutamakan rasa penasaran ('apa nih??')", "apa nih??" in prompts[0] and "apa nih??" in prompts[1]
      and "saling melengkapi" not in prompts[0].replace("tidak perlu saling melengkapi", ""))

prompts.clear()
clickbait.write(clip, "GTA VI")
check("penulis diminta kata kunci wajib", "KATA KUNCI WAJIB" in prompts[0] and '"kunci"' in prompts[0])

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
