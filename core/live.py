"""
Mode Live — klip dari siaran YouTube yang MASIH berlangsung.

Alur:
1. Recorder  : FFmpeg membaca playlist HLS siaran (video + audio terpisah) dan menyimpan potongan
               60 detik (`rec/seg_*.ts`). Playlist YouTube menyimpan ±1 jam ke belakang, jadi
               rekaman bisa dimulai beberapa menit sebelum tombol ditekan. Bila URL kedaluwarsa
               (±6 jam) atau koneksi putus, rekaman disambung lagi tanpa celah.
2. Chat      : yt-dlp membaca chat live (pesan, Super Chat, member, gift) ke file JSON per baris.
3. Jendela   : tiap N menit rekaman, potongan digabung jadi satu video "jendela" lalu dikirim ke
               antrean sebagai job `live_window` (transkrip → AI pilih momen → render). Job induk
               tetap merekam, jadi klip muncul satu per satu selama live berjalan.
4. Sinyal    : lonjakan chat ("WKWKWK", "anjir", Super Chat) dan lonjakan suara streamer dipetakan
               ke waktu video dan dipakai AI sebagai petunjuk momen seru.

Setiap potongan punya jam dinding (dari #EXT-X-PROGRAM-DATE-TIME playlist) agar waktu chat
bisa dicocokkan ke video.
"""
from __future__ import annotations

import csv
import math
import os
import json
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from loguru import logger

import config

SEGMENT_SEC = 60
# Penonton melihat siaran ±5–15 dtk di belakang, lalu butuh beberapa detik untuk mengetik.
CHAT_LAG_SEC = 12.0
MAX_BACKLOG_SEC = 3500  # playlist YouTube menyimpan ±3600 potongan 1 dtk

# Koreksi offset audio antar-rendition HLS. Default MATI: -itsoffset negatif
# membuat timestamp audio mundur dan segment muxer berhenti merotasi (rekaman
# macet di segmen pertama). Nyalakan hanya untuk sumber yang terbukti desync:
#   set LIVE_AUDIO_OFFSET=1 di .env
AUDIO_OFFSET_ENABLED = os.getenv("LIVE_AUDIO_OFFSET", "1").lower() in ("1", "true", "yes")


# ============================================================================ info siaran

def probe(url: str) -> Dict:
    """Metadata siaran via yt-dlp (live_status: is_live / is_upcoming / was_live / not_live)."""
    import yt_dlp
    from .sources import _ydl_opts
    with yt_dlp.YoutubeDL(_ydl_opts({"skip_download": True})) as ydl:
        return ydl.extract_info(url, download=False) or {}


def live_status(url: str) -> str:
    try:
        return str(probe(url).get("live_status") or "")
    except Exception as exc:
        logger.debug(f"Cek status live gagal: {exc}")
        return ""


def _pick_streams(info: Dict, max_height: int = 1080) -> Tuple[str, Optional[str], Dict]:
    """URL playlist HLS terbaik: (video, audio|None, headers)."""
    fmts = [f for f in info.get("formats") or [] if "m3u8" in str(f.get("protocol") or "") and f.get("url")]
    has_v = lambda f: f.get("vcodec") not in (None, "none")
    has_a = lambda f: f.get("acodec") not in (None, "none")
    rank_v = lambda f: (int(f.get("height") or 0), float(f.get("fps") or 0), float(f.get("tbr") or 0))
    videos = sorted([f for f in fmts if has_v(f) and int(f.get("height") or 0) <= max_height], key=rank_v)
    if not videos:
        raise RuntimeError("Siaran ini tidak menyediakan stream HLS yang bisa direkam")
    best = videos[-1]
    headers = dict(best.get("http_headers") or {})
    if has_a(best):
        return best["url"], None, headers
    audios = sorted([f for f in fmts if not has_v(f)], key=lambda f: (float(f.get("abr") or f.get("tbr") or 0), str(f.get("format_id"))))
    muxed = [f for f in videos if has_a(f)]
    if not audios:
        if muxed:
            return muxed[-1]["url"], None, headers
        raise RuntimeError("Stream audio siaran tidak ditemukan")
    return best["url"], audios[-1]["url"], headers


def _playlist_timing(url: str, headers: Dict) -> Tuple[Optional[float], List[float]]:
    """(jam dinding potongan pertama, durasi tiap potongan) dari playlist HLS."""
    pdt, _seq, segs = _fetch_segments(url, headers)
    return pdt, [d for _, d in segs]


