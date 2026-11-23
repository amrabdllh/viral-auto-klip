#!/usr/bin/env python3
"""Upload ke TikTok lewat browser (Playwright, **selalu headless**).

Sama seperti `core/youtube_browser.py`: tidak ada jendela/popup, tidak ada login
interaktif. Sesi datang dari cookies yang kamu tempel di Pengaturan.

Kenapa Playwright, bukan `tiktok-uploader`: paket itu memakai Selenium dan
membuka Chrome sendiri; kita butuh jaminan tidak ada jendela yang muncul.
Playwright juga sudah terpasang untuk YouTube.

Cookie disimpan di `data/tiktok/cookies.json` (data/ gitignored).
Format yang diterima: JSON (list Playwright / export extension) atau Netscape
cookies.txt.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

import config
from .utils import trim_caption

COOKIE_DIR = config.BASE_DIR / "data" / "tiktok"
COOKIE_FILE = COOKIE_DIR / "cookies.json"
# Cookie sesi TikTok (HttpOnly). Tanpa salah satunya TikTok Studio selalu mengarah ke halaman login.
_LOGIN_COOKIES = {"sessionid", "sessionid_ss", "sid_tt", "sid_guard"}
_MISSING_HELP = ("Cookie login TikTok (sessionid, sid_tt, sid_guard) tidak ikut. Biasanya karena disalin dari document.cookie/DevTools yang tidak bisa membaca cookie HttpOnly. Buka tiktok.com dalam keadaan login, export pakai extension \"Get cookies.txt LOCALLY\" (atau Cookie-Editor → Export JSON), lalu pilih/tempel file itu di sini.")

UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

CAPTION_SELECTORS = (
    'div[contenteditable="true"]',
    '.public-DraftEditor-content',
    '[data-e2e="caption-editor"]',
    'div[role="textbox"]',
)
POST_SELECTORS = (
    'button[data-e2e="post_video_button"]',
    'button:has-text("Post")',
    'button:has-text("Posting")',
    'button:has-text("Unggah")',
    'button:has-text("Upload")',
)
SAVE_DRAFT_SELECTORS = (
    'button[data-e2e="save_draft_button"]',
    'button[data-e2e="draft_button"]',
    'button:has-text("Simpan draf")',
    'button:has-text("Simpan sebagai draf")',
    'button:has-text("Save draft")',
    'button:has-text("Save as draft")',
    'button:has-text("Draf")',
)


class TikTokUploadError(ValueError):
    """Upload gagal — pesan sudah ramah untuk UI."""


# ==================== Cookies ====================
def _norm_cookie(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    name = str(item.get("name") or "").strip()
    value = item.get("value")
    domain = str(item.get("domain") or "").strip()
    if not name or value in (None, "") or not domain:
        return None
    expires = item.get("expires", item.get("expirationDate"))
    try:
        expires = float(expires) if expires not in (None, "", -1) else -1
    except (TypeError, ValueError):
        expires = -1
    if expires != -1 and expires < time.time():
        return None
    cookie: Dict[str, Any] = {
        "name": name, "value": str(value), "domain": domain,
        "path": str(item.get("path") or "/"), "expires": expires,
        "httpOnly": bool(item.get("httpOnly", False)),
        "secure": bool(item.get("secure", True)),
    }
    same_site = item.get("sameSite")
    if same_site in ("Strict", "Lax", "None"):
        cookie["sameSite"] = same_site
    return cookie


def parse_cookies(raw: str) -> List[Dict[str, Any]]:
    text = (raw or "").strip()
    if not text:
        raise TikTokUploadError("Isi cookies masih kosong")
    if text[0] in "[{":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise TikTokUploadError(f"JSON cookies tidak valid: {exc}") from exc
        items = data if isinstance(data, list) else data.get("cookies") or []
        cookies = [c for c in (_norm_cookie(i) for i in items if isinstance(i, dict)) if c]
        if not cookies:
            raise TikTokUploadError("Tidak ada cookie TikTok yang valid di JSON itu")
        return cookies

    cookies: List[Dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain, _flag, path, secure, expires, name, value = parts[:7]
        if "tiktok" not in domain:
            continue
        cookie = _norm_cookie({
            "name": name, "value": value, "domain": domain, "path": path,
            "secure": secure.upper() == "TRUE", "expires": expires, "httpOnly": False,
        })
        if cookie:
            cookies.append(cookie)
    if not cookies:
        raise TikTokUploadError("Tidak ada cookie tiktok.com di file itu")
    return cookies


def _session_check(cookies: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Apakah cookie LOGIN ikut ter-export. Cookie sesi bersifat HttpOnly sehingga tidak ikut bila disalin
    dari document.cookie / tab Console DevTools — hasilnya cookie 'tersimpan' tapi tidak pernah login."""
    names = {c.get("name") for c in cookies}
    found = sorted(names & _LOGIN_COOKIES)
    expiries = [c["expires"] for c in cookies if c.get("name") in _LOGIN_COOKIES and c.get("expires", -1) > 0]
    return {"logged_in": bool(found), "login_cookies": found, "session_expires_at": min(expiries) if expiries else None}


