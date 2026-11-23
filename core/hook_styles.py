"""Hook overlay bergaya (v7) — teks hook di detik-detik pertama klip dengan gaya visual per klip.

Dulu hook selalu satu kartu `drawtext` kotak gelap. Sekarang tiap klip mendapat satu dari 19 gaya (Breaking Bar, Stabilo,
Stiker Pop, Notifikasi HP, Chat Bubble, Blok Bertumpuk, Coret & Ganti, Mesin Ketik, Editorial, Balas Komentar, Polling,
Stempel Fakta, Postingan X, Angka Besar, Pencarian, Duel VS, Pencapaian, Checklist, Quote) — dipilih AI sesuai isi klip,
atau heuristik bila AI tidak tersedia, dan dijaga bervariasi antar-klip.

Markup teks hook:
- ``*kata*``  = kata yang ditonjolkan (warna/stabilo/garis bawah sesuai gaya)
- ``A | B``   = bagian terpisah (Coret & Ganti: yang dicoret | penggantinya, Angka Besar: angka | keterangan,
                Polling: pertanyaan | pilihan A | pilihan B, Duel VS: A | B, Checklist: judul | butir…, Stempel: kalimat | vonis)

Render: setiap frame digambar dengan Pillow (RGBA, selebar video) → urutan PNG → satu input ffmpeg yang ditimpa
di encode utama (`editing.render_polish(hook_seq=…)`), jadi tidak ada generasi encode tambahan.
"""
from __future__ import annotations

import hashlib
import math
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from loguru import logger
from PIL import Image, ImageDraw, ImageFilter, ImageFont

import config

FPS = 30
ENTER = 0.35   # detik animasi masuk
EXIT = 0.28    # detik animasi keluar

# id: label UI, kapan cocok (dipakai di prompt AI), sfx pembuka, jumlah bagian minimal, cara masuk
STYLES: Dict[str, Dict] = {
    "breaking":    {"label": "Breaking Bar", "desc": "kejadian mengejutkan / klaim tegas seperti berita", "sfx": "impact", "parts": 1},
    "stabilo":     {"label": "Stabilo", "desc": "pernyataan inti, 1-3 kata kunci distabilo", "sfx": "whoosh", "parts": 1},
    "sticker":     {"label": "Stiker Pop", "desc": "momen lucu / heboh / reaksi spontan", "sfx": "pop", "parts": 1},
    "notif":       {"label": "Notifikasi HP", "desc": "peringatan atau info penting gaya pesan masuk", "sfx": "ding", "parts": 1},
    "chat":        {"label": "Chat Bubble", "desc": "POV / pertanyaan relate seperti pesan teman", "sfx": "ding", "parts": 1},
    "stack":       {"label": "Blok Bertumpuk", "desc": "klaim tegas, bisnis, kalimat pendek bertenaga", "sfx": "whoosh", "parts": 1},
    "strike":      {"label": "Coret & Ganti", "desc": "kontras: yang salah | yang benar (2 bagian)", "sfx": "whoosh", "parts": 2},
    "typewriter":  {"label": "Mesin Ketik", "desc": "fakta / rahasia yang diungkap pelan", "sfx": "type", "parts": 1},
    "editorial":   {"label": "Editorial", "desc": "cerita / opini, gaya majalah huruf kecil", "sfx": "whoosh", "parts": 1},
    "reply":       {"label": "Balas Komentar", "desc": "menjawab pertanyaan penonton", "sfx": "ding", "parts": 1},
    "poll":        {"label": "Polling", "desc": "dua pilihan memancing debat: pertanyaan | pilihan A | pilihan B", "sfx": "pop", "parts": 3},
    "stamp":       {"label": "Stempel Fakta", "desc": "mitos atau fakta dengan stempel vonis: kalimat | FAKTA/MITOS", "sfx": "impact", "parts": 2},
    "tweet":       {"label": "Postingan X", "desc": "hot take / opini pedas", "sfx": "pop", "parts": 1},
    "bigstat":     {"label": "Angka Besar", "desc": "angka mengejutkan yang DIUCAPKAN di klip: angka | keterangan", "sfx": "impact", "parts": 2},
    "search":      {"label": "Pencarian", "desc": "hal yang orang penasaran / cari tahu", "sfx": "type", "parts": 1},
    "versus":      {"label": "Duel VS", "desc": "membandingkan dua hal: A | B", "sfx": "impact", "parts": 2},
    "achievement": {"label": "Pencapaian", "desc": "momen sukses / menang / berhasil", "sfx": "ding", "parts": 1},
    "checklist":   {"label": "Checklist", "desc": "judul | butir 1 | butir 2 (| butir 3)", "sfx": "pop", "parts": 3},
    "quote":       {"label": "Quote", "desc": "kalimat bijak / motivasi", "sfx": "whoosh", "parts": 1},
}
STYLE_IDS = list(STYLES)

_EMOJI = re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0000FE0F\U0000200D\U00002B00-\U00002BFF]+")
_STOP = set("""yang dan atau untuk dengan dari pada itu ini aku kamu dia kita kami mereka tidak nggak gak bukan sudah udah belum
akan bisa juga saja aja lagi kalau kalo karena tapi jadi ya iya oke ada apa gimana kenapa sama lebih sangat banget dalam
sebuah oleh agar sih dong deh kok nih tuh lah mau harus punya jika maka saat gua gue lu lo guys the and you that this""".split())


# ====================================================================== teks & markup

def clean(text: str) -> str:
    return re.sub(r"\s+", " ", _EMOJI.sub("", str(text or ""))).strip()


def plain(text: str) -> str:
    """Teks tanpa markup (untuk cek kejujuran / log)."""
    return re.sub(r"\s+", " ", str(text or "").replace("*", " ").replace("|", " ")).strip()


def split_parts(text: str) -> List[str]:
    return [p.strip() for p in clean(text).split("|") if p.strip()]


def tokens(part: str) -> List[Tuple[str, bool]]:
    """'yang *cuma 1 dolar*' → [('yang',False),('cuma',True),('1',True),('dolar',True)]."""
    out: List[Tuple[str, bool]] = []
    for i, chunk in enumerate(str(part).split("*")):
        for w in chunk.split():
            out.append((w, i % 2 == 1))
    return out