def _fetch_segments(url: str, headers: Dict) -> Tuple[Optional[float], int, List[Tuple[str, float]]]:
    """Baca playlist HLS: (PDT potongan pertama, media-sequence, [(url absolut, durasi)])."""
    req = urllib.request.Request(url, headers={k: v for k, v in headers.items() if k.lower() != "accept-encoding"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        text = resp.read().decode("utf-8", "replace")
    pdt: Optional[float] = None
    seq = 0
    segs: List[Tuple[str, float]] = []
    dur = 1.0
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#EXT-X-PROGRAM-DATE-TIME:") and pdt is None:
            try:
                pdt = datetime.fromisoformat(s.split(":", 1)[1].strip().replace("Z", "+00:00")).timestamp()
            except ValueError:
                pdt = None
        elif s.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            try:
                seq = int(s.split(":", 1)[1].strip())
            except ValueError:
                pass
        elif s.startswith("#EXTINF:"):
            try:
                dur = float(s[8:].split(",")[0])
            except ValueError:
                dur = 1.0
        elif s and not s.startswith("#"):
            segs.append((urllib.parse.urljoin(url, s), dur))
            dur = 1.0
    return pdt, seq, segs


def _segment_starts(pdt: float, segs: List[Tuple[str, float]]) -> List[float]:
    out, t = [], pdt
    for _, d in segs:
        out.append(t)
        t += d
    return out


def _iso(wall: float) -> str:
    return datetime.fromtimestamp(wall, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _write_mirror(path: Path, segs: List[Tuple[str, float]], seq0: int, wall0: float) -> None:
    """Tulis playlist HLS lokal yang menunjuk langsung ke segmen remote.

    FFmpeg membacanya seperti playlist live biasa (file ini diperbarui terus),
    tetapi kita yang menentukan segmen pertama — tidak ada lagi balapan
    "playlist bergeser satu segmen" antara pembacaan kita dan pembukaan FFmpeg.
    """
    maxdur = max((d for _, d in segs), default=1.0)
    lines = ["#EXTM3U", "#EXT-X-VERSION:3", f"#EXT-X-TARGETDURATION:{math.ceil(maxdur)}",
             f"#EXT-X-MEDIA-SEQUENCE:{seq0}", f"#EXT-X-PROGRAM-DATE-TIME:{_iso(wall0)}"]
    for u, d in segs:
        lines.extend((f"#EXTINF:{d:.6f},", u))
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def cleanup_orphan_recorders() -> int:
    """Bunuh ffmpeg rekaman yang tertinggal saat server dimatikan paksa.

    Server yang di-`taskkill /F` tidak menjalankan `finally`, jadi subproses
    ffmpeg tetap hidup dan terus mengunduh stream — beberapa proses yatim
    berebut bandwidth dan membuat rekaman baru berjalan 0,2x realtime.
    PID dicatat di <job>/rec/ffmpeg.pid.
    """
    killed = 0
    try:
        roots = (list(Path(config.OUTPUT_DIR).glob("job_*/rec/ffmpeg.pid"))
                 + list(Path(config.OUTPUT_DIR).glob("job_*/chat/chat.pid")))
    except OSError:
        return 0
    for pid_file in roots:
        try:
            pid = int(pid_file.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            continue
        try:
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                                 capture_output=True, text=True, timeout=10).stdout.lower()
        except Exception:
            out = ""
        is_ours = "ffmpeg.exe" in out or "python.exe" in out
        if is_ours:
            try:
                subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=10)
                killed += 1
            except Exception:
                pass
        pid_file.unlink(missing_ok=True)
    if killed:
        logger.warning(f"{killed} proses ffmpeg yatim dari sesi sebelumnya dimatikan")
    return killed


# ============================================================================ recorder

class Recorder:
    """Rekam siaran ke potongan .ts 60 dtk, dengan jam dinding tiap potongan."""

    def __init__(self, url: str, rec_dir: Path, backlog_sec: float = 0, max_height: int = 1080, log=None):
        self.url, self.dir = url, Path(rec_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.backlog = max(0.0, min(float(backlog_sec), MAX_BACKLOG_SEC))
        self.max_height = max_height
        self.log = log or (lambda m: logger.info(m))
        self.segments: List[Dict] = []       # {file, wall, dur}
        self.runs: List[Dict] = []           # {csv, wall_base}
        self.ended = False                   # siaran selesai
        self.error: Optional[str] = None
        self.info: Dict = {}
        self._stop = threading.Event()
        self._proc: Optional[subprocess.Popen] = None
        self._next_number = 0
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._loop, name="live-recorder", daemon=True)

    # ---- publik
    def start(self) -> "Recorder":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._kill()
        self._thread.join(timeout=20)
        self.refresh()

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    def recorded_until(self) -> Optional[float]:
        """Jam dinding akhir potongan terakhir yang sudah SELESAI ditulis."""
        self.refresh()
        with self._lock:
            return (self.segments[-1]["wall"] + self.segments[-1]["dur"]) if self.segments else None

    def recorded_from(self) -> Optional[float]:
        with self._lock:
            return self.segments[0]["wall"] if self.segments else None

    def segments_between(self, wall_from: float, wall_to: float) -> List[Dict]:
        self.refresh()
        with self._lock:
            return [s for s in self.segments if s["wall"] + s["dur"] > wall_from and s["wall"] < wall_to]

    def drop_before(self, wall: float) -> int:
        """Hapus potongan yang sudah tidak dibutuhkan (hemat disk: 1 jam 1080p ±2,5 GB)."""
        removed = 0
        with self._lock:
            for s in self.segments:
                if s["wall"] + s["dur"] < wall and not s.get("deleted"):
                    try:
                        (self.dir / s["file"]).unlink(missing_ok=True)
                        s["deleted"] = True
                        removed += 1
                    except OSError:
                        pass
        return removed

    def refresh(self) -> None:
        """Baca daftar potongan (CSV segment muxer) dari semua sesi FFmpeg."""
        known = {s["file"] for s in self.segments}
        fresh = []
        for run in list(self.runs):
            path = Path(run["csv"])
            if not path.exists():
                continue
            try:
                rows = list(csv.reader(path.open(encoding="utf-8", errors="replace")))
            except OSError:
                continue
            for row in rows:
                if len(row) < 3 or row[0] in known:
                    continue
                try:
                    start, end = float(row[1]), float(row[2])
                except ValueError:
                    continue
                if end - start < 0.5:
                    continue
                fresh.append({"file": row[0], "wall": run["wall_base"] + start, "dur": end - start})
                known.add(row[0])
        if fresh:
            with self._lock:
                self.segments.extend(fresh)
                self.segments.sort(key=lambda s: s["wall"])

    # ---- internal
    def _kill(self) -> None:
        try:
            (self.dir / "ffmpeg.pid").unlink(missing_ok=True)
        except OSError:
            pass
        proc = self._proc
        if proc and proc.poll() is None:
            try:
                proc.stdin.write(b"q")  # FFmpeg menutup potongan terakhir dengan rapi
                proc.stdin.flush()
                proc.wait(timeout=8)
            except Exception:
                proc.kill()

    def _loop(self) -> None:
        backlog, failures = self.backlog, 0
        while not self._stop.is_set():
            try:
                info = probe(self.url)
                self.info = {k: info.get(k) for k in ("title", "channel", "uploader", "live_status", "release_timestamp",
                                                      "concurrent_view_count", "webpage_url")}
                if info.get("live_status") != "is_live":
                    self.ended = True
                    self.log(f"Siaran tidak sedang live (status: {info.get('live_status') or '-'}) — rekaman berhenti.")
                    break
                v_url, a_url, headers = _pick_streams(info, self.max_height)
                self._run_ffmpeg(v_url, a_url, headers, backlog)
                failures = 0
            except Exception as exc:
                failures += 1
                self.log(f"Rekaman terganggu ({failures}x): {str(exc)[:200]}")
                if failures >= 6:
                    self.error = str(exc)[:300]
                    break
                self._stop.wait(min(60, 5 * failures))
            if self._stop.is_set():
                break
            # FFmpeg berhenti (URL kedaluwarsa / jaringan / siaran selesai): sambung dari titik terakhir
            last = self.recorded_until()
            backlog = min(MAX_BACKLOG_SEC, max(4.0, time.time() - last + 6)) if last else 30.0
        self.refresh()

    # ---- playlist cermin: titik mulai deterministik, tanpa balapan segmen
    def _mirror_inputs(self, v_url: str, a_url: Optional[str], headers: Dict, backlog: float) -> Optional[Dict]:
        """Tulis playlist HLS lokal dari segmen yang KITA pilih.

        Masalah lama: `-live_start_index -n` dihitung per playlist, tetapi playlist
        live bergeser tiap detik. Antara pembacaan kita dan pembukaan FFmpeg, satu
        playlist bisa sudah bergeser satu segmen (1 dtk) sementara yang lain belum —
        hasilnya audio bergeser ±1 dtk dan offset yang kita hitung justru menambah
        kesalahan. Dengan playlist cermin, segmen pertama yang dibaca FFmpeg adalah
        segmen yang sama yang kita pakai untuk menghitung offset.
        """
        try:
            v_pdt, v_seq, v_segs = _fetch_segments(v_url, headers)
            a_pdt, a_seq, a_segs = _fetch_segments(a_url, headers) if a_url else (None, 0, [])
            if not v_segs or v_pdt is None:
                return None
            target = time.time() - max(0.0, backlog)

            def pick(pdt: float, segs: List[Tuple[str, float]]) -> Tuple[int, float]:
                starts = _segment_starts(pdt, segs)
                for i, start in enumerate(starts):
                    if start + segs[i][1] > target:
                        return i, start
                return max(0, len(segs) - 1), starts[-1]

            vi, v_wall0 = pick(v_pdt, v_segs)
            ai, a_wall0 = pick(a_pdt, a_segs) if (a_segs and a_pdt is not None) else (0, v_wall0)
            v_path, a_path = self.dir / "v_live.m3u8", self.dir / "a_live.m3u8"
            _write_mirror(v_path, v_segs[vi:], v_seq + vi, v_wall0)
            state = {"v": {"path": str(v_path), "url": v_url, "headers": headers,
                           "segs": list(v_segs[vi:]), "seq": v_seq + vi, "wall0": v_wall0},
                     "a": None}
            if a_url and a_segs:
                _write_mirror(a_path, a_segs[ai:], a_seq + ai, a_wall0)
                state["a"] = {"path": str(a_path), "url": a_url, "headers": headers,
                              "segs": list(a_segs[ai:]), "seq": a_seq + ai, "wall0": a_wall0}
            return {"v": v_wall0, "a": a_wall0, "state": state}
        except Exception as exc:  # noqa: BLE001 - fallback ke mode -live_start_index
            self.log(f"Playlist cermin gagal ({str(exc)[:140]}); memakai mode lama")
            return None

    @staticmethod
    def _refresh_mirrors(items: List[Dict], stop: threading.Event) -> None:
        """Tambahkan segmen baru ke playlist cermin selama FFmpeg membaca.

        Harus APPEND, bukan tulis-ulang: di Windows FFmpeg memegang file
        playlist yang sedang dibaca, sehingga `os.replace` gagal dan playlist
        cermin tidak pernah bertambah — FFmpeg berhenti di ujung playlist awal.
        """
        while not stop.is_set():
            for item in items:
                try:
                    _pdt, seq, segs = _fetch_segments(item["url"], item["headers"])
                    last = item["seq"] + len(item["segs"]) - 1
                    fresh = [(u, d) for j, (u, d) in enumerate(segs) if seq + j > last]
                    if fresh:
                        if seq > last + 1:
                            logger.warning(f"Playlist cermin kehilangan {seq - last - 1} segmen")
                        chunk = "".join(f"#EXTINF:{d:.6f},\n{u}\n" for u, d in fresh)
                        with open(item["path"], "a", encoding="utf-8") as fh:
                            fh.write(chunk)
                            fh.flush()
                        item["segs"].extend(fresh)
                except Exception:  # noqa: BLE001 - dicoba lagi 2 dtk kemudian
                    pass
            stop.wait(2)

    def _run_ffmpeg(self, v_url: str, a_url: Optional[str], headers: Dict, backlog: float) -> None:
        run_no = len(self.runs)
        csv_path = self.dir / f"list_{run_no:02d}.csv"
        hdr = "".join(f"{k}: {v}\r\n" for k, v in headers.items() if k.lower() in ("user-agent", "referer", "origin", "cookie"))
        mirror = self._mirror_inputs(v_url, a_url, headers, backlog) if v_url.startswith("http") else None
        stop_refresh = threading.Event()
        if mirror:
            v_wall0 = float(mirror["v"])
            offset = 0.0
            if a_url and mirror["state"]["a"] and AUDIO_OFFSET_ENABLED:
                offset = float(mirror["a"]) - float(mirror["v"])
            def inp(path: str):
                # Tanpa -live_start_index, demuxer HLS mulai di "live edge"
                # (3 segmen dari akhir) dan titik mulai kita diabaikan; 0 = mulai
                # dari segmen pertama playlist cermin — segmen yang kita pilih.
                args = ["-live_start_index", "0",
                        "-protocol_whitelist", "file,http,https,tcp,tls,crypto",
                        "-allowed_extensions", "ALL"]
                return args + ["-f", "hls", "-i", path]
            cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *inp(mirror["state"]["v"]["path"])]
            if a_url:
                if abs(offset) > 0.05:
                    self.log(f"Offset audio {offset:+.2f} dtk dikoreksi")
                    cmd += ["-itsoffset", f"{offset:.3f}"]
                cmd += [*inp(mirror["state"]["a"]["path"]), "-map", "0:v:0", "-map", "1:a:0"]
            else:
                cmd += ["-map", "0:v:0", "-map", "0:a:0?"]
            items = [mirror["state"]["v"]] + ([mirror["state"]["a"]] if a_url and mirror["state"]["a"] else [])
            threading.Thread(target=self._refresh_mirrors, args=(items, stop_refresh),
                             name="live-mirror", daemon=True).start()
            self.log("Merekam dari playlist cermin (titik mulai pasti)")
        else:
            # Fallback: perilaku lama dengan -live_start_index (bisa meleset 1 segmen).
            try:
                _, durs = _playlist_timing(v_url, headers)
                avg = (sum(durs) / len(durs)) if durs else 5.0
            except Exception:
                avg = 5.0
            n = max(3, int(round(backlog / max(avg, 1.0))))
            self.log(f"Backlog {backlog:.0f} dtk, segmen rata-rata {avg:.1f} dtk → mulai {n} segmen ke belakang")
            def inp(u):
                args = ["-live_start_index", str(-n)]
                if hdr:
                    args += ["-headers", hdr]
                return args + ["-i", u]
            cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *inp(v_url)]
            if a_url:
                cmd += [*inp(a_url), "-map", "0:v:0", "-map", "1:a:0"]
            else:
                cmd += ["-map", "0:v:0", "-map", "0:a:0?"]
            v_wall0 = time.time() - n
            try:
                pdt, durs = _playlist_timing(v_url, headers)
                if pdt and durs:
                    idx = max(0, len(durs) - n)
                    v_wall0 = pdt + sum(durs[:idx])
            except Exception as exc:
                self.log(f"Jam playlist tidak terbaca, pakai perkiraan: {exc}")
        cmd += ["-c", "copy", "-f", "segment", "-segment_time", str(SEGMENT_SEC), "-reset_timestamps", "1",
                "-segment_start_number", str(self._next_number), "-segment_list", str(csv_path),
                "-segment_list_type", "csv", str(self.dir / "seg_%05d.ts")]
        err_path = self.dir / f"ffmpeg_{run_no:02d}.log"
        err = open(err_path, "wb")
        self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=err)
        try:
            (self.dir / "ffmpeg.pid").write_text(str(self._proc.pid), encoding="utf-8")
        except OSError:
            pass
        wall_base = v_wall0
        self.runs.append({"csv": str(csv_path), "wall_base": wall_base})
        if mirror:
            self.log(f"Merekam siaran (mulai {max(0, int((time.time() - wall_base) // 60))} mnt ke belakang)…")
        else:
            self.log(f"Merekam siaran (mulai {int(n // 60)} mnt {int(n % 60)} dtk ke belakang)…")
        try:
            while self._proc.poll() is None and not self._stop.is_set():
                self._stop.wait(2)
                self.refresh()
        finally:
            stop_refresh.set()
            if self._stop.is_set():
                self._kill()
            err.close()
            self.refresh()
            with self._lock:
                nums = [int(m.group(1)) for s in self.segments if (m := re.search(r"(\d+)", s["file"]))]
            self._next_number = (max(nums) + 1) if nums else self._next_number
            code = self._proc.returncode
            if code not in (0, None) and not self._stop.is_set():
                tail = err_path.read_text(encoding="utf-8", errors="replace")[-300:].strip()
                self.log(f"FFmpeg rekaman keluar (kode {code}). {tail}")


