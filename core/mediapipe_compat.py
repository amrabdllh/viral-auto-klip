"""
MediaPipe compatibility layer (v3).

mediapipe >= 0.10.30 (satu-satunya yang punya wheel untuk Python 3.13/3.14)
MENGHAPUS API legacy `mp.solutions`. Modul ini otomatis memilih:
- Legacy mp.solutions (mediapipe < 0.10.30) — tanpa file model eksternal.
- Tasks API (mediapipe >= 0.10.30) — dengan auto-download model kecil ke models/mediapipe/.

Kedua jalur mengembalikan format yang sama, sehingga clipper.py tidak perlu tahu
versi mediapipe mana yang terpasang.
"""
import urllib.request
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
from loguru import logger

import config

_MODEL_REGISTRY = {
    "face_detector": (
        "blaze_face_short_range.tflite",
        "https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite",
    ),
    "selfie_segmenter": (
        "selfie_segmenter.tflite",
        "https://storage.googleapis.com/mediapipe-models/image_segmenter/selfie_segmenter/float16/1/selfie_segmenter.tflite",
    ),
}

try:
    import mediapipe as mp
except ImportError:
    mp = None

_LEGACY = mp is not None and hasattr(mp, "solutions")


def _models_dir() -> Path:
    directory = config.BASE_DIR / "models" / "mediapipe"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def ensure_model(name: str) -> Optional[str]:
    """Pastikan file model MediaPipe Tasks tersedia (unduh bila perlu)."""
    if _LEGACY:
        return None  # legacy tidak butuh file model
    file_name, url = _MODEL_REGISTRY[name]
    target = _models_dir() / file_name
    if target.exists() and target.stat().st_size > 1000:
        return str(target)
    try:
        logger.info(f"Mengunduh model MediaPipe {name} (kecil)...")
        urllib.request.urlretrieve(url, target)
        return str(target)
    except Exception as exc:
        logger.warning(f"Gagal mengunduh model MediaPipe {name}: {str(exc)[:120]}")
        return None


_YUNET_URL = "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"


def _yunet_model() -> Optional[str]:
    target = _models_dir().parent / "face_detection_yunet_2023mar.onnx"
    if target.exists() and target.stat().st_size > 10000:
        return str(target)
    try:
        logger.info("Mengunduh model YuNet face detector (230 KB)...")
        urllib.request.urlretrieve(_YUNET_URL, target)
        return str(target)
    except Exception as exc:
        logger.warning(f"Gagal mengunduh YuNet: {str(exc)[:120]} — pakai MediaPipe.")
        return None


class YuNetFaceDetector:
    """OpenCV YuNet (cv2.FaceDetectorYN): akurat untuk wajah kecil/jauh (podcast wide shot) +
    5 landmark (mata, hidung, sudut mulut) untuk deteksi pembicara aktif.

    Diuji pada footage podcast 640x360: YuNet 100% frame terdeteksi vs MediaPipe short-range 49–72%.
    """

    def __init__(self, min_confidence: float = 0.6):
        import cv2
        model = _yunet_model()
        if not model or not hasattr(cv2, "FaceDetectorYN"):
            raise RuntimeError("YuNet tidak tersedia")
        self._cv2 = cv2
        self._detector = cv2.FaceDetectorYN.create(model, "", (320, 320), min_confidence, 0.3, 5000)
        self._size = (320, 320)

    def detect_full(self, bgr_frame) -> List[dict]:
        h, w = bgr_frame.shape[:2]
        if (w, h) != self._size:
            self._detector.setInputSize((w, h))
            self._size = (w, h)
        _, faces = self._detector.detect(bgr_frame)
        results = []
        for row in (faces if faces is not None else []):
            x, y, bw, bh = (int(v) for v in row[:4])
            mouth = ((float(row[10]), float(row[11])), (float(row[12]), float(row[13])))
            results.append({"box": (x, y, bw, bh), "score": float(row[14]), "mouth": mouth})
        return results

    def close(self):
        pass


