"""
Viral Detection & Analysis Engine (v4)

Alur:
1. Kandidat dari segmen Whisper + sliding window.
2. (v4) AI Moment Finder: LLM membaca seluruh transkrip bertimestamp dan mengusulkan momen sendiri.
3. Skor lokal (keyword, sentimen, hook, durasi, kelengkapan, retensi, shareability, visual).
4. Rerank LLM untuk shortlist teratas (skor AI dipakai sebagai bobot LLM_RERANK_WEIGHT).
5. Non-overlap + diversity + niche.
6. (v4) AI Packaging: hook, judul, caption upload, hashtag per klip final.

Semua LLM lewat core/ai_providers.py (Groq, OpenAI, Claude, Gemini, Antigravity, DeepSeek,
Mistral, OpenRouter, Ollama, Custom).
"""
import re
from collections import Counter
from functools import lru_cache
from typing import Dict, List, Optional

import numpy as np
from loguru import logger
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

import config
from . import ai_providers as ai

vader_analyzer = SentimentIntensityAnalyzer()
LLM_RATE_LIMITED = False

# v6.4: petunjuk momen dari luar transkrip (Mode Live: lonjakan chat penonton & suara streamer).
# Diisi pemanggil sebelum analyze_viral_potential lalu dikosongkan lagi. Format item:
# {"t": detik_video, "kind": "chat"|"audio", "strength": rasio_vs_normal, "label": teks_untuk_AI}
SIGNAL_HINTS: List[Dict] = []
# v6.8: profil audio video yang sedang dianalisis (core/audio_signals.AudioProfile) — "telinga" untuk skor & AI
AUDIO = None


def _hints_between(start: float, end: float, pad_before: float = 2.0, pad_after: float = 1.0) -> List[Dict]:
    return [h for h in SIGNAL_HINTS if start - pad_before <= float(h["t"]) <= end + pad_after]


def _signal_candidates(transcript: List[Dict], video_duration: float) -> List[Dict]:
    """Kandidat langsung di sekitar lonjakan kuat (momen game/reaksi bisa minim kata-kata)."""
    out = []
    for h in sorted(SIGNAL_HINTS, key=lambda h: -float(h.get("strength", 0)))[:8]:
        if float(h.get("strength", 0)) < (2.2 if h.get("kind") == "chat" else 2.6):
            continue
        t = float(h["t"])
        lo, hi = max(0.0, t - 22), min(video_duration or t + 10, t + 9)
        cand = _build_candidate_from_window(transcript, lo, hi)
        if not cand or not (cand["start"] <= t <= cand["end"]):
            words = [w for s in transcript for w in (s.get("words") or []) if lo <= float(w.get("start", 0)) < hi]
            text = " ".join(str(w.get("word", "")).strip() for w in words).strip()
            cand = {"start": lo, "end": hi, "text": text or "(momen reaksi)", "words": words,
                    "subtitle_segments": [s for s in transcript if max(s["start"], lo) < min(s["end"], hi)]}
        cand["signal_moment"] = h
        out.append(cand)
    return out

CATEGORIES = ["humor", "motivasi", "shocking", "emotional", "educational", "engagement"]
_CATEGORY_ALIASES = {
    "lucu": "humor", "comedy": "humor", "funny": "humor",
    "motivation": "motivasi", "inspirasi": "motivasi",
    "education": "educational", "edukasi": "educational", "edukatif": "educational", "tutorial": "educational",
    "hook": "engagement", "emosional": "emotional",
}


# ============================================================ provider config (kompatibel API lama)

def configure_llm_clients(
    provider: str = "Groq",
    model: str = "",
    groq_api_key: str = "",
    gemini_api_key: str = "",
    openrouter_api_key: str = "",
    ollama_base_url: str = "",
    openai_api_key: str = "",
    api_key: str = "",
    base_url: str = "",
) -> bool:
    """Atur provider AI saat runtime (key dari UI/CLI). Return True bila provider siap dipakai.

    `api_key` / `base_url` generik berlaku untuk provider terpilih (Anthropic, DeepSeek,
    Mistral, Custom, OpenAI, ...). Parameter per-provider lama tetap didukung.
    """
    global LLM_RATE_LIMITED
    LLM_RATE_LIMITED = False
    config.AI_PROVIDER = provider
    if groq_api_key:
        config.GROQ_API_KEY = groq_api_key.strip()
    if gemini_api_key:
        config.GEMINI_API_KEY = gemini_api_key.strip()
    if openrouter_api_key:
        config.OPENROUTER_API_KEY = openrouter_api_key.strip()
    if openai_api_key:
        config.OPENAI_API_KEY = openai_api_key.strip()
    if ollama_base_url:
        config.OLLAMA_BASE_URL = ollama_base_url.strip().rstrip("/")
    ai.set_credentials(provider, api_key=api_key, base_url=base_url, model=model)
    config.LLM_MODEL = ai.resolve_model(provider, model)
    ready = ai.is_configured(provider)
    if ready:
        logger.info(f"AI provider aktif: {provider} / {config.LLM_MODEL}")
    return ready


def _llm_configured() -> bool:
    return ai.is_configured(config.AI_PROVIDER)


def _call_selected_llm(prompt: str, max_tokens: int = 400, json_mode: bool = True, json_schema: Optional[Dict] = None,
                       system: str = "", model: str = "") -> str:
    """`model` = model khusus tahap ini (mis. AI_REVIEW_MODEL). Bila ditolak provider, ulang dengan model utama."""
    if model and model != config.LLM_MODEL:
        try:
            return ai.complete(prompt, system=system, max_tokens=max_tokens, json_mode=json_mode, json_schema=json_schema,
                               model=model)
        except ai.AIRateLimited:
            raise
        except Exception as exc:
            logger.warning(f"Model {model} tidak bisa dipakai ({str(exc)[:120]}); pakai {config.LLM_MODEL}")
    return ai.complete(prompt, system=system, max_tokens=max_tokens, json_mode=json_mode, json_schema=json_schema)


# ============================================================ main entry

def analyze_viral_potential(
    transcript_segments: List[Dict],
    video_info: Dict,
    face_data: Optional[List] = None,
    use_llm: bool = True,
    video_path: str = "",
) -> List[Dict]:
    """Skor setiap kandidat momen dan kembalikan klip berpotensi viral.

    `video_path` (opsional): bila ada, suara video dianalisis (energi, nada, kata yang ditekan, tawa) dan dipakai
    sebagai sinyal tambahan untuk skor, AI Moment Finder, Editor-AI, dan titik punch-in saat render."""
    global AUDIO
    AUDIO = None
    path = video_path or str(video_info.get("path") or "")
    if path and getattr(config, "AUDIO_SIGNALS", True):
        from . import audio_signals
        AUDIO = audio_signals.analyze(path, transcript_segments)
    try:
        return _analyze_viral_potential(transcript_segments, video_info, face_data, use_llm)
    finally:
        AUDIO = None