def build_window(rec_dir: Path, segments: List[Dict], out_path: Path) -> Tuple[float, float]:
    """Gabung potongan jadi satu MP4 tanpa encode ulang. Return (jam dinding awal, durasi)."""
    segs = [s for s in segments if not s.get("deleted") and (Path(rec_dir) / s["file"]).exists()]
    if not segs:
        raise RuntimeError("Belum ada potongan rekaman untuk jendela ini")
    lst = out_path.with_suffix(".txt")
    lst.write_text("".join(f"file '{(Path(rec_dir) / s['file']).resolve().as_posix()}'\n" for s in segs), encoding="utf-8")
    try:
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                        "-c", "copy", "-bsf:a", "aac_adtstoasc", "-movflags", "+faststart", str(out_path)],
                       check=True, capture_output=True)
    finally:
        lst.unlink(missing_ok=True)
    return segs[0]["wall"], sum(s["dur"] for s in segs)


# ============================================================================ chat

_HYPE_RE = re.compile(
    r"(wk(wk)+|kwk|haha|hehe|wkwk|awok|ngakak|kocak|lucu|anj(ir|ay|g)?|njir|jir\b|gila|gokil|parah|mantap|"
    r"gg\b|ggwp|wtf|lol|lmao|omg|asu|bjir|buset|anjrit|keren|epic|clip|klip|😂|🤣|🔥|😱|💀|😭|!!+|\?\?+)",
    re.IGNORECASE,
)


