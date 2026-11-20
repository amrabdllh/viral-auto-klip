"""Restorasi wajah AI (GFPGAN v1.4, PyTorch CUDA) — seluruhnya di GPU.

Mengapa: sumber podcast berbingkai hanya ±784 px tinggi, jadi wajah di klip 1080x1920 berasal dari ±250 px piksel asli.
Super-resolution umum hanya menghaluskan blok kompresi (kulit seperti lilin). GFPGAN dilatih khusus pada wajah (FFHQ)
sehingga mengembalikan detail mata, bingkai kacamata, bibir, dan tekstur kulit.

Alur per frame (tensor CUDA 1x3xHxW, rentang 0..1):
  YuNet 5 landmark (CPU, frame kecil) → pelacak wajah antar frame (landmark dihaluskan, kekuatan naik/turun bertahap
  supaya tidak "pop") → align ke template FFHQ 512 via grid_sample → GFPGAN bf16 dengan noise tetap (stabil antar frame)
  → dicampur dengan wajah asli (`AI_FACE_WEIGHT`, default 0.6: natural & identitas terjaga) → ditempel balik lewat
  mask elips lembut. Wajah kecil/menyamping dilewati (landmark tidak bisa diandalkan → hasil GAN aneh).

Model: tools/models/GFPGANv1.4.pth (rilis resmi TencentARC, Apache-2.0), dimuat lewat `spandrel`.
"""
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

import config

_MODEL = Path(config.BASE_DIR) / "tools" / "models" / "GFPGANv1.4.pth"
_URL = "https://github.com/TencentARC/GFPGAN/releases/download/v1.3.0/GFPGANv1.4.pth"
# template 5 titik FFHQ (crop 512): mata kiri-penonton, mata kanan-penonton, hidung, mulut kiri, mulut kanan
_TEMPLATE = np.array([[192.98138, 239.94708], [318.90277, 240.1936], [256.63416, 314.01935],
                      [201.26117, 371.41043], [313.08905, 371.15118]], dtype=np.float32)
_LOCK = threading.Lock()
_NET = None


def available() -> bool:
    """GFPGAN aktif & PyTorch CUDA ada (bobot diunduh otomatis saat pertama dipakai)."""
    if not getattr(config, "AI_FACE_RESTORE", True):
        return False
    from .upscale import _torch_ready
    return _torch_ready()


def _net():
    """(model, dtype). GFPGAN tidak stabil di fp16 (spandrel: supports_half=False) → bfloat16 di Ampere+, else fp32."""
    global _NET
    from .upscale import ensure_weights
    if _NET is None and not ensure_weights(_MODEL, _URL, 300_000_000):
        raise RuntimeError("model GFPGAN tidak tersedia")
    with _LOCK:
        if _NET is None:
            import torch
            from spandrel import ModelLoader
            m = ModelLoader().load_from_file(str(_MODEL))
            dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float32
            _NET = (m.model.cuda().eval().to(dtype), dtype)
    return _NET


def _grid(M: np.ndarray, out_hw: Tuple[int, int], src_hw: Tuple[int, int], x0: int = 0, y0: int = 0):
    """Grid untuk grid_sample: piksel output (x0+j, y0+i) → piksel sumber via affine M (2x3, output→sumber)."""
    import torch
    h, w = out_hw
    sh, sw = src_hw
    ys, xs = torch.meshgrid(torch.arange(h, device="cuda", dtype=torch.float32) + y0,
                            torch.arange(w, device="cuda", dtype=torch.float32) + x0, indexing="ij")
    m = torch.tensor(M, device="cuda", dtype=torch.float32)
    sx = m[0, 0] * xs + m[0, 1] * ys + m[0, 2]
    sy = m[1, 0] * xs + m[1, 1] * ys + m[1, 2]
    return torch.stack([(2 * sx + 1) / sw - 1, (2 * sy + 1) / sh - 1], dim=-1)[None]