def _auto_highlight(part: str) -> str:
    """Tanpa markup → tonjolkan angka, atau 1-2 kata isi terpanjang di separuh akhir kalimat."""
    if "*" in part:
        return part
    words = part.split()
    if len(words) <= 1:
        return part
    idx = [i for i, w in enumerate(words) if re.search(r"\d", w)]
    if idx:  # angka + satuannya ("1 dolar", "90% orang") + penguat di depannya ("cuma", "hampir")
        lo, hi = min(idx), max(idx)
        if hi + 1 < len(words) and re.fullmatch(r"[^\W\d_]+", words[hi + 1].strip(".,!?")) \
                and words[hi + 1].lower().strip(".,!?") not in _STOP:
            hi += 1
        if lo > 0 and words[lo - 1].lower() in ("cuma", "hanya", "sampai", "hampir", "lebih", "kurang", "total", "rp", "rp."):
            lo -= 1
        idx = [lo, hi]
    if not idx:
        cand = [(len(re.sub(r"\W", "", w)), i) for i, w in enumerate(words)
                if len(re.sub(r"\W", "", w)) >= 4 and w.lower().strip(".,!?") not in _STOP and i >= len(words) // 3]
        if not cand:
            return part
        best = max(cand)[1]
        idx = [best]
        if best + 1 < len(words) and words[best + 1].lower().strip(".,!?") not in _STOP and len(words) >= 5:
            idx.append(best + 1)
    lo, hi = min(idx), max(idx)
    return " ".join(words[:lo] + ["*" + " ".join(words[lo:hi + 1]) + "*"] + words[hi + 1:])


_NUM = re.compile(r"(?:rp\.?\s?)?\d[\d.,]*\s?(?:%|persen|juta|ribu|rb|jt|miliar|m|k|dolar|dollar|kali|x|detik|menit|jam|hari|tahun)?", re.I)


def normalize(style: str, text: str) -> Tuple[str, str]:
    """Lengkapi bagian yang dibutuhkan gaya. Gaya yang tidak bisa dipenuhi turun ke gaya satu-bagian. Return (style, text)."""
    style = style if style in STYLES else "stabilo"
    parts = split_parts(text)
    if not parts:
        return style, ""
    one = " ".join(p.replace("*", "") for p in parts)
    if style == "bigstat":
        if len(parts) < 2 or not re.search(r"\d", parts[0]):
            m = _NUM.search(one)
            if not m:
                return "stabilo", _auto_highlight(one)
            num = m.group(0).strip()
            rest = clean((one[:m.start()] + " " + one[m.end():]).strip(" ,.-"))
            parts = [num, rest or one]
        parts = [parts[0].replace("*", ""), " ".join(parts[1:])]
    elif style == "strike":
        if len(parts) < 2:
            return "stabilo", _auto_highlight(one)
        parts = [parts[0].replace("*", ""), parts[1].replace("*", "")]
    elif style == "versus":
        if len(parts) < 2:
            m = re.split(r"\s+(?:vs\.?|versus|atau)\s+", one, maxsplit=1, flags=re.I)
            if len(m) < 2:
                return "stabilo", _auto_highlight(one)
            parts = m
        parts = [p.replace("*", "") for p in parts[:3]]
    elif style == "poll":
        if len(parts) < 3:
            q = parts[0].replace("*", "")
            parts = [q if q.endswith("?") else q + "?", "Setuju", "Nggak"]
        parts = [p.replace("*", "") for p in parts[:3]]
    elif style == "checklist":
        if len(parts) < 3:
            return "stack", one
        parts = [p.replace("*", "") for p in parts[:5]]
    elif style == "stamp":
        verdict = (parts[1] if len(parts) > 1 else "FAKTA").replace("*", "").upper()
        if len(verdict) > 12:
            verdict = "FAKTA"
        parts = [_auto_highlight(parts[0]), verdict]
    else:
        parts = [_auto_highlight(" ".join(parts))]
    return style, " | ".join(parts)


# ====================================================================== pemilihan gaya

def heuristic_style(text: str, category: str = "", recent: Sequence[str] = ()) -> str:
    """Tanpa AI: tebak dari bentuk teks + kategori klip; hindari gaya yang baru dipakai."""
    t = plain(text).lower()
    parts = split_parts(text)
    cand: List[str] = []
    if len(parts) >= 3 and t.count("?"):
        cand.append("poll")
    if len(parts) == 2:
        cand += ["bigstat"] if re.search(r"\d", parts[0]) else ["strike"]
    if re.search(r"\s(vs\.?|versus)\s", t):
        cand.append("versus")
    if re.search(r"\d{2,}|\d+\s?(%|persen|juta|ribu|dolar)", t):
        cand.append("bigstat")
    if t.endswith("?"):
        cand += ["search", "chat", "reply"]
    if re.search(r"\b(mitos|fakta|hoax|bohong)\b", t):
        cand.append("stamp")
    if re.search(r"\b(berhasil|menang|akhirnya|juara|dapet|dapat|sukses|pertama)\b", t):
        cand.append("achievement")
    cat = str(category or "").lower()
    cand += {
        "humor": ["sticker", "chat", "tweet"], "comedy": ["sticker", "chat", "tweet"],
        "shocking": ["breaking", "notif", "stamp"], "emotional": ["editorial", "quote", "typewriter"],
        "motivation": ["quote", "stack", "editorial"], "education": ["typewriter", "stack", "stabilo"],
        "drama": ["breaking", "tweet", "editorial"], "gaming": ["sticker", "achievement", "breaking"],
    }.get(cat, [])
    cand += ["stabilo", "sticker", "chat", "notif", "stack", "breaking", "typewriter", "tweet", "editorial", "search"]
    recent_set = list(recent)[-3:]
    for c in cand:
        if c not in recent_set and normalize(c, text)[0] == c:
            return c
    return cand[0]


_AI_PROMPT = """Kamu editor video pendek Indonesia. Pilih GAYA VISUAL untuk teks hook yang tampil {dur:.0f} detik pertama klip,
lalu tulis ulang teksnya dengan markup gaya itu.

Hook saat ini: «{hook}»
Isi klip (ucapan asli): \"\"\"{text}\"\"\"
Kategori: {category}
Gaya yang baru dipakai klip lain (usahakan beda): {recent}

GAYA (id: kapan cocok):
{styles}

ATURAN:
- Maks 9 kata total. Bahasa Indonesia santai, sama makna dengan hook saat ini.
- JUJUR: hanya kata/kejadian/angka yang ada di isi klip. Angka harus persis diucapkan.
- Tandai 1-3 kata terpenting dengan *bintang*. Pisahkan bagian dengan " | " sesuai kebutuhan gaya.
- Gaya dua/tiga bagian (strike, bigstat, poll, versus, checklist, stamp) hanya bila isinya memang cocok.

Balas JSON: {{"style": "<id>", "text": "<teks dengan markup>"}}"""


def plan_ai(clip: Dict, recent: Sequence[str] = (), dur: float = 3.0) -> Optional[Dict]:
    from . import ai_providers as ai
    from .editor_review import numbers_supported, overlap_ratio
    hook = clean(clip.get("hook_text") or "")
    src = str(clip.get("transcript") or clip.get("text") or "")[:1400]
    if not hook or len(src.split()) < 3:
        return None
    try:
        if not ai.is_configured(config.AI_PROVIDER):
            return None
    except Exception:  # noqa: BLE001
        return None
    styles = "\n".join(f"- {k}: {v['desc']}" for k, v in STYLES.items())
    prompt = _AI_PROMPT.format(dur=dur, hook=hook, text=src, category=clip.get("category") or "-",
                               recent=", ".join(recent[-4:]) or "-", styles=styles)
    try:
        raw = ai.complete(prompt, max_tokens=600, json_mode=True)
        import json
        data = json.JSONDecoder().raw_decode(raw[raw.index("{"):])[0]
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"AI gaya hook gagal: {str(exc)[:120]}")
        return None
    style, text = normalize(str(data.get("style") or ""), str(data.get("text") or ""))
    flat = plain(text)
    reference = f"{src} {hook}"
    if not flat or len(flat.split()) > 14 or not numbers_supported(flat, reference) or overlap_ratio(flat, reference) < 0.6:
        logger.debug(f"AI gaya hook ditolak (tidak sesuai isi klip): {text!r}")
        return None
    return {"style": style, "text": text, "by": "ai"}


def plan(clip: Dict, recent: Sequence[str] = (), mode: Optional[str] = None, dur: Optional[float] = None) -> Optional[Dict]:
    """Rencana hook {style, text} untuk satu klip. None = pakai kartu hook lama (mode 'card' / tanpa hook)."""
    mode = str(mode or getattr(config, "HOOK_OVERLAY_STYLE", "auto") or "auto").lower()
    hook = clean(clip.get("hook_text") or "")
    if mode in ("card", "off", "none") or not hook:
        return None
    dur = float(dur or getattr(config, "HOOK_OVERLAY_DURATION", 3.0))
    if mode in STYLES:
        style, text = normalize(mode, hook)
        return {"style": style, "text": text, "by": "setting"}
    if getattr(config, "HOOK_STYLE_AI", True):
        got = plan_ai(clip, recent, dur)
        if got:
            return got
    style = heuristic_style(hook, clip.get("category") or "", recent)
    style, text = normalize(style, hook)
    return {"style": style, "text": text, "by": "heuristic"}


# ====================================================================== font & primitif gambar

_FONT_CANDS = {
    "black": ["Montserrat-Black.ttf", "Montserrat-ExtraBold.ttf", "C:/Windows/Fonts/ariblk.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/System/Library/Fonts/Supplemental/Arial Black.ttf"],
    "bold": ["Montserrat-ExtraBold.ttf", "Poppins-Bold.ttf", "C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf",
             "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"],
    "semi": ["Montserrat-SemiBold.ttf", "C:/Windows/Fonts/seguisb.ttf", "C:/Windows/Fonts/arial.ttf",
             "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf"],
    "mono": ["C:/Windows/Fonts/consolab.ttf", "C:/Windows/Fonts/courbd.ttf",
             "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", "/System/Library/Fonts/Menlo.ttc"],
    "serif": ["C:/Windows/Fonts/georgiab.ttf", "C:/Windows/Fonts/timesbd.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf", "/System/Library/Fonts/Supplemental/Georgia Bold.ttf"],
    "serif_i": ["C:/Windows/Fonts/georgiaz.ttf", "C:/Windows/Fonts/timesbi.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSerif-BoldItalic.ttf", "/System/Library/Fonts/Supplemental/Georgia Bold Italic.ttf"],
}
_FONT_PATH: Dict[str, Optional[str]] = {}
_FONT_OBJ: Dict[Tuple[str, int], ImageFont.FreeTypeFont] = {}