def _analyze_viral_potential(transcript_segments: List[Dict], video_info: Dict, face_data: Optional[List],
                             use_llm: bool) -> List[Dict]:
    global LLM_RATE_LIMITED
    LLM_RATE_LIMITED = False
    logger.info("🔍 Starting viral potential analysis...")

    video_duration = video_info.get("duration", 0) or (transcript_segments[-1]["end"] if transcript_segments else 0)
    ai_enabled = bool(use_llm and _llm_configured())

    # 1. Kandidat: AI Moment Finder dulu (agar menang saat dedupe), lalu rule-based.
    candidates: List[Dict] = []
    if ai_enabled and config.AI_MOMENT_FINDER:
        try:
            candidates.extend(_ai_moment_candidates(transcript_segments, video_duration))
        except Exception as exc:
            logger.warning(f"AI Moment Finder gagal, lanjut kandidat rule-based: {str(exc)[:200]}")
    if SIGNAL_HINTS:
        candidates.extend(_signal_candidates(transcript_segments, video_duration))
    candidates.extend(_create_candidate_segments(transcript_segments, video_duration))
    candidates = _dedupe_candidates(candidates)

    # 2. Skor lokal semua kandidat (kandidat AI langsung memakai skor AI-nya, tanpa panggilan tambahan).
    local_scored = [(seg, _score_segment(seg, video_info, face_data, use_llm=bool(seg.get("ai_moment")))) for seg in candidates]

    # 3. Rerank LLM untuk shortlist teratas.
    # Momen pilihan AI sudah punya awal/akhir yang utuh dan skor AI; jendela geser (sliding window) hanya
    # cadangan. Jadi bila AI sudah mengusulkan cukup momen, rerank jendela geser dilewati/dipangkas
    # (dulu 50 panggilan LLM berurutan = ±5 menit) dan momen AI diberi bonus peringkat.
    ai_found = sum(1 for seg, _ in local_scored if seg.get("ai_moment"))
    ai_shortlist = set()
    if ai_enabled and not LLM_RATE_LIMITED:
        shortlist_size = min(len(local_scored), max(config.MAX_CLIPS_PER_VIDEO * 2, config.AI_MAX_CANDIDATES))
        if getattr(config, "AI_RERANK_LIMIT", None) is not None:  # Mode Live: hemat waktu per jendela
            shortlist_size = min(shortlist_size, int(config.AI_RERANK_LIMIT))
        if ai_found >= config.MAX_CLIPS_PER_VIDEO:
            shortlist_size = 0
        elif ai_found:
            shortlist_size = min(shortlist_size, max(6, (config.MAX_CLIPS_PER_VIDEO - ai_found) * 2))
        ranked = sorted((item for item in local_scored if not item[0].get("ai_moment")),
                        key=lambda item: item[1]["score"], reverse=True)
        ai_shortlist = {id(seg) for seg, _ in ranked[:shortlist_size]}
        logger.info(f"AI rerank: {len(ai_shortlist)} kandidat teratas dari {len(local_scored)} "
                    f"({ai_found} momen pilihan AI; {config.AI_PROVIDER}/{config.LLM_MODEL}).")

    rerank_jobs = [seg for seg, _ in local_scored if id(seg) in ai_shortlist]
    reranked: Dict[int, Dict] = {}
    if rerank_jobs:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=max(1, int(getattr(config, "AI_RERANK_WORKERS", 4)))) as pool:
            for seg, res in zip(rerank_jobs, pool.map(lambda sg: _score_segment(sg, video_info, face_data, use_llm=True), rerank_jobs)):
                reranked[id(seg)] = res

    clips = []
    for seg, score_data in local_scored:
        if id(seg) in reranked:
            score_data = reranked[id(seg)]
        if seg.get("ai_moment"):
            score_data = {**score_data, "score": min(100.0, score_data["score"] + config.AI_MOMENT_BONUS)}
        if score_data["score"] >= config.MIN_VIRAL_SCORE:
            clips.append(_build_clip_payload(seg, score_data))

    logger.info(f"Generated {len(candidates)} candidates with durations: {_duration_summary(candidates)}")

    # FIX: kandidat di luar shortlist rerank menyimpan skor lokal murni, sehingga dulu bisa
    # mengalahkan kandidat yang sudah DINILAI AI (skornya di-blend). Bila AI berhasil menilai,
    # klip yang dievaluasi AI diurutkan lebih dulu; sisanya hanya pengisi bila kurang.
    any_ai_scored = any(c.get("llm_score") for c in clips)
    clips = sorted(clips, key=lambda x: ((1 if (any_ai_scored and x.get("llm_score")) else 0), x["score"]), reverse=True)
    clips = _remove_overlapping_clips(clips)
    clips = _apply_diversity_ranking(clips)
    # Editor-AI: tinjau kandidat teratas dengan konteks sekitar (standalone, hook, payoff), rapikan batas potong,
    # buang yang lemah, dan tulis hook/judul/caption yang terdukung kutipan dari klip.
    if ai_enabled and config.AI_EDITOR_REVIEW and not LLM_RATE_LIMITED and clips:
        try:
            from .editor_review import review_clips
            clips = review_clips(clips, transcript_segments, config.MAX_CLIPS_PER_VIDEO, str(video_info.get("title") or ""))
        except Exception as exc:
            logger.warning(f"Editor-AI dilewati: {str(exc)[:200]}")
    clips = clips[:config.MAX_CLIPS_PER_VIDEO]

    detected_niche = detect_niche(" ".join(c.get("transcript", c.get("text", "")) for c in clips))
    for clip in clips:
        clip["niche"] = detected_niche

    # 4. AI packaging (hook / judul / caption / hashtag) untuk klip final.
    if ai_enabled and config.AI_CLIP_PACKAGING and clips:
        # klip yang sudah dikemas Editor-AI (terdukung kutipan) tidak ditulis ulang oleh pengemas lama
        _ai_package_clips([c for c in clips if not str(c.get("ai_packaged_by", "")).startswith("editor-review")])

    logger.success(f"✅ Found {len(clips)} high-viral-potential clips (min score {config.MIN_VIRAL_SCORE}) | Niche: {detected_niche}")
    return clips


