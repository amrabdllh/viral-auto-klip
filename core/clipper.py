"""
Video Clipping, Face Reframing, Captioning & Encoding Engine
"""
import os
import json
import subprocess
import tempfile
import shutil
from pathlib import Path
from typing import List, Dict, Tuple, Optional
import cv2
import mediapipe as mp
import numpy as np
from moviepy.editor import VideoFileClip, TextClip, CompositeVideoClip, ColorClip
from tqdm import tqdm
from loguru import logger
import config
from .utils import generate_ass_subtitles, smooth_face_trajectory, create_thumbnail, get_video_info
from .fyp_agent import enrich_clip_for_platforms, build_upload_strategy
from .audio_enhancer import enhance_audio, add_background_music_with_duck

mp_face_detection = mp.solutions.face_detection
mp_drawing = mp.solutions.drawing_utils

_NVENC_AVAILABLE = None


def _video_encode_args(crf: int = 20, fast: bool = False) -> List[str]:
    """Return FFmpeg video encoder args tuned for the user's RTX 3060 laptop."""
    if config.USE_NVENC and _has_nvenc():
        cq = max(16, min(28, crf + 1))
        preset = "p1" if fast else config.NVENC_PRESET
        return [
            "-c:v", "h264_nvenc",
            "-preset", preset,
            "-cq", str(cq),
            "-b:v", "0",
            "-pix_fmt", "yuv420p",
        ]
    return ["-c:v", "libx264", "-preset", "ultrafast" if fast else config.PRESET, "-crf", str(crf)]


