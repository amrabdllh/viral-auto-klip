"""
Utility functions for Viral Auto Klip
"""
import os
import json
import subprocess
import re
from pathlib import Path
from typing import List, Dict, Tuple, Optional
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from loguru import logger
import config

def setup_logging():
    logger.remove()
    logger.add(
        sink=lambda msg: print(msg, end=""),
        level=config.LOG_LEVEL,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>"
    )

def get_video_info(video_path: str) -> Dict:
    """Get video metadata using ffprobe"""
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", video_path
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        info = json.loads(result.stdout)
        duration = float(info["format"]["duration"])
        video_stream = next((stream for stream in info.get("streams", []) if stream.get("codec_type") == "video"), None)
        if not video_stream:
            raise RuntimeError("No video stream found")
        width = int(video_stream["width"])
        height = int(video_stream["height"])
        return {"duration": duration, "width": width, "height": height, "path": video_path}
    except Exception as e:
        logger.error(f"Error getting video info: {e}")
        return {"duration": 0, "width": 1920, "height": 1080, "path": video_path}

def download_video(
    url: str,
    output_path: Path = None,
    cookies_from_browser: str = "",
    cookies_file: str = "",
) -> str:
    """Download video using yt-dlp. 
    Saves to 'mentahan/' folder inside project (easy to find + auto cleanup).
    Returns path to downloaded file.
    """
    import yt_dlp
    from pathlib import Path as P
    from yt_dlp.utils import DownloadError
    
    # Use fixed 'mentahan' folder if no output_path given
    if output_path is None:
        base_dir = P(__file__).parent.parent
        output_path = base_dir / "mentahan"
        output_path.mkdir(exist_ok=True)
    
    base_opts = {
        "outtmpl": str(output_path / "%(title).60s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "merge_output_format": "mp4",
        "windowsfilenames": True,
        "postprocessors": [{"key": "FFmpegVideoConvertor", "preferedformat": "mp4"}],
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "web", "ios"],
            }
        },
    }
    if cookies_file:
        base_opts["cookiefile"] = cookies_file
    elif cookies_from_browser:
        base_opts["cookiesfrombrowser"] = (cookies_from_browser,)
    elif config.YTDLP_COOKIES:
        env_cookie_path = Path(config.YTDLP_COOKIES)
        if not env_cookie_path.is_absolute():
            env_cookie_path = config.BASE_DIR / env_cookie_path
        if env_cookie_path.exists():
            base_opts["cookiefile"] = str(env_cookie_path)
            logger.info(f"Using yt-dlp cookies from .env: {env_cookie_path}")
        else:
            logger.warning(f"YTDLP_COOKIES is set but file was not found: {env_cookie_path}")

    format_attempts = [
        (
            "1080p+",
            "bv*[height>=1080][ext=mp4]+ba[ext=m4a]/bv*[height>=1080]+ba/"
            "b[height>=1080][ext=mp4]/b[height>=1080]",
        ),
        (
            "720p+",
            "bv*[height>=720][ext=mp4]+ba[ext=m4a]/bv*[height>=720]+ba/"
            "b[height>=720][ext=mp4]/b[height>=720]",
        ),
        (
            "best available",
            "bv*[ext=mp4]+ba[ext=m4a]/bv*+ba/b[ext=mp4]/b/best",
        ),
    ]
    last_error = None
    
    for format_label, format_selector in format_attempts:
        ydl_opts = dict(base_opts)
        ydl_opts["format"] = format_selector
        try:
            logger.info(f"Downloading video with yt-dlp target={format_label}, format={format_selector}")
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                filename = ydl.prepare_filename(info)
                if not filename.endswith(".mp4"):
                    filename = filename.rsplit(".", 1)[0] + ".mp4"
                video_info = get_video_info(filename)
                height = video_info.get("height", 0)
                if height and height < 720:
                    logger.warning(
                        f"Downloaded best available video is only {height}p. "
                        "Continuing because 720p/1080p was not available."
                    )
                else:
                    logger.info(f"Downloaded video resolution: {video_info.get('width')}x{height}")
                return filename
        except DownloadError as exc:
            last_error = exc
            message = str(exc)
            if "Could not copy Chrome cookie database" in message:
                raise RuntimeError(
                    "Gagal mengambil cookies dari Chrome. Tutup semua jendela Chrome lalu coba lagi, "
                    "atau export cookies YouTube ke cookies.txt dan upload lewat opsi cookies.txt."
                ) from exc
            if "Requested format is not available" in message:
                logger.warning(f"yt-dlp format failed ({format_label}), trying next format.")
                continue
            raise

    pytubefix_result = _download_video_with_pytubefix(url, output_path)
    if pytubefix_result:
        return pytubefix_result

    raise RuntimeError(
        "yt-dlp gagal mengunduh video ini bahkan dengan format terbaik yang tersedia. "
        "Coba update yt-dlp (`python -m pip install -U yt-dlp`), install fallback pytubefix "
        "(`python -m pip install -U pytubefix`), pakai cookies.txt yang valid, "
        "atau download manual lewat cobalt.tools lalu upload file video ke aplikasi."
    ) from last_error