def _build_clip_payload(seg: Dict, score_data: Dict) -> Dict:
    text = seg["text"]
    return {
        "start": seg["start"],
        "end": seg["end"],
        "duration": seg["end"] - seg["start"],
        "text": text[:200] + "..." if len(text) > 200 else text,
        "transcript": text[:2000],
        "score": round(score_data["score"], 1),
        "local_score": score_data.get("local_score"),
        "llm_score": score_data.get("llm_score", 0),
        "score_breakdown": score_data.get("breakdown", {}),
        "category": score_data["category"],
        "reason": score_data["reason"],
        "keywords_matched": score_data.get("keywords_matched", []),
        "sentiment": score_data.get("sentiment", 0),
        "fyp_signals": score_data.get("fyp_signals", []),
        "hook_text": score_data.get("hook_text", ""),
        "upload_caption": score_data.get("upload_caption", ""),
        "hashtags": score_data.get("hashtags", []),
        "source": "ai_moment_finder" if seg.get("ai_moment") else ("live_signal" if seg.get("signal_moment") else "rule_based"),
        "subtitle_segments": seg.get("subtitle_segments", []),
        "words": seg.get("words", []),
        "signal_peaks": score_data.get("signal_peaks", []),
        "audio_profile": score_data.get("audio_profile"),
    }


# ============================================================ candidates

