#!/usr/bin/env python3
"""Live spike clipping: deteksi lonjakan chat → ambil window TEPAT dari buffer HLS.

Berbeda dari `live_clip` (vak) yang memotong per jendela 5/10/15 menit memakai
`build_window` (-c copy, terbatas di batas segmen 60 detik), job ini:

    1. menyimpan segmen HLS ber-timestamp di `LiveMediaBuffer`
    2. mendeteksi lonjakan chat dengan rolling median (`core.chat_spikes`)
    3. mengambil [momen-25s, momen+20s] secara presisi (`buffer.capture`)
    4. mengirim hasilnya ke antrean sebagai job render biasa

Parameter job:
    url                 link siaran (YouTube)
    present             detik sebelum momen (default 25)
    post_sec            detik sesudah momen (default 20)
    reaction_delay      koreksi jeda penonton (default 3)
    min_lift            rasio lonjakan minimum (default 3.0)
    max_spikes          0 = tanpa batas
    poll_interval       detik antar-poll (default 4)
    campaign            True = render lewat campaign_render (butuh preset)
    preset              nama preset campaign
    use_chat            False = hanya rekam buffer, tanpa chat (jarang berguna)
"""
from __future__ import annotations

import math
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from loguru import logger

import config
from core import db, live
from core.chat_spikes import chat_spike_moments
from core.jobs import JobContext, handler, submit
from core.live_media import LiveMediaBuffer, MediaError, WindowNotReady, WindowUnavailable

MAX_OUTSTANDING = 4          # spike yang menunggu/menjalankan render sekaligus
FRESH_SEC = 30.0             # spike lebih tua dari ini diabaikan (buffer sudah lewat)
SEEN_GAP = 30.0              # dua spike dalam jarak ini dianggap satu momen
CAPTURE_DEADLINE = 90.0      # tunggu segmen yang dibutuhkan muncul


def _ydl_opts(p: Dict) -> Dict:
    opts = {"quiet": True, "no_warnings": True, "skip_download": True,
            "noplaylist": True, "socket_timeout": 8}
    if p.get("cookies_browser"):
        opts["cookiesfrombrowser"] = (str(p["cookies_browser"]),)
    if p.get("cookiefile"):
        opts["cookiefile"] = str(p["cookiefile"])
    return opts


