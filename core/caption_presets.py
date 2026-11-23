"""(v7) Preset subtitle bergaya — 37 animasi kata per kata di atas libass (ASS).

Tiap kata ditempatkan sendiri (`\\pos`) dari tata letak yang kita hitung dengan metrik font yang sama
seperti libass (OS/2 winAscent/winDescent). Karena posisi tiap kata diketahui, kita bisa menggambar
pil yang bergeser ke kata aktif, stabilo di belakang kata kunci, lingkaran spidol, gelembung chat,
kaca, kotak per baris, salinan glitch RGB, getar, gelombang, dsb. — semuanya tetap satu file .ass
yang di-burn di encode utama (tidak ada encode tambahan).

Pemakaian: config.CAPTION_STYLES[<id>] berisi {"preset": <id>, ...}; utils.generate_ass_subtitles
meneruskan ke `write_ass` bila kunci "preset" ada. Gaya lama (viral, modern, ...) tidak berubah.
"""
from __future__ import annotations

import math
import random
import re
import struct
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple


REF_W = 1080  # ukuran preset ditulis untuk kanvas 1080x1920

# ============================================================================ preset

_DEF = dict(
    font="black", size_k=1.0, case="upper", color="FFFFFF", outline="000000", bord=7, shad=0,
    shadow="000000", shadow_a=0x80, words=4, lines=2, reveal="chunk", enter=None, stagger=0.0,
    active="color", act_color="FFE600", act_scale=112, sticky=False, dim=0, keyword=None, kw_color=None,
    bg=None, palette=None, gradient=None, alt=None, tilt=0.0, word_tilt=0.0, glow=None, pulse=False,
    sticker=None, hollow=False, emoji=None, stack=False, gap=1.0, lh=None, fade_out=0, act_text="111111",
    max_w=0.86, hold=0.35, wave=0.0, glitch=("FF1E56", "00E5FF"), blur_others=0.0, label="", desc="",
)

PRESETS: Dict[str, Dict] = {
    "no_animation": dict(label="Tanpa Animasi", desc="Putih tebal bergaris hitam, diam.", active=None, words=5),
    "karaoke": dict(label="Karaoke", desc="Kata berubah kuning saat diucapkan dan tetap kuning.", sticky=True,
                    act_color="FFD400", words=5),
    "pop": dict(label="Pop", desc="Baris muncul membesar, kata aktif kuning & membesar.", enter="pop", active="pop"),
    "bounce": dict(label="Bounce", desc="Kata jatuh memantul satu per satu.", reveal="word", enter="bounce",
                   act_color="5CFF7A"),
    "glitch": dict(label="Glitch", desc="Kata aktif pecah RGB merah-biru.", reveal="word", enter="flicker",
                   active="glitch", act_color="FFFFFF"),
    "shake": dict(label="Getar", desc="Kata aktif merah dan bergetar.", active="shake", act_color="FF3B3B"),
    "glow": dict(label="Glow", desc="Teks bercahaya biru, kata aktif menyala.", glow="00E5FF", bord=3,
                 outline="00303A", active="glowup", act_color="FFFFFF"),
    "shadow": dict(label="Bayangan", desc="Bayangan tegas ungu ala poster retro.", bord=0, shad=7,
                   shadow="7A1FFF", shadow_a=0x00, act_color="FFE600"),
    "fade": dict(label="Fade", desc="Masuk-keluar halus, huruf biasa.", font="semi", case="asis", enter="fade",
                 fade_out=140, bord=4, act_color="FFE27A", words=5),
    "wave": dict(label="Gelombang", desc="Kata naik-turun seperti ombak, kata aktif terangkat.", active="lift",
                 wave=9.0, act_color="FF8BD2"),
    # klipers "Box": kotak hijau (#22c55e) di belakang kata yang diucapkan, teks putih
    "box": dict(label="Kotak", desc="Kotak berwarna di belakang kata yang diucapkan.", active="pill", pill_r=0.16,
                act_text="FFFFFF", act_color="22C55E"),
    "motion": dict(label="Motion", desc="Kata naik satu per satu, kata aktif hijau lime.", reveal="word",
                   enter="rise", active="pop", act_color="A3E635"),
    "neon-pulse": dict(label="Neon Pulse", desc="Neon pink berdenyut.", font="semi", color="FFE3F8",
                       glow="FF2BD6", pulse=True, bord=2, outline="FF2BD6", act_color="FFFFFF"),
    "gradient-slide": dict(label="Gradasi Geser", desc="Kata meluncur dari kiri, warna gradasi oranye-pink.",
                           enter="slide", stagger=0.05, gradient=("FFB000", "FF3D7F"), active="pop",
                           act_color="FFFFFF"),
    "color-burst": dict(label="Ledakan Warna", desc="Tiap kata yang diucapkan dapat warna baru.",
                        reveal="word", enter="pop", active="burst", sticky=True, act_scale=118,
                        palette=("FF3B6B", "FFD400", "3BE8FF", "7CFF5B", "FF8A00", "C77DFF")),
    "bold-impact": dict(label="Bold Impact", desc="Huruf Impact besar, 2 kata per layar.", font="impact",
                        size_k=1.38, words=2, bord=8, active="pop", act_color="FF2D2D", lh=1.02),
    "flicker": dict(label="Kedip", desc="Kata menyala berkedip seperti lampu.", reveal="word", enter="flicker",
                    glow="FFFFFF", bord=4, act_color="FFF27A"),
    "outline-pop": dict(label="Outline Pop", desc="Huruf berongga, kata aktif terisi kuning.", hollow=True,
                        bord=4, outline="FFFFFF", active="fill", act_color="FFE600"),
    "minimal-apple": dict(label="Minimal Clean", desc="Bersih tanpa garis, kata lain redup.", font="semi",
                          case="asis", bord=0, shad=2, shadow="000000", shadow_a=0x60, dim=0x70,
                          act_color="FFFFFF", words=5),
    "horror-glitch": dict(label="Horor Glitch", desc="Merah gelap, bergetar dan pecah.", font="serif",
                          color="EDEDED", outline="3A0000", act_color="FF1A1A", active="shake+glitch",
                          glitch=("FF0000", "000000"), reveal="word", enter="flicker"),
    "candy-pop": dict(label="Candy Pop", desc="Warna permen pastel, garis putih tebal.", font="heavy",
                      palette=("FF8FC7", "8FD3FF", "FFE27A", "B8F28C", "C9A7FF"), outline="FFFFFF", bord=8,
                      shad=5, shadow="5B2A86", shadow_a=0x20, reveal="word", enter="bounce", active="pop",
                      act_color=None),
    "street-bold": dict(label="Street Bold", desc="Impact kuning miring, menghantam.", font="impact",
                        color="FFE600", bord=9, tilt=4.0, enter="slam", size_k=1.15, words=3,
                        act_color="FFFFFF", lh=1.0),
    "dynamic": dict(label="Dinamis", desc="Kata kunci lebih besar & berwarna.", keyword="big",
                    kw_color="FFE600", enter="pop", active="pop", act_color="5CF2FF"),
    "pill-slide": dict(label="Pill Slide", desc="Pil hijau bergeser ke kata yang diucapkan.", active="pill",
                       act_color="A3E635", bord=6),
    "stabilo": dict(label="Stabilo", desc="Coretan stabilo kuning di belakang kata kunci.", keyword="marker",
                    kw_color="FFEB3B", act_color="FFF4A8"),
    "focus-blur": dict(label="Fokus Blur", desc="Hanya kata aktif yang tajam.", active="focus", dim=0x58,
                       blur_others=2.6, act_color="FFFFFF"),
    "sticker": dict(label="Stiker", desc="Kata seperti stiker: tepi putih tebal, miring acak.", font="heavy",
                    color="FFE600", outline="111111", bord=5, sticker="FFFFFF", word_tilt=5.0, enter="pop",
                    active="pop", act_color="FFFFFF"),
    "flip-3d": dict(label="Flip 3D", desc="Kata berputar masuk 3D.", reveal="word", enter="flip",
                    act_color="7CF5FF"),
    "chat-bubble": dict(label="Chat Bubble", desc="Teks putih di gelembung chat biru.", font="bold", case="asis",
                        bg="bubble", color="1C1C1E", bord=0, act_color="0A84FF", enter="pop", words=5),
    "emoji-swap": dict(label="Emoji", desc="Emoji berwarna muncul di kata kunci.", enter="pop", active="pop",
                       emoji="swap"),
    "counter-roll": dict(label="Gulir", desc="Kata menggulir naik dari bawah garis.", reveal="word",
                         enter="roll", act_color="FFB020"),
    "marker-circle": dict(label="Lingkar Spidol", desc="Kata kunci dilingkari spidol merah.",
                          keyword="circle", kw_color="FF3B3B", act_color="FFE600"),
    "slam": dict(label="Slam", desc="Kata menghantam layar, 2 kata per layar.", font="impact", size_k=1.3,
                 words=2, reveal="word", enter="slam", act_color="FF3B3B", lh=1.02),
    "jelly": dict(label="Jelly", desc="Kata aktif kenyal bergoyang.", font="heavy", active="jelly",
                  act_color="FF7AD9"),
    "live-reaction": dict(label="Live Reaction", desc="Gaya komentar live + emoji reaksi.", font="bold",
                          case="asis", bord=0, shad=3, shadow="000000", shadow_a=0x40, act_color="FF4FA3",
                          emoji="reaction", words=5),
    "liquid-glass": dict(label="Liquid Glass", desc="Panel kaca bening di belakang teks.", font="bold",
                         case="asis", bg="glass", bord=0, shad=2, shadow="000000", shadow_a=0x80,
                         act_color="7CF5FF", words=5),
    "word-stack": dict(label="Word Stack", desc="Kata ditumpuk, tiap baris memenuhi lebar.", stack=True,
                       words=3, reveal="word", enter="pop", active=None, alt=("FFFFFF", "FFE600"), lh=1.0),
}

