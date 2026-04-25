"""
Viral Detection & Analysis Engine
"""
import json
import re
import urllib.error
import urllib.request
from typing import List, Dict, Tuple, Optional
from collections import Counter
import numpy as np
from textblob import TextBlob
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
from loguru import logger
import config

try:
    from groq import Groq
    GROQ_CLIENT = Groq(api_key=config.GROQ_API_KEY) if config.GROQ_API_KEY else None
except:
    GROQ_CLIENT = None

try:
    import openai
    OPENAI_CLIENT = openai.OpenAI(api_key=config.OPENAI_API_KEY) if config.OPENAI_API_KEY else None
    OPENROUTER_CLIENT = openai.OpenAI(
        api_key=config.OPENROUTER_API_KEY,
        base_url="https://openrouter.ai/api/v1",
    ) if config.OPENROUTER_API_KEY else None
except:
    OPENAI_CLIENT = None
    OPENROUTER_CLIENT = None

vader_analyzer = SentimentIntensityAnalyzer()
LLM_RATE_LIMITED = False

def configure_llm_clients(
    provider: str = "Groq",
    model: str = "",
    groq_api_key: str = "",
    gemini_api_key: str = "",
    openrouter_api_key: str = "",
    ollama_base_url: str = "",
    openai_api_key: str = "",
) -> bool:
    """Configure LLM clients at runtime, for UI-entered API keys."""
    global GROQ_CLIENT, OPENAI_CLIENT, OPENROUTER_CLIENT, LLM_RATE_LIMITED
    LLM_RATE_LIMITED = False

    config.AI_PROVIDER = provider
    if model:
        config.LLM_MODEL = model
    if groq_api_key:
        config.GROQ_API_KEY = groq_api_key.strip()
    if gemini_api_key:
        config.GEMINI_API_KEY = gemini_api_key.strip()
    if openrouter_api_key:
        config.OPENROUTER_API_KEY = openrouter_api_key.strip()
    if ollama_base_url:
        config.OLLAMA_BASE_URL = ollama_base_url.strip().rstrip("/")
    if openai_api_key:
        config.OPENAI_API_KEY = openai_api_key.strip()

    if provider == "Groq":
        if not config.GROQ_API_KEY:
            return False
        try:
            from groq import Groq
            GROQ_CLIENT = Groq(api_key=config.GROQ_API_KEY)
            return True
        except Exception as exc:
            logger.warning(f"Could not configure Groq client: {exc}")
            return False

    if provider == "Gemini":
        return bool(config.GEMINI_API_KEY)

    if provider == "OpenRouter":
        if not config.OPENROUTER_API_KEY:
            return False
        try:
            import openai
            OPENROUTER_CLIENT = openai.OpenAI(
                api_key=config.OPENROUTER_API_KEY,
                base_url="https://openrouter.ai/api/v1",
            )
            return True
        except Exception as exc:
            logger.warning(f"Could not configure OpenRouter client: {exc}")
            return False

    if provider == "Ollama":
        return bool(config.OLLAMA_BASE_URL)

    if provider == "OpenAI":
        if not config.OPENAI_API_KEY:
            return False
        try:
            import openai
            OPENAI_CLIENT = openai.OpenAI(api_key=config.OPENAI_API_KEY)
            return True
        except Exception as exc:
            logger.warning(f"Could not configure OpenAI client: {exc}")
            return False

    return False

