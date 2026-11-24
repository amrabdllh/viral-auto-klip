"""
Ambil bahan teks dari luar: info & subtitle YouTube (tanpa download video), artikel web, PDF/TXT/SRT.
"""
from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Dict, List, Optional

import requests
from loguru import logger

import config

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36"}


def _ydl_opts(extra: Optional[Dict] = None) -> Dict:
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True}
    cookies = Path(config.YTDLP_COOKIES) if config.YTDLP_COOKIES else None
    if cookies and not cookies.is_absolute():
        cookies = config.BASE_DIR / cookies
    if cookies and cookies.exists():
        opts["cookiefile"] = str(cookies)
    opts.update(extra or {})
    return opts


_INFO_CACHE: Dict[str, tuple] = {}


def video_info(url: str) -> Dict:
    """Metadata video (judul, channel, tag, …). Di-cache 15 menit: satu job memanggilnya beberapa kali
    (judul untuk Editor-AI, channel untuk kredit, keyterm transkripsi) — tiap panggilan = request ke YouTube."""
    import copy
    import time as _time
    hit = _INFO_CACHE.get(url)
    if hit and _time.time() - hit[0] < 900:
        return copy.deepcopy(hit[1])
    out = _video_info(url)
    _INFO_CACHE[url] = (_time.time(), out)
    return copy.deepcopy(out)


def _video_info(url: str) -> Dict:
    import yt_dlp
    with yt_dlp.YoutubeDL(_ydl_opts()) as ydl:
        info = ydl.extract_info(url, download=False)
    thumbs = sorted(info.get("thumbnails") or [], key=lambda t: (t.get("width") or 0) * (t.get("height") or 0))
    return {
        "id": info.get("id"), "title": info.get("title") or "", "description": info.get("description") or "",
        "channel": info.get("channel") or info.get("uploader") or "", "duration": info.get("duration") or 0,
        "view_count": info.get("view_count"), "like_count": info.get("like_count"),
        "comment_count": info.get("comment_count"), "upload_date": info.get("upload_date"),
        "tags": info.get("tags") or [], "thumbnail": info.get("thumbnail") or (thumbs[-1]["url"] if thumbs else ""),
        "thumbnails": [{"url": t["url"], "w": t.get("width"), "h": t.get("height")} for t in thumbs if t.get("url")][-6:],
        "subtitles": info.get("subtitles") or {}, "automatic_captions": info.get("automatic_captions") or {},
        "webpage_url": info.get("webpage_url") or url,
    }


def _vtt_to_segments(vtt: str) -> List[Dict]:
    segs: List[Dict] = []
    ts = re.compile(r"(\d+):(\d{2}):(\d{2})[.,](\d{3})\s+-->\s+(\d+):(\d{2}):(\d{2})[.,](\d{3})")
    blocks = re.split(r"\n\s*\n", vtt.replace("\r", ""))
    last_text = ""
    for block in blocks:
        lines = block.strip().split("\n")
        for i, line in enumerate(lines):
            m = ts.search(line)
            if not m:
                continue
            g = list(map(int, m.groups()))
            start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
            end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
            text = " ".join(re.sub(r"<[^>]+>", "", l).strip() for l in lines[i + 1:]).strip()
            text = html.unescape(text)
            if text and text != last_text:
                # subtitle otomatis YouTube mengulang baris sebelumnya — buang bagian yang berulang
                if last_text and text.startswith(last_text):
                    text = text[len(last_text):].strip()
                if text:
                    segs.append({"start": start, "end": end, "text": text})
                    last_text = text
            break
    return segs


