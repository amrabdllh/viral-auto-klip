"""Penulis teks thumbnail & judul clickbait (v6.9) — 2 tahap: PENULIS membuat kandidat, JURI menilai.

Kenapa perlu juri: Editor-AI cenderung mengutip potongan ucapan kreator ("NGGAK SLOP, MEN", "UFO NGULIK IBU") yang
hanya nyambung bagi orang yang SUDAH menonton. Thumbnail justru dibaca orang yang BELUM menonton, dalam ±1 detik.
Juri menilai tiap kandidat: (1) jelas tanpa konteks, (2) memicu rasa penasaran, (3) jujur terhadap isi klip.
Yang menang dipakai; fakta/angka karangan tetap ditolak (core/editor_review.numbers_supported).
"""
from __future__ import annotations

import json
import re
from typing import Dict, List, Optional

from loguru import logger

import config

_WRITER = """Kamu copywriter kanal clip YouTube Shorts/TikTok Indonesia yang videonya selalu FYP.
Tulis teks THUMBNAIL dan JUDUL untuk klip di bawah.

ATURAN TEKS THUMBNAIL (paling penting):
- 2–5 kata, HURUF BESAR, terbaca dalam 1 detik di layar HP.
- JELAS TANPA KONTEKS: orang yang BELUM menonton langsung paham siapa/apa yang terjadi. Sebut subjeknya dengan nama
  yang dikenal penonton (nama game, tokoh, benda kunci: GTA 6, LUCIA, JASON, UFO, ALIEN, POLISI…).
- DILARANG potongan ucapan/slang yang hanya dimengerti setelah menonton, kata ganti tanpa rujukan, atau kalimat rusak.
  Contoh BURUK: "NGGAK SLOP, MEN" · "UFO NGULIK IBU" · "SIAPA-SIAPA COY" · "INI DIA".
- Julukan/istilah yang hanya dikenal dari dalam klip (mis. "IBU GEMOY", "SI CLEAN") → ganti dengan nama/peran yang
  dipahami orang awam (mis. LUCIA, NPC, POLISI) atau buang.
- MEMICU PENASARAN: pakai salah satu teknik — celah informasi (akibat tanpa sebab), kontras/kejutan ("X TAPI Y"),
  reaksi ekstrem, pertanyaan yang ingin dijawab, angka/fakta spesifik yang memang ada.
- Boleh diakhiri ?! atau !! — tanpa emoji.
  Contoh BAGUS: "UFO BENERAN DI GTA 6?!" · "LUCIA LEBIH SERU DARI JASON!" · "HEADSHOT = BAD ENDING?!" · "ALIEN INI CUMA BAYI?"

ATURAN JUDUL:
- Maks 70 karakter, jelas tanpa konteks, kata paling memancing di depan, 1–2 KATA KUNCI HURUF BESAR, boleh 1 emoji di akhir.
- Bentuk yang terbukti: "X SAMPAI Y?!", "NEKAT X… Malah Y!", "Baru Kali Ini X 🤣", "Ternyata X Itu Y?!".

JUJUR & SPESIFIK: teks harus menggambarkan kejadian/ucapan YANG ADA DI KLIP INI. Judul video sumber hanya untuk
mengetahui nama game/tokoh — JANGAN memakai kejadian dari judul video yang tidak terjadi di klip ini (video punya banyak
klip; tiap klip butuh teks tentang momennya sendiri). Jangan mengarang kejadian, angka, nama, atau hal sensitif.
{avoid}
Judul video sumber (hanya untuk nama): {video_title}
Judul klip sekarang: {title}
Isi klip (ucapan kreator):
\"\"\"{text}\"\"\"

Balas JSON: {{"thumb": ["6 kandidat teks thumbnail berbeda teknik"], "title": ["4 kandidat judul"]}}"""

_JUDGE = """Kamu juri thumbnail YouTube Shorts/TikTok. Penonton yang BELUM menonton klip melihat teks ini 1 detik saat scroll.
Nilai SETIAP kandidat:
- jelas 0-10: orang asing paham siapa/apa yang terjadi tanpa menonton? (potongan ucapan/slang/kalimat rusak = maks 3;
  julukan/istilah yang hanya dikenal penonton setia atau dari dalam klip — mis. "IBU GEMOY", "SI CLEAN", "SLOP" — = maks 4)
- penasaran 0-10: seberapa kuat membuat orang berhenti scroll & ingin tahu jawabannya?
- jujur true/false: kejadiannya BENAR-BENAR ada di ISI KLIP di bawah? (hanya ada di judul video = false; mengarang = false)

Isi klip: \"\"\"{text}\"\"\"
Judul video sumber (hanya konteks nama, BUKAN bukti kejadian): {video_title}

Kandidat THUMBNAIL:
{thumbs}

Kandidat JUDUL:
{titles}

Balas JSON: {{"thumb": [{{"i": 0, "jelas": 0, "penasaran": 0, "jujur": true}}], "title": [{{"i": 0, "jelas": 0, "penasaran": 0, "jujur": true}}]}}"""


# model terkuat yang tersedia untuk tugas kecil tapi menentukan ini (2 panggilan per klip)
_STRONG = (("DeepSeek", "deepseek-v4-pro"), ("Anthropic", "claude-sonnet-5-5"), ("OpenAI", "gpt-4.1"))


