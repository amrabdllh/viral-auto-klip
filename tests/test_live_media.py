"""Isolated live HLS checks using generated playlists and local FFmpeg fixtures."""

import array
import io
import json
import shutil
import subprocess
import sys
import tempfile
import traceback
import unittest
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core import live_media as media  # noqa: E402


BASE = "http://fixture.invalid/"
VIDEO = BASE + "video.m3u8"
AUDIO = BASE + "audio.m3u8"
MASTER = BASE + "master.m3u8"
UTC = 1_800_000_000.0
PACKET = b"\x47" + b"\x00" * 187


def playlist(sequence, entries, *, end=False, discontinuity=0, tail=""):
    lines = ["#EXTM3U", "#EXT-X-TARGETDURATION:5", f"#EXT-X-MEDIA-SEQUENCE:{sequence}",
             f"#EXT-X-DISCONTINUITY-SEQUENCE:{discontinuity}"]
    for name, duration, anchor, *tags in entries:
        lines.extend(tags)
        if anchor is not None:
            pdt = datetime.fromtimestamp(UTC + anchor, timezone.utc).isoformat()
            lines.append(f"#EXT-X-PROGRAM-DATE-TIME:{pdt}")
        lines.extend([f"#EXTINF:{duration},", name])
    if tail:
        lines.extend(tail.splitlines())
    if end:
        lines.append("#EXT-X-ENDLIST")
    return ("\n".join(lines) + "\n").encode()


def video_format(*, url=VIDEO, height=720, master=MASTER, muxed=False):
    return {"url": url, "protocol": "m3u8_native", "manifest_url": master, "height": height,
            "vcodec": "avc1", "acodec": "aac" if muxed else "none"}


def youtube(*formats):
    return {"extractor_key": "Youtube", "formats": [video_format(), {
        "url": AUDIO, "protocol": "m3u8_native", "manifest_url": MASTER, "vcodec": "none", "acodec": "aac",
    }, *formats]}


def twitch(*formats):
    return {"extractor_key": "TwitchStream", "formats": [video_format(muxed=True), *formats]}


class LocalResponse(io.BytesIO):
    def __init__(self, data, *, url=None, headers=None):
        super().__init__(data)
        self.url = url
        self.headers = headers or {}


class Fixture:
    def __init__(self):
        self.bodies = {}
        self.requests = Counter()

    def urlopen(self, url):
        self.requests[url] += 1
        data = self.bodies[url]
        if isinstance(data, Exception):
            raise data
        if isinstance(data, LocalResponse):
            return data
        return LocalResponse(data)

    def advertise(self, url, sequence, entries, **kwargs):
        self.bodies[url] = playlist(sequence, entries, **kwargs)
        for name, _, _, *_ in entries:
            self.bodies.setdefault(BASE + name, PACKET)


