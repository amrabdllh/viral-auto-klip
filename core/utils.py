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

def _safe_print(msg) -> None:
    """Cetak log tanpa pernah crash: konsol/pipa Windows (cp1252) tidak bisa memuat emoji."""
    try:
        print(msg, end="")
    except UnicodeEncodeError:
        enc = getattr(__import__("sys").stdout, "encoding", None) or "ascii"
        print(str(msg).encode(enc, "replace").decode(enc, "replace"), end="")


def setup_logging():
    logger.remove()
    logger.add(
        sink=_safe_print,
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
    
    # FIX v4: dulu dipaksa player_client android,web,ios -> YouTube hanya memberi format 18 (360p),
    # hasil klip 9:16 jadi sangat buram (crop ~200px di-upscale 5x). Default yt-dlp memberi hingga 4K.
    # FFmpegVideoConvertor juga dihapus (meng-encode ulang video = turun kualitas); merge ke mp4 cukup.
    base_opts = {
        "outtmpl": str(output_path / "%(title).60s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "merge_output_format": "mp4",
        "windowsfilenames": True,
        # pilih resolusi terbaik s.d. 1080p (cukup untuk 9:16 1080x1920), utamakan h264 + m4a
        "format_sort": ["res:1080", "fps:60", "vcodec:h264", "acodec:m4a"],
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

    # Percobaan client YouTube: default yt-dlp dulu (paling lengkap), lalu alternatif bila
    # hasilnya < 720p atau gagal. Hasil terbaik yang didapat tetap dipakai.
    client_attempts = [
        ("default", None),
        ("tv+web_safari", ["tv", "web_safari"]),
        ("mweb", ["mweb"]),
    ]
    last_error = None
    best_file, best_height = None, 0

    for label, clients in client_attempts:
        ydl_opts = dict(base_opts)
        ydl_opts["format"] = "bv*+ba/b"
        if clients:
            ydl_opts["extractor_args"] = {"youtube": {"player_client": clients}}
        try:
            logger.info(f"Downloading video with yt-dlp (client={label}, max 1080p)")
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                filename = ydl.prepare_filename(info)
                if not Path(filename).exists():
                    filename = filename.rsplit(".", 1)[0] + ".mp4"
            height = int(get_video_info(filename).get("height") or 0)
            logger.info(f"Downloaded video resolution: {height}p (client={label})")
            if height > best_height:
                if best_file and best_file != filename:
                    Path(best_file).unlink(missing_ok=True)
                best_file, best_height = filename, height
            if best_height >= 720 or "youtu" not in url.lower():
                break
            logger.warning(f"Hanya {height}p dengan client={label}; mencoba client YouTube lain untuk kualitas lebih baik...")
        except DownloadError as exc:
            last_error = exc
            message = str(exc)
            if "Could not copy Chrome cookie database" in message:
                raise RuntimeError(
                    "Gagal mengambil cookies dari Chrome. Tutup semua jendela Chrome lalu coba lagi, "
                    "atau export cookies YouTube ke cookies.txt dan upload lewat opsi cookies.txt."
                ) from exc
            logger.warning(f"yt-dlp gagal dengan client={label}: {message[:160]}")
            continue

    if best_file:
        if best_height and best_height < 720:
            logger.warning(
                f"⚠️ Video sumber hanya {best_height}p — hasil 9:16 akan buram. "
                "Coba update yt-dlp (python -m pip install -U yt-dlp) atau pakai cookies.txt."
            )
        return best_file

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

_FONT_CANDIDATES = [
    "arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/segoeuib.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]


_HEAVY_FONTS = [str(Path(config.FONT_DIR) / "Montserrat-Black.ttf"), str(Path(config.FONT_DIR) / "Montserrat-ExtraBold.ttf"),
                "C:/Windows/Fonts/ariblk.ttf", "C:/Windows/Fonts/impact.ttf"]


def _load_font(size: int, heavy: bool = False):
    custom = Path(str(config.DEFAULT_FONT))
    candidates = (_HEAVY_FONTS if heavy else []) + ([str(custom)] if custom.suffix.lower() in (".ttf", ".otf") else []) + _FONT_CANDIDATES
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap_text(draw, text: str, font, max_width: int, max_lines: int = 3) -> List[str]:
    lines, current = [], ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if draw.textlength(trial, font=font) <= max_width or not current:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(".,") + "…"
    return lines


_THUMB_KEYWORDS = {"rahasia", "ternyata", "jangan", "stop", "gila", "fakta", "kesalahan", "cara", "tips", "uang",
                   "sukses", "secret", "never", "truth", "why", "kenapa", "wajib", "penting", "viral", "plot", "twist"}


def _key_word_index(words: List[str]) -> int:
    """Kata yang di-highlight kuning di thumbnail: kata kunci / angka / kata terpanjang."""
    best, best_score = 0, -1
    for i, word in enumerate(words):
        clean = re.sub(r"[^\w]", "", word.lower())
        score = (5 if clean in _THUMB_KEYWORDS else 0) + (4 if re.search(r"\d", clean) else 0) + min(len(clean), 10) / 10
        if score > best_score:
            best, best_score = i, score
    return best


def create_thumbnail(video_path: str, timestamp: float, output_path: str, hook_text: str = "", score: int = 0,
                     width: int = 720, face_box=None):
    """Cover 9:16 dari klip: frame terpilih + teks hook besar dengan 1 kata kunci di-highlight.

    v4: teks diletakkan menjauhi wajah (face_box relatif 0-1 dari editing.pick_best_frames),
    warna/kontras sedikit dinaikkan, badge skor internal tidak lagi dicetak di cover publik.
    FIX lama: dulu dipaksa 320x180 (vertikal gepeng), emoji tampil kotak, overlay RGBA salah.
    """
    from PIL import ImageEnhance

    cmd = [
        "ffmpeg", "-y", "-ss", f"{max(timestamp, 0):.2f}", "-i", video_path,
        "-vframes", "1", "-vf", f"scale={int(width)}:-2",
        "-q:v", "2", output_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    try:
        base = Image.open(output_path).convert("RGB")
        base = ImageEnhance.Contrast(ImageEnhance.Color(base).enhance(1.15)).enhance(1.07).convert("RGBA")
        w, h = base.size
        overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        hook = re.sub(r"[\U0001F000-\U0001FAFF☀-➿️]", "", hook_text or "").strip()
        if hook:
            # (v6.8) teks pendek = huruf raksasa & tebal (gaya thumbnail kreator); teks panjang tetap muat 4 baris
            n_words = len(hook.split())
            scale = 0.15 if n_words <= 3 else 0.125 if n_words <= 5 else 0.085
            font = _load_font(max(int(w * scale), 18), heavy=n_words <= 6)
            lines = _wrap_text(draw, hook.upper(), font, int(w * 0.9), max_lines=3 if n_words <= 6 else 4)
            def _overflow(ls):  # kata terpotong "…" atau satu kata panjang melebihi lebar gambar
                return len(" ".join(ls).replace("…", "").split()) < n_words or "…" in ls[-1] or \
                    any(draw.textlength(ln, font=font) > w * 0.94 for ln in ls)
            while _overflow(lines) and scale > 0.06:  # tidak muat → kecilkan
                scale -= 0.015
                font = _load_font(max(int(w * scale), 18), heavy=True)
                lines = _wrap_text(draw, hook.upper(), font, int(w * 0.9), max_lines=4)
            line_h = int(font.size * 1.16) if hasattr(font, "size") else 22
            block_h = line_h * len(lines)
            # Wajah di setengah atas -> teks di bawah (di atas zona caption/UI); selain itu teks di atas.
            face_cy = (face_box[1] + face_box[3] / 2) if face_box else 0.4
            y = int(h * 0.58) if face_cy < 0.5 else int(h * 0.12)
            # bayangan gradasi lembut di belakang blok teks agar terbaca di latar apa pun
            for i in range(block_h + 80):
                yy = y - 40 + i
                if 0 <= yy < h:
                    alpha = int(150 * (1 - abs(i - (block_h + 80) / 2) / ((block_h + 80) / 2)))
                    draw.line([(0, yy), (w, yy)], fill=(0, 0, 0, max(alpha, 0)))
            all_words = " ".join(lines).split()
            key_idx = _key_word_index(all_words)
            stroke = max(3, int(w * 0.008))
            word_counter = 0
            for line in lines:
                words = line.split()
                space = draw.textlength(" ", font=font)
                total = sum(draw.textlength(word, font=font) for word in words) + space * (len(words) - 1)
                x = (w - total) / 2
                for word in words:
                    color = (255, 225, 0, 255) if word_counter == key_idx else (255, 255, 255, 255)
                    draw.text((x, y), word, font=font, fill=color, stroke_width=stroke, stroke_fill=(0, 0, 0, 255))
                    x += draw.textlength(word, font=font) + space
                    word_counter += 1
                y += line_h
        Image.alpha_composite(base, overlay).convert("RGB").save(output_path, quality=93)
    except Exception as exc:
        logger.warning(f"Overlay thumbnail gagal (thumbnail polos dipakai): {exc}")

def smooth_face_trajectory(bboxes: List[Tuple[int, int, int, int]], window: int = 15) -> List[Tuple[int, int, int, int]]:
    """Smooth face bounding boxes over time for stable reframing"""
    if not bboxes:
        return bboxes
    
    # FIX v4: dulu rata-rata bergerak MELINTASI pergantian orang (kiri <-> kanan) sehingga
    # crop jatuh di tengah keduanya = dinding kosong. Sekarang urutan dipecah per "segmen"
    # setiap kali pusat wajah lompat jauh, dan smoothing hanya di dalam segmen yang sama.
    segment_ids, seg, prev = [], 0, None
    for box in bboxes:
        if box is not None and prev is not None:
            jump = np.hypot((box[0] + box[2] / 2) - (prev[0] + prev[2] / 2), (box[1] + box[3] / 2) - (prev[1] + prev[3] / 2))
            if jump > 1.2 * max(box[2], prev[2]):
                seg += 1
        if box is not None:
            prev = box
        segment_ids.append(seg)

    smoothed = []
    for i in range(len(bboxes)):
        start = max(0, i - window // 2)
        end = min(len(bboxes), i + window // 2 + 1)
        window_boxes = [box for j, box in enumerate(bboxes[start:end], start)
                        if box is not None and segment_ids[j] == segment_ids[i]]
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
    # FIX: warna style (neon cyan, karaoke gold, gradient pink outline, dst.) dulu diabaikan —
    # semua style tampil putih/hitam. Sekarang color & stroke_color dari config dipakai.
    primary_colour = _ass_colour(style.get("color", (255, 255, 255)))
    outline_colour = _ass_colour(style.get("stroke_color", (0, 0, 0)))
    bold_flag = -1 if style.get("bold", True) else 0
    # Bila DEFAULT_FONT berupa path .ttf, ASS butuh NAMA font (libass mencari di sistem/fontsdir)
    font_name = Path(str(config.DEFAULT_FONT)).stem if str(config.DEFAULT_FONT).lower().endswith((".ttf", ".otf")) else config.DEFAULT_FONT
    # ASS header
    ass_content = f"""[Script Info]
Title: Viral Auto Klip Captions
ScriptType: v4.00+
Collisions: Normal
PlayResX: {video_width}
PlayResY: {video_height}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{font_size},{primary_colour},&H00FFFFFF,{outline_colour},&H00000000,{bold_flag},0,0,0,100,100,0,0,1,{outline},0,{alignment},10,10,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    def to_ass_time(t):
        t = max(float(t), 0.0)
        h = int(t // 3600)
        m = int((t % 3600) // 60)
        s = t % 60
        return f"{h}:{m:02d}:{s:05.2f}"

    karaoke = style.get("animation") == "karaoke"
    active = style.get("animation") == "active"
    upper = bool(style.get("uppercase"))
    active_colour = _ass_colour(style.get("active_color", (255, 230, 0))).replace("&H00", "&H")
    # v6.4 caption_fx: baris masuk dengan pop, kata 'berat' berwarna aksen permanen, bayangan lembut
    fx = bool(style.get("fx"))
    power_words = {str(w).lower() for w in (style.get("power_words") or [])}
    power_colour = _ass_colour(style.get("power_color", (64, 255, 140))).replace("&H00", "&H")
    base_colour = primary_colour.replace("&H00", "&H")
    if fx:
        ass_content = ass_content.replace(
            f"{outline_colour},&H00000000,{bold_flag},0,0,0,100,100,0,0,1,{outline},0,",
            f"{outline_colour},&H96000000,{bold_flag},0,0,0,100,100,0,0,1,{outline},3,")

    def _is_power(token_raw: str) -> bool:
        return bool(power_words) and re.sub(r"[^\w]", "", token_raw.lower(), flags=re.UNICODE) in power_words
    for seg in segments:
        start = seg["start"]
        end = seg["end"]
        text = _ass_escape(seg["text"].strip())
        if upper:
            text = text.upper()

        # NEW v4: active-word — tiap kata yang sedang diucapkan menyala & 'pop' (gaya OpusClip/Submagic).
        # Satu event per kata; kata aktif diberi warna + animasi skala singkat, sisanya warna utama.
        if active and seg.get("words"):
            seg_words = [w for w in seg["words"] if str(w.get("word", "")).strip()]
            tokens = [_ass_escape(str(w["word"]).strip()) for w in seg_words]
            if upper:
                tokens = [t.upper() for t in tokens]
            for i, w in enumerate(seg_words):
                w_start = float(start) if i == 0 else float(w.get("start", start))
                # tahan kata sampai kata berikutnya, tapi jangan lebih dari 0.6 dtk setelah selesai diucapkan
                # (dulu jeda panjang membuat kata 'nyangkut' di layar)
                own_end = float(w.get("end", end))
                nxt = float(seg_words[i + 1].get("start", end)) if i + 1 < len(seg_words) else float(end)
                w_end = min(nxt, own_end + 0.6) if i + 1 < len(seg_words) else min(max(float(end), own_end), own_end + 0.6)
                if w_end - w_start < 0.04:
                    continue
                pieces = []
                # baris baru 'masuk' (skala 86% → 100% dalam 0.11 dtk) hanya di event kata pertama
                enter = "\\fscx86\\fscy86\\t(0,110,\\fscx100\\fscy100)" if fx and i == 0 else "\\fscx100\\fscy100"
                for j, token in enumerate(tokens):
                    raw = str(seg_words[j].get("word", ""))
                    if j == i:
                        grow = "\\fscx92\\fscy92\\t(0,70,\\fscx120\\fscy120)\\t(70,160,\\fscx112\\fscy112)" if fx else \
                            "\\fscx100\\fscy100\\t(0,90,\\fscx116\\fscy116)\\t(90,170,\\fscx110\\fscy110)"
                        pieces.append("{\\c" + active_colour + "&" + grow + "}" + token)
                    elif fx and _is_power(raw):
                        pieces.append("{\\c" + power_colour + "&" + enter + "}" + token)
                    else:
                        pieces.append("{\\c" + base_colour + "&" + enter + "}" + token if fx else token)
                    if j == i and not fx:
                        pieces[-1] += "{\\r}"
                line = " ".join(pieces)
                if style.get("emoji", False) and i == 0:
                    line = f"{_pick_caption_emoji(text)} {line}"
                ass_content += f"Dialogue: 0,{to_ass_time(w_start)},{to_ass_time(w_end)},Default,,0,0,0,,{line}\n"
            continue

        # NEW v4: karaoke sungguhan — kata menyala satu per satu sesuai timing Whisper (\k).
        # SecondaryColour (putih) = belum diucapkan, PrimaryColour (warna style) = sudah.
        if karaoke and seg.get("words"):
            parts, cursor = [], float(start)
            for word in seg["words"]:
                w_end = max(float(word.get("end", cursor)), cursor)
                cs = max(1, int(round((w_end - cursor) * 100)))
                token = _ass_escape(str(word.get('word', '')).strip())
                parts.append(f"{{\\k{cs}}}{token.upper() if upper else token}")
                cursor = w_end
            line = " ".join(parts)
            if style.get("emoji", False):
                line = f"{_pick_caption_emoji(text)} {line}"
            ass_content += f"Dialogue: 0,{to_ass_time(start)},{to_ass_time(max(end, cursor))},Default,,0,0,0,,{line}\n"
            continue

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
            # Add emoji if enabled (rich keyword->emoji mapping dari config)
            if style.get("emoji", False):
                line = f"{_pick_caption_emoji(line)} {line}"

            line_start = min(start + idx * line_duration, end - 0.2)
            line_end = min(line_start + line_duration, end)
            start_str = to_ass_time(line_start)
            end_str = to_ass_time(line_end)
            
            # Simple effect: {\an2} for bottom center, etc.
            effect = ""
            if style.get("animation") == "pop":
                effect = r"{\fad(150,150)}"  # fade in/out
            
            if style.get("highlight_keywords", True):
                line = _highlight_caption_keywords(line, reset_colour=primary_colour)
            ass_content += f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{effect}{line}\n"
    
    # v6.4: posisi caption per rentang waktu (mis. di sambungan saat layar dibagi dua orang)
    zones = style.get("margin_zones") or []
    if zones:
        def _secs(stamp: str) -> float:
            h, m, s = stamp.split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
        lines = []
        for line in ass_content.split("\n"):
            if line.startswith("Dialogue:"):
                parts = line.split(",", 9)
                t0 = _secs(parts[1])
                mv = next((int(z[2]) for z in zones if z[0] - 0.05 <= t0 < z[1]), None)
                if mv is not None:
                    parts[7] = str(mv)
                    line = ",".join(parts)
            lines.append(line)
        ass_content = "\n".join(lines)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(ass_content)

    logger.info(f"ASS subtitles generated: {output_path}")


def _ass_escape(text: str) -> str:
    """Cegah teks transkrip merusak tag ASS ({ } \\ dan baris baru)."""
    return str(text).replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")


def _pick_caption_emoji(line: str) -> str:
    """Pilih emoji berdasarkan kata kunci di baris caption (word-boundary; fallback 🔥)."""
    lowered = line.lower()
    for emoji, keywords in config.EMOJI_KEYWORDS.items():
        if any(re.search(r"(?<![a-z0-9])" + re.escape(k) + r"(?![a-z0-9])", lowered) for k in keywords):
            return emoji
    return "🔥"


def _ass_colour(rgb) -> str:
    """Konversi (R, G, B) ke format warna ASS &H00BBGGRR."""
    try:
        r, g, b = (max(0, min(255, int(c))) for c in rgb[:3])
    except Exception:
        r, g, b = 255, 255, 255
    return f"&H00{b:02X}{g:02X}{r:02X}"


def _highlight_caption_keywords(line: str, reset_colour: str = "&H00FFFFFF") -> str:
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
            # reset ke warna utama style (dulu selalu putih walau style berwarna)
            highlighted.append(r"{\c&H00D7FF&}" + word + "{\\c" + reset_colour.replace("&H00", "&H") + "&}")
        else:
            highlighted.append(word)
    return " ".join(highlighted)