def font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    size = max(8, int(size))
    key = (kind, size)
    if key in _FONT_OBJ:
        return _FONT_OBJ[key]
    if kind not in _FONT_PATH:
        _FONT_PATH[kind] = None
        for cand in _FONT_CANDS.get(kind, _FONT_CANDS["bold"]):
            p = Path(cand) if Path(cand).is_absolute() or ":" in cand else Path(config.FONT_DIR) / cand
            if p.exists():
                _FONT_PATH[kind] = str(p)
                break
    path = _FONT_PATH[kind] or (_FONT_PATH.get("bold") if kind != "bold" else None)
    try:
        f = ImageFont.truetype(path, size) if path else ImageFont.load_default(size)
    except Exception:  # noqa: BLE001
        f = ImageFont.load_default(size)
    _FONT_OBJ[key] = f
    return f


def _rgba(c, a: Optional[int] = None) -> Tuple[int, int, int, int]:
    if isinstance(c, str):
        c = c.lstrip("#")
        r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
        al = int(c[6:8], 16) if len(c) >= 8 else 255
    else:
        r, g, b = c[:3]
        al = c[3] if len(c) > 3 else 255
    return (r, g, b, al if a is None else a)


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def prog(t: float, a: float, b: float) -> float:
    return clamp((t - a) / max(1e-6, b - a))


def ease_out(x: float) -> float:
    return 1 - (1 - clamp(x)) ** 3


def ease_back(x: float, s: float = 1.9) -> float:
    x = clamp(x) - 1
    return 1 + (s + 1) * x ** 3 + s * x ** 2


def _wrap(toks: List[Tuple[str, bool]], f: ImageFont.FreeTypeFont, max_w: float) -> List[List[Tuple[str, bool]]]:
    lines: List[List[Tuple[str, bool]]] = [[]]
    cur, sp = 0.0, f.getlength(" ")
    for w, hl in toks:
        ww = f.getlength(w)
        if lines[-1] and cur + sp + ww > max_w:
            lines.append([])
            cur = 0.0
        cur += (sp if lines[-1] else 0) + ww
        lines[-1].append((w, hl))
    return [ln for ln in lines if ln]


def fit(toks: List[Tuple[str, bool]], kind: str, max_w: float, max_lines: int, size: int, min_size: int):
    """Ukuran font terbesar ≤ size sehingga teks muat ≤ max_lines baris dan tiap kata ≤ max_w."""
    s = size
    while True:
        f = font(kind, s)
        lines = _wrap(toks, f, max_w)
        if (len(lines) <= max_lines and all(f.getlength(w) <= max_w for w, _ in toks)) or s <= min_size:
            return f, lines
        s = int(s * 0.92)


def line_w(line: List[Tuple[str, bool]], f) -> float:
    return f.getlength(" ".join(w for w, _ in line))


def upper(toks: List[Tuple[str, bool]]) -> List[Tuple[str, bool]]:
    return [(w.upper(), h) for w, h in toks]


def lower(toks: List[Tuple[str, bool]]) -> List[Tuple[str, bool]]:
    return [(w.lower(), h) for w, h in toks]


def cap_h(f) -> float:
    """Tinggi huruf kapital di atas baseline (tata letak berpatokan baseline → padding atas/bawah simetris)."""
    return float(-f.getbbox("H", anchor="ls")[1])


def draw_lines(d: ImageDraw.ImageDraw, lines, f, box: Tuple[float, float, float], y: float, *, color, hl=None,
               align: str = "center", lh: float = 1.2, stroke: int = 0, stroke_fill=(0, 0, 0, 255),
               chars: Optional[int] = None, hl_font=None, marker=None, marker_p: float = 1.0, marker_text=None,
               underline=None) -> Tuple[float, float, float]:
    """Gambar baris kaya-markup mulai dari y (= puncak huruf kapital baris pertama). box=(x0, lebar, _).
    Return (y_akhir, x_kursor, baseline_kursor) untuk kursor ketik."""
    x0, bw = box[0], box[1]
    cap = cap_h(f)
    step = f.size * lh
    left = chars
    cx, cby = x0, y + cap
    base = y + cap
    for line in lines:
        lw = line_w(line, f)
        x = x0 + (bw - lw) / 2 if align == "center" else x0
        sp = f.getlength(" ")
        if marker is not None and marker_p > 0:  # stabilo: satu blok per rangkaian kata yang ditonjolkan, menyapu dari kiri
            mx, run = x, None
            for j, (w, h) in enumerate(line + [("", False)]):
                ww = f.getlength(w)
                if h and run is None:
                    run = mx
                if not h and run is not None:
                    end = mx - sp
                    pad = f.size * 0.12
                    d.rounded_rectangle((run - pad, base - cap - f.size * 0.16,
                                         run - pad + (end - run + 2 * pad) * marker_p, base + f.size * 0.16),
                                        radius=int(f.size * 0.14), fill=marker)
                    run = None
                mx += ww + sp
        for w, h in line:
            ff = hl_font if (h and hl_font) else f
            txt = w
            if left is not None:
                if left <= 0:
                    return base + step - cap, cx, cby
                txt = w[:left]
                left -= len(w) + 1
            on_marker = h and marker is not None and marker_p > 0.5
            col = (marker_text or color) if on_marker else (hl if (h and hl) else color)
            d.text((x, base), txt, font=ff, fill=col, anchor="ls", stroke_width=0 if on_marker else stroke,
                   stroke_fill=stroke_fill)
            ww = ff.getlength(txt)
            if h and underline is not None:
                d.rectangle((x, base + f.size * 0.08, x + ww, base + f.size * 0.08 + max(3, f.size * 0.07)), fill=underline)
            cx, cby = x + ww, base
            x += ff.getlength(w) + sp
        base += step
    return base - step + f.size * 0.22, cx, cby


def block_h(n_lines: int, f, lh: float = 1.2, caps: bool = False) -> float:
    """Tinggi visual blok teks: puncak kapital baris 1 → dasar baris terakhir (+ ruang descender bila bukan huruf besar)."""
    return cap_h(f) + max(0, n_lines - 1) * f.size * lh + f.size * (0.03 if caps else 0.24)


@dataclass
class Ctx:
    W: int
    u: float
    style: str
    parts: List[str]
    dur: float
    accent: str = "#FFD400"
    name: str = ""
    cache: Dict = field(default_factory=dict)

    def toks(self, i: int = 0) -> List[Tuple[str, bool]]:
        return tokens(self.parts[i]) if i < len(self.parts) else []


def shadow_rrect(img: Image.Image, box, r: float, fill, *, ctx: Ctx, blur: float = 22, off: Tuple[float, float] = (0, 10),
                 shadow: int = 120, outline=None, width: int = 0) -> None:
    """Kotak membulat + bayangan lembut (bayangan di-cache per ukuran)."""
    u = ctx.u
    key = ("sh", img.size, tuple(int(v) for v in box), int(r), blur, off, shadow)
    layer = ctx.cache.get(key)
    if layer is None:
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(layer).rounded_rectangle((box[0] + off[0] * u, box[1] + off[1] * u, box[2] + off[0] * u, box[3] + off[1] * u),
                                                radius=int(r), fill=(0, 0, 0, shadow))
        layer = layer.filter(ImageFilter.GaussianBlur(blur * u))
        ctx.cache[key] = layer
    img.alpha_composite(layer)
    ImageDraw.Draw(img).rounded_rectangle(box, radius=int(r), fill=fill, outline=outline, width=width)


def transform(img: Image.Image, scale: float = 1.0, dx: float = 0, dy: float = 0, alpha: float = 1.0, rot: float = 0.0,
              center: Optional[Tuple[float, float]] = None) -> Image.Image:
    """Skala/geser/putar/transparansi di kanvas berukuran sama."""
    if scale == 1 and not dx and not dy and alpha >= 1 and not rot:
        return img
    w, h = img.size
    cx, cy = center or (w / 2, h / 2)
    im = img
    if rot:
        im = im.rotate(rot, resample=Image.BICUBIC, center=(cx, cy))
    if abs(scale - 1) > 1e-3:
        sw, shh = max(1, int(w * scale)), max(1, int(h * scale))
        im = im.resize((sw, shh), Image.BICUBIC)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ox = int(round(cx - cx * scale + dx))
    oy = int(round(cy - cy * scale + dy))
    out.paste(im, (ox, oy), im)
    if alpha < 1:
        a = out.getchannel("A").point(lambda v, k=clamp(alpha): int(v * k))
        out.putalpha(a)
    return out


# ====================================================================== gaya

