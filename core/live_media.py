"""Rolling buffer segmen HLS live — dipindahkan dari Clipper (live_media.py).

Satu instance = satu buffer milik satu job. Menyimpan segmen MPEG-TS ber-timestamp
dinding (PDT), lalu bisa `capture(start, end, path)` untuk rentang TEPAT, bukan
dibulatkan ke batas segmen 60 detik seperti `core.live.build_window` (-c copy).

Dipakai oleh job `live_spike`: deteksi lonjakan chat → ambil [moment-25s, moment+20s].
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import tempfile
import threading
import time
import weakref
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from itertools import groupby
from pathlib import Path
from typing import Sequence
from urllib.parse import urljoin, urlsplit

try:
    from yt_dlp import YoutubeDL
    from yt_dlp.networking import Request
except ImportError:  # The timeline can also be used with an in-memory fixture.
    YoutubeDL = Request = None


class MediaError(RuntimeError):
    """An unsupported stream or a source, download, storage, or media tool failure."""


class WindowNotReady(RuntimeError):
    """At least one live rendition has not published the requested media yet."""


class WindowUnavailable(RuntimeError):
    """The request cannot be mapped to one buffered interval on every rendition."""


@dataclass(frozen=True)
class CapturedWindow:
    """UTC epoch seconds corresponding to the output's start and end."""

    start: float
    end: float


_PLAYLIST_BYTES = 16 * 1024**2
_SEGMENT_BYTES = 64 * 1024**2
_MAX_SEGMENTS = 32768
_SOCKET_TIMEOUT = 10
_BODY_TIMEOUT = 20
_EPSILON = 0.00001  # Arithmetic at UTC epoch magnitude.
_CLOCK_TOLERANCE = 0.075  # Allow minor PDT rounding, then start a new interval.
# A discontinuity restarts PDT at an arbitrary epoch; only its ordering is usable.
_UNANCHORED_TOLERANCE = 1.0
_CAPTURE_MARGIN = 0.25
_TIMESTAMP_TOLERANCE = 0.125  # Frame/sample quantization at a capture edge.


@dataclass(eq=False)
class _Segment:
    seq: int
    discontinuity: int
    duration: float
    url: str = field(repr=False)
    pdt: float | None = None
    gap: bool = False
    break_before: bool = False
    start: float | None = None
    seen: float = 0.0
    published: float | None = None  # Approximate local order, independent of PDT or rendition sequence.
    size: int | None = None  # Retained on refresh even if the bytes were evicted.
    filename: str | None = None

    @property
    def key(self) -> tuple[int, int]:
        return self.discontinuity, self.seq

    @property
    def end(self) -> float | None:
        return None if self.start is None else self.start + self.duration


@dataclass
class _Track:
    url: str = field(repr=False)
    user_agent: str | None = field(repr=False)
    listed: list[_Segment] = field(default_factory=list)
    cached: set[_Segment] = field(default_factory=set)
    ended: bool = False


@dataclass
class _Run:
    segments: list[_Segment]

    @property
    def start(self) -> float:
        return self.segments[0].start

    @property
    def end(self) -> float:
        return self.segments[-1].end


def _url(value: str, base: str = "") -> str:
    # Never let FFmpeg or the HTTP handler interpret a filesystem/device URI.
    if (not isinstance(value, str) or not value or "\\" in value
            or any(ord(char) <= 32 or ord(char) == 127 for char in value)):
        raise MediaError("Invalid live HLS URL")
    try:
        resolved = urljoin(base, value)
        parts = urlsplit(resolved)
        if (parts.scheme in ("http", "https") and parts.hostname and parts.port != 0
                and parts.username is None and parts.password is None and not parts.fragment):
            return resolved
    except ValueError:
        pass
    raise MediaError("Unsupported live HLS URL")


