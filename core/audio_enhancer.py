"""
Audio Enhancer Module for Viral Auto Klip
Features: Noise reduction, Loudness normalization, Bass boost, Auto-duck for background music
Inspired by Klipers.pro premium audio tools
"""
import subprocess
import tempfile
from pathlib import Path
from loguru import logger
import config

def enhance_audio(
    input_audio: str,
    output_audio: str,
    noise_reduction: bool = True,
    normalize: bool = True,
    bass_boost: bool = True,
    bass_gain: float = 2.5,
    compress: bool = True,
) -> str:
    """
    Enhance audio using FFmpeg filters.
    v6.4: high-pass (buang dengung/getaran meja) → denoise ringan → EQ 'presence' agar suara
    jelas di speaker HP → kompresor suara (volume pelan/keras lebih rata). Loudness akhir
    (-14 LUFS) dikerjakan editing.master_audio setelah SFX dicampur, jadi normalize bisa dimatikan.
    Returns path to enhanced audio.
    """
    filters = ["highpass=f=70"]

    if noise_reduction:
        # High-quality noise reduction (afftdn is good for speech)
        filters.append("afftdn=nr=10:nf=-48")

    if bass_boost:
        # sedikit 'badan' suara; dulu +4 dB di 80 Hz membuat suara berdengung di speaker HP
        filters.append(f"equalizer=f=110:width_type=o:width=1.5:g={bass_gain}")
    filters.append("equalizer=f=3200:width_type=o:width=1.2:g=2")  # presence / kejelasan kata

    if compress:
        filters.append("acompressor=threshold=-21dB:ratio=3:attack=8:release=160:makeup=2.5:knee=4")

    if normalize:
        # EBU R128 loudness normalization (broadcast standard)
        filters.append("loudnorm=I=-16:TP=-1.5:LRA=11")
    
    if not filters:
        # Just copy
        subprocess.run(["ffmpeg", "-y", "-i", input_audio, "-c:a", "aac", "-b:a", "192k", output_audio], check=True)
        return output_audio
    
    filter_chain = ",".join(filters)
    cmd = [
        "ffmpeg", "-y",
        "-i", input_audio,
        "-af", filter_chain,
        # FIX: loudnorm meng-upsample ke 192 kHz -> tanpa -ar output jadi 96 kHz (boros & tidak standar)
        "-ar", "48000",
        "-c:a", "aac", "-b:a", "192k",
        output_audio
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        logger.success(f"✅ Audio enhanced: {Path(output_audio).name}")
        return output_audio
    except subprocess.CalledProcessError as e:
        logger.error(f"Audio enhancement failed: {e.stderr.decode()}")
        # Fallback: copy original
        subprocess.run(["ffmpeg", "-y", "-i", input_audio, "-c:a", "copy", output_audio], check=True)
        return output_audio


def add_background_music_with_duck(
    video_path: str,
    music_path: str,
    output_path: str,
    music_volume: float = 0.15,
    duck_amount: float = 0.35
) -> str:
    """
    Mix background music with auto-ducking (lower music when speech is loud).
    Uses ffmpeg sidechain compress for professional ducking.
    """
    temp_dir = Path(tempfile.mkdtemp())
    music_only = temp_dir / "music.wav"
    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", music_path, "-ac", "2", "-ar", "48000", str(music_only)
        ], check=True, capture_output=True)

        # FIX: parameter `makeup` sidechaincompress hanya menerima 1-64. Nilai lama
        # (duck_amount=0.35) selalu error -> ducking TIDAK PERNAH jalan, selalu fallback
        # ke mix biasa. Level musik sudah diatur oleh `volume`, jadi makeup=1 (netral).
        duck_filter = (
            f"[1:a]volume={music_volume}[music];"
            f"[music][0:a]sidechaincompress=threshold=0.03:ratio=8:"
            f"attack=50:release=300:makeup=1[ducked];"
            "[0:a][ducked]amix=inputs=2:duration=first:dropout_transition=2[aout]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-stream_loop", "-1", "-i", str(music_only),
            "-filter_complex", duck_filter,
            "-map", "0:v:0",
            "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            output_path
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        logger.success(f"✅ Background music with auto-duck added")
    except Exception as e:
        logger.warning(f"Ducking failed, using simple mix: {e}")
        # Fallback simple mix
        subprocess.run([
            "ffmpeg", "-y",
            "-i", video_path,
            "-stream_loop", "-1",
            "-i", music_path,
            "-filter_complex", f"[1:a]volume={music_volume}[music];[0:a][music]amix=inputs=2:duration=first:dropout_transition=2",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            output_path
        ], check=True)
    
    # Cleanup
    import shutil
    shutil.rmtree(temp_dir, ignore_errors=True)
    return output_path
