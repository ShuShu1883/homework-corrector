from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from image_processing import create_annotated_correction_image, create_preview_image, process_document_image
from llm_corrector import correct_homework
from paper_cut_tencent import recognize_question_split
from storage import save_result


def _prepare_ocr_image(task_id: str, image_path: str) -> dict[str, Any]:
    processed = process_document_image(image_path, task_id=task_id, enhance_mode="strong")
    enhanced_path = processed.get("enhanced_path")
    if processed.get("status") == "failed" or not enhanced_path or not Path(enhanced_path).exists():
        raise RuntimeError(f"图片增强失败，无法送入腾讯云 OCR：{processed.get('message', '未知错误')}")

    return {
        "image_path": str(enhanced_path),
        "processing": processed,
    }


def _create_annotation_image(
    task_id: str,
    ocr_result: dict[str, Any],
    correction: dict[str, Any],
    fallback_image_path: str,
) -> str | None:
    try:
        return create_annotated_correction_image(
            ocr_result.get("image_path", fallback_image_path),
            ocr_result.get("questions", []),
            correction.get("questions", []),
            task_id=task_id,
        )
    except Exception:
        return None


def process_homework(task_id: str, image_path: str) -> dict[str, Any]:
    prepared = _prepare_ocr_image(task_id, image_path)
    ocr_result = recognize_question_split(prepared["image_path"], task_id=task_id)
    if ocr_result.get("question_count", 0) == 0:
        raise RuntimeError("腾讯云切题 OCR 未识别到题目区域。")
    if not (ocr_result.get("ocr_text") or "").strip():
        raise RuntimeError("腾讯云切题 OCR 已返回题目区域，但未识别到可批改文字。")

    correction = correct_homework(ocr_result)
    annotated_image_path = _create_annotation_image(task_id, ocr_result, correction, prepared["image_path"])

    result = {
        "task_id": task_id,
        "status": "finished",
        "image_path": image_path,
        "image_preview_path": create_preview_image(image_path, task_id=task_id, suffix="original_preview"),
        "enhanced_image_path": prepared["image_path"],
        "ocr_image_path": ocr_result.get("image_path", prepared["image_path"]),
        "ocr_preview_path": create_preview_image(
            ocr_result.get("image_path", prepared["image_path"]),
            task_id=task_id,
            suffix="ocr_preview",
        ),
        "annotated_image_path": annotated_image_path,
        "api_image_meta": ocr_result.get("api_image_meta", {}),
        "processing": prepared["processing"],
        "ocr_text": ocr_result.get("ocr_text", ""),
        "paper_cut_questions": ocr_result.get("questions", []),
        "questions": correction.get("questions", []),
        "score": correction.get("score", 0),
        "summary": correction.get("summary", ""),
        "comments": correction.get("comments", ""),
        "suggestions": correction.get("suggestions", ""),
        "score_breakdown": correction.get("score_breakdown", ""),
        "strengths": correction.get("strengths", []),
        "weaknesses": correction.get("weaknesses", []),
        "next_steps": correction.get("next_steps", []),
        "error": None,
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "ocr_raw": ocr_result.get("raw"),
    }
    save_result(task_id, result)
    return result