def _select_tracks(info: dict, max_height: int) -> list[_Track]:
    formats = [fmt for fmt in (info.get("formats") or [info])
               if isinstance(fmt, dict) and str(fmt.get("protocol", "")).startswith("m3u8")
               and fmt.get("url") and not fmt.get("has_drm")]
    source = str(info.get("extractor_key") or info.get("extractor") or "").lower()
    youtube = source.startswith("youtube")
    twitch = source.startswith("twitch")
    if source and not (youtube or twitch):
        raise MediaError("Live media buffering supports YouTube and Twitch HLS")

    def video_rank(fmt: dict) -> tuple[float, float, float]:
        return (float(fmt.get("height") or 0), float(fmt.get("fps") or 0),
                float(fmt.get("tbr") or 0))

    videos = [fmt for fmt in reversed(formats) if fmt.get("vcodec") != "none"
              and (not max_height or 0 < video_rank(fmt)[0] <= max_height)]
    for video in sorted(videos, key=video_rank, reverse=True):
        if video.get("acodec") == "none" and not twitch:
            master = video.get("manifest_url")
            audio = next((fmt for fmt in reversed(formats) if master and fmt.get("manifest_url") == master
                          and fmt.get("vcodec") == "none" and fmt.get("acodec") != "none"
                          and fmt["url"] != video["url"]), None)
            if audio is None:
                continue
            selected = (video, audio)
        elif video.get("acodec") != "none":
            selected = (video,)
        else:
            continue
        # Cookie handling stays with yt-dlp's domain-scoped jar. Don't forward
        # arbitrary extractor headers to the hosts named in a playlist.
        return [_Track(_url(fmt["url"]), (fmt.get("http_headers") or {}).get("User-Agent"))
                for fmt in selected]
    raise MediaError("No supported live HLS video/audio format at the requested resolution")


def _sequence(value: str) -> int:
    if not value.isascii() or not value.isdecimal() or len(value) > 20 or int(value) >= 2**64:
        raise MediaError("Invalid live HLS sequence number")
    return int(value)


def _playlist(body: bytes, base_url: str) -> tuple[list[_Segment], bool]:
    try:
        lines = body.decode("utf-8-sig").splitlines()
    except UnicodeError:
        raise MediaError("Invalid live HLS playlist encoding") from None
    if not lines or lines[0].strip() != "#EXTM3U":
        raise MediaError("Expected a live HLS media playlist")
    seq = discontinuity = 0
    duration = pdt = None
    ended = gap = False
    segments = []
    for raw in lines[1:]:
        line = raw.strip()
        tag, _, value = line.partition(":")
        if tag == "#EXT-X-MEDIA-SEQUENCE":
            seq = _sequence(value)
        elif tag == "#EXT-X-DISCONTINUITY-SEQUENCE":
            discontinuity = _sequence(value)
        elif tag == "#EXT-X-DISCONTINUITY":
            discontinuity += 1
        elif tag == "#EXT-X-GAP":
            gap = True
        elif tag == "#EXT-X-ENDLIST":
            ended = True
        elif tag in ("#EXT-X-KEY", "#EXT-X-SESSION-KEY") and value != "METHOD=NONE":
            raise MediaError("Encrypted live HLS segments are unsupported")
        elif tag == "#EXT-X-BYTERANGE":
            raise MediaError("Byte-range live HLS segments are unsupported")
        elif tag == "#EXT-X-MAP":
            raise MediaError("Fragmented MP4 (fMP4) HLS and initialization maps are unsupported")
        elif tag in ("#EXT-X-STREAM-INF", "#EXT-X-I-FRAME-STREAM-INF", "#EXT-X-I-FRAMES-ONLY",
                     "#EXT-X-SKIP"):
            raise MediaError("Expected a full live HLS media playlist of complete segments")
        elif tag == "#EXT-X-PROGRAM-DATE-TIME":
            try:
                date = datetime.fromisoformat(value.removesuffix("Z") + "+00:00" if value.endswith("Z") else value)
                if date.utcoffset() is None:
                    raise ValueError
                pdt = date.timestamp()
            except (ValueError, OverflowError, OSError):
                raise MediaError("Invalid live HLS program date-time") from None
        elif tag == "#EXTINF":
            if duration is not None:
                raise MediaError("Missing live HLS segment URI")
            try:
                duration = float(value.split(",", 1)[0])
            except ValueError:
                raise MediaError("Invalid live HLS segment duration") from None
            if not math.isfinite(duration) or not 0 < duration <= 3600:
                raise MediaError("Invalid live HLS segment duration")
        elif line and not line.startswith("#"):
            if duration is None:
                raise MediaError("Expected a duration before a live HLS segment URI")
            uri = _url(line, base_url)
            suffix = Path(urlsplit(uri).path).suffix.lower()
            if suffix in (".m4s", ".mp4", ".cmfv", ".cmfa", ".m4a"):
                raise MediaError("Fragmented MP4 (fMP4) HLS segments are unsupported")
            segments.append(_Segment(seq, discontinuity, duration, uri, pdt, gap))
            if len(segments) > _MAX_SEGMENTS:
                raise MediaError("Live HLS playlist contains too many segments")
            seq += 1
            duration = pdt = None
            gap = False
    return segments, ended


