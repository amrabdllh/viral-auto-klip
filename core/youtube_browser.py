#!/usr/bin/env python3
"""Upload ke YouTube lewat browser (Playwright, **selalu headless**).

Kenapa browser, bukan API: kuota YouTube Data API hanya ~10.000 unit/hari dan
satu upload = 1.600 unit (≈6 upload/hari). Browser tidak memakai kuota itu.

Aturan penting (permintaan user): **tidak boleh ada popup/jendela browser**.
Semua jalan headless. Jadi:
    - `headless=True` tidak bisa dimatikan lewat UI
    - tidak ada login interaktif; sesi datang dari cookies yang di-paste di
      Pengaturan (lihat `save_cookies`)
    - kalau cookies kedaluwarsa: kembalikan error yang jelas, JANGAN buka jendela

Cookie disimpan di `data/youtube/cookies.json` (data/ gitignored).
Format yang diterima: JSON (list Playwright / export extension) atau Netscape
cookies.txt.
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

import config
from .utils import trim_caption

COOKIE_DIR = config.BASE_DIR / "data" / "youtube"
COOKIE_FILE = COOKIE_DIR / "cookies.json"
# Cookie sesi Google/YouTube (HttpOnly). Minimal salah satu wajib ada agar YouTube Studio terbuka tanpa login.
_LOGIN_COOKIES = {"SID", "__Secure-1PSID", "__Secure-3PSID", "HSID", "SSID", "LOGIN_INFO"}
_MISSING_HELP = ("Cookie login YouTube (SID, HSID, SSID, __Secure-1PSID, LOGIN_INFO) tidak ikut. Biasanya karena disalin dari document.cookie/DevTools yang tidak bisa membaca cookie HttpOnly. Buka youtube.com dalam keadaan login, export pakai extension \"Get cookies.txt LOCALLY\" (atau Cookie-Editor → Export JSON), lalu pilih/tempel file itu di sini.")

TITLE_SELECTOR = (
    '#textbox[placeholder*="title" i], #textbox[placeholder*="judul" i], '
    '#textbox[aria-label*="title" i], #textbox[aria-label*="judul" i], '
    '#textbox[aria-label*="Tambahkan judul" i]'
)
DESCRIPTION_SELECTOR = (
    '#textbox[placeholder*="description" i], #textbox[placeholder*="deskripsi" i], '
    '#textbox[aria-label*="description" i], #textbox[aria-label*="deskripsi" i], '
    '#textbox[aria-label*="Beri tahu penonton" i]'
)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

PRIVACY_MAP = {"public": "PUBLIC", "unlisted": "UNLISTED", "private": "PRIVATE"}


class YouTubeUploadError(ValueError):
    """Upload gagal — pesan sudah ramah untuk ditampilkan di UI."""


# ==================== Cookie management ====================
def _norm_cookie(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Satu cookie → format Playwright, atau None kalau tidak bisa dipakai."""
    name = str(item.get("name") or "").strip()
    value = item.get("value")
    domain = str(item.get("domain") or "").strip()
    if not name or value in (None, ""):
        return None
    if not domain:
        return None
    path = str(item.get("path") or "/")
    expires = item.get("expires", item.get("expirationDate"))
    try:
        expires = float(expires) if expires not in (None, "", -1) else -1
    except (TypeError, ValueError):
        expires = -1
    if expires != -1 and expires < time.time():
        return None  # cookie kedaluwarsa: jangan dikirim
    cookie: Dict[str, Any] = {
        "name": name, "value": str(value), "domain": domain, "path": path,
        "expires": expires, "httpOnly": bool(item.get("httpOnly", False)),
        "secure": bool(item.get("secure", True)),
    }
    same_site = item.get("sameSite")
    if same_site in ("Strict", "Lax", "None"):
        cookie["sameSite"] = same_site
    return cookie


