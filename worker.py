from __future__ import annotations

from datetime import datetime
from typing import Any

from llm_corrector import correct_homework
from ocr_aliyun import recognize_homework
from storage import save_result


def process_homework(task_id: str, image_path: str) -> dict[str, Any]:
    ocr_result = recognize_homework(image_path)
    correction = correct_homework(ocr_result)

    result = {
        "task_id": task_id,
        "status": "finished",
        "image_path": image_path,
        "ocr_text": ocr_result.get("ocr_text", ""),
        "questions": correction.get("questions", []),
        "score": correction.get("score", 0),
        "summary": correction.get("summary", ""),
        "comments": correction.get("comments", ""),
        "suggestions": correction.get("suggestions", ""),
        "error": None,
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "ocr_raw": ocr_result.get("raw"),
    }
    save_result(task_id, result)
    return result