def _adjacent(left: _Segment, right: _Segment) -> bool:
    return (not left.gap and not right.gap and left.discontinuity == right.discontinuity
            and left.seq + 1 == right.seq)


def _apply_clock(fresh: list[_Segment], old: list[_Segment], now: float) -> list[_Segment]:
    """Map each contiguous part independently, reusing anchors on matching overlaps."""
    previous = {segment.key: segment for segment in old}
    part = []
    result = []
    origin = None
    elapsed = 0.0

    def flush() -> None:
        nonlocal elapsed, origin
        # Prefer an already established origin if a fresh PDT rounded differently.
        base = next((before.start - offset for _, before, offset in part
                     if before is not None and before.start is not None), origin)
        for segment, before, offset in part:
            mapped = None if base is None else base + offset
            if before is not None:
                before.url = segment.url  # The signing query may have changed.
                before.start = mapped
                result.append(before)
            else:
                segment.start = mapped
                segment.seen = now
                result.append(segment)
        part.clear()
        elapsed = 0.0
        origin = None

    for segment in fresh:
        if part and not _adjacent(part[-1][0], segment):
            flush()
        if segment.gap:
            result.append(segment)
            continue
        before = previous.get(segment.key)
        if before is not None and (before.gap or abs(before.duration - segment.duration) > _EPSILON
                                   or before.url.split("?", 1)[0] != segment.url.split("?", 1)[0]
                                   or (segment.pdt is not None and before.start is not None
                                       and abs(before.start - segment.pdt) > _CLOCK_TOLERANCE)):
            before = None
            segment.break_before = True  # A reused sequence belongs to a different run.
        current = segment if before is None else before
        if part and current.break_before:
            flush()
        anchor = current.start if current.start is not None else segment.pdt
        if anchor is not None:
            if not part and before is None:
                predecessor = previous.get((segment.discontinuity, segment.seq - 1))
                if (predecessor is not None and _adjacent(predecessor, segment)
                        and predecessor.end is not None
                        and abs(predecessor.end - anchor) > _CLOCK_TOLERANCE):
                    # The last refresh had a different predecessor than any stale cached match.
                    current.break_before = True
            if origin is not None and abs(origin + elapsed - anchor) > _CLOCK_TOLERANCE:
                flush()
                # Preserve the boundary for newly dated segments, even if an
                # older version of the predecessor has a matching UTC end.
                if current.start is None:
                    current.break_before = True
            if origin is None:
                origin = anchor - elapsed
        part.append((segment, before, elapsed))
        elapsed += segment.duration
    flush()

    # A new explicit anchor may date an overlap that was buffered without a clock.
    # Backfill only into the previous listing's adjacent, non-gap prefix.
    for left, right in zip(reversed(old[:-1]), reversed(old[1:])):
        if left.start is None and right.start is not None and not right.break_before and _adjacent(left, right):
            left.start = right.start - left.duration
    return result


def _runs(track: _Track) -> list[_Run]:
    # Keep run starts sorted for intersection, even when a backward PDT
    # interleaves segments from different runs on the UTC timeline.
    runs = []
    tails = {}
    for segment in sorted((seg for seg in track.cached if seg.start is not None), key=lambda seg: seg.start):
        previous_key = (segment.discontinuity, segment.seq - 1)
        predecessors = [] if segment.break_before else tails.get(previous_key, [])
        # Cached entries are non-gap. UTC adjacency distinguishes reused sequences.
        matches = [run for run in predecessors if abs(run.end - segment.start) <= _EPSILON]
        if len(matches) == 1:
            run = matches[0]
            predecessors.remove(run)
            run.segments.append(segment)
        else:
            run = _Run([segment])
            runs.append(run)
        tails.setdefault(segment.key, []).append(run)
    return runs