@handler("live_spike", background=True)
def live_spike(ctx: JobContext) -> Dict:
    import yt_dlp

    p = ctx.params
    url = str(p.get("url") or "").strip()
    if not url:
        raise ValueError("Masukkan link siaran live YouTube")

    pre = float(p.get("pre_sec", 25))
    post = float(p.get("post_sec", 20))
    reaction = float(p.get("reaction_delay", 3))
    min_lift = float(p.get("min_lift", 3.0))
    max_spikes = int(p.get("max_spikes", 0))
    poll_interval = max(1.0, float(p.get("poll_interval", 4)))
    if not all(math.isfinite(v) and v >= 0 for v in (pre, post, reaction)):
        raise ValueError("pre_sec, post_sec, reaction_delay harus >= 0")
    if pre + post < 5:
        raise ValueError("pre_sec + post_sec minimal 5 detik")

    ctx.progress(2, "Cek siaran", "Membaca info siaran…")
    info = live.probe(url)
    if info.get("live_status") != "is_live":
        raise RuntimeError("Siaran tidak sedang live. Untuk replay, pakai Analisis AI.")

    chat_dir = ctx.dir / "chat"
    seg_dir = ctx.dir / "segments"
    clips_dir = ctx.dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)

    ctx.log(f"Live spike: buffer {pre:.0f}s sebelum + {post:.0f}s sesudah momen")
    opts = _ydl_opts(p)
    accepted = 0
    started = time.time()
    children: List[str] = []
    seen_ts: List[float] = []

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        if not info or not info.get("is_live"):
            raise ValueError("Sumber tidak sedang live")
        buffer = LiveMediaBuffer(ydl, info, seg_dir, max_height=int(p.get("max_height", 1080)))
        chat = live.ChatReader(url, chat_dir, log=ctx.log).start() if p.get("use_chat", True) else None
        try:
            while True:
                ctx.check_cancel()
                now = time.time()
                if ctx.id in live.STOP_REQUESTS:
                    break
                if p.get("max_hours") and now - started > float(p["max_hours"]) * 3600:
                    ctx.log("Batas max_hours tercapai — berhenti")
                    break

                # ---- deteksi spike dari chat 90 detik terakhir
                new_spikes: List[Dict] = []
                if chat is not None:
                    events = chat.between(now - 90, now)
                    hints = chat_spike_moments(events, now - 90, now)
                    for h in hints:
                        abs_t = now - 90 + float(h.get("t") or 0)
                        if float(h.get("strength") or 0) < min_lift:
                            continue
                        if now - abs_t > FRESH_SEC:
                            continue
                        if any(abs(abs_t - prev) < SEEN_GAP for prev in seen_ts):
                            continue
                        seen_ts.append(abs_t)
                        new_spikes.append({"t": abs_t, "hint": h})

                # ---- ambil window presisi untuk spike baru
                for spike in new_spikes:
                    if max_spikes and accepted >= max_spikes:
                        break
                    if len(children) - _finished(children) >= MAX_OUTSTANDING:
                        ctx.log("Antrean spike penuh — melewati satu momen")
                        continue
                    accepted += 1
                    moment = spike["t"] - reaction
                    start, end = moment - pre, moment + post
                    out = clips_dir / f"spike_{accepted:03d}_{int(moment)}.mp4"
                    ctx.progress(min(90, 10 + accepted * 5), "Ambil momen",
                                 f"Spike #{accepted}: {int(spike['hint'].get('count') or 0)} pesan, "
                                 f"lift {spike['hint'].get('strength')}×")
                    try:
                        _capture_when_ready(buffer, start, end, out, deadline=CAPTURE_DEADLINE)
                    except (WindowNotReady, WindowUnavailable) as exc:
                        ctx.log(f"Spike #{accepted} dilewati: {exc}")
                        continue
                    child = _submit_render(ctx, out, p, accepted)
                    children.append(child)
                    ctx.log(f"Spike #{accepted} → job {child}")

                # ---- poll buffer; pin rentang yang masih ditunggu
                pins = [(s["t"] - reaction - pre, s["t"] - reaction + post) for s in new_spikes]
                try:
                    ended = buffer.poll(pins)
                except MediaError as exc:
                    ctx.log(f"Poll HLS gagal: {exc}")
                    ended = False
                if ended and (not chat or not chat.events):
                    break
                if max_spikes and accepted >= max_spikes and not new_spikes:
                    break
                time.sleep(poll_interval)
        finally:
            if chat is not None:
                chat.stop()
            live.STOP_REQUESTS.discard(ctx.id)

    return {
        "_message": f"{accepted} spike ditangkap, {len(children)} render dikirim",
        "spikes": accepted,
        "jobs": children,
        "total_clips": 0,
    }


def _finished(children: List[str]) -> int:
    done = 0
    for job_id in children:
        job = db.get_job(job_id)
        if job and job.get("status") in ("done", "error", "cancelled", "interrupted"):
            done += 1
    return done


def _capture_when_ready(buffer: LiveMediaBuffer, start: float, end: float,
                        out: Path, *, deadline: float) -> Path:
    """Tunggu sampai buffer mencakup [start, end], lalu capture sekali."""
    limit = time.monotonic() + deadline
    while True:
        cov = buffer.coverage()
        if cov and cov[0] <= start + 0.5 and cov[1] >= end - 0.5:
            buffer.capture(start, end, out)
            return Path(out)
        if time.monotonic() > limit:
            raise WindowNotReady(f"segmen {start:.0f}-{end:.0f} tidak tersedia dalam {deadline:.0f}s")
        time.sleep(1.0)
        try:
            buffer.poll([(start, end)])
        except MediaError:
            pass


def _submit_render(ctx: JobContext, window: Path, p: Dict, index: int) -> str:
    """Kirim window ke antrean: campaign atau klip biasa."""
    base = {"path": str(window), "parent": ctx.id, "source_url": p.get("url"), "channel": p.get("channel")}
    if p.get("campaign"):
        params = {**base,
                  "start": 0, "end": None,
                  "preset": p.get("preset") or "motion-klip-gtavi-split.json",
                  "title": "", "subtitle_provider": p.get("subtitle_provider"),
                  "intro_voice": p.get("intro_voice")}
        params = {k: v for k, v in params.items() if v is not None}
        return submit("campaign_render", params, title=f"Spike {index} · campaign")["id"]
    params = {**base, "num_clips": int(p.get("clips_per_spike", 1)),
              "min_score": p.get("min_score", 40)}
    return submit("clip_quick", params, title=f"Spike {index} · klip")["id"]


__all__ = ["live_spike", "MAX_OUTSTANDING"]