def _download_video_with_pytubefix(url: str, output_path: Path) -> Optional[str]:
    """Fallback downloader for YouTube when yt-dlp cannot resolve formats."""
    try:
        from pytubefix import YouTube
    except ImportError:
        logger.warning("pytubefix is not installed. Skipping pytubefix fallback.")
        return None

    try:
        logger.info("yt-dlp failed. Trying pytubefix fallback...")
        yt = YouTube(url, use_oauth=False, allow_oauth_cache=True)
        stream = (
            yt.streams
            .filter(progressive=True, file_extension="mp4")
            .order_by("resolution")
            .desc()
            .first()
        )
        if stream is None:
            stream = yt.streams.get_highest_resolution()
        if stream is None:
            logger.warning("pytubefix found no downloadable video stream.")
            return None
        filename = stream.download(output_path=str(output_path))
        video_info = get_video_info(filename)
        logger.info(
            f"pytubefix downloaded video resolution: "
            f"{video_info.get('width')}x{video_info.get('height')}"
        )
        return filename
    except Exception as exc:
        logger.warning(f"pytubefix fallback failed: {str(exc)[:200]}")
        return None

def extract_audio(video_path: str, audio_path: str):
    """Extract audio from video"""
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        audio_path
    ]
    subprocess.run(cmd, check=True, capture_output=True)