def s_breaking(c: Ctx, t: float):
    u, W = c.u, c.W
    toks = upper(c.toks())
    pad, mx = 36 * u, 54 * u
    f, lines = fit(toks, "black", W - 2 * mx - 2 * pad, 3, int(66 * u), int(40 * u))
    th = block_h(len(lines), f, 1.08, caps=True)
    tag_h, gap = 68 * u, 10 * u
    H = int(tag_h + gap + th + 2 * pad + 40 * u)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bar = (mx, tag_h + gap, W - mx, tag_h + gap + th + 2 * pad)
    p = ease_out(prog(t, 0.05, 0.38))
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    shadow_rrect(layer, bar, 14 * u, (255, 255, 255, 250), ctx=c)
    d = ImageDraw.Draw(layer)
    d.rectangle((bar[0], bar[1], bar[0] + 14 * u, bar[3]), fill=_rgba("#E50914"))
    draw_lines(d, lines, f, (bar[0] + pad + 8 * u, bar[2] - bar[0] - 2 * pad - 8 * u, 0), bar[1] + pad,
               color=(17, 17, 17, 255), hl=_rgba("#E50914"), align="left", lh=1.08)
    mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask).rectangle((0, 0, int(mx + (W - 2 * mx) * p), H), fill=255)
    img.paste(layer, (0, 0), Image.composite(layer.getchannel("A"), Image.new("L", (W, H), 0), mask))
    tp = ease_back(prog(t, 0.0, 0.3))
    tf = font("black", int(40 * u))
    label = "BREAKING"
    tw = tf.getlength(label) + 74 * u
    tag = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    td = ImageDraw.Draw(tag)
    td.rounded_rectangle((mx, 0, mx + tw, tag_h), radius=int(10 * u), fill=_rgba("#E50914"))
    blink = 255 if int(t * 3) % 2 == 0 else 110
    td.ellipse((mx + 22 * u, tag_h / 2 - 9 * u, mx + 40 * u, tag_h / 2 + 9 * u), fill=(255, 255, 255, blink))
    td.text((mx + 54 * u, tag_h / 2), label, font=tf, fill="white", anchor="lm")
    img.alpha_composite(transform(tag, scale=max(0.01, tp), center=(mx, tag_h / 2)))
    return img


def s_stabilo(c: Ctx, t: float):
    u, W = c.u, c.W
    toks = upper(c.toks())
    f, lines = fit(toks, "black", W - 140 * u, 3, int(84 * u), int(52 * u))
    H = int(block_h(len(lines), f, 1.18, caps=True) + 60 * u)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    mp = ease_out(prog(t, 0.22, 0.62))
    draw_lines(d, lines, f, (70 * u, W - 140 * u, 0), 24 * u, color="white", stroke=int(7 * u), lh=1.18,
               marker=_rgba("#FFE11A"), marker_p=mp, marker_text=(17, 17, 17, 255))
    return transform(img, scale=0.86 + 0.14 * ease_back(prog(t, 0, ENTER)), alpha=prog(t, 0, 0.12))


def s_sticker(c: Ctx, t: float):
    u, W = c.u, c.W
    toks = upper(c.toks())
    f, lines = fit(toks, "black", W - 300 * u, 3, int(74 * u), int(46 * u))
    th = block_h(len(lines), f, 1.1, caps=True)
    tw = max(line_w(ln, f) for ln in lines)
    pad = 40 * u
    bw, bh = tw + 2 * pad, th + 2 * pad
    H = int(bh + 120 * u)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    x0, y0 = (W - bw) / 2, 50 * u
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((x0 + 14 * u, y0 + 14 * u, x0 + bw + 14 * u, y0 + bh + 14 * u), radius=int(30 * u), fill=(0, 0, 0, 255))
    d.rounded_rectangle((x0, y0, x0 + bw, y0 + bh), radius=int(30 * u), fill="white", outline=(0, 0, 0, 255), width=int(9 * u))
    draw_lines(d, lines, f, (x0, bw, 0), y0 + pad, color=(17, 17, 17, 255), hl=_rgba("#FF2D55"), lh=1.1)
    wob = math.sin(t * 7) * 1.2 * (1 - prog(t, 0.4, 1.4))
    return transform(img, scale=max(0.05, ease_back(prog(t, 0, ENTER), 2.4)), rot=4 + wob)


def _app_icon(d, box, color, u, glyph="!"):
    d.rounded_rectangle(box, radius=int((box[2] - box[0]) * 0.24), fill=_rgba(color))
    gf = font("black", int((box[3] - box[1]) * 0.58))
    d.text(((box[0] + box[2]) / 2, (box[1] + box[3]) / 2), glyph, font=gf, fill="white", anchor="mm")


def s_notif(c: Ctx, t: float):
    u, W = c.u, c.W
    mx, pad = 46 * u, 34 * u
    toks = c.toks()
    f, lines = fit(toks, "bold", W - 2 * mx - 2 * pad, 3, int(46 * u), int(34 * u))
    head = 96 * u
    th = block_h(len(lines), f, 1.12)
    H = int(head + th + 2 * pad + 50 * u)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    card = (mx, 10 * u, W - mx, 10 * u + head + th + pad * 1.6)
    shadow_rrect(img, card, 40 * u, (246, 246, 248, 245), ctx=c, shadow=110)
    d = ImageDraw.Draw(img)
    ix = card[0] + pad
    _app_icon(d, (ix, card[1] + 28 * u, ix + 62 * u, card[1] + 90 * u), c.accent if c.accent != "#FFD400" else "#25D366", u, "!")
    sf = font("semi", int(30 * u))
    d.text((ix + 80 * u, card[1] + 40 * u), "PESAN BARU", font=sf, fill=(110, 110, 118, 255))
    d.text((card[2] - pad, card[1] + 40 * u), "sekarang", font=sf, fill=(140, 140, 148, 255), anchor="ra")
    draw_lines(d, lines, f, (ix, card[2] - card[0] - 2 * pad, 0), card[1] + head + 6 * u, color=(20, 20, 24, 255),
               hl=_rgba("#0A84FF"), align="left")
    p = ease_back(prog(t, 0, 0.42), 1.4)
    return transform(img, dy=-(1 - p) * H, alpha=prog(t, 0, 0.15))


def _typing_dots(d, x, y, u, t):
    for i in range(3):
        a = 0.5 + 0.5 * math.sin(t * 10 - i * 0.9)
        r = 10 * u
        d.ellipse((x + i * 34 * u, y - r - a * 8 * u, x + i * 34 * u + 2 * r, y + r - a * 8 * u), fill=(140, 140, 150, 255))


def s_chat(c: Ctx, t: float):
    u, W = c.u, c.W
    toks = c.toks()
    av = 92 * u
    mx, pad = 40 * u, 32 * u
    max_bw = W - 2 * mx - av - 24 * u
    f, lines = fit(toks, "bold", max_bw - 2 * pad, 3, int(50 * u), int(34 * u))
    tw = max(line_w(ln, f) for ln in lines)
    th = block_h(len(lines), f, 1.12)
    bw, bh = tw + 2 * pad, th + 2 * pad - 8 * u
    H = int(bh + 70 * u)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    ax, ay = mx, 20 * u + bh - av
    d.ellipse((ax, ay, ax + av, ay + av), fill=_rgba(c.accent if c.accent != "#FFD400" else "#7C4DFF"))
    d.text((ax + av / 2, ay + av / 2), (c.name[:1] or "K").upper(), font=font("black", int(46 * u)), fill="white", anchor="mm")
    bx = ax + av + 22 * u
    typing = t < 0.55
    bwx = (150 * u + 2 * pad) if typing else bw
    box = (bx, 20 * u, bx + bwx, 20 * u + bh)
    shadow_rrect(img, box, 44 * u, "white", ctx=c, shadow=100, blur=16)
    d = ImageDraw.Draw(img)
    d.polygon([(bx + 4 * u, box[3] - 40 * u), (bx - 18 * u, box[3] + 4 * u), (bx + 40 * u, box[3] - 6 * u)], fill="white")
    if typing:
        _typing_dots(d, bx + pad + 6 * u, (box[1] + box[3]) / 2, u, t)
    else:
        draw_lines(d, lines, f, (bx + pad, tw, 0), box[1] + pad - 6 * u, color=(22, 22, 26, 255), hl=_rgba("#1D9BF0"), align="left")
    p = ease_out(prog(t, 0, ENTER))
    return transform(img, dy=(1 - p) * 50 * u, alpha=p)


