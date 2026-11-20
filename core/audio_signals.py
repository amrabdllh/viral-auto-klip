"""Sinyal dari SUARA (bukan teks) untuk memilih & mengedit momen — "telinga" untuk AI.

Transkrip tidak memuat cara orang berbicara: nada yang naik, kata yang ditekan keras, jeda dramatis, tawa. Editor
manusia memilih momen justru dari situ. Modul ini mengukurnya langsung dari audio (CPU, numpy, ±10–20 dtk untuk
video 40 menit):

- energi (dB) & nada dasar F0 (autokorelasi ternormalisasi) tiap 20 ms;
- dinormalisasi PER PEMBICARA (label speaker Scribe): "keras" = relatif terhadap suara normal orang itu sendiri;
- tiap kata diberi `loud` (dB di atas median pembicara), `pitch` (semiton di atas median), `stress` 0–3;
- event dari ElevenLabs Scribe: tawa/tepuk tangan (positif) dan efek suara editorial sumber (bunyi ketik/transisi
  bab = batas topik video asli → klip yang melintasinya cenderung lompat topik);
- `segment_profile(start, end)`: ekspresivitas (persentil terhadap seluruh video), variasi intonasi, kata yang
  ditekan, tawa, jeda dramatis, + ringkasan teks untuk Moment Finder & Editor-AI.

Pemakaian: `prof = analyze(video, transcript)` (kata di transkrip dianotasi di tempat), lalu
`prof.segment_profile(s, e)` / `prof.hint_lines(lo, hi)`.
"""
import re
import subprocess
from typing import Dict, List, Optional

import numpy as np
from loguru import logger

SR = 16000
HOP = 320          # 20 ms
WIN = 640          # 40 ms (≥ 2 periode pada 70 Hz)
F0_MIN, F0_MAX = 70.0, 400.0
_LAUGH = re.compile(r"laugh|tawa|ketawa|ngakak|chuckl|giggl|applause|tepuk|sorak|cheer", re.I)
# kata fungsi: wajar keras di awal frasa, bukan "penekanan" yang berarti
_STOP = set("""aku kamu kau lu lo gue gua gw saya dia kita kami mereka itu ini yang dan atau kalau kalo tapi tetapi
terus trus lalu jadi jd nah ya yah iya sih tuh deh dong kan kok loh lho eh ee eeh eeeh em emm hmm ah oh udah sudah
ada di ke dari untuk buat sama dengan karena soalnya gitu gini begitu begini aja saja juga lagi masih mau bisa
kek kayak kaya kayaknya akan apa gimana emang memang tentu yaitu adalah nih tadi pun lah kah dah toh
the a an and or but so to of in is it i you we he she they that this like""".split())
_EDITORIAL = re.compile(r"keyboard|ketik|efek|sound effect|whoosh|transisi|music|musik|jingle|intro", re.I)


def _decode(video: str) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", video, "-vn", "-ac", "1", "-ar", str(SR),
                          "-f", "s16le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0


