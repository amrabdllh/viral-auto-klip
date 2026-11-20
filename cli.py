#!/usr/bin/env python3
"""
Viral Auto Klip v4 - Command Line Interface

Contoh:
  python cli.py --url "https://youtube.com/..." --num-clips 10 --use-llm --ai-provider Anthropic
  python cli.py --file video.mp4 --use-llm --ai-provider Custom --ai-base-url https://host/v1 --ai-api-key KEY --ai-model nama-model
  python cli.py --url "..." --transcriber elevenlabs           # transkripsi ElevenLabs Scribe
  python cli.py --google-login                                  # login Google untuk provider Antigravity
  python cli.py --list-providers                                # status semua provider AI
  python cli.py --upload-from output/klip_xxx --upload-youtube  # upload klip yang SUDAH jadi
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import config
from core.utils import setup_logging
from loguru import logger

setup_logging()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Viral Auto Klip v4 - AI Viral Video Clipper")
    src = p.add_argument_group("Sumber")
    src.add_argument("--url", type=str, help="Video URL (YouTube, TikTok, IG, FB, X, MP4)")
    src.add_argument("--file", type=str, help="File video lokal")
    src.add_argument("--output", type=str, default=None, help="Folder output (default: output/klip_<timestamp>)")
    src.add_argument("--cookies-from-browser", type=str, default="", choices=["", "chrome", "edge", "firefox", "brave", "opera"])
    src.add_argument("--cookies-file", type=str, default="", help="cookies.txt untuk yt-dlp")

    clip = p.add_argument_group("Klip")
    clip.add_argument("--num-clips", type=int, default=8)
    clip.add_argument("--min-score", type=float, default=60)
    clip.add_argument("--min-duration", type=int, default=15)
    clip.add_argument("--max-duration", type=int, default=60)
    clip.add_argument("--caption-style", type=str, default="modern",
                      choices=["viral", "modern", "minimal", "karaoke", "emoji", "tiktok_bold", "gradient", "neon"])
    clip.add_argument("--no-captions", action="store_true", help="Render tanpa caption")
    clip.add_argument("--edit-style", type=str, default="", choices=["", "minimal", "dinamis", "maksimal"],
                      help="Gaya editing: minimal (potong+caption), dinamis (jump cut, zoom, progress bar), maksimal (+SFX, warna vibrant)")
    clip.add_argument("--color-grade", type=str, default=None, choices=["none", "natural", "vibrant", "cinematic"])
    clip.add_argument("--no-jump-cuts", action="store_true", help="Jangan buang jeda/kata pengisi")
    clip.add_argument("--face-reframe", action="store_true")
    clip.add_argument("--watermark", action="store_true")
    clip.add_argument("--target-platform", type=str, default="Semua", choices=["TikTok", "Instagram Reels", "YouTube Shorts", "Semua"])
    clip.add_argument("--scene-snap", type=str, default="auto", choices=["auto", "on", "off"])
    clip.add_argument("--parallel", type=int, default=config.PARALLEL_CLIPS, help="Klip dirender paralel (0/1 = sekuensial)")

    aig = p.add_argument_group("AI")
    aig.add_argument("--use-llm", action="store_true", help="Aktifkan AI (moment finder + rerank + packaging)")
    aig.add_argument("--ai-provider", type=str, default=config.AI_PROVIDER, choices=config.ALL_AI_PROVIDERS)
    aig.add_argument("--ai-model", type=str, default="", help="Model AI (kosong = default provider)")
    aig.add_argument("--ai-api-key", type=str, default="", help="API key untuk provider terpilih")
    aig.add_argument("--ai-base-url", type=str, default="", help="Base URL (Custom / Ollama / proxy)")
    aig.add_argument("--no-moment-finder", action="store_true", help="Matikan AI Moment Finder")
    aig.add_argument("--no-ai-packaging", action="store_true", help="Matikan hook/judul/caption buatan AI")
    aig.add_argument("--list-providers", action="store_true", help="Tampilkan status semua provider AI lalu keluar")
    aig.add_argument("--test-ai", action="store_true", help="Tes koneksi provider AI terpilih lalu keluar")
    aig.add_argument("--detect-models", action="store_true", help="Tampilkan model yang tersedia untuk API key provider terpilih lalu keluar")
    aig.add_argument("--google-login", action="store_true", help="Login Google untuk provider Antigravity lalu keluar")
    aig.add_argument("--google-client-secret", type=str, default="", help="client_secret.json OAuth Desktop (Antigravity)")
    # kompatibilitas opsi lama
    aig.add_argument("--groq-api-key", type=str, default="", help=argparse.SUPPRESS)
    aig.add_argument("--gemini-api-key", type=str, default="", help=argparse.SUPPRESS)
    aig.add_argument("--openrouter-api-key", type=str, default="", help=argparse.SUPPRESS)
    aig.add_argument("--ollama-base-url", type=str, default="", help=argparse.SUPPRESS)

    tr = p.add_argument_group("Transkripsi")
    tr.add_argument("--transcriber", type=str, default="", choices=["", "local", "groq", "elevenlabs"],
                    help="local = Whisper di laptop; groq = Groq Whisper cloud; elevenlabs = ElevenLabs Scribe")
    tr.add_argument("--elevenlabs-api-key", type=str, default="")
    tr.add_argument("--language", type=str, default=None, help="Kode bahasa video (id, en, ...). Kosong = otomatis")
    tr.add_argument("--whisper-model", type=str, default=config.WHISPER_MODEL)
    tr.add_argument("--whisper-device", type=str, default=config.WHISPER_DEVICE, choices=["cuda", "cpu", "auto"])
    tr.add_argument("--whisper-compute-type", type=str, default=config.WHISPER_COMPUTE_TYPE)

    pub = p.add_argument_group("Publish")
    pub.add_argument("--publish-package", action="store_true", help="Buat folder publish/ siap upload")
    pub.add_argument("--upload-youtube", action="store_true", help="Upload otomatis ke YouTube Shorts")
    pub.add_argument("--upload-tiktok", action="store_true", help="Upload otomatis ke TikTok (tiktok-uploader + cookies)")
    pub.add_argument("--upload-limit", type=int, default=0, help="Hanya upload N klip skor tertinggi (0 = semua)")
    pub.add_argument("--youtube-client-secret", type=str, default="")
    pub.add_argument("--upload-from", type=str, default="",
                     help="Upload klip yang SUDAH dirender dari folder output (tanpa proses ulang)")
    return p


def configure_ai(args) -> bool:
    from core.analyzer import configure_llm_clients
    return configure_llm_clients(
        provider=args.ai_provider,
        model=args.ai_model,
        groq_api_key=args.groq_api_key,
        gemini_api_key=args.gemini_api_key,
        openrouter_api_key=args.openrouter_api_key,
        ollama_base_url=args.ollama_base_url,
        api_key=args.ai_api_key,
        base_url=args.ai_base_url,
    )


def main():
    parser = build_parser()
    args = parser.parse_args()

    from core import ai_providers as ai

    if args.google_client_secret:
        config.GOOGLE_AI_CLIENT_SECRET_FILE = args.google_client_secret
    if args.list_providers:
        for row in ai.provider_status():
            mark = "✅" if row["configured"] else "—"
            logger.info(f"{mark} {row['provider']:<12} {row['label']} | model: {', '.join(row['models']) or '(isi sendiri)'}")
        return
    if args.google_login:
        status = ai.google_login(args.google_client_secret)
        logger.success(f"Login Google OK. Token: {status['token_file']}")
        return
    if args.detect_models:
        try:
            found = ai.detect_models(args.ai_provider, api_key=args.ai_api_key, base_url=args.ai_base_url)
        except Exception as exc:
            logger.error(f"Deteksi model gagal: {exc}")
            sys.exit(1)
        logger.success(f"{found['total']} model chat untuk {args.ai_provider} (rekomendasi: {found['recommended'] or '-'})")
        for model in found["models"]:
            logger.info(f"  {'★' if model == found['recommended'] else ' '} {model}")
        return
    if args.test_ai:
        configure_ai(args)
        ok, message = ai.test_connection(args.ai_provider, config.LLM_MODEL)
        (logger.success if ok else logger.error)(f"{args.ai_provider}/{config.LLM_MODEL}: {message}")
        sys.exit(0 if ok else 1)

    # Mode upload saja (klip sudah jadi)
    if args.upload_from:
        from core.publisher import load_clips_from_output, upload_clips
        clips = load_clips_from_output(args.upload_from)
        if not (args.upload_youtube or args.upload_tiktok):
            parser.error("--upload-from butuh --upload-youtube dan/atau --upload-tiktok")
        report = upload_clips(clips, youtube=args.upload_youtube, tiktok=args.upload_tiktok,
                              client_secrets_path=args.youtube_client_secret, limit=args.upload_limit)
        out = Path(args.upload_from) / f"upload_report_{datetime.now():%Y%m%d_%H%M%S}.json"
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.success(f"Upload selesai untuk {len(report)} klip. Laporan: {out}")
        return

    if not args.url and not args.file:
        parser.error("Butuh --url atau --file (atau --upload-from / --list-providers / --google-login)")

    from core.analyzer import analyze_viral_potential
    from core.clipper import process_clips
    from core.transcriber import transcribe_video
    from core.utils import download_video, get_video_info

    # Override config
    config.MIN_VIRAL_SCORE = args.min_score
    config.MIN_CLIP_DURATION = args.min_duration
    config.MAX_CLIP_DURATION = max(args.max_duration, args.min_duration + 5)
    config.MAX_CLIPS_PER_VIDEO = args.num_clips
    config.WHISPER_MODEL = args.whisper_model
    if args.language is not None:
        config.WHISPER_LANGUAGE = args.language.strip().lower()
    if args.elevenlabs_api_key:
        config.ELEVENLABS_API_KEY = args.elevenlabs_api_key.strip()
    if args.scene_snap != "auto":
        config.SCENE_SNAP_ENABLED = args.scene_snap == "on"
    config.PARALLEL_CLIPS = max(1, int(args.parallel))
    config.PARALLEL_PROCESSING = args.parallel > 1
    if args.no_moment_finder:
        config.AI_MOMENT_FINDER = False
    if args.no_ai_packaging:
        config.AI_CLIP_PACKAGING = False

    output_dir = Path(args.output) if args.output else config.OUTPUT_DIR / f"klip_{datetime.now():%Y%m%d_%H%M%S}"
    output_dir.mkdir(parents=True, exist_ok=True)
    logger.info("🚀 Starting Viral Auto Klip v4 CLI")

    use_llm = args.use_llm
    if use_llm and not configure_ai(args):
        logger.warning(f"AI diminta tapi {args.ai_provider} belum dikonfigurasi (API key/base URL/login). Lanjut scoring lokal.")
        use_llm = False

    # 1. Video
    if args.url:
        video_path = download_video(args.url, output_dir / "source",
                                    cookies_from_browser=args.cookies_from_browser, cookies_file=args.cookies_file)
    else:
        video_path = args.file
        if not Path(video_path).exists():
            logger.error(f"File not found: {video_path}")
            sys.exit(1)

    # 2. Transkripsi
    device = {"groq": "groq", "elevenlabs": "elevenlabs", "local": args.whisper_device}.get(args.transcriber, args.whisper_device)
    transcript, transcription_info = transcribe_video(
        video_path, model_name=config.WHISPER_MODEL, word_timestamps=True,
        device=device, compute_type=args.whisper_compute_type,
    )
    logger.info(f"Transcription: {transcription_info['device']} ({transcription_info['compute_type']}) lang={transcription_info.get('language')}")

    # 3. Analisis
    clips = analyze_viral_potential(transcript, get_video_info(video_path), use_llm=use_llm, video_path=video_path)
    if not clips:
        logger.warning("No high-viral clips found. Try lowering --min-score")
        sys.exit(0)
    logger.info(f"Found {len(clips)} potential clips. Processing top {args.num_clips}...")

    # 4. Render
    processed = process_clips(
        video_path, clips[:args.num_clips], output_dir,
        style_name=args.caption_style, enable_face_reframe=args.face_reframe,
        add_watermark=args.watermark, target_platform=args.target_platform,
        enable_captions=not args.no_captions,
        edit_opts={"style": args.edit_style, "color_grade": args.color_grade,
                   "jump_cuts": False if args.no_jump_cuts else None},
    )

    # 5. Publish + upload
    publish_folder = None
    if args.publish_package and processed:
        from core.publisher import build_publish_package
        platforms = [args.target_platform] if args.target_platform != "Semua" else None
        publish_folder = build_publish_package(processed, output_dir, platforms=platforms)
        logger.success(f"📤 Paket publish siap: {publish_folder}")

    upload_report = []
    if (args.upload_youtube or args.upload_tiktok) and processed:
        from core.publisher import upload_clips
        upload_report = upload_clips(processed, youtube=args.upload_youtube, tiktok=args.upload_tiktok,
                                     client_secrets_path=args.youtube_client_secret, limit=args.upload_limit)

    # 6. Ringkasan
    logger.success(f"\n🎉 Done! {len(processed)} clips created in {output_dir}")
    for i, c in enumerate(processed[:5], 1):
        logger.info(f"  {i}. Score {c['score']} | {c['category']} | {c['duration']:.1f}s | {str(c.get('reason', ''))[:60]}")
        logger.info(f"     Hook: {c.get('hook_text', '-')} | Grade: {c.get('fyp_grade', '-')}")
        logger.info(f"     Caption: {c.get('upload_caption', '-')}")
        logger.info(f"     Hashtags: {' '.join(c.get('hashtags', [])) or '-'}")

    summary = {
        "source": str(video_path),
        "clips_created": len(processed),
        "average_score": round(sum(c["score"] for c in processed) / len(processed), 1) if processed else 0,
        "ai": f"{config.AI_PROVIDER}/{config.LLM_MODEL}" if use_llm else "local-only",
        "transcription": transcription_info,
        "best_upload_caption": processed[0].get("upload_caption") if processed else "",
        "best_hashtags": processed[0].get("hashtags", []) if processed else [],
        "output_folder": str(output_dir),
        "publish_folder": str(publish_folder) if publish_folder else None,
        "uploads": upload_report,
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    logger.info(f"Full metadata: {output_dir / 'metadata.json'}")


if __name__ == "__main__":
    main()