class _BodyTooLarge(MediaError):
    pass


class LiveMediaBuffer:
    """One coordinator owns the buffer; each instance has a private segment directory."""

    def __init__(self, ydl, info: dict, directory: Path, *, max_height=1080,
                 retention_seconds=180, max_bytes=256 * 1024**2):
        if not math.isfinite(retention_seconds) or retention_seconds <= 0 or max_bytes <= 0:
            raise ValueError("Live HLS buffer limits must be positive")
        try:
            self._tracks = _select_tracks(info, max_height)
        except (ValueError, TypeError, AttributeError, OverflowError):
            raise MediaError("Invalid live HLS format metadata") from None
        self._ydl = ydl
        self._real_ydl = YoutubeDL is not None and isinstance(ydl, YoutubeDL)
        self._retention = retention_seconds
        self._max_bytes = int(max_bytes)
        self._bytes = 0
        self._serial = 0
        # Serialize public operations that share the cache and temporary playlists.
        self._lock = threading.RLock()
        try:
            directory.mkdir(parents=True, exist_ok=True)
            self._temporary = tempfile.TemporaryDirectory(prefix="clipper-live-media-", dir=directory.resolve())
            self._directory = Path(self._temporary.name)
        except OSError:
            raise MediaError("Cannot create a live HLS buffer directory") from None

    def _read(self, url: str, track: _Track, limit: int, kind: str) -> tuple[bytes, str]:
        deadline = time.monotonic() + _BODY_TIMEOUT
        try:
            request = url
            if self._real_ydl:
                headers = {"Accept-Encoding": "identity"}
                if track.user_agent:
                    headers["User-Agent"] = track.user_agent
                request = Request(url, headers=headers, extensions={"timeout": _SOCKET_TIMEOUT})
            with self._ydl.urlopen(request) as response:
                # yt-dlp's Response exposes .getcode()/.geturl()/.info(); a plain urllib
                # response exposes .status/.url/.headers. Read whichever the object offers
                # so real-YDL 4xx/5xx, redirect targets, and Content-Length are honored.
                status = getattr(response, "status", None)
                if status is None and hasattr(response, "getcode"):
                    status = response.getcode()
                if status is not None and status != 200:
                    raise MediaError(f"Unexpected HTTP response for live HLS {kind}")
                final_url = _url(getattr(response, "url", None)
                                 or (response.geturl() if hasattr(response, "geturl") else None)
                                 or url)
                headers = getattr(response, "headers", None)
                if headers is None and hasattr(response, "info"):
                    headers = response.info()
                if headers is None:
                    headers = {}
                length = headers.get("Content-Length")
                if length is not None:
                    length = int(length)
                    if length < 0:
                        raise ValueError
                    if length > limit:
                        raise _BodyTooLarge(f"Live HLS {kind} exceeds its download limit")
                payload = bytearray()
                while True:
                    chunk = response.read(min(64 * 1024, limit + 1 - len(payload)))
                    if time.monotonic() > deadline:
                        raise MediaError(f"Timed out reading live HLS {kind}")
                    if not chunk:
                        break
                    payload.extend(chunk)
                    if len(payload) > limit:
                        raise _BodyTooLarge(f"Live HLS {kind} exceeds its download limit")
                if (length is not None and headers.get("Content-Encoding", "identity") == "identity"
                        and length != len(payload)):
                    raise MediaError(f"Incomplete live HLS {kind} download")
                return bytes(payload), final_url
        except MediaError:
            raise
        except Exception:
            # yt-dlp and HTTP errors can contain the request URL, headers, and tokens.
            raise MediaError(f"Could not download live HLS {kind}") from None

    @staticmethod
    def _priority(segment: _Segment, pins: Sequence[tuple[float, float]]) -> tuple:
        end = segment.end
        pinned = end is not None and any(lo < end and hi > segment.start for lo, hi in pins)
        return pinned, end is not None, segment.published if segment.published is not None else segment.seen, segment.seq

    def _drop(self, track: _Track, segment: _Segment) -> None:
        try:
            (self._directory / segment.filename).unlink(missing_ok=True)
        except OSError:
            raise MediaError("Could not evict a buffered HLS segment") from None
        self._bytes -= segment.size
        segment.filename = None
        track.cached.remove(segment)

    def _victims(self, segment: _Segment, size: int, pins: Sequence[tuple[float, float]]) -> list | None:
        needed = self._bytes + size - self._max_bytes
        if needed <= 0:
            return []
        if size > self._max_bytes:
            return None
        priority = self._priority(segment, pins)
        victims = []
        cached = ((track, item) for track in self._tracks for item in track.cached)
        for track, item in sorted(cached, key=lambda entry: self._priority(entry[1], pins)):
            if self._priority(item, pins) >= priority:
                break
            victims.append((track, item))
            needed -= item.size
            if needed <= 0:
                return victims
        return None

    def _save(self, track: _Track, segment: _Segment, pins: Sequence[tuple[float, float]]) -> None:
        if segment.size is not None and self._victims(segment, segment.size, pins) is None:
            return
        try:
            payload, _ = self._read(segment.url, track, min(self._max_bytes, _SEGMENT_BYTES), "segment")
        except _BodyTooLarge:
            if self._max_bytes > _SEGMENT_BYTES:
                raise
            segment.size = self._max_bytes + 1
            return
        if payload[4:8] in (b"ftyp", b"styp", b"moof", b"moov", b"sidx"):
            raise MediaError("Fragmented MP4 (fMP4) HLS segments are unsupported")
        sync_bytes = payload[::188]
        if not payload or len(payload) % 188 or sync_bytes.count(b"\x47") != len(sync_bytes):
            raise MediaError("Live HLS segment is not a complete MPEG-TS segment")
        segment.size = len(payload)
        victims = self._victims(segment, segment.size, pins)
        if victims is None:
            return
        for rendition, victim in victims:
            self._drop(rendition, victim)
        self._serial += 1
        target = self._directory / f"segment-{self._serial}.ts"
        try:
            target.write_bytes(payload)
        except OSError:
            target.unlink(missing_ok=True)
            raise MediaError("Could not store a live HLS segment") from None
        segment.filename = target.name
        self._bytes += segment.size
        track.cached.add(segment)

    def poll(self, pins: Sequence[tuple[float, float]] = ()) -> bool:
        """Fetch complete segments, giving pinned windows priority within the hard byte cap."""
        with self._lock:
            return self._poll_locked(pins)

    def _poll_locked(self, pins: Sequence[tuple[float, float]]) -> bool:
        manifests = []
        for track in self._tracks:
            if track.ended:
                manifests.append(None)
            else:
                body, base = self._read(track.url, track, _PLAYLIST_BYTES, "playlist")
                manifests.append(_playlist(body, base))
        now = time.monotonic()
        pending = []
        for track, manifest in zip(self._tracks, manifests):
            if manifest is not None:
                segments, track.ended = manifest
                track.listed = _apply_clock(segments, track.listed, now)
            # A backward PDT may leave earlier segments with a later UTC end.
            newest = next((seg.end for seg in reversed(track.listed) if seg.start is not None), None)
            elapsed = 0.0
            expired_in_listing = set()
            for segment in reversed(track.listed):
                if segment.published is None:
                    segment.published = now - elapsed
                if elapsed >= self._retention:
                    expired_in_listing.add(segment)
                elapsed += segment.duration
            for segment in track.cached | set(track.listed):
                expired = (now - segment.seen > self._retention or segment in expired_in_listing
                           or (newest is not None and segment.end is not None
                               and segment.end <= newest - self._retention))
                if expired and not self._priority(segment, pins)[0]:
                    if segment.filename is not None:
                        self._drop(track, segment)
                elif segment.filename is None and not segment.gap:
                    pending.append((track, segment))
        for track, segment in sorted(pending, key=lambda entry: self._priority(entry[1], pins), reverse=True):
            self._save(track, segment, pins)
        return all(track.ended for track in self._tracks)

    def coverage(self) -> tuple[float, float] | None:
        """Return the latest shared contiguous interval on the UTC timeline."""
        with self._lock:
            intervals = [(run.start, run.end) for run in _runs(self._tracks[0])]
            if len(self._tracks) == 2:
                intervals = _intersections(intervals, [(run.start, run.end) for run in _runs(self._tracks[1])])
            return max(intervals, key=lambda interval: (interval[1], interval[0]), default=None)

    def _select_window(self, start: float, end: float) -> list[_Run]:
        selected = []
        pending = False
        for track in self._tracks:
            runs = _runs(track)
            candidates = [run for run in runs if run.start - _EPSILON <= start < run.end - _EPSILON]
            tail = track.listed[-1] if track.listed else None
            if len(candidates) > 1:
                raise WindowUnavailable("Requested start has no unambiguous buffered HLS clock")
            run = candidates[0] if candidates else None
            if any(other is not run and other.start < end - _EPSILON and other.end > start + _EPSILON
                   for other in runs):
                raise WindowUnavailable("Requested window crosses a live HLS gap or clock change")
            if run is None:
                if (tail is not None and tail.filename and tail.end is not None
                        and start >= tail.end - _EPSILON and not track.ended):
                    pending = True
                    continue
                raise WindowUnavailable("Requested start has no unambiguous buffered HLS clock")
            if end > run.end + _EPSILON:
                if tail is not run.segments[-1] or track.ended:
                    raise WindowUnavailable("Requested window crosses a live HLS gap or end of stream")
                pending = True
            selected.append(run)
        if pending:
            raise WindowNotReady("Waiting for live HLS postroll")
        return selected

    def capture(self, start: float, end: float, path: Path) -> CapturedWindow:
        """Write an aligned local MP4 and return the UTC clock of its output timeline."""
        with self._lock:
            return self._capture_locked(start, end, path)

    def _capture_locked(self, start: float, end: float, path: Path) -> CapturedWindow:
        if not math.isfinite(start) or not math.isfinite(end) or start >= end:
            raise ValueError("Expected a finite, increasing live capture interval")
        runs = self._select_window(start, end)
        origin = max(start - _CAPTURE_MARGIN, *(run.start for run in runs))
        stop = min(end + _CAPTURE_MARGIN, *(run.end for run in runs))
        inputs = []
        try:
            path = path.resolve()
            if path.is_relative_to(self._directory):
                raise MediaError("Live capture output must be outside the segment buffer")
            path.parent.mkdir(parents=True, exist_ok=True)
            for name, run in zip(("video.m3u8", "audio.m3u8"), runs):
                segments = [seg for seg in run.segments if seg.end > origin and seg.start < stop]
                inputs.append((name, segments[0].start))
                _write_playlist(self._directory / name, segments)
            with tempfile.TemporaryDirectory(prefix="clipper-live-output-", dir=path.parent) as tmp:
                output = Path(tmp) / "capture.mp4"
                result = _mux(self._directory, inputs, origin, stop, start, end, output)
                output.replace(path)
            return result
        except OSError:
            raise MediaError("Could not write the local live HLS capture") from None
        finally:
            for name, _ in inputs:
                (self._directory / name).unlink(missing_ok=True)


