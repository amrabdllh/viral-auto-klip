"""Paket judul + teks thumbnail + teks hook yang SINKRON (v6.9) — 2 tahap: PENULIS membuat paket, JURI menilai.

Kenapa paket: dulu judul (Editor-AI), teks thumbnail, dan kartu hook di 3 detik pertama video ditulis terpisah — penonton
melihat tiga kalimat berbeda untuk satu klip ("Mencuri dengan gampang…" / "MOBIL PERTAMA GUA" / "Mencuri mobil,
ternyata segampang ini"). Sekarang ketiganya ditulis sebagai SATU paket tentang momen & sudut yang sama: thumbnail
memancing, judul melengkapi, hook di video langsung menyambung rasa penasaran orang yang mengeklik.

Juri menilai tiap paket: jelas tanpa konteks · memancing penasaran · jujur pada isi klip · sinkron antar-teks.
Yang sudah terlanjur dipakai (judul yang sudah tayang, hook yang sudah tercetak di video) dikunci & diikuti paketnya.
Dijalankan SEBELUM render (core/tools) agar kartu hook di video ikut paket; untuk klip yang sudah dirender, hook dikunci.
"""
from __future__ import annotations

import json
import re
from typing import Dict, List, Optional

from loguru import logger

import config

_STYLE = {
    "clickbait": ("Gaya CLICKBAIT kanal clip Indonesia: 1–2 KATA KUNCI di judul HURUF BESAR, boleh 1 emoji ekspresif di "
                  "akhir judul (😱🤣🔥😭😳), akhiran '?!' / '!!' / '…' yang menggantung."),
    "normal": "Gaya natural kreator: huruf biasa, tanpa emoji, tetap memancing rasa penasaran.",
}

_WRITER = """Kamu copywriter kanal clip YouTube Shorts/TikTok Indonesia yang videonya selalu FYP.
Tulis 5 PAKET untuk klip di bawah. Satu paket = THUMBNAIL + JUDUL + HOOK yang membahas MOMEN & SUDUT YANG SAMA.

THUMBNAIL (paling penting): 2–5 kata, HURUF BESAR, terbaca 1 detik di layar HP, JELAS bagi orang yang BELUM menonton
(sebut subjek dengan nama yang dikenal: game/tokoh/benda — GTA 6, LUCIA, JASON, UFO, POLISI…). DILARANG potongan
ucapan/slang/julukan yang hanya nyambung setelah menonton (buruk: "NGGAK SLOP, MEN", "UFO NGULIK IBU", "MOBIL PERTAMA GUA"
bila judulnya soal mencuri mobil). Teknik: celah informasi, kontras/kejutan, reaksi ekstrem, pertanyaan.
JUDUL: SINGKAT — 4–8 kata, maks 50 karakter (sependek thumbnail, sedikit lebih jelas), tanpa hashtag, jelas tanpa
konteks, kata paling memancing di depan (celah informasi / kontras / reaksi / pertanyaan). {style}
HOOK: teks layar 3 detik pertama video, maks 8 kata, kalimat biasa (bukan huruf besar semua) — MENYAMBUNG rasa
penasaran dari thumbnail (orang yang mengeklik langsung merasa "ini dia jawabannya, tonton sampai habis").
SINKRON: thumbnail, judul, dan hook membicarakan hal yang SAMA dan tidak bertentangan; jangan mengulang kata persis —
saling melengkapi.
SIAPA MELAKUKAN APA harus persis seperti di klip: "gua/aku/kita" = kreator/pemain; JANGAN menisbatkan tindakan ke tokoh
bernama (mis. LUCIA, JASON) kecuali klip jelas menyebut tokoh itu yang melakukannya — salah pelaku = bohong.
JUJUR & SPESIFIK: hanya kejadian/ucapan yang ADA DI KLIP INI. Judul video sumber hanya untuk tahu nama game/tokoh — bukan
bukti kejadian. Jangan mengarang kejadian, angka, nama, atau hal sensitif.
{streamer}{patterns}{locked}{avoid}
Judul video sumber (hanya untuk nama): {video_title}
{focus}Isi klip (ucapan kreator):
\"\"\"{text}\"\"\"

Balas JSON: {{"paket": [{{"thumb": "...", "title": "...", "hook": "..."}}]}}"""