def _runs_text(msg: Dict) -> str:
    out = []
    for r in (msg or {}).get("runs") or []:
        if "text" in r:
            out.append(r["text"])
        elif "emoji" in r:
            e = r["emoji"]
            sc = (e.get("shortcuts") or [""])[0]
            out.append(e.get("emojiId") if len(str(e.get("emojiId", ""))) <= 2 else sc)
    return "".join(out).strip()


def parse_chat_line(line: str) -> List[Dict]:
    """Satu baris JSON chat yt-dlp → event {ts, type, text, author, amount}."""
    try:
        data = json.loads(line)
    except (ValueError, TypeError):
        return []
    actions = (data.get("replayChatItemAction") or {}).get("actions") or [data]
    events, last_ts = [], None
    for act in actions:
        item = (act.get("addChatItemAction") or {}).get("item") or {}
        for kind, r in item.items():
            ts = r.get("timestampUsec")
            ts = int(ts) / 1e6 if ts else last_ts
            if ts:
                last_ts = ts
            author = ((r.get("authorName") or {}).get("simpleText") or "").strip()
            if kind == "liveChatTextMessageRenderer":
                events.append({"ts": ts, "type": "msg", "text": _runs_text(r.get("message")), "author": author})
            elif kind in ("liveChatPaidMessageRenderer", "liveChatPaidStickerRenderer"):
                amount = ((r.get("purchaseAmountText") or {}).get("simpleText") or "").strip()
                events.append({"ts": ts, "type": "paid", "text": _runs_text(r.get("message")), "author": author, "amount": amount})
            elif kind in ("liveChatMembershipItemRenderer", "liveChatSponsorshipsGiftPurchaseAnnouncementRenderer"):
                events.append({"ts": ts, "type": "member", "text": "", "author": author})
            elif kind == "giftMessageViewModel":
                events.append({"ts": ts, "type": "gift", "text": "", "author": author})
    return [e for e in events if e["ts"]]