def youtube_transcript(url: str, langs: Optional[List[str]] = None) -> Dict:
    """Subtitle YouTube (manual → otomatis). Return {info, segments, text, language, source}."""
    info = video_info(url)
    langs = langs or ["id", "en", "ms"]
    for bucket_name in ("subtitles", "automatic_captions"):
        bucket = info.get(bucket_name) or {}
        keys = list(bucket.keys())
        ordered = [k for lang in langs for k in keys if k == lang or k.startswith(lang + "-")]
        ordered += [k for k in keys if k not in ordered and not k.startswith("live_chat")]
        for lang in ordered[:6]:
            tracks = bucket.get(lang) or []
            vtt = next((t for t in tracks if t.get("ext") == "vtt"), None)
            if not vtt:
                continue
            try:
                resp = requests.get(vtt["url"], headers=_UA, timeout=60)
                resp.raise_for_status()
                segments = _vtt_to_segments(resp.text)
                if segments:
                    info.pop("subtitles", None); info.pop("automatic_captions", None)
                    return {"info": info, "segments": segments, "text": " ".join(s["text"] for s in segments),
                            "language": lang, "source": bucket_name}
            except Exception as exc:
                logger.warning(f"Subtitle {lang} gagal: {exc}")
    info.pop("subtitles", None); info.pop("automatic_captions", None)
    return {"info": info, "segments": [], "text": "", "language": "", "source": "none"}


def fetch_article(url: str, max_chars: int = 20000) -> Dict:
    resp = requests.get(url, headers=_UA, timeout=45)
    resp.raise_for_status()
    raw = resp.text
    title = html.unescape(re.search(r"<title[^>]*>(.*?)</title>", raw, re.S | re.I).group(1).strip()) if re.search(r"<title", raw, re.I) else ""
    body = re.sub(r"(?is)<(script|style|noscript|nav|footer|header|aside|form)[^>]*>.*?</\1>", " ", raw)
    paras = re.findall(r"(?is)<(?:p|h1|h2|h3|li)[^>]*>(.*?)</(?:p|h1|h2|h3|li)>", body)
    text = "\n".join(html.unescape(re.sub(r"<[^>]+>", "", p)).strip() for p in paras)
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    if len(text) < 300:
        text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", body))).strip()
    return {"title": title, "text": text[:max_chars], "url": url}


def read_document(path: str, max_chars: int = 40000) -> str:
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError:
            raise RuntimeError("Untuk PDF jalankan: pip install pypdf")
        reader = PdfReader(str(p))
        text = "\n".join((page.extract_text() or "") for page in reader.pages[:80])
    elif suffix in (".srt", ".vtt"):
        raw = p.read_text(encoding="utf-8", errors="replace")
        text = " ".join(s["text"] for s in _vtt_to_segments(raw.replace(",", ".") if suffix == ".srt" else raw))
    else:
        text = p.read_text(encoding="utf-8", errors="replace")
    return text[:max_chars]


def segments_to_srt(segments: List[Dict]) -> str:
    def ts(t):
        t = max(0.0, float(t))
        h, rem = divmod(t, 3600)
        m, s = divmod(rem, 60)
        return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int(round((s - int(s)) * 1000)):03d}"
    out = []
    for i, seg in enumerate(segments, 1):
        out.append(f"{i}\n{ts(seg['start'])} --> {ts(seg['end'])}\n{seg['text'].strip()}\n")
    return "\n".join(out)


def segments_to_timestamped(segments: List[Dict]) -> str:
    lines = []
    for seg in segments:
        m, s = divmod(int(seg["start"]), 60)
        h, m = divmod(m, 60)
        stamp = f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
        lines.append(f"[{stamp}] {seg['text'].strip()}")
    return "\n".join(lines)


def youtube_search(query: str, limit: int = 20) -> List[Dict]:
    """Pencarian YouTube via yt-dlp (tanpa API key)."""
    import yt_dlp
    with yt_dlp.YoutubeDL(_ydl_opts({"extract_flat": "in_playlist"})) as ydl:
        data = ydl.extract_info(f"ytsearch{int(limit)}:{query}", download=False)
    out = []
    for e in data.get("entries") or []:
        if not e:
            continue
        out.append({"id": e.get("id"), "title": e.get("title") or "", "channel": e.get("channel") or e.get("uploader") or "",
                    "views": e.get("view_count") or 0, "duration": e.get("duration") or 0,
                    "url": e.get("url") or f"https://www.youtube.com/watch?v={e.get('id')}",
                    "thumbnail": (e.get("thumbnails") or [{}])[-1].get("url", "") if e.get("thumbnails") else "",
                    "upload_date": e.get("upload_date")})
    return out