_JUDGE = """Kamu juri paket Shorts/TikTok. Penonton yang BELUM menonton melihat THUMBNAIL + JUDUL 1 detik saat scroll;
yang mengeklik langsung melihat HOOK di awal video.
Nilai SETIAP paket (0-10, jujur true/false):
- jelas: thumbnail & judul dipahami orang asing tanpa menonton? (potongan ucapan/slang/julukan dalam = maks 4)
- penasaran: seberapa kuat membuat orang berhenti scroll & ingin tahu?
- sinkron: thumbnail, judul, hook membahas hal yang sama & saling menyambung? (topik beda / bertentangan = maks 3)
- jujur: semua yang diklaim BENAR-BENAR ada di ISI KLIP? Periksa PELAKU tiap tindakan: menisbatkan tindakan ke tokoh
  bernama yang tidak disebut klip melakukannya = false. Hanya ada di judul video / dikarang = false.
- (penasaran) thumbnail yang hanya nama game/tokoh tanpa KEJADIAN spesifik (mis. "GTA 6 LUCIA TEMBAK") = maks 5
- tepat 0-10: paket membahas MOMEN UTAMA klip (lihat di bawah), bukan ucapan/kejadian sampingan yang cuma lewat?
  Penonton yang mengeklik harus melihat persis yang dijanjikan judul. Membahas hal sampingan = maks 4.

{focus}Isi klip: \"\"\"{text}\"\"\"
Judul video sumber (hanya konteks nama): {video_title}
{streamer_judge}

PAKET:
{pakets}

Balas JSON: {{"nilai": [{{"i": 0, "jelas": 0, "penasaran": 0, "sinkron": 0, "tepat": 0, "jujur": true}}]}}"""

# model terkuat yang tersedia untuk tugas kecil tapi menentukan ini (2 panggilan per klip)
_STRONG = (("DeepSeek", "deepseek-v4-pro"), ("Anthropic", "claude-sonnet-5-5"), ("OpenAI", "gpt-4.1"))
_CLIPPER_CHANNEL = re.compile(r"clip|klip|moments?\b|highlights?|shorts?\b|compilation|kompilasi|best of|"
                              r"reupload|re-upload|fan ?page|\bfans?\b|archive|arsip|vods?\b", re.I)
_ROMAN = {"II": "2", "III": "3", "IV": "4", "V": "5", "VI": "6", "VII": "7", "VIII": "8", "IX": "9", "X": "10"}


def _ask(prompt: str) -> str:
    from . import ai_providers as ai
    for prov, model in _STRONG:
        try:
            # yang saldonya habis / kena limit dilewati: dulu DeepSeek 402 dicoba tiap panggilan lalu jatuh ke cadangan
            # lambat (gemini-…-high ±75 dtk) alih-alih ke provider utama pilihan pengguna
            if ai.is_configured(prov) and model in (ai.provider_models(prov) or []) and not ai.is_cooling(prov, model):
                return ai.complete(prompt, max_tokens=12000, json_mode=True, provider=prov, model=model)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"{prov}/{model} gagal untuk clickbait: {str(exc)[:120]}")
    return ai.complete(prompt, max_tokens=12000, json_mode=True)


def _json(raw: str) -> Dict:
    raw = str(raw or "")
    return json.JSONDecoder().raw_decode(raw[raw.index("{"):])[0]


def _clean_pkg(p: Dict, fixed_title: Optional[str], fixed_hook: Optional[str]) -> Optional[Dict]:
    thumb = re.sub(r"\s+", " ", str(p.get("thumb") or "")).strip().upper()
    title = fixed_title or re.sub(r"\s+", " ", str(p.get("title") or "")).strip()
    hook = fixed_hook or re.sub(r"\s+", " ", str(p.get("hook") or "")).strip()
    if not (2 <= len(thumb.split()) <= 6 and len(thumb) <= 38) or not (10 <= len(title) <= 95):
        return None
    if not hook or len(hook.split()) > 12:
        return None
    return {"thumb": thumb, "title": title, "hook": hook}


