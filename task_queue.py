from __future__ import annotations

import queue
import shutil
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from config import UPLOAD_DIR, ensure_runtime_dirs, get_int_setting
from runtime_cleanup import cleanup_runtime_files
from storage import load_result, save_result


_task_queue: queue.Queue[dict[str, str]] = queue.Queue()
_task_status: dict[str, dict[str, Any]] = {}
_status_lock = threading.RLock()
_workers: list[threading.Thread] = []
_started = False


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _safe_extension(filename: str | None) -> str:
    if not filename:
        return ".png"
    suffix = Path(filename).suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
        return suffix
    return ".png"


def _save_upload(image_file: Any, task_id: str) -> str:
    ensure_runtime_dirs()
    extension = _safe_extension(getattr(image_file, "name", None))
    target = UPLOAD_DIR / f"{task_id}{extension}"

    if hasattr(image_file, "getbuffer"):
        target.write_bytes(bytes(image_file.getbuffer()))
        return str(target)

    if hasattr(image_file, "read"):
        with target.open("wb") as output:
            shutil.copyfileobj(image_file, output)
        return str(target)

    if isinstance(image_file, (str, Path)):
        source = Path(image_file)
        extension = _safe_extension(source.name)
        target = UPLOAD_DIR / f"{task_id}{extension}"
        shutil.copy2(source, target)
        return str(target)

    raise TypeError("不支持的图片上传对象。")


def update_task_status(task_id: str, **updates: Any) -> None:
    with _status_lock:
        current = _task_status.setdefault(
            task_id,
            {
                "task_id": task_id,
                "status": "waiting",
                "created_at": _now(),
                "updated_at": _now(),
                "image_path": "",
                "error": None,
            },
        )
        current.update(updates)
        current["updated_at"] = _now()


def submit_task(image_file: Any) -> str:
    start_workers()
    cleanup_runtime_files(force=True)
    task_id = str(uuid.uuid4())
    image_path = _save_upload(image_file, task_id)
    update_task_status(
        task_id,
        status="waiting",
        image_path=image_path,
        error=None,
        result_path=None,
    )
    _task_queue.put({"task_id": task_id, "image_path": image_path})
    return task_id


def get_task_status(task_id: str) -> dict[str, Any]:
    with _status_lock:
        status = dict(_task_status.get(task_id, {"task_id": task_id, "status": "unknown"}))

    result = load_result(task_id)
    if result:
        status.update(
            {
                "status": result.get("status", status.get("status")),
                "score": result.get("score"),
                "error": result.get("error"),
                "image_path": result.get("image_path", status.get("image_path")),
                "result_saved": True,
            }
        )
    return status


def list_tasks() -> list[dict[str, Any]]:
    with _status_lock:
        tasks = [dict(item) for item in _task_status.values()]
    return sorted(tasks, key=lambda item: item.get("created_at", ""), reverse=True)


def start_workers(max_workers: int | None = None) -> None:
    global _started

    with _status_lock:
        if _started:
            return

        worker_count = max_workers or get_int_setting("MAX_WORKERS", 3)
        worker_count = max(1, min(worker_count, 8))
        for index in range(worker_count):
            thread = threading.Thread(
                target=_worker_loop,
                name=f"homework-worker-{index + 1}",
                daemon=True,
            )
            thread.start()
            _workers.append(thread)
        _started = True


def _worker_loop() -> None:
    from worker import process_homework

    while True:
        task = _task_queue.get()
        task_id = task["task_id"]
        image_path = task["image_path"]
        try:
            update_task_status(task_id, status="running", error=None)
            result = process_homework(task_id, image_path)
            update_task_status(
                task_id,
                status="finished",
                score=result.get("score"),
                error=None,
                result_path=f"results/{task_id}.json",
            )
        except Exception as exc:
            error_message = str(exc) or exc.__class__.__name__
            failed_result = {
                "task_id": task_id,
                "status": "failed",
                "image_path": image_path,
                "ocr_text": "",
                "questions": [],
                "score": 0,
                "summary": "",
                "comments": "",
                "suggestions": "",
                "error": error_message,
                "finished_at": _now(),
            }
            save_result(task_id, failed_result)
            update_task_status(task_id, status="failed", error=error_message)
        finally:
            _task_queue.task_done()