class ChatReader:
    """Baca chat live lewat yt-dlp (subproses) ke daftar event."""

    def __init__(self, url: str, chat_dir: Path, log=None):
        self.url, self.dir = url, Path(chat_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.log = log or (lambda m: logger.info(m))
        self.events: List[Dict] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._proc: Optional[subprocess.Popen] = None
        self._thread = threading.Thread(target=self._loop, name="live-chat", daemon=True)

    def start(self) -> "ChatReader":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        try:
            (self.dir / "chat.pid").unlink(missing_ok=True)
        except OSError:
            pass
        if self._proc and self._proc.poll() is None:
            self._proc.kill()
        self._thread.join(timeout=10)

    def between(self, wall_from: float, wall_to: float) -> List[Dict]:
        with self._lock:
            return [e for e in self.events if wall_from <= e["ts"] < wall_to]

    def count(self) -> int:
        with self._lock:
            return len(self.events)

    def _loop(self) -> None:
        attempt = 0
        while not self._stop.is_set() and attempt < 20:
            attempt += 1
            stem = self.dir / f"chat_{attempt:02d}"
            cmd = [sys.executable, "-m", "yt_dlp", "--skip-download", "--write-subs", "--sub-langs", "live_chat",
                   "--no-part", "--no-warnings", "-o", str(stem), self.url]
            cookies = str(getattr(config, "YTDLP_COOKIES", "") or "")
            if cookies and Path(cookies).exists():
                cmd[3:3] = ["--cookies", cookies]
            try:
                self._proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                try:
                    (self.dir / "chat.pid").write_text(str(self._proc.pid), encoding="utf-8")
                except OSError:
                    pass
            except OSError as exc:
                self.log(f"Chat live tidak bisa dibaca: {exc}")
                return
            target = Path(str(stem) + ".live_chat.json")
            offset, buf = 0, b""
            while not self._stop.is_set():
                running = self._proc.poll() is None
                if target.exists():
                    with open(target, "rb") as fh:
                        fh.seek(offset)
                        chunk = fh.read()
                    offset += len(chunk)
                    buf += chunk
                    *lines, buf = buf.split(b"\n")
                    new = [e for ln in lines if ln.strip() for e in parse_chat_line(ln.decode("utf-8", "replace"))]
                    if new:
                        with self._lock:
                            self.events.extend(new)
                if not running:
                    break
                self._stop.wait(3)
            if self._stop.is_set():
                break
            self._stop.wait(10)  # yt-dlp berhenti (siaran selesai / gangguan): coba lagi sebentar lagi


# ============================================================================ sinyal momen

def chat_signals(events: List[Dict], wall_from: float, wall_to: float, lag: float = CHAT_LAG_SEC,
                 bin_sec: float = 5.0, limit: int = 12) -> List[Dict]:
    """Lonjakan chat → petunjuk momen (waktu relatif terhadap wall_from, sudah dikoreksi jeda penonton)."""
    import numpy as np
    span = wall_to - wall_from
    if span <= 30 or not events:
        return []
    n = int(span // bin_sec) + 1
    weight = np.zeros(n)
    texts: List[List[str]] = [[] for _ in range(n)]
    paid: List[List[str]] = [[] for _ in range(n)]
    for e in events:
        t = e["ts"] - lag - wall_from
        if not (0 <= t < span):
            continue
        i = int(t // bin_sec)
        w = {"msg": 1.0, "paid": 6.0, "member": 3.0, "gift": 1.5}.get(e["type"], 1.0)
        if e.get("text") and _HYPE_RE.search(e["text"]):
            w += 1.0
        weight[i] += w
        if e.get("text"):
            texts[i].append(e["text"])
        if e["type"] == "paid":
            paid[i].append(f"{e.get('amount', '')} {e.get('text', '')}".strip())
    if weight.sum() < 15:
        return []
    smooth = np.convolve(weight, np.ones(3) / 3, mode="same")
    k = max(3, int(240 // bin_sec))  # latar ±2 menit
    pad = np.pad(smooth, (k, k), mode="edge")
    base = np.array([np.median(pad[i:i + 2 * k + 1]) for i in range(n)])
    ratio = smooth / (base + 0.6)
    floor = max(4.0, float(np.percentile(smooth, 60)) + 1.5)
    order = np.argsort(-ratio)
    picks: List[int] = []
    for i in order:
        if ratio[i] < 1.7 or smooth[i] < floor / 3:
            break
        if all(abs(int(i) - p) * bin_sec >= 40 for p in picks):
            picks.append(int(i))
        if len(picks) >= limit:
            break
    out = []
    for i in sorted(picks):
        lo, hi = max(0, i - 1), min(n, i + 2)
        sample = [t for b in range(lo, hi) for t in texts[b]]
        counts: Dict[str, int] = {}
        for t in sample:
            key = re.sub(r"(.)\1{2,}", r"\1\1", t.lower().strip())[:40]
            if key:
                counts[key] = counts.get(key, 0) + 1
        top = [k for k, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:4]]
        sc = [p for b in range(lo, hi) for p in paid[b]][:2]
        out.append({"t": round(i * bin_sec + bin_sec / 2, 1), "kind": "chat", "strength": round(float(ratio[i]), 2),
                    "count": int(sum(len(texts[b]) for b in range(lo, hi))), "samples": top, "superchat": sc})
    return out


def audio_signals(video: str, limit: int = 10) -> List[Dict]:
    """Lonjakan suara (teriakan/tawa streamer, ledakan game) relatif terhadap latar ±40 dtk."""
    import numpy as np
    from . import ffx
    try:
        energy = ffx.audio_energy(video, hop=0.5)
    except Exception:
        return []
    if len(energy) < 120:
        return []
    t = np.array([e[0] for e in energy])
    v = np.array([e[1] for e in energy])
    def avg(x, k):
        k = max(1, min(int(k), len(x)))
        return np.convolve(x, np.ones(k), mode="same") / np.convolve(np.ones_like(x), np.ones(k), mode="same")
    excite = avg(v, 4) / (avg(v, 160) + 1e-6)
    picks: List[int] = []
    for i in np.argsort(-excite):
        if excite[i] < 1.9:
            break
        if all(abs(t[i] - t[p]) >= 40 for p in picks):
            picks.append(int(i))
        if len(picks) >= limit:
            break
    return [{"t": round(float(t[i]), 1), "kind": "audio", "strength": round(float(excite[i]), 2)} for i in sorted(picks)]


def describe_signal(h: Dict) -> str:
    if h["kind"] == "chat":
        extra = f" Super Chat: {'; '.join(h['superchat'])}." if h.get("superchat") else ""
        samples = ", ".join(f'"{s}"' for s in h.get("samples", [])[:4])
        return f"chat penonton melonjak {h['strength']}× dari biasanya ({h.get('count', 0)} pesan) — contoh: {samples}.{extra}"
    return f"suara streamer/game melonjak {h['strength']}× (teriak/tertawa/aksi heboh)"


# ============================================================================ state antar jendela

def load_state(job_dir: Path) -> Dict:
    p = Path(job_dir) / "live_state.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"clips": [], "windows": []}


def save_state(job_dir: Path, state: Dict) -> None:
    p = Path(job_dir) / "live_state.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


_STATE_LOCK = threading.Lock()


def update_state(job_dir: Path, fn) -> Dict:
    with _STATE_LOCK:
        state = load_state(job_dir)
        fn(state)
        save_state(job_dir, state)
        return state


# ============================================================================ job

from . import db  # noqa: E402
from .chat_spikes import chat_spike_moments, merge_hints  # noqa: E402
from .jobs import JobCancelled, JobContext, handler, submit  # noqa: E402

STOP_REQUESTS: set = set()   # "Selesai rekam": proses sisa rekaman lalu berhenti dengan rapi
RUNNING: Dict[str, Dict] = {}  # job_id → ringkasan status

# parameter klip yang diteruskan ke tiap jendela
_PASS_KEYS = ("caption_style", "edit_style", "language", "whisper_device", "whisper_model", "ai_provider", "ai_model",
              "min_score", "min_duration", "max_duration", "target_platform", "watermark_text", "brand_kit",
              "enable_captions", "hook_card", "use_llm", "audio_enhance", "layout", "video_mode", "output_type",
              "cold_open", "sound_design", "caption_fx", "audio_master", "jump_cuts", "dynamic_zoom", "progress_bar",
              "sfx", "end_cta", "color_grade", "enable_face_reframe", "speaker_cam", "broll", "shake", "freeze",
              "lower_third", "censor", "speaker_names", "campaign", "preset", "subtitle_provider", "intro_voice")


def _clock(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M")


def request_stop(job_id: str) -> bool:
    STOP_REQUESTS.add(job_id)
    return job_id in RUNNING


@handler("live_clip", background=True)
def live_clip(ctx: JobContext) -> Dict:
    """Job induk: rekam siaran + chat, potong per jendela, kirim tiap jendela ke antrean."""
    p = ctx.params
    url = (p.get("url") or "").strip()
    if not url:
        raise ValueError("Masukkan link siaran live YouTube")
    ctx.progress(1, "Cek siaran", "Membaca info siaran…")
    info = probe(url)
    status = info.get("live_status")
    if status == "is_upcoming":
        raise RuntimeError("Siaran belum dimulai. Jalankan lagi setelah streamer mulai live.")
    if status != "is_live":
        raise RuntimeError("Video ini tidak sedang live. Untuk siaran yang sudah selesai, pakai Analisis AI biasa.")
    channel = info.get("channel") or info.get("uploader") or "Live"
    title = re.sub(r"\s+\d{4}-\d{2}-\d{2} \d{2}:\d{2}$", "", str(info.get("title") or "")).strip()
    db.update_job(ctx.id, title=f"Live: {channel} · {title[:70]}")

    window = max(3, min(int(p.get("window_min", 10)), 30)) * 60
    backlog = max(0, min(int(p.get("backlog_min", 20)), 58)) * 60
    max_hours = max(0.5, min(float(p.get("max_hours", 6)), 12))
    overlap = 90.0
    started = time.time()

    rec = Recorder(url, ctx.dir / "rec", backlog_sec=backlog, max_height=int(p.get("max_height", 1080)), log=ctx.log).start()
    chat = ChatReader(url, ctx.dir / "chat", log=ctx.log).start() if p.get("use_chat", True) else None
    (ctx.dir / "windows").mkdir(exist_ok=True)
    state = update_state(ctx.dir, lambda s: s.update({"url": url, "channel": channel, "title": title, "started": started}))
    RUNNING[ctx.id] = {"channel": channel}
    cursor: Optional[float] = None
    n_win, children = len(state.get("windows", [])), []
    last_msg_at = 0.0
    ctx.log(f"Mode Live: {channel} — {title}")
    ctx.log(f"Jendela {window // 60} menit, mulai {backlog // 60} menit ke belakang, chat {'aktif' if chat else 'mati'}")

    def flush(final: bool) -> bool:
        """Kirim satu jendela bila rekaman baru sudah cukup panjang. Return True bila terkirim."""
        nonlocal cursor, n_win
        until, first = rec.recorded_until(), rec.recorded_from()
        if until is None or first is None:
            return False
        if cursor is None:
            cursor = first
        ready = until - cursor
        if ready < (45 if final else window):
            return False
        w_from = max(first, cursor - (overlap if n_win else 0))
        w_to = until if final and ready <= window * 1.5 else cursor + window
        segs = rec.segments_between(w_from, w_to)
        if not segs:
            return False
        n_win += 1
        out = ctx.dir / "windows" / f"window_{n_win:03d}.mp4"
        wall0, dur = build_window(rec.dir, segs, out)
        seg_end = segs[-1]["wall"] + segs[-1]["dur"]
        events = chat.between(wall0 - 5, wall0 + dur + CHAT_LAG_SEC + 10) if chat else []
        chat_file = out.with_suffix(".chat.json")
        chat_file.write_text(json.dumps(events, ensure_ascii=False), encoding="utf-8")
        params = {k: p[k] for k in _PASS_KEYS if k in p}
        params.update({"path": str(out), "parent": ctx.id, "parent_dir": str(ctx.dir), "window": n_win,
                       "wall_start": wall0, "duration": dur, "new_from": max(0.0, cursor - wall0),
                       "final": final and seg_end >= until - 1, "chat_file": str(chat_file),
                       "num_clips": int(p.get("clips_per_window", 3)), "source_url": url, "channel": channel})
        child = submit("live_window", params, title=f"Live jendela {n_win} · {_clock(wall0)}–{_clock(wall0 + dur)}")
        children.append(child["id"])
        update_state(ctx.dir, lambda s: s.setdefault("windows", []).append(
            {"n": n_win, "job": child["id"], "wall_start": wall0, "duration": dur, "chat": len(events)}))
        ctx.log(f"Jendela {n_win} ({_clock(wall0)}–{_clock(wall0 + dur)}, {len(events)} chat) → antrean")
        cursor = seg_end
        rec.drop_before(cursor - overlap - 5)
        return True

    try:
        while True:
            if ctx.cancel_requested():
                raise JobCancelled()
            stop_now = ctx.id in STOP_REQUESTS or time.time() - started > max_hours * 3600
            ended = rec.ended or not rec.alive or stop_now
            if rec.error:
                raise RuntimeError(f"Rekaman gagal: {rec.error}")
            while flush(final=False):
                pass
            if ended:
                if rec.alive:
                    rec.stop()
                while flush(final=True):
                    pass
                break
            if time.time() - last_msg_at >= 4:
                last_msg_at = time.time()
                until, first = rec.recorded_until(), rec.recorded_from()
                st = load_state(ctx.dir)
                length = (until - first) if until and first else 0
                behind = (time.time() - until) if until else 0
                msg = (f"Merekam {int(length // 60)} mnt · {n_win} jendela · {len(st.get('clips', []))} klip"
                       + (f" · {chat.count()} chat" if chat else "")
                       + (f" · mengejar siaran ({int(behind // 60)} mnt di belakang)" if behind > 90 else ""))
                db.update_job(ctx.id, progress=min(95, 5 + n_win * 5), stage="Merekam live", message=msg,
                              result={"clips": st.get("clips", []), "live": True, "channel": channel, "title": title,
                                      "windows": n_win, "recorded_sec": round(length), "chat": chat.count() if chat else 0})
            time.sleep(2)
    finally:
        STOP_REQUESTS.discard(ctx.id)
        RUNNING.pop(ctx.id, None)
        try:
            rec.stop()
        finally:
            if chat:
                chat.stop()

    # tunggu jendela yang masih di antrean supaya hasil akhir lengkap
    db.update_job(ctx.id, stage="Menyelesaikan", message=f"Rekaman selesai — memproses {len(children)} jendela terakhir…")
    deadline = time.time() + 3 * 3600
    while time.time() < deadline:
        pending = [c for c in children if (db.get_job(c) or {}).get("status") in ("queued", "running")]
        if not pending:
            break
        if ctx.cancel_requested():
            raise JobCancelled()
        st = load_state(ctx.dir)
        db.update_job(ctx.id, message=f"Memproses {len(pending)} jendela terakhir · {len(st.get('clips', []))} klip",
                      result={"clips": st.get("clips", []), "live": True, "channel": channel, "title": title})
        time.sleep(5)
    for f in (ctx.dir / "rec").glob("seg_*.ts"):
        f.unlink(missing_ok=True)
    st = load_state(ctx.dir)
    clips = st.get("clips", [])
    return {"_message": f"Live selesai: {len(clips)} klip dari {n_win} jendela", "clips": clips, "total_clips": len(clips),
            "live": True, "channel": channel, "title": title, "windows": n_win,
            "chat": chat.count() if chat else 0}


def _gameplay_from_cam(cam: List[float]) -> List[float]:
    """Area gameplay = frame dikurangi sisi tempat facecam berada."""
    x, y, w, h = cam
    cx, cy = x + w / 2, y + h / 2
    if cx > 0.55:            # cam di kanan
        return [0.0, 0.0, round(max(0.55, min(0.79, x)), 4), 1.0]
    if cx < 0.45:            # cam di kiri
        return [round(min(0.8, x + w), 4), 0.0, round(max(0.2, 1.0 - (x + w)), 4), 1.0]
    if cy > 0.5:             # cam di bawah
        return [0.0, 0.0, 1.0, round(max(0.2, y), 4)]
    return [0.0, round(min(0.8, y + h), 4), 1.0, round(max(0.2, 1.0 - (y + h)), 4)]


def _render_campaign_window(video: str, picked: List[Dict], out_dir: Path,
                            p: Dict, wall0: float) -> List[Dict]:
    """Render jendela live dengan EDITING PENUH + banner + outro.

    Semua efek yang dipakai klip biasa (jump cut, zoom, cold open, caption
    animasi, sound design, color grade) dikerjakan `process_clips`. Setelah itu
    banner dan outro ditempel. Jadi satu-satunya beda campaign adalah
    split + banner + outro — sesuai permintaan.
    """
    from . import campaign, ffx, tools
    from .clipper import detect_stream_layout, process_clips

    preset_path = Path(p.get("preset") or getattr(config, "CAMPAIGN_DEFAULT_PRESET", "")
                       or "motion-klip-gtavi-split.json")
    if not preset_path.is_absolute():
        preset_path = Path(config.BASE_DIR) / "presets" / preset_path.name
    preset = campaign.load_preset(preset_path)
    preset_tags = [t for t in str(preset.get("hashtags") or "").split() if t]
    overrides = {k: p[k] for k in ("subtitle_provider", "intro_voice", "layout", "subtitle_position") if p.get(k)}
    settings = campaign.campaign_settings(preset, overrides)

    layout = None
    if settings["layout"] == "split":
        cam = game = face = None
        try:
            det = detect_stream_layout(video, picked)
        except Exception as exc:  # noqa: BLE001
            det = None
            logger.warning(f"Deteksi facecam campaign dilewati: {str(exc)[:160]}")
        if det:
            # Auto-detect menentukan perlu split atau tidak + kotak yang
            # memusatkan wajah; preset hanya cadangan bila deteksi gagal.
            cam, game = det["cam_box"], _gameplay_from_cam(det["cam_box"])
            face = [det["face"][0], det["face"][1]]
            logger.info(f"Campaign live: auto {cam} (wajah {face[0]:.3f},{face[1]:.3f})")
        else:
            cam, game = settings.get("facecam_rect"), settings.get("gameplay_rect")
        if cam and game:
            layout = {"cam_box": list(cam), "game_box": list(game),
                      "cam_ratio": settings["facecam_height"] / 1920.0,
                      "face": face, "caption_y": campaign.caption_margin(settings, preset),
                      "caption_font_size": campaign.caption_font_size(settings)}

    opts = tools.render_options(p)
    opts["stream_layout"] = layout
    if layout is None:
        opts["layout"] = "off"  # preset tanpa split: jangan auto-deteksi facecam
    opts["enable_captions"] = bool(p.get("enable_captions", True))
    from . import editing
    prev_fps = editing.OUTPUT_FPS
    editing.OUTPUT_FPS = max(24, min(60, int(settings.get("fps") or 60)))
    try:
        ctx_clips = process_clips(video, picked, out_dir, **opts)
    finally:
        editing.OUTPUT_FPS = prev_fps

    clips: List[Dict] = []
    for index, clip in enumerate(ctx_clips):
        src = Path(clip["file_path"])
        final = src.with_name(f"{src.stem}_campaign.mp4")
        try:
            campaign.finish_with_banner_and_outro(src, preset, final)
        except Exception as exc:  # noqa: BLE001 - jangan buang klip yang sudah diedit
            logger.warning(f"Banner/outro gagal ({str(exc)[:160]}); memakai klip tanpa banner")
            final = src
        if final != src:
            src.unlink(missing_ok=True)  # hanya file campaign yang disimpan
        row = dict(clip)
        row["file_path"] = str(final)
        row["campaign"] = True
        # Hashtag preset campaign jadi default; field Hashtag manual tetap menang.
        if preset_tags and not p.get("hashtags_override"):
            row["hashtags"] = preset_tags
        start = float(row.get("start") or (picked[index]["start"] if index < len(picked) else 0))
        end = float(row.get("end") or (picked[index]["end"] if index < len(picked) else 0))
        row["start"], row["end"] = start, end
        row["wall_span"] = [wall0 + start, wall0 + end]
        clips.append(row)
    return clips


@handler("live_window")
def live_window(ctx: JobContext) -> Dict:
    """Satu jendela rekaman live: transkrip → sinyal chat/suara → AI pilih momen → render klip."""
    from . import analyzer, tools
    from .clipper import process_clips
    from .utils import get_video_info
    p = ctx.params
    video = p["path"]
    parent_dir = Path(p["parent_dir"])
    if not Path(video).exists():
        raise FileNotFoundError("File jendela rekaman sudah terhapus")
    config.MIN_VIRAL_SCORE = max(0.0, min(float(p.get("min_score", 45)), 100.0))
    config.MIN_CLIP_DURATION = max(5, int(p.get("min_duration", 25)))
    config.MAX_CLIP_DURATION = max(config.MIN_CLIP_DURATION + 5, int(p.get("max_duration", 50)))
    num = max(1, min(int(p.get("num_clips", 3)), 10))
    config.MAX_CLIPS_PER_VIDEO = num + 3  # cadangan: sebagian bisa gugur karena tumpang tindih jendela lain
    if p.get("watermark_text"):
        config.WATERMARK_TEXT = p["watermark_text"]
    llm = tools.apply_ai_params(p) and p.get("use_llm", True)
    info = get_video_info(video)
    dur = float(info.get("duration") or p.get("duration") or 0)
    transcript, tinfo = tools.transcribe(ctx, video, p, 10)
    if p.get("streamer_only", True):
        from .transcriber import drop_foreign_segments
        before = len(transcript)
        transcript = drop_foreign_segments(transcript, str(p.get("language") or config.WHISPER_LANGUAGE or "id"))
        if before > len(transcript):
            ctx.log(f"Subtitle streamer saja: {before - len(transcript)} segmen dialog game/bahasa lain dibuang")
        if llm and transcript:
            try:
                kept = tools.filter_streamer_segments(
                    transcript, context=str(p.get("channel") or p.get("url") or ""))
                if len(kept) < len(transcript):
                    ctx.log(f"Dialog game dibuang LLM: {len(transcript) - len(kept)} segmen")
                transcript = kept
            except Exception:  # noqa: BLE001 - filter opsional
                pass
    p["_lower_third"] = tools.build_lower_third(p, transcript, default_name=p.get("channel", ""), subtitle="Live di YouTube")

    ctx.progress(35, "Sinyal live", "Membaca lonjakan chat & suara streamer")
    wall0 = float(p["wall_start"])
    try:
        events = json.loads(Path(p["chat_file"]).read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError):
        events = []
    hints = merge_hints(
        chat_signals(events, wall0, wall0 + dur),
        chat_spike_moments(events, wall0, wall0 + dur, lag=CHAT_LAG_SEC),
    ) + audio_signals(video)
    for h in hints:
        h["label"] = describe_signal(h)
    ctx.log(f"Sinyal: {sum(h['kind'] == 'chat' for h in hints)} lonjakan chat, {sum(h['kind'] == 'audio' for h in hints)} lonjakan suara")
    for h in [h for h in hints if h["kind"] == "chat"][:5]:
        ctx.log(f"  chat @{h['t']:.0f}s {h['strength']}× — {', '.join(h.get('samples', [])[:3])}")

    ctx.progress(45, "Analisis AI", f"{config.AI_PROVIDER}/{config.LLM_MODEL} mencari momen (dengan sinyal live)…" if llm else "Skor lokal")
    analyzer.SIGNAL_HINTS = hints
    # rerank per kandidat = 1 panggilan LLM masing-masing (bisa 24× ±20 dtk). AI Moment Finder (yang sudah
    # membaca sinyal live) cukup; rerank dibatasi agar satu jendela selesai sebelum jendela berikutnya.
    config.AI_RERANK_LIMIT = int(p.get("rerank_limit", 4))
    # Mode live: Editor-AI tidak diberi hak veto. Di VOD menolak klip lemah itu
    # bagus, tapi di jendela live ia menolak SEMUA kandidat (rubrik 13-27) dan
    # jendela jadi kosong — Clipper lama tidak punya gerbang ini sehingga selalu
    # dapat klip. Utamakan recall; penilaian tetap dipakai untuk memilih urutan.
    _review_before = config.AI_EDITOR_REVIEW
    config.AI_EDITOR_REVIEW = False
    try:
        clips_data = analyzer.analyze_viral_potential(transcript, info, use_llm=llm)
        clips_data = tools.enforce_clip_duration(clips_data, max_available=dur)
    finally:
        config.AI_EDITOR_REVIEW = _review_before
        analyzer.SIGNAL_HINTS = []
        config.AI_RERANK_LIMIT = None

    new_from, final = float(p.get("new_from", 0)), bool(p.get("final"))
    taken = [(c["wall_start"], c["wall_end"]) for c in load_state(parent_dir).get("clips", [])]
    picked: List[Dict] = []
    for c in clips_data:
        if c["end"] <= new_from + 4:
            continue  # sudah dinilai jendela sebelumnya
        if not final and c["end"] >= dur - 2.5:
            continue  # terpotong di ujung rekaman — jendela berikutnya (overlap) mengambilnya utuh
        ws, we = wall0 + c["start"], wall0 + c["end"]
        others = taken + [(wall0 + x["start"], wall0 + x["end"]) for x in picked]
        if any(min(we, b) - max(ws, a) > 0.3 * (we - ws) for a, b in others):
            continue
        c["wall_span"] = [ws, we]
        picked.append(c)
        if len(picked) >= num:
            break
    if p.get("hook_card") is False:
        for c in picked:
            c["no_hook_card"] = True
    if not picked and clips_data:
        # Fallback ala Clipper: jangan pulangkan jendela kosong. Ambil kandidat
        # terbaik yang masih menghormati batas durasi & wilayah baru, walau
        # skornya di bawah ambang. Tanpa ini, ambang tinggi = nol klip.
        allowed = [c for c in clips_data
                   if c["end"] > new_from + 4 and (final or c["end"] < dur - 2.5)]
        if allowed:
            best = dict(max(allowed, key=lambda c: float(c.get("score") or 0)))
            ws, we = wall0 + best["start"], wall0 + best["end"]
            best["wall_span"] = [ws, we]
            best["fallback"] = True
            picked = [best]
            ctx.log(f"Tidak ada kandidat di atas ambang — memakai yang terbaik "
                    f"(skor {float(best.get('score') or 0):.0f}) seperti mode Clipper")
    if not picked:
        Path(video).unlink(missing_ok=True)
        return {"_message": "Tidak ada momen yang cukup kuat di jendela ini", "clips": [], "total_clips": 0,
                "signals": hints[:12]}

    ctx.progress(55, "Render", f"Merender {len(picked)} klip (layout otomatis, cold open, sound design)…")
    out_dir = parent_dir / f"klip_{int(p.get('window', 0)):03d}"
    if p.get("campaign"):
        clips = _render_campaign_window(video, picked, out_dir, p, wall0)
    else:
        clips = process_clips(video, picked, out_dir, **tools.render_options(p))
    tools.brand_clips(ctx, clips, p.get("brand_kit"))
    tools.register_clips(ctx, clips, "live")
    summary = []
    for c in clips:
        row = {k: c.get(k) for k in ("file_path", "thumbnail", "asset_id", "ai_title", "hook_text", "score", "duration",
                                     "category", "upload_caption", "hashtags", "reason", "fyp_grade")}
        ws, we = c.get("wall_span") or [wall0 + float(c["start"]), wall0 + float(c["start"]) + float(c.get("duration") or 0)]
        row.update({"wall_start": ws, "wall_end": we,
                    "window": p.get("window"), "clock": _clock(wall0 + float(c["start"]))})
        summary.append(row)
    update_state(parent_dir, lambda s: s.setdefault("clips", []).extend(summary))
    Path(video).unlink(missing_ok=True)  # jendela ±400 MB — klip sudah jadi
    try:
        ctx.dir.rmdir()  # hasil ada di folder job induk; folder job jendela sendiri kosong
    except OSError:
        pass
    return {"_message": f"{len(clips)} klip dari jendela {p.get('window')}", "clips": clips, "total_clips": len(clips),
            "signals": hints[:12], "transcription": tinfo}