class BufferTests(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory(prefix="test-clipper-live-"))
        self.root = Path(self.temp)
        self.fixture = Fixture()
        self.output = self.root / "output.mp4"

    def buffer(self, info=None, **kwargs):
        return media.LiveMediaBuffer(self.fixture, info or twitch(), self.root / "buffer", **kwargs)

    def assertCoverage(self, buffer, start, end):
        actual = buffer.coverage()
        self.assertIsNotNone(actual)
        self.assertAlmostEqual(actual[0] - UTC, start, places=5)
        self.assertAlmostEqual(actual[1] - UTC, end, places=5)

    def test_split_renditions_select_same_master_and_keep_sliding_anchor(self):
        self.fixture.advertise(VIDEO, 40, [("v0.ts", 2, None), ("v1.ts?sig=first", 2, 2), ("v2.ts", 2, None)])
        self.fixture.advertise(AUDIO, 900, [("a0.ts", 2, 1), ("a1.ts", 2, None), ("a2.ts", 2, None)])
        info = youtube(video_format(url=BASE + "unpaired.m3u8", height=1080, master=BASE + "another.m3u8"))
        buffer = self.buffer(info)
        self.assertFalse(buffer.poll())
        self.assertCoverage(buffer, 1, 6)
        with self.assertRaises(media.WindowNotReady):
            buffer.capture(UTC + 2, UTC + 7, self.output)

        self.fixture.advertise(VIDEO, 41, [("v1.ts?sig=second", 2, None), ("v2.ts", 2, None), ("v3.ts", 2, None)],
                               tail='#EXT-X-PART:DURATION=0.2,URI="partial.ts"\n'
                                    '#EXT-X-PRELOAD-HINT:TYPE=PART,URI="next.ts"\n#EXTINF:2,')
        self.fixture.advertise(AUDIO, 901, [("a1.ts", 2, None), ("a2.ts", 2, None), ("a3.ts", 2, None)], end=True)
        self.assertFalse(buffer.poll())
        self.assertCoverage(buffer, 1, 8)
        self.assertNotIn(BASE + "v1.ts?sig=second", self.fixture.requests)
        self.assertEqual(self.fixture.requests[BASE + "a1.ts"], 1)
        for uri in ("partial.ts", "next.ts", "unpaired.m3u8"):
            self.assertNotIn(BASE + uri, self.fixture.requests)

        self.fixture.advertise(VIDEO, 42, [("v2.ts", 2, None), ("v3.ts", 2, None), ("v4.ts", 2, None)], end=True)
        self.assertTrue(buffer.poll())
        self.assertCoverage(buffer, 1, 9)
        with self.assertRaises(media.WindowUnavailable):
            buffer.capture(UTC + 2, UTC + 9.5, self.output)
        self.assertFalse(self.output.exists())

    def test_twitch_muxed_quality_limit(self):
        self.fixture.advertise(VIDEO, 7, [("v0.ts", 2, 0)])
        buffer = self.buffer(twitch(video_format(url=BASE + "1080p.m3u8", height=1080, muxed=True)), max_height=720)
        buffer.poll()
        self.assertCoverage(buffer, 0, 2)
        self.assertNotIn(BASE + "1080p.m3u8", self.fixture.requests)

    def test_no_height_cap_takes_highest_resolution_then_frame_rate(self):
        high = video_format(url=BASE + "2160p60.m3u8", height=2160, muxed=True) | {"fps": 60, "tbr": 20000}
        slow = video_format(url=BASE + "2160p30.m3u8", height=2160, muxed=True) | {"fps": 30, "tbr": 30000}
        self.fixture.advertise(high["url"], 7, [("best.ts", 2, 0)])
        buffer = self.buffer(youtube(high, slow), max_height=0)
        buffer.poll()
        self.assertCoverage(buffer, 0, 2)
        self.assertNotIn(slow["url"], self.fixture.requests)
        self.assertNotIn(VIDEO, self.fixture.requests)

    def test_late_anchor_dates_buffered_overlap(self):
        self.fixture.advertise(VIDEO, 3, [("v0.ts", 2, None), ("v1.ts", 2, None)])
        buffer = self.buffer()
        buffer.poll()
        self.assertIsNone(buffer.coverage())
        with self.assertRaises(media.WindowUnavailable):
            buffer.capture(UTC, UTC + 1, self.output)
        self.fixture.advertise(VIDEO, 4, [("v1.ts", 2, None), ("v2.ts", 2, 4)])
        buffer.poll()
        self.assertCoverage(buffer, 0, 6)
        self.assertEqual(self.fixture.requests[BASE + "v1.ts"], 1)

    def test_no_clock_inheritance_across_nonoverlapping_refresh(self):
        self.fixture.advertise(VIDEO, 3, [("v0.ts", 2, 0)])
        buffer = self.buffer()
        buffer.poll()
        self.fixture.advertise(VIDEO, 4, [("v1.ts", 2, None), ("v2.ts", 2, None)])
        buffer.poll()
        self.assertCoverage(buffer, 0, 2)
        with self.assertRaises(media.WindowUnavailable):
            buffer.capture(UTC + 1, UTC + 4, self.output)

    def test_discontinuity_even_with_an_adjacent_utc_clock(self):
        self.fixture.advertise(VIDEO, 0, [("v0.ts", 2, 0), ("v1.ts", 2, 2, "#EXT-X-DISCONTINUITY"),
                                         ("v2.ts", 2, None)])
        buffer = self.buffer()
        buffer.poll()
        self.assertCoverage(buffer, 2, 6)
        with self.assertRaises(media.WindowUnavailable):
            buffer.capture(UTC + 1, UTC + 3, self.output)
        self.fixture.advertise(VIDEO, 1, [("v1.ts", 2, None), ("v2.ts", 2, None), ("v3.ts", 2, None)],
                               discontinuity=1)
        buffer.poll()
        self.assertCoverage(buffer, 2, 8)

    def test_gap_and_clock_jump_split_the_available_runs(self):
        self.fixture.advertise(VIDEO, 6, [("v0.ts", 2, 0), ("gap.ts", 2, None, "#EXT-X-GAP"),
                                         ("v2.ts", 2, None), ("v3.ts", 2, 6), ("v4.ts", 2, 9)])
        buffer = self.buffer()
        buffer.poll()
        self.assertCoverage(buffer, 9, 11)
        self.assertNotIn(BASE + "gap.ts", self.fixture.requests)
        with self.assertRaises(media.WindowUnavailable):
            buffer.capture(UTC + 1, UTC + 5, self.output)
        with self.assertRaises(media.WindowUnavailable):
            buffer.capture(UTC + 7, UTC + 10, self.output)

    def test_backward_pdt_preserves_muxed_run_and_rejects_overlaps(self):
        self.fixture.advertise(VIDEO, 40, [("v0.ts", 2, 0), ("v1.ts", 2, None), ("v2.ts", 2, None),
                                          ("v3.ts", 0.5, 0.5), ("v4.ts", 0.5, None)], end=True)
        buffer = self.buffer()
        self.assertTrue(buffer.poll())
        for start, end in ((0.75, 1.25), (0.25, 0.75)):
            with self.subTest(start=start, end=end), self.assertRaises(media.WindowUnavailable):
                buffer.capture(UTC + start, UTC + end, self.output)
        self.assertFalse(self.output.exists())
        self.assertCoverage(buffer, 0, 6)
        run, = buffer._select_window(UTC + 1.75, UTC + 2.25)
        self.assertEqual([seg.seq for seg in run.segments], [40, 41, 42])

    def test_reused_media_sequences_keep_distinct_dated_runs(self):
        self.fixture.advertise(VIDEO, 40, [("v0.ts", 2, 0), ("v1.ts", 2, None), ("v2.ts", 2, None)])
        buffer = self.buffer()
        buffer.poll()
        self.fixture.advertise(VIDEO, 40, [("restart0.ts", 0.5, 0.5), ("restart1.ts", 0.5, None)], end=True)
        self.assertTrue(buffer.poll())
        with self.assertRaises(media.WindowUnavailable):
            buffer.capture(UTC + 0.75, UTC + 1.25, self.output)
        self.assertCoverage(buffer, 0, 6)

    def test_backward_pdt_does_not_join_a_reused_cached_predecessor(self):
        self.fixture.advertise(VIDEO, 40, [("v0.ts", 2, 0)])
        buffer = self.buffer()
        buffer.poll()
        self.fixture.advertise(VIDEO, 40, [("restart0.ts", 2, 5), ("restart1.ts", 2, 2)])
        buffer.poll()
        with self.assertRaises(media.WindowUnavailable):
            buffer.capture(UTC + 1, UTC + 3, self.output)
        self.assertFalse(self.output.exists())

        # Later listings omit the PDT and eventually slide past the undated prefix.
        self.fixture.advertise(VIDEO, 40, [("undated.ts", 2, None), ("restart1.ts", 2, None)])
        buffer.poll()
        self.fixture.advertise(VIDEO, 41, [("restart1.ts", 2, None)], end=True)
        self.assertTrue(buffer.poll())
        run, = buffer._select_window(UTC + 0.5, UTC + 1.5)
        self.assertEqual(run.segments[0].url, BASE + "v0.ts")

    def test_backward_pdt_across_refreshes_cannot_join_stale_predecessor(self):
        for info in (twitch(), youtube()):
            with self.subTest(source=info["extractor_key"]):
                buffer = self.buffer(info)
                for seq, uri, seconds in ((40, "v0.ts", 0), (40, "replacement.ts", 5), (41, "v1.ts", 2)):
                    self.fixture.advertise(VIDEO, seq, [(uri, 2, seconds)])
                    if info["extractor_key"] == "Youtube":
                        self.fixture.advertise(AUDIO, 900, [("a0.ts", 8, 0)])
                    buffer.poll()
                with self.assertRaises(media.WindowUnavailable):
                    buffer._select_window(UTC + 1, UTC + 3.4)

    def test_byte_eviction_prefers_recent_publication_over_backward_pdt(self):
        self.fixture.advertise(VIDEO, 40, [("older.ts", 2, 20), ("v0.ts", 2, 0), ("v1.ts", 2, None)])
        buffer = self.buffer(max_bytes=2 * len(PACKET))
        buffer.poll()
        run, = buffer._select_window(UTC + 1.75, UTC + 2.25)
        self.assertEqual([segment.seq for segment in run.segments], [41, 42])

    def test_split_renditions_intersect_distinct_runs_after_backward_pdt(self):
        self.fixture.advertise(VIDEO, 40, [("v0.ts", 2, 0), ("v1.ts", 2, None), ("v2.ts", 2, None),
                                          ("v3.ts", 1, 0.5)], end=True)
        self.fixture.advertise(AUDIO, 900, [("a0.ts", 2, 6), ("a1.ts", 2, 1), ("a2.ts", 2, None),
                                           ("a3.ts", 0.5, 2.75)], end=True)
        buffer = self.buffer(youtube())
        self.assertTrue(buffer.poll())
        for start, end in ((1.1, 1.4), (2.9, 3.1)):
            with self.subTest(start=start, end=end), self.assertRaises(media.WindowUnavailable):
                buffer.capture(UTC + start, UTC + end, self.output)
        self.assertFalse(self.output.exists())
        self.assertCoverage(buffer, 1, 5)
        runs = buffer._select_window(UTC + 1.75, UTC + 2.25)
        self.assertEqual([[seg.seq for seg in run.segments] for run in runs], [[40, 41, 42], [901, 902]])

    def test_backward_pdt_does_not_expire_new_segments(self):
        self.fixture.advertise(VIDEO, 40, [("v0.ts", 2, 20), ("v1.ts", 2, 0), ("v2.ts", 2, None)])
        buffer = self.buffer(retention_seconds=5)
        buffer.poll()
        run, = buffer._select_window(UTC + 1.75, UTC + 2.25)
        self.assertEqual([seg.seq for seg in run.segments], [41, 42])

    def test_postroll_after_backward_pdt_cannot_cross_another_buffered_run(self):
        self.fixture.advertise(VIDEO, 40, [("v0.ts", 2, 3), ("v1.ts", 2, 0)])
        buffer = self.buffer()
        self.assertFalse(buffer.poll())
        with self.assertRaises(media.WindowNotReady):
            buffer.capture(UTC + 2, UTC + 2.75, self.output)
        with self.assertRaises(media.WindowUnavailable):
            buffer.capture(UTC + 2, UTC + 3.5, self.output)

    def test_hard_byte_cap_with_pins_and_time_eviction_after_release(self):
        self.fixture.advertise(VIDEO, 0, [("v0.ts", 2, 0), ("v1.ts", 2, None),
                                         ("v2.ts", 2, None), ("v3.ts", 2, None)])
        buffer = self.buffer(retention_seconds=2, max_bytes=2 * len(PACKET))
        buffer.poll([(UTC + 0.5, UTC + 3.5)])
        self.assertCoverage(buffer, 0, 4)
        self.assertEqual(sum(path.stat().st_size for path in self.root.rglob("*.ts")), 2 * len(PACKET))
        buffer.poll([(UTC, UTC + 8)])
        self.assertCoverage(buffer, 4, 8)
        self.assertEqual(sum(path.stat().st_size for path in self.root.rglob("*.ts")), 2 * len(PACKET))
        with self.assertRaises(media.WindowUnavailable):
            buffer.capture(UTC + 1, UTC + 3, self.output)
        buffer.poll()
        self.assertCoverage(buffer, 6, 8)
        self.assertEqual(sum(path.stat().st_size for path in self.root.rglob("*.ts")), len(PACKET))

    def test_incoming_segment_larger_than_byte_cap(self):
        self.fixture.advertise(VIDEO, 0, [("v0.ts", 2, 0), ("large.ts", 2, None)])
        self.fixture.bodies[BASE + "large.ts"] = PACKET * 2
        buffer = self.buffer(max_bytes=len(PACKET))
        buffer.poll([(UTC, UTC + 4)])
        self.assertCoverage(buffer, 0, 2)
        self.assertEqual(sum(path.stat().st_size for path in self.root.rglob("*.ts")), len(PACKET))
        with self.assertRaises(media.WindowUnavailable):
            buffer.capture(UTC + 1, UTC + 3, self.output)

    def test_rejects_unsupported_media_and_untrusted_urls(self):
        samples = [
            ("v0.ts", '#EXT-X-KEY:METHOD=AES-128,URI="https://fixture.invalid/?secret=one"', "Encrypted"),
            ("v0.ts", "#EXT-X-BYTERANGE:200@0", "Byte-range"),
            ("v0.ts", '#EXT-X-MAP:URI="init.mp4"', "fMP4"),
            ("v0.m4s", "", "fMP4"),
            ("file:///private/path.ts", "", "Unsupported live HLS URL"),
            ("https://user:password@fixture.invalid/path.ts", "", "Unsupported live HLS URL"),
        ]
        for name, tag, error in samples:
            with self.subTest(name=name, tag=tag):
                self.fixture.advertise(VIDEO, 0, [(name, 2, 0, tag)])
                buffer = self.buffer()
                with self.assertRaisesRegex(media.MediaError, error):
                    buffer.poll()
        self.assertEqual(set(self.fixture.requests), {VIDEO})

    def test_network_errors_redact_urls_and_detect_incomplete_reads(self):
        secret = "private-fixture-token"
        self.fixture.advertise(VIDEO, 0, [(f"v0.ts?token={secret}", 2, 0)])
        url = BASE + f"v0.ts?token={secret}"
        self.fixture.bodies[url] = OSError(f"Unable to read {url}")
        buffer = self.buffer()
        try:
            buffer.poll()
        except media.MediaError as exc:
            self.assertNotIn(secret, "".join(traceback.format_exception(exc)))
        else:
            self.fail("Expected a redacted media download error")
        self.assertIsNone(buffer.coverage())
        self.fixture.bodies[url] = LocalResponse(PACKET, headers={"Content-Length": str(2 * len(PACKET))})
        with self.assertRaisesRegex(media.MediaError, "Incomplete"):
            buffer.poll()
        self.assertEqual(list(self.root.rglob("*.ts")), [])

    def test_relative_segments_use_redirected_playlist_base(self):
        self.fixture.bodies[VIDEO] = LocalResponse(playlist(1, [("../v0.ts", 2, 0)]),
                                                   url=BASE + "edge/channel/video.m3u8")
        self.fixture.bodies[BASE + "edge/v0.ts"] = PACKET
        buffer = self.buffer()
        buffer.poll()
        self.assertCoverage(buffer, 0, 2)
        self.assertEqual(self.fixture.requests[BASE + "edge/v0.ts"], 1)

    def test_ytdlp_response_interface_status_url_headers(self):
        # A real YoutubeDL.urlopen returns a yt_dlp.networking.Response that has
        # .getcode()/.geturl()/.info() but NOT .status/.url/.headers. The reader must
        # honor redirects and Content-Length through that interface too.
        class YDLResponse(LocalResponse):
            def __init__(self, data, code, url, headers):
                super().__init__(data, url=None, headers=None)
                self._code, self._url, self._headers = code, url, headers

            def getcode(self):
                return self._code

            def geturl(self):
                return self._url

            def info(self):
                return self._headers

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self.close()
                return False

        body = playlist(1, [("v0.ts", 2, 0)])
        self.fixture.bodies[VIDEO] = YDLResponse(body, 200, BASE + "edge/live/video.m3u8", {})
        self.fixture.bodies[BASE + "edge/live/v0.ts"] = YDLResponse(
            PACKET, 200, BASE + "edge/live/v0.ts", {"Content-Length": str(len(PACKET))})
        buffer = self.buffer()
        buffer.poll()
        self.assertCoverage(buffer, 0, 2)
        # The playlist was fetched from VIDEO; the response's geturl() reported the
        # redirected URL, so relative segments resolve against the redirect base.
        self.assertEqual(self.fixture.requests[VIDEO], 1)

    def test_ytdlp_response_non_200_status_rejected(self):
        class YDLResponse(LocalResponse):
            def getcode(self):
                return 403

            def geturl(self):
                return VIDEO

            def info(self):
                return {}

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self.close()
                return False

        self.fixture.bodies[VIDEO] = YDLResponse(b"forbidden")
        buffer = self.buffer()
        with self.assertRaisesRegex(media.MediaError, "Unexpected HTTP response"):
            buffer.poll()