# (v7) Parameter persis klipers.pro (tabel `u2` di bundle klien, lihat docs/klipers-re/LAPORAN_KLIPERS.md §12.1):
# id: (font, warna teks, warna sorot, warna garis, tebal garis, kapitalisasi, kata per layar (0 = auto), warna kotak)
KLIPERS = {
    "no_animation": ("Montserrat", "FFFFFF", "FFFFFF", "000000", 8, "upper", 0, None),
    "karaoke": ("Montserrat", "FFFFFF", "FBBF24", "000000", 8, "upper", 0, None),
    "box": ("Montserrat", "FFFFFF", "22C55E", "000000", 6, "upper", 0, '22C55E'),
    "pop": ("Montserrat", "FFFFFF", "22C55E", "000000", 8, "upper", 0, None),
    "bounce": ("Montserrat", "FFFFFF", "EAB308", "000000", 8, "upper", 0, None),
    "glitch": ("Roboto Mono", "00FFFF", "FF00FF", "FF00FF", 8, "upper", 0, None),
    "shake": ("Montserrat", "FFFFFF", "EF4444", "000000", 8, "upper", 0, None),
    "glow": ("Montserrat", "FFFFFF", "F0ABFC", "7C3AED", 8, "upper", 0, None),
    "shadow": ("Montserrat", "FFFFFF", "FACC15", "000000", 8, "upper", 0, None),
    "fade": ("Montserrat", "FFFFFF", "FFFFFF", "000000", 8, "upper", 0, None),
    "wave": ("Montserrat", "FFFFFF", "0EA5E9", "0369A1", 8, "upper", 0, None),
    "klipers-motion": ("Montserrat 800", "FFFFFF", "22C55E", "000000", 8, "upper", 5, None),
    "neon-pulse": ("Orbitron", "00FFFF", "FF00FF", "0A0A2E", 8, "upper", 0, None),
    "gradient-slide": ("Teko", "FFFFFF", "F97316", "000000", 8, "upper", 0, None),
    "color-burst": ("Bangers", "FFFFFF", "FF3366", "000000", 8, "upper", 0, None),
    "bold-impact": ("Archivo Black", "FFFFFF", "FBBF24", "000000", 8, "upper", 0, None),
    "flicker": ("Montserrat", "FFFFFF", "FBBF24", "000000", 8, "upper", 0, None),
    "outline-pop": ("Bebas Neue", "FFFFFF", "22C55E", "000000", 8, "upper", 0, None),
    "minimal-apple": ("Quicksand", "FFFFFF", "60A5FA", "000000", 4, "asis", 0, None),
    "horror-glitch": ("Creepster", "FF6B6B", "FF1744", "1A0000", 8, "asis", 0, None),
    "candy-pop": ("Fredoka One", "FFFFFF", "F472B6", "000000", 3, "upper", 0, None),
    "street-bold": ("Anton", "FFD700", "FFFFFF", "000000", 10, "upper", 0, None),
    "dynamic": ("Montserrat 800", "FFFFFF", "22C55E", "000000", 8, "upper", 0, None),
    "pill-slide": ("Montserrat 800", "FFFFFF", "A3E635", "000000", 7, "upper", 0, None),
    "stabilo": ("Montserrat 800", "FFFFFF", "FDE047", "000000", 7, "upper", 0, None),
    "focus-blur": ("Poppins", "FFFFFF", "FFFFFF", "000000", 4, "asis", 0, None),
    "sticker": ("Fredoka One", "FFFFFF", "FDE047", "111111", 8, "upper", 0, None),
    "flip-3d": ("Montserrat 800", "FFFFFF", "22D3EE", "000000", 7, "upper", 0, None),
    "chat-bubble": ("Poppins", "FFFFFF", "0A84FF", "000000", 0, "asis", 0, None),
    "emoji-swap": ("Montserrat 800", "FFFFFF", "FACC15", "000000", 7, "upper", 0, None),
    "counter-roll": ("Poppins", "FFFFFF", "4ADE80", "000000", 6, "asis", 0, None),
    "marker-circle": ("Montserrat 800", "FFFFFF", "FF3B30", "000000", 7, "upper", 0, None),
    "slam": ("Anton", "FFFFFF", "FF2D55", "000000", 8, "upper", 0, None),
    "jelly": ("Fredoka One", "FFFFFF", "FDE68A", "7C3AED", 8, "asis", 0, None),
    "live-reaction": ("Poppins", "FFFFFF", "FDE047", "000000", 3, "asis", 0, None),
    "liquid-glass": ("Poppins", "FFFFFF", "FFFFFF", "000000", 0, "asis", 0, None),
    "word-stack": ("Anton", "FFFFFF", "FACC15", "000000", 7, "upper", 0, None),
}
_KLIPERS_ALIAS = {"motion": "klipers-motion"}
# nama font klipers → kunci _FONT_CANDS (file di fonts/, cadangan font Windows/Linux)
_KLIPERS_FONT = {"Montserrat 800": "montserrat800", "Montserrat": "montserrat", "Anton": "anton", "Poppins": "poppins",
                 "Fredoka One": "fredoka", "Bebas Neue": "bebas", "Orbitron": "orbitron", "Teko": "teko",
                 "Archivo Black": "archivo", "Creepster": "creepster", "Quicksand": "quicksand", "Bangers": "bangers",
                 "Roboto Mono": "robotomono"}
REF_CAP = 0.70  # tinggi kapital acuan (Montserrat ExtraBold) — semua font dinormalkan ke sini, seperti klipers