def _create_candidate_segments(transcript: List[Dict], video_duration: float) -> List[Dict]:
    candidates = []
    for seg in transcript:
        duration = seg["end"] - seg["start"]
        if config.MIN_CLIP_DURATION <= duration <= config.MAX_CLIP_DURATION:
            candidates.append({**seg, "subtitle_segments": [seg], "words": seg.get("words", [])})

    if transcript:
        step = max(5, min(12, config.MIN_CLIP_DURATION // 2))
        max_start = max(video_duration - config.MIN_CLIP_DURATION, 0)
        for window_size in _candidate_window_sizes():
            for start in np.arange(0, max_start + 0.1, step):
                end = min(start + window_size, video_duration)
                candidate = _build_candidate_from_window(transcript, start, end)
                # +3 dtk toleransi: lebih baik sedikit lewat batas daripada memotong kalimat
                if candidate and config.MIN_CLIP_DURATION * 0.85 <= candidate["end"] - candidate["start"] <= config.MAX_CLIP_DURATION + 3:
                    candidates.append(candidate)
    return candidates


def _candidate_window_sizes() -> List[int]:
    min_d = int(config.MIN_CLIP_DURATION)
    max_d = int(config.MAX_CLIP_DURATION)
    sizes = [int(size) for size in [min_d, 25, 35, 45, 55, max_d] if min_d <= int(size) <= max_d]
    if max_d >= 60:
        sizes.append(60)
    return sorted(set(sizes))


_CONNECTIVE_STARTS = {
    "dan", "tapi", "terus", "lalu", "nah", "jadi", "karena", "makanya", "soalnya", "trus", "atau", "sama",
    "and", "but", "so", "because", "or", "then",
}
_SENTENCE_CACHE: Dict[int, List[Dict]] = {}


def _sentences(transcript: List[Dict]) -> List[Dict]:
    """Kalimat dari timestamp kata (batas: . ? ! atau jeda > 0.9 dtk). Tanpa kata: tiap segmen = kalimat."""
    key = id(transcript)
    cached = _SENTENCE_CACHE.get(key)
    if cached is not None and cached and cached[-1].get("_n") == len(transcript):
        return cached
    words = [w for s in transcript for w in (s.get("words") or []) if str(w.get("word", "")).strip()]
    sentences: List[Dict] = []
    if len(words) >= max(10, len(transcript)):
        current: List[Dict] = []
        for i, w in enumerate(words):
            current.append(w)
            nxt = words[i + 1] if i + 1 < len(words) else None
            ends = str(w["word"]).rstrip().endswith((".", "?", "!"))
            pause = nxt is not None and float(nxt["start"]) - float(w["end"]) > 0.9
            if ends or pause or nxt is None:
                sentences.append({"start": float(current[0]["start"]), "end": float(current[-1]["end"]),
                                  "text": " ".join(str(x["word"]).strip() for x in current), "words": current})
                current = []
    else:
        sentences = [{"start": float(s["start"]), "end": float(s["end"]), "text": s["text"].strip(),
                      "words": s.get("words") or []} for s in transcript if s.get("text", "").strip()]
    if sentences:
        sentences[-1]["_n"] = len(transcript)
    _SENTENCE_CACHE.clear()
    _SENTENCE_CACHE[key] = sentences
    return sentences


def _first_word(text: str) -> str:
    match = re.match(r"\W*([\w']+)", text.lower())
    return match.group(1) if match else ""


def _build_candidate_from_window(transcript: List[Dict], start: float, end: float) -> Optional[Dict]:
    """Bangun kandidat dari jendela waktu, DIBULATKAN ke batas kalimat.

    FIX v4: dulu akhir klip dipotong di ujung jendela (di tengah kalimat: '...Aku ringkasin',
    '...kita mauin') dan awal bisa dimulai dengan kata sambung ('Nah, tapi...'). Sekarang:
    - awal = awal kalimat terdekat; bila diawali kata sambung, kalimat sebelumnya ikut (konteks)
    - akhir = akhir kalimat terdekat yang masih dalam batas durasi
    """
    sents = _sentences(transcript)
    if not sents:
        return None
    min_d, max_d = float(config.MIN_CLIP_DURATION), float(config.MAX_CLIP_DURATION)

    # --- awal: kalimat yang mulainya paling dekat dengan `start` (rentang -6..+3 dtk)
    start_candidates = [i for i, s in enumerate(sents) if start - 6 <= s["start"] <= start + 3]
    if not start_candidates:
        start_candidates = [i for i, s in enumerate(sents) if s["end"] > start][:1]
        if not start_candidates:
            return None
    si = min(start_candidates, key=lambda i: abs(sents[i]["start"] - start))
    if _first_word(sents[si]["text"]) in _CONNECTIVE_STARTS and si > 0 and sents[si]["start"] - sents[si - 1]["start"] <= 12:
        si -= 1
    s_start = sents[si]["start"]

    # --- akhir: akhir kalimat paling dekat dengan `end`, tetap di [min_d, max_d + 2]
    end_candidates = [j for j in range(si, len(sents))
                      if min_d * 0.85 <= sents[j]["end"] - s_start <= max_d + 2]
    if not end_candidates:
        # kalimat sangat panjang: ambil kalimat terakhir yang masih muat, atau satu-satunya yang ada
        fitting = [j for j in range(si, len(sents)) if sents[j]["end"] - s_start <= max_d + 2]
        if not fitting:
            return None
        end_candidates = [fitting[-1]]
    ej = min(end_candidates, key=lambda j: abs(sents[j]["end"] - end))
    # jangan akhiri dengan kalimat yang menggantung (mis. diakhiri koma) bila ada alternatif dekat
    if not sents[ej]["text"].rstrip().endswith((".", "?", "!")):
        closed = [j for j in end_candidates if sents[j]["text"].rstrip().endswith((".", "?", "!")) and abs(sents[j]["end"] - end) <= 8]
        if closed:
            ej = min(closed, key=lambda j: abs(sents[j]["end"] - end))
    chosen = sents[si:ej + 1]
    # buang "ekor" sisa percakapan (mis. "Ya.", "Oke.", "Ya, ya.") agar klip berakhir di inti/payoff
    while len(chosen) > 2 and len(re.findall(r"\w+", chosen[-1]["text"])) <= 2:
        chosen = chosen[:-1]
    snapped_start = max(s_start - 0.15, 0.0)
    snapped_end = chosen[-1]["end"] + 0.35

    words = [w for s in chosen for w in s["words"]]
    subtitle_segments = [s for s in transcript if max(s["start"], snapped_start) < min(s["end"], snapped_end)]
    return {
        "start": snapped_start,
        "end": snapped_end,
        "text": " ".join(s["text"] for s in chosen).strip(),
        "subtitle_segments": subtitle_segments,
        "words": words,
    }


def _dedupe_candidates(candidates: List[Dict]) -> List[Dict]:
    seen, deduped = set(), []
    for candidate in candidates:
        key = (round(candidate["start"], 1), round(candidate["end"], 1))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def _duration_summary(candidates: List[Dict]) -> str:
    if not candidates:
        return "-"
    buckets = Counter(int(round(c["end"] - c["start"], -1)) for c in candidates)
    return ", ".join(f"{d}s={n}" for d, n in sorted(buckets.items()))


# ============================================================ AI Moment Finder (NEW v4)

_MOMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "moments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "score": {"type": "integer"},
                    "category": {"type": "string"},
                    "reason": {"type": "string"},
                    "hook": {"type": "string"},
                },
                "required": ["start", "end", "score", "category", "reason", "hook"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["moments"],
    "additionalProperties": False,
}

_MOMENT_SYSTEM = (
    "Kamu editor short-form video kelas dunia (TikTok, YouTube Shorts, Instagram Reels). "
    "Tugasmu memilih potongan dari video panjang yang paling mungkin ditonton sampai habis, "
    "di-share, dan dikomentari. Jawab hanya dengan JSON sesuai format yang diminta."
)


def _ai_moment_candidates(transcript: List[Dict], video_duration: float) -> List[Dict]:
    """Minta AI membaca transkrip bertimestamp lalu mengusulkan momen terbaik (start/end sendiri)."""
    if not transcript:
        return []
    lines = [f"[{s['start']:.1f}-{s['end']:.1f}] {s['text'].strip()}" for s in transcript if s.get("text", "").strip()]
    chunks, current, size = [], [], 0
    for line in lines:
        if current and size + len(line) > config.AI_MOMENT_CHUNK_CHARS:
            chunks.append(current)
            current = current[-3:]  # sedikit overlap konteks antar potongan
            size = sum(len(x) for x in current)
        current.append(line)
        size += len(line) + 1
    if current:
        chunks.append(current)

    def _span(chunk: List[str]) -> tuple:
        nums: List[float] = []
        for line in chunk:
            m = re.match(r"\[([\d.]+)-([\d.]+)\]", line)
            if m:
                nums += [float(m.group(1)), float(m.group(2))]
        return (min(nums), max(nums)) if nums else (0.0, 0.0)

    def _hint_block(chunk: List[str]) -> str:
        lo, hi = _span(chunk)
        out = ""
        hs = [h for h in SIGNAL_HINTS if lo - 5 <= float(h["t"]) <= hi + 5]
        if hs:
            rows = "\n".join(f"- detik {float(h['t']):.0f}: {h.get('label') or h.get('kind')}" for h in sorted(hs, key=lambda h: float(h["t"])))
            out += ("\n\nSinyal dari siaran live (sudah dicocokkan ke waktu video). Ini bukti nyata penonton/streamer "
                    "bereaksi — momen yang mencakup detik ini (biasanya 5–20 detik SEBELUM-nya adalah pemicunya) sangat "
                    "layak dipilih walau kata-katanya sedikit (aksi game, teriakan, tawa):\n" + rows)
        if AUDIO is not None and not SIGNAL_HINTS:
            ah = AUDIO.peaks(lo, hi)
            if ah:
                rows = "\n".join(f"- detik {h['t']:.0f}: {h['label']}" for h in ah)
                out += ("\n\nSinyal dari SUARA video (diukur dari audio, bukan tebakan): di sini pembicara tertawa atau "
                        "bicara jauh lebih bersemangat dari biasanya. Momen emosional/lucu sering ada di sekitar titik ini — "
                        "pertimbangkan, tetapi isi yang utuh dan menarik tetap syarat utama:\n" + rows)
        return out
    if len(chunks) > config.AI_MOMENT_MAX_CHUNKS:
        logger.warning(f"Transkrip panjang: hanya {config.AI_MOMENT_MAX_CHUNKS} dari {len(chunks)} potongan dianalisis AI Moment Finder.")
        chunks = chunks[:config.AI_MOMENT_MAX_CHUNKS]

    per_chunk = max(3, min(10, config.MAX_CLIPS_PER_VIDEO))

    def _ask(index: int, chunk: List[str]):
        from . import viral_standard

        rules = [
            f"Durasi tiap momen {config.MIN_CLIP_DURATION}-{config.MAX_CLIP_DURATION} detik; start/end harus jatuh di batas kalimat dari transkrip.",
            "Momen harus utuh: ada hook/konteks di awal, inti, dan payoff/ending. Jangan mulai di tengah kalimat.",
            "Utamakan: pernyataan kuat/kontroversial, cerita dengan twist, insight praktis, humor, emosi, opini yang memancing komentar.",
            "score 0-100 = peluang viral nyata (jangan murah memberi >85).",
            f"category salah satu: {', '.join(CATEGORIES)}.",
            "reason 1 kalimat; hook = teks pembuka layar maks 10 kata, bahasa sama dengan video.",
            viral_standard.SELECTION_RULES,
        ]
        prompt = (
            f"Transkrip video (format [mulai-selesai detik] teks), bagian {index}/{len(chunks)}:\n"
            + "\n".join(chunk)
            + _hint_block(chunk)
            + f"\n\nPilih maksimal {per_chunk} momen terbaik untuk dijadikan klip vertikal.\nAturan:\n"
            + "\n".join("- " + r for r in rules)
            + '\nFormat JSON: {"moments": [{"start": 12.3, "end": 48.9, "score": 82, "category": "shocking", '
            '"reason": "...", "hook": "..."}]}'
        )
        try:
            content = _call_selected_llm(prompt, max_tokens=2500, json_schema=_MOMENT_SCHEMA, system=_MOMENT_SYSTEM)
            return ai.parse_json(content)
        except Exception as exc:
            if isinstance(exc, ai.AIRateLimited):
                _mark_rate_limited()
                return None
            logger.warning(f"AI Moment Finder potongan {index} gagal: {str(exc)[:200]}")
            return {}

    # Potongan transkrip dikirim PARALEL (dulu berurutan: tiap potongan ±1–2 menit, podcast 42 menit = 3 potongan)
    results: List[Dict] = []
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(3, len(chunks))) as pool:
        answers = list(pool.map(lambda ic: _ask(*ic), enumerate(chunks, 1)))
    for data in answers:
        for moment in (data or {}).get("moments") or (data or {}).get("items") or []:
            candidate = _moment_to_candidate(transcript, moment, video_duration)
            if candidate:
                results.append(candidate)
    logger.info(f"🧠 AI Moment Finder: {len(results)} momen diusulkan oleh {config.AI_PROVIDER}/{config.LLM_MODEL}")
    return results


