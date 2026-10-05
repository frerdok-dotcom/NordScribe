"""Тесты сетевого слоя: адреса, хеши, скачивание, докачка, целостность кэша.

Сеть не нужна: поднимается локальный HTTP-сервер, который умеет отдавать
файл целиком, поддерживать Range, игнорировать Range и обрывать соединение.
"""

from __future__ import annotations

import hashlib
import http.server
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import download_manager as dm
from tests import make_temp_dir, remove_temp_dir

PAYLOAD = b"NordScribe-model-bytes" * 8000
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()
BAD_DIGEST = "0" * 64


class _Handler(http.server.BaseHTTPRequestHandler):
    """Отдаёт PAYLOAD с разным поведением в зависимости от первого сегмента."""

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
            # Делаем вид, что Range не поддерживается: отдаём всё с 200.
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
    """Поднимает локальный сервер на время всего модуля."""
    global _server, _base
    _server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    _base = f"http://127.0.0.1:{_server.server_address[1]}"
    threading.Thread(target=_server.serve_forever, daemon=True).start()


def tearDownModule() -> None:
    _server.shutdown()
    _server.server_close()


def url_for(segment: str, filename: str, digest: str = DIGEST) -> str:
    """Собирает URL так, как его понимает download_whisper_model."""
    return f"{_base}/{segment}/{digest}/{filename}"


class BaseCase(unittest.TestCase):
    """Общая песочница: свой каталог загрузок на каждый тест."""

    def setUp(self) -> None:
        self._tmp = make_temp_dir()
        self.dest = self._tmp

    def tearDown(self) -> None:
        remove_temp_dir(self._tmp)

    def assert_no_parts(self) -> None:
        self.assertEqual(list(self.dest.glob("*.part")), [])


class AddressesTest(unittest.TestCase):
    """Адреса, хеши и имена файлов моделей."""

    def test_every_model_has_url_and_digest(self) -> None:
        for model in dm.WHISPER_MODELS:
            with self.subTest(model=model):
                self.assertIsNotNone(dm.get_model_url(model))
                self.assertIsNotNone(dm.expected_sha256(model))

    def test_large_maps_to_large_v3_file(self) -> None:
        # Именно под этим именем файл ищет whisper.load_model("large").
        path = dm.model_file_path("large", Path("C:/x"))
        self.assertEqual(path.name, "large-v3.pt")

    def test_other_models_use_plain_names(self) -> None:
        self.assertEqual(dm.model_file_path("tiny", Path("C:/x")).name, "tiny.pt")
        self.assertEqual(dm.model_file_path("small", Path("C:/x")).name, "small.pt")

    def test_unknown_model_has_nothing(self) -> None:
        self.assertIsNone(dm.get_model_url("нет-такой"))
        self.assertIsNone(dm.model_file_path("нет-такой", Path("C:/x")))
        self.assertIsNone(dm.expected_sha256("нет-такой"))


