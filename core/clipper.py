"""
Video Clipping, Face Reframing, Captioning & Encoding Engine
"""
import os
import re
import json
import subprocess
import tempfile
import shutil
import threading
from pathlib import Path
from typing import List, Dict, Tuple, Optional
import cv2
import numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
from loguru import logger
import config
from .utils import generate_ass_subtitles, smooth_face_trajectory, create_thumbnail, get_video_info
from .fyp_agent import enrich_clip_for_platforms, build_upload_strategy
from .audio_enhancer import enhance_audio, add_background_music_with_duck
from .mediapipe_compat import FaceDetectorCompat, SelfieSegmenterCompat
from . import editing
from .framing import detect_content_rect
from . import upscale

_NVENC_AVAILABLE = None
_NVENC_LOCK = threading.Lock()


def _video_encode_args(crf: int = 20, fast: bool = False) -> List[str]:
    """Argumen encoder video untuk RTX 3060.

    v6.7 (kualitas maksimal):
    - File antara (fast=True): NVENC p1 tapi kualitas sangat tinggi (cq 14) agar tiap generasi encode tidak
      menambah blok — dulu cq 19 per generasi, padahal klip melewati 4-6 encode berturut-turut.
    - Hasil akhir: preset p7 (paling lambat/terbaik), tune hq, VBR + lookahead 32, AQ spasial & temporal,
      3 B-frame sebagai referensi, multipass penuh. GPU 3060 masih jauh dari penuh dengan ini.
    - ENCODE_CQ (opsional): override CQ/CRF, mis. 18–20 untuk file lebih kecil. 0 = ikut codebase.
    """
    cq_override = int(getattr(config, "ENCODE_CQ", 0) or 0)
    if cq_override:
        crf = max(1, min(51, cq_override))
    if config.USE_NVENC and _has_nvenc():
        if fast:
            return ["-c:v", "h264_nvenc", "-preset", "p1", "-rc", "vbr", "-cq", str(cq_override or 14), "-b:v", "0", "-pix_fmt", "yuv420p"]
        cq = cq_override or max(14, min(28, crf - 1))
        return [
            "-c:v", "h264_nvenc", "-preset", config.NVENC_PRESET, "-tune", "hq", "-profile:v", "high",
            "-rc", "vbr", "-cq", str(cq), "-b:v", "0", "-maxrate", "40M", "-bufsize", "80M",
            "-rc-lookahead", "32", "-spatial-aq", "1", "-temporal-aq", "1", "-aq-strength", "8",
            "-bf", "3", "-b_ref_mode", "middle", "-multipass", "fullres",
            "-pix_fmt", "yuv420p",
        ]
    return ["-c:v", "libx264", "-preset", "ultrafast" if fast else config.PRESET, "-crf", str(crf)]


def _has_nvenc() -> bool:
    # FIX: lock agar smoke test tidak berjalan ganda saat render paralel.
    with _NVENC_LOCK:
        return _has_nvenc_locked()


def _has_nvenc_locked() -> bool:
    global _NVENC_AVAILABLE
    if _NVENC_AVAILABLE is not None:
        return _NVENC_AVAILABLE
    try:
        encoders = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            check=True,
        )
        if "h264_nvenc" not in encoders.stdout:
            _NVENC_AVAILABLE = False
            if config.USE_NVENC:
                logger.warning("USE_NVENC aktif, tapi FFmpeg tidak menyediakan h264_nvenc. Fallback ke libx264.")
        else:
            smoke_path = config.TEMP_DIR / "nvenc_smoke.mp4"
            test = subprocess.run(
                [
                    "ffmpeg", "-hide_banner", "-y",
                    # FIX: dulu 64x64 @1fps selama 0.1 dtk = 0 frame → tes selalu gagal dan render jatuh ke CPU.
                    # NVENC juga butuh resolusi minimal ±145px.
                    "-f", "lavfi", "-i", "color=s=256x256:r=30",
                    "-frames:v", "10",
                    "-c:v", "h264_nvenc",
                    str(smoke_path),
                ],
                capture_output=True,
                text=True,
            )
            _NVENC_AVAILABLE = test.returncode == 0
            if smoke_path.exists():
                try:
                    smoke_path.unlink()
                except OSError:
                    pass
            if config.USE_NVENC and not _NVENC_AVAILABLE:
                stderr_tail = test.stderr[-500:].strip()
                if "minimum required Nvidia driver" in test.stderr:
                    logger.warning(
                        "NVENC tersedia di FFmpeg, tapi driver NVIDIA terlalu lama untuk build FFmpeg ini. "
                        "Update driver NVIDIA ke 570+ agar export bisa memakai GPU. Fallback ke libx264."
                    )
                else:
                    logger.warning(f"NVENC tersedia, tapi smoke test encoder gagal. Fallback ke libx264. {stderr_tail}")
    except Exception:
        _NVENC_AVAILABLE = False
        if config.USE_NVENC:
            logger.warning("Gagal mengecek NVENC. Fallback ke libx264.")
    return _NVENC_AVAILABLE

def process_clips(
    video_path: str,
    clips_data: List[Dict],
    output_dir: Path,
    style_name: str = "modern",
    caption_overrides: Optional[Dict] = None,
    enable_face_reframe: bool = True,
    add_watermark: bool = False,
    target_platform: str = "Semua",
    enable_audio_enhance: bool = True,
    background_music_path: str = "",
    auto_duck: bool = True,
    conversation_mode: bool = False,
    target_aspect: str = "portrait",
    auto_zoom: bool = True,
    split_screen: bool = False,
    bg_blur: bool = False,
    group_framing: bool = False,
    klipers_reframe: bool = False,
    eye_contact_correction: bool = False,
    virtual_background_path: str = "",
    enable_captions: bool = True,
    edit_opts: Optional[Dict] = None,
    layout: str = "auto",
    lower_third: Optional[Dict] = None,
    stream_layout: Optional[Dict] = None,
) -> List[Dict]:
    """
    Process each viral clip: extract, reframe, caption, encode.
    v3: paralel (ThreadPoolExecutor), scene-snap boundaries, peak-moment thumbnail.
    v6.4: layout "auto" mendeteksi facecam streamer → split (wajah atas, gameplay bawah).
    Returns list of processed clip info with file paths.
    """
    logger.info(f"🎬 Processing {len(clips_data)} viral clips...")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    style = dict(config.CAPTION_STYLES.get(style_name, config.CAPTION_STYLES["modern"]))
    if caption_overrides:
        style.update({key: value for key, value in caption_overrides.items() if value is not None})

    video_info = get_video_info(video_path)
    scene_cuts = _detect_scene_cuts(video_path, clips_data)

    # stream_layout eksplisit (dict) menang: campaign/live sudah menentukan kotak
    # facecam sendiri (rect preset atau deteksi per jendela). layout="off" = tanpa split.
    preset_layout = stream_layout if isinstance(stream_layout, dict) else None
    stream_layout = None
    if preset_layout is not None:
        stream_layout = preset_layout
    elif layout != "off" and layout in ("auto", "gaming") and target_aspect == "portrait" \
            and not split_screen and not group_framing:
        try:
            stream_layout = detect_stream_layout(video_path, clips_data, force=layout == "gaming")
        except Exception as exc:
            logger.warning(f"Deteksi layout facecam gagal: {exc}")
    if stream_layout:
        logger.info(f"  Layout gaming: facecam {stream_layout['cam_box']} → split wajah/gameplay")
        # Posisi caption: layout campaign boleh menetapkan sendiri (mis. di bawah
        # gameplay, tepat di atas banner). Default: di sambungan facecam/gameplay.
        caption_y = stream_layout.get("caption_y")
        if caption_y is not None:
            style["vertical_position"] = max(80, min(1800, int(caption_y)))
            logger.info(f"  Caption Y (margin bawah): {style['vertical_position']}px")
        else:
            seam = int(1920 * float(stream_layout.get("cam_ratio", 0.38)))
            style["vertical_position"] = max(200, 1920 - seam - 150)
        if stream_layout.get("caption_font_size"):
            style["font_size"] = max(40, min(110, int(stream_layout["caption_font_size"])))
            logger.info(f"  Caption font: {style['font_size']}px")

    content_rect = None
    if not stream_layout:  # sumber berbingkai (kartu video + border hitam + branding) → crop hanya gambarnya
        try:
            content_rect = detect_content_rect(video_path)
        except Exception as exc:
            logger.warning(f"Deteksi bingkai video dilewati: {exc}")

    opts = {
        "upscale": upscale.available(),
        "content_rect": content_rect,
        "enable_face_reframe": enable_face_reframe,
        "add_watermark": add_watermark,
        "target_platform": target_platform,
        "enable_audio_enhance": enable_audio_enhance,
        "background_music_path": background_music_path,
        "auto_duck": auto_duck,
        "conversation_mode": conversation_mode,
        "target_aspect": target_aspect,
        "auto_zoom": auto_zoom,
        "split_screen": split_screen,
        "bg_blur": bg_blur,
        "group_framing": group_framing,
        "klipers_reframe": klipers_reframe,
        "eye_contact_correction": eye_contact_correction,
        "virtual_background_path": virtual_background_path,
        "enable_captions": enable_captions,
        "edit": editing.resolve_edit_opts(edit_opts),
        # v6.8: gaya "auto" → dipilih per klip (editing.auto_style)
        "edit_auto": str((edit_opts or {}).get("style") or "").lower() == "auto",
        "edit_overrides": dict(edit_opts or {}),
        "stream_layout": stream_layout,
        "lower_third": lower_third or {},
    }
    logger.info(f"  Gaya editing: {opts['edit']}")

    workers = max(1, int(config.PARALLEL_CLIPS)) if config.PARALLEL_PROCESSING else 1
    results: Dict[int, Optional[Dict]] = {}

    if workers > 1 and len(clips_data) > 1:
        logger.info(f"  Parallel rendering: {min(workers, len(clips_data))} workers")
        with ThreadPoolExecutor(max_workers=min(workers, len(clips_data))) as pool:
            futures = {
                pool.submit(_process_single_clip, idx, clip_info, output_dir, style, video_path, video_info, scene_cuts, opts): idx
                for idx, clip_info in enumerate(clips_data)
            }
            for future in tqdm(as_completed(futures), total=len(futures), desc="Clipping"):
                idx = futures[future]
                try:
                    _, clip = future.result()
                except Exception as exc:
                    logger.exception(f"Parallel clip worker {idx} crashed: {exc}")
                    clip = None
                results[idx] = clip
                _write_metadata(output_dir, video_path, results, target_platform, len(clips_data))
    else:
        for idx, clip_info in enumerate(tqdm(clips_data, desc="Clipping")):
            _, clip = _process_single_clip(idx, clip_info, output_dir, style, video_path, video_info, scene_cuts, opts)
            results[idx] = clip
            _write_metadata(output_dir, video_path, results, target_platform, len(clips_data))

    processed_clips = [results[idx] for idx in sorted(results) if results[idx] is not None]
    metadata_path = _write_metadata(output_dir, video_path, results, target_platform, len(clips_data), done=True)
    logger.success(f"🎉 All clips processed! Metadata saved to {metadata_path}")
    return processed_clips


def _write_metadata(output_dir: Path, video_path: str, results: Dict[int, Optional[Dict]], target_platform: str,
                    planned: int, done: bool = False) -> Path:
    """Tulis metadata.json SETIAP klip selesai (v4). Dulu hanya di akhir — bila proses berhenti
    di tengah (server ditutup/crash), caption & hashtag semua klip yang sudah jadi ikut hilang."""
    processed = [results[i] for i in sorted(results) if results[i] is not None]
    metadata = {
        "video_source": video_path,
        "status": "done" if done else "in_progress",
        "planned_clips": planned,
        "total_clips": len(processed),
        "fyp_summary": build_fyp_summary(processed),
        "upload_strategy": build_upload_strategy(processed, target_platform),
        "clips": processed,
    }
    metadata_path = output_dir / "metadata.json"
    tmp = output_dir / "metadata.json.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(json_safe(metadata), f, indent=2, ensure_ascii=False)
    os.replace(tmp, metadata_path)
    return metadata_path


