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
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from loguru import logger

import config
from . import ai_providers as ai
from .utils import trim_caption

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


_UNITS = {"nol": 0, "satu": 1, "dua": 2, "tiga": 3, "empat": 4, "lima": 5, "enam": 6, "tujuh": 7, "delapan": 8,
          "sembilan": 9}
_SE = {"sepuluh": 10, "sebelas": 11, "seratus": 100, "seribu": 1000, "sejuta": 1_000_000}
_BIG = {"ribu": 1000, "rb": 1000, "juta": 1_000_000, "jt": 1_000_000, "miliar": 1_000_000_000, "milyar": 1_000_000_000}


def spoken_numbers(text: str) -> set:
    """Angka yang DIUCAPKAN sebagai kata ("dua puluh sembilan" → 29, "lima" → 5, "seribu" → 1000), plus digit
    yang tertulis. Transkrip ElevenLabs/Whisper sering menulis angka dengan kata, sedangkan judul AI memakai digit."""
    words = _tokens(text)
    found = {w for w in words if w.isdigit()}
    found |= {w.lstrip("0") or "0" for w in found}
    total = chunk = pending = 0
    active = False

    def flush():
        nonlocal total, chunk, pending, active
        if active:
            found.add(str(total + chunk + pending))
        total = chunk = pending = 0
        active = False

    for w in words:
        if w in _UNITS:
            if pending:  # "dua tiga" = dua angka berbeda
                flush()
            pending, active = _UNITS[w], True
            found.add(str(_UNITS[w]))
        elif w in _SE:
            v = _SE[w]
            if v >= 1000:
                total += v
            else:
                chunk += pending + v
                pending = 0
            active = True
        elif w == "belas" and active:
            chunk, pending = chunk + 10 + pending, 0
        elif w == "puluh" and active:
            chunk, pending = chunk + pending * 10, 0
        elif w == "ratus" and active:
            chunk, pending = chunk + pending * 100, 0
        elif w in _BIG and active:
            found.add(str(chunk + pending or 1))  # "150 juta" di judul = "seratus lima puluh juta" diucapkan
            total, chunk, pending = total + (chunk + pending or 1) * _BIG[w], 0, 0
        else:
            flush()
    flush()
    return found


def numbers_supported(claim: str, source_text: str) -> bool:
    """Angka di hook/judul/thumbnail harus benar-benar diucapkan (angka karangan = clickbait menipu).
    "1.000"/"1,000" dibaca 1000; angka yang diucapkan dengan kata ("lima kali") sah untuk "5x"."""
    src = spoken_numbers(source_text)
    nums = [re.sub(r"[.,]", "", n) for n in re.findall(r"\d{1,3}(?:[.,]\d{3})+|\d+", str(claim or ""))]
    return all(n in src or (n.lstrip("0") or "0") in src for n in nums)


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
        + (_RUBRIC_GAMING if an().CONTENT_TYPE == "gaming_stream" else _RUBRIC_TALK)
        + _SCALE
        + "verdict: 'keep' | 'trim' | 'reject'. 'trim' = ide bagus tapi batasnya salah: isi new_start dan new_end "
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
        "berbeda dari hook_text, dan JELAS bagi orang yang BELUM menonton: sebut subjek dengan nama yang dikenal "
        "(game/tokoh/benda), bukan potongan ucapan, slang, atau julukan yang hanya nyambung setelah menonton "
        "(buruk: 'NGGAK SLOP, MEN', 'UFO NGULIK IBU'; bagus: 'LUCIA GENDONG JASON?!', 'UFO BENERAN MUNCUL?!'). "
        "thumb_alternatives: 2 opsi thumb_text lain dengan teknik berbeda.\n"
        "title: maks 70 karakter, kata paling memancing di depan, tanpa awalan kategori; format bagus: "
        "'X, tapi Y', 'Kenapa …?', kutipan berani dalam tanda petik. title_quote: kutipan PERSIS dari klip yang mendasarinya.\n"
        f"caption: maks {config.UPLOAD_CAPTION_MAX_CHARS} karakter. Baris pertama = kalimat penasaran (BUKAN ringkasan), "
        "lalu 1–2 kalimat konteks yang jujur, akhiri pertanyaan yang memancing orang berdebat di komentar.\n"
        f"hashtags: {config.MAX_HASHTAGS} hashtag relevan (campuran spesifik topik + umum). "
        "hook_alternatives: 2 hook lain dengan teknik berbeda (juga berdasar isi klip).\n"
        "Bahasa sama dengan transkrip, gaya natural kreator. Tulis hook/judul/caption dengan suara kreator atau kutipan "
        "langsung; JANGAN memakai kata 'pembicara', 'narator', atau 'dia' sebagai subjek."
        + (_CLICKBAIT if str(getattr(config, "TITLE_STYLE", "normal")).lower() == "clickbait" else "")
        + (f"\n\nPERBAIKI: {feedback}" if feedback else "")
    )


