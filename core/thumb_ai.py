"""Thumbnail AI gaya clipper (v6.9): latar dibuat AI + WAJAH ASLI dari klip + cuplikan momen asli + teks tebal.

Kenapa wajah asli, bukan wajah buatan AI: gambar AI orang nyata dalam adegan yang tidak pernah terjadi bisa dianggap
menyesatkan/peniruan oleh YouTube & TikTok (jangkauan turun / strike) — wajah asli juga lebih dipercaya penonton.

Tata letak 1080×1920 dengan zona aman 3:4 (y 240–1680): TikTok memotong cover ke 3:4 di profil & editor cover,
YouTube Shorts menampilkan 9:16 penuh. Semua elemen penting (teks, wajah, cuplikan) ada di zona aman.

Alur per klip: pilih frame (wajah paling jelas & tajam) → potong orang (MediaPipe; hanya bagian yang menyatu dengan
wajah) → latar AI (prompt adegan ditulis LLM dari isi klip; tanpa orang/teks) → cuplikan momen dari video SUMBER
(bersih, tanpa subtitle) → teks dari Editor-AI (thumb_text) → simpan thumbnail_ai.jpg di folder klip.
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger

import config

W, H = 1080, 1920
SAFE_TOP, SAFE_BOTTOM = 240, 1680  # zona 3:4 di tengah 9:16
FONT = r"C:\Windows\Fonts\impact.ttf"
FONT_FALLBACK = r"C:\Windows\Fonts\ariblk.ttf"

_SCENES = {  # cadangan bila LLM tidak tersedia
    "gaming": "neon open-world city at golden-pink sunset, palm trees, police car lights on a wet street",
    "comedy": "bright colorful pop-art studio backdrop with confetti bursts and spotlight",
    "education": "clean modern classroom with glowing chalkboard diagrams, soft cinematic light",
    "finance": "dramatic stock market screens and gold coins, dark blue cinematic light",
    "motivation": "mountain summit at sunrise above the clouds, epic golden rays",
    "relationship": "romantic city street at night with bokeh lights and soft pink glow",
    "tech": "futuristic neon tech lab with holographic screens",
    "beauty": "pastel vanity studio with ring light and sparkles",
    "general": "vibrant colorful abstract background with dynamic light rays",
}


def enabled() -> bool:
    if not getattr(config, "THUMB_AI", True):
        return False
    from . import media_ai
    return any(media_ai.image_available(p) for p in media_ai.image_providers() if p != "card")


# ------------------------------------------------------------------------------------------------ bahan
def _scene_prompt(clip: Dict, niche: str) -> str:
    """Adegan latar (bahasa Inggris, tanpa orang/teks) yang cocok dengan isi klip — ditulis LLM."""
    base = _SCENES.get(niche, _SCENES["general"])
    try:
        from . import ai_providers as ai
        raw = ai.complete(
            "Write ONE short English image prompt (max 40 words) for the BACKGROUND SCENE of a vertical YouTube Shorts / "
            "TikTok thumbnail for this clip. Match the clip's setting and mood (game world, place, objects, time of day). "
            "Vivid saturated colors, cinematic lighting, depth. ABSOLUTELY no people, no faces, no characters, no text, "
            "no letters, no logos, no brand names. Return JSON {\"scene\": \"...\"}.\n\n"
            f"Clip title: {clip.get('ai_title') or ''}\nThumbnail text: {clip.get('thumb_text') or ''}\n"
            f"Niche: {niche}\nWhat is said: {str(clip.get('text') or clip.get('transcript') or '')[:600]}",
            max_tokens=200, json_mode=True)
        obj, _ = json.JSONDecoder().raw_decode(raw[raw.index("{"):])  # objek JSON pertama saja
        scene = str(obj.get("scene") or "").strip()
        if len(scene) > 15:
            base = scene
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"Prompt latar thumbnail cadangan: {str(exc)[:120]}")
    return (f"Vertical 9:16 dramatic thumbnail background: {base}. Bold saturated colors, cinematic lighting, high detail, "
            "subject-free composition. No people, no faces, no text, no letters, no logos, no watermark.")


def _generate_bg(prompt: str, out: str) -> Dict:
    """Latar AI: provider utama dicoba ulang saat dibatasi (HTTP 429) sebelum jatuh ke provider cadangan yang lebih
    lemah — hasil terbaik lebih penting daripada beberapa detik."""
    import time
    from . import media_ai
    for wait in (20, 45, 90):
        try:
            return media_ai.image(prompt, out, w=W, h=H, allow_fallback=False)
        except Exception as exc:  # noqa: BLE001
            if not re.search(r"429|rate limit|too many", str(exc), re.I):
                break
            logger.info(f"Generator gambar dibatasi — coba lagi {wait} dtk")
            time.sleep(wait)
    return media_ai.image(prompt, out, w=W, h=H, allow_fallback=True)


def _frame(path: str, t: float):
    import cv2
    from PIL import Image
    cap = cv2.VideoCapture(path)
    cap.set(cv2.CAP_PROP_POS_MSEC, max(t, 0) * 1000)
    ok, fr = cap.read()
    cap.release()
    if not ok or fr is None:
        return None
    return Image.fromarray(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB))


def _content_end(clip: Dict) -> Optional[float]:
    """Detik terakhir ISI klip di file akhir — setelahnya penutup Brand Kit / CTA (bergambar, bukan wajah kreator)."""
    try:
        length = float(clip["end"]) - float(clip["start"]) - float(clip.get("jump_cut_removed_sec") or 0)
        shift = float((clip.get("cold_open") or {}).get("shift") or 0)
        return max(2.0, length + shift - 0.8)
    except (KeyError, TypeError, ValueError):
        return None


def _best_face_frame(clip_path: str, content_end: Optional[float] = None) -> Optional[Dict]:
    """Frame dengan wajah paling jelas & tajam (hindari kartu hook di awal dan penutup/CTA di akhir)."""
    import cv2
    from . import editing
    cap = cv2.VideoCapture(clip_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    dur = (cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0) / fps
    cap.release()
    if dur < 2:
        return None
    start, end = min(2.8, dur * 0.2), max(dur - 2.5, dur * 0.6)
    if content_end:
        end = min(end, content_end)
    cands = [start + (end - start) * i / 23 for i in range(24)]
    # wajah utuh: ada ruang di atas kepala & tidak terlalu besar (frame yang sedang di-zoom membuat wajah terpotong)
    best = [b for b in editing.pick_best_frames(clip_path, cands, top=12)
            if b.get("face") and b["face"][1] - 0.35 * b["face"][3] >= 0 and b["face"][3] <= 0.32
            and b["face"][0] >= 0.01 and b["face"][0] + b["face"][2] <= 0.99]
    return best[0] if best else None


def _panel_bottom(img, y_from: int, y_to: int) -> Optional[int]:
    """Batas bawah panel facecam (tata letak split: facecam di atas gameplay) di bawah wajah: garis pemisah tipis
    (baris seragam gelap/terang) atau perubahan gambar tajam yang melintang penuh. None bila tidak ada (satu panel)."""
    import numpy as np
    g = np.asarray(img.convert("L"), dtype=np.float32)
    y_from, y_to = max(1, y_from), min(g.shape[0] - 2, y_to)
    if y_to - y_from < 10:
        return None
    rows = g[y_from:y_to]
    mean, std = rows.mean(axis=1), rows.std(axis=1)
    line = np.where((std < 12) & ((mean < 30) | (mean > 225)))[0]
    if len(line):
        return y_from + int(line[0])
    diff = np.abs(np.diff(g[y_from - 1:y_to + 1], axis=0))
    full = (diff > 18).mean(axis=1)
    i = int(np.argmax(full))
    return y_from + i if full[i] > 0.55 else None


def _cutout(img, face_box, seam_margin: int = 70):
    """Potong orang di sekitar wajah: area wajah diperlebar, segmentasi MediaPipe, hanya komponen yang menyatu dengan
    wajah (karakter game/orang lain di gameplay tidak ikut). Return (RGBA, kotak wajah di dalam potongan) atau None."""
    import cv2
    import numpy as np
    from PIL import Image
    from .mediapipe_compat import SelfieSegmenterCompat
    iw, ih = img.size
    fx, fy, fw, fh = face_box[0] * iw, face_box[1] * ih, face_box[2] * iw, face_box[3] * ih
    x0, x1 = max(0, int(fx - 1.7 * fw)), min(iw, int(fx + fw + 1.7 * fw))
    y0, y1 = max(0, int(fy - 0.8 * fh)), min(ih, int(fy + fh + 2.8 * fh))
    cut = _panel_bottom(img, int(fy + fh * 1.3), y1)
    if cut:  # jangan ikut membawa gameplay di bawah panel facecam, juga subtitle yang duduk di perbatasannya
        y1 = max(int(fy + fh * 1.25), cut - seam_margin)
    crop = img.crop((x0, y0, x1, y1))
    seg = SelfieSegmenterCompat()
    try:
        m = np.array(seg.segment_mask(np.ascontiguousarray(np.asarray(crop))), dtype=np.float32, copy=True)
    finally:
        try:
            seg.close()
        except Exception:  # noqa: BLE001
            pass
    m = cv2.GaussianBlur(m, (0, 0), 1.5)
    hard = (m > 0.5).astype(np.uint8)
    n, labels = cv2.connectedComponents(hard)
    cx, cy = int(fx + fw / 2 - x0), int(fy + fh / 2 - y0)
    keep = labels[min(cy, labels.shape[0] - 1), min(cx, labels.shape[1] - 1)]
    if keep == 0:  # pusat wajah tidak tertangkap → pakai komponen terbesar
        sizes = [(labels == i).sum() for i in range(1, n)]
        if not sizes:
            return None
        keep = 1 + int(np.argmax(sizes))
    alpha = np.clip((m - 0.4) / 0.3, 0, 1) * (labels == keep)
    alpha = cv2.GaussianBlur(alpha.astype(np.float32), (0, 0), 1.2)
    # tepi bawah dipudarkan (potongan frame yang rata tidak terlihat kaku)
    fade = max(8, int(alpha.shape[0] * 0.22))
    alpha[-fade:] *= np.linspace(1, 0, fade)[:, None]
    out = crop.copy()
    out.putalpha(Image.fromarray((alpha * 255).astype(np.uint8)))
    bbox = out.getbbox()
    if not bbox:
        return None
    face_in = (fx - x0 - bbox[0], fy - y0 - bbox[1], fw, fh)
    return out.crop(bbox), face_in


def _overlay_bounds(rgb, box):
    """Tepi kotak facecam (overlay di atas gameplay) di sekitar wajah: garis lurus panjang di kiri, kanan & bawah wajah.
    Return (x0, x1, y1) dalam piksel crop — sisi yang tidak jelas = batas crop."""
    import cv2
    import numpy as np
    g = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    h, w = g.shape
    bx, by, bw, bh = box
    dx = np.abs(np.diff(g, axis=1))   # tepi vertikal
    dy = np.abs(np.diff(g, axis=0))   # tepi horizontal
    ys = slice(max(0, int(by - 0.3 * bh)), min(h, int(by + 2.0 * bh)))
    xs = slice(max(0, int(bx - 0.6 * bw)), min(w - 1, int(bx + 1.6 * bw)))

    def col(lo, hi, rev=False):
        rng = range(hi - 1, lo - 1, -1) if rev else range(lo, hi)
        best, at = 0.0, None
        for x in rng:
            if 0 <= x < w - 1:
                f = float((dx[ys, x] > 22).mean())
                if f > best:
                    best, at = f, x
        return at if best > 0.6 else None

    left = col(0, max(1, int(bx - 0.25 * bw)), rev=True)
    right = col(min(w - 1, int(bx + 1.25 * bw)), w - 1)
    bottom, best = None, 0.0
    for y in range(min(h - 2, int(by + 1.6 * bh)), h - 1):
        f = float((dy[y, xs] > 22).mean())
        if f > best:
            best, bottom = f, y
    return ((left + 1) if left is not None else 0, (right + 1) if right is not None else w,
            (bottom + 1) if (bottom is not None and best > 0.6) else h)


def _source_face(source: str, clip: Dict, layout: Dict):
    """Frame terbaik dari kotak facecam di video SUMBER selama rentang klip: area di sekitar wajah streamer diperbesar
    (LANCZOS + sedikit tajam). Return (gambar, kotak wajah relatif, detik relatif) atau None."""
    import cv2
    from PIL import Image, ImageFilter
    from .mediapipe_compat import FaceDetectorCompat
    mx, my, fw, fh = (float(v) for v in layout.get("face") or [0, 0, 0, 0])
    if fw <= 0 or fh <= 0:
        return None
    cap = cv2.VideoCapture(source)
    vw, vh = cap.get(cv2.CAP_PROP_FRAME_WIDTH), cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
    # area: lebar ±5× wajah, dari atas kepala sampai dada; tetap di dalam frame
    x0 = max(0, int((mx - 2.1 * fw) * vw)); x1 = min(int(vw), int((mx + 2.1 * fw) * vw))
    y0 = max(0, int((my - 1.3 * fh) * vh)); y1 = min(int(vh), int((my + 1.9 * fh) * vh))
    if x1 - x0 < 40 or y1 - y0 < 40:
        return None
    start, end = float(clip["start"]), float(clip.get("end") or clip["start"] + 20)
    det = FaceDetectorCompat(min_confidence=0.6)
    best = None
    try:
        for k in range(16):
            t = start + 0.6 + (end - start - 1.2) * k / 15
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, fr = cap.read()
            if not ok:
                continue
            crop = cv2.cvtColor(fr[y0:y1, x0:x1], cv2.COLOR_BGR2RGB)
            faces = det.detect_full(crop)
            if not faces:
                continue
            f = max(faces, key=lambda z: z["box"][2] * z["box"][3] * z["score"])
            bx, by, bw, bh = f["box"]
            if by - 0.3 * bh < 0 or bx < 1 or bx + bw > crop.shape[1] - 1:
                continue  # wajah terpotong tepi
            face = crop[max(0, by):by + bh, max(0, bx):bx + bw]
            sharp = float(cv2.Laplacian(cv2.cvtColor(face, cv2.COLOR_RGB2GRAY), cv2.CV_64F).var()) if face.size else 0
            score = f["score"] * (1 + min(sharp, 400) / 200)
            if best is None or score > best[0]:
                best = (score, crop.copy(), (bx, by, bw, bh), t - start)
    finally:
        cap.release()
    if best is None:
        return None
    _, crop, (bx, by, bw, bh), rel = best
    ox0, ox1, oy1 = _overlay_bounds(crop, (bx, by, bw, bh))   # buang gameplay/HUD di luar kotak facecam
    crop = crop[:oy1, ox0:ox1]
    bx -= ox0
    h, w = crop.shape[:2]
    k = max(1.0, 900 / w)
    img = Image.fromarray(crop).resize((int(w * k), int(h * k)), Image.LANCZOS)
    img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=90, threshold=2))
    return img, (bx / w, by / h, bw / w, bh / h), rel


def _lines(text: str) -> List[str]:
    """Teks thumbnail → 2–3 baris seimbang, HURUF BESAR."""
    words = re.sub(r"\s+", " ", str(text or "").strip()).upper().split(" ")
    words = [w for w in words if w]
    if not words:
        return []
    if len(words) <= 2:
        return words if len(" ".join(words)) > 10 else [" ".join(words)]
    best, target = [], max(len(" ".join(words)) / 3, 6)
    cur = ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > max(target, 9):
            best.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    best.append(cur)
    while len(best) > 3:  # gabungkan baris terpendek
        i = min(range(len(best) - 1), key=lambda k: len(best[k]) + len(best[k + 1]))
        best[i:i + 2] = [best[i] + " " + best[i + 1]]
    return best


# ------------------------------------------------------------------------------------------------ komposisi
def make(clip: Dict, source: Optional[str] = None, niche: str = "gaming") -> Optional[str]:
    """Buat thumbnail_ai.jpg di folder klip. Return path, atau None bila tidak bisa (tanpa wajah, provider mati, dll.)."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    clip_path = clip.get("file_path")
    if not clip_path or not Path(clip_path).exists():
        return None
    out_dir = Path(clip_path).parent
    out = out_dir / "thumbnail_ai.jpg"
    layout = clip.get("stream_layout")
    cut, t, frame = None, None, None
    if layout and source and Path(source).exists() and clip.get("start") is not None:
        got = _source_face(source, clip, layout)   # klip streamer: wajah dari facecam sumber (bersih)
        if got:
            frame, fbox, t = got
            cut = _cutout(frame, fbox, seam_margin=0)
    if not cut:                                   # selain itu: frame terbaik di klip akhir
        pick = _best_face_frame(clip_path, _content_end(clip))
        if not pick:
            logger.info(f"Thumbnail AI dilewati (tidak ada wajah jelas): {Path(clip_path).name}")
            return None
        t = float(pick["time"])
        frame = _frame(clip_path, t)
        cut = _cutout(frame, pick["face"]) if frame is not None else None
    if not cut:
        return None
    person, face_in = cut

    # latar AI (disimpan, tidak dibuat ulang)
    bg_path = out_dir / "thumb_bg.jpg"
    if not bg_path.exists():
        try:
            res = _generate_bg(_scene_prompt(clip, niche), str(bg_path))
            if res.get("provider") == "card":  # bukan gambar AI → pakai frame klip yang diburamkan
                bg_path.unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Latar AI gagal, pakai frame klip: {str(exc)[:160]}")
    if bg_path.exists():
        canvas = Image.open(bg_path).convert("RGB").resize((W, H))
    else:
        from PIL import ImageEnhance
        canvas = ImageEnhance.Color(frame.resize((W, H)).filter(ImageFilter.GaussianBlur(28))).enhance(1.6)
    shade = Image.linear_gradient("L").resize((W, H)).point(lambda v: int(max(0, v - 90) * 1.25))
    canvas = Image.composite(Image.new("RGB", (W, H), (0, 0, 0)), canvas, shade).convert("RGBA")

    # cuplikan momen: dari video sumber (bersih) bila ada, selain itu frame klip itu sendiri
    shot = None
    if source and Path(source).exists() and clip.get("start") is not None:
        # cuplikan yang "terbaca": dekat momen wajah dulu, lalu seluruh rentang klip — hindari frame polos/kilatan/gelap
        span = max(1.0, float(clip.get("end") or clip["start"] + 20) - float(clip["start"]))
        times = [t + d for d in (0.0, 1.5, -1.5, 3.0, -3.0)] + [span * k / 9 for k in range(1, 9)]
        best_std = -1.0
        for tt in times:
            cand = _frame(source, float(clip["start"]) + min(max(tt, 0.0), span))
            if cand is None:
                continue
            g = np.asarray(cand.convert("L").resize((160, 90)), dtype=np.float32)[9:81, 12:120]  # gameplay, tanpa facecam
            if g.std() >= 28 and 25 <= g.mean() <= 215:
                shot = cand
                break
            if g.std() > best_std:
                best_std, shot = float(g.std()), cand
    inset_bottom = SAFE_TOP
    if shot is not None:
        sw = 880
        g = shot.resize((sw, int(sw * shot.height / shot.width)))
        g = g.crop((0, 0, sw, min(g.height, 500)))
        gb = Image.new("RGBA", (g.width + 24, g.height + 24), (255, 255, 255, 255))
        gb.paste(g, (12, 12))
        mask = Image.new("L", gb.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, *gb.size), 34, fill=255)
        gb.putalpha(mask)
        gb = gb.rotate(-4, expand=True, resample=Image.BICUBIC)
        glow = Image.new("RGBA", (gb.width + 80, gb.height + 80), (0, 0, 0, 0))
        glow.paste((255, 214, 0, 255), (40, 40), gb.split()[3])
        glow = glow.filter(ImageFilter.GaussianBlur(20))
        top = SAFE_TOP + 10
        canvas.alpha_composite(glow, (W // 2 - glow.width // 2, top - 40))
        canvas.alpha_composite(gb, (W // 2 - gb.width // 2, top))
        inset_bottom = top + gb.height

    # teks: ukur dulu (bawah teks = batas zona aman)
    lines = _lines(clip.get("thumb_text") or clip.get("hook_text") or "") or ["WAJIB TONTON"]
    font_file = FONT if Path(FONT).exists() else FONT_FALLBACK
    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    sizes = []
    for line in lines:
        size = 190
        while size > 90 and probe.textlength(line, font=ImageFont.truetype(font_file, size)) > W - 120:
            size -= 6
        sizes.append(size)
    text_h = int(sum(s * 0.98 for s in sizes))
    text_top = SAFE_BOTTOM - 20 - text_h

    # orang: wajah ±34% lebar kanvas, dagu tepat di atas teks, boleh menimpa tepi cuplikan
    fw = max(face_in[2], 1)
    scale = min(W * 0.34 / fw, (W * 1.05) / person.width)
    pp = person.resize((max(1, int(person.width * scale)), max(1, int(person.height * scale))), Image.LANCZOS)
    face_bottom = (face_in[1] + face_in[3]) * scale
    py = int(text_top + 40 - face_bottom)
    py = max(py, inset_bottom - int(face_in[1] * scale) - 60)  # jangan sampai wajah tertutup cuplikan
    px = int(W / 2 - (face_in[0] + face_in[2] / 2) * scale)
    px = max(-pp.width // 3, min(px, W - pp.width * 2 // 3))
    a = pp.split()[3]
    # badan berakhir tepat di bawah teks (zona bawah = latar saja; HUD game/pinggir frame tidak ikut terlihat)
    end_y = SAFE_BOTTOM + 30 - py
    if 0 < end_y < pp.height:
        ramp = Image.linear_gradient("L").resize((1, 160)).point(lambda v: 255 - v)  # 255 → 0
        fade = Image.new("L", pp.size, 255)
        fade.paste(ramp.resize((pp.width, 160)), (0, max(0, end_y - 160)))
        fade.paste(0, (0, end_y, pp.width, pp.height))
        a = Image.fromarray((np.asarray(a, dtype=np.uint16) * np.asarray(fade, dtype=np.uint16) // 255).astype(np.uint8))
        pp.putalpha(a)
    outline = a.filter(ImageFilter.MaxFilter(21))
    stroke = Image.new("RGBA", pp.size, (255, 255, 255, 0))
    stroke.putalpha(outline)
    shadow = Image.new("RGBA", pp.size, (0, 0, 0, 0))
    shadow.putalpha(outline.filter(ImageFilter.GaussianBlur(26)).point(lambda v: int(v * 0.75)))
    canvas.alpha_composite(shadow, (px + 18, py + 22))
    canvas.alpha_composite(stroke, (px, py))
    canvas.alpha_composite(pp, (px, py))

    # teks tebal: baris kata kunci kuning, sisanya putih, stroke hitam, sedikit miring
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    hi = max(range(len(lines)), key=lambda i: max((len(w) for w in lines[i].split()), default=0))
    y = text_top
    for i, (line, size) in enumerate(zip(lines, sizes)):
        font = ImageFont.truetype(font_file, size)
        tw = d.textlength(line, font=font)
        d.text(((W - tw) / 2, y), line, font=font, fill=(255, 221, 0) if i == hi else (255, 255, 255),
               stroke_width=max(10, size // 12), stroke_fill=(0, 0, 0))
        y += int(size * 0.98)
    layer = layer.rotate(3, resample=Image.BICUBIC, center=(W // 2, text_top + text_h // 2))
    canvas.alpha_composite(layer)

    # stiker tanda baca di pojok cuplikan
    mark = "?!" if "?" in " ".join(lines) else "!!"
    st = Image.new("RGBA", (320, 260), (0, 0, 0, 0))
    ImageDraw.Draw(st).text((16, 8), mark, font=ImageFont.truetype(font_file, 165), fill=(255, 221, 0),
                            stroke_width=12, stroke_fill=(0, 0, 0))
    canvas.alpha_composite(st.rotate(12, expand=True), (W - 300, SAFE_TOP - 110))

    canvas.convert("RGB").save(out, quality=92)
    return str(out)


def make_all(clips: List[Dict], source: Optional[str] = None, niche_for=None, progress=None) -> int:
    """Thumbnail AI untuk semua klip (3 sekaligus — waktu habis menunggu generator gambar). Klip yang berhasil
    mendapat clip["thumbnail"] = thumbnail AI (thumbnail frame lama tetap di clip["thumbnail_frame"])."""
    if not clips or not enabled():
        return 0
    done = 0

    def one(c: Dict) -> Optional[str]:
        try:
            return make(c, source, niche_for(c) if niche_for else "gaming")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Thumbnail AI gagal untuk {Path(str(c.get('file_path'))).name}: {str(exc)[:200]}")
            return None

    with ThreadPoolExecutor(max_workers=2) as ex:  # generator gambar membatasi permintaan bersamaan
        for c, path in zip(clips, ex.map(one, clips)):
            if path:
                c["thumbnail_frame"] = c.get("thumbnail")
                c["thumbnail"] = path
                done += 1
                if progress:
                    progress(done)
    return done


def upload_image_for(video_path: str) -> Optional[str]:
    """Gambar thumbnail/cover untuk diunggah bersama video: thumbnail AI, kalau tidak ada thumbnail frame."""
    folder = Path(video_path).parent
    for name in ("thumbnail_ai.jpg", "thumbnail.jpg"):
        if (folder / name).exists():
            return str(folder / name)
    return None