def analyze_viral_potential(
    transcript_segments: List[Dict],
    video_info: Dict,
    face_data: Optional[List] = None,
    use_llm: bool = True
) -> List[Dict]:
    """
    Main function: Score every potential segment for virality.
    Returns list of high-potential clips with scores and reasons.
    """
    logger.info("🔍 Starting viral potential analysis...")
    
    clips = []
    video_duration = video_info.get("duration", 0)
    
    # 1. Create candidate segments (scene-aware + sliding window)
    candidates = _create_candidate_segments(transcript_segments, video_duration)
    
    local_scored = [
        (seg, _score_segment(seg, video_info, face_data, use_llm=False))
        for seg in candidates
    ]
    ai_enabled = bool(use_llm and not LLM_RATE_LIMITED and _llm_configured())
    ai_shortlist = set()
    if ai_enabled:
        shortlist_size = min(
            len(local_scored),
            max(config.MAX_CLIPS_PER_VIDEO * 4, config.AI_MAX_CANDIDATES),
        )
        ai_shortlist = {
            id(seg) for seg, _ in sorted(
                local_scored,
                key=lambda item: item[1]["score"],
                reverse=True,
            )[:shortlist_size]
        }
        logger.info(f"AI scoring enabled: reranking top {len(ai_shortlist)} of {len(local_scored)} candidates.")

    for seg, local_score_data in local_scored:
        score_data = local_score_data
        if id(seg) in ai_shortlist:
            score_data = _score_segment(seg, video_info, face_data, use_llm=True)

        if score_data["score"] >= config.MIN_VIRAL_SCORE:
            clips.append(_build_clip_payload(seg, score_data))
    
    logger.info(
        f"Generated {len(candidates)} candidates with durations: "
        f"{_duration_summary(candidates)}"
    )

    # Sort by score, remove overlaps
    clips = sorted(clips, key=lambda x: x["score"], reverse=True)
    clips = _remove_overlapping_clips(clips)
    clips = _apply_diversity_ranking(clips)
    
    # Add niche detection to each clip (for niche-specific B-Roll)
    full_text = " ".join([c.get("text", "") for c in clips])
    detected_niche = detect_niche(full_text)
    for clip in clips:
        clip["niche"] = detected_niche
    
    logger.success(f"✅ Found {len(clips)} high-viral-potential clips (min score {config.MIN_VIRAL_SCORE}) | Niche: {detected_niche}")
    return clips[:config.MAX_CLIPS_PER_VIDEO]


def _build_clip_payload(seg: Dict, score_data: Dict) -> Dict:
    return {
        "start": seg["start"],
        "end": seg["end"],
        "duration": seg["end"] - seg["start"],
        "text": seg["text"][:200] + "..." if len(seg["text"]) > 200 else seg["text"],
        "score": round(score_data["score"], 1),
        "category": score_data["category"],
        "reason": score_data["reason"],
        "keywords_matched": score_data.get("keywords_matched", []),
        "sentiment": score_data.get("sentiment", 0),
        "fyp_signals": score_data.get("fyp_signals", []),
        "hook_text": score_data.get("hook_text", ""),
        "upload_caption": score_data.get("upload_caption", ""),
        "hashtags": score_data.get("hashtags", []),
        "subtitle_segments": seg.get("subtitle_segments", []),
        "words": seg.get("words", []),
    }

