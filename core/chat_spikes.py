#!/usr/bin/env python3
"""Deteksi lonjakan chat dengan bin waktu + rolling median + sinyal isi pesan.

Latar belakang: di channel besar (mis. Windah Basudara) chat sudah ramai sejak
awal, sehingga **laju pesan** saturasi — momen paling heboh hanya menaikkan
100 → 140 pesan/5 dtk (lift 1,4), di bawah ambang 3×. Yang berubah saat momen
viral bukan volumenya, tapi **komposisinya**: penonton mengetik hal yang sama
("WKWK", "ANJIR"), emoji membanjir, dan hype rate melonjak.

Karena itu skor sekarang menggabungkan:
    z_rate   — lonjakan jumlah pesan (tetap berguna di chat sepi)
    z_rep    — pangsa pesan yang teksnya berulang >= 3x dalam satu bin
    z_hype   — pangsa pesan yang cocok pola hype (wkwk, anjir, gila, emoji)
    z_emoji  — pangsa pesan yang isinya hanya emoji/simbol
    reaction — gabungan berbobot, dipakai sebagai `strength` di hint

Ambang: lonjakan laju SENDIRIAN tetap lolos (perilaku lama), tetapi momen
dengan isi yang memusat juga lolos walau lajunya tidak naik — itulah yang
dulu terlewat di chat cepat.
"""
from __future__ import annotations

import math
import re
from collections import Counter, OrderedDict, deque
from dataclasses import dataclass
from statistics import median
from typing import Dict, Iterable, List, Optional, Tuple

BIN_SECONDS = 5
LATENESS_SECONDS = 8
BASELINE_BINS = 12
WARMUP_BINS = 4
MERGE_BINS = 2
COOLDOWN_SECONDS = 30
MAX_RECENT_IDS = 50_000
MAX_TEXTS_PER_BIN = 800

# Ambang sinyal isi. Dipakai hanya sebagai jalur alternatif: lonjakan laju
# yang sah tetap terdeteksi lewat min_lift.
CONTENT_MIN_Z = 3.0       # z-score repetisi/hype minimal
MIN_REPETITION = 0.25     # minimal 25% pesan dalam bin harus berulang
MIN_CONTENT_MESSAGES = 12 # jangan memicu dari 3 pesan yang kebetulan sama

_HYPE_RE = re.compile(
    r"(wk(wk)+|kwk|haha|hehe|wkwk|awok|ngakak|kocak|lucu|anj(ir|ay|g)?|njir|jir\b|gila|gokil|parah|mantap|"
    r"gg\b|ggwp|wtf|lol|lmao|omg|asu|bjir|buset|anjrit|keren|epic|clip|klip|😂|🤣|🔥|😱|💀|😭|!!+|\?\?+)",
    re.IGNORECASE,
)
_EMOJI_ONLY = re.compile(r"^[\W_]+$", re.UNICODE)
_WORD_STRIP = re.compile(r"^[^0-9a-zA-Z]+|[^0-9a-zA-Z]+$")


@dataclass(frozen=True)
class ChatSpike:
    timestamp: float  # UTC epoch seconds, midpoint of the strongest bin
    count: int
    baseline: float  # median count per bin, preceding this bin
    lift: float
    repetition: float = 0.0
    hype: float = 0.0
    emoji: float = 0.0
    reaction: float = 0.0
    reason: str = "rate"


def _normalize(text: str) -> str:
    t = (text or "").strip().lower()
    return _WORD_STRIP.sub("", t)[:40]


def _content_scores(texts: List[str]) -> Tuple[float, float, float]:
    """Return (repetition, hype_rate, emoji_rate) untuk satu bin."""
    if not texts:
        return 0.0, 0.0, 0.0
    norms = [n for n in (_normalize(t) for t in texts) if n]
    pool = norms or texts
    counts = Counter(pool)
    repeated = sum(c for c in counts.values() if c >= 3)
    repetition = repeated / max(1, len(pool))
    hype = sum(1 for t in texts if _HYPE_RE.search(t)) / len(texts)
    emoji = sum(1 for t in texts if t and _EMOJI_ONLY.match(t)) / len(texts)
    return repetition, hype, emoji


def _mad(values: List[float], centre: float) -> float:
    if not values:
        return 0.0
    return median([abs(v - centre) for v in values])


