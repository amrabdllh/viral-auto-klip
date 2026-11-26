"""(v7) Subtitle live + burn ulang — caption bisa diedit SETELAH klip jadi, tanpa render ulang klip.

Saat render (core/clipper), encode utama menghasilkan dua video sekaligus:
  - <klip>_final.mp4  : hasil akhir (caption di lapisan teratas)
  - clean.mp4         : video yang sama TANPA caption (tanpa audio), timeline sebelum tempo
lalu disimpan `subtitle.json` (layar caption + timing per kata, gaya, tata letak) dan `live_audio.m4a`
(audio final sebelum Brand Kit). Burn ulang = clean.mp4 + caption baru (+ tempo yang sama) + audio itu,
lalu Brand Kit dipasang lagi bila klip memakainya. Satu encode, kualitas sama dengan render asli.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

import config

DATA_FILE = "subtitle.json"
CLEAN_FILE = "clean.mp4"
AUDIO_FILE = "live_audio.m4a"
# kunci gaya yang berasal dari tata letak klip (bukan dari gaya caption) → ikut saat gaya diganti
LAYOUT_KEYS = ("margin_zones", "power_words", "fx")
_locks: Dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


# ============================================================================ simpan (saat render)

def style_overrides(style_name: str, clip_style: Dict) -> Dict:
    """Bagian gaya klip yang harus dipertahankan saat gaya caption diganti (posisi, ukuran khusus, zona)."""
    base = config.CAPTION_STYLES.get(style_name) or {}
    out = {k: clip_style[k] for k in LAYOUT_KEYS if clip_style.get(k)}
    for k in ("font_size", "vertical_position"):
        if k in clip_style and clip_style.get(k) != base.get(k):
            out[k] = clip_style[k]
    return out


def save(clip_dir: Path, *, style_name: str, clip_style: Dict, segments: List[Dict], width: int, height: int,
         enabled: bool, tempo: float = 1.0, final_path: str = "") -> bool:
    """Tulis subtitle.json + ekstrak audio final (copy, tanpa encode). False bila master bersih tidak ada."""
    clip_dir = Path(clip_dir)
    clean = clip_dir / CLEAN_FILE
    if not clean.exists() or clean.stat().st_size == 0:
        return False
    if final_path:
        try:
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", final_path, "-vn", "-c:a", "copy",
                            str(clip_dir / AUDIO_FILE)], check=True, capture_output=True)
        except subprocess.CalledProcessError:
            return False
    data = {
        "version": 1, "style": style_name, "style_overrides": style_overrides(style_name, clip_style),
        "segments": [_seg_clean(s) for s in segments], "width": int(width), "height": int(height),
        "enabled": bool(enabled), "tempo": float(tempo or 1.0), "branded": None, "edited_at": None,
        "original": {"style": style_name, "enabled": bool(enabled)},
    }
    _write(clip_dir, data)
    return True


def mark_branded(final_path: str, force: Optional[bool]) -> None:
    """Brand Kit sudah dipasang di file final → dipasang lagi setelah burn ulang."""
    clip_dir = Path(final_path).parent
    data = load(clip_dir)
    if data is not None:
        data["branded"] = {"force": force}
        _write(clip_dir, data)


def set_intro(clip_dir, name: str) -> None:
    """Intro Hook B-roll (file di folder klip) disambung lagi di depan setiap burn ulang."""
    data = load(clip_dir)
    if data is not None:
        data["intro"] = name
        _write(Path(clip_dir), data)


def _seg_clean(seg: Dict) -> Dict:
    out = {"start": round(float(seg.get("start", 0)), 3), "end": round(float(seg.get("end", 0)), 3),
           "text": str(seg.get("text", "")).strip()}
    if seg.get("words"):
        out["words"] = [{"word": str(w.get("word", "")).strip(), "start": round(float(w.get("start", 0)), 3),
                         "end": round(float(w.get("end", 0)), 3), **({"stress": w["stress"]} if w.get("stress") else {})}
                        for w in seg["words"] if str(w.get("word", "")).strip()]
    return out


def _write(clip_dir: Path, data: Dict) -> None:
    tmp = Path(clip_dir) / (DATA_FILE + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, Path(clip_dir) / DATA_FILE)


# ============================================================================ baca

def clip_dir_of(final_path: str) -> Path:
    return Path(final_path).parent


def load(clip_dir) -> Optional[Dict]:
    p = Path(clip_dir) / DATA_FILE
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def available(final_path: str) -> bool:
    d = clip_dir_of(final_path)
    return (d / DATA_FILE).exists() and (d / CLEAN_FILE).exists() and (d / AUDIO_FILE).exists()


def editor_view(final_path: str) -> Dict:
    """Data untuk editor subtitle di UI: layar caption (teks + waktu), gaya, status."""
    d = clip_dir_of(final_path)
    data = load(d)
    if not data or not available(final_path):
        return {"available": False}
    return {"available": True, "style": data.get("style"), "enabled": data.get("enabled", True),
            "edited_at": data.get("edited_at"), "tempo": data.get("tempo", 1.0),
            "subtitle_y": current_y(data),
            "segments": [{"i": i, "start": s["start"], "end": s["end"], "text": s.get("text") or
                          " ".join(w["word"] for w in s.get("words") or [])} for i, s in enumerate(data["segments"])],
            "original": data.get("original")}


# ============================================================================ posisi

def current_y(data: Dict) -> float:
    """Posisi subtitle saat ini (pecahan dari atas, dibulatkan) untuk slider editor."""
    st = dict(config.CAPTION_STYLES.get(data.get("style") or "") or {})
    st.update(data.get("style_overrides") or {})
    margin = float(st.get("vertical_position", 420))
    return round(1.0 - margin / 1920.0, 3)


def move_overrides(overrides: Dict, subtitle_y: float) -> Dict:
    """Override gaya dengan posisi baru. Zona geser sementara (caption turun selama headline hook) hanya
    dipertahankan bila memang di bawah posisi baru — kalau tidak, subtitle akan melompat ke atas."""
    from .clipper import subtitle_margin

    out = dict(overrides)
    mv = subtitle_margin(max(0.12, min(0.92, subtitle_y)))
    out["vertical_position"] = mv
    zones = [z for z in out.get("margin_zones") or [] if len(z) >= 3 and int(z[2]) < mv]
    if zones:
        out["margin_zones"] = zones
    else:
        out.pop("margin_zones", None)
    return out


# ============================================================================ edit teks

def apply_text_edits(segments: List[Dict], edits: Dict[int, str]) -> List[Dict]:
    """Ganti teks layar caption. Jumlah kata sama → timing per kata dipertahankan (kata diganti di tempat);
    beda → waktu layar dibagi rata menurut panjang kata. Teks kosong = layar dihapus."""
    out = []
    for i, seg in enumerate(segments):
        if i not in edits:
            out.append(seg)
            continue
        text = " ".join(str(edits[i] or "").split())
        if not text:
            continue
        toks = text.split()
        words = seg.get("words") or []
        if words and len(words) == len(toks):
            new_words = [{**w, "word": t} for w, t in zip(words, toks)]
        else:
            s0, s1 = float(seg["start"]), float(seg["end"])
            weights = [max(2, len(t)) + 1 for t in toks]
            total, cur = float(sum(weights)), s0
            new_words = []
            for t, wt in zip(toks, weights):
                d = (s1 - s0) * wt / total
                new_words.append({"word": t, "start": round(cur, 3), "end": round(cur + d * 0.92, 3)})
                cur += d
        out.append({**seg, "text": text, "words": new_words})
    return out


# ============================================================================ burn ulang

def reburn(final_path: str, *, style: Optional[str] = None, edits: Optional[Dict[int, str]] = None,
           enabled: Optional[bool] = None, reset: bool = False, subtitle_y: Optional[float] = None) -> Dict:
    """Burn ulang caption ke klip final. `style` = id gaya (config.CAPTION_STYLES), `edits` = {indeks: teks},
    `enabled=False` = tanpa caption, `reset` = kembali ke teks & gaya asli,
    `subtitle_y` = posisi bawah teks (pecahan tinggi layar dari atas, 0.12–0.92)."""
    from . import editing
    from .clipper import _escape_ffmpeg_filter_path, _video_encode_args
    from .utils import generate_ass_subtitles

    final = Path(final_path)
    d = final.parent
    data = load(d)
    if not data or not available(str(final)):
        raise ValueError("Klip ini tidak punya data subtitle live (dirender sebelum fitur ini, atau fitur dimatikan).")
    if style is not None and style not in config.CAPTION_STYLES:
        raise ValueError(f"Gaya caption tidak dikenal: {style}")
    with _lock(str(d.resolve())):
        t0 = time.time()
        if reset:
            orig = d / "subtitle_original.json"
            if orig.exists():
                data = json.loads(orig.read_text(encoding="utf-8"))
        else:
            orig = d / "subtitle_original.json"
            if not orig.exists():  # simpan versi asli sekali, untuk tombol "Kembalikan"
                shutil.copy2(d / DATA_FILE, orig)
        segs = data["segments"]
        if edits:
            segs = apply_text_edits(segs, {int(k): v for k, v in edits.items()})
        style_name = style or data.get("style") or "viral"
        on = data.get("enabled", True) if enabled is None else bool(enabled)
        st = dict(config.CAPTION_STYLES.get(style_name) or config.CAPTION_STYLES["viral"])
        W, H = int(data["width"]), int(data["height"])
        if subtitle_y is not None and not reset:
            data["style_overrides"] = move_overrides(data.get("style_overrides") or {}, float(subtitle_y))
        st.update(data.get("style_overrides") or {})
        ass = d / "captions_live.ass"
        if on and segs:
            generate_ass_subtitles(segs, st, str(ass), W, H)
        tempo = float(data.get("tempo") or 1.0)
        chain = [editing.subtitle_filter(str(ass), _escape_ffmpeg_filter_path)] if on and segs else []
        if tempo > 1.0001:
            chain.append(f"setpts=PTS/{tempo:.4f}")
        chain.append("format=yuv420p")
        tmp = d / "_reburn.mp4"
        cmd = ["ffmpeg", "-y", "-i", str(d / CLEAN_FILE), "-i", str(d / AUDIO_FILE), "-filter_complex",
               f"[0:v]{','.join(chain)}[v]", "-map", "[v]", "-map", "1:a?", *_video_encode_args(crf=17),
               "-c:a", "copy", "-shortest", "-movflags", "+faststart", str(tmp)]
        subprocess.run(cmd, check=True, capture_output=True)
        intro = d / str(data.get("intro") or "")
        if data.get("intro") and intro.exists():  # Hook B-roll di depan klip
            from .hook_broll import prepend
            prepend(str(tmp), str(intro), _video_encode_args(crf=17))
        branded = data.get("branded")
        if branded is not None:  # logo/penutup Brand Kit dipasang lagi (mengikuti Brand Kit saat ini)
            from .creator import apply_brand
            out_b = str(d / "_reburn_branded.mp4")
            got = apply_brand(str(tmp), out_b, force=branded.get("force"))
            if got != str(tmp):
                os.replace(got, tmp)
        os.replace(tmp, final)
        ass.unlink(missing_ok=True)
        if not reset:
            data.update({"segments": segs, "style": style_name, "enabled": on, "edited_at": time.time()})
        _write(d, data)
        _update_metadata(final, style_name, on, data.get("edited_at"))
        return {"file_path": str(final), "style": style_name, "enabled": on, "seconds": round(time.time() - t0, 1),
                "screens": len(segs)}


def _update_metadata(final: Path, style_name: str, enabled: bool, edited_at) -> None:
    """Catat di metadata.json job (bila ada) agar UI/arsip tahu caption sudah diubah."""
    for meta in (final.parent.parent / "metadata.json", final.parent / "metadata.json"):
        if not meta.exists():
            continue
        try:
            m = json.loads(meta.read_text(encoding="utf-8"))
            hit = False
            for c in m.get("clips") or []:
                if Path(str(c.get("file_path", ""))).resolve() == final.resolve():
                    c.update({"caption_style": style_name, "captions": enabled, "subtitle_edited_at": edited_at})
                    hit = True
            if hit:
                tmp = meta.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
                os.replace(tmp, meta)
        except (OSError, ValueError):
            pass

