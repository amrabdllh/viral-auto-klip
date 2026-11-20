"""Super-resolution + restorasi wajah AI untuk klip vertikal hasil reframe.

Mengapa di sini: sumber podcast 1080p yang berbingkai hanya menyisakan area gambar ±784 px. Crop 9:16 darinya
(±441x784) harus diperbesar ±2,4x ke 1080x1920; interpolasi biasa (bicubic/Lanczos) hanya membesarkan blok kompresi
YouTube.

Jalur utama (v6.8, PyTorch CUDA, semua di memori GPU — tanpa file PNG perantara):
  frame diperkecil ke 432x768 (≈ informasi asli crop) → Real-ESRGAN `realesr-general-x4v3` (model untuk video nyata,
  bukan anime) fp16 → 1728x3072 → bicubic antialias ke 1080x1920 → GFPGAN v1.4 untuk wajah (core/face_restore.py)
  → NVENC. Uji pada klip podcast: kacamata, bulu mata, tekstur kulit kembali; tidak lagi "kulit lilin".
Cadangan: biner Real-ESRGAN ncnn-vulkan (realesr-animevideov3 x2) bila PyTorch CUDA / model tidak ada.
Bila semuanya gagal, klip tetap diproses tanpa tahap ini.
"""
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import List, Optional

from loguru import logger

import config

_EXE = Path(config.BASE_DIR) / "tools" / "realesrgan" / "realesrgan-ncnn-vulkan.exe"
_NCNN_MODEL = "realesr-animevideov3"
_SR_MODEL = Path(config.BASE_DIR) / "tools" / "models" / "realesr-general-x4v3.pth"
_SR_URL = "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-general-x4v3.pth"
_GPU_LOCK = threading.Lock()  # satu klip sekali jalan di GPU: render paralel tidak berebut VRAM
_LOAD_LOCK = threading.Lock()
_SR_NET = None
_IN_W, _IN_H, _OUT_W, _OUT_H = 432, 768, 1080, 1920


def ensure_weights(path: Path, url: str, min_bytes: int) -> bool:
    """Unduh bobot model sekali (rilis resmi GitHub) bila belum ada. False bila gagal."""
    if path.exists() and path.stat().st_size >= min_bytes:
        return True
    with _LOAD_LOCK:
        if path.exists() and path.stat().st_size >= min_bytes:
            return True
        try:
            import urllib.request
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".part")
            logger.info(f"Mengunduh model AI {path.name} (sekali saja)...")
            urllib.request.urlretrieve(url, tmp)
            if tmp.stat().st_size < min_bytes:
                raise RuntimeError(f"ukuran tidak wajar ({tmp.stat().st_size} B)")
            tmp.replace(path)
            return True
        except Exception as exc:
            logger.warning(f"Gagal mengunduh {path.name}: {str(exc)[:120]}")
            return False


_CUDA_OK = None


def _torch_ready() -> bool:
    """PyTorch CUDA + spandrel terpasang (bobot model diunduh otomatis saat pertama dipakai)."""
    global _CUDA_OK
    if _CUDA_OK is not None:
        return _CUDA_OK
    try:
        import torch
        import spandrel  # noqa: F401
        _CUDA_OK = bool(torch.cuda.is_available())
    except Exception:
        _CUDA_OK = False
    return _CUDA_OK


def available() -> bool:
    return bool(getattr(config, "AI_UPSCALE", True)) and (_torch_ready() or _EXE.exists())


def engine() -> str:
    """Nama mesin yang akan dipakai (untuk log/metadata)."""
    if not getattr(config, "AI_UPSCALE", True):
        return ""
    if _torch_ready():
        from . import face_restore
        return "realesr-general-x4v3" + (" + GFPGAN" if face_restore.available() else "")
    return _NCNN_MODEL if _EXE.exists() else ""


def _sr_net():
    global _SR_NET
    if _SR_NET is None and not ensure_weights(_SR_MODEL, _SR_URL, 4_000_000):
        raise RuntimeError("model realesr-general-x4v3 tidak tersedia")
    with _LOAD_LOCK:
        if _SR_NET is None:
            import torch
            from spandrel import ModelLoader
            torch.backends.cudnn.benchmark = True
            m = ModelLoader().load_from_file(str(_SR_MODEL))
            _SR_NET = m.model.cuda().eval().half().to(memory_format=torch.channels_last)
    return _SR_NET


