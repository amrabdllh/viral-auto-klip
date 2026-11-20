"""LUT 3D (.cube) buatan sendiri untuk tampilan sinematik — tanpa file aset dari luar.

LUT dihitung sekali dari rumus warna lalu disimpan di temp/luts/. FFmpeg `lut3d` menerapkannya per piksel di GPU-less
pipeline dengan biaya kecil (interpolasi tetrahedral), jauh lebih kaya daripada eq/curves biasa: kurva-S per kanal,
pemisahan warna bayangan (teal) dan sorotan/kulit (hangat), serta saturasi selektif.
"""
import threading
from pathlib import Path
from typing import Dict

import numpy as np

import config

_LOCK = threading.Lock()
SIZE = 33

# parameter tiap tampilan: kontras (kurva-S), split-toning, saturasi kulit vs warna lain
LOOKS: Dict[str, Dict] = {
    # podcast/talking head: kulit hangat & bersih, bayangan sedikit teal, hitam dalam tapi tidak hancur
    "film": {"contrast": 0.70, "lift": 0.012, "shadow": (-0.032, 0.012, 0.045), "high": (0.038, 0.012, -0.030),
             "skin_sat": 1.15, "other_sat": 0.90, "gamma": 0.975},
    # lebih berani: teal-orange jelas, kontras tinggi (cocok untuk konten emosional/dramatis)
    "teal_orange": {"contrast": 0.8, "lift": 0.01, "shadow": (-0.045, 0.015, 0.060), "high": (0.050, 0.015, -0.040),
                    "skin_sat": 1.18, "other_sat": 0.85, "gamma": 0.97},
}


def _scurve(x: np.ndarray, k: float) -> np.ndarray:
    """Kurva-S halus berpusat di 0.5; k=0 → linear."""
    s = x * x * (3 - 2 * x)  # smoothstep
    return x + k * (s - x)


def _build(p: Dict) -> np.ndarray:
    g = np.linspace(0.0, 1.0, SIZE, dtype=np.float64)
    b, gg, r = np.meshgrid(g, g, g, indexing="ij")   # .cube: R berubah paling cepat
    rgb = np.stack([r, gg, b], axis=-1)
    rgb = np.power(rgb, p["gamma"])
    rgb = p["lift"] + (1 - p["lift"]) * _scurve(rgb, p["contrast"])
    luma = (rgb * np.array([0.2126, 0.7152, 0.0722])).sum(-1, keepdims=True)
    # split toning: bayangan → teal, sorotan → hangat (bobot halus menurut luma)
    w_sh = np.clip(1 - luma / 0.5, 0, 1) ** 1.5
    w_hi = np.clip((luma - 0.5) / 0.5, 0, 1) ** 1.2
    rgb = rgb + w_sh * np.array(p["shadow"]) + w_hi * np.array(p["high"])
    # saturasi selektif: warna kulit (hue jingga ±) dinaikkan, sisanya diturunkan sedikit
    mx, mn = rgb.max(-1, keepdims=True), rgb.min(-1, keepdims=True)
    r_, g_, b_ = rgb[..., :1], rgb[..., 1:2], rgb[..., 2:3]
    skin = np.clip(1 - np.abs((r_ - g_) - 0.12) / 0.18, 0, 1) * (r_ > b_) * np.clip((mx - mn) * 6, 0, 1)
    sat = p["other_sat"] + (p["skin_sat"] - p["other_sat"]) * skin
    luma = (rgb * np.array([0.2126, 0.7152, 0.0722])).sum(-1, keepdims=True)
    rgb = luma + (rgb - luma) * sat
    return np.clip(rgb, 0, 1)


def lut_path(name: str) -> str:
    """Path file .cube (dibuat sekali). Kosong bila tampilan tidak dikenal."""
    p = LOOKS.get(name)
    if not p:
        return ""
    target = Path(config.TEMP_DIR) / "luts" / f"{name}_v2.cube"
    with _LOCK:
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            data = _build(p).reshape(-1, 3)
            lines = [f'TITLE "{name}"', f"LUT_3D_SIZE {SIZE}", "DOMAIN_MIN 0 0 0", "DOMAIN_MAX 1 1 1"]
            lines += [f"{v[0]:.6f} {v[1]:.6f} {v[2]:.6f}" for v in data]
            target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(target)
