"""Deteksi area video yang sebenarnya (active picture) di dalam frame sumber.

Banyak podcast/webinar diunggah dengan bingkai: video asli ditaruh di dalam kartu bersudut bulat
dengan border hitam dan teks branding (logo channel, nama acara) di tepi atas/bawah. Bila 9:16
di-crop dari seluruh frame, hasilnya penuh bar hitam dan potongan teks branding yang terpotong.
`cropdetect` bawaan FFmpeg gagal di kasus ini karena teks putih di border dianggap konten, jadi
deteksi dilakukan per baris/kolom: sebuah baris dianggap "gambar" bila cukup banyak pikselnya
terang, sedangkan baris yang hanya berisi teks tipis tidak.
"""
from typing import List, Optional, Tuple

import cv2
import numpy as np
from loguru import logger

Rect = Tuple[int, int, int, int]  # x, y, w, h

_DARK = 38             # luminance <= ini dianggap hitam (border)
_MIN_FILL = 0.22       # baris/kolom dianggap gambar bila >22% pikselnya terang
_MIN_GAIN = 0.05       # abaikan bila border yang dibuang <5% di kedua sumbu
_INSET = 0.04          # buang sedikit tepi agar sudut bulat & anti-alias tidak ikut


def _longest_run(mask: np.ndarray) -> Optional[Tuple[int, int]]:
    best, start = None, None
    for i, v in enumerate(list(mask) + [False]):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if best is None or i - start > best[1] - best[0]:
                best = (start, i)
            start = None
    return best


def _frame_rect(gray: np.ndarray) -> Optional[Rect]:
    h, w = gray.shape
    bright = gray > _DARK
    if bright.mean() < 0.25:        # adegan gelap: tidak bisa membedakan border dari konten
        return None
    rows = _longest_run(bright.mean(axis=1) > _MIN_FILL)
    cols = _longest_run(bright.mean(axis=0) > _MIN_FILL)
    if not rows or not cols:
        return None
    return cols[0], rows[0], cols[1] - cols[0], rows[1] - rows[0]


def detect_content_rect(video_path: str, samples: int = 9) -> Optional[Rect]:
    """Rect (x, y, w, h) area gambar sebenarnya, atau None bila video sudah penuh-frame."""
    cap = cv2.VideoCapture(video_path)
    try:
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width, height = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        if total <= 0 or width <= 0 or height <= 0:
            return None
        rects: List[Rect] = []
        for k in range(samples):
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * (0.06 + 0.88 * k / max(samples - 1, 1))))
            ok, frame = cap.read()
            if not ok:
                continue
            r = _frame_rect(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
            if r:
                rects.append(r)
    finally:
        cap.release()
    if len(rects) < max(3, samples // 2):
        return None
    # Baris/kolom dibandingkan sebagai gabungan: kolom bisa tampak sempit di adegan dengan baju gelap
    # menempel di tepi, tetapi tepi kartu bingkai yang sebenarnya selalu terlihat di salah satu sampel.
    x0 = int(np.percentile([r[0] for r in rects], 10))
    x1 = int(np.percentile([r[0] + r[2] for r in rects], 90))
    y0 = int(np.percentile([r[1] for r in rects], 10))
    y1 = int(np.percentile([r[1] + r[3] for r in rects], 90))
    x, y, w, h = x0, y0, x1 - x0, y1 - y0
    spread = np.std([r[3] for r in rects]) / height     # tinggi kartu harus konsisten antar sampel
    gain = max(1 - w / width, 1 - h / height)
    if spread > 0.03 or gain < _MIN_GAIN or w * h < 0.35 * width * height:
        return None
    ix, iy = int(w * _INSET), int(h * _INSET)
    x, y, w, h = x + ix, y + iy, w - 2 * ix, h - 2 * iy
    x, y, w, h = x, y, w - w % 2, h - h % 2
    logger.info(f"  Bingkai video terdeteksi: konten {w}x{h} di ({x},{y}) dari {width}x{height} — border/branding dibuang")
    return x, y, w, h
