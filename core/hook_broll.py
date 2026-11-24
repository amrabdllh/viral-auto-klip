"""(v7) Hook B-roll + suara — pembuka bergaya ala klipers.pro "Hook B-roll".

Video dibekukan sebentar di awal, lalu sampul bergaya muncul: potongan orang dari klip (segmentasi
MediaPipe) di atas latar salah satu 7 gaya — Kolase Stiker, Sampul Majalah, Kertas Sobek, Pop Art Komik,
Scrapbook, Arsip Investigasi, Sampul Podcast — sementara narator (TTS) membacakan judul klip.
Durasi mengikuti narasi (maks HOOK_BROLL_MAX dtk). Frame terakhir sampul disimpan sebagai cover.

Alur di core/clipper: build() sebelum render utama (bila berhasil, hook teks & cold open dilewati),
prepend() setelah klip final jadi (video di-copy tanpa encode ulang bila bisa).
"""
from __future__ import annotations

import math
import random
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

import config
from . import hook_styles as hs

try:
    from loguru import logger
except ImportError:  # pragma: no cover
    import logging
    logger = logging.getLogger(__name__)

FPS = 30
STYLES = {
    "kolase": {"label": "Kolase Stiker", "desc": "Potongan orang bertepi putih di atas kertas sobek & film strip."},
    "majalah": {"label": "Sampul Majalah", "desc": "Masthead besar, orang di depan, judul ala cover line."},
    "sobek": {"label": "Kertas Sobek", "desc": "Foto klip robek, judul tulisan tangan di kertas."},
    "popart": {"label": "Pop Art Komik", "desc": "Titik halftone, sinar komik, judul di balon kata."},
    "scrapbook": {"label": "Scrapbook", "desc": "Polaroid, selotip, catatan tulisan tangan."},
    "investigasi": {"label": "Arsip Investigasi", "desc": "Papan gabus, foto arsip, benang merah, stempel."},
    "podcast": {"label": "Sampul Podcast", "desc": "Gradasi gelap, cincin cahaya, gelombang suara."},
}
_BY_CATEGORY = {
    "humor": ["popart", "kolase", "sobek", "scrapbook"], "shocking": ["investigasi", "sobek", "kolase", "majalah"],
    "emotional": ["scrapbook", "sobek", "podcast"], "education": ["majalah", "podcast", "investigasi"],
    "motivation": ["podcast", "majalah", "sobek"], "controversial": ["investigasi", "majalah", "popart"],
    "story": ["sobek", "scrapbook", "investigasi"], "gaming": ["kolase", "popart", "majalah"],
}
_ACCENTS = {"kolase": (212, 255, 58), "majalah": (255, 214, 0), "sobek": (230, 40, 40), "popart": (230, 30, 50),
            "scrapbook": (200, 70, 60), "investigasi": (210, 30, 30), "podcast": (124, 92, 255)}


# ============================================================================ rencana

def pick_style(clip: Dict, recent: Sequence[str] = ()) -> str:
    fixed = str(getattr(config, "HOOK_BROLL_STYLE", "auto") or "auto").lower()
    if fixed in STYLES:
        return fixed
    cat = str(clip.get("category") or "").lower()
    order = _BY_CATEGORY.get(cat, ["kolase", "majalah", "sobek", "popart", "scrapbook", "investigasi", "podcast"])
    order = order + [s for s in STYLES if s not in order]
    last = list(recent)[-2:]
    for s in order:
        if s not in last:
            return s
    return order[0]


def title_text(clip: Dict, max_words: int = 12) -> str:
    """Judul untuk dibacakan & ditampilkan (klipers memakai judul klip). Emoji/hashtag dibuang."""
    raw = str(clip.get("ai_title") or clip.get("hook_text") or clip.get("thumb_text") or "").strip()
    raw = re.sub(r"#\w+", "", raw)
    raw = "".join(ch for ch in raw if ch.isalnum() or ch.isspace() or ch in ".,!?-:%&")
    words = raw.split()
    if len(words) > max_words:  # potong di batas frasa terdekat
        cut = " ".join(words[:max_words])
        m = re.search(r"^(.{20,}?)[,.:!?](\s|$)", cut)
        raw = m.group(1) if m else cut
    else:
        raw = " ".join(words)
    return raw.strip(" ,.-:") or "Wajib tonton sampai habis"


# ============================================================================ orang (cut-out)

