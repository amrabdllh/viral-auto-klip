"""Uji penilaian yang lebih "manusia" (core/editor_review) tanpa jaringan:
- rubrik live gaming: reaksi & kejadian paling menentukan (dulu bobot podcast: payoff/standalone)
- ranking pembanding: semua kandidat diurutkan sekaligus → tier S/A/B/C, skor dari posisi (klip terbaik ≥80)
- ranking tidak lengkap / gagal → skor rubrik dipakai apa adanya
- saringan mode Otomatis memakai tier (S/A/B lolos, C hanya mode farming)

Jalankan: python tests/test_editor_ranking.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from core import analyzer, editor_review as er  # noqa: E402

fails = []


def check(name, cond):
    print(("OK   " if cond else "GAGAL ") + name)
    if not cond:
        fails.append(name)


# --- rubrik per jenis konten
reaksi = {"standalone": 5, "hook": 8, "payoff": 7, "emotion": 9, "clarity": 6}  # momen reaksi heboh, tanpa "cerita"
check("gaming: momen reaksi heboh dinilai lebih tinggi dari rubrik podcast",
      er.rubric_score(reaksi, gaming=True) > er.rubric_score(reaksi, gaming=False))
check("gaming: semua 8/10 → 80", round(er.rubric_score({k: 8 for k in reaksi}, gaming=True)) == 80)
analyzer.CONTENT_TYPE = "gaming_stream"
p = er._prompt({"start": 10.0, "end": 40.0, "text": "x"}, [{"start": 12, "end": 14, "text": "ANJIR terbang"}], "GTA")
check("prompt gaming: reaksi & kejadian, tanpa 'jangan murah hati 4-6'", "REAKSI" in p and "KEJADIAN" in p and "4-6" not in p)
analyzer.CONTENT_TYPE = ""
p = er._prompt({"start": 10.0, "end": 40.0, "text": "x"}, [{"start": 12, "end": 14, "text": "jadi intinya"}], "Podcast")
check("prompt podcast tetap rubrik cerita + skala penonton", "kesimpulan/punchline" in p and "9-10 = pasti" in p)


# --- ranking pembanding
def clips(n=6):
    return [{"start": float(i * 100), "end": float(i * 100 + 40), "score": 58.0, "hook_text": f"hook {i}",
             "transcript": f"isi klip {i}", "review": {"status": "reviewed", "summary": f"klip {i}"}} for i in range(n)]


def fake_llm(answer):
    calls = []

    def call(prompt, **kw):
        calls.append(prompt)
        return json.dumps(answer)
    analyzer._call_selected_llm = call
    return calls


cs = clips()
calls = fake_llm({"ranking": [{"id": 4, "tier": "S", "why": "mobil terbang, Windah teriak"}, {"id": 1, "tier": "S"},
                              {"id": 6, "tier": "A"}, {"id": 2, "tier": "B"}, {"id": 3, "tier": "B"},
                              {"id": 5, "tier": "C", "why": "cuma jalan"}]})
check("ranking berhasil", er.rank_clips(cs, "GTA VI") is True)
check("satu panggilan berisi semua kandidat", len(calls) == 1 and all(f"#{i}" in calls[0] for i in range(1, 7)))
by_start = {c["start"]: c for c in cs}
top, last = by_start[300.0], by_start[400.0]
check("klip terbaik ≥80", top["score"] >= 80 and top["review"]["tier"] == "S" and top["review"]["rank"] == 1)
check("alasan editor masuk ke klip", "mobil terbang" in top["reason"])
check("urutan skor = urutan ranking",
      [c["review"]["rank"] for c in sorted(cs, key=lambda c: -c["score"])] == [1, 2, 3, 4, 5, 6])
check("tier C < 60", last["score"] < 60 and last["review"]["tier"] == "C")

# kasus nyata: skor lama berbeda bisa membalik urutan di batas tier (A bawah vs B atas)
cs = clips(4)
for c, old in zip(cs, (60.0, 52.5, 55.8, 60.0)):
    c["score"] = old
fake_llm({"ranking": [{"id": 1, "tier": "A"}, {"id": 2, "tier": "A"}, {"id": 3, "tier": "B"}, {"id": 4, "tier": "B"}]})
er.rank_clips(cs)
check("urutan skor selalu = urutan ranking walau skor lama berbeda",
      [c["review"]["rank"] for c in sorted(cs, key=lambda c: -c["score"])] == [1, 2, 3, 4])

cs = clips()
fake_llm({"ranking": [{"id": 1, "tier": "S"}, {"id": 2, "tier": "Z"}]})
check("ranking tidak lengkap → skor rubrik tetap", er.rank_clips(cs) is False and all(c["score"] == 58.0 for c in cs))

# --- saringan mode Otomatis
cs = clips()
fake_llm({"ranking": [{"id": i, "tier": t} for i, t in zip(range(1, 7), "SABBCC")]})
er.rank_clips(cs)
config.AUTO_MIN_CLIPS = 1
check("saringan normal: S/A/B lolos, C tidak", len(analyzer._auto_quality_gate(cs)) == 4)
check("saringan farming: C ikut", len(analyzer._auto_quality_gate(cs, farm=True)) == 6)

print("\nSemua uji ranking editor lulus" if not fails else f"\nGAGAL: {len(fails)} uji")
sys.exit(1 if fails else 0)
