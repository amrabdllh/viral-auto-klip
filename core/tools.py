"""
Handler job video & tool AI untuk Studio v6.

Job (antrean, lewat core/jobs.py):
  clip_quick, clip_manual, split_cam, highlight_bola, konten_tempel, merge, loop, wa_story,
  editor, transcript, tts, music, sfx
Sinkron (langsung dijawab endpoint): optimize_title, generate_prompts, viral_suggestions,
  assistant_chat, viral_analytics, thumbnail_download
"""
from __future__ import annotations

import json
import math
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger

import config
from . import ai_providers as ai
from . import db, ffx, media_ai, sources
from .creator import apply_brand
from .jobs import JobContext, handler

# ============================================================================ helper umum


def resolve_source(ctx: JobContext, params: Dict, key: str = "") -> str:
    """Path video lokal dari params: path (upload/aset) atau url (diunduh ke folder job)."""
    path = params.get(f"{key}path" if key else "path") or ""
    url = params.get(f"{key}url" if key else "url") or ""
    if path:
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"File tidak ditemukan: {p.name}")
        return str(p)
    if not url:
        raise ValueError("Masukkan link video atau upload file")
    from .utils import SOURCE_CACHE_DIR, download_video
    ctx.progress(4, "Download", "Mengunduh video sumber (yt-dlp)…")
    src_dir = ctx.dir / "source"
    src_dir.mkdir(exist_ok=True)
    last = {"t": 0.0, "pct": -1.0}

    def on_progress(pct: float, speed: float, eta) -> None:
        now = time.monotonic()
        if pct < 100 and pct - last["pct"] < 1.0 and now - last["t"] < 2.0:
            return
        last["t"], last["pct"] = now, pct
        mb = (speed or 0) / 1_000_000
        eta_txt = f"sisa {int(eta)} dtk" if eta else "sisa ?"
        ctx.progress(4 + int(max(0.0, min(pct, 100.0)) * 0.14), "Download",
                     f"{pct:.0f}% · {mb:.1f} MB/s · {eta_txt}")

    raw_height = params.get("source_max_height")
    try:
        max_height = int(raw_height) if raw_height not in (None, "") else None
    except (TypeError, ValueError):
        max_height = None
    video = download_video(url, src_dir, cookies_from_browser=params.get("cookies_browser", ""),
                           progress_cb=on_progress, cache_dir=SOURCE_CACHE_DIR, max_height=max_height)
    ctx.log(f"Sumber: {Path(video).name}")
    return video


def apply_ai_params(params: Dict) -> bool:
    from .analyzer import configure_llm_clients
    provider = params.get("ai_provider") or config.AI_PROVIDER
    return configure_llm_clients(provider=provider, model=params.get("ai_model") or "",
                                 api_key=params.get("ai_api_key") or "", base_url=params.get("ai_base_url") or "")


def whisper_device(params: Dict) -> tuple:
    dev = params.get("whisper_device") or config.WHISPER_DEVICE or "auto"
    compute = "cloud" if dev in ("groq", "elevenlabs") else (params.get("whisper_compute_type") or config.WHISPER_COMPUTE_TYPE)
    return dev, compute


def transcribe(ctx: JobContext, video: str, params: Dict, pct: int = 20) -> tuple:
    from .transcriber import transcribe_video
    if params.get("language") is not None:
        config.WHISPER_LANGUAGE = str(params.get("language") or "").strip().lower()
    dev, compute = whisper_device(params)
    engine = {"elevenlabs": f"ElevenLabs Scribe ({config.ELEVENLABS_STT_MODEL})", "groq": "Groq Whisper (cloud)"}.get(
        dev, f"Whisper {params.get('whisper_model') or config.WHISPER_MODEL} ({'CPU' if dev == 'cpu' else 'GPU'})")
    ctx.progress(pct, "Transkripsi", f"{engine} mentranskrip ucapan…")
    return transcribe_video(video, model_name=params.get("whisper_model") or config.WHISPER_MODEL,
                            word_timestamps=True, device=dev, compute_type=compute)


def register_clips(ctx: JobContext, clips: List[Dict], source: str) -> None:
    for c in clips:
        meta = {k: c.get(k) for k in ("score", "category", "hook_text", "ai_title", "upload_caption", "hashtags",
                                       "duration", "thumbnail", "reason", "fyp_grade")}
        meta["source"] = source
        meta["title"] = c.get("ai_title") or c.get("hook_text") or Path(c["file_path"]).stem
        c["asset_id"] = ctx.asset("clip", c["file_path"], name=meta["title"], meta=meta)["id"]


def brand_clips(ctx: JobContext, clips: List[Dict], force: Optional[bool]) -> None:
    for c in clips:
        try:
            src = c["file_path"]
            tmp = str(Path(src).with_name("_branded.mp4"))
            out = apply_brand(src, tmp, force=force)
            if out != src:
                Path(out).replace(src)
                c["duration"] = round(ffx.probe(src)["duration"], 2)  # penutup klip menambah durasi
        except Exception as exc:
            ctx.log(f"Logo Brand Kit gagal di {Path(c['file_path']).name}: {exc}")


def enforce_clip_duration(clips: List[Dict], *, tolerance: float = 0.75,
                          max_available: float = 0.0) -> List[Dict]:
    """Buang kandidat di luar MIN/MAX_CLIP_DURATION; panjangkan yang terlalu pendek bila bisa.

    AI kadang mengembalikan momen lebih pendek dari batas (mis. 12 dtk padahal
    minimum 15). Perilaku sekarang:
      1. kandidat >= minimum - tolerance  -> lolos apa adanya
      2. kandidat pendek -> `end` digeser sampai minimum, selama masih muat di
         sumber (max_available) dan tidak melewati maksimum
      3. kalau tetap tidak memenuhi -> DIBUANG (jangan kirim klip di bawah standar)

    Catatan: tidak ada lagi fallback "ambil yang terpanjang" — fallback itulah
    yang dulu meloloskan klip 12 dtk saat minimum 15 dtk.
    """
    lo, hi = float(config.MIN_CLIP_DURATION), float(config.MAX_CLIP_DURATION)

    def dur(c: Dict) -> float:
        try:
            return float(c.get("end") or 0) - float(c.get("start") or 0)
        except (TypeError, ValueError):
            return 0.0

    kept: List[Dict] = []
    for c in clips:
        d = dur(c)
        if d <= 0:
            continue
        if d < lo - tolerance:
            try:
                start = float(c.get("start") or 0)
            except (TypeError, ValueError):
                continue
            target = lo
            if max_available and max_available > 0:
                # Jangan melewati ujung rekaman.
                target = min(target, float(max_available) - start)
            if target >= lo - tolerance and target > d:
                fixed = dict(c)
                fixed["end"] = round(start + min(target, hi), 3)
                fixed["duration_extended"] = True
                c, d = fixed, dur(fixed)
        if lo - tolerance <= d <= hi + tolerance:
            kept.append(c)
    return kept


def render_options(params: Dict) -> Dict:
    aspect = params.get("output_type", "Vertical")
    return dict(
        style_name=params.get("caption_style") or "viral",  # sama dengan default form UI
        caption_overrides={k: params[k] for k in ("font_size", "vertical_position") if params.get(k)},
        enable_face_reframe=params.get("enable_face_reframe", True),
        add_watermark=bool(params.get("watermark_text")),
        target_platform=params.get("target_platform", "Semua"),
        enable_captions=params.get("enable_captions", True),
        conversation_mode=params.get("video_mode") in ("podcast", "auto"),
        group_framing=aspect == "Fit" or params.get("video_mode") == "group",
        target_aspect="landscape" if aspect == "Wide" else "portrait",
        split_screen=bool(params.get("split_screen")),
        klipers_reframe=params.get("klipers_reframe", True),
        auto_zoom=params.get("auto_zoom", True),
        enable_audio_enhance=params.get("audio_enhance", True),
        edit_opts={k: params.get(k) for k in ("jump_cuts", "dynamic_zoom", "progress_bar", "sfx", "color_grade",
                                               "smart_thumbnail", "end_cta", "cold_open", "sound_design",
                                               "caption_fx", "audio_master", "speaker_cam", "broll", "shake", "freeze",
                                               "lower_third", "censor", "pro_fx", "auto_tempo")} | {"style": params.get("edit_style", "")},
        # v6.4: "auto" = facecam streamer terdeteksi → split wajah/gameplay; podcast/grup tetap reframe wajah
        layout=params.get("layout") or ("face" if params.get("video_mode") in ("podcast", "group") else "auto"),
        lower_third=params.get("_lower_third") or None,
    )


def build_lower_third(params: Dict, transcript: List[Dict], default_name: str = "", subtitle: str = "") -> Dict:
    """Nama untuk kartu lower third: isian 'Nama pembicara' (urut sesuai siapa yang bicara duluan
    di video) atau nama channel. Label pembicara Scribe dipetakan ke nama sesuai urutan bicara."""
    names = [n.strip() for n in re.split(r"[,;\n]", str(params.get("speaker_names") or "")) if n.strip()]
    if not names and default_name:
        names = [default_name]
    order: List[str] = []
    for seg in transcript or []:
        for w in seg.get("words") or []:
            spk = w.get("speaker")
            if spk and spk not in order:
                order.append(spk)
    by_speaker = {spk: names[i] for i, spk in enumerate(order[:len(names)])} if len(names) >= 2 else {}
    return {"names": names, "by_speaker": by_speaker, "subtitle": subtitle} if names else {}