# Rubrik per jenis konten. Kunci JSON sama (standalone/hook/payoff/emotion/clarity) tetapi ARTINYA beda: momen viral
# live gaming sering hanya REAKSI (teriak, ketawa, umpatan saat mobil nabrak) tanpa "cerita utuh" atau "kesimpulan" —
# rubrik podcast dulu menghukum momen seperti itu.
_RUBRIC_TALK = (
    "\n\nNilai klip itu seperti penonton FYP yang BELUM PERNAH melihat video penuhnya:\n"
    "- standalone 0-10: bisa dipahami tanpa konteks sebelumnya? (kata ganti 'dia/itu/begitu' jelas rujukannya? "
    "tidak mulai dengan jawaban atas pertanyaan yang tidak ikut terpotong?)\n"
    "- hook 0-10: apakah 3 detik pertama membuat orang berhenti scroll?\n"
    "- payoff 0-10: ada inti/kesimpulan/punchline yang memuaskan, tidak menggantung atau berakhir di tengah ide?\n"
    "- emotion 0-10: intensitas emosi/opini/cerita yang membuat orang ingin komentar atau share.\n"
    "- clarity 0-10: bahasa jelas, tidak berantakan/saling potong.\n"
)
_RUBRIC_GAMING = (
    "\n\nTIPE KONTEN: live streaming gaming (klip vertikal: wajah streamer di atas, gameplay di bawah). Penonton MELIHAT "
    "gameplay dan wajah streamer, jadi yang penting adalah KEJADIAN di game dan REAKSI streamer — bukan kalimat yang "
    "rapi atau cerita utuh. Reaksi spontan, teriakan, umpatan, bahasa gaul, dan kalimat pendek itu justru bahan utama. "
    "Transkrip bisa bercampur dialog karakter game.\n"
    "Nilai seperti penonton TikTok gaming yang lagi scroll:\n"
    "- hook 0-10: dalam 3 detik pertama sudah ada reaksi atau kejadian yang bikin berhenti scroll?\n"
    "- emotion 0-10 (= REAKSI): seberapa kuat & menghibur reaksi streamer — kaget, panik, ngakak, marah, euforia, "
    "nge-roast, umpatan spontan. Reaksi datar/menjelaskan = rendah.\n"
    "- payoff 0-10 (= KEJADIAN): ada momen yang seru & tuntas di klip — gagal konyol, aksi epik, plot twist, bug lucu, "
    "interaksi chat yang kocak. TIDAK perlu kesimpulan atau pelajaran.\n"
    "- standalone 0-10: penonton baru langsung nangkep apa yang lucu/seru dari gameplay + reaksi, tanpa harus nonton "
    "live sebelumnya?\n"
    "- clarity 0-10: reaksi streamer terdengar jelas (tidak tertutup dialog game/musik), alurnya gampang diikuti.\n"
)
_SCALE = (
    "Pakai skala seperti penonton sungguhan, bukan juri: 9-10 = pasti ditonton ulang & dibagikan; 7-8 = seru, "
    "ditonton sampai habis; 5-6 = biasa, kemungkinan di-scroll lewat; 3-4 = membosankan; 0-2 = rusak/tidak jelas. "
    "Jujur — jangan menaikkan nilai klip biasa, tapi jangan pelit pada momen yang memang seru.\n"
)
# bobot rubrik → skor 0-100. Gaming: reaksi & kejadian paling menentukan; standalone/kejelasan pelengkap.
_WEIGHTS_TALK = {"standalone": 0.22, "hook": 0.26, "payoff": 0.26, "emotion": 0.14, "clarity": 0.12}
_WEIGHTS_GAMING = {"standalone": 0.10, "hook": 0.25, "payoff": 0.25, "emotion": 0.30, "clarity": 0.10}