def s_stack(c: Ctx, t: float):
    u, W = c.u, c.W
    toks = upper(c.toks())
    f, lines = fit(toks, "black", W - 260 * u, 4, int(78 * u), int(46 * u))
    lh = block_h(1, f, caps=True)
    padx, pady, gap = 26 * u, 14 * u, 12 * u
    H = int(len(lines) * (lh + 2 * pady + gap) + 40 * u)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    y = 10 * u
    colors = [("#FFFFFF", "#111111"), (c.accent if c.accent != "#FFFFFF" else "#FFD400", "#111111")]
    for i, ln in enumerate(lines):
        bg, fg = colors[i % 2]
        lw = line_w(ln, f)
        x0 = 70 * u
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        d.rectangle((x0 + 10 * u, y + 10 * u, x0 + lw + 2 * padx + 10 * u, y + lh + 2 * pady + 10 * u), fill=(0, 0, 0, 200))
        d.rectangle((x0, y, x0 + lw + 2 * padx, y + lh + 2 * pady), fill=_rgba(bg))
        draw_lines(d, [ln], f, (x0 + padx, lw, 0), y + pady, color=_rgba(fg), hl=_rgba("#E50914") if bg == "#FFFFFF" else _rgba("#FFFFFF"),
                   align="left", lh=1.04, stroke=0)
        p = ease_out(prog(t, i * 0.08, i * 0.08 + 0.3))
        img.alpha_composite(transform(layer, dx=-(1 - p) * (lw + 300 * u), alpha=p))
        y += lh + 2 * pady + gap
    return img


def s_strike(c: Ctx, t: float):
    u, W = c.u, c.W
    a, b = upper(c.toks(0)), upper(c.toks(1))
    fa, la = fit(a, "black", W - 260 * u, 2, int(58 * u), int(38 * u))
    fb, lb = fit(b, "black", W - 220 * u, 2, int(80 * u), int(46 * u))
    ha, hb = block_h(len(la), fa, caps=True), block_h(len(lb), fb, caps=True)
    pad = 30 * u
    H = int(ha + hb + 4 * pad + 70 * u)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    wa = max(line_w(ln, fa) for ln in la)
    xa = (W - wa) / 2
    ya = 14 * u
    d.rounded_rectangle((xa - pad, ya, xa + wa + pad, ya + ha + 1.4 * pad), radius=int(14 * u), fill=(10, 10, 10, 200))
    draw_lines(d, la, fa, (xa, wa, 0), ya + pad * 0.7, color=(205, 205, 205, 255))
    sp = ease_out(prog(t, 0.3, 0.6))
    if sp > 0:
        cap = cap_h(fa)
        for i, ln in enumerate(la):
            lw = line_w(ln, fa)
            lx = xa + (wa - lw) / 2 - 10 * u
            ly = ya + pad * 0.7 + cap + i * fa.size * 1.2 - cap * 0.45
            d.line((lx, ly + 3 * u, lx + (lw + 20 * u) * sp, ly - 3 * u * sp), fill=_rgba("#FF2D2D"), width=int(11 * u))
    bp = ease_back(prog(t, 0.62, 0.95))
    if bp > 0:
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        dl = ImageDraw.Draw(layer)
        wb = max(line_w(ln, fb) for ln in lb)
        xb, yb = (W - wb) / 2, ya + ha + 1.4 * pad + 22 * u
        dl.rounded_rectangle((xb - pad, yb, xb + wb + pad, yb + hb + 1.4 * pad), radius=int(16 * u), fill=_rgba(c.accent))
        draw_lines(dl, lb, fb, (xb, wb, 0), yb + pad * 0.7, color=(17, 17, 17, 255))
        img.alpha_composite(transform(layer, scale=max(0.05, bp), center=(W / 2, yb + hb / 2)))
    return transform(img, alpha=prog(t, 0, 0.15))


def s_typewriter(c: Ctx, t: float):
    u, W = c.u, c.W
    toks = c.toks()
    mx, pad = 70 * u, 40 * u
    f, lines = fit(toks, "mono", W - 2 * mx - 2 * pad, 4, int(52 * u), int(34 * u))
    th = block_h(len(lines), f, 1.18)
    H = int(th + 2 * pad + 50 * u)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    card = (mx, 10 * u, W - mx, 10 * u + th + 2 * pad)
    shadow_rrect(img, card, 10 * u, (246, 240, 226, 250), ctx=c, shadow=130)
    d = ImageDraw.Draw(img)
    total = sum(len(w) + 1 for ln in lines for w, _ in ln)
    t_end = min(1.6, max(0.8, c.dur * 0.5))
    n = int(total * prog(t, 0.12, t_end))
    _, cx, cy = draw_lines(d, lines, f, (card[0] + pad, card[2] - card[0] - 2 * pad, 0), card[1] + pad, color=(34, 30, 26, 255),
                           hl=_rgba("#C1121F"), align="left", lh=1.18, chars=n, underline=_rgba("#C1121F"))
    if int(t * 4) % 2 == 0 or n < total:
        d.rectangle((cx + 4 * u, cy - cap_h(f), cx + 4 * u + f.getlength("M") * 0.55, cy + f.size * 0.12), fill=(34, 30, 26, 255))
    return transform(img, alpha=prog(t, 0, 0.15))


def s_editorial(c: Ctx, t: float):
    u, W = c.u, c.W
    toks = lower(c.toks())
    f, lines = fit(toks, "serif", W - 160 * u, 4, int(86 * u), int(50 * u))
    fi = font("serif_i", f.size)
    H = int(block_h(len(lines), f, 1.1) + 70 * u)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    k = 0
    y = 24 * u + cap_h(f)   # baseline baris pertama
    step = f.size * 1.1
    for ln in lines:
        lw = sum((fi if h else f).getlength(w) for w, h in ln) + f.getlength(" ") * (len(ln) - 1)
        x = (W - lw) / 2
        for w, h in ln:
            ff = fi if h else f
            layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            dl = ImageDraw.Draw(layer)
            col = _rgba(c.accent) if h else (255, 255, 255, 255)
            dl.text((x, y), w, font=ff, fill=col, anchor="ls", stroke_width=int(4 * u), stroke_fill=(0, 0, 0, 230))
            if h:
                ww = ff.getlength(w)
                dl.line((x, y + f.size * 0.16, x + ww, y + f.size * 0.1), fill=col, width=int(7 * u))
            p = ease_out(prog(t, k * 0.05, k * 0.05 + 0.32))
            img.alpha_composite(transform(layer, dy=(1 - p) * 34 * u, alpha=p))
            x += ff.getlength(w) + f.getlength(" ")
            k += 1
        y += step
    return img


def s_reply(c: Ctx, t: float):
    u, W = c.u, c.W
    toks = c.toks()
    mx, pad = 60 * u, 32 * u
    f, lines = fit(toks, "bold", W - 2 * mx - 2 * pad, 3, int(48 * u), int(34 * u))
    tw = max(max(line_w(ln, f) for ln in lines), 520 * u)
    th = block_h(len(lines), f, 1.12)
    head = 86 * u
    bw, bh = tw + 2 * pad, head + th + 1.6 * pad
    H = int(bh + 80 * u)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    x0 = (W - bw) / 2
    box = (x0, 14 * u, x0 + bw, 14 * u + bh)
    shadow_rrect(img, box, 28 * u, "white", ctx=c, shadow=110)
    d = ImageDraw.Draw(img)
    d.polygon([(x0 + 60 * u, box[3] - 2), (x0 + 60 * u, box[3] + 34 * u), (x0 + 104 * u, box[3] - 2)], fill="white")
    r = 26 * u
    d.ellipse((x0 + pad, box[1] + pad - 4 * u, x0 + pad + 2 * r, box[1] + pad - 4 * u + 2 * r), fill=_rgba("#FE2C55"))
    sf = font("semi", int(28 * u))
    d.text((x0 + pad + 2 * r + 16 * u, box[1] + pad + r - 4 * u), f"Membalas komentar @{c.name or 'penonton'}", font=sf,
           fill=(120, 120, 128, 255), anchor="lm")
    draw_lines(d, lines, f, (x0 + pad, tw, 0), box[1] + head + 8 * u, color=(22, 22, 26, 255), hl=_rgba("#FE2C55"), align="left")
    return transform(img, scale=max(0.05, ease_back(prog(t, 0, ENTER), 1.5)), alpha=prog(t, 0, 0.1))


def _pct(text: str) -> int:
    return 52 + int(hashlib.md5(text.encode("utf-8")).hexdigest()[:4], 16) % 27