def filter_streamer_segments(segments: List[Dict], context: str = "") -> List[Dict]:
    """Buang dialog game dari caption. LLM memilah segmen yang benar-benar diucapkan streamer.

    Audio siaran mencampur mikrofon streamer + suara game. Whisper bisa ikut menulis
    dialog game, bahkan menerjemahkannya ke Bahasa Indonesia saat tidak ada ucapan
    streamer di jendela itu. Filter kata fungsi (drop_foreign_segments) tidak bisa
    menangkap versi terjemahan, jadi LLM memilah berdasarkan isi/kalimat.
    """
    if not segments:
        return segments
    if len(segments) > 50:
        # Transkrip panjang (live window / VOD): panggil per potongan agar jawaban
        # LLM tidak terpotong dan setiap segmen tetap punya nomor yang jelas.
        kept: List[Dict] = []
        for start in range(0, len(segments), 40):
            kept.extend(filter_streamer_segments(segments[start:start + 40], context))
        return kept
    lines = []
    for index, seg in enumerate(segments, 1):
        text = " ".join(str(seg.get("text") or "").split())
        if text:
            lines.append(f"{index}. [{float(seg.get('start') or 0):.1f}-{float(seg.get('end') or 0):.1f}] {text}")
    if not lines:
        return segments
    prompt = (
        "Kamu memilah transkrip klip streamer Indonesia yang sedang bermain game. "
        "Audio klip mencampur mikrofon streamer dengan suara game, jadi dialog karakter game "
        "(bahasa Inggris) bisa ikut tertranskripsi atau diterjemahkan ke Bahasa Indonesia.\n"
        "Pilih HANYA nomor segmen yang benar-benar diucapkan streamer. Buang dialog game/NPC.\n"
        "Ciri dialog game: formal, terjemahan kaku, membahas toko/misi/karakter/instruksi. "
        "Ciri streamer: bahasa gaul, reaksi, menyapa penonton, komentar bebas.\n"
        f"Konteks klip: {context[:300] or '-'}\n"
        "Balas HANYA JSON: {\"keep\": [nomor segmen], \"reason\": \"singkat\"}.\n"
        "Kalau tidak ada ucapan streamer sama sekali, keep: [].\n\n"
        "Transkrip:\n" + "\n".join(lines)
    )
    try:
        raw = ai.complete(prompt, max_tokens=min(3000, 300 + 12 * len(segments)), json_mode=True)
        data = ai.parse_json(raw)
        keep = data.get("keep")
        if not isinstance(keep, list):
            return segments
        picked, seen = [], set()
        for value in keep:
            try:
                index = int(value)
            except (TypeError, ValueError):
                continue
            if 1 <= index <= len(segments) and index not in seen:
                seen.add(index)
                picked.append(segments[index - 1])
        logger.info(f"Filter dialog game: {len(segments)} → {len(picked)} segmen "
                    f"({str(data.get('reason') or '')[:120]})")
        return picked
    except Exception as exc:  # noqa: BLE001 - filter opsional, jangan gagalkan render
        logger.warning(f"Filter dialog game gagal ({str(exc)[:120]}); memakai semua segmen")
        return segments


def _source_title(params: Dict, video: str) -> str:
    """Judul video sumber (konteks bagi Editor-AI); nama file bila video lokal."""
    url = params.get("url") or ""
    if url:
        try:
            return str(sources.video_info(url).get("title") or "")
        except Exception:
            pass
    return Path(video).stem


_GAMING_RE = re.compile(r"\b(gta|grand theft auto|minecraft|roblox|valorant|mobile legends?|mlbb|pubg|free ?fire|"
                        r"fortnite|genshin|elden ring|resident evil|horror game|gameplay|let'?s play|walkthrough|"
                        r"live ?stream|mabar|speedrun|ranked|push rank|playthrough|ps5|nintendo|steam|\bgame\b)", re.I)


def content_type(params: Dict, title: str, url: str = "") -> str:
    """v6.8: tebak tipe konten untuk menyesuaikan kriteria momen. 'gaming_stream' bila judul/link menunjukkan
    gaming atau siaran live (youtube.com/live/…), selain itu kosong (obrolan/podcast)."""
    if params.get("content_type"):
        return str(params["content_type"])
    if _GAMING_RE.search(title or "") or "/live/" in (url or ""):
        return "gaming_stream"
    return ""


def source_channel(url: str) -> str:
    """Nama channel/uploader sumber (untuk lower third). Kosong bila gagal."""
    if not url:
        return ""
    try:
        info = sources.video_info(url)
        return str(info.get("channel") or info.get("uploader") or "")
    except Exception:
        return ""


def _publish(ctx: JobContext, clips: List[Dict], params: Dict) -> Optional[str]:
    if not params.get("publish_package", True) or not clips:
        return None
    try:
        from .publisher import build_publish_package
        platforms = [params["target_platform"]] if params.get("target_platform", "Semua") != "Semua" else None
        return str(build_publish_package(clips, ctx.dir, platforms=platforms))
    except Exception as exc:
        ctx.log(f"Paket publish gagal: {exc}")
        return None


@handler("fetch_source")
def fetch_source(ctx: JobContext) -> Dict:
    """Unduh video dari link agar bisa dipratinjau di editor (Atur Manual / Split Cam / Konten Tempel)."""
    video = resolve_source(ctx, ctx.params)
    info = ffx.probe(video)
    a = ctx.asset("source", video, name=Path(video).stem, meta={"duration": info["duration"], "width": info["width"],
                                                                "height": info["height"], "url": ctx.params.get("url")})
    return {"_message": "Video siap dipratinjau", "file": video, "asset_id": a["id"], **info}


# ============================================================================ Analisis AI (Quick Process)

