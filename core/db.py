"""
Penyimpanan lokal (SQLite, stdlib) — job, aset, jadwal upload, key-value (Brand Kit, preferensi).

Satu pengguna, satu file: data/studio.db. Thread-safe (koneksi per panggilan + WAL).
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import config

DATA_DIR = config.BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
ASSET_DIR = DATA_DIR / "assets"
for _d in (DATA_DIR, UPLOAD_DIR, ASSET_DIR):
    _d.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "studio.db"
_INIT_LOCK = threading.Lock()
_initialized = False

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    title TEXT,
    status TEXT NOT NULL,
    progress INTEGER DEFAULT 0,
    stage TEXT DEFAULT '',
    message TEXT DEFAULT '',
    params TEXT DEFAULT '{}',
    result TEXT,
    log TEXT DEFAULT '',
    created REAL,
    updated REAL
);
CREATE INDEX IF NOT EXISTS jobs_created ON jobs(created DESC);
CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    name TEXT,
    path TEXT NOT NULL,
    job_id TEXT,
    meta TEXT DEFAULT '{}',
    created REAL
);
CREATE INDEX IF NOT EXISTS assets_kind ON assets(kind, created DESC);
CREATE TABLE IF NOT EXISTS schedules (
    id TEXT PRIMARY KEY,
    file_path TEXT NOT NULL,
    title TEXT,
    description TEXT,
    platforms TEXT DEFAULT '[]',
    run_at REAL NOT NULL,
    status TEXT DEFAULT 'pending',
    result TEXT,
    created REAL
);
CREATE INDEX IF NOT EXISTS schedules_run ON schedules(status, run_at);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init() -> None:
    global _initialized
    with _INIT_LOCK:
        if _initialized:
            return
        with _connect() as conn:
            conn.executescript(_SCHEMA)
        _initialized = True


@contextmanager
def conn() -> Iterator[sqlite3.Connection]:
    init()
    c = _connect()
    try:
        yield c
        c.commit()
    finally:
        c.close()


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def _loads(raw: Optional[str], default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


# ----------------------------------------------------------------------------- jobs

def job_row(row: sqlite3.Row, with_log: bool = False) -> Dict:
    data = {
        "id": row["id"], "job_id": row["id"], "kind": row["kind"], "title": row["title"] or "",
        "status": row["status"], "progress": row["progress"] or 0, "stage": row["stage"] or "",
        "current_stage": row["stage"] or "", "message": row["message"] or "",
        "params": _loads(row["params"], {}), "result": _loads(row["result"], None),
        "created": row["created"], "updated": row["updated"],
    }
    if with_log:
        data["log"] = row["log"] or ""
    return data


def insert_job(kind: str, title: str, params: Dict) -> str:
    job_id = new_id()
    now = time.time()
    with conn() as c:
        c.execute(
            "INSERT INTO jobs (id, kind, title, status, progress, stage, message, params, created, updated)"
            " VALUES (?, ?, ?, 'queued', 0, 'Antre', 'Menunggu giliran…', ?, ?, ?)",
            (job_id, kind, title[:200], dumps(params), now, now),
        )
    return job_id


def update_job(job_id: str, **fields) -> None:
    if not fields:
        return
    allowed = {"status", "progress", "stage", "message", "result", "title"}
    sets, values = [], []
    for key, value in fields.items():
        if key not in allowed:
            continue
        if key == "result":
            value = dumps(value)
        sets.append(f"{key} = ?")
        values.append(value)
    sets.append("updated = ?")
    values.append(time.time())
    values.append(job_id)
    with conn() as c:
        c.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", values)


def append_log(job_id: str, line: str) -> None:
    stamp = time.strftime("%H:%M:%S")
    with conn() as c:
        c.execute("UPDATE jobs SET log = substr(COALESCE(log, '') || ?, -60000) WHERE id = ?",
                  (f"[{stamp}] {line}\n", job_id))


def get_job(job_id: str, with_log: bool = False) -> Optional[Dict]:
    with conn() as c:
        row = c.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return job_row(row, with_log) if row else None


def list_jobs(limit: int = 100, kind: str = "") -> List[Dict]:
    with conn() as c:
        if kind:
            rows = c.execute("SELECT * FROM jobs WHERE kind = ? ORDER BY created DESC LIMIT ?", (kind, limit)).fetchall()
        else:
            rows = c.execute("SELECT * FROM jobs ORDER BY created DESC LIMIT ?", (limit,)).fetchall()
    return [job_row(r) for r in rows]


def delete_job(job_id: str) -> None:
    with conn() as c:
        c.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        c.execute("DELETE FROM assets WHERE job_id = ?", (job_id,))


def mark_interrupted() -> int:
    """Job yang masih 'running' saat server mati → ditandai agar bisa diulang."""
    with conn() as c:
        cur = c.execute(
            "UPDATE jobs SET status = 'interrupted', message = 'Server berhenti saat job berjalan — klik Ulangi.',"
            " updated = ? WHERE status = 'running'", (time.time(),))
        return cur.rowcount


def queued_job_ids() -> List[str]:
    with conn() as c:
        rows = c.execute("SELECT id FROM jobs WHERE status = 'queued' ORDER BY created").fetchall()
    return [r["id"] for r in rows]


# ----------------------------------------------------------------------------- assets

def add_asset(kind: str, path: str, name: str = "", job_id: str = "", meta: Optional[Dict] = None) -> Dict:
    asset_id = new_id()
    now = time.time()
    with conn() as c:
        c.execute("INSERT INTO assets (id, kind, name, path, job_id, meta, created) VALUES (?, ?, ?, ?, ?, ?, ?)",
                  (asset_id, kind, name or Path(path).name, str(path), job_id, dumps(meta or {}), now))
    return {"id": asset_id, "kind": kind, "name": name or Path(path).name, "path": str(path),
            "job_id": job_id, "meta": meta or {}, "created": now}


def _asset_row(row: sqlite3.Row) -> Dict:
    return {"id": row["id"], "kind": row["kind"], "name": row["name"], "path": row["path"],
            "job_id": row["job_id"], "meta": _loads(row["meta"], {}), "created": row["created"]}


def list_assets(kind: str = "", limit: int = 500) -> List[Dict]:
    with conn() as c:
        if kind:
            rows = c.execute("SELECT * FROM assets WHERE kind = ? ORDER BY created DESC LIMIT ?", (kind, limit)).fetchall()
        else:
            rows = c.execute("SELECT * FROM assets ORDER BY created DESC LIMIT ?", (limit,)).fetchall()
    return [_asset_row(r) for r in rows]


def get_asset(asset_id: str) -> Optional[Dict]:
    with conn() as c:
        row = c.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
    return _asset_row(row) if row else None


def delete_asset(asset_id: str) -> Optional[Dict]:
    asset = get_asset(asset_id)
    if asset:
        with conn() as c:
            c.execute("DELETE FROM assets WHERE id = ?", (asset_id,))
    return asset


# ----------------------------------------------------------------------------- schedules

def _schedule_row(row: sqlite3.Row) -> Dict:
    return {"id": row["id"], "file_path": row["file_path"], "title": row["title"] or "",
            "description": row["description"] or "", "platforms": _loads(row["platforms"], []),
            "run_at": row["run_at"], "status": row["status"], "result": _loads(row["result"], None),
            "created": row["created"]}


def add_schedule(file_path: str, title: str, description: str, platforms: List[str], run_at: float) -> Dict:
    sid = new_id()
    with conn() as c:
        c.execute("INSERT INTO schedules (id, file_path, title, description, platforms, run_at, status, created)"
                  " VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)",
                  (sid, file_path, title, description, dumps(platforms), run_at, time.time()))
    return get_schedule(sid)


def get_schedule(sid: str) -> Optional[Dict]:
    with conn() as c:
        row = c.execute("SELECT * FROM schedules WHERE id = ?", (sid,)).fetchone()
    return _schedule_row(row) if row else None


def list_schedules(limit: int = 500) -> List[Dict]:
    with conn() as c:
        rows = c.execute("SELECT * FROM schedules ORDER BY run_at DESC LIMIT ?", (limit,)).fetchall()
    return [_schedule_row(r) for r in rows]


def due_schedules(now: float) -> List[Dict]:
    with conn() as c:
        rows = c.execute("SELECT * FROM schedules WHERE status = 'pending' AND run_at <= ? ORDER BY run_at", (now,)).fetchall()
    return [_schedule_row(r) for r in rows]


def update_schedule(sid: str, **fields) -> None:
    allowed = {"status", "result", "run_at", "title", "description", "platforms"}
    sets, values = [], []
    for key, value in fields.items():
        if key not in allowed:
            continue
        if key in ("result", "platforms"):
            value = dumps(value)
        sets.append(f"{key} = ?")
        values.append(value)
    if not sets:
        return
    values.append(sid)
    with conn() as c:
        c.execute(f"UPDATE schedules SET {', '.join(sets)} WHERE id = ?", values)


def delete_schedule(sid: str) -> None:
    with conn() as c:
        c.execute("DELETE FROM schedules WHERE id = ?", (sid,))


# ----------------------------------------------------------------------------- key-value

def kv_get(key: str, default: Any = None) -> Any:
    with conn() as c:
        row = c.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    return _loads(row["value"], default) if row else default


def kv_set(key: str, value: Any) -> None:
    with conn() as c:
        c.execute("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                  (key, dumps(value)))