def create_thumbnail(video_path: str, timestamp: float, output_path: str, hook_text: str = "", score: int = 0, size=(320, 180)):
    """Create thumbnail from video at specific timestamp + add hook text + score badge"""
    # Base thumbnail
    cmd = [
        "ffmpeg", "-y", "-ss", str(timestamp), "-i", video_path,
        "-vframes", "1", "-vf", f"scale={size[0]}:{size[1]}",
        "-q:v", "2", output_path
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    
    # Add text overlay if hook provided (using ImageMagick or ffmpeg drawtext on existing image)
    if hook_text or score:
        try:
            from PIL import Image, ImageDraw, ImageFont
            img = Image.open(output_path)
            draw = ImageDraw.Draw(img)
            
            # Add semi-transparent bar at bottom
            draw.rectangle([(0, 140), (320, 180)], fill=(0, 0, 0, 180))
            
            # Add score badge
            if score:
                draw.text((10, 145), f"🔥 {score}", fill=(255, 215, 0))
            
            # Add short hook
            if hook_text:
                short_hook = hook_text[:35] + "..." if len(hook_text) > 35 else hook_text
                draw.text((10, 162), short_hook, fill=(255, 255, 255))
            
            img.save(output_path, quality=90)
        except:
            pass  # fallback to plain thumbnail

def smooth_face_trajectory(bboxes: List[Tuple[int, int, int, int]], window: int = 15) -> List[Tuple[int, int, int, int]]:
    """Smooth face bounding boxes over time for stable reframing"""
    if not bboxes:
        return bboxes
    
    smoothed = []
    for i in range(len(bboxes)):
        start = max(0, i - window // 2)
        end = min(len(bboxes), i + window // 2 + 1)
        window_boxes = [box for box in bboxes[start:end] if box is not None]
        if not window_boxes:
            smoothed.append(None)
            continue
        
        avg_x = int(np.mean([b[0] for b in window_boxes]))
        avg_y = int(np.mean([b[1] for b in window_boxes]))
        avg_w = int(np.mean([b[2] for b in window_boxes]))
        avg_h = int(np.mean([b[3] for b in window_boxes]))
        smoothed.append((avg_x, avg_y, avg_w, avg_h))
    return smoothed

def generate_ass_subtitles(segments: List[Dict], style: Dict, output_path: str, video_width: int, video_height: int):
    """Generate advanced ASS subtitle file with animations"""
    font_size = int(style.get("font_size", 48))
    outline = int(style.get("stroke_width", style.get("border_thickness", 3)))
    vertical_position = int(style.get("vertical_position", 95))
    alignment = int(style.get("alignment", 2))
    margin_v = max(10, min(video_height - 10, vertical_position))
    # ASS header
    ass_content = f"""[Script Info]
Title: Viral Auto Klip Captions
ScriptType: v4.00+
Collisions: Normal
PlayResX: {video_width}
PlayResY: {video_height}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{config.DEFAULT_FONT},{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,{outline},0,{alignment},10,10,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    
    for seg in segments:
        start = seg["start"]
        end = seg["end"]
        text = seg["text"].strip()
        
        # Simple word wrapping
        words = text.split()
        lines = []
        current_line = ""
        for word in words:
            if len(current_line.split()) < config.MAX_WORDS_PER_LINE:
                current_line += " " + word if current_line else word
            else:
                lines.append(current_line.strip())
                current_line = word
        if current_line:
            lines.append(current_line.strip())
        
        line_duration = max((end - start) / max(len(lines), 1), 1.2)
        for idx, line in enumerate(lines):
            # Add emoji if enabled
            if style.get("emoji", False) and any(kw in line.lower() for kw in ["lucu", "wow", "gila", "shocking", "rahasia"]):
                line = "🔥 " + line
            
            # Time format for ASS: 0:00:00.00
            def to_ass_time(t):
                h = int(t // 3600)
                m = int((t % 3600) // 60)
                s = t % 60
                return f"{h}:{m:02d}:{s:05.2f}"
            
            line_start = min(start + idx * line_duration, end - 0.2)
            line_end = min(line_start + line_duration, end)
            start_str = to_ass_time(line_start)
            end_str = to_ass_time(line_end)
            
            # Simple effect: {\an2} for bottom center, etc.
            effect = ""
            if style.get("animation") == "pop":
                effect = r"{\fad(150,150)}"  # fade in/out
            
            if style.get("highlight_keywords", True):
                line = _highlight_caption_keywords(line)
            ass_content += f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{effect}{line}\n"
    
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(ass_content)
    
    logger.info(f"ASS subtitles generated: {output_path}")


def _highlight_caption_keywords(line: str) -> str:
    keywords = [
        "tidak", "nggak", "gak", "belum", "jangan", "harus", "kenapa", "rahasia",
        "tumbuh", "dewasa", "model", "betul", "bener", "ada", "karena", "ternyata",
        "tips", "cara", "salah", "penting", "viral", "fyp", "uang", "sukses",
    ]
    words = line.split()
    if not words:
        return line

    scored = []
    for index, word in enumerate(words):
        clean = re.sub(r"[^A-Za-zÀ-ÖØ-öø-ÿ0-9]", "", word).lower()
        score = 0
        if clean in keywords:
            score += 3
        if len(clean) >= 7:
            score += 1
        if word.isupper() and len(clean) > 2:
            score += 1
        if score:
            scored.append((score, index))

    if not scored:
        # Highlight the most meaningful word in short captions, Klipers-style.
        longest = max(range(len(words)), key=lambda idx: len(re.sub(r"[^A-Za-zÀ-ÖØ-öø-ÿ0-9]", "", words[idx])))
        scored = [(1, longest)]

    highlight_indexes = {index for _, index in sorted(scored, reverse=True)[:2]}
    highlighted = []
    for index, word in enumerate(words):
        if index in highlight_indexes:
            highlighted.append(r"{\c&H00D7FF&}" + word + r"{\c&HFFFFFF&}")
        else:
            highlighted.append(word)
    return " ".join(highlighted)