@handler("clip_quick")
def clip_quick(ctx: JobContext) -> Dict:
    from .analyzer import analyze_viral_potential
    from .clipper import process_clips
    from .utils import get_video_info
    from . import viral_standard

    p = ctx.params

    def _auto(v) -> bool:  # isian dikosongkan / "auto" = AI yang menentukan (v6.8)
        return v is None or str(v).strip().lower() in ("", "0", "auto", "otomatis")
    farm = str(p.get("num_clips") or "").strip().lower() in ("farm", "farming")  # v6.8: lebih banyak klip
    auto_n, auto_score = _auto(p.get("num_clips")) or farm, _auto(p.get("min_score"))
    auto_dur = _auto(p.get("min_duration")) and _auto(p.get("max_duration"))
    if p.get("viral_standard", True) and not auto_n:
        p = viral_standard.apply_to_params(p)   # skill make-clip: 15-45 dtk, skor 55
    # Subtitle provider dari form (opsional): "none" mematikan caption; cloud memakai engine cloud.
    subtitle_provider = str(p.get("subtitle_provider") or "").lower()
    if subtitle_provider in ("none", "off", "tanpa"):
        p["enable_captions"] = False
    elif subtitle_provider == "whisper" and not p.get("whisper_device"):
        p["whisper_device"] = "auto"
    elif subtitle_provider in ("elevenlabs", "scribe", "elevenlabs-scribe", "groq", "cloud", "groq-whisper") \
            and not p.get("whisper_device"):
        p["whisper_device"] = subtitle_provider
    # skor minimum otomatis: gerbang kandidat dilonggarkan, kualitas akhirnya disaring Editor-AI
    config.MIN_VIRAL_SCORE = 45.0 if auto_score else max(0.0, min(float(p.get("min_score", 55)), 100.0))
    config.MIN_CLIP_DURATION = int(config.AUTO_MIN_DURATION) if _auto(p.get("min_duration")) else max(5, int(p.get("min_duration", 15)))
    config.MAX_CLIP_DURATION = max(config.MIN_CLIP_DURATION + 5, int(config.AUTO_MAX_DURATION) if _auto(p.get("max_duration")) else int(p.get("max_duration", 60)))
    num = 0 if auto_n else max(1, min(int(p.get("num_clips", 8)), 40))
    auto_mode = ("farm" if farm else "quality") if auto_n else False
    config.MAX_CLIPS_PER_VIDEO = num or int(config.AUTO_MAX_CLIPS)
    if p.get("watermark_text"):
        config.WATERMARK_TEXT = p["watermark_text"]
    llm = apply_ai_params(p) and p.get("use_llm", True)
    # v6.4: link siaran yang MASIH live tidak bisa diunduh utuh → otomatis pindah ke Mode Live
    if p.get("url") and not p.get("path") and re.search(r"youtu\.?be", p["url"]):
        from . import jobs, live
        ctx.progress(2, "Cek sumber", "Memeriksa apakah video sedang live…")
        if live.live_status(p["url"]) == "is_live":
            child = jobs.submit("live_clip", {**p, "url": p["url"]}, title="Klip Live")
            ctx.log(f"Video masih live → Mode Live dimulai (job {child['id']})")
            return {"_message": "Video ini masih live — Mode Live dimulai, klip muncul tiap ±10 menit",
                    "live_job": child["id"], "clips": [], "total_clips": 0}
    video = resolve_source(ctx, p)
    info = get_video_info(video)
    info["title"] = _source_title(p, video)
    info["content_type"] = content_type(p, info["title"], p.get("url", ""))
    if info["content_type"] == "gaming_stream":
        ctx.log("Tipe konten: live/gaming → momen reaksi & kejadian game diutamakan, kandidat dari puncak suara ikut dinilai")
        if _auto(p.get("min_duration")):
            config.MIN_CLIP_DURATION = max(config.MIN_CLIP_DURATION, 22)  # reaksi butuh pengantar
    if auto_n:  # batas atas sesuai panjang video: ±1 klip per 4,5 menit (video 4 jam → hingga 30 klip)
        minutes = float(info.get("duration") or 0) / 60
        per, cap = ((config.AUTO_FARM_MINUTES_PER_CLIP, config.AUTO_FARM_MAX_CLIPS) if farm
                    else (config.AUTO_MINUTES_PER_CLIP, config.AUTO_MAX_CLIPS))
        num = int(max(config.AUTO_MIN_CLIPS, min(cap, math.ceil(minutes / per))))
        config.MAX_CLIPS_PER_VIDEO = num
        ctx.log(f"Mode otomatis{' farming' if farm else ''}: video {minutes:.0f} menit → hingga {num} klip (jumlah akhir ditentukan kualitas)"
                + (f", durasi {config.MIN_CLIP_DURATION}–{config.MAX_CLIP_DURATION} dtk" if auto_dur else ""))
    transcript, tinfo = transcribe(ctx, video, p, 18)
    if p.get("streamer_only", True) and transcript:
        # Caption/analisis hanya dari ucapan streamer, bukan dialog game.
        from .transcriber import drop_foreign_segments
        transcript = drop_foreign_segments(transcript, str(p.get("language") or config.WHISPER_LANGUAGE or "id"))
    # Nama channel bukan nama pembicara: di podcast ia menempel pada tamu/host yang sedang tampil.
    # Kartu nama hanya muncul bila pengguna mengisi "Nama pembicara".
    p["_lower_third"] = build_lower_third(p, transcript)
    ctx.progress(42, "Analisis AI", f"{config.AI_PROVIDER}/{config.LLM_MODEL} mencari momen viral…" if llm else "Skor lokal (AI nonaktif)")
    clips_data = analyze_viral_potential(transcript, info, use_llm=llm, video_path=video, auto_count=auto_mode)
    clips_data = enforce_clip_duration(clips_data, max_available=float(info.get("duration") or 0))
    if p.get("categories"):
        clips_data = [c for c in clips_data if c.get("category") in p["categories"]]
    if p.get("keyword"):
        kw = p["keyword"].lower()
        boosted = [c for c in clips_data if kw in (c.get("transcript") or c.get("text", "")).lower()]
        clips_data = boosted + [c for c in clips_data if c not in boosted]
    else:  # v6.8: tanpa kata kunci → momen yang menyebut topik yang SEDANG naik diutamakan otomatis
        try:
            from . import trends
            hits = trends.boost_trending(clips_data)
            if hits:
                ctx.log(f"Topik lagi naik disebut di {len(hits)} klip: {', '.join(sorted(set(hits)))[:120]}")
        except Exception as exc:
            logger.warning(f"Boost topik tren dilewati: {str(exc)[:120]}")
    if not clips_data:
        raise RuntimeError("Tidak ada momen yang lolos skor minimum — turunkan Min Score atau perpendek durasi")
    if p.get("hook_card") is False:
        for c in clips_data:
            c["no_hook_card"] = True
    n_render = len(clips_data) if auto_n else min(len(clips_data), num)
    selected = clips_data[:n_render]
    # Metadata eksplisit dari form (opsional): override judul/deskripsi/hashtag AI.
    title_override = str(p.get("title_override") or "").strip()
    description_override = str(p.get("description_override") or "").strip()
    hashtags_override = str(p.get("hashtags_override") or "").strip()
    tags_override: List[str] = []
    if hashtags_override:
        for tag in re.split(r"[\s,]+", hashtags_override):
            clean = "#" + re.sub(r"[^\w]", "", tag.lstrip("#"), flags=re.UNICODE)
            if len(clean) > 1 and clean.lower() not in {t.lower() for t in tags_override}:
                tags_override.append(clean)
    from .utils import trim_caption
    for c in selected:
        if title_override:
            c["ai_title"] = title_override[:95]
        if description_override:
            c["upload_caption"] = trim_caption(description_override)
        if tags_override:
            c["hashtags"] = tags_override[:config.MAX_HASHTAGS]
    if p.get("streamer_only", True):
        # Filter per klip (transkrip 15 menit terlalu panjang untuk satu panggilan LLM):
        # buang dialog game dari caption, biarkan kategori & durasi dari analyzer.
        # Tetap jalan walau pemilihan momen memakai skor lokal; gagal → segmen apa adanya.
        for c in selected:
            segs = c.get("subtitle_segments") or []
            if not segs:
                continue
            try:
                kept = filter_streamer_segments(
                    segs, context=str(c.get("ai_title") or c.get("hook_text") or info.get("title") or ""))
                c["subtitle_segments"] = kept
                c["words"] = [w for s in kept for w in (s.get("words") or [])]
            except Exception:  # noqa: BLE001 - filter opsional
                pass
    ctx.progress(58, "Render", f"Merender {n_render} klip (reframe wajah, caption, editing)"
                 + (f" — mode otomatis, perkiraan ±{max(1, round(n_render * 4))} menit…" if auto_n else "…"))
    if p.get("campaign"):
        # Hasil akhir hanya template MotionKlip (banner + judul + subtitle + outro),
        # dengan kategori & durasi tetap dari analyzer per klip.
        from . import live
        clips = live._render_campaign_window(video, selected, ctx.dir, p, 0.0)
    else:
        clips = process_clips(video, selected, ctx.dir, **render_options(p))
    brand_clips(ctx, clips, p.get("brand_kit"))
    ctx.progress(92, "Publish", "Paket upload & metadata")
    publish = _publish(ctx, clips, p)
    register_clips(ctx, clips, "analisis_ai")
    (ctx.dir / "transcript.json").write_text(json.dumps(transcript, ensure_ascii=False), encoding="utf-8")
    auto_pub = None
    if p.get("auto_publish") and clips:  # v6.8: langsung dijadwalkan ke jam FYP terbaik, upload sendiri
        from . import autopublish
        ctx.progress(96, "Upload otomatis", "Menjadwalkan klip ke jam FYP terbaik…")
        auto_pub = autopublish.schedule_clips(clips, platforms=p.get("auto_publish_platforms") or None,
                                              niche_override=p.get("publish_category") or "",
                                              extra_tags=p.get("extra_hashtags") or "", content_type=info.get("content_type", ""))
        if auto_pub["skipped"]:
            ctx.log(f"Upload otomatis dilewati: {auto_pub['skipped']}")
        for s in auto_pub["scheduled"]:
            ctx.log(f"Dijadwalkan {datetime.fromtimestamp(s['run_at']).strftime('%a %d %b %H:%M')} → "
                    f"{', '.join(s['platforms'])} ({s['niche']}, jam {s.get('slot', 'jangkar')}): {s['title'][:60]}")
    return {"_message": f"{len(clips)} klip viral siap" + (f" · {len(auto_pub['scheduled'])} dijadwalkan otomatis"
                                                            if auto_pub and auto_pub["scheduled"] else ""),
            "clips": clips, "total_clips": len(clips),
            "transcription": tinfo, "publish_folder": publish, "source": video,
            "ai": f"{config.AI_PROVIDER}/{config.LLM_MODEL}" if llm else "local-only",
            "auto_publish": auto_pub}


# ============================================================================ Atur Manual

def _manual_clip_data(transcript: List[Dict], start: float, end: float, title: str, idx: int) -> Dict:
    segs = [s for s in transcript if max(s["start"], start) < min(s["end"], end)]
    words = [w for s in segs for w in (s.get("words") or []) if start - 0.05 <= float(w.get("start", 0)) < end]
    text = " ".join(s["text"].strip() for s in segs).strip()
    return {
        "start": start, "end": end, "duration": end - start, "text": text[:200], "transcript": text[:2000],
        "score": 0, "local_score": 0, "llm_score": 0, "score_breakdown": {}, "category": f"manual{idx + 1}",
        "reason": "Dipilih manual", "keywords_matched": [], "sentiment": 0, "fyp_signals": [],
        "hook_text": title or (text[:60] if text else ""), "upload_caption": title or text[:120], "hashtags": [],
        "source": "manual", "subtitle_segments": segs, "words": words, "ai_title": title,
    }


@handler("clip_manual")
def clip_manual(ctx: JobContext) -> Dict:
    from .clipper import process_clips
    p = ctx.params
    ranges = [r for r in (p.get("ranges") or []) if float(r.get("end", 0)) > float(r.get("start", 0))]
    if not ranges:
        raise ValueError("Tambahkan minimal satu segmen (mulai < selesai)")
    video = resolve_source(ctx, p)
    transcript: List[Dict] = []
    if p.get("enable_captions", True):
        transcript, _ = transcribe(ctx, video, p, 15)
    clips_data = [_manual_clip_data(transcript, float(r["start"]), float(r["end"]), r.get("title", ""), i)
                  for i, r in enumerate(ranges)]
    if not p.get("hook_card", False):
        for c in clips_data:
            c["no_hook_card"] = True
    old_snap = config.SCENE_SNAP_ENABLED
    config.SCENE_SNAP_ENABLED = False
    try:
        ctx.progress(45, "Render", f"Merender {len(clips_data)} segmen manual…")
        clips = process_clips(video, clips_data, ctx.dir, **render_options(p))
    finally:
        config.SCENE_SNAP_ENABLED = old_snap
    brand_clips(ctx, clips, p.get("brand_kit"))
    register_clips(ctx, clips, "atur_manual")
    return {"_message": f"{len(clips)} klip manual siap", "clips": clips, "total_clips": len(clips), "source": video}


