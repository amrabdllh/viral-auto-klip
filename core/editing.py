"""
Pro editing engine (v4) — membuat hasil klip terasa diedit manusia, bukan sekadar potong + subtitle.

v6.4 (lihat bagian bawah file):
- Cold open     : kalimat paling nendang diputar di detik pertama, lalu efek "rewind" ke awal cerita.
- Sound design  : impact di hook, riser → impact di puncak (lonjakan chat/suara), pop saat stiker muncul.
- Stiker emoji  : emoji berwarna muncul dengan animasi pop di kata kunci/reaksi.
- Mastering     : loudness -14 LUFS dua tahap (standar TikTok/Shorts/Reels) + limiter.

Fitur (semua FFmpeg/OpenCV, tanpa aset eksternal):
- Jump cut      : buang jeda panjang & kata pengisi ("eh", "em", "anu", "um") → tempo rapat.
- Dynamic zoom  : zoom bergantian per kalimat (kesan multi-kamera) + punch-in di kata penekanan
                  + hook punch 0.8 detik pertama. Diterapkan SEBELUM caption agar teks tidak ikut goyang.
- Color grade   : natural / vibrant / cinematic.
- Progress bar  : bar tipis di bawah (trik retensi TikTok/Shorts).
- Whoosh SFX    : efek suara halus saat punch-in (disintesis FFmpeg).
- Smart frame   : pilih frame thumbnail paling tajam, terang cukup, dengan wajah jelas.
- End CTA       : ajakan follow di 2.5 detik terakhir.
"""
from __future__ import annotations

import re
import subprocess
import threading
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
from loguru import logger

import config

# ============================================================ presets

EDIT_PRESETS: Dict[str, Dict] = {
    "minimal": {"jump_cuts": False, "dynamic_zoom": False, "progress_bar": False, "sfx": False,
                "color_grade": "none", "smart_thumbnail": True, "end_cta": False,
                "cold_open": False, "sound_design": False, "caption_fx": False, "audio_master": True,
                "speaker_cam": True, "broll": False, "shake": False, "freeze": False, "lower_third": False, "censor": False,
                "pro_fx": False, "auto_tempo": False},
    "dinamis": {"jump_cuts": True, "dynamic_zoom": True, "progress_bar": True, "sfx": False,
                "color_grade": "film", "smart_thumbnail": True, "end_cta": False,
                "cold_open": True, "sound_design": True, "caption_fx": True, "audio_master": True,
                "speaker_cam": True, "broll": True, "shake": True, "freeze": False, "lower_third": True, "censor": True,
                "pro_fx": True, "auto_tempo": True},
    "maksimal": {"jump_cuts": True, "dynamic_zoom": True, "progress_bar": True, "sfx": True,
                 "color_grade": "teal_orange", "smart_thumbnail": True, "end_cta": False,
                 "cold_open": True, "sound_design": True, "caption_fx": True, "audio_master": True,
                 "speaker_cam": True, "broll": True, "shake": True, "freeze": True, "lower_third": True, "censor": True,
                 "pro_fx": True, "auto_tempo": True},
}
EDIT_KEYS = ("jump_cuts", "dynamic_zoom", "progress_bar", "sfx", "color_grade", "smart_thumbnail", "end_cta",
             "cold_open", "sound_design", "caption_fx", "audio_master",
             "speaker_cam", "broll", "shake", "freeze", "lower_third", "censor", "pro_fx", "auto_tempo")

COLOR_GRADES: Dict[str, str] = {
    "none": "",
    "natural": "eq=contrast=1.04:saturation=1.08,unsharp=5:5:0.35:5:5:0",
    "vibrant": "eq=contrast=1.08:saturation=1.28:brightness=0.01,unsharp=5:5:0.55:5:5:0",
    # "pro": kurva-S (hitam lebih dalam, highlight bersih), saturasi kulit sedikit naik, bayangan agak dingin, sorotan hangat
    "pro": ("curves=m='0/0 0.20/0.17 0.50/0.52 0.80/0.86 1/1',eq=contrast=1.05:saturation=1.14,"
            "colorbalance=rs=0.02:bs=-0.03:rh=-0.02:bh=0.04"),
    # LUT 3D buatan core/looks.py (diisi saat render; string kosong di sini hanya penanda nama valid)
    "film": "",
    "teal_orange": "",
    "cinematic": "curves=preset=medium_contrast,eq=saturation=0.9,colorbalance=rs=0.04:bs=-0.05:rh=-0.03:bh=0.06,vignette=PI/5",
}

# Finishing SETELAH zoom (zoom me-resample ulang dan melunakkan gambar): sharpen, vignette lembut, grain halus
# yang menyamarkan blok kompresi sumber. Dipakai bila grade bukan "none".
FINISH_CHAIN = "unsharp=7:7:0.55:5:5:0,vignette=PI/6,noise=alls=4:allf=t"

FILLER_WORDS = {
    "eh", "ehm", "em", "emm", "eem", "hmm", "hm", "mm", "mmm", "anu", "eee", "ee",
    "uh", "uhm", "um", "umm", "erm", "er", "ah", "uhh",
}
# variasi panjang yang ditulis transkriptor: "eeeh", "hmmm", "ehmmm", "aah", "uhmm", "eemm", "errr"
_FILLER_RE = re.compile(r"(e{1,}h*m*|e+r+m*|h+m+|m{2,}|a{2,}h*|a+h{2,}|u+h*m*)")


def is_filler(word: str) -> bool:
    c = _clean(word)
    return bool(c) and (c in FILLER_WORDS or bool(_FILLER_RE.fullmatch(c)))


def resolve_edit_opts(overrides: Optional[Dict] = None) -> Dict:
    """Gabungkan preset (EDIT_STYLE di .env / request) + override per fitur."""
    overrides = dict(overrides or {})
    style = str(overrides.pop("style", "") or config.EDIT_STYLE or "dinamis").lower()
    opts = dict(EDIT_PRESETS.get(style, EDIT_PRESETS["dinamis"]))
    opts["style"] = style if style in EDIT_PRESETS else "dinamis"
    for key in EDIT_KEYS:
        if overrides.get(key) is not None:
            opts[key] = overrides[key]
    if opts["color_grade"] not in COLOR_GRADES:
        opts["color_grade"] = "none"
    return opts


# ============================================================ words helpers

def _clean(word: str) -> str:
    return re.sub(r"[^\w]", "", str(word or "").lower(), flags=re.UNICODE)


def relative_words(clip_info: Dict) -> List[Dict]:
    """Kata-kata klip dengan timestamp relatif terhadap awal klip."""
    start, end = float(clip_info["start"]), float(clip_info["end"])
    out = []
    for w in clip_info.get("words") or []:
        ws, we = float(w.get("start", 0)), float(w.get("end", 0))
        if we <= start or ws >= end or not str(w.get("word", "")).strip():
            continue
        item = {"start": max(ws - start, 0.0), "end": min(we - start, end - start), "word": str(w["word"]).strip()}
        for key in ("speaker", "stress", "prom", "pause_before"):  # anotasi audio (core/audio_signals)
            if w.get(key):
                item[key] = w[key]
        out.append(item)
    return sorted(out, key=lambda w: w["start"])


# ============================================================ jump cuts

def plan_jump_cuts(words_rel: List[Dict], duration: float, max_gap: float = 0.55, pad: float = 0.10) -> List[Tuple[float, float]]:
    """Interval yang DIPERTAHANKAN: buang jeda > max_gap dan kata pengisi berdiri sendiri."""
    spoken = [w for w in words_rel if not is_filler(w["word"])]
    if len(spoken) < 3:
        return [(0.0, duration)]
    spans: List[List[float]] = []
    for w in spoken:
        s, e = max(w["start"] - pad, 0.0), min(w["end"] + pad, duration)
        if spans and s - spans[-1][1] <= max_gap:
            spans[-1][1] = max(spans[-1][1], e)
        else:
            spans.append([s, e])
    spans[0][0] = max(spans[0][0] - 0.05, 0.0)
    spans[-1][1] = min(spans[-1][1] + 0.25, duration)  # ekor kecil agar kalimat terakhir tidak terpotong
    return [(round(s, 3), round(e, 3)) for s, e in spans if e - s >= 0.12]