def _moment_to_candidate(transcript: List[Dict], moment: Dict, video_duration: float) -> Optional[Dict]:
    try:
        start = float(moment.get("start", 0))
        end = float(moment.get("end", 0))
    except (TypeError, ValueError):
        return None
    if video_duration:
        start, end = max(0.0, min(start, video_duration)), max(0.0, min(end, video_duration))
    if end <= start:
        return None
    if end - start < config.MIN_CLIP_DURATION:
        end = min(start + config.MIN_CLIP_DURATION, video_duration or start + config.MIN_CLIP_DURATION)
    if end - start > config.MAX_CLIP_DURATION:
        end = start + config.MAX_CLIP_DURATION
    candidate = _build_candidate_from_window(transcript, start, end)
    if not candidate:
        return None
    duration = candidate["end"] - candidate["start"]
    if not (config.MIN_CLIP_DURATION * 0.8 <= duration <= config.MAX_CLIP_DURATION + 3):
        return None
    candidate["ai_moment"] = {
        "score": max(0, min(100, int(moment.get("score", 0) or 0))),
        "category": _normalize_category(moment.get("category", "")),
        "reason": str(moment.get("reason", ""))[:160],
        "hook": str(moment.get("hook", ""))[:90],
    }
    return candidate


# ============================================================ scoring

