#!/usr/bin/env python3
"""Campaign / MotionKlip export — dipindahkan dari Clipper.

Satu campaign = satu MP4 final 1080x1920 @ 60 fps yang dirakit dari:
    - crop facecam + gameplay (layout split) atau frame penuh (layout full)
    - banner/watermark MotionKlip (strip penuh atau mengambang)
    - judul bernarasi (freeze di awal)
    - subtitle karaoke per kata
    - outro pendek

Preset JSON (lihat data/presets/motion-klip-gtavi-split.json):
    layout, facecam_rect, gameplay_rect, subtitle_provider, intro_voice,
    highlight, watermark, watermark_width, watermark_strip, watermark_top,
    watermark_y_offset, outro, outro_seconds, hashtags, discord_tag

Perbedaan dari Clipper lama (disengaja):
    - geometri bisa diatur lewat preset (facecam_height, subtitle_height, fps)
    - preset punya schema_version + validasi di awal, bukan gagal di tengah render
    - encoder memakai core/ffx.venc (NVENC bila tersedia), bukan libx264 hardcode
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import tempfile
import textwrap
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from loguru import logger

import config
from core import ffx

CAMPAIGN_FPS = int(os.getenv("CAMPAIGN_FPS", "60"))
DEFAULT_FACECAM_HEIGHT = 730
DEFAULT_SUBTITLE_HEIGHT = 188
CAMPAIGN_W, CAMPAIGN_H = 1080, 1920
PRESET_SCHEMA_VERSION = 1

class CampaignError(ValueError):
    """Preset atau parameter campaign tidak valid."""


# ==================== Preset ====================
def load_preset(path: Path | str) -> Dict[str, Any]:
    """Baca dan validasi preset. Error jelas sebelum render dimulai."""
    preset_path = Path(path).resolve()
    if not preset_path.is_file():
        raise CampaignError(f"preset campaign tidak ditemukan: {preset_path}")
    try:
        preset = json.loads(preset_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CampaignError(f"preset bukan JSON valid: {exc}") from exc
    if not isinstance(preset, dict):
        raise CampaignError("preset campaign harus berupa objek JSON")

    version = preset.get("schema_version", PRESET_SCHEMA_VERSION)
    if not isinstance(version, int) or version > PRESET_SCHEMA_VERSION:
        raise CampaignError(
            f"schema_version {version!r} tidak didukung (maks {PRESET_SCHEMA_VERSION})")

    # Path aset relatif terhadap file preset.
    for key in ("watermark", "outro"):
        if not isinstance(preset.get(key), str) or not preset[key].strip():
            raise CampaignError(f"preset wajib punya '{key}'")
        asset = (preset_path.parent / Path(preset[key]).expanduser()).resolve()
        if not asset.is_file():
            raise CampaignError(f"aset {key} tidak ditemukan: {asset}")
        preset[key] = str(asset)

    preset.setdefault("layout", "full")
    preset.setdefault("subtitle_provider", None)
    preset.setdefault("intro_voice", "none")
    preset.setdefault("highlight", True)
    preset.setdefault("watermark_width", 864)
    preset.setdefault("watermark_y_offset", 0)
    preset.setdefault("outro_seconds", 3)
    preset.setdefault("hashtags", "")
    preset.setdefault("discord_tag", "")
    preset.setdefault("fps", CAMPAIGN_FPS)
    preset.setdefault("facecam_height", DEFAULT_FACECAM_HEIGHT)
    preset.setdefault("subtitle_height", DEFAULT_SUBTITLE_HEIGHT)

    if preset["layout"] not in ("split", "full"):
        raise CampaignError(f"layout tidak dikenal: {preset['layout']!r}")
    if preset["layout"] == "split":
        if preset.get("facecam_rect") is None or preset.get("gameplay_rect") is None:
            raise CampaignError("layout split butuh facecam_rect dan gameplay_rect")
        campaign_rectangle(preset["facecam_rect"], "facecam_rect")
        campaign_rectangle(preset["gameplay_rect"], "gameplay_rect")
    if type(preset["facecam_height"]) is not int or not 2 <= preset["facecam_height"] < CAMPAIGN_H:
        raise CampaignError("facecam_height harus bilangan bulat 2..1919")
    return preset


def campaign_settings(preset: Dict[str, Any], overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Gabungkan override job (UI/CLI) di atas preset."""
    overrides = overrides or {}
    layout = overrides.get("layout") or (
        "split" if overrides.get("facecam_rect") is not None or overrides.get("gameplay_rect") is not None
        else preset.get("layout", "full"))
    if layout not in ("split", "full"):
        raise CampaignError(f"layout tidak dikenal: {layout!r}")

    face = gameplay = None
    if layout == "split":
        face = campaign_rectangle(
            overrides.get("facecam_rect", preset.get("facecam_rect")), "facecam_rect")
        gameplay = campaign_rectangle(
            overrides.get("gameplay_rect", preset.get("gameplay_rect")), "gameplay_rect")
    elif overrides.get("facecam_rect") is not None or overrides.get("gameplay_rect") is not None:
        raise CampaignError("facecam_rect/gameplay_rect hanya untuk layout split")

    provider = overrides.get("subtitle_provider") or preset.get("subtitle_provider") or "none"
    if provider not in ("none", "whisper", "elevenlabs"):
        raise CampaignError(f"subtitle_provider tidak dikenal: {provider!r}")

    highlight = overrides.get("highlight")
    if highlight is None:
        highlight = preset.get("highlight", True)
    if type(highlight) is not bool:
        raise CampaignError("highlight harus true atau false")

    intro = overrides.get("intro_voice") or preset.get("intro_voice", "none")
    if intro not in ("none", "auto", "elevenlabs", "edge", "edgetts", "openai", "gemini"):
        raise CampaignError(f"intro_voice tidak dikenal: {intro!r}")

    return {
        "layout": layout,
        "facecam_rect": face,
        "gameplay_rect": gameplay,
        "subtitle_provider": provider,
        "highlight": highlight,
        "intro_voice": intro,
        "fps": int(preset.get("fps", CAMPAIGN_FPS)),
        "facecam_height": int(preset.get("facecam_height", DEFAULT_FACECAM_HEIGHT)),
        "subtitle_height": int(preset.get("subtitle_height", DEFAULT_SUBTITLE_HEIGHT)),
    }