class FormatBytesTest(unittest.TestCase):
    """Человекочитаемые размеры."""

    def test_scales(self) -> None:
        cases = {
            0: "0 Б",
            999: "999 Б",
            2048: "2 КБ",
            461234567: "440 МБ",
            3_100_000_000: "2.9 ГБ",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(dm.format_bytes(value), expected)


class Sha256Test(BaseCase):
    """Подсчёт контрольной суммы."""

    def test_matches_hashlib(self) -> None:
        target = self.dest / "sample.bin"
        target.write_bytes(PAYLOAD)
        self.assertEqual(dm.sha256_of_file(target), DIGEST)

    def test_cancellation_stops_hashing(self) -> None:
        target = self.dest / "sample.bin"
        target.write_bytes(PAYLOAD)
        with self.assertRaises(dm.DownloadCancelled):
            dm.sha256_of_file(target, chunk_size=64, is_cancelled=lambda: True)


class DownloadSuccessTest(BaseCase):
    """Обычное скачивание без обрывов."""

    def test_downloads_and_verifies(self) -> None:
        progress: list[tuple[int, int]] = []
        result = dm.download_whisper_model(
            url_for("plain", "model.pt"),
            self.dest,
            on_progress=lambda d, t: progress.append((d, t)),
        )
        self.assertEqual(result.read_bytes(), PAYLOAD)
        self.assert_no_parts()
        self.assertEqual(progress[-1], (len(PAYLOAD), len(PAYLOAD)))

    def test_progress_is_throttled(self) -> None:
        many: list[tuple[int, int]] = []
        dm.download_whisper_model(
            url_for("plain", "a.pt"),
            self.dest,
            on_progress=lambda d, t: many.append((d, t)),
            chunk_size=2048,
            progress_interval=0.0,
        )
        few: list[tuple[int, int]] = []
        dm.download_whisper_model(
            url_for("plain", "b.pt"),
            self.dest,
            on_progress=lambda d, t: few.append((d, t)),
            chunk_size=2048,
            progress_interval=60.0,
        )
        self.assertGreater(len(many), 20)
        self.assertLessEqual(len(few), 3)
        self.assertGreater(len(many), len(few) * 5)


class DownloadResumeTest(BaseCase):
    """Докачка недокачанного файла."""

    def _seed_part(self, filename: str, size: int) -> Path:
        part = self.dest / f"{filename}.part"
        part.write_bytes(PAYLOAD[:size])
        return part

    def test_resumes_from_existing_part(self) -> None:
        offset = len(PAYLOAD) // 3
        self._seed_part("model.pt", offset)
        progress: list[tuple[int, int]] = []
        result = dm.download_whisper_model(
            url_for("plain", "model.pt"),
            self.dest,
            on_progress=lambda d, t: progress.append((d, t)),
        )
        self.assertEqual(result.read_bytes(), PAYLOAD)
        self.assert_no_parts()
        # Прогресс начинается не с нуля, а с уже скачанного объёма.
        self.assertEqual(progress[0][0], offset)
        self.assertEqual(progress[0][1], len(PAYLOAD))

    def test_restarts_when_server_ignores_range(self) -> None:
        self._seed_part("model.pt", len(PAYLOAD) // 2)
        result = dm.download_whisper_model(url_for("norange", "model.pt"), self.dest)
        self.assertEqual(result.read_bytes(), PAYLOAD)
        self.assert_no_parts()

    def test_resume_can_be_disabled(self) -> None:
        self._seed_part("model.pt", len(PAYLOAD) // 2)
        progress: list[tuple[int, int]] = []
        dm.download_whisper_model(
            url_for("plain", "model.pt"),
            self.dest,
            on_progress=lambda d, t: progress.append((d, t)),
            allow_resume=False,
        )
        self.assertEqual(progress[0][0], 0)


class DownloadErrorTest(BaseCase):
    """Политика удаления .part при разных сбоях."""

    def test_incomplete_keeps_part_for_resume(self) -> None:
        with self.assertRaises(dm.IncompleteDownload) as ctx:
            dm.download_whisper_model(url_for("truncated", "t.pt"), self.dest)
        self.assertIn("продолжится", str(ctx.exception))
        part = self.dest / "t.pt.part"
        self.assertTrue(part.exists(), "частичный файл должен остаться для докачки")
        self.assertEqual(part.read_bytes(), PAYLOAD[: len(PAYLOAD) // 2])

    def test_checksum_error_removes_part(self) -> None:
        with self.assertRaises(dm.ChecksumError):
            dm.download_whisper_model(url_for("plain", "bad.pt", digest=BAD_DIGEST), self.dest)
        self.assertFalse((self.dest / "bad.pt").exists())
        self.assert_no_parts()

    def test_http_error_removes_part(self) -> None:
        url = f"{_base}/missing/{DIGEST}/none.pt"
        with self.assertRaises(Exception) as ctx:
            dm.download_whisper_model(url, self.dest)
        self.assertIn("404", dm.describe_download_error(ctx.exception))
        self.assert_no_parts()

    def test_no_network_reports_clearly(self) -> None:
        url = f"http://127.0.0.1:9/none/{DIGEST}/model.pt"
        with self.assertRaises(Exception) as ctx:
            dm.download_whisper_model(url, self.dest)
        self.assertIn("нет соединения", dm.describe_download_error(ctx.exception))

    def test_cancel_removes_part(self) -> None:
        calls = {"n": 0}

        def on_progress(done: int, total: int) -> None:
            calls["n"] += 1
            calls["cancel"] = calls["n"] >= 2

        calls["cancel"] = False
        with self.assertRaises(dm.DownloadCancelled):
            dm.download_whisper_model(
                url_for("slow", "slow.pt"),
                self.dest,
                on_progress=on_progress,
                is_cancelled=lambda: calls["cancel"],
                progress_interval=0.0,
                chunk_size=2048,
            )
        self.assert_no_parts()
        self.assertFalse((self.dest / "slow.pt").exists())


class DescribeErrorTest(unittest.TestCase):
    """Тексты ошибок для пользователя."""

    def test_checksum_message_is_passed_through(self) -> None:
        text = dm.describe_download_error(dm.ChecksumError("файл повреждён"))
        self.assertEqual(text, "файл повреждён")

    def test_incomplete_message_is_passed_through(self) -> None:
        text = dm.describe_download_error(dm.IncompleteDownload("обрыв"))
        self.assertEqual(text, "обрыв")

    def test_timeout_is_named(self) -> None:
        self.assertIn("время", dm.describe_download_error(TimeoutError()))

    def test_unknown_error_falls_back_to_class_name(self) -> None:
        class Weird(Exception):
            pass

        self.assertEqual(dm.describe_download_error(Weird()), "Weird")


class CacheTest(BaseCase):
    """Проверка целостности и удаление файлов в кэше."""

    def test_missing_file_gives_none(self) -> None:
        with mock.patch.object(dm, "WHISPER_CACHE_DIR", self.dest):
            self.assertIsNone(dm.cached_model_is_valid("tiny"))

    def test_matching_hash_is_true(self) -> None:
        (self.dest / "tiny.pt").write_bytes(PAYLOAD)
        with (
            mock.patch.object(dm, "WHISPER_CACHE_DIR", self.dest),
            mock.patch.object(dm, "expected_sha256", return_value=DIGEST),
        ):
            self.assertTrue(dm.cached_model_is_valid("tiny"))

    def test_mismatched_hash_is_false(self) -> None:
        (self.dest / "tiny.pt").write_bytes(b"truncated-weights")
        with (
            mock.patch.object(dm, "WHISPER_CACHE_DIR", self.dest),
            mock.patch.object(dm, "expected_sha256", return_value=DIGEST),
        ):
            self.assertFalse(dm.cached_model_is_valid("tiny"))

    def test_remove_deletes_model_and_part(self) -> None:
        (self.dest / "tiny.pt").write_bytes(b"x")
        (self.dest / "tiny.pt.part").write_bytes(b"y")
        with mock.patch.object(dm, "WHISPER_CACHE_DIR", self.dest):
            removed = dm.remove_model_file("tiny")
        self.assertEqual(sorted(removed), ["tiny.pt", "tiny.pt.part"])
        self.assertFalse((self.dest / "tiny.pt").exists())

    def test_remove_on_empty_cache_is_noop(self) -> None:
        with mock.patch.object(dm, "WHISPER_CACHE_DIR", self.dest):
            self.assertEqual(dm.remove_model_file("tiny"), [])


class ScannedModelsTest(BaseCase):
    """Сканирование кэша."""

    def test_recognises_large_v3_as_large(self) -> None:
        (self.dest / "large-v3.pt").write_bytes(b"x")
        found = dm.get_downloaded_whisper_models(self.dest)
        self.assertIn("large-v3", found)
        self.assertIn("large", found)

    def test_ignores_unrelated_files(self) -> None:
        (self.dest / "случайный.pt").write_bytes(b"x")
        (self.dest / "notes.txt").write_text("x", encoding="utf-8")
        self.assertEqual(dm.get_downloaded_whisper_models(self.dest), set())

    def test_missing_directory_is_empty(self) -> None:
        self.assertEqual(dm.get_downloaded_whisper_models(self.dest / "нет-такого"), set())


if __name__ == "__main__":
    unittest.main()