def _apply_klipers(pid: str, p: Dict) -> None:
    """Timpa font/warna/garis/kapitalisasi preset dengan nilai persis klipers; pola animasi kita tetap."""
    row = KLIPERS.get(_KLIPERS_ALIAS.get(pid, pid))
    if not row:
        return
    fontname, fg, hl, stroke, width, case, words, box = row
    p["font"] = _KLIPERS_FONT.get(fontname, p["font"])
    p["color"], p["act_color"], p["bord"], p["case"] = fg, hl, width, case
    p["outline"] = fg if p.get("hollow") else stroke  # outline-pop: huruf berongga bergaris warna teks
    if words:
        p["words"] = words
    if box:
        p["box_color"] = box
    if p.get("kw_color"):
        p["kw_color"] = hl
    if p.get("glow"):  # glow = warna garis klipers (ungu); neon = warna sorot (magenta)
        p["glow"] = hl if pid == "neon-pulse" else stroke
    if pid == "glitch":
        p["glitch"] = (hl, fg)
    if pid == "chat-bubble":  # klipers: teks putih di gelembung biru (warna sorot)
        p["bubble_color"], p["act_color"] = hl, "FFFFFF"
    p["size_k"] = 1.0  # klipers: ukuran sama untuk semua preset (dinormalkan per tinggi kapital)


HIGHLIGHTS = {"lime": "A3E635", "yellow": "FFE600", "red": "FF3B3B", "cyan": "3BE8FF", "pink": "FF4FA3",
              "orange": "FF9F1A", "white": "FFFFFF"}


def resolve(pid: str) -> Dict:
    """Preset + override dari Pengaturan (font, kapitalisasi, kata per layar, warna sorot, skala)."""
    p = dict(_DEF)
    p.update(PRESETS.get(pid, {}))
    _apply_klipers(pid, p)
    font = str(getattr(config, "CAPTION_FONT", "auto") or "auto").lower()
    if font in _FONT_CANDS:
        p["font"] = font
    case = str(getattr(config, "CAPTION_CASE", "auto") or "auto").lower()
    if case in ("upper", "title", "lower", "asis"):
        p["case"] = case
    words = str(getattr(config, "CAPTION_WORDS", "auto") or "auto")
    if words.isdigit() and 1 <= int(words) <= 6:
        p["words"] = int(words)
    hl = str(getattr(config, "CAPTION_HIGHLIGHT", "auto") or "auto").lower()
    if hl in HIGHLIGHTS:
        p["act_color"] = HIGHLIGHTS[hl]
        if p.get("kw_color"):
            p["kw_color"] = HIGHLIGHTS[hl]
    try:
        p["size_k"] *= max(0.5, min(1.8, float(getattr(config, "CAPTION_SCALE", 1.0) or 1.0)))
    except (TypeError, ValueError):
        pass
    if p["lh"] is None:
        p["lh"] = 1.28 if p["case"] == "upper" else 1.34
    return p


def style_entries() -> Dict[str, Dict]:
    """Entri untuk config.CAPTION_STYLES (dipakai semua jalur render)."""
    out = {}
    for pid, pr in PRESETS.items():
        out[pid] = {"preset": pid, "label": pr["label"], "desc": pr.get("desc", ""), "font_size": 76,
                    "vertical_position": 420, "emoji_stickers": pr.get("emoji")}
    return out


# ============================================================================ font & metrik

_WIN = "C:/Windows/Fonts/"
_DJ = "/usr/share/fonts/truetype/dejavu/"
_FONT_CANDS: Dict[str, List[Tuple[str, int, str]]] = {
    # (v7) font klipers (Google Fonts OFL, di folder fonts/) — nama keluarga = nameID 1 file-nya
    "montserrat800": [("Montserrat ExtraBold", 0, "Montserrat-ExtraBold.ttf"), ("Arial Black", 0, _WIN + "ariblk.ttf"),
                      ("DejaVu Sans", 1, _DJ + "DejaVuSans-Bold.ttf")],
    "montserrat": [("Montserrat", 1, "Montserrat-Bold.ttf"), ("Arial", 1, _WIN + "arialbd.ttf"),
                   ("DejaVu Sans", 1, _DJ + "DejaVuSans-Bold.ttf")],
    "anton": [("Anton", 0, "Anton-Regular.ttf"), ("Impact", 0, _WIN + "impact.ttf"),
              ("DejaVu Sans Condensed", 1, _DJ + "DejaVuSansCondensed-Bold.ttf")],
    "poppins": [("Poppins", 1, "Poppins-Bold.ttf"), ("Segoe UI", 1, _WIN + "segoeuib.ttf"), ("DejaVu Sans", 1, _DJ + "DejaVuSans-Bold.ttf")],
    "fredoka": [("Fredoka One", 0, "FredokaOne-Regular.ttf"), ("Segoe UI Black", 0, _WIN + "seguibl.ttf"),
                ("DejaVu Sans", 1, _DJ + "DejaVuSans-Bold.ttf")],
    "bebas": [("Bebas Neue", 0, "BebasNeue-Regular.ttf"), ("Impact", 0, _WIN + "impact.ttf"),
              ("DejaVu Sans Condensed", 1, _DJ + "DejaVuSansCondensed-Bold.ttf")],
    "orbitron": [("Orbitron", 1, "Orbitron-Bold.ttf"), ("Bahnschrift", 1, _WIN + "bahnschrift.ttf"), ("DejaVu Sans", 1, _DJ + "DejaVuSans-Bold.ttf")],
    "teko": [("Teko", 1, "Teko-SemiBold.ttf"), ("Impact", 0, _WIN + "impact.ttf"),
             ("DejaVu Sans Condensed", 1, _DJ + "DejaVuSansCondensed-Bold.ttf")],
    "archivo": [("Archivo Black", 0, "ArchivoBlack-Regular.ttf"), ("Arial Black", 0, _WIN + "ariblk.ttf"),
                ("DejaVu Sans", 1, _DJ + "DejaVuSans-Bold.ttf")],
    "creepster": [("Creepster", 0, "Creepster-Regular.ttf"), ("Georgia", 1, _WIN + "georgiab.ttf"),
                  ("DejaVu Serif", 1, _DJ + "DejaVuSerif-Bold.ttf")],
    "quicksand": [("Quicksand", 1, "Quicksand-Bold.ttf"), ("Segoe UI Semibold", 0, _WIN + "seguisb.ttf"),
                  ("DejaVu Sans", 1, _DJ + "DejaVuSans-Bold.ttf")],
    "bangers": [("Bangers", 0, "Bangers-Regular.ttf"), ("Impact", 0, _WIN + "impact.ttf"),
                ("DejaVu Sans Condensed", 1, _DJ + "DejaVuSansCondensed-Bold.ttf")],
    "robotomono": [("Roboto Mono", 1, "RobotoMono-Bold.ttf"), ("Consolas", 1, _WIN + "consolab.ttf"),
                   ("DejaVu Sans Mono", 1, _DJ + "DejaVuSansMono-Bold.ttf")],
    "black": [("Montserrat ExtraBold", 0, "Montserrat-ExtraBold.ttf"),
              ("Arial Black", 0, "C:/Windows/Fonts/ariblk.ttf"),
              ("DejaVu Sans", 1, "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")],
    "impact": [("Anton", 0, "Anton-Regular.ttf"), ("Impact", 0, "C:/Windows/Fonts/impact.ttf"),
               ("DejaVu Sans Condensed", 1, "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf")],
    "heavy": [("Poppins", 900, "Poppins-Black.ttf"), ("Segoe UI Black", 0, "C:/Windows/Fonts/seguibl.ttf"),
              ("Arial Black", 0, "C:/Windows/Fonts/ariblk.ttf"),
              ("DejaVu Sans", 1, "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")],
    "bold": [("Montserrat ExtraBold", 0, "Montserrat-ExtraBold.ttf"), ("Segoe UI", 1, "C:/Windows/Fonts/segoeuib.ttf"),
             ("Arial", 1, "C:/Windows/Fonts/arialbd.ttf"),
             ("DejaVu Sans", 1, "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")],
    "semi": [("Poppins SemiBold", 0, "Poppins-SemiBold.ttf"), ("Segoe UI Semibold", 0, "C:/Windows/Fonts/seguisb.ttf"),
             ("Arial", 1, "C:/Windows/Fonts/arialbd.ttf"),
             ("DejaVu Sans", 1, "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")],
    "serif": [("Georgia", 1, "C:/Windows/Fonts/georgiab.ttf"),
              ("DejaVu Serif", 1, "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf")],
    "mono": [("Consolas", 1, "C:/Windows/Fonts/consolab.ttf"),
             ("DejaVu Sans Mono", 1, "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf")],
}