def grab_people(video: str, dur: float, n: int = 2, region: Optional[Tuple[float, float]] = None,
                skip: Sequence[Tuple[float, float]] = ()) -> List[Dict]:
    """Frame berisi orang paling jelas (≥3 dtk berjauhan) + potongan RGBA orangnya.

    `region` = (y0, y1) pecahan tinggi frame tempat orang dicari — di layout streamer cukup panel facecam
    (segmentasi selfie di frame gabungan ikut menandai panel gameplay sebagai 'orang'). Hanya bagian mask
    yang menyambung dengan wajah terdeteksi yang dipakai. `skip` = rentang waktu yang dihindari."""
    import cv2
    from .mediapipe_compat import FaceDetectorCompat, SelfieSegmenterCompat

    seg = SelfieSegmenterCompat()
    try:
        det = FaceDetectorCompat(min_confidence=0.5)
    except Exception:
        det = None
    cap = cv2.VideoCapture(video)
    times = [t for t in (0.6 + (max(dur - 1.2, 0.5)) * i / 9 for i in range(10))
             if not any(a <= t <= b for a, b in skip)] or [dur / 2]
    cands = []
    try:
        for t in times:
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, bgr = cap.read()
            if not ok or bgr is None:
                continue
            full = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            FH = full.shape[0]
            y0 = int(FH * region[0]) if region else 0
            y1 = int(FH * region[1]) if region else FH
            rgb = np.ascontiguousarray(full[y0:y1])
            h, w = rgb.shape[:2]
            if h < 64:
                continue
            faces = det.detect(rgb) if det is not None else []
            small = cv2.resize(rgb, (max(64, w // 2), max(64, h // 2)), interpolation=cv2.INTER_AREA)
            m = cv2.resize(seg.segment_mask(small), (w, h), interpolation=cv2.INTER_LINEAR)
            binm = (m > 0.5).astype(np.uint8)
            k, lab, stats, _ = cv2.connectedComponentsWithStats(binm, 8)
            if k <= 1:
                continue
            face = 0.0
            if faces:
                (fx, fy, fw, fh), conf = max(faces, key=lambda f: f[0][2] * f[0][3] * f[1])
                face = min(fw * fh / (w * h) / 0.02, 1.0) * conf
                # komponen yang menyentuh kotak wajah (diperbesar) = orangnya; sisanya (gameplay, kursi) dibuang
                fx0, fy0 = max(0, int(fx - fw * 0.3)), max(0, int(fy - fh * 0.3))
                fx1, fy1 = min(w, int(fx + fw * 1.3)), min(h, int(fy + fh * 1.3))
                keep = [int(v) for v in np.unique(lab[fy0:fy1, fx0:fx1]) if v]
                # tubuh di bawah wajah boleh, tapi jangan lebih dari ±4,5x tinggi wajah
                limit = np.zeros_like(binm)
                limit[max(0, int(fy - fh * 0.9)):min(h, int(fy + fh * 5.5)),
                      max(0, int(fx - fw * 2.6)):min(w, int(fx + fw * 3.6))] = 1
            else:
                areas = stats[1:, cv2.CC_STAT_AREA]
                keep = [i + 1 for i, a in enumerate(areas) if a >= 0.25 * areas.max()]
                limit = np.ones_like(binm)
            if not keep:
                continue
            mask = np.isin(lab, keep).astype(np.float32) * limit * np.clip((m - 0.3) / 0.35, 0, 1)
            ratio = float(mask.mean())
            if ratio < 0.015 or (ratio > 0.8 and not faces):
                continue
            sharp = float(cv2.Laplacian(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var())
            score = min(ratio / 0.12, 1.0) * 2 + face * 2 + min(sharp / 400, 1.0)
            cands.append({"t": t, "rgb": rgb, "full": full, "mask": mask, "score": score, "ratio": ratio})
    finally:
        cap.release()
        try:
            seg.close()
        except Exception:
            pass
    picked: List[Dict] = []
    for c in sorted(cands, key=lambda c: -c["score"]):
        if all(abs(c["t"] - p["t"]) >= 3.0 for p in picked):
            picked.append(c)
        if len(picked) >= n:
            break
    out = []
    for c in picked:
        alpha = Image.fromarray((np.clip(c["mask"], 0, 1) * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.2))
        img = Image.fromarray(c["rgb"]).convert("RGBA")
        img.putalpha(alpha)
        bbox = alpha.point(lambda v: 255 if v > 40 else 0).getbbox()
        if not bbox:
            continue
        x0, y0, x1, y1 = bbox
        pad = 6
        cut = img.crop((max(0, x0 - pad), max(0, y0 - pad), min(img.width, x1 + pad), min(img.height, y1 + pad)))
        out.append({"t": round(c["t"], 2), "cut": cut, "frame": Image.fromarray(c["full"]),
                    "box": [round(x0 / img.width, 4), round(y0 / img.height, 4),
                            round((x1 - x0) / img.width, 4), round((y1 - y0) / img.height, 4)]})
    return out


def frame_at(video: str, t: float) -> Optional[Image.Image]:
    import cv2
    cap = cv2.VideoCapture(video)
    try:
        cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t) * 1000)
        ok, bgr = cap.read()
        return Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)) if ok and bgr is not None else None
    finally:
        cap.release()


# ============================================================================ primitif gambar

def _font(path_cands: Sequence[str], size: int, kind: str = "black") -> ImageFont.FreeTypeFont:
    for p in path_cands:
        pp = Path(p) if Path(p).is_absolute() else Path(config.FONT_DIR) / p
        if pp.exists():
            try:
                return ImageFont.truetype(str(pp), size)
            except OSError:
                continue
    return hs.font(kind, size)


HAND = ["Inkfree.ttf", "C:/Windows/Fonts/Inkfree.ttf", "C:/Windows/Fonts/segoeprb.ttf"]
PRINT = ["C:/Windows/Fonts/segoeprb.ttf", "C:/Windows/Fonts/Inkfree.ttf"]
COMIC = ["Bangers-Regular.ttf", "C:/Windows/Fonts/comicbd.ttf", "C:/Windows/Fonts/impact.ttf"]
TYPE = ["C:/Windows/Fonts/courbd.ttf", "C:/Windows/Fonts/consolab.ttf"]
IMPACT = ["Anton-Regular.ttf", "C:/Windows/Fonts/impact.ttf"]


def cover(img: Image.Image, W: int, H: int) -> Image.Image:
    return ImageOps.fit(img.convert("RGB"), (W, H), Image.LANCZOS)


def sticker(cut: Image.Image, border: int, color=(255, 255, 255), shadow: int = 0, outline: Optional[Tuple] = None,
            outline_w: int = 0) -> Image.Image:
    """Potongan RGBA → stiker: tepi warna (dilasi alpha) + bayangan lembut."""
    pad = border + outline_w + shadow * 2 + 8
    base = Image.new("RGBA", (cut.width + 2 * pad, cut.height + 2 * pad), (0, 0, 0, 0))
    base.paste(cut, (pad, pad), cut)
    a = base.getchannel("A")
    layers = []
    if outline_w:
        o = a.filter(ImageFilter.MaxFilter(_odd(2 * outline_w + 1))).filter(ImageFilter.GaussianBlur(0.8))
        layers.append((o, outline))
    if border:
        b = a.filter(ImageFilter.MaxFilter(_odd(2 * border + 1))).filter(ImageFilter.GaussianBlur(1.0))
        layers.append((b, color))
    out = Image.new("RGBA", base.size, (0, 0, 0, 0))
    if shadow:
        sa = (layers[0][0] if layers else a).filter(ImageFilter.GaussianBlur(shadow))
        sh = Image.new("RGBA", base.size, (0, 0, 0, 0))
        sh.putalpha(sa.point(lambda v: int(v * 0.55)))
        out.alpha_composite(sh, (int(shadow * 0.4), int(shadow * 0.8)))
    for m, col in layers:
        lay = Image.new("RGBA", base.size, tuple(col[:3]) + (255,))
        lay.putalpha(m)
        out.alpha_composite(lay)
    out.alpha_composite(base)
    return out


def _odd(n: int) -> int:
    n = max(3, int(n))
    # MaxFilter besar mahal: batasi lalu ulang
    return n if n % 2 else n + 1


def fit_h(img: Image.Image, h: float) -> Image.Image:
    s = h / img.height
    return img.resize((max(1, int(img.width * s)), max(1, int(img.height * s))), Image.LANCZOS)


def fit_box(img: Image.Image, w: float, h: float) -> Image.Image:
    s = min(w / img.width, h / img.height)
    return img.resize((max(1, int(img.width * s)), max(1, int(img.height * s))), Image.LANCZOS)


def torn_strip(w: int, h: int, color, rng: random.Random, jag: float = 18, alpha: int = 255) -> Image.Image:
    """Pita kertas dengan tepi atas & bawah robek."""
    img = Image.new("RGBA", (w, h + int(jag * 2)), (0, 0, 0, 0))
    pts_top = [(0, jag)]
    x = 0
    while x < w:
        x += rng.uniform(10, 28)
        pts_top.append((min(x, w), jag + rng.uniform(-jag, jag)))
    pts_bot = []
    x = w
    while x > 0:
        x -= rng.uniform(10, 28)
        pts_bot.append((max(x, 0), jag + h + rng.uniform(-jag, jag)))
    ImageDraw.Draw(img).polygon(pts_top + pts_bot, fill=tuple(color[:3]) + (alpha,))
    return _grain(img, 10, rng)


