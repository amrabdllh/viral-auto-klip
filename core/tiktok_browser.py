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
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

import config

COOKIE_DIR = config.BASE_DIR / "data" / "tiktok"
COOKIE_FILE = COOKIE_DIR / "cookies.json"

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


def save_cookies(raw: str) -> Dict[str, Any]:
    cookies = parse_cookies(raw)
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
    return {"configured": bool(cookies), "count": len(cookies), "saved_at": saved_at,
            "expires_at": soonest, "path": str(COOKIE_FILE)}


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


def upload_video(video: Path | str, *, description: str = "", timeout: int = 300,
                 progress=None, mode: Optional[str] = None) -> Dict[str, Any]:
    """Upload satu video ke TikTok Studio (headless). Tidak pernah membuka jendela.

    mode: "draft" (default — simpan ke Draf TikTok Studio) atau "post" (langsung posting).
    """
    mode = str(mode or getattr(config, "TIKTOK_UPLOAD_MODE", "draft") or "draft").strip().lower()
    if mode not in ("draft", "post"):
        mode = "draft"
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
                try:
                    editor.click()
                    page.keyboard.press("Control+A")
                    page.keyboard.press("Backspace")
                    page.keyboard.insert_text(description[:2000])
                except Exception:  # noqa: BLE001 - caption opsional
                    pass

            say("Upload", 60, "Menunggu video selesai diproses…")
            target = SAVE_DRAFT_SELECTORS if mode == "draft" else POST_SELECTORS
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

            say("Upload", 80, "Menyimpan ke Draf…" if mode == "draft" else "Menekan Post…")
            try:
                action_btn.click(timeout=5000)
            except Exception:  # noqa: BLE001
                action_btn.evaluate("el => el.click()")

            # Tunggu konfirmasi: URL pindah ke halaman konten, atau muncul teks sukses.
            uploaded = False
            confirm = r"draf|draft|tersimpan|saved" if mode == "draft" else r"uploaded|berhasil|success"
            for _ in range(60):
                if time.monotonic() > deadline:
                    break
                if "tiktokstudio/content" in page.url or "upload" not in page.url:
                    uploaded = True
                    break
                try:
                    if page.locator(f"text=/{confirm}/i").count():
                        uploaded = True
                        break
                except Exception:  # noqa: BLE001
                    pass
                page.wait_for_timeout(2000)

            if not uploaded:
                if mode == "draft":
                    # Draf kadang tersimpan tanpa memunculkan teks konfirmasi — jangan
                    # gagalkan jadwal; minta pengguna mengecek TikTok Studio → Draf.
                    logger.warning("[tiktok-browser] Draf tidak terkonfirmasi — cek TikTok Studio → Konten → Draf")
                    return {"ok": True, "mode": "draft", "confirmed": False,
                            "privacy": "default", "description": description, "file": str(video)}
                raise TikTokUploadError(
                    "Post ditekan tetapi konfirmasi tidak terlihat. Cek TikTok Studio → Konten.")
            say("Upload", 100, "Draf TikTok tersimpan" if mode == "draft" else "Upload TikTok selesai")
            return {"ok": True, "mode": mode, "privacy": "default", "description": description,
                    "file": str(video)}
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
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"ok": False, "error": "Playwright belum terpasang"}
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
            return {"ok": True, "username": name}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc).splitlines()[0][:200]}
        finally:
            try:
                browser.close()
            except Exception:  # noqa: BLE001
                pass


__all__ = [
    "TikTokUploadError", "COOKIE_FILE", "parse_cookies", "save_cookies",
    "load_cookies", "cookie_status", "clear_cookies", "upload_video", "test_session",
]