# ============================================================================ Split Cam

@handler("split_cam")
def split_cam_job(ctx: JobContext) -> Dict:
    p = ctx.params
    video = resolve_source(ctx, p)
    cam = tuple(float(v) for v in p.get("cam_box", [0.7, 0.0, 0.3, 0.3]))
    game = tuple(float(v) for v in p.get("game_box", [0.0, 0.0, 1.0, 1.0]))
    segments = p.get("ranges") or [{"start": 0, "end": 0}]
    outs = []
    for i, r in enumerate(segments):
        ctx.progress(20 + int(60 * i / len(segments)), "Render", f"Split-Cam segmen {i + 1}/{len(segments)}")
        out = ctx.dir / f"splitcam_{i + 1:02d}.mp4"
        ffx.split_cam(video, str(out), cam, game, cam_ratio=float(p.get("cam_ratio", 0.38)),
                      cam_top=p.get("cam_top", True), start=float(r.get("start") or 0), end=float(r.get("end") or 0))
        outs.append(out)
    clips = []
    if p.get("enable_captions"):
        ctx.progress(82, "Caption", "Transkripsi & membakar caption")
        from .utils import generate_ass_subtitles
        for out in outs:
            segs, _ = transcribe(ctx, str(out), p, 84)
            ass = out.with_suffix(".ass")
            style = dict(config.CAPTION_STYLES.get(p.get("caption_style", "modern"), {}))
            style["vertical_position"] = int(1920 * (1 - float(p.get("cam_ratio", 0.38))) * 0.25)
            generate_ass_subtitles(segs, style, str(ass), 1080, 1920)
            capped = out.with_name(out.stem + "_cap.mp4")
            ffx.burn_ass(str(out), str(ass), str(capped))
            capped.replace(out)
            ass.unlink(missing_ok=True)
    for out in outs:
        final = apply_brand(str(out), str(out.with_name("_b.mp4")), force=p.get("brand_kit"))
        if final != str(out):
            Path(final).replace(out)
        thumb = out.with_suffix(".jpg")
        ffx.extract_frame(str(out), 1.0, str(thumb), 540)
        a = ctx.asset("clip", out, name=out.stem, meta={"source": "split_cam", "thumbnail": str(thumb),
                                                         "duration": ffx.probe(str(out))["duration"]})
        clips.append({"file_path": str(out), "thumbnail": str(thumb), "asset_id": a["id"], "title": out.stem})
    return {"_message": f"{len(clips)} video Split-Cam siap", "clips": clips, "total_clips": len(clips)}


# ============================================================================ Highlight Bola

_GOAL_WORDS = ["gol", "goal", "golll", "masuk", "penalti", "penalty", "kartu merah", "red card", "selamatkan",
               "save", "tendangan", "assist", "offside", "var", "luar biasa", "what a"]


@handler("highlight_bola")
def highlight_bola(ctx: JobContext) -> Dict:
    """Deteksi momen heboh pertandingan dari lonjakan energi audio (sorakan/komentator) + kata kunci transkrip."""
    import numpy as np
    p = ctx.params
    video = resolve_source(ctx, p)
    ctx.progress(15, "Analisis audio", "Mengukur lonjakan sorakan penonton & komentator")
    energy = ffx.audio_energy(video, hop=0.5)
    if not energy:
        raise RuntimeError("Video tidak punya audio — highlight butuh suara pertandingan")
    t = np.array([e[0] for e in energy]); v = np.array([e[1] for e in energy])
    def moving_avg(x, k):
        k = max(1, min(int(k), len(x)))
        # dibagi jumlah sampel efektif agar tepi awal/akhir tidak dianggap "sunyi" (false peak)
        return np.convolve(x, np.ones(k), mode="same") / np.convolve(np.ones_like(x), np.ones(k), mode="same")

    base = moving_avg(v, 80)  # ±40 dtk latar suara pertandingan
    smooth = moving_avg(v, 6)
    excite = smooth / (base + 1e-6)
    keyword_boost = np.zeros_like(excite)
    if p.get("use_transcript", True):
        try:
            segs, _ = transcribe(ctx, video, p, 30)
            for s in segs:
                text = s["text"].lower()
                hits = sum(1 for w in _GOAL_WORDS if w in text)
                if hits:
                    mask = (t >= s["start"] - 2) & (t <= s["end"] + 2)
                    keyword_boost[mask] += 0.35 * hits
        except Exception as exc:
            ctx.log(f"Transkrip dilewati: {exc}")
    score = excite + keyword_boost
    num = max(1, min(int(p.get("num_clips", 6)), 20))
    pre, post = float(p.get("pre_sec", 12)), float(p.get("post_sec", 8))
    duration = float(t[-1])
    picks: List[float] = []
    order = np.argsort(-score)
    for idx in order:
        cand = float(t[idx])
        if all(abs(cand - q) > pre + post for q in picks):
            picks.append(cand)
        if len(picks) >= num:
            break
    picks.sort()
    ctx.progress(45, "Render", f"{len(picks)} momen terdeteksi")
    mode = p.get("fit_mode", "blur")
    w, h = ffx.size_for(p.get("aspect", "9:16"))
    crop = ffx.auto_crop(video)  # sumber berbingkai (border + logo channel) → hanya area gambar
    clips = []
    for i, peak in enumerate(picks):
        ctx.progress(45 + int(45 * i / len(picks)), "Render", f"Highlight {i + 1}/{len(picks)} @ {int(peak // 60)}:{int(peak % 60):02d}")
        start, end = max(0.0, peak - pre), min(duration, peak + post)
        raw = ctx.dir / f"_hl_{i:02d}.mp4"
        ffx.run(["-ss", f"{start:.2f}", "-i", video, "-t", f"{end - start:.2f}", "-filter_complex",
                 f"[0:v]{crop}{ffx.fit_filter(w, h, mode)},fps=30,format=yuv420p[v]", "-map", "[v]", "-map", "0:a?",
                 *ffx.venc(19), *ffx.AENC, str(raw)])
        out = ctx.dir / f"highlight_{i + 1:02d}.mp4"
        label = (p.get("caption_text") or "MOMEN KUNCI").strip()
        items = [{"type": "text", "text": f"{label} {i + 1}", "x": 0.06, "y": 0.1, "size": 0.04, "start": 0, "end": 3.5}]
        ffx.overlay_items(str(raw), str(out), items)
        raw.unlink(missing_ok=True)
        if p.get("commentary") and media_ai.tts_available(p.get("tts_provider") or "edge"):
            try:
                line = p.get("commentary_text") or "Gooool! Momen luar biasa di pertandingan ini!"
                voice = media_ai.tts(line, str(ctx.dir / f"_c{i}.mp3"), provider=p.get("tts_provider", ""),
                                     voice=p.get("voice", ""))
                mixed = out.with_name("_mix.mp4")
                ffx.run(["-i", str(out), "-i", voice["path"], "-filter_complex",
                         f"[1:a]adelay={int(pre * 1000)}|{int(pre * 1000)},volume=1.4[c];[0:a][c]amix=inputs=2:duration=first:normalize=0[a]",
                         "-map", "0:v", "-map", "[a]", "-c:v", "copy", *ffx.AENC, str(mixed)])
                mixed.replace(out)
            except Exception as exc:
                ctx.log(f"Narasi komentator gagal: {exc}")
        final = apply_brand(str(out), str(out.with_name("_b.mp4")), force=p.get("brand_kit"))
        if final != str(out):
            Path(final).replace(out)
        thumb = out.with_suffix(".jpg")
        ffx.extract_frame(str(out), pre, str(thumb), 540)
        a = ctx.asset("clip", out, name=f"Highlight {i + 1}", meta={"source": "highlight_bola", "thumbnail": str(thumb),
                                                                    "duration": end - start, "peak": peak})
        clips.append({"file_path": str(out), "thumbnail": str(thumb), "asset_id": a["id"], "peak": peak,
                      "start": start, "end": end, "title": f"Highlight {i + 1}"})
    return {"_message": f"{len(clips)} highlight siap", "clips": clips, "total_clips": len(clips)}


# ============================================================================ Konten Tempel, Merge, Loop, WA, Editor

def _single_output(ctx: JobContext, out: Path, source: str, title: str = "", meta: Optional[Dict] = None) -> Dict:
    thumb = out.with_suffix(".jpg")
    try:
        info = ffx.probe(str(out))
        ffx.extract_frame(str(out), min(1.0, info["duration"] / 3), str(thumb), 540)
    except Exception:
        info, thumb = {"duration": 0}, None
    a = ctx.asset("video", out, name=title or out.stem,
                  meta={"source": source, "thumbnail": str(thumb) if thumb else "", "duration": info.get("duration"), **(meta or {})})
    return {"file": str(out), "thumbnail": str(thumb) if thumb else "", "asset_id": a["id"], "duration": info.get("duration")}