def _grain(img: Image.Image, amount: int, rng: random.Random) -> Image.Image:
    arr = np.asarray(img).astype(np.int16)
    noise = np.random.default_rng(rng.randint(0, 10 ** 6)).integers(-amount, amount + 1, arr.shape[:2])
    for ch in range(3):
        arr[..., ch] = np.clip(arr[..., ch] + noise, 0, 255)
    return Image.fromarray(arr.astype(np.uint8), img.mode)


def polaroid(img: Image.Image, w: int, rng: random.Random, gray: bool = False) -> Image.Image:
    ph = int(w * 0.92)
    inner = ImageOps.fit(img.convert("RGB"), (w - 40, ph - 40), Image.LANCZOS)
    if gray:
        inner = ImageEnhance.Contrast(ImageOps.grayscale(inner).convert("RGB")).enhance(1.15)
        inner = Image.blend(inner, Image.new("RGB", inner.size, (190, 170, 130)), 0.18)
    card = Image.new("RGBA", (w, ph + 90), (250, 248, 242, 255))
    card.paste(inner, (20, 20))
    return _shadowed(card, 14)


def _shadowed(img: Image.Image, blur: int) -> Image.Image:
    pad = blur * 3
    out = Image.new("RGBA", (img.width + 2 * pad, img.height + 2 * pad), (0, 0, 0, 0))
    sh = Image.new("RGBA", out.size, (0, 0, 0, 0))
    m = Image.new("L", out.size, 0)
    m.paste(img.getchannel("A"), (pad + blur // 2, pad + blur))
    sh.putalpha(m.filter(ImageFilter.GaussianBlur(blur)).point(lambda v: int(v * 0.6)))
    out.alpha_composite(sh)
    out.alpha_composite(img, (pad, pad))
    return out


def tape(w: int, h: int, rng: random.Random) -> Image.Image:
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pts = [(0, rng.uniform(0, 4)), (w, rng.uniform(0, 4)), (w - rng.uniform(0, 6), h), (rng.uniform(0, 6), h)]
    d.polygon(pts, fill=(240, 228, 190, 170))
    return img


def halftone(W: int, H: int, bg, dot, step: int, max_r: float, fade_from_top: bool = True) -> Image.Image:
    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)
    for yi, y in enumerate(range(0, H + step, step)):
        k = y / H if fade_from_top else 1 - y / H
        r = max_r * (0.25 + 0.75 * k)
        off = step / 2 if yi % 2 else 0
        for x in range(0, W + step, step):
            d.ellipse((x + off - r, y - r, x + off + r, y + r), fill=dot)
    return img


def burst(W: int, H: int, cx: float, cy: float, n: int, c1, c2) -> Image.Image:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    R = math.hypot(W, H)
    for i in range(n):
        a0, a1 = 2 * math.pi * i / n, 2 * math.pi * (i + 0.5) / n
        d.polygon([(cx, cy), (cx + R * math.cos(a0), cy + R * math.sin(a0)), (cx + R * math.cos(a1), cy + R * math.sin(a1))],
                  fill=c1 if i % 2 == 0 else c2)
    return img


def vignette(img: Image.Image, strength: float = 0.55) -> Image.Image:
    W, H = img.size
    yy, xx = np.mgrid[0:H, 0:W]
    r = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2)
    k = 1 - strength * np.clip(r - 0.35, 0, 1)
    arr = np.asarray(img.convert("RGB")).astype(np.float32) * k[..., None]
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def text_sprite(text: str, kind_or_font, W: int, max_lines: int, size: int, *, color=(255, 255, 255), hl=(255, 214, 0),
                stroke: int = 0, stroke_fill=(0, 0, 0), upper: bool = True, align: str = "center", lh: float = 1.08,
                min_size: int = 40, font_paths: Optional[Sequence[str]] = None, underline=None, pad: int = 24) -> Image.Image:
    """Teks bermarkup (*sorot*) → sprite RGBA ketat."""
    marked = text if "*" in text else hs._auto_highlight(text)
    toks = hs.tokens(marked)
    if upper:
        toks = hs.upper(toks)
    if font_paths:
        s = size
        while True:
            f = _font(font_paths, s)
            lines = hs._wrap(toks, f, W - 2 * pad)
            if (len(lines) <= max_lines and all(f.getlength(w) <= W - 2 * pad for w, _ in toks)) or s <= min_size:
                break
            s = int(s * 0.92)
    else:
        f, lines = hs.fit(toks, kind_or_font, W - 2 * pad, max_lines, size, min_size)
    hgt = int(hs.block_h(len(lines), f, lh=lh, caps=upper) + 2 * pad + stroke * 2 + f.size * 0.25)
    img = Image.new("RGBA", (W, hgt), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    hs.draw_lines(d, lines, f, (pad, W - 2 * pad, 0), pad + stroke, color=tuple(color) + (255,) if len(color) == 3 else color,
                  hl=tuple(hl) + (255,) if hl and len(hl) == 3 else hl, align=align, lh=lh, stroke=stroke,
                  stroke_fill=tuple(stroke_fill) + (255,), underline=underline)
    bb = img.getbbox()
    return img.crop((max(0, bb[0] - 6), max(0, bb[1] - 6), min(W, bb[2] + 6), min(hgt, bb[3] + 6))) if bb else img


# ============================================================================ tata letak per gaya

class Scene:
    """Latar statis (di-zoom pelan) + sprite yang muncul bertahap (pop/drop/slide/stamp) + lapisan dinamis."""

    def __init__(self, W: int, H: int, bg: Image.Image):
        self.W, self.H = W, H
        self.bg = bg.convert("RGB")
        self.sprites: List[Dict] = []
        self.dynamic = None  # fn(img, t)

    def add(self, img: Image.Image, cx: float, cy: float, t_in: float, kind: str = "pop", rot: float = 0.0) -> None:
        if rot:
            img = img.rotate(rot, resample=Image.BICUBIC, expand=True)
        self.sprites.append({"img": img, "cx": cx, "cy": cy, "t": t_in, "kind": kind})

    def render(self, t: float, zoom: float = 1.0) -> Image.Image:
        W, H = self.W, self.H
        if zoom > 1.0005:
            cw, ch = W / zoom, H / zoom
            x0, y0 = (W - cw) / 2, (H - ch) / 2
            frame = self.bg.resize((W, H), Image.BILINEAR, box=(x0, y0, x0 + cw, y0 + ch))
        else:
            frame = self.bg.copy()
        frame = frame.convert("RGBA")
        for sp in self.sprites:
            p = hs.prog(t, sp["t"], sp["t"] + 0.38)
            if p <= 0:
                continue
            img = sp["img"]
            cx, cy = sp["cx"], sp["cy"]
            if sp["kind"] == "pop":
                s = hs.ease_back(p) if p < 1 else 1.0
                s = max(0.05, s)
                if abs(s - 1) > 0.01:
                    img = img.resize((max(1, int(img.width * s)), max(1, int(img.height * s))), Image.BILINEAR)
            elif sp["kind"] == "stamp":
                s = 1 + 0.9 * (1 - hs.ease_out(p))
                if abs(s - 1) > 0.01:
                    img = img.resize((max(1, int(img.width * s)), max(1, int(img.height * s))), Image.BILINEAR)
                if p < 1:
                    img = hs.transform(img, alpha=min(1.0, p * 1.6))
            elif sp["kind"] == "drop":
                cy = cy - (1 - hs.ease_back(p)) * H * 0.12 if p < 1 else cy
            elif sp["kind"] == "slide":
                cx = cx - (1 - hs.ease_out(p)) * W * 0.6 if p < 1 else cx
            elif sp["kind"] == "fade" and p < 1:
                img = hs.transform(img, alpha=p)
            frame.alpha_composite(img, (int(cx - img.width / 2), int(cy - img.height / 2)))
        if self.dynamic:
            self.dynamic(frame, t)
        return frame.convert("RGB")


def build_scene(style: str, title: str, people: List[Dict], still: Image.Image, W: int, H: int, seed: int = 7,
                label: str = "") -> Scene:
    rng = random.Random(seed)
    u = W / 1080.0
    acc = _ACCENTS.get(style, (255, 214, 0))
    cuts = [p["cut"] for p in people]
    frames = [p["frame"] for p in people] or [still]
    t0 = 0.32  # sampul muncul setelah freeze singkat

    if style == "kolase":
        bg = cover(still, W, H).filter(ImageFilter.GaussianBlur(22 * u))
        bg = ImageEnhance.Brightness(bg).enhance(0.5)
        bg = Image.blend(bg, Image.new("RGB", (W, H), (30, 20, 50)), 0.25).convert("RGBA")
        for y, rot, col in ((0.30, -5, (245, 238, 222)), (0.63, 4, (255, 255, 255))):
            strip = torn_strip(int(W * 1.3), int(260 * u), col, rng, jag=16 * u).rotate(rot, expand=True, resample=Image.BICUBIC)
            bg.alpha_composite(strip, (int(W / 2 - strip.width / 2), int(H * y - strip.height / 2)))
        film = _film_strip(frames, int(W * 1.25), int(230 * u), rng)
        film = film.rotate(-6, expand=True, resample=Image.BICUBIC)
        bg.alpha_composite(film, (int(W / 2 - film.width / 2), int(H * 0.86 - film.height / 2)))
        sc = Scene(W, H, vignette(bg, 0.35))
        if cuts:
            big = sticker(fit_box(cuts[0], W * 0.72, H * 0.44), int(16 * u), shadow=int(14 * u))
            sc.add(big, W * (0.40 if len(cuts) > 1 else 0.5), H * 0.47, t0 + 0.05, "pop", rot=-4)
        if len(cuts) > 1:
            sm = sticker(fit_box(cuts[1], W * 0.5, H * 0.3), int(14 * u), shadow=int(12 * u))
            sc.add(sm, W * 0.70, H * 0.68, t0 + 0.18, "pop", rot=6)
        ttl = text_sprite(title, "black", int(W * 0.92), 3, int(112 * u), color=(212, 255, 58), hl=(255, 255, 255),
                          stroke=int(9 * u), min_size=int(56 * u))
        sc.add(ttl, W / 2, H * 0.14 + ttl.height / 2 * 0.2, t0 + 0.32, "pop", rot=-3)
        return sc

    if style == "majalah":
        col = _dominant(still)
        bg = Image.new("RGB", (W, H), col)
        g = Image.linear_gradient("L").resize((W, H))
        bg = Image.composite(Image.new("RGB", (W, H), tuple(int(c * 0.55) for c in col)), bg, g).convert("RGBA")
        mast = text_sprite("VIRAL", "serif", W, 1, int(330 * u), color=(255, 255, 255), hl=None, upper=True,
                           min_size=int(200 * u), pad=10)
        bg.alpha_composite(mast, (int(W / 2 - mast.width / 2), int(60 * u)))
        d = ImageDraw.Draw(bg)
        f = hs.font("bold", int(30 * u))
        tag = f"EDISI KHUSUS  •  {label.upper()}" if label else "EDISI KHUSUS"
        d.text((W / 2, 60 * u + mast.height + 18 * u), tag, font=f, fill=(255, 255, 255), anchor="mt")
        _barcode(d, W - 250 * u, H - 220 * u, 190 * u, 120 * u)
        sc = Scene(W, H, bg)
        if cuts:
            p = fit_box(cuts[0], W * 0.8, H * 0.55)
            sc.add(_shadowed(p, int(18 * u)), W - p.width / 2 + 30 * u, H - p.height / 2 - 10 * u, t0 + 0.05, "drop")
        ttl = text_sprite(title, "black", int(W * 0.62), 5, int(92 * u), color=(255, 255, 255), hl=acc, stroke=int(5 * u),
                          align="left", min_size=int(46 * u))
        sc.add(ttl, 40 * u + ttl.width / 2, H * 0.58 + ttl.height / 2 * 0.1, t0 + 0.3, "slide")
        badge = _badge("BARU!", int(190 * u), (230, 30, 40))
        sc.add(badge, W - 140 * u, H * 0.30, t0 + 0.5, "stamp", rot=12)
        return sc

    if style == "sobek":
        bg = Image.new("RGB", (W, H), (244, 236, 220))
        top = cover(frames[0], W, int(H * 0.6))
        top = ImageEnhance.Brightness(top).enhance(0.85)
        bg.paste(top, (0, 0))
        bg = _grain(bg.convert("RGBA"), 6, rng)
        shadow = torn_strip(int(W * 1.2), int(H * 0.5), (0, 0, 0), rng, jag=22 * u, alpha=90).filter(ImageFilter.GaussianBlur(8))
        bg.alpha_composite(shadow, (int(-W * 0.1), int(H * 0.52)))
        paper = torn_strip(int(W * 1.2), int(H * 0.5), (246, 239, 224), rng, jag=22 * u)
        bg.alpha_composite(paper, (int(-W * 0.1), int(H * 0.535)))
        d = ImageDraw.Draw(bg)
        for i in range(9):  # garis buku tulis tipis
            y = H * 0.66 + i * 92 * u
            d.line((60 * u, y, W - 60 * u, y), fill=(180, 200, 225, 120), width=max(1, int(2 * u)))
        sc = Scene(W, H, bg)
        if cuts:
            p = sticker(fit_box(cuts[0], W * 0.56, H * 0.26), int(12 * u), shadow=int(12 * u))
            sc.add(p, W * 0.66, H * 0.47, t0 + 0.05, "pop", rot=3)
        ttl = text_sprite(title, None, int(W * 0.86), 4, int(104 * u), color=(25, 25, 30), hl=(210, 30, 30), upper=False,
                          font_paths=HAND, min_size=int(52 * u), underline=(210, 30, 30, 255), lh=1.18)
        sc.add(ttl, W / 2, H * 0.66 + ttl.height / 2 + 30 * u, t0 + 0.3, "fade", rot=-2)
        return sc

    if style == "popart":
        bg = halftone(W, H, (255, 212, 0), (235, 60, 40), int(34 * u), 11 * u).convert("RGBA")
        bg.alpha_composite(burst(W, H, W / 2, H * 0.58, 22, (255, 240, 120, 150), (255, 140, 0, 110)))
        sc = Scene(W, H, bg)
        if cuts:
            pc = fit_box(cuts[0], W * 0.86, H * 0.5)
            rgb = ImageOps.posterize(pc.convert("RGB"), 3)
            rgb = ImageEnhance.Color(rgb).enhance(1.6)
            rgb = ImageEnhance.Contrast(rgb).enhance(1.25)
            pc2 = rgb.convert("RGBA")
            pc2.putalpha(pc.getchannel("A"))
            st = sticker(pc2, int(8 * u), color=(255, 255, 255), outline=(10, 10, 10), outline_w=int(9 * u), shadow=0)
            sc.add(st, W / 2, H - st.height / 2 + 20 * u, t0 + 0.05, "drop")
        ttl = text_sprite(title, None, int(W * 0.68), 4, int(92 * u), color=(15, 15, 15), hl=(225, 20, 40),
                          font_paths=COMIC, min_size=int(48 * u))
        balloon = _balloon(ttl, int(26 * u))
        sc.add(balloon, W / 2, 90 * u + balloon.height / 2, t0 + 0.3, "pop", rot=-2)
        star = _starburst(int(200 * u), (255, 255, 255), (15, 15, 15), "!")
        sc.add(star, W - 150 * u, H * 0.44, t0 + 0.55, "stamp", rot=10)
        return sc

    if style == "scrapbook":
        bg = Image.new("RGB", (W, H), (196, 160, 118))
        bg = _grain(bg.convert("RGBA"), 14, rng)
        d = ImageDraw.Draw(bg)
        for x in range(0, W, int(54 * u)):
            d.line((x, 0, x, H), fill=(170, 135, 95, 60), width=1)
        for y in range(0, H, int(54 * u)):
            d.line((0, y, W, y), fill=(170, 135, 95, 60), width=1)
        sc = Scene(W, H, bg)
        pics = frames[:2] if len(frames) >= 2 else frames * 2
        for i, (fr, cx, cy, rot) in enumerate(((pics[0], 0.33, 0.47, -7), (pics[1], 0.70, 0.62, 6))):
            pol = polaroid(fr, int(470 * u), rng)
            tp = tape(int(170 * u), int(48 * u), rng)
            pol.alpha_composite(tp, (pol.width // 2 - tp.width // 2, int(16 * u)))
            sc.add(pol, W * cx, H * cy, t0 + 0.05 + 0.12 * i, "drop", rot=rot)
        if cuts:
            p = sticker(fit_box(cuts[0], W * 0.52, H * 0.26), int(14 * u), shadow=int(10 * u))
            sc.add(p, W * 0.32, H * 0.84, t0 + 0.34, "pop", rot=-5)
        note = _note_paper(title, int(W * 0.84), u)
        sc.add(note, W / 2, H * 0.16 + note.height * 0.1, t0 + 0.24, "pop", rot=-2)
        return sc

    if style == "investigasi":
        bg = Image.new("RGB", (W, H), (120, 82, 48))
        bg = _grain(bg.convert("RGBA"), 22, rng).filter(ImageFilter.GaussianBlur(0.6))
        bg = vignette(bg, 0.75).convert("RGBA")
        sc = Scene(W, H, bg)
        spots = [(0.30, 0.40, -6), (0.72, 0.52, 5), (0.40, 0.74, 3)]
        pics = (frames + frames + frames)[:3]
        pins = []
        for i, ((cx, cy, rot), fr) in enumerate(zip(spots, pics)):
            pol = polaroid(fr, int(430 * u), rng, gray=True)
            sc.add(pol, W * cx, H * cy, t0 + 0.05 + 0.1 * i, "drop", rot=rot)
            pins.append((W * cx, H * cy - pol.height * 0.42))

        def strings(img, t, pins=pins):
            p = hs.prog(t, t0 + 0.4, t0 + 0.9)
            if p <= 0:
                return
            d = ImageDraw.Draw(img)
            for a, b in zip(pins, pins[1:] + pins[:1]):
                d.line((a[0], a[1], a[0] + (b[0] - a[0]) * p, a[1] + (b[1] - a[1]) * p), fill=(200, 20, 30, 255),
                       width=max(2, int(5 * u)))
            for x, y in pins:
                d.ellipse((x - 14 * u, y - 14 * u, x + 14 * u, y + 14 * u), fill=(220, 30, 40, 255), outline=(90, 0, 0, 255))
        sc.dynamic = strings
        if cuts:
            p = sticker(fit_box(cuts[0], W * 0.46, H * 0.24), int(10 * u), shadow=int(10 * u))
            sc.add(p, W * 0.74, H * 0.86, t0 + 0.5, "pop", rot=-3)
        lab = _label_strip(title, int(W * 0.9), u)
        sc.add(lab, W / 2, H * 0.13, t0 + 0.25, "slide", rot=-1)
        stamp = _stamp("RAHASIA", int(380 * u), (200, 20, 30))
        sc.add(stamp, W * 0.66, H * 0.30, t0 + 0.75, "stamp", rot=-14)
        return sc

    # podcast
    base = tuple(int(c * 0.35) for c in acc)
    bg = Image.new("RGB", (W, H), (12, 12, 18))
    glow = Image.new("RGB", (W, H), base)
    m = Image.new("L", (W, H), 0)
    ImageDraw.Draw(m).ellipse((W * -0.2, H * 0.2, W * 1.2, H * 0.95), fill=255)
    bg = Image.composite(glow, bg, m.filter(ImageFilter.GaussianBlur(160 * u))).convert("RGBA")
    d = ImageDraw.Draw(bg)
    cx, cy, R = W / 2, H * 0.56, W * 0.40
    for k, a in ((0, 255), (18, 90), (40, 40)):
        d.ellipse((cx - R - k * u, cy - R - k * u, cx + R + k * u, cy + R + k * u), outline=acc + (a,), width=int(10 * u))
    sc = Scene(W, H, bg)
    if cuts:
        p = fit_box(cuts[0], W * 0.86, H * 0.5)
        sc.add(_shadowed(p, int(20 * u)), W / 2, H * 0.86 - p.height / 2 + 40 * u, t0 + 0.05, "drop")
    chip = _chip("SOROTAN", int(30 * u), acc)
    sc.add(chip, W / 2, H * 0.075, t0 + 0.2, "pop")
    ttl = text_sprite(title, "black", int(W * 0.9), 3, int(96 * u), color=(255, 255, 255), hl=acc, stroke=0,
                      min_size=int(48 * u))
    sc.add(ttl, W / 2, H * 0.10 + ttl.height / 2 + 30 * u, t0 + 0.3, "pop")

    def wave(img, t):
        d2 = ImageDraw.Draw(img)
        n, bw = 36, W * 0.8 / 36
        for i in range(n):
            h = (0.25 + 0.75 * abs(math.sin(t * 7 + i * 0.7) * math.sin(t * 3.1 + i * 0.33))) * 90 * u
            x = W * 0.1 + i * bw
            d2.rounded_rectangle((x, H * 0.93 - h / 2, x + bw * 0.6, H * 0.93 + h / 2), radius=int(bw * 0.3),
                                 fill=acc + (230,))
    sc.dynamic = wave
    return sc


def _dominant(img: Image.Image) -> Tuple[int, int, int]:
    small = img.convert("RGB").resize((48, 48))
    arr = np.asarray(small).reshape(-1, 3).astype(np.float32)
    sat = arr.max(axis=1) - arr.min(axis=1)
    top = arr[np.argsort(-sat)[: len(arr) // 5]].mean(axis=0)
    hsv = Image.fromarray(np.uint8([[top]])).convert("HSV")
    h, s, v = np.asarray(hsv)[0, 0]
    rgb = Image.fromarray(np.uint8([[[h, max(int(s), 170), 205]]]), "HSV").convert("RGB")
    return tuple(int(c) for c in np.asarray(rgb)[0, 0])


def _film_strip(frames: List[Image.Image], w: int, h: int, rng: random.Random) -> Image.Image:
    img = Image.new("RGBA", (w, h), (14, 14, 16, 255))
    d = ImageDraw.Draw(img)
    hole_w, hole_h = h * 0.08, h * 0.1
    x = 10
    while x < w:
        d.rounded_rectangle((x, h * 0.05, x + hole_w, h * 0.05 + hole_h), radius=3, fill=(235, 235, 235, 255))
        d.rounded_rectangle((x, h * 0.85, x + hole_w, h * 0.85 + hole_h), radius=3, fill=(235, 235, 235, 255))
        x += hole_w * 2.2
    fw, fh = int(h * 0.62 * 16 / 9), int(h * 0.62)
    x = int(w * 0.08)
    i = 0
    while x + fw < w:
        fr = ImageOps.fit(frames[i % len(frames)].convert("RGB"), (fw, fh), Image.LANCZOS)
        img.paste(fr, (x, int(h * 0.19)))
        x += fw + int(h * 0.1)
        i += 1
    return img


def _barcode(d: ImageDraw.ImageDraw, x: float, y: float, w: float, h: float) -> None:
    d.rectangle((x - 10, y - 10, x + w + 10, y + h + 34), fill=(255, 255, 255))
    rng = random.Random(3)
    cx = x
    while cx < x + w:
        bw = rng.choice((2, 3, 5))
        d.rectangle((cx, y, cx + bw - 1, y + h), fill=(0, 0, 0))
        cx += bw + rng.choice((2, 3, 4))
    f = hs.font("mono", 22)
    d.text((x + w / 2, y + h + 6), "9 771234 567003", font=f, fill=(0, 0, 0), anchor="mt")


def _badge(text: str, size: int, color) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pts = []
    for i in range(32):
        r = size / 2 * (1.0 if i % 2 == 0 else 0.86)
        a = math.pi * 2 * i / 32
        pts.append((size / 2 + r * math.cos(a), size / 2 + r * math.sin(a)))
    d.polygon(pts, fill=tuple(color) + (255,))
    f = hs.font("black", int(size * 0.22))
    d.text((size / 2, size / 2), text, font=f, fill=(255, 255, 255), anchor="mm")
    return img


def _starburst(size: int, fill, line, text: str) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pts = []
    rng = random.Random(5)
    for i in range(22):
        r = size / 2 * (0.98 if i % 2 == 0 else rng.uniform(0.55, 0.68))
        a = math.pi * 2 * i / 22
        pts.append((size / 2 + r * math.cos(a), size / 2 + r * math.sin(a)))
    d.polygon(pts, fill=tuple(fill) + (255,), outline=tuple(line) + (255,), width=max(3, size // 40))
    f = _font(COMIC, int(size * 0.5))
    d.text((size / 2, size / 2), text, font=f, fill=(225, 20, 40, 255), anchor="mm", stroke_width=max(2, size // 50),
           stroke_fill=(15, 15, 15, 255))
    return img


def _balloon(content: Image.Image, pad: int) -> Image.Image:
    # persegi teks harus muat di DALAM elips → sumbu elips ±1,4x ukuran teks
    w, h = int(content.width * 1.32 + pad * 2), int(content.height * 1.42 + pad * 2)
    img = Image.new("RGBA", (w + 20, h + pad * 3), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    lw = max(4, pad // 4)
    tail = [(w * 0.62, h - pad), (w * 0.78, h + pad * 2.6), (w * 0.48, h - pad * 0.6)]
    d.polygon(tail, fill=(255, 255, 255, 255), outline=(15, 15, 15, 255))
    d.ellipse((lw, lw, w - lw, h - lw), fill=(255, 255, 255, 255), outline=(15, 15, 15, 255), width=lw)
    d.polygon([(w * 0.6, h - pad * 1.6), (w * 0.77, h + pad * 2.2), (w * 0.5, h - pad * 1.2)], fill=(255, 255, 255, 255))
    d.line([(w * 0.62, h - lw * 1.2), (w * 0.78, h + pad * 2.6), (w * 0.5, h - lw * 1.6)], fill=(15, 15, 15, 255), width=lw)
    img.alpha_composite(content, (int(w / 2 - content.width / 2), int(h / 2 - content.height / 2)))
    return img


def _note_paper(title: str, w: int, u: float) -> Image.Image:
    ttl = text_sprite(title, None, w - int(100 * u), 4, int(84 * u), color=(70, 40, 25), hl=(200, 40, 40), upper=False,
                      font_paths=PRINT, min_size=int(44 * u), lh=1.22)
    h = ttl.height + int(110 * u)
    img = Image.new("RGBA", (w, h), (252, 250, 240, 255))
    d = ImageDraw.Draw(img)
    for y in range(int(70 * u), h, int(52 * u)):
        d.line((0, y, w, y), fill=(150, 185, 225, 255), width=max(1, int(2 * u)))
    d.line((70 * u, 0, 70 * u, h), fill=(230, 110, 110, 255), width=max(2, int(3 * u)))
    img.alpha_composite(ttl, (int(w / 2 - ttl.width / 2 + 20 * u), int(h / 2 - ttl.height / 2)))
    rng = random.Random(9)
    tp = tape(int(200 * u), int(54 * u), rng)
    img.alpha_composite(tp, (int(w / 2 - tp.width / 2), 0))
    return _shadowed(img, int(12 * u))


def _label_strip(title: str, w: int, u: float) -> Image.Image:
    ttl = text_sprite(title, None, w - int(80 * u), 3, int(70 * u), color=(20, 20, 20), hl=(190, 20, 30),
                      font_paths=TYPE, min_size=int(38 * u), lh=1.15)
    h = ttl.height + int(70 * u)
    img = Image.new("RGBA", (w, h), (236, 222, 180, 255))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, w - 1, h - 1), outline=(120, 95, 55, 255), width=max(2, int(3 * u)))
    f = hs.font("mono", int(24 * u))
    d.text((24 * u, 12 * u), "BERKAS NO. 07", font=f, fill=(120, 30, 30, 255))
    img.alpha_composite(ttl, (int(w / 2 - ttl.width / 2), int(h / 2 - ttl.height / 2 + 12 * u)))
    return _shadowed(img, int(12 * u))


def _stamp(text: str, w: int, color) -> Image.Image:
    f = hs.font("black", int(w * 0.2))
    tw = f.getlength(text)
    h = int(w * 0.34)
    img = Image.new("RGBA", (int(max(w, tw + w * 0.16)), h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    lw = max(4, w // 45)
    d.rounded_rectangle((lw, lw, img.width - lw, h - lw), radius=lw * 2, outline=tuple(color) + (230,), width=lw)
    d.text((img.width / 2, h / 2), text, font=f, fill=tuple(color) + (230,), anchor="mm")
    arr = np.asarray(img).copy()  # tinta tidak rata
    rng = np.random.default_rng(4)
    holes = rng.random(arr.shape[:2]) < 0.12
    arr[..., 3][holes] = (arr[..., 3][holes] * 0.3).astype(np.uint8)
    return Image.fromarray(arr)


def _chip(text: str, size: int, color) -> Image.Image:
    f = hs.font("black", size)
    tw = f.getlength(text)
    w, h = int(tw + size * 2.6), int(size * 2)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, w - 1, h - 1), radius=h // 2, fill=tuple(color) + (255,))
    d.ellipse((size * 0.7, h / 2 - size * 0.3, size * 1.3, h / 2 + size * 0.3), fill=(255, 60, 60, 255))
    d.text((size * 1.6, h / 2), text, font=f, fill=(255, 255, 255, 255), anchor="lm")
    return img


# ============================================================================ narasi & audio

def narrate(text: str, out_dir: Path, max_sec: float) -> Optional[Dict]:
    """TTS judul. Terlalu panjang → dibaca lebih cepat (maks 1,25×)."""
    from . import media_ai
    voice = str(getattr(config, "HOOK_BROLL_VOICE", "") or "")
    out = str(Path(out_dir) / "hook_voice.mp3")
    try:
        res = media_ai.tts(text, out, voice=voice)
        budget = max_sec - 0.9
        if res["duration"] > budget:
            speed = min(1.25, res["duration"] / budget * 1.02)
            res = media_ai.tts(text, out, voice=voice, speed=speed)
        return res
    except Exception as exc:
        logger.warning(f"Narasi hook B-roll gagal: {str(exc)[:200]}")
        return None


def mix_audio(voice: Optional[str], dur: float, out_wav: str, voice_at: float = 0.38) -> None:
    from . import editing
    inputs, parts, labels = [], [], []
    k = 0
    for name, at, vol in (("whoosh", 0.18, 0.5), ("impact", voice_at - 0.04, 0.35)):
        try:
            p = editing.sfx_path(name)
        except Exception:
            continue
        if not Path(p).exists():
            continue
        inputs += ["-i", str(p)]
        parts.append(f"[{k}:a]aresample=48000,aformat=channel_layouts=stereo,volume={vol},adelay={int(at * 1000)}|{int(at * 1000)}[s{k}]")
        labels.append(f"[s{k}]")
        k += 1
    if voice and Path(voice).exists():
        inputs += ["-i", voice]
        parts.append(f"[{k}:a]aresample=48000,aformat=channel_layouts=stereo,volume=1.6,"
                     f"adelay={int(voice_at * 1000)}|{int(voice_at * 1000)}[s{k}]")
        labels.append(f"[s{k}]")
        k += 1
    if not labels:
        subprocess.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                        "-t", f"{dur:.3f}", out_wav], check=True, capture_output=True)
        return
    graph = ";".join(parts) + f";{''.join(labels)}amix=inputs={len(labels)}:normalize=0:dropout_transition=0," \
                              f"apad=whole_dur={dur:.3f},atrim=duration={dur:.3f}," \
                              "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[a]"  # setara mastering klip (-14 LUFS)
    # -t + timeout = pengaman: versi lama (apad tanpa whole_dur) pernah menulis WAV tanpa akhir sampai 140 GB
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", *inputs, "-filter_complex", graph, "-map", "[a]",
                    "-ar", "48000", "-ac", "2", "-t", f"{dur:.3f}", out_wav], check=True, capture_output=True, timeout=180)


# ============================================================================ render intro

def render_video(scene: Scene, still: Image.Image, dur: float, out_path: str, encode_args: List[str],
                 cover_path: Optional[str] = None, freeze: float = 0.32) -> int:
    """Frame demi frame → ffmpeg (rawvideo stdin). Return jumlah frame."""
    W, H = scene.W, scene.H
    n = int(round(dur * FPS))
    first = cover(still, W, H)
    import tempfile
    # stderr ke file (bukan PIPE): pipe stderr yang penuh sementara kita menulis stdin = deadlock
    errf = tempfile.TemporaryFile()
    proc = subprocess.Popen(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-hide_banner", "-f", "rawvideo",
                             "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", *encode_args, "-an",
                             "-movflags", "+faststart", out_path],
                            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=errf)
    cov = None
    try:
        for i in range(n):
            t = i / FPS
            if t < freeze:  # freeze frame pembuka + zoom kecil
                z = 1 + 0.05 * (t / freeze)
                cw, ch = W / z, H / z
                img = first.resize((W, H), Image.BILINEAR, box=((W - cw) / 2, (H - ch) / 2, (W + cw) / 2, (H + ch) / 2))
            else:
                tt = t - freeze
                z = 1.0 + 0.045 * min(1.0, tt / max(dur - freeze, 0.5))
                img = scene.render(t, zoom=z)
                flash = 1 - hs.prog(tt, 0, 0.16)
                if flash > 0:
                    img = Image.blend(img, Image.new("RGB", (W, H), (255, 255, 255)), 0.75 * flash)
            if cover_path and cov is None and t >= dur - 0.35:
                cov = img
            proc.stdin.write(img.tobytes())
        proc.stdin.close()
        if proc.wait(timeout=300) != 0:
            errf.seek(0)
            raise subprocess.CalledProcessError(proc.returncode, "ffmpeg intro", stderr=errf.read()[-2000:])
    finally:
        if proc.poll() is None:
            proc.kill()
        errf.close()
    if cover_path:
        (cov or scene.render(dur, 1.04)).save(cover_path, quality=92)
    return n


def build(clip_info: Dict, video: str, W: int, H: int, clip_dir: Path, encode_args: List[str],
          style: Optional[str] = None, region: Optional[Tuple[float, float]] = None) -> Optional[Dict]:
    """Intro hook B-roll untuk satu klip: intro_video.mp4 (tanpa audio) + intro_audio.wav + cover.jpg."""
    clip_dir = Path(clip_dir)
    dur_clip = float(clip_info["end"]) - float(clip_info["start"])
    style = style or clip_info.get("hook_broll_style") or pick_style(clip_info)
    title = title_text(clip_info)
    max_sec = max(2.5, min(10.0, float(getattr(config, "HOOK_BROLL_MAX", 7.0) or 7.0)))
    still = frame_at(video, 0.0)
    if still is None:
        return None
    people = []
    try:
        people = grab_people(video, dur_clip, n=2, region=region)
    except Exception as exc:
        logger.warning(f"Potongan orang hook B-roll dilewati: {str(exc)[:160]}")
    voice = narrate(title, clip_dir, max_sec)
    vdur = float(voice["duration"]) if voice else 2.2
    dur = round(min(max_sec, max(2.5, 0.38 + vdur + 0.45)), 2)
    label = str(clip_info.get("category") or "").replace("_", " ")
    scene = build_scene(style, title, people, still, W, H, seed=int(float(clip_info.get("start", 0)) * 10) % 997, label=label)
    vid, wav, cov = clip_dir / "intro_video.mp4", clip_dir / "intro_audio.wav", clip_dir / "hook_cover.jpg"
    n = render_video(scene, still, dur, str(vid), encode_args, cover_path=str(cov))
    mix_audio(voice["path"] if voice else None, n / FPS, str(wav))
    return {"style": style, "title": title, "dur": round(n / FPS, 2), "video": str(vid), "audio": str(wav),
            "cover": str(cov), "voice": (voice or {}).get("voice"), "provider": (voice or {}).get("provider"),
            "people": [{"t": p["t"], "box": p["box"]} for p in people], "cast": "composed" if people else "frame"}


# ============================================================================ sambung ke klip

def _probe_audio(path: str) -> Tuple[int, int]:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=sample_rate,channels",
                        "-of", "csv=p=0", path], capture_output=True, text=True)
    try:
        sr, ch = r.stdout.strip().split(",")[:2]
        return int(sr), int(ch)
    except ValueError:
        return 48000, 2


def mux_intro(intro_video: str, intro_audio: str, like: str, out: str) -> str:
    """Intro + audionya, parameter audio disamakan dengan klip (syarat sambung tanpa encode ulang)."""
    sr, ch = _probe_audio(like)
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-i", intro_video, "-i", intro_audio, "-map", "0:v", "-map", "1:a",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "256k", "-ar", str(sr), "-ac", str(ch), "-shortest",
                    "-movflags", "+faststart", out], check=True, capture_output=True)
    return out


def prepend(final_path: str, intro_mp4: str, encode_args: List[str]) -> str:
    """intro + klip → menimpa final. Coba concat tanpa encode ulang video; bila hasil tidak bersih → encode ulang."""
    final = Path(final_path)
    d = final.parent
    lst = d / "_concat.txt"
    out = d / "_with_intro.mp4"

    def q(p) -> str:
        return "'" + str(Path(p).resolve()).replace("\\", "/").replace("'", "'\\''") + "'"

    lst.write_text(f"file {q(intro_mp4)}\nfile {q(final)}\n", encoding="utf-8")
    ok = False
    try:
        subprocess.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c:v", "copy",
                        "-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart", str(out)], check=True, capture_output=True)
        chk = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(out), "-f", "null", "-"], capture_output=True, text=True)
        want = _duration(intro_mp4) + _duration(str(final))
        ok = not chk.stderr.strip() and abs(_duration(str(out)) - want) < 0.25
    except subprocess.CalledProcessError:
        ok = False
    if not ok:
        logger.info("  Sambung intro: encode ulang (parameter stream berbeda)")
        subprocess.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-i", intro_mp4, "-i", str(final), "-filter_complex",
                        "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[v][a]", "-map", "[v]", "-map", "[a]", *encode_args,
                        "-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart", str(out)], check=True, capture_output=True)
    lst.unlink(missing_ok=True)
    out.replace(final)
    return str(final)


