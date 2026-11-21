"""
FYP packaging agent: platform copy, upload order, and quality checklist.
"""
from typing import Dict, List

import config
from .utils import trim_caption


def enrich_clip_for_platforms(clip: Dict, target_platform: str = "Semua") -> Dict:
    platforms = _resolve_platforms(target_platform)
    platform_copy = {}
    for platform in platforms:
        profile = config.PLATFORM_PROFILES.get(platform, config.PLATFORM_PROFILES["Semua"])
        platform_copy[platform] = _build_platform_copy(clip, platform, profile)

    clip["platform_copy"] = platform_copy
    clip["quality_checklist"] = _build_quality_checklist(clip, platforms)
    clip["fyp_grade"] = _grade_clip(clip)
    return clip


def build_upload_strategy(clips: List[Dict], target_platform: str = "Semua") -> Dict:
    platforms = _resolve_platforms(target_platform)
    ordered = sorted(clips, key=lambda item: item.get("score", 0), reverse=True)
    return {
        "target_platforms": platforms,
        "upload_order": [
            {
                "rank": index + 1,
                "file_path": clip.get("file_path"),
                "score": clip.get("score"),
                "category": clip.get("category"),
                "hook_text": clip.get("hook_text"),
                "best_caption": _first_platform_caption(clip, platforms),
            }
            for index, clip in enumerate(ordered)
        ],
        "batching_tip": "Upload 2-4 klip terbaik di jam berbeda, lalu ulangi pola kategori yang retensinya paling tinggi.",
        "review_checks": config.FYP_AGENT_CHECKS,
        "recommended_schedule": get_best_upload_schedule(ordered[0].get("niche", "general"), platforms[0] if platforms else "TikTok") if ordered else {}
    }


def _resolve_platforms(target_platform: str) -> List[str]:
    if target_platform == "Semua":
        return ["TikTok", "Instagram Reels", "YouTube Shorts"]
    return [target_platform] if target_platform in config.PLATFORM_PROFILES else ["TikTok", "Instagram Reels", "YouTube Shorts"]


def _build_platform_copy(clip: Dict, platform: str, profile: Dict) -> Dict:
    hook = clip.get("hook_text") or "Bagian ini wajib ditonton"
    base_caption = clip.get("upload_caption") or hook
    niche = clip.get("niche", "general")
    score = clip.get("score", 75)
    category = clip.get("category", "general")
    
    hashtags = clip.get("hashtags", [])[: config.MAX_HASHTAGS]
    
    # === VIRAL-OPTIMIZED CONTENT PER PLATFORM ===
    
    if platform == "TikTok":
        # TikTok style: direct, comment bait, emoji heavy
        viral_caption = f"{base_caption} 👀\n\n{ _get_viral_cta(niche, 'tiktok') }"
        title = hook[:60]
        hashtags = _boost_hashtags(hashtags, niche, "tiktok")
        
    elif platform == "Instagram Reels":
        # Reels: aesthetic + save/share driven
        viral_caption = f"{base_caption}\n\n✨ { _get_viral_cta(niche, 'reels') }"
        title = hook[:65]
        hashtags = _boost_hashtags(hashtags, niche, "reels")
        
    else:  # YouTube Shorts
        # YouTube: searchable + curiosity driven
        viral_caption = f"{base_caption}\n\n👇 { _get_viral_cta(niche, 'shorts') }"
        # v4: judul tulisan AI (bila ada) lebih natural daripada template
        title = clip.get("ai_title") or _generate_youtube_title(hook, niche, category, score)
        hashtags = _boost_hashtags(hashtags, niche, "shorts")
    
    cta = profile.get("cta", "")
    limit = profile.get("caption_limit", config.UPLOAD_CAPTION_MAX_CHARS)
    
    full_caption = f"{viral_caption} {cta}".strip()
    if len(full_caption) > limit:
        full_caption = full_caption[:limit].rsplit(" ", 1)[0].rstrip(".,")
    
    return {
        "title": title[:95] if platform == "YouTube Shorts" else title[:70],
        "caption": full_caption,
        "hashtags": hashtags,
        "safe_caption_zone": profile.get("safe_caption_zone"),
        "duration_fit": bool(_duration_fit(clip.get("duration", 0), profile.get("preferred_duration", (15, 60)))),
        "viral_score": score,
        "niche": niche
    }