def _ask(prompt: str) -> str:
    from . import ai_providers as ai
    for prov, model in _STRONG:
        try:
            if ai.is_configured(prov) and model in (ai.provider_models(prov) or []):
                return ai.complete(prompt, max_tokens=12000, json_mode=True, provider=prov, model=model)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"{prov}/{model} gagal untuk clickbait: {str(exc)[:120]}")
    return ai.complete(prompt, max_tokens=12000, json_mode=True)


def _json(raw: str) -> Dict:
    raw = str(raw or "")
    return json.JSONDecoder().raw_decode(raw[raw.index("{"):])[0]


def _rank(cands: List[str], scores: List[Dict], src: str) -> List[str]:
    """Kandidat jujur, urut skor (jelas 55% + penasaran 45%). Yang jelas ≥7 didahulukan; sisanya cadangan."""
    from .editor_review import numbers_supported
    good, weak = [], []
    for sc in scores or []:
        try:
            text = cands[int(sc.get("i"))]
        except (TypeError, ValueError, IndexError):
            continue
        clear, curious = float(sc.get("jelas") or 0), float(sc.get("penasaran") or 0)
        if not sc.get("jujur", False) or not numbers_supported(text, src):
            continue
        (good if clear >= 7 else weak).append((clear * 0.55 + curious * 0.45, text))
    return [t for _, t in sorted(good, reverse=True)] + [t for _, t in sorted(weak, reverse=True) if _ >= 5.5]


def write(clip: Dict, video_title: str = "", avoid: Optional[List[str]] = None) -> Dict:
    """{"thumb": [kandidat lolos, terbaik dulu], "title": [...]} — kosong bila gagal (pemanggil memakai teks lama)."""
    text = str(clip.get("transcript") or clip.get("text") or "")[:1500]
    out: Dict[str, List[str]] = {"thumb": [], "title": []}
    if len(text.split()) < 3:
        return out
    try:
        avoid_txt = ("Teks thumbnail klip lain dari video yang sama (JANGAN mirip): " + " · ".join(avoid)) if avoid else ""
        gen = _json(_ask(_WRITER.format(video_title=video_title or "-", title=clip.get("ai_title") or "-", text=text,
                                        avoid=avoid_txt)))
        thumbs = [re.sub(r"\s+", " ", str(t)).strip().upper() for t in gen.get("thumb") or [] if str(t).strip()]
        thumbs = [t for t in thumbs if 2 <= len(t.split()) <= 6 and len(t) <= 38][:8]
        titles = [re.sub(r"\s+", " ", str(t)).strip() for t in gen.get("title") or [] if str(t).strip()]
        titles = [t for t in titles if 12 <= len(t) <= 80][:5]
        old_thumb = str(clip.get("thumb_text") or "").strip()
        if old_thumb and old_thumb.upper() not in thumbs:
            thumbs.append(old_thumb.upper())  # teks lama ikut dinilai — hanya dipakai bila memang menang
        if not thumbs:
            return out
        judge = _json(_ask(_JUDGE.format(
            text=text, video_title=video_title or "-",
            thumbs="\n".join(f"{i}. {t}" for i, t in enumerate(thumbs)),
            titles="\n".join(f"{i}. {t}" for i, t in enumerate(titles)) or "-")))
        roman = {"II": "2", "III": "3", "IV": "4", "V": "5", "VI": "6", "VII": "7", "VIII": "8", "IX": "9", "X": "10"}
        src = f"{text} {video_title} " + " ".join(roman[w] for w in re.findall(r"[IVX]+", video_title) if w in roman)
        out["thumb"] = _rank(thumbs, judge.get("thumb") or [], src)
        out["title"] = _rank(titles, judge.get("title") or [], src) if titles else []
        logger.info(f"Teks thumbnail: {out['thumb'][:1]} (lolos {len(out['thumb'])}/{len(thumbs)}) · judul: {out['title'][:1]}")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Penulis clickbait gagal (pakai teks Editor-AI): {str(exc)[:160]}")
    return out


def apply(clips: List[Dict], video_title: str = "") -> int:
    """Tulis ulang teks thumbnail (selalu) & judul (bila gaya clickbait), 4 klip sekaligus. Tiap klip dapat teks
    BERBEDA: bila kandidat terbaik mirip teks klip lain, dipakai kandidat peringkat berikutnya. Return jumlah berubah."""
    from concurrent.futures import ThreadPoolExecutor
    clickbait = str(getattr(config, "TITLE_STYLE", "normal")).lower() == "clickbait"
    with ThreadPoolExecutor(max_workers=4) as ex:
        results = list(ex.map(lambda c: write(c, video_title), clips))
    used_t: List[str] = []
    used_h: List[str] = []
    changed = 0
    for c, res in zip(clips, results):
        thumb = next((t for t in res["thumb"] if not _similar(t, used_t)), None)
        if thumb:
            c["thumb_text_editor"] = c.get("thumb_text")
            c["thumb_text"] = thumb
            changed += 1
        used_t.append(str(c.get("thumb_text") or ""))
        title = next((t for t in res["title"] if not _similar(t, used_h)), None)
        if clickbait and title:
            c["ai_title_editor"] = c.get("ai_title")
            c["ai_title"] = title
        used_h.append(str(c.get("ai_title") or ""))
    return changed


def _similar(text: str, others: List[str]) -> bool:
    """Mirip teks lain (≥60% kata sama) → dianggap duplikat."""
    w = set(re.findall(r"\w+", text.lower()))
    for o in others:
        ow = set(re.findall(r"\w+", str(o).lower()))
        if w and ow and len(w & ow) / max(1, min(len(w), len(ow))) >= 0.6:
            return True
    return False