def _duration(path: str) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


# ============================================================================ pratinjau

def preview_png(style: str, out_path: str, width: int = 540, still: Optional[Image.Image] = None,
                title: str = "Ternyata begini *rahasia* mereka selama ini") -> str:
    """Sampul contoh tanpa video sumber: siluet sederhana sebagai pengganti potongan orang."""
    W, H = width, int(width * 16 / 9) // 2 * 2
    if still is None:
        still = Image.new("RGB", (W, H), (60, 70, 95))
        d = ImageDraw.Draw(still)
        for i in range(12):
            d.rectangle((0, H * i / 12, W, H * (i + 1) / 12), fill=(60 + i * 8, 70 + i * 4, 95 + i * 6))
    fig = Image.new("RGBA", (int(W * 0.5), int(H * 0.5)), (0, 0, 0, 0))
    d = ImageDraw.Draw(fig)
    fw, fh = fig.size
    d.ellipse((fw * 0.3, 0, fw * 0.7, fh * 0.3), fill=(235, 190, 160, 255))
    d.rounded_rectangle((fw * 0.08, fh * 0.32, fw * 0.92, fh), radius=int(fw * 0.2), fill=(40, 120, 220, 255))
    people = [{"cut": fig, "frame": still, "t": 0.0, "box": [0, 0, 1, 1]}]
    sc = build_scene(style, title, people, still, W, H, label="humor")
    sc.render(3.0, 1.04).save(out_path)
    return out_path