class _Font:
    def __init__(self, family: str, bold: int, path: Optional[str]):
        self.family, self.bold, self.path = family, bold, path
        upm, asc, desc = _ttf_metrics(path) if path else (1000, 1000, 250)
        self.upm, self.asc, self.desc = upm, asc, desc
        self.h_ratio = (asc + desc) / upm  # \fs libass = tinggi asc+desc
        self.cap = 0.72
        self.adv = 0.7
        self.desc_em = desc / upm
        if path:
            try:
                f = self.pil(800)  # em 200 px (argumen = em x 4)
                top = f.getbbox("H", anchor="ls")[1]
                self.cap = -top / 200.0
                # lebar rata-rata huruf kapital per em → ukuran visual font sempit (Anton, Bebas, Teko) bisa disamakan
                self.adv = f.getlength("ABCDEFGHIJKLMNOPQRSTUVWXYZ") / 26 / 200.0
            except Exception:
                pass

    @lru_cache(maxsize=64)
    def pil(self, em4: int):
        from PIL import ImageFont
        return ImageFont.truetype(self.path, max(4, em4) / 4.0) if self.path else None

    def width(self, text: str, em: float) -> float:
        f = self.pil(int(round(em * 4))) if self.path else None
        if f is None:
            return len(text) * em * 0.62
        return float(f.getlength(text))

    def fs(self, em: float) -> float:
        return em * self.h_ratio

    def center_off(self, em: float) -> float:
        """Jarak baseline → titik tengah kotak baris libass (\\an5), dalam px."""
        return (self.asc - self.desc) / self.upm * em / 2.0


def _ttf_metrics(path: str) -> Tuple[int, int, int]:
    """(unitsPerEm, winAscent, winDescent) — sama dengan yang dipakai libass untuk \\fs."""
    try:
        data = Path(path).read_bytes()
        off = struct.unpack(">I", data[12:16])[0] if data[:4] == b"ttcf" else 0
        num = struct.unpack(">H", data[off + 4:off + 6])[0]
        tables = {}
        for i in range(num):
            rec = data[off + 12 + 16 * i: off + 28 + 16 * i]
            tables[rec[:4]] = struct.unpack(">I", rec[8:12])[0]
        upm = struct.unpack(">H", data[tables[b"head"] + 18: tables[b"head"] + 20])[0]
        os2 = tables.get(b"OS/2")
        if os2 is not None:
            wa, wd = struct.unpack(">hh", data[os2 + 74: os2 + 78])
            if wa + wd > 0:
                return upm, wa, wd
        hh = tables[b"hhea"]
        a, d = struct.unpack(">hh", data[hh + 4: hh + 8])
        return upm, a, -d
    except Exception:
        return 1000, 1000, 250


_FONTS: Dict[str, _Font] = {}


def visual_scale(fnt: "_Font") -> float:
    """Faktor em agar luas visual huruf (√ tinggi kapital × lebar rata-rata) sama dengan Montserrat ExtraBold.
    Hanya menyamakan tinggi kapital membuat font sempit tampak kecil; luas visual lebih mendekati mata."""
    ref = font("montserrat800")
    vis = (max(fnt.cap, 0.3) * max(getattr(fnt, "adv", 0.7), 0.3)) ** 0.5
    rv = (max(ref.cap, 0.3) * max(getattr(ref, "adv", 0.7), 0.3)) ** 0.5
    return max(0.8, min(1.35, rv / vis))


def font(kind: str) -> _Font:
    if kind not in _FONTS:
        found = None
        for fam, bold, cand in _FONT_CANDS.get(kind, _FONT_CANDS["black"]):
            p = Path(cand)
            if not p.is_absolute():
                p = Path(config.FONT_DIR) / cand
            if p.exists():
                found = (fam, bold, str(p))
                break
        _FONTS[kind] = _Font(*(found or ("Arial", 1, None)))
    return _FONTS[kind]


# ============================================================================ util ASS

def _col(hexstr: str) -> str:
    h = str(hexstr or "FFFFFF").lstrip("#").upper()
    if len(h) != 6:
        h = "FFFFFF"
    return f"&H{h[4:6]}{h[2:4]}{h[0:2]}&"


def _alpha(v: int) -> str:
    return f"&H{max(0, min(255, int(v))):02X}&"


def _mix(a: str, b: str, t: float) -> str:
    ca = [int(a[i:i + 2], 16) for i in (0, 2, 4)]
    cb = [int(b[i:i + 2], 16) for i in (0, 2, 4)]
    return "".join(f"{int(round(x + (y - x) * t)):02X}" for x, y in zip(ca, cb))


def _cs(t: float) -> int:
    return int(round(max(0.0, t) * 100))


def _ts(cs: int) -> str:
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, c = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


def _esc(text: str) -> str:
    return str(text).replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")


def _rrect(w: float, h: float, r: float) -> str:
    r = max(0.0, min(r, w / 2, h / 2))
    k = 0.5523 * r
    f = lambda v: f"{v:.1f}"  # noqa: E731
    return (f"m {f(r)} 0 l {f(w - r)} 0 b {f(w - r + k)} 0 {f(w)} {f(r - k)} {f(w)} {f(r)} "
            f"l {f(w)} {f(h - r)} b {f(w)} {f(h - r + k)} {f(w - r + k)} {f(h)} {f(w - r)} {f(h)} "
            f"l {f(r)} {f(h)} b {f(r - k)} {f(h)} 0 {f(h - r + k)} 0 {f(h - r)} "
            f"l 0 {f(r)} b 0 {f(r - k)} {f(r - k)} 0 {f(r)} 0")


def _ellipse(w: float, h: float) -> str:
    rx, ry = w / 2, h / 2
    kx, ky = 0.5523 * rx, 0.5523 * ry
    f = lambda v: f"{v:.1f}"  # noqa: E731
    return (f"m {f(rx)} 0 b {f(rx + kx)} 0 {f(w)} {f(ry - ky)} {f(w)} {f(ry)} "
            f"b {f(w)} {f(ry + ky)} {f(rx + kx)} {f(h)} {f(rx)} {f(h)} "
            f"b {f(rx - kx)} {f(h)} 0 {f(ry + ky)} 0 {f(ry)} "
            f"b 0 {f(ry - ky)} {f(rx - kx)} 0 {f(rx)} 0")


class _Events:
    def __init__(self):
        self.rows: List[Tuple[int, int, int, str]] = []

    def add(self, layer: int, t0: float, t1: float, text: str) -> None:
        a, b = _cs(t0), _cs(t1)
        if b > a:
            self.rows.append((a, layer, b, text))

    def dump(self) -> str:
        return "".join(f"Dialogue: {ly},{_ts(a)},{_ts(b)},Default,,0,0,0,,{tx}\n"
                       for a, ly, b, tx in sorted(self.rows, key=lambda r: (r[0], r[1])))