@handler("konten_tempel")
def konten_tempel(ctx: JobContext) -> Dict:
    p = ctx.params
    video = resolve_source(ctx, p)
    ctx.progress(30, "Render", f"Menempel {len(p.get('items') or [])} elemen")
    out = ctx.dir / "konten_tempel.mp4"
    ffx.overlay_items(video, str(out), p.get("items") or [])
    return {"_message": "Konten tempel selesai", **_single_output(ctx, out, "konten_tempel")}


@handler("merge")
def merge_job(ctx: JobContext) -> Dict:
    p = ctx.params
    files: List[str] = []
    entries = p.get("items") or [{"path": f} for f in (p.get("files") or [])] + [{"url": u} for u in (p.get("urls") or [])]
    for i, it in enumerate(entries):
        if it.get("path") and Path(it["path"]).exists():
            files.append(it["path"])
        elif (it.get("url") or "").strip():
            ctx.progress(5 + int(20 * i / max(1, len(entries))), "Download", f"Mengunduh video {i + 1}")
            files.append(resolve_source(ctx, {"url": it["url"].strip()}))
    if len(files) < 2:
        raise ValueError("Butuh minimal 2 video untuk digabung")
    ctx.progress(30, "Merge", f"Menggabungkan {len(files)} video ({p.get('transition', 'none')})")
    out = ctx.dir / "merged.mp4"
    ffx.concat(files, str(out), aspect=p.get("aspect", "9:16"), transition=p.get("transition", "none"),
               tdur=float(p.get("transition_sec", 0.5)), mode=p.get("fit_mode", "blur"), workdir=ctx.dir)
    return {"_message": "Video tergabung", **_single_output(ctx, out, "merge")}


@handler("loop")
def loop_job(ctx: JobContext) -> Dict:
    p = ctx.params
    video = resolve_source(ctx, p)
    ctx.progress(30, "Loop", "Membuat seamless loop")
    out = ctx.dir / "loop.mp4"
    ffx.loop(video, str(out), times=int(p.get("times") or 0), target_sec=float(p.get("target_sec") or 0),
             crossfade=float(p.get("crossfade") or 0))
    return {"_message": "Loop selesai", **_single_output(ctx, out, "loop")}


@handler("wa_story")
def wa_story_job(ctx: JobContext) -> Dict:
    p = ctx.params
    video = resolve_source(ctx, p)
    ctx.progress(30, "WA Story", "Memotong & mengompres untuk Status WhatsApp")
    outs = ffx.wa_story(video, ctx.dir, segment_sec=int(p.get("segment_sec", 60)), max_mb=float(p.get("max_mb", 15.5)),
                        aspect=p.get("aspect", "9:16"), mode=p.get("fit_mode", "blur"))
    clips = []
    for o in outs:
        r = _single_output(ctx, Path(o), "wa_story", meta={"size_mb": round(Path(o).stat().st_size / 1048576, 2)})
        clips.append({"file_path": r["file"], "thumbnail": r["thumbnail"], "asset_id": r["asset_id"], "title": Path(o).stem,
                      "size_mb": round(Path(o).stat().st_size / 1048576, 2)})
    return {"_message": f"{len(clips)} bagian Status WA siap", "clips": clips, "total_clips": len(clips)}


@handler("editor")
def editor_job(ctx: JobContext) -> Dict:
    """Video Editor ringkas: potong → kecepatan → rasio → teks/gambar → musik → logo."""
    p = ctx.params
    current = resolve_source(ctx, p)
    step = 0

    def nxt(label: str) -> str:
        nonlocal step
        step += 1
        return str(ctx.dir / f"_ed{step}_{label}.mp4")

    if p.get("end") and float(p["end"]) > float(p.get("start") or 0):
        ctx.progress(15, "Potong", "Memotong durasi")
        current = ffx.trim(current, nxt("trim"), float(p.get("start") or 0), float(p["end"]))
    if p.get("speed") and abs(float(p["speed"]) - 1.0) > 0.01:
        ctx.progress(30, "Kecepatan", f"{p['speed']}×")
        current = ffx.change_speed(current, nxt("speed"), float(p["speed"]))
    if p.get("aspect") and p["aspect"] != "original":
        ctx.progress(40, "Rasio", p["aspect"])
        w, h = ffx.size_for(p["aspect"])
        current = ffx.normalize(current, nxt("aspect"), w, h, mode=p.get("fit_mode", "blur"))
    if p.get("items"):
        ctx.progress(55, "Overlay", "Teks & gambar")
        current = ffx.overlay_items(current, nxt("ovl"), p["items"])
    if p.get("subtitles"):
        ctx.progress(62, "Subtitle", "Transkripsi & caption")
        from .utils import generate_ass_subtitles
        info = ffx.probe(current)
        segs, _ = transcribe(ctx, current, p, 64)
        ass = ctx.dir / "editor.ass"
        generate_ass_subtitles(segs, dict(config.CAPTION_STYLES.get(p.get("caption_style", "modern"), {})),
                               str(ass), info["width"], info["height"])
        current = ffx.burn_ass(current, str(ass), nxt("sub"))
    if p.get("music_path"):
        ctx.progress(78, "Musik", "Mixing musik latar")
        current = ffx.mux_voice_music(current, nxt("music"), music=p["music_path"],
                                      music_vol=float(p.get("music_volume", 0.15)), keep_original=True)
    ctx.progress(90, "Finalisasi", "Logo & ekspor")
    final = ctx.dir / "edited.mp4"
    branded = apply_brand(current, nxt("brand"), force=p.get("brand_kit"))
    shutil.copy2(branded, final)
    for f in ctx.dir.glob("_ed*.mp4"):
        f.unlink(missing_ok=True)
    return {"_message": "Edit selesai", **_single_output(ctx, final, "editor")}


# ============================================================================ Transcript, TTS, Musik, SFX

def _translate_segments(segments: List[Dict], target: str) -> List[Dict]:
    out: List[Dict] = []
    for i in range(0, len(segments), 60):
        batch = segments[i:i + 60]
        payload = [{"i": j, "t": s["text"]} for j, s in enumerate(batch)]
        raw = ai.complete(f"Terjemahkan tiap 't' ke {target}. Pertahankan makna & gaya bicara. "
                          f"Balas JSON {{\"items\":[{{\"i\":0,\"t\":\"...\"}}]}}.\n\n{json.dumps(payload, ensure_ascii=False)}",
                          max_tokens=6000)
        items = {int(x["i"]): x["t"] for x in ai.parse_json(raw).get("items", []) if "i" in x}
        out += [{**s, "text": items.get(j, s["text"])} for j, s in enumerate(batch)]
    return out


@handler("transcript")
def transcript_job(ctx: JobContext) -> Dict:
    p = ctx.params
    segments: List[Dict] = []
    title = ""
    origin = ""
    if p.get("url") and not p.get("path") and p.get("prefer_subtitles", True) and ("youtu" in p["url"]):
        ctx.progress(10, "Subtitle YouTube", "Mengambil subtitle tanpa download video")
        try:
            data = sources.youtube_transcript(p["url"])
            segments, title, origin = data["segments"], data["info"].get("title", ""), f"subtitle YouTube ({data['language']})"
        except Exception as exc:
            ctx.log(f"Subtitle YouTube gagal: {exc}")
    if not segments:
        if p.get("path") and Path(p["path"]).suffix.lower() in (".txt", ".srt", ".vtt", ".pdf"):
            text = sources.read_document(p["path"])
            segments = [{"start": 0, "end": 0, "text": text}]
            origin = "dokumen"
        else:
            video = resolve_source(ctx, p)
            segments, tinfo = transcribe(ctx, video, p, 25)
            dev = str((tinfo or {}).get("device") or whisper_device(p)[0])
            origin = {"elevenlabs": "ElevenLabs Scribe", "groq": "Groq Whisper"}.get(dev, f"Whisper ({dev})")
            title = title or Path(video).stem
    if p.get("translate_to"):
        ctx.progress(70, "Terjemah", f"Menerjemahkan ke {p['translate_to']}")
        segments = _translate_segments(segments, p["translate_to"])
    clean = [{"start": round(float(s["start"]), 2), "end": round(float(s["end"]), 2), "text": s["text"].strip()} for s in segments]
    text = " ".join(s["text"] for s in clean)
    (ctx.dir / "transcript.txt").write_text(text, encoding="utf-8")
    (ctx.dir / "transcript_timestamp.txt").write_text(sources.segments_to_timestamped(clean), encoding="utf-8")
    if any(s["end"] for s in clean):
        (ctx.dir / "transcript.srt").write_text(sources.segments_to_srt(clean), encoding="utf-8")
    (ctx.dir / "transcript.json").write_text(json.dumps(clean, ensure_ascii=False, indent=1), encoding="utf-8")
    ctx.asset("transcript", ctx.dir / "transcript.txt", name=title or "Transkrip", meta={"origin": origin, "words": len(text.split())})
    return {"_message": f"Transkrip {len(text.split())} kata ({origin})", "title": title, "origin": origin,
            "segments": clean[:3000], "text": text[:200000],
            "files": {k: str(ctx.dir / v) for k, v in (("txt", "transcript.txt"), ("srt", "transcript.srt"),
                                                         ("timestamp", "transcript_timestamp.txt"), ("json", "transcript.json"))
                      if (ctx.dir / v).exists()}}