def _get_viral_cta(niche: str, platform: str) -> str:
    ctas = {
        "tiktok": {
            "gaming": "Kamu pernah ngerasain gak? Comment di bawah! 👇",
            "finance": "Mau tau caranya? Comment 'MAU' biar gue kasih tau! 💰",
            "beauty": "Save buat nanti ya! 💄",
            "motivation": "Tag orang yang butuh ini! 🔥",
            "education": "Simpan biar gak lupa! 📚",
            "comedy": "Tag temen yang bakal ngakak! 😂",
            "relationship": "Pernah ngalamin juga? Comment! ❤️",
            "tech": "Mau coba? Comment 'COBA' 👇",
            "general": "Kamu setuju gak? Comment! 👀"
        },
        "reels": {
            "gaming": "Save buat main nanti! 🎮",
            "finance": "Share ke temen yang lagi cari duit! 💸",
            "beauty": "Coba dulu baru tag aku! ✨",
            "motivation": "Save buat pengingat harian! 🚀",
            "education": "Bookmark biar gak lupa! 📖",
            "comedy": "Tag 3 orang yang harus nonton ini! 🤣",
            "relationship": "Kirim ke pasangan kamu! 💕",
            "tech": "Coba sekarang juga! 🤖",
            "general": "Save + Share kalau bermanfaat! ✨"
        },
        "shorts": {
            "gaming": "Part 2 ada di komentar! 🎮",
            "finance": "Mau tau rahasianya? Lihat komentar! 💰",
            "beauty": "Tutorial lengkap di komentar! 💄",
            "motivation": "Save buat besok pagi! 🔥",
            "education": "Step by step di komentar! 📚",
            "comedy": "Endingnya gila kan? 😂",
            "relationship": "Kamu setuju? Comment! ❤️",
            "tech": "Link full tutorial di komentar! ⚡",
            "general": "Lanjut di komentar! 👇"
        }
    }
    return ctas.get(platform, {}).get(niche, "Comment pendapat kamu! 👇")


def _boost_hashtags(base_tags: List[str], niche: str, platform: str) -> List[str]:
    """Add high-potential viral hashtags based on niche"""
    niche_tags = {
        "gaming": ["#gaming", "#fyp", "#viral", "#gameplay", "#shorts"],
        "finance": ["#fyp", "#uang", "#investasi", "#saham", "#viral"],
        "beauty": ["#fyp", "#makeup", "#skincare", "#beauty", "#viral"],
        "motivation": ["#fyp", "#motivasi", "#mindset", "#sukses", "#viral"],
        "education": ["#fyp", "#tips", "#belajar", "#edukasi", "#viral"],
        "comedy": ["#fyp", "#lucu", "#ngakak", "#comedy", "#viral"],
        "relationship": ["#fyp", "#relationship", "#cinta", "#viral", "#fypシ"],
        "tech": ["#fyp", "#teknologi", "#ai", "#tech", "#viral"],
        "general": ["#fyp", "#viral", "#shorts", "#trending", "#fypシ"]
    }
    
    extra = niche_tags.get(niche, niche_tags["general"])
    combined = list(dict.fromkeys(base_tags + extra))  # remove duplicates, keep order
    return combined[:8]


def _generate_youtube_title(hook: str, niche: str, category: str, score: int) -> str:
    """Generate clickable YouTube Shorts title"""
    templates = {
        "gaming": [
            f"🔥 {hook} (Endingnya Gila!)",
            f"🎮 {hook} #Shorts",
            f"Game Ini Bikin {hook} 😱"
        ],
        "finance": [
            f"💰 {hook} (Rahasia Orang Kaya)",
            f"Cara {hook} dalam 30 Detik!",
            f"💸 {hook} #Shorts"
        ],
        "beauty": [
            f"💄 {hook} (Hasilnya Gila!)",
            f"Makeup Hack: {hook}",
            f"✨ {hook} #Shorts"
        ],
        "motivation": [
            f"🚀 {hook}",
            f"Mindset Ini Bikin {hook} 💪",
            f"⚡ {hook} (Jangan Lewatkan!)"
        ],
        "education": [
            f"📚 {hook} (Tips Rahasia)",
            f"Cara {hook} dalam 1 Menit!",
            f"🧠 {hook} #Shorts"
        ],
        "comedy": [
            f"😂 {hook} (Endingnya Ngakak!)",
            f"Lucu Banget! {hook}",
            f"🤣 {hook} #Shorts"
        ],
        "relationship": [
            f"❤️ {hook} (Pernah Ngalamin?)",
            f"Relationship Hack: {hook}",
            f"💕 {hook} #Shorts"
        ],
        "tech": [
            f"🤖 {hook} (Teknologi Baru!)",
            f"Tech Hack: {hook} ⚡",
            f"🔥 {hook} #Shorts"
        ],
        "general": [
            f"🔥 {hook}",
            f"{hook} (Wajib Nonton!)",
            f"✨ {hook} #Shorts"
        ]
    }
    
    options = templates.get(niche, templates["general"])
    title = options[0] if options else hook
    return title[:95]


def _build_quality_checklist(clip: Dict, platforms: List[str]) -> List[Dict]:
    duration = clip.get("duration", 0)
    signals = set(clip.get("fyp_signals", []))
    return [
        {"check": "Hook awal kuat", "ok": bool("strong_opening_hook" in signals or clip.get("score", 0) >= 70)},
        {"check": "Retensi cukup padat", "ok": bool("good_retention_curve" in signals or 18 <= duration <= 50)},
        {"check": "Ada pemicu komentar/share", "ok": bool("share_or_comment_trigger" in signals or clip.get("hashtags"))},
        {"check": "Durasi cocok short-form", "ok": bool(any(_duration_fit(duration, config.PLATFORM_PROFILES[p]["preferred_duration"]) for p in platforms))},
        {"check": "Copy upload sudah dibuat", "ok": bool(clip.get("upload_caption"))},
    ]


