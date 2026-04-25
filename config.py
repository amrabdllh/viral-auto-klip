"""
Viral Auto Klip - Configuration
Edit this file to customize scoring, styles, keywords, etc.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ==================== PATHS ====================
BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)
TEMP_DIR = BASE_DIR / "temp"
TEMP_DIR.mkdir(exist_ok=True)
FONT_DIR = BASE_DIR / "fonts"
FONT_DIR.mkdir(exist_ok=True)

CLIPPING_SOFTWARE_API_KEY = os.getenv("CLIPPING_SOFTWARE_API_KEY", "")

# Download a cool font for captions if needed (e.g. from Google Fonts)
# Example: Impact, Montserrat, Poppins, etc. Put .ttf in fonts/
DEFAULT_FONT = "Arial"  # or path to .ttf like str(FONT_DIR / "Montserrat-Bold.ttf")

# ==================== WHISPER ====================
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "large-v3-turbo")  # tiny, base, small, medium, large-v3-turbo, large-v3
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "auto")  # auto = coba CUDA kalau terdeteksi, fallback CPU otomatis.
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8")  # int8 paling stabil untuk RTX 3060 6GB; CPU fallback otomatis int8
WHISPER_CUDA_DEVICE_INDEX = int(os.getenv("WHISPER_CUDA_DEVICE_INDEX", "0"))  # index CUDA, bukan index GPU di Task Manager
WHISPER_GPU_SAFE_VRAM_GB = float(os.getenv("WHISPER_GPU_SAFE_VRAM_GB", "5.5"))
WHISPER_ALLOW_LARGE_ON_GPU = os.getenv("WHISPER_ALLOW_LARGE_ON_GPU", "0").lower() in ("1", "true", "yes")
GROQ_WHISPER_MODEL = os.getenv("GROQ_WHISPER_MODEL", "whisper-large-v3-turbo")
GROQ_WHISPER_FALLBACK = os.getenv("GROQ_WHISPER_FALLBACK", "1").lower() in ("1", "true", "yes")
GROQ_WHISPER_AUDIO_BITRATE = os.getenv("GROQ_WHISPER_AUDIO_BITRATE", "64k")

# ==================== YT-DLP / DOWNLOAD ====================
# Optional cookies.txt path for YouTube videos that require login/bot verification.
# Relative paths are resolved from the project root, e.g. YTDLP_COOKIES=cookies.txt
YTDLP_COOKIES = os.getenv("YTDLP_COOKIES", "").strip()

# ==================== VIRAL SCORING ====================
MIN_CLIP_DURATION = 15  # seconds
MAX_CLIP_DURATION = 60
IDEAL_CLIP_DURATION = 30

# Viral keywords (Bahasa Indonesia + English) - tambah sesuai niche kamu!
VIRAL_KEYWORDS = {
    "humor": ["lucu", "gokil", "ngakak", "rofl", "haha", "wtf", "shocking", "gila", "aneh", "funny", "joke", "punchline", "kocak", "absurd"],
    "motivasi": ["sukses", "motivasi", "rahasia", "cara", "tips", "how to", "jangan lewatkan", "ubah hidup", "dream", "goal", "inspirasi", "bangkit", "mindset"],
    "shocking": ["tidak percaya", "you won't believe", "rahasia", "terkejut", "plot twist", "ending", "akhirnya", "baru tau", "ternyata", "fakta"],
    "emotional": ["sedih", "menangis", "haru", "bangga", "cinta", "keluarga", "perjuangan", "emotional", "touching", "kehilangan", "takut"],
    "educational": ["tutorial", "cara mudah", "step by step", "belajar", "rahasia", "pro tips", "hacks", "kesalahan", "strategi", "framework"],
    "engagement": ["comment", "like", "share", "tag temen", "kamu setuju?", "pilih mana", "tebak", "quiz", "menurut kamu", "setuju nggak"]
}

HOOK_PATTERNS = [
    "jangan", "stop", "ini alasan", "kenapa", "ternyata", "rahasia",
    "kamu harus", "pernah nggak", "gue kasih tahu", "listen", "watch this",
    "the truth", "here is why", "you need to", "nobody tells you"
]

RETENTION_KEYWORDS = [
    "tapi", "namun", "akhirnya", "terus", "lalu", "ternyata", "masalahnya",
    "plot twist", "bagian paling", "ini yang", "wait", "but", "then", "finally"
]

CTA_KEYWORDS = [
    "follow", "subscribe", "komen", "comment", "share", "save", "simpan",
    "tag", "bagikan", "like", "setuju", "pilih"
]

# Scoring weights (total 100)
SCORING_WEIGHTS = {
    "keyword_match": 25,
    "sentiment_intensity": 20,
    "hook_presence": 15,
    "length_score": 10,
    "visual_interest": 10,  # face close-up, motion
    "retention_curve": 10,
    "shareability": 10,
    "llm_score": 15        # if LLM enabled
}

MIN_VIRAL_SCORE = 55  # default threshold

# ==================== LLM / AI SCORING ====================
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

AI_PROVIDER_MODELS = {
    "Groq": [
        "llama-3.3-70b-versatile",
        "openai/gpt-oss-120b",
        "llama-3.1-8b-instant",
    ],
    "Gemini": [
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.0-flash",
        "gemini-2.0-flash-lite",
    ],
    "OpenRouter": [
        "openrouter/free",
    ],
    "Ollama": [
        "qwen2.5:7b",
        "llama3.1:8b",
        "gemma3:4b",
    ],
}

AI_PROVIDER = os.getenv("AI_PROVIDER", "Groq")
LLM_MODEL = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")
USE_LLM_SCORING = bool(GROQ_API_KEY or GEMINI_API_KEY or OPENROUTER_API_KEY or AI_PROVIDER == "Ollama")
AI_MAX_CANDIDATES = int(os.getenv("AI_MAX_CANDIDATES", "24"))  # cap AI calls per source video

VIRAL_PROMPT = """
Kamu adalah expert viral content analyst untuk TikTok, YouTube Shorts, dan Instagram Reels.

