from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from mobile_capture import (
    MobileCaptureError,
    create_mobile_capture,
    get_mobile_capture_image,
    get_mobile_capture_status,
    save_mobile_capture_upload,
)


class FakeUpload:
    def __init__(self, data: bytes, name: str = "photo.jpg") -> None:
        self._data = data
        self.name = name

    def getbuffer(self):
        return memoryview(self._data)


class MobileCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.capture_path = root / "mobile_captures.json"
        self.image_dir = root / "images"
        self.patches = [
            patch("mobile_capture.CAPTURE_PATH", self.capture_path),
            patch("mobile_capture.CAPTURE_IMAGE_DIR", self.image_dir),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp_dir.cleanup()

    def test_create_token_binds_owner_source_and_expiry(self):
        now = datetime(2026, 6, 3, 12, 0, 0)
        with patch("mobile_capture._now", return_value=now):
            capture = create_mobile_capture("alice", "correction")

        self.assertEqual(capture["owner_username"], "alice")
        self.assertEqual(capture["source_key"], "correction")
        self.assertEqual(capture["created_at"], "2026-06-03T12:00:00")
        self.assertEqual(capture["expires_at"], "2026-06-03T12:10:00")

    def test_expired_token_cannot_upload_or_read(self):
        now = datetime(2026, 6, 3, 12, 0, 0)
        with patch("mobile_capture._now", return_value=now):
            capture = create_mobile_capture("alice", "correction")

        expired_time = now + timedelta(minutes=11)
        with patch("mobile_capture._now", return_value=expired_time):
            self.assertIsNone(get_mobile_capture_status(capture["token"]))
            with self.assertRaises(MobileCaptureError):
                save_mobile_capture_upload(capture["token"], FakeUpload(b"data"))
            self.assertIsNone(get_mobile_capture_image(capture["token"], "alice"))

    def test_token_cannot_upload_twice(self):
        capture = create_mobile_capture("alice", "correction")

        save_mobile_capture_upload(capture["token"], FakeUpload(b"first"))
        with self.assertRaises(MobileCaptureError):
            save_mobile_capture_upload(capture["token"], FakeUpload(b"second"))

    def test_other_user_cannot_read_mobile_photo(self):
        capture = create_mobile_capture("alice", "correction")
        save_mobile_capture_upload(capture["token"], FakeUpload(b"image"))

        self.assertIsNone(get_mobile_capture_image(capture["token"], "bob"))

    def test_mobile_upload_wraps_as_image_input(self):
        capture = create_mobile_capture("alice", "correction")
        save_mobile_capture_upload(capture["token"], FakeUpload(b"image-bytes", "homework.png"))

        image = get_mobile_capture_image(capture["token"], "alice")

        self.assertIsNotNone(image)
        self.assertEqual(bytes(image.getbuffer()), b"image-bytes")
        self.assertTrue(image.name.endswith(".png"))


if __name__ == "__main__":
    unittest.main()
