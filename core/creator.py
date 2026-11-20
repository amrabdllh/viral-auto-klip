"""
Generator video faceless: AI Content Creator, Story Studio, Motion Graphic.

Alur: bahan (topik / link YouTube / artikel / naskah) → LLM menulis storyboard per adegan →
(opsional) pratinjau & edit tiap adegan → render: visual (gambar AI / stok / kartu) + narasi TTS
+ subtitle karaoke + judul + teks kinetik + transisi + musik + logo Brand Kit → MP4.
"""
from __future__ import annotations

import json
import random
import re
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger

import config
from . import ai_providers as ai
from . import db, ffx, media_ai, sources
from .jobs import JobContext, handler

# ----------------------------------------------------------------------------- katalog

CATEGORIES = [
    "Sejarah", "Religi & Islami", "Sains & Teknologi", "Mental Health", "Mitos vs Fakta", "Keuangan & Bisnis",
    "Fakta Unik", "Luar Angkasa", "Cerita Rakyat", "True Crime", "Misteri & Horor", "Review Film", "Berita & Isu",
    "Opini & Debat", "Kesehatan", "Motivasi", "Psikologi", "Teknologi AI", "Olahraga", "Kuliner", "Travel",
    "Edukasi Anak", "Biografi Tokoh", "Geografi & Negara", "Hewan & Alam",
]

TONES = {
    "naratif": "naratif & emosional, bercerita seperti dokumenter",
    "edukatif": "step-by-step edukatif, jelas dan runtut",
    "inspiratif": "inspiratif dan membangkitkan semangat",
    "fakta_viral": "fakta viral cepat, padat, bikin penasaran",
    "profesional": "profesional dan tenang seperti pembawa berita",
    "misteri": "menegangkan & misterius, penuh ketegangan",
    "ceria": "ceria, santai, dan lucu",
    "kritis": "kritis dan tajam, berani mempertanyakan, tetap sopan",
    "savage": "savage & blak-blakan namun TANPA SARA, body-shaming, ujaran kebencian, atau menyerang individu",
}

FOCUS = {"topik": "Fokus pada topik/peristiwa", "tokoh": "Fokus pada satu tokoh utama yang konsisten di tiap adegan"}

ENDINGS = ["kesimpulan kuat", "plot twist", "pertanyaan ke penonton", "cliffhanger part 2", "pesan moral", "call to action"]

VISUAL_STYLES = {
    "sinematik": "cinematic photorealistic film still, dramatic lighting, shallow depth of field, 35mm",
    "3d_render": "high quality 3D render, Pixar style, soft global illumination, vibrant",
    "claymation": "3D claymation, plasticine characters, handmade stop-motion look",
    "anime": "anime illustration, detailed background, Makoto Shinkai style lighting",
    "kartun": "cheerful flat cartoon illustration, bold outlines, bright colors",
    "komik": "comic book panel, halftone shading, bold ink lines, dramatic angles",
    "cat_air": "watercolor painting, soft washes, paper texture",
    "dokumenter": "documentary photograph, natural light, realistic, journalistic",
    "arsip": "vintage archival photograph, sepia, film grain, historical",
    "found_footage": "found footage, grainy night vision camcorder still, eerie",
    "pixel_art": "detailed pixel art, 16-bit retro game style",
    "low_poly": "low poly 3D art, geometric facets, pastel lighting",
    "fantasi": "epic fantasy concept art, volumetric light, highly detailed",
    "stickman": "simple black stickman drawing on white background, minimal doodle",
    "whiteboard": "whiteboard marker doodle illustration, hand drawn, white background",
    "kolase": "paper cut-out collage, layered textured paper, craft style",
    "diorama": "miniature diorama, tilt-shift photography, handcrafted tiny world",
    "noir": "film noir comic, black and white with red accents, heavy shadows",
}

# Story Studio = gaya ilustrasi bercerita; Motion Graphic = preset explainer
STORY_STYLES = ["stickman", "kartun", "whiteboard", "kolase", "cat_air", "anime", "noir", "pixel_art",
                "3d_render", "low_poly", "diorama", "sinematik", "dokumenter", "arsip", "fantasi"]