@handler("tts")
def tts_job(ctx: JobContext) -> Dict:
    p = ctx.params
    text = (p.get("text") or "").strip()
    if not text:
        raise ValueError("Teks kosong")
    chunks = [c.strip() for c in re.split(r"\n\s*\n", text) if c.strip()] if p.get("split_paragraphs") else [text]
    outs = []
    for i, chunk in enumerate(chunks):
        ctx.progress(10 + int(80 * i / len(chunks)), "TTS", f"Bagian {i + 1}/{len(chunks)}")
        res = media_ai.tts(chunk, str(ctx.dir / f"tts_{i + 1:02d}.mp3"), provider=p.get("provider", ""), voice=p.get("voice", ""),
                           speed=float(p.get("speed") or 1.0), instructions=p.get("instructions", ""))
        outs.append(res)
    if len(outs) > 1:
        lst = ctx.dir / "_list.txt"
        lst.write_text("".join(f"file '{Path(o['path']).resolve().as_posix()}'\n" for o in outs), encoding="utf-8")
        final = ctx.dir / "tts_full.mp3"
        ffx.run(["-f", "concat", "-safe", "0", "-i", str(lst), "-c:a", "libmp3lame", "-q:a", "2", str(final)])
        lst.unlink(missing_ok=True)
    else:
        final = Path(outs[0]["path"])
    dur = ffx.probe(str(final))["duration"]
    a = ctx.asset("audio", final, name=(p.get("name") or text[:40]), meta={"source": "tts", "provider": outs[0]["provider"],
                                                                           "voice": outs[0]["voice"], "duration": dur})
    return {"_message": f"Audio {dur:.1f} dtk ({outs[0]['provider']})", "file": str(final), "asset_id": a["id"],
            "duration": dur, "provider": outs[0]["provider"], "voice": outs[0]["voice"],
            "parts": [o["path"] for o in outs]}


@handler("music")
def music_job(ctx: JobContext) -> Dict:
    p = ctx.params
    ctx.progress(20, "Musik AI", "ElevenLabs Music sedang membuat lagu…")
    out = ctx.dir / "music.mp3"
    media_ai.music(p.get("prompt", ""), str(out), duration=float(p.get("duration", 30)), lyrics=p.get("lyrics", ""),
                   instrumental=p.get("instrumental", True))
    a = ctx.asset("music", out, name=p.get("name") or p.get("prompt", "Musik")[:40], meta={"source": "music_ai", "prompt": p.get("prompt")})
    return {"_message": "Musik jadi", "file": str(out), "asset_id": a["id"]}


@handler("sfx")
def sfx_job(ctx: JobContext) -> Dict:
    p = ctx.params
    ctx.progress(20, "SFX AI", "Membuat efek suara…")
    out = ctx.dir / "sfx.mp3"
    media_ai.sound_effect(p.get("prompt", ""), str(out), duration=float(p.get("duration") or 0))
    a = ctx.asset("sfx", out, name=p.get("prompt", "SFX")[:40], meta={"source": "sfx_ai"})
    return {"_message": "SFX jadi", "file": str(out), "asset_id": a["id"]}


# ============================================================================ Tool AI sinkron

def _llm(prompt: str, system: str = "", max_tokens: int = 3000, json_mode: bool = True) -> Dict:
    raw = ai.complete(prompt, system=system or "Kamu ahli strategi konten short-form & SEO YouTube/TikTok Indonesia.",
                      max_tokens=max_tokens, json_mode=json_mode)
    return ai.parse_json(raw) if json_mode else {"text": raw}


def optimize_title(params: Dict) -> Dict:
    material = params.get("text", "").strip()
    if params.get("url"):
        info = sources.youtube_transcript(params["url"])
        material = (f"Judul sekarang: {info['info'].get('title')}\nDeskripsi: {info['info'].get('description', '')[:1200]}\n"
                    f"Transkrip: {info['text'][:8000]}")
    if not material:
        raise ValueError("Isi judul/topik atau link YouTube")
    # v6.8: judul/tag dioptimasi terhadap apa yang SEDANG naik minggu ini, bukan pengetahuan lama AI
    trend = {"text": "", "topics": [], "videos": []}
    try:
        from . import trends
        topic = " ".join(re.findall(r"\w+", material.split("\n")[0].replace("Judul sekarang:", ""))[:6]) or material[:60]
        trend = trends.ai_context(topic, max_titles=10)
    except Exception as exc:
        logger.warning(f"Data tren untuk optimasi judul gagal: {str(exc)[:120]}")
    out = _llm(
        f"Optimasi metadata video untuk {params.get('platform', 'YouTube')} ({params.get('language', 'Bahasa Indonesia')}).\n\n{material}\n\n"
        + (f"DATA TREN TERBARU (pakai kata kunci/topik yang relevan dengan video ini saja):\n{trend['text']}\n\n" if trend["text"] else "")
        +
        "Balas JSON: {\"titles\":[{\"type\":\"SEO\",\"title\":\"...\",\"why\":\"...\"},{\"type\":\"Viral\",...},{\"type\":\"CTR\",...},"
        "{\"type\":\"Balanced\",...}],\"description\":\"250-300 karakter\",\"tags\":[\"15 tag tanpa #\"],"
        "\"hashtags\":[\"#..\"],\"thumbnail_text\":[\"3 opsi teks thumbnail maks 4 kata\"]}. Judul maks 70 karakter, "
        "memancing penasaran tapi tanpa clickbait menipu.")
    out["trend_topics"] = trend.get("topics", [])
    return out


def generate_prompts(params: Dict) -> Dict:
    idea = (params.get("idea") or "").strip()
    if not idea:
        raise ValueError("Tulis ide dulu")
    target = params.get("target", "gambar")
    return _llm(
        f"Ide: {idea}\nTarget: prompt {target} AI (gaya: {params.get('style') or 'bebas'}, rasio {params.get('aspect', '9:16')}).\n"
        f"Buat {int(params.get('count', 4))} variasi prompt profesional BAHASA INGGRIS yang siap ditempel ke "
        "Midjourney/Flux/Gemini/GPT-Image (untuk gambar) atau Veo/Kling/Hailuo/Runway (untuk video: sertakan gerak kamera & durasi). "
        "Balas JSON {\"prompts\":[{\"title\":\"..\",\"prompt\":\"..\",\"negative\":\"..\",\"notes\":\"tips singkat bahasa Indonesia\"}]}")


def viral_suggestions(params: Dict) -> Dict:
    niche = (params.get("niche") or "umum").strip()
    # v6.8: data HARI INI/MINGGU INI (urut views) + topik yang naik + Google Trends, bukan pencarian relevansi lama
    from . import trends
    ctx_data = {"text": "", "videos": [], "topics": [], "google_trends": []}
    try:
        ctx_data = trends.ai_context(niche if niche != "umum" else "viral indonesia")
    except Exception as exc:
        logger.warning(f"Data tren segar gagal: {str(exc)[:120]}")
    trending = [v["title"] for v in ctx_data["videos"]]
    if not trending:
        try:
            trending = [v["title"] for v in sources.youtube_search(f"{niche} viral", 15)]
        except Exception as exc:
            logger.warning(f"Pencarian tren gagal: {exc}")
    return _llm(
        f"Niche: {niche}. Platform: {params.get('platform', 'TikTok & YouTube Shorts')}. Audiens: Indonesia.\n"
        + (ctx_data["text"] + "\n\nUtamakan ide yang menunggangi topik yang SEDANG naik di atas (momentum). " if ctx_data["text"] else
           "Judul video yang sedang ramai di niche ini (data pencarian):\n- " + "\n- ".join(trending[:15])) +
        f"\n\nBuat {int(params.get('count', 10))} ide konten viral. Balas JSON {{\"patterns\":[\"pola viral yang terlihat\"],"
        "\"ideas\":[{\"title\":\"..\",\"hook\":\"kalimat 2 detik pertama\",\"format\":\"talking head / faceless / listicle / ...\","
        "\"why\":\"alasan berpotensi viral\",\"difficulty\":\"mudah|sedang|sulit\",\"tool\":\"fitur studio yang cocok: "
        "Analisis AI / Content Creator / Story Studio / Motion Graphic / Split Cam\"}],\"avoid\":[\"hal yang sebaiknya dihindari\"]}",
        max_tokens=4000) | {"trending_sample": trending[:15], "trend_topics": ctx_data["topics"],
                            "google_trends": ctx_data["google_trends"][:10], "data_date": ctx_data.get("date", "")}


def assistant_chat(params: Dict) -> Dict:
    msgs = params.get("messages") or []
    history = "\n".join(f"{'Pengguna' if m.get('role') == 'user' else 'Asisten'}: {m.get('content', '')}" for m in msgs[-16:])
    system = ("Kamu 'Studio AI', asisten kreator konten short-form berbahasa Indonesia di aplikasi Viral Auto Klip. "
              "Bantu ide konten, naskah, hook, strategi upload, analisis niche, dan cara memakai fitur aplikasi: "
              "Analisis AI (klip otomatis), Atur Manual, Split Cam, Highlight Bola, Content Creator, Story Studio, "
              "Motion Graphic, TTS, Clone Suara, Brand Kit, Auto Upload, Optimasi Judul, Transcript. Jawab ringkas & praktis.")
    reply = ai.complete(f"{history}\nAsisten:", system=system, max_tokens=1500, json_mode=False)
    return {"reply": reply.strip()}


