#!/usr/bin/env python3
"""Penjaga publish: cegah upload dobel dan salah akun.

Masalah yang diselesaikan:
1. Klik "Upload" dua kali (atau job di-retry) → dua video identik di platform.
2. Cookies diganti ke akun lain tanpa sadar → klip terunggah ke channel salah.

Data disimpan di `db.kv` (SQLite), bukan file terpisah, supaya satu sumber
dengan job & aset. Kunci memakai hash path + ukuran + mtime, jadi file yang
di-render ulang dianggap baru (isinya berbeda).
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, Optional

from . import db

KEY_PREFIX = "publish"


def _fingerprint(path: str | Path) -> str:
    p = Path(path)
    try:
        st = p.stat()
        raw = f"{p.resolve()}|{st.st_size}|{int(st.st_mtime)}"
    except OSError:
        raw = str(path)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def _key(path: str | Path, platform: str) -> str:
    return f"{KEY_PREFIX}:{platform.lower()}:{_fingerprint(path)}"


def already_uploaded(path: str | Path, platform: str) -> Optional[Dict[str, Any]]:
    """Record upload sebelumnya untuk file+platform ini, atau None."""
    try:
        record = db.kv_get(_key(path, platform), None)
    except Exception:  # noqa: BLE001 - guard tidak boleh mematikan fitur
        return None
    return record if isinstance(record, dict) else None


def record_upload(path: str | Path, platform: str, result: Dict[str, Any]) -> None:
    """Simpan jejak upload supaya percobaan berikutnya bisa dicegah."""
    try:
        db.kv_set(_key(path, platform), {
            "path": str(path),
            "platform": platform.lower(),
            "url": result.get("url") or "",
            "channel": result.get("channel") or "",
            "title": result.get("title") or "",
            "status": result.get("status") or result.get("ok") and "done" or "unknown",
            "at": result.get("at"),
        })
    except Exception:  # noqa: BLE001
        pass


def check_or_raise(path: str | Path, platform: str, *, force: bool = False) -> Optional[Dict[str, Any]]:
    """Kalau sudah pernah diupload dan force=False, kembalikan record lama.

    Pemanggil yang memutuskan: lewati (return record) atau `force=True`.
    """
    if force:
        return None
    return already_uploaded(path, platform)


def verify_channel(expected: str, actual: str) -> Optional[str]:
    """Return pesan error kalau akun berbeda, None kalau cocok/kosong."""
    expected, actual = (expected or "").strip(), (actual or "").strip()
    if expected and actual and expected.casefold() != actual.casefold():
        return f"Akun tidak cocok: diharapkan '{expected}', sesi sekarang '{actual}'"
    return None


__all__ = [
    "already_uploaded", "record_upload", "check_or_raise",
    "verify_channel", "_fingerprint",
]