# v6.9: gaya clickbait ala kanal clip Indonesia — tetap lolos pemeriksaan "didukung ucapan klip" (grounding)
_CLICKBAIT = (
    "\n\nGAYA CLICKBAIT (aktif): title & thumb_text bergaya kanal clip Indonesia yang ramai: "
    "1–2 KATA KUNCI dalam HURUF BESAR, akhiri dengan '?!', '!!' atau '…' yang menggantung, boleh SATU emoji ekspresif di akhir "
    "title (😱🤣🔥😭😳), sebut nama kreator/game bila memang ada di judul video atau diucapkan. Pola: 'X SAMPAI Y?!', "
    "'NEKAT X… Malah Y!', 'Baru Kali Ini X 🤣', 'X Bikin Y SHOCK!'. thumb_text: 2–4 kata HURUF BESAR SEMUA, sangat ekspresif. "
    "Baris pertama caption juga dramatis. TETAP JUJUR: tidak ada fakta/nama/peristiwa yang tidak terjadi di klip, tidak "
    "menyiratkan hal sensitif (politik, kematian/kecelakaan nyata, skandal, konten dewasa) yang tidak ada di klip."
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
    except ai.AICancelled:
        return None
    except ai.AIRateLimited:
        an()._mark_rate_limited()
    except Exception as exc:
        logger.warning(f"Editor-AI gagal menilai klip {clip['start']:.0f}s: {str(exc)[:160]}")
    return None


# ============================================================================ ranking pembanding (seperti editor manusia)
#
# Rubrik per klip = mengisi formulir satu per satu; AI tidak tahu "6" itu bagus atau jelek DIBANDING kandidat lain dari
# video yang sama, jadi semua nilai menumpuk di 5–6 dan skor ≥80 nyaris mustahil. Editor manusia menonton semuanya
# lalu MEMBANDINGKAN: "ini paling gila, itu lumayan, sisanya buang". Tahap ini meniru itu: satu panggilan berisi semua
# kandidat → urutan + tier S/A/B/C. Skor akhir terutama dari posisi di urutan itu.

_TIERS = {"S": (86.0, 96.0), "A": (74.0, 85.0), "B": (60.0, 73.0), "C": (40.0, 55.0)}
_TIER_NAME = {"S": "pasti rame", "A": "bagus", "B": "layak upload", "C": "lemah"}
_RANK_SCHEMA = {
    "type": "object",
    "properties": {"ranking": {"type": "array", "items": {"type": "object", "properties": {
        "id": {"type": "integer"}, "tier": {"type": "string"}, "why": {"type": "string"}},
        "required": ["id", "tier"]}}},
    "required": ["ranking"],
}


def _rank_item(i: int, clip: Dict) -> str:
    rv = clip.get("review") or {}
    vis = rv.get("visual") or {}
    text = re.sub(r"\s+", " ", str(clip.get("transcript") or clip.get("text") or "")).strip()
    prof = clip.get("audio_profile") or {}
    audio = []
    if prof.get("laughs"):
        audio.append(f"{len(prof['laughs'])} tawa")
    if prof.get("pct") is not None:
        audio.append(f"energi suara {float(prof['pct']):.0f}/100")
    return (f"#{i} [{float(clip['end']) - float(clip['start']):.0f} dtk] hook: {clip.get('hook_text', '')}\n"
            f"   isi: {rv.get('summary') or clip.get('reason', '')}\n"
            + (f"   terlihat di layar: {vis.get('what')}\n" if vis.get("what") else "")
            + (f"   suara: {', '.join(audio)}\n" if audio else "")
            + f"   kutipan: \"{text[:320]}{'…' if len(text) > 320 else ''}\"")


def rank_clips(clips: List[Dict], source_title: str = "") -> bool:
    """Urutkan semua kandidat sekaligus (bandingkan satu sama lain) → tier S/A/B/C + skor dari posisi. Mengubah klip di
    tempat: score, review.tier/rank/rank_why. Return False bila AI gagal (skor rubrik dipakai apa adanya)."""
    order = sorted(range(len(clips)), key=lambda i: float(clips[i]["start"]))  # urutan waktu = netral, bukan urutan skor
    gaming = an().CONTENT_TYPE == "gaming_stream"
    want = ("reaksi streamer paling kuat dan kejadian game paling seru/konyol/epik" if gaming
            else "pernyataan/cerita paling mengejutkan, lucu, atau memancing debat")
    prompt = (
        (f"Video sumber: {source_title}\n" if source_title else "")
        + f"Ini {len(clips)} kandidat klip pendek dari video yang SAMA. Kamu editor TikTok/Shorts berpengalaman yang "
        "harus memilih mana yang diupload. Bandingkan SEMUANYA satu sama lain — jangan menilai sendiri-sendiri.\n\n"
        + "\n".join(_rank_item(n, clips[i]) for n, i in enumerate(order, 1))
        + f"\n\nUrutkan SEMUA kandidat dari yang paling berpotensi viral ke yang paling lemah. Utamakan {want}; "
        "pertimbangkan juga apakah penonton baru langsung paham dan apakah 3 detik pertamanya menahan scroll.\n"
        "Beri tier: S = pasti rame (ditonton ulang & dibagikan), A = bagus, B = layak upload tapi biasa, "
        "C = lemah (lebih baik tidak diupload). Tier relatif terhadap kandidat lain, tapi tetap jujur: kalau semuanya "
        "biasa, jangan paksa ada S; kalau banyak yang seru, boleh banyak S/A.\n"
        "why: 1 kalimat singkat alasannya, seperti editor ngomong ke timnya.\n"
        'Balas JSON: {"ranking": [{"id": 3, "tier": "S", "why": "..."}, ...]} — semua id muncul tepat sekali, '
        "urutan array = urutan peringkat.")
    raw = an()._call_selected_llm(prompt, max_tokens=4000, json_schema=_RANK_SCHEMA,
                                  system="Kamu editor senior short-form video. Jawab hanya JSON.",
                                  model=config.AI_REVIEW_MODEL)
    rows = (ai.parse_json(raw) or {}).get("ranking") or []
    ranked: List[Tuple[int, str, str]] = []
    seen = set()
    for r in rows:
        try:
            n = int(r.get("id"))
        except (TypeError, ValueError, AttributeError):
            continue
        tier = str(r.get("tier") or "").strip().upper()[:1]
        if 1 <= n <= len(order) and n not in seen and tier in _TIERS:
            seen.add(n)
            ranked.append((order[n - 1], tier, str(r.get("why") or "").strip()[:200]))
    if len(ranked) < max(3, int(len(clips) * 0.6)):
        logger.warning(f"Ranking pembanding tidak lengkap ({len(ranked)}/{len(clips)}) — skor rubrik dipakai")
        return False
    apply_ranking(clips, ranked)
    count = {t: sum(1 for _, x, _ in ranked if x == t) for t in _TIERS}
    logger.info(f"🏆 Ranking pembanding {len(ranked)} klip: " + ", ".join(f"{t}={n}" for t, n in count.items() if n)
                + (f" · teratas: {ranked[0][2][:90]}" if ranked[0][2] else ""))
    return True


def apply_ranking(clips: List[Dict], ranked: List[Tuple[int, str, str]]) -> None:
    """Skor dari posisi: tier memberi rentang (S 86–96, A 74–85, B 60–73, C 40–55), urutan di dalam tier menentukan
    letak di rentang itu. Skor akhir = 75% ranking + 25% skor rubrik/sinyal (tetap ada pengaruh energi suara, chat,
    penilaian visual). Kandidat yang tidak diurutkan AI ditaruh di bawah dan skornya tidak naik."""
    by_tier: Dict[str, List[int]] = {}
    for idx, tier, _ in ranked:
        by_tier.setdefault(tier, []).append(idx)
    position = {idx: (tier, members.index(idx), len(members)) for tier, members in by_tier.items() for idx in members}
    why = {idx: w for idx, _, w in ranked}
    overall = {idx: n for n, (idx, _, _) in enumerate(ranked, 1)}
    for idx, clip in enumerate(clips):
        rv = clip.setdefault("review", {})
        if idx not in position:
            rv["tier"] = None
            clip["score"] = round(min(float(clip["score"]), _TIERS["B"][0]), 1)
            continue
        tier, pos, size = position[idx]
        lo, hi = _TIERS[tier]
        rank_score = hi - (hi - lo) * (pos / max(1, size - 1) if size > 1 else 0.0)
        clip["score"] = round(0.75 * rank_score + 0.25 * float(clip["score"]), 1)
        rv.update(tier=tier, rank=overall[idx], rank_why=why[idx])
        if why[idx]:
            clip["reason"] = f"{_TIER_NAME[tier].capitalize()}: {why[idx]}"[:160]
    # campuran 25% skor lama bisa membuat peringkat #2 sedikit di bawah #3 (uji nyata: A 68,6 vs B 68,7) → paksa urut
    prev = None
    for idx, _, _ in ranked:
        if prev is not None and clips[idx]["score"] >= prev:
            clips[idx]["score"] = round(prev - 0.1, 1)
        prev = clips[idx]["score"]


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


def rubric_score(d: Dict, gaming: Optional[bool] = None) -> float:
    if gaming is None:
        gaming = an().CONTENT_TYPE == "gaming_stream"
    w = _WEIGHTS_GAMING if gaming else _WEIGHTS_TALK
    return 10 * sum(weight * _num(d.get(key)) for key, weight in w.items())


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
    if not (max(config.MIN_CLIP_DURATION * 0.85, 15) <= dur <= config.MAX_CLIP_DURATION + 3):
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


def _fmt_dur(sec: float) -> str:
    return f"{sec:.0f} dtk" if sec < 90 else f"{sec / 60:.0f} mnt"


def _run_logged(fn, items: List, workers: int, label: str, every: float = 30.0) -> List:
    """Seperti ThreadPoolExecutor.map (urutan hasil tetap), tapi melaporkan progres: dulu 45 klip dinilai tanpa satu
    baris log pun selama 30–40 menit sehingga job tampak macet di 42%."""
    results: List = [None] * len(items)
    if not items:
        return results
    t0 = last = time.time()
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(fn, it): i for i, it in enumerate(items)}
        stopping = False
        for fut in as_completed(futures):
            if not stopping and ai.aborted():
                stopping = True  # job dibatalkan: klip yang belum mulai tidak dikerjakan
                for f in futures:
                    f.cancel()
                logger.info(f"{label}: dibatalkan — menunggu {workers} panggilan AI yang sedang jalan")
            if fut.cancelled():
                continue
            results[futures[fut]] = fut.result()
            done += 1
            if stopping:
                continue
            now = time.time()
            if done < len(items) and now - last < every:
                continue
            last = now
            spent = now - t0
            eta = f", ±{_fmt_dur(spent / done * (len(items) - done))} lagi" if done < len(items) else ""
            logger.info(f"{label}: {done}/{len(items)} klip ({_fmt_dur(spent)}{eta})")
    return results