def plan_tempo(words_rel: List[Dict], target_wps: Optional[float] = None, max_tempo: Optional[float] = None) -> float:
    """v6.8: percepatan halus untuk pembicara lambat (gaya kliper: 1,05–1,1×), nada suara tetap.

    Diukur dari kata per detik SETELAH jump cut. Data podcast uji: median 2,93 kata/dtk; klip viral terasa pas
    ±3,2. Klip yang sudah cepat tidak diubah (1.0). Hasil dibulatkan 0,01; < 1,03 dianggap tidak perlu."""
    target = float(target_wps or getattr(config, "AUTO_TEMPO_TARGET_WPS", 3.15))
    cap = float(max_tempo or getattr(config, "AUTO_TEMPO_MAX", 1.08))
    spoken = [w for w in words_rel if not is_filler(w.get("word", ""))]
    if len(spoken) < 15:
        return 1.0
    span = float(spoken[-1]["end"]) - float(spoken[0]["start"])
    if span <= 3:
        return 1.0
    tempo = round(max(1.0, min(cap, target / (len(spoken) / span))), 2)
    return tempo if tempo >= 1.03 else 1.0


def apply_tempo(input_path: str, output_path: str, tempo: float, encode_args: List[str]) -> None:
    """Percepat video+audio bersamaan (setpts + atempo → nada tidak berubah); semua efek yang sudah dibakar ikut."""
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", input_path, "-filter_complex",
                    f"[0:v]setpts=PTS/{tempo:.4f}[v];[0:a]atempo={tempo:.4f}[a]", "-map", "[v]", "-map", "[a]",
                    *encode_args, "-c:a", "aac", "-b:a", "256k", "-ar", "48000", "-movflags", "+faststart", output_path],
                   check=True, capture_output=True)


def kept_duration(intervals: Sequence[Tuple[float, float]]) -> float:
    return sum(e - s for s, e in intervals)


def remap_time(t: float, intervals: Sequence[Tuple[float, float]]) -> float:
    """Waktu lama (relatif klip) -> waktu baru setelah jump cut."""
    acc = 0.0
    for s, e in intervals:
        if t < s:
            return acc
        if t <= e:
            return acc + (t - s)
        acc += e - s
    return acc


def remap_clip_info(clip_info: Dict, intervals: Sequence[Tuple[float, float]]) -> Dict:
    """Sesuaikan words/subtitle/durasi klip ke timeline baru (absolut tetap berbasis clip start)."""
    start = float(clip_info["start"])
    new = dict(clip_info)
    words = []
    for w in clip_info.get("words") or []:
        rel_s, rel_e = float(w.get("start", 0)) - start, float(w.get("end", 0)) - start
        if is_filler(w.get("word", "")):
            continue
        if not any(s - 0.05 <= rel_s <= e + 0.05 for s, e in intervals):
            continue
        ns, ne = remap_time(rel_s, intervals), remap_time(rel_e, intervals)
        words.append({**w, "start": start + ns, "end": start + max(ne, ns + 0.05)})
    new["words"] = words
    new["subtitle_segments"] = []  # sudah tidak sinkron; caption dibangun dari words
    new_duration = kept_duration(intervals)
    new["original_end"] = clip_info["end"]
    new["end"] = start + new_duration
    new["duration"] = new_duration
    return new


def apply_jump_cuts(input_path: str, output_path: str, intervals: Sequence[Tuple[float, float]], encode_args: List[str]) -> None:
    """Render ulang hanya bagian yang dipertahankan (video+audio sinkron, micro-fade anti 'klik')."""
    parts, labels = [], []
    for i, (s, e) in enumerate(intervals):
        d = max(e - s, 0.05)
        fade = min(0.012, d / 4)
        parts.append(f"[0:v]trim=start={s:.3f}:end={e:.3f},setpts=PTS-STARTPTS[v{i}]")
        parts.append(f"[0:a]atrim=start={s:.3f}:end={e:.3f},asetpts=PTS-STARTPTS,"
                     f"afade=t=in:d={fade:.3f},afade=t=out:st={max(d - fade, 0):.3f}:d={fade:.3f}[a{i}]")
        labels.append(f"[v{i}][a{i}]")
    graph = ";".join(parts) + ";" + "".join(labels) + f"concat=n={len(intervals)}:v=1:a=1[vout][aout]"
    script = Path(output_path).with_suffix(".jumpcut.txt")
    script.write_text(graph, encoding="utf-8")  # graph bisa panjang -> lewat file, bukan argumen CLI
    try:
        # `-/filter_complex <file>` = baca graph dari file (FFmpeg ≥7; -filter_complex_script sudah dihapus di FFmpeg 9)
        cmd = ["ffmpeg", "-y", "-i", input_path, "-/filter_complex", str(script),
               "-map", "[vout]", "-map", "[aout]", *encode_args, "-c:a", "aac", "-b:a", "192k", output_path]
        subprocess.run(cmd, check=True, capture_output=True)
    finally:
        script.unlink(missing_ok=True)


# ============================================================ dynamic zoom