def s_poll(c: Ctx, t: float):
    u, W = c.u, c.W
    mx, pad = 70 * u, 36 * u
    q = c.toks(0)
    fq, lq = fit(q, "bold", W - 2 * mx - 2 * pad, 2, int(50 * u), int(36 * u))
    fo = font("bold", int(40 * u))
    opt_h, gap = 84 * u, 18 * u
    hq = block_h(len(lq), fq)
    foot = 54 * u
    H = int(hq + 2 * (opt_h + gap) + foot + 2 * pad + 40 * u)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    card = (mx, 10 * u, W - mx, 10 * u + hq + 2 * (opt_h + gap) + foot + 1.6 * pad)
    shadow_rrect(img, card, 34 * u, "white", ctx=c, shadow=110)
    d = ImageDraw.Draw(img)
    draw_lines(d, lq, fq, (card[0] + pad, card[2] - card[0] - 2 * pad, 0), card[1] + pad, color=(20, 20, 24, 255), align="left")
    a = _pct(c.parts[0])
    pcts = [a, 100 - a]
    y = card[1] + pad + hq + 10 * u
    fill_p = ease_out(prog(t, 0.45, 1.25))
    for i in range(2):
        label = c.parts[i + 1] if i + 1 < len(c.parts) else ("Setuju" if i == 0 else "Nggak")
        bx0, bx1 = card[0] + pad, card[2] - pad
        d.rounded_rectangle((bx0, y, bx1, y + opt_h), radius=int(opt_h / 2), fill=(236, 237, 241, 255))
        fw = (bx1 - bx0) * pcts[i] / 100 * fill_p
        if fw > opt_h * 0.6:
            d.rounded_rectangle((bx0, y, bx0 + fw, y + opt_h), radius=int(opt_h / 2),
                                fill=_rgba("#FE2C55" if i == 0 else "#25F4EE"))
        d.text((bx0 + 30 * u, y + opt_h / 2), clean(label)[:28], font=fo, fill=(20, 20, 24, 255), anchor="lm")
        if fill_p > 0.05:
            d.text((bx1 - 30 * u, y + opt_h / 2), f"{int(pcts[i] * fill_p)}%", font=fo, fill=(20, 20, 24, 255), anchor="rm")
        y += opt_h + gap
    d.text((W / 2, y + foot / 2 - 6 * u), "Jawab di komentar", font=font("semi", int(30 * u)),
           fill=(130, 130, 138, 255), anchor="mm")
    return transform(img, scale=max(0.05, ease_back(prog(t, 0, ENTER), 1.3)), alpha=prog(t, 0, 0.1))


def s_stamp(c: Ctx, t: float):
    u, W = c.u, c.W
    toks = c.toks(0)
    mx, pad = 70 * u, 40 * u
    f, lines = fit(toks, "bold", W - 2 * mx - 2 * pad, 3, int(54 * u), int(38 * u))
    th = block_h(len(lines), f)
    H = int(th + 2 * pad + 170 * u)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    card = (mx, 10 * u, W - mx, 10 * u + th + 2 * pad)
    shadow_rrect(img, card, 22 * u, (16, 16, 18, 225), ctx=c, shadow=90)
    d = ImageDraw.Draw(img)
    draw_lines(d, lines, f, (card[0] + pad, card[2] - card[0] - 2 * pad, 0), card[1] + pad, color="white", hl=_rgba(c.accent), align="left")
    verdict = (c.parts[1] if len(c.parts) > 1 else "FAKTA").upper()
    col = "#E53935" if re.search(r"MITOS|HOAX|BOHONG|SALAH", verdict) else "#22C55E"
    sp = prog(t, 0.42, 0.62)
    if sp > 0:
        sf = font("black", int(78 * u))
        tw = sf.getlength(verdict)
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        dl = ImageDraw.Draw(layer)
        cx, cy = card[2] - tw / 2 - 70 * u, card[3] + 10 * u
        box = (cx - tw / 2 - 30 * u, cy - 56 * u, cx + tw / 2 + 30 * u, cy + 56 * u)
        dl.rounded_rectangle(box, radius=int(18 * u), outline=_rgba(col), width=int(10 * u), fill=(255, 255, 255, 235))
        dl.text((cx, cy), verdict, font=sf, fill=_rgba(col), anchor="mm")
        scale = 2.3 - 1.3 * ease_out(sp)
        layer = transform(layer, scale=scale, rot=12, alpha=clamp(sp * 2), center=(cx, cy))
        img.alpha_composite(layer)
    return transform(img, alpha=prog(t, 0, 0.15))


def s_tweet(c: Ctx, t: float):
    u, W = c.u, c.W
    toks = c.toks()
    mx, pad = 54 * u, 36 * u
    f, lines = fit(toks, "bold", W - 2 * mx - 2 * pad, 4, int(46 * u), int(34 * u))
    head = 132 * u
    th = block_h(len(lines), f, 1.16)
    H = int(head + th + 2 * pad + 40 * u)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    card = (mx, 10 * u, W - mx, 10 * u + head + th + 1.4 * pad)
    shadow_rrect(img, card, 30 * u, "white", ctx=c, shadow=110)
    d = ImageDraw.Draw(img)
    r = 38 * u
    ax, ay = card[0] + pad, card[1] + pad
    d.ellipse((ax, ay, ax + 2 * r, ay + 2 * r), fill=_rgba(c.accent if c.accent != "#FFD400" else "#111111"))
    d.text((ax + r, ay + r), (c.name[:1] or "H").upper(), font=font("black", int(36 * u)), fill="white", anchor="mm")
    name = c.name or "Hot Take"
    nf, hf = font("bold", int(34 * u)), font("semi", int(28 * u))
    d.text((ax + 2 * r + 18 * u, ay + 8 * u), name[:26], font=nf, fill=(15, 20, 25, 255))
    handle = "@" + re.sub(r"[^a-z0-9_]", "", name.lower())[:15] + " · baru saja"
    d.text((ax + 2 * r + 18 * u, ay + 48 * u), handle, font=hf, fill=(83, 100, 113, 255))
    d.text((card[2] - pad, ay + 6 * u), "X", font=font("black", int(42 * u)), fill=(15, 20, 25, 255), anchor="ra")
    draw_lines(d, lines, f, (ax, card[2] - card[0] - 2 * pad, 0), card[1] + head + 6 * u, color=(15, 20, 25, 255),
               hl=_rgba("#1D9BF0"), align="left", lh=1.16)
    p = ease_out(prog(t, 0, ENTER))
    return transform(img, dy=(1 - p) * 60 * u, alpha=p)


def _split_num(s: str) -> Tuple[str, float, str, int]:
    """'Rp 3.000' → ('Rp ', 3000, '', 0) ; '90%' → ('', 90, '%', 0) ; '1,5 juta' → ('', 1.5, ' juta', 1)."""
    m = re.search(r"\d[\d.,]*", s)
    if not m:
        return "", 0.0, s, 0
    raw = m.group(0)
    if re.fullmatch(r"\d{1,3}(\.\d{3})+", raw):
        val, dec = float(raw.replace(".", "")), 0
    elif "," in raw and re.fullmatch(r"\d+,\d+", raw):
        val, dec = float(raw.replace(",", ".")), len(raw.split(",")[1])
    else:
        try:
            val = float(raw.replace(",", ""))
        except ValueError:
            val = 0.0
        dec = len(raw.split(".")[1]) if "." in raw and not re.fullmatch(r"\d{1,3}(\.\d{3})+", raw) else 0
    return s[:m.start()], val, s[m.end():], dec


def _fmt_num(v: float, dec: int) -> str:
    if dec:
        return f"{v:.{dec}f}".replace(".", ",")
    return f"{int(round(v)):,}".replace(",", ".")


def s_bigstat(c: Ctx, t: float):
    u, W = c.u, c.W
    pre, val, suf, dec = _split_num(c.parts[0] if c.parts else "")
    p = ease_out(prog(t, 0.1, 1.0))
    num = pre + _fmt_num(val * p, dec) + suf
    final = pre + _fmt_num(val, dec) + suf
    nf = font("black", int(210 * u))
    while nf.getlength(final.upper()) > W - 120 * u and nf.size > 70 * u:
        nf = font("black", int(nf.size * 0.9))
    cap = upper(c.toks(1))
    fc, lc = fit(cap, "black", W - 220 * u, 2, int(52 * u), int(34 * u))
    hc = block_h(len(lc), fc, caps=True)
    asc, desc = nf.getmetrics()
    H = int(asc + desc + hc + 90 * u)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.text((W / 2 + 10 * u, 12 * u), num.upper(), font=nf, fill=(0, 0, 0, 200), anchor="ma")
    d.text((W / 2, 0), num.upper(), font=nf, fill=_rgba(c.accent), anchor="ma", stroke_width=int(8 * u), stroke_fill=(0, 0, 0, 255))
    if lc:
        wc = max(line_w(ln, fc) for ln in lc)
        yc = asc + desc - 6 * u
        d.rounded_rectangle(((W - wc) / 2 - 26 * u, yc, (W + wc) / 2 + 26 * u, yc + hc + 22 * u), radius=int(14 * u),
                            fill=(12, 12, 14, 220))
        draw_lines(d, lc, fc, ((W - wc) / 2, wc, 0), yc + 10 * u, color="white", hl=_rgba(c.accent))
    return transform(img, scale=0.8 + 0.2 * ease_back(prog(t, 0, ENTER)), alpha=prog(t, 0, 0.12))


