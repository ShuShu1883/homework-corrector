from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from config import RESULT_DIR, ensure_runtime_dirs


def _result_path(task_id: str) -> Path:
    return RESULT_DIR / f"{task_id}.json"


def save_result(task_id: str, result: dict[str, Any]) -> None:
    ensure_runtime_dirs()
    payload = {
        "task_id": task_id,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        **result,
    }
    path = _result_path(task_id)
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp_path.replace(path)


def load_result(task_id: str, *, owner_username: str | None = None) -> dict[str, Any] | None:
    path = _result_path(task_id)
    if not path.exists():
        return None

    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        result = {
            "task_id": task_id,
            "status": "failed",
            "error": "结果文件格式损坏，无法读取。",
        }

    if owner_username is not None and result.get("owner_username") != owner_username:
        return None
    return result


def list_results(*, owner_username: str | None = None) -> list[dict[str, Any]]:
    ensure_runtime_dirs()
    results: list[dict[str, Any]] = []
    for path in sorted(RESULT_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        item = load_result(path.stem, owner_username=owner_username)
        if item:
            results.append(item)
    return results