class SpikeDetector:
    """Feed message IDs, UTC timestamps, and text; call advance even when quiet.

    Callers serialize access. A gap restarts the baseline with the first fresh
    message. A small lookahead lets neighboring peaks share one video window.
    """

    def __init__(self, now: float, *, min_messages: int = 8, min_lift: float = 3.0):
        if min_messages < 1 or not math.isfinite(min_lift) or min_lift <= 1:
            raise ValueError("chat spikes require a positive message count and a lift greater than 1")
        self.min_messages = min_messages
        self.min_lift = min_lift
        self.next_bin: int | None = math.floor(now / BIN_SECONDS)
        self.started_at = now
        self.counts: Counter[int] = Counter()
        self.texts: Dict[int, List[str]] = {}
        self.recent_ids: OrderedDict[str, None] = OrderedDict()
        # (count, repetition, hype, emoji)
        self.baseline: deque[Tuple[float, float, float, float]] = deque(maxlen=BASELINE_BINS)
        self.last_count = 0
        self.merge_until = 0
        self.peak: ChatSpike | None = None
        self.last_spike = -math.inf

    def gap(self, now: float) -> None:
        self.started_at = now
        self.next_bin = None
        self.counts.clear()
        self.texts.clear()
        self.baseline.clear()
        self.last_count = 0
        self.peak = None

    def add_message(self, message_id: str, timestamp: float, now: float, text: str = "") -> None:
        # Accept messages posted just before the detector started: network latency and
        # clock granularity put the post's publishedAt a few hundred ms behind started_at.
        # Anything older than one bin is history the caller did not ask to observe.
        if (not message_id or not math.isfinite(timestamp) or timestamp < self.started_at - BIN_SECONDS
                or timestamp > now + 2 or timestamp < now - BIN_SECONDS - LATENESS_SECONDS):
            return
        if message_id in self.recent_ids:
            return
        self.recent_ids[message_id] = None
        if len(self.recent_ids) > MAX_RECENT_IDS:
            self.recent_ids.popitem(last=False)
        if self.next_bin is None:
            self.next_bin = math.floor(timestamp / BIN_SECONDS)
        bucket = math.floor(timestamp / BIN_SECONDS)
        if bucket >= self.next_bin:
            self.counts[bucket] += 1
            bucket_texts = self.texts.setdefault(bucket, [])
            if len(bucket_texts) < MAX_TEXTS_PER_BIN:
                bucket_texts.append(text or "")

    @property
    def baseline_count(self) -> float:
        return float(median(b[0] for b in self.baseline)) if self.baseline else 0.0

    def _baseline_field(self, index: int) -> float:
        return float(median(b[index] for b in self.baseline)) if self.baseline else 0.0

    def _z(self, value: float, centre: float, spread: float) -> float:
        return (value - centre) / (1.4826 * spread + 1.0)

    @staticmethod
    def _z_content(value: float, centre: float, spread: float) -> float:
        """Skala 0..1 (repetisi/hype/emoji) butuh penyebut jauh lebih kecil.

        Kalau memakai +1 seperti laju pesan, kenaikan 0 → 0,5 hanya menghasilkan
        z 0,5 sehingga momen spam tidak pernah lolos. Lantai 0,02 setara
        "setengah pesan per bin" — cukup untuk mendeteksi perubahan nyata tanpa
        meledak saat baseline benar-benar datar.
        """
        return (value - centre) / max(1.4826 * spread, 0.02)

    def advance(self, now: float) -> List[ChatSpike]:
        spikes = []
        last_complete = math.floor((now - LATENESS_SECONDS) / BIN_SECONDS) - 1
        if self.next_bin is None:
            return spikes
        if last_complete - self.next_bin > 2 * BASELINE_BINS:
            # A suspended process cannot observe a healthy baseline while asleep.
            self.gap(now)
            return spikes
        while self.next_bin <= last_complete:
            bucket = self.next_bin
            self.next_bin += 1
            if self.peak is not None and bucket >= self.merge_until:
                spikes.append(self.peak)
                self.last_spike = self.peak.timestamp
                self.peak = None

            count = self.counts.pop(bucket, 0)
            bin_texts = self.texts.pop(bucket, [])
            self.last_count = count
            base_count = self.baseline_count
            base_rep = self._baseline_field(1)
            base_hype = self._baseline_field(2)
            base_emoji = self._baseline_field(3)
            rep, hype, emoji = _content_scores(bin_texts)
            lift = count / max(base_count, 1.0)
            timestamp = (bucket + 0.5) * BIN_SECONDS

            spreads = [
                _mad([b[0] for b in self.baseline], base_count),
                _mad([b[1] for b in self.baseline], base_rep),
                _mad([b[2] for b in self.baseline], base_hype),
                _mad([b[3] for b in self.baseline], base_emoji),
            ]
            z_rate = self._z(count, base_count, spreads[0])
            z_rep = self._z_content(rep, base_rep, spreads[1])
            z_hype = self._z_content(hype, base_hype, spreads[2])
            z_emoji = self._z_content(emoji, base_emoji, spreads[3])
            content_z = max(z_rep, z_hype)
            reaction = (0.35 * z_rate + 0.30 * z_rep + 0.20 * z_hype
                        + 0.10 * z_emoji + 15.0 * rep)

            warmed = len(self.baseline) >= WARMUP_BINS
            rate_hit = warmed and count >= self.min_messages and lift >= self.min_lift
            content_hit = (warmed and count >= MIN_CONTENT_MESSAGES
                           and content_z >= CONTENT_MIN_Z and rep >= MIN_REPETITION)
            triggered = (rate_hit or content_hit) and timestamp - self.last_spike >= COOLDOWN_SECONDS
            if triggered:
                candidate = ChatSpike(
                    timestamp, count, base_count, lift,
                    repetition=round(rep, 3), hype=round(hype, 3), emoji=round(emoji, 3),
                    reaction=round(reaction, 2),
                    reason="rate" if rate_hit and not content_hit else ("content" if content_hit and not rate_hit else "both"),
                )
                if self.peak is None:
                    self.peak = candidate
                    self.merge_until = bucket + MERGE_BINS
                elif candidate.count > self.peak.count or candidate.reaction > self.peak.reaction:
                    self.peak = candidate
            self.baseline.append((count, rep, hype, emoji))
        return spikes

    def finish(self, now: float) -> List[ChatSpike]:
        """Emit the strongest remaining complete bin when the source closes."""
        spikes = self.advance(now)
        if self.peak is not None:
            spikes.append(self.peak)
            self.last_spike = self.peak.timestamp
            self.peak = None
        return spikes


