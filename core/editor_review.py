"""Editor-AI: tinjauan kritis tiap klip kandidat SETELAH dipotong, sebelum dirender.

Masalah yang diselesaikan (terbukti pada uji nyata podcast 42 menit):
- AI Moment Finder hanya menebak "peluang viral" dari teks; tidak ada yang memeriksa hasil potongannya.
- Klip dimulai dari tengah percakapan (kata ganti "dia/itu" tanpa referensi) atau berakhir tanpa payoff.
- Hook/judul/caption ditulis dalam panggilan terpisah dan bisa mengarang (mis. caption "belajar di luar kelas
  bisa jadi modal finansial" padahal klip tidak pernah membahasnya).

Cara kerja: tiap kandidat dikirim ke LLM beserta KONTEKS ±30 dtk di sekitarnya (penanda MULAI/AKHIR klip) dan
diminta menilai dengan rubrik, memutuskan keep/trim/reject, menetapkan batas potong baru bila perlu, dan menulis
hook/judul/caption yang WAJIB menyertakan kutipan persis dari klip. Kutipan & kata kunci diperiksa dengan kode
(bukan percaya LLM); klaim yang tidak terdukung dibuang atau diperbaiki.
"""
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Tuple

from loguru import logger

import config
from . import ai_providers as ai

CONTEXT_SEC = 30.0

