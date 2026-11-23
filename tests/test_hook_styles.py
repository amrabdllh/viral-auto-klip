"""Uji hook bergaya (core/hook_styles) tanpa jaringan: AI dipalsukan, render sungguhan dengan Pillow + FFmpeg.

- markup *highlight* dan bagian "A | B" terbaca benar
- gaya multi-bagian yang tidak terpenuhi turun ke gaya satu bagian (tidak pernah merender kartu kosong)
- heuristik memilih gaya sesuai bentuk kalimat & menjaga variasi antar-klip
- jawaban AI yang mengarang angka / tidak sesuai isi klip ditolak → heuristik
- setting gaya tetap & 'card' dihormati
- 19 gaya merender semua frame dengan tinggi kanvas tetap
- render_polish menimpa urutan PNG hook di encode utama (FFmpeg sungguhan)

Jalankan: python tests/test_hook_styles.py
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import config  # noqa: E402
from core import ffx  # noqa: E402,F401  (ffmpeg/bin lokal ke PATH, sama seperti server)
from core import ai_providers, editing, hook_styles as hs  # noqa: E402

fails = []


def check(name, cond):
    print(("OK   " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)


# ---------------------------------------------------------------- markup
check("tokens: kata bertanda bintang ditonjolkan",
      hs.tokens("yang *cuma 1 dolar* deh") == [("yang", False), ("cuma", True), ("1", True), ("dolar", True), ("deh", False)])
check("split_parts: pemisah |", hs.split_parts("Gua bunuh | Tembak ban") == ["Gua bunuh", "Tembak ban"])
check("plain: markup dibuang", hs.plain("*Solo squad* | paling jago") == "Solo squad paling jago")
check("clean: emoji dibuang", hs.clean("Gila banget 😱🔥") == "Gila banget")
check("auto highlight: angka + satuan + penguat ditonjolkan",
      hs._auto_highlight("yang nambah darah cuma 1 dolar") == "yang nambah darah *cuma 1 dolar*")

# ---------------------------------------------------------------- normalize
st, tx = hs.normalize("bigstat", "Billy ngasih kita kerjaan 3000 dolar")
check("bigstat tanpa | → angka dipisah jadi bagian pertama", st == "bigstat" and hs.split_parts(tx)[0].startswith("3000"))
check("bigstat tanpa angka → turun ke stabilo", hs.normalize("bigstat", "Brando panik banget")[0] == "stabilo")
check("strike satu bagian → turun ke stabilo", hs.normalize("strike", "Gua bunuh tembak ban")[0] == "stabilo")
check("versus dari kata 'vs'", hs.split_parts(hs.normalize("versus", "Kopi sachet vs kopi kafe")[1])[:2] == ["Kopi sachet", "kopi kafe"])
st, tx = hs.normalize("poll", "Lebih enak sendiri atau bareng")
check("poll tanpa pilihan → pertanyaan + 2 pilihan bawaan", st == "poll" and len(hs.split_parts(tx)) == 3 and tx.split("|")[0].strip().endswith("?"))
check("checklist kurang butir → stack", hs.normalize("checklist", "Modal bikin konten")[0] == "stack")
st, tx = hs.normalize("stamp", "Minum air dingin bikin perut buncit")
check("stamp tanpa vonis → FAKTA", st == "stamp" and hs.split_parts(tx)[1] == "FAKTA")
check("gaya tak dikenal → stabilo", hs.normalize("ngawur", "teks apa saja")[0] == "stabilo")

# ---------------------------------------------------------------- heuristik
check("heuristik: angka → bigstat", hs.heuristic_style("Billy ngasih kerjaan 3000 dolar") == "bigstat")
check("heuristik: dua bagian → strike", hs.heuristic_style("Gua bunuh | Tembak ban") == "strike")
check("heuristik: pertanyaan → search/chat/reply", hs.heuristic_style("Kok bisa motornya dibegal?") in ("search", "chat", "reply"))
check("heuristik: menghindari gaya yang baru dipakai",
      hs.heuristic_style("Kok bisa motornya dibegal?", recent=["search"]) != "search")
seen = []
for h in ["Jason jadi striptis guys", "Brando panik banget", "Lebih enak sendiri deh", "Gua yang tembak tuh"]:
    seen.append(hs.heuristic_style(h, "humor", seen))
check("heuristik: 4 klip berturut-turut tidak memakai gaya yang sama berdempetan",
      all(a != b for a, b in zip(seen, seen[1:])))

# ---------------------------------------------------------------- plan + AI palsu
CLIP = {"hook_text": "Yang nambah darah cuma 1 dolar", "category": "humor",
        "transcript": "Ah mau beli coklat deh, nama darah nih. Ah ya nama darah, cuma 1 dollar. yang nambah darah cuma 1 dolar"}
orig_complete, orig_conf = ai_providers.complete, ai_providers.is_configured
ai_providers.is_configured = lambda p: True
try:
    ai_providers.complete = lambda *a, **k: json.dumps({"style": "search", "text": "yang nambah darah *cuma 1 dolar*"})
    p = hs.plan(CLIP, [], mode="auto")
    check("AI: gaya & markup dipakai bila jujur", p and p["style"] == "search" and p["by"] == "ai" and "*cuma 1 dolar*" in p["text"])
    ai_providers.complete = lambda *a, **k: json.dumps({"style": "bigstat", "text": "50000 | dolar buat darah"})
    p = hs.plan(CLIP, [], mode="auto")
    check("AI: angka karangan ditolak → heuristik", p and p["by"] == "heuristic")
    ai_providers.complete = lambda *a, **k: json.dumps({"style": "breaking", "text": "Presiden *mundur* hari ini"})
    p = hs.plan(CLIP, [], mode="auto")
    check("AI: klaim di luar isi klip ditolak → heuristik", p and p["by"] == "heuristic")
    ai_providers.complete = lambda *a, **k: "bukan json"
    p = hs.plan(CLIP, [], mode="auto")
    check("AI: balasan rusak → heuristik", p and p["by"] == "heuristic")
finally:
    ai_providers.complete, ai_providers.is_configured = orig_complete, orig_conf
check("setting gaya tetap dihormati", hs.plan(CLIP, [], mode="sticker")["style"] == "sticker")
check("setting 'card' → None (kartu lama)", hs.plan(CLIP, [], mode="card") is None)
check("tanpa hook → None", hs.plan({"hook_text": ""}, [], mode="auto") is None)

# ---------------------------------------------------------------- render semua gaya
SAMPLES = {
    "breaking": "Brando *dibegal* di tengah jalan", "stabilo": "yang nambah darah *cuma 1 dolar*",
    "sticker": "Jason *jadi striptis* guys", "notif": "Kamu wajib tahu ini *sebelum terlambat*",
    "chat": "Bro, kamu udah coba *cara ini* belum?", "stack": "Cara edit video *10x lebih cepat*",
    "strike": "Gua bunuh | Tembak ban", "typewriter": "Fakta: *90% orang* tidak tahu ini",
    "editorial": "*tapi jason* botak *ganteng banget* ya", "reply": "Kak, gimana caranya *konsisten upload*?",
    "poll": "Gaji besar tapi capek? | Gaji besar | Santai aja", "stamp": "Minum air dingin bikin *perut buncit* | MITOS",
    "tweet": "Gaji UMR tapi gaya hidup selebgram", "bigstat": "10000 | investasi buat keluarga",
    "search": "cara dapat duit dari hp *tanpa modal*", "versus": "Kopi sachet | Kopi kafe",
    "achievement": "Solo squad *paling jago*", "checklist": "Modal konten | HP biasa | Satu ide | 10 menit",
    "quote": "Kesuksesan bukan soal *bakat* tapi *konsistensi*",
}
check("19 gaya terdaftar & punya painter", len(hs.STYLES) == 19 and set(hs.STYLES) == set(hs.PAINTERS))
tmp = Path(tempfile.mkdtemp(prefix="hookstyles_"))
try:
    bad = []
    for style, text in SAMPLES.items():
        st, tx = hs.normalize(style, text)
        ctx = hs.make_ctx({"style": st, "text": tx}, 540, 2.0)
        sizes = {hs.frame(ctx, i / 30).size for i in range(0, 60, 7)}
        if len(sizes) != 1 or st != style:
            bad.append(f"{style}:{sizes}")
    check("semua gaya: kanvas lebar video & tinggi tetap di semua frame" + (f" ({bad})" if bad else ""), not bad)

    seq = hs.render_sequence({"style": "search", "text": SAMPLES["search"]}, 540, 960, tmp, duration=1.5, y_frac=0.11)
    frames = sorted(Path(seq["dir"]).glob("f*.png"))
    check("render_sequence: jumlah frame = durasi × 30", len(frames) == 45 and seq["frames"] == 45)
    check("render_sequence: posisi di zona aman atas", seq["y"] == int(0.11 * 960) and seq["x"] == 0)
    check("sfx: gaya ketik → bunyi 'type'", hs.sfx_events("search", 3.0)[0][1] == "type")
    check("sfx: stempel → dentum saat stempel jatuh", any(n == "impact" and t > 0.4 for t, n, _ in hs.sfx_events("stamp", 3.0)))

    # ---- FFmpeg: overlay urutan PNG di encode utama
    if shutil.which("ffmpeg"):
        src = tmp / "src.mp4"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=540x960:r=30:d=3",
                        "-f", "lavfi", "-i", "sine=f=440:d=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                        "-shortest", str(src)], check=True)
        out = tmp / "out.mp4"
        editing.render_polish(str(src), str(out), width=540, height=960, duration=3.0, ass_path=None, grade="none",
                              zoom_chain="", progress_bar=False, encode_args=["-c:v", "libx264", "-preset", "ultrafast", "-crf", "28"],
                              escape_path=lambda x: x.replace("\\", "/").replace(":", r"\:"), overlays=[], hook_seq=seq)
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
                               capture_output=True, text=True).stdout.strip()
        check(f"render_polish + hook_seq: video jadi ({probe} dtk)", out.exists() and abs(float(probe or 0) - 3.0) < 0.2)
        from PIL import Image
        import numpy as np
        f1, f2 = tmp / "a.png", tmp / "b.png"
        for t, fp in ((1.0, f1), (2.5, f2)):
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(t), "-i", str(out), "-frames:v", "1", str(fp)], check=True)
        # baris bar pencarian (putih) hanya ada selama hook tampil
        y = seq["y"] + seq["h"] // 3
        row_hook = np.asarray(Image.open(f1).convert("L"))[y, 60:480].mean()
        check("hook tampil di detik 1 (bar putih di zona hook)", row_hook > 200)
        bump = editing.sfx_path("ding")
        check("sfx 'ding' & 'type' tersintesis", bump.exists() and editing.sfx_path("type").exists())
    else:
        print("SKIP ffmpeg tidak ada di PATH")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
print("SEMUA LULUS" if not fails else f"{len(fails)} GAGAL: {fails}")
sys.exit(1 if fails else 0)