def thumbnail_download(params: Dict) -> Dict:
    info = sources.video_info(params["url"])
    return {"title": info["title"], "thumbnails": info["thumbnails"], "best": info["thumbnail"]}


# ============================================================================ Analitik Viral

_RPM_IDR = {"Gaming": 9000, "Musik": 7000, "Komedi": 9000, "Hiburan": 10000, "Tutorial": 18000,
            "Olahraga": 11000, "Teknologi": 20000, "Keuangan": 28000, "Edukasi": 16000, "Semua": 12000}
_CAT_QUERY = {"Semua": "viral indonesia", "Gaming": "gaming indonesia", "Musik": "lagu viral indonesia",
              "Komedi": "komedi lucu indonesia", "Hiburan": "hiburan indonesia", "Tutorial": "tutorial indonesia",
              "Olahraga": "sepak bola indonesia", "Teknologi": "teknologi indonesia", "Keuangan": "investasi keuangan",
              "Edukasi": "edukasi fakta"}
_YT_CAT = {"Gaming": "20", "Musik": "10", "Komedi": "23", "Hiburan": "24", "Tutorial": "26", "Olahraga": "17",
           "Teknologi": "28", "Edukasi": "27"}


def _days_since(date: Optional[str]) -> float:
    if not date:
        return 7.0
    try:
        d = datetime.strptime(date[:10].replace("-", ""), "%Y%m%d")
        return max(0.5, (datetime.now() - d).total_seconds() / 86400)
    except ValueError:
        return 7.0


def viral_analytics(params: Dict) -> Dict:
    import requests
    from . import trends
    category = params.get("category", "Semua")
    if not params.get("_auto"):
        trends.watch_category(category)  # kategori yang dibuka ikut diperbarui otomatis
    cache_key = f"analytics:{category}"
    cached = db.kv_get(cache_key)
    if cached and time.time() - cached.get("_ts", 0) < 3600 and not params.get("refresh") and cached.get("trending_topics") is not None:
        return cached
    videos: List[Dict] = []
    source = "yt-dlp search"
    key = media_ai._key("YOUTUBE_API_KEY")
    if key:
        try:
            q = {"part": "snippet,statistics,contentDetails", "chart": "mostPopular", "regionCode": params.get("region", "ID"),
                 "maxResults": 25, "key": key}
            if category in _YT_CAT:
                q["videoCategoryId"] = _YT_CAT[category]
            resp = requests.get("https://www.googleapis.com/youtube/v3/videos", params=q, timeout=30)
            resp.raise_for_status()
            for it in resp.json().get("items", []):
                st, sn = it.get("statistics", {}), it.get("snippet", {})
                videos.append({"id": it["id"], "title": sn.get("title"), "channel": sn.get("channelTitle"),
                               "views": int(st.get("viewCount", 0)), "likes": int(st.get("likeCount", 0) or 0),
                               "comments": int(st.get("commentCount", 0) or 0), "upload_date": sn.get("publishedAt", "")[:10],
                               "thumbnail": sn.get("thumbnails", {}).get("medium", {}).get("url", ""),
                               "url": f"https://www.youtube.com/watch?v={it['id']}", "duration": 0})
            source = "YouTube Data API (mostPopular ID)"
        except Exception as exc:
            logger.warning(f"YouTube API gagal: {exc}")
    # v6.8: video segar (hari ini + minggu ini, urut views) — dulu pencarian relevansi tanpa filter waktu
    seen = {v["id"] for v in videos}
    fresh = [v for v in trends.fresh_videos(_CAT_QUERY.get(category, category), 30) if v["id"] not in seen and not v.get("live")]
    if fresh:
        videos += fresh
        source = (source + " + " if seen else "") + "YouTube hari ini & minggu ini (urut views)"
    if not videos:
        videos = sources.youtube_search(_CAT_QUERY.get(category, category), 24)

    def _age(v):
        return float(v["age_days"]) if v.get("age_days") and not v.get("upload_date") else _days_since(v.get("upload_date"))
    rpm = _RPM_IDR.get(category, 12000)
    max_vpd = max([v.get("views", 0) / _age(v) for v in videos] or [1]) or 1
    for v in videos:
        views = v.get("views", 0) or 0
        vpd = views / _age(v)
        eng = ((v.get("likes", 0) + v.get("comments", 0) * 2) / views * 100) if views and v.get("likes") is not None else None
        v["engagement"] = round(eng, 2) if eng is not None else None
        v["viral_score"] = int(min(100, round(100 * math.log10(1 + vpd) / math.log10(1 + max_vpd))))
        v["est_revenue_idr"] = int(views / 1000 * rpm * 0.55)
        v["rising"] = vpd > max_vpd * 0.25
    videos.sort(key=lambda v: -v["viral_score"])
    from .fyp_agent import get_best_upload_schedule
    niche = {"Gaming": "gaming", "Komedi": "comedy", "Teknologi": "tech", "Keuangan": "finance", "Tutorial": "education",
             "Edukasi": "education"}.get(category, "general")
    schedule = {pf: get_best_upload_schedule(niche, pf)["best_times"] for pf in ("TikTok", "YouTube Shorts", "Instagram Reels")}
    views = [v.get("views", 0) for v in videos] or [0]
    tags: Dict[str, int] = {}
    for v in videos:
        for tag in re.findall(r"#(\w+)", v.get("title", "")):
            tags[tag.lower()] = tags.get(tag.lower(), 0) + 1
    avg_score = sum(v["viral_score"] for v in videos) / max(1, len(videos))
    result = {
        "category": category, "source": source, "videos": videos,
        "summary": {"avg_views": int(sum(views) / max(1, len(views))),
                    "est_revenue_idr": int(sum(v["est_revenue_idr"] for v in videos) / max(1, len(videos))),
                    "viral_score": int(avg_score), "total": len(videos),
                    "competition": "Sangat tinggi" if avg_score > 60 else "Tinggi" if avg_score > 40 else "Sedang"},
        "best_times": schedule, "top_hashtags": sorted(tags, key=lambda k: -tags[k])[:8] or ["viral", "fyp", "shorts"],
        "note": "Skor viral = kecepatan views/hari relatif terhadap video tercepat di daftar. Pendapatan = estimasi kasar RPM Indonesia.",
        "trending_topics": trends.topics_from_titles(videos, limit=10, exclude=_CAT_QUERY.get(category, category)),
        "google_trends": [],
        "auto_refresh_min": int(getattr(config, "TRENDS_REFRESH_MIN", 60)) if getattr(config, "TRENDS_AUTO_REFRESH", True) else 0,
        "_ts": time.time(),
    }
    try:
        result["google_trends"] = trends.google_trends(limit=12)
    except Exception as exc:
        logger.warning(f"Google Trends gagal: {str(exc)[:120]}")
    if sum(views) > 0:  # YouTube kadang mengembalikan hasil tanpa jumlah views — jangan di-cache
        db.kv_set(cache_key, result)
    else:
        result["note"] = "YouTube tidak mengirim jumlah views kali ini — klik Perbarui sebentar lagi. " + result["note"]
    return result


# ============================================================================ Campaign (MotionKlip)