def _grade_clip(clip: Dict) -> str:
    score = clip.get("score", 0)
    if score >= 85:
        return "A+"
    if score >= 75:
        return "A"
    if score >= 65:
        return "B"
    if score >= 55:
        return "C"
    return "Needs Review"


def _duration_fit(duration: float, bounds: tuple) -> bool:
    low, high = bounds
    return bool(low <= duration <= high)


def _first_platform_caption(clip: Dict, platforms: List[str]) -> str:
    platform_copy = clip.get("platform_copy", {})
    for platform in platforms:
        if platform in platform_copy:
            return platform_copy[platform].get("caption", "")
    return clip.get("upload_caption", "")


# ==================== SCHEDULE UPLOADER (NEW) ====================

def get_best_upload_schedule(niche: str, platform: str = "Semua") -> Dict:
    """
    Returns best posting times per platform based on niche (Klipers.pro style).
    """
    schedules = {
        "TikTok": {
            "gaming": ["19:00 - 21:00", "22:00 - 00:00", "14:00 - 16:00"],
            "finance": ["07:00 - 09:00", "17:00 - 19:00", "12:00 - 13:00"],
            "beauty": ["18:00 - 20:00", "09:00 - 11:00", "21:00 - 23:00"],
            "motivation": ["05:00 - 07:00", "19:00 - 21:00", "12:00 - 14:00"],
            "education": ["18:00 - 20:00", "08:00 - 10:00", "20:00 - 22:00"],
            "comedy": ["19:00 - 23:00", "12:00 - 14:00", "16:00 - 18:00"],
            "relationship": ["20:00 - 23:00", "10:00 - 12:00", "14:00 - 16:00"],
            "tech": ["19:00 - 21:00", "08:00 - 10:00", "17:00 - 19:00"],
            "general": ["18:00 - 21:00", "12:00 - 14:00", "20:00 - 22:00"]
        },
        "Instagram Reels": {
            "gaming": ["18:00 - 20:00", "21:00 - 23:00"],
            "finance": ["07:30 - 09:30", "17:30 - 19:30"],
            "beauty": ["17:00 - 19:00", "08:00 - 10:00", "20:00 - 22:00"],
            "motivation": ["06:00 - 08:00", "18:00 - 20:00"],
            "education": ["17:00 - 19:00", "07:00 - 09:00"],
            "comedy": ["18:00 - 22:00", "11:00 - 13:00"],
            "relationship": ["19:00 - 22:00", "09:00 - 11:00"],
            "tech": ["18:00 - 20:00", "07:30 - 09:30"],
            "general": ["17:00 - 20:00", "11:00 - 13:00"]
        },
        "YouTube Shorts": {
            "gaming": ["18:00 - 21:00", "12:00 - 14:00"],
            "finance": ["06:00 - 08:00", "17:00 - 19:00"],
            "beauty": ["17:00 - 19:00", "08:00 - 10:00"],
            "motivation": ["05:30 - 07:30", "18:00 - 20:00"],
            "education": ["17:00 - 19:00", "07:00 - 09:00"],
            "comedy": ["17:00 - 22:00", "11:00 - 13:00"],
            "relationship": ["18:00 - 21:00", "09:00 - 11:00"],
            "tech": ["18:00 - 20:00", "07:00 - 09:00"],
            "general": ["17:00 - 20:00", "11:00 - 14:00"]
        }
    }
    
    platform = platform if platform in schedules else "TikTok"
    niche = niche if niche in schedules[platform] else "general"
    
    return {
        "platform": platform,
        "niche": niche,
        "best_times": schedules[platform][niche],
        "note": "Waktu di atas adalah waktu lokal terbaik berdasarkan data engagement niche kamu. Upload 2-3x sehari di jam berbeda untuk hasil maksimal."
    }


# ==================== A/B TEST VARIANTS (NEW v3) ====================

def suggest_caption_variants(clip: Dict, platform: str = "TikTok") -> List[str]:
    """Generate 3 alternatif caption untuk A/B test upload (hook-first, tanya-opini, POV)."""
    hook = (clip.get("hook_text") or "").strip()
    base = (clip.get("upload_caption") or "").strip()
    niche = clip.get("niche", "general")
    if platform == "TikTok":
        cta = _get_viral_cta(niche, "tiktok")
    elif platform == "Instagram Reels":
        cta = _get_viral_cta(niche, "reels")
    else:
        cta = _get_viral_cta(niche, "shorts")

    # v4: variasi hook dari AI (hook_alternatives) dipakai lebih dulu untuk A/B test
    raw_variants = [
        f"{alt} {cta}".strip() for alt in (clip.get("hook_alternatives") or [])
    ] + [
        f"{hook} {base}".strip(),
        f"{hook} — kamu tim yang mana? 👀 {cta}".strip(),
        f"POV: {base}".strip(),
        f"{cta} {hook}".strip(),
    ]
    variants = []
    for variant in raw_variants:
        variant = trim_caption(variant).strip()
        if variant and variant not in variants:
            variants.append(variant)
        if len(variants) >= 3:
            break
    return variants
