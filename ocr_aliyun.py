from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import requests

from config import get_setting


class OCRConfigError(RuntimeError):
    pass


def _mock_ocr_result(image_path: str) -> dict[str, Any]:
    return {
        "provider": "mock",
        "image_path": image_path,
        "ocr_text": "1. 计算 3 + 5 = ? 学生答案：9\n2. 计算 12 / 3 = ? 学生答案：4",
        "questions": [
            {
                "question_no": "1",
                "question": "计算 3 + 5 = ?",
                "student_answer": "9",
                "bbox": None,
            },
            {
                "question_no": "2",
                "question": "计算 12 / 3 = ?",
                "student_answer": "4",
                "bbox": None,
            },
        ],
        "raw": None,
    }


def _extract_text(payload: Any) -> str:
    if isinstance(payload, str):
        return payload

    if isinstance(payload, list):
        parts = [_extract_text(item) for item in payload]
        return "\n".join(part for part in parts if part)

    if not isinstance(payload, dict):
        return ""

    preferred_keys = (
        "text",
        "content",
        "ocr_text",
        "result",
        "data",
        "words",
        "wordsInfo",
        "prism_wordsInfo",
    )
    for key in preferred_keys:
        value = payload.get(key)
        text = _extract_text(value)
        if text:
            return text

    parts = []
    for value in payload.values():
        text = _extract_text(value)
        if text:
            parts.append(text)
    return "\n".join(parts)


def _extract_questions(ocr_text: str) -> list[dict[str, Any]]:
    lines = [line.strip() for line in ocr_text.splitlines() if line.strip()]
    if not lines:
        return []

    return [
        {
            "question_no": str(index),
            "question": line,
            "student_answer": "",
            "bbox": None,
        }
        for index, line in enumerate(lines, start=1)
    ]


def recognize_homework(image_path: str) -> dict[str, Any]:
    mode = (get_setting("OCR_MODE", "aliyun") or "aliyun").lower()
    if mode == "mock":
        return _mock_ocr_result(image_path)

    endpoint = get_setting("ALIYUN_OCR_ENDPOINT")
    appcode = get_setting("ALIYUN_OCR_APPCODE")
    api_key = get_setting("ALIYUN_OCR_API_KEY")

    if not endpoint:
        raise OCRConfigError(
            "未配置 ALIYUN_OCR_ENDPOINT。可以在 .env 或 Streamlit Secrets 中配置，"
            "或将 OCR_MODE 设置为 mock 先跑通演示流程。"
        )

    image_bytes = Path(image_path).read_bytes()
    encoded_image = base64.b64encode(image_bytes).decode("utf-8")

    headers = {"Content-Type": "application/json"}
    if appcode:
        headers["Authorization"] = f"APPCODE {appcode}"
    elif api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    else:
        raise OCRConfigError(
            "未配置 ALIYUN_OCR_APPCODE 或 ALIYUN_OCR_API_KEY。请配置密钥，"
            "或将 OCR_MODE 设置为 mock。"
        )

    response = requests.post(
        endpoint,
        headers=headers,
        json={"image": encoded_image, "body": encoded_image},
        timeout=60,
    )
    response.raise_for_status()
    raw = response.json()
    ocr_text = _extract_text(raw).strip()

    if not ocr_text:
        raise RuntimeError("阿里云 OCR 调用成功，但没有从返回结果中解析到文字。")

    return {
        "provider": "aliyun",
        "image_path": image_path,
        "ocr_text": ocr_text,
        "questions": _extract_questions(ocr_text),
        "raw": raw,
    }