def _intersections(left: list[tuple[float, float]], right: list[tuple[float, float]]) -> list[tuple[float, float]]:
    # The same UTC may occur twice after a backward clock change. Keep runs
    # distinct, even where two intersections overlap or touch.
    result = []
    for start, end in left:
        for other_start, other_end in right:
            if other_start >= end:
                break
            lo, hi = max(start, other_start), min(end, other_end)
            if hi - lo > _EPSILON:
                result.append((lo, hi))
    return result


def _write_playlist(path: Path, segments: list[_Segment]) -> None:
    lines = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-PLAYLIST-TYPE:VOD",
             f"#EXT-X-TARGETDURATION:{math.ceil(max(seg.duration for seg in segments))}"]
    for segment in segments:
        lines.extend((f"#EXTINF:{segment.duration:.6f},", segment.filename))
    path.write_text("\n".join([*lines, "#EXT-X-ENDLIST", ""]), encoding="ascii")


def _tool(name: str) -> str:
    bundled = Path(__file__).resolve().parent / "tools" / "ffmpeg" / (name + (".exe" if os.name == "nt" else ""))
    return str(bundled) if bundled.is_file() else shutil.which(name) or name


def _run_tool(tool: str, args: list[str], cwd: Path, timeout: int) -> bytes:
    try:
        result = subprocess.run([_tool(tool), *args], cwd=cwd, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=timeout, check=True)
    except (OSError, subprocess.SubprocessError):
        raise MediaError(f"{tool} could not process the local live HLS capture") from None
    return result.stdout