Analisis segmen video berikut (transcript + durasi + konteks):
{segment_info}

Berikan:
1. Viral Score: 0-100 (seberapa mungkin jadi viral)
2. Kategori utama: lucu | motivasi | shocking | emotional | educational | hook
3. Alasan singkat (1 kalimat kenapa orang akan share/nonton sampai habis)
4. Saran improvement (opsional)

Aturan scoring:
- Naikkan score hanya jika segmen terasa utuh: ada konteks/setup, inti konflik/poin, dan payoff/ending.
- Turunkan score jika segmen seperti potongan random, mulai di tengah kalimat, ending menggantung tanpa rasa penasaran, atau tidak jelas siapa/apa yang dibahas.
- Untuk podcast/storytelling, segmen 35-60 detik boleh lebih bagus daripada 30 detik jika konteksnya lebih lengkap.
- Jangan pilih hanya karena ada keyword viral; utamakan momen yang benar-benar bisa ditonton ulang/share.

Jawab dalam format JSON ketat:
{{"score": 78, "category": "lucu", "reason": "Punchline dadakan + ekspresi wajah bikin orang ngakak", "suggestion": "Tambah zoom di muka saat punchline"}}
"""

# ==================== FACE & REFRAMING ====================
ENABLE_FACE_DETECTION = True
FACE_DETECTION_CONFIDENCE = 0.5
SMOOTHING_FRAMES = 15  # smooth camera movement
TARGET_ASPECT = (9, 16)  # vertical
OUTPUT_RESOLUTION = (1080, 1920)  # Full HD vertical

# ==================== CAPTIONS ====================
CAPTION_STYLES = {
    "modern": {
        "font_size": 48,
        "color": (255, 255, 255),
        "stroke_color": (0, 0, 0),
        "stroke_width": 3,
        "position": "center",  # or "bottom"
        "animation": "pop",    # pop, fade, karaoke
        "emoji": True
    },
    "minimal": {
        "font_size": 42,
        "color": (255, 255, 255),
        "stroke_color": (0, 0, 0),
        "stroke_width": 2,
        "position": "bottom",
        "animation": "fade",
        "emoji": False
    },
    "karaoke": {
        "font_size": 50,
        "color": (255, 215, 0),  # gold
        "stroke_color": (0, 0, 0),
        "stroke_width": 4,
        "position": "center",
        "animation": "karaoke",  # highlight per word
        "emoji": True
    },
    # NEW Klipers.pro-inspired styles
    "tiktok_bold": {
        "font_size": 52,
        "color": (255, 255, 255),
        "stroke_color": (0, 0, 0),
        "stroke_width": 5,
        "position": "center",
        "animation": "pop",
        "emoji": True,
        "bold": True
    },
    "gradient": {
        "font_size": 48,
        "color": (255, 255, 255),
        "stroke_color": (255, 0, 150),  # pink-purple gradient feel
        "stroke_width": 4,
        "position": "center",
        "animation": "pop",
        "emoji": True
    },
    "neon": {
        "font_size": 50,
        "color": (0, 255, 255),  # cyan neon
        "stroke_color": (255, 0, 255),  # magenta glow
        "stroke_width": 6,
        "position": "center",
        "animation": "pop",
        "emoji": True
    }
}

DEFAULT_CAPTION_STYLE = "modern"
MAX_WORDS_PER_LINE = 8  # for readability on mobile

# ==================== OUTPUT ====================
VIDEO_CODEC = "libx264"
AUDIO_CODEC = "aac"
VIDEO_BITRATE = "5000k"
AUDIO_BITRATE = "192k"
PRESET = "fast"  # ultrafast, fast, medium, slow (better quality)
USE_NVENC = os.getenv("USE_NVENC", "1").lower() in ("1", "true", "yes")
NVENC_PRESET = os.getenv("NVENC_PRESET", "p4")
NVENC_CQ = int(os.getenv("NVENC_CQ", "20"))
KEEP_SOURCE_VIDEO = os.getenv("KEEP_SOURCE_VIDEO", "1").lower() in ("1", "true", "yes")

ADD_WATERMARK = False
WATERMARK_TEXT = "@yourhandle"
WATERMARK_OPACITY = 0.6

ADD_END_SCREEN = True
END_SCREEN_TEXT = "Follow for more! 🔥"

# ==================== NEW KLIPERS.PRO FEATURES ====================
ENABLE_AUDIO_ENHANCE = True
ENABLE_BACKGROUND_MUSIC = False
BACKGROUND_MUSIC_PATH = ""  # path to mp3/wav
MUSIC_VOLUME = 0.12
AUTO_DUCK = True

ENABLE_BROLL = True  # Auto add intro bumper (Klipers.pro style)
BROLL_FOLDER = ""  # future: custom B-roll clips

ENABLE_HOOK_OPTIMIZER = True
ENABLE_NICHE_DETECTOR = True

# Platform safe zones (caption positioning)
PLATFORM_SAFE_ZONES = {
    "TikTok": "lower_third",
    "Instagram Reels": "bottom_safe",
    "YouTube Shorts": "center",
    "Semua": "center"
}

# ==================== OTHER ====================
MAX_CLIPS_PER_VIDEO = 20
PARALLEL_CLIPS = 2  # how many clips process in parallel (memory heavy)
LOG_LEVEL = "INFO"

# ==================== FYP PACK ====================
DEFAULT_HASHTAGS = {
    "humor": ["#fyp", "#viral", "#lucu", "#ngakak", "#shorts"],
    "motivasi": ["#fyp", "#motivasi", "#mindset", "#sukses", "#shorts"],
    "shocking": ["#fyp", "#fakta", "#plotwist", "#viral", "#shorts"],
    "emotional": ["#fyp", "#kisahnyata", "#inspirasi", "#viral", "#shorts"],
    "educational": ["#fyp", "#tips", "#belajar", "#edukasi", "#shorts"],
    "engagement": ["#fyp", "#tanyajawab", "#opini", "#viral", "#shorts"],
    "general": ["#fyp", "#viral", "#shorts", "#reels", "#tiktok"]
}
MAX_HASHTAGS = 6
UPLOAD_CAPTION_MAX_CHARS = 140

PLATFORM_PROFILES = {
    "TikTok": {
        "caption_limit": 150,
        "style": "hooky, direct, comment-driven",
        "cta": "Komen pendapat kamu.",
        "preferred_duration": (18, 42),
        "safe_caption_zone": "middle-lower"
    },
    "Instagram Reels": {
        "caption_limit": 180,
        "style": "clean, relatable, save/share-driven",
        "cta": "Simpan buat nanti.",
        "preferred_duration": (15, 45),
        "safe_caption_zone": "lower"
    },
    "YouTube Shorts": {
        "caption_limit": 100,
        "style": "searchable, curiosity-driven",
        "cta": "Subscribe untuk part berikutnya.",
        "preferred_duration": (20, 58),
        "safe_caption_zone": "middle"
    },
    "Semua": {
        "caption_limit": 140,
        "style": "balanced short-form",
        "cta": "Follow untuk clip berikutnya.",
        "preferred_duration": (18, 50),
        "safe_caption_zone": "middle-lower"
    }
}

FYP_AGENT_CHECKS = [
    "Hook terlihat dalam 2 detik pertama",
    "Caption tidak menutup wajah/subjek utama",
    "Durasi cukup padat untuk ditonton ulang",
    "Ada alasan untuk komentar, share, atau save",
    "Hashtag tidak terlalu banyak dan tetap relevan"
]

# Niche-specific (example for gaming/podcast - uncomment & customize)
# GAMING_MODE = True
# PODCAST_MODE = False