def _score_segment(seg: Dict, video_info: Dict, face_data: Optional[List], use_llm: bool) -> Dict:
    text = seg["text"].lower()
    original_text = seg["text"].strip()
    duration = seg["end"] - seg["start"]

    reasons: List[str] = []
    category = "general"
    keywords_matched: List[str] = []
    fyp_signals: List[str] = []
    breakdown: Dict[str, float] = {}

    # 1. Keyword (word-boundary)
    keyword_score = 0
    first_keyword_pos = None
    for cat, words in config.VIRAL_KEYWORDS.items():
        for word in words:
            pos = _find_keyword_position(text, word)
            if pos is not None:
                keyword_score += 8
                keywords_matched.append(word)
                if category == "general":
                    category = cat
                if first_keyword_pos is None or pos < first_keyword_pos:
                    first_keyword_pos = pos
    breakdown["keyword_match"] = min(keyword_score, config.SCORING_WEIGHTS["keyword_match"])
    if keywords_matched:
        reasons.append(f"Keyword kuat: {', '.join(keywords_matched[:3])}")

    # 2. Sentimen
    compound = vader_analyzer.polarity_scores(text)["compound"]
    breakdown["sentiment_intensity"] = min(abs(compound) * 100 * 0.4, config.SCORING_WEIGHTS["sentiment_intensity"])
    if compound > 0.6:
        reasons.append("Sangat positif/excited")
        if category == "general":
            category = "motivasi"
    elif compound < -0.5:
        reasons.append("Emotional/negatif kuat (bisa viral)")
        category = "emotional"

    # 3. Hook
    hook_score = 0
    intro_text = " ".join(original_text.split()[:18]).lower()
    if re.search(r"[?!]", intro_text) or text.rstrip().endswith("?"):
        hook_score += 12
        reasons.append("Ada hook pertanyaan/exclamation")
    if re.search(r"\d+%|\d+ juta|\d+ ribu", text) or _any_keyword(text, ["rahasia", "tips", "cara"]):
        hook_score += 10
    if _any_keyword(text, ["kamu", "anda", "lo", "lu", "bro", "sist", "you"]):
        hook_score += 8
    matched_hooks = [p for p in config.HOOK_PATTERNS if _find_keyword_position(intro_text, p) is not None]
    if matched_hooks:
        hook_score += 12
        fyp_signals.append("strong_opening_hook")
        reasons.append(f"Hook awal kuat: {matched_hooks[0]}")
    if first_keyword_pos is not None and first_keyword_pos / max(len(text), 1) <= 0.25:
        hook_score += 5
        fyp_signals.append("keyword_in_opening")
        reasons.append("Kata kunci muncul di pembukaan")
    breakdown["hook_presence"] = min(hook_score, config.SCORING_WEIGHTS["hook_presence"])

    # 4. Durasi
    breakdown["length_score"] = _score_duration_fit(duration)

    # 5. Kelengkapan momen
    completeness = _score_moment_completeness(text, duration)
    breakdown["completeness"] = completeness
    if completeness >= 7:
        fyp_signals.append("complete_moment")
        reasons.append("Momen terasa lebih utuh")

    # 6. Retensi
    retention = _score_retention_curve(text, duration)
    breakdown["retention_curve"] = min(retention, config.SCORING_WEIGHTS["retention_curve"])
    if retention >= 7:
        fyp_signals.append("good_retention_curve")
        reasons.append("Ada alur yang bikin orang bertahan")

    # 7. Shareability
    share = _score_shareability(text)
    breakdown["shareability"] = min(share, config.SCORING_WEIGHTS["shareability"])
    if share >= 7:
        fyp_signals.append("share_or_comment_trigger")

    # 8. Visual
    visual = 5.0
    if face_data:
        visual += len([f for f in face_data if f]) / max(len(face_data), 1) * 10
    breakdown["visual_interest"] = min(visual, config.SCORING_WEIGHTS["visual_interest"])

    local_score = min(max(sum(breakdown.values()), 0), 100)
    if len(keywords_matched) >= 2 and abs(compound) > 0.4:
        local_score = min(local_score + 8, 100)
    if "strong_opening_hook" in fyp_signals and retention >= 6:
        local_score = min(local_score + 6, 100)

    # 9. LLM rerank
    llm_score, llm_reason, ai_hook = 0, "", ""
    pre = seg.get("ai_moment")
    if pre:
        llm_score, llm_reason, ai_hook = pre.get("score", 0), pre.get("reason", ""), pre.get("hook", "")
        if pre.get("category"):
            category = pre["category"]
        fyp_signals.append("ai_selected_moment")
    elif use_llm and not LLM_RATE_LIMITED and _llm_configured():
        result = _get_llm_viral_score(seg, category)
        llm_score, llm_reason, ai_hook = result.get("score", 0), result.get("reason", ""), result.get("hook", "")
        if result.get("category"):
            category = result["category"]

    final_score = local_score
    if llm_score and config.LLM_RERANK_WEIGHT:
        weight = min(max(config.LLM_RERANK_WEIGHT, 0.0), 1.0)
        final_score = local_score * (1 - weight) + llm_score * weight
    breakdown["llm_score"] = llm_score

    # v6.4: bonus sinyal live — penonton & streamer sendiri "bilang" momen ini seru
    hints = _hints_between(seg["start"], seg["end"])
    signal_peaks = []
    if hints:
        bonus = 0.0
        for h in hints:
            s = float(h.get("strength", 1))
            bonus += min(9.0, 3.2 * (s - 1)) if h.get("kind") == "chat" else min(5.0, 1.8 * (s - 1))
        bonus = min(bonus, 18.0)
        if bonus >= 1:
            final_score = min(100.0, final_score + bonus)
            breakdown["live_signal"] = bonus
            top = max(hints, key=lambda h: float(h.get("strength", 0)))
            fyp_signals.append("chat_spike" if top.get("kind") == "chat" else "audio_spike")
            label = f"Chat live meledak ({top['strength']}×)" if top.get("kind") == "chat" else f"Reaksi heboh ({top['strength']}×)"
            reasons.insert(0, label)
        signal_peaks = [float(h["t"]) for h in sorted(hints, key=lambda h: -float(h.get("strength", 0)))]

    # v6.8: cara bicara (dari audio) — penonton merasakan energi, tawa, dan penekanan, bukan hanya kata-kata
    audio_profile = AUDIO.segment_profile(seg["start"], seg["end"]) if AUDIO is not None else None
    if audio_profile:
        a_bonus = max(-5.0, min(6.0, (audio_profile["pct"] - 50) / 50 * 6))
        a_bonus += min(6.0, 3.0 * len(audio_profile["laughs"]))
        if audio_profile["crosses_editorial"]:
            a_bonus -= 4.0  # melintasi transisi bab video sumber → rawan lompat topik
        final_score = max(0.0, min(100.0, final_score + a_bonus))
        breakdown["audio"] = a_bonus
        if audio_profile["laughs"]:
            fyp_signals.append("laughter")
            reasons.append("Ada tawa")
        if audio_profile["pct"] >= 75:
            fyp_signals.append("expressive_delivery")
            reasons.append("Cara bicara sangat ekspresif")

    reason_text = llm_reason or " | ".join(reasons[:3]) or "Momen natural dengan potensi engagement"
    if llm_reason and "live_signal" in breakdown:
        reason_text = f"{reasons[0]} · {llm_reason}"
    category = _normalize_category(category)
    return {
        "score": round(final_score, 1),
        "local_score": round(local_score, 1),
        "category": category,
        "reason": reason_text[:160],
        "keywords_matched": keywords_matched[:5],
        "sentiment": round(compound, 2),
        "llm_score": llm_score,
        "fyp_signals": fyp_signals,
        "breakdown": {k: round(float(v), 1) for k, v in breakdown.items()},
        "upload_caption": _build_upload_caption(original_text, category),
        "hashtags": _suggest_hashtags(category, keywords_matched),
        "hook_text": ai_hook or _build_hook_text(original_text, category),
        "signal_peaks": signal_peaks,
        "audio_profile": audio_profile,
    }


def _score_retention_curve(text: str, duration: float) -> float:
    words = re.findall(r"[\w']+", text)
    if not words:
        return 0
    wps = len(words) / max(duration, 1)
    density = 4 if 1.7 <= wps <= 3.8 else 2 if (1.2 <= wps < 1.7 or 3.8 < wps <= 4.6) else 0
    payoff_hits = sum(1 for kw in config.RETENTION_KEYWORDS if _find_keyword_position(text, kw) is not None)
    sentence_count = max(len(re.findall(r"[.!?]+", text)), 1)
    sentence_score = 3 if len(words) / sentence_count <= 18 else 1
    return min(density + sentence_score + min(payoff_hits * 2, 5), 10)


def _score_duration_fit(duration: float) -> float:
    min_d, max_d = config.MIN_CLIP_DURATION, config.MAX_CLIP_DURATION
    if duration < min_d * 0.85 or duration > max_d + 3:
        return 0
    if max_d >= 55:
        # v4: titik manis short-form 25–45 dtk (dulu 38–60 dtk dapat nilai tertinggi -> klip kepanjangan)
        if 25 <= duration <= 45:
            return 10
        if 18 <= duration < 25 or 45 < duration <= 52:
            return 8
        return 6
    midpoint = (min_d + max_d) / 2
    distance = abs(duration - midpoint) / max((max_d - min_d) / 2, 1)
    return max(4, 10 - distance * 5)


def _score_moment_completeness(text: str, duration: float) -> float:
    words = re.findall(r"[\w']+", text)
    if not words:
        return 0
    score = 3 if len(words) >= 45 else 2 if len(words) >= 25 else 0
    if duration >= 35:
        score += 2
    intro = " ".join(words[:16]).lower()
    outro = " ".join(words[-20:]).lower()
    if _any_keyword(intro, config.HOOK_PATTERNS) or re.search(r"[?!]", text[:140]):
        score += 2
    if _any_keyword(outro, config.RETENTION_KEYWORDS + ["jadi", "makanya", "akhirnya", "ending"]):
        score += 2
    if re.search(r"\b(tapi|terus|lalu|karena|jadi|makanya|akhirnya)\b", text):
        score += 1
    if words[0].lower() in {"dan", "atau", "tapi", "terus", "lalu", "karena"}:
        score -= 2
    return max(0, min(score, 10))