def _create_candidate_segments(transcript: List[Dict], video_duration: float) -> List[Dict]:
    """Create smart candidate segments from transcript"""
    candidates = []
    
    # Use natural segments from Whisper first
    for seg in transcript:
        duration = seg["end"] - seg["start"]
        if config.MIN_CLIP_DURATION <= duration <= config.MAX_CLIP_DURATION:
            candidates.append({
                **seg,
                "subtitle_segments": [seg],
                "words": seg.get("words", []),
            })
    
    # Add sliding windows for better coverage. Window sizes follow UI config,
    # not a hardcoded 30 seconds, so longer clips can be selected when useful.
    if len(transcript) > 0:
        window_sizes = _candidate_window_sizes()
        step = max(5, min(12, config.MIN_CLIP_DURATION // 2))
        for window_size in window_sizes:
            max_start = max(video_duration - config.MIN_CLIP_DURATION, 0)
            for start in np.arange(0, max_start + 0.1, step):
                end = min(start + window_size, video_duration)
                candidate = _build_candidate_from_window(transcript, start, end)
                if candidate and config.MIN_CLIP_DURATION <= candidate["end"] - candidate["start"] <= config.MAX_CLIP_DURATION:
                    candidates.append(candidate)
    
    return _dedupe_candidates(candidates)


def _candidate_window_sizes() -> List[int]:
    min_d = int(config.MIN_CLIP_DURATION)
    max_d = int(config.MAX_CLIP_DURATION)
    sizes = [min_d, 25, 35, 45, 55, max_d]
    sizes = [int(size) for size in sizes if min_d <= int(size) <= max_d]
    if max_d >= 60:
        sizes.append(60)
    return sorted(set(sizes))


def _build_candidate_from_window(transcript: List[Dict], start: float, end: float) -> Optional[Dict]:
    subtitle_segments = [
        s for s in transcript
        if max(s["start"], start) < min(s["end"], end)
    ]
    if not subtitle_segments:
        return None

    # Snap to transcript boundaries so clips start/end on spoken moments.
    snapped_start = max(float(subtitle_segments[0]["start"]) - 0.3, 0)
    snapped_end = min(float(subtitle_segments[-1]["end"]) + 0.3, end)
    if snapped_end - snapped_start < config.MIN_CLIP_DURATION:
        snapped_end = min(snapped_start + config.MIN_CLIP_DURATION, end)

    words = []
    for s in subtitle_segments:
        words.extend([
            word for word in s.get("words", [])
            if snapped_start <= word.get("start", 0) <= snapped_end
        ])

    return {
        "start": snapped_start,
        "end": snapped_end,
        "text": " ".join(s["text"] for s in subtitle_segments).strip(),
        "subtitle_segments": subtitle_segments,
        "words": words,
    }


def _dedupe_candidates(candidates: List[Dict]) -> List[Dict]:
    seen = set()
    deduped = []
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
    buckets = Counter(int(round(candidate["end"] - candidate["start"], -1)) for candidate in candidates)
    return ", ".join(f"{duration}s={count}" for duration, count in sorted(buckets.items()))

def _score_segment(seg: Dict, video_info: Dict, face_data: Optional[List], use_llm: bool) -> Dict:
    """Score a single segment"""
    text = seg["text"].lower()
    original_text = seg["text"].strip()
    duration = seg["end"] - seg["start"]
    
    score = 0
    reasons = []
    category = "general"
    keywords_matched = []
    fyp_signals = []
    
    # 1. Keyword matching (strong signal for viral)
    keyword_score = 0
    for cat, words in config.VIRAL_KEYWORDS.items():
        for word in words:
            if word.lower() in text:
                keyword_score += 8
                keywords_matched.append(word)
                if cat not in category:
                    category = cat
    score += min(keyword_score, config.SCORING_WEIGHTS["keyword_match"])
    if keywords_matched:
        reasons.append(f"Keyword kuat: {', '.join(keywords_matched[:3])}")
    
    # 2. Sentiment intensity (VADER)
    sentiment = vader_analyzer.polarity_scores(text)
    compound = sentiment["compound"]
    intensity = abs(compound) * 100
    score += min(intensity * 0.4, config.SCORING_WEIGHTS["sentiment_intensity"])
    
    if compound > 0.6:
        reasons.append("Sangat positif/excited")
        category = "motivasi" if "motivasi" not in category else category
    elif compound < -0.5:
        reasons.append("Emotional/negatif kuat (bisa viral)")
        category = "emotional"
    
    # 3. Hook presence (pertanyaan, bold claim, angka)
    hook_score = 0
    intro_text = " ".join(original_text.split()[:18]).lower()
    if re.search(r'\?$|!', text) or re.search(r'[?!]', intro_text):
        hook_score += 12
        reasons.append("Ada hook pertanyaan/exclamation")
    if re.search(r'\d+%|\d+ juta|\d+ ribu|rahasia|tips|cara', text):
        hook_score += 10
    if any(w in text for w in ["kamu", "anda", "lo", "lu", "bro", "sist"]):  # direct address
        hook_score += 8
    matched_hooks = [p for p in config.HOOK_PATTERNS if p in intro_text]
    if matched_hooks:
        hook_score += 12
        fyp_signals.append("strong_opening_hook")
        reasons.append(f"Hook awal kuat: {matched_hooks[0]}")
    score += min(hook_score, config.SCORING_WEIGHTS["hook_presence"])
    
    # 4. Length score follows the user's configured range.
    length_score = _score_duration_fit(duration)
    score += length_score

    # 5. Moment completeness: avoids random fragments with no setup/payoff.
    completeness_score = _score_moment_completeness(text, duration)
    score += completeness_score
    if completeness_score >= 7:
        fyp_signals.append("complete_moment")
        reasons.append("Momen terasa lebih utuh")

    # 6. Retention curve: pace, payoff words, and sentence density.
    retention_score = _score_retention_curve(text, duration)
    score += min(retention_score, config.SCORING_WEIGHTS["retention_curve"])
    if retention_score >= 7:
        fyp_signals.append("good_retention_curve")
        reasons.append("Ada alur yang bikin orang bertahan")

    # 7. Share/comment potential.
    share_score = _score_shareability(text)
    score += min(share_score, config.SCORING_WEIGHTS["shareability"])
    if share_score >= 7:
        fyp_signals.append("share_or_comment_trigger")
    
    # 8. Visual interest (face close-up ratio if available)
    visual_score = 5  # base
    if face_data:
        # Simple: if many frames have face in center-ish, +points
        face_ratio = len([f for f in face_data if f]) / max(len(face_data), 1)
        visual_score += face_ratio * 10
    score += min(visual_score, config.SCORING_WEIGHTS["visual_interest"])
    
    # 9. LLM Scoring (the magic)
    llm_score = 0
    llm_reason = ""
    if use_llm and not LLM_RATE_LIMITED and _llm_configured():
        try:
            llm_result = _get_llm_viral_score(seg, category)
            llm_score = llm_result.get("score", 50)
            llm_reason = llm_result.get("reason", "")
            score += llm_score * (config.SCORING_WEIGHTS["llm_score"] / 100)
            if llm_reason:
                reasons.append(llm_reason)
            if llm_result.get("category"):
                category = llm_result["category"]
        except Exception as e:
            logger.warning(f"LLM scoring failed: {e}")
    
    # Normalize to 0-100
    final_score = min(max(score, 0), 100)
    if llm_score:
        # Let AI meaningfully rerank the moment quality, not just add a tiny bonus.
        final_score = (final_score * 0.58) + (llm_score * 0.42)
    
    # Boost if strong keywords + good sentiment
    if len(keywords_matched) >= 2 and abs(compound) > 0.4:
        final_score = min(final_score + 8, 100)
    if "strong_opening_hook" in fyp_signals and retention_score >= 6:
        final_score = min(final_score + 6, 100)
    
    reason_text = " | ".join(reasons[:3]) or "Momen natural dengan potensi engagement"
    if llm_reason:
        reason_text = llm_reason
    
    return {
        "score": round(final_score, 1),
        "category": category,
        "reason": reason_text[:150],
        "keywords_matched": keywords_matched[:5],
        "sentiment": round(compound, 2),
        "llm_score": llm_score,
        "fyp_signals": fyp_signals,
        "upload_caption": _build_upload_caption(original_text, category),
        "hashtags": _suggest_hashtags(category, keywords_matched),
        "hook_text": _build_hook_text(original_text, category)
    }

def _score_retention_curve(text: str, duration: float) -> float:
    words = re.findall(r"[\w']+", text)
    if not words:
        return 0

    words_per_second = len(words) / max(duration, 1)
    density_score = 0
    if 1.7 <= words_per_second <= 3.8:
        density_score = 4
    elif 1.2 <= words_per_second < 1.7 or 3.8 < words_per_second <= 4.6:
        density_score = 2

    payoff_hits = sum(1 for keyword in config.RETENTION_KEYWORDS if keyword in text)
    sentence_count = max(len(re.findall(r"[.!?]+", text)), 1)
    avg_sentence_words = len(words) / sentence_count
    sentence_score = 3 if avg_sentence_words <= 18 else 1
    payoff_score = min(payoff_hits * 2, 5)
    return min(density_score + sentence_score + payoff_score, 10)


def _score_duration_fit(duration: float) -> float:
    min_d = config.MIN_CLIP_DURATION
    max_d = config.MAX_CLIP_DURATION
    if duration < min_d or duration > max_d:
        return 0
    if max_d >= 55:
        if 38 <= duration <= max_d:
            return 10
        if 25 <= duration < 38:
            return 8
        return 6
    midpoint = (min_d + max_d) / 2
    distance = abs(duration - midpoint) / max((max_d - min_d) / 2, 1)
    return max(4, 10 - distance * 5)


def _score_moment_completeness(text: str, duration: float) -> float:
    words = re.findall(r"[\w']+", text)
    if not words:
        return 0
    score = 0
    if len(words) >= 45:
        score += 3
    elif len(words) >= 25:
        score += 2
    if duration >= 35:
        score += 2
    intro = " ".join(words[:16]).lower()
    outro = " ".join(words[-20:]).lower()
    if any(pattern in intro for pattern in config.HOOK_PATTERNS) or re.search(r"[?!]", text[:140]):
        score += 2
    if any(keyword in outro for keyword in config.RETENTION_KEYWORDS + ["jadi", "makanya", "akhirnya", "ending"]):
        score += 2
    if re.search(r"\b(tapi|terus|lalu|karena|jadi|makanya|akhirnya)\b", text):
        score += 1
    if words[0].lower() in {"dan", "atau", "tapi", "terus", "lalu", "karena"}:
        score -= 2
    return max(0, min(score, 10))

def _score_shareability(text: str) -> float:
    score = 0
    if any(keyword in text for keyword in config.CTA_KEYWORDS):
        score += 4
    if re.search(r"kamu setuju|setuju nggak|pilih mana|menurut kamu|what do you think|agree", text):
        score += 4
    if re.search(r"\b(3|5|7|10)\b|kesalahan|rahasia|tips|cara", text):
        score += 3
    return min(score, 10)

def _build_upload_caption(text: str, category: str) -> str:
    clean = re.sub(r"\s+", " ", text).strip()
    words = clean.split()
    first_line = " ".join(words[:14]).rstrip(".,")
    if not first_line:
        first_line = "Momen ini wajib ditonton sampai akhir"

    ctas = {
        "humor": "Tag teman yang bakal relate.",
        "motivasi": "Simpan buat pengingat hari ini.",
        "shocking": "Tonton sampai akhir.",
        "emotional": "Pernah ngalamin juga?",
        "educational": "Simpan biar nggak lupa.",
        "engagement": "Kamu pilih yang mana?",
        "general": "Menurut kamu gimana?"
    }
    caption = f"{first_line}. {ctas.get(category, ctas['general'])}"
    return caption[:config.UPLOAD_CAPTION_MAX_CHARS].rstrip()

def _suggest_hashtags(category: str, keywords: List[str]) -> List[str]:
    category_aliases = {
        "lucu": "humor",
        "hook": "engagement",
        "education": "educational",
        "motivation": "motivasi"
    }
    category = category_aliases.get(category, category)
    base = list(config.DEFAULT_HASHTAGS.get(category, config.DEFAULT_HASHTAGS["general"]))
    keyword_tags = []
    for keyword in keywords:
        tag = "#" + re.sub(r"[^a-z0-9]", "", keyword.lower())
        if len(tag) > 1:
            keyword_tags.append(tag)
    combined = []
    for tag in base + keyword_tags:
        if tag not in combined:
            combined.append(tag)
    return combined[:config.MAX_HASHTAGS]

def _build_hook_text(text: str, category: str) -> str:
    clean = re.sub(r"\s+", " ", text).strip()
    first_words = " ".join(clean.split()[:10]).rstrip(".,")
    if len(first_words) >= 18:
        return first_words
    fallback = {
        "humor": "Endingnya nggak kepikiran",
        "motivasi": "Ini mindset yang sering dilupain",
        "shocking": "Ternyata faktanya begini",
        "emotional": "Bagian ini bikin mikir",
        "educational": "Cara simpel yang jarang dibahas",
        "engagement": "Kamu setuju sama ini?",
        "general": "Bagian ini wajib ditonton"
    }
    return fallback.get(category, fallback["general"])

def _get_llm_viral_score(seg: Dict, current_category: str) -> Dict:
    """Get viral score from the selected AI provider."""
    global LLM_RATE_LIMITED
    segment_info = f"""
Durasi: {seg['end'] - seg['start']:.1f} detik
Transcript: {seg['text'][:400]}
Kategori awal: {current_category}
"""
    
    prompt = config.VIRAL_PROMPT.format(segment_info=segment_info)
    
    try:
        content = _call_selected_llm(prompt, temperature=0.3, max_tokens=220, json_mode=True)
        result = _parse_json_object(content)
        return {
            "score": int(result.get("score", 50)),
            "category": result.get("category", current_category),
            "reason": result.get("reason", "")[:120]
        }
    except Exception as e:
        if "rate_limit" in str(e).lower() or "429" in str(e):
            LLM_RATE_LIMITED = True
            logger.warning("LLM rate limit reached. Disabling LLM scoring for the rest of this video; local scoring will continue.")
        else:
            logger.warning(f"LLM parse error: {e}")
        return {"score": 50, "reason": ""}


def _llm_configured() -> bool:
    provider = config.AI_PROVIDER
    if provider == "Groq":
        return bool(config.GROQ_API_KEY)
    if provider == "Gemini":
        return bool(config.GEMINI_API_KEY)
    if provider == "OpenRouter":
        return bool(config.OPENROUTER_API_KEY)
    if provider == "Ollama":
        return bool(config.OLLAMA_BASE_URL)
    if provider == "OpenAI":
        return bool(config.OPENAI_API_KEY)
    return False


def _call_selected_llm(prompt: str, temperature: float = 0.3, max_tokens: int = 220, json_mode: bool = False) -> str:
    provider = config.AI_PROVIDER
    if provider == "Groq":
        if not GROQ_CLIENT:
            configure_llm_clients(provider="Groq", model=config.LLM_MODEL)
        kwargs = {
            "model": config.LLM_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        response = GROQ_CLIENT.chat.completions.create(**kwargs)
        return response.choices[0].message.content

    if provider == "Gemini":
        return _call_gemini(prompt, temperature, max_tokens)

    if provider == "OpenRouter":
        if not OPENROUTER_CLIENT:
            configure_llm_clients(provider="OpenRouter", model=config.LLM_MODEL)
        kwargs = {
            "model": config.LLM_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        response = OPENROUTER_CLIENT.chat.completions.create(**kwargs)
        return response.choices[0].message.content

    if provider == "Ollama":
        return _call_ollama(prompt, temperature, max_tokens)

    if provider == "OpenAI":
        if not OPENAI_CLIENT:
            configure_llm_clients(provider="OpenAI", model=config.LLM_MODEL)
        kwargs = {
            "model": config.LLM_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        response = OPENAI_CLIENT.chat.completions.create(**kwargs)
        return response.choices[0].message.content

    raise RuntimeError(f"Unsupported AI provider: {provider}")


def _call_gemini(prompt: str, temperature: float, max_tokens: int) -> str:
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{config.LLM_MODEL}:generateContent?key={config.GEMINI_API_KEY}"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
            "responseMimeType": "application/json",
        },
    }
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=60) as response:
        result = json.loads(response.read().decode("utf-8"))
    return result["candidates"][0]["content"]["parts"][0]["text"]


def _call_ollama(prompt: str, temperature: float, max_tokens: int) -> str:
    url = f"{config.OLLAMA_BASE_URL.rstrip('/')}/api/chat"
    payload = {
        "model": config.LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "format": "json",
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
        },
    }
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=120) as response:
        result = json.loads(response.read().decode("utf-8"))
    return result.get("message", {}).get("content", "")