def _frames_features(x: np.ndarray):
    """(db, f0, voiced) per frame 20 ms. F0 via autokorelasi ternormalisasi (FFT) — cepat & cukup untuk ucapan."""
    n = max(0, (len(x) - WIN) // HOP + 1)
    db = np.full(n, -90.0, np.float32)
    f0 = np.zeros(n, np.float32)
    conf = np.zeros(n, np.float32)
    win = np.hanning(WIN).astype(np.float32)
    lag_lo, lag_hi = int(SR / F0_MAX), int(SR / F0_MIN)
    step = 8192
    for i0 in range(0, n, step):
        i1 = min(n, i0 + step)
        idx = (np.arange(i0, i1) * HOP)[:, None] + np.arange(WIN)[None, :]
        fr = x[idx]
        fr = fr - fr.mean(1, keepdims=True)
        rms = np.sqrt((fr ** 2).mean(1) + 1e-10)
        db[i0:i1] = 20 * np.log10(rms + 1e-9)
        spec = np.fft.rfft(fr * win, n=1024)
        ac = np.fft.irfft(np.abs(spec) ** 2, n=1024)[:, :lag_hi + 2]
        ac = ac / (ac[:, :1] + 1e-9)
        seg = ac[:, lag_lo:lag_hi + 1]
        k = seg.argmax(1)
        conf[i0:i1] = seg[np.arange(len(k)), k]
        # interpolasi parabola untuk lag sub-sampel
        kk = np.clip(k + lag_lo, 1, ac.shape[1] - 2)
        a, b, c = ac[np.arange(len(k)), kk - 1], ac[np.arange(len(k)), kk], ac[np.arange(len(k)), kk + 1]
        den = a - 2 * b + c
        with np.errstate(divide="ignore", invalid="ignore"):
            off = np.where(np.abs(den) > 1e-9, 0.5 * (a - c) / np.where(den == 0, 1, den), 0.0)
        f0[i0:i1] = SR / np.maximum(kk + np.clip(off, -0.5, 0.5), 1)
    floor = float(np.percentile(db, 10))
    voiced = (conf > 0.45) & (db > max(floor + 12, -55))
    return db, f0, voiced


def _semi(f: np.ndarray) -> np.ndarray:
    return 12 * np.log2(np.maximum(f, 1.0) / 100.0)


class AudioProfile:
    def __init__(self, db, f0, voiced, transcript: List[Dict]):
        self.db, self.f0, self.voiced = db, f0, voiced
        self.st = _semi(f0)
        # hilangkan loncatan oktaf sesaat (median 5 frame pada frame bersuara)
        st = self.st.copy()
        vi = np.where(voiced)[0]
        if len(vi) > 5:
            from numpy.lib.stride_tricks import sliding_window_view
            pad = np.pad(st[vi], 2, mode="edge")
            st[vi] = np.median(sliding_window_view(pad, 5), axis=1)
        self.st = st
        self.words: List[Dict] = [w for s in transcript for w in (s.get("words") or []) if str(w.get("word", "")).strip()]
        self.events = [ev for s in transcript for ev in (s.get("audio_events") or [])]
        self.laughs = sorted(float(e["start"]) for e in self.events if _LAUGH.search(str(e.get("text", ""))))
        self.editorial = sorted((float(e["start"]), float(e.get("end", e["start"]))) for e in self.events
                                if _EDITORIAL.search(str(e.get("text", ""))) and not _LAUGH.search(str(e.get("text", ""))))
        self._annotate_words()
        self._baseline()

    # ------------------------------------------------------------------ per kata
    def _fr(self, t0: float, t1: float) -> slice:
        return slice(max(0, int(t0 * SR / HOP)), min(len(self.db), int(np.ceil(t1 * SR / HOP)) + 1))

    def _annotate_words(self) -> None:
        by_spk: Dict[str, Dict[str, list]] = {}
        stats = []
        for w in self.words:
            sl = self._fr(float(w["start"]), float(w["end"]))
            d, v, s = self.db[sl], self.voiced[sl], self.st[sl]
            if not len(d):
                stats.append(None)
                continue
            loud = float(np.percentile(d, 90))
            pit = float(np.percentile(s[v], 80)) if int(v.sum()) >= 2 else None
            stats.append((loud, pit))
            spk = str(w.get("speaker") or "_")
            b = by_spk.setdefault(spk, {"db": [], "st": []})
            b["db"].append(loud)
            if pit is not None:
                b["st"].append(pit)
        base = {k: (float(np.median(v["db"])) if v["db"] else -30.0,
                    float(np.median(v["st"])) if v["st"] else 0.0,
                    float(np.std(v["st"])) if len(v["st"]) > 10 else 3.0) for k, v in by_spk.items()}
        self.speaker_base = base
        # penonjolan LOKAL: kata dibanding kata-kata pembicara yang sama ±4 dtk (penekanan = menonjol dari sekitarnya)
        times = np.array([float(w["start"]) for w in self.words])
        loud_a = np.array([st[0] if st else np.nan for st in stats])
        pit_a = np.array([st[1] if st and st[1] is not None else np.nan for st in stats])
        spk_a = np.array([str(w.get("speaker") or "_") for w in self.words])
        lo_i, hi_i = np.searchsorted(times, times - 4.0), np.searchsorted(times, times + 4.0)
        prev_end = None
        for i, (w, st) in enumerate(zip(self.words, stats)):
            if st is None:
                continue
            mdb, mst, _ = base[str(w.get("speaker") or "_")]
            loud = st[0] - mdb
            pit = (st[1] - mst) if st[1] is not None else 0.0
            nb = slice(lo_i[i], hi_i[i])
            same = spk_a[nb] == spk_a[i]
            ctx_l, ctx_p = loud_a[nb][same], pit_a[nb][same]
            ctx_p = ctx_p[~np.isnan(ctx_p)]
            p_loud = st[0] - float(np.median(ctx_l)) if len(ctx_l) >= 4 else loud
            p_pit = (st[1] - float(np.median(ctx_p))) if (st[1] is not None and len(ctx_p) >= 4) else pit
            clean = re.sub(r"[^\w]", "", str(w["word"]).lower())
            stress = (1 if p_loud >= 3 else 0) + (1 if p_loud >= 6 else 0) + (1 if p_pit >= 2.5 else 0) + (1 if p_pit >= 5 else 0)
            if loud < -3:  # menonjol dari sekitar tapi tetap pelan secara umum → bukan penekanan
                stress = min(stress, 1)
            if clean in _STOP or len(clean) <= 2:
                stress = min(stress, 1)
            w["prom"] = round(p_loud / 6 + p_pit / 4, 2)
            pause = float(w["start"]) - prev_end if prev_end is not None else 0.0
            w["loud"], w["pitch"] = round(loud, 1), round(pit, 1)
            w["stress"] = int(min(3, stress))
            if pause >= 0.6 and stress >= 1:
                w["pause_before"] = round(pause, 2)  # jeda lalu kata ditekan: pola punchline
            prev_end = float(w["end"])

    # ------------------------------------------------------------------ ekspresivitas segmen
    def _raw_arousal(self, start: float, end: float) -> Optional[Dict]:
        ws = [w for w in self.words if start <= float(w["start"]) < end and "loud" in w]
        if len(ws) < 4:
            return None
        loud = np.array([w["loud"] for w in ws])
        pit = np.array([w["pitch"] for w in ws])
        a = 0.5 * np.clip(loud / 6, -1, 2) + 0.5 * np.clip(pit / 4, -1, 2)
        sl = self._fr(start, end)
        v = self.voiced[sl]
        st = self.st[sl][v]
        spk_std = np.median([b[2] for b in self.speaker_base.values()]) or 3.0
        var = float(np.std(st)) / spk_std if len(st) > 20 else 1.0
        stressed = float(np.mean([w["stress"] >= 2 for w in ws]))
        dur = max(1e-3, end - start)
        return {"arousal": float(a.mean()) + 0.6 * stressed + 0.25 * (var - 1), "var": var, "stressed": stressed,
                "rate": len(ws) / dur, "words": ws}

    def _baseline(self) -> None:
        """Distribusi ekspresivitas jendela 30 dtk di seluruh video → skor segmen dinyatakan sebagai persentil."""
        if not self.words:
            self.base_arousal = np.array([0.0])
            self.base_rate = 2.5
            return
        t_end = float(self.words[-1]["end"])
        vals, rates = [], []
        for t in np.arange(0, max(1.0, t_end - 30), 10.0):
            r = self._raw_arousal(t, t + 30)
            if r:
                vals.append(r["arousal"])
                rates.append(r["rate"])
        self.base_arousal = np.sort(np.array(vals or [0.0]))
        self.base_rate = float(np.median(rates)) if rates else 2.5

    def segment_profile(self, start: float, end: float) -> Optional[Dict]:
        r = self._raw_arousal(start, end)
        if not r:
            return None
        pct = float(np.searchsorted(self.base_arousal, r["arousal"]) / max(1, len(self.base_arousal)) * 100)
        ws = r["words"]
        top = sorted((w for w in ws if w["stress"] >= 2), key=lambda w: -w.get("prom", 0))[:4]
        laughs = [t for t in self.laughs if start + 0.5 <= t <= end + 1.0]
        punches = [w for w in ws if w.get("pause_before")]
        crosses = [a for a, b in self.editorial if start + 1.5 < a < end - 1.5]
        return {
            "pct": round(pct), "intonation": round(r["var"], 2), "stressed_ratio": round(r["stressed"], 2),
            "rate": round(r["rate"] / max(self.base_rate, 0.1), 2),
            "stressed_words": [(round(float(w["start"]), 1), str(w["word"]).strip(",.?!")) for w in top],
            "laughs": [round(t, 1) for t in laughs], "punch_pauses": len(punches),
            "crosses_editorial": [round(t, 1) for t in crosses],
        }

    # ------------------------------------------------------------------ teks untuk AI
    @staticmethod
    def describe(p: Optional[Dict]) -> str:
        if not p:
            return ""
        lvl = ("sangat ekspresif" if p["pct"] >= 85 else "ekspresif" if p["pct"] >= 65 else
               "biasa" if p["pct"] >= 35 else "datar/monoton")
        parts = [f"cara bicara {lvl} (persentil {p['pct']} dibanding bagian lain video ini)"]
        parts.append("intonasi naik-turun" if p["intonation"] >= 1.15 else "intonasi datar" if p["intonation"] <= 0.8 else "intonasi normal")
        if p["rate"] >= 1.2:
            parts.append("tempo bicara cepat")
        elif p["rate"] <= 0.8:
            parts.append("tempo lambat")
        if p["stressed_words"]:
            parts.append("kata yang ditekan keras: " + ", ".join(f'"{w}" ({t:.0f}s)' for t, w in p["stressed_words"]))
        if p["punch_pauses"]:
            parts.append(f"{p['punch_pauses']}× jeda dramatis sebelum kata yang ditekan")
        if p["laughs"]:
            parts.append("TAWA terdengar di " + ", ".join(f"{t:.0f}s" for t in p["laughs"]))
        if p["crosses_editorial"]:
            parts.append("melintasi efek suara transisi video sumber di " + ", ".join(f"{t:.0f}s" for t in p["crosses_editorial"])
                         + " (kemungkinan pergantian topik/segmen)")
        return "; ".join(parts)

    def peaks(self, lo: float, hi: float, every: float = 150.0, min_pct: float = 85.0) -> List[Dict]:
        """Puncak ekspresi (jendela 12 dtk) + tawa dalam rentang — bahan petunjuk untuk AI Moment Finder."""
        out = [{"t": t, "label": "tawa terdengar"} for t in self.laughs if lo <= t <= hi]
        cands = []
        for t in np.arange(lo, max(lo + 1, hi - 12), 4.0):
            r = self._raw_arousal(t, t + 12)
            if r:
                pct = float(np.searchsorted(self.base_arousal, r["arousal"]) / max(1, len(self.base_arousal)) * 100)
                cands.append((pct, t + 6))
        limit = max(1, int((hi - lo) / every))
        chosen: List[float] = []
        for pct, t in sorted(cands, reverse=True):
            if pct < min_pct or len(chosen) >= limit:
                break
            if all(abs(t - c) > 30 for c in chosen):
                chosen.append(t)
                out.append({"t": t, "label": f"suara meninggi/bersemangat (persentil {pct:.0f})"})
        return sorted(out, key=lambda h: h["t"])


def analyze(video: str, transcript: List[Dict]) -> Optional[AudioProfile]:
    """Hitung profil audio & anotasi kata transkrip di tempat. None bila gagal (pipeline tetap jalan)."""
    import time
    t0 = time.time()
    try:
        x = _decode(video)
        if len(x) < SR * 5:
            return None
        db, f0, voiced = _frames_features(x)
        prof = AudioProfile(db, f0, voiced, transcript)
        logger.info(f"Sinyal audio: {len(prof.words)} kata dianotasi, {len(prof.laughs)} tawa, "
                    f"{len(prof.editorial)} efek transisi sumber ({time.time() - t0:.1f} dtk)")
        return prof
    except Exception as exc:
        logger.warning(f"Sinyal audio dilewati: {str(exc)[:200]}")
        return None