# ============================================================================ chunk & kata kunci

_STOP = set("""yang dan di ke dari ini itu aja aku kamu dia kita kami mereka gue gua lu lo loh sih dong deh kok ya
nya pun lah kan tuh nih mah yg ada adalah akan atau juga sama buat untuk dengan dalam pada jadi kalau kalo karena
udah sudah belum lagi bisa mau masih tapi terus trus gitu gini gak nggak ga enggak kayak kaya biar the a an to of
and in is it you i we that this for on are was be with so just like oh uh um eh hmm""".split())


def _clean(w: str) -> str:
    return re.sub(r"[^\w]", "", str(w).lower(), flags=re.UNICODE)


def _case(tok: str, case: str) -> str:
    if case == "upper":
        return tok.upper()
    if case == "lower":
        return tok.lower()
    if case == "title":
        return tok[:1].upper() + tok[1:]
    return tok


def _chunks(segments: List[Dict], n_max: int, hold: float) -> List[Dict]:
    out: List[Dict] = []
    for seg in segments:
        s0, s1 = float(seg.get("start", 0)), float(seg.get("end", 0))
        ws = [dict(w) for w in (seg.get("words") or []) if str(w.get("word", "")).strip()]
        if not ws:
            toks = str(seg.get("text", "")).split()
            if not toks or s1 <= s0:
                continue
            step = (s1 - s0) / len(toks)
            ws = [{"word": t, "start": s0 + i * step, "end": s0 + (i + 1) * step} for i, t in enumerate(toks)]
        groups = max(1, math.ceil(len(ws) / max(1, n_max)))
        size = math.ceil(len(ws) / groups)
        for gi in range(groups):
            g = ws[gi * size:(gi + 1) * size]
            if not g:
                continue
            start = s0 if gi == 0 else float(g[0]["start"])
            end = max(float(g[-1]["end"]), s1 if gi == groups - 1 else float(g[-1]["end"]))
            out.append({"words": g, "start": min(start, float(g[0]["start"])), "end": end})
    out.sort(key=lambda c: c["start"])
    for i, ch in enumerate(out):
        nxt = out[i + 1]["start"] if i + 1 < len(out) else ch["end"] + hold
        ch["end"] = max(ch["start"] + 0.12, min(ch["end"] + hold, nxt))
    return out


def _keywords(toks: Sequence[str], words: Sequence[Dict], power: set, need: bool) -> List[int]:
    scored = []
    for i, (t, w) in enumerate(zip(toks, words)):
        c = _clean(t)
        if (len(c) < 3 and not c.isdigit()) or c in _STOP:
            continue
        s = 0.0
        if c in power:
            s += 4
        if int(w.get("stress") or 0) >= 2:
            s += 3
        if any(ch.isdigit() for ch in c):
            s += 3
        if len(c) >= 6:
            s += 1
        s += len(c) / 20.0
        scored.append((s, i))
    if not scored:
        return []
    scored.sort(reverse=True)
    if not need and scored[0][0] < 2:
        return []
    picks = [scored[0][1]]
    if len(toks) >= 5 and len(scored) > 1 and scored[1][0] >= 4:
        picks.append(scored[1][1])
    return picks


# ============================================================================ tata letak

def _layout(toks: List[str], sizes: List[float], fnt: _Font, p: Dict, W: int, bottom: float) -> Optional[List[Dict]]:
    n = len(toks)
    if p["stack"]:
        target = 0.74 * W
        sz = []
        for t, s in zip(toks, sizes):
            w = fnt.width(t, s)
            sz.append(max(0.8 * s, min(1.75 * s, s * target / max(w, 1.0))))
        sizes = sz
        lines = [[i] for i in range(n)]
    else:
        lines = None
        for _ in range(6):
            meas = [fnt.width(t, s) for t, s in zip(toks, sizes)]
            space = [fnt.width(" ", s) * p["gap"] for s in sizes]
            lines = _wrap([m * 1.1 for m in meas], space, p["max_w"] * W, p["lines"])
            if lines:
                break
            sizes = [s * 0.9 for s in sizes]
        if not lines:
            lines = [list(range(n))]
    meas = [fnt.width(t, s) for t, s in zip(toks, sizes)]
    space = [fnt.width(" ", s) * p["gap"] for s in sizes]
    # kata aktif yang membesar butuh ruang agar tidak menempel ke tetangga
    grow = {"pop": 1.0, "burst": 1.0, "fill": 1.08, "jelly": 1.14, "focus": 1.06}.get(str(p["active"]), 0)
    grow = (int(p["act_scale"]) / 100.0 if grow == 1.0 else grow) or 1.0
    slot = [m * grow for m in meas]
    out: List[Optional[Dict]] = [None] * n
    # baseline baris terakhir: bawah kotak baris (descender) di `bottom` (sama dengan MarginV gaya lama)
    line_em = [max(sizes[i] for i in ln) for ln in lines]
    base = bottom - fnt.desc_em * line_em[-1]
    baselines = [0.0] * len(lines)
    for li in range(len(lines) - 1, -1, -1):
        baselines[li] = base
        if li > 0:
            if p["stack"]:  # tumpukan rapat: puncak huruf baris bawah + celah kecil = baseline baris atas
                base -= fnt.cap * line_em[li] + 0.2 * min(line_em[li], line_em[li - 1]) * p["lh"]
            else:
                base -= max(line_em[li], line_em[li - 1]) * p["lh"]
    for li, ln in enumerate(lines):
        lw = sum(slot[i] for i in ln) + sum(space[i] for i in ln[:-1])
        x = W / 2 - lw / 2
        for i in ln:
            out[i] = {"i": i, "tok": toks[i], "em": sizes[i], "w": meas[i], "x": x + slot[i] / 2,
                      "base": baselines[li], "line": li, "y": baselines[li] - fnt.center_off(sizes[i]),
                      "cy": baselines[li] - fnt.cap * sizes[i] / 2}
            x += slot[i] + space[i]
    return out  # type: ignore[return-value]


def _wrap(meas: List[float], space: List[float], max_w: float, max_lines: int) -> Optional[List[List[int]]]:
    n = len(meas)

    def width(a: int, b: int) -> float:
        return sum(meas[a:b]) + sum(space[a:b - 1])

    if width(0, n) <= max_w:
        return [list(range(n))]
    if max_lines >= 2 and n >= 2:
        best = None
        for k in range(1, n):
            m = max(width(0, k), width(k, n))
            if m <= max_w and (best is None or m < best[0]):
                best = (m, k)
        if best:
            return [list(range(best[1])), list(range(best[1], n))]
    if max_lines >= 3 and n >= 3:
        lines, cur = [], []
        for i in range(n):
            if cur and width(cur[0], i + 1) > max_w:
                lines.append(cur)
                cur = []
            cur.append(i)
        lines.append(cur)
        if len(lines) <= max_lines and all(width(ln[0], ln[-1] + 1) <= max_w for ln in lines):
            return lines
    return None


# ============================================================================ animasi