def _score_shareability(text: str) -> float:
    score = 0
    if _any_keyword(text, config.CTA_KEYWORDS):
        score += 4
    if re.search(r"kamu setuju|setuju nggak|pilih mana|menurut kamu|what do you think|\bagree\b", text):
        score += 4
    if re.search(r"\b(3|5|7|10)\b", text) or _any_keyword(text, ["kesalahan", "rahasia", "tips", "cara"]):
        score += 3
    return min(score, 10)


# ============================================================ copy helpers (fallback non-AI)

def _build_upload_caption(text: str, category: str) -> str:
    words = re.sub(r"\s+", " ", text).strip().split()
    first_line = " ".join(words[:14]).rstrip(".,") or "Momen ini wajib ditonton sampai akhir"
    ctas = {
        "humor": "Tag teman yang bakal relate.",
        "motivasi": "Simpan buat pengingat hari ini.",
        "shocking": "Tonton sampai akhir.",
        "emotional": "Pernah ngalamin juga?",
        "educational": "Simpan biar nggak lupa.",
        "engagement": "Kamu pilih yang mana?",
        "general": "Menurut kamu gimana?",
    }
    return f"{first_line}. {ctas.get(category, ctas['general'])}"[:config.UPLOAD_CAPTION_MAX_CHARS].rstrip()


def _suggest_hashtags(category: str, keywords: List[str]) -> List[str]:
    category = _normalize_category(category)
    base = list(config.DEFAULT_HASHTAGS.get(category, config.DEFAULT_HASHTAGS["general"]))
    keyword_tags = ["#" + re.sub(r"[^a-z0-9]", "", k.lower()) for k in keywords]
    combined: List[str] = []
    for tag in base + [t for t in keyword_tags if len(t) > 1]:
        if tag not in combined:
            combined.append(tag)
    return combined[:config.MAX_HASHTAGS]


_DANGLING_WORDS = {"and", "the", "a", "an", "of", "to", "but", "or", "so", "dan", "yang", "di", "ke", "dari",
                   "tapi", "atau", "untuk", "dengan", "itu", "ini", "karena", "jadi", "terus"}


def _build_hook_text(text: str, category: str) -> str:
    """Hook non-AI: kalimat pertama (maks 10 kata), tidak berhenti di kata sambung yang menggantung."""
    clean = re.sub(r"\s+", " ", text).strip()
    first_sentence = re.split(r"(?<=[.!?])\s", clean, maxsplit=1)[0]
    words = first_sentence.split()[:10]
    while words and words[-1].lower().strip(".,!?;:") in _DANGLING_WORDS:
        words.pop()
    first_words = " ".join(words).rstrip(".,;:")
    if len(first_words) >= 18:
        return first_words
    fallback = {
        "humor": "Endingnya nggak kepikiran",
        "motivasi": "Ini mindset yang sering dilupain",
        "shocking": "Ternyata faktanya begini",
        "emotional": "Bagian ini bikin mikir",
        "educational": "Cara simpel yang jarang dibahas",
        "engagement": "Kamu setuju sama ini?",
        "general": "Bagian ini wajib ditonton",
    }
    return fallback.get(category, fallback["general"])


def _normalize_category(category: str) -> str:
    value = str(category or "").strip().lower()
    value = _CATEGORY_ALIASES.get(value, value)
    return value if value in CATEGORIES else "general"


# ============================================================ LLM rerank

_SCORE_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer"},
        "category": {"type": "string"},
        "reason": {"type": "string"},
        "hook": {"type": "string"},
    },
    "required": ["score", "category", "reason", "hook"],
    "additionalProperties": False,
}


def _get_llm_viral_score(seg: Dict, current_category: str) -> Dict:
    segment_info = (
        f"Durasi: {seg['end'] - seg['start']:.1f} detik\n"
        f"Transcript: {seg['text'][:1500]}\n"
        f"Kategori awal: {current_category}\n"
        f"Kategori yang boleh: {', '.join(CATEGORIES)}"
    )
    prompt = config.VIRAL_PROMPT.format(segment_info=segment_info) + (
        '\nTambahkan juga field "hook": teks pembuka layar maks 10 kata (bahasa sama dengan video).'
    )
    try:
        result = ai.parse_json(_call_selected_llm(prompt, max_tokens=400, json_schema=_SCORE_SCHEMA))
        return {
            "score": max(0, min(100, int(float(result.get("score", 0) or 0)))),
            "category": _normalize_category(result.get("category", current_category)),
            "reason": str(result.get("reason", ""))[:140],
            "hook": str(result.get("hook", ""))[:90],
        }
    except ai.AIRateLimited:
        _mark_rate_limited()
    except Exception as exc:
        if ai._looks_rate_limited(exc):
            _mark_rate_limited()
        else:
            logger.warning(f"LLM scoring error: {str(exc)[:200]}")
    # Gagal -> skor 0 = rerank dilewati (tidak menarik skor ke 50).
    return {"score": 0, "reason": ""}


def _mark_rate_limited():
    global LLM_RATE_LIMITED
    if not LLM_RATE_LIMITED:
        logger.warning("LLM rate limit. AI dinonaktifkan untuk sisa video ini; scoring lokal tetap jalan. "
                       "Tip: isi AI_FALLBACK_PROVIDERS di .env agar otomatis pindah provider.")
    LLM_RATE_LIMITED = True


# ============================================================ AI packaging (NEW v4)

_PACKAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "hook": {"type": "string"},
        "hook_alternatives": {"type": "array", "items": {"type": "string"}},
        "title": {"type": "string"},
        "caption": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["hook", "hook_alternatives", "title", "caption", "hashtags"],
    "additionalProperties": False,
}