def _process_single_clip(
    idx: int,
    clip_info: Dict,
    output_dir: Path,
    style: Dict,
    video_path: str,
    video_info: Dict,
    scene_cuts: Optional[List[float]],
    opts: Dict,
) -> Tuple[int, Optional[Dict]]:
    """Proses satu klip (bisa dijalankan paralel). Selalu kembali (idx, clip|None)."""
    try:
        clip_info = _snap_clip_to_scene_cuts(clip_info, scene_cuts)

        score = int(clip_info.get("score") or 0)
        clip_name = f"clip_{idx+1:03d}_" + (f"viral{score}_" if score else "") + str(clip_info.get("category") or "klip")
        clip_dir = output_dir / clip_name
        clip_dir.mkdir(exist_ok=True)

        raw_clip_path = clip_dir / "raw.mp4"
        final_clip_path = clip_dir / f"{clip_name}.mp4"
        subtitle_path = clip_dir / "captions.ass"
        thumb_path = clip_dir / "thumbnail.jpg"

        edit = opts["edit"]
        if opts.get("edit_auto"):
            chosen = editing.auto_style(clip_info)
            edit = editing.resolve_edit_opts({**opts["edit_overrides"], "style": chosen})
            clip_info["edit_style_auto"] = chosen
            logger.info(f"  Gaya editing otomatis: {chosen} (kategori {clip_info.get('category')})")
        layout = opts.get("stream_layout")
        # puncak sinyal (lonjakan chat/suara live) relatif terhadap awal klip
        laughs = [float(ev["start"]) for sg in clip_info.get("subtitle_segments") or []
                  for ev in sg.get("audio_events") or [] if re.search(r"laugh|tawa|ngakak|chuckl", str(ev.get("text", "")))]
        clip_info["signal_peaks_rel"] = sorted({
            round(float(p) - float(clip_info["start"]), 2) for p in list(clip_info.get("signal_peaks") or []) + laughs
            if float(clip_info["start"]) + 1 <= float(p) <= float(clip_info["end"]) - 0.5})
        if layout:
            clip_info["hook_y"] = 0.56

        # 1. Extract raw segment
        extract_segment(video_path, clip_info["start"], clip_info["end"], str(raw_clip_path), crop=opts.get("content_rect"))

        working_clip_path = raw_clip_path

        # 1b. (v4) Jump cut: buang jeda panjang & kata pengisi -> tempo rapat seperti editor manusia
        if edit["jump_cuts"]:
            words_rel = editing.relative_words(clip_info)
            clip_len = clip_info["end"] - clip_info["start"]
            intervals = editing.plan_jump_cuts(words_rel, clip_len, max_gap=config.JUMP_CUT_MAX_GAP)
            removed = clip_len - editing.kept_duration(intervals)
            if removed >= 0.4 and len(intervals) > 1 and editing.kept_duration(intervals) >= config.MIN_CLIP_DURATION * 0.7:
                cut_path = clip_dir / "raw_cut.mp4"
                editing.apply_jump_cuts(str(working_clip_path), str(cut_path), intervals, _video_encode_args(crf=18, fast=True))
                working_clip_path = cut_path
                peaks = clip_info.get("signal_peaks_rel") or []
                clip_info = editing.remap_clip_info(clip_info, intervals)
                clip_info["signal_peaks_rel"] = [editing.remap_time(p, intervals) for p in peaks]
                clip_info["jump_cut_removed_sec"] = round(removed, 2)
                logger.info(f"  Jump cut: {len(intervals)} potongan, {removed:.1f} dtk jeda/filler dibuang")

        # 1c. (v6.4) Cold open: kalimat paling nendang diputar dulu, lalu efek rewind ke awal cerita
        cold_shift = 0.0
        if edit.get("cold_open") and not clip_info.get("no_cold_open"):
            clip_len = clip_info["end"] - clip_info["start"]
            teaser = editing.plan_cold_open(editing.relative_words(clip_info), clip_len, clip_info.get("signal_peaks_rel") or [])
            if teaser:
                co_path = clip_dir / "cold_open.mp4"
                try:
                    cold_shift = editing.apply_cold_open(str(working_clip_path), str(co_path), teaser,
                                                         _video_encode_args(crf=18, fast=True))
                    working_clip_path = co_path
                    clip_info = editing.cold_open_clip_info(clip_info, teaser, cold_shift)
                    logger.info(f"  Cold open: teaser {teaser[0]:.1f}-{teaser[1]:.1f} dtk diputar di awal + rewind")
                except subprocess.CalledProcessError as exc:
                    cold_shift = 0.0
                    logger.warning(f"Cold open dilewati: {(exc.stderr or b'')[-300:]!r}")

        # 1d. (v6.4) Freeze frame + record scratch di puncak terkuat (preset maksimal)
        freeze_at = None
        if edit.get("freeze"):
            clip_len = clip_info["end"] - clip_info["start"]
            words_now = editing.relative_words(clip_info)
            at = editing.plan_freeze(clip_info.get("signal_peaks_rel") or [], editing.emphasis_moments(words_now, limit=3),
                                     clip_len, skip_until=cold_shift)
            if at:
                fz_path = clip_dir / "freeze.mp4"
                try:
                    extra = editing.apply_freeze(str(working_clip_path), str(fz_path), at, _video_encode_args(crf=18, fast=True))
                    working_clip_path = fz_path
                    clip_info = editing.freeze_clip_info(clip_info, at, extra)
                    freeze_at = at
                    logger.info(f"  Freeze frame di {at:.1f} dtk")
                except subprocess.CalledProcessError as exc:
                    logger.warning(f"Freeze frame dilewati: {(exc.stderr or b'')[-300:]!r}")
        if opts["virtual_background_path"]:
            bg_path = Path(opts["virtual_background_path"])
            if bg_path.exists():
                bg_clip_path = clip_dir / "virtual_bg.mp4"
                apply_virtual_background(str(working_clip_path), str(bg_clip_path), str(bg_path))
                working_clip_path = bg_clip_path
            else:
                logger.warning(f"Virtual background file not found: {opts['virtual_background_path']}")

        if opts["eye_contact_correction"]:
            eye_clip_path = clip_dir / "eye_contact.mp4"
            apply_eye_contact_correction(str(working_clip_path), str(eye_clip_path))
            working_clip_path = eye_clip_path

        # 2. Face detection & reframing (if enabled)
        face_bboxes = []
        reframed_path = clip_dir / "reframed.mp4"
        speaker_plan, stacked_zones = None, []
        if layout:
            # (v6.4) streamer: facecam di atas, gameplay di bawah (tanpa tracking wajah per frame)
            from . import ffx
            ffx.split_cam(str(working_clip_path), str(reframed_path), tuple(layout["cam_box"]), tuple(layout["game_box"]),
                          cam_ratio=layout["cam_ratio"], cam_top=True,
                          face=tuple(layout["face"][:2]) if layout.get("face") else None)
        elif opts["enable_face_reframe"]:
            face_bboxes = detect_faces_in_clip(
                str(working_clip_path),
                group_framing=opts["group_framing"],
                return_metadata=opts["klipers_reframe"],
            )
            if face_bboxes:
                logger.info(f"  Face detected in clip {idx+1}")
                # Frame rewind VHS (noise + geser warna) membuat detektor wajah menebak asal → crop melayang
                # ke mikrofon/dinding. Selama rewind, kamera ditahan di posisi terakhir teaser.
                if cold_shift and opts["klipers_reframe"]:
                    blank = {"box": None, "main_box": None, "group_box": None, "face_count": 0, "all_faces": []}
                    rw_len = editing.REWIND_FRAMES / editing.OUTPUT_FPS
                    _cap = cv2.VideoCapture(str(working_clip_path))
                    fps_src = float(_cap.get(cv2.CAP_PROP_FPS) or 30)
                    _cap.release()
                    step = 3  # sample_rate detect_faces_in_clip
                    lo = int((cold_shift - rw_len) * fps_src / step)
                    hi = int(cold_shift * fps_src / step) + 1
                    for k in range(max(lo, 0), min(hi, len(face_bboxes))):
                        if isinstance(face_bboxes[k], dict):
                            face_bboxes[k] = dict(blank)
            # (v6.4) kamera ikut pembicara dari label suara Scribe; saling sahut = layar dibagi
            if (edit.get("speaker_cam") and opts["klipers_reframe"] and face_bboxes and opts["target_aspect"] == "portrait"
                    and not opts["group_framing"] and not opts["split_screen"]):
                from . import camera
                words_now = editing.relative_words(clip_info)
                if len({w.get("speaker") for w in words_now if w.get("speaker")}) >= 2:
                    winfo = get_video_info(str(working_clip_path))
                    try:
                        speaker_plan = camera.plan_speaker_shots(words_now, face_bboxes, float(winfo.get("fps") or 30),
                                                                 clip_info["end"] - clip_info["start"], int(winfo.get("width") or 1920))
                        if speaker_plan:
                            stacked_zones = camera.render_speaker_cam(str(working_clip_path), str(reframed_path), face_bboxes, speaker_plan)
                    except Exception as exc:
                        logger.warning(f"Kamera pembicara dilewati: {exc}")
                        speaker_plan, stacked_zones = None, []

        # 3. Reframe (support Conversation Mode + Portrait/Landscape)
        reframe_kwargs = {
            "conversation_mode": opts["conversation_mode"],
            "target_aspect": opts["target_aspect"],
            "group_framing": opts["group_framing"],
            "klipers_reframe": opts["klipers_reframe"],
        }
        if layout or speaker_plan:
            pass  # sudah dirender di langkah 2 (split facecam/gameplay atau kamera pembicara)
        elif opts["enable_face_reframe"] and face_bboxes:
            reframe_video(
                str(working_clip_path),
                str(reframed_path),
                face_bboxes,
                get_video_info(str(working_clip_path)),
                auto_zoom=opts["auto_zoom"],
                split_screen=opts["split_screen"],
                bg_blur=opts["bg_blur"],
                **reframe_kwargs,
            )
        else:
            reframe_video(
                str(working_clip_path),
                str(reframed_path),
                [],
                get_video_info(str(working_clip_path)),
                **reframe_kwargs,
            )
        input_for_caption = str(reframed_path)
        # Super-resolution AI (Real-ESRGAN) pada hasil reframe portrait: ganti blok kompresi hasil upscale biasa
        if opts.get("upscale") and opts["target_aspect"] == "portrait":
            _cap = cv2.VideoCapture(str(reframed_path))
            _fps, _w, _h = float(_cap.get(cv2.CAP_PROP_FPS) or 30), int(_cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(_cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            _cap.release()
            if (_w, _h) == (1080, 1920):
                sr_path = upscale.upscale_clip(str(reframed_path), str(clip_dir / "reframed_sr.mp4"), _video_encode_args(crf=14), _fps)
                if sr_path:
                    input_for_caption = sr_path
                    clip_info["enhance"] = upscale.engine()
                    logger.info(f"  Upscale AI ({clip_info['enhance']}) diterapkan pada klip {idx+1}")

        # Audio Enhancement (Klipers.pro style)
        # v6.4: loudness akhir dikerjakan master_audio setelah SFX dicampur (bila audio_master aktif)
        enhanced_audio = None
        if opts["enable_audio_enhance"]:
            enhanced_audio = clip_dir / "enhanced.aac"
            enhance_audio(str(reframed_path), str(enhanced_audio), normalize=not edit.get("audio_master"))

        # 4. (v4) Polish render dalam SATU encode: color grade -> dynamic zoom -> caption -> progress bar.
        reframed_info = get_video_info(input_for_caption)
        out_w, out_h = int(reframed_info.get("width") or 1080), int(reframed_info.get("height") or 1920)
        clip_duration = clip_info["end"] - clip_info["start"]
        mute_spans: List[Tuple[float, float]] = []
        if edit.get("censor"):  # (v6.4) kata kasar: caption disamarkan, audio di-beep
            clip_info, mute_spans = editing.censor_words(clip_info)
            if mute_spans:
                logger.info(f"  Sensor: {len(mute_spans)} kata kasar di-beep")
        words_rel = editing.relative_words(clip_info)
        peaks_rel = clip_info.get("signal_peaks_rel") or []
        if freeze_at:  # momen freeze sudah jadi 'puncak'-nya — jangan ditumpuk impact/goyang lagi
            peaks_rel = [p for p in peaks_rel if not (freeze_at - 1.5 <= p <= freeze_at + editing.FREEZE_SEC + 1.8)]
        emphasis = editing.emphasis_moments(words_rel) if edit["dynamic_zoom"] else []
        if cold_shift:  # tidak ada punch di tengah rewind
            emphasis = [(s, e) for s, e in emphasis if s >= cold_shift + 1.0]
        if freeze_at:
            emphasis = [(s, e) for s, e in emphasis if not (freeze_at - 0.6 <= s <= freeze_at + editing.FREEZE_SEC + 0.6)]
        shakes: List[float] = []
        if edit.get("shake"):  # (v6.4) layar goyang di puncak (lonjakan chat/tawa/teriakan)
            for p in peaks_rel:
                if p >= cold_shift + 1.0 and p <= clip_duration - 1.0 and all(abs(p - q) >= 4 for q in shakes):
                    shakes.append(p)
            shakes = shakes[:2]
        zoom_chain = editing.zoom_filter(
            out_w, out_h, editing.shot_plan(words_rel, clip_duration), emphasis,
            dynamic=edit["dynamic_zoom"], hook_punch=config.ENABLE_HOOK_PUNCH,
            alt_shots=not layout and not speaker_plan, extra_punches=[cold_shift] if cold_shift else [],
            shakes=shakes,
        )
        broll: List[Dict] = []
        if edit.get("broll") and not layout and words_rel:  # (v6.4) gameplay sudah jadi visualnya
            from . import broll as broll_mod
            try:
                items = broll_mod.plan_broll(words_rel, clip_duration, skip_until=max(cold_shift, 2.8),
                                             title=clip_info.get("ai_title") or clip_info.get("hook_text") or "")
                if freeze_at:
                    items = [b for b in items if not (b["t"] - 1 <= freeze_at <= b["t"] + b["dur"] + 1)]
                broll = broll_mod.fetch_broll(items, clip_dir)
                if broll:
                    logger.info(f"  B-roll: {', '.join(b['query'] for b in broll)}")
            except Exception as exc:
                logger.warning(f"B-roll dilewati: {exc}")
        clip_style = dict(style)
        if stacked_zones:  # caption di sambungan dua panel agar tidak menutup wajah orang bawah
            clip_style["margin_zones"] = [(a, b, out_h // 2 - int(clip_style.get("font_size", 70)))
                                          for a, b in stacked_zones]
        stickers: List[Dict] = []
        if edit.get("caption_fx"):
            clip_style["fx"] = True
            clip_style["power_words"] = sorted(editing.power_word_set(words_rel))
            margin_v = int(clip_style.get("vertical_position", 420))
            size = int(out_w * 0.17)
            cy = max(size, out_h - margin_v - int(clip_style.get("font_size", 70)) * 2 - size // 2 - 20)
            busy = [(b["t"] - 0.3, b["t"] + b["dur"] + 0.3) for b in broll]
            if freeze_at:
                busy.append((freeze_at - 0.5, freeze_at + editing.FREEZE_SEC + 0.6))
            for k, st in enumerate(editing.plan_stickers(words_rel, clip_duration, skip_until=cold_shift)):
                if any(st["t"] <= b and st["t"] + st["dur"] >= a for a, b in busy):
                    continue
                png = editing.emoji_png(st["emoji"])
                if png:
                    cx = int(out_w * (0.76 if k % 2 == 0 else 0.24))
                    stickers.append({**st, "png": png, "cx": cx, "cy": cy, "size": size})
        if freeze_at:  # emoji besar di tengah saat gambar dibekukan
            emo = {"humor": "😂", "shocking": "😱", "emotional": "😭"}.get(str(clip_info.get("category")), "🤯")
            png = editing.emoji_png(emo, px=320)
            if png:
                # di samping (bukan tengah) agar tidak menutup wajah yang sedang dibekukan
                stickers.append({"t": freeze_at, "emoji": emo, "dur": editing.FREEZE_SEC + 0.25, "png": png,
                                 "cx": int(out_w * 0.78), "cy": int(out_h * 0.56), "size": int(out_w * 0.28)})
        ass_for_burn = None
        if opts.get("enable_captions", True):
            segments_for_sub = build_subtitle_segments_for_clip(clip_info)
            generate_ass_subtitles(segments_for_sub, clip_style, str(subtitle_path), out_w, out_h)
            ass_for_burn = str(subtitle_path)
        # v4: kartu hook + watermark + CTA akhir digabung ke encode yang sama (dulu 2 encode tambahan)
        overlays, overlay_files = [], []
        # (v6.7) efek visual pro: teks hook di belakang orang, flash transisi, light leak, motion blur punch-in
        behind, fx_str, leaks = None, "", []
        pro_fx = bool(edit.get("pro_fx")) and not layout
        if pro_fx and config.ENABLE_BROLL and not clip_info.get("no_hook_card") and clip_info.get("hook_text"):
            try:
                mask_file = clip_dir / "person_mask.mkv"
                mask_avg = editing.person_mask(input_for_caption, str(mask_file), until=2.6)
                plan = editing.behind_text_plan(clip_info["hook_text"], mask_avg, out_w, out_h, clip_dir,
                                                _escape_ffmpeg_filter_path, 2.6) if mask_avg is not None else None
                if plan and plan.get("front"):
                    overlays += plan["filters"]
                    overlay_files += plan["files"]
                    behind = {"front": True}
                    logger.info(f"  Hook headline di area dada (y={plan['y']}, {plan['size']}px)")
                    # v6.8: selama headline tampil, caption digeser ke bawah headline (dulu menempel/bertumpuk)
                    if ass_for_burn:
                        head_bottom = int(plan["y"]) + len(plan["filters"]) * int(plan["size"] * 1.02)
                        fs = int(clip_style.get("font_size", 70))
                        mv = max(230, out_h - head_bottom - 40 - int(fs * 2.3))
                        if mv < int(clip_style.get("vertical_position", 420)):
                            clip_style["margin_zones"] = list(clip_style.get("margin_zones") or []) + [(0.0, float(plan["until"]), mv)]
                            generate_ass_subtitles(segments_for_sub, clip_style, str(subtitle_path), out_w, out_h)
                elif plan:
                    behind = {"mask": str(mask_file), "filters": plan["filters"], "until": plan["until"]}
                    overlay_files += plan["files"]
                    logger.info(f"  Hook di belakang orang (y={plan['y']}, {plan['size']}px)")
            except Exception as exc:
                logger.warning(f"Teks di belakang orang dilewati: {str(exc)[:160]}")
                behind = None
        if config.ENABLE_BROLL and not clip_info.get("no_hook_card") and not behind:
            f, files = bumper_filters(clip_info, str(final_clip_path))
            overlays += f
            overlay_files += files
        if pro_fx:
            flashes = sorted({round(b["t"], 2) for b in broll} | ({round(cold_shift, 2)} if cold_shift else set()) | set(shakes))
            # motion blur hanya saat punch-in di dalam satu shot; di titik potong (cold open/b-roll) tmix = bayangan ganda
            cuts = [cold_shift] + [b["t"] for b in broll] + [b["t"] + b["dur"] for b in broll]
            blur_starts = ([0.0] if config.ENABLE_HOOK_PUNCH and not cold_shift else []) +                 [s for s, _ in emphasis if all(abs(s - c) > 0.35 for c in cuts if c)]
            fx_str = editing.fx_chain(flashes, blur_starts)
            busy_b = [(b["t"] - 0.5, b["t"] + b["dur"] + 0.5) for b in broll]
            peak_list = list(shakes) or [s for s, _ in emphasis[:3]]
            for t in peak_list:
                if t >= 3.0 and t <= clip_duration - 1.2 and not any(a <= t <= b for a, b in busy_b) and all(abs(t - q) > 6 for q in leaks):
                    leaks.append(round(t, 2))
            leaks = leaks[:2]
            clip_info["pro_fx"] = {"hook_style": ("headline" if behind and behind.get("front") else "behind" if behind else "card"),
                                   "flashes": flashes, "motion_blur": blur_starts, "leaks": leaks}
        end_cta = edit["end_cta"] and config.ADD_END_SCREEN
        f, files = extras_filters(str(final_clip_path), clip_duration, add_watermark=opts["add_watermark"], end_cta=end_cta)
        overlays += f
        overlay_files += files
        lt_items = _lower_third_items(opts.get("lower_third") or {}, speaker_plan, clip_duration,
                                      t_min=max(cold_shift + 0.4, 2.9 if config.ENABLE_BROLL and not clip_info.get("no_hook_card") else 0.6))
        if edit.get("lower_third") and lt_items:
            # Split/campaign: kartu nama di area gameplay, di atas subtitle dan
            # banner (0.86 dulu menabrak banner MotionKlip di y=1584).
            f, files = editing.lower_third_filters(lt_items, clip_dir, _drawtext_font(), _escape_ffmpeg_filter_path,
                                                   y_frac=0.74 if layout else 0.58)
            overlays += f
            overlay_files += files
        polish = dict(width=out_w, height=out_h, duration=clip_duration, ass_path=ass_for_burn, grade=edit["color_grade"],
                      zoom_chain=zoom_chain, progress_bar=edit["progress_bar"], encode_args=_video_encode_args(crf=17),
                      escape_path=_escape_ffmpeg_filter_path, overlays=overlays)
        fx = dict(fx_chain=fx_str, behind_text=behind if behind and behind.get("mask") else None, leaks=leaks)
        try:
            try:
                editing.render_polish(input_for_caption, str(final_clip_path), stickers=stickers, broll=broll, **fx, **polish)
            except subprocess.CalledProcessError as exc:
                if not stickers and not broll and not fx_str and not behind and not leaks:
                    raise
                logger.warning(f"Stiker/b-roll/efek pro dilewati ({(exc.stderr or b'')[-300:]!r})")
                stickers, broll = [], []
                if behind and behind.get("mask"):  # teks di belakang orang gagal → kembalikan kartu hook biasa
                    f, files = bumper_filters(clip_info, str(final_clip_path))
                    polish["overlays"] = f + polish["overlays"]
                    overlay_files += files
                editing.render_polish(input_for_caption, str(final_clip_path), **polish)
        except subprocess.CalledProcessError as exc:
            logger.warning(f"Polish render gagal ({(exc.stderr or b'')[-300:]!r}); fallback caption saja.")
            if ass_for_burn:
                burn_subtitles(input_for_caption, ass_for_burn, str(final_clip_path), clip_style)
            else:
                shutil.copy2(input_for_caption, final_clip_path)
        finally:
            for text_file in overlay_files:
                Path(text_file).unlink(missing_ok=True)

        if enhanced_audio and enhanced_audio.exists():
            audio_replaced_path = clip_dir / f"{clip_name}_audio.mp4"
            mux_audio(str(final_clip_path), str(enhanced_audio), str(audio_replaced_path))
            final_clip_path = audio_replaced_path

        if opts["background_music_path"]:
            music_path = Path(opts["background_music_path"])
            if music_path.exists():
                music_mix_path = clip_dir / f"{clip_name}_music.mp4"
                add_background_music_with_duck(
                    str(final_clip_path),
                    str(music_path),
                    str(music_mix_path),
                    music_volume=config.MUSIC_VOLUME,
                )
                final_clip_path = music_mix_path
            else:
                logger.warning(f"Background music file not found: {opts['background_music_path']}")

        # (v6.4) Sound design: impact di hook/awal cerita, riser→impact di puncak, whoosh di punch-in, pop di stiker.
        # (v4) tanpa sound design: whoosh halus di titik punch-in saja
        sfx_out = clip_dir / f"{clip_name}_sfx.mp4"
        try:
            if edit.get("sound_design") or mute_spans:
                events = editing.plan_sound_design(clip_duration, emphasis, stickers, peaks_rel, cold_shift=cold_shift) \
                    if edit.get("sound_design") else []
                if edit.get("sound_design"):
                    events += [(max(b["t"] - 0.15, 0), "whoosh", 0.32) for b in broll]
                    events += [(t, "impact", 0.45) for t in shakes if all(abs(t - e[0]) > 0.3 for e in events)]
                    events.sort()
                if editing.add_sound_design(str(final_clip_path), str(sfx_out), events, mute=mute_spans):
                    final_clip_path = sfx_out
                    clip_info["sound_design"] = [{"t": round(t, 2), "sfx": n} for t, n, _ in events]
            elif edit["sfx"] and emphasis:
                if editing.add_sfx(str(final_clip_path), str(sfx_out), [s for s, _ in emphasis]):
                    final_clip_path = sfx_out
        except subprocess.CalledProcessError as exc:
            logger.warning(f"SFX dilewati: {(exc.stderr or b'')[-300:]!r}")

        # (v6.4) Mastering: -14 LUFS dua tahap + limiter (setelah semua suara dicampur)
        if edit.get("audio_master"):
            mastered = clip_dir / f"{clip_name}_master.mp4"
            try:
                if editing.master_audio(str(final_clip_path), str(mastered)):
                    final_clip_path = mastered
            except subprocess.CalledProcessError as exc:
                logger.warning(f"Mastering audio dilewati: {(exc.stderr or b'')[-300:]!r}")
        # (v6.8) Tempo adaptif: pembicara lambat dipercepat halus (≤1,08×, nada tetap) — pacing ala kliper pro
        if edit.get("auto_tempo") and getattr(config, "AUTO_TEMPO", True) and not layout:
            tempo = editing.plan_tempo(editing.relative_words(clip_info))
            if tempo > 1.0:
                tempo_out = clip_dir / f"{clip_name}_tempo.mp4"
                try:
                    editing.apply_tempo(str(final_clip_path), str(tempo_out), tempo, _video_encode_args(crf=16))
                    final_clip_path = tempo_out
                    clip_info["tempo"] = tempo
                    logger.info(f"  Tempo {tempo:.2f}× (pembicara lambat → pacing lebih rapat)")
                except subprocess.CalledProcessError as exc:
                    logger.warning(f"Tempo dilewati: {(exc.stderr or b'')[-200:]!r}")
        clip_info["stickers"] = [{"t": s["t"], "emoji": s["emoji"]} for s in stickers]
        clip_info["broll"] = [{"t": b["t"], "query": b["query"], "source": b.get("source")} for b in broll]
        clip_info["shakes"] = shakes
        clip_info["censored"] = len(mute_spans)
        if speaker_plan:
            clip_info["camera"] = [{"t": round(sh["t0"], 2), "mode": sh["mode"], "side": sh["side"]} for sh in speaker_plan["shots"]]

        # (kartu hook, watermark & CTA sudah dirender di polish render)

        # 5. Thumbnail at the peak moment (keyword-dense) or middle of clip.
        # v6: frame diambil dari versi reframe SEBELUM subtitle dibakar — dulu dari klip final,
        # sehingga teks hook thumbnail bertumpuk dengan subtitle. Timeline-nya sama (jump cut sudah
        # diterapkan sebelum reframe).
        thumb_src = input_for_caption if Path(input_for_caption).exists() else str(final_clip_path)
        if config.PEAK_THUMBNAIL:
            thumb_time = _peak_thumb_time(clip_info)
        else:
            thumb_time = (clip_info["start"] + clip_info["end"]) / 2
        clip_len = max(clip_info["end"] - clip_info["start"], 0.5)
        rel_thumb = min(max(thumb_time - clip_info["start"], 3.0 if clip_len > 6 else 0.2), clip_len - 0.3)
        thumb_alt_path = None
        if edit["smart_thumbnail"]:
            # (v4) pilih frame paling tajam + wajah jelas; frame ke-2 + hook alternatif = varian A/B
            cands = editing.thumbnail_candidates(clip_len, rel_thumb)
            if clip_info.get("cold_open"):  # frame rewind (noise VHS) jangan jadi thumbnail
                t_len = clip_info["cold_open"]["teaser"][1] - clip_info["cold_open"]["teaser"][0]
                cands = [t for t in cands if not (t_len - 0.1 <= t <= clip_info["cold_open"]["shift"] + 0.2)] or cands
            best = editing.pick_best_frames(thumb_src, cands, top=2)
        else:
            best = []
        first = best[0] if best else {"time": rel_thumb, "face": None}
        # (v6.8) teks thumbnail khusus 2–5 kata dari Editor-AI (lebih besar & memancing); cadangan: hook layar
        thumb_txt = clip_info.get("thumb_text") or clip_info.get("hook_text", "")
        create_thumbnail(thumb_src, first["time"], str(thumb_path),
                         thumb_txt, clip_info.get("score", 0), face_box=first.get("face"))
        if len(best) > 1:
            thumb_alt_path = clip_dir / "thumbnail_b.jpg"
            alt_hook = (clip_info.get("thumb_alternatives") or clip_info.get("hook_alternatives") or [thumb_txt])[0]
            create_thumbnail(thumb_src, best[1]["time"], str(thumb_alt_path),
                             alt_hook, clip_info.get("score", 0), face_box=best[1].get("face"))

        # 6. Nama file akhir yang konsisten (tanpa encode ulang)
        final_named = clip_dir / f"{clip_name}_final.mp4"
        if Path(final_clip_path).resolve() != final_named.resolve():
            os.replace(final_clip_path, final_named)
            final_clip_path = final_named

        if not Path(final_clip_path).exists() or Path(final_clip_path).stat().st_size == 0:
            raise RuntimeError(f"Final clip was not created: {final_clip_path}")

        # Cleanup temp files.
        # FIX: dulu hanya raw/virtual_bg/eye_contact/reframed yang dihapus; versi antara
        # (<clip>.mp4, _audio, _music, _broll) + enhanced.aac ikut tersimpan -> 4-5 salinan
        # video penuh per klip. Sekarang semua file antara dihapus, hanya hasil akhir tersisa.
        final_resolved = Path(final_clip_path).resolve()
        intermediates = [
            raw_clip_path,
            clip_dir / "raw_cut.mp4",
            clip_dir / "cold_open.mp4",
            clip_dir / "freeze.mp4",
            clip_dir / f"{clip_name}_sfx.mp4",
            clip_dir / f"{clip_name}_master.mp4",
            clip_dir / "virtual_bg.mp4",
            clip_dir / "eye_contact.mp4",
            clip_dir / "reframed.mp4",
            clip_dir / "reframed_sr.mp4",
            clip_dir / "person_mask.mkv",
            clip_dir / "enhanced.aac",
            clip_dir / f"{clip_name}.mp4",
            clip_dir / f"{clip_name}_audio.mp4",
            clip_dir / f"{clip_name}_music.mp4",
            clip_dir / f"{clip_name}_broll.mp4",
        ]
        for f in intermediates:
            if f.exists() and f.resolve() != final_resolved:
                f.unlink()

        processed_clip = {
            **clip_info,
            "file_path": str(final_clip_path),
            "thumbnail": str(thumb_path),
            "thumbnail_alt": str(thumb_alt_path) if thumb_alt_path else None,
            "edit_style": edit,
            # durasi nyata file akhir (cold open, freeze, tempo ikut terhitung)
            "duration": round(float(get_video_info(str(final_clip_path)).get("duration") or (clip_info["end"] - clip_info["start"])), 2),
        }
        processed_clip = enrich_clip_for_platforms(processed_clip, opts["target_platform"])

        logger.success(f"✅ Clip {idx+1} done: {clip_name} (score {clip_info['score']})")
        return idx, processed_clip
    except Exception as e:
        logger.exception(f"Failed to process clip {idx+1}: {e}")
        return idx, None

# Lebar wajah maksimum (fraksi lebar frame) yang masih dianggap facecam streamer.
# 0.32 memberi ruang untuk facecam besar; wajah podcast biasanya >= 0.35 dan di tengah.
FACE_CAM_MAX_WIDTH = 0.32


def centered_cam_box(face, cam_ratio: float = 0.38) -> List[float]:
    """Kotak facecam yang memusatkan wajah di panel, tanpa padding.

    Tinggi kotak ±2× tinggi wajah (ruang rambut), lebar mengikuti rasio panel
    atas. Kalau wajah berada sangat dekat tepi frame, kotak dipersempit sampai
    bisa muat terpusat — konsekuensinya crop lebih dekat, tetapi tidak ada bar
    hitam. Dipakai untuk semua streamer, bukan koordinat tetap.
    """
    mx, my, _fw, fh = (float(v) for v in face)
    panel_ratio = 1080 / (1920 * max(0.2, min(0.6, cam_ratio)))
    k = panel_ratio * 1080 / 1920  # cw/ch ternormalisasi (vh/vw = 1080/1920)
    ch = min(0.9, max(fh * 2.0, 0.18))
    cw = ch * k
    # wajah tepat di tengah: x = mx - cw/2 harus di dalam [0, 1-cw]
    cw = max(0.08, min(cw, 2 * min(mx, 1 - mx)))
    ch = min(0.9, cw / k)
    # jaga kotak tetap di dalam frame (wajah ~52% dari atas kotak)
    ch = min(ch, 2 * min(my / 0.52, (1 - my) / 0.48))
    cw = max(0.08, ch * k)
    x = min(max(mx - cw / 2, 0.0), 1 - cw)
    y = min(max(my - ch * 0.52, 0.0), 1 - ch)
    return [round(x, 4), round(y, 4), round(cw, 4), round(ch, 4)]


def detect_stream_layout(video_path: str, clips_data: Optional[List[Dict]] = None, samples: int = 14,
                         force: bool = False, cam_ratio: float = 0.38) -> Optional[Dict]:
    """Deteksi facecam streamer (wajah kecil yang posisinya tetap di pinggir layar gameplay).

    Return {cam_box, game_box, cam_ratio} relatif 0..1, atau None bila bukan layout streamer
    (podcast/vlog: wajah besar di tengah → tetap pakai reframe wajah biasa)."""
    info = get_video_info(video_path)
    vw, vh, dur = int(info.get("width") or 0), int(info.get("height") or 0), float(info.get("duration") or 0)
    if not vw or not vh or vw <= vh or dur <= 0:
        return None
    if clips_data:
        spans = [(float(c["start"]), float(c["end"])) for c in clips_data[:8]]
        # Minimal 3 sampel per klip: dulu 2 sampel/klip, jadi jendela dengan 1 klip
        # hanya membaca 2 frame -> `read < 3` -> deteksi SELALU gagal tanpa log.
        times = [s + (e - s) * k / 4 for s, e in spans for k in (1, 2, 3)]
    else:
        times = [dur * (0.05 + 0.9 * k / max(samples - 1, 1)) for k in range(samples)]
    times = sorted(t for t in times if 0 < t < dur)[:samples]
    detector = FaceDetectorCompat(min_confidence=0.5)
    cap = cv2.VideoCapture(video_path)
    found: List[Tuple[float, float, float, float]] = []
    read = 0
    try:
        for t in times:
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            read += 1
            faces = detector.detect(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            # Wajah 'facecam' streamer bisa cukup besar: facecam Windah ±22% lebar
            # frame (preset campaign: 0.225). Ambang lama 14% membuat facecam seperti
            # itu dianggap "wajah besar" -> deteksi gagal -> jatuh ke reframe wajah
            # penuh dan gameplay hilang. Podcast tetap terpisah karena wajahnya
            # >= 35% dan biasanya di tengah.
            small = [((x + w / 2) / vw, (y + h / 2) / vh, w / vw, h / vh) for (x, y, w, h), s in faces if s >= 0.6 and w / vw < FACE_CAM_MAX_WIDTH]
            big = [1 for (x, y, w, h), s in faces if s >= 0.6 and w / vw >= FACE_CAM_MAX_WIDTH]
            if big and not force:
                found.append((-1, -1, 0, 0))  # ada wajah besar = kemungkinan bukan gameplay
            found.extend(small)
    finally:
        cap.release()
        try:
            detector.close()
        except Exception:
            pass
    pts = [f for f in found if f[0] >= 0]
    big_frames = sum(1 for f in found if f[0] < 0)
    if read < 3 or not pts:
        logger.info(f"Layout probe: frames={read} wajah_kecil={len(pts)} → kurang sampel, tanpa split")
        return None
    # kluster posisi wajah: facecam = posisi yang muncul di sebagian besar frame
    best: List[Tuple[float, float, float, float]] = []
    for cx, cy, _, _ in pts:
        group = [p for p in pts if abs(p[0] - cx) < 0.07 and abs(p[1] - cy) < 0.09]
        if len(group) > len(best):
            best = group
    share = len(best) / read
    mx, my = float(np.median([p[0] for p in best])), float(np.median([p[1] for p in best]))
    fw, fh = float(np.median([p[2] for p in best])), float(np.median([p[3] for p in best]))
    at_edge = mx < 0.32 or mx > 0.68 or my < 0.3 or my > 0.7
    logger.info(f"Layout probe: frames={read} small={len(pts)} big={big_frames} "
                f"share={share:.2f} face_w={fw:.3f} mx={mx:.2f} my={my:.2f} at_edge={at_edge} force={force}")
    if not force and (share < 0.55 or big_frames > read * 0.3 or not at_edge):
        logger.info(f"Layout probe: share={share:.2f} big={big_frames} at_edge={at_edge} → bukan layout streamer")
        return None
    # Kotak facecam dihitung agar wajah SELALU di tengah panel (bukan preset
    # tetap): tinggi 2× wajah, lebar mengikuti rasio panel, lalu dipersempit
    # bila wajah terlalu dekat tepi frame — tanpa padding/bar hitam.
    box = centered_cam_box((mx, my, fw, fh), cam_ratio)
    return {"cam_box": box, "game_box": [0.0, 0.0, 1.0, 1.0],
            "cam_ratio": cam_ratio, "face": [round(mx, 3), round(my, 3), round(fw, 3), round(fh, 3)], "share": round(share, 2)}


def _lower_third_items(lt: Dict, speaker_plan: Optional[Dict], duration: float, t_min: float) -> List[Tuple[float, str, str]]:
    """(waktu, nama, keterangan) kartu nama. Dengan kamera pembicara: tiap orang saat shot pertamanya."""
    names = [n for n in (lt.get("names") or []) if n]
    if not names or duration < t_min + 3.5:
        return []
    sub = str(lt.get("subtitle") or "")
    by_speaker = lt.get("by_speaker") or {}
    if speaker_plan and by_speaker:
        items, seen = [], set()
        spk_of_side = {side: spk for spk, side in speaker_plan["side_of"].items()}
        for sh in speaker_plan["shots"]:
            if sh["mode"] != "single" or sh.get("reaction"):
                continue
            name = by_speaker.get(spk_of_side.get(sh["side"], ""))
            t = max(sh["t0"] + 0.3, t_min)
            if name and name not in seen and t < duration - 3.5 and sh["t1"] - t >= 1.5:
                items.append((t, name, sub if not items else ""))
                seen.add(name)
        return items[:2]
    return [(t_min, names[0], sub)]


def extract_segment(video_path: str, start: float, end: float, output_path: str, crop: Optional[Tuple[int, int, int, int]] = None):
    """Extract exact segment using ffmpeg (fast & accurate). `crop` = (x, y, w, h) area gambar sebenarnya."""
    duration = end - start
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start),
        "-i", video_path,
        "-t", str(duration),
        "-vf", (("crop={}:{}:{}:{},".format(crop[2], crop[3], crop[0], crop[1]) if crop else "") + "hqdn3d=1.2:1.0:3:3"),
        *_video_encode_args(crf=18, fast=True),
        "-c:a", "aac", "-b:a", "192k",
        "-avoid_negative_ts", "make_zero",
        output_path
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    logger.debug(f"Extracted segment {start:.1f}-{end:.1f}s")

def detect_faces_in_clip(
    video_path: str,
    sample_rate: int = 3,
    group_framing: bool = False,
    return_metadata: bool = False,
) -> List:
    """Detect faces in video (legacy mp.solutions / Tasks API via compat layer).

    Single mode returns one tracked face per sampled frame.
    Group mode returns a union box around up to 5 visible faces, better for 3+ people.
    """
    bboxes = []

    cap = cv2.VideoCapture(video_path)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    last_center = None
    speakers = None

    try:
        detector = FaceDetectorCompat(min_confidence=config.FACE_DETECTION_CONFIDENCE)
    except Exception as exc:
        logger.warning(f"Face detection tidak tersedia ({str(exc)[:120]}). Klip diproses tanpa face tracking.")
        cap.release()
        return bboxes

    try:
        frame_idx = 0
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break

            if frame_idx % sample_rate == 0:
                rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                full = detector.detect_full(rgb_frame)
                h, w, _ = frame.shape
                if speakers is None:
                    speakers = _SpeakerTracker(w, sample_rate / fps)
                faces = []
                for item in full:
                    box = _clamp_bbox(item["box"], w, h)
                    if box is not None:
                        faces.append({**item, "box": box})
                # v4: pembicara aktif (gerak mulut) dengan hysteresis; bila tak bisa ditentukan,
                # jatuh ke pelacakan wajah terdekat seperti sebelumnya.
                speaker_box = speakers.update(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), faces)

                if faces:
                    face_boxes = [(f["box"], f["score"]) for f in faces]
                    best_box = speaker_box or _choose_tracking_face(face_boxes, last_center)
                    group_box = _union_face_boxes(face_boxes, w, h)
                    active_box = group_box if group_framing else best_box
                    if active_box:
                        last_center = (active_box[0] + active_box[2] / 2, active_box[1] + active_box[3] / 2)
                    if return_metadata:
                        bboxes.append({
                            "box": active_box,
                            "main_box": best_box,
                            "group_box": group_box,
                            "face_count": len(face_boxes),
                            "all_faces": [item[0] for item in face_boxes],
                        })
                    else:
                        bboxes.append(active_box)
                else:
                    bboxes.append({"box": None, "main_box": None, "group_box": None, "face_count": 0, "all_faces": []} if return_metadata else None)

            frame_idx += 1
            if frame_idx > total_frames:
                break
    finally:
        detector.close()
        cap.release()

    if return_metadata:
        return _fill_short_face_meta_gaps(_smooth_face_metadata(bboxes, window=config.SMOOTHING_FRAMES))
    return _fill_short_face_gaps(smooth_face_trajectory(bboxes, window=config.SMOOTHING_FRAMES))


class _SpeakerTracker:
    """Pilih pembicara aktif di antara beberapa wajah berdasarkan gerak area mulut.

    - Wajah dilacak antar-sampel (pencocokan pusat terdekat).
    - Aktivitas = selisih piksel area mulut (dinormalisasi kecerahan) antar sampel, di-EMA.
    - Pindah pembicara hanya bila penantang jelas lebih aktif selama ±0.7 detik (anti kedip).
    """

    def __init__(self, width: int, sample_period: float):
        self.width = width
        self.hold = max(2, int(round(0.7 / max(sample_period, 1e-3))))
        self.tracks: List[Dict] = []
        self.current = None
        self.challenger, self.challenge = None, 0
        self._next = 0

    @staticmethod
    def _mouth_patch(gray, face):
        x, y, bw, bh = face["box"]
        mouth = face.get("mouth")
        if mouth:
            (x1, y1), (x2, y2) = mouth
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
            mw = max(abs(x2 - x1) * 1.7, bw * 0.38)
        else:
            cx, cy, mw = x + bw / 2, y + bh * 0.78, bw * 0.45
        mh = mw * 0.7
        x0, y0 = int(max(cx - mw / 2, 0)), int(max(cy - mh * 0.45, 0))
        x1_, y1_ = int(min(cx + mw / 2, gray.shape[1])), int(min(cy + mh * 0.55, gray.shape[0]))
        if x1_ - x0 < 4 or y1_ - y0 < 4:
            return None
        patch = cv2.resize(gray[y0:y1_, x0:x1_], (32, 20)).astype(np.float32)
        return patch - patch.mean()

    def update(self, gray, faces: List[Dict]):
        new_tracks, used = [], set()
        for face in faces:
            x, y, bw, bh = face["box"]
            center = np.array([x + bw / 2, y + bh / 2], dtype=np.float32)
            match = None
            for track in self.tracks:
                if track["id"] in used:
                    continue
                if np.linalg.norm(track["center"] - center) < max(0.15 * self.width, bw * 1.2):
                    if match is None or np.linalg.norm(track["center"] - center) < np.linalg.norm(match["center"] - center):
                        match = track
            patch = self._mouth_patch(gray, face)
            activity = 0.0
            if match is not None:
                used.add(match["id"])
                if patch is not None and match["patch"] is not None:
                    activity = float(np.mean(np.abs(patch - match["patch"])))
                activity = 0.65 * match["act"] + 0.35 * activity
                track_id = match["id"]
            else:
                track_id, self._next = self._next, self._next + 1
            new_tracks.append({"id": track_id, "center": center, "box": face["box"], "patch": patch, "act": activity})
        self.tracks = new_tracks
        if not new_tracks:
            return None
        if len(new_tracks) == 1:
            self.current = new_tracks[0]["id"]
            return new_tracks[0]["box"]
        by_id = {t["id"]: t for t in new_tracks}
        leader = max(new_tracks, key=lambda t: t["act"])
        current = by_id.get(self.current)
        if current is None:
            # pembicara sebelumnya hilang dari frame: ambil yang paling aktif (atau terbesar bila semua diam)
            pick = leader if leader["act"] > 1.0 else max(new_tracks, key=lambda t: t["box"][2] * t["box"][3])
            self.current, self.challenge = pick["id"], 0
            return pick["box"]
        if leader["id"] != self.current and leader["act"] > current["act"] * 1.35 + 0.8:
            if self.challenger == leader["id"]:
                self.challenge += 1
            else:
                self.challenger, self.challenge = leader["id"], 1
            if self.challenge >= self.hold:
                self.current, self.challenge = leader["id"], 0
                return leader["box"]
        else:
            self.challenge = 0
        return current["box"]



def _resize_interp(cropped, out_w: int):
    """Lanczos saat memperbesar (crop kecil -> 1080p; lebih tajam dari bicubic), area saat memperkecil."""
    return cv2.INTER_LANCZOS4 if cropped.shape[1] < out_w else cv2.INTER_AREA



def _min_crop_h(base_h: float) -> float:
    """Batas zoom-in: wide shot (wajah kecil) boleh di-zoom ±1.7x agar orang tidak kecil di bawah
    frame (v4; dulu maks 1.33x), tapi tinggi crop minimal 600px agar tidak terlalu buram."""
    return max(base_h * 0.6, min(600.0, base_h))

class _FFmpegFrameWriter:
    """Pengganti cv2.VideoWriter('mp4v'): frame BGR dipipe ke FFmpeg (x264 CRF 16 / NVENC).

    mp4v (MPEG-4 Part 2) OpenCV bitrate-nya rendah dan menambah satu generasi kualitas buruk.
    """

    def __init__(self, path: str, width: int, height: int, fps: float):
        self.path = path
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{width}x{height}", "-r", f"{fps:.5f}", "-i", "-",
            *_video_encode_args(crf=16), "-pix_fmt", "yuv420p", path,
        ]
        self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)

    def write(self, frame) -> None:
        self._proc.stdin.write(np.ascontiguousarray(frame).tobytes())

    def close(self) -> None:
        self._proc.stdin.close()
        err = self._proc.stderr.read().decode("utf-8", errors="replace")
        if self._proc.wait() != 0:
            raise RuntimeError(f"FFmpeg frame writer gagal: {err[-300:]}")

def _union_face_boxes(face_boxes, width: int, height: int, max_faces: int = 5):
    if not face_boxes:
        return None
    ranked = sorted(face_boxes, key=lambda item: item[0][2] * item[0][3] * item[1], reverse=True)[:max_faces]
    boxes = [item[0] for item in ranked]
    min_x = min(box[0] for box in boxes)
    min_y = min(box[1] for box in boxes)
    max_x = max(box[0] + box[2] for box in boxes)
    max_y = max(box[1] + box[3] for box in boxes)
    pad_x = int((max_x - min_x) * 0.30)
    pad_y = int((max_y - min_y) * 0.85)
    return _clamp_bbox(
        (min_x - pad_x, min_y - pad_y, (max_x - min_x) + pad_x * 2, (max_y - min_y) + pad_y * 2),
        width,
        height,
    )


def _clamp_bbox(box: Tuple[int, int, int, int], width: int, height: int):
    x, y, bw, bh = box
    x = max(0, min(x, width - 1))
    y = max(0, min(y, height - 1))
    bw = max(1, min(bw, width - x))
    bh = max(1, min(bh, height - y))
    if bw * bh < width * height * 0.002:
        return None
    return (x, y, bw, bh)


def _choose_tracking_face(face_boxes, last_center):
    if not face_boxes:
        return None
    if last_center is None:
        return max(face_boxes, key=lambda item: item[0][2] * item[0][3] * item[1])[0]
    lx, ly = last_center
    def rank(item):
        box, confidence = item
        x, y, bw, bh = box
        cx, cy = x + bw / 2, y + bh / 2
        distance = np.hypot(cx - lx, cy - ly)
        size_score = np.sqrt(max(bw * bh, 1)) * 8
        return (confidence * 1000) + size_score - (distance * 6)
    return max(face_boxes, key=rank)[0]


def _fill_short_face_gaps(bboxes: List):
    if not bboxes:
        return bboxes
    filled = list(bboxes)
    last_valid = None
    for index, box in enumerate(filled):
        if box is not None:
            last_valid = box
            continue
        next_valid = next((item for item in filled[index + 1:index + 6] if item is not None), None)
        if last_valid is not None and next_valid is not None:
            filled[index] = last_valid
        elif last_valid is not None:
            filled[index] = last_valid
    return filled


def _extract_box(item):
    if isinstance(item, dict):
        return item.get("box")
    return item


def _metadata_for_frame(face_bboxes: List, frame_idx: int, total_frames: int):
    if not face_bboxes:
        return None
    pos = (frame_idx / max(total_frames - 1, 1)) * max(len(face_bboxes) - 1, 0)
    index = int(np.clip(round(pos), 0, len(face_bboxes) - 1))
    item = face_bboxes[index]
    if isinstance(item, dict):
        return item
    return {"box": item, "main_box": item, "group_box": item, "face_count": 1 if item else 0, "all_faces": [item] if item else []}


def _smooth_face_metadata(items: List[Dict], window: int = 15):
    if not items:
        return items
    boxes = smooth_face_trajectory([item.get("box") if item else None for item in items], window=window)
    main_boxes = smooth_face_trajectory([item.get("main_box") if item else None for item in items], window=window)
    group_boxes = smooth_face_trajectory([item.get("group_box") if item else None for item in items], window=window)
    smoothed = []
    for index, item in enumerate(items):
        data = dict(item or {})
        data["box"] = boxes[index]
        data["main_box"] = main_boxes[index]
        data["group_box"] = group_boxes[index]
        smoothed.append(data)
    return smoothed


def _fill_short_face_meta_gaps(items: List[Dict]):
    if not items:
        return items
    filled = list(items)
    last_valid = None
    for index, item in enumerate(filled):
        if item and item.get("box") is not None:
            last_valid = item
            continue
        next_valid = next((candidate for candidate in filled[index + 1:index + 6] if candidate and candidate.get("box") is not None), None)
        if last_valid is not None and next_valid is not None:
            filled[index] = dict(last_valid)
        elif last_valid is not None:
            filled[index] = dict(last_valid)
    return filled


def _has_face_metadata(face_bboxes: List) -> bool:
    return bool(face_bboxes and isinstance(face_bboxes[0], dict))


def caption_chunks(words: List[Dict], max_words: int = 5, max_chars: int = 30, pause: float = 0.45) -> List[List[Dict]]:
    """Pecah kata menjadi layar caption yang enak dibaca (v6.8).

    Dulu: potong tiap 5 kata atau di tanda titik → sisa 1 kata ("tujuh.") tampil sendirian, dan caption
    menyeberangi jeda panjang/pergantian pembicara. Sekarang: potong juga di jeda > `pause` dtk, pergantian
    pembicara, dan batas karakter; kata yatim digabung ke layar tetangga."""
    chunks: List[List[Dict]] = []
    cur: List[Dict] = []
    for i, w in enumerate(words):
        if cur:
            gap = float(w.get("start", 0)) - float(cur[-1].get("end", 0))
            spk_change = w.get("speaker") and cur[-1].get("speaker") and w.get("speaker") != cur[-1].get("speaker")
            chars = len(" ".join(str(x.get("word", "")).strip() for x in cur + [w]))
            if gap > pause or spk_change or len(cur) >= max_words or chars > max_chars:
                chunks.append(cur)
                cur = []
        cur.append(w)
        if str(w.get("word", "")).strip().endswith((".", "!", "?")):
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    # gabungkan kata yatim ke layar sebelumnya (atau berikutnya) bila dekat & pembicara sama
    merged: List[List[Dict]] = []
    for ch in chunks:
        if len(ch) == 1 and merged:
            prev = merged[-1]
            gap = float(ch[0].get("start", 0)) - float(prev[-1].get("end", 0))
            same = not (ch[0].get("speaker") and prev[-1].get("speaker") and ch[0]["speaker"] != prev[-1]["speaker"])
            ends_sentence = str(prev[-1].get("word", "")).strip().endswith((".", "!", "?"))
            if gap <= 0.6 and same and not ends_sentence and len(prev) <= max_words and \
                    len(" ".join(str(x.get("word", "")).strip() for x in prev + ch)) <= max_chars + 10:
                prev.extend(ch)
                continue
        merged.append(ch)
    out: List[List[Dict]] = []
    for ch in merged:  # yatim di depan → gabung ke layar berikutnya
        if out and len(out[-1]) == 1 and len(ch) <= max_words and \
                not str(out[-1][-1].get("word", "")).strip().endswith((".", "!", "?")) and \
                len(" ".join(str(x.get("word", "")).strip() for x in out[-1] + ch)) <= max_chars + 10 and \
                float(ch[0].get("start", 0)) - float(out[-1][-1].get("end", 0)) <= 0.6:
            out[-1] = out[-1] + ch
            continue
        out.append(ch)
    return out


def build_subtitle_segments_for_clip(clip_info: Dict) -> List[Dict]:
    """Build subtitle events relative to the clipped video timeline."""
    clip_start = float(clip_info["start"])
    clip_end = float(clip_info["end"])
    clip_duration = max(clip_end - clip_start, 0.1)
    words = [
        word for word in clip_info.get("words", [])
        if word.get("word") and word.get("end", 0) > clip_start and word.get("start", 0) < clip_end
    ]

    if words:
        chunks = caption_chunks(words)

        return [
            {
                "start": max(chunk[0]["start"] - clip_start, 0),
                "end": min(chunk[-1]["end"] - clip_start, clip_duration),
                "text": " ".join(item["word"] for item in chunk).strip(),
                # timing per kata relatif terhadap klip -> dipakai caption karaoke (\k)
                "words": [
                    {
                        "start": max(float(item["start"]) - clip_start, 0),
                        "end": min(max(float(item["end"]) - clip_start, 0), clip_duration),
                        "word": str(item["word"]).strip(),
                        # v6.8: tekanan suara ikut ke caption (kata yang ditekan "pop" lebih besar)
                        **({"stress": item["stress"]} if item.get("stress") else {}),
                    }
                    for item in chunk
                ],
            }
            for chunk in chunks
            if chunk and chunk[-1]["end"] > chunk[0]["start"]
        ]

    subtitle_segments = []
    for seg in clip_info.get("subtitle_segments", []):
        start = max(float(seg.get("start", clip_start)), clip_start)
        end = min(float(seg.get("end", clip_end)), clip_end)
        text = str(seg.get("text", "")).strip()
        if text and end > start:
            subtitle_segments.append({
                "start": start - clip_start,
                "end": end - clip_start,
                "text": text,
            })

    if subtitle_segments:
        return subtitle_segments

    return [{
        "start": 0,
        "end": clip_duration,
        "text": clip_info.get("text", ""),
    }]

def reframe_video(input_path: str, output_path: str, face_bboxes: List, video_info: Dict, 
                   conversation_mode: bool = False, target_aspect: str = "portrait",
                   auto_zoom: bool = True, split_screen: bool = False, bg_blur: bool = False,
                   group_framing: bool = False, klipers_reframe: bool = False):
    """
    Advanced Reframing with multiple effects:
    - Auto Zoom on Speaker (smooth)
    - Split Screen Mode (for 2 speakers)
    - Background Blur (like Zoom)
    - Portrait or Landscape output
    """
    valid_faces = [_extract_box(b) for b in face_bboxes if _extract_box(b) is not None]
    width = max(int(video_info.get("width", 1920)), 1)
    height = max(int(video_info.get("height", 1080)), 1)
    
    # Output resolution
    if target_aspect == "portrait":
        out_w, out_h = 1080, 1920
    else:
        out_w, out_h = 1920, 1080

    if group_framing and target_aspect == "portrait":
        render_group_stage_view(input_path, output_path, out_w, out_h)
        logger.info("  Group/Multi Person context view applied (keeps the full scene in frame)")
        return

    if klipers_reframe and valid_faces and not split_screen:
        klipers_style_reframe_video(
            input_path,
            output_path,
            face_bboxes,
            width,
            height,
            out_w,
            out_h,
            target_aspect=target_aspect,
            auto_zoom=auto_zoom,
        )
        return

    if valid_faces and not split_screen:
        dynamic_reframe_video(
            input_path,
            output_path,
            face_bboxes,
            width,
            height,
            out_w,
            out_h,
            target_aspect=target_aspect,
            auto_zoom=auto_zoom,
            bg_blur=bg_blur,
            group_framing=group_framing,
        )
        return
    
    # === SPLIT SCREEN MODE (Conversation) ===
    if split_screen and len(valid_faces) >= 2:
        # Create side-by-side view of both speakers
        face1 = valid_faces[0]
        face2 = valid_faces[-1]

        # FIX: dulu crop setengah frame (mis. 960x1080) di-scale paksa ke 540x1920 -> gambar
        # gepeng/melar. Sekarang crop tiap sisi dengan rasio panel yang benar, berpusat di wajah.
        panel_w, panel_h = out_w // 2, out_h
        crop_h = height
        crop_w = min(width // 2, max(16, int(round(crop_h * panel_w / panel_h))))
        if crop_w == width // 2:
            crop_h = min(height, int(round(crop_w * panel_h / panel_w)))
        left_face, right_face = sorted([face1, face2], key=lambda b: b[0] + b[2] / 2)

        def _panel_crop(face):
            cx = face[0] + face[2] / 2
            cy = face[1] + face[3] / 2
            x = int(np.clip(cx - crop_w / 2, 0, max(width - crop_w, 0)))
            y = int(np.clip(cy - crop_h / 2, 0, max(height - crop_h, 0)))
            return f"crop={crop_w}:{crop_h}:{x}:{y},scale={panel_w}:{panel_h}"

        left_crop = _panel_crop(left_face)
        right_crop = _panel_crop(right_face)
        
        vf = f"[0:v]{left_crop}[left];[0:v]{right_crop}[right];[left][right]hstack=inputs=2,scale={out_w}:{out_h}"
        
        cmd = [
            "ffmpeg", "-y", "-i", input_path,
            "-filter_complex", vf,
            *_video_encode_args(crf=20),
            "-c:a", "copy",
            output_path
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        logger.info("  Split Screen Mode applied (2 speakers side by side)")
        return
    
    # === SINGLE SPEAKER MODE ===
    crop_w = min(width, int(height * (9/16 if target_aspect == "portrait" else 16/9)))
    crop_h = min(height, int(width * (16/9 if target_aspect == "portrait" else 9/16)))
    
    if valid_faces:
        main_face = valid_faces[-1]  # most recent face
        
        if auto_zoom:
            # FIX: dulu crop = ukuran wajah x0.75 (lebih kecil dari wajah & rasio salah ->
            # output melar). Sekarang zoom tetap menjaga rasio output.
            out_ratio = out_w / out_h
            crop_h = int(min(height, max(main_face[3] / 0.28, crop_h * 0.6)))
            crop_w = int(min(width, crop_h * out_ratio))
            crop_h = int(min(height, crop_w / out_ratio))
        
        avg_x = main_face[0] + main_face[2] // 2
        avg_y = main_face[1] + main_face[3] // 2
        crop_x = int(np.clip(avg_x - crop_w / 2, 0, max(width - crop_w, 0)))
        crop_y = int(np.clip(avg_y - crop_h / 2, 0, max(height - crop_h, 0)))
    else:
        crop_x = max((width - crop_w) // 2, 0)
        crop_y = max((height - crop_h) // 2, 0)
    
    # Base crop + scale
    vf_parts = [f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y}", f"scale={out_w}:{out_h}"]
    
    # === BACKGROUND BLUR ===
    if bg_blur:
        # Simple background blur using boxblur (fast)
        vf_parts.insert(0, "boxblur=20:20")
        # Then crop to face area (so face stays sharp)
        logger.info("  Background Blur applied")
    
    vf = ",".join(vf_parts)
    
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vf", vf,
        *_video_encode_args(crf=20),
        "-c:a", "copy",
        output_path
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    logger.info(f"  Reframed to {target_aspect} with effects (zoom={auto_zoom}, blur={bg_blur})")
    return


def dynamic_reframe_video(
    input_path: str,
    output_path: str,
    face_bboxes: List,
    width: int,
    height: int,
    out_w: int,
    out_h: int,
    target_aspect: str = "portrait",
    auto_zoom: bool = True,
    bg_blur: bool = False,
    group_framing: bool = False,
):
    """Render a smooth per-frame crop that follows detected face trajectory."""
    cap = cv2.VideoCapture(input_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 24
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1

    temp_video = str(Path(output_path).with_name(Path(output_path).stem + "_video_only.mp4"))
    writer = _FFmpegFrameWriter(temp_video, out_w, out_h, fps)

    base_crop_w = min(width, int(height * (9 / 16 if target_aspect == "portrait" else 16 / 9)))
    base_crop_h = min(height, int(base_crop_w * (16 / 9 if target_aspect == "portrait" else 9 / 16)))
    if base_crop_h > height:
        base_crop_h = height
        base_crop_w = min(width, int(base_crop_h * (9 / 16 if target_aspect == "portrait" else 16 / 9)))

    first_box = next((_extract_box(box) for box in face_bboxes if _extract_box(box) is not None), None)
    if first_box:
        fx, fy, fw, fh = first_box
        last_center = np.array([fx + fw / 2, fy + fh * 0.58], dtype=np.float32)
        initial_h = np.clip(fh / 0.22, _min_crop_h(base_crop_h), height)
        initial_w = min(width, initial_h * (out_w / out_h))
        initial_h = min(height, initial_w * (out_h / out_w))
        last_crop = np.array([initial_w, initial_h], dtype=np.float32)
    else:
        last_center = np.array([width / 2, height / 2], dtype=np.float32)
        last_crop = np.array([base_crop_w, base_crop_h], dtype=np.float32)
    alpha = 0.18
    frame_idx = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        bbox = _bbox_for_frame(face_bboxes, frame_idx, total_frames)
        if bbox is not None:
            x, y, bw, bh = bbox
            target_center = np.array([x + bw / 2, y + (bh * (0.56 if group_framing else 0.58))], dtype=np.float32)
            if auto_zoom and bw > 0 and bh > 0:
                if group_framing:
                    desired_w = min(width, max(base_crop_w, bw * 1.42))
                    desired_h = desired_w * (out_h / out_w)
                    if desired_h < bh * 1.85:
                        desired_h = min(height, bh * 1.85)
                        desired_w = min(width, desired_h * (out_w / out_h))
                    if desired_h > height:
                        desired_h = height
                        desired_w = min(width, desired_h * (out_w / out_h))
                else:
                    desired_h = np.clip(bh / 0.22, _min_crop_h(base_crop_h), height)
                    desired_w = desired_h * (out_w / out_h)
                    if desired_w > width:
                        desired_w = width
                        desired_h = desired_w * (out_h / out_w)
                target_crop = np.array([desired_w, desired_h], dtype=np.float32)
            else:
                target_crop = np.array([base_crop_w, base_crop_h], dtype=np.float32)
        else:
            target_center = last_center
            target_crop = last_crop

        if np.linalg.norm(target_center - last_center) > width * 0.22:
            # pindah ke orang lain = potongan keras seperti editor manusia (bukan pan melewati dinding kosong)
            last_center, last_crop = target_center.copy(), target_crop.copy()
        else:
            last_center = (1 - alpha) * last_center + alpha * target_center
            last_crop = (1 - alpha) * last_crop + alpha * target_crop

        crop_w = int(np.clip(last_crop[0], 16, width))
        crop_h = int(np.clip(last_crop[1], 16, height))
        crop_x = int(np.clip(last_center[0] - crop_w / 2, 0, max(width - crop_w, 0)))
        crop_y = int(np.clip(last_center[1] - crop_h / 2, 0, max(height - crop_h, 0)))

        cropped = frame[crop_y:crop_y + crop_h, crop_x:crop_x + crop_w]
        if cropped.size == 0:
            cropped = frame
        output_frame = cv2.resize(cropped, (out_w, out_h), interpolation=_resize_interp(cropped, out_w))
        if bg_blur:
            output_frame = cv2.GaussianBlur(output_frame, (0, 0), 0.35)
        writer.write(output_frame)
        frame_idx += 1

    cap.release()
    writer.close()
    mux_video_with_source_audio(temp_video, input_path, output_path)
    Path(temp_video).unlink(missing_ok=True)
    logger.info(f"  Dynamic face-follow reframe applied ({frame_idx} frames)")


def klipers_style_reframe_video(
    input_path: str,
    output_path: str,
    face_bboxes: List,
    width: int,
    height: int,
    out_w: int,
    out_h: int,
    target_aspect: str = "portrait",
    auto_zoom: bool = True,
):
    """Klipers-like framing: closeup, two-person, group context, reaction."""
    cap = cv2.VideoCapture(input_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 24
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1

    temp_video = str(Path(output_path).with_name(Path(output_path).stem + "_video_only.mp4"))
    writer = _FFmpegFrameWriter(temp_video, out_w, out_h, fps)

    base_crop_w = min(width, int(height * (9 / 16 if target_aspect == "portrait" else 16 / 9)))
    base_crop_h = min(height, int(base_crop_w * (16 / 9 if target_aspect == "portrait" else 9 / 16)))
    if base_crop_h > height:
        base_crop_h = height
        base_crop_w = min(width, int(base_crop_h * (9 / 16 if target_aspect == "portrait" else 16 / 9)))

    first_meta = next((m for m in face_bboxes if isinstance(m, dict) and (m.get("main_box") or m.get("box"))), None)
    if first_meta:  # v4: mulai dari wajah pertama, bukan tengah frame (dinding kosong)
        fb = first_meta.get("main_box") or first_meta.get("box")
        last_center = np.array([fb[0] + fb[2] / 2, fb[1] + fb[3] * 0.58], dtype=np.float32)
    else:
        last_center = np.array([width / 2, height / 2], dtype=np.float32)
    last_crop = np.array([base_crop_w, base_crop_h], dtype=np.float32)
    previous_main_center = None
    alpha = 0.13
    shot_counts = {}
    frame_idx = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        meta = _metadata_for_frame(face_bboxes, frame_idx, total_frames)
        shot_type = _classify_shot_type(meta, previous_main_center, width, height)
        shot_counts[shot_type] = shot_counts.get(shot_type, 0) + 1
        target_box = _target_box_for_shot(meta, shot_type)

        if target_box is not None:
            x, y, bw, bh = target_box
            if shot_type in ("two_person", "group_context"):
                y_weight = 0.56
            elif shot_type == "reaction":
                y_weight = 0.54
            else:
                y_weight = 0.58
            target_center = np.array([x + bw / 2, y + bh * y_weight], dtype=np.float32)
            target_crop = np.array(
                _crop_size_for_shot(shot_type, target_box, base_crop_w, base_crop_h, width, height, out_w / out_h, auto_zoom),
                dtype=np.float32,
            )
            main_box = meta.get("main_box") if isinstance(meta, dict) else target_box
            if main_box:
                previous_main_center = (main_box[0] + main_box[2] / 2, main_box[1] + main_box[3] / 2)
        else:
            target_center = last_center
            target_crop = last_crop

        if np.linalg.norm(target_center - last_center) > width * 0.22:
            # pindah ke orang lain = potongan keras seperti editor manusia (bukan pan melewati dinding kosong)
            last_center, last_crop = target_center.copy(), target_crop.copy()
        else:
            last_center = (1 - alpha) * last_center + alpha * target_center
            last_crop = (1 - alpha) * last_crop + alpha * target_crop

        crop_w = int(np.clip(last_crop[0], 16, width))
        crop_h = int(np.clip(last_crop[1], 16, height))
        crop_x = int(np.clip(last_center[0] - crop_w / 2, 0, max(width - crop_w, 0)))
        crop_y = int(np.clip(last_center[1] - crop_h / 2, 0, max(height - crop_h, 0)))
        crop_y = _apply_caption_safe_y(crop_y, crop_h, height)

        cropped = frame[crop_y:crop_y + crop_h, crop_x:crop_x + crop_w]
        if cropped.size == 0:
            cropped = frame
        output_frame = cv2.resize(cropped, (out_w, out_h), interpolation=_resize_interp(cropped, out_w))
        writer.write(output_frame)
        frame_idx += 1

    cap.release()
    writer.close()
    mux_video_with_source_audio(temp_video, input_path, output_path)
    Path(temp_video).unlink(missing_ok=True)
    summary = ", ".join(f"{name}={count}" for name, count in sorted(shot_counts.items()))
    logger.info(f"  Klipers-style reframe applied ({summary})")


def _classify_shot_type(meta, previous_main_center, width: int, height: int) -> str:
    if not meta:
        return "closeup"
    face_count = int(meta.get("face_count") or 0)
    main_box = meta.get("main_box")
    group_box = meta.get("group_box") or main_box
    # v4: grup/dua orang hanya bila MUAT di crop 9:16; jika tidak (podcast kiri-kanan),
    # ikuti pembicara aktif (closeup) — dulu crop jatuh di tengah keduanya = dinding kosong.
    fits = bool(group_box) and group_box[2] <= height * 9 / 16 * 0.92
    if face_count >= 3 and fits:
        return "group_context"
    if face_count == 2 and fits:
        return "two_person"
    if main_box and previous_main_center:
        cx = main_box[0] + main_box[2] / 2
        cy = main_box[1] + main_box[3] / 2
        jump = np.hypot(cx - previous_main_center[0], cy - previous_main_center[1])
        if jump > width * 0.18:
            return "reaction"
    return "closeup"


def _target_box_for_shot(meta, shot_type: str):
    if not meta:
        return None
    if shot_type in ("two_person", "group_context", "reaction"):
        return meta.get("group_box") or meta.get("main_box") or meta.get("box")
    return meta.get("main_box") or meta.get("box") or meta.get("group_box")


def _crop_size_for_shot(shot_type: str, box, base_w, base_h, width, height, out_ratio, auto_zoom):
    x, y, bw, bh = box
    if not auto_zoom:
        return base_w, base_h
    if shot_type == "closeup":
        desired_h = np.clip(bh / 0.24, _min_crop_h(base_h), height)
    elif shot_type == "reaction":
        desired_h = np.clip(bh / 0.20, base_h * 0.92, height)
    elif shot_type == "two_person":
        desired_h = np.clip(max(bh * 2.1, base_h * 0.95), base_h * 0.95, height)
    else:
        desired_h = np.clip(max(bh * 2.35, base_h), base_h, height)
    desired_w = desired_h * out_ratio
    if desired_w < bw * 1.32:
        desired_w = min(width, bw * 1.32)
        desired_h = desired_w / out_ratio
    if desired_w > width:
        desired_w = width
        desired_h = desired_w / out_ratio
    if desired_h > height:
        desired_h = height
        desired_w = min(width, desired_h * out_ratio)
    return desired_w, desired_h


def _apply_caption_safe_y(crop_y: int, crop_h: int, height: int) -> int:
    # Keep a little more subject headroom so mid-lower captions do not cover mouths.
    safe_shift = int(crop_h * 0.04)
    return int(np.clip(crop_y - safe_shift, 0, max(height - crop_h, 0)))


def _bbox_for_frame(face_bboxes: List, frame_idx: int, total_frames: int):
    valid_count = len(face_bboxes)
    if valid_count == 0:
        return None
    pos = (frame_idx / max(total_frames - 1, 1)) * max(valid_count - 1, 0)
    low = int(np.floor(pos))
    high = min(low + 1, valid_count - 1)
    low_box = _extract_box(face_bboxes[low])
    high_box = _extract_box(face_bboxes[high])
    if low_box is None and high_box is None:
        return None
    if low_box is None:
        return high_box
    if high_box is None:
        return low_box
    ratio = pos - low
    return tuple(int((1 - ratio) * low_box[i] + ratio * high_box[i]) for i in range(4))


def render_group_stage_view(input_path: str, output_path: str, out_w: int, out_h: int):
    """Portrait layout that preserves the full source frame for group videos."""
    vf = (
        f"[0:v]scale={out_w}:{out_h}:force_original_aspect_ratio=increase,"
        f"crop={out_w}:{out_h},boxblur=24:12,eq=brightness=-0.05:saturation=0.85[bg];"
        f"[0:v]scale={out_w}:-2:force_original_aspect_ratio=decrease[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p"
    )
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-filter_complex", vf,
        *_video_encode_args(crf=20),
        "-c:a", "copy",
        output_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def mux_video_with_source_audio(video_only_path: str, source_path: str, output_path: str):
    cmd = [
        "ffmpeg", "-y",
        "-i", video_only_path,
        "-i", source_path,
        "-map", "0:v:0",
        "-map", "1:a:0?",
        "-c:v", "copy",  # v4: tanpa encode ulang (video sudah di-encode writer)
        "-c:a", "copy",
        "-shortest",
        output_path,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError:
        shutil.copy2(video_only_path, output_path)


def apply_eye_contact_correction(input_path: str, output_path: str):
    """
    Local eye-contact assist.

    This is not a neural gaze redirection model; it improves the perceived
    camera-facing look by applying a subtle face-friendly sharpen/contrast pass
    before dynamic reframe keeps the eyes centered.
    """
    vf = "unsharp=5:5:0.75:3:3:0.35,eq=contrast=1.04:saturation=1.03"
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vf", vf,
        *_video_encode_args(crf=19),
        "-c:a", "copy",
        output_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    logger.info("  Eye-contact assist applied")


def apply_virtual_background(input_path: str, output_path: str, background_path: str):
    """Replace the background using MediaPipe selfie segmentation (legacy/Tasks via compat)."""
    cap = cv2.VideoCapture(input_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 24
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 1280
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 720

    background = cv2.imread(background_path)
    if background is None:
        cap.release()
        raise FileNotFoundError(f"Could not read virtual background: {background_path}")
    background = _resize_cover(background, width, height)

    try:
        segmenter = SelfieSegmenterCompat()
    except Exception as exc:
        logger.warning(f"Selfie segmentation tidak tersedia ({str(exc)[:120]}). Virtual background dilewati.")
        cap.release()
        shutil.copy2(input_path, output_path)
        return

    temp_video = str(Path(output_path).with_name(Path(output_path).stem + "_video_only.mp4"))
    writer = _FFmpegFrameWriter(temp_video, width // 2 * 2, height // 2 * 2, fps)

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mask = segmenter.segment_mask(rgb)
            if mask is None or mask.size == 0:
                mask = np.ones((height, width), dtype=np.float32)
            mask = cv2.GaussianBlur(mask, (17, 17), 0)
            alpha = np.clip((mask - 0.18) / 0.45, 0, 1).astype(np.float32)
            alpha = alpha[..., None]
            composed = (frame.astype(np.float32) * alpha + background.astype(np.float32) * (1 - alpha)).astype(np.uint8)
            writer.write(composed[: height // 2 * 2, : width // 2 * 2])
    finally:
        cap.release()
        writer.close()
        segmenter.close()

    mux_video_with_source_audio(temp_video, input_path, output_path)
    Path(temp_video).unlink(missing_ok=True)
    logger.info("  Virtual background applied")


def _resize_cover(image: np.ndarray, target_w: int, target_h: int) -> np.ndarray:
    h, w = image.shape[:2]
    scale = max(target_w / max(w, 1), target_h / max(h, 1))
    resized = cv2.resize(image, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_LINEAR)
    y = max((resized.shape[0] - target_h) // 2, 0)
    x = max((resized.shape[1] - target_w) // 2, 0)
    return resized[y:y + target_h, x:x + target_w]

def burn_subtitles(video_path: str, ass_path: str, output_path: str, style: Dict):
    """Burn ASS subtitles into video with high quality"""
    # Use ffmpeg subtitles filter (supports ASS perfectly)
    escaped_ass_path = _escape_ffmpeg_filter_path(ass_path)
    # Font, ukuran, warna sudah diatur di file ASS (force_style dulu menimpa FontName dengan
    # path .ttf bila DEFAULT_FONT berupa path). fontsdir = folder fonts/ proyek untuk font custom.
    vf = f"subtitles='{escaped_ass_path}'"
    fonts_dir = Path(config.FONT_DIR)
    if fonts_dir.exists() and any(fonts_dir.glob("*.[ot]tf")):
        vf += f":fontsdir='{_escape_ffmpeg_filter_path(str(fonts_dir))}'"
    
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vf", vf,
        *_video_encode_args(crf=18),
        "-c:a", "copy",
        "-movflags", "+faststart",
        output_path
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    logger.debug("Subtitles burned in")


def mux_audio(video_path: str, audio_path: str, output_path: str):
    """Replace a video's audio track while keeping the encoded video stream."""
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", audio_path,
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        output_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    logger.debug("Enhanced audio muxed into final clip")

def _escape_ffmpeg_filter_path(path: str) -> str:
    path = Path(path).resolve().as_posix()
    return path.replace(":", r"\:").replace("'", r"\'")

def _drawtext_font() -> str:
    """fontfile eksplisit (bold) agar tidak bergantung fontconfig; kosong = default FFmpeg."""
    candidates = []
    custom = Path(str(config.DEFAULT_FONT))
    if custom.suffix.lower() in (".ttf", ".otf"):
        candidates.append(custom)
    candidates += [Path(config.FONT_DIR) / name for name in ("Montserrat-ExtraBold.ttf", "Poppins-Bold.ttf")]
    candidates += [Path("C:/Windows/Fonts/arialbd.ttf"), Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
                   Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf")]
    for path in candidates:
        if path.exists():
            return f"fontfile='{_escape_ffmpeg_filter_path(str(path))}':"
    return ""


def _wrap_lines(text: str, width: int = 18, max_lines: int = 3) -> str:
    import textwrap
    lines = textwrap.wrap(editing.strip_emoji(text), width=width) or [""]
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(".,") + "…"
    return "\n".join(lines)


NICHE_ACCENTS = {
    "gaming": "#FF4500", "finance": "#00FF7F", "beauty": "#FF69B4", "motivation": "#1E90FF",
    "education": "#FFD700", "comedy": "#FF6347", "relationship": "#FF1493", "tech": "#00CED1", "general": "#FFD700",
}


def _write_drawtext_file(output_path: str, label: str, text: str) -> str:
    text_path = Path(output_path).with_suffix(f".{label}.txt")
    # newline="\n": di Windows write_text menulis \r\n dan drawtext membaca \r sebagai baris
    # kosong tambahan -> jarak antar baris jadi dua kali lipat.
    with open(text_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(str(text))
    return str(text_path)


def bumper_filters(clip_info: Dict, output_path: str) -> Tuple[List[str], List[str]]:
    """Filter kartu judul hook (2.6 dtk pertama). Return (filters, file_teks_sementara).

    v4: teks besar terbungkus otomatis di kotak gelap + garis aksen warna niche; skor internal
    tidak ditampilkan; tanpa fade dari hitam (melemahkan hook); emoji dibuang (font tak punya glyph).
    """
    hook = clip_info.get("hook_text") or "Wajib tonton sampai habis"
    accent = NICHE_ACCENTS.get(clip_info.get("niche", "general"), NICHE_ACCENTS["general"]).lstrip("#")
    hook_file = _write_drawtext_file(output_path, "bumper_hook", _wrap_lines(hook, width=18, max_lines=4))
    font = _drawtext_font()
    fade = "alpha='if(lt(t,2.2),1,max(0,1-(t-2.2)/0.4))':enable='lte(t,2.6)'"
    y = float(clip_info.get("hook_y", 0.085))  # layout split: di panel gameplay agar tidak menutup wajah
    filters = [
        f"drawtext={font}textfile='{_escape_ffmpeg_filter_path(hook_file)}':expansion=none:"
        f"x=(w-tw)/2:y=h*{y:.3f}:fontsize=h/24:fontcolor=white:line_spacing=6:text_align=center:"
        f"box=1:boxcolor=black@0.72:boxborderw=32:{fade}",
        # drawbox: 'w'/'h' = ukuran kotak itu sendiri, ukuran video = iw/ih
        f"drawbox=x=iw*0.25:y=ih*{y:.3f}-46:w=iw*0.5:h=10:color=0x{accent}@0.95:t=fill:enable='lte(t,2.4)'",
    ]
    return filters, [hook_file]


def extras_filters(output_path: str, duration: float, add_watermark: bool = False, end_cta: bool = True) -> Tuple[List[str], List[str]]:
    """Filter watermark + CTA akhir. Return (filters, file_teks_sementara).

    FIX v4: 'end screen' dulu tampil di AWAL klip (lte(t,2.8)) dan mengulang hook bumper.
    Sekarang CTA (END_SCREEN_TEXT) muncul di 2.5 detik TERAKHIR dengan fade-in.
    """
    filters, files = [], []
    font = _drawtext_font()
    if add_watermark or config.ADD_WATERMARK:
        watermark_file = _write_drawtext_file(output_path, "watermark", editing.strip_emoji(config.WATERMARK_TEXT))
        files.append(watermark_file)
        filters.append(
            f"drawtext={font}textfile='{_escape_ffmpeg_filter_path(watermark_file)}':expansion=none:"
            "x=w-tw-36:y=48:fontsize=h/60:fontcolor=white@0.7:borderw=2:bordercolor=black@0.4"
        )
    if end_cta and duration > 6:
        cta_file = _write_drawtext_file(output_path, "cta", _wrap_lines(config.END_SCREEN_TEXT, width=22, max_lines=2))
        files.append(cta_file)
        t0 = max(duration - 2.5, 0)
        filters.append(
            f"drawtext={font}textfile='{_escape_ffmpeg_filter_path(cta_file)}':expansion=none:"
            # di zona aman atas (di bawah UI platform): tidak menimpa wajah/badan pembicara maupun caption
            f"x=(w-tw)/2:y=h*0.12:fontsize=h/38:fontcolor=white:line_spacing=6:text_align=center:"
            f"box=1:boxcolor=black@0.5:boxborderw=20:"
            f"alpha='if(lt(t,{t0:.2f}),0,min(1,(t-{t0:.2f})/0.35))':enable='gte(t,{t0:.2f})'"
        )
    return filters, files


def _apply_overlay_filters(input_path: str, output_path: str, filters: List[str], files: List[str], label: str) -> None:
    """Untuk pemanggilan terpisah (kompatibilitas): render filter teks dalam satu encode."""
    if not filters:
        shutil.copy2(input_path, output_path)
        return
    cmd = ["ffmpeg", "-y", "-i", input_path, "-vf", ",".join(filters), *_video_encode_args(crf=18),
           "-c:a", "copy", output_path]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, encoding="utf-8", errors="replace")
    except subprocess.CalledProcessError as exc:
        logger.warning(f"{label} gagal, klip dipakai tanpa overlay: {(exc.stderr or '')[-300:]}")
        shutil.copy2(input_path, output_path)
    finally:
        for text_file in files:
            Path(text_file).unlink(missing_ok=True)


def add_extras(input_path: str, output_path: str, clip_info: Dict, add_watermark: bool = False, end_cta: bool = True):
    """Watermark + CTA akhir sebagai encode terpisah (pipeline utama kini menggabungnya ke polish render)."""
    duration = float(get_video_info(input_path).get("duration") or 0)
    filters, files = extras_filters(output_path, duration, add_watermark=add_watermark, end_cta=end_cta)
    _apply_overlay_filters(input_path, output_path, filters, files, "Watermark/CTA")


def add_intro_bumper(input_path: str, output_path: str, clip_info: Dict):
    """Kartu judul hook sebagai encode terpisah (pipeline utama kini menggabungnya ke polish render)."""
    filters, files = bumper_filters(clip_info, output_path)
    _apply_overlay_filters(input_path, output_path, filters, files, "Intro bumper")


def build_fyp_summary(clips: List[Dict]) -> Dict:
    if not clips:
        return {
            "best_clip": None,
            "average_score": 0,
            "recommended_hashtags": [],
            "upload_order": []
        }

    best_clip = max(clips, key=lambda clip: clip.get("score", 0))
    hashtag_counts = {}
    for clip in clips:
        for tag in clip.get("hashtags", []):
            hashtag_counts[tag] = hashtag_counts.get(tag, 0) + 1

    recommended_hashtags = [
        tag for tag, _ in sorted(hashtag_counts.items(), key=lambda item: item[1], reverse=True)
    ][:config.MAX_HASHTAGS]

    return {
        "best_clip": {
            "file_path": best_clip.get("file_path"),
            "score": best_clip.get("score"),
            "hook_text": best_clip.get("hook_text"),
            "upload_caption": best_clip.get("upload_caption"),
            "hashtags": best_clip.get("hashtags", [])
        },
        "average_score": round(sum(c.get("score", 0) for c in clips) / len(clips), 1),
        "recommended_hashtags": recommended_hashtags,
        "upload_order": [clip.get("file_path") for clip in sorted(clips, key=lambda item: item.get("score", 0), reverse=True)]
    }

def json_safe(value):
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [json_safe(item) for item in value]
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, Path):
        return str(value)
    return value


# ==================== NEW v3: SCENE SNAP / PEAK THUMB / PUNCH ZOOM ====================

def _scan_windows(clips_data: Optional[List[Dict]], pad: float) -> Optional[List[Tuple[float, float]]]:
    """Jendela [t-pad, t+pad] di sekitar awal/akhir tiap klip, digabung bila tumpang tindih."""
    if not clips_data:
        return None
    raw = sorted((max(0.0, float(t) - pad), float(t) + pad)
                 for c in clips_data for t in (c.get("start", 0), c.get("end", 0)))
    merged: List[Tuple[float, float]] = []
    for a, b in raw:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def _detect_scene_cuts(video_path: str, clips_data: Optional[List[Dict]] = None) -> Optional[List[float]]:
    """Deteksi batas scene dengan PySceneDetect (dependensi opsional).

    Snap hanya butuh batas scene di dekat awal/akhir klip (± SCENE_SNAP_TOLERANCE), jadi bila
    clips_data diberikan yang dipindai hanya jendela kecil di sekitar titik itu — dulu seluruh
    video dipindai (±8 menit untuk podcast 1 jam di CPU)."""
    if not config.SCENE_SNAP_ENABLED:
        return None
    try:
        from scenedetect import open_video, SceneManager
        from scenedetect.detectors import ContentDetector
        video = open_video(video_path)
        cuts = set()
        windows = _scan_windows(clips_data, pad=float(config.SCENE_SNAP_TOLERANCE) + 1.5)
        if windows is None:
            manager = SceneManager()
            manager.add_detector(ContentDetector(threshold=config.SCENE_DETECT_THRESHOLD))
            manager.detect_scenes(video, show_progress=False)
            for scene in manager.get_scene_list():
                cuts.add(float(scene[0].get_seconds()))
                cuts.add(float(scene[1].get_seconds()))
        else:
            for a, b in windows:
                manager = SceneManager()
                manager.add_detector(ContentDetector(threshold=config.SCENE_DETECT_THRESHOLD))
                video.seek(a)
                manager.detect_scenes(video, end_time=b, show_progress=False)
                # awal scene pertama = titik seek, bukan cut sungguhan
                for scene in manager.get_scene_list()[1:]:
                    cuts.add(float(scene[0].get_seconds()))
        cuts = sorted(cut for cut in cuts if cut >= 0)
        logger.info(f"  Scene detection: {len(cuts)} boundaries found")
        return cuts
    except Exception as exc:
        logger.warning(f"Scene detection unavailable ({str(exc)[:120]}). Skipping scene snapping.")
        return None


def _nearest_boundary(time_sec: float, cuts: List[float], tolerance: float) -> Optional[float]:
    best = None
    for cut in cuts:
        distance = abs(cut - time_sec)
        if distance <= tolerance and (best is None or distance < abs(best - time_sec)):
            best = cut
    return best


def _snap_clip_to_scene_cuts(clip_info: Dict, cuts: Optional[List[float]]) -> Dict:
    """Snap start/end klip ke batas scene terdekat agar potongan terasa alami."""
    if not cuts or config.SCENE_SNAP_TOLERANCE <= 0:
        return clip_info
    start, end = float(clip_info["start"]), float(clip_info["end"])
    new_start = _nearest_boundary(start, cuts, config.SCENE_SNAP_TOLERANCE)
    new_end = _nearest_boundary(end, cuts, config.SCENE_SNAP_TOLERANCE)
    if new_start is None and new_end is None:
        return clip_info
    # FIX v4: snap hanya boleh di area HENING — tidak memotong kata pertama/terakhir
    # (dulu akhir klip bisa digeser ke scene cut di tengah kalimat).
    words = [w for w in clip_info.get("words") or [] if str(w.get("word", "")).strip()]
    if words:
        first_word = min(float(w["start"]) for w in words)
        last_word = max(float(w["end"]) for w in words)
        if new_start is not None and not (start - 0.6 <= new_start <= first_word - 0.05):
            new_start = None
        if new_end is not None and not (last_word + 0.05 <= new_end <= end + 0.6):
            new_end = None
        if new_start is None and new_end is None:
            return clip_info
    candidate_start = new_start if new_start is not None else start
    candidate_end = new_end if new_end is not None else end
    if candidate_end - candidate_start < config.MIN_CLIP_DURATION * 0.6:
        return clip_info
    if candidate_end - candidate_start > config.MAX_CLIP_DURATION + 6:
        return clip_info
    adjusted = dict(clip_info)
    adjusted["start"] = candidate_start
    adjusted["end"] = candidate_end
    adjusted["duration"] = candidate_end - candidate_start
    logger.debug(f"  Scene snap: {start:.1f}-{end:.1f} -> {candidate_start:.1f}-{candidate_end:.1f}")
    return adjusted


def _peak_thumb_time(clip_info: Dict) -> float:
    """Pilih timestamp thumbnail pada kata paling 'panas' (keyword viral / kata panjang)."""
    words = clip_info.get("words") or []
    if not words:
        return (clip_info["start"] + clip_info["end"]) / 2
    all_keywords = {word for words_list in config.VIRAL_KEYWORDS.values() for word in words_list}
    best_word = None
    best_weight = -1.0
    for item in words:
        text = str(item.get("word", "")).strip().lower()
        if not text:
            continue
        weight = 0.0
        if text in all_keywords:
            weight += 3.0
        if len(text) >= 7:
            weight += 0.5
        if weight > best_weight:
            best_weight = weight
            best_word = item
    if best_word is not None and best_weight > 0:
        return float((best_word.get("start", 0) + best_word.get("end", 0)) / 2)
    return (clip_info["start"] + clip_info["end"]) / 2