def _has_nvenc() -> bool:
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
            test = subprocess.run(
                [
                    "ffmpeg", "-hide_banner", "-y",
                    "-f", "lavfi", "-i", "color=s=64x64:r=1",
                    "-t", "0.1",
                    "-c:v", "h264_nvenc",
                    "-f", "null", "-",
                ],
                capture_output=True,
                text=True,
            )
            _NVENC_AVAILABLE = test.returncode == 0
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
) -> List[Dict]:
    """
    Process each viral clip: extract, reframe, caption, encode.
    Returns list of processed clip info with file paths.
    """
    logger.info(f"🎬 Processing {len(clips_data)} viral clips...")
    
    style = dict(config.CAPTION_STYLES.get(style_name, config.CAPTION_STYLES["modern"]))
    if caption_overrides:
        style.update({key: value for key, value in caption_overrides.items() if value is not None})
    processed_clips = []
    
    video_info = get_video_info(video_path)
    
    for idx, clip_info in enumerate(tqdm(clips_data, desc="Clipping")):
        try:
            clip_name = f"clip_{idx+1:03d}_viral{int(clip_info['score'])}_{clip_info['category']}"
            clip_dir = output_dir / clip_name
            clip_dir.mkdir(exist_ok=True)
            
            raw_clip_path = clip_dir / "raw.mp4"
            final_clip_path = clip_dir / f"{clip_name}.mp4"
            subtitle_path = clip_dir / "captions.ass"
            thumb_path = clip_dir / "thumbnail.jpg"
            
            # 1. Extract raw segment
            extract_segment(video_path, clip_info["start"], clip_info["end"], str(raw_clip_path))

            working_clip_path = raw_clip_path
            if virtual_background_path:
                bg_path = Path(virtual_background_path)
                if bg_path.exists():
                    bg_clip_path = clip_dir / "virtual_bg.mp4"
                    apply_virtual_background(str(working_clip_path), str(bg_clip_path), str(bg_path))
                    working_clip_path = bg_clip_path
                else:
                    logger.warning(f"Virtual background file not found: {virtual_background_path}")

            if eye_contact_correction:
                eye_clip_path = clip_dir / "eye_contact.mp4"
                apply_eye_contact_correction(str(working_clip_path), str(eye_clip_path))
                working_clip_path = eye_clip_path
            
            # 2. Face detection & reframing (if enabled)
            face_bboxes = []
            if enable_face_reframe:
                face_bboxes = detect_faces_in_clip(
                    str(working_clip_path),
                    group_framing=group_framing,
                    return_metadata=klipers_reframe,
                )
                if face_bboxes:
                    logger.info(f"  Face detected in clip {idx+1}")
            
            # 3. Reframe (support Conversation Mode + Portrait/Landscape)
            if enable_face_reframe and face_bboxes:
                reframed_path = clip_dir / "reframed.mp4"
                reframe_video(
                    str(working_clip_path), 
                    str(reframed_path), 
                    face_bboxes, 
                    get_video_info(str(working_clip_path)),
                    conversation_mode=conversation_mode,
                    target_aspect=target_aspect,
                    auto_zoom=auto_zoom,
                    split_screen=split_screen,
                    bg_blur=bg_blur,
                    group_framing=group_framing,
                    klipers_reframe=klipers_reframe
                )
                input_for_caption = str(reframed_path)
            else:
                reframed_path = clip_dir / "reframed.mp4"
                reframe_video(
                    str(working_clip_path), 
                    str(reframed_path), 
                    [], 
                    get_video_info(str(working_clip_path)),
                    conversation_mode=conversation_mode,
                    target_aspect=target_aspect,
                    group_framing=group_framing,
                    klipers_reframe=klipers_reframe
                )
                input_for_caption = str(reframed_path)
            
            # NEW: Audio Enhancement (Klipers.pro style)
            enhanced_audio = None
            if enable_audio_enhance:
                enhanced_audio = clip_dir / "enhanced.aac"
                enhance_audio(str(reframed_path), str(enhanced_audio))  # note: enhance works on video too, extracts internally
                input_for_caption = str(reframed_path)  # still use video path, audio will be replaced later if needed
            
            # 4. Generate & burn captions from real Whisper timestamps.
            segments_for_sub = build_subtitle_segments_for_clip(clip_info)
            
            generate_ass_subtitles(
                segments_for_sub, style, str(subtitle_path),
                1080 if target_aspect == "portrait" else 1920,
                1920 if target_aspect == "portrait" else 1080,
            )
            
            # Burn subtitles with ffmpeg
            burn_subtitles(input_for_caption, str(subtitle_path), str(final_clip_path), style)

            if enhanced_audio and enhanced_audio.exists():
                audio_replaced_path = clip_dir / f"{clip_name}_audio.mp4"
                mux_audio(str(final_clip_path), str(enhanced_audio), str(audio_replaced_path))
                final_clip_path = audio_replaced_path

            if background_music_path:
                music_path = Path(background_music_path)
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
                    logger.warning(f"Background music file not found: {background_music_path}")
            
            # NEW: B-Roll Intro Bumper (Klipers.pro style) - simple text intro
            if config.ENABLE_BROLL:
                final_with_broll = clip_dir / f"{clip_name}_broll.mp4"
                add_intro_bumper(str(final_clip_path), str(final_with_broll), clip_info)
                final_clip_path = final_with_broll
            
            # 5. Thumbnail from middle of clip (improved with hook + score)
            mid_time = (clip_info["start"] + clip_info["end"]) / 2
            create_thumbnail(video_path, mid_time, str(thumb_path), 
                           clip_info.get("hook_text", ""), clip_info.get("score", 0))
            
            # 6. Add watermark / end screen if configured
            if add_watermark or config.ADD_END_SCREEN:
                final_with_extra = clip_dir / f"{clip_name}_final.mp4"
                add_extras(str(final_clip_path), str(final_with_extra), clip_info, add_watermark=add_watermark)
                final_clip_path = final_with_extra

            if not Path(final_clip_path).exists() or Path(final_clip_path).stat().st_size == 0:
                raise RuntimeError(f"Final clip was not created: {final_clip_path}")
            
            # Cleanup temp files
            for f in [raw_clip_path, clip_dir / "virtual_bg.mp4", clip_dir / "eye_contact.mp4", clip_dir / "reframed.mp4"]:
                if f.exists():
                    f.unlink()
            
            processed_clip = {
                **clip_info,
                "file_path": str(final_clip_path),
                "thumbnail": str(thumb_path),
                "duration": clip_info["end"] - clip_info["start"]
            }
            processed_clips.append(enrich_clip_for_platforms(processed_clip, target_platform))
            
            logger.success(f"✅ Clip {idx+1} done: {clip_name} (score {clip_info['score']})")
            
        except Exception as e:
            logger.exception(f"Failed to process clip {idx+1}: {e}")
            continue
    
    # Save metadata
    metadata_path = output_dir / "metadata.json"
    with open(metadata_path, "w", encoding="utf-8") as f:
        metadata = {
            "video_source": video_path,
            "total_clips": len(processed_clips),
            "fyp_summary": build_fyp_summary(processed_clips),
            "upload_strategy": build_upload_strategy(processed_clips, target_platform),
            "clips": processed_clips
        }
        json.dump(json_safe(metadata), f, indent=2, ensure_ascii=False)
    
    logger.success(f"🎉 All clips processed! Metadata saved to {metadata_path}")
    return processed_clips