def shot_plan(words_rel: List[Dict], duration: float, max_shot: float = 3.8) -> List[Tuple[float, float]]:
    """Bagi klip menjadi 'shot' di batas kalimat / tiap ±3.8 detik (selalu di batas kata)."""
    if not words_rel:
        step = max_shot
        return [(t, min(t + step, duration)) for t in [i * step for i in range(int(duration // step) + 1)] if t < duration]
    shots, shot_start = [], 0.0
    for i, w in enumerate(words_rel):
        sentence_end = str(w["word"]).endswith((".", "!", "?"))
        nxt = words_rel[i + 1]["start"] if i + 1 < len(words_rel) else duration
        if (sentence_end and w["end"] - shot_start >= 1.6) or (nxt - shot_start >= max_shot):
            cut = (w["end"] + nxt) / 2 if nxt > w["end"] else w["end"]
            if cut - shot_start >= 1.0:
                shots.append((shot_start, cut))
                shot_start = cut
    if duration - shot_start > 0.3 or not shots:
        shots.append((shot_start, duration))
    else:
        shots[-1] = (shots[-1][0], duration)  # sisa kecil digabung ke shot terakhir
    return shots


def emphasis_moments(words_rel: List[Dict], limit: int = 6, min_spacing: float = 2.5) -> List[Tuple[float, float]]:
    """Kata penekanan: kata yang BENAR-BENAR ditekan pembicara (audio: lebih keras/tinggi dari sekitarnya, jeda
    dramatis sebelumnya) + kata kunci viral, angka, seruan, HURUF KAPITAL."""
    keywords = {w for words in config.VIRAL_KEYWORDS.values() for w in words if " " not in w}
    keywords |= {"rahasia", "ternyata", "penting", "gila", "wow", "serius", "never", "always", "secret", "truth"}
    candidates = []
    for w in words_rel:
        raw, clean = str(w["word"]), _clean(w["word"])
        score = 0
        if clean in keywords:
            score += 3
        if raw.endswith("!"):
            score += 2
        if re.search(r"\d", raw):
            score += 2
        if raw.isupper() and len(clean) > 2:
            score += 1
        stress = int(w.get("stress") or 0)
        if stress >= 2:
            score += 2 * stress - 1  # 2 → +3, 3 → +5: penekanan nyata mengalahkan kata kunci yang diucapkan biasa
            if w.get("pause_before"):
                score += 2
        if score and w["start"] > 1.0:
            candidates.append((score, w["start"], w["end"]))
    chosen: List[Tuple[float, float]] = []
    for _, s, e in sorted(candidates, key=lambda c: (-c[0], c[1])):
        if all(abs(s - cs) >= min_spacing for cs, _ in chosen):
            chosen.append((s, max(e, s + 0.25) + 0.35))
        if len(chosen) >= limit:
            break
    return sorted(chosen)


def zoom_filter(width: int, height: int, shots: List[Tuple[float, float]], emphasis: List[Tuple[float, float]],
                dynamic: bool = True, hook_punch: bool = True, alt_shots: bool = True,
                extra_punches: Sequence[float] = (), shakes: Sequence[float] = ()) -> str:
    """Rantai filter scale(eval=frame)+crop: Z(t) = 1 + max(alt_shot, punch_emphasis, hook_punch).

    alt_shots=False untuk layout split (facecam+gameplay): zoom bergantian terasa aneh di dua panel.
    extra_punches: titik "hook punch" tambahan (mis. awal cerita setelah cold open)."""
    terms = []
    if hook_punch:
        terms.append("if(lt(t,0.8),0.10-0.125*t,0)")
        for p in extra_punches:
            terms.append(f"if(between(t,{p:.2f},{p + 0.8:.2f}),0.10-0.125*(t-{p:.2f}),0)")
    if dynamic:
        alt = "+".join(f"between(t,{s:.2f},{e:.2f})" for i, (s, e) in enumerate(shots[:60]) if i % 2 == 1) if alt_shots else ""
        if alt:
            terms.append(f"0.07*({alt})")
        if emphasis:
            # punch dengan ease-in cepat (0.12 dtk) supaya tidak terasa patah
            punch = "+".join(f"between(t,{s:.2f},{e:.2f})*min(1,(t-{s:.2f})*8)" for s, e in emphasis[:12])
            terms.append(f"0.13*({punch})")
    shakes = [float(t) for t in shakes][:4]
    if shakes:  # layar goyang: zoom kecil memberi ruang geser, goyangan meredam dalam 0.45 dtk
        terms.append("0.06*(" + "+".join(f"between(t,{a:.2f},{a + 0.45:.2f})" for a in shakes) + ")")
    if not terms:
        return ""
    expr = terms[0]
    for term in terms[1:]:
        expr = f"max({expr},{term})"
    w, h = width // 2 * 2, height // 2 * 2
    sx = sy = "0"
    if shakes:
        sx = "+".join(f"between(t,{a:.2f},{a + 0.45:.2f})*sin((t-{a:.2f})*75)*(1-(t-{a:.2f})/0.45)" for a in shakes)
        sy = "+".join(f"between(t,{a:.2f},{a + 0.45:.2f})*cos((t-{a:.2f})*61)*(1-(t-{a:.2f})/0.45)" for a in shakes)
    return (f"scale=w='trunc(iw*(1+{expr})/2)*2':h=-2:eval=frame:flags=bicubic,"
            f"crop={w}:{h}:'(in_w-{w})/2*(1+0.9*({sx}))':'(in_h-{h})*(0.42+0.4*({sy}))'")


# ============================================================ polish render (grade + zoom + caption + progress bar)

OUTPUT_FPS = 30  # frame rate konstan (CFR) — dulu hasil 29.93/29.95 fps tidak beraturan


def grade_filter(grade: str, escape_path) -> str:
    """Filter warna untuk nama grade: LUT 3D (film/teal_orange) atau rantai eq/curves biasa."""
    from . import looks
    if grade in looks.LOOKS:
        return f"lut3d=file='{escape_path(looks.lut_path(grade))}':interp=tetrahedral"
    return COLOR_GRADES.get(grade, "")


def render_polish(input_path: str, output_path: str, *, width: int, height: int, duration: float,
                  ass_path: Optional[str], grade: str, zoom_chain: str, progress_bar: bool,
                  encode_args: List[str], escape_path, overlays: Optional[List[str]] = None,
                  stickers: Optional[List[Dict]] = None, broll: Optional[List[Dict]] = None,
                  fx_chain: str = "", behind_text: Optional[Dict] = None, leaks: Sequence[float] = ()) -> None:
    """Satu kali encode: fps CFR → color grade → zoom → b-roll → caption (tidak ikut zoom)
    → overlay teks (kartu hook, watermark, CTA akhir, lower third) → stiker emoji → progress bar.

    v4: kartu hook & CTA dulu masing-masing encode ulang sendiri (tambah 2 generasi kualitas);
    sekarang semuanya di encode yang sama.
    """
    finish = FINISH_CHAIN if grade and grade != "none" else ""
    pre = ",".join([f"fps={OUTPUT_FPS}"] + [c for c in (grade_filter(grade, escape_path), zoom_chain, finish, fx_chain) if c])
    post = []
    if ass_path:
        sub = f"subtitles='{escape_path(ass_path)}'"
        fonts_dir = Path(config.FONT_DIR)
        if fonts_dir.exists() and any(fonts_dir.glob("*.[ot]tf")):
            sub += f":fontsdir='{escape_path(str(fonts_dir))}'"
        post.append(sub)
    post.extend(o for o in (overlays or []) if o)
    inputs: List[str] = []
    n_in = 0

    def add_image(path: str, t0: float = 0.0, span: Optional[float] = None) -> int:
        """Gambar diam sebagai input berulang. Hanya `span` detik yang dibangkitkan (bukan seluruh klip):
        scale(eval=frame) dievaluasi PER FRAME, jadi dulu b-roll/stiker memakan ±28 dtk x 30 fps kerja
        scaler di luar jendela tampilnya (render 100 dtk untuk klip 24 dtk)."""
        nonlocal n_in
        length = duration + 0.5 if span is None else span
        inputs.extend(["-loop", "1", "-framerate", str(OUTPUT_FPS), "-t", f"{length:.2f}", "-i", path])
        n_in += 1
        offset[n_in] = t0
        return n_in

    offset: Dict[int, float] = {}

    def shifted(idx: int) -> str:
        """Awalan filter yang menggeser timestamp input ke waktu tampilnya di timeline utama."""
        return f"setpts=PTS+{offset.get(idx, 0.0):.3f}/TB," if offset.get(idx) else ""

    def add_input(args: List[str], t0: float = 0.0) -> int:
        nonlocal n_in
        inputs.extend(args)
        n_in += 1
        offset[n_in] = t0
        return n_in

    parts = [f"[0:v]{pre}[p0]"]
    cur = "p0"
    if behind_text and behind_text.get("mask") and behind_text.get("filters"):
        # teks hook DI BELAKANG orang: teks digambar di frame, lalu orang (mask segmentasi, ikut zoom yang sama)
        # ditempel lagi di atasnya -> kepala/bahu menutupi sebagian huruf, kesan berlapis seperti editan pro
        until = float(behind_text["until"])
        mk = add_input(["-i", behind_text["mask"]])
        parts.append("[p0]split[bt0][bp0]")
        parts.append(f"[bt0]{','.join(behind_text['filters'])}[bt1]")
        parts.append(f"[{mk}:v]fps={OUTPUT_FPS},format=gray,tpad=stop_mode=add:stop_duration={duration + 1:.2f},"
                     f"scale={width}:{height},{zoom_chain or 'null'},format=gray[bm]")
        parts.append("[bp0][bm]alphamerge[bp1]")
        parts.append(f"[bt1][bp1]overlay=0:0:enable='lte(t,{until:.2f})':eof_action=pass,format=yuv420p[p0b]")
        cur = "p0b"
    for i, t0 in enumerate(list(leaks)[:3]):
        # light leak: cahaya hangat dari sudut, masuk-keluar 0.9 dtk di momen puncak
        t0 = float(t0)
        lk = add_input(["-f", "lavfi", "-t", "0.9", "-i",
                        "gradients=s=540x960:r=30:type=radial:x0=540:y0=0:x1=300:y1=380:speed=0.03:nb_colors=3:"
                        "c0=0xFFB050@0.9:c1=0xFF6A20@0.25:c2=0x00000000"], t0)
        parts.append(f"[{lk}:v]{shifted(lk)}format=rgba,scale={width}:{height},gblur=sigma=30,"
                     f"fade=t=in:st={t0:.2f}:d=0.25:alpha=1,fade=t=out:st={t0 + 0.5:.2f}:d=0.4:alpha=1,"
                     f"colorchannelmixer=aa=0.6[lk{i}]")
        parts.append(f"[{cur}][lk{i}]overlay=0:0:enable='between(t,{t0:.2f},{t0 + 0.9:.2f})':eof_action=pass[pl{i}]")
        cur = f"pl{i}"
    for i, b in enumerate((broll or [])[:3]):
        # b-roll layar penuh: potong keras masuk, zoom pelan (Ken Burns) selama tampil
        t0, t1 = float(b["t"]), float(b["t"]) + float(b["dur"])
        idx = add_image(b["img"], t0, float(b["dur"]) + 0.2)
        z = f"(1.10-0.08*min(1,(t-{t0:.2f})/0.2)+0.06*max(0,(t-{t0:.2f}))/{float(b['dur']):.2f})"
        parts.append(f"[{idx}:v]{shifted(idx)}scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1,"
                     f"scale=w='trunc({width}*{z}/2)*2':h=-2:eval=frame,crop={width}:{height},format=yuv420p[b{i}]")
        parts.append(f"[{cur}][b{i}]overlay=0:0:enable='between(t,{t0:.2f},{t1:.2f})':eof_action=pass[pb{i}]")
        cur = f"pb{i}"
    parts.append(f"[{cur}]{','.join(post) or 'null'},format=yuv420p[s0]")
    last = "s0"
    for i, st in enumerate((stickers or [])[:8]):
        idx = add_image(st["png"], float(st["t"]), float(st["dur"]) + 0.2)
        parts.append(f"[{idx}:v]{shifted(idx)}format=rgba,{sticker_anim_filter(st['t'], st['size'])}[k{i}]")
        cx, cy = st["cx"], st["cy"]
        parts.append(f"[{last}][k{i}]overlay=x={cx}-w/2:y={cy}-h/2:enable='between(t,{st['t']:.2f},{st['t'] + st['dur']:.2f})'"
                     f":eval=frame:eof_action=pass[s{i + 1}]")
        last = f"s{i + 1}"
    if progress_bar and duration > 1:
        bar_h = max(6, height // 240)
        color = config.PROGRESS_BAR_COLOR.lstrip("#")
        parts.append(f"color=c=0x{color}:s={width}x{bar_h}:r={OUTPUT_FPS}:d={duration + 1:.2f},format=yuva420p,colorchannelmixer=aa=0.9[bar]")
        parts.append(f"[{last}][bar]overlay=x='-w+W*t/{duration:.3f}':y=H-h:eval=frame:shortest=1,format=yuv420p[vout]")
    else:
        parts.append(f"[{last}]format=yuv420p[vout]")
    graph = ";".join(parts)
    cmd = ["ffmpeg", "-y", "-i", input_path, *inputs, "-filter_complex", graph, "-map", "[vout]", "-map", "0:a?",
           *encode_args, "-c:a", "copy", "-movflags", "+faststart", output_path]
    subprocess.run(cmd, check=True, capture_output=True)


# ============================================================ v6.7: efek visual pro

def fx_chain(flashes: Sequence[float] = (), blur_starts: Sequence[float] = ()) -> str:
    """Flash putih singkat (transisi/puncak) + motion blur 0.16 dtk saat punch-in zoom (gerak terasa mulus)."""
    parts = []
    blur = [float(t) for t in blur_starts if t >= 0][:14]
    if blur:
        win = "+".join(f"between(t,{t:.2f},{t + 0.16:.2f})" for t in blur)
        parts.append(f"tmix=frames=3:weights='1 2 1':enable='{win}'")
    fl = [float(t) for t in flashes if t >= 0][:8]
    if fl:
        expr = "+".join(f"between(t,{t:.2f},{t + 0.14:.2f})*0.30*(1-(t-{t:.2f})/0.14)" for t in fl)
        parts.append(f"eq=brightness='{expr}':eval=frame")
    return ",".join(parts)


HEAVY_FONTS = ("C:/Windows/Fonts/ariblk.ttf", "C:/Windows/Fonts/impact.ttf",
               "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")


def _heavy_font() -> str:
    for cand in [Path(config.FONT_DIR) / "Montserrat-Black.ttf", Path(config.FONT_DIR) / "Montserrat-ExtraBold.ttf",
                 *map(Path, HEAVY_FONTS)]:
        if cand.exists():
            return str(cand)
    return ""


def person_mask(video_path: str, out_path: str, until: float) -> Optional[np.ndarray]:
    """Mask orang (MediaPipe) untuk `until` detik pertama -> video gray lossless. Return mask rata-rata (untuk tata letak)."""
    import cv2
    from .mediapipe_compat import SelfieSegmenterCompat
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or OUTPUT_FPS
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    seg = SelfieSegmenterCompat()
    proc = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{w}x{h}",
                             "-r", f"{fps:.5f}", "-i", "-", "-c:v", "ffv1", "-pix_fmt", "gray", out_path],
                            stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    acc, n = None, 0
    try:
        for _ in range(int((until + 0.2) * fps)):
            ok, frame = cap.read()
            if not ok:
                break
            small = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB), (w // 2, h // 2), interpolation=cv2.INTER_AREA)
            m = seg.segment_mask(small)
            m = cv2.GaussianBlur(cv2.resize(m, (w, h), interpolation=cv2.INTER_LINEAR), (0, 0), 4)
            m8 = (np.clip((m - 0.35) / 0.4, 0, 1) * 255).astype(np.uint8)  # tepi tegas tapi lembut
            proc.stdin.write(m8.tobytes())
            acc = m8.astype(np.float32) if acc is None else acc + m8
            n += 1
    finally:
        cap.release()
        seg.close()
        proc.stdin.close()
        proc.wait()
    if proc.returncode != 0 or not n:
        return None
    return acc / n / 255.0


def behind_text_plan(hook: str, mask_avg: np.ndarray, width: int, height: int, text_dir: Path, escape,
                     until: float) -> Optional[Dict]:
    """Susun teks hook besar di pita vertikal yang sebagian tertutup kepala/bahu (12-40%).
    None bila tidak ada posisi yang tetap terbaca -> pemanggil memakai kartu hook biasa."""
    import textwrap
    words = strip_emoji(hook).upper().split()
    if not words or len(" ".join(words)) > 64:
        return None
    text = " ".join(words)
    target = 1 if len(text) <= 12 else 2 if len(text) <= 28 else 3 if len(text) <= 44 else 4  # baris sedikit = huruf besar
    lines = textwrap.wrap(text, width=max(10, -(-len(text) // target) + 2))
    if len(lines) > 4:
        return None
    longest = max(len(l) for l in lines)
    fs = int(min(height / 9.5, width * 0.94 / max(longest * 0.70, 1)))
    if fs < height / 30:
        return None
    lh = int(fs * 1.02)
    band_h = lh * len(lines)
    # Posisi: baris atas harus bersih (≤12% tertutup) agar terbaca; hanya baris terakhir yang boleh "tenggelam"
    # sedikit di belakang kepala (6–30%) untuk kesan berlapis. Tanpa posisi seperti itu → teks di atas kepala
    # tanpa tumpang tindih; bila itu pun tidak ada → None (kartu hook biasa).
    def line_cov(y0: int) -> List[float]:
        return [float(mask_avg[y0 + i * lh:y0 + (i + 1) * lh, :].mean()) for i in range(len(lines))]

    best, best_d, clear = None, 9.0, None
    for y0 in range(int(height * 0.05), max(int(height * 0.05) + 1, int(height * 0.55) - band_h), int(height * 0.01)):
        cov = line_cov(y0)
        if max(cov[:-1] or [0.0]) > 0.12:
            continue
        if 0.06 <= cov[-1] <= 0.30 and abs(cov[-1] - 0.16) < best_d:
            best, best_d = y0, abs(cov[-1] - 0.16)
        elif cov[-1] <= 0.06:
            clear = y0  # posisi paling bawah yang masih bersih (paling dekat kepala)
    front = False
    if best is None:
        best = clear
    if best is None:
        # close-up: kepala memenuhi bagian atas → headline tebal DI DEPAN, di area dada di atas subtitle (wajah bebas)
        best, front = int(height * 0.71) - band_h, True
        if best < height * 0.40:
            return None
    if best is None:
        return None
    font = _heavy_font()
    ff = f"fontfile='{escape(font)}':" if font else ""
    filters, files = [], []
    for i, line in enumerate(lines):
        tf = Path(text_dir) / f"_bt_{i}.txt"
        tf.write_text(line, encoding="utf-8")
        files.append(str(tf))
        color = "0xFFD400" if (i == len(lines) - 1 and len(lines) > 1) else "white"
        d = 0.06 * i  # tiap baris masuk sedikit bergantian
        alpha = f"if(lt(t,{until - 0.3:.2f}),min(1,max(0,(t-{d:.2f})/0.18)),max(0,({until:.2f}-t)/0.3))"
        y = f"{best + i * lh}+50*max(0,1-(t-{d:.2f})/0.28)"
        filters.append(f"drawtext={ff}textfile='{escape(str(tf))}':expansion=none:x=(w-tw)/2:y='{y}':fontsize={fs}:"
                       f"fontcolor={color}:shadowcolor=black@0.55:shadowx=0:shadowy={max(4, fs // 22)}:"
                       f"alpha='{alpha}':enable='lte(t,{until:.2f})'")
    return {"filters": filters, "files": files, "until": until, "y": best, "size": fs, "front": front}


# ============================================================ SFX

_SFX_LOCK = threading.Lock()


def whoosh_path() -> Path:
    """Sintesis efek 'whoosh' sekali (noise tersaring + sweep fade), disimpan di temp/."""
    target = Path(config.TEMP_DIR) / "sfx_whoosh.wav"
    with _SFX_LOCK:
        if not target.exists():
            subprocess.run([
                "ffmpeg", "-y", "-f", "lavfi", "-i", "anoisesrc=color=pink:duration=0.5:amplitude=0.7",
                "-af", "highpass=f=350,lowpass=f=5500,afade=t=in:d=0.18,afade=t=out:st=0.2:d=0.3,volume=0.9",
                "-ar", "48000", "-ac", "2", str(target)], check=True, capture_output=True)
    return target


def add_sfx(input_path: str, output_path: str, times: Sequence[float], volume: float = 0.35) -> bool:
    """Campur whoosh di titik-titik waktu (detik). Video di-copy (tanpa encode ulang)."""
    times = [t for t in times if t >= 0.2][:12]
    if not times:
        return False
    sfx = whoosh_path()
    parts = [f"[1:a]asplit={len(times)}" + "".join(f"[s{i}]" for i in range(len(times)))]
    for i, t in enumerate(times):
        ms = int(max(t - 0.18, 0) * 1000)
        parts.append(f"[s{i}]adelay={ms}|{ms},volume={volume}[d{i}]")
    parts.append("[0:a]" + "".join(f"[d{i}]" for i in range(len(times))) +
                 f"amix=inputs={len(times) + 1}:duration=first:normalize=0[aout]")
    cmd = ["ffmpeg", "-y", "-i", input_path, "-i", str(sfx), "-filter_complex", ";".join(parts),
           "-map", "0:v", "-map", "[aout]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", output_path]
    subprocess.run(cmd, check=True, capture_output=True)
    return True


# ============================================================ smart thumbnail frame

def pick_best_frames(video_path: str, candidates: Sequence[float], top: int = 2) -> List[Dict]:
    """Nilai frame kandidat: ketajaman (Laplacian), eksposur, ukuran & posisi wajah. Return top-N."""
    import cv2
    import numpy as np

    try:
        from .mediapipe_compat import FaceDetectorCompat
        detector = FaceDetectorCompat(min_confidence=0.5)
    except Exception:
        detector = None
    cap = cv2.VideoCapture(video_path)
    scored = []
    try:
        for t in candidates:
            cap.set(cv2.CAP_PROP_POS_MSEC, max(t, 0) * 1000)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            h, w = frame.shape[:2]
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            sharp = float(cv2.Laplacian(gray, cv2.CV_64F).var())
            bright = float(gray.mean())
            exposure = 1.0 - min(abs(bright - 125) / 125, 1.0)
            face_score, face_box = 0.0, None
            if detector is not None:
                faces = detector.detect(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                if faces:
                    (fx, fy, fw, fh), conf = max(faces, key=lambda f: f[0][2] * f[0][3] * f[1])
                    area = (fw * fh) / float(w * h)
                    face_score = min(area / 0.06, 1.0) * conf
                    face_box = (fx / w, fy / h, fw / w, fh / h)
            score = min(sharp / 300.0, 1.0) * 0.45 + exposure * 0.2 + face_score * 0.35
            scored.append({"time": float(t), "score": round(score, 4), "face": face_box})
    finally:
        cap.release()
        if detector is not None:
            detector.close()
    scored.sort(key=lambda s: s["score"], reverse=True)
    picked: List[Dict] = []
    for item in scored:  # variasi: frame ke-2 minimal 1.5 detik dari frame pertama
        if all(abs(item["time"] - p["time"]) >= 1.5 for p in picked):
            picked.append(item)
        if len(picked) >= top:
            break
    return picked


def thumbnail_candidates(duration: float, peak: Optional[float], n: int = 14) -> List[float]:
    # hindari 2.6 dtk pertama (kartu hook) & 3 dtk terakhir (CTA follow)
    start = min(3.0, duration * 0.2)
    end = max(duration - 3.0, start + 0.5)
    times = [start + (end - start) * i / max(n - 1, 1) for i in range(n)]
    if peak is not None and start <= peak <= end:
        times += [max(peak + d, 0.2) for d in (-0.4, 0.0, 0.4)]
    return sorted({round(t, 2) for t in times if 0 < t < duration})


# ============================================================ text utils

_EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF️‍]+")


def strip_emoji(text: str) -> str:
    """Font default drawtext tidak punya glyph emoji (muncul kotak) — buang dari teks video."""
    return re.sub(r"\s{2,}", " ", _EMOJI_RE.sub("", str(text or ""))).strip()


# ============================================================ v6.4: penekanan kata

POWER_WORDS = {
    "rahasia", "ternyata", "penting", "gila", "wow", "serius", "bahaya", "jangan", "harus", "pertama", "terakhir",
    "gratis", "juta", "miliar", "ribu", "mati", "anjir", "anjay", "njir", "astaga", "buset", "waduh", "parah",
    "gokil", "mantap", "keren", "polisi", "kabur", "menang", "kalah", "uang", "duit", "never", "always", "secret",
    "truth", "insane", "crazy", "money", "free", "dead", "win", "lose",
}


def _word_score(raw: str) -> int:
    clean = _clean(raw)
    keywords = {w for words in config.VIRAL_KEYWORDS.values() for w in words if " " not in w}
    score = 0
    if clean in POWER_WORDS or clean in keywords:
        score += 3
    if raw.endswith("!"):
        score += 2
    if raw.endswith("?"):
        score += 1
    if re.search(r"\d", raw):
        score += 2
    if raw.isupper() and len(clean) > 2:
        score += 1
    return score


def power_word_set(words_rel: List[Dict], limit_ratio: float = 0.12) -> set:
    """Kata yang diberi warna aksen permanen di caption (±12% kata, yang paling 'berat')."""
    # v6.8: kata yang BENAR-BENAR ditekan pembicara (audio_signals: stress 2–3) ikut diberi warna aksen
    scored = sorted(((_word_score(w["word"]) + (2 * int(w.get("stress") or 0) - 1 if int(w.get("stress") or 0) >= 2 else 0),
                      _clean(w["word"])) for w in words_rel), reverse=True)
    cap = max(2, int(len(words_rel) * limit_ratio))
    return {c for s, c in scored[:cap] if s >= 2 and len(c) >= 3}


# ============================================================ v6.4: cold open (teaser + rewind)

REWIND_FRAMES = 22  # ±0.73 dtk pada 30 fps


def plan_cold_open(words_rel: List[Dict], duration: float, peaks_rel: Sequence[float] = (),
                   min_clip: float = 18.0) -> Optional[Tuple[float, float]]:
    """Pilih potongan 1.8–4.2 dtk paling 'nendang' di tengah/akhir klip untuk diputar di awal.

    Skor: kata penekanan + kalimat utuh + dekat puncak sinyal (lonjakan chat/suara).
    None bila klip terlalu pendek atau tidak ada kalimat yang cukup kuat."""
    if duration < min_clip:
        return None
    lo, hi = max(5.0, duration * 0.22), duration - 1.5
    best, best_score = None, 0.0
    n = len(words_rel)
    for i in range(n):
        ws = words_rel[i]["start"]
        if ws < lo or ws > hi:
            continue
        prev_gap = ws - words_rel[i - 1]["end"] if i else 9
        prev_end = i == 0 or str(words_rel[i - 1]["word"]).endswith((".", "!", "?", ","))
        clean_start = prev_gap > 0.25 or prev_end
        acc = 0
        for j in range(i, n):
            we = words_rel[j]["end"]
            span = we - ws
            if we > hi or span > 4.2:
                break
            acc += _word_score(words_rel[j]["word"])
            if span < 1.8:
                continue
            nxt_gap = (words_rel[j + 1]["start"] - we) if j + 1 < n else 9
            clean_end = str(words_rel[j]["word"]).endswith((".", "!", "?")) or nxt_gap > 0.3
            score = acc + (2.5 if clean_end else -2) + (1.5 if clean_start else -1.5)
            score += sum(6 for p in peaks_rel if ws - 3 <= p <= we + 1.5)
            if score > best_score:
                best, best_score = (max(ws - 0.08, 0), min(we + 0.12, duration)), score
    if best and best_score >= 6:
        return round(best[0], 3), round(best[1], 3)
    # tanpa kata yang kuat (mis. momen game tanpa omongan) — pakai puncak sinyal bila ada
    for p in sorted(peaks_rel):
        if lo + 2 <= p <= hi:
            return round(max(p - 3.0, lo), 3), round(min(p + 0.8, hi), 3)
    return None


def apply_cold_open(input_path: str, output_path: str, teaser: Tuple[float, float], encode_args: List[str]) -> float:
    """[teaser] → [rewind cepat bergaya VHS + suara kaset] → [klip utuh dari awal]. Return panjang tambahan.

    Tiap bagian dibaca sebagai input terpisah (bukan split) supaya FFmpeg tidak menyangga ribuan frame."""
    ts, te = teaser
    t_len = te - ts
    rw_len = REWIND_FRAMES / OUTPUT_FPS
    rw_fps = REWIND_FRAMES / max(te, 0.5)
    rew = sfx_path("rewind")
    graph = (
        f"[0:v]fps={OUTPUT_FPS},setpts=PTS-STARTPTS,format=yuv420p[tv];"
        f"[0:a]asetpts=PTS-STARTPTS,aformat=sample_rates=48000:channel_layouts=stereo,"
        f"afade=t=in:d=0.03,afade=t=out:st={max(t_len - 0.09, 0):.3f}:d=0.09[ta];"
        f"[1:v]fps={rw_fps:.4f},reverse,setpts=N/({OUTPUT_FPS}*TB),fps={OUTPUT_FPS},"
        f"eq=saturation=0.45:contrast=1.18:brightness=0.03,noise=alls=28:allf=t,rgbashift=rh=-9:bh=9,"
        f"format=yuv420p,trim=end_frame={REWIND_FRAMES}[rv];"
        f"[3:a]atrim=0:{rw_len:.3f},asetpts=PTS-STARTPTS,aformat=sample_rates=48000:channel_layouts=stereo[ra];"
        f"[2:v]fps={OUTPUT_FPS},setpts=PTS-STARTPTS,format=yuv420p[mv];"
        f"[2:a]asetpts=PTS-STARTPTS,aformat=sample_rates=48000:channel_layouts=stereo,afade=t=in:d=0.04[ma];"
        f"[tv][ta][rv][ra][mv][ma]concat=n=3:v=1:a=1[vout][aout]"
    )
    cmd = ["ffmpeg", "-y",
           "-ss", f"{ts:.3f}", "-t", f"{t_len:.3f}", "-i", input_path,
           "-t", f"{te:.3f}", "-i", input_path,
           "-i", input_path,
           "-i", str(rew),
           "-filter_complex", graph, "-map", "[vout]", "-map", "[aout]",
           *encode_args, "-c:a", "aac", "-b:a", "192k", "-ar", "48000", output_path]
    subprocess.run(cmd, check=True, capture_output=True)
    return t_len + rw_len


def cold_open_clip_info(clip_info: Dict, teaser: Tuple[float, float], shift: float) -> Dict:
    """Sesuaikan words ke timeline baru: kata teaser disalin ke awal, sisanya bergeser `shift` dtk."""
    start = float(clip_info["start"])
    ts, te = teaser
    words = []
    for w in clip_info.get("words") or []:
        rs, re_ = float(w.get("start", 0)) - start, float(w.get("end", 0)) - start
        if ts - 0.05 <= rs and re_ <= te + 0.15:
            words.append({**w, "start": start + max(rs - ts, 0), "end": start + min(re_, te) - ts})
    for w in clip_info.get("words") or []:
        words.append({**w, "start": float(w["start"]) + shift, "end": float(w["end"]) + shift})
    new = dict(clip_info)
    new["words"] = sorted(words, key=lambda w: w["start"])
    new["subtitle_segments"] = []
    old_dur = float(clip_info["end"]) - start
    new["end"] = start + shift + old_dur
    new["duration"] = shift + old_dur
    new["cold_open"] = {"teaser": [ts, te], "shift": round(shift, 3)}
    new["signal_peaks_rel"] = [p + shift for p in clip_info.get("signal_peaks_rel") or []]
    return new


# ============================================================ v6.4: pustaka SFX (disintesis FFmpeg)

_SFX_RECIPES = {
    # dentuman: sinus turun (90→~40 Hz) + semburan noise rendah
    "impact": ("aevalsrc='0.95*sin(2*PI*(95*t-38*t*t))*exp(-3.2*t)':s=48000:d=1.1",
               "anoisesrc=color=brown:duration=0.3:amplitude=0.9,lowpass=f=700,afade=t=out:st=0.01:d=0.28:curve=exp"),
    # riser: sweep naik + desis yang makin keras
    "riser": ("aevalsrc='0.32*sin(2*PI*(170*t+240*t*t))*pow(t/1.5,2)':s=48000:d=1.5",
              "anoisesrc=color=white:duration=1.5:amplitude=0.35,highpass=f=1800,afade=t=in:d=1.5:curve=qsin"),
    # pop: blip pendek seperti gelembung
    "pop": ("aevalsrc='0.8*sin(2*PI*(820*t-2600*t*t))*exp(-38*t)':s=48000:d=0.14", None),
    # record scratch (freeze frame)
    "scratch": ("aevalsrc='0.55*sin(2*PI*t*(520+2600*t))*exp(-5*t)*(0.6+0.4*sin(2*PI*31*t))':s=48000:d=0.45",
                "anoisesrc=color=white:duration=0.45:amplitude=0.5,bandpass=f=2200:w=1600,afade=t=out:st=0.05:d=0.4:curve=exp"),
    # beep sensor 1 kHz
    "beep": ("aevalsrc='0.42*sin(2*PI*1000*t)':s=48000:d=2", None),
    # rewind kaset: chirp turun bergetar + desis pita
    "rewind": ("aevalsrc='0.42*sin(2*PI*(1500*t-760*t*t))*(0.6+0.4*sin(2*PI*28*t))':s=48000:d=0.8",
               "anoisesrc=color=pink:duration=0.8:amplitude=0.4,bandpass=f=2600:w=1800,tremolo=f=22:d=0.7"),
}


def sfx_path(name: str) -> Path:
    """File SFX (WAV 48 kHz stereo) — disintesis sekali lalu disimpan di temp/."""
    if name == "whoosh":
        return whoosh_path()
    target = Path(config.TEMP_DIR) / f"sfx_{name}_v1.wav"
    with _SFX_LOCK:
        if not target.exists():
            tone, noise = _SFX_RECIPES[name]
            cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", tone]
            if noise:
                cmd += ["-f", "lavfi", "-i", noise, "-filter_complex",
                        "[0:a][1:a]amix=inputs=2:duration=longest:normalize=0,alimiter=limit=0.95[a]", "-map", "[a]"]
            cmd += ["-ar", "48000", "-ac", "2", str(target)]
            subprocess.run(cmd, check=True, capture_output=True)
    return target


def plan_sound_design(duration: float, emphasis: Sequence[Tuple[float, float]], stickers: Sequence[Dict],
                      peaks_rel: Sequence[float], cold_shift: float = 0.0, whoosh: bool = True) -> List[Tuple[float, str, float]]:
    """Daftar (detik, nama_sfx, volume). Volume rendah: SFX menghidupkan, tidak menutupi suara."""
    ev: List[Tuple[float, str, float]] = []
    if cold_shift:
        ev.append((cold_shift, "impact", 0.42))   # cerita dimulai setelah rewind
    else:
        ev.append((0.03, "impact", 0.38))         # hook
    peaks = [p for p in peaks_rel if cold_shift + 2.5 <= p <= duration - 1.0]
    if peaks:
        p = peaks[0]
        ev.append((max(p - 1.5, cold_shift + 0.3), "riser", 0.22))
        ev.append((p, "impact", 0.5))
    if whoosh:
        ev += [(max(s - 0.18, 0.05), "whoosh", 0.28) for s, _ in emphasis]
    ev += [(st["t"], "pop", 0.32) for st in stickers]
    ev.sort()
    out: List[Tuple[float, str, float]] = []
    for t, name, vol in ev:  # jangan tumpuk dua SFX dalam 0.35 dtk (kecuali riser→impact)
        if out and t - out[-1][0] < 0.35 and not (out[-1][1] == "riser"):
            continue
        out.append((t, name, vol))
    return out[:16]


def add_sound_design(input_path: str, output_path: str, events: Sequence[Tuple[float, str, float]],
                     mute: Sequence[Tuple[float, float]] = ()) -> bool:
    """Campur beberapa jenis SFX di titik waktunya (+ bisukan rentang `mute` lalu isi beep). Video di-copy."""
    events = [(t, n, v) for t, n, v in events if t >= 0][:16]
    mute = [(a, b) for a, b in mute if b > a][:20]
    events += [(a, "beep", 1.0) for a, _ in mute]
    beep_len = {round(a, 3): b - a for a, b in mute}
    if not events:
        return False
    names = sorted({n for _, n, _ in events})
    inputs: List[str] = []
    for n in names:
        inputs += ["-i", str(sfx_path(n))]
    parts, labels = [], []
    for k, n in enumerate(names):
        uses = [(i, t, v) for i, (t, nn, v) in enumerate(events) if nn == n]
        parts.append(f"[{k + 1}:a]asplit={len(uses)}" + "".join(f"[x{i}]" for i, _, _ in uses))
        for i, t, v in uses:
            ms = int(t * 1000)
            trim = f"atrim=0:{beep_len.get(round(t, 3), 0.3):.3f}," if n == "beep" else ""
            parts.append(f"[x{i}]{trim}adelay={ms}|{ms},volume={v}[d{i}]")
            labels.append(f"[d{i}]")
    src = "[0:a]"
    if mute:
        cond = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in mute)
        parts.append(f"[0:a]volume=0:enable='{cond}'[m0]")
        src = "[m0]"
    parts.append(src + "".join(labels) + f"amix=inputs={len(labels) + 1}:duration=first:normalize=0[aout]")
    cmd = ["ffmpeg", "-y", "-i", input_path, *inputs, "-filter_complex", ";".join(parts),
           "-map", "0:v", "-map", "[aout]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", output_path]
    subprocess.run(cmd, check=True, capture_output=True)
    return True


# ============================================================ v6.4: stiker emoji (pop)

STICKER_KEYWORDS: Dict[str, List[str]] = {
    "😂": ["wkwk", "wkwkwk", "haha", "hahaha", "ngakak", "kocak", "lucu", "lol", "awokwok"],
    "😱": ["anjir", "anjay", "njir", "astaga", "buset", "waduh", "gila", "kaget", "takut", "hah", "what"],
    "💀": ["mati", "mampus", "modar", "tewas", "dead", "rip", "game over"],
    "🔥": ["mantap", "keren", "gokil", "epic", "jago", "pro", "hebat", "dahsyat", "viral"],
    "💰": ["uang", "duit", "kaya", "juta", "miliar", "cuan", "money", "dollar", "profit"],
    "🚓": ["polisi", "police", "polis", "wanted", "ditangkap", "kabur"],
    "🔫": ["tembak", "nembak", "pistol", "senjata", "gun", "shoot"],
    "🚗": ["mobil", "car", "nyetir", "tabrak", "nabrak"],
    "😭": ["sedih", "nangis", "kasihan", "kecewa"],
    "🤯": ["ternyata", "rahasia", "fakta", "plot twist"],
    "❤️": ["cinta", "sayang", "pacar", "love"],
    "🤔": ["kenapa", "gimana", "masa", "kok"],
    "🏆": ["menang", "juara", "win", "winner", "rank"],
    "👀": ["liat", "lihat", "look", "ngintip"],
}


def plan_stickers(words_rel: List[Dict], duration: float, skip_until: float = 0.0, limit: int = 4,
                  min_spacing: float = 5.0) -> List[Dict]:
    """Emoji yang muncul di kata kunci/reaksi (maks 4, berjarak ≥5 dtk, tidak di 1 dtk pertama/terakhir)."""
    lookup = {}
    for emoji, kws in list(STICKER_KEYWORDS.items()) + list(getattr(config, "EMOJI_KEYWORDS", {}).items()):
        for k in kws:
            lookup.setdefault(k.lower(), emoji)
    hits = []
    for i, w in enumerate(words_rel):
        if w["start"] < max(1.0, skip_until) or w["start"] > duration - 1.5:
            continue
        clean = _clean(w["word"])
        two = f"{clean} {_clean(words_rel[i + 1]['word'])}" if i + 1 < len(words_rel) else ""
        emoji = lookup.get(two) or lookup.get(clean) or (lookup.get(re.sub(r"(.)\1+", r"\1", clean)) if len(clean) > 3 else None)
        if not emoji and re.fullmatch(r"(wk)+w?k?|(ha)+h?|(kw)+k?", clean or "-"):
            emoji = "😂"
        if emoji:
            hits.append((_word_score(w["word"]) + 1, w["start"], emoji))
    chosen: List[Tuple[float, str]] = []
    for _, t, emoji in sorted(hits, key=lambda h: (-h[0], h[1])):
        if all(abs(t - ct) >= min_spacing for ct, _ in chosen):
            chosen.append((t, emoji))
        if len(chosen) >= limit:
            break
    return [{"t": round(t, 2), "emoji": e, "dur": 1.3} for t, e in sorted(chosen)]


def emoji_png(emoji: str, px: int = 220) -> Optional[str]:
    """Render emoji berwarna (Segoe UI Emoji / Noto Color) ke PNG transparan + bayangan lembut."""
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    code = "-".join(f"{ord(c):x}" for c in emoji)
    target = Path(config.TEMP_DIR) / f"emoji_{code}_{px}.png"
    if target.exists():
        return str(target)
    fonts = [Path(config.FONT_DIR) / "seguiemj.ttf", Path("C:/Windows/Fonts/seguiemj.ttf"),
             Path("/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf")]
    font_path = next((p for p in fonts if p.exists()), None)
    if not font_path:
        return None
    try:
        size = 109 if "Noto" in font_path.name else int(px * 0.62)  # NotoColorEmoji hanya bitmap 109px
        font = ImageFont.truetype(str(font_path), size)
        canvas = Image.new("RGBA", (px, px), (0, 0, 0, 0))
        ImageDraw.Draw(canvas).text((px / 2, px / 2), emoji, font=font, embedded_color=True, anchor="mm")
        box = canvas.getbbox()
        if not box:
            return None
        glyph = canvas.crop(box)
        scale = (px * 0.82) / max(glyph.size)
        glyph = glyph.resize((max(1, int(glyph.width * scale)), max(1, int(glyph.height * scale))), Image.LANCZOS)
        out = Image.new("RGBA", (px, px), (0, 0, 0, 0))
        shadow = Image.new("RGBA", glyph.size, (0, 0, 0, 150))
        shadow.putalpha(glyph.split()[3].point(lambda a: int(a * 0.55)))
        ox, oy = (px - glyph.width) // 2, (px - glyph.height) // 2
        out.paste(shadow, (ox + 6, oy + 10), shadow)
        out = out.filter(ImageFilter.GaussianBlur(5))
        out.alpha_composite(glyph, (ox, oy))
        out.save(target)
        return str(target)
    except Exception as exc:
        logger.debug(f"Emoji {emoji!r} gagal dirender: {exc}")
        return None


def sticker_anim_filter(t0: float, size: int) -> str:
    """Skala 'pop': 0.3 → 1.15 (0.09 dtk) → 1.0 (0.2 dtk) → goyang halus. Kanvas tetap agar overlay stabil."""
    canvas = int(size * 1.3) // 2 * 2
    f = (f"if(lt(t,{t0:.2f}),0.3,if(lt(t-{t0:.2f},0.09),0.3+(t-{t0:.2f})*9.4,"
         f"if(lt(t-{t0:.2f},0.2),1.15-(t-{t0:.2f}-0.09)*1.36,1+0.035*sin(10*(t-{t0:.2f})))))")
    return (f"scale=w='trunc(max(4,{size}*{f})/2)*2':h=-2:eval=frame,"
            f"pad=w={canvas}:h={canvas}:x=(ow-iw)/2:y=(oh-ih)/2:color=0x00000000:eval=frame")


# ============================================================ v6.4: mastering audio

def master_audio(input_path: str, output_path: str, target_lufs: float = -14.0) -> bool:
    """Loudness dua tahap (EBU R128) ke -14 LUFS, true peak -1.2 dB. Video di-copy."""
    probe_cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", input_path, "-vn",
                 "-af", f"loudnorm=I={target_lufs}:TP=-1.2:LRA=9:print_format=json", "-f", "null", "-"]
    proc = subprocess.run(probe_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    match = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", proc.stderr or "")
    if proc.returncode != 0 or not match:
        return False
    import json as _json
    m = _json.loads(match.group(0))
    try:
        if float(m["input_i"]) < -60:  # nyaris sunyi — jangan dipaksa naik
            return False
    except (KeyError, ValueError):
        return False
    af = (f"loudnorm=I={target_lufs}:TP=-1.2:LRA=9:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
          f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true,"
          "alimiter=limit=0.9:level=false")
    cmd = ["ffmpeg", "-y", "-i", input_path, "-map", "0:v?", "-map", "0:a", "-c:v", "copy", "-af", af,
           "-ar", "48000", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", output_path]
    subprocess.run(cmd, check=True, capture_output=True)
    return True


# ============================================================ v6.4: freeze frame (maksimal)

FREEZE_SEC = 0.7


def plan_freeze(peaks_rel: Sequence[float], emphasis: Sequence[Tuple[float, float]], duration: float,
                skip_until: float = 0.0) -> Optional[float]:
    """Satu titik freeze di puncak terkuat (lonjakan chat/tawa), atau penekanan terkuat bila tidak ada."""
    lo, hi = max(skip_until + 2.0, 3.0), duration - 3.0
    for p in peaks_rel:
        if lo <= p <= hi:
            return round(p, 3)
    for s, e in emphasis:
        if lo <= e <= hi:
            return round(e - 0.1, 3)
    return None


def apply_freeze(input_path: str, output_path: str, at: float, encode_args: List[str]) -> float:
    """[0..at] → frame `at` dibekukan FREEZE_SEC dtk (+ record scratch) → [at..akhir]. Return tambahan dtk."""
    n = int(round(FREEZE_SEC * OUTPUT_FPS))
    graph = (
        f"[0:v]fps={OUTPUT_FPS},setpts=PTS-STARTPTS,format=yuv420p[a];"
        f"[0:a]asetpts=PTS-STARTPTS,aformat=sample_rates=48000:channel_layouts=stereo,afade=t=out:st={max(at - 0.06, 0):.3f}:d=0.06[aa];"
        f"[1:v]fps={OUTPUT_FPS},trim=end_frame=1,loop=loop={n - 1}:size=1:start=0,setpts=N/({OUTPUT_FPS}*TB),"
        f"eq=saturation=0.75:contrast=1.08,format=yuv420p[f];"
        f"[3:a]atrim=0:{FREEZE_SEC:.3f},apad=whole_dur={FREEZE_SEC:.3f},asetpts=PTS-STARTPTS,"
        f"aformat=sample_rates=48000:channel_layouts=stereo[fa];"
        f"[2:v]fps={OUTPUT_FPS},setpts=PTS-STARTPTS,format=yuv420p[c];"
        f"[2:a]asetpts=PTS-STARTPTS,aformat=sample_rates=48000:channel_layouts=stereo,afade=t=in:d=0.05[ca];"
        f"[a][aa][f][fa][c][ca]concat=n=3:v=1:a=1[vout][aout]"
    )
    cmd = ["ffmpeg", "-y", "-t", f"{at:.3f}", "-i", input_path, "-ss", f"{at:.3f}", "-i", input_path,
           "-ss", f"{at:.3f}", "-i", input_path, "-i", str(sfx_path("scratch")),
           "-filter_complex", graph, "-map", "[vout]", "-map", "[aout]",
           *encode_args, "-c:a", "aac", "-b:a", "192k", "-ar", "48000", output_path]
    subprocess.run(cmd, check=True, capture_output=True)
    return FREEZE_SEC


def freeze_clip_info(clip_info: Dict, at: float, extra: float) -> Dict:
    """Kata setelah titik freeze bergeser `extra` dtk."""
    start = float(clip_info["start"])
    new = dict(clip_info)
    new["words"] = [{**w, "start": float(w["start"]) + (extra if float(w["start"]) - start >= at else 0),
                     "end": float(w["end"]) + (extra if float(w["start"]) - start >= at else 0)}
                    for w in clip_info.get("words") or []]
    new["subtitle_segments"] = []
    new["end"] = float(clip_info["end"]) + extra
    new["duration"] = new["end"] - start
    new["freeze"] = {"at": at, "dur": extra}
    new["signal_peaks_rel"] = [p + (extra if p >= at else 0) for p in clip_info.get("signal_peaks_rel") or []]
    return new


# ============================================================ v6.4: sensor kata kasar

PROFANITY = {
    "anjing", "anjeng", "anjg", "njing", "njeng", "bangsat", "bajingan", "kontol", "kntl", "memek", "ngentot", "ngewe",
    "entot", "jancok", "jancuk", "dancok", "asu", "pepek", "peler", "tai", "taik", "goblog",
    "fuck", "fucking", "fucked", "motherfucker", "shit", "bitch", "dick", "pussy", "asshole",
}


def _is_profane(word: str) -> bool:
    c = _clean(word)
    return bool(c) and (c in PROFANITY or c.startswith("fuck") or c.startswith("ngentot") or c.startswith("kontol"))


def censor_words(clip_info: Dict) -> Tuple[Dict, List[Tuple[float, float]]]:
    """Samarkan kata kasar di caption (a****g) dan kembalikan rentang relatif untuk dibisukan + beep."""
    start = float(clip_info["start"])
    spans, words = [], []
    for w in clip_info.get("words") or []:
        raw = str(w.get("word", ""))
        if _is_profane(raw):
            core = re.sub(r"[^\w]", "", raw, flags=re.UNICODE)
            masked = core[0] + "*" * max(len(core) - 2, 1) + (core[-1] if len(core) > 2 else "")
            words.append({**w, "word": raw.replace(core, masked)})
            spans.append((max(float(w["start"]) - start - 0.03, 0), float(w["end"]) - start + 0.03))
        else:
            words.append(w)
    if not spans:
        return clip_info, []
    new = dict(clip_info)
    new["words"] = words
    merged: List[Tuple[float, float]] = []
    for a, b in sorted(spans):
        if merged and a - merged[-1][1] < 0.08:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return new, merged


# ============================================================ v6.4: lower third (nama pembicara)

def lower_third_filters(items: Sequence[Tuple[float, str, str]], text_dir: Path, font: str, escape,
                        y_frac: float = 0.66) -> Tuple[List[str], List[str]]:
    """Kartu nama geser-masuk dari kiri (2.8 dtk). items: [(t, nama, keterangan)]. Return (filter, file_teks)."""
    filters, files = [], []
    for k, (t0, name, sub) in enumerate(items[:3]):
        t1 = t0 + 2.8
        nf = Path(text_dir) / f"_lt_name_{k}.txt"
        nf.write_text(strip_emoji(name)[:40], encoding="utf-8")
        files.append(str(nf))
        slide = f"if(lt(t,{t0:.2f}+0.28),-w*0.6+(t-{t0:.2f})/0.28*(w*0.6+64),64)"
        fade = f"alpha='if(gt(t,{t1 - 0.3:.2f}),max(0,({t1:.2f}-t)/0.3),1)'"
        enable = f"enable='between(t,{t0:.2f},{t1:.2f})'"
        filters.append(f"drawbox=x=40:y=ih*{y_frac:.3f}-10:w=10:h=ih/20+ih/36:color=0xFF3B6B@0.95:t=fill:"
                       f"enable='between(t,{t0 + 0.25:.2f},{t1 - 0.2:.2f})'")
        filters.append(f"drawtext={font}textfile='{escape(str(nf))}':expansion=none:x='{slide}':y=h*{y_frac:.3f}:"
                       f"fontsize=h/26:fontcolor=white:box=1:boxcolor=black@0.62:boxborderw=16:{fade}:{enable}")
        if sub:
            sf = Path(text_dir) / f"_lt_sub_{k}.txt"
            sf.write_text(strip_emoji(sub)[:48], encoding="utf-8")
            files.append(str(sf))
            filters.append(f"drawtext={font}textfile='{escape(str(sf))}':expansion=none:x='{slide}':y=h*{y_frac:.3f}+h/19+8:"
                           f"fontsize=h/52:fontcolor=0xEDEDED:box=1:boxcolor=black@0.5:boxborderw=10:{fade}:{enable}")
    return filters, files
