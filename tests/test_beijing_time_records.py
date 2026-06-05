import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import auth
import storage
import task_queue
import worker
from mobile_capture import create_mobile_capture


FIXED_UTC_NOW = datetime(2026, 6, 5, 2, 20, 17, tzinfo=timezone.utc)


class BeijingRecordTimeTests(unittest.TestCase):
    def test_auth_json_created_at_uses_beijing_time(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            users_path = Path(temp_dir) / "data" / "users.json"
            with (
                patch.object(auth, "USERS_PATH", users_path),
                patch.object(auth, "ensure_runtime_dirs", lambda: users_path.parent.mkdir(parents=True, exist_ok=True)),
                patch.object(auth, "is_mysql_enabled", return_value=False),
                patch("time_utils._utc_now", return_value=FIXED_UTC_NOW),
            ):
                auth.register_user("alice", "secret1")

            payload = json.loads(users_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["users"][0]["created_at"], "2026-06-05T10:20:17")

    def test_storage_json_saved_at_uses_beijing_time(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result_dir = Path(temp_dir) / "results"
            with (
                patch.object(storage, "RESULT_DIR", result_dir),
                patch.object(storage, "ensure_runtime_dirs", lambda: result_dir.mkdir(parents=True, exist_ok=True)),
                patch.object(storage, "is_mysql_enabled", return_value=False),
                patch("time_utils._utc_now", return_value=FIXED_UTC_NOW),
            ):
                storage.save_result("task-1", {"owner_username": "alice", "status": "finished"})

            payload = json.loads((result_dir / "task-1.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["saved_at"], "2026-06-05T10:20:17")

    def test_task_queue_status_and_failed_result_use_beijing_time(self):
        local_status = {}
        with (
            patch.object(task_queue, "_task_status", local_status),
            patch("time_utils._utc_now", return_value=FIXED_UTC_NOW),
        ):
            task_queue.update_task_status("task-1", status="waiting")
            failed = task_queue._failed_result("task-1", "image.jpg", "alice", "boom")

        self.assertEqual(local_status["task-1"]["created_at"], "2026-06-05T10:20:17")
        self.assertEqual(local_status["task-1"]["updated_at"], "2026-06-05T10:20:17")
        self.assertEqual(failed["finished_at"], "2026-06-05T10:20:17")

    def test_worker_finished_at_uses_beijing_time(self):
        correction = {
            "questions": [],
            "score": 0,
            "summary": "",
            "comments": "",
            "suggestions": "",
        }
        with (
            patch("worker._prepare_ocr_image", return_value={"image_path": "enhanced.jpg", "processing": {}}),
            patch(
                "worker.recognize_question_split",
                return_value={"question_count": 1, "ocr_text": "1. 1+1", "questions": []},
            ),
            patch("worker.correct_homework", return_value=correction),
            patch("worker._create_annotation_image", return_value=None),
            patch("worker.create_preview_image", return_value="preview.jpg"),
            patch("worker.save_result"),
            patch("time_utils._utc_now", return_value=FIXED_UTC_NOW),
        ):
            result = worker.process_homework("task-1", "image.jpg", "alice")

        self.assertEqual(result["finished_at"], "2026-06-05T10:20:17")

    def test_mobile_capture_uses_beijing_time_for_ttl(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            capture_path = Path(temp_dir) / "mobile_captures.json"
            image_dir = Path(temp_dir) / "images"
            with (
                patch("mobile_capture.CAPTURE_PATH", capture_path),
                patch("mobile_capture.CAPTURE_IMAGE_DIR", image_dir),
                patch("time_utils._utc_now", return_value=FIXED_UTC_NOW),
            ):
                capture = create_mobile_capture("alice", "correction")

        self.assertEqual(capture["created_at"], "2026-06-05T10:20:17")
        self.assertEqual(capture["expires_at"], "2026-06-05T10:30:17")


if __name__ == "__main__":
    unittest.main()