def _parse_json_object(content: str) -> Dict:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))

def _remove_overlapping_clips(clips: List[Dict], min_gap: float = 5.0) -> List[Dict]:
    """Remove overlapping clips, keep highest score"""
    if not clips:
        return []
    
    selected = [clips[0]]
    for clip in clips[1:]:
        overlap = False
        for sel in selected:
            if (clip["start"] < sel["end"] + min_gap and 
                clip["end"] > sel["start"] - min_gap):
                overlap = True
                break
        if not overlap:
            selected.append(clip)
    
    return selected

def _apply_diversity_ranking(clips: List[Dict]) -> List[Dict]:
    """Avoid filling output with only one category when good alternatives exist."""
    if len(clips) <= 3:
        return clips

    category_counts = Counter()
    adjusted = []
    for clip in clips:
        penalty = category_counts[clip["category"]] * 4
        adjusted.append((clip["score"] - penalty, clip))
        category_counts[clip["category"]] += 1

    return [clip for _, clip in sorted(adjusted, key=lambda item: item[0], reverse=True)]


# ==================== NEW KLIPERS.PRO FEATURES ====================

def detect_niche(transcript_text: str) -> str:
    """Auto-detect content niche from transcript (Klipers.pro style)"""
    text = transcript_text.lower()
    niche_keywords = {
        "gaming": ["game", "gaming", "play", "win", "lose", "boss", "level", "skin", "rank"],
        "finance": ["uang", "investasi", "saham", "crypto", "profit", "rugi", "trading", "modal"],
        "beauty": ["makeup", "skincare", "cantik", "wajah", "lipstik", "foundation", "glow"],
        "motivation": ["sukses", "mindset", "motivasi", "bangkit", "dream", "goal", "rahasia sukses"],
        "education": ["cara", "tips", "tutorial", "belajar", "rahasia", "pro tip", "hack"],
        "comedy": ["lucu", "ngakak", "gokil", "joke", "punchline", "absurd", "wtf"],
        "relationship": ["pacar", "cinta", "mantan", "relationship", "toxic", "date", "nikah"],
        "tech": ["ai", "teknologi", "gadget", "hp", "laptop", "app", "software"]
    }
    scores = {niche: sum(1 for kw in kws if kw in text) for niche, kws in niche_keywords.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] >= 2 else "general"