def _probe(cwd: Path, path: str, demuxer: str) -> dict:
    try:
        return json.loads(_run_tool("ffprobe", ["-v", "error", "-protocol_whitelist", "file", "-f", demuxer,
                           "-show_entries", "stream=codec_type,start_time,duration:format=start_time,duration",
                           "-of", "json", path], cwd, 20))
    except ValueError:
        raise MediaError("Could not read live HLS media timestamps") from None


def _stream(probe: dict, kind: str) -> dict:
    for stream in probe.get("streams", []):
        if stream.get("codec_type") == kind:
            return stream
    raise MediaError(f"Local live HLS media has no {kind} stream")


def _seconds(section: dict, key: str) -> float:
    try:
        value = float(section[key])
        if math.isfinite(value):
            return value
    except (KeyError, ValueError, TypeError):
        pass
    raise MediaError("Local live HLS media has no usable timestamps")


def _mux(cwd: Path, inputs: list[tuple[str, float]], origin: float, stop: float,
         requested_start: float, requested_end: float, output: Path) -> CapturedWindow:
    video, video_utc = inputs[0]
    probe = _probe(cwd, video, "hls")
    v_pts = _seconds(_stream(probe, "video"), "start_time")
    audio_index = 0
    if len(inputs) == 1:
        a_pts = _seconds(_stream(probe, "audio"), "start_time")
        offsets = [video_utc - origin - min(v_pts, a_pts)]
    else:
        audio, audio_utc = inputs[1]
        a_pts = _seconds(_stream(_probe(cwd, audio, "hls"), "audio"), "start_time")
        v_offset = video_utc - origin - v_pts
        a_offset = audio_utc - origin - a_pts
        # Preserve the encoder's common PTS clock when it agrees with PDT. For
        # independent timestamp origins, place each input on the same UTC axis.
        offsets = [v_offset, v_offset if abs(v_offset - a_offset) <= _CLOCK_TOLERANCE else a_offset]
        audio_index = 1
    args = ["-hide_banner", "-loglevel", "error", "-nostdin", "-xerror", "-copyts"]
    for (playlist, _), offset in zip(inputs, offsets):
        args.extend(["-itsoffset", f"{offset:.6f}", "-protocol_whitelist", "file", "-f", "hls", "-i", playlist])
    length = stop - origin
    # Trim with a common origin. Resetting each track to its own first frame
    # would erase the alignment, especially for staggered segment boundaries.
    args.extend(["-filter_complex", f"[0:v:0]trim=start=0:end={length:.6f}[v];"
                 f"[{audio_index}:a:0]atrim=start=0:end={length:.6f}[a]",
                 "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
                 "-pix_fmt", "yuv420p", "-fps_mode:v", "passthrough", "-enc_time_base:v", "1:90000",
                 "-c:a", "aac", "-b:a", "160k", "-avoid_negative_ts", "disabled", "-movflags", "+faststart",
                 "-f", "mp4", str(output)])
    _run_tool("ffmpeg", args, cwd, 180)
    captured = _probe(cwd, str(output), "mov")
    streams = [_stream(captured, kind) for kind in ("video", "audio")]
    starts = [_seconds(stream, "start_time") for stream in streams]
    ends = [start + _seconds(stream, "duration") for start, stream in zip(starts, streams)]
    file_start = _seconds(captured.get("format", {}), "start_time")
    duration = _seconds(captured.get("format", {}), "duration")
    if (duration <= 0 or abs(file_start) > _TIMESTAMP_TOLERANCE or abs(duration - length) > _TIMESTAMP_TOLERANCE
            or origin + max(starts) > requested_start + _TIMESTAMP_TOLERANCE
            or origin + min(ends) < requested_end - _TIMESTAMP_TOLERANCE):
        raise WindowUnavailable("Local media timestamps do not cover the requested HLS window")
    return CapturedWindow(origin + file_start, origin + duration)