def save_cookies(raw: str) -> Dict[str, Any]:
    cookies = parse_cookies(raw)
    if not _session_check(cookies)["logged_in"]:
        raise TikTokUploadError(_MISSING_HELP)
    COOKIE_DIR.mkdir(parents=True, exist_ok=True)
    COOKIE_FILE.write_text(json.dumps({"saved_at": time.time(), "cookies": cookies}, indent=2),
                           encoding="utf-8")
    try:
        COOKIE_FILE.chmod(0o600)
    except OSError:
        pass
    return cookie_status()


def load_cookies() -> List[Dict[str, Any]]:
    if not COOKIE_FILE.is_file():
        return []
    try:
        payload = json.loads(COOKIE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    items = payload.get("cookies") if isinstance(payload, dict) else payload
    return [c for c in (_norm_cookie(i) for i in items or [] if isinstance(i, dict)) if c]


def cookie_status() -> Dict[str, Any]:
    cookies = load_cookies()
    saved_at = None
    if COOKIE_FILE.is_file():
        try:
            saved_at = json.loads(COOKIE_FILE.read_text(encoding="utf-8")).get("saved_at")
        except (OSError, json.JSONDecodeError, AttributeError):
            pass
    soonest = min((c["expires"] for c in cookies if c.get("expires", -1) > 0), default=None)
    sess = _session_check(cookies)
    return {"configured": sess["logged_in"], "incomplete": bool(cookies) and not sess["logged_in"],
            "count": len(cookies), "saved_at": saved_at, "expires_at": sess["session_expires_at"] or soonest,
            "login_cookies": sess["login_cookies"], "path": str(COOKIE_FILE)}


def persist_refreshed(context) -> bool:
    """Simpan cookie yang DIPERBARUI situs selama sesi headless (rotasi sesi). Tanpa ini salinan di disk cepat basi
    dan sesi mati dalam hitungan jam. Hanya disimpan bila masih berisi cookie login."""
    try:
        fresh = [c for c in (_norm_cookie(dict(x)) for x in context.cookies()) if c]
    except Exception:  # noqa: BLE001
        return False
    if not fresh or not _session_check(fresh)["logged_in"]:
        return False
    try:
        payload = json.loads(COOKIE_FILE.read_text(encoding="utf-8")) if COOKIE_FILE.is_file() else {}
    except (OSError, json.JSONDecodeError):
        payload = {}
    merged = {(c["name"], c.get("domain"), c.get("path")): c for c in load_cookies()}
    merged.update({(c["name"], c.get("domain"), c.get("path")): c for c in fresh})
    payload = {"saved_at": (payload or {}).get("saved_at") or time.time(), "refreshed_at": time.time(),
               "cookies": list(merged.values())}
    COOKIE_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return True


def clear_cookies() -> Dict[str, Any]:
    try:
        COOKIE_FILE.unlink(missing_ok=True)
    except OSError as exc:
        raise TikTokUploadError(f"Tidak bisa menghapus cookies: {exc}") from exc
    return cookie_status()


# ==================== Upload ====================
def _launch(p):
    args = ["--disable-blink-features=AutomationControlled",
            "--no-first-run", "--no-default-browser-check",
            "--disable-notifications", "--mute-audio"]
    try:
        return p.chromium.launch(channel="msedge", headless=True, args=args)
    except Exception:
        return p.chromium.launch(headless=True, args=args)


def _first(page, selectors, *, state="visible", timeout=8000):
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            loc.wait_for(state=state, timeout=timeout)
            return loc
        except Exception:  # noqa: BLE001 - coba selector berikutnya
            continue
    return None


def _dismiss_overlays(page) -> None:
    """Tutup tur/tutorial TikTok Studio (react-joyride: "Got it", "Last"...). Overlay-nya menutupi seluruh halaman,
    sehingga klik ke kolom caption gagal dan caption tetap NAMA FILE."""
    for _ in range(6):
        btn = page.locator("#react-joyride-portal button, .react-joyride__tooltip button").first
        try:
            if not btn.count():
                break
            try:
                btn.click(timeout=2000)
            except Exception:  # noqa: BLE001
                btn.evaluate("el => el.click()")
        except Exception:  # noqa: BLE001
            break
        page.wait_for_timeout(600)
    try:  # sisa overlay yang tidak punya tombol
        page.evaluate("document.querySelectorAll('.react-joyride__overlay').forEach(e => e.remove())")
    except Exception:  # noqa: BLE001
        pass


def _fill_caption(page, editor, text: str) -> None:
    """Ganti deskripsi default (nama file) dengan caption. Diverifikasi — lebih baik gagal daripada memposting
    video dengan caption nama file."""
    probe = " ".join(text.split()[:5])[:40]
    for _ in range(3):
        _dismiss_overlays(page)
        try:
            editor.click(timeout=5000)
        except Exception:  # noqa: BLE001
            editor.evaluate("el => el.focus()")
        page.keyboard.press("Control+A")
        page.keyboard.press("Backspace")
        page.keyboard.insert_text(text)
        page.wait_for_timeout(800)
        try:
            got = " ".join(editor.inner_text().split())
        except Exception:  # noqa: BLE001
            got = ""
        if probe and probe in got:
            return
    raise TikTokUploadError("Caption TikTok tidak bisa diisi (deskripsi masih nama file) — upload dibatalkan")


def _set_cover(page, image: str) -> str:
    """Edit cover → unggah gambar → Save. Tidak pernah menggagalkan upload video (cover hanya pemanis).
    Catatan: TikTok memotong cover ke 3:4 di profil — thumbnail AI menaruh isi penting di zona itu."""
    try:
        _dismiss_overlays(page)
        page.locator("text=Edit cover").first.click(timeout=8000)
        page.wait_for_timeout(2000)
        page.locator('input[type=file][accept*="image"]').first.set_input_files(str(image))
        page.wait_for_timeout(4000)
        page.locator('[role="dialog"] button', has_text="Save").first.click(timeout=8000)
        page.wait_for_timeout(2500)
        if page.locator('[role="dialog"] button', has_text="Save").count():
            raise RuntimeError("dialog cover tidak tertutup")
        return "set"
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[tiktok-browser] Cover tidak terpasang (pakai frame bawaan): {str(exc)[:160]}")
        try:  # tutup dialog yang mungkin masih terbuka
            page.locator('[role="dialog"] button', has_text="Cancel").first.click(timeout=2000)
        except Exception:  # noqa: BLE001
            pass
        return "skipped"


def _set_schedule(page, publish_at: float) -> None:
    """When to post -> Schedule, lalu pilih tanggal dari kalender & jam (kelipatan 5 menit). Zona waktu = lokal."""
    import re as _re
    from datetime import datetime
    ts = round(publish_at / 300) * 300  # pemilih menit TikTok: kelipatan 5
    when = datetime.fromtimestamp(ts)
    if ts < time.time() + 16 * 60:
        raise TikTokUploadError("Jadwal TikTok minimal 15 menit dari sekarang")
    _dismiss_overlays(page)
    radio = page.locator('input[name="postSchedule"][value="schedule"]').first
    radio.wait_for(state="attached", timeout=20_000)
    radio.evaluate("el => el.click()")
    page.wait_for_timeout(1500)
    if not page.evaluate("document.querySelector('input[name=postSchedule][value=schedule]').checked"):
        raise TikTokUploadError("Opsi Schedule TikTok tidak bisa dipilih")
    fields = page.locator("input.TUXTextInputCore-input")
    want_t, want_d = when.strftime("%H:%M"), when.strftime("%Y-%m-%d")
    day_re = _re.compile(rf"^{when.day}$")
    # tanggal dulu (mengganti tanggal bisa mereset jam)
    for _ in range(3):
        if fields.nth(1).input_value() == want_d:
            break
        fields.nth(1).click()
        page.wait_for_timeout(800)
        day = page.locator(".calendar-wrapper .day.valid", has_text=day_re)
        if not day.count():  # bulan berikutnya
            page.locator(".calendar-wrapper .arrow").last.click()
            page.wait_for_timeout(600)
            day = page.locator(".calendar-wrapper .day.valid", has_text=day_re)
        if not day.count():
            raise TikTokUploadError(f"Tanggal {want_d} tidak bisa dipilih di TikTok (di luar batas jadwal)")
        day.first.click()
        page.wait_for_timeout(800)
    # jam
    fields.nth(0).click()
    page.wait_for_timeout(800)
    for side, val in (("left", when.strftime("%H")), ("right", when.strftime("%M"))):
        opt = page.locator(f".tiktok-timepicker-option-text.tiktok-timepicker-{side}", has_text=_re.compile(rf"^{val}$")).first
        opt.scroll_into_view_if_needed()
        opt.click()
        page.wait_for_timeout(500)
    page.keyboard.press("Escape")
    page.wait_for_timeout(500)
    got_t, got_d = fields.nth(0).input_value(), fields.nth(1).input_value()
    if (got_t, got_d) != (want_t, want_d):
        raise TikTokUploadError(f"Jadwal TikTok tidak terpasang (terisi {got_d} {got_t}, seharusnya {want_d} {want_t})")


CONTENT_URL = "https://www.tiktok.com/tiktokstudio/content"
_ACTION_MORE = '[data-tt="components_ActionCell_Clickable"]'  # tombol "…" tiap baris (Unduh / Hapus)


def _search_content(page, probe: str, wait_ms: int = 6000) -> None:
    """Buka TikTok Studio → Konten lalu saring dengan kolom 'Cari deskripsi postingan'. Daftar hanya memuat ±10 baris
    pertama (urut jam tayang terbaru), jadi postingan terjadwal yang lebih awal tidak terlihat tanpa pencarian."""
    page.goto(CONTENT_URL, wait_until="domcontentloaded", timeout=45_000)
    page.wait_for_timeout(wait_ms)
    _dismiss_overlays(page)
    box = _first(page, ['input[placeholder*="Cari"]', 'input[placeholder*="Search"]', 'input[type="search"]'], timeout=8000)
    if box is None:
        return
    box.fill(probe[:60])
    box.press("Enter")
    page.wait_for_timeout(4000)


def _in_content_list(page, probe: str, tries: int = 3) -> bool:
    """Apakah postingan dengan awal caption `probe` ada di TikTok Studio → Konten (termasuk yang terjadwal)."""
    want = " ".join(probe.split()).lower()
    seen = lambda: want in " ".join(page.locator("body").inner_text().split()).lower()  # noqa: E731
    for i in range(tries):
        try:
            # 1) daftar biasa, di-scroll: postingan terjadwal ada di urutan teratas (urut jam tayang), tetapi hanya
            #    ±10 baris pertama yang dimuat. Pencarian TikTok belum mengindeks postingan yang baru diunggah.
            page.goto(CONTENT_URL, wait_until="domcontentloaded", timeout=45_000)
            page.wait_for_timeout(6000 + 3000 * i)
            _dismiss_overlays(page)
            for _ in range(6):
                if seen():
                    return True
                page.mouse.move(800, 600)
                page.mouse.wheel(0, 2500)
                page.wait_for_timeout(1500)
            # 2) cadangan: kolom pencarian (postingan lama yang tertimbun)
            _search_content(page, " ".join(probe.split()[:8]), 4000)
            if seen():
                return True
        except Exception:  # noqa: BLE001
            pass
    return False


def delete_posts(probes: List[str], progress=None) -> Dict[str, Any]:
    """HAPUS PERMANEN postingan (mis. yang terjadwal, karena TikTok tidak bisa mengedit jadwal) berdasarkan awal
    caption. Tiap postingan dicari, dihapus lewat menu "…" → Hapus → konfirmasi, lalu dipastikan sudah hilang.
    Return {probe: "deleted" | "not_found" | "error: …"}."""
    from playwright.sync_api import sync_playwright
    say = progress or (lambda *_: None)
    out: Dict[str, Any] = {}
    with sync_playwright() as p:
        browser = _launch(p)
        try:
            context = browser.new_context(user_agent=UA, viewport={"width": 1440, "height": 1000})
            context.add_cookies(load_cookies())
            page = context.new_page()
            for probe in probes:
                want = " ".join(probe.split()).lower()
                try:
                    _search_content(page, probe)
                    if "/login" in page.url:
                        raise TikTokUploadError("Cookies kedaluwarsa — perbarui di Pengaturan")
                    rows = page.locator("div").filter(has_text=probe[:40]).filter(has=page.locator(_ACTION_MORE))
                    if not rows.count() or want not in " ".join(page.locator("body").inner_text().split()).lower():
                        out[probe] = "not_found"
                        continue
                    row = rows.last  # elemen terdalam = baris postingan itu sendiri
                    if row.locator(_ACTION_MORE).count() != 1:
                        out[probe] = "error: baris tidak unik"
                        continue
                    row.hover()
                    row.locator(_ACTION_MORE).click()
                    page.wait_for_timeout(800)
                    page.get_by_text(re.compile(r"^\s*(Hapus|Delete)\s*$")).last.click(timeout=5000)
                    page.wait_for_timeout(1200)
                    page.locator('[role="dialog"] button').filter(
                        has_text=re.compile(r"^\s*(Hapus|Delete)\s*$")).last.click(timeout=8000)
                    page.wait_for_timeout(4000)
                    _search_content(page, probe)
                    gone = want not in " ".join(page.locator("body").inner_text().split()).lower()
                    out[probe] = "deleted" if gone else "error: masih ada setelah dihapus"
                    say("Hapus", 0, f"{probe[:50]} → {out[probe]}")
                except Exception as exc:  # noqa: BLE001
                    out[probe] = "error: " + str(exc).splitlines()[0][:200]
            persist_refreshed(context)
        finally:
            browser.close()
    return out


def upload_video(video: Path | str, *, description: str = "", timeout: int = 300,
                 progress=None, mode: Optional[str] = None, publish_at: Optional[float] = None,
                 cover: Optional[str] = None) -> Dict[str, Any]:
    """Upload satu video ke TikTok Studio (headless). Tidak pernah membuka jendela.

    mode: "draft" (default — simpan ke Draf TikTok Studio) atau "post" (langsung posting).
    publish_at (epoch detik): JADWAL BAWAAN TikTok ("When to post -> Schedule") — diunggah sekarang, TikTok sendiri
    yang memposting pada waktu itu (dibulatkan ke 5 menit; TikTok hanya menerima >=15 menit s/d +-10 hari ke depan).
    """
    mode = str(mode or getattr(config, "TIKTOK_UPLOAD_MODE", "draft") or "draft").strip().lower()
    if mode not in ("draft", "post"):
        mode = "draft"
    if publish_at:
        mode = "schedule"
    def say(stage: str, pct: int, message: str) -> None:
        logger.info(f"[tiktok-browser] {message}")
        if progress:
            try:
                progress(stage, pct, message)
            except Exception:  # noqa: BLE001
                pass

    video = Path(video).resolve()
    if not video.is_file():
        raise TikTokUploadError(f"File video tidak ditemukan: {video.name}")
    cookies = load_cookies()
    if not cookies:
        raise TikTokUploadError(
            "Cookies TikTok belum diatur. Buka Pengaturan → TikTok, lalu tempel cookies.")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise TikTokUploadError(
            "Playwright belum terpasang: pip install playwright && playwright install chromium"
        ) from exc

    deadline = time.monotonic() + timeout
    say("Upload", 5, f"Membuka TikTok Studio (headless) untuk {video.name}…")

    with sync_playwright() as p:
        browser = _launch(p)
        try:
            context = browser.new_context(user_agent=UA)
            context.add_cookies(cookies)
            page = context.new_page()
            page.goto(UPLOAD_URL, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(5000)

            if "/login" in page.url or page.locator('text="Log in"').count():
                if "/login" in page.url:
                    raise TikTokUploadError(
                        "Sesi TikTok kedaluwarsa. Perbarui cookies di Pengaturan → TikTok.")

            say("Upload", 20, "Memilih file…")
            file_input = _first(page, ('input[type="file"]',), state="attached", timeout=30_000)
            if file_input is None:
                raise TikTokUploadError("Input upload tidak ditemukan — UI TikTok mungkin berubah")
            file_input.set_input_files(str(video))

            say("Upload", 40, "Menunggu editor caption…")
            editor = _first(page, CAPTION_SELECTORS, timeout=60_000)
            if editor is not None and description:
                _fill_caption(page, editor, trim_caption(description, 2000))

            cover_set = None
            if cover and Path(cover).is_file():
                say("Upload", 50, "Memasang cover (thumbnail)…")
                cover_set = _set_cover(page, cover)

            if publish_at:
                say("Upload", 55, "Mengatur jadwal posting di TikTok…")
                _set_schedule(page, float(publish_at))

            say("Upload", 60, "Menunggu video selesai diproses…")
            target = SAVE_DRAFT_SELECTORS if mode == "draft" else (
                ('button[data-e2e="post_video_button"]',) + tuple(POST_SELECTORS))
            action_btn = None
            while time.monotonic() < deadline:
                action_btn = _first(page, target, timeout=3000)
                if action_btn is not None:
                    try:
                        disabled = action_btn.get_attribute("disabled") is not None or \
                                   action_btn.get_attribute("aria-disabled") == "true"
                    except Exception:  # noqa: BLE001
                        disabled = False
                    if not disabled:
                        break
                page.wait_for_timeout(2000)

            if action_btn is None:
                if mode == "draft":
                    raise TikTokUploadError(
                        "Tombol Simpan draf tidak ditemukan — UI TikTok mungkin berubah. "
                        "Ubah Pengaturan → Mode upload TikTok ke 'post' untuk langsung posting.")
                raise TikTokUploadError("Tombol Post tidak muncul — UI TikTok mungkin berubah")

            _dismiss_overlays(page)
            say("Upload", 80, {"draft": "Menyimpan ke Draf…", "schedule": "Menekan Schedule…"}.get(mode, "Menekan Post…"))
            try:
                action_btn.click(timeout=5000)
            except Exception:  # noqa: BLE001
                action_btn.evaluate("el => el.click()")

            # Tunggu konfirmasi. JANGAN mencocokkan kata umum ("Uploaded" = status file, "Save draft" = label tombol):
            # dulu itu membuat upload dianggap sukses padahal browser ditutup sebelum TikTok menyimpan apa pun.
            uploaded = False
            toast = (r"draft saved|saved to drafts|draf (berhasil )?disimpan|disimpan ke draf" if mode == "draft" else
                     r"your video (has been|is being) (uploaded|posted|scheduled)|post(ed)? successfully|"
                     r"scheduled successfully|video (berhasil )?(diposting|dijadwalkan)")
            clicked_modal = False
            for _ in range(45):
                if time.monotonic() > deadline:
                    break
                if "tiktokstudio/content" in page.url or "/upload" not in page.url:
                    uploaded = True
                    break
                try:
                    if page.locator(f"text=/{toast}/i").count():
                        uploaded = True
                        break
                    # dialog konfirmasi ("Post now?", "Continue to post?") → tekan tombol utamanya sekali
                    if not clicked_modal:
                        btn = page.locator('[role="dialog"] button').filter(
                            has_text=__import__("re").compile(r"^(post now|post|schedule|continue|lanjutkan|posting)$", 2)).first
                        if btn.count():
                            btn.click(timeout=3000)
                            clicked_modal = True
                except Exception:  # noqa: BLE001
                    pass
                page.wait_for_timeout(2000)

            if mode != "draft":
                # bukti terakhir: postingan (dengan caption ini) benar-benar ada di TikTok Studio → Konten
                probe = " ".join(trim_caption(description, 2000).split()[:5])[:40] if description else ""
                if probe and not _in_content_list(page, probe):
                    raise TikTokUploadError("TikTok tidak menyimpan postingan (tidak ada di TikTok Studio → Konten) — "
                                            "akan dicoba lagi")
                uploaded = True

            if not uploaded:
                if mode == "draft":
                    # Draf kadang tersimpan tanpa memunculkan teks konfirmasi — jangan
                    # gagalkan jadwal; minta pengguna mengecek TikTok Studio → Draf.
                    logger.warning("[tiktok-browser] Draf tidak terkonfirmasi — cek TikTok Studio → Konten → Draf")
                    persist_refreshed(context)  # simpan cookie hasil rotasi sesi
                    return {"ok": True, "mode": "draft", "confirmed": False,
                            "privacy": "default", "description": description, "file": str(video)}
                raise TikTokUploadError(
                    "Post ditekan tetapi konfirmasi tidak terlihat. Cek TikTok Studio → Konten.")
            say("Upload", 100, {"draft": "Draf TikTok tersimpan", "schedule": "Terjadwal di TikTok"}.get(mode, "Upload TikTok selesai"))
            persist_refreshed(context)  # simpan cookie hasil rotasi sesi
            if mode == "schedule":
                return {"ok": True, "status": "scheduled", "mode": "schedule", "publish_at": float(publish_at),
                        "privacy": "everyone", "description": description, "file": str(video), "cover": cover_set}
            return {"ok": True, "mode": mode, "privacy": "default", "description": description,
                    "file": str(video), "cover": cover_set}
        except TikTokUploadError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise TikTokUploadError(f"Upload browser gagal: {str(exc).splitlines()[0][:300]}") from exc
        finally:
            try:
                browser.close()
            except Exception:  # noqa: BLE001
                pass


def test_session() -> Dict[str, Any]:
    """Cek cookies TikTok tanpa upload (headless, tanpa popup)."""
    cookies = load_cookies()
    if not cookies:
        return {"ok": False, "error": "Cookies belum diatur"}
    if not _session_check(cookies)["logged_in"]:
        return {"ok": False, "error": _MISSING_HELP}
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"ok": False, "error": "Playwright belum terpasang: pip install playwright && playwright install chromium"}
    with sync_playwright() as p:
        browser = _launch(p)
        try:
            context = browser.new_context(user_agent=UA)
            context.add_cookies(cookies)
            page = context.new_page()
            page.goto(UPLOAD_URL, wait_until="domcontentloaded", timeout=45_000)
            page.wait_for_timeout(4000)
            if "/login" in page.url:
                return {"ok": False, "error": "Cookies kedaluwarsa — perbarui di Pengaturan"}
            name = ""
            try:
                name = page.locator('[data-e2e="profile-username"], [data-e2e="user-name"]').first.inner_text(timeout=3000).strip()
            except Exception:  # noqa: BLE001
                pass
            persist_refreshed(context)  # simpan cookie hasil rotasi sesi
            return {"ok": True, "username": name}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc).splitlines()[0][:200]}
        finally:
            try:
                browser.close()
            except Exception:  # noqa: BLE001
                pass


__all__ = [
    "TikTokUploadError", "COOKIE_FILE", "parse_cookies", "save_cookies", "delete_posts",
    "load_cookies", "cookie_status", "clear_cookies", "upload_video", "test_session",
]