def s_search(c: Ctx, t: float):
    u, W = c.u, c.W
    toks = lower(c.toks())
    mx = 50 * u
    bar_h = 120 * u
    f, lines = fit(toks, "semi", W - 2 * mx - 250 * u, 2, int(46 * u), int(32 * u))
    th = block_h(len(lines), f)
    bh = max(bar_h, th + 44 * u)
    H = int(bh + 50 * u)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    box = (mx, 10 * u, W - mx, 10 * u + bh)
    shadow_rrect(img, box, bh / 2, "white", ctx=c, shadow=110)
    d = ImageDraw.Draw(img)
    cx, cy = box[0] + 60 * u, (box[1] + box[3]) / 2
    r = 20 * u
    d.ellipse((cx - r, cy - r - 6 * u, cx + r, cy + r - 6 * u), outline=(120, 120, 128, 255), width=int(6 * u))
    d.line((cx + r * 0.7, cy + r * 0.7 - 6 * u, cx + r * 1.6, cy + r * 1.6 - 6 * u), fill=(120, 120, 128, 255), width=int(7 * u))
    total = sum(len(w) + 1 for ln in lines for w, _ in ln)
    t_end = min(1.25, max(0.7, c.dur * 0.42))
    n = int(total * prog(t, 0.15, t_end))
    _, kx, ky = draw_lines(d, lines, f, (box[0] + 110 * u, box[2] - box[0] - 250 * u, 0), cy - th / 2, color=(30, 30, 34, 255),
                           hl=(0, 0, 0, 255), hl_font=font("bold", f.size), align="left", chars=n)
    if n < total or int(t * 4) % 2 == 0:
        d.rectangle((kx + 5 * u, ky - cap_h(f) - 4 * u, kx + 9 * u, ky + f.size * 0.16), fill=_rgba("#1A73E8"))
    pulse = 1 + 0.12 * math.sin(clamp((t - t_end) / 0.35) * math.pi) if t > t_end else 1
    br = (bh / 2 - 14 * u) * pulse
    bx, by = box[2] - bh / 2, cy
    d.ellipse((bx - br, by - br, bx + br, by + br), fill=_rgba("#1A73E8"))
    d.line((bx - br * 0.4, by, bx + br * 0.35, by), fill="white", width=int(8 * u))
    d.line((bx + br * 0.05, by - br * 0.35, bx + br * 0.4, by), fill="white", width=int(8 * u))
    d.line((bx + br * 0.05, by + br * 0.35, bx + br * 0.4, by), fill="white", width=int(8 * u))
    return transform(img, scale=0.9 + 0.1 * ease_out(prog(t, 0, ENTER)), alpha=prog(t, 0, 0.12))


def s_versus(c: Ctx, t: float):
    u, W = c.u, c.W
    a, b = upper(c.toks(0)), upper(c.toks(1))
    colw = (W - 3 * 40 * u) / 2 - 30 * u
    fa, la = fit(a, "black", colw - 40 * u, 3, int(56 * u), int(32 * u))
    fb, lb = fit(b, "black", colw - 40 * u, 3, int(56 * u), int(32 * u))
    fs = font("black", min(fa.size, fb.size))
    la, lb = _wrap(a, fs, colw - 40 * u), _wrap(b, fs, colw - 40 * u)
    bh = max(block_h(len(la), fs, caps=True), block_h(len(lb), fs, caps=True)) + 70 * u
    q = c.toks(2)
    fq, lq = fit(q, "bold", W - 160 * u, 2, int(40 * u), int(30 * u)) if q else (None, [])
    hq = block_h(len(lq), fq) + 30 * u if lq else 0
    H = int(bh + hq + 50 * u)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    pa = ease_out(prog(t, 0, 0.32))
    for i, (ln, colr, side) in enumerate(((la, "#E53935", -1), (lb, "#1E88E5", 1))):
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        x0 = 40 * u if side < 0 else W / 2 + 30 * u
        box = (x0, 10 * u, x0 + colw, 10 * u + bh)
        shadow_rrect(layer, box, 22 * u, _rgba(colr), ctx=c, shadow=120)
        dl = ImageDraw.Draw(layer)
        draw_lines(dl, ln, fs, (x0, colw, 0), 10 * u + (bh - block_h(len(ln), fs, caps=True)) / 2, color="white")
        img.alpha_composite(transform(layer, dx=side * (1 - pa) * W * 0.6, alpha=pa))
    vp = ease_back(prog(t, 0.3, 0.58), 2.6)
    if vp > 0:
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        dl = ImageDraw.Draw(layer)
        r = 66 * u
        cx, cy = W / 2, 10 * u + bh / 2
        dl.ellipse((cx - r, cy - r, cx + r, cy + r), fill=_rgba("#FFD400"), outline=(0, 0, 0, 255), width=int(7 * u))
        dl.text((cx, cy), "VS", font=font("black", int(60 * u)), fill=(17, 17, 17, 255), anchor="mm")
        img.alpha_composite(transform(layer, scale=vp, center=(cx, cy), rot=-8))
    if lq:
        d = ImageDraw.Draw(img)
        draw_lines(d, lq, fq, (80 * u, W - 160 * u, 0), 10 * u + bh + 22 * u, color="white", stroke=int(5 * u))
    return img


def _trophy(d, cx, cy, r, u):
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=_rgba("#F5B301"), outline=_rgba("#FFE27A"), width=int(5 * u))
    w = r * 0.9
    d.rounded_rectangle((cx - w / 2, cy - r * 0.5, cx + w / 2, cy + r * 0.12), radius=int(r * 0.3), fill="white")
    d.rectangle((cx - r * 0.08, cy + r * 0.1, cx + r * 0.08, cy + r * 0.38), fill="white")
    d.rectangle((cx - r * 0.3, cy + r * 0.36, cx + r * 0.3, cy + r * 0.5), fill="white")


def s_achievement(c: Ctx, t: float):
    u, W = c.u, c.W
    toks = c.toks()
    mx, pad = 50 * u, 30 * u
    icon = 110 * u
    f, lines = fit(toks, "bold", W - 2 * mx - icon - 3 * pad, 2, int(46 * u), int(32 * u))
    th = block_h(len(lines), f)
    bh = max(icon + 2 * pad, th + 2 * pad + 50 * u)
    H = int(bh + 40 * u)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    box = (mx, 10 * u, W - mx, 10 * u + bh)
    shadow_rrect(img, box, 26 * u, (24, 27, 38, 240), ctx=c, outline=_rgba("#F5B301"), width=int(5 * u), shadow=140)
    d = ImageDraw.Draw(img)
    _trophy(d, box[0] + pad + icon / 2, (box[1] + box[3]) / 2, icon / 2, u)
    tx = box[0] + 2 * pad + icon
    d.text((tx, box[1] + pad), "PENCAPAIAN TERBUKA · +100 XP", font=font("bold", int(26 * u)), fill=_rgba("#F5B301"))
    draw_lines(d, lines, f, (tx, box[2] - tx - pad, 0), box[1] + pad + 44 * u, color="white", hl=_rgba("#FFE27A"), align="left")
    sp = prog(t, 0.5, 1.0)
    if 0 < sp < 1:
        shine = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        sx = box[0] + (box[2] - box[0] + 300 * u) * sp - 150 * u
        ImageDraw.Draw(shine).polygon([(sx, box[1]), (sx + 70 * u, box[1]), (sx - 30 * u, box[3]), (sx - 100 * u, box[3])],
                                       fill=(255, 255, 255, 70))
        m = Image.new("L", (W, H), 0)
        ImageDraw.Draw(m).rounded_rectangle(box, radius=int(26 * u), fill=255)
        img.paste(Image.alpha_composite(img, shine), (0, 0), m)
    p = ease_back(prog(t, 0, 0.42), 1.3)
    return transform(img, dy=(1 - p) * 80 * u, alpha=prog(t, 0, 0.15))


