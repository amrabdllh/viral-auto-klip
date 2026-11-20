"""
Kamera pembicara (v6.4) — podcast/obrolan 2 orang dipotong seperti editor manusia.

- Siapa yang bicara diambil dari label pembicara ElevenLabs Scribe (diarization) per kata, lalu
  dicocokkan ke wajah kiri/kanan lewat voting gerak mulut (_SpeakerTracker di clipper).
- Tiap giliran bicara = shot close-up pembicara itu, dengan potongan keras saat giliran berganti.
- Saling sahut cepat (≥3 giliran pendek berturut-turut) = layar dibagi atas/bawah (dua orang terlihat).
- Monolog panjang (>9 dtk) diselingi 1 shot reaksi pendengar ±1.3 dtk di jeda kalimat.

Bila label pembicara tidak ada / hanya 1 orang / wajah tidak bisa dipetakan dengan yakin → None,
dan klip memakai reframe biasa.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

import cv2
import numpy as np
from loguru import logger

SAMPLE_RATE = 3  # detect_faces_in_clip mengambil 1 dari tiap 3 frame


def _center_x(box) -> float:
    return box[0] + box[2] / 2


def face_clusters(meta: List[Dict], width: int) -> Optional[Dict]:
    """Dua posisi wajah tetap (kiri/kanan). None bila bukan setup 2 orang berjauhan."""
    if not meta:
        return None
    boxes = [b for m in meta if m for b in (m.get("all_faces") or []) if b]
    two = sum(1 for m in meta if m and len(m.get("all_faces") or []) >= 2)
    if len(boxes) < 8 or two < 0.3 * len(meta):
        return None
    xs = np.array([_center_x(b) for b in boxes], dtype=np.float32)
    c = np.array([np.percentile(xs, 15), np.percentile(xs, 85)], dtype=np.float32)
    for _ in range(12):
        lab = np.abs(xs[:, None] - c[None, :]).argmin(axis=1)
        for k in (0, 1):
            if (lab == k).any():
                c[k] = xs[lab == k].mean()
    if abs(c[1] - c[0]) < 0.22 * width:
        return None
    lab = np.abs(xs[:, None] - c[None, :]).argmin(axis=1)
    out = {}
    for k, side in ((int(np.argmin(c)), "L"), (int(np.argmax(c)), "R")):
        sel = np.array([b for b, lb in zip(boxes, lab) if lb == k], dtype=np.float32)
        if len(sel) < 3:
            return None
        out[side] = tuple(float(v) for v in np.median(sel, axis=0))
    out["xL"], out["xR"] = _center_x(out["L"]), _center_x(out["R"])
    return out


def _speaker_at(words: List[Dict], t: float) -> Optional[str]:
    for w in words:
        if w["start"] - 0.05 <= t <= w["end"] + 0.25:
            return w.get("speaker")
        if w["start"] > t + 0.3:
            break
    return None


def plan_speaker_shots(words_rel: List[Dict], meta: List[Dict], fps: float, duration: float, width: int) -> Optional[Dict]:
    """Rencana shot: {"shots": [{t0, t1, mode: single|stacked, side}], "clusters": …}."""
    spk_words = [w for w in words_rel if w.get("speaker")]
    counts: Dict[str, int] = {}
    for w in spk_words:
        counts[w["speaker"]] = counts.get(w["speaker"], 0) + 1
    top = sorted(counts, key=lambda k: -counts[k])[:2]
    if len(top) < 2 or counts[top[1]] < 6:
        return None
    clusters = face_clusters(meta, width)
    if not clusters:
        return None
    # voting: wajah aktif (gerak mulut) saat speaker X bicara → X ada di sisi itu
    votes = {s: {"L": 0, "R": 0} for s in top}
    for i, m in enumerate(meta):
        box = (m or {}).get("main_box")
        if not box:
            continue
        spk = _speaker_at(spk_words, i * SAMPLE_RATE / fps)
        if spk in votes:
            side = "L" if abs(_center_x(box) - clusters["xL"]) < abs(_center_x(box) - clusters["xR"]) else "R"
            votes[spk][side] += 1
    a, b = top
    straight = votes[a]["L"] + votes[b]["R"]
    crossed = votes[a]["R"] + votes[b]["L"]
    total = straight + crossed
    if total < 8 or max(straight, crossed) < 0.58 * total:
        logger.info(f"  Kamera pembicara: pemetaan suara→wajah tidak yakin ({straight}/{crossed}) — reframe biasa")
        return None
    side_of = {a: "L", b: "R"} if straight >= crossed else {a: "R", b: "L"}

    # giliran bicara
    turns: List[Dict] = []
    for w in spk_words:
        if w["speaker"] not in side_of:
            continue
        if turns and turns[-1]["spk"] == w["speaker"]:
            turns[-1]["end"] = w["end"]
            turns[-1]["words"].append(w)
        else:
            turns.append({"spk": w["speaker"], "start": w["start"], "end": w["end"], "words": [w]})
    if len(turns) < 2:
        return None

    shots: List[Dict] = []
    i = 0
    while i < len(turns):
        j = i
        while j < len(turns) and turns[j]["end"] - turns[j]["start"] < 2.0:
            j += 1
        if j - i >= 3:  # saling sahut cepat → dua orang sekaligus
            shots.append({"t0": turns[i]["start"], "mode": "stacked", "side": ""})
            i = j
            continue
        t = turns[i]
        side = side_of[t["spk"]]
        shots.append({"t0": t["start"], "mode": "single", "side": side})
        if t["end"] - t["start"] > 9.0:  # shot reaksi pendengar di jeda dekat tengah giliran
            mid = (t["start"] + t["end"]) / 2
            gaps = [(t["words"][k + 1]["start"] - t["words"][k]["end"], t["words"][k]["end"])
                    for k in range(len(t["words"]) - 1)]
            gaps = [(g, at) for g, at in gaps if g >= 0.15 and t["start"] + 3 < at < t["end"] - 3]
            if gaps:
                at = min(gaps, key=lambda ga: abs(ga[1] - mid) - ga[0] * 2)[1]
                other = "R" if side == "L" else "L"
                shots.append({"t0": at + 0.05, "mode": "single", "side": other, "reaction": True})
                shots.append({"t0": at + 1.35, "mode": "single", "side": side})
        i += 1

    # rapikan: cut sedikit sebelum orang mulai bicara, shot minimal 1 dtk, gabung yang sama
    for s in shots:
        s["t0"] = max(0.0, s["t0"] - 0.12)
    shots.sort(key=lambda s: s["t0"])
    shots[0]["t0"] = 0.0
    merged: List[Dict] = []
    for s in shots:
        if merged and (s["mode"], s["side"]) == (merged[-1]["mode"], merged[-1]["side"]):
            continue
        if merged and s["t0"] - merged[-1]["t0"] < 1.0:
            if len(merged) == 1:
                merged[-1] = {**s, "t0": 0.0}
                continue
            merged.pop()  # shot sebelumnya terlalu singkat
            if merged and (s["mode"], s["side"]) == (merged[-1]["mode"], merged[-1]["side"]):
                continue
        merged.append(s)
    for k, s in enumerate(merged):
        s["t1"] = merged[k + 1]["t0"] if k + 1 < len(merged) else duration + 1
    stacked = sum(s["t1"] - s["t0"] for s in merged if s["mode"] == "stacked")
    logger.info(f"  Kamera pembicara: {len(merged)} shot, split {stacked:.1f} dtk, "
                f"{sum(1 for s in merged if s.get('reaction'))} reaksi")
    return {"shots": merged, "clusters": clusters, "side_of": side_of}


def _closeup_crop(box, width: int, height: int, out_ratio: float):
    from .clipper import _crop_size_for_shot
    base_w = min(width, int(height * out_ratio))
    base_h = min(height, int(base_w / out_ratio))
    return _crop_size_for_shot("closeup", box, base_w, base_h, width, height, out_ratio, True)


def _panel_crop(box, width: int, height: int, panel_ratio: float):
    fx, fy, fw, fh = box
    ch = min(height, max(fh * 2.6, height * 0.42))
    cw = ch * panel_ratio
    if cw > width:
        cw, ch = width, width / panel_ratio
    cx, cy = fx + fw / 2, fy + fh * 0.62
    x = int(np.clip(cx - cw / 2, 0, width - cw))
    y = int(np.clip(cy - ch * 0.46, 0, height - ch))
    return x, y, int(cw), int(ch)


def render_speaker_cam(input_path: str, output_path: str, meta: List[Dict], plan: Dict,
                       out_w: int = 1080, out_h: int = 1920) -> List[tuple]:
    """Render per frame sesuai rencana shot. Return interval split [(t0, t1)] untuk posisi caption."""
    from .clipper import _FFmpegFrameWriter, _apply_caption_safe_y, _resize_interp, mux_video_with_source_audio
    cap = cv2.VideoCapture(input_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    width, height = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    temp_video = str(Path(output_path).with_name(Path(output_path).stem + "_video_only.mp4"))
    writer = _FFmpegFrameWriter(temp_video, out_w, out_h, fps)
    shots, cl = plan["shots"], plan["clusters"]
    out_ratio = out_w / out_h
    panel_h = out_h // 2
    panel_ratio = out_w / panel_h
    pan = {side: _panel_crop(cl[side], width, height, panel_ratio) for side in ("L", "R")}
    pan_ema: Dict[str, np.ndarray] = {}
    stacked_used: List[tuple] = []  # rentang yang benar-benar dirender split (untuk posisi caption)
    last_center = last_crop = None
    last_shot = -1
    frame_idx = 0
    k = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            t = frame_idx / fps
            while k + 1 < len(shots) and t >= shots[k + 1]["t0"]:
                k += 1
            shot = shots[k]
            si = int(min(max(round(frame_idx / SAMPLE_RATE), 0), len(meta) - 1)) if meta else 0
            faces = [b for b in ((meta[si] or {}).get("all_faces") or [])] if meta else []
            two = sorted(faces, key=_center_x)
            if shot["mode"] == "stacked" and len(two) >= 2 and _center_x(two[-1]) - _center_x(two[0]) > 0.22 * width:
                # crop panel per frame dari wajah yang benar-benar terlihat (sumber multi-kamera ikut aman)
                for side, box in (("L", two[0]), ("R", two[-1])):
                    new = np.array(_panel_crop(box, width, height, panel_ratio), dtype=np.float32)
                    old = pan_ema.get(side)
                    pan_ema[side] = new if old is None or abs(new[0] - old[0]) > 0.12 * width else 0.85 * old + 0.15 * new
                    pan[side] = tuple(int(v) for v in pan_ema[side])
                panels = []
                for side in ("L", "R"):
                    x, y, cw, ch = pan[side]
                    crop = frame[y:y + ch, x:x + cw]
                    panels.append(cv2.resize(crop, (out_w, panel_h), interpolation=_resize_interp(crop, out_w)))
                out = np.vstack(panels)
                if out.shape[0] != out_h:
                    out = cv2.resize(out, (out_w, out_h))
                cv2.rectangle(out, (0, panel_h - 4), (out_w, panel_h + 4), (20, 20, 20), -1)
                writer.write(out)
                if stacked_used and t - stacked_used[-1][1] < 0.2:
                    stacked_used[-1][1] = t
                else:
                    stacked_used.append([t, t])
                last_shot = -1
                frame_idx += 1
                continue
            # close-up: wajah sisi itu di sampel terdekat; bila tidak terlihat (sumber sedang menampilkan
            # angle lain) ikuti wajah terbesar yang tampil; tanpa wajah sama sekali baru pakai posisi median
            side = shot["side"] or ("L" if k % 2 else "R")
            ref_x = cl["x" + side]
            near = [b for b in faces if abs(_center_x(b) - ref_x) < 0.14 * width]
            if near:
                box = min(near, key=lambda b: abs(_center_x(b) - ref_x))
            elif faces:
                box = max(faces, key=lambda b: b[2] * b[3])
            else:
                box = cl[side]
            target_center = np.array([box[0] + box[2] / 2, box[1] + box[3] * 0.58], dtype=np.float32)
            target_crop = np.array(_closeup_crop(box, width, height, out_ratio), dtype=np.float32)
            jump = last_center is not None and abs(float(target_center[0] - last_center[0])) > 0.2 * width
            if k != last_shot or last_center is None or jump:  # potongan keras: ganti shot / angle sumber berganti
                last_center, last_crop, last_shot = target_center, target_crop, k
            else:
                last_center = 0.88 * last_center + 0.12 * target_center
                last_crop = 0.9 * last_crop + 0.1 * target_crop
            cw, ch = int(np.clip(last_crop[0], 16, width)), int(np.clip(last_crop[1], 16, height))
            x = int(np.clip(last_center[0] - cw / 2, 0, max(width - cw, 0)))
            y = _apply_caption_safe_y(int(np.clip(last_center[1] - ch / 2, 0, max(height - ch, 0))), ch, height)
            crop = frame[y:y + ch, x:x + cw]
            writer.write(cv2.resize(crop, (out_w, out_h), interpolation=_resize_interp(crop, out_w)))
            frame_idx += 1
    finally:
        cap.release()
        writer.close()
    mux_video_with_source_audio(temp_video, input_path, output_path)
    Path(temp_video).unlink(missing_ok=True)
    return [(a, b + 0.1) for a, b in stacked_used if b - a >= 0.5]