# ==================== Batch adapter ====================
def chat_spike_moments(events: Iterable[Dict], wall_from: float, wall_to: float,
                       lag: float = 0.0, limit: int = 12) -> List[Dict]:
    """Jalankan SpikeDetector atas rekaman chat.

    `events` berisi dict dengan kunci `id`, `ts` (UTC epoch detik kedatangan),
    dan opsional `text`, `type`. `lag` adalah jeda penonton: momen video =
    ts - lag, jadi detektor diberi waktu yang sudah dikoreksi.

    Return: list dict dengan bentuk sama seperti `chat_signals` supaya bisa
    digabung langsung ke hint yang sudah ada. `strength` sekarang = reaction
    (skor gabungan), dengan `lift` dan komponen isi ikut disertakan.
    """
    rows = []
    for index, event in enumerate(events):
        try:
            ts = float(event["ts"]) - lag
        except (KeyError, TypeError, ValueError):
            continue
        if not (wall_from <= ts <= wall_to):
            continue
        rows.append((ts, str(event.get("id") or f"evt-{index}"), event))
    if not rows:
        return []
    rows.sort(key=lambda item: item[0])

    detector = SpikeDetector(rows[0][0])
    found: List[ChatSpike] = []
    for ts, message_id, event in rows:
        found.extend(detector.advance(ts))
        detector.add_message(message_id, ts, ts, str(event.get("text") or ""))
    found.extend(detector.finish(wall_to))

    out: List[Dict] = []
    for spike in found:
        t = spike.timestamp - wall_from
        if not (0 <= t <= wall_to - wall_from):
            continue
        # Sampel teks di sekitar puncak (±7.5 dtk) supaya hint bisa dijelaskan ke LLM/UI.
        window = [(ts, ev) for ts, _mid, ev in rows if abs(ts - spike.timestamp) <= 7.5]
        counts: Dict[str, int] = {}
        for _ts, event in window:
            text = str(event.get("text") or "").strip()
            if not text:
                continue
            key = text.lower()[:40]
            counts[key] = counts.get(key, 0) + 1
        samples = [k for k, _n in sorted(counts.items(), key=lambda kv: -kv[1])[:4]]
        superchat = [str(ev.get("text") or "") for _ts, ev in window if ev.get("type") == "paid"][:2]
        out.append({
            "t": round(t, 1),
            "kind": "chat",
            "detector": "spike",
            "strength": max(spike.reaction, spike.lift),
            "lift": round(float(spike.lift), 2),
            "reaction": spike.reaction,
            "repetition": spike.repetition,
            "hype": spike.hype,
            "emoji": spike.emoji,
            "reason": spike.reason,
            "count": int(spike.count),
            "samples": samples,
            "superchat": superchat,
        })
        if len(out) >= limit:
            break
    return out


def merge_hints(primary: List[Dict], extra: List[Dict], *, min_gap: float = 12.0) -> List[Dict]:
    """Gabung dua daftar hint tanpa duplikat dalam `min_gap` detik.

    Hint yang sudah ada di `primary` menang; `extra` hanya menambah momen baru.
    """
    merged = list(primary)
    for item in sorted(extra, key=lambda h: h.get("t", 0)):
        if all(abs(item.get("t", 0) - other.get("t", 0)) >= min_gap for other in merged):
            merged.append(item)
    return merged


__all__ = ["ChatSpike", "SpikeDetector", "chat_spike_moments", "merge_hints",
           "BIN_SECONDS", "COOLDOWN_SECONDS", "CONTENT_MIN_Z", "MIN_REPETITION"]