def s_checklist(c: Ctx, t: float):
    u, W = c.u, c.W
    mx, pad = 70 * u, 36 * u
    title = c.toks(0)
    ft, lt = fit(title, "bold", W - 2 * mx - 2 * pad, 2, int(50 * u), int(36 * u))
    items = [clean(p) for p in c.parts[1:5]]
    fi = font("bold", int(42 * u))
    row = 74 * u
    ht = block_h(len(lt), ft)
    H = int(ht + len(items) * row + 2 * pad + 50 * u)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    card = (mx, 10 * u, W - mx, 10 * u + ht + len(items) * row + 1.8 * pad)
    shadow_rrect(img, card, 28 * u, "white", ctx=c, shadow=110)
    d = ImageDraw.Draw(img)
    draw_lines(d, lt, ft, (card[0] + pad, card[2] - card[0] - 2 * pad, 0), card[1] + pad, color=(20, 20, 24, 255), align="left")
    y = card[1] + pad + ht + 10 * u
    for i, it in enumerate(items):
        bs = 46 * u
        bx, by = card[0] + pad, y + (row - bs) / 2
        cp = prog(t, 0.45 + i * 0.32, 0.6 + i * 0.32)
        d.rounded_rectangle((bx, by, bx + bs, by + bs), radius=int(10 * u), outline=(40, 40, 44, 255), width=int(5 * u),
                            fill=_rgba("#22C55E") if cp > 0 else None)
        if cp > 0:
            pts = [(bx + bs * 0.22, by + bs * 0.52), (bx + bs * 0.43, by + bs * 0.74), (bx + bs * 0.8, by + bs * 0.28)]
            seg = cp * 2
            d.line([pts[0], (pts[0][0] + (pts[1][0] - pts[0][0]) * min(1, seg), pts[0][1] + (pts[1][1] - pts[0][1]) * min(1, seg))],
                   fill="white", width=int(7 * u))
            if seg > 1:
                k = seg - 1
                d.line([pts[1], (pts[1][0] + (pts[2][0] - pts[1][0]) * k, pts[1][1] + (pts[2][1] - pts[1][1]) * k)], fill="white", width=int(7 * u))
        txt = it
        while fi.getlength(txt) > card[2] - bx - bs - 2 * pad and len(txt) > 4:
            txt = txt[:-2]
        d.text((bx + bs + 22 * u, y + row / 2), txt, font=fi, fill=(20, 20, 24, 255) if cp > 0 else (110, 110, 118, 255), anchor="lm")
        y += row
    return transform(img, scale=max(0.05, ease_back(prog(t, 0, ENTER), 1.2)), alpha=prog(t, 0, 0.1))


def s_quote(c: Ctx, t: float):
    u, W = c.u, c.W
    toks = upper(c.toks())
    f, lines = fit(toks, "black", W - 200 * u, 4, int(70 * u), int(42 * u))
    th = block_h(len(lines), f, 1.12, caps=True)
    qf = font("serif", int(200 * u))
    H = int(th + 190 * u)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.text((W / 2, -20 * u), "\u201C", font=qf, fill=_rgba(c.accent), anchor="ma", stroke_width=int(4 * u), stroke_fill=(0, 0, 0, 200))
    draw_lines(d, lines, f, (100 * u, W - 200 * u, 0), 120 * u, color="white", hl=_rgba(c.accent), stroke=int(6 * u))
    lp = ease_out(prog(t, 0.2, 0.7))
    lw = 220 * u * lp
    d.rectangle((W / 2 - lw / 2, 120 * u + th + 34 * u, W / 2 + lw / 2, 120 * u + th + 44 * u), fill=_rgba(c.accent))
    return transform(img, scale=0.92 + 0.08 * ease_out(prog(t, 0, ENTER)), alpha=prog(t, 0, 0.2))


PAINTERS = {
    "breaking": s_breaking, "stabilo": s_stabilo, "sticker": s_sticker, "notif": s_notif, "chat": s_chat, "stack": s_stack,
    "strike": s_strike, "typewriter": s_typewriter, "editorial": s_editorial, "reply": s_reply, "poll": s_poll,
    "stamp": s_stamp, "tweet": s_tweet, "bigstat": s_bigstat, "search": s_search, "versus": s_versus,
    "achievement": s_achievement, "checklist": s_checklist, "quote": s_quote,
}


# ====================================================================== render

def make_ctx(spec: Dict, width: int, dur: float, accent: Optional[str] = None, name: str = "") -> Ctx:
    style = spec.get("style") if spec.get("style") in PAINTERS else "stabilo"
    return Ctx(W=int(width), u=width / 1080.0, style=style, parts=split_parts(spec.get("text") or "") or ["..."], dur=float(dur),
               accent=accent or STYLE_ACCENT.get(style, "#FFD400"), name=clean(name)[:26])


STYLE_ACCENT = {"stabilo": "#FFE11A", "stack": "#FFD400", "strike": "#FFD400", "editorial": "#FFD400", "bigstat": "#FFD400",
                "quote": "#FFD400", "stamp": "#FFD400"}


def frame(ctx: Ctx, t: float) -> Image.Image:
    """Satu frame (RGBA, lebar video) pada detik t, termasuk animasi keluar."""
    img = PAINTERS[ctx.style](ctx, t)
    out = clamp((ctx.dur - t) / EXIT)
    if out < 1:
        img = transform(img, scale=0.96 + 0.04 * out, alpha=out)
    return img


def render_sequence(spec: Dict, width: int, height: int, out_dir: Path, *, duration: Optional[float] = None,
                    y_frac: float = 0.11, accent: Optional[str] = None, name: str = "") -> Optional[Dict]:
    """Render urutan PNG untuk overlay ffmpeg. Return {pattern, x, y, dur, frames, style, text} atau None."""
    dur = float(duration or getattr(config, "HOOK_OVERLAY_DURATION", 3.0))
    dur = max(1.2, min(dur, 8.0))
    ctx = make_ctx(spec, width, dur, accent, name)
    seq_dir = Path(out_dir) / "hook_seq"
    shutil.rmtree(seq_dir, ignore_errors=True)
    seq_dir.mkdir(parents=True, exist_ok=True)
    n = int(round(dur * FPS))
    first = frame(ctx, min(1.4, dur * 0.5))
    h = first.size[1]
    y = int(clamp(y_frac, 0.02, 0.9) * height)
    y = max(0, min(y, height - h))
    last_key, last_png = None, None
    for i in range(n):
        im = frame(ctx, i / FPS)
        if im.size[1] != h:  # tinggi kanvas wajib tetap per gaya
            im = im.crop((0, 0, width, h)) if im.size[1] > h else _pad_h(im, h)
        path = seq_dir / f"f{i:04d}.png"
        key = hashlib.md5(im.tobytes()).digest()
        if key == last_key and last_png is not None:
            shutil.copyfile(last_png, path)  # frame diam: salin, jangan kompres ulang
        else:
            im.save(path, compress_level=1)
            last_key, last_png = key, path
    return {"pattern": str(seq_dir / "f%04d.png"), "dir": str(seq_dir), "x": 0, "y": y, "h": h, "dur": round(n / FPS, 3),
            "frames": n, "style": ctx.style, "text": spec.get("text")}


def _pad_h(im: Image.Image, h: int) -> Image.Image:
    out = Image.new("RGBA", (im.size[0], h), (0, 0, 0, 0))
    out.paste(im, (0, 0), im)
    return out


def preview_png(style: str, text: str, out_path: str, width: int = 1080, t: Optional[float] = None,
                dur: float = 3.0, background: Optional[str] = "#3a3f4b") -> str:
    """Gambar contoh satu gaya (untuk UI & cek visual)."""
    st, txt = normalize(style, text)
    ctx = make_ctx({"style": st, "text": txt}, width, dur)
    im = frame(ctx, dur * 0.6 if t is None else t)
    if background:
        bg = Image.new("RGBA", im.size, _rgba(background))
        bg.alpha_composite(im)
        im = bg
    im.save(out_path)
    return out_path


def sfx_events(style: str, dur: float) -> List[Tuple[float, str, float]]:
    """SFX pembuka sesuai gaya (dicampur di tahap sound design)."""
    name = STYLES.get(style, {}).get("sfx") or "pop"
    ev = [(0.04, name, 0.34 if name in ("impact", "type") else 0.4)]
    if style == "stamp":
        ev.append((0.47, "impact", 0.42))
    elif style == "strike":
        ev.append((0.66, "pop", 0.36))
    elif style == "versus":
        ev.append((0.34, "pop", 0.36))
    elif style == "checklist":
        ev += [(0.48 + i * 0.32, "pop", 0.3) for i in range(3)]
    return ev