def _enter(kind: Optional[str], wd: Dict, k: float, clip: Optional[Tuple] = None) -> Tuple[Optional[str], str, int]:
    """(tag posisi pengganti, tag animasi, fade-in ms) untuk event pertama sebuah kata."""
    x, y = wd["x"], wd["y"]
    if kind == "pop":
        return None, r"\fscx70\fscy70\t(0,110,\fscx108\fscy108)\t(110,180,\fscx100\fscy100)", 60
    if kind == "bounce":
        return (f"\\move({x:.1f},{y - 46 * k:.1f},{x:.1f},{y:.1f},0,150)",
                r"\fscx86\fscy86\t(0,150,\fscx100\fscy112)\t(150,230,\fscx100\fscy94)\t(230,300,\fscx100\fscy100)", 50)
    if kind == "slam":
        return None, r"\fscx230\fscy230\alpha&HFF&\t(0,110,0.6,\fscx94\fscy94\alpha&H00&)\t(110,170,\fscx100\fscy100)", 0
    if kind == "flip":
        return None, r"\frx90\t(0,220,\frx0)", 80
    if kind == "rise":
        return f"\\move({x:.1f},{y + 38 * k:.1f},{x:.1f},{y:.1f},0,150)", "", 110
    if kind == "slide":
        return f"\\move({x - 70 * k:.1f},{y:.1f},{x:.1f},{y:.1f},0,190)", "", 130
    if kind == "roll":
        c = clip
        tag = f"\\clip({c[0]:.0f},{c[1]:.0f},{c[2]:.0f},{c[3]:.0f})" if c else ""
        return f"\\move({x:.1f},{y + wd['em'] * 0.9:.1f},{x:.1f},{y:.1f},0,170)", tag, 0
    if kind == "flicker":
        return None, (r"\alpha&HFF&\t(0,30,\alpha&H00&)\t(30,60,\alpha&HD0&)\t(60,90,\alpha&H00&)"
                      r"\t(90,130,\alpha&HA0&)\t(130,170,\alpha&H00&)"), 0
    if kind == "fade":
        return None, "", 130
    return None, "", 0


def _active(kind: Optional[str], p: Dict) -> str:
    s = int(p["act_scale"])
    if kind in ("pop", "burst"):
        return f"\\fscx100\\fscy100\\t(0,80,\\fscx{s + 7}\\fscy{s + 7})\\t(80,170,\\fscx{s}\\fscy{s})"
    if kind == "jelly":
        return r"\t(0,70,\fscx124\fscy82)\t(70,150,\fscx88\fscy116)\t(150,230,\fscx108\fscy95)\t(230,310,\fscx104\fscy104)"
    if kind == "focus":
        return r"\fscx100\fscy100\t(0,90,\fscx106\fscy106)"
    if kind == "fill":
        return r"\fscx100\fscy100\t(0,80,\fscx114\fscy114)\t(80,160,\fscx108\fscy108)"
    return ""


# ============================================================================ penulis ASS

