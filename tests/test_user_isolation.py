import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import storage
import task_queue
import worker


class StorageIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.result_dir = Path(self.temp_dir.name) / "results"
        self.result_dir.mkdir()
        self.patches = [
            patch.object(storage, "RESULT_DIR", self.result_dir),
            patch.object(storage, "ensure_runtime_dirs", lambda: self.result_dir.mkdir(parents=True, exist_ok=True)),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp_dir.cleanup()

    def test_results_are_filtered_by_owner_and_legacy_results_are_hidden(self):
        storage.save_result("alice-task", {"status": "finished", "owner_username": "alice"})
        storage.save_result("bob-task", {"status": "finished", "owner_username": "bob"})
        storage.save_result("legacy-task", {"status": "finished"})

        alice_results = storage.list_results(owner_username="alice")

        self.assertEqual([item["task_id"] for item in alice_results], ["alice-task"])
        self.assertIsNone(storage.load_result("bob-task", owner_username="alice"))
        self.assertIsNone(storage.load_result("legacy-task", owner_username="alice"))


class TaskQueueIsolationTests(unittest.TestCase):
    def test_task_status_and_list_are_filtered_by_owner(self):
        local_status = {
            "alice-task": {"task_id": "alice-task", "owner_username": "alice", "status": "waiting"},
            "bob-task": {"task_id": "bob-task", "owner_username": "bob", "status": "waiting"},
        }
        with (
            patch.object(task_queue, "_task_status", local_status),
            patch("task_queue.load_result", return_value=None),
        ):
            tasks = task_queue.list_tasks(owner_username="alice")
            hidden_status = task_queue.get_task_status("bob-task", owner_username="alice")

        self.assertEqual([item["task_id"] for item in tasks], ["alice-task"])
        self.assertEqual(hidden_status, {"task_id": "bob-task", "status": "unknown"})

    def test_terminal_task_without_result_is_hidden(self):
        local_status = {
            "finished-task": {
                "task_id": "finished-task",
                "owner_username": "alice",
                "status": "finished",
            },
            "running-task": {
                "task_id": "running-task",
                "owner_username": "alice",
                "status": "running",
            },
        }
        with (
            patch.object(task_queue, "_task_status", local_status),
            patch("task_queue.load_result", return_value=None),
        ):
            tasks = task_queue.list_tasks(owner_username="alice")
            finished_status = task_queue.get_task_status("finished-task", owner_username="alice")

        self.assertEqual([item["task_id"] for item in tasks], ["running-task"])
        self.assertEqual(finished_status, {"task_id": "finished-task", "status": "unknown"})

    def test_failed_result_keeps_owner(self):
        result = task_queue._failed_result("demo", "image.jpg", "alice", "failed")

        self.assertEqual(result["owner_username"], "alice")
        self.assertEqual(result["status"], "failed")


class WorkerIsolationTests(unittest.TestCase):
    def test_success_result_keeps_owner(self):
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
            patch("worker.save_result") as save_result,
        ):
            result = worker.process_homework("demo", "image.jpg", "alice")

        self.assertEqual(result["owner_username"], "alice")
        self.assertEqual(save_result.call_args.args[1]["owner_username"], "alice")


if __name__ == "__main__":
    unittest.main()
