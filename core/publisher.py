"""
Publisher module (v3): paket upload siap-unggah + auto-upload opsional.

Fitur:
- build_publish_package(): susun folder publish/ berisi video, thumbnail,
  caption per platform, rencana posting, dan varian caption A/B test.
- upload_to_youtube(): auto-upload ke YouTube Shorts via YouTube Data API v3
  (butuh client_secret.json OAuth — dependensi opsional).
- upload_to_tiktok(): auto-upload TikTok via CLI `tiktok-uploader` jika
  terpasang; jika tidak, menghasilkan panduan langkah manual.

Semua dependensi eksternal bersifat opsional: fungsi tetap bisa dipanggil dan
akan memberikan pesan bantuan yang jelas bila library tidak terpasang.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger

import config
from .fyp_agent import build_upload_strategy, get_best_upload_schedule, suggest_caption_variants

ALL_PLATFORMS = ["TikTok", "Instagram Reels", "YouTube Shorts"]


def sanitize_name(name: str) -> str:
    """Bersihkan nama folder/file dari karakter ilegal lintas OS."""
    return re.sub(r'[\\/:*?"<>|]+', "_", str(name)).strip() or "clip"


def build_publish_package(
    clips: List[Dict],
    output_dir,
    platforms: Optional[List[str]] = None,
) -> Path:
    """
    Susun paket upload siap-unggah di <output_dir>/publish/:

    publish/
    ├── video/                      # mp4 + thumbnail (semua platform)
    ├── <Platform>/upload_captions.json
    ├── publish_plan.json           # urutan upload, jam terbaik, tips
    └── README.txt                  # panduan upload manual singkat
    """
    if not clips:
        raise ValueError("Tidak ada klip untuk dipaketkan.")

    platforms = platforms or ALL_PLATFORMS
    output_dir = Path(output_dir)
    publish_dir = output_dir / config.PUBLISH_DIR_NAME
    video_dir = publish_dir / "video"
    video_dir.mkdir(parents=True, exist_ok=True)

    copied_files: Dict[str, str] = {}

    def _copy_once(src_path: str) -> str:
        src = Path(src_path)
        if not src.exists():
            return ""
        key = str(src)
        if key not in copied_files:
            dest = video_dir / src.name
            if dest != src:
                shutil.copy2(src, dest)
            copied_files[key] = dest.name
        return copied_files[key]

    plan = build_upload_strategy(clips, target_platform="Semua")
    manifest = []
    for clip in clips:
        file_name = _copy_once(clip.get("file_path", ""))
        thumb_name = _copy_once(clip.get("thumbnail", ""))
        for platform in platforms:
            platform_copy = clip.get("platform_copy", {}).get(platform, {})
            entry = {
                "file": file_name,
                "thumbnail": thumb_name,
                "score": clip.get("score"),
                "category": clip.get("category"),
                "niche": clip.get("niche", "general"),
                "fyp_grade": clip.get("fyp_grade"),
                "duration": clip.get("duration"),
                "title": platform_copy.get("title", ""),
                "caption": platform_copy.get("caption", ""),
                "hashtags": platform_copy.get("hashtags", []),
                "ab_test_variants": suggest_caption_variants(clip, platform),
                "safe_caption_zone": platform_copy.get("safe_caption_zone"),
                "duration_fit": platform_copy.get("duration_fit", True),
                "best_upload_times": get_best_upload_schedule(
                    clip.get("niche", "general"), platform
                ).get("best_times", []),
            }
            manifest.append({"platform": platform, **entry})

    for platform in platforms:
        platform_dir = publish_dir / sanitize_name(platform)
        platform_dir.mkdir(exist_ok=True)
        platform_entries = [entry for entry in manifest if entry["platform"] == platform]
        with open(platform_dir / "upload_captions.json", "w", encoding="utf-8") as f:
            json.dump(platform_entries, f, indent=2, ensure_ascii=False)

    plan_payload = {
        "generated_by": "Viral Auto Klip v3",
        "target_platforms": platforms,
        "upload_strategy": plan,
        "ab_test_variants": {
            clip.get("file_path", f"clip_{index}"): {
                platform: suggest_caption_variants(clip, platform)
                for platform in platforms
            }
            for index, clip in enumerate(clips)
        },
    }
    with open(publish_dir / "publish_plan.json", "w", encoding="utf-8") as f:
        json.dump(plan_payload, f, indent=2, ensure_ascii=False)

    (publish_dir / "README.txt").write_text(_manual_upload_guide(platforms), encoding="utf-8")

    logger.success(f"📤 Publish package siap: {publish_dir}")
    return publish_dir


def _manual_upload_guide(platforms: List[str]) -> str:
    lines = [
        "VIRAL AUTO KLIP v3 — PANDUAN UPLOAD",
        "=" * 44,
        "",
        "File video siap upload ada di folder: video/",
        "Caption + hashtag + jam upload terbaik ada di: <Platform>/upload_captions.json",
        "Rencana urutan upload & A/B test ada di: publish_plan.json",
        "",
    ]
    if "TikTok" in platforms:
        lines += [
            "[TIKTOK]",
            "1. Buka tiktok.com/upload (web) atau aplikasi TikTok.",
            "2. Upload video dari folder video/, tempel caption + hashtag dari TikTok/upload_captions.json.",
            "3. Auto-upload opsional: pip install tiktok-uploader, export cookies TikTok ke cookies.txt",
            "   (isi TIKTOK_COOKIES_FILE di .env), lalu: python cli.py --upload-from <folder_output> --upload-tiktok",
            "",
        ]
    if "YouTube Shorts" in platforms:
        lines += [
            "[YOUTUBE SHORTS]",
            "1. Buka studio.youtube.com -> Create -> Short.",
            "2. Upload video, tempel title/description/tag dari YouTube Shorts/upload_captions.json.",
            "3. Auto-upload: buat OAuth client di console.cloud.google.com (YouTube Data API v3),",
            "   simpan sebagai client_secret.json, lalu:",
            "   python cli.py --upload-from <folder_output> --upload-youtube --youtube-client-secret client_secret.json",
            "",
        ]
    if "Instagram Reels" in platforms:
        lines += [
            "[INSTAGRAM REELS]",
            "1. Buka instagram.com -> Create -> Reel.",
            "2. Upload video, tempel caption + hashtag dari Instagram Reels/upload_captions.json.",
            "   (Instagram tidak punya API upload publik untuk pihak ketiga biasa.)",
            "",
        ]
    lines += ["Upload 2-4 klip terbaik di jam berbeda, lalu ulangi pola kategori yang retensinya paling tinggi."]
    return "\n".join(lines)


def upload_to_youtube(
    video_path: str,
    title: str,
    description: str = "",
    tags: Optional[List[str]] = None,
    privacy: Optional[str] = None,
    client_secrets_path: Optional[str] = None,
    category_id: Optional[str] = None,
) -> Dict:
    """
    Upload satu video ke YouTube (Shorts otomatis bila durasi < 3 menit & vertikal).

    Butuh dependensi opsional:
        pip install google-api-python-client google-auth-oauthlib google-auth-httplib2
    dan client_secret.json OAuth (Desktop app, scope youtube.upload).
    Token disimpan di <BASE_DIR>/.yt_upload_token.json (jangan di-commit).
    """
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
    except ImportError as exc:
        raise RuntimeError(
            "Dependensi YouTube upload belum terpasang. Jalankan: "
            "pip install google-api-python-client google-auth-oauthlib google-auth-httplib2"
        ) from exc

    scopes = ["https://www.googleapis.com/auth/youtube.upload"]
    token_path = config.BASE_DIR / config.YOUTUBE_OAUTH_TOKEN_FILE
    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), scopes)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            secrets_path = client_secrets_path or config.YOUTUBE_CLIENT_SECRET_FILE
            if not secrets_path or not Path(secrets_path).exists():
                raise RuntimeError(
                    "Butuh client_secret.json YouTube Data API v3. Buat OAuth client di "
                    "console.cloud.google.com (Desktop app, scope youtube.upload), simpan "
                    "file-nya, lalu panggil dengan client_secrets_path=<path>."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path), scopes)
            creds = flow.run_local_server(port=0, prompt="consent")
        token_path.write_text(creds.to_json())
        logger.info(f"Token OAuth YouTube tersimpan di {token_path} (jangan di-commit!)")

    service = build("youtube", "v3", credentials=creds)
    clean_tags = [tag.lstrip("#").strip() for tag in (tags or []) if tag.strip()]
    body = {
        "snippet": {
            "title": str(title)[:100],
            "description": str(description)[:4900],
            "tags": clean_tags[:20],
            "categoryId": category_id or config.YOUTUBE_CATEGORY_ID,
        },
        "status": {
            "privacyStatus": (privacy or config.YOUTUBE_PRIVACY) or "public",
            "selfDeclaredMadeForKids": False,
        },
    }
    media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True)
    request = service.videos().insert(part="snippet,status", body=body, media_body=media)
    logger.info(f"⬆️ Uploading {Path(video_path).name} ke YouTube ({body['status']['privacyStatus']})...")
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            logger.info(f"  YouTube upload progress: {int(status.progress() * 100)}%")
    video_id = response["id"]
    result = {
        "video_id": video_id,
        "url": f"https://youtube.com/shorts/{video_id}",
        "title": body["snippet"]["title"],
    }
    logger.success(f"✅ YouTube upload selesai: {result['url']}")
    return result


def load_clips_from_output(output_folder) -> List[Dict]:
    """Baca metadata.json dari folder hasil render (untuk upload ulang TANPA memproses video lagi)."""
    meta_path = Path(output_folder) / "metadata.json"
    if not meta_path.exists():
        raise FileNotFoundError(f"metadata.json tidak ditemukan di {output_folder}")
    clips = json.loads(meta_path.read_text(encoding="utf-8")).get("clips") or []
    return [c for c in clips if c.get("file_path") and Path(c["file_path"]).exists()]


def upload_clips(
    clips: List[Dict],
    youtube: bool = False,
    tiktok: bool = False,
    client_secrets_path: Optional[str] = None,
    limit: int = 0,
) -> List[Dict]:
    """Upload klip yang SUDAH jadi ke YouTube Shorts / TikTok, urut dari skor tertinggi."""
    ordered = sorted(clips, key=lambda c: c.get("score", 0), reverse=True)
    if limit:
        ordered = ordered[:limit]
    report = []
    for clip in ordered:
        entry: Dict = {"file": clip["file_path"], "score": clip.get("score")}
        if youtube:
            pc = clip.get("platform_copy", {}).get("YouTube Shorts", {})
            try:
                entry["youtube"] = upload_to_youtube(
                    clip["file_path"],
                    title=pc.get("title") or clip.get("ai_title") or clip.get("hook_text", "Viral clip"),
                    description=pc.get("caption") or clip.get("upload_caption", ""),
                    tags=pc.get("hashtags") or clip.get("hashtags", []),
                    client_secrets_path=client_secrets_path,
                )
            except Exception as exc:
                entry["youtube"] = {"status": "failed", "reason": str(exc)[:300]}
                logger.error(f"YouTube upload gagal ({Path(clip['file_path']).name}): {exc}")
        if tiktok:
            pc = clip.get("platform_copy", {}).get("TikTok", {})
            caption = pc.get("caption") or clip.get("upload_caption", "")
            tags = " ".join(pc.get("hashtags") or clip.get("hashtags", []))
            entry["tiktok"] = upload_to_tiktok(clip["file_path"], description=f"{caption} {tags}".strip())
        report.append(entry)
    return report


def upload_to_tiktok(video_path: str, description: str = "", cookies_file: str = "") -> Dict:
    """
    Auto-upload TikTok via paket `tiktok-uploader` (cookie sesi TikTok wajib).

    FIX v4: dulu memanggil CLI dengan sintaks karangan (`upload --video`). Sekarang memakai
    API Python resmi paket itu (tiktok_uploader.upload.upload_video) bila terpasang, lalu
    CLI-nya (`tiktok-uploader -v <video> -d <deskripsi> -c <cookies>`) sebagai cadangan.
    """
    cookies = cookies_file or config.TIKTOK_COOKIES_FILE
    if not cookies or not Path(cookies).exists():
        return {
            "status": "skipped",
            "reason": "cookies.txt TikTok belum diatur (TIKTOK_COOKIES_FILE)",
            "instructions": "Export cookies tiktok.com (format Netscape) ke cookies.txt lalu isi TIKTOK_COOKIES_FILE di .env.",
        }
    try:
        from tiktok_uploader.upload import upload_video  # type: ignore
    except ImportError:
        upload_video = None
    if upload_video is not None:
        try:
            failed = upload_video(str(video_path), description=description, cookies=str(cookies))
            if failed:
                return {"status": "failed", "reason": str(failed)[:500]}
            logger.success(f"✅ TikTok upload selesai: {Path(video_path).name}")
            return {"status": "done"}
        except Exception as exc:
            return {"status": "failed", "reason": str(exc)[:500]}

    executable = config.TIKTOK_UPLOADER_PATH or shutil.which("tiktok-uploader")
    if not executable:
        return {
            "status": "skipped",
            "reason": "tiktok-uploader tidak terpasang",
            "instructions": (
                "pip install tiktok-uploader, atau upload manual: buka tiktok.com/upload "
                "dan tempel caption dari upload_captions.json."
            ),
        }
    cmd = [executable, "-v", str(video_path), "-c", str(cookies)]
    if description:
        cmd += ["-d", description]
    try:
        completed = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if completed.returncode == 0:
            logger.success(f"✅ TikTok upload selesai: {Path(video_path).name}")
            return {"status": "done", "output": (completed.stdout or "")[-500:]}
        return {
            "status": "failed",
            "reason": (completed.stderr or completed.stdout or "")[-500:],
        }
    except Exception as exc:
        return {"status": "failed", "reason": str(exc)[:500]}