@unittest.skipUnless(shutil.which(media._tool("ffmpeg")) and shutil.which(media._tool("ffprobe")),
                     "local FFmpeg and ffprobe required")
class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="test-clipper-markers-")))
        self.fixture = Fixture()

    def tool(self, name, *args):
        return subprocess.run([media._tool(name), *args], cwd=self.root, capture_output=True,
                              timeout=30, check=True).stdout

    def generate(self, stem, *, video=None, audio=None):
        inputs = []
        codecs = []
        if video is not None:
            inputs += ["-f", "lavfi", "-i", "color=black:s=96x64:r=25:d=8"]
            codecs += ["-vf", f"drawbox=color=white:t=fill:enable='between(t,3.2,3.56)',setpts=PTS+{video}/TB",
                       "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "50", "-keyint_min", "50",
                       "-sc_threshold", "0", "-bf", "0"]
        if audio is not None:
            inputs += ["-f", "lavfi", "-i", "aevalsrc='if(between(t,3.2,3.56),0.5*sin(2*PI*880*t),0)':s=48000:d=8"]
            codecs += ["-af", f"asetpts=PTS+{audio}/TB", "-c:a", "aac"]
        try:
            self.tool("ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", *inputs, *codecs,
                      "-f", "hls", "-hls_time", "2" if video is not None else "1.6", "-hls_list_size", "0",
                      "-hls_segment_filename", f"{stem}-%d.ts", f"{stem}.m3u8")
        except subprocess.CalledProcessError as exc:
            self.fail(exc.stderr.decode(errors="replace"))

    def offer(self, url, stem, seq, skip, offset=0.0):
        entries = []
        duration = None
        clock = offset
        for line in (self.root / f"{stem}.m3u8").read_text().splitlines():
            if line.startswith("#EXTINF:"):
                duration = float(line[8:].split(",")[0])
            elif line and not line.startswith("#"):
                self.fixture.bodies[BASE + line] = (self.root / line).read_bytes()
                entries.append((line, duration, clock))
                clock += duration
        self.fixture.advertise(url, seq, entries[skip:], end=True)

    def assert_markers(self, result, output):
        video = self.tool("ffmpeg", "-v", "error", "-i", str(output), "-an", "-vf", "scale=1:1,format=gray",
                          "-fps_mode", "passthrough", "-f", "rawvideo", "pipe:1")
        frames = json.loads(self.tool("ffprobe", "-v", "error", "-select_streams", "v:0", "-show_frames",
                                      "-show_entries", "frame=best_effort_timestamp_time", "-of", "json", str(output)))
        times = [float(frame["best_effort_timestamp_time"]) for frame in frames["frames"]]
        self.assertEqual(len(times), len(video))
        first_white = times[next(index for index, level in enumerate(video) if level > 200)]
        audio = self.tool("ffmpeg", "-v", "error", "-i", str(output), "-vn", "-ac", "1", "-ar", "48000", "-f",
                          "s16le", "pipe:1")
        samples = array.array("h", audio)
        first_tone = next(index / 48000 for index, level in enumerate(samples) if abs(level) > 3000)
        self.assertAlmostEqual(first_white, first_tone, delta=0.06)
        self.assertAlmostEqual(result.start - UTC + first_white, 3.2, delta=0.075)
        self.assertAlmostEqual(result.start - UTC + first_tone, 3.2, delta=0.075)

    def test_split_renditions_with_staggered_starts_and_independent_pts(self):
        self.generate("v", video=7)
        self.generate("a", audio=23)
        self.offer(VIDEO, "v", 101, skip=1)
        self.offer(AUDIO, "a", 9001, skip=1, offset=-1024 / 48000)
        buffer = media.LiveMediaBuffer(self.fixture, youtube(), self.root / "buffer")
        self.assertTrue(buffer.poll())
        output = self.root / "split output.mp4"
        result = buffer.capture(UTC + 2.65, UTC + 4.35, output)
        self.assertLess(result.start, UTC + 2.65)
        self.assertGreater(result.end, UTC + 4.35)
        self.assert_markers(result, output)
        self.assertEqual(list((self.root / "buffer").rglob("*.m3u8")), [])

    def test_twitch_muxed_capture_preserves_native_av_clock(self):
        self.generate("m", video=11, audio=11)
        self.offer(VIDEO, "m", 400, skip=0, offset=-1024 / 48000)
        buffer = media.LiveMediaBuffer(self.fixture, twitch(), self.root / "buffer")
        self.assertTrue(buffer.poll())
        output = self.root / "muxed.mp4"
        result = buffer.capture(UTC + 2.65, UTC + 4.35, output)
        self.assert_markers(result, output)


if __name__ == "__main__":
    unittest.main()
