#!/usr/bin/env python3
"""Test untuk core/campaign.py (port MotionKlip).

Jalankan:  .venv\\Scripts\\python.exe tests\\test_campaign.py
Render penuh (butuh ffmpeg + aset):  CAMPAIGN_RENDER_TEST=1 .venv\\Scripts\\python.exe tests\\test_campaign.py
"""
from __future__ import annotations

import json
import math
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import campaign  # noqa: E402

failures = []


def check(name, cond, detail=""):
    if cond:
        print(f"PASS {name}")
    else:
        print(f"FAIL {name}  -> {detail}")
        failures.append(name)


PRESET = ROOT / "presets" / "motion-klip-gtavi-split.json"

# ---------- preset ----------
preset = campaign.load_preset(PRESET)
check("preset loads", preset["layout"] == "split")
check("preset assets resolve inside repo",
      "presets" in str(PRESET) or "assets" in Path(preset["watermark"]).as_posix() and Path(preset["watermark"]).is_file(),
      preset["watermark"])
check("preset keeps schema version", preset.get("schema_version") == 1)
check("preset exposes geometry", preset["facecam_height"] == 730 and preset["fps"] == 60)

# missing asset must fail before render
with tempfile.TemporaryDirectory() as td:
    bad = Path(td) / "bad.json"
    bad.write_text(json.dumps({"layout": "full", "watermark": "nope.png", "outro": "nope.mov"}),
                   encoding="utf-8")
    try:
        campaign.load_preset(bad)
        check("missing asset raises", False, "no error")
    except campaign.CampaignError:
        check("missing asset raises", True)

    future = Path(td) / "future.json"
    future.write_text(json.dumps({"schema_version": 99, "watermark": "a", "outro": "b"}),
                      encoding="utf-8")
    try:
        campaign.load_preset(future)
        check("future schema rejected", False, "no error")
    except campaign.CampaignError:
        check("future schema rejected", True)

# ---------- geometry ----------
r = campaign.campaign_rectangle([0.1, 0.2, 0.3, 0.4], "r")
check("rectangle parses list", r == (0.1, 0.2, 0.3, 0.4))
check("rectangle parses csv", campaign.campaign_rectangle("0.1,0.2,0.3,0.4", "r") == r)
for bad_value in ([0.9, 0, 0.2, 0.2], [0, 0, 0, 0.2], "a,b,c,d"):
    try:
        campaign.campaign_rectangle(bad_value, "bad")
        check(f"rectangle rejects {bad_value}", False)
    except campaign.CampaignError:
        check(f"rectangle rejects {bad_value}", True)

crop = campaign.campaign_crop([0.775, 0.703, 0.225, 0.297], (1920, 1080), "facecam")
check("crop is chroma-aligned", all(int(v) % 2 == 0 for v in crop.replace("crop=", "").split(":")),
      crop)
w, h = crop.replace("crop=", "").split(":")[:2]
check("crop size matches the preset fractions", (int(w), int(h)) == (432, 322), crop)

fit = campaign.campaign_fit(730, fill=True)
check("fill fit crops to 1080", "crop=1080:730" in fit and "increase" in fit, fit)
pad = campaign.campaign_fit(1920)
check("pad fit letterboxes", "pad=1080:1920" in pad and "decrease" in pad, pad)

# ---------- settings precedence ----------
s = campaign.campaign_settings(preset)
check("settings default from preset", s["layout"] == "split" and s["subtitle_provider"] == "elevenlabs")
s2 = campaign.campaign_settings(preset, {"layout": "full"})
check("override forces full", s2["layout"] == "full" and s2["facecam_rect"] is None)
try:
    campaign.campaign_settings(preset, {"layout": "full", "facecam_rect": [0, 0, 0.5, 0.5]})
    check("rects with full layout rejected", False)
except campaign.CampaignError:
    check("rects with full layout rejected", True)

# ---------- captions ----------
with tempfile.TemporaryDirectory() as td:
    ass = Path(td) / "c.ass"
    segments = [{
        "start": 0.0, "end": 1.4, "text": "halo dunia",
        "words": [{"word": "halo", "start": 0.10, "end": 0.50},
                  {"word": "dunia", "start": 0.60, "end": 1.00}],
    }]
    n = campaign.write_campaign_captions(segments, 0, 2.0, ass, title="tes judul", intro_seconds=0)
    text = ass.read_text(encoding="utf-8")
    check("campaign ass has events", n >= 3, n)
    check("ass has title style and event", "CampaignTitle" in text and "TES JUDUL" in text)
    check("ass highlights the active word", r"{\1c&H00FFFF&}halo" in text, text[-300:])
    check("ass uses 1080x1920 PlayRes", "PlayResX: 1080" in text and "PlayResY: 1920" in text)

    empty = Path(td) / "e.ass"
    campaign.write_campaign_captions([], 0, 1.0, empty, title="")
    check("empty transcript still writes a valid header", "[Events]" in empty.read_text(encoding="utf-8"))

# ---------- optional real render ----------
if os.getenv("CAMPAIGN_RENDER_TEST") == "1":
    import subprocess

    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "source.mp4"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30",
                        "-f", "lavfi", "-i", "sine=frequency=440", "-t", "6",
                        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "32",
                        "-c:a", "aac", str(src)], check=True)
        out = campaign.render_campaign(src, PRESET, start=1, end=4, output_dir=Path(td) / "out",
                                       overrides={"subtitle_provider": "none", "intro_voice": "none"})
        clip = Path(out["output"])
        check("render produced a file", clip.is_file() and clip.stat().st_size > 100_000,
              clip)
        info = campaign.ffx.probe(str(clip))
        check("render is 1080x1920", (info["width"], info["height"]) == (1080, 1920), info)
        check("render is 60 fps", abs(info["fps"] - 60) < 0.1, info["fps"])
else:
    print("SKIP render test (set CAMPAIGN_RENDER_TEST=1)")

print()
print("CAMPAIGN_RESULT " + ("FAIL" if failures else "PASS"))
if failures:
    print("FAILURES: " + ", ".join(failures))
    sys.exit(1)
print("ALL CAMPAIGN CHECKS PASSED")