def write_ass(segments: List[Dict], style: Dict, out_path: str, W: int, H: int) -> Dict:
    pid = str(style.get("preset") or "pop")
    p = resolve(pid)
    k = W / REF_W
    fnt = font(p["font"])
    em0 = float(style.get("font_size", 76)) / 1.12 * p["size_k"] * k
    em0 *= visual_scale(fnt)  # (v7) ukuran tampak sama di semua font (klipers: Street/Outline/Slide tidak lagi kecil)
    margin = float(style.get("vertical_position", 420)) * H / 1920.0
    zones = style.get("margin_zones") or []
    chunks = _chunks(segments, int(p["words"]), float(p["hold"]))
    power = {str(w).lower() for w in (style.get("power_words") or [])}
    if p["keyword"] or not power:
        try:
            from .editing import power_word_set
            allw = [{"word": w["word"], "stress": w.get("stress")} for c in chunks for w in c["words"]]
            power |= power_word_set(allw) if allw else set()
        except Exception:
            pass
    ev = _Events()
    acts = set(str(p["active"] or "").split("+")) - {""}
    rng = random.Random(len(chunks) * 7919 + len(pid))
    act_default = p["act_color"]
    for ci, ch in enumerate(chunks):
        c0, c1 = float(ch["start"]), float(ch["end"])
        mv = next((float(z[2]) for z in zones if float(z[0]) - 0.05 <= c0 < float(z[1])), None)
        bottom = H - (mv * H / 1920.0 if mv is not None else margin)
        toks = [_case(_esc(str(w["word"]).strip()), p["case"]) for w in ch["words"]]
        kws = _keywords(toks, ch["words"], power, need=p["keyword"] in ("marker", "circle", "big")) if p["keyword"] else []
        sizes = [em0 * (1.38 if (p["keyword"] == "big" and i in kws) else 1.0) for i in range(len(toks))]
        lay = _layout(toks, sizes, fnt, p, W, bottom)
        if not lay:
            continue
        n = len(lay)
        # rentang aktif tiap kata: dari mulai diucapkan s/d kata berikutnya mulai
        starts = [c0]
        for w in ch["words"][1:]:
            starts.append(max(starts[-1], min(float(w["start"]), c1 - 0.04)))
        spans = [(starts[i], starts[i + 1] if i + 1 < n else c1) for i in range(n)]
        bx0 = min(wd["x"] - wd["w"] / 2 for wd in lay)
        bx1 = max(wd["x"] + wd["w"] / 2 for wd in lay)
        by0 = min(wd["base"] - fnt.cap * wd["em"] for wd in lay)
        by1 = max(wd["base"] for wd in lay) + (fnt.desc_em * 0.55 * em0 if p["case"] != "upper" else 0)
        org = f"\\org({(bx0 + bx1) / 2:.1f},{(by0 + by1) / 2:.1f})"
        tilt = (p["tilt"] if ci % 2 == 0 else -p["tilt"] * 0.6) if p["tilt"] else 0.0
        rot = f"{org}\\frz{tilt:.1f}" if tilt else ""
        # ---------------------------------------------------------------- latar
        if p["bg"] in ("bubble", "glass"):
            px, py = 0.5 * em0, 0.36 * em0
            w, h = bx1 - bx0 + 2 * px, by1 - by0 + 2 * py
            cx, cy = (bx0 + bx1) / 2, (by0 + by1) / 2
            if p["bg"] == "bubble":
                look = f"\\c{_col(p.get('bubble_color') or 'FFFFFF')}\\bord0\\shad{3 * k:.1f}\\4c{_col('000000')}\\4a&HA0&"
                pop =r"\fscx82\fscy82\t(0,120,\fscx102\fscy102)\t(120,170,\fscx100\fscy100)"
                ev.add(0, c0, c1, f"{{\\an5\\pos({cx:.1f},{cy:.1f}){look}{pop}{rot}\\p1}}{_rrect(w, h, 0.42 * em0)}")
                tw = 0.42 * em0
                tail = f"m 0 0 l {tw:.1f} 0 l {tw * 0.15:.1f} {tw * 0.8:.1f}"
                ev.add(0, c0, c1, f"{{\\an7\\pos({cx - w / 2 + 0.55 * em0:.1f},{cy + h / 2 - 2:.1f}){look}{rot}"
                                  f"\\fad(120,0)\\p1}}{tail}")
            else:
                look = (f"\\c{_col('FFFFFF')}\\1a&HC4&\\bord{2.2 * k:.1f}\\3c{_col('FFFFFF')}\\3a&H60&"
                        f"\\shad0\\blur0.8")
                ev.add(0, c0, c1, f"{{\\an5\\pos({cx:.1f},{cy:.1f}){look}\\fad(120,0)\\p1}}{_rrect(w, h, 0.5 * em0)}")
        if p["bg"] == "line_box":
            for li in sorted({wd["line"] for wd in lay}):
                ln = [wd for wd in lay if wd["line"] == li]
                x0 = min(wd["x"] - wd["w"] / 2 for wd in ln) - 0.3 * em0
                x1 = max(wd["x"] + wd["w"] / 2 for wd in ln) + 0.3 * em0
                y0 = min(wd["base"] - fnt.cap * wd["em"] for wd in ln) - 0.24 * em0
                y1 = max(wd["base"] for wd in ln) + 0.24 * em0
                ev.add(0, c0, c1, f"{{\\an7\\pos({x0:.1f},{y0:.1f})\\c{_col('000000')}\\1a&H38&\\bord0\\shad0"
                                  f"\\fad(80,0)\\p1}}{_rrect(x1 - x0, y1 - y0, 0.2 * em0)}")
        # ---------------------------------------------------------------- kata
        for i, wd in enumerate(lay):
            a0, a1 = spans[i]
            appear = c0 if p["reveal"] == "chunk" else a0
            if p["reveal"] == "chunk" and p["stagger"]:
                appear = min(a0, c0 + i * p["stagger"])
            if p["palette"]:
                base_col = p["palette"][(ci * 3 + i) % len(p["palette"])]
            elif p["gradient"]:
                base_col = _mix(p["gradient"][0], p["gradient"][1], i / max(1, n - 1))
            elif p["alt"]:
                base_col = p["alt"][(ci + i) % len(p["alt"])]
            else:
                base_col = p["color"]
            act_col = act_default or base_col
            if "burst" in acts:
                act_col = ("FF3B6B", "FFD400", "3BE8FF", "7CFF5B", "FF8A00", "C77DFF")[(ci * 2 + i) % 6]
            is_kw = i in kws
            wave_dy = math.sin(i * 1.15 + ci * 0.7) * p["wave"] * k if p["wave"] else 0.0
            wtilt = (rng.uniform(-1, 1) * p["word_tilt"]) if p["word_tilt"] else 0.0
            pieces = []
            if appear < a0:
                pieces.append(("pre", appear, a0))
            pieces.append(("act", a0, a1))
            if a1 < c1:
                pieces.append(("post", a1, c1))
            # entrance dipasang pada potongan pertama
            line_clip = None
            if p["enter"] == "roll":
                line_clip = (0, wd["base"] - fnt.cap * wd["em"] - 0.12 * wd["em"], W,
                             wd["base"] + fnt.desc_em * wd["em"] * 0.9)
            first = True
            for state, t0, t1 in pieces:
                if t1 - t0 < 0.01:
                    continue
                y = wd["y"] + wave_dy
                x = wd["x"]
                tags = _look(p, fnt, wd, state, base_col, act_col, acts, is_kw, k)
                pos = f"\\pos({x:.1f},{y:.1f})"
                anim = ""
                fin = 0
                if first and p["enter"]:
                    mv_tag, anim, fin = _enter(p["enter"], {**wd, "y": y}, k, line_clip)
                    if mv_tag:
                        pos = mv_tag
                elif state == "act":
                    anim = _active(p["active"] if p["active"] in ("pop", "jelly", "focus", "fill", "burst")
                                   else None, p)
                if state == "act" and "lift" in acts and pos.startswith("\\pos"):
                    pos = f"\\move({x:.1f},{y:.1f},{x:.1f},{y - 15 * k:.1f},0,110)"
                if state == "post" and "lift" in acts:
                    pos = f"\\move({x:.1f},{y - 15 * k:.1f},{x:.1f},{y:.1f},0,140)"
                fo = p["fade_out"] if abs(t1 - c1) < 0.005 else 0
                fad = f"\\fad({fin},{fo})" if (fin or fo) else ""
                wrot = rot or (f"\\frz{wtilt:.1f}" if wtilt else "")
                head = f"\\an5{pos}{tags}{wrot}{fad}{anim}"
                body = wd["tok"]
                if state == "act" and acts & {"shake", "glitch"}:
                    _shake_glitch(ev, p, acts, head, body, x, y, t0, t1, k, rng, tags, wrot, fad, anim, act_col)
                else:
                    ev.add(3, t0, t1, f"{{{head}}}{body}")
                    _extra_layers(ev, p, head, body, t0, t1, state, k)
                first = False
            # ------------------------------------------------------------ latar per kata
            cy = wd["cy"] + wave_dy
            if "pill" in acts:
                ph = fnt.cap * wd["em"] + 0.46 * wd["em"]
                pw = wd["w"] + 0.5 * wd["em"]
                prev = lay[i - 1] if i > 0 else None
                look = f"\\c{_col(p['act_color'])}\\bord0\\shad0"
                if prev and prev["line"] == wd["line"] and abs(spans[i - 1][1] - a0) < 0.005:
                    ratio = (prev["w"] + 0.5 * prev["em"]) / pw * 100
                    mov = f"\\move({prev['x']:.1f},{cy:.1f},{wd['x']:.1f},{cy:.1f},0,110)"
                    sc = f"\\fscx{ratio:.0f}\\t(0,110,\\fscx100)"
                else:
                    mov = f"\\pos({wd['x']:.1f},{cy:.1f})"
                    sc = r"\fscx60\fscy60\t(0,100,\fscx100\fscy100)"
                ev.add(0, a0, a1, f"{{\\an5{mov}{look}{sc}{rot}\\p1}}{_rrect(pw, ph, ph * float(p.get('pill_r', 0.5)))}")
            if is_kw and p["keyword"] == "marker":
                mh = fnt.cap * wd["em"] * 1.12
                mw = wd["w"] + 0.34 * wd["em"]
                ev.add(1, a0, c1, f"{{\\an4\\pos({wd['x'] - mw / 2:.1f},{cy + 0.04 * wd['em']:.1f})"
                                  f"\\c{_col(p['kw_color'])}\\bord0\\shad0\\frz-2\\fscx0\\t(0,170,\\fscx100)"
                                  f"{rot}\\p1}}{_rrect(mw, mh, 0.12 * wd['em'])}")
            if is_kw and p["keyword"] == "circle":
                ew, eh = wd["w"] * 1.16 + 0.55 * wd["em"], fnt.cap * wd["em"] * 2.15
                ev.add(4, a0, c1, f"{{\\an5\\pos({wd['x']:.1f},{cy:.1f})\\1a&HFF&\\bord{6 * k:.1f}"
                                  f"\\3c{_col(p['kw_color'])}\\shad0\\frz-4\\fscx40\\fscy40"
                                  f"\\t(0,150,\\fscx104\\fscy104)\\t(150,210,\\fscx100\\fscy100){rot}\\p1}}"
                                  f"{_ellipse(ew, eh)}")
    header = (
        "[Script Info]\nTitle: Viral Auto Klip Captions (preset)\nScriptType: v4.00+\nWrapStyle: 2\n"
        f"ScaledBorderAndShadow: yes\nYCbCr Matrix: None\nPlayResX: {W}\nPlayResY: {H}\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{fnt.family},{fnt.fs(em0):.1f},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,"
        f"{-1 if fnt.bold else 0},0,0,0,100,100,0,0,1,{p['bord'] * k:.1f},0,5,10,10,10,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    Path(out_path).write_text(header + ev.dump(), encoding="utf-8")
    return {"preset": pid, "chunks": len(chunks), "events": len(ev.rows), "font": fnt.family}


def _look(p: Dict, fnt: _Font, wd: Dict, state: str, base_col: str, act_col: str, acts: set, is_kw: bool,
          k: float) -> str:
    col, oc = base_col, p["outline"]
    bord, shad = p["bord"] * k, p["shad"] * k
    extra = ""
    lit = state == "act" and acts & {"color", "pop", "lift", "jelly", "shake", "glitch", "glowup", "burst", "focus", "fill"}
    if lit or (state == "post" and p["sticky"]):
        col = act_col
    if state != "act" and p["dim"]:
        extra += f"\\1a{_alpha(p['dim'])}\\3a{_alpha(p['dim'])}"
    if state != "act" and p["blur_others"]:
        extra += f"\\blur{p['blur_others'] * k:.1f}"
    if p["hollow"]:
        extra += "\\1a&H00&" if state == "act" else "\\1a&HFF&"
    if state == "act" and "pill" in acts:
        col, bord = p["act_text"], 0.0
    if is_kw and state != "pre":
        if p["keyword"] == "marker":
            col, bord, shad = "161616", 0.0, 0.0
        elif p["keyword"] in ("circle", "big") and p["kw_color"]:
            col = p["kw_color"] if not lit else act_col
    if is_kw and p["keyword"] == "big" and state == "pre":
        col = p["kw_color"] or col
    if bord and not p["hollow"] and str(col).upper() == str(oc).upper():  # teks = garis (glitch) → huruf tertelan
        oc = "120012"
    shadow = f"\\4c{_col(p['shadow'])}\\4a{_alpha(p['shadow_a'])}" if shad else ""
    if p["font"] in ("semi",) and not bord and shad:
        extra += f"\\blur{1.2 * k:.1f}"
    return (f"\\fn{fnt.family}\\b{fnt.bold}\\fs{fnt.fs(wd['em']):.1f}\\c{_col(col)}\\3c{_col(oc)}"
            f"\\bord{bord:.1f}\\shad{shad:.1f}{shadow}{extra}")


def _extra_layers(ev: _Events, p: Dict, head: str, body: str, t0: float, t1: float, state: str, k: float) -> None:
    """Lapisan di bawah teks: glow (neon) dan tepi stiker putih."""
    if p["glow"]:
        strong = state == "act" and "glowup" in str(p["active"] or "")
        b = (p["bord"] + (13 if strong else 8)) * k
        a = "&H10&" if strong else "&H50&"
        pulse = ""
        if p["pulse"]:
            steps, dur = [], int((t1 - t0) * 1000)
            for j, ms in enumerate(range(0, min(dur, 6000), 420)):
                steps.append(f"\\t({ms},{ms + 420},\\3a{'&H20&' if j % 2 == 0 else '&H90&'})")
            pulse = "".join(steps)
        ev.add(1, t0, t1, f"{{{head}\\1a&HFF&\\3c{_col(p['glow'])}\\3a{a}\\bord{b:.1f}\\blur{9 * k:.1f}\\shad0{pulse}}}{body}")
    if p["sticker"]:
        b = (p["bord"] + 11) * k
        ev.add(1, t0, t1, f"{{{head}\\c{_col(p['sticker'])}\\3c{_col(p['sticker'])}\\bord{b:.1f}"
                          f"\\shad{5 * k:.1f}\\4c{_col('000000')}\\4a&H70&}}{body}")


def _shake_glitch(ev: _Events, p: Dict, acts: set, head: str, body: str, x: float, y: float, t0: float, t1: float,
                  k: float, rng: random.Random, tags: str, wrot: str, fad: str, anim: str, act_col: str) -> None:
    """Kata aktif dipecah per 45 ms: posisi acak (getar) dan/atau salinan RGB bergeser (glitch)."""
    step = 0.045
    t = t0
    j = 0
    end_fx = min(t1, t0 + 0.42)
    while t < end_fx - 0.005:
        t2 = min(end_fx, t + step)
        dx = rng.uniform(-7, 7) * k if "shake" in acts else 0.0
        dy = rng.uniform(-5, 5) * k if "shake" in acts else 0.0
        h = f"\\an5\\pos({x + dx:.1f},{y + dy:.1f}){tags}{wrot}{fad if j == 0 else ''}{anim if j == 0 else ''}"
        ev.add(3, t, t2, f"{{{h}}}{body}")
        if "glitch" in acts:
            g = rng.uniform(7, 16) * k * (1 if j % 2 == 0 else -1)
            for ci, col in enumerate(p["glitch"]):
                off = g if ci == 0 else -g
                ev.add(2, t, t2, f"{{\\an5\\pos({x + dx + off:.1f},{y + dy:.1f}){tags}{wrot}\\c{_col(col)}"
                                 f"\\1a&H00&\\bord0\\shad0}}{body}")
        t = t2
        j += 1
    if t < t1 - 0.005:
        h = f"\\an5\\pos({x:.1f},{y:.1f}){tags}{wrot}"
        ev.add(3, t, t1, f"{{{h}}}{body}")
        if "glitch" in acts:
            for ci, col in enumerate(p["glitch"]):
                off = 4.0 * k if ci == 0 else -4.0 * k
                ev.add(2, t, t1, f"{{\\an5\\pos({x + off:.1f},{y:.1f}){tags}{wrot}\\c{_col(col)}\\1a&H30&"
                                 f"\\bord0\\shad0}}{body}")


# ============================================================================ pratinjau

SAMPLE = "Ternyata rahasia konten viral cuma 3 langkah ini"


def preview_png(pid: str, out_path: str, width: int = 540, t: Optional[float] = None,
                text: str = SAMPLE) -> str:
    """PNG contoh preset (render libass sungguhan via ffmpeg) — area caption saja."""
    from . import ffx  # noqa: F401  ffmpeg lokal ke PATH
    W, H = width, int(width * 16 / 9) // 2 * 2
    toks = text.split()
    words, cur = [], 0.0
    for tok in toks:
        d = 0.32 + 0.03 * len(tok)
        words.append({"word": tok, "start": round(cur, 2), "end": round(cur + d - 0.04, 2)})
        cur += d
    segs = []
    n = PRESETS.get(pid, {}).get("words", _DEF["words"])
    for g in range(0, len(words), 5):
        part = words[g:g + 5]
        segs.append({"start": part[0]["start"], "end": part[-1]["end"], "text": " ".join(w["word"] for w in part),
                     "words": part})
    style = dict(config.CAPTION_STYLES.get(pid) or {"preset": pid, "font_size": 76, "vertical_position": 420})
    out = Path(out_path)
    ass = out.with_suffix(".ass")
    if style.get("preset"):
        write_ass(segs, style, str(ass), W, H)
    else:  # gaya lama memakai px absolut kanvas 1080 — skalakan ke ukuran pratinjau
        from .utils import generate_ass_subtitles
        sc = W / REF_W
        for key in ("font_size", "vertical_position", "stroke_width"):
            if key in style:
                style[key] = max(1, int(round(float(style[key]) * sc)))
        generate_ass_subtitles(segs, style, str(ass), W, H)
    if t is None:  # kata ke-3 (atau ke-2 untuk preset 2 kata) sedang aktif, animasinya sudah jalan
        idx = 2 if n >= 3 else 1
        t = words[idx]["start"] + 0.2
    esc = str(ass).replace("\\", "/").replace(":", "\\:")
    bg = (f"gradients=s={W}x{H}:c0=0x2b3a55:c1=0x6b4a3a:x0=0:y0=0:x1={W}:y1={H}:d=4,"
          if _has_gradients() else f"color=c=0x3a4458:s={W}x{H}:d=4,")
    crop_y = int(H * 0.48)
    fonts = Path(config.FONT_DIR)
    fd = (":fontsdir='" + str(fonts).replace("\\", "/").replace(":", "\\:") + "'") if any(fonts.glob("*.[ot]tf")) else ""
    vf = f"{bg}format=yuv420p,subtitles='{esc}'{fd},crop={W}:{int(H * 0.36)}:0:{crop_y}"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", vf.split(",", 1)[0],
                    "-vf", vf.split(",", 1)[1], "-ss", f"{t:.2f}", "-frames:v", "1", str(out)], check=True)
    try:
        ass.unlink()
    except OSError:
        pass
    return str(out)


@lru_cache(maxsize=1)
def _has_gradients() -> bool:
    try:
        r = subprocess.run(["ffmpeg", "-hide_banner", "-h", "filter=gradients"], capture_output=True, text=True, timeout=10)
        return "gradients" in r.stdout and "Unknown" not in r.stdout
    except Exception:
        return False


# di akhir modul: config mengimpor style_entries dari sini — urutan impor apa pun tetap aman
import config  # noqa: E402