MOTION_PRESETS = {
    "bedah_3d": {"label": "Bedah 3D", "desc": "Benda dibedah tembus pandang, tiap bagian dijelaskan",
                 "visual": "3D exploded view cutaway diagram, technical illustration, labeled parts, clean studio background",
                 "use": "Cara kerja mesin, kulkas, mobil, organ tubuh"},
    "proses_3d": {"label": "Proses 3D", "desc": "Benda besar bekerja di tempat aslinya, lalu dibelah",
                  "visual": "3D isometric process illustration, cross-section, industrial, soft lighting",
                  "use": "Cara kerja bendungan, pabrik, gunung berapi"},
    "jurnalisme": {"label": "Jurnalisme Visual", "desc": "Latar gelap, angka besar, anotasi garis",
                   "visual": "dark editorial infographic background, bold numbers, thin annotation lines, data journalism",
                   "use": "Berita, isu, fakta, data"},
    "kolase_kartun": {"label": "Kolase Kartun", "desc": "Ilustrasi flat, catatan tulisan tangan",
                      "visual": "flat cartoon collage on beige paper, handwritten notes, doodles",
                      "use": "Sains, edukasi, tips sehari-hari"},
    "sinematik": {"label": "Sinematik", "desc": "Seperti film pendek animasi",
                  "visual": "cinematic animated film still, stylized 3D, dramatic lighting",
                  "use": "Cerita, penjelasan sains, kisah"},
    "geografi": {"label": "Geografi", "desc": "Bumi dilihat dari atas, globe, peta",
                 "visual": "stylized 3D globe and map from above, highlighted country, satellite view, clean",
                 "use": "Fakta negara, geografi, wisata"},
    "finansial": {"label": "Finansial", "desc": "Grafik, uang, chart naik turun",
                  "visual": "clean finance infographic, charts, coins, minimal 3D icons, dark blue background",
                  "use": "Keuangan, investasi, ekonomi"},
}

ASPECTS = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080)}


def catalog() -> Dict:
    return {"categories": CATEGORIES, "tones": TONES, "focus": FOCUS, "endings": ENDINGS,
            "visual_styles": list(VISUAL_STYLES), "story_styles": STORY_STYLES,
            "motion_presets": {k: {kk: v[kk] for kk in ("label", "desc", "use")} for k, v in MOTION_PRESETS.items()},
            "transitions": ffx.TRANSITIONS, "caption_styles": list(config.CAPTION_STYLES)}


# ----------------------------------------------------------------------------- naskah

_SCRIPT_SCHEMA_HINT = """
Balas HANYA JSON dengan bentuk:
{
  "title": "judul video pendek & memancing klik (maks 60 karakter)",
  "hook": "kalimat pembuka 1-2 detik pertama",
  "scenes": [
    {
      "narration": "narasi yang diucapkan di adegan ini (1-3 kalimat)",
      "on_screen_text": "teks singkat di layar (maks 6 kata) atau kosong",
      "image_prompt": "prompt gambar dalam BAHASA INGGRIS, deskriptif: subjek, aksi, latar, komposisi, cahaya",
      "stock_query": "2-4 kata bahasa Inggris untuk mencari footage stok",
      "video_prompt": "BAHASA INGGRIS, untuk AI video: aksi/gerakan yang terjadi di adegan (siapa melakukan apa, ekspresi) + gerak kamera, 1-2 kalimat",
      "motion": "zoom_in | zoom_out | pan_left | pan_right | pan_up | pan_down",
      "sfx": "efek suara singkat bahasa Inggris atau kosong"
    }
  ],
  "captions": {"tiktok": "...", "instagram": "...", "youtube": "..."},
  "hashtags": ["#..."],
  "description": "deskripsi 2-3 kalimat"
}
"""


def _gather_source(params: Dict) -> Dict:
    kind = params.get("source_type", "topik")
    value = (params.get("source") or "").strip()
    if not value:
        raise ValueError("Isi topik / link / naskah dulu")
    if kind == "youtube":
        data = sources.youtube_transcript(value)
        info = data["info"]
        text = data["text"] or info.get("description", "")
        return {"label": f"Video YouTube: {info.get('title')}", "material": f"Judul: {info.get('title')}\n"
                f"Channel: {info.get('channel')}\nDeskripsi: {info.get('description', '')[:1500]}\n\nTranskrip:\n{text[:18000]}"}
    if kind == "artikel":
        if re.match(r"^https?://", value):
            art = sources.fetch_article(value)
            return {"label": f"Artikel: {art['title']}", "material": f"Judul: {art['title']}\n\n{art['text']}"}
        return {"label": "Dokumen", "material": value[:20000]}
    if kind == "naskah":
        return {"label": "Naskah dari pengguna", "material": value[:20000], "verbatim": True}
    return {"label": f"Topik: {value}", "material": value}