def parse_cookies(raw: str) -> List[Dict[str, Any]]:
    """Terima JSON atau Netscape cookies.txt, kembalikan cookie siap Playwright."""
    text = (raw or "").strip()
    if not text:
        raise YouTubeUploadError("Isi cookies masih kosong")

    # ---- JSON
    if text[0] in "[{":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise YouTubeUploadError(f"JSON cookies tidak valid: {exc}") from exc
        items = data if isinstance(data, list) else data.get("cookies") or []
        cookies = [c for c in (_norm_cookie(item) for item in items if isinstance(item, dict)) if c]
        if not cookies:
            raise YouTubeUploadError("Tidak ada cookie YouTube yang valid di JSON itu")
        return cookies

    # ---- Netscape cookies.txt
    cookies: List[Dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain, _flag, path, secure, expires, name, value = parts[:7]
        if "youtube" not in domain and "google" not in domain:
            continue
        cookie = _norm_cookie({
            "name": name, "value": value, "domain": domain, "path": path,
            "secure": secure.upper() == "TRUE", "expires": expires,
            "httpOnly": False,
        })
        if cookie:
            cookies.append(cookie)
    if not cookies:
        raise YouTubeUploadError("Tidak ada cookie youtube.com/google.com di file itu")
    return cookies


def _session_check(cookies: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Apakah cookie LOGIN ikut ter-export. Cookie sesi bersifat HttpOnly sehingga tidak ikut bila disalin
    dari document.cookie / tab Console DevTools — hasilnya cookie 'tersimpan' tapi tidak pernah login."""
    names = {c.get("name") for c in cookies}
    found = sorted(names & _LOGIN_COOKIES)
    expiries = [c["expires"] for c in cookies if c.get("name") in _LOGIN_COOKIES and c.get("expires", -1) > 0]
    return {"logged_in": bool(found), "login_cookies": found, "session_expires_at": min(expiries) if expiries else None}


def save_cookies(raw: str) -> Dict[str, Any]:
    """Validasi lalu simpan cookies. Return ringkasan (tanpa isi cookie)."""
    cookies = parse_cookies(raw)
    if not _session_check(cookies)["logged_in"]:
        raise YouTubeUploadError(_MISSING_HELP)
    COOKIE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"saved_at": time.time(), "cookies": cookies}
    COOKIE_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
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
    """Ringkasan untuk UI — TIDAK pernah mengembalikan nilai cookie."""
    cookies = load_cookies()
    saved_at = None
    if COOKIE_FILE.is_file():
        try:
            saved_at = json.loads(COOKIE_FILE.read_text(encoding="utf-8")).get("saved_at")
        except (OSError, json.JSONDecodeError, AttributeError):
            pass
    soonest = min((c["expires"] for c in cookies if c.get("expires", -1) > 0), default=None)
    sess = _session_check(cookies)
    return {
        # configured = siap dipakai upload (ada cookie login). incomplete = ada cookie, tapi bukan sesi login.
        "configured": sess["logged_in"],
        "incomplete": bool(cookies) and not sess["logged_in"],
        "count": len(cookies),
        "saved_at": saved_at,
        "expires_at": sess["session_expires_at"] or soonest,
        "login_cookies": sess["login_cookies"],
        "path": str(COOKIE_FILE),
    }


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
        raise YouTubeUploadError(f"Tidak bisa menghapus cookies: {exc}") from exc
    return cookie_status()


# ==================== Upload ====================
def _launch(p):
    """Chromium headless; Edge kalau tersedia. Tidak pernah headless=False."""
    args = ["--disable-blink-features=AutomationControlled",
            "--no-first-run", "--no-default-browser-check",
            "--disable-notifications", "--mute-audio"]
    try:
        return p.chromium.launch(channel="msedge", headless=True, args=args)
    except Exception:
        return p.chromium.launch(headless=True, args=args)


def _set_text(page, selector: str, text: str, wait: int = 60_000) -> None:
    box = page.locator(selector).first
    box.wait_for(state="visible", timeout=wait)
    box.click()
    page.keyboard.press("Control+A")
    page.keyboard.press("Backspace")
    page.wait_for_timeout(200)
    page.keyboard.insert_text(text)
    page.wait_for_timeout(300)


def _set_schedule(page, publish_at: float) -> None:
    """Visibilitas → Jadwalkan: isi tanggal & jam (zona waktu lokal). Format mengikuti bahasa Studio
    (mis. id-ID "22 Nov 2026" · "10.48", en-US "Nov 22, 2026" · "10:48 AM") — dibuat dengan Intl di halaman."""
    page.locator("#second-container-expand-button").first.wait_for(state="visible", timeout=20_000)
    page.locator("#second-container-expand-button").first.click()
    page.wait_for_timeout(1200)
    ms = int(publish_at * 1000)
    date_s, time_s = page.evaluate("""(ms) => { const d = new Date(ms); const l = document.documentElement.lang || 'en-US';
        return [new Intl.DateTimeFormat(l, {day: 'numeric', month: 'short', year: 'numeric'}).format(d),
                new Intl.DateTimeFormat(l, {hour: '2-digit', minute: '2-digit'}).format(d)]; }""", ms)
    page.locator("#datepicker-trigger").first.click()
    page.wait_for_timeout(800)
    date_in = page.locator("ytcp-date-picker input").first
    date_in.fill(date_s)
    date_in.press("Enter")
    page.wait_for_timeout(1000)
    time_in = page.locator("#time-of-day-container input").first
    time_in.click()
    time_in.fill(time_s)
    time_in.press("Enter")
    page.wait_for_timeout(1000)
    got_date = page.locator("#datepicker-trigger").first.inner_text().strip()
    got_time = time_in.input_value().strip()
    if got_date != date_s or got_time != time_s:
        raise YouTubeUploadError(f"Jadwal YouTube tidak terpasang (tanggal '{got_date}' jam '{got_time}', "
                                 f"seharusnya '{date_s}' '{time_s}')")


def _set_thumbnail(page, image: str) -> str:
    """Detail → Thumbnail → Upload file. YouTube hanya mengizinkan thumbnail kustom untuk channel TERVERIFIKASI
    (youtube.com/verify) — kalau belum, dilewati tanpa menggagalkan upload video."""
    try:
        inp = page.locator("ytcp-thumbnails-compact-editor-uploader input[type=file], #file-loader").first
        inp.wait_for(state="attached", timeout=8000)
        if inp.is_disabled():
            return "skipped: thumbnail kustom butuh channel terverifikasi"
        inp.set_input_files(str(image))
        page.wait_for_timeout(3500)
        txt = page.locator("ytcp-uploads-dialog").first.inner_text(timeout=3000)
        if re.search(r"verifi|verify", txt, re.I) and re.search(r"thumbnail", txt, re.I):
            return "skipped: thumbnail kustom butuh channel terverifikasi"
        return "set"
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[yt-browser] Thumbnail kustom tidak terpasang: {str(exc)[:160]}")
        return "skipped"


DAILY_LIMIT_MSG = ("Batas upload harian YouTube tercapai (channel belum diverifikasi: ±15 video/24 jam). "
                   "Upload dilanjutkan otomatis nanti — atau verifikasi channel sekali di youtube.com/verify agar batasnya hilang.")


def _check_daily_limit(page, wait_ms: int = 8000) -> None:
    """YouTube mengunci dialog upload dengan "Batas upload harian telah tercapai" (channel belum terverifikasi).
    Form tetap tampil tapi tertutup lapisan transparan, sehingga klik apa pun timeout — kenali & laporkan jelas."""
    import re as _re
    end = time.monotonic() + wait_ms / 1000
    while True:  # peringatan muncul bersamaan dengan form detail — pantau sebentar setelah file dipilih
        try:
            txt = page.locator("ytcp-uploads-dialog").first.inner_text(timeout=2000)
        except Exception:  # noqa: BLE001
            txt = ""
        if _re.search(r"batas upload harian|daily upload limit", txt, _re.I):
            try:
                page.locator("ytcp-uploads-dialog #close-button").first.click(timeout=3000)
            except Exception:  # noqa: BLE001
                pass
            raise YouTubeUploadError(DAILY_LIMIT_MSG)
        if time.monotonic() >= end:
            return
        page.wait_for_timeout(1000)


def _confirm_scheduled(page, title: str) -> bool:
    """Bukti jadwal terpasang: dialog "Video dijadwalkan", atau baris video di Konten → Shorts berstatus Dijadwalkan."""
    import re as _re
    try:
        txt = " ".join(page.locator("body").inner_text().split())
        if _re.search(r"video dijadwalkan|video scheduled|akan disetel ke publik pada|will (go|be made) public on", txt, _re.I):
            return True
    except Exception:  # noqa: BLE001
        pass
    want = " ".join(title.split())[:40].lower()
    m = _re.search(r"/channel/[^/?#]+", page.url)
    base = "https://studio.youtube.com" + (m.group(0) if m else "")
    for i in range(3):
        try:
            page.goto(base + "/videos/short", wait_until="domcontentloaded", timeout=45_000)
            page.wait_for_timeout(7000 + 3000 * i)
            rows = page.evaluate("""() => [...document.querySelectorAll('ytcp-video-row')].slice(0, 15).map(r =>
                (((r.querySelector('#video-title') || {}).innerText || '') + ' || ' +
                 ((r.querySelector('.tablecell-visibility, [class*=visibility]') || {}).innerText || '')))""")
            for row in rows:
                name, _, vis = row.partition(" || ")
                if " ".join(name.split()).lower().startswith(want) and _re.search(r"dijadwalkan|scheduled", vis, _re.I):
                    return True
        except Exception:  # noqa: BLE001
            pass
    return False


def upload_video(video: Path | str, *, title: str, description: str = "",
                 tags: Optional[List[str]] = None, privacy: str = "private",
                 timeout: int = 300,
                 progress=None, publish_at: Optional[float] = None,
                 thumbnail: Optional[str] = None) -> Dict[str, Any]:
    """Upload satu video ke YouTube Studio (headless). Return dict hasil.

    `publish_at` (epoch detik): pakai JADWAL BAWAAN YouTube — video diunggah sekarang sebagai pribadi dan YouTube
    sendiri yang mempublikasikannya pada waktu itu (tidak bergantung sesi cookie / laptop menyala saat jamnya).

    `progress(stage: str, pct: int, message: str)` opsional untuk job log.
    Tidak pernah membuka jendela browser dan tidak pernah meminta input.
    """
    def say(stage: str, pct: int, message: str) -> None:
        logger.info(f"[yt-browser] {message}")
        if progress:
            try:
                progress(stage, pct, message)
            except Exception:  # noqa: BLE001
                pass

    video = Path(video).resolve()
    if not video.is_file():
        raise YouTubeUploadError(f"File video tidak ditemukan: {video.name}")

    cookies = load_cookies()
    if not cookies:
        raise YouTubeUploadError(
            "Cookies YouTube belum diatur. Buka Pengaturan → YouTube, lalu tempel cookies."
        )

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise YouTubeUploadError(
            "Playwright belum terpasang: pip install playwright && playwright install chromium"
        ) from exc

    privacy = PRIVACY_MAP.get(str(privacy).lower(), "PRIVATE")
    deadline = time.monotonic() + timeout
    say("Upload", 5, f"Membuka YouTube Studio (headless) untuk {video.name}…")

    with sync_playwright() as p:
        browser = _launch(p)
        try:
            context = browser.new_context(user_agent=UA)
            context.add_cookies(cookies)
            page = context.new_page()
            page.goto("https://studio.youtube.com", wait_until="domcontentloaded", timeout=60_000)
            try:
                page.wait_for_load_state("networkidle", timeout=15_000)
            except Exception:  # noqa: BLE001 - networkidle sering tidak tercapai
                pass
            page.wait_for_timeout(3000)

            if "accounts.google.com" in page.url:
                raise YouTubeUploadError(
                    "Sesi YouTube kedaluwarsa. Perbarui cookies di Pengaturan → YouTube."
                )

            # Buka dialog upload
            file_input = None
            for attempt in range(1, 4):
                say("Upload", 10 + attempt * 5, f"Membuka dialog upload (percobaan {attempt})…")
                btn = page.locator('#upload-button, button:has-text("Upload video"), [aria-label*="Upload" i]').first
                if btn.is_visible(timeout=4000):
                    btn.click()
                else:
                    create = page.locator('#create-icon, button:has-text("Buat"), button:has-text("Create")').first
                    if create.is_visible(timeout=4000):
                        create.click()
                        page.wait_for_timeout(800)
                        page.locator('tp-yt-paper-item:has-text("Upload"), [test-id="upload-action"]').first.click()
                try:
                    candidate = page.locator('input[type="file"]')
                    candidate.wait_for(state="attached", timeout=10_000)
                    file_input = candidate
                    break
                except Exception:  # noqa: BLE001
                    page.wait_for_timeout(2000)
            if file_input is None:
                raise YouTubeUploadError("Dialog upload tidak terbuka setelah 3 percobaan")

            say("Upload", 30, "Memilih file & menunggu form detail…")
            file_input.set_input_files(str(video))
            _check_daily_limit(page)
            _set_text(page, TITLE_SELECTOR, title[:100])
            if description:
                try:
                    page.locator('button:has-text("Tampilkan lainnya"), button:has-text("Show more")').first.click(timeout=3000)
                    page.wait_for_timeout(500)
                except Exception:  # noqa: BLE001
                    pass
                _set_text(page, DESCRIPTION_SELECTOR, trim_caption(description, 4900), wait=20_000)
            thumb_set = _set_thumbnail(page, thumbnail) if thumbnail and Path(thumbnail).is_file() else None
            if tags:
                try:
                    _set_text(page, '#textbox[aria-label*="tag" i], #textbox[placeholder*="tag" i]',
                              ", ".join(tags)[:500], wait=10_000)
                except Exception:  # noqa: BLE001 - tag opsional
                    pass

            # Wizard: klik Next sampai halaman visibility
            for step in range(3):
                if time.monotonic() > deadline:
                    raise YouTubeUploadError("Upload melewati batas waktu")
                nxt = page.locator("#next-button").first
                try:
                    nxt.wait_for(state="attached", timeout=10_000)
                    page.wait_for_function(
                        """() => { const n = document.querySelector('#next-button');
                                   return n && !n.hasAttribute('disabled'); }""",
                        timeout=90_000)
                    nxt.evaluate("el => el.click()")
                    page.wait_for_timeout(1200)
                except Exception:  # noqa: BLE001
                    break

            if publish_at:
                say("Upload", 65, "Mengatur jadwal publikasi di YouTube…")
                _set_schedule(page, float(publish_at))
            else:
                say("Upload", 65, f"Mengatur visibilitas: {privacy.lower()}…")
            if not publish_at:
                try:
                    page.locator('tp-yt-paper-radio-button[name="PUBLIC"]').first.wait_for(state="attached", timeout=15_000)
                except Exception:  # noqa: BLE001
                    pass
                radio = page.locator(f'tp-yt-paper-radio-button[name="{privacy}"]').first
                if radio.count():
                    try:
                        radio.click(timeout=5000)
                    except Exception:  # noqa: BLE001
                        radio.evaluate("el => el.click()")

            say("Upload", 75, "Menunggu proses selesai & menekan Publish…")
            page.wait_for_function(
                """() => { const d = document.querySelector('#done-button');
                           return d && !d.hasAttribute('disabled') && d.offsetParent !== null; }""",
                timeout=120_000)
            page.locator("#done-button").first.evaluate("el => el.click()")
            page.wait_for_timeout(6000)

            # Ambil link video dari dialog konfirmasi
            link = None
            for _ in range(20):
                try:
                    href = page.locator('a[href*="youtu.be/"], a[href*="youtube.com/watch"]').first.get_attribute("href", timeout=2000)
                    if href:
                        link = href
                        break
                except Exception:  # noqa: BLE001
                    pass
                page.wait_for_timeout(1000)

            if publish_at and not _confirm_scheduled(page, title):
                raise YouTubeUploadError("YouTube tidak menampilkan video ini sebagai 'Dijadwalkan' — akan dicoba lagi")
            say("Upload", 100, "Upload selesai — terjadwal di YouTube" if publish_at else "Upload selesai")
            persist_refreshed(context)  # simpan cookie hasil rotasi sesi
            if publish_at:
                return {"ok": True, "status": "scheduled", "url": link, "privacy": "scheduled",
                        "publish_at": float(publish_at), "title": title, "file": str(video), "thumbnail": thumb_set}
            return {"ok": True, "url": link, "privacy": privacy.lower(),
                    "title": title, "file": str(video), "thumbnail": thumb_set}
        except YouTubeUploadError:
            raise
        except Exception as exc:  # noqa: BLE001 - pesan Playwright panjang, ringkas
            _check_daily_limit(page, wait_ms=0)  # klik gagal karena dialog terkunci batas harian?
            msg = str(exc).splitlines()[0][:300]
            raise YouTubeUploadError(f"Upload browser gagal: {msg}") from exc
        finally:
            try:
                browser.close()
            except Exception:  # noqa: BLE001
                pass


def set_thumbnail(video_id: str, image: str, timeout: int = 120) -> Dict[str, Any]:
    """Pasang thumbnail kustom ke video yang SUDAH ada (Studio → Detail video → Thumbnail → Upload file → Simpan).
    Headless. Return {"ok", "status": "set"|"skipped"|"failed", "reason"}."""
    if not Path(image).is_file():
        return {"ok": False, "status": "failed", "reason": "file thumbnail tidak ada"}
    cookies = load_cookies()
    if not cookies:
        return {"ok": False, "status": "failed", "reason": "Cookies YouTube belum diatur"}
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = _launch(p)
        try:
            context = browser.new_context(user_agent=UA)
            context.add_cookies(cookies)
            page = context.new_page()
            page.goto(f"https://studio.youtube.com/video/{video_id}/edit", wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(6000)
            if "accounts.google.com" in page.url:
                return {"ok": False, "status": "failed", "reason": "Sesi YouTube kedaluwarsa — perbarui cookies"}
            inp = page.locator("#file-loader, ytcp-thumbnail-uploader input[type=file]").first
            inp.wait_for(state="attached", timeout=20_000)
            inp.set_input_files(str(image))
            page.wait_for_timeout(5000)
            body = page.locator("body").inner_text()
            if re.search(r"verifikasi|verify", body, re.I) and re.search(r"thumbnail|gambar mini", body, re.I)                     and not page.locator("#save:not([disabled])").count():
                return {"ok": False, "status": "skipped", "reason": "Thumbnail kustom butuh channel terverifikasi (youtube.com/verify)"}
            save = page.locator("ytcp-button#save, #save").first
            page.wait_for_function("""() => { const b = document.querySelector('ytcp-button#save, #save');
                                               return b && !b.hasAttribute('disabled'); }""", timeout=30_000)
            save.click()
            ok = False
            for _ in range(20):  # setelah tersimpan tombol Simpan kembali nonaktif
                page.wait_for_timeout(1000)
                if page.evaluate("""() => { const b = document.querySelector('ytcp-button#save, #save');
                                            return b && b.hasAttribute('disabled'); }"""):
                    ok = True
                    break
            persist_refreshed(context)
            if not ok:
                return {"ok": False, "status": "failed", "reason": "Simpan tidak terkonfirmasi"}
            return {"ok": True, "status": "set"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "status": "failed", "reason": str(exc).splitlines()[0][:200]}
        finally:
            try:
                browser.close()
            except Exception:  # noqa: BLE001
                pass


def test_session() -> Dict[str, Any]:
    """Cek cookies masih valid tanpa upload apa pun. Headless, tanpa popup."""
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
            page.goto("https://studio.youtube.com", wait_until="domcontentloaded", timeout=45_000)
            page.wait_for_timeout(3000)
            if "accounts.google.com" in page.url:
                return {"ok": False, "error": "Cookies kedaluwarsa — perbarui di Pengaturan"}
            channel = ""
            try:
                channel = page.locator("ytcp-account-name, #account-name").first.inner_text(timeout=3000).strip()
            except Exception:  # noqa: BLE001
                pass
            persist_refreshed(context)  # simpan cookie hasil rotasi sesi
            return {"ok": True, "channel": channel}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc).splitlines()[0][:200]}
        finally:
            try:
                browser.close()
            except Exception:  # noqa: BLE001
                pass


__all__ = [
    "YouTubeUploadError", "COOKIE_FILE", "parse_cookies", "save_cookies",
    "load_cookies", "cookie_status", "clear_cookies", "upload_video", "test_session", "set_thumbnail",
]