def _enhance_torch(src: str, dst: str, encode_args: List[str], fps: float) -> Optional[str]:
    import numpy as np
    import torch
    import torch.nn.functional as F
    from . import face_restore

    net = _sr_net()
    restorer = None
    if face_restore.available():
        try:
            restorer = face_restore.FaceRestorer()
        except Exception as exc:
            logger.warning(f"Restorasi wajah dilewati: {str(exc)[:160]}")
    frame_bytes = _IN_W * _IN_H * 3
    dec = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-i", src, "-vf", f"scale={_IN_W}:{_IN_H}:flags=area",
                            "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=frame_bytes * 4)
    enc = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                            "-s", f"{_OUT_W}x{_OUT_H}", "-framerate", f"{fps:.5f}", "-i", "-", "-i", src,
                            "-map", "0:v", "-map", "1:a?", *encode_args, "-c:a", "copy", "-shortest", dst],
                           stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    # baca & tulis di thread terpisah: GPU tidak menunggu dekode/enkode
    q_in: "queue.Queue" = queue.Queue(maxsize=8)
    q_out: "queue.Queue" = queue.Queue(maxsize=8)
    err: List[BaseException] = []

    def reader():
        try:
            while True:
                buf = dec.stdout.read(frame_bytes)
                if len(buf) < frame_bytes:
                    break
                q_in.put(buf)
        finally:
            q_in.put(None)

    def writer():
        try:
            while True:
                item = q_out.get()
                if item is None:
                    break
                enc.stdin.write(item)
        except BaseException as exc:  # encoder mati → hentikan loop utama
            err.append(exc)
            while q_out.get() is not None:
                pass
        finally:
            try:
                enc.stdin.close()
            except Exception:
                pass

    tr, tw = threading.Thread(target=reader, daemon=True), threading.Thread(target=writer, daemon=True)
    tr.start()
    tw.start()
    n, t0 = 0, time.time()
    try:
        with torch.inference_mode():
            while True:
                buf = q_in.get()
                if buf is None or err:
                    break
                small = np.frombuffer(buf, np.uint8).reshape(_IN_H, _IN_W, 3)
                x = torch.from_numpy(small.copy()).cuda().permute(2, 0, 1)[None][:, [2, 1, 0]]
                x = x.half().div_(255).contiguous(memory_format=torch.channels_last)
                y = net(x).float()
                y = F.interpolate(y, size=(_OUT_H, _OUT_W), mode="bicubic", antialias=True, align_corners=False)
                y = y.clamp_(0, 1)
                if restorer is not None:
                    y = restorer.process(y, small)
                out = y.mul_(255).round_().byte()[0].permute(1, 2, 0).contiguous().cpu().numpy()
                q_out.put(out.tobytes())
                n += 1
    finally:
        q_out.put(None)
        tw.join()
        try:
            dec.kill()
        except Exception:
            pass
        dec.wait()
        _, enc_err = enc.communicate()
    if err or enc.returncode != 0 or n == 0:
        raise RuntimeError(f"enkode gagal ({n} frame): {(enc_err or b'').decode('utf-8', 'replace')[-200:]} {err[:1]}")
    dt = time.time() - t0
    faces = f", wajah dipulihkan di {restorer.faces_done} titik" if restorer else ""
    logger.info(f"  Upscale AI (realesr-general-x4v3{' + GFPGAN' if restorer else ''}): {n} frame, {n / dt:.1f} fps{faces}")
    return dst


def _upscale_ncnn(src: str, dst: str, encode_args: List[str], fps: float) -> Optional[str]:
    work = Path(dst).with_suffix(".sr")
    fin, fout = work / "in", work / "out"
    try:
        shutil.rmtree(work, ignore_errors=True)
        fin.mkdir(parents=True)
        fout.mkdir(parents=True)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-vf", "scale=540:960:flags=area",
                        "-fps_mode", "passthrough", str(fin / "%06d.png")], check=True, capture_output=True)
        res = subprocess.run([str(_EXE), "-i", str(fin), "-o", str(fout), "-n", _NCNN_MODEL, "-s", "2", "-f", "png"],
                             capture_output=True, text=True, errors="replace", timeout=1800)
        n_in, n_out = len(list(fin.glob("*.png"))), len(list(fout.glob("*.png")))
        if res.returncode != 0 or n_out == 0 or n_out != n_in:
            logger.warning(f"Upscale AI dilewati (rc={res.returncode}, {n_out}/{n_in} frame): {(res.stderr or '')[-200:]}")
            return None
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", f"{fps:.5f}", "-i", str(fout / "%06d.png"),
                        "-i", src, "-map", "0:v", "-map", "1:a?", "-vf", "scale=1080:1920:flags=lanczos",
                        *encode_args, "-c:a", "copy", "-shortest", dst], check=True, capture_output=True)
        return dst
    finally:
        shutil.rmtree(work, ignore_errors=True)


def upscale_clip(src: str, dst: str, encode_args: List[str], fps: float = 30.0) -> Optional[str]:
    """Naikkan resolusi klip 1080x1920 → bersih & tajam (+ wajah dipulihkan). Return `dst`, atau None bila dilewati."""
    if not available():
        return None
    with _GPU_LOCK:
        if _torch_ready():
            try:
                return _enhance_torch(src, dst, encode_args, fps)
            except Exception as exc:
                logger.warning(f"Upscale AI PyTorch gagal → pakai ncnn: {str(exc)[:200]}")
        if not _EXE.exists():
            return None
        try:
            return _upscale_ncnn(src, dst, encode_args, fps)
        except Exception as exc:
            detail = getattr(exc, "stderr", b"") or b""
            detail = detail.decode("utf-8", "replace") if isinstance(detail, bytes) else str(detail)
            logger.warning(f"Upscale AI dilewati: {str(exc)[:120]} {detail[-200:]}")
            return None