def extract_segment(video_path: str, start: float, end: float, output_path: str):
    """Extract exact segment using ffmpeg (fast & accurate)"""
    duration = end - start
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start),
        "-i", video_path,
        "-t", str(duration),
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
    """Detect faces in video.

    Single mode returns one tracked face per sampled frame.
    Group mode returns a union box around up to 5 visible faces, better for 3+ people.
    """
    bboxes = []
    
    cap = cv2.VideoCapture(video_path)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    last_center = None
    
    with mp_face_detection.FaceDetection(
        model_selection=1, min_detection_confidence=config.FACE_DETECTION_CONFIDENCE
    ) as face_detection:
        
        frame_idx = 0
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break
            
            if frame_idx % sample_rate == 0:
                rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                results = face_detection.process(rgb_frame)
                
                if results.detections:
                    h, w, _ = frame.shape
                    face_boxes = []
                    for detection in results.detections:
                        bbox = detection.location_data.relative_bounding_box
                        x = int(bbox.xmin * w)
                        y = int(bbox.ymin * h)
                        bw = int(bbox.width * w)
                        bh = int(bbox.height * h)
                        box = _clamp_bbox((x, y, bw, bh), w, h)
                        if box is None:
                            continue
                        score = float(detection.score[0]) if detection.score else 0.5
                        face_boxes.append((box, score))
                    best_box = _choose_tracking_face(face_boxes, last_center)
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
    
    cap.release()
    if return_metadata:
        return _fill_short_face_meta_gaps(_smooth_face_metadata(bboxes, window=config.SMOOTHING_FRAMES))
    return _fill_short_face_gaps(smooth_face_trajectory(bboxes, window=config.SMOOTHING_FRAMES))


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
        chunks = []
        current = []
        for word in words:
            current.append(word)
            if len(current) >= 5 or str(word.get("word", "")).endswith((".", "!", "?")):
                chunks.append(current)
                current = []
        if current:
            chunks.append(current)

        return [
            {
                "start": max(chunk[0]["start"] - clip_start, 0),
                "end": min(chunk[-1]["end"] - clip_start, clip_duration),
                "text": " ".join(item["word"] for item in chunk).strip(),
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
        
        # Crop left half for speaker 1, right half for speaker 2
        left_crop = f"crop={width//2}:{height}:0:0,scale={out_w//2}:{out_h}"
        right_crop = f"crop={width//2}:{height}:{width//2}:0,scale={out_w//2}:{out_h}"
        
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
            # Auto zoom: zoom in 20-30% on face
            zoom_factor = 0.75
            face_w, face_h = main_face[2], main_face[3]
            crop_w = int(face_w * zoom_factor)
            crop_h = int(face_h * zoom_factor)
        
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
    writer = cv2.VideoWriter(temp_video, cv2.VideoWriter_fourcc(*"mp4v"), fps, (out_w, out_h))
    if not writer.isOpened():
        cap.release()
        raise RuntimeError("Could not initialize OpenCV video writer for dynamic reframe")

    base_crop_w = min(width, int(height * (9 / 16 if target_aspect == "portrait" else 16 / 9)))
    base_crop_h = min(height, int(base_crop_w * (16 / 9 if target_aspect == "portrait" else 9 / 16)))
    if base_crop_h > height:
        base_crop_h = height
        base_crop_w = min(width, int(base_crop_h * (9 / 16 if target_aspect == "portrait" else 16 / 9)))

    first_box = next((_extract_box(box) for box in face_bboxes if _extract_box(box) is not None), None)
    if first_box:
        fx, fy, fw, fh = first_box
        last_center = np.array([fx + fw / 2, fy + fh * 0.58], dtype=np.float32)
        initial_h = np.clip(fh / 0.24, base_crop_h * 0.75, height)
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
                    desired_h = np.clip(bh / 0.24, base_crop_h * 0.75, height)
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

        last_center = (1 - alpha) * last_center + alpha * target_center
        last_crop = (1 - alpha) * last_crop + alpha * target_crop

        crop_w = int(np.clip(last_crop[0], 16, width))
        crop_h = int(np.clip(last_crop[1], 16, height))
        crop_x = int(np.clip(last_center[0] - crop_w / 2, 0, max(width - crop_w, 0)))
        crop_y = int(np.clip(last_center[1] - crop_h / 2, 0, max(height - crop_h, 0)))

        cropped = frame[crop_y:crop_y + crop_h, crop_x:crop_x + crop_w]
        if cropped.size == 0:
            cropped = frame
        output_frame = cv2.resize(cropped, (out_w, out_h), interpolation=cv2.INTER_LINEAR)
        if bg_blur:
            output_frame = cv2.GaussianBlur(output_frame, (0, 0), 0.35)
        writer.write(output_frame)
        frame_idx += 1

    cap.release()
    writer.release()
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
    writer = cv2.VideoWriter(temp_video, cv2.VideoWriter_fourcc(*"mp4v"), fps, (out_w, out_h))
    if not writer.isOpened():
        cap.release()
        raise RuntimeError("Could not initialize OpenCV video writer for Klipers-style reframe")

    base_crop_w = min(width, int(height * (9 / 16 if target_aspect == "portrait" else 16 / 9)))
    base_crop_h = min(height, int(base_crop_w * (16 / 9 if target_aspect == "portrait" else 9 / 16)))
    if base_crop_h > height:
        base_crop_h = height
        base_crop_w = min(width, int(base_crop_h * (9 / 16 if target_aspect == "portrait" else 16 / 9)))

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
        output_frame = cv2.resize(cropped, (out_w, out_h), interpolation=cv2.INTER_LINEAR)
        writer.write(output_frame)
        frame_idx += 1

    cap.release()
    writer.release()
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
    if face_count >= 3:
        return "group_context"
    if face_count == 2:
        return "two_person"
    if main_box and previous_main_center:
        cx = main_box[0] + main_box[2] / 2
        cy = main_box[1] + main_box[3] / 2
        jump = np.hypot(cx - previous_main_center[0], cy - previous_main_center[1])
        if jump > width * 0.18:
            return "reaction"
    if group_box and group_box[2] > height * 9 / 16 * 0.78:
        return "group_context"
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
        desired_h = np.clip(bh / 0.28, base_h * 0.68, height)
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


def _group_needs_stage_view(face_bboxes: List, width: int, height: int) -> bool:
    valid = [_extract_box(box) for box in face_bboxes if _extract_box(box) is not None]
    if not valid:
        return False
    median_width = float(np.median([box[2] for box in valid]))
    portrait_crop_width = height * 9 / 16
    return median_width > portrait_crop_width * 0.82


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
        *_video_encode_args(crf=20),
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
    """Replace the background using MediaPipe selfie segmentation."""
    selfie_segmentation = mp.solutions.selfie_segmentation
    cap = cv2.VideoCapture(input_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 24
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 1280
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 720

    background = cv2.imread(background_path)
    if background is None:
        cap.release()
        raise FileNotFoundError(f"Could not read virtual background: {background_path}")
    background = _resize_cover(background, width, height)

    temp_video = str(Path(output_path).with_name(Path(output_path).stem + "_video_only.mp4"))
    writer = cv2.VideoWriter(temp_video, cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    if not writer.isOpened():
        cap.release()
        raise RuntimeError("Could not initialize OpenCV video writer for virtual background")

    with selfie_segmentation.SelfieSegmentation(model_selection=1) as segmenter:
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            results = segmenter.process(rgb)
            mask = results.segmentation_mask
            mask = cv2.GaussianBlur(mask, (17, 17), 0)
            alpha = np.clip((mask - 0.18) / 0.45, 0, 1).astype(np.float32)
            alpha = alpha[..., None]
            composed = (frame.astype(np.float32) * alpha + background.astype(np.float32) * (1 - alpha)).astype(np.uint8)
            writer.write(composed)

    cap.release()
    writer.release()
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
    vf = f"subtitles='{escaped_ass_path}':force_style='FontName={config.DEFAULT_FONT},FontSize={style['font_size']}'"
    
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

def add_extras(input_path: str, output_path: str, clip_info: Dict, add_watermark: bool = False):
    """Add watermark and/or end screen"""
    filters = []
    temp_text_files = []
    
    if add_watermark or config.ADD_WATERMARK:
        watermark_file = _write_drawtext_file(output_path, "watermark", config.WATERMARK_TEXT)
        temp_text_files.append(watermark_file)
        filters.append(
            f"drawtext=textfile='{_escape_ffmpeg_filter_path(watermark_file)}':"
            "x=w-tw-28:y=36:fontsize=26:fontcolor=white@0.65:borderw=2:bordercolor=black@0.35"
        )
    
    if config.ADD_END_SCREEN:
        hook_text = clip_info.get("hook_text") or config.END_SCREEN_TEXT
        hook_file = _write_drawtext_file(output_path, "hook", hook_text)
        temp_text_files.append(hook_file)
        filters.append(
            f"drawtext=textfile='{_escape_ffmpeg_filter_path(hook_file)}':"
            "x=(w-tw)/2:y=120:fontsize=42:fontcolor=white:borderw=4:bordercolor=black@0.7:enable='lte(t,2.8)'"
        )
    
    if not filters:
        # Just copy
        shutil.copy2(input_path, output_path)
        return
    
    vf = ",".join(filters)
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vf", vf,
        *_video_encode_args(crf=20),
        "-c:a", "copy",
        output_path
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, encoding="utf-8", errors="replace")
    except subprocess.CalledProcessError as exc:
        logger.error(f"FFmpeg add_extras failed: {exc.stderr or exc.stdout or exc}")
        raise
    finally:
        for text_file in temp_text_files:
            Path(text_file).unlink(missing_ok=True)

def _escape_drawtext(text: str) -> str:
    return str(text).replace("\\", "\\\\").replace(":", r"\:").replace("'", r"\'").replace(",", r"\,")

def _write_drawtext_file(output_path: str, label: str, text: str) -> str:
    text_path = Path(output_path).with_suffix(f".{label}.txt")
    text_path.write_text(str(text), encoding="utf-8")
    return str(text_path)


def add_intro_bumper(input_path: str, output_path: str, clip_info: Dict):
    """Add Niche-specific B-Roll style intro bumper (Klipers.pro inspired)"""
    hook = clip_info.get("hook_text", "Viral moment inside 🔥")
    score = clip_info.get("score", 80)
    niche = clip_info.get("niche", "general")
    
    # Niche-specific styling (Klipers.pro style)
    niche_styles = {
        "gaming":    {"emoji": "🎮", "color": "#FF4500", "accent": "🔥", "bg": "black@0.75"},
        "finance":   {"emoji": "💰", "color": "#00FF7F", "accent": "📈", "bg": "black@0.7"},
        "beauty":    {"emoji": "💄", "color": "#FF69B4", "accent": "✨", "bg": "black@0.65"},
        "motivation":{"emoji": "🚀", "color": "#1E90FF", "accent": "💪", "bg": "black@0.7"},
        "education": {"emoji": "📚", "color": "#FFD700", "accent": "🧠", "bg": "black@0.7"},
        "comedy":    {"emoji": "😂", "color": "#FF6347", "accent": "🤣", "bg": "black@0.75"},
        "relationship":{"emoji": "❤️", "color": "#FF1493", "accent": "💕", "bg": "black@0.65"},
        "tech":      {"emoji": "🤖", "color": "#00CED1", "accent": "⚡", "bg": "black@0.7"},
        "general":   {"emoji": "🔥", "color": "#FFD700", "accent": "✨", "bg": "black@0.7"}
    }
    
    style = niche_styles.get(niche, niche_styles["general"])
    
    vf = (
        f"drawtext=text='{style['emoji']} {hook}':x=(w-tw)/2:y=(h-th)/2-45:fontsize=34:fontcolor=white:"
        f"borderw=3:bordercolor={style['bg']}:enable='lte(t,2.8)',"
        f"drawtext=text='{style['accent']} Score: {int(score)}':x=(w-tw)/2:y=(h-th)/2+15:fontsize=26:fontcolor={style['color']}:"
        f"borderw=2:bordercolor=black:enable='lte(t,2.8)',"
        "fade=t=in:st=0:d=0.6"
    )
    
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vf", vf,
        *_video_encode_args(crf=18),
        "-c:a", "copy",
        output_path
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        logger.info(f"  Niche-specific B-Roll ({niche}) added")
    except:
        shutil.copy2(input_path, output_path)


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