def _rank(pkgs: List[Dict], scores: List[Dict], src: str) -> List[Dict]:
    """Paket jujur & sinkron, urut skor. Yang jelas ≥7 didahulukan; sisanya cadangan bila skornya masih layak."""
    from .editor_review import numbers_supported
    good, weak = [], []
    for sc in scores or []:
        try:
            p = pkgs[int(sc.get("i"))]
        except (TypeError, ValueError, IndexError):
            continue
        clear, curious, sync = (float(sc.get(k) or 0) for k in ("jelas", "penasaran", "sinkron"))
        on_point = float(sc.get("tepat") if sc.get("tepat") is not None else 10)  # juri lama tanpa "tepat" = lolos
        # tepat < 6 = judul membahas hal sampingan, bukan momen utama klip → penonton merasa tertipu
        if not sc.get("jujur", False) or sync < 7 or on_point < 6 \
                or not all(numbers_supported(p[k], src) for k in ("thumb", "title", "hook")):
            continue
        s = clear * 0.3 + curious * 0.3 + sync * 0.15 + on_point * 0.25
        (good if clear >= 7 else weak).append((s, p))
    good.sort(key=lambda x: -x[0])
    weak.sort(key=lambda x: -x[0])
    return [p for _, p in good] + [p for s, p in weak if s >= 6.0]


def main_moment(clip: Dict) -> str:
    """Momen utama klip menurut editor yang sudah 'menonton': ringkasan Editor-AI, apa yang terlihat di layar (penilaian
    visual), dan alasan ranking. Dulu penulis judul hanya membaca ucapan → di live gaming judul sering memungut kalimat
    sampingan ("judulnya apa, momennya apa")."""
    rv = clip.get("review") or {}
    parts = []
    if rv.get("summary"):
        parts.append(f"inti: {rv['summary']}")
    what = (rv.get("visual") or {}).get("what")
    if what:
        parts.append(f"terlihat di layar: {what}")
    if rv.get("rank_why"):
        parts.append(f"kenapa menarik: {rv['rank_why']}")
    if not parts:
        return ""
    return ("MOMEN UTAMA klip (dari editor yang sudah menonton) — paket WAJIB tentang ini, bukan ucapan sampingan:\n"
            + "\n".join(f"- {p}" for p in parts) + "\n")


def streamer_block(streamer: str) -> str:
    """Nama streamer pemilik siaran untuk judul. Riset outlier GTA 6 (2026-11-26): judul yang menyebut streamer
    ("Tara bilang GTA 5 lewat jauh" 54x, "Windah Review Mandi di GTA 6" 10x) menang di 3 channel berbeda — penonton
    mengenal namanya. Dipakai sebagian paket saja, dan hanya untuk aksi/reaksi streamer itu sendiri."""
    name = re.sub(r"\s+", " ", str(streamer or "")).strip()
    # channel kumpulan klip/re-upload bukan streamernya → "gua" di klip bukan pemilik channel, nama tidak dipakai
    if not name or _CLIPPER_CHANNEL.search(name):
        return ""
    return (f"STREAMER pemilik siaran (\"gua/aku\" di klip = dia): {name}. Penonton mengenal namanya → tulis 2 dari 5 "
            "paket dengan nama panggilannya di JUDUL (boleh dipendekkan ke panggilan umum, mis. \"Windah Basudara\" → "
            "\"Windah\"), HANYA untuk aksi/reaksi yang dia lakukan sendiri di klip. Jangan menisbatkan ucapan/aksi orang "
            "lain (tamu, NPC, tokoh game) kepadanya. Paket lain tanpa nama.\n")


