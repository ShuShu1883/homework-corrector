from __future__ import annotations

import json
import re
from typing import Any

import requests

from config import get_setting


class LLMConfigError(RuntimeError):
    pass


def _mock_correction(ocr_result: dict[str, Any]) -> dict[str, Any]:
    return {
        "score": 50,
        "summary": "共识别 2 道题，1 道正确，1 道需要订正。",
        "comments": "第 1 题计算错误；第 2 题答案正确。",
        "suggestions": "建议加强基础加减法计算训练，做题后用反向验算检查答案。",
        "questions": [
            {
                "question_no": "1",
                "is_correct": False,
                "student_answer": "9",
                "correct_answer": "8",
                "analysis": "3 + 5 的结果应为 8，学生答案 9 多算了 1。",
                "comment": "计算结果错误，请重新检查加法过程。",
            },
            {
                "question_no": "2",
                "is_correct": True,
                "student_answer": "4",
                "correct_answer": "4",
                "analysis": "12 / 3 = 4，答案正确。",
                "comment": "答案正确，继续保持。",
            },
        ],
    }


def _prompt_for(ocr_result: dict[str, Any]) -> str:
    ocr_text = ocr_result.get("ocr_text", "")
    questions = ocr_result.get("questions", [])
    return f"""
你是一名严谨、耐心的中小学作业批改老师。请根据 OCR 识别结果批改学生作业。

要求：
1. 判断每道题是否正确。
2. 给出总分，范围 0-100。
3. 给出正确答案、错误原因、简短批注和学习建议。
4. 如果 OCR 内容不完整，请在分析中说明不确定性。
5. 必须只返回 JSON，不要输出 Markdown 或额外解释。

OCR 原文：
{ocr_text}

OCR 结构化题目：
{json.dumps(questions, ensure_ascii=False)}

返回 JSON 格式：
{{
  "score": 85,
  "summary": "整体表现概述",
  "comments": "总体批注",
  "suggestions": "学习建议",
  "questions": [
    {{
      "question_no": "1",
      "is_correct": true,
      "student_answer": "学生答案",
      "correct_answer": "正确答案",
      "analysis": "解题分析",
      "comment": "简短批注"
    }}
  ]
}}
""".strip()


def _parse_json_response(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        raise ValueError("大模型没有返回 JSON 对象。")

    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise ValueError(f"大模型返回内容不是合法 JSON：{exc}") from exc


def _normalize_correction(payload: dict[str, Any]) -> dict[str, Any]:
    questions = payload.get("questions")
    if not isinstance(questions, list):
        questions = []

    score = payload.get("score", 0)
    try:
        score = int(score)
    except (TypeError, ValueError):
        score = 0

    return {
        "score": max(0, min(100, score)),
        "summary": str(payload.get("summary", "")),
        "comments": str(payload.get("comments", "")),
        "suggestions": str(payload.get("suggestions", "")),
        "questions": questions,
    }


def correct_homework(ocr_result: dict[str, Any]) -> dict[str, Any]:
    mode = (get_setting("LLM_MODE", "api") or "api").lower()
    if mode == "mock":
        return _mock_correction(ocr_result)

    api_key = get_setting("LLM_API_KEY")
    base_url = (get_setting("LLM_BASE_URL", "https://api.openai.com/v1") or "").rstrip("/")
    model = get_setting("LLM_MODEL", "gpt-4o-mini")

    if not api_key:
        raise LLMConfigError(
            "未配置 LLM_API_KEY。可以在 .env 或 Streamlit Secrets 中配置，"
            "或将 LLM_MODE 设置为 mock 先跑通演示流程。"
        )

    response = requests.post(
        f"{base_url}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": "你只返回严格 JSON。"},
                {"role": "user", "content": _prompt_for(ocr_result)},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        },
        timeout=90,
    )
    response.raise_for_status()
    data = response.json()
    content = data["choices"][0]["message"]["content"]
    return _normalize_correction(_parse_json_response(content))