_SCHEMA = {
    "type": "object",
    "properties": {
        "standalone": {"type": "number"}, "hook": {"type": "number"}, "payoff": {"type": "number"},
        "emotion": {"type": "number"}, "clarity": {"type": "number"},
        "verdict": {"type": "string"},
        "new_start": {"type": "number"}, "new_end": {"type": "number"},
        "problems": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
        "risk": {"type": "string"},
        "hook_text": {"type": "string"}, "hook_quote": {"type": "string"},
        "hook_alternatives": {"type": "array", "items": {"type": "string"}},
        "title": {"type": "string"}, "title_quote": {"type": "string"},
        "thumb_text": {"type": "string"},
        "thumb_alternatives": {"type": "array", "items": {"type": "string"}},
        "caption": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["standalone", "hook", "payoff", "emotion", "clarity", "verdict", "summary", "hook_text",
                 "hook_quote", "title", "title_quote", "thumb_text", "caption", "hashtags"],
}

_SYSTEM = (
    "Kamu editor senior short-form video (TikTok, YouTube Shorts, Reels) yang SKEPTIS dan jujur. "
    "Tugasmu menolak klip yang lemah dan memperbaiki yang bisa diselamatkan. Kamu hanya boleh mengklaim hal yang "
    "benar-benar DIUCAPKAN di dalam klip; dilarang mengarang isi, angka, nama, atau kesimpulan. "
    "Jawab hanya JSON sesuai format."
)

_STOP = set("""yang dan atau untuk dengan dari pada itu ini aku kamu dia kita kami mereka tidak nggak gak bukan sudah udah
belum akan bisa juga saja aja lagi kalau kalo karena tapi tetapi jadi ya iya oke ada apa gimana kenapa sama lebih
sangat banget dalam sebuah oleh agar supaya sih dong deh kok nih tuh lah mau harus punya jika maka saat ketika
the and you that this with for are was have not but""".split())


# kata pembingkai clickbait (bukan klaim isi): boleh dipakai walau tidak diucapkan di klip
_FRAME = set("""ternyata alasan alasannya rahasia rahasianya kenapa mengapa gimana bagaimana cara caranya fakta faktanya
kebenaran sebenarnya justru bahkan cuma hanya semua orang banyak jangan pernah selalu bikin buat bilang dibilang ngomong
katanya akhirnya awalnya diam kaget terkejut nyesel menyesal salah benar beda bedanya tanpa sebelum sesudah setelah
begini begitu inilah itulah hal satu dua tiga terlalu paling sampai hingga langsung seketika berani blak blakan
jujur pengakuan terungkap terbongkar mengejutkan nyata sebenarnya dibalik balik inilah kalian penonton banget parah
gila serius beneran wajib tonton dengar dengerin tunggu akhir ending mindblowing why how what never always truth""".split())


def _tokens(text: str) -> List[str]:
    return re.findall(r"[a-z0-9]+", str(text or "").lower())


def _content_words(text: str) -> List[str]:
    return [w for w in _tokens(text) if len(w) >= 4 and w not in _STOP]


def quote_supported(quote: str, source_text: str, min_ratio: float = 0.8) -> bool:
    """Kutipan harus (hampir) persis ada di teks klip."""
    q = _tokens(quote)
    if len(q) < 3:
        return False
    src = set(_tokens(source_text))
    return sum(1 for w in q if w in src) / len(q) >= min_ratio


def overlap_ratio(claim: str, source_text: str) -> float:
    """Porsi kata isi pada klaim (hook/judul/caption) yang benar-benar muncul di teks klip.
    Kata pembingkai rasa penasaran (_FRAME: "ternyata", "alasan", "rahasia", …) tidak dihitung — itu gaya, bukan klaim."""
    words = [w for w in _content_words(claim) if w not in _FRAME]
    if not words:
        return 1.0
    src = set(_tokens(source_text))
    return sum(1 for w in words if w in src or any(s.startswith(w[:5]) for s in src if len(s) >= 5)) / len(words)


def numbers_supported(claim: str, source_text: str) -> bool:
    """Angka di hook/judul/thumbnail harus benar-benar diucapkan (angka karangan = clickbait menipu)."""
    src = set(_tokens(source_text))
    return all(n in src for n in re.findall(r"\d+", str(claim or "")))


def _speaker_of(seg: Dict) -> str:
    sp = [w.get("speaker") for w in seg.get("words") or [] if w.get("speaker")]
    if not sp:
        return ""
    top = max(set(sp), key=sp.count)
    return str(top).replace("speaker_", "P")


def _context_block(transcript: List[Dict], start: float, end: float) -> str:
    lines = []
    marked_start = marked_end = False
    for s in transcript:
        t0, t1 = float(s["start"]), float(s["end"])
        if t1 < start - CONTEXT_SEC or t0 > end + CONTEXT_SEC:
            continue
        text = str(s.get("text", "")).strip()
        if not text:
            continue
        if not marked_start and t0 >= start - 0.4:
            lines.append(">>> MULAI KLIP")
            marked_start = True
        if not marked_end and t0 >= end - 0.4:
            lines.append("<<< AKHIR KLIP")
            marked_end = True
        sp = _speaker_of(s)
        lines.append(f"[{t0:.1f}-{t1:.1f}]{' ' + sp + ':' if sp else ''} {text}")
    if not marked_end:
        lines.append("<<< AKHIR KLIP")
    return "\n".join(lines)


def _prompt(clip: Dict, transcript: List[Dict], source_title: str, feedback: str = "") -> str:
    dur = clip["end"] - clip["start"]
    return (
        (f"Judul video sumber: {source_title}\n" if source_title else "")
        + f"Klip kandidat: {clip['start']:.1f}-{clip['end']:.1f} dtk (durasi {dur:.0f} dtk). "
        f"Batas durasi klip akhir {config.MIN_CLIP_DURATION}-{config.MAX_CLIP_DURATION} dtk.\n\n"
        "Transkrip dengan konteks sebelum/sesudah (baris di antara >>> MULAI dan <<< AKHIR adalah isi klip):\n"
        + _context_block(transcript, clip["start"], clip["end"])
        + _audio_note(clip)
        + "\n\nNilai klip itu seperti penonton yang BELUM PERNAH melihat video penuhnya:\n"
        "- standalone 0-10: bisa dipahami tanpa konteks sebelumnya? (kata ganti 'dia/itu/begitu' jelas rujukannya? "
        "tidak mulai dengan jawaban atas pertanyaan yang tidak ikut terpotong?)\n"
        "- hook 0-10: apakah 3 detik pertama membuat orang berhenti scroll?\n"
        "- payoff 0-10: ada inti/kesimpulan/punchline yang memuaskan, tidak menggantung atau berakhir di tengah ide?\n"
        "- emotion 0-10: intensitas emosi/opini/cerita yang membuat orang ingin komentar atau share.\n"
        "- clarity 0-10: bahasa jelas, tidak berantakan/saling potong.\n"
        "Jangan murah hati: klip biasa-biasa saja harus mendapat 4-6.\n"
        "verdict: 'keep' | 'trim' | 'reject'. 'trim' = ide bagus tapi batasnya salah: isi new_start dan new_end "
        "(detik, HARUS persis awal/akhir salah satu baris transkrip di atas) agar klip mulai di kalimat pembuka yang "
        "kuat dan berakhir di payoff. 'reject' bila tidak bisa diselamatkan.\n"
        "summary: 1 kalimat tentang apa yang BENAR-BENAR dikatakan klip.\n"
        "risk: 'none' | 'medical' | 'financial' | 'legal' | 'hate' | 'misleading' (saran yang bisa merugikan penonton).\n"
        "\nPAKET UPLOAD — tujuannya membuat orang BERHENTI scroll dan PENASARAN sampai akhir, tapi tetap JUJUR "
        "(setiap fakta/angka/nama harus benar diucapkan di klip). Pakai teknik yang paling cocok dengan isi klip:\n"
        "  1) celah penasaran: sebut akibat/kesimpulan yang mengejutkan TANPA membuka alasannya (jawabannya ada di klip);\n"
        "  2) kontras/kontradiksi: 'Punya X, tapi justru Y';\n"
        "  3) detail spesifik dari klip: angka, nama, benda, situasi konkret (bukan kata abstrak);\n"
        "  4) pernyataan berani/kontroversial yang benar-benar diucapkan, boleh dikutip dalam tanda petik;\n"
        "  5) tantangan langsung ke penonton: 'Kamu masih …?'.\n"
        "DILARANG generik & hambar: 'Tips sukses', 'Motivasi hidup', 'Pelajaran berharga', 'Kisah inspiratif', "
        "'Wajib tonton', 'Ternyata begini' tanpa objek, ringkasan datar seperti 'X menjelaskan pentingnya Y'. "
        "DILARANG menipu: janji yang tidak dibayar klip, angka/nama karangan, emoji berlebihan.\n"
        "Contoh lemah → kuat: 'Pentingnya pendidikan bagi anak' → 'Keluar kuliah? Ini syarat dari papanya'; "
        "'Tips mengatur uang' → 'Gaji 5 juta tapi tetap bisa nabung setengahnya'.\n"
        "hook_text: teks layar 3 detik pertama, maks 8 kata, membuka pertanyaan yang baru terjawab di klip. "
        "hook_quote: kutipan PERSIS (≥3 kata) dari ucapan di klip yang mendasari hook.\n"
        "thumb_text: teks thumbnail 2–5 kata, sangat singkat & kuat, terbaca dalam 1 detik (boleh '?' atau '!'), "
        "berbeda dari hook_text. thumb_alternatives: 2 opsi thumb_text lain dengan teknik berbeda.\n"
        "title: maks 70 karakter, kata paling memancing di depan, tanpa awalan kategori; format bagus: "
        "'X, tapi Y', 'Kenapa …?', kutipan berani dalam tanda petik. title_quote: kutipan PERSIS dari klip yang mendasarinya.\n"
        f"caption: maks {config.UPLOAD_CAPTION_MAX_CHARS} karakter. Baris pertama = kalimat penasaran (BUKAN ringkasan), "
        "lalu 1–2 kalimat konteks yang jujur, akhiri pertanyaan yang memancing orang berdebat di komentar.\n"
        f"hashtags: {config.MAX_HASHTAGS} hashtag relevan (campuran spesifik topik + umum). "
        "hook_alternatives: 2 hook lain dengan teknik berbeda (juga berdasar isi klip).\n"
        "Bahasa sama dengan transkrip, gaya natural kreator. Tulis hook/judul/caption dengan suara kreator atau kutipan "
        "langsung; JANGAN memakai kata 'pembicara', 'narator', atau 'dia' sebagai subjek."
        + (f"\n\nPERBAIKI: {feedback}" if feedback else "")
    )


def _audio_note(clip: Dict) -> str:
    """Catatan cara bicara (diukur dari audio) — transkrip saja tidak memuat nada, tekanan, dan tawa."""
    prof = an().AUDIO
    if prof is None:
        return ""
    desc = prof.describe(prof.segment_profile(clip["start"], clip["end"]))
    if not desc:
        return ""
    return ("\n\nCatatan AUDIO isi klip (diukur dari suara, bukan tebakan): " + desc + ".\n"
            "Pakai ini untuk skor emotion & hook: penyampaian datar membuat isi yang sama terasa lemah; tawa/suara "
            "meninggi menandai momen yang hidup. Bila klip melintasi efek transisi video sumber, periksa apakah topiknya "
            "berganti (standalone/payoff).")


def _ask(clip: Dict, transcript: List[Dict], source_title: str, feedback: str = "") -> Optional[Dict]:
    try:
        raw = an()._call_selected_llm(_prompt(clip, transcript, source_title, feedback), max_tokens=2200,
                                      json_schema=_SCHEMA, system=_SYSTEM, model=config.AI_REVIEW_MODEL)
        return _normalize(ai.parse_json(raw))
    except ai.AIRateLimited:
        an()._mark_rate_limited()
    except Exception as exc:
        logger.warning(f"Editor-AI gagal menilai klip {clip['start']:.0f}s: {str(exc)[:160]}")
    return None


def an():
    from . import analyzer
    return analyzer


_SCORE_KEYS = ("standalone", "hook", "payoff", "emotion", "clarity")


def _normalize(d: Optional[Dict]) -> Optional[Dict]:
    """LLM kadang menaruh skor di dalam objek 'scores'/'rubrik' atau menulis alternatif hook sebagai objek."""
    if not isinstance(d, dict):
        return None
    for key in ("scores", "score", "rubric", "rubrik", "penilaian"):
        if isinstance(d.get(key), dict):
            for k in _SCORE_KEYS:
                if k in d[key] and k not in d:
                    d[k] = d[key][k]
    alts = []
    for a in d.get("hook_alternatives") or []:
        alts.append(str(a.get("hook_text") or a.get("hook") or "") if isinstance(a, dict) else str(a))
    d["hook_alternatives"] = [a for a in alts if a.strip()]
    d["thumb_alternatives"] = [str(a.get("thumb_text") or a.get("text") or "") if isinstance(a, dict) else str(a)
                               for a in (d.get("thumb_alternatives") or []) if a]
    if not all(k in d for k in _SCORE_KEYS):
        return None  # tanpa skor lengkap penilaian tidak bisa dipercaya
    return d


def _num(v, default=0.0) -> float:
    try:
        return max(0.0, min(10.0, float(v)))
    except (TypeError, ValueError):
        return default


def rubric_score(d: Dict) -> float:
    return 10 * (0.22 * _num(d.get("standalone")) + 0.26 * _num(d.get("hook")) + 0.26 * _num(d.get("payoff"))
                 + 0.14 * _num(d.get("emotion")) + 0.12 * _num(d.get("clarity")))


def _first_words(text: str, n: int) -> str:
    w = str(text).split()
    return " ".join(w[:n]).strip(" ,.;:") if w else ""


def _ground_package(d: Dict, clip_text: str) -> Tuple[Dict, List[str]]:
    """Periksa hook/judul/caption terhadap teks klip. Return (paket yang sudah aman, daftar masalah)."""
    problems: List[str] = []
    out = dict(d)
    hook, title, cap = str(d.get("hook_text", "")), str(d.get("title", "")), str(d.get("caption", ""))
    if not quote_supported(d.get("hook_quote", ""), clip_text) or overlap_ratio(hook, clip_text) < 0.5:
        problems.append(f"hook '{hook}' tidak didukung ucapan klip")
    if not quote_supported(d.get("title_quote", ""), clip_text) or overlap_ratio(title, clip_text) < 0.45:
        problems.append(f"judul '{title}' tidak didukung ucapan klip")
    if overlap_ratio(re.sub(r"#\w+", "", cap), clip_text) < 0.5:
        problems.append("caption memuat hal yang tidak dikatakan di klip")
    thumb = str(d.get("thumb_text", ""))
    if not thumb.strip() or overlap_ratio(thumb, clip_text) < 0.5 or len(thumb.split()) > 6:
        problems.append(f"thumb_text '{thumb}' tidak didukung ucapan klip atau terlalu panjang (maks 5 kata)")
    for label, text in (("hook", hook), ("judul", title), ("thumb_text", thumb)):
        if not numbers_supported(text, clip_text):
            problems.append(f"{label} memuat angka yang tidak diucapkan di klip")
    out["thumb_alternatives"] = [t for t in (d.get("thumb_alternatives") or [])
                                 if isinstance(t, str) and t.strip() and overlap_ratio(t, clip_text) >= 0.5
                                 and numbers_supported(t, clip_text) and len(t.split()) <= 6][:2]
    return out, problems


def _apply_fallback(clip: Dict, d: Dict, problems: List[str], clip_text: str) -> None:
    """Bila setelah diperbaiki masih tidak terdukung: pakai kutipan/ringkasan nyata, bukan karangan."""
    hook, title, cap = str(d.get("hook_text", "")), str(d.get("title", "")), str(d.get("caption", ""))
    if any(p.startswith("hook") for p in problems):
        hook = _first_words(d.get("hook_quote") or clip_text, 10)
    if any(p.startswith("judul") for p in problems):
        title = _first_words(d.get("title_quote") or hook or clip_text, 12)
        title = title[:1].upper() + title[1:]
    if any(p.startswith("caption") for p in problems):
        # cadangan tetap memancing: hook + kutipan asli dari klip + pertanyaan, tanpa klaim karangan
        quote = _first_words(d.get("hook_quote") or d.get("title_quote") or clip_text, 16)
        cap = f"{(hook or _first_words(clip_text, 10)).rstrip('.?!')}?\n\"{quote}…\"\nKamu setuju atau nggak? Tulis di komentar 👇"
    thumb = str(d.get("thumb_text", ""))
    if any(p.startswith("thumb_text") for p in problems):
        alts = d.get("thumb_alternatives") or []
        thumb = alts[0] if alts else _first_words(hook or clip_text, 4)
    d["hook_text"], d["title"], d["caption"], d["thumb_text"] = hook, title, cap, thumb


def _consensus(reviews: List[Dict]) -> Dict:
    """Gabungkan beberapa penilaian independen: skor dirata-rata (mengurangi acak/murah hati satu jawaban),
    'reject' hanya bila mayoritas menolak, batas trim dari penilai pertama yang memberi trim."""
    base = dict(max(reviews, key=rubric_score))  # paket upload dari penilaian terbaik
    for k in _SCORE_KEYS:
        base[k] = sum(_num(r.get(k)) for r in reviews) / len(reviews)
    verdicts = [str(r.get("verdict", "keep")).lower().strip() for r in reviews]
    if verdicts.count("reject") * 2 > len(verdicts):
        base["verdict"] = "reject"
    else:
        trim = next((r for r in reviews if str(r.get("verdict", "")).lower().strip() == "trim"
                     and r.get("new_start") is not None and r.get("new_end") is not None), None)
        base["verdict"] = "trim" if trim else "keep"
        if trim:
            base["new_start"], base["new_end"] = trim["new_start"], trim["new_end"]
    base["problems"] = list(dict.fromkeys(str(p) for r in reviews for p in (r.get("problems") or [])))[:6]
    base["_passes"] = len(reviews)
    return base


def _review_one(clip: Dict, transcript: List[Dict], source_title: str) -> Optional[Dict]:
    reviews = [r for r in (_ask(clip, transcript, source_title) for _ in range(max(1, config.AI_REVIEW_PASSES))) if r]
    if not reviews:
        return None
    d = _consensus(reviews)
    clip_text = clip.get("transcript") or clip.get("text") or ""
    d, problems = _ground_package(d, clip_text)
    d["_grounding_retry"] = False
    if problems:
        d2 = _ask(clip, transcript, source_title,
                  feedback="; ".join(problems) + ". Tulis ulang hook/judul/thumb_text/caption: tetap memancing penasaran, "
                           "tapi setiap fakta/angka HANYA dari hal yang diucapkan di klip, dan salin kutipan persisnya.")
        if d2:
            d2, problems2 = _ground_package(d2, clip_text)
            # skor/verdict tetap dari penilaian pertama; paket diambil dari percobaan kedua bila lebih baik
            if len(problems2) <= len(problems):
                for k in ("hook_text", "hook_quote", "hook_alternatives", "title", "title_quote", "caption", "hashtags",
                          "summary", "thumb_text", "thumb_alternatives"):
                    if k in d2:
                        d[k] = d2[k]
                problems = problems2
            d["_grounding_retry"] = True
    if problems:
        _apply_fallback(clip, d, problems, clip_text)
    d["_grounding_problems"] = problems
    return d


def _apply_trim(clip: Dict, d: Dict, transcript: List[Dict], taken: List[Tuple[float, float]]) -> bool:
    try:
        ns, ne = float(d.get("new_start")), float(d.get("new_end"))
    except (TypeError, ValueError):
        return False
    if ne <= ns or ns < clip["start"] - CONTEXT_SEC - 1 or ne > clip["end"] + CONTEXT_SEC + 1:
        return False
    if abs(ns - clip["start"]) < 1.0 and abs(ne - clip["end"]) < 1.0:
        return False
    cand = an()._build_candidate_from_window(transcript, ns, ne)
    if not cand:
        return False
    dur = cand["end"] - cand["start"]
    if not (config.MIN_CLIP_DURATION * 0.8 <= dur <= config.MAX_CLIP_DURATION + 3):
        return False
    old = (clip["start"], clip["end"])
    clip.update({
        "start": cand["start"], "end": cand["end"], "duration": dur,
        "text": (cand["text"][:200] + "...") if len(cand["text"]) > 200 else cand["text"],
        "transcript": cand["text"][:2000],
        "subtitle_segments": cand["subtitle_segments"], "words": cand["words"],
    })
    logger.info(f"  Editor-AI trim: {old[0]:.0f}-{old[1]:.0f} → {cand['start']:.0f}-{cand['end']:.0f}")
    return True


def review_clips(clips: List[Dict], transcript: List[Dict], limit: int, source_title: str = "") -> List[Dict]:
    """Tinjau kandidat (urut skor menurun), buang yang lemah, rapikan batas, tulis ulang paket upload.
    Mengembalikan maksimal `limit` klip, diurutkan ulang berdasarkan skor gabungan."""
    if not clips or not transcript:
        return clips[:limit]
    pool = clips[:min(len(clips), limit + max(4, limit // 2))]
    workers = max(1, int(getattr(config, "AI_RERANK_WORKERS", 4)))
    logger.info(f"🎞️ Editor-AI meninjau {len(pool)} kandidat klip (rubrik: standalone, hook, payoff, emosi, kejelasan)…")
    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(lambda c: _review_one(c, transcript, source_title), pool))

    kept: List[Dict] = []
    rejected = trimmed = 0
    for clip, d in zip(pool, results):
        if d is None:  # tidak bisa ditinjau (rate limit/error) → pertahankan apa adanya, tanpa klaim baru
            clip["review"] = {"status": "unreviewed"}
            kept.append(clip)
            continue
        verdict = str(d.get("verdict", "keep")).lower().strip()
        rub = rubric_score(d)
        weak = _num(d.get("standalone")) < config.REVIEW_MIN_STANDALONE or _num(d.get("payoff")) < config.REVIEW_MIN_PAYOFF
        if verdict == "trim" and _apply_trim(clip, d, transcript, []):
            trimmed += 1
        elif verdict == "trim":
            verdict = "keep"
        if verdict == "reject" or (weak and verdict != "trim") or rub < config.REVIEW_MIN_SCORE:
            rejected += 1
            logger.info(f"  Editor-AI menolak klip {clip['start']:.0f}s (rubrik {rub:.0f}): "
                        f"{'; '.join(map(str, d.get('problems') or []))[:140]}")
            continue
        clip["score"] = round(0.35 * float(clip["score"]) + 0.65 * rub, 1)
        clip["hook_text"] = str(d["hook_text"]).strip()[:90]
        clip["hook_alternatives"] = [str(h).strip()[:90] for h in (d.get("hook_alternatives") or []) if str(h).strip()][:3]
        clip["ai_title"] = str(d["title"]).strip()[:95]
        clip["thumb_text"] = str(d.get("thumb_text") or "").strip()[:48]
        clip["thumb_alternatives"] = [str(t).strip()[:48] for t in (d.get("thumb_alternatives") or []) if str(t).strip()][:2]
        clip["upload_caption"] = str(d["caption"]).strip()[:config.UPLOAD_CAPTION_MAX_CHARS]
        tags = []
        for tag in d.get("hashtags") or []:
            tag = "#" + re.sub(r"[^\w]", "", str(tag).lstrip("#"), flags=re.UNICODE)
            if len(tag) > 1 and tag.lower() not in {t.lower() for t in tags}:
                tags.append(tag)
        if tags:
            clip["hashtags"] = tags[:config.MAX_HASHTAGS]
        clip["reason"] = str(d.get("summary", clip.get("reason", "")))[:160]
        clip["ai_packaged_by"] = f"editor-review:{config.AI_PROVIDER}/{config.LLM_MODEL}"
        clip["review"] = {
            "status": "reviewed", "verdict": verdict, "rubric": round(rub, 1),
            "standalone": _num(d.get("standalone")), "hook": _num(d.get("hook")), "payoff": _num(d.get("payoff")),
            "emotion": _num(d.get("emotion")), "clarity": _num(d.get("clarity")),
            "problems": [str(p) for p in (d.get("problems") or [])][:5],
            "risk": str(d.get("risk", "none")), "grounding_problems": d.get("_grounding_problems", []),
            "grounding_retry": d.get("_grounding_retry", False), "passes": d.get("_passes", 1),
        }
        kept.append(clip)
    logger.info(f"🎞️ Editor-AI: {len(kept)} lolos, {rejected} ditolak, {trimmed} batasnya dirapikan")
    kept = an()._remove_overlapping_clips(sorted(kept, key=lambda c: c["score"], reverse=True))
    return kept[:limit]
