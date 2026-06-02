import io
import os
import queue
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import runtime_cleanup
import task_queue


class RuntimeCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.transient_dirs = tuple(
            self.root / name for name in ("uploads", "processed", "cuts", "debug")
        )
        for directory in self.transient_dirs:
            directory.mkdir(parents=True)

        self.previous_cleanup_at = runtime_cleanup._last_cleanup_at
        runtime_cleanup._last_cleanup_at = 0.0
        self.patches = [
            patch.object(runtime_cleanup, "TRANSIENT_DIRS", self.transient_dirs),
            patch.object(runtime_cleanup, "ensure_runtime_dirs", self._ensure_runtime_dirs),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        runtime_cleanup._last_cleanup_at = self.previous_cleanup_at
        self.temp_dir.cleanup()

    def _ensure_runtime_dirs(self):
        for directory in self.transient_dirs:
            directory.mkdir(parents=True, exist_ok=True)

    def test_cleanup_recursively_deletes_only_expired_files(self):
        fresh_file = self.transient_dirs[0] / "fresh.jpg"
        fresh_file.write_text("fresh", encoding="utf-8")

        nested_dir = self.transient_dirs[3] / "llm_cache" / "old"
        nested_dir.mkdir(parents=True)
        expired_file = nested_dir / "expired.json"
        expired_file.write_text("expired", encoding="utf-8")
        expired_at = time.time() - (25 * 3600)
        os.utime(expired_file, (expired_at, expired_at))

        keep_file = self.transient_dirs[2] / ".gitkeep"
        keep_file.write_text("", encoding="utf-8")

        result = runtime_cleanup.cleanup_runtime_files(force=True)

        self.assertEqual(result, {"deleted": 1, "scanned": 2})
        self.assertTrue(fresh_file.exists())
        self.assertFalse(expired_file.exists())
        self.assertFalse(nested_dir.exists())
        self.assertTrue(keep_file.exists())
        for directory in self.transient_dirs:
            self.assertTrue(directory.exists())

    def test_cleanup_has_no_file_count_limit(self):
        upload_dir = self.transient_dirs[0]
        for index in range(130):
            (upload_dir / f"fresh_{index}.jpg").write_text("fresh", encoding="utf-8")

        result = runtime_cleanup.cleanup_runtime_files(force=True)

        self.assertEqual(result, {"deleted": 0, "scanned": 130})
        self.assertEqual(len(list(upload_dir.glob("*.jpg"))), 130)

    def test_results_directory_is_not_cleaned(self):
        result_dir = self.root / "results"
        result_dir.mkdir()
        result_file = result_dir / "historical.json"
        result_file.write_text("{}", encoding="utf-8")
        expired_at = time.time() - (25 * 3600)
        os.utime(result_file, (expired_at, expired_at))

        runtime_cleanup.cleanup_runtime_files(force=True)

        self.assertTrue(result_file.exists())


class TaskSubmissionTests(unittest.TestCase):
    def test_submitting_new_task_keeps_fresh_existing_upload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            upload_dir = Path(temp_dir) / "uploads"
            upload_dir.mkdir()
            existing_upload = upload_dir / "running-task.jpg"
            existing_upload.write_bytes(b"still in use")

            new_upload = io.BytesIO(b"new task")
            new_upload.name = "new-task.jpg"
            local_queue = queue.Queue()
            local_status = {}

            with (
                patch.object(task_queue, "UPLOAD_DIR", upload_dir),
                patch.object(task_queue, "ensure_runtime_dirs", lambda: None),
                patch.object(task_queue, "start_workers", lambda: None),
                patch.object(task_queue, "_task_queue", local_queue),
                patch.object(task_queue, "_task_status", local_status),
                patch.object(runtime_cleanup, "TRANSIENT_DIRS", (upload_dir,)),
                patch.object(runtime_cleanup, "ensure_runtime_dirs", lambda: None),
            ):
                previous_cleanup_at = runtime_cleanup._last_cleanup_at
                runtime_cleanup._last_cleanup_at = 0.0
                try:
                    task_id = task_queue.submit_task(new_upload)
                finally:
                    runtime_cleanup._last_cleanup_at = previous_cleanup_at

            self.assertTrue(existing_upload.exists())
            self.assertTrue((upload_dir / f"{task_id}.jpg").exists())
            self.assertEqual(local_queue.get_nowait()["task_id"], task_id)


class RuntimePathTests(unittest.TestCase):
    def test_empty_runtime_dir_uses_project_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env = os.environ.copy()
            env["APP_RUNTIME_DIR"] = ""
            project_dir = Path(__file__).resolve().parents[1]
            script = f"""
import sys
sys.path.insert(0, {str(project_dir)!r})
import config
assert config.RUNTIME_DIR == config.BASE_DIR
"""
            subprocess.run(
                [sys.executable, "-c", script],
                cwd=temp_dir,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )

    def test_runtime_dir_controls_cut_and_debug_outputs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env = os.environ.copy()
            env["APP_RUNTIME_DIR"] = temp_dir
            script = """
from pathlib import Path
from PIL import Image
import config
import llm_corrector
import paper_cut_tencent

config.ensure_runtime_dirs()
assert paper_cut_tencent.CUT_DIR == config.CUT_DIR
assert llm_corrector.DEBUG_DIR == config.DEBUG_DIR

image_path = Path(config.RUNTIME_DIR) / "input.png"
Image.new("RGB", (20, 20), "white").save(image_path)
raw = {"Response": {"QuestionInfo": [{"ResultList": [{"Question": [{"Text": "1+1"}]}]}]}}
result = paper_cut_tencent.normalize_question_split_response(
    raw,
    image_path=str(image_path),
    task_id="runtime-path",
)
llm_corrector._save_debug_response("runtime_path", {"messages": []}, "{}")

assert Path(result["raw_path"]).parent == config.CUT_DIR
assert Path(result["raw_path"]).exists()
assert list(config.DEBUG_DIR.glob("runtime_path_*.json"))
"""
            subprocess.run(
                [sys.executable, "-c", script],
                cwd=Path(__file__).resolve().parents[1],
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )


if __name__ == "__main__":
    unittest.main()