# ==================== Geometry ====================
def campaign_rectangle(value: Any, name: str) -> Tuple[float, float, float, float]:
    if isinstance(value, str):
        value = value.split(",")
    try:
        x, y, w, h = map(float, value)
    except (ValueError, TypeError):
        raise CampaignError(f"{name} butuh empat angka normalisasi: x,y,width,height") from None
    if not (all(math.isfinite(v) for v in (x, y, w, h))
            and x >= 0 and y >= 0 and w > 0 and h > 0
            and x + w <= 1.000000001 and y + h <= 1.000000001):
        raise CampaignError(f"{name} harus berada di dalam frame sumber")
    return x, y, w, h


def campaign_crop(value: Any, size: Tuple[int, int], name: str) -> str:
    x, y, w, h = campaign_rectangle(value, name)
    sw, sh = size
    left, top = int(x * sw) // 2 * 2, int(y * sh) // 2 * 2
    right = min(sw, round((x + w) * sw)) // 2 * 2
    bottom = min(sh, round((y + h) * sh)) // 2 * 2
    if right <= left or bottom <= top:
        raise CampaignError(f"{name} lebih kecil dari dua piksel di frame sumber")
    return f"crop={right - left}:{bottom - top}:{left}:{top}"


def campaign_fit(height: int, *, fill: bool = False) -> str:
    scale = f"scale={CAMPAIGN_W}:{height}:force_original_aspect_ratio={'increase' if fill else 'decrease'}"
    position = (f"crop={CAMPAIGN_W}:{height}" if fill
                else f"pad={CAMPAIGN_W}:{height}:(ow-iw)/2:(oh-ih)/2")
    return f"{scale}:force_divisible_by=2:reset_sar=1:flags=lanczos,{position}"


def campaign_audio(input_index: int, has_audio: bool, frames: int, label: str, *,
                   fps: int = CAMPAIGN_FPS, intro_frames: int = 0) -> str:
    if has_audio:
        audio = (f"[{input_index}:a:0]aresample=48000:async=1:first_pts=0,"
                 "aformat=channel_layouts=stereo,apad")
    else:
        audio = "anullsrc=r=48000:cl=stereo"
    samples_per_frame = 48000 // fps
    delay = f",adelay={intro_frames * samples_per_frame}S:all=1" if intro_frames else ""
    return f"{audio},atrim=end_sample={frames * samples_per_frame}{delay},asetpts=N/SR/TB[{label}]"