def review_clips(clips: List[Dict], transcript: List[Dict], limit: int, source_title: str = "") -> List[Dict]:
    """Tinjau kandidat (urut skor menurun), buang yang lemah, rapikan batas, tulis ulang paket upload.
    Mengembalikan maksimal `limit` klip, diurutkan ulang berdasarkan skor gabungan."""
    if not clips or not transcript:
        return clips[:limit]
    pool = clips[:min(len(clips), limit + max(4, limit // 2))]
    workers = max(1, int(getattr(config, "AI_RERANK_WORKERS", 4)))
    rubric = "reaksi, kejadian, hook, standalone, kejelasan" if an().CONTENT_TYPE == "gaming_stream"         else "standalone, hook, payoff, emosi, kejelasan"
    passes = max(1, config.AI_REVIEW_PASSES)
    logger.info(f"🎞️ Editor-AI meninjau {len(pool)} kandidat klip (rubrik: {rubric}; {passes} penilaian/klip, "
                f"{workers} paralel)…")
    results = _run_logged(lambda c: _review_one(c, transcript, source_title), pool, workers, "Editor-AI menilai")
    # v6.8: live/gaming → Editor-AI juga MELIHAT 4 frame tiap kandidat (nilai ada di layar, bukan hanya di ucapan)
    visuals: List[Optional[Dict]] = [None] * len(pool)
    video = an().VIDEO_PATH
    if an().CONTENT_TYPE == "gaming_stream" and video and Path(video).exists():
        from . import vision_review
        if vision_review.available():
            todo = [i for i, d in enumerate(results) if d is not None]
            for i, v in zip(todo, _run_logged(lambda i: vision_review.review(pool[i], video), todo, workers,
                                              "Editor-AI melihat frame")):
                visuals[i] = v
            logger.info(f"👁️ Editor-AI melihat {sum(1 for v in visuals if v)} kandidat ({vision_review.pick_model()[1]})")

    kept: List[Dict] = []
    rejected = trimmed = 0
    for clip, d, vis in zip(pool, results, visuals):
        if d is None:  # tidak bisa ditinjau (rate limit/error) → pertahankan apa adanya, tanpa klaim baru
            clip["review"] = {"status": "unreviewed"}
            kept.append(clip)
            continue
        verdict = str(d.get("verdict", "keep")).lower().strip()
        rub = rubric_score(d)
        if vis:  # gabungkan: 60% penilaian teks + 40% penilaian visual (kejadian di layar & kejelasan)
            rub = 0.6 * rub + 0.4 * 10 * (0.65 * vis["visual"] + 0.35 * vis["clarity"])
            d["standalone"] = max(_num(d.get("standalone")), vis["clarity"])
            d["payoff"] = max(_num(d.get("payoff")), 0.9 * vis["visual"])
            if verdict == "reject" and vis["visual"] >= 7:
                verdict = "keep"  # teks lemah tapi kejadian di layar kuat → jangan dibuang
        # live/gaming: AI hanya membaca teks (tidak melihat gameplay) → ambang lebih longgar agar momen visual tidak terbuang
        gaming = an().CONTENT_TYPE == "gaming_stream"
        min_st, min_pay = (4.0, 4.5) if gaming else (config.REVIEW_MIN_STANDALONE, config.REVIEW_MIN_PAYOFF)
        weak = _num(d.get("standalone")) < min_st or _num(d.get("payoff")) < min_pay
        if verdict == "trim" and _apply_trim(clip, d, transcript, []):
            trimmed += 1
            # v6.8 FIX: paket ditulis dari teks SEBELUM dipangkas → hook bisa membahas bagian yang sudah terbuang
            # (mis. hook "Nyari Bigfoot…" padahal klip akhirnya soal loot senjata). Periksa ulang ke teks baru.
            new_text = clip.get("transcript") or clip.get("text") or ""
            _, probs = _ground_package(d, new_text)
            if probs:
                alt = next((h for h in d.get("hook_alternatives") or [] if overlap_ratio(h, new_text) >= 0.5), None)
                if alt and any(p.startswith("hook") for p in probs):
                    d["hook_text"] = alt
                    _, probs = _ground_package(d, new_text)
                if probs:
                    _apply_fallback(clip, d, probs, new_text)
                d["_grounding_problems"] = list(d.get("_grounding_problems") or []) + [f"setelah trim: {p}" for p in probs]
        elif verdict == "trim":
            verdict = "keep"
        if verdict == "reject" or (weak and verdict != "trim") or rub < (config.REVIEW_MIN_SCORE - (6 if gaming else 0)):
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
        clip["upload_caption"] = trim_caption(str(d["caption"]))
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
            "summary": str(d.get("summary") or "")[:300],  # momen utama (dipakai penulis paket judul)
            "standalone": _num(d.get("standalone")), "hook": _num(d.get("hook")), "payoff": _num(d.get("payoff")),
            "emotion": _num(d.get("emotion")), "clarity": _num(d.get("clarity")),
            "problems": [str(p) for p in (d.get("problems") or [])][:5],
            "risk": str(d.get("risk", "none")), "grounding_problems": d.get("_grounding_problems", []),
            "grounding_retry": d.get("_grounding_retry", False), "passes": d.get("_passes", 1),
            **({"visual": vis} if vis else {}),
        }
        kept.append(clip)
    logger.info(f"🎞️ Editor-AI: {len(kept)} lolos, {rejected} ditolak, {trimmed} batasnya dirapikan")
    if getattr(config, "AI_COMPARE_RANKING", True) and len(kept) >= 3:
        try:
            rank_clips(kept, source_title)
        except Exception as exc:  # noqa: BLE001 - ranking opsional; skor rubrik tetap dipakai
            logger.warning(f"Ranking pembanding dilewati: {str(exc)[:160]}")
    kept = an()._remove_overlapping_clips(sorted(kept, key=lambda c: c["score"], reverse=True))
    kept = an()._remove_topic_duplicates(kept)
    return kept[:limit]
