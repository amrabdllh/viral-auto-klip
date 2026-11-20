"""
Toolkit FFmpeg untuk tool video (merge, loop, WA Story, overlay/Konten Tempel, logo Brand Kit,
Split-Cam, gambar→video Ken Burns, energi audio untuk Highlight Bola, dll).
"""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from loguru import logger

import config

_LOCAL_BIN = config.BASE_DIR / "ffmpeg" / "bin"
_BUNDLED_BIN = config.BASE_DIR / "tools" / "ffmpeg"
# Pakai ffmpeg bawaan repo bila ada, supaya tidak bergantung pada PATH sistem.
for _candidate in (_LOCAL_BIN, _BUNDLED_BIN):
    if shutil.which("ffmpeg") is None and (_candidate / "ffmpeg.exe").exists():
        os.environ["PATH"] = str(_candidate) + os.pathsep + os.environ.get("PATH", "")

W, H = 1080, 1920


class FFmpegError(RuntimeError):
    pass


def run(args: Sequence[str], timeout: int = 3600, cwd: Optional[str] = None) -> subprocess.CompletedProcess:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *map(str, args)]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, cwd=cwd)
    if proc.returncode != 0:
        raise FFmpegError((proc.stderr or "ffmpeg gagal")[-1500:])
    return proc


def available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def probe(path: str) -> Dict:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        raise FFmpegError(f"Tidak bisa membaca media: {Path(path).name}")
    data = json.loads(proc.stdout or "{}")
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    audio = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    fps = 30.0
    rate = video.get("avg_frame_rate") or video.get("r_frame_rate") or "30/1"
    try:
        num, den = rate.split("/")
        fps = float(num) / float(den) if float(den) else 30.0
    except (ValueError, ZeroDivisionError):
        pass
    return {
        "duration": float(data.get("format", {}).get("duration") or video.get("duration") or 0),
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "fps": round(fps, 3) if fps > 0 else 30.0,
        "has_audio": audio is not None,
        "size": int(data.get("format", {}).get("size") or 0),
    }


def display_size(path: str) -> Tuple[int, int]:
    """(w, h) setelah rotasi diterapkan — dipakai untuk menata crop & caption.

    probe() melaporkan ukuran coded. Video ponsel bertag rotation=90 di-decode
    transpos, jadi crop yang dihitung dari ukuran coded akan salah.
    """
    info = probe(path)
    width, height = info["width"], info["height"]
    rotation = None
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream_side_data=rotation:stream_tags=rotate",
             "-of", "json", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode == 0:
            stream = (json.loads(proc.stdout or "{}").get("streams") or [{}])[0]
            for side in stream.get("side_data_list") or []:
                if "rotation" in side:
                    rotation = float(side["rotation"])
                    break
            if rotation is None:
                tag = (stream.get("tags") or {}).get("rotate")
                if tag is not None:
                    rotation = float(tag)
    except (ValueError, KeyError, IndexError, TypeError):
        pass
    if rotation is not None and abs(round(rotation)) % 180 == 90:
        return height, width
    return width, height


def venc(crf: int = 20) -> List[str]:
    from .clipper import _video_encode_args
    return _video_encode_args(crf=crf)


AENC = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]


def escape_path(path: str) -> str:
    return Path(path).resolve().as_posix().replace(":", r"\:").replace("'", r"\'")


