#!/usr/bin/env python3
"""Test untuk core/chat_spikes.py (port dari Clipper).

Jalankan:  .venv\\Scripts\\python.exe tests\\test_chat_spikes.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.chat_spikes import (  # noqa: E402
    BIN_SECONDS, SpikeDetector, chat_spike_moments, merge_hints,
)

failures = []


def check(name, cond, detail=""):
    if cond:
        print(f"PASS {name}")
    else:
        print(f"FAIL {name}  -> {detail}")
        failures.append(name)


def feed(detector: SpikeDetector, start: float, rate: float, seconds: float, prefix: str) -> float:
    """Kirim `rate` pesan/detik mulai `start` selama `seconds`.

    `advance()` dipanggil tiap pesan — sama seperti pemanggil sungguhan yang
    melakukan polling, dan seperti adapter batch `chat_spike_moments`.
    """
    t = start
    n = 0
    got = []
    while t < start + seconds:
        n += 1
        detector.add_message(f"{prefix}-{n}", t, t)
        got.extend(detector.advance(t))
        t += 1.0 / rate
    return t, got


# ---------- 1. baseline tenang ----------
d = SpikeDetector(0.0)
_, spikes = feed(d, 0.0, 1.0, 60.0, "calm")
check("chat tenang tidak menghasilkan spike", spikes == [], spikes)

# ---------- 2. burst jelas ----------
d = SpikeDetector(0.0)
_, _ = feed(d, 0.0, 1.0, 60.0, "calm")          # baseline 1 pesan/detik
end, spikes = feed(d, 60.0, 12.0, 4.0, "burst")  # 48 pesan dalam 4 detik
spikes += d.finish(end + 16)  # bin butuh LATENESS 8s + 1 bin sebelum dihitung
check("burst terdeteksi", len(spikes) >= 1, spikes)
if spikes:
    s = spikes[0]
    check("lift burst > 3x", s.lift > 3, s.lift)
    check("waktu spike masuk akal", 55 <= s.timestamp <= 70, s.timestamp)
    check("count mencatat pesan burst", s.count >= 10, s.count)

# ---------- 3. warmup ----------
d = SpikeDetector(0.0)
_, spikes = feed(d, 0.0, 20.0, 3.0, "early")   # burst tepat di awal, sebelum baseline matang
check("burst saat warmup tidak langsung memicu", len(spikes) == 0, spikes)

# ---------- 4. cooldown ----------
d = SpikeDetector(0.0)
_, _ = feed(d, 0.0, 1.0, 60.0, "calm")
_, first = feed(d, 60.0, 15.0, 3.0, "b1")
first += d.finish(80.0)
_, second = feed(d, 70.0, 15.0, 3.0, "b2")   # 7,5 dtk setelah spike pertama (< cooldown 30 dtk)
second += d.finish(92.0)
check("spike pertama terdeteksi", len(first) >= 1, first)
check("spike beruntun ditekan cooldown", len(second) <= 1, second)

# ---------- 5. finish mengosongkan sisa ----------
d = SpikeDetector(0.0)
_, _ = feed(d, 0.0, 1.0, 60.0, "calm")
_, before = feed(d, 60.0, 15.0, 3.0, "last")
rest = before + d.finish(85.0)
check("finish mengembalikan spike yang tertahan", len(rest) >= 1, rest)

# ---------- 6. adapter batch ----------
events = []
for i in range(60):
    events.append({"id": f"c{i}", "ts": float(i), "text": "wkwk"})
for i in range(48):
    events.append({"id": f"b{i}", "ts": 60.0 + i * 0.08, "text": "anjir"})
hints = chat_spike_moments(events, 0.0, 100.0)
check("chat_spike_moments mengembalikan hint", len(hints) >= 1, hints)
if hints:
    h = hints[0]
    check("hint punya t relatif terhadap wall_from", 0 <= h["t"] <= 100, h["t"])
    check("hint punya strength & kind", h.get("kind") == "chat" and float(h.get("strength") or 0) > 0, h)

# ---------- 7. merge_hints ----------
primary = [{"t": 10.0, "kind": "chat", "strength": 2.0}]
extra = [{"t": 11.0, "kind": "chat", "strength": 9.0}, {"t": 40.0, "kind": "chat", "strength": 5.0}]
merged = merge_hints(primary, extra)
check("merge_hints tidak menduplikasi dalam 12 dtk", len(merged) == 2, merged)
check("merge_hints mempertahankan hint utama", merged[0]["strength"] == 2.0, merged)

print()
print("CHAT_SPIKES_RESULT " + ("FAIL" if failures else "PASS"))
if failures:
    print("FAILURES: " + ", ".join(failures))
    sys.exit(1)
print("ALL CHAT SPIKE CHECKS PASSED")