def local_storyboard(text: str, params: Dict) -> Dict:
    """Pecah naskah pengguna jadi adegan ±15 kata tanpa LLM (dipakai bila AI tidak tersedia)."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]
    scenes, buf = [], []
    for s in sentences:
        buf.append(s)
        if len(" ".join(buf).split()) >= 14:
            scenes.append(" ".join(buf)); buf = []
    if buf:
        if scenes and len(" ".join(buf).split()) < 6:
            scenes[-1] += " " + " ".join(buf)
        else:
            scenes.append(" ".join(buf))
    motions = ffx.MOTIONS[:-1]
    style = VISUAL_STYLES.get(params.get("visual_style", ""), "")
    out = []
    for i, narration in enumerate(scenes[:40]):
        words = [w for w in re.findall(r"[\w'-]+", narration) if len(w) > 3]
        out.append({"id": db.new_id(), "narration": narration, "on_screen_text": " ".join(words[:3]).upper(),
                    "image_prompt": f"{narration}. {style}".strip(), "stock_query": " ".join(words[:3]),
                    "motion": motions[i % len(motions)], "sfx": ""})
    title = (sentences[0] if sentences else "Video")[:60]
    return {"title": title, "hook": sentences[0] if sentences else "", "scenes": out, "captions": {}, "hashtags": [],
            "description": "", "source_label": "Naskah dari pengguna (dipecah lokal tanpa AI)"}


def generate_script(params: Dict) -> Dict:
    if params.get("source_type") == "naskah":
        try:
            return _generate_script_llm(params)
        except ai.AIError as exc:
            logger.warning(f"LLM tidak tersedia, naskah dipecah lokal: {exc}")
            return local_storyboard(params.get("source", ""), params)
    return _generate_script_llm(params)


def _generate_script_llm(params: Dict) -> Dict:
    """LLM → storyboard. params: source_type, source, category, tone, focus, ending, duration, language,
    visual_style, mode ('creator'|'story'|'motion'), preset, character (deskripsi tokoh), scene_count."""
    src = _gather_source(params)
    mode = params.get("mode", "creator")
    duration = int(params.get("duration") or 60)
    language = params.get("language") or "Bahasa Indonesia"
    words_per_sec = 2.5
    scene_count = int(params.get("scene_count") or max(4, min(24, round(duration / 6))))
    tone = TONES.get(params.get("tone", "naratif"), params.get("tone", ""))
    style_key = params.get("visual_style") or ("sinematik" if mode != "motion" else "")
    visual_desc = VISUAL_STYLES.get(style_key, style_key)
    preset = MOTION_PRESETS.get(params.get("preset", "")) if mode == "motion" else None
    character = (params.get("character") or "").strip()
    focus = params.get("focus", "topik")

    rules = [
        f"Bahasa narasi: {language}. Gaya penyampaian: {tone}.",
        f"Total durasi narasi ±{duration} detik (±{int(duration * words_per_sec)} kata), dibagi menjadi {scene_count} adegan.",
        "Struktur: Hook kuat → pengenalan → konflik/isi utama → klimaks → penutup dengan " + (params.get("ending") or "kesimpulan kuat") + ".",
        "Kalimat pendek, mudah diucapkan TTS, tanpa emoji, tanpa simbol aneh, angka ditulis sebagai kata bila perlu.",
        "Fakta harus akurat; bila tidak yakin, sampaikan sebagai 'menurut cerita' atau 'konon'. Jangan mengarang kutipan tokoh nyata.",
        "Hindari SARA, ujaran kebencian, kekerasan grafis, dan konten seksual.",
        f"Kategori: {params.get('category') or 'umum'}.",
    ]
    if src.get("verbatim"):
        rules.append("Pakai naskah pengguna apa adanya sebagai narasi (boleh dipecah ke adegan dan dirapikan sedikit), JANGAN menulis ulang isinya.")
    if focus == "tokoh":
        rules.append("Fokus pada satu tokoh utama; deskripsikan penampilan tokoh yang SAMA di setiap image_prompt agar konsisten.")
    if character:
        rules.append(f"Tokoh utama: {character}. Sertakan deskripsi visual tokoh ini di setiap image_prompt.")
    if visual_desc:
        rules.append(f"Gaya visual seluruh gambar: {visual_desc}. Tambahkan gaya ini di akhir setiap image_prompt.")
    if preset:
        rules.append(f"Ini video motion graphic explainer gaya '{preset['label']}' ({preset['desc']}). "
                     f"Gaya visual: {preset['visual']}. on_screen_text WAJIB diisi angka/kata kunci besar tiap adegan.")
    if mode == "story":
        rules.append("Ini video bercerita bergambar (story). Tiap adegan satu momen cerita yang jelas untuk diilustrasikan.")
    prompt = (f"Buat storyboard video pendek vertikal untuk media sosial.\n\nBAHAN ({src['label']}):\n{src['material']}\n\n"
              "ATURAN:\n- " + "\n- ".join(rules) + "\n" + _SCRIPT_SCHEMA_HINT)
    raw = ai.complete(prompt, system="Kamu penulis naskah video viral TikTok/Shorts/Reels profesional berbahasa Indonesia.",
                      max_tokens=6000, json_mode=True)
    data = ai.parse_json(raw)
    scenes = [s for s in (data.get("scenes") or []) if str(s.get("narration", "")).strip()]
    if not scenes:
        raise RuntimeError("AI tidak menghasilkan adegan — coba lagi atau ganti provider")
    motions = ffx.MOTIONS[:-1]
    for i, s in enumerate(scenes):
        s["id"] = s.get("id") or db.new_id()
        s["motion"] = s.get("motion") if s.get("motion") in ffx.MOTIONS else motions[i % len(motions)]
        s.setdefault("on_screen_text", "")
        s.setdefault("image_prompt", s["narration"])
        s.setdefault("stock_query", "")
        s.setdefault("sfx", "")
    return {
        "title": data.get("title") or "Video AI", "hook": data.get("hook") or "", "scenes": scenes,
        "captions": data.get("captions") or {}, "hashtags": data.get("hashtags") or [],
        "description": data.get("description") or "", "source_label": src["label"],
    }


def style_prompt(params: Dict, prompt: str) -> str:
    mode = params.get("mode", "creator")
    if mode == "motion":
        preset = MOTION_PRESETS.get(params.get("preset", ""), {})
        suffix = preset.get("visual", "")
    else:
        suffix = VISUAL_STYLES.get(params.get("visual_style", ""), params.get("visual_style", ""))
    char = (params.get("character") or "").strip()
    parts = [prompt.strip()]
    if char and char.lower() not in prompt.lower():
        parts.append(f"Main character: {char}")
    if suffix and suffix.lower() not in prompt.lower():
        parts.append(suffix)
    parts.append("vertical composition, no text, no watermark, no letters")
    return ", ".join(p for p in parts if p)


def preview_scene_image(params: Dict, scene: Dict, folder: Path) -> Dict:
    w, h = ASPECTS.get(params.get("aspect", "9:16"), ASPECTS["9:16"])
    refs = [r for r in (params.get("character_images") or []) if Path(r).exists()]
    out = folder / f"scene_{scene.get('id') or db.new_id()}_{random.randint(1000, 9999)}.jpg"
    kinetic = params.get("kinetic_text", params.get("mode") == "motion")
    card_text = "" if kinetic and scene.get("on_screen_text") else (scene.get("on_screen_text") or scene.get("narration", "")[:80])
    res = media_ai.image(style_prompt(params, scene.get("image_prompt") or scene.get("narration", "")), str(out),
                         provider=params.get("image_provider", ""), w=w, h=h, refs=refs, card_text=card_text)
    return res


# ----------------------------------------------------------------------------- render

def _ass_time(t: float) -> str:
    t = max(0.0, t)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def _ass_esc(text: str) -> str:
    return str(text).replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def build_ass(path: Path, w: int, h: int, scenes: List[Dict], offsets: List[float], title: str,
              caption_style: str, kinetic: bool, show_title: bool, words_per_line: int = 4) -> None:
    style = dict(config.CAPTION_STYLES.get(caption_style, config.CAPTION_STYLES.get("modern", {})))
    from .utils import _ass_colour
    primary = _ass_colour(style.get("color", (255, 255, 255)))
    outline_c = _ass_colour(style.get("stroke_color", (0, 0, 0)))
    active = _ass_colour(style.get("active_color", (255, 214, 0))).replace("&H00", "&H")
    fs = int(h * 0.036)
    margin_v = int(h * 0.22)
    font = "Arial"
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {w}", f"PlayResY: {h}", "WrapStyle: 0", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Cap,{font},{fs},{primary},&H00FFFFFF,{outline_c},&H80000000,-1,0,0,0,100,100,0,0,1,{max(3, fs // 14)},2,2,60,60,{margin_v},1",
        # MarginV 14%: di bawah UI atas TikTok/Reels & di bawah logo Brand Kit pojok atas
        f"Style: Title,{font},{int(h * 0.04)},&H00FFFFFF,&H00FFFFFF,&H00000000,&HB4000000,-1,0,0,0,100,100,0,0,3,18,0,8,70,70,{int(h * 0.14)},1",
        f"Style: Kin,{font},{int(h * 0.062)},&H00FFFFFF,&H00FFFFFF,&H00101010,&H00000000,-1,0,0,0,100,100,1,0,1,{max(4, h // 260)},3,5,60,60,0,1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    upper = bool(style.get("uppercase", True))
    if show_title and title:
        lines.append(f"Dialogue: 2,{_ass_time(0)},{_ass_time(3.6)},Title,,0,0,0,,{{\\fad(200,300)}}{_ass_esc(title)}")
    for scene, off in zip(scenes, offsets):
        words = scene.get("_words") or []
        for i in range(0, len(words), words_per_line):
            chunk = words[i:i + words_per_line]
            c_start = off + chunk[0]["start"]
            c_end = off + (words[i + words_per_line]["start"] if i + words_per_line < len(words) else chunk[-1]["end"] + 0.25)
            for j, wd in enumerate(chunk):
                w_start = off + wd["start"] if j else c_start
                w_end = off + chunk[j + 1]["start"] if j + 1 < len(chunk) else c_end
                if w_end - w_start < 0.03:
                    continue
                toks = []
                for k, other in enumerate(chunk):
                    t = _ass_esc(other["word"])
                    t = t.upper() if upper else t
                    toks.append("{\\c" + active + "&\\t(0,80,\\fscx112\\fscy112)}" + t + "{\\r}" if k == j else t)
                lines.append(f"Dialogue: 0,{_ass_time(w_start)},{_ass_time(w_end)},Cap,,0,0,0,,{' '.join(toks)}")
        text = (scene.get("on_screen_text") or "").strip()
        if kinetic and text:
            dur = scene["_dur"]
            lines.append(f"Dialogue: 1,{_ass_time(off + 0.15)},{_ass_time(off + max(0.8, dur - 0.2))},Kin,,0,0,0,,"
                         f"{{\\an5\\pos({w // 2},{int(h * 0.42)})\\fad(120,180)\\fscx40\\fscy40\\t(0,260,\\fscx108\\fscy108)\\t(260,380,\\fscx100\\fscy100)}}"
                         f"{_ass_esc(text.upper())}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def brand_logo() -> Optional[Dict]:
    kit = db.kv_get("brand_kit", {}) or {}
    if kit.get("logo") and Path(kit["logo"]).exists():
        return kit
    return None


def apply_brand(ctx_path: str, out: str, force: Optional[bool] = None) -> str:
    """Brand Kit: logo/watermark gambar (posisi pilihan) lalu penutup klip (gambar/video pengguna) di akhir.

    `force` = saklar "Brand Kit" di form proses (saklar UTAMA): False = lewati semua untuk proses ini;
    True/None = pasang, tetapi tiap bagian tetap mengikuti saklarnya sendiri di halaman Brand Kit
    ("Pasang logo otomatis" / "Pasang penutup otomatis"). FIX v6.8: dulu True memaksa keduanya terpasang
    sehingga saklar di halaman Brand Kit tidak berpengaruh (saklar form aktif secara default)."""
    kit = db.kv_get("brand_kit", {}) or {}
    if force is False:
        return ctx_path
    want_logo = bool(kit.get("logo") and Path(kit["logo"]).exists()) and bool(kit.get("auto", True))
    want_outro = bool(kit.get("outro") and Path(kit["outro"]).exists()) and bool(kit.get("outro_auto", True))
    if not (want_logo or want_outro):
        return ctx_path
    cur = ctx_path
    if want_logo:
        dst = out if not want_outro else str(Path(out).with_name(Path(out).stem + "_logo.mp4"))
        xy = (kit.get("x"), kit.get("y")) if kit.get("position") == "custom" and kit.get("x") is not None else None
        ffx.apply_logo(cur, dst, kit["logo"], position=kit.get("position", "top-left"), size=float(kit.get("size", 0.22)),
                       opacity=float(kit.get("opacity", 0.9)), custom_xy=xy)
        cur = dst
    if want_outro:
        ffx.append_outro(cur, out, kit["outro"], image_sec=float(kit.get("outro_duration", 3.0)))
        if cur != ctx_path:
            Path(cur).unlink(missing_ok=True)
    return out


def _ai_video_scene(ctx: JobContext, params: Dict, scene: Dict, idx: int, dur: float, w: int, h: int,
                    out: Path, image: Optional[str]) -> Optional[str]:
    """v6.8: hidupkan adegan dengan video AI (Gemini Omni Flash / Kling). None bila gagal → gerak kamera."""
    from . import video_ai
    engine = params.get("video_engine", "camera")
    prompt = scene.get("video_prompt") or scene.get("image_prompt") or scene["narration"]
    if not image:  # teks→video: gaya visual & tokoh ikut di prompt
        prompt = style_prompt(params, prompt)
    raw = ctx.dir / f"aivideo_{idx:02d}.mp4"
    try:
        res = video_ai.generate(prompt, str(raw), engine, image=image, duration=dur + 0.3,
                                aspect="9:16" if h >= w else "16:9")
        video_ai.fit_to_scene(str(raw), str(out), dur, w, h)
        ctx.log(f"Adegan {idx + 1}: video AI {video_ai.PROVIDERS[engine]['label']} "
                f"({'gambar→video' if image else 'teks→video'}, {res['seconds']} dtk)")
        return str(out)
    except Exception as exc:
        ctx.log(f"Adegan {idx + 1}: video AI gagal → gerak kamera ({str(exc)[:160]})")
        return None


def _scene_visual(ctx: JobContext, params: Dict, scene: Dict, idx: int, dur: float, w: int, h: int) -> str:
    visual_mode = params.get("visual_mode", "ai_image")
    out = ctx.dir / f"scene_{idx:02d}.mp4"
    if scene.get("_ai_video") and params.get("video_ai_source", "image") == "text":
        done = _ai_video_scene(ctx, params, scene, idx, dur, w, h, out, image=None)
        if done:
            return done
    use_stock = visual_mode == "stock" or (visual_mode == "mixed" and idx % 3 == 2)
    if use_stock and media_ai.stock_available():
        query = scene.get("stock_query") or scene.get("on_screen_text") or params.get("category", "")
        try:
            hits = media_ai.stock_search(query, kind="video", orientation="portrait" if h > w else "landscape", per_page=4)
            if hits:
                raw = ctx.dir / f"stock_{idx:02d}.mp4"
                media_ai.download(hits[0]["url"], str(raw))
                ffx.video_to_scene(str(raw), str(out), dur, w, h)
                raw.unlink(missing_ok=True)
                ctx.log(f"Adegan {idx + 1}: footage stok {hits[0]['source']}")
                return str(out)
        except Exception as exc:
            ctx.log(f"Adegan {idx + 1}: stok gagal ({exc}) → gambar")
    img = scene.get("image_path")
    if not img or not Path(img).exists():
        provider = "card" if visual_mode == "card" else params.get("image_provider", "")
        kinetic = params.get("kinetic_text", params.get("mode") == "motion")
        card_text = "" if kinetic and scene.get("on_screen_text") else (scene.get("on_screen_text") or scene["narration"][:80])
        res = media_ai.image(style_prompt(params, scene.get("image_prompt") or scene["narration"]),
                             str(ctx.dir / f"scene_{idx:02d}.jpg"), provider=provider, w=w, h=h,
                             refs=[r for r in (params.get("character_images") or []) if Path(r).exists()],
                             card_text=card_text)
        img = res["path"]
        last_real = params.get("_last_real_image")
        if res["provider"] == "card" and visual_mode != "card" and last_real and Path(last_real).exists():
            # generator gambar gagal total → kartu polos terlihat janggal di tengah video bergambar;
            # pakai ulang gambar AI sebelumnya (gerak kamera dibalik agar tetap terasa berbeda)
            img = last_real
            scene["motion"] = {"zoom_in": "zoom_out", "pan_left": "pan_right", "pan_up": "pan_down"}.get(scene.get("motion"), "zoom_in")
            ctx.log(f"Adegan {idx + 1}: generator gambar gagal — memakai ulang gambar sebelumnya")
        else:
            scene["image_path"] = img
            ctx.log(f"Adegan {idx + 1}: gambar {res['provider']}")
            if res["provider"] != "card":
                params["_last_real_image"] = img
    else:
        params["_last_real_image"] = img  # gambar pratinjau / upload pengguna
    if scene.get("_ai_video") and params.get("video_ai_source", "image") != "text" and not str(img).endswith(".svg"):
        done = _ai_video_scene(ctx, params, scene, idx, dur, w, h, out, image=img)
        if done:
            return done
    ffx.image_to_clip(img, str(out), dur, w, h, motion=scene.get("motion", "zoom_in"))
    return str(out)


@handler("creator_render")
def render_job(ctx: JobContext) -> Dict:
    p = ctx.params
    story = p.get("storyboard") or {}
    if not story.get("scenes"):
        ctx.progress(3, "Menulis naskah", "AI menyusun storyboard…")
        story = generate_script(p)
    scenes: List[Dict] = story["scenes"]
    w, h = ASPECTS.get(p.get("aspect", "9:16"), ASPECTS["9:16"])
    n = len(scenes)
    (ctx.dir / "storyboard.json").write_text(json.dumps(story, ensure_ascii=False, indent=2), encoding="utf-8")

    # 1. Narasi TTS per adegan
    voice_files = []
    for i, s in enumerate(scenes):
        ctx.progress(5 + int(30 * i / n), "Narasi", f"TTS adegan {i + 1}/{n}")
        res = media_ai.tts(s["narration"], str(ctx.dir / f"voice_{i:02d}.mp3"), provider=p.get("tts_provider", ""),
                           voice=p.get("voice", ""), speed=float(p.get("speed") or 1.0),
                           instructions=p.get("voice_instructions", ""))
        s["_words"] = res["words"]
        s["_dur"] = res["duration"] + 0.35
        voice_files.append(res["path"])
    ctx.log(f"TTS: {res['provider']} / {res.get('voice') or 'suara default'}")

    # 2. Visual per adegan (v6.8: sebagian adegan dihidupkan video AI bila dipilih)
    engine = p.get("video_engine", "camera")
    if engine != "camera":
        from . import video_ai
        if video_ai.available(engine):
            k = max(0, min(n, int(p.get("video_ai_max") or getattr(config, "VIDEO_AI_MAX_SCENES", 4))))
            picks = set(range(n)) if k >= n else ({0} | {round(j * (n - 1) / max(k - 1, 1)) for j in range(k)} if k else set())
            for i in sorted(picks)[:k]:
                scenes[i]["_ai_video"] = True
            ctx.log(f"Video AI {video_ai.PROVIDERS[engine]['label']}: {len(picks)} dari {n} adegan dihidupkan")
        else:
            ctx.log(f"Video AI dilewati: API key {video_ai.PROVIDERS.get(engine, {}).get('key', engine)} belum diisi → gerak kamera")
    scene_clips = []
    for i, s in enumerate(scenes):
        ctx.progress(35 + int(35 * i / n), "Visual", f"Membuat visual adegan {i + 1}/{n}")
        vis = _scene_visual(ctx, p, s, i, s["_dur"], w, h)
        with_voice = ctx.dir / f"scene_{i:02d}_v.mp4"
        ffx.run(["-i", vis, "-i", voice_files[i], "-filter_complex",
                 f"[1:a]apad=whole_dur={s['_dur']:.3f},aresample=48000[a]", "-map", "0:v", "-map", "[a]",
                 "-c:v", "copy", *ffx.AENC, "-t", f"{s['_dur']:.3f}", str(with_voice)])
        scene_clips.append(str(with_voice))

    # 3. Rakit dengan transisi
    ctx.progress(72, "Merakit", "Menggabungkan adegan + transisi")
    transition = p.get("transition", "fade")
    tdur = 0.35 if transition != "none" else 0.0
    assembled = ctx.dir / "assembled.mp4"
    aspect = p.get("aspect", "9:16")
    ffx.concat(scene_clips, str(assembled), aspect=aspect, transition=transition, tdur=tdur, workdir=ctx.dir)
    offsets, t = [], 0.0
    for s in scenes:
        offsets.append(t)
        t += s["_dur"] - tdur

    # 4. Subtitle karaoke + judul + teks kinetik
    current = str(assembled)
    if p.get("subtitles", True) or p.get("mode") == "motion":
        ctx.progress(80, "Subtitle", "Membakar subtitle & teks layar")
        ass = ctx.dir / "captions.ass"
        build_ass(ass, w, h, scenes if p.get("subtitles", True) else [{**s, "_words": []} for s in scenes], offsets,
                  story.get("title", ""), p.get("caption_style", "modern"),
                  kinetic=p.get("kinetic_text", p.get("mode") == "motion"), show_title=p.get("show_title", True))
        subbed = ctx.dir / "subbed.mp4"
        ffx.burn_ass(current, str(ass), str(subbed))
        current = str(subbed)

    # 5. Musik latar
    music = p.get("music_path") or ""
    if not music and p.get("music_prompt") and media_ai._key("ELEVENLABS_API_KEY"):
        try:
            ctx.progress(86, "Musik", "Membuat musik latar AI")
            music = media_ai.music(p["music_prompt"], str(ctx.dir / "music.mp3"), duration=min(t + 2, 180))
        except Exception as exc:
            ctx.log(f"Musik AI gagal: {exc}")
            music = ""
    if music and Path(music).exists():
        ctx.progress(88, "Musik", "Mixing musik + auto-duck")
        mixed = ctx.dir / "mixed.mp4"
        ffx.mux_voice_music(current, str(mixed), music=music, music_vol=float(p.get("music_volume") or 0.12),
                            keep_original=True)
        current = str(mixed)

    # 6. Brand kit & finalisasi
    ctx.progress(93, "Finalisasi", "Logo & ekspor")
    safe = re.sub(r"[^\w\- ]", "", story.get("title", "video"))[:50].strip().replace(" ", "_") or "video"
    final = ctx.dir / f"{safe}.mp4"
    branded = apply_brand(current, str(ctx.dir / "branded.mp4"), force=p.get("brand_kit"))
    Path(branded).replace(final)
    thumb = ctx.dir / "thumbnail.jpg"
    try:
        ffx.extract_frame(str(final), min(1.2, t / 3), str(thumb))
    except Exception:
        thumb = None
    for f in ctx.dir.glob("*.mp4"):
        if f != final and (f.name.startswith(("scene_", "_norm", "assembled", "subbed", "mixed", "branded"))):
            f.unlink(missing_ok=True)
    narration = "\n\n".join(s["narration"] for s in scenes)
    (ctx.dir / "narasi.txt").write_text(narration, encoding="utf-8")
    caps = story.get("captions") or {}
    tags = " ".join(story.get("hashtags") or [])
    (ctx.dir / "caption.txt").write_text(
        f"JUDUL: {story.get('title', '')}\n\nDESKRIPSI: {story.get('description', '')}\n\n" +
        "\n\n".join(f"[{k.upper()}]\n{v}\n{tags}" for k, v in caps.items()), encoding="utf-8")
    for s in scenes:
        s.pop("_words", None)
    dur = ffx.probe(str(final))["duration"]
    asset = ctx.asset("video", final, name=story.get("title", final.name),
                      meta={"source": p.get("mode", "creator"), "duration": dur, "thumbnail": str(thumb) if thumb else "",
                            "title": story.get("title"), "description": story.get("description"),
                            "captions": caps, "hashtags": story.get("hashtags")})
    return {"_message": f"Video jadi ({dur:.0f} dtk)", "file": str(final), "thumbnail": str(thumb) if thumb else "",
            "asset_id": asset["id"], "duration": dur, "storyboard": story, "narration": narration}