def get_viral_score_breakdown(seg: Dict) -> Dict:
    """Return detailed breakdown of viral score factors (for UI visualization)"""
    text = seg["text"].lower()
    breakdown = {
        "keyword_match": 0,
        "sentiment": 0,
        "hook_presence": 0,
        "retention_curve": 0,
        "shareability": 0,
        "length_score": 0,
        "visual": 5  # base
    }
    
    # Simplified scoring for breakdown (full logic in _score_segment)
    for cat, words in config.VIRAL_KEYWORDS.items():
        for word in words:
            if word.lower() in text:
                breakdown["keyword_match"] += 8
    
    breakdown["keyword_match"] = min(breakdown["keyword_match"], 25)
    
    # Add more factors...
    return breakdown


def optimize_hooks_with_llm(original_text: str, category: str) -> List[str]:
    """Generate 3 alternative viral hooks using LLM (Klipers.pro Hook Optimizer)"""
    if not (GROQ_CLIENT or OPENAI_CLIENT):
        return [
            f"Hook 1: {original_text[:60]}...",
            f"Hook 2: Kamu nggak bakal percaya ini!",
            f"Hook 3: Rahasia yang baru gue temuin..."
        ]
    
    prompt = f"""Buat 3 hook alternatif super viral untuk TikTok/Shorts dari teks ini:
"{original_text[:200]}"
Kategori: {category}
Hook harus pendek (max 12 kata), bikin orang penasaran, pakai bahasa gaul Indonesia.
Jawab hanya dalam format JSON: {{"hooks": ["hook1", "hook2", "hook3"]}}"""
    
    try:
        if GROQ_CLIENT:
            resp = GROQ_CLIENT.chat.completions.create(
                model=config.LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7, max_tokens=150
            )
            content = resp.choices[0].message.content
        else:
            return ["Hook 1 (fallback)", "Hook 2 (fallback)", "Hook 3 (fallback)"]
        
        result = json.loads(content)
        return result.get("hooks", [])[:3]
    except:
        return [original_text[:50], "Kamu harus lihat ini!", "Endingnya gila!"]
