"""Pola judul yang TERBUKTI meledak per niche, dari riset outlier Shorts (views ÷ median channel sendiri).

Dipakai penulis paket judul (core/clickbait) sebagai PILIHAN, bukan paksaan: hanya untuk 1–2 dari 5 paket, dan hanya
bila momen klip memang cocok. Juri tetap menolak paket yang mengarang. Pola punya tanggal riset & masa berlaku —
pola niche cepat basi (detail GTA 6 yang "baru ketemu" habis dibahas dalam beberapa minggu), jadi yang kedaluwarsa
tidak dipakai lagi sampai riset diulang.

Sumber riset 2026-11-26: 24 channel ± 700 Shorts + 252 Shorts hashtag #gta6/#gta6indonesia/#gtavi (video-agent/swipe.md).
"""
from __future__ import annotations

import re
from datetime import date
from typing import Dict, List, Optional

NICHES: List[Dict] = [
    {
        "key": "gta6",
        "label": "GTA 6",
        "match": r"\bgta\s*(?:6|vi)\b|grand theft auto\s*(?:6|vi)\b",
        "researched": "2026-11-26",
        "valid_days": 60,
        "patterns": [
            {"name": "Ternyata bisa",
             "when": "klip menunjukkan detail/mekanik kecil yang SPESIFIK dan baru ketemu",
             "shape": "Ternyata {objek} di GTA 6 bisa {aksi}",
             "proof": "Tegar 58x, VINZEROX 16x, Fadhel Maula 13x",
             "avoid": "'detail' generik tanpa objek jelas (kalah jauh)"},
            {"name": "GTA 6 vs GTA 5",
             "when": "streamer membandingkan dengan GTA 5 atau kagum karena jauh lebih detail",
             "shape": "{aksi} di GTA 6, GTA 5 lewat jauh",
             "proof": "Tegar 54x & 9,5x, Fadhel Maula 6x",
             "avoid": "menyebut GTA 5 bila klip tidak membandingkan"},
            {"name": "NPC aneh / bug",
             "when": "NPC, polisi, atau hewan berkelakuan absurd, atau ada bug lucu",
             "shape": "{NPC} GTA 6 {kelakuan aneh}",
             "proof": "VINZEROX 10x, 9x, 8x; dvklip 3,5x",
             "avoid": "menyebut bug bila itu mekanik normal"},
            {"name": "Ini beneran game?",
             "when": "streamer kagum pada grafis/realisme yang terlihat seperti video asli",
             "shape": "Kalian nggak bakal percaya ini GTA 6",
             "proof": "NeoRift 687x (2,2 juta views) — baru 1 bukti",
             "avoid": "dipakai untuk momen yang bukan soal grafis"},
        ],
    },
]


def niche_for(*texts: str) -> Optional[Dict]:
    """Niche yang cocok dengan judul video sumber / isi klip; None bila tidak ada."""
    blob = " ".join(t for t in texts if t)
    for n in NICHES:
        if re.search(n["match"], blob, re.I):
            return n
    return None


def fresh(niche: Dict, today: Optional[date] = None) -> bool:
    """Riset masih berlaku (pola niche cepat basi)."""
    age = ((today or date.today()) - date.fromisoformat(niche["researched"])).days
    return 0 <= age <= int(niche.get("valid_days", 60))


def prompt_block(*texts: str, today: Optional[date] = None) -> str:
    """Blok prompt pola terbukti untuk niche klip ini. Kosong bila niche tidak dikenal atau risetnya kedaluwarsa."""
    n = niche_for(*texts)
    if not n or not fresh(n, today):
        return ""
    lines = [f"- {p['name']}: bila {p['when']} → bentuk «{p['shape']}» (bukti: {p['proof']}; hindari: {p['avoid']})"
             for p in n["patterns"]]
    return (f"POLA JUDUL YANG TERBUKTI MELEDAK di niche {n['label']} (riset outlier {n['researched']}) — PILIHAN, bukan "
            "keharusan: pakai untuk 1–2 paket SAJA dan HANYA bila momen klip benar-benar cocok; isi {…} dengan kejadian "
            "nyata dari klip, jangan disalin mentah. Paket lain tetap bebas.\n" + "\n".join(lines) + "\n")