class FaceRestorer:
    """Satu instance per klip (menyimpan jejak wajah antar frame)."""

    def __init__(self, weight: Optional[float] = None, min_face: int = 72):
        import cv2
        import torch
        from .mediapipe_compat import _yunet_model
        model = _yunet_model()
        if not model:
            raise RuntimeError("YuNet tidak tersedia")
        self._cv2 = cv2
        self._det = cv2.FaceDetectorYN.create(model, "", (320, 320), 0.6, 0.3, 50)
        self._det_size = None
        self.weight = float(getattr(config, "AI_FACE_WEIGHT", 0.6) if weight is None else weight)
        self.min_face = min_face
        self.net, self.dtype = _net()
        m = np.zeros((512, 512), np.float32)
        cv2.ellipse(m, (256, 282), (178, 222), 0, 0, 360, 1.0, -1)  # wajah + dahi, tanpa rambut/latar
        self.mask = torch.from_numpy(cv2.GaussianBlur(m, (0, 0), 18)).cuda()[None, None]
        self.tracks: List[Dict] = []
        self.frames = 0
        self.faces_done = 0

    # ------------------------------------------------------------------ deteksi & pelacakan
    def _detect(self, small_bgr: np.ndarray, scale: float) -> List[Tuple[np.ndarray, bool]]:
        h, w = small_bgr.shape[:2]
        if self._det_size != (w, h):
            self._det.setInputSize((w, h))
            self._det_size = (w, h)
        _, faces = self._det.detect(small_bgr)
        out = []
        for row in (faces if faces is not None else []):
            bw = float(row[2]) * scale
            if bw < self.min_face * 0.8:
                continue
            lm = row[4:14].reshape(5, 2).astype(np.float32) * scale
            eye = float(np.linalg.norm(lm[0] - lm[1]))
            lo, hi = sorted((lm[0, 0], lm[1, 0]))
            # frontal / tiga-perempat saja: jarak mata cukup lebar & hidung di antara mata
            ok = bool(row[14] >= 0.75 and bw >= self.min_face and eye >= 0.30 * bw and lo - 0.15 * eye <= lm[2, 0] <= hi + 0.15 * eye)
            out.append((lm, ok))
        return out

    def _update_tracks(self, dets: List[Tuple[np.ndarray, bool]]) -> None:
        used = set()
        for tr in self.tracks:
            eye = float(np.linalg.norm(tr["lm"][0] - tr["lm"][1])) or 1.0
            best, best_d = None, 9.0
            for i, (lm, _) in enumerate(dets):
                d = float(np.abs(lm - tr["lm"]).mean()) / eye
                if i not in used and d < best_d:
                    best, best_d = i, d
            if best is not None and best_d < 0.6:
                used.add(best)
                lm, ok = dets[best]
                # zona mati: deteksi mentah hampir sama dengan frame lalu (wajah diam / frame duplikat) → landmark
                # dikunci, crop identik → hasil GAN identik (tidak berkedip). Gerak kecil diredam, gerak besar diikuti.
                raw_d = float(np.abs(lm - tr.get("raw", lm)).mean()) / eye
                if raw_d >= 0.008:
                    a = 0.35 if best_d < 0.06 else (0.7 if best_d < 0.25 else 1.0)
                    tr["lm"] = tr["lm"] + a * (lm - tr["lm"])
                tr["raw"] = lm
                tr["cont"] = best_d < 0.25
                tr["miss"] = 0
                tr["s"] = min(1.0, tr["s"] + 0.25) if ok else max(0.0, tr["s"] - 0.25)
            else:
                tr["miss"] += 1
                tr["s"] = max(0.0, tr["s"] - 0.25)
        self.tracks = [t for t in self.tracks if t["miss"] <= 3 and (t["s"] > 0 or t["miss"] == 0)]
        for i, (lm, ok) in enumerate(dets):
            if i not in used:
                self.tracks.append({"lm": lm, "s": 0.25 if ok else 0.0, "miss": 0, "cont": False})

    # ------------------------------------------------------------------ proses
    def process(self, img, small_bgr: np.ndarray):
        """img: tensor CUDA float 1x3xHxW (RGB, 0..1) — diubah di tempat & dikembalikan.
        small_bgr: frame yang sama versi kecil (uint8 BGR) untuk deteksi wajah."""
        import torch
        import torch.nn.functional as F
        cv2 = self._cv2
        self.frames += 1
        H, W = img.shape[-2:]
        self._update_tracks(self._detect(small_bgr, W / small_bgr.shape[1]))
        for tr in self.tracks:
            wgt = self.weight * tr["s"]
            if wgt <= 0.02:
                continue
            M, _ = cv2.estimateAffinePartial2D(tr["lm"], _TEMPLATE, method=cv2.LMEDS)
            if M is None:
                continue
            Minv = cv2.invertAffineTransform(M)
            crop = F.grid_sample(img, _grid(Minv, (512, 512), (H, W)), mode="bilinear", padding_mode="reflection",
                                 align_corners=False)
            with torch.inference_mode():
                y = self.net((crop * 2 - 1).to(self.dtype), return_rgb=False, randomize_noise=False)[0]
            gan = (y.float().clamp(-1, 1) + 1) / 2
            prev = tr.get("prev")
            if prev is not None and tr.get("cont") and tr["miss"] == 0:
                # penghalusan temporal peka-gerak di ruang wajah ter-align (otomatis terkompensasi gerak kepala):
                # piksel yang inputnya hampir sama dengan frame lalu → rata-rata dengan hasil GAN sebelumnya
                # (tekstur tidak "mendidih"); piksel yang berubah (mulut bicara, kedip) → pakai hasil baru (tanpa ghosting)
                diff = F.avg_pool2d((crop - prev[0]).abs().mean(1, keepdim=True), 9, 1, 4)
                a = 0.6 * (1 - diff / 0.05).clamp(0, 1)
                gan = gan + a * (prev[1] - gan)
            tr["prev"] = (crop, gan)
            restored = crop + wgt * (gan - crop)
            pts = cv2.transform(np.array([[[0, 0]], [[512, 0]], [[0, 512]], [[512, 512]]], np.float32), Minv).reshape(-1, 2)
            x0, y0 = (int(v) for v in np.floor(pts.min(0)).clip(0, [W, H]))
            x1, y1 = (int(v) for v in np.ceil(pts.max(0)).clip(0, [W, H]))
            if x1 - x0 < 8 or y1 - y0 < 8:
                continue
            g = _grid(M, (y1 - y0, x1 - x0), (512, 512), x0, y0)
            back = F.grid_sample(restored, g, mode="bicubic", padding_mode="border", align_corners=False)
            alpha = F.grid_sample(self.mask, g, mode="bilinear", padding_mode="zeros", align_corners=False)
            roi = img[:, :, y0:y1, x0:x1]
            img[:, :, y0:y1, x0:x1] = roi + alpha * (back.clamp(0, 1) - roi)
            self.faces_done += 1
        return img
