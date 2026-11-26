"""Uji tata letak manual (subtitle & facecam) tanpa render video.

- nilai form dibersihkan & dibatasi; toggle mati = otomatis
- letak facecam manual (pojok) menggantikan deteksi; tinggi panel menghitung ulang kotak agar wajah tetap di tengah
- facecam bawah diabaikan untuk template campaign (banner di bawah)
- burn ulang subtitle: posisi baru tersimpan, zona geser yang bertabrakan dibuang

Jalankan: python tests/test_manual_layout.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import clipper, subtitle_live, tools  # noqa: E402

fails = []


def check(name, cond):
    print(("OK   " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)


# ---------------------------------------------------------------- pembersihan nilai form
m = clipper.clean_manual_layout({"subtitle_y": 0.6, "cam_ratio": "45", "cam_position": "bottom",
                                 "cam_corner": "TL", "cam_size": 30})
check("subtitle_y pecahan dipakai", m["subtitle_y"] == 0.6)
check("cam_ratio persen → pecahan", abs(m["cam_ratio"] - 0.45) < 1e-9)
check("posisi bawah → cam_top False", m["cam_top"] is False)
check("pojok tidak peka huruf besar", m["cam_corner"] == "tl")
check("lebar facecam persen → pecahan", abs(m["cam_size"] - 0.30) < 1e-9)
check("nilai dibatasi", clipper.clean_manual_layout({"subtitle_y": 0.99, "cam_ratio": 0.9})
      == {"subtitle_y": 0.92, "cam_ratio": 0.6})
check("kosong / auto / sampah = otomatis",
      clipper.clean_manual_layout({"subtitle_y": "", "cam_ratio": "auto", "cam_corner": "tengah"}) == {})
check("posisi atas tidak ditulis", "cam_top" not in clipper.clean_manual_layout({"cam_position": "top"}))

# ---------------------------------------------------------------- toggle form
base = {"subtitle_y": 0.5, "cam_ratio": 0.45, "cam_position": "bottom", "cam_corner": "tr", "cam_size": 0.2}
check("toggle mati → tidak ada pengaturan manual",
      tools.manual_layout({**base, "subtitle_manual": False, "cam_manual": False}) == {})
only_sub = tools.manual_layout({**base, "subtitle_manual": True, "cam_manual": False})
check("hanya subtitle", only_sub == {"subtitle_y": 0.5})
check("panggilan API tanpa toggle tetap berlaku", tools.manual_layout(base) == base)
check("render_options membawa manual_layout",
      tools.render_options({**base, "subtitle_manual": True, "cam_manual": True})["manual_layout"] == base)
check("render_options default kosong", tools.render_options({})["manual_layout"] == {})

# ---------------------------------------------------------------- kotak facecam
check("pojok kanan atas", clipper.corner_cam_box("tr", 0.25) == [0.75, 0.0, 0.25, 0.25])
check("pojok kiri bawah", clipper.corner_cam_box("bl", 0.3) == [0.0, 0.7, 0.3, 0.3])
check("margin subtitle 72% dari atas", clipper.subtitle_margin(0.72) == round(1920 * 0.28))

det = {"cam_box": [0.76, 0.02, 0.22, 0.3], "game_box": [0, 0, 0.76, 1], "cam_ratio": 0.38,
       "face": [0.87, 0.15, 0.08, 0.13]}
check("tanpa pengaturan → layout apa adanya", clipper.apply_manual_layout(det, {}) is det)
check("tanpa facecam & tanpa pojok → tetap tanpa split", clipper.apply_manual_layout(None, {"cam_ratio": 0.5}) is None)
manual = clipper.apply_manual_layout(None, clipper.clean_manual_layout({"cam_corner": "tl", "cam_size": 0.25}))
check("pojok manual memaksa split", manual and manual["cam_box"] == [0.0, 0.0, 0.25, 0.25])
check("gameplay tanpa jendela facecam (kiri)", manual and manual["game_box"][0] >= 0.21)
check("split tidak dipaksa bila layout tidak boleh split",
      clipper.apply_manual_layout(None, {"cam_corner": "tl", "cam_size": 0.25}, can_split=False) is None)
taller = clipper.apply_manual_layout(det, {"cam_ratio": 0.5})
check("tinggi panel diganti", taller["cam_ratio"] == 0.5)
check("kotak dihitung ulang agar wajah tetap di tengah",
      taller["cam_box"] == clipper.centered_cam_box(det["face"], 0.5) and taller["cam_box"] != det["cam_box"])
check("layout asli tidak diubah", det["cam_ratio"] == 0.38)
check("facecam bawah", clipper.apply_manual_layout(det, {"cam_top": False})["cam_top"] is False)
camp = clipper.apply_manual_layout({**det, "campaign": True}, {"cam_top": False})
check("template campaign tetap facecam atas", camp.get("cam_top", True) is True)

check("area kanan bawah", clipper.in_area(0.9, 0.66, "br") and not clipper.in_area(0.9, 0.3, "br"))
check("area kiri atas", clipper.in_area(0.1, 0.1, "tl") and not clipper.in_area(0.6, 0.1, "tl"))
check("tanpa area = semua", clipper.in_area(0.5, 0.5, ""))

# ---------------------------------------------------------------- burn ulang subtitle
ov = subtitle_live.move_overrides({"vertical_position": 700, "margin_zones": [[0, 2.5, 300], [3, 4, 900]],
                                   "fx": True}, 0.6)
check("posisi baru (margin bawah)", ov["vertical_position"] == clipper.subtitle_margin(0.6))
check("zona yang tetap di bawah dipertahankan", ov["margin_zones"] == [[0, 2.5, 300]])
check("kunci lain tidak hilang", ov["fx"] is True)
check("zona dibuang semua bila bertabrakan", "margin_zones" not in subtitle_live.move_overrides(
    {"margin_zones": [[0, 2, 900]]}, 0.75))
check("posisi saat ini terbaca untuk slider",
      subtitle_live.current_y({"style": "modern", "style_overrides": {"vertical_position": 960}}) == 0.5)

if fails:
    print(f"\n{len(fails)} GAGAL")
    sys.exit(1)
print("\nSemua lolos")
