"""Tests for the HTTP model downloader.

No external network: a local HTTP server serves a payload and can truncate the
response, ignore Range headers, or stream slowly for cancellation tests.
"""

from __future__ import annotations

import hashlib
import http.server
import threading
import time
import unittest
from pathlib import Path

from nordscribe.core.errors import (
    ChecksumError,
    DownloadCancelled,
    DownloadError,
    IncompleteDownload,
)
from nordscribe.services.downloader import describe_download_error, download_file
from tests import make_temp_dir, remove_temp_dir

PAYLOAD = b"NordScribe-model-bytes" * 8000
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()
BAD_DIGEST = "0" * 64


class _Handler(http.server.BaseHTTPRequestHandler):
    """Serve PAYLOAD with behaviour selected by the first URL segment."""

    def log_message(self, *args: object) -> None:
        pass

    def do_GET(self) -> None:
        segment = self.path.strip("/").split("/")[0]

        if segment == "missing":
            self.send_error(404, "File not found")
            return

        if segment == "truncated":
            self.send_response(200)
            self.send_header("Content-Length", str(len(PAYLOAD) + 50000))
            self.end_headers()
            self.wfile.write(PAYLOAD[: len(PAYLOAD) // 2])
            return

        if segment == "slow":
            self.send_response(200)
            self.send_header("Content-Length", str(len(PAYLOAD)))
            self.end_headers()
            for offset in range(0, len(PAYLOAD), 2048):
                try:
                    self.wfile.write(PAYLOAD[offset : offset + 2048])
                except OSError:
                    return
                time.sleep(0.02)
            return

        if segment == "norange":
            # Pretend Range is unsupported: send the whole file with 200.
            self.send_response(200)
            self.send_header("Content-Length", str(len(PAYLOAD)))
            self.end_headers()
            self.wfile.write(PAYLOAD)
            return

        start = 0
        header = self.headers.get("Range") or ""
        if header.startswith("bytes="):
            try:
                start = int(header.split("=", 1)[1].split("-")[0])
            except ValueError:
                start = 0
        body = PAYLOAD[start:]
        if start:
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(PAYLOAD) - 1}/{len(PAYLOAD)}")
        else:
            self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


_server: http.server.ThreadingHTTPServer
_base = ""


def setUpModule() -> None:
    global _server, _base
    _server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    _base = f"http://127.0.0.1:{_server.server_address[1]}"
    threading.Thread(target=_server.serve_forever, daemon=True).start()


def tearDownModule() -> None:
    _server.shutdown()
    _server.server_close()


def url_for(segment: str, filename: str, digest: str = DIGEST) -> str:
    """Build a URL the downloader understands."""
    return f"{_base}/{segment}/{digest}/{filename}"


class BaseCase(unittest.TestCase):
    """A fresh destination directory per test."""

    def setUp(self) -> None:
        self._tmp = make_temp_dir()
        self.dest = self._tmp

    def tearDown(self) -> None:
        remove_temp_dir(self._tmp)

    def assert_no_parts(self) -> None:
        self.assertEqual(list(self.dest.glob("*.part")), [])


class DownloadSuccessTest(BaseCase):
    """Normal download and progress throttling."""

    def test_downloads_and_verifies(self) -> None:
        progress: list[tuple[int, int]] = []
        result = download_file(
            url_for("plain", "model.pt"),
            self.dest,
            on_progress=lambda done, total: progress.append((done, total)),
        )
        self.assertEqual(result.read_bytes(), PAYLOAD)
        self.assert_no_parts()
        self.assertEqual(progress[-1], (len(PAYLOAD), len(PAYLOAD)))

    def test_progress_is_throttled(self) -> None:
        many: list[tuple[int, int]] = []
        download_file(
            url_for("plain", "a.pt"),
            self.dest,
            on_progress=lambda done, total: many.append((done, total)),
            chunk_size=2048,
            progress_interval=0.0,
        )
        few: list[tuple[int, int]] = []
        download_file(
            url_for("plain", "b.pt"),
            self.dest,
            on_progress=lambda done, total: few.append((done, total)),
            chunk_size=2048,
            progress_interval=60.0,
        )
        self.assertGreater(len(many), 20)
        self.assertLessEqual(len(few), 3)
        self.assertGreater(len(many), len(few) * 5)

    def test_creates_destination_directory(self) -> None:
        nested = self.dest / "a" / "b"
        result = download_file(url_for("plain", "model.pt"), nested)
        self.assertTrue(result.is_file())


class DownloadResumeTest(BaseCase):
    """Resume from an existing .part file."""

    def _seed_part(self, filename: str, size: int) -> Path:
        part = self.dest / f"{filename}.part"
        part.write_bytes(PAYLOAD[:size])
        return part

    def test_resumes_from_existing_part(self) -> None:
        offset = len(PAYLOAD) // 3
        self._seed_part("model.pt", offset)
        progress: list[tuple[int, int]] = []
        result = download_file(
            url_for("plain", "model.pt"),
            self.dest,
            on_progress=lambda done, total: progress.append((done, total)),
        )
        self.assertEqual(result.read_bytes(), PAYLOAD)
        self.assert_no_parts()
        self.assertEqual(progress[0][0], offset)
        self.assertEqual(progress[0][1], len(PAYLOAD))

    def test_restarts_when_server_ignores_range(self) -> None:
        self._seed_part("model.pt", len(PAYLOAD) // 2)
        result = download_file(url_for("norange", "model.pt"), self.dest)
        self.assertEqual(result.read_bytes(), PAYLOAD)
        self.assert_no_parts()

    def test_resume_can_be_disabled(self) -> None:
        self._seed_part("model.pt", len(PAYLOAD) // 2)
        progress: list[tuple[int, int]] = []
        download_file(
            url_for("plain", "model.pt"),
            self.dest,
            on_progress=lambda done, total: progress.append((done, total)),
            allow_resume=False,
        )
        self.assertEqual(progress[0][0], 0)


class DownloadErrorTest(BaseCase):
    """Cleanup policy for each failure mode."""

    def test_incomplete_keeps_part_for_resume(self) -> None:
        with self.assertRaises(IncompleteDownload) as ctx:
            download_file(url_for("truncated", "t.pt"), self.dest)
        self.assertIn("продолжится", str(ctx.exception))
        part = self.dest / "t.pt.part"
        self.assertTrue(part.exists(), "partial file must remain for resume")
        self.assertEqual(part.read_bytes(), PAYLOAD[: len(PAYLOAD) // 2])

    def test_checksum_error_removes_part(self) -> None:
        with self.assertRaises(ChecksumError):
            download_file(url_for("plain", "bad.pt", digest=BAD_DIGEST), self.dest)
        self.assertFalse((self.dest / "bad.pt").exists())
        self.assert_no_parts()

    def test_http_error_removes_part(self) -> None:
        url = f"{_base}/missing/{DIGEST}/none.pt"
        with self.assertRaises(Exception) as ctx:
            download_file(url, self.dest)
        self.assertIn("404", describe_download_error(ctx.exception))
        self.assert_no_parts()

    def test_no_network_reports_clearly(self) -> None:
        url = f"http://127.0.0.1:9/none/{DIGEST}/model.pt"
        with self.assertRaises(Exception) as ctx:
            download_file(url, self.dest)
        self.assertIn("нет соединения", describe_download_error(ctx.exception))

    def test_cancel_removes_part(self) -> None:
        calls = {"n": 0}
        cancel = {"value": False}

        def on_progress(done: int, total: int) -> None:
            calls["n"] += 1
            cancel["value"] = calls["n"] >= 2

        with self.assertRaises(DownloadCancelled):
            download_file(
                url_for("slow", "slow.pt"),
                self.dest,
                on_progress=on_progress,
                is_cancelled=lambda: cancel["value"],
                progress_interval=0.0,
                chunk_size=2048,
            )
        self.assert_no_parts()
        self.assertFalse((self.dest / "slow.pt").exists())

    def test_url_without_filename_is_rejected(self) -> None:
        with self.assertRaises(DownloadError):
            download_file(f"{_base}/", self.dest)


class DescribeErrorTest(unittest.TestCase):
    """Messages shown to the user."""

    def test_checksum_message_is_passed_through(self) -> None:
        self.assertEqual(
            describe_download_error(ChecksumError("файл повреждён")),
            "файл повреждён",
        )

    def test_incomplete_message_is_passed_through(self) -> None:
        self.assertEqual(
            describe_download_error(IncompleteDownload("обрыв")),
            "обрыв",
        )

    def test_timeout_is_named(self) -> None:
        self.assertIn("время", describe_download_error(TimeoutError()))

    def test_unknown_error_falls_back_to_class_name(self) -> None:
        class Weird(Exception):
            pass

        self.assertEqual(describe_download_error(Weird()), "Weird")