@handler("campaign_render", background=True)
def campaign_render_job(ctx: JobContext) -> Dict:
    """Export satu cut MotionKlip (split/full + banner + judul + subtitle + outro).

    Didaftarkan sebagai `background=True`: render 60 fps bisa puluhan menit dan
    tidak boleh memblokir antrian job lain.
    """
    from . import campaign

    p = ctx.params
    video = resolve_source(ctx, p)
    # Preset default bisa diatur di Pengaturan (config.CAMPAIGN_DEFAULT_PRESET).
    preset = p.get("preset") or getattr(config, "CAMPAIGN_DEFAULT_PRESET", "") or "motion-klip-gtavi-split.json"
    preset_path = Path(preset)
    if not preset_path.is_absolute() and not preset_path.exists():
        preset_path = Path(config.BASE_DIR) / "presets" / preset_path.name
    overrides = {k: p[k] for k in (
        "layout", "facecam_rect", "gameplay_rect", "subtitle_provider",
        "highlight", "intro_voice",
    ) if k in p and p[k] not in (None, "")}

    # ---- Rentang: satu cut, atau beberapa sekaligus (batch).
    raw_ranges = p.get("ranges") or []
    ranges = []
    for item in raw_ranges:
        try:
            r_start = float(item.get("start") or 0)
            r_end = item.get("end")
            r_end = None if r_end in (None, "") else float(r_end)
        except (TypeError, ValueError):
            continue
        if r_end is not None and r_end <= r_start:
            continue
        ranges.append({"start": r_start, "end": r_end,
                       "title": str(item.get("title") or p.get("title") or "")})
    if not ranges:
        end = p.get("end")
        ranges = [{"start": float(p.get("start") or 0),
                   "end": float(end) if end not in (None, "") else None,
                   "title": str(p.get("title") or "")}]

    from .jobs import submit

    clips: List[Dict] = []
    reviews: List[Dict] = []
    upload_jobs: List[str] = []
    for index, item in enumerate(ranges, 1):
        ctx.progress(5 + int(80 * (index - 1) / len(ranges)), "Campaign",
                     f"Merender {index}/{len(ranges)}…" if len(ranges) > 1 else "Merender campaign…")
        review = campaign.render_edited(
            video, preset_path,
            start=item["start"], end=item["end"],
            output_dir=ctx.dir, params=p, title=item["title"],
            model=p.get("whisper_model") or None,
        )
        reviews.append(review)

        out = Path(review["output"])
        thumb = out.with_suffix(".jpg")
        try:
            ffx.extract_frame(str(out), 1.0, str(thumb), 540)
        except Exception:  # noqa: BLE001 - thumbnail opsional
            thumb = None
        preset_tags = str(review.get("hashtags") or "").strip() if p.get("use_preset_hashtags") else ""
        # Judul & deskripsi dibuat di sini (bukan hanya saat auto-upload) supaya
        # tombol upload manual di Library juga punya metadata — sama seperti klip biasa.
        generated: Dict = {}
        if p.get("auto_meta", True):
            generated = _auto_upload_meta(review.get("transcript_text") or "", preset_tags)
        title = str(item["title"] or generated.get("title") or "")
        name = title[:100] or out.stem
        asset = ctx.asset("clip", out, name=name, meta={
            "source": "campaign", "thumbnail": str(thumb) if thumb else "",
            "duration": ffx.probe(str(out))["duration"],
        # Simpan sebagai list: UI memanggil .join(), dan klip biasa juga list.
        "hashtags": [t for t in preset_tags.split() if t],
            "layout": review.get("layout"),
            "category": review.get("category") or "",
            "title": name,
            "description": generated.get("description") or "",
            "upload_caption": generated.get("description") or "",
        })
        clips.append({"file_path": str(out), "thumbnail": str(thumb) if thumb else "",
                      "asset_id": asset["id"], "title": name,
                      "ai_title": generated.get("title") or "",
                      "upload_caption": generated.get("description") or ""})

        # ---- Auto-publish per klip (YouTube headless, opsional TikTok juga).
        if p.get("auto_upload"):
            title = (title or out.stem)[:100]
            description = (generated.get("description") or "") if p.get("auto_meta", True) else ""
            discord = str(review.get("discord_tag") or "").strip()
            missing = [t for t in preset_tags.split() if t and t.lstrip("#") not in description]
            parts = [description, " ".join(missing), f"Discord: {discord}" if discord else ""]
            description = " ".join(part for part in parts if part).strip()
            targets = p.get("upload_targets") or ["youtube"]
            if isinstance(targets, str):
                targets = [t.strip() for t in targets.split(",") if t.strip()]
            ctx.progress(96, "Upload", f"Mengirim {out.name} ke antrean upload…")
            if "youtube" in targets:
                upload_jobs.append(submit("upload_browser", {
                    "path": str(out), "title": title, "description": description,
                    "privacy": str(p.get("upload_privacy") or "private"), "parent": ctx.id,
                }, title=f"Upload · {title[:50]}")["id"])
            if "tiktok" in targets:
                upload_jobs.append(submit("upload_tiktok", {
                    "path": str(out), "description": description, "parent": ctx.id,
                }, title=f"TikTok · {title[:50]}")["id"])

    ctx.progress(99, "Selesai", "Campaign selesai")
    joined = ", ".join(Path(r["output"]).name for r in reviews[:3])
    return {
        "_message": f"{len(clips)} campaign siap ({joined}{'…' if len(reviews) > 3 else ''})"
                    + (f" · {len(upload_jobs)} upload diantrekan" if upload_jobs else ""),
        "clips": clips,
        "total_clips": len(clips),
        "campaign": reviews[0] if len(reviews) == 1 else reviews,
        "upload_job": upload_jobs[0] if len(upload_jobs) == 1 else None,
        "upload_jobs": upload_jobs,
    }


# ============================================================================ Upload browser (tanpa popup)

@handler("upload_browser", background=True)
def upload_browser_job(ctx: JobContext) -> Dict:
    """Upload satu klip ke YouTube lewat browser headless (bypass kuota API).

    Tidak membuka jendela apa pun; progress hanya lewat job log.
    Deskripsi default diambil dari metadata aset (caption + hashtag hasil AI)
    kalau job tidak mengirim description sendiri.
    """
    from . import publish_guard, youtube_browser

    p = ctx.params
    path = resolve_source(ctx, p)
    previous = publish_guard.check_or_raise(path, "youtube", force=bool(p.get("force")))
    if previous:
        ctx.log(f"Sudah pernah diupload: {previous.get('url') or previous.get('title') or 'record lama'}")
        return {
            "_message": "Dilewati — klip ini sudah pernah diupload ke YouTube. "
                        "Kirim force=true untuk mengulang.",
            "skipped": True, "previous": previous, "clips": [], "total_clips": 0,
        }
    meta = _asset_meta(path)
    title = str(p.get("title") or meta.get("title") or Path(path).stem)[:100]
    description = str(p.get("description") or meta.get("description") or "")
    hashtags = p.get("tags") or meta.get("hashtags") or []
    if isinstance(hashtags, str):
        hashtags = [t.strip().lstrip("#") for t in hashtags.replace(",", " ").split() if t.strip()]
    if hashtags and not all(f"#{t}" in description for t in hashtags[:3]):
        description = (description + " " + " ".join(f"#{t}" for t in hashtags)).strip()
    privacy = str(p.get("privacy") or "private")

    def progress(stage: str, pct: int, message: str) -> None:
        ctx.progress(max(5, min(99, pct)), stage, message)

    result = youtube_browser.upload_video(
        path, title=title, description=description,
        tags=hashtags, privacy=privacy, progress=progress)
    publish_guard.record_upload(path, "youtube", {
        "url": result.get("url"), "title": title, "status": "done", "at": time.time(),
    })
    return {
        "_message": f"Terupload ke YouTube ({privacy}): {title[:60]}",
        "upload": result,
        "clips": [{"file_path": path, "title": title, "upload": result}],
    }


@handler("upload_tiktok", background=True)
def upload_tiktok_job(ctx: JobContext) -> Dict:
    """Upload satu klip ke TikTok lewat browser **headless** (tanpa popup/jendela)."""
    from . import publish_guard, tiktok_browser

    p = ctx.params
    path = resolve_source(ctx, p)
    previous = publish_guard.check_or_raise(path, "tiktok", force=bool(p.get("force")))
    if previous:
        ctx.log("Sudah pernah diupload ke TikTok — dilewati")
        return {
            "_message": "Dilewati — klip ini sudah pernah diupload ke TikTok. "
                        "Kirim force=true untuk mengulang.",
            "skipped": True, "previous": previous, "clips": [], "total_clips": 0,
        }
    meta = _asset_meta(path)
    description = str(p.get("description") or meta.get("description") or "")
    hashtags = p.get("tags") or meta.get("hashtags") or []
    if isinstance(hashtags, str):
        hashtags = [t.strip().lstrip("#") for t in hashtags.replace(",", " ").split() if t.strip()]
    if hashtags:
        description = (description + " " + " ".join(f"#{t}" for t in hashtags)).strip()

    def progress(stage: str, pct: int, message: str) -> None:
        ctx.progress(max(5, min(99, pct)), stage, message)

    result = tiktok_browser.upload_video(str(path), description=description[:2000],
                                         progress=progress)
    publish_guard.record_upload(path, "tiktok", {
        "url": result.get("url"), "status": result.get("status") or "done", "at": time.time(),
    })
    return {
        "_message": "Upload TikTok selesai",
        "upload": result,
        "clips": [{"file_path": str(path), "title": meta.get("title") or Path(path).stem,
                   "upload": result}],
    }


def _auto_upload_meta(transcript: str, preset_hashtags: str = "") -> Dict:
    """Buat judul + deskripsi upload dari transkrip klip. Tidak melempar."""
    if not (transcript or "").strip():
        return {}
    prompt = (
        "Buat metadata upload YouTube Shorts untuk klip berikut.\n"
        "Balas HANYA JSON: {\"title\": str, \"description\": str}\n"
        "- title: maksimal 60 karakter, memancing rasa penasaran, tanpa emoji berlebihan.\n"
        "- description: 2 kalimat + 5 hashtag relevan di akhir.\n"
        f"- Hashtag wajib ikut kalau ada: {preset_hashtags or '(bebas)'}\n\n"
        f"Transkrip:\n{transcript[:4000]}"
    )
    try:
        from . import ai_providers as _ai

        raw = _ai.complete(prompt, json_mode=True, max_tokens=500)
        data = _ai.parse_json(raw)
        title = str(data.get("title") or "").strip()[:100]
        description = str(data.get("description") or "").strip()[:4500]
        return {"title": title, "description": description} if title or description else {}
    except Exception as exc:  # noqa: BLE001 - metadata opsional, jangan gagalkan render
        logger.warning(f"auto metadata gagal ({str(exc)[:160]}); memakai nama file")
        return {}


def _asset_meta(path: str) -> Dict:
    """Metadata aset untuk path ini (judul, caption, hashtag), kalau ada."""
    try:
        target = str(Path(path))
        for asset in db.list_assets(kind="clip", limit=500):
            if str(Path(asset.get("path") or "")) == target:
                meta = dict(asset.get("meta") or {})
                meta.setdefault("title", asset.get("name") or "")
                return meta
    except Exception:  # noqa: BLE001 - metadata opsional
        pass
    return {}


# Mode Live (v6.4): daftarkan handler live_clip & live_window
from . import live  # noqa: E402,F401

# Live spike presisi: buffer HLS + rolling-median chat detector (dari Clipper)
from . import live_spike  # noqa: E402,F401
