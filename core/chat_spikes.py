#!/usr/bin/env python3
"""Deteksi lonjakan chat dengan bin waktu-kejadian + rolling median.

Dipindahkan dari Clipper (`chat_spikes.py`). Dipakai sebagai detektor yang
lebih kuat daripada rasio baseline datar di `core/live.py`.

Dua cara pakai:
    - Streaming (live): `SpikeDetector` — `add_message()` tiap pesan,
      `advance(now)` tiap polling (juga saat chat sepi), `finish()` saat tutup.
    - Batch (setelah rekaman): `chat_spike_moments(events, ...)` mengembalikan
      dict dengan bentuk sama seperti `core.live.chat_signals`.
"""
from __future__ import annotations

import math
from collections import Counter, OrderedDict, deque
from dataclasses import dataclass
from statistics import median
from typing import Dict, Iterable, List

BIN_SECONDS = 5
LATENESS_SECONDS = 8
BASELINE_BINS = 12
WARMUP_BINS = 4
MERGE_BINS = 2
COOLDOWN_SECONDS = 30
MAX_RECENT_IDS = 50_000


@dataclass(frozen=True)
class ChatSpike:
    timestamp: float  # UTC epoch seconds, midpoint of the strongest bin
    count: int
    baseline: float  # median count per bin, preceding this bin
    lift: float


class SpikeDetector:
    """Feed message IDs and UTC timestamps; call advance even when chat is quiet.

    Callers serialize access. A gap restarts the baseline with the first fresh
    message. A small lookahead lets neighboring peaks share one video window.
    """

    def __init__(self, now: float, *, min_messages: int = 8, min_lift: float = 3.0):
        if min_messages < 1 or not math.isfinite(min_lift) or min_lift <= 1:
            raise ValueError("chat spikes require a positive message count and a lift greater than 1")
        self.min_messages = min_messages
        self.min_lift = min_lift
        # The bin in progress at detector start is floor(now/BIN). Counting messages in
        # it (not ceil, which would drop the first partial bin's messages) is correct
        # because advance() holds the bin open until LATENESS_SECONDS after it closes.
        self.next_bin: int | None = math.floor(now / BIN_SECONDS)
        self.started_at = now
        self.counts: Counter[int] = Counter()
        self.recent_ids: OrderedDict[str, None] = OrderedDict()
        self.baseline: deque[int] = deque(maxlen=BASELINE_BINS)
        self.last_count = 0
        self.merge_until = 0
        self.peak: ChatSpike | None = None
        self.last_spike = -math.inf

    def gap(self, now: float) -> None:
        self.started_at = now
        self.next_bin = None
        self.counts.clear()
        self.baseline.clear()
        self.last_count = 0
        self.peak = None

    def add_message(self, message_id: str, timestamp: float, now: float) -> None:
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

    @property
    def baseline_count(self) -> float:
        return float(median(self.baseline)) if self.baseline else 0.0

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
            self.last_count = count
            baseline = self.baseline_count
            lift = count / max(baseline, 1.0)
            timestamp = (bucket + 0.5) * BIN_SECONDS
            if (len(self.baseline) >= WARMUP_BINS and count >= self.min_messages
                    and lift >= self.min_lift and timestamp - self.last_spike >= COOLDOWN_SECONDS):
                if self.peak is None:
                    self.peak = ChatSpike(timestamp, count, baseline, lift)
                    self.merge_until = bucket + MERGE_BINS
                elif count > self.peak.count:
                    self.peak = ChatSpike(timestamp, count, baseline, lift)
            self.baseline.append(count)
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
    digabung langsung ke hint yang sudah ada.
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
    for ts, message_id, _event in rows:
        found.extend(detector.advance(ts))
        detector.add_message(message_id, ts, ts)
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
            "kind": "chat",           # bentuk sama dengan chat_signals
            "detector": "spike",
            "strength": round(float(spike.lift), 2),
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
           "BIN_SECONDS", "COOLDOWN_SECONDS"]