def write(clip: Dict, video_title: str = "", fixed_title: Optional[str] = None, fixed_hook: Optional[str] = None,
          avoid: Optional[List[str]] = None, streamer: str = "") -> List[Dict]:
    """Paket {thumb, title, hook} lolos juri, terbaik dulu. Kosong bila gagal (pemanggil memakai teks lama).
    `streamer`: nama channel sumber (pemilik siaran) — sebagian paket menyebutnya di judul."""
    from . import title_patterns
    text = str(clip.get("transcript") or clip.get("text") or "")[:1500]
    if len(text.split()) < 3:
        return []
    style = "clickbait" if str(getattr(config, "TITLE_STYLE", "normal")).lower() == "clickbait" else "normal"
    locked = ""
    if fixed_title:
        locked += f"JUDUL SUDAH TAYANG & TIDAK BOLEH DIUBAH (semua paket memakai persis ini): «{fixed_title}»\n"
    if fixed_hook:
        locked += f"HOOK SUDAH TERCETAK DI VIDEO & TIDAK BOLEH DIUBAH (semua paket memakai persis ini): «{fixed_hook}»\n"
    avoid_txt = ("Thumbnail klip lain dari video yang sama (JANGAN mirip): " + " · ".join(avoid) + "\n") if avoid else ""
    focus = main_moment(clip)
    who = "" if fixed_title else streamer_block(streamer)  # judul terkunci = nama streamer tak bisa ditambahkan
    patterns = "" if fixed_title else title_patterns.prompt_block(video_title, text)
    who_judge = (f"Streamer pemilik siaran: {streamer} — \"gua/aku\" di klip = dia, jadi judul yang menyebut namanya untuk "
                 "aksi/reaksi di klip itu JUJUR (bukan nama karangan).\n") if who else ""
    try:
        gen = _json(_ask(_WRITER.format(style=_STYLE[style], locked=locked, avoid=avoid_txt, streamer=who,
                                        patterns=patterns, video_title=video_title or "-", text=text, focus=focus)))
        pkgs = [p for p in (_clean_pkg(x, fixed_title, fixed_hook) for x in (gen.get("paket") or []) if isinstance(x, dict)) if p]
        old = _clean_pkg({"thumb": clip.get("thumb_text"), "title": clip.get("ai_title"), "hook": clip.get("hook_text")},
                         fixed_title, fixed_hook)
        if old and old not in pkgs:
            pkgs.append(old)  # paket lama ikut dinilai — hanya dipakai bila memang menang
        if not pkgs:
            return []
        listing = "\n".join(f"{i}. THUMBNAIL: {p['thumb']} | JUDUL: {p['title']} | HOOK: {p['hook']}" for i, p in enumerate(pkgs))
        judge = _json(_ask(_JUDGE.format(text=text, video_title=video_title or "-", pakets=listing, focus=focus,
                                         streamer_judge=who_judge)))
        src = f"{text} {video_title} " + " ".join(_ROMAN[w] for w in re.findall(r"\b[IVX]+\b", video_title or "") if w in _ROMAN)
        ranked = _rank(pkgs, judge.get("nilai") or [], src)
        logger.info(f"Paket judul/thumbnail/hook: {ranked[0] if ranked else None} (lolos {len(ranked)}/{len(pkgs)})")
        return ranked
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Penulis paket gagal (pakai teks Editor-AI): {str(exc)[:160]}")
        return []


def apply(clips: List[Dict], video_title: str = "", prerender: bool = False, streamer: str = "") -> int:
    """Tulis paket untuk tiap klip (4 sekaligus) & terapkan: thumb_text, ai_title, dan hook_text (hanya sebelum render —
    sesudah render hook sudah tercetak, jadi dikunci). Judul dikunci bila klip punya `_fixed_title` (sudah tayang).
    Teks thumbnail tiap klip dijaga berbeda. Return jumlah klip yang mendapat paket baru."""
    from concurrent.futures import ThreadPoolExecutor

    def one(c: Dict) -> List[Dict]:
        return write(c, video_title, fixed_title=c.get("_fixed_title"), streamer=streamer,
                     fixed_hook=None if prerender else (str(c.get("hook_text") or "").strip() or None))

    with ThreadPoolExecutor(max_workers=4) as ex:
        results = list(ex.map(one, clips))
    used: List[str] = []
    changed = 0
    for c, ranked in zip(clips, results):
        pkg = next((p for p in ranked if not _similar(p["thumb"], used)), None)
        if pkg:
            c["copy_editor"] = {k: c.get(k) for k in ("thumb_text", "ai_title", "hook_text")}
            c["thumb_text"], c["ai_title"] = pkg["thumb"], pkg["title"]
            if prerender:
                c["hook_text"] = pkg["hook"]
            c["copy_package"] = True
            changed += 1
        used.append(str(c.get("thumb_text") or ""))
    return changed


def _similar(text: str, others: List[str]) -> bool:
    """Mirip teks lain (≥60% kata sama) → dianggap duplikat."""
    w = set(re.findall(r"\w+", text.lower()))
    for o in others:
        ow = set(re.findall(r"\w+", str(o).lower()))
        if w and ow and len(w & ow) / max(1, min(len(w), len(ow))) >= 0.6:
            return True
    return False