class FaceDetectorCompat:
    """Deteksi wajah. v4: YuNet (OpenCV) dulu; fallback MediaPipe legacy/Tasks.

    detect(rgb) -> [(box, score)] (API lama); detect_full(rgb) -> [{box, score, mouth}].
    """

    def __init__(self, min_confidence: float = 0.5):
        self._yunet = None
        try:
            self._yunet = YuNetFaceDetector(min_confidence=max(min_confidence, 0.6))
            return
        except Exception as exc:
            logger.debug(f"YuNet tidak dipakai: {exc}")
        self._init_mediapipe(min_confidence)

    def detect_full(self, rgb_frame) -> List[dict]:
        if self._yunet is not None:
            bgr = self._yunet._cv2.cvtColor(rgb_frame, self._yunet._cv2.COLOR_RGB2BGR)
            return self._yunet.detect_full(bgr)
        return [{"box": box, "score": score, "mouth": None} for box, score in self._detect_mediapipe(rgb_frame)]

    def detect(self, rgb_frame) -> List[Tuple[Tuple[int, int, int, int], float]]:
        return [(f["box"], f["score"]) for f in self.detect_full(rgb_frame)]

    def _init_mediapipe(self, min_confidence: float):
        if mp is None:
            raise RuntimeError("mediapipe tidak terpasang dan YuNet tidak tersedia")
        self._legacy = _LEGACY
        if self._legacy:
            self._detector = mp.solutions.face_detection.FaceDetection(
                model_selection=1, min_detection_confidence=min_confidence
            )
            self._detector.__enter__()
        else:
            from mediapipe.tasks import python as mp_python
            from mediapipe.tasks.python import vision
            model_path = ensure_model("face_detector")
            if not model_path:
                raise RuntimeError("model face_detector tidak tersedia")
            options = vision.FaceDetectorOptions(
                base_options=mp_python.BaseOptions(model_asset_path=model_path),
                min_detection_confidence=min_confidence,
                running_mode=vision.RunningMode.IMAGE,
            )
            self._mp_python = mp_python
            self._detector = vision.FaceDetector.create_from_options(options)

    def _detect_mediapipe(self, rgb_frame) -> List[Tuple[Tuple[int, int, int, int], float]]:
        """Kembalikan daftar (box_piksel(x,y,w,h), skor)."""
        results: List[Tuple[Tuple[int, int, int, int], float]] = []
        if self._legacy:
            detections = (self._detector.process(rgb_frame).detections or [])
            h, w = rgb_frame.shape[:2]
            for detection in detections:
                bbox = detection.location_data.relative_bounding_box
                x = int(bbox.xmin * w)
                y = int(bbox.ymin * h)
                bw = int(bbox.width * w)
                bh = int(bbox.height * h)
                score = float(detection.score[0]) if detection.score else 0.5
                results.append(((x, y, bw, bh), score))
            return results

        data = np.ascontiguousarray(rgb_frame)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=data)
        detection_result = self._detector.detect(mp_image)
        h, w = rgb_frame.shape[:2]
        for detection in (detection_result.detections or []):
            bbox = detection.bounding_box
            x, y = float(bbox.origin_x), float(bbox.origin_y)
            bw, bh = float(bbox.width), float(bbox.height)
            # Tasks API: bila tampak ternormalisasi (<=1), konversi ke piksel.
            if x <= 1.0 and y <= 1.0 and bw <= 1.0 and bh <= 1.0:
                x, y, bw, bh = x * w, y * h, bw * w, bh * h
            score = float(detection.categories[0].score) if detection.categories else 0.5
            results.append(((int(x), int(y), int(bw), int(bh)), score))
        return results

    def close(self):
        if self._yunet is not None:
            return
        if self._legacy:
            self._detector.__exit__(None, None, None)
        else:
            self._detector.close()


class SelfieSegmenterCompat:
    """Segmentasi orang (selfie) untuk virtual background: legacy atau Tasks."""

    def __init__(self):
        if mp is None:
            raise RuntimeError("mediapipe tidak terpasang")
        self._legacy = _LEGACY
        if self._legacy:
            self._segmenter = mp.solutions.selfie_segmentation.SelfieSegmentation(model_selection=1)
            self._segmenter.__enter__()
        else:
            from mediapipe.tasks import python as mp_python
            from mediapipe.tasks.python import vision
            model_path = ensure_model("selfie_segmenter")
            if not model_path:
                raise RuntimeError("model selfie_segmenter tidak tersedia")
            options = vision.ImageSegmenterOptions(
                base_options=mp_python.BaseOptions(model_asset_path=model_path),
                running_mode=vision.RunningMode.IMAGE,
                output_category_mask=False,
                output_confidence_masks=True,
            )
            self._mp_python = mp_python
            self._segmenter = vision.ImageSegmenter.create_from_options(options)

    def segment_mask(self, rgb_frame) -> np.ndarray:
        """Kembalikan mask confidence float32 HxW bernilai [0,1]."""
        if self._legacy:
            return self._segmenter.process(rgb_frame).segmentation_mask
        data = np.ascontiguousarray(rgb_frame)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=data)
        result = self._segmenter.segment(mp_image)
        if not result.confidence_masks:
            raise RuntimeError("segmentasi gagal menghasilkan mask")
        mask = np.asarray(result.confidence_masks[0].numpy_view(), dtype=np.float32)
        if mask.ndim == 3 and mask.shape[2] == 1:
            mask = mask[:, :, 0]
        return mask

    def close(self):
        if self._legacy:
            self._segmenter.__exit__(None, None, None)
        else:
            self._segmenter.close()