def font_file() -> str:
    for p in [*Path(config.FONT_DIR).glob("*.ttf"), Path("C:/Windows/Fonts/arialbd.ttf"),
              Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")]:
        if p.exists():
            return str(p)
    return ""


def write_text_file(path: Path, text: str) -> str:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return str(path)


def size_for(aspect: str) -> Tuple[int, int]:
    return {"9:16": (1080, 1920), "1:1": (1080, 1080), "4:5": (1080, 1350),
            "16:9": (1920, 1080), "4:3": (1440, 1080)}.get(aspect, (1080, 1920))


def auto_crop(src: str) -> str:
    """Awalan filter `crop=...,` bila sumber berbingkai (kartu video + border hitam/branding), kalau tidak kosong."""
    try:
        from .framing import detect_content_rect
        r = detect_content_rect(src)
    except Exception:
        r = None
    return f"crop={r[2]}:{r[3]}:{r[0]}:{r[1]}," if r else ""


def fit_filter(w: int, h: int, mode: str = "cover") -> str:
    """cover = isi penuh (crop), pad = letterbox hitam, blur = latar blur."""
    if mode == "pad":
        return f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1"
    if mode == "blur":
        return (f"split[a][b];[a]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},boxblur=28:6[bg];"
                f"[b]scale={w}:{h}:force_original_aspect_ratio=decrease[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1")
    return f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1"


# ----------------------------------------------------------------------------- basic ops

def trim(src: str, out: str, start: float, end: float, reencode: bool = True) -> str:
    dur = max(0.1, end - start)
    if reencode:
        run(["-ss", f"{start:.3f}", "-i", src, "-t", f"{dur:.3f}", *venc(18), *AENC, "-movflags", "+faststart", out])
    else:
        run(["-ss", f"{start:.3f}", "-i", src, "-t", f"{dur:.3f}", "-c", "copy", out])
    return out


def normalize(src: str, out: str, w: int, h: int, fps: int = 30, mode: str = "cover") -> str:
    """Samakan resolusi/fps/audio (wajib sebelum concat/xfade). Tambah audio senyap bila tak ada."""
    info = probe(src)
    args = ["-i", src]
    if not info["has_audio"]:
        args += ["-f", "lavfi", "-t", f"{max(info['duration'], 0.1):.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
    vf = f"{auto_crop(src)}{fit_filter(w, h, mode)},fps={fps},format=yuv420p"
    args += ["-filter_complex", f"[0:v]{vf}[v]", "-map", "[v]", "-map", "0:a:0" if info["has_audio"] else "1:a:0",
             *venc(18), *AENC, "-shortest", "-movflags", "+faststart", out]
    run(args)
    return out


TRANSITIONS = ["none", "fade", "fadeblack", "fadewhite", "wipeleft", "wiperight", "wipeup", "wipedown",
               "slideleft", "slideright", "slideup", "slidedown", "smoothleft", "smoothright",
               "circleopen", "circleclose", "dissolve", "pixelize", "radial", "zoomin"]


def concat(files: Sequence[str], out: str, aspect: str = "9:16", transition: str = "none",
           tdur: float = 0.5, mode: str = "cover", workdir: Optional[Path] = None) -> str:
    if not files:
        raise ValueError("Tidak ada video untuk digabung")
    w, h = size_for(aspect)
    workdir = Path(workdir or Path(out).parent)
    norm = []
    for i, f in enumerate(files):
        p = workdir / f"_norm_{i:03d}.mp4"
        normalize(f, str(p), w, h, mode=mode)
        norm.append(str(p))
    try:
        if transition == "none" or len(norm) == 1:
            lst = workdir / "_concat.txt"
            lst.write_text("".join(f"file '{Path(p).resolve().as_posix()}'\n" for p in norm), encoding="utf-8")
            run(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", "-movflags", "+faststart", out])
            lst.unlink(missing_ok=True)
            return out
        durs = [probe(p)["duration"] for p in norm]
        tdur = max(0.1, min(tdur, min(durs) / 2.5))
        args: List[str] = []
        for p in norm:
            args += ["-i", p]
        vparts, aparts = [], []
        offset = 0.0
        last_v, last_a = "[0:v]", "[0:a]"
        for i in range(1, len(norm)):
            offset += durs[i - 1] - tdur
            v_out, a_out = f"[v{i}]", f"[a{i}]"
            vparts.append(f"{last_v}[{i}:v]xfade=transition={transition}:duration={tdur:.2f}:offset={offset:.3f}{v_out}")
            aparts.append(f"{last_a}[{i}:a]acrossfade=d={tdur:.2f}{a_out}")
            last_v, last_a = v_out, a_out
        run([*args, "-filter_complex", ";".join(vparts + aparts), "-map", last_v, "-map", last_a,
             *venc(18), *AENC, "-movflags", "+faststart", out])
        return out
    finally:
        for p in norm:
            Path(p).unlink(missing_ok=True)


def loop(src: str, out: str, times: int = 0, target_sec: float = 0, crossfade: float = 0.0) -> str:
    info = probe(src)
    d = max(info["duration"], 0.1)
    if target_sec and target_sec > 0:
        times = max(1, math.ceil(target_sec / d))
    times = max(1, min(int(times or 2), 500))
    if crossfade > 0 and d > crossfade * 3:
        # seamless: ujung klip di-crossfade ke awalnya sendiri sebelum diulang
        seamless = Path(out).with_name("_seamless.mp4")
        cf = crossfade
        fc = (f"[0:v]split[a][b];[a]trim=0:{d - cf:.3f},setpts=PTS-STARTPTS[main];"
              f"[b]trim={d - cf:.3f}:{d:.3f},setpts=PTS-STARTPTS[tail];"
              f"[0:v]trim=0:{cf:.3f},setpts=PTS-STARTPTS[head];"
              f"[tail][head]xfade=transition=fade:duration={cf:.3f}:offset=0[blend];"
              f"[blend][main]concat=n=2:v=1:a=0[v]")
        amap = ["-map", "0:a?"] if info["has_audio"] else []
        run(["-i", src, "-filter_complex", fc, "-map", "[v]", *amap, "-t", f"{d:.3f}", *venc(18), *AENC, str(seamless)])
        src = str(seamless)
    args = ["-stream_loop", str(times - 1), "-i", src]
    if target_sec:
        args += ["-t", f"{target_sec:.3f}"]
    run([*args, *venc(18), *AENC, "-movflags", "+faststart", out])
    Path(out).with_name("_seamless.mp4").unlink(missing_ok=True)
    return out


def wa_story(src: str, out_dir: Path, segment_sec: int = 60, max_mb: float = 15.5, aspect: str = "9:16",
             mode: str = "blur") -> List[str]:
    """Potong & kompres untuk Status WhatsApp: per segmen ≤ segment_sec, 720p, ukuran ≤ max_mb."""
    info = probe(src)
    total = info["duration"]
    w, h = (720, 1280) if aspect == "9:16" else (1280, 720)
    parts = max(1, math.ceil(total / segment_sec))
    outs = []
    for i in range(parts):
        start = i * segment_sec
        dur = min(segment_sec, total - start)
        if dur < 0.5:
            continue
        kbps_total = int(max_mb * 8 * 1024 / max(dur, 1) * 0.92)
        vkbps = max(400, min(3500, kbps_total - 96))
        out = Path(out_dir) / f"wa_story_{i + 1:02d}.mp4"
        vf = auto_crop(src) + fit_filter(w, h, mode)
        run(["-ss", f"{start:.3f}", "-i", src, "-t", f"{dur:.3f}", "-filter_complex", f"[0:v]{vf},fps=30,format=yuv420p[v]",
             "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-preset", "medium", "-b:v", f"{vkbps}k",
             "-maxrate", f"{int(vkbps * 1.2)}k", "-bufsize", f"{vkbps * 2}k", "-profile:v", "main",
             "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(out)])
        outs.append(str(out))
    return outs


def overlay_items(src: str, out: str, items: List[Dict]) -> str:
    """Konten Tempel: tempel gambar/teks di atas video.

    item gambar: {type:'image', path, x, y, w (0-1 relatif lebar video), start, end, opacity}
    item teks:   {type:'text', text, x, y, size (0-1 relatif tinggi), color, box, start, end}
    x, y = posisi relatif 0..1 (pojok kiri atas elemen).
    """
    info = probe(src)
    vw, vh = info["width"], info["height"]
    args = ["-i", src]
    chains: List[str] = []
    last = "[0:v]"
    temp_files: List[str] = []
    img_index = 1
    font = font_file()
    for n, it in enumerate(items):
        start = float(it.get("start") or 0)
        end = float(it.get("end") or info["duration"])
        enable = f"enable='between(t,{start:.2f},{end:.2f})'"
        x = float(it.get("x", 0.05)); y = float(it.get("y", 0.05))
        if it.get("type") == "image" and it.get("path"):
            args += ["-loop", "1", "-i", it["path"]] if Path(it["path"]).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp") else ["-i", it["path"]]
            width = max(8, int(vw * float(it.get("w", 0.3))))
            op = max(0.05, min(1.0, float(it.get("opacity", 1.0))))
            chains.append(f"[{img_index}:v]scale={width}:-1,format=rgba,colorchannelmixer=aa={op:.2f}[img{n}]")
            out_label = f"[o{n}]"
            chains.append(f"{last}[img{n}]overlay=x={int(vw * x)}:y={int(vh * y)}:shortest=1:{enable}{out_label}")
            last = out_label
            img_index += 1
        elif it.get("type") == "text" and str(it.get("text", "")).strip():
            tf = write_text_file(Path(out).with_suffix(f".t{n}.txt"), str(it["text"]))
            temp_files.append(tf)
            size = max(10, int(vh * min(max(float(it.get("size", 0.045)), 0.01), 0.3)))  # relatif tinggi video; nilai di luar batas bikin FFmpeg gagal
            color = str(it.get("color", "white")).lstrip("#")
            color = f"0x{color}" if len(color) == 6 else color
            box = ":box=1:boxcolor=black@0.6:boxborderw=18" if it.get("box", True) else ""
            ff = f"fontfile='{escape_path(font)}':" if font else ""
            out_label = f"[o{n}]"
            chains.append(f"{last}drawtext={ff}textfile='{escape_path(tf)}':expansion=none:x={int(vw * x)}:y={int(vh * y)}:"
                          f"fontsize={size}:fontcolor={color}:borderw=3:bordercolor=black@0.7{box}:{enable}{out_label}")
            last = out_label
    if not chains:
        shutil.copy2(src, out)
        return out
    try:
        run([*args, "-filter_complex", ";".join(chains), "-map", last, "-map", "0:a?", *venc(18), "-c:a", "copy",
             "-t", f"{info['duration']:.3f}", "-movflags", "+faststart", out])
    finally:
        for f in temp_files:
            Path(f).unlink(missing_ok=True)
    return out


def apply_logo(src: str, out: str, logo: str, position: str = "top-left", size: float = 0.22,
               opacity: float = 0.9, margin: float = 0.04, custom_xy: Optional[Tuple[float, float]] = None) -> str:
    info = probe(src)
    vw, vh = info["width"], info["height"]
    lw = max(16, int(vw * max(0.05, min(0.6, size))))
    m = int(vw * margin)
    # 9 posisi. Baris bawah sengaja di atas ±13% tinggi bawah (tombol & deskripsi TikTok/Reels menutupi area itu)
    top, mid, bot = f"{m + int(vh * 0.03)}", "(H-h)/2", f"H-h-{int(vh * 0.13)}"
    left, cx, right = f"{m}", "(W-w)/2", f"W-w-{m}"
    pos = {"top-left": (left, top), "top-center": (cx, top), "top-right": (right, top),
           "middle-left": (left, mid), "center": (cx, mid), "middle-right": (right, mid),
           "bottom-left": (left, bot), "bottom-center": (cx, bot), "bottom-right": (right, bot)}
    if custom_xy:
        x, y = str(int(vw * custom_xy[0])), str(int(vh * custom_xy[1]))
    else:
        x, y = pos.get(position, pos["top-left"])
    fc = (f"[1:v]scale={lw}:-1,format=rgba,colorchannelmixer=aa={max(0.05, min(1, opacity)):.2f}[lg];"
          f"[0:v][lg]overlay=x={x}:y={y}[v]")
    run(["-i", src, "-loop", "1", "-i", logo, "-filter_complex", fc, "-map", "[v]", "-map", "0:a?",
         *venc(16), "-c:a", "copy", "-t", f"{info['duration']:.3f}", "-movflags", "+faststart", out])
    return out


OUTRO_IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp")
OUTRO_VIDEO_EXT = (".mp4", ".mov", ".webm", ".mkv", ".m4v")
OUTRO_MAX_SEC = 15.0


def append_outro(src: str, out: str, outro: str, image_sec: float = 3.0, fade: float = 0.4) -> str:
    """Sambungkan penutup (gambar atau video pendek milik pengguna) di akhir klip dengan transisi fade.

    Ukuran/rasio penutup bebas: diisi ke ukuran klip dengan latar blur (tanpa pita hitam). Gambar tampil
    `image_sec` detik tanpa suara (audio klip memudar keluar); video memakai suaranya sendiri bila ada,
    maksimal 15 dtk."""
    info = probe(src)
    w, h, fps, dur = info["width"], info["height"], info["fps"] or 30.0, info["duration"]
    is_img = Path(outro).suffix.lower() in OUTRO_IMAGE_EXT
    if is_img:
        o_dur, o_audio = max(0.5, min(OUTRO_MAX_SEC, float(image_sec))), False
        o_in = ["-loop", "1", "-framerate", f"{fps:.3f}", "-t", f"{o_dur:.3f}", "-i", outro]
    else:
        oi = probe(outro)
        o_dur, o_audio = max(0.3, min(OUTRO_MAX_SEC, oi["duration"])), oi["has_audio"]
        o_in = ["-t", f"{o_dur:.3f}", "-i", outro]
    fade = max(0.0, min(float(fade), dur / 3, o_dur / 3))
    inputs = ["-i", src, *o_in]
    n = 2
    a_main = "[0:a]" if info["has_audio"] else None
    if a_main is None:
        inputs += ["-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
        a_main, n = f"[{n}:a]", n + 1
    if o_audio:
        a_out = "[1:a]"
    else:
        inputs += ["-f", "lavfi", "-t", f"{o_dur:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
        a_out = f"[{n}:a]"
    norm_a = "aformat=sample_rates=48000:channel_layouts=stereo,aresample=async=1"
    fc = (f"[1:v]{fit_filter(w, h, 'blur')},fps={fps:.3f},format=yuv420p,settb=AVTB[o];"
          f"[0:v]fps={fps:.3f},format=yuv420p,setsar=1,settb=AVTB[m];"
          f"{a_main}{norm_a}[am];{a_out}{norm_a},atrim=0:{o_dur:.3f}[ao];")
    if fade > 0.05:
        fc += (f"[m][o]xfade=transition=fade:duration={fade:.3f}:offset={dur - fade:.3f}[v];"
               f"[am][ao]acrossfade=d={fade:.3f}[a]")
    else:
        fc += "[m][o]concat=n=2:v=1:a=0[v];[am][ao]concat=n=2:v=0:a=1[a]"
    run([*inputs, "-filter_complex", fc, "-map", "[v]", "-map", "[a]", *venc(16), *AENC,
         "-movflags", "+faststart", out])
    return out


def split_cam(src: str, out: str, cam: Tuple[float, float, float, float], game: Tuple[float, float, float, float],
              cam_ratio: float = 0.38, cam_top: bool = True, start: float = 0, end: float = 0,
              face: Optional[Tuple[float, float]] = None) -> str:
    """Split-Cam gaming: area facecam & gameplay (x,y,w,h relatif 0..1) ditumpuk vertikal 9:16.

    `face` = pusat wajah (x, y relatif 0..1 pada frame sumber). Bila wajah tidak
    tepat di tengah kotak (mis. webcam menempel tepi kanan layar), panel diberi
    padding hitam di sisi yang kosong supaya wajah tetap di tengah panel.
    """
    info = probe(src)
    vw, vh = info["width"], info["height"]
    cam_h = int(H * max(0.2, min(0.6, cam_ratio))) // 2 * 2
    game_h = H - cam_h

    def crop(box):
        x, y, w, h = box
        return (f"crop={max(2, int(vw * w)) // 2 * 2}:{max(2, int(vh * h)) // 2 * 2}:"
                f"{int(vw * x)}:{int(vh * y)}")

    # Panel atas: crop facecam; bila wajah berada di tepi, padding hitam di sisi
    # seberang agar wajah di tengah panel (Clipper mencapai ini lewat koordinat
    # yang sudah dikurasi; di sini kita hitung otomatis dari posisi wajah).
    cam_w = max(2, int(vw * cam[2])) // 2 * 2
    cam_h_px = max(2, int(vh * cam[3])) // 2 * 2
    panel_ratio = W / cam_h
    total_w = max(cam_w, int(round(cam_h_px * panel_ratio)) // 2 * 2)
    pad_left = pad_right = 0
    if face and total_w > cam_w:
        fx = float(face[0]) * vw - int(vw * cam[0])  # posisi wajah di dalam crop (px)
        pad_left = max(0, int(round(total_w / 2 - fx))) // 2 * 2
        pad_right = max(0, total_w - cam_w - pad_left)
        if pad_left == 0 and pad_right == 0:
            pad_right = total_w - cam_w
    if pad_left or pad_right:
        cam_chain = (f"{crop(cam)},pad={total_w}:{cam_h_px}:{pad_left}:0:color=black,"
                     f"scale={W}:{cam_h}:force_divisible_by=2:reset_sar=1:flags=lanczos")
    else:
        cam_chain = f"{crop(cam)},scale={W}:{cam_h}:force_original_aspect_ratio=increase,crop={W}:{cam_h}"

    top, bottom = ("c", "g") if cam_top else ("g", "c")
    # Panel gameplay: frame utuh (contain) di atas latar blur — sama seperti
    # renderer campaign Clipper. Dulu 'increase' (cover) memotong sisi kiri/kanan
    # sehingga menu/UI game dan area penting hilang.
    bg_w = max(2, W // 4)
    bg_h = max(2, (game_h // 8) * 2)
    fc = (f"[0:v]split[c0][g0];"
          f"[c0]{cam_chain}[c];"
          f"[g0]{crop(game)},split=2[gf][gb];"
          f"[gf]scale={W}:{game_h}:force_original_aspect_ratio=decrease:force_divisible_by=2:"
          f"reset_sar=1:flags=lanczos[gfit];"
          f"[gb]scale={bg_w}:{bg_h}:force_original_aspect_ratio=increase:force_divisible_by=2:"
          f"reset_sar=1,crop={bg_w}:{bg_h},gblur=sigma=10,scale={W}:{game_h}[gbg];"
          f"[gbg][gfit]overlay=x=(W-w)/2:y=0[g];"
          f"[{top}][{bottom}]vstack=inputs=2,setsar=1,format=yuv420p[v]")
    args = []
    if start:
        args += ["-ss", f"{start:.3f}"]
    args += ["-i", src]
    if end and end > start:
        args += ["-t", f"{end - start:.3f}"]
    run([*args, "-filter_complex", fc, "-map", "[v]", "-map", "0:a?", *venc(19), *AENC, "-movflags", "+faststart", out])
    return out


MOTIONS = ["zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down", "still"]


def image_to_clip(image: str, out: str, duration: float, w: int = W, h: int = H, motion: str = "zoom_in",
                  fps: int = 30) -> str:
    """Gambar diam → klip video dengan gerak kamera Ken Burns (efek parallax sederhana)."""
    frames = max(1, int(duration * fps))
    big_w, big_h = int(w * 1.25) // 2 * 2, int(h * 1.25) // 2 * 2
    z = {
        "zoom_in": ("min(1+0.0009*on,1.22)", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),
        "zoom_out": ("max(1.22-0.0009*on,1.0)", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),
        "pan_left": ("1.18", f"(iw-iw/zoom)*(1-on/{frames})", "ih/2-(ih/zoom/2)"),
        "pan_right": ("1.18", f"(iw-iw/zoom)*on/{frames}", "ih/2-(ih/zoom/2)"),
        "pan_up": ("1.18", "iw/2-(iw/zoom/2)", f"(ih-ih/zoom)*(1-on/{frames})"),
        "pan_down": ("1.18", "iw/2-(iw/zoom/2)", f"(ih-ih/zoom)*on/{frames}"),
        "still": ("1", "0", "0"),
    }.get(motion, ("min(1+0.0009*on,1.22)", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"))
    vf = (f"scale={big_w}:{big_h}:force_original_aspect_ratio=increase,crop={big_w}:{big_h},"
          f"zoompan=z='{z[0]}':x='{z[1]}':y='{z[2]}':d={frames}:s={w}x{h}:fps={fps},format=yuv420p")
    run(["-loop", "1", "-i", image, "-vf", vf, "-t", f"{duration:.3f}", "-an", *venc(19), out])
    return out


def video_to_scene(src: str, out: str, duration: float, w: int = W, h: int = H) -> str:
    """Footage stok → potongan sesuai durasi scene (diulang bila terlalu pendek), tanpa audio."""
    d = probe(src)["duration"]
    loops = max(0, math.ceil(duration / max(d, 0.1)) - 1)
    run(["-stream_loop", str(loops), "-i", src, "-t", f"{duration:.3f}", "-vf", f"{fit_filter(w, h)},fps=30,format=yuv420p",
         "-an", *venc(19), out])
    return out


def mux_voice_music(video: str, out: str, voice: Optional[str] = None, music: Optional[str] = None,
                    music_vol: float = 0.12, keep_original: bool = False) -> str:
    """Gabung suara narasi + musik latar (auto-duck: musik mengecil saat narasi bicara)."""
    info = probe(video)
    args = ["-i", video]
    idx = 1
    labels = []
    fc = []
    if keep_original and info["has_audio"]:
        labels.append("[0:a]")
    if voice:
        args += ["-i", voice]
        fc.append(f"[{idx}:a]aresample=48000,volume=1.0[vo]")
        voice_label = "[vo]"
        idx += 1
    else:
        voice_label = ""
    if music:
        args += ["-stream_loop", "-1", "-i", music]
        fc.append(f"[{idx}:a]aresample=48000,volume={music_vol:.3f}[mu]")
        if voice_label:
            fc.append("[vo]asplit[vo1][vo2]")
            fc.append("[mu][vo2]sidechaincompress=threshold=0.04:ratio=8:attack=20:release=400[mud]")
            voice_label = "[vo1]"
            labels.append("[mud]")
        else:
            labels.append("[mu]")
        idx += 1
    if voice_label:
        labels.append(voice_label)
    if not labels:
        shutil.copy2(video, out)
        return out
    fc.append(f"{''.join(labels)}amix=inputs={len(labels)}:duration=first:normalize=0[aout]")
    run([*args, "-filter_complex", ";".join(fc), "-map", "0:v", "-map", "[aout]", "-c:v", "copy", *AENC,
         "-t", f"{info['duration']:.3f}", "-movflags", "+faststart", out])
    return out


def burn_ass(src: str, ass: str, out: str) -> str:
    vf = f"subtitles='{escape_path(ass)}'"
    fonts = Path(config.FONT_DIR)
    if any(fonts.glob("*.[ot]tf")):
        vf += f":fontsdir='{escape_path(str(fonts))}'"
    run(["-i", src, "-vf", vf, *venc(18), "-c:a", "copy", "-movflags", "+faststart", out])
    return out


def extract_frame(src: str, t: float, out: str, width: int = 0) -> str:
    vf = ["-vf", f"scale={width}:-2"] if width else []
    run(["-ss", f"{max(0.0, t):.3f}", "-i", src, "-frames:v", "1", *vf, "-q:v", "2", out])
    return out


def extract_audio(src: str, out: str, sr: int = 44100) -> str:
    run(["-i", src, "-vn", "-ac", "1", "-ar", str(sr), out])
    return out


def change_speed(src: str, out: str, speed: float) -> str:
    speed = max(0.25, min(4.0, speed))
    atempo = []
    s = speed
    while s > 2.0:
        atempo.append("atempo=2.0"); s /= 2.0
    while s < 0.5:
        atempo.append("atempo=0.5"); s /= 0.5
    atempo.append(f"atempo={s:.4f}")
    info = probe(src)
    if info["has_audio"]:
        run(["-i", src, "-filter_complex", f"[0:v]setpts=PTS/{speed:.4f}[v];[0:a]{','.join(atempo)}[a]",
             "-map", "[v]", "-map", "[a]", *venc(18), *AENC, out])
    else:
        run(["-i", src, "-vf", f"setpts=PTS/{speed:.4f}", *venc(18), out])
    return out


def audio_energy(src: str, hop: float = 0.5) -> List[Tuple[float, float]]:
    """RMS audio per `hop` detik (mono 8 kHz). Dipakai mendeteksi sorakan penonton / komentator heboh."""
    import numpy as np
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(src), "-vn", "-ac", "1",
                           "-ar", "8000", "-f", "s16le", "-"], capture_output=True)
    if proc.returncode != 0 or not proc.stdout:
        raise FFmpegError("Gagal membaca audio video")
    pcm = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    step = int(8000 * hop)
    n = len(pcm) // step
    if n == 0:
        return []
    frames = pcm[: n * step].reshape(n, step)
    rms = np.sqrt((frames ** 2).mean(axis=1) + 1e-12)
    return [(i * hop, float(v)) for i, v in enumerate(rms)]


def color_card(out: str, text: str, w: int = W, h: int = H, bg: str = "#111827", fg: str = "#FFFFFF",
               accent: str = "#F59E0B", subtitle: str = "") -> str:
    """Kartu teks diam (PIL) — fallback visual bila tidak ada provider gambar."""
    from PIL import Image, ImageDraw, ImageFont
    import textwrap

    def hex2rgb(c):
        c = c.lstrip("#")
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))

    img = Image.new("RGB", (w, h), hex2rgb(bg))
    draw = ImageDraw.Draw(img)
    top, bot = hex2rgb(bg), tuple(max(0, v - 40) for v in hex2rgb(bg))
    for yy in range(h):
        t = yy / h
        draw.line([(0, yy), (w, yy)], fill=tuple(int(top[i] * (1 - t) + bot[i] * t) for i in range(3)))
    ff = font_file()
    size = int(w * 0.085)
    font = ImageFont.truetype(ff, size) if ff else ImageFont.load_default()
    lines = textwrap.wrap(text, width=16)[:6]
    lh = int(size * 1.25)
    y0 = h // 2 - lh * len(lines) // 2
    if not lines:
        # latar polos bergradasi + lingkaran aksen samar (teks datang dari overlay kinetik)
        r = int(min(w, h) * 0.38)
        glow = Image.new("RGB", (w, h), hex2rgb(accent))
        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).ellipse([w // 2 - r, int(h * 0.42) - r, w // 2 + r, int(h * 0.42) + r], fill=38)
        img.paste(glow, (0, 0), mask)
        img.save(out, quality=92)
        return out
    draw.rectangle([w * 0.1, y0 - lh * 0.8, w * 0.3, y0 - lh * 0.8 + 14], fill=hex2rgb(accent))
    for i, line in enumerate(lines):
        tw = draw.textlength(line, font=font)
        draw.text(((w - tw) / 2, y0 + i * lh), line, font=font, fill=hex2rgb(fg))
    if subtitle:
        sf = ImageFont.truetype(ff, int(size * 0.45)) if ff else ImageFont.load_default()
        tw = draw.textlength(subtitle, font=sf)
        draw.text(((w - tw) / 2, y0 + len(lines) * lh + 30), subtitle, font=sf, fill=hex2rgb(accent))
    img.save(out, quality=92)
    return out