# ==================== Captions (campaign-specific) ====================
_CONTROLS = r"\x00-\x1f\x7f-\x9f"
_TEXT_ESCAPES = str.maketrans({" ": " ", " ": " "})


def _campaign_text(text: str) -> str:
    return " ".join(re.sub(f"[{_CONTROLS}]", " ", text or "").split()).translate(_TEXT_ESCAPES)


def campaign_title_seconds(title: str) -> float:
    return 2.5 if _campaign_text(title) else 0.0


def _ass_time(seconds: float) -> str:
    cs = max(0, int(round(seconds * 100)))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, c = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


def _word_text(word: Dict[str, Any]) -> str:
    return _campaign_text(str(word.get("word") or word.get("text") or "").strip())


def _clip_words(segments: Sequence[Dict[str, Any]], start: float, end: float) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for seg in segments:
        for w in seg.get("words") or []:
            try:
                s, e = float(w["start"]), float(w["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if not (math.isfinite(s) and math.isfinite(e)) or e < s:
                continue
            if e < start or s > end:
                continue
            text = _word_text(w)
            if text:
                out.append({"text": text, "start": max(s, start) - start, "end": min(e, end) - start})
    out.sort(key=lambda item: item["start"])
    return out


def _paginate(words: List[Dict[str, Any]], max_chars: int = 28) -> List[List[Dict[str, Any]]]:
    pages: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = []
    for w in words:
        if current:
            gap = w["start"] - current[-1]["end"]
            too_long = (w["end"] - current[0]["start"]) > 2.5
            too_many = len(current) >= 4
            too_wide = sum(len(x["text"]) + 1 for x in current) + len(w["text"]) > max_chars
            ended = bool(re.search(r"[.!?]['\")\]]*$", current[-1]["text"]))
            if gap > 0.6 or too_long or too_many or too_wide or ended:
                pages.append(current)
                current = []
        current.append(w)
    if current:
        pages.append(current)
    return pages


def write_campaign_captions(segments: Sequence[Dict[str, Any]], clip_start: float, clip_end: float,
                            out_path: Path, *, title: str = "", font: str = "Arial Black",
                            highlight: bool = True, title_y: int = 1260, subtitle_y: int = 1260,
                            intro_seconds: Optional[float] = None) -> int:
    """Tulis ASS campaign (judul + subtitle per kata) untuk frame 1080x1920."""
    if not 0 <= clip_start < clip_end or not math.isfinite(clip_end):
        raise CampaignError("caption campaign butuh 0 <= clip_start < clip_end")
    if not font.strip() or re.search(r"[,{}\\" + _CONTROLS + "]", font):
        raise CampaignError("font caption harus satu field ASS yang valid")
    font = font.strip()

    duration = clip_end - clip_start
    limit = round(duration * 100)
    if limit / 100 > duration:
        limit -= 1

    events: List[str] = []

    def add_event(layer: int, start: int, end: int, style: str, text: str) -> None:
        if start < end:
            events.append(f"Dialogue: {layer},{_ass_time(start / 100)},{_ass_time(end / 100)},"
                          f"{style},,0,0,0,,{text}")

    title_lines = textwrap.wrap(_campaign_text(title).upper(), width=22, max_lines=3, placeholder="…")
    intro = round((campaign_title_seconds(title) if intro_seconds is None else intro_seconds) * 100)
    if title_lines:
        add_event(1, 0, intro, "CampaignTitle",
                  rf"{{\pos(540,{title_y})}}" + r"\N".join(title_lines))
    position = rf"{{\pos(540,{subtitle_y})}}"

    words = _clip_words(segments, clip_start, clip_end)

    def add_subtitle(start: int, end: int, text: str) -> None:
        add_event(0, start + intro, end + intro, "CampaignSubtitle", position + text)

    pages = _paginate(words)
    for page_index, page in enumerate(pages):
        page_start = round(page[0]["start"] * 100)
        page_end = min(limit, max(round(page[-1]["end"] * 100), round(page[-1]["start"] * 100) + 1))
        if page_index + 1 < len(pages):
            # Jangan biarkan halaman ini menutupi halaman berikutnya.
            page_end = min(page_end, round(pages[page_index + 1][0]["start"] * 100))
        if page_end <= page_start:
            continue
        texts = [w["text"] for w in page]
        plain = " ".join(texts)
        if not highlight:
            add_subtitle(page_start, page_end, plain)
            continue
        for index, word in enumerate(page):
            wstart = round(word["start"] * 100)
            following = page_end
            if index + 1 < len(page):
                following = min(page_end, round(page[index + 1]["start"] * 100))
            active_end = min(following, max(wstart + 1, round(word["end"] * 100)))
            if wstart < active_end:
                coloured = texts.copy()
                coloured[index] = r"{\1c&H00FFFF&}" + word["text"] + r"{\1c&HFFFFFF&}"
                add_subtitle(wstart, active_end, " ".join(coloured))
            add_subtitle(active_end, following, plain)

    header = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n"
        "WrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: CampaignTitle,{font},80,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,"
        "-1,0,0,0,100,100,0,0,1,9,0,5,76,76,0,1\n"
        f"Style: CampaignSubtitle,{font},76,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,"
        "-1,0,0,0,100,100,0,0,1,8,0,5,76,76,0,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    out_path.write_text(header + "".join(event + "\n" for event in events), encoding="utf-8")
    return len(events)


# ==================== Render ====================

def finish_with_banner_and_outro(clip: Path | str, preset: Dict[str, Any], out_path: Path | str,
                                 *, fps: int = CAMPAIGN_FPS, crf: int = 16) -> Path:
    """Tempel banner + outro ke klip yang SUDAH diedit.

    Dua pass terpisah, keduanya berhingga:
      1. overlay banner pada klip (audio di-copy)
      2. sambung outro lewat ffx.append_outro (sudah dipakai produksi)
    Pendekatan satu-graph dengan `-loop 1` menggantung karena input watermark
    tak berujung, jadi jangan digabung.
    """
    from core import ffx

    clip = Path(clip)
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    bannered = target.with_name(target.stem + ".banner" + target.suffix)
    watermark = Path(preset["watermark"])
    info = ffx.probe(str(clip))
    w, h = int(info["width"]), int(info["height"])

    strip = preset.get("watermark_strip")
    strip_top = preset.get("watermark_top")
    if strip and strip_top is None:
        strip_top = h - (strip[1] - strip[0])
    if strip:
        sh = strip[1] - strip[0]
        graph = (f"[1:v]format=rgba,crop={w}:{sh}:0:{strip[0]}[wm];"
                 f"[0:v][wm]overlay=x=0:y={strip_top}:eof_action=repeat[v]")
    else:
        ww = int(preset.get("watermark_width", 864))
        offset = int(preset.get("watermark_y_offset", 0))
        graph = (f"[1:v]format=rgba,scale={ww}:-2[wm];"
                 f"[0:v][wm]overlay=x=(W-w)/2:y=(H-h)/2+({offset}):eof_action=repeat[v]")

    ffx.run(["-i", str(clip), "-i", str(watermark),
             "-filter_complex", graph, "-map", "[v]", "-map", "0:a?",
             "-c:a", "copy", "-r", str(fps), *ffx.venc(crf),
             "-movflags", "+faststart", str(bannered)], timeout=1200)
    try:
        ffx.append_outro(str(bannered), str(target), str(preset["outro"]),
                         image_sec=float(preset.get("outro_seconds", 3)))
    finally:
        bannered.unlink(missing_ok=True)
    return target



def render_edited(video_path: Path | str, preset_path: Path | str, *, start: float,
                  end: Optional[float], output_dir: Path | str, params: Optional[Dict[str, Any]] = None,
                  title: str = "", model: Optional[str] = None) -> Dict[str, Any]:
    """Campaign dengan EDITING PENUH: process_clips → banner → outro.

    Satu-satunya beda dari klip biasa: layout split dari preset + banner + outro.
    Fallback ke render_campaign lama bila pipeline editing gagal.
    """
    from core import ffx, tools
    from core.clipper import detect_stream_layout, process_clips
    from core.transcriber import transcribe_video

    params = params or {}
    preset = load_preset(preset_path)
    settings = campaign_settings(preset, {k: params[k] for k in (
        "layout", "facecam_rect", "gameplay_rect", "subtitle_provider", "highlight") if params.get(k)})
    out_dir = Path(output_dir); out_dir.mkdir(parents=True, exist_ok=True)
    info = ffx.probe(str(video_path))
    total = float(info.get("duration") or 0)
    end = total if end in (None, "") else min(float(end), total)
    duration = max(0.5, end - float(start))

    cut = out_dir / "cut.mp4"
    ffx.run(["-ss", f"{float(start):.3f}", "-t", f"{duration:.3f}", "-i", str(video_path),
             "-c", "copy", "-movflags", "+faststart", str(cut)], timeout=600)
    try:
        segments, _ = transcribe_video(str(cut), model_name=model, word_timestamps=True,
                                       device=params.get("whisper_device") or None)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Transkripsi campaign gagal ({str(exc)[:160]}); caption dilewati")
        segments = []
    if params.get("streamer_only", True) and segments:
        # Dialog game ikut tertranskrip (audio siaran satu campuran) — buang
        # segmen yang kosakatanya didominasi bahasa lain, caption tetap milik streamer.
        try:
            from core.transcriber import drop_foreign_segments
            segments = drop_foreign_segments(segments, str(params.get("language") or config.WHISPER_LANGUAGE or ""))
        except Exception:  # noqa: BLE001
            pass

    layout = None
    if settings["layout"] == "split":
        cam, game = settings["facecam_rect"], settings["gameplay_rect"]
        if not cam:
            try:
                det = detect_stream_layout(str(cut))
                if det:
                    cam, game = det["cam_box"], _gameplay_rect_from_cam(det["cam_box"])
            except Exception:  # noqa: BLE001
                pass
        layout = {"cam_box": list(cam), "game_box": list(game),
                  "cam_ratio": settings["facecam_height"] / 1920.0}

    clip = {"start": 0.0, "end": duration, "title": title or Path(video_path).stem,
            "hook_text": title, "score": 100, "subtitle_segments": segments,
            "words": [w for seg in segments for w in seg.get("words") or []]}
    fields = {**tools.render_options(params), "stream_layout": layout, "enable_captions": True}
    if layout is None:
        fields["layout"] = "off"  # preset tanpa split: jangan auto-deteksi facecam
    # Render editing pada fps campaign (default 60). Tanpa ini process_clips
    # memakai editing.OUTPUT_FPS=30, lalu pass banner hanya menduplikasi frame
    # ke 60 — hasilnya 60 fps semu, gerakan tetap 30 fps.
    from core import editing
    prev_fps = editing.OUTPUT_FPS
    editing.OUTPUT_FPS = max(24, min(60, int(settings.get("fps") or CAMPAIGN_FPS)))
    try:
        try:
            edited = process_clips(str(cut), [clip], out_dir, **fields)
            src = Path(edited[0]["file_path"])
        except Exception as exc:  # noqa: BLE001 - fallback ke renderer lama
            logger.warning(f"Pipeline editing gagal ({str(exc)[:160]}); memakai renderer campaign lama")
            cut.unlink(missing_ok=True)
            return render_campaign(video_path, preset_path, start=start, end=end,
                                   output_dir=out_dir, title=title,
                                   overrides={k: params[k] for k in ("subtitle_provider", "intro_voice") if params.get(k)},
                                   model=model)
    finally:
        editing.OUTPUT_FPS = prev_fps

    final = src.with_name(f"{src.stem}_campaign.mp4")
    finish_with_banner_and_outro(src, preset, final)
    cut.unlink(missing_ok=True)
    return {
        "output": str(final), "layout": settings["layout"], "fps": CAMPAIGN_FPS,
        "duration": round(duration, 3), "subtitle_provider": settings["subtitle_provider"],
        "intro_voice": "none", "hashtags": preset.get("hashtags", ""),
        "discord_tag": preset.get("discord_tag", ""),
        "transcript_text": " ".join(str(seg.get("text") or "").strip() for seg in segments).strip()[:8000],
    }


def _gameplay_rect_from_cam(cam: List[float]) -> List[float]:
    x, y, w, h = cam
    cx, cy = x + w / 2, y + h / 2
    if cx > 0.55:
        # Clipper memakai lebar 0.79 walau cam mulai di 0.775 — sisakan hampir
        # seluruh frame, jangan potong selebar kotak cam.
        return [0.0, 0.0, round(max(0.55, min(0.79, x)), 4), 1.0]
    if cx < 0.45:
        return [round(min(0.8, x + w), 4), 0.0, round(max(0.2, 1.0 - (x + w)), 4), 1.0]
    if cy > 0.5:
        return [0.0, 0.0, 1.0, round(max(0.2, y), 4)]
    return [0.0, round(min(0.8, y + h), 4), 1.0, round(max(0.2, 1.0 - (y + h)), 4)]


def render_campaign(video_path: Path | str, preset_path: Path | str, *, start: float,
                    end: Optional[float] = None, output_dir: Path | str,
                    title: str = "", overrides: Optional[Dict[str, Any]] = None,
                    caption_font: str = "Arial Black",
                    model: Optional[str] = None, device: Optional[str] = None,
                    compute_type: Optional[str] = None) -> Dict[str, Any]:
    """Render satu cut campaign. Return dict (bukan Path) agar cocok dengan job vak."""
    preset = load_preset(preset_path)
    settings = campaign_settings(preset, overrides)
    fps = settings["fps"]
    facecam_height = settings["facecam_height"]
    subtitle_height = settings["subtitle_height"]
    gameplay_height = CAMPAIGN_H - facecam_height

    watermark = Path(preset["watermark"])
    outro = Path(preset["outro"])
    if ffx.probe(str(watermark))["width"] != CAMPAIGN_W or ffx.probe(str(watermark))["height"] != CAMPAIGN_H:
        raise CampaignError("watermark harus PNG template asli 1080x1920")
    watermark_width = preset["watermark_width"]
    if type(watermark_width) is not int or not 2 <= watermark_width <= CAMPAIGN_W or watermark_width % 2:
        raise CampaignError("watermark_width harus bilangan genap 2..1080")

    outro_info = ffx.probe(str(outro))
    outro_asset_duration = float(outro_info["duration"] or 0)
    outro_duration = float(preset["outro_seconds"])
    if not 1 / fps <= outro_duration <= outro_asset_duration:
        raise CampaignError(f"outro_seconds harus muat di dalam outro {outro_asset_duration:.3f} s")

    video_path = Path(video_path).resolve()
    if not video_path.is_file():
        raise CampaignError(f"video sumber tidak ditemukan: {video_path}")
    src = ffx.probe(str(video_path))
    source_duration = float(src["duration"] or 0)
    source_has_audio = bool(src["has_audio"])
    end = source_duration if end is None else float(end)
    if (not math.isfinite(end) or not math.isfinite(start) or start < 0
            or end > source_duration + 1 / fps or end <= start):
        raise CampaignError(f"pilih rentang di dalam sumber {source_duration:.3f} s")
    end = min(end, source_duration)
    frames = math.floor((end - start) * fps + 1e-6)
    if frames < 1:
        raise CampaignError(f"pilih minimal satu frame {fps} fps")
    duration = frames / fps
    outro_frames = math.floor(outro_duration * fps + 1e-6)
    outro_duration = outro_frames / fps

    layout = settings["layout"]
    provider = settings["subtitle_provider"]
    highlight = settings["highlight"]
    captions_on = provider != "none"
    if provider == "elevenlabs" and not source_has_audio:
        raise CampaignError("subtitle ElevenLabs butuh audio di sumber")

    title = title or ""
    intro_frames = round(campaign_title_seconds(title) * fps) if title else 0

    title_y = subtitle_y = 1260
    strip = preset.get("watermark_strip") if layout == "split" else None
    strip_top = preset.get("watermark_top") if layout == "split" else None
    if strip_top is not None and strip is None:
        raise CampaignError("watermark_top butuh watermark_strip")
    footer = strip is not None and strip_top is None
    strip_height = 0
    if strip is not None:
        if (not isinstance(strip, list) or len(strip) != 2
                or any(type(y) is not int or y % 2 for y in strip)
                or not 0 <= strip[0] < strip[1] <= CAMPAIGN_H):
            raise CampaignError("watermark_strip butuh dua baris piksel genap yang menaik di 1920px")
        if watermark_width != CAMPAIGN_W:
            raise CampaignError("watermark_strip butuh watermark selebar 1080px")
        strip_height = strip[1] - strip[0]
        if footer:
            strip_top = CAMPAIGN_H - strip_height
            gameplay_height = strip_top - subtitle_height - facecam_height
            if gameplay_height < 2:
                raise CampaignError("watermark_strip menyisakan ruang kosong untuk gameplay")
        elif (type(strip_top) is not int or strip_top % 2
              or not facecam_height + subtitle_height < strip_top <= CAMPAIGN_H - strip_height):
            raise CampaignError("watermark_top harus genap, di bawah facecam, dan menyisakan ruang subtitle")
        title_y = (facecam_height + strip_top - subtitle_height) // 2
        subtitle_y = strip_top - subtitle_height // 2

    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    output = out_dir / f"{video_path.stem}_{start:.3f}-{end:.3f}s_motion-klip.mp4"
    if output.exists():
        raise CampaignError(f"export campaign sudah ada: {output}")

    def video_trim(frame_count: int) -> str:
        return (f"fps={fps}:start_time=0,format=yuv420p,tpad=stop=9:stop_mode=clone,"
                f"trim=end_frame={frame_count},setpts=PTS-STARTPTS")

    graph: List[str] = [f"[0:v:0]{video_trim(frames)}[source]"]
    if layout == "split":
        size = ffx.display_size(str(video_path))
        face = campaign_crop(settings["facecam_rect"], size, "facecam_rect")
        gameplay = campaign_crop(settings["gameplay_rect"], size, "gameplay_rect")
        bg_height = max(2, round(gameplay_height / 8) * 2)
        stack_output = "scenes" if footer else "base"
        graph.extend([
            "[source]split=2[face_src][game_src]",
            f"[face_src]{face},{campaign_fit(facecam_height, fill=True)}[face]",
            f"[game_src]{gameplay},split=2[game_fit_src][game_bg_src]",
            f"[game_fit_src]scale={CAMPAIGN_W}:{gameplay_height}:force_original_aspect_ratio=decrease:"
            "force_divisible_by=2:reset_sar=1:flags=lanczos[game_fit]",
            f"[game_bg_src]scale=270:{bg_height}:force_original_aspect_ratio=increase:"
            f"force_divisible_by=2:reset_sar=1,crop=270:{bg_height},"
            f"gblur=sigma=10,scale={CAMPAIGN_W}:{gameplay_height}[game_bg]",
            "[game_bg][game_fit]overlay=x=(W-w)/2:y=0[game]",
            f"[face][game]vstack[{stack_output}]",
        ])
        if footer:
            graph.append(f"[scenes]pad={CAMPAIGN_W}:{CAMPAIGN_H}:0:0:color=0x101318[base]")
    else:
        graph.append(f"[source]{campaign_fit(CAMPAIGN_H)}[base]")

    if strip is not None:
        graph.extend([
            f"[1:v:0]format=rgba,crop={CAMPAIGN_W}:{strip_height}:0:{strip[0]}[watermark]",
            f"[base][watermark]overlay=x=0:y={strip_top}:eof_action=repeat[main]",
        ])
    else:
        offset = preset.get("watermark_y_offset", 0) if layout == "split" else 0
        if type(offset) is not int or abs(offset) > 960:
            raise CampaignError("watermark_y_offset harus -960..960")
        graph.extend([
            f"[1:v:0]format=rgba,scale={watermark_width}:-2[watermark]",
            f"[base][watermark]overlay=x=(W-w)/2:y=(H-h)/2+({offset}):eof_action=repeat[main]",
        ])

    review = {
        "layout": layout,
        "fps": fps,
        "duration": round(duration, 3),
        "outro_duration": round(outro_duration, 3),
        "subtitle_provider": provider,
        "intro_voice": settings["intro_voice"],
        "hashtags": preset.get("hashtags", ""),
        "discord_tag": preset.get("discord_tag", ""),
        "transcript_text": "",
    }

    print(f"[campaign] {layout} layout. Outro: {outro_duration:.3f} s dari {outro.name}")
    with tempfile.TemporaryDirectory(prefix="klip-campaign-", dir=out_dir) as work:
        work_dir = Path(work)
        narration_input: List[str] = []
        if intro_frames and settings["intro_voice"] != "none":
            try:
                from core import media_ai

                # "auto" = ElevenLabs dulu, lalu fallback lokal (edge/openai/gemini).
                intro_provider = settings["intro_voice"]
                if intro_provider == "auto":
                    intro_provider = "elevenlabs"
                elif intro_provider in ("edgetts", "edge-tts"):
                    intro_provider = "edge"
                narration = work_dir / "intro.mp3"
                result = media_ai.tts(title, str(narration), provider=intro_provider)
                spoken = float(result.get("duration") or 0)
                if spoken > 0:
                    intro_frames = max(intro_frames, math.ceil((spoken + 0.10) * 20) * (fps // 20))
                    narration_input = ["-i", str(narration)]
                    print(f"[campaign] intro {result.get('provider')} "
                          f"{spoken:.2f}s, freeze {intro_frames / fps:.2f}s")
            except Exception as exc:  # noqa: BLE001 - intro opsional
                logger.warning(f"intro voice gagal ({str(exc)[:200]}); render tanpa narasi")

        video_label = "main"
        if intro_frames:
            graph.append(f"[main]tpad=start={intro_frames}:start_mode=clone[with_intro]")
            video_label = "with_intro"

        segments: List[Dict[str, Any]] = []
        cut_input = ["-ss", f"{start:.6f}", "-t", f"{duration:.6f}", "-i", str(video_path)]
        if captions_on and source_has_audio:
            from core import transcriber

            try:
                # Transkripsi HANYA potongan yang dipakai, bukan seluruh VOD.
                cut_audio = work_dir / "audio.wav"
                ffx.run(["-ss", f"{start:.6f}", "-t", f"{duration:.6f}", "-i", str(video_path),
                         "-map", "0:a:0", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
                         str(cut_audio)])
                segments, _info = transcriber.transcribe_video(
                    str(cut_audio), model_name=model,
                    word_timestamps=True,
                    device=device or ("elevenlabs" if provider == "elevenlabs" else None),
                    compute_type=compute_type,
                )
                # Audio potongan mulai dari 0, jadi tidak perlu rebase.
                review["transcript_text"] = " ".join(
                    str(seg.get("text") or "").strip() for seg in segments).strip()[:8000]
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"transkripsi campaign gagal ({str(exc)[:200]}); caption dilewati")

        work_ass = work_dir / "captions.ass"
        if captions_on or intro_frames:
            write_campaign_captions(
                segments, 0, duration, work_ass, title=title, font=caption_font,
                highlight=highlight, title_y=title_y, subtitle_y=subtitle_y,
                intro_seconds=intro_frames / fps)
        if work_ass.is_file():
            graph.append(f"[{video_label}]subtitles=captions.ass[main_sub]")
            video_label = "main_sub"

        if narration_input:
            graph.extend([
                campaign_audio(3, True, intro_frames, "voice_audio", fps=fps),
                campaign_audio(0, source_has_audio, frames, "clip_audio", fps=fps),
                "[voice_audio][clip_audio]concat=n=2:v=0:a=1[main_audio]",
            ])
        else:
            graph.append(campaign_audio(0, source_has_audio, frames, "main_audio",
                                        fps=fps, intro_frames=intro_frames))
        graph.extend([
            f"[2:v:0]{video_trim(outro_frames)},{campaign_fit(CAMPAIGN_H)}[outro]",
            campaign_audio(2, bool(outro_info.get("has_audio")), outro_frames, "outro_audio", fps=fps),
            f"[{video_label}][main_audio][outro][outro_audio]concat=n=2:v=1:a=1[v][a]",
        ])

        staged = work_dir / "export.mp4"
        cmd = [
            "-nostdin", *cut_input, "-i", str(watermark), "-t", f"{outro_duration:.6f}", "-i", str(outro),
            *narration_input, "-filter_complex", ";".join(graph),
            "-map", "[v]", "-map", "[a]", "-map_metadata", "-1", "-map_chapters", "-1",
            "-r", str(fps), *ffx.venc(16), *ffx.AENC, "-movflags", "+faststart", str(staged),
        ]
        ffx.run(cmd, cwd=str(work_dir))
        os.link(staged, output)
        if work_ass.is_file():
            shutil.copy2(work_ass, output.with_suffix(".ass"))

    review["output"] = str(output)
    print(f"[campaign] exported {output}")
    if preset.get("hashtags"):
        print(f"[campaign] hashtags: {preset['hashtags']}")
    if preset.get("discord_tag"):
        print(f"[campaign] Discord tag: {preset['discord_tag']}")
    return review


__all__ = [
    "CampaignError", "CAMPAIGN_FPS", "PRESET_SCHEMA_VERSION",
    "load_preset", "campaign_settings", "campaign_rectangle", "campaign_crop",
    "campaign_fit", "campaign_audio", "campaign_title_seconds",
    "write_campaign_captions", "render_campaign",
]