def _ai_package_clips(clips: List[Dict]) -> None:
    """AI menulis hook, judul, caption & hashtag yang siap upload untuk tiap klip final."""
    done = 0
    for clip in clips:
        if LLM_RATE_LIMITED:
            break
        prompt = (
            "Buat paket upload untuk klip short-form berikut (TikTok / YouTube Shorts / Instagram Reels).\n"
            f"Kategori: {clip.get('category')} | Niche: {clip.get('niche', 'general')} | Durasi: {clip.get('duration', 0):.0f} detik\n"
            f"Transkrip klip:\n{clip.get('transcript') or clip.get('text')}\n\n"
            "Ketentuan (pakai bahasa yang sama dengan transkrip, gaya natural kreator):\n"
            "- hook: teks layar 0-3 detik pertama, maks 10 kata, bikin penasaran tanpa clickbait palsu.\n"
            "- hook_alternatives: 2 variasi hook lain untuk A/B test.\n"
            "- title: judul YouTube Shorts maks 80 karakter, mudah dicari.\n"
            f"- caption: caption upload maks {config.UPLOAD_CAPTION_MAX_CHARS} karakter, diakhiri ajakan komentar/simpan.\n"
            f"- hashtags: {config.MAX_HASHTAGS} hashtag relevan (campuran luas + spesifik), diawali #.\n"
            'Format JSON: {"hook": "...", "hook_alternatives": ["...", "..."], "title": "...", "caption": "...", "hashtags": ["#..."]}'
        )
        data = None
        for attempt in range(2):  # satu jawaban JSON rusak tidak boleh membuat klip kehilangan judul
            try:
                data = ai.parse_json(_call_selected_llm(prompt, max_tokens=700, json_schema=_PACKAGE_SCHEMA))
                break
            except ai.AIRateLimited:
                _mark_rate_limited()
                break
            except Exception as exc:
                logger.warning(f"AI packaging gagal untuk klip {clip.get('start', 0):.0f}s (percobaan {attempt + 1}): {str(exc)[:160]}")
        if LLM_RATE_LIMITED:
            break
        if not data:
            continue
        hook = str(data.get("hook", "")).strip()
        if hook:
            clip["hook_text"] = hook[:90]
        alternatives = [str(h).strip()[:90] for h in (data.get("hook_alternatives") or []) if str(h).strip()]
        if alternatives:
            clip["hook_alternatives"] = alternatives[:3]
        if str(data.get("title", "")).strip():
            clip["ai_title"] = str(data["title"]).strip()[:95]
        if str(data.get("caption", "")).strip():
            clip["upload_caption"] = str(data["caption"]).strip()[:config.UPLOAD_CAPTION_MAX_CHARS]
        tags = []
        for tag in data.get("hashtags") or []:
            tag = "#" + re.sub(r"[^\w]", "", str(tag).lstrip("#"), flags=re.UNICODE)
            if len(tag) > 1 and tag.lower() not in {t.lower() for t in tags}:
                tags.append(tag)
        if tags:
            clip["hashtags"] = tags[:config.MAX_HASHTAGS]
        clip["ai_packaged_by"] = f"{config.AI_PROVIDER}/{config.LLM_MODEL}"
        done += 1
    if done:
        logger.info(f"✍️ AI packaging: {done}/{len(clips)} klip mendapat hook/judul/caption/hashtag dari AI")


# ============================================================ keyword helpers

@lru_cache(maxsize=2048)
def _keyword_pattern(word: str):
    return re.compile(r"(?<![a-z0-9])" + re.escape(word.lower()) + r"(?![a-z0-9])")


def _find_keyword_position(text: str, word: str) -> Optional[int]:
    """Cari kata kunci dengan word-boundary (hindari 'cara' cocok di 'acara', 'ai' di 'sampai')."""
    match = _keyword_pattern(word).search(text.lower())
    return match.start() if match else None


def _any_keyword(text: str, words) -> bool:
    return any(_find_keyword_position(text, w) is not None for w in words)


def _parse_json_object(content: str) -> Dict:
    """Kompatibilitas lama — gunakan ai.parse_json."""
    return ai.parse_json(content)


# ============================================================ selection

def _remove_overlapping_clips(clips: List[Dict], min_gap: float = 5.0) -> List[Dict]:
    selected: List[Dict] = []
    for clip in clips:
        if all(not (clip["start"] < s["end"] + min_gap and clip["end"] > s["start"] - min_gap) for s in selected):
            selected.append(clip)
    return selected


def _apply_diversity_ranking(clips: List[Dict]) -> List[Dict]:
    if len(clips) <= 3:
        return clips
    any_ai_scored = any(c.get("llm_score") for c in clips)
    counts: Counter = Counter()
    adjusted = []
    for clip in clips:
        tier = 1 if (any_ai_scored and clip.get("llm_score")) else 0
        adjusted.append(((tier, clip["score"] - counts[clip["category"]] * 4), clip))
        counts[clip["category"]] += 1
    return [clip for _, clip in sorted(adjusted, key=lambda item: item[0], reverse=True)]


# ============================================================ niche & helpers

def detect_niche(transcript_text: str) -> str:
    text = transcript_text.lower()
    niche_keywords = {
        "gaming": ["game", "gaming", "play", "win", "lose", "boss", "level", "skin", "rank"],
        "finance": ["uang", "investasi", "saham", "crypto", "profit", "rugi", "trading", "modal"],
        "beauty": ["makeup", "skincare", "cantik", "wajah", "lipstik", "foundation", "glow"],
        "motivation": ["sukses", "mindset", "motivasi", "bangkit", "dream", "goal", "rahasia sukses"],
        "education": ["cara", "tips", "tutorial", "belajar", "rahasia", "pro tip", "hack"],
        "comedy": ["lucu", "ngakak", "gokil", "joke", "punchline", "absurd", "wtf"],
        "relationship": ["pacar", "cinta", "mantan", "relationship", "toxic", "date", "nikah"],
        "tech": ["ai", "teknologi", "gadget", "hp", "laptop", "app", "software"],
    }
    scores = {niche: sum(1 for kw in kws if _find_keyword_position(text, kw) is not None)
              for niche, kws in niche_keywords.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] >= 2 else "general"


def get_viral_score_breakdown(seg: Dict) -> Dict:
    """Rincian komponen skor (lokal) untuk visualisasi UI — sama persis dengan _score_segment."""
    return _score_segment(seg, {}, None, use_llm=False)["breakdown"]


def optimize_hooks_with_llm(original_text: str, category: str) -> List[str]:
    """3 hook alternatif via provider AI aktif (fallback template bila AI tidak tersedia)."""
    if _llm_configured():
        prompt = (
            f'Buat 3 hook alternatif super viral (maks 12 kata, bahasa sama dengan teks) untuk klip ini:\n"{original_text[:600]}"\n'
            f'Kategori: {category}\nFormat JSON: {{"hooks": ["...", "...", "..."]}}'
        )
        try:
            hooks = ai.parse_json(_call_selected_llm(prompt, max_tokens=300)).get("hooks") or []
            hooks = [str(h).strip() for h in hooks if str(h).strip()]
            if hooks:
                return hooks[:3]
        except Exception as exc:
            logger.warning(f"Hook optimizer gagal: {str(exc)[:120]}")
    return [original_text[:50], "Kamu harus lihat ini!", "Endingnya nggak kepikiran!"]
