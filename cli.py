#!/usr/bin/env python3
"""
Viral Auto Klip - Command Line Interface (Powerful for automation & scripting)
Usage examples:
  python cli.py --url "https://youtube.com/..." --num-clips 10 --min-score 65 --face-reframe
  python cli.py --file /path/to/video.mp4 --output ./my_clips
"""
import argparse
import sys
from pathlib import Path
from datetime import datetime
import json

from core.utils import setup_logging, download_video, get_video_info
from core.analyzer import analyze_viral_potential, configure_llm_clients
from core.clipper import process_clips
from core.transcriber import transcribe_video
import config
from loguru import logger

setup_logging()

def main():
    parser = argparse.ArgumentParser(description="Viral Auto Klip - AI Viral Video Clipper")
    parser.add_argument("--url", type=str, help="Video URL (YouTube, TikTok, etc.)")
    parser.add_argument("--file", type=str, help="Local video file path")
    parser.add_argument("--output", type=str, default=None, help="Output directory (default: auto timestamp)")
    parser.add_argument("--num-clips", type=int, default=8, help="Max number of clips")
    parser.add_argument("--min-score", type=float, default=60, help="Minimum viral score (0-100)")
    parser.add_argument("--min-duration", type=int, default=15, help="Min clip duration (seconds)")
    parser.add_argument("--max-duration", type=int, default=60, help="Max clip duration (seconds)")
    parser.add_argument("--caption-style", type=str, default="modern", choices=["modern", "minimal", "karaoke"])
    parser.add_argument("--face-reframe", action="store_true", help="Enable face tracking & reframing")
    parser.add_argument("--use-llm", action="store_true", help="Use Groq/OpenAI for better scoring")
    parser.add_argument("--ai-provider", type=str, default=config.AI_PROVIDER, choices=["Groq", "Gemini", "OpenRouter", "Ollama"], help="AI provider for viral scoring")
    parser.add_argument("--ai-model", type=str, default=config.LLM_MODEL, help="AI model for viral scoring")
    parser.add_argument("--groq-api-key", type=str, default="", help="Groq API key")
    parser.add_argument("--gemini-api-key", type=str, default="", help="Gemini API key")
    parser.add_argument("--openrouter-api-key", type=str, default="", help="OpenRouter API key")
    parser.add_argument("--ollama-base-url", type=str, default=config.OLLAMA_BASE_URL, help="Ollama base URL")
    parser.add_argument("--watermark", action="store_true", help="Add watermark")
    parser.add_argument("--whisper-model", type=str, default=config.WHISPER_MODEL, help="Whisper model size")
    parser.add_argument("--whisper-device", type=str, default=config.WHISPER_DEVICE, choices=["cuda", "cpu", "auto"], help="Use cpu for safest mode, cuda for NVIDIA GPU")
    parser.add_argument("--whisper-compute-type", type=str, default=config.WHISPER_COMPUTE_TYPE, help="Use int8 for safest mode, float16 for CUDA only")
    parser.add_argument("--target-platform", type=str, default="Semua", choices=["TikTok", "Instagram Reels", "YouTube Shorts", "Semua"], help="Generate platform-specific upload copy")
    parser.add_argument("--cookies-from-browser", type=str, default="", choices=["", "chrome", "edge", "firefox", "brave", "opera"], help="Use browser cookies for yt-dlp")
    parser.add_argument("--cookies-file", type=str, default="", help="Path to cookies.txt for yt-dlp")
    
    args = parser.parse_args()
    
    if not args.url and not args.file:
        parser.error("Either --url or --file is required")
    
    # Override config
    config.MIN_VIRAL_SCORE = args.min_score
    config.MIN_CLIP_DURATION = args.min_duration
    config.MAX_CLIP_DURATION = args.max_duration
    config.MAX_CLIPS_PER_VIDEO = args.num_clips
    config.WHISPER_MODEL = args.whisper_model
    config.AI_PROVIDER = args.ai_provider
    config.LLM_MODEL = args.ai_model
    
    # Setup output
    if args.output:
        output_dir = Path(args.output)
    else:
        output_dir = config.OUTPUT_DIR / f"klip_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    logger.info("🚀 Starting Viral Auto Klip CLI")
    
    # 1. Get video
    if args.url:
        logger.info(f"Downloading from {args.url}")
        video_path = download_video(
            args.url,
            output_dir / "source",
            cookies_from_browser=args.cookies_from_browser,
            cookies_file=args.cookies_file,
        )
    else:
        video_path = args.file
        if not Path(video_path).exists():
            logger.error(f"File not found: {video_path}")
            sys.exit(1)
    
    # 2. Transcribe
    logger.info("Transcribing with Whisper...")
    transcript, transcription_info = transcribe_video(
        video_path,
        model_name=config.WHISPER_MODEL,
        word_timestamps=True,
        device=args.whisper_device,
        compute_type=args.whisper_compute_type,
    )
    logger.info(f"Transcription device: {transcription_info['device']} ({transcription_info['compute_type']})")
    
    # 3. Analyze
    logger.info("Analyzing viral potential...")
    if args.use_llm and not configure_llm_clients(
        provider=args.ai_provider,
        model=args.ai_model,
        groq_api_key=args.groq_api_key,
        gemini_api_key=args.gemini_api_key,
        openrouter_api_key=args.openrouter_api_key,
        ollama_base_url=args.ollama_base_url,
    ):
        logger.warning("AI scoring requested but provider/API key is not configured. Continuing with local scoring.")
        args.use_llm = False
    video_info = get_video_info(video_path)
    clips = analyze_viral_potential(transcript, video_info, use_llm=args.use_llm)
    
    if not clips:
        logger.warning("No high-viral clips found. Try lowering --min-score")
        sys.exit(0)
    
    logger.info(f"Found {len(clips)} potential clips. Processing top {args.num_clips}...")
    
    # 4. Process
    processed = process_clips(
        video_path,
        clips[:args.num_clips],
        output_dir,
        style_name=args.caption_style,
        enable_face_reframe=args.face_reframe,
        add_watermark=args.watermark,
        target_platform=args.target_platform
    )
    
    # 5. Summary
    logger.success(f"\n🎉 Done! {len(processed)} clips created in {output_dir}")
    logger.info("Top clips:")
    for i, c in enumerate(processed[:5], 1):
        logger.info(f"  {i}. Score {c['score']} | {c['category']} | {c['duration']:.1f}s | {c['reason'][:60]}...")
        logger.info(f"     Hook: {c.get('hook_text', '-')}")
        logger.info(f"     Caption: {c.get('upload_caption', '-')}")
        logger.info(f"     Hashtags: {' '.join(c.get('hashtags', [])) or '-'}")
        logger.info(f"     FYP Grade: {c.get('fyp_grade', '-')}")
    
    # Save summary
    summary = {
        "source": str(video_path),
        "clips_created": len(processed),
        "average_score": round(sum(c["score"] for c in processed) / len(processed), 1) if processed else 0,
        "best_upload_caption": processed[0].get("upload_caption") if processed else "",
        "best_hashtags": processed[0].get("hashtags", []) if processed else [],
        "output_folder": str(output_dir)
    }
    with open(output_dir / "summary.json", "w") as f:
        json.dump(summary, f, indent=2)
    
    logger.info(f"\nFull metadata: {output_dir / 'metadata.json'}")

if __name__ == "__main__":
    main()
